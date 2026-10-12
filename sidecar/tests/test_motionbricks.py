"""MotionBricks backend tests that run without a GPU or checkpoints.

The neural agent is replaced by a fake with the same interface (reset / get_next_frame /
get_context_mujoco_qpos / generate_new_frames) that walks where it is steered. Everything
around it is real: the path controller, MuJoCo forward kinematics on the actual G1 model,
retargeting onto Mannequin skeletons, placement at the start, and foot locking.

Skipped when mujoco isn't installed (pip install mujoco numpy).
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

from sovereign_motion.backends.motionbricks import (
    MOTIONBRICKS_MODES,
    MotionBricksBackend,
    PathController,
    build_constraints,
)
from sovereign_motion.metrics import world_positions
from sovereign_motion.request import parse_request

HAS_MUJOCO = importlib.util.find_spec("mujoco") is not None and importlib.util.find_spec("numpy") is not None
HERE = Path(__file__).resolve().parent
G1_XML = HERE.parent / "third_party" / "unitree_g1" / "g1_29dof.xml"
EXAMPLE = HERE.parents[1] / "examples" / "requests" / "dominion_soldier_walk.json"


class FakeTensor(list):
    def view(self, *_):
        return self


FAKE_TORCH = SimpleNamespace(
    tensor=lambda value: FakeTensor(value),
    manual_seed=lambda seed: None,
    no_grad=contextlib.nullcontext,
)


class FakeAgent:
    """Walks at the requested velocity toward the movement direction and turns toward the facing direction."""

    def __init__(self, np):
        self.np = np
        self.reset()

    def reset(self):
        self.pos = [0.0, 0.0]
        self.heading = 0.0
        self.velocity = [0.0, 0.0]
        self.target_heading = 0.0
        self.knee_phase = 0.0

    def get_next_frame(self):
        self.pos = [self.pos[0] + self.velocity[0] / 30, self.pos[1] + self.velocity[1] / 30]
        delta = (self.target_heading - self.heading + math.pi) % (2 * math.pi) - math.pi
        self.heading += max(-0.2, min(0.2, delta))
        speed = math.hypot(*self.velocity)
        self.knee_phase += speed * 0.3
        qpos = self.np.zeros(36)
        qpos[0], qpos[1], qpos[2] = self.pos[0], self.pos[1], 0.793
        qpos[3:7] = [math.cos(self.heading / 2), 0.0, 0.0, math.sin(self.heading / 2)]
        qpos[7 + 3] = 0.5 * max(0.0, math.sin(self.knee_phase)) * min(speed, 1.0)  # left knee bends while walking
        return qpos

    def get_context_mujoco_qpos(self):
        return None

    def generate_new_frames(self, control, dt):
        move = control["movement_direction"][0]
        speed = control.get("target_vel", [0.0])[0]
        self.velocity = [move[0] * speed, move[1] * speed]
        face = control["facing_direction"][0]
        self.target_heading = math.atan2(face[1], face[0])


def fake_backend():
    import numpy as np

    from sovereign_motion.g1 import G1Converter

    backend = MotionBricksBackend()
    backend._agent = FakeAgent(np)
    backend._agent._inferencer = SimpleNamespace(_args={"min_tokens": 6, "max_tokens": 16})
    backend._clips = SimpleNamespace(CLIPS={mode: {"allowed_pred_num_tokens": [1]} for mode in MOTIONBRICKS_MODES})
    backend._torch = FAKE_TORCH
    backend._converter = G1Converter(G1_XML)
    return backend


def request(fixture: str | None = "ue5_manny", **overrides) -> dict:
    with open(EXAMPLE, encoding="utf-8") as f:
        data = json.load(f)
    data.update(backend="motionbricks", start={"root_location": [100, 200, 0], "root_yaw_deg": 90},
                goal={"root_location": [100, 600, 0], "root_yaw_deg": 180}, trajectory={"waypoints": []},
                joint_targets=[], text_prompt=None)
    data.pop("text_prompt")
    data["character"].pop("profile", None)
    if fixture:
        with open(HERE / "fixtures" / f"{fixture}_skeleton.json", encoding="utf-8") as f:
            data["character"]["skeleton"] = json.load(f)
    data.update(overrides)
    return data


class ConstraintTests(unittest.TestCase):
    def test_path_is_expressed_in_the_agents_frame(self):
        # Start faces +Y in Unreal; the goal is 4 m ahead, which is +X (forward) for the agent.
        c = build_constraints(parse_request(request(None)))
        self.assertAlmostEqual(c["path_m"][0][0], 0.0)
        self.assertAlmostEqual(c["path_m"][-1][0], 4.0, places=6)
        self.assertAlmostEqual(c["path_m"][-1][1], 0.0, places=6)
        # Goal yaw is +90 degrees from the start in Unreal; mirrored (negated) for MuJoCo.
        self.assertAlmostEqual(c["goal_yaw_rad"], -math.pi / 2)

    def test_style_mapping_and_override(self):
        self.assertEqual(build_constraints(parse_request(request(None)))["mode"], "walk")
        data = request(None, locomotion={"style": "walk", "backend_style": "walk_gun"})
        self.assertEqual(build_constraints(parse_request(data))["mode"], "walk_gun")
        data = request(None, locomotion={"style": "crouch_walk"})
        self.assertEqual(build_constraints(parse_request(data))["mode"], "walk_stealth")

    def test_controller_follows_then_idles_facing_goal(self):
        controller = PathController([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)], goal_yaw=1.0, mode="walk")
        first = controller.signals((0.0, 0.0))
        self.assertEqual(first["mode"], "walk")
        self.assertAlmostEqual(first["movement"][0], 1.0, places=6)
        corner = controller.signals((1.9, 0.0))
        self.assertGreater(corner["movement"][1], 0.0)  # starts turning toward the second leg
        done = controller.signals((2.0, 1.9))
        self.assertEqual(done["mode"], "idle")
        self.assertAlmostEqual(math.atan2(done["facing"][1], done["facing"][0]), 1.0, places=6)


@unittest.skipUnless(HAS_MUJOCO, "mujoco/numpy not installed")
class FakeAgentEndToEndTests(unittest.TestCase):
    def test_walks_to_goal_on_manny(self):
        result = fake_backend().generate(parse_request(request()))
        self.assertEqual(result.skeleton.name, "SK_Mannequin")
        idx = result.skeleton.index
        start = world_positions(result, 0)[idx("root")]
        end = world_positions(result, len(result.frames) - 1)[idx("root")]
        self.assertLess(math.dist(start[:2], (100, 200)), 5.0)
        self.assertLess(math.dist(end[:2], (100, 600)), 40.0)
        self.assertTrue(any("retargeted unitree_g1_29dof -> SK_Mannequin" in n for n in result.diagnostics["notes"]))

    def test_faces_start_and_goal_yaw(self):
        result = fake_backend().generate(parse_request(request()))
        idx = result.skeleton.index

        def facing(frame):
            p = world_positions(result, frame)
            dx = sum(p[idx(f"ball_{s}")][0] - p[idx(f"foot_{s}")][0] for s in "lr")
            dy = sum(p[idx(f"ball_{s}")][1] - p[idx(f"foot_{s}")][1] for s in "lr")
            return math.degrees(math.atan2(dy, dx))

        self.assertAlmostEqual(facing(0), 90.0, delta=3.0)
        self.assertAlmostEqual(abs(facing(len(result.frames) - 1)), 180.0, delta=5.0)

    def test_without_target_skeleton_stays_on_g1(self):
        result = fake_backend().generate(parse_request(request(None)))
        self.assertEqual(result.skeleton.name, "unitree_g1_29dof")
        self.assertEqual(len(result.frames[0].rotations), 30)

    def test_full_pipeline_with_foot_lock(self):
        import sovereign_motion.pipeline as pipeline

        backend = fake_backend()
        original = pipeline.get_backend
        pipeline.get_backend = lambda name: backend
        try:
            result = pipeline.run_json(request())
        finally:
            pipeline.get_backend = original
        self.assertIn("foot_slide_after_cm_s", result.diagnostics)
        self.assertEqual(result.skeleton.name, "SK_Mannequin")

    def test_unknown_backend_style_is_rejected(self):
        from sovereign_motion.backends.base import BackendUnavailable

        data = request(locomotion={"style": "walk", "backend_style": "moonwalk"})
        with self.assertRaises(BackendUnavailable):
            fake_backend().generate(parse_request(data))


@unittest.skipUnless(HAS_MUJOCO, "mujoco/numpy not installed")
class G1RetargetTests(unittest.TestCase):
    """Poses set directly on the G1 and checked on each Mannequin."""

    @classmethod
    def setUpClass(cls):
        from sovereign_motion.g1 import G1Converter

        cls.converter = G1Converter(G1_XML)

    def _pose(self, fixture: str, **angles):
        from sovereign_motion.request import parse_skeleton
        from sovereign_motion.retarget import retarget

        q = self.converter.model.qpos0.copy()
        for joint, angle in angles.items():
            q[self.converter.model.joint(joint).qposadr[0]] = angle
        source = self.converter.to_result([q], 30, "t", "test")
        with open(HERE / "fixtures" / f"{fixture}_skeleton.json", encoding="utf-8") as f:
            target = parse_skeleton(json.load(f), "target")
        result, _ = retarget(source, target)
        return world_positions(result, 0), result.skeleton.index

    def test_g1_conversion_matches_mujoco(self):
        q = self.converter.model.qpos0.copy()
        q[self.converter.model.joint("left_knee_joint").qposadr[0]] = 1.0
        q[3:7] = [math.cos(0.4), 0.0, 0.0, math.sin(0.4)]
        result = self.converter.to_result([q], 30, "t", "test")
        ours = world_positions(result, 0)
        mujoco_positions, _ = self.converter._fk(q)
        for a, b in zip(ours, mujoco_positions):
            self.assertLess(math.dist(a, b), 1e-6)

    def test_bind_pose_faces_forward_with_left_on_left(self):
        for fixture in ("ue4_mannequin", "ue5_manny", "ue5_quinn"):
            with self.subTest(fixture=fixture):
                p, idx = self._pose(fixture)
                self.assertLess(p[idx("foot_l")][1], 0.0)
                self.assertGreater(p[idx("ball_l")][0], p[idx("foot_l")][0])  # toes point +X
                self.assertLess(p[idx("ball_l")][2], 10.0)

    def test_knee_and_arm_follow_the_g1(self):
        for fixture in ("ue4_mannequin", "ue5_manny", "ue5_quinn"):
            with self.subTest(fixture=fixture):
                bent, idx = self._pose(fixture, left_hip_pitch_joint=-0.6, left_knee_joint=1.2)
                rest, _ = self._pose(fixture)
                self.assertGreater(bent[idx("calf_l")][0], rest[idx("calf_l")][0] + 10.0)  # knee forward
                self.assertGreater(bent[idx("foot_l")][2], rest[idx("foot_l")][2] + 5.0)   # foot lifted
                raised, _ = self._pose(fixture, left_shoulder_pitch_joint=-1.2)
                self.assertGreater(raised[idx("hand_l")][2], rest[idx("hand_l")][2] + 25.0)


if __name__ == "__main__":
    unittest.main()
