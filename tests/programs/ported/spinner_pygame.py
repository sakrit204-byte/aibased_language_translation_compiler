# A pygame program: a spinning square. Its behaviour is a window, so there is
# nothing on stdout for the usual comparison to work with.
import pygame
import math

WIDTH, HEIGHT = 640, 480


class Spinner:
    def __init__(self, size=120):
        self.size = size
        self.angle = 0.0

    def corners(self, cx, cy):
        points = []
        for i in range(4):
            a = self.angle + i * math.pi / 2
            points.append((cx + math.cos(a) * self.size,
                           cy + math.sin(a) * self.size))
        return points

    def step(self, dt):
        self.angle += dt * 1.5


def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    clock = pygame.time.Clock()
    spinner = Spinner()
    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
        spinner.step(dt)
        screen.fill((23, 26, 33))
        pygame.draw.polygon(screen, (80, 200, 120),
                            spinner.corners(WIDTH // 2, HEIGHT // 2))
        pygame.display.flip()
    pygame.quit()
