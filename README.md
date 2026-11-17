# koha-rfid-middleware

UHF RFID for [Koha](https://koha-community.org/), built against a Koha 21.11 test copy of the Middelburg (Mpumalanga) library data. Hardware: an Impinj Speedway R220 reader and a Zebra ZD500R RFID label printer.

The repo has three areas:

| Area | What it does | Status |
|---|---|---|
| **Staff desk** (production) | Check out and check in from the RFID pad **inside Koha's own pages**, recorded as the logged-in librarian. Print RFID labels from a record | Built; first install on the test instance pending |
| **Self-check** (semi-separate) | Patrons check out by themselves at a kiosk, over SIP2 | Core done and tested; kiosk next |
| **Testing** | The step-by-step scripts and field test that proved each part | Steps 1–5 done; field test planned |

```
Staff desk                                                   Self-check (next)
browser + RFID desk plugin ──► Koha pages (librarian)        kiosk ──SIP2──► Koha SIP server
   │ 127.0.0.1:8765                                            │
   ▼                                                           ▼
desk agent ──LLRP──► R220 pad      ──9100──► ZD500R          same core library (koha_rfid)
```

## Staff desk: production

| Path | What it is |
|---|---|
| [`docs/desk/desk-setup.md`](docs/desk/desk-setup.md) | **Start here:** Koha server setup, desk PC install, how librarians use it, troubleshooting |
| [`koha-plugin/`](koha-plugin/) | The Koha plugin (Perl + JS). Build the `.kpz` with `python deploy/build_plugin.py`. The `.kpz` also carries the desk agent and its installer: desk PCs get them from Koha, not from this repo |
| [`koha_rfid/agent/`](koha_rfid/agent/) | The desk agent: owns the reader and printer, serves them on 127.0.0.1 to the plugin |
| [`deploy/install_agent.ps1`](deploy/install_agent.ps1) | Installs the agent on a desk PC as a boot-time Scheduled Task, from Koha (the command on the plugin's Configure page) or from this repo. It keeps a copy as `C:\koha-rfid\install_agent.ps1` for `-Status`, `-Restart`, `-Update`, `-Pause`, `-Resume`, `-Uninstall` |
| [`deploy/agent.example.ini`](deploy/agent.example.ini) | Agent settings: reader, pad power, printer, allowed Koha address |

## Self-check: SIP2 (semi-separate)

| Path | What it is |
|---|---|
| [`docs/selfcheck/selfcheck-sip2.md`](docs/selfcheck/selfcheck-sip2.md) | Design, status, rules and open decisions for the kiosk |
| [`docs/selfcheck/sip2-koha.md`](docs/selfcheck/sip2-koha.md) | The SIP2 messages used, with replies captured from the test server, and Koha's quirks |
| [`koha_rfid/selfcheck/circulation.py`](koha_rfid/selfcheck/circulation.py) | Plan / check-out / check-in / verify / log over SIP2 |
| [`koha_rfid/sip2.py`](koha_rfid/sip2.py) | SIP2 client: messages, `AY`/`AZ` checksums, reply parser, session with keep-alive |

## Shared core library: `koha_rfid/`

| Path | What it is |
|---|---|
| [`koha_rfid/reader.py`](koha_rfid/reader.py) | `R220Reader` (LLRP via `sllurp==2.0.1`) and `FakeReader`. The only reader-specific code |
| [`koha_rfid/epc.py`](koha_rfid/epc.py) | EPC ↔ barcode (length-prefixed digits). Rejects tags that aren't library items |
| [`koha_rfid/barcode.py`](koha_rfid/barcode.py) | Check digit, format classification, `lookup_candidates()` (exact barcode first, then repaired forms) |
| [`koha_rfid/labels.py`](koha_rfid/labels.py) | ZD500R RFID labels (ZPL), with tag read-back. CLI for bulk printing |
| [`docs/reference/uhf-rfid.md`](docs/reference/uhf-rfid.md) | UHF specifics: radio band, EPC encodings, security options, cheaper hardware |
| [`docs/reference/barcode-format.md`](docs/reference/barcode-format.md) | The item barcode standard and how well the real data follows it; label printing |

## Testing: not deployed

| Path | What it is |
|---|---|
| [`docs/testing/steps.md`](docs/testing/steps.md) | The build steps 1–6 |
| [`docs/testing/field-test.md`](docs/testing/field-test.md) | Hands-on test with real books: pad tuning, 17 scenarios, results table |
| [`docs/testing/koha-test-setup.md`](docs/testing/koha-test-setup.md) | How the Koha test instance was set up (passwords redacted) |
| [`testing/`](testing/) | Step scripts: `step1_read_tags.py`, `step4_identify.py` (read-only), `step5_circulate.py` (**writes to Koha**), `sip2_probe.py`, `check_test_books.py`, `barcode_report.py`, `print_rfid_labels.ps1`. Step 1 results are in `testing/results/` |

The testing scripts and the desk agent both use the R220, which allows only one connection. Pause the agent first: `C:\koha-rfid\install_agent.ps1 -Pause`.

## Quick start (development)

```powershell
python -m unittest            # all tests; the plugin checks also use node and perl when installed
$py = "C:\Users\keanu\AppData\Local\Programs\Python\Python312\python.exe"   # has sllurp
# once: the SIP password for the testing scripts, in a git-ignored file
Set-Content .sip_password "<sip password>"
& $py testing\sip2_probe.py 33228100096617
```

Run the desk agent by hand with fake tags, without the R220:
```powershell
# agent-dev.ini:  [agent] allowed_origins = http://debian.mshome.net:8082
#                 [reader] fake_epcs = 0E3322801679489100000000
python -m koha_rfid.agent --config agent-dev.ini
```

## Key findings so far

- 74.7% of item barcodes are standard 14-digit barcodes. Most of the rest are the same numbers stored without the check digit (11.8%) or with spaces or hyphens (3.9%). So the barcode is always tried **exactly as read** first, then the repaired forms.
- Koha reports an unknown item as an `18` reply with an empty title, not as an error.
- Koha treats a second check-out to the same patron as a **renewal**, so items already on loan are skipped.
- Patron card numbers have no common format, and many are South African ID numbers. They're never logged in full.

## Not in this repo

Passwords and production server details. Keep them in `*.private.md` files and `.sip_password`, which `.gitignore` excludes.
