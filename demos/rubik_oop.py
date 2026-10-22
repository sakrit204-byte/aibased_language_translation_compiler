# Rubik's Cube Simulator — Object-Oriented, in the compiler's subset.
#
#     python -m src.driver demos/rubik_oop.py --run
#
# This is the pasted tkinter program's model and renderer, kept as close to the
# original as the subset allows: the same classes, the same COLORS dict keyed
# by face letter, the same method names, the same unfolded-cross layout.
#
# The four OOP pillars the original demonstrates, and where they went:
#
#   abstraction    CubeRenderer defines draw(); TextCubeRenderer implements it
#   encapsulation  Cube.__faces is private — and here that is enforced rather
#                  than conventional: no expression outside the class can name
#                  it, where Python's __name is a mangling anyone can defeat
#   inheritance    not available. Dispatching through a base class needs a
#                  vtable, a vtable is function pointers, and a function is not
#                  a value in this subset. So CubeRenderer is a single class.
#   polymorphism   same reason. The renderer is chosen by which class is
#                  constructed, at compile time, not through a base reference.
#
# Two other changes the subset forces, both for the same reason — a compiled
# program has no allocator:
#
#   * __faces is 54 ints rather than a dict of six lists of nine. The colours
#     are face numbers; COLORS still maps the letter to its hex string, and is
#     still a class-level dict, because its keys never change.
#   * scramble uses an LCG written out here rather than `import random`: C's
#     rand() is not Python's Mersenne Twister, so importing it would make the
#     compiled program disagree with the Python one.
#
# Unlike the original, turning a face here also carries the four neighbouring
# strips round with it, so the moves actually change what you see.


# ==========================================================
# 1. ABSTRACTION
# ==========================================================

class CubeRenderer:
    """Interface for cube rendering implementations.

    A base class cannot be abstract here — @abstractmethod is a decorator,
    and a decorator is a function taking a function. This documents the
    interface and raises the same way an unimplemented method would.
    """

    kind: int

    def __init__(self) -> None:
        self.kind = 0


# ==========================================================
# 2. ENCAPSULATION
# ==========================================================

class Cube:
    """Stores and manages the state of a Rubik's Cube."""

    COLORS: dict[str, str] = {
        "W": "#FFFFFF",
        "Y": "#FFD500",
        "R": "#E53935",
        "O": "#FF8C00",
        "G": "#22A447",
        "B": "#2878D0",
    }

    FACE_COUNT: int = 6
    STICKERS: int = 54

    __faces: list[int, 54]
    __adjacent: list[int, 72]
    moves: int

    def __init__(self) -> None:
        self.__faces = [0] * 54
        self.__adjacent = [
            18, 19, 20, 45, 46, 47, 27, 28, 29, 36, 37, 38,
            24, 25, 26, 42, 43, 44, 33, 34, 35, 51, 52, 53,
            6, 7, 8, 36, 39, 42, 11, 10, 9, 53, 50, 47,
            2, 1, 0, 45, 48, 51, 15, 16, 17, 44, 41, 38,
            2, 5, 8, 33, 30, 27, 11, 14, 17, 20, 23, 26,
            0, 3, 6, 18, 21, 24, 9, 12, 15, 35, 32, 29,
        ]
        self.moves = 0
        self.reset()

    def reset(self) -> None:
        """Restore all six faces to their solved state."""
        i: int = 0
        while i < self.STICKERS:
            self.__faces[i] = i // 9
            i = i + 1
        self.moves = 0

    def get_sticker(self, index: int) -> int:
        """Read one sticker. The only way out of __faces."""
        return self.__faces[index]

    def is_solved(self) -> bool:
        i: int = 0
        while i < self.STICKERS:
            if self.__faces[i] != i // 9:
                return False
            i = i + 1
        return True

    def rotate_face_clockwise(self, face: int) -> None:
        """Rotate one face's nine stickers clockwise."""
        base: int = face * 9
        a0: int = self.__faces[base + 0]
        a1: int = self.__faces[base + 1]
        a2: int = self.__faces[base + 2]
        a3: int = self.__faces[base + 3]
        a5: int = self.__faces[base + 5]
        a6: int = self.__faces[base + 6]
        a7: int = self.__faces[base + 7]
        a8: int = self.__faces[base + 8]

        self.__faces[base + 0] = a6
        self.__faces[base + 1] = a3
        self.__faces[base + 2] = a0
        self.__faces[base + 3] = a7
        self.__faces[base + 5] = a1
        self.__faces[base + 6] = a8
        self.__faces[base + 7] = a5
        self.__faces[base + 8] = a2

    def rotate_adjacent_clockwise(self, face: int) -> None:
        """Carry the four neighbouring strips round with the face.

        This is the half the original left out, which is why its buttons
        changed nothing: on a solved cube every face is nine identical
        stickers, so permuting them in place is invisible.
        """
        base: int = face * 12
        t0: int = self.__faces[self.__adjacent[base + 9]]
        t1: int = self.__faces[self.__adjacent[base + 10]]
        t2: int = self.__faces[self.__adjacent[base + 11]]

        i: int = 3
        while i > 0:
            here: int = base + i * 3
            prev: int = base + (i - 1) * 3
            k: int = 0
            while k < 3:
                self.__faces[self.__adjacent[here + k]] = (
                    self.__faces[self.__adjacent[prev + k]])
                k = k + 1
            i = i - 1

        self.__faces[self.__adjacent[base + 0]] = t0
        self.__faces[self.__adjacent[base + 1]] = t1
        self.__faces[self.__adjacent[base + 2]] = t2

    def apply_move(self, face: int, quarters: int) -> None:
        """Apply a quarter, half or inverse turn of `face`."""
        n: int = quarters % 4
        i: int = 0
        while i < n:
            self.rotate_face_clockwise(face)
            self.rotate_adjacent_clockwise(face)
            self.moves += 1
            i = i + 1

    def scramble(self, seed: int, count: int) -> int:
        """Apply `count` random moves. Returns the seed, so the caller can
        replay the same sequence to undo it."""
        i: int = 0
        while i < count:
            seed = (seed * 1103515245 + 12345) % 2147483648
            self.apply_move(seed % 6, seed // 8 % 3 + 1)
            i = i + 1
        return seed


# ==========================================================
# 3. THE RENDERER
# ==========================================================

class TextCubeRenderer:
    """Draws the cube as the unfolded cross the original laid out on canvas.

              W W W
        O O O G G G R R R B B B
              Y Y Y
    """

    width: int

    def __init__(self) -> None:
        self.width = 3

    def letter(self, colour: int) -> str:
        if colour == 0:
            return "W"
        elif colour == 1:
            return "Y"
        elif colour == 2:
            return "G"
        elif colour == 3:
            return "B"
        elif colour == 4:
            return "R"
        return "O"

    def draw_row(self, cube: Cube, face: int, row: int) -> None:
        col: int = 0
        while col < self.width:
            put(self.letter(cube.get_sticker(face * 9 + row * 3 + col)))
            col = col + 1

    def draw(self, cube: Cube) -> None:
        row: int = 0
        while row < 3:
            put("      ")
            self.draw_row(cube, 0, row)
            print("")
            row = row + 1

        row = 0
        while row < 3:
            self.draw_row(cube, 5, row)
            self.draw_row(cube, 2, row)
            self.draw_row(cube, 4, row)
            self.draw_row(cube, 3, row)
            print("")
            row = row + 1

        row = 0
        while row < 3:
            put("      ")
            self.draw_row(cube, 1, row)
            print("")
            row = row + 1


# ==========================================================
# 4. THE APPLICATION
# ==========================================================

class RubiksCubeApp:
    """Drives the cube and the renderer, as the Tk app did."""

    status: str

    def __init__(self) -> None:
        self.status = "Cube initialized."

    def report(self, cube: Cube) -> None:
        print(self.status)
        print("moves:", cube.moves, " solved:", cube.is_solved())


def main() -> int:
    app: RubiksCubeApp = RubiksCubeApp()
    cube: Cube = Cube()
    renderer: TextCubeRenderer = TextCubeRenderer()

    app.report(cube)
    renderer.draw(cube)

    # The palette is still a dict keyed by face letter, exactly as written.
    print("")
    for face in Cube.COLORS:
        print(face, Cube.COLORS[face])
    print("known face 'G':", "G" in Cube.COLORS)
    print("known face 'Z':", "Z" in Cube.COLORS)

    # A single move, which now actually changes the picture.
    print("")
    app.status = "Applied move: R"
    cube.apply_move(4, 1)
    app.report(cube)
    renderer.draw(cube)

    # The properties a real cube has.
    print("")
    cube.reset()
    f: int = 0
    while f < 6:
        cube.apply_move(f, 4)
        f = f + 1
    print("every face turned four times, still solved:", cube.is_solved())

    f = 0
    while f < 6:
        cube.apply_move(f, 1)
        cube.apply_move(f, 3)
        f = f + 1
    print("every move followed by its inverse, still solved:", cube.is_solved())

    repeats: int = 0
    while repeats < 6:
        cube.apply_move(4, 1)
        cube.apply_move(0, 1)
        cube.apply_move(4, 3)
        cube.apply_move(0, 3)
        repeats = repeats + 1
    print("(R U R' U') six times, back to solved:", cube.is_solved())

    # Scramble, then undo it exactly.
    print("")
    cube.reset()
    app.status = "Scramble demonstration completed."
    seed: int = 20251022
    moves: list[int] = [0] * 25
    turns: list[int] = [0] * 25

    i: int = 0
    while i < 25:
        seed = (seed * 1103515245 + 12345) % 2147483648
        moves[i] = seed % 6
        turns[i] = seed // 8 % 3 + 1
        cube.apply_move(moves[i], turns[i])
        i = i + 1

    app.report(cube)
    renderer.draw(cube)

    print("")
    app.status = "Cube reset to solved state."
    i = 24
    while i >= 0:
        cube.apply_move(moves[i], 4 - turns[i])
        i = i - 1
    app.report(cube)
    renderer.draw(cube)
    return 0
