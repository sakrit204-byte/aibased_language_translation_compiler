# A Rubik's cube, in the compiler's subset.
#
#   python -m src.driver demos/rubik.py --run
#
# This is the program that was impossible a day ago. The blocker was never
# the graphics or the imports — it was that a cube is 54 stickers and a
# permutation table, and the subset had no way to hold either. It has one
# now, and nothing else here needed inventing: every line below is integer
# arithmetic the compiler already knew how to translate.
#
# THE STATE
#
# state[0..53] holds one colour per sticker. Face f owns state[f*9 .. f*9+8],
# laid out in reading order:
#
#       0 1 2
#       3 4 5
#       6 7 8
#
# Faces are numbered in the order W Y G B R O — up, down, front, back, right,
# left — and a sticker's colour is just that face's number, so a solved cube
# is state[i] = i // 9.
#
# THE TABLE
#
# Turning a face also drags one strip of three stickers from each of its four
# neighbours round with it. ADJ holds those four strips for every face, as
# absolute indices into state: 6 faces x 4 strips x 3 stickers = 72 numbers.
#
# In the Python version of this program that table was a dict of lists of
# tuples. Here it is one array of ints, which is the same information with
# the indirection removed — and unlike the dict, it costs no allocation.

# Face numbers, used throughout:  0 W (up)    1 Y (down)   2 G (front)
#                                 3 B (back)  4 R (right)  5 O (left)


def rotate_face(state: list[int], face: int) -> None:
    """Turn one face's own nine stickers clockwise.

    0 1 2      6 3 0
    3 4 5  ->  7 4 1
    6 7 8      8 5 2
    """
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
    """Carry the four neighbouring strips round with the face.

    The contents of each strip move into the next one, so the last strip is
    saved first — writing as we go would overwrite a strip before the strip
    after it has been read.
    """
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
    """Apply `quarters` clockwise quarter turns of `face`."""
    n: int = quarters % 4
    i: int = 0
    while i < n:
        rotate_face(state, face)
        rotate_adjacent(state, adj, face)
        i = i + 1


def solved(state: list[int]) -> bool:
    """True when every sticker is back on the face it started from."""
    i: int = 0
    while i < 54:
        if state[i] != i // 9:
            return False
        i = i + 1
    return True


def next_random(seed: int) -> int:
    """A linear congruential generator, written out rather than imported.

    `random` cannot be imported here on purpose: C's rand() is not Python's
    Mersenne Twister, so the two would disagree. This disagrees with neither,
    because it is the same arithmetic on both sides — and it is kept under
    2^31 so the multiply cannot exceed a 64-bit py_int, which is the one
    place the subset and Python really would part company.
    """
    return (seed * 1103515245 + 12345) % 2147483648


def put_colour(c: int) -> None:
    if c == 0:
        put("W")
    elif c == 1:
        put("Y")
    elif c == 2:
        put("G")
    elif c == 3:
        put("B")
    elif c == 4:
        put("R")
    else:
        put("O")


def put_row(state: list[int], face: int, row: int) -> None:
    put_colour(state[face * 9 + row * 3 + 0])
    put_colour(state[face * 9 + row * 3 + 1])
    put_colour(state[face * 9 + row * 3 + 2])


def show(state: list[int]) -> None:
    """Print the cube as the usual unfolded cross."""
    row: int = 0
    while row < 3:
        put("   ")
        put_row(state, 0, row)
        print("")
        row = row + 1

    row = 0
    while row < 3:
        put_row(state, 5, row)
        put_row(state, 2, row)
        put_row(state, 4, row)
        put_row(state, 3, row)
        print("")
        row = row + 1

    row = 0
    while row < 3:
        put("   ")
        put_row(state, 1, row)
        print("")
        row = row + 1


def main() -> int:
    # Face f owns state[f*9 .. f*9+8], and a solved sticker's colour is its
    # own face's number.
    state: list[int] = [0] * 54
    i: int = 0
    while i < 54:
        state[i] = i // 9
        i = i + 1

    # Four strips of three, for each of the six faces, as absolute indices.
    adj: list[int] = [
        18, 19, 20, 45, 46, 47, 27, 28, 29, 36, 37, 38,
        24, 25, 26, 42, 43, 44, 33, 34, 35, 51, 52, 53,
        6, 7, 8, 36, 39, 42, 11, 10, 9, 53, 50, 47,
        2, 1, 0, 45, 48, 51, 15, 16, 17, 44, 41, 38,
        2, 5, 8, 33, 30, 27, 11, 14, 17, 20, 23, 26,
        0, 3, 6, 18, 21, 24, 9, 12, 15, 35, 32, 29,
    ]

    print("solved")
    show(state)

    # --- the properties a real cube has -------------------------------
    print("")
    face: int = 0
    while face < 6:
        turn(state, adj, face, 4)
        face = face + 1
    print("every face turned four times, still solved:")
    print(solved(state))

    face = 0
    while face < 6:
        turn(state, adj, face, 1)
        turn(state, adj, face, 3)
        face = face + 1
    print("every move followed by its inverse, still solved:")
    print(solved(state))

    # (R U R' U') has order six on a real cube. If the table above were
    # merely self-consistent rather than correct, this would not come back.
    repeats: int = 0
    while repeats < 6:
        turn(state, adj, 4, 1)
        turn(state, adj, 0, 1)
        turn(state, adj, 4, 3)
        turn(state, adj, 0, 3)
        repeats = repeats + 1
    print("(R U R' U') six times, back to solved:")
    print(solved(state))

    # --- scramble, then undo it exactly -------------------------------
    print("")
    seed: int = 20251022
    moves: list[int] = [0] * 30
    turns: list[int] = [0] * 30

    i = 0
    while i < 30:
        seed = next_random(seed)
        moves[i] = seed % 6
        seed = next_random(seed)
        turns[i] = seed % 3 + 1
        turn(state, adj, moves[i], turns[i])
        i = i + 1

    print("after a 30-move scramble:")
    show(state)
    print("solved?")
    print(solved(state))

    i = 29
    while i >= 0:
        turn(state, adj, moves[i], 4 - turns[i])
        i = i - 1

    print("")
    print("after playing the scramble backwards:")
    show(state)
    print("solved?")
    print(solved(state))
    return 0
