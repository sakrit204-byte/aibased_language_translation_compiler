"""Codegen tests that do not need a C compiler: what the emitted C says."""

import re

import pytest

from src.codegen.rules import c_name, c_string_literal
from src.codegen.runtime import HELPERS, prelude_for
from src.driver import compile_source
from src.semantic.builtins import BUILTINS


def emit(body, signature="def f() -> None:"):
    source = signature + "\n" + "\n".join("    " + line for line in body.splitlines()) + "\n"
    c_code, _ = compile_source(source)
    return c_code


def test_true_division_uses_the_runtime_helper_not_c_division():
    c = emit("x = 7 / 2\n")
    assert "py_div_int(7, 2)" in c
    assert "7 / 2" not in c


def test_floor_division_and_modulo_use_helpers():
    c = emit("a = -7 // 2\nb = -7 % 2\n")
    assert "py_floordiv_int" in c
    assert "py_mod_int" in c


def test_string_equality_uses_strcmp_not_pointer_comparison():
    c = emit('flag = "a" == "b"\n')
    assert 'strcmp("a", "b") == 0' in c


def test_string_truthiness_is_emptiness_not_a_null_check():
    c = emit('s = "x"\nif s:\n    pass\n')
    assert "py_truthy_str(s)" in c


def test_declarations_are_hoisted_out_of_branches():
    """The C declaration must sit at function scope, not inside the if block."""
    c = emit("if 1:\n    x = 1\nelse:\n    x = 2\nprint(x)\n")
    body = c.split("py_user_main" if "py_user_main" in c else "void f(void)")[-1]
    decl = re.search(r"py_int x = 0;", body)
    assert decl, "x was not hoisted"
    assert decl.start() < body.index("if ("), "declaration must precede the branch"


def test_range_bounds_are_evaluated_once():
    c = emit("for i in range(g(3)):\n    pass\n",
             signature="def g(n: int) -> int:\n    return n\n\ndef f() -> None:")
    assert "py_int py_stop_1 = g(3);" in c
    assert "i < py_stop_1" in c
    assert c.count("g(3)") == 1


def test_range_with_a_step_guards_against_zero():
    c = emit("for i in range(10, 0, -2):\n    pass\n")
    assert "py_step_1" in c
    assert "range() arg 3 must not be zero" in c


def test_augmented_assignment_is_desugared():
    c = emit("x = 1\nx += 2\n")
    assert "x = (x + 2);" in c


def test_int_arguments_to_printf_are_cast_to_long_long():
    """A bare C `int` read back as `%lld` prints garbage."""
    c = emit("print(1 + 1)\n")
    assert '(long long)((1 + 1))' in c


def test_floats_and_bools_use_the_print_helpers():
    c = emit("print(1.5)\nprint(True)\n")
    assert "py_print_float" in c
    assert "py_print_bool" in c
    assert "%f" not in c


def test_entry_point_wraps_the_user_main():
    c, _ = compile_source("def main() -> int:\n    return 0\n")
    assert "py_int py_user_main(void)" in c
    assert "int main(void)" in c
    assert "return (int)py_user_main();" in c


def test_a_program_without_main_still_links():
    c, _ = compile_source("def helper() -> int:\n    return 1\n")
    assert "int main(void)" in c


def test_generated_code_is_self_contained():
    c, _ = compile_source("def main() -> int:\n    return 0\n")
    assert "#include" in c
    assert 'pyrt.h' not in c, "the runtime is inlined; no external header should be needed"


def test_only_the_helpers_actually_used_are_emitted():
    plain, _ = compile_source("def main() -> int:\n    return 0\n")
    with_mod, _ = compile_source("def main() -> int:\n    return 7 % 2\n")
    assert "py_mod_int" not in plain
    assert "py_mod_int" in with_mod
    assert len(with_mod) > len(plain)


def test_c_keywords_in_python_identifiers_are_mangled():
    c = emit("switch = 1\ndouble = 2\nprint(switch, double)\n")
    assert "switch__c" in c and "double__c" in c
    assert re.search(r"\bpy_int switch = ", c) is None


@pytest.mark.parametrize("text,expected", [
    ("a", '"a"'),
    ('say "hi"', r'"say \"hi\""'),
    ("back\\slash", r'"back\\slash"'),
    ("tab\there", r'"tab\there"'),
    ("new\nline", r'"new\nline"'),
])
def test_string_literals_are_re_escaped_for_c(text, expected):
    assert c_string_literal(text) == expected


def test_c_name_leaves_ordinary_identifiers_alone():
    assert c_name("total") == "total"
    assert c_name("value_2") == "value_2"


def test_every_builtin_with_a_type_rule_has_a_c_template():
    """The two halves of a builtin live in different files; keep them in sync."""
    for name in BUILTINS:
        source = _sample_call(name)
        c_code, stats = compile_source(source)
        assert stats["ai_calls_used"] == 0, f"{name} must not need AI"
        assert c_code, name


def _sample_call(name):
    calls = {
        "len": 'len("abc")',
        "abs": "abs(-1)",
        "min": "min(1, 2)",
        "max": "max(1, 2)",
        "pow": "pow(2, 3)",
        "round": "round(1.5)",
        "int": "int(1.5)",
        "float": "float(1)",
        "bool": "bool(1)",
    }
    ret = "bool" if name == "bool" else ("float" if name == "float" else "int")
    return f"def f() -> {ret}:\n    return {calls[name]}\n"


@pytest.mark.parametrize("name", sorted(HELPERS))
def test_requesting_a_helper_pulls_in_its_dependencies_first(name):
    text = prelude_for({name})
    own = HELPERS[name][1]
    assert own in text
    for dep in HELPERS[name][0]:
        dep_code = HELPERS[dep][1]
        assert dep_code in text, f"{name} is missing its dependency {dep}"
        assert text.index(dep_code) < text.index(own), f"{dep} must precede {name}"


def test_prelude_emits_dependencies_before_dependents():
    text = prelude_for({"py_print_float"})
    assert text.index("py_repr_float(char") < text.index("void py_print_float")
    assert text.index("py__print_sep(void)") < text.index("void py_print_float")
