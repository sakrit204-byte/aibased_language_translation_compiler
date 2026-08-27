def in_range(value: int, low: int, high: int) -> bool:
    return value >= low and value <= high


def main() -> int:
    print(True, False)
    print(not True, not False)
    print(1 < 2, 2 < 1, 1 == 1.0)

    print(in_range(5, 1, 10), in_range(50, 1, 10))
    print(True and False, True or False)

    flag = in_range(3, 1, 10)
    if flag:
        print("3 is in range")
    if not in_range(0, 1, 10):
        print("0 is not in range")

    print("mixed:", -True, True + True)
    return 0
