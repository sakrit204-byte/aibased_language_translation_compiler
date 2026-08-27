"""Scoped symbol table. One scope per function (this subset has no nested
function defs or closures, so a single flat scope per function is enough)."""


class SemanticError(Exception):
    pass


class SymbolTable:
    def __init__(self):
        self.functions: dict[str, dict] = {}   # name -> {params: [(name,type)], return_type}
        self._scope: dict[str, str] = {}        # name -> type, for the function currently being checked

    def declare_function(self, name, params, return_type, line):
        if name in self.functions:
            raise SemanticError(f"line {line}: function '{name}' redefined")
        self.functions[name] = {
            "params": [(p.name, p.type) for p in params],
            "return_type": return_type,
        }

    def enter_function(self, func_entry):
        self._scope = {}
        for pname, ptype in func_entry["params"]:
            self._scope[pname] = ptype

    def declare_var(self, name, type_, line):
        existing = self._scope.get(name)
        if existing is not None and existing != type_:
            raise SemanticError(
                f"line {line}: '{name}' reassigned as {type_}, previously {existing} "
                f"(this subset requires consistent variable types)"
            )
        self._scope[name] = type_

    def lookup_var(self, name, line):
        if name not in self._scope:
            raise SemanticError(f"line {line}: use of undeclared variable '{name}'")
        return self._scope[name]

    def lookup_function(self, name, line):
        if name not in self.functions:
            raise SemanticError(f"line {line}: call to undefined function '{name}'")
        return self.functions[name]
