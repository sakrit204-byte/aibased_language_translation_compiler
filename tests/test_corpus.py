"""
The repair corpus: every program in it has to be a fair question.

tools/repair_bench.py asks the model to rewrite each of these, and the number
it prints is only worth reading if the corpus itself is sound. Four things
make a program fair to ask, and all four are checked here without a model:

  1. It is ordinary Python that parses.
  2. The deterministic compiler refuses it, so repair is the path under test
     rather than an accident of the program already fitting.
  3. It defines main() and does not call it, which is the entry convention
     the generated C needs.
  4. It is usable as a reference at all: it terminates, exits 0, prints
     something, and prints the same thing twice.

A corpus program that stops satisfying (2) is the interesting failure here —
it means the subset grew to cover it, and the corpus should grow too.
"""

import ast
import io
import os

import pytest

from src.ai_assist import repair as R
from src.driver import CompileError, compile_source_detailed

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")
PROGRAMS = sorted(f for f in os.listdir(CORPUS) if f.endswith(".py"))


def read(name):
    with io.open(os.path.join(CORPUS, name), encoding="utf-8") as f:
        return f.read()


def test_the_corpus_is_not_empty():
    """A corpus that quietly emptied would make every test below vacuous."""
    assert len(PROGRAMS) >= 6, PROGRAMS


@pytest.mark.parametrize("name", PROGRAMS)
def test_corpus_program_is_valid_python(name):
    ast.parse(read(name))


@pytest.mark.parametrize("name", PROGRAMS)
def test_corpus_program_defines_main_and_leaves_it_uncalled(name):
    source = read(name)
    tree = ast.parse(source)
    assert any(isinstance(n, ast.FunctionDef) and n.name == "main"
               for n in tree.body), "no top-level def main()"
    assert R.needs_entry_call(source), "main() is already called"


@pytest.mark.parametrize("name", PROGRAMS)
def test_corpus_program_is_refused_by_the_compiler(name):
    """If this fails, the subset now covers the program and it is no longer a
    repair case — which is good news for the compiler and means the corpus
    needs a harder entry, not that this test should be relaxed."""
    with pytest.raises(CompileError):
        compile_source_detailed(read(name))


@pytest.mark.parametrize("name", PROGRAMS)
def test_corpus_program_can_serve_as_a_reference(name):
    """Terminates, exits 0, prints something, and agrees with itself.

    This runs the program twice, which is the same thing repair does before
    spending a token on it. A program that failed here would make its bench
    result BLOCKED rather than a measurement of the model.
    """
    reference = R.reference_run(read(name))
    assert reference["exit"] == 0
    assert reference["stdout"].strip(), "prints nothing, so matching it proves nothing"


# ------------------------------------------------- what the attempts remember

def test_prompt_lists_every_rejection_not_just_the_last():
    """An attempt told only about the previous answer can walk back into the
    mistake before it. With a whole-file rewrite that is a real failure mode,
    so the prompt carries the whole history."""
    prompt = R._prompt("def main() -> int:\n    return 0\n", "an error", "",
                       None, ["pasted the answer", "did not compile",
                              "printed one line too few"])
    assert "pasted the answer" in prompt
    assert "did not compile" in prompt
    assert "printed one line too few" in prompt
    assert "Attempt 1:" in prompt and "Attempt 3:" in prompt


def test_prompt_with_one_rejection_does_not_number_it():
    """One rejection reads as a correction; a numbered list of one reads as a
    form. Same information, and the singular wording is the honest one."""
    prompt = R._prompt("def main() -> int:\n    return 0\n", "an error", "",
                       None, ["did not compile"])
    assert "did not compile" in prompt
    assert "Attempt 1:" not in prompt


def test_prompt_still_accepts_a_bare_string():
    """The history is a list now; a single string was the old shape and is
    cheap to keep working."""
    prompt = R._prompt("def main() -> int:\n    return 0\n", "an error", "",
                       None, "did not compile")
    assert "did not compile" in prompt


def test_prompt_without_rejections_has_no_rejection_section():
    prompt = R._prompt("def main() -> int:\n    return 0\n", "an error", "",
                       None, [])
    # Not the bare word: the rules themselves explain what gets rejected.
    assert "your previous answer was rejected" not in prompt
    assert "every answer you have given so far" not in prompt
