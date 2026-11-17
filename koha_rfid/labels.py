"""RFID item labels for the Zebra ZD500R: ZPL that writes the EPC, reads it back,
and prints the Codabar barcode, the number and the title.

Port of testing/print_rfid_labels.ps1 (same ZPL, byte for byte, for a label
with a title). Layout: 203 dpi, label at least 50 x 25 mm. The desk agent's
"Print RFID label" uses print_labels(); for bulk printing:

    python -m koha_rfid.labels --csv fieldtest\\test_books.csv --printer 192.168.1.60
    python -m koha_rfid.labels --barcode 33228016794891 --title "The dog in the manger"

Without --printer the ZPL is printed to the console (or --out FILE).
"""

from __future__ import annotations

import argparse
import csv
import re
import socket
import sys
import time
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .barcode import is_valid_standard
from .epc import encode_epc

TITLE_MAX = 32
EOL = "\r\n"


def label_text(text: str, max_len: int = TITLE_MAX) -> str:
    """ASCII-only text for ZPL's default font: accents folded, ^ and ~ removed,
    trailing catalogue punctuation ("Title /") dropped, cut to max_len."""
    t = "".join(c for c in unicodedata.normalize("NFD", text or "")
                if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^\x20-\x7E]", "", t)
    t = re.sub(r"\s+", " ", re.sub(r"[\^~]", "", t)).strip()
    t = t.rstrip(" /:;,=")
    if len(t) > max_len:
        t = t[:max_len - 1].rstrip() + "."
    return t


def human_readable(barcode: str) -> str:
    """'3 3228 01679489 1' for a standard barcode, otherwise unchanged."""
    if is_valid_standard(barcode):
        b = barcode
        return f"{b[0]} {b[1:5]} {b[5:13]} {b[13]}"
    return barcode


def label_zpl(barcode: str, title: str = "") -> str:
    """ZPL for one label. Raises ValueError for barcodes the tag encoding can't hold."""
    epc = encode_epc(barcode)          # numeric, 1-22 digits, 96-bit
    line3 = label_text(title)
    lines = [
        "^XA",
        "^PW400^LL200^LH0,0",
        f"^RFW,H^FD{epc}^FS",                       # write the EPC
        "^FN1^RFR,H^FS^HV1,,EPC:^FS",               # read it back, send "EPC:<hex>" to the host
        f"^FO20,15^BY2,2.5,80^BKN,N,80,N,N,A,A^FD{barcode}^FS",   # Codabar, no added check digit
        f"^FO20,105^A0N,28,28^FD{human_readable(barcode)}^FS",
    ]
    if line3:
        lines.append(f"^FO20,140^A0N,22,22^FD{line3}^FS")
    lines += [f"^FO20,170^A0N,18,18^FDEPC {epc}^FS", "^PQ1", "^XZ"]
    return EOL.join(lines)


def labels_zpl(items: Iterable[Tuple[str, str]]) -> str:
    """ZPL for several (barcode, title) labels."""
    return EOL.join(label_zpl(b, t) for b, t in items)


def parse_readback(reply: str) -> List[str]:
    """EPCs from the printer's read-back, e.g. 'EPC:0E33...EPC:0D33...'.

    Replies arrive back to back with no separator, and matching hex greedily
    would swallow the 'E' of the next 'EPC:', so split on the header.
    """
    return [re.match(r"[0-9A-Fa-f]*", part).group(0).upper()
            for part in reply.split("EPC:")[1:]]


@dataclass(frozen=True)
class PrintResult:
    barcode: str
    written: str
    read_back: str
    status: str          # OK, MISMATCH, NO REPLY

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def print_labels(host: str, items: List[Tuple[str, str]], port: int = 9100,
                 timeout: float = 15.0) -> List[PrintResult]:
    """Send labels to a networked ZD500R (raw port 9100) and check each tag's read-back."""
    zpl = labels_zpl(items)              # validates every barcode before anything is sent
    reply = ""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(zpl.encode("ascii"))
        deadline = time.time() + timeout
        while reply.count("EPC:") < len(items) and time.time() < deadline:
            s.settimeout(max(deadline - time.time(), 0.1))
            try:
                chunk = s.recv(1024)
            except socket.timeout:
                break
            if not chunk:
                break
            reply += chunk.decode("ascii", "replace")
    got = parse_readback(reply)
    out = []
    for i, (barcode, _) in enumerate(items):
        written = encode_epc(barcode)
        if i >= len(got):
            out.append(PrintResult(barcode, written, "", "NO REPLY"))
        else:
            out.append(PrintResult(barcode, written, got[i],
                                   "OK" if got[i].startswith(written) else "MISMATCH"))
    return out


def _read_csv(path: str) -> List[Tuple[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [(r["barcode"].strip(), r.get("title", "") or "")
                for r in csv.DictReader(fh) if (r.get("barcode") or "").strip()]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--barcode", action="append", default=[], help="repeatable, or comma-separated")
    ap.add_argument("--title", default="", help="title for a single --barcode")
    ap.add_argument("--csv", help="CSV with barcode,title columns (tools: check_test_books.py)")
    ap.add_argument("--printer", help="printer IP (raw port 9100); omit to just show the ZPL")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--out", help="write the ZPL to this file")
    a = ap.parse_args(argv)

    items = [(b.strip(), a.title) for arg in a.barcode for b in arg.split(",") if b.strip()]
    if a.csv:
        items += _read_csv(a.csv)
    if not items:
        ap.error("give --barcode or --csv")
    try:
        zpl = labels_zpl(items)
    except ValueError as e:
        raise SystemExit(f"Can't encode: {e}")
    for b, _ in items:
        if not is_valid_standard(b):
            print(f"note: {b} is not a standard-14 barcode; writing it exactly as given",
                  file=sys.stderr)
    if a.out:
        with open(a.out, "w", encoding="ascii", newline="") as fh:
            fh.write(zpl)
        print(f"Wrote {len(items)} label(s) to {a.out}")
    if a.printer:
        results = print_labels(a.printer, items, a.port)
        for r in results:
            print(f"{r.status:9} {r.barcode}  written {r.written}  read back {r.read_back or '-'}")
        return 0 if all(r.ok for r in results) else 1
    if not a.out:
        print(zpl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
