# Stocktake with the Impinj R220

Walk the shelves with a laptop and the R220. It reads every library tag in range, shelf by shelf, and writes plain barcode files. You then upload those to Koha's own **Tools → Inventory**, which marks the items as seen and lists what's missing.

**Nothing is written to Koha by this tool**, and nothing is written to the tags. Koha's Inventory tool makes the changes, when a librarian uploads the file.

> Status: built and unit-tested with fake tags. **Not yet tried on real shelves** with the R220. Do a small trial first (one bay), and compare the result with a manual count.

## What you need

- The **R220** with power, and 1 or 2 antennas. A trolley carrying the reader, a battery or extension lead, and the antenna facing the shelves works well.
- A **laptop** on the same network as the R220 (or plugged straight into it), with this package and `sllurp`:
  ```powershell
  pip install "koha-rfid-middleware[r220]"     # or, in the repo:  pip install -e ".[r220]"
  ```
- The R220 allows **only one connection**. If a desk agent uses it, pause that agent first: `C:\koha-rfid\install_agent.ps1 -Pause` (and `-Resume` afterwards).

## Running it

```powershell
koha-rfid-stocktake 192.168.1.3 --antennas 1,2
# or: python -m koha_rfid.stocktake 192.168.1.3 --antennas 1,2 --out D:\stocktake\2026-11-adult-fiction
```

1. It connects and starts reading.
2. Type a **section name** (e.g. `Shelf 12A`, `Adult fiction bay 3`) and press Enter. Press Enter with no name to number it.
3. Move the antenna slowly along the section, a hand's width from the spines, top to bottom. The line on screen counts the books found in this section and in total.
4. Press **Enter** to end the section. It shows how many books and other tags it found, and how many were already counted in an earlier section.
5. Repeat for the next section. Type `q` to finish. Ctrl+C also finishes and keeps what was read.

| Option | Default | |
|---|---|---|
| `--antennas 1,2` | `1` | Antenna ports connected |
| `--power N` | `0` (max, 32.5 dBm) | Index into the R220 power table, as for the desk. Lower it if the next shelf over gets read too |
| `--session N` | `2` | Gen2 session. 2: each tag answers once and then stays quiet, so a full shelf is read quickly |
| `--out DIR` | `stocktake-YYYYMMDD-HHMM` | Where the results go |
| `--fake EPC,...` | | No reader: pretend these tags are in range. For practice |

**Sections and stray reads.** Each book is counted in the section where it's **first** read. Tags read while no section is open (walking up to the next shelf, typing its name) count towards the **next** section. Because the R220 reads a metre or more, a book can be read from the neighbouring shelf. That's why each book is counted once, and `reads.csv` shows every section it was seen in.

## The results

| File | What it is |
|---|---|
| `barcodes-all.txt` | Every library barcode found, one per line. **This is the file for Koha** |
| `barcodes-01-shelf-12a.txt`, ... | The same, per section, numbered in scan order |
| `reads.csv` | One row per tag: section, EPC, barcode, number of reads, best RSSI, antenna, first and last seen, other sections it was read in |
| `foreign.txt` | EPCs that aren't library tags (blank tags, ID cards, stock tags). Normally short |
| `summary.txt` | Counts per section and in total |

The barcode is written **exactly as encoded on the tag**. The desk's "repaired forms" (adding a check digit and so on) aren't applied, because Koha's Inventory tool matches barcodes exactly.

## Uploading to Koha (Tools → Inventory)

In the staff interface: **Tools → Inventory** (Koha 21.11).

1. **Barcode file:** choose `barcodes-all.txt`, or one section's file.
2. **Set inventory date to:** the day of the stocktake. Koha sets each found item's "last seen" date to this.
3. To list what's **missing**, limit the inventory to the area you scanned (library, shelving location, call number range) and tick **Compare barcodes list to results**. Items in that area that aren't in the file are listed as missing. Use a section's file when the sections match a shelving location or call number range.
4. Tick **"Do not check in items scanned during inventory"** (if your Koha shows that option) unless you've decided that books found on the shelf should be checked in automatically. Otherwise review the "checked out" items in the results by hand.
5. Ignore any **"out of order" / "wrong place"** reports that depend on scan order: RFID reads come in no particular order.

Check these option names against the Koha version in use. They differ a little between releases.

## Before relying on it

- **Trial on one bay:** scan it, then count by hand. Note books missed (tag on metal shelving, books packed tightly, tag hidden behind other tags) and books read from the next bay.
- Tune `--power` and the antenna distance from that trial, and record the settings here.
- Check `foreign.txt`: lots of entries there can mean tags written in a different encoding.
