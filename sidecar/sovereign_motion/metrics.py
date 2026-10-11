"""Quality metrics for generated clips. Used to judge backends in the 0.0 spike
and, later, to flag clips that need a foot-locking pass before baking."""

from __future__ import annotations

import math

from .mathutil import Quat, Vec3, quat_mul, quat_rotate
from .result import MotionResult


def world_positions(result: MotionResult, frame_index: int) -> list[Vec3]:
    """Forward kinematics for one frame. Joints are ordered parents-first."""
    return world_transforms(result, frame_index)[0]


def world_transforms(result: MotionResult, frame_index: int) -> tuple[list[Vec3], list[Quat]]:
    """World positions and rotations of every joint for one frame."""
    frame = result.frames[frame_index]
    joints = result.skeleton.joints
    world_rot: list[Quat] = []
    world_pos: list[Vec3] = []
    for i, joint in enumerate(joints):
        local_rot = frame.rotations[i]
        if joint.parent < 0:
            pos = tuple(a + b for a, b in zip(frame.root_translation, joint.offset))
            rot = local_rot
        else:
            parent_rot = world_rot[joint.parent]
            parent_pos = world_pos[joint.parent]
            offset = quat_rotate(parent_rot, joint.offset)
            pos = tuple(a + b for a, b in zip(parent_pos, offset))
            rot = quat_mul(parent_rot, local_rot)
        world_pos.append(pos)
        world_rot.append(rot)
    return world_pos, world_rot


def foot_slide(result: MotionResult, left_foot: str, right_foot: str) -> dict:
    """Mean and max horizontal foot speed (cm/s) on frames flagged as in contact.

    A clean clip has near-zero speed while a foot is planted.
    """
    li = result.skeleton.index(left_foot)
    ri = result.skeleton.index(right_foot)
    positions = [world_positions(result, f) for f in range(len(result.frames))]
    speeds: list[float] = []
    for f in range(1, len(result.frames)):
        for idx, contacts in ((li, result.left_contacts), (ri, result.right_contacts)):
            if contacts[f] and contacts[f - 1]:
                a, b = positions[f - 1][idx], positions[f][idx]
                speeds.append(math.dist(a[:2], b[:2]) * result.fps)
    if not speeds:
        return {"contact_frames": 0, "mean_cm_s": 0.0, "max_cm_s": 0.0}
    return {
        "contact_frames": len(speeds),
        "mean_cm_s": round(sum(speeds) / len(speeds), 3),
        "max_cm_s": round(max(speeds), 3),
    }
