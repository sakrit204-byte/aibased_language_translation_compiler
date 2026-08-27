"""
Tests for the AI-usage policy — the claims the README makes, checked in code.

The point of the project is that the deterministic core stands alone and the
model is confined to a narrow, validated, auditable role. That is only worth
saying if it is enforced, so these tests assert:

  * no sample program invokes AI;
  * every builtin is translated statically, never by the model;
  * an unknown call fails loudly with AI off rather than being guessed at;
  * the validator rejects unsafe or wrong candidates, including ones that
    compile perfectly well;
  * nothing is accepted without a compiler having approved it.

Nothing here contacts Ollama: the client is stubbed, so the *policy* is tested
even on a machine with no model installed.
"""

import json
import os

import pytest

from conftest import CORE_PROGRAMS, read_program
from src.ai_assist import client, gate as gate_module
from src.ai_assist.gate import AIAssistError, Gate, TemplateCache, _extract_expression
from src.ai_assist.validator import check_structure, validate
from src.driver import compile_source
from src.semantic.builtins import BUILTINS
from src.semantic.symbol_table import SemanticError


class FakeCall:
    def __init__(self, name, arity, line=1):
        self.name = name
        self.args = [None] * arity
        self.line = line


@pytest.fixture
def fake_model(monkeypatch):
    """Replace the Ollama client with a scripted sequence of replies."""
    def install(*replies):
        state = {"calls": [], "replies": list(replies)}

        def fake_query(prompt, model=None, timeout=60):
            state["calls"].append(prompt)
            if not state["replies"]:
                raise client.AIClientError("no more scripted replies")
            return state["replies"].pop(0)

        monkeypatch.setattr(gate_module.client, "query", fake_query)
        return state
    return install


# --------------------------------------------------------- scope of the gate

@pytest.mark.parametrize("filename", CORE_PROGRAMS)
def test_no_sample_program_uses_ai_even_when_it_is_enabled(filename):
    _, stats = compile_source(read_program(filename), use_ai=True)
    assert stats["ai_calls_used"] == 0
    assert stats["ai_queries"] == 0


@pytest.mark.parametrize("name", sorted(BUILTINS))
def test_builtins_are_never_routed_to_ai(name, fake_model):
    """If a builtin ever reached the model, this scripted client would be called."""
    state = fake_model()
    samples = {
        "len": ('len("ab")', "int"), "abs": ("abs(-1)", "int"),
        "min": ("min(1, 2)", "int"), "max": ("max(1, 2)", "int"),
        "pow": ("pow(2, 3)", "int"), "round": ("round(1.5)", "int"),
        "int": ("int(1.5)", "int"), "float": ("float(1)", "float"),
        "bool": ("bool(1)", "bool"),
    }
    expr, ret = samples[name]
    compile_source(f"def f() -> {ret}:\n    return {expr}\n", use_ai=True)
    assert state["calls"] == [], f"{name} was sent to the model"


def test_unknown_call_fails_hard_without_ai():
    with pytest.raises(SemanticError) as excinfo:
        compile_source("def f() -> float:\n    return mystery(1.0)\n", use_ai=False)
    assert "mystery" in str(excinfo.value)
    assert "--ai" in str(excinfo.value)


# ------------------------------------------------------------- the validator

@pytest.mark.parametrize("candidate", [
    "system(a0)",
    "fopen(a0)",
    "sqrt(a0); system(a0)",
    "remove(a0)",
    'printf("%s", a0)',
    "*(int*)a0",
    "a0 = 1",
    "a0->x",
    "getenv(a0)",
])
def test_structure_check_rejects_dangerous_candidates(candidate):
    ok, reason = check_structure(candidate, 1)
    assert not ok, f"{candidate!r} should have been rejected"
    assert reason


def test_structure_check_rejects_a_candidate_that_ignores_its_arguments():
    ok, reason = check_structure("1.0", 1)
    assert not ok and "ignores" in reason


def test_structure_check_rejects_out_of_range_placeholders():
    ok, reason = check_structure("hypot(a0, a1)", 1)
    assert not ok and "a1" in reason


@pytest.mark.parametrize("candidate,arity", [
    ("sqrt(a0)", 1),
    ("hypot(a0, a1)", 2),
    ("(a0) * 180.0 / M_PI", 1),
    ("(a0 > a1 ? a0 : a1)", 2),
    ("fabs(a0) + log(a1)", 2),
])
def test_structure_check_accepts_pure_math_expressions(candidate, arity):
    ok, reason = check_structure(candidate, arity)
    assert ok, reason


def test_a_dangerous_candidate_is_rejected_even_though_it_compiles():
    """`system(...)` is perfectly valid C. "It compiles" is not enough."""
    ok, reason = validate("system(a0)", 1)
    assert not ok
    assert "safety check" in reason


# -------------------------------------------------------- response handling

@pytest.mark.parametrize("raw,expected", [
    ("sqrt(a0)", "sqrt(a0)"),
    ("sqrt(a0);", "sqrt(a0)"),
    ("```c\nsqrt(a0)\n```", "sqrt(a0)"),
    ("```\nsqrt(a0)\n```", "sqrt(a0)"),
    ("Here you go:\n```c\nsqrt(a0)\n```", "sqrt(a0)"),
    ("  sqrt(a0)  \n\nThat computes the square root.", "sqrt(a0)"),
])
def test_model_responses_are_unwrapped(raw, expected):
    assert _extract_expression(raw) == expected


# ------------------------------------------------------------- the gate loop

def test_gate_rejects_then_retries_with_the_failure_fed_back(fake_model, tmp_path):
    state = fake_model("system(a0)", "sqrt(a0)")
    g = Gate(log_path=str(tmp_path / "log.md"), cache_path=str(tmp_path / "cache.json"))
    result = g.resolve(FakeCall("sqrt", 1))

    assert result == "sqrt(a0)"
    assert len(state["calls"]) == 2
    assert "system(a0)" in state["calls"][1], "the retry must include the previous failure"
    assert g.queries_made == 2


def test_gate_raises_rather_than_emitting_an_unvalidated_stub(fake_model, tmp_path):
    fake_model("system(a0)", "fopen(a0)", "exec(a0)")
    g = Gate(cache_path=str(tmp_path / "cache.json"))
    with pytest.raises(AIAssistError, match="could not produce a valid C translation"):
        g.resolve(FakeCall("sqrt", 1))


def test_gate_fails_when_the_model_is_unreachable(monkeypatch, tmp_path):
    def unreachable(prompt, model=None, timeout=60):
        raise client.AIClientError("connection refused")

    monkeypatch.setattr(gate_module.client, "query", unreachable)
    g = Gate(cache_path=str(tmp_path / "cache.json"))
    with pytest.raises(AIAssistError):
        g.resolve(FakeCall("sqrt", 1))


def test_a_verified_translation_is_cached_and_reused(fake_model, tmp_path):
    cache_path = str(tmp_path / "cache.json")
    state = fake_model("sqrt(a0)")

    first = Gate(cache_path=cache_path)
    assert first.resolve(FakeCall("sqrt", 1)) == "sqrt(a0)"
    assert first.queries_made == 1

    # A fresh gate reading the same cache must not contact the model at all.
    second = Gate(cache_path=cache_path)
    assert second.resolve(FakeCall("sqrt", 1)) == "sqrt(a0)"
    assert second.queries_made == 0
    assert second.cache_hits == 1
    assert len(state["calls"]) == 1

    with open(cache_path, encoding="utf-8") as f:
        stored = json.load(f)
    assert stored["templates"]["sqrt/1"]["template"] == "sqrt(a0)"


def test_a_corrupt_cache_is_ignored_not_fatal(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{not json", encoding="utf-8")
    assert TemplateCache(str(path)).get("sqrt", 1) is None


def test_every_attempt_is_logged(fake_model, tmp_path):
    fake_model("system(a0)", "sqrt(a0)")
    log = tmp_path / "log.md"
    g = Gate(log_path=str(log), cache_path=str(tmp_path / "cache.json"))
    g.resolve(FakeCall("sqrt", 1))

    text = log.read_text(encoding="utf-8")
    assert "system(a0)" in text, "the rejected candidate must be in the audit trail"
    assert "sqrt(a0)" in text
    assert "accepted" in text


def test_sampling_is_pinned_to_zero_temperature():
    """A compiler that emits different output on two identical runs is not one."""
    import inspect
    source = inspect.getsource(client.query)
    assert '"temperature": 0' in source
    assert '"seed"' in source


def test_placeholder_substitution_wraps_arguments():
    from src.codegen.rules import substitute_placeholders

    assert substitute_placeholders("hypot(a0, a1)", ["x + 1", "y"]) == "(hypot((x + 1), (y)))"


def test_ai_cache_directory_is_created_on_demand(fake_model, tmp_path):
    fake_model("sqrt(a0)")
    nested = tmp_path / "deep" / "reports" / "cache.json"
    g = Gate(cache_path=str(nested))
    g.resolve(FakeCall("sqrt", 1))
    assert os.path.exists(nested)
