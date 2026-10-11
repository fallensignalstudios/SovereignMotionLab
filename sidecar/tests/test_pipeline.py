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
from sovereign_motion.metrics import foot_slide, world_positions
from sovereign_motion.pipeline import run_json
from sovereign_motion.request import RequestError, parse_request
from sovereign_motion.result import Frame, Joint, MotionResult, Skeleton
from sovereign_motion.server import make_server

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "requests" / "dominion_soldier_walk.json"
# UE4 SK_Mannequin reference pose in the result convention, extracted from a Motus FBX.
MANNEQUIN = Path(__file__).resolve().parent / "fixtures" / "ue4_mannequin_skeleton.json"


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


def mannequin_request() -> dict:
    data = example()
    with open(MANNEQUIN, encoding="utf-8") as f:
        data["character"]["skeleton"] = json.load(f)
    return data


class TargetSkeletonTests(unittest.TestCase):
    def setUp(self):
        self.result = run_json(mannequin_request())
        self.idx = self.result.skeleton.index

    def _facing_deg(self, frame: int) -> float:
        p = world_positions(self.result, frame)
        dx = sum(p[self.idx(f"ball_{s}")][0] - p[self.idx(f"foot_{s}")][0] for s in "lr")
        dy = sum(p[self.idx(f"ball_{s}")][1] - p[self.idx(f"foot_{s}")][1] for s in "lr")
        return math.degrees(math.atan2(dy, dx))

    def test_generates_on_requested_skeleton(self):
        self.assertEqual(self.result.skeleton.name, "SK_Mannequin")
        self.assertEqual(len(self.result.frames[0].rotations), 68)

    def test_mesh_facing_is_corrected(self):
        # The mannequin mesh faces +Y; the clip must still face start/goal yaw in world space.
        self.assertAlmostEqual(self._facing_deg(0), 0.0, delta=1.0)
        self.assertAlmostEqual(self._facing_deg(len(self.result.frames) - 1), 90.0, delta=1.0)

    def test_feet_grounded_and_arms_hanging(self):
        p = world_positions(self.result, 0)
        for side in "lr":
            self.assertLess(p[self.idx(f"ball_{side}")][2], 10.0)
            upper, hand = p[self.idx(f"upperarm_{side}")], p[self.idx(f"hand_{side}")]
            self.assertLess(hand[2], upper[2] - 45.0)

    def test_left_foot_is_on_the_left(self):
        # Facing +X in Unreal, left is -Y.
        p = world_positions(self.result, 0)
        self.assertLess(p[self.idx("foot_l")][1], 0.0)
        self.assertGreater(p[self.idx("foot_r")][1], 0.0)

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
