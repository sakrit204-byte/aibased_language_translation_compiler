"""Symbol table: one flat scope per function (this subset has no nested
function definitions or closures, so nothing deeper is needed).

Beyond name resolution, this records the *declared type* of every local in
every function. Codegen needs that table to hoist declarations to the top of
the C function — which is what makes a variable first assigned inside an
`if` branch still visible after it, exactly as in Python.
"""

from .builtins import assignable


class SemanticError(Exception):
    pass


class SymbolTable:
    def __init__(self):
        # name -> {"params": [(name, type)], "return_type": str, "line": int}
        self.functions: dict[str, dict] = {}
        # function name -> {var name -> declared type}, including parameters
        self.function_locals: dict[str, dict] = {}
        self._scope: dict[str, str] = {}
        self._params: set = set()
        self._current: str = None

    # ---- functions ----

    def declare_function(self, name, params, return_type, line):
        if name in self.functions:
            first = self.functions[name]["line"]
            raise SemanticError(
                f"line {line}: function '{name}' is already defined at line {first}"
            )
        self.functions[name] = {
            "params": [(p.name, p.type) for p in params],
            "return_type": return_type,
            "line": line,
        }

    def lookup_function(self, name, line):
        if name not in self.functions:
            raise SemanticError(f"line {line}: call to undefined function '{name}'")
        return self.functions[name]

    # ---- scopes ----

    def enter_function(self, name, local_types):
        """Install the pre-computed local type environment for `name`."""
        self._current = name
        self._scope = dict(local_types)
        self._params = {p for p, _ in self.functions[name]["params"]}
        self.function_locals[name] = dict(local_types)

    def declare_var(self, name, type_, line):
        """Record an assignment. The declared type is fixed by the inference
        pre-pass; this only verifies the value fits."""
        existing = self._scope.get(name)
        if existing is None:
            self._scope[name] = type_
            self.function_locals[self._current][name] = type_
            return type_
        if not assignable(type_, existing):
            hint = ""
            if type_ == "float" and existing == "int":
                hint = (f" — annotate the first assignment as `{name}: float = ...` if it "
                        f"should hold a float")
            raise SemanticError(
                f"line {line}: cannot assign {type_} to '{name}', which is {existing} "
                f"in this function (this subset gives each variable one static type){hint}"
            )
        return existing

    def lookup_var(self, name, line):
        if name not in self._scope:
            raise SemanticError(
                f"line {line}: use of variable '{name}' before any assignment"
            )
        return self._scope[name]

    def has_var(self, name):
        return name in self._scope

    def is_param(self, name):
        return name in self._params
