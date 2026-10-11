"""MotionBricks adapter (stub).

This is where the context layer meets NVIDIA's model. Splitting the work in two:

1. build_constraints(): translate a MotionRequest into the constraint types the
   MotionBricks paper describes (2D waypoints / dense 2D path, sparse joint
   positions and rotations, full-body keyframes, text). Pure data, testable
   without the model, and the part that is ours.
2. generate(): feed those constraints to the real inference code and convert
   its output back to a MotionResult. Not written yet, because it depends on
   the actual API in the MotionBricks repo, which must be read first (spike 0.0,
   see docs/spike-0.0.md). Nothing here guesses at that API.

Configuration (environment variables):
    SOVEREIGN_MOTIONBRICKS_REPO        path to a local clone of the MotionBricks repo
    SOVEREIGN_MOTIONBRICKS_CHECKPOINT  path to the downloaded model weights
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from ..request import MotionRequest
from ..result import MotionResult
from .base import BackendUnavailable, MotionBackend

# Unreal works in centimeters, Z-up. Fill these in once the model's conventions
# are confirmed from the repo (likely meters; up axis to be checked).
UNREAL_TO_MODEL_SCALE = 0.01


def build_constraints(request: MotionRequest) -> dict:
    """Translate a request into backend-neutral constraint descriptions.

    Positions stay in Unreal's axes but are scaled to model units. Axis
    conversion happens in generate() once the model's frame is known.
    """
    s = UNREAL_TO_MODEL_SCALE
    path = [(p[0] * s, p[1] * s) for p in request.path_points()]
    constraints: dict = {
        "fps": request.fps,
        "seed": request.seed,
        "root_waypoints_2d": path,
        "start_yaw_deg": request.start.root_yaw_deg,
        "goal_yaw_deg": request.goal.root_yaw_deg,
        "target_speed": request.effective_speed_cm_s() * s,
        "duration_s": request.goal.arrive_by_s,
        "style": request.locomotion.style,
        "joint_targets": [
            {
                "joint": t.joint,
                "time_s": t.time_s,
                "location": tuple(c * s for c in t.location) if t.location else None,
                "rotation": t.rotation,
                # Hard targets are also re-enforced with IK in Unreal afterwards.
                "hard": t.hard,
            }
            for t in request.joint_targets
        ],
        "text": request.text_prompt,
    }
    return constraints


class MotionBricksBackend(MotionBackend):
    name = "motionbricks"

    def __init__(self) -> None:
        self.repo = os.environ.get("SOVEREIGN_MOTIONBRICKS_REPO")
        self.checkpoint = os.environ.get("SOVEREIGN_MOTIONBRICKS_CHECKPOINT")

    def available(self) -> tuple[bool, str]:
        if not self.repo or not Path(self.repo).is_dir():
            return False, "set SOVEREIGN_MOTIONBRICKS_REPO to a local clone of the MotionBricks repo"
        if not self.checkpoint or not Path(self.checkpoint).exists():
            return False, "set SOVEREIGN_MOTIONBRICKS_CHECKPOINT to the downloaded model weights"
        if importlib.util.find_spec("torch") is None:
            return False, "torch is not installed (pip install -e 'sidecar[motionbricks]')"
        return True, "configured (inference not wired up yet)"

    def generate(self, request: MotionRequest) -> MotionResult:
        ok, reason = self.available()
        if not ok:
            raise BackendUnavailable(reason)
        constraints = build_constraints(request)  # noqa: F841 - consumed once inference is wired up
        # TODO(spike 0.0): after reading the MotionBricks inference scripts:
        #   1. load the model from self.checkpoint (cache it on self; loading is slow)
        #   2. map `constraints` onto its root-module / pose-module inputs, converting axes
        #   3. run inference and read back joint rotations, root motion and foot contacts
        #   4. convert to Unreal axes/centimeters and return a MotionResult on the
        #      model's own skeleton (retargeting happens in Unreal)
        raise BackendUnavailable("MotionBricks inference is not wired up yet; see docs/spike-0.0.md")
