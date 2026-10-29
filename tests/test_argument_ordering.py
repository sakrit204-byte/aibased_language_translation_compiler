"""Hoisting arguments must not cost an argument its identity.

Python evaluates a call's arguments left to right, and C does not promise any
order at all, so when a statement holds more than one sub-expression the
emitter computes them into temporaries first and then re-emits the statement
against those. That is correct and it is tested below.

What it used to do as well was replace *every* argument with its temporary,
including a plain name. That broke a rule the call needs: an array argument
has to be a name, because the call passes the array's length beside it, and an
object argument has to be a name, because the call passes its address. Neither
can be recovered from a rendered expression. So

    draw_cube(state, offset_for(cube), is_solved(state, base))

- a name beside two calls, which is exactly when the ordering pass runs - was
refused with "an array argument must be a plain name" about an argument that
was a plain name. One call beside the array was fine; two were not, which is
the kind of seam nobody would think to try.

Keeping the name is also free. Hoisting exists to pin down evaluation order,
and reading a local cannot be affected by the calls beside it: a callee has no
way to assign to its caller's variable, and an array is passed as a pointer
whose value no callee can change. Its *contents* can change, which is why the
differential test below checks exactly that.
"""

import pytest

from conftest import needs_c_compiler
from src.driver import build_and_run, compile_source

ORDERING = (
    "def bump(a: list[int]) -> int:\n"
    "    a[0] = a[0] + 1\n"
    "    return a[0]\n"
    "\n"
    "\n"
    "def show(a: list[int], x: int, y: int) -> None:\n"
    "    print(x)\n"
    "    print(y)\n"
    "    print(a[0])\n"
    "\n"
    "\n"
    "def main() -> int:\n"
    "    arr: list[int] = [0] * 2\n"
    "    show(arr, bump(arr), bump(arr))\n"
    "    return 0\n"
)


def test_an_array_argument_survives_two_calls_beside_it():
    """The regression. One call beside it always worked; two did not."""
    source = (
        "def take(a: list[int], n: int, m: int) -> None:\n"
        "    print(a[0] + n + m)\n"
        "\n"
        "\n"
        "def f(x: int) -> int:\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def g(a: list[int], k: int) -> int:\n"
        "    return a[k]\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    arr: list[int] = [0] * 4\n"
        "    take(arr, f(1), g(arr, 2))\n"
        "    return 0\n"
    )
    c_code, _ = compile_source(source)
    # The length still goes with it, which is the thing the name was needed
    # for in the first place.
    assert "take(arr, 4" in c_code


def test_one_call_beside_an_array_argument_still_works():
    """Pinned so a future change cannot fix the two-call case by breaking
    the one-call case."""
    source = (
        "def take(a: list[int], n: int) -> None:\n"
        "    print(a[0] + n)\n"
        "\n"
        "\n"
        "def f(x: int) -> int:\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    arr: list[int] = [0] * 4\n"
        "    take(arr, f(1))\n"
        "    return 0\n"
    )
    assert "take(arr, 4" in compile_source(source)[0]


def test_the_arguments_are_still_ordered():
    """The hoisting is still happening: the calls are computed into
    temporaries before the call that uses them."""
    c_code, _ = compile_source(ORDERING)
    # Each call lands in its own temporary, and both are computed before the
    # call that reads them. The array goes in by name, with its length.
    assert "py_int t__1 = bump(arr, 2);" in c_code
    assert "py_int t__2 = bump(arr, 2);" in c_code
    assert "show(arr, 2, t__1, t__2);" in c_code
    assert c_code.index("t__1 = bump") < c_code.index("show(arr, 2, t__1")


@needs_c_compiler
def test_the_order_matches_cpython_when_the_calls_mutate_the_array():
    """The case the change could have broken: the name is read in place, so
    its *contents* must still be the contents after both calls ran.

    CPython prints 1, 2, 2 - the two bumps in order, then the array as it
    stands afterwards.
    """
    c_code, _ = compile_source(ORDERING)
    code, out, err = build_and_run(c_code)
    assert code == 0, err
    assert out.split() == ["1", "2", "2"]


@needs_c_compiler
def test_an_index_target_is_still_ordered_against_its_value():
    """A different statement shape through the same code path."""
    source = (
        "def pick(a: list[int]) -> int:\n"
        "    a[3] = a[3] + 1\n"
        "    return a[3]\n"
        "\n"
        "\n"
        "def main() -> int:\n"
        "    arr: list[int] = [0] * 4\n"
        "    arr[pick(arr)] = pick(arr)\n"
        "    print(arr[1])\n"
        "    print(arr[3])\n"
        "    return 0\n"
    )
    c_code, _ = compile_source(source)
    code, out, err = build_and_run(c_code)
    assert code == 0, err
    # CPython evaluates the index first, then the value: arr[1] = 2.
    assert out.split() == ["2", "2"]
