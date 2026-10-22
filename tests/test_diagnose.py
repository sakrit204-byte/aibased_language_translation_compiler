"""
The survey: everything in a file the subset cannot translate, at once.

A compiler stops at the first problem, which is correct and is also the
wrong answer to "can this program be translated?". Answering that one error
per attempt turns a five-minute question into twenty rounds.

The property that matters here is honesty about the three verdicts. Calling
something NEVER when it is merely unimplemented would be a lie that stops
someone trying; calling something FEATURE when it needs an allocator and a
garbage collector would be a promise the project cannot keep.
"""

import pytest

from src.diagnose import FEATURE, FIX, NEVER, diagnose, report, summarise
from src.driver import compile_source
from src.semantic.symbol_table import SemanticError
from src.lexer.lexer import LexError
from src.parser.parser import ParseError


def verdicts_for(source):
    return {(f.verdict, f.construct) for f in diagnose(source)}


def constructs(source):
    return {f.construct for f in diagnose(source)}


# ------------------------------------------------------- a clean program

def test_a_subset_program_has_nothing_to_report():
    source = (
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "\ndef main() -> int:\n"
        "    print(add(1, 2))\n"
        "    return 0\n"
    )
    assert diagnose(source) == []
    assert "nothing outside the subset" in report(source)


def test_the_current_features_are_not_reported():
    """Arrays, dicts and classes all work now, so none of them is a finding.
    A survey that still flagged them would send people to rewrite working
    code."""
    source = (
        "class Box:\n"
        "    SIZE: int = 3\n"
        "    cells: list[int, 3]\n"
        "    COLORS: dict[str, int] = {\"a\": 1}\n"
        "    def __init__(self) -> None:\n"
        "        self.cells = [0] * 3\n"
        "    def get(self, i: int) -> int:\n"
        "        return self.cells[i]\n"
        "\ndef main() -> int:\n"
        "    b: Box = Box()\n"
        "    print(b.get(0))\n"
        "    return 0\n"
    )
    assert diagnose(source) == []


def test_imports_that_work_are_not_reported():
    source = ("import math\n"
              "from math import sqrt\n"
              "def main() -> int:\n"
              "    print(math.pi, sqrt(4.0))\n"
              "    return 0\n")
    assert diagnose(source) == []


# ----------------------------------------------------------- the verdicts

@pytest.mark.parametrize("source,construct", [
    ("def f():\n    pass\n", "def f(...)"),
    ("def f(x) -> int:\n    return x\n", "parameter 'x'"),
    ("x = 1\n", "code at module level"),
    ("def f() -> None:\n    global x\n    x = 1\n", "global"),
])
def test_fixable_things_are_marked_fixable(source, construct):
    assert (FIX, construct) in verdicts_for(source)


@pytest.mark.parametrize("source,construct", [
    ("def f() -> None:\n    raise ValueError('x')\n", "raise"),
    ("def f(n: int = 1) -> None:\n    pass\n", "default argument"),
    ("def f() -> None:\n    a, b = 1, 2\n", "tuple unpacking"),
    ("def f(d: dict) -> None:\n    for k in d.items():\n        pass\n", ".items()"),
])
def test_unimplemented_things_are_marked_not_yet(source, construct):
    assert (FEATURE, construct) in verdicts_for(source)


@pytest.mark.parametrize("source,construct", [
    ("import tkinter as tk\n", "import tkinter"),
    ("class B:\n    pass\nclass A(B):\n    pass\n", "class A(B)"),
    ("def f() -> None:\n    g = lambda x: x\n", "lambda"),
    ("def f() -> None:\n    try:\n        pass\n    except Exception:\n        pass\n",
     "try/except"),
    ("def f() -> None:\n    x = f'{1}'\n", "f-string"),
    ("def f() -> None:\n    yield 1\n", "yield"),
    ("def f() -> None:\n    with open('x') as fh:\n        pass\n", "with"),
    ("def f() -> None:\n    x = (1, 2)\n", "tuple"),
])
def test_impossible_things_are_marked_never(source, construct):
    assert (NEVER, construct) in verdicts_for(source)


def test_a_decorator_is_never_but_property_is_a_feature():
    """@property could be recognised as a form; a general decorator cannot,
    because it is a function taking a function."""
    assert (FEATURE, "@property") in verdicts_for(
        "class A:\n    @property\n    def x(self) -> int:\n        return 1\n")
    assert (NEVER, "@cache") in verdicts_for(
        "@cache\ndef f() -> int:\n    return 1\n")


def test_a_comprehension_is_a_feature_but_a_dict_one_is_never():
    """A list comprehension could be supported with a fixed bound. A dict
    comprehension decides the keys while running, and the keys are the type
    here, so it cannot be."""
    assert (FEATURE, "list comprehension") in verdicts_for(
        "def f() -> None:\n    xs = [i for i in range(3)]\n")
    assert (NEVER, "dict comprehension") in verdicts_for(
        "def f() -> None:\n    d = {i: i for i in range(3)}\n")


def test_the_entry_point_guard_is_explained_not_just_flagged():
    findings = diagnose("def main() -> int:\n    return 0\n"
                        "if __name__ == '__main__':\n    main()\n")
    guard = [f for f in findings if f.construct == "code at module level"]
    assert guard
    assert "calls main() itself" in guard[0].reason


# --------------------------------------------------------------- the report

def test_the_report_orders_by_line():
    source = ("import tkinter\n"
              "def f():\n"
              "    pass\n")
    lines = [f.line for f in diagnose(source)]
    assert lines == sorted(lines)


def test_the_report_says_when_a_program_is_impossible():
    text = report("import tkinter as tk\ndef f() -> None:\n    tk.Label()\n")
    assert "cannot be translated" in text


def test_the_report_says_when_a_program_is_only_work():
    text = report("def f():\n    pass\n")
    assert "Nothing here is impossible" in text


def test_summarise_counts_by_verdict():
    source = ("import tkinter\n"
              "def f():\n"
              "    pass\n"
              "def g():\n"
              "    pass\n")
    counts = summarise(diagnose(source))
    assert counts[NEVER]["import tkinter"] == 1
    assert counts[FIX]["def f(...)"] == 1


def test_invalid_python_is_reported_as_such():
    assert "not valid Python" in report("def f(:\n")


# ------------------------------------------- it agrees with the compiler

@pytest.mark.parametrize("source", [
    "def f():\n    pass\ndef main() -> int:\n    return 0\n",
    "import tkinter\ndef main() -> int:\n    return 0\n",
    "def main() -> int:\n    x = (1, 2)\n    return 0\n",
    "class A(object):\n    x: int\ndef main() -> int:\n    return 0\n",
])
def test_anything_the_survey_flags_the_compiler_also_rejects(source):
    """The survey must not be more permissive than the compiler. If it were,
    someone would fix everything it listed and still be stuck."""
    assert diagnose(source), "the survey found nothing"
    with pytest.raises((LexError, ParseError, SemanticError)):
        compile_source(source)


@pytest.mark.parametrize("source", [
    "def main() -> int:\n    return 0\n",
    "import math\ndef main() -> int:\n    print(math.pi)\n    return 0\n",
    "def main() -> int:\n    xs: list[int] = [0] * 3\n    return xs[0]\n",
    'def main() -> int:\n    d: dict[str, int] = {"a": 1}\n    return d["a"]\n',
])
def test_anything_the_survey_passes_the_compiler_also_accepts(source):
    """And it must not be stricter, or it would send people to rewrite code
    that already works."""
    assert diagnose(source) == []
    compile_source(source)
