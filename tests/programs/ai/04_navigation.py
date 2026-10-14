# Needs: atan2, degrees, hypot
# atan2 picks the correct quadrant, which a naive atan(y/x) cannot.

def bearing(dx: float, dy: float) -> float:
    return degrees(atan2(dy, dx))


def distance(dx: float, dy: float) -> float:
    return hypot(dx, dy)


def report(dx: float, dy: float) -> None:
    print("dx =", dx, " dy =", dy,
          " bearing =", round(bearing(dx, dy), 4),
          " distance =", round(distance(dx, dy), 4))


def main() -> int:
    report(1.0, 0.0)
    report(0.0, 1.0)
    report(-1.0, 0.0)
    report(0.0, -1.0)
    report(3.0, 4.0)
    report(-3.0, -4.0)
    return 0
