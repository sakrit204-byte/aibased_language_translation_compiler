"""Repair without an API key: the answer comes from a file, nothing else moves.

The pipeline never cared where a rewrite came from. Everything that decides
the verdict - the safety check, the whole compiler, the shortcut check, the C
build and the four compared runs - happens after the answer arrives. So an
answer written by hand goes through exactly the same code as one that came
over the network, and these tests are mostly about proving that the local
route is not a privileged one.

The second thing pinned here is the difference between "the model said
something unusable" and "the model was never asked". An empty account is not
a failed repair attempt, and reporting it as one makes a billing problem look
like a limitation of the compiler.
"""

import io
import os

import pytest

from conftest import ROOT, needs_c_compiler
from src.ai_assist import client
from src.ai_assist.repair import repair
from src.driver import CompileError, compile_source_detailed

# An f-string puts this outside the subset, and nothing else does.
ORIGINAL = (
    "def main() -> int:\n"
    "    total = 0\n"
    "    for i in range(1, 11):\n"
    "        total += i * i\n"
    '    print(f"sum = {total}")\n'
    "    return 0\n"
)

# The same output, computed the same way, written the way the subset allows.
HONEST = (
    "def main() -> int:\n"
    "    total: int = 0\n"
    "    for i in range(1, 11):\n"
    "        total = total + i * i\n"
    '    put("sum = ")\n'
    "    print(total)\n"
    "    return 0\n"
)

# The answer with the work taken out: it prints the number rather than
# reaching it. This must be refused, and refused for that reason.
PASTED = (
    "def main() -> int:\n"
    '    put("sum = ")\n'
    "    print(385)\n"
    "    return 0\n"
)


def the_error(source):
    try:
        compile_source_detailed(source)
    except CompileError as e:
        return str(e)
    return ""


def answers(*texts):
    """An `ask` that hands back each text in turn, one per attempt."""
    def ask(prompt, attempt):
        assert prompt, "the prompt should not be empty"
        if attempt > len(texts):
            raise AssertionError("asked for attempt %d, only %d answer(s) given"
                                 % (attempt, len(texts)))
        return texts[attempt - 1]
    ask.label = "a test answer"
    return ask


def test_the_original_really_is_refused():
    """Otherwise every test below is measuring nothing."""
    assert "f-string" in the_error(ORIGINAL).lower() or the_error(ORIGINAL)


# ------------------------------------------------- the answer can come anywhere

@needs_c_compiler
def test_an_answer_from_a_file_is_accepted_on_its_merits():
    repaired, report = repair(ORIGINAL, the_error(ORIGINAL),
                              ask=answers(HONEST), attempts=1)
    assert report["accepted"], report["reason"]
    assert repaired is not None
    assert "verified byte for byte" in report["reason"]


@needs_c_compiler
def test_the_report_names_what_actually_answered():
    """Naming a model that was never contacted would make the log say the
    opposite of what happened."""
    _, report = repair(ORIGINAL, the_error(ORIGINAL),
                       ask=answers(HONEST), attempts=1)
    assert report["model"] == "a test answer"


@needs_c_compiler
def test_a_local_answer_is_not_a_privileged_one():
    """The shortcut check does not care that a human wrote it: an answer that
    prints the number instead of working it out is refused either way."""
    repaired, report = repair(ORIGINAL, the_error(ORIGINAL),
                              ask=answers(PASTED), attempts=1)
    assert not report["accepted"]
    assert repaired is None
    stages = [a.get("stage") for a in report["attempts"]]
    assert "shortcut" in stages, stages


@needs_c_compiler
def test_a_rejected_answer_is_followed_by_another_question():
    """The loop is the same loop: a bad first answer costs an attempt and the
    next prompt carries the rejection."""
    seen = []

    def ask(prompt, attempt):
        seen.append(attempt)
        return PASTED if attempt == 1 else HONEST

    ask.label = "a test answer"
    repaired, report = repair(ORIGINAL, the_error(ORIGINAL), ask=ask, attempts=2)
    assert seen == [1, 2]
    assert report["accepted"], report["reason"]
    assert len(report["attempts"]) == 2


def test_the_second_prompt_carries_the_first_rejection():
    prompts = []

    def ask(prompt, attempt):
        prompts.append(prompt)
        return PASTED

    ask.label = "a test answer"
    repair(ORIGINAL, the_error(ORIGINAL), ask=ask, attempts=2)
    assert len(prompts) == 2
    header = "--- your previous answer was rejected ---"
    assert header not in prompts[0], "there was nothing to reject yet"
    assert header in prompts[1]
    # And what it carries is the real rejection, not a summary of one.
    assert "385" in prompts[1] or "without doing the work" in prompts[1]


# ------------------------------------------- never asked is not a failed answer

def test_an_unreachable_model_is_blocked_not_failed():
    def ask(prompt, attempt):
        raise client.OutOfCredit("no credit (HTTP 402).")

    ask.label = "a test answer"
    repaired, report = repair(ORIGINAL, the_error(ORIGINAL), ask=ask, attempts=5)

    assert repaired is None
    assert report["blocked"], "an unreachable model leaves the run blocked"
    assert report["attempts"][0].get("unreachable") is True


def test_an_unreachable_model_says_nothing_was_ever_produced():
    """The old wording counted it as a failed attempt at the model stage,
    which reads as "repair could not do this" when the truth is "repair was
    never asked"."""
    def ask(prompt, attempt):
        raise client.OutOfCredit("no credit (HTTP 402).")

    ask.label = "a test answer"
    _, report = repair(ORIGINAL, the_error(ORIGINAL), ask=ask, attempts=5)
    assert "no rewrite was ever produced" in report["reason"]
    assert "attempt" not in report["reason"], \
        "quoting 1 of 5 attempts invites the wrong question"


def test_it_stops_after_the_first_unreachable_attempt():
    """Four more round trips to an empty account prove nothing."""
    tries = []

    def ask(prompt, attempt):
        tries.append(attempt)
        raise client.OutOfCredit("no credit (HTTP 402).")

    ask.label = "a test answer"
    repair(ORIGINAL, the_error(ORIGINAL), ask=ask, attempts=5)
    assert tries == [1]


def test_out_of_credit_is_its_own_class_of_error():
    """So a caller can tell an empty account from a model that misbehaved."""
    assert issubclass(client.OutOfCredit, client.AIClientError)
    error = client._http_error(402, "any/model", "")
    assert isinstance(error, client.OutOfCredit)
    assert "credits" in str(error)          # says where to look


# ---------------------------------------------------------------- the tool

def test_the_tool_writes_the_prompt_and_stops(tmp_path):
    from tools.repair_offline import NeedAnswer, make_ask

    folder = str(tmp_path)
    ask = make_ask(folder, verbose=False)
    with pytest.raises(NeedAnswer) as caught:
        ask("the prompt text", 1)

    need = caught.value
    assert need.attempt == 1
    assert os.path.exists(need.prompt_path)
    with io.open(need.prompt_path, encoding="utf-8") as f:
        assert f.read() == "the prompt text"
    assert not os.path.exists(need.answer_path)


def test_the_tool_reads_an_answer_that_is_there(tmp_path):
    from tools.repair_offline import make_ask

    folder = str(tmp_path)
    with io.open(os.path.join(folder, "answer-1.py"), "w",
                 encoding="utf-8") as f:
        f.write(HONEST)

    ask = make_ask(folder, verbose=False)
    assert ask("the prompt text", 1) == HONEST


def test_the_tool_treats_an_empty_answer_as_not_written(tmp_path):
    """An empty file is what you get from creating one and not filling it."""
    from tools.repair_offline import NeedAnswer, make_ask

    folder = str(tmp_path)
    with io.open(os.path.join(folder, "answer-1.py"), "w",
                 encoding="utf-8") as f:
        f.write("   \n\n")

    with pytest.raises(NeedAnswer):
        make_ask(folder, verbose=False)("the prompt text", 1)


def test_the_tool_is_documented_where_it_is_found():
    """The whole mode is only usable if the first run says what to do next."""
    import tools.repair_offline as offline

    assert "no API key" in offline.__doc__
    assert "answer-1.py" in offline.__doc__


def test_the_tool_does_not_hold_a_program_the_compiler_accepts():
    from tools.repair_offline import compiler_error

    assert compiler_error("def main() -> int:\n    return 0\n") == ""
    assert compiler_error(ORIGINAL) != ""


def test_the_recorded_answer_for_bigconst_still_verifies():
    """tests/corpus/bigconst.py has a recorded answer that was accepted with
    no model involved. It is kept because it makes the claim re-checkable by
    anyone, at any time, with no account at all."""
    answer = os.path.join(ROOT, "tests", "corpus", "answers", "bigconst.py")
    if not os.path.exists(answer):
        pytest.skip("no recorded answer for bigconst")
    with io.open(answer, encoding="utf-8") as f:
        text = f.read()
    # Cheap checks only; the expensive end-to-end run lives in the tool.
    assert "def main() -> int:" in text
    assert "1267650600228229401496703205376" not in text, \
        "a recorded answer must compute the number, not contain it"
