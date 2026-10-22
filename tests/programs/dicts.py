# Dicts with a fixed key set, diffed against CPython.
#
# The case that matters most is iteration order: Python has iterated dicts in
# insertion order since 3.7, and the keys here are an array in the order they
# were written, so `for k in d` has to yield the same sequence in both. If it
# ever stopped doing so, this file's output would change.


def main() -> int:
    colours: dict[str, str] = {
        "W": "#FFFFFF",
        "Y": "#FFD500",
        "R": "#E53935",
        "O": "#FF8C00",
        "G": "#22A447",
        "B": "#2878D0",
    }

    # insertion order, not sorted order, and not hash order
    for face in colours:
        put(face)
    print("")

    for face in colours:
        print(face, colours[face])

    print(len(colours))
    print(colours["W"], colours["B"])

    # membership
    print("W" in colours, "Z" in colours, "b" in colours)

    # values are mutable, keys are not
    counts: dict[str, int] = {"a": 0, "b": 10, "c": 20}
    counts["a"] = 5
    counts["b"] += 7
    counts["c"] -= 1
    for key in counts:
        print(key, counts[key])
    print(len(counts))

    # int keys
    names: dict[int, str] = {0: "zero", 7: "seven", 42: "forty-two"}
    print(names[0], names[7], names[42])
    print(7 in names, 8 in names)
    for n in names:
        put(names[n])
        put(" ")
    print("")

    # float values
    rates: dict[str, float] = {"low": 0.25, "high": 2.5}
    rates["low"] = 0.75
    print(rates["low"] + rates["high"])

    # bool values
    flags: dict[str, bool] = {"on": True, "off": False}
    print(flags["on"], flags["off"])

    # a key computed at run time
    i: int = 0
    total: int = 0
    while i < len(counts):
        total = total + 1
        i = i + 1
    print(total)

    # a dict read inside a loop over another
    lookup: dict[str, int] = {"W": 0, "Y": 1, "G": 2, "B": 3, "R": 4, "O": 5}
    for face in colours:
        put(colours[face])
        put(" ")
        print(lookup[face])
    return 0
