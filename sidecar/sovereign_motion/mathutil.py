"""Small quaternion helpers. Quaternions are (x, y, z, w) tuples, matching Unreal's FQuat order."""

from __future__ import annotations

import math

Quat = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]

IDENTITY: Quat = (0.0, 0.0, 0.0, 1.0)


def quat_from_axis_angle(axis: Vec3, angle_rad: float) -> Quat:
    ax, ay, az = axis
    length = math.sqrt(ax * ax + ay * ay + az * az)
    if length == 0.0:
        return IDENTITY
    s = math.sin(angle_rad / 2.0) / length
    return (ax * s, ay * s, az * s, math.cos(angle_rad / 2.0))


def quat_mul(a: Quat, b: Quat) -> Quat:
    """Hamilton product a * b (apply b first, then a)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_to_matrix(q: Quat) -> list[list[float]]:
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def quat_rotate(q: Quat, v: Vec3) -> Vec3:
    m = quat_to_matrix(q)
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def lerp_angle_rad(a: float, b: float, t: float) -> float:
    """Interpolate between two angles along the shortest arc."""
    delta = (b - a + math.pi) % (2.0 * math.pi) - math.pi
    return a + delta * t


def quat_conjugate(q: Quat) -> Quat:
    return (-q[0], -q[1], -q[2], q[3])


def normalize(v: Vec3) -> Vec3:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length == 0.0:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def quat_from_to(a: Vec3, b: Vec3) -> Quat:
    """Shortest-arc rotation taking direction a onto direction b."""
    a, b = normalize(a), normalize(b)
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    if d < -0.999999:
        # Opposite directions: rotate 180 degrees about any perpendicular axis.
        axis = cross((1.0, 0.0, 0.0), a)
        if sum(c * c for c in axis) < 1e-12:
            axis = cross((0.0, 1.0, 0.0), a)
        return quat_from_axis_angle(axis, math.pi)
    c = cross(a, b)
    q = (c[0], c[1], c[2], 1.0 + d)
    n = math.sqrt(sum(x * x for x in q))
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)
