"""Backend interface. Every motion model plugs in behind this."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..request import MotionRequest
from ..result import MotionResult


class BackendUnavailable(RuntimeError):
    """The backend exists but can't run here (missing checkpoint, missing dependencies)."""


class MotionBackend(ABC):
    name: str = ""

    def available(self) -> tuple[bool, str]:
        """Whether this backend can run right now, and why not if it can't."""
        return True, "ok"

    @abstractmethod
    def generate(self, request: MotionRequest) -> MotionResult:
        """Generate a clip. Must be deterministic for a given request (including seed)."""
