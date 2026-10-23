"""Capture the IDE for the project report.

Drives the real application through a real mainloop and photographs the real
window, so every figure in the report is the program as it runs rather than a
mock-up. Tk only lets the main thread touch widgets while it is inside
mainloop(), so each step is scheduled with root.after rather than written as a
straight line of code.

    python -m tools.shots_gui

The DPI call matters: a DPI-unaware process reports virtualised coordinates
while the screen capture works in physical pixels, which silently crops the
right-hand side of every shot.
"""
import ctypes
import ctypes.wintypes
import io
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "report_submission", "screenshots")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import gui  # noqa: E402  (must come after the DPI call)
from src.ai_assist import client as ai_client  # noqa: E402

#: A rewrite this project's model produced earlier, kept so the repair figure
#: can still be captured when the API account has no credit left. Only the
#: model's answer is replayed: the compile, the four runs and the byte
#: comparison in the captured window are all performed live, which is what the
#: figure is there to show. The fallback announces itself in the log.
REPLAY_SOURCE = os.path.join(ROOT, "demos", "repair_me.repaired.py")
_real_query = ai_client.query


def query_with_fallback(prompt, **kwargs):
    try:
        return _real_query(prompt, **kwargs)
    except ai_client.AIClientError as e:
        if "credit" not in str(e).lower() or not os.path.exists(REPLAY_SOURCE):
            raise
        log("  !! live model call refused (%s)" % e)
        log("  !! replaying the saved rewrite; verification still runs live")
        with io.open(REPLAY_SOURCE, encoding="utf-8") as f:
            return f.read()


ai_client.query = query_with_fallback

LOG = os.path.join(OUT, "_gui_capture_log.txt")
os.makedirs(OUT, exist_ok=True)
io.open(LOG, "w", encoding="utf-8").close()


def log(*parts):
    line = " ".join(str(p) for p in parts)
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


app = gui.TranslationCompilerApp()
# The report is read and printed in black and white, where the dark theme turns
# every figure into a solid block of toner. The IDE ships both, so the figures
# use the light one.
if app.theme_name != "light":
    app.toggle_theme()
app.root.geometry("1500x900+30+20")
errors = []


def record(exc, value, tb):
    errors.append("".join(traceback.format_exception(exc, value, tb)))
    log("!! callback exception:\n" + errors[-1])


app.root.report_callback_exception = record


def grab(widget, name, settle=0.9, region=None):
    """Photograph the window `widget` belongs to.

    `region`, when given, is a child widget; only its rectangle is captured.
    A figure of one panel is far easier to read on a printed page than the
    whole window scaled down to fit the text width.
    """
    try:
        app.root.update_idletasks()
        widget.lift()
        widget.attributes("-topmost", True)
        app.root.update()
        end = time.time() + settle
        while time.time() < end:
            app.root.update()
        user32 = ctypes.windll.user32
        top = user32.GetAncestor(widget.winfo_id(), 2)
        rect = ctypes.wintypes.RECT()
        if region is not None:
            path = os.path.join(OUT, name)
            x, y = region.winfo_rootx(), region.winfo_rooty()
            w, h = region.winfo_width(), region.winfo_height()
            _capture(x, y, w, h, path)
            widget.attributes("-topmost", False)
            log("  shot %-32s %d x %d  (%d bytes)"
                % (name, w, h, os.path.getsize(path)))
            return
        # GetWindowRect includes the invisible resize border, so a shot taken
        # from it carries a strip of whatever is behind the window. The DWM
        # frame bounds are what is actually drawn.
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        got = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(top),
            ctypes.wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect), ctypes.sizeof(rect))
        if got != 0:
            user32.GetWindowRect(top, ctypes.byref(rect))
        x, y = rect.left, rect.top
        w, h = rect.right - rect.left, rect.bottom - rect.top
        path = os.path.join(OUT, name)
        script_text = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing\n"
            "$b = New-Object System.Drawing.Bitmap %d,%d\n"
            "$g = [System.Drawing.Graphics]::FromImage($b)\n"
            "$g.CopyFromScreen(%d,%d,0,0,$b.Size)\n"
            '$b.Save("%s", [System.Drawing.Imaging.ImageFormat]::Png)\n'
            % (w, h, x, y, path.replace("\\", "\\\\")))
        script = os.path.join(OUT, "_shot.ps1")
        with io.open(script, "w", encoding="utf-8") as f:
            f.write(script_text)
        done = subprocess.run(
            [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
             "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            capture_output=True, text=True)
        if done.returncode:
            log("  powershell failed:", done.stderr[:300])
        widget.attributes("-topmost", False)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        log("  shot %-32s %d x %d  (%d bytes)" % (name, w, h, size))
    except Exception:
        log("  grab failed:\n" + traceback.format_exc())


def _capture(x, y, w, h, path):
    """Photograph a screen rectangle through PowerShell."""
    script_text = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing\n"
        "$b = New-Object System.Drawing.Bitmap %d,%d\n"
        "$g = [System.Drawing.Graphics]::FromImage($b)\n"
        "$g.CopyFromScreen(%d,%d,0,0,$b.Size)\n"
        '$b.Save("%s", [System.Drawing.Imaging.ImageFormat]::Png)\n'
        % (w, h, x, y, path.replace("\\", "\\\\")))
    script = os.path.join(OUT, "_shot.ps1")
    with io.open(script, "w", encoding="utf-8") as f:
        f.write(script_text)
    done = subprocess.run(
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
         "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
        capture_output=True, text=True)
    if done.returncode:
        log("  powershell failed:", done.stderr[:300])


def split_at(y):
    """Move the sash between the editors and the lower panel."""
    try:
        app.split.sash_place(0, 1, y)
        app.root.update_idletasks()
    except Exception:
        log("  could not move the sash\n" + traceback.format_exc())


def read(path):
    with io.open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


state = {"deadline": 0}


def wait_for(predicate, label, limit, nxt):
    """Poll inside the mainloop until `predicate` holds."""
    if not state["deadline"]:
        state["deadline"] = time.time() + limit

    def tick():
        if predicate():
            state["deadline"] = 0
            nxt()
            return
        if time.time() > state["deadline"]:
            state["deadline"] = 0
            log("  TIMED OUT waiting for", label)
            finish()
            return
        app.root.after(100, tick)

    tick()


def later(fn, ms=600):
    app.root.after(ms, fn)


# --------------------------------------------------------------- the script

def step_01_translate_good():
    log("1. a program the deterministic core handles alone")
    app.ai_var.set(False)
    app.ai_switch.refresh()
    app.source_pane.set_text(read("tests/programs/classes.py"))
    app.translate()
    wait_for(lambda: not app.busy and app.pipeline is not None,
             "the translation", 60, step_02_ide)


def step_02_ide():
    app.show_tab("pipeline")
    later(lambda: (grab(app.root, "gui_01_ide_overview.png"), step_03_stages()))


STAGES = [("lexer", "gui_02_stage_lexer.png"),
          ("parser", "gui_03_stage_parser.png"),
          ("semantic", "gui_04_stage_semantic.png"),
          ("codegen", "gui_05_stage_codegen.png")]


def panel():
    """The lower panel, which is what the stage figures are actually about."""
    return app.pipeline_frame.master


def step_03_stages(index=0):
    if index >= len(STAGES):
        step_04_run_c()
        return
    if index == 0:
        # Give the panel most of the height: at the default split it shows
        # four or five lines, which is too few to be worth a figure.
        split_at(150)
    key, name = STAGES[index]
    log("2.%d pipeline stage: %s" % (index + 1, key))
    try:
        app.show_stage(key)
    except Exception:
        log("  could not open stage", key, "\n" + traceback.format_exc())
    later(lambda: (grab(app.root, name, region=panel()),
                   step_03_stages(index + 1)), 500)


def step_04_run_c():
    log("3. running the generated C from the IDE")
    app.run_c()
    wait_for(lambda: not app.busy, "the C run", 90,
             lambda: later(lambda: (grab(app.root, "gui_06_terminal_run.png",
                                         region=panel()),
                                    step_05_gate())))


def step_05_gate():
    log("4. the AI gate, on a program that needs an unknown call")
    app.ai_var.set(True)
    app.ai_switch.refresh()
    app.source_pane.set_text(read("tests/programs/ai/01_pythagoras.py"))
    app.translate()
    wait_for(lambda: not app.busy and app.pipeline is not None,
             "the gated translation", 180, step_06_gate_tab)


def step_06_gate_tab():
    app.show_tab("ai")
    later(lambda: (grab(app.root, "gui_07_ai_gate_report.png", region=panel()),
                   step_07_failure()))


def step_07_failure():
    log("5. a refused translation, AI off, with the whole-file survey")
    # Back to a balanced split: this figure is about the highlighted source
    # line as much as about the survey underneath it.
    split_at(420)
    app.ai_var.set(False)
    app.ai_switch.refresh()
    app.source_pane.set_text(read("demos/repair_me.py"))
    app.show_tab("pipeline")
    app.translate()
    wait_for(lambda: not app.busy, "the failure", 60,
             lambda: later(lambda: (grab(app.root, "gui_08_failed_survey.png"),
                                    step_08_repair())))


def step_08_repair():
    log("6. the same program with AI on: the repair window opens by itself")
    app.ai_var.set(True)
    app.ai_switch.refresh()
    app.source_pane.set_text(read("demos/repair_me.py"))
    app.translate()
    wait_for(lambda: app.repair_window is not None, "the repair window", 60,
             step_09_wait_verdict)


def step_09_wait_verdict():
    window = app.repair_window
    window.geometry("1460x920+20+10")
    log("   waiting for the verdict (one model call)")
    later(lambda: wait_for(lambda: not app.busy, "the verdict", 600,
                           step_10_repair_shot), 1500)


def step_10_repair_shot():
    window = app.repair_window
    log("   verdict:", window.verdict_var.get())
    log("   accepted:", bool(window.repaired))
    later(lambda: (grab(window, "gui_09_repair_comparison.png"),
                   step_11_run_both()), 800)


def step_11_run_both():
    window = app.repair_window
    if not window.repaired:
        log("   nothing accepted, skipping the run-both shot")
        finish()
        return
    log("7. Run both and compare, inside the window that claims they agree")
    window.run_both()
    wait_for(lambda: not window._running, "both runs", 240,
             lambda: later(lambda: (grab(window, "gui_10_run_both.png"),
                                    step_12_apply()), 800))


def step_12_apply():
    window = app.repair_window
    log("8. Use this repair, then an ordinary translation of the rewrite")
    window.apply_repair()
    wait_for(lambda: not app.busy and app.pipeline is not None,
             "the re-translation", 90,
             lambda: later(lambda: (grab(app.root, "gui_11_after_apply.png"),
                                    finish()), 700))


def finish():
    log("callback exceptions:", len(errors))
    app.root.after(400, app.root.destroy)


#: Capturing the repair figures needs a live model call, so they can fail on a
#: dropped connection while the rest of the run was fine. `--from-repair`
#: retakes just those without redoing the eight that already came out.
START = (step_08_repair if "--from-repair" in sys.argv else step_01_translate_good)

app.root.after(900, START)
app.root.mainloop()
log("done")
