"""
AI simplification of generated C, accepted only when it is proven equivalent.

The compiler emits correct but verbose C: every runtime helper it might need,
defensive parentheses, one print call per argument. This module asks the model
to tidy that up — and then refuses the result unless it behaves identically.

**What "identical" means here, precisely.** Programs in this subset take no
input: there is no `input()`, no file access, no clock, no randomness, no
environment access, and the grammar has no way to express any of them. A
program's entire observable behaviour is therefore its standard output and its
exit status. Building both versions and comparing those two things byte for
byte is not a sample of the behaviour — it *is* the behaviour. So for the
program as written, equivalence is established, not estimated.

That claim is bounded in two honest ways, both worth stating rather than
glossing:

  * It is equivalence *for this program*, not a proof that the two C files
    agree on inputs the program never receives. For a subset with no inputs
    those are the same statement; for C in general they are not.
  * Undefined behaviour could in principle make two binaries agree today and
    diverge under a different compiler. The generated C is compiled with
    warnings enabled, which is a mitigation, not a guarantee.

The simplified C is checked for dangerous constructs **before** it is compiled
or run, because verification involves executing it.
"""

import os
import re
import subprocess
import tempfile

from . import client
from ..toolchain import compile_command, exe_suffix, find_c_compiler, run_env

MAX_ATTEMPTS = 2
SIMPLIFY_TOKEN_BUDGET = 6000
RUN_TIMEOUT = 20

# Only these headers may appear. The generated runtime needs no others, so
# anything else is a sign the model has reached for capabilities the program
# never had.
ALLOWED_INCLUDES = {"stdio.h", "stdlib.h", "string.h", "math.h", "limits.h",
                    "stdint.h", "stdbool.h", "float.h"}

# Identifiers that must not appear. Verification runs the binary, so this check
# happens first — a denylist is a blunt instrument, but combined with the
# include whitelist it covers every route out of the process that the standard
# library offers.
FORBIDDEN_IDENTIFIERS = {
    "system", "popen", "_popen", "fork", "vfork", "clone", "exec", "execl",
    "execle", "execlp", "execv", "execve", "execvp", "spawn", "_spawnl",
    "CreateProcess", "CreateProcessA", "CreateProcessW", "ShellExecute",
    "ShellExecuteA", "ShellExecuteW", "WinExec", "LoadLibrary", "LoadLibraryA",
    "dlopen", "dlsym", "mmap", "mprotect", "VirtualAlloc", "VirtualProtect",
    "fopen", "freopen", "open", "_open", "creat", "remove", "unlink", "rename",
    "rmdir", "mkdir", "_mkdir", "chdir", "chmod", "truncate", "ftruncate",
    "socket", "connect", "bind", "listen", "accept", "send", "recv",
    "sendto", "recvfrom", "gethostbyname", "getaddrinfo", "curl_easy_init",
    "getenv", "setenv", "putenv", "_putenv", "unsetenv",
    "signal", "raise", "kill", "abort", "atexit", "longjmp", "setjmp",
    "asm", "__asm", "__asm__", "syscall", "ptrace",
    "scanf", "fscanf", "gets", "fgets", "getchar", "fread", "read",
}

_INCLUDE = re.compile(r"#\s*include\s*[<\"]([^>\"]+)[>\"]")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FENCE = re.compile(r"```[a-zA-Z+]*\n?")


class SimplifyError(Exception):
    pass


# ------------------------------------------------------------------ safety

def code_only(c_code):
    """Blank out comments and string literals, leaving executable code.

    The denylist has to run over code, not prose: the compiler's own runtime
    carries the comment "this at least gets 64 bits", and scanning raw text
    flags `gets` in it. Blanking strings is equally deliberate — what matters
    is whether `system` is *called*, not what text a literal happens to hold.
    """
    out = []
    i, n = 0, len(c_code)
    while i < n:
        ch = c_code[i]
        if ch == "/" and i + 1 < n and c_code[i + 1] == "*":
            end = c_code.find("*/", i + 2)
            i = n if end < 0 else end + 2
            out.append(" ")
        elif ch == "/" and i + 1 < n and c_code[i + 1] == "/":
            end = c_code.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
        elif ch in "\"'":
            quote, j = ch, i + 1
            while j < n and c_code[j] != quote:
                j += 2 if c_code[j] == "\\" else 1
            i = min(j + 1, n)
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def check_safe(c_code):
    """Static check applied before the candidate is compiled or executed.
    Returns (ok, reason)."""
    if not c_code or not c_code.strip():
        return False, "the model returned nothing"

    code = code_only(c_code)

    for header in _INCLUDE.findall(code):
        if header.strip() not in ALLOWED_INCLUDES:
            return False, f"includes <{header}>, which the original did not need"

    if "#pragma" in code:
        return False, "contains a #pragma directive"

    found = sorted(set(_IDENT.findall(code)) & FORBIDDEN_IDENTIFIERS)
    if found:
        return False, f"uses forbidden identifier(s): {', '.join(found)}"

    if "int main" not in code:
        return False, "has no main() function"
    return True, ""


# ------------------------------------------------------------- build & run

def build_and_capture(c_code, label):
    """Compile and run `c_code`. Returns (exit_code, stdout, stderr, c_path)."""
    cc = find_c_compiler()
    if cc is None:
        raise SimplifyError("no C compiler available, so equivalence cannot be verified")

    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, f"{label}.c")
        binary = os.path.join(tmp, label + exe_suffix())
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_code)
        env = run_env(cc)
        build = subprocess.run(compile_command(cc, c_path, binary),
                               capture_output=True, text=True, cwd=tmp,
                               env=env, timeout=120)
        if build.returncode != 0:
            return None, "", (build.stderr or build.stdout or "")[:800], c_path
        run = subprocess.run([binary], capture_output=True, text=True,
                             env=env, timeout=RUN_TIMEOUT)
        return run.returncode, run.stdout, run.stderr, c_path


# ------------------------------------------------------------------ prompt

def _prompt(c_code, previous_failure=None):
    prompt = (
        "You are optimising C that was produced by a Python-to-C compiler.\n"
        "Rewrite it so it is shorter and easier to read, while computing "
        "EXACTLY the same output.\n\n"
        "Allowed:\n"
        "  - delete helper functions that are never called\n"
        "  - inline a helper that is called exactly once\n"
        "  - remove redundant parentheses and dead code\n"
        "  - merge consecutive printf calls that always run together\n"
        "  - simplify obviously constant expressions\n\n"
        "Forbidden:\n"
        "  - changing any printed value, its formatting, or the order of output\n"
        "  - changing integer or floating-point types, or the order of "
        "floating-point operations\n"
        "  - adding any #include beyond those already present\n"
        "  - any file, process, network or environment access\n\n"
        "Reply with the complete C file and nothing else. No prose, no fences.\n\n"
        "--- C file ---\n" + c_code
    )
    if previous_failure:
        prompt += (f"\n\n--- Your previous attempt was rejected ---\n"
                   f"{previous_failure}\nFix it and reply with the whole file again.")
    return prompt


def _extract_c(raw):
    """Pull a C file out of a reply that may be fenced or prefaced."""
    text = (raw or "").strip()
    if "```" in text:
        parts = text.split("```")
        # Prefer the longest fenced block: that is the file, not an aside.
        blocks = [parts[i] for i in range(1, len(parts), 2)]
        if blocks:
            text = max(blocks, key=len)
    text = _FENCE.sub("", text)
    lines = text.split("\n")
    while lines and not (lines[0].lstrip().startswith(("#", "/*", "//", "typedef",
                                                       "static", "int", "double",
                                                       "long", "void", "const"))):
        lines.pop(0)
    return "\n".join(lines).strip() + "\n"


# ------------------------------------------------------------------- entry

def simplify(c_code, model=None, log_path=None, attempts=MAX_ATTEMPTS):
    """Return (result_c, report).

    `result_c` is the simplified file when it was proven equivalent, otherwise
    the original, unchanged. `report` always explains which happened and why.
    """
    report = {"accepted": False, "attempts": [], "model": model or client.DEFAULT_MODEL,
              "original_lines": c_code.count("\n"), "simplified_lines": None,
              "reason": "", "baseline": None}

    baseline_code, baseline_out, baseline_err, _ = build_and_capture(c_code, "original")
    if baseline_code is None:
        report["reason"] = ("the original C does not build, so there is nothing to "
                            "compare against:\n" + baseline_err)
        return c_code, report
    report["baseline"] = {"exit": baseline_code, "stdout_bytes": len(baseline_out),
                          "lines": len(baseline_out.splitlines())}

    previous = None
    for attempt in range(1, attempts + 1):
        record = {"attempt": attempt}
        trace = []
        try:
            raw = client.query(_prompt(c_code, previous), model=model,
                               max_tokens=SIMPLIFY_TOKEN_BUDGET, timeout=180,
                               trace=trace)
        except client.AIClientError as e:
            record.update(ok=False, stage="model", reason=str(e), providers=trace)
            report["attempts"].append(record)
            break
        record["providers"] = trace

        candidate = _extract_c(raw)
        record["lines"] = candidate.count("\n")
        # Carried on the record so a caller can diff a rejected proposal and
        # show what the model actually got wrong, not just that it was wrong.
        record["candidate"] = candidate

        ok, reason = check_safe(candidate)
        if not ok:
            record.update(ok=False, stage="safety", reason=reason)
            report["attempts"].append(record)
            previous = f"Rejected by the safety check: {reason}"
            continue

        code, out, err, _ = build_and_capture(candidate, "simplified")
        if code is None:
            record.update(ok=False, stage="compile", reason=err[:400])
            report["attempts"].append(record)
            previous = f"It did not compile:\n{err[:600]}"
            continue

        if out != baseline_out or code != baseline_code:
            detail = _first_difference(baseline_out, out, baseline_code, code)
            record.update(ok=False, stage="equivalence", reason=detail)
            report["attempts"].append(record)
            previous = f"Its output differed from the original: {detail}"
            continue

        record.update(ok=True, stage="verified", reason="")
        report["attempts"].append(record)
        report.update(accepted=True, simplified_lines=candidate.count("\n"),
                      reason=(f"verified identical: {len(out.splitlines())} lines of "
                              f"output and exit code {code} match the original"))
        _log(log_path, report)
        return candidate, report

    last = report["attempts"][-1] if report["attempts"] else {}
    report["reason"] = (f"kept the original; last rejection was at the "
                        f"{last.get('stage', 'model')} stage: "
                        f"{last.get('reason', 'no candidate produced')}")
    _log(log_path, report)
    return c_code, report


def _first_difference(expected, actual, expected_code, actual_code):
    if expected_code != actual_code:
        return f"exit code {actual_code} instead of {expected_code}"
    exp, got = expected.splitlines(), actual.splitlines()
    for i in range(max(len(exp), len(got))):
        e = exp[i] if i < len(exp) else "<missing>"
        g = got[i] if i < len(got) else "<missing>"
        if e != g:
            return f"line {i + 1} became {g!r}, expected {e!r}"
    return "output lengths differ"


def _log(log_path, report):
    if not log_path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n### simplify  (model: {report['model']})\n")
            f.write(f"- original: {report['original_lines']} lines\n")
            for a in report["attempts"]:
                f.write(f"- attempt {a['attempt']}: stage={a.get('stage')} "
                        f"ok={a.get('ok')} {str(a.get('reason', ''))[:200]}\n")
            f.write(f"- **accepted**: {report['accepted']} — {report['reason']}\n")
    except OSError:
        pass
