# Sovereign Motion Lab

Contextual motion authoring for Sovereign Call, built into Unreal Engine 5. You describe what a
character needs to do (where it goes, how it moves, what it touches) and a motion model fills in
the animation. The result is baked into a normal Animation Sequence.

NVIDIA MotionBricks is the first model backend, but nothing outside its adapter depends on it.
The workflow, request format and Unreal tools are the durable part. Models are swappable.

> Status: **early prototype**. The sidecar runs end to end with a procedural mock backend and is
> tested. The UE5 plugin builds and loads. The editor round trip (pick points in the level,
> generate on your own skeleton, bake, open) is written but not yet verified in-engine.
> The MotionBricks adapter translates requests but doesn't call the model yet; see
> [docs/spike-0.0.md](docs/spike-0.0.md).

## How it fits together

```
Unreal Editor (Plugins/SovereignMotionStudio)
  Studio tab: edit a motion request -> Generate -> Bake to Animation Sequence
        |  HTTP + JSON (schema/motion_request.schema.json)
        v
Python sidecar (sidecar/sovereign_motion), localhost:8765
  request -> character profile (context translation) -> backend -> result
                                                          |-- mock         (procedural, no GPU)
                                                          '-- motionbricks (adapter stub)
        |  JSON (schema/motion_result.schema.json)
        v
Unreal: result on the backend's skeleton -> AnimSequence (+ foot contact curves)
        -> IK Retargeter -> Sovereign Call characters
```

Design rules:

- **The request is the source of truth.** Every generation saves its request JSON next to the
  result, so a clip can be regenerated or tweaked later instead of hand-edited.
- **Context gets translated, not passed through.** Models only understand positions, rotations,
  timing and text. Traits like heavy armor become numbers in a character profile
  (`sidecar/profiles/*.json`). That translation layer is ours, and it grows over time.
- **Unreal enforces hard rules.** Collision, movement locks and attack windows stay in gameplay
  code. Hard joint targets get re-applied with IK after generation, and foot contacts are baked as
  curves (`FootContact_L` / `FootContact_R`) for a foot-locking pass.
- **Results use a fixed convention**: Unreal axes (Z-up, X-forward, cm), joint-local rotations
  relative to an identity-rotation bind pose. Each backend converts to this, and the baker transfers
  it onto any skeleton with matching bone names, bone rolls included.

## Repository layout

| Path | What |
| --- | --- |
| `schema/` | JSON Schemas for requests and results (v1.0). The contract between Unreal and models. |
| `sidecar/` | Python service (stdlib only): CLI, HTTP server, backends, BVH export, metrics, tests. |
| `sidecar/profiles/` | Character profiles that turn traits into numeric limits. |
| `examples/requests/` | Sample requests. |
| `Plugins/SovereignMotionStudio/` | UE5 plugin. `SovereignMotionCore` (runtime: types, JSON, sidecar client, settings) and `SovereignMotionEditor` (studio tab, baker). |
| `docs/` | Spike plan and notes. |

## Quick start: sidecar

Requires Python 3.10+. No packages needed for the mock backend.

```bash
cd sidecar
python3 -m sovereign_motion backends                       # what can run here
python3 -m sovereign_motion generate ../examples/requests/dominion_soldier_walk.json --out out/walk
python3 -m sovereign_motion evaluate out/walk.json         # foot-slide metric
python3 -m sovereign_motion serve                          # for the Unreal plugin
python3 -m unittest discover -s tests                      # tests
```

`generate` writes `out/walk.json` (the result) and `out/walk.bvh`. To look at the BVH, import it
into Blender (File > Import > Motion Capture) with **Up = Z, Forward = X, Scale = 0.01**.

The mock backend is a test dummy. Its legs aren't tied to ground speed, so it slides
(~55 cm/s mean on the example). That gives `evaluate` a known-bad baseline to compare real
backends against.

## Quick start: Unreal plugin

1. Copy or symlink `Plugins/SovereignMotionStudio` into a UE 5.4+ project's `Plugins/` folder
   and build the project.
2. Start the sidecar: `python3 -m sovereign_motion serve`.
3. Open **Tools > Sovereign Motion Studio** and click **Check Sidecar**.
4. Set **Target Skeleton** (e.g. UE5's `SK_Mannequin`, shared by Manny and Quinn, or the UE4 `SK_Mannequin`).
   Its reference pose is sent with the request, and the mock animates it directly. Limbs are found by
   standard Mannequin bone names (`pelvis`, `thigh_l`, `calf_l`, `foot_l`, `ball_l`, `upperarm_l`,
   `lowerarm_l`, ...), and the mesh's facing comes from its feet.
5. Lay out the path. Either:
   - Click **New Path**. A **Sovereign Motion Path** (a spline) appears in front of the camera, already
     selected. Click a point and drag it; Alt-drag a point to add one; right-click a point for more.
     The first point is the start, the last is the goal, and the curve is sampled into waypoints
     every 50 cm (**Sample Spacing**). Points snap to the floor, and the clip faces along the path
     unless **Face Along Path** is off. Paths are saved with the level, so you can keep and reuse
     them; they're editor-only and stripped from game builds. To reuse one, select it and click
     **Use Level Selection**.
   - Or place actors (Target Points work well), select them in order (start, any waypoints, goal),
     and click **Use Level Selection**.

   With **Auto-regenerate** on (the default), editing or moving the bound path regenerates the clip
   about a quarter-second after you stop dragging, and the preview (step 7) updates to match.
6. Click **Generate**. Foot locking runs by default (toggle **Foot Lock** in the request), and the
   status line reports slide before and after.
7. Click **Preview in Level** to watch the clip play along its path on a temporary mesh (the
   skeleton's preview mesh unless you set **Preview Mesh**). The actual root path is drawn, with the
   start in green and the end in red. Nothing is saved. The viewport must be in Realtime mode (Ctrl+R).
   Generating again while previewing updates the preview.
8. Click **Bake to Animation Sequence**. With Source Skeleton left empty it bakes onto the target
   skeleton, makes root motion relative to the start, and opens the clip.
   Requests and results are saved in `Saved/SovereignMotion/` so any clip can be regenerated.

For backends that output their own skeleton (MotionBricks, once wired up), the result notes say
so. Bake onto a skeleton matching theirs, then retarget with an IK Retargeter.

IK helper bones are driven at bake and preview time. Each frame, every bone in **IK Bone Sources**
(Bake settings) is placed exactly on its source bone in component space. The defaults follow the
Mannequin convention, where `ik_foot_l/r` follow `foot_l/r`, `ik_hand_gun` and `ik_hand_r` follow
`hand_r`, and `ik_hand_l` follows `hand_l`. That matches UE4 and UE5 reference poses, where each IK
bone coincides with its source. Edit the map for custom rigs. Pairs a skeleton doesn't have are
skipped. `center_of_mass` and `interaction` are not driven.

Known gaps in the mock: fingers, twist bones and spine don't move. It walks with soft knees and lowers the **root** bone
a few cm to keep the feet on the floor, because results only carry root translation. Real backends
supply their own hip motion.

Tested skeletons: `sidecar/tests/fixtures/` holds the reference poses of the UE4 Mannequin (68 bones),
UE5 Manny and UE5 Quinn (89 bones each), extracted from FBX exports. Every target-skeleton and
foot-lock test runs on all three.

### Foot locking

`sidecar/sovereign_motion/postprocess/footlock.py` runs after every backend. During each contact
window from the result's foot contacts, the ankle is pinned where contact began, and thigh and calf
are re-solved with two-bone IK. The knee keeps its bend plane and the foot keeps its orientation.
After lift-off the foot eases back to the original motion over 4 frames. Frames where a planted
foot is out of the leg's reach are counted and reported, since that usually points at a
stride/contact mismatch in the source motion. On the example walk it takes mean slide from about
50 cm/s to under 2 cm/s on the mock skeleton, the UE4 Mannequin, and UE5 Manny and Quinn.

The sidecar URL is set in Project Settings > Plugins > Sovereign Motion Studio.
`USovereignMotionBakeLibrary` exposes baking and result loading to Editor Utility Blueprints.

## Roadmap

| Stage | Goal |
| --- | --- |
| 0.0-editor (done) | Plugin round trip on our own skeleton with the mock: level points, generate, bake, open. |
| 0.0-editor-2 (done) | Foot locking (sidecar post-process) and in-level preview. |
| 0.0-editor-3 (done) | IK bones driven on bake/preview; draggable spline paths with auto-regenerate. |
| **0.0 spike** | Run MotionBricks for real, export, retarget by hand onto a Sovereign Call soldier, and judge quality. Go/no-go for the model. [Checklist](docs/spike-0.0.md). |
| 0.1 | The designer's milestone: generate a walk to an editor-picked destination, preview it, bake it, with foot locking and no significant sliding. |
| 0.2 | Viewport gizmos for trajectory and joint targets, plus regenerating part of a clip. |
| 0.3 | Smart-object interactions (terminals, doors, ladders), automated retargeting, contact validation. |
| 0.4 | Runtime: in-engine inference (likely ONNX through Unreal's NNE) driven by StateTree. |
