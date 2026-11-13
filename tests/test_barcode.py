import unittest

from koha_rfid.barcode import (
    KIND_BAD_CHECK, KIND_EMPTY, KIND_ISBN, KIND_NO_CHECK, KIND_OTHER,
    KIND_SEPARATORS, KIND_SHORT, KIND_STANDARD,
    check_digit, classify, is_valid_standard, lookup_candidates, make_barcode,
)


class CheckDigit(unittest.TestCase):
    def test_known_luhn_value(self):
        # Standard Luhn example: 7992739871 -> check digit 3, zero-padded to 13 digits.
        self.assertEqual(check_digit("0007992739871"), "3")

    def test_make_barcode_is_valid(self):
        for n in (0, 1, 1234567, 99999999):
            b = make_barcode(n)
            self.assertEqual(len(b), 14)
            self.assertTrue(b.startswith("33228"))
            self.assertTrue(is_valid_standard(b), b)

    def test_any_single_digit_error_is_caught(self):
        b = make_barcode(1234567)
        for i in range(14):
            for d in "0123456789":
                if d != b[i]:
                    self.assertFalse(is_valid_standard(b[:i] + d + b[i + 1:]))

    def test_payload_must_be_13_digits(self):
        with self.assertRaises(ValueError):
            check_digit("12345")


class Classify(unittest.TestCase):
    STD = make_barcode(10009661)  # 33228100096617 -- the SIP test item, check digit added

    def test_standard(self):
        b = classify(self.STD)
        self.assertEqual((b.kind, b.standard, b.institution, b.item_number),
                         (KIND_STANDARD, self.STD, "3228", "10009661"))

    def test_separators(self):
        for raw in ("33228 10009661 7", "3 3228 10009661 7", "33228-100096617",
                    self.STD + '"', " 33228 1000 9661 7 "):
            b = classify(raw)
            self.assertEqual((b.kind, b.standard), (KIND_SEPARATORS, self.STD), raw)

    def test_no_check_digit(self):
        b = classify("3322810009661")  # stored this way in Koha, tested over SIP
        self.assertEqual((b.kind, b.standard), (KIND_NO_CHECK, self.STD))

    def test_13_digits_unknown_institution_is_not_repaired(self):
        self.assertEqual(classify("3999910009661").kind, KIND_OTHER)

    def test_isbn(self):
        self.assertEqual(classify("9780553213119").kind, KIND_ISBN)

    def test_bad_check(self):
        bad = self.STD[:-1] + str((int(self.STD[-1]) + 1) % 10)
        self.assertEqual(classify(bad).kind, KIND_BAD_CHECK)

    def test_short_other_empty(self):
        self.assertEqual(classify("12345").kind, KIND_SHORT)
        self.assertEqual(classify("GM 048 150").kind, KIND_OTHER)
        self.assertEqual(classify("").kind, KIND_EMPTY)
        self.assertEqual(classify(None).kind, KIND_EMPTY)


class Candidates(unittest.TestCase):
    STD = make_barcode(10009661)

    def test_exact_first(self):
        self.assertEqual(lookup_candidates("33228 10009661 7\r\n")[0], "33228 10009661 7")

    def test_standard_tag_also_tries_13_digit_form(self):
        self.assertEqual(lookup_candidates(self.STD), [self.STD, self.STD[:13]])

    def test_13_digit_tries_standard(self):
        self.assertEqual(lookup_candidates("3322810009661"), ["3322810009661", self.STD])

    def test_separators(self):
        self.assertEqual(lookup_candidates("33228-100096617"),
                         ["33228-100096617", self.STD, self.STD[:13]])

    def test_unknown_format_only_exact_and_cleaned(self):
        self.assertEqual(lookup_candidates("GM 048 150"), ["GM 048 150", "GM048150"])
        self.assertEqual(lookup_candidates("12345"), ["12345"])


if __name__ == "__main__":
    unittest.main()
