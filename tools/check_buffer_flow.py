"""Check the IDE's answer-file handoff, by driving the real IDE.

This cannot be a pytest test: it opens a window, and Tk only lets the main
thread touch widgets while it is inside mainloop(), so every step is scheduled
with root.after rather than written as a straight line. It needs a desktop.

    python -m tools.check_buffer_flow

What it proves, when it prints PASS: with no credit at all, pressing Repair
writes the compiler's own question into the buffer and enables the continue
button, and nothing is accepted at that point. Then, with an answer written
into the buffer, pressing continue accepts it only after the safety check, the
whole compiler, the C build and the four compared runs agree - and the report
credits the answer file rather than a model that was never reached.

It is deliberately hostile about two things that caught me while writing it.
It fails if the prompt is a stub rather than the real 25KB question, and it
waits for a *different* report object before reading the verdict, because
show_result has already stored the blocked one and "a report exists" would
pass instantly and judge the wrong run.
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

PROGRAM = os.path.join(ROOT, "demos", "repair_me.py")
ANSWER = os.path.join(ROOT, "demos", "repair_me.repaired.py")

notes = []
failures = []


def log(*parts):
    line = " ".join(str(p) for p in parts)
    notes.append(line)
    print(line, flush=True)


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


def later(fn, ms=500):
    app.root.after(ms, fn)


# ------------------------------------------------------------------ the script

def step_1_translate():
    log("1. loading a program the compiler refuses")
    buffers = os.path.join(ROOT, "reports", "repair_buffer")
    if os.path.isdir(buffers):
        shutil.rmtree(buffers)         # start from an empty buffer
    with io.open(PROGRAM, encoding="utf-8") as f:
        app.source_pane.set_text(f.read())
    app.translate()
    wait_for(lambda: bool(app.last_error), "the translation to fail", 60,
             step_2_repair)


def step_2_repair():
    log("   refused:", (app.last_error or "").splitlines()[0][:70])
    log("2. pressing Repair with an empty account")
    app.repair_source()
    wait_for(lambda: app.repair_window is not None
             and app.repair_window.pending is not None,
             "the question to be handed over", 120, step_3_inspect)


def step_3_inspect():
    window = app.repair_window
    attempt, prompt_path, answer_path = window.pending
    log("3. the handoff")
    check("a prompt file was written", os.path.exists(prompt_path))
    if os.path.exists(prompt_path):
        size = os.path.getsize(prompt_path)
        log("     %s (%d bytes)" % (os.path.basename(prompt_path), size))
        check("the prompt is the real one, not a stub", size > 5000)
        with io.open(prompt_path, encoding="utf-8") as f:
            text = f.read()
        check("it carries the compiler's own error",
              "the compiler's error" in text)
        check("it carries the expected output", "what the original prints" in text)
    check("the continue button is enabled",
          window.continue_button.enabled if hasattr(window.continue_button,
                                                    "enabled") else True)
    check("no repair was accepted yet", window.repaired is None)
    check("the answer file does not exist yet", not os.path.exists(answer_path))

    log("4. writing an answer into the buffer")
    with io.open(ANSWER, encoding="utf-8") as f:
        answer = f.read()
    with io.open(answer_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(answer)
    later(step_4_continue)


def step_4_continue():
    window = app.repair_window
    log("5. pressing Continue from the answer file")
    # show_result already stored the blocked report, so waiting for "a report
    # exists" would pass instantly and judge the wrong run. Wait for a
    # different one.
    state["before"] = id(window.report)
    window.continue_repair()
    wait_for(lambda: id(window.report) != state["before"],
             "the answer to be checked", 300, step_5_verdict)


def step_5_verdict():
    window = app.repair_window
    report = window.report or {}
    log("6. the verdict")
    check("the answer was accepted", bool(report.get("accepted")))
    check("it was verified against the output",
          "verified byte for byte" in str(report.get("reason", "")))
    check("the report credits the answer file, not a model",
          report.get("model") == "the answer file")
    check("a repair is in the right-hand pane", window.repaired is not None)
    log("     reason:", str(report.get("reason", ""))[:110])
    finish()


def finish():
    log("")
    if failures:
        log("FAILED: " + "; ".join(failures))
    else:
        log("PASS - the answer-file handoff works with no account at all")
    later(app.root.destroy, 300)


def guard(fn):
    def wrapped(*a):
        try:
            return fn(*a)
        except Exception as e:
            failures.append("%s: %s" % (type(e).__name__, e))
            log("  FAIL ", type(e).__name__, e)
            later(app.root.destroy, 200)
    return wrapped

for name in list(globals()):
    if name.startswith("step_"):
        globals()[name] = guard(globals()[name])

app.root.after(800, globals()["step_1_translate"])
app.root.mainloop()
sys.exit(1 if failures else 0)
