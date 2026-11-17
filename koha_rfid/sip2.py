"""Minimal SIP2 client helpers, tested against Koha 21.11's SIP server.

Only what the RFID middleware needs: building messages with sequence numbers
and checksums (AY/AZ), and splitting replies into their fixed part and
variable fields. docs/sip2-koha.md describes every message used here.
"""

from __future__ import annotations

import os
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


def _f(fields: Dict[str, List[str]], key: str) -> str:
    return fields.get(key, [""])[0].strip()


@dataclass(frozen=True)
class PatronStatus:
    """From a 24 reply. Koha enforces its own rules at check-out; this is the pre-check."""
    valid: bool             # BL = Y
    pin_ok: Optional[bool]  # CQ = Y/N, None if Koha didn't say
    flags: str              # 14-char patron status; 'Y' = that privilege denied
    name: str               # AE
    message: str            # AF

    @property
    def charge_denied(self) -> bool:
        return self.flags[:1] == "Y"

    def refusal(self, pin_required: bool) -> Optional[str]:
        """Why this patron can't check out, or None if they can."""
        if not self.valid:
            return self.message or "not a valid patron"
        if pin_required and self.pin_ok is not True:
            return "wrong PIN"
        if self.charge_denied:
            return self.message or "check-out privileges denied"
        return None


def get_patron_status(client: "Client", institution: str, card: str, pin: str = "") -> PatronStatus:
    _, fixed, f = parse(client.send(patron_status(institution, card, pin)))
    cq = _f(f, "CQ")
    return PatronStatus(
        valid=_f(f, "BL") == "Y",
        pin_ok=None if not cq else cq == "Y",
        flags=fixed[:14], name=_f(f, "AE"), message=_f(f, "AF"))


@dataclass(frozen=True)
class CircResult:
    """From a 12 (check-out) or 10 (check-in) reply."""
    ok: bool
    security: str      # 12: desensitize, 10: resensitize (Y/N/U)
    alert: bool        # 10 only: item needs staff attention
    alert_type: str    # CV, 10 only, e.g. 01 = hold for another patron, 04 = send to other branch
    due_date: str      # AH, 12 only
    message: str       # AF
    raw: str           # the full reply, for the transaction log


def _send_write(client, msg: str) -> str:
    # A Session would retry on a dropped connection; a write must never be resent.
    return getattr(client, "send_once", client.send)(msg)


def do_checkout(client: "Client", institution: str, card: str, barcode: str,
                pin: str = "") -> CircResult:
    """11 check-out. Send the barcode as Koha recognised it in lookup_item()."""
    reply = _send_write(client, checkout(institution, card, barcode, pin))
    cmd, fixed, f = parse(reply)
    if cmd != "12":
        raise IOError(f"expected a 12 reply, got {reply!r}")
    return CircResult(ok=fixed[:1] == "1", security=fixed[3:4], alert=False, alert_type="",
                      due_date=_f(f, "AH"), message=_f(f, "AF"), raw=reply)


def do_checkin(client: "Client", institution: str, location: str, barcode: str) -> CircResult:
    """09 check-in. Send the barcode as Koha recognised it in lookup_item()."""
    reply = _send_write(client, checkin(institution, location, barcode))
    cmd, fixed, f = parse(reply)
    if cmd != "10":
        raise IOError(f"expected a 10 reply, got {reply!r}")
    return CircResult(ok=fixed[:1] == "1", security=fixed[1:2], alert=fixed[3:4] == "Y",
                      alert_type=_f(f, "CV"), due_date="", message=_f(f, "AF"), raw=reply)


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


class Session:
    """A logged-in Client that keeps itself alive and reconnects after a drop.

    send() retries once on a lost connection, so use it only for read-only
    messages (17, 23, 99). For 11 / 09 use send_once(): if the connection drops
    mid-transaction we can't know whether Koha applied it, and resending a
    check-out to the same patron would be a renewal.
    """

    KEEPALIVE_S = 240   # docs/sip2-koha.md: repeat 99 every ~5 min

    def __init__(self, host: str, port: int, user: str, password: str, location: str,
                 timeout: float = 10.0):
        self.host, self.port, self.user, self.location = host, port, user, location
        self._pw, self._timeout = password, timeout
        self.client: Optional[Client] = None
        self.last = 0.0
        self.connect()

    def connect(self) -> None:
        self.close()
        self.client = Client(self.host, self.port, timeout=self._timeout)
        _, fixed, _ = parse(self.client.send(login(self.user, self._pw, self.location)))
        if fixed != "1":
            raise PermissionError("SIP login failed - check the user and password")
        self.client.send(sc_status())
        self.last = time.time()

    def send(self, msg: str) -> str:
        try:
            reply = self.client.send(msg)
        except (OSError, ConnectionError):
            self.connect()
            reply = self.client.send(msg)
        self.last = time.time()
        return reply

    def send_once(self, msg: str) -> str:
        """Send without retrying. On a drop, reconnect for next time and re-raise."""
        try:
            reply = self.client.send(msg)
        except (OSError, ConnectionError):
            try:
                self.connect()
            except OSError:
                pass
            raise
        self.last = time.time()
        return reply

    def keepalive(self) -> None:
        if time.time() - self.last > self.KEEPALIVE_S:
            self.send(sc_status())

    def close(self) -> None:
        if self.client:
            try:
                self.client.close()
            except OSError:
                pass
            self.client = None


PASSWORD_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             ".sip_password")


def load_password() -> Optional[str]:
    """SIP password: $SIP_PASSWORD if set, else the first line of .sip_password
    in the repo root (git-ignored, so it never reaches GitHub)."""
    pw = os.environ.get("SIP_PASSWORD")
    if pw:
        return pw
    try:
        with open(PASSWORD_FILE, encoding="utf-8") as fh:
            return fh.readline().strip() or None
    except OSError:
        return None
