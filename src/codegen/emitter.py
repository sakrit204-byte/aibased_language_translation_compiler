"""Walks the type-checked AST and emits C source using the deterministic
rule templates in rules.py. The only non-deterministic input it accepts is
`ai_resolver`, a callback the driver wires up to the gated AI-assist module
(src/ai_assist/gate.py) — used exclusively for A.Call nodes the type checker
flagged with `needs_ai = True`."""

from ..parser import ast_nodes as A
from .rules import CodegenRules, type_to_c


class Emitter:
    def __init__(self, ai_resolver=None):
        self.rules = CodegenRules()
        self.ai_resolver = ai_resolver
        self.ai_calls_used = 0
        self.total_call_sites = 0

    def emit_program(self, program: A.Program) -> str:
        lines = ['#include <stdio.h>', '#include <string.h>', '#include <math.h>',
                  '#include "runtime/pyrt.h"', ""]
        # forward declarations so functions can call each other / recurse in any order
        for fn in program.functions:
            lines.append(self._signature(fn) + ";")
        lines.append("")
        for fn in program.functions:
            lines.append(self._emit_function(fn))
            lines.append("")
        if any(fn.name == "main" for fn in program.functions) is False:
            pass  # subset requires user to define their own entry via a call, see driver.py
        return "\n".join(lines)

    def _signature(self, fn: A.FunctionDef):
        params = ", ".join(f"{type_to_c(p.type)} {p.name}" for p in fn.params)
        return f"{type_to_c(fn.return_type)} {fn.name}({params})"

    def _emit_function(self, fn: A.FunctionDef):
        declared = {p.name for p in fn.params}
        body_lines = []
        for stmt in fn.body:
            body_lines.append(self._emit_stmt(stmt, 1, declared))
        return f"{self._signature(fn)} {{\n" + "\n".join(body_lines) + "\n}"

    def _emit_stmt(self, node, indent, declared):
        pad = "    " * indent
        if isinstance(node, A.Assign):
            expr_code = self.rules.expr(node.value, self._resolve)
            t = getattr(node.value, "inferred_type", "int")
            if node.name in declared:
                return f"{pad}{node.name} = {expr_code};"
            declared.add(node.name)
            return f"{pad}{type_to_c(t)} {node.name} = {expr_code};"

        if isinstance(node, A.If):
            out = []
            for i, (cond, body) in enumerate(node.branches):
                inner = "\n".join(self._emit_stmt(s, indent + 1, declared) for s in body)
                if cond is None:  # else
                    out.append(f"{pad}else {{\n{inner}\n{pad}}}")
                elif i == 0:
                    out.append(f"{pad}if ({self.rules.expr(cond, self._resolve)}) {{\n{inner}\n{pad}}}")
                else:
                    out.append(f"{pad}else if ({self.rules.expr(cond, self._resolve)}) {{\n{inner}\n{pad}}}")
            return "\n".join(out)

        if isinstance(node, A.While):
            inner = "\n".join(self._emit_stmt(s, indent + 1, declared) for s in node.body)
            return f"{pad}while ({self.rules.expr(node.condition, self._resolve)}) {{\n{inner}\n{pad}}}"

        if isinstance(node, A.For):
            var = node.var
            start = self.rules.expr(node.start, self._resolve)
            stop = self.rules.expr(node.stop, self._resolve)
            declared = declared | {var}
            inner = "\n".join(self._emit_stmt(s, indent + 1, declared) for s in node.body)
            return (f"{pad}for (int {var} = {start}; {var} < {stop}; {var}++) {{\n"
                    f"{inner}\n{pad}}}")

        if isinstance(node, A.Return):
            if node.value is None:
                return f"{pad}return;"
            return f"{pad}return {self.rules.expr(node.value, self._resolve)};"

        if isinstance(node, A.Print):
            return self.rules.print_stmt(node, self._resolve, indent)

        if isinstance(node, A.ExprStmt):
            return f"{pad}{self.rules.expr(node.expr, self._resolve)};"

        raise NotImplementedError(f"no emitter case for {type(node).__name__}")

    def _resolve(self, call_node: A.Call, args_str: str) -> str:
        """Called by rules.py only for Call nodes flagged needs_ai=True."""
        self.total_call_sites += 1
        if self.ai_resolver is None:
            raise RuntimeError(
                f"line {call_node.line}: '{call_node.name}' has no static rule and "
                f"AI-assist is disabled (--no-ai). Add a rule or enable AI-assist."
            )
        self.ai_calls_used += 1
        return self.ai_resolver(call_node, args_str)
