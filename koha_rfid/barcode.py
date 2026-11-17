"""Item barcode handling for the Middelburg / Mpumalanga Koha data.

The standard format is the 14-digit Codabar library barcode:

    3 3228 01234567 C
    | |    |        +-- check digit (Luhn, a.k.a. "library mod 10")
    | |    +----------- 8-digit item number
    | +---------------- 4-digit institution code (3228 for most of the consortium)
    +------------------ type digit: 3 = item

About 75% of items match it exactly. Many others are the same number stored
in a damaged form: separators typed in, or the check digit dropped.
docs/reference/barcode-format.md has the numbers.

The rule for the middleware: send Koha the exact string first. Only if Koha
says the item is unknown, try the repaired forms from `lookup_candidates`.
Never send only a repaired form, because some damaged and repaired forms
exist in Koha as two different items.

No third-party dependencies; works on Python 3.8+.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

ITEM_TYPE_DIGIT = "3"

# Institution codes seen on at least 50 valid item barcodes in the
# 2026-09-14 dump. 3228 covers ~95% of valid barcodes.
KNOWN_INSTITUTIONS = frozenset({
    "3228", "3089", "3809", "3909", "3010", "3003",
    "3001", "3006", "3050", "3005", "1050", "3004",
})

# Characters that crept into stored barcodes and are never part of the number.
_SEPARATORS = re.compile(r"[\s\-\"'.]")

KIND_STANDARD = "standard-14"                 # exact, valid 14-digit
KIND_SEPARATORS = "standard-with-separators"  # valid 14-digit after removing spaces/hyphens/quotes
KIND_NO_CHECK = "13-no-check-digit"           # 3 + known institution + 8-digit item, check digit missing
KIND_BAD_CHECK = "14-bad-check-digit"         # 14 digits, check digit wrong (not standard-numbered)
KIND_ISBN = "isbn-ean"                        # a book's ISBN/EAN-13 used as the barcode
KIND_SHORT = "short-numeric"                  # legacy local numbers, mostly 5-6 digits
KIND_OTHER = "other"                          # anything else, e.g. alphanumeric "GM 048 150"
KIND_EMPTY = "empty"


def luhn_sum(digits: str) -> int:
    """Luhn sum, doubling every second digit counted from the right (the last digit is not doubled)."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        x = int(ch)
        if i % 2:
            x = x * 2 - 9 if x > 4 else x * 2
        total += x
    return total


def check_digit(payload: str) -> str:
    """Check digit for a 13-digit payload (type digit + institution + item number)."""
    if len(payload) != 13 or not payload.isdigit():
        raise ValueError(f"payload must be 13 digits: {payload!r}")
    return str((10 - luhn_sum(payload + "0") % 10) % 10)


def make_barcode(item_number: int, institution: str = "3228") -> str:
    """Build a new standard barcode, e.g. for test tags. item_number must fit in 8 digits."""
    if not 0 <= item_number <= 99_999_999:
        raise ValueError("item_number must be 0..99999999")
    payload = f"{ITEM_TYPE_DIGIT}{institution}{item_number:08d}"
    return payload + check_digit(payload)


def is_valid_standard(barcode: str) -> bool:
    """True for a well-formed 14-digit item barcode with a correct check digit."""
    return (
        len(barcode) == 14
        and barcode.isdigit()
        and barcode[0] == ITEM_TYPE_DIGIT
        and luhn_sum(barcode) % 10 == 0
    )


@dataclass(frozen=True)
class Barcode:
    raw: str
    kind: str                       # one of the KIND_* constants
    standard: Optional[str] = None  # the valid 14-digit form, when it can be derived
    institution: Optional[str] = None
    item_number: Optional[str] = None


def _std(raw: str, kind: str, std: str) -> Barcode:
    return Barcode(raw, kind, std, institution=std[1:5], item_number=std[5:13])


def classify(raw: Optional[str]) -> Barcode:
    """Work out which of the known formats a stored or scanned barcode is in."""
    if raw is None or not raw.strip():
        return Barcode(raw or "", KIND_EMPTY)
    s = raw.strip()
    if is_valid_standard(s):
        return _std(raw, KIND_STANDARD, s)
    cleaned = _SEPARATORS.sub("", s)
    if cleaned != s and is_valid_standard(cleaned):
        return _std(raw, KIND_SEPARATORS, cleaned)
    if not cleaned.isdigit():
        return Barcode(raw, KIND_OTHER)
    if len(cleaned) == 13 and cleaned.startswith(("978", "979")):
        return Barcode(raw, KIND_ISBN)
    if (len(cleaned) == 13 and cleaned[0] == ITEM_TYPE_DIGIT
            and cleaned[1:5] in KNOWN_INSTITUTIONS):
        # A missing check digit can't be confirmed (any 13 digits can be
        # completed), but the type digit + known institution prefix makes it
        # very likely, and it's the most common damaged form.
        return _std(raw, KIND_NO_CHECK, cleaned + check_digit(cleaned))
    if len(cleaned) == 14:
        return Barcode(raw, KIND_BAD_CHECK)
    if len(cleaned) <= 12:
        return Barcode(raw, KIND_SHORT)
    return Barcode(raw, KIND_OTHER)


def lookup_candidates(scanned: str) -> List[str]:
    """Strings to try in Koha, in order, for one scanned or tag-read barcode.

    The first entry is always the input exactly as read (only surrounding
    whitespace and CR/LF trimmed). The rest are repaired forms. Try them only
    after Koha reports the first as unknown, in this order, and stop at the
    first hit. No duplicates.
    """
    first = scanned.strip()
    out = [first]

    def add(x: Optional[str]) -> None:
        if x and x not in out:
            out.append(x)

    cleaned = _SEPARATORS.sub("", first)
    add(cleaned)
    add(classify(first).standard)
    # The reverse repair: a valid 14-digit tag for an item stored in Koha
    # without its check digit (the 69k "13-no-check-digit" items).
    if is_valid_standard(cleaned):
        add(cleaned[:13])
    return out
