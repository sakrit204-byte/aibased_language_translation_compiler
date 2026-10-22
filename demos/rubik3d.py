# A Rubik's cube you can turn, in a window — written in the compiler's subset.
#
#   python -m src.driver demos/rubik3d.py --run
#
#   W Y G B R O   turn that face clockwise
#   1 2 3 4 5 6   turn the same faces anticlockwise
#   SPACE         scramble
#   arrow keys    orbit the camera
#   ESC           quit
#
# This is the program the whole exercise was aimed at, and it needed two
# things the subset did not have a day ago.
#
# `import gfx` is the first. The shim in src/runtime/gfx.c owns the window,
# the GL context and the keyboard — all things with no representation here —
# and exposes an API of nothing but numbers.
#
# Fixed-size arrays are the second, and they are the one that mattered. A
# cube is 54 stickers and a permutation table; before arrays there was
# nowhere to put either. state[] holds the stickers, adj[] holds the table,
# and held[] remembers which keys were down last frame so a turn happens once
# per press rather than once per frame.
#
# There is still no class, no dict, no list and no object anywhere in here,
# and there is no AI gate either: every call below has a static C rule.

import gfx
import math


# Face numbers:  0 W (up)    1 Y (down)   2 G (front)
#                3 B (back)  4 R (right)  5 O (left)
#
# Sticker i of a face is at row i // 3, column i % 3, in reading order.


def rotate_face(state: list[int], face: int) -> None:
    """Turn one face's own nine stickers clockwise."""
    base: int = face * 9
    a0: int = state[base + 0]
    a1: int = state[base + 1]
    a2: int = state[base + 2]
    a3: int = state[base + 3]
    a5: int = state[base + 5]
    a6: int = state[base + 6]
    a7: int = state[base + 7]
    a8: int = state[base + 8]

    state[base + 0] = a6
    state[base + 1] = a3
    state[base + 2] = a0
    state[base + 3] = a7
    state[base + 5] = a1
    state[base + 6] = a8
    state[base + 7] = a5
    state[base + 8] = a2


def rotate_adjacent(state: list[int], adj: list[int], face: int) -> None:
    """Carry the four neighbouring strips round with the face."""
    base: int = face * 12

    t0: int = state[adj[base + 9]]
    t1: int = state[adj[base + 10]]
    t2: int = state[adj[base + 11]]

    i: int = 3
    while i > 0:
        state[adj[base + i * 3 + 0]] = state[adj[base + (i - 1) * 3 + 0]]
        state[adj[base + i * 3 + 1]] = state[adj[base + (i - 1) * 3 + 1]]
        state[adj[base + i * 3 + 2]] = state[adj[base + (i - 1) * 3 + 2]]
        i = i - 1

    state[adj[base + 0]] = t0
    state[adj[base + 1]] = t1
    state[adj[base + 2]] = t2


def turn(state: list[int], adj: list[int], face: int, quarters: int) -> None:
    n: int = quarters % 4
    i: int = 0
    while i < n:
        rotate_face(state, face)
        rotate_adjacent(state, adj, face)
        i = i + 1


def next_random(seed: int) -> int:
    """An LCG, written out rather than imported — see demos/rubik.py."""
    return (seed * 1103515245 + 12345) % 2147483648


# ------------------------------------------------------------- geometry
#
# The cube spans -1.5 .. 1.5, so each of the 27 cubies is one unit across and
# a sticker's centre sits on a half-integer face plane. There are no tuples
# here, so a point is three functions rather than one returning three values.


def centre_x(face: int, i: int) -> float:
    col: int = i % 3
    row: int = i // 3
    if face == 0:
        return float(col - 1)
    elif face == 1:
        return float(col - 1)
    elif face == 2:
        return float(col - 1)
    elif face == 3:
        return float(1 - col)
    elif face == 4:
        return 1.5
    return -1.5


def centre_y(face: int, i: int) -> float:
    row: int = i // 3
    if face == 0:
        return 1.5
    elif face == 1:
        return -1.5
    return float(1 - row)


def centre_z(face: int, i: int) -> float:
    col: int = i % 3
    row: int = i // 3
    if face == 0:
        return float(row - 1)
    elif face == 1:
        return float(1 - row)
    elif face == 2:
        return 1.5
    elif face == 3:
        return -1.5
    elif face == 4:
        return float(1 - col)
    return float(col - 1)


# The in-plane axes: u runs along increasing column, v along increasing row.


def axis_ux(face: int) -> float:
    if face == 0:
        return 1.0
    elif face == 1:
        return 1.0
    elif face == 2:
        return 1.0
    elif face == 3:
        return -1.0
    return 0.0


def axis_uy(face: int) -> float:
    return 0.0


def axis_uz(face: int) -> float:
    if face == 4:
        return -1.0
    elif face == 5:
        return 1.0
    return 0.0


def axis_vx(face: int) -> float:
    return 0.0


def axis_vy(face: int) -> float:
    if face == 0:
        return 0.0
    elif face == 1:
        return 0.0
    return -1.0


def axis_vz(face: int) -> float:
    if face == 0:
        return 1.0
    elif face == 1:
        return -1.0
    return 0.0


def colour_r(c: int) -> float:
    if c == 0:
        return 0.95
    elif c == 1:
        return 0.95
    elif c == 2:
        return 0.13
    elif c == 3:
        return 0.13
    elif c == 4:
        return 0.90
    return 0.95


def colour_g(c: int) -> float:
    if c == 0:
        return 0.95
    elif c == 1:
        return 0.84
    elif c == 2:
        return 0.65
    elif c == 3:
        return 0.40
    elif c == 4:
        return 0.19
    return 0.52


def colour_b(c: int) -> float:
    if c == 0:
        return 0.95
    elif c == 1:
        return 0.09
    elif c == 2:
        return 0.28
    elif c == 3:
        return 0.75
    elif c == 4:
        return 0.19
    return 0.11


def quad(cx: float, cy: float, cz: float,
         ux: float, uy: float, uz: float,
         vx: float, vy: float, vz: float, half: float) -> None:
    """Two triangles around a centre, wound so the outward face is the
    visible one — back-face culling is on in the shim."""
    gfx.tri(cx - ux * half - vx * half, cy - uy * half - vy * half, cz - uz * half - vz * half,
            cx - ux * half + vx * half, cy - uy * half + vy * half, cz - uz * half + vz * half,
            cx + ux * half + vx * half, cy + uy * half + vy * half, cz + uz * half + vz * half)
    gfx.tri(cx - ux * half - vx * half, cy - uy * half - vy * half, cz - uz * half - vz * half,
            cx + ux * half + vx * half, cy + uy * half + vy * half, cz + uz * half + vz * half,
            cx + ux * half - vx * half, cy + uy * half - vy * half, cz + uz * half - vz * half)


def draw_core() -> None:
    """A dark cube just under the stickers, so the gaps between them read as
    grout rather than as holes through to the far side."""
    gfx.color(0.07, 0.07, 0.08)
    face: int = 0
    while face < 6:
        quad(centre_x(face, 4) * 0.99, centre_y(face, 4) * 0.99, centre_z(face, 4) * 0.99,
             axis_ux(face), axis_uy(face), axis_uz(face),
             axis_vx(face), axis_vy(face), axis_vz(face), 1.5)
        face = face + 1


def draw_cube(state: list[int]) -> None:
    draw_core()
    face: int = 0
    while face < 6:
        i: int = 0
        while i < 9:
            c: int = state[face * 9 + i]
            gfx.color(colour_r(c), colour_g(c), colour_b(c))
            quad(centre_x(face, i), centre_y(face, i), centre_z(face, i),
                 axis_ux(face), axis_uy(face), axis_uz(face),
                 axis_vx(face), axis_vy(face), axis_vz(face), 0.44)
            i = i + 1
        face = face + 1


# ---------------------------------------------------------------- input
#
# Slots 0..5 are the clockwise keys W Y G B R O, slots 6..11 the
# anticlockwise keys 1..6, and slot 12 is space. Windows virtual-key codes
# for letters and digits are their ASCII values.


def key_for_slot(slot: int) -> int:
    if slot == 0:
        return 87
    elif slot == 1:
        return 89
    elif slot == 2:
        return 71
    elif slot == 3:
        return 66
    elif slot == 4:
        return 82
    elif slot == 5:
        return 79
    elif slot == 6:
        return 49
    elif slot == 7:
        return 50
    elif slot == 8:
        return 51
    elif slot == 9:
        return 52
    elif slot == 10:
        return 53
    elif slot == 11:
        return 54
    return 32


def main() -> int:
    state: list[int] = [0] * 54
    i: int = 0
    while i < 54:
        state[i] = i // 9
        i = i + 1

    adj: list[int] = [
        18, 19, 20, 45, 46, 47, 27, 28, 29, 36, 37, 38,
        24, 25, 26, 42, 43, 44, 33, 34, 35, 51, 52, 53,
        6, 7, 8, 36, 39, 42, 11, 10, 9, 53, 50, 47,
        2, 1, 0, 45, 48, 51, 15, 16, 17, 44, 41, 38,
        2, 5, 8, 33, 30, 27, 11, 14, 17, 20, 23, 26,
        0, 3, 6, 18, 21, 24, 9, 12, 15, 35, 32, 29,
    ]

    # Which keys were down last frame, so a held key turns a face once.
    held: list[int] = [0] * 13

    if gfx.open(900, 700) == 0:
        return 1

    yaw: float = 0.6
    pitch: float = 0.5
    seed: int = 20251022
    last: float = gfx.time()

    while gfx.running() != 0:
        gfx.poll()

        now: float = gfx.time()
        dt: float = now - last
        last = now
        if dt > 0.1:
            dt = 0.1

        if gfx.key(gfx.key_left()) != 0:
            yaw = yaw - dt * 1.8
        if gfx.key(gfx.key_right()) != 0:
            yaw = yaw + dt * 1.8
        if gfx.key(gfx.key_up()) != 0:
            pitch = pitch + dt * 1.4
        if gfx.key(gfx.key_down()) != 0:
            pitch = pitch - dt * 1.4
        if pitch > 1.45:
            pitch = 1.45
        if pitch < -1.45:
            pitch = -1.45

        slot: int = 0
        while slot < 13:
            down: int = gfx.key(key_for_slot(slot))
            if down != 0 and held[slot] == 0:
                if slot < 6:
                    turn(state, adj, slot, 1)
                elif slot < 12:
                    turn(state, adj, slot - 6, 3)
                else:
                    i = 0
                    while i < 25:
                        seed = next_random(seed)
                        turn(state, adj, seed % 6, seed // 8 % 3 + 1)
                        i = i + 1
            held[slot] = down
            slot = slot + 1

        eye_x: float = math.cos(pitch) * math.sin(yaw) * 9.0
        eye_y: float = math.sin(pitch) * 9.0
        eye_z: float = math.cos(pitch) * math.cos(yaw) * 9.0

        gfx.clear(0.09, 0.10, 0.13)
        gfx.perspective(45.0, 0.1, 100.0)
        gfx.camera(eye_x, eye_y, eye_z, 0.0, 0.0, 0.0)
        draw_cube(state)
        gfx.present()

    gfx.close()
    return 0
