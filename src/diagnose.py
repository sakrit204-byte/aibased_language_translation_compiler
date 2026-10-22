"""
Report everything in a file that this subset cannot translate, at once.

The compiler stops at the first construct it cannot handle, which is right
for a compiler and useless for the question people actually ask: *can this
program be translated, and if not, what would it take?* Answering that one
error at a time turns a five-minute question into twenty rounds of pressing
Translate.

This walks the file with Python's own parser — a subset program is valid
Python, so `ast` can read anything the user might paste, including programs
far outside the subset — and reports every blocker with its line number, the
reason, and whether it is fixable at all.

The three verdicts matter more than the counts:

  FIX      the subset can express this; the code needs a change
  FEATURE  the compiler could support it, and does not yet
  NEVER    it needs an allocator, dynamic dispatch, or a runtime this
           compiler does not have, and no amount of work here changes that

Nothing here is a substitute for the compiler's own errors: this is a survey,
and the compiler remains the authority on any one line.
"""

import ast

from .semantic import modules

FIX = "FIX"
FEATURE = "FEATURE"
NEVER = "NEVER"

# Modules a program may import. Anything else is judged by modules.py, which
# owns the reasons.
IMPORTABLE = set(modules.MODULES)

SUPPORTED_CALLS = {
    "len", "abs", "min", "max", "pow", "round", "int", "float", "bool",
    "put", "print", "range",
}

# Names that are a runtime, not a language feature: no rule can translate a
# call into a GUI toolkit that is not there.
RUNTIME_ROOTS = {
    "tk", "tkinter", "ttk", "messagebox", "pygame", "numpy", "np", "pd",
    "pandas", "requests", "scipy", "matplotlib", "plt", "PIL", "cv2",
}


class Finding:
    def __init__(self, line, verdict, construct, reason):
        self.line = line
        self.verdict = verdict
        self.construct = construct
        self.reason = reason

    def __repr__(self):
        return "Finding(%d, %s, %r)" % (self.line, self.verdict, self.construct)


class _Walker(ast.NodeVisitor):
    def __init__(self):
        self.findings = []
        self.class_depth = 0

    def visit_Assign(self, node):
        """`self.cells = [0] * 3` in __init__ is how an array field is
        filled, so the literal there is not a finding."""
        target = node.targets[0] if node.targets else None
        if (isinstance(target, ast.Attribute)
                and _is_array_literal(node.value)):
            return
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        """`xs: list[int, 3] = [0] * 3` and friends.

        The annotation is not code — `list[int, 3]` and `dict[str, int]` read
        to Python's parser as subscripts holding tuples, and reporting those
        as tuples would flag the very syntax this subset asks for. And an
        array or dict literal in an annotated assignment is the supported
        way to write one, so it is not a finding either.
        """
        if node.value is not None and not _is_container_initialiser(node):
            self.visit(node.value)
        if node.value is not None and _is_container_initialiser(node):
            # Still look inside for anything that is a finding on its own,
            # such as a call in an element position.
            for child in ast.walk(node.value):
                if isinstance(child, (ast.Lambda, ast.ListComp, ast.DictComp,
                                      ast.GeneratorExp, ast.JoinedStr)):
                    self.visit(child)
        for child in ast.iter_child_nodes(node):
            if child is node.annotation or child is node.value:
                continue
            self.visit(child)

    def add(self, node, verdict, construct, reason):
        self.findings.append(
            Finding(getattr(node, "lineno", 0), verdict, construct, reason))

    # ---- imports ---------------------------------------------------------

    def visit_Import(self, node):
        for alias in node.names:
            self._import(node, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        self._import(node, node.module or "")
        self.generic_visit(node)

    def _import(self, node, name):
        root = name.split(".")[0]
        if root in IMPORTABLE:
            return
        if root in modules.UNSUPPORTED:
            self.add(node, NEVER, "import " + name,
                     modules.UNSUPPORTED[root].split(".")[0])
        else:
            self.add(node, NEVER, "import " + name,
                     "no such module here; importable: "
                     + ", ".join(sorted(IMPORTABLE)))

    # ---- classes and functions ------------------------------------------

    def visit_ClassDef(self, node):
        if node.bases:
            names = ", ".join(
                b.id if isinstance(b, ast.Name) else "..." for b in node.bases)
            self.add(node, NEVER, "class %s(%s)" % (node.name, names),
                     "inheritance needs dynamic dispatch, which needs a table "
                     "of function pointers, and a function is not a value here")
        for d in node.decorator_list:
            self.add(node, NEVER, "@" + _name_of(d),
                     "a decorator takes a function and returns one; there are "
                     "no function values here")
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1

    def visit_FunctionDef(self, node):
        for d in node.decorator_list:
            label = _name_of(d)
            if label in ("property", "staticmethod", "classmethod"):
                self.add(node, FEATURE, "@" + label,
                         "could be recognised as a form rather than a real "
                         "decorator; it is not yet")
            else:
                self.add(node, NEVER, "@" + label,
                         "a decorator takes a function and returns one; there "
                         "are no function values here")
        if node.returns is None:
            self.add(node, FIX, "def %s(...)" % node.name,
                     "needs a return type, e.g. `-> None`; there is no "
                     "inference across function boundaries")
        for arg in node.args.args:
            if arg.arg == "self" and self.class_depth:
                continue
            if arg.annotation is None:
                self.add(node, FIX, "parameter '%s'" % arg.arg,
                         "needs a type annotation, e.g. `%s: int`" % arg.arg)
        if node.args.defaults or node.args.kw_defaults:
            self.add(node, FEATURE, "default argument",
                     "every call would have to be rewritten to pass it; not "
                     "supported yet")
        if node.args.vararg or node.args.kwarg:
            self.add(node, NEVER, "*args / **kwargs",
                     "a variadic call needs a runtime argument list")
        # The body and the defaults are code; the annotations are not.
        for stmt in node.body:
            self.visit(stmt)
        for default in node.args.defaults:
            self.visit(default)

    visit_AsyncFunctionDef = visit_FunctionDef

    # ---- expressions -----------------------------------------------------

    def visit_Lambda(self, node):
        self.add(node, NEVER, "lambda",
                 "a lambda is a function value, and there are none here")
        self.generic_visit(node)

    def visit_ListComp(self, node):
        self.add(node, FEATURE, "list comprehension",
                 "builds a list whose length is not known until it runs")
        self.generic_visit(node)

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node):
        self.add(node, NEVER, "dict comprehension",
                 "a dict's keys are fixed when it is written here, and a "
                 "comprehension decides them while running")
        self.generic_visit(node)

    def visit_List(self, node):
        if isinstance(node.ctx, ast.Load):
            self.add(node, FIX, "list literal",
                     "a list is a fixed-size array here: annotate it, e.g. "
                     "`xs: list[int] = [0] * 9`")
        self.generic_visit(node)

    def visit_Tuple(self, node):
        if isinstance(node.ctx, ast.Store):
            self.add(node, FEATURE, "tuple unpacking",
                     "assigning to several names at once is not supported yet")
        else:
            self.add(node, NEVER, "tuple",
                     "a tuple is a value of several types at once, which this "
                     "subset has no way to hold; pass the parts separately")
        self.generic_visit(node)

    def visit_Set(self, node):
        self.add(node, FEATURE, "set literal",
                 "no set type yet; a dict with unused values does the same job")
        self.generic_visit(node)

    def visit_Slice(self, node):
        self.add(node, NEVER, "slicing",
                 "a slice makes a new sequence, which needs an allocator")
        self.generic_visit(node)

    def visit_JoinedStr(self, node):
        self.add(node, NEVER, "f-string",
                 "building a string needs an allocator")
        self.generic_visit(node)

    def visit_Raise(self, node):
        self.add(node, FEATURE, "raise",
                 "there are no exceptions; a function returning a bool, or "
                 "stopping the program, is the equivalent here")
        self.generic_visit(node)

    def visit_Try(self, node):
        self.add(node, NEVER, "try/except",
                 "catching needs stack unwinding and exception objects")
        self.generic_visit(node)

    def visit_Global(self, node):
        self.add(node, FIX, "global",
                 "there is no module level, so nothing to be global to; pass "
                 "the value in, or make it a field")
        self.generic_visit(node)

    visit_Nonlocal = visit_Global

    def visit_With(self, node):
        self.add(node, NEVER, "with",
                 "a context manager is an object with methods called on exit")
        self.generic_visit(node)

    def visit_Yield(self, node):
        self.add(node, NEVER, "yield",
                 "a generator is a suspended stack frame")
        self.generic_visit(node)

    visit_YieldFrom = visit_Yield
    visit_Await = visit_Yield

    def visit_Attribute(self, node):
        root = node
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in RUNTIME_ROOTS:
            self.add(node, NEVER, "%s.%s" % (root.id, node.attr),
                     "a library the generated C has no binding to, and whose "
                     "API hands back objects this subset cannot hold")
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in ("enumerate", "zip", "sorted", "reversed", "map",
                        "filter", "sum", "any", "all", "list", "dict", "set",
                        "tuple", "str", "repr", "input", "open"):
                self.add(node, NEVER if name in ("open", "input") else FEATURE,
                         name + "()",
                         "returns a sequence or needs an allocator"
                         if name not in ("open", "input")
                         else "there is no file or stdin access here")
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in ("append", "pop", "insert", "remove", "extend",
                          "clear", "update", "copy", "items", "keys",
                          "values", "sort", "split", "join", "strip",
                          "upper", "lower", "replace", "startswith",
                          "endswith", "format"):
                self.add(node, FEATURE if method in ("items", "keys", "values")
                         else NEVER,
                         "." + method + "()",
                         "a method on a built-in type; most of these resize "
                         "something or build a string, which needs an "
                         "allocator")
        self.generic_visit(node)

    def visit_Module(self, node):
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef, ast.Import,
                                     ast.ImportFrom, ast.Expr)):
                if isinstance(stmt, ast.If):
                    self.add(stmt, FIX, "code at module level",
                             "only imports, classes and functions live at the "
                             "top level; `if __name__ == ...` is unnecessary "
                             "because the compiler calls main() itself")
                else:
                    self.add(stmt, FIX, "code at module level",
                             "move it into a function, e.g. `def main() -> int:`")
        self.generic_visit(node)


def _is_array_literal(value):
    """`[1, 2, 3]` or `[0] * n`."""
    if isinstance(value, ast.List):
        return True
    return (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult)
            and isinstance(value.left, ast.List))


def _is_container_initialiser(node):
    """True for `xs: list[...] = [...]` and `d: dict[...] = {...}`, which are
    how an array and a dict are written here."""
    annotation = node.annotation
    root = annotation
    while isinstance(root, ast.Subscript):
        root = root.value
    if not isinstance(root, ast.Name):
        return False
    if root.id == "list":
        value = node.value
        if isinstance(value, ast.List):
            return True
        return (isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult)
                and isinstance(value.left, ast.List))
    if root.id == "dict":
        return isinstance(node.value, ast.Dict)
    return False


def _name_of(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    return "decorator"


def diagnose(source):
    """Every blocker in `source`, in line order. Raises SyntaxError if the
    file is not valid Python at all."""
    walker = _Walker()
    walker.visit(ast.parse(source))
    findings = walker.findings
    findings.sort(key=lambda f: (f.line, f.construct))
    return findings


def summarise(findings):
    """Counts by verdict, and by construct within each verdict."""
    out = {FIX: {}, FEATURE: {}, NEVER: {}}
    for f in findings:
        bucket = out[f.verdict]
        bucket[f.construct] = bucket.get(f.construct, 0) + 1
    return out


def report(source, path="<source>", limit=0):
    """A human-readable survey of everything in the way."""
    try:
        findings = diagnose(source)
    except SyntaxError as e:
        return "%s is not valid Python: line %s: %s" % (path, e.lineno, e.msg)

    if not findings:
        return ("%s: nothing outside the subset. If it still does not "
                "compile, the compiler's own error is the one to read." % path)

    verdicts = summarise(findings)
    lines = ["%s: %d things outside the subset" % (path, len(findings)), ""]

    headings = [
        (FIX, "FIXABLE — the subset can express these, the code needs a change"),
        (FEATURE, "NOT YET — the compiler could support these"),
        (NEVER, "NEVER — these need an allocator, dispatch, or a runtime that "
                "is not here"),
    ]
    for verdict, heading in headings:
        group = verdicts[verdict]
        if not group:
            continue
        total = sum(group.values())
        lines.append("%s  (%d)" % (heading, total))
        for construct, count in sorted(group.items(), key=lambda kv: -kv[1]):
            first = next(f.line for f in findings
                         if f.verdict == verdict and f.construct == construct)
            lines.append("    %-34s x%-4d first at line %d"
                         % (construct[:34], count, first))
        lines.append("")

    never = sum(verdicts[NEVER].values())
    if never:
        lines.append("This program cannot be translated: %d of the %d are in "
                     "the NEVER group." % (never, len(findings)))
    else:
        lines.append("Nothing here is impossible — every item is a change to "
                     "the code or a feature to add.")
    return "\n".join(lines)
