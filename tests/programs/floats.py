def average(total: float, count: int) -> float:
    return total / count


def main() -> int:
    print(0.1)
    print(1.0)
    print(-0.0)
    print(1.0 / 3.0)
    print(2.0 / 3.0)
    print(1e15)
    print(1e16)
    print(1e-4)
    print(1e-5)
    print(1.5e300)
    print(average(10.0, 4))
    print(average(1.0, 3))

    x: float = 0.0
    i = 0
    while i < 10:
        x = x + 0.1
        i = i + 1
    print("accumulated:", x)
    return 0
