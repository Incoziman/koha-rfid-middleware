"""EPC <-> item barcode, length-prefixed digit encoding (docs/reference/uhf-rfid.md).

    0E 33228100096617 00000000
    |  |              +-- zero padding up to the tag's EPC size (96 bits here)
    |  +----------------- the barcode digits, one hex nibble per digit
    +-------------------- number of digits, one byte (0E = 14)

This is the encoding testing/print_rfid_labels.ps1 writes. Step 1 confirmed it
reads back unchanged from the R220 (2026-11-16).

decode_epc() is strict on purpose: the UHF reader also sees tags that are not
library items (factory-blank Impinj tags "E280...", other people's tags), and
these must be ignored, not turned into a barcode by accident. Anything that
does not decode exactly raises NotLibraryTag.

The decoded barcode is returned exactly as stored on the tag. It is not
repaired or validated against the 14-digit standard, because some real Koha
barcodes are non-standard (see barcode.py). Pass it to
barcode.lookup_candidates() as you would a scanned barcode.

No third-party dependencies; works on Python 3.8+.
"""

from __future__ import annotations

import re
from typing import Optional

_HEX = re.compile(r"^[0-9A-F]+$")


class NotLibraryTag(ValueError):
    """The EPC is not a barcode in our encoding (foreign tag, blank tag, bad read)."""


def normalise_epc(epc: str) -> str:
    """Upper-case hex with no spaces, dashes or 0x prefix."""
    s = epc.strip().upper()
    if s.startswith("0X"):
        s = s[2:]
    return re.sub(r"[\s\-:]", "", s)


def decode_epc(epc: str) -> str:
    """Return the barcode held in an EPC, or raise NotLibraryTag.

    Works for any EPC size (64, 96, 128 bits, ...): the length byte says how
    many digits to take, and everything after them must be zero.
    """
    s = normalise_epc(epc)
    if not s or not _HEX.match(s) or len(s) % 4:
        # EPCs are whole 16-bit words
        raise NotLibraryTag(f"not a hex EPC: {epc!r}")
    n = int(s[:2], 16)
    body = s[2:]
    if n == 0 or n > len(body):
        raise NotLibraryTag(f"length byte {n} does not fit a {len(s) * 4}-bit EPC: {s}")
    digits, padding = body[:n], body[n:]
    if not digits.isdigit():
        raise NotLibraryTag(f"non-digit in barcode part {digits!r}: {s}")
    if padding.strip("0"):
        raise NotLibraryTag(f"padding is not all zeros ({padding}): {s}")
    return digits


def try_decode_epc(epc: str) -> Optional[str]:
    """decode_epc(), but returns None for tags that aren't ours."""
    try:
        return decode_epc(epc)
    except NotLibraryTag:
        return None


def encode_epc(barcode: str, bits: int = 96) -> str:
    """EPC hex for a numeric barcode. Same output as Get-Epc in print_rfid_labels.ps1."""
    if not barcode or not barcode.isdigit():
        raise ValueError(f"only numeric barcodes can use this encoding: {barcode!r}")
    if bits % 16 or bits < 32:
        raise ValueError("bits must be a multiple of 16, at least 32")
    width = bits // 4
    if len(barcode) > width - 2:
        raise ValueError(f"{len(barcode)} digits don't fit a {bits}-bit EPC "
                         f"(max {width - 2}); use a larger EPC")
    return (f"{len(barcode):02X}{barcode}").ljust(width, "0")
