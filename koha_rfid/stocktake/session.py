"""Collect tag reads during a stocktake, section by section, and write the results.

A section is whatever the person scanning calls it ("Shelf 12A", "Junior
fiction bay 3"). The reader keeps running between sections. Tags read while no
section is open (while walking up to a shelf and typing its name) count towards
the **next** section opened; if none follows, they stay UNASSIGNED. Nothing
read is ever dropped.

Each tag is counted in the section where it was **first** read. If it shows up
again in a later section it stays there, but reads.csv lists every section it
was seen in, so misplaced or wandering books can be traced.

Output (write()):
    barcodes-all.txt        every library barcode, one per line: upload this to
                            Koha > Tools > Inventory
    barcodes-NN-<name>.txt  the same, per section (for an inventory per shelf
                            or location)
    reads.csv               one row per tag: section, EPC, barcode, reads, RSSI ...
    foreign.txt             EPCs that aren't library tags (blank tags, ID cards, ...)
    summary.txt             counts per section
"""

from __future__ import annotations

import csv
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..epc import normalise_epc, try_decode_epc

UNASSIGNED = "(not in a section)"


@dataclass
class TagRecord:
    epc: str
    barcode: Optional[str]          # None: not a library tag
    section: str                    # where it was first read
    antenna: int
    best_rssi: int
    first_seen: float
    last_seen: float
    reads: int = 1
    sections: List[str] = field(default_factory=list)   # every section it was read in


@dataclass
class SectionSummary:
    name: str
    library: int        # library tags first read in this section
    foreign: int        # other tags first read in this section
    seen_before: int    # library tags read here that were already counted in an earlier section
    seconds: float


class StocktakeSession:
    """Thread-safe: on_epc() is called from the reader's thread."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._tags: Dict[str, TagRecord] = {}
        self.sections: List[str] = []           # in the order they were scanned
        self.current: Optional[str] = None
        self._started = 0.0
        self.summaries: List[SectionSummary] = []

    # ---- sections ------------------------------------------------------------
    def start_section(self, name: str) -> str:
        """Open a section (ending any open one). Returns the name actually used:
        blank becomes "Section N", and a repeated name gets " (2)", " (3)" ..."""
        with self._lock:
            if self.current is not None:
                self._end_locked()
            name = " ".join((name or "").split()) or f"Section {len(self.sections) + 1}"
            base, n = name, 2
            while name in self.sections or name == UNASSIGNED:
                name, n = f"{base} ({n})", n + 1
            self.sections.append(name)
            self.current = name
            for t in self._tags.values():      # read on the way here: this section's
                if t.section == UNASSIGNED:
                    t.section = name
                if UNASSIGNED in t.sections:
                    t.sections = [name if x == UNASSIGNED else x for x in t.sections]
                    t.sections = _unique(t.sections)
            self._started = self._clock()
            return name

    def end_section(self) -> Optional[SectionSummary]:
        """Close the open section and return its counts (None if none was open)."""
        with self._lock:
            return self._end_locked()

    def _end_locked(self) -> Optional[SectionSummary]:
        name = self.current
        if name is None:
            return None
        first = [t for t in self._tags.values() if t.section == name]
        s = SectionSummary(
            name=name,
            library=sum(1 for t in first if t.barcode),
            foreign=sum(1 for t in first if not t.barcode),
            seen_before=sum(1 for t in self._tags.values()
                            if t.barcode and t.section != name and name in t.sections),
            seconds=round(self._clock() - self._started, 1))
        self.summaries.append(s)
        self.current = None
        return s

    # ---- reads -----------------------------------------------------------------
    def on_epc(self, epc: str, antenna: int, rssi: int) -> None:
        epc = normalise_epc(epc)
        now = self._clock()
        with self._lock:
            section = self.current or UNASSIGNED
            t = self._tags.get(epc)
            if t is None:
                self._tags[epc] = TagRecord(epc, try_decode_epc(epc), section, antenna, rssi,
                                            now, now, sections=[section])
                return
            t.reads += 1
            t.last_seen = now
            if rssi > t.best_rssi:
                t.best_rssi, t.antenna = rssi, antenna
            if section not in t.sections:
                t.sections.append(section)

    def counts(self) -> Dict[str, int]:
        """Live counts for the screen: library tags and other tags, in total and
        first read in the open section."""
        with self._lock:
            tags = list(self._tags.values())
            cur = self.current
        return {"library": sum(1 for t in tags if t.barcode),
                "foreign": sum(1 for t in tags if not t.barcode),
                "section": sum(1 for t in tags if t.barcode and t.section == cur)}

    def tags(self) -> List[TagRecord]:
        with self._lock:
            return sorted(self._tags.values(), key=lambda t: (t.first_seen, t.epc))

    # ---- output ----------------------------------------------------------------
    def write(self, out_dir: str) -> List[str]:
        """Write the result files into out_dir (created if needed). Returns their paths."""
        with self._lock:
            if self.current is not None:
                self._end_locked()
            tags = sorted(self._tags.values(), key=lambda t: (t.first_seen, t.epc))
            sections = list(self.sections)
            summaries = list(self.summaries)
        if any(t.section == UNASSIGNED for t in tags):
            sections.append(UNASSIGNED)
        os.makedirs(out_dir, exist_ok=True)
        written: List[str] = []

        def lines(name: str, items) -> None:
            path = os.path.join(out_dir, name)
            # Plain "\n": Koha's Inventory upload reads one barcode per line.
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                for item in items:
                    fh.write(f"{item}\n")
            written.append(path)

        lines("barcodes-all.txt", _unique(t.barcode for t in tags if t.barcode))
        for i, sec in enumerate(sections, 1):
            barcodes = _unique(t.barcode for t in tags if t.barcode and t.section == sec)
            if barcodes:
                lines(f"barcodes-{i:02d}-{_slug(sec)}.txt", barcodes)

        path = os.path.join(out_dir, "reads.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["section", "epc", "barcode", "reads", "best_rssi", "antenna",
                        "first_seen", "last_seen", "also_seen_in"])
            for t in tags:
                w.writerow([t.section, t.epc, t.barcode or "", t.reads, t.best_rssi, t.antenna,
                            _iso(t.first_seen), _iso(t.last_seen),
                            "; ".join(s for s in t.sections if s != t.section)])
        written.append(path)

        lines("foreign.txt", [t.epc for t in tags if not t.barcode])

        summary = [f"{'Section':<30} {'books':>6} {'other':>6} {'counted earlier':>16} {'seconds':>8}"]
        for s in summaries:
            summary.append(f"{s.name:<30} {s.library:>6} {s.foreign:>6} {s.seen_before:>16} {s.seconds:>8}")
        between = [t for t in tags if t.section == UNASSIGNED]
        if between:
            summary.append(f"{UNASSIGNED:<30} {sum(1 for t in between if t.barcode):>6} "
                           f"{sum(1 for t in between if not t.barcode):>6}")
        summary.append("")
        summary.append(f"Library tags (unique barcodes): {len(_unique(t.barcode for t in tags if t.barcode))}")
        summary.append(f"Other tags: {sum(1 for t in tags if not t.barcode)}")
        lines("summary.txt", summary)
        return written


def _unique(items) -> List[str]:
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:40] or "section"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
