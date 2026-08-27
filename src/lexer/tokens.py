"""Token types for the Python-subset lexer."""

from dataclasses import dataclass

# Words the lexer classifies as KEYWORD. Note that `range`, `print`, `int`,
# `float`, `str` and `bool` are *not* here: in real Python they are ordinary
# builtin names, and treating them as keywords made `int(x)` unparseable in
# the previous version. They are recognised by the parser/semantic phase as
# builtin identifiers instead.
KEYWORDS = {
    "def", "return", "if", "elif", "else", "while", "for", "in",
    "and", "or", "not", "True", "False", "None",
    "break", "continue", "pass",
}

# Names that are valid as a type annotation.
TYPE_NAMES = {"int", "float", "str", "bool", "None"}


@dataclass
class Token:
    kind: str      # NAME, NUMBER, FLOAT, STRING, KEYWORD, NEWLINE, INDENT, DEDENT, EOF, or an operator kind
    value: str
    line: int
    col: int

    def __repr__(self):
        return f"Token({self.kind!r}, {self.value!r}, line={self.line})"
