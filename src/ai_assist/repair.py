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

# Five rather than three because the benched failures were converging, not
# stuck: the stage that rejected an attempt kept moving forward - safety, then
# equivalence, then compile - and twice the third attempt was one letter or
# one array size from being accepted. An attempt is only spent when the one
# before it failed, so a program that lands on the first try costs the same as
# it always did.
MAX_ATTEMPTS = 5

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


# --------------------------------------------------- printing vs computing

#: A single digit is not evidence of anything: write_int() legitimately prints
#: "0" through "9", and every one of them turns up in almost any output.
SHORTCUT_MIN_DIGITS = 2

_DIGIT_RUN = re.compile(r"\d+")


def printed_constants(source):
    """Every constant that reaches a print() or put() in `source`.

    Constants nested inside an f-string or a `+` chain count — the literal
    half of `f"total = {n}"` is text the program prints, and the point of
    collecting these is to know what text the author wrote rather than
    computed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        if name not in ("print", "put"):
            continue
        for argument in node.args:
            for child in ast.walk(argument):
                if isinstance(child, ast.Constant) and not isinstance(
                        child.value, bool):
                    if isinstance(child.value, (str, int, float)):
                        found.append(str(child.value))
    return found


def shortcut_literals(candidate, original, expected):
    """Numbers the rewrite prints as literals that the original never wrote.

    This exists because of a real hole in "the output is the whole spec". For
    a program that takes no input, that spec is satisfied by a program which
    prints the answer and computes nothing: it builds, it runs, and its bytes
    match exactly. No behavioural test can tell the two apart, because there
    is no input to vary — so the check has to be on the text itself.

    Which text, though, matters. The first version of this compared whole
    printed strings and rejected `put("calling bump")` as copied, when `bump`
    is a function name the original wrote and the subset has no introspection
    to recover — hardcoding it is the *correct* translation, not a shortcut.

    What is actually diagnostic is narrower, and it rests on a property of the
    subset: **it cannot compute a string.** There is no concatenation, no
    `str()`, no formatting. So every piece of text in the output either came
    from a literal or is a number — and the only thing a rewrite can cheat at
    is printing a *number* it should have worked out. So that is what is
    checked: a run of two or more digits that the rewrite prints, that appears
    in the expected output, and that is nowhere in the original's source.

    `385` is caught. `"0"` from a digit table is not (one digit). `"bump"` is
    not (no digits). `10` out of `range(1, 11)`'s neighbourhood is not — it is
    in the original.

    The hole this leaves, stated rather than papered over: a program whose
    every computed number is a single digit could be pasted undetected. That
    is what `does_no_work` below is for.
    """
    known = set(_DIGIT_RUN.findall(original))
    suspects = []
    for literal in printed_constants(candidate):
        if literal not in expected:
            continue
        for run in _DIGIT_RUN.findall(literal):
            if len(run) < SHORTCUT_MIN_DIGITS or run in known:
                continue
            if run not in suspects:
                suspects.append(run)
    return suspects


def _computes(source):
    """True when `source` does arithmetic, compares, or loops."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(isinstance(node, (ast.BinOp, ast.AugAssign, ast.Compare,
                                 ast.For, ast.While))
               for node in ast.walk(tree))


def does_no_work(candidate, original):
    """True when the original works something out and the rewrite only prints.

    The second half of the shortcut check, and the half that does not depend
    on how many digits a number has: a lookup table has no arithmetic and no
    loop in it. A rewrite of a program that computes, which computes nothing
    itself, is a table however short its numbers are.
    """
    return _computes(original) and not _computes(candidate)


# ------------------------------------------------------------------ prompt

def _grammar():
    try:
        with open(GRAMMAR_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "(grammar file not found; follow the rules above)"


RULES = """\
What is being asked of you:
  - The OUTPUT must be identical. The CODE does not have to resemble the
    original at all. Rewrite it, do not transliterate it: change the
    algorithm, the loop shapes, the function names, the number of functions,
    the data layout - whatever makes it expressible here.
  - Do not try to reproduce a construct the subset does not have. There is no
    decorator, no class hierarchy, no comprehension, no generator, and no
    reason to simulate one. Work out what the code DOES and write that.
    A decorated function is just a function; a comprehension is just a loop;
    an iterator is just an index.
  - Keep the computation. The one thing you may not do is print the expected
    output as string literals. You have been shown what the program prints so
    you can check yourself against it, not so you can paste it back - a
    program that prints the answer instead of working it out is rejected, and
    the rejection says so.

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
  - `print(some_list)` in the original prints Python's repr of the list, which
    is an exact format: "[", the items separated by ", " (comma AND space),
    then "]". Nothing in the subset prints an array, so write this helper and
    call it - getting the brackets or the separator slightly wrong is the most
    common way a rewrite fails the output comparison:

        def write_list(a: list[int], n: int) -> None:
            put("[")
            i: int = 0
            while i < n:
                if i > 0:
                    put(", ")
                write_int(a[i])
                i += 1
            put("]")

    A list of lists nests the same way - "[", then each row written by
    write_list with ", " between the rows, then "]":

        def write_grid(m: list[int], rows: int, width: int) -> None:
            put("[")
            r: int = 0
            while r < rows:
                if r > 0:
                    put(", ")
                put("[")
                c: int = 0
                while c < width:
                    if c > 0:
                        put(", ")
                    write_int(m[r * width + c])
                    c += 1
                put("]")
                r += 1
            put("]")

    `print("label", xs)` is then: put("label "), write_list(...), print().
    Note the single space after the label, which is what print() would have
    put there.
  - Python prints a bool as "True" or "False", not as 1 or 0. If the original
    prints a bool, branch and put() the word.
  - Lists are fixed-size arrays of int, float or bool: `xs: list[int] = [0] * 9`
    or `xs: list[int, 9]` as a class field. No growth, no slicing, no list of
    strings.
  - An array PARAMETER is declared WITHOUT a size - `a: list[int]`, never
    `a: list[int, 6]`. A sized parameter accepts that one length and nothing
    else, so `list[int, 6]` cannot be given a `list[int, 4]`; an unsized one
    takes any length, and len(a) still reports the real length inside the
    function. Pass the shape as plain ints when the function needs it:

        def print_row(m: list[int], width: int, row: int) -> None:
            c: int = 0
            while c < width:
                write_int(m[row * width + c])
                c += 1

    A local array and a class field do carry their size, because there the
    size is known: `buf: list[int, 16] = [0] * 16`.
  - A function CANNOT return an array. Pass the destination array in as a
    parameter, fill it, and return how many entries you filled - an array
    argument is the caller's own storage, exactly as in Python, so writing to
    it is visible to the caller:

        def squares(out: list[int, 16], count: int) -> int:
            i: int = 0
            while i < count:
                out[i] = (i + 1) * (i + 1)
                i += 1
            return count

        def main() -> int:
            buf: list[int, 16] = [0] * 16
            used: int = squares(buf, 5)
            ...

    This is also the answer to "a list whose length is not known": declare an
    array big enough for the worst case and carry the count beside it. Loop to
    `used`, not to the array's length.
  - Integers are 64-bit. A constant that does not fit is now a compile
    error naming the value, so `2 ** 100` is refused rather than printed as
    0. A number larger than about 9.2e18 is carried as its DIGITS in an
    array, least significant first, with the arithmetic done a digit at a
    time and the carry propagated by hand:

        # 2 ** 100, one decimal digit per slot
        n: list[int, 40] = [0] * 40
        n[0] = 1
        used: int = 1
        i: int = 0
        while i < 100:
            carry: int = 0
            j: int = 0
            while j < used:
                doubled: int = n[j] * 2 + carry
                n[j] = doubled % 10
                carry = doubled // 10
                j = j + 1
            if carry > 0:
                n[used] = carry
                used = used + 1
            i = i + 1
        # then print n[used - 1] down to n[0]

  - Arrays hold numbers only - there is no `list[list[int]]` and no
    `list[str]`. A grid is one flat array with the index worked out:

        grid: list[int, 6] = [0] * 6      # 2 rows of 3
        grid[row * 3 + col] = value

  - A str cannot be indexed, iterated or taken apart, and there is no ord()
    and no chr(). `s[0]`, `for ch in s`, `s.upper()`, `s.split()` - none of
    them exist, and nothing in the subset can build a str at runtime.
    So text that the program works on is carried as NUMBERS, one per letter
    (0 for "a" through 25 for "z"), in an array, and written out with a branch
    table the same shape as write_int. Write out all 26 branches:

        def put_letter(i: int) -> None:
            if i == 0:
                put("a")
            elif i == 1:
                put("b")
            elif i == 2:
                put("c")
            ... one branch for every letter you can print ...
            else:
                put("z")

    A literal that the original program also wrote as a literal stays a
    literal - put("total: ") is fine and is not cheating. It is only the text
    the program *computes* that has to become numbers.
  - A dict's keys are literals and fixed: `d: dict[str, int] = {"a": 1}`.
    Values can be assigned; keys cannot be added or removed.
  - If a value is a float, annotate it and write it as a float: `t: float = 0.0`,
    not `t: float = 0`.
"""


def _prompt(source, error, survey, reference, previous=None):
    parts = [
        "You are reimplementing a Python program so that a Python-to-C compiler "
        "can translate it.\n\n"
        "The compiler accepts a strict subset of Python. The program below is "
        "valid Python but is written with constructs outside that subset, so "
        "translation failed. Write a program that is inside the subset and "
        "prints exactly what this one prints.\n\n"
        "Only the output has to match. The code is yours to write however the "
        "subset allows - what it looks like does not matter and is not "
        "checked.\n\n",
        "--- the compiler's error ---\n%s\n\n" % (error or "(none recorded)"),
    ]
    if survey:
        parts.append(
            "--- the constructs that are in the way ---\n"
            "These are listed so you know what to AVOID, not what to emulate. "
            "Each one is a way of writing something, and the subset has another "
            "way of writing the same thing.\n%s\n\n" % survey)
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
        if isinstance(previous, str):
            previous = [previous]
        if len(previous) == 1:
            parts.append(
                "\n--- your previous answer was rejected ---\n%s\n"
                "Fix that and reply with the whole file again.\n" % previous[0])
        else:
            lines = ["\n--- every answer you have given so far was rejected "
                     "---\n"]
            for index, item in enumerate(previous, 1):
                lines.append("Attempt %d: %s\n\n" % (index, item))
            lines.append(
                "Those are all of them. Fix the latest one WITHOUT going back "
                "to a mistake from an earlier one - each attempt was a whole "
                "file, so re-reading this list is the only way to know what "
                "you have already tried.\nReply with the whole file again.\n")
            parts.append("".join(lines))
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
           attempts=MAX_ATTEMPTS, progress=None, ask=None):
    """Try to rewrite `source` into the subset. Returns (repaired, report).

    `repaired` is the accepted program, or None when nothing was accepted —
    in which case the original is unchanged and `report` says why.

    `progress`, if given, is called with (stage, text, ok) as each step
    finishes, so a caller can show the work as it happens rather than after.

    `ask` is where the rewrite comes from: a callable taking (prompt, attempt)
    and returning Python source. It defaults to an HTTP call to OpenRouter,
    but nothing in this function cares which end of the wire the answer came
    from — see tools/repair_offline.py, which puts the prompt in a file and
    reads the answer back out of one.

    The reason that seam exists where it does is that *none* of the work this
    function is actually for happens inside `ask`. The safety check, the whole
    compiler, the shortcut check, the C build and the four compared runs all
    happen after it returns, and they are what decide the verdict. An answer
    written by hand is held to exactly the same standard as one that arrived
    over the network, because it goes through exactly the same code.
    """
    from ..driver import CompileError, compile_source_detailed

    report = {
        "accepted": False,
        # What answered. Naming a model that was never contacted would make
        # the log say the opposite of what happened.
        "model": (getattr(ask, "label", "a local answer") if ask is not None
                  else (model or client.DEFAULT_MODEL)),
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

    # Every rejection so far, not just the last one. An attempt that is only
    # told about the previous answer is free to go back to the mistake before
    # it, and with a whole-file rewrite that happens: the model fixes the line
    # it was scolded about and reverts the rest. The list is what stops the
    # attempts from cycling.
    history = []
    previous = None
    for attempt in range(1, attempts + 1):
        if previous:
            history.append(previous)
            previous = None
        record = {"attempt": attempt}
        trace = []
        if ask is None:
            say("model", "attempt %d: asking %s for a rewrite…"
                % (attempt, report["model"]))
        else:
            say("model", "attempt %d: taking the rewrite from %s"
                % (attempt, getattr(ask, "label", "a local answer")))
        started = time.time()
        prompt = _prompt(source, error, survey, reference, history)
        try:
            if ask is not None:
                raw = ask(prompt, attempt)
            else:
                raw = client.query(prompt, model=model,
                                   max_tokens=REPAIR_TOKEN_BUDGET,
                                   timeout=REPAIR_TIMEOUT, trace=trace)
        except client.AIClientError as e:
            # The model was never reached, so nothing was rejected and nothing
            # was learned about the program. That is a different outcome from
            # an answer that failed a check, and it is recorded as one: an
            # unreachable attempt is blocked, not failed. Scoring it as a
            # failure would read as "repair cannot do this" when the truth is
            # "repair was never asked".
            record.update(stage="model", ok=False, reason=str(e),
                          providers=trace, unreachable=True)
            report["attempts"].append(record)
            report["blocked"] = str(e)
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

        # -- 2b. did it compute the answer, or paste it? -------------------
        #
        # For a program with no input, "prints the right bytes" is satisfied by
        # a program that prints the right bytes and does nothing else. That
        # would pass every check below it, so it has to be caught here, on the
        # text, before any of them.
        expected_text = reference["stdout"].decode("utf-8", "replace")
        # A program that prints nothing has nothing to paste, and folding its
        # arithmetic away changes nothing anyone can observe. Checking it here
        # would only reject correct rewrites.
        pasted, idle = [], False
        if expected_text.strip():
            pasted = shortcut_literals(candidate, source, expected_text)
            idle = does_no_work(candidate, source)
        if pasted or idle:
            if pasted:
                shown = ", ".join(repr(p) for p in pasted[:4])
                reason = ("it prints the answer instead of working it out: %s "
                          "%s copied from the expected output and %s nowhere "
                          "in your program"
                          % (shown, "were" if len(pasted) > 1 else "was",
                             "are" if len(pasted) > 1 else "is"))
            else:
                shown = "(none)"
                reason = ("it does no arithmetic and no looping, and the "
                          "original does — it is a list of print statements, "
                          "not a translation")
            record.update(stage="shortcut", ok=False, reason=reason)
            report["attempts"].append(record)
            say("shortcut", reason, False)
            previous = (
                "Rejected: %s. The expected output was shown to you so you "
                "could check yourself against it, not so you could paste it "
                "back. Work the values out the way the original does."
                % reason)
            continue

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
    if last.get("unreachable"):
        # Not "could not do it" - "was never asked". The attempt count is
        # left out on purpose: quoting "after 1 attempt" out of five invites
        # the reader to wonder what happened to the other four, when the
        # answer is that there was no point making them.
        report["reason"] = (
            "no rewrite was ever produced, because the model could not be "
            "reached: %s" % last.get("reason", "the request did not complete"))
    else:
        report["reason"] = (
            "nothing was accepted after %d attempt(s); the last one failed at "
            "the %s stage: %s" % (len(report["attempts"]) or attempts,
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
