"""Retarget a clip between skeletons that both use the result convention.

Both clips use an identity-rotation bind pose, so each bone's world rotation is
simply "the delta since bind". For every mapped target bone we take the source
bone's world delta and append a fixed alignment that maps the target's bind pose
onto the source's:

    TargetDelta(t) = SourceDelta(t) * Align
    Align          = from_to(Facing * target_bind_dir, source_bind_dir) * Facing

`Facing` turns the target mesh's forward axis onto the source's (Mannequin meshes
face +Y, the G1 faces +X); `from_to` lines up the bone directions (e.g. A-pose
arms onto the G1's hanging arms). Bones without a mapping follow their parent.
Root motion is scaled by the ratio of leg lengths. The target keeps its own bone
lengths, so its proportions are preserved.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .mathutil import (
    IDENTITY,
    Quat,
    Vec3,
    quat_conjugate,
    quat_from_axis_angle,
    quat_from_to,
    quat_mul,
    quat_rotate,
)
from .result import Frame, MotionResult, Skeleton

Z_AXIS = (0.0, 0.0, 1.0)


@dataclass(frozen=True)
class BoneMap:
    target: str
    source: str
    # Child bones defining each side's bone direction, or None to align facing only.
    target_child: str | None = None
    source_child: str | None = None


# Unitree G1 (MotionBricks) -> Unreal Mannequin (UE4 SK_Mannequin, UE5 Manny/Quinn).
G1_TO_MANNEQUIN: tuple[BoneMap, ...] = (
    BoneMap("pelvis", "pelvis"),
    BoneMap("spine_01", "waist_yaw_link"),
    # Topmost spine bone takes the torso; whichever exists (UE5: spine_05, UE4: spine_03).
    BoneMap("spine_05", "torso_link"),
    BoneMap("spine_03", "torso_link"),
    BoneMap("thigh_l", "left_hip_yaw_link", "calf_l", "left_knee_link"),
    BoneMap("calf_l", "left_knee_link", "foot_l", "left_ankle_pitch_link"),
    BoneMap("foot_l", "left_ankle_roll_link"),
    BoneMap("thigh_r", "right_hip_yaw_link", "calf_r", "right_knee_link"),
    BoneMap("calf_r", "right_knee_link", "foot_r", "right_ankle_pitch_link"),
    BoneMap("foot_r", "right_ankle_roll_link"),
    BoneMap("upperarm_l", "left_shoulder_yaw_link", "lowerarm_l", "left_elbow_link"),
    BoneMap("lowerarm_l", "left_elbow_link", "hand_l", "left_wrist_roll_link"),
    BoneMap("hand_l", "left_wrist_yaw_link"),
    BoneMap("upperarm_r", "right_shoulder_yaw_link", "lowerarm_r", "right_elbow_link"),
    BoneMap("lowerarm_r", "right_elbow_link", "hand_r", "right_wrist_roll_link"),
    BoneMap("hand_r", "right_wrist_yaw_link"),
)

# Bones whose alignment is inherited from the bone above them, so they keep their bind
# relationship to it (hands to forearms, feet to shins) instead of being re-aimed.
INHERIT_ALIGNMENT = {"hand_l": "lowerarm_l", "hand_r": "lowerarm_r", "foot_l": "calf_l", "foot_r": "calf_r"}


def _component_positions(skeleton: Skeleton) -> list[Vec3]:
    positions: list[Vec3] = []
    for joint in skeleton.joints:
        if joint.parent < 0:
            positions.append(joint.offset)
        else:
            p = positions[joint.parent]
            positions.append((p[0] + joint.offset[0], p[1] + joint.offset[1], p[2] + joint.offset[2]))
    return positions


def _facing_yaw(skeleton: Skeleton, positions: list[Vec3], foot: str, toe: str | None) -> float:
    """Mesh facing from the feet: foot -> toe (or ball) direction, flattened. Defaults to +X."""
    names = {j.name: i for i, j in enumerate(skeleton.joints)}
    dx = dy = 0.0
    for side in ("l", "r"):
        f, b = foot.format(side=side), (toe or "").format(side=side)
        if f in names and b in names:
            dx += positions[names[b]][0] - positions[names[f]][0]
            dy += positions[names[b]][1] - positions[names[f]][1]
    return math.atan2(dy, dx) if (dx or dy) else 0.0


def _leg_length(skeleton: Skeleton, positions: list[Vec3], hip: str, ankle: str) -> float:
    names = {j.name: i for i, j in enumerate(skeleton.joints)}
    return positions[names[hip]][2] - positions[names[ankle]][2]


def retarget(
    source: MotionResult,
    target: Skeleton,
    mapping: tuple[BoneMap, ...] = G1_TO_MANNEQUIN,
    source_facing: tuple[str, str | None] = ("{side}", None),
    source_legs: tuple[str, str] = ("left_hip_pitch_link", "left_ankle_roll_link"),
) -> tuple[MotionResult, list[str]]:
    """Retarget `source` onto `target`. Returns the new clip and notes."""
    notes: list[str] = []
    src_names = {j.name: i for i, j in enumerate(source.skeleton.joints)}
    tgt_names = {j.name: i for i, j in enumerate(target.joints)}
    src_pos = _component_positions(source.skeleton)
    tgt_pos = _component_positions(target)

    # Facing correction: rotate the target's forward onto the source's (G1 faces +X, Mannequins +Y).
    target_yaw = _facing_yaw(target, tgt_pos, "foot_{side}", "ball_{side}")
    source_yaw = 0.0 if source_facing[1] is None else _facing_yaw(source.skeleton, src_pos, *source_facing)
    facing = quat_from_axis_angle(Z_AXIS, source_yaw - target_yaw)

    # Usable mappings: both bones exist (the spine entries cover UE4 and UE5; one of them applies).
    active: dict[int, int] = {}
    align: dict[int, Quat] = {}
    for m in mapping:
        if m.target not in tgt_names or m.source not in src_names or tgt_names[m.target] in active:
            continue
        t_idx, s_idx = tgt_names[m.target], src_names[m.source]
        a = facing
        if m.target_child in tgt_names and m.source_child in src_names:
            t_dir = tuple(c - p for c, p in zip(tgt_pos[tgt_names[m.target_child]], tgt_pos[t_idx]))
            s_dir = tuple(c - p for c, p in zip(src_pos[src_names[m.source_child]], src_pos[s_idx]))
            a = quat_mul(quat_from_to(quat_rotate(facing, t_dir), s_dir), facing)
        active[t_idx] = s_idx
        align[t_idx] = a
    for bone, parent_bone in INHERIT_ALIGNMENT.items():
        if bone in tgt_names and parent_bone in tgt_names and tgt_names[parent_bone] in align:
            align[tgt_names[bone]] = align[tgt_names[parent_bone]]
    missing = [m.target for m in mapping if m.target not in tgt_names and not m.target.startswith("spine_0")]
    if missing:
        notes.append(f"retarget: target has no {', '.join(missing)}; those parts follow their parents")

    # Root motion scale from leg length (hip height above ankle).
    src_leg = _leg_length(source.skeleton, src_pos, *source_legs)
    tgt_leg = _leg_length(target, tgt_pos, "thigh_l", "foot_l") if "thigh_l" in tgt_names and "foot_l" in tgt_names else src_leg
    scale = tgt_leg / src_leg if src_leg > 1e-6 else 1.0
    notes.append(f"retargeted {source.skeleton.name} -> {target.name} (root motion scale x{scale:.2f})")

    if source.skeleton.joints[0].name != "pelvis":
        raise ValueError("retarget expects the source root joint to be its pelvis")
    src_pelvis = 0
    src_pelvis_bind = src_pos[0]

    frames: list[Frame] = []
    for frame in source.frames:
        # Source world deltas (rotation since bind) and pelvis position.
        deltas: list[Quat] = []
        for i, joint in enumerate(source.skeleton.joints):
            local = frame.rotations[i]
            deltas.append(local if joint.parent < 0 else quat_mul(deltas[joint.parent], local))
        pelvis_world = tuple(r + o for r, o in zip(frame.root_translation, src_pelvis_bind))

        target_deltas: list[Quat] = []
        rotations: list[Quat] = []
        for b, joint in enumerate(target.joints):
            parent_delta = target_deltas[joint.parent] if joint.parent >= 0 else IDENTITY
            if b in active:
                delta = quat_mul(deltas[active[b]], align[b])
            elif joint.parent < 0:
                # Target root (e.g. Mannequin "root" at the floor) turns with the source's heading only.
                delta = quat_mul(_yaw_only(deltas[src_pelvis]), facing)
            else:
                delta = parent_delta
            target_deltas.append(delta)
            rotations.append(quat_mul(quat_conjugate(parent_delta), delta))

        # Root motion: the source pelvis's ground position and height change (crouch, bob), scaled.
        # The target's own root offset carries its hip height if its root is the pelvis.
        root = (
            pelvis_world[0] * scale,
            pelvis_world[1] * scale,
            (pelvis_world[2] - src_pelvis_bind[2]) * scale,
        )
        frames.append(Frame(root, rotations))

    result = MotionResult(
        request_id=source.request_id,
        backend=source.backend,
        fps=source.fps,
        skeleton=target,
        frames=frames,
        left_contacts=list(source.left_contacts),
        right_contacts=list(source.right_contacts),
        diagnostics=dict(source.diagnostics),
    )
    return result, notes


def _yaw_only(q: Quat) -> Quat:
    """The heading (rotation about Z) part of a rotation."""
    forward = quat_rotate(q, (1.0, 0.0, 0.0))
    return quat_from_axis_angle(Z_AXIS, math.atan2(forward[1], forward[0]))
