# A worked example of the conversion stage

`spinner_pygame.py` is a pygame program: a spinning square in a window. It has
no output, so the comparison that accepts a repair has nothing to work with,
and repair is blocked before it starts.

`spinner_gfx.py` is the conversion into the subset, drawing through `gfx`. It
was produced through `tools/repair_offline.py --port`, with no API key and no
account, and it went through the whole compiler, built to a 154KB executable
and ran.

What the pair is evidence of: the subset can express the program, and the
compiler turns the conversion into a native executable.

What it is **not** evidence of: that the two behave the same. Nothing compared
them, because a window has no bytes to compare. The stage reports CONVERTED,
NOT VERIFIED for exactly that reason, and `tests/test_port.py` asserts the
report never uses the word "verified" on its own.

These live under `tests/programs/ported/` rather than `tests/programs/` so the
sample suite does not try to translate the pygame one - it is here to be
refused.
