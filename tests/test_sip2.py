import unittest

from koha_rfid import sip2


class Checksum(unittest.TestCase):
    # Request/reply pairs captured from the middelburg SIP server on 2026-11-13.
    def test_requests(self):
        self.assertEqual(sip2.with_error_detection("9900302.00", 1), "9900302.00AY1AZFCA5")
        self.assertEqual(sip2.checksum("941AY0AZ"), "FDFD")

    def test_verify_replies(self):
        self.assertTrue(sip2.verify("941AY0AZFDFD"))
        self.assertTrue(sip2.verify(
            "1803020120261113    125444AB3322810009661|AJSonder gister se spoor |"
            "CK001|AQMIDDL|BGMIDDL|AY2AZE621"))
        self.assertFalse(sip2.verify("941AY0AZ0000"))
        self.assertTrue(sip2.verify("941"))  # no checksum at all


class Parse(unittest.TestCase):
    def test_item_found(self):
        cmd, fixed, f = sip2.parse(
            "1803020120261113    125444AB3322810009661|AJSonder gister se spoor |"
            "CK001|AQMIDDL|BGMIDDL|AY2AZE621")
        self.assertEqual(cmd, "18")
        self.assertEqual(fixed[:2], "03")            # available
        self.assertEqual(fixed[2:4], "02")           # security marker: tattle-tape
        self.assertEqual(f["AB"], ["3322810009661"])
        self.assertEqual(f["AY"], ["2"])
        self.assertTrue(sip2.item_found(f))

    def test_item_unknown(self):
        _, fixed, f = sip2.parse("1801010120261113    125444AB00000000000000|AJ|AY3AZF522")
        self.assertFalse(sip2.item_found(f))

    def test_status(self):
        cmd, fixed, f = sip2.parse(
            "98YYYYNN10000520261113    1254442.00AOMIDDL|BXYYYYYYYYYYYNYYYY|AY1AZEDEE")
        self.assertEqual(cmd, "98")
        self.assertEqual(fixed[:6], "YYYYNN")
        self.assertEqual(f["AO"], ["MIDDL"])


class Builders(unittest.TestCase):
    def test_fixed_widths(self):
        self.assertEqual(len(sip2.timestamp()), 18)
        m = sip2.checkout("MIDDL", "CARD", "BC")
        self.assertTrue(m.startswith("11NN"))
        self.assertEqual(m[4 + 18:4 + 36], " " * 18)
        self.assertTrue(sip2.checkin("MIDDL", "MIDDL", "BC").startswith("09N"))


class FakeClient:
    """Answers 17s from a dict {barcode: reply}; anything else gets the unknown-item reply."""
    UNKNOWN = "1801010120261113    125444AB{}|AJ|"

    def __init__(self, known):
        self.known, self.asked = known, []

    def send(self, msg):
        bc = sip2.parse(msg)[2]["AB"][0]
        self.asked.append(bc)
        return self.known.get(bc, self.UNKNOWN.format(bc))


class LookupItem(unittest.TestCase):
    FOUND = ("1803020120261113    125444AB3322810009661|AJSonder gister se spoor |"
             "CK001|AQMIDDL|BGMIDDL|")

    def test_tag_with_check_digit_finds_item_stored_without(self):
        # Step 4's real case: the tag holds 33228100096617, Koha has 3322810009661.
        c = FakeClient({"3322810009661": self.FOUND})
        item = sip2.lookup_item(c, "MIDDL", "33228100096617")
        self.assertEqual(c.asked, ["33228100096617", "3322810009661"])   # exact first
        self.assertEqual(item.barcode, "3322810009661")
        self.assertTrue(item.repaired)
        self.assertEqual(item.title, "Sonder gister se spoor")
        self.assertEqual(item.status_text, "available")
        self.assertEqual(item.permanent_location, "MIDDL")

    def test_exact_hit_stops_there(self):
        c = FakeClient({"33228100096617": self.FOUND})
        item = sip2.lookup_item(c, "MIDDL", "33228100096617")
        self.assertEqual(c.asked, ["33228100096617"])
        self.assertFalse(item.repaired)

    def test_unknown_everywhere(self):
        c = FakeClient({})
        self.assertIsNone(sip2.lookup_item(c, "MIDDL", "33228999900010"))
        self.assertEqual(c.asked, ["33228999900010", "3322899990001"])


if __name__ == "__main__":
    unittest.main()
