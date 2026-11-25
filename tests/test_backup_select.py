import builtins
import contextlib
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from lrclean import backup_plan, inventory, paths, store
from lrclean.backup_plan import ManifestError, plan_manifests


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.lib = os.path.join(self.tmp, "lib")
        self.photos = os.path.join(self.lib, "photos")
        self.own = os.path.join(self.lib, "tool")
        self.arc = os.path.join(self.tmp, "arc-a")
        os.makedirs(self.photos)
        os.makedirs(self.own)
        self.conn = store.connect(os.path.join(self.tmp, "w.db"))
        self.addCleanup(self.conn.close)

    def make(self, *parts, data=b"x"):
        path = os.path.join(self.photos, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def scan(self):
        return inventory.scan_files(
            self.conn, [self.lib], catalog_roots=[], exclude=[], own_dir=self.own)

    def catalogs_scan(self, *records):
        """A stored catalogs scan, as inventory reads it; records name files in .lrdata folders."""
        scan_id = store.begin_scan(self.conn, self.lib, "catalogs")
        cat_id = store.insert_row(
            self.conn, "catalog", scan_id=scan_id, source_path=self.lib + "/a.lrcat",
            snapshot_path="", role="library", status="ok", size=0, mtime_ns=0,
            db_version="1", wal_bytes=0, has_lock=0)
        for path in records:
            store.insert_row(self.conn, "record", catalog_id=cat_id, path_norm=paths.norm(path),
                             path_orig=path, via="absolute", ext="arw", capture_time="",
                             is_sidecar=0)
        store.finish_scan(self.conn, scan_id, "complete")

    def plan(self, archive_roots=None, **kw):
        args = dict(
            library_roots=[self.lib], photo_root=self.photos,
            archive_roots=archive_roots or [self.arc], own_dir=self.own,
            free_bytes=lambda path: 1000)
        args.update(kw)
        return plan_manifests(self.conn, **args)

    def sources(self, plan, n=0):
        return [os.path.relpath(r.source, self.photos).replace("\\", "/") for r in plan.roots[n].rows]


class SelectionTest(Base):
    def test_selection_is_by_content_never_by_name(self):
        self.make("shoot-2024", "notes.txt")
        self.make("odd name", "IMG_0001.ARW")
        self.make("card.lrdata", "IMG_0002.ARW")
        self.make("IMG_0003.ARW")
        self.make("jpeg-only", "IMG_0004.JPG")
        self.make("sidecar-only", "IMG_0005.xmp")
        self.scan()
        plan = self.plan()
        self.assertEqual(sorted(self.sources(plan)),
                         ["IMG_0003.ARW", "card.lrdata/IMG_0002.ARW", "jpeg-only/IMG_0004.JPG",
                          "odd name/IMG_0001.ARW"])
        self.assertEqual(plan.roots[0].folders, 4)
        self.assertEqual(plan.not_selected, 2)

    def test_a_raw_outside_photo_root_selects_nothing_and_is_counted(self):
        other = os.path.join(self.lib, "elsewhere")
        os.makedirs(other)
        with open(os.path.join(other, "IMG_0009.ARW"), "wb") as handle:
            handle.write(b"x")
        self.make("shoot-a", "IMG_0001.ARW")
        self.scan()
        plan = self.plan()
        self.assertEqual(plan.outside_raws, 1)
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.ARW"])

    def test_a_folder_inside_own_dir_is_never_selected_even_when_a_scan_row_names_it(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.scan()
        scan_id = store.latest_scans(self.conn, "files")[0]["id"]
        inside = self.own + "/shoot-x/IMG_0005.ARW"
        os.makedirs(self.own + "/shoot-x")
        with open(inside, "wb") as handle:
            handle.write(b"x")
        store.insert_row(
            self.conn, "file", scan_id=scan_id, volume=paths.volume(inside),
            path_norm=paths.norm(inside), path_orig=inside, ext="arw", size=1, mtime_ns=1,
            attrs=0, placeholder=0, container="", file_index=0, nlink=1, checked_at="t")
        photos_in_own = backup_plan.plan_manifests(
            self.conn, library_roots=[self.lib], photo_root=self.lib, archive_roots=[self.arc],
            own_dir=self.own, free_bytes=lambda path: 1)
        self.assertEqual([os.path.basename(r.source) for r in photos_in_own.roots[0].rows],
                         ["IMG_0001.ARW"])
        self.assertEqual(photos_in_own.roots[0].folders, 1)
        self.assertEqual(photos_in_own.unlisted, 0)
        self.assertEqual(photos_in_own.not_selected, 0)


class ListingTest(Base):
    def test_folders_are_listed_afresh_with_any_file_type(self):
        gone = self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-a", "IMG_0002.ARW")
        self.make("shoot-a", "IMG_0002.xmp")
        self.scan()
        os.remove(gone)
        self.make("shoot-a", "notes.txt")
        self.make("shoot-a", "noext")
        self.make("shoot-a", "added-later.jpg")
        def reversed_scandir(path):
            return contextlib.nullcontext(list(reversed(list(os.scandir(path)))))

        self.assertEqual(
            self.sources(self.plan(scandir=reversed_scandir)),
            sorted(["shoot-a/IMG_0002.ARW", "shoot-a/IMG_0002.xmp", "shoot-a/notes.txt",
                    "shoot-a/noext", "shoot-a/added-later.jpg"], key=paths.norm))

    def test_a_subfolder_holding_a_raw_is_selected_alone_and_one_without_is_left_out(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-a", "with-raw", "IMG_0002.ARW")
        self.make("shoot-a", "without", "readme.txt")
        self.scan()
        plan = self.plan()
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.ARW", "shoot-a/with-raw/IMG_0002.ARW"])
        self.assertEqual(plan.roots[0].left_out, 1)
        self.assertEqual(plan.roots[0].folders, 2)

    def test_a_folder_left_out_of_the_listing_is_also_counted_by_the_walk(self):
        self.make("shoot-a", "IMG_0001.jpg")
        self.make("shoot-a", "with-raw", "IMG_0002.arw")
        self.make("shoot-a", "without", "readme.txt")
        self.scan()
        plan = self.plan()
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.jpg", "shoot-a/with-raw/IMG_0002.arw"])
        self.assertEqual(plan.roots[0].folders, 2)
        self.assertEqual(plan.roots[0].left_out, 1)
        self.assertEqual((plan.not_selected, plan.not_selected_exts), (1, (("txt", 1),)))

    def test_a_directory_symlink_or_junction_is_neither_a_row_nor_a_folder(self):
        self.make("shoot-a", "IMG_0001.ARW")
        target = os.path.join(self.tmp, "target")
        os.makedirs(target)
        with open(os.path.join(target, "IMG_0007.ARW"), "wb") as handle:
            handle.write(b"x")
        made = False
        link = os.path.join(self.photos, "shoot-a", "sym")
        try:
            os.symlink(target, link, target_is_directory=True)
            made = True
        except (OSError, NotImplementedError):
            pass
        junction = os.path.join(self.photos, "shoot-a", "junc")
        if subprocess.run(["cmd", "/c", "mklink", "/J", junction, target],
                          capture_output=True).returncode == 0:
            made = True
        if not made:
            self.skipTest("cannot make a symlink or a junction")
        self.scan()
        plan = self.plan()
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.ARW"])
        self.assertEqual(plan.roots[0].left_out, 0)

    def test_an_unlistable_folder_and_an_unstatable_file_are_counted_and_yield_no_row(self):
        good = self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-b", "IMG_0002.ARW")
        bad = self.make("shoot-a", "bad.txt")
        self.scan()
        real_scandir, real_stat = os.scandir, os.stat

        def scandir(path):
            if path.endswith("shoot-b"):
                raise OSError("denied")
            return real_scandir(path)

        def stat(path):
            if path.endswith("bad.txt"):
                raise OSError("denied")
            return real_stat(path)

        plan = self.plan(scandir=scandir, stat=stat)
        self.assertEqual(plan.unlisted, 2)
        self.assertEqual(plan.unread_folders, (os.path.join(self.photos, "shoot-b"),))
        self.assertEqual(plan.roots[0].folders, 1)
        self.assertEqual([r.source for r in plan.roots[0].rows], [good])
        self.assertNotIn(bad, [r.source for r in plan.roots[0].rows])


# The design's list, written out on purpose: a test over BACKUP_EXTS itself would shrink with it.
DESIGN_EXTS = (
    "arw cr2 cr3 dng nef nrw orf pef raf rw2 srw jpg jpeg heic heif png tif tiff psd psb gif "
    "webp bmp mp4 mov m4v avi mts m2ts 3gp mkv").split()


class ExtensionTest(Base):
    def test_backup_exts_is_exactly_the_design_list(self):
        self.assertEqual(backup_plan.BACKUP_EXTS, frozenset(DESIGN_EXTS))

    def test_each_listed_extension_selects_its_folder_and_the_file_is_a_row(self):
        for ext in DESIGN_EXTS:
            self.make(f"dir-{ext}", f"IMG_0001.{ext}")
        self.make("upper", "IMG_0002.JPG")
        self.scan()
        plan = self.plan()
        found = self.sources(plan)
        for ext in DESIGN_EXTS:
            with self.subTest(ext=ext):
                self.assertIn(f"dir-{ext}/IMG_0001.{ext}", found)
        self.assertIn("upper/IMG_0002.JPG", found)
        self.assertEqual(plan.roots[0].folders, len(DESIGN_EXTS) + 1)
        self.assertEqual(plan.not_selected, 0)

    def test_an_off_list_extension_selects_nothing_and_is_tallied(self):
        self.make("shoot-a", "scan.pdf")
        self.scan()
        plan = self.plan()
        self.assertEqual(plan.roots[0].rows, [])
        self.assertEqual((plan.not_selected, plan.not_selected_exts), (1, (("pdf", 1),)))


class NotSelectedTest(Base):
    def test_folders_of_other_files_are_counted_and_tallied_by_extension(self):
        self.make("notes-only", "notes.txt")
        self.make("notes-only", "IMG_0005.xmp")
        self.make("catalog-only", "shoot-a.lrcat")
        self.scan()
        plan = self.plan()
        self.assertEqual(plan.roots[0].rows, [])
        self.assertEqual(plan.not_selected, 2)
        self.assertEqual(plan.not_selected_exts, (("lrcat", 1), ("txt", 1), ("xmp", 1)))

    def test_the_tally_is_by_count_descending_then_extension(self):
        self.make("one", "a.txt")
        self.make("one", "b.txt")
        self.make("one", "c.xmp")
        self.make("two", "d.lrcat")
        self.make("two", "noext")
        self.scan()
        self.assertEqual(self.plan().not_selected_exts,
                         (("txt", 2), ("", 1), ("lrcat", 1), ("xmp", 1)))


class CatalogFileTest(Base):
    def test_each_catalog_suffix_is_no_row_and_is_counted_for_every_root(self):
        self.make("shoot-a", "IMG_0001.ARW")
        for name in ("a.lrcat", "a.lrcat-wal", "a.lrcat-shm", "a.lrcat.lock", "a.LRCAT-journal"):
            self.make("shoot-a", name)
        self.scan()
        plan = self.plan(archive_roots=[self.arc, os.path.join(self.tmp, "arc-b")])
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.ARW"])
        self.assertEqual([root.catalog_files for root in plan.roots], [5, 5])

    def test_a_name_that_only_contains_or_begins_with_a_suffix_is_a_row(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-a", "shoot-a.lrcat.jpg")
        self.make("shoot-a", "my-lrcat-notes.txt")
        self.make("shoot-a", ".lrcat-first.txt")
        self.scan()
        plan = self.plan()
        self.assertEqual(len(plan.roots[0].rows), 4)
        self.assertEqual(plan.roots[0].catalog_files, 0)


class ContainerTest(Base):
    def tree(self):
        self.make("x Previews.lrdata", "IMG_0001.ARW")
        self.make("Mobile Downloads.lrdata", "IMG_0002.ARW")
        self.make("card.lrdata", "IMG_0003.ARW")
        self.make("y Previews.lrdata", "IMG_0004.ARW")
        self.make("shoot-a", "IMG_0005.ARW")

    def test_with_a_catalogs_scan_only_a_cache_no_catalog_records_a_file_in_is_skipped(self):
        self.tree()
        self.catalogs_scan(os.path.join(self.photos, "y Previews.lrdata", "IMG_0004.ARW"))
        self.scan()
        self.assertEqual(
            sorted(self.sources(self.plan())),
            ["Mobile Downloads.lrdata/IMG_0002.ARW", "card.lrdata/IMG_0003.ARW",
             "shoot-a/IMG_0005.ARW", "y Previews.lrdata/IMG_0004.ARW"])

    def test_with_a_catalogs_scan_that_records_nothing_a_cache_is_skipped(self):
        self.tree()
        self.catalogs_scan()
        self.scan()
        self.assertNotIn("x Previews.lrdata/IMG_0001.ARW", self.sources(self.plan()))

    def test_with_no_catalogs_scan_every_lrdata_folder_is_walked(self):
        self.tree()
        self.scan()
        self.assertEqual(len(self.sources(self.plan())), 5)


class PruneTest(Base):
    def test_exclude_own_dir_and_an_archive_root_are_not_entered_or_left_out(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-b", "IMG_0002.ARW")
        for kind in ("excluded", "own", "archive"):
            self.make("shoot-a", kind, "IMG_0009.ARW")
        self.scan()
        inside = {kind: os.path.join(self.photos, "shoot-a", kind)
                  for kind in ("excluded", "own", "archive")}
        for kind, folder in inside.items():
            kw = dict(exclude=[]) if kind != "excluded" else dict(exclude=[folder])
            if kind == "own":
                kw["own_dir"] = folder
            with self.subTest(kind=kind):
                plan = self.plan(
                    archive_roots=[folder if kind == "archive" else self.arc], **kw)
                found = self.sources(plan)
                self.assertEqual(
                    sorted(found),
                    sorted(["shoot-a/IMG_0001.ARW", "shoot-b/IMG_0002.ARW"]
                           + [f"shoot-a/{k}/IMG_0009.ARW" for k in inside if k != kind]))
                self.assertEqual(plan.roots[0].left_out, 0)


class LinkTest(Base):
    def test_each_link_is_counted_and_gives_no_row_or_folder(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-b", "IMG_0002.ARW")
        target = os.path.join(self.tmp, "target")
        os.makedirs(target)
        other = os.path.join(target, "IMG_0007.ARW")
        with open(other, "wb") as handle:
            handle.write(b"x")
        made = 0
        for link, directory in (("sym", True), ("filesym.ARW", False)):
            try:
                os.symlink(target if directory else other,
                           os.path.join(self.photos, "shoot-a", link), target_is_directory=directory)
                made += 1
            except (OSError, NotImplementedError):
                pass
        junction = os.path.join(self.photos, "shoot-a", "junc")
        if subprocess.run(["cmd", "/c", "mklink", "/J", junction, target],
                          capture_output=True).returncode == 0:
            made += 1
        if not made:
            self.skipTest("cannot make a symlink or a junction")
        self.scan()
        plan = self.plan()
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.ARW", "shoot-b/IMG_0002.ARW"])
        self.assertEqual((plan.links, plan.roots[0].folders, plan.roots[0].left_out), (made, 2, 0))


class UnreadTest(Base):
    def test_a_folder_scandir_refuses_is_named_once_and_the_others_still_plan(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-a", "locked", "IMG_0002.ARW")
        self.make("shoot-b", "IMG_0003.ARW")
        self.make("shoot-c", "IMG_0004.ARW")
        self.scan()

        def scandir(path):
            if path.endswith(("locked", "shoot-b")):
                raise OSError("denied")
            return os.scandir(path)

        plan = self.plan(scandir=scandir)
        self.assertEqual(sorted(plan.unread_folders),
                         sorted([os.path.join(self.photos, "shoot-a", "locked"),
                                 os.path.join(self.photos, "shoot-b")]))
        self.assertEqual(plan.unlisted, 2)
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.ARW", "shoot-c/IMG_0004.ARW"])

    def test_a_selected_folder_whose_second_listing_refuses_is_named_too(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-b", "IMG_0002.ARW")
        self.scan()
        calls = []

        def scandir(path):
            calls.append(path)
            if path.endswith("shoot-a") and calls.count(path) > 1:
                raise OSError("denied")
            return os.scandir(path)

        plan = self.plan(scandir=scandir)
        self.assertEqual(plan.unread_folders, (os.path.join(self.photos, "shoot-a"),))
        self.assertEqual(plan.unlisted, 1)
        self.assertEqual(self.sources(plan), ["shoot-b/IMG_0002.ARW"])


class PlaceholderAndDestTest(Base):
    def test_placeholder_attributes_are_skipped_unopened_and_others_are_copied(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.scan()
        for attrs, action in [(0x1000, "skip_placeholder"), (0x40000, "skip_placeholder"),
                              (0x400000, "skip_placeholder"), (0x20, "copy")]:
            with self.subTest(attrs=attrs), mock.patch.object(
                    builtins, "open", side_effect=AssertionError("opened")):
                row = self.plan(attrs_of=lambda path, st, a=attrs: a).roots[0].rows[0]
            self.assertEqual(row.action, action)
            self.assertEqual(row.reason, "placeholder" if action != "copy" else "")

    def test_dest_is_the_archive_root_plus_the_path_below_photo_root(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.scan()
        second = os.path.join(self.tmp, "arc-b")
        plan = self.plan(archive_roots=[self.arc, second])
        for root, root_plan in zip([self.arc, second], plan.roots):
            self.assertEqual(root_plan.archive_root, root)
            self.assertEqual(root_plan.rows[0].dest, os.path.join(root, "shoot-a", "IMG_0001.ARW"))

    def test_a_path_over_260_characters_is_planned(self):
        deep = self.make(*["d" * 40] * 6, "IMG_0001.ARW")
        self.assertGreater(len(deep), 260)
        self.scan()
        self.assertEqual([r.source for r in self.plan().roots[0].rows], [deep])


class SummaryTest(Base):
    def test_figures_are_exact_per_root_and_sources_equal_across_roots(self):
        self.make("shoot-a", "IMG_0001.ARW", data=b"12345")
        self.make("shoot-a", "IMG_0002.ARW", data=b"123")
        self.make("shoot-a", "left", "n.txt")
        self.scan()
        second = os.path.join(self.tmp, "arc-b")
        free = {self.arc: 11, second: 22}
        plan = self.plan(
            archive_roots=[self.arc, second], free_bytes=free.__getitem__,
            attrs_of=lambda path, st: 0x1000 if path.endswith("0002.ARW") else 0x20)
        first, other = plan.roots
        self.assertEqual((first.folders, first.files, first.bytes, first.placeholders,
                          first.left_out, first.free), (1, 2, 5, 1, 1, 11))
        self.assertEqual(other.free, 22)
        self.assertEqual([r.source for r in first.rows], [r.source for r in other.rows])

    def test_every_root_never_scanned_raises(self):
        with self.assertRaises(ManifestError):
            self.plan()

    def test_an_unread_root_is_listed_while_a_complete_one_still_plans(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.scan()
        other = os.path.join(self.tmp, "lib2")
        plan = self.plan(library_roots=[self.lib, other])
        self.assertEqual(plan.unread_roots, [other])
        self.assertEqual(self.sources(plan), ["shoot-a/IMG_0001.ARW"])

    def test_an_unread_root_with_an_older_complete_scan_gives_no_row(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.scan()
        newer = store.begin_scan(self.conn, self.lib, "files")
        store.finish_scan(self.conn, newer, "partial")
        with self.assertRaises(ManifestError):
            self.plan()


class NoSideEffectTest(Base):
    def listing(self):
        found = []
        for folder, _, names in os.walk(self.lib):
            for name in names:
                st = os.stat(os.path.join(folder, name))
                found.append((os.path.join(folder, name), st.st_size, st.st_mtime_ns))
        return sorted(found)

    def test_planning_changes_nothing_and_is_repeatable(self):
        self.make("shoot-a", "IMG_0001.ARW")
        self.make("shoot-b", "IMG_0002.ARW")
        self.scan()
        before = self.listing()
        first = self.plan()
        second = self.plan()
        self.assertEqual(self.listing(), before)
        self.assertFalse(os.path.exists(self.arc))
        self.assertEqual(first, second)
        keys = [paths.norm(r.source) for r in first.roots[0].rows]
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
