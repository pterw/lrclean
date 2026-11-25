import os
import tempfile
import unittest

from lrclean import backups, hashing, paths, store

BIG = 300_000


def blob(seed: int, size: int = BIG, middle: int = 0) -> bytes:
    data = bytearray([seed]) * size
    data[size // 2] = middle
    return bytes(data)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = os.path.join(self.tmp.name, "data")
        self.conn = store.connect(os.path.join(self.tmp.name, "work.db"))
        self.addCleanup(self.conn.close)
        self.scans = {}

    def root(self, name, status="complete"):
        full = os.path.join(self.dir, name)
        os.makedirs(full, exist_ok=True)
        if status is not None:
            scan_id = store.begin_scan(self.conn, full, "files")
            store.finish_scan(self.conn, scan_id, status)
            self.scans[name] = scan_id
        return full

    def put(self, root, rel, data, placeholder=0, link_to=None):
        full = os.path.join(self.dir, root, *rel.split("/"))
        os.makedirs(paths.fs_path(os.path.dirname(full)), exist_ok=True)
        if link_to:
            os.link(link_to, full)
        else:
            with open(paths.fs_path(full), "wb") as handle:
                handle.write(data)
        st = os.stat(paths.fs_path(full))
        store.insert_row(
            self.conn, "file", scan_id=self.scans[root], volume=paths.volume(full),
            path_norm=paths.norm(full), path_orig=full, ext=full.rsplit(".", 1)[-1].lower(),
            size=st.st_size, mtime_ns=st.st_mtime_ns, attrs=0, placeholder=placeholder,
            container="", file_index=paths.signed64(st.st_ino), nlink=1, checked_at="t")
        self.conn.commit()
        return full

    def hash_all(self, libs, backs):
        hashing.hash_files(self.conn, library_roots=libs, backup_roots=backs)

    def rows(self, libs, backs, photo_root=None, depth=1):
        out = backups.external_copy_rows(
            self.conn, photo_root=photo_root or libs[0], group_depth=depth,
            library_roots=libs, backup_roots=backs)
        return {(r["kind"], r["folder"]): r for r in out}


class SectionFive(Fixture):
    def stale(self, path):
        self.conn.execute("UPDATE file SET mtime_ns = mtime_ns + 1 WHERE path_orig = ?", (path,))
        self.conn.commit()

    def test_sampled_match_needs_equal_size_and_sample_and_names_backup_root(self):
        lib, bk, bk2 = self.root("lib"), self.root("bk"), self.root("bk2")
        self.put("lib", "2020/shoot-a/IMG_0001.ARW", blob(1))
        self.put("lib", "2020/shoot-a/IMG_0002.ARW", blob(2))
        self.put("lib", "2021/shoot-b/IMG_0003.ARW", blob(3))
        stale_lib = self.put("lib", "2022/IMG_0004.ARW", blob(4))
        self.put("lib", "2022/IMG_0005.ARW", blob(5))
        self.put("lib", "2022/IMG_0006.ARW", blob(6))
        self.put("bk", "x/IMG_0001.ARW", blob(1))
        self.put("bk", "x/IMG_0002.ARW", blob(9))
        stale_bk = self.put("bk", "x/IMG_0005.ARW", blob(5))
        self.put("bk", "x/IMG_0006.ARW", blob(6), placeholder=1)
        self.put("bk", "x/IMG_0004.ARW", blob(4))
        self.hash_all([lib], [bk, bk2])
        self.stale(stale_lib)
        self.stale(stale_bk)
        self.put("bk", "x/IMG_0003.ARW", blob(3))
        rows = self.rows([lib], [bk, bk2])
        y20, y21, y22 = rows[("year", "2020")], rows[("year", "2021")], rows[("year", "2022")]
        self.assertEqual((y20["raws"], y20["copied_sampled"], y20["copied_verified"],
                          y20["no_copy"]), (2, 1, 0, 1))
        self.assertEqual(y20["backup_roots"], f"{bk}=1;{bk2}=0")
        self.assertEqual((y21["no_copy"], y21["copied_sampled"]), (1, 0))
        self.assertEqual((y22["raws"], y22["copied_sampled"], y22["no_copy"]), (3, 0, 3))
        self.assertEqual(y20["bytes"], 2 * BIG)

    def test_group_depth_changes_groups_and_two_named_groups_exist(self):
        lib, bk = self.root("lib"), self.root("bk")
        other = self.root("lib2")
        self.put("lib", "2020/Shoot-A/IMG_0001.ARW", blob(1))
        self.put("lib", "2020/shoot-b/IMG_0002.ARW", blob(2))
        self.put("lib", "IMG_0005.ARW", blob(5))
        self.put("lib2", "IMG_0004.ARW", blob(4))
        self.hash_all([lib, other], [bk])
        one = self.rows([lib, other], [bk], photo_root=lib, depth=1)
        two = self.rows([lib, other], [bk], photo_root=lib, depth=2)
        self.assertEqual({k[1] for k in one}, {"2020", backups.TOP_OF_PHOTO_ROOT,
                                               backups.OUTSIDE})
        self.assertEqual(one[("year", "2020")]["raws"], 2)
        self.assertEqual({k[1] for k in two}, {"2020/Shoot-A", "2020/shoot-b",
                                               backups.TOP_OF_PHOTO_ROOT, backups.OUTSIDE})
        self.assertEqual(two[("year", backups.OUTSIDE)]["raws"], 1)
        self.assertEqual(two[("year", backups.TOP_OF_PHOTO_ROOT)]["bytes"], BIG)

    def test_unread_backup_roots_give_no_copy_and_are_named_rule_1(self):
        lib, bk = self.root("lib"), self.root("bk")
        part = self.root("bk-partial", "partial")
        off = self.root("bk-off", "not_connected")
        never = self.root("bk-never", None)
        self.put("lib", "2020/IMG_0001.ARW", blob(1))
        self.put("lib", "2020/IMG_0002.ARW", blob(2))
        self.put("bk", "2020/IMG_0001.ARW", blob(1))
        self.put("bk", "2020/IMG_0007.ARW", blob(7, 5000))
        self.put("bk-partial", "2020/IMG_0002.ARW", blob(2))
        self.put("bk-partial", "2020/IMG_0008.ARW", blob(8, 6000))
        roots = [bk, part, off, never]
        self.hash_all([lib], [bk, part])
        rows = self.rows([lib], roots)
        year = rows[("year", "2020")]
        self.assertEqual((year["copied_sampled"], year["no_copy"]), (1, 1))
        self.assertEqual(year["backup_roots"], f"{bk}=1")
        for unread in (part, off, never):
            self.assertIn(unread, year["how_decided"])
        only = [r for r in rows.values() if r["kind"] == "backup_only"]
        self.assertEqual([(r["folder"], r["raws"], r["bytes"]) for r in only],
                         [("2020", 1, 5000)])
        self.assertEqual(only[0]["backup_roots"], f"{bk}=1")


class Verify(Fixture):
    def setUp(self):
        super().setUp()
        self.lib, self.bk = self.root("lib"), self.root("bk")
        self.opened = []

    def opener(self, path):
        self.opened.append(os.path.basename(path))
        return hashing.open_binary(path)

    def verify(self, folder, **kw):
        return backups.verify(self.conn, os.path.join(self.lib, folder),
                              library_roots=[self.lib], backup_roots=[self.bk],
                              opener=kw.pop("opener", self.opener), **kw)

    def test_middle_byte_difference_is_mismatch_and_exact_copy_verified_rule_8(self):
        self.put("lib", "a/IMG_0001.ARW", blob(1))
        self.put("lib", "a/IMG_0002.ARW", blob(2))
        self.put("lib", "b/IMG_0003.ARW", blob(3))
        self.put("bk", "x/IMG_0001.ARW", blob(1))
        self.put("bk", "x/IMG_0002.ARW", blob(2, middle=7))
        self.put("bk", "x/IMG_0003.ARW", blob(3))
        self.put("lib", "c/IMG_0004.ARW", blob(4))
        unread = self.put("bk", "x/IMG_0004.ARW", blob(4))
        self.hash_all([self.lib], [self.bk])
        before = self.rows([self.lib], [self.bk], depth=1)
        self.assertEqual(before[("year", "a")]["copied_sampled"], 2)
        self.opened.clear()
        counts = self.verify("a")
        self.assertEqual((counts["verified"], counts["mismatch"], counts["no_copy"]), (1, 1, 0))
        self.assertNotIn("IMG_0003.ARW", self.opened)
        after = self.rows([self.lib], [self.bk], depth=1)
        a, b = after[("year", "a")], after[("year", "b")]
        self.assertEqual((a["copied_verified"], a["copied_sampled"], a["no_copy"]), (1, 0, 1))
        self.assertEqual((b["copied_verified"], b["copied_sampled"]), (0, 1))

        def deny_copy(path):
            if path == unread:
                raise OSError("denied")
            return hashing.open_binary(path)

        self.assertEqual(self.verify("c", opener=deny_copy)["unreadable"], 1)
        c = self.rows([self.lib], [self.bk], depth=1)[("year", "c")]
        self.assertEqual((c["copied_sampled"], c["copied_verified"], c["no_copy"]), (1, 0, 0))

    def test_same_file_is_never_its_own_copy_hard_link_and_root_on_both_sides(self):
        lib_file = self.put("lib", "a/IMG_0001.ARW", blob(1))
        self.put("bk", "x/IMG_0001.ARW", b"", link_to=lib_file)
        self.hash_all([self.lib], [self.bk])
        year = self.rows([self.lib], [self.bk])[("year", "a")]
        self.assertEqual((year["copied_sampled"], year["copied_verified"], year["no_copy"]),
                         (0, 0, 1))
        counts = self.verify("a")
        self.assertEqual((counts["verified"], counts["no_copy"]), (0, 1))
        year = self.rows([self.lib], [self.lib, self.bk])[("year", "a")]
        self.assertEqual((year["copied_sampled"], year["no_copy"]), (0, 1))
        both = backups.verify(self.conn, os.path.join(self.lib, "a"), library_roots=[self.lib],
                              backup_roots=[self.lib])
        self.assertEqual((both["verified"], both["no_copy"]), (0, 1))
        lib_file = self.put("lib", "b/IMG_0007.ARW", blob(7))
        self.put("bk", "x/IMG_0007.ARW", b"", link_to=lib_file)
        real = self.put("bk", "y/IMG_0007.ARW", blob(7))
        self.hash_all([self.lib], [self.bk])

        def deny_real(path):
            if path == real:
                raise OSError("denied")
            return hashing.open_binary(path)

        counts = self.verify("b", opener=deny_real)
        self.assertEqual((counts["verified"], counts["unreadable"]), (0, 1))

    def test_backup_file_changed_since_verify_no_longer_counts_as_copy(self):
        self.put("lib", "a/IMG_0001.ARW", blob(1))
        copy = self.put("bk", "x/IMG_0001.ARW", blob(1))
        self.hash_all([self.lib], [self.bk])
        self.assertEqual(self.verify("a")["verified"], 1)
        self.assertEqual(self.rows([self.lib], [self.bk])[("year", "a")]["copied_verified"], 1)
        with open(copy, "wb") as handle:
            handle.write(blob(2, size=BIG + 5))
        self.assertEqual(self.verify("a")["changed"], 1)
        year = self.rows([self.lib], [self.bk])[("year", "a")]
        self.assertEqual((year["copied_sampled"], year["copied_verified"], year["no_copy"]),
                         (0, 0, 1))

    def test_raw_without_sample_matched_copy_counts_no_copy(self):
        self.put("lib", "a/IMG_0001.ARW", blob(1))
        self.put("bk", "x/IMG_0001.ARW", blob(9))
        self.hash_all([self.lib], [self.bk])
        self.assertEqual(self.verify("a")["no_copy"], 1)

    def test_placeholder_changed_and_unreadable_are_counted_rules_4_7_9(self):
        self.put("lib", "a/IMG_0001.ARW", blob(1), placeholder=1)
        changed = self.put("lib", "a/IMG_0002.ARW", blob(2))
        self.put("lib", "a/IMG_0003.ARW", blob(3))
        long_dir = "d" * 60
        deep = self.put("lib", "/".join([long_dir] * 5) + "/IMG_0004.ARW", blob(4))
        self.assertGreater(len(deep), 260)
        for n in (2, 3, 4):
            self.put("bk", f"x/IMG_000{n}.ARW", blob(n))
        self.hash_all([self.lib], [self.bk])
        with open(changed, "wb") as handle:
            handle.write(blob(2, size=BIG + 5))

        def opener(path):
            if path.endswith("IMG_0003.ARW") and path.startswith(self.lib):
                raise OSError("denied")
            return hashing.open_binary(path)

        counts = self.verify("", opener=opener)
        self.assertEqual({k: counts[k] for k in ("skipped_placeholder", "changed",
                                                 "unreadable", "verified")},
                         {"skipped_placeholder": 1, "changed": 1, "unreadable": 1,
                          "verified": 1})


class BackupOnly(Fixture):
    def test_backup_raw_matching_no_library_raw_is_listed_with_top_folder_and_bytes(self):
        lib, bk = self.root("lib"), self.root("bk")
        self.put("lib", "2020/IMG_0001.ARW", blob(1))
        self.put("lib", "2020/IMG_0009.ARW", blob(6, 8000), placeholder=1)
        self.put("bk", "Old/IMG_0001.ARW", blob(1))
        self.put("bk", "Old/IMG_0002.ARW", blob(2, 5000))
        self.put("bk", "IMG_0003.ARW", blob(3, 7000))
        self.put("bk", "Unsure/IMG_0009.ARW", blob(6, 8000))
        self.hash_all([lib], [bk])
        only = {k[1]: r for k, r in self.rows([lib], [bk]).items() if k[0] == "backup_only"}
        self.assertEqual(only["Old"]["bytes"], 5000)
        self.assertEqual(only["Old"]["raws"], 1)
        self.assertIn("no library raw matches", only["Old"]["how_decided"])
        self.assertEqual(only[backups.TOP_OF_ROOT]["bytes"], 7000)
        self.assertEqual((only["Unsure"]["raws"], only["Unsure"]["no_copy"]), (0, 0))
        self.assertIn("1 more not compared", only["Unsure"]["how_decided"])


if __name__ == "__main__":
    unittest.main()
