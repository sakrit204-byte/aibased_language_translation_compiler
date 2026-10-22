# Classes as value types, diffed against CPython.
#
# A class here is a C struct and some functions that take a pointer to one.
# The cases that matter are where that could quietly differ from Python:
# whether a method sees the caller's instance, whether two instances share
# anything, and whether a class constant behaves like a shared attribute.


class Counter:
    """A counter with a settable step."""

    START: int = 100

    total: int
    step: int

    def __init__(self, step: int) -> None:
        self.total = 0
        self.step = step

    def bump(self) -> None:
        self.total = self.total + self.step

    def add(self, n: int) -> None:
        self.total += n * self.step

    def value(self) -> int:
        return self.total

    def from_start(self) -> int:
        return self.START + self.total


class Grid:
    """Holds an array field, filled per instance in __init__."""

    SIZE: int = 6

    cells: list[int, 6]
    writes: int

    def __init__(self) -> None:
        self.cells = [0] * 6
        self.writes = 0

    def put(self, i: int, v: int) -> None:
        self.cells[i] = v
        self.writes += 1

    def get(self, i: int) -> int:
        return self.cells[i]

    def total(self) -> int:
        s: int = 0
        i: int = 0
        while i < self.SIZE:
            s = s + self.cells[i]
            i = i + 1
        return s

    def bump_all(self) -> None:
        i: int = 0
        while i < self.SIZE:
            self.cells[i] += 1
            i = i + 1


class Account:
    """Private state, reachable only through methods."""

    __balance: int
    name: str

    def __init__(self, name: str) -> None:
        self.__balance = 0
        self.name = name

    def deposit(self, amount: int) -> bool:
        if amount <= 0:
            return False
        self.__balance = self.__balance + amount
        return True

    def balance(self) -> int:
        return self.__balance


def drain(g: Grid) -> int:
    """A free function taking an object: it gets the caller's instance, not
    a copy, which is what Python does too."""
    taken: int = g.total()
    i: int = 0
    while i < 6:
        g.put(i, 0)
        i = i + 1
    return taken


def main() -> int:
    a: Counter = Counter(1)
    b: Counter = Counter(10)

    a.bump()
    a.bump()
    b.bump()
    print(a.value(), b.value())

    # two instances must not share anything
    a.add(3)
    print(a.value(), b.value())

    # a class constant reads the same through either
    print(a.from_start(), b.from_start(), Counter.START)

    # fields are readable from outside when they are not private
    print(a.total, a.step, b.step)

    g: Grid = Grid()
    g.put(0, 5)
    g.put(3, 7)
    print(g.get(0), g.get(3), g.total(), g.writes)

    g.bump_all()
    print(g.total(), g.get(0), g.get(1))

    # mutation through a function reaches the caller's object
    print(drain(g), g.total())

    h: Grid = Grid()
    g.put(2, 9)
    print(g.get(2), h.get(2))        # separate storage

    acc: Account = Account("savings")
    print(acc.deposit(50), acc.deposit(-1), acc.balance(), acc.name)
    return 0
