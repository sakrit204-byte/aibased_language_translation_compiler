"""
Fixed-size arrays — the one compound type in this subset.

The rules worth testing are the ones where C's defaults and Python's
semantics disagree, because the project's claim is that the generated code
agrees with CPython exactly:

  * a negative index counts from the end, it does not read off the front
  * an out-of-range index stops the program, it does not read whatever is
    next in memory
  * an array argument is shared with the caller, not copied
  * elements are never widened, because Python would keep the value you
    wrote and C would convert it

See src/semantic/arrays.py for why the type is what it is.
"""

import os
import subprocess
import sys

import pytest

from tests.conftest import needs_c_compiler

from src.driver import build_and_run, compile_source
from src.lexer.lexer import LexError, Lexer
from src.parser.parser import ParseError, Parser
from src.semantic import arrays
from src.semantic.type_checker import SemanticError, TypeChecker


def check(source):
    ast = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker()
    checker.check(ast)
    return checker


def in_main(body):
    return "def main() -> int:\n" + body + "    return 0\n"


def run(source):
    """Compile, build and run. Returns stdout."""
    c_code, _ = compile_source(source)
    code, out, err = build_and_run(c_code)
    assert code == 0, "exit %d\n%s" % (code, err)
    return out


# ------------------------------------------------------------- the type

def test_array_type_round_trips():
    t = arrays.make("int", 54)
    assert arrays.is_array(t)
    assert arrays.element(t) == "int"
    assert arrays.size(t) == 54


def test_an_unsized_array_is_what_a_parameter_has():
    t = arrays.make("float")
    assert arrays.is_array(t)
    assert arrays.size(t) is None
    assert arrays.element(t) == "float"


def test_a_scalar_is_not_an_array():
    for t in ("int", "float", "str", "bool", "None"):
        assert not arrays.is_array(t)
        assert arrays.element(t) is None


def test_a_sized_array_satisfies_an_unsized_parameter():
    assert arrays.compatible(arrays.make("int", 9), arrays.make("int"))


def test_element_types_are_never_widened():
    """int -> float widens for scalars. It must not for arrays: the two have
    different layouts, so C would reinterpret the bytes."""
    assert not arrays.compatible(arrays.make("int", 4), arrays.make("float"))
    assert not arrays.compatible(arrays.make("float", 4), arrays.make("int"))


def test_describe_speaks_the_source_language():
    assert arrays.describe(arrays.make("int", 9)) == "list[int]"
    assert arrays.describe("int") == "int"


# ------------------------------------------------------------- declaring

@pytest.mark.parametrize("decl", [
    "a: list[int] = [0] * 9",
    "a: list[int] = [1, 2, 3]",
    "a: list[float] = [0.0] * 4",
    "a: list[float] = [1.5, 2.5]",
    "a: list[bool] = [True] * 2",
    "a: list[int] = [-1] * 3",
])
def test_declarations_that_work(decl):
    check(in_main("    " + decl + "\n"))


def test_an_array_literal_may_span_lines():
    """A 72-element table is unreadable on one line, and Python joins lines
    inside brackets, so the lexer has to as well."""
    source = in_main("    a: list[int] = [\n        1, 2, 3,\n        4, 5, 6,\n    ]\n")
    check(source)


def test_the_size_comes_from_the_initialiser():
    checker = check(in_main("    a: list[int] = [0] * 7\n"))
    assert checker.symtab.function_locals["main"]["a"] == arrays.make("int", 7)


def test_a_literal_initialiser_sets_the_size():
    checker = check(in_main("    a: list[int] = [1, 2, 3, 4]\n"))
    assert arrays.size(checker.symtab.function_locals["main"]["a"]) == 4


def test_an_array_needs_an_annotation():
    with pytest.raises(SemanticError, match="may only initialise a variable"):
        check(in_main("    a = [0] * 3\n"))


def test_the_size_must_be_a_literal():
    with pytest.raises(ParseError, match="must be a literal number"):
        check(in_main("    n: int = 3\n    a: list[int] = [0] * n\n"))


def test_initialiser_elements_must_be_literals():
    with pytest.raises(SemanticError, match="literal numbers only"):
        check(in_main("    n: int = 1\n    a: list[int] = [n, 2]\n"))


def test_mixed_element_types_are_rejected():
    with pytest.raises(SemanticError, match="must all be the same type"):
        check(in_main("    a: list[int] = [1, 2.5]\n"))


def test_int_values_do_not_fill_a_float_array():
    with pytest.raises(SemanticError, match="initialised with int values"):
        check(in_main("    a: list[float] = [0] * 3\n"))


def test_float_values_do_not_fill_an_int_array():
    with pytest.raises(SemanticError, match="initialised with float values"):
        check(in_main("    a: list[int] = [0.0] * 3\n"))


def test_an_array_cannot_be_declared_without_a_value():
    with pytest.raises(SemanticError, match="must be initialised with an array"):
        check(in_main("    a: list[int] = 5\n"))


def test_an_empty_array_is_rejected():
    with pytest.raises(ParseError, match="empty array"):
        check(in_main("    a: list[int] = []\n"))


def test_the_size_has_a_ceiling():
    with pytest.raises(ParseError, match="beyond this subset's limit"):
        check(in_main("    a: list[int] = [0] * 99999\n"))


def test_nested_arrays_are_rejected():
    with pytest.raises(ParseError, match="not part of this subset"):
        check(in_main("    a: list[list[int]] = [0] * 2\n"))


def test_a_bare_list_annotation_asks_for_an_element_type():
    with pytest.raises(ParseError, match="needs an element type"):
        check(in_main("    a: list = [0] * 2\n"))


def test_str_arrays_are_rejected():
    with pytest.raises(ParseError, match="not an array element type"):
        check(in_main("    a: list[str] = [0] * 2\n"))


# -------------------------------------------------------------- indexing

def test_reading_and_writing_elements():
    out = run(in_main(
        "    a: list[int] = [0] * 3\n"
        "    a[0] = 10\n"
        "    a[1] = a[0] + 5\n"
        "    a[2] += 7\n"
        "    print(a[0], a[1], a[2])\n"
    ))
    assert out.strip() == "10 15 7"


def test_an_index_may_be_computed():
    out = run(in_main(
        "    a: list[int] = [4, 0, 3, 1, 2]\n"
        "    print(a[a[0]])\n"
    ))
    assert out.strip() == "2"


def test_a_negative_index_counts_from_the_end():
    out = run(in_main(
        "    a: list[int] = [1, 2, 3]\n"
        "    print(a[-1], a[-2], a[-3])\n"
    ))
    assert out.strip() == "3 2 1"


def test_len_of_a_local_is_a_constant():
    c_code, _ = compile_source(in_main(
        "    a: list[int] = [0] * 9\n"
        "    print(len(a))\n"
    ))
    assert "(py_int)(9)" in c_code or "(long long)(9)" in c_code


def test_only_an_array_can_be_indexed():
    with pytest.raises(SemanticError, match="not an array"):
        check(in_main("    x: int = 1\n    print(x[0])\n"))


def test_an_index_must_be_an_int():
    with pytest.raises(SemanticError, match="index must be an int"):
        check(in_main("    a: list[int] = [0] * 3\n    print(a[1.5])\n"))


def test_an_element_is_not_widened_on_assignment():
    with pytest.raises(SemanticError, match="int cannot be stored"):
        check(in_main("    a: list[float] = [0.0] * 3\n    a[0] = 1\n"))


def test_double_indexing_is_rejected():
    with pytest.raises(ParseError, match="indexes twice"):
        check(in_main("    a: list[int] = [0] * 3\n    print(a[0][0])\n"))


# ------------------------------------------------- out of range, at runtime

@needs_c_compiler
def test_an_out_of_range_index_stops_the_program():
    """C would read whatever is at that address. Python raises. The compiled
    program has to do what Python does, or the two disagree."""
    c_code, _ = compile_source(in_main(
        "    a: list[int] = [1, 2, 3]\n"
        "    i: int = 0\n"
        "    while i < 5:\n"
        "        print(a[i])\n"
        "        i = i + 1\n"
    ))
    code, out, err = build_and_run(c_code)
    assert out.split() == ["1", "2", "3"]
    assert code == 1                       # CPython's exit status for a traceback
    assert "IndexError" in err


@needs_c_compiler
def test_a_too_negative_index_also_stops():
    c_code, _ = compile_source(in_main(
        "    a: list[int] = [1, 2, 3]\n"
        "    print(a[-4])\n"
    ))
    code, out, err = build_and_run(c_code)
    assert code == 1
    assert "IndexError" in err


# ------------------------------------------------------------- parameters

def test_an_array_parameter_carries_its_length():
    """C arrays decay to pointers, losing the size, so the length travels as
    a second parameter and len() keeps working inside the function."""
    c_code, _ = compile_source(
        "def f(a: list[int]) -> int:\n"
        "    return len(a)\n"
        + in_main("    xs: list[int] = [0] * 5\n    print(f(xs))\n")
    )
    assert "py_int *a, py_int a__len" in c_code
    assert "f(xs, 5)" in c_code


def test_len_works_on_a_parameter():
    out = run(
        "def f(a: list[int]) -> int:\n"
        "    return len(a)\n"
        + in_main("    xs: list[int] = [0] * 5\n    print(f(xs))\n")
    )
    assert out.strip() == "5"


def test_an_array_argument_is_shared_not_copied():
    """Python passes the same list; C passes the same storage. Both mean the
    callee's writes are visible to the caller."""
    out = run(
        "def bump(a: list[int]) -> None:\n"
        "    a[0] = 99\n"
        + in_main("    xs: list[int] = [0] * 2\n    bump(xs)\n    print(xs[0])\n")
    )
    assert out.strip() == "99"


def test_element_types_must_match_at_a_call():
    with pytest.raises(SemanticError, match="argument 1"):
        check(
            "def f(a: list[float]) -> int:\n"
            "    return 0\n"
            + in_main("    xs: list[int] = [0] * 3\n    print(f(xs))\n")
        )


def test_a_function_cannot_return_an_array():
    with pytest.raises(SemanticError, match="cannot return an array"):
        check(
            "def f() -> list[int]:\n"
            "    a: list[int] = [0] * 3\n"
            "    return a\n"
            + in_main("    return 0\n")
        )


# ---------------------------------------------- an array has exactly one name

def test_an_array_cannot_be_reassigned():
    with pytest.raises(SemanticError, match="cannot be replaced"):
        check(in_main(
            "    a: list[int] = [0] * 3\n"
            "    a: list[int] = [1] * 3\n"
        ))


def test_an_array_cannot_be_aliased():
    with pytest.raises(SemanticError, match="second name for the array"):
        check(in_main(
            "    a: list[int] = [0] * 3\n"
            "    b: int = a\n"
        ))


def test_an_array_has_no_truth_value():
    with pytest.raises(SemanticError, match="no truth value"):
        check(in_main("    a: list[int] = [0] * 3\n    if a:\n        return 1\n"))


def test_arrays_are_not_arithmetic():
    with pytest.raises(SemanticError, match="not valid on list"):
        check(in_main("    a: list[int] = [0] * 3\n    print(a + 1)\n"))


def test_print_of_a_whole_array_is_refused_clearly():
    with pytest.raises(SemanticError, match="cannot take a whole array"):
        check(in_main("    a: list[int] = [0] * 3\n    print(a)\n"))


def test_an_array_literal_cannot_appear_mid_expression():
    with pytest.raises((ParseError, SemanticError)):
        check(in_main("    x: int = [1, 2][0]\n"))


# ------------------------------------------------------------ the demo

@needs_c_compiler
def test_the_rubik_demo_matches_cpython():
    """demos/rubik.py is the program this feature was added for: a cube is
    54 stickers and a permutation table, and neither was expressible before.

    Its own output asserts the cube properties — four turns of a face is the
    identity, a move undoes its inverse, (R U R' U') has order six — so a
    wrong translation shows up as a False in the diff, not just a crash.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo = os.path.join(root, "demos", "rubik.py")
    source = open(demo, encoding="utf-8").read()

    oracle = ("import sys\ndef put(s): sys.stdout.write(s)\n\n"
              + source + "\n\nmain()\n")
    expected = subprocess.run([sys.executable, "-c", oracle],
                              capture_output=True, text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr

    c_code, stats = compile_source(source)
    assert stats["ai_calls_used"] == 0        # no model involved anywhere
    code, actual, err = build_and_run(c_code, timeout=120)
    assert code == 0, err

    assert actual == expected.stdout
    # The three cube identities, plus "solved?" after the scramble is undone.
    assert actual.count("True") == 4
    assert actual.count("False") == 1         # the scramble really scrambled
