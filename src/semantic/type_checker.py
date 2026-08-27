"""
Semantic analysis phase: scope resolution + type checking/inference.

This is 100% deterministic — no AI involvement anywhere in this file.
Type inference decisions are exactly the category of decision the project's
AI-usage policy forbids handing to a model (a wrong type here becomes a
silent memory bug in the generated C, which gcc cannot catch).

The one place this phase touches the AI-assist boundary is `AI_FALLBACK_FUNCS`:
a small, explicit allow-list of stdlib-style functions with no static codegen
rule. Calling one of them doesn't fail semantic analysis — it flags the call
node (`node.needs_ai = True`) so the codegen phase knows to route it through
the gated AI-assist module instead of a template. Calling anything NOT on
this allow-list and not user-defined is a hard semantic error, not an AI job:
unbounded fallback is exactly what the strict rules exist to prevent.
"""

from ..parser import ast_nodes as A
from .symbol_table import SymbolTable, SemanticError

# name -> (arg_types_or_None_for_any, return_type)
BUILTIN_FUNCS = {
    "len": (["str"], "int"),
}

# Explicit, bounded allow-list of functions with NO static rule, whose
# translation is delegated to the AI-assist module (see src/ai_assist/gate.py).
# Adding a name here is a deliberate project decision, not something inferred.
AI_FALLBACK_FUNCS = {"abs", "pow", "min", "max", "round"}


class TypeChecker:
    def __init__(self):
        self.symtab = SymbolTable()

    def check(self, program: A.Program):
        for fn in program.functions:
            self.symtab.declare_function(fn.name, fn.params, fn.return_type, fn.line)
        for fn in program.functions:
            self._check_function(fn)
        return self.symtab

    def _check_function(self, fn: A.FunctionDef):
        entry = self.symtab.functions[fn.name]
        self.symtab.enter_function(entry)
        for stmt in fn.body:
            self._check_stmt(stmt, fn.return_type)

    def _check_stmt(self, stmt, ret_type):
        if isinstance(stmt, A.Assign):
            t = self._infer(stmt.value)
            self.symtab.declare_var(stmt.name, t, stmt.line)
        elif isinstance(stmt, A.If):
            for cond, body in stmt.branches:
                if cond is not None:
                    self._infer(cond)
                for s in body:
                    self._check_stmt(s, ret_type)
        elif isinstance(stmt, A.While):
            self._infer(stmt.condition)
            for s in stmt.body:
                self._check_stmt(s, ret_type)
        elif isinstance(stmt, A.For):
            self._infer(stmt.start)
            self._infer(stmt.stop)
            self.symtab.declare_var(stmt.var, "int", stmt.line)
            for s in stmt.body:
                self._check_stmt(s, ret_type)
        elif isinstance(stmt, A.Return):
            if stmt.value is not None:
                t = self._infer(stmt.value)
                if ret_type != "None" and t != ret_type and not (t == "int" and ret_type == "float"):
                    raise SemanticError(
                        f"line {stmt.line}: returning {t}, function declared -> {ret_type}"
                    )
        elif isinstance(stmt, A.Print):
            for a in stmt.args:
                self._infer(a)
        elif isinstance(stmt, A.ExprStmt):
            self._infer(stmt.expr)
        else:
            raise SemanticError(f"unhandled statement node: {type(stmt).__name__}")

    def _infer(self, expr):
        """Compute (and cache on the node as .inferred_type) the static type."""
        if isinstance(expr, A.Num):
            t = "float" if expr.is_float else "int"
        elif isinstance(expr, A.Str):
            t = "str"
        elif isinstance(expr, A.Bool):
            t = "bool"
        elif isinstance(expr, A.Name):
            t = self.symtab.lookup_var(expr.id, expr.line)
        elif isinstance(expr, A.BinOp):
            lt, rt = self._infer(expr.left), self._infer(expr.right)
            if "str" in (lt, rt):
                raise SemanticError(f"line {expr.line}: arithmetic op '{expr.op}' not valid on str")
            t = "float" if "float" in (lt, rt) else "int"
        elif isinstance(expr, A.UnaryOp):
            if expr.op == "not":
                self._infer(expr.operand)
                t = "bool"
            else:
                t = self._infer(expr.operand)
        elif isinstance(expr, A.Compare):
            self._infer(expr.left)
            self._infer(expr.right)
            t = "bool"
        elif isinstance(expr, A.BoolOp):
            self._infer(expr.left)
            self._infer(expr.right)
            t = "bool"
        elif isinstance(expr, A.Call):
            t = self._infer_call(expr)
        else:
            raise SemanticError(f"unhandled expression node: {type(expr).__name__}")
        expr.inferred_type = t
        return t

    def _infer_call(self, call: A.Call):
        arg_types = [self._infer(a) for a in call.args]
        if call.name in self.symtab.functions:
            entry = self.symtab.functions[call.name]
            expected = [t for _, t in entry["params"]]
            if len(expected) != len(arg_types):
                raise SemanticError(
                    f"line {call.line}: '{call.name}' expects {len(expected)} args, got {len(arg_types)}"
                )
            call.needs_ai = False
            return entry["return_type"]
        if call.name in BUILTIN_FUNCS:
            _, ret = BUILTIN_FUNCS[call.name]
            call.needs_ai = False
            return ret
        if call.name in AI_FALLBACK_FUNCS:
            # Flag for the AI-assist gate; codegen will route this node
            # through validation instead of a static template.
            call.needs_ai = True
            return arg_types[0] if arg_types else "int"
        raise SemanticError(
            f"line {call.line}: call to unknown function '{call.name}' — not user-defined, "
            f"not a builtin, and not on the AI-fallback allow-list. This is a real error, "
            f"not something to hand to the model."
        )
