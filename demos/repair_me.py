# A program that is ordinary Python and outside the subset in four ways.
#
# It is here to exercise --repair: an f-string, a list comprehension,
# enumerate(), and string concatenation are each a construct the deterministic
# core refuses, and none of them changes what the program prints - so a rewrite
# into the subset has something exact to be checked against.
#
#     python -m src.driver demos/repair_me.py --repair --run


def main() -> int:
    total = 0
    for i in range(1, 11):
        total += i * i
    print(f"sum of squares 1..10 = {total}")

    doubled = [x * 2 for x in range(5)]
    for index, value in enumerate(doubled):
        print("  " + str(index) + " -> " + str(value))

    best = max(doubled)
    print(f"largest is {best}, and their total is {sum(doubled)}")
    return 0
