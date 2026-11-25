import os
import tempfile
import unittest

from lrclean import backup_plan
from lrclean.backup_plan import BackupPlan, RootPlan, Row


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _plan(base: str) -> BackupPlan:
    arc1, arc2 = os.path.join(base, "arc1"), os.path.join(base, "arc2")
    rows1 = [
        Row("C:/lib/shoot-a/IMG_0001.ARW", os.path.join(arc1, "shoot-a", "IMG_0001.ARW"),
            1234, 5, "copy", ""),
        Row("C:/lib/shoot-a/IMG_0002.ARW", os.path.join(arc1, "shoot-a", "IMG_0002.ARW"),
            0, -3, "skip_placeholder", "placeholder"),
    ]
    rows2 = [Row(r.source, r.dest.replace("arc1", "arc2"), r.bytes, r.mtime_ns, r.action, r.reason)
             for r in rows1]
    return BackupPlan(
        # folders, files (placeholders included), bytes, placeholders, left out, free:
        # distinct multi-digit values, so a line is found only where it belongs.
        # then the catalog files left out.
        [RootPlan(arc1, rows1, 41, 85, 1234567, 31, 47, 987654321, 5151),
         RootPlan(arc2, rows2, 52, 97, 7654321, 63, 78, 123456, 6262)],
        outside_raws=4242, unread_roots=["C:/lib/unread-one", "C:/lib/unread-two"], unlisted=3131,
        links=7373, not_selected=8484,
        not_selected_exts=(("txt", 9), ("", 5), ("xmp", 5), ("zip", 2)))


class WritePlanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.abspath(self._tmp.name)
        self.out = os.path.join(self.base, "tool", "out")
        self.plan = _plan(self.base)

    def _summary(self) -> str:
        with open(os.path.join(self.out, "backup-summary.md"), "rb") as handle:
            return handle.read().decode("utf-8")

    def test_manifests_follow_root_order_read_back_equal_and_repeat_byte_identical(self):
        written = backup_plan.write_plan(self.out, self.plan)
        for n, root in enumerate(self.plan.roots, start=1):
            path = os.path.join(self.out, f"backup-manifest-{n}.csv")
            self.assertIn(path, written)
            self.assertEqual(backup_plan.read_manifest(path), root.rows)
        first = {p: _read(p) for p in written}
        backup_plan.write_plan(self.out, self.plan)
        self.assertEqual({p: _read(p) for p in written}, first)

    def test_each_root_section_holds_its_own_labelled_figures_and_uses_lf_only(self):
        backup_plan.write_plan(self.out, self.plan)
        text = self._summary()
        self.assertNotIn("\r", text)
        sections = [part.splitlines() for part in text.split("## Archive root ")[1:]]
        wanted = [
            ["- folders: 41", "- files to copy: 54; placeholders skipped, apart from them: 31",
             "- bytes: 1234567", "- subfolders left out: 47", "- catalog files left out: 5151",
             "- free space: 987654321"],
            ["- folders: 52", "- files to copy: 34; placeholders skipped, apart from them: 63",
             "- bytes: 7654321", "- subfolders left out: 78", "- catalog files left out: 6262",
             "- free space: 123456"],
        ]
        self.assertEqual(len(sections), 2)
        for n, (lines, root, expected) in enumerate(zip(sections, self.plan.roots, wanted), 1):
            self.assertEqual(lines[0], f"{n}: {root.archive_root}")
            self.assertEqual([line for line in lines[1:] if line.startswith("- ")][:6], expected)
        for needle in ("4242", "C:/lib/unread-one", "C:/lib/unread-two", "3131"):
            self.assertIn(needle, text)

    def test_root_whose_bytes_exceed_free_space_says_so_and_a_fitting_root_does_not(self):
        backup_plan.write_plan(self.out, self.plan)
        fitting, short = [part.splitlines() for part in
                          self._summary().split("## Archive root ")[1:]]
        self.assertIn("- NOT ENOUGH FREE SPACE: needs 7530865 more bytes", short)
        self.assertFalse([line for line in fitting if "NOT ENOUGH" in line])

    def test_selection_section_lists_extensions_counts_and_the_tally_once_before_outside(self):
        backup_plan.write_plan(self.out, self.plan)
        text = self._summary()
        self.assertEqual(text.count("## Selection"), 1)
        self.assertLess(text.index("## Archive root 2"), text.index("## Selection"))
        self.assertLess(text.index("## Selection"), text.index("## Outside the photo folder"))
        section = text.split("## Selection\n")[1].split("\n## ")[0].splitlines()
        # The design's list, written out on purpose (as in test_backup_select), not BACKUP_EXTS.
        exts = ("arw cr2 cr3 dng nef nrw orf pef raf rw2 srw jpg jpeg heic heif png tif tiff psd "
                "psb gif webp bmp mp4 mov m4v avi mts m2ts 3gp mkv").split()
        self.assertEqual(section, [
            "", "- extensions that select a folder: " + ", ".join(sorted(exts)),
            "- links and junctions not followed: 7373", "- folders not selected: 8484",
            "  - txt: 9", "  - (none): 5", "  - xmp: 5", "  - zip: 2"])

    def test_a_plan_with_unread_folders_opens_with_them_and_one_without_has_no_such_section(self):
        folders = ("C:/lib/shoot-a", "C:/lib/shoot-b")
        backup_plan.write_plan(self.out, self.plan._replace(unread_folders=folders))
        opened = self._summary().splitlines()
        self.assertEqual(opened[:2], ["# Backup plan", ""])
        self.assertEqual(opened[2], "## NOT READ: folders that could not be listed")
        self.assertLess(self._summary().index("NOT READ"), self._summary().index("## Archive root 1"))
        for folder in folders:
            self.assertIn(f"- {folder}", opened[:10])
        self.assertIn("not one to approve", self._summary())
        backup_plan.write_plan(self.out, self.plan)
        self.assertNotIn("NOT READ", self._summary())

    def test_manifest_of_a_root_missing_from_the_new_plan_is_removed_and_nothing_else(self):
        backup_plan.write_plan(self.out, self.plan)
        for name in ("backup-manifest-3.csv", "backup-manifest-12.csv", "notes.txt"):
            with open(os.path.join(self.out, name), "w", encoding="utf-8") as handle:
                handle.write("x")
        backup_plan.write_plan(self.out, self.plan._replace(roots=self.plan.roots[:1]))
        self.assertEqual(sorted(os.listdir(self.out)),
                         ["backup-manifest-1.csv", "backup-summary.md", "notes.txt"])

    def test_only_listed_files_are_created_and_nothing_under_an_archive_root(self):
        written = backup_plan.write_plan(self.out, self.plan)
        found = [os.path.join(self.out, name) for name in os.listdir(self.out)]
        self.assertEqual(sorted(written), sorted(found))
        self.assertEqual(len(written), 3)
        for root in self.plan.roots:
            self.assertFalse(os.path.exists(root.archive_root))
        self.assertEqual(sorted(os.listdir(os.path.join(self.base, "tool"))), ["out"])


if __name__ == "__main__":
    unittest.main()
