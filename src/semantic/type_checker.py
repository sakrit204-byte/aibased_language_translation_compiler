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
from . import arrays
from . import modules
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
        # Filled by _check_imports. `module_aliases` serves `math.sqrt`,
        # `imported` serves the plain `sqrt` a from-import binds, and
        # `profiles` is the set of C shims this program must be linked
        # against, which the driver reads back off the checker.
        self.module_aliases = {}        # alias -> modules.Module
        self.imported = {}              # local name -> (Module, Member)
        self.profiles = []              # profiles.Profile, in import order
        self.ai_calls = []          # Call nodes tagged kind="ai", in source order
        self.call_sites = 0         # every call site, for an honest AI-usage rate
        self.env = {}
        self.prepass = False
        self.defined = set()
        self.loop_depth = 0
        self.current_fn = None

    # ---------------------------------------------------------------- driver

    def check(self, program: A.Program):
        self._check_imports(program)

        for fn in program.functions:
            if arrays.is_array(fn.return_type):
                raise SemanticError(
                    f"line {fn.line}: '{fn.name}' is declared -> "
                    f"{arrays.describe(fn.return_type)}, but a function cannot return "
                    f"an array in this subset. C cannot return one either: the storage "
                    f"is the function's own stack frame, which is gone by the time the "
                    f"caller looks.\n  Pass the array in as a parameter and fill it "
                    f"instead — it is shared, not copied."
                )

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

    def _check_imports(self, program):
        """Bind the names each import brings into scope.

        Nothing is executed and no file is read: an import here resolves
        against the table in semantic/modules.py, and fails with the reason
        when a module cannot be expressed in this subset at all.
        """
        for node in getattr(program, "imports", ()):
            try:
                module = modules.resolve(node.module, node.line)
            except modules.ModuleError as e:
                raise SemanticError(str(e)) from e

            if module.profile is not None and module.profile not in self.profiles:
                self.profiles.append(module.profile)

            # `import math` / `import math as m`
            if node.members is None and not node.star:
                alias = node.alias or node.module
                self.module_aliases[alias] = module
                continue

            # `from math import *`
            #
            # A member whose name a builtin already owns is skipped rather
            # than rejected: `math.pow` and the builtin `pow` mean nearly the
            # same thing, and refusing the whole star import over that would
            # be unhelpful. The builtin keeps the name, and `math.pow` is
            # still reachable through the module. Asking for the collision
            # *by name* — `from math import pow` — is still an error, because
            # that request cannot be honoured as written.
            if node.star:
                for name in module.names():
                    if name in RESERVED_NAMES:
                        continue
                    self._bind_imported(name, module, module.get(name), node.line)
                continue

            # `from math import sqrt, cos as c`
            for member_name, local in node.members:
                member = module.get(member_name)
                if member is None:
                    raise SemanticError(
                        f"line {node.line}: '{module.name}' has no member "
                        f"'{member_name}' in this subset.\n"
                        f"  It offers: {', '.join(module.names())}."
                    )
                self._bind_imported(local, module, member, node.line)

    def _bind_imported(self, local, module, member, line):
        if local in RESERVED_NAMES:
            raise SemanticError(
                f"line {line}: '{local}' is a builtin in this subset, so an import "
                f"cannot bind that name. Import it under another name "
                f"(`as {local}_`) or call it through the module."
            )
        self.imported[local] = (module, member)

    def _module_member(self, name):
        """Resolve a call or name to (Module, Member), or None.

        `name` is either a plain name a from-import bound, or the canonical
        `module.member` the parser builds for dotted access.
        """
        if name in self.imported:
            return self.imported[name]
        if "." in name:
            alias, _, member_name = name.partition(".")
            module = self.module_aliases.get(alias)
            if module is not None:
                member = module.get(member_name)
                if member is None:
                    return (module, None)
                return (module, member)
        return None

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

    # ------------------------------------------------------------- arrays

    def _size_annotation(self, stmt):
        """Turn `list[int]` into `int[9]`, taking the size from the value.

        The size has to be settled here because it becomes part of the C
        type, and C needs it at the declaration.
        """
        declared = stmt.declared_type
        want = arrays.element(declared)
        value = stmt.value

        if isinstance(value, A.ArrayRepeat):
            count = value.count
            got = self._literal_element_type(value.element, stmt.line)
        elif isinstance(value, A.ArrayLit):
            count = len(value.elements)
            got = self._literal_element_type(value.elements[0], stmt.line)
            for other in value.elements[1:]:
                if self._literal_element_type(other, stmt.line) != got:
                    raise SemanticError(
                        f"line {stmt.line}: the elements of an array must all be the "
                        f"same type; this one mixes {got} and "
                        f"{self._literal_element_type(other, stmt.line)}"
                    )
        else:
            raise SemanticError(
                f"line {stmt.line}: '{stmt.name}' is declared "
                f"{arrays.describe(declared)}, so it must be initialised with an "
                f"array — `[0] * n` or `[a, b, c]`. Its size becomes part of the "
                f"C type, so it cannot come from anywhere else."
            )

        if got != want:
            hint = ""
            if want == "float" and got == "int":
                hint = " — write the values as floats, e.g. `0.0`"
            elif want == "int" and got == "float":
                hint = " — write the values as ints, e.g. `0`"
            raise SemanticError(
                f"line {stmt.line}: '{stmt.name}' is declared "
                f"{arrays.describe(declared)} but is initialised with {got} values"
                f"{hint}.\n  Array elements are not widened, because Python would "
                f"keep what you wrote and C would not, and the two would then print "
                f"different numbers."
            )
        return arrays.make(want, count)

    def _literal_element_type(self, node, line):
        """The type of one element of an array initialiser.

        Only literals are allowed: the initialiser becomes a C initialiser
        list, which has to be constant.
        """
        if isinstance(node, A.Num):
            return "float" if node.is_float else "int"
        if isinstance(node, A.Bool):
            return "bool"
        if isinstance(node, A.UnaryOp) and node.op in ("-", "+"):
            return self._literal_element_type(node.operand, line)
        raise SemanticError(
            f"line {line}: an array initialiser takes literal numbers only, "
            f"because it becomes a C initialiser list. Fill it with a loop "
            f"after declaring it."
        )

    def _array_of(self, name, line):
        """The array type of `name`, or an error explaining what it is."""
        t = self.env.get(name) if self.env else None
        if t is None:
            t = self.symtab.lookup_var(name, line)
        if not arrays.is_array(t):
            raise SemanticError(
                f"line {line}: '{name}' is {t}, not an array, so it cannot be indexed"
            )
        return t

    # ------------------------------------------------- pass 1: type inference

    def _collect_local_types(self, fn: A.FunctionDef):
        """Fixpoint inference of every local's declared type."""
        env = {p.name: p.type for p in fn.params}
        annotated = {}

        for stmt in _walk_stmts(fn.body):
            if isinstance(stmt, A.Assign) and stmt.declared_type:
                if arrays.is_array(stmt.declared_type):
                    stmt.declared_type = self._size_annotation(stmt)
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
            if isinstance(stmt.value, (A.ArrayLit, A.ArrayRepeat)):
                # Checked by _size_annotation during the pre-pass; the type is
                # already on the statement, and inferring the literal here
                # would hit the "nowhere else one can go" rule above.
                if not arrays.is_array(stmt.declared_type or ""):
                    raise SemanticError(
                        f"line {stmt.line}: '{stmt.name}' is given an array, so it must "
                        f"be annotated, e.g. `{stmt.name}: list[int] = ...`"
                    )
                if stmt.name in self.defined:
                    raise SemanticError(
                        f"line {stmt.line}: '{stmt.name}' is already an array and cannot "
                        f"be replaced with another one — assign to its elements instead. "
                        f"A C array is storage, not a reference that can be repointed."
                    )
                stmt.value.inferred_type = stmt.declared_type
                self.symtab.declare_var(stmt.name, stmt.declared_type, stmt.line)
                self.defined.add(stmt.name)
                return
            t = self._infer(stmt.value)
            if arrays.is_array(t):
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name}' would be a second name for the "
                    f"array '{getattr(stmt.value, 'id', '?')}'. This subset has no "
                    f"references, so an array has exactly one name."
                )
            if t == "None":
                raise SemanticError(
                    f"line {stmt.line}: cannot assign None to '{stmt.name}' — this subset "
                    f"has no optional values"
                )
            declared = self.symtab.declare_var(stmt.name, t, stmt.line)
            stmt.declared_type = declared
            self.defined.add(stmt.name)

        elif isinstance(stmt, A.IndexAssign):
            array_type = self._array_of(stmt.name, stmt.line)
            element = arrays.element(array_type)
            index_type = self._infer(stmt.index)
            if index_type not in ("int", "bool"):
                raise SemanticError(
                    f"line {stmt.line}: an array index must be an int, not {index_type}"
                )
            value_type = self._infer(stmt.value)
            if stmt.op:
                value_type = self._binop_type(stmt.op, element, value_type,
                                              stmt.value, stmt.line)
            if value_type != element:
                hint = ""
                if element == "float" and value_type == "int":
                    hint = " — write it as a float, e.g. `1.0`"
                elif element == "int" and value_type == "float":
                    hint = " — use int(...) if you mean to truncate"
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name}' holds {element}, so {value_type} "
                    f"cannot be stored in it{hint}.\n  Elements are not widened: "
                    f"Python would keep the value you wrote and C would convert it, "
                    f"and the two would then print different numbers."
                )

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
                if isinstance(arg, A.Name) and arrays.is_array(
                        self.env.get(arg.id, "")):
                    raise SemanticError(
                        f"line {stmt.line}: print() cannot take a whole array. "
                        f"Python would print `[1, 2, 3]` and matching that formatting "
                        f"exactly is a separate job — loop and print the elements."
                    )
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
                f"line {getattr(expr, 'line', '?')}: a {arrays.describe(t)} value has no truth value in this "
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
            resolved = self._module_member(expr.id)
            if resolved is not None and expr.id not in self.env:
                t = self._module_constant(expr, resolved)
            else:
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
                        f"line {expr.line}: a {arrays.describe(st)} value has no truth value in this subset"
                    )
            t = "bool"
        elif isinstance(expr, A.Call):
            t = self._infer_call(expr)
        elif isinstance(expr, A.Index):
            t = self._infer_index(expr)
        elif isinstance(expr, (A.ArrayLit, A.ArrayRepeat)):
            raise SemanticError(
                f"line {expr.line}: an array literal may only initialise a variable "
                f"declared `list[...]`. There is nowhere else one can go: this subset "
                f"has no way to pass an array except by name."
            )
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
                f"line {line}: operator '{op}' is not valid on {arrays.describe(lt)} and {arrays.describe(rt)}"
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
                    f"line {expr.line}: a {arrays.describe(t)} value has no truth value in this subset"
                )
            return "bool"
        if not is_numeric(t):
            raise SemanticError(f"line {expr.line}: unary '{expr.op}' is not valid on {arrays.describe(t)}")
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
                        f"{arrays.describe(ptype)}, got {arrays.describe(atype)}"
                    )
            call.kind = "user"
            return entry["return_type"]

        resolved = self._module_member(call.name)
        if resolved is not None:
            return self._check_module_call(call, resolved, arg_types)

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

    def _infer_index(self, expr: A.Index):
        array_type = self._array_of(expr.name, expr.line)
        index_type = self._infer(expr.index)
        if index_type not in ("int", "bool"):
            raise SemanticError(
                f"line {expr.line}: an array index must be an int, not {index_type}"
            )
        return arrays.element(array_type)

    # ------------------------------------------------------------- modules

    def _check_module_call(self, call: A.Call, resolved, arg_types):
        module, member = resolved
        where = call.name if "." in call.name else f"{module.name}.{call.name}"

        if member is None:
            _, _, member_name = call.name.partition(".")
            raise SemanticError(
                f"line {call.line}: '{module.name}' has no member '{member_name}' "
                f"in this subset.\n  It offers: {', '.join(module.names())}."
            )
        if not member.is_function:
            raise SemanticError(
                f"line {call.line}: '{where}' is a number, not a function — "
                f"use it without the parentheses"
            )
        if len(arg_types) not in member.arities:
            raise SemanticError(
                f"line {call.line}: '{where}' takes {_arity_words(member.arities)} "
                f"argument(s), got {len(arg_types)}"
            )
        for i, t in enumerate(arg_types, 1):
            if not is_numeric(t):
                raise SemanticError(
                    f"line {call.line}: argument {i} of '{where}' is {t}; every "
                    f"argument of an imported function must be a number"
                )
        call.kind = "module"
        call.member = member
        return member.return_type

    def _module_constant(self, node: A.Name, resolved):
        module, member = resolved
        where = node.id if "." in node.id else f"{module.name}.{node.id}"
        if member is None:
            _, _, member_name = node.id.partition(".")
            raise SemanticError(
                f"line {node.line}: '{module.name}' has no member '{member_name}' "
                f"in this subset.\n  It offers: {', '.join(module.names())}."
            )
        if member.is_function:
            raise SemanticError(
                f"line {node.line}: '{where}' is a function here; it needs "
                f"parentheses to be called"
            )
        node.member = member
        return member.return_type


def _arity_words(arities):
    return " or ".join(str(n) for n in sorted(arities))


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
