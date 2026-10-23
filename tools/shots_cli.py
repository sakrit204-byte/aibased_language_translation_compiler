"""Render real CLI output to PNG images for the project report.

Each shot runs the command for real and draws whatever came back. Nothing is
typed in by hand, so a screenshot cannot drift away from what the program
actually does. The theme is light with black text because the report is read
and printed in black and white.

    python -m tools.shots_cli            # all of them
    python -m tools.shots_cli --only run_basic
"""
import argparse
import io
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "report_submission", "screenshots")

FONT_PATH = r"C:\Windows\Fonts\consola.ttf"
FONT_BOLD = r"C:\Windows\Fonts\consolab.ttf"
SIZE = 15
PAD = 18
LINE_GAP = 4
MAX_COLS = 100

BG = (255, 255, 255)
FG = (24, 24, 24)
PROMPT = (0, 0, 0)
BORDER = (170, 170, 170)
BAR = (240, 240, 240)

#: Characters the console font may not have, mapped to something it does.
SAFE = {"\u2192": "->", "\u2014": "-", "\u2013": "-", "\u2026": "...",
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2713": "ok", "\u2717": "x", "\u2726": "*", "\u251c": "+",
        "\u2500": "-", "\u2502": "|", "\u2514": "+", "\u252c": "+"}


def clean(text):
    for bad, good in SAFE.items():
        text = text.replace(bad, good)
    return "".join(ch for ch in text if ch == "\n" or ch >= " ")


def wrap(line, width):
    """Hard-wrap, because a report page cannot scroll sideways."""
    if len(line) <= width:
        return [line]
    out = []
    while len(line) > width:
        out.append(line[:width])
        line = line[width:]
    out.append(line)
    return out


def run(command, cwd=ROOT, timeout=600):
    # Without this the child encodes its output in the console code page, and
    # a character cp1252 cannot represent arrives here as a replacement box.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    done = subprocess.run(command, shell=True, cwd=cwd, timeout=timeout, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return done.stdout.decode("utf-8", "replace")


def render(path, command, body, head=None, tail=None, max_lines=46):
    """Draw one terminal shot. `head`/`tail` trim a long capture honestly."""
    lines = clean(body).replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()

    if head is not None or tail is not None:
        head = head or 0
        tail = tail or 0
        if len(lines) > head + tail + 1:
            kept = lines[:head] + ["", "   ... %d lines omitted ..." %
                                   (len(lines) - head - tail), ""]
            lines = kept + (lines[-tail:] if tail else [])
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["   ... output continues ..."]

    wrapped = []
    for line in lines:
        wrapped.extend(wrap(line.rstrip(), MAX_COLS))

    font = ImageFont.truetype(FONT_PATH, SIZE)
    try:
        bold = ImageFont.truetype(FONT_BOLD, SIZE)
    except OSError:
        bold = font

    probe = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(probe)
    cw = draw.textlength("M", font=font)
    lh = SIZE + LINE_GAP

    prompt_lines = wrap("$ " + command, MAX_COLS)
    rows = len(prompt_lines) + len(wrapped)
    width = int(PAD * 2 + cw * MAX_COLS) + 2
    bar_h = 10
    height = int(PAD * 2 + lh * rows) + bar_h + 2

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width - 1, bar_h], fill=BAR)
    d.rectangle([0, 0, width - 1, height - 1], outline=BORDER)

    y = PAD + bar_h
    for line in prompt_lines:
        d.text((PAD, y), line, font=bold, fill=PROMPT)
        y += lh
    for line in wrapped:
        d.text((PAD, y), line, font=font, fill=FG)
        y += lh

    img.save(path)
    print("  %-34s %d x %d" % (os.path.basename(path), width, height))


# ----------------------------------------------------------------- the shots

def shot_run_basic():
    cmd = "python -m src.driver tests/programs/arithmetic.py --run"
    render(os.path.join(OUT, "cli_01_translate_and_run.png"), cmd, run(cmd),
           max_lines=30)


def shot_generated_c():
    run("python -m src.driver tests/programs/arithmetic.py")
    with io.open(os.path.join(ROOT, "tests", "programs", "arithmetic.c"),
                 encoding="utf-8") as f:
        text = f.read()
    render(os.path.join(OUT, "cli_02_generated_c.png"),
           "type tests\\programs\\arithmetic.c", text, head=34, tail=8)


def shot_check():
    cmd = "python -m src.driver gui.py --check"
    render(os.path.join(OUT, "cli_03_check_survey.png"), cmd, run(cmd),
           head=26, tail=6)


def shot_gate():
    cmd = "python -m src.driver tests/programs/ai/01_pythagoras.py --ai --run"
    render(os.path.join(OUT, "cli_04_ai_gate.png"), cmd, run(cmd))


def shot_gate_off():
    cmd = "python -m src.driver tests/programs/ai/01_pythagoras.py --run"
    render(os.path.join(OUT, "cli_05_gate_off_fails.png"), cmd, run(cmd))


def shot_repair():
    # tools.replay_repair is the ordinary driver; it only substitutes a saved
    # model answer when the account is out of credit, and says so when it does.
    cmd = "python -m tools.replay_repair demos/repair_me.py --repair --run"
    render(os.path.join(OUT, "cli_06_repair_accepted.png"), cmd,
           run(cmd, timeout=900), head=28, tail=18)


def shot_repair_refused():
    path = os.path.join(OUT, "_varies.py")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("import time\n\n\ndef main() -> int:\n"
                "    stamp: int = int(time.time() * 1000) % 1000\n"
                '    print(f"started at {stamp}")\n    return 0\n')
    cmd = "python -m src.driver report_submission/screenshots/_varies.py --repair"
    render(os.path.join(OUT, "cli_07_repair_refused.png"), cmd, run(cmd))
    os.remove(path)


def shot_tests():
    cmd = "python -m pytest tests/ -q"
    render(os.path.join(OUT, "cli_08_test_suite.png"), cmd,
           run(cmd, timeout=1200), head=4, tail=6)


def shot_bench():
    """The corpus score, from the saved run rather than a fresh one."""
    log = os.path.join(ROOT, "report_submission", "screenshots",
                       "_bench_summary.txt")
    if not os.path.exists(log):
        print("  (skipped bench shot: no saved summary)")
        return
    with io.open(log, encoding="utf-8") as f:
        text = f.read()
    render(os.path.join(OUT, "cli_09_repair_bench.png"),
           "python -m tools.repair_bench", text)


SHOTS = {
    "run_basic": shot_run_basic,
    "generated_c": shot_generated_c,
    "check": shot_check,
    "gate": shot_gate,
    "gate_off": shot_gate_off,
    "repair": shot_repair,
    "repair_refused": shot_repair_refused,
    "tests": shot_tests,
    "bench": shot_bench,
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    args = ap.parse_args(argv)

    os.makedirs(OUT, exist_ok=True)
    names = args.only or list(SHOTS)
    for name in names:
        if name not in SHOTS:
            print("no such shot: %s" % name)
            continue
        print("%s ..." % name)
        SHOTS[name]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
