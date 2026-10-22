"""
Classes as value types.

A class is a C struct plus functions that take a pointer to one. See
semantic/classes.py for why that shape, and what it rules out: no allocator
means an object cannot outlive its frame, and no function values means no
vtable, so no inheritance and no dynamic dispatch.

The tests that matter are the ones where the C could quietly disagree with
Python — whether a method sees the caller's instance, whether two instances
share storage, and in what order arguments are evaluated.
"""

import pytest

from tests.conftest import needs_c_compiler

from src.driver import build_and_run, compile_source
from src.lexer.lexer import Lexer
from src.parser.parser import ParseError, Parser
from src.semantic import classes
from src.semantic.type_checker import SemanticError, TypeChecker

MAIN = "\ndef main() -> int:\n    return 0\n"

SIMPLE = (
    "class Counter:\n"
    "    total: int\n"
    "    def __init__(self) -> None:\n"
    "        self.total = 0\n"
    "    def bump(self) -> None:\n"
    "        self.total = self.total + 1\n"
    "    def value(self) -> int:\n"
    "        return self.total\n"
)


def check(source):
    ast = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker()
    checker.check(ast)
    return checker


def run(source):
    c_code, _ = compile_source(source)
    code, out, err = build_and_run(c_code)
    assert code == 0, "exit %d\n%s" % (code, err)
    return out


# -------------------------------------------------------------- the type

def test_instance_type_round_trips():
    t = classes.make("Cube")
    assert classes.is_instance(t)
    assert classes.class_of(t) == "Cube"
    assert classes.describe(t) == "Cube"


def test_a_scalar_is_not_an_instance():
    for t in ("int", "float", "str", "bool", "int[9]"):
        assert not classes.is_instance(t)


def test_private_is_the_python_rule():
    assert classes.is_private("__faces")
    assert not classes.is_private("faces")
    assert not classes.is_private("__init__")        # dunder is not private


# ------------------------------------------------------------- it works

def test_a_class_compiles_and_runs():
    out = run(SIMPLE + "\ndef main() -> int:\n"
              "    c: Counter = Counter()\n"
              "    c.bump()\n"
              "    c.bump()\n"
              "    print(c.value(), c.total)\n"
              "    return 0\n")
    assert out.strip() == "2 2"


def test_a_class_becomes_a_struct_and_free_functions():
    c_code, _ = compile_source(SIMPLE + MAIN)
    assert "} Counter;" in c_code
    assert "void Counter__bump(Counter * self)" in c_code
    assert "self->total" in c_code


def test_two_instances_share_nothing():
    out = run(SIMPLE + "\ndef main() -> int:\n"
              "    a: Counter = Counter()\n"
              "    b: Counter = Counter()\n"
              "    a.bump()\n"
              "    print(a.value(), b.value())\n"
              "    return 0\n")
    assert out.strip() == "1 0"


def test_a_method_mutates_the_callers_instance():
    """self is a pointer, so this comes free — and it is what Python does."""
    out = run(SIMPLE + "\ndef twice(c: Counter) -> None:\n"
              "    c.bump()\n"
              "    c.bump()\n"
              "\ndef main() -> int:\n"
              "    c: Counter = Counter()\n"
              "    twice(c)\n"
              "    print(c.value())\n"
              "    return 0\n")
    assert out.strip() == "2"


def test_an_object_argument_is_passed_as_a_pointer():
    c_code, _ = compile_source(
        SIMPLE + "\ndef f(c: Counter) -> int:\n    return c.value()\n"
        "\ndef main() -> int:\n"
        "    c: Counter = Counter()\n"
        "    print(f(c))\n"
        "    return 0\n")
    assert "py_int f(Counter * c)" in c_code
    assert "f(&c)" in c_code


def test_an_array_field_is_inline_storage():
    c_code, _ = compile_source(
        "class Grid:\n"
        "    cells: list[int, 4]\n"
        "    def __init__(self) -> None:\n"
        "        self.cells = [0] * 4\n" + MAIN)
    assert "py_int cells[4];" in c_code


def test_array_fields_are_per_instance():
    out = run(
        "class Grid:\n"
        "    cells: list[int, 3]\n"
        "    def __init__(self) -> None:\n"
        "        self.cells = [0] * 3\n"
        "    def put(self, i: int, v: int) -> None:\n"
        "        self.cells[i] = v\n"
        "    def get(self, i: int) -> int:\n"
        "        return self.cells[i]\n"
        "\ndef main() -> int:\n"
        "    a: Grid = Grid()\n"
        "    b: Grid = Grid()\n"
        "    a.put(0, 9)\n"
        "    print(a.get(0), b.get(0))\n"
        "    return 0\n")
    assert out.strip() == "9 0"


def test_a_class_constant_is_inlined():
    c_code, _ = compile_source(
        "class A:\n"
        "    SIZE: int = 7\n"
        "    x: int\n"
        "    def __init__(self) -> None:\n"
        "        self.x = 0\n"
        "    def size(self) -> int:\n"
        "        return self.SIZE\n" + MAIN)
    assert "return 7;" in c_code
    assert "SIZE" not in c_code.split("} A;")[0]      # not a struct member


def test_a_constant_reads_through_the_class_too():
    out = run(
        "class A:\n"
        "    SIZE: int = 7\n"
        "    x: int\n"
        "    def __init__(self) -> None:\n"
        "        self.x = 0\n"
        "\ndef main() -> int:\n"
        "    print(A.SIZE)\n"
        "    return 0\n")
    assert out.strip() == "7"


# ------------------------------------------------------------ what it refuses

def test_inheritance_is_refused_with_the_reason():
    with pytest.raises(ParseError) as excinfo:
        check("class A:\n    x: int\n    def __init__(self) -> None:\n"
              "        self.x = 0\n"
              "class B(A):\n    y: int\n    def __init__(self) -> None:\n"
              "        self.y = 0\n" + MAIN)
    assert "dynamic dispatch" in str(excinfo.value)


def test_init_is_required():
    with pytest.raises(SemanticError, match="needs an __init__"):
        check("class A:\n    x: int\n    def get(self) -> int:\n"
              "        return self.x\n" + MAIN)


def test_init_must_assign_every_field():
    """An unset C struct member holds whatever was on the stack; Python would
    not have the attribute at all. Requiring __init__ to set them is what
    makes the two agree."""
    with pytest.raises(SemanticError, match="leaves 'y' unassigned"):
        check("class A:\n    x: int\n    y: int\n"
              "    def __init__(self) -> None:\n        self.x = 0\n" + MAIN)


def test_a_private_field_cannot_be_read_from_outside():
    with pytest.raises(SemanticError, match="is private to"):
        check("class A:\n    __x: int\n    def __init__(self) -> None:\n"
              "        self.__x = 1\n"
              "\ndef main() -> int:\n"
              "    a: A = A()\n"
              "    return a.__x\n")


def test_a_private_field_is_reachable_from_inside():
    out = run("class A:\n    __x: int\n    def __init__(self) -> None:\n"
              "        self.__x = 42\n"
              "    def get(self) -> int:\n        return self.__x\n"
              "\ndef main() -> int:\n"
              "    a: A = A()\n"
              "    print(a.get())\n"
              "    return 0\n")
    assert out.strip() == "42"


def test_an_unknown_member_lists_what_exists():
    with pytest.raises(SemanticError) as excinfo:
        check(SIMPLE + "\ndef main() -> int:\n"
              "    c: Counter = Counter()\n"
              "    return c.nope\n")
    assert "has no member 'nope'" in str(excinfo.value)
    assert "total" in str(excinfo.value)


def test_a_method_is_not_a_value():
    with pytest.raises(SemanticError, match="needs parentheses"):
        check(SIMPLE + "\ndef main() -> int:\n"
              "    c: Counter = Counter()\n"
              "    return c.value\n")


def test_a_method_cannot_return_an_object():
    with pytest.raises(SemanticError, match="returns an object"):
        check("class A:\n    x: int\n    def __init__(self) -> None:\n"
              "        self.x = 1\n"
              "    def me(self) -> A:\n        return self\n" + MAIN)


def test_a_field_cannot_be_another_object():
    with pytest.raises(SemanticError, match="is another object"):
        check("class A:\n    x: int\n    def __init__(self) -> None:\n"
              "        self.x = 1\n"
              "class B:\n    a: A\n    def __init__(self) -> None:\n"
              "        self.a = 0\n" + MAIN)


def test_a_shared_mutable_default_is_refused():
    """`xs: list[int] = [0] * 4` in a class body is ONE list for every
    instance in Python. That would silently disagree with per-instance C
    storage, so it is refused and the alternative is spelled out."""
    with pytest.raises(SemanticError) as excinfo:
        check("class A:\n    xs: list[int] = [0] * 4\n"
              "    def __init__(self) -> None:\n        self.xs = [0] * 4\n" + MAIN)
    message = str(excinfo.value)
    assert "shared by every instance" in message
    assert "list[int, N]" in message


def test_an_array_field_needs_its_size():
    with pytest.raises(SemanticError, match="needs its length in the annotation"):
        check("class A:\n    xs: list[int]\n"
              "    def __init__(self) -> None:\n        self.xs = [0] * 4\n" + MAIN)


def test_an_object_cannot_be_replaced():
    with pytest.raises(SemanticError, match="already holds an object"):
        check(SIMPLE + "\ndef main() -> int:\n"
              "    c: Counter = Counter()\n"
              "    c: Counter = Counter()\n"
              "    return 0\n")


def test_a_constant_cannot_be_assigned():
    with pytest.raises(SemanticError, match="shared by every instance"):
        check("class A:\n    S: int = 3\n    x: int\n"
              "    def __init__(self) -> None:\n"
              "        self.x = 1\n        self.S = 9\n" + MAIN)


def test_init_cannot_be_called_again():
    with pytest.raises(SemanticError, match="cannot be called again"):
        check(SIMPLE.replace("def bump", "def unused_bump") +
              "\ndef main() -> int:\n"
              "    c: Counter = Counter()\n"
              "    c.__init__()\n"
              "    return 0\n")


def test_a_method_needs_self():
    with pytest.raises(SemanticError, match="needs `self`"):
        check("class A:\n    x: int\n    def __init__(self) -> None:\n"
              "        self.x = 1\n    def f() -> int:\n        return 1\n" + MAIN)


def test_a_whole_array_field_cannot_be_reassigned_outside_init():
    with pytest.raises(SemanticError, match="storage, not a reference"):
        check("class A:\n    xs: list[int, 4]\n"
              "    def __init__(self) -> None:\n        self.xs = [0] * 4\n"
              "    def f(self) -> None:\n        self.xs = [1] * 4\n" + MAIN)


def test_a_dot_on_a_non_object_still_explains_itself():
    with pytest.raises(SemanticError) as excinfo:
        check("def main() -> int:\n    x: int = 1\n    return x.real\n")
    assert "not an object" in str(excinfo.value)


# -------------------------------------------- evaluation order, left to right

@needs_c_compiler
def test_arguments_are_evaluated_left_to_right():
    """Python orders arguments strictly left to right; C does not specify an
    order and gcc often picks right to left.

    This was invisible until arrays and objects gave a function something it
    could change — every function before that was pure. `print(take(xs),
    peek(xs))` now means one thing in both languages only because codegen
    computes each argument into a temporary first.
    """
    source = (
        "def take(a: list[int]) -> int:\n"
        "    v: int = a[0]\n"
        "    a[0] = 0\n"
        "    return v\n"
        "\ndef peek(a: list[int]) -> int:\n"
        "    return a[0]\n"
        "\ndef main() -> int:\n"
        "    xs: list[int] = [7] * 2\n"
        "    print(take(xs), peek(xs))\n"
        "    return 0\n"
    )
    assert run(source).strip() == "7 0"


@needs_c_compiler
def test_operands_are_evaluated_left_to_right():
    source = (
        "def take(a: list[int]) -> int:\n"
        "    v: int = a[0]\n"
        "    a[0] = 0\n"
        "    return v\n"
        "\ndef peek(a: list[int]) -> int:\n"
        "    return a[0]\n"
        "\ndef main() -> int:\n"
        "    xs: list[int] = [5] * 2\n"
        "    print(take(xs) + peek(xs))\n"
        "    return 0\n"
    )
    assert run(source).strip() == "5"


def test_ordinary_code_is_not_cluttered_with_temporaries():
    """Nothing is hoisted when at most one sub-expression can have an effect,
    so the generated C for normal code is unchanged."""
    c_code, _ = compile_source(
        "def main() -> int:\n"
        "    a: int = 1\n"
        "    print(a, a + 1, 7)\n"
        "    return 0\n")
    assert "t__" not in c_code
