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
from . import classes
from . import dicts
from . import modules
from .builtins import (
    BUILTINS, BuiltinError, assignable, check_call, is_numeric, join,
    power_result_type, promote,
)

RESERVED_NAMES = set(BUILTINS) | {"print", "range"}

# Types that have a truth value in this subset.
TRUTHY_TYPES = ("bool", "int", "float", "str")

MAX_INFERENCE_ROUNDS = 16

# What the generated C can hold. Python's int has no such limit, so a constant
# outside this range is refused rather than quietly wrapped.
_INT64_MIN = -9223372036854775808
_INT64_MAX = 9223372036854775807
# Above this exponent a power is rejected on size alone, because computing it
# first is how you hang the compiler on `2 ** 100000000`.
_POW_FOLD_LIMIT = 4096


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
        self.classes = {}               # name -> classes.ClassInfo
        # Class-level dicts, which are shared and never written, so they
        # become file-scope arrays: C symbol -> (type, literal).
        self.global_dicts = {}
        self.current_class = None       # set while checking a method body
        # Nodes whose own int64 range check is already settled by the
        # enclosing expression. See the UnaryOp branch of _infer.
        self._range_ok = set()
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
        self._check_classes(program)

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

    def _check_new(self, stmt):
        """`c: Cube = Cube()` — the one place an object comes into being."""
        node = stmt.value
        info = self.classes.get(node.class_name)
        if info is None:
            raise SemanticError(
                f"line {stmt.line}: there is no class '{node.class_name}'"
            )
        declared = stmt.declared_type
        if declared is None:
            raise SemanticError(
                f"line {stmt.line}: '{stmt.name}' is given a {info.name}, so it must "
                f"be annotated: `{stmt.name}: {info.name} = {info.name}(...)`"
            )
        if classes.class_of(declared) != info.name:
            raise SemanticError(
                f"line {stmt.line}: '{stmt.name}' is declared "
                f"{classes.describe(declared)} but is given a {info.name}"
            )
        if stmt.name in self.defined:
            raise SemanticError(
                f"line {stmt.line}: '{stmt.name}' already holds an object and cannot "
                f"be replaced with another. An object here is storage, not a "
                f"reference that can be repointed."
            )

        expected = info.init.params[1:]
        arg_types = [self._infer(a) for a in node.args]
        if len(expected) != len(arg_types):
            raise SemanticError(
                f"line {stmt.line}: {info.name}() takes {len(expected)} "
                f"argument(s), got {len(arg_types)}"
            )
        for i, (param, actual) in enumerate(zip(expected, arg_types), 1):
            if not assignable(actual, param.type):
                raise SemanticError(
                    f"line {stmt.line}: argument {i} of {info.name}() "
                    f"({param.name}) is {arrays.describe(param.type)}, got "
                    f"{arrays.describe(actual)}"
                )
        node.inferred_type = declared
        self.symtab.declare_var(stmt.name, declared, stmt.line)
        self.defined.add(stmt.name)

    def _check_attribute_assign(self, stmt):
        info = self._class_of_owner(stmt.owner, stmt.line)
        kind, thing = self._member(info, stmt.member, stmt.line, stmt.owner)
        stmt.class_name = info.name

        if kind == "method":
            raise SemanticError(
                f"line {stmt.line}: '{info.name}.{stmt.member}' is a method and "
                f"cannot be assigned to"
            )
        if kind == "constant":
            raise SemanticError(
                f"line {stmt.line}: '{info.name}.{stmt.member}' is a class constant "
                f"and is shared by every instance, so it cannot be assigned to"
            )

        indexed = isinstance(stmt, A.AttributeIndexAssign)
        if indexed:
            if not arrays.is_array(thing.type):
                raise SemanticError(
                    f"line {stmt.line}: '{info.name}.{stmt.member}' is "
                    f"{arrays.describe(thing.type)}, not an array"
                )
            index_type = self._infer(stmt.index)
            if index_type not in ("int", "bool"):
                raise SemanticError(
                    f"line {stmt.line}: an array index must be an int, not "
                    f"{index_type}"
                )
            target_type = arrays.element(thing.type)
        else:
            target_type = thing.type

        if arrays.is_array(target_type):
            value_type = target_type
        else:
            value_type = self._infer(stmt.value)
            if stmt.op:
                value_type = self._binop_type(stmt.op, target_type, value_type,
                                              stmt.value, stmt.line)

        if arrays.is_array(target_type):
            # The one exception: __init__ fills the array, which is exactly
            # what Python does there — a fresh list per instance.
            in_init = (self.current_fn is not None
                       and self.current_fn.name == "__init__"
                       and stmt.owner == "self")
            if not in_init:
                raise SemanticError(
                    f"line {stmt.line}: '{info.name}.{stmt.member}' is an array and "
                    f"is storage, not a reference — assign to its elements instead, "
                    f"e.g. `{stmt.owner}.{stmt.member}[i] = ...`.\n  Only __init__ "
                    f"may fill it as a whole."
                )
            if not isinstance(stmt.value, (A.ArrayLit, A.ArrayRepeat)):
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.member}' must be filled with an array "
                    f"literal here, e.g. `[0] * {arrays.size(target_type)}`"
                )
            count = (stmt.value.count if isinstance(stmt.value, A.ArrayRepeat)
                     else len(stmt.value.elements))
            if count != arrays.size(target_type):
                raise SemanticError(
                    f"line {stmt.line}: '{info.name}.{stmt.member}' is declared with "
                    f"{arrays.size(target_type)} elements but is filled with {count}"
                )
            element = (stmt.value.element if isinstance(stmt.value, A.ArrayRepeat)
                       else stmt.value.elements[0])
            got = self._literal_element_type(element, stmt.line)
            if got != arrays.element(target_type):
                raise SemanticError(
                    f"line {stmt.line}: '{info.name}.{stmt.member}' holds "
                    f"{arrays.element(target_type)} but is filled with {got} values"
                )
            stmt.value.inferred_type = target_type
            return
        if indexed:
            if value_type != target_type:
                raise SemanticError(
                    f"line {stmt.line}: '{info.name}.{stmt.member}' holds "
                    f"{target_type}, so {value_type} cannot be stored in it"
                )
        elif not assignable(value_type, target_type):
            raise SemanticError(
                f"line {stmt.line}: '{info.name}.{stmt.member}' is "
                f"{arrays.describe(target_type)}, so {arrays.describe(value_type)} "
                f"cannot be stored in it"
            )

    # ------------------------------------------------------------ classes

    def _check_classes(self, program: A.Program):
        """Register every class, then check its methods like functions.

        A method is an ordinary function whose first parameter is a pointer
        to the instance; see semantic/classes.py. Registering all classes
        before checking any body means a method may mention a class defined
        further down the file.
        """
        for node in getattr(program, "classes", ()):
            if node.name in self.classes:
                first = self.classes[node.name].line
                raise SemanticError(
                    f"line {node.line}: class '{node.name}' is already defined at "
                    f"line {first}"
                )
            if node.name in RESERVED_NAMES or node.name in ("int", "float",
                                                            "str", "bool", "list"):
                raise SemanticError(
                    f"line {node.line}: '{node.name}' is a builtin name and cannot "
                    f"be a class"
                )
            info = classes.ClassInfo(node.name, node.line)

            for field_name, field_type, line in node.fields:
                if classes.is_instance(field_type):
                    raise SemanticError(
                        f"line {line}: field '{field_name}' is another object. A "
                        f"field here is a number, a string or an array — an object "
                        f"holding an object would need a pointer, and this subset "
                        f"has no references."
                    )
                if arrays.is_array(field_type) and arrays.size(field_type) is None:
                    raise SemanticError(
                        f"line {line}: array field '{field_name}' needs its length "
                        f"in the annotation, e.g. "
                        f"`{field_name}: list[{arrays.element(field_type)}, 9]` — it "
                        f"is storage inside the struct, so the size is part of the "
                        f"type. Fill it in __init__."
                    )
                info.fields[field_name] = classes.Field(field_name, field_type, line)

            for const_name, const_type, value, line in node.constants:
                if arrays.is_array(const_type):
                    raise SemanticError(
                        f"line {line}: `{const_name}: list[...] = [...]` in a class "
                        f"body makes ONE list shared by every instance — writing "
                        f"through one object would change all of them. That is "
                        f"Python's behaviour and it is almost never what is "
                        f"wanted.\n  Declare the size in the annotation and fill it "
                        f"in __init__ instead:\n"
                        f"      {const_name}: list[{arrays.element(const_type)}, N]\n"
                        f"      def __init__(self) -> None:\n"
                        f"          self.{const_name} = [0] * N"
                    )
                if isinstance(value, A.DictLit):
                    const_type = self._dict_constant_type(
                        node.name, const_name, const_type, value, line)
                    info.constants[const_name] = classes.Constant(
                        const_name, const_type, value, line)
                    self.global_dicts[
                        classes.method_symbol(node.name, const_name)] = (
                            const_type, value)
                    continue
                if not isinstance(value, (A.Num, A.Str, A.Bool,
                                          A.UnaryOp)):
                    raise SemanticError(
                        f"line {line}: a class constant must be a literal — it is "
                        f"shared by every instance and is inlined where it is used"
                    )
                if const_name in info.fields:
                    raise SemanticError(
                        f"line {line}: '{const_name}' is already a field of "
                        f"'{node.name}'"
                    )
                info.constants[const_name] = classes.Constant(
                    const_name, const_type, value, line)

            for method in node.methods:
                if method.name in info.fields or method.name in info.constants:
                    raise SemanticError(
                        f"line {method.line}: '{method.name}' is already a member of "
                        f"'{node.name}'"
                    )
                if not method.params or method.params[0].name != "self":
                    raise SemanticError(
                        f"line {method.line}: method '{node.name}.{method.name}' "
                        f"needs `self` as its first parameter"
                    )
                if classes.is_instance(method.return_type):
                    raise SemanticError(
                        f"line {method.line}: '{node.name}.{method.name}' returns an "
                        f"object. C would copy the struct where Python hands back the "
                        f"same instance, so the two would part company the moment the "
                        f"caller wrote to it.\n  Fill an object passed in as a "
                        f"parameter instead — that is shared, not copied."
                    )
                if arrays.is_array(method.return_type):
                    raise SemanticError(
                        f"line {method.line}: '{node.name}.{method.name}' cannot "
                        f"return an array"
                    )
                info.methods[method.name] = method
                if method.name == "__init__":
                    info.init = method

            if info.init is None:
                raise SemanticError(
                    f"line {node.line}: class '{node.name}' needs an __init__, and it "
                    f"has to assign every field. A C struct starts as whatever was on "
                    f"the stack, and Python has no attribute at all until one is "
                    f"assigned, so the two only agree if __init__ sets them."
                )
            if info.init.return_type != "None":
                raise SemanticError(
                    f"line {info.init.line}: __init__ must be declared -> None"
                )
            self.classes[node.name] = info

        # Methods are registered under a mangled name so a call can be checked
        # exactly like a call to a free function.
        for name, info in self.classes.items():
            for method_name, method in info.methods.items():
                self.symtab.declare_function(
                    classes.method_symbol(name, method_name),
                    method.params, method.return_type, method.line)

        for node in getattr(program, "classes", ()):
            info = self.classes[node.name]
            for method in node.methods:
                self.current_class = info
                self._check_function(
                    method, symbol=classes.method_symbol(node.name, method.name))
                self.current_class = None
            self._check_init_assigns_every_field(info)

    def _check_init_assigns_every_field(self, info):
        assigned = set()
        for stmt in _walk_stmts(info.init.body):
            if isinstance(stmt, (A.AttributeAssign, A.AttributeIndexAssign)):
                if stmt.owner == "self":
                    assigned.add(stmt.member)
        missing = [f for f in info.fields if f not in assigned]
        if missing:
            raise SemanticError(
                f"line {info.init.line}: __init__ of '{info.name}' leaves "
                f"{', '.join(repr(m) for m in missing)} unassigned. Every field has "
                f"to be set here: an unset C struct member holds whatever was on the "
                f"stack, while Python would not have the attribute at all."
            )

    def _class_of_owner(self, owner, line):
        """The ClassInfo for the variable `owner`, or an error saying why not."""
        t = (self.env or {}).get(owner)
        if t is None and self.symtab.has_var(owner):
            t = self.symtab.lookup_var(owner, line)
        if t is None:
            raise SemanticError(
                f"line {line}: '{owner}' is not defined in this function, so "
                f"'{owner}.…' is attribute access on nothing.\n  A dot is valid on "
                f"an imported module (`import math` then `math.sqrt(x)`) or on a "
                f"class instance (`c: Cube = Cube()` then `c.turn(0)`)."
            )
        if not classes.is_instance(t):
            raise SemanticError(
                f"line {line}: '{owner}' is {arrays.describe(t)}, not an object, so "
                f"it has no attributes — this subset has no objects here to hold a "
                f"field.\n  A dot is valid on an imported module (`import math` then "
                f"`math.sqrt(x)`) or on a class instance."
            )
        return self.classes[classes.class_of(t)]

    def _member(self, info, name, line, owner):
        kind, thing = info.lookup(name)
        if kind is None:
            raise SemanticError(
                f"line {line}: '{info.name}' has no member '{name}'.\n"
                f"  It has: {', '.join(info.member_names())}."
            )
        if classes.is_private(name) and owner != "self":
            raise SemanticError(
                f"line {line}: '{name}' is private to '{info.name}' and cannot be "
                f"read from outside it. Add a method that returns what is needed."
            )
        return kind, thing

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

    def _dict_constant_type(self, class_name, const_name, declared, node, line):
        """Check a class-level dict literal and give it its sized type.

        A dict in a class body is one object shared by every instance — which
        is exactly right when nothing writes to it, and that is enforced:
        assigning to a class constant is already an error.
        """
        if not dicts.is_dict(declared):
            raise SemanticError(
                f"line {line}: '{class_name}.{const_name}' is given a dict, so it "
                f"must be annotated, e.g. `{const_name}: dict[str, str] = {{...}}`"
            )
        want_key = dicts.key_type(declared)
        want_value = dicts.value_type(declared)
        seen = []
        for key in node.keys:
            got = self._literal_key_type(key, line)
            if got != want_key:
                raise SemanticError(
                    f"line {line}: '{class_name}.{const_name}' has {want_key} keys "
                    f"but one is {got}"
                )
            text = self._literal_key_text(key)
            if text in seen:
                raise SemanticError(
                    f"line {line}: duplicate key {text!r} in "
                    f"'{class_name}.{const_name}'"
                )
            seen.append(text)
        for value in node.values:
            got = self._literal_value_type(value, line)
            if got != want_value:
                raise SemanticError(
                    f"line {line}: '{class_name}.{const_name}' holds {want_value} "
                    f"values but one is {got}"
                )
        return dicts.make(want_key, want_value, len(node.keys))

    def _size_dict_annotation(self, stmt):
        """Turn `dict[str, int]` into a type carrying the key count, and
        check the literal against it."""
        declared = stmt.declared_type
        want_key = dicts.key_type(declared)
        want_value = dicts.value_type(declared)
        node = stmt.value

        if not isinstance(node, A.DictLit):
            raise SemanticError(
                f"line {stmt.line}: '{stmt.name}' is declared "
                f"{dicts.describe(declared)}, so it must be initialised with a "
                f"dict literal. Its keys become part of the type, so they have to "
                f"be written out here."
            )

        seen = []
        for key in node.keys:
            got = self._literal_key_type(key, stmt.line)
            if got != want_key:
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name}' has {want_key} keys but "
                    f"'{self._literal_key_text(key)}' is {got}"
                )
            text = self._literal_key_text(key)
            if text in seen:
                raise SemanticError(
                    f"line {stmt.line}: duplicate key {text!r} in '{stmt.name}'. "
                    f"Python would keep the last one silently; here the key set is "
                    f"the type, so it has to be written once."
                )
            seen.append(text)

        for value in node.values:
            got = self._literal_value_type(value, stmt.line)
            if got != want_value:
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name}' holds {want_value} values but "
                    f"one entry is {got}"
                )
        return dicts.make(want_key, want_value, len(node.keys))

    def _literal_value_type(self, node, line):
        """A dict value may be a string as well as a number — unlike an
        array element, which has to be numeric."""
        if isinstance(node, A.Str):
            return "str"
        return self._literal_element_type(node, line)

    def _literal_key_type(self, node, line):
        if isinstance(node, A.Str):
            return "str"
        if isinstance(node, A.Num) and not node.is_float:
            return "int"
        raise SemanticError(
            f"line {line}: a dict key must be a literal string or int — the keys "
            f"are part of the type, so they are fixed while compiling"
        )

    @staticmethod
    def _literal_key_text(node):
        return node.value if isinstance(node, A.Str) else str(node.value)

    def _dict_of(self, name, line):
        """The dict type of `name`, which may be `d` or `obj.CONST`."""
        if "." in name:
            owner, _, member = name.partition(".")
            info = None
            if owner in self.classes and not (self.env or {}).get(owner):
                info = self.classes[owner]
            else:
                t = (self.env or {}).get(owner)
                if classes.is_instance(t or ""):
                    info = self.classes[classes.class_of(t)]
            if info is None:
                return None
            kind, thing = info.lookup(member)
            if kind != "constant" or not dicts.is_dict(thing.type):
                return None
            return thing.type
        t = (self.env or {}).get(name)
        if t is None and self.symtab.has_var(name):
            t = self.symtab.lookup_var(name, line)
        return t if dicts.is_dict(t) else None

    def _dict_symbol(self, name):
        """The C symbol a dotted dict name refers to."""
        if "." not in name:
            return name
        owner, _, member = name.partition(".")
        if owner in self.classes and not (self.env or {}).get(owner):
            return classes.method_symbol(owner, member)
        t = (self.env or {}).get(owner)
        return classes.method_symbol(classes.class_of(t), member)

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
                elif dicts.is_dict(stmt.declared_type):
                    stmt.declared_type = self._size_dict_annotation(stmt)
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
            elif isinstance(stmt, A.ForKeys):
                t = self._dict_of(stmt.dict_name, stmt.line)
                if dicts.is_dict(t):
                    env[stmt.var] = dicts.key_type(t)
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

    def _check_function(self, fn: A.FunctionDef, symbol=None):
        self.current_fn = fn
        local_types = self._collect_local_types(fn)
        self.symtab.enter_function(symbol or fn.name, local_types)
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
            if isinstance(stmt.value, A.DictLit):
                if not dicts.is_dict(stmt.declared_type or ""):
                    raise SemanticError(
                        f"line {stmt.line}: '{stmt.name}' is given a dict, so it "
                        f"must be annotated, e.g. "
                        f"`{stmt.name}: dict[str, int] = {{...}}`"
                    )
                if stmt.name in self.defined:
                    raise SemanticError(
                        f"line {stmt.line}: '{stmt.name}' is already a dict and "
                        f"cannot be replaced with another — its keys are part of "
                        f"its type. Assign to its entries instead."
                    )
                stmt.value.inferred_type = stmt.declared_type
                self.symtab.declare_var(stmt.name, stmt.declared_type, stmt.line)
                self.defined.add(stmt.name)
                return
            if isinstance(stmt.value, A.New):
                self._check_new(stmt)
                return
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

        elif isinstance(stmt, (A.AttributeAssign, A.AttributeIndexAssign)):
            self._check_attribute_assign(stmt)

        elif isinstance(stmt, A.MethodCall):
            self._infer(stmt)

        elif isinstance(stmt, A.ForKeys):
            dict_type = self._dict_of(stmt.dict_name, stmt.line)
            if dict_type is None:
                raise SemanticError(
                    f"line {stmt.line}: `for {stmt.var} in {stmt.dict_name}` needs "
                    f"{stmt.dict_name!r} to be a dict; `for` otherwise iterates "
                    f"over range(...) in this subset"
                )
            stmt.dict_name = self._dict_symbol(stmt.dict_name)
            self.symtab.declare_var(stmt.var, dicts.key_type(dict_type), stmt.line)
            self.defined.add(stmt.var)
            self.loop_depth += 1
            self._check_block(stmt.body, ret_type)
            self.loop_depth -= 1

        elif isinstance(stmt, A.IndexAssign) and self._dict_of(stmt.name,
                                                               stmt.line):
            dict_type = self._dict_of(stmt.name, stmt.line)
            key_type = self._infer(stmt.index)
            if key_type != dicts.key_type(dict_type):
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name}' has "
                    f"{dicts.key_type(dict_type)} keys, so it cannot be indexed "
                    f"with {key_type}"
                )
            want = dicts.value_type(dict_type)
            value_type = self._infer(stmt.value)
            if stmt.op:
                value_type = self._binop_type(stmt.op, want, value_type,
                                              stmt.value, stmt.line)
            if value_type != want:
                raise SemanticError(
                    f"line {stmt.line}: '{stmt.name}' holds {want}, so {value_type} "
                    f"cannot be stored in it"
                )
            stmt.kind = "dict"

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

    def _fold_int(self, expr):
        """The value of an integer constant expression, or None if it is not
        one (or Python would not give an int back).

        Python's int grows to whatever size it needs; the subset computes in
        int64. Without this check `print(2 ** 100)` compiles without a word
        of complaint and prints 0, which is the worst thing a compiler can do:
        a wrong answer that looks like an answer. Folding the constant
        expressions turns that into a diagnostic, and a diagnostic is
        something the repair path can act on.
        """
        if isinstance(expr, A.Num):
            return None if expr.is_float else int(expr.value)
        if isinstance(expr, A.UnaryOp) and expr.op in ("-", "+"):
            inner = self._fold_int(expr.operand)
            if inner is None:
                return None
            return -inner if expr.op == "-" else inner
        if not isinstance(expr, A.BinOp):
            return None
        left = self._fold_int(expr.left)
        right = self._fold_int(expr.right)
        if left is None or right is None:
            return None
        try:
            if expr.op == "+":
                return left + right
            if expr.op == "-":
                return left - right
            if expr.op == "*":
                return left * right
            if expr.op == "//":
                return left // right if right else None
            if expr.op == "%":
                return left % right if right else None
            if expr.op == "**":
                if right < 0:
                    return None          # Python hands back a float
                if abs(left) > 1 and right > _POW_FOLD_LIMIT:
                    # 2 ** 100000000 would be computed in full before it
                    # could be rejected, so decide by size alone.
                    self._does_not_fit(expr.line, None)
                return left ** right
        except ArithmeticError:
            return None
        return None

    def _does_not_fit(self, line, value):
        """Refuse a constant the generated C could not hold."""
        if value is None:
            raise SemanticError(
                f"line {line}: this power is far larger than a 64-bit "
                f"integer can hold. The subset computes in int64, so the "
                f"compiled program would print a wrapped number rather than "
                f"the value Python prints."
            )
        raise SemanticError(
            f"line {line}: this computes {value}, which does not fit in a "
            f"64-bit integer. The subset computes in int64, so the compiled "
            f"program would print a wrapped number rather than this value. "
            f"Carry a number this large as its digits in an array."
        )

    def _check_int_range(self, expr, t):
        """int-typed constant expressions have to fit in int64."""
        if t != "int" or id(expr) in self._range_ok:
            return
        value = self._fold_int(expr)
        if value is not None and not _INT64_MIN <= value <= _INT64_MAX:
            self._does_not_fit(expr.line, value)

    def _infer(self, expr):
        """Compute and cache the static type of `expr` on the node itself."""
        if isinstance(expr, A.Num):
            t = "float" if expr.is_float else "int"
            self._check_int_range(expr, t)
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
            self._check_int_range(expr, t)
        elif isinstance(expr, A.UnaryOp):
            # `-2 ** 63` is a minus sign applied to 2**63, and
            # `-9223372036854775808` is a minus sign applied to a literal one
            # past int64's top. Both are perfectly good programs: the
            # negation lands exactly on int64's lower bound. So settle the
            # whole expression before the operand is judged on its own.
            if expr.op == "-":
                whole = self._fold_int(expr)
                if whole is not None and _INT64_MIN <= whole <= _INT64_MAX:
                    self._range_ok.add(id(expr.operand))
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
        elif isinstance(expr, A.Contains):
            t = self._infer_contains(expr)
        elif isinstance(expr, A.Attribute):
            t = self._infer_attribute(expr)
        elif isinstance(expr, A.AttributeIndex):
            t = self._infer_attribute_index(expr)
        elif isinstance(expr, A.MethodCall):
            t = self._infer_method_call(expr)
        elif isinstance(expr, A.New):
            raise SemanticError(
                f"line {expr.line}: `{expr.class_name}()` may only initialise a "
                f"variable declared `{expr.class_name}`, e.g. "
                f"`c: {expr.class_name} = {expr.class_name}()`. An object here is "
                f"storage, not a reference that can be moved around."
            )
        elif isinstance(expr, A.DictLit):
            raise SemanticError(
                f"line {expr.line}: a dict literal may only initialise a variable "
                f"declared `dict[...]`. Its keys are part of its type, so there is "
                f"nowhere else one can go."
            )
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

    def _infer_contains(self, expr: A.Contains):
        dict_type = self._dict_of(expr.dict_name, expr.line)
        if dict_type is None:
            raise SemanticError(
                f"line {expr.line}: `in` tests membership of a dict in this subset, "
                f"and '{expr.dict_name}' is not one"
            )
        key_type = self._infer(expr.key)
        if key_type != dicts.key_type(dict_type):
            raise SemanticError(
                f"line {expr.line}: '{expr.dict_name}' has "
                f"{dicts.key_type(dict_type)} keys, so {key_type} cannot be in it"
            )
        expr.dict_name = self._dict_symbol(expr.dict_name)
        return "bool"

    def _infer_index(self, expr: A.Index):
        dict_type = self._dict_of(expr.name, expr.line)
        if dict_type is not None:
            key_type = self._infer(expr.index)
            if key_type != dicts.key_type(dict_type):
                raise SemanticError(
                    f"line {expr.line}: '{expr.name}' has "
                    f"{dicts.key_type(dict_type)} keys, so it cannot be indexed "
                    f"with {key_type}"
                )
            expr.kind = "dict"
            return dicts.value_type(dict_type)

        array_type = self._array_of(expr.name, expr.line)
        index_type = self._infer(expr.index)
        if index_type not in ("int", "bool"):
            raise SemanticError(
                f"line {expr.line}: an array index must be an int, not {index_type}"
            )
        return arrays.element(array_type)

    def _infer_attribute(self, expr: A.Attribute):
        # `Counter.START` — a constant read through the class rather than an
        # instance. Only a constant: a field needs an object to live in, and
        # a method needs one to act on.
        if expr.owner in self.classes and not (self.env or {}).get(expr.owner):
            info = self.classes[expr.owner]
            kind, thing = info.lookup(expr.member)
            if kind != "constant":
                raise SemanticError(
                    f"line {expr.line}: '{expr.owner}.{expr.member}' is "
                    f"{'not a member of ' + expr.owner if kind is None else 'a ' + kind}"
                    f". Only a class constant can be read through the class itself; "
                    f"a field or method needs an instance."
                )
            expr.kind = "constant"
            expr.class_name = info.name
            if dicts.is_dict(thing.type):
                expr.kind = "dict_constant"
                expr.member = classes.method_symbol(info.name, expr.member)
            return thing.type

        info = self._class_of_owner(expr.owner, expr.line)
        kind, thing = self._member(info, expr.member, expr.line, expr.owner)
        expr.class_name = info.name
        if kind == "method":
            raise SemanticError(
                f"line {expr.line}: '{info.name}.{expr.member}' is a method; it "
                f"needs parentheses to be called. A method is not a value here."
            )
        expr.kind = kind
        if kind == "constant" and dicts.is_dict(thing.type):
            # A class-level dict lives at file scope, so the node carries the
            # symbol rather than a struct member.
            expr.kind = "dict_constant"
            expr.member = classes.method_symbol(info.name, expr.member)
        return thing.type

    def _infer_attribute_index(self, expr: A.AttributeIndex):
        dotted = f"{expr.owner}.{expr.member}"
        dict_type = self._dict_of(dotted, expr.line)
        if dict_type is not None:
            key_type = self._infer(expr.index)
            if key_type != dicts.key_type(dict_type):
                raise SemanticError(
                    f"line {expr.line}: '{dotted}' has "
                    f"{dicts.key_type(dict_type)} keys, so it cannot be indexed "
                    f"with {key_type}"
                )
            expr.class_name = self._dict_symbol(dotted)
            expr.kind = "dict"
            return dicts.value_type(dict_type)

        info = self._class_of_owner(expr.owner, expr.line)
        kind, thing = self._member(info, expr.member, expr.line, expr.owner)
        if kind != "field" or not arrays.is_array(thing.type):
            raise SemanticError(
                f"line {expr.line}: '{info.name}.{expr.member}' is "
                f"{arrays.describe(getattr(thing, 'type', '?'))}, not an array, so "
                f"it cannot be indexed"
            )
        index_type = self._infer(expr.index)
        if index_type not in ("int", "bool"):
            raise SemanticError(
                f"line {expr.line}: an array index must be an int, not {index_type}"
            )
        expr.class_name = info.name
        return arrays.element(thing.type)

    def _infer_method_call(self, call: A.MethodCall):
        info = self._class_of_owner(call.owner, call.line)
        kind, method = self._member(info, call.method, call.line, call.owner)
        if kind != "method":
            raise SemanticError(
                f"line {call.line}: '{info.name}.{call.method}' is a "
                f"{'constant' if kind == 'constant' else 'field'}, not a method, so "
                f"it cannot be called"
            )
        if call.method == "__init__":
            raise SemanticError(
                f"line {call.line}: __init__ runs when the object is created; it "
                f"cannot be called again. A C struct is storage, so there is no "
                f"second construction to perform."
            )
        arg_types = [self._infer(a) for a in call.args]
        expected = method.params[1:]          # self is implicit
        if len(expected) != len(arg_types):
            raise SemanticError(
                f"line {call.line}: '{info.name}.{call.method}' takes "
                f"{len(expected)} argument(s), got {len(arg_types)}"
            )
        for i, (param, actual) in enumerate(zip(expected, arg_types), 1):
            if not assignable(actual, param.type):
                raise SemanticError(
                    f"line {call.line}: argument {i} of '{info.name}.{call.method}' "
                    f"({param.name}) is {arrays.describe(param.type)}, got "
                    f"{arrays.describe(actual)}"
                )
        if not self.prepass:
            self.call_sites += 1
        call.class_name = info.name
        return method.return_type

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
