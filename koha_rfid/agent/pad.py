"""What's on the pad right now, and the thread that keeps the reader connected."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from ..barcode import lookup_candidates
from ..epc import try_decode_epc

log = logging.getLogger("koha_rfid.agent")


class PadState:
    """Thread-safe record of tag reads. A tag is 'on the pad' until pad_seconds
    after its last read."""

    def __init__(self, pad_seconds: float = 2.0, clock: Callable[[], float] = time.monotonic):
        self.pad_seconds = pad_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: Dict[str, Tuple[float, int, int]] = {}   # epc -> (last seen, antenna, rssi)
        self.connected = False
        self.error = ""
        self.since = clock()

    def on_epc(self, epc: str, antenna: int, rssi: int) -> None:
        with self._lock:
            self._seen[epc] = (self._clock(), antenna, rssi)

    def set_connected(self, connected: bool, error: str = "") -> None:
        with self._lock:
            if connected != self.connected:
                self.since = self._clock()
            self.connected, self.error = connected, error
            if not connected:
                self._seen.clear()   # nothing is known to be on the pad without a reader

    def state_seconds(self) -> float:
        """How long the reader has been connected (or disconnected)."""
        return self._clock() - self.since

    def snapshot(self) -> Dict:
        """{'tags': [library tags on the pad], 'foreign': count of other tags}."""
        now = self._clock()
        with self._lock:
            for epc in [e for e, (t, _, _) in self._seen.items() if now - t > 60]:
                del self._seen[epc]
            present = [(e, a, r, now - t) for e, (t, a, r) in self._seen.items()
                       if now - t <= self.pad_seconds]
        tags, foreign = [], 0
        for epc, ant, rssi, age in sorted(present):
            barcode = try_decode_epc(epc)
            if barcode is None:
                foreign += 1
                continue
            tags.append({"epc": epc, "barcode": barcode,
                         "candidates": lookup_candidates(barcode),
                         "antenna": ant, "rssi": rssi, "age": round(age, 2)})
        return {"tags": tags, "foreign": foreign}


class ReaderRunner(threading.Thread):
    """Keeps a reader running: connects, watches it, reconnects after failures.

    `factory(on_epc)` returns an object with start() / stop() / is_alive()
    (see koha_rfid.readers; make_reader_factory picks one from the config).
    """

    def __init__(self, factory: Callable, pad: PadState, retry_seconds: float = 10.0,
                 check_seconds: float = 1.0):
        super().__init__(name="reader-runner", daemon=True)
        self.factory, self.pad = factory, pad
        self.retry_seconds, self.check_seconds = retry_seconds, check_seconds
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            reader: Optional[object] = None
            try:
                reader = self.factory(self.pad.on_epc)
                reader.start()
                self.pad.set_connected(True)
                log.info("reader connected")
                while not self._halt.wait(self.check_seconds):
                    if not reader.is_alive():
                        raise ConnectionError("reader connection lost")
            except Exception as e:   # noqa: BLE001 - any failure means: retry later
                msg = f"{type(e).__name__}: {e}"
                if self.pad.connected or self.pad.error != msg:
                    log.warning("reader unavailable (%s); retrying every %ss", msg, self.retry_seconds)
                self.pad.set_connected(False, msg)
            finally:
                if reader is not None:
                    try:
                        reader.stop()
                    except Exception:   # noqa: BLE001
                        pass
            if self._halt.is_set():
                break
            self._halt.wait(self.retry_seconds)
        self.pad.set_connected(False, "agent stopping")

    def stop(self) -> None:
        self._halt.set()

