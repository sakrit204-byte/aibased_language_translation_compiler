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
from ..semantic import arrays
from ..semantic import classes
from ..semantic import dicts

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

# Python's int is unbounded; C's is not. The subset computes in int64, so
# these are the limits of what it can represent at all.
_C_INT64_MIN = -9223372036854775808
_C_INT64_MAX = 9223372036854775807


class CodegenError(Exception):
    pass


def type_to_c(t):
    if classes.is_instance(t):
        return classes.class_of(t) + " *"
    if arrays.is_array(t):
        return C_TYPE[arrays.element(t)] + " *"
    try:
        return C_TYPE[t]
    except KeyError:
        raise CodegenError(f"no C type for {t!r}") from None


def array_declaration(t, name):
    """`py_int a[54]` — the declaration form, where the size is part of the
    type rather than a pointer."""
    return "%s %s[%d]" % (C_TYPE[arrays.element(t)], name, arrays.size(t))


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
    if not _C_INT64_MIN <= value <= _C_INT64_MAX:
        raise CodegenError(
            f"the integer {value} does not fit in the 64 bits this subset "
            f"computes in")
    # Every integer literal carries LL, even a small one. Without it C gives
    # the literal type `int`, and then computes the whole expression in 32
    # bits: `1000000 * 1000000 * 1000000` is 10**18, which fits in int64
    # perfectly well, but wrapped to -1486618624 because each operand was an
    # int. The suffix is the only thing standing between the subset and a
    # silently wrong number.
    return f"{value}LL"


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
        # The local type environment of the function being emitted. Set by
        # the emitter before each one, because an array's length depends on
        # which function it is declared in.
        self.local_types = {}
        # Parameters of the function being emitted. An object parameter is
        # already a pointer in C; an object local is storage, so a call on it
        # needs its address.
        self.param_names = set()
        # name -> the symbol table's entry, for the parameter types.
        self.functions = {}
        # name -> classes.ClassInfo, for field offsets and method symbols.
        self.class_info = {}
        # C symbol -> (type, literal) for class-level dicts.
        self.global_dicts = {}
        # Maps a Python function name to the C symbol the emitter gave it.
        # `main` needs special handling: C's entry point has a fixed signature.
        self.func_name = func_name

    # ------------------------------------------------------------ dispatch

    def expr(self, node) -> str:
        if type(node).__name__ == "_Rendered":
            return node.text
        if isinstance(node, A.Num):
            return c_float_literal(node.value) if node.is_float else c_int_literal(node.value)
        if isinstance(node, A.Str):
            return c_string_literal(node.value)
        if isinstance(node, A.Bool):
            return "1" if node.value else "0"
        if isinstance(node, A.Name):
            if node.member is not None:          # a module constant: math.pi
                return node.member.render([])
            if classes.is_instance(self.local_types.get(node.id, "")):
                return self.receiver(node.id)
            return c_name(node.id)
        if isinstance(node, A.Contains):
            return self.dict_contains(node.dict_name, node.key)
        if isinstance(node, A.Index):
            if getattr(node, "kind", None) == "dict":
                return self.dict_lookup(node.name, node.index)
            return f"{c_name(node.name)}[{self.index_expr(node)}]"
        if isinstance(node, A.Attribute):
            if node.kind == "constant":
                return self.class_constant(node.class_name, node.member)
            return self.field(node.owner, node.member)
        if isinstance(node, A.AttributeIndex):
            if getattr(node, "kind", None) == "dict":
                return self.dict_lookup(node.class_name, node.index)
            target = self.field(node.owner, node.member)
            self.request("py_index")
            length = self.field_length(node.class_name, node.member)
            return f"{target}[py_index({self.expr(node.index)}, {length})]"
        if isinstance(node, A.MethodCall):
            return self.method_call(node)
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

    def type_of_name(self, name):
        try:
            return self.local_types[name]
        except KeyError:
            raise CodegenError(
                f"internal error: '{name}' has no recorded type in this function"
            ) from None

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
        if (node.op == "-" and isinstance(node.operand, A.Num)
                and not node.operand.is_float
                and int(node.operand.value) == -_C_INT64_MIN):
            # int64's lower bound has no positive counterpart, so C cannot
            # write 9223372036854775808LL either. Every C header spells this
            # one constant the same way, one away from the top.
            return "(-9223372036854775807LL - 1)"
        return f"({node.op}{self.expr(node.operand)})"

    def compare(self, node: A.Compare) -> str:
        left, right = self.expr(node.left), self.expr(node.right)
        if self.type_of(node.left) == "str":
            # C's == on `const char*` compares addresses, not contents.
            return f"(strcmp({left}, {right}) {node.op} 0)"
        return f"({left} {node.op} {right})"

    # --------------------------------------------------------------- dicts

    def dict_type_of(self, name):
        """A dict is either a local or a class-level one, which lives at file
        scope because it is shared and never written."""
        if name in self.global_dicts:
            return self.global_dicts[name][0]
        return self.type_of_name(name)

    def dict_lookup(self, name, key_node):
        """`d[k]` — find the key's slot, then read the value beside it."""
        t = self.dict_type_of(name)
        kind = dicts.key_type(t)
        helper = f"py_dict_get_{kind}"
        self.request(helper)
        keys = c_name(dicts.keys_name(name))
        values = c_name(dicts.values_name(name))
        n = dicts.size(t)
        return f"{values}[{helper}({keys}, {n}, {self.expr(key_node)})]"

    def dict_slot(self, name, key_node):
        """The same, as an assignable slot."""
        return self.dict_lookup(name, key_node)

    def dict_contains(self, name, key_node):
        t = self.dict_type_of(name)
        helper = f"py_dict_find_{dicts.key_type(t)}"
        self.request(helper)
        keys = c_name(dicts.keys_name(name))
        n = dicts.size(t)
        return f"({helper}({keys}, {n}, {self.expr(key_node)}) >= 0)"

    # ------------------------------------------------------------- classes

    def receiver(self, owner):
        """How to pass `owner` where a pointer to an object is wanted."""
        if owner in self.param_names:
            return c_name(owner) if owner != "self" else "self"
        return f"&{c_name(owner)}"

    def field(self, owner, member):
        """`self->count` or `c.count`.

        `self` is a pointer because a method must be able to mutate the
        caller's instance; a local is storage, so it uses a dot.
        """
        if owner in self.param_names:
            base = "self" if owner == "self" else c_name(owner)
            return f"{base}->{c_name(member)}"
        return f"{c_name(owner)}.{c_name(member)}"

    def field_length(self, class_name, member):
        info = self.class_info[class_name]
        return str(arrays.size(info.fields[member].type))

    def class_constant(self, class_name, member):
        """A class constant is shared, so it is inlined rather than stored."""
        info = self.class_info[class_name]
        return self.expr(info.constants[member].value)

    def method_call(self, node):
        """`c.turn(f)` -> `Cube__turn(&c, f)`, and `self.turn(f)` ->
        `Cube__turn(self, f)` because self is already a pointer."""
        receiver = self.receiver(node.owner)
        info = self.class_info[node.class_name]
        method = info.methods[node.method]
        args = [self.expr(a) for a in node.args]
        out = [receiver]
        for param, arg_node, rendered in zip(method.params[1:], node.args, args):
            if classes.is_instance(param.type):
                if not isinstance(arg_node, A.Name):
                    raise CodegenError(
                        f"line {node.line}: an object argument must be a plain name"
                    )
                out.append(self.receiver(arg_node.id))
                continue
            out.append(rendered)
            if arrays.is_array(param.type):
                if not isinstance(arg_node, A.Name):
                    raise CodegenError(
                        f"line {node.line}: an array argument must be a plain name"
                    )
                out.append(self.array_length(arg_node.id))
        return "%s(%s)" % (classes.method_symbol(node.class_name, node.method),
                           ", ".join(out))

    # -------------------------------------------------------------- arrays

    def index_expr(self, node):
        """The C index expression for `a[i]`, bounds-checked.

        Python raises IndexError for an out-of-range index and counts a
        negative one from the end. C does neither — it reads whatever is at
        that address. py_index() restores both behaviours, so a program that
        would raise in Python stops in C instead of quietly reading rubbish.
        """
        self.request("py_index")
        return f"py_index({self.expr(node.index)}, {self.array_length(node.name)})"

    def array_length(self, name):
        """The length of an array, as a C expression.

        A local knows its own size at compile time. A parameter does not —
        C arrays decay to pointers — so it carries its length alongside; see
        semantic/arrays.py.
        """
        t = self.type_of_name(name)
        n = arrays.size(t)
        if n is not None:
            return str(n)
        return c_name(arrays.length_name(name))

    # --------------------------------------------------------------- calls

    def call(self, node: A.Call) -> str:
        args = [self.expr(a) for a in node.args]
        types = [self.type_of(a) for a in node.args]

        if node.kind == "user":
            return f"{self.func_name(node.name)}({', '.join(self.user_args(node, args))})"
        if node.kind == "builtin":
            return self.builtin(node, args, types)
        if node.kind == "module":
            # The C expression comes from semantic/modules.py, which owns both
            # the type rule and the translation, so there is nothing to decide
            # here and nothing for the AI gate to be asked.
            return node.member.render(args)
        if node.kind == "ai":
            return self.ai_call(node, args)
        raise CodegenError(
            f"line {node.line}: call to '{node.name}' was never classified by semantic analysis"
        )

    def user_args(self, node: A.Call, args):
        """Arguments for a call to a user function.

        Each array argument is followed by its length, matching the two C
        parameters the callee was given.
        """
        entry = self.functions.get(node.name)
        if entry is None:
            return args
        out = []
        for (_, param_type), arg_node, rendered in zip(entry["params"], node.args, args):
            if classes.is_instance(param_type):
                # An object parameter is a pointer, so the caller passes the
                # address — except `self`, which is already one. Python passes
                # the same object either way, which is the point.
                if not isinstance(arg_node, A.Name):
                    raise CodegenError(
                        f"line {node.line}: an object argument must be a plain name"
                    )
                out.append(self.receiver(arg_node.id))
                continue
            out.append(rendered)
            if arrays.is_array(param_type):
                if not isinstance(arg_node, A.Name):
                    raise CodegenError(
                        f"line {node.line}: an array argument must be a plain name"
                    )
                out.append(self.array_length(arg_node.id))
        return out

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
            target = node.args[0]
            if isinstance(target, A.Attribute) and target.kind == "dict_constant":
                return str(dicts.size(self.global_dicts[target.member][0]))
            if isinstance(target, A.Name):
                t = self.local_types.get(target.id, "")
                if arrays.is_array(t):
                    return self.array_length(target.id)
                if dicts.is_dict(t):
                    return str(dicts.size(t))
            return f"(py_int)strlen({args[0]})"

        if name == "put":
            # No separator, no newline, no allocation — the counterpart to
            # print() that lets a program build a line one piece at a time.
            return f"fputs({args[0]}, stdout)"

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
