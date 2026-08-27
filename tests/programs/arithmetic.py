def show_division(a: int, b: int) -> None:
    """Python's / is true division; // floors; % takes the divisor's sign."""
    print(a, "/", b, "=", a / b)
    print(a, "//", b, "=", a // b)
    print(a, "%", b, "=", a % b)


def main() -> int:
    show_division(7, 2)
    show_division(-7, 2)
    show_division(7, -2)
    show_division(-7, -2)

    print("2 ** 10 =", 2 ** 10)
    print("2 ** 0 =", 2 ** 0)
    print("-2 ** 2 =", -2 ** 2)
    print("2 ** -1 =", 2 ** -1)
    print("2.0 ** 0.5 =", 2.0 ** 0.5)
    print("2 ** 3 ** 2 =", 2 ** 3 ** 2)

    print("7.5 // 2 =", 7.5 // 2)
    print("-7.5 % 2 =", -7.5 % 2)
    print("mixed:", 3 + 0.5, 3 * 2.0, 10 / 4)
    return 0
