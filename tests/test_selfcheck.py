import json
import os
import tempfile
import unittest

from koha_rfid import sip2
from koha_rfid.selfcheck import circulation as circ

DATE = "20261116    120000"


class FakeKoha:
    """In-memory SIP server: items {barcode: [title, status]}, answers 17 / 11 / 09.

    drop_write = "before" / "after" makes the next 11/09 raise ConnectionError,
    without / after applying it, like a connection lost mid-transaction.
    """

    def __init__(self, items):
        self.items = {b: [t, s] for b, (t, s) in items.items()}
        self.drop_write = None
        self.sent = []

    def _reply(self, msg):
        cmd, _, f = sip2.parse(msg)
        bc = f.get("AB", [""])[0]
        if cmd == "17":
            if bc not in self.items:
                return f"18010101{DATE}AB{bc}|AJ|"
            title, status = self.items[bc]
            return f"18{status}0201{DATE}AB{bc}|AJ{title} |AQMIDDL|"
        if cmd == "11":
            self.items[bc][1] = circ.ON_LOAN
            return (f"121NNY{DATE}AOMIDDL|AA{f['AA'][0]}|AB{bc}|AJ{self.items[bc][0]} |"
                    f"AH20261123    235900|AEJane Doe|")
        if cmd == "09":
            self.items[bc][1] = "03"
            return f"101YNN{DATE}AOMIDDL|AB{bc}|AQMIDDL|AA9001010000088|"
        raise AssertionError(msg)

    def send(self, msg):
        self.sent.append(msg[:2])
        return self._reply(msg)

    def send_once(self, msg):
        self.sent.append(msg[:2])
        if self.drop_write:
            when, self.drop_write = self.drop_write, None
            if when == "after":
                self._reply(msg)
            raise ConnectionError("SIP server closed the connection")
        return self._reply(msg)


def books():
    return FakeKoha({
        "3322810009661": ("Sonder gister se spoor", "03"),   # stored without check digit
        "33228016794891": ("The dog in the manger", "03"),
        "3322830031985": ("The boy who cried wolf", circ.ON_LOAN),
    })


class Plan(unittest.TestCase):
    def test_checkout_rules(self):
        k = books()
        todo, skipped = circ.plan(k, "MIDDL", circ.CHECKOUT, [
            ("33228100096617", "0E3322810009661700000000"),   # tag with check digit
            ("3322810009661", None),                          # same item, other form
            ("33228016794891", None),
            ("33228016794891", None),                         # same tag twice
            ("3322830031985", None),                          # on loan
            ("33228999900010", None),                         # not in Koha
        ])
        self.assertEqual([p.item.barcode for p in todo], ["3322810009661", "33228016794891"])
        self.assertEqual(todo[0].epc, "0E3322810009661700000000")
        self.assertTrue(todo[0].item.repaired)
        reasons = {s.tag_barcode: s.reason for s in skipped}
        self.assertIn("already listed", reasons["3322810009661"])
        self.assertIn("already on loan", reasons["3322830031985"])
        self.assertEqual(reasons["33228999900010"], "not in Koha")
        self.assertNotIn("11", k.sent)                  # planning never writes

    def test_checkin_only_items_on_loan(self):
        todo, skipped = circ.plan(books(), "MIDDL", circ.CHECKIN,
                                  [("3322830031985", None), ("33228016794891", None)])
        self.assertEqual([p.item.barcode for p in todo], ["3322830031985"])
        self.assertIn("not on loan", skipped[0].reason)

    def test_bad_action(self):
        with self.assertRaises(ValueError):
            circ.plan(books(), "MIDDL", "renew", [])


class Run(unittest.TestCase):
    def one(self, k, action, barcode):
        todo, _ = circ.plan(k, "MIDDL", action, [(barcode, None)])
        return todo[0]

    def test_checkout_then_checkin(self):
        k = books()
        o = circ.run(k, "MIDDL", circ.CHECKOUT, self.one(k, circ.CHECKOUT, "33228016794891"), card="64")
        self.assertTrue(o.ok and o.verified)
        self.assertEqual(o.result.due_date, "20261123    235900")
        self.assertEqual(k.sent[-2:], ["11", "17"])     # write, then verify
        o = circ.run(k, "MIDDL", circ.CHECKIN, self.one(k, circ.CHECKIN, "33228016794891"))
        self.assertTrue(o.ok)
        self.assertEqual(o.after.circulation_status, "03")

    def test_drop_before_applied_is_not_resent(self):
        k = books()
        p = self.one(k, circ.CHECKOUT, "33228016794891")
        k.drop_write = "before"
        o = circ.run(k, "MIDDL", circ.CHECKOUT, p, card="64")
        self.assertIsNone(o.result)
        self.assertFalse(o.verified or o.ok)
        self.assertEqual(k.sent.count("11"), 1)          # never resent

    def test_drop_after_applied_counts_as_done(self):
        k = books()
        p = self.one(k, circ.CHECKOUT, "33228016794891")
        k.drop_write = "after"
        o = circ.run(k, "MIDDL", circ.CHECKOUT, p, card="64")
        self.assertIsNone(o.result)
        self.assertTrue(o.verified and o.ok)
        self.assertEqual(k.sent.count("11"), 1)


class Log(unittest.TestCase):
    def test_masks_card_and_drops_names(self):
        k = books()
        todo, _ = circ.plan(k, "MIDDL", circ.CHECKOUT, [("33228016794891", None)])
        card = "9001010000088"
        o = circ.run(k, "MIDDL", circ.CHECKOUT, todo[0], card=card)
        with tempfile.TemporaryDirectory() as d:
            log = circ.TransactionLog(os.path.join(d, "sub", "circ.jsonl"))
            log.write(o, card=card, sip_user="sipterm1")
            text = open(log.path, encoding="utf-8").read()
        self.assertNotIn(card, text)
        self.assertNotIn("Jane Doe", text)
        e = json.loads(text)
        self.assertEqual(e["patron"], "**********088")
        self.assertIn("AA**********088", e["reply"])
        self.assertEqual((e["verified"], e["status_after"], e["sip_user"]), (True, "04", "sipterm1"))

    def test_scrub_and_mask(self):
        self.assertEqual(circ.mask("64"), "64")
        self.assertEqual(circ.scrub("101YNN" + DATE + "AB1|AA1234567|DAHold Person|CK001"),
                         "101YNN" + DATE + "AB1|AA****567|CK001")


class DecodeTags(unittest.TestCase):
    def test_library_tags_only(self):
        tags, skipped = circ.decode_tags(["E280116060000207C82A0004", "0E3322810009661700000000",
                                          "464D4B32534E3637"])
        self.assertEqual(tags, [("33228100096617", "0E3322810009661700000000")])
        self.assertEqual(skipped, 2)


if __name__ == "__main__":
    unittest.main()
