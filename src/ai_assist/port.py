"""The last stage: convert a program that cannot be verified by its output.

Every other stage in this pipeline refuses to produce a result it cannot
prove. That is the right default and it is why the project can make the claims
it makes. But there is a class of program where the default leaves nothing at
all: a program whose behaviour is a window.

For those, "the bytes are identical" has nothing to work with. A rewrite that
printed nothing would be accepted while proving nothing; one that did the work
and showed it would be refused for not matching an empty target. Repair is
therefore blocked before it starts, which is honest and also useless - the
program stays untranslated and the AI's contribution stays undemonstrated.

This stage runs after that, and only after it. It asks for a conversion into
the subset, using `gfx` for the window, and then establishes everything that
*can* be established about the answer:

    1. it is valid Python and declares main()        (the safety check)
    2. the whole deterministic compiler accepts it   (lexer to codegen)
    3. the C it produced builds and links            (with the gfx shim)
    4. the binary starts and stays up                (a few seconds, then killed)

What it does NOT establish is that the converted program behaves like the
original. Nothing here compares them, because there is nothing to compare. So
this stage never reports "verified" - it reports PORTED, and it carries the
list of what was actually checked so the distinction cannot be lost by the
time it reaches a reader. A verdict that overstated itself would be worse than
no verdict, which is the whole reason the other stages are as strict as they
are.

Read it as: the AI wrote a program, this compiler compiled it, and the result
is a native executable that runs. For a pygame or tkinter program that is a
real and visible result. It is not a proof of equivalence and is not presented
as one.
"""
import io
import os
import subprocess
import sys
import tempfile
import time

from . import client
from ..toolchain import install_hint
from .repair import (MAX_ATTEMPTS, REPAIR_TIMEOUT, REPAIR_TOKEN_BUDGET,
                     _extract_python, _grammar, check_candidate,
                     opens_a_window)

#: How long a started binary has to stay up before it counts as started. A
#: windowed program never exits on its own, so the pass condition is "still
#: running", and three seconds is long enough for a crash on startup - a
#: missing GL context, a bad shader, an immediate segfault - to have happened.
SMOKE_SECONDS = 3


def gfx_api():
    """The window API as the subset actually exposes it.

    Generated from the module table rather than written out here, so the
    prompt cannot drift away from what the compiler will accept.
    """
    from ..semantic import modules

    lines = []
    for name in sorted(modules.GFX.names()):
        member = modules.GFX.get(name)
        if not getattr(member, "is_function", False):
            lines.append("  gfx.%s" % name)
            continue
        arity = min(member.arities) if member.arities else 0
        args = ", ".join("a%d" % i for i in range(arity))
        lines.append("  gfx.%s(%s) -> %s" % (name, args, member.return_type))
    return "\n".join(lines)


PORT_RULES = """\
What is being asked of you:
  - This program's behaviour cannot be captured as output that could be
    compared - the section above says why. So nothing will be compared, and
    what is wanted is the closest working equivalent the subset can express.
  - Reply with ONE complete Python file and nothing else: no prose, no fences,
    no explanation before or after.
  - Define `def main() -> int:` and do NOT call it. The generated C calls it.
  - Draw with `gfx`, which is the only window this subset has. It is an API of
    numbers: there is no object to hold, no surface, no sprite, no rect.
  - Keep the program's shape and purpose. If it is a cube, show a cube; if it
    reacts to keys, read the keys. Drop what cannot be expressed rather than
    faking it, and leave the rest working.
  - Everything in the subset still applies: fixed-size arrays, no string
    building, no classes with inheritance, no objects from libraries. The
    grammar below is the authority.

The window API available to you:

%s

A loop that draws looks like this. It is copied from a program in this
repository that compiles and runs, so follow its shape:

    import gfx

    def main() -> int:
        if gfx.open(900, 700) == 0:
            return 1
        while gfx.running() != 0:
            gfx.poll()
            # read gfx.key_left() and friends here, and move whatever moves
            gfx.clear(0.09, 0.10, 0.13)
            gfx.perspective(45.0, 0.1, 100.0)
            gfx.camera(eye_x, eye_y, eye_z, 0.0, 0.0, 0.0)
            # then, per triangle: gfx.color(r, g, b) and gfx.tri(nine floats)
            gfx.present()
        gfx.close()
        return 0

Note the arities above and use them exactly: clear takes three floats,
camera takes six, perspective three, tri nine. A quad is two triangles.
"""


def _prompt(source, error, reason, previous=None):
    parts = [
        "You are converting a Python program so that a Python-to-C compiler "
        "can translate it.\n\nThe compiler accepts a strict subset of Python. "
        "This program is outside that subset, and - unlike most - its "
        "behaviour cannot be checked by comparing output, because it draws a "
        "window rather than printing text.\n",
        "--- why the usual verification does not apply here ---\n%s\n" % reason,
        "--- the compiler's error ---\n%s\n" % (error or "(none recorded)"),
        PORT_RULES % gfx_api(),
        "--- the grammar of the subset (the authority) ---\n%s\n" % _grammar(),
    ]
    if previous:
        parts.append("--- your previous answer was not usable ---\n%s\n"
                     "Fix that and reply with the whole file again.\n" % previous)
    parts.append("--- the program to convert ---\n%s" % source)
    return "\n".join(parts)


def _build(c_code, link, cc):
    """Build the C to a real binary. Returns (binary_dir, binary, error)."""
    from ..toolchain import compile_command, exe_suffix, run_env

    link = link or {}
    folder = tempfile.mkdtemp(prefix="port-")
    c_path = os.path.join(folder, "prog.c")
    binary = os.path.join(folder, "prog" + exe_suffix())
    with io.open(c_path, "w", encoding="utf-8") as f:
        f.write(c_code)
    build = subprocess.run(
        compile_command(cc, c_path, binary,
                        extra_sources=link.get("sources", ()),
                        extra_flags=link.get("flags", ())),
        capture_output=True, text=True, cwd=folder, env=run_env(cc),
        timeout=300)
    if build.returncode != 0:
        return folder, None, (build.stderr or build.stdout or "(no diagnostics)")
    return folder, binary, None


def smoke_test(binary, cc, seconds=SMOKE_SECONDS):
    """Start the binary and see whether it survives.

    A windowed program does not exit on its own, so "still running after a few
    seconds" is the pass. An immediate non-zero exit is the fail, and its
    stderr is the diagnostic. A program that exits 0 quickly also passes: it
    ran to completion.

    This opens a window for a moment. That is the cost of checking that the
    thing actually starts, and checking is the only reason this stage can say
    anything at all.
    """
    from ..toolchain import run_env

    started = time.time()
    process = subprocess.Popen([binary], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=run_env(cc))
    while time.time() - started < seconds:
        if process.poll() is not None:
            out, err = process.communicate()
            if process.returncode == 0:
                return True, ("it ran to completion in %.1fs and exited 0"
                              % (time.time() - started))
            detail = (err or b"").decode("utf-8", "replace").strip()
            return False, ("it exited %d almost immediately%s"
                           % (process.returncode,
                              (": " + detail.splitlines()[0]) if detail else ""))
        time.sleep(0.1)

    process.kill()
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    return True, ("it started and was still running after %ds, so it was "
                  "stopped - which is what a program with a window does"
                  % seconds)


def port(source, error="", model=None, use_ai=False, attempts=MAX_ATTEMPTS,
         progress=None, ask=None, reason="", smoke=True):
    """Convert `source` into the subset without claiming equivalence.

    Returns (converted, report). `report["ported"]` says whether a conversion
    got all the way to a running binary; `report["verified"]` is always False,
    and `report["proof"]` lists what was actually established.
    """
    from ..driver import CompileError, compile_source_detailed
    from ..toolchain import find_c_compiler

    report = {
        "ported": False,
        # Never true. Kept in the report so that anything reading it has to
        # see the word and the value together.
        "verified": False,
        "model": (getattr(ask, "label", "a local answer") if ask is not None
                  else (model or client.DEFAULT_MODEL)),
        "source": source,
        "error": error,
        "reason": "",
        "why_unverifiable": reason or (
            "this program's behaviour is a window, so there is no output to "
            "compare a conversion against"),
        "converted": None,
        "c_code": None,
        "link": None,
        "proof": [],
        "attempts": [],
    }

    def say(stage, text, ok=None):
        if progress:
            progress(stage, text, ok)

    cc = find_c_compiler()
    if cc is None:
        report["reason"] = install_hint()
        say("build", report["reason"], False)
        return None, report

    say("survey", "no output to compare, so this is a conversion and will not "
                  "be reported as verified")

    previous = None
    for attempt in range(1, attempts + 1):
        record = {"attempt": attempt}
        if ask is None:
            say("model", "attempt %d: asking %s to convert it" % (attempt, report["model"]))
        else:
            say("model", "attempt %d: taking the conversion from %s"
                % (attempt, getattr(ask, "label", "a local answer")))

        prompt = _prompt(source, error, report["why_unverifiable"], previous)
        try:
            raw = ask(prompt, attempt) if ask is not None else client.query(
                prompt, model=model, max_tokens=REPAIR_TOKEN_BUDGET,
                timeout=REPAIR_TIMEOUT)
        except client.AIClientError as e:
            record.update(stage="model", ok=False, reason=str(e),
                          unreachable=True, prompt=prompt)
            report["attempts"].append(record)
            report["blocked"] = str(e)
            report["reason"] = ("no conversion was ever produced, because the "
                                "model could not be reached: %s" % e)
            say("model", str(e), False)
            return None, report

        candidate = _extract_python(raw)
        record["candidate"] = candidate
        record["lines"] = candidate.count("\n")

        ok, why = check_candidate(candidate)
        if not ok:
            record.update(stage="safety", ok=False, reason=why)
            report["attempts"].append(record)
            say("safety", why, False)
            previous = "Rejected before the compiler saw it: %s" % why
            continue

        if opens_a_window(candidate):
            why = ("it still imports a windowing library. The subset has no "
                   "way to hold an object from one; draw with `gfx` instead.")
            record.update(stage="safety", ok=False, reason=why)
            report["attempts"].append(record)
            say("safety", why, False)
            previous = "Rejected before the compiler saw it: %s" % why
            continue

        say("compile", "attempt %d: lexer -> parser -> semantic -> codegen" % attempt)
        try:
            c_code, stats, results = compile_source_detailed(
                candidate, use_ai=use_ai, model=model)
        except CompileError as e:
            record.update(stage="compile", ok=False, reason=str(e))
            report["attempts"].append(record)
            say("compile", str(e), False)
            previous = "The compiler rejected it:\n%s" % e
            continue
        except Exception as e:                      # pragma: no cover - rare
            record.update(stage="compile", ok=False, reason=str(e))
            report["attempts"].append(record)
            say("compile", str(e), False)
            previous = "The compiler rejected it:\n%s" % e
            continue

        link = results.get("link") or {"sources": [], "flags": []}
        say("compile", "it compiles - %d lines of C" % c_code.count("\n"), True)

        say("build", "attempt %d: building the C and linking it" % attempt)
        folder, binary, build_error = _build(c_code, link, cc)
        if build_error:
            record.update(stage="build", ok=False, reason=build_error)
            report["attempts"].append(record)
            say("build", build_error.splitlines()[0], False)
            previous = ("The C it produced did not build:\n%s"
                        % "\n".join(build_error.splitlines()[:12]))
            continue
        size = os.path.getsize(binary)
        say("build", "it builds and links - %d bytes of executable" % size, True)

        if smoke:
            say("start", "attempt %d: starting it to see that it runs" % attempt)
            alive, detail = smoke_test(binary, cc)
            if not alive:
                record.update(stage="start", ok=False, reason=detail)
                report["attempts"].append(record)
                say("start", detail, False)
                previous = ("It built, but the program failed as soon as it "
                            "started: %s" % detail)
                continue
            say("start", detail, True)
        else:
            detail = "not started (asked not to)"

        record.update(stage="ported", ok=True)
        report["attempts"].append(record)
        report["ported"] = True
        report["converted"] = candidate
        report["c_code"] = c_code
        report["link"] = link
        report["proof"] = [
            "it is valid Python and declares main()",
            "the whole compiler accepts it, with no stage special-cased",
            "the C it produced builds and links (%d bytes of executable)" % size,
            detail,
        ]
        report["reason"] = (
            "CONVERTED, NOT VERIFIED. The compiler accepted the conversion and "
            "the C it produced builds, links and runs. Nothing here compares "
            "it with your original, because a window has no output to compare "
            "- so this says the conversion is a working program, not that it "
            "behaves the same.")
        say("ported", report["reason"], True)
        return candidate, report

    last = report["attempts"][-1] if report["attempts"] else {}
    report["reason"] = (
        "no conversion got all the way through after %d attempt(s); the last "
        "stopped at the %s stage: %s"
        % (len(report["attempts"]) or attempts, last.get("stage", "model"),
           last.get("reason", "nothing was produced")))
    return None, report
