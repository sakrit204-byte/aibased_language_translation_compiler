"""
Translation Compiler GUI — IDE-like interface for the Python→C hybrid compiler.

Launch:  py gui.py   (from the translation-compiler/ directory)

Features:
  • Split-screen editors (source Python / output C) with language dropdowns + swap
  • Model selection dropdown (Ollama models)
  • Collapsible pipeline viewer showing each compiler layer's result
  • Code history sidebar with auto-save (persisted to history/ as JSON)
  • Integrated terminal with Run Python / Run C options
  • ChatGPT-inspired dark theme via ttkbootstrap
"""

import json
import os
import sys
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from datetime import datetime

# ---------------------------------------------------------------------------
# ttkbootstrap import with graceful fallback
# ---------------------------------------------------------------------------
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import *
    HAS_BOOTSTRAP = True
except ImportError:
    import tkinter.ttk as ttk
    HAS_BOOTSTRAP = False

# ---------------------------------------------------------------------------
# Make the project importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.driver import compile_source_detailed
from src.lexer.lexer import LexError
from src.parser.parser import ParseError
from src.semantic.symbol_table import SemanticError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HISTORY_DIR = os.path.join(PROJECT_ROOT, "history")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "reports")
AVAILABLE_MODELS = ["qwen2.5-coder:7b", "qwen2.5-coder:3b", "codellama:7b", "deepseek-coder:6.7b"]

LANGUAGES = ["Python", "C"]

# Dark color palette (ChatGPT-inspired)
BG_DARK       = "#1e1e2e"
BG_SIDEBAR    = "#181825"
BG_EDITOR     = "#11111b"
BG_TERMINAL   = "#0d0d14"
BG_TOOLBAR    = "#1e1e2e"
FG_TEXT        = "#cdd6f4"
FG_DIM         = "#6c7086"
FG_ACCENT      = "#89b4fa"
FG_GREEN       = "#a6e3a1"
FG_RED         = "#f38ba8"
FG_YELLOW      = "#f9e2af"
FG_ORANGE      = "#fab387"
BORDER_COLOR   = "#313244"
HIGHLIGHT_BG   = "#313244"
BUTTON_BG      = "#45475a"
BUTTON_FG      = "#cdd6f4"

# Python keyword sets for syntax highlighting
PY_KEYWORDS  = {"def", "return", "if", "elif", "else", "while", "for", "in",
                "range", "print", "and", "or", "not", "True", "False", "None",
                "int", "float", "str", "bool", "import", "from", "class", "pass",
                "break", "continue", "try", "except", "finally", "with", "as",
                "yield", "lambda", "global", "nonlocal", "assert", "del", "raise"}

C_KEYWORDS   = {"int", "double", "float", "char", "void", "return", "if", "else",
                "while", "for", "include", "define", "struct", "typedef", "const",
                "static", "extern", "sizeof", "switch", "case", "break", "continue",
                "do", "enum", "union", "unsigned", "signed", "long", "short",
                "printf", "scanf", "NULL", "stdin", "stdout", "stderr"}


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  LINE-NUMBER GUTTER WIDGET                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class LineNumbers(tk.Canvas):
    """A canvas that displays line numbers aligned to a paired Text widget."""

    def __init__(self, parent, text_widget, **kw):
        kw.setdefault("width", 48)
        kw.setdefault("bg", BG_DARK)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, **kw)
        self.text_widget = text_widget

    def redraw(self, *_args):
        self.delete("all")
        i = self.text_widget.index("@0,0")
        while True:
            dline = self.text_widget.dlineinfo(i)
            if dline is None:
                break
            y = dline[1]
            linenum = str(i).split(".")[0]
            self.create_text(42, y, anchor="ne", text=linenum,
                             fill=FG_DIM, font=("Consolas", 10))
            i = self.text_widget.index(f"{i}+1line")


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  CODE EDITOR PANEL (with line numbers + syntax highlighting)            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class CodeEditorPanel(tk.Frame):
    """One side of the split-screen: language dropdown + editor + line numbers."""

    def __init__(self, parent, language="Python", readonly=False, **kw):
        super().__init__(parent, bg=BG_DARK, **kw)
        self.language = language
        self.readonly = readonly

        # ── Header bar (language selector) ──
        header = tk.Frame(self, bg=BG_TOOLBAR, height=32)
        header.pack(fill="x", padx=0, pady=0)
        header.pack_propagate(False)

        self.lang_var = tk.StringVar(value=language)
        self.lang_combo = ttk.Combobox(header, textvariable=self.lang_var,
                                        values=LANGUAGES, state="readonly", width=10)
        self.lang_combo.pack(side="left", padx=8, pady=4)
        self.lang_combo.bind("<<ComboboxSelected>>", lambda e: self.highlight_syntax())

        label_text = "  Source" if not readonly else "  Output"
        lbl = tk.Label(header, text=label_text, bg=BG_TOOLBAR, fg=FG_DIM,
                       font=("Segoe UI", 9))
        lbl.pack(side="left", padx=4)

        # ── Editor area (line numbers + text) ──
        editor_frame = tk.Frame(self, bg=BG_EDITOR)
        editor_frame.pack(fill="both", expand=True)

        self.text = tk.Text(editor_frame, wrap="none", undo=True,
                            bg=BG_EDITOR, fg=FG_TEXT, insertbackground=FG_ACCENT,
                            selectbackground=HIGHLIGHT_BG, selectforeground=FG_TEXT,
                            font=("Consolas", 11), borderwidth=0, padx=8, pady=8,
                            relief="flat", tabs="4c")
        self.line_nums = LineNumbers(editor_frame, self.text)
        self.line_nums.pack(side="left", fill="y")

        # Scrollbar
        vscroll = tk.Scrollbar(editor_frame, command=self._on_scroll, bg=BG_DARK,
                               troughcolor=BG_DARK, activebackground=BUTTON_BG)
        vscroll.pack(side="right", fill="y")
        self.text.config(yscrollcommand=vscroll.set)

        hscroll = tk.Scrollbar(editor_frame, orient="horizontal",
                               command=self.text.xview, bg=BG_DARK,
                               troughcolor=BG_DARK, activebackground=BUTTON_BG)
        hscroll.pack(side="bottom", fill="x")
        self.text.config(xscrollcommand=hscroll.set)

        self.text.pack(side="left", fill="both", expand=True)

        if readonly:
            self.text.config(state="disabled")

        # Syntax highlighting tags
        self.text.tag_configure("keyword", foreground="#c678dd")
        self.text.tag_configure("string", foreground="#98c379")
        self.text.tag_configure("comment", foreground="#5c6370", font=("Consolas", 11, "italic"))
        self.text.tag_configure("number", foreground="#d19a66")
        self.text.tag_configure("function", foreground="#61afef")
        self.text.tag_configure("type", foreground="#e5c07b")
        self.text.tag_configure("preprocessor", foreground="#56b6c2")

        # Bind events for live syntax highlighting + line numbers
        self.text.bind("<KeyRelease>", self._on_text_change)
        self.text.bind("<MouseWheel>", lambda e: self.after(10, self.line_nums.redraw))
        self.text.bind("<Configure>", lambda e: self.after(10, self.line_nums.redraw))

    def _on_scroll(self, *args):
        self.text.yview(*args)
        self.line_nums.redraw()

    def _on_text_change(self, event=None):
        self.highlight_syntax()
        self.line_nums.redraw()

    def get_text(self):
        return self.text.get("1.0", "end-1c")

    def set_text(self, content):
        was_disabled = self.text.cget("state") == "disabled"
        if was_disabled:
            self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        if was_disabled:
            self.text.config(state="disabled")
        self.highlight_syntax()
        self.line_nums.redraw()

    def highlight_syntax(self):
        """Apply syntax highlighting based on the selected language."""
        lang = self.lang_var.get()
        keywords = PY_KEYWORDS if lang == "Python" else C_KEYWORDS

        was_disabled = self.text.cget("state") == "disabled"
        if was_disabled:
            self.text.config(state="normal")

        # Remove old tags
        for tag in ("keyword", "string", "comment", "number", "function", "type", "preprocessor"):
            self.text.tag_remove(tag, "1.0", "end")

        content = self.text.get("1.0", "end")
        lines = content.split("\n")

        for line_idx, line in enumerate(lines, 1):
            col = 0
            i = 0
            while i < len(line):
                ch = line[i]

                # Comments
                if (lang == "Python" and ch == "#") or \
                   (lang == "C" and i + 1 < len(line) and line[i:i+2] == "//"):
                    start = f"{line_idx}.{i}"
                    end = f"{line_idx}.{len(line)}"
                    self.text.tag_add("comment", start, end)
                    break

                # Preprocessor (#include, #define)
                if lang == "C" and ch == "#" and i == 0:
                    start = f"{line_idx}.{i}"
                    end = f"{line_idx}.{len(line)}"
                    self.text.tag_add("preprocessor", start, end)
                    break

                # Strings
                if ch in ('"', "'"):
                    quote = ch
                    start = f"{line_idx}.{i}"
                    i += 1
                    while i < len(line) and line[i] != quote:
                        if line[i] == '\\' and i + 1 < len(line):
                            i += 1
                        i += 1
                    i += 1  # closing quote
                    end = f"{line_idx}.{i}"
                    self.text.tag_add("string", start, end)
                    continue

                # Numbers
                if ch.isdigit():
                    start = f"{line_idx}.{i}"
                    while i < len(line) and (line[i].isdigit() or line[i] == '.'):
                        i += 1
                    end = f"{line_idx}.{i}"
                    self.text.tag_add("number", start, end)
                    continue

                # Identifiers / keywords
                if ch.isalpha() or ch == '_':
                    start_i = i
                    while i < len(line) and (line[i].isalnum() or line[i] == '_'):
                        i += 1
                    word = line[start_i:i]
                    start = f"{line_idx}.{start_i}"
                    end = f"{line_idx}.{i}"

                    if word in keywords:
                        # Types get a different color
                        if word in ("int", "float", "str", "bool", "double", "char",
                                    "void", "const", "unsigned", "signed", "long", "short"):
                            self.text.tag_add("type", start, end)
                        else:
                            self.text.tag_add("keyword", start, end)
                    elif lang == "Python" and i < len(line) and line[i] == '(':
                        self.text.tag_add("function", start, end)
                    elif lang == "C" and i < len(line) and line[i] == '(':
                        self.text.tag_add("function", start, end)
                    continue

                i += 1

        if was_disabled:
            self.text.config(state="disabled")


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  MAIN APPLICATION                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

class TranslationCompilerApp:
    def __init__(self):
        # ── Window setup ──
        if HAS_BOOTSTRAP:
            self.root = ttk.Window(themename="darkly",
                                    title="Translation Compiler — AI-Hybrid Python→C",
                                    size=(1400, 900))
        else:
            self.root = tk.Tk()
            self.root.title("Translation Compiler — AI-Hybrid Python→C")
            self.root.geometry("1400x900")

        self.root.configure(bg=BG_DARK)
        self.root.minsize(900, 600)

        # State
        self.current_file = None
        self.pipeline_results = None
        self.pipeline_visible = False
        self.history = []

        # Ensure directories exist
        os.makedirs(HISTORY_DIR, exist_ok=True)
        os.makedirs(REPORTS_DIR, exist_ok=True)

        self._build_ui()
        self._load_history()
        self._load_sample_code()

    # ──────────────────────────────────────────────────────────────────────
    #  UI CONSTRUCTION
    # ──────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── TOOLBAR ──
        self._build_toolbar()

        # ── MAIN CONTENT (sidebar + editors + pipeline + terminal) ──
        main_container = tk.Frame(self.root, bg=BG_DARK)
        main_container.pack(fill="both", expand=True)

        # Left: History sidebar
        self._build_sidebar(main_container)

        # Right: Everything else in a vertical arrangement
        right_pane = tk.Frame(main_container, bg=BG_DARK)
        right_pane.pack(side="left", fill="both", expand=True)

        # Use PanedWindow for resizable split between editors and bottom panels
        self.vpaned = tk.PanedWindow(right_pane, orient="vertical",
                                      bg=BORDER_COLOR, sashwidth=3,
                                      sashrelief="flat")
        self.vpaned.pack(fill="both", expand=True)

        # Top half: Split-screen editors
        editor_container = tk.Frame(self.vpaned, bg=BG_DARK)
        self._build_editors(editor_container)
        self.vpaned.add(editor_container, minsize=250)

        # Bottom half: Pipeline + Terminal stacked
        bottom_container = tk.Frame(self.vpaned, bg=BG_DARK)
        self._build_pipeline_viewer(bottom_container)
        self._build_terminal(bottom_container)
        self.vpaned.add(bottom_container, minsize=150)

    # ── TOOLBAR ──────────────────────────────────────────────────────────

    def _build_toolbar(self):
        toolbar = tk.Frame(self.root, bg=BG_TOOLBAR, height=44)
        toolbar.pack(fill="x", side="top")
        toolbar.pack_propagate(False)

        # Left group: file ops
        btn_style = {"bg": BUTTON_BG, "fg": BUTTON_FG, "bd": 0,
                     "padx": 10, "pady": 4, "font": ("Segoe UI", 9),
                     "activebackground": HIGHLIGHT_BG, "activeforeground": FG_TEXT,
                     "cursor": "hand2", "relief": "flat"}

        tk.Button(toolbar, text="📄 New", command=self.new_file, **btn_style).pack(side="left", padx=(8, 2), pady=6)
        tk.Button(toolbar, text="📂 Open", command=self.open_file, **btn_style).pack(side="left", padx=2, pady=6)
        tk.Button(toolbar, text="💾 Save", command=self.save_file, **btn_style).pack(side="left", padx=2, pady=6)

        # Separator
        sep = tk.Frame(toolbar, bg=BORDER_COLOR, width=1)
        sep.pack(side="left", fill="y", padx=8, pady=8)

        # Model selection
        tk.Label(toolbar, text="Model:", bg=BG_TOOLBAR, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 2))
        self.model_var = tk.StringVar(value=AVAILABLE_MODELS[0])
        model_combo = ttk.Combobox(toolbar, textvariable=self.model_var,
                                    values=AVAILABLE_MODELS, width=22)
        model_combo.pack(side="left", padx=2, pady=6)

        # Separator
        sep2 = tk.Frame(toolbar, bg=BORDER_COLOR, width=1)
        sep2.pack(side="left", fill="y", padx=8, pady=8)

        # AI toggle
        self.ai_enabled = tk.BooleanVar(value=False)
        ai_check = tk.Checkbutton(toolbar, text="🤖 AI Assist", variable=self.ai_enabled,
                                   bg=BG_TOOLBAR, fg=FG_ACCENT, selectcolor=BG_DARK,
                                   activebackground=BG_TOOLBAR, activeforeground=FG_ACCENT,
                                   font=("Segoe UI", 9), bd=0, highlightthickness=0,
                                   cursor="hand2")
        ai_check.pack(side="left", padx=4, pady=6)

        # Separator
        sep3 = tk.Frame(toolbar, bg=BORDER_COLOR, width=1)
        sep3.pack(side="left", fill="y", padx=8, pady=8)

        # TRANSLATE button (prominent)
        translate_btn = tk.Button(toolbar, text="⚡ Translate", command=self.translate,
                                   bg="#89b4fa", fg="#11111b", bd=0,
                                   padx=16, pady=4, font=("Segoe UI", 10, "bold"),
                                   activebackground="#74c7ec", activeforeground="#11111b",
                                   cursor="hand2", relief="flat")
        translate_btn.pack(side="left", padx=8, pady=6)

        # Status label (right side)
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = tk.Label(toolbar, textvariable=self.status_var,
                                      bg=BG_TOOLBAR, fg=FG_DIM,
                                      font=("Segoe UI", 9))
        self.status_label.pack(side="right", padx=12)

    # ── SIDEBAR (history) ────────────────────────────────────────────────

    def _build_sidebar(self, parent):
        sidebar = tk.Frame(parent, bg=BG_SIDEBAR, width=220)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Header
        header = tk.Frame(sidebar, bg=BG_SIDEBAR)
        header.pack(fill="x", padx=8, pady=(12, 4))
        tk.Label(header, text="📁 History", bg=BG_SIDEBAR, fg=FG_TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")

        btn_style = {"bg": BUTTON_BG, "fg": BUTTON_FG, "bd": 0,
                     "padx": 8, "pady": 2, "font": ("Segoe UI", 8),
                     "activebackground": HIGHLIGHT_BG, "activeforeground": FG_TEXT,
                     "cursor": "hand2", "relief": "flat"}
        tk.Button(header, text="+ New", command=self.new_file, **btn_style).pack(side="right")

        # Divider
        tk.Frame(sidebar, bg=BORDER_COLOR, height=1).pack(fill="x", padx=8, pady=4)

        # History list (scrollable)
        list_frame = tk.Frame(sidebar, bg=BG_SIDEBAR)
        list_frame.pack(fill="both", expand=True, padx=4, pady=4)

        self.history_canvas = tk.Canvas(list_frame, bg=BG_SIDEBAR,
                                         highlightthickness=0, bd=0)
        self.history_scrollbar = tk.Scrollbar(list_frame, orient="vertical",
                                               command=self.history_canvas.yview,
                                               bg=BG_SIDEBAR, troughcolor=BG_SIDEBAR)
        self.history_inner = tk.Frame(self.history_canvas, bg=BG_SIDEBAR)

        self.history_inner.bind("<Configure>",
            lambda e: self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all")))
        self.history_canvas.create_window((0, 0), window=self.history_inner, anchor="nw")
        self.history_canvas.configure(yscrollcommand=self.history_scrollbar.set)

        self.history_canvas.pack(side="left", fill="both", expand=True)
        self.history_scrollbar.pack(side="right", fill="y")

    # ── SPLIT-SCREEN EDITORS ─────────────────────────────────────────────

    def _build_editors(self, parent):
        # Horizontal PanedWindow for left/right split
        hpaned = tk.PanedWindow(parent, orient="horizontal",
                                 bg=BORDER_COLOR, sashwidth=4,
                                 sashrelief="flat")
        hpaned.pack(fill="both", expand=True)

        # Source editor (left)
        self.source_panel = CodeEditorPanel(hpaned, language="Python", readonly=False)
        hpaned.add(self.source_panel, minsize=300)

        # Swap button column (between panels)
        swap_frame = tk.Frame(hpaned, bg=BG_DARK, width=40)
        swap_btn = tk.Button(swap_frame, text="⇄", command=self.swap_panels,
                              bg=BUTTON_BG, fg=FG_ACCENT, bd=0,
                              font=("Segoe UI", 16, "bold"),
                              activebackground=HIGHLIGHT_BG, activeforeground=FG_ACCENT,
                              cursor="hand2", relief="flat", width=3, height=1)
        swap_btn.place(relx=0.5, rely=0.5, anchor="center")
        hpaned.add(swap_frame, minsize=40, width=40)

        # Output editor (right)
        self.output_panel = CodeEditorPanel(hpaned, language="C", readonly=True)
        hpaned.add(self.output_panel, minsize=300)

    # ── PIPELINE VIEWER ──────────────────────────────────────────────────

    def _build_pipeline_viewer(self, parent):
        # Toggle bar
        toggle_frame = tk.Frame(parent, bg=BG_DARK)
        toggle_frame.pack(fill="x")

        self.pipeline_toggle_btn = tk.Button(
            toggle_frame, text="▶ Show Pipeline", command=self.toggle_pipeline,
            bg=BUTTON_BG, fg=FG_ACCENT, bd=0,
            padx=12, pady=4, font=("Segoe UI", 9),
            activebackground=HIGHLIGHT_BG, activeforeground=FG_ACCENT,
            cursor="hand2", relief="flat")
        self.pipeline_toggle_btn.pack(side="left", padx=8, pady=4)

        # Pipeline content (initially hidden)
        self.pipeline_frame = tk.Frame(parent, bg=BG_DARK)
        # NOT packed initially — toggle shows/hides it

        # Layer buttons bar
        self.layer_bar = tk.Frame(self.pipeline_frame, bg=BG_DARK)
        self.layer_bar.pack(fill="x", padx=8, pady=(4, 0))

        self.layer_buttons = {}
        self.layer_status = {}
        layers = ["Lexer", "Parser", "Semantic", "CodeGen", "AI Gate"]
        for i, layer in enumerate(layers):
            if i > 0:
                arrow = tk.Label(self.layer_bar, text="→", bg=BG_DARK, fg=FG_DIM,
                                 font=("Consolas", 12))
                arrow.pack(side="left", padx=2)

            btn = tk.Button(self.layer_bar, text=f"○ {layer}",
                            command=lambda l=layer: self.show_layer_detail(l),
                            bg=BUTTON_BG, fg=FG_DIM, bd=0,
                            padx=10, pady=4, font=("Segoe UI", 9),
                            activebackground=HIGHLIGHT_BG, activeforeground=FG_TEXT,
                            cursor="hand2", relief="flat")
            btn.pack(side="left", padx=2)
            self.layer_buttons[layer] = btn
            self.layer_status[layer] = "pending"

        # Detail area
        self.pipeline_detail = tk.Text(self.pipeline_frame, wrap="word",
                                        bg=BG_TERMINAL, fg=FG_TEXT,
                                        font=("Consolas", 10), height=8,
                                        borderwidth=0, padx=8, pady=8,
                                        relief="flat", state="disabled")
        self.pipeline_detail.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        # Tag config for pipeline detail
        self.pipeline_detail.tag_configure("header", foreground=FG_ACCENT,
                                            font=("Consolas", 10, "bold"))
        self.pipeline_detail.tag_configure("ok", foreground=FG_GREEN)
        self.pipeline_detail.tag_configure("error", foreground=FG_RED)
        self.pipeline_detail.tag_configure("info", foreground=FG_DIM)

    # ── TERMINAL ─────────────────────────────────────────────────────────

    def _build_terminal(self, parent):
        term_container = tk.Frame(parent, bg=BG_DARK)
        term_container.pack(fill="both", expand=True)

        # Terminal header
        term_header = tk.Frame(term_container, bg=BG_DARK)
        term_header.pack(fill="x", padx=8, pady=(4, 0))

        tk.Label(term_header, text="⌨ Terminal", bg=BG_DARK, fg=FG_TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        btn_style = {"bd": 0, "padx": 10, "pady": 3, "font": ("Segoe UI", 9),
                     "activebackground": HIGHLIGHT_BG, "activeforeground": FG_TEXT,
                     "cursor": "hand2", "relief": "flat"}

        tk.Button(term_header, text="Clear", command=self.clear_terminal,
                  bg=BUTTON_BG, fg=BUTTON_FG, **btn_style).pack(side="right", padx=2)

        # Run buttons with dropdown-style
        run_c_btn = tk.Button(term_header, text="▶ Run C",
                               command=self.run_c_code,
                               bg="#a6e3a1", fg="#11111b", **btn_style)
        run_c_btn.pack(side="right", padx=2)

        run_py_btn = tk.Button(term_header, text="▶ Run Python",
                                command=self.run_python_code,
                                bg="#89b4fa", fg="#11111b", **btn_style)
        run_py_btn.pack(side="right", padx=2)

        # Terminal output
        self.terminal = tk.Text(term_container, wrap="word",
                                 bg=BG_TERMINAL, fg=FG_TEXT,
                                 font=("Consolas", 10), height=8,
                                 borderwidth=0, padx=8, pady=8,
                                 relief="flat", state="disabled")
        term_scroll = tk.Scrollbar(term_container, command=self.terminal.yview,
                                    bg=BG_TERMINAL, troughcolor=BG_TERMINAL)
        self.terminal.config(yscrollcommand=term_scroll.set)
        term_scroll.pack(side="right", fill="y", padx=(0, 8), pady=(0, 8))
        self.terminal.pack(fill="both", expand=True, padx=(8, 0), pady=(4, 8))

        # Terminal tags
        self.terminal.tag_configure("cmd", foreground=FG_ACCENT, font=("Consolas", 10, "bold"))
        self.terminal.tag_configure("stdout", foreground=FG_TEXT)
        self.terminal.tag_configure("stderr", foreground=FG_RED)
        self.terminal.tag_configure("info", foreground=FG_DIM)
        self.terminal.tag_configure("success", foreground=FG_GREEN)

    # ──────────────────────────────────────────────────────────────────────
    #  ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def new_file(self):
        self.source_panel.set_text("")
        self.output_panel.set_text("")
        self.current_file = None
        self.pipeline_results = None
        self._reset_pipeline_status()
        self.status_var.set("New file")

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open Python Source",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
            initialdir=os.path.join(PROJECT_ROOT, "tests", "programs"))
        if path:
            with open(path, "r") as f:
                content = f.read()
            self.source_panel.set_text(content)
            self.source_panel.lang_var.set("Python")
            self.current_file = path
            self.status_var.set(f"Opened: {os.path.basename(path)}")

    def save_file(self):
        content = self.source_panel.get_text()
        if self.current_file:
            path = self.current_file
        else:
            path = filedialog.asksaveasfilename(
                title="Save Source",
                defaultextension=".py",
                filetypes=[("Python files", "*.py"), ("C files", "*.c"), ("All files", "*.*")])
        if path:
            with open(path, "w") as f:
                f.write(content)
            self.current_file = path
            self.status_var.set(f"Saved: {os.path.basename(path)}")

    def swap_panels(self):
        """Swap the source and output panel contents and language labels."""
        src_text = self.source_panel.get_text()
        src_lang = self.source_panel.lang_var.get()
        out_text = self.output_panel.get_text()
        out_lang = self.output_panel.lang_var.get()

        self.source_panel.set_text(out_text)
        self.source_panel.lang_var.set(out_lang)
        self.output_panel.set_text(src_text)
        self.output_panel.lang_var.set(src_lang)

        self.source_panel.highlight_syntax()
        self.output_panel.highlight_syntax()

    def translate(self):
        """Run the compiler pipeline and display results."""
        source = self.source_panel.get_text()
        if not source.strip():
            messagebox.showwarning("Empty Source", "Please enter or open some Python source code first.")
            return

        self.status_var.set("Translating...")
        self._reset_pipeline_status()
        self.root.update_idletasks()

        # Run in a thread to keep UI responsive
        threading.Thread(target=self._do_translate, args=(source,), daemon=True).start()

    def _do_translate(self, source):
        use_ai = self.ai_enabled.get()
        model = self.model_var.get()
        log_path = os.path.join(REPORTS_DIR, "ai_invocation_log.md")

        try:
            c_code, stats, results = compile_source_detailed(
                source, use_ai=use_ai, model=model, log_path=log_path
            )
            self.pipeline_results = results

            # Update UI from main thread
            self.root.after(0, lambda: self._on_translate_success(c_code, stats, results, source))

        except (LexError, ParseError, SemanticError, RuntimeError) as e:
            self.root.after(0, lambda: self._on_translate_error(str(e)))

    def _on_translate_success(self, c_code, stats, results, source):
        self.output_panel.set_text(c_code)
        self.output_panel.lang_var.set("C")
        self.output_panel.highlight_syntax()

        # Update pipeline status
        self._update_pipeline_from_results(results)

        ai_info = f"AI: {stats['ai_calls_used']}/{stats['total_ai_eligible_calls']}"
        self.status_var.set(f"✓ Translation complete  |  {ai_info}")

        # Auto-save to history
        self._auto_save_history(source, c_code, stats)

    def _on_translate_error(self, error_msg):
        self.status_var.set(f"✗ Error: {error_msg[:80]}")
        self._write_terminal(f"Compile Error: {error_msg}\n", "stderr")

        # Update pipeline with partial results if available
        if self.pipeline_results:
            self._update_pipeline_from_results(self.pipeline_results)

    # ── Pipeline Viewer Actions ──────────────────────────────────────────

    def toggle_pipeline(self):
        if self.pipeline_visible:
            self.pipeline_frame.pack_forget()
            self.pipeline_toggle_btn.config(text="▶ Show Pipeline")
            self.pipeline_visible = False
        else:
            self.pipeline_frame.pack(fill="both", expand=True, before=self.pipeline_frame.master.winfo_children()[-1])
            self.pipeline_toggle_btn.config(text="▼ Hide Pipeline")
            self.pipeline_visible = True

    def _reset_pipeline_status(self):
        for layer, btn in self.layer_buttons.items():
            btn.config(text=f"○ {layer}", fg=FG_DIM)
            self.layer_status[layer] = "pending"

    def _update_pipeline_from_results(self, results):
        stages = results.get("stages", {})
        mapping = {
            "Lexer": "lexer",
            "Parser": "parser",
            "Semantic": "semantic",
            "CodeGen": "codegen",
            "AI Gate": "ai_gate",
        }
        for display_name, key in mapping.items():
            if key in stages:
                status = stages[key].get("status", "pending")
                if status == "ok" or status == "used":
                    self.layer_buttons[display_name].config(
                        text=f"✓ {display_name}", fg=FG_GREEN)
                    self.layer_status[display_name] = "ok"
                elif status == "skipped":
                    self.layer_buttons[display_name].config(
                        text=f"○ {display_name}", fg=FG_YELLOW)
                    self.layer_status[display_name] = "skipped"
                elif status == "error":
                    self.layer_buttons[display_name].config(
                        text=f"✗ {display_name}", fg=FG_RED)
                    self.layer_status[display_name] = "error"

    def show_layer_detail(self, layer_name):
        """Display details for a clicked pipeline layer."""
        if not self.pipeline_results:
            self._write_pipeline_detail("No pipeline data yet. Click ⚡ Translate first.\n", "info")
            return

        mapping = {"Lexer": "lexer", "Parser": "parser", "Semantic": "semantic",
                    "CodeGen": "codegen", "AI Gate": "ai_gate"}
        key = mapping.get(layer_name)
        data = self.pipeline_results.get("stages", {}).get(key)

        self.pipeline_detail.config(state="normal")
        self.pipeline_detail.delete("1.0", "end")

        if not data:
            self.pipeline_detail.insert("end", f"{layer_name}: no data available\n", "info")
            self.pipeline_detail.config(state="disabled")
            return

        # Header
        status = data.get("status", "unknown")
        status_tag = "ok" if status in ("ok", "used") else "error" if status == "error" else "info"
        self.pipeline_detail.insert("end", f"═══ {layer_name} ═══  [{status.upper()}]\n\n", "header")

        if status == "error":
            self.pipeline_detail.insert("end", f"Error: {data.get('error', 'unknown')}\n", "error")
            self.pipeline_detail.config(state="disabled")
            return

        # Layer-specific detail
        if key == "lexer":
            count = data.get("token_count", 0)
            self.pipeline_detail.insert("end", f"Total tokens: {count}\n\n", "info")
            tokens = data.get("tokens", [])
            for kind, value, line in tokens[:100]:  # cap display
                val_display = repr(value) if value.strip() else '""'
                self.pipeline_detail.insert("end", f"  L{line:3d}  {kind:10s}  {val_display}\n")
            if len(tokens) > 100:
                self.pipeline_detail.insert("end", f"\n  ... and {len(tokens) - 100} more tokens\n", "info")

        elif key == "parser":
            fc = data.get("function_count", 0)
            self.pipeline_detail.insert("end", f"Functions parsed: {fc}\n\n", "info")
            ast_data = data.get("ast", {})
            pretty = json.dumps(ast_data, indent=2, default=str)
            # Truncate for display
            if len(pretty) > 5000:
                pretty = pretty[:5000] + "\n\n... (truncated)"
            self.pipeline_detail.insert("end", pretty + "\n")

        elif key == "semantic":
            funcs = data.get("functions", {})
            self.pipeline_detail.insert("end", f"Functions in symbol table: {len(funcs)}\n\n", "info")
            for fname, info in funcs.items():
                params_str = ", ".join(f"{n}: {t}" for n, t in info["params"])
                self.pipeline_detail.insert("end",
                    f"  def {fname}({params_str}) → {info['return_type']}\n", "ok")

        elif key == "codegen":
            ai_used = data.get("ai_calls_used", 0)
            total = data.get("total_call_sites", 0)
            self.pipeline_detail.insert("end",
                f"AI calls used: {ai_used} / {total} eligible\n", "info")
            if ai_used == 0:
                self.pipeline_detail.insert("end",
                    "\n✓ Fully deterministic — no AI fallbacks needed\n", "ok")
            else:
                pct = (ai_used / total * 100) if total > 0 else 0
                self.pipeline_detail.insert("end",
                    f"\n⚠ AI-invocation rate: {pct:.1f}%\n", "info")
                if pct > 15:
                    self.pipeline_detail.insert("end",
                        "  Warning: >15% AI rate — consider adding static rules\n", "error")

        elif key == "ai_gate":
            calls = data.get("calls", 0)
            st = data.get("status", "skipped")
            if st == "skipped":
                self.pipeline_detail.insert("end",
                    "AI gate was not invoked (all constructs had static rules)\n", "info")
            else:
                self.pipeline_detail.insert("end",
                    f"AI gate processed {calls} call(s)\n", "info")
                self.pipeline_detail.insert("end",
                    f"\nSee reports/ai_invocation_log.md for full details\n", "info")

        self.pipeline_detail.config(state="disabled")

    def _write_pipeline_detail(self, text, tag="info"):
        self.pipeline_detail.config(state="normal")
        self.pipeline_detail.delete("1.0", "end")
        self.pipeline_detail.insert("end", text, tag)
        self.pipeline_detail.config(state="disabled")

    # ── Terminal Actions ─────────────────────────────────────────────────

    def run_python_code(self):
        source = self.source_panel.get_text()
        if not source.strip():
            self._write_terminal("No source code to run.\n", "stderr")
            return

        self._write_terminal("$ python <source>\n", "cmd")
        threading.Thread(target=self._exec_python, args=(source,), daemon=True).start()

    def _exec_python(self, source):
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False,
                                          dir=PROJECT_ROOT) as f:
            # Add a main() call since the subset requires functions
            f.write(source)
            if "def main" in source:
                f.write("\nmain()\n")
            path = f.name
        try:
            result = subprocess.run(
                [sys.executable, path],
                capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT)
            self.root.after(0, lambda: self._show_run_result(result))
        except subprocess.TimeoutExpired:
            self.root.after(0, lambda: self._write_terminal("Timeout (30s exceeded)\n", "stderr"))
        except Exception as e:
            self.root.after(0, lambda: self._write_terminal(f"Error: {e}\n", "stderr"))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def run_c_code(self):
        c_code = self.output_panel.get_text()
        if not c_code.strip():
            self._write_terminal("No C code to compile. Translate first.\n", "stderr")
            return

        self._write_terminal("$ gcc prog.c -I src -lm -o prog && ./prog\n", "cmd")
        threading.Thread(target=self._exec_c, args=(c_code,), daemon=True).start()

    def _exec_c(self, c_code):
        with tempfile.TemporaryDirectory() as tmpdir:
            c_path = os.path.join(tmpdir, "prog.c")
            bin_path = os.path.join(tmpdir, "prog")
            if sys.platform == "win32":
                bin_path += ".exe"
            with open(c_path, "w") as f:
                f.write(c_code)

            # Compile
            compile_result = subprocess.run(
                ["gcc", c_path, "-I", PROJECT_ROOT + "/src", "-lm", "-o", bin_path],
                capture_output=True, text=True, cwd=PROJECT_ROOT)

            if compile_result.returncode != 0:
                self.root.after(0, lambda: self._write_terminal(
                    f"gcc failed:\n{compile_result.stderr}\n", "stderr"))
                return

            self.root.after(0, lambda: self._write_terminal("Compilation successful ✓\n", "success"))

            # Run
            try:
                run_result = subprocess.run(
                    [bin_path], capture_output=True, text=True, timeout=30, cwd=PROJECT_ROOT)
                self.root.after(0, lambda: self._show_run_result(run_result))
            except subprocess.TimeoutExpired:
                self.root.after(0, lambda: self._write_terminal("Timeout (30s exceeded)\n", "stderr"))
            except Exception as e:
                self.root.after(0, lambda: self._write_terminal(f"Error: {e}\n", "stderr"))

    def _show_run_result(self, result):
        if result.stdout:
            self._write_terminal(result.stdout, "stdout")
        if result.stderr:
            self._write_terminal(result.stderr, "stderr")
        if result.returncode == 0:
            self._write_terminal(f"\nProcess exited with code 0 ✓\n", "success")
        else:
            self._write_terminal(f"\nProcess exited with code {result.returncode}\n", "stderr")

    def _write_terminal(self, text, tag="stdout"):
        self.terminal.config(state="normal")
        self.terminal.insert("end", text, tag)
        self.terminal.see("end")
        self.terminal.config(state="disabled")

    def clear_terminal(self):
        self.terminal.config(state="normal")
        self.terminal.delete("1.0", "end")
        self.terminal.config(state="disabled")

    # ── History ──────────────────────────────────────────────────────────

    def _load_history(self):
        """Load saved history files from disk."""
        self.history = []
        if not os.path.exists(HISTORY_DIR):
            return
        for fname in sorted(os.listdir(HISTORY_DIR), reverse=True):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(HISTORY_DIR, fname), "r") as f:
                        entry = json.load(f)
                    self.history.append(entry)
                except (json.JSONDecodeError, OSError):
                    continue
        self._refresh_history_ui()

    def _auto_save_history(self, source, c_code, stats):
        """Auto-save the current translation to history."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "filename": os.path.basename(self.current_file) if self.current_file else "untitled.py",
            "source": source,
            "output": c_code,
            "model": self.model_var.get(),
            "ai_enabled": self.ai_enabled.get(),
            "stats": stats,
        }
        fname = datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
        try:
            with open(os.path.join(HISTORY_DIR, fname), "w") as f:
                json.dump(entry, f, indent=2)
        except OSError:
            pass

        self.history.insert(0, entry)
        self._refresh_history_ui()

    def _refresh_history_ui(self):
        """Rebuild the history sidebar list."""
        for widget in self.history_inner.winfo_children():
            widget.destroy()

        for i, entry in enumerate(self.history[:50]):  # cap at 50
            ts = entry.get("timestamp", "Unknown")
            try:
                dt = datetime.fromisoformat(ts)
                display_time = dt.strftime("%b %d  %H:%M")
            except (ValueError, TypeError):
                display_time = ts[:16]

            name = entry.get("filename", "untitled.py")
            ai_flag = "🤖" if entry.get("ai_enabled") else ""

            item_frame = tk.Frame(self.history_inner, bg=BG_SIDEBAR, cursor="hand2")
            item_frame.pack(fill="x", padx=4, pady=1)

            # Bind click to restore
            item_frame.bind("<Button-1>", lambda e, idx=i: self._restore_history(idx))

            lbl_name = tk.Label(item_frame, text=f"{ai_flag} {name}",
                                bg=BG_SIDEBAR, fg=FG_TEXT, font=("Segoe UI", 9),
                                anchor="w", cursor="hand2")
            lbl_name.pack(fill="x", padx=4)
            lbl_name.bind("<Button-1>", lambda e, idx=i: self._restore_history(idx))

            lbl_time = tk.Label(item_frame, text=display_time,
                                bg=BG_SIDEBAR, fg=FG_DIM, font=("Segoe UI", 8),
                                anchor="w", cursor="hand2")
            lbl_time.pack(fill="x", padx=4)
            lbl_time.bind("<Button-1>", lambda e, idx=i: self._restore_history(idx))

            # Hover effect
            for widget in (item_frame, lbl_name, lbl_time):
                widget.bind("<Enter>", lambda e, f=item_frame: f.config(bg=HIGHLIGHT_BG))
                widget.bind("<Leave>", lambda e, f=item_frame: f.config(bg=BG_SIDEBAR))
                widget.bind("<Enter>", lambda e, f=item_frame, n=lbl_name, t=lbl_time: (
                    f.config(bg=HIGHLIGHT_BG), n.config(bg=HIGHLIGHT_BG), t.config(bg=HIGHLIGHT_BG)),
                    add=True)
                widget.bind("<Leave>", lambda e, f=item_frame, n=lbl_name, t=lbl_time: (
                    f.config(bg=BG_SIDEBAR), n.config(bg=BG_SIDEBAR), t.config(bg=BG_SIDEBAR)),
                    add=True)

    def _restore_history(self, index):
        """Restore a history entry into the editors."""
        if index >= len(self.history):
            return
        entry = self.history[index]
        self.source_panel.set_text(entry.get("source", ""))
        self.source_panel.lang_var.set("Python")
        self.output_panel.set_text(entry.get("output", ""))
        self.output_panel.lang_var.set("C")
        self.source_panel.highlight_syntax()
        self.output_panel.highlight_syntax()

        model = entry.get("model", AVAILABLE_MODELS[0])
        self.model_var.set(model)
        self.ai_enabled.set(entry.get("ai_enabled", False))

        name = entry.get("filename", "untitled.py")
        self.status_var.set(f"Restored: {name}")

    # ── Sample code ──────────────────────────────────────────────────────

    def _load_sample_code(self):
        """Load a sample program on startup so the UI isn't empty."""
        sample_path = os.path.join(PROJECT_ROOT, "tests", "programs", "factorial.py")
        if os.path.exists(sample_path):
            with open(sample_path, "r") as f:
                self.source_panel.set_text(f.read())
            self.current_file = sample_path
            self.status_var.set(f"Loaded sample: factorial.py")

    # ── Run ──────────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ENTRY POINT                                                            ║
# ╚══════════════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    app = TranslationCompilerApp()
    app.run()
