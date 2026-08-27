"""
Every AI-generated snippet passes through here before it can be spliced into
the final output. If it doesn't compile, it is rejected — full stop. This is
the enforcement point for "AI output is never trusted blindly."
"""

import re
import subprocess
import tempfile
import os

C_KEYWORDS = {
    "int", "double", "float", "char", "void", "return", "if", "else", "while",
    "for", "abs", "pow", "min", "max", "round", "printf", "sizeof",
}


def _free_identifiers(expr: str):
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr))
    return names - C_KEYWORDS


def is_valid_c_expression(expr: str) -> tuple[bool, str]:
    """Wraps `expr` in a throwaway function and asks gcc if it's syntactically
    and semantically sound. Returns (ok, gcc_stderr_if_any)."""
    idents = _free_identifiers(expr)
    decls = "\n".join(f"    double {name} = 1.0;" for name in idents)
    src = f"""
#include <math.h>
#include <stdlib.h>
double __check(void) {{
{decls}
    double __r = ({expr});
    return __r;
}}
"""
    with tempfile.NamedTemporaryFile(suffix=".c", mode="w", delete=False) as f:
        f.write(src)
        path = f.name
    try:
        result = subprocess.run(
            ["gcc", "-fsyntax-only", "-lm", path],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0, result.stderr
    finally:
        os.unlink(path)
