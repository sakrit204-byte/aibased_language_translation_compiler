"""
Every AI-proposed translation passes through here before it can be spliced into
the output. It is checked twice, and rejected if either check fails:

1. **Structurally** — the candidate must be a pure expression built only from
   the call's own arguments, numeric literals, operators, and an allow-list of
   `<math.h>` functions. This is not decoration: `-fsyntax-only` happily accepts
   `system("rm -rf /")`, so "it compiles" is not on its own a safe criterion for
   pasting a language model's output into a program.

2. **By the C compiler** — the candidate is wrapped in a function and compiled.
   If there is no C compiler available, validation *fails*; it does not pass by
   default. An unvalidated translation is never accepted.

Candidates are templates over placeholders `a0`, `a1`, ... rather than over the
concrete argument text, so one verified translation covers every call site with
the same arity — and can be cached.
"""

import os
import re
import subprocess
import tempfile

from ..toolchain import find_c_compiler, run_env, syntax_only_command

# Pure, side-effect-free functions from <math.h> and <stdlib.h>. Anything not
# on this list (or a placeholder) is rejected outright.
ALLOWED_FUNCTIONS = {
    "sqrt", "cbrt", "hypot", "fabs", "labs", "llabs",
    "floor", "ceil", "trunc", "round", "nearbyint", "rint",
    "fmod", "remainder", "pow", "exp", "exp2", "expm1",
    "log", "log2", "log10", "log1p",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "fmax", "fmin", "fdim", "fma", "copysign",
    "erf", "erfc", "tgamma", "lgamma", "ldexp", "frexp", "modf",
    "isnan", "isinf", "isfinite", "signbit",
}

ALLOWED_KEYWORDS = {"double", "float", "int", "long", "unsigned", "py_int", "py_float"}
ALLOWED_CONSTANTS = {"M_PI", "M_E", "M_SQRT2", "M_LN2", "M_LN10", "NAN", "INFINITY"}

# Characters that cannot appear in a pure expression. Notably `;` (statement
# sequencing), `"` (string literals -> arbitrary syscall arguments), and the
# preprocessor escape. `?` and `:` stay legal so a conditional expression like
# `(a0 > a1 ? a0 : a1)` is accepted — it is still side-effect free.
FORBIDDEN_CHARS = set(';{}[]"\'#\\@$`')

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PLACEHOLDER = re.compile(r"^a(\d+)$")
# A bare `=` that is not part of ==, !=, <=, >=
_ASSIGNMENT = re.compile(r"(?<![=!<>])=(?!=)")
_POINTER_TYPE = re.compile(r"\*\s*\)")
# A `*` or `&` in prefix position (start of the expression, or right after an
# opening paren, comma or operator) is unary: a dereference or address-of.
_DEREFERENCE = re.compile(r"(^|[(,+\-*/%<>=!&|?:])\s*\*")
_ADDRESS_OF = re.compile(r"(^|[(,+\-*/%<>=|?:])\s*&")


def check_structure(template: str, arity: int):
    """Static safety check. Returns (ok, reason)."""
    if not template or not template.strip():
        return False, "empty candidate"
    if len(template) > 400:
        return False, "candidate is implausibly long for a single expression"

    bad = sorted(set(template) & FORBIDDEN_CHARS)
    if bad:
        return False, f"contains forbidden character(s): {' '.join(bad)}"

    if _ASSIGNMENT.search(template):
        return False, "contains an assignment; only a pure expression is allowed"
    if "->" in template or re.search(r"\.\s*[A-Za-z_]", template):
        return False, "contains member access"
    # `(int)a0` is a harmless cast, but `*(int*)a0` reinterprets an argument as
    # a pointer and dereferences it. Casts are allowed; pointers are not.
    if _POINTER_TYPE.search(template):
        return False, "contains a pointer type"
    if _DEREFERENCE.search(template):
        return False, "contains a pointer dereference"
    if _ADDRESS_OF.search(template):
        return False, "contains an address-of operator"
    if template.count("(") != template.count(")"):
        return False, "unbalanced parentheses"

    for name in set(_IDENT.findall(template)):
        m = _PLACEHOLDER.match(name)
        if m:
            if int(m.group(1)) >= arity:
                return False, f"references placeholder {name} but the call has {arity} argument(s)"
            continue
        if name in ALLOWED_FUNCTIONS or name in ALLOWED_KEYWORDS or name in ALLOWED_CONSTANTS:
            continue
        return False, (
            f"references {name!r}, which is not an argument and not on the allow-list of "
            f"pure <math.h> functions"
        )

    used = {int(m.group(1)) for m in (_PLACEHOLDER.match(n) for n in _IDENT.findall(template)) if m}
    missing = set(range(arity)) - used
    if missing:
        return False, (
            f"ignores argument(s) {', '.join('a' + str(i) for i in sorted(missing))}"
        )
    return True, ""


def _probe_source(template: str, arity: int) -> str:
    params = ", ".join(f"double a{i}" for i in range(arity)) or "void"
    return (
        "#define _USE_MATH_DEFINES 1\n"
        "#include <math.h>\n"
        "#include <stdlib.h>\n"
        "#ifndef M_PI\n#define M_PI 3.14159265358979323846\n#endif\n"
        "#ifndef M_E\n#define M_E 2.7182818284590452354\n#endif\n"
        "typedef long long py_int;\n"
        "typedef double py_float;\n"
        f"double __ai_check({params})\n"
        "{\n"
        f"    return (double)({template});\n"
        "}\n"
    )


def check_compiles(template: str, arity: int):
    """Ask a real C compiler whether the candidate is valid. Returns (ok, detail)."""
    cc = find_c_compiler()
    if cc is None:
        return False, (
            "no C compiler available, so the candidate cannot be validated — "
            "AI output is never accepted unvalidated"
        )
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
            f.write(_probe_source(template, arity))
            path = f.name
        result = subprocess.run(
            syntax_only_command(cc, path), capture_output=True, text=True, timeout=20,
            env=run_env(cc),
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or result.stdout).strip()[:600]
    except subprocess.TimeoutExpired:
        return False, "the C compiler timed out validating the candidate"
    except OSError as e:
        return False, f"could not run the C compiler: {e}"
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def validate(template: str, arity: int):
    """Run both checks. Returns (ok, reason) — reason is '' when ok."""
    ok, reason = check_structure(template, arity)
    if not ok:
        return False, f"rejected by the safety check: {reason}"
    ok, detail = check_compiles(template, arity)
    if not ok:
        return False, f"rejected by the C compiler: {detail}"
    return True, ""
