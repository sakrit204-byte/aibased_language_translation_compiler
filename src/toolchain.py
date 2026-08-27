"""
C toolchain discovery.

Everything downstream of codegen (the AI validator, the e2e test harness, the
GUI's "Run C" button) needs a working C compiler. Rather than hard-coding
``gcc`` and crashing with a bare FileNotFoundError when it is missing, this
module finds whatever is actually installed and reports a clear, actionable
message when nothing is.

This matters for the AI-assist policy too: the gate is *fail-closed*. If no C
compiler exists, an AI-proposed translation cannot be validated, so it is
rejected rather than trusted. See src/ai_assist/gate.py.
"""

import os
import shutil
import subprocess
import sys

# Ordered by preference. MSVC (`cl`) is last: it needs a Developer Command
# Prompt environment and takes different flags, handled separately below.
_CANDIDATES = ("gcc", "clang", "cc", "tcc", "cl")

# Windows installers routinely leave the compiler off PATH, so check the
# standard locations before giving up and telling the user to install one.
_WINDOWS_FALLBACK_DIRS = (
    r"C:\msys64\ucrt64\bin",
    r"C:\msys64\mingw64\bin",
    r"C:\msys64\clang64\bin",
    r"C:\mingw64\bin",
    r"C:\MinGW\bin",
    r"C:\TDM-GCC-64\bin",
    r"C:\Program Files\LLVM\bin",
    r"C:\Program Files (x86)\LLVM\bin",
)

_INSTALL_HINT = {
    "win32": (
        "No C compiler found on PATH.\n"
        "Install one of:\n"
        "  winget install -e --id BrechtSanders.WinLibs.POSIX.UCRT   (gcc)\n"
        "  winget install -e --id LLVM.LLVM                          (clang)\n"
        "  choco install mingw                                       (gcc, needs choco)\n"
        "Then reopen your terminal so PATH is refreshed."
    ),
    "darwin": (
        "No C compiler found on PATH.\n"
        "Install the Xcode command line tools:  xcode-select --install"
    ),
    "linux": (
        "No C compiler found on PATH.\n"
        "Install one of:  sudo apt install gcc   |   sudo dnf install gcc"
    ),
}


class ToolchainError(Exception):
    """Raised when a C compiler is required but none is available."""


def _looks_runnable(exe: str) -> bool:
    directory = os.path.dirname(os.path.abspath(exe))
    env = dict(os.environ)
    env["PATH"] = directory + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run(
            [exe, "--version"], capture_output=True, timeout=10, env=env
        )
        return result.returncode == 0 or os.path.basename(exe).lower().startswith("cl")
    except (OSError, subprocess.SubprocessError):
        # MSVC `cl` has no --version and exits nonzero, but it does run.
        return os.path.basename(exe).lower().startswith("cl")


def find_c_compiler():
    """Return the path of the first usable C compiler, or None."""
    override = os.environ.get("CC")
    if override:
        path = shutil.which(override) or (override if os.path.isfile(override) else None)
        if path and _looks_runnable(path):
            return path
    for name in _CANDIDATES:
        path = shutil.which(name)
        if path and _looks_runnable(path):
            return path
    if sys.platform == "win32":
        for directory in _WINDOWS_FALLBACK_DIRS:
            for name in ("gcc.exe", "clang.exe", "cc.exe"):
                path = os.path.join(directory, name)
                if os.path.isfile(path) and _looks_runnable(path):
                    return path
    return None


def install_hint() -> str:
    for key, text in _INSTALL_HINT.items():
        if sys.platform.startswith(key):
            return text
    return _INSTALL_HINT["linux"]


def require_c_compiler() -> str:
    cc = find_c_compiler()
    if cc is None:
        raise ToolchainError(install_hint())
    return cc


def run_env(cc: str) -> dict:
    """Environment for invoking `cc`.

    A MinGW/MSYS2 gcc found outside PATH cannot start: it loads its own DLLs
    from the directory it lives in. Windows reports that as a bare non-zero
    exit with no diagnostics, which is a genuinely confusing failure, so the
    compiler's own directory is prepended to PATH for every invocation.
    """
    env = dict(os.environ)
    directory = os.path.dirname(os.path.abspath(cc))
    if directory:
        env["PATH"] = directory + os.pathsep + env.get("PATH", "")
    return env


def is_msvc(cc: str) -> bool:
    return os.path.basename(cc).lower() in ("cl", "cl.exe")


def exe_suffix() -> str:
    return ".exe" if sys.platform == "win32" else ""


def compile_command(cc: str, c_path: str, out_path: str) -> list:
    """Build the argv that turns one .c file into an executable.

    The generated C is deliberately self-contained (the runtime prelude is
    inlined by the emitter), so no -I include path is ever needed.
    """
    if is_msvc(cc):
        return [cc, "/nologo", "/W3", c_path, f"/Fe:{out_path}"]
    return [cc, "-std=c99", "-O2", c_path, "-lm", "-o", out_path]


def syntax_only_command(cc: str, c_path: str) -> list:
    """Build the argv that type-checks a .c file without producing output."""
    if is_msvc(cc):
        return [cc, "/nologo", "/Zs", c_path]
    return [cc, "-std=c99", "-fsyntax-only", c_path]
