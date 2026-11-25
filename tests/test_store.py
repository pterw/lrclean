import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from lrclean import store

SCHEMA = {
    "scan": ["id", "root", "kind", "started", "finished", "status"],
    "catalog": ["id", "scan_id", "source_path", "snapshot_path", "role", "status",
                "size", "mtime_ns", "db_version", "wal_bytes", "has_lock"],
    "record": ["catalog_id", "path_norm", "path_orig", "via", "ext", "capture_time",
               "is_sidecar"],
    "catalog_root": ["catalog_id", "path_norm", "path_orig", "via"],
    "file": ["id", "scan_id", "volume", "path_norm", "path_orig", "ext", "size",
             "mtime_ns", "attrs", "placeholder", "container", "file_index", "nlink",
             "checked_at"],
    "hash": ["file_id", "file_index", "size", "mtime_ns", "sample", "full", "hashed_at"],
    "image": ["catalog_id", "path_norm", "via", "pick", "developed", "settings"],
}


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = os.path.join(tmp.name, "sub", "work.db")
        self.conn = store.connect(self.db)
        self.addCleanup(self.conn.close)

    def other(self):
        conn = store.connect(self.db)
        self.addCleanup(conn.close)
        return conn

    def count(self, table):
        return self.other().execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def test_connect_uses_wal_so_commits_do_not_wait_for_the_disk(self):
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_schema_is_exactly_the_frozen_tables_and_columns(self):
        tables = [r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        self.assertEqual(tables, sorted(SCHEMA))
        for table, columns in SCHEMA.items():
            found = [r["name"] for r in self.conn.execute(f'PRAGMA table_info("{table}")')]
            self.assertEqual(found, columns, table)
            self.assertEqual(list(store.TABLES[table]), columns)

    def test_image_pick_and_developed_are_integers_and_empty_stays_empty(self):
        types = {r["name"]: r["type"]
                 for r in self.conn.execute('PRAGMA table_info("image")')}
        self.assertEqual(types["pick"], "INTEGER")
        self.assertEqual(types["developed"], "INTEGER")
        for value in (None, -1, 0, 1):
            store.insert_row(self.conn, "image", catalog_id=1, path_norm="a",
                             via="", pick=value, developed=value)
        self.conn.commit()
        rows = self.conn.execute("SELECT pick, developed FROM image ORDER BY rowid")
        self.assertEqual([(r["pick"], r["developed"]) for r in rows],
                         [(None, None), (-1, -1), (0, 0), (1, 1)])
        with self.assertRaises(ValueError):
            store.insert_row(self.conn, "image", flag=1)

    def old_db(self, statements):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "old.db")
        raw = sqlite3.connect(path)
        for sql in statements:
            raw.execute(sql)
        raw.commit()
        raw.close()
        return path

    def columns(self, conn, table):
        return [(r["name"], r["type"]) for r in conn.execute(f'PRAGMA table_info("{table}")')]

    def test_connect_adds_settings_to_an_old_image_table_keeping_its_row(self):
        path = self.old_db([
            'CREATE TABLE image ("catalog_id" INTEGER, "path_norm" TEXT, "via" TEXT, '
            '"pick" INTEGER, "developed" INTEGER)',
            "INSERT INTO image VALUES (1, 'a', '', 0, 1)"])
        conn = store.connect(path)
        self.addCleanup(conn.close)
        self.assertEqual(self.columns(conn, "image")[-1], ("settings", "TEXT"))
        row = conn.execute("SELECT * FROM image").fetchall()
        self.assertEqual([tuple(r) for r in row], [(1, "a", "", 0, 1, None)])
        again = store.connect(path)
        self.addCleanup(again.close)
        self.assertEqual(self.columns(again, "image"), self.columns(conn, "image"))
        self.assertEqual([tuple(r) for r in again.execute("SELECT * FROM image")],
                         [(1, "a", "", 0, 1, None)])

    def test_connect_adds_every_missing_column_to_any_table_not_only_settings(self):
        head = ('CREATE TABLE catalog ("id" INTEGER PRIMARY KEY, "scan_id" INTEGER, '
                '"source_path" TEXT, "snapshot_path" TEXT, "role" TEXT, "status" TEXT, '
                '"size" INTEGER, "mtime_ns" INTEGER, ')
        cases = {
            "one column": (head + '"db_version" TEXT, "wal_bytes" INTEGER)',
                           {"has_lock": "INTEGER"}),
            "two columns": (head + '"has_lock" INTEGER)',
                            {"db_version": "TEXT", "wal_bytes": "INTEGER"}),
        }
        for name, (create, missing) in cases.items():
            with self.subTest(name):
                path = self.old_db([create, "INSERT INTO catalog (id, scan_id) VALUES (7, 1)"])
                conn = store.connect(path)
                self.addCleanup(conn.close)
                got = dict(self.columns(conn, "catalog"))
                self.assertEqual(sorted(got), sorted(store.TABLES["catalog"]))
                for col, kind in missing.items():
                    self.assertEqual(got[col], kind)
                cols = ", ".join(missing)
                self.assertEqual(
                    tuple(conn.execute(f"SELECT id, {cols} FROM catalog").fetchone()),
                    (7,) + (None,) * len(missing))
                again = store.connect(path)
                self.addCleanup(again.close)
                self.assertEqual(self.columns(again, "catalog"), self.columns(conn, "catalog"))
                self.assertEqual(again.execute("SELECT COUNT(*) FROM catalog").fetchone()[0], 1)

    def test_insert_row_rejects_unknown_table_and_column(self):
        with self.assertRaises(ValueError):
            store.insert_row(self.conn, "nope", id=1)
        with self.assertRaises(ValueError):
            store.insert_row(self.conn, "scan", bogus=1)

    def test_latest_scans_newest_per_root_and_roots_grouped_by_norm(self):
        first = store.begin_scan(self.conn, "C:\\Shoot-A", "files")
        other = store.begin_scan(self.conn, "D:\\other", "files")
        newest = store.begin_scan(self.conn, "c:/shoot-a", "files")
        store.begin_scan(self.conn, "E:\\cats", "catalogs")
        ids = [r["id"] for r in store.latest_scans(self.conn, "files")]
        self.assertEqual(ids, [other, newest])
        self.assertNotIn(first, ids)

    def test_begin_and_finish_scan_are_visible_to_a_second_connection(self):
        scan_id = store.begin_scan(self.conn, "c:/a", "files", now=lambda: "t0")
        seen = self.other().execute("SELECT * FROM scan").fetchone()
        self.assertEqual((seen["status"], seen["started"]), ("running", "t0"))
        store.finish_scan(self.conn, scan_id, "complete", now=lambda: "t1")
        seen = self.other().execute("SELECT * FROM scan").fetchone()
        self.assertEqual((seen["status"], seen["finished"]), ("complete", "t1"))

    def test_insert_many_commits(self):
        store.insert_many(self.conn, "catalog_root", [
            {"catalog_id": 1, "path_norm": "c:/a", "path_orig": "C:/a", "via": "absolute"},
            {"catalog_id": 2, "path_norm": "c:/b", "path_orig": "C:/b", "via": "absolute"}])
        self.assertEqual(self.count("catalog_root"), 2)

    def test_insert_many_with_a_bad_row_leaves_nothing_pending(self):
        with self.assertRaises(ValueError):
            store.insert_many(self.conn, "catalog_root", [
                {"catalog_id": 1, "path_norm": "c:/a"}, {"bogus": 1}])
        self.assertFalse(self.conn.in_transaction)
        self.conn.commit()
        self.assertEqual(self.count("catalog_root"), 0)

    def test_put_hash_without_file_id_raises(self):
        with self.assertRaises(ValueError):
            store.put_hash(self.conn, sample="x")
        self.assertEqual(self.count("hash"), 0)

    def test_put_hash_commits_and_replaces_by_file_id(self):
        store.put_hash(self.conn, file_id=1, sample="s1")
        store.put_hash(self.conn, file_id=1, sample="s2")
        rows = self.other().execute("SELECT * FROM hash").fetchall()
        self.assertEqual([r["sample"] for r in rows], ["s2"])

    def test_insert_row_needs_caller_commit(self):
        new_id = store.insert_row(self.conn, "scan", root="c:/a", kind="files")
        self.assertEqual(new_id, 1)
        self.assertEqual(self.count("scan"), 0)
        self.conn.commit()
        self.assertEqual(self.count("scan"), 1)

    def test_utc_now_is_iso_seconds(self):
        text = store.utc_now()
        self.assertRegex(text, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        gap = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        self.assertLess(gap, 5)


if __name__ == "__main__":
    unittest.main()
