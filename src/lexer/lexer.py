"""
Hand-written lexer for the Python subset.

Deliberately NOT using Python's own `tokenize`/`ast` modules — demonstrating
the lexing phase ourselves is the point of the project.

Correctness notes (these were real bugs, not hypotheticals):
  * CRLF input is normalised up front. Previously a '\\r' fell through to the
    operator table and raised "unexpected character", so *every* file saved by
    a Windows editor failed to compile.
  * Tab indentation is expanded the way CPython's tokenizer does (tabs to the
    next multiple of 8) and inconsistent tab/space mixing raises TabError,
    instead of tabs being silently skipped as ordinary whitespace and
    destroying the block structure.
  * String literals are *decoded* here (escape sequences become real
    characters). Codegen re-encodes them for C. Previously the raw source text
    was carried through, so a literal backslash or an escaped quote produced
    broken C.
  * `//`, `**` and the augmented-assignment operators are tokenised, so the
    parser can support them at all.
"""

from .tokens import Token, KEYWORDS

TAB_SIZE = 8


class LexError(Exception):
    pass


# Longest match first — the scanner tries 3-char, then 2-char, then 1-char.
OPERATORS_3 = {
    "//=": "FLOORDIV_ASSIGN",
    "**=": "POW_ASSIGN",
}

OPERATORS_2 = {
    "**": "POW", "//": "FLOORDIV",
    "==": "EQ", "!=": "NE", "<=": "LE", ">=": "GE", "->": "ARROW",
    "+=": "PLUS_ASSIGN", "-=": "MINUS_ASSIGN",
    "*=": "STAR_ASSIGN", "/=": "SLASH_ASSIGN", "%=": "PERCENT_ASSIGN",
}

OPERATORS_1 = {
    "+": "PLUS", "-": "MINUS", "*": "STAR", "/": "SLASH", "%": "PERCENT",
    "(": "LPAREN", ")": "RPAREN", ":": "COLON", ",": "COMMA", "=": "ASSIGN",
    "<": "LT", ">": "GT",
}

# Characters that are valid Python but outside this subset. Reporting them by
# name is far more useful than "unexpected character '['".
UNSUPPORTED = {
    "[": "list/subscript syntax",
    "]": "list/subscript syntax",
    "{": "dict/set syntax",
    "}": "dict/set syntax",
    ".": "attribute access",
    "@": "decorators",
    ";": "multiple statements on one line",
    "&": "bitwise operators",
    "|": "bitwise operators",
    "^": "bitwise operators",
    "~": "bitwise operators",
}

SIMPLE_ESCAPES = {
    "\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t", "r": "\r",
    "0": "\0", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
}


class Lexer:
    def __init__(self, source: str):
        # Normalise line endings once, here, so nothing downstream ever sees
        # a carriage return. Also strip a UTF-8 BOM if the editor added one.
        source = source.replace("\r\n", "\n").replace("\r", "\n")
        if source.startswith("﻿"):
            source = source[1:]
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1
        # Two parallel indent stacks: one measuring tabs as 8 columns, one
        # measuring them as 1. If they ever disagree about the nesting
        # relationship, the file mixes tabs and spaces ambiguously.
        self.indent_stack = [0]
        self.alt_indent_stack = [0]
        self.tokens: list[Token] = []
        self.paren_depth = 0
        self.line_has_tokens = False
        self.at_line_start = True

    # ---- low-level helpers ----

    def error(self, msg, line=None):
        raise LexError(f"line {line or self.line}: {msg}")

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

    def emit(self, kind, value, line=None, col=None):
        self.tokens.append(Token(kind, value, line or self.line, col or self.col))
        if kind not in ("NEWLINE", "INDENT", "DEDENT"):
            self.line_has_tokens = True

    # ---- main loop ----

    def tokenize(self) -> list[Token]:
        while self.pos < len(self.src):
            if self.at_line_start and self.paren_depth == 0:
                self.at_line_start = False
                self._handle_indentation()
                continue

            ch = self.peek()

            if ch in " \t\f":
                self.advance()
                continue

            if ch == "\\" and self.peek(1) == "\n":
                # Explicit line continuation: the next physical line is part of
                # this logical line, so its leading whitespace is not indentation.
                self.advance()
                self.advance()
                continue

            if ch == "#":
                while self.peek() and self.peek() != "\n":
                    self.advance()
                continue

            if ch == "\n":
                self.advance()
                if self.paren_depth == 0:
                    if self.line_has_tokens:
                        self.emit("NEWLINE", "\\n", self.line - 1)
                    self.line_has_tokens = False
                    self.at_line_start = True
                continue

            if ch.isdigit() or (ch == "." and self.peek(1).isdigit()):
                self._read_number()
                continue

            if ch.isalpha() or ch == "_":
                self._read_name()
                continue

            if ch in ('"', "'"):
                self._read_string()
                continue

            if self._read_operator():
                continue

            if ch in UNSUPPORTED:
                self.error(f"{ch!r} ({UNSUPPORTED[ch]}) is not part of this Python subset")

            self.error(f"unexpected character {ch!r}")

        if self.paren_depth != 0:
            self.error("unbalanced '(' — reached end of file inside a parenthesised expression")

        # Close the final logical line, then unwind every open indent level.
        if self.line_has_tokens:
            self.emit("NEWLINE", "\\n")
            self.line_has_tokens = False
        while len(self.indent_stack) > 1:
            self.indent_stack.pop()
            self.alt_indent_stack.pop()
            self.emit("DEDENT", "")
        self.emit("EOF", "")
        return self.tokens

    # ---- indentation ----

    def _handle_indentation(self):
        """Measure leading whitespace and emit INDENT/DEDENT as needed."""
        indent = 0       # tabs counted as advancing to the next multiple of 8
        alt_indent = 0   # tabs counted as a single column
        while True:
            c = self.peek()
            if c == " ":
                indent += 1
                alt_indent += 1
            elif c == "\t":
                indent = (indent // TAB_SIZE + 1) * TAB_SIZE
                alt_indent += 1
            elif c == "\f":
                indent = 0
                alt_indent = 0
            else:
                break
            self.advance()

        # Blank line, comment-only line, or EOF: never affects the indent stack.
        if self.peek() in ("\n", "#", ""):
            return

        current = self.indent_stack[-1]
        alt_current = self.alt_indent_stack[-1]

        if indent > current:
            if alt_indent <= alt_current:
                self.error("inconsistent use of tabs and spaces in indentation")
            self.indent_stack.append(indent)
            self.alt_indent_stack.append(alt_indent)
            self.emit("INDENT", "")
            return

        if indent == current:
            if alt_indent != alt_current:
                self.error("inconsistent use of tabs and spaces in indentation")
            return

        while indent < self.indent_stack[-1]:
            self.indent_stack.pop()
            self.alt_indent_stack.pop()
            self.emit("DEDENT", "")
        if indent != self.indent_stack[-1]:
            self.error(
                f"unindent does not match any outer indentation level "
                f"(column {indent}, open levels: {self.indent_stack})"
            )
        if alt_indent != self.alt_indent_stack[-1]:
            self.error("inconsistent use of tabs and spaces in indentation")

    # ---- literals ----

    def _read_number(self):
        start_line, start_col = self.line, self.col

        # Non-decimal integer bases.
        if self.peek() == "0" and self.peek(1).lower() in ("x", "o", "b"):
            prefix = self.advance() + self.advance()
            base = {"x": 16, "o": 8, "b": 2}[prefix[1].lower()]
            digits = ""
            while self.peek().isalnum() or self.peek() == "_":
                digits += self.advance()
            clean = digits.replace("_", "")
            try:
                value = int(clean, base)
            except ValueError:
                self.error(f"invalid base-{base} integer literal {prefix + digits!r}", start_line)
            self.emit("NUMBER", str(value), start_line, start_col)
            return

        text = ""
        is_float = False
        while self.peek().isdigit() or self.peek() == "_":
            text += self.advance()
        if self.peek() == "." and (self.peek(1).isdigit() or not self._starts_ident(self.peek(1))):
            is_float = True
            text += self.advance()
            while self.peek().isdigit() or self.peek() == "_":
                text += self.advance()
        if self.peek().lower() == "e" and (
            self.peek(1).isdigit() or (self.peek(1) in "+-" and self.peek(2).isdigit())
        ):
            is_float = True
            text += self.advance()          # e/E
            if self.peek() in "+-":
                text += self.advance()
            while self.peek().isdigit() or self.peek() == "_":
                text += self.advance()

        if self._starts_ident(self.peek()):
            self.error(f"invalid numeric literal near {text + self.peek()!r}", start_line)

        clean = text.replace("_", "")
        try:
            if is_float:
                float(clean)
            else:
                int(clean)
        except ValueError:
            self.error(f"invalid numeric literal {text!r}", start_line)

        self.emit("FLOAT" if is_float else "NUMBER", clean, start_line, start_col)

    @staticmethod
    def _starts_ident(ch):
        return bool(ch) and (ch.isalpha() or ch == "_")

    def _read_name(self):
        start_line, start_col = self.line, self.col
        s = ""
        while self.peek().isalnum() or self.peek() == "_":
            s += self.advance()
        self.emit("KEYWORD" if s in KEYWORDS else "NAME", s, start_line, start_col)

    def _read_string(self):
        """Read a string literal and decode its escape sequences.

        The token's value is the *decoded* text; codegen re-escapes it for C.
        Triple-quoted strings are supported so docstrings don't break parsing.
        """
        start_line, start_col = self.line, self.col
        quote = self.peek()
        if self.peek(1) == quote and self.peek(2) == quote:
            delim = quote * 3
            self.advance(); self.advance(); self.advance()
        else:
            delim = quote
            self.advance()

        out = []
        while True:
            if self.pos >= len(self.src):
                self.error("unterminated string literal", start_line)
            if self.src.startswith(delim, self.pos):
                for _ in delim:
                    self.advance()
                break
            ch = self.peek()
            if ch == "\n" and len(delim) == 1:
                self.error("unterminated string literal (newline inside single-quoted string)",
                           start_line)
            if ch == "\\":
                self.advance()
                out.append(self._read_escape(start_line))
                continue
            out.append(self.advance())

        self.emit("STRING", "".join(out), start_line, start_col)

    def _read_escape(self, start_line):
        e = self.peek()
        if e == "":
            self.error("unterminated escape sequence", start_line)
        if e == "\n":                      # backslash-newline inside a string
            self.advance()
            return ""
        if e in SIMPLE_ESCAPES:
            self.advance()
            return SIMPLE_ESCAPES[e]
        if e == "x":
            self.advance()
            digits = ""
            for _ in range(2):
                if self.peek() in "0123456789abcdefABCDEF":
                    digits += self.advance()
            if len(digits) != 2:
                self.error(r"truncated \x escape (expected two hex digits)", start_line)
            return chr(int(digits, 16))
        # Python keeps the backslash for unrecognised escapes.
        self.advance()
        return "\\" + e

    # ---- operators ----

    def _read_operator(self):
        start_line, start_col = self.line, self.col
        three = self.src[self.pos:self.pos + 3]
        if three in OPERATORS_3:
            for _ in range(3):
                self.advance()
            self.emit(OPERATORS_3[three], three, start_line, start_col)
            return True

        two = self.src[self.pos:self.pos + 2]
        if two in OPERATORS_2:
            self.advance(); self.advance()
            self.emit(OPERATORS_2[two], two, start_line, start_col)
            return True

        one = self.peek()
        if one in OPERATORS_1:
            self.advance()
            if one == "(":
                self.paren_depth += 1
            elif one == ")":
                if self.paren_depth == 0:
                    self.error("unmatched ')'", start_line)
                self.paren_depth -= 1
            self.emit(OPERATORS_1[one], one, start_line, start_col)
            return True

        if one == "!":
            self.error("'!' is not a Python operator — did you mean 'not' or '!='?", start_line)
        return False
