"""AST node classes. Plain dataclasses — no behavior, just structure.

Every expression node carries an `inferred_type` slot that the semantic phase
fills in. Codegen *requires* it to be set: a missing type is a compiler bug,
not something to paper over with a default, because a wrong type here becomes
a silently wrong value in the generated C.
"""

from dataclasses import dataclass


# ---------------------------------------------------------------- structure

@dataclass
class Program:
    functions: list


@dataclass
class Param:
    name: str
    type: str


@dataclass
class FunctionDef:
    name: str
    params: list          # list[Param]
    return_type: str
    body: list
    line: int
    docstring: str = None


# ---------------------------------------------------------------- statements

@dataclass
class Assign:
    name: str
    value: object
    line: int
    declared_type: str = None    # filled in by the type checker


@dataclass
class AugAssign:
    name: str
    op: str               # "+", "-", "*", "/", "//", "%", "**"
    value: object
    line: int
    declared_type: str = None
    # `x += e` is desugared to `x = x + e` by the semantic phase, so codegen
    # has exactly one path for binary operators instead of two that can drift.
    expanded: object = None


@dataclass
class If:
    branches: list        # list[(condition, block)]; a trailing (None, block) is the else
    line: int


@dataclass
class While:
    condition: object
    body: list
    line: int


@dataclass
class For:
    var: str
    start: object
    stop: object
    step: object          # None when the source used the 1- or 2-argument form
    body: list
    line: int


@dataclass
class Return:
    value: object
    line: int


@dataclass
class Print:
    args: list
    line: int


@dataclass
class ExprStmt:
    expr: object
    line: int


@dataclass
class Break:
    line: int


@dataclass
class Continue:
    line: int


@dataclass
class Pass:
    line: int


# ---------------------------------------------------------------- expressions

@dataclass
class BinOp:
    op: str               # "+" "-" "*" "/" "//" "%" "**"
    left: object
    right: object
    line: int
    inferred_type: str = None


@dataclass
class BoolOp:
    op: str               # "and" | "or"
    left: object
    right: object
    line: int
    inferred_type: str = None


@dataclass
class UnaryOp:
    op: str               # "-" | "+" | "not"
    operand: object
    line: int
    inferred_type: str = None


@dataclass
class Compare:
    op: str               # "==" "!=" "<" ">" "<=" ">="
    left: object
    right: object
    line: int
    inferred_type: str = None


@dataclass
class Call:
    name: str
    args: list
    line: int
    inferred_type: str = None
    kind: str = None          # "user" | "builtin" | "ai"
    ai_expr: str = None       # the validated C expression, when kind == "ai"


@dataclass
class Name:
    id: str
    line: int
    inferred_type: str = None


@dataclass
class Num:
    value: str
    is_float: bool
    line: int
    inferred_type: str = None


@dataclass
class Str:
    value: str            # decoded text; codegen re-escapes it for C
    line: int
    inferred_type: str = None


@dataclass
class Bool:
    value: bool
    line: int
    inferred_type: str = None


@dataclass
class NoneLit:
    line: int
    inferred_type: str = None
