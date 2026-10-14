"""
The golf demo, end to end.

demos/golf3d.py is the largest program the compiler handles: a ray-marched
ASCII 3D renderer, 36 functions, with no arrays anywhere because the subset has
none. It exists as a test because it exercises far more of the compiler at once
than any of the small samples — recursion-free deep call chains, float-heavy
arithmetic, early returns from nested loops, and sixteen AI-translated call
sites.

The assertion that matters is the last one: the C the compiler produced and the
Python it came from print exactly the same round of golf, character for
character, including every frame of the rendering.
"""

import os
import subprocess
import sys

import pytest

from conftest import ROOT, needs_ai_provider, needs_c_compiler

GOLF = os.path.join(ROOT, "demos", "golf3d.py")
SHIM = ("import sys\n"
        "from math import *\n"
        "def put(s): sys.stdout.write(s)\n")


@pytest.fixture(scope="module")
def source():
    if not os.path.exists(GOLF):
        pytest.skip("demos/golf3d.py is not present")
    with open(GOLF, encoding="utf-8") as f:
        return f.read()


def test_golf_needs_ai_and_names_what_is_missing(source):
    """Without the gate this is a hard error, not a silent guess."""
    from src.driver import compile_source
    from src.semantic.symbol_table import SemanticError

    with pytest.raises(SemanticError) as excinfo:
        compile_source(source, use_ai=False)
    message = str(excinfo.value)
    assert any(fn in message for fn in ("sqrt", "sin", "cos", "floor"))
    assert "--ai" in message


@needs_ai_provider
def test_golf_uses_the_gate_for_every_math_function(source):
    from src.lexer.lexer import Lexer
    from src.parser.parser import Parser
    from src.semantic.type_checker import TypeChecker

    ast = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker(ai_enabled=True)
    checker.check(ast)

    needed = {call.name for call in checker.ai_calls}
    assert needed == {"sqrt", "sin", "cos", "floor"}, needed
    assert len(checker.ai_calls) >= 10, "the game should have many AI call sites"


@needs_c_compiler
@needs_ai_provider
def test_golf_translates_and_plays_identically_to_cpython(source):
    """The headline claim, checked rather than asserted: a 3D renderer written
    in Python and translated to C produces byte-identical output."""
    from src.driver import build_and_run, compile_source

    c_code, stats = compile_source(
        source, use_ai=True,
        cache_path=os.path.join(ROOT, "reports", "ai_cache.json"))
    assert stats["ai_calls_used"] >= 10

    code, actual, err = build_and_run(c_code, timeout=300)
    assert code == 0, err

    reference = subprocess.run(
        [sys.executable, "-c", SHIM + source + "\nmain()\n"],
        capture_output=True, text=True, timeout=600)
    assert reference.returncode == 0, reference.stderr

    assert actual == reference.stdout
    assert len(actual.splitlines()) > 150, "the renderer should produce frames"
    assert "FINAL CARD" in actual


@needs_c_compiler
@needs_ai_provider
def test_golf_round_is_deterministic(source):
    """The shot scatter comes from a hash of a sine, not an RNG, so the same
    round must come out of two separate builds. Without that the byte-for-byte
    comparison above would be meaningless."""
    from src.driver import build_and_run, compile_source

    c_code, _ = compile_source(
        source, use_ai=True,
        cache_path=os.path.join(ROOT, "reports", "ai_cache.json"))
    first = build_and_run(c_code, timeout=300)[1]
    second = build_and_run(c_code, timeout=300)[1]
    assert first == second
