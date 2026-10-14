# Needs: hypot, sqrt
# Neither has a static rule, so the compiler must ask the model for a C
# expression and prove it compiles before using it.

def diagonal(width: float, height: float) -> float:
    return hypot(width, height)


def side_from_hypotenuse(h: float, a: float) -> float:
    return sqrt(h * h - a * a)


def main() -> int:
    print("diagonal of 3 x 4 :", round(diagonal(3.0, 4.0), 6))
    print("diagonal of 5 x 12:", round(diagonal(5.0, 12.0), 6))
    print("missing side      :", round(side_from_hypotenuse(13.0, 5.0), 6))
    print("sqrt(2)           :", round(sqrt(2.0), 6))
    return 0
