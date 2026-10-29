"""Check what Translate launches, by driving the real IDE.

Two behaviours, both of which live in the window and neither of which a pytest
test can see:

  1. A successful translation runs the C it produced, not the Python it came
     from. The compiled program is the point of the exercise; running the
     Python proves nothing about the translation.
  2. A program that opens a window is refused without being launched, so
     pressing Translate on a tkinter app does not start that app.

    python -m tools.check_translate_runs_c

Needs a desktop. Turn the auto-run off in settings (auto_run_c) and behaviour
1 stops, which will make this fail - that is the setting working, not a bug.
"""
import ctypes
import io
import os
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

CONSOLE = os.path.join(ROOT, "tests", "programs", "loop_sum.py")
WINDOWED = os.path.join(ROOT, "tests", "programs", "windowed",
                        "tk_window.py")


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


def terminal_text():
    try:
        return app.terminal.get("1.0", "end")
    except Exception:
        for name in ("terminal_pane", "term", "console"):
            widget = getattr(app, name, None)
            if widget is not None:
                try:
                    return widget.get("1.0", "end")
                except Exception:
                    pass
        return ""


def step_1():
    log("1. translating a console program (auto-run should launch the C)")
    with io.open(CONSOLE, encoding="utf-8") as f:
        app.source_pane.set_text(f.read())
    app.translate()
    wait_for(lambda: "[exit" in terminal_text(),
             "the C to run by itself", 180, step_2)


def step_2():
    text = terminal_text()
    log("2. what the terminal shows")
    check("the C was compiled and run without being asked", "[exit" in text)
    check("it was the C, not the Python",
          "$ python source.py" not in text)
    tail = [l for l in text.splitlines() if l.strip()][-4:]
    for line in tail:
        log("     |", line[:74])
    step_3()


def step_3():
    log("3. translating the tkinter program (it must not be launched)")
    with io.open(WINDOWED, encoding="utf-8") as f:
        app.source_pane.set_text(f.read())
    app.ai_var.set(True)                   # so repair is offered automatically
    app.translate()
    wait_for(lambda: app.repair_window is not None
             and app.repair_window.report is not None,
             "the repair to reach a verdict", 180, step_4)


def step_4():
    report = app.repair_window.report or {}
    log("4. the verdict on the windowed program")
    check("it was blocked", bool(report.get("blocked")))
    check("the reason names the toolkit", "tkinter" in str(report.get("blocked")))
    check("the original was never run",
          "not run at all" in str(report.get("blocked")))
    check("no reference output was taken", not report.get("reference"))
    log("     reason:", str(report.get("blocked"))[:100])
    finish()


def finish():
    log("")
    log("FAILED: " + "; ".join(failures) if failures
        else "PASS - the C runs on translate, and a windowed program is not run")
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
