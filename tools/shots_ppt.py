"""Capture the figures the slide deck needs that the report's set does not.

The report was written before the answer-file buffer, the conversion stage and
the triple cube existed, so tools/shots_gui.py does not photograph any of
them. This takes the four that the deck is built around:

    ppt_01_buffer_handoff.png   no model reached, the question becomes a file
    ppt_02_converted_verdict.png  CONVERTED, NOT VERIFIED, with its proof list
    ppt_03_triple_cube.png      the converted cube running as a native binary
    ppt_04_int64_refused.png    a wrong answer turned into a diagnostic

    python -m tools.shots_ppt

Needs a desktop. The DPI call matters: a DPI-unaware process reports
virtualised coordinates while the screen capture works in physical pixels,
which silently crops the right-hand side of every shot.
"""
import ctypes
import ctypes.wintypes
import io
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "presentation", "screenshots")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import gui  # noqa: E402  (must come after the DPI call)

ORIGINAL = os.path.join(ROOT, "tests", "programs", "ported", "triple_ursina.py")
ANSWER = os.path.join(ROOT, "demos", "rubik_triple.py")

os.makedirs(OUT, exist_ok=True)


def log(*parts):
    print(" ".join(str(p) for p in parts), flush=True)


def capture_rect(x, y, w, h, path):
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


def grab_window(widget, name, settle=1.2):
    """Photograph a toplevel by its drawn bounds, not GetWindowRect - the
    latter includes the invisible resize border and brings in a strip of
    whatever is behind the window."""
    widget.lift()
    widget.attributes("-topmost", True)
    widget.update()
    end = time.time() + settle
    while time.time() < end:
        widget.update()

    top = ctypes.windll.user32.GetAncestor(widget.winfo_id(), 2)
    rect = ctypes.wintypes.RECT()
    got = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        ctypes.wintypes.HWND(top), ctypes.wintypes.DWORD(9),
        ctypes.byref(rect), ctypes.sizeof(rect))
    if got != 0:
        ctypes.windll.user32.GetWindowRect(top, ctypes.byref(rect))
    path = os.path.join(OUT, name)
    capture_rect(rect.left, rect.top, rect.right - rect.left,
                 rect.bottom - rect.top, path)
    widget.attributes("-topmost", False)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    log("  shot %-30s (%d bytes)" % (name, size))


def grab_foreign_window(title_fragment, name, settle=2.5):
    """Photograph a window this process does not own - the compiled binary."""
    time.sleep(settle)
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND,
                        ctypes.wintypes.LPARAM)
    def each(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_fragment.lower() in buf.value.lower() and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    if not found:
        log("  no window matching %r" % title_fragment)
        return False
    hwnd = found[0]
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    rect = ctypes.wintypes.RECT()
    got = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        ctypes.wintypes.HWND(hwnd), ctypes.wintypes.DWORD(9),
        ctypes.byref(rect), ctypes.sizeof(rect))
    if got != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    path = os.path.join(OUT, name)
    capture_rect(rect.left, rect.top, rect.right - rect.left,
                 rect.bottom - rect.top, path)
    log("  shot %-30s (%d bytes)" % (name, os.path.getsize(path)))
    return True


# ------------------------------------------------------- the running binary

def shoot_triple_cube():
    """Build the converted cube and photograph the window it opens.

    This is the payoff figure of the whole deck: an ursina program, refused by
    the compiler, converted into the subset, and running here as a native
    executable with no Python underneath it.
    """
    from src.driver import link_requirements
    from src.lexer.lexer import Lexer
    from src.parser.parser import Parser
    from src.semantic.type_checker import TypeChecker
    from src.toolchain import compile_command, exe_suffix, find_c_compiler, run_env
    from src.driver import compile_source

    with io.open(ANSWER, encoding="utf-8") as f:
        source = f.read()
    c_code, _ = compile_source(source)
    checker = TypeChecker()
    checker.check(Parser(Lexer(source).tokenize()).parse_program())
    link = link_requirements(checker.profiles)

    cc = find_c_compiler()
    folder = os.path.join(OUT, "_build")
    os.makedirs(folder, exist_ok=True)
    c_path = os.path.join(folder, "cube.c")
    binary = os.path.join(folder, "cube" + exe_suffix())
    with io.open(c_path, "w", encoding="utf-8") as f:
        f.write(c_code)
    build = subprocess.run(
        compile_command(cc, c_path, binary, extra_sources=link["sources"],
                        extra_flags=link["flags"]),
        capture_output=True, text=True, env=run_env(cc), timeout=300)
    if build.returncode != 0:
        log("  the cube did not build:", (build.stderr or "")[:300])
        return
    log("  built %d bytes; starting it" % os.path.getsize(binary))

    process = subprocess.Popen([binary], env=run_env(cc))
    try:
        # The title the shim gives its window, from src/runtime/gfx.c.
        grab_foreign_window("Python ->", "ppt_03_triple_cube.png")
    finally:
        process.kill()
        process.wait(timeout=10)


# ------------------------------------------------------------- the IDE shots

app = gui.TranslationCompilerApp()

# The deck already carries eleven light-theme figures from the report, and
# a deck that alternates between dark and light screenshots reads as two
# different applications. Match the set that already exists.
if app.theme_name != "light":
    app.toggle_theme()

state = {"deadline": 0}


def wait_for(pred, label, limit, nxt):
    if not state["deadline"]:
        state["deadline"] = time.time() + limit

    def tick():
        if pred():
            state["deadline"] = 0
            nxt()
            return
        if time.time() > state["deadline"]:
            log("  TIMED OUT waiting for", label)
            app.root.after(200, app.root.destroy)
            return
        app.root.after(150, tick)
    tick()


def step_1_int64():
    log("1. a constant too large for 64 bits, refused rather than wrapped")
    app.source_pane.set_text(
        "def main() -> None:\n"
        "    # CPython prints 1267650600228229401496703205376.\n"
        "    # This used to compile and print 0.\n"
        "    print(2 ** 100)\n")
    app.ai_var.set(False)
    app.translate()
    app.root.after(2500, step_2_shoot_int64)


def step_2_shoot_int64():
    grab_window(app.root, "ppt_04_int64_refused.png")
    step_3_ursina()


def step_3_ursina():
    log("2. an ursina program: refused, then handed over as a file")
    import shutil
    buffers = os.path.join(ROOT, "reports", "repair_buffer")
    if os.path.isdir(buffers):
        shutil.rmtree(buffers)
    with io.open(ORIGINAL, encoding="utf-8") as f:
        app.source_pane.set_text(f.read())
    app.ai_var.set(True)
    app.translate()
    wait_for(lambda: app.repair_window is not None
             and app.repair_window.pending is not None,
             "the handoff", 240, step_4_shoot_handoff)


def step_4_shoot_handoff():
    grab_window(app.repair_window, "ppt_01_buffer_handoff.png")
    log("3. writing the conversion into the buffer")
    window = app.repair_window
    answer_path = window.pending[2]
    with io.open(ANSWER, encoding="utf-8") as f:
        answer = f.read()
    with io.open(answer_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(answer)
    state["before"] = id(window.report)
    wait_for(lambda: id(window.report) != state["before"],
             "the conversion to be checked", 300, step_5_shoot_verdict)


def step_5_shoot_verdict():
    grab_window(app.repair_window, "ppt_02_converted_verdict.png")
    log("4. building and photographing the converted cube")
    app.root.after(400, finish)


def finish():
    app.root.destroy()


app.root.after(900, step_1_int64)
app.root.mainloop()

# The binary's window is photographed after the IDE has gone, so nothing
# overlaps it.
shoot_triple_cube()
log("\nfigures in", OUT)
