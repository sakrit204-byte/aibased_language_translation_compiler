"""The cube demos that nothing else covered.

demos/rubik.py and demos/rubik_oop.py are already tested, against CPython and
byte for byte, in test_arrays.py and test_dicts.py. The other three were not
tested at all, which is an odd gap given that rubik3d.py's own header calls it
the program the whole exercise was aimed at.

The 3D ones cannot be run in a test: they open a window and wait for a key.
But the part that breaks when the compiler changes is not the window, it is
the 600 lines of C in front of it - so these build an actual executable and
stop there. That catches a codegen regression, C that will not compile, a
missing declaration, and a link that has lost the shim. It does not catch a
wrong picture, and does not pretend to.

What made this worth writing: every integer literal in the compiler's output
changed when the subset moved to 64-bit arithmetic, and these two programs are
the largest bodies of integer arithmetic in the repository.
"""

import io
import os
import subprocess
import tempfile

import pytest

from conftest import C_COMPILER, ROOT, needs_c_compiler
from src.ai_assist.repair import build_and_capture, run_python
from src.driver import compile_source, link_requirements
from src.lexer.lexer import Lexer
from src.parser.parser import Parser
from src.semantic.type_checker import TypeChecker
from src.toolchain import compile_command, exe_suffix, run_env

WINDOWED = ["rubik3d", "cube3d"]


def demo(name):
    path = os.path.join(ROOT, "demos", name + ".py")
    if not os.path.exists(path):
        pytest.skip("demos/%s.py is not present" % name)
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def profiles_of(source):
    """The link profiles the program needs, from the checker that found them."""
    checker = TypeChecker()
    checker.check(Parser(Lexer(source).tokenize()).parse_program())
    return checker.profiles


# ------------------------------------------------------------- cube_class.py

@needs_c_compiler
def test_the_cube_class_demo_matches_cpython_byte_for_byte():
    """Both sides are captured as bytes. Decoding them to text would
    translate CRLF to LF on each side and hide a real difference."""
    source = demo("cube_class")
    c_code, stats = compile_source(source)
    assert stats["ai_calls_used"] == 0

    want_code, want, _ = run_python(source)
    error, runs = build_and_capture(c_code, runs=2)
    assert error is None, error

    (code, out, _), (code2, out2, _) = runs
    assert (out, code) == (out2, code2), "the compiled program is not deterministic"
    assert code == want_code
    assert out == want


# --------------------------------------------------- rubik3d.py and cube3d.py

@pytest.mark.parametrize("name", WINDOWED)
def test_the_windowed_cube_translates_with_no_model_involved(name):
    """gfx is a static profile, not an AI-resolved call: a window is not a
    thing the compiler guesses at."""
    c_code, stats = compile_source(demo(name))
    assert stats["ai_calls_used"] == 0
    assert "gfx_open" in c_code


@pytest.mark.parametrize("name", WINDOWED)
def test_the_windowed_cube_declares_the_shim_rather_than_including_it(name):
    """The emitted .c has to stay self-contained, so no -I path is needed."""
    c_code, _ = compile_source(demo(name))
    assert 'gfx.h' not in c_code


@pytest.mark.parametrize("name", WINDOWED)
def test_the_windowed_cube_keeps_the_shim_in_its_link(name):
    link = link_requirements(profiles_of(demo(name)))
    assert any(path.endswith("gfx.c") for path in link["sources"])


@needs_c_compiler
@pytest.mark.parametrize("name", WINDOWED)
def test_the_windowed_cube_builds_all_the_way_to_an_executable(name):
    """Built and never run: it would open a window and wait for a key.

    Everything up to that point is the compiler's work, and this is the only
    test that holds the whole of it to account for these two programs.
    """
    source = demo(name)
    c_code, _ = compile_source(source)
    link = link_requirements(profiles_of(source))

    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "cube.c")
        binary = os.path.join(tmp, "cube" + exe_suffix())
        with io.open(c_path, "w", encoding="utf-8") as f:
            f.write(c_code)
        build = subprocess.run(
            compile_command(C_COMPILER, c_path, binary,
                            extra_sources=link["sources"],
                            extra_flags=link["flags"]),
            capture_output=True, text=True, env=run_env(C_COMPILER),
            timeout=300)

    assert build.returncode == 0, (
        "%s.py no longer builds:\n%s" % (name, build.stderr or build.stdout))


# An unused local or parameter in the generated C is an unused local or
# parameter in the Python it came from: a method that ignores `self`, or
# `def spin(face)` that never reads face, translates to a C function that
# does the same. Those warnings are facts about the demo, not about the
# emitter, so they are not the emitter's to answer for. Everything else
# under -Wall -Wextra is.
_THE_PROGRAM_S_OWN_BUSINESS = (
    "-Wunused-parameter", "-Wunused-variable", "-Wunused-but-set-variable",
    "-Wunused-function", "-Wunused-label",
)


@needs_c_compiler
@pytest.mark.parametrize("name", WINDOWED)
def test_the_windowed_cube_compiles_without_a_meaningful_warning(name):
    """A -Wall -Wextra warning on generated C, outside the unused family,
    is a bug in the emitter: a type that does not match, a value read
    before it is set, a printf format against the wrong argument. Nobody
    is going to read 600 lines of generated C and spot one, so it is
    pinned here instead."""
    source = demo(name)
    c_code, _ = compile_source(source)
    link = link_requirements(profiles_of(source))

    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "cube.c")
        binary = os.path.join(tmp, "cube" + exe_suffix())
        with io.open(c_path, "w", encoding="utf-8") as f:
            f.write(c_code)
        build = subprocess.run(
            compile_command(C_COMPILER, c_path, binary,
                            extra_sources=link["sources"],
                            extra_flags=tuple(link["flags"]) + ("-Wall", "-Wextra")),
            capture_output=True, text=True, env=run_env(C_COMPILER),
            timeout=300)

    noise = [line for line in (build.stderr or "").splitlines()
             if ": warning:" in line
             and "gfx.c" not in line
             and not any(w in line
                         for w in _THE_PROGRAM_S_OWN_BUSINESS)]
    assert not noise, "the generated C warns:\n" + "\n".join(noise[:12])
