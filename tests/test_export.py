"""
Tests for the runnable-folder export.

The export only earns its name if someone who has never seen this project can
open the folder and get output. So these tests do not inspect the files and
declare victory — they execute the exported bundle and compare what it prints
against what the compiler itself produces.
"""

import os
import subprocess
import sys

import pytest

from conftest import needs_c_compiler, read_program
from src.driver import compile_source
from src.export import (
    export_c, export_python, needs_math_import, runnable_python)

SOURCE = (
    "def area(side: float) -> float:\n"
    "    return side * side\n"
    "\n"
    "def main() -> int:\n"
    "    print(\"area(3) =\", area(3.0))\n"
    "    return 0\n"
)


# ------------------------------------------------------- the Python bundle

def test_exported_python_actually_runs(tmp_path):
    report = export_python(SOURCE, str(tmp_path))
    program = os.path.join(report["folder"], "program.py")

    result = subprocess.run([sys.executable, program], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "area(3) = 9.0"


def test_exported_python_gets_an_entry_point():
    """Subset programs define main() but never call it — the compiler
    synthesises C's entry point. Exported Python needs the call appended or
    running the file does nothing at all."""
    assert "main()" not in SOURCE.replace("def main()", "")
    body = runnable_python(SOURCE)
    assert '__name__ == "__main__"' in body
    assert body.rstrip().endswith("main()")


def test_entry_point_is_not_added_twice():
    already = SOURCE + "\nmain()\n"
    assert runnable_python(already).count("main()") == already.count("main()")


def test_math_import_is_added_only_when_needed():
    assert not needs_math_import(SOURCE)
    assert "from math import" not in runnable_python(SOURCE)

    uses_math = ("def f(x: float) -> float:\n    return sqrt(x)\n\n"
                 "def main() -> int:\n    print(f(4.0))\n    return 0\n")
    assert needs_math_import(uses_math)
    assert "from math import *" in runnable_python(uses_math)


def test_exported_ai_program_runs_under_cpython(tmp_path):
    """An ai/ program calls functions from `math`; without the added import the
    exported file raises NameError."""
    source = read_program(os.path.join("ai", "01_pythagoras.py"))
    report = export_python(source, str(tmp_path))
    result = subprocess.run([sys.executable,
                             os.path.join(report["folder"], "program.py")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "diagonal of 3 x 4 : 5.0" in result.stdout


# ------------------------------------------------------------ the C bundle

@needs_c_compiler
def test_exported_c_is_prebuilt_and_runs(tmp_path):
    c_code, _ = compile_source(SOURCE, use_ai=False)
    report = export_c(c_code, str(tmp_path))

    assert report["binary"], report.get("build_error")
    binary = os.path.join(report["folder"], report["binary"])
    assert os.path.exists(binary)

    result = subprocess.run([binary], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert result.stdout.strip() == "area(3) = 9.0"


@needs_c_compiler
def test_exported_c_matches_the_exported_python(tmp_path):
    """The whole point of the project, demonstrated through the export: both
    folders print the same thing."""
    source = read_program(os.path.join("ai", "05_quadratic.py"))
    c_code, _ = compile_source(source, use_ai=True,
                               cache_path=os.path.join("reports", "ai_cache.json"))

    py_report = export_python(source, str(tmp_path))
    c_report = export_c(c_code, str(tmp_path))

    py_out = subprocess.run(
        [sys.executable, os.path.join(py_report["folder"], "program.py")],
        capture_output=True, text=True, timeout=60)
    c_out = subprocess.run(
        [os.path.join(c_report["folder"], c_report["binary"])],
        capture_output=True, text=True, timeout=60)

    assert py_out.returncode == 0, py_out.stderr
    assert c_out.returncode == 0, c_out.stderr
    assert c_out.stdout == py_out.stdout


# ------------------------------------------------------------- the bundles

@pytest.mark.parametrize("launcher", ["run.bat", "run.sh", "README.txt"])
def test_python_bundle_is_complete(tmp_path, launcher):
    report = export_python(SOURCE, str(tmp_path))
    assert launcher in report["files"]


@pytest.mark.parametrize("launcher", ["run.bat", "run.sh", "README.txt", "program.c"])
def test_c_bundle_is_complete(tmp_path, launcher):
    c_code, _ = compile_source(SOURCE, use_ai=False)
    report = export_c(c_code, str(tmp_path), build=False)
    assert launcher in report["files"]


def test_windows_launcher_runs_the_binary_from_the_folder(tmp_path):
    """cmd does not search the current directory, so a bare `program.exe` is
    'not recognized'. The launcher must use a relative path."""
    c_code, _ = compile_source(SOURCE, use_ai=False)
    report = export_c(c_code, str(tmp_path), build=False)
    with open(os.path.join(report["folder"], "run.bat"), encoding="utf-8") as f:
        bat = f.read()
    assert ".\\program" in bat
    assert "pause" in bat, "the window must stay open so output can be read"


def test_exports_never_overwrite_an_earlier_one(tmp_path):
    first = export_python(SOURCE, str(tmp_path), "bundle")
    second = export_python(SOURCE, str(tmp_path), "bundle")
    assert first["folder"] != second["folder"]
    assert os.path.exists(first["folder"]) and os.path.exists(second["folder"])


def test_exported_python_gets_a_put_shim(tmp_path):
    """put() is a compiler builtin with no CPython equivalent. Without a shim
    the exported file raises NameError at the first character it draws."""
    source = ("def main() -> int:\n"
              '    put("ab")\n'
              '    put("\\n")\n'
              "    return 0\n")
    report = export_python(source, str(tmp_path))
    result = subprocess.run([sys.executable,
                             os.path.join(report["folder"], "program.py")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ab\n"


def test_put_shim_is_added_only_when_put_is_used():
    from src.export import runnable_python, uses_builtin

    plain = "def main() -> int:\n    print(1)\n    return 0\n"
    assert not uses_builtin(plain, "put")
    assert "def put" not in runnable_python(plain)

    draws = 'def main() -> int:\n    put("x")\n    return 0\n'
    assert uses_builtin(draws, "put")
    assert "def put" in runnable_python(draws)


def test_mentioning_put_in_a_comment_does_not_add_the_shim():
    """Detection walks the parsed program, not the text."""
    from src.export import uses_builtin

    source = ("# this program does not call put\n"
              'def main() -> int:\n    print("put")\n    return 0\n')
    assert not uses_builtin(source, "put")
