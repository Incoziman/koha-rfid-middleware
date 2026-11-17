# Step 1 results – Impinj R220 tag reads (2026-11-16)

Reader 192.168.1.3:5084 (LLRP) · PC 192.168.1.50 · sllurp 2.0.1 · Python 3.12.10 (full path, not Store Python)
Script: `step1_read_tags.py` · raw logs in `results\`

## Band check – PASS
- CountryCode **710** (South Africa)
- Hop table, 17 channels, all within 915–919 MHz:
  917.0, 918.6, 916.2, 916.6, 918.4, 917.8, 916.0, 915.8, 916.4, 915.6, 916.8, 917.4, 918.8, 918.0, 917.6, 918.2, 917.2
- Power table 10.0–32.5 dBm in 0.25 dB steps (index 0 = max = 32.5 dBm; all runs used max)
- Tags were read on all 17 channels, so the whole hop table is in use.

## EPCs seen
| EPC | Bits | Ant 1 RSSI | Ant 2 RSSI | Notes |
|---|---|---|---|---|
| 464D4B32534E3637 | 64 | −26 | −47 | ASCII "FMK2SN67". Very close to ant 1 |
| 464D4B32534E3638 | 64 | −45 | −24 | ASCII "FMK2SN68". Very close to ant 2 |
| E280116060000207C82A0004 | 96 | −61 | – | Impinj chip (E2801160) |
| 0E3322810009661700000000 | 96 | −62 | – | |
| 0E3322899990001000000000 | 96 | −66 | – | |
| 0E3322899990003600000000 | 96 | −67 | −64 | |
| 0E3322899990002800000000 | 96 | −72 | −72 | weak, marginal |
| E280116060000207C829CC44 | 96 | −62 | – | session 2 only, not read in any session-0 run |
| E280116060000207C829CC54 | 96 | −64 | – | session 2, plus 1 read in session-0 run 1 |
| E280116060000207C829CCF4 | 96 | – | −62 | ant 2 only |

Note for step 2: the EPC lengths differ (64-bit and 96-bit), so the SIP2 or item-ID mapping must not assume 96 bits.

## Consistency – antenna 1, session 0, 3 × 30 s (tags left untouched)
| EPC | Run 1 reads/s | Run 2 | Run 3 | RSSI range |
|---|---|---|---|---|
| 464D4B32534E3637 | 6.8 | 6.4 | 7.1 | −26..−25 |
| 464D4B32534E3638 | 4.5 | 4.6 | 5.0 | −47..−43 |
| 0E3322810009661700000000 | 1.6 | 2.1 | 1.7 | −64..−61 |
| E280116060000207C82A0004 | 1.8 | 1.7 | 1.2 | −62..−59 |
| 0E3322899990001000000000 | 0.9 | 1.2 | 0.9 | −68..−65 |
| 0E3322899990003600000000 | 0.6 | 0.8 | 0.9 | −69..−65 |
| 0E3322899990002800000000 | 0.1 | 0.0 | 0.2 | −73..−71 (marginal) |

**Result:** 6 tags were read in every run, at similar rates, with RSSI spreads of 4 dB or less. **PASS** for those 6.
The weak or edge tags (…0028, …CC44, …CC54) came and went, which points to tag placement or orientation rather than a reader fault.

## Antenna 2 – session 0, 30 s – PASS
Read 5 tags at 0.1–9.5 reads/s with tight RSSI (≤4 dB spread). The port and its cable work.

## Still to do (needs someone at the reader)
Put the 3 chosen test tags in a fixed spot. Move the FMK2SN67/68 tags and any other tags out of the field, or ignore them. Then run 3×:

    & "C:\Users\keanu\AppData\Local\Programs\Python\Python312\python.exe" .\step1_read_tags.py 192.168.1.3 --seconds 30 --session 0 --quiet --expect <EPC1> <EPC2> <EPC3>

## Notes
- Read rates are modest (about 16–25 reads/s in total at session 0). This is fine for Step 1. If check-in or out feels slow later, tune the RF mode (`mode_identifier`) or report batching.
- If a run hangs: `sllurp reset 192.168.1.3` (only one LLRP client is allowed at a time).
