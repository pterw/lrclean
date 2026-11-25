import csv
import hashlib
import os
import re
import tempfile
import unittest
from typing import NamedTuple

from lrclean.copy_out import LIST, SUMMARY, write_outputs
from lrclean.mover_gate import _SCAN_LINE
from lrclean.reject_out import SummaryInput
from lrclean.rejects import CatalogView

REASONS = ("outside_photo_root", "placeholder", "recorded", "work", "sidecar",
           "twin_rejected", "changed", "no_archive_copy", "no_keeper")
SHOOT = "C:/cat/shoot-a.lrcat"
SCAN_LINE = re.compile(r"^- \w+ scan \d+ of ", re.MULTILINE)


# Stand-ins with the frozen field names of lrclean.copies (AX); AZ's end-to-end test joins the real types.
class CopyRow(NamedTuple):
    path: str
    bytes: int
    mtime_ns: int
    dests: tuple
    twin: str


class Purged(NamedTuple):
    by_size: dict
    results: int
    rejects: int


class Built(NamedTuple):
    rows: tuple
    examined: dict
    purged: Purged


def copy(path="C:/photos/a/IMG_0001.ARW", size=100, twin="C:/photos/keep/IMG_0001.ARW", dests=("D:/a/x", "E:/b/x")):
    return CopyRow(path, size, 5, tuple(dests), twin)


def built(rows=(), examined=None, purged=Purged({}, 0, 0)) -> Built:
    return Built(tuple(rows), examined or {reason: (0, 0) for reason in REASONS}, purged)


def view(excluded=False) -> CatalogView:
    return CatalogView(SHOOT, "shoot", "ok", excluded, {}, {}, set(), set(), {}, frozenset(), {})


def summary(views=(), limiting=()) -> SummaryInput:
    scans = [{"kind": "catalogs", "id": 3, "root": "C:/cat", "status": "complete", "finished": "t1"}]
    return SummaryInput("2026-11-23T10:00:00", scans, list(views), list(limiting))


def rows_of(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


class CopyOutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = os.path.join(tmp.name, "out")

    def write(self, rows=(), roots=2, sm=None, unhashed=0, **kw):
        return write_outputs(self.out, built(rows, **kw), roots=roots, summary=sm or summary(), unhashed=unhashed)

    def text(self, name=SUMMARY):
        with open(os.path.join(self.out, name), encoding="utf-8", newline="") as handle:
            return handle.read()

    def section(self, title):
        lines = self.text().split("\n")
        body = lines[lines.index(f"## {title}") + 2:]
        body = body[:next((i for i, line in enumerate(body) if line.startswith("## ")), len(body))]
        while body and body[-1] == "":
            body.pop()
        return body

    def test_list_rows_header_and_classes(self):
        header = ["source", "bytes", "mtime_ns", "sidecars", "sidecar_bytes"]
        a = copy()
        b = copy("C:/photos/b/IMG_0002.ARW", twin="", dests=("D:/a/y", "E:/b/y"))
        for roots, rows, expected in [
            (2, [a, b], [[a.path, "100", "5", "", "0", "D:/a/x", "E:/b/x", a.twin],
                         [b.path, "100", "5", "", "0", "D:/a/y", "E:/b/y", ""]]),
            (3, [copy(dests=("D:/a/x", "E:/b/x", "F:/c/x"))],
             [[a.path, "100", "5", "", "0", "D:/a/x", "E:/b/x", "F:/c/x", a.twin]]),
            (2, [], []),
        ]:
            with self.subTest(roots=roots, rows=len(rows)):
                self.write(rows, roots=roots)
                got = rows_of(os.path.join(self.out, LIST))
                self.assertEqual(got[0], header + [f"dest_{n}" for n in range(1, roots + 1)] + ["twin"])
                self.assertEqual(got[1:], expected)

    def test_rows_dests_must_match_roots(self):
        for dests in [("D:/a/x",), ("D:/a/x", "E:/b/x", "F:/c/x")]:  # fewer and more than the 2 roots
            with self.subTest(dests=len(dests)):
                with self.assertRaises(ValueError):
                    self.write([copy(dests=dests)])
                self.assertFalse(os.path.exists(os.path.join(self.out, SUMMARY)))

    def test_summary_ends_with_list_hash_and_names_only_the_catalogs_scans(self):
        scans = [{"kind": "catalogs", "id": n, "root": f"C:/cat{n}", "status": "complete", "finished": "t1"}
                 for n in (3, 4)]  # one per root
        self.write([copy()], sm=SummaryInput("2026-11-23T10:00:00", scans, [], []))
        text = self.text()
        with open(os.path.join(self.out, LIST), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(text.split("\n")[-4:], ["## Removal list", "", f"- SHA-256: {digest}", ""])
        self.assertTrue(text.startswith("# Unrecorded copies\nGenerated: 2026-11-23T10:00:00\n"))
        self.assertEqual(SCAN_LINE.findall(text), ["- catalogs scan 3 of ", "- catalogs scan 4 of "])
        self.assertEqual(_SCAN_LINE.findall(text), [("catalogs", "3"), ("catalogs", "4")])  # the mover's reader

    def test_classes_apart_with_totals_and_per_folder_counts(self):
        # folders fed out of order (c before a, b before a) to pin the sorted summary
        rows = [copy("C:/photos/b/3.ARW", 40), copy("C:/photos/a/1.ARW", 10), copy("C:/photos/a/2.ARW", 20),
                copy("C:/photos/c/5.ARW", 2, twin=""), copy("C:/photos/a/4.ARW", 1, twin="")]
        a_title, b_title = "Class (a): copies with a kept twin", "Class (b): copies of a reject"
        with self.subTest("both classes"):
            self.write(rows)
            self.assertEqual(self.section(a_title),
                             ["Files: 3, bytes: 70", "", "- C:/photos/a: 2 files, 30 bytes", "- C:/photos/b: 1 files, 40 bytes"])
            self.assertEqual(self.section(b_title),
                             ["Files: 2, bytes: 3", "", "- C:/photos/a: 1 files, 1 bytes", "- C:/photos/c: 1 files, 2 bytes"])
        with self.subTest("empty class"):
            self.write([copy()])
            self.assertEqual(self.section(b_title), ["Files: 0, bytes: 0", "", "- none"])

    def test_every_reason_shown_zero_too_with_its_bytes(self):
        examined = {reason: (0, 0) for reason in REASONS}
        examined["recorded"] = (3, 300)
        self.write(examined=examined)
        body = self.section("Examined")
        self.assertIn("`recorded` counts every file a catalog records, the kept twins included", body[0])
        self.assertIn("held files are counted, not listed", body[0])
        self.assertEqual(body[4:], [f"| {r} | {examined[r][0]} | {examined[r][1]} |" for r in REASONS])

    def test_purge_results_and_not_checked(self):
        self.write(purged=Purged({1: frozenset()}, 2, 7), sm=summary([view(True), view()], ["a root is unlistable"]))
        self.assertEqual(self.section("Purge results"), ["- result files read: 2", "- purged rejects in them: 7"])
        self.assertEqual(self.section("Not checked"), [f"- {SHOOT}: ok", "- limiting: a root is unlistable"])
        self.write(sm=summary([view()]))
        self.assertEqual(self.section("Not checked"), ["- none"])
        self.write(sm=summary([view()]), unhashed=3)  # files hash did not cover are listed, so not "none"
        self.assertEqual(self.section("Not checked"), ["- 3 files not covered by hash: run hash"])
        self.write(sm=summary([view(True)], ["a root is unlistable"]), unhashed=1)
        self.assertEqual(self.section("Not checked"), [f"- {SHOOT}: ok", "- limiting: a root is unlistable",
                                                       "- 1 files not covered by hash: run hash"])

    def test_failed_write_leaves_no_summary_and_no_tmp_and_a_second_run_replaces_the_first(self):
        self.write([copy()])
        self.write([copy("C:/photos/z/IMG_9.ARW", 9)])
        self.assertIn("C:/photos/z", self.text())
        self.assertNotIn("C:/photos/a", self.text())
        target = os.path.join(self.out, LIST)
        os.remove(target)
        os.mkdir(target)  # a folder where the list belongs
        with self.assertRaises(OSError):
            self.write([copy()])
        self.assertEqual(sorted(os.listdir(self.out)), [LIST])
