"""
Tests for AI simplification.

The feature's whole value is the refusal, not the rewrite: anything can shorten
C, but only a check that actually runs both versions can tell you the short one
still behaves the same. These tests therefore spend most of their effort on the
cases that must be *rejected*, with the model stubbed so they run with no
network and no API key.
"""

import pytest

from conftest import needs_c_compiler
from src.ai_assist import client, simplify as simplify_module
from src.ai_assist.simplify import check_safe, simplify
from src.driver import compile_source

SOURCE = (
    "def double(n: int) -> int:\n"
    "    return n * 2\n"
    "\n"
    "def main() -> int:\n"
    "    print(\"double(21) =\", double(21))\n"
    "    return 0\n"
)


@pytest.fixture
def generated_c():
    code, _ = compile_source(SOURCE, use_ai=False)
    return code


@pytest.fixture
def fake_model(monkeypatch):
    """Make the model return a scripted reply instead of calling OpenRouter."""
    def install(*replies):
        state = {"calls": 0, "replies": list(replies)}

        def fake_query(prompt, model=None, timeout=60, trace=None, max_tokens=256):
            state["calls"] += 1
            if not state["replies"]:
                raise client.AIClientError("no more scripted replies")
            return state["replies"].pop(0)

        monkeypatch.setattr(simplify_module.client, "query", fake_query)
        return state
    return install


# ------------------------------------------------------- the safety check

@pytest.mark.parametrize("snippet,expect", [
    ('#include <stdio.h>\nint main(void){ system("rm -rf /"); return 0; }', "system"),
    ('#include <stdio.h>\nint main(void){ FILE*f=fopen("x","w"); return 0; }', "fopen"),
    ('#include <unistd.h>\nint main(void){ return 0; }', "unistd.h"),
    ('#include <stdio.h>\nint main(void){ char*p=getenv("PATH"); return 0; }', "getenv"),
    ('#include <stdio.h>\n#pragma once\nint main(void){ return 0; }', "#pragma"),
    ('#include <stdio.h>\nint helper(void){ return 1; }', "main"),
    ("", "nothing"),
])
def test_safety_check_rejects_dangerous_candidates(snippet, expect):
    ok, reason = check_safe(snippet)
    assert not ok
    assert expect in reason


def test_safety_check_accepts_ordinary_generated_c(generated_c):
    ok, reason = check_safe(generated_c)
    assert ok, reason


def test_safety_check_runs_before_anything_is_executed(fake_model, generated_c):
    """A dangerous candidate must never reach the compiler, let alone the CPU."""
    state = fake_model('#include <stdio.h>\nint main(void){ system("echo pwned"); }')
    result, report = simplify(generated_c, attempts=1)
    assert result == generated_c
    assert not report["accepted"]
    assert report["attempts"][-1]["stage"] == "safety"
    assert "system" in report["attempts"][-1]["reason"]


# --------------------------------------------------- the equivalence check

@needs_c_compiler
def test_a_simplification_that_changes_output_is_rejected(fake_model, generated_c):
    """The point of the feature. This candidate is valid C, compiles cleanly,
    and is shorter — but prints 43 instead of 42."""
    wrong = generated_c.replace("double(21)", "double(21) + 1")
    fake_model(wrong)
    result, report = simplify(generated_c, attempts=1)

    assert result == generated_c, "a behaviour change must not be accepted"
    assert not report["accepted"]
    assert report["attempts"][-1]["stage"] == "equivalence"
    assert "expected" in report["attempts"][-1]["reason"]


@needs_c_compiler
def test_a_simplification_that_does_not_compile_is_rejected(fake_model, generated_c):
    fake_model("#include <stdio.h>\nint main(void){ this is not C }\n")
    result, report = simplify(generated_c, attempts=1)
    assert result == generated_c
    assert not report["accepted"]
    assert report["attempts"][-1]["stage"] == "compile"


@needs_c_compiler
def test_an_equivalent_simplification_is_accepted(fake_model, generated_c):
    """Strip the comment banner: shorter, same behaviour."""
    stripped = "\n".join(line for line in generated_c.split("\n")
                         if not line.strip().startswith(("/*", "*", "*/")))
    fake_model(stripped)
    result, report = simplify(generated_c, attempts=1)

    assert report["accepted"], report["reason"]
    assert result != generated_c
    assert report["simplified_lines"] < report["original_lines"]
    assert "verified identical" in report["reason"]


@needs_c_compiler
def test_rejection_is_fed_back_into_the_retry(fake_model, generated_c):
    wrong = generated_c.replace("double(21)", "double(21) + 1")
    stripped = generated_c.replace("/* ---- translated program ---- */", "")
    state = fake_model(wrong, stripped)

    result, report = simplify(generated_c, attempts=2)
    assert state["calls"] == 2
    assert report["accepted"]
    assert report["attempts"][0]["stage"] == "equivalence"
    assert report["attempts"][1]["ok"]


@needs_c_compiler
def test_model_failure_leaves_the_original_untouched(monkeypatch, generated_c):
    def unreachable(prompt, model=None, timeout=60, trace=None, max_tokens=256):
        raise client.AIClientError("connection refused")

    monkeypatch.setattr(simplify_module.client, "query", unreachable)
    result, report = simplify(generated_c, attempts=2)
    assert result == generated_c
    assert not report["accepted"]


@needs_c_compiler
def test_report_records_the_baseline_behaviour(fake_model, generated_c):
    fake_model(generated_c)                       # identical input is equivalent
    _, report = simplify(generated_c, attempts=1)
    assert report["accepted"]
    assert report["baseline"]["exit"] == 0
    assert report["baseline"]["lines"] == 1


def test_forbidden_word_in_a_comment_is_not_a_rejection():
    """The runtime's own comment says 'this at least gets 64 bits'. Scanning
    raw text flagged `gets`; the check must look at code, not prose."""
    code = ('#include <stdio.h>\n'
            '/* this at least gets 64 bits, and never calls system() */\n'
            'int main(void){ printf("no system here\n"); return 0; }\n')
    ok, reason = check_safe(code)
    assert ok, reason


def test_forbidden_call_in_real_code_is_still_a_rejection():
    code = ('#include <stdio.h>\n#include <stdlib.h>\n'
            'int main(void){ system("echo hi"); return 0; }\n')
    ok, reason = check_safe(code)
    assert not ok and "system" in reason
