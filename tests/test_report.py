import csv
import os
import tempfile
import unittest

from lrclean import report

COVERAGE = {
    "roots": [{"root": "D:/lib", "status": "complete"}, {"root": "E:/part-root", "status": "partial"}],
    "catalogs": [{"catalog": "cat-limit.lrcat", "status": "unreadable", "limits_coverage": True}],
    "limiting": ["cat-limit.lrcat is unreadable"],
    "generated": "2026-02-03T04:05:06Z",
    "scans": [
        {"kind": "catalogs", "id": 1, "root": "D:/cats", "status": "complete",
         "finished": "2026-02-03T01:00:00Z"},
        {"kind": "files", "id": 2, "root": "E:/part-root", "status": "partial",
         "finished": "2026-02-03T02:00:00Z"},
    ],
}
LIMITS = {n: f"limit sentence {n}" for n in range(1, 12)}


def blank_row(number, **values):
    row = {c: ("0" if c == "bytes" else "x") for c in report.SECTIONS[number].columns}
    row.update(values)
    return row


def empty_sections():
    return {n: [] for n in report.SECTIONS}


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


class ReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = os.path.join(self._tmp.name, "out")

    def summary(self):
        with open(os.path.join(self.out, "summary.md"), encoding="utf-8", newline="") as handle:
            return handle.read()

    def snapshot(self):
        result = {}
        for name in os.listdir(self.out):
            with open(os.path.join(self.out, name), "rb") as handle:
                result[name] = handle.read()
        return result

    def write(self, sections):
        report.write_report(self.out, sections, COVERAGE, LIMITS)

    def test_output_rules(self):
        sections = empty_sections()
        sections[8] = [blank_row(8, path="D:/lib/Zoë/IMG_0001.ARW")]
        self.write(sections)
        names = sorted(os.listdir(self.out))
        self.assertEqual(names, sorted([s.filename for s in report.SECTIONS.values()] + ["summary.md"]))
        with open(os.path.join(self.out, "08_missing.csv"), "rb") as handle:
            self.assertTrue(handle.read().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(read_csv(os.path.join(self.out, "08_missing.csv"))[1][0], "D:/lib/Zoë/IMG_0001.ARW")
        self.assertNotIn("\r", self.summary())

    def test_every_section_ends_with_how_decided(self):
        for section in report.SECTIONS.values():
            self.assertEqual(section.columns[-1], "how_decided")

    def test_invalid_row_raises_and_writes_nothing(self):
        good = blank_row(7)
        cases = {
            "missing": {k: v for k, v in good.items() if k != "files"},
            "extra": {**good, "more": "1"},
            "renamed": {("count" if k == "files" else k): v for k, v in good.items()},
            "empty how": {**good, "how_decided": ""},
            "none how": {**good, "how_decided": None},
            "float bytes": {**good, "bytes": 5.9},
        }
        for name, bad in cases.items():
            with self.subTest(name):
                sections = empty_sections()
                sections[1] = [blank_row(1)]
                sections[7] = [bad]
                with self.assertRaises(ValueError):
                    self.write(sections)
                self.assertFalse(os.path.exists(self.out))

    def test_missing_limit_is_key_error_and_writes_nothing(self):
        limits = {k: v for k, v in LIMITS.items() if k != 5}
        with self.assertRaises(KeyError):
            report.write_report(self.out, empty_sections(), COVERAGE, limits)
        self.assertFalse(os.path.exists(self.out))

    def test_failed_write_raises_and_leaves_existing_report_unchanged(self):
        self.write(empty_sections())
        before = self.snapshot()
        os.mkdir(os.path.join(self.out, "05_external_copies.csv.tmp"))  # the fifth write fails
        sections = empty_sections()
        sections[1] = [blank_row(1, catalog="new.lrcat")]
        with self.assertRaises(OSError):
            self.write(sections)
        os.rmdir(os.path.join(self.out, "05_external_copies.csv.tmp"))
        self.assertEqual(before, self.snapshot())  # no section file changed, no .tmp left behind

    @unittest.skipUnless(os.name == "nt", "a file held open blocks os.replace only on Windows")
    def test_refused_replace_leaves_no_summary_and_no_tmp_files(self):
        self.write(empty_sections())
        sections = empty_sections()
        sections[1] = [blank_row(1, catalog="new.lrcat")]
        with open(os.path.join(self.out, "03_safe_candidates.csv"), "rb"):  # held like a spreadsheet does
            with self.assertRaises(OSError):
                self.write(sections)
        names = os.listdir(self.out)
        self.assertEqual([n for n in names if n.endswith(".tmp")], [])
        self.assertNotIn("summary.md", names)

    def test_summary_opens_with_coverage_and_empty_sections_print_zero(self):
        self.write(empty_sections())
        text = self.summary()
        self.assertTrue(text.startswith("# Coverage"))
        for needle in ("E:/part-root: partial", "cat-limit.lrcat: unreadable (limits coverage)",
                       "cat-limit.lrcat is unreadable", "Rows: 0", "Bytes: 0", "limit sentence 11",
                       "\nGenerated: 2026-02-03T04:05:06Z\n", "\n## Scans\n",
                       "\n- catalogs scan 1 of D:/cats: complete, finished 2026-02-03T01:00:00Z\n",
                       "\n- files scan 2 of E:/part-root: partial, finished 2026-02-03T02:00:00Z\n"):
            self.assertIn(needle, text)

    def test_generated_time_is_read_back_from_a_written_summary(self):
        self.write(empty_sections())
        path = os.path.join(self.out, "summary.md")
        self.assertEqual(report.read_generated(path), "2026-02-03T04:05:06Z")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Coverage\n\n## Roots\n")
        self.assertIsNone(report.read_generated(path))

    def test_totals_equal_row_sums(self):
        sections = empty_sections()
        sections[7] = [blank_row(7, folder="D:/a", bytes="5"), blank_row(7, folder="D:/b", bytes="7")]
        self.write(sections)
        self.assertIn("Rows: 2\nBytes: 12\n", self.summary())

    def test_container_reclaimable_counts_only_yes_rows(self):
        sections = empty_sections()
        sections[9] = [
            blank_row(9, bytes="100", reclaimable="yes"),
            blank_row(9, bytes="40", type="storage", reclaimable="no"),
            blank_row(9, bytes="30", type="unknown", reclaimable="unverified"),
        ]
        self.write(sections)
        text = self.summary()
        self.assertIn("Total size: 170", text)
        self.assertIn("Reclaimable: 100", text)

    def test_by_folder_top_ten_and_case_folding(self):
        sections = empty_sections()
        rows = [blank_row(3, folder=f"D:/f{i:02d}", bytes=str(100 + i)) for i in range(11)]
        rows += [blank_row(3, folder="D:/F00", bytes="1000"), blank_row(3, folder="D:/solo", bytes="900")]
        sections[3] = rows
        self.write(sections)
        text = self.summary()
        self.assertIn("| D:/f00 | 1100 |", text)  # case variants add up, first spelling shown
        self.assertNotIn("D:/F00 |", text)
        self.assertIn("| D:/solo | 900 |", text)
        self.assertNotIn("| D:/f01 |", text)  # the eleventh-largest folder is cut

    def test_needs_eye_counts_per_kind(self):
        sections = empty_sections()
        sections[4] = [blank_row(4, kind="same_file"), blank_row(4, kind="same_file"),
                       blank_row(4, kind="not_compared")]
        self.write(sections)
        text = self.summary()
        self.assertIn("Kind same_file: 2", text)
        self.assertIn("Kind not_compared: 1", text)

    def test_two_writes_are_byte_identical(self):
        sections = empty_sections()
        sections[3] = [blank_row(3, bytes="3")]
        self.write(sections)
        first = self.snapshot()
        self.write(sections)
        self.assertEqual(first, self.snapshot())


if __name__ == "__main__":
    unittest.main()
