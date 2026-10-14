# Needs: log, log10, log2, exp

def half_life_remaining(initial: float, elapsed: float, half_life: float) -> float:
    return initial * exp(-log(2.0) * elapsed / half_life)


def main() -> int:
    print("log(e)      :", round(log(2.718281828459045), 6))
    print("log10(1000) :", round(log10(1000.0), 6))
    print("log2(1024)  :", round(log2(1024.0), 6))
    print("exp(1)      :", round(exp(1.0), 6))
    print("decay 100g after 2 half-lives:",
          round(half_life_remaining(100.0, 2.0, 1.0), 6))
    return 0
