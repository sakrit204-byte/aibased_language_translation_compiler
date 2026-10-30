
import pygame
import random
import sys

pygame.init()

# -------------------- SETTINGS --------------------
WIDTH, HEIGHT = 1440, 650
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Triple Rubik's Cube")
clock = pygame.time.Clock()

BG = (24, 27, 38)
WHITE = (245, 245, 245)
GRAY = (70, 75, 90)

COLORS = {
    "U": (245, 245, 245),  # White
    "D": (255, 210, 0),    # Yellow
    "F": (0, 190, 80),     # Green
    "B": (40, 100, 240),   # Blue
    "R": (220, 40, 45),    # Red
    "L": (255, 130, 20),   # Orange
}

# Each face has a normal, right direction, and down direction.
BASIS = {
    "U": ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
    "D": ((0, -1, 0), (1, 0, 0), (0, 0, -1)),
    "F": ((0, 0, 1), (1, 0, 0), (0, -1, 0)),
    "B": ((0, 0, -1), (-1, 0, 0), (0, -1, 0)),
    "R": ((1, 0, 0), (0, 0, -1), (0, -1, 0)),
    "L": ((-1, 0, 0), (0, 0, 1), (0, -1, 0)),
}

# Unfolded cube layout.
NET = {
    "U": (1, 0),
    "L": (0, 1),
    "F": (1, 1),
    "R": (2, 1),
    "B": (3, 1),
    "D": (1, 2),
}

CELL = 27
CUBE_WIDTH = CELL * 4
CUBE_HEIGHT = CELL * 3

FONT = pygame.font.SysFont("arial", 20, bold=True)
SMALL = pygame.font.SysFont("arial", 15)
TITLE = pygame.font.SysFont("arial", 28, bold=True)

def add(a, b):
    return tuple(x + y for x, y in zip(a, b))

def scale(v, k):
    return tuple(x * k for x in v)

def dot(a, b):
    return sum(x * y for x, y in zip(a, b))

def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )

def rotate_vector(v, axis, direction):
    # Rotate a vector by +90 or -90 degrees around an axis.
    parallel = scale(axis, dot(axis, v))
    perpendicular = scale(cross(axis, v), direction)
    return add(parallel, perpendicular)

class RubiksCube:
    def __init__(self):
        self.reset()

    def reset(self):
        self.stickers = {}

        for face, (normal, right, down) in BASIS.items():
            for row in range(3):
                for col in range(3):
                    position = add(
                        normal,
                        add(scale(right, col - 1),
                            scale(down, row - 1))
                    )
                    self.stickers[(position, normal)] = COLORS[face]

        self.moves = 0

    def turn(self, face, inverse=False):
        axis = BASIS[face][0]
        direction = -1 if inverse else 1
        updated = {}

        for (position, normal), color in self.stickers.items():
            if dot(position, axis) == 1:
                new_position = rotate_vector(
                    position, axis, direction
                )
                new_normal = rotate_vector(
                    normal, axis, direction
                )
                updated[(new_position, new_normal)] = color
            else:
                updated[(position, normal)] = color

        self.stickers = updated
        self.moves += 1

    def scramble(self, count=25):
        faces = list(BASIS.keys())

        for _ in range(count):
            self.turn(
                random.choice(faces),
                random.choice([True, False])
            )

    def solved(self):
        for face, (normal, _, _) in BASIS.items():
            face_colors = [
                color
                for (position, n), color in self.stickers.items()
                if n == normal
            ]

            if any(color != COLORS[face] for color in face_colors):
                return False

        return True

    def draw(self, surface, origin_x, origin_y, number):
        # Draw all six faces in an unfolded cube layout.
        for face, (net_x, net_y) in NET.items():
            normal, right, down = BASIS[face]

            for row in range(3):
                for col in range(3):
                    position = add(
                        normal,
                        add(scale(right, col - 1),
                            scale(down, row - 1))
                    )

                    color = self.stickers[(position, normal)]

                    x = origin_x + net_x * CELL + col * CELL
                    y = origin_y + net_y * CELL + row * CELL

                    pygame.draw.rect(
                        surface, color,
                        (x + 1, y + 1, CELL - 2, CELL - 2),
                        border_radius=3
                    )
                    pygame.draw.rect(
                        surface, (15, 15, 20),
                        (x, y, CELL, CELL), 1
                    )

        label = FONT.render(f"Cube {number}", True, WHITE)
        surface.blit(label, (origin_x + 30, origin_y - 35))

        moves = SMALL.render(
            f"Moves: {self.moves}", True, WHITE
        )
        surface.blit(
            moves, (origin_x + 30, origin_y + CUBE_HEIGHT + 10)
        )

        if self.solved():
            status = SMALL.render("SOLVED!", True, (60, 255, 130))
            surface.blit(
                status,
                (origin_x + 30, origin_y + CUBE_HEIGHT + 30)
            )


# -------------------- GAME SETUP --------------------

cubes = [RubiksCube() for _ in range(3)]
selected = 0

# Keyboard controls:
# 1, 2, 3 = select cube
# U, D, L, R, F, B = rotate a face
# Hold SHIFT for the inverse turn
# S = scramble selected cube
# X = reset selected cube
# SPACE = scramble all three cubes

def draw_button(text, x, y, w, h):
    pygame.draw.rect(
        screen, GRAY, (x, y, w, h), border_radius=8
    )
    label = FONT.render(text, True, WHITE)
    screen.blit(
        label,
        (x + (w - label.get_width()) // 2,
         y + (h - label.get_height()) // 2)
    )

running = True

while running:
    screen.fill(BG)

    heading = TITLE.render(
        "TRIPLE RUBIK'S CUBE", True, WHITE
    )
    screen.blit(
        heading,
        ((WIDTH - heading.get_width()) // 2, 20)
    )

    for i, cube in enumerate(cubes):
        x = 55 + i * 465
        y = 150

        if i == selected:
            pygame.draw.rect(
                screen, (75, 95, 145),
                (x - 12, y - 48, CUBE_WIDTH + 24,
                 CUBE_HEIGHT + 105),
                border_radius=12
            )

        cube.draw(screen, x, y, i + 1)

    draw_button("SCRAMBLE", 410, 490, 150, 42)
    draw_button("RESET", 580, 490, 120, 42)
    draw_button("SCRAMBLE ALL", 720, 490, 180, 42)

    help_text = [
        "Select cube: 1 / 2 / 3",
        "Turn face: U D L R F B",
        "Hold SHIFT for inverse turn",
        "S: Scramble   X: Reset   SPACE: Scramble all",
    ]

    for i, line in enumerate(help_text):
        text = SMALL.render(line, True, WHITE)
        screen.blit(text, (WIDTH // 2 - 160, 555 + i * 19))

    pygame.display.flip()

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                running = False

            elif event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                selected = event.key - pygame.K_1

            elif event.key == pygame.K_s:
                cubes[selected].scramble()

            elif event.key == pygame.K_x:
                cubes[selected].reset()

            elif event.key == pygame.K_SPACE:
                for cube in cubes:
                    cube.scramble()

            else:
                key_to_face = {
                    pygame.K_u: "U",
                    pygame.K_d: "D",
                    pygame.K_l: "L",
                    pygame.K_r: "R",
                    pygame.K_f: "F",
                    pygame.K_b: "B",
                }

                if event.key in key_to_face:
                    cubes[selected].turn(
                        key_to_face[event.key],
                        inverse=bool(
                            pygame.key.get_mods() & pygame.KMOD_SHIFT
                        )
                    )

        elif event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos

            if 410 <= mx <= 560 and 490 <= my <= 532:
                cubes[selected].scramble()

            elif 580 <= mx <= 700 and 490 <= my <= 532:
                cubes[selected].reset()

            elif 720 <= mx <= 900 and 490 <= my <= 532:
                for cube in cubes:
                    cube.scramble()

    clock.tick(60)

pygame.quit()
sys.exit()
