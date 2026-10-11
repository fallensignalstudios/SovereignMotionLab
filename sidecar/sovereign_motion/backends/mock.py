"""Procedural stand-in backend.

It walks a simple humanoid along the requested path with a swinging-leg cycle.
It looks like a test dummy on purpose: its job is to exercise the full
request -> result -> BVH/Unreal pipeline without a GPU or model checkpoint.
"""

from __future__ import annotations

import math
import random

from ..mathutil import IDENTITY, Vec3, lerp_angle_rad, quat_from_axis_angle
from ..request import MotionRequest
from ..result import Frame, Joint, MotionResult, Skeleton
from .base import MotionBackend

Z_AXIS = (0.0, 0.0, 1.0)
Y_AXIS = (0.0, 1.0, 0.0)

# Unreal conventions: X forward, Y right, Z up, centimeters. Left side is -Y.
MOCK_SKELETON = Skeleton(
    name="sovereign_mock_humanoid",
    joints=[
        Joint("root", -1, (0.0, 0.0, 0.0)),
        Joint("pelvis", 0, (0.0, 0.0, 95.0)),
        Joint("spine_01", 1, (0.0, 0.0, 15.0)),
        Joint("spine_02", 2, (0.0, 0.0, 20.0)),
        Joint("neck", 3, (0.0, 0.0, 20.0)),
        Joint("head", 4, (0.0, 0.0, 12.0)),
        Joint("upperarm_l", 3, (0.0, -18.0, 12.0)),
        Joint("lowerarm_l", 6, (0.0, 0.0, -28.0)),
        Joint("hand_l", 7, (0.0, 0.0, -25.0)),
        Joint("upperarm_r", 3, (0.0, 18.0, 12.0)),
        Joint("lowerarm_r", 9, (0.0, 0.0, -28.0)),
        Joint("hand_r", 10, (0.0, 0.0, -25.0)),
        Joint("thigh_l", 1, (0.0, -10.0, -5.0)),
        Joint("calf_l", 12, (0.0, 0.0, -45.0)),
        Joint("foot_l", 13, (0.0, 0.0, -42.0)),
        Joint("thigh_r", 1, (0.0, 10.0, -5.0)),
        Joint("calf_r", 15, (0.0, 0.0, -45.0)),
        Joint("foot_r", 16, (0.0, 0.0, -42.0)),
    ],
)

RAMP_S = 0.4
IDLE_DURATION_S = 1.0
MAX_TURN_RAD_S = math.radians(240.0)


def _segment_lengths(points: list[Vec3]) -> list[float]:
    return [math.dist(points[i][:2], points[i + 1][:2]) for i in range(len(points) - 1)]


def _point_on_path(points: list[Vec3], lengths: list[float], s: float) -> tuple[Vec3, float | None]:
    """Position at arc length s along the path, plus the heading (radians) of that segment."""
    for i, length in enumerate(lengths):
        if length == 0.0:
            continue
        if s <= length or i == len(lengths) - 1:
            t = max(0.0, min(1.0, s / length))
            a, b = points[i], points[i + 1]
            pos = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
            return pos, math.atan2(b[1] - a[1], b[0] - a[0])
        s -= length
    return points[-1], None


def _trapezoid_distance(t: float, total_t: float, ramp: float, speed: float) -> float:
    """Distance covered at time t with linear accel/decel ramps of length `ramp`."""
    if t <= 0.0:
        return 0.0
    if t >= total_t:
        return speed * (total_t - ramp)
    if t < ramp:
        return 0.5 * speed * t * t / ramp
    if t > total_t - ramp:
        remaining = total_t - t
        return speed * (total_t - ramp) - 0.5 * speed * remaining * remaining / ramp
    return speed * (t - 0.5 * ramp)


class MockBackend(MotionBackend):
    name = "mock"

    def generate(self, request: MotionRequest) -> MotionResult:
        diagnostics: dict = {"notes": []}
        points = request.path_points()
        lengths = _segment_lengths(points)
        distance = sum(lengths)
        speed = request.effective_speed_cm_s()

        if request.goal.arrive_by_s is not None and distance > 0:
            needed = distance / max(request.goal.arrive_by_s - RAMP_S, 1e-3)
            cap = request.character.max_speed_cm_s
            if cap is not None and needed > cap:
                diagnostics["notes"].append(
                    f"arrive_by_s needs {needed:.0f} cm/s but max is {cap:.0f}; arriving late"
                )
                needed = cap
            speed = needed

        moving = distance > 1e-3 and speed > 0 and request.locomotion.style != "idle"
        if moving:
            ramp = min(RAMP_S, distance / speed)
            total_t = distance / speed + ramp
        else:
            ramp = 0.0
            total_t = request.goal.arrive_by_s or IDLE_DURATION_S
            if distance > 1e-3:
                diagnostics["notes"].append("idle style or zero speed: holding at start")

        num_frames = max(2, math.ceil(total_t * request.fps) + 1)
        # Stride length grows with speed; ~140 cm per full cycle at a 150 cm/s walk.
        cycle_cm = 140.0 * math.sqrt(max(speed, 1.0) / 150.0)
        phase_offset = random.Random(request.seed).uniform(0.0, 2.0 * math.pi)
        start_yaw = math.radians(request.start.root_yaw_deg)
        goal_yaw = math.radians(request.goal.root_yaw_deg)

        frames: list[Frame] = []
        left_contacts: list[bool] = []
        right_contacts: list[bool] = []
        heading = start_yaw
        for f in range(num_frames):
            t = min(f / request.fps, total_t)
            s = _trapezoid_distance(t, total_t, ramp, speed) if moving else 0.0
            pos, seg_heading = _point_on_path(points, lengths, s) if moving else (points[0], None)
            if seg_heading is not None:
                # Turn toward the segment direction at a limited rate instead of snapping at corners.
                delta = (seg_heading - heading + math.pi) % (2.0 * math.pi) - math.pi
                max_step = MAX_TURN_RAD_S / request.fps
                heading += max(-max_step, min(max_step, delta))

            # Turn from the start yaw into the path, and from the path into the goal yaw.
            yaw = heading
            if moving and t < ramp:
                yaw = lerp_angle_rad(start_yaw, heading, t / ramp)
            elif moving and t > total_t - ramp:
                yaw = lerp_angle_rad(heading, goal_yaw, (t - (total_t - ramp)) / ramp)
            elif not moving:
                yaw = lerp_angle_rad(start_yaw, goal_yaw, t / total_t)

            # Gait amplitude fades in and out with the speed ramps.
            current_speed = speed
            if moving and ramp > 0:
                current_speed = speed * min(1.0, t / ramp, (total_t - t) / ramp)
            amp = (current_speed / 150.0) if moving else 0.0
            amp = min(amp, 2.0)
            phase = 2.0 * math.pi * s / cycle_cm + phase_offset

            frames.append(Frame(pos, self._pose(yaw, phase, amp)))
            if amp < 0.05:
                left_contacts.append(True)
                right_contacts.append(True)
            else:
                left_contacts.append(math.cos(phase) <= 0.0)
                right_contacts.append(math.cos(phase) >= 0.0)

        if request.joint_targets:
            diagnostics["notes"].append(
                f"mock backend ignores {len(request.joint_targets)} joint target(s)"
            )
        if request.text_prompt:
            diagnostics["notes"].append("mock backend ignores text_prompt")
        diagnostics["distance_cm"] = round(distance, 2)
        diagnostics["speed_cm_s"] = round(speed, 2)

        return MotionResult(
            request_id=request.request_id,
            backend=self.name,
            fps=request.fps,
            skeleton=MOCK_SKELETON,
            frames=frames,
            left_contacts=left_contacts,
            right_contacts=right_contacts,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _pose(yaw: float, phase: float, amp: float) -> list:
        rotations = [IDENTITY] * len(MOCK_SKELETON.joints)
        rotations[0] = quat_from_axis_angle(Z_AXIS, yaw)

        # Rotating about +Y by a negative angle swings a downward limb forward (+X).
        thigh_swing = 0.45 * amp * math.sin(phase)
        knee_l = 0.9 * amp * max(0.0, math.cos(phase))
        knee_r = 0.9 * amp * max(0.0, -math.cos(phase))
        arm_swing = 0.3 * amp * math.sin(phase)

        idx = MOCK_SKELETON.index
        rotations[idx("thigh_l")] = quat_from_axis_angle(Y_AXIS, -thigh_swing)
        rotations[idx("thigh_r")] = quat_from_axis_angle(Y_AXIS, thigh_swing)
        rotations[idx("calf_l")] = quat_from_axis_angle(Y_AXIS, knee_l)
        rotations[idx("calf_r")] = quat_from_axis_angle(Y_AXIS, knee_r)
        # Arms counter-swing against the leg on the same side.
        rotations[idx("upperarm_l")] = quat_from_axis_angle(Y_AXIS, arm_swing)
        rotations[idx("upperarm_r")] = quat_from_axis_angle(Y_AXIS, -arm_swing)
        rotations[idx("lowerarm_l")] = quat_from_axis_angle(Y_AXIS, -0.2 * amp)
        rotations[idx("lowerarm_r")] = quat_from_axis_angle(Y_AXIS, -0.2 * amp)
        return rotations
