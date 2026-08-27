"""
Walks the type-checked AST and emits C, using the deterministic templates in
rules.py and the runtime helpers in runtime.py.

Two structural decisions that fix real bugs in the previous version:

* **Declarations are hoisted** to the top of each C function. Python scopes
  names to the whole function, C scopes them to the enclosing block, so a
  variable first assigned inside an `if` used to be declared inside that block
  and was either invisible afterwards or silently a different variable.

* **`range()` bounds are evaluated once**, into temporaries, before the loop.
  Emitting `for (i = 0; i < f(n); i++)` re-evaluates `f(n)` on every iteration,
  which Python never does.

The only non-deterministic input is `ai_resolver`, used exclusively for Call
nodes the semantic phase tagged `kind == "ai"`.
"""

from ..parser import ast_nodes as A
from .rules import ZERO_VALUE, CodegenError, CodegenRules, c_name, type_to_c
from .runtime import prelude_for

INDENT = "    "

# The user's `main` cannot be C's `main`: the entry point has a fixed
# signature (`int main(void)`) while the Python one may return None or a
# 64-bit int. It is emitted under this name and called from a real main().
USER_MAIN = "py_user_main"


class Emitter:
    def __init__(self, symtab, ai_resolver=None):
        self.symtab = symtab
        self.needs = set()
        self.ai_call_sites = 0
        self._tmp = 0
        self.rules = CodegenRules(self.needs.add, self._count_ai(ai_resolver), self._func_name)

    def _count_ai(self, ai_resolver):
        if ai_resolver is None:
            return None

        def resolve(call_node):
            self.ai_call_sites += 1
            return ai_resolver(call_node)

        return resolve

    # ------------------------------------------------------------- helpers

    def _func_name(self, name):
        return USER_MAIN if name == "main" else c_name(name)

    def _next_tmp(self):
        self._tmp += 1
        return self._tmp

    def _signature(self, fn: A.FunctionDef):
        if fn.params:
            params = ", ".join(f"{type_to_c(p.type)} {c_name(p.name)}" for p in fn.params)
        else:
            params = "void"
        return f"{type_to_c(fn.return_type)} {self._func_name(fn.name)}({params})"

    # -------------------------------------------------------------- program

    def emit_program(self, program: A.Program) -> str:
        # Bodies first: emitting them is what populates `self.needs`, so the
        # prelude can contain exactly the helpers this program uses.
        bodies = [self._emit_function(fn) for fn in program.functions]

        decls = [self._signature(fn) + ";" for fn in program.functions]

        parts = [prelude_for(self.needs)]
        if decls:
            parts.append("\n/* ---- forward declarations (any call order, including recursion) ---- */\n")
            parts.append("\n".join(decls) + "\n")
        parts.append("\n/* ---- translated program ---- */\n")
        for body in bodies:
            parts.append("\n" + body + "\n")
        parts.append("\n" + self._emit_entry_point(program))
        return "".join(parts)

    def _emit_entry_point(self, program: A.Program) -> str:
        main = next((fn for fn in program.functions if fn.name == "main"), None)
        if main is None:
            return (
                "/* This program defines no main(), so the entry point does nothing. */\n"
                "int main(void)\n{\n    return 0;\n}\n"
            )
        if main.return_type == "None":
            call = f"{INDENT}{USER_MAIN}();\n{INDENT}return 0;"
        else:
            call = f"{INDENT}return (int){USER_MAIN}();"
        return (
            "/* C's entry point. Python's main() is emitted above under another\n"
            " * name because int main(void) has a fixed signature. */\n"
            f"int main(void)\n{{\n{call}\n}}\n"
        )

    # ------------------------------------------------------------- function

    def _emit_function(self, fn: A.FunctionDef) -> str:
        local_types = self.symtab.function_locals[fn.name]
        param_names = {p.name for p in fn.params}

        lines = []
        if fn.docstring:
            for doc_line in fn.docstring.strip().splitlines():
                lines.append(f"/* {doc_line.strip()} */")
        lines.append(self._signature(fn))
        lines.append("{")

        declared = self._declaration_order(fn, local_types, param_names)
        for name in declared:
            t = local_types[name]
            lines.append(f"{INDENT}{type_to_c(t)} {c_name(name)} = {ZERO_VALUE[t]};")
        if declared:
            lines.append("")

        body = self._emit_block(fn.body, 1)
        lines.extend(body)
        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _declaration_order(fn, local_types, param_names):
        """Locals in the order they first appear in the source, which keeps the
        generated C readable and stable across runs."""
        order = {}
        for stmt in _walk_stmts(fn.body):
            if isinstance(stmt, (A.Assign, A.AugAssign)):
                if stmt.name not in param_names:
                    order.setdefault(stmt.name, None)
            elif isinstance(stmt, A.For):
                if stmt.var not in param_names:
                    order.setdefault(stmt.var, None)
        for name in local_types:                       # anything the walk missed
            if name not in param_names:
                order.setdefault(name, None)
        return [n for n in order if n in local_types]

    # ------------------------------------------------------------ statements

    def _emit_block(self, block, indent):
        lines = []
        for stmt in block:
            lines.extend(self._emit_stmt(stmt, indent))
        return lines

    def _emit_stmt(self, node, indent):
        pad = INDENT * indent

        if isinstance(node, A.Assign):
            return [f"{pad}{c_name(node.name)} = {self.rules.expr(node.value)};"]

        if isinstance(node, A.AugAssign):
            if node.expanded is None:
                raise CodegenError(
                    f"line {node.line}: augmented assignment was not desugared by "
                    f"semantic analysis"
                )
            return [f"{pad}{c_name(node.name)} = {self.rules.expr(node.expanded)};"]

        if isinstance(node, A.If):
            return self._emit_if(node, indent)

        if isinstance(node, A.While):
            lines = [f"{pad}while ({_unwrap(self.rules.truth(node.condition))}) {{"]
            lines.extend(self._emit_block(node.body, indent + 1))
            lines.append(f"{pad}}}")
            return lines

        if isinstance(node, A.For):
            return self._emit_for(node, indent)

        if isinstance(node, A.Return):
            if node.value is None or isinstance(node.value, A.NoneLit):
                return [f"{pad}return;"]
            return [f"{pad}return {self.rules.expr(node.value)};"]

        if isinstance(node, A.Print):
            return self._emit_print(node, indent)

        if isinstance(node, A.ExprStmt):
            if isinstance(node.expr, A.Str):
                return []                       # a stray docstring emits nothing
            return [f"{pad}{self.rules.expr(node.expr)};"]

        if isinstance(node, A.Break):
            return [f"{pad}break;"]

        if isinstance(node, A.Continue):
            return [f"{pad}continue;"]

        if isinstance(node, A.Pass):
            return [f"{pad}/* pass */"]

        raise CodegenError(f"no emitter case for {type(node).__name__}")

    def _emit_if(self, node: A.If, indent):
        pad = INDENT * indent
        lines = []
        for i, (cond, body) in enumerate(node.branches):
            if cond is None:
                lines.append(f"{pad}else {{")
            elif i == 0:
                lines.append(f"{pad}if ({_unwrap(self.rules.truth(cond))}) {{")
            else:
                lines.append(f"{pad}else if ({_unwrap(self.rules.truth(cond))}) {{")
            lines.extend(self._emit_block(body, indent + 1))
            lines.append(f"{pad}}}")
        return lines

    def _emit_for(self, node: A.For, indent):
        """`for i in range(start, stop, step)` with the bounds evaluated once."""
        pad = INDENT * indent
        inner = INDENT * (indent + 1)
        var = c_name(node.var)
        n = self._next_tmp()

        start = self.rules.expr(node.start)
        stop = self.rules.expr(node.stop)

        lines = [f"{pad}{{"]
        lines.append(f"{inner}py_int py_stop_{n} = {stop};")

        if node.step is None:
            head = f"for ({var} = {start}; {var} < py_stop_{n}; {var}++)"
        else:
            self.needs.add("py_fatal")
            step = self.rules.expr(node.step)
            lines.append(f"{inner}py_int py_step_{n} = {step};")
            lines.append(
                f"{inner}if (py_step_{n} == 0) "
                f'py_fatal("ValueError: range() arg 3 must not be zero");'
            )
            head = (f"for ({var} = {start}; py_step_{n} > 0 ? {var} < py_stop_{n} "
                    f": {var} > py_stop_{n}; {var} += py_step_{n})")

        lines.append(f"{inner}{head} {{")
        lines.extend(self._emit_block(node.body, indent + 2))
        lines.append(f"{inner}}}")
        lines.append(f"{pad}}}")
        return lines

    def _emit_print(self, node: A.Print, indent):
        pad = INDENT * indent
        if not node.args:
            return [f'{pad}printf("\\n");']

        types = [self.rules.type_of(a) for a in node.args]

        # Fast path: ints and strings map straight onto one printf, which keeps
        # the generated C readable for the common case.
        if all(t in ("int", "str") for t in types):
            fmt = " ".join("%lld" if t == "int" else "%s" for t in types) + "\\n"
            # printf is variadic, so an argument that happens to be a 32-bit C
            # `int` (a literal, a comparison, `1 + 1`) would be read as a
            # `long long` and print garbage. Cast every one of them.
            args = ", ".join(
                f"(long long)({self.rules.expr(a)})" if t == "int" else self.rules.expr(a)
                for a, t in zip(node.args, types)
            )
            return [f'{pad}printf("{fmt}", {args});']

        # General path: floats need repr() formatting and bools print as words,
        # neither of which printf can do, so one call per argument.
        self.needs.add("py_print")
        lines = [f"{pad}py_print_begin();"]
        for arg, t in zip(node.args, types):
            helper = f"py_print_{t}"
            self.needs.add(helper)
            lines.append(f"{pad}{helper}({self.rules.expr(arg)});")
        lines.append(f"{pad}py_print_end();")
        return lines


def _unwrap(code: str) -> str:
    """Drop one redundant layer of parentheses.

    Expression rules parenthesise defensively; `if`/`while` supply their own
    parentheses, so without this every condition comes out as `if ((a < b))`.
    String literals are skipped so `(strcmp(s, "(") == 0)` is not miscounted.
    """
    if not (code.startswith("(") and code.endswith(")")):
        return code
    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(code):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(code) - 1:
                return code           # the leading '(' closes early: not a wrapper
    return code[1:-1] if depth == 0 else code


def _walk_stmts(block):
    for stmt in block:
        yield stmt
        if isinstance(stmt, A.If):
            for _, body in stmt.branches:
                yield from _walk_stmts(body)
        elif isinstance(stmt, (A.While, A.For)):
            yield from _walk_stmts(stmt.body)
