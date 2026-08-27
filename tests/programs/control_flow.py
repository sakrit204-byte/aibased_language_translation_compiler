def first_divisor(n: int) -> int:
    d = 2
    while d < n:
        if n % d == 0:
            return d
        d += 1
    return n


def count_evens(limit: int) -> int:
    total = 0
    for i in range(limit):
        if i % 2 != 0:
            continue
        total += i
    return total


def main() -> int:
    print("first divisor of 91:", first_divisor(91))
    print("first divisor of 97:", first_divisor(97))
    print("sum of evens below 10:", count_evens(10))

    for i in range(10, 0, -2):
        print("countdown", i)

    for i in range(3):
        for j in range(3):
            if j > i:
                break
            print(i, j)

    total = 0
    total += 5
    total -= 2
    total *= 4
    total //= 3
    total %= 3
    total **= 2
    print("augmented:", total)

    # A variable first assigned inside a branch stays visible afterwards.
    if total > 0:
        label = "positive"
    else:
        label = "non-positive"
    print("label:", label)
    return 0
