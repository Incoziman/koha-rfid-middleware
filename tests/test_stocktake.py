"""R220 stocktake: sections, de-duplication, output files. No reader needed."""

import csv
import io
import os
import tempfile
import time
import unittest

from koha_rfid.stocktake.__main__ import main
from koha_rfid.stocktake.session import UNASSIGNED, StocktakeSession

DOG = "0E3322801679489100000000"        # 33228016794891
WOLF = "0D3322830031985000000000"       # 3322830031985
BLANK = "E280116060000207C82A0004"      # factory-blank tag: not a library item


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        self.t += 1
        return self.t


class Session(unittest.TestCase):
    def test_sections_and_dedupe(self):
        s = StocktakeSession(clock=Clock())
        self.assertEqual(s.start_section("  Shelf   12A "), "Shelf 12A")
        for rssi in (-60, -45, -50):
            s.on_epc(DOG.lower(), 1, rssi)
        s.on_epc(BLANK, 2, -70)
        first = s.end_section()
        self.assertEqual((first.library, first.foreign, first.seen_before), (1, 1, 0))

        s.start_section("Shelf 12B")
        s.on_epc(WOLF, 2, -55)
        s.on_epc(DOG, 2, -40)                 # wandered over: still counted in 12A
        second = s.end_section()
        self.assertEqual((second.library, second.foreign, second.seen_before), (1, 0, 1))

        dog = next(t for t in s.tags() if t.epc == DOG)
        self.assertEqual((dog.section, dog.reads, dog.best_rssi, dog.antenna),
                         ("Shelf 12A", 4, -40, 2))
        self.assertEqual(dog.sections, ["Shelf 12A", "Shelf 12B"])
        self.assertEqual(s.counts(), {"library": 2, "foreign": 1, "section": 0})

    def test_names(self):
        s = StocktakeSession()
        self.assertEqual(s.start_section(""), "Section 1")
        self.assertEqual(s.start_section("A"), "A")          # also ends "Section 1"
        self.assertEqual(s.start_section("A"), "A (2)")
        self.assertEqual(s.start_section(UNASSIGNED), UNASSIGNED + " (2)")
        self.assertEqual([x.name for x in s.summaries], ["Section 1", "A", "A (2)"])

    def test_reads_before_a_section_go_to_the_next_one(self):
        s = StocktakeSession()
        s.on_epc(DOG, 1, -50)                 # walking up to the shelf
        s.start_section("Shelf 1")
        self.assertEqual(s.end_section().library, 1)
        s.on_epc(WOLF, 1, -50)                # after the last section
        with tempfile.TemporaryDirectory() as d:
            s.write(d)
            with open(os.path.join(d, "reads.csv"), encoding="utf-8") as fh:
                rows = {r["epc"]: r for r in csv.DictReader(fh)}
        self.assertEqual(rows[DOG]["section"], "Shelf 1")
        self.assertEqual(rows[WOLF]["section"], UNASSIGNED)

    def test_write(self):
        s = StocktakeSession(clock=Clock())
        s.start_section("Shelf 12A / Fiction")
        s.on_epc(DOG, 1, -50)
        s.on_epc(BLANK, 1, -50)
        s.start_section("Shelf 12B")
        s.on_epc(WOLF, 1, -50)
        s.on_epc(DOG, 1, -50)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "run")
            files = [os.path.basename(f) for f in s.write(out)]

            def read(name):
                with open(os.path.join(out, name), encoding="utf-8", newline="") as fh:
                    return fh.read()

            self.assertEqual(read("barcodes-all.txt"), "33228016794891\n3322830031985\n")
            self.assertEqual(read("barcodes-01-shelf-12a-fiction.txt"), "33228016794891\n")
            self.assertEqual(read("barcodes-02-shelf-12b.txt"), "3322830031985\n")
            self.assertEqual(read("foreign.txt"), BLANK + "\n")
            self.assertIn("Library tags (unique barcodes): 2", read("summary.txt"))
            rows = list(csv.DictReader(io.StringIO(read("reads.csv"))))
        self.assertEqual(files, ["barcodes-all.txt", "barcodes-01-shelf-12a-fiction.txt",
                                 "barcodes-02-shelf-12b.txt", "reads.csv", "foreign.txt",
                                 "summary.txt"])
        self.assertEqual([r["barcode"] for r in rows], ["33228016794891", "", "3322830031985"])
        self.assertEqual(rows[0]["also_seen_in"], "Shelf 12B")


class Cli(unittest.TestCase):
    def test_fake_run(self):
        answers = iter(["Shelf 1", None, "q"])

        def inp(prompt):
            a = next(answers)
            if a is None:              # "scanning": give the fake reader time to report
                time.sleep(0.4)
                return ""
            return a

        printed = []
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "st")
            code = main(["--fake", f"{DOG},{BLANK}", "--out", out], input_fn=inp,
                        out=printed.append)
            self.assertEqual(code, 0)
            with open(os.path.join(out, "barcodes-all.txt"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "33228016794891\n")
            self.assertTrue(os.path.exists(os.path.join(out, "barcodes-01-shelf-1.txt")))
        text = "".join(printed)
        self.assertIn("'Shelf 1': 1 books, 1 other tags", text)
        self.assertIn("Tools > Inventory", text)

    def test_needs_a_host(self):
        with self.assertRaises(SystemExit):
            main([], input_fn=lambda p: "q", out=lambda s: None)

    def test_eof_finishes(self):
        def inp(prompt):
            raise EOFError
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(main(["--fake", DOG, "--out", d], input_fn=inp, out=lambda s: None), 0)
            self.assertTrue(os.path.exists(os.path.join(d, "summary.txt")))


if __name__ == "__main__":
    unittest.main()
