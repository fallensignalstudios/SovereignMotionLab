"""Unitree G1 (MotionBricks' output skeleton) -> our result format.

MotionBricks produces MuJoCo `qpos` frames for the 29-DOF G1: root position (3),
root quaternion (w, x, y, z) and 29 hinge angles. We run MuJoCo's own forward
kinematics to get every body's world transform, then express the clip in the
result convention: Unreal axes (Z-up, X-forward, left-handed), centimeters,
joint-local rotations relative to an identity-rotation bind pose.

Requires `mujoco` and `numpy` (installed with the motionbricks extra). Mesh
files are not needed: they are stripped before loading, so Git LFS mesh stubs
are fine.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from .mathutil import Quat, Vec3, quat_conjugate, quat_mul
from .result import Frame, Joint, MotionResult, Skeleton

METERS_TO_CM = 100.0
G1_SKELETON_NAME = "unitree_g1_29dof"

# Contact detection thresholds for the ankle-roll bodies (MotionBricks' qpos carries no contact flags).
CONTACT_HEIGHT_MARGIN_CM = 3.0
CONTACT_MAX_SPEED_CM_S = 25.0


def _mirror_vec(v) -> Vec3:
    """MuJoCo (right-handed, Z-up, X-forward, meters) -> Unreal (left-handed, cm): negate Y."""
    return (float(v[0]) * METERS_TO_CM, -float(v[1]) * METERS_TO_CM, float(v[2]) * METERS_TO_CM)


def _mirror_quat_wxyz(q) -> Quat:
    """MuJoCo quaternion (w, x, y, z) -> Unreal-axes quaternion (x, y, z, w) under the Y mirror."""
    w, x, y, z = (float(c) for c in q)
    return (-x, y, -z, w)


def load_kinematic_model(xml_path: str | Path):
    """Load a MuJoCo model with all mesh geometry removed (only the kinematic tree matters here)."""
    import mujoco

    root = ET.parse(xml_path).getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "mesh" or (child.tag == "geom" and (child.get("mesh") or child.get("type") == "mesh")):
                parent.remove(child)
    for compiler in root.findall("compiler"):
        compiler.attrib.pop("meshdir", None)
    return mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))


class G1Converter:
    """Converts G1 qpos frames into MotionResults on the G1 skeleton."""

    def __init__(self, xml_path: str | Path):
        import mujoco
        import numpy as np

        self._mujoco = mujoco
        self._np = np
        self.model = load_kinematic_model(xml_path)
        self.data = mujoco.MjData(self.model)
        if self.model.nq != 36:
            raise ValueError(f"expected the 29-DOF G1 (nq=36), got nq={self.model.nq} from {xml_path}")

        # Bodies 1..N (0 is the MuJoCo world). Parents always have lower ids, so the order is parents-first.
        self.body_ids = list(range(1, self.model.nbody))
        self.names = [mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, b) for b in self.body_ids]

        # Bind pose: the model's default qpos (zero joint angles, standing at its default height).
        bind_pos, bind_rot = self._fk(self.model.qpos0)
        self.bind_pos = bind_pos
        self.bind_rot = bind_rot
        joints = []
        for i, body in enumerate(self.body_ids):
            parent_body = int(self.model.body_parentid[body])
            parent = parent_body - 1  # world (0) -> -1
            if parent < 0:
                offset = bind_pos[i]
            else:
                offset = tuple(a - b for a, b in zip(bind_pos[i], bind_pos[parent]))
            joints.append(Joint(self.names[i], parent, offset))
        self.skeleton = Skeleton(G1_SKELETON_NAME, joints)

    def _fk(self, qpos) -> tuple[list[Vec3], list[Quat]]:
        self.data.qpos[:] = qpos
        self._mujoco.mj_kinematics(self.model, self.data)
        positions = [_mirror_vec(self.data.xpos[b]) for b in self.body_ids]
        rotations = [_mirror_quat_wxyz(self.data.xquat[b]) for b in self.body_ids]
        return positions, rotations

    def to_result(self, qpos_frames, fps: int, request_id: str, backend: str) -> MotionResult:
        """Convert an iterable of 36-float qpos frames into a MotionResult on the G1 skeleton."""
        frames: list[Frame] = []
        ankle_l = self.names.index("left_ankle_roll_link")
        ankle_r = self.names.index("right_ankle_roll_link")
        ankles: list[tuple[Vec3, Vec3]] = []
        root_bind = self.bind_pos[0]
        for qpos in qpos_frames:
            positions, rotations = self._fk(qpos)
            # World deltas from the bind pose, then joint-local in the identity-bind convention.
            deltas = [quat_mul(rotations[i], quat_conjugate(self.bind_rot[i])) for i in range(len(rotations))]
            local: list[Quat] = []
            for i, joint in enumerate(self.skeleton.joints):
                if joint.parent < 0:
                    local.append(deltas[i])
                else:
                    local.append(quat_mul(quat_conjugate(deltas[joint.parent]), deltas[i]))
            root = tuple(a - b for a, b in zip(positions[0], root_bind))
            frames.append(Frame(root, local))
            ankles.append((positions[ankle_l], positions[ankle_r]))

        left, right = self._contacts([a[0] for a in ankles], fps, self.bind_pos[ankle_l][2]), \
            self._contacts([a[1] for a in ankles], fps, self.bind_pos[ankle_r][2])
        return MotionResult(
            request_id=request_id,
            backend=backend,
            fps=fps,
            skeleton=self.skeleton,
            frames=frames,
            left_contacts=left,
            right_contacts=right,
            diagnostics={"notes": []},
        )

    @staticmethod
    def _contacts(positions: list[Vec3], fps: int, bind_height: float) -> list[bool]:
        """A foot is planted when it is near its standing height and barely moving horizontally."""
        contacts = []
        for f, pos in enumerate(positions):
            prev = positions[max(0, f - 1)]
            nxt = positions[min(len(positions) - 1, f + 1)]
            span = max(1, min(len(positions) - 1, f + 1) - max(0, f - 1))
            speed = math.dist(prev[:2], nxt[:2]) * fps / span
            low = pos[2] <= bind_height + CONTACT_HEIGHT_MARGIN_CM
            contacts.append(low and speed <= CONTACT_MAX_SPEED_CM_S)
        return contacts
