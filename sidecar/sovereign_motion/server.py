"""Local HTTP service the Unreal plugin talks to.

Endpoints (JSON in and out):
    GET  /health    -> {"status": "ok", "schema_version": "1.0"}
    GET  /backends  -> [{"name", "available", "reason"}]
    POST /generate  -> motion result (body: motion request)

Binds to localhost only: it runs arbitrary model code and has no auth.
"""

from __future__ import annotations

import json
import logging
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import SCHEMA_VERSION
from .backends import BackendUnavailable, backend_names, get_backend
from .pipeline import run_json
from .request import RequestError

DEFAULT_PORT = 8765
MAX_BODY_BYTES = 10 * 1024 * 1024

log = logging.getLogger("sovereign_motion.server")


class Handler(BaseHTTPRequestHandler):
    server_version = "SovereignMotion/0.0"

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        if self.path == "/health":
            self._send(HTTPStatus.OK, {"status": "ok", "schema_version": SCHEMA_VERSION})
        elif self.path == "/backends":
            listing = []
            for name in backend_names():
                ok, reason = get_backend(name).available()
                listing.append({"name": name, "available": ok, "reason": reason})
            self._send(HTTPStatus.OK, listing)
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no route {self.path}")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/generate":
            self._error(HTTPStatus.NOT_FOUND, f"no route {self.path}")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._error(HTTPStatus.BAD_REQUEST, "request body missing or too large")
            return
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            self._error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}")
            return

        started = time.perf_counter()
        try:
            result = run_json(data)
        except RequestError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except KeyError as exc:  # unknown backend
            self._error(HTTPStatus.BAD_REQUEST, str(exc.args[0]) if exc.args else "unknown key")
            return
        except BackendUnavailable as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        except Exception:  # keep the service alive and tell the editor something went wrong
            log.exception("generation failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "generation failed; see sidecar log")
            return
        result.diagnostics["generate_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        self._send(HTTPStatus.OK, result.to_dict())

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(port: int = DEFAULT_PORT) -> None:
    server = make_server(port)
    log.info("Sovereign Motion sidecar listening on http://127.0.0.1:%d", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
