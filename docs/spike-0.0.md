# Spike 0.0: does MotionBricks work for our characters?

The goal is to answer the riskiest question before building more editor UI: **does motion from the
public MotionBricks weights look acceptable on Sovereign Call characters?** Budget: a few days.
The result is a go/no-go, not shippable code.

## Facts confirmed from the repo

Checked against NVlabs/GR00T-WholeBodyControl at commit `b042411f` (the `motionbricks/` folder).

- [x] **Location and licenses:** github.com/NVlabs/GR00T-WholeBodyControl, folder `motionbricks/`.
      There is no standalone repo. The repository LICENSE is dual: Apache 2.0 for source code, and
      the NVIDIA Open Model License for weights (commercial use allowed with attribution).
- [x] **Output skeleton:** Unitree G1, 29 DOF. In MuJoCo that is 30 bodies, `qpos` of 36 values.
      The model's internal representation is a 34-joint G1 (two dummy toes).
- [x] **Training data:** BONES-SEED, about 350k clips of human mocap retargeted onto the G1. The
      motion is human-derived; the proportions are a robot's.
- [x] **Inputs:** the public `motion_inference.predict` takes context and target keyframes (root and
      pose) plus a token count. Text-embedding inputs exist but are unused in the preview. The
      shipped agent (`full_navigation_agent`) is steered by movement direction, facing direction,
      style mode, optional target speed, and optional specific target positions and headings.
- [x] **Styles in the preview:** idle, slow_walk, walk, hand_crawling, walk_boxing, elbow_crawling,
      stealth_walk, injured_walk, walk_stealth, walk_happy_dance, walk_zombie, walk_gun, walk_scared,
      walk_left, walk_right. There's no run.
- [x] **Conventions:** the motion space is Y-up, Z-forward, right-handed. MuJoCo is Z-up, X-forward.
      Units are meters, 30 fps, 4 frames per token.
- [ ] GPU and VRAM needed, and time per clip: **measure on the work PC.**
- [ ] ONNX export: the code mentions ONNX/TensorRT paths ("prebaked inference engine") but they are
      not implemented in the preview. A community GGML/C++ port exists (localai-org/motion-bricks.cpp).

## Steps

1. ~~Clone and read the code.~~ Done.
2. ~~Implement the backend.~~ Done: `sidecar/sovereign_motion/backends/motionbricks.py` drives
   NVIDIA's agent with a path-following controller. `g1.py` converts the G1 output with MuJoCo FK,
   and `retarget.py` maps it onto Mannequin skeletons. Everything except the neural network itself
   is tested on CPU with a fake agent.
3. **On the GPU PC:** follow [motionbricks-setup.md](motionbricks-setup.md), run the smoke test,
   and record time per clip and foot slide against the mock.
4. Preview MotionBricks clips on Manny in the Studio tab, with a few styles (`walk_gun`,
   `injured_walk`).
5. Review with the animation lead.

## Go / no-go

Go if all of these hold:

- Retargeted clips read as human, not robotic, on a Dominion soldier.
- Foot slide during contact is low enough that a foot-lock pass can clean it up.
- Generation is fast enough for interactive editor use (seconds, not minutes).
- License terms allow our internal use. (We're not planning to sell the plugin.)

If not, the scaffold still stands: plug a different model in behind `MotionBackend`.
