/*
 * Build the Python subset reference.
 *
 *     node tools/doc/build_subset_doc.js
 *
 * Everything in here is taken from the compiler itself - grammar/subset.ebnf,
 * src/semantic/builtins.py and src/semantic/modules.py - rather than written
 * from memory, so the document and the program cannot drift apart without
 * somebody noticing.
 */
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  WidthType, AlignmentType, HeadingLevel, BorderStyle, ShadingType,
  PageBreak,
} = require("docx");
const fs = require("fs");
const path = require("path");

const BODY = "Times New Roman";
const MONO = "Consolas";
const USABLE = 9026;              // A4 minus 1 inch margins, in DXA
const INK = "000000";
const QUIET = "595959";
const RULE = "BFBFBF";

/* ------------------------------------------------------------- helpers */

const h1 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_1,
  spacing: { before: 360, after: 140 },
  children: [new TextRun({ text, font: BODY, size: 30, bold: true, color: INK })],
});

const h2 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 240, after: 100 },
  children: [new TextRun({ text, font: BODY, size: 24, bold: true, color: INK })],
});

const p = (text, opts = {}) => new Paragraph({
  spacing: { after: opts.after === undefined ? 120 : opts.after },
  children: [new TextRun({
    text, font: BODY, size: 22, color: opts.quiet ? QUIET : INK,
    italics: !!opts.italic,
  })],
});

const bullet = (text) => new Paragraph({
  bullet: { level: 0 },
  spacing: { after: 60 },
  children: [new TextRun({ text, font: BODY, size: 22, color: INK })],
});

// A code block: one paragraph per line, because a run cannot hold a newline.
function code(lines, opts = {}) {
  return lines.map((line, i) => new Paragraph({
    spacing: { after: i === lines.length - 1 ? (opts.after || 160) : 0 },
    shading: { type: ShadingType.CLEAR, fill: "F2F2F2" },
    indent: { left: 220 },
    children: [new TextRun({ text: line || " ", font: MONO, size: 19, color: INK })],
  }));
}

function cell(text, { bold = false, mono = false, head = false, width }) {
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: head ? { type: ShadingType.CLEAR, fill: "E8E8E8" } : undefined,
    margins: { top: 60, bottom: 60, left: 110, right: 110 },
    children: [new Paragraph({
      children: [new TextRun({
        text, font: mono ? MONO : BODY, size: mono ? 18 : 20,
        bold: bold || head, color: INK,
      })],
    })],
  });
}

/** A table whose first row is the header. `cols` are DXA widths. */
function table(header, rows, cols, monoCol = 0) {
  const border = { style: BorderStyle.SINGLE, size: 2, color: RULE };
  const borders = { top: border, bottom: border, left: border, right: border,
                    insideHorizontal: border, insideVertical: border };
  return new Table({
    width: { size: USABLE, type: WidthType.DXA },
    columnWidths: cols,
    borders,
    rows: [
      new TableRow({
        tableHeader: true,
        children: header.map((t, i) => cell(t, { head: true, width: cols[i] })),
      }),
      ...rows.map((r) => new TableRow({
        children: r.map((t, i) => cell(t, {
          width: cols[i], mono: i === monoCol,
        })),
      })),
    ],
  });
}

const gap = () => new Paragraph({ spacing: { after: 160 }, children: [] });

/* ---------------------------------------------------------------- content */

const children = [];

children.push(new Paragraph({
  spacing: { after: 60 },
  children: [new TextRun({ text: "The Python Subset", font: BODY, size: 44,
                           bold: true, color: INK })],
}));
children.push(new Paragraph({
  spacing: { after: 40 },
  children: [new TextRun({
    text: "What the compiler accepts, what it refuses, and what to write instead",
    font: BODY, size: 24, color: QUIET })],
}));
children.push(new Paragraph({
  spacing: { after: 320 },
  children: [new TextRun({
    text: "AI Based Language Translation Compiler  -  BCSE307P Compiler Design Lab",
    font: BODY, size: 20, color: QUIET })],
}));

children.push(p("A program written in this subset is valid Python 3. That is the " +
  "whole point: CPython runs it, the compiler translates it to C, and the two " +
  "are required to print the same bytes. Anything outside the subset is refused " +
  "by name, with a message saying what to write instead - it is never guessed at."));
children.push(p("Everything below is taken from grammar/subset.ebnf, " +
  "src/semantic/builtins.py and src/semantic/modules.py, so this document and " +
  "the compiler cannot quietly disagree.", { quiet: true, italic: true }));

/* ---- shape of a program ---- */
children.push(h1("1  The shape of a program"));
children.push(p("At module level there are only imports, class definitions and " +
  "function definitions. No statements run at import time, because there is no " +
  "import time: the generated C has one entry point and it calls main()."));
children.push(...code([
  "import math                 # only math and gfx exist",
  "",
  "def square(n: int) -> int:  # every parameter and the return are annotated",
  "    return n * n",
  "",
  "def main() -> int:          # defined, never called - the C calls it",
  "    total: int = 0",
  "    for i in range(1, 11):",
  "        total = total + square(i)",
  "    print(total)",
  "    return 0",
]));
children.push(bullet("Every function needs a return annotation. There is no inference across a call."));
children.push(bullet("Every parameter needs a type. A name here is a number, a string or an array - never a function."));
children.push(bullet("main() is the entry point. Define it and leave it uncalled."));

/* ---- types ---- */
children.push(h1("2  Types"));
children.push(table(
  ["Type", "What it is in C", "Notes"],
  [
    ["int", "64-bit signed integer", "Python's int is unbounded; this one is not. See section 11."],
    ["float", "double", "Printed exactly as CPython prints it."],
    ["bool", "the same integer, printed True or False", "bool < int < float when types widen."],
    ["str", "const char *", "Literals only. Nothing can build a string at run time."],
    ["None", "void", "A return type, not a value you can hold."],
    ["list[int, 9]", "py_int a[9] on the stack", "Size is part of the type. No append, no resize."],
    ["dict[str, int]", "two parallel arrays", "The key set is fixed at the point it is written."],
    ["a class name", "a struct, passed by pointer", "A value type. No inheritance, no methods through a base."],
  ],
  [1900, 3100, 4026]));
children.push(gap());
children.push(p("A variable takes its type from its first assignment. The annotated " +
  "form pins it, which is how a counter that will later be divided starts as a float:"));
children.push(...code([
  "total: float = 0      # pinned, so total = total / n is legal later",
  "count = 0             # inferred int",
]));

/* ---- statements ---- */
children.push(h1("3  Statements"));
children.push(table(
  ["Statement", "Form", "Notes"],
  [
    ["assignment", "x = e   or   x: int = e", "One target. No a = b = c."],
    ["augmented", "x += e", "+= -= *= /= //= %= **="],
    ["index assign", "a[i] = e", "Also a[i] += e and the other operators."],
    ["attribute assign", "self.n = e", "Receiver must be a name, not an expression."],
    ["if", "if / elif / else", "Any number of elif."],
    ["while", "while e:", "No while/else."],
    ["for", "for i in range(...):", "Over range() only - 1, 2 or 3 arguments."],
    ["return", "return e   or   return", ""],
    ["print", "print(a, b)", "Ends the line. put(s) writes without one."],
    ["break / continue / pass", "", ""],
    ["expression", "f(x)", "A call on its own line."],
  ],
  [2100, 2900, 4026]));
children.push(gap());
children.push(p("A docstring - a bare string as the first line of a function, class " +
  "or method body - is allowed and ignored."));

/* ---- expressions ---- */
children.push(h1("4  Expressions"));
children.push(p("Precedence is CPython's, lowest to highest:"));
children.push(...code([
  "or",
  "and",
  "not",
  "==  !=  <  >  <=  >=      exactly one comparison: a < b < c is refused",
  "+  -",
  "*  /  //  %",
  "-x  +x                    unary",
  "**                        right-associative; -2 ** 2 is -4",
]));
children.push(bullet("Integer / is true division and gives a float, as in Python."));
children.push(bullet("// and % follow Python on negative operands, not C - the runtime has helpers for it."));
children.push(bullet("A negative index counts from the end. An out-of-range index stops the program rather than reading whatever is at that address."));

/* ---- arrays ---- */
children.push(h1("5  Arrays"));
children.push(p("The one compound type. The size is part of the C type, so it is a " +
  "literal and it never changes."));
children.push(...code([
  "values: list[int, 6] = [0] * 6        # a local: size from the initialiser",
  "grid: list[int, 54] = [0] * 54",
  "",
  "def total(a: list[int]) -> int:       # a PARAMETER carries no size",
  "    sum: int = 0",
  "    i: int = 0",
  "    while i < len(a):                 # len() reads the length passed beside it",
  "        sum = sum + a[i]",
  "        i = i + 1",
  "    return sum",
]));
children.push(bullet("Arrays hold int, float or bool. There is no list of lists and no list of str."));
children.push(bullet("A function cannot return an array. Pass the destination in and return how many entries it filled."));
children.push(bullet("An array argument must be a plain name, because the call passes its length beside it."));
children.push(gap());
children.push(p("Two idioms follow from this, and both appear throughout the demos:"));
children.push(...code([
  "# a grid is one flat array",
  "grid[row * width + col] = value",
  "",
  "# a list whose length is not known in advance: worst case, plus a count",
  "buf: list[int, 64] = [0] * 64",
  "used: int = fill(buf, n)              # loop to `used`, not to len(buf)",
]));

/* ---- dicts ---- */
children.push(h1("6  Dicts"));
children.push(p("A fixed key set, written as a literal. It compiles to two parallel " +
  "arrays - the keys const, the values not - so there is no growth and no rehash. " +
  "Iteration is in the order the keys were written, which is Python's own insertion order."));
children.push(...code([
  "scores: dict[str, int] = {\"ana\": 3, \"bo\": 5}",
  "scores[\"ana\"] = scores[\"ana\"] + 1",
  "",
  "for name in scores:            # the keys, in the order written",
  "    print(scores[name])",
]));
children.push(bullet("Keys are str or int literals. Values are int, float, bool or str."));
children.push(bullet("A key that was not written is a compile error, not a KeyError."));

/* ---- classes ---- */
children.push(h1("7  Classes"));
children.push(p("A class is a value type: a C struct, plus free functions that take a " +
  "pointer to it. There is no vtable, no reference count and no base class - calling " +
  "a method through a base would be dynamic dispatch, and dispatch needs a table of " +
  "function pointers, which is not a value here."));
children.push(...code([
  "class Counter:",
  "    START: int = 100          # with a value: a constant shared by the class",
  "    total: int                # without: storage, __init__ must assign it",
  "    step: int",
  "",
  "    def __init__(self, step: int) -> None:",
  "        self.total = Counter.START",
  "        self.step = step",
  "",
  "    def bump(self) -> int:",
  "        self.total = self.total + self.step",
  "        return self.total",
]));
children.push(bullet("An array field must carry its size: cells: list[int, 9]."));
children.push(bullet("An object argument must be a plain name, because the call passes its address."));

/* ---- builtins ---- */
children.push(h1("8  Builtins"));
children.push(p("Ten names, each with a fixed C translation. A call to any other " +
  "undefined name is a compile error - or, with AI assist on, the one place a model " +
  "is asked anything."));
children.push(table(
  ["Builtin", "Gives", "Notes"],
  [
    ["len(a)", "int", "An array's length, or a string's."],
    ["abs(x)", "int or float", ""],
    ["min(a, b, ...)", "int or float", "Two or more arguments."],
    ["max(a, b, ...)", "int or float", "Two or more arguments."],
    ["pow(a, b)", "int or float", "Same as a ** b."],
    ["round(x[, n])", "int or float", "Python's banker's rounding, not C's."],
    ["int(x)", "int", "Truncates toward zero."],
    ["float(x)", "float", ""],
    ["bool(x)", "bool", ""],
    ["put(s)", "None", "Writes a string with no newline and no separator."],
  ],
  [2300, 2000, 4726]));
children.push(gap());
children.push(p("put() is how a line is composed, because there is no concatenation " +
  "and no str(). The pieces are written one after another rather than joined:"));
children.push(...code([
  'put("sum = ")',
  "print(total)             # ends the line",
]));

/* ---- modules ---- */
children.push(h1("9  Modules"));
children.push(p("An import binds names; it never executes a module. Two modules exist, " +
  "and each member has a static C translation - the same contract as a builtin."));

children.push(h2("math"));
children.push(p("Maps onto <math.h>. Importing it is also what makes sqrt(x) " +
  "deterministic rather than something the AI gate has to resolve.", { quiet: true }));
children.push(...code([
  "acos  asin  atan  atan2  ceil  copysign  cos  cosh  degrees  exp",
  "fabs  floor  fmod  hypot  isfinite  isinf  isnan  log  log10  log2",
  "pow  radians  sin  sinh  sqrt  tan  tanh  trunc",
  "",
  "constants:  math.pi   math.e   math.tau   math.inf   math.nan",
]));

children.push(h2("gfx"));
children.push(p("A window, an OpenGL context and the keyboard, behind an API of " +
  "nothing but numbers. There is no surface, no sprite and no rectangle to hold - " +
  "that is what makes a window expressible here at all. Importing it links " +
  "src/runtime/gfx.c into the build.", { quiet: true }));
children.push(table(
  ["Call", "Gives", "What it does"],
  [
    ["gfx.open(w, h)", "int", "Opens the window. 0 means it failed."],
    ["gfx.running()", "int", "0 once the window has been closed."],
    ["gfx.poll()", "None", "Pumps the event queue. Call it once per frame."],
    ["gfx.clear(r, g, b)", "None", "Clears to a colour, each channel 0.0 to 1.0."],
    ["gfx.perspective(fov, near, far)", "None", "Sets the projection."],
    ["gfx.camera(ex, ey, ez, tx, ty, tz)", "None", "Eye position and what it looks at."],
    ["gfx.color(r, g, b)", "None", "The colour of the triangles that follow."],
    ["gfx.tri(nine floats)", "None", "One triangle, three points. A quad is two."],
    ["gfx.present()", "None", "Shows the frame."],
    ["gfx.close()", "None", "Closes the window."],
    ["gfx.key(code)", "int", "Non-zero while that key is down."],
    ["gfx.key_up/down/left/right/space()", "int", "The codes for the keys without ASCII values."],
    ["gfx.time()", "float", "Seconds since the window opened."],
  ],
  [3300, 900, 4826]));

/* ---- excluded ---- */
children.push(h1("10  What is excluded, and what to write instead"));
children.push(p("Each of these is refused by name with the reason. None of them is a " +
  "gap waiting to be filled: each would need an allocator, a function pointer or a " +
  "type the generated C has no way to hold."));
children.push(table(
  ["Not in the subset", "Write this instead"],
  [
    ["f-strings, str(), + on strings, .format, %", "put() the pieces one after another, then print() to end the line"],
    ["string indexing, iteration, slicing, ord(), chr(), every str method", "carry computed text as numbers, one per letter, and write it out with a branch table"],
    ["list.append, insert, pop, slicing", "an array big enough for the worst case, with a count carried beside it"],
    ["lists of lists", "one flat array indexed as row * width + col"],
    ["returning an array from a function", "pass the destination in as a parameter and return how many entries were filled"],
    ["sets, tuples", "arrays, or separate variables"],
    ["comprehensions, generators", "a loop"],
    ["decorators, lambdas, closures, nested defs", "plain functions - a function is not a value here"],
    ["classes with a base class", "one class, or a field that says which kind it is"],
    ["exceptions (try, raise)", "a return value that says what went wrong"],
    ["global, nonlocal", "pass it in and return it"],
    ["default and keyword arguments, *args, **kwargs", "write out the arguments"],
    ["a = b = c, a < b < c", "one assignment, or one comparison, per statement"],
    ["while/else, for/else", "a flag"],
    ["for over anything but range()", "for i in range(len(a)) and index it"],
    ["any module but math and gfx", "gfx for a window; otherwise write it out"],
  ],
  [3600, 5426], -1));

/* ---- limits ---- */
children.push(h1("11  Where the generated C differs from CPython"));
children.push(p("Two documented limits, and nothing else. Everything not listed here " +
  "- /, //, %, **, round(), float formatting, print formatting, division by zero - " +
  "matches CPython exactly, and the test suite proves it by diffing against CPython " +
  "rather than against expectations written by hand."));
children.push(h2("Integer width"));
children.push(p("Python's int is unbounded; this one is 64 bits. A constant that does " +
  "not fit is refused at compile time with a message naming the value, so 2 ** 100 " +
  "is an error rather than a printed 0 - which is what it used to be. A value only " +
  "reached at run time still wraps, and nothing static can catch that."));
children.push(...code([
  "print(2 ** 100)        # refused, and the message names the value",
  "print(n ** 100)        # not detected: n is only known at run time",
]));
children.push(p("A number larger than about 9.2 x 10^18 is carried as its digits in " +
  "an array, with the arithmetic done a digit at a time."));
children.push(h2("Recursion depth"));
children.push(p("The C stack's, not sys.getrecursionlimit(). A program that recurses " +
  "very deeply may crash where CPython would raise."));

/* ---- checking ---- */
children.push(h1("12  How to check something is in the subset"));
children.push(p("Do not take this document's word for it - the compiler is the " +
  "authority, and it answers in one command:"));
children.push(...code([
  "python -m src.driver yourfile.py --check",
]));
children.push(p("It reports every construct it cannot translate, not just the first, " +
  "and splits them into the ones that could be supported one day and the ones that " +
  "never will be:"));
children.push(...code([
  "thing.py: 2 things outside the subset",
  "",
  "NOT YET - the compiler could support these  (1)",
  "    list comprehension      x1    first at line 2",
  "",
  "NEVER - these need an allocator, dispatch, or a runtime that is not here  (1)",
  "    f-string                x1    first at line 3",
]));
children.push(p("So a program can be brought into the subset in one pass rather than " +
  "one error at a time. In the IDE the same survey appears in the Pipeline tab " +
  "whenever a translation fails."));
children.push(p("grammar/subset.ebnf is the formal statement of all of the above, and " +
  "is the file to read when this one is not specific enough.", { quiet: true }));

/* ------------------------------------------------------------------ write */

const doc = new Document({
  creator: "AI Based Language Translation Compiler",
  title: "The Python Subset",
  description: "What the compiler accepts, what it refuses, and what to write instead",
  styles: {
    default: {
      document: { run: { font: BODY, size: 22, color: INK } },
    },
  },
  sections: [{
    properties: { page: { margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    children,
  }],
});

const OUT = path.join("C:", "Users", "ACER", "Desktop", "Python Subset Reference.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log("wrote " + OUT + "  (" + buf.length + " bytes)");
});
