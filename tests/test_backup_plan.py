import os
import shutil
import tempfile
import unittest

from lrclean import backup_plan
from lrclean.backup_plan import ManifestError, Row, archive_index, check_archive_roots, is_inside


class ManifestIoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "out", "m.csv")

    def _write_raw(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)

    def test_round_trip_keeps_comma_quote_and_non_ascii_and_writes_bom(self):
        rows = [
            Row('C:/lib/shoot-a/IMG,"1".ARW', "D:/arc/shoot-ä/IMG_0001.ARW", 5, 7, "copy", ""),
            Row("C:/lib/shoot-a/x.ARW", "D:/arc/x.ARW", 0, 1, "skip_placeholder", "placeholder"),
        ]
        backup_plan.write_manifest(self.path, rows)
        with open(self.path, "rb") as handle:
            self.assertTrue(handle.read().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(backup_plan.read_manifest(self.path), rows)

    def test_round_trip_keeps_negative_mtime_of_file_dated_before_1970(self):
        rows = [Row("C:/lib/shoot-a/x.ARW", "D:/arc/x.ARW", 5, -86400 * 10**9, "copy", "")]
        backup_plan.write_manifest(self.path, rows)
        self.assertEqual(backup_plan.read_manifest(self.path), rows)

    def test_empty_manifest_writes_header_and_reads_back_empty(self):
        backup_plan.write_manifest(self.path, [])
        self.assertEqual(backup_plan.read_manifest(self.path), [])

    def test_bad_manifests_raise_manifest_error(self):
        header = ",".join(backup_plan.COLUMNS) + "\n"
        cases = {
            "empty file": "",
            "wrong header": "a,b,c\n",
            "wrong header of six names": "a,b,c,d,e,f\n",
            "non-integer bytes": header + "s,d,x,7,copy,\n",
            "non-integer mtime": header + "s,d,5,x,copy,\n",
            "signed bytes": header + "s,d,-5,7,copy,\n",
            "plus-signed mtime": header + "s,d,5,+7,copy,\n",
            "double-minus mtime": header + "s,d,5,--5,copy,\n",
            "bare minus mtime": header + "s,d,5,-,copy,\n",
            "padded bytes": header + "s,d, 5 ,7,copy,\n",
            "underscored mtime": header + "s,d,5,1_0,copy,\n",
            "non-ASCII digit bytes": header + "s,d,٥,7,copy,\n",
            "unknown action": header + "s,d,5,7,move,\n",
            "empty dest": header + "s,,5,7,copy,\n",
            "empty source": header + ",d,5,7,copy,\n",
            "short row": header + "s,d,5\n",
        }
        for name, text in cases.items():
            with self.subTest(name):
                self._write_raw(text)
                with self.assertRaises(ManifestError):
                    backup_plan.read_manifest(self.path)

    def test_missing_file_raises_manifest_error(self):
        with self.assertRaises(ManifestError):
            backup_plan.read_manifest(self.path)


class CheckArchiveRootsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.abspath(self._tmp.name)
        self.lib = os.path.join(self.base, "lib")
        self.own = os.path.join(self.base, "tool")
        self.arc1 = os.path.join(self.base, "arc1")
        self.arc2 = os.path.join(self.base, "arc2")

    def check(self, roots, volumes=None, library=None):
        mapping = volumes or {self.arc1: "v1", self.arc2: "v2"}
        check_archive_roots(
            roots,
            library_roots=[self.lib] if library is None else library,
            own_dir=self.own,
            volume_of=lambda p: mapping.get(p, "v9"),
        )

    def test_accepts_two_roots_on_different_volumes(self):
        self.check([self.arc1, self.arc2])

    def test_accepts_root_sharing_only_a_name_prefix_with_a_library_root(self):
        self.check([os.path.join(self.base, "lib2")], volumes={os.path.join(self.base, "lib2"): "v1"})

    def test_refuses_empty_list(self):
        with self.assertRaises(ValueError):
            self.check([])

    def test_refuses_relative_root(self):
        with self.assertRaises(ValueError):
            self.check(["arc"], volumes={})

    def test_refuses_root_equal_to_or_inside_a_library_root_or_tool_folder(self):
        for protected in (self.lib, self.own):
            spellings = {
                "equal": protected,
                "upper": protected.upper(),
                "backslash": protected.replace("/", "\\"),
                "dotdot": os.path.join(protected, "x", ".."),
                "inside": os.path.join(protected, "deep"),
                "inside upper": os.path.join(protected, "DEEP").upper(),
                "inside dotdot": os.path.join(protected, "a", "..", "b"),
            }
            for name, root in spellings.items():
                with self.subTest(protected=protected, spelling=name):
                    with self.assertRaises(ValueError) as caught:
                        self.check([root], volumes={os.path.abspath(root): "v1"})
                    self.assertIn(root, str(caught.exception))

    def test_refuses_root_holding_a_library_root_or_tool_folder(self):
        library = [os.path.join(self.base, "x", "lib")]
        cases = {
            "holds library root": os.path.join(self.base, "x"),
            "holds tool folder": self.base,
        }
        for name, root in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.check([root], volumes={root: "v1"}, library=library)

    def test_refuses_root_with_segment_ending_in_dot_or_space(self):
        cases = {
            "trailing dot": os.path.join(self.base, "lib."),
            "trailing space": os.path.join(self.base, "lib "),
            "inner dot segment": os.path.join(self.base, "arc. ", "x"),
        }
        for name, root in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.check([root], volumes={root: "v1"})

    def test_refuses_two_roots_on_one_volume(self):
        with self.assertRaises(ValueError):
            self.check([self.arc1, self.arc2], volumes={self.arc1: "v1", self.arc2: "v1"})

    def test_refuses_root_with_empty_volume(self):
        with self.assertRaises(ValueError):
            self.check([self.arc1], volumes={self.arc1: ""})


class ArchiveIndexTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.abspath(self._tmp.name)
        self.arc1 = os.path.join(self.base, "arc")
        self.arc2 = os.path.join(self.base, "arc2")

    def rows(self, *dests):
        return [Row("C:/lib/a", dest, 1, 1, "copy", "") for dest in dests]

    def test_index_is_one_based_and_follows_root_order(self):
        roots = [self.arc1, self.arc2]
        self.assertEqual(archive_index(self.rows(os.path.join(self.arc1, "a")), roots), 1)
        self.assertEqual(archive_index(self.rows(os.path.join(self.arc2, "a")), roots), 2)

    def test_refused_dests(self):
        one, two = [self.arc1], [self.arc1, self.arc2]
        valid = os.path.join(self.arc1, "a")
        outside = os.path.join(self.arc1, "..", "x")
        cases = {
            "no rows": (one, []),
            "relative": (one, self.rows("rel/x")),
            "dotdot escape": (one, self.rows(outside)),
            "valid first then outside": (one, self.rows(valid, outside)),
            "outside then valid last": (one, self.rows(outside, valid)),
            "equal to root": (one, self.rows(self.arc1)),
            "sibling sharing prefix": (one, self.rows(os.path.join(self.base, "arc-other", "x"))),
            "split over two roots": (
                two,
                self.rows(valid, os.path.join(self.arc2, "b")),
            ),
        }
        for name, (roots, rows) in cases.items():
            with self.subTest(name):
                with self.assertRaises(ManifestError):
                    archive_index(rows, roots)


class IsInsideTest(unittest.TestCase):
    def test_boundary_case_and_below_only(self):
        self.assertTrue(is_inside("c:/a/b/c", "C:\\A\\B"))
        self.assertTrue(is_inside("c:/a/b", "c:/a/b"))
        self.assertFalse(is_inside("c:/a/b", "c:/a/b", below_only=True))
        self.assertFalse(is_inside("c:/a/bc", "c:/a/b"))
        self.assertFalse(is_inside("c:/a/b/../x", "c:/a/b"))

    def test_drive_root_as_outer_folder(self):
        self.assertTrue(is_inside("c:/arc", "C:\\"))


class FreeSpaceTest(unittest.TestCase):
    def test_missing_folder_answers_like_its_existing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "not", "yet")
            self.assertAlmostEqual(
                backup_plan.free_space(missing), shutil.disk_usage(tmp).free, delta=10_000_000
            )


if __name__ == "__main__":
    unittest.main()
