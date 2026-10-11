"""BVH export, the 0.0 spike's route into Unreal (BVH -> Blender -> FBX -> UE).

Unreal is left-handed (X forward, Y right, Z up). BVH tools like Blender expect
right-handed axes, so Y is negated on export. The file stays Z-up and in
centimeters: in Blender's BVH importer set Up = Z, Forward = X, Scale = 0.01.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..mathutil import Quat, Vec3, quat_to_matrix
from ..result import MotionResult

END_SITE_LENGTH_CM = 8.0


def _mirror_vec(v: Vec3) -> Vec3:
    return (v[0], -v[1], v[2])


def _mirror_quat(q: Quat) -> Quat:
    # Reflecting across the XZ plane flips the rotation sense of the X and Z axis components.
    return (-q[0], q[1], -q[2], q[3])


def quat_to_euler_zxy_deg(q: Quat) -> tuple[float, float, float]:
    """Decompose into (z, x, y) degrees such that R = Rz(z) @ Rx(x) @ Ry(y), BVH channel order."""
    m = quat_to_matrix(q)
    x = math.asin(max(-1.0, min(1.0, m[2][1])))
    if abs(m[2][1]) < 0.999999:
        z = math.atan2(-m[0][1], m[1][1])
        y = math.atan2(-m[2][0], m[2][2])
    else:  # gimbal lock: fold everything into z
        z = math.atan2(m[1][0], m[0][0])
        y = 0.0
    return math.degrees(z), math.degrees(x), math.degrees(y)


def _fmt(values) -> str:
    # "+ 0.0" turns -0.0 into 0.0; the second check catches tiny negatives that round to zero.
    out = []
    for v in values:
        s = f"{v + 0.0:.4f}"
        out.append("0.0000" if s == "-0.0000" else s)
    return " ".join(out)


def to_bvh(result: MotionResult) -> str:
    skeleton = result.skeleton
    lines: list[str] = ["HIERARCHY"]

    def write_joint(index: int, depth: int) -> None:
        joint = skeleton.joints[index]
        pad = "\t" * depth
        if joint.parent < 0:
            lines.append(f"{pad}ROOT {joint.name}")
        else:
            lines.append(f"{pad}JOINT {joint.name}")
        lines.append(pad + "{")
        lines.append(f"{pad}\tOFFSET {_fmt(_mirror_vec(joint.offset))}")
        if joint.parent < 0:
            lines.append(f"{pad}\tCHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation")
        else:
            lines.append(f"{pad}\tCHANNELS 3 Zrotation Xrotation Yrotation")
        children = skeleton.children(index)
        for child in children:
            write_joint(child, depth + 1)
        if not children:
            # Point the end site along the bone so leaf bones get a visible length.
            length = math.sqrt(sum(c * c for c in joint.offset))
            if length > 0:
                tip = tuple(c / length * END_SITE_LENGTH_CM for c in joint.offset)
            else:
                tip = (0.0, 0.0, END_SITE_LENGTH_CM)
            lines.append(f"{pad}\tEnd Site")
            lines.append(pad + "\t{")
            lines.append(f"{pad}\t\tOFFSET {_fmt(_mirror_vec(tip))}")
            lines.append(pad + "\t}")
        lines.append(pad + "}")

    roots = [i for i, j in enumerate(skeleton.joints) if j.parent < 0]
    if len(roots) != 1:
        raise ValueError("BVH export needs exactly one root joint")
    write_joint(roots[0], 0)

    # BVH frame data follows hierarchy (depth-first) order, not skeleton list order.
    order: list[int] = []

    def visit(index: int) -> None:
        order.append(index)
        for child in skeleton.children(index):
            visit(child)

    visit(roots[0])

    lines.append("MOTION")
    lines.append(f"Frames: {len(result.frames)}")
    lines.append(f"Frame Time: {1.0 / result.fps:.6f}")
    for frame in result.frames:
        values: list[float] = list(_mirror_vec(frame.root_translation))
        for index in order:
            values.extend(quat_to_euler_zxy_deg(_mirror_quat(frame.rotations[index])))
        lines.append(_fmt(values))
    return "\n".join(lines) + "\n"


def write_bvh(result: MotionResult, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(to_bvh(result))
