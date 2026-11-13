# SIP2 with Koha: what the middleware needs

Reference for building the self-check / RFID middleware against Koha 21.11's SIP server. Messages marked **tested** were run against the `middelburg` test instance on 2026-11-13. The rest follow the 3M SIP2 specification and still need testing (they change data, so test them on the test instance only).

Working code: [`koha_rfid/sip2.py`](../koha_rfid/sip2.py). Read-only test tool: [`tools/sip2_probe.py`](../tools/sip2_probe.py).

## Connection

| Setting | Value |
|---|---|
| Host | `debian.mshome.net` (the test VM, from the Windows host) |
| Port | `6002`, raw TCP, no TLS |
| Login | `sipterm1` / the SIP password (not in this repo) |
| Institution (`AO`) and location (`CP`) | `MIDDL` |
| Message terminator | carriage return `\r`. Koha may send a stray `\n` before a reply, so strip it |
| Character set | Set per account (`encoding` in `SIPconfig.xml`). Check how titles with accents arrive |
| Error detection | enabled in `SIPconfig.xml`. Use it (see below) |

One TCP connection per self-check station: log in once (`93`), then send requests one at a time, each waiting for its reply.

## Framing and error detection (tested)

Every message is `<2-digit command><fixed-width fields><variable fields>`. A variable field is `<2-letter id><value>|`. With error detection, the message ends with `AY<seq>AZ<checksum>`:

- `seq` is one digit, 0–9, increasing with each message.
- The checksum covers every byte up to and including `AZ`: sum the bytes, take the two's complement, keep the low 16 bits, and write it as 4 uppercase hex digits.

```
>> 9900302.00AY1AZFCA5
<< 98YYYYNN10000520261113    1254442.00AOMIDDL|BXYYYYYYYYYYYNYYYY|AY1AZEDEE
```

Koha behaviour (tested):
- A request with a valid `AY`/`AZ` gets a reply with `AY`/`AZ` (same sequence number, valid checksum).
- A request with a **wrong** checksum gets `96` ("please resend"). Resend the same message once.
- A request with no `AY`/`AZ` is accepted and gets a reply without them. Don't rely on this.

**Dates** are 18 characters: `YYYYMMDDZZZZHHMMSS`, where `ZZZZ` is four spaces for local time, e.g. `20261113    125444`.

## Messages

### 93 → 94 Login (tested)
```
>> 9300CNsipterm1|CO<password>|CPMIDDL|AY0AZ....
<< 941AY0AZFDFD
```
`94` + `1` = OK, `0` = refused. The two `0`s after `93` are the user ID and password "encryption" algorithms. Koha refuses anything other than `0`. After a refused login, close the connection and alert staff; don't retry in a loop.

### 99 → 98 SC status (tested)
`99` + status (`0` = OK) + print width (`030`) + protocol version (`2.00`).
Reply fixed part: online, check-in OK, check-out OK, renewal policy, status update OK, offline OK (each `Y`/`N`), then timeout (3), retries (3), date (18), protocol (4). Then `AO`, and `BX` (which messages are supported). Send it after login and every few minutes as a keep-alive.

### 17 → 18 Item information (tested, read-only)
```
>> 17<date>AOMIDDL|AB3322810009661|AC|
<< 1803020120261113    125444AB3322810009661|AJSonder gister se spoor |CK001|AQMIDDL|BGMIDDL|
```
Reply fixed part: **circulation status** (2), **security marker** (2), fee type (2), date (18).

| Circulation status | Meaning | | Field | Meaning |
|---|---|---|---|---|
| `03` | available | | `AB` | barcode |
| `04` | charged (on loan) | | `AJ` | title. **Empty means the item is unknown** |
| `08` | waiting on hold shelf | | `AQ` | permanent location (home branch) |
| `10` | in transit | | `BG` | owner (holding branch) |
| `12` / `13` | lost / missing | | `AH` | due date, if on loan |
| `01` | other, also used for unknown items | | `CK` | media type |

Security marker: `00` other, `01` none, `02` tattle-tape, `03` whisper tape. Koha returns `02`.

**Unknown barcode (tested):** Koha does *not* send an error. It replies `18` with status `01` and an empty title:
```
<< 1801010120261113    125444AB00000000000000|AJ|
```
`item_found()` in `sip2.py` checks for that. It's the trigger for trying the repaired barcode forms from [`barcode-format.md`](barcode-format.md).

### 23 → 24 Patron status (tested with an unknown card)
```
>> 23000<date>AOMIDDL|AA<card>|AC|AD<pin>|
<< 24YYYY          00020261113    125444AE|AA<card>|BLN|AFInvalid cardnumber|AOMIDDL|
```
- `BL`: `Y` = valid patron, `N` = unknown.
- `CQ`: `Y` = PIN correct.
- The 14-character patron status in the fixed part: a `Y` at any position is a block (card not valid, charge privileges denied, and so on).
- `AE`: the patron's name.
- `AF`: a message to show on screen.

Decide whether stations ask for a PIN. If they do, send it in `AD` and refuse when `CQ` is `N`.

### 63 → 64 Patron information (tested in the setup notes)
Like `23`, but also returns counts and optional lists of loans and holds, plus contact details. Use `23` unless the screen needs to show the patron's loans.

### 11 → 12 Check-out (not tested yet)
```
>> 11NN<date><18 spaces>AOMIDDL|AA<card>|AB<barcode>|AC|AD<pin>|
```
Fixed part: renewal policy (`N`), no-block (`N`), transaction date, no-block due date (18 spaces).
Reply `12`: ok (`1`/`0`), renewal ok, magnetic media (`Y`/`N`/`U`), **desensitize** (`Y`/`N`/`U`), date, then `AB`, `AJ`, **`AH` due date**, and `AF` message.

- **Act on the security bit only when the reply has ok = `1` and desensitize = `Y`.** What "act" means depends on the security design. With UHF and a gate that asks the system, there's nothing to write. If the tag carries a flag, write "checked out" and confirm the check-out only once the write succeeds. See [`uhf-rfid.md`](uhf-rfid.md#security-checked-out-or-not).
- `AllowItemsOnHoldCheckoutSIP` is `0`, so an item on hold for someone else is refused (`120`…).

### 09 → 10 Check-in (not tested yet)
```
>> 09N<date><date>APMIDDL|AOMIDDL|AB<barcode>|AC|
```
Fixed part: no-block (`N`), transaction date, return date. `AP` = current location.
Reply `10`: ok, **resensitize** (`Y`/`N`/`U`), magnetic media, alert (`Y`/`N`), date, then `AB`, `AQ` permanent location, `CL` sort bin, and `CV` alert type.

- **When resensitize = `Y`, set the item back to "on shelf".** With a tag flag, write it; with a gate that asks the system, Koha's own status change is enough.
- If alert = `Y`, the item needs staff attention, e.g. `CV01` = hold for another patron, `CV04` = send to another branch. Put it in the staff bin, not back on the shelf.

## Self-check flow

```
on start:       connect → 93 login → 99 status (repeat every ~5 min as keep-alive)
patron card:    23 patron status → refuse if BL=N, CQ=N, or blocks
tags in range:  de-duplicate EPCs → decode to barcodes → drop any that don't decode
for each tag:   17 item info (exact barcode, then repaired forms) → show titles, patron confirms
  check-out:    11 with the barcode Koha recognised → if ok=1 and desensitize=Y, mark checked out*
  check-in:     09 → if resensitize=Y, mark on shelf* → if alert=Y, divert to staff bin
on error:       96 → resend once; connection lost → reconnect and log in again

* only if the tag carries the security flag; see uhf-rfid.md
```

Always send `11` / `09` with the barcode **as Koha recognised it** in the `17` step (the exact value or the repaired form that matched), not the raw tag value.

## Koha quirks seen on this server

- `SIPconfig.xml` must say `port="6002/tcp"`. `0.0.0.0:6002/tcp` opens the port, but every connection is dropped without a reply ("Unknown recognized server connection" in `sip.log`).
- `koha-sip --start` can hang in the terminal, but the server starts fine.
- The SIP log (`/var/log/koha/middelburg/sip.log`) records full messages, including patron names and contact details. Treat it as personal data.

## Still to find out

- **How the barcode is encoded in the EPC** (step 2 of [`steps.md`](../steps.md)), and whether it includes the check digit. Options and a proposal are in [`uhf-rfid.md`](uhf-rfid.md#tag-encoding-barcode--epc).
- **Security design:** a gate that asks the system, or a flag written to the tag. See [`uhf-rfid.md`](uhf-rfid.md#security-checked-out-or-not).
- Whether to use `pysip2` (step 3) or `koha_rfid/sip2.py`. Either way, check that it sends `AY`/`AZ` and handles the empty-title "unknown item" reply.
