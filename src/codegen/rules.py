"""
Deterministic AST -> C translation rules.

Every construct defined in grammar/subset.ebnf has a rule function here.
If you add a new language construct, you add a rule here — you do NOT
reach for the AI-assist module. AI is only ever invoked for the specific,
allow-listed function calls flagged by the type checker (see
semantic/type_checker.py:AI_FALLBACK_FUNCS) that have no rule below.
"""

from ..parser import ast_nodes as A

C_TYPE = {"int": "int", "float": "double", "str": "const char*", "bool": "int", "None": "void"}


def type_to_c(t):
    return C_TYPE[t]


class CodegenRules:
    """Stateless template functions, one per AST node kind."""

    def expr(self, node, ai_resolver=None):
        if isinstance(node, A.Num):
            return node.value if not node.is_float else node.value
        if isinstance(node, A.Str):
            escaped = node.value.replace('"', '\\"')
            return f'"{escaped}"'
        if isinstance(node, A.Bool):
            return "1" if node.value else "0"
        if isinstance(node, A.Name):
            return node.id
        if isinstance(node, A.BinOp):
            return f"({self.expr(node.left, ai_resolver)} {node.op} {self.expr(node.right, ai_resolver)})"
        if isinstance(node, A.UnaryOp):
            op = "!" if node.op == "not" else node.op
            return f"({op}{self.expr(node.operand, ai_resolver)})"
        if isinstance(node, A.Compare):
            return f"({self.expr(node.left, ai_resolver)} {node.op} {self.expr(node.right, ai_resolver)})"
        if isinstance(node, A.BoolOp):
            op = "&&" if node.op == "and" else "||"
            return f"({self.expr(node.left, ai_resolver)} {op} {self.expr(node.right, ai_resolver)})"
        if isinstance(node, A.Call):
            return self._call_expr(node, ai_resolver)
        raise NotImplementedError(f"no codegen rule for expression node {type(node).__name__}")

    def _call_expr(self, node: A.Call, ai_resolver):
        args = ", ".join(self.expr(a, ai_resolver) for a in node.args)
        if getattr(node, "needs_ai", False):
            if ai_resolver is None:
                raise RuntimeError(f"line {node.line}: call to '{node.name}' needs AI-assist but no resolver given")
            return ai_resolver(node, args)
        if node.name == "len":
            return f"(int)strlen({args})"
        return f"{node.name}({args})"

    def print_stmt(self, node: A.Print, ai_resolver, indent):
        pad = "    " * indent
        parts = []
        for a in node.args:
            t = getattr(a, "inferred_type", "int")
            fmt = {"int": "%d", "float": "%f", "str": "%s", "bool": "%d"}[t]
            parts.append((fmt, self.expr(a, ai_resolver)))
        fmt_str = " ".join(f for f, _ in parts) + r"\n"
        args_str = ", ".join(v for _, v in parts)
        if args_str:
            return f'{pad}printf("{fmt_str}", {args_str});'
        return f'{pad}printf("\\n");'
