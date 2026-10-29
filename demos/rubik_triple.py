# Three Rubik's cubes side by side, in the compiler's subset.
#
#   python -m src.driver demos/rubik_triple.py --run
#
#   1 2 3         select a cube
#   U D F B R L   turn that face of the selected cube clockwise
#   hold SHIFT    turn it anticlockwise
#   S             scramble the selected cube     X  reset it
#   SPACE         scramble all three            ESC quit
#   arrow keys    orbit the camera
#
# This is a conversion of an ursina program (tests/programs/ported/
# triple_ursina.py), produced through the compiler's last-resort port stage
# with no API key: the prompt went to a file, the answer came back from one,
# and the compiler did the rest. It compiles, links and runs - 290KB of
# executable - and that is all that was checked. Nothing compared it with the
# ursina original, because a window has no output to compare.
#
import gfx
import math


# Three Rubik's cubes side by side, converted from the ursina version.
#
# Faces:  0 W (up)   1 Y (down)   2 G (front)
#         3 B (back) 4 R (right)  5 O (left)
#
# All three cubes live in ONE array: cube k's sticker i is at k * 54 + i.
# The subset has no array of arrays, so the cube index is arithmetic rather
# than a dimension, which is the same trick the flat grid uses.
#
# What the original did and this does not: the three lines of on-screen text.
# There is no way to build a string here, and gfx draws triangles, not glyphs.
# The two things that text reported are shown instead by what is drawn - the
# selected cube is the one with pale grout, and a solved cube's grout turns
# green. The move counter is simply gone; it cannot be shown without text.


def rotate_face(state: list[int], base: int, face: int) -> None:
    """Turn one face's own nine stickers clockwise."""
    at: int = base + face * 9
    a0: int = state[at + 0]
    a1: int = state[at + 1]
    a2: int = state[at + 2]
    a3: int = state[at + 3]
    a5: int = state[at + 5]
    a6: int = state[at + 6]
    a7: int = state[at + 7]
    a8: int = state[at + 8]

    state[at + 0] = a6
    state[at + 1] = a3
    state[at + 2] = a0
    state[at + 3] = a7
    state[at + 5] = a1
    state[at + 6] = a8
    state[at + 7] = a5
    state[at + 8] = a2


def rotate_adjacent(state: list[int], adj: list[int], base: int,
                    face: int) -> None:
    """Carry the four neighbouring strips round with the face.

    Without this a turn is invisible: on a solved cube every face is nine
    stickers of one colour, so permuting them in place changes nothing. The
    original's comment made the same point.
    """
    at: int = face * 12

    t0: int = state[base + adj[at + 9]]
    t1: int = state[base + adj[at + 10]]
    t2: int = state[base + adj[at + 11]]

    i: int = 3
    while i > 0:
        state[base + adj[at + i * 3 + 0]] = state[base + adj[at + (i - 1) * 3 + 0]]
        state[base + adj[at + i * 3 + 1]] = state[base + adj[at + (i - 1) * 3 + 1]]
        state[base + adj[at + i * 3 + 2]] = state[base + adj[at + (i - 1) * 3 + 2]]
        i = i - 1

    state[base + adj[at + 0]] = t0
    state[base + adj[at + 1]] = t1
    state[base + adj[at + 2]] = t2


def turn(state: list[int], adj: list[int], base: int, face: int,
         quarters: int) -> None:
    n: int = quarters % 4
    i: int = 0
    while i < n:
        rotate_face(state, base, face)
        rotate_adjacent(state, adj, base, face)
        i = i + 1


def solve_cube(state: list[int], base: int) -> None:
    """Put one cube back to solved, which is the original's reset()."""
    i: int = 0
    while i < 54:
        state[base + i] = i // 9
        i = i + 1


def is_solved(state: list[int], base: int) -> int:
    """1 when every face is one colour. The original returned a bool; there
    is no bool to return across a call here, so it is 1 or 0."""
    i: int = 0
    while i < 54:
        if state[base + i] != i // 9:
            return 0
        i = i + 1
    return 1


def next_random(seed: int) -> int:
    """An LCG. random.choice cannot be used: C's rand() is not Python's
    Mersenne Twister, and this subset refuses the random module for that
    reason."""
    return (seed * 1103515245 + 12345) % 2147483648


def scramble(state: list[int], adj: list[int], base: int, seed: int,
             count: int) -> int:
    """`count` random turns, avoiding two in a row on the same face - which
    is what the original's `while move[0] == previous` was for. Returns the
    seed so the caller can carry it on."""
    previous: int = -1
    i: int = 0
    while i < count:
        seed = next_random(seed)
        face: int = seed % 6
        if face == previous:
            seed = next_random(seed)
            face = (seed + 1) % 6
        turn(state, adj, base, face, seed // 8 % 3 + 1)
        previous = face
        i = i + 1
    return seed


# ------------------------------------------------------------- geometry
#
# A cube spans -1.5 .. 1.5 about its own centre. A point is three functions
# rather than one returning three values, because there are no tuples.


def centre_x(face: int, i: int) -> float:
    col: int = i % 3
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
    """Two triangles around a centre, wound so the outward face is visible:
    back-face culling is on in the shim."""
    gfx.tri(cx - ux * half - vx * half, cy - uy * half - vy * half, cz - uz * half - vz * half,
            cx - ux * half + vx * half, cy - uy * half + vy * half, cz - uz * half + vz * half,
            cx + ux * half + vx * half, cy + uy * half + vy * half, cz + uz * half + vz * half)
    gfx.tri(cx - ux * half - vx * half, cy - uy * half - vy * half, cz - uz * half - vz * half,
            cx + ux * half + vx * half, cy + uy * half + vy * half, cz + uz * half + vz * half,
            cx + ux * half - vx * half, cy + uy * half - vy * half, cz + uz * half - vz * half)


def draw_core(ox: float, r: float, g: float, b: float) -> None:
    """A dark cube just under the stickers, so the gaps read as grout rather
    than as holes through to the far side. Its colour is what tells you which
    cube is selected, and whether it is solved."""
    gfx.color(r, g, b)
    face: int = 0
    while face < 6:
        quad(centre_x(face, 4) * 0.99 + ox, centre_y(face, 4) * 0.99,
             centre_z(face, 4) * 0.99,
             axis_ux(face), axis_uy(face), axis_uz(face),
             axis_vx(face), axis_vy(face), axis_vz(face), 1.5)
        face = face + 1


def draw_cube(state: list[int], base: int, ox: float, chosen: int,
              solved: int) -> None:
    if solved != 0:
        draw_core(ox, 0.16, 0.45, 0.24)
    elif chosen != 0:
        draw_core(ox, 0.42, 0.44, 0.50)
    else:
        draw_core(ox, 0.07, 0.07, 0.08)

    face: int = 0
    while face < 6:
        i: int = 0
        while i < 9:
            c: int = state[base + face * 9 + i]
            gfx.color(colour_r(c), colour_g(c), colour_b(c))
            quad(centre_x(face, i) + ox, centre_y(face, i), centre_z(face, i),
                 axis_ux(face), axis_uy(face), axis_uz(face),
                 axis_vx(face), axis_vy(face), axis_vz(face), 0.44)
            i = i + 1
        face = face + 1


def offset_for(cube: int) -> float:
    """The original placed the three cubes at x = -4.5, 0 and 4.5."""
    if cube == 0:
        return -4.5
    elif cube == 1:
        return 0.0
    return 4.5


# ---------------------------------------------------------------- input
#
# Windows virtual-key codes. Letters and digits are their ASCII values, so
# U is 85 and 1 is 49. Slots 0..5 are the face keys U D F B R L in face
# order, 6..8 the cube-select digits, 9 is S, 10 is X, 11 is space.


def key_for_slot(slot: int) -> int:
    if slot == 0:
        return 85
    elif slot == 1:
        return 68
    elif slot == 2:
        return 70
    elif slot == 3:
        return 66
    elif slot == 4:
        return 82
    elif slot == 5:
        return 76
    elif slot == 6:
        return 49
    elif slot == 7:
        return 50
    elif slot == 8:
        return 51
    elif slot == 9:
        return 83
    elif slot == 10:
        return 88
    return 32


def main() -> int:
    # 3 cubes x 54 stickers, solved.
    state: list[int] = [0] * 162
    cube: int = 0
    while cube < 3:
        solve_cube(state, cube * 54)
        cube = cube + 1

    adj: list[int] = [
        18, 19, 20, 45, 46, 47, 27, 28, 29, 36, 37, 38,
        24, 25, 26, 42, 43, 44, 33, 34, 35, 51, 52, 53,
        6, 7, 8, 36, 39, 42, 11, 10, 9, 53, 50, 47,
        2, 1, 0, 45, 48, 51, 15, 16, 17, 44, 41, 38,
        2, 5, 8, 33, 30, 27, 11, 14, 17, 20, 23, 26,
        0, 3, 6, 18, 21, 24, 9, 12, 15, 35, 32, 29,
    ]

    # Which keys were down last frame, so a held key acts once.
    held: list[int] = [0] * 12

    if gfx.open(1100, 700) == 0:
        return 1

    selected: int = 1
    yaw: float = 0.6
    pitch: float = 0.45
    seed: int = 20251030
    last: float = gfx.time()

    while gfx.running() != 0:
        gfx.poll()

        if gfx.key(27) != 0:                 # escape, as in the original
            break

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

        # Shift reverses a turn, exactly as held_keys['shift'] did.
        quarters: int = 1
        if gfx.key(16) != 0:
            quarters = 3

        slot: int = 0
        while slot < 12:
            down: int = gfx.key(key_for_slot(slot))
            if down != 0 and held[slot] == 0:
                if slot < 6:
                    turn(state, adj, selected * 54, slot, quarters)
                elif slot < 9:
                    selected = slot - 6
                elif slot == 9:
                    seed = scramble(state, adj, selected * 54, seed, 25)
                elif slot == 10:
                    solve_cube(state, selected * 54)
                else:
                    cube = 0
                    while cube < 3:
                        seed = scramble(state, adj, cube * 54, seed, 25)
                        cube = cube + 1
            held[slot] = down
            slot = slot + 1

        eye_x: float = math.cos(pitch) * math.sin(yaw) * 17.0
        eye_y: float = math.sin(pitch) * 17.0
        eye_z: float = math.cos(pitch) * math.cos(yaw) * 17.0

        gfx.clear(0.071, 0.086, 0.137)
        gfx.perspective(45.0, 0.1, 100.0)
        gfx.camera(eye_x, eye_y, eye_z, 0.0, 0.0, 0.0)

        cube = 0
        while cube < 3:
            chosen: int = 0
            if cube == selected:
                chosen = 1
            draw_cube(state, cube * 54, offset_for(cube), chosen,
                      is_solved(state, cube * 54))
            cube = cube + 1

        gfx.present()

    gfx.close()
    return 0
