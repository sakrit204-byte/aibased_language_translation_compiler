/* gfx — Win32 + OpenGL implementation of the scalar graphics API.
 *
 * Everything the Python subset cannot express is held here as file-scope
 * state: the window handle, the device and GL contexts, the keyboard table
 * and the start time. The API above passes only numbers.
 *
 * Build:  cc program.c gfx.c -lopengl32 -lgdi32 -luser32 -lm -o program
 */
#include "gfx.h"

#include <windows.h>
#include <GL/gl.h>
#include <math.h>

static HWND          g_window;
static HDC           g_dc;
static HGLRC         g_gl;
static int           g_running;
static unsigned char g_keys[256];
static LARGE_INTEGER g_freq, g_start;
static int           g_width = 800, g_height = 600;

static LRESULT CALLBACK gfx_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CLOSE:
    case WM_DESTROY:
        g_running = 0;
        return 0;
    case WM_KEYDOWN:
    case WM_SYSKEYDOWN:
        if (wp < 256) g_keys[wp] = 1;
        if (wp == VK_ESCAPE) g_running = 0;
        return 0;
    case WM_KEYUP:
    case WM_SYSKEYUP:
        if (wp < 256) g_keys[wp] = 0;
        return 0;
    case WM_SIZE:
        g_width = LOWORD(lp);
        g_height = HIWORD(lp);
        if (g_height < 1) g_height = 1;
        glViewport(0, 0, g_width, g_height);
        return 0;
    default:
        break;
    }
    return DefWindowProcA(hwnd, msg, wp, lp);
}

int gfx_open(long long width, long long height)
{
    WNDCLASSA wc;
    PIXELFORMATDESCRIPTOR pfd;
    RECT rect;
    int format;
    HINSTANCE instance = GetModuleHandleA(NULL);

    g_width = (int)width;
    g_height = (int)height;

    memset(&wc, 0, sizeof wc);
    wc.style = CS_OWNDC;
    wc.lpfnWndProc = gfx_proc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = "gfx_window";
    if (!RegisterClassA(&wc)) return 0;

    rect.left = 0;
    rect.top = 0;
    rect.right = g_width;
    rect.bottom = g_height;
    AdjustWindowRect(&rect, WS_OVERLAPPEDWINDOW, FALSE);

    g_window = CreateWindowA("gfx_window",
                             "Python -> C   (Esc to quit)",
                             WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                             CW_USEDEFAULT, CW_USEDEFAULT,
                             rect.right - rect.left, rect.bottom - rect.top,
                             NULL, NULL, instance, NULL);
    if (!g_window) return 0;

    g_dc = GetDC(g_window);

    memset(&pfd, 0, sizeof pfd);
    pfd.nSize = sizeof pfd;
    pfd.nVersion = 1;
    pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER;
    pfd.iPixelType = PFD_TYPE_RGBA;
    pfd.cColorBits = 32;
    pfd.cDepthBits = 24;
    format = ChoosePixelFormat(g_dc, &pfd);
    if (!format || !SetPixelFormat(g_dc, format, &pfd)) return 0;

    g_gl = wglCreateContext(g_dc);
    if (!g_gl || !wglMakeCurrent(g_dc, g_gl)) return 0;

    glEnable(GL_DEPTH_TEST);
    glEnable(GL_CULL_FACE);
    glCullFace(GL_BACK);
    glShadeModel(GL_SMOOTH);

    /* A single directional light, plus enough ambient that faces turned away
     * from it are still readable. Colours come from glColor via
     * GL_COLOR_MATERIAL so the caller only has to set a colour. */
    {
        GLfloat position[4] = {-0.45f, 0.80f, 0.40f, 0.0f};
        GLfloat diffuse[4]  = {1.0f, 0.97f, 0.90f, 1.0f};
        GLfloat ambient[4]  = {0.30f, 0.32f, 0.38f, 1.0f};
        glEnable(GL_LIGHTING);
        glEnable(GL_LIGHT0);
        glLightfv(GL_LIGHT0, GL_POSITION, position);
        glLightfv(GL_LIGHT0, GL_DIFFUSE, diffuse);
        glLightfv(GL_LIGHT0, GL_AMBIENT, ambient);
        glEnable(GL_COLOR_MATERIAL);
        glColorMaterial(GL_FRONT, GL_AMBIENT_AND_DIFFUSE);
    }

    glViewport(0, 0, g_width, g_height);
    QueryPerformanceFrequency(&g_freq);
    QueryPerformanceCounter(&g_start);
    memset(g_keys, 0, sizeof g_keys);
    g_running = 1;
    return 1;
}

int gfx_running(void) { return g_running; }

void gfx_poll(void)
{
    MSG msg;
    while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
}

void gfx_clear(double r, double g, double b)
{
    glClearColor((GLfloat)r, (GLfloat)g, (GLfloat)b, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
}

void gfx_perspective(double fovy, double znear, double zfar)
{
    /* Built by hand rather than with gluPerspective: GLU is a separate
     * library and this is four lines of arithmetic. */
    double aspect = (double)g_width / (double)(g_height ? g_height : 1);
    double f = 1.0 / tan(fovy * 3.14159265358979323846 / 360.0);
    GLdouble m[16];
    memset(m, 0, sizeof m);
    m[0]  = f / aspect;
    m[5]  = f;
    m[10] = (zfar + znear) / (znear - zfar);
    m[11] = -1.0;
    m[14] = (2.0 * zfar * znear) / (znear - zfar);
    glMatrixMode(GL_PROJECTION);
    glLoadMatrixd(m);
    glMatrixMode(GL_MODELVIEW);
    glLoadIdentity();
}

void gfx_camera(double ex, double ey, double ez,
                double tx, double ty, double tz)
{
    double fx = tx - ex, fy = ty - ey, fz = tz - ez;
    double rx, ry, rz, ux, uy, uz, len;
    GLdouble m[16];

    len = sqrt(fx * fx + fy * fy + fz * fz);
    if (len < 1e-9) return;
    fx /= len; fy /= len; fz /= len;

    /* right = forward x worldUp, with worldUp = (0,1,0), which is
     * (-fz, 0, fx). This had the sign the other way round, making `right`
     * point left and, through up = right x forward, `up` point down — a
     * view mirrored on both axes. A spinning symmetric cube looks identical
     * either way, which is why it survived until something with a
     * distinguishable top and right was drawn. */
    rx = -fz;
    ry = 0.0;
    rz = fx;
    len = sqrt(rx * rx + rz * rz);
    if (len < 1e-9) { rx = 1.0; ry = 0.0; rz = 0.0; }
    else { rx /= len; rz /= len; }

    /* up = right x forward */
    ux = ry * fz - rz * fy;
    uy = rz * fx - rx * fz;
    uz = rx * fy - ry * fx;

    m[0] = rx;  m[4] = ry;  m[8]  = rz;  m[12] = -(rx * ex + ry * ey + rz * ez);
    m[1] = ux;  m[5] = uy;  m[9]  = uz;  m[13] = -(ux * ex + uy * ey + uz * ez);
    m[2] = -fx; m[6] = -fy; m[10] = -fz; m[14] =  (fx * ex + fy * ey + fz * ez);
    m[3] = 0.0; m[7] = 0.0; m[11] = 0.0; m[15] = 1.0;

    glMatrixMode(GL_MODELVIEW);
    glLoadMatrixd(m);
}

void gfx_color(double r, double g, double b)
{
    glColor3d(r, g, b);
}

void gfx_tri(double x1, double y1, double z1,
             double x2, double y2, double z2,
             double x3, double y3, double z3)
{
    /* The caller has no arrays and no vector type, so the face normal is
     * computed here from the winding order. */
    double ax = x2 - x1, ay = y2 - y1, az = z2 - z1;
    double bx = x3 - x1, by = y3 - y1, bz = z3 - z1;
    double nx = ay * bz - az * by;
    double ny = az * bx - ax * bz;
    double nz = ax * by - ay * bx;
    double len = sqrt(nx * nx + ny * ny + nz * nz);
    if (len > 1e-12) { nx /= len; ny /= len; nz /= len; }

    glBegin(GL_TRIANGLES);
    glNormal3d(nx, ny, nz);
    glVertex3d(x1, y1, z1);
    glVertex3d(x2, y2, z2);
    glVertex3d(x3, y3, z3);
    glEnd();
}

void gfx_present(void)
{
    SwapBuffers(g_dc);
}

int gfx_key(long long code)
{
    if (code < 0 || code > 255) return 0;
    return g_keys[code] ? 1 : 0;
}

long long gfx_key_left(void)  { return VK_LEFT; }
long long gfx_key_right(void) { return VK_RIGHT; }
long long gfx_key_up(void)    { return VK_UP; }
long long gfx_key_down(void)  { return VK_DOWN; }
long long gfx_key_space(void) { return VK_SPACE; }

double gfx_time(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    return (double)(now.QuadPart - g_start.QuadPart) / (double)g_freq.QuadPart;
}

void gfx_close(void)
{
    if (g_gl) { wglMakeCurrent(NULL, NULL); wglDeleteContext(g_gl); g_gl = NULL; }
    if (g_dc && g_window) { ReleaseDC(g_window, g_dc); g_dc = NULL; }
    if (g_window) { DestroyWindow(g_window); g_window = NULL; }
    g_running = 0;
}
