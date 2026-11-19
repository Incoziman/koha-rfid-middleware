"""A reader that pretends tags are present. No hardware needed."""

from __future__ import annotations

import threading
from typing import Iterable

from . import EpcCallback


class FakeReader:
    """Pretends the given EPCs are on the pad, reporting each every `interval` s.

    Same start()/stop()/is_alive() shape as the real readers. Used by the tests,
    by the desk agent's `fake_epcs` setting (to work on the Koha plugin without a
    reader) and by `koha-rfid-stocktake --fake`.
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
