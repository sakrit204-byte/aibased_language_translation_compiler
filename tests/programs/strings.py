def describe(word: str) -> str:
    if word == "":
        return "empty"
    elif word == "hello":
        return "greeting"
    elif word < "m":
        return "early"
    else:
        return "late"


def main() -> int:
    print(describe("hello"))
    print(describe(""))
    print(describe("apple"))
    print(describe("zebra"))

    word = "compiler"
    print(word, "has", len(word), "characters")

    if word:
        print("non-empty strings are truthy")

    quoted = "she said \"hi\"\tand left"
    print(quoted)
    print("backslash:", "a\\b")
    return 0
