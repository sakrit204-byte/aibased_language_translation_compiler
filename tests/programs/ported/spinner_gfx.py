import gfx
import math


def main() -> int:
    if gfx.open(640, 480) == 0:
        return 1

    # The original's 120px half-width in a 640x480 window, expressed as world
    # units for a camera 6 back with a 45 degree field of view.
    size: float = 1.2
    quarter: float = math.pi / 2.0

    angle: float = 0.0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    x3: float = 0.0
    y3: float = 0.0

    while gfx.running() != 0:
        gfx.poll()

        # The original turned at 1.5 radians per second off a frame clock.
        # Reading the clock directly gives the same rate without one.
        angle = gfx.time() * 1.5

        x0 = math.cos(angle) * size
        y0 = math.sin(angle) * size
        x1 = math.cos(angle + quarter) * size
        y1 = math.sin(angle + quarter) * size
        x2 = math.cos(angle + quarter * 2.0) * size
        y2 = math.sin(angle + quarter * 2.0) * size
        x3 = math.cos(angle + quarter * 3.0) * size
        y3 = math.sin(angle + quarter * 3.0) * size

        # (23, 26, 33) and (80, 200, 120), as fractions.
        gfx.clear(0.0902, 0.1020, 0.1294)
        gfx.perspective(45.0, 0.1, 100.0)
        gfx.camera(0.0, 0.0, 6.0, 0.0, 0.0, 0.0)

        gfx.color(0.3137, 0.7843, 0.4706)
        gfx.tri(x0, y0, 0.0, x1, y1, 0.0, x2, y2, 0.0)
        gfx.tri(x0, y0, 0.0, x2, y2, 0.0, x3, y3, 0.0)

        gfx.present()

    gfx.close()
    return 0
