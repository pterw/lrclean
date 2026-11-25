import hashlib
import io
import os
import tempfile
import unittest
import unittest.mock

from lrclean import hashing, paths, store


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.conn = store.connect(os.path.join(self.dir, "work.db"))
        self.addCleanup(self.conn.close)
        self.opened = []

    def opener(self, path):
        self.opened.append(os.path.basename(path))
        return hashing.open_binary(path)

    def write(self, rel, data):
        full = os.path.join(self.dir, "data", rel)
        os.makedirs(paths.fs_path(os.path.dirname(full)), exist_ok=True)
        with open(paths.fs_path(full), "wb") as handle:
            handle.write(data)
        return full

    def add_scan(self, root, status="complete"):
        scan_id = store.begin_scan(self.conn, root, "files")
        store.finish_scan(self.conn, scan_id, status)
        return scan_id

    def add_file(self, scan_id, full, placeholder=0, ext=None):
        st = os.stat(paths.fs_path(full))
        file_id = store.insert_row(
            self.conn, "file", scan_id=scan_id, volume=paths.volume(full),
            path_norm=paths.norm(full), path_orig=full,
            ext=ext or full.rsplit(".", 1)[-1].lower(), size=st.st_size,
            mtime_ns=st.st_mtime_ns, attrs=0, placeholder=placeholder, container="",
            file_index=paths.signed64(st.st_ino), nlink=1, checked_at="t")
        self.conn.commit()
        return self.row(file_id)

    def row(self, file_id):
        return self.conn.execute('SELECT * FROM "file" WHERE id = ?', (file_id,)).fetchone()

    def hash_row(self, row):
        return self.conn.execute('SELECT * FROM "hash" WHERE file_id = ?', (row["id"],)).fetchone()

    def root(self, name, files, status="complete"):
        """Make `files` (name -> bytes) under data/<name> and a scan of it; returns rows."""
        base = os.path.join(self.dir, "data", name)
        scan_id = self.add_scan(base, status)
        return base, [self.add_file(scan_id, self.write(f"{name}/{n}", d)) for n, d in files.items()]

    def run_all(self, library, backup=(), **kw):
        return hashing.hash_files(self.conn, library_roots=list(library),
                                  backup_roots=list(backup), opener=self.opener, **kw)


class HashFunctionsTest(Fixture):
    def sample(self, data, name="a.arw"):
        full = self.write(name, data)
        return hashing.sample_hash(full, len(data))

    def test_sample_ignores_middle_bytes_but_full_sees_them_rule_8(self):
        size = 300 * 1024
        a = b"a" * size
        b = a[:100000] + b"X" + a[100001:]
        self.assertEqual(self.sample(a, "a.arw"), self.sample(b, "b.arw"))
        self.assertNotEqual(hashing.full_hash(self.write("a.arw", a)),
                            hashing.full_hash(self.write("b.arw", b)))

    def test_sample_differs_on_first_bytes_rule_8(self):
        self.assertNotEqual(self.sample(b"x" + b"a" * 200000, "a.arw"),
                            self.sample(b"y" + b"a" * 200000, "b.arw"))
        self.assertNotEqual(self.sample(b"a" * 100, "c.arw"), self.sample(b"a" * 101, "d.arw"))

    def test_files_up_to_128kb_are_covered_whole_rule_8(self):
        a = bytes(range(256)) * 512
        b = a[:70000] + b"X" + a[70001:]
        self.assertEqual(len(a), 128 * 1024)
        self.assertNotEqual(self.sample(a, "a.arw"), self.sample(b, "b.arw"))

    def test_full_hash_is_sha256_of_the_bytes(self):
        data = os.urandom(2 * 1024 * 1024 + 17)
        self.assertEqual(hashing.full_hash(self.write("a.arw", data)), hashlib.sha256(data).hexdigest())

    def test_open_binary_is_read_only(self):
        with hashing.open_binary(self.write("a.arw", b"x")) as handle:
            with self.assertRaises(io.UnsupportedOperation):
                handle.write(b"y")

    def test_long_path_is_hashed_through_fs_path_rule_4(self):
        rel = "/".join(["d" * 100] * 3) + "/IMG_0001.ARW"
        full = self.write(rel, b"abc")
        self.assertGreater(len(full), paths.LONG_LIMIT)
        scan_id = self.add_scan(os.path.join(self.dir, "data"))
        row = self.add_file(scan_id, full)
        self.assertEqual(hashing.hash_one(self.conn, row, want_full=True), "hashed")
        self.assertEqual(self.hash_row(row)["full"], hashlib.sha256(b"abc").hexdigest())

    def test_open_binary_and_restat_use_fs_path_rule_4(self):
        rel = "/".join(["d" * 100] * 3) + "/IMG_0001.ARW"
        full = self.write(rel, b"abc")
        row = self.add_file(self.add_scan(os.path.join(self.dir, "data")), full)
        seen = []

        def stat(path):
            seen.append(path)
            return os.stat(path)

        with unittest.mock.patch("builtins.open", wraps=open) as spy:
            self.assertEqual(hashing.hash_one(self.conn, row, want_full=True, stat=stat), "hashed")
        self.assertEqual(seen, [paths.fs_path(full)])
        self.assertTrue(seen[0].startswith("\\\\?\\"))
        self.assertTrue(all(c.args[0] == paths.fs_path(full) for c in spy.call_args_list))


class HashOneTest(Fixture):
    def one(self, row, **kw):
        kw.setdefault("want_full", False)
        return hashing.hash_one(self.conn, row, opener=self.opener, **kw)

    def setUp(self):
        super().setUp()
        _, (self.row_a,) = self.root("r", {"IMG_0001.ARW": b"a" * 500})

    def test_hashed_then_reused_then_upgraded_to_full_rule_9(self):
        self.assertEqual(self.one(self.row_a), "hashed")
        self.assertIsNone(self.hash_row(self.row_a)["full"])
        self.assertEqual(self.one(self.row_a), "reused")
        self.assertEqual(self.opened, ["IMG_0001.ARW"])
        self.assertEqual(self.one(self.row_a, want_full=True), "hashed")
        row = self.hash_row(self.row_a)
        self.assertTrue(row["sample"] and row["full"])
        self.assertEqual(self.one(self.row_a, want_full=True), "reused")

    def test_placeholder_row_is_never_opened_rule_7(self):
        scan_id = self.row_a["scan_id"]
        row = self.add_file(scan_id, self.write("r/P.ARW", b"p"), placeholder=1)

        def boom(path):
            raise AssertionError("opened")

        self.assertEqual(hashing.hash_one(self.conn, row, want_full=True, opener=boom,
                                          stat=boom), "skipped_placeholder")
        self.assertIsNone(self.hash_row(row))

    def test_stat_placeholder_attribute_skips_without_opening_rule_7(self):
        def stat(path):
            real = os.stat(path)
            return type("S", (), {"st_file_attributes": 0x400000, "st_size": real.st_size,
                                  "st_mtime_ns": real.st_mtime_ns, "st_ino": real.st_ino})()

        self.assertEqual(self.one(self.row_a, stat=stat), "skipped_placeholder")
        self.assertEqual(self.opened, [])
        self.assertIsNone(self.hash_row(self.row_a))

    def test_replaced_file_with_same_size_and_mtime_is_changed_rule_9(self):
        self.one(self.row_a)
        path = self.row_a["path_orig"]
        other = path + ".new"
        with open(other, "wb") as handle:
            handle.write(b"b" * 500)
        os.utime(other, ns=(self.row_a["mtime_ns"], self.row_a["mtime_ns"]))
        os.replace(other, path)
        self.assertEqual(os.stat(path).st_mtime_ns, self.row_a["mtime_ns"])
        self.assertIsNotNone(self.hash_row(self.row_a))
        self.assertEqual(self.one(self.row_a, want_full=True), "changed")
        self.assertIsNone(self.hash_row(self.row_a))

    def test_hash_row_stale_in_any_column_is_not_trusted_rule_9(self):
        for column in ("size", "mtime_ns", "file_index"):
            with self.subTest(column=column):
                self.one(self.row_a)
                self.conn.execute(f'UPDATE "hash" SET {column} = {column} + 1')
                self.assertEqual(self.one(self.row_a), "hashed")
                self.assertEqual(self.hash_row(self.row_a)[column], self.row_a[column])

    def test_file_row_differing_in_any_column_from_stat_is_changed_rule_9(self):
        for column in ("size", "mtime_ns", "file_index"):
            with self.subTest(column=column):
                self.conn.execute(f'UPDATE "file" SET {column} = {column} + 1 WHERE id = ?',
                                  (self.row_a["id"],))
                row = self.row(self.row_a["id"])
                self.assertEqual(self.one(row), "changed")
                self.conn.execute(f'UPDATE "file" SET {column} = {column} - 1 WHERE id = ?',
                                  (self.row_a["id"],))
        self.assertIsNone(self.hash_row(self.row_a))

        # C-51: an inode above 2**63 is stored signed and must not read as changed.
        big = 2**63 + 5
        self.conn.execute('UPDATE "file" SET file_index = ? WHERE id = ?',
                          (paths.signed64(big), self.row_a["id"]))
        row = self.row(self.row_a["id"])

        def stat(path):
            real = os.stat(path)
            return type("S", (), {"st_file_attributes": 0, "st_size": real.st_size,
                                  "st_mtime_ns": real.st_mtime_ns, "st_ino": big})()

        self.assertEqual(self.one(row, stat=stat), "hashed")
        self.assertEqual(self.hash_row(row)["file_index"], paths.signed64(big))

    def test_oserror_is_unreadable_and_writes_nothing(self):
        def bad(path):
            raise PermissionError("denied")

        self.assertEqual(hashing.hash_one(self.conn, self.row_a, want_full=False, opener=bad),
                         "unreadable")
        self.assertIsNone(self.hash_row(self.row_a))

    def test_stat_failing_with_file_not_found_is_unreadable_and_nothing_opened(self):
        def gone(path):
            raise FileNotFoundError(path)

        self.assertEqual(self.one(self.row_a, stat=gone), "unreadable")
        self.assertEqual(self.opened, [])
        self.assertIsNone(self.hash_row(self.row_a))


class HashFilesTest(Fixture):
    def test_unique_files_sampled_collisions_full_same_size_alone_nothing_rule_8(self):
        size = 200 * 1024
        base = b"a" * size
        twin = base[:100000] + b"X" + base[100001:]
        lib, rows = self.root("lib", {
            "1.ARW": base, "2.ARW": twin, "3.ARW": b"z" + base[1:], "4.ARW": b"tiny"})
        counts = self.run_all([lib])
        fulls = [self.hash_row(r)["full"] for r in rows]
        self.assertEqual([f is not None for f in fulls], [True, True, False, False])
        self.assertNotEqual(fulls[0], fulls[1])
        self.assertTrue(all(self.hash_row(r)["sample"] for r in rows))
        self.assertEqual((counts["sampled"], counts["full"]), (4, 2))

    def test_non_photo_rows_are_not_taken(self):
        lib, rows = self.root("lib", {"a.xmp": b"x", "b.ARW": b"y"})
        self.assertEqual(self.run_all([lib])["sampled"], 1)
        self.assertIsNone(self.hash_row(rows[0]))

    def test_backup_sampled_only_on_library_size_match_and_never_full_rule_9(self):
        lib, _ = self.root("lib", {"1.ARW": b"a" * 100})
        bak, rows = self.root("bak", {"2.ARW": b"a" * 100, "3.ARW": b"b" * 101})
        counts = self.run_all([lib], [bak])
        self.assertEqual(self.opened.count("3.ARW"), 0)
        self.assertIsNone(self.hash_row(rows[1]))
        match = self.hash_row(rows[0])
        self.assertTrue(match["sample"])
        self.assertIsNone(match["full"])
        self.assertEqual((counts["sampled"], counts["full"]), (2, 0))

    def test_unlisted_and_incomplete_roots_contribute_nothing_rule_9(self):
        old = self.add_scan(os.path.join(self.dir, "data", "lib"))
        self.add_file(old, self.write("lib/1.ARW", b"a"))
        lib, _ = self.root("lib", {"1.ARW": b"a"}, status="partial")
        self.root("other", {"2.ARW": b"b"})
        self.assertEqual(self.run_all([lib]), dict.fromkeys(
            ("sampled", "full", "skipped_placeholder", "changed", "unreadable", "reused"), 0))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM "hash"').fetchone()[0], 0)
        self.assertNotIn("2.ARW", self.opened)

    def test_placeholder_rows_are_counted_and_never_opened_rule_7(self):
        lib, _ = self.root("lib", {"1.ARW": b"a"})
        scan_id = store.latest_scans(self.conn, "files")[0]["id"]
        self.add_file(scan_id, self.write("lib/P.ARW", b"p"), placeholder=1)
        counts = self.run_all([lib])
        self.assertEqual((counts["sampled"], counts["skipped_placeholder"]), (1, 1))
        self.assertNotIn("P.ARW", self.opened)

    def test_second_run_reuses_everything_rule_9(self):
        lib, _ = self.root("lib", {"1.ARW": b"a" * 50, "2.ARW": b"b" * 60})
        self.run_all([lib])
        self.opened.clear()
        counts = self.run_all([lib])
        self.assertEqual(self.opened, [])
        self.assertEqual((counts["reused"], counts["sampled"]), (2, 0))

    def test_interrupted_run_resumes_with_only_missing_files_rule_9(self):
        lib, rows = self.root("lib", {"1.ARW": b"a", "2.ARW": b"bb", "3.ARW": b"ccc", "4.ARW": b"dddd"})

        def stopper(path):
            if path.endswith("3.ARW"):
                raise KeyboardInterrupt
            return self.opener(path)

        with self.assertRaises(KeyboardInterrupt):
            hashing.hash_files(self.conn, library_roots=[lib], backup_roots=[], opener=stopper)
        self.assertEqual([self.hash_row(r) is not None for r in rows], [True, True, False, False])
        self.opened.clear()
        counts = self.run_all([lib])
        self.assertEqual(sorted(self.opened), ["3.ARW", "4.ARW"])
        self.assertEqual((counts["reused"], counts["sampled"]), (2, 2))

    def test_rerun_after_sample_pass_still_full_hashes_collisions_and_counts_once_rule_9(self):
        data = b"a" * 300000
        lib, rows = self.root("lib", {"1.ARW": data, "2.ARW": data, "3.ARW": b"b" * 10})
        self.run_all([lib])
        self.conn.execute('UPDATE "hash" SET "full" = NULL')
        self.conn.commit()
        self.opened.clear()
        counts = self.run_all([lib])
        self.assertEqual(sorted(self.opened), ["1.ARW", "2.ARW"])
        self.assertEqual((counts["sampled"], counts["full"], counts["reused"]), (0, 2, 3))
        self.assertTrue(all(self.hash_row(r)["full"] for r in rows[:2]))
        self.assertEqual(self.run_all([lib])["reused"], 3)

    def test_unreadable_file_is_counted_and_run_continues(self):
        lib, rows = self.root("lib", {"1.ARW": b"a", "2.ARW": b"bb", "3.ARW": b"ccc"})

        def flaky(path):
            if path.endswith("2.ARW"):
                raise PermissionError("denied")
            return self.opener(path)

        counts = hashing.hash_files(self.conn, library_roots=[lib], backup_roots=[], opener=flaky)
        self.assertEqual((counts["unreadable"], counts["sampled"]), (1, 2))
        self.assertIsNone(self.hash_row(rows[1]))


if __name__ == "__main__":
    unittest.main()
