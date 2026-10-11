"""Foot locking: pin planted feet and re-solve the legs with two-bone IK.

Runs on any backend's output, using the result's per-frame foot contact flags.
While a foot is in contact, its ankle is held where the contact began. After
lift-off it eases back to the original animation over a few frames. The hip
stays put, the knee keeps its original bend plane, and the foot keeps its
original world orientation.
"""

from __future__ import annotations

import dataclasses
import math

from ..mathutil import Quat, Vec3, normalize, quat_conjugate, quat_from_to, quat_mul, quat_rotate
from ..metrics import foot_slide, world_transforms
from ..result import Frame, MotionResult

DEFAULT_BLEND_FRAMES = 4


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return _add(a, _scale(_sub(b, a), t))


def _contact_targets(feet: list[Vec3], contacts: list[bool], blend_frames: int) -> list[Vec3 | None]:
    """Per-frame ankle target: locked during contact, easing back to the animation after lift-off."""
    n = len(feet)
    targets: list[Vec3 | None] = [None] * n
    f = 0
    while f < n:
        if not contacts[f]:
            f += 1
            continue
        start = f
        while f < n and contacts[f]:
            f += 1
        lock = feet[start]
        for i in range(start, f):
            targets[i] = lock
        for k in range(1, blend_frames + 1):
            i = f - 1 + k
            if i >= n or contacts[i]:
                break
            weight = 1.0 - k / (blend_frames + 1)
            targets[i] = _lerp(feet[i], lock, weight)
    return targets


def _solve_leg(
    hip: Vec3, knee: Vec3, ankle: Vec3, target: Vec3, thigh_world: Quat, calf_world: Quat
) -> tuple[Quat, Quat, bool]:
    """Two-bone IK. Returns new world rotations for thigh and calf, and whether the target was reachable.

    An out-of-reach target leaves the leg fully extended toward it, so the foot drags rather than
    the leg stretching.
    """
    upper = math.dist(hip, knee)
    lower = math.dist(knee, ankle)
    to_target = _sub(target, hip)
    reach = math.sqrt(_dot(to_target, to_target))
    if reach < 1e-6 or upper < 1e-6 or lower < 1e-6:
        return thigh_world, calf_world, True
    u = _scale(to_target, 1.0 / reach)
    # Ignore sub-centimeter shortfalls: a nearly straight leg is normal at the end of stance.
    reachable = reach <= upper + lower + 1.0
    reach = max(abs(upper - lower) + 1e-3, min(upper + lower - 1e-3, reach))

    # Keep the knee in the plane it was already bending in.
    hip_to_knee = _sub(knee, hip)
    pole = _sub(hip_to_knee, _scale(u, _dot(hip_to_knee, u)))
    if _dot(pole, pole) < 1e-8:
        return thigh_world, calf_world, reachable
    v = normalize(pole)

    cos_a = max(-1.0, min(1.0, (upper * upper + reach * reach - lower * lower) / (2.0 * upper * reach)))
    sin_a = math.sqrt(1.0 - cos_a * cos_a)
    new_knee = _add(hip, _add(_scale(u, upper * cos_a), _scale(v, upper * sin_a)))
    new_ankle = _add(hip, _scale(u, reach))

    q_thigh = quat_from_to(hip_to_knee, _sub(new_knee, hip))
    moved_shin = quat_rotate(q_thigh, _sub(ankle, knee))
    q_calf = quat_from_to(moved_shin, _sub(new_ankle, new_knee))
    return quat_mul(q_thigh, thigh_world), quat_mul(q_calf, quat_mul(q_thigh, calf_world)), reachable


def lock_feet(result: MotionResult, blend_frames: int = DEFAULT_BLEND_FRAMES) -> tuple[MotionResult, list[str]]:
    """Return a copy of the result with planted feet pinned, plus notes on what was done."""
    skeleton = result.skeleton
    names = {j.name: i for i, j in enumerate(skeleton.joints)}
    notes: list[str] = []
    legs = []
    for side, contacts in (("l", result.left_contacts), ("r", result.right_contacts)):
        chain = (f"thigh_{side}", f"calf_{side}", f"foot_{side}")
        if not all(b in names for b in chain):
            notes.append(f"foot lock skipped for side {side!r}: no {'/'.join(chain)} chain")
            continue
        thigh, calf, foot = (names[b] for b in chain)
        if skeleton.joints[calf].parent != thigh or skeleton.joints[foot].parent != calf:
            notes.append(f"foot lock skipped for side {side!r}: {'/'.join(chain)} is not a parent chain")
            continue
        legs.append((thigh, calf, foot, contacts))
    if not legs:
        return result, notes

    transforms = [world_transforms(result, f) for f in range(len(result.frames))]
    frames = [Frame(fr.root_translation, list(fr.rotations)) for fr in result.frames]
    unreachable = 0
    for thigh, calf, foot, contacts in legs:
        feet = [positions[foot] for positions, _ in transforms]
        targets = _contact_targets(feet, contacts, blend_frames)
        parent = skeleton.joints[thigh].parent
        for f, target in enumerate(targets):
            if target is None:
                continue
            pos, rot = transforms[f]
            thigh_w, calf_w, reachable = _solve_leg(pos[thigh], pos[calf], pos[foot], target, rot[thigh], rot[calf])
            unreachable += not reachable
            parent_w = rot[parent] if parent >= 0 else (0.0, 0.0, 0.0, 1.0)
            rotations = frames[f].rotations
            rotations[thigh] = quat_mul(quat_conjugate(parent_w), thigh_w)
            rotations[calf] = quat_mul(quat_conjugate(thigh_w), calf_w)
            rotations[foot] = quat_mul(quat_conjugate(calf_w), rot[foot])

    locked = dataclasses.replace(result, frames=frames, diagnostics=dict(result.diagnostics))
    left, right = skeleton.joints[legs[0][2]].name, skeleton.joints[legs[-1][2]].name
    before = foot_slide(result, left, right)
    after = foot_slide(locked, left, right)
    locked.diagnostics["foot_slide_before_cm_s"] = before
    locked.diagnostics["foot_slide_after_cm_s"] = after
    locked.diagnostics["foot_lock_unreachable_frames"] = unreachable
    notes.append(f"foot lock: mean slide {before['mean_cm_s']:.1f} -> {after['mean_cm_s']:.1f} cm/s")
    if unreachable:
        notes.append(
            f"foot lock: {unreachable} frame(s) where a planted foot was out of the leg's reach "
            "(it drags there); usually a stride/contact mismatch in the source motion"
        )
    return locked, notes
