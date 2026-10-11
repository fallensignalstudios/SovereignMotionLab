"""Backend registry. Add a new model by subclassing MotionBackend and registering it here."""

from __future__ import annotations

from .base import BackendUnavailable, MotionBackend
from .mock import MockBackend
from .motionbricks import MotionBricksBackend

_FACTORIES = {
    MockBackend.name: MockBackend,
    MotionBricksBackend.name: MotionBricksBackend,
}
_instances: dict[str, MotionBackend] = {}


def backend_names() -> list[str]:
    return sorted(_FACTORIES)


def get_backend(name: str) -> MotionBackend:
    """Return a shared instance so expensive model loads happen once per process."""
    if name not in _FACTORIES:
        raise KeyError(f"unknown backend {name!r}; available: {', '.join(backend_names())}")
    if name not in _instances:
        _instances[name] = _FACTORIES[name]()
    return _instances[name]


__all__ = ["BackendUnavailable", "MotionBackend", "backend_names", "get_backend"]
