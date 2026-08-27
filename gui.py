"""
Translation Compiler — an IDE for the hybrid Python-subset -> C compiler.

    py gui.py        (from the translation-compiler/ directory)

Layout, roughly ChatGPT's: a quiet sidebar of past translations on the left, a
single accent colour, generous spacing, and a light/dark toggle that follows
the same palette.

  * left  — history of translations, click to restore
  * top   — file actions, model picker, AI-assist switch, Translate
  * main  — Python source (editable) beside generated C (read-only)
  * bottom— Pipeline / Terminal / AI tabs

No third-party dependencies: standard-library tkinter only.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.ai_assist import client as ai_client                      # noqa: E402
from src.ai_assist.gate import AIAssistError                       # noqa: E402
from src.codegen.rules import CodegenError                         # noqa: E402
from src.driver import compile_source_detailed                     # noqa: E402
from src.lexer.lexer import LexError                               # noqa: E402
from src.parser.parser import ParseError                           # noqa: E402
from src.semantic.symbol_table import SemanticError                # noqa: E402
from src.toolchain import (                                        # noqa: E402
    compile_command, exe_suffix, find_c_compiler, install_hint, run_env,
)

HISTORY_DIR = os.path.join(PROJECT_ROOT, "history")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
SAMPLES_DIR = os.path.join(PROJECT_ROOT, "tests", "programs")
SETTINGS_PATH = os.path.join(PROJECT_ROOT, "history", "gui_settings.json")

COMPILE_ERRORS = (LexError, ParseError, SemanticError, CodegenError, AIAssistError)

MONO = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New")
UI = ("Segoe UI Variable Text", "Segoe UI", "Helvetica", "Arial")


def _pick_font(root, candidates, size, weight="normal", slant="roman"):
    from tkinter import font as tkfont
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return (name, size, weight, slant)
    return ("TkDefaultFont", size, weight, slant)


# ╭──────────────────────────────────────────────────────────────────────────╮
# │  THEME                                                                   │
# ╰──────────────────────────────────────────────────────────────────────────╯

THEMES = {
    "dark": {
        "bg":        "#212121",
        "sidebar":   "#171717",
        "editor":    "#181818",
        "console":   "#0d0d0d",
        "raised":    "#2f2f2f",
        "hover":     "#3a3a3a",
        "border":    "#303030",
        "fg":        "#ececec",
        "dim":       "#9b9b9b",
        "faint":     "#6b6b6b",
        "accent":    "#10a37f",
        "accent_hi": "#1cb894",
        "on_accent": "#ffffff",
        "ok":        "#48c78e",
        "err":       "#f2555a",
        "warn":      "#e2b53d",
        "sel":       "#2f4f47",
        "errline":   "#4a2326",
        # syntax
        "kw":        "#c792ea",
        "type":      "#e5c07b",
        "str":       "#8fce7f",
        "num":       "#e0975a",
        "com":       "#6b6b6b",
        "fn":        "#61afef",
        "pre":       "#56b6c2",
    },
    "light": {
        "bg":        "#ffffff",
        "sidebar":   "#f9f9f9",
        "editor":    "#ffffff",
        "console":   "#f7f7f8",
        "raised":    "#ececec",
        "hover":     "#e0e0e0",
        "border":    "#e3e3e3",
        "fg":        "#0d0d0d",
        "dim":       "#5d5d5d",
        "faint":     "#8f8f8f",
        "accent":    "#10a37f",
        "accent_hi": "#0d8c6d",
        "on_accent": "#ffffff",
        "ok":        "#0f7b57",
        "err":       "#c62828",
        "warn":      "#946200",
        "sel":       "#d3ece4",
        "errline":   "#fde8e8",
        "kw":        "#8250df",
        "type":      "#953800",
        "str":       "#0a7f3f",
        "num":       "#b35900",
        "com":       "#8f8f8f",
        "fn":        "#0550ae",
        "pre":       "#1a7f8c",
    },
}

PY_KEYWORDS = {
    "def", "return", "if", "elif", "else", "while", "for", "in", "and", "or",
    "not", "True", "False", "None", "break", "continue", "pass", "import",
    "from", "class", "lambda", "global", "nonlocal", "assert", "del", "raise",
    "try", "except", "finally", "with", "as", "yield",
}
PY_TYPES = {"int", "float", "str", "bool"}
PY_BUILTINS = {"print", "range", "len", "abs", "min", "max", "pow", "round"}

C_KEYWORDS = {
    "auto", "break", "case", "const", "continue", "default", "do", "else",
    "enum", "extern", "for", "goto", "if", "inline", "register", "restrict",
    "return", "sizeof", "static", "struct", "switch", "typedef", "union",
    "volatile", "while", "NULL",
}
C_TYPES = {
    "char", "double", "float", "int", "long", "short", "signed", "unsigned",
    "void", "size_t", "py_int", "py_float", "py_str", "py_bool",
}


# ╭──────────────────────────────────────────────────────────────────────────╮
# │  SMALL WIDGETS                                                           │
# ╰──────────────────────────────────────────────────────────────────────────╯

class FlatButton(tk.Label):
    """A label that behaves like a button. tk.Button ignores background
    colours on macOS and draws a platform border everywhere else."""

    def __init__(self, parent, text, command, theme, kind="ghost", padx=12, pady=6,
                 font=None):
        super().__init__(parent, text=text, padx=padx, pady=pady, font=font,
                         cursor="hand2", anchor="center")
        self.command = command
        self.kind = kind
        self._enabled = True
        self.apply_theme(theme)
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def apply_theme(self, theme):
        self.theme = theme
        self._paint(hover=False)

    def _colors(self, hover):
        t = self.theme
        if not self._enabled:
            return t["raised"], t["faint"]
        if self.kind == "primary":
            return (t["accent_hi"] if hover else t["accent"]), t["on_accent"]
        if self.kind == "danger":
            return (t["hover"] if hover else t["raised"]), t["err"]
        if self.kind == "flat":
            return (t["hover"] if hover else t["sidebar"]), t["fg"]
        return (t["hover"] if hover else t["raised"]), t["fg"]

    def _paint(self, hover):
        bg, fg = self._colors(hover)
        self.configure(bg=bg, fg=fg)

    def _enter(self, _=None):
        self._paint(hover=True)

    def _leave(self, _=None):
        self._paint(hover=False)

    def _click(self, _=None):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, value):
        self._enabled = bool(value)
        self.configure(cursor="hand2" if value else "arrow")
        self._paint(hover=False)


class Dropdown(tk.Menubutton):
    """A themed dropdown. ttk.Combobox cannot be recoloured reliably."""

    def __init__(self, parent, variable, values, theme, font=None, width=20,
                 on_change=None):
        super().__init__(parent, textvariable=variable, width=width, font=font,
                         anchor="w", padx=10, pady=4, relief="flat",
                         borderwidth=0, highlightthickness=0, cursor="hand2",
                         indicatoron=False)
        self.variable = variable
        self.on_change = on_change
        self.menu = tk.Menu(self, tearoff=0)
        self.configure(menu=self.menu)
        self.set_values(values)
        self.apply_theme(theme)
        self.bind("<Enter>", lambda e: self.configure(bg=self.theme["hover"]))
        self.bind("<Leave>", lambda e: self.configure(bg=self.theme["raised"]))

    def set_values(self, values):
        self.menu.delete(0, "end")
        for value in values:
            self.menu.add_command(label=value, command=lambda v=value: self._choose(v))

    def _choose(self, value):
        self.variable.set(value)
        if self.on_change:
            self.on_change(value)

    def apply_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme["raised"], fg=theme["fg"],
                       activebackground=theme["hover"], activeforeground=theme["fg"])
        self.menu.configure(bg=theme["raised"], fg=theme["fg"],
                            activebackground=theme["accent"],
                            activeforeground=theme["on_accent"],
                            borderwidth=0, relief="flat")


class Switch(tk.Frame):
    """A small pill toggle — clearer than a tk.Checkbutton and themeable."""

    def __init__(self, parent, text, variable, theme, font=None, command=None):
        super().__init__(parent)
        self.variable = variable
        self.command = command
        self.canvas = tk.Canvas(self, width=34, height=18, highlightthickness=0,
                                bd=0, cursor="hand2")
        self.canvas.pack(side="left")
        self.label = tk.Label(self, text=text, font=font, cursor="hand2", padx=6)
        self.label.pack(side="left")
        for widget in (self, self.canvas, self.label):
            widget.bind("<Button-1>", self._toggle)
        self.apply_theme(theme)

    def _toggle(self, _=None):
        self.variable.set(not self.variable.get())
        self._draw()
        if self.command:
            self.command()

    def refresh(self):
        """Redraw after the bound variable was changed elsewhere."""
        self._draw()

    def apply_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme["bg"])
        self.canvas.configure(bg=theme["bg"])
        self.label.configure(bg=theme["bg"], fg=theme["fg"])
        self._draw()

    def _draw(self):
        t = self.theme
        on = bool(self.variable.get())
        self.canvas.delete("all")
        track = t["accent"] if on else t["raised"]
        self.canvas.create_oval(1, 1, 17, 17, fill=track, outline=track)
        self.canvas.create_oval(17, 1, 33, 17, fill=track, outline=track)
        self.canvas.create_rectangle(9, 1, 25, 17, fill=track, outline=track)
        x = 25 if on else 9
        knob = t["on_accent"] if on else t["dim"]
        self.canvas.create_oval(x - 7, 2, x + 7, 16, fill=knob, outline=knob)


class ThinScrollbar(tk.Canvas):
    """A minimal scrollbar drawn by us.

    tk.Scrollbar on Windows draws with the native theme and ignores the
    background colours we set, which left two bright strips down the middle of
    an otherwise dark UI. Drawing it ourselves is the only way to make it match.
    The thumb hides entirely when the content already fits.
    """

    THICKNESS = 10

    def __init__(self, parent, command, theme, orient="vertical"):
        vertical = orient == "vertical"
        super().__init__(parent, highlightthickness=0, bd=0,
                         width=self.THICKNESS if vertical else 1,
                         height=1 if vertical else self.THICKNESS)
        self.command = command
        self.vertical = vertical
        self.theme = theme
        self.surface = "editor"
        self.first, self.last = 0.0, 1.0
        self._drag_origin = None
        self._hover = False
        self.configure(bg=theme["editor"])
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_origin", None))
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw()

    def apply_theme(self, theme, surface="editor"):
        self.theme = theme
        self.surface = surface
        self.configure(bg=theme[surface])
        self._draw()

    def _enter(self, _):
        self._hover = True
        self._draw()

    def _leave(self, _):
        self._hover = False
        self._draw()

    def _span(self):
        return self.winfo_height() if self.vertical else self.winfo_width()

    def _draw(self):
        self.delete("all")
        if self.last - self.first >= 0.999:
            return                                  # everything fits: no thumb
        span = self._span()
        if span <= 1:
            return
        start = int(self.first * span)
        end = max(int(self.last * span), start + 24)
        colour = self.theme["hover" if self._hover else "raised"]
        pad = 2
        radius = (self.THICKNESS - 2 * pad) // 2
        if self.vertical:
            self.create_oval(pad, start, self.THICKNESS - pad, start + 2 * radius,
                             fill=colour, outline=colour)
            self.create_oval(pad, end - 2 * radius, self.THICKNESS - pad, end,
                             fill=colour, outline=colour)
            self.create_rectangle(pad, start + radius, self.THICKNESS - pad,
                                  end - radius, fill=colour, outline=colour)
        else:
            self.create_oval(start, pad, start + 2 * radius, self.THICKNESS - pad,
                             fill=colour, outline=colour)
            self.create_oval(end - 2 * radius, pad, end, self.THICKNESS - pad,
                             fill=colour, outline=colour)
            self.create_rectangle(start + radius, pad, end - radius,
                                  self.THICKNESS - pad, fill=colour, outline=colour)

    def _position(self, event):
        return event.y if self.vertical else event.x

    def _press(self, event):
        span = self._span()
        if span <= 1:
            return
        fraction = self._position(event) / span
        size = self.last - self.first
        if self.first <= fraction <= self.last:
            self._drag_origin = (fraction, self.first)
        else:
            self._drag_origin = (fraction, max(0.0, min(1.0 - size,
                                                        fraction - size / 2)))
            self.command("moveto", self._drag_origin[1])

    def _drag(self, event):
        if not self._drag_origin:
            return
        span = self._span()
        if span <= 1:
            return
        origin_fraction, origin_first = self._drag_origin
        delta = self._position(event) / span - origin_fraction
        size = self.last - self.first
        self.command("moveto", max(0.0, min(1.0 - size, origin_first + delta)))


class LineNumbers(tk.Canvas):
    """Line-number gutter aligned to a paired Text widget."""

    def __init__(self, parent, text_widget, theme, font):
        super().__init__(parent, width=52, highlightthickness=0, bd=0,
                         bg=theme["editor"])
        self.text = text_widget
        self.theme = theme
        self.font = font
        self.error_line = None

    def apply_theme(self, theme):
        self.theme = theme
        self.configure(bg=theme["editor"])
        self.redraw()

    def redraw(self, *_):
        self.delete("all")
        index = self.text.index("@0,0")
        while True:
            info = self.text.dlineinfo(index)
            if info is None:
                break
            number = int(str(index).split(".")[0])
            colour = self.theme["err"] if number == self.error_line else self.theme["faint"]
            self.create_text(44, info[1], anchor="ne", text=str(number),
                             fill=colour, font=self.font)
            index = self.text.index(f"{index}+1line")


# ╭──────────────────────────────────────────────────────────────────────────╮
# │  EDITOR PANE                                                             │
# ╰──────────────────────────────────────────────────────────────────────────╯

class EditorPane(tk.Frame):
    """One side of the split: a title bar, a gutter, and a text area."""

    def __init__(self, parent, app, title, language, readonly=False, actions=()):
        super().__init__(parent)
        self.app = app
        self.language = language
        self.readonly = readonly
        self._highlight_job = None

        self.header = tk.Frame(self, height=38)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)

        self.title_label = tk.Label(self.header, text=title, font=app.font_ui_bold,
                                    padx=14, anchor="w")
        self.title_label.pack(side="left")
        self.badge = tk.Label(self.header, text=language, font=app.font_small, padx=8)
        self.badge.pack(side="left", pady=9)

        self.action_buttons = []
        for label, command in reversed(actions):
            button = FlatButton(self.header, label, command, app.theme,
                                kind="ghost", padx=10, pady=3, font=app.font_small)
            button.pack(side="right", padx=(0, 10), pady=7)
            self.action_buttons.append(button)

        body = tk.Frame(self)
        body.pack(fill="both", expand=True)

        self.text = tk.Text(body, wrap="none", undo=True, borderwidth=0,
                            relief="flat", padx=10, pady=10,
                            font=app.font_mono, insertwidth=2, spacing1=1)
        self.gutter = LineNumbers(body, self.text, app.theme, app.font_small_mono)
        self.gutter.pack(side="left", fill="y")

        self.vbar = ThinScrollbar(body, self._yview, app.theme, "vertical")
        self.vbar.pack(side="right", fill="y")
        self.hbar = ThinScrollbar(body, self.text.xview, app.theme, "horizontal")
        self.hbar.pack(side="bottom", fill="x")
        self.text.configure(yscrollcommand=self._on_yscroll, xscrollcommand=self.hbar.set)
        self.text.pack(side="left", fill="both", expand=True)

        tab = app.font_measure(" " * 4)
        self.text.configure(tabs=(tab, "left"))

        if readonly:
            self.text.configure(insertwidth=0)
            self.text.bind("<Key>", self._block_typing)

        self.text.bind("<KeyRelease>", self._changed)
        self.text.bind("<MouseWheel>", lambda e: self.after(12, self.gutter.redraw))
        self.text.bind("<Configure>", lambda e: self.after(12, self.gutter.redraw))
        self.text.bind("<ButtonRelease-1>", lambda e: app.update_cursor_position())
        self.text.bind("<Return>", self._auto_indent)

        self.apply_theme(app.theme)

    # -- behaviour ------------------------------------------------------

    @staticmethod
    def _block_typing(event):
        # Allow navigation, copy and select-all; block edits.
        if event.state & 0x4 and event.keysym.lower() in ("c", "a"):
            return None
        if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End",
                            "Prior", "Next", "Shift_L", "Shift_R", "Control_L",
                            "Control_R"):
            return None
        return "break"

    def _auto_indent(self, _event):
        if self.readonly:
            return "break"
        line = self.text.get("insert linestart", "insert")
        indent = len(line) - len(line.lstrip(" "))
        if line.rstrip().endswith(":"):
            indent += 4
        self.text.insert("insert", "\n" + " " * indent)
        self.text.see("insert")
        return "break"

    def _yview(self, *args):
        self.text.yview(*args)
        self.gutter.redraw()

    def _on_yscroll(self, first, last):
        self.vbar.set(first, last)
        self.gutter.redraw()

    def _changed(self, _=None):
        self.clear_error()
        self.app.update_cursor_position()
        if self._highlight_job:
            self.after_cancel(self._highlight_job)
        self._highlight_job = self.after(120, self.highlight)
        self.gutter.redraw()

    # -- content --------------------------------------------------------

    def get_text(self):
        return self.text.get("1.0", "end-1c")

    def set_text(self, content):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.clear_error()
        self.highlight()
        self.gutter.redraw()

    def mark_error(self, line):
        self.clear_error()
        if not line:
            return
        try:
            self.text.tag_add("errorline", f"{line}.0", f"{line}.end+1c")
        except tk.TclError:
            return
        self.gutter.error_line = line
        self.gutter.redraw()
        self.text.see(f"{line}.0")

    def clear_error(self):
        self.text.tag_remove("errorline", "1.0", "end")
        if self.gutter.error_line is not None:
            self.gutter.error_line = None
            self.gutter.redraw()

    # -- theming --------------------------------------------------------

    def apply_theme(self, theme):
        t = theme
        self.configure(bg=t["bg"])
        self.header.configure(bg=t["bg"])
        self.title_label.configure(bg=t["bg"], fg=t["fg"])
        self.badge.configure(bg=t["raised"], fg=t["dim"])
        for button in self.action_buttons:
            button.apply_theme(t)
        self.text.configure(bg=t["editor"], fg=t["fg"], insertbackground=t["accent"],
                            selectbackground=t["sel"], selectforeground=t["fg"])
        for bar in (self.vbar, self.hbar):
            bar.apply_theme(t, "editor")
        self.gutter.apply_theme(t)
        self.text.tag_configure("kw", foreground=t["kw"])
        self.text.tag_configure("type", foreground=t["type"])
        self.text.tag_configure("str", foreground=t["str"])
        self.text.tag_configure("num", foreground=t["num"])
        self.text.tag_configure("com", foreground=t["com"])
        self.text.tag_configure("fn", foreground=t["fn"])
        self.text.tag_configure("pre", foreground=t["pre"])
        self.text.tag_configure("errorline", background=t["errline"])
        self.highlight()

    # -- syntax ---------------------------------------------------------

    def highlight(self):
        keywords = PY_KEYWORDS if self.language == "Python" else C_KEYWORDS
        types = PY_TYPES if self.language == "Python" else C_TYPES
        builtins = PY_BUILTINS if self.language == "Python" else set()
        line_comment = "#" if self.language == "Python" else "//"

        for tag in ("kw", "type", "str", "num", "com", "fn", "pre"):
            self.text.tag_remove(tag, "1.0", "end")

        content = self.text.get("1.0", "end-1c")
        in_block_comment = False

        for row, line in enumerate(content.split("\n"), 1):
            i = 0
            n = len(line)
            while i < n:
                rest = line[i:]

                if in_block_comment:
                    end = rest.find("*/")
                    stop = n if end < 0 else i + end + 2
                    self.text.tag_add("com", f"{row}.{i}", f"{row}.{stop}")
                    in_block_comment = end < 0
                    i = stop
                    continue

                if self.language == "C" and rest.startswith("/*"):
                    end = rest.find("*/", 2)
                    stop = n if end < 0 else i + end + 2
                    self.text.tag_add("com", f"{row}.{i}", f"{row}.{stop}")
                    in_block_comment = end < 0
                    i = stop
                    continue

                if rest.startswith(line_comment):
                    self.text.tag_add("com", f"{row}.{i}", f"{row}.{n}")
                    break

                if self.language == "C" and line[:i].strip() == "" and rest.startswith("#"):
                    self.text.tag_add("pre", f"{row}.{i}", f"{row}.{n}")
                    break

                ch = line[i]
                if ch in "\"'":
                    j = i + 1
                    while j < n and line[j] != ch:
                        j += 2 if line[j] == "\\" else 1
                    j = min(j + 1, n)
                    self.text.tag_add("str", f"{row}.{i}", f"{row}.{j}")
                    i = j
                    continue

                if ch.isdigit():
                    j = i
                    while j < n and (line[j].isalnum() or line[j] in "._"):
                        j += 1
                    self.text.tag_add("num", f"{row}.{i}", f"{row}.{j}")
                    i = j
                    continue

                if ch.isalpha() or ch == "_":
                    j = i
                    while j < n and (line[j].isalnum() or line[j] == "_"):
                        j += 1
                    word = line[i:j]
                    if word in types:
                        tag = "type"
                    elif word in keywords:
                        tag = "kw"
                    elif word in builtins:
                        tag = "fn"
                    elif j < n and line[j] == "(":
                        tag = "fn"
                    else:
                        tag = None
                    if tag:
                        self.text.tag_add(tag, f"{row}.{i}", f"{row}.{j}")
                    i = j
                    continue

                i += 1


# ╭──────────────────────────────────────────────────────────────────────────╮
# │  APPLICATION                                                             │
# ╰──────────────────────────────────────────────────────────────────────────╯

class TranslationCompilerApp:
    STAGES = [
        ("lexer", "Lexer"),
        ("parser", "Parser"),
        ("semantic", "Semantic"),
        ("codegen", "Codegen"),
        ("ai_gate", "AI gate"),
    ]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Translation Compiler — Python → C")
        self.root.geometry("1440x900")
        self.root.minsize(1000, 640)

        os.makedirs(HISTORY_DIR, exist_ok=True)
        os.makedirs(REPORTS_DIR, exist_ok=True)

        self.settings = self._load_settings()
        self.theme_name = self.settings.get("theme", "dark")
        self.theme = THEMES[self.theme_name]

        self.font_ui = _pick_font(self.root, UI, 10)
        self.font_ui_bold = _pick_font(self.root, UI, 10, "bold")
        self.font_small = _pick_font(self.root, UI, 9)
        self.font_mono = _pick_font(self.root, MONO, 11)
        self.font_small_mono = _pick_font(self.root, MONO, 9)
        self._measure_font = None

        self.current_file = None
        self.pipeline = None
        self.active_stage = "lexer"
        self.history = []
        self.busy = False
        self.c_compiler = find_c_compiler()

        self.model_var = tk.StringVar(value=self.settings.get("model",
                                                             ai_client.DEFAULT_MODEL))
        self.ai_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")
        self.position_var = tk.StringVar(value="Ln 1, Col 1")

        self._themed = []
        self._build()
        self.apply_theme()
        self._load_history()
        self._load_startup_sample()
        self._bind_shortcuts()
        self._refresh_models_async()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- font helper ----------------------------------------------------

    def font_measure(self, text):
        from tkinter import font as tkfont
        if self._measure_font is None:
            self._measure_font = tkfont.Font(font=self.font_mono)
        return self._measure_font.measure(text)

    # -- theming registry -----------------------------------------------

    def register(self, widget, **roles):
        """Remember which palette entry drives which option of a widget."""
        self._themed.append((widget, roles))
        return widget

    def apply_theme(self):
        t = self.theme
        self.root.configure(bg=t["bg"])
        for widget, roles in self._themed:
            options = {}
            for option, role in roles.items():
                options[option] = t[role] if role in t else role
            try:
                widget.configure(**options)
            except tk.TclError:
                pass
        for pane in (self.source_pane, self.output_pane):
            pane.apply_theme(t)
        for button in self._buttons:
            button.apply_theme(t)
        self.model_menu.apply_theme(t)
        self.ai_switch.apply_theme(t)
        for bar, surface in self._thin_bars:
            bar.apply_theme(t, surface)
        self._style_console(self.terminal)
        self._style_console(self.detail)
        self._refresh_stage_chips()
        self._refresh_history_ui()
        self.theme_button.configure(text="☀  Light" if self.theme_name == "dark"
                                    else "☾  Dark")

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.theme = THEMES[self.theme_name]
        self.apply_theme()

    def _style_console(self, widget):
        t = self.theme
        widget.configure(bg=t["console"], fg=t["fg"], insertbackground=t["accent"],
                         selectbackground=t["sel"], selectforeground=t["fg"])
        widget.tag_configure("head", foreground=t["accent"], font=self.font_ui_bold)
        widget.tag_configure("ok", foreground=t["ok"])
        widget.tag_configure("err", foreground=t["err"])
        widget.tag_configure("warn", foreground=t["warn"])
        widget.tag_configure("dim", foreground=t["dim"])
        widget.tag_configure("cmd", foreground=t["accent"])

    # ------------------------------------------------------------------
    #  LAYOUT
    # ------------------------------------------------------------------

    def _build(self):
        self._buttons = []
        self._build_toolbar()

        body = self.register(tk.Frame(self.root), bg="bg")
        body.pack(fill="both", expand=True)

        self._build_sidebar(body)

        right = self.register(tk.Frame(body), bg="bg")
        right.pack(side="left", fill="both", expand=True)

        self.split = tk.PanedWindow(right, orient="vertical", sashwidth=6,
                                    sashrelief="flat", borderwidth=0)
        self.register(self.split, bg="border")
        self.split.pack(fill="both", expand=True)

        editors = self.register(tk.Frame(self.split), bg="bg")
        self._build_editors(editors)
        self.split.add(editors, minsize=240, stretch="always")

        panel = self.register(tk.Frame(self.split), bg="bg")
        self._build_bottom_panel(panel)
        self.split.add(panel, minsize=160, height=280)

        self._build_status_bar()

    def _add_button(self, parent, text, command, kind="ghost", **kw):
        button = FlatButton(parent, text, command, self.theme, kind=kind,
                            font=kw.pop("font", self.font_small), **kw)
        self._buttons.append(button)
        return button

    def _build_toolbar(self):
        bar = self.register(tk.Frame(self.root, height=52), bg="bg")
        bar.pack(fill="x")
        bar.pack_propagate(False)

        title = self.register(
            tk.Label(bar, text="Translation Compiler", font=self.font_ui_bold, padx=16),
            bg="bg", fg="fg")
        title.pack(side="left")

        for text, command in (("New", self.new_file), ("Open", self.open_file),
                              ("Save", self.save_file), ("Samples", self.open_sample)):
            self._add_button(bar, text, command).pack(side="left", padx=3, pady=10)

        self._separator(bar)

        self.model_menu = Dropdown(bar, self.model_var, [self.model_var.get()],
                                   self.theme, font=self.font_small, width=22,
                                   on_change=lambda v: self._save_settings())
        self.model_menu.pack(side="left", padx=6, pady=10)

        self.ai_switch = Switch(bar, "AI-assist", self.ai_var, self.theme,
                                font=self.font_small)
        self.ai_switch.pack(side="left", padx=8)

        self._separator(bar)

        self.translate_button = self._add_button(bar, "⚡  Translate", self.translate,
                                                 kind="primary", padx=18, pady=7,
                                                 font=self.font_ui_bold)
        self.translate_button.pack(side="left", padx=8, pady=8)

        self.theme_button = self._add_button(bar, "☀  Light", self.toggle_theme)
        self.theme_button.pack(side="right", padx=(4, 14), pady=10)

    def _separator(self, parent):
        line = self.register(tk.Frame(parent, width=1), bg="border")
        line.pack(side="left", fill="y", padx=10, pady=14)

    def _build_sidebar(self, parent):
        self.sidebar = self.register(tk.Frame(parent, width=236), bg="sidebar")
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        head = self.register(tk.Frame(self.sidebar), bg="sidebar")
        head.pack(fill="x", padx=14, pady=(16, 8))
        self.register(tk.Label(head, text="History", font=self.font_ui_bold),
                      bg="sidebar", fg="fg").pack(side="left")
        self._add_button(head, "＋", self.new_file, kind="flat", padx=8,
                         pady=1).pack(side="right")

        self.register(tk.Frame(self.sidebar, height=1), bg="border").pack(
            fill="x", padx=14)

        holder = self.register(tk.Frame(self.sidebar), bg="sidebar")
        holder.pack(fill="both", expand=True, padx=6, pady=6)

        self.history_canvas = self.register(
            tk.Canvas(holder, highlightthickness=0, bd=0), bg="sidebar")
        scroll = ThinScrollbar(holder, self.history_canvas.yview, self.theme)
        self._thin_bars = [(scroll, "sidebar")]
        self.history_inner = self.register(tk.Frame(self.history_canvas), bg="sidebar")
        self.history_inner.bind(
            "<Configure>",
            lambda e: self.history_canvas.configure(
                scrollregion=self.history_canvas.bbox("all")))
        self.history_window = self.history_canvas.create_window(
            (0, 0), window=self.history_inner, anchor="nw", width=214)
        self.history_canvas.configure(yscrollcommand=scroll.set)
        self.history_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        footer = self.register(tk.Frame(self.sidebar), bg="sidebar")
        footer.pack(fill="x", padx=10, pady=10)
        self._add_button(footer, "Clear history", self.clear_history,
                         kind="flat", padx=8, pady=4).pack(fill="x")

    def _build_editors(self, parent):
        panes = tk.PanedWindow(parent, orient="horizontal", sashwidth=6,
                               sashrelief="flat", borderwidth=0)
        self.register(panes, bg="border")
        panes.pack(fill="both", expand=True)

        self.source_pane = EditorPane(
            panes, self, "Source", "Python", readonly=False,
            actions=(("Format", self.normalise_source),))
        panes.add(self.source_pane, minsize=320, stretch="always")

        self.output_pane = EditorPane(
            panes, self, "Generated C", "C", readonly=True,
            actions=(("Copy", self.copy_output), ("Save .c", self.save_output)))
        panes.add(self.output_pane, minsize=320, stretch="always")

    def _build_bottom_panel(self, parent):
        tabs = self.register(tk.Frame(parent, height=40), bg="bg")
        tabs.pack(fill="x")
        tabs.pack_propagate(False)

        self.tab_var = tk.StringVar(value="pipeline")
        self.tab_buttons = {}
        for key, label in (("pipeline", "Pipeline"), ("terminal", "Terminal"),
                           ("ai", "AI gate")):
            button = self._add_button(tabs, label, lambda k=key: self.show_tab(k),
                                      padx=14, pady=5)
            button.pack(side="left", padx=(14 if key == "pipeline" else 4, 0), pady=8)
            self.tab_buttons[key] = button

        self.run_python_button = self._add_button(tabs, "▶  Run Python", self.run_python)
        self.run_python_button.pack(side="right", padx=(4, 14), pady=8)
        self.run_c_button = self._add_button(tabs, "▶  Run C", self.run_c, kind="primary")
        self.run_c_button.pack(side="right", padx=4, pady=8)
        self._add_button(tabs, "Clear", self.clear_console).pack(side="right", padx=4,
                                                                pady=8)

        self.panel_body = self.register(tk.Frame(parent), bg="bg")
        self.panel_body.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        # -- pipeline tab
        self.pipeline_frame = self.register(tk.Frame(self.panel_body), bg="bg")
        self.chip_bar = self.register(tk.Frame(self.pipeline_frame), bg="bg")
        self.chip_bar.pack(fill="x", pady=(0, 8))
        self.chips = {}
        for index, (key, label) in enumerate(self.STAGES):
            if index:
                arrow = self.register(tk.Label(self.chip_bar, text="→",
                                               font=self.font_small),
                                      bg="bg", fg="faint")
                arrow.pack(side="left", padx=3)
            chip = tk.Label(self.chip_bar, text=f"○  {label}", font=self.font_small,
                            padx=12, pady=5, cursor="hand2")
            chip.pack(side="left")
            chip.bind("<Button-1>", lambda e, k=key: self.show_stage(k))
            self.chips[key] = chip

        self.detail = tk.Text(self.pipeline_frame, wrap="none", borderwidth=0,
                              relief="flat", padx=12, pady=10, font=self.font_small_mono,
                              state="disabled", height=8)
        detail_scroll = ThinScrollbar(self.pipeline_frame, self.detail.yview, self.theme)
        self._thin_bars.append((detail_scroll, "console"))
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail.pack(fill="both", expand=True)

        # -- terminal / ai tab share one console
        self.console_frame = self.register(tk.Frame(self.panel_body), bg="bg")
        self.terminal = tk.Text(self.console_frame, wrap="word", borderwidth=0,
                                relief="flat", padx=12, pady=10,
                                font=self.font_small_mono, state="disabled")
        console_scroll = ThinScrollbar(self.console_frame, self.terminal.yview, self.theme)
        self._thin_bars.append((console_scroll, "console"))
        self.terminal.configure(yscrollcommand=console_scroll.set)
        console_scroll.pack(side="right", fill="y")
        self.terminal.pack(fill="both", expand=True)

        self.show_tab("pipeline")

    def _build_status_bar(self):
        bar = self.register(tk.Frame(self.root, height=26), bg="sidebar")
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self.status_label = self.register(
            tk.Label(bar, textvariable=self.status_var, font=self.font_small,
                     padx=14, anchor="w"),
            bg="sidebar", fg="dim")
        self.status_label.pack(side="left")

        compiler = os.path.basename(self.c_compiler) if self.c_compiler else "none found"
        self.register(tk.Label(bar, text=f"cc: {compiler}", font=self.font_small,
                               padx=14), bg="sidebar", fg="faint").pack(side="right")
        self.register(tk.Label(bar, textvariable=self.position_var,
                               font=self.font_small, padx=14),
                      bg="sidebar", fg="faint").pack(side="right")

    def _bind_shortcuts(self):
        self.root.bind("<Control-Return>", lambda e: self.translate())
        self.root.bind("<F5>", lambda e: self.run_c())
        self.root.bind("<Control-s>", lambda e: self.save_file())
        self.root.bind("<Control-o>", lambda e: self.open_file())
        self.root.bind("<Control-n>", lambda e: self.new_file())
        self.root.bind("<Control-l>", lambda e: self.clear_console())

    # ------------------------------------------------------------------
    #  TABS AND STATUS
    # ------------------------------------------------------------------

    def show_tab(self, key):
        self.tab_var.set(key)
        self.pipeline_frame.pack_forget()
        self.console_frame.pack_forget()
        if key == "pipeline":
            self.pipeline_frame.pack(fill="both", expand=True)
        else:
            self.console_frame.pack(fill="both", expand=True)
            if key == "ai":
                self.render_ai_report()
        for name, button in self.tab_buttons.items():
            button.kind = "primary" if name == key else "ghost"
            button.apply_theme(self.theme)

    def set_status(self, text, tone="dim"):
        self.status_var.set(text)
        self.status_label.configure(fg=self.theme.get(tone, self.theme["dim"]))

    def update_cursor_position(self):
        try:
            line, col = self.source_pane.text.index("insert").split(".")
            self.position_var.set(f"Ln {line}, Col {int(col) + 1}")
        except (tk.TclError, ValueError):
            pass

    # ------------------------------------------------------------------
    #  FILE ACTIONS
    # ------------------------------------------------------------------

    def new_file(self):
        self.source_pane.set_text("")
        self.output_pane.set_text("")
        self.current_file = None
        self.pipeline = None
        self._refresh_stage_chips()
        self._write_detail("Nothing translated yet. Press Ctrl+Enter to translate.\n",
                           "dim")
        self.set_status("New file")

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open Python source",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
            initialdir=SAMPLES_DIR if os.path.isdir(SAMPLES_DIR) else PROJECT_ROOT)
        if path:
            self._load_path(path)

    def open_sample(self):
        path = filedialog.askopenfilename(
            title="Open a sample program", initialdir=SAMPLES_DIR,
            filetypes=[("Python files", "*.py")])
        if path:
            self._load_path(path)

    def _load_path(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            messagebox.showerror("Open failed", str(e))
            return
        self.source_pane.set_text(content)
        self.current_file = path
        self.set_status(f"Opened {os.path.basename(path)}")

    def save_file(self):
        path = self.current_file or filedialog.asksaveasfilename(
            title="Save source", defaultextension=".py",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.source_pane.get_text())
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.current_file = path
        self.set_status(f"Saved {os.path.basename(path)}", "ok")

    def save_output(self):
        code = self.output_pane.get_text()
        if not code.strip():
            self.set_status("Nothing to save — translate first", "warn")
            return
        default = "out.c"
        if self.current_file:
            default = os.path.splitext(os.path.basename(self.current_file))[0] + ".c"
        path = filedialog.asksaveasfilename(
            title="Save generated C", defaultextension=".c", initialfile=default,
            filetypes=[("C files", "*.c"), ("All files", "*.*")])
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)
        self.set_status(f"Saved {os.path.basename(path)}", "ok")

    def copy_output(self):
        code = self.output_pane.get_text()
        if not code.strip():
            self.set_status("Nothing to copy — translate first", "warn")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        self.set_status("Generated C copied to clipboard", "ok")

    def normalise_source(self):
        """Normalise line endings and strip trailing whitespace."""
        text = self.source_pane.get_text().replace("\r\n", "\n").replace("\r", "\n")
        cleaned = "\n".join(line.rstrip() for line in text.split("\n"))
        self.source_pane.set_text(cleaned.rstrip("\n") + "\n" if cleaned.strip() else "")
        self.set_status("Whitespace normalised")

    # ------------------------------------------------------------------
    #  TRANSLATE
    # ------------------------------------------------------------------

    def translate(self):
        if self.busy:
            return
        source = self.source_pane.get_text()
        if not source.strip():
            self.set_status("Nothing to translate — write or open some Python", "warn")
            return

        self.busy = True
        self.translate_button.set_enabled(False)
        self.source_pane.clear_error()
        self.pipeline = None
        self._refresh_stage_chips()
        self.set_status("Translating…")
        threading.Thread(target=self._translate_worker, args=(source,),
                         daemon=True).start()

    def _translate_worker(self, source):
        use_ai = bool(self.ai_var.get())
        model = self.model_var.get()
        log_path = os.path.join(REPORTS_DIR, "ai_invocation_log.md")
        cache_path = os.path.join(REPORTS_DIR, "ai_cache.json")
        results = {"stages": {}}
        try:
            c_code, stats, results = compile_source_detailed(
                source, use_ai=use_ai, model=model,
                log_path=log_path, cache_path=cache_path)
        except COMPILE_ERRORS as error:
            message = str(error)
            self.root.after(0, lambda: self._on_failure(message, results))
        except Exception as error:                       # unexpected: still show it
            message = f"{type(error).__name__}: {error}"
            self.root.after(0, lambda: self._on_failure(message, results))
        else:
            self.root.after(0, lambda: self._on_success(c_code, stats, results, source))

    def _finish(self):
        self.busy = False
        self.translate_button.set_enabled(True)

    def _on_success(self, c_code, stats, results, source):
        self._finish()
        self.pipeline = results
        self.output_pane.set_text(c_code)
        self._refresh_stage_chips()
        self.show_stage("codegen")

        if stats["ai_calls_used"]:
            note = (f"{stats['ai_calls_used']} AI-resolved call site(s), "
                    f"{stats['ai_queries']} model quer{'y' if stats['ai_queries'] == 1 else 'ies'}")
            tone = "warn"
        else:
            note = "fully deterministic — no AI used"
            tone = "ok"
        self.set_status(f"Translated · {stats['total_call_sites']} call site(s) · {note}",
                        tone)
        self._save_history(source, c_code, stats)

    def _on_failure(self, message, results):
        self._finish()
        self.pipeline = results
        self._refresh_stage_chips()
        line = _line_from_message(message)
        if line:
            self.source_pane.mark_error(line)
        self.set_status(f"✗  {message.splitlines()[0][:110]}", "err")
        self.show_tab("pipeline")
        self._write_detail("Compilation failed\n\n", "err")
        self._append_detail(message + "\n", "err")
        if line:
            self._append_detail(f"\nHighlighted line {line} in the source.\n", "dim")

    # ------------------------------------------------------------------
    #  PIPELINE VIEW
    # ------------------------------------------------------------------

    def _stage_status(self, key):
        if not self.pipeline:
            return "pending"
        return self.pipeline.get("stages", {}).get(key, {}).get("status", "pending")

    def _refresh_stage_chips(self):
        marks = {"ok": "✓", "used": "✦", "skipped": "○", "disabled": "—",
                 "error": "✗", "pending": "○"}
        tones = {"ok": "ok", "used": "warn", "skipped": "dim", "disabled": "faint",
                 "error": "err", "pending": "faint"}
        for key, label in self.STAGES:
            status = self._stage_status(key)
            chip = self.chips[key]
            selected = key == self.active_stage and self.pipeline is not None
            chip.configure(
                text=f"{marks.get(status, '○')}  {label}",
                bg=self.theme["raised"] if selected else self.theme["bg"],
                fg=self.theme[tones.get(status, "faint")])

    def show_stage(self, key):
        self.active_stage = key
        self._refresh_stage_chips()
        self.show_tab("pipeline")
        if not self.pipeline:
            self._write_detail("Nothing translated yet. Press Ctrl+Enter to translate.\n",
                               "dim")
            return
        data = self.pipeline.get("stages", {}).get(key)
        label = dict(self.STAGES)[key]
        if not data:
            self._write_detail(f"{label}: not reached.\n", "dim")
            return

        self._write_detail(f"{label}  ·  {data.get('status', '?').upper()}\n\n", "head")
        if data.get("status") == "error":
            self._append_detail(data.get("error", "unknown error") + "\n", "err")
            return

        renderer = getattr(self, f"_render_{key}", None)
        if renderer:
            renderer(data)

    def _render_lexer(self, data):
        tokens = data.get("tokens", [])
        self._append_detail(f"{data.get('token_count', 0)} tokens\n\n", "dim")
        for kind, value, line in tokens[:400]:
            shown = repr(value) if value.strip() else ""
            self._append_detail(f"  {line:>4}  {kind:<18}{shown}\n")
        if len(tokens) > 400:
            self._append_detail(f"\n  … {len(tokens) - 400} more\n", "dim")

    def _render_parser(self, data):
        self._append_detail(f"{data.get('function_count', 0)} function(s)\n\n", "dim")
        text = json.dumps(data.get("ast", {}), indent=2, default=str)
        if len(text) > 40000:
            text = text[:40000] + "\n…(truncated)"
        self._append_detail(text + "\n")

    def _render_semantic(self, data):
        functions = data.get("functions", {})
        locals_ = data.get("locals", {})
        self._append_detail(f"{len(functions)} function(s), "
                            f"{data.get('call_sites', 0)} call site(s)\n\n", "dim")
        for name, info in functions.items():
            params = ", ".join(f"{n}: {t}" for n, t in info["params"])
            self._append_detail(f"  def {name}({params}) -> {info['return_type']}\n", "ok")
            own = locals_.get(name, {})
            declared = [n for n in own if n not in {p for p, _ in info["params"]}]
            for var in declared:
                self._append_detail(f"       {var}: {own[var]}\n", "dim")
            self._append_detail("\n")

    def _render_codegen(self, data):
        helpers = data.get("runtime_helpers", [])
        self._append_detail(
            f"{data.get('total_call_sites', 0)} call site(s), "
            f"{data.get('ai_call_sites', 0)} resolved by AI\n\n", "dim")
        if data.get("ai_call_sites"):
            rate = data["ai_call_sites"] / max(1, data["total_call_sites"]) * 100
            self._append_detail(f"  AI-invocation rate: {rate:.0f}%\n\n", "warn")
        else:
            self._append_detail("  Fully deterministic: every construct had a "
                                "static rule.\n\n", "ok")
        self._append_detail(f"Runtime helpers emitted ({len(helpers)}):\n", "dim")
        for helper in helpers:
            self._append_detail(f"  {helper}\n")
        if not helpers:
            self._append_detail("  none — the program needed no runtime support\n")

    def _render_ai_gate(self, data):
        status = data.get("status")
        if status == "disabled":
            self._append_detail(
                "AI-assist is switched off.\n\n"
                "Every construct in the grammar and every builtin (len, abs, min, max,\n"
                "pow, round, int, float, bool) has a static C rule, so nothing here is\n"
                "needed for ordinary programs. Turn the switch on only when your code\n"
                "calls a function this compiler does not know.\n", "dim")
            return
        if status == "skipped":
            self._append_detail("AI-assist was enabled but never needed: every call had "
                                "a static rule.\n", "ok")
            return
        self._append_detail(
            f"{data.get('calls', 0)} call site(s) resolved · "
            f"{data.get('queries', 0)} model quer(y/ies) · "
            f"{data.get('cache_hits', 0)} cache hit(s)\n\n", "warn")
        for record in data.get("records", []):
            self._append_detail(f"  {record['call']}  ({record['source']})\n", "head")
            for attempt in record.get("attempts", []):
                tone = "ok" if attempt.get("valid") else "err"
                self._append_detail(f"    → {attempt.get('candidate', attempt)!r}\n", tone)
                if attempt.get("reason"):
                    self._append_detail(f"      {attempt['reason']}\n", "dim")
            self._append_detail(f"    accepted: {record['accepted']!r}\n\n", "ok")

    def render_ai_report(self):
        self._clear_console()
        stage = (self.pipeline or {}).get("stages", {}).get("ai_gate")
        self._write_terminal("AI-assist policy\n", "head")
        self._write_terminal(
            "  • The deterministic core translates the whole language on its own.\n"
            "  • The model is consulted only for a call to a name that is neither\n"
            "    defined in your program nor a known builtin.\n"
            "  • Its answer is rejected unless it is a pure math expression over the\n"
            "    call's own arguments AND a real C compiler accepts it.\n"
            "  • Verified answers are cached, so a rebuild makes no model call.\n\n",
            "dim")
        if not self.c_compiler:
            self._write_terminal(
                "No C compiler was found, so the gate cannot validate anything and will\n"
                "reject every candidate. " + install_hint() + "\n\n", "warn")
        if not stage:
            self._write_terminal("Nothing translated yet.\n", "dim")
            return
        self._write_terminal(f"Last run: {stage.get('status')}\n",
                             "ok" if stage.get("status") in ("disabled", "skipped") else "warn")
        for record in stage.get("records", []):
            self._write_terminal(f"\n{record['call']} via {record['source']}\n", "head")
            for attempt in record.get("attempts", []):
                self._write_terminal(f"  {attempt}\n",
                                     "ok" if attempt.get("valid") else "err")
            self._write_terminal(f"  accepted: {record['accepted']!r}\n", "ok")

    # ------------------------------------------------------------------
    #  RUNNING
    # ------------------------------------------------------------------

    def run_python(self):
        source = self.source_pane.get_text()
        if not source.strip():
            self.set_status("Nothing to run", "warn")
            return
        self.show_tab("terminal")
        self._write_terminal("$ python source.py\n", "cmd")
        threading.Thread(target=self._run_python_worker, args=(source,),
                         daemon=True).start()

    def _run_python_worker(self, source):
        path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             encoding="utf-8") as f:
                f.write(source)
                if "def main" in source:
                    f.write("\n\nif True:\n    main()\n")
                path = f.name
            result = subprocess.run([sys.executable, path], capture_output=True,
                                    text=True, timeout=30)
            self.root.after(0, lambda: self._show_result(result.returncode,
                                                         result.stdout, result.stderr))
        except subprocess.TimeoutExpired:
            self.root.after(0, lambda: self._write_terminal("Timed out after 30s\n", "err"))
        except Exception as error:
            message = str(error)
            self.root.after(0, lambda: self._write_terminal(f"Error: {message}\n", "err"))
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def run_c(self):
        code = self.output_pane.get_text()
        if not code.strip():
            self.set_status("Translate first — there is no C to run", "warn")
            return
        if not self.c_compiler:
            self.show_tab("terminal")
            self._write_terminal(install_hint() + "\n", "err")
            return
        self.show_tab("terminal")
        self._write_terminal(
            f"$ {os.path.basename(self.c_compiler)} prog.c -lm -o prog && ./prog\n", "cmd")
        threading.Thread(target=self._run_c_worker, args=(code,), daemon=True).start()

    def _run_c_worker(self, code):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                c_path = os.path.join(tmp, "prog.c")
                bin_path = os.path.join(tmp, "prog" + exe_suffix())
                with open(c_path, "w", encoding="utf-8") as f:
                    f.write(code)
                env = run_env(self.c_compiler)
                build = subprocess.run(
                    compile_command(self.c_compiler, c_path, bin_path),
                    capture_output=True, text=True, env=env, cwd=tmp, timeout=120)
                if build.returncode != 0:
                    detail = build.stderr or build.stdout or "(no diagnostics)"
                    self.root.after(0, lambda: self._write_terminal(
                        f"compilation failed:\n{detail}\n", "err"))
                    return
                self.root.after(0, lambda: self._write_terminal("compiled ✓\n", "ok"))
                run = subprocess.run([bin_path], capture_output=True, text=True,
                                     env=env, timeout=30)
                self.root.after(0, lambda: self._show_result(run.returncode, run.stdout,
                                                             run.stderr))
        except subprocess.TimeoutExpired:
            self.root.after(0, lambda: self._write_terminal("Timed out\n", "err"))
        except Exception as error:
            message = str(error)
            self.root.after(0, lambda: self._write_terminal(f"Error: {message}\n", "err"))

    def _show_result(self, returncode, out, err):
        if out:
            self._write_terminal(out, "")
        if err:
            self._write_terminal(err, "err")
        tone = "ok" if returncode == 0 else "err"
        self._write_terminal(f"[exit {returncode}]\n\n", tone)

    # ------------------------------------------------------------------
    #  CONSOLE / DETAIL HELPERS
    # ------------------------------------------------------------------

    def _write_terminal(self, text, tag=""):
        self.terminal.configure(state="normal")
        self.terminal.insert("end", text, tag or ())
        self.terminal.see("end")
        self.terminal.configure(state="disabled")

    def _clear_console(self):
        self.terminal.configure(state="normal")
        self.terminal.delete("1.0", "end")
        self.terminal.configure(state="disabled")

    def clear_console(self):
        if self.tab_var.get() == "pipeline":
            self._write_detail("")
        else:
            self._clear_console()

    def _write_detail(self, text, tag=""):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if text:
            self.detail.insert("end", text, tag or ())
        self.detail.configure(state="disabled")

    def _append_detail(self, text, tag=""):
        self.detail.configure(state="normal")
        self.detail.insert("end", text, tag or ())
        self.detail.configure(state="disabled")

    # ------------------------------------------------------------------
    #  HISTORY
    # ------------------------------------------------------------------

    def _load_history(self):
        self.history = []
        if os.path.isdir(HISTORY_DIR):
            for name in sorted(os.listdir(HISTORY_DIR), reverse=True):
                if not name.endswith(".json") or name == "gui_settings.json":
                    continue
                try:
                    with open(os.path.join(HISTORY_DIR, name), encoding="utf-8") as f:
                        entry = json.load(f)
                    entry["_file"] = name
                    self.history.append(entry)
                except (OSError, json.JSONDecodeError):
                    continue
        self._refresh_history_ui()

    def _save_history(self, source, c_code, stats):
        stamp = datetime.now()
        entry = {
            "timestamp": stamp.isoformat(timespec="seconds"),
            "filename": os.path.basename(self.current_file) if self.current_file
                        else "untitled.py",
            "source": source,
            "output": c_code,
            "model": self.model_var.get(),
            "ai_enabled": bool(self.ai_var.get()),
            "stats": stats,
        }
        name = stamp.strftime("%Y%m%d_%H%M%S_%f") + ".json"
        try:
            with open(os.path.join(HISTORY_DIR, name), "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2)
            entry["_file"] = name
        except OSError:
            pass
        self.history.insert(0, entry)
        del self.history[200:]
        self._refresh_history_ui()

    def _refresh_history_ui(self):
        for widget in self.history_inner.winfo_children():
            widget.destroy()
        t = self.theme
        self.history_canvas.configure(bg=t["sidebar"])
        self.history_inner.configure(bg=t["sidebar"])

        if not self.history:
            tk.Label(self.history_inner, text="No translations yet",
                     font=self.font_small, bg=t["sidebar"], fg=t["faint"],
                     anchor="w", padx=10, pady=10).pack(fill="x")
            return

        for index, entry in enumerate(self.history[:80]):
            row = tk.Frame(self.history_inner, bg=t["sidebar"], cursor="hand2")
            row.pack(fill="x", pady=1, padx=2)

            stamp = entry.get("timestamp", "")
            try:
                when = datetime.fromisoformat(stamp).strftime("%d %b · %H:%M")
            except (ValueError, TypeError):
                when = stamp[:16]

            marker = "✦ " if entry.get("ai_enabled") else ""
            name = tk.Label(row, text=marker + entry.get("filename", "untitled.py"),
                            font=self.font_small, bg=t["sidebar"], fg=t["fg"],
                            anchor="w", padx=10, cursor="hand2")
            name.pack(fill="x", pady=(3, 0))
            sub = tk.Label(row, text=when, font=self.font_small, bg=t["sidebar"],
                           fg=t["faint"], anchor="w", padx=10, cursor="hand2")
            sub.pack(fill="x", pady=(0, 4))

            group = (row, name, sub)
            for widget in group:
                widget.bind("<Button-1>", lambda e, i=index: self._restore(i))
                widget.bind("<Enter>",
                            lambda e, g=group: [w.configure(bg=t["hover"]) for w in g])
                widget.bind("<Leave>",
                            lambda e, g=group: [w.configure(bg=t["sidebar"]) for w in g])

    def _restore(self, index):
        if index >= len(self.history):
            return
        entry = self.history[index]
        self.source_pane.set_text(entry.get("source", ""))
        self.output_pane.set_text(entry.get("output", ""))
        self.model_var.set(entry.get("model", self.model_var.get()))
        self.ai_var.set(bool(entry.get("ai_enabled")))
        self.ai_switch.refresh()
        self.pipeline = None
        self._refresh_stage_chips()
        self.set_status(f"Restored {entry.get('filename', 'untitled.py')}")

    def clear_history(self):
        if not self.history:
            return
        if not messagebox.askyesno("Clear history",
                                   f"Delete all {len(self.history)} saved translations?"):
            return
        for entry in self.history:
            name = entry.get("_file")
            if name:
                try:
                    os.unlink(os.path.join(HISTORY_DIR, name))
                except OSError:
                    pass
        self.history = []
        self._refresh_history_ui()
        self.set_status("History cleared")

    # ------------------------------------------------------------------
    #  SETTINGS / STARTUP
    # ------------------------------------------------------------------

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_settings(self):
        try:
            os.makedirs(HISTORY_DIR, exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({"theme": self.theme_name, "model": self.model_var.get()},
                          f, indent=2)
        except OSError:
            pass

    def _refresh_models_async(self):
        def worker():
            models = ai_client.list_models()
            self.root.after(0, lambda: self.model_menu.set_values(models))
        threading.Thread(target=worker, daemon=True).start()

    def _load_startup_sample(self):
        sample = os.path.join(SAMPLES_DIR, "factorial.py")
        if os.path.exists(sample):
            self._load_path(sample)
        self._write_detail("Press Ctrl+Enter to translate. F5 runs the generated C.\n",
                           "dim")

    def _on_close(self):
        self._save_settings()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def _line_from_message(message):
    """Pull the source line number out of a compiler diagnostic."""
    import re
    match = re.search(r"line (\d+)", message)
    return int(match.group(1)) if match else None


if __name__ == "__main__":
    TranslationCompilerApp().run()
