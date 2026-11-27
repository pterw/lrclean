import os
import tempfile
import unittest
from datetime import datetime, timezone

from lrclean import catalog_views as cv
from lrclean import paths, store

LIB = "C:/lib"
BAK = "C:/bak"
T1 = "2024-05-01T10:00:00"
T2 = "2024-05-02T10:00:00"


def ns(text: str, hours: float = 0) -> int:
    dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return int((dt.timestamp() + hours * 3600) * 1e9)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.connect(os.path.join(self.tmp.name, "work.db"))
        self.addCleanup(self.conn.close)
        self.cat_scan = store.begin_scan(self.conn, "C:/catalogs", "catalogs")
        store.finish_scan(self.conn, self.cat_scan, "complete")
        self.scans = {}

    def scan(self, root, status="complete"):
        sid = store.begin_scan(self.conn, root, "files")
        store.finish_scan(self.conn, sid, status)
        self.scans[root] = sid
        return sid

    def catalog(self, path, role="shoot", status="ok", **kw):
        values = dict(scan_id=self.cat_scan, source_path=path, snapshot_path="", role=role,
                      status=status, size=1, mtime_ns=1, db_version="1300014", wal_bytes=0,
                      has_lock=0)
        values.update(kw)
        cid = store.insert_row(self.conn, "catalog", **values)
        self.conn.commit()
        return cid

    def record(self, cid, path, capture=T1, via="absolute"):
        ext = path.rsplit(".", 1)[-1].lower()
        store.insert_row(self.conn, "record", catalog_id=cid, path_norm=paths.norm(path),
                         path_orig=path, via=via, ext=ext, capture_time=capture,
                         is_sidecar=int(ext in paths.SIDECAR_EXTS))
        self.conn.commit()

    def file(self, root, path, mtime_ns=0):
        ext = path.rsplit(".", 1)[-1].lower()
        store.insert_row(self.conn, "file", scan_id=self.scans[root], volume=paths.volume(path),
                         path_norm=paths.norm(path), path_orig=path, ext=ext, size=10,
                         mtime_ns=mtime_ns, attrs=0, placeholder=0, container="", file_index=0,
                         nlink=1, checked_at="")
        self.conn.commit()

    def root_folder(self, cid, path, via="absolute"):
        store.insert_row(self.conn, "catalog_root", catalog_id=cid, path_norm=paths.norm(path),
                         path_orig=path, via=via)
        self.conn.commit()

    def shoot(self, excluded=(), exists=lambda p: True):
        return {r["catalog"]: r for r in cv.shoot_catalog_rows(
            self.conn, library_roots=[LIB], backup_roots=[BAK],
            excluded_catalogs=list(excluded), exists=exists)}


class InventoryTest(Base):
    def test_every_catalog_is_a_row_with_stored_values_and_owner_exclusion(self):
        a = self.catalog("C:/c/a.lrcat", role="main", wal_bytes=7, has_lock=1)
        self.catalog("C:/c/b.lrcat", status="unreadable", db_version=None, reason="why-b")
        self.record(a, f"{LIB}/x.mp4")
        self.record(a, f"{LIB}/y.ARW")
        rows = cv.catalog_inventory_rows(self.conn, excluded_catalogs=["C:\\C\\B.lrcat"])
        self.assertEqual([r["catalog"] for r in rows], ["C:/c/a.lrcat", "C:/c/b.lrcat"])
        first, second = rows
        self.assertEqual((first["records"], first["wal_bytes"], first["has_lock"],
                          first["excluded_by_owner"]), (2, 7, "yes", "no"))
        self.assertEqual((second["status"], second["records"], second["excluded_by_owner"]),
                         ("unreadable", 0, "yes"))
        self.assertTrue(first["how_decided"])
        self.assertEqual((first["reason"], second["reason"]), ("", "why-b"))  # a row stored before the column: ""


class DateMatchesTest(unittest.TestCase):
    def test_whole_hour_offsets_within_a_second(self):
        base = ns(T1)
        for hours, expected in ((1, True), (-3, True), (0.5, False), (15, False), (0, True)):
            with self.subTest(hours=hours):
                self.assertIs(cv.date_matches(int(base + hours * 3600e9), T1), expected)
        self.assertTrue(cv.date_matches(base + 900_000_000, T1))
        self.assertFalse(cv.date_matches(base + 2_000_000_000, T1))

    def test_unparseable_text_is_false(self):
        for text in ("", "not a date", None):
            self.assertFalse(cv.date_matches(ns(T1), text))


class ShootTest(Base):
    def setUp(self):
        super().setUp()
        self.scan(LIB)
        self.scan(BAK)
        self.s = self.catalog("C:/c/shoot.lrcat")

    def test_counts_split_across_present_and_three_missing_classes(self):
        self.record(self.s, f"{LIB}/a/IMG_0001.ARW")
        self.file(LIB, f"{LIB}/a/IMG_0001.ARW")
        self.record(self.s, f"{LIB}/a/IMG_0002.ARW", T1)
        self.file(LIB, f"{LIB}/b/IMG_0002.ARW")
        self.record(self.s, f"{LIB}/a/IMG_0003.ARW", T1)
        self.record(self.s, f"{LIB}/a/IMG_0004.ARW", T1)
        self.record(self.s, f"{LIB}/a/IMG_0005.ARW", T1)
        self.file(LIB, f"{LIB}/c/IMG_0005.ARW")
        self.file(BAK, f"{BAK}/IMG_0005.ARW", ns(T1, 1))
        other = self.catalog("C:/c/other.lrcat")
        self.record(other, f"{LIB}/c/IMG_0003.ARW", T1)
        self.file(LIB, f"{LIB}/c/IMG_0003.ARW")
        self.record(other, f"{LIB}/c/IMG_0005.ARW", T2)
        self.file(BAK, f"{BAK}/IMG_0004.ARW", ns(T1, -3))
        self.file(BAK, f"{BAK}/IMG_0001.ARW", ns(T1))
        self.file(BAK, f"{BAK}/IMG_0003.ARW", ns(T1))
        self.record(self.s, f"{LIB}/a/IMG_0006.ARW", T1)
        self.file(BAK, f"{BAK}/IMG_0006.ARW", ns(T1, 0.5))
        self.record(self.s, f"{BAK}/kept/IMG_0007.ARW", T1)
        self.file(BAK, f"{BAK}/kept/IMG_0007.ARW", ns(T1))
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual(row["records_present"], 2)
        self.assertEqual(row["missing_moved"], 1)
        self.assertEqual(row["missing_same_name_only"], 2)
        self.assertEqual(row["missing_nothing"], 2)
        self.assertEqual(row["records_not_read"], 0)
        self.assertEqual(row["backup_same_name_only_lower_bound"], 1)
        self.assertEqual(row["backup_nothing_lower_bound"], 1)
        self.assertIn("lower bound", row["how_decided"])

    def test_same_name_with_different_capture_time_is_not_moved(self):
        self.record(self.s, f"{LIB}/a/IMG_0001.ARW", T1)
        other = self.catalog("C:/c/other.lrcat")
        self.record(other, f"{LIB}/c/IMG_0001.ARW", T2)
        self.file(LIB, f"{LIB}/c/IMG_0001.ARW")
        self.record(self.s, f"{LIB}/a/IMG_0002.ARW", "")
        gone = self.catalog("C:/c/gone.lrcat")
        self.record(gone, f"{LIB}/c/IMG_0002.ARW", "")
        self.file(LIB, f"{LIB}/c/IMG_0002.ARW")
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual((row["missing_moved"], row["missing_same_name_only"]), (0, 2))

    def test_same_name_and_time_in_two_folders_are_two_photos(self):
        self.record(self.s, f"{LIB}/day-1/IMG_0001.ARW")
        self.record(self.s, f"{LIB}/day-2/IMG_0001.ARW")
        self.file(LIB, f"{LIB}/day-1/IMG_0001.ARW")
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual((row["records_present"], row["missing_same_name_only"]), (1, 1))

    def test_moved_needs_a_readable_non_excluded_catalog_with_an_inventoried_file(self):
        self.record(self.s, f"{LIB}/a/IMG_0001.ARW")
        for name, status in (("excl", "ok"), ("bad", "unreadable"), ("nofile", "ok")):
            other = self.catalog(f"C:/c/{name}.lrcat", status=status)
            self.record(other, f"{LIB}/c/{name}/IMG_0001.ARW")
        self.file(LIB, f"{LIB}/c/excl/IMG_0001.ARW")
        self.file(LIB, f"{LIB}/c/bad/IMG_0001.ARW")
        row = self.shoot(excluded=["C:/c/excl.lrcat"])["C:/c/shoot.lrcat"]
        self.assertEqual((row["missing_moved"], row["missing_same_name_only"]), (0, 1))

    def test_records_under_partial_or_unscanned_root_are_only_not_read(self):
        self.scan("C:/part", "partial")
        self.record(self.s, "C:/part/IMG_0001.ARW")
        self.record(self.s, "C:/nowhere/IMG_0002.ARW")
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual(row["records_not_read"], 2)
        for col in ("records_present", "missing_moved", "missing_same_name_only",
                    "missing_nothing", "backup_nothing_lower_bound"):
            self.assertEqual(row[col], 0, col)

    def test_video_and_tiff_records_are_never_missing(self):
        self.record(self.s, f"{LIB}/a/clip.mp4")
        self.record(self.s, f"{LIB}/a/scan.tif")
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual((row["records_present"], row["missing_nothing"],
                          row["missing_same_name_only"], row["records_not_read"]), (0, 0, 0, 0))
        self.assertIn("not checked", row["how_decided"])
        self.assertIn("video", row["how_decided"])

    def test_only_shoot_catalogs_are_listed(self):
        self.catalog("C:/c/main.lrcat", role="main")
        self.assertEqual(list(self.shoot()), ["C:/c/shoot.lrcat"])

    def test_root_folders_show_absolute_rows_only_with_existence(self):
        self.root_folder(self.s, "C:/p/there")
        self.root_folder(self.s, "C:/p/gone")
        self.root_folder(self.s, "C:/p/rel", via="relative")
        row = self.shoot(exists=lambda p: p.endswith("there"))["C:/c/shoot.lrcat"]
        self.assertEqual(row["root_folders"], "C:/p/gone=missing;C:/p/there=exists")

    def test_uncatalogued_counts_unrecorded_photos_under_root_folders(self):
        self.root_folder(self.s, f"{LIB}/shoot-a")
        self.record(self.s, f"{LIB}/shoot-a/IMG_0001.ARW")
        for path in ("shoot-a/IMG_0001.ARW", "shoot-a/sub/IMG_0002.ARW", "shoot-a/IMG_0003.xmp",
                     "shoot-b/IMG_0004.ARW", "shoot-ab/IMG_0005.ARW"):
            self.file(LIB, f"{LIB}/{path}")
        self.root_folder(self.s, f"{LIB}/shoot-r", via="relative")
        self.file(LIB, f"{LIB}/shoot-r/IMG_0006.ARW")
        self.assertEqual(self.shoot()["C:/c/shoot.lrcat"]["uncatalogued_in_tree"], 1)

    def test_backup_not_complete_gives_no_backup_count(self):
        self.record(self.s, f"{LIB}/a/IMG_0001.ARW")
        self.conn.execute('UPDATE "scan" SET status = ? WHERE id = ?', ("partial", self.scans[BAK]))
        self.file(BAK, f"{BAK}/IMG_0001.ARW", ns(T1))
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual((row["missing_nothing"], row["backup_nothing_lower_bound"]), (1, 0))

    def test_record_on_backup_root_is_present_and_a_backup_copy_makes_it_moved(self):
        self.record(self.s, f"{LIB}/a/IMG_0001.ARW")
        other = self.catalog("C:/c/other.lrcat")
        self.record(other, f"{BAK}/IMG_0001.ARW")
        self.file(BAK, f"{BAK}/IMG_0001.ARW", ns(T1))
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual((row["missing_moved"], row["missing_nothing"]), (1, 0))

    def test_record_on_backup_root_not_complete_is_not_read(self):
        self.record(self.s, f"{BAK}/IMG_0001.ARW")
        self.conn.execute('UPDATE "scan" SET status = ? WHERE id = ?', ("partial", self.scans[BAK]))
        self.file(BAK, f"{BAK}/IMG_0001.ARW", ns(T1))
        row = self.shoot()["C:/c/shoot.lrcat"]
        self.assertEqual((row["records_not_read"], row["records_present"]), (1, 0))


class MissingTest(Base):
    def setUp(self):
        super().setUp()
        self.scan(LIB)
        self.live = self.catalog("C:/c/live.lrcat", role="main")

    def rows(self, live="c:\\C\\LIVE.lrcat", drives=lambda p: True):
        return {r["path"]: r for r in cv.missing_rows(
            self.conn, live_catalog=live, library_roots=[LIB, "C:/off", "C:/part"],
            backup_roots=[BAK, "Z:/bak"],
            drive_exists=drives)}

    def test_record_without_file_is_missing_and_present_via_relative_is_not(self):
        self.record(self.live, f"{LIB}/a/IMG_0001.ARW")
        self.record(self.live, f"{LIB}/b/x/IMG_0002.ARW", via="absolute")
        self.record(self.live, f"{LIB}/c/x/IMG_0002.ARW", via="relative")
        self.record(self.live, f"{LIB}/a/IMG_0003.xmp")
        self.record(self.live, f"{LIB}/a/clip.mp4")
        self.record(self.live, f"{LIB}/a/scan.tif")
        self.file(LIB, f"{LIB}/c/x/IMG_0002.ARW")
        rows = self.rows()
        self.assertEqual(set(rows), {f"{LIB}/a/IMG_0001.ARW", f"{LIB}/a/IMG_0003.xmp"})
        self.assertEqual((rows[f"{LIB}/a/IMG_0001.ARW"]["state"],
                          rows[f"{LIB}/a/IMG_0003.xmp"]["kind"]), ("missing", "sidecar"))
        self.assertEqual(rows[f"{LIB}/a/IMG_0001.ARW"]["capture_time"], T1)

    def test_not_connected_is_kept_apart_and_unscanned_is_never_missing(self):
        self.scan("C:/off", "not_connected")
        self.scan("C:/part", "partial")
        self.record(self.live, "C:/off/IMG_0001.ARW")
        self.record(self.live, "C:/part/IMG_0002.ARW")
        self.record(self.live, "C:/nowhere/IMG_0003.ARW")
        self.record(self.live, "Z:/x/IMG_0004.ARW")
        self.record(self.live, "Z:/bak/IMG_0005.ARW")
        rows = self.rows(drives=lambda p: not p.lower().startswith("z"))
        self.assertEqual({p: r["state"] for p, r in rows.items()}, {
            "C:/off/IMG_0001.ARW": "drive_not_connected",
            "C:/part/IMG_0002.ARW": "not_read",
            "C:/nowhere/IMG_0003.ARW": "not_read",
            "Z:/x/IMG_0004.ARW": "drive_not_connected",
            "Z:/bak/IMG_0005.ARW": "drive_not_connected"})

    def test_photo_with_records_in_several_states_takes_the_least_certain(self):
        self.scan("C:/off", "not_connected")
        self.scan("C:/part", "partial")
        for path, via in (("C:/off/x/IMG_0001.ARW", "absolute"), ("C:/part/x/IMG_0001.ARW", "relative"),
                          (f"{LIB}/x/IMG_0001.ARW", "relative")):
            self.record(self.live, path, via=via)
        for path, via in (("C:/part/x/IMG_0002.ARW", "absolute"), (f"{LIB}/x/IMG_0002.ARW", "relative")):
            self.record(self.live, path, via=via)
        states = {r["path"].rsplit("/", 1)[1]: r["state"] for r in self.rows().values()}
        self.assertEqual(states, {"IMG_0001.ARW": "drive_not_connected", "IMG_0002.ARW": "not_read"})

    def test_same_name_and_time_in_two_folders_are_two_photos(self):
        self.record(self.live, f"{LIB}/day-1/IMG_0001.ARW")
        self.record(self.live, f"{LIB}/day-2/IMG_0001.ARW")
        self.file(LIB, f"{LIB}/day-1/IMG_0001.ARW")
        self.assertEqual(list(self.rows()), [f"{LIB}/day-2/IMG_0001.ARW"])

    def test_backup_root_counts_as_scanned_and_other_catalogs_are_not_listed(self):
        self.scan(BAK)
        self.record(self.live, f"{BAK}/IMG_0001.ARW")
        other = self.catalog("C:/c/other.lrcat")
        self.record(other, f"{LIB}/q/IMG_0009.ARW")
        self.assertEqual({r["state"] for r in self.rows().values()}, {"missing"})
        self.assertEqual(list(self.rows()), [f"{BAK}/IMG_0001.ARW"])
        self.assertEqual(self.rows(live=None), {})


if __name__ == "__main__":
    unittest.main()
