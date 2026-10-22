class Cube:
    """Stores and manages the state of a Rubik's Cube."""

    SIZE: int = 54

    __faces: list[int, 54]
    moves: int

    def __init__(self) -> None:
        self.__faces = [0] * 54
        self.moves = 0
        self.reset()

    def reset(self) -> None:
        i: int = 0
        while i < self.SIZE:
            self.__faces[i] = i // 9
            i = i + 1
        self.moves = 0

    def get_sticker(self, i: int) -> int:
        return self.__faces[i]

    def rotate_face_clockwise(self, face: int) -> None:
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
        self.moves += 1

    def is_solved(self) -> bool:
        i: int = 0
        while i < self.SIZE:
            if self.__faces[i] != i // 9:
                return False
            i = i + 1
        return True


def main() -> int:
    c: Cube = Cube()
    print(c.is_solved(), c.moves, Cube.SIZE)
    c.rotate_face_clockwise(2)
    print(c.is_solved(), c.moves, c.get_sticker(18))
    c.reset()
    print(c.is_solved(), c.moves)
    return 0
