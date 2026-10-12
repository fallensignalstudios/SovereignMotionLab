"""MotionBricks backend (NVIDIA, public preview in GR00T-WholeBodyControl/motionbricks).

How it runs:
1. Load the pose/root models and the style clips exactly as NVIDIA's interactive demo
   does, then drive their `full_navigation_agent` headless.
2. Instead of a keyboard, a path-following controller steers the agent: each frame it
   picks a movement and facing direction toward a look-ahead point on our path, and at
   the goal it switches to idle facing the goal yaw.
3. The agent's MuJoCo qpos frames (Unitree G1, 29 DOF) are converted to our result
   format with MuJoCo forward kinematics (g1.py), then retargeted onto the request's
   target skeleton when it has Unreal Mannequin bone names (retarget.py).

The agent works in its own frame (starting at the origin facing +X); we generate there
and then place the clip at the request's start location and yaw.

Configuration (environment variables):
    SOVEREIGN_MOTIONBRICKS_REPO    path to the `motionbricks` folder of a GR00T-WholeBodyControl
                                   clone, with checkpoints pulled (see docs/motionbricks-setup.md)
    SOVEREIGN_MOTIONBRICKS_DEVICE  torch device, default "cuda"
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from pathlib import Path

from ..mathutil import quat_from_axis_angle, quat_mul, quat_rotate
from ..request import MotionRequest
from ..result import Frame, MotionResult
from ..retarget import retarget
from .base import BackendUnavailable, MotionBackend

FPS = 30
# Replanning cadence, matching NVIDIA's demo: a new plan every 8 frames, scaled by generate_dt=2.
CONTROLLER_DT = 8 / FPS
GENERATE_DT = 2.0
LOOKAHEAD_M = 0.8
ARRIVE_RADIUS_M = 0.25
SETTLE_FRAMES = 45
MAX_SECONDS = 60.0

# Our locomotion styles -> MotionBricks clip modes. locomotion.backend_style overrides with any mode.
STYLE_TO_MODE = {
    "idle": "idle",
    "walk": "walk",
    "jog": "walk",
    "run": "walk",
    "crouch_walk": "walk_stealth",
}
MOTIONBRICKS_MODES = (
    "idle", "slow_walk", "walk", "hand_crawling", "walk_boxing", "elbow_crawling", "stealth_walk",
    "injured_walk", "walk_stealth", "walk_happy_dance", "walk_zombie", "walk_gun", "walk_scared",
    "walk_left", "walk_right",
)


def build_constraints(request: MotionRequest) -> dict:
    """The request as the agent sees it: a 2D path in its own frame (meters, MuJoCo axes)."""
    sx, sy, _ = request.start.root_location
    yaw0 = math.radians(request.start.root_yaw_deg)
    cos0, sin0 = math.cos(-yaw0), math.sin(-yaw0)
    path = []
    for x, y, _ in request.path_points():
        # Into the start frame (Unreal axes), then to MuJoCo (negate Y) in meters.
        dx, dy = x - sx, y - sy
        lx, ly = dx * cos0 - dy * sin0, dx * sin0 + dy * cos0
        path.append((lx / 100.0, -ly / 100.0))
    goal_yaw = -(math.radians(request.goal.root_yaw_deg) - yaw0)  # relative, mirrored for MuJoCo
    return {
        "path_m": path,
        "goal_yaw_rad": goal_yaw,
        "mode": _mode_for(request),
        "speed_m_s": request.effective_speed_cm_s() / 100.0,
        "seed": request.seed,
    }


def _mode_for(request: MotionRequest) -> str:
    backend_style = request.locomotion.backend_style
    if backend_style:
        if backend_style not in MOTIONBRICKS_MODES:
            raise BackendUnavailable(
                f"unknown MotionBricks style {backend_style!r}; choose from {', '.join(MOTIONBRICKS_MODES)}"
            )
        return backend_style
    return STYLE_TO_MODE[request.locomotion.style]


class PathController:
    """Steers the agent along a polyline, then turns to the goal yaw and idles."""

    def __init__(self, path: list[tuple[float, float]], goal_yaw: float, mode: str):
        self.path = path
        self.goal_yaw = goal_yaw
        self.mode = mode
        self.lengths = [math.dist(path[i], path[i + 1]) for i in range(len(path) - 1)]
        self.total = sum(self.lengths)
        self.progress = 0.0
        self.arrived = self.total < ARRIVE_RADIUS_M or mode == "idle"

    def _point_at(self, s: float) -> tuple[float, float]:
        for i, length in enumerate(self.lengths):
            if length > 0 and (s <= length or i == len(self.lengths) - 1):
                t = max(0.0, min(1.0, s / length))
                a, b = self.path[i], self.path[i + 1]
                return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            s -= length
        return self.path[-1]

    def _project(self, pos: tuple[float, float]) -> float:
        """Arc length of the closest path point, never moving backwards."""
        best, best_d, start = self.progress, float("inf"), 0.0
        for i, length in enumerate(self.lengths):
            a, b = self.path[i], self.path[i + 1]
            if length > 0:
                t = ((pos[0] - a[0]) * (b[0] - a[0]) + (pos[1] - a[1]) * (b[1] - a[1])) / (length * length)
                t = max(0.0, min(1.0, t))
                p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                d = math.dist(p, pos)
                if d < best_d and start + t * length >= self.progress - 0.05:
                    best, best_d = start + t * length, d
            start += length
        return max(self.progress, best)

    def signals(self, pos: tuple[float, float]) -> dict:
        """(mode, movement_direction, facing_direction) in MuJoCo axes."""
        if not self.arrived:
            self.progress = self._project(pos)
            if math.dist(pos, self.path[-1]) < ARRIVE_RADIUS_M or self.progress >= self.total - 1e-3:
                self.arrived = True
        if self.arrived:
            facing = (math.cos(self.goal_yaw), math.sin(self.goal_yaw))
            return {"mode": "idle", "movement": (0.0, 0.0), "facing": facing}
        target = self._point_at(min(self.total, self.progress + LOOKAHEAD_M))
        dx, dy = target[0] - pos[0], target[1] - pos[1]
        norm = math.hypot(dx, dy) or 1.0
        direction = (dx / norm, dy / norm)
        return {"mode": self.mode, "movement": direction, "facing": direction}


class MotionBricksBackend(MotionBackend):
    name = "motionbricks"
    supports_target_skeleton = True  # via retargeting, for Mannequin-named skeletons

    def __init__(self) -> None:
        self.repo = os.environ.get("SOVEREIGN_MOTIONBRICKS_REPO")
        self.device = os.environ.get("SOVEREIGN_MOTIONBRICKS_DEVICE", "cuda")
        self._agent = None
        self._clips = None
        self._converter = None

    # -- setup ---------------------------------------------------------------------------------

    def _paths(self) -> dict:
        root = Path(self.repo or "")
        return {
            "result_dir": root / "out",
            "clips_ckpt": root / "out" / "G1-clip.ckpt",
            "skeleton_xml": root / "assets" / "skeletons" / "g1" / "g1.xml",
            "kinematics_xml": root / "assets" / "skeletons" / "g1" / "g1_29dof.xml",
        }

    def available(self) -> tuple[bool, str]:
        if not self.repo or not Path(self.repo).is_dir():
            return False, "set SOVEREIGN_MOTIONBRICKS_REPO to the motionbricks folder of a GR00T-WholeBodyControl clone"
        paths = self._paths()
        ckpt = paths["clips_ckpt"]
        if not ckpt.is_file():
            return False, f"checkpoints missing ({ckpt}); run the git lfs pull in docs/motionbricks-setup.md"
        if ckpt.stat().st_size < 10_000:
            return False, f"{ckpt} is a Git LFS pointer, not the checkpoint; run the git lfs pull"
        for module in ("torch", "mujoco", "numpy"):
            if importlib.util.find_spec(module) is None:
                return False, f"{module} is not installed in this Python environment"
        return True, f"ready (device {self.device})"

    def _load(self):
        if self._agent is not None:
            return
        ok, reason = self.available()
        if not ok:
            raise BackendUnavailable(reason)
        if self.repo not in sys.path:
            sys.path.insert(0, self.repo)  # works with or without `pip install -e` of motionbricks

        from types import SimpleNamespace

        # torch >= 2.6 defaults torch.load to weights_only, which rejects these Lightning checkpoints
        # (they carry config objects). They are NVIDIA's published files, so allow full loading here.
        os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
        import torch
        from motionbricks.exp_setup.experiment import test
        from motionbricks.motion_backbone.demo.clips import clip_holder_G1
        from motionbricks.motion_backbone.demo.full_agent import full_navigation_agent
        from motionbricks.motion_backbone.inference.motion_inference import motion_inference

        from ..g1 import G1Converter

        paths = self._paths()
        # Mirrors navigation_demo._initialize_inference_modles, minus the keyboard controller.
        args = SimpleNamespace(
            result_dir=str(paths["result_dir"]), data_root=str(Path(self.repo) / "datasets"),
            explicit_dataset_folder=None, EXP="default",
            return_model_configs=True, return_dataloader=False,
        )
        models, confs = test(args)
        for model_name in ("pose", "root"):
            state = torch.load(confs[model_name].ckpt_path, map_location=self.device, weights_only=False)["state_dict"]
            models[model_name].load_state_dict(state)
        inferencer = motion_inference(models, models["pose"].args, device=self.device)
        self._agent = full_navigation_agent(
            inferencer, None, device=self.device, speed_scale=[1.0, 1.0],
            target_root_realignment=True, source_root_realignment=True, force_canonicalization=True,
            skeleton_xml=str(paths["skeleton_xml"]), skip_ending_target_cond=False, filter_qpos=True,
            clips="G1", ckpt_path=str(paths["clips_ckpt"]), reprocess_clips=False, val_dataloader=None,
        ).to(self.device)
        self._clips = clip_holder_G1
        self._torch = torch
        self._converter = G1Converter(paths["kinematics_xml"])

    # -- generation ----------------------------------------------------------------------------

    def generate(self, request: MotionRequest) -> MotionResult:
        self._load()
        torch = self._torch
        constraints = build_constraints(request)
        notes: list[str] = []
        for unsupported, label in ((request.joint_targets, "joint targets"), (request.text_prompt, "text_prompt"),
                                   (request.goal.arrive_by_s, "arrive_by_s")):
            if unsupported:
                notes.append(f"motionbricks backend ignores {label} for now")
        if request.locomotion.style in ("jog", "run") and not request.locomotion.backend_style:
            notes.append(f"no {request.locomotion.style} clip in the public preview; using walk at the requested speed")

        # The agent is G1-sized (~1.3x smaller than a Mannequin), so ask it for proportionally slower motion.
        g1_scale = 1.0
        if request.character.skeleton is not None:
            g1_scale = _leg_scale(self._converter.skeleton, request.character.skeleton)
        # Retargeting scales root motion up by g1_scale, so the agent walks a proportionally shorter path.
        agent_path = [(x / g1_scale, y / g1_scale) for x, y in constraints["path_m"]]
        controller = PathController(agent_path, constraints["goal_yaw_rad"], constraints["mode"])
        mode_names = list(self._clips.CLIPS.keys())

        torch.manual_seed(request.seed)
        agent = self._agent
        agent.reset()
        qpos_frames = []
        settle = 0
        max_frames = int(MAX_SECONDS * FPS)
        for _ in range(max_frames):
            qpos = agent.get_next_frame()
            qpos_frames.append(qpos.copy())
            sig = controller.signals((float(qpos[0]), float(qpos[1])))
            mode = sig["mode"]
            control = {
                "movement_direction": torch.tensor([[sig["movement"][0], sig["movement"][1], 0.0]]),
                "facing_direction": torch.tensor([[sig["facing"][0], sig["facing"][1], 0.0]]),
                "mode": torch.tensor([[mode_names.index(mode)]]),
                "allowed_pred_num_tokens": self._allowed_tokens(mode),
                "random_seed": torch.tensor([request.seed % 10000]),
                "context_mujoco_qpos": agent.get_context_mujoco_qpos(),
            }
            if mode != "idle":
                control["target_vel"] = torch.tensor([constraints["speed_m_s"] / g1_scale])
            with torch.no_grad():
                agent.generate_new_frames(control, CONTROLLER_DT * GENERATE_DT)
            if controller.arrived:
                settle += 1
                if settle >= SETTLE_FRAMES:
                    break
        else:
            notes.append(f"stopped after {MAX_SECONDS:.0f}s without reaching the goal")

        # Convert and retarget in the agent's frame, then place at the start (placing first would
        # scale the start position along with the root motion).
        result = self._converter.to_result(qpos_frames, FPS, request.request_id, self.name)
        target = request.character.skeleton
        if target is not None:
            names = {j.name for j in target.joints}
            if {"pelvis", "thigh_l", "calf_l", "foot_l"} <= names:
                result, retarget_notes = retarget(result, target)
                notes.extend(retarget_notes)
            else:
                notes.append(f"{target.name} doesn't use Mannequin bone names; output stays on the G1 skeleton")
        result = _place_at_start(result, request)
        result.diagnostics["notes"] = notes
        result.diagnostics["frames_generated"] = len(qpos_frames)
        return result

    def _allowed_tokens(self, mode: str):
        torch = self._torch
        allowed = self._clips.CLIPS[mode].get("allowed_pred_num_tokens")
        if allowed is None:
            args = self._agent._inferencer._args
            allowed = [1] * (args["max_tokens"] - args["min_tokens"] + 1)
        return torch.tensor(allowed).view([1, -1])


def _leg_scale(source_skeleton, target_skeleton) -> float:
    """Target leg length over the G1's (hip height above ankle in the bind pose)."""
    from ..retarget import _component_positions, _leg_length

    try:
        src = _leg_length(source_skeleton, _component_positions(source_skeleton), "left_hip_pitch_link", "left_ankle_roll_link")
        tgt = _leg_length(target_skeleton, _component_positions(target_skeleton), "thigh_l", "foot_l")
    except KeyError:
        return 1.0
    return tgt / src if src > 1e-6 and tgt > 1e-6 else 1.0


def _place_at_start(result: MotionResult, request: MotionRequest) -> MotionResult:
    """Move a clip generated in the agent's frame (origin, facing +X) to the request's start."""
    yaw0 = math.radians(request.start.root_yaw_deg)
    turn = quat_from_axis_angle((0.0, 0.0, 1.0), yaw0)
    sx, sy, sz = request.start.root_location
    frames = []
    for frame in result.frames:
        # Root translation is relative to the pelvis bind offset, which is purely vertical for the G1.
        rx, ry, rz = quat_rotate(turn, frame.root_translation)
        rotations = list(frame.rotations)
        rotations[0] = quat_mul(turn, rotations[0])
        frames.append(Frame((rx + sx, ry + sy, rz + sz), rotations))
    result.frames = frames
    return result
