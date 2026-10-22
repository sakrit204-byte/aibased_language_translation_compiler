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
from ..semantic import arrays
from ..semantic import classes
from ..semantic import arrays
from .rules import (ZERO_VALUE, CodegenError, CodegenRules, array_declaration,
                    c_name, type_to_c)
from .runtime import prelude_for

INDENT = "    "


def _param_decls(params):
    """C parameters for a Python parameter list.

    An array becomes two: the pointer C decays it to, and its length, which
    the pointer loses. Every call site passes both. See semantic/arrays.py.
    """
    out = []
    for p in params:
        if arrays.is_array(p.type):
            out.append(f"{type_to_c(p.type)}{c_name(p.name)}")
            out.append(f"py_int {c_name(arrays.length_name(p.name))}")
        else:
            out.append(f"{type_to_c(p.type)} {c_name(p.name)}")
    return out


class _Rendered:
    """An expression already computed into a temporary.

    Standing in for the original node lets a statement be emitted a second
    time with its operands replaced, without the emitter needing a separate
    path for every statement kind.
    """

    def __init__(self, text, inferred_type):
        self.text = text
        self.inferred_type = inferred_type
        self.line = 0


def _is_call(node):
    return isinstance(node, (A.Call, A.MethodCall))


def _child_exprs(node):
    """The sub-expressions of `node`, in Python's evaluation order."""
    if isinstance(node, A.BinOp):
        return [node.left, node.right]
    if isinstance(node, A.Compare):
        return [node.left, node.right]
    if isinstance(node, A.UnaryOp):
        return [node.operand]
    if isinstance(node, A.Call):
        return list(node.args)
    if isinstance(node, A.MethodCall):
        return list(node.args)
    if isinstance(node, (A.Index, A.AttributeIndex)):
        return [node.index]
    return []


def _count_calls(node):
    """Calls anywhere in `node`, not descending into a BoolOp.

    `and` and `or` compile to && and ||, which C evaluates left to right and
    short-circuits exactly as Python does, so a BoolOp is already correct in
    itself and is treated as one unit.
    """
    if isinstance(node, A.BoolOp):
        return 1 if (_count_calls(node.left) or _count_calls(node.right)) else 0
    total = 1 if _is_call(node) else 0
    for child in _child_exprs(node):
        total += _count_calls(child)
    return total


def _walk_assignments(block):
    """Every Assign in a block, including nested ones."""
    for stmt in block:
        yield stmt
        if isinstance(stmt, A.If):
            for _, body in stmt.branches:
                yield from _walk_assignments(body)
        elif isinstance(stmt, (A.While, A.For)):
            yield from _walk_assignments(stmt.body)

# The user's `main` cannot be C's `main`: the entry point has a fixed
# signature (`int main(void)`) while the Python one may return None or a
# 64-bit int. It is emitted under this name and called from a real main().
USER_MAIN = "py_user_main"


class Emitter:
    def __init__(self, symtab, ai_resolver=None, profiles=(), class_info=None):
        self.symtab = symtab
        self.class_info = class_info or {}
        self.profiles = list(profiles)
        self.needs = set()
        self.ai_call_sites = 0
        self._tmp = 0
        self.rules = CodegenRules(self.needs.add, self._count_ai(ai_resolver), self._func_name)
        # Call sites need the callee's parameter types, to know which
        # arguments are arrays and must be followed by their length.
        self.rules.functions = symtab.functions
        self.rules.class_info = self.class_info

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

    def _signature(self, fn: A.FunctionDef, symbol=None):
        if fn.params:
            params = ", ".join(_param_decls(fn.params))
        else:
            params = "void"
        name = symbol if symbol else self._func_name(fn.name)
        return f"{type_to_c(fn.return_type)} {name}({params})"

    # -------------------------------------------------------------- program

    def emit_program(self, program: A.Program) -> str:
        # Bodies first: emitting them is what populates `self.needs`, so the
        # prelude can contain exactly the helpers this program uses.
        method_bodies = []
        method_decls = []
        for node in getattr(program, "classes", ()):
            for method in node.methods:
                symbol = classes.method_symbol(node.name, method.name)
                method_bodies.append(self._emit_function(method, symbol=symbol))
                method_decls.append(self._signature(method, symbol=symbol) + ";")

        bodies = [self._emit_function(fn) for fn in program.functions]

        decls = method_decls + [self._signature(fn) + ";" for fn in program.functions]
        bodies = method_bodies + bodies

        parts = [prelude_for(self.needs)]
        if getattr(program, "classes", None):
            parts.append(self._emit_structs(program))
        if self.profiles:
            parts.append(self._emit_profile_decls())
        if decls:
            parts.append("\n/* ---- forward declarations (any call order, including recursion) ---- */\n")
            parts.append("\n".join(decls) + "\n")
        parts.append("\n/* ---- translated program ---- */\n")
        for body in bodies:
            parts.append("\n" + body + "\n")
        parts.append("\n" + self._emit_entry_point(program))
        return "".join(parts)

    def _emit_structs(self, program: A.Program) -> str:
        """One struct per class, in source order.

        There is no header, no vtable and no refcount — a class here is its
        fields and nothing else, and its methods are free functions that take
        a pointer to one. See semantic/classes.py.
        """
        out = ["\n/* ---- classes: a struct and its methods ---- */\n"]
        for node in getattr(program, "classes", ()):
            info = self.class_info[node.name]
            out.append(f"typedef struct {{\n")
            if not info.fields:
                out.append(f"{INDENT}char _unused;   "
                           f"/* C has no zero-sized struct */\n")
            for field in info.field_order():
                if arrays.is_array(field.type):
                    out.append(f"{INDENT}"
                               f"{array_declaration(field.type, c_name(field.name))};\n")
                else:
                    out.append(f"{INDENT}{type_to_c(field.type)} "
                               f"{c_name(field.name)};\n")
            out.append(f"}} {node.name};\n")
        return "".join(out)

    def _emit_profile_decls(self) -> str:
        """Declare the C shim functions an import brought into scope.

        These are declared inline rather than via #include so the generated
        .c stays self-contained — the same reason the runtime prelude is
        inlined. Only the linker needs to know where the shim lives.
        """
        out = []
        for profile in self.profiles:
            out.append(
                f"\n/* ---- {profile.name}: implemented in "
                f"{', '.join(profile.link_sources)} ---- */\n"
            )
            for name in sorted(profile.functions):
                out.append(profile.functions[name] + "\n")
        return "".join(out)

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

    def _emit_function(self, fn: A.FunctionDef, symbol=None) -> str:
        local_types = self.symtab.function_locals[symbol or fn.name]
        param_names = {p.name for p in fn.params}

        lines = []
        if fn.docstring:
            for doc_line in fn.docstring.strip().splitlines():
                lines.append(f"/* {doc_line.strip()} */")
        lines.append(self._signature(fn, symbol=symbol))
        lines.append("{")

        self.rules.local_types = local_types
        self.rules.param_names = param_names

        declared = self._declaration_order(fn, local_types, param_names)
        for name in declared:
            t = local_types[name]
            if classes.is_instance(t):
                lines.append(f"{INDENT}{classes.class_of(t)} {c_name(name)};")
            elif arrays.is_array(t):
                lines.append(f"{INDENT}{array_declaration(t, c_name(name))} = "
                             f"{self._array_initialiser(fn, name, t)};")
            else:
                lines.append(f"{INDENT}{type_to_c(t)} {c_name(name)} = {ZERO_VALUE[t]};")
        if declared:
            lines.append("")

        body = self._emit_block(fn.body, 1)
        lines.extend(body)
        lines.append("}")
        return "\n".join(lines)

    def _fill_array_field(self, target, node, indent):
        pad = INDENT * indent
        if isinstance(node, A.ArrayRepeat):
            value = self.rules.expr(node.element)
            counter = f"py_i{self._next_tmp()}"
            return [
                f"{pad}for (py_int {counter} = 0; {counter} < {node.count}; "
                f"{counter}++) {{",
                f"{pad}{INDENT}{target}[{counter}] = {value};",
                f"{pad}}}",
            ]
        return [f"{pad}{target}[{i}] = {self.rules.expr(e)};"
                for i, e in enumerate(node.elements)]

    def _array_initialiser(self, fn, name, t):
        """The `= {...}` for an array declaration.

        C initialises the rest of an array to zero when the list is short,
        so `[0] * 54` needs only `{0}` however long it is. Anything else is
        written out in full, which is why the elements have to be literals.
        """
        node = None
        for stmt in _walk_assignments(fn.body):
            if isinstance(stmt, A.Assign) and stmt.name == name:
                node = stmt.value
                break
        if node is None:
            return "{0}"

        if isinstance(node, A.ArrayRepeat):
            value = self.rules.expr(node.element)
            if value in ("0", "0.0"):
                return "{0}"
            return "{" + ", ".join([value] * node.count) + "}"

        if isinstance(node, A.ArrayLit):
            return "{" + ", ".join(self.rules.expr(e) for e in node.elements) + "}"

        return "{0}"

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

    def _sequence(self, nodes, indent):
        """Force Python's left-to-right evaluation order on `nodes`.

        Python evaluates the arguments of a call, and the operands of an
        operator, strictly left to right. C leaves the order unspecified, and
        gcc routinely picks right to left. That difference is invisible while
        every function is pure — which it was, until arrays and objects gave
        a function something it could change. Now `print(drain(g), g.total())`
        means two different things in the two languages unless each part is
        computed into a temporary first, in order.

        Nothing is hoisted when at most one sub-expression can have an effect,
        so ordinary code is emitted exactly as before.
        """
        if sum(_count_calls(n) for n in nodes) < 2:
            return [], [self.rules.expr(n) for n in nodes]

        pad = INDENT * indent
        lines, rendered = [], []
        for node in nodes:
            t = self.rules.type_of(node)
            if arrays.is_array(t) or classes.is_instance(t):
                # A pointer, and nothing can change which object it points
                # at, so its value does not depend on when it is taken.
                rendered.append(self.rules.expr(node))
                continue
            tmp = f"t__{self._next_tmp()}"
            lines.append(f"{pad}{type_to_c(t)} {tmp} = {self.rules.expr(node)};")
            rendered.append(tmp)
        return lines, rendered

    def _emit_block(self, block, indent):
        lines = []
        for stmt in block:
            lines.extend(self._emit_stmt(stmt, indent))
        return lines

    def _emit_stmt(self, node, indent):
        prelude = []
        ordered = self._statement_operands(node)
        if ordered is not None:
            prelude, values = self._sequence(ordered, indent)
            if prelude:
                return prelude + self._emit_stmt_with(node, indent, ordered, values)
        return self._emit_stmt_inner(node, indent)

    @staticmethod
    def _statement_operands(node):
        """The sub-expressions of a statement that Python orders left to
        right, or None when there is nothing to order."""
        if isinstance(node, (A.Assign, A.AugAssign, A.Return, A.ExprStmt)):
            value = getattr(node, "value", None) or getattr(node, "expr", None)
            if value is None:
                return None
            return _child_exprs(value) if len(_child_exprs(value)) > 1 else None
        if isinstance(node, (A.IndexAssign, A.AttributeIndexAssign)):
            return [node.index, node.value]
        if isinstance(node, A.AttributeAssign):
            return None
        return None

    def _emit_stmt_with(self, node, indent, originals, values):
        """Re-emit `node` with its ordered operands already computed."""
        import copy
        replacements = {id(o): v for o, v in zip(originals, values)}
        shadow = copy.copy(node)
        if isinstance(node, (A.IndexAssign, A.AttributeIndexAssign)):
            shadow.index = _Rendered(replacements[id(node.index)],
                                     self.rules.type_of(node.index))
            shadow.value = _Rendered(replacements[id(node.value)],
                                     self.rules.type_of(node.value))
        else:
            value = getattr(node, "value", None) or getattr(node, "expr", None)
            new_value = copy.copy(value)
            children = _child_exprs(value)
            if isinstance(value, (A.BinOp, A.Compare)):
                new_value.left = _Rendered(replacements[id(children[0])],
                                           self.rules.type_of(children[0]))
                new_value.right = _Rendered(replacements[id(children[1])],
                                            self.rules.type_of(children[1]))
            elif isinstance(value, (A.Call, A.MethodCall)):
                new_value.args = [
                    _Rendered(replacements[id(c)], self.rules.type_of(c))
                    for c in children]
            if hasattr(shadow, "value"):
                shadow.value = new_value
            else:
                shadow.expr = new_value
        return self._emit_stmt_inner(shadow, indent)

    def _emit_stmt_inner(self, node, indent):
        pad = INDENT * indent

        if isinstance(node, A.Assign):
            if isinstance(node.value, (A.ArrayLit, A.ArrayRepeat)):
                # The declaration above already carries the initialiser list;
                # C has no way to assign a whole array afterwards.
                return []
            if isinstance(node.value, A.New):
                # The struct is declared above; construction is the __init__
                # call, which takes a pointer to it.
                args = [f"&{c_name(node.name)}"] + [
                    self.rules.expr(a) for a in node.value.args]
                symbol = classes.method_symbol(node.value.class_name, "__init__")
                return [f"{pad}{symbol}({', '.join(args)});"]
            return [f"{pad}{c_name(node.name)} = {self.rules.expr(node.value)};"]

        if isinstance(node, A.AttributeAssign):
            target = self.rules.field(node.owner, node.member)
            if isinstance(node.value, (A.ArrayLit, A.ArrayRepeat)):
                # __init__ filling an array field. Python makes a fresh list
                # here; C fills the struct member in place.
                return self._fill_array_field(target, node.value, indent)
            op = f"{node.op}=" if node.op else "="
            return [f"{pad}{target} {op} {self.rules.expr(node.value)};"]

        if isinstance(node, A.AttributeIndexAssign):
            target = self.rules.field(node.owner, node.member)
            self.needs.add("py_index")
            length = self.rules.field_length(node.class_name, node.member)
            index = f"py_index({self.rules.expr(node.index)}, {length})"
            op = f"{node.op}=" if node.op else "="
            return [f"{pad}{target}[{index}] {op} {self.rules.expr(node.value)};"]

        if isinstance(node, A.MethodCall):
            return [f"{pad}{self.rules.method_call(node)};"]

        if isinstance(node, A.IndexAssign):
            target = f"{c_name(node.name)}[{self.rules.index_expr(node)}]"
            op = f"{node.op}=" if node.op else "="
            return [f"{pad}{target} {op} {self.rules.expr(node.value)};"]

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

        # printf's arguments, like any C call's, are evaluated in whatever
        # order the compiler likes. Python's are strictly left to right.
        prelude, values = self._sequence(node.args, indent)

        # Fast path: ints and strings map straight onto one printf, which keeps
        # the generated C readable for the common case.
        if all(t in ("int", "str") for t in types):
            fmt = " ".join("%lld" if t == "int" else "%s" for t in types) + "\\n"
            # printf is variadic, so an argument that happens to be a 32-bit C
            # `int` (a literal, a comparison, `1 + 1`) would be read as a
            # `long long` and print garbage. Cast every one of them.
            args = ", ".join(
                f"(long long)({value})" if t == "int" else value
                for value, t in zip(values, types)
            )
            return prelude + [f'{pad}printf("{fmt}", {args});']

        # General path: floats need repr() formatting and bools print as words,
        # neither of which printf can do, so one call per argument.
        self.needs.add("py_print")
        lines = list(prelude)
        lines.append(f"{pad}py_print_begin();")
        for value, t in zip(values, types):
            helper = f"py_print_{t}"
            self.needs.add(helper)
            lines.append(f"{pad}{helper}({value});")
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
