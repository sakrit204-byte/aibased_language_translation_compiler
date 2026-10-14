/* ------------------------------------------------------------------ *
 *  gfx — a scalar-only graphics API for compiled Python-subset programs
 * ------------------------------------------------------------------ *
 *
 *  The subset has no arrays, no pointers and no function values, so it
 *  cannot call OpenGL directly: a vertex buffer is an array, a window is a
 *  handle, and input is a callback. Every one of those is a thing the source
 *  language cannot express, and no amount of translation invents them.
 *
 *  So the awkward parts live here instead. This shim owns the window handle,
 *  the GL context, the keyboard state and the frame timer, and exposes an
 *  API that takes and returns nothing but numbers — which the subset *can*
 *  express. The translated program does the 3D maths and the game logic; it
 *  asks this layer to put triangles on the screen.
 *
 *  Drawing is immediate mode (glBegin/glVertex3f) on purpose. Modern OpenGL
 *  needs vertex arrays uploaded to the GPU; immediate mode needs only scalar
 *  calls, which is exactly what a program with no arrays can produce.
 *
 *  Arguments are double and long long because that is what the compiler emits
 *  for py_float and py_int.
 * ------------------------------------------------------------------ */
#ifndef GFX_H
#define GFX_H

/* Open a window and a GL context. Returns 1 on success, 0 on failure. */
int gfx_open(long long width, long long height);

/* 1 while the window is open and Escape has not been pressed. */
int gfx_running(void);

/* Pump the OS message queue. Call once per frame. */
void gfx_poll(void);

/* Clear the colour and depth buffers. Components are 0.0 .. 1.0. */
void gfx_clear(double r, double g, double b);

/* Set up a perspective projection. fovy is in degrees. */
void gfx_perspective(double fovy, double znear, double zfar);

/* Place the camera at (ex,ey,ez) looking at (tx,ty,tz), Y up. */
void gfx_camera(double ex, double ey, double ez,
                double tx, double ty, double tz);

/* Colour for subsequent triangles, 0.0 .. 1.0. */
void gfx_color(double r, double g, double b);

/* One triangle. Its normal is computed here, so the caller need not. */
void gfx_tri(double x1, double y1, double z1,
             double x2, double y2, double z2,
             double x3, double y3, double z3);

/* Show the frame that has been drawn. */
void gfx_present(void);

/* 1 if the key is currently down. Codes are Windows virtual-key values;
 * gfx_key_left() and friends below name the ones a program is likely to want
 * so the caller need not know them. */
int gfx_key(long long code);
long long gfx_key_left(void);
long long gfx_key_right(void);
long long gfx_key_up(void);
long long gfx_key_down(void);
long long gfx_key_space(void);

/* Seconds since gfx_open, as a double. */
double gfx_time(void);

/* Close the window and release the context. */
void gfx_close(void);

#endif /* GFX_H */
