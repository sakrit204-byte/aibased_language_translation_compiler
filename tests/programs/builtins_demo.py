def clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)


def main() -> int:
    print("abs:", abs(-7), abs(7), abs(-2.5))
    print("min/max:", min(3, 9), max(3, 9), min(1, 2, 3), max(1.5, 2.0, 0.0))
    print("clamp:", clamp(15, 0, 10), clamp(-4, 0, 10), clamp(5, 0, 10))

    print("pow:", pow(2, 8), pow(2.0, 0.5))

    print("round:", round(2.5), round(3.5), round(-2.5), round(0.5))
    print("round n:", round(3.14159, 2), round(2.675, 2), round(1234, -2), round(1350, -2))

    print("int:", int(3.9), int(-3.9), int("42"), int(True))
    print("float:", float(3), float("2.5"))
    print("bool:", bool(0), bool(3), bool(0.0), bool(""), bool("x"))

    print("len:", len("hello"), len(""))
    return 0
