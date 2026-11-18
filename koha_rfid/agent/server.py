"""The desk agent's local HTTP API, used by the Koha plugin's JavaScript.

Listens on 127.0.0.1 only. Browsers send an Origin header with every
cross-origin request; only the Koha staff origin(s) in the config are
answered, so other web pages can't read the pad or print labels.

    GET  /status   agent version, reader and printer state, current settings
    GET  /pad      library tags on the pad now: [{epc, barcode, candidates, ...}]
    POST /print    {"items": [{"barcode": "...", "title": "..."}]} -> read-back results
    POST /config   {"reader": {...}, "printer": {...}} -> saved to the INI file and
                   applied at once (the reader reconnects); see config.EDITABLE
    POST /restart  {} -> drop and reconnect the reader
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional, Tuple

from .. import __version__, labels
from . import config
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
        self._reader_factory = reader_factory      # tests: a fixed reader, kept across apply()
        self.pad = PadState(cfg.pad_seconds)
        self.runner = ReaderRunner(reader_factory or make_reader_factory(cfg), self.pad,
                                   cfg.retry_seconds)
        self.print_lock = threading.Lock()
        self._apply_lock = threading.Lock()
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
        cfg, pad = self.cfg, self.pad
        reader = {"connected": pad.connected, "error": pad.error,
                  "since": round(pad.state_seconds()),
                  "source": "fake" if cfg.fake_epcs else (cfg.reader_host or None)}
        printer = {"configured": bool(cfg.printer_host), "host": cfg.printer_host or None}
        return {"agent": "koha-rfid", "version": __version__, "reader": reader, "printer": printer,
                "pad_seconds": cfg.pad_seconds,
                "config": config.editable_values(cfg),
                # Shown on the Koha page, but only changeable in the INI file.
                "config_path": cfg.path or None, "listen_port": cfg.listen_port,
                "allowed_origins": list(cfg.allowed_origins),
                "editable": bool(cfg.path)}

    def update_config(self, body: object) -> Tuple[int, dict]:
        """Save reader/printer settings from Koha to the INI file and apply them."""
        path = self.cfg.path
        if not path:
            return 409, {"error": "this agent was started without a config file, "
                                  "so there is nowhere to save settings"}
        try:
            changes = config.validate_changes(body)
        except config.ConfigError as e:
            return 400, {"error": e.message, "field": e.field}
        try:
            with open(path, "rb") as fh:
                before = fh.read()
            config.save_changes(path, changes)
            try:
                new_cfg = config.load(path)
            except (ValueError, KeyError) as e:   # the file had another bad line: undo
                with open(path, "wb") as fh:
                    fh.write(before)
                return 500, {"error": f"{path} doesn't load after the change ({e}); left as it was"}
        except OSError as e:
            return 500, {"error": f"couldn't save {path}: {e}"}
        log.info("settings changed from Koha: %s",
                 ", ".join(f"{s}.{k}={v or '(empty)'}" for (s, k), v in changes.items()))
        self.apply(new_cfg)
        return 200, self.status()

    def restart_reader(self) -> dict:
        log.info("reader restart requested from Koha")
        self.apply(self.cfg)
        return self.status()

    def apply(self, cfg: AgentConfig) -> None:
        """Switch to new settings without stopping the HTTP server: the reader
        thread is replaced, so the reader disconnects and connects again."""
        # The listening socket and the CORS check were set up at start; keep
        # reporting what is actually in force.
        cfg = dataclasses.replace(cfg, listen_port=self.cfg.listen_port,
                                  allowed_origins=self.cfg.allowed_origins,
                                  log_file=self.cfg.log_file)
        with self._apply_lock, self.print_lock:     # let a print in progress finish
            old = self.runner
            old.stop()
            if old.is_alive():
                old.join(timeout=20)                # frees the R220 (one connection only)
                if old.is_alive():
                    log.warning("old reader connection still closing; the new one will retry")
            self.cfg = cfg
            # A fresh pad: the old thread's last updates can't touch the new state.
            self.pad = PadState(cfg.pad_seconds)
            self.runner = ReaderRunner(self._reader_factory or make_reader_factory(cfg), self.pad,
                                       cfg.retry_seconds, check_seconds=old.check_seconds)
            self.runner.start()

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
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            # Read the body before any refusal: closing a socket with unread data
            # makes Windows reset the connection, and the client never sees the reply.
            raw = self.rfile.read(n) if 0 < n <= MAX_BODY else b""
            if not self._origin_ok():
                return self._send(403, {"error": "origin not allowed"})
            path = self.path.split("?", 1)[0]
            if path not in ("/print", "/config", "/restart"):
                return self._send(404, {"error": "not found"})
            # JSON only: forces a CORS preflight, so other sites can't post blindly.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._send(415, {"error": "Content-Type must be application/json"})
            if not raw:
                self.close_connection = True       # oversized: don't read the rest
                return self._send(400, {"error": "missing or oversized body"})
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._send(400, {"error": "body is not JSON"})
            if path == "/print":
                code, payload = agent.print_items(body)
            elif path == "/config":
                code, payload = agent.update_config(body)
            else:
                code, payload = 200, agent.restart_reader()
            self._send(code, payload)

    return Handler
