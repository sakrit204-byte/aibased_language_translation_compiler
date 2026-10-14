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

needs_ai_provider = pytest.mark.skipif(
    not os.environ.get("RUN_AI_TESTS"),
    reason="set RUN_AI_TESTS=1 to exercise the AI gate against a real model "
           "(needs an OpenRouter key or a local Ollama server)",
)

# Kept under the old name so existing imports keep working.
needs_ollama = needs_ai_provider


AI_PROGRAMS_DIR = os.path.join(PROGRAMS_DIR, "ai")


def all_programs():
    """Programs the deterministic core handles alone."""
    return sorted(f for f in os.listdir(PROGRAMS_DIR) if f.endswith(".py"))


def ai_programs():
    """Programs in tests/programs/ai/ — each calls at least one function the
    compiler has no static rule for, so none of them can be translated
    without the AI gate."""
    if not os.path.isdir(AI_PROGRAMS_DIR):
        return []
    return sorted(os.path.join("ai", f) for f in os.listdir(AI_PROGRAMS_DIR)
                  if f.endswith(".py"))


CORE_PROGRAMS = [p for p in all_programs() if not p.startswith("ai_")]
AI_PROGRAMS = ai_programs()


def _is_ai(filename):
    return filename.startswith("ai_") or filename.replace("\\", "/").startswith("ai/")


def read_program(filename):
    with open(os.path.join(PROGRAMS_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


def python_ground_truth(filename):
    """Run a sample under CPython and return its stdout.

    Programs under ai/ call maths functions bare — `sqrt(x)` with no import —
    which is what makes them AI-eligible, so the oracle is given the import
    they deliberately leave out. A program that imports `math` itself needs
    no help here; see programs/imports_math.py.
    """
    source = read_program(filename)
    # put() is a compiler builtin with no CPython equivalent — print always
    # ends a line — so the oracle gets a one-line shim for it.
    preamble = "import sys\ndef put(s): sys.stdout.write(s)\n"
    if _is_ai(filename):
        preamble += "from math import *\n"
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
