"""Minimal SIP2 client helpers, tested against Koha 21.11's SIP server.

Only what the RFID middleware needs: building messages with sequence numbers
and checksums (AY/AZ), and splitting replies into their fixed part and
variable fields. docs/sip2-koha.md describes every message used here.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Length of the fixed-width part (after the 2-char command) of each reply
# the middleware reads.
FIXED_LEN = {
    "94": 1,    # login: ok
    "98": 34,   # ACS status
    "18": 24,   # item information
    "12": 22,   # checkout
    "10": 22,   # checkin
    "24": 35,   # patron status
    "64": 61,   # patron information
    "96": 0,    # resend request
}

CIRCULATION_STATUS = {
    "01": "other", "02": "on order", "03": "available", "04": "charged (on loan)",
    "05": "charged; not to be recalled until earliest recall date", "06": "in process",
    "07": "recalled", "08": "waiting on hold shelf", "09": "waiting to be re-shelved",
    "10": "in transit", "11": "claimed returned", "12": "lost", "13": "missing",
}


def checksum(msg: str) -> str:
    """SIP2 checksum: two's complement of the byte sum of everything up to and including 'AZ'."""
    return "%04X" % (-sum(msg.encode("ascii")) & 0xFFFF)


def with_error_detection(msg: str, seq: int) -> str:
    """Append AY<seq>AZ<checksum>. seq is 0-9 and should increase per message."""
    m = f"{msg}AY{seq % 10}AZ"
    return m + checksum(m)


def verify(reply: str) -> bool:
    """True if a reply has no checksum or a correct one."""
    if "AZ" not in reply:
        return True
    body, got = reply.rsplit("AZ", 1)
    return checksum(body + "AZ") == got[:4]


def timestamp(t: Optional[float] = None) -> str:
    """SIP2 date: YYYYMMDDZZZZHHMMSS, with four spaces for local time zone."""
    return time.strftime("%Y%m%d    %H%M%S", time.localtime(t))


def parse(reply: str) -> Tuple[str, str, Dict[str, List[str]]]:
    """Split a reply into (command, fixed part, {field id: [values]})."""
    reply = reply.rstrip("\r\n")
    cmd = reply[:2]
    n = FIXED_LEN.get(cmd, 0)
    fixed, rest = reply[2:2 + n], reply[2 + n:]
    fields: Dict[str, List[str]] = {}
    # AY/AZ are not |-terminated; peel them off the end first.
    if "AZ" in rest and rest.rfind("AZ") >= len(rest) - 6:
        rest, az = rest[:rest.rfind("AZ")], rest[rest.rfind("AZ") + 2:]
        fields["AZ"] = [az]
        if rest[-3:-1] == "AY":
            fields["AY"] = [rest[-1]]
            rest = rest[:-3]
    for part in rest.split("|"):
        if len(part) >= 2:
            fields.setdefault(part[:2], []).append(part[2:])
    return cmd, fixed, fields


def login(user: str, password: str, location: str) -> str:
    return f"9300CN{user}|CO{password}|CP{location}|"


def sc_status() -> str:
    return "9900302.00"   # status ok, print width 030, protocol 2.00


def item_information(institution: str, barcode: str) -> str:
    return f"17{timestamp()}AO{institution}|AB{barcode}|AC|"


def patron_status(institution: str, card: str, pin: str = "") -> str:
    return f"23000{timestamp()}AO{institution}|AA{card}|AC|AD{pin}|"


def checkout(institution: str, card: str, barcode: str, pin: str = "") -> str:
    # renewal policy N, no-block N, transaction date, no-block due date (blank)
    return f"11NN{timestamp()}{' ' * 18}AO{institution}|AA{card}|AB{barcode}|AC|AD{pin}|"


def checkin(institution: str, location: str, barcode: str) -> str:
    # no-block N, transaction date, return date
    ts = timestamp()
    return f"09N{ts}{ts}AP{location}|AO{institution}|AB{barcode}|AC|"


def item_found(fields: Dict[str, List[str]]) -> bool:
    """Koha answers an unknown barcode with an 18 reply whose title (AJ) is empty."""
    return bool(fields.get("AJ", [""])[0].strip())


@dataclass(frozen=True)
class ItemInfo:
    """What Koha said about one item, from a 18 reply."""
    tag_barcode: str        # the barcode as read from the tag
    barcode: str            # the form Koha recognised: send this in 11 / 09
    title: str
    circulation_status: str  # 2-digit code, see CIRCULATION_STATUS
    permanent_location: str  # AQ
    current_location: str    # AP (may be empty)

    @property
    def repaired(self) -> bool:
        return self.barcode != self.tag_barcode.strip()

    @property
    def status_text(self) -> str:
        return CIRCULATION_STATUS.get(self.circulation_status, self.circulation_status)


def lookup_item(client: "Client", institution: str, barcode: str) -> Optional[ItemInfo]:
    """17 item information for the exact barcode, then its repaired forms.

    Returns the first hit, or None if Koha knows none of them.
    """
    from .barcode import lookup_candidates  # local import: keep sip2 usable on its own
    for cand in lookup_candidates(barcode):
        _, fixed, f = parse(client.send(item_information(institution, cand)))
        if item_found(f):
            return ItemInfo(
                tag_barcode=barcode, barcode=cand, title=f["AJ"][0].strip(),
                circulation_status=fixed[:2],
                permanent_location=f.get("AQ", [""])[0],
                current_location=f.get("AP", [""])[0],
            )
    return None


class Client:
    """One SIP2 connection. Not thread-safe; use one per self-check station."""

    def __init__(self, host: str, port: int, timeout: float = 10.0, error_detection: bool = True):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.error_detection = error_detection
        self.seq = 0

    def send(self, msg: str, retries: int = 1) -> str:
        out = with_error_detection(msg, self.seq) if self.error_detection else msg
        self.seq = (self.seq + 1) % 10
        self.sock.sendall((out + "\r").encode("ascii"))
        reply = self._read()
        if reply.startswith("96") and retries > 0:   # server asks us to resend
            return self.send(msg, retries - 1)
        if not verify(reply):
            raise IOError(f"bad checksum in reply: {reply!r}")
        return reply

    def _read(self) -> str:
        buf = b""
        while not buf.endswith(b"\r"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("SIP server closed the connection")
            buf += chunk
        # Koha may send a stray LF before the next message; strip it.
        return buf.decode("ascii", "replace").strip("\r\n")

    def close(self) -> None:
        self.sock.close()
