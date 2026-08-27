"""
Hand-written lexer for the Python subset.

Deliberately NOT using Python's own tokenize/ast modules — the whole point
of this project is to demonstrate the lexing phase ourselves.
"""

from .tokens import Token, KEYWORDS


class LexError(Exception):
    pass


SINGLE_OPS = {
    "+": "PLUS", "-": "MINUS", "*": "STAR", "/": "SLASH", "%": "PERCENT",
    "(": "LPAREN", ")": "RPAREN", ":": "COLON", ",": "COMMA", "=": "ASSIGN",
    "<": "LT", ">": "GT",
}

# Longest-match-first multi-char operators
MULTI_OPS = {
    "==": "EQ", "!=": "NE", "<=": "LE", ">=": "GE", "->": "ARROW",
}


class Lexer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.indent_stack = [0]
        self.tokens: list[Token] = []
        self.at_line_start = True
        self.paren_depth = 0

    def error(self, msg):
        raise LexError(f"Lex error at line {self.line}: {msg}")

    def peek(self, offset=0):
        i = self.pos + offset
        return self.src[i] if i < len(self.src) else ""

    def advance(self):
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def tokenize(self) -> list[Token]:
        while self.pos < len(self.src):
            if self.at_line_start and self.paren_depth == 0:
                self._handle_indentation()
                if self.pos >= len(self.src):
                    break
            ch = self.peek()

            if ch in " \t":
                self.advance()
                continue
            if ch == "#":
                while self.peek() and self.peek() != "\n":
                    self.advance()
                continue
            if ch == "\n":
                self.advance()
                if self.paren_depth == 0 and self.tokens and self.tokens[-1].kind != "NEWLINE":
                    self.tokens.append(Token("NEWLINE", "\\n", self.line - 1, self.col))
                self.at_line_start = True
                continue
            if ch.isdigit():
                self._read_number()
                continue
            if ch.isalpha() or ch == "_":
                self._read_name()
                continue
            if ch in ('"', "'"):
                self._read_string(ch)
                continue

            two = self.peek() + self.peek(1)
            if two in MULTI_OPS:
                self.advance(); self.advance()
                self.tokens.append(Token(MULTI_OPS[two], two, self.line, self.col))
                continue
            if ch in SINGLE_OPS:
                start_line, start_col = self.line, self.col
                self.advance()
                if ch == "(":
                    self.paren_depth += 1
                elif ch == ")":
                    self.paren_depth = max(0, self.paren_depth - 1)
                self.tokens.append(Token(SINGLE_OPS[ch], ch, start_line, start_col))
                continue

            self.error(f"unexpected character {ch!r}")

        # final NEWLINE + DEDENTs to close everything out
        if self.tokens and self.tokens[-1].kind != "NEWLINE":
            self.tokens.append(Token("NEWLINE", "\\n", self.line, self.col))
        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self.tokens.append(Token("DEDENT", "", self.line, self.col))
        self.tokens.append(Token("EOF", "", self.line, self.col))
        return self.tokens

    def _handle_indentation(self):
        start = self.pos
        indent = 0
        while self.peek() == " ":
            indent += 1
            self.advance()
        if self.peek() in ("\n", "#", ""):
            # blank or comment-only line: no indent tracking
            self.at_line_start = False
            return
        self.at_line_start = False
        current = self.indent_stack[-1]
        if indent > current:
            self.indent_stack.append(indent)
            self.tokens.append(Token("INDENT", "", self.line, self.col))
        else:
            while indent < self.indent_stack[-1]:
                self.indent_stack.pop()
                self.tokens.append(Token("DEDENT", "", self.line, self.col))
            if indent != self.indent_stack[-1]:
                self.error("inconsistent indentation")

    def _read_number(self):
        start_line, start_col = self.line, self.col
        s = ""
        is_float = False
        while self.peek().isdigit():
            s += self.advance()
        if self.peek() == "." and self.peek(1).isdigit():
            is_float = True
            s += self.advance()
            while self.peek().isdigit():
                s += self.advance()
        kind = "FLOAT" if is_float else "NUMBER"
        self.tokens.append(Token(kind, s, start_line, start_col))

    def _read_name(self):
        start_line, start_col = self.line, self.col
        s = ""
        while self.peek().isalnum() or self.peek() == "_":
            s += self.advance()
        kind = "KEYWORD" if s in KEYWORDS else "NAME"
        self.tokens.append(Token(kind, s, start_line, start_col))

    def _read_string(self, quote):
        start_line, start_col = self.line, self.col
        self.advance()  # opening quote
        s = ""
        while self.peek() != quote:
            if self.peek() == "":
                self.error("unterminated string literal")
            s += self.advance()
        self.advance()  # closing quote
        self.tokens.append(Token("STRING", s, start_line, start_col))
