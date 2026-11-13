# Item barcode format

Analysis of the 644,503 item barcodes in the Mpumalanga consortium Koha dump of 2026-09-14 (the data loaded into the `middelburg` test instance). Reproduce it with [`tools/barcode_report.py`](../tools/barcode_report.py).

## Summary

- **There is a standard:** the 14-digit Codabar library barcode with a Luhn check digit. 74.7% of items match it exactly, and 89% of Middelburg's (`MIDDL`) items do.
- **Most of the rest is that same standard stored in a damaged form:** with spaces or hyphens typed in, or without the check digit.
- **The middleware must send the barcode exactly as read first, and only then try repaired forms.** 4,410 items have a repaired form that is itself a different item in Koha.

## The standard format

```
3 3228 10009661 7
│ │    │        └─ check digit (Luhn, also called "library mod 10")
│ │    └────────── item number, 8 digits
│ └─────────────── institution code, 4 digits
└───────────────── type digit: 3 = item
```

**Institution codes** seen on valid barcodes (type digit `3`, at least 50 items):

| Code | Valid items | | Code | Valid items |
|---|---|---|---|---|
| `3228` | 460,152 (95.6%) | | `3001` | 734 |
| `3089` | 12,780 | | `3006` | 279 |
| `3809` | 3,204 | | `3050` | 251 |
| `3909` | 1,654 | | `3005` | 220 |
| `3010` | 905 | | `1050` | 79 |
| `3003` | 888 | | `3004` | 66 |

`3228` covers nearly the whole consortium, including MIDDL. The others are probably barcode stock from earlier systems or other suppliers.

### Check digit

The Luhn algorithm runs over the first 13 digits:

1. Starting from the left, double the digits in odd positions (1st, 3rd, … 13th). If a result is over 9, subtract 9.
2. Add those to the digits in even positions.
3. Check digit = `(10 − total mod 10) mod 10`.

Worked example, payload `3322810009661`:

| Position | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Digit | 3 | 3 | 2 | 2 | 8 | 1 | 0 | 0 | 0 | 9 | 6 | 6 | 1 |
| Value | 6 | 3 | 4 | 2 | 7 | 1 | 0 | 0 | 0 | 9 | 3 | 6 | 2 |

Total 43, so the check digit is `(10 − 3) mod 10 = 7`, and the full barcode is `33228100096617`. In code: [`koha_rfid.barcode.check_digit`](../koha_rfid/barcode.py).

The check digit catches every single-digit error, which makes it a useful sanity check on what an RFID reader returns.

## What's actually stored in Koha

| Format (`classify()` kind) | Items | % | What it is |
|---|---|---|---|
| `standard-14` | 481,243 | 74.7 | Correct 14-digit barcode |
| `13-no-check-digit` | 76,357 | 11.8 | `3` + known institution + 8 digits, check digit missing. Probably scanned with a scanner set not to transmit the check digit |
| `14-bad-check-digit` | 27,240 | 4.2 | 14 digits, check digit wrong. Concentrated in a few branches (LYDEL, KWAML, STANRL, MBOMRL), so it's probably a local numbering scheme, not scanning errors |
| `standard-with-separators` | 25,333 | 3.9 | A correct barcode with spaces, hyphens or a trailing `"` typed in, e.g. `33228 02175355 1`, `33228-012954630` |
| `other` | 18,077 | 2.8 | Alphanumeric local numbers (`GM 048 150`, `AA999999`, …) |
| `short-numeric` | 13,219 | 2.1 | Legacy local numbers, mostly 5–6 digits (EVANL, LEBOL, KLIPL, SECL) |
| `isbn-ean` | 1,750 | 0.3 | The book's ISBN was used as the item barcode |
| `empty` | 1,284 | 0.2 | No barcode, so the item can't be circulated by SIP at all |

**MIDDL only** (2,348 items): 88.6% `standard-14`, 5.6% `14-bad-check-digit`, 4.6% `13-no-check-digit`, 0.9% empty.

Other checks:
- **Exact duplicates:** none. Koha enforces unique barcodes.
- **Surrounding spaces:** 282. Koha's SIP lookup won't match these if the reader sends the barcode without the spaces.
- **Repaired form already taken:** 4,410 barcodes have a repaired form that belongs to a *different* item. For example, the same book was entered once as `33228 0217…` and once as `332280217…`. Probably duplicate records for one physical copy, but someone at the library needs to check.
- **Branches that are mostly non-standard:** SOMAPL, PULLL, MHLUL, MKHUL (mostly separators, so easy to fix), STANRL, LYDEL, MBOMRL, ERMEL (mostly bad check digits), and LEANL, KLIPL, KOMAL (short local numbers). Run the report for the full list.

The SIP test item from the setup notes is a `13-no-check-digit` item. It's stored as `3322810009661`, but its printed label is almost certainly `33228100096617`.

## What this means for the RFID middleware

1. **The tag holds a string, and Koha matches it exactly.** Koha's SIP server (`C4::SIP::ILS::Item`) passes the barcode through `barcodedecode()` and then looks up `items.barcode` with an exact match. `barcodedecode()` runs any `item_barcode_transform` Koha plugin, then `itemBarcodeInputFilter`. Here no plugin is installed and the filter is empty, so the barcode isn't changed at all.
2. **When programming new tags,** write the barcode exactly as Koha stores it, or first correct the barcode in Koha (see 5).
3. **When reading tags**, use [`lookup_candidates()`](../koha_rfid/barcode.py):
   1. Send the exact value (trimmed of CR/LF and outer spaces).
   2. If Koha says it's unknown (empty `AJ` title in the `18` reply), try the repaired forms one at a time and stop at the first hit.
   3. Log every hit on a repaired form, so those items can be fixed in Koha.

   Tested on 2026-11-13: a tag holding `33228100096617` isn't found as-is, and the fallback finds the item as `3322810009661`. See [`tools/sip2_probe.py`](../tools/sip2_probe.py).
4. **Don't use the check digit to reject reads.** A quarter of the stock has no valid check digit. Use it only to flag a probable misread (a `standard-14` that fails can't come from a correct read of a `3228` tag).
5. **The better long-term fix is cleaning the data in Koha:** strip separators, and add the missing check digit, or relabel. Then the fallback is rarely needed. Before changing anything, check a sample of physical labels against Koha, branch by branch, and resolve the 4,410 conflicting pairs first.
6. **Alternative: do the repair inside Koha.** A Koha plugin using the `item_barcode_transform` hook could map a scanned barcode to the stored form, for every SIP client and the staff interface alike. More work, but it keeps the middleware simple. Don't set `itemBarcodeInputFilter` to `whitespace` as a shortcut: it would strip spaces from the *scanned* value, and the 25,333 items stored *with* spaces would then never match.

## Koha settings involved

From `systempreferences` in the 2026-09-14 dump:

| Preference | Value | Effect |
|---|---|---|
| `itemBarcodeInputFilter` | *(empty)* | No filter. The barcode is used exactly as entered, in both the staff interface and SIP |
| `BarcodeSeparators` | `\s\r\n` | Splits batch check-out input in the staff interface only |
| `itemBarcodeFallbackSearch` | `1` | Staff interface only: an unknown barcode falls back to a catalogue search. SIP doesn't use this |
| `autoBarcode` | `OFF` | Koha doesn't generate barcodes. New stock comes pre-labelled |
| `CardnumberLength` | *(empty)* | No length rule for patron cards |
| `AllowItemsOnHoldCheckoutSIP` | `0` | SIP check-out is refused for items on hold for someone else |

## Patron card numbers

There's **no single format**. Of 20,428 patrons:

| Shape (9 = digit, A = letter) | Patrons | % |
|---|---|---|
| 13 digits | 9,198 | 45.0 |
| 17 digits | 3,420 | 16.7 |
| `AAA99999` | 1,658 | 8.1 |
| `AAA 999` (with a space) | 1,590 | 7.8 |
| `AAA9999` | 751 | 3.7 |
| 8 digits | 360 | 1.8 |
| other shapes | 3,451 | 16.9 |

> **Privacy:** 9,192 of the 9,198 13-digit card numbers start with a valid `YYMMDD` date. They appear to be **South African ID numbers** used as card numbers. These are personal information under POPIA. The middleware must not write card numbers to plain-text logs, and SIP2 sends them unencrypted, which is another reason to keep port 6002 firewalled to the self-check host.

The middleware should send patron card numbers exactly as read, with no format check of its own. Koha replies to an unknown card with `BLN` (not a valid patron).
