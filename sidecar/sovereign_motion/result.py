"""Motion result model. Mirrors schema/motion_result.schema.json (version 1.0)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .mathutil import Quat, Vec3


@dataclass
class Joint:
    name: str
    parent: int
    offset: Vec3


@dataclass
class Skeleton:
    name: str
    joints: list[Joint]

    def index(self, name: str) -> int:
        for i, joint in enumerate(self.joints):
            if joint.name == name:
                return i
        raise KeyError(name)

    def children(self, index: int) -> list[int]:
        return [i for i, j in enumerate(self.joints) if j.parent == index]


@dataclass
class Frame:
    root_translation: Vec3
    rotations: list[Quat]


@dataclass
class MotionResult:
    request_id: str
    backend: str
    fps: int
    skeleton: Skeleton
    frames: list[Frame]
    left_contacts: list[bool]
    right_contacts: list[bool]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return (len(self.frames) - 1) / self.fps if self.frames else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": self.request_id,
            "backend": self.backend,
            "fps": self.fps,
            "skeleton": {
                "name": self.skeleton.name,
                "joints": [
                    {"name": j.name, "parent": j.parent, "offset": list(j.offset)}
                    for j in self.skeleton.joints
                ],
            },
            "frames": [
                {
                    "root_translation": [round(v, 4) for v in f.root_translation],
                    "rotations": [[round(c, 6) for c in q] for q in f.rotations],
                }
                for f in self.frames
            ],
            "foot_contacts": {"left": self.left_contacts, "right": self.right_contacts},
            "diagnostics": self.diagnostics,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MotionResult":
        skel = data["skeleton"]
        return cls(
            request_id=data["request_id"],
            backend=data["backend"],
            fps=data["fps"],
            skeleton=Skeleton(
                name=skel["name"],
                joints=[Joint(j["name"], j["parent"], tuple(j["offset"])) for j in skel["joints"]],
            ),
            frames=[
                Frame(tuple(f["root_translation"]), [tuple(q) for q in f["rotations"]])
                for f in data["frames"]
            ],
            left_contacts=list(data["foot_contacts"]["left"]),
            right_contacts=list(data["foot_contacts"]["right"]),
            diagnostics=data.get("diagnostics", {}),
        )

    def save(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f)
