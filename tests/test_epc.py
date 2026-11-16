import unittest

from koha_rfid.barcode import KIND_STANDARD, classify, make_barcode
from koha_rfid.epc import NotLibraryTag, decode_epc, encode_epc, try_decode_epc

# EPCs read from the R220 in step 1 (2026-11-16), with the barcodes their
# labels were printed with (tools/print_rfid_labels.ps1).
KNOWN_PAIRS = {
    "0E3322810009661700000000": "33228100096617",
    "0E3322899990001000000000": "33228999900010",
    "0E3322899990002800000000": "33228999900028",
    "0E3322899990003600000000": "33228999900036",
}

# Other tags seen in step 1 that are not library items.
FOREIGN = [
    "464D4B32534E3637",          # 64-bit, ASCII "FMK2SN67"
    "464D4B32534E3638",          # 64-bit, ASCII "FMK2SN68"
    "E280116060000207C82A0004",  # factory-blank Impinj Monza tags
    "E280116060000207C829CC44",
    "E280116060000207C829CC54",
    "E280116060000207C829CCF4",
]


class KnownTags(unittest.TestCase):
    def test_step1_tags_decode_to_their_barcodes(self):
        for epc, barcode in KNOWN_PAIRS.items():
            self.assertEqual(decode_epc(epc), barcode)

    def test_decoded_barcodes_are_valid_standard(self):
        # A wrong decode (byte order, offset, hex vs ASCII) would almost
        # never give a correct Luhn check digit.
        for epc in KNOWN_PAIRS:
            self.assertEqual(classify(decode_epc(epc)).kind, KIND_STANDARD, epc)

    def test_encode_matches_what_the_printer_wrote(self):
        for epc, barcode in KNOWN_PAIRS.items():
            self.assertEqual(encode_epc(barcode), epc)

    def test_foreign_tags_are_rejected(self):
        for epc in FOREIGN:
            with self.assertRaises(NotLibraryTag, msg=epc):
                decode_epc(epc)
            self.assertIsNone(try_decode_epc(epc))


class Decode(unittest.TestCase):
    def test_input_formatting_is_tolerated(self):
        for s in ("0e3322810009661700000000", " 0E33 2281 0009 6617 0000 0000\n",
                  "0x0E3322810009661700000000", "0E-3322-8100-0966-1700-0000-00"):
            self.assertEqual(decode_epc(s), "33228100096617", s)

    def test_leading_zeros_are_kept(self):
        self.assertEqual(decode_epc(encode_epc("0799342823")), "0799342823")

    def test_other_epc_sizes(self):
        self.assertEqual(decode_epc("0631234500000000"), "312345")            # 64-bit
        b = "1234567890123456789012345678"
        self.assertEqual(decode_epc(encode_epc(b, bits=128)), b)              # 128-bit

    def test_rejects(self):
        bad = [
            "",                              # empty
            "000000000000000000000000",      # length 0 (blank / erased tag)
            "0E33228100096617000000",        # not whole 16-bit words
            "0E3322810009661700000001",      # junk in the padding
            "0E33228100096A1700000000",      # hex letter in the digits
            "173322810009661700000000",      # length 23 > 22 digits that fit
            "ZZ3322810009661700000000",      # not hex
        ]
        for s in bad:
            with self.assertRaises(NotLibraryTag, msg=s):
                decode_epc(s)


class Encode(unittest.TestCase):
    def test_round_trip(self):
        for n in (0, 1, 96617, 99999999):
            b = make_barcode(n)
            self.assertEqual(decode_epc(encode_epc(b)), b)

    def test_limits(self):
        encode_epc("1" * 22)
        with self.assertRaises(ValueError):
            encode_epc("1" * 23)
        for b in ("", "GM048150", "3322 8100"):
            with self.assertRaises(ValueError):
                encode_epc(b)


if __name__ == "__main__":
    unittest.main()
