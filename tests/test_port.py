"""The last stage: a conversion that does not claim to be a proof.

Every other stage refuses to produce a result it cannot prove, which is why
the project can make the claims it makes. For a program whose behaviour is a
window there is no proof available at all, and refusing to act left the
program untranslated for no gain. This stage converts it anyway and reports
exactly what was established.

So the tests here are mostly about the promise, not the plumbing. The one that
matters most is that nothing in a port report ever says "verified": if it did,
the single result in this program that cannot be proved would be the one that
looked strongest.
"""

import io
import os

import pytest

from conftest import ROOT, needs_c_compiler
from src.ai_assist import client
from src.ai_assist.port import PORT_RULES, gfx_api, port

PYGAME = (
    "import pygame\n"
    "\n"
    "def main():\n"
    "    pygame.init()\n"
    "    screen = pygame.display.set_mode((320, 240))\n"
    "    pygame.quit()\n"
)

# A real conversion: it draws with gfx, which is the only window the subset
# has. Kept short on purpose - what is under test is the stage, not the art.
CONVERSION = (
    "import gfx\n"
    "\n"
    "def main() -> int:\n"
    "    if gfx.open(320, 240) == 0:\n"
    "        return 1\n"
    "    while gfx.running() != 0:\n"
    "        gfx.poll()\n"
    "        gfx.clear(0.1, 0.1, 0.12)\n"
    "        gfx.perspective(45.0, 0.1, 100.0)\n"
    "        gfx.camera(0.0, 0.0, 5.0, 0.0, 0.0, 0.0)\n"
    "        gfx.color(0.3, 0.8, 0.5)\n"
    "        gfx.tri(-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 0.0, 1.0, 0.0)\n"
    "        gfx.present()\n"
    "    gfx.close()\n"
    "    return 0\n"
)


def answers(*texts):
    def ask(prompt, attempt):
        assert prompt
        return texts[min(attempt, len(texts)) - 1]
    ask.label = "a test answer"
    return ask


# ------------------------------------------------------------- the promise

@needs_c_compiler
def test_a_conversion_is_never_reported_as_verified():
    """The whole point. `verified` is in the report so that anything reading
    it has to see the word and the value together."""
    converted, report = port(PYGAME, "line 1: no pygame here",
                             ask=answers(CONVERSION), attempts=1, smoke=False)
    assert converted is not None
    assert report["ported"] is True
    assert report["verified"] is False
    assert "verified" not in report["reason"].lower().replace("not verified", "")


@needs_c_compiler
def test_the_verdict_says_converted_not_verified():
    _, report = port(PYGAME, "", ask=answers(CONVERSION), attempts=1, smoke=False)
    assert "CONVERTED, NOT VERIFIED" in report["reason"]
    assert "not that it behaves the same" in report["reason"]


@needs_c_compiler
def test_it_lists_what_was_actually_established():
    _, report = port(PYGAME, "", ask=answers(CONVERSION), attempts=1, smoke=False)
    proof = " ".join(report["proof"])
    assert "declares main()" in proof
    assert "the whole compiler accepts it" in proof
    assert "builds and links" in proof


@needs_c_compiler
def test_nothing_is_accepted_without_going_through_the_compiler():
    """A conversion that will not compile is refused like anything else."""
    broken = "def main() -> int:\n    x = [1, 2, 3]\n    x.append(4)\n    return 0\n"
    converted, report = port(PYGAME, "", ask=answers(broken), attempts=1,
                             smoke=False)
    assert converted is None
    assert not report["ported"]
    assert report["attempts"][-1]["stage"] == "compile"


def test_a_conversion_that_still_imports_a_toolkit_is_refused():
    """Converting pygame to pygame is not a conversion."""
    still_pygame = ("import pygame\n\ndef main() -> int:\n"
                    "    pygame.init()\n    return 0\n")
    converted, report = port(PYGAME, "", ask=answers(still_pygame), attempts=1,
                             smoke=False)
    assert converted is None
    assert report["attempts"][-1]["stage"] == "safety"
    assert "windowing library" in report["attempts"][-1]["reason"]


def test_a_conversion_without_main_is_refused():
    converted, report = port(PYGAME, "", ask=answers("x = 1\n"), attempts=1,
                             smoke=False)
    assert converted is None
    assert report["attempts"][-1]["stage"] == "safety"


def test_an_unreachable_model_is_blocked_and_keeps_the_prompt():
    def ask(prompt, attempt):
        raise client.OutOfCredit("no credit (HTTP 402).")

    ask.label = "a test answer"
    converted, report = port(PYGAME, "", ask=ask, attempts=3)
    assert converted is None
    assert report["blocked"]
    assert report["attempts"][0]["unreachable"] is True
    # The prompt is what a caller needs to carry on by hand.
    assert report["attempts"][0]["prompt"]
    assert "no conversion was ever produced" in report["reason"]


@needs_c_compiler
def test_a_rejected_conversion_is_followed_by_another_question():
    seen = []

    def ask(prompt, attempt):
        seen.append(attempt)
        return "x = 1\n" if attempt == 1 else CONVERSION

    ask.label = "a test answer"
    converted, report = port(PYGAME, "", ask=ask, attempts=2, smoke=False)
    assert seen == [1, 2]
    assert report["ported"]


def test_the_second_prompt_carries_the_first_refusal():
    prompts = []

    def ask(prompt, attempt):
        prompts.append(prompt)
        return "x = 1\n"

    ask.label = "a test answer"
    port(PYGAME, "", ask=ask, attempts=2, smoke=False)
    assert len(prompts) == 2
    assert "not usable" in prompts[1]
    assert "main()" in prompts[1]


# ------------------------------------------------------------- the prompt

def test_the_window_api_is_read_off_the_module_table():
    """Written out by hand it would drift from what the compiler accepts."""
    api = gfx_api()
    assert "gfx.tri(a0, a1, a2, a3, a4, a5, a6, a7, a8) -> None" in api
    assert "gfx.clear(a0, a1, a2) -> None" in api
    assert "gfx.open(a0, a1) -> int" in api


def test_the_rules_do_not_promise_a_comparison():
    assert "nothing will be compared" in PORT_RULES % gfx_api()


def test_the_example_loop_matches_the_real_arities():
    """An example with the wrong arity would teach the model an API that does
    not exist. These are the numbers gfx_api() reports."""
    text = PORT_RULES % gfx_api()
    assert "gfx.clear(0.09, 0.10, 0.13)" in text
    assert "gfx.camera(eye_x, eye_y, eye_z, 0.0, 0.0, 0.0)" in text
    assert "clear takes three floats" in text


# -------------------------------------------------------- the real artefact

@needs_c_compiler
def test_the_recorded_conversion_of_the_spinner_still_goes_through():
    """A pygame program and its conversion, kept as the worked example.

    This is the evidence for the stage: an AI-written conversion of a real
    pygame program that the compiler turns into a native executable. It is
    re-checked here with no model and no account.
    """
    folder = os.path.join(ROOT, "tests", "programs", "ported")
    original = os.path.join(folder, "spinner_pygame.py")
    conversion = os.path.join(folder, "spinner_gfx.py")
    if not (os.path.exists(original) and os.path.exists(conversion)):
        pytest.skip("the worked example is not present")

    with io.open(original, encoding="utf-8") as f:
        source = f.read()
    with io.open(conversion, encoding="utf-8") as f:
        answer = f.read()

    converted, report = port(source, "line 6: not in this subset",
                             ask=answers(answer), attempts=1, smoke=False)
    assert report["ported"], report["reason"]
    assert report["verified"] is False
    assert converted.count("gfx.") >= 6
