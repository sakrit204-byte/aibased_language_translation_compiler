# Every import form, and enough of `math` to catch a wrong translation.
#
# This program needs no AI gate: each member of `math` has a static C rule in
# src/semantic/modules.py, so the whole file is translated deterministically.
# That is the difference between `math.sqrt(x)` and a bare `sqrt(x)`.
#
# The oracle for this file is CPython running the same source, so every line
# printed below is a claim that the C and the Python agree exactly. The
# integer-returning cases are the interesting ones: C's floor() hands back a
# double, and printing 3.0 where CPython prints 3 would be a real bug.
#
# The transcendental cases (sin, log, atan2) assume both sides call the same
# libm, which holds here because CPython on Windows and this toolchain both
# target the UCRT. They are kept because that assumption breaking is itself
# worth a failing test.

import math
from math import sqrt, hypot
from math import degrees as to_degrees


def circle_area(r: float) -> float:
    return math.pi * r * r


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1))


def main() -> int:
    # constants
    print(math.pi)
    print(math.e)
    print(math.tau)

    # exact by IEEE, whatever the libm
    print(math.sqrt(2.0))
    print(math.sqrt(144.0))
    print(sqrt(0.0))
    print(math.fabs(-2.5))
    print(math.copysign(3.0, -1.0))
    print(hypot(3.0, 4.0))
    print(math.pow(2.0, 10.0))

    # Python returns an int here and C returns a double; Python wins
    print(math.floor(3.7))
    print(math.floor(-3.7))
    print(math.ceil(3.2))
    print(math.ceil(-3.2))
    print(math.trunc(3.9))
    print(math.trunc(-3.9))

    # plain arithmetic under another name
    print(to_degrees(math.pi))
    print(math.radians(180.0))

    # predicates come back as bool, so they print True/False, not 1/0
    print(math.isnan(0.0))
    print(math.isinf(1.0))
    print(math.isfinite(1.0))

    # transcendentals
    print(math.sin(0.0))
    print(math.cos(0.0))
    print(math.exp(0.0))
    print(math.log(1.0))
    print(math.log10(1000.0))
    print(math.log2(1024.0))
    print(math.log(8.0, 2.0))
    print(math.atan2(1.0, 1.0))

    # through user functions, to check the types survive a call boundary
    print(circle_area(2.0))
    print(distance(0.0, 0.0, 3.0, 4.0))

    # a loop, so the translation is exercised more than once
    i: int = 1
    total: float = 0.0
    while i <= 5:
        total = total + math.sqrt(float(i))
        i = i + 1
    print(total)
    return 0
