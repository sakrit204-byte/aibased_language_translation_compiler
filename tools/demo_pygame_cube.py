"""Drive the real IDE through the pygame cube, end to end, and leave it open.

    python -m tools.demo_pygame_cube

Loads tests/programs/ported/triple_pygame.py, translates it with AI assist on,
lets the pipeline fall through to the conversion stage, answers the question it
writes with tests/programs/ported/triple_pygame_gfx.py, and waits for the
verdict. If the conversion lands, it builds the C and starts the program, then
leaves both the IDE and the running cube on screen rather than tearing them
down - the point of this one is to be looked at.

Progress is written to presentation/screenshots/_demo_log.txt as it happens, so
the run can be followed from outside the window.

Needs a desktop. Close the windows yourself when you are done with them.
"""
import ctypes
import io
import os
import shutil
import subprocess
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

ORIGINAL = os.path.join(ROOT, "tests", "programs", "ported", "triple_pygame.py")
ANSWER = os.path.join(ROOT, "tests", "programs", "ported", "triple_pygame_gfx.py")
LOG = os.path.join(ROOT, "presentation", "screenshots", "_demo_log.txt")

os.makedirs(os.path.dirname(LOG), exist_ok=True)
with io.open(LOG, "w", encoding="utf-8") as f:
    f.write("")

failures = []
state = {"deadline": 0, "process": None}


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check(label, ok):
    log(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)


app = gui.TranslationCompilerApp()


def wait_for(pred, label, limit, nxt):
    if not state["deadline"]:
        state["deadline"] = time.time() + limit

    def tick():
        if pred():
            state["deadline"] = 0
            nxt()
            return
        if time.time() > state["deadline"]:
            state["deadline"] = 0
            failures.append("timed out waiting for " + label)
            log("  FAIL  timed out waiting for", label)
            finish()
            return
        app.root.after(200, tick)

    tick()


def step_1_translate():
    log("1. loading the pygame program and translating it with AI assist on")
    buffers = os.path.join(ROOT, "reports", "repair_buffer")
    if os.path.isdir(buffers):
        shutil.rmtree(buffers)          # start from an empty buffer
    with io.open(ORIGINAL, encoding="utf-8") as f:
        app.source_pane.set_text(f.read())
    app.ai_var.set(True)
    app.translate()
    wait_for(lambda: app.repair_window is not None
             and app.repair_window.pending is not None,
             "the question to be handed over", 300, step_2_answer)


def step_2_answer():
    window = app.repair_window
    attempt, prompt_path, answer_path = window.pending
    log("2. the pipeline fell through and asked for an answer")
    check("the window is on the conversion stage", window.stage == "port")
    check("a prompt was written", os.path.exists(prompt_path))
    log("     " + prompt_path)
    log("     " + answer_path)

    log("3. writing the conversion into the buffer, then touching nothing")
    with io.open(ANSWER, encoding="utf-8") as f:
        answer = f.read()
    with io.open(answer_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(answer)
    state["before"] = id(window.report)
    wait_for(lambda: id(window.report) != state["before"],
             "the answer to be picked up and checked", 420, step_3_verdict)


def step_3_verdict():
    window = app.repair_window
    report = window.report or {}
    log("4. the verdict")
    check("it was converted", bool(report.get("ported")))
    check("it is not reported as verified", report.get("verified") is False)
    check("the C was produced", bool(report.get("c_code")))
    for item in report.get("proof", []):
        log("     + " + item[:86])
    if not report.get("ported"):
        log("     reason:", str(report.get("reason", ""))[:160])
        finish()
        return
    app.root.after(400, step_4_run)


def step_4_run():
    """Build the conversion and start it, so the cube is on screen too."""
    from src.driver import compile_source, link_requirements
    from src.lexer.lexer import Lexer
    from src.parser.parser import Parser
    from src.semantic.type_checker import TypeChecker
    from src.toolchain import (compile_command, exe_suffix, find_c_compiler,
                               run_env)

    log("5. building the conversion and starting it")
    with io.open(ANSWER, encoding="utf-8") as f:
        source = f.read()
    c_code, _ = compile_source(source)
    checker = TypeChecker()
    checker.check(Parser(Lexer(source).tokenize()).parse_program())
    link = link_requirements(checker.profiles)

    cc = find_c_compiler()
    folder = os.path.join(ROOT, "reports", "demo_build")
    os.makedirs(folder, exist_ok=True)
    c_path = os.path.join(folder, "cube.c")
    binary = os.path.join(folder, "cube" + exe_suffix())
    with io.open(c_path, "w", encoding="utf-8") as f:
        f.write(c_code)
    build = subprocess.run(
        compile_command(cc, c_path, binary, extra_sources=link["sources"],
                        extra_flags=link["flags"]),
        capture_output=True, text=True, env=run_env(cc), timeout=300)
    check("the C builds", build.returncode == 0)
    if build.returncode != 0:
        log("     " + (build.stderr or "")[:300])
        finish()
        return
    log("     %d bytes of executable" % os.path.getsize(binary))

    state["process"] = subprocess.Popen([binary], env=run_env(cc))
    app.root.after(2500, step_5_still_up)


def step_5_still_up():
    process = state["process"]
    alive = process is not None and process.poll() is None
    check("the program is still running two seconds later", alive)
    if not alive and process is not None:
        log("     it exited with", process.returncode)
    finish()


def finish():
    log("")
    if failures:
        log("FAILED: " + "; ".join(failures))
        log("DONE-FAIL")
        app.root.after(500, app.root.destroy)
        return
    log("PASS - the pygame program is converted, built and running")
    log("")
    log("Both windows are left open on purpose:")
    log("  - the IDE, showing CONVERTED, NOT VERIFIED and the two programs")
    log("  - the cube itself, running as a native binary")
    log("Controls: 1 2 3 select, U D F B R L turn, SHIFT reverse,")
    log("          S scramble, X reset, SPACE scramble all, ESC quit.")
    log("Close them yourself when you are done.")
    log("DONE-PASS")


def guard(fn):
    def wrapped(*a):
        try:
            return fn(*a)
        except Exception as e:
            import traceback
            failures.append("%s: %s" % (type(e).__name__, e))
            log("  FAIL ", type(e).__name__, e)
            log(traceback.format_exc()[:900])
            log("DONE-FAIL")
            app.root.after(500, app.root.destroy)
    return wrapped


for name in [n for n in list(globals()) if n.startswith("step_")]:
    globals()[name] = guard(globals()[name])

app.root.after(900, globals()["step_1_translate"])
app.root.mainloop()
