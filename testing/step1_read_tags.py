#!/usr/bin/env python3
"""
Step 1 - read tags only. No Koha, no SIP2.

Connects to an Impinj Speedway R220 over LLRP using sllurp (pinned 2.0.1),
prints every EPC to the console, checks the reader's frequency table against
the South African band (915-919 MHz), and summarises how consistently each
tag was read.

Usage:
    pip install sllurp==2.0.1
    python3 step1_read_tags.py READER_IP [--seconds 20] [--power 0]
                               [--expect EPC1 EPC2 ...] [--min-reads 5]

  --power   tx power INDEX into the reader's power table (0 = max). Drop it
            if tags are being read from across the room.
  --expect  EPCs of your test tags (hex, any case). Enables the pass/fail check.
"""
import argparse
import collections
import statistics
import sys
import threading
import time

from sllurp.llrp import LLRPReaderClient, LLRPReaderConfig, LLRPReaderState

BAND_LO_MHZ, BAND_HI_MHZ = 915.0, 919.0


def to_hex(v):
    # sllurp 2.0.1 already hexlifies EPCs, so v is ASCII hex bytes like b'e280...'
    if isinstance(v, (bytes, bytearray)):
        return v.decode("ascii").upper() or "(EMPTY)"
    return str(v).upper().replace("0X", "")


def find_key(obj, key):
    """Recursively find the first value stored under `key`."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=5084)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--power", type=int, default=0)
    ap.add_argument("--session", type=int, default=2, choices=[0, 1, 2, 3],
                    help="Gen2 session. 2 = each tag answers once then goes quiet; "
                         "use 0 for the consistency test")
    ap.add_argument("--antennas", type=int, nargs="+", default=[1],
                    help="antenna port(s), e.g. --antennas 2  or  --antennas 1 2")
    ap.add_argument("--quiet", action="store_true",
                    help="don't print every read, only the band check and summary")
    ap.add_argument("--expect", nargs="*", default=[])
    ap.add_argument("--min-reads", type=int, default=5,
                    help="minimum reads per expected tag to count as consistent")
    args = ap.parse_args()

    expected = {e.upper() for e in args.expect}
    reads = collections.Counter()
    rssi = collections.defaultdict(list)
    antennas = collections.defaultdict(set)
    channels = collections.defaultdict(set)
    freq_table_mhz = []

    def on_tags(reader, tag_reports):
        if not inventorying.is_set():
            return  # stale buffered reports from a previous session - ignore
        for t in tag_reports:
            # "EPC" covers both EPC-96 and variable-length EPCData in sllurp 2.0.1
            epc = t.get("EPC", t.get("EPC-96"))
            epc = to_hex(epc) if epc is not None else "(NO EPC FIELD)"
            n = t.get("TagSeenCount", 1)
            reads[epc] += n
            if "PeakRSSI" in t:
                rssi[epc].append(t["PeakRSSI"])
            if "AntennaID" in t:
                antennas[epc].add(t["AntennaID"])
            if "ChannelIndex" in t:
                channels[epc].add(t["ChannelIndex"])
            if not args.quiet:
                print(f"{time.strftime('%H:%M:%S')}  EPC={epc}  ant={t.get('AntennaID')}  "
                      f"rssi={t.get('PeakRSSI')}  ch={t.get('ChannelIndex')}  seen={n}")

    inventorying = threading.Event()

    def on_inventorying(reader, state):
        print(f"[reader state] INVENTORYING (session {args.session}, antennas {args.antennas})")
        inventorying.set()

    config = LLRPReaderConfig({
        "start_inventory": True,
        "report_every_n_tags": 1,
        "tx_power": args.power,
        "session": args.session,
        "antennas": args.antennas,
        "tag_content_selector": {
            "EnableROSpecID": False,
            "EnableSpecIndex": False,
            "EnableInventoryParameterSpecID": False,
            "EnableAntennaID": True,
            "EnableChannelIndex": True,
            "EnablePeakRSSI": True,
            "EnableFirstSeenTimestamp": True,
            "EnableLastSeenTimestamp": True,
            "EnableTagSeenCount": True,
            "EnableAccessSpecID": False,
        },
    })

    reader = LLRPReaderClient(args.host, args.port, config)
    reader.add_tag_report_callback(on_tags)
    # sllurp 2.0.1 signature: add_state_callback(state, cb)
    reader.add_state_callback(LLRPReaderState.STATE_INVENTORYING, on_inventorying)

    print(f"Connecting to {args.host}:{args.port} ...")
    reader.connect()

    # ---- band check -------------------------------------------------------
    if not inventorying.wait(15):
        print("Reader never reached INVENTORYING within 15 s. Another LLRP client may be "
              f"connected - try: sllurp reset {args.host}")
        reader.disconnect()
        sys.exit(3)
    try:
        caps = reader.llrp.capabilities
    except Exception:
        caps = None
    if caps:
        reg = caps.get("RegulatoryCapabilities", {})
        print(f"\nCountryCode: {reg.get('CountryCode')}  "
              f"(710 = South Africa; 0 = not reported)  "
              f"CommunicationsStandard: {reg.get('CommunicationsStandard')}")
        try:
            print(f"Power table (dBm, index 0 = max): {reader.llrp.tx_power_table}")
        except Exception:
            pass
        hop = find_key(caps, "FrequencyHopTable") or find_key(caps, "FixedFrequencyTable")
        freqs = find_key(hop, "Frequency") if hop else None
        if freqs is None:
            freqs = find_key(caps, "Frequency")
        if freqs:
            # LLRP reports kHz
            freq_table_mhz = [f / 1000.0 for f in freqs]
            print(f"\nReader frequency table ({len(freq_table_mhz)} channels, MHz): "
                  f"{[round(f, 2) for f in freq_table_mhz]}")
            out = [f for f in freq_table_mhz if not (BAND_LO_MHZ <= f <= BAND_HI_MHZ)]
            if out:
                print(f"  !! BAND CHECK FAILED - outside {BAND_LO_MHZ}-{BAND_HI_MHZ}: {out}")
                print("  902-928 = FCC, 865-868 = ETSI. Change the region on the reader itself "
                      "(web UI or Impinj shell over SSH) - it can't be set via LLRP.")
            else:
                print(f"  Band check OK - all channels within {BAND_LO_MHZ}-{BAND_HI_MHZ} MHz.")
        else:
            print("\nCould not locate a frequency table in the capabilities. "
                  "Dumping regulatory capabilities so you can read it manually:")
            print(find_key(caps, "RegulatoryCapabilities") or caps)
    else:
        print("\nNo capabilities available from this sllurp build - "
              "band can't be checked from the reader; rely on channel indexes below.")

    print(f"\nReading for {args.seconds:.0f}s - put your test tags in front of the antenna ...\n")
    t0 = time.time()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.time() - t0
        reader.disconnect(timeout=5)

    # ---- summary ----------------------------------------------------------
    print("\n==== Summary ====")
    if not reads:
        print("No tags read. Check antenna cable/port, power index, and tag distance.")
        sys.exit(2)

    for epc, n in reads.most_common():
        r = rssi.get(epc, [])
        rs = f"rssi {min(r)}..{max(r)} (mean {statistics.mean(r):.0f})" if r else "rssi n/a"
        chs = sorted(channels.get(epc, []))
        chinfo = ""
        if freq_table_mhz and chs:
            mhz = [round(freq_table_mhz[c - 1], 2) for c in chs if 0 < c <= len(freq_table_mhz)]
            chinfo = f"  freqs seen: {mhz}"
        tag = ""
        if expected:
            tag = "  [expected]" if epc in expected else "  [UNEXPECTED]"
        print(f"{epc}  reads={n} ({n / elapsed:.1f}/s)  ants={sorted(antennas[epc])}  {rs}{chinfo}{tag}")

    ok = True
    if expected:
        print()
        for e in sorted(expected):
            n = reads.get(e, 0)
            if n == 0:
                print(f"FAIL  {e}: never read")
                ok = False
            elif n < args.min_reads:
                print(f"WARN  {e}: only {n} reads (< {args.min_reads}) - inconsistent")
                ok = False
            else:
                print(f"PASS  {e}: {n} reads")
        stray = [e for e in reads if e not in expected]
        if stray:
            print(f"\nNote: {len(stray)} tag(s) read that weren't in --expect "
                  f"(nearby stock/other tags?).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
