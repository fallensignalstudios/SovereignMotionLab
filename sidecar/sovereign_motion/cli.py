"""Command line entry point.

    python -m sovereign_motion generate ../examples/requests/dominion_soldier_walk.json --out out/walk
    python -m sovereign_motion serve --port 8765
    python -m sovereign_motion backends
    python -m sovereign_motion evaluate out/walk.json --left-foot foot_l --right-foot foot_r
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from pathlib import Path

from .backends import BackendUnavailable, backend_names, get_backend
from .export.bvh import write_bvh
from .metrics import foot_slide
from .pipeline import run
from .request import RequestError, load_request
from .result import MotionResult


def _generate(args: argparse.Namespace) -> int:
    try:
        request = load_request(args.request)
        if args.backend:
            request = dataclasses.replace(request, backend=args.backend)
        result = run(request)
    except (RequestError, BackendUnavailable, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    json_path = out.with_suffix(".json")
    bvh_path = out.with_suffix(".bvh")
    result.save(json_path)
    write_bvh(result, bvh_path)
    print(f"{len(result.frames)} frames @ {result.fps} fps ({result.duration_s:.2f}s) via {result.backend}")
    for note in result.diagnostics.get("notes", []):
        print(f"  note: {note}")
    print(f"wrote {json_path}")
    print(f"wrote {bvh_path}")
    return 0


def _backends(_: argparse.Namespace) -> int:
    for name in backend_names():
        ok, reason = get_backend(name).available()
        print(f"{name:14} {'ready' if ok else 'unavailable':12} {reason}")
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    with open(args.result, encoding="utf-8") as f:
        result = MotionResult.from_dict(json.load(f))
    try:
        slide = foot_slide(result, args.left_foot, args.right_foot)
    except KeyError as exc:
        print(f"error: no joint named {exc.args[0]!r} in skeleton {result.skeleton.name!r}", file=sys.stderr)
        return 1
    print(f"{len(result.frames)} frames @ {result.fps} fps, backend {result.backend}")
    print(f"foot slide while planted: mean {slide['mean_cm_s']} cm/s, max {slide['max_cm_s']} cm/s "
          f"over {slide['contact_frames']} contact frame(s)")
    return 0


def _serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="sovereign-motion")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="run one request and write .json + .bvh")
    gen.add_argument("request", help="path to a motion request JSON file")
    gen.add_argument("--out", default="out/motion", help="output path without extension")
    gen.add_argument("--backend", help="override the request's backend")
    gen.set_defaults(func=_generate)

    srv = sub.add_parser("serve", help="run the local HTTP service for the Unreal plugin")
    srv.add_argument("--port", type=int, default=8765)
    srv.set_defaults(func=_serve)

    lst = sub.add_parser("backends", help="list backends and whether they can run here")
    lst.set_defaults(func=_backends)

    ev = sub.add_parser("evaluate", help="report quality metrics (foot slide) for a result .json")
    ev.add_argument("result", help="path to a motion result JSON file")
    ev.add_argument("--left-foot", default="foot_l")
    ev.add_argument("--right-foot", default="foot_r")
    ev.set_defaults(func=_evaluate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
