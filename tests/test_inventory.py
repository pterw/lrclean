import builtins
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import synth
from lrclean import inventory, paths, store


def _catalogs_scan(conn, root, records=(), status="ok"):
    scan_id = store.begin_scan(conn, root, "catalogs")
    cat_id = store.insert_row(
        conn, "catalog", scan_id=scan_id, source_path=root + "/a.lrcat", snapshot_path="",
        role="library", status=status, size=0, mtime_ns=0, db_version="1", wal_bytes=0, has_lock=0)
    for path in records:
        store.insert_row(conn, "record", catalog_id=cat_id, path_norm=paths.norm(path),
                         path_orig=path, via="absolute", ext="arw", capture_time="", is_sidecar=0)
    store.finish_scan(conn, scan_id, "complete")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.root = os.path.join(self.tmp, "lib")
        os.makedirs(self.root)
        self.cat_root = os.path.join(self.tmp, "cat")
        self.conn = store.connect(os.path.join(self.tmp, "work", "w.db"))
        self.addCleanup(self.conn.close)

    def scan(self, records=(), roots=None, **kw):
        _catalogs_scan(self.conn, self.cat_root, records)
        args = dict(catalog_roots=[self.cat_root], exclude=[], own_dir=os.path.join(self.tmp, "work"))
        args.update(kw)
        return inventory.scan_files(self.conn, roots or [self.root], **args)

    def rows(self):
        return [dict(r) for r in self.conn.execute('SELECT * FROM "file" ORDER BY path_norm')]

    def status(self, scan_id):
        return self.conn.execute('SELECT status FROM scan WHERE id=?', (scan_id,)).fetchone()[0]

    def names(self):
        return [os.path.basename(r["path_orig"]) for r in self.rows()]


class ScanStatusTest(Base):
    def test_clean_walk_is_complete_and_keeps_only_photo_and_sidecar_types(self):
        synth.make_tree(self.root, {"s/IMG_0001.ARW": b"a", "s/IMG_0001.xmp": b"b", "s/notes.txt": b"c"})
        (sid,) = self.scan()
        self.assertEqual(self.status(sid), "complete")
        self.assertEqual(self.names(), ["IMG_0001.ARW", "IMG_0001.xmp"])
        self.assertEqual(self.rows()[0]["ext"], "arw")

    def test_missing_root_is_not_connected_with_no_rows(self):
        (sid,) = self.scan(roots=[os.path.join(self.tmp, "gone")])
        self.assertEqual(self.status(sid), "not_connected")
        self.assertEqual(self.rows(), [])

    def test_interrupt_keeps_rows_marks_partial_and_reraises(self):
        synth.make_tree(self.root, {f"s/IMG_000{i}.ARW": b"a" for i in range(4)})
        calls = []

        def attrs_of(path, st):
            calls.append(path)
            if len(calls) == 3:
                raise KeyboardInterrupt
            return 0

        before = sys.getrecursionlimit()
        with self.assertRaises(KeyboardInterrupt):
            self.scan(attrs_of=attrs_of)
        self.assertEqual(sys.getrecursionlimit(), before)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.status(self.conn.execute("SELECT MAX(id) FROM scan").fetchone()[0]), "partial")

    def test_unlistable_folder_gives_marker_row_and_partial(self):
        synth.make_tree(self.root, {"good/IMG_0001.ARW": b"a", "bad/IMG_0002.ARW": b"b"})

        def scandir(path):
            if os.path.basename(path) == "bad":
                raise PermissionError(path)
            return os.scandir(path)

        (sid,) = self.scan(scandir=scandir)
        self.assertEqual(self.status(sid), "partial")
        by_ext = {r["ext"]: r for r in self.rows()}
        self.assertEqual(by_ext["<unreadable>"]["size"], 0)
        self.assertTrue(by_ext["<unreadable>"]["path_orig"].endswith("bad"))
        self.assertIn("arw", by_ext)

    def test_stat_failure_gives_marker_row_and_partial(self):
        synth.make_tree(self.root, {"s/IMG_0001.ARW": b"a"})
        real = os.stat

        def failing(path, *a, **k):
            if str(path).endswith("IMG_0001.ARW"):
                raise PermissionError(path)
            return real(path, *a, **k)

        orig = inventory.os.stat
        inventory.os.stat = failing
        self.addCleanup(setattr, inventory.os, "stat", orig)
        (sid,) = self.scan()
        self.assertEqual(self.status(sid), "partial")
        self.assertEqual([r["ext"] for r in self.rows()], ["<unreadable>"])

    def test_nested_second_root_raises_and_stores_nothing(self):
        synth.make_tree(self.root, {"s/IMG_0001.ARW": b"a"})
        _catalogs_scan(self.conn, self.cat_root)
        before = self.conn.execute('SELECT COUNT(*) FROM scan').fetchone()[0]
        with self.assertRaises(ValueError):
            inventory.scan_files(
                self.conn, [self.root, os.path.join(self.root, "S")], catalog_roots=[self.cat_root],
                exclude=[], own_dir=os.path.join(self.tmp, "work"))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM scan').fetchone()[0], before)
        with self.subTest("same root twice"), self.assertRaises(ValueError):
            inventory.scan_files(
                self.conn, [self.root, self.root], catalog_roots=[self.cat_root],
                exclude=[], own_dir=os.path.join(self.tmp, "work"))
        with self.subTest("sibling sharing a name prefix"):
            os.makedirs(self.root + "2")
            inventory.scan_files(
                self.conn, [self.root, self.root + "2"], catalog_roots=[self.cat_root],
                exclude=[], own_dir=os.path.join(self.tmp, "work"))


class OrderTest(Base):
    def test_no_catalogs_scan_raises_and_stores_no_scan(self):
        with self.assertRaises(inventory.NoCatalogScan) as ctx:
            inventory.scan_files(self.conn, [self.root], catalog_roots=[self.cat_root],
                                 exclude=[], own_dir="x")
        self.assertIn(self.cat_root, str(ctx.exception))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM scan').fetchone()[0], 0)

    def test_one_of_two_catalog_roots_scanned_raises(self):
        _catalogs_scan(self.conn, self.cat_root)
        before = self.conn.execute('SELECT COUNT(*) FROM scan').fetchone()[0]
        with self.assertRaises(inventory.NoCatalogScan):
            inventory.scan_files(self.conn, [self.root], catalog_roots=[self.cat_root, self.tmp + "/other"],
                                 exclude=[], own_dir="x")
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM scan').fetchone()[0], before)


class FileFactsTest(Base):
    def test_default_attrs_and_volume_come_from_the_file_and_path(self):
        synth.make_tree(self.root, {"s/IMG_0001.ARW": b"abc"})
        target = os.path.join(self.root, "s", "IMG_0001.ARW")
        self.scan()
        (row,) = self.rows()
        st = os.stat(target)
        self.assertEqual(row["attrs"], st.st_file_attributes)
        self.assertEqual(row["volume"], paths.volume(target))
        self.assertEqual((row["size"], row["mtime_ns"]), (st.st_size, st.st_mtime_ns))
        self.assertEqual(row["file_index"], paths.signed64(st.st_ino))

    def test_inode_of_2_63_or_more_is_stored_as_its_signed_value(self):
        synth.make_tree(self.root, {"s/IMG_0001.ARW": b"abc"})
        real = os.stat

        class BigInode:
            def __init__(self, st):
                self.st = st
                self.st_ino = 2**63 + 5

            def __getattr__(self, name):
                return getattr(self.st, name)

        orig = inventory.os.stat
        inventory.os.stat = lambda path, *a, **k: (
            BigInode(real(path, *a, **k)) if str(path).endswith("IMG_0001.ARW") else real(path, *a, **k))
        self.addCleanup(setattr, inventory.os, "stat", orig)
        self.scan()
        (row,) = self.rows()
        self.assertEqual(row["file_index"], 2**63 + 5 - 2**64)

    def test_hard_link_gives_same_file_index_and_nlink_two(self):
        synth.make_tree(self.root, {"s/IMG_0001.ARW": b"abc"})
        os.link(os.path.join(self.root, "s", "IMG_0001.ARW"), os.path.join(self.root, "s", "IMG_0002.ARW"))
        self.scan()
        a, b = self.rows()
        self.assertEqual((a["volume"], a["file_index"]), (b["volume"], b["file_index"]))
        self.assertNotEqual(a["file_index"], 0)
        self.assertEqual((a["nlink"], b["nlink"]), (2, 2))

    def test_long_path_is_inventoried_with_unprefixed_path_orig(self):
        folder = self.root
        while len(os.path.join(folder, "IMG_0001.ARW")) < 261 - 40:
            folder = os.path.join(folder, "d" * 30)
        pad = 261 - len(os.path.join(folder, "IMG_0001.ARW"))
        folder = os.path.join(folder, "p" * (pad - 1))
        full = os.path.join(folder, "IMG_0001.ARW")
        self.assertEqual(len(full), 261)
        os.makedirs(paths.fs_path(folder))
        with open(paths.fs_path(full), "wb") as handle:
            handle.write(b"x")
        asked = []

        def scandir(path):
            asked.append(path)
            return os.scandir(path)

        self.scan(scandir=scandir)
        self.assertTrue(any(p.startswith("\\\\?\\") for p in asked))
        self.assertTrue(all(len(p) <= 260 or p.startswith("\\\\?\\") for p in asked))
        (row,) = self.rows()
        self.assertEqual(row["path_orig"], full)
        self.assertFalse(row["path_orig"].startswith("\\\\?\\"))

    def test_placeholder_attributes_are_recorded_never_opened(self):
        synth.make_tree(self.root, {"s/IMG_0001.ARW": b"a"})
        real_open = builtins.open
        root = os.path.normcase(paths.fs_path(self.root))

        def guarded_open(file, *a, **k):
            if (isinstance(file, (str, bytes, os.PathLike))
                    and os.path.normcase(paths.fs_path(os.fsdecode(file))).startswith(root)):
                raise AssertionError(f"the walk opened {file}")
            return real_open(file, *a, **k)

        for attrs, flag in ((0x1000, 1), (0x40000, 1), (0x400000, 1), (0x20, 0)):
            with self.subTest(attrs=hex(attrs)):
                self.conn.execute('DELETE FROM "file"')
                builtins.open = guarded_open
                try:
                    self.scan(attrs_of=lambda p, st, a=attrs: a)
                finally:
                    builtins.open = real_open
                (row,) = self.rows()
                self.assertEqual((row["attrs"], row["placeholder"]), (attrs, flag))


class ContainerTest(Base):
    def kinds(self):
        return {os.path.basename(r["path_orig"]): r["container"] for r in self.rows()}

    def test_mobile_downloads_is_storage(self):
        synth.make_tree(self.root, {"Mobile Downloads.lrdata/IMG_0001.ARW": b"a"})
        self.scan()
        self.assertEqual(self.kinds()["IMG_0001.ARW"].split(":")[0], "storage")

    def test_previews_without_record_is_one_cache_row_with_summed_size(self):
        synth.make_tree(self.root, {"Shoot Previews.lrdata/a/x.jpg": b"1234", "Shoot Previews.lrdata/y.bin": b"56"})
        self.scan()
        (row,) = self.rows()
        self.assertEqual((row["ext"], row["size"]), ("lrdata", 6))
        self.assertTrue(row["container"].startswith("cache:"))

    def test_previews_with_record_under_it_is_storage_with_photos(self):
        folder = os.path.join(self.root, "Shoot Previews.lrdata")
        synth.make_tree(self.root, {"Shoot Previews.lrdata/IMG_0001.ARW": b"a"})
        self.scan(records=[os.path.join(folder, "IMG_0001.ARW")])
        self.assertEqual(self.kinds()["IMG_0001.ARW"].split(":")[0], "storage")

    def test_record_of_unusable_catalog_does_not_make_storage(self):
        folder = os.path.join(self.root, "Shoot Previews.lrdata")
        synth.make_tree(self.root, {"Shoot Previews.lrdata/IMG_0001.ARW": b"a"})
        _catalogs_scan(self.conn, self.cat_root, [os.path.join(folder, "IMG_0001.ARW")], status="unreadable")
        inventory.scan_files(self.conn, [self.root], catalog_roots=[self.cat_root], exclude=[], own_dir="x")
        (row,) = self.rows()
        self.assertTrue(row["container"].startswith("cache:"))

    def test_partial_catalog_record_makes_storage(self):
        folder = os.path.join(self.root, "Shoot Previews.lrdata")
        synth.make_tree(self.root, {"Shoot Previews.lrdata/IMG_0001.ARW": b"a"})
        _catalogs_scan(self.conn, self.cat_root, [os.path.join(folder, "IMG_0001.ARW")], status="partial")
        inventory.scan_files(self.conn, [self.root], catalog_roots=[self.cat_root], exclude=[], own_dir="x")
        self.assertEqual(self.kinds()["IMG_0001.ARW"].split(":")[0], "storage")

    def test_record_beside_the_folder_does_not_make_storage(self):
        synth.make_tree(self.root, {"Shoot Previews.lrdata/a.jpg": b"1"})
        siblings = [os.path.join(self.root, "Shoot Previews.lrdata.bak", "Other.lrdata", "x"),
                    os.path.join(self.root, "shoot previews.lrdata0", "Other.lrdata", "y")]
        self.scan(records=siblings)
        (row,) = self.rows()
        self.assertTrue(row["container"].startswith("cache:"))

    def test_storage_is_found_when_records_arrive_unsorted(self):
        synth.make_tree(self.root, {"Shoot Previews.lrdata/IMG_0001.ARW": b"a"})
        self.scan(records=[os.path.join(self.root, "z.lrdata", "a"),
                           os.path.join(self.root, "Shoot Previews.lrdata", "IMG_0001.ARW"),
                           os.path.join(self.root, "a.lrdata", "b")])
        self.assertEqual({r["container"].split(":")[0] for r in self.rows()}, {"storage"})

    def test_mobile_downloads_name_is_compared_case_folded(self):
        synth.make_tree(self.root, {"MOBILE DOWNLOADS.LRDATA/IMG_0001.ARW": b"a"})
        self.scan()
        self.assertEqual(self.kinds()["IMG_0001.ARW"].split(":")[0], "storage")

    def test_unknown_container_is_sized_and_photos_inventoried(self):
        synth.make_tree(self.root, {"Other.lrdata/IMG_0001.ARW": b"abc", "Other.lrdata/z.bin": b"12"})
        self.scan()
        kinds = self.kinds()
        self.assertEqual(kinds["IMG_0001.ARW"].split(":")[0], "unknown")
        folder = next(r for r in self.rows() if r["ext"] == "lrdata")
        self.assertEqual(folder["size"], 5)

    def test_photo_in_nested_container_carries_the_innermost(self):
        synth.make_tree(self.root, {"Other.lrdata/Mobile Downloads.lrdata/IMG_0001.ARW": b"a"})
        self.scan()
        value = self.kinds()["IMG_0001.ARW"]
        self.assertTrue(value.startswith("storage:") and value.endswith("mobile downloads.lrdata"))


class DeepTreeTest(Base):
    def test_700_levels_deep_is_walked_and_the_recursion_limit_is_restored(self):
        folder = self.root
        for _ in range(700):
            folder = os.path.join(folder, "d")
        os.makedirs(paths.fs_path(folder))
        self.addCleanup(shutil.rmtree, paths.fs_path(self.root), True)
        with open(paths.fs_path(os.path.join(folder, "IMG_0001.ARW")), "wb") as handle:
            handle.write(b"x")
        before = sys.getrecursionlimit()
        (sid,) = self.scan()
        self.assertEqual(self.status(sid), "complete")
        self.assertEqual(self.names(), ["IMG_0001.ARW"])
        self.assertEqual(sys.getrecursionlimit(), before)


class PruneAndLinkTest(Base):
    def test_own_dir_and_excludes_are_never_listed_or_stored(self):
        synth.make_tree(self.root, {
            "work/IMG_0001.ARW": b"a", "skip/IMG_0002.ARW": b"b", "skip2/IMG_0003.ARW": b"c",
            "keep/IMG_0004.ARW": b"d", "keep/sub/skip/IMG_0005.ARW": b"e"})
        listed = []

        def scandir(path):
            listed.append(paths.norm(path))
            return os.scandir(path)

        own = (self.root + "\\WORK").replace("/", "\\")
        excl = os.path.join(self.root, "SKIP").replace("\\", "/")
        self.scan(own_dir=own, exclude=[excl], scandir=scandir)
        self.assertNotIn(paths.norm(os.path.join(self.root, "work")), listed)
        self.assertNotIn(paths.norm(os.path.join(self.root, "skip")), listed)
        self.assertIn(paths.norm(os.path.join(self.root, "skip2")), listed)
        self.assertEqual(sorted(self.names()), ["IMG_0003.ARW", "IMG_0004.ARW", "IMG_0005.ARW"])

    def test_links_are_not_entered_and_tree_is_unchanged(self):
        synth.make_tree(self.root, {"real/IMG_0001.ARW": b"a"})
        target = os.path.join(self.root, "real")
        made = 0
        try:
            os.symlink(target, os.path.join(self.root, "sym"), target_is_directory=True)
            made += 1
        except OSError:
            pass
        junction = os.path.join(self.root, "junc")
        made_junction = subprocess.run(["cmd", "/c", "mklink", "/J", junction, target],
                                       capture_output=True, timeout=30).returncode == 0
        if not made and not made_junction:
            self.skipTest("cannot make a symlink or a junction")
        before = self.listing()
        self.scan()
        self.assertEqual(self.names(), ["IMG_0001.ARW"])
        self.assertEqual(self.listing(), before)

    def listing(self):
        out = []
        for base, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(base, d))
                       and not os.path.isjunction(os.path.join(base, d))]
            for name in files:
                st = os.stat(os.path.join(base, name))
                out.append((os.path.join(base, name), st.st_size, st.st_mtime_ns))
        return sorted(out)


if __name__ == "__main__":
    unittest.main()
