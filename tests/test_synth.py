import os
import shutil
import sqlite3
import tempfile
import unittest

import synth

ENTRY = {"root": "c:/shoot-a/", "root_relative": "", "folder": "day-1/", "base": "IMG_0001", "ext": "ARW"}


def query(path, sql):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


class SynthTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_make_tree_creates_exactly_the_given_files_with_nested_folders(self):
        root = os.path.join(self.dir, "tree")
        files = {"shoot-a/IMG_0001.ARW": b"raw", "shoot-a/day-1/deep/IMG_0002.JPG": b"jpg", "top.txt": b""}
        synth.make_tree(root, files)
        found = {}
        for folder, _, names in os.walk(root):
            for name in names:
                full = os.path.join(folder, name)
                with open(full, "rb") as handle:
                    found[os.path.relpath(full, root).replace(os.sep, "/")] = handle.read()
        self.assertEqual(found, files)

    def test_make_catalog_has_c6_tables_columns_and_version(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [ENTRY], db_version="1200000")
        self.assertEqual(query(path, "SELECT value FROM Adobe_variablesTable WHERE name='Adobe_DBVersion'"), [("1200000",)])
        self.assertEqual(
            query(path, "SELECT absolutePath, relativePathFromCatalog FROM AgLibraryRootFolder"), [("c:/shoot-a/", "")]
        )
        self.assertEqual(query(path, "SELECT pathFromRoot, rootFolder FROM AgLibraryFolder"), [("day-1/", 1)])
        self.assertEqual(
            query(path, "SELECT baseName, extension, folder, sidecarExtensions FROM AgLibraryFile"),
            [("IMG_0001", "ARW", 1, "")],
        )
        self.assertEqual(
            query(path, "SELECT rootFile, captureTime, masterImage FROM Adobe_images"),
            [(1, "2024-05-01T10:00:00", None)],
        )

    def test_null_root_relative_is_stored_as_null(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [{**ENTRY, "root_relative": None}])
        self.assertEqual(query(path, "SELECT relativePathFromCatalog FROM AgLibraryRootFolder"), [(None,)])

    def test_omit_tables_omits_exactly_those_tables(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [ENTRY], omit_tables=("Adobe_images", "AgLibraryFolder", "Adobe_variablesTable"))
        names = {row[0] for row in query(path, "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, {"AgLibraryRootFolder", "AgLibraryFile"})

    def test_entries_share_root_and_folder_rows_and_distinct_ones_get_new_ids(self):
        path = os.path.join(self.dir, "a.lrcat")
        other_root = {**ENTRY, "root": "d:/shoot-b/", "root_relative": None, "base": "IMG_0003"}
        other_folder = {**ENTRY, "folder": "day-2/", "base": "IMG_0004", "sidecars": "xmp", "ext": "CR3"}
        synth.make_catalog(path, [ENTRY, {**ENTRY, "base": "IMG_0002"}, other_root, other_folder])
        self.assertEqual(
            query(path, "SELECT id_local, absolutePath, relativePathFromCatalog FROM AgLibraryRootFolder ORDER BY id_local"),
            [(1, "c:/shoot-a/", ""), (2, "d:/shoot-b/", None)],
        )
        self.assertEqual(
            query(path, "SELECT id_local, pathFromRoot, rootFolder FROM AgLibraryFolder ORDER BY id_local"),
            [(1, "day-1/", 1), (2, "day-1/", 2), (3, "day-2/", 1)],
        )
        self.assertEqual(
            query(path, "SELECT baseName, extension, folder, sidecarExtensions FROM AgLibraryFile ORDER BY id_local"),
            [("IMG_0001", "ARW", 1, ""), ("IMG_0002", "ARW", 1, ""), ("IMG_0003", "ARW", 2, ""), ("IMG_0004", "CR3", 3, "xmp")],
        )

    def test_virtual_copy_points_at_its_masters_image(self):
        path = os.path.join(self.dir, "a.lrcat")
        copy = {**ENTRY, "base": "IMG_0001-copy", "master": "IMG_0001"}
        synth.make_catalog(path, [ENTRY, copy])
        self.assertEqual(query(path, "SELECT id_local, masterImage FROM Adobe_images ORDER BY id_local"), [(1, None), (2, 1)])

    def test_unclean_rows_visible_only_with_wal_beside_and_lock_exists(self):
        path = os.path.join(self.dir, "u.lrcat")
        synth.make_unclean(path, [ENTRY])
        self.assertTrue(os.path.exists(path + ".lock"))
        self.assertTrue(os.path.exists(path + "-wal"))
        self.assertTrue(os.path.exists(path + "-shm"))
        count = "SELECT count(*) FROM Adobe_images"
        bare = os.path.join(self.dir, "bare.lrcat")
        shutil.copyfile(path, bare)
        self.assertEqual(query(bare, count), [(0,)])
        with_wal = os.path.join(self.dir, "wal.lrcat")
        shutil.copyfile(path, with_wal)
        shutil.copyfile(path + "-wal", with_wal + "-wal")
        self.assertEqual(query(with_wal, count), [(1,)])

    def test_catalog_without_the_new_keys_has_no_pick_column_and_no_develop_table(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [ENTRY])
        columns = [row[1] for row in query(path, "PRAGMA table_info(Adobe_images)")]
        self.assertEqual(columns, ["id_local", "rootFile", "captureTime", "masterImage"])
        names = {row[0] for row in query(path, "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("Adobe_imageDevelopSettings", names)

    def test_pick_values_read_back_with_the_type_they_were_given(self):
        path = os.path.join(self.dir, "a.lrcat")
        picks = [-1, -1.0, 0, 1, None, "-1", "x"]
        entries = [{**ENTRY, "base": f"IMG_{n:04d}", "pick": pick} for n, pick in enumerate(picks)]
        synth.make_catalog(path, entries + [{**ENTRY, "base": "IMG_9999"}])
        rows = query(path, "SELECT pick, typeof(pick) FROM Adobe_images ORDER BY id_local")
        self.assertEqual(
            rows,
            [(-1, "integer"), (-1.0, "real"), (0, "integer"), (1, "integer"), (None, "null"), ("-1", "text"), ("x", "text"), (None, "null")],
        )
        self.assertIsInstance(rows[1][0], float)

    def test_develop_table_has_exactly_the_named_columns_and_a_row_per_naming_entry(self):
        both = ("hasDevelopAdjustments", "hasDevelopAdjustmentsEx")
        cases = [
            ({"developed": 1}, ("hasDevelopAdjustments",)),
            ({"developed_ex": 1}, ("hasDevelopAdjustmentsEx",)),
            ({"developed": 1, "developed_ex": 0}, both),
        ]
        for named, columns in cases:
            with self.subTest(named=named):
                path = os.path.join(self.dir, f"{len(named)}{sorted(named)[0]}.lrcat")
                synth.make_catalog(path, [{**ENTRY, **named}, {**ENTRY, "base": "IMG_0002"}])
                info = [row[1] for row in query(path, "PRAGMA table_info(Adobe_imageDevelopSettings)")]
                self.assertEqual(info, ["id_local", "image", *columns])
                self.assertEqual(query(path, "SELECT image FROM Adobe_imageDevelopSettings"), [(1,)])

    def test_develop_columns_forces_the_columns_and_the_table(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [ENTRY], develop_columns=())
        info = [row[1] for row in query(path, "PRAGMA table_info(Adobe_imageDevelopSettings)")]
        self.assertEqual(info, ["id_local", "image"])
        forced = os.path.join(self.dir, "b.lrcat")
        synth.make_catalog(forced, [{**ENTRY, "developed": 1}], develop_columns=("hasDevelopAdjustmentsEx",))
        info = [row[1] for row in query(forced, "PRAGMA table_info(Adobe_imageDevelopSettings)")]
        self.assertEqual(info, ["id_local", "image", "hasDevelopAdjustmentsEx"])
        self.assertEqual(query(forced, "SELECT hasDevelopAdjustmentsEx FROM Adobe_imageDevelopSettings"), [(None,)])

    def test_omit_tables_removes_the_develop_table(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [{**ENTRY, "developed": 1}], omit_tables=("Adobe_imageDevelopSettings",))
        names = {row[0] for row in query(path, "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("Adobe_imageDevelopSettings", names)

    def test_develop_values_read_back_with_the_type_they_were_given(self):
        values = [None, -1, -1.0, 0, 1, 2, "unknown", "1"]
        types = ["null", "integer", "real", "integer", "integer", "integer", "text", "text"]
        entries = [{**ENTRY, "base": f"IMG_{n:04d}", "developed": v, "developed_ex": v} for n, v in enumerate(values)]
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, entries)
        sql = "SELECT hasDevelopAdjustments, typeof(hasDevelopAdjustments), hasDevelopAdjustmentsEx, typeof(hasDevelopAdjustmentsEx) FROM Adobe_imageDevelopSettings ORDER BY image"
        rows = query(path, sql)
        self.assertEqual(rows, [(v, t, v, t) for v, t in zip(values, types)])
        self.assertIsInstance(rows[2][0], float)

    def test_settings_are_stored_in_the_text_column_as_given_and_only_when_asked_for(self):
        values = [None, "x", "", 7, "café"]
        types = ["null", "text", "text", "integer", "text"]
        entries = [{**ENTRY, "base": f"IMG_{n:04d}", "settings": v} for n, v in enumerate(values)]
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, entries)
        rows = query(path, "SELECT text, typeof(text) FROM Adobe_imageDevelopSettings ORDER BY image")
        self.assertEqual(rows, list(zip(values, types)))
        plain = os.path.join(self.dir, "b.lrcat")
        synth.make_catalog(plain, [{**ENTRY, "developed": 1}])
        info = [row[1] for row in query(plain, "PRAGMA table_info(Adobe_imageDevelopSettings)")]
        self.assertEqual(info, ["id_local", "image", "hasDevelopAdjustments"])

    def test_develop_columns_accepts_text(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [{**ENTRY, "developed": 1}], develop_columns=("text",))
        info = [row[1] for row in query(path, "PRAGMA table_info(Adobe_imageDevelopSettings)")]
        self.assertEqual(info, ["id_local", "image", "text"])
        self.assertEqual(query(path, "SELECT text FROM Adobe_imageDevelopSettings"), [(None,)])

    def test_develop_row_ids_are_independent_of_image_ids(self):
        path = os.path.join(self.dir, "a.lrcat")
        synth.make_catalog(path, [ENTRY, {**ENTRY, "base": "IMG_0002", "developed": 1}])
        self.assertEqual(query(path, "SELECT id_local, image FROM Adobe_imageDevelopSettings"), [(1002, 2)])
        self.assertEqual(
            query(path, "SELECT count(*) FROM Adobe_imageDevelopSettings WHERE id_local IN (SELECT id_local FROM Adobe_images)"),
            [(0,)],
        )

    def test_copy_of_shares_the_masters_file_and_carries_its_own_pick_and_develop_state(self):
        path = os.path.join(self.dir, "a.lrcat")
        other = {**ENTRY, "base": "IMG_0002"}
        copy = {"copy_of": "IMG_0002", "pick": -1, "developed": 1}
        synth.make_catalog(path, [ENTRY, other, copy, {**ENTRY, "base": "IMG_0003"}])
        self.assertEqual(query(path, "SELECT count(*) FROM AgLibraryFile"), [(3,)])
        self.assertEqual(query(path, "SELECT id_local, rootFile, masterImage, pick FROM Adobe_images ORDER BY id_local")[1:], [
            (2, 2, None, None), (3, 2, 2, -1), (4, 3, None, None)
        ])
        self.assertEqual(query(path, "SELECT image, hasDevelopAdjustments FROM Adobe_imageDevelopSettings"), [(3, 1)])

    def test_unclean_pick_and_develop_state_are_only_in_the_wal(self):
        path = os.path.join(self.dir, "u.lrcat")
        synth.make_unclean(path, [{**ENTRY, "pick": -1, "developed": 1, "settings": "s"}])
        self.assertGreater(os.path.getsize(path + "-wal"), 0)
        bare = os.path.join(self.dir, "bare.lrcat")
        shutil.copyfile(path, bare)  # before any read of `path`, which would checkpoint the -wal
        self.assertEqual(query(bare, "SELECT count(*) FROM Adobe_images"), [(0,)])
        self.assertEqual(query(bare, "SELECT count(*) FROM Adobe_imageDevelopSettings"), [(0,)])
        sql = "SELECT i.pick, d.hasDevelopAdjustments FROM Adobe_images i JOIN Adobe_imageDevelopSettings d ON d.image = i.id_local"
        self.assertEqual(query(path, sql), [(-1, 1)])
        self.assertEqual(query(path, "SELECT text FROM Adobe_imageDevelopSettings"), [("s",)])

    def test_empty_shell_is_a_database_file_with_no_tables(self):
        path = os.path.join(self.dir, "s.lrcat")
        synth.make_empty_shell(path)
        self.assertGreater(os.path.getsize(path), 0)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(16), b"SQLite format 3\x00")
        self.assertEqual(query(path, "SELECT name FROM sqlite_master"), [])

    def test_unreadable_is_garbage_of_the_given_size_and_not_a_database(self):
        path = os.path.join(self.dir, "g.lrcat")
        synth.make_unreadable(path, size=4096)
        self.assertEqual(os.path.getsize(path), 4096)
        with self.assertRaises(sqlite3.DatabaseError):
            query(path, "SELECT name FROM sqlite_master")


if __name__ == "__main__":
    unittest.main()
