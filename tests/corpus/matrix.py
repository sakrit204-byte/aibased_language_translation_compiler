# Nested lists, zip-based transpose, a matrix product and a sort by a derived
# key. Every list here is built by a comprehension over another one.


def transpose(m):
    return [list(col) for col in zip(*m)]


def multiply(a, b):
    bt = transpose(b)
    return [[sum(x * y for x, y in zip(row, col)) for col in bt] for row in a]


def main() -> int:
    a = [[1, 2, 3], [4, 5, 6]]
    b = [[7, 8], [9, 10], [11, 12]]

    product = multiply(a, b)
    for row in product:
        print(row)

    print("transposed", transpose(product))
    print("row sums", [sum(row) for row in product])

    ranked = sorted(product, key=lambda row: -sum(row))
    print("ranked", ranked)
    return 0
