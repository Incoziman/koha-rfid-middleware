"""Reader adapters. Each one hands raw EPC hex strings to a callback.

This is the only part of the package that knows about reader hardware. Every
reader has the same shape:

    reader = SomeReader(..., on_epc)   # on_epc(epc_hex, antenna, rssi)
    reader.start(timeout)              # connect and start inventory, or raise
    reader.is_alive()                  # False once the connection is gone
    reader.stop()

| type     | module  | role                                                         |
|----------|---------|--------------------------------------------------------------|
| `r220`   | r220.py | Impinj R220 over LLRP: desk test reader, and stocktake       |
| `chafon` | chafon.py | Chafon CF606 USB desk reader: NOT CONFIRMED, no driver yet |
| (fake)   | fake.py | pretend tags, for tests and development                      |

See docs/hardware/README.md.
"""

from __future__ import annotations

from typing import Callable

EpcCallback = Callable[[str, int, int], None]   # (epc_hex, antenna, rssi)

READER_TYPES = ("r220", "chafon")


def make_reader_factory(cfg) -> Callable:
    """The reader the agent config asks for, as `factory(on_epc) -> reader`.

    fake_epcs wins over everything (testing). A reader that isn't set up gives a
    factory that raises, so the agent shows the reason and keeps retrying.
    """
    if cfg.fake_epcs:
        from .fake import FakeReader
        return lambda on_epc: FakeReader(on_epc, cfg.fake_epcs)

    def missing(msg: str) -> Callable:
        def no_reader(on_epc):
            raise RuntimeError(msg)
        return no_reader

    if cfg.reader_type == "chafon":
        if not cfg.serial_port:
            return missing("no [reader] serial_port set in the agent config")
        from .chafon import ChafonReader
        return lambda on_epc: ChafonReader(cfg.serial_port, on_epc, baud=cfg.baud,
                                           power_dbm=cfg.power_dbm)
    if cfg.reader_type == "r220":
        if not cfg.reader_host:
            return missing("no [reader] host set in the agent config")
        from .r220 import R220Reader
        return lambda on_epc: R220Reader(cfg.reader_host, on_epc, port=cfg.reader_port,
                                         antennas=cfg.antennas, power=cfg.power,
                                         session=cfg.session, keepalive_ms=cfg.keepalive_ms)
    return missing(f"unknown reader type {cfg.reader_type!r}")
