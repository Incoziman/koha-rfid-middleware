# UHF RFID: hardware, tag encoding and security

This project uses **UHF** RFID (EPC Gen2 / ISO 18000-63). Most library RFID worldwide is HF (13.56 MHz, ISO 15693), and some of the standard library advice doesn't apply to UHF. This doc covers what's different, how to put barcodes on tags, and what changes with cheaper hardware.

> **Nothing here has been tested on the hardware yet.** The reader and printer details come from vendor documentation and general UHF practice. Check them against the actual devices during steps 1–2 of [`steps.md`](../testing/steps.md), and update this doc with what you find.

## Test hardware

| Device | Role | Talks over | Notes |
|---|---|---|---|
| **Impinj Speedway R220** | Fixed UHF reader, 2 antenna ports | Ethernet, **LLRP** (TCP port 5084). [`sllurp`](https://github.com/sllurp/sllurp) is a Python LLRP client | Discontinued (Impinj's current fixed reader is the R700). Reads many tags at once, several metres away with the right antenna |
| **Zebra ZD500R** | Thermal label printer with a built-in UHF encoder | USB / Ethernet, **ZPL** | Prints the barcode label and writes the tag in one job. Each label stock needs RFID calibration once |

Both are serious enterprise kit: more than self-check needs. See [Cheaper hardware](#side-note-cheaper-hardware) at the end.

### Radio band

South Africa allows UHF RFID in the **915–919 MHz** band (the band your step 1 targets; confirm against ICASA's current rules). UHF readers and printers are **sold per region**. Check that:
- the R220's model or region setting lists South Africa or covers 915–919 MHz (a US 902–928 MHz unit is *not* automatically legal to use here), and
- the ZD500R's RFID region setting matches, too.

## How UHF differs from HF for a library

| | HF (ISO 15693, most libraries) | UHF (this project) |
|---|---|---|
| Range | ~10–30 cm | metres, depending on power and antenna |
| Where the barcode lives | User memory, ISO 28560-2/-3 data model | **EPC memory** (96 or 128 bits typical), library standard ISO 28560-4 |
| Security bit | AFI byte (often `0x07` on shelf / `0xC2` checked out) or EAS | No single standard practice. See [Security](#security-checked-out-or-not) |
| Many tags at once | Yes, but short range keeps it to the pile on the pad | Yes, **including books that aren't on the pad**, unless power and antenna are tuned |

**The read-range point matters most for self-check.** A UHF reader can easily pick up books on a nearby trolley or in the next person's arms. The middleware must:
- turn the R220's transmit power right down, and use a small near-field antenna or a shielded pad for the self-check spot;
- show the patron the list of titles it is about to check out and require confirmation; never silently check out every tag in range;
- ignore tags that don't decode to a valid barcode (other tags in the building: ID cards, stock tags, etc.);
- de-duplicate reads: the reader reports the same tag many times a second.

## Tag encoding: barcode ↔ EPC

The tag only needs to hold the item barcode; Koha holds everything else. The open question is *how* the barcode string is stored in EPC memory. That's step 2 of [`steps.md`](../testing/steps.md) (decode EPC → barcode). None of the existing stock has RFID tags (checked 2026-11-13), so there's no encoding already in use that we'd have to follow.

Encodings you may meet, all for barcode `33228100096617`:

| Encoding | EPC (hex) | Bits | Pros / cons |
|---|---|---|---|
| **Digits as hex, left-padded** | `000000000033228100096617` | 96 | Common and simple. **Loses leading zeros**: `0799342823` would decode as `799342823`. Numeric barcodes only |
| **ASCII bytes** | `33333232383130303039363631370000` | 128 (112 used) | Handles letters (`GM048150` → `474D303438313530`). Needs 128-bit EPC; most cheap tags are 96-bit |
| **Length-prefixed digits** (proposed for testing) | `0E3322810009661700000000` | 96 | First byte = number of digits (`0E` = 14). No ambiguity, keeps leading zeros, fits 96-bit tags for barcodes up to 22 digits. Numeric only |
| **ISO 28560-4** | *(compacted, with a data model header)* | varies | The library UHF standard, so other vendors' gates and readers understand it. More complex to encode; needs a library or a vendor tool |
| **GS1 GIAI-96** | *(GS1 header `34` + company prefix + asset number)* | 96 | Standard for asset tags; needs a GS1 company prefix |

**Recommendation for testing:** length-prefixed digits on 96-bit tags. It's easy to implement and inspect by eye, and it covers the 97% of stock with numeric barcodes. For a real rollout, use ISO 28560-4 if the gate/security system vendor supports it, so tags stay readable if the middleware is ever replaced.

Whichever you pick, the decoder should **reject** anything that doesn't decode cleanly, and should run the result through `koha_rfid.barcode.classify()`. A `standard-14` barcode with a wrong check digit means the decoding is wrong (byte order, padding, hex vs ASCII). That's a cheap self-test.

About 3% of items have alphanumeric (2.8%) or empty (0.2%) barcodes; see [`barcode-format.md`](barcode-format.md). Those need ASCII on 128-bit tags, or new numeric barcodes in Koha before tagging.

### Encoding with the ZD500R (ZPL)

One job that writes the EPC and prints a matching Codabar label. **Untested; check against Zebra's ZPL and RFID programming guides**:
```zpl
^XA
^RFW,H^FD0E3322810009661700000000^FS
^FO50,50^BKN,N,100,Y,N,A,A^FD33228100096617^FS
^XZ
```
- `^RFW,H`: write to the tag, data given as hex. EPC memory is the default target.
- `^BK`: Codabar barcode. `A`,`A` are the start/stop characters; most scanners don't transmit them.
- Read the tag back afterwards (`^RFR,H`, or with the R220) and compare. Zebra printers can void the label when the write fails. Turn that on.
- For a full label and a PowerShell script that prints test sets, see [Printing test RFID labels](barcode-format.md#printing-test-rfid-labels-zebra-zd500r).

## Security: checked out or not

On HF, check-out flips the AFI byte and the gate alarms on "on shelf" tags. On UHF, the common options are:

1. **Gate asks the system (recommended here).** The tag is never written at check-out. The exit gate (or a reader at the door) reads the EPC, decodes the barcode, and asks the middleware. The middleware asks Koha with a SIP `17`: circulation status `04` (charged) means OK to leave; anything else means alarm. It's simple, and there are no failed writes when ten books are on the pad, but the gate needs network access and Koha has to be up.
2. **A flag on the tag.** Write a "checked out" marker at check-out and clear it at check-in: the AFI in the EPC PC word (ISO 28560-4), or a bit in user memory. The gate works offline, but every check-out and check-in must write every tag successfully, and on UHF writes fail more often than reads.
3. **Chip-specific EAS.** Some tag chips (for example NXP UCODE models) have their own EAS alarm bit. This ties you to one chip family and readers that support it.

How Koha's SIP replies map onto these:

| SIP reply field | Option 1 (gate asks) | Option 2 (flag on tag) |
|---|---|---|
| `12` check-out, desensitize = `Y` | nothing to write | write "checked out" to each tag; only confirm the check-out once the write is verified |
| `10` check-in, resensitize = `Y` | nothing to write | write "on shelf" to each tag |

## Side note: cheaper hardware

The R220 + ZD500R pair costs far more than a self-check point needs. Cheaper setups change **only the reader end** of the middleware, if the code is split into layers:

```
reader adapter  →  EPC decoder  →  barcode lookup (koha_rfid.barcode)  →  SIP2 (koha_rfid.sip2)
   changes          may change         never changes                        never changes
```

That split now exists: every reader is an adapter in [`koha_rfid/readers/`](../../koha_rfid/readers/) with the same `start()` / `stop()` / `is_alive()` shape, chosen by `[reader] type` in the desk agent. Which reader does what, and the candidate cheap desk reader (the **Chafon CF606, not confirmed for any implementation**), are in [`../hardware/README.md`](../hardware/README.md). The R220 moves to [stocktake](../stocktake/stocktake.md).

### Desktop USB UHF reader (pad-style, much cheaper)

| What changes | Details |
|---|---|
| **Connection** | No LLRP or `sllurp`. Usually either **keyboard emulation** (the reader "types" the tag as text and presses Enter) or a **virtual serial port** with the vendor's own command protocol (use `pyserial` and the vendor's protocol document) |
| **Output format** | Set in the vendor's config tool, not by the middleware: hex EPC, or the EPC already turned into ASCII/decimal, with or without a prefix, spaces or a trailing CR/LF. The decoder must match whatever the reader is set to. **Record the settings in this repo**, because a factory reset silently changes them |
| **Multiple tags** | Keyboard-emulation readers often send one tag per read, or repeat tags. Serial readers usually report a list. De-duplicate either way |
| **Range** | Short, often 0–20 cm. That's *better* for self-check: fewer stray books in range |
| **Writing tags** | Most can write EPCs through the vendor tool or serial protocol. Keyboard-emulation mode can't, so encoding needs the vendor tool or a serial-mode reader |
| **Region** | Same rule: buy the 915–919 MHz (or switchable) variant |

A keyboard-emulation reader is the cheapest way to start: the middleware just reads lines of text. But the window must have focus, and you can't tell the reader apart from a real keyboard. For anything unattended, prefer serial mode.

### Instead of the ZD500R

- **Encode with the reader:** most desktop UHF readers can write EPCs (through the vendor tool or serial commands). Print barcode labels on any ordinary label printer.
- **Pre-encoded tags:** a supplier can deliver tags already encoded with a barcode range. Only practical for *new* stock with new barcodes; existing items must keep the barcode Koha already has.

### Switching to HF instead

HF (ISO 15693 + ISO 28560-2/-3) is what most library vendors sell, and HF pads and gates are widely available. It would change:
- the **tags** (re-tag everything; UHF and HF tags can't be read by each other's readers);
- the **decoder** (the barcode lives in user memory in the ISO 28560 format, not in the EPC);
- **security**, which becomes the standard AFI byte flip at check-out and check-in.

The barcode lookup and SIP2 layers would stay exactly the same. Make sure an HF reader supports **ISO 15693**: the cheap NFC readers (ISO 14443, e.g. ACR122U) can't read library tags.
