# A lit, rotating cube in a real window — written in the compiler's subset.
#
#   python -m src.driver demos/cube3d.py --run
#
#   Arrow keys   orbit the camera
#   Space        hold to stop the spin
#   Esc          quit
#
# WHAT IS INTERESTING ABOUT THIS FILE
#
# There is no array, no list, no object and no callback anywhere in it, and
# there cannot be: the subset has none of those. A vertex buffer is an array,
# a window is a handle, and input is a callback, so a program in this subset
# cannot talk to OpenGL at all.
#
# So it doesn't. `import gfx` brings in the shim in src/runtime/gfx.c, which
# owns the window handle, the GL context and the keyboard table, and exposes
# an API of nothing but numbers. This file does the 3D maths — rotation,
# projection of each corner, face winding — and asks that layer to put
# triangles on the screen. Every face below is passed as nine doubles.
#
# The cube's eight corners live in twenty-four named local variables rather
# than an array, which is exactly as awkward as it sounds. That awkwardness
# is the honest shape of the subset, not a stylistic choice: it is the reason
# fixed-size arrays would be the single most valuable thing to add next.

import gfx
import math


def rotate_x(y: float, z: float, angle: float, want_y: int) -> float:
    """One component of a rotation about X. Returns y' if want_y, else z'.

    A function can only return one number, and there is no tuple to put two
    in, so the caller asks for the component it wants.
    """
    c: float = math.cos(angle)
    s: float = math.sin(angle)
    if want_y == 1:
        return y * c - z * s
    return y * s + z * c


def rotate_y(x: float, z: float, angle: float, want_x: int) -> float:
    c: float = math.cos(angle)
    s: float = math.sin(angle)
    if want_x == 1:
        return x * c + z * s
    return -x * s + z * c


def spin_x(x: float, y: float, z: float, ax: float, ay: float) -> float:
    """The x of (x,y,z) after rotating about X by ax, then about Y by ay."""
    y1: float = rotate_x(y, z, ax, 1)
    z1: float = rotate_x(y, z, ax, 0)
    return rotate_y(x, z1, ay, 1)


def spin_y(x: float, y: float, z: float, ax: float, ay: float) -> float:
    return rotate_x(y, z, ax, 1)


def spin_z(x: float, y: float, z: float, ax: float, ay: float) -> float:
    z1: float = rotate_x(y, z, ax, 0)
    return rotate_y(x, z1, ay, 0)


def quad(x1: float, y1: float, z1: float,
         x2: float, y2: float, z2: float,
         x3: float, y3: float, z3: float,
         x4: float, y4: float, z4: float,
         ax: float, ay: float) -> None:
    """One face of the cube: four corners, spun, emitted as two triangles.

    The shim computes each triangle's normal from its winding order, so the
    lighting needs nothing from this side.
    """
    gfx.tri(spin_x(x1, y1, z1, ax, ay), spin_y(x1, y1, z1, ax, ay), spin_z(x1, y1, z1, ax, ay),
            spin_x(x2, y2, z2, ax, ay), spin_y(x2, y2, z2, ax, ay), spin_z(x2, y2, z2, ax, ay),
            spin_x(x3, y3, z3, ax, ay), spin_y(x3, y3, z3, ax, ay), spin_z(x3, y3, z3, ax, ay))
    gfx.tri(spin_x(x1, y1, z1, ax, ay), spin_y(x1, y1, z1, ax, ay), spin_z(x1, y1, z1, ax, ay),
            spin_x(x3, y3, z3, ax, ay), spin_y(x3, y3, z3, ax, ay), spin_z(x3, y3, z3, ax, ay),
            spin_x(x4, y4, z4, ax, ay), spin_y(x4, y4, z4, ax, ay), spin_z(x4, y4, z4, ax, ay))


def draw_cube(ax: float, ay: float) -> None:
    """Six faces, each a different colour, wound counter-clockwise from
    outside so back-face culling keeps the inside hidden."""
    h: float = 1.0

    gfx.color(0.91, 0.30, 0.24)                       # +Z, red
    quad(-h, -h, h, h, -h, h, h, h, h, -h, h, h, ax, ay)

    gfx.color(0.18, 0.55, 0.34)                       # -Z, green
    quad(h, -h, -h, -h, -h, -h, -h, h, -h, h, h, -h, ax, ay)

    gfx.color(0.20, 0.45, 0.80)                       # +X, blue
    quad(h, -h, h, h, -h, -h, h, h, -h, h, h, h, ax, ay)

    gfx.color(0.95, 0.77, 0.20)                       # -X, yellow
    quad(-h, -h, -h, -h, -h, h, -h, h, h, -h, h, -h, ax, ay)

    gfx.color(0.95, 0.95, 0.95)                       # +Y, white
    quad(-h, h, h, h, h, h, h, h, -h, -h, h, -h, ax, ay)

    gfx.color(0.95, 0.52, 0.16)                       # -Y, orange
    quad(-h, -h, -h, h, -h, -h, h, -h, h, -h, -h, h, ax, ay)


def main() -> int:
    if gfx.open(900, 650) == 0:
        return 1

    # Camera orbit, driven by the arrow keys.
    yaw: float = 0.7
    pitch: float = 0.45
    distance: float = 6.5

    # The cube's own rotation, accumulated per frame so holding Space can
    # pause it without the cube jumping when released.
    spin: float = 0.0
    last: float = gfx.time()

    while gfx.running() != 0:
        gfx.poll()

        now: float = gfx.time()
        dt: float = now - last
        last = now
        if dt > 0.1:                  # a long stall should not teleport it
            dt = 0.1

        if gfx.key(gfx.key_left()) != 0:
            yaw = yaw - dt * 1.6
        if gfx.key(gfx.key_right()) != 0:
            yaw = yaw + dt * 1.6
        if gfx.key(gfx.key_up()) != 0:
            pitch = pitch + dt * 1.2
        if gfx.key(gfx.key_down()) != 0:
            pitch = pitch - dt * 1.2

        # Keep the camera off the poles, where the up vector degenerates.
        if pitch > 1.45:
            pitch = 1.45
        if pitch < -1.45:
            pitch = -1.45

        if gfx.key(gfx.key_space()) == 0:
            spin = spin + dt * 0.9

        eye_x: float = math.cos(pitch) * math.sin(yaw) * distance
        eye_y: float = math.sin(pitch) * distance
        eye_z: float = math.cos(pitch) * math.cos(yaw) * distance

        gfx.clear(0.07, 0.08, 0.11)
        gfx.perspective(55.0, 0.1, 100.0)
        gfx.camera(eye_x, eye_y, eye_z, 0.0, 0.0, 0.0)
        draw_cube(spin * 0.7, spin)
        gfx.present()

    gfx.close()
    return 0
