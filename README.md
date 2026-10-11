# Sovereign Motion Lab

Contextual motion authoring for Sovereign Call, built into Unreal Engine 5. You describe what a
character needs to do (where it goes, how it moves, what it touches) and a motion model fills in
the animation. The result is baked into a normal Animation Sequence.

NVIDIA MotionBricks is the first model backend, but nothing outside its adapter depends on it.
The workflow, request format and Unreal tools are the durable part. Models are swappable.

> Status: **scaffold**. The Python sidecar runs end to end with a procedural mock backend and is
> tested. The UE5 plugin is written against UE 5.4+ APIs but **has not been compiled yet**.
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
   and build the project. (It hasn't been compiled yet, so expect some API fixes on the first build.)
2. Start the sidecar: `python3 -m sovereign_motion serve`.
3. Open **Tools > Sovereign Motion Studio**. Click **Check Sidecar**, edit the request, then **Generate**.
   Requests and results are saved in `Saved/SovereignMotion/`.
4. To bake, set **Source Skeleton** to a skeleton whose bone names match the backend's output
   skeleton (for the mock, import `out/walk.bvh` via Blender as FBX once). Then click
   **Bake to Animation Sequence**, and retarget the sequence onto game characters with an IK Retargeter.

The sidecar URL is set in Project Settings > Plugins > Sovereign Motion Studio.
`USovereignMotionBakeLibrary` exposes baking and result loading to Editor Utility Blueprints.

## Roadmap

| Stage | Goal |
| --- | --- |
| **0.0 spike** | Run MotionBricks for real, export, retarget by hand onto a Sovereign Call soldier, and judge quality. Go/no-go for the model. [Checklist](docs/spike-0.0.md). |
| 0.1 | The designer's milestone: generate a walk to an editor-picked destination, preview it, bake it, with foot locking and no significant sliding. |
| 0.2 | Viewport gizmos for trajectory and joint targets, plus regenerating part of a clip. |
| 0.3 | Smart-object interactions (terminals, doors, ladders), automated retargeting, contact validation. |
| 0.4 | Runtime: in-engine inference (likely ONNX through Unreal's NNE) driven by StateTree. |
