"""
For every sample program: compile source -> C, compile C -> binary, run the
binary, and diff its stdout against running the SAME source through real
Python 3 as ground truth (valid, since our subset is a strict subset of
real Python syntax).

Programs whose name contains "ai_" are skipped by default since they
require a running local Ollama server — run with RUN_AI_TESTS=1 to include
them.
"""

import os
import subprocess
import sys
import tempfile
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAMS_DIR = os.path.join(ROOT, "tests", "programs")
sys.path.insert(0, ROOT)

from src.driver import compile_source  # noqa: E402


def _python_ground_truth(path):
    code = f"exec(open({path!r}).read()); main()"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"reference python run failed: {result.stderr}"
    return result.stdout


def _compile_and_run(path, use_ai):
    with open(path) as f:
        source = f.read()
    c_code, stats = compile_source(source, use_ai=use_ai)

    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "prog.c")
        bin_path = os.path.join(tmp, "prog")
        with open(c_path, "w") as f:
            f.write(c_code)
        # runtime header lives at src/runtime/pyrt.h relative to project root
        compile_result = subprocess.run(
            ["gcc", c_path, "-I", ROOT + "/src", "-lm", "-o", bin_path],
            capture_output=True, text=True,
        )
        assert compile_result.returncode == 0, f"gcc failed:\n{compile_result.stderr}\n---C---\n{c_code}"
        run_result = subprocess.run([bin_path], capture_output=True, text=True)
        assert run_result.returncode == 0, f"binary exited nonzero: {run_result.stderr}"
        return run_result.stdout, stats


ALL_PROGRAMS = sorted(f for f in os.listdir(PROGRAMS_DIR) if f.endswith(".py"))
CORE_PROGRAMS = [p for p in ALL_PROGRAMS if "ai_" not in p]
AI_PROGRAMS = [p for p in ALL_PROGRAMS if "ai_" in p]


@pytest.mark.parametrize("filename", CORE_PROGRAMS)
def test_core_programs_match_python(filename):
    path = os.path.join(PROGRAMS_DIR, filename)
    expected = _python_ground_truth(path)
    actual, stats = _compile_and_run(path, use_ai=False)
    assert actual == expected
    assert stats["ai_calls_used"] == 0, "core programs must not need AI-assist"


@pytest.mark.skipif(not os.environ.get("RUN_AI_TESTS"), reason="requires local Ollama server")
@pytest.mark.parametrize("filename", AI_PROGRAMS)
def test_ai_fallback_programs_match_python(filename):
    path = os.path.join(PROGRAMS_DIR, filename)
    expected = _python_ground_truth(path)
    actual, stats = _compile_and_run(path, use_ai=True)
    assert actual == expected
    assert stats["ai_calls_used"] > 0, "this program should have exercised the AI-assist path"


def test_ai_disabled_fails_hard_on_gap():
    """--no-ai must fail loudly on a construct with no static rule, never
    silently produce wrong output."""
    path = os.path.join(PROGRAMS_DIR, "ai_fallback_demo.py")
    with pytest.raises(RuntimeError, match="AI-assist is disabled"):
        _compile_and_run(path, use_ai=False)
