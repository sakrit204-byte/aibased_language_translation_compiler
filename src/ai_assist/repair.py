"""
AI repair of the *source*, accepted only when the compiled result behaves
identically to the original.

The gate in gate.py answers one small question — what C expression does this
unknown call mean — and it only ever runs *inside* a successful compile. This
module covers the other case, the one that actually stops people: the program
does not compile at all. A list comprehension, an f-string, `enumerate`, a
tuple unpack, `.items()` — each is a construct the subset has no rule for, and
the deterministic core is right to refuse it.

So when the byte-for-byte translation cannot be made, the model is asked to
rewrite the *program* into the subset, and the rewrite is then put through the
whole compiler — lexer, parser, semantic analysis, codegen — exactly as a
hand-written program would be. Nothing is special-cased for it.

**What makes a repair acceptable.** Five things, in order, and all five:

  1. The original program agrees with itself. It is run **twice** before
     anything else happens, and if the two runs differ it is not repairable —
     matching one run of a program that varies says nothing about the next.
  2. The rewrite compiles. Not "looks plausible": the real pipeline accepts
     it, which means every construct in it has a deterministic C rule. If it
     does not, the new diagnostic goes back to the model and it tries again.
  3. The rewrite, run under CPython, prints exactly what the original printed.
     This is safe to do only here, *after* step 2, because compiling proves
     the program is inside a subset that cannot open a file, start a process
     or reach the network — there is no syntax for it.
  4. The generated C builds with a real C compiler.
  5. That binary, run **twice**, prints exactly what the original printed,
     both times.

"Exactly" means the raw bytes of stdout and the exit status, compared with no
text decoding and no newline translation. `subprocess.run(text=True)` turns
CRLF into LF on both sides of a comparison, which would hide a real difference
in line endings or in stdout encoding; every run here is captured as bytes.

Steps 3 and 5 together are what make the claim worth making. If the rewrite
and the compiled C both match the original, then the model preserved the
behaviour *and* the compiler translated it faithfully — and a bug in one
cannot be hidden by a matching bug in the other, because neither produced the
reference. The model is not trusted at all: it proposes, and the original
program's own output decides.

This is exact rather than approximate because programs in this subset take no
input — no `input()`, no file access, no clock, no randomness — so a program's
entire observable behaviour is its stdout and its exit status. Comparing those
is not a sample of the behaviour, it is the behaviour.

The one case where that argument does not hold is a program that prints
nothing. Matching "no output" is not evidence, so the report marks it
`vacuous` and says so instead of showing a tick.

**What the model's output never does.** It is never executed as Python, and it
never reaches the C compiler directly. It goes through the deterministic
pipeline first, and only the C that *this compiler* emitted is built and run.
That is a narrower attack surface than simplify.py has, where the model writes
the C itself.

**When repair is refused, and why that is the honest answer.** A program whose
behaviour cannot be reproduced in the subset cannot be repaired, and no amount
of rewriting changes that:

  * It opens a window, reads a file, or waits for events. Then it does not
    terminate under CPython on its own, so there is no finite output to
    preserve, and the attempt is blocked before a single token is spent.
  * It uses the clock, randomness, or anything else that varies between runs.
    Then it has no fixed output to preserve, and the two reference runs say
    so before any model call is made.
  * It needs a growable list, dynamic dispatch, or a library the generated C
    has no binding to. Then every rewrite either fails to compile or prints
    something else, and all three attempts are reported with the reason.

A refusal here is a real answer about the program, not a failure of the model.
"""

import ast
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time

from . import client

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRAMMAR_PATH = os.path.join(PROJECT_ROOT, "grammar", "subset.ebnf")

MAX_ATTEMPTS = 3

# A rewritten program is a whole file, and the model has to restate every line
# it keeps. 12k leaves room for a few hundred lines of output plus whatever a
# reasoning model spends before it starts writing.
REPAIR_TOKEN_BUDGET = 12000
REPAIR_TIMEOUT = 240

# The original is the user's own program, run to find out what it prints. A
# program that waits for events never comes back, so the wait is bounded and a
# timeout is reported as "not repairable" rather than hanging the GUI.
REFERENCE_TIMEOUT = 20
RUN_TIMEOUT = 30

# print() always ends a line, so the subset has put() for writing without one.
# CPython has no such builtin, so the reference run is given the one-line shim
# — the same shim tests/conftest.py gives the differential oracle.
REFERENCE_PREAMBLE = "import sys\ndef put(s): sys.stdout.write(s)\n"

# Names that would mean the rewrite reaches outside the program. The compiler
# rejects all of them anyway — none has a C rule, and with --ai on the gate's
# validator only accepts pure <math.h> expressions — but failing here gives a
# straight reason instead of a confusing one three stages later.
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "breakpoint",
}

_ENTRY_CALL = re.compile(r"^\s*main\s*\(\s*\)", re.MULTILINE)
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*$", re.MULTILINE)


class RepairError(Exception):
    """Raised when repair cannot even be attempted."""


# ------------------------------------------------------------ the reference

def needs_entry_call(source):
    """True when `main()` has to be appended for CPython to run the program.

    The compiler makes `main` the entry point, so a subset program defines it
    and never calls it. CPython would then define it and do nothing.
    """
    return "def main" in source and not _ENTRY_CALL.search(source)


def run_python(source, timeout=REFERENCE_TIMEOUT):
    """Run `source` under CPython and return (exit, stdout, stderr) as BYTES.

    Bytes, not text, and deliberately so. `subprocess.run(text=True)` turns
    CRLF into LF on the way in, on both sides of any comparison — so two
    programs that really do write different line endings, or different bytes
    for the same character under a different stdout encoding, would compare
    equal. Capturing raw bytes is what makes "identical" mean identical.
    """
    text = source
    if needs_entry_call(source):
        text = source.rstrip("\n") + "\n\nmain()\n"

    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(REFERENCE_PREAMBLE + text)
            path = f.name
        run = subprocess.run([sys.executable, path], capture_output=True,
                             timeout=timeout)
        return run.returncode, run.stdout, run.stderr
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def reference_run(source, timeout=REFERENCE_TIMEOUT):
    """What the original program does — established, not assumed.

    Returns a dict with `exit`, `stdout` and `stderr`, stdout and stderr as
    bytes. Raises RepairError when the program cannot serve as a reference at
    all, which is itself the answer about whether it can be repaired.

    The program is run **twice**. A program that does not agree with itself —
    one that uses the clock, or randomness, or iterates something unordered —
    cannot have a repair verified against it, because matching one of its runs
    says nothing about the next. Catching that here costs one extra run and
    removes the one case where a green tick would have been meaningless.
    """
    try:
        code, out, err = run_python(source, timeout)
    except subprocess.TimeoutExpired:
        raise RepairError(
            "the original program does not finish on its own (it was still "
            "running after %ds), so it has no finite output to preserve. A "
            "program that opens a window or waits for events cannot be "
            "repaired into this subset: there is nothing to compare a "
            "rewrite against." % timeout)

    if code != 0:
        detail = err.decode("utf-8", "replace").strip().splitlines()
        raise RepairError(
            "the original program fails under CPython itself (exit %d): %s\n"
            "There is no correct behaviour to preserve, so fix the Python "
            "first — a repair verified against a crash would only reproduce "
            "the crash." % (code, detail[-1] if detail else "no output"))

    try:
        again_code, again_out, _ = run_python(source, timeout)
    except subprocess.TimeoutExpired:
        raise RepairError("the original program finished once and then did not; "
                          "it cannot be used as a reference.")
    if (again_code, again_out) != (code, out):
        raise RepairError(
            "the original program does not print the same thing twice: %s\n"
            "Nothing can be verified against it, because matching one of its "
            "runs would say nothing about the next. A program that uses the "
            "clock, randomness, or anything else that varies has no fixed "
            "output to preserve." % first_difference(out, again_out, code,
                                                     again_code))
    return {"exit": code, "stdout": out, "stderr": err}


def build_and_capture(c_code, link=None, timeout=RUN_TIMEOUT, runs=2):
    """Build `c_code` and run the binary `runs` times, capturing raw bytes.

    Returns (build_error, [(exit, stdout, stderr), ...]). `build_error` is a
    string when the C did not build, and None otherwise.

    It runs more than once for the same reason the original does: one run of
    a program that varies proves nothing about the next.
    """
    from ..toolchain import (compile_command, exe_suffix, find_c_compiler,
                             install_hint, run_env)

    cc = find_c_compiler()
    if cc is None:
        raise RepairError(install_hint())

    link = link or {}
    with tempfile.TemporaryDirectory() as tmp:
        c_path = os.path.join(tmp, "prog.c")
        binary = os.path.join(tmp, "prog" + exe_suffix())
        with open(c_path, "w", encoding="utf-8") as f:
            f.write(c_code)
        env = run_env(cc)
        build = subprocess.run(
            compile_command(cc, c_path, binary,
                            extra_sources=link.get("sources", ()),
                            extra_flags=link.get("flags", ())),
            capture_output=True, text=True, cwd=tmp, env=env, timeout=180)
        if build.returncode != 0:
            return (build.stderr or build.stdout or "(no diagnostics)"), []

        results = []
        for _ in range(runs):
            run = subprocess.run([binary], capture_output=True, env=env,
                                 timeout=timeout)
            results.append((run.returncode, run.stdout, run.stderr))
        return None, results


# ------------------------------------------------------------------ safety

def check_candidate(source):
    """Check the model's reply before it goes near the compiler.

    Returns (ok, reason). This is a courtesy, not the safety boundary: the
    boundary is that the reply is only ever read by the compiler's own front
    end, and only the C *this compiler* emits is ever built or run.
    """
    if not source or not source.strip():
        return False, ("the model returned nothing — usually the token budget "
                       "ran out before it wrote any code")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, "it is not valid Python: line %s: %s" % (e.lineno, e.msg)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return False, ("it uses %r, which reaches outside the program; the "
                           "subset has no such builtin" % node.id)
    if not any(isinstance(n, ast.FunctionDef) and n.name == "main"
               for n in tree.body):
        return False, ("it has no top-level `def main()`, which is the entry "
                       "point the generated C needs")
    return True, ""


# ------------------------------------------------------------------ prompt

def _grammar():
    try:
        with open(GRAMMAR_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "(grammar file not found; follow the rules above)"


RULES = """\
Hard rules for your answer:
  - Reply with ONE complete Python file and nothing else: no prose, no fences,
    no explanation before or after.
  - It must print EXACTLY the same text, in the same order, as the original.
    Same spacing, same number of decimals, same line breaks. This is checked
    by running both and comparing byte for byte, so a cosmetic difference is a
    rejection.
  - Put the whole program in functions and make `def main() -> int:` the entry
    point. Do not call main() yourself; do not use `if __name__`.
  - Every function needs annotated parameters and an explicit return type.
  - No construct outside the grammar below. In particular: no f-strings, no
    `str()`, no string concatenation (`+` on str), no comprehensions, no
    tuples, no `enumerate`, no `.items()`, no `zip`, no slicing, no `append`,
    no default or keyword arguments, no `try`, no decorators, no lambdas, no
    inheritance, no chained comparison (`a < b < c`), no multiple assignment.
  - To build a line of text, use put() for the pieces and a bare print() to
    end the line. put() writes a string with no newline; it is the only way to
    compose output, because there is no string concatenation.
  - put() takes a str and nothing else. print(x) prints x AND ends the line.
    So to put a number in the middle of a line, write this helper once and
    call it - it is the idiom here, copy it as it stands:

        def write_int(n: int) -> None:
            if n < 0:
                put("-")
                n = -n
            if n >= 10:
                write_int(n // 10)
            digit: int = n % 10
            if digit == 0:
                put("0")
            elif digit == 1:
                put("1")
            elif digit == 2:
                put("2")
            elif digit == 3:
                put("3")
            elif digit == 4:
                put("4")
            elif digit == 5:
                put("5")
            elif digit == 6:
                put("6")
            elif digit == 7:
                put("7")
            elif digit == 8:
                put("8")
            else:
                put("9")

    Then `print(f"a={x} b={y}")` becomes:
        put("a=")
        write_int(x)
        put(" b=")
        write_int(y)
        print()
  - print(a, b) separates its arguments with one space and ends the line, the
    same as Python's. If the original did not print a space there, do not use
    it - build the line with put() instead.
  - Lists are fixed-size arrays of int, float or bool: `xs: list[int] = [0] * 9`
    or `xs: list[int, 9]` as a class field. No growth, no slicing, no list of
    strings.
  - A dict's keys are literals and fixed: `d: dict[str, int] = {"a": 1}`.
    Values can be assigned; keys cannot be added or removed.
  - If a value is a float, annotate it and write it as a float: `t: float = 0.0`,
    not `t: float = 0`.
"""


def _prompt(source, error, survey, reference, previous=None):
    parts = [
        "You are repairing a Python program so that a Python-to-C compiler can "
        "translate it.\n\n"
        "The compiler accepts a strict subset of Python. The program below is "
        "valid Python but uses constructs outside that subset, so translation "
        "failed. Rewrite it so it is inside the subset AND prints exactly what "
        "it prints now.\n\n",
        "--- the compiler's error ---\n%s\n\n" % (error or "(none recorded)"),
    ]
    if survey:
        parts.append("--- everything else outside the subset ---\n%s\n\n" % survey)
    if reference is not None:
        out = reference["stdout"].decode("utf-8", "replace")
        shown = out if len(out) <= 3000 else out[:3000] + "\n... (truncated)"
        parts.append(
            "--- what the original prints (this is what your answer must "
            "reproduce exactly) ---\n%s\n--- end of expected output "
            "(exit status %d) ---\n\n" % (shown, reference["exit"]))
    parts.append(RULES)
    parts.append("\n--- the subset's grammar ---\n%s\n" % _grammar())
    parts.append("\n--- the program to rewrite ---\n%s\n" % source)
    if previous:
        parts.append(
            "\n--- your previous answer was rejected ---\n%s\n"
            "Fix that and reply with the whole file again.\n" % previous)
    return "".join(parts)


def _extract_python(raw):
    """Pull a Python file out of a reply that may be fenced or prefaced."""
    text = (raw or "").strip()
    if "```" in text:
        parts = text.split("```")
        blocks = [parts[i] for i in range(1, len(parts), 2)]
        if blocks:
            block = max(blocks, key=len)
            first, _, rest = block.partition("\n")
            if first.strip().lower() in ("python", "py", "python3", ""):
                block = rest
            text = block
    text = _FENCE.sub("", text)
    lines = text.split("\n")
    # Drop any prose the model put before the first line of code.
    starts = ("import ", "from ", "def ", "class ", "#", '"""', "'''")
    while lines and not lines[0].startswith(starts):
        lines.pop(0)
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------- entry

def repair(source, error="", model=None, log_path=None, use_ai=False,
           attempts=MAX_ATTEMPTS, progress=None):
    """Try to rewrite `source` into the subset. Returns (repaired, report).

    `repaired` is the accepted program, or None when nothing was accepted —
    in which case the original is unchanged and `report` says why.

    `progress`, if given, is called with (stage, text, ok) as each step
    finishes, so a caller can show the work as it happens rather than after.
    """
    from ..driver import CompileError, compile_source_detailed

    report = {
        "accepted": False,
        "model": model or client.DEFAULT_MODEL,
        "error": error,
        "source": source,
        "repaired": None,
        "c_code": None,
        "link": None,
        "reference": None,
        "blocked": "",
        "vacuous": False,
        "checks": [],
        "attempts": [],
        "reason": "",
    }

    def say(stage, text, ok=None):
        if progress:
            progress(stage, text, ok)

    # -- 1. what does the original actually do? ----------------------------
    say("reference", "running the original under CPython to find out what it "
                     "prints…")
    try:
        reference = reference_run(source)
    except RepairError as e:
        report["blocked"] = str(e)
        report["reason"] = str(e)
        say("reference", str(e), False)
        _log(log_path, report)
        return None, report

    report["reference"] = {
        "exit": reference["exit"],
        "stdout": reference["stdout"].decode("utf-8", "replace"),
        # The bytes themselves, not just their text form: a caller that wants
        # to repeat the comparison has to compare what was compared.
        "raw": reference["stdout"],
        "lines": len(reference["stdout"].splitlines()),
        "bytes": len(reference["stdout"]),
        # Shown as evidence: two outputs with the same digest are the same
        # bytes, which a screenshot of the text cannot establish on its own.
        "sha256": hashlib.sha256(reference["stdout"]).hexdigest(),
    }
    if not reference["stdout"].strip():
        report["vacuous"] = True
    say("reference",
        "the original prints %d line(s) / %d bytes and exits %d, twice over — "
        "that is the target" % (report["reference"]["lines"],
                                report["reference"]["bytes"], reference["exit"]),
        True)

    # The whole-file survey is better grounding than one error: it names every
    # construct in the way, so the model does not fix line 9 and trip on 11.
    survey = ""
    try:
        from ..diagnose import report as survey_report
        survey = survey_report(source, "the program")
    except Exception:
        pass

    previous = None
    for attempt in range(1, attempts + 1):
        record = {"attempt": attempt}
        trace = []
        say("model", "attempt %d: asking %s for a rewrite…"
            % (attempt, report["model"]))
        started = time.time()
        try:
            raw = client.query(_prompt(source, error, survey, reference, previous),
                               model=model, max_tokens=REPAIR_TOKEN_BUDGET,
                               timeout=REPAIR_TIMEOUT, trace=trace)
        except client.AIClientError as e:
            record.update(stage="model", ok=False, reason=str(e), providers=trace)
            report["attempts"].append(record)
            say("model", str(e), False)
            break
        record["providers"] = trace
        record["seconds"] = round(time.time() - started, 1)

        candidate = _extract_python(raw)
        record["candidate"] = candidate
        record["lines"] = candidate.count("\n")

        ok, reason = check_candidate(candidate)
        if not ok:
            record.update(stage="safety", ok=False, reason=reason)
            report["attempts"].append(record)
            say("safety", reason, False)
            previous = "Rejected before it reached the compiler: %s" % reason
            continue

        # -- 2. the whole pipeline, exactly as a hand-written program ------
        say("compile", "attempt %d: lexer → parser → semantic → codegen" % attempt)
        try:
            c_code, _stats, results = compile_source_detailed(
                candidate, use_ai=use_ai, model=model, log_path=log_path)
        except CompileError as e:
            record.update(stage="compile", ok=False, reason=str(e))
            report["attempts"].append(record)
            say("compile", str(e).splitlines()[0], False)
            previous = ("The compiler rejected it:\n%s\nEverything else in the "
                        "file still has to stay inside the subset." % e)
            continue
        except Exception as e:                       # a compiler bug, not theirs
            record.update(stage="compile", ok=False,
                          reason="%s: %s" % (type(e).__name__, e))
            report["attempts"].append(record)
            say("compile", "%s: %s" % (type(e).__name__, e), False)
            previous = "The compiler raised %s: %s" % (type(e).__name__, e)
            continue
        record["c_code"] = c_code
        record["link"] = results.get("link")
        say("compile", "it compiles — %d lines of C" % c_code.count("\n"), True)

        # -- 3. three-way agreement, on raw bytes --------------------------
        #
        # Having compiled, the candidate is *proven* to be inside the subset,
        # which has no file, process, network or clock access — there is no way
        # to express one. Only then is it safe to run it under CPython, and
        # doing so is what separates "the model rewrote it faithfully" from
        # "the compiler translated it faithfully": if the two agree with the
        # original, both held.
        checks = []
        say("verify", "attempt %d: running the rewrite under CPython and "
                      "comparing it with your program" % attempt)
        try:
            py_code, py_out, _ = run_python(candidate, REFERENCE_TIMEOUT)
        except subprocess.TimeoutExpired:
            reason = "the rewrite does not terminate"
            record.update(stage="equivalence", ok=False, reason=reason)
            report["attempts"].append(record)
            say("verify", reason, False)
            previous = "Your rewrite did not finish; the original does."
            continue

        if py_out != reference["stdout"] or py_code != reference["exit"]:
            detail = first_difference(reference["stdout"], py_out,
                                      reference["exit"], py_code)
            record.update(stage="equivalence", ok=False, reason=detail)
            report["attempts"].append(record)
            say("verify", "the rewrite itself prints something else: " + detail,
                False)
            previous = ("Your rewrite does not print what the original prints, "
                        "even under CPython: %s" % detail)
            continue
        checks.append(("the rewrite under CPython", "identical"))
        say("verify", "the rewrite prints the same thing under CPython", True)

        say("build", "attempt %d: building the C and running it twice" % attempt)
        try:
            build_error, runs = build_and_capture(
                c_code, link=results.get("link"), timeout=RUN_TIMEOUT)
        except Exception as e:
            record.update(stage="build", ok=False, reason=str(e))
            report["attempts"].append(record)
            say("build", str(e), False)
            previous = "The generated C could not be built or run: %s" % e
            continue

        if build_error:
            record.update(stage="build", ok=False, reason=build_error.strip()[:400])
            report["attempts"].append(record)
            say("build", build_error.strip().splitlines()[0], False)
            previous = "The generated C failed to build:\n%s" % build_error[:600]
            continue

        record["exit"] = runs[0][0]
        record["stdout"] = runs[0][1].decode("utf-8", "replace")

        mismatch = None
        for index, (code, out, err) in enumerate(runs, 1):
            if out != reference["stdout"] or code != reference["exit"]:
                mismatch = (index, first_difference(reference["stdout"], out,
                                                    reference["exit"], code))
                break
            checks.append(("the compiled C, run %d" % index, "identical"))
        if mismatch:
            index, detail = mismatch
            reason = detail if len(runs) == 1 else "run %d: %s" % (index, detail)
            record.update(stage="equivalence", ok=False, reason=reason)
            report["attempts"].append(record)
            say("verify", reason, False)
            previous = ("It compiled, but the C printed something else: %s"
                        % detail)
            continue

        record.update(stage="verified", ok=True, reason="")
        report["attempts"].append(record)
        report["checks"] = (
            [("your program under CPython, run twice", "identical")] + checks)
        report.update(accepted=True, repaired=candidate, c_code=c_code,
                      link=results.get("link"))
        report["reason"] = (
            "verified byte for byte: %d bytes of output and exit status %d, the "
            "same from your program under CPython, from the rewrite under "
            "CPython, and from the compiled C on both of its runs"
            % (report["reference"]["bytes"], reference["exit"]))
        if report["vacuous"]:
            report["reason"] = (
                "it compiles and runs, but the original prints nothing — so "
                "matching it proves only that the repair also prints nothing, "
                "not that the behaviour survived")
        say("verify", report["reason"], not report["vacuous"])
        _log(log_path, report)
        return candidate, report

    last = report["attempts"][-1] if report["attempts"] else {}
    report["reason"] = (
        "nothing was accepted after %d attempt(s); the last one failed at the "
        "%s stage: %s" % (len(report["attempts"]) or attempts,
                          last.get("stage", "model"),
                          last.get("reason", "no candidate produced")))
    _log(log_path, report)
    return None, report


def _show(value):
    """A line of output as it should be read in a message.

    Comparison is on bytes, but `b'0:2'` in an error message is noise, so the
    text form is shown — and when the two differ only in bytes that a text
    form hides, the caller falls through to the byte-level report below.
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def first_difference(expected, actual, expected_exit, actual_exit):
    """The first place two runs disagree, named precisely enough to act on.

    Takes bytes (or text). The line comparison is for the message; the byte
    comparison below it is the one that decides, which is why a pair that
    differs only in line endings or in encoding is still reported rather than
    being silently equal.
    """
    if expected_exit != actual_exit:
        return "exit status %s instead of %s" % (actual_exit, expected_exit)

    # splitlines() for the message, because it drops the line ending and so
    # reads the way the output looks. A pair that differs only in those endings
    # therefore comes out equal here and is caught by the byte walk below.
    exp, got = expected.splitlines(), actual.splitlines()
    for i in range(max(len(exp), len(got))):
        e = exp[i] if i < len(exp) else None
        g = got[i] if i < len(got) else None
        if e != g:
            if e is None:
                return "line %d is %r, but the original stopped at line %d" % (
                    i + 1, _show(g), len(exp))
            if g is None:
                return "line %d is missing; the original has %r there" % (
                    i + 1, _show(e))
            return "line %d is %r, expected %r" % (i + 1, _show(g), _show(e))

    if expected == actual:
        return "no difference"
    # Same lines, different bytes: CRLF against LF, or two encodings of the
    # same character. Say exactly where, because nothing on screen would show
    # it.
    for i, (e, g) in enumerate(zip(expected, actual)):
        if e != g:
            return ("byte %d differs: %r where the original has %r — the text "
                    "looks the same, the bytes are not" % (i + 1, bytes([g]) if
                                                           isinstance(actual, bytes)
                                                           else g,
                                                           bytes([e]) if
                                                           isinstance(expected, bytes)
                                                           else e))
    return ("one output is %d bytes and the other %d — the shorter is a prefix "
            "of the longer" % (len(expected), len(actual)))


def _log(log_path, report):
    if not log_path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n### repair  (model: %s)\n" % report["model"])
            f.write("- compiler error: %s\n"
                    % (report["error"] or "").splitlines()[:1])
            if report["blocked"]:
                f.write("- blocked: %s\n" % report["blocked"])
            for a in report["attempts"]:
                f.write("- attempt %s: stage=%s ok=%s %s\n"
                        % (a.get("attempt"), a.get("stage"), a.get("ok"),
                           str(a.get("reason", ""))[:200]))
            f.write("- **accepted**: %s — %s\n"
                    % (report["accepted"], report["reason"]))
    except OSError:
        pass
