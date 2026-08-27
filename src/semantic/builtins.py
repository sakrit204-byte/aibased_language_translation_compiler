"""
Static type rules for the builtin functions this compiler knows.

Every name here has a deterministic C translation in codegen/rules.py. None of
them ever reaches the AI-assist module — that was the central conceptual flaw
in the earlier design, which routed `abs`, `pow`, `min`, `max` and `round` to a
language model despite each having a one-line, obviously-correct C rule.

Adding a builtin means adding a type rule here *and* a C template in
codegen/rules.py. `tests/test_builtin_coverage.py` asserts the two stay in sync.
"""

NUMERIC = ("bool", "int", "float")
_RANK = {"bool": 0, "int": 1, "float": 2}


class BuiltinError(Exception):
    """Raised for a misuse of a builtin (wrong arity or argument type)."""


def is_numeric(t):
    return t in NUMERIC


def promote(*types):
    """Result type of an arithmetic operation over `types`.

    Follows Python: bool is an int, and any float makes the result float.
    """
    if any(t == "float" for t in types):
        return "float"
    return "int"


def assignable(source, target):
    """True if a value of type `source` may be stored in a `target` slot.

    Widening only, exactly as Python's numeric tower allows: bool -> int -> float.
    """
    if source == target:
        return True
    if source in _RANK and target in _RANK:
        return _RANK[source] <= _RANK[target]
    return False


def join(a, b):
    """Least type that can hold both `a` and `b`, or None if there isn't one."""
    if a is None:
        return b
    if b is None:
        return a
    if a == b:
        return a
    if a in _RANK and b in _RANK:
        return a if _RANK[a] >= _RANK[b] else b
    return None


def _require_numeric(name, arg_types, line):
    for i, t in enumerate(arg_types):
        if not is_numeric(t):
            raise BuiltinError(
                f"line {line}: {name}() argument {i + 1} is {t}, expected a number"
            )


# --- individual rules -------------------------------------------------------
# Each returns the result type, or raises BuiltinError.

def _len(arg_types, args, line):
    if len(arg_types) != 1:
        raise BuiltinError(f"line {line}: len() takes exactly one argument, got {len(arg_types)}")
    if arg_types[0] != "str":
        raise BuiltinError(
            f"line {line}: len() needs a str in this subset, got {arg_types[0]} "
            f"(there are no sequence types)"
        )
    return "int"


def _abs(arg_types, args, line):
    if len(arg_types) != 1:
        raise BuiltinError(f"line {line}: abs() takes exactly one argument, got {len(arg_types)}")
    _require_numeric("abs", arg_types, line)
    return "float" if arg_types[0] == "float" else "int"


def _min_max(name):
    def rule(arg_types, args, line):
        if len(arg_types) < 2:
            raise BuiltinError(
                f"line {line}: {name}() needs at least two arguments in this subset "
                f"(there are no iterables to take a single argument from)"
            )
        _require_numeric(name, arg_types, line)
        # Python's min/max return one of the *objects* passed in, so
        # `max(1.5, 2)` is the int 2 and prints as "2", while a statically
        # typed C result would be the double 2.0 and print as "2.0". Rather
        # than silently diverge, require the arguments to agree.
        kinds = {"int" if t == "bool" else t for t in arg_types}
        if len(kinds) > 1:
            raise BuiltinError(
                f"line {line}: {name}() arguments must all be int or all be float — "
                f"got {', '.join(arg_types)}. Python returns whichever argument won, so a "
                f"mixed call has no single static type; write "
                f"{name}(float(...), ...) to make the intent explicit."
            )
        return promote(*arg_types)
    return rule


def _pow(arg_types, args, line):
    if len(arg_types) != 2:
        raise BuiltinError(f"line {line}: pow() takes exactly two arguments, got {len(arg_types)}")
    _require_numeric("pow", arg_types, line)
    return power_result_type(arg_types[0], arg_types[1], args[1])


def _round(arg_types, args, line):
    if len(arg_types) not in (1, 2):
        raise BuiltinError(f"line {line}: round() takes one or two arguments, got {len(arg_types)}")
    _require_numeric("round", arg_types, line)
    if len(arg_types) == 2 and arg_types[1] == "float":
        raise BuiltinError(f"line {line}: round() second argument must be an int, got float")
    if len(arg_types) == 1:
        return "int"                      # Python: round(x) -> int
    return arg_types[0] if arg_types[0] != "bool" else "int"


def _int(arg_types, args, line):
    if len(arg_types) != 1:
        raise BuiltinError(f"line {line}: int() takes exactly one argument, got {len(arg_types)}")
    if arg_types[0] not in NUMERIC and arg_types[0] != "str":
        raise BuiltinError(f"line {line}: int() cannot convert {arg_types[0]}")
    return "int"


def _float(arg_types, args, line):
    if len(arg_types) != 1:
        raise BuiltinError(f"line {line}: float() takes exactly one argument, got {len(arg_types)}")
    if arg_types[0] not in NUMERIC and arg_types[0] != "str":
        raise BuiltinError(f"line {line}: float() cannot convert {arg_types[0]}")
    return "float"


def _bool(arg_types, args, line):
    if len(arg_types) != 1:
        raise BuiltinError(f"line {line}: bool() takes exactly one argument, got {len(arg_types)}")
    if arg_types[0] not in NUMERIC and arg_types[0] != "str":
        raise BuiltinError(f"line {line}: bool() cannot convert {arg_types[0]}")
    return "bool"


BUILTINS = {
    "len": _len,
    "abs": _abs,
    "min": _min_max("min"),
    "max": _min_max("max"),
    "pow": _pow,
    "round": _round,
    "int": _int,
    "float": _float,
    "bool": _bool,
}


def power_result_type(base_type, exp_type, exp_node):
    """Result type of `base ** exp`.

    Python returns a float for a negative integer exponent (`2 ** -1 == 0.5`).
    When the exponent is a literal we can decide that statically; when it isn't,
    the result stays int and the runtime raises on a negative exponent rather
    than silently producing 0.
    """
    from ..parser import ast_nodes as A

    if base_type == "float" or exp_type == "float":
        return "float"
    if isinstance(exp_node, A.UnaryOp) and exp_node.op == "-" and isinstance(exp_node.operand, A.Num):
        return "float"
    return "int"


def check_call(name, arg_types, args, line):
    """Type-check a call to a known builtin and return its result type."""
    return BUILTINS[name](arg_types, args, line)
