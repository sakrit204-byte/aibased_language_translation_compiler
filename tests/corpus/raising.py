# Exceptions as control flow. The subset has no try, no except and no raise,
# so an error has to travel back as a value the caller checks.


class TooBig(Exception):
    pass


def checked_double(n):
    if n > 50:
        raise TooBig("%d is too big" % n)
    return n * 2


def main() -> int:
    for n in (4, 20, 60, 9):
        try:
            print("doubled", checked_double(n))
        except TooBig:
            print("refused", n)
    print("done")
    return 0
