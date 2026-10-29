# A program whose behaviour is a window, kept so the refusal can be tested.
#
# It is not a sample the compiler is meant to handle: it is here to be turned
# down. The subset has no way to hold a tkinter object, and more to the point
# a window has no bytes on stdout, so "the output is the spec" has nothing to
# work with. It lives under tests/programs/windowed/ rather than
# tests/programs/ so the sample suite does not try to translate it.
import tkinter as tk


def main() -> int:
    root = tk.Tk()
    root.title("nothing to see")
    root.mainloop()
    return 0
