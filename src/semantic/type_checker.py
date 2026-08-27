"""
Semantic analysis: scope resolution, type inference, and static checks.

This phase is 100% deterministic — there is no AI anywhere in this file, by
design. A wrong type here becomes a silently wrong value (or a memory bug) in
the generated C that the C compiler cannot catch, so it is exactly the category
of decision the project's AI-usage policy refuses to delegate.

Three analyses run per function:

1. **Local type inference** (`_collect_local_types`) — a small monotone
   dataflow pass. Types only ever widen along the lattice bool < int < float,
   so iterating to a fixpoint terminates. This is what lets

       total = 0
       total = total / n

   infer `total: float` instead of rejecting the program, and it produces the
   type table codegen uses to hoist declarations to the top of the C function.

2. **Definite assignment** — using a variable before any assignment that
   dominates the use is an error here, rather than an UnboundLocalError at
   runtime (or, worse, a garbage read in C).

3. **Return coverage** — a function declared `-> int` must return on every
   path. C would happily fall off the end and return whatever was in the
   return register.

The only point where this phase touches the AI boundary: a call to a name that
is neither user-defined nor a known builtin. With AI-assist off (the default)
that is a hard error. With it on, the node is tagged `kind = "ai"` so codegen
can route it through the gate. See src/ai_assist/gate.py.
"""

from ..parser import ast_nodes as A
from .symbol_table import SymbolTable, SemanticError
from .builtins import (
    BUILTINS, BuiltinError, assignable, check_call, is_numeric, join,
    power_result_type, promote,
)

RESERVED_NAMES = set(BUILTINS) | {"print", "range"}

# Types that have a truth value in this subset.
TRUTHY_TYPES = ("bool", "int", "float", "str")

MAX_INFERENCE_ROUNDS = 16


class _UnknownType(Exception):
    """Internal: a type could not be determined yet during the inference pre-pass."""


class TypeChecker:
    def __init__(self, ai_enabled: bool = False):
        self.symtab = SymbolTable()
        self.ai_enabled = ai_enabled
        self.ai_calls = []          # Call nodes tagged kind="ai", in source order
        self.call_sites = 0         # every call site, for an honest AI-usage rate
        self.env = {}
        self.prepass = False
        self.defined = set()
        self.loop_depth = 0
        self.current_fn = None

    # ---------------------------------------------------------------- driver

    def check(self, program: A.Program):
        for fn in program.functions:
            if fn.name in RESERVED_NAMES:
                raise SemanticError(
                    f"line {fn.line}: '{fn.name}' is a builtin in this subset and cannot be "
                    f"redefined"
                )
            self.symtab.declare_function(fn.name, fn.params, fn.return_type, fn.line)

        for fn in program.functions:
            self._check_function(fn)

        self._check_entry_point(program)
        return self.symtab

    def _check_entry_point(self, program):
        main = self.symtab.functions.get("main")
        if main is None:
            return
        if main["params"]:
            raise SemanticError(
                f"line {main['line']}: main() is the program entry point and must take no "
                f"parameters"
            )
        if main["return_type"] not in ("int", "None"):
            raise SemanticError(
                f"line {main['line']}: main() must be declared -> int or -> None, "
                f"not -> {main['return_type']}"
            )

    # ------------------------------------------------- pass 1: type inference

    def _collect_local_types(self, fn: A.FunctionDef):
        """Fixpoint inference of every local's declared type."""
        env = {p.name: p.type for p in fn.params}
        annotated = {}

        for stmt in _walk_stmts(fn.body):
            if isinstance(stmt, A.Assign) and stmt.declared_type:
                prev = annotated.get(stmt.name)
                if prev and prev != stmt.declared_type:
                    raise SemanticError(
                        f"line {stmt.line}: '{stmt.name}' is annotated {prev} earlier and "
                        f"{stmt.declared_type} here"
                    )
                if stmt.name in env and env[stmt.name] != stmt.declared_type:
                    raise SemanticError(
                        f"line {stmt.line}: '{stmt.name}' is a parameter of type "
                        f"{env[stmt.name]} and cannot be re-annotated as {stmt.declared_type}"
                    )
                annotated[stmt.name] = stmt.declared_type
            elif isinstance(stmt, A.For):
                env.setdefault(stmt.var, "int")
                if annotated.get(stmt.var, "int") != "int":
                    raise SemanticError(
                        f"line {stmt.line}: loop variable '{stmt.var}' is annotated "
                        f"{annotated[stmt.var]} but range() yields int"
                    )
                env[stmt.var] = "int"
        env.update(annotated)

        self.prepass = True
        try:
            for _ in range(MAX_INFERENCE_ROUNDS):
                changed = False
                for stmt in _walk_stmts(fn.body):
                    if isinstance(stmt, A.Assign):
                        name, value = stmt.name, stmt.value
                    elif isinstance(stmt, A.AugAssign):
                        name, value = stmt.name, stmt.value
                    else:
                        continue
                    if name in annotated or name in {p.name for p in fn.params}:
                        continue
                    self.env = env
                    try:
                        t = self._infer(value)
                    except _UnknownType:
                        continue
                    if isinstance(stmt, A.AugAssign):
                        known = env.get(name)
                        if known is None:
                            continue
                        t = self._binop_type(stmt.op, known, t, stmt.value, stmt.line)
                    merged = join(env.get(name), t)
                    if merged is None:
                        raise SemanticError(
                            f"line {stmt.line}: '{name}' is assigned both "
                            f"{env.get(name)} and {t} in the same function; this subset "
                            f"gives each variable a single static type"
                        )
                    if merged != env.get(name):
                        env[name] = merged
                        changed = True
                if not changed:
                    break
        finally:
            self.prepass = False
        return env

    # -------------------------------------------------- pass 2: full checking

    def _check_function(self, fn: A.FunctionDef):
        self.current_fn = fn
        local_types = self._collect_local_types(fn)
        self.symtab.enter_function(fn.name, local_types)
        self.env = local_types
        self.defined = {p.name for p in fn.params}
        self.loop_depth = 0

        self._check_block(fn.body, fn.return_type)

        if fn.return_type != "None" and not _always_returns(fn.body):
            raise SemanticError(
                f"line {fn.line}: function '{fn.name}' is declared -> {fn.return_type} but "
                f"can finish without returning a value"
            )

    def _check_block(self, block, ret_type):
        for stmt in block:
            self._check_stmt(stmt, ret_type)

    def _check_stmt(self, stmt, ret_type):
        if isinstance(stmt, A.Assign):
            t = self._infer(stmt.value)
            if t == "None":
                raise SemanticError(
                    f"line {stmt.line}: cannot assign None to '{stmt.name}' — this subset "
                    f"has no optional values"
                )
            declared = self.symtab.declare_var(stmt.name, t, stmt.line)
            stmt.declared_type = declared
            self.defined.add(stmt.name)

        elif isinstance(stmt, A.AugAssign):
            if stmt.name not in self.defined:
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name}' is used by '{stmt.op}=' before it is "
                    f"assigned"
                )
            current = self.symtab.lookup_var(stmt.name, stmt.line)
            rhs = self._infer(stmt.value)
            result = self._binop_type(stmt.op, current, rhs, stmt.value, stmt.line)
            if not assignable(result, current):
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name} {stmt.op}= ...' produces {result} but "
                    f"'{stmt.name}' is {current}"
                    + (f" — annotate it as `{stmt.name}: float = ...`"
                       if result == "float" and current == "int" else "")
                )
            stmt.declared_type = current
            # Desugar to `name = name <op> value` so codegen has a single path.
            target = A.Name(stmt.name, stmt.line)
            target.inferred_type = current
            expanded = A.BinOp(stmt.op, target, stmt.value, stmt.line)
            expanded.inferred_type = result
            stmt.expanded = expanded

        elif isinstance(stmt, A.If):
            after = None
            has_else = any(cond is None for cond, _ in stmt.branches)
            for cond, body in stmt.branches:
                if cond is not None:
                    self._check_condition(cond)
                saved = set(self.defined)
                self._check_block(body, ret_type)
                branch_defined = set(self.defined)
                self.defined = saved
                if _always_returns(body):
                    continue                     # this path cannot reach the code after
                after = branch_defined if after is None else (after & branch_defined)
            # A name is only definitely assigned afterwards if every reachable
            # branch assigned it — and only when there is an else at all.
            if has_else and after is not None:
                self.defined |= after

        elif isinstance(stmt, A.While):
            self._check_condition(stmt.condition)
            saved = set(self.defined)
            self.loop_depth += 1
            self._check_block(stmt.body, ret_type)
            self.loop_depth -= 1
            self.defined = saved          # the body may run zero times

        elif isinstance(stmt, A.For):
            for bound, what in ((stmt.start, "start"), (stmt.stop, "stop"), (stmt.step, "step")):
                if bound is None:
                    continue
                t = self._infer(bound)
                if t not in ("int", "bool"):
                    raise SemanticError(
                        f"line {stmt.line}: range() {what} must be an int, got {t}"
                    )
            saved = set(self.defined)
            self.defined.add(stmt.var)
            self.loop_depth += 1
            self._check_block(stmt.body, ret_type)
            self.loop_depth -= 1
            self.defined = saved

        elif isinstance(stmt, A.Return):
            if stmt.value is None or isinstance(stmt.value, A.NoneLit):
                if ret_type != "None":
                    raise SemanticError(
                        f"line {stmt.line}: bare `return` in a function declared -> {ret_type}"
                    )
                if stmt.value is not None:
                    stmt.value.inferred_type = "None"
            else:
                t = self._infer(stmt.value)
                if ret_type == "None":
                    raise SemanticError(
                        f"line {stmt.line}: returning {t} from a function declared -> None"
                    )
                if not assignable(t, ret_type):
                    raise SemanticError(
                        f"line {stmt.line}: returning {t}, function is declared -> {ret_type}"
                    )

        elif isinstance(stmt, A.Print):
            for arg in stmt.args:
                t = self._infer(arg)
                if t == "None":
                    raise SemanticError(f"line {stmt.line}: cannot print None in this subset")

        elif isinstance(stmt, A.ExprStmt):
            if isinstance(stmt.expr, A.Str):     # a stray docstring: no effect, no code
                stmt.expr.inferred_type = "str"
            else:
                self._infer(stmt.expr)

        elif isinstance(stmt, A.Break):
            if self.loop_depth == 0:
                raise SemanticError(f"line {stmt.line}: 'break' outside a loop")

        elif isinstance(stmt, A.Continue):
            if self.loop_depth == 0:
                raise SemanticError(f"line {stmt.line}: 'continue' outside a loop")

        elif isinstance(stmt, A.Pass):
            pass

        else:
            raise SemanticError(f"unhandled statement node: {type(stmt).__name__}")

    def _check_condition(self, expr):
        t = self._infer(expr)
        if t not in TRUTHY_TYPES:
            raise SemanticError(
                f"line {getattr(expr, 'line', '?')}: a {t} value has no truth value in this "
                f"subset"
            )
        return t

    # ------------------------------------------------------- type inference

    def _lookup(self, name, line):
        if self.prepass:
            if name not in self.env:
                raise _UnknownType()
            return self.env[name]
        if name not in self.defined:
            if self.symtab.has_var(name):
                raise SemanticError(
                    f"line {line}: '{name}' is used before it is assigned on every path "
                    f"reaching this point"
                )
            raise SemanticError(f"line {line}: undefined variable '{name}'")
        return self.symtab.lookup_var(name, line)

    def _infer(self, expr):
        """Compute and cache the static type of `expr` on the node itself."""
        if isinstance(expr, A.Num):
            t = "float" if expr.is_float else "int"
        elif isinstance(expr, A.Str):
            t = "str"
        elif isinstance(expr, A.Bool):
            t = "bool"
        elif isinstance(expr, A.NoneLit):
            t = "None"
        elif isinstance(expr, A.Name):
            t = self._lookup(expr.id, expr.line)
        elif isinstance(expr, A.BinOp):
            lt = self._infer(expr.left)
            rt = self._infer(expr.right)
            t = self._binop_type(expr.op, lt, rt, expr.right, expr.line)
        elif isinstance(expr, A.UnaryOp):
            t = self._unaryop_type(expr)
        elif isinstance(expr, A.Compare):
            t = self._compare_type(expr)
        elif isinstance(expr, A.BoolOp):
            for side in (expr.left, expr.right):
                st = self._infer(side)
                if st not in TRUTHY_TYPES:
                    raise SemanticError(
                        f"line {expr.line}: a {st} value has no truth value in this subset"
                    )
            t = "bool"
        elif isinstance(expr, A.Call):
            t = self._infer_call(expr)
        else:
            raise SemanticError(f"unhandled expression node: {type(expr).__name__}")
        expr.inferred_type = t
        return t

    def _binop_type(self, op, lt, rt, right_node, line):
        if lt == "str" or rt == "str":
            if op == "+" and lt == "str" and rt == "str":
                raise SemanticError(
                    f"line {line}: string concatenation is not part of this subset — it needs "
                    f"heap allocation, which the generated C deliberately avoids"
                )
            raise SemanticError(f"line {line}: operator '{op}' is not valid on str")
        if not (is_numeric(lt) and is_numeric(rt)):
            raise SemanticError(
                f"line {line}: operator '{op}' is not valid on {lt} and {rt}"
            )
        if op == "/":
            return "float"                        # Python's / is always true division
        if op == "**":
            return power_result_type(lt, rt, right_node)
        return promote(lt, rt)

    def _unaryop_type(self, expr):
        t = self._infer(expr.operand)
        if expr.op == "not":
            if t not in TRUTHY_TYPES:
                raise SemanticError(
                    f"line {expr.line}: a {t} value has no truth value in this subset"
                )
            return "bool"
        if not is_numeric(t):
            raise SemanticError(f"line {expr.line}: unary '{expr.op}' is not valid on {t}")
        return "int" if t == "bool" else t        # -True is -1 in Python

    def _compare_type(self, expr):
        lt = self._infer(expr.left)
        rt = self._infer(expr.right)
        if lt == "str" and rt == "str":
            return "bool"
        if is_numeric(lt) and is_numeric(rt):
            return "bool"
        raise SemanticError(
            f"line {expr.line}: cannot compare {lt} with {rt} using '{expr.op}'"
        )

    def _infer_call(self, call: A.Call):
        arg_types = [self._infer(a) for a in call.args]
        if not self.prepass:
            self.call_sites += 1

        if call.name in self.symtab.functions:
            entry = self.symtab.functions[call.name]
            expected = entry["params"]
            if len(expected) != len(arg_types):
                raise SemanticError(
                    f"line {call.line}: '{call.name}' takes {len(expected)} argument(s), "
                    f"got {len(arg_types)}"
                )
            for i, ((pname, ptype), atype) in enumerate(zip(expected, arg_types), 1):
                if not assignable(atype, ptype):
                    raise SemanticError(
                        f"line {call.line}: argument {i} of '{call.name}' ({pname}) is "
                        f"{ptype}, got {atype}"
                    )
            call.kind = "user"
            return entry["return_type"]

        if call.name in BUILTINS:
            call.kind = "builtin"
            try:
                return check_call(call.name, arg_types, call.args, call.line)
            except BuiltinError as e:
                raise SemanticError(str(e)) from e

        # ---- the one and only AI-eligible case ----
        if not self.ai_enabled:
            raise SemanticError(
                f"line {call.line}: '{call.name}' is not defined in this program and is not "
                f"one of the builtins this compiler translates statically "
                f"({', '.join(sorted(BUILTINS))}). Define it, or enable AI-assist with --ai "
                f"to have the gate propose and validate a C translation."
            )
        for i, t in enumerate(arg_types, 1):
            if not is_numeric(t):
                raise SemanticError(
                    f"line {call.line}: AI-assist only handles numeric calls; argument {i} "
                    f"of '{call.name}' is {t}"
                )
        call.kind = "ai"
        if not self.prepass:
            self.ai_calls.append(call)
        # Unknown callees are assumed to produce a double. That is the widest
        # numeric type, so it can never silently truncate a real result.
        return "float"


# --------------------------------------------------------------- utilities

def _walk_stmts(block):
    """Yield every statement in `block`, including nested ones."""
    for stmt in block:
        yield stmt
        if isinstance(stmt, A.If):
            for _, body in stmt.branches:
                yield from _walk_stmts(body)
        elif isinstance(stmt, (A.While, A.For)):
            yield from _walk_stmts(stmt.body)


def _always_returns(block):
    """True if control cannot fall off the end of `block`."""
    for stmt in block:
        if isinstance(stmt, A.Return):
            return True
        if isinstance(stmt, A.If):
            if not any(cond is None for cond, _ in stmt.branches):
                continue                       # no else: the fall-through path exists
            if all(_always_returns(body) for _, body in stmt.branches):
                return True
        elif isinstance(stmt, A.While):
            # `while True:` with no break never falls through.
            if isinstance(stmt.condition, A.Bool) and stmt.condition.value:
                if not _contains_break(stmt.body):
                    return True
    return False


def _contains_break(block):
    """True if `block` has a break belonging to the enclosing loop."""
    for stmt in block:
        if isinstance(stmt, A.Break):
            return True
        if isinstance(stmt, A.If):
            if any(_contains_break(body) for _, body in stmt.branches):
                return True
        # A break inside a nested While/For binds to that loop, not this one.
    return False
