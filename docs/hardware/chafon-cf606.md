# Chafon CF606 UHF desktop reader

> **⚠ The Chafon CF606 is a candidate only.** It is **not confirmed for any implementation**, has not been tested, and its driver has not been written. Nothing in this repo has talked to one. The agent accepts `type = chafon` so the settings and the Koha page are ready, but the reader always shows as **not connected**, with this message as the reason.

**Why it's being considered:** the Impinj R220 costs too much for every desk in a library-wide rollout. The CF606, or one of Chafon's sibling desktop models, is the likely cheap desk reader. The R220 moves to [stocktake](../stocktake/stocktake.md).

## Vendor specs (as given, not checked)

| | |
|---|---|
| Frequency | **902–928 MHz (US band)**: see [Radio band](#radio-band-check-before-buying) |
| Protocol | ISO 18000-6C (EPC Gen2): the same tags as the R220 |
| RF output | 5–26 dBm, 1 dB steps, ±0.2 dB; read and write power set separately |
| Antenna | Built-in, −6 dBi near-field |
| Read range | 0–1 m, depending on tag and surroundings |
| Speed | 100 tags/s |
| Sensitivity | −79 dBm at 15 dBm transmit, −77 dBm at 20 dBm |
| RSSI | Yes |
| Power | 4.0–5.5 V, < 1.5 W at full power (USB-powered) |
| Firmware | Online upgrade |
| SDK | Demo software, API, examples, manual; C#, Delphi, Java, Python, VB |

## Radio band: check before buying

The listed band is the US one. South Africa allows UHF RFID at **915–919 MHz** ([`../reference/uhf-rfid.md`](../reference/uhf-rfid.md#radio-band)), and a 902–928 MHz unit isn't automatically legal to use here. Ask Chafon whether:
- the unit can be set to (and kept at) 915–919 MHz in software, or
- there's a regional variant.

The tags themselves work in either band.

## What to get from Chafon

- The **serial command protocol document**: the bytes sent over the COM port, not just the Windows SDK DLL. The driver will talk to the reader directly from Python with `pyserial`, so it doesn't depend on a Windows-only DLL.
- How to set the **region/band**, **transmit power**, and the **Gen2 session / Q** (or the reader's equivalent).
- Whether the USB connection is a **virtual COM port** (CH340, CP210x, ...) or USB-HID. Some models also have a **keyboard-emulation** mode that types the EPC as text. Don't use it for the desk: the agent must keep knowing which books are still on the pad.
- Which sibling models share the same command set, so one driver covers them.

## Checklist for when a unit arrives

1. **Band:** set to 915–919 MHz; note how it's done here.
2. **Connection:** which COM port appears, baud rate, driver needed.
3. **Read the test tags** with Chafon's demo software: EPCs match what the ZD500R wrote (`0E3322...` style, see [`../reference/uhf-rfid.md`](../reference/uhf-rfid.md#tag-encoding-barcode--epc)).
4. **Presence:** a book left on the pad must keep being reported every round (session 0 or equivalent). Otherwise it drops off the pad list after `pad_seconds` while it's still there.
5. **Range at low power:** find the power where books about 1 m away (a trolley, the next patron's pile) are no longer read, but a stack of 5–10 on the pad all are. Same method as the R220 field test ([`../testing/field-test.md`](../testing/field-test.md#3-tune-the-pad)).
6. **Write:** can it write an EPC (for re-tagging without the ZD500R)?
7. Record the findings here, then write the driver.

## Writing the driver

Where it goes: [`koha_rfid/readers/chafon.py`](../../koha_rfid/readers/chafon.py). Its docstring has the step-by-step. In short:
- `start()`: open the COM port, set band and `power_dbm`, start a thread that runs inventory rounds and calls `on_epc(epc_hex, 1, rssi)` for each tag in each round. Raise if the reader doesn't answer.
- `stop()` / `is_alive()`: as for the R220.
- Add `pyserial` as an optional dependency (`[project.optional-dependencies] chafon = [...]` in `pyproject.toml`) and install it in `deploy/install_agent.ps1` when `type = chafon`.
- Replace the "not confirmed" refusal and its test in `tests/test_readers.py` with tests against recorded reader replies.

The config (`serial_port`, `baud`, `power_dbm`), the Configure page fields and the installer questions already exist. Remove the warnings only once the reader is tested and confirmed.
