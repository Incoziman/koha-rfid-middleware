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


class Canned:
    """Returns one fixed reply; records which send method was used."""

    def __init__(self, reply, with_send_once=False):
        self.reply, self.used, self.sent = reply, [], []
        if with_send_once:
            self.send_once = lambda m: self._go("send_once", m)

    def _go(self, how, msg):
        self.used.append(how)
        self.sent.append(msg)
        return self.reply

    def send(self, msg):
        return self._go("send", msg)


DATE = "20261116    120000"


class Patron(unittest.TestCase):
    def test_unknown_card_captured(self):
        c = Canned("24YYYY          00020261113    125444AE|AA999|BLN|"
                   "AFInvalid cardnumber|AOMIDDL|")
        p = sip2.get_patron_status(c, "MIDDL", "999")
        self.assertFalse(p.valid)
        self.assertEqual(p.refusal(pin_required=False), "Invalid cardnumber")

    def test_valid_patron(self):
        c = Canned("24" + " " * 14 + "000" + DATE + "AOMIDDL|AA64|AEsipterm1|BLY|CQY|")
        p = sip2.get_patron_status(c, "MIDDL", "64", "1234")
        self.assertTrue(p.valid and p.pin_ok)
        self.assertEqual(p.name, "sipterm1")
        self.assertIsNone(p.refusal(pin_required=True))

    def test_wrong_pin_and_charge_denied(self):
        p = sip2.get_patron_status(Canned("24" + " " * 14 + "000" + DATE + "BLY|CQN|"), "M", "1")
        self.assertEqual(p.refusal(pin_required=True), "wrong PIN")
        self.assertIsNone(p.refusal(pin_required=False))
        p = sip2.get_patron_status(Canned("24Y" + " " * 13 + "000" + DATE + "BLY|AFFines|"), "M", "1")
        self.assertEqual(p.refusal(pin_required=False), "Fines")


class Circulation(unittest.TestCase):
    # Real replies from the middelburg server, 2026-11-16 (step 5, item 3322810009661).
    CHECKOUT_OK = ("121NNY20261116    123631AOMIDDL|AA64|AB3322810009661|AJSonder gister se spoor |"
                   "AH20261123    235900|CK001|AY5AZE2E5")
    CHECKIN_OK = ("101YNN20261116    123631AOMIDDL|AB3322810009661|AQMIDDL|AJSonder gister se spoor |"
                  "AA64|CK001|CSLP AF COET|AY4AZE119")

    def test_checkout_ok_captured(self):
        c = Canned(self.CHECKOUT_OK, with_send_once=True)
        r = sip2.do_checkout(c, "MIDDL", "64", "3322810009661")
        self.assertTrue(r.ok)
        self.assertEqual(r.security, "Y")                       # desensitize
        self.assertEqual(r.due_date, "20261123    235900")      # SIP date format, not ISO
        self.assertTrue(c.sent[0].startswith("11NN"))
        self.assertTrue(sip2.verify(r.raw))

    def test_checkin_ok_captured(self):
        r = sip2.do_checkin(Canned(self.CHECKIN_OK), "MIDDL", "MIDDL", "3322810009661")
        self.assertTrue(r.ok)
        self.assertEqual(r.security, "Y")                       # resensitize
        self.assertFalse(r.alert)
        self.assertTrue(sip2.verify(r.raw))

    def test_writes_never_use_the_retrying_send(self):
        c = Canned("121NUY" + DATE + "AB1|", with_send_once=True)
        sip2.do_checkout(c, "MIDDL", "64", "1")
        c2 = Canned("101YUN" + DATE + "AB1|", with_send_once=True)
        sip2.do_checkin(c2, "MIDDL", "MIDDL", "1")
        self.assertEqual(c.used + c2.used, ["send_once", "send_once"])

    def test_checkout_refused_captured(self):
        # Real reply from the middelburg server, 2026-11-16 (IndependentBranches refusal).
        r = sip2.do_checkout(Canned(
            "120NUN20261116    122045AOMIDDL|AA64|AB3322810009661|AJSonder gister se spoor |"
            "AH|AFIssue failed : ITEMNOTSAMEBRANCH|BLY|AY7AZDB67"), "MIDDL", "64", "3322810009661")
        self.assertFalse(r.ok)
        self.assertEqual(r.security, "N")
        self.assertEqual(r.due_date, "")
        self.assertEqual(r.message, "Issue failed : ITEMNOTSAMEBRANCH")
        self.assertTrue(sip2.verify(r.raw))

    def test_checkin_with_alert(self):
        r = sip2.do_checkin(Canned("101YUY" + DATE + "AOMIDDL|AB1|AQMIDDL|CV01|"), "M", "M", "1")
        self.assertTrue(r.ok and r.alert)
        self.assertEqual((r.security, r.alert_type), ("Y", "01"))

    def test_wrong_reply_type_raises(self):
        with self.assertRaises(IOError):
            sip2.do_checkin(Canned("96"), "M", "M", "1")


if __name__ == "__main__":
    unittest.main()
