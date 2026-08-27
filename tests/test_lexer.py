"""Lexer unit tests, focused on the cases that were previously broken."""

import pytest

from src.lexer.lexer import LexError, Lexer


def kinds(source):
    return [t.kind for t in Lexer(source).tokenize()]


def values(source, kind):
    return [t.value for t in Lexer(source).tokenize() if t.kind == kind]


def test_crlf_line_endings_are_accepted():
    """Every file saved by a Windows editor used to fail with
    "unexpected character '\\r'"."""
    unix = kinds("def f() -> int:\n    return 1\n")
    windows = kinds("def f() -> int:\r\n    return 1\r\n")
    assert unix == windows
    assert "INDENT" in windows and "DEDENT" in windows


def test_lone_carriage_returns_are_accepted():
    assert kinds("def f() -> int:\r    return 1\r") == kinds("def f() -> int:\n    return 1\n")


def test_utf8_bom_is_stripped():
    assert kinds("﻿def f() -> int:\n    return 1\n")[0] == "KEYWORD"


def test_tab_indentation_is_measured_like_cpython():
    toks = kinds("def f() -> int:\n\treturn 1\n")
    assert "INDENT" in toks


def test_mixed_tabs_and_spaces_is_an_error():
    source = "def f() -> int:\n\tif 1:\n        \treturn 1\n        return 2\n"
    with pytest.raises(LexError, match="tabs and spaces"):
        Lexer(source).tokenize()


@pytest.mark.parametrize("text,kind", [
    ("//", "FLOORDIV"), ("**", "POW"), ("+=", "PLUS_ASSIGN"), ("-=", "MINUS_ASSIGN"),
    ("*=", "STAR_ASSIGN"), ("/=", "SLASH_ASSIGN"), ("//=", "FLOORDIV_ASSIGN"),
    ("%=", "PERCENT_ASSIGN"), ("**=", "POW_ASSIGN"), ("==", "EQ"), ("!=", "NE"),
    ("->", "ARROW"), ("<=", "LE"), (">=", "GE"),
])
def test_multi_character_operators(text, kind):
    assert kinds(f"a {text} b\n")[1] == kind


def test_longest_match_wins():
    assert kinds("a //= b\n")[1] == "FLOORDIV_ASSIGN"
    assert kinds("a // b\n")[1] == "FLOORDIV"
    assert kinds("a / b\n")[1] == "SLASH"


def test_string_escapes_are_decoded():
    assert values(r'"a\nb"', "STRING") == ["a\nb"]
    assert values(r'"a\\b"', "STRING") == ["a\\b"]
    assert values(r'"say \"hi\""', "STRING") == ['say "hi"']
    assert values(r'"tab\there"', "STRING") == ["tab\there"]
    assert values(r'"\x41"', "STRING") == ["A"]


def test_unknown_escape_keeps_the_backslash_like_python():
    assert values(r'"a\qb"', "STRING") == ["a\\qb"]


def test_triple_quoted_strings():
    assert values('"""line1\nline2"""', "STRING") == ["line1\nline2"]


def test_unterminated_string_is_an_error():
    with pytest.raises(LexError, match="unterminated string"):
        Lexer('x = "oops\n').tokenize()


@pytest.mark.parametrize("text,expected", [
    ("1", "1"), ("1_000", "1000"), ("0xff", "255"), ("0o17", "15"), ("0b1010", "10"),
])
def test_integer_literals(text, expected):
    assert values(f"x = {text}\n", "NUMBER") == [expected]


@pytest.mark.parametrize("text", ["1.5", "1.", ".5", "1e10", "1.5e-3", "2E+4"])
def test_float_literals(text):
    assert values(f"x = {text}\n", "FLOAT") == [text.replace("_", "")]


def test_line_continuation_does_not_start_a_new_logical_line():
    toks = kinds("x = 1 + \\\n    2\n")
    assert toks.count("NEWLINE") == 1
    assert "INDENT" not in toks


def test_newlines_inside_parentheses_are_ignored():
    toks = kinds("f(\n    1,\n    2,\n)\n")
    assert toks.count("NEWLINE") == 1


def test_blank_and_comment_lines_do_not_affect_indentation():
    source = "def f() -> int:\n    x = 1\n\n    # a comment\n    return x\n"
    toks = kinds(source)
    assert toks.count("INDENT") == 1
    assert toks.count("DEDENT") == 1


def test_unsupported_syntax_is_named():
    with pytest.raises(LexError, match="list/subscript"):
        Lexer("x = [1]\n").tokenize()
    with pytest.raises(LexError, match="dict/set"):
        Lexer("x = {1}\n").tokenize()


def test_bang_suggests_not():
    with pytest.raises(LexError, match="did you mean 'not'"):
        Lexer("x = !y\n").tokenize()


def test_unbalanced_parenthesis_is_reported():
    with pytest.raises(LexError, match="unbalanced"):
        Lexer("x = (1 + 2\n").tokenize()
    with pytest.raises(LexError, match="unmatched"):
        Lexer("x = 1)\n").tokenize()


def test_dedent_to_an_unknown_level_is_an_error():
    source = "def f() -> int:\n        x = 1\n    return x\n"
    with pytest.raises(LexError, match="does not match any outer"):
        Lexer(source).tokenize()
