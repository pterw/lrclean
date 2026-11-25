import csv
import hashlib
import os
import tempfile
import unittest

from lrclean.reject_out import SummaryInput, write_outputs
from lrclean.rejects import VERDICTS, CatalogView, Reject

LIVE = "C:/cat/live.lrcat"
SHOOT = "C:/cat/shoot-a.lrcat"
TWIN = "C:/live-photos/IMG_0001.ARW"
LIFTED_LINE = "Rejects on the list whose develop hold rule 26a lifted: {} files, {} bytes"


def reject(path="C:/shoot-a/IMG_0001.ARW", **kw) -> Reject:
    fields = dict(path=path, path_norm=path.lower(), bytes=100, mtime_ns=5, verdict="on_list",
                  rejected_by=[(SHOOT, "shoot")], kept_by=[], archive=["ok", "ok"], developed_in=[],
                  images=[(SHOOT, 1)], sidecars=[], sidecar_bytes=0, held_by="", dests=["D:/a/x", "E:/b/x"])
    fields.update(kw)
    return Reject(**fields)


def view(source=SHOOT, role="shoot", status="ok", excluded=False, picks=None, absolute=None) -> CatalogView:
    picks = picks or {}
    absolute = set(picks if absolute is None else absolute)
    return CatalogView(source, role, status, excluded, picks, {}, set(picks), set(), {k: k for k in picks},
                       frozenset(absolute), {k: len(v) for k, v in picks.items() if k in absolute})


def summary(views=None, limiting=None) -> SummaryInput:
    scans = [{"kind": "catalogs", "id": 3, "root": "C:/cat", "status": "complete", "finished": "t1"}]
    return SummaryInput("2026-11-18T10:00:00", scans, views if views is not None else [view()], limiting or [])


def rows_of(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


class RejectOutTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = os.path.join(tmp.name, "out")

    def write(self, rejects, roots=2, **kw):
        return write_outputs(self.out, rejects, roots=roots, summary=kw.get("summary") or summary())

    def read_summary(self):
        with open(os.path.join(self.out, "rejects-summary.md"), encoding="utf-8", newline="") as handle:
            return handle.read()

    def section(self, title):
        """Lines of `## <title>` up to the next heading, blank edges trimmed."""
        lines = self.read_summary().split("\n")
        body = lines[lines.index(f"## {title}") + 2:]
        body = body[:next((i for i, line in enumerate(body) if line.startswith("## ")), len(body))]
        while body and body[-1] == "":
            body.pop()
        return body

    def test_rejects_csv_row_carries_every_cell_in_its_column(self):
        r = reject(bytes=100, sidecar_bytes=9, verdict="kept_for_work", kept_by=[(LIVE, "live")],
                   archive=["ok", "differs"], dests=["D:/a/x", "E:/b/x"], twin=TWIN, twin_settings="abc123")
        self.write([r])
        self.assertEqual(rows_of(os.path.join(self.out, "rejects.csv"))[1],
                         ["C:/shoot-a/IMG_0001.ARW", "100", "kept_for_work", f"{SHOOT} (shoot)",
                          f"{LIVE} (live)", "ok", "differs", "", TWIN, "abc123", f"images: {SHOOT}=1"])

    def test_rejects_csv_twin_cells_are_empty_for_a_reject_built_without_them(self):
        self.write([reject()])
        row = rows_of(os.path.join(self.out, "rejects.csv"))[1]
        self.assertEqual(row[-3:-1], ["", ""])

    def test_header_has_one_archive_and_dest_column_per_root(self):
        for roots in (2, 3):
            r = reject(archive=["ok"] * roots, dests=["d"] * roots)
            self.write([r], roots=roots)
            n = range(1, roots + 1)
            self.assertEqual(rows_of(os.path.join(self.out, "rejects.csv"))[0],
                             ["path", "bytes", "verdict", "rejected_by", "kept_by",
                              *[f"archive_{i}" for i in n], "developed_in", "twin", "twin_settings",
                              "how_decided"])
            self.assertEqual(rows_of(os.path.join(self.out, "reject-removal-list.csv"))[0],
                             ["source", "bytes", "mtime_ns", "sidecars", "sidecar_bytes",
                              *[f"dest_{i}" for i in n], "twin"])

    def test_rows_are_sorted_with_semicolon_cells_and_how_decided(self):
        b = reject("C:/shoot-a/B.ARW", rejected_by=[(SHOOT, "shoot"), (LIVE, "live")],
                   kept_by=[(LIVE, "live")], developed_in=[(LIVE, "live", "develop+pick")],
                   images=[(SHOOT, 2), (LIVE, 1)], held_by="C:/shoot-a/B.XMP")
        a = reject("C:/shoot-a/a.ARW")
        self.write([b, a])
        rows = rows_of(os.path.join(self.out, "rejects.csv"))
        self.assertEqual([r[0] for r in rows[1:]], ["C:/shoot-a/a.ARW", "C:/shoot-a/B.ARW"])
        self.assertEqual(rows[1][-1], f"images: {SHOOT}=1")
        self.assertEqual(rows[2][3], f"{SHOOT} (shoot);{LIVE} (live)")
        self.assertEqual(rows[2][-4], f"{LIVE} (live: develop+pick)")
        self.assertEqual(rows[2][-1], f"images: {SHOOT}=2; {LIVE}=1; held by: C:/shoot-a/B.XMP")

    def test_removal_list_holds_on_list_rows_only(self):
        keep = reject("C:/shoot-a/K.ARW", verdict="kept_for_work")
        gone = reject("C:/shoot-a/G.ARW", sidecars=["C:/shoot-a/G.XMP"], sidecar_bytes=7)
        self.write([keep, gone])
        rows = rows_of(os.path.join(self.out, "reject-removal-list.csv"))
        self.assertEqual(rows[1:], [["C:/shoot-a/G.ARW", "100", "5", "C:/shoot-a/G.XMP", "7", "D:/a/x", "E:/b/x", ""]])
        self.assertEqual(len(rows_of(os.path.join(self.out, "rejects.csv"))), 3)

    def test_removal_list_repeats_the_twin_in_its_last_column_for_a_lifted_row_only(self):
        lifted = reject("C:/shoot-a/L.ARW", twin=TWIN, twin_settings="abc123")
        plain = reject("C:/shoot-a/P.ARW")
        self.write([plain, lifted])
        rows = rows_of(os.path.join(self.out, "reject-removal-list.csv"))
        self.assertEqual([(r[0], r[-1]) for r in rows[1:]], [("C:/shoot-a/L.ARW", TWIN), ("C:/shoot-a/P.ARW", "")])

    def test_lifted_reject_that_travels_as_a_sidecar_has_no_removal_row_but_keeps_its_twin_cells(self):
        raw = reject("C:/shoot-a/IMG_0001.ARW", sidecars=["C:/shoot-a/IMG_0001.JPG"], sidecar_bytes=40)
        jpg = reject("C:/shoot-a/IMG_0001.JPG", bytes=40, twin=TWIN, twin_settings="abc123")
        self.write([raw, jpg])
        removal = rows_of(os.path.join(self.out, "reject-removal-list.csv"))
        self.assertEqual([r[0] for r in removal[1:]], ["C:/shoot-a/IMG_0001.ARW"])
        self.assertEqual(rows_of(os.path.join(self.out, "rejects.csv"))[2][-3:-1], [TWIN, "abc123"])
        self.assertEqual(self.section("Lifted by rule 26a"), [LIFTED_LINE.format(1, 0)])

    def test_the_summary_ends_with_the_sha256_of_the_removal_list_bytes_for_a_list_with_rows_and_without(self):
        for name, rejects in (("rows", [reject("C:/shoot-a/G.ARW"), reject("C:/shoot-a/H.ARW")]),
                              ("header only", [reject(verdict="kept")])):
            with self.subTest(name):
                self.write(rejects)
                with open(os.path.join(self.out, "reject-removal-list.csv"), "rb") as handle:
                    data = handle.read()
                self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
                self.assertEqual(len(data.splitlines()), 3 if name == "rows" else 1)
                self.assertEqual(self.read_summary().split("\n")[-4:],
                                 ["## Removal list", "", f"- SHA-256: {hashlib.sha256(data).hexdigest()}", ""])

    def test_lifted_section_sits_between_kept_in_live_and_not_checked(self):
        self.write([reject()])
        headings = [line for line in self.read_summary().split("\n") if line.startswith("## ")]
        at = headings.index("## Lifted by rule 26a")
        self.assertEqual(headings[at - 1:at + 2], ["## Kept in the live catalog", "## Lifted by rule 26a", "## Not checked"])

    def test_lifted_section_counts_zero_when_no_reject_is_lifted(self):
        self.write([reject(), reject("C:/shoot-a/B.ARW", sidecar_bytes=5)])
        self.assertEqual(self.section("Lifted by rule 26a"), [LIFTED_LINE.format(0, 0)])

    def test_lifted_section_counts_on_list_lifted_rejects_with_sidecar_bytes_and_no_other_verdict(self):
        lifted = reject("C:/shoot-a/L.ARW", bytes=100, sidecar_bytes=11, twin=TWIN, twin_settings="abc123")
        held = reject("C:/shoot-a/H.ARW", bytes=1000, verdict="kept_for_work", twin=TWIN, twin_settings="abc123")
        plain = reject("C:/shoot-a/P.ARW", bytes=10000)
        self.write([plain, held, lifted])
        self.assertEqual(self.section("Lifted by rule 26a"), [LIFTED_LINE.format(1, 111)])

    def test_no_reject_writes_headers_alone_and_three_paths(self):
        paths_ = self.write([])
        self.assertEqual(len(paths_), 3)
        self.assertEqual(len(rows_of(os.path.join(self.out, "rejects.csv"))), 1)
        self.assertEqual(len(rows_of(os.path.join(self.out, "reject-removal-list.csv"))), 1)

    def test_sidecar_that_is_also_a_reject_is_listed_once_and_counted_once(self):
        raw = reject("C:/shoot-a/IMG_0001.ARW", sidecars=["C:/shoot-a/IMG_0001.JPG"], sidecar_bytes=40)
        jpg = reject("C:/shoot-a/IMG_0001.JPG", bytes=40)
        self.write([jpg, raw])
        self.assertEqual(len(rows_of(os.path.join(self.out, "rejects.csv"))), 3)
        removal = rows_of(os.path.join(self.out, "reject-removal-list.csv"))
        self.assertEqual([r[0] for r in removal[1:]], ["C:/shoot-a/IMG_0001.ARW"])
        self.assertIn("| on_list | 2 | 140 |", self.read_summary())

    def test_summary_lists_every_verdict_with_bytes_including_sidecars(self):
        self.write([reject(sidecar_bytes=11)])
        text = self.read_summary()
        for verdict in VERDICTS:
            self.assertIn(f"| {verdict} | ", text)
        self.assertIn("| on_list | 1 | 111 |", text)
        self.assertIn("| missing | 0 | 0 |", text)

    def test_summary_sections_counts_and_not_checked(self):
        views = [view(SHOOT, "shoot", picks={"c:/shoot-a/img_0001.arw": [-1, 0]}),
                 view(LIVE, "live"), view("C:/cat/old.lrcat", "other", "unreadable", excluded=True),
                 view("C:/cat/mid.lrcat", "other")]
        rejects = [reject(verdict="coverage_incomplete", kept_by=[(LIVE, "live")]),
                   reject("C:/shoot-a/B.ARW", kept_by=[(SHOOT, "shoot")])]
        self.write(rejects, summary=summary(views, ["catalog C:/cat/x.lrcat not read"]))
        text = self.read_summary()
        lines = text.split("\n")
        self.assertEqual(lines[0], "# Reject check")
        self.assertEqual(lines[1], "Generated: 2026-11-18T10:00:00")
        self.assertNotIn("\r", text)
        self.assertIn(f"- {SHOOT} (shoot): 2 rejects", text)
        self.assertIn(f"- {LIVE} (live): 0 rejects", text)
        self.assertIn(f"- {SHOOT}: c:/shoot-a/img_0001.arw", text)
        self.assertIn("Rejects the live catalog keeps: 1", text)
        self.assertIn("- C:/cat/old.lrcat: unreadable", text)
        self.assertIn("- limiting: catalog C:/cat/x.lrcat not read", text)
        self.assertIn("Catalogs the tool never read are not known to it.", text)

    def test_summary_scans_section_lists_every_scan_a_verdict_rests_on(self):
        scans = [{"kind": "catalogs", "id": 3, "root": "C:/cat", "status": "complete", "finished": "t1"},
                 {"kind": "files", "id": 4, "root": "C:/shoot-a", "status": "partial", "finished": "t2"}]
        self.write([reject()], summary=summary()._replace(scans=scans))
        self.assertEqual(self.section("Scans"),
                         ["- catalogs scan 3 of C:/cat: complete, finished t1",
                          "- files scan 4 of C:/shoot-a: partial, finished t2"])

    def test_rejected_in_part_section_says_flagged_but_not_a_reject(self):
        views = [view(SHOOT, "shoot", picks={"c:/shoot-a/img_0001.arw": [-1, 0]})]
        self.write([], summary=summary(views))
        self.assertEqual(self.section("Rejected in part")[:3],
                         ["Flagged -1 but not a reject:", "", f"- {SHOOT}: c:/shoot-a/img_0001.arw"])

    def test_rejected_in_part_lists_relative_form_flags_apart_with_their_number(self):
        views = [view(SHOOT, "shoot", picks={"c:/a": [-1, 0], "c:/b": [-1], "c:/c": [-1]}, absolute={"c:/a"}),
                 view(LIVE, "live", picks={"c:/a": [0]})]
        self.write([], summary=summary(views))
        self.assertEqual(self.section("Rejected in part"),
                         ["Flagged -1 but not a reject:", "", f"- {SHOOT}: c:/a", "",
                          "Flagged -1 only through the catalog-relative form, which rejects nothing (rule 24):", "",
                          f"- {SHOOT}: 2 paths"])

    def test_rejected_in_part_says_none_for_each_empty_list(self):
        self.write([], summary=summary([view(SHOOT, "shoot", picks={"c:/a": [0]})]))
        self.assertEqual(self.section("Rejected in part"),
                         ["Flagged -1 but not a reject:", "", "- none", "",
                          "Flagged -1 only through the catalog-relative form, which rejects nothing (rule 24):", "",
                          "- none"])

    def test_how_decided_says_catalog_relative_form_only_for_a_zero_count(self):
        self.write([reject(images=[(SHOOT, 1), (LIVE, 0)])])
        self.assertEqual(rows_of(os.path.join(self.out, "rejects.csv"))[1][-1],
                         f"images: {SHOOT}=1; {LIVE}=0 (catalog-relative form only)")

    def test_per_catalog_lists_only_live_and_shoot_views(self):
        views = [view(SHOOT, "shoot"), view(LIVE, "live"), view("C:/cat/old.lrcat", "other"),
                 view("C:/cat/bak.lrcat", "backup", "unreadable", excluded=True)]
        self.write([reject()], summary=summary(views))
        self.assertEqual(self.section("Per catalog"),
                         [f"- {SHOOT} (shoot): 1 rejects", f"- {LIVE} (live): 0 rejects"])

    def test_not_checked_lists_only_excluded_views_and_limiting_causes(self):
        views = [view(SHOOT, "shoot"), view(LIVE, "live", "partial", excluded=True)]
        self.write([reject()], summary=summary(views, ["root D:/a not complete"]))
        self.assertEqual(self.section("Not checked"),
                         [f"- {LIVE}: partial", "- limiting: root D:/a not complete", "",
                          "Catalogs the tool never read are not known to it."])

    def test_not_checked_with_nothing_excluded_holds_only_the_never_read_sentence(self):
        self.write([reject()])
        self.assertEqual(self.section("Not checked"), ["- none", "", "Catalogs the tool never read are not known to it."])

    def test_csvs_start_with_a_bom(self):
        self.write([reject()])
        for name in ("rejects.csv", "reject-removal-list.csv"):
            with open(os.path.join(self.out, name), "rb") as handle:
                self.assertEqual(handle.read(3), b"\xef\xbb\xbf")

    def test_reject_with_wrong_number_of_roots_is_refused_before_any_write(self):
        for field in ("archive", "dests"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.write([reject(**{field: ["x"]})])
            self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))

    def test_sidecar_of_a_reject_that_is_not_on_list_stays_on_the_removal_list_with_its_bytes(self):
        raw = reject("C:/shoot-a/IMG_0001.ARW", verdict="kept_for_work", sidecars=["C:/shoot-a/IMG_0001.JPG"])
        jpg = reject("C:/shoot-a/IMG_0001.JPG", bytes=40)
        self.write([raw, jpg])
        removal = rows_of(os.path.join(self.out, "reject-removal-list.csv"))
        self.assertEqual([r[0] for r in removal[1:]], ["C:/shoot-a/IMG_0001.JPG"])
        self.assertIn("| on_list | 1 | 40 |", self.read_summary())

    def test_two_on_list_rejects_that_are_each_others_sidecar_both_leave_the_removal_list(self):
        # A stated limit: nothing is removed (fail-safe), and the pair adds no bytes under any verdict.
        a = reject("C:/shoot-a/A.ARW", sidecars=["C:/shoot-a/B.JPG"], sidecar_bytes=40)
        b = reject("C:/shoot-a/B.JPG", bytes=40, sidecars=["C:/shoot-a/A.ARW"], sidecar_bytes=100)
        self.write([a, b])
        self.assertEqual(len(rows_of(os.path.join(self.out, "rejects.csv"))), 3)
        self.assertEqual(len(rows_of(os.path.join(self.out, "reject-removal-list.csv"))), 1)
        self.assertIn("| on_list | 2 | 0 |", self.read_summary())

    def snapshot(self):
        names = ("rejects.csv", "reject-removal-list.csv", "rejects-summary.md")
        found = {}
        for n in names:
            with open(os.path.join(self.out, n), "rb") as handle:
                found[n] = handle.read()
        return found

    def test_rerun_with_same_input_is_byte_identical(self):
        self.write([reject()])
        first = self.snapshot()
        self.write([reject()])
        self.assertEqual(self.snapshot(), first)

    def test_failure_writing_any_tmp_changes_nothing_and_leaves_no_tmp(self):
        self.write([reject()])
        before = self.snapshot()
        for name in ("rejects.csv", "reject-removal-list.csv", "rejects-summary.md"):
            blocker = os.path.join(self.out, name + ".tmp")
            os.mkdir(blocker)
            with self.subTest(name=name):
                with self.assertRaises(OSError):
                    self.write([reject("C:/shoot-a/other.ARW")])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(sorted(n for n in os.listdir(self.out) if n.endswith(".tmp")), [name + ".tmp"])
            os.rmdir(blocker)

    def test_failure_replacing_leaves_no_tmp_and_no_summary(self):
        os.makedirs(os.path.join(self.out, "rejects.csv"))
        with open(os.path.join(self.out, "rejects-summary.md"), "w") as handle:
            handle.write("old")
        with self.assertRaises(OSError):
            self.write([reject()])
        self.assertEqual([n for n in os.listdir(self.out) if n.endswith(".tmp")], [])
        self.assertFalse(os.path.exists(os.path.join(self.out, "rejects-summary.md")))


if __name__ == "__main__":
    unittest.main()
