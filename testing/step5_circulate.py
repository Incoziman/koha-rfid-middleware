#!/usr/bin/env python3
"""Step 5: check items out to a patron, or check them in, over SIP2.

WRITES TO KOHA. Use the middelburg TEST instance only. Only SIP 11 (check-out)
and 09 (check-in) are sent, so Koha applies its normal circulation rules and
records each one like a desk transaction. Nothing is renewed, edited or deleted.

    # tags on the R220 antenna:
    python testing\\step5_circulate.py checkout --card 64 --reader 192.168.1.3
    python testing\\step5_circulate.py checkin --reader 192.168.1.3
    # or typed barcodes, no reader needed:
    python testing\\step5_circulate.py checkout --card 64 --barcode 3322810009661

How it works:
  1. check-out only: patron status (23); stop if the card is invalid or blocked
  2. read tags for --read-seconds, decode, and skip tags that aren't library items
  3. item info (17) for each; plan what to do; skip what doesn't apply:
       check-out skips items already on loan (it would become a renewal),
       check-in skips items that aren't on loan (it can trigger holds/transfers)
  4. show the plan and ask y/N (--yes skips the question)
  5. send 11 / 09 one item at a time, then 17 again to confirm Koha's status changed
  6. append one JSON line per transaction to --log (card numbers masked)
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from koha_rfid import sip2  # noqa: E402
from koha_rfid.selfcheck import circulation as circ  # noqa: E402
from koha_rfid.selfcheck.circulation import mask  # noqa: E402

DEFAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs",
                           "circulation.jsonl")


def gather(a):
    """[(tag_barcode, epc or None)] from --barcode values and/or the reader."""
    out = [(b.strip(), None) for b in a.barcode if b.strip()]
    if a.reader:
        from koha_rfid.reader import collect_epcs
        print(f"Reading tags for {a.read_seconds:.0f}s on antenna(s) {a.antennas} ...")
        try:
            epcs = collect_epcs(a.reader, a.read_seconds, antennas=a.antennas,
                                power=a.power, session=a.session)
        except TimeoutError as e:
            raise SystemExit(str(e))
        tags, skipped = circ.decode_tags(epcs)
        out += tags
        print(f"  {len(epcs)} tags, {len(tags)} library items, {skipped} other tags skipped")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["checkout", "checkin"])
    ap.add_argument("--card", help="patron card number (check-out only)")
    ap.add_argument("--pin", action="store_true", help="ask for the patron's PIN and require it")
    src = ap.add_argument_group("items (one or both)")
    src.add_argument("--reader", help="R220 IP address: read the tags in range")
    src.add_argument("--barcode", action="append", default=[], help="item barcode; repeatable")
    ap.add_argument("--read-seconds", type=float, default=3)
    ap.add_argument("--antennas", type=int, nargs="+", default=[1])
    ap.add_argument("--power", type=int, default=0, help="power table index, 0 = max")
    ap.add_argument("--session", type=int, default=0, choices=[0, 1, 2, 3],
                    help="Gen2 session; 0 so tags answer again on every run")
    ap.add_argument("--sip-host", default="debian.mshome.net")
    ap.add_argument("--sip-port", type=int, default=6002)
    ap.add_argument("--user", default="sipterm1")
    ap.add_argument("--institution", default="MIDDL", help="AO / CP value")
    ap.add_argument("--location", default=None, help="AP for check-in (default: --institution)")
    ap.add_argument("--log", default=DEFAULT_LOG, help="JSONL transaction log")
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    a = ap.parse_args()

    pw = sip2.load_password()
    if not pw:
        ap.error("no SIP password: put it in .sip_password in the repo root, or set SIP_PASSWORD")
    if a.action == "checkout" and not a.card:
        ap.error("checkout needs --card")
    if not a.reader and not a.barcode:
        ap.error("give --reader and/or --barcode")
    location = a.location or a.institution
    pin = getpass.getpass("Patron PIN: ") if a.pin else ""

    try:
        sip = sip2.Session(a.sip_host, a.sip_port, a.user, pw, a.institution)
    except PermissionError as e:
        raise SystemExit(str(e))
    except OSError as e:
        raise SystemExit(f"Can't reach Koha's SIP server at {a.sip_host}:{a.sip_port} ({e}). "
                         "Nothing was sent.")
    print(f"SIP: logged in to {a.sip_host}:{a.sip_port} as {a.user}")

    patron = None
    if a.action == "checkout":
        patron = sip2.get_patron_status(sip, a.institution, a.card, pin)
        why = patron.refusal(pin_required=a.pin)
        if why:
            print(f"Patron {mask(a.card)}: REFUSED - {why}")
            return 1
        warn = f"  (status flags: {patron.flags!r})" if "Y" in patron.flags else ""
        print(f"Patron {mask(a.card)}: {patron.name or '(no name)'}{warn}")

    # ---- plan ---------------------------------------------------------------
    plan, skip = circ.plan(sip, a.institution, a.action, gather(a))

    for sk in skip:
        print(f"  skip {sk.tag_barcode}: {sk.reason}")
    if not plan:
        print("Nothing to do.")
        return 0
    verb = "Check out" if a.action == "checkout" else "Check in"
    print(f"\n{verb} {len(plan)} item(s)" +
          (f" to {patron.name or mask(a.card)}" if patron else "") + ":")
    for p in plan:
        item = p.item
        via = f" (Koha barcode {item.barcode})" if item.repaired else ""
        print(f"  {item.tag_barcode}{via}  {item.title!r}  [{item.status_text}]")
    try:
        answer = "y" if a.yes else input("Go ahead? [y/N] ").strip().lower()
    except EOFError:   # no terminal (piped/scheduled): never assume yes
        answer = ""
    if answer != "y":
        print("Cancelled - nothing sent.")
        return 0

    # ---- do it --------------------------------------------------------------
    log = circ.TransactionLog(a.log)
    failures = 0
    for p in plan:
        o = circ.run(sip, a.institution, a.action, p, card=a.card or "", pin=pin, location=location)
        log.write(o, card=a.card or "", sip_host=a.sip_host, sip_user=a.user)
        item, r, after = p.item, o.result, o.after
        if r is None:
            state = "OK (applied despite the drop)" if o.verified else "NOT applied"
            print(f"  ?? {item.barcode}  connection lost - Koha status now: "
                  f"{after.status_text if after else 'unknown'} -> {state}")
            failures += not o.verified
        elif o.ok:
            extra = f"due {r.due_date}" if r.due_date else ""
            if r.alert:
                extra += f"  ALERT CV{r.alert_type}: put in the staff bin"
            print(f"  OK {item.barcode}  {item.title!r}  {extra}".rstrip())
        else:
            failures += 1
            print(f"  FAILED {item.barcode}  {item.title!r}: {r.message or 'no message'}"
                  f"  (ok={int(r.ok)}, status now {after.status_text if after else '?'})")
    sip.close()
    print(f"\n{len(plan) - failures}/{len(plan)} done. Logged to {os.path.abspath(a.log)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
