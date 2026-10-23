# Counter, defaultdict, sets, and a sort on a composite key. The ordering
# rule is the hard part: ties break on the name, so the rewrite has to
# reproduce the comparison, not just the counts.
import collections


def main() -> int:
    reading = "the quick brown fox jumps over the lazy dog the end".split()

    counts = collections.Counter(reading)
    for word, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(count, word)

    lengths = collections.defaultdict(list)
    for word in set(reading):
        lengths[len(word)].append(word)

    for size in sorted(lengths):
        print(size, sorted(lengths[size]))

    print("distinct", len(set(reading)))
    return 0
