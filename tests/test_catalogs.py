import contextlib
import csv
import hashlib
import os
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import synth
from lrclean import catalog_views, catalogs, paths, report, store

NONE_RUNNING = lambda: []  # noqa: E731
ASTRAL = "\U0001F4F7\U0001F4F8\U0001F4F9" * 41 + ".lrcat"  # 252 UTF-16 units, 129 characters


def name_of(units):
    """A catalog file name of exactly `units` UTF-16 units; its stem cycles a to z, so its two ends differ."""
    return "".join(chr(ord("a") + i % 26) for i in range(units - len(".lrcat"))) + ".lrcat"


def invalid_for(suffix, source, size=7, mtime_ns=11):
    """A stat answering `source` itself, failing `suffix` with an invalid-name error, the rest not found."""
    def stat(path):
        if path == paths.fs_path(source):
            return SimpleNamespace(st_size=size, st_mtime_ns=mtime_ns, st_file_attributes=0)
        if path.endswith(suffix):
            raise OSError(22, "invalid name")
        raise FileNotFoundError(path)
    return stat


def listing(folder):
    out = {}
    for base, _dirs, names in os.walk(folder):
        for name in names:
            full = os.path.join(base, name)
            st = os.stat(full)
            with open(full, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            out[os.path.relpath(full, folder)] = (st.st_size, st.st_mtime_ns, digest)
    return out


def placeholder_stat(*endings):
    """A stat that reports the online-only attribute for paths ending in one of `endings`."""
    def stat(path):
        real = os.stat(path)
        if path.replace("\\", "/").endswith(endings):
            return SimpleNamespace(st_file_attributes=0x400000, st_size=real.st_size,
                                   st_mtime_ns=real.st_mtime_ns)
        return real
    return stat


class Tmp(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = holder.name
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)

    def path(self, *parts):
        full = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    def conn(self):
        conn = store.connect(os.path.join(self.work, "work.db"))
        self.addCleanup(conn.close)
        return conn

    def read(self, source):
        snap = catalogs.snapshot_catalog(source, self.work, NONE_RUNNING)
        return catalogs.read_snapshot(snap, source)

    def scan(self, conn, roots, **kw):
        args = dict(catalog_roots=roots, own_dir=os.path.join(self.tmp, "own"), exclude=[],
                    work_dir=self.work, live_catalog=None, old_states=[], processes=NONE_RUNNING)
        args.update(kw)
        return catalogs.scan_catalogs(conn, **args)


class TestRecords(Tmp):
    def test_sidecar_variants_and_relative_path_rule_2(self):
        for sidecars, expected in (("JPG", {"jpg"}), ("xmp", {"xmp"}), ("jpg,xmp", {"jpg", "xmp"}),
                                   ("xmp,acr", {"xmp", "acr"})):
            with self.subTest(sidecars=sidecars):
                source = self.path(f"c-{sidecars}", "cat", "main.lrcat")
                synth.make_catalog(source, [{"root_relative": "../photos/", "folder": "sub/",
                                             "sidecars": sidecars}])
                got = self.read(source)
                real = os.path.join(os.path.dirname(source), "..", "photos", "sub", "IMG_0001.ARW")
                synth.make_tree(os.path.dirname(os.path.dirname(source)), {"photos/sub/IMG_0001.ARW": b"x"})
                rel = {(r["ext"], r["is_sidecar"]) for r in got.records if r["via"] == "relative"}
                absolute = {(r["ext"], r["is_sidecar"]) for r in got.records if r["via"] == "absolute"}
                want = {("arw", 0)} | {(e, 1) for e in expected}
                self.assertEqual(rel, want)
                self.assertEqual(absolute, want)
                main = [r for r in got.records if r["via"] == "relative" and r["ext"] == "arw"][0]
                self.assertEqual(main["path_norm"], paths.norm(os.path.normpath(real)))
                self.assertTrue(os.path.exists(main["path_orig"]))

    def test_virtual_copy_takes_master_capture_time_rule_2(self):
        source = self.path("cat", "main.lrcat")
        synth.make_catalog(source, [
            {"base": "IMG_1", "capture": "2024-01-01T00:00:00"},
            {"base": "IMG_1_vc", "capture": "2025-01-01T00:00:00", "master": "IMG_1"}])
        got = self.read(source)
        times = {r["path_orig"].rsplit("/", 1)[-1]: r["capture_time"] for r in got.records if r["via"] == "absolute"}
        self.assertEqual(times["IMG_1_vc.arw"], "2024-01-01T00:00:00")


    def test_a_file_with_a_virtual_copy_is_recorded_once_rule_2(self):
        source = self.path("cat", "main.lrcat")
        synth.make_catalog(source, [{"base": "IMG_1", "root_relative": "../photos/"}])
        con = sqlite3.connect(source)
        con.execute("INSERT INTO Adobe_images VALUES(2, 1, '2025-01-01T00:00:00', 1)")
        con.commit()
        rows = con.execute("SELECT COUNT(*) FROM AgLibraryFile").fetchone()[0]
        con.close()
        got = self.read(source)
        self.assertEqual(rows, 1)
        self.assertEqual(sorted(r["via"] for r in got.records), ["absolute", "relative"])


class TestSnapshot(Tmp):
    def test_same_name_in_two_folders_gets_two_snapshots_rule_6(self):
        a, b = self.path("a", "main.lrcat"), self.path("b", "main.lrcat")
        for src in (a, b):
            synth.make_catalog(src, [])
            for suffix in ("-wal", "-shm"):
                synth.make_tree(os.path.dirname(src), {"main.lrcat" + suffix: b"x"})
        snaps = [catalogs.snapshot_catalog(s, self.work, NONE_RUNNING) for s in (a, b)]
        self.assertNotEqual(os.path.dirname(snaps[0]), os.path.dirname(snaps[1]))
        for snap in snaps:
            self.assertTrue(os.path.exists(snap + "-wal"))
            self.assertTrue(os.path.exists(snap + "-shm"))

    def test_earlier_snapshot_files_are_replaced_and_nothing_else_deleted_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        for suffix in ("-wal", "-shm"):
            synth.make_tree(os.path.dirname(source), {"main.lrcat" + suffix: b"x"})
        first = catalogs.snapshot_catalog(source, self.work, NONE_RUNNING)
        neighbours = [os.path.join(os.path.dirname(first), "notes.txt"),
                      os.path.join(os.path.dirname(os.path.dirname(first)), "other.txt")]
        for neighbour in neighbours:
            with open(neighbour, "wb") as handle:
                handle.write(b"k")
        for suffix in ("-wal", "-shm"):
            os.remove(source + suffix)
        second = catalogs.snapshot_catalog(source, self.work, NONE_RUNNING)
        self.assertEqual(second, first)
        self.assertFalse(os.path.exists(second + "-wal"))
        self.assertFalse(os.path.exists(second + "-shm"))
        self.assertTrue(all(os.path.exists(n) for n in neighbours))

    def test_work_dir_inside_the_catalog_folder_is_refused_and_nothing_created_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        before = listing(os.path.dirname(source))
        home = os.path.dirname(source)
        # The relative spelling is taken from the temporary folder, so it exists even when the checkout is on
        # another drive (issue #13); the working folder is restored before the temporary folder is removed.
        self.enterContext(contextlib.chdir(os.path.dirname(home)))
        spellings = [home, os.path.join(home, "work"), os.path.join(home, "x", "..", "work"),
                     os.path.relpath(os.path.join(home, "work"))]
        self.assertFalse(os.path.isabs(spellings[-1]))
        for work in spellings:
            with self.subTest(work=work):
                with self.assertRaises(ValueError):
                    catalogs.snapshot_catalog(source, work, NONE_RUNNING)
                self.assertEqual(listing(home), before)
                self.assertFalse(os.path.exists(os.path.join(work, "catalogs")))
        self.assertEqual(sorted(os.listdir(home)), ["main.lrcat"])
        sibling = os.path.join(os.path.dirname(home), "a-work")
        self.assertTrue(os.path.exists(catalogs.snapshot_catalog(source, sibling, NONE_RUNNING)))

    def test_a_work_dir_that_resolves_into_the_catalog_folder_through_a_link_is_refused_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        home = os.path.dirname(source)
        for suffix, data in (("-wal", b"W" * 4096), ("-shm", b"M" * 1024)):
            with open(source + suffix, "wb") as handle:
                handle.write(data)
        made = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(self.tmp, "up"), home],
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(os.rmdir, os.path.join(self.tmp, "up"))  # the link only, never the target
        before, names = listing(home), sorted(os.listdir(home))
        self.assertEqual(len(before), 3)
        with self.assertRaises(ValueError):
            catalogs.snapshot_catalog(source, os.path.join(self.tmp, "up", "w"), NONE_RUNNING)
        self.assertEqual(listing(home), before)
        self.assertEqual(sorted(os.listdir(home)), names)

    def test_a_snapshot_folder_that_is_itself_a_link_to_the_catalog_folder_is_refused_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        home = os.path.dirname(source)
        for suffix, data in (("-wal", b"W" * 4096), ("-shm", b"M" * 1024)):
            with open(source + suffix, "wb") as handle:
                handle.write(data)
        link = os.path.join(self.work, "catalogs", "main-" + hashlib.sha1(paths.norm(source).encode("utf-8")).hexdigest()[:12])
        os.makedirs(os.path.dirname(link))
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, home], capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(os.rmdir, link)  # the link only, never the target
        before, names = listing(home), sorted(os.listdir(home))
        self.assertEqual(len(before), 3)
        with self.assertRaises(ValueError):
            catalogs.snapshot_catalog(source, self.work, NONE_RUNNING)
        self.assertEqual(listing(home), before)
        self.assertEqual(sorted(os.listdir(home)), names)

    def test_work_dir_inside_the_catalog_folder_gives_an_unreadable_row_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        conn = self.conn()
        ids = self.scan(conn, [os.path.dirname(source)], work_dir=os.path.join(os.path.dirname(source), "w"))
        self.assertEqual(len(ids), 1)
        self.assertEqual(conn.execute("SELECT status FROM catalog").fetchone()[0], "unreadable")

    def test_online_only_catalog_file_is_never_copied_and_the_earlier_snapshot_stays(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        for suffix in ("-wal", "-shm"):
            synth.make_tree(os.path.dirname(source), {"main.lrcat" + suffix: b"x"})
        catalogs.snapshot_catalog(source, self.work, NONE_RUNNING)
        before = listing(self.work)
        for suffix in ("", "-wal", "-shm"):
            with self.subTest(placeholder="main.lrcat" + suffix):
                with mock.patch("shutil.copyfile") as copy:
                    with self.assertRaisesRegex(ValueError, "online-only"):
                        catalogs.snapshot_catalog(source, self.work, NONE_RUNNING,
                                                  stat=placeholder_stat("a/main.lrcat" + suffix))
                copy.assert_not_called()
                self.assertEqual(listing(self.work), before)
        fresh = os.path.join(self.tmp, "fresh-work")
        os.makedirs(fresh)
        with self.assertRaisesRegex(ValueError, "online-only"):
            catalogs.snapshot_catalog(source, fresh, NONE_RUNNING, stat=placeholder_stat("a/main.lrcat"))
        self.assertEqual(os.listdir(fresh), [])

    def test_online_only_catalog_is_an_unreadable_row_and_the_scan_goes_on(self):
        for name in ("shoot-a", "shoot-b"):
            synth.make_catalog(self.path(name, "main.lrcat"), [])
        conn = self.conn()
        ids = self.scan(conn, [self.tmp], stat=placeholder_stat("shoot-a/main.lrcat"))
        self.assertEqual(len(ids), 2)
        rows = {os.path.basename(os.path.dirname(s)): (st, snap) for s, st, snap in conn.execute(
            "SELECT source_path, status, snapshot_path FROM catalog")}
        self.assertEqual(rows["shoot-a"], ("unreadable", ""))
        self.assertEqual(rows["shoot-b"][0], "ok")
        self.assertNotEqual(rows["shoot-b"][1], "")
        self.assertEqual(conn.execute("SELECT status FROM scan").fetchone()[0], "complete")

    def test_a_stat_that_fails_on_a_catalog_file_is_an_unreadable_row_with_nothing_copied(self):
        synth.make_catalog(self.path("shoot-a", "main.lrcat"), [])
        conn = self.conn()
        denied_work = os.path.join(self.tmp, "denied-work")
        os.makedirs(denied_work)

        def denied_stat(path):
            if path.replace("\\", "/").endswith("shoot-a/main.lrcat-shm"):
                raise PermissionError("denied")
            return os.stat(path)

        with mock.patch("shutil.copyfile") as copy:
            self.scan(conn, [os.path.join(self.tmp, "shoot-a")], stat=denied_stat, work_dir=denied_work)
        copy.assert_not_called()
        self.assertEqual(os.listdir(denied_work), [])
        self.assertEqual([tuple(r) for r in conn.execute("SELECT status, snapshot_path FROM catalog")],
                         [("unreadable", "")])
        source = os.path.join(self.tmp, "shoot-b", name_of(249))
        for suffix in ("-wal", "-shm"):
            with self.subTest(fitting=suffix):
                self.assertLessEqual(paths.name_units(name_of(249) + suffix), paths.NAME_LIMIT)
                with mock.patch("shutil.copyfile") as copy:
                    with self.assertRaises(ValueError) as caught:
                        catalogs.snapshot_catalog(source, self.work, NONE_RUNNING, stat=invalid_for(suffix, source))
                copy.assert_not_called()
                self.assertIn(source + suffix, str(caught.exception))
        self.assertFalse(os.path.exists(os.path.join(self.work, "catalogs")))

    def make_pair(self):
        """Two catalogs of different names and sizes; returns the sources of shoot-a and shoot-b."""
        a, b = self.path("shoot-a", "main.lrcat"), self.path("shoot-b", "other.lrcat")
        synth.make_catalog(a, [])
        synth.make_catalog(b, [{"base": f"IMG_{n:04d}"} for n in range(400)])
        self.assertNotEqual(os.path.basename(a), os.path.basename(b))
        self.assertNotEqual(os.path.getsize(a), os.path.getsize(b))
        return a, b

    def test_a_catalog_that_vanishes_before_the_copy_is_unreadable_not_empty_issue_34(self):
        a, b = self.make_pair()
        calls = []

        def vanishing(*_):
            calls.append(1)
            if len(calls) == 1:
                os.rename(a, a + ".away")
            return []

        conn = self.conn()
        try:
            self.scan(conn, [self.tmp], processes=vanishing)
        finally:
            os.rename(a + ".away", a)
        rows = {os.path.basename(os.path.dirname(s)): (st, reason) for s, st, reason in conn.execute(
            "SELECT source_path, status, reason FROM catalog")}
        self.assertEqual(rows["shoot-a"][0], "unreadable")
        self.assertIn("copying the catalog failed", rows["shoot-a"][1])
        self.assertEqual(rows["shoot-b"], ("ok", ""))

    def test_a_wal_file_that_cannot_be_read_marks_that_catalog_and_the_scan_goes_on_issue_35(self):
        a, b = self.make_pair()
        with open(a + "-wal", "wb") as handle:
            handle.write(b"x" * 10)

        def denied_wal(path):
            if path.replace("\\", "/").endswith("shoot-a/main.lrcat-wal"):
                raise PermissionError("denied")
            return os.stat(path)

        def denied_size(path):
            if path.replace("\\", "/").endswith("shoot-a/main.lrcat-wal"):
                raise PermissionError("denied")
            return os.stat(path).st_size

        conn = self.conn()
        # the size is read through `stat`; the getsize patch only fails a read that bypasses the seam
        with mock.patch("os.path.getsize", denied_size):
            ids = self.scan(conn, [self.tmp], stat=denied_wal)
        self.assertEqual(len(ids), 2)
        rows = {os.path.basename(os.path.dirname(s)): (st, reason) for s, st, reason in conn.execute(
            "SELECT source_path, status, reason FROM catalog")}
        self.assertEqual(rows["shoot-a"][0], "unreadable")
        self.assertIn("main.lrcat-wal", rows["shoot-a"][1])
        self.assertEqual(rows["shoot-b"], ("ok", ""))
        self.assertEqual(conn.execute("SELECT status FROM scan").fetchone()[0], "complete")

    def long_copy(self, folder, name):
        """A short synthetic catalog copied to `folder/name`, made and removed through paths.fs_path."""
        seed = self.path("seed-" + folder, "seed.lrcat")
        synth.make_catalog(seed, [{"base": "IMG_0001"}])
        source = os.path.join(self.tmp, folder, name)
        os.makedirs(os.path.dirname(source), exist_ok=True)
        shutil.copyfile(seed, paths.fs_path(source))
        self.addCleanup(shutil.rmtree, paths.fs_path(os.path.join(self.work, "catalogs")), True)
        self.addCleanup(os.remove, paths.fs_path(source))
        return source

    def test_a_snapshot_folder_name_is_cut_to_255_units_and_stays_stable_issue_18(self):
        cases = {248: name_of(248), 249: name_of(249), 255: name_of(255), 252: ASTRAL}
        self.assertEqual(len(ASTRAL), 129)
        for units, name in cases.items():
            with self.subTest(units=units, chars=len(name)):
                self.assertEqual(paths.name_units(name), units)
                source = self.long_copy(f"shoot-{units}", name)
                digest = hashlib.sha1(paths.norm(source).encode("utf-8")).hexdigest()[:12]
                uncut = f"{os.path.splitext(name)[0]}-{digest}"
                if units == 248:
                    self.assertEqual(paths.name_units(uncut), paths.NAME_LIMIT)
                else:
                    self.assertGreater(paths.name_units(uncut), paths.NAME_LIMIT)
                snap = catalogs.snapshot_catalog(source, self.work, NONE_RUNNING)
                folder = os.path.basename(os.path.dirname(snap))
                stem = os.path.splitext(name)[0]
                keep = max(k for k in range(len(stem) + 1)
                           if paths.name_units(f"{stem[:k]}-{digest}") <= paths.NAME_LIMIT)
                if units != 248:
                    self.assertNotEqual(stem[:keep], stem[len(stem) - keep:])  # a cut from the front differs
                if units in (249, 255):
                    self.assertEqual((len(stem) - keep) % 2, 1)  # a cut two at a time misses `keep`
                self.assertEqual(folder, f"{stem[:keep]}-{digest}")
                self.assertLessEqual(paths.name_units(folder), paths.NAME_LIMIT)
                if units == 248:
                    self.assertEqual(folder, uncut)
                with open(paths.fs_path(snap), "rb") as copy, open(paths.fs_path(source), "rb") as original:
                    self.assertEqual(copy.read(), original.read())
                self.assertEqual(catalogs.snapshot_catalog(source, self.work, NONE_RUNNING), snap)

    def test_a_catalog_name_of_247_units_is_read_and_248_to_255_are_unreadable_with_sqlite_reason_issue_18(self):
        sources = {u: self.long_copy(f"shoot-{u}", name_of(u)) for u in (247, 248, 252, 255)}
        unread = tuple(name_of(u) for u in (248, 252, 255))
        self.assertGreater(paths.name_units(name_of(252) + "-wal"), paths.NAME_LIMIT)
        conn = self.conn()
        real_connect = sqlite3.connect

        def connect(path, *args, **kwargs):
            if str(path).endswith(unread):
                raise sqlite3.OperationalError("disk I/O error")
            return real_connect(path, *args, **kwargs)

        with mock.patch("sqlite3.connect", connect):
            self.scan(conn, [os.path.dirname(s) for s in sources.values()])
        rows = {paths.name_units(os.path.basename(r["source_path"])): r for r in conn.execute("SELECT * FROM catalog")}
        self.assertEqual(rows[247]["status"], "ok")
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM record WHERE catalog_id = ?",
                                        (rows[247]["id"],)).fetchone()[0], 0)
        for units in (248, 252, 255):
            with self.subTest(units=units):
                self.assertEqual((rows[units]["status"], rows[units]["has_lock"]), ("unreadable", 0))
                self.assertNotEqual(rows[units]["snapshot_path"], "")
                self.assertTrue(rows[units]["reason"].startswith("SQLite cannot read the copy of the catalog: "))
                self.assertNotIn("the attributes of", rows[units]["reason"])

    def test_lightroom_listed_refuses_before_copy_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        with self.assertRaises(catalogs.LightroomRunning):
            catalogs.snapshot_catalog(source, self.work, lambda: ["lightroom.exe"])
        self.assertFalse(os.path.exists(os.path.join(self.work, "catalogs")))

    def test_unlistable_processes_refuse_the_copy_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])

        def broken():
            raise RuntimeError("no list")

        with self.assertRaises(RuntimeError):
            catalogs.snapshot_catalog(source, self.work, broken)
        self.assertFalse(os.path.exists(os.path.join(self.work, "catalogs")))

    def test_stale_lock_does_not_refuse_and_unclean_rows_are_read_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_unclean(source, [{"base": "IMG_7"}])
        before = os.stat(source)
        conn = self.conn()
        ids = self.scan(conn, [os.path.dirname(source)])
        row = conn.execute("SELECT * FROM catalog WHERE id = ?", (ids[0],)).fetchone()
        self.assertEqual((row["has_lock"], row["status"]), (1, "ok"))
        self.assertEqual((row["size"], row["mtime_ns"]), (before.st_size, before.st_mtime_ns))
        self.assertEqual(row["db_version"], self.read(source).db_version)
        self.assertNotEqual(row["db_version"], "")
        self.assertGreater(row["wal_bytes"], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM record WHERE via = 'absolute'").fetchone()[0], 1)

    def test_source_folder_is_untouched_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_unclean(source, [{"base": "IMG_7"}])
        before = listing(os.path.dirname(source))
        self.read(source)
        self.assertEqual(listing(os.path.dirname(source)), before)

    def test_is_stale_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        st = os.stat(source)
        args = (st.st_size, st.st_mtime_ns)
        self.assertFalse(catalogs.is_stale(source, *args, 0, 0))
        self.assertTrue(catalogs.is_stale(source, st.st_size + 1, st.st_mtime_ns, 0, 0))
        self.assertTrue(catalogs.is_stale(source, st.st_size, st.st_mtime_ns + 1, 0, 0))
        self.assertTrue(catalogs.is_stale(source + "-gone", *args, 0, 0))

        def denied_lock(path):
            if str(path).endswith(".lock"):
                raise PermissionError(path)
            return os.stat(path)

        self.assertTrue(catalogs.is_stale(source, *args, 0, 0, stat=denied_lock), "lock cannot be read")
        for suffix, units, stale in (("-wal", 251, True), ("-wal", 252, False),
                                     (".lock", 250, True), (".lock", 251, False)):
            with self.subTest(suffix=suffix, units=units):
                name = name_of(units)
                self.assertEqual(paths.name_units(name + suffix), 255 if stale else 256)
                long = os.path.join(self.tmp, "b", name)
                self.assertEqual(catalogs.is_stale(long, 7, 11, 0, 0, stat=invalid_for(suffix, long)), stale)

    def test_wal_and_lock_are_read_before_the_copy_rule_6(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        with open(source + "-wal", "wb") as handle:
            handle.write(b"x" * 10)
        real_copy = shutil.copyfile

        def copy_then_write(src, dst, *args, **kwargs):
            result = real_copy(src, dst, *args, **kwargs)
            if str(src).endswith("-wal"):
                with open(source + "-wal", "ab") as handle:
                    handle.write(b"y" * 5)
                with open(source + ".lock", "wb"):
                    pass
            return result

        conn = self.conn()
        with mock.patch("shutil.copyfile", copy_then_write):
            self.scan(conn, [os.path.dirname(source)])
        row = conn.execute("SELECT * FROM catalog").fetchone()
        self.assertEqual((row["wal_bytes"], row["has_lock"]), (10, 0))
        self.assertTrue(catalogs.is_stale(source, row["size"], row["mtime_ns"],
                                          row["wal_bytes"], row["has_lock"]))

    def test_is_stale_counts_a_changed_wal_file_or_a_new_lock(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [])
        st = os.stat(source)
        args = (source, st.st_size, st.st_mtime_ns)

        def write(suffix, size):
            with open(source + suffix, "wb") as handle:
                handle.write(b"x" * size)

        def denied(path):
            if str(path).endswith("-wal"):
                raise PermissionError(path)
            return os.stat(path)

        write("-wal", 10)
        self.assertTrue(catalogs.is_stale(*args, 4, 0), "the -wal file grew")
        self.assertTrue(catalogs.is_stale(*args, 0, 0), "a -wal file appeared")
        self.assertFalse(catalogs.is_stale(*args, 10, 0), "same -wal size")
        self.assertTrue(catalogs.is_stale(*args, 0, 0, stat=denied), "-wal cannot be read")
        os.remove(source + "-wal")
        self.assertTrue(catalogs.is_stale(*args, 10, 0), "the -wal file is gone")
        self.assertFalse(catalogs.is_stale(*args, 0, 0), "no -wal file, none stored")
        write(".lock", 0)
        self.assertTrue(catalogs.is_stale(*args, 0, 0), "a lock appeared")
        self.assertFalse(catalogs.is_stale(*args, 0, 1), "the lock was already there")


class TestStatuses(Tmp):
    def test_four_statuses_in_one_run_rule_5(self):
        synth.make_catalog(self.path("r", "ok.lrcat"), [{"base": "IMG_5"}])
        synth.make_catalog(self.path("r", "partial.lrcat"), [{"base": "IMG_6"}], omit_tables=("Adobe_images",))
        synth.make_empty_shell(self.path("r", "empty.lrcat"))
        synth.make_unreadable(self.path("r", "bad.lrcat"))
        conn = self.conn()
        self.scan(conn, [os.path.join(self.tmp, "r")])
        got = {os.path.basename(r["source_path"]): r["status"] for r in conn.execute("SELECT * FROM catalog")}
        self.assertEqual(got, {"ok.lrcat": "ok", "partial.lrcat": "partial",
                               "empty.lrcat": "empty", "bad.lrcat": "unreadable"})
        reasons = {os.path.basename(r["source_path"]): r["reason"] for r in conn.execute("SELECT * FROM catalog")}
        self.assertEqual({k: v == "" for k, v in reasons.items()},
                         {"ok.lrcat": True, "partial.lrcat": True, "empty.lrcat": True, "bad.lrcat": False})
        stored = [r["path_orig"] for r in conn.execute(
            "SELECT path_orig FROM record JOIN catalog ON catalog.id = catalog_id "
            "WHERE source_path LIKE '%ok.lrcat' AND via = 'absolute'")]
        self.assertEqual(stored, ["c:/shoot-a/IMG_5.arw"])
        partial = conn.execute("SELECT COUNT(*) FROM record JOIN catalog ON catalog.id = catalog_id "
                               "WHERE source_path LIKE '%partial.lrcat'").fetchone()[0]
        self.assertGreater(partial, 0)

    def test_read_error_after_some_records_is_partial_rule_5(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [{"base": "IMG_1"}, {"base": "IMG_2"}])
        con = sqlite3.connect(source)
        con.execute("UPDATE AgLibraryFile SET baseName = CAST(x'ff' AS TEXT) WHERE id_local = 2")
        con.commit()
        con.close()
        got = self.read(source)
        self.assertEqual(got.status, "partial")
        self.assertEqual({os.path.basename(r["path_orig"]) for r in got.records}, {"IMG_1.arw"})

    def test_read_error_before_any_record_is_unreadable_rule_5(self):
        source = self.path("a", "main.lrcat")
        synth.make_catalog(source, [{"base": "IMG_1"}])
        con = sqlite3.connect(source)
        con.execute("UPDATE AgLibraryFile SET baseName = CAST(x'ff' AS TEXT)")
        con.commit()
        con.close()
        self.assertEqual(self.read(source).status, "unreadable")


UNREADABLE_REASONS = {  # case -> a phrase only that case's reason holds
    "placeholder": "is an online-only placeholder",
    "attributes": "main.lrcat-shm cannot be read",
    "attributes_catalog": "main.lrcat cannot be read",
    "snapshot_link": "with links resolved",
    "work_inside": "work folder is inside",
    "copy_failed": "copying the catalog failed",
    "sqlite": "SQLite cannot read",
}
DENIED_TEXT = "denied\nagain"  # an OS error text holding a newline


class TestUnreadableReasons(Tmp):
    def build(self, name):
        """One catalog in its own tree; returns (source, extra arguments for the scan)."""
        home = os.path.join(self.tmp, name, "shoot-a")
        source = self.path(name, "shoot-a", "main.lrcat")
        work = os.path.join(self.tmp, name, "work")
        os.makedirs(work)
        if name == "sqlite":
            synth.make_unreadable(source)
        else:
            synth.make_catalog(source, [])
        extra = {"work_dir": work}

        def denied_stat(ending):
            def stat(path):
                if path.replace("\\", "/").endswith(ending):
                    raise PermissionError(DENIED_TEXT)
                return os.stat(path)
            return stat

        if name == "placeholder":
            extra["stat"] = placeholder_stat(f"{name}/shoot-a/main.lrcat")
        elif name == "attributes":
            extra["stat"] = denied_stat("shoot-a/main.lrcat-shm")
        elif name == "attributes_catalog":
            extra["stat"] = denied_stat("shoot-a/main.lrcat")
        elif name == "work_inside":
            extra["work_dir"] = os.path.join(home, "w")
        elif name == "snapshot_link":
            digest = hashlib.sha1(paths.norm(source).encode("utf-8")).hexdigest()[:12]
            link = os.path.join(work, "catalogs", f"main-{digest}")
            os.makedirs(os.path.dirname(link))
            made = subprocess.run(["cmd", "/c", "mklink", "/J", link, home], capture_output=True, text=True)
            self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
            self.addCleanup(os.rmdir, link)  # the link only, never the target
        return source, extra

    def test_every_path_to_unreadable_records_its_reason_in_the_row_the_csv_and_the_summary(self):
        conn = self.conn()
        sources = {}
        for name in UNREADABLE_REASONS:
            source, extra = self.build(name)
            sources[name] = source
            if name == "copy_failed":
                with mock.patch("shutil.copyfile", side_effect=OSError("disk full")):
                    ids = self.scan(conn, [os.path.dirname(source)], **extra)
            else:
                ids = self.scan(conn, [os.path.dirname(source)], **extra)
            self.assertEqual(len(ids), 1, name)
        rows = {r["source_path"]: r for r in conn.execute("SELECT * FROM catalog")}
        cat_rows = {r["catalog"]: r for r in catalog_views.catalog_inventory_rows(conn, excluded_catalogs=[])}
        sections = {n: [] for n in report.SECTIONS}
        sections[1] = list(cat_rows.values())
        coverage = {"roots": [], "limiting": [], "generated": "g", "scans": [],
                    "catalogs": [{"catalog": s, "status": rows[s]["status"], "reason": rows[s]["reason"],
                                  "limits_coverage": True} for s in sources.values()]}
        out = os.path.join(self.tmp, "out")
        report.write_report(out, sections, coverage, {n: "x" for n in report.SECTIONS})
        with open(os.path.join(out, "summary.md"), encoding="utf-8") as handle:
            summary = handle.read().splitlines()
        with open(os.path.join(out, "01_catalogs.csv"), encoding="utf-8-sig", newline="") as handle:
            csv_rows = {r["catalog"]: r for r in csv.DictReader(handle)}
        self.assertEqual(len({rows[s]["reason"] for s in sources.values()}), len(sources), "distinct reasons")
        for name, source in sources.items():
            with self.subTest(name):
                reason = rows[source]["reason"]
                self.assertEqual(rows[source]["status"], "unreadable")
                self.assertIn(UNREADABLE_REASONS[name], reason)
                self.assertIn("\n", DENIED_TEXT)
                self.assertNotIn("\n", reason)  # one line, though the error text holds a newline
                if name.startswith("attributes"):
                    self.assertIn("denied again", reason)
                for other, phrase in UNREADABLE_REASONS.items():
                    if other != name:
                        self.assertNotIn(phrase, reason)
                self.assertEqual(csv_rows[source]["reason"], reason)
                self.assertEqual(csv_rows[source]["status"], "unreadable")
                self.assertIn(f"- {source}: unreadable (limits coverage): {reason}", summary)


class TestRunningProcesses(unittest.TestCase):
    def test_names_are_lower_case_and_bounded_to_30_seconds_rule_6(self):
        done = subprocess.CompletedProcess(
            [], 0, stdout='"Lightroom.exe","12","Console","1","1 K"\n"System","4","","0","1 K"\n')
        with mock.patch("lrclean.catalogs.subprocess.run", return_value=done) as run:
            self.assertEqual(catalogs.running_processes(), ["lightroom.exe", "system"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_a_list_that_cannot_be_read_raises_rule_6(self):
        empty = subprocess.CompletedProcess([], 0, stdout="")
        for failure in (FileNotFoundError("no tasklist"),
                        subprocess.TimeoutExpired("tasklist", 30),
                        subprocess.CalledProcessError(1, "tasklist"),
                        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte"),
                        empty):
            with self.subTest(failure=type(failure).__name__):
                if isinstance(failure, Exception):
                    patch = mock.patch("lrclean.catalogs.subprocess.run", side_effect=failure)
                else:
                    patch = mock.patch("lrclean.catalogs.subprocess.run", return_value=failure)
                with patch:
                    with self.assertRaises(catalogs.ProcessListUnreadable) as caught:
                        catalogs.running_processes()
                self.assertIn("cannot list processes", str(caught.exception))
                self.assertNotIn("is running", str(caught.exception))

    def test_a_failing_tasklist_is_not_read_as_nothing_running_rule_6(self):
        def fake(*args, **kw):
            if kw.get("check"):
                raise subprocess.CalledProcessError(1, "tasklist")
            return subprocess.CompletedProcess([], 1, stdout='"x.exe","1"\n')
        with mock.patch("lrclean.catalogs.subprocess.run", side_effect=fake):
            with self.assertRaises(catalogs.ProcessListUnreadable) as caught:
                catalogs.running_processes()
        self.assertIn("cannot list processes", str(caught.exception))
        self.assertNotIn("is running", str(caught.exception))


class TestFind(Tmp):
    def recording(self):
        asked = []

        def scandir(path):
            asked.append(paths.norm(path))
            return os.scandir(path)

        return asked, scandir

    def test_pruned_folders_are_not_listed_rule_14(self):
        root = os.path.join(self.tmp, "r")
        for rel in ("own/x.lrcat", "x/y/in.lrcat", "x2/keep.lrcat", "keep/k.lrcat"):
            synth.make_tree(root, {rel: b"c"})
        asked, scandir = self.recording()
        own = os.path.join(root, "OWN").replace("\\", "/")
        found, unlisted = catalogs.find_catalogs(
            root, own, [os.path.join(root, "X").upper()], scandir)
        self.assertEqual(unlisted, [])
        names = [os.path.relpath(p, root).replace("\\", "/") for p in found]
        self.assertEqual(names, ["keep/k.lrcat", "x2/keep.lrcat"])
        banned = {paths.norm(os.path.join(root, d)) for d in ("own", "x", "x/y")}
        self.assertFalse(banned & set(asked))
        self.assertIn(paths.norm(os.path.join(root, "x2")), asked)

    def test_a_directory_link_is_not_entered_rule_14(self):
        root = os.path.join(self.tmp, "r")
        real = os.path.join(root, "real")
        synth.make_catalog(self.path("r", "real", "main.lrcat"), [])
        link = os.path.join(root, "link")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, real], capture_output=True)
        if made.returncode != 0 or not os.path.exists(link):
            self.skipTest("cannot make a junction here")
        found, unlisted = catalogs.find_catalogs(root, "", [])
        self.assertEqual((found, unlisted), ([os.path.join(real, "main.lrcat")], []))

    def test_own_snapshots_inside_root_are_ignored_rule_14(self):
        root = os.path.join(self.tmp, "r")
        own = os.path.join(root, "own")
        synth.make_catalog(self.path("r", "shoot", "main.lrcat"), [])
        conn = self.conn()
        runs = []
        for _ in range(2):
            self.scan(conn, [root], own_dir=own, work_dir=own)
            found, _ = catalogs.find_catalogs(root, own, [])
            runs.append(found)
        self.assertEqual(runs[0], runs[1])
        self.assertTrue(all(paths.norm(own) not in paths.norm(p) for p in runs[0]))
        self.assertEqual(len(runs[0]), 1)

    def test_role_is_live_only_when_given_rule_5(self):
        source = self.path("r", "live", "main.lrcat")
        synth.make_catalog(source, [])
        for given, role in ((None, "shoot"), (source.upper(), "live")):
            with self.subTest(given=given):
                conn = self.conn()
                conn.execute("DELETE FROM catalog")
                self.scan(conn, [os.path.join(self.tmp, "r")], live_catalog=given)
                self.assertEqual(conn.execute("SELECT role FROM catalog").fetchone()[0], role)

    def test_old_state_role(self):
        source = self.path("r", "old", "main.lrcat")
        synth.make_catalog(source, [])
        conn = self.conn()
        self.scan(conn, [os.path.join(self.tmp, "r")], old_states=[source])
        self.assertEqual(conn.execute("SELECT role FROM catalog").fetchone()[0], "old_state")


class TestRootStates(Tmp):
    def test_missing_root_is_not_connected_with_no_rows_rule_5(self):
        conn = self.conn()
        self.scan(conn, [os.path.join(self.tmp, "gone")])
        self.assertEqual(conn.execute("SELECT status, kind FROM scan").fetchone()[:], ("not_connected", "catalogs"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0], 0)

    def test_unlistable_folder_makes_scan_partial_but_keeps_others_rule_5(self):
        root = os.path.join(self.tmp, "r")
        synth.make_catalog(self.path("r", "a", "one.lrcat"), [])
        os.makedirs(os.path.join(root, "locked"))
        bad = paths.norm(os.path.join(root, "locked"))

        def scandir(path):
            if paths.norm(path) == bad:
                raise PermissionError("denied")
            return os.scandir(path)

        found, unlisted = catalogs.find_catalogs(root, "", [], scandir)
        self.assertEqual(len(found), 1)
        self.assertEqual([paths.norm(u) for u in unlisted], [bad])
        conn = self.conn()
        self.scan(conn, [root], scandir=scandir)
        self.assertEqual(conn.execute("SELECT status FROM scan").fetchone()[0], "partial")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0], 1)

    def test_lightroom_running_leaves_scan_partial_and_reraises_rule_5(self):
        source = self.path("r", "a", "one.lrcat")
        synth.make_catalog(source, [])
        with open(source + "-wal", "wb") as handle:
            handle.write(b"x" * 10)
        self.assertTrue(os.path.exists(source + "-wal"))
        asked = []

        def recording(path):
            asked.append(path)
            return os.stat(path)

        def unreadable():
            raise catalogs.ProcessListUnreadable("cannot list processes: x")

        cases = (("running", catalogs.LightroomRunning, lambda: ["lightroom.exe"]),
                 ("unreadable", catalogs.ProcessListUnreadable, unreadable))
        for name, expected, processes in cases:
            with self.subTest(cause=name), contextlib.closing(store.connect(self.path("work", f"{name}.db"))) as conn:
                asked.clear()
                with self.assertRaises(expected) as caught:
                    self.scan(conn, [os.path.join(self.tmp, "r")], processes=processes, stat=recording)
                self.assertIs(type(caught.exception), expected)
                self.assertEqual(conn.execute("SELECT status FROM scan").fetchone()[0], "partial")
                self.assertIn(paths.fs_path(source), asked)
                self.assertFalse([p for p in asked if p.endswith("-wal")])


class TestRootFolders(Tmp):
    def test_root_rows_for_each_via_c35_c41(self):
        source = self.path("cat", "main.lrcat")
        synth.make_catalog(source, [
            {"root": "c:/shoot-a/", "root_relative": "../photos/"},
            {"root": "c:/shoot-b/", "root_relative": None, "base": "IMG_2"},
            {"root": "c:/shoot-c/", "root_relative": "", "base": "IMG_3"}])
        conn = self.conn()
        self.scan(conn, [os.path.dirname(source)])
        stored = [(r["path_norm"], r["path_orig"], r["via"]) for r in conn.execute("SELECT * FROM catalog_root")]
        read = self.read(source).roots
        self.assertEqual(stored, [(r["path_norm"], r["path_orig"], r["via"]) for r in read])
        folder = os.path.dirname(source)
        want = [("c:/shoot-a", "absolute"),
                (paths.norm(os.path.normpath(os.path.join(folder, "..", "photos"))), "relative"),
                ("c:/shoot-b", "absolute"),
                ("c:/shoot-c", "absolute"),
                (paths.norm(folder), "relative")]
        self.assertEqual([(p, v) for p, _o, v in stored], want)
        self.assertFalse([r for r in read if r["path_orig"].endswith(("/", "\\"))])
        relative_records = {r["path_orig"] for r in self.read(source).records if r["via"] == "relative"}
        self.assertFalse([p for p in relative_records if "IMG_2" in p])
        self.assertEqual(len(relative_records), 2)


if __name__ == "__main__":
    unittest.main()
