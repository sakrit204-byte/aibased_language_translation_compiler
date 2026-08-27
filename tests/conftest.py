"""Shared test fixtures and helpers."""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAMS_DIR = os.path.join(ROOT, "tests", "programs")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.driver import compile_source  # noqa: E402
from src.toolchain import find_c_compiler  # noqa: E402

C_COMPILER = find_c_compiler()

needs_c_compiler = pytest.mark.skipif(
    C_COMPILER is None,
    reason="no C compiler found (set CC, or install gcc/clang) — "
           "translation tests still run, only the execution ones are skipped",
)

needs_ollama = pytest.mark.skipif(
    not os.environ.get("RUN_AI_TESTS"),
    reason="set RUN_AI_TESTS=1 and run a local Ollama server to exercise the AI gate",
)


def all_programs():
    return sorted(f for f in os.listdir(PROGRAMS_DIR) if f.endswith(".py"))


CORE_PROGRAMS = [p for p in all_programs() if not p.startswith("ai_")]
AI_PROGRAMS = [p for p in all_programs() if p.startswith("ai_")]


def read_program(filename):
    with open(os.path.join(PROGRAMS_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


def python_ground_truth(filename):
    """Run a sample under CPython and return its stdout.

    Programs named ai_* call functions that live in `math`, which this subset
    has no import syntax for, so the preamble is supplied here.
    """
    source = read_program(filename)
    preamble = "from math import *\n" if filename.startswith("ai_") else ""
    result = subprocess.run(
        [sys.executable, "-c", preamble + source + "\nmain()\n"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"CPython reference run failed:\n{result.stderr}"
    return result.stdout


def compile_and_run(filename, use_ai=False):
    """Translate, build and run a sample. Returns (stdout, stats)."""
    from src.driver import build_and_run

    c_code, stats = compile_source(read_program(filename), use_ai=use_ai)
    code, out, err = build_and_run(c_code)
    assert code == 0, f"generated program failed (exit {code}):\n{err}\n---C---\n{c_code}"
    return out, stats
