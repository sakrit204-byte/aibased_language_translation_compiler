# 25! does not fit in 64 bits. CPython prints 15511210043330985984000000;
# a py_int is 64-bit and wraps, so the obvious translation is silently wrong.
#
# The f-string is what the compiler refuses first, which is what sends this to
# repair at all. The interesting part is what happens next: a rewrite that
# multiplies into a py_int compiles, runs, and prints the wrong number, so it
# is rejected by the output comparison and the model has to carry the digits
# itself.


def factorial(n):
    total = 1
    for i in range(2, n + 1):
        total *= i
    return total


def main() -> int:
    for n in (5, 10, 25):
        print(f"{n}! = {factorial(n)}")
    return 0
