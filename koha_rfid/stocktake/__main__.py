"""Stocktake with the Impinj R220: scan shelves section by section, then upload
the barcode file to Koha > Tools > Inventory.

    koha-rfid-stocktake 192.168.1.3 --antennas 1,2
    python -m koha_rfid.stocktake 192.168.1.3 --out D:\\stocktake
    python -m koha_rfid.stocktake --fake 0E3322801679489100000000   (practice, no reader)

Nothing is written to Koha. See docs/stocktake/stocktake.md.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from typing import Callable, List, Optional

from .session import StocktakeSession


def _antennas(v: str):
    try:
        ports = tuple(int(x) for x in v.replace(" ", "").split(",") if x)
    except ValueError:
        raise argparse.ArgumentTypeError("antenna numbers 1-4, comma-separated") from None
    if not ports or any(not 1 <= p <= 4 for p in ports):
        raise argparse.ArgumentTypeError("antenna numbers 1-4, comma-separated")
    return ports


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="koha-rfid-stocktake",
        description="Stocktake with the Impinj R220. Writes barcode files for Koha's Tools > Inventory.")
    ap.add_argument("host", nargs="?", help="R220 IP address")
    ap.add_argument("--port", type=int, default=5084, help="LLRP port (default 5084)")
    ap.add_argument("--antennas", type=_antennas, default=(1,),
                    help="antenna ports in use, e.g. 1,2 (default 1)")
    ap.add_argument("--power", type=int, default=0,
                    help="index into the R220 power table, 0 = max 32.5 dBm (default 0)")
    ap.add_argument("--session", type=int, choices=range(4), default=2,
                    help="Gen2 session: 2 (default) makes each tag answer once and then stay quiet, "
                         "so a shelf full of tags gets read quickly")
    ap.add_argument("--out", help="folder for the results (default stocktake-YYYYMMDD-HHMM)")
    ap.add_argument("--fake", metavar="EPC,EPC",
                    help="no reader: pretend these EPCs are in range (practice and tests)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def _make_reader(a, on_epc):
    if a.fake:
        from ..readers.fake import FakeReader
        return FakeReader(on_epc, [e.strip() for e in a.fake.split(",") if e.strip()], interval=0.1)
    from ..readers.r220 import R220Reader
    return R220Reader(a.host, on_epc, port=a.port, antennas=a.antennas, power=a.power,
                      session=a.session)


def _live(session: StocktakeSession, reader, stop: threading.Event,
          out: Callable[[str], None]) -> None:
    """Keep one status line updated while a section is being scanned."""
    warned = False
    while not stop.wait(0.5):
        c = session.counts()
        out(f"\r  this section: {c['section']:>5} books   total: {c['library']:>6} books, "
            f"{c['foreign']} other tags   (Enter = end section) ")
        if not warned and not reader.is_alive():
            warned = True
            out("\n  ! The reader connection was lost: no more reads. "
                "End the section; what was read so far is kept.\n")


def main(argv: Optional[List[str]] = None, input_fn: Callable[[str], str] = input,
         out: Callable[[str], None] = None) -> int:
    if out is None:
        def out(s: str) -> None:
            sys.stdout.write(s)
            sys.stdout.flush()
    a = build_parser().parse_args(argv)
    if not a.host and not a.fake:
        build_parser().error("give the R220's IP address (or --fake EPC,... to practise)")
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("sllurp").setLevel(logging.DEBUG if a.verbose else logging.WARNING)
    out_dir = a.out or time.strftime("stocktake-%Y%m%d-%H%M")

    session = StocktakeSession()
    reader = _make_reader(a, session.on_epc)
    if not a.fake:
        out(f"Connecting to the R220 at {a.host} (only one program can use it at a time: "
            f"pause any desk agent that uses it) ...\n")
    try:
        reader.start()
    except Exception as e:   # noqa: BLE001 - show the reason, nothing collected yet
        out(f"Can't start the reader: {type(e).__name__}: {e}\n")
        return 1
    out("Reader running. For each shelf or section: type its name, press Enter, scan, "
        "then press Enter again.\n")

    try:
        while True:
            try:
                name = input_fn("\nSection name (Enter = numbered, q = finish): ")
            except EOFError:
                break
            if name.strip().lower() in ("q", "quit", "exit"):
                break
            name = session.start_section(name)
            out(f"Scanning '{name}' ...\n")
            stop = threading.Event()
            live = threading.Thread(target=_live, args=(session, reader, stop, out), daemon=True)
            live.start()
            try:
                input_fn("")
            except EOFError:
                pass
            finally:
                stop.set()
                live.join()
            s = session.end_section()
            out(f"\n  '{s.name}': {s.library} books, {s.foreign} other tags"
                + (f", {s.seen_before} already counted in an earlier section" if s.seen_before else "")
                + f" ({s.seconds:g} s)\n")
    except KeyboardInterrupt:
        out("\nStopped (Ctrl+C). Saving what was read ...\n")
    finally:
        try:
            reader.stop()
        except Exception:   # noqa: BLE001
            pass

    files = session.write(out_dir)
    c = session.counts()
    out(f"\n{c['library']} library tags and {c['foreign']} other tags. Results in "
        f"{os.path.abspath(out_dir)}:\n")
    for f in files:
        out(f"  {os.path.basename(f)}\n")
    out("Next: Koha staff interface > Tools > Inventory > upload barcodes-all.txt "
        "(docs/stocktake/stocktake.md).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
