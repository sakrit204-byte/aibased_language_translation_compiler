"""A program that opens a window is refused before it is launched.

Two things were wrong with running it first and deciding afterwards.

The obvious one: the program's own window appears on screen, twice, because
the reference run runs it twice to check it agrees with itself. Nobody pressing
Translate asked for their tkinter app to start.

The one that matters more: a windowed program has no bytes on stdout, and
"the bytes are identical" is the whole of what accepts a repair. So a rewrite
that printed nothing was ACCEPTED - flagged vacuous, but accepted - while
proving nothing at all, and a rewrite that did the work and showed it was
REFUSED for not matching an empty target. A seven-line program that summed
0 to 8 and printed nothing was accepted as a repair of a 360-line Rubik's
cube. There is no verdict worth giving on such a program, so none is given.
"""

import pytest

from src.ai_assist.repair import (RepairError, WINDOWING_MODULES,
                                  opens_a_window, reference_run, repair)

TKINTER = (
    "import tkinter as tk\n"
    "\n"
    "def main() -> int:\n"
    "    root = tk.Tk()\n"
    "    root.mainloop()\n"
    "    return 0\n"
)


# ------------------------------------------------------------------ detection

@pytest.mark.parametrize("line, expected", [
    ("import tkinter", "tkinter"),
    ("import tkinter as tk", "tkinter"),
    ("from tkinter import ttk", "tkinter"),
    ("import tkinter.font", "tkinter"),
    ("from ursina import *", "ursina"),
    ("import pygame", "pygame"),
    ("import turtle", "turtle"),
    ("from PyQt6.QtWidgets import QApplication", "PyQt6"),
])
def test_a_windowing_import_is_recognised(line, expected):
    assert opens_a_window(line + "\n\ndef main() -> int:\n    return 0\n") == expected


@pytest.mark.parametrize("source", [
    "# this program does not import tkinter\ndef main() -> int:\n    return 0\n",
    'def main() -> int:\n    print("pygame is not imported here")\n    return 0\n',
    "def main() -> int:\n    ursina = 3\n    return ursina\n",
    "import math\n\ndef main() -> int:\n    return 0\n",
])
def test_a_mention_is_not_an_import(source):
    """Read off the import statements, not searched for as text. A comment,
    a string and a variable called ursina are all just words."""
    assert opens_a_window(source) is None


def test_a_program_that_does_not_parse_is_not_judged_here():
    """Repair is given programs the compiler refused, but they are still
    valid Python. Something that will not parse at all is a different
    complaint, and not this function's to make."""
    assert opens_a_window("def main(:\n") is None


def test_the_list_covers_the_toolkits_someone_would_actually_reach_for():
    for name in ("tkinter", "pygame", "ursina", "turtle"):
        assert name in WINDOWING_MODULES


# -------------------------------------------------------------- the refusal

def test_the_reference_run_refuses_without_running_anything(monkeypatch):
    """The point of the whole change: the decision is made before launching."""
    import src.ai_assist.repair as R

    def must_not_run(*args, **kwargs):
        raise AssertionError("the program was launched, which is the bug")

    monkeypatch.setattr(R, "run_python", must_not_run)
    with pytest.raises(RepairError) as caught:
        reference_run(TKINTER)
    assert "tkinter" in str(caught.value)
    assert "not run at all" in str(caught.value)


def test_the_refusal_explains_why_a_verdict_would_be_worthless():
    with pytest.raises(RepairError) as caught:
        reference_run(TKINTER)
    message = str(caught.value)
    # Both halves of the bind, so the reader is not left thinking the tool
    # simply could not be bothered.
    assert "accepted while proving nothing" in message
    assert "refused for not matching" in message


def test_repair_blocks_a_windowed_program_and_spends_nothing(monkeypatch):
    """No model is asked, and no attempt is recorded: there was no question."""
    import src.ai_assist.repair as R

    def must_not_run(*args, **kwargs):
        raise AssertionError("the program was launched")

    monkeypatch.setattr(R, "run_python", must_not_run)

    def must_not_ask(prompt, attempt):
        raise AssertionError("a rewrite was requested for an unverifiable program")

    repaired, report = repair(TKINTER, "line 1: tkinter is not in this subset",
                              ask=must_not_ask)
    assert repaired is None
    assert report["blocked"]
    assert "tkinter" in report["blocked"]
    assert report["attempts"] == []
    assert not report["accepted"]


def test_a_console_program_is_still_run_as_before():
    """The guard must not catch anything that does print."""
    source = 'def main() -> int:\n    print("hello")\n    return 0\n'
    assert opens_a_window(source) is None
    reference = reference_run(source)
    assert reference["stdout"].strip() == b"hello"
    assert reference["exit"] == 0
