#!/usr/bin/env python3
"""Summarise item barcode formats in a Koha database.

Reads tab-separated "barcode<TAB>homebranch" lines on stdin, as printed by:

    sudo koha-mysql middelburg --batch -e "SELECT barcode, homebranch FROM items" \
      | python3 tools/barcode_report.py

Prints only counts and branch codes. It never prints barcodes, so the output
is safe to paste into an issue or doc.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from koha_rfid.barcode import KIND_STANDARD, classify, lookup_candidates  # noqa: E402

SEP = re.compile(r"[\s\-\"'.]")


def main() -> None:
    rows = []
    for i, line in enumerate(sys.stdin):
        line = line.rstrip("\n")
        if i == 0 and line.lower().startswith("barcode\t"):
            continue  # mysql --batch header
        bc, _, branch = line.partition("\t")
        rows.append((None if bc == "NULL" else bc, branch or "?"))

    existing = collections.Counter(b for b, _ in rows if b)
    kinds = collections.Counter()
    per_branch = collections.defaultdict(collections.Counter)
    for b, br in rows:
        k = classify(b).kind
        kinds[k] += 1
        per_branch[br][k] += 1

    total = len(rows)
    print(f"items: {total}")
    print("\nformat                         items      %")
    for k, n in kinds.most_common():
        print(f"{k:28s} {n:8d}  {100 * n / total:5.1f}")

    dup = sum(1 for n in existing.values() if n > 1)
    ws = sum(1 for b in existing if b != b.strip())
    shadowed = 0
    for b in existing:
        for alt in lookup_candidates(b)[1:]:
            if alt in existing:
                shadowed += 1
                break
    print(f"\nexact duplicate barcodes:           {dup}")
    print(f"barcodes with surrounding spaces:   {ws}")
    print(f"barcodes whose repaired form is a different item: {shadowed}")

    print("\nbranches where under 80% of items are standard-14:")
    for br, c in sorted(per_branch.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        share = c[KIND_STANDARD] / n
        if n >= 100 and share < 0.8:
            top = ", ".join(f"{k} {v}" for k, v in c.most_common(3))
            print(f"  {br:8s} {n:7d} items, {100 * share:5.1f}% standard  ({top})")


if __name__ == "__main__":
    main()
