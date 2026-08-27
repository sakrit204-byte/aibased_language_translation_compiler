"""
Deterministic AST -> C translation rules.

Every construct in grammar/subset.ebnf and every builtin in
semantic/builtins.py has a rule here. Adding a language feature means adding a
rule in this file — you do *not* reach for the AI-assist module. The only node
that can escape to AI is a Call the semantic phase tagged `kind == "ai"`,
meaning its callee is neither user-defined nor a known builtin.
"""

import re

from ..parser import ast_nodes as A

C_TYPE = {
    "int": "py_int",
    "float": "py_float",
    "str": "py_str",
    "bool": "py_bool",
    "None": "void",
}

ZERO_VALUE = {
    "int": "0",
    "float": "0.0",
    "str": '""',
    "bool": "0",
}

# Identifiers that are legal in Python but reserved (or ours) in C.
C_RESERVED = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline",
    "int", "long", "register", "restrict", "return", "short", "signed",
    "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned",
    "void", "volatile", "while", "_Bool", "_Complex", "_Imaginary",
    "bool", "true", "false", "NULL", "main",
    "printf", "putchar", "fputs", "strlen", "strcmp", "malloc", "free",
    "abs", "pow", "round", "floor", "ceil", "fabs", "fmod", "sqrt", "exit",
}

INT_LIKE = ("int", "bool")

_C_INT32_MAX = 2147483647


class CodegenError(Exception):
    pass


def type_to_c(t):
    try:
        return C_TYPE[t]
    except KeyError:
        raise CodegenError(f"no C type for {t!r}") from None


def c_name(name: str) -> str:
    """Map a Python identifier onto a C one that cannot collide with a C
    keyword or with this compiler's runtime helpers."""
    if name in C_RESERVED or name.startswith("py_") or name.startswith("__"):
        return name + "__c"
    return name


def c_string_literal(s: str) -> str:
    """Encode a decoded Python string as a C string literal."""
    out = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\a":
            out.append("\\a")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\v":
            out.append("\\v")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\{ord(ch):03o}")
        elif ord(ch) > 0x7F:
            # Emit non-ASCII as raw UTF-8 bytes so the literal stays portable.
            for byte in ch.encode("utf-8"):
                out.append(f"\\{byte:03o}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def c_int_literal(text: str) -> str:
    value = int(text)
    return f"{value}LL" if abs(value) > _C_INT32_MAX else str(value)


def c_float_literal(text: str) -> str:
    value = float(text)
    literal = repr(value)
    if literal in ("inf", "-inf", "nan"):
        raise CodegenError(f"cannot emit a C literal for {literal}")
    if "." not in literal and "e" not in literal and "E" not in literal:
        literal += ".0"
    return literal


class CodegenRules:
    """Expression templates. `request` records which runtime helpers the
    generated file needs, so the prelude only contains what is used."""

    def __init__(self, request, ai_resolver=None, func_name=c_name):
        self.request = request
        self.ai_resolver = ai_resolver
        # Maps a Python function name to the C symbol the emitter gave it.
        # `main` needs special handling: C's entry point has a fixed signature.
        self.func_name = func_name

    # ------------------------------------------------------------ dispatch

    def expr(self, node) -> str:
        if isinstance(node, A.Num):
            return c_float_literal(node.value) if node.is_float else c_int_literal(node.value)
        if isinstance(node, A.Str):
            return c_string_literal(node.value)
        if isinstance(node, A.Bool):
            return "1" if node.value else "0"
        if isinstance(node, A.Name):
            return c_name(node.id)
        if isinstance(node, A.BinOp):
            return self.binop(node)
        if isinstance(node, A.UnaryOp):
            return self.unaryop(node)
        if isinstance(node, A.Compare):
            return self.compare(node)
        if isinstance(node, A.BoolOp):
            op = "&&" if node.op == "and" else "||"
            return f"({self.truth(node.left)} {op} {self.truth(node.right)})"
        if isinstance(node, A.Call):
            return self.call(node)
        if isinstance(node, A.NoneLit):
            raise CodegenError(f"line {node.line}: None has no value in this subset")
        raise CodegenError(f"no codegen rule for expression node {type(node).__name__}")

    def type_of(self, node) -> str:
        t = getattr(node, "inferred_type", None)
        if t is None:
            raise CodegenError(
                f"internal error: {type(node).__name__} on line "
                f"{getattr(node, 'line', '?')} has no inferred type — semantic analysis "
                f"did not visit it"
            )
        return t

    # ---------------------------------------------------------- truthiness

    def truth(self, node) -> str:
        """Emit `node` in a boolean context, applying Python's truthiness."""
        t = self.type_of(node)
        code = self.expr(node)
        if t in INT_LIKE:
            return code
        if t == "float":
            return f"({code} != 0.0)"
        if t == "str":
            self.request("py_truthy_str")
            return f"py_truthy_str({code})"
        raise CodegenError(f"line {getattr(node, 'line', '?')}: {t} has no truth value")

    # -------------------------------------------------------------- binops

    def binop(self, node: A.BinOp) -> str:
        left, right = self.expr(node.left), self.expr(node.right)
        lt, rt = self.type_of(node.left), self.type_of(node.right)
        both_int = lt in INT_LIKE and rt in INT_LIKE
        op = node.op

        if op in ("+", "-", "*"):
            # C's usual arithmetic conversions already match Python's promotion
            # rules for these three; only overflow behaviour differs.
            return f"({left} {op} {right})"

        if op == "/":
            helper = "py_div_int" if both_int else "py_div_float"
            self.request(helper)
            return f"{helper}({left}, {right})"

        if op == "//":
            helper = "py_floordiv_int" if both_int else "py_floordiv_float"
            self.request(helper)
            return f"{helper}({left}, {right})"

        if op == "%":
            helper = "py_mod_int" if both_int else "py_mod_float"
            self.request(helper)
            return f"{helper}({left}, {right})"

        if op == "**":
            if self.type_of(node) == "int":
                self.request("py_pow_int")
                return f"py_pow_int({left}, {right})"
            self.request("py_pow_float")
            return f"py_pow_float((py_float)({left}), (py_float)({right}))"

        raise CodegenError(f"line {node.line}: no rule for binary operator {op!r}")

    def unaryop(self, node: A.UnaryOp) -> str:
        if node.op == "not":
            return f"(!{self.truth(node.operand)})"
        return f"({node.op}{self.expr(node.operand)})"

    def compare(self, node: A.Compare) -> str:
        left, right = self.expr(node.left), self.expr(node.right)
        if self.type_of(node.left) == "str":
            # C's == on `const char*` compares addresses, not contents.
            return f"(strcmp({left}, {right}) {node.op} 0)"
        return f"({left} {node.op} {right})"

    # --------------------------------------------------------------- calls

    def call(self, node: A.Call) -> str:
        args = [self.expr(a) for a in node.args]
        types = [self.type_of(a) for a in node.args]

        if node.kind == "user":
            return f"{self.func_name(node.name)}({', '.join(args)})"
        if node.kind == "builtin":
            return self.builtin(node, args, types)
        if node.kind == "ai":
            return self.ai_call(node, args)
        raise CodegenError(
            f"line {node.line}: call to '{node.name}' was never classified by semantic analysis"
        )

    def ai_call(self, node: A.Call, args) -> str:
        if node.ai_expr is None:
            if self.ai_resolver is None:
                raise CodegenError(
                    f"line {node.line}: '{node.name}' has no static rule and AI-assist is "
                    f"disabled. Define the function, or re-run with --ai."
                )
            node.ai_expr = self.ai_resolver(node)
        return substitute_placeholders(node.ai_expr, args)

    def builtin(self, node: A.Call, args, types) -> str:
        name = node.name
        result = self.type_of(node)

        if name == "len":
            return f"(py_int)strlen({args[0]})"

        if name == "abs":
            if types[0] == "float":
                return f"fabs({args[0]})"
            self.request("py_abs_int")
            return f"py_abs_int({args[0]})"

        if name in ("min", "max"):
            helper = f"py_{name}_{'float' if result == 'float' else 'int'}"
            self.request(helper)
            cast = "(py_float)" if result == "float" else ""
            folded = f"{cast}({args[0]})"
            for a in args[1:]:
                folded = f"{helper}({folded}, {cast}({a}))"
            return folded

        if name == "pow":
            if result == "int":
                self.request("py_pow_int")
                return f"py_pow_int({args[0]}, {args[1]})"
            self.request("py_pow_float")
            return f"py_pow_float((py_float)({args[0]}), (py_float)({args[1]}))"

        if name == "round":
            return self._round(node, args, types, result)

        if name == "int":
            if types[0] == "float":
                self.request("py_int_from_float")
                return f"py_int_from_float({args[0]})"
            if types[0] == "str":
                self.request("py_int_from_str")
                return f"py_int_from_str({args[0]})"
            return f"(py_int)({args[0]})"

        if name == "float":
            if types[0] == "str":
                self.request("py_float_from_str")
                return f"py_float_from_str({args[0]})"
            return f"(py_float)({args[0]})"

        if name == "bool":
            return self.truth(node.args[0])

        raise CodegenError(
            f"line {node.line}: builtin '{name}' has a type rule but no C template — "
            f"add one in codegen/rules.py"
        )

    def _round(self, node, args, types, result):
        if len(args) == 1:
            if types[0] in INT_LIKE:
                return f"(py_int)({args[0]})"        # round(int) is the int itself
            self.request("py_round_int")
            return f"py_round_int({args[0]})"
        if types[0] in INT_LIKE:
            self.request("py_round_int_ndigits")
            return f"py_round_int_ndigits({args[0]}, {args[1]})"
        self.request("py_round_float")
        return f"py_round_float({args[0]}, {args[1]})"


_PLACEHOLDER = re.compile(r"\ba(\d+)\b")


def substitute_placeholders(template: str, args) -> str:
    """Replace the `a0`, `a1`, ... placeholders in a validated AI template with
    the real argument expressions."""
    def repl(m):
        idx = int(m.group(1))
        if idx >= len(args):
            raise CodegenError(
                f"AI translation references placeholder a{idx} but the call has "
                f"{len(args)} argument(s)"
            )
        return f"({args[idx]})"

    return f"({_PLACEHOLDER.sub(repl, template)})"
