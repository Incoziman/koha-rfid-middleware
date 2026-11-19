"""Reader selection, and the Chafon CF606 placeholder (not confirmed, no driver)."""

import time
import unittest

from koha_rfid.agent.config import AgentConfig
from koha_rfid.agent.pad import PadState, ReaderRunner
from koha_rfid.readers import make_reader_factory
from koha_rfid.readers.chafon import ChafonReader
from koha_rfid.readers.fake import FakeReader


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Factory(unittest.TestCase):
    def test_fake_epcs_win(self):
        cfg = AgentConfig(reader_type="chafon", serial_port="COM3", fake_epcs=["0E33"])
        self.assertIsInstance(make_reader_factory(cfg)(lambda *a: None), FakeReader)

    def test_chafon(self):
        cfg = AgentConfig(reader_type="chafon", serial_port="COM3", baud=115200, power_dbm=10)
        r = make_reader_factory(cfg)(lambda *a: None)
        self.assertIsInstance(r, ChafonReader)
        self.assertEqual((r.serial_port, r.baud, r.power_dbm), ("COM3", 115200, 10))

    def test_r220(self):
        try:
            import sllurp  # noqa: F401
        except ImportError:
            self.skipTest("sllurp not installed")
        from koha_rfid.readers.r220 import R220Reader
        cfg = AgentConfig(reader_type="r220", reader_host="192.0.2.1")
        self.assertIsInstance(make_reader_factory(cfg)(lambda *a: None), R220Reader)

    def test_missing_settings(self):
        for cfg, msg in ((AgentConfig(reader_type="r220"), "host"),
                         (AgentConfig(reader_type="chafon"), "serial_port"),
                         (AgentConfig(reader_type="nope"), "unknown reader type")):
            with self.assertRaisesRegex(RuntimeError, msg):
                make_reader_factory(cfg)(lambda *a: None)


class Chafon(unittest.TestCase):
    def test_refuses_to_start(self):
        r = ChafonReader("COM3", lambda *a: None)
        with self.assertRaisesRegex(NotImplementedError, "not confirmed for any implementation"):
            r.start()
        self.assertFalse(r.is_alive())
        r.stop()    # harmless

    def test_agent_shows_why_and_keeps_retrying(self):
        pad = PadState()
        made = []

        def factory(cb):
            made.append(ChafonReader("COM3", cb))
            return made[-1]

        runner = ReaderRunner(factory, pad, retry_seconds=0.05, check_seconds=0.05)
        runner.start()
        try:
            self.assertTrue(wait_for(lambda: len(made) >= 2))
            self.assertFalse(pad.connected)
            self.assertIn("not confirmed", pad.error)
        finally:
            runner.stop()
            runner.join(2)
        self.assertFalse(runner.is_alive())


if __name__ == "__main__":
    unittest.main()
