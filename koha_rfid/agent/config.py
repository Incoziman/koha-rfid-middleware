"""Desk agent settings, from an INI file (deploy/agent.example.ini).

The reader and printer settings can also be changed from the Koha plugin's
Configure page (POST /config): validate_changes() checks them and
save_changes() writes them back into the INI file, keeping its comments.
"""

from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


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

    path: str = ""                  # the INI file this came from ("" = defaults only)


def _list(value: str) -> List[str]:
    return [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]


def load(path: Optional[str]) -> AgentConfig:
    """Read the INI file. Missing keys keep their defaults; a missing file is an error."""
    c = AgentConfig()
    if not path:
        return c
    if not os.path.exists(path):
        raise FileNotFoundError(f"agent config not found: {path}")
    c.path = os.path.abspath(path)
    ini = configparser.ConfigParser()
    ini.read(path, encoding="utf-8-sig")     # tolerate a BOM from Windows editors
    a = ini["agent"] if ini.has_section("agent") else {}
    r = ini["reader"] if ini.has_section("reader") else {}
    p = ini["printer"] if ini.has_section("printer") else {}

    c.listen_port = int(a.get("listen_port", c.listen_port))
    c.allowed_origins = tuple(o.rstrip("/") for o in _list(a.get("allowed_origins", "")))
    log_file = a.get("log_file", "").strip()
    if log_file and not os.path.isabs(log_file):
        log_file = os.path.join(os.path.dirname(c.path), log_file)
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


# ---- settings the Koha Configure page may change ------------------------------------
# Deliberately not here: listen_port and allowed_origins (a wrong value from the
# browser would lock the page out of the agent), log_file (a path the agent,
# running as SYSTEM, writes to) and fake_epcs (testing only).

_HOST = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$")


class ConfigError(ValueError):
    """A rejected setting. `field` is "section.key", for the page to point at."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _host(v) -> str:
    v = "" if v is None else str(v).strip()
    if v and not _HOST.match(v):
        raise ValueError("must be an IP address or host name, or empty")
    return v


def _int(lo: int, hi: int) -> Callable[[object], str]:
    def check(v) -> str:
        if isinstance(v, bool):
            raise ValueError(f"must be a whole number from {lo} to {hi}")
        try:
            n = int(str(v).strip())
        except ValueError:
            raise ValueError(f"must be a whole number from {lo} to {hi}") from None
        if not lo <= n <= hi:
            raise ValueError(f"must be from {lo} to {hi}")
        return str(n)
    return check


def _seconds(lo: float, hi: float) -> Callable[[object], str]:
    def check(v) -> str:
        if isinstance(v, bool):
            raise ValueError(f"must be a number of seconds from {lo:g} to {hi:g}")
        try:
            x = float(str(v).strip())
        except ValueError:
            raise ValueError(f"must be a number of seconds from {lo:g} to {hi:g}") from None
        if not lo <= x <= hi:
            raise ValueError(f"must be from {lo:g} to {hi:g} seconds")
        return f"{x:g}"
    return check


def _antennas(v) -> str:
    items = v if isinstance(v, (list, tuple)) else _list(str(v or ""))
    try:
        ports = [int(str(x).strip()) for x in items]
    except ValueError:
        raise ValueError("must be antenna numbers 1-4, comma-separated") from None
    if not ports or any(not 1 <= p <= 4 for p in ports) or len(set(ports)) != len(ports):
        raise ValueError("must be antenna numbers 1-4, comma-separated")
    return ", ".join(str(p) for p in ports)


# section -> key -> validator returning the INI text
EDITABLE: Dict[str, Dict[str, Callable[[object], str]]] = {
    "reader": {
        "host": _host,
        "port": _int(1, 65535),
        "antennas": _antennas,
        "power": _int(0, 91),          # R220 power table: 0 = max, 1..91 = 10..32.5 dBm
        "session": _int(0, 3),
        "pad_seconds": _seconds(0.2, 30),
        "retry_seconds": _seconds(1, 300),
    },
    "printer": {
        "host": _host,
        "port": _int(1, 65535),
    },
}


def editable_values(c: AgentConfig) -> Dict[str, Dict[str, object]]:
    """The current values of the EDITABLE settings, shaped like a POST /config body."""
    return {
        "reader": {"host": c.reader_host, "port": c.reader_port, "antennas": list(c.antennas),
                   "power": c.power, "session": c.session, "pad_seconds": c.pad_seconds,
                   "retry_seconds": c.retry_seconds},
        "printer": {"host": c.printer_host, "port": c.printer_port},
    }


def validate_changes(body: object) -> Dict[Tuple[str, str], str]:
    """{"reader": {...}, "printer": {...}} -> {(section, key): INI text}.

    Raises ConfigError for unknown sections or keys and for bad values.
    """
    if not isinstance(body, dict) or not body:
        raise ConfigError("body", "expected {\"reader\": {...}, \"printer\": {...}}")
    out: Dict[Tuple[str, str], str] = {}
    for section, values in body.items():
        if section not in EDITABLE:
            raise ConfigError(str(section), "can't be changed from Koha")
        if not isinstance(values, dict):
            raise ConfigError(section, "expected an object of settings")
        for key, value in values.items():
            check = EDITABLE[section].get(key)
            if check is None:
                raise ConfigError(f"{section}.{key}", "can't be changed from Koha")
            try:
                text = check(value)
            except ValueError as e:
                raise ConfigError(f"{section}.{key}", str(e)) from None
            out[(section, key)] = text
    return out


_SECTION_LINE = re.compile(r"^\s*\[([^\]]+)\]")
_KEY_LINE = re.compile(r"^\s*([^;#\s=:][^=:]*?)\s*[=:]")


def save_changes(path: str, changes: Dict[Tuple[str, str], str]) -> None:
    """Write validated settings into the INI file in place.

    Comments and the order of lines are kept: an existing `key = value` line is
    replaced, a missing key is added at the end of its section, and a missing
    section is added at the end of the file. The file is replaced atomically.
    """
    with open(path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pending = dict(changes)

    section = None
    for i, line in enumerate(lines):
        m = _SECTION_LINE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        m = _KEY_LINE.match(line)
        if m and (section, m.group(1).strip().lower()) in pending:
            key = m.group(1).strip().lower()
            value = pending.pop((section, key))
            lines[i] = f"{key} = {value}" if value else f"{key} ="

    # Keys that weren't in the file: append them to their section.
    by_section: Dict[str, List[str]] = {}
    for (sec, key), value in pending.items():
        by_section.setdefault(sec, []).append(f"{key} = {value}" if value else f"{key} =")
    for sec, new_lines in by_section.items():
        start = next((i for i, l in enumerate(lines)
                      if (m := _SECTION_LINE.match(l)) and m.group(1).strip() == sec), None)
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{sec}]")
            lines.extend(new_lines)
            continue
        end = next((i for i in range(start + 1, len(lines)) if _SECTION_LINE.match(lines[i])),
                   len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = new_lines

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:   # no BOM: configparser
        fh.write(nl.join(lines) + nl)
    os.replace(tmp, path)
