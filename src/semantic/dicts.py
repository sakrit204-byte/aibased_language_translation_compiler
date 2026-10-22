"""
Dictionaries with a fixed set of keys.

A Python dict allocates, rehashes and grows. None of that is available here,
and none of it is needed for what a dict is mostly used for in a program this
size: a lookup table whose keys are known when the program is written.

    COLORS: dict[str, int] = {"W": 0, "Y": 1, "G": 2}

The keys are compile-time constants, so they become a `static const` array.
The values are ordinary storage, so they become a second array beside it:

    static const py_str COLORS__keys[3] = {"W", "Y", "G"};
    py_int COLORS__vals[3] = {0, 1, 2};

Two arrays, no allocator, no hashing, no rehash. Lookup is a scan, which for
three to fifty keys is faster than hashing anyway.

WHY THIS KEEPS THE CPYTHON PROPERTY

Since Python 3.7 a dict iterates in insertion order. Storing the keys in the
order they were written reproduces that exactly, so `for k in d` yields the
same sequence in both languages — which is the whole reason a dict can be
added here at all without giving up the one thing the project can prove.

A missing key raises KeyError in Python; the generated C stops with the same
message and the same exit status rather than reading past the end.

WHAT IT CANNOT DO

No new keys, no `del`, no growth: the key set is part of the type, the same
way an array's length is. Values are freely mutable. That is the line between
"a table I look things up in" — which this is — and "a hash map I build at
run time", which needs the allocator this compiler does not have.
"""

import re

_DICT = re.compile(r"^dict\[(int|str):(int|float|bool|str):(\d+)\]$")

KEY_TYPES = ("str", "int")
VALUE_TYPES = ("int", "float", "bool", "str")


def make(key_type, value_type, size):
    return "dict[%s:%s:%d]" % (key_type, value_type, size)


def is_dict(t):
    return isinstance(t, str) and bool(_DICT.match(t))


def key_type(t):
    m = _DICT.match(t) if isinstance(t, str) else None
    return m.group(1) if m else None


def value_type(t):
    m = _DICT.match(t) if isinstance(t, str) else None
    return m.group(2) if m else None


def size(t):
    m = _DICT.match(t) if isinstance(t, str) else None
    return int(m.group(3)) if m else None


def describe(t):
    if not is_dict(t):
        return t
    return "dict[%s, %s]" % (key_type(t), value_type(t))


def keys_name(name):
    return name + "__keys"


def values_name(name):
    return name + "__vals"
