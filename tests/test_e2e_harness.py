"""
End-to-end: compile source -> C, build the C, run it, and diff its stdout
against running the *same file* through CPython.

That diff is the whole correctness argument. The subset is a strict subset of
real Python syntax, so CPython is a free, exact oracle — no golden files to
drift, no hand-written expectations to be wrong in the same way the compiler is.
"""

import pytest

from conftest import (
    AI_PROGRAMS, CORE_PROGRAMS, compile_and_run, needs_c_compiler, needs_ollama,
    python_ground_truth,
)


@needs_c_compiler
@pytest.mark.parametrize("filename", CORE_PROGRAMS)
def test_core_programs_match_python(filename):
    expected = python_ground_truth(filename)
    actual, stats = compile_and_run(filename, use_ai=False)
    assert actual == expected
    assert stats["ai_calls_used"] == 0, "core programs must translate without AI"
    assert stats["ai_queries"] == 0


@needs_c_compiler
@needs_ollama
@pytest.mark.parametrize("filename", AI_PROGRAMS)
def test_ai_programs_match_python(filename):
    expected = python_ground_truth(filename)
    actual, stats = compile_and_run(filename, use_ai=True)
    assert actual == expected
    assert stats["ai_calls_used"] > 0, "this program should exercise the AI-assist path"


@pytest.mark.parametrize("filename", CORE_PROGRAMS)
def test_core_programs_need_no_ai_to_translate(filename):
    """Runs even without a C compiler: translation alone must not touch AI."""
    from conftest import read_program
    from src.driver import compile_source

    _, stats = compile_source(read_program(filename), use_ai=False)
    assert stats["ai_calls_used"] == 0
    assert stats["ai_queries"] == 0
