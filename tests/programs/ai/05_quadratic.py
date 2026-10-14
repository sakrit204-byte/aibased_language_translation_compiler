# Needs: sqrt, fabs

def discriminant(a: float, b: float, c: float) -> float:
    return b * b - 4.0 * a * c


def root_one(a: float, b: float, c: float) -> float:
    return (-b + sqrt(discriminant(a, b, c))) / (2.0 * a)


def root_two(a: float, b: float, c: float) -> float:
    return (-b - sqrt(discriminant(a, b, c))) / (2.0 * a)


def solve(a: float, b: float, c: float) -> None:
    d = discriminant(a, b, c)
    if d < 0.0:
        print("no real roots, discriminant =", round(d, 6))
    else:
        print("roots:", round(root_one(a, b, c), 6), "and",
              round(root_two(a, b, c), 6))


def main() -> int:
    solve(1.0, -3.0, 2.0)
    solve(1.0, 2.0, 1.0)
    solve(1.0, 0.0, 1.0)
    print("fabs(-7.25) =", round(fabs(-7.25), 6))
    return 0
