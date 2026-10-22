# Fixed-size arrays, diffed against CPython line by line.
#
# The interesting cases are the ones where C's defaults differ from Python's
# and Python has to win: a negative index counts from the end rather than
# reading backwards off the front, and an array passed to a function is the
# same storage the caller holds rather than a copy.


def fill_squares(values: list[int]) -> None:
    """Writes through the parameter. In C the array decayed to a pointer, so
    the caller sees these writes — which is what Python does too."""
    i: int = 0
    while i < len(values):
        values[i] = i * i
        i = i + 1


def total(values: list[int]) -> int:
    s: int = 0
    i: int = 0
    while i < len(values):
        s = s + values[i]
        i = i + 1
    return s


def largest(values: list[int]) -> int:
    best: int = values[0]
    i: int = 1
    while i < len(values):
        if values[i] > best:
            best = values[i]
        i = i + 1
    return best


def dot(a: list[float], b: list[float]) -> float:
    s: float = 0.0
    i: int = 0
    while i < len(a):
        s = s + a[i] * b[i]
        i = i + 1
    return s


def reverse(values: list[int]) -> None:
    i: int = 0
    j: int = len(values) - 1
    while i < j:
        hold: int = values[i]
        values[i] = values[j]
        values[j] = hold
        i = i + 1
        j = j - 1


def bubble_sort(values: list[int]) -> None:
    n: int = len(values)
    i: int = 0
    while i < n:
        j: int = 0
        while j < n - i - 1:
            if values[j] > values[j + 1]:
                hold: int = values[j]
                values[j] = values[j + 1]
                values[j + 1] = hold
            j = j + 1
        i = i + 1


def show(values: list[int]) -> None:
    i: int = 0
    while i < len(values):
        if i > 0:
            put(" ")
        print_one(values[i])
        i = i + 1
    print("")


def print_one(v: int) -> None:
    """put() takes a str, so a number is spelled out a digit at a time."""
    if v < 0:
        put("-")
        v = -v
    if v >= 10:
        print_one(v // 10)
    put_digit(v % 10)


def put_digit(d: int) -> None:
    if d == 0:
        put("0")
    elif d == 1:
        put("1")
    elif d == 2:
        put("2")
    elif d == 3:
        put("3")
    elif d == 4:
        put("4")
    elif d == 5:
        put("5")
    elif d == 6:
        put("6")
    elif d == 7:
        put("7")
    elif d == 8:
        put("8")
    else:
        put("9")


def main() -> int:
    # zero-filled, then written through a function
    squares: list[int] = [0] * 8
    fill_squares(squares)
    show(squares)
    print(total(squares))
    print(largest(squares))

    # literal initialiser
    primes: list[int] = [2, 3, 5, 7, 11, 13]
    show(primes)
    print(len(primes))
    print(primes[0], primes[5])

    # negative indices count from the end, as Python's do
    print(primes[-1], primes[-2], primes[-6])

    # non-zero repeat
    sevens: list[int] = [7] * 4
    show(sevens)
    print(total(sevens))

    # in-place mutation through a function
    reverse(primes)
    show(primes)

    unsorted: list[int] = [9, 2, 7, 1, 8, 3]
    bubble_sort(unsorted)
    show(unsorted)

    # element arithmetic and augmented assignment
    counters: list[int] = [0] * 5
    i: int = 0
    while i < 5:
        counters[i] += i * 3
        counters[i] -= 1
        i = i + 1
    show(counters)

    # an index computed from other elements
    lookup: list[int] = [4, 0, 3, 1, 2]
    print(lookup[lookup[0]])

    # floats
    xs: list[float] = [1.5, 2.5, 3.0]
    ys: list[float] = [2.0, 4.0, 0.5]
    print(dot(xs, ys))
    xs[0] = 10.25
    print(xs[0] + xs[1])

    # bool elements
    flags: list[bool] = [True] * 3
    flags[1] = False
    print(flags[0], flags[1], flags[2])

    # a single-element array, and the largest the loop above touched
    one: list[int] = [42] * 1
    print(len(one), one[0], one[-1])
    return 0
