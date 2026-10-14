# Needs: sin, cos, radians, sqrt, atan2, degrees
# The widest AI surface of the ten: six separate translations the compiler
# has no rule for, all in one program.

def range_of_shot(speed: float, angle_deg: float, gravity: float) -> float:
    return speed * speed * sin(2.0 * radians(angle_deg)) / gravity


def peak_height(speed: float, angle_deg: float, gravity: float) -> float:
    vertical = speed * sin(radians(angle_deg))
    return vertical * vertical / (2.0 * gravity)


def flight_time(speed: float, angle_deg: float, gravity: float) -> float:
    return 2.0 * speed * sin(radians(angle_deg)) / gravity


def impact_angle(speed: float, angle_deg: float) -> float:
    return degrees(atan2(speed * sin(radians(angle_deg)),
                         speed * cos(radians(angle_deg))))


def main() -> int:
    g = 9.81
    for a in range(15, 76, 15):
        angle = float(a)
        print(a, "deg  range =", round(range_of_shot(20.0, angle, g), 4),
              " peak =", round(peak_height(20.0, angle, g), 4),
              " time =", round(flight_time(20.0, angle, g), 4))
    print("launch angle check:", round(impact_angle(20.0, 40.0), 6))
    print("escape-ish speed  :", round(sqrt(2.0 * g * 100.0), 6))
    return 0
