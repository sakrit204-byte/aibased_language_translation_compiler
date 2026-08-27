def factorial(n: int) -> int:
    if n <= 1:
        return 1
    else:
        return n * factorial(n - 1)

def main() -> int:
    result = factorial(6)
    print("factorial(6) =", result)
    return 0
