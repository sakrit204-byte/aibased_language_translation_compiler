def classify(n: int) -> str:
    """A name first assigned inside a branch is visible after it, as in Python.

    C scopes declarations to the enclosing block, so codegen hoists every local
    to the top of the function instead of declaring it where it first appears.
    """
    if n < 0:
        kind = "negative"
        magnitude = -n
    elif n == 0:
        kind = "zero"
        magnitude = 0
    else:
        kind = "positive"
        magnitude = n

    if magnitude > 100:
        size = "large"
    else:
        size = "small"

    if size == "large":
        return kind
    return kind


def running_total(n: int) -> int:
    """`doubled` lives only inside the loop body in C, but the whole function
    in Python. Hoisting the declaration makes the two agree."""
    acc = 0
    for i in range(n):
        doubled = i * 2
        acc = acc + doubled
    return acc


def main() -> int:
    print(classify(-5), classify(0), classify(250))
    print("running total:", running_total(5))
    return 0
