# koha-rfid-middleware

Groundwork for RFID self-check middleware that connects library RFID readers to [Koha](https://koha-community.org/) over SIP2. It's being built against a Koha 21.11 test copy of the Middelburg (Mpumalanga) library data.

```
UHF reader ──LLRP/Ethernet──> middleware ──SIP2 over TCP──> Koha SIP server
(Impinj Speedway R220)         (this repo)                   (port 6002)

Zebra ZD500R ── encodes tags + prints barcode labels
```

Koha never sees the reader. The middleware reads EPCs from the UHF tags, decodes each one to an item barcode, asks Koha about it over SIP2, and handles security based on Koha's reply.

## Plan

From [`steps.md`](steps.md). Each step is read-only until step 5:

1. **Read tags:** `sllurp` against the R220, print EPCs. Check the 915–919 MHz region setting.
2. **Decode EPC → barcode,** offline, tested against known tag/item pairs.
3. **Barcode → SIP item info,** offline (`17` message).
4. **Join them:** reader → decode → SIP → print the title. This is the first real milestone.
5. **Check-out** (`11`) against the `middelburg` test instance.

## What's here

| Path | What it is |
|---|---|
| [`steps.md`](steps.md) | The step-by-step build plan |
| [`docs/uhf-rfid.md`](docs/uhf-rfid.md) | UHF specifics: the test hardware, radio band, barcode-in-EPC encodings (with a proposal), security options, and what changes with cheaper readers or with HF |
| [`docs/barcode-format.md`](docs/barcode-format.md) | The item barcode standard (14-digit Codabar, Luhn check digit), how well the real data follows it, and what that means for reading tags. Also covers patron card formats |
| [`docs/sip2-koha.md`](docs/sip2-koha.md) | The SIP2 messages the middleware needs, with replies captured from the test server, Koha's quirks, and the self-check flow |
| [`koha_rfid/barcode.py`](koha_rfid/barcode.py) | Check digit, format classification, and `lookup_candidates()` (exact barcode first, then repaired forms) |
| [`koha_rfid/epc.py`](koha_rfid/epc.py) | EPC ↔ barcode in the length-prefixed encoding (step 2). Rejects tags that aren't library items |
| [`koha_rfid/reader.py`](koha_rfid/reader.py) | Reader adapter: `R220Reader` (LLRP via `sllurp==2.0.1`) hands raw EPCs to a callback. The only hardware-specific code |
| [`tools/step4_identify.py`](tools/step4_identify.py) | Step 4, read-only: reader → decode → SIP `17` → prints title and status for each tag in range |
| [`koha_rfid/sip2.py`](koha_rfid/sip2.py) | SIP2 message builders, `AY`/`AZ` checksums, reply parser, a simple client |
| [`tools/sip2_probe.py`](tools/sip2_probe.py) | Read-only SIP2 test: login, status, and item lookups with fallback. Runs on Windows or Linux |
| [`tools/barcode_report.py`](tools/barcode_report.py) | Counts barcode formats in a Koha database. Prints only counts, never barcodes |
| [`middelburg-koha-test-setup.md`](middelburg-koha-test-setup.md) | How the Koha test instance was set up (passwords redacted) |

Python 3.8+, standard library only.

## Quick start

Run the tests:
```bash
python3 -m unittest -v
```

Check SIP from the Windows host (PowerShell):
```powershell
$env:SIP_PASSWORD = "<sip password>"
python tools\sip2_probe.py --host debian.mshome.net 33228100096617
```

Re-run the barcode analysis on the VM:
```bash
sudo koha-mysql middelburg --batch -e "SELECT barcode, homebranch FROM items" | python3 tools/barcode_report.py
```

## Key findings so far

- 74.7% of item barcodes are standard 14-digit barcodes. Most of the rest are the same numbers stored without the check digit (11.8%) or with spaces or hyphens (3.9%).
- So the middleware sends the barcode **exactly as read** first, and only falls back to repaired forms when Koha doesn't know it.
- Koha reports an unknown item as an `18` reply with an empty title, not as an error.
- Patron card numbers have no common format, and many appear to be South African ID numbers. Don't log them.

## Not in this repo

Passwords and production server details. Keep them in `*.private.md` files, which `.gitignore` excludes.
