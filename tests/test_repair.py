"""
Source-level repair: what it accepts, what it refuses, and why.

The model is stubbed out everywhere except the one test marked otherwise, so
these run offline and deterministically. What is being tested is not the
model's taste — it is that a rewrite is only ever accepted when it goes through
the whole compiler AND its C prints exactly what the original printed, and that
each way of failing that is reported as the thing it is.
"""

import pytest

from conftest import needs_c_compiler
from src.ai_assist import repair as R


# ------------------------------------------------------- the entry convention

def test_entry_call_is_appended_only_when_main_is_defined_and_uncalled():
    assert R.needs_entry_call("def main() -> int:\n    return 0\n")
    assert not R.needs_entry_call("def f() -> int:\n    return 0\n")
    assert not R.needs_entry_call("def main() -> int:\n    return 0\n\nmain()\n")
    # the guarded form counts as a call: CPython would run it
    assert not R.needs_entry_call(
        'def main() -> int:\n    return 0\n\n\nif __name__ == "__main__":\n'
        "    main()\n")


# ------------------------------------------------------------- the reference

def test_reference_run_captures_raw_bytes_and_exit():
    """Bytes, not text. text=True would translate CRLF to LF on both sides of
    every later comparison, which is exactly the difference worth catching."""
    result = R.reference_run('def main() -> int:\n    print("hi")\n    return 0\n')
    assert isinstance(result["stdout"], bytes)
    assert result["stdout"] in (b"hi\n", b"hi\r\n")
    assert result["exit"] == 0


def test_reference_run_gets_the_put_shim():
    """put() is a compiler builtin with no CPython equivalent, so the oracle is
    given the one-line shim — otherwise every program using it would look like
    a crash rather than a program."""
    result = R.reference_run('def main() -> int:\n    put("a")\n    put("b")\n'
                             "    print()\n    return 0\n")
    assert result["stdout"].replace(b"\r\n", b"\n") == b"ab\n"


def test_a_program_that_disagrees_with_itself_cannot_be_a_reference():
    """Matching one run of a program that varies says nothing about the next,
    so this is refused before any model call rather than verified against a
    coin flip."""
    varying = ("import random\n\n\ndef main() -> int:\n"
               "    print(random.random())\n    return 0\n")
    with pytest.raises(R.RepairError) as excinfo:
        R.reference_run(varying)
    assert "does not print the same thing twice" in str(excinfo.value)


def test_a_program_that_never_finishes_is_refused_before_any_model_call():
    with pytest.raises(R.RepairError) as excinfo:
        R.reference_run("def main() -> int:\n    while True:\n        pass\n"
                        "    return 0\n", timeout=2)
    message = str(excinfo.value)
    assert "does not finish on its own" in message
    assert "nothing to compare a rewrite against" in message


def test_a_program_that_crashes_has_no_behaviour_to_preserve():
    with pytest.raises(R.RepairError) as excinfo:
        R.reference_run("def main() -> int:\n    return 1 // 0\n")
    assert "fails under CPython itself" in str(excinfo.value)


# ------------------------------------------------------------- the pre-check

@pytest.mark.parametrize("source, fragment", [
    ("", "returned nothing"),
    ("def main( -> int:\n", "not valid Python"),
    ('def main() -> int:\n    exec("x = 1")\n    return 0\n', "reaches outside"),
    ('def main() -> int:\n    f = open("x")\n    return 0\n', "reaches outside"),
    ("def helper() -> int:\n    return 1\n", "no top-level `def main()`"),
])
def test_candidates_that_never_reach_the_compiler(source, fragment):
    ok, reason = R.check_candidate(source)
    assert not ok
    assert fragment in reason


def test_a_plain_subset_program_passes_the_pre_check():
    ok, reason = R.check_candidate("def main() -> int:\n    return 0\n")
    assert ok, reason


# ------------------------------------------------------------- the extractor

def test_a_fenced_reply_is_unwrapped():
    raw = ('Here is the rewrite:\n\n```python\ndef main() -> int:\n'
           "    return 0\n```\n\nHope that helps!")
    assert R._extract_python(raw) == "def main() -> int:\n    return 0\n"


def test_a_bare_reply_survives_unchanged():
    assert R._extract_python("def main() -> int:\n    return 0\n") == (
        "def main() -> int:\n    return 0\n")


def test_prose_before_the_code_is_dropped():
    raw = "Sure. The fix is to use put().\ndef main() -> int:\n    return 0\n"
    assert R._extract_python(raw).startswith("def main()")


# ------------------------------------------------- naming the first mismatch

def test_first_difference_names_the_line_and_both_values():
    assert R.first_difference(b"a\nb\n", b"a\nc\n", 0, 0) == (
        "line 2 is 'c', expected 'b'")


def test_first_difference_notices_a_short_run():
    assert "missing" in R.first_difference(b"a\nb\n", b"a\n", 0, 0)


def test_first_difference_notices_an_extra_line():
    assert "stopped at line" in R.first_difference(b"a\n", b"a\nb\n", 0, 0)


def test_an_exit_status_difference_outranks_the_output():
    assert R.first_difference(b"a\n", b"z\n", 0, 3) == "exit status 3 instead of 0"


def test_a_difference_only_in_line_endings_is_not_hidden():
    """The whole reason output is captured as bytes: these two look identical
    in any viewer, and are not the same program's output."""
    detail = R.first_difference(b"a\r\nb\r\n", b"a\nb\n", 0, 0)
    assert "the text looks the same, the bytes are not" in detail
    assert "byte 2" in detail


def test_a_difference_only_in_encoding_is_not_hidden():
    detail = R.first_difference("é\n".encode("utf-8"), "é\n".encode("cp1252"),
                                0, 0)
    assert detail != "no difference"


def test_identical_bytes_report_no_difference():
    assert R.first_difference(b"a\n", b"a\n", 0, 0) == "no difference"


# --------------------------------------------------------- the whole machine

ORIGINAL = (
    "def main() -> int:\n"
    "    values = [2, 4, 6]\n"
    "    for index, value in enumerate(values):\n"
    '        print(f"{index}:{value}")\n'
    "    return 0\n"
)

GOOD_REWRITE = (
    "def write_int(n: int) -> None:\n"
    "    if n >= 10:\n"
    "        write_int(n // 10)\n"
    "    digit: int = n % 10\n"
    "    if digit == 0:\n"
    '        put("0")\n'
    "    elif digit == 2:\n"
    '        put("2")\n'
    "    elif digit == 4:\n"
    '        put("4")\n'
    "    elif digit == 6:\n"
    '        put("6")\n'
    "    else:\n"
    '        put("1")\n'
    "\n\n"
    "def main() -> int:\n"
    "    values: list[int] = [2, 4, 6]\n"
    "    for index in range(3):\n"
    "        write_int(index)\n"
    '        put(":")\n'
    "        write_int(values[index])\n"
    "        print()\n"
    "    return 0\n"
)

# Compiles, but prints a space the original never printed.
WRONG_REWRITE = GOOD_REWRITE.replace('put(":")', 'put(" :")')

# Does not compile: the subset has no string concatenation.
UNCOMPILABLE_REWRITE = (
    "def main() -> int:\n"
    '    s: str = "a" + "b"\n'
    "    print(s)\n"
    "    return 0\n"
)


def _stub(monkeypatch, *replies):
    """Make the model return `replies` in order."""
    answers = list(replies)

    def fake_query(prompt, **kwargs):
        return answers.pop(0) if answers else ""

    monkeypatch.setattr(R.client, "query", fake_query)


@needs_c_compiler
def test_a_rewrite_is_accepted_only_after_its_output_is_compared(monkeypatch):
    _stub(monkeypatch, GOOD_REWRITE)
    repaired, report = R.repair(ORIGINAL, error="line 3: enumerate")

    assert repaired == GOOD_REWRITE
    assert report["accepted"]
    assert report["reference"]["stdout"].replace("\r\n", "\n") == "0:2\n1:4\n2:6\n"
    assert report["attempts"][-1]["stage"] == "verified"
    assert "byte for byte" in report["reason"]
    # The C is the compiler's, not the model's.
    assert report["c_code"] and "int main" in report["c_code"]


@needs_c_compiler
def test_acceptance_names_every_run_that_was_compared(monkeypatch):
    """Four agreeing runs, not one: the original twice, the rewrite under
    CPython, and the compiled C twice. A bug in the model and a matching bug
    in the compiler cannot cancel out, because neither produced the
    reference."""
    _stub(monkeypatch, GOOD_REWRITE)
    _, report = R.repair(ORIGINAL, error="line 3: enumerate")

    labels = [label for label, _ in report["checks"]]
    assert labels == [
        "your program under CPython, run twice",
        "the rewrite under CPython",
        "the compiled C, run 1",
        "the compiled C, run 2",
    ]
    assert all(detail == "identical" for _, detail in report["checks"])
    assert len(report["reference"]["sha256"]) == 64


@needs_c_compiler
def test_a_rewrite_that_prints_something_else_is_caught_before_any_c_is_built(
        monkeypatch):
    """Running the rewrite under CPython catches a wrong rewrite as a wrong
    rewrite, rather than leaving it to look like a codegen bug."""
    _stub(monkeypatch, WRONG_REWRITE)
    repaired, report = R.repair(ORIGINAL, error="line 3: enumerate",
                                attempts=1)
    assert repaired is None
    assert report["attempts"][0]["stage"] == "equivalence"
    # it never got as far as producing output from a binary
    assert "stdout" not in report["attempts"][0]


@needs_c_compiler
def test_a_rewrite_that_compiles_but_prints_something_else_is_refused(monkeypatch):
    _stub(monkeypatch, WRONG_REWRITE, WRONG_REWRITE, WRONG_REWRITE)
    repaired, report = R.repair(ORIGINAL, error="line 3: enumerate")

    assert repaired is None
    assert not report["accepted"]
    assert len(report["attempts"]) == 3
    assert all(a["stage"] == "equivalence" for a in report["attempts"])
    assert "line 1 is '0 :2', expected '0:2'" in report["attempts"][0]["reason"]


@needs_c_compiler
def test_a_rewrite_the_compiler_rejects_is_retried_with_its_diagnostic(monkeypatch):
    seen = []

    def fake_query(prompt, **kwargs):
        seen.append(prompt)
        return UNCOMPILABLE_REWRITE if len(seen) == 1 else GOOD_REWRITE

    monkeypatch.setattr(R.client, "query", fake_query)
    repaired, report = R.repair(ORIGINAL, error="line 3: enumerate")

    assert repaired == GOOD_REWRITE
    assert report["accepted"]
    assert report["attempts"][0]["stage"] == "compile"
    assert not report["attempts"][0]["ok"]
    # The second prompt carries the compiler's own words, not a summary.
    assert "The compiler rejected it" in seen[1]


def test_a_program_with_no_finite_output_is_refused_without_a_model_call(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("the model must not be consulted here")

    monkeypatch.setattr(R.client, "query", fail)
    monkeypatch.setattr(R, "REFERENCE_TIMEOUT", 2)

    repaired, report = R.repair(
        "def main() -> int:\n    while True:\n        pass\n    return 0\n")
    assert repaired is None
    assert "does not finish on its own" in report["blocked"]
    assert report["attempts"] == []


@needs_c_compiler
def test_a_silent_program_is_accepted_but_the_claim_is_marked_vacuous(monkeypatch):
    """Matching "prints nothing" is not evidence that behaviour survived, and
    the report says so rather than showing a green tick."""
    silent = "def main() -> int:\n    x = 1 + 1\n    return 0\n"
    _stub(monkeypatch, "def main() -> int:\n    y: int = 2\n    return 0\n")

    repaired, report = R.repair(silent, error="whatever")
    assert repaired is not None
    assert report["accepted"]
    assert report["vacuous"]
    assert "proves only" in report["reason"]


def test_the_model_being_unreachable_is_reported_as_itself(monkeypatch):
    def fail(*args, **kwargs):
        raise R.client.AIClientError("no API key and no Ollama server")

    monkeypatch.setattr(R.client, "query", fail)
    repaired, report = R.repair("def main() -> int:\n    return 0\n")
    assert repaired is None
    assert report["attempts"][0]["stage"] == "model"
    assert "no API key" in report["reason"]


# ------------------------------------------------------- the comparison view

gui = pytest.importorskip("gui", reason="tkinter is not available")


def test_equal_lines_land_on_the_same_row():
    left, right, left_tags, right_tags, numbers = gui._aligned(
        "a\nb\nc\n", "a\nB\nc\n")
    assert left == ["a", "b", "c"]
    assert right == ["a", "B", "c"]
    assert left_tags == [None, "removed", None]
    assert right_tags == [None, "added", None]
    assert numbers == [1, 2, 3]


def test_an_inserted_line_pads_the_other_side():
    left, right, left_tags, right_tags, numbers = gui._aligned("a\n", "a\nb\n")
    assert left == ["a", ""]
    assert right == ["a", "b"]
    assert left_tags == [None, "filler"]
    assert right_tags == [None, "added"]
    # the padded row belongs to no line of the original
    assert numbers == [1, None]


def test_the_error_line_can_still_be_found_after_padding():
    before = "a\nb\nc\nd\n"
    after = "a\nX\nY\nZ\nb\nc\nd\n"
    _, _, _, _, numbers = gui._aligned(before, after)
    # line 4 of the original is still locatable in the padded view
    assert numbers.index(4) >= 3


def test_prose_becomes_comment_lines_that_fit():
    text = gui._as_comment("one " * 60, width=40)
    assert all(line.startswith("#") for line in text.splitlines())
    assert all(len(line) <= 42 for line in text.splitlines())
