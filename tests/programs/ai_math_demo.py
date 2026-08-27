# The ONLY thing in this repo that needs AI-assist.
#
# `hypot`, `degrees` and `log` are not user-defined here and are not among the
# builtins this compiler translates statically, so with --ai each one is sent
# to the gate, which asks the local model for a C expression and refuses it
# unless it passes the safety check and compiles. Compile this without --ai and
# you get a hard error naming the missing function, not a silent guess.
#
# The file is not runnable as plain Python (these names live in `math`); the
# test harness supplies `from math import *` when using CPython as the oracle.

def diagonal(a: float, b: float) -> float:
    return hypot(a, b)


def main() -> int:
    print("diagonal:", diagonal(3.0, 4.0))
    print("degrees:", degrees(3.141592653589793))
    print("log:", log(1.0))
    return 0
