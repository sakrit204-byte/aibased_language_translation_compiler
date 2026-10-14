"""
Export a translation as a self-contained, runnable folder.

Both halves of the IDE can be extracted: the Python source on one side and the
generated C on the other. Each export produces a folder that someone with no
knowledge of this project can open and run by double-clicking one file, which
is the point — a demonstration is more convincing when the examiner runs it
themselves.

Each bundle contains:

    README.txt      what it is and how to run it
    program.py      or program.c
    run.bat         Windows: build if needed, run, keep the window open
    run.sh          macOS and Linux: the same
    program.exe     C exports only, pre-built when a compiler was available

Two details that make the difference between "a folder of files" and something
that actually runs:

  * **The Python export gets an entry point.** Programs in this subset define
    `main()` but never call it, because the compiler synthesises C's entry
    point. Exported Python needs the call appended or double-clicking it does
    nothing at all.
  * **The C export bakes in the compiler path it was built with.** A MinGW
    toolchain that is not on PATH cannot even load its own DLLs, so the script
    puts its directory on PATH before invoking it.
"""

import os
import subprocess

from .toolchain import compile_command, exe_suffix, find_c_compiler, run_env

PY_README = """\
{title}

WHAT THIS IS
    A program written in the Python subset accepted by the hybrid
    Python-to-C compiler. This is the ORIGINAL source, before translation.

HOW TO RUN IT
    Windows      double-click run.bat
    macOS/Linux  ./run.sh       (you may need: chmod +x run.sh)
    Any system   python program.py

WHAT YOU NEED
    Python 3.9 or newer. Nothing else - no third-party packages.
{extra}
FILES
    program.py   the source
    run.bat      Windows launcher
    run.sh       macOS/Linux launcher
"""

C_README = """\
{title}

WHAT THIS IS
    C generated from a Python program by the hybrid Python-to-C compiler.
    It is self-contained: the runtime it needs is inlined at the top of the
    file, so there is no header to copy and no -I flag to pass.

HOW TO RUN IT
    Windows      double-click run.bat
    macOS/Linux  ./run.sh       (you may need: chmod +x run.sh)
    By hand      cc program.c -lm -o program && ./program

WHAT YOU NEED
    Any C compiler - gcc, clang, cc, tcc or MSVC. The maths library (-lm)
    is linked because the generated code uses <math.h>.
{extra}
FILES
    program.c    the generated C
    run.bat      Windows launcher (builds if needed, then runs)
    run.sh       macOS/Linux launcher
{binary_note}"""


def _unique_dir(parent, name):
    """Return a folder path that does not exist yet, so an export never
    silently overwrites an earlier one."""
    candidate = os.path.join(parent, name)
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(parent, f"{name}_{suffix}")
        suffix += 1
    return candidate


def _write(path, text, executable=False):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if executable:
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass


def needs_math_import(source):
    """True if the program calls functions that live in Python's `math`.

    Determined by running the compiler's own semantic phase rather than by
    guessing from a name list: whatever it could not resolve is exactly what
    the export has to import for CPython to run the same file.
    """
    try:
        from .lexer.lexer import Lexer
        from .parser.parser import Parser
        from .semantic.type_checker import TypeChecker

        ast = Parser(Lexer(source).tokenize()).parse_program()
        checker = TypeChecker(ai_enabled=True)
        checker.check(ast)
        return bool(checker.ai_calls)
    except Exception:
        return False


def uses_builtin(source, name):
    """True if `source` calls the named builtin. Determined by walking the
    parsed program rather than searching the text, so a mention in a comment
    or a string does not count."""
    try:
        from .lexer.lexer import Lexer
        from .parser import ast_nodes as A
        from .parser.parser import Parser

        program = Parser(Lexer(source).tokenize()).parse_program()

        def walk(node):
            import dataclasses
            if dataclasses.is_dataclass(node) and not isinstance(node, type):
                if isinstance(node, A.Call) and node.name == name:
                    return True
                return any(walk(getattr(node, f.name))
                           for f in dataclasses.fields(node))
            if isinstance(node, (list, tuple)):
                return any(walk(item) for item in node)
            return False

        return walk(program)
    except Exception:
        return False


def runnable_python(source):
    """The source plus whatever it needs to run directly under CPython.

    Three things are missing from a subset program on its own, and without all
    three `python program.py` either crashes or silently does nothing:

      * the maths functions, which the subset has no import syntax for;
      * `put`, which is a compiler builtin with no CPython equivalent;
      * a call to main(), because the compiler synthesises C's entry point and
        the source itself only ever defines functions.
    """
    parts = []
    if needs_math_import(source):
        parts.append("# These live in Python's math module. The compiler has no\n"
                     "# import syntax, so the translator asks the model for a C\n"
                     "# equivalent; CPython just needs the import.\n"
                     "from math import *\n\n")
    if uses_builtin(source, "put"):
        parts.append("# put() is a compiler builtin: it writes a string with no\n"
                     "# newline and no separator, which print() cannot do.\n"
                     "import sys\n\n\n"
                     "def put(s):\n"
                     "    sys.stdout.write(s)\n\n\n")
    parts.append(source.rstrip("\n") + "\n")
    if "def main(" in source and "\nmain()" not in source:
        parts.append('\n\nif __name__ == "__main__":\n    main()\n')
    return "".join(parts)


# --------------------------------------------------------------- launchers

def _python_bat():
    return (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo === Running program.py ===\r\n"
        "echo.\r\n"
        "where py >nul 2>&1 && (py program.py) || (python program.py)\r\n"
        "echo.\r\n"
        "echo === Finished ===\r\n"
        "pause\r\n"
    )


def _python_sh():
    return (
        "#!/bin/sh\n"
        'cd "$(dirname "$0")" || exit 1\n'
        "echo '=== Running program.py ==='\n"
        "echo\n"
        "if command -v python3 >/dev/null 2>&1; then python3 program.py\n"
        "else python program.py; fi\n"
        "echo\n"
        "echo '=== Finished ==='\n"
    )


def _c_bat(compiler_path, binary):
    """Windows launcher. Reuses the pre-built binary when it is there, and
    otherwise builds with the compiler this export was made with."""
    directory = os.path.dirname(compiler_path) if compiler_path else ""
    lines = [
        "@echo off\r\n",
        "setlocal\r\n",
        "cd /d \"%~dp0\"\r\n",
    ]
    if directory:
        # A MinGW compiler off PATH cannot load its own DLLs and exits with no
        # diagnostics at all, which is a baffling failure to debug.
        lines.append(f'set "PATH={directory};%PATH%"\r\n')
    lines += [
        f"if exist {binary} goto run\r\n",
        "echo === Building program.c ===\r\n",
        'set "CC="\r\n',
        "for %%c in (gcc clang cc tcc) do (\r\n",
        '  if not defined CC (where %%c >nul 2>&1 && set "CC=%%c")\r\n',
        ")\r\n",
        'if not defined CC (\r\n',
        "  echo No C compiler found on PATH.\r\n",
        "  echo Install gcc or clang, then run this again.\r\n",
        "  pause\r\n"
        "  exit /b 1\r\n",
        ")\r\n",
        f'%CC% -std=c99 -O2 program.c -lm -o {binary}\r\n',
        "if errorlevel 1 (\r\n",
        "  echo Build failed.\r\n",
        "  pause\r\n",
        "  exit /b 1\r\n",
        ")\r\n",
        ":run\r\n",
        "echo === Running ===\r\n",
        "echo.\r\n",
        # The `.\` matters: cmd does not search the current directory for
        # executables, so a bare `program.exe` is "not recognized".
        f".\\{binary}\r\n",
        "echo.\r\n",
        "echo === Finished ===\r\n",
        "pause\r\n",
    ]
    return "".join(lines)


def _c_sh():
    return (
        "#!/bin/sh\n"
        'cd "$(dirname "$0")" || exit 1\n'
        "if [ ! -x ./program ]; then\n"
        "  echo '=== Building program.c ==='\n"
        "  CC=\"\"\n"
        "  for c in cc gcc clang tcc; do\n"
        "    command -v \"$c\" >/dev/null 2>&1 && { CC=\"$c\"; break; }\n"
        "  done\n"
        '  [ -z "$CC" ] && { echo "No C compiler found."; exit 1; }\n'
        '  "$CC" -std=c99 -O2 program.c -lm -o program || exit 1\n'
        "fi\n"
        "echo '=== Running ==='\n"
        "echo\n"
        "./program\n"
        "echo\n"
        "echo '=== Finished ==='\n"
    )


# ----------------------------------------------------------------- exports

def export_python(source, destination, folder_name="python_export"):
    """Write a runnable Python bundle. Returns a report dict."""
    target = _unique_dir(destination, folder_name)
    os.makedirs(target)

    body = runnable_python(source)
    _write(os.path.join(target, "program.py"), body)
    _write(os.path.join(target, "run.bat"), _python_bat())
    _write(os.path.join(target, "run.sh"), _python_sh(), executable=True)

    extra = ""
    if needs_math_import(source):
        extra = ("\n    This program calls functions from Python's math module,\n"
                 "    so `from math import *` has been added at the top.\n")
    _write(os.path.join(target, "README.txt"),
           PY_README.format(title="PYTHON SOURCE (before translation)", extra=extra))

    return {"folder": target, "kind": "Python", "binary": None,
            "files": sorted(os.listdir(target))}


def export_c(source_c, destination, folder_name="c_export", build=True):
    """Write a runnable C bundle, pre-building the binary when possible."""
    target = _unique_dir(destination, folder_name)
    os.makedirs(target)

    _write(os.path.join(target, "program.c"), source_c)

    compiler = find_c_compiler()
    binary_name = "program" + exe_suffix()
    built = False
    build_error = ""

    if build and compiler:
        binary_path = os.path.join(target, binary_name)
        c_path = os.path.join(target, "program.c")
        try:
            result = subprocess.run(
                compile_command(compiler, c_path, binary_path),
                capture_output=True, text=True, cwd=target,
                env=run_env(compiler), timeout=120)
            built = result.returncode == 0 and os.path.exists(binary_path)
            if not built:
                build_error = (result.stderr or result.stdout or "")[:400]
        except (OSError, subprocess.SubprocessError) as e:
            build_error = str(e)
        # MSVC leaves an object file behind; it is noise in an export folder.
        for junk in ("program.obj", "program.o"):
            path = os.path.join(target, junk)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    _write(os.path.join(target, "run.bat"), _c_bat(compiler, binary_name))
    _write(os.path.join(target, "run.sh"), _c_sh(), executable=True)

    if built:
        extra = ("\n    A pre-built binary is included, so run.bat runs it\n"
                 "    immediately without needing a compiler at all.\n")
        binary_note = f"    {binary_name}  pre-built executable\n"
    else:
        extra = ("\n    No pre-built binary is included; run.bat will compile\n"
                 "    program.c the first time you run it.\n")
        binary_note = ""
    _write(os.path.join(target, "README.txt"),
           C_README.format(title="GENERATED C (after translation)",
                           extra=extra, binary_note=binary_note))

    return {"folder": target, "kind": "C", "binary": binary_name if built else None,
            "build_error": build_error, "files": sorted(os.listdir(target))}
