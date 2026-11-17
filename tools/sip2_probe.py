#!/usr/bin/env python3
"""Read-only SIP2 check against Koha: login, SC status, and item lookups.

Never checks anything out or in, so it is safe to run against real data.

    python3 tools/sip2_probe.py --host debian.mshome.net 33228100096617 3322810009661

For each barcode it tries the exact value first, then the repaired forms from
koha_rfid.barcode.lookup_candidates, the same way the middleware should.
Works on Windows too (Python 3.8+, no packages needed).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from koha_rfid import sip2  # noqa: E402
from koha_rfid.barcode import classify, lookup_candidates  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("barcodes", nargs="*")
    ap.add_argument("--host", default="debian.mshome.net")
    ap.add_argument("--port", type=int, default=6002)
    ap.add_argument("--user", default="sipterm1")
    ap.add_argument("--institution", default="MIDDL", help="AO / CP value")
    ap.add_argument("--no-checksum", action="store_true", help="send without AY/AZ")
    ap.add_argument("-v", "--verbose", action="store_true", help="print raw messages")
    a = ap.parse_args()
    pw = sip2.load_password()
    if not pw:
        ap.error("no SIP password: put it in .sip_password in the repo root, or set SIP_PASSWORD")

    c = sip2.Client(a.host, a.port, error_detection=not a.no_checksum)

    def ask(msg):
        r = c.send(msg)
        if a.verbose:
            print(f"   >> {msg.replace(pw, '****')}\n   << {r}")
        return sip2.parse(r)

    _, fixed, _ = ask(sip2.login(a.user, pw, a.institution))
    print(f"login: {'OK' if fixed == '1' else 'FAILED'}")
    if fixed != "1":
        return 1
    _, fixed, f = ask(sip2.sc_status())
    print(f"status: online={fixed[0]} checkin={fixed[1]} checkout={fixed[2]} "
          f"institution={f.get('AO', ['?'])[0]}")

    for raw in a.barcodes:
        print(f"\nbarcode {raw!r} ({classify(raw).kind})")
        for n, cand in enumerate(lookup_candidates(raw)):
            _, fixed, f = ask(sip2.item_information(a.institution, cand))
            if sip2.item_found(f):
                status = sip2.CIRCULATION_STATUS.get(fixed[:2], fixed[:2])
                how = "exact" if n == 0 else "repaired form"
                print(f"  FOUND as {cand!r} ({how}): {f['AJ'][0].strip()!r}, {status}, "
                      f"branch {f.get('AQ', ['?'])[0]}")
                break
            print(f"  not found as {cand!r}")
        else:
            print("  -> not in Koha under any form")
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
