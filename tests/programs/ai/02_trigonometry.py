# Needs: sin, cos, tan, radians
# `radians` has no C counterpart at all - the model has to derive
# `a0 * M_PI / 180.0` rather than rename a function.

def sine_of_degrees(deg: float) -> float:
    return sin(radians(deg))


def cosine_of_degrees(deg: float) -> float:
    return cos(radians(deg))


def main() -> int:
    for d in range(0, 91, 15):
        angle = float(d)
        print(d, "deg  sin =", round(sine_of_degrees(angle), 6),
              " cos =", round(cosine_of_degrees(angle), 6))
    print("tan(45 deg) =", round(tan(radians(45.0)), 6))
    return 0
