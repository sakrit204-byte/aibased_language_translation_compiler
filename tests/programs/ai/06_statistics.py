# Needs: sqrt, erf
# erf is the Gaussian error function - there is no way to express the normal
# CDF in the subset without it.

def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def z_score(value: float, mean: float, sd: float) -> float:
    return (value - mean) / sd


def main() -> int:
    print("P(Z < -2) =", round(normal_cdf(-2.0), 6))
    print("P(Z < -1) =", round(normal_cdf(-1.0), 6))
    print("P(Z <  0) =", round(normal_cdf(0.0), 6))
    print("P(Z <  1) =", round(normal_cdf(1.0), 6))
    print("P(Z <  2) =", round(normal_cdf(2.0), 6))
    print("z of 130 in N(100, 15):", round(z_score(130.0, 100.0, 15.0), 6))
    return 0
