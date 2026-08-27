def classify(n: int) -> str:
    if n % 15 == 0:
        return "fizzbuzz"
    elif n % 3 == 0:
        return "fizz"
    elif n % 5 == 0:
        return "buzz"
    else:
        return "num"

def main() -> int:
    i = 1
    while i <= 15:
        print(i, classify(i))
        i = i + 1
    return 0
