"""Run with: cd sidecar && python3 -m unittest discover -s tests"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from sovereign_motion.backends import BackendUnavailable, get_backend
from sovereign_motion.backends.motionbricks import build_constraints
from sovereign_motion.export.bvh import quat_to_euler_zxy_deg, to_bvh
from sovereign_motion.mathutil import quat_from_axis_angle, quat_mul
from sovereign_motion.metrics import foot_slide, world_positions, world_transforms
from sovereign_motion.postprocess.footlock import lock_feet
from sovereign_motion.pipeline import run_json
from sovereign_motion.request import RequestError, parse_request
from sovereign_motion.result import Frame, Joint, MotionResult, Skeleton
from sovereign_motion.server import make_server

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "requests" / "dominion_soldier_walk.json"
# Reference poses in the result convention, extracted from FBX files: the UE4 SK_Mannequin (from a
# Motus animation) and UE5 Manny and Quinn (from SKM_Manny_Simple / SKM_Quinn_Simple).
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HUMANOIDS = ("ue4_mannequin", "ue5_manny", "ue5_quinn")


def example() -> dict:
    with open(EXAMPLE, encoding="utf-8") as f:
        return json.load(f)


class RequestTests(unittest.TestCase):
    def test_example_parses(self):
        request = parse_request(example())
        self.assertEqual(request.backend, "mock")
        self.assertEqual(len(request.path_points()), 3)
        self.assertTrue(request.joint_targets[0].hard)

    def test_rejects_bad_input(self):
        cases = [
            ("schema_version", "2.0"),
            ("fps", 0),
            ("locomotion", {"style": "moonwalk"}),
            ("goal", {"root_location": [1, 2]}),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                data = example()
                data[key] = value
                with self.assertRaises(RequestError):
                    parse_request(data)

    def test_profile_path_traversal_rejected(self):
        data = example()
        data["character"]["profile"] = "../pyproject"
        with self.assertRaises(RequestError):
            run_json(data)

    def test_profile_caps_explicit_speed(self):
        data = example()
        data["locomotion"] = {"style": "run", "speed_cm_s": 900}
        result = run_json(data)
        self.assertLessEqual(result.diagnostics["speed_cm_s"], 420)


class MockBackendTests(unittest.TestCase):
    def test_deterministic_for_seed(self):
        a = run_json(example()).to_dict()
        b = run_json(example()).to_dict()
        self.assertEqual(a["frames"], b["frames"])
        other = example()
        other["seed"] = 8
        self.assertNotEqual(a["frames"], run_json(other).to_dict()["frames"])

    def test_reaches_goal_and_faces_goal_yaw(self):
        result = run_json(example())
        last = result.frames[-1]
        self.assertAlmostEqual(last.root_translation[0], 600, delta=0.5)
        self.assertAlmostEqual(last.root_translation[1], 250, delta=0.5)
        yaw = 2 * math.atan2(last.rotations[0][2], last.rotations[0][3])
        self.assertAlmostEqual(math.degrees(yaw), 90, delta=0.5)

    def test_contacts_cover_every_frame(self):
        result = run_json(example())
        self.assertEqual(len(result.left_contacts), len(result.frames))
        self.assertEqual(len(result.right_contacts), len(result.frames))
        # Standing still at both ends: both feet planted.
        self.assertTrue(result.left_contacts[0] and result.right_contacts[0])
        self.assertTrue(result.left_contacts[-1] and result.right_contacts[-1])

    def test_idle_holds_position(self):
        data = example()
        data["locomotion"] = {"style": "idle"}
        result = run_json(data)
        self.assertEqual(result.frames[-1].root_translation, (0.0, 0.0, 0.0))

    def test_result_round_trips(self):
        result = run_json(example())
        again = MotionResult.from_dict(json.loads(json.dumps(result.to_dict())))
        self.assertEqual(again.to_dict(), result.to_dict())


def mannequin_request(fixture: str = "ue4_mannequin") -> dict:
    data = example()
    with open(FIXTURES / f"{fixture}_skeleton.json", encoding="utf-8") as f:
        data["character"]["skeleton"] = json.load(f)
    return data


class TargetSkeletonTests(unittest.TestCase):
    """Generation directly on real Unreal skeletons, checked on every humanoid fixture."""

    @classmethod
    def setUpClass(cls):
        cls.results = {name: run_json(mannequin_request(name)) for name in HUMANOIDS}

    def _facing_deg(self, result: MotionResult, frame: int) -> float:
        idx = result.skeleton.index
        p = world_positions(result, frame)
        dx = sum(p[idx(f"ball_{s}")][0] - p[idx(f"foot_{s}")][0] for s in "lr")
        dy = sum(p[idx(f"ball_{s}")][1] - p[idx(f"foot_{s}")][1] for s in "lr")
        return math.degrees(math.atan2(dy, dx))

    def test_generates_on_requested_skeleton(self):
        for name, expected in (("ue4_mannequin", 68), ("ue5_manny", 89), ("ue5_quinn", 89)):
            with self.subTest(skeleton=name):
                result = self.results[name]
                self.assertEqual(result.skeleton.name, "SK_Mannequin")
                self.assertEqual(len(result.frames[0].rotations), expected)

    def test_mesh_facing_is_corrected(self):
        # Mannequin meshes face +Y; the clip must still face start/goal yaw in world space.
        for name, result in self.results.items():
            with self.subTest(skeleton=name):
                self.assertAlmostEqual(self._facing_deg(result, 0), 0.0, delta=1.0)
                self.assertAlmostEqual(self._facing_deg(result, len(result.frames) - 1), 90.0, delta=1.0)

    def test_feet_grounded_and_arms_hanging(self):
        for name, result in self.results.items():
            idx = result.skeleton.index
            with self.subTest(skeleton=name):
                p = world_positions(result, 0)
                for side in "lr":
                    self.assertLess(p[idx(f"ball_{side}")][2], 10.0)
                    upper, hand = p[idx(f"upperarm_{side}")], p[idx(f"hand_{side}")]
                    self.assertLess(hand[2], upper[2] - 40.0)
                lowest = min(
                    world_positions(result, f)[idx(f"ball_{side}")][2]
                    for f in range(len(result.frames)) for side in "lr"
                )
                self.assertGreater(lowest, -2.0)

    def test_left_foot_is_on_the_left(self):
        # Facing +X in Unreal, left is -Y.
        for name, result in self.results.items():
            idx = result.skeleton.index
            with self.subTest(skeleton=name):
                p = world_positions(result, 0)
                self.assertLess(p[idx("foot_l")][1], 0.0)
                self.assertGreater(p[idx("foot_r")][1], 0.0)

    def test_rejects_skeleton_without_legs(self):
        data = example()
        data["character"]["skeleton"] = {"name": "blob", "joints": [{"name": "root", "parent": -1, "offset": [0, 0, 0]}]}
        with self.assertRaises(RequestError):
            run_json(data)

    def test_rejects_children_before_parents(self):
        data = example()
        data["character"]["skeleton"] = {"name": "bad", "joints": [
            {"name": "root", "parent": -1, "offset": [0, 0, 0]},
            {"name": "a", "parent": 2, "offset": [0, 0, 1]},
            {"name": "b", "parent": 0, "offset": [0, 0, 1]},
        ]}
        with self.assertRaises(RequestError):
            parse_request(data)

    def test_unsupported_backend_says_it_needs_retargeting(self):
        from sovereign_motion.backends.base import MotionBackend
        from sovereign_motion.pipeline import run

        captured = []

        class Fake(MotionBackend):
            name = "fake"

            def generate(self, request):
                captured.append(request)
                return get_backend("mock").generate(dataclasses.replace(
                    request, character=dataclasses.replace(request.character, skeleton=None)))

        import sovereign_motion.pipeline as pipeline
        original = pipeline.get_backend
        pipeline.get_backend = lambda name: Fake()
        try:
            result = run(parse_request(mannequin_request()))
        finally:
            pipeline.get_backend = original
        self.assertTrue(any("needs retargeting" in n for n in result.diagnostics["notes"]))


class CurvedPathTests(unittest.TestCase):
    """Dense waypoints, as sampled from a Sovereign Motion Path spline in the editor."""

    def setUp(self):
        radius = 400.0
        samples = int(radius * math.pi / 2 / 50)  # every ~50 cm, like the path actor
        points = [
            [radius * math.sin(a), radius - radius * math.cos(a), 0.0]
            for a in (math.pi / 2 * i / samples for i in range(samples + 1))
        ]
        data = mannequin_request("ue5_manny")
        data["start"] = {"root_location": points[0], "root_yaw_deg": 0.0}
        data["goal"] = {"root_location": points[-1], "root_yaw_deg": 90.0}
        data["trajectory"] = {"waypoints": points[1:-1]}
        self.radius = radius
        self.result = run_json(data)

    def test_root_follows_the_curve(self):
        for frame in self.result.frames:
            x, y, _ = frame.root_translation
            self.assertAlmostEqual(math.hypot(x, y - self.radius), self.radius, delta=3.0)

    def test_turns_smoothly(self):
        # Root yaw should never jump between frames on a gentle curve.
        yaws = [2 * math.atan2(f.rotations[0][2], f.rotations[0][3]) for f in self.result.frames]
        for a, b in zip(yaws, yaws[1:]):
            step = abs((b - a + math.pi) % (2 * math.pi) - math.pi)
            self.assertLess(math.degrees(step), 10.0)

    def test_feet_stay_planted(self):
        self.assertLess(foot_slide(self.result, "foot_l", "foot_r")["mean_cm_s"], 3.0)


class FootLockTests(unittest.TestCase):
    def _pair(self, data: dict):
        unlocked = dict(data, foot_lock=False)
        return run_json(unlocked), run_json(data)

    def test_reduces_slide_on_both_skeletons(self):
        for name, data in (("mock", example()), *((h, mannequin_request(h)) for h in HUMANOIDS)):
            with self.subTest(skeleton=name):
                raw, locked = self._pair(data)
                before = foot_slide(raw, "foot_l", "foot_r")["mean_cm_s"]
                after = foot_slide(locked, "foot_l", "foot_r")["mean_cm_s"]
                self.assertGreater(before, 30.0)
                self.assertLess(after, 3.0)

    def test_planted_foot_keeps_orientation_and_hip_stays(self):
        raw, locked = self._pair(mannequin_request())
        idx = raw.skeleton.index
        f = next(i for i in range(len(raw.frames)) if raw.left_contacts[i] and i > 30)
        (raw_pos, raw_rot), (lock_pos, lock_rot) = world_transforms(raw, f), world_transforms(locked, f)
        for a, b in zip(raw_rot[idx("foot_l")], lock_rot[idx("foot_l")]):
            self.assertAlmostEqual(abs(a), abs(b), places=5)
        for a, b in zip(raw_pos[idx("thigh_l")], lock_pos[idx("thigh_l")]):
            self.assertAlmostEqual(a, b, places=6)

    def test_swing_frames_outside_blend_are_untouched(self):
        raw, locked = self._pair(example())
        thigh = raw.skeleton.index("thigh_l")
        contacts = raw.left_contacts
        for f in range(len(contacts)):
            recent = any(contacts[max(0, f - 5):f + 1])
            if not recent:
                self.assertEqual(raw.frames[f].rotations[thigh], locked.frames[f].rotations[thigh])

    def test_skips_skeleton_without_leg_chain(self):
        skeleton = Skeleton("t", [Joint("root", -1, (0, 0, 0)), Joint("foot_l", 0, (0, -10, 0))])
        frames = [Frame((0.0, 0.0, 0.0), [(0.0, 0.0, 0.0, 1.0)] * 2)] * 3
        result = MotionResult("t", "test", 30, skeleton, frames, [True] * 3, [True] * 3)
        locked, notes = lock_feet(result)
        self.assertIs(locked, result)
        self.assertTrue(any("skipped" in n for n in notes))

    def test_reports_result_in_diagnostics(self):
        result = run_json(example())
        self.assertIn("foot_slide_after_cm_s", result.diagnostics)
        self.assertTrue(any(n.startswith("foot lock:") for n in result.diagnostics["notes"]))


class MotionBricksAdapterTests(unittest.TestCase):
    def test_unconfigured_backend_reports_why(self):
        data = example()
        data["backend"] = "motionbricks"
        with self.assertRaises(BackendUnavailable):
            run_json(data)

    def test_constraints_scale_to_meters(self):
        constraints = build_constraints(parse_request(example()))
        self.assertEqual(constraints["root_waypoints_2d"][-1], (6.0, 2.5))
        self.assertTrue(constraints["joint_targets"][0]["hard"])


class BvhTests(unittest.TestCase):
    def test_euler_decomposition_recomposes(self):
        q = quat_mul(
            quat_from_axis_angle((0, 0, 1), 0.7),
            quat_mul(quat_from_axis_angle((1, 0, 0), -0.4), quat_from_axis_angle((0, 1, 0), 1.1)),
        )
        z, x, y = quat_to_euler_zxy_deg(q)
        self.assertAlmostEqual(z, math.degrees(0.7), places=4)
        self.assertAlmostEqual(x, math.degrees(-0.4), places=4)
        self.assertAlmostEqual(y, math.degrees(1.1), places=4)

    def test_bvh_structure(self):
        result = run_json(example())
        text = to_bvh(result)
        joint_count = len(result.skeleton.joints)
        self.assertEqual(text.count("JOINT ") + text.count("ROOT "), joint_count)
        motion = text.split("MOTION\n", 1)[1].splitlines()
        self.assertEqual(motion[0], f"Frames: {len(result.frames)}")
        self.assertEqual(len(motion) - 2, len(result.frames))
        self.assertEqual(len(motion[2].split()), 3 + 3 * joint_count)


class MetricsTests(unittest.TestCase):
    def _two_bone(self, root_xs: list[float]) -> MotionResult:
        skeleton = Skeleton("t", [Joint("root", -1, (0, 0, 0)), Joint("foot_l", 0, (0, -10, 0)),
                                  Joint("foot_r", 0, (0, 10, 0))])
        identity = [(0.0, 0.0, 0.0, 1.0)] * 3
        frames = [Frame((x, 0.0, 0.0), list(identity)) for x in root_xs]
        contacts = [True] * len(frames)
        return MotionResult("t", "test", 10, skeleton, frames, contacts, list(contacts))

    def test_planted_feet_do_not_slide(self):
        self.assertEqual(foot_slide(self._two_bone([0, 0, 0]), "foot_l", "foot_r")["max_cm_s"], 0.0)

    def test_sliding_feet_are_measured(self):
        # Root moves 5 cm per frame at 10 fps with both feet "planted": 50 cm/s of slide.
        self.assertAlmostEqual(foot_slide(self._two_bone([0, 5, 10]), "foot_l", "foot_r")["mean_cm_s"], 50.0)

    def test_fk_applies_parent_rotation(self):
        result = self._two_bone([0])
        result.frames[0].rotations[0] = quat_from_axis_angle((0, 0, 1), math.pi / 2)
        left = world_positions(result, 0)[1]
        self.assertAlmostEqual(left[0], 10.0, places=6)
        self.assertAlmostEqual(left[1], 0.0, places=6)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"{self.base}/generate", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as err:
            return err.code, json.load(err)

    def test_health(self):
        with urllib.request.urlopen(f"{self.base}/health") as resp:
            self.assertEqual(json.load(resp)["status"], "ok")

    def test_generate(self):
        status, body = self._post(example())
        self.assertEqual(status, 200)
        self.assertEqual(body["request_id"], "dominion_walk_to_terminal_001")
        self.assertIn("generate_ms", body["diagnostics"])

    def test_error_codes(self):
        bad = copy.deepcopy(example())
        bad["fps"] = 0
        self.assertEqual(self._post(bad)[0], 400)
        unknown = copy.deepcopy(example())
        unknown["backend"] = "nope"
        self.assertEqual(self._post(unknown)[0], 400)
        unavailable = copy.deepcopy(example())
        unavailable["backend"] = "motionbricks"
        self.assertEqual(self._post(unavailable)[0], 503)


if __name__ == "__main__":
    unittest.main()
