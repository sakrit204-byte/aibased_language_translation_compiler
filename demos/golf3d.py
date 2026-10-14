# =====================================================================
#  ASCII 3D GOLF
#  written in the Python subset accepted by the hybrid Python-to-C compiler
# =====================================================================
#
#  Every frame is ray-marched: one ray is cast per character cell, against an
#  analytic height field, the ball sphere and the flagstick. The surface normal
#  comes from finite differences of the terrain function, and the brightness of
#  the lit surface picks a character from a ramp.
#
#  There are no arrays anywhere in this file. The subset has none, so the
#  terrain is a mathematical function rather than a stored grid and each pixel
#  is shaded and written immediately with put(). That constraint is the reason
#  the renderer works the way it does.
#
#  The compiler has no rule for sqrt, sin, cos, atan2, floor or fabs, so every
#  one of them is translated by the AI gate and proved to compile before use.
#  Without --ai this file does not compile at all.
#
#  Run the original:    python golf3d.py          (needs the shim below)
#  Translate and run:   python -m src.driver "golf3d.py" --run --ai
# =====================================================================

def view_w() -> int:
    return 72


def view_h() -> int:
    return 26


# ---------------------------------------------------------------- helpers

def length3(x: float, y: float, z: float) -> float:
    return sqrt(x * x + y * y + z * z)


def length2(x: float, z: float) -> float:
    return sqrt(x * x + z * z)


def clampf(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def smooth_fall(d: float, radius: float) -> float:
    """1.0 at the centre, easing to 0.0 at `radius`."""
    if d >= radius:
        return 0.0
    t = 1.0 - d / radius
    return t * t * (3.0 - 2.0 * t) * 0.5


# ------------------------------------------------------------- the course

def hole_x(hole: int) -> float:
    if hole == 1:
        return 0.0
    elif hole == 2:
        return 14.0
    else:
        return -16.0


def hole_z(hole: int) -> float:
    if hole == 1:
        return 58.0
    elif hole == 2:
        return 72.0
    else:
        return 66.0


def tee_x(hole: int) -> float:
    if hole == 1:
        return 0.0
    elif hole == 2:
        return -6.0
    else:
        return 8.0


def par_of(hole: int) -> int:
    if hole == 1:
        return 3
    elif hole == 2:
        return 4
    else:
        return 4


def base_terrain(x: float, z: float) -> float:
    """Rolling parkland, built from layered sine waves."""
    h = 0.0
    h = h + sin(x * 0.085) * 2.6
    h = h + cos(z * 0.062) * 2.1
    h = h + sin((x + z) * 0.041) * 3.1
    h = h + cos((x - z) * 0.11) * 0.9
    h = h + sin(x * 0.23 + z * 0.17) * 0.55
    return h


def terrain_height(x: float, z: float, hx: float, hz: float) -> float:
    """Terrain, flattened into a putting green around the hole and a level tee."""
    h = base_terrain(x, z)

    # The green: pull the surface toward one height so putts behave.
    dg = length2(x - hx, z - hz)
    g = smooth_fall(dg, 11.0) * 2.0
    if g > 1.0:
        g = 1.0
    green_level = base_terrain(hx, hz)
    h = h * (1.0 - g) + green_level * g

    # The tee box, flat and slightly raised.
    dt = length2(x, z + 4.0)
    t = smooth_fall(dt, 7.0) * 2.0
    if t > 1.0:
        t = 1.0
    h = h * (1.0 - t) + (base_terrain(0.0, -4.0) + 0.35) * t

    # A bunker short and right of every green.
    db = length2(x - (hx + 7.5), z - (hz - 8.0))
    h = h - smooth_fall(db, 5.0) * 2.4
    return h


def on_green(x: float, z: float, hx: float, hz: float) -> bool:
    return length2(x - hx, z - hz) < 9.0


def in_bunker(x: float, z: float, hx: float, hz: float) -> bool:
    return length2(x - (hx + 7.5), z - (hz - 8.0)) < 4.4


def on_fairway(x: float, z: float, hx: float, hz: float) -> bool:
    """Distance from the tee-to-hole line, which is the fairway corridor."""
    ax = 0.0
    az = -4.0
    bx = hx - ax
    bz = hz - az
    px = x - ax
    pz = z - az
    denom = bx * bx + bz * bz
    if denom < 0.0001:
        return False
    t = clampf((px * bx + pz * bz) / denom, 0.0, 1.0)
    return length2(px - bx * t, pz - bz * t) < 9.5


# ---------------------------------------------------------------- shading

def normal_y(x: float, z: float, hx: float, hz: float) -> float:
    """Upward component of the surface normal, from finite differences."""
    e = 0.6
    dx = terrain_height(x + e, z, hx, hz) - terrain_height(x - e, z, hx, hz)
    dz = terrain_height(x, z + e, hx, hz) - terrain_height(x, z - e, hx, hz)
    return 2.0 * e / length3(-dx, 2.0 * e, -dz)


def light_at(x: float, z: float, hx: float, hz: float) -> float:
    """Lambert term for a sun low in the west, plus a little ambient."""
    e = 0.6
    dx = terrain_height(x + e, z, hx, hz) - terrain_height(x - e, z, hx, hz)
    dz = terrain_height(x, z + e, hx, hz) - terrain_height(x, z - e, hx, hz)
    nx = -dx
    ny = 2.0 * e
    nz = -dz
    n = length3(nx, ny, nz)
    nx = nx / n
    ny = ny / n
    nz = nz / n
    # sun direction, normalised by hand
    lx = -0.55
    ly = 0.72
    lz = -0.42
    d = nx * lx + ny * ly + nz * lz
    if d < 0.0:
        d = 0.0
    return clampf(0.22 + 0.78 * d, 0.0, 1.0)


def ramp(level: float) -> str:
    if level < 0.10:
        return " "
    elif level < 0.20:
        return "."
    elif level < 0.32:
        return ":"
    elif level < 0.44:
        return "-"
    elif level < 0.56:
        return "="
    elif level < 0.68:
        return "+"
    elif level < 0.80:
        return "*"
    elif level < 0.91:
        return "#"
    else:
        return "@"


def surface_char(x: float, z: float, hx: float, hz: float, lit: float) -> str:
    """Pick a glyph from the material, modulated by the light."""
    if in_bunker(x, z, hx, hz):
        if lit > 0.62:
            return "~"
        return "-"
    if on_green(x, z, hx, hz):
        if lit > 0.70:
            return "+"
        elif lit > 0.45:
            return "-"
        return ":"
    if on_fairway(x, z, hx, hz):
        return ramp(lit)
    # rough: same ramp, darkened, so it reads as coarser ground
    return ramp(lit * 0.72)


# ----------------------------------------------------------------- camera

def cam_height(hole: int) -> float:
    """A slightly different vantage per hole, so they do not all look alike."""
    if hole == 2:
        return 8.6
    if hole == 3:
        return 6.8
    return 7.5


def cam_back(hole: int) -> float:
    if hole == 2:
        return 16.5
    if hole == 3:
        return 13.5
    return 15.0


def cam_x(bx: float, bz: float, hx: float, hz: float, hole: int) -> float:
    dx = hx - bx
    dz = hz - bz
    d = length2(dx, dz)
    if d < 0.001:
        return bx
    return bx - dx / d * cam_back(hole)


def cam_z(bx: float, bz: float, hx: float, hz: float, hole: int) -> float:
    dx = hx - bx
    dz = hz - bz
    d = length2(dx, dz)
    if d < 0.001:
        return bz - cam_back(hole)
    return bz - dz / d * cam_back(hole)


def cam_y(bx: float, bz: float, hx: float, hz: float, hole: int) -> float:
    cx = cam_x(bx, bz, hx, hz, hole)
    cz = cam_z(bx, bz, hx, hz, hole)
    return terrain_height(cx, cz, hx, hz) + cam_height(hole)


# ------------------------------------------------------------- ray casting

def sphere_hit(ox: float, oy: float, oz: float,
               dx: float, dy: float, dz: float,
               cx: float, cy: float, cz: float, r: float) -> float:
    """Distance along the ray to a sphere, or -1.0 for a miss."""
    ex = ox - cx
    ey = oy - cy
    ez = oz - cz
    b = ex * dx + ey * dy + ez * dz
    c = ex * ex + ey * ey + ez * ez - r * r
    disc = b * b - c
    if disc < 0.0:
        return -1.0
    t = -b - sqrt(disc)
    if t < 0.05:
        return -1.0
    return t


def pin_hit(ox: float, oy: float, oz: float,
            dx: float, dy: float, dz: float,
            hx: float, hy: float, hz: float) -> float:
    """Distance to the flagstick, or -1.0 for a miss.

    Solved rather than marched. The stick is a vertical rod, so in plan view
    this is just the closest approach of a 2D ray to a point; stepping along
    the ray instead walks straight past something this thin.

    The radius grows with distance so the stick stays about one character
    wide. A rod of its true thickness is far narrower than a pixel at any
    real distance, and would flicker in and out as the camera moved.
    """
    denom = dx * dx + dz * dz
    if denom < 0.000001:
        return -1.0
    t = ((hx - ox) * dx + (hz - oz) * dz) / denom
    if t < 0.5:
        return -1.0
    radius = 0.18 + t * 0.013
    if length2(ox + dx * t - hx, oz + dz * t - hz) > radius:
        return -1.0
    py = oy + dy * t
    if py < hy or py > hy + 3.4:
        return -1.0
    return t


def march_terrain(ox: float, oy: float, oz: float,
                  dx: float, dy: float, dz: float,
                  hx: float, hz: float) -> float:
    """March until the ray drops below the surface, then bisect once."""
    t = 1.0
    step = 0.7
    prev = t
    for i in range(150):
        px = ox + dx * t
        py = oy + dy * t
        pz = oz + dz * t
        if py < terrain_height(px, pz, hx, hz):
            # one bisection pass sharpens the silhouette considerably
            lo = prev
            hi = t
            for k in range(6):
                mid = (lo + hi) * 0.5
                mx = ox + dx * mid
                my = oy + dy * mid
                mz = oz + dz * mid
                if my < terrain_height(mx, mz, hx, hz):
                    hi = mid
                else:
                    lo = mid
            return (lo + hi) * 0.5
        prev = t
        step = 0.7 + t * 0.045
        t = t + step
        if t > 115.0:
            return -1.0
    return -1.0


def sky_char(sy: float) -> str:
    if sy > 0.62:
        return " "
    elif sy > 0.40:
        return "."
    elif sy > 0.26:
        return ":"
    else:
        return "-"


def shade(row: int, col: int,
          bx: float, by: float, bz: float,
          hx: float, hz: float, hole: int) -> str:
    """Colour of one character cell."""
    cx = cam_x(bx, bz, hx, hz, hole)
    cy = cam_y(bx, bz, hx, hz, hole)
    cz = cam_z(bx, bz, hx, hz, hole)

    # look from the camera toward a point just ahead of the ball
    tx = bx * 0.35 + hx * 0.65
    tz = bz * 0.35 + hz * 0.65
    ty = terrain_height(tx, tz, hx, hz) + 1.2

    fx = tx - cx
    fy = ty - cy
    fz = tz - cz
    f = length3(fx, fy, fz)
    fx = fx / f
    fy = fy / f
    fz = fz / f

    # right = normalise(cross(forward, world_up))
    rx = fz
    rz = -fx
    r = length2(rx, rz)
    if r < 0.0001:
        rx = 1.0
        rz = 0.0
    else:
        rx = rx / r
        rz = rz / r

    # up = cross(right, forward)
    ux = rz * fy
    uy = rx * fz - rz * fx
    uz = -rx * fy

    sx = (float(col) + 0.5) / float(view_w()) * 2.0 - 1.0
    sy = 1.0 - (float(row) + 0.5) / float(view_h()) * 2.0
    # characters are about twice as tall as they are wide
    aspect = float(view_w()) / float(view_h()) * 0.5
    fov = 0.80

    dx = fx + (rx * sx * aspect + ux * sy) * fov
    dy = fy + (uy * sy) * fov
    dz = fz + (rz * sx * aspect + uz * sy) * fov
    d = length3(dx, dy, dz)
    dx = dx / d
    dy = dy / d
    dz = dz / d

    hy = terrain_height(hx, hz, hx, hz)

    t_ball = sphere_hit(cx, cy, cz, dx, dy, dz, bx, by, bz, 0.42)
    t_land = march_terrain(cx, cy, cz, dx, dy, dz, hx, hz)
    t_pin = pin_hit(cx, cy, cz, dx, dy, dz, hx, hy, hz)

    best = 1000000.0
    what = 0
    if t_ball > 0.0 and t_ball < best:
        best = t_ball
        what = 1
    if t_pin > 0.0 and t_pin < best:
        best = t_pin
        what = 2
    if t_land > 0.0 and t_land < best:
        best = t_land
        what = 3

    if what == 1:
        return "O"
    if what == 2:
        # the flag itself sits at the top of the stick
        py = cy + dy * best
        if py > hy + 2.5:
            return "P"
        return "|"
    if what == 3:
        px = cx + dx * best
        pz = cz + dz * best
        if length2(px - hx, pz - hz) < 0.65:
            return "o"
        lit = light_at(px, pz, hx, hz)
        # haze: distant ground fades toward the sky
        fade = clampf(1.0 - best / 130.0, 0.0, 1.0)
        return surface_char(px, pz, hx, hz, lit * (0.35 + 0.65 * fade))
    return sky_char(dy + 0.35)


# ----------------------------------------------------------------- drawing

def draw_frame(bx: float, by: float, bz: float,
               hx: float, hz: float, hole: int) -> None:
    for row in range(view_h()):
        for col in range(view_w()):
            put(shade(row, col, bx, by, bz, hx, hz, hole))
        put("\n")


def rule(n: int) -> None:
    for i in range(n):
        put("-")
    put("\n")


# ----------------------------------------------------------------- physics

def ground_at(x: float, z: float, hx: float, hz: float) -> float:
    return terrain_height(x, z, hx, hz)


def friction_for(x: float, z: float, hx: float, hz: float) -> float:
    if in_bunker(x, z, hx, hz):
        return 0.72
    if on_green(x, z, hx, hz):
        return 0.985
    if on_fairway(x, z, hx, hz):
        return 0.962
    return 0.918


# ------------------------------------------------------------------- shots

def fract(v: float) -> float:
    return v - floor(v)


def jitter(seed: float) -> float:
    """Deterministic pseudo-randomness in -1.0 .. 1.0.

    The subset has no random number generator and no state to seed one with,
    so this is the standard hash-of-a-sine trick. Being deterministic is a
    feature here: the generated C and the original Python must play exactly
    the same round for their output to be compared byte for byte.
    """
    return fract(sin(seed * 12.9898) * 43758.5453) * 2.0 - 1.0


def aim_power(dist: float) -> float:
    """How hard the player swings for a given distance to the pin."""
    p = sqrt(dist) * 2.42
    if p > 26.0:
        p = 26.0
    if p < 4.5:
        p = 4.5
    return p


def loft_for(dist: float) -> float:
    if dist < 12.0:
        return 0.06
    if dist < 30.0:
        return 0.42
    return 0.60


def main() -> int:
    print("================================================================")
    print("                      A S C I I   3 D   G O L F")
    print("   Python subset  ->  C      every frame ray-marched, no arrays")
    print("================================================================")
    print("")

    total: int = 0
    total_par: int = 0
    hole: int = 1

    while hole <= 3:
        hx = hole_x(hole)
        hz = hole_z(hole)
        par = par_of(hole)
        total_par += par

        bx = tee_x(hole)
        bz = -4.0
        by = terrain_height(bx, bz, hx, hz) + 0.42

        print("")
        rule(72)
        print("  HOLE", hole, " par", par, "  length",
              int(length2(hx - bx, hz - bz)), "m")
        rule(72)

        strokes: int = 0
        sunk = False

        while strokes < 7 and not sunk:
            dist = length2(hx - bx, hz - bz)

            print("")
            print("  Stroke", strokes + 1, " ball to pin:",
                  round(dist, 1), "m")
            # Only the opening strokes are drawn; a full round of frames would
            # be thousands of lines of output.
            if strokes < 2:
                print("")
                draw_frame(bx, by, bz, hx, hz, hole)

            # aim at the pin, then miss it slightly, the way a golfer does
            adx = hx - bx
            adz = hz - bz
            a = length2(adx, adz)
            if a < 0.001:
                a = 1.0
            adx = adx / a
            adz = adz / a

            seed = float(hole) * 7.3 + float(strokes) * 3.1
            # long shots stray further than short ones
            spread = clampf(dist / 45.0, 0.22, 1.0)
            err = jitter(seed) * 0.075 * spread
            ndx = adx * cos(err) - adz * sin(err)
            ndz = adx * sin(err) + adz * cos(err)
            adx = ndx
            adz = ndz

            speed = aim_power(dist) * (1.0 + jitter(seed + 1.7) * 0.085)
            loft = loft_for(dist)
            vx = adx * speed
            vz = adz * speed
            vy = speed * loft

            # a little wind, different every hole, so shots are not perfect
            wind = sin(float(hole) * 2.1) * 0.55
            vx += wind

            # integrate the flight
            step = 0.028
            flying = True
            guard = 0
            while flying and guard < 1500:
                guard += 1
                vy -= 19.6 * step
                bx += vx * step
                by += vy * step
                bz += vz * step

                g = ground_at(bx, bz, hx, hz)
                if by - 0.42 <= g:
                    by = g + 0.42
                    if vy < 0.0:
                        vy = -vy * 0.34
                    f = friction_for(bx, bz, hx, hz)
                    vx = vx * f
                    vz = vz * f
                    if vy < 1.2:
                        vy = 0.0
                    speed_now = length3(vx, vy, vz)
                    if speed_now < 0.55:
                        flying = False

                if length2(bx - hx, bz - hz) < 0.62:
                    if length3(vx, vy, vz) < 6.0:
                        sunk = True
                        flying = False

            strokes += 1

            if sunk:
                bx = hx
                bz = hz
                by = terrain_height(hx, hz, hx, hz) + 0.42

        total += strokes

        print("")
        if sunk:
            print("  Holed out in", strokes, "  par", par, "  score",
                  strokes - par)
        else:
            print("  Picked up after", strokes, "strokes")
        draw_frame(bx, by, bz, hx, hz, hole)

        hole += 1

    print("")
    rule(72)
    print("  FINAL CARD    strokes", total, "   par", total_par,
          "   to par", total - total_par)
    rule(72)
    return 0
