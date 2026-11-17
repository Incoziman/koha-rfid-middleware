# Patron self-check over SIP2

Patrons check books out (and in) by themselves at a kiosk: they scan their card, put the books on the RFID pad, and the kiosk checks out everything on the pad. It talks to Koha over **SIP2** as a kiosk account, which is the standard for self-check machines.

This is **semi-separate** from the staff desk plugin ([`../desk/desk-setup.md`](../desk/desk-setup.md)). Both share the core library (`koha_rfid`: reader, EPC decoding, barcodes), but:

| | Staff desk | Self-check kiosk |
|---|---|---|
| Who operates it | a librarian, logged in to Koha | the patron |
| Talks to Koha via | Koha's own pages (plugin) | SIP2, port 6002 |
| Koha records as operator | the librarian | the kiosk's SIP account |
| Confirmation | librarian clicks "Check out N" | automatic: everything on the pad |
| Code | `koha_rfid/agent/`, `koha-plugin/` | `koha_rfid/selfcheck/` |

## Status

| Part | State |
|---|---|
| SIP2 client (`koha_rfid/sip2.py`): login, `17` item info, `23` patron status, `11` check-out, `09` check-in, keep-alive, checksums | Done. Tested against the middelburg test instance (2026-11-16/17) |
| Circulation core (`koha_rfid/selfcheck/circulation.py`): plan, send, verify, log | Done. Extracted from step 5 and unit-tested; `testing/step5_circulate.py` uses it |
| **Kiosk** (`koha_rfid/selfcheck/kiosk.py`): the running station with a screen | **Next.** Not started |

## The flow

Based on the self-check flow in [`sip2-koha.md`](sip2-koha.md#self-check-flow).

```
idle screen ──card scanned──► 23 patron status ──refused──► "See the desk: <Koha's message>" ──► idle
                                   │ ok
                                   ▼
                       "Put your books on the pad" ── pad read (tags settle for ~2 s)
                                   │
                                   ▼
                     decode tags → 17 for each (exact barcode, then repaired forms)
                     plan(): skip on loan / not in Koha / duplicates
                                   │
                                   ▼
                     11 for each, then 17 to verify (run())
                                   │
                                   ▼
             summary screen: ✓ title, due date  /  ✗ title, Koha's reason
             receipt (print or e-mail?), "Take your books" ──timeout──► idle
```
**Check-in mode** (a returns kiosk or a "Return" button): no card. It's `09` for each book on loan. If Koha's reply has `alert = Y` (`CV01` hold, `CV04` transfer), the screen says "Put this book in the staff tray" instead of "on the trolley".

## Rules carried over from testing (in `circulation.py`)

- **Skip items already on loan.** A second `11` for the same patron is a **renewal** in Koha.
- **Check-in only touches items on loan.** An `09` for a book on the shelf can trigger holds or transfers.
- **Verify every `11` / `09` with a `17`.** The tool reports what Koha actually did, not what it said.
- **Never resend a write after a dropped connection.** Koha may already have applied it. The `17` afterwards tells which.
- **Send the barcode as Koha recognised it** in the `17`, not the raw tag value ([`sip2-koha.md`](sip2-koha.md)).
- **Card numbers are often SA ID numbers (POPIA).** Logs keep only the last 3 characters and drop patron names (`mask()`, `scrub()`).

## What the kiosk needs that the desk doesn't

- **A pad that only sees the pad.** Check-out is automatic, so a stray tag on a trolley or in the next person's bag would be checked out to this patron. That needs a near-field antenna or a shielded pad, and low power. The field test ([`../testing/field-test.md`](../testing/field-test.md#3-tune-the-pad)) shows whether the R220 antenna can do it.
- **"Pad settled" detection.** Wait until the set of tags has been stable for ~2 s before checking out. `koha_rfid.agent.pad.PadState` already tracks presence and can be reused.
- **Patron card input.** Either a barcode scanner (keyboard wedge) or the card on the RFID pad, if cards get tags.
- **A screen.** Probably a local web page in a kiosk-mode browser, served by the same kind of local server as the desk agent.

## Deployment (Koha side)

- **One SIP account per kiosk** (`SIPconfig.xml` login + a patron with the `circulate` permission), with `institution` set to the kiosk's **branch**. Koha uses it as the checkout branch.
- `IndependentBranches` is **off** on the live system, which is needed or Koha refuses items from other branches (`ITEMNOTSAMEBRANCH`, seen on 2026-11-16).
- **Firewall port 6002** to the kiosk IPs only. SIP2 is unencrypted and carries card numbers.
- `AllowItemsOnHoldCheckoutSIP` is `0`: Koha refuses items on hold for someone else. The kiosk shows "See the desk".

## Open decisions

- **PIN:** ask for it (sent in `AD`, refused if `CQ` = `N`), or card only?
- **Receipt:** printed slip, e-mail, or on screen only?
- **Kiosk UI:** a local web page in kiosk mode (recommended), or a full-screen app?
- **Check-in at the kiosk,** or only at the desk?
- **Renewals** at the kiosk (a `29` message), or not at all?

## Trying it now

`testing/step5_circulate.py` runs the same core from the command line against the test instance:
```powershell
& $py testing\step5_circulate.py checkout --card 64 --reader 192.168.1.3
& $py testing\step5_circulate.py checkin --reader 192.168.1.3
```
The R220 allows only one connection. If the desk agent runs on the same PC, pause it first: `C:\koha-rfid\install_agent.ps1 -Pause`.
