# Needs: sin, radians, fabs

def amplitude_at(degrees_in: float, peak: float) -> float:
    return peak * sin(radians(degrees_in))


def rectified(degrees_in: float, peak: float) -> float:
    return fabs(amplitude_at(degrees_in, peak))


def main() -> int:
    step = 45
    for d in range(0, 361, step):
        angle = float(d)
        print(d, " raw =", round(amplitude_at(angle, 5.0), 6),
              " rectified =", round(rectified(angle, 5.0), 6))
    return 0
