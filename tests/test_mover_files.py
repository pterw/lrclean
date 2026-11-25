import os
import tempfile
import unittest
from types import SimpleNamespace

from lrclean import mover_files, paths
from mover_world import World


def stat_of(dev: int, ino: int):
    return SimpleNamespace(st_dev=dev, st_ino=ino)


class WorldCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.world = World(tmp.name)
        self.ctx = self.world.ctx()


class BatchTests(unittest.TestCase):
    def test_rule_39_a_batch_name_is_the_start_stamp_without_separators(self):
        self.assertEqual(mover_files.batch_name("2026-11-19T10:15:00Z"), "20261119T101500Z")

    def test_rule_39_valid_batch_accepts_only_a_whole_stamp(self):
        cases = {"20261119T101500Z": True, "..\\x": False, "20261119T1015Z": False,
                 "20261119T101500Zx": False, "20261119T101500Z\n": False, "x20261119T101500Z": False,
                 "２0261119T101500Z": False, "": False,
                 "20261119\\101500Z": False, "20261119/101500Z": False, "20261119.101500Z": False,
                 "20261119T101500": False, "20261119T101500Q": False,
                 "2026111T101500Z": False, "202611190T101500Z": False,
                 "20261119T10150Z": False, "20261119T1015000Z": False}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(mover_files.valid_batch(text), expected)


class StagedPathTests(unittest.TestCase):
    def test_rule_37_staged_path_mirrors_the_original_under_the_lowercase_drive(self):
        base, batch = os.path.join("D:\\", "tool"), "20261119T101500Z"
        for original in ("C:\\a\\b.arw", "c:\\a\\b.arw", "C:/a/b.arw", "C:\\a\\b.arw\\"):
            with self.subTest(original=original):
                self.assertEqual(mover_files.staged_path(base, batch, original),
                                 os.path.join(base, "work", "staging", batch, "c", "a", "b.arw"))

    def test_rule_37_staged_path_keeps_the_spelling_of_the_rest(self):
        self.assertEqual(mover_files.staged_path("D:\\t", "b", "E:\\Shoot-A\\IMG_0001.ARW"),
                         os.path.join("D:\\t", "work", "staging", "b", "e", "Shoot-A", "IMG_0001.ARW"))

    def test_rule_37_a_path_that_is_not_plain_and_absolute_with_a_drive_is_refused(self):
        for original in ("\\a\\b.arw", "a\\b.arw", "b.arw", "", "\\a\\b.arw:stream", "1:\\a\\b.arw",
                         "C:\\a\\..\\..\\x", "C:\\a\\b.arw:stream", "C:\\a:\\b.arw", "C:\\a\\z:\\evil",
                         "C:", "C:\\", "C:a\\b"):
            with self.subTest(original=original), self.assertRaises(ValueError):
                mover_files.staged_path("D:\\t", "b", original)


class ResolveTests(WorldCase):
    def with_listing(self, names):
        seams = self.ctx.seams._replace(listdir=lambda folder: list(names))
        return self.ctx._replace(seams=seams)

    def test_rule_40_resolve_finds_the_entry_whatever_the_case_of_the_name(self):
        path = self.world.add("shoot-a/IMG_0001.arw", b"x", 1)
        folder = os.path.dirname(path)
        self.assertEqual(mover_files.resolve(folder, "IMG_0001.ARW", self.ctx), ["IMG_0001.arw"])

    def test_rule_40_resolve_gives_both_entries_of_one_casefold_name(self):
        ctx = self.with_listing(["IMG_0001.ARW", "img_0001.arw", "IMG_0002.ARW"])
        self.assertEqual(mover_files.resolve("f", "Img_0001.arw", ctx), ["IMG_0001.ARW", "img_0001.arw"])

    def test_rule_40_resolve_compares_composed_and_decomposed_forms_as_one_name(self):
        ctx = self.with_listing(["e\u0301.arw"])
        self.assertEqual(mover_files.resolve("f", "\u00e9.ARW", ctx), ["e\u0301.arw"])

    def test_rule_40_resolve_gives_nothing_for_an_unknown_name_or_another_spelling_of_one(self):
        ctx = self.with_listing(["IMG_0001.ARW"])
        for name in ("IMG_0002.ARW", "IMG_0001.ARW.", "IMG_0001.ARW ", "IMG_0001.ARW:stream", "IMG_00~1.ARW"):
            with self.subTest(name=name):
                self.assertEqual(mover_files.resolve("f", name, ctx), [])


class IdentityTests(unittest.TestCase):
    def test_rule_35_distinct_refuses_an_equal_pair_in_any_position(self):
        a, b, c = stat_of(1, 10), stat_of(2, 20), stat_of(3, 30)
        equal_pair = {"first and second": [a, stat_of(1, 10), c], "second and last": [a, b, stat_of(2, 20)],
                      "first and last": [a, b, stat_of(1, 10)], "two stats": [a, stat_of(1, 10)]}
        for position, stats in equal_pair.items():
            with self.subTest(position):
                self.assertFalse(mover_files.distinct(stats))

    def test_rule_35_distinct_accepts_pairs_that_differ_in_either_field(self):
        cases = {"all differ": [stat_of(1, 10), stat_of(2, 20), stat_of(3, 30)],
                 "same ino, other dev": [stat_of(1, 10), stat_of(2, 10), stat_of(3, 10)],
                 "same dev, other ino": [stat_of(1, 10), stat_of(1, 20), stat_of(1, 30)],
                 "one stat": [stat_of(1, 10)], "none": []}
        for case, stats in cases.items():
            with self.subTest(case):
                self.assertTrue(mover_files.distinct(stats))

    def test_rule_35_other_volume_compares_the_device_only(self):
        cases = [(stat_of(1, 10), stat_of(2, 10), True), (stat_of(1, 10), stat_of(2, 20), True),
                 (stat_of(1, 10), stat_of(1, 10), False), (stat_of(1, 10), stat_of(1, 20), False)]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(mover_files.other_volume(a, b), expected)


class LinkInPathTests(WorldCase):
    def setUp(self):
        super().setUp()
        self.root = self.world.photo_root
        self.between = os.path.join(self.root, "shoot-a")
        self.between2 = os.path.join(self.between, "day-1")
        self.parent = os.path.join(self.between2, "raw")
        self.target = os.path.join(self.parent, "IMG_0001.ARW")

    def found(self, *links):
        self.world.links.update(links)
        return mover_files.link_in_path(self.root, self.target, self.ctx)

    def test_rule_40_a_link_at_each_position_of_the_walk_is_named(self):
        for name, link in {"photo_root": self.root, "a folder between": self.between,
                           "another folder between": self.between2, "the parent": self.parent}.items():
            with self.subTest(name):
                self.world.links.clear()
                self.assertEqual(self.found(link), link)

    def test_rule_40_the_first_link_from_photo_root_down_is_named(self):
        self.assertEqual(self.found(self.parent, self.between2, self.root), self.root)
        self.world.links.clear()
        self.assertEqual(self.found(self.parent, self.between2), self.between2)

    def test_rule_40_no_link_gives_an_empty_text_and_the_target_itself_is_not_tested(self):
        self.assertEqual(self.found(), "")
        self.assertEqual(self.found(self.target), "")

    def test_rule_40_a_folder_with_only_the_reparse_attribute_is_not_a_link(self):
        for folder in (self.root, self.between, self.parent):
            self.world.attrs[paths.norm(folder)] = 0x400
        self.assertEqual(self.found(), "")

    def test_rule_40_the_walk_compares_photo_root_through_paths_norm(self):
        self.world.links.add(self.between)
        self.assertEqual(mover_files.link_in_path(self.root.upper(), self.target, self.ctx), self.between)

    def test_rule_40_a_target_not_strictly_below_photo_root_is_named_by_its_absolute_path_and_never_raises(self):
        outside = [os.path.join(self.world.base, "f.arw"), self.root, self.root + "-x\\f.arw", "f.arw", ""]
        for target in outside:
            with self.subTest(target=target):
                found = mover_files.link_in_path(self.root, target, self.ctx)
                self.assertEqual(found, os.path.abspath(target))
                self.assertNotEqual(found, "")

    def test_rule_40_a_target_directly_in_photo_root_tests_photo_root_only(self):
        target = os.path.join(self.root, "IMG_0001.ARW")
        self.world.links.update({self.between, target})
        self.assertEqual(mover_files.link_in_path(self.root, target, self.ctx), "")
        self.world.links.add(self.root)
        self.assertEqual(mover_files.link_in_path(self.root, target, self.ctx), self.root)


if __name__ == "__main__":
    unittest.main()
