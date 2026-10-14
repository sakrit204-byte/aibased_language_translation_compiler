"""
API profiles: extra C libraries the gate is allowed to translate calls onto.

By default the gate may only produce pure `<math.h>` expressions, because
anything wider gives up the guarantee that model output cannot touch the
machine. A profile widens that deliberately and by name: it lists the exact
functions that become callable, and the header the generated C must include.

The graphics profile is the interesting one. It is what makes a windowed 3D
program translatable at all. The subset has no arrays, no pointers and no
function values, so it cannot call OpenGL directly — a vertex buffer is an
array, a window is a handle, input is a callback. The shim in
src/runtime/gfx.c owns all three and exposes an API of nothing but numbers,
which the subset *can* express. The model's job is then only to map a
Pythonic call onto the matching C function.
"""


class Profile:
    def __init__(self, name, header, link_sources, link_flags, functions,
                 description):
        self.name = name
        self.header = header
        self.link_sources = link_sources      # extra .c files to compile in
        self.link_flags = link_flags          # extra linker flags
        self.functions = functions            # name -> C signature, for the prompt
        self.description = description

    def allowed_names(self):
        return set(self.functions)

    def prompt_section(self):
        lines = [self.description, "",
                 f"Available C functions (declared in <{self.header}>):"]
        for name in sorted(self.functions):
            lines.append(f"  {self.functions[name]}")
        return "\n".join(lines)


GFX = Profile(
    name="gfx",
    header="gfx.h",
    link_sources=["src/runtime/gfx.c"],
    link_flags=["-lopengl32", "-lgdi32", "-luser32"],
    functions={
        "gfx_open":        "int gfx_open(long long width, long long height);",
        "gfx_running":     "int gfx_running(void);",
        "gfx_poll":        "void gfx_poll(void);",
        "gfx_clear":       "void gfx_clear(double r, double g, double b);",
        "gfx_perspective": "void gfx_perspective(double fovy, double znear, double zfar);",
        "gfx_camera":      ("void gfx_camera(double ex, double ey, double ez,"
                            " double tx, double ty, double tz);"),
        "gfx_color":       "void gfx_color(double r, double g, double b);",
        "gfx_tri":         ("void gfx_tri(double x1, double y1, double z1,"
                            " double x2, double y2, double z2,"
                            " double x3, double y3, double z3);"),
        "gfx_present":     "void gfx_present(void);",
        "gfx_key":         "int gfx_key(long long code);",
        "gfx_key_left":    "long long gfx_key_left(void);",
        "gfx_key_right":   "long long gfx_key_right(void);",
        "gfx_key_up":      "long long gfx_key_up(void);",
        "gfx_key_down":    "long long gfx_key_down(void);",
        "gfx_key_space":   "long long gfx_key_space(void);",
        "gfx_time":        "double gfx_time(void);",
        "gfx_close":       "void gfx_close(void);",
    },
    description=(
        "This program draws 3D graphics through a small C shim. The shim owns "
        "the window, the GL context and the keyboard, so every function below "
        "takes and returns only numbers."),
)

PROFILES = {"gfx": GFX}


def get(name):
    return PROFILES.get(name)
