# Ordinary Python that builds text by joining pieces together.
#
# The subset has no string concatenation, no str() and no formatting, because
# all three need an allocator the generated C does not have. Every line here
# therefore has to come out of put() calls instead.


def row(name, count, unit):
    label = name + ":"
    return label + " " + str(count) + " " + unit


def main() -> int:
    print(row("apples", 12, "crates"))
    print(row("pears", 7, "boxes"))
    print(row("plums", 103, "bags"))
    total = 12 + 7 + 103
    print("total " + str(total) + " items")
    return 0
