# demos

Larger programs than the test samples, kept here because they exercise the
whole compiler at once.

## golf3d.py — ASCII 3D golf

A ray-marched 3D golf game written in the Python subset and translated to C.

```bash
# It does not compile without the gate: sqrt, sin, cos and floor have no rules
python -m src.driver demos/golf3d.py --run --ai

# The original, under CPython (needs a shim for put() and the math functions)
python -c "import sys; from math import *; exec(open('demos/golf3d.py').read().replace('put(', 'sys.stdout.write(')); main()"
```

### What it demonstrates

**It cannot be compiled without AI.** `sqrt`, `sin`, `cos` and `floor` have no
static rule, so all sixteen call sites go through the gate and every answer is
proved to compile before it is used. Without `--ai` the compiler stops and names
the missing function.

**It produces byte-identical output.** The C and the Python play the same round
of golf — every frame of the rendering, character for character. The shot
scatter comes from a hash of a sine rather than a random number generator, so
the round is reproducible; without that the comparison would be meaningless.

**It is written entirely within the subset.** There are no arrays, because the
subset has none. That single constraint shapes the whole renderer:

- the terrain is a *function* of x and z rather than a stored height grid, so
  there is nothing to allocate;
- surface normals come from finite differences of that function;
- each character cell is ray-marched and written immediately with `put()`,
  because with no array there is no frame buffer to fill in and no string
  concatenation to build a line with.

`put()` exists for exactly this. `print()` always ends a line and always puts a
space between its arguments, so with it alone a program can emit lines but never
build one.

### How the renderer works

For each of the 72x26 character cells:

1. Build a camera ray. The camera sits behind the ball, looking toward the pin.
2. Intersect it with the ball (analytic sphere) and the flagstick (analytic —
   the stick is far thinner than a pixel, so marching walks straight past it).
3. March the height field until the ray drops below the surface, then bisect
   six times to sharpen the silhouette.
4. Take the nearest of the three hits. For ground, compute the normal, apply a
   Lambert term for a low sun, fade with distance, and pick a glyph from the
   material — fairway, green, bunker or rough.

The physics is a straightforward integrator: gravity, bounce with restitution,
and friction that depends on what the ball is sitting on. Sand is slow, the
green is fast.
