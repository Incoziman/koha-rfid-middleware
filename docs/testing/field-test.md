# Field test: RFID check-out and check-in with real books

Step 6 of [`steps.md`](steps.md). Steps 1–5 proved each part works: the reader, the EPC decoding, SIP lookups, and check-out/check-in. This test runs the whole thing with real library books, to see how it behaves in practice: read reliability, stray tags, stacks of books, and the cases Koha refuses.

Everything still runs against the **middelburg test instance**, so nothing reaches the live catalogue. Check-outs go to test patrons, and outgoing email stays disabled ([setup notes](koha-test-setup.md)).

## What you need

- The rig: R220 (192.168.1.3) with both antennas, the ZD500R, and the PC with the Koha VM running.
- 10–20 real books from MIDDL (see [Pick the books](#1-pick-the-books)).
- The SIP password in `.sip_password` in the repo root (one line; git-ignored, so it never reaches GitHub), and Python 3.12 with `sllurp==2.0.1`:
  ```powershell
  $py = "C:\Users\keanu\AppData\Local\Programs\Python\Python312\python.exe"
  ```
- Two test patrons: card `64` (`sipterm1`) already exists. Create a second in the staff interface (category `L`, library MIDDL, card e.g. `RFIDTEST2`) for the hold test.
- A notebook, or a copy of the [results table](#results).

## 1. Pick the books

Two ways. Either works, and both end in the same `test_books.csv`.

**A. From the shelf (simplest).** Take 10–20 books, and type or scan their barcodes into `scanned.txt`, one per line.

**B. From a Koha report.** Staff interface → Reports → Create from SQL. Paste this query, run it, and download the result as CSV. It lists available MIDDL books with standard barcodes and no holds, in random order, with call numbers so you can find them. It contains no patron data.
```sql
SELECT i.barcode, b.title, b.author, i.itemcallnumber AS callnumber,
       i.location, i.ccode, i.itype
FROM items i
JOIN biblio b ON b.biblionumber = i.biblionumber
WHERE i.homebranch = 'MIDDL' AND i.holdingbranch = 'MIDDL'
  AND i.onloan IS NULL AND i.itemlost = 0 AND i.withdrawn = 0
  AND i.damaged = 0 AND i.notforloan = 0
  AND i.barcode REGEXP '^3[0-9]{12,13}$'
  AND NOT EXISTS (SELECT 1 FROM reserves r WHERE r.biblionumber = i.biblionumber)
ORDER BY RAND()
LIMIT 40
```
Find the books on the shelf, and delete the rows for any you can't find.

**Try to include:**
- mostly ordinary books with 14-digit barcodes;
- 2–3 books whose Koha barcode is **13 digits** (no check digit), the "repaired form" case;
- 1 thin book (a pamphlet or children's book) and 1 thick one;
- 1 book with a foil or metallic cover, or a spiral binding, if you have one: metal blocks UHF.

**Check them against Koha** (read-only):
```powershell
& $py testing\check_test_books.py --file scanned.txt --out test_books.csv
```
It skips books that the test copy doesn't have (it's from 2026-09-14), that Koha shows as on loan, or that have barcodes the numeric tag encoding can't hold. If a book you're holding shows as on loan, it was probably returned after the copy was made. Either leave it out, or check it in first with `testing\step5_circulate.py checkin --barcode ...`.

## 2. Print and attach the tags

```powershell
# preview first
powershell -ExecutionPolicy Bypass -File testing\print_rfid_labels.ps1 -CsvFile test_books.csv
# print; every row of the read-back table should say OK
powershell -ExecutionPolicy Bypass -File testing\print_rfid_labels.ps1 -CsvFile test_books.csv -PrinterHost <printer IP>
```
Each label carries the barcode exactly as Koha stores it, the title, and the EPC. Check the printer IP first: the old example in [`barcode-format.md`](../reference/barcode-format.md#printing-test-rfid-labels-zebra-zd500r) uses 192.168.1.50, which step 1 recorded as the PC's address.

**Attaching:**
- **The tag doesn't have to go on the real book.** Koha only ever sees the barcode, so any stand-in works: an old notebook, a piece of cardboard, an empty box. Use one tag per object, keep metal away, and the title on the label tells you which "book" it is.
- **On real books, keep it removable.** Put the label on a card slipped inside the cover, or use low-tack tape. The tag encoding may change for the real rollout ([ISO 28560-4](../reference/uhf-rfid.md#tag-encoding-barcode--epc)), and the books belong to the live library.
- **Vary the position.** Put the tag inside the back cover, but at different heights or distances from the spine. Tags lying exactly on top of each other in a stack block each other. Test 5 in [Scenarios](#4-scenarios) checks how much this matters.
- Don't cover the existing barcode label.

**Check:** `& $py testing\step4_identify.py 192.168.1.3 --seconds 10 --antennas 1 2`, with all the tagged books near antenna 1. Every book should show `OK` with its title.

## 3. Tune the pad

Antenna 1 is the self-check pad. At full power it reads tags metres away. That's fine for testing, but in a library it would check out the next person's books.

The `--power` value is an index into the reader's power table: `0` = maximum (32.5 dBm), otherwise dBm = 10 + (index − 1) × 0.25. So `21` = 15 dBm, `41` = 20 dBm, `61` = 25 dBm.

1. Put 3 books flat on antenna 1. Put 3 others about 1 m away, as if on a trolley or in someone's arms.
2. For each power level, run `& $py testing\step4_identify.py 192.168.1.3 --seconds 10 --antennas 1 --session 0 --power <index>` three times. Use indexes 0, 61, 41, 31 and 21.
3. **Pick the lowest power at which the pad books are read every time.** Then check that the far books are never read at that level. Write down the index and use it as `--power` from here on.

If no level works, the antenna is too far-field for a pad. Note it: the real self-check point will need a near-field antenna or a shielded box ([uhf-rfid.md](../reference/uhf-rfid.md#how-uhf-differs-from-hf-for-a-library)).

## 4. Scenarios

Use `--power <your index> --antennas 1 --read-seconds 5` on every `step5_circulate.py` run. Before answering `y`, compare the list it shows with the books on the pad: **a missing or extra book is a finding**, whatever happens next. After each run, open patron `64` in the staff interface → Checkouts, and check that it matches.

| # | Scenario | How | Expected |
|---|---|---|---|
| 1 | Single check-out | 1 book on the pad, `checkout --card 64 --reader 192.168.1.3` | OK, with a due date. Koha shows it on loan to `sipterm1` |
| 2 | Single check-in | same book, `checkin --reader 192.168.1.3` | OK, and the book is available again |
| 3 | Stack of 5 | 5 books stacked, tags at different heights; check out, then check in | All 5 listed and done in one run |
| 4 | Stack of 10 | as 3, with 10 books | All 10. Note how long the list takes to appear |
| 5 | Tags aligned | 5 books with tags in the same spot, stacked exactly | Expect missed tags. Count how many are listed |
| 6 | Orientation | 1 book standing on its spine, then on its edge, then flat | Note which positions get read |
| 7 | Already on loan | check out a book, then run check-out again | `skip … already on loan`. Nothing sent (no renewal) |
| 8 | Not on loan | check-in on a book that's available | `skip … not on loan`. Nothing sent |
| 9 | Mixed pad | 2 on-loan books + 2 available books, check-in | Only the 2 on-loan books are checked in |
| 10 | Item on hold | place a hold on a pad book for `RFIDTEST2` in the staff interface, then check out to `64` | Refused by Koha (`AllowItemsOnHoldCheckoutSIP` = 0). Then check it out to `RFIDTEST2`, check it in: `ALERT CV01`? Cancel the hold afterwards |
| 11 | Unknown card | `checkout --card 000000` | `REFUSED - Invalid cardnumber`, before any tag is read |
| 12 | Blocked patron | give `RFIDTEST2` a manual restriction, then check out to it | Refused |
| 13 | Foreign tags | your old `FMK2SN…` / blank `E280…` tags on the pad with the books | Only the books are listed |
| 14 | Tag not in Koha | a `3322899990…` test tag on the pad | `skip … not in Koha` |
| 15 | Metal | a book on a metal shelf or trolley next to the pad; foil-cover book on the pad | Note what is and isn't read |
| 16 | Koha down | stop the VM's network (or `koha-sip --stop`), run a check-out | Clean error. No crash, and no half-done transaction in the log |
| 17 | Exit gate | antenna 2 at a "door". Walk past with 1 checked-out and 1 available book, running `step4_identify.py ... --antennas 2 --seconds 0` | Both read. The available one shows `available` (would alarm); the other shows `charged` |

## 5. Check Koha's side

For a few of the transactions, compare these with `logs\circulation.jsonl`:
- the item record → **Checkouts history**: the patron, the date, and the library;
- patron `64` → **Circulation history**;
- Tools → **Log viewer** (module: Circulation), if `IssueLog`/`ReturnLog` are on.

Every log line with `"verified": true` should match what Koha shows.

## 6. Clean up

1. Put every tagged book on the pad and run `checkin --reader 192.168.1.3`, until it says `Nothing to do`.
2. Patron `64` and `RFIDTEST2` should have **no checkouts**. Cancel any holds you placed, and lift any restrictions.
3. Take the tags off the books before they go back on the shelf.
4. Keep `logs\circulation.jsonl` and the results table. Don't commit the log; it's in `.gitignore`.

## Results

| # | Date | Books | Read OK / on pad | Koha result | Time to list | Notes |
|---|---|---|---|---|---|---|
| 1 | | | / | | | |
| 2 | | | / | | | |
| … | | | | | | |

**Pad power used:** index ___ (= ___ dBm). Far books read at that power: yes / no.

**Things to decide from the results:**
- Is the R220 antenna usable as a pad at low power, or is a near-field antenna or shielded box needed?
- How long should the pad read for before showing the list? The scripts use 5 s.
- Do the tags need fixed positions in the book?
- Is the "gate asks the system" security design workable at the door (scenario 17)?
