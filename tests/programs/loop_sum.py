def sum_to(n: int) -> int:
    total = 0
    for i in range(1, n + 1):
        total = total + i
    return total

def main() -> int:
    print("sum_to(10) =", sum_to(10))
    return 0
