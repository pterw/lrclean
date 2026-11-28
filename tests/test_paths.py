import os
import tempfile
import unittest

from lrclean import paths, store


class NormTests(unittest.TestCase):
    def test_rule3_spellings_of_one_path_are_equal(self):
        forms = ["C:\\Shoot-A\\IMG_0001.ARW", "c:/shoot-a/img_0001.arw",
                 "\\\\?\\C:\\Shoot-A\\IMG_0001.ARW"]
        self.assertEqual(len({paths.norm(f) for f in forms}), 1)

    def test_rule3_nfd_and_nfc_spellings_are_equal(self):
        nfd = "C:\\Shoot-A\\cafe\u0301.ARW"
        nfc = "c:/shoot-a/caf\u00e9.arw"
        self.assertEqual(paths.norm(nfd), paths.norm(nfc))

    def test_rule3_different_files_stay_apart(self):
        self.assertNotEqual(paths.norm("c:/a/IMG_0001.ARW"), paths.norm("c:/a/IMG_0002.ARW"))
        self.assertNotEqual(paths.norm("c:/a/Stra\u00dfe.ARW"), paths.norm("c:/a/Strasse.ARW"))

    def test_norm_empty_stays_empty(self):
        self.assertEqual(paths.norm(""), "")

    def test_norm_and_volume_see_through_extended_unc_prefix(self):
        self.assertEqual(paths.norm("\\\\?\\UNC\\server\\share\\x"),
                         paths.norm("\\\\server\\share\\x"))
        self.assertEqual(paths.volume("\\\\?\\UNC\\server\\share\\x"), "")

    def test_trailing_slash_dropped_except_drive_root(self):
        self.assertEqual(paths.norm("C:\\Shoot-A\\"), "c:/shoot-a")
        self.assertEqual(paths.norm("C:\\"), "c:/")


class FsPathTests(unittest.TestCase):
    def test_rule4_prefix_only_above_247(self):
        base = "C:\\" + "a" * 244
        self.assertEqual(len(base), 247)
        self.assertEqual(paths.fs_path(base), base)
        self.assertEqual(paths.fs_path(base + "b"), "\\\\?\\" + base + "b")

    def test_rule4_long_relative_path_is_unchanged(self):
        rel = "a/" * 200
        self.assertEqual(paths.fs_path(rel), rel)

    def test_rule4_long_unc_path_gets_unc_prefix(self):
        unc = "\\\\server\\share\\" + "a" * 260
        self.assertTrue(paths.fs_path(unc).startswith("\\\\?\\UNC\\server\\share"))

    def test_rule4_file_over_260_chars_is_readable_through_fs_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(os.path.abspath(tmp), *["d" * 60] * 4)
            target = os.path.join(folder, "IMG_0001.ARW")
            self.assertGreater(len(target), 260)
            os.makedirs(paths.fs_path(folder))
            with open(paths.fs_path(target), "wb") as handle:
                handle.write(b"raw")
            with open(paths.fs_path(target), "rb") as handle:
                self.assertEqual(handle.read(), b"raw")


class NameUnitsTests(unittest.TestCase):
    def test_counts_utf16_code_units_including_a_lone_surrogate(self):
        cases = {
            "abc": 3,
            "\u00e9": 1,
            "\u65e5\u672c": 2,
            "\U0001F4F7": 2,
            "a\U0001F4F7": 3,
            "\ud800": 1,
        }
        for name, units in cases.items():
            with self.subTest(name=name):
                self.assertEqual(paths.name_units(name), units)


class SplitTests(unittest.TestCase):
    def test_split_path_mixed_slashes(self):
        self.assertEqual(paths.split_path("c:/a\\b/IMG_0001.ARW"), ("c:/a\\b", "IMG_0001.ARW"))
        self.assertEqual(paths.split_path("c:\\a\\IMG_0001.ARW"), ("c:\\a", "IMG_0001.ARW"))

    def test_split_path_bare_name(self):
        self.assertEqual(paths.split_path("IMG_0001.ARW"), ("", "IMG_0001.ARW"))

    def test_volume(self):
        self.assertEqual(paths.volume("C:\\a"), "c:")
        self.assertEqual(paths.volume("\\\\server\\share"), "")

    def test_container_round_trip(self):
        value = paths.make_container("storage", "c:/shoot-a")
        self.assertEqual(value, "storage:c:/shoot-a")
        self.assertEqual(paths.split_container(value), ("storage", "c:/shoot-a"))
        self.assertIsNone(paths.split_container(""))


class PlaceholderTests(unittest.TestCase):
    def test_rule7_each_bit_alone_and_together(self):
        for attrs in (0x1000, 0x40000, 0x400000, 0x1000 | 0x40000 | 0x400000):
            self.assertTrue(paths.is_placeholder(attrs), hex(attrs))

    def test_rule7_other_bits_are_not_placeholders(self):
        self.assertFalse(paths.is_placeholder(0x20))
        self.assertFalse(paths.is_placeholder(0))
        self.assertTrue(paths.is_placeholder(0x20 | 0x40000))


class Signed64Tests(unittest.TestCase):
    def test_values_wrap_to_signed_64_bit_and_can_be_stored(self):
        for same in (0, 5, 2**63 - 1):
            self.assertEqual(paths.signed64(same), same)
        self.assertEqual(paths.signed64(2**63), -2**63)
        self.assertEqual(paths.signed64(2**64 - 1), -1)
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.connect(os.path.join(tmp, "work.db"))
            try:
                wrapped = paths.signed64(2**63 + 5)
                row_id = store.insert_row(conn, "file", file_index=wrapped)
                conn.commit()
                got = conn.execute('SELECT file_index FROM "file" WHERE id = ?',
                                   (row_id,)).fetchone()[0]
                self.assertEqual(got, wrapped)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
