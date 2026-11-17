import socket
import threading
import unittest

from koha_rfid import labels

# Output of testing/print_rfid_labels.ps1 -CsvFile for these two books (2026-11-16).
PS_REFERENCE = "\r\n".join([
    "^XA", "^PW400^LL200^LH0,0",
    "^RFW,H^FD0E3322801679489100000000^FS",
    "^FN1^RFR,H^FS^HV1,,EPC:^FS",
    "^FO20,15^BY2,2.5,80^BKN,N,80,N,N,A,A^FD33228016794891^FS",
    "^FO20,105^A0N,28,28^FD3 3228 01679489 1^FS",
    "^FO20,140^A0N,22,22^FDThe dog in the manger and the o.^FS",
    "^FO20,170^A0N,18,18^FDEPC 0E3322801679489100000000^FS",
    "^PQ1", "^XZ",
    "^XA", "^PW400^LL200^LH0,0",
    "^RFW,H^FD0D3322830031985000000000^FS",
    "^FN1^RFR,H^FS^HV1,,EPC:^FS",
    "^FO20,15^BY2,2.5,80^BKN,N,80,N,N,A,A^FD3322830031985^FS",
    "^FO20,105^A0N,28,28^FD3322830031985^FS",
    "^FO20,140^A0N,22,22^FDThe boy who cried wolf^FS",
    "^FO20,170^A0N,18,18^FDEPC 0D3322830031985000000000^FS",
    "^PQ1", "^XZ",
])


class Zpl(unittest.TestCase):
    def test_matches_powershell_output(self):
        self.assertEqual(labels.labels_zpl([
            ("33228016794891", "The dog in the manger and the other aesop fables"),
            ("3322830031985", "The boy who cried wolf"),
        ]), PS_REFERENCE)

    def test_label_text(self):
        self.assertEqual(labels.label_text("Wanneer die sonbesies sing /"), "Wanneer die sonbesies sing")
        self.assertEqual(labels.label_text("Ek sê ^~ 'n  ding"), "Ek se 'n ding")
        self.assertEqual(labels.label_text("From Notting Hill to New York...Actually"),
                         "From Notting Hill to New York...")

    def test_no_title_line_without_title(self):
        z = labels.label_zpl("33228016794891")
        self.assertNotIn("^FO20,140", z)

    def test_rejects_what_the_tag_cant_hold(self):
        for bad in ("GM 048 150", "1" * 23, ""):
            with self.assertRaises(ValueError):
                labels.label_zpl(bad)

    def test_parse_readback_back_to_back(self):
        self.assertEqual(labels.parse_readback("xxEPC:0E33228016794891000000000\r\nEPC:0d33EPC:"),
                         ["0E33228016794891000000000", "0D33", ""])


class FakePrinter:
    """Accepts one connection on a free port and answers with `reply`."""

    def __init__(self, reply):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got = b""
        self.reply = reply
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        c, _ = self.srv.accept()
        with c:
            c.settimeout(2)
            while not self.got.endswith(b"^XZ"):
                chunk = c.recv(4096)
                if not chunk:
                    break
                self.got += chunk
            c.sendall(self.reply.encode())
        self.srv.close()


class PrintLabels(unittest.TestCase):
    def test_ok_and_mismatch(self):
        p = FakePrinter("EPC:0E3322801679489100000000EPC:0D3322830031985FFFFFFFFF")
        r = labels.print_labels("127.0.0.1", [("33228016794891", "x"), ("3322830031985", "y")],
                                port=p.port, timeout=3)
        self.assertEqual([x.status for x in r], ["OK", "MISMATCH"])
        self.assertIn(b"^RFW,H^FD0E3322801679489100000000^FS", p.got)

    def test_no_reply(self):
        p = FakePrinter("")
        r = labels.print_labels("127.0.0.1", [("33228016794891", "x")], port=p.port, timeout=1)
        self.assertEqual(r[0].status, "NO REPLY")


if __name__ == "__main__":
    unittest.main()
