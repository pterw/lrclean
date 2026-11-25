import hashlib
import os
import sqlite3
import unittest
from contextlib import closing
from unittest import mock

import synth
from lrclean import catalogs
from test_catalogs import Tmp, listing

ENTRY = {"base": "IMG_0001", "root_relative": "../photos/"}


class Images(Tmp):
    def next_count(self):
        self.count = getattr(self, "count", 0) + 1
        return self.count

    def images_of(self, entries, **kw):
        source = self.path(f"cat{self.next_count()}", "main.lrcat")
        synth.make_catalog(source, entries, **kw)
        return self.read(source)

    def absolute(self, got):
        return [r for r in got.images if r["via"] == "absolute"]

    def developed_of(self, entries, **kw):
        got = self.images_of(entries, **kw)
        self.assertEqual(got.status, "ok")
        return [r["developed"] for r in self.absolute(got)]


class TestImageRows(Images):
    def test_master_and_virtual_copy_sharing_a_file_keep_their_own_rows_rule_24(self):
        got = self.images_of([{**ENTRY, "pick": 1}, {"copy_of": "IMG_0001", "pick": -1}])
        self.assertEqual(len(got.images), 4)
        for via in ("absolute", "relative"):
            picks = [r["pick"] for r in got.images if r["via"] == via]
            self.assertEqual(picks, [1, -1])
        self.assertEqual({r["path_norm"] for r in got.images if r["via"] == "absolute"}, {"c:/shoot-a/img_0001.arw"})

    def test_only_the_recorded_path_form_gets_a_row(self):
        got = self.images_of([{"base": "IMG_0001", "root_relative": None}])
        self.assertEqual([r["via"] for r in got.images], ["absolute"])

    def test_no_sidecar_path_row(self):
        got = self.images_of([{**ENTRY, "ext": "XMP", "sidecars": "JPG"}])
        self.assertEqual({r["path_norm"].rsplit(".", 1)[1] for r in got.images}, {"xmp"})

    def test_pick_values(self):
        cases = [(-1, -1), (-1.0, -1), (0, 0), (1, 1), (None, None), (2, None), ("-1", None), ("x", None)]
        for given, want in cases:
            with self.subTest(given=given):
                got = self.images_of([{**ENTRY, "pick": given}])
                self.assertEqual(got.status, "ok")
                self.assertEqual({r["pick"] for r in got.images}, {want})

    def test_missing_pick_column_stays_ok_with_unknown_picks(self):
        got = self.images_of([ENTRY])
        self.assertEqual((got.status, {r["pick"] for r in got.images}), ("ok", {None}))


class TestDevelopState(Images):
    def test_develop_state_by_column_layout_rc18(self):
        cases = [
            ("developed alone", {"developed": 1}, 1),
            ("developed_ex alone", {"developed_ex": 1}, 1),
            ("both 0", {"developed": 0, "developed_ex": 0}, 0),
            ("both NULL", {"developed": None, "developed_ex": None}, None),
            ("text in main", {"developed": "unknown", "developed_ex": 0}, None),
            ("text in ex", {"developed": 0, "developed_ex": "unknown"}, None),
            ("2 in main", {"developed": 2, "developed_ex": 0}, None),
            ("2 in ex", {"developed": 0, "developed_ex": 2}, None),
            ("text 1 in untyped column", {"developed": "1", "developed_ex": None}, None),
            ("main 0, ex NULL", {"developed": 0, "developed_ex": None}, 0),
            ("ex 1, main text", {"developed": "x", "developed_ex": 1}, 1),
            ("ex -1.0", {"developed_ex": -1.0}, 0),
            ("ex -1", {"developed_ex": -1}, 0),
            ("ex -1 main NULL", {"developed": None, "developed_ex": -1}, 0),
            ("ex -1 main 0", {"developed": 0, "developed_ex": -1}, 0),
            ("main -1 beside ex 1", {"developed": -1, "developed_ex": 1}, 1),
            ("two rows, 1 last", {"developed": 0, "developed_ex": 0}, 1, (1, 0)),
            ("two rows, 1 first", {"developed": 1, "developed_ex": 0}, 1, (0, 0)),
            ("two rows, ex -1 then ex 1", {"developed": None, "developed_ex": -1}, 1, (None, 1)),
            ("two rows, ex 1 then ex -1", {"developed": None, "developed_ex": 1}, 1, (None, -1)),
            ("two rows, 0 and all NULL", {"developed": 0, "developed_ex": 0}, None, (None, None)),
            ("two rows, both 0", {"developed": 0, "developed_ex": 0}, 0, (0, 0)),
        ]
        for name, keys, want, *second in cases:
            with self.subTest(name):
                source = self.path(f"dev{self.next_count()}", "main.lrcat")
                synth.make_catalog(source, [{**ENTRY, **keys}])
                if second:
                    with closing(sqlite3.connect(source)) as con, con:
                        con.execute("INSERT INTO Adobe_imageDevelopSettings VALUES(5000, 1, ?, ?)", second[0])
                got = self.read(source)
                self.assertEqual(got.status, "ok")
                self.assertEqual([r["developed"] for r in self.absolute(got)], [want])

    def test_image_without_a_settings_row_is_unknown(self):
        self.assertEqual(self.developed_of([{**ENTRY, "developed": 1}, {"base": "IMG_0002"}]), [1, None])

    def test_table_without_the_columns_or_without_the_table_is_unknown(self):
        self.assertEqual(self.developed_of([ENTRY], develop_columns=()), [None])
        self.assertEqual(self.developed_of([ENTRY]), [None])

    def test_settings_are_joined_on_the_image_column_not_the_row_id(self):
        entries = [ENTRY, {"base": "IMG_0002", "developed": 1}]
        self.assertEqual(self.developed_of(entries), [None, 1])


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestSettingsDigest(Images):
    GOOD = {"base": "IMG_0001", "settings": "good"}

    def settings_of(self, entries, extra_rows=(), **kw):
        """The settings of every absolute-form image, in image order; extra_rows add (image, text) rows."""
        source = self.path(f"set{self.next_count()}", "main.lrcat")
        synth.make_catalog(source, entries, **kw)
        with closing(sqlite3.connect(source)) as con, con:
            for n, (image, text) in enumerate(extra_rows):
                con.execute("INSERT INTO Adobe_imageDevelopSettings(id_local, image, text) VALUES(?, ?, ?)",
                            (5000 + n, image, text))
        got = self.read(source)
        self.assertEqual(got.status, "ok")
        return [r["settings"] for r in self.absolute(got)], got

    def test_text_is_stored_as_the_sha256_of_its_utf8_bytes_for_every_path_form(self):
        for text in ("x", "café 日本"):
            with self.subTest(text=text):
                settings, got = self.settings_of([self.GOOD, {**ENTRY, "settings": text}])
                self.assertEqual(settings, [sha("good"), sha(text)])
                self.assertEqual([r["settings"] for r in got.images if r["via"] == "relative"], settings)
        self.assertEqual(sha("x"), "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881")

    def test_an_image_without_a_usable_single_text_has_no_digest(self):
        cases = [
            ("NULL", {"settings": None}, ()),
            ("empty", {"settings": ""}, ()),
            ("a number", {"settings": 7}, ()),
            ("a BLOB", {"settings": b"abc"}, ()),
            ("two rows, different texts", {"settings": "a"}, ((2, "b"),)),
            ("one row NULL, another with text", {"settings": None}, ((2, "b"),)),
            ("one row with text, another NULL", {"settings": "a"}, ((2, None),)),
            ("one row with text, another empty", {"settings": "a"}, ((2, ""),)),
        ]
        for name, keys, extra in cases:
            with self.subTest(name):
                settings, _ = self.settings_of([self.GOOD, {"base": "IMG_0002", **keys}], extra)
                self.assertEqual(settings, [sha("good"), None])

    def test_two_rows_with_the_same_text_have_a_digest(self):
        settings, _ = self.settings_of([self.GOOD, {"base": "IMG_0002", "settings": "a"}], ((2, "a"),))
        self.assertEqual(settings, [sha("good"), sha("a")])

    def test_missing_text_column_table_or_settings_row_gives_no_digest_and_keeps_developed(self):
        cases = [
            ("no text column", {"developed": 1}, {}, [1, 1]),
            ("no table", {"developed": 1}, {"omit_tables": ("Adobe_imageDevelopSettings",)}, [None, None]),
        ]
        for name, keys, kw, developed in cases:
            with self.subTest(name):
                settings, got = self.settings_of([{"base": "IMG_0001", **keys}, {"base": "IMG_0002", **keys}], **kw)
                self.assertEqual(settings, [None, None])
                self.assertEqual([r["developed"] for r in self.absolute(got)], developed)
        settings, got = self.settings_of([self.GOOD, {"base": "IMG_0002"}])
        self.assertEqual((settings, [r["developed"] for r in self.absolute(got)]), ([sha("good"), None], [None, None]))

    def test_the_text_does_not_change_developed(self):
        entries = [{"base": "IMG_0001", "developed_ex": 1, "settings": "a"}, {"base": "IMG_0002", "developed_ex": 0, "settings": "b"}]
        settings, got = self.settings_of(entries)
        self.assertEqual([r["developed"] for r in self.absolute(got)], [1, 0])
        self.assertEqual(settings, [sha("a"), sha("b")])

    def test_virtual_copies_store_their_own_digests(self):
        for name, copy_text, want in (("other texts", "b", [sha("a"), sha("b")]), ("equal texts", "a", [sha("a")] * 2)):
            with self.subTest(name):
                entries = [self.GOOD, {"base": "IMG_0002", "settings": "a"}, {"copy_of": "IMG_0002", "settings": copy_text}]
                settings, _ = self.settings_of(entries)
                self.assertEqual(settings[1:], want)


class TestStoredRows(Images):
    def stored(self, source, **kw):
        conn = self.conn()
        ids = self.scan(conn, [os.path.dirname(source)], **kw)
        return conn, ids

    def test_reading_leaves_the_source_alone_and_old_state_and_shoot_both_store_rows(self):
        shoot = self.path("shoot", "a.lrcat")
        old = self.path("old", "b.lrcat")
        for source in (shoot, old):
            synth.make_catalog(source, [{**ENTRY, "pick": 1}])
        before = {p: listing(os.path.dirname(p)) for p in (shoot, old)}
        conn = self.conn()
        self.scan(conn, [os.path.dirname(shoot), os.path.dirname(old)], old_states=[old])
        self.assertEqual({p: listing(os.path.dirname(p)) for p in (shoot, old)}, before)
        rows = conn.execute("SELECT c.role, count(*) FROM image i JOIN catalog c ON c.id = i.catalog_id "
                            "GROUP BY c.role ORDER BY c.role").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("old_state", 2), ("shoot", 2)])

    def test_unreadable_catalog_stores_no_image_row(self):
        source = self.path("bad", "x.lrcat")
        synth.make_unreadable(source)
        conn, _ = self.stored(source)
        self.assertEqual(conn.execute("SELECT count(*) FROM image").fetchone()[0], 0)

    def test_pick_and_develop_state_only_in_the_wal_are_read(self):
        source = self.path("a", "main.lrcat")
        synth.make_unclean(source, [{**ENTRY, "pick": -1, "developed": 1, "settings": "s"}])
        conn, ids = self.stored(source)
        self.assertEqual(conn.execute("SELECT status FROM catalog").fetchone()[0], "ok")
        rows = conn.execute("SELECT pick, developed, settings FROM image").fetchall()
        self.assertEqual({tuple(r) for r in rows}, {(-1, 1, sha("s"))})
        self.assertEqual(len(rows), 2)

    def test_a_sqlite_error_while_reading_images_makes_the_catalog_partial_and_keeps_rows(self):
        source = self.path("cat", "main.lrcat")
        synth.make_catalog(source, [ENTRY, {**ENTRY, "base": "IMG_0002"}])
        real = catalogs._entry_records
        calls = []

        def failing(row, source_dir):
            calls.append(1)
            if len(calls) > 3:  # two file rows for the records, then the first image only
                raise sqlite3.OperationalError("boom")
            return real(row, source_dir)

        with mock.patch.object(catalogs, "_entry_records", failing):
            got = self.read(source)
        self.assertEqual(got.status, "partial")
        self.assertEqual(len(got.records), 4)
        self.assertEqual(len(got.images), 2)


class TestSidecarOverlapLimit(Images):
    def test_raw_declaring_jpg_and_a_jpeg_photo_row_a03(self):
        raw = {"base": "IMG_0001", "ext": "ARW", "sidecars": "JPG", "root_relative": "../photos/"}
        jpeg = {"base": "IMG_0001", "ext": "JPG", "root_relative": "../photos/"}
        for name, entries, flag in (("jpeg first", [jpeg, raw], 0), ("raw first", [raw, jpeg], 1)):
            with self.subTest(name):
                got = self.images_of(entries)
                jpgs = [r for r in got.records if r["ext"] == "jpg"]
                self.assertEqual([(r["via"], r["is_sidecar"]) for r in jpgs],
                                 [("absolute", flag), ("relative", flag)])
                self.assertEqual(sorted(r["via"] for r in got.images if r["path_norm"].endswith(".jpg")),
                                 ["absolute", "relative"])


if __name__ == "__main__":
    unittest.main()
