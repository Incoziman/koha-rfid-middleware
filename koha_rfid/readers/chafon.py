"""Chafon CF606 UHF desktop reader (USB). PLACEHOLDER: no driver yet.

    WARNING: the Chafon CF606 is a candidate only. It is NOT confirmed for any
    implementation, has not been tested, and its driver has not been written.
    See docs/hardware/chafon-cf606.md.

Why it's here: it's the likely cheap desk reader for a library-wide rollout
(the R220 costs too much for every desk). This class holds its place in the
agent so the reader type, settings and Koha settings page already exist, and
the driver can be added later without touching anything else.

Vendor specs (not checked on a real unit):
    band        902-928 MHz (the US band; South Africa allows 915-919 MHz,
                so the unit must be set or bought for that, see the doc)
    protocol    ISO 18000-6C (EPC Gen2): the same tags as the R220
    tx power    5-26 dBm in 1 dB steps, read and write power set separately
    antenna     built-in -6 dBi near-field, 0-1 m range
    speed       up to 100 tags/s, RSSI reported
    connection  USB, 4.0-5.5 V; SDK for C#, Delphi, Java, Python, VB

To write the driver (when the reader and Chafon's serial protocol document
are here):
    1. Talk to it over its virtual COM port with pyserial (add it as an
       optional dependency, like sllurp is for the R220). Don't use keyboard
       emulation mode: the agent must know which tags are still present.
    2. start(): open the port, set the region (915-919 MHz) and power_dbm,
       start a thread that runs inventory rounds and calls
       on_epc(epc_hex, 1, rssi) for every tag in every round (single antenna).
       Raise if the reader doesn't answer within `timeout`.
    3. stop(): end the thread and close the port. is_alive(): False once the
       thread has died (cable out, port gone).
    4. Tags must answer every round (Gen2 session 0, or the reader's
       equivalent) or books will drop off the pad list while still on it.
"""

from __future__ import annotations

from . import EpcCallback

NOT_CONFIRMED = ("The Chafon CF606 is a candidate only: it is not confirmed for any "
                 "implementation, has not been tested, and its driver has not been written "
                 "(docs/hardware/chafon-cf606.md). Use type = r220 or fake_epcs for now.")


class ChafonReader:
    """Same start()/stop()/is_alive() shape as R220Reader. start() always refuses."""

    def __init__(self, serial_port: str, on_epc: EpcCallback, baud: int = 57600,
                 power_dbm: int = 20):
        self.serial_port = serial_port
        self.baud = baud
        self.power_dbm = power_dbm
        self._on_epc = on_epc

    def start(self, timeout: float = 15.0) -> None:
        raise NotImplementedError(NOT_CONFIRMED)

    def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False
