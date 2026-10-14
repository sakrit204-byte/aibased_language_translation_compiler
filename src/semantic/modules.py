"""
The modules an import may name, and what each one makes callable.

This subset has no module system in the Python sense: there is nothing to
execute at import time, no namespace object, and no file to go and read. An
import here is a *declaration* — it tells the compiler that a known set of
names is now in scope, each with a static type rule and a static C
translation. That is the same contract as a builtin in semantic/builtins.py;
the only difference is that an import is what brings it into scope.

Two modules exist.

`math` maps onto <math.h>. Every member below has a deterministic C
translation, so none of them ever reaches the AI gate. That is a real
improvement on calling `sqrt(x)` bare: the bare call has no rule, so it costs
a model round trip and comes back proven only to *compile*, whereas
`math.sqrt` is known to be right here. Note the places Python and C disagree:
math.floor/ceil/trunc return an int in Python and a double in C, so the cast
is part of the rule.

`gfx` is the graphics shim in src/runtime/gfx.c. Importing it is what links
that file in — see ai_assist/profiles.py, which owns the C signatures. It is
the one way a program in this subset can open a window, and it works only
because every function in it takes and returns numbers.

A module that cannot be expressed is rejected by name, with the reason,
rather than falling through to a generic "unknown module".
"""

from ..ai_assist import profiles


class ModuleError(Exception):
    """Raised for an import this subset cannot honour."""


class Member:
    """One importable name: either a function or a constant.

    `arities` is the set of argument counts accepted. `template` is the C
    expression, with {0}, {1}, ... for the arguments. A constant has no
    arities and a template with no placeholders.
    """

    def __init__(self, name, return_type, template, arities=None):
        self.name = name
        self.return_type = return_type
        self.template = template
        self.arities = arities          # None => constant

    @property
    def is_function(self):
        return self.arities is not None

    def render(self, args):
        return self.template.format(*args)


class Module:
    def __init__(self, name, members, profile=None, summary=""):
        self.name = name
        self.members = {m.name: m for m in members}
        self.profile = profile          # profiles.Profile, or None
        self.summary = summary

    def get(self, member):
        return self.members.get(member)

    def names(self):
        return sorted(self.members)


# ------------------------------------------------------------------- math
#
# M_PI is deliberately not used: it is POSIX rather than C99, and the
# generated code is compiled with -std=c99. These literals are the same
# doubles Python's math module exposes, so printing one matches CPython.
_PI = "3.14159265358979323846"


def _f1(name, template=None):
    """A one-argument libm function returning a double."""
    return Member(name, "float", template or name + "({0})", arities={1})


def _f2(name, template=None):
    return Member(name, "float", template or name + "({0}, {1})", arities={2})


def _toint(name):
    """floor/ceil/trunc: a double in C, an int in Python. Python wins."""
    return Member(name, "int", "(py_int)" + name + "({0})", arities={1})


def _pred(name):
    return Member(name, "bool", "(" + name + "({0}) ? 1 : 0)", arities={1})


class _Log(Member):
    """math.log(x) is the natural log; math.log(x, base) divides. C has no
    two-argument log, so the arity decides the expression."""

    def __init__(self):
        super().__init__("log", "float", None, arities={1, 2})

    def render(self, args):
        if len(args) == 1:
            return "log({})".format(args[0])
        return "(log({}) / log({}))".format(args[0], args[1])


MATH = Module(
    "math",
    [
        _f1("sqrt"),
        _f1("sin"), _f1("cos"), _f1("tan"),
        _f1("asin"), _f1("acos"), _f1("atan"),
        _f1("sinh"), _f1("cosh"), _f1("tanh"),
        _f1("exp"), _f1("log10"), _f1("log2"),
        _f1("fabs"),
        _Log(),
        _f2("pow"), _f2("fmod"), _f2("hypot"),
        _f2("atan2"), _f2("copysign"),
        _toint("floor"), _toint("ceil"), _toint("trunc"),
        Member("degrees", "float", "({0} * (180.0 / " + _PI + "))", arities={1}),
        Member("radians", "float", "({0} * (" + _PI + " / 180.0))", arities={1}),
        _pred("isnan"), _pred("isinf"), _pred("isfinite"),
        Member("pi", "float", _PI),
        Member("e", "float", "2.71828182845904523536"),
        Member("tau", "float", "6.28318530717958647692"),
        Member("inf", "float", "INFINITY"),
        Member("nan", "float", "NAN"),
    ],
    summary="the parts of <math.h> that take and return numbers",
)


# -------------------------------------------------------------------- gfx
#
# Built from the profile rather than restated here, so the Python-visible
# names and the C signatures cannot drift apart. `gfx_open` is reachable as
# `gfx.open`: the prefix is what the module name already says.

_GFX_RETURNS = {
    "gfx_open": "int", "gfx_running": "int", "gfx_key": "int",
    "gfx_key_left": "int", "gfx_key_right": "int", "gfx_key_up": "int",
    "gfx_key_down": "int", "gfx_key_space": "int",
    "gfx_time": "float",
}


def _gfx_members():
    out = []
    for c_name, signature in profiles.GFX.functions.items():
        member = c_name[len("gfx_"):]
        params = signature[signature.index("(") + 1:signature.rindex(")")]
        arity = 0 if params.strip() == "void" else params.count(",") + 1
        placeholders = ", ".join("{%d}" % i for i in range(arity))
        out.append(Member(
            member,
            _GFX_RETURNS.get(c_name, "None"),
            "{}({})".format(c_name, placeholders),
            arities={arity},
        ))
    return out


GFX = Module(
    "gfx",
    _gfx_members(),
    profile=profiles.GFX,
    summary="a window, a GL context and a keyboard, behind a scalar-only API",
)


MODULES = {"math": MATH, "gfx": GFX}


# ------------------------------------------------- modules we cannot accept
#
# Named explicitly so the error can say *why*. Most of these fail for the same
# underlying reason — the API hands back something the subset has no way to
# hold — and saying so is more use than "no such module".

_NO_OBJECTS = ("its API is built from objects and arrays, and this subset has "
               "neither, so there is no way to hold what it would hand back")

UNSUPPORTED = {
    "pygame": _NO_OBJECTS + ". For a window, use `import gfx` instead",
    "OpenGL": _NO_OBJECTS + ". For 3D drawing, use `import gfx` instead",
    "numpy": _NO_OBJECTS,
    "pandas": _NO_OBJECTS,
    "scipy": _NO_OBJECTS,
    "matplotlib": _NO_OBJECTS,
    "PIL": _NO_OBJECTS,
    "requests": _NO_OBJECTS,
    "tkinter": _NO_OBJECTS,
    "os": _NO_OBJECTS,
    "collections": _NO_OBJECTS,
    "itertools": "it is built on iterators, which this subset cannot represent",
    "re": "it returns match objects and lists, which this subset cannot hold",
    "json": "it returns dicts and lists, which this subset cannot hold",
    "random": ("it could be translated, but C's rand() is not Python's "
               "Mersenne Twister, so the compiled program would print "
               "different numbers from the Python one — and agreeing with "
               "CPython exactly is how this compiler is tested"),
    "time": ("the subset has no way to express a clock both sides agree on. "
             "gfx.time() is available for frame timing"),
    "sys": ("there is no argv, stdin or environment in the subset. print() "
            "and put() are the whole of its I/O"),
}


def resolve(module_name, line):
    """Return the Module for `module_name`, or raise ModuleError saying why not."""
    if module_name in MODULES:
        return MODULES[module_name]

    root = module_name.split(".")[0]
    if root in UNSUPPORTED:
        raise ModuleError(
            "line {}: '{}' cannot be imported — {}.\n"
            "  Importable here: {}.".format(
                line, module_name, UNSUPPORTED[root], ", ".join(sorted(MODULES)))
        )
    raise ModuleError(
        "line {}: there is no module '{}' in this subset.\n"
        "  Importable here: {}.".format(
            line, module_name, ", ".join(sorted(MODULES)))
    )
