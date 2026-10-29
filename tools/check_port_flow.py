"""Check the IDE's last-resort conversion stage, by driving the real IDE.

    python -m tools.check_port_flow

Needs a desktop. What it proves, when it prints PASS: translating a pygame
program fails, repair declines to even start because a window has no output to
compare, the pipeline falls through to the conversion stage on its own, the
question is handed over as a file because no model can be reached, and the
answer written into that file is turned into a running native executable and
reported as CONVERTED, NOT VERIFIED.

The last assertion is the one worth having. It checks the window does not use
the word "verified" about a conversion, because that is the single result in
this program that cannot be proved, and it must not be the one that looks
strongest.
"""
import ctypes
import io
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import gui  # noqa: E402

ORIGINAL = os.path.join(ROOT, "tests", "programs", "ported", "spinner_pygame.py")
ANSWER = os.path.join(ROOT, "tests", "programs", "ported", "spinner_gfx.py")

failures = []


def log(*parts):
    print(" ".join(str(p) for p in parts), flush=True)


def check(label, ok):
    log(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)


app = gui.TranslationCompilerApp()
state = {"deadline": 0}


def wait_for(predicate, label, limit, nxt):
    if not state["deadline"]:
        state["deadline"] = time.time() + limit

    def tick():
        if predicate():
            state["deadline"] = 0
            nxt()
            return
        if time.time() > state["deadline"]:
            state["deadline"] = 0
            failures.append("timed out waiting for " + label)
            log("  FAIL  timed out waiting for", label)
            finish()
            return
        app.root.after(150, tick)

    tick()


def step_1():
    log("1. translating a pygame program with AI-assist on")
    buffers = os.path.join(ROOT, "reports", "repair_buffer")
    if os.path.isdir(buffers):
        shutil.rmtree(buffers)
    with io.open(ORIGINAL, encoding="utf-8") as f:
        app.source_pane.set_text(f.read())
    app.ai_var.set(True)
    app.translate()
    wait_for(lambda: app.repair_window is not None
             and app.repair_window.pending is not None,
             "the conversion stage to hand over a question", 240, step_2)


def step_2():
    window = app.repair_window
    attempt, prompt_path, answer_path = window.pending
    log("2. the pipeline fell through to the conversion stage")
    check("the window is showing the port stage", window.stage == "port")
    check("a prompt was written", os.path.exists(prompt_path))
    if os.path.exists(prompt_path):
        with io.open(prompt_path, encoding="utf-8") as f:
            text = f.read()
        log("     prompt-%d.md (%d bytes)" % (attempt, len(text)))
        check("it is the conversion prompt, not the repair one",
              "nothing will be compared" in text)
        check("it describes the window API", "gfx.tri(" in text)
        check("it says why no comparison is possible",
              "window" in text.lower())

    log("3. writing the conversion into the buffer")
    with io.open(ANSWER, encoding="utf-8") as f:
        answer = f.read()
    with io.open(answer_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(answer)
    state["before"] = id(window.report)
    app.root.after(400, step_3)


def step_3():
    window = app.repair_window
    log("4. not pressing anything - the file should be picked up on its own")
    wait_for(lambda: id(window.report) != state["before"],
             "the conversion to be picked up and checked", 300, step_4)


def step_4():
    window = app.repair_window
    report = window.report or {}
    log("5. the verdict")
    check("it was converted", bool(report.get("ported")))
    check("it is NOT reported as verified", report.get("verified") is False)
    check("the verdict says so in words",
          "CONVERTED, NOT VERIFIED" in str(report.get("reason", "")))
    check("the C was produced", bool(report.get("c_code")))
    check("the conversion is in the right-hand pane", window.repaired is not None)
    proof = " ".join(report.get("proof", []))
    check("it lists what was established", "builds and links" in proof)
    log("     proof:")
    for item in report.get("proof", []):
        log("       +", item[:72])
    finish()


def finish():
    log("")
    log("FAILED: " + "; ".join(failures) if failures
        else "PASS - a pygame program became a running executable, and the "
             "verdict does not overstate what that proves")
    app.root.after(300, app.root.destroy)


def guard(fn):
    def wrapped(*a):
        try:
            return fn(*a)
        except Exception as e:
            failures.append("%s: %s" % (type(e).__name__, e))
            log("  FAIL ", type(e).__name__, e)
            app.root.after(200, app.root.destroy)
    return wrapped


for name in [n for n in list(globals()) if n.startswith("step_")]:
    globals()[name] = guard(globals()[name])

app.root.after(800, globals()["step_1"])
app.root.mainloop()
sys.exit(1 if failures else 0)
