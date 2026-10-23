"""
Fixed-size arrays: the one compound type in this subset.

Everything else here is a scalar, and that is deliberate — the generated C has
no allocator and no ownership model, so a growable list or a dict would need
machinery the project does not have. A *fixed-size* array needs none of it.
`a: list[int] = [0] * 54` becomes `py_int a[54]`, which lives on the stack,
is freed by returning, and cannot leak.

The declared size must be a literal, because it becomes part of the C type.
That is the whole constraint: no resizing, no append, no slicing.

TYPES AS STRINGS

Every other type in this compiler is a plain string ("int", "float", ...), and
the inference, promotion and assignability rules are written in terms of them.
Array types join that scheme rather than replacing it: "int[54]" is a sized
array and "int[]" is one whose size is not known here, which is what a
parameter has. The helpers below are the only place that encoding is read, so
it can change without touching the rules.

PARAMETERS CARRY THEIR LENGTH

C arrays decay to pointers when passed, which loses the size — so `len(a)`
inside a function would have nothing to read. Rather than ban it, every array
parameter is compiled to *two* C parameters, the pointer and its length, and
every call site passes both. `len(a)` then works anywhere, and so do the
bounds checks, which is what keeps the C agreeing with Python.

Pass-by-reference comes free and matches Python: a list argument is the same
object the caller holds, and a `py_int *` is the same storage.
"""

import re

_ARRAY = re.compile(r"^(int|float|bool|str)\[(\d*)\]$")

ELEMENT_TYPES = ("int", "float", "bool")

# Suffix for the hidden length parameter that accompanies every array
# parameter. Double underscore so it cannot collide with a Python name that
# reached here — c_name() already renames anything starting with "__".
LENGTH_SUFFIX = "__len"


def make(element_type, size=None):
    """Build an array type. `size=None` means "size not known here"."""
    return "%s[%s]" % (element_type, "" if size is None else size)


def is_array(t):
    return isinstance(t, str) and bool(_ARRAY.match(t))


def element(t):
    """The element type of an array type, or None if `t` is not one."""
    match = _ARRAY.match(t) if isinstance(t, str) else None
    return match.group(1) if match else None


def size(t):
    """The declared length, or None when the size is not known here."""
    match = _ARRAY.match(t) if isinstance(t, str) else None
    if not match or not match.group(2):
        return None
    return int(match.group(2))


def length_name(array_name):
    return array_name + LENGTH_SUFFIX


def describe(t):
    """How to write the type in a message, in the source language.

    The size is part of the type, so it is part of the description. Leaving it
    out produced the one kind of diagnostic nobody can act on — passing a
    `list[int, 3]` where a `list[int, 2]` was wanted said "argument 1 of 'f'
    (a) is list[int], got list[int]", naming the same type twice and hiding
    the only thing that was wrong with it.

    A parameter whose size is not known here still describes as `list[int]`,
    because that is how it is written in the source and there is no number to
    report.
    """
    if not is_array(t):
        return t
    declared = size(t)
    if declared is None:
        return "list[%s]" % element(t)
    return "list[%s, %d]" % (element(t), declared)


def compatible(source, target):
    """True if an array of type `source` may be passed where `target` is
    expected.

    Element types must match exactly — unlike scalars, there is no widening,
    because an array of int and an array of double have different layouts in
    memory and C would reinterpret the bytes rather than convert them. A sized
    array satisfies an unsized parameter, which is the only direction that
    loses information and the only one that is safe.
    """
    if not (is_array(source) and is_array(target)):
        return False
    if element(source) != element(target):
        return False
    want = size(target)
    return want is None or want == size(source)
