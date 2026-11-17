"""Desk agent settings, from an INI file (deploy/agent.example.ini)."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class AgentConfig:
    listen_port: int = 8765
    allowed_origins: Tuple[str, ...] = ()    # Koha staff interface origin(s)
    log_file: str = ""

    reader_host: str = ""
    reader_port: int = 5084
    antennas: Tuple[int, ...] = (1,)
    power: int = 0                  # index into the R220 power table, 0 = max
    session: int = 0                # 0: tags answer on every round, so presence is live
    pad_seconds: float = 2.0        # a tag counts as on the pad this long after its last read
    retry_seconds: float = 10.0     # wait between reader reconnect attempts
    keepalive_ms: int = 10_000
    fake_epcs: List[str] = field(default_factory=list)   # no reader: pretend these are on the pad

    printer_host: str = ""
    printer_port: int = 9100


def _list(value: str) -> List[str]:
    return [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]


def load(path: Optional[str]) -> AgentConfig:
    """Read the INI file. Missing keys keep their defaults; a missing file is an error."""
    c = AgentConfig()
    if not path:
        return c
    if not os.path.exists(path):
        raise FileNotFoundError(f"agent config not found: {path}")
    ini = configparser.ConfigParser()
    ini.read(path, encoding="utf-8")
    a = ini["agent"] if ini.has_section("agent") else {}
    r = ini["reader"] if ini.has_section("reader") else {}
    p = ini["printer"] if ini.has_section("printer") else {}

    c.listen_port = int(a.get("listen_port", c.listen_port))
    c.allowed_origins = tuple(o.rstrip("/") for o in _list(a.get("allowed_origins", "")))
    log_file = a.get("log_file", "").strip()
    if log_file and not os.path.isabs(log_file):
        log_file = os.path.join(os.path.dirname(os.path.abspath(path)), log_file)
    c.log_file = log_file

    c.reader_host = r.get("host", "").strip()
    c.reader_port = int(r.get("port", c.reader_port))
    c.antennas = tuple(int(x) for x in _list(r.get("antennas", "1")))
    c.power = int(r.get("power", c.power))
    c.session = int(r.get("session", c.session))
    c.pad_seconds = float(r.get("pad_seconds", c.pad_seconds))
    c.retry_seconds = float(r.get("retry_seconds", c.retry_seconds))
    c.keepalive_ms = int(r.get("keepalive_ms", c.keepalive_ms))
    c.fake_epcs = [e.upper() for e in _list(r.get("fake_epcs", ""))]

    c.printer_host = p.get("host", "").strip()
    c.printer_port = int(p.get("port", c.printer_port))
    return c
