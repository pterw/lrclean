import unittest

from lrclean import classify, file_views, paths, store

LIB = "C:/lib"
BAK = "C:/bak"
CAT = "C:/cat"
SEC = 1_000_000_000


class Scene:
    """Rows inserted literally, in the stored value forms."""

    def __init__(self, test):
        self.conn = store.connect(":memory:")
        test.addCleanup(self.conn.close)
        self.scans = {}
        self.cat_scan = None

    def scan(self, root, status="complete", kind="files"):
        sid = store.insert_row(self.conn, "scan", root=root, kind=kind, started="t",
                               finished="t", status=status)
        self.scans[root] = sid
        return sid

    def file(self, path, root=LIB, size=100, mtime=0, ext=None, container="", placeholder=0):
        ext = ext if ext is not None else path.rpartition(".")[2].lower()
        return store.insert_row(
            self.conn, "file", scan_id=self.scans[root], volume=paths.volume(path),
            path_norm=paths.norm(path), path_orig=path, ext=ext, size=size, mtime_ns=mtime,
            attrs=0, placeholder=placeholder, container=container, file_index=1, nlink=1,
            checked_at="t")

    def catalog(self, source, status="ok", records=()):
        if self.cat_scan is None:
            self.cat_scan = self.scan(CAT, kind="catalogs")
        cid = store.insert_row(
            self.conn, "catalog", scan_id=self.cat_scan, source_path=source, snapshot_path="",
            role="shoot", status=status, size=1, mtime_ns=1, db_version="1", wal_bytes=0,
            has_lock=0)
        for path, sidecar in records:
            store.insert_row(self.conn, "record", catalog_id=cid, path_norm=paths.norm(path),
                             path_orig=path, via="absolute", ext=path.rpartition(".")[2].lower(),
                             capture_time="", is_sidecar=sidecar)
        return cid


def lib_scene(test):
    s = Scene(test)
    s.scan(LIB)
    s.scan(BAK)
    return s


class PairTests(unittest.TestCase):
    def rows(self, s, coverage_complete=True):
        return file_views.pair_rows(s.conn, library_roots=[LIB],
                                    excluded_catalogs=[f"{CAT}/skip.lrcat"],
                                    coverage_complete=coverage_complete)

    def test_pairs_match_case_insensitively_and_ignore_other_names_folders_and_backup(self):
        """Rule 12: same folder, same base name ignoring case; backup pairs not listed."""
        s = lib_scene(self)
        s.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        s.file(f"{LIB}/shoot-a/img_0001.JPG", size=7)
        s.file(f"{LIB}/shoot-a/IMG_0002.jpg")
        s.file(f"{LIB}/shoot-b/IMG_0001.jpg")
        s.file(f"{BAK}/shoot-a/IMG_0001.ARW", root=BAK)
        s.file(f"{BAK}/shoot-a/IMG_0001.jpg", root=BAK)
        rows = self.rows(s)
        self.assertEqual([(r["folder"], r["pairs"], r["jpeg_bytes"]) for r in rows],
                         [(f"{LIB}/shoot-a", 1, 7)])

    def test_three_way_split_sums_to_pairs_and_bytes(self):
        """Section 6: sidecar, own photo and uncatalogued sum to the pair count and bytes."""
        s = lib_scene(self)
        for n, size in (("1", 10), ("2", 20), ("3", 40)):
            s.file(f"{LIB}/a/IMG_000{n}.ARW")
            s.file(f"{LIB}/a/IMG_000{n}.JPG", size=size)
        s.catalog(f"{CAT}/a.lrcat", records=[(f"{LIB}/a/IMG_0001.JPG", 1), (f"{LIB}/a/IMG_0002.JPG", 0)])
        s.file(f"{LIB}/a/IMG_0004.ARW")
        s.file(f"{LIB}/a/IMG_0004.JPG", size=80)
        s.file(f"{LIB}/a/IMG_0005.ARW")
        s.file(f"{LIB}/a/IMG_0005.JPG", size=160)
        s.catalog(f"{CAT}/skip.lrcat", records=[(f"{LIB}/a/IMG_0004.JPG", 1),
                                                (f"{LIB}/a/IMG_0005.JPG", 0)])
        (r,) = self.rows(s)
        # IMG_0004 and IMG_0005 are recorded only by an owner-excluded catalog (sidecar flag set
        # or not): uncatalogued
        self.assertEqual((r["sidecar_pairs"], r["own_photo_pairs"], r["uncatalogued_pairs"]), (1, 1, 3))
        self.assertEqual((r["sidecar_bytes"], r["own_photo_bytes"], r["uncatalogued_bytes"]),
                         (10, 20, 280))
        self.assertEqual((r["pairs"], r["jpeg_bytes"]), (5, 310))
        self.assertEqual(r["how_decided"], file_views.PAIR_HOW)
        # incomplete coverage: same counts, every row says the split may be wrong
        (limited,) = self.rows(s, coverage_complete=False)
        self.assertEqual({k: v for k, v in limited.items() if k != "how_decided"},
                         {k: v for k, v in r.items() if k != "how_decided"})
        self.assertTrue(limited["how_decided"].startswith(classify.NOT_RECORDED))
        self.assertIn(file_views.PAIR_HOW, limited["how_decided"])

    def test_same_time_counts_within_two_seconds_only(self):
        """Rule 12: one second apart is same-time, one hour apart is not; both stay in pairs."""
        s = lib_scene(self)
        s.file(f"{LIB}/a/IMG_0001.ARW", mtime=100 * SEC)
        s.file(f"{LIB}/a/IMG_0001.JPG", mtime=101 * SEC, size=5)
        s.file(f"{LIB}/a/IMG_0002.ARW", mtime=100 * SEC)
        s.file(f"{LIB}/a/IMG_0002.JPG", mtime=3700 * SEC, size=9)
        s.file(f"{LIB}/a/IMG_0003.ARW", mtime=100 * SEC)
        s.file(f"{LIB}/a/IMG_0003.JPG", mtime=102 * SEC, size=11)
        s.file(f"{LIB}/a/IMG_0004.ARW", mtime=100 * SEC)
        s.file(f"{LIB}/a/IMG_0004.JPG", mtime=102 * SEC + 1, size=13)
        (r,) = self.rows(s)
        self.assertEqual((r["pairs"], r["same_time_pairs"], r["same_time_bytes"]), (4, 2, 16))
        self.assertIn("camera JPEG", r["how_decided"])
        self.assertIn("export", r["how_decided"])

    def test_any_raw_of_the_base_name_within_two_seconds_makes_the_pair_same_time(self):
        """Rule 12: a later DNG conversion does not hide the camera pair."""
        s = lib_scene(self)
        s.file(f"{LIB}/a/IMG_0001.DNG", mtime=5000 * SEC)
        s.file(f"{LIB}/a/IMG_0001.ARW", mtime=100 * SEC)
        s.file(f"{LIB}/a/IMG_0001.JPG", mtime=100 * SEC, size=5)
        s.file(f"{LIB}/a/IMG_0002.ARW", mtime=100 * SEC)
        s.file(f"{LIB}/a/IMG_0002.DNG", mtime=5000 * SEC)
        s.file(f"{LIB}/a/IMG_0002.JPG", mtime=100 * SEC, size=6)
        (r,) = self.rows(s)
        self.assertEqual((r["pairs"], r["same_time_pairs"], r["same_time_bytes"]), (2, 2, 11))
        self.assertIn("one pair per JPEG", r["how_decided"])

    def test_pairs_under_a_root_that_is_not_complete_are_not_listed(self):
        s = Scene(self)
        s.scan(LIB, status="partial")
        s.file(f"{LIB}/a/IMG_0001.ARW")
        s.file(f"{LIB}/a/IMG_0001.JPG")
        self.assertEqual(self.rows(s), [])


class ContainerTests(unittest.TestCase):
    def build(self):
        s = lib_scene(self)
        for name, kind, size in (("shoot-a previews.lrdata", "cache", 300),
                                 ("shoot-a mobile downloads.lrdata", "storage", 50),
                                 ("mystery.lrdata", "unknown", 5)):
            path = f"{LIB}/x/{name}"
            box = paths.make_container(kind, paths.norm(path))
            s.file(path, ext="lrdata", size=size, container=box)
            s.file(f"{path}/a.bin", size=size, container=box)
        s.file(f"{BAK}/y/backup-only previews.lrdata", root=BAK, ext="lrdata",
               container=paths.make_container("cache", f"{BAK}/y/backup-only previews.lrdata"))
        s.catalog(f"{LIB}/x/shoot-a.lrcat", status="ok")
        return s

    def rows(self, s, complete=True):
        rows = file_views.container_rows(s.conn, library_roots=[LIB], coverage_complete=complete)
        return {paths.split_path(r["container"])[1]: r for r in rows}

    def test_only_cache_is_reclaimable_with_its_bytes_and_beside_catalog(self):
        """Rule 11: storage and unknown are no, cache is yes; backup caches are never listed."""
        rows = self.rows(self.build())
        self.assertEqual(len(rows), 3)
        self.assertNotIn("backup-only previews.lrdata", rows)
        cache = rows["shoot-a previews.lrdata"]
        self.assertEqual((cache["reclaimable"], cache["bytes"], cache["files"], cache["type"]),
                         ("yes", 300, 1, "cache"))
        self.assertEqual((cache["catalog_beside"], cache["catalog_state"]),
                         (f"{LIB}/x/shoot-a.lrcat", "ok"))
        self.assertEqual(rows["shoot-a mobile downloads.lrdata"]["reclaimable"], "no")
        self.assertEqual(rows["mystery.lrdata"]["reclaimable"], "no")
        self.assertEqual(rows["mystery.lrdata"]["catalog_beside"], "none")

    def test_container_on_a_root_that_is_not_complete_is_not_listed(self):
        """Rule 11 / section 9: only complete library scans are read."""
        s = Scene(self)
        s.scan(LIB, status="partial")
        path = f"{LIB}/x/shoot-a previews.lrdata"
        s.file(path, ext="lrdata", container=paths.make_container("cache", paths.norm(path)))
        rows = file_views.container_rows(s.conn, library_roots=[LIB], coverage_complete=True)
        self.assertEqual(rows, [])

    def test_catalog_beside_needs_the_stem_to_end_at_a_space_or_dot(self):
        """A container is not given a catalog whose name is only a prefix of its own."""
        s = self.build()
        path = f"{LIB}/x/shoot-a-v2 previews.lrdata"
        s.file(path, ext="lrdata", container=paths.make_container("cache", paths.norm(path)))
        self.assertEqual(self.rows(s)["shoot-a-v2 previews.lrdata"]["catalog_beside"], "none")
        s.catalog(f"{LIB}/x/shoot-a-v2.lrcat", status="empty")
        rows = self.rows(s)
        self.assertEqual(rows["shoot-a-v2 previews.lrdata"]["catalog_beside"],
                         f"{LIB}/x/shoot-a-v2.lrcat")
        self.assertEqual(rows["shoot-a previews.lrdata"]["catalog_beside"], f"{LIB}/x/shoot-a.lrcat")

    def test_cache_is_unverified_while_coverage_is_incomplete(self):
        """Rule 11: coverage incomplete makes the cache unverified and says so."""
        cache = self.rows(self.build(), complete=False)["shoot-a previews.lrdata"]
        self.assertEqual(cache["reclaimable"], "unverified")
        self.assertIn("coverage incomplete", cache["how_decided"])


class LooseTests(unittest.TestCase):
    def test_loose_photos_outside_photo_root_and_app_folders_unlisted(self):
        """Section 10: inside photo_root and backup files are not loose; app folders get one row."""
        s = lib_scene(self)
        s.file(f"{LIB}/photos/a/IMG_0001.ARW")
        s.file(f"{LIB}/other/IMG_0002.ARW", size=3)
        s.file(f"{LIB}/other/IMG_0003.jpg", size=4)
        s.file(f"{LIB}/Tools/Node_Modules/pkg/IMG_0004.jpg", size=5)
        s.file(f"{LIB}/Tools/Node_Modules/pkg2/IMG_0005.jpg", size=6)
        s.file(f"{BAK}/other/IMG_0006.ARW", root=BAK)
        s.file(f"{LIB}/Photos-old/IMG_0007.ARW", size=2)
        rows = file_views.loose_rows(s.conn, library_roots=[LIB], photo_root=f"{LIB}/Photos")
        self.assertEqual([(r["folder"], r["raws"], r["jpegs"], r["bytes"], r["listed"]) for r in rows],
                         [(f"{LIB}/Photos-old", 1, 0, 2, "yes"),
                          (f"{LIB}/Tools/Node_Modules", 0, 2, 11, "no"),
                          (f"{LIB}/other", 1, 1, 7, "yes")])


class NotReadTests(unittest.TestCase):
    def rows(self, s, stale=(), unseen=()):
        return file_views.not_read_rows(
            s.conn, library_roots=[LIB], backup_roots=[BAK, "C:/bak2", "C:/bak3"],
            catalog_roots=[CAT], excluded_catalogs=[f"{CAT}/skip.lrcat"], stale_catalogs=stale,
            unseen_catalogs=unseen)

    def test_each_cause_gives_one_row_of_its_kind(self):
        """Rule 7: placeholder, unreadable folder, partial, not connected, not scanned, limiting."""
        s = Scene(self)
        s.scan(LIB)
        s.scan(BAK, status="partial")
        s.scan("C:/bak2", status="not_connected")
        s.file(f"{LIB}/a/IMG_0001.ARW", placeholder=1)
        s.file(f"{LIB}/locked", ext="<unreadable>", size=0)
        s.file(f"{LIB}/a/IMG_0002.ARW")
        s.file(f"{BAK}/p/IMG_0009.ARW", root=BAK, placeholder=1)
        s.catalog(f"{CAT}/bad.lrcat", status="unreadable")
        s.catalog(f"{CAT}/empty.lrcat", status="empty")
        s.catalog(f"{CAT}/skip.lrcat", status="unreadable")
        got = sorted((r["kind"], r["item"]) for r in self.rows(
            s, stale=[f"{CAT}/old.lrcat"], unseen=[f"{CAT}/new.lrcat", f"{CAT}/skip.lrcat"]))
        self.assertEqual(got, sorted([
            ("placeholder", f"{LIB}/a/IMG_0001.ARW"), ("unreadable_folder", f"{LIB}/locked"),
            ("placeholder", f"{BAK}/p/IMG_0009.ARW"), ("partial_root", BAK), ("not_connected_root", "C:/bak2"),
            ("not_scanned_root", "C:/bak3"),
            ("limiting_catalog", f"{CAT}/bad.lrcat"),
            ("limiting_catalog", f"{CAT}/old.lrcat (changed since its snapshot)"),
            ("limiting_catalog", f"{CAT}/new.lrcat (appeared since the catalogs scan)")]))
        new = next(r for r in self.rows(s, unseen=[f"{CAT}/new.lrcat"]) if "new.lrcat" in r["item"])
        self.assertIn("not stored by the catalogs scan", new["how_decided"])

    def test_unscanned_catalog_root_is_limiting_and_unscanned_library_is_silent(self):
        """Rule 7: a not-scanned catalog root limits coverage; no library row for it."""
        s = Scene(self)
        rows = file_views.not_read_rows(s.conn, library_roots=[LIB], backup_roots=[],
                                        catalog_roots=[CAT], excluded_catalogs=[],
                                        stale_catalogs=[], unseen_catalogs=[])
        self.assertEqual([(r["kind"], r["item"]) for r in rows],
                         [("limiting_catalog", f"{CAT} (not scanned)")])


if __name__ == "__main__":
    unittest.main()
