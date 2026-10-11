"""Request -> context translation -> backend -> result. Shared by the CLI and the server."""

from __future__ import annotations

from typing import Any

from .backends import get_backend
from .profiles import apply_profile
from .request import MotionRequest, parse_request
from .result import MotionResult


def run(request: MotionRequest) -> MotionResult:
    request, notes = apply_profile(request)
    result = get_backend(request.backend).generate(request)
    result.diagnostics.setdefault("notes", [])
    result.diagnostics["notes"] = notes + result.diagnostics["notes"]
    return result


def run_json(data: dict[str, Any]) -> MotionResult:
    return run(parse_request(data))
