# Fixed-point money printed to two decimals. The subset can print a float,
# but it prints it the way repr does; there is no formatting, so "12.50" has
# to be composed - and the trailing zero is the part that catches a rewrite
# that just divides and prints.


RATE = 0.0825


def line(label, cents):
    dollars = cents / 100.0
    tax = dollars * RATE
    return "%-10s %8.2f %7.2f %9.2f" % (label, dollars, tax, dollars + tax)


def main() -> int:
    items = [("widget", 1250), ("gasket", 399), ("flange", 100000), ("nut", 5)]
    total = 0
    for label, cents in items:
        print(line(label, cents))
        total += cents
    print("%-10s %8.2f" % ("total", total / 100.0))
    print("rounded", round(total / 100.0, 1))
    return 0
