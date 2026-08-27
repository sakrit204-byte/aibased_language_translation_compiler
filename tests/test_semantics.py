"""Semantic-analysis tests: type rules, definite assignment, return coverage."""

import pytest

from src.lexer.lexer import Lexer
from src.parser.parser import Parser
from src.semantic.symbol_table import SemanticError
from src.semantic.type_checker import TypeChecker


def check(source, ai_enabled=False):
    ast = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker(ai_enabled=ai_enabled)
    return checker.check(ast), ast, checker


def type_of_expr(text, prelude="", ret="int"):
    """Static type of `text` used as the return value of a function."""
    source = f"def f() -> {ret}:\n{prelude}    return {text}\n"
    _, ast, _ = check(source)
    return ast.functions[0].body[-1].value.inferred_type


@pytest.mark.parametrize("text,expected", [
    ("1 + 2", "int"),
    ("1 + 2.0", "float"),
    ("1 / 2", "float"),        # Python's / is always true division
    ("1.0 / 2", "float"),
    ("7 // 2", "int"),
    ("7.0 // 2", "float"),
    ("7 % 2", "int"),
    ("2 ** 3", "int"),
    ("2 ** -1", "float"),      # a negative literal exponent yields a float
    ("2.0 ** 3", "float"),
    ("-1", "int"),
    ("1 < 2", "bool"),
    ('"a" == "b"', "bool"),
    ("True and False", "bool"),
    ("not 1", "bool"),
    ("True + True", "int"),
    ("-True", "int"),
])
def test_expression_types(text, expected):
    ret = {"bool": "bool", "float": "float", "int": "int"}[expected]
    assert type_of_expr(text, ret=ret) == expected


def test_true_division_of_ints_is_a_float_not_an_int():
    with pytest.raises(SemanticError, match="returning float"):
        check("def f() -> int:\n    return 1 / 2\n")


def test_int_widens_to_float_on_return():
    check("def f() -> float:\n    return 1\n")


def test_variable_type_is_inferred_across_assignments():
    """`total = 0` then `total = total / n` must infer float, not fail."""
    symtab, _, _ = check(
        "def f(n: int) -> float:\n"
        "    total = 0\n"
        "    total = total / n\n"
        "    return total\n"
    )
    assert symtab.function_locals["f"]["total"] == "float"


def test_annotation_pins_a_variable_type():
    symtab, _, _ = check("def f() -> float:\n    total: float = 0\n    return total\n")
    assert symtab.function_locals["f"]["total"] == "float"


def test_incompatible_reassignment_is_rejected():
    with pytest.raises(SemanticError, match="single static type"):
        check("def f() -> int:\n    x = 1\n    x = \"a\"\n    return x\n")


def test_loop_variable_is_an_int():
    symtab, _, _ = check(
        "def f() -> int:\n    for i in range(3):\n        pass\n    return 0\n"
    )
    assert symtab.function_locals["f"]["i"] == "int"


def test_range_bounds_must_be_ints():
    with pytest.raises(SemanticError, match="range\\(\\) stop must be an int"):
        check("def f() -> int:\n    for i in range(2.5):\n        pass\n    return 0\n")


def test_variable_assigned_in_every_branch_is_defined_afterwards():
    check(
        "def f(n: int) -> int:\n"
        "    if n > 0:\n        x = 1\n"
        "    else:\n        x = 2\n"
        "    return x\n"
    )


def test_variable_assigned_in_only_one_branch_is_rejected():
    with pytest.raises(SemanticError, match="before it is assigned"):
        check(
            "def f(n: int) -> int:\n"
            "    if n > 0:\n        x = 1\n"
            "    return x\n"
        )


def test_use_before_assignment_is_rejected():
    with pytest.raises(SemanticError, match="undefined variable|before it is assigned"):
        check("def f() -> int:\n    return y\n")


def test_missing_return_on_some_path_is_rejected():
    with pytest.raises(SemanticError, match="can finish without returning"):
        check("def f(n: int) -> int:\n    if n > 0:\n        return 1\n")


def test_if_else_that_always_returns_is_accepted():
    check("def f(n: int) -> int:\n    if n > 0:\n        return 1\n    else:\n        return 2\n")


def test_infinite_loop_counts_as_returning():
    check("def f() -> int:\n    while True:\n        return 1\n")


def test_argument_count_is_checked():
    with pytest.raises(SemanticError, match="takes 1 argument"):
        check("def g(a: int) -> int:\n    return a\n\ndef f() -> int:\n    return g(1, 2)\n")


def test_argument_type_is_checked():
    with pytest.raises(SemanticError, match="argument 1 of 'g'"):
        check('def g(a: int) -> int:\n    return a\n\ndef f() -> int:\n    return g("x")\n')


def test_argument_widening_is_allowed():
    check("def g(a: float) -> float:\n    return a\n\ndef f() -> float:\n    return g(1)\n")


def test_break_outside_a_loop():
    with pytest.raises(SemanticError, match="'break' outside a loop"):
        check("def f() -> None:\n    break\n")


def test_string_arithmetic_is_rejected_with_an_explanation():
    with pytest.raises(SemanticError, match="heap allocation"):
        check('def f() -> str:\n    return "a" + "b"\n')


def test_comparing_a_string_with_a_number_is_rejected():
    with pytest.raises(SemanticError, match="cannot compare"):
        check('def f() -> bool:\n    return "a" < 1\n')


def test_min_max_require_matching_types():
    with pytest.raises(SemanticError, match="all be int or all be float"):
        check("def f() -> float:\n    return max(1.5, 2)\n")


def test_redefining_a_builtin_is_rejected():
    with pytest.raises(SemanticError, match="cannot be redefined"):
        check("def abs(x: int) -> int:\n    return x\n")


def test_main_must_take_no_parameters():
    with pytest.raises(SemanticError, match="must take no parameters"):
        check("def main(x: int) -> int:\n    return 0\n")


def test_duplicate_function_definition():
    with pytest.raises(SemanticError, match="already defined"):
        check("def f() -> int:\n    return 1\n\ndef f() -> int:\n    return 2\n")


def test_unknown_call_is_an_error_when_ai_is_off():
    with pytest.raises(SemanticError, match="not one of the builtins"):
        check("def f() -> float:\n    return sqrt(2.0)\n")


def test_unknown_call_is_flagged_for_ai_when_enabled():
    _, _, checker = check("def f() -> float:\n    return sqrt(2.0)\n", ai_enabled=True)
    assert [c.name for c in checker.ai_calls] == ["sqrt"]
    assert checker.ai_calls[0].kind == "ai"


def test_ai_is_never_used_for_a_known_builtin():
    _, _, checker = check("def f() -> int:\n    return abs(-1)\n", ai_enabled=True)
    assert checker.ai_calls == []


def test_ai_only_handles_numeric_arguments():
    with pytest.raises(SemanticError, match="only handles numeric calls"):
        check('def f() -> float:\n    return mystery("text")\n', ai_enabled=True)
