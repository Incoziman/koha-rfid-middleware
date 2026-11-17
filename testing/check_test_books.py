#!/usr/bin/env python3
"""Check real books against Koha before printing RFID tags for them. Read-only.

Give it item barcodes from any of these:
  - typed or scanned off the books:   check_test_books.py 33228012345678 3322801234567
  - a text file, one barcode per line: check_test_books.py --file scanned.txt
  - a Koha report export (CSV/TSV with a "barcode" column; other columns such as
    callnumber and location are carried through to the output):
                                       check_test_books.py --file report.csv

Each barcode is looked up with SIP 17 (exact, then repaired forms, as the
middleware does). Books that can go on a test tag are written to --out, a CSV
that testing/print_rfid_labels.ps1 -CsvFile prints from. The tag gets the barcode
exactly as Koha stores it, so the first lookup at check-out is an exact hit.

    python testing\\check_test_books.py --file report.csv --out test_books.csv
"""
import argparse
import csv
import io
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from koha_rfid import sip2  # noqa: E402
from koha_rfid.barcode import classify  # noqa: E402

AVAILABLE = "03"
MAX_DIGITS = 22   # length-prefixed encoding on a 96-bit tag


def read_rows(path):
    """[{barcode: ..., other columns...}] from a plain list or a CSV/TSV export."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    if "barcode" not in lines[0].lower():
        # plain list, or report rows pasted without a header: first column only
        return [{"barcode": re.split(r"[\t;,]", l)[0].strip()} for l in lines]
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for r in csv.DictReader(io.StringIO(text), dialect=dialect):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if r.get("barcode"):
            rows.append(r)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("barcodes", nargs="*")
    ap.add_argument("--file", help="barcode list, or Koha report export with a 'barcode' column")
    ap.add_argument("--out", default="test_books.csv", help="CSV for print_rfid_labels.ps1 -CsvFile")
    ap.add_argument("--include-unavailable", action="store_true",
                    help="also keep books Koha shows as on loan, in transit, etc.")
    ap.add_argument("--sip-host", default="debian.mshome.net")
    ap.add_argument("--sip-port", type=int, default=6002)
    ap.add_argument("--user", default="sipterm1")
    ap.add_argument("--institution", default="MIDDL")
    a = ap.parse_args()
    pw = sip2.load_password()
    if not pw:
        ap.error("no SIP password: put it in .sip_password in the repo root, or set SIP_PASSWORD")

    rows = [{"barcode": b} for b in a.barcodes]
    if a.file:
        rows += read_rows(a.file)
    if not rows:
        ap.error("give barcodes or --file")

    try:
        sip = sip2.Session(a.sip_host, a.sip_port, a.user, pw, a.institution)
    except PermissionError as e:
        raise SystemExit(str(e))
    except OSError as e:
        raise SystemExit(f"Can't reach Koha's SIP server at {a.sip_host}:{a.sip_port} ({e}). "
                         "Nothing was sent.")
    keep, seen = [], set()
    print(f"{'barcode':16} {'result':10} title / reason")
    for r in rows:
        given = r["barcode"]
        if given in seen:
            continue
        seen.add(given)
        item = sip2.lookup_item(sip, a.institution, given)
        if item is None:
            print(f"{given:16} {'SKIP':10} not in Koha (the test copy is from 2026-09-14)")
            continue
        bc = item.barcode
        if bc in seen and bc != given:
            print(f"{given:16} {'DUPLICATE':10} same book as {bc}, already listed")
            continue
        seen.add(bc)
        if not bc.isdigit() or len(bc) > MAX_DIGITS:
            print(f"{given:16} {'SKIP':10} {item.title[:40]!r}: Koha barcode {bc!r} "
                  f"can't use the numeric tag encoding")
            continue
        if item.circulation_status != AVAILABLE and not a.include_unavailable:
            print(f"{given:16} {'SKIP':10} {item.title[:40]!r}: {item.status_text} in Koha "
                  f"(--include-unavailable to keep)")
            continue
        note = f"  (Koha has it as {bc})" if item.repaired else ""
        print(f"{given:16} {'OK':10} {item.title[:50]!r} [{classify(bc).kind}]{note}")
        out = {"barcode": bc, "title": item.title, "status": item.status_text,
               "scanned_as": given}
        for k in ("author", "callnumber", "itemcallnumber", "location", "ccode", "itype"):
            if r.get(k):
                out[k] = r[k]
        keep.append(out)
    sip.close()

    if not keep:
        print("\nNo usable books - nothing written.")
        return 1
    cols = ["barcode", "title", "status", "scanned_as"]
    cols += sorted({k for r in keep for k in r} - set(cols))
    with open(a.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(keep)
    print(f"\n{len(keep)} book(s) written to {os.path.abspath(a.out)}")
    print(f"Next: powershell -ExecutionPolicy Bypass -File testing\\print_rfid_labels.ps1 "
          f"-CsvFile {a.out} -PrinterHost <printer IP>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
