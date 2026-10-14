# Needs: sinh, cosh, tanh, cosh for the catenary curve

def catenary_height(x: float, a: float) -> float:
    return a * cosh(x / a)


def main() -> int:
    print("sinh(1) =", round(sinh(1.0), 6))
    print("cosh(1) =", round(cosh(1.0), 6))
    print("tanh(1) =", round(tanh(1.0), 6))
    print("cable sag profile, a = 10:")
    for x in range(-20, 21, 10):
        position = float(x)
        print("  x =", x, " height =", round(catenary_height(position, 10.0), 6))
    return 0
