import gfx


# Three Rubik's cubes as unfolded nets, converted from the pygame version.
#
# The original draws flat rectangles with pygame.draw.rect in a cross-shaped
# net per cube. gfx has no 2D rectangle, so each sticker is a quad on the
# z = 0 plane with the camera square on to it, which comes out as the same
# flat picture.
#
# Faces:  0 U white   1 D yellow   2 F green
#         3 B blue    4 R red      5 L orange
#
# All three cubes live in ONE array: cube k's sticker i is at k * 54 + i.
# There is no array of arrays here, so the cube index is arithmetic.
#
# What the original had and this does not: the title, the per-cube labels,
# the move counter and the help panel. Nothing in this subset can build a
# string at run time and gfx draws triangles, not glyphs. The two things the
# labels carried are shown by the backing panel instead - it is pale for the
# selected cube and green for a solved one. The move counter is simply gone;
# it cannot be shown without text, and dropping it beat faking it.


def rotate_face(state: list[int], base: int, face: int) -> None:
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
    stickers of one colour, so permuting them in place changes nothing.
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
    i: int = 0
    while i < 54:
        state[base + i] = i // 9
        i = i + 1


def is_solved(state: list[int], base: int) -> int:
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
    """`count` random turns, never twice running on the same face - which is
    what the original's `while move[0] == previous` was for."""
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


# ------------------------------------------------------------ the net
#
# The original's NET table, as two functions: there are no tuples to return.
#
#        U
#     L  F  R  B
#        D


def net_col(face: int) -> int:
    if face == 0:
        return 1
    elif face == 1:
        return 1
    elif face == 2:
        return 1
    elif face == 3:
        return 3
    elif face == 4:
        return 2
    return 0


def net_row(face: int) -> int:
    if face == 0:
        return 0
    elif face == 1:
        return 2
    return 1


def colour_r(c: int) -> float:
    if c == 0:
        return 0.961
    elif c == 1:
        return 1.0
    elif c == 2:
        return 0.0
    elif c == 3:
        return 0.157
    elif c == 4:
        return 0.863
    return 1.0


def colour_g(c: int) -> float:
    if c == 0:
        return 0.961
    elif c == 1:
        return 0.824
    elif c == 2:
        return 0.745
    elif c == 3:
        return 0.392
    elif c == 4:
        return 0.157
    return 0.510


def colour_b(c: int) -> float:
    if c == 0:
        return 0.961
    elif c == 1:
        return 0.0
    elif c == 2:
        return 0.314
    elif c == 3:
        return 0.941
    elif c == 4:
        return 0.176
    return 0.078


def flat_quad(cx: float, cy: float, cz: float, half_w: float,
              half_h: float) -> None:
    """One axis-aligned rectangle on the z plane, as two triangles.

    Wound anticlockwise seen from the camera, because the shim culls back
    faces and a clockwise quad would simply not be there.
    """
    gfx.tri(cx - half_w, cy - half_h, cz,
            cx - half_w, cy + half_h, cz,
            cx + half_w, cy + half_h, cz)
    gfx.tri(cx - half_w, cy - half_h, cz,
            cx + half_w, cy + half_h, cz,
            cx + half_w, cy - half_h, cz)


def draw_net(state: list[int], base: int, ox: float, cell: float,
             chosen: int, solved: int) -> None:
    net_w: float = cell * 12.0
    net_h: float = cell * 9.0

    # The panel behind the stickers. It is what says which cube is selected
    # and whether it is solved, now that there are no labels to say it.
    if solved != 0 and chosen != 0:
        gfx.color(0.35, 0.85, 0.45)
    elif solved != 0:
        gfx.color(0.11, 0.33, 0.17)
    elif chosen != 0:
        gfx.color(0.60, 0.63, 0.70)
    else:
        gfx.color(0.275, 0.294, 0.353)
    flat_quad(ox, 0.0, -0.02, net_w * 0.5 + cell * 0.35,
              net_h * 0.5 + cell * 0.35)

    face: int = 0
    while face < 6:
        base_col: int = net_col(face) * 3
        base_row: int = net_row(face) * 3
        i: int = 0
        while i < 9:
            c: int = state[base + face * 9 + i]
            gfx.color(colour_r(c), colour_g(c), colour_b(c))
            col: int = base_col + i % 3
            row: int = base_row + i // 3
            x: float = ox + (float(col) + 0.5) * cell - net_w * 0.5
            y: float = net_h * 0.5 - (float(row) + 0.5) * cell
            flat_quad(x, y, 0.0, cell * 0.43, cell * 0.43)
            i = i + 1
        face = face + 1


def offset_for(cube: int) -> float:
    if cube == 0:
        return -4.4
    elif cube == 1:
        return 0.0
    return 4.4


# ---------------------------------------------------------------- input
#
# Windows virtual-key codes; letters and digits are their ASCII values.
# Slots 0..5 are the face keys in face order U D F B R L, 6..8 the cube
# digits, 9 is S, 10 is X, 11 is space.


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

    held: list[int] = [0] * 12

    if gfx.open(1440, 650) == 0:
        return 1

    cell: float = 0.3
    selected: int = 0
    seed: int = 20251030

    while gfx.running() != 0:
        gfx.poll()

        if gfx.key(27) != 0:
            break

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

        gfx.clear(0.094, 0.106, 0.149)
        gfx.perspective(45.0, 0.1, 100.0)
        gfx.camera(0.0, 0.0, 8.6, 0.0, 0.0, 0.0)

        cube = 0
        while cube < 3:
            chosen: int = 0
            if cube == selected:
                chosen = 1
            draw_net(state, cube * 54, offset_for(cube), cell, chosen,
                     is_solved(state, cube * 54))
            cube = cube + 1

        gfx.present()

    gfx.close()
    return 0
