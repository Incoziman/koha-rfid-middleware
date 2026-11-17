"""The desk agent's local HTTP API, used by the Koha plugin's JavaScript.

Listens on 127.0.0.1 only. Browsers send an Origin header with every
cross-origin request; only the Koha staff origin(s) in the config are
answered, so other web pages can't read the pad or print labels.

    GET  /status   agent version, reader and printer state
    GET  /pad      library tags on the pad now: [{epc, barcode, candidates, ...}]
    POST /print    {"items": [{"barcode": "...", "title": "..."}]} -> read-back results
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Tuple

from .. import __version__, labels
from .config import AgentConfig
from .pad import PadState, ReaderRunner, make_reader_factory

log = logging.getLogger("koha_rfid.agent")
MAX_BODY = 64 * 1024
MAX_LABELS = 50


class Agent:
    """Reader runner + HTTP server. start() returns once the server is listening."""

    def __init__(self, cfg: AgentConfig, reader_factory: Optional[Callable] = None,
                 host: str = "127.0.0.1"):
        self.cfg = cfg
        self.pad = PadState(cfg.pad_seconds)
        self.runner = ReaderRunner(reader_factory or make_reader_factory(cfg), self.pad,
                                   cfg.retry_seconds)
        self.print_lock = threading.Lock()
        self.httpd = ThreadingHTTPServer((host, cfg.listen_port), _make_handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.runner.start()
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True)
        self._thread.start()
        log.info("agent %s listening on http://127.0.0.1:%s, allowed origins: %s",
                 __version__, self.port, ", ".join(self.cfg.allowed_origins) or "(none)")

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.runner.stop()
        self.runner.join(timeout=10)

    # ---- request handling, independent of HTTP plumbing -----------------------
    def status(self) -> dict:
        reader = {"connected": self.pad.connected, "error": self.pad.error,
                  "source": "fake" if self.cfg.fake_epcs else (self.cfg.reader_host or None)}
        printer = {"configured": bool(self.cfg.printer_host), "host": self.cfg.printer_host or None}
        return {"agent": "koha-rfid", "version": __version__, "reader": reader, "printer": printer,
                "pad_seconds": self.cfg.pad_seconds}

    def pad_contents(self) -> dict:
        snap = self.pad.snapshot()
        snap["reader_connected"] = self.pad.connected
        return snap

    def print_items(self, body: dict) -> Tuple[int, dict]:
        if not self.cfg.printer_host:
            return 503, {"error": "no label printer configured on this desk"}
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list) or not items or len(items) > MAX_LABELS:
            return 400, {"error": f"'items' must be a list of 1-{MAX_LABELS} labels"}
        pairs = []
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("barcode"), str):
                return 400, {"error": "each item needs a 'barcode' string"}
            pairs.append((it["barcode"].strip(), str(it.get("title") or "")))
        try:
            labels.labels_zpl(pairs)        # validate everything before printing anything
        except ValueError as e:
            return 400, {"error": f"can't encode on a tag: {e}"}
        with self.print_lock:
            try:
                results = labels.print_labels(self.cfg.printer_host, pairs, self.cfg.printer_port)
            except OSError as e:
                return 502, {"error": f"printer {self.cfg.printer_host} not reachable: {e}"}
        log.info("printed %s", ", ".join(f"{r.barcode}={r.status}" for r in results))
        return 200, {"results": [r.__dict__ for r in results]}


def _make_handler(agent: Agent):
    allowed = set(agent.cfg.allowed_origins)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"koha-rfid-agent/{__version__}"

        def log_message(self, fmt, *args):   # route through logging, quietly
            log.debug("%s - %s", self.address_string(), fmt % args)

        # -- helpers --
        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            # No Origin: not a cross-origin browser request (curl, local scripts).
            return origin is None or origin.rstrip("/") in allowed

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if origin and origin.rstrip("/") in allowed:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # -- methods --
        def do_OPTIONS(self):
            if not self._origin_ok():
                return self._send(403, {"error": "origin not allowed"})
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Chrome's Private Network Access: an https Koha page may call localhost.
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._origin_ok():
                return self._send(403, {"error": "origin not allowed"})
            path = self.path.split("?", 1)[0]
            if path == "/status":
                return self._send(200, agent.status())
            if path == "/pad":
                return self._send(200, agent.pad_contents())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._origin_ok():
                return self._send(403, {"error": "origin not allowed"})
            if self.path.split("?", 1)[0] != "/print":
                return self._send(404, {"error": "not found"})
            # JSON only: forces a CORS preflight, so other sites can't post blindly.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._send(415, {"error": "Content-Type must be application/json"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(400, {"error": "missing or oversized body"})
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "body is not JSON"})
            code, payload = agent.print_items(body)
            self._send(code, payload)

    return Handler
