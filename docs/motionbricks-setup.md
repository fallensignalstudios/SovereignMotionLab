# Running the MotionBricks backend

These steps get NVIDIA's MotionBricks preview running behind the Sovereign Motion sidecar on a
machine with an NVIDIA GPU. Commands are shown for Windows (PowerShell); the Linux equivalents are
the same apart from paths and `export` instead of `$env:`.

## 1. Prerequisites

- An NVIDIA GPU with a recent driver.
- Git and [Git LFS](https://git-lfs.com/). Run `git lfs install` once.
- Python 3.10 or newer. A conda environment is easiest.

## 2. Get MotionBricks and its checkpoints (~2.2 GB)

MotionBricks lives inside NVIDIA's GR00T-WholeBodyControl repository. The checkpoints are skipped on
a normal clone, so pull them explicitly:

```powershell
git clone https://github.com/NVlabs/GR00T-WholeBodyControl.git
cd GR00T-WholeBodyControl
git lfs pull --include="motionbricks/out/**" --exclude=""
```

Check that these are real files, not ~1 KB LFS pointers:

```powershell
dir motionbricks\out\G1-clip.ckpt                                     # ~7.5 MB
dir motionbricks\out\motionbricks_vqvae\version_1\checkpoints\*.ckpt  # ~273 MB
dir motionbricks\out\motionbricks_pose\version_1\checkpoints\*.ckpt   # ~1.6 GB
dir motionbricks\out\motionbricks_root\version_1\checkpoints\*.ckpt   # ~391 MB
```

The G1 meshes aren't needed: the sidecar only uses the robot's kinematic tree.

## 3. Python environment

```powershell
conda create -n motionbricks python=3.10 -y
conda activate motionbricks

# PyTorch with CUDA: pick the command for your CUDA version at https://pytorch.org/get-started/locally/
pip install torch --index-url https://download.pytorch.org/whl/cu124

# MotionBricks itself, then our sidecar, in the same environment
pip install -e GR00T-WholeBodyControl\motionbricks
pip install -e SovereignMotionLab\sidecar
```

Check that torch sees the GPU:

```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

## 4. Point the sidecar at it

```powershell
$env:SOVEREIGN_MOTIONBRICKS_REPO = "C:\path\to\GR00T-WholeBodyControl\motionbricks"
cd SovereignMotionLab\sidecar
python -m sovereign_motion backends
```

`motionbricks` should say `ready (device cuda)`. If not, the reason is printed.
To make the variable permanent: `setx SOVEREIGN_MOTIONBRICKS_REPO "C:\path\to\...\motionbricks"`,
then open a new terminal.

## 5. Smoke test: generate a walk on UE5 Manny

```powershell
python -m sovereign_motion generate ..\examples\requests\dominion_soldier_walk.json --backend motionbricks --skeleton tests\fixtures\ue5_manny_skeleton.json --out out\mb_manny
python -m sovereign_motion evaluate out\mb_manny.json
```

For comparison, the same request on the mock:

```powershell
python -m sovereign_motion generate ..\examples\requests\dominion_soldier_walk.json --skeleton tests\fixtures\ue5_manny_skeleton.json --out out\mock_manny
python -m sovereign_motion evaluate out\mock_manny.json
```

The first MotionBricks run loads about 2 GB of weights, so it takes a while. Later requests through
`serve` reuse the loaded models.

Things to note from the output:
- the `note:` lines (retarget scale, foot lock before/after, anything ignored)
- the frame count and duration
- foot slide from `evaluate`
- any traceback, in full

## 6. Try a style

Edit a copy of the request and set the locomotion block to, for example:

```json
"locomotion": { "style": "walk", "backend_style": "walk_gun" }
```

Available styles: `walk_gun`, `injured_walk`, `walk_zombie`, `walk_stealth`, `stealth_walk`,
`walk_scared`, `walk_boxing`, `walk_happy_dance`, `slow_walk`, `hand_crawling`, `elbow_crawling`.

## 7. In Unreal

Start the service from the same environment, with the variable set:

```powershell
python -m sovereign_motion serve
```

In the Studio tab, set **Backend** to `motionbricks`, set **Target Skeleton** to UE5 `SK_Mannequin`,
optionally set **Backend Style**, then Generate and Preview as usual. With Auto-regenerate on,
each path edit runs MotionBricks again.

## How the backend works

- NVIDIA's demo agent runs headless. A path-following controller replaces the keyboard: it steers
  toward a point 0.8 m ahead on the path, then idles facing the goal yaw.
- The agent outputs Unitree G1 poses. These are converted with MuJoCo forward kinematics, then
  retargeted onto any skeleton with Unreal Mannequin bone names (`sovereign_motion/retarget.py`).
- The G1 is about 1.3 times smaller than a Mannequin. The agent walks a proportionally scaled path
  at a proportionally scaled speed, and the retarget scales root motion back up.
- Foot contacts are detected from the G1's ankle height and speed, then foot lock runs as usual.

## Known limitations of this first version

- **Ignored for now:** joint targets, `text_prompt` and `arrive_by_s`.
- **No run style:** there is no run or jog clip in the public preview. `jog` and `run` use `walk` at
  the requested speed.
- **Spine, fingers and twist bones:** the G1 has one waist and no fingers or twist bones. Manny's
  spine follows the G1 torso, and fingers and twist bones stay in their reference pose.
- **GPU required:** NVIDIA's loader expects CUDA. `SOVEREIGN_MOTIONBRICKS_DEVICE` exists, but CPU
  is untested and likely fails inside their code.
- **torch 2.6+ loading:** newer torch refuses full-object checkpoint loads by default. The sidecar
  allows them for NVIDIA's checkpoints only, within its own process.
