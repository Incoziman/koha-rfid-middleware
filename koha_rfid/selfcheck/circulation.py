"""Check-out / check-in over SIP2: plan, send, verify, log.

The building blocks of the self-check flow (docs/selfcheck/selfcheck-sip2.md),
first proven by testing/step5_circulate.py against the test instance on
2026-11-16. Rules that came out of that testing:

- Check-out skips items already on loan: in Koha a second 11 for the same
  patron is a renewal.
- Check-in skips items that aren't on loan: an 09 for an item on the shelf can
  still trigger holds or transfers.
- Every 11 / 09 is followed by a 17 to confirm Koha's status really changed.
- A write is never resent after a dropped connection (Koha may have applied it
  already); the 17 afterwards says whether it did.
- Card numbers are often SA ID numbers (POPIA): logs keep only the last 3
  characters, and patron names are never logged.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .. import sip2
from ..epc import NotLibraryTag, decode_epc

CHECKOUT = "checkout"
CHECKIN = "checkin"
ON_LOAN = "04"

# SIP fields that can carry personal data: patron name, hold patron name/id,
# address, email, phone.
_PERSONAL = ("AE", "DA", "CY", "BD", "BE", "BF")


def mask(card: str) -> str:
    """Keep only the last 3 characters of a card number."""
    return "*" * max(len(card) - 3, 0) + card[-3:]


def scrub(reply: str) -> str:
    """Raw SIP reply for a log: personal fields dropped, card numbers masked."""
    parts = []
    for p in reply.split("|"):
        if p[:2] in _PERSONAL:
            continue
        parts.append("AA" + mask(p[2:]) if p[:2] == "AA" else p)
    return "|".join(parts)


def decode_tags(epcs: Iterable[str]) -> Tuple[List[Tuple[str, str]], int]:
    """EPCs read from the pad -> ([(barcode, epc)], number of non-library tags skipped)."""
    out, skipped = [], 0
    for epc in sorted(epcs):
        try:
            out.append((decode_epc(epc), epc))
        except NotLibraryTag:
            skipped += 1
    return out, skipped


@dataclass(frozen=True)
class Planned:
    item: sip2.ItemInfo
    epc: Optional[str]


@dataclass(frozen=True)
class Skipped:
    tag_barcode: str
    reason: str


def plan(client, institution: str, action: str,
         tags: Iterable[Tuple[str, Optional[str]]]) -> Tuple[List[Planned], List[Skipped]]:
    """Look up each (tag barcode, epc) with 17 and decide what to do with it.

    Duplicates are dropped, both the same tag barcode twice and two tags that
    resolve to the same Koha item.
    """
    if action not in (CHECKOUT, CHECKIN):
        raise ValueError(f"unknown action {action!r}")
    todo: List[Planned] = []
    skipped: List[Skipped] = []
    seen_tags, seen_items = set(), set()
    for tag_bc, epc in tags:
        tag_bc = tag_bc.strip()
        if not tag_bc or tag_bc in seen_tags:
            continue
        seen_tags.add(tag_bc)
        item = sip2.lookup_item(client, institution, tag_bc)
        if item is None:
            skipped.append(Skipped(tag_bc, "not in Koha"))
        elif item.barcode in seen_items:
            skipped.append(Skipped(tag_bc, f"same item as {item.barcode}, already listed"))
        elif action == CHECKOUT and item.circulation_status == ON_LOAN:
            skipped.append(Skipped(tag_bc, f"{item.title!r} is already on loan - check it in first"))
        elif action == CHECKIN and item.circulation_status != ON_LOAN:
            skipped.append(Skipped(tag_bc, f"{item.title!r} is not on loan ({item.status_text})"))
        else:
            seen_items.add(item.barcode)
            todo.append(Planned(item, epc))
    return todo, skipped


@dataclass(frozen=True)
class Outcome:
    action: str
    planned: Planned
    result: Optional[sip2.CircResult]   # None: the connection dropped mid-transaction
    error: str                          # set when result is None
    after: Optional[sip2.ItemInfo]      # 17 after the transaction
    verified: bool                      # Koha's status changed as intended

    @property
    def ok(self) -> bool:
        """Done, by Koha's own status, whether or not the reply arrived."""
        return self.verified and (self.result is None or self.result.ok)

    def log_entry(self, **context) -> Dict:
        it = self.planned.item
        e = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "action": self.action, "barcode": it.barcode, "tag_barcode": it.tag_barcode,
            "epc": self.planned.epc, "title": it.title,
            "status_before": it.circulation_status,
        }
        e.update(context)
        if self.result is not None:
            r = self.result
            e.update(ok=r.ok, message=r.message, due_date=r.due_date, security=r.security,
                     alert=r.alert, alert_type=r.alert_type, reply=scrub(r.raw))
        else:
            e.update(ok=None, message=f"connection lost during transaction: {self.error}")
        e["status_after"] = self.after.circulation_status if self.after else None
        e["verified"] = self.verified
        return e


def run(client, institution: str, action: str, planned: Planned, card: str = "",
        pin: str = "", location: str = "") -> Outcome:
    """Send one 11 / 09, then confirm with 17. Never resends the write."""
    barcode = planned.item.barcode
    result, error = None, ""
    try:
        if action == CHECKOUT:
            result = sip2.do_checkout(client, institution, card, barcode, pin)
        else:
            result = sip2.do_checkin(client, institution, location or institution, barcode)
    except (OSError, ConnectionError) as e:
        error = str(e) or type(e).__name__
    after = sip2.lookup_item(client, institution, barcode)
    on_loan = after is not None and after.circulation_status == ON_LOAN
    verified = after is not None and on_loan == (action == CHECKOUT)
    return Outcome(action, planned, result, error, after, verified)


class TransactionLog:
    """Append-only JSONL log, one line per transaction. Card numbers masked."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def write(self, outcome: Outcome, card: str = "", **context) -> Dict:
        entry = outcome.log_entry(**context)
        if card:
            entry["patron"] = mask(card)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
