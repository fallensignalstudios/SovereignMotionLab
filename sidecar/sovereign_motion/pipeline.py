"""Request -> context translation -> backend -> result. Shared by the CLI and the server."""

from __future__ import annotations

from typing import Any

from .backends import get_backend
from .profiles import apply_profile
from .request import MotionRequest, parse_request
from .result import MotionResult


def run(request: MotionRequest) -> MotionResult:
    request, notes = apply_profile(request)
    backend = get_backend(request.backend)
    if request.character.skeleton is not None and not backend.supports_target_skeleton:
        notes.append(
            f"{backend.name} can't generate on {request.character.skeleton.name!r}; "
            "output is on the backend's own skeleton and needs retargeting"
        )
    result = backend.generate(request)
    result.diagnostics.setdefault("notes", [])
    result.diagnostics["notes"] = notes + result.diagnostics["notes"]
    return result


def run_json(data: dict[str, Any]) -> MotionResult:
    return run(parse_request(data))
