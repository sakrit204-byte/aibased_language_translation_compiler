# Lazy evaluation: a generator consumed by itertools, plus a reduce. Nothing
# here has a value until something pulls on it, and the subset has no way to
# suspend a function, so the rewrite has to turn pull into push.
import functools
import itertools


def triangles():
    total = 0
    n = 1
    while True:
        total += n
        yield total
        n += 1


def main() -> int:
    first = list(itertools.islice(triangles(), 8))
    print("triangles", first)

    running = list(itertools.accumulate(first))
    print("accumulated", running)

    product = functools.reduce(lambda a, b: a * b, first[:5], 1)
    print("product of five", product)

    pairs = list(zip(first, first[1:]))
    gaps = [b - a for a, b in pairs]
    print("gaps", gaps)
    return 0
