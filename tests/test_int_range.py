"""Integer width: the subset computes in int64, and Python's int does not.

Two separate defects lived here, and both of them printed a wrong number with
no error at all, which is the worst thing a compiler can do. A diagnostic can
be read, argued with, and handed to the repair path. A wrong answer that looks
like an answer cannot.

1. Integer literals were emitted bare, so C typed them `int` and computed the
   whole expression in 32 bits. `1000000 * 1000000 * 1000000` is 10**18, which
   fits in an int64 with room to spare, and it printed -1486618624.

2. A value that genuinely does not fit in 64 bits, like `2 ** 100`, compiled
   without a word of complaint and printed 0.

The tests below pin both, and equally pin the boundary cases that must still be
accepted, because a range check that refuses valid programs has only traded one
wrong behaviour for another.
"""

import time

import pytest

from conftest import needs_c_compiler
from src.driver import CompileError, build_and_run, compile_source

INT64_MAX = 9223372036854775807
INT64_MIN = -9223372036854775808


def in_main(body):
    lines = "".join("    " + line + "\n" for line in body.splitlines())
    return "def main() -> None:\n" + lines


def emit(body):
    c_code, _ = compile_source(in_main(body))
    return c_code


def run(body):
    c_code, _ = compile_source(in_main(body))
    code, out, err = build_and_run(c_code)
    assert code == 0, f"generated program failed (exit {code}):\n{err}"
    return out


def refusal(body):
    """The compiler's complaint about `body`, or a failure if it had none."""
    with pytest.raises(CompileError) as caught:
        compile_source(in_main(body))
    return str(caught.value)


# --------------------------------------------------------------- 32 vs 64 bit

def test_every_integer_literal_carries_the_64_bit_suffix():
    """Without LL, C types the literal `int` and computes in 32 bits."""
    c = emit("x = 2\ny = x + 1\n")
    assert "2LL" in c
    assert "1LL" in c


def test_a_small_literal_is_suffixed_too():
    """The suffix is not reserved for large literals: the operands decide the
    width of the arithmetic, so a small one has to carry it as well."""
    c = emit("print(0)\n")
    assert "0LL" in c


@needs_c_compiler
def test_a_product_that_overflows_32_bits_is_still_computed_in_64():
    assert run("print(1000000 * 1000000 * 1000000)\n") == "1000000000000000000\n"


@needs_c_compiler
def test_a_sum_just_past_the_top_of_int32_is_not_wrapped():
    assert run("print(2147483647 + 1)\n") == "2147483648\n"


@needs_c_compiler
def test_a_square_past_int32_is_not_wrapped():
    assert run("print(50000 * 50000)\n") == "2500000000\n"


# ------------------------------------------------------- beyond int64 at all

def test_a_power_beyond_64_bits_is_refused_rather_than_wrapped():
    message = refusal("print(2 ** 100)\n")
    assert "does not fit in a 64-bit integer" in message
    # The value itself belongs in the message: it is what the program meant.
    assert "1267650600228229401496703205376" in message


def test_one_past_the_top_of_int64_is_refused():
    message = refusal(f"print({INT64_MAX} + 1)\n")
    assert "does not fit in a 64-bit integer" in message


def test_a_literal_beyond_64_bits_is_refused():
    message = refusal("print(99999999999999999999)\n")
    assert "does not fit in a 64-bit integer" in message


def test_the_refusal_says_what_to_do_instead():
    """A diagnostic that only says no is a worse diagnostic. This one names
    the representation that would work, which is also the one the repair path
    has been shown to reach for."""
    assert "digits in an array" in refusal("print(2 ** 100)\n")


def test_a_hopeless_exponent_is_refused_without_computing_it():
    """`2 ** 100000000` has an exact value, and computing it to find out that
    it is too large is how you hang the compiler. It is decided on size."""
    started = time.time()
    message = refusal("print(2 ** 100000000)\n")
    assert "far larger than a 64-bit integer" in message
    assert time.time() - started < 5, "the power was computed before it was refused"


# ------------------------------------------------------- what must still work

def test_the_top_of_int64_is_accepted():
    assert str(INT64_MAX) in emit(f"print({INT64_MAX})\n")


@pytest.mark.parametrize("body", [
    "print(2 ** 62)\n",
    "print(10 ** 18)\n",
    "print(-2 ** 63)\n",
    f"print({INT64_MIN})\n",
])
def test_a_value_that_fits_is_not_refused(body):
    """A range check that refuses valid programs has traded one wrong
    behaviour for another."""
    emit(body)


def test_int64_min_is_emitted_the_way_c_can_spell_it():
    """int64's lower bound has no positive counterpart, so 9223372036854775808
    is not a valid `long long` literal in C either. Every C header writes this
    one constant as one away from the top."""
    c = emit(f"print({INT64_MIN})\n")
    assert "(-9223372036854775807LL - 1)" in c
    assert "9223372036854775808LL" not in c


def test_the_lower_bound_is_reachable_as_a_negated_power_too():
    """`-2 ** 63` is a minus sign applied to 2**63. Judging the operand on its
    own would refuse a program that is perfectly representable."""
    emit("print(-2 ** 63)\n")


@needs_c_compiler
@pytest.mark.parametrize("expression, expected", [
    ("2 ** 62", "4611686018427387904"),
    ("10 ** 18", "1000000000000000000"),
    ("-2 ** 63", "-9223372036854775808"),
    (str(INT64_MIN), "-9223372036854775808"),
    (str(INT64_MAX), "9223372036854775807"),
    ("1000000 * 1000000 * 1000000", "1000000000000000000"),
])
def test_what_is_accepted_prints_what_cpython_prints(expression, expected):
    """The point of the whole exercise: accepted means the same bytes."""
    assert eval(expression) == int(expected)      # the reference, stated here
    assert run(f"print({expression})\n") == expected + "\n"


# ----------------------------------------------------------- not over-reaching

def test_a_variable_is_not_folded_and_so_not_refused():
    """Only constant expressions are folded. A value that depends on a
    variable is not known at compile time, so there is nothing to check and
    the program must not be refused on suspicion."""
    emit("n = 2\nprint(n ** 100)\n")


def test_a_float_expression_is_left_alone():
    """Floats have their own range, and 2.0 ** 100 is an ordinary double."""
    emit("print(2.0 ** 100)\n")
