import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from koha_rfid.agent import config
from koha_rfid.agent.pad import PadState, ReaderRunner
from koha_rfid.agent.server import Agent
from koha_rfid.reader import FakeReader
from tests.test_labels import FakePrinter

KOHA = "http://debian.mshome.net:8082"
DOG = "0E3322801679489100000000"        # 33228016794891
WOLF = "0D3322830031985000000000"       # 3322830031985 (13 digits)
BLANK = "E280116060000207C82A0004"      # factory-blank tag


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def request(base, path, origin=KOHA, method="GET", body=None, ctype="application/json"):
    headers = {}
    if origin:
        headers["Origin"] = origin
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = ctype
    r = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"null")


class Pad(unittest.TestCase):
    def test_tags_leave_the_pad(self):
        now = [100.0]
        pad = PadState(pad_seconds=2.0, clock=lambda: now[0])
        pad.on_epc(DOG, 1, -40)
        pad.on_epc(BLANK, 1, -60)
        snap = pad.snapshot()
        self.assertEqual([t["barcode"] for t in snap["tags"]], ["33228016794891"])
        self.assertEqual(snap["foreign"], 1)
        now[0] += 2.5
        self.assertEqual(pad.snapshot()["tags"], [])

    def test_candidates_keep_exact_first(self):
        pad = PadState()
        pad.on_epc("0E3322810009661700000000", 1, -40)
        self.assertEqual(pad.snapshot()["tags"][0]["candidates"][:2],
                         ["33228100096617", "3322810009661"])

    def test_disconnect_clears_the_pad(self):
        pad = PadState()
        pad.on_epc(DOG, 1, -40)
        pad.set_connected(False, "gone")
        self.assertEqual(pad.snapshot()["tags"], [])


class Runner(unittest.TestCase):
    def test_reconnects_after_failures(self):
        attempts = []

        class Flaky(FakeReader):
            def start(self, timeout=15.0):
                attempts.append(1)
                if len(attempts) < 3:
                    raise TimeoutError("reader never started")
                super().start()

        pad = PadState()
        r = ReaderRunner(lambda cb: Flaky(cb, [DOG], interval=0.05), pad,
                         retry_seconds=0.05, check_seconds=0.05)
        r.start()
        try:
            self.assertTrue(wait_for(lambda: pad.connected))
            self.assertEqual(len(attempts), 3)
            self.assertTrue(wait_for(lambda: pad.snapshot()["tags"]))
        finally:
            r.stop()
            r.join(2)

    def test_notices_a_dead_reader(self):
        readers = []

        def factory(cb):
            readers.append(FakeReader(cb, [DOG], interval=0.05))
            return readers[-1]

        pad = PadState()
        r = ReaderRunner(factory, pad, retry_seconds=0.05, check_seconds=0.05)
        r.start()
        try:
            self.assertTrue(wait_for(lambda: pad.connected))
            readers[0].stop()                         # connection dies
            self.assertTrue(wait_for(lambda: len(readers) >= 2 and pad.connected))
        finally:
            r.stop()
            r.join(2)


class Http(unittest.TestCase):
    def setUp(self):
        self.cfg = config.AgentConfig(listen_port=0, allowed_origins=(KOHA,), pad_seconds=2.0,
                                      retry_seconds=0.05)
        self.agent = Agent(self.cfg, reader_factory=lambda cb: FakeReader(cb, [DOG, WOLF, BLANK],
                                                                          interval=0.05))
        self.agent.runner.check_seconds = 0.05
        self.agent.start()
        self.base = f"http://127.0.0.1:{self.agent.port}"
        self.assertTrue(wait_for(lambda: len(self.agent.pad.snapshot()["tags"]) == 2))

    def tearDown(self):
        self.agent.stop()

    def req(self, path, **kw):
        return request(self.base, path, **kw)

    def test_status(self):
        code, headers, body = self.req("/status")
        self.assertEqual(code, 200)
        self.assertTrue(body["reader"]["connected"])
        self.assertFalse(body["printer"]["configured"])
        self.assertEqual(headers["Access-Control-Allow-Origin"], KOHA)

    def test_pad(self):
        code, _, body = self.req("/pad")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(t["barcode"] for t in body["tags"]),
                         ["33228016794891", "3322830031985"])
        self.assertEqual(body["foreign"], 1)
        self.assertTrue(body["reader_connected"])

    def test_other_origins_refused(self):
        code, headers, _ = self.req("/pad", origin="https://evil.example")
        self.assertEqual(code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_no_origin_is_local_tool(self):
        self.assertEqual(self.req("/status", origin=None)[0], 200)

    def test_preflight(self):
        code, headers, _ = self.req("/print", method="OPTIONS")
        self.assertEqual(code, 204)
        self.assertEqual(headers["Access-Control-Allow-Private-Network"], "true")
        self.assertEqual(self.req("/print", origin="https://evil.example", method="OPTIONS")[0], 403)

    def test_print_needs_json_and_a_printer(self):
        self.assertEqual(self.req("/print", method="POST", body=b"x", ctype="text/plain")[0], 415)
        code, _, body = self.req("/print", method="POST", body={"items": [{"barcode": "1"}]})
        self.assertEqual(code, 503)

    def test_print(self):
        printer = FakePrinter("EPC:0E3322801679489100000000")
        self.agent.cfg.printer_host, self.agent.cfg.printer_port = "127.0.0.1", printer.port
        code, _, body = self.req("/print", method="POST",
                                 body={"items": [{"barcode": "33228016794891", "title": "Dog"}]})
        self.assertEqual(code, 200)
        self.assertEqual(body["results"][0]["status"], "OK")
        self.assertIn(b"^FDDog^FS", printer.got)

    def test_print_rejects_unencodable_before_printing(self):
        self.agent.cfg.printer_host = "127.0.0.1"
        code, _, body = self.req("/print", method="POST",
                                 body={"items": [{"barcode": "GM 048 150", "title": "x"}]})
        self.assertEqual(code, 400)
        self.assertIn("can't encode", body["error"])


class Config(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "agent.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[agent]\nallowed_origins = http://a:8082/, https://b\nlog_file = agent.log\n"
                         "[reader]\nhost = 192.168.1.3\nantennas = 1, 2\npower = 41\n"
                         "fake_epcs = 0e33, 0d33\n[printer]\nhost = 192.168.1.60\n")
            c = config.load(p)
        self.assertEqual(c.allowed_origins, ("http://a:8082", "https://b"))
        self.assertEqual((c.reader_host, c.antennas, c.power), ("192.168.1.3", (1, 2), 41))
        self.assertEqual(c.fake_epcs, ["0E33", "0D33"])
        self.assertEqual(c.log_file, os.path.join(d, "agent.log"))
        self.assertEqual(c.listen_port, 8765)

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            config.load("nope.ini")

    def test_save_keeps_comments_and_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "agent.ini")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(EXAMPLE)
            changes = config.validate_changes({"reader": {"host": "10.0.0.7", "power": "21",
                                                          "antennas": [1, 2], "session": 1},
                                               "printer": {"host": "192.168.1.2"}})
            config.save_changes(p, changes)
            with open(p, encoding="utf-8", newline="") as fh:
                text = fh.read()
            c = config.load(p)
        self.assertIn("; Index into the R220 power table", text)
        self.assertIn("; fake_epcs = 0E33", text)                 # commented key untouched
        self.assertIn("\r\n", text)                               # line endings kept
        self.assertIn("power = 21\r\n", text)
        self.assertIn("printer]\r\nhost = 192.168.1.2\r\n", text)
        # session wasn't in the file: added at the end of [reader], before [printer]
        self.assertLess(text.index("session = 1"), text.index("[printer]"))
        self.assertEqual((c.reader_host, c.power, c.antennas, c.session, c.printer_host),
                         ("10.0.0.7", 21, (1, 2), 1, "192.168.1.2"))
        self.assertEqual(c.allowed_origins, (KOHA,))              # untouched
        self.assertEqual(c.path, os.path.abspath(p))

    def test_save_empty_and_missing_section(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "agent.ini")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("[reader]\nhost = 1.2.3.4\n")
            config.save_changes(p, config.validate_changes({"reader": {"host": ""},
                                                            "printer": {"port": 9101}}))
            c = config.load(p)
        self.assertEqual((c.reader_host, c.printer_port), ("", 9101))

    def test_validate_rejects(self):
        bad = [
            {"agent": {"listen_port": 1}},
            {"agent": {"allowed_origins": "https://evil.example"}},
            {"reader": {"fake_epcs": "0E33"}},
            {"reader": {"log_file": "C:\\Windows\\x"}},
            {"reader": {"power": 92}},
            {"reader": {"power": "max"}},
            {"reader": {"power": True}},
            {"reader": {"port": 0}},
            {"reader": {"host": "1.2.3.4\nlog_file = x"}},
            {"reader": {"host": "a b"}},
            {"reader": {"host": "[agent]"}},
            {"reader": {"antennas": "1, 5"}},
            {"reader": {"antennas": ""}},
            {"reader": {"pad_seconds": 0}},
            {"printer": {"host": "x;y"}},
            {"printer": "192.168.1.2"},
            [],
            {},
        ]
        for body in bad:
            with self.assertRaises(config.ConfigError, msg=repr(body)):
                config.validate_changes(body)
        with self.assertRaises(config.ConfigError) as cm:
            config.validate_changes({"reader": {"power": 99}})
        self.assertEqual(cm.exception.field, "reader.power")


EXAMPLE = (
    "; agent settings\r\n[agent]\r\nlisten_port = 8765\r\nallowed_origins = " + KOHA + "\r\n"
    "log_file = agent.log\r\n\r\n[reader]\r\nhost = 192.168.1.3\r\nantennas = 1\r\n"
    "; Index into the R220 power table: 0 = max\r\npower = 0\r\npad_seconds = 2.0\r\n"
    "; fake_epcs = 0E33\r\n\r\n[printer]\r\nhost =\r\nport = 9100\r\n"
)


class HttpConfig(unittest.TestCase):
    """POST /config and /restart against an agent started from an INI file."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ini = os.path.join(self.dir.name, "agent.ini")
        with open(self.ini, "w", encoding="utf-8") as fh:
            fh.write(f"[agent]\nlisten_port = 0\nallowed_origins = {KOHA}\n"
                     f"[reader]\nfake_epcs = {DOG}\nretry_seconds = 1\n[printer]\nhost =\n")
        self.agent = Agent(config.load(self.ini))
        self.agent.runner.check_seconds = 0.05
        self.agent.start()
        self.base = f"http://127.0.0.1:{self.agent.port}"
        self.assertTrue(wait_for(lambda: self.agent.pad.connected))

    def tearDown(self):
        self.agent.stop()
        self.dir.cleanup()

    def req(self, path, **kw):
        return request(self.base, path, **kw)

    def test_status_carries_settings(self):
        code, _, body = self.req("/status")
        self.assertEqual(code, 200)
        self.assertEqual(body["config"]["reader"]["power"], 0)
        self.assertEqual(body["config"]["printer"]["host"], "")
        self.assertEqual(body["allowed_origins"], [KOHA])
        self.assertTrue(body["editable"])
        self.assertEqual(body["config_path"], os.path.abspath(self.ini))

    def test_change_settings(self):
        old_runner = self.agent.runner
        code, _, body = self.req("/config", method="POST",
                                      body={"reader": {"power": 41, "pad_seconds": 3},
                                            "printer": {"host": "192.168.1.2"}})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["config"]["reader"]["power"], 41)
        self.assertEqual(body["printer"]["host"], "192.168.1.2")
        self.assertEqual(self.agent.cfg.pad_seconds, 3.0)
        self.assertIsNot(self.agent.runner, old_runner)
        self.assertFalse(old_runner.is_alive())
        self.assertTrue(wait_for(lambda: self.agent.pad.connected))   # reader back
        self.assertEqual(config.load(self.ini).power, 41)
        self.assertEqual(self.agent.port, self.agent.httpd.server_address[1])

    def test_bad_value_leaves_file_alone(self):
        with open(self.ini, "rb") as fh:
            before = fh.read()
        code, _, body = self.req("/config", method="POST", body={"reader": {"power": 200}})
        self.assertEqual(code, 400)
        self.assertEqual(body["field"], "reader.power")
        with open(self.ini, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_other_origins_cant_change_settings(self):
        code, _, _ = self.req("/config", origin="https://evil.example", method="POST",
                                   body={"printer": {"host": "10.6.6.6"}})
        self.assertEqual(code, 403)
        self.assertEqual(config.load(self.ini).printer_host, "")
        self.assertEqual(self.req("/config", method="POST", body=b"{}",
                                       ctype="text/plain")[0], 415)

    def test_restart(self):
        old_runner = self.agent.runner
        code, _, body = self.req("/restart", method="POST", body={})
        self.assertEqual(code, 200)
        self.assertIsNot(self.agent.runner, old_runner)
        self.assertTrue(wait_for(lambda: self.agent.pad.connected))

    def test_no_config_file(self):
        self.agent.cfg.path = ""
        code, _, _ = self.req("/config", method="POST", body={"reader": {"power": 1}})
        self.assertEqual(code, 409)


if __name__ == "__main__":
    unittest.main()
