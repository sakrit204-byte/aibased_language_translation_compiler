# A dict whose keys are discovered while counting, then walked in sorted
# order. The subset's dicts have a key set fixed at compile time, so the
# rewrite has to decide the keys up front and iterate them deliberately.


def main() -> int:
    words = ["pear", "fig", "pear", "apple", "fig", "pear", "quince"]

    counts = {}
    for word in words:
        counts[word] = counts.get(word, 0) + 1

    for name, count in sorted(counts.items()):
        print(name, count)

    best = max(counts, key=counts.get)
    print("most common:", best, "with", counts[best])
    return 0
