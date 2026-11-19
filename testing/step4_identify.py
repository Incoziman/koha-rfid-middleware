#!/usr/bin/env python3
"""Step 4: reader -> decode -> SIP item info -> print the title. Read-only.

Reads tags from the R220. Each new EPC is decoded to a barcode, and Koha is
asked about it with a SIP 17. Tags that aren't library items are skipped. Never sends
11 or 09, so it can't change circulation data.

    python testing\\step4_identify.py 192.168.1.3 --seconds 30

Needs sllurp==2.0.1 (pip install sllurp==2.0.1). Each tag is looked up once
per run. Ctrl+C stops early and still prints the summary.
"""
import argparse
import os
import queue
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from koha_rfid import sip2  # noqa: E402
from koha_rfid.barcode import classify  # noqa: E402
from koha_rfid.epc import NotLibraryTag, decode_epc  # noqa: E402
from koha_rfid.readers.r220 import R220Reader  # noqa: E402

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reader", help="R220 IP address")
    ap.add_argument("--seconds", type=float, default=30, help="0 = run until Ctrl+C")
    ap.add_argument("--antennas", type=int, nargs="+", default=[1])
    ap.add_argument("--power", type=int, default=0, help="power table index, 0 = max")
    ap.add_argument("--session", type=int, default=2, choices=[0, 1, 2, 3])
    ap.add_argument("--sip-host", default="debian.mshome.net")
    ap.add_argument("--sip-port", type=int, default=6002)
    ap.add_argument("--user", default="sipterm1")
    ap.add_argument("--institution", default="MIDDL", help="AO / CP value")
    ap.add_argument("--show-foreign", action="store_true",
                    help="also list tags that aren't library items")
    a = ap.parse_args()
    pw = sip2.load_password()
    if not pw:
        ap.error("no SIP password: put it in .sip_password in the repo root, or set SIP_PASSWORD")

    # SIP first: no point reading tags if Koha isn't there.
    try:
        sip = sip2.Session(a.sip_host, a.sip_port, a.user, pw, a.institution)
    except PermissionError as e:
        raise SystemExit(str(e))
    print(f"SIP: logged in to {a.sip_host}:{a.sip_port} as {a.user}")

    seen = set()
    new_epcs = queue.Queue()

    def on_epc(epc, antenna, rssi):          # runs on the reader thread
        if epc not in seen:
            seen.add(epc)
            new_epcs.put((epc, antenna, rssi))

    reader = R220Reader(a.reader, on_epc, antennas=a.antennas, power=a.power, session=a.session)
    try:
        reader.start()
    except TimeoutError as e:
        print(e)
        return 3
    secs = f"{a.seconds:.0f}s" if a.seconds else "until Ctrl+C"
    print(f"Reader: inventorying on antenna(s) {a.antennas}, power index {a.power}, "
          f"for {secs}\n")

    found, unknown, foreign = [], [], []
    deadline = time.time() + a.seconds if a.seconds else None
    try:
        while deadline is None or time.time() < deadline:
            try:
                epc, ant, rssi = new_epcs.get(timeout=0.5)
            except queue.Empty:
                sip.keepalive()
                continue
            try:
                barcode = decode_epc(epc)
            except NotLibraryTag as e:
                foreign.append(epc)
                if a.show_foreign:
                    print(f"-- {epc}  skipped: {e}")
                continue
            kind = classify(barcode).kind
            t0 = time.time()
            item = sip2.lookup_item(sip, a.institution, barcode)
            ms = (time.time() - t0) * 1000
            if item is None:
                unknown.append(barcode)
                print(f"?? {barcode}  ({kind})  NOT IN KOHA  [ant {ant}, {rssi} dBm, {ms:.0f} ms]")
                continue
            found.append(item)
            via = f" via repaired form {item.barcode}" if item.repaired else ""
            print(f"OK {barcode}{via}  {item.title!r}\n"
                  f"   {item.status_text}, branch {item.permanent_location}  "
                  f"[ant {ant}, {rssi} dBm, {ms:.0f} ms]")
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        sip.close()

    print(f"\n==== Summary ====\n{len(seen)} distinct tags: {len(found)} items found in Koha, "
          f"{len(unknown)} decoded but not in Koha, {len(foreign)} not library tags")
    for b in unknown:
        print(f"  not in Koha: {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
