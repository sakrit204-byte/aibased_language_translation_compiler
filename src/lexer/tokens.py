"""Token types for the Python-subset lexer."""

from dataclasses import dataclass

KEYWORDS = {
    "def", "return", "if", "elif", "else", "while", "for", "in", "range",
    "print", "and", "or", "not", "True", "False", "None",
    "int", "float", "str", "bool",
}


@dataclass
class Token:
    kind: str      # e.g. NAME, NUMBER, STRING, OP, KEYWORD, NEWLINE, INDENT, DEDENT, EOF
    value: str
    line: int
    col: int

    def __repr__(self):
        return f"Token({self.kind!r}, {self.value!r}, line={self.line})"
