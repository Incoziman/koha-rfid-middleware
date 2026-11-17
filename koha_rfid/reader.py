"""Reader adapters. Each one hands raw EPC hex strings to a callback.

This is the only module that knows about the reader hardware (docs/reference/uhf-rfid.md,
"Side note: cheaper hardware"). A desktop USB reader would get its own class
here with the same start()/stop() shape; nothing downstream changes.

R220Reader needs `pip install sllurp==2.0.1` (the version step 1 was tested
with). sllurp is imported only when an R220Reader is created, so the rest of
the package stays standard-library only.
"""

from __future__ import annotations

import threading
from typing import Callable, Iterable

EpcCallback = Callable[[str, int, int], None]   # (epc_hex, antenna, rssi)


def _epc_hex(v) -> str:
    # sllurp 2.0.1 already hexlifies EPCs: v is ASCII hex bytes like b'0e33...'
    if isinstance(v, (bytes, bytearray)):
        return v.decode("ascii").upper()
    return str(v).upper().replace("0X", "")


class R220Reader:
    """Impinj Speedway R220 over LLRP, continuous inventory.

    The callback runs on sllurp's network thread: keep it short (e.g. put the
    EPC on a queue) and don't do SIP calls from it.
    """

    def __init__(self, host: str, on_epc: EpcCallback, port: int = 5084,
                 antennas: Iterable[int] = (1,), power: int = 0, session: int = 2,
                 keepalive_ms: int = 60_000):
        from sllurp.llrp import LLRPReaderClient, LLRPReaderConfig, LLRPReaderState

        self.host = host
        self._on_epc = on_epc
        self._inventorying = threading.Event()
        config = LLRPReaderConfig({
            "start_inventory": True,
            "report_every_n_tags": 1,
            "tx_power": power,          # index into the reader's power table, 0 = max
            "session": session,         # 2: each tag answers, then stays quiet for a while
            "antennas": list(antennas),
            "keepalive_interval": keepalive_ms,   # lets sllurp notice a dead connection
            "tag_content_selector": {
                "EnableROSpecID": False, "EnableSpecIndex": False,
                "EnableInventoryParameterSpecID": False, "EnableAntennaID": True,
                "EnableChannelIndex": False, "EnablePeakRSSI": True,
                "EnableFirstSeenTimestamp": False, "EnableLastSeenTimestamp": True,
                "EnableTagSeenCount": True, "EnableAccessSpecID": False,
            },
        })
        self._client = LLRPReaderClient(host, port, config)
        self._client.add_tag_report_callback(self._on_report)
        self._client.add_state_callback(LLRPReaderState.STATE_INVENTORYING,
                                        lambda reader, state: self._inventorying.set())

    def _on_report(self, reader, tag_reports) -> None:
        if not self._inventorying.is_set():
            return  # stale buffered reports from a previous session
        for t in tag_reports:
            epc = t.get("EPC", t.get("EPC-96"))
            if epc is not None:
                self._on_epc(_epc_hex(epc), t.get("AntennaID", 0), t.get("PeakRSSI", 0))

    def start(self, timeout: float = 15.0) -> None:
        """Connect and start inventory. Raises TimeoutError if the reader never starts."""
        self._client.connect()
        if not self._inventorying.wait(timeout):
            self.stop()
            raise TimeoutError(
                f"R220 at {self.host} never started inventory. Another LLRP client may "
                f"still be connected; try: sllurp reset {self.host}")

    def stop(self) -> None:
        self._client.disconnect(timeout=5)

    def is_alive(self) -> bool:
        """False once the LLRP connection thread has ended (reader gone, cable out)."""
        return self._client.is_alive()


class FakeReader:
    """Pretends the given EPCs are on the pad, reporting each every `interval` s.

    Same start()/stop()/is_alive() shape as R220Reader. Used by the tests, and by
    the desk agent's `fake_epcs` setting to work on the Koha plugin without the
    R220 (which only allows one LLRP client at a time).
    """

    def __init__(self, on_epc: EpcCallback, epcs: Iterable[str] = (), interval: float = 0.5,
                 antenna: int = 1, rssi: int = -40):
        self._on_epc = on_epc
        self.epcs = list(epcs)
        self.interval, self.antenna, self.rssi = interval, antenna, rssi
        self._stop = threading.Event()
        self._thread = None

    def start(self, timeout: float = 15.0) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            for epc in list(self.epcs):
                self._on_epc(epc, self.antenna, self.rssi)
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def collect_epcs(host: str, seconds: float = 3.0, **kwargs) -> dict:
    """Inventory for `seconds`, return {epc_hex: (antenna, best_rssi)} for every tag seen."""
    import time
    seen: dict = {}

    def on_epc(epc, ant, rssi):
        if epc not in seen or rssi > seen[epc][1]:
            seen[epc] = (ant, rssi)

    r = R220Reader(host, on_epc, **kwargs)
    r.start()
    try:
        time.sleep(seconds)
    finally:
        r.stop()
    return dict(seen)
