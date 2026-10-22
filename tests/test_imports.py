"""
Imports: what a program may bring into scope, and what it may not.

An import in this subset binds names; it never executes a module. The table
of what exists lives in src/semantic/modules.py, so these tests are mostly
about the boundary — that `math` is translated statically with no AI involved,
that `gfx` pulls the C shim into the link, and that everything else is
refused with the reason rather than a shrug.
"""

import pytest

from tests.conftest import needs_c_compiler

from src.driver import compile_source, link_requirements
from src.lexer.lexer import Lexer
from src.parser.parser import ParseError, Parser
from src.semantic import modules
from src.semantic.type_checker import SemanticError, TypeChecker


def check(source):
    """Lex, parse and type-check. Returns the checker, for its profiles."""
    ast = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker()
    checker.check(ast)
    return checker


def compile_ok(source):
    c_code, stats = compile_source(source)
    return c_code, stats


# ------------------------------------------------------------------- shapes

@pytest.mark.parametrize("source", [
    "import math\n",
    "import math as m\n",
    "from math import sqrt\n",
    "from math import sqrt, cos\n",
    "from math import sqrt as s, cos as c\n",
    "from math import *\n",
])
def test_every_import_form_is_accepted(source):
    check(source + "def main() -> int:\n    return 0\n")


def test_one_module_per_line():
    with pytest.raises(ParseError, match="one module per line"):
        check("import math, gfx\ndef main() -> int:\n    return 0\n")


def test_from_needs_its_import():
    with pytest.raises(ParseError, match="expected `import`"):
        check("from math sqrt\ndef main() -> int:\n    return 0\n")


def test_trailing_junk_on_an_import_line():
    with pytest.raises(ParseError, match="end of the import"):
        check("import math sqrt\ndef main() -> int:\n    return 0\n")


# -------------------------------------------------------------------- math

def test_math_is_translated_without_the_ai():
    """The point of having a math module: `math.sqrt` has a known-correct C
    translation, so unlike a bare `sqrt(x)` it costs no model round trip."""
    c_code, stats = compile_ok(
        "import math\n"
        "def main() -> int:\n"
        "    print(math.sqrt(2.0))\n"
        "    return 0\n"
    )
    assert stats["ai_calls_used"] == 0
    assert stats["ai_queries"] == 0
    assert "sqrt(" in c_code


def test_a_bare_call_still_needs_the_ai():
    """The contrast: without the import there is no rule, so the gate is the
    only route and it is an error to ask for one without --ai."""
    with pytest.raises(SemanticError, match="not defined in this program"):
        check("def main() -> int:\n    print(sqrt(2.0))\n    return 0\n")


@pytest.mark.parametrize("expr,fragment", [
    ("math.sqrt(2.0)", "sqrt("),
    ("math.floor(1.5)", "(py_int)floor("),
    ("math.ceil(1.5)", "(py_int)ceil("),
    ("math.trunc(1.5)", "(py_int)trunc("),
    ("math.log(8.0)", "log("),
    ("math.log(8.0, 2.0)", "/ log("),
    ("math.degrees(1.0)", "180.0 /"),
    ("math.radians(1.0)", "/ 180.0"),
    ("math.hypot(3.0, 4.0)", "hypot("),
    ("math.isnan(1.0)", "isnan("),
])
def test_each_math_member_emits_its_own_c(expr, fragment):
    c_code, _ = compile_ok(
        f"import math\ndef main() -> int:\n    print({expr})\n    return 0\n"
    )
    assert fragment in c_code


def test_floor_returns_an_int_as_python_does():
    """C's floor returns a double. Python's returns an int, and Python wins,
    because otherwise print() would show 3.0 where CPython shows 3."""
    checker_source = (
        "import math\n"
        "def main() -> int:\n"
        "    n: int = math.floor(3.7)\n"
        "    return n\n"
    )
    compile_ok(checker_source)          # an int target would reject a float


def test_a_constant_needs_no_parentheses():
    c_code, _ = compile_ok(
        "import math\ndef main() -> int:\n    print(math.pi)\n    return 0\n"
    )
    assert "3.14159" in c_code


def test_calling_a_constant_is_an_error():
    with pytest.raises(SemanticError, match="is a number, not a function"):
        check("import math\ndef main() -> int:\n    print(math.pi(1.0))\n    return 0\n")


def test_using_a_function_without_calling_it_is_an_error():
    with pytest.raises(SemanticError, match="needs parentheses"):
        check("import math\ndef main() -> int:\n    x: float = math.sqrt\n    return 0\n")


def test_wrong_arity_is_caught_statically():
    with pytest.raises(SemanticError, match="takes 1 argument"):
        check("import math\ndef main() -> int:\n    print(math.sqrt(1.0, 2.0))\n    return 0\n")


def test_log_accepts_one_or_two_arguments():
    for args in ("8.0", "8.0, 2.0"):
        compile_ok(f"import math\ndef main() -> int:\n    print(math.log({args}))\n    return 0\n")


def test_a_string_argument_is_rejected():
    with pytest.raises(SemanticError, match="must be a number"):
        check('import math\ndef main() -> int:\n    print(math.sqrt("x"))\n    return 0\n')


def test_an_unknown_member_lists_what_exists():
    with pytest.raises(SemanticError) as excinfo:
        check("import math\ndef main() -> int:\n    print(math.frobnicate(1.0))\n    return 0\n")
    message = str(excinfo.value)
    assert "no member 'frobnicate'" in message
    assert "sqrt" in message            # it says what there is instead


def test_an_unknown_from_member_is_caught_at_the_import():
    with pytest.raises(SemanticError, match="no member 'frobnicate'"):
        check("from math import frobnicate\ndef main() -> int:\n    return 0\n")


def test_an_import_may_not_shadow_a_builtin():
    with pytest.raises(SemanticError, match="cannot bind that name"):
        check("from math import pow\ndef main() -> int:\n    return 0\n")


def test_an_alias_keeps_the_module_reachable():
    c_code, _ = compile_ok(
        "import math as m\ndef main() -> int:\n    print(m.sqrt(4.0))\n    return 0\n"
    )
    assert "sqrt(" in c_code


def test_a_from_import_binds_the_plain_name():
    c_code, _ = compile_ok(
        "from math import sqrt\ndef main() -> int:\n    print(sqrt(4.0))\n    return 0\n"
    )
    assert "sqrt(" in c_code


def test_a_renamed_from_import_uses_the_local_name():
    c_code, _ = compile_ok(
        "from math import cos as c\ndef main() -> int:\n    print(c(0.0))\n    return 0\n"
    )
    assert "cos(" in c_code


def test_star_import_brings_in_everything():
    c_code, _ = compile_ok(
        "from math import *\n"
        "def main() -> int:\n"
        "    print(sqrt(4.0))\n"
        "    print(pi)\n"
        "    return 0\n"
    )
    assert "sqrt(" in c_code and "3.14159" in c_code


def test_a_user_function_wins_over_an_imported_name():
    """Python's own precedence: the later binding is the one in force."""
    c_code, _ = compile_ok(
        "from math import sqrt\n"
        "def sqrt(x: float) -> float:\n"
        "    return x\n"
        "def main() -> int:\n"
        "    print(sqrt(9.0))\n"
        "    return 0\n"
    )
    # `sqrt` collides with libm, so codegen renames the user's function.
    # That it is renamed at all proves the call bound to the definition
    # rather than to the import.
    assert "sqrt__c(9.0)" in c_code


def test_a_local_variable_wins_over_an_imported_name():
    compile_ok(
        "from math import pi\n"
        "def main() -> int:\n"
        "    pi: int = 3\n"
        "    return pi\n"
    )


# --------------------------------------------------------------------- gfx

def test_importing_gfx_activates_the_profile():
    checker = check("import gfx\ndef main() -> int:\n    return gfx.open(640, 480)\n")
    assert [p.name for p in checker.profiles] == ["gfx"]


def test_gfx_declarations_are_inlined_into_the_c():
    """The generated .c stays self-contained — no -I path is ever needed, so
    the shim is declared rather than #included."""
    c_code, _ = compile_ok(
        "import gfx\n"
        "def main() -> int:\n"
        "    if gfx.open(320, 240) == 0:\n"
        "        return 1\n"
        "    gfx.close()\n"
        "    return 0\n"
    )
    assert "int gfx_open(long long width, long long height);" in c_code
    assert "#include \"gfx.h\"" not in c_code


def test_gfx_pulls_the_shim_into_the_link():
    checker = check("import gfx\ndef main() -> int:\n    return gfx.open(1, 1)\n")
    link = link_requirements(checker.profiles)
    assert any(path.endswith("gfx.c") for path in link["sources"])
    assert "-lopengl32" in link["flags"]


def test_a_program_without_gfx_links_nothing_extra():
    checker = check("def main() -> int:\n    return 0\n")
    assert checker.profiles == []
    assert link_requirements(checker.profiles) == {"sources": [], "flags": []}


def test_a_void_gfx_call_is_a_statement():
    c_code, _ = compile_ok(
        "import gfx\ndef main() -> int:\n    gfx.poll()\n    return 0\n"
    )
    assert "gfx_poll()" in c_code


def test_gfx_names_drop_the_redundant_prefix():
    """`gfx.open`, not `gfx.gfx_open` — the module name already said it."""
    assert "open" in modules.GFX.names()
    assert "gfx_open" not in modules.GFX.names()


def test_gfx_member_names_match_the_profile_exactly():
    """The Python-visible names are derived from the profile's C signatures,
    so the two cannot drift apart."""
    from src.ai_assist import profiles
    expected = {name[len("gfx_"):] for name in profiles.GFX.functions}
    assert set(modules.GFX.names()) == expected


# ----------------------------------------------------- what we cannot accept

@pytest.mark.parametrize("module,reason", [
    ("pygame", "objects and arrays"),
    ("numpy", "objects and arrays"),
    ("tkinter", "objects and arrays"),
    ("random", "Mersenne Twister"),
    ("time", "clock"),
    ("sys", "argv"),
    ("json", "dicts and lists"),
    ("itertools", "iterators"),
])
def test_an_unsupported_module_is_refused_with_its_reason(module, reason):
    with pytest.raises(SemanticError) as excinfo:
        check(f"import {module}\ndef main() -> int:\n    return 0\n")
    message = str(excinfo.value)
    assert reason in message
    assert "Importable here: gfx, math" in message


def test_pygame_points_at_the_alternative():
    with pytest.raises(SemanticError, match="use `import gfx` instead"):
        check("import pygame\ndef main() -> int:\n    return 0\n")


def test_a_submodule_is_judged_by_its_root():
    with pytest.raises(SemanticError, match="objects and arrays"):
        check("from pygame.locals import *\ndef main() -> int:\n    return 0\n")


def test_an_unknown_module_says_what_is_importable():
    with pytest.raises(SemanticError) as excinfo:
        check("import flibble\ndef main() -> int:\n    return 0\n")
    assert "no module 'flibble'" in str(excinfo.value)
    assert "Importable here: gfx, math" in str(excinfo.value)


# ------------------------------------------------------- dots are still rare

def test_attribute_access_on_a_value_is_still_rejected():
    """The dot reaches the semantic phase now that classes exist, because
    that is the first stage that knows whether the owner is an instance. The
    error still has to name attribute access and point somewhere useful."""
    with pytest.raises(SemanticError) as excinfo:
        check("def main() -> int:\n    return obj.field\n")
    message = str(excinfo.value)
    assert "attribute access" in message
    assert "import math" in message


def test_a_dot_on_a_name_that_is_not_a_module_is_attribute_access():
    with pytest.raises(SemanticError, match="no objects here"):
        check(
            "import math\n"
            "def main() -> int:\n"
            "    x: float = 1.0\n"
            "    return x.real\n"
        )


def test_a_module_member_cannot_be_nested_deeper():
    with pytest.raises(ParseError, match="a level deeper"):
        check("import math\ndef main() -> int:\n    return math.a.b\n")


def test_the_dot_suggestion_names_a_working_example():
    with pytest.raises(SemanticError, match="import math"):
        check("def main() -> int:\n    return obj.field\n")


# -------------------------------------------- the gfx shim actually builds

@needs_c_compiler
def test_a_gfx_program_links_against_the_shim():
    """The end of the chain: `import gfx` has to produce an executable.

    Nothing is run — the program would open a window and wait — but it is
    compiled *and linked*, which is what proves the declarations the emitter
    inlined match the definitions in src/runtime/gfx.c. A mismatch in any
    signature fails here.
    """
    import os
    import subprocess
    import tempfile

    from src import toolchain

    source = (
        "import gfx\n"
        "import math\n"
        "def main() -> int:\n"
        "    if gfx.open(320, 240) == 0:\n"
        "        return 1\n"
        "    t: float = 0.0\n"
        "    while gfx.running() != 0:\n"
        "        gfx.poll()\n"
        "        t = gfx.time()\n"
        "        gfx.clear(0.1, 0.1, 0.1)\n"
        "        gfx.perspective(60.0, 0.1, 100.0)\n"
        "        gfx.camera(math.cos(t), 1.0, math.sin(t), 0.0, 0.0, 0.0)\n"
        "        gfx.color(1.0, 0.5, 0.2)\n"
        "        gfx.tri(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)\n"
        "        if gfx.key(gfx.key_space()) != 0:\n"
        "            gfx.present()\n"
        "        gfx.present()\n"
        "    gfx.close()\n"
        "    return 0\n"
    )
    c_code, stats = compile_source(source)
    assert stats["imports"] == ["gfx"]

    checker = check(source)
    link = link_requirements(checker.profiles)

    cc = toolchain.find_c_compiler()
    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "prog.c")
        exe_path = os.path.join(tmp, "prog" + toolchain.exe_suffix())
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_code)
        argv = toolchain.compile_command(cc, c_path, exe_path,
                                         link["sources"], link["flags"])
        result = subprocess.run(argv, capture_output=True, text=True,
                                env=toolchain.run_env(cc), timeout=180)
        assert result.returncode == 0, (
            f"linking the gfx shim failed:\n{result.stderr[-3000:]}"
        )
        assert os.path.exists(exe_path)
        assert os.path.getsize(exe_path) > 0
