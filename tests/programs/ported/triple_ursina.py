
from ursina import *
import random

app = Ursina()

window.title = "Triple 3D Rubik's Cube"
window.borderless = False
window.color = color.rgb(18, 22, 35)
window.exit_button.visible = False

camera.position = (0, 0, -18)
camera.rotation = (0, 0, 0)
camera.fov = 65

COLORS = {
    'U': color.white,
    'D': color.yellow,
    'F': color.green,
    'B': color.blue,
    'R': color.red,
    'L': color.orange,
}

# Front is the negative Z direction.
FACES = {
    'U': (Vec3(0, 1, 0), Vec3(0, 0.51, 0)),
    'D': (Vec3(0, -1, 0), Vec3(0, -0.51, 0)),
    'F': (Vec3(0, 0, -1), Vec3(0, 0, -0.51)),
    'B': (Vec3(0, 0, 1), Vec3(0, 0, 0.51)),
    'R': (Vec3(1, 0, 0), Vec3(0.51, 0, 0)),
    'L': (Vec3(-1, 0, 0), Vec3(-0.51, 0, 0)),
}

FACE_AXIS = {
    'U': (1, 1),
    'D': (1, -1),
    'R': (0, 1),
    'L': (0, -1),
    'F': (2, -1),
    'B': (2, 1),
}

def rotate_coord(p, axis, turns):
    """Rotate an integer coordinate by quarter turns."""
    x, y, z = p
    for _ in range(turns % 4):
        if axis == 0:
            x, y, z = x, -z, y
        elif axis == 1:
            x, y, z = z, y, -x
        else:
            x, y, z = -y, x, z
    return (x, y, z)


class Cube:
    def __init__(self, center, number):
        self.center = Vec3(*center)
        self.number = number
        self.pieces = {}
        self.moves = 0
        self.create()

    def create(self):
        self.pieces.clear()

        for x in (-1, 0, 1):
            for y in (-1, 0, 1):
                for z in (-1, 0, 1):
                    pos = (x, y, z)

                    piece = Entity(
                        model='cube',
                        color=color.rgb(12, 13, 18),
                        scale=0.96,
                        position=self.center + Vec3(x, y, z),
                        collider='box'
                    )

                    piece.cube_coord = pos
                    piece.stickers = {}

                    for face, (normal, offset) in FACES.items():
                        if round(
                            x * normal.x + y * normal.y
                            + z * normal.z
                        ) == 1:
                            sticker = Entity(
                                parent=piece,
                                model='cube',
                                color=COLORS[face],
                                position=offset,
                                scale=0.88,
                            )
                            piece.stickers[face] = sticker

                    self.pieces[pos] = piece

    def turn(self, face, inverse=False):
        axis, layer_sign = FACE_AXIS[face]
        layer = layer_sign
        turns = -1 if inverse else 1

        selected = [
            (pos, piece)
            for pos, piece in self.pieces.items()
            if pos[axis] == layer
        ]

        # Rotate coordinates and visual cubies together.
        new_positions = {}

        for pos, piece in selected:
            new_pos = rotate_coord(pos, axis, turns)

            piece.cube_coord = new_pos
            piece.position = self.center + Vec3(*new_pos)

            # Quarter-turn rotation of the cubie's orientation.
            if axis == 0:
                piece.rotation_x += turns * 90
            elif axis == 1:
                piece.rotation_y += turns * 90
            else:
                piece.rotation_z += turns * 90

            new_positions[new_pos] = piece

        for pos, _ in selected:
            del self.pieces[pos]

        self.pieces.update(new_positions)
        self.moves += 1
        update_status()

    def scramble(self, count=25):
        faces = list(FACES.keys())
        previous = None

        for _ in range(count):
            face = random.choice([f for f in faces if f != previous])
            self.turn(face, random.choice([False, True]))
            previous = face

    def reset(self):
        for piece in self.pieces.values():
            destroy(piece)

        self.pieces = {}
        self.moves = 0
        self.create()
        update_status()

    def solved(self):
        # Each visible face must have its original color.
        for face, (normal, _) in FACES.items():
            axis = next(
                i for i, value in enumerate(normal)
                if value != 0
            )
            sign = int(normal[axis])

            visible = [
                piece for pos, piece in self.pieces.items()
                if pos[axis] == sign
            ]

            for piece in visible:
                sticker = piece.stickers.get(face)

                if sticker is None or sticker.color != COLORS[face]:
                    return False

        return True


cubes = [
    Cube((-4.5, 0, 0), 1),
    Cube((0, 0, 0), 2),
    Cube((4.5, 0, 0), 3),
]

selected = 0

heading = Text(
    text='TRIPLE RUBIK\'S CUBE',
    origin=(0, 0),
    y=0.44,
    scale=1.8,
    color=color.azure
)

selected_text = Text(
    text='',
    origin=(0, 0),
    y=-0.32,
    scale=1.1
)

help_text = Text(
    text=(
        '1 / 2 / 3: Select Cube\n'
        'U D L R F B: Rotate Faces\n'
        'Hold SHIFT: Reverse Turn\n'
        'S: Scramble   X: Reset   SPACE: Scramble All'
    ),
    origin=(0, 0),
    y=-0.40,
    scale=0.8,
    color=color.light_gray
)

def update_status():
    cube = cubes[selected]
    result = 'SOLVED!' if cube.solved() else 'Keep solving!'
    selected_text.text = (
        f'Cube {selected + 1}  |  Moves: {cube.moves}  |  {result}'
    )

def input(key):
    global selected

    if key in ('1', '2', '3'):
        selected = int(key) - 1
        update_status()
        return

    if key == 'escape':
        application.quit()

    if key == 's':
        cubes[selected].scramble()
        return

    if key == 'x':
        cubes[selected].reset()
        return

    if key == 'space':
        for cube in cubes:
            cube.scramble()
        return

    if key in ('u', 'd', 'l', 'r', 'f', 'b'):
        face = key.upper()
        inverse = held_keys['shift']
        cubes[selected].turn(face, inverse)


# Lighting and background
AmbientLight(color=color.rgba(180, 180, 180, 255))

sun = DirectionalLight()
sun.rotation = (35, -35, 35)

# Smooth orbiting camera effect can be added later.
update_status()
app.run()
