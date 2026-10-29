# A constant too large for 64 bits, and nothing else wrong with the program.
#
# This is the narrowest possible repair case for integer width. Every line is
# otherwise inside the subset: no f-string, no growing list, no string work.
# The one and only reason the compiler refuses it is the range check, so if
# repair lands this, the new diagnostic is reaching the model and the model is
# acting on it.
#
# It used to be worse than a refusal. `print(2 ** 100)` compiled without a
# complaint and printed 0, which no amount of repair could help with, because
# repair never runs on a program that compiled.
#
# tests/corpus/bignum.py is the runtime version of the same problem: there the
# overflow happens inside factorial(), where no compile-time check can see it,
# and only the output comparison catches the wrong answer.


def main() -> int:
    print(2 ** 100)
    print(10 ** 25 + 7)
    print(2 ** 64)
    return 0
