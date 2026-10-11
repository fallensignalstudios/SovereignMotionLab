# Spike 0.0: does MotionBricks work for our characters?

The goal is to answer the riskiest question before building more editor UI: **does motion from the
public MotionBricks weights look acceptable on Sovereign Call characters?** Budget: a few days.
The result is a go/no-go, not shippable code.

## Facts to confirm from the repo (don't assume)

Write the answers into this file as you find them.

- [ ] Exact repo URL, commit tested, and license files for both code and weights.
- [ ] Which skeleton the released weights output (joint count, names, rest pose). Reported as a
      Unitree G1 robot skeleton; confirm it.
- [ ] What training data the released weights cover (human mocap? robot data?). This decides
      whether the motion will look human.
- [ ] Which inputs the public inference code actually accepts: keyframes, sparse joint targets,
      2D waypoints, dense paths, text, smart primitives.
- [ ] Model coordinate conventions: up axis, units, handedness, frame rate.
- [ ] GPU and VRAM needed, and time per clip.
- [ ] Whether the model exports to ONNX (decides whether runtime use in 0.4 is possible).

## Steps

1. Clone the MotionBricks repo and download the weights outside this repo
   (`checkpoints/` is gitignored). Get NVIDIA's own example script running first.
2. Generate three clips with NVIDIA's tools: a straight walk, a walk with a turn, and a
   reach-to-target if supported.
3. Fill in `generate()` in `sidecar/sovereign_motion/backends/motionbricks.py`. Map
   `build_constraints()` onto the real inputs, then convert the output to the result convention
   (Unreal axes, cm, identity-rotation bind pose). Cache the loaded model on the backend instance.
4. Run the example request through `--backend motionbricks`, then run `evaluate` and compare its
   foot slide with the mock baseline.
5. Bring the BVH into UE5 (Blender: Up = Z, Forward = X, Scale = 0.01, then export FBX). Set up an
   IK Retargeter from the backend skeleton to a Dominion soldier, and retarget the clips.
6. Review the clips in UE5 with the animation lead.

## Go / no-go

Go if all of these hold:

- Retargeted clips read as human, not robotic, on a Dominion soldier.
- Foot slide during contact is low enough that a foot-lock pass can clean it up.
- Generation is fast enough for interactive editor use (seconds, not minutes).
- License terms allow our internal use. (We're not planning to sell the plugin.)

If not, the scaffold still stands: plug a different model in behind `MotionBackend`.
