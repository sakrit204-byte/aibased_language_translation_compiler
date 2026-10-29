def put_digit(d: int) -> None:
    if d == 0:
        put("0")
    elif d == 1:
        put("1")
    elif d == 2:
        put("2")
    elif d == 3:
        put("3")
    elif d == 4:
        put("4")
    elif d == 5:
        put("5")
    elif d == 6:
        put("6")
    elif d == 7:
        put("7")
    elif d == 8:
        put("8")
    else:
        put("9")


def write_big(digits: list[int], used: int) -> None:
    i: int = used - 1
    while i >= 0:
        put_digit(digits[i])
        i = i - 1
    print()


def start_at_one(digits: list[int], size: int) -> int:
    i: int = 0
    while i < size:
        digits[i] = 0
        i = i + 1
    digits[0] = 1
    return 1


def mul_small(digits: list[int], used: int, m: int) -> int:
    carry: int = 0
    i: int = 0
    while i < used:
        product: int = digits[i] * m + carry
        digits[i] = product % 10
        carry = product // 10
        i = i + 1
    while carry > 0:
        digits[used] = carry % 10
        carry = carry // 10
        used = used + 1
    return used


def add_small(digits: list[int], used: int, k: int) -> int:
    carry: int = k
    i: int = 0
    while i < used:
        total: int = digits[i] + carry
        digits[i] = total % 10
        carry = total // 10
        i = i + 1
    while carry > 0:
        digits[used] = carry % 10
        carry = carry // 10
        used = used + 1
    return used


def power_of(digits: list[int], size: int, base: int, exponent: int) -> int:
    used: int = start_at_one(digits, size)
    i: int = 0
    while i < exponent:
        used = mul_small(digits, used, base)
        i = i + 1
    return used


def main() -> int:
    digits: list[int, 64] = [0] * 64
    used: int = 0

    used = power_of(digits, 64, 2, 100)
    write_big(digits, used)

    used = power_of(digits, 64, 10, 25)
    used = add_small(digits, used, 7)
    write_big(digits, used)

    used = power_of(digits, 64, 2, 64)
    write_big(digits, used)

    return 0
