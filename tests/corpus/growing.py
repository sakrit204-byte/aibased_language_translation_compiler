# A list whose length is not written down anywhere: it depends on how many
# numbers below 40 survive the filter. The subset's arrays carry their length
# in their type, so the rewrite has to establish a bound and keep a count.
#
# Also uses len(), slicing, sorted() and `in`, none of which the subset has.


def collatz_steps(n):
    steps = 0
    while n != 1:
        if n % 2 == 0:
            n //= 2
        else:
            n = 3 * n + 1
        steps += 1
    return steps


def main() -> int:
    long_ones = []
    for start in range(2, 40):
        steps = collatz_steps(start)
        if steps > 15:
            long_ones.append(start)

    print("count", len(long_ones))
    print("first three", long_ones[:3])
    print("sorted desc", sorted(long_ones, reverse=True)[:3])
    print("has 27", 27 in long_ones)
    return 0
