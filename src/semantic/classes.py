"""
Classes, as value types.

A class here is a C struct and a set of functions that take a pointer to one.
That is the whole of it: no allocator, no vtable, no refcount, no garbage
collector. An instance lives in its enclosing function's frame exactly as an
array does, and dies when that function returns.

    class Cube:                     typedef struct {
        faces: list[int]                py_int faces[54];
                                    } Cube;

        def turn(self,              void Cube__turn(Cube *self,
                 face: int) -> None:                py_int face)

What that buys, and what it costs:

  * `self` is a pointer, so a method mutates the caller's instance — which
    is what Python does, and it comes free rather than needing references.
  * Passing an instance to a function passes the pointer, so that matches
    Python too.
  * Returning one does not. C would copy the struct, Python would hand back
    the same object, and the two would diverge the moment the caller wrote
    to it. So returning an instance is refused, the same as for an array.
  * Inheritance is refused. A base-class reference calling a subclass method
    is dynamic dispatch, and dispatch needs a vtable — a function pointer
    per method, which is a function value, which this subset does not have.

ENCAPSULATION IS ENFORCED, NOT CONVENTIONAL

Python's leading underscore is a convention and its `__name` is a mangling
anyone can see through. Here a `__`-prefixed field simply cannot be named
from outside the class: there is no expression that reaches it. That is
stricter than Python, never looser, so a program the compiler accepts
behaves the same in both.

TYPES AS STRINGS

As with arrays, an instance type is a string the rest of the compiler can
carry around without knowing what is inside it: "obj:Cube".
"""

PREFIX = "obj:"


def make(class_name):
    return PREFIX + class_name


def is_instance(t):
    return isinstance(t, str) and t.startswith(PREFIX)


def class_of(t):
    return t[len(PREFIX):] if is_instance(t) else None


def describe(t):
    return class_of(t) if is_instance(t) else t


def is_private(field_name):
    """Python's own rule for 'this belongs to the class', made real."""
    return field_name.startswith("__") and not field_name.endswith("__")


class Field:
    def __init__(self, name, type_, line):
        self.name = name
        self.type = type_
        self.line = line


class Constant:
    """A class-level literal: `SIZE: int = 3`. Shared by every instance, so
    it is not a struct member at all — it is inlined where it is used."""

    def __init__(self, name, type_, value_node, line):
        self.name = name
        self.type = type_
        self.value = value_node
        self.line = line


class ClassInfo:
    def __init__(self, name, line):
        self.name = name
        self.line = line
        self.fields = {}        # name -> Field, in declaration order
        self.constants = {}     # name -> Constant
        self.methods = {}       # name -> FunctionDef
        self.init = None        # the __init__ FunctionDef, required

    def field_order(self):
        return list(self.fields.values())

    def member_names(self):
        return sorted(set(self.fields) | set(self.constants) | set(self.methods))

    def lookup(self, name):
        """Returns ("field"|"constant"|"method", thing), or (None, None)."""
        if name in self.fields:
            return "field", self.fields[name]
        if name in self.constants:
            return "constant", self.constants[name]
        if name in self.methods:
            return "method", self.methods[name]
        return None, None


def method_symbol(class_name, method_name):
    """The C name of a method. Two underscores so it cannot collide with a
    plain function called `Cube_turn`."""
    return "%s__%s" % (class_name, method_name)
