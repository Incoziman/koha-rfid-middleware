# Hardware: which reader does what

All readers here are **UHF, EPC Gen2** (ISO 18000-63), so they all read the same library tags. The barcode lives in the EPC ([`../reference/uhf-rfid.md`](../reference/uhf-rfid.md#tag-encoding-barcode--epc)). Swapping readers changes only the reader adapter in [`koha_rfid/readers/`](../../koha_rfid/readers/). The EPC decoding, barcode lookup, SIP2, desk agent and Koha plugin stay the same.

> **⚠ The Chafon CF606 is a candidate only.** It is **not confirmed for any implementation**, has not been tested, and its driver has not been written. Details: [`chafon-cf606.md`](chafon-cf606.md).

| Device | Role | Status | Code | Talks over |
|---|---|---|---|---|
| **Impinj Speedway R220** | Desk **test** reader; **stocktake** reader | Tested (step 1, desk agent) | [`readers/r220.py`](../../koha_rfid/readers/r220.py), [`stocktake/`](../../koha_rfid/stocktake/) | Ethernet, LLRP (`sllurp==2.0.1`) |
| **Chafon CF606** (or a sibling model) | Likely desk reader for a wide rollout (cost) | **NOT CONFIRMED.** No unit, no protocol document, no driver | [`readers/chafon.py`](../../koha_rfid/readers/chafon.py) (placeholder: refuses to start) | USB virtual COM port, Chafon's own serial protocol (expected) |
| **Zebra ZD500R** | RFID label printer: prints the label and writes the tag | Tested | [`labels.py`](../../koha_rfid/labels.py) | Ethernet, ZPL on port 9100 |
| (fake reader) | Tests and development | n/a | [`readers/fake.py`](../../koha_rfid/readers/fake.py) | `fake_epcs` in `agent.ini`, `--fake` for stocktake |

## Why two readers

- The **R220** reads many tags at once, metres away, with up to 2 antennas (4 on its bigger siblings). That's too much for a desk pad and too expensive for every desk. But it suits walking the shelves for a **stocktake**: [`../stocktake/stocktake.md`](../stocktake/stocktake.md).
- A **desktop USB reader** like the CF606 has a built-in near-field antenna with a short range (0–1 m). That suits a desk or self-check pad, where the risk is reading books on a nearby trolley, and it costs a fraction of the R220.

## Choosing the reader on a desk

`[reader] type` in the desk agent's `agent.ini`, or **Reader** on the plugin's Configure page (This desk PC):

| `type` | Settings | |
|---|---|---|
| `r220` (default) | `host`, `power` (power-table index), `antennas`, `session`, `port` | |
| `chafon` | `serial_port` (e.g. `COM3`), `power_dbm` (5–26), `baud` | ⚠ not confirmed: the agent shows "not connected" with the reason |

## Adding another reader

1. A class in `koha_rfid/readers/` with `start(timeout)`, `stop()` and `is_alive()`, calling `on_epc(epc_hex, antenna, rssi)` for every tag seen in every round.
2. Its name in `READER_TYPES` and a branch in `make_reader_factory()` ([`readers/__init__.py`](../../koha_rfid/readers/__init__.py)).
3. Its settings in `koha_rfid/agent/config.py` (`AgentConfig`, `load()`, `EDITABLE`), on the Configure page (`configure.tt`, rows marked `data-reader-type`) and in the installer's first-install questions.
4. Tests in `tests/test_readers.py`, and a page in this folder.
