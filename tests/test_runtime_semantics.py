"""
Differential tests for the C runtime against CPython itself.

The helpers in codegen/runtime.py exist because C's operators disagree with
Python's. Reviewing them by eye is not evidence, so each is compiled into a
small C driver, fed a table of adversarial inputs, and its output compared to
what CPython computes for the same inputs.

`py_repr_float` gets a fuzz run over random bit patterns: exact float
formatting is the single most fiddly piece of the runtime, and "prints 3.5 not
3.500000" only matters if it is right for *every* double, not the easy ones.
"""

import math
import random
import struct
import subprocess
import tempfile
import os

import pytest

from conftest import C_COMPILER, needs_c_compiler
from src.codegen.runtime import prelude_for
from src.toolchain import compile_command, exe_suffix, run_env


def run_c(body: str, helpers) -> str:
    """Compile `body` as the contents of main() with `helpers` available."""
    source = prelude_for(set(helpers)) + "\nint main(void)\n{\n" + body + "\n    return 0;\n}\n"
    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "probe.c")
        bin_path = os.path.join(tmp, "probe" + exe_suffix())
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(source)
        env = run_env(C_COMPILER)
        build = subprocess.run(
            compile_command(C_COMPILER, c_path, bin_path),
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert build.returncode == 0, f"probe failed to build:\n{build.stderr}\n{source}"
        run = subprocess.run([bin_path], capture_output=True, text=True, env=env, timeout=60)
        assert run.returncode == 0, f"probe crashed: {run.stderr}"
        return run.stdout


# --------------------------------------------------------------- integer ops

INT_PAIRS = [
    (7, 2), (-7, 2), (7, -2), (-7, -2), (0, 5), (0, -5),
    (1, 1), (-1, 1), (1, -1), (-1, -1),
    (10, 3), (-10, 3), (10, -3), (-10, -3),
    (123456789, 1000), (-123456789, 1000),
    (2**40, 7), (-(2**40), 7),
]


@needs_c_compiler
def test_floor_division_matches_python():
    body = "\n".join(
        f'    printf("%lld\\n", (long long)py_floordiv_int({a}LL, {b}LL));'
        for a, b in INT_PAIRS
    )
    got = run_c(body, ["py_floordiv_int"]).split()
    expected = [str(a // b) for a, b in INT_PAIRS]
    assert got == expected


@needs_c_compiler
def test_modulo_matches_python():
    body = "\n".join(
        f'    printf("%lld\\n", (long long)py_mod_int({a}LL, {b}LL));'
        for a, b in INT_PAIRS
    )
    got = run_c(body, ["py_mod_int"]).split()
    expected = [str(a % b) for a, b in INT_PAIRS]
    assert got == expected


@needs_c_compiler
def test_integer_power_matches_python():
    cases = [(2, 0), (2, 10), (2, 62), (-2, 3), (-2, 4), (3, 5), (10, 18), (0, 0), (1, 63)]
    body = "\n".join(
        f'    printf("%lld\\n", (long long)py_pow_int({b}LL, {e}LL));' for b, e in cases
    )
    got = run_c(body, ["py_pow_int"]).split()
    assert got == [str(b ** e) for b, e in cases]


@needs_c_compiler
def test_round_int_matches_python_bankers_rounding():
    cases = [0.5, 1.5, 2.5, 3.5, -0.5, -1.5, -2.5, -3.5, 0.4, 0.6, -0.4, -0.6, 2.675, 1e15]
    body = "\n".join(
        f'    printf("%lld\\n", (long long)py_round_int({c!r}));' for c in cases
    )
    got = run_c(body, ["py_round_int"]).split()
    assert got == [str(round(c)) for c in cases]


@needs_c_compiler
def test_round_int_with_negative_ndigits_matches_python():
    cases = [(1234, -2), (1250, -2), (1350, -2), (-1250, -2), (-1350, -2), (5, -1), (15, -1)]
    body = "\n".join(
        f'    printf("%lld\\n", (long long)py_round_int_ndigits({x}LL, {n}LL));'
        for x, n in cases
    )
    got = run_c(body, ["py_round_int_ndigits"]).split()
    assert got == [str(round(x, n)) for x, n in cases]


# ----------------------------------------------------------------- float ops

FLOAT_PAIRS = [
    (7.5, 2.0), (-7.5, 2.0), (7.5, -2.0), (-7.5, -2.0),
    (1.0, 3.0), (-1.0, 3.0), (0.0, 5.0), (10.5, 0.25), (-10.5, 0.25),
]


@needs_c_compiler
def test_float_modulo_matches_python():
    body = "\n".join(
        f'    {{ char b[64]; py_repr_float(b, sizeof b, py_mod_float({a!r}, {c!r}));'
        f' printf("%s\\n", b); }}'
        for a, c in FLOAT_PAIRS
    )
    got = run_c(body, ["py_mod_float", "py_repr_float"]).splitlines()
    assert got == [repr(a % c) for a, c in FLOAT_PAIRS]


@needs_c_compiler
def test_float_floor_division_matches_python():
    body = "\n".join(
        f'    {{ char b[64]; py_repr_float(b, sizeof b, py_floordiv_float({a!r}, {c!r}));'
        f' printf("%s\\n", b); }}'
        for a, c in FLOAT_PAIRS
    )
    got = run_c(body, ["py_floordiv_float", "py_repr_float"]).splitlines()
    assert got == [repr(a // c) for a, c in FLOAT_PAIRS]


# ------------------------------------------------------------- float repr()

INTERESTING_FLOATS = [
    0.0, -0.0, 1.0, -1.0, 0.1, 0.2, 0.3, 1 / 3, 2 / 3,
    2.5, 3.14159265358979, 1e-1, 1e-4, 1e-5, 1e-10, 1e-300, 5e-324,
    1e15, 1e16, 1e17, 1e22, 1e300, 1.7976931348623157e308,
    123456789.0, 1234567890123456.0, 12345678901234567.0,
    9007199254740992.0, 0.30000000000000004, 2.675, 1.5e300, -2.5e-7,
]


def _repr_probe(values):
    # C99 hex float literals are exact, so the test compares formatting only —
    # not the decimal-literal parsing of the C compiler.
    body = "\n".join(
        f'    {{ char b[64]; py_repr_float(b, sizeof b, {v.hex()}); printf("%s\\n", b); }}'
        for v in values
    )
    return run_c(body, ["py_repr_float"]).splitlines()


@needs_c_compiler
def test_repr_of_interesting_floats_matches_python():
    got = _repr_probe(INTERESTING_FLOATS)
    assert got == [repr(v) for v in INTERESTING_FLOATS]


@needs_c_compiler
def test_repr_of_random_floats_matches_python():
    """Fuzz over random bit patterns, which is where formatting bugs hide."""
    rng = random.Random(20240827)
    values = []
    while len(values) < 400:
        bits = rng.getrandbits(64)
        v = struct.unpack("<d", struct.pack("<Q", bits))[0]
        if math.isnan(v) or math.isinf(v):
            continue
        values.append(v)
    # A band of ordinary magnitudes as well, since random bits skew extreme.
    values += [rng.uniform(-1e6, 1e6) for _ in range(200)]
    values += [rng.uniform(-1, 1) for _ in range(200)]

    got = _repr_probe(values)
    expected = [repr(v) for v in values]
    mismatches = [(v, e, g) for v, e, g in zip(values, expected, got) if e != g]
    assert not mismatches, f"{len(mismatches)} mismatch(es), first: {mismatches[0]}"


@needs_c_compiler
def test_repr_of_non_finite_floats():
    body = (
        '    { char b[64]; py_repr_float(b, sizeof b, 1.0/0.0*0.0); printf("%s\\n", b); }\n'
        '    { char b[64]; py_repr_float(b, sizeof b, 1e308*10); printf("%s\\n", b); }\n'
        '    { char b[64]; py_repr_float(b, sizeof b, -1e308*10); printf("%s\\n", b); }'
    )
    assert run_c(body, ["py_repr_float"]).splitlines() == ["nan", "inf", "-inf"]


# ------------------------------------------------------------- error paths

@needs_c_compiler
@pytest.mark.parametrize("expr,helper,message", [
    ("py_div_int(1, 0)", "py_div_int", "ZeroDivisionError: division by zero"),
    ("py_floordiv_int(1, 0)", "py_floordiv_int",
     "ZeroDivisionError: integer division or modulo by zero"),
    ("py_mod_int(1, 0)", "py_mod_int",
     "ZeroDivisionError: integer division or modulo by zero"),
    ("py_div_float(1.0, 0.0)", "py_div_float", "ZeroDivisionError: float division by zero"),
])
def test_division_by_zero_reports_like_python(expr, helper, message):
    source = (
        prelude_for({helper})
        + f"\nint main(void)\n{{\n    volatile double x = (double)({expr});\n"
        "    (void)x;\n    return 0;\n}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "probe.c")
        bin_path = os.path.join(tmp, "probe" + exe_suffix())
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(source)
        env = run_env(C_COMPILER)
        build = subprocess.run(
            compile_command(C_COMPILER, c_path, bin_path),
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert build.returncode == 0, build.stderr
        run = subprocess.run([bin_path], capture_output=True, text=True, env=env, timeout=60)
    assert run.returncode == 1
    assert message in run.stderr
