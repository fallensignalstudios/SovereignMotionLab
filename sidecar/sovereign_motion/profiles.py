"""Character profiles: the first piece of the context-translation layer.

A backend can't know what "Dominion heavy armor" means. A profile translates
those traits into numbers every backend understands (speed limits and scales).
Add profiles as JSON files in sidecar/profiles/; the file stem is the name a
request uses in character.profile.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .request import DEFAULT_STYLE_SPEEDS, MotionRequest, RequestError

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


def load_profile(name: str, profile_dir: Path = PROFILE_DIR) -> dict:
    # Profile names come from editor input; keep lookups inside the profile directory.
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise RequestError(f"invalid profile name {name!r}")
    path = profile_dir / f"{name}.json"
    if not path.is_file():
        raise RequestError(f"unknown character profile {name!r}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def apply_profile(request: MotionRequest, profile_dir: Path = PROFILE_DIR) -> tuple[MotionRequest, list[str]]:
    """Return a copy of the request with profile limits applied, plus notes on what changed.

    Explicit values in the request always win over profile defaults, except that
    a profile's max speed still caps an explicitly requested speed.
    """
    if not request.character.profile:
        return request, []

    profile = load_profile(request.character.profile, profile_dir)
    notes = [f"profile {request.character.profile!r} applied"]
    character = request.character
    locomotion = request.locomotion

    if character.max_speed_cm_s is None and "max_speed_cm_s" in profile:
        character = dataclasses.replace(character, max_speed_cm_s=float(profile["max_speed_cm_s"]))
        notes.append(f"max speed limited to {character.max_speed_cm_s} cm/s")

    if character.height_cm is None and "height_cm" in profile:
        character = dataclasses.replace(character, height_cm=float(profile["height_cm"]))

    scale = profile.get("speed_scale", {}).get(locomotion.style)
    if locomotion.speed_cm_s is None and scale is not None:
        speed = DEFAULT_STYLE_SPEEDS[locomotion.style] * float(scale)
        locomotion = dataclasses.replace(locomotion, speed_cm_s=speed)
        notes.append(f"{locomotion.style} speed scaled x{scale} to {speed:.0f} cm/s")

    return dataclasses.replace(request, character=character, locomotion=locomotion), notes
