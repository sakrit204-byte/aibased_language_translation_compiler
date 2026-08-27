"""Parser unit tests: precedence, new statements, and rejection messages."""

import pytest

from src.lexer.lexer import Lexer
from src.parser import ast_nodes as A
from src.parser.parser import ParseError, Parser


def parse(source):
    return Parser(Lexer(source).tokenize()).parse_program()


def parse_expr(text):
    """Parse `text` as the right-hand side of an assignment."""
    program = parse(f"def f() -> int:\n    x = {text}\n    return x\n")
    return program.functions[0].body[0].value


def shape(node):
    """A compact, comparable rendering of an expression tree."""
    if isinstance(node, A.BinOp):
        return f"({shape(node.left)} {node.op} {shape(node.right)})"
    if isinstance(node, A.UnaryOp):
        return f"({node.op} {shape(node.operand)})"
    if isinstance(node, A.Compare):
        return f"({shape(node.left)} {node.op} {shape(node.right)})"
    if isinstance(node, A.BoolOp):
        return f"({shape(node.left)} {node.op} {shape(node.right)})"
    if isinstance(node, A.Num):
        return node.value
    if isinstance(node, A.Name):
        return node.id
    if isinstance(node, A.Call):
        return f"{node.name}({', '.join(shape(a) for a in node.args)})"
    return type(node).__name__


@pytest.mark.parametrize("text,expected", [
    ("1 + 2 * 3", "(1 + (2 * 3))"),
    ("1 * 2 + 3", "((1 * 2) + 3)"),
    ("1 - 2 - 3", "((1 - 2) - 3)"),
    ("1 + 2 < 4", "((1 + 2) < 4)"),
    ("a and b or c", "((a and b) or c)"),
    ("not a and b", "((not a) and b)"),
    ("2 // 3 % 4", "((2 // 3) % 4)"),
])
def test_precedence(text, expected):
    assert shape(parse_expr(text)) == expected


def test_power_is_right_associative():
    assert shape(parse_expr("2 ** 3 ** 2")) == "(2 ** (3 ** 2))"


def test_power_binds_tighter_than_unary_minus_on_the_left():
    """-2 ** 2 is -(2 ** 2) == -4 in Python, not (-2) ** 2 == 4."""
    assert shape(parse_expr("-2 ** 2")) == "(- (2 ** 2))"


def test_power_accepts_a_unary_exponent():
    assert shape(parse_expr("2 ** -1")) == "(2 ** (- 1))"


def test_repeated_unary_operators():
    assert shape(parse_expr("- -3")) == "(- (- 3))"
    assert shape(parse_expr("not not a")) == "(not (not a))"


def test_augmented_assignment():
    body = parse("def f() -> None:\n    x = 1\n    x += 2\n").functions[0].body
    assert isinstance(body[1], A.AugAssign)
    assert body[1].op == "+"


def test_annotated_assignment():
    body = parse("def f() -> None:\n    total: float = 0\n").functions[0].body
    assert body[0].declared_type == "float"


def test_break_continue_pass():
    body = parse(
        "def f() -> None:\n"
        "    while 1:\n"
        "        if 1:\n"
        "            break\n"
        "        continue\n"
        "    pass\n"
    ).functions[0].body
    loop = body[0]
    assert isinstance(loop.body[0].branches[0][1][0], A.Break)
    assert isinstance(loop.body[1], A.Continue)
    assert isinstance(body[1], A.Pass)


@pytest.mark.parametrize("args,start,stop,step", [
    ("5", "0", "5", None),
    ("1, 5", "1", "5", None),
    ("10, 0, -2", "10", "0", "(- 2)"),
])
def test_range_forms(args, start, stop, step):
    loop = parse(f"def f() -> None:\n    for i in range({args}):\n        pass\n").functions[0].body[0]
    assert shape(loop.start) == start
    assert shape(loop.stop) == stop
    assert (shape(loop.step) if loop.step else None) == step


def test_docstrings_are_metadata_not_statements():
    fn = parse('def f() -> None:\n    """doc"""\n    pass\n').functions[0]
    assert fn.docstring == "doc"
    assert isinstance(fn.body[0], A.Pass)


def test_trailing_comma_in_a_call():
    assert len(parse_expr("g(1, 2,)").args) == 2


@pytest.mark.parametrize("source,message", [
    ("def f() -> int:\n    return 1 < 2 < 3\n", "chained comparisons"),
    ("def f() -> int:\n    a = b = 1\n    return a\n", "chained assignment"),
    ("def f() -> int:\n    return (1, 2)\n", "tuples"),
    ("def f(x) -> int:\n    return x\n", "needs a type annotation"),
    ("def f():\n    return 1\n", "explicit return-type annotation"),
    ("x = 1\n", "only function definitions are allowed at module level"),
    ("def f() -> None:\n    def g() -> None:\n        pass\n", "nested function"),
    ("def f() -> None:\n    for x in y:\n        pass\n", "only iterate over range"),
    ("def f() -> None:\n    x = print(1)\n", "only be used as a statement"),
    ("def f() -> None:\n    1 + 2\n", "no effect"),
    ("def f() -> None:\n    while 1:\n        pass\n    else:\n        pass\n", "while/else"),
    ("def f() -> list:\n    pass\n", "not a type in this subset"),
])
def test_rejections_explain_themselves(source, message):
    with pytest.raises(ParseError, match=message):
        parse(source)


def test_duplicate_parameter():
    with pytest.raises(ParseError, match="duplicate parameter"):
        parse("def f(a: int, a: int) -> int:\n    return a\n")
