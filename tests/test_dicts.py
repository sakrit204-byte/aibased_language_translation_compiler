"""
Dicts with a fixed key set.

Two arrays — the keys, constant, and the values, not — so no allocator is
needed. See semantic/dicts.py.

The test that carries the most weight is insertion order: that is the single
property that lets a dict exist here without giving up agreement with
CPython, so it is checked directly rather than inferred.
"""

import pytest

from tests.conftest import needs_c_compiler

from src.driver import build_and_run, compile_source
from src.lexer.lexer import Lexer
from src.parser.parser import ParseError, Parser
from src.semantic import dicts
from src.semantic.type_checker import SemanticError, TypeChecker

MAIN_TAIL = "    return 0\n"


def check(source):
    ast = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker()
    checker.check(ast)
    return checker


def in_main(body):
    return "def main() -> int:\n" + body + MAIN_TAIL


def run(source):
    c_code, _ = compile_source(source)
    code, out, err = build_and_run(c_code)
    assert code == 0, "exit %d\n%s" % (code, err)
    return out


# --------------------------------------------------------------- the type

def test_dict_type_round_trips():
    t = dicts.make("str", "int", 6)
    assert dicts.is_dict(t)
    assert dicts.key_type(t) == "str"
    assert dicts.value_type(t) == "int"
    assert dicts.size(t) == 6


def test_describe_speaks_the_source_language():
    assert dicts.describe(dicts.make("str", "float", 3)) == "dict[str, float]"
    assert dicts.describe("int") == "int"


def test_a_scalar_is_not_a_dict():
    for t in ("int", "str", "int[4]", "obj:Cube"):
        assert not dicts.is_dict(t)


# --------------------------------------------------------------- it works

def test_lookup_and_assignment():
    out = run(in_main(
        '    d: dict[str, int] = {"a": 1, "b": 2}\n'
        '    d["a"] = 10\n'
        '    d["b"] += 5\n'
        '    print(d["a"], d["b"])\n'))
    assert out.strip() == "10 7"


def test_len_is_the_key_count():
    out = run(in_main(
        '    d: dict[str, int] = {"a": 1, "b": 2, "c": 3}\n'
        "    print(len(d))\n"))
    assert out.strip() == "3"


def test_membership():
    out = run(in_main(
        '    d: dict[str, int] = {"a": 1}\n'
        '    print("a" in d, "b" in d)\n'))
    assert out.strip() == "True False"


def test_int_keys():
    out = run(in_main(
        '    d: dict[int, str] = {0: "zero", 7: "seven"}\n'
        '    print(d[0], d[7], 7 in d, 8 in d)\n'))
    assert out.strip() == "zero seven True False"


def test_iteration_is_in_insertion_order():
    """The property the whole feature rests on. Not sorted, not hashed —
    the order the keys were written, which is what Python does."""
    out = run(in_main(
        '    d: dict[str, int] = {"z": 1, "a": 2, "m": 3}\n'
        "    for k in d:\n"
        "        put(k)\n"
        '    print("")\n'))
    assert out.strip() == "zam"


def test_the_keys_become_a_constant_array():
    c_code, _ = compile_source(in_main(
        '    d: dict[str, int] = {"a": 1, "b": 2}\n'
        '    print(d["a"])\n'))
    assert "static const py_str d__keys[2] = {\"a\", \"b\"};" in c_code
    assert "py_int d__vals[2] = {1LL, 2LL};" in c_code


def test_a_missing_key_is_a_keyerror_at_runtime():
    """Python raises KeyError; C would read past the end. The generated code
    stops with the same message and the same exit status."""
    c_code, _ = compile_source(
        'def pick(k: str) -> int:\n'
        '    d: dict[str, int] = {"a": 1}\n'
        '    return d[k]\n'
        'def main() -> int:\n'
        '    print(pick("a"))\n'
        '    print(pick("zzz"))\n'
        "    return 0\n")
    code, out, err = build_and_run(c_code)
    assert out.strip() == "1"
    assert code == 1
    assert "KeyError" in err


# ------------------------------------------------------- what it refuses

def test_a_dict_needs_an_annotation():
    with pytest.raises(SemanticError, match="may only initialise a variable"):
        check(in_main('    d = {"a": 1}\n'))


def test_an_empty_dict_is_refused():
    with pytest.raises(ParseError, match="no key set"):
        check(in_main("    d: dict[str, int] = {}\n"))


def test_keys_must_be_literals():
    with pytest.raises(SemanticError, match="literal string or int"):
        check(in_main('    k: str = "a"\n    d: dict[str, int] = {k: 1}\n'))


def test_duplicate_keys_are_refused():
    """Python keeps the last one silently. Here the key set is the type, so
    writing one twice is a mistake worth reporting."""
    with pytest.raises(SemanticError, match="duplicate key"):
        check(in_main('    d: dict[str, int] = {"a": 1, "a": 2}\n'))


def test_a_wrong_key_type_is_refused():
    with pytest.raises(SemanticError, match="keys but"):
        check(in_main('    d: dict[str, int] = {1: 1}\n'))


def test_a_wrong_value_type_is_refused():
    with pytest.raises(SemanticError, match="values but"):
        check(in_main('    d: dict[str, int] = {"a": 1.5}\n'))


def test_indexing_with_the_wrong_key_type():
    with pytest.raises(SemanticError, match="cannot be indexed with"):
        check(in_main('    d: dict[str, int] = {"a": 1}\n    print(d[0])\n'))


def test_a_dict_cannot_be_replaced():
    with pytest.raises(SemanticError, match="cannot be replaced"):
        check(in_main('    d: dict[str, int] = {"a": 1}\n'
                      '    d: dict[str, int] = {"b": 2}\n'))


def test_a_dict_literal_cannot_appear_mid_expression():
    with pytest.raises(SemanticError, match="nowhere else"):
        check(in_main('    x: int = len({"a": 1})\n'))


def test_a_nested_value_type_is_refused():
    with pytest.raises(ParseError, match="not a dict value type"):
        check(in_main("    d: dict[str, list] = {}\n"))


def test_in_needs_a_dict():
    with pytest.raises(SemanticError, match="is not one"):
        check(in_main('    xs: list[int] = [1, 2]\n    print(1 in xs)\n'))


def test_for_over_a_non_dict_explains_itself():
    with pytest.raises(SemanticError, match="needs"):
        check(in_main("    x: int = 1\n    for k in x:\n        print(k)\n"))


# ---------------------------------------------------------- in a class

CLASS_DICT = (
    "class Cube:\n"
    "    COLORS: dict[str, str] = {\n"
    '        "W": "#FFFFFF",\n'
    '        "Y": "#FFD500",\n'
    '        "G": "#22A447",\n'
    "    }\n"
    "    moves: int\n"
    "    def __init__(self) -> None:\n"
    "        self.moves = 0\n"
    "    def hex_for(self, face: str) -> str:\n"
    "        return self.COLORS[face]\n"
    "    def knows(self, face: str) -> bool:\n"
    "        return face in self.COLORS\n"
)


def test_a_class_level_dict_is_shared_and_read_only():
    """It is one object for every instance, which is what Python does — and
    safe, because assigning to a class constant is already an error."""
    c_code, _ = compile_source(CLASS_DICT + "\ndef main() -> int:\n"
                               "    c: Cube = Cube()\n"
                               '    print(c.hex_for("W"))\n'
                               "    return 0\n")
    assert "static const py_str Cube__COLORS__keys[3]" in c_code


def test_a_class_dict_reads_through_self_and_through_the_class():
    out = run(CLASS_DICT + "\ndef main() -> int:\n"
              "    c: Cube = Cube()\n"
              '    print(c.hex_for("Y"), Cube.COLORS["G"], len(Cube.COLORS))\n'
              '    print(c.knows("W"), c.knows("Z"))\n'
              "    return 0\n")
    assert out.strip().splitlines() == ["#FFD500 #22A447 3", "True False"]


def test_iterating_a_class_dict_keeps_insertion_order():
    out = run(CLASS_DICT + "\ndef main() -> int:\n"
              "    for face in Cube.COLORS:\n"
              "        put(face)\n"
              '    print("")\n'
              "    return 0\n")
    assert out.strip() == "WYG"


def test_a_class_dict_cannot_be_assigned_to():
    with pytest.raises(SemanticError, match="shared by every instance"):
        check(CLASS_DICT.replace(
            "        self.moves = 0\n",
            '        self.moves = 0\n        self.COLORS = 1\n') +
            "\ndef main() -> int:\n    return 0\n")


# ------------------------------------------------------------- the demo

@needs_c_compiler
def test_the_oop_rubik_demo_matches_cpython():
    """demos/rubik_oop.py is the pasted tkinter program's model and renderer,
    kept as close to the original as the subset allows — same classes, same
    COLORS dict, same method names, same unfolded cross."""
    import os
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    demo = os.path.join(root, "demos", "rubik_oop.py")
    source = open(demo, encoding="utf-8").read()

    oracle = ("import sys\ndef put(s): sys.stdout.write(s)\n\n"
              + source + "\n\nmain()\n")
    expected = subprocess.run([sys.executable, "-c", oracle],
                              capture_output=True, text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr

    c_code, stats = compile_source(source)
    assert stats["ai_calls_used"] == 0
    code, actual, err = build_and_run(c_code, timeout=120)
    assert code == 0, err
    assert actual == expected.stdout
    assert "#FFFFFF" in actual                 # the palette survived
    # solved at the start, the three cube identities, and after undoing
    assert actual.count("solved: True") == 5
    assert actual.count("solved: False") == 2  # after R, and after scrambling
