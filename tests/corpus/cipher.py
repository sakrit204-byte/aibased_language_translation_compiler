# Character arithmetic. The subset has no ord(), no chr(), no string indexing
# and no way to build a string, so there is no direct route at all: the
# rewrite has to carry the alphabet itself and decide each letter by hand.


ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def shift(word, by):
    out = []
    for ch in word:
        index = ALPHABET.index(ch)
        out.append(ALPHABET[(index + by) % 26])
    return "".join(out)


def main() -> int:
    words = ["attack", "at", "dawn"]
    for by in (3, 13):
        encoded = [shift(w, by) for w in words]
        print(by, " ".join(encoded))

    roundtrip = shift(shift("hello", 7), 19)
    print("roundtrip", roundtrip)
    print("longest", max(words, key=len))
    return 0
