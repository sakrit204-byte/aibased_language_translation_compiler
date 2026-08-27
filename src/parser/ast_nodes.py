"""AST node classes. Plain dataclasses — no behavior, just structure."""

from dataclasses import dataclass, field


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
    params: list        # list[Param]
    return_type: str
    body: list
    line: int


@dataclass
class Assign:
    name: str
    value: object
    line: int


@dataclass
class If:
    branches: list       # list[(condition, block)] ; last may have condition=None for else
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
class BinOp:
    op: str
    left: object
    right: object
    line: int


@dataclass
class BoolOp:
    op: str              # "and" | "or"
    left: object
    right: object
    line: int


@dataclass
class UnaryOp:
    op: str               # "-" | "not"
    operand: object
    line: int


@dataclass
class Compare:
    op: str
    left: object
    right: object
    line: int


@dataclass
class Call:
    name: str
    args: list
    line: int


@dataclass
class Name:
    id: str
    line: int


@dataclass
class Num:
    value: str
    is_float: bool
    line: int


@dataclass
class Str:
    value: str
    line: int


@dataclass
class Bool:
    value: bool
    line: int
