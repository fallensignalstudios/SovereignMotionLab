"""Motion request model. Mirrors schema/motion_request.schema.json (version 1.0)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .mathutil import Quat, Vec3
from .result import Joint, Skeleton

LOCOMOTION_STYLES = ("idle", "walk", "jog", "run", "crouch_walk")

# Default speeds per style, cm/s. Profiles and explicit speeds override these.
DEFAULT_STYLE_SPEEDS = {
    "idle": 0.0,
    "walk": 150.0,
    "jog": 300.0,
    "run": 500.0,
    "crouch_walk": 100.0,
}


class RequestError(ValueError):
    """Raised when a request is malformed. The message is safe to return to the editor."""


@dataclass
class Character:
    skeleton_id: str
    profile: str | None = None
    height_cm: float | None = None
    max_speed_cm_s: float | None = None
    # Skeleton to generate on, sent by the editor from the target USkeleton. Backends that
    # can't generate on arbitrary skeletons ignore it and output their own (see the result).
    skeleton: Skeleton | None = None


@dataclass
class RootState:
    root_location: Vec3
    root_yaw_deg: float = 0.0


@dataclass
class Goal(RootState):
    arrive_by_s: float | None = None


@dataclass
class Locomotion:
    style: str = "walk"
    speed_cm_s: float | None = None
    # Backend-specific style name, e.g. a MotionBricks clip mode such as "walk_gun".
    backend_style: str | None = None


@dataclass
class JointTarget:
    joint: str
    time_s: float
    location: Vec3 | None = None
    rotation: Quat | None = None
    hard: bool = False


@dataclass
class MotionRequest:
    request_id: str
    backend: str
    character: Character
    start: RootState
    goal: Goal
    seed: int = 0
    fps: int = 30
    waypoints: list[Vec3] = field(default_factory=list)
    locomotion: Locomotion = field(default_factory=Locomotion)
    joint_targets: list[JointTarget] = field(default_factory=list)
    text_prompt: str | None = None
    gameplay_context: dict[str, Any] = field(default_factory=dict)
    # Pin planted feet with IK after generation (postprocess/footlock.py).
    foot_lock: bool = True

    def path_points(self) -> list[Vec3]:
        """Start, waypoints and goal as one polyline."""
        return [self.start.root_location, *self.waypoints, self.goal.root_location]

    def effective_speed_cm_s(self) -> float:
        """Requested speed, falling back to the style default, clamped by the character's max."""
        speed = self.locomotion.speed_cm_s
        if speed is None:
            speed = DEFAULT_STYLE_SPEEDS[self.locomotion.style]
        if self.character.max_speed_cm_s is not None:
            speed = min(speed, self.character.max_speed_cm_s)
        return speed


def _vec3(value: Any, where: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise RequestError(f"{where} must be [x, y, z]")
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise RequestError(f"{where} must contain numbers") from exc


def _quat(value: Any, where: str) -> Quat:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise RequestError(f"{where} must be [x, y, z, w]")
    try:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError) as exc:
        raise RequestError(f"{where} must contain numbers") from exc


def parse_skeleton(value: Any, where: str) -> Skeleton:
    """Parse {"name", "joints": [{"name", "parent", "offset"}]} with parents before children."""
    if not isinstance(value, dict):
        raise RequestError(f"{where} must be an object")
    raw_joints = value.get("joints")
    if not isinstance(raw_joints, list) or not raw_joints:
        raise RequestError(f"{where}.joints must be a non-empty list")
    joints = []
    for i, j in enumerate(raw_joints):
        jw = f"{where}.joints[{i}]"
        parent = _require(j, "parent", jw)
        if not isinstance(parent, int) or parent >= i or parent < -1 or (parent == -1) != (i == 0):
            raise RequestError(f"{jw}.parent must reference an earlier joint (only joint 0 is the root)")
        joints.append(Joint(str(_require(j, "name", jw)), parent, _vec3(_require(j, "offset", jw), f"{jw}.offset")))
    return Skeleton(name=str(value.get("name", "target")), joints=joints)


def _require(obj: dict[str, Any], key: str, where: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise RequestError(f"{where}.{key} is required")
    return obj[key]


def parse_request(data: dict[str, Any]) -> MotionRequest:
    """Validate a decoded JSON request and build a MotionRequest."""
    if not isinstance(data, dict):
        raise RequestError("request must be a JSON object")
    version = _require(data, "schema_version", "request")
    if version != SCHEMA_VERSION:
        raise RequestError(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION!r}")

    char = _require(data, "character", "request")
    character = Character(
        skeleton_id=str(_require(char, "skeleton_id", "character")),
        profile=char.get("profile"),
        height_cm=char.get("height_cm"),
        max_speed_cm_s=char.get("max_speed_cm_s"),
        skeleton=parse_skeleton(char["skeleton"], "character.skeleton") if "skeleton" in char else None,
    )

    start_raw = _require(data, "start", "request")
    start = RootState(
        root_location=_vec3(_require(start_raw, "root_location", "start"), "start.root_location"),
        root_yaw_deg=float(start_raw.get("root_yaw_deg", 0.0)),
    )
    goal_raw = _require(data, "goal", "request")
    goal = Goal(
        root_location=_vec3(_require(goal_raw, "root_location", "goal"), "goal.root_location"),
        root_yaw_deg=float(goal_raw.get("root_yaw_deg", 0.0)),
        arrive_by_s=goal_raw.get("arrive_by_s"),
    )
    if goal.arrive_by_s is not None and goal.arrive_by_s <= 0:
        raise RequestError("goal.arrive_by_s must be positive")

    loco_raw = data.get("locomotion", {})
    locomotion = Locomotion(
        style=loco_raw.get("style", "walk"),
        speed_cm_s=loco_raw.get("speed_cm_s"),
        backend_style=loco_raw.get("backend_style") or None,
    )
    if locomotion.style not in LOCOMOTION_STYLES:
        raise RequestError(f"locomotion.style must be one of {', '.join(LOCOMOTION_STYLES)}")
    if locomotion.speed_cm_s is not None and locomotion.speed_cm_s <= 0:
        raise RequestError("locomotion.speed_cm_s must be positive")

    waypoints = [
        _vec3(p, f"trajectory.waypoints[{i}]")
        for i, p in enumerate(data.get("trajectory", {}).get("waypoints", []))
    ]

    targets = []
    for i, t in enumerate(data.get("joint_targets", [])):
        where = f"joint_targets[{i}]"
        targets.append(
            JointTarget(
                joint=str(_require(t, "joint", where)),
                time_s=float(_require(t, "time_s", where)),
                location=_vec3(t["location"], f"{where}.location") if "location" in t else None,
                rotation=_quat(t["rotation"], f"{where}.rotation") if "rotation" in t else None,
                hard=bool(t.get("hard", False)),
            )
        )

    fps = int(data.get("fps", 30))
    if not 1 <= fps <= 240:
        raise RequestError("fps must be between 1 and 240")

    return MotionRequest(
        request_id=str(_require(data, "request_id", "request")),
        backend=str(_require(data, "backend", "request")),
        character=character,
        start=start,
        goal=goal,
        seed=int(data.get("seed", 0)),
        fps=fps,
        waypoints=waypoints,
        locomotion=locomotion,
        joint_targets=targets,
        text_prompt=data.get("text_prompt"),
        gameplay_context=data.get("gameplay_context", {}),
        foot_lock=bool(data.get("foot_lock", True)),
    )


def load_request(path: str | Path) -> MotionRequest:
    with open(path, encoding="utf-8") as f:
        return parse_request(json.load(f))
