import contextlib
import csv
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import synth
from lrclean import backup, backup_plan, backup_verify, catalogs, classify, cli, mover_files, mover_journal, mover_purge, store
from mover_world import BATCH, World

NOW = "2026-01-01T00:00:00Z"
KEYS = ("library_roots", "backup_roots", "catalog_roots", "photo_root", "group_depth",
        "exclude", "live_catalog", "old_states", "excluded_catalogs")


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.lib = os.path.join(self.top, "lib")
        self.backup = os.path.join(self.top, "backup")
        self.cats = os.path.join(self.top, "cats")
        self.base = os.path.join(self.lib, "tool")  # inside a scanned root (rule 14)
        self.catalog = os.path.join(self.cats, "shoot-a.lrcat")
        synth.make_tree(self.lib, {"shoot-a/IMG_0001.ARW": b"a" * 100,
                                   "shoot-a/IMG_0002.ARW": b"b" * 100,
                                   "tool/stray/IMG_9999.ARW": b"z"})
        synth.make_tree(self.backup, {"x/IMG_0001.ARW": b"a" * 100})
        os.makedirs(self.cats)
        synth.make_catalog(self.catalog, [{"root": self.lib.replace("\\", "/") + "/",
                                           "folder": "shoot-a/", "base": "IMG_0001"}])
        self.config = {
            "library_roots": [self.lib], "backup_roots": [self.backup],
            "catalog_roots": [self.cats], "photo_root": self.lib, "group_depth": 1,
            "exclude": [], "live_catalog": None, "old_states": [], "excluded_catalogs": [],
        }
        self.write_config(self.config)

    def write_config(self, config):
        os.makedirs(os.path.join(self.base, "work"), exist_ok=True)
        with open(os.path.join(self.base, "work", "config.json"), "w", encoding="utf-8",
                  newline="\n") as handle:
            json.dump(config, handle)

    def run_cli(self, *argv, **injected):
        injected = {"processes": lambda: ["explorer.exe"], "now": lambda: NOW,
                    "attrs_of": lambda path, st: 0, **injected}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--base", self.base, *argv], **injected)
        self.stdout = out.getvalue()
        return code, err.getvalue()

    def rows(self, sql):
        con = sqlite3.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def out_dir(self):
        return os.path.join(self.base, "out")


class ConfigTests(CliCase):
    def files_under_top(self):
        return sorted(os.path.join(d, name) for d, dirs, files in os.walk(self.top) for name in dirs + files)

    def refused(self, name, key, change, named):
        """`catalogs` exits 2 on this configuration, names `key` and each of `named`, and writes nothing."""
        with self.subTest(name):
            self.write_config({**self.config, **change})
            before = self.files_under_top()
            code, err = self.run_cli("catalogs")
            self.assertEqual(code, 2)
            for text in (key, *named):
                self.assertIn(text, err)
            self.assertEqual(self.files_under_top(), before)
            self.assertFalse(os.path.exists(self.out_dir()))

    def test_a_path_spelled_wrongly_is_refused_for_every_key_before_anything_is_written(self):
        os.makedirs(os.path.join(self.cats, "a"))
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.top)
        self.assertTrue(os.path.isdir("cats"))  # the relative path exists, so only the check refuses it
        spellings = {
            "relative path": "cats",
            "drive-relative path": os.path.splitdrive(self.lib)[0] + "shoot-a",
            "doubled trailing separator": self.lib + "\\\\",
            "doubled interior separator": self.lib + "/shoot-a//other",
            "extended path with forward slashes": "\\\\?\\" + self.lib.replace("\\", "/"),
            "doubled separator inside a UNC path": "\\\\server\\share\\\\x",
            "dot segment": self.lib + "/shoot-a/..",
            "lone dot segment": self.lib + "/.",
            "dot segment between backslashes": self.lib + "\\shoot-a\\..\\x",
            "lone dot between backslashes": self.lib + "\\.\\x",
            "tab inside an absolute path": os.path.join(self.lib, "x\ty"),
            "newline inside an absolute path": os.path.join(self.lib, "x\ny"),
            "unit separator inside an absolute path": os.path.join(self.lib, "x\x1fy"),
        }
        controls = ("tab", "newline", "unit separator")
        for name in controls:
            self.assertTrue(os.path.isabs(spellings[f"{name} inside an absolute path"]))
        for key in (*cli.LIST_KEYS, "photo_root", "live_catalog"):
            for name, item in spellings.items():
                change = {key: [item] if key in cli.LIST_KEYS else item}
                fix = ["forward slashes"] if name.startswith(controls) else []
                self.refused(f"{key}: {name}", key, change, [repr(item), *fix])

    def test_a_nested_pair_or_a_shared_folder_is_refused_in_any_spelling_and_names_both_entries(self):
        nested = os.path.join(self.lib, "shoot-a")
        in_backup = os.path.join(self.backup, "x")
        in_cats = os.path.join(self.cats, "a")
        slashed = nested.replace("\\", "/")
        upper = nested.upper()  # the whole child in capitals, so its start differs in case from the parent's
        photo_root = self.base.replace("\\", "/").upper() + "/stray"
        self.assertNotEqual(nested, upper)
        self.assertNotEqual(nested, slashed)
        cases = {
            "library child before parent": ("library_roots", {"library_roots": [nested, self.lib]},
                                            [nested, self.lib]),
            "library parent before child": ("library_roots", {"library_roots": [self.lib, nested]},
                                            [nested, self.lib]),
            "library duplicate": ("library_roots", {"library_roots": [self.lib, self.lib]}, [self.lib]),
            "backup nested": ("backup_roots", {"backup_roots": [self.backup, in_backup]},
                              [self.backup, in_backup]),
            "catalog nested": ("catalog_roots", {"catalog_roots": [self.cats, in_cats]}, [self.cats, in_cats]),
            "backup inside library": ("backup_roots", {"backup_roots": [nested]},
                                      ["library_roots", self.lib, nested]),
            "library inside backup": ("backup_roots", {"library_roots": [in_backup]},
                                      ["library_roots", self.backup, in_backup]),
            "child in another letter case": ("library_roots", {"library_roots": [self.lib, upper]},
                                             [self.lib, upper]),
            "child with forward slashes": ("library_roots", {"library_roots": [self.lib, slashed]},
                                           [self.lib, slashed]),
            "parent with a trailing separator": ("library_roots",
                                                 {"library_roots": [self.lib + os.sep, nested]},
                                                 [self.lib + os.sep, nested]),
            "photo root is the base": ("photo_root", {"photo_root": self.base}, [self.base, "outside"]),
            "photo root inside the base": ("photo_root", {"photo_root": os.path.join(self.base, "stray")},
                                           [os.path.join(self.base, "stray"), self.base, "outside"]),
            "photo root inside the base in another case and slash": (
                "photo_root", {"photo_root": photo_root}, [photo_root, self.base, "outside"]),
        }
        os.makedirs(in_cats)
        for name, (key, change, named) in cases.items():
            self.refused(name, key, change, [repr(text) if os.path.isabs(text) else text for text in named])  # paths as the message spells them

    def test_spellings_and_layouts_that_are_read_rightly_still_load(self):
        path = os.path.join(self.base, "work", "config.json")
        drive = os.path.splitdrive(self.lib)[0] + "\\"
        cases = {
            "forward slashes": {"library_roots": [self.lib.replace("\\", "/")]},
            "UNC prefix": {"catalog_roots": [r"\\server\share\cats"]},
            "extended prefix": {"library_roots": ["\\\\?\\" + self.lib]},
            "trailing separator": {"library_roots": [self.lib + os.sep]},
            "other letter case": {"library_roots": [self.lib.upper()]},
            "catalog root inside a library root": {"catalog_roots": [os.path.join(self.lib, "cats")]},
            "a drive root beside a catalog root inside it": {"library_roots": [drive], "backup_roots": []},
            "sibling roots that share a prefix": {"library_roots": [os.path.join(self.top, "photos"),
                                                                    os.path.join(self.top, "photos2")]},
            "a pair in another case and with forward slashes": {
                "library_roots": [self.lib.upper()], "backup_roots": [self.backup.replace("\\", "/")]},
        }
        self.assertNotEqual(self.lib.upper(), self.lib)
        for name, change in cases.items():
            with self.subTest(name):
                self.write_config({**self.config, **change})
                try:
                    cli.load_config(path)
                except cli.ConfigError as exc:
                    self.fail(f"refused: {exc}")

    def test_every_key_is_required_and_named(self):
        for key in KEYS:
            with self.subTest(key=key):
                self.write_config({k: v for k, v in self.config.items() if k != key})
                code, err = self.run_cli("catalogs")
                self.assertEqual(code, 2)
                self.assertIn(key, err)

    def test_wrong_types_and_bad_files_are_refused(self):
        cases = {
            "string root": ("library_roots", {"library_roots": self.lib}),
            "non-string in a list": ("catalog_roots", {"catalog_roots": [5]}),
            "bool depth": ("group_depth", {"group_depth": True}),
            "zero depth": ("group_depth", {"group_depth": 0}),
            "float depth": ("group_depth", {"group_depth": 1.5}),
            "number catalog": ("live_catalog", {"live_catalog": 5}),
        }
        for name, (key, change) in cases.items():
            with self.subTest(name=name):
                self.write_config({**self.config, **change})
                code, err = self.run_cli("catalogs")
                self.assertEqual(code, 2)
                self.assertIn(key, err)
        path = os.path.join(self.base, "work", "config.json")
        for name, text in (("not json", "not json"), ("not an object", "[]")):
            with self.subTest(name=name):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                self.assertEqual(self.run_cli("catalogs")[0], 2)
        os.remove(path)
        code, err = self.run_cli("catalogs")
        self.assertEqual((code, "config.json" in err), (2, True))


class OrderTests(CliCase):
    def junction(self, link, target):
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(os.rmdir, link)  # the link only, never the target

    def test_issue25_a_linked_work_stops_stage_undo_and_purge_before_the_database_opens_rule_38(self):
        # catalogs on the same layout proves the layout can show a database and that catalogs opens it first
        for argv, opens in ((("stage", "absent.csv", "--approved-sha256", "0" * 64), False),
                            (("purge", BATCH), False), (("undo", BATCH), False), (("catalogs",), True)):
            with self.subTest(argv[0]):
                self.setUp()  # a fresh synthetic tree for each command
                if argv[0] == "stage":
                    argv = (argv[0], os.path.join(self.top, argv[1]), *argv[2:])
                work = os.path.join(self.base, "work")
                shutil.move(os.path.join(work, "config.json"), os.path.join(self.cats, "config.json"))
                shutil.rmtree(work)
                self.junction(work, self.cats)
                names = sorted(os.listdir(self.cats))
                self.assertNotIn("lrclean.sqlite", names)
                code, err = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn(work, err)
                if opens:
                    self.assertIn("lrclean.sqlite", os.listdir(self.cats))
                else:
                    self.assertIn("is a link", err)
                    self.assertEqual(sorted(os.listdir(self.cats)), names)

    def test_a_link_at_work_or_its_snapshot_folders_stops_catalogs_and_the_original_is_untouched_rule_6(self):
        def original_files():
            out = {}
            for suffix in ("", "-wal", "-shm"):
                path = self.catalog + suffix
                with open(path, "rb") as handle:
                    out[suffix] = (os.stat(path).st_size, os.stat(path).st_mtime_ns,
                                   hashlib.sha256(handle.read()).hexdigest())
            return out

        wal, shm = b"W" * 4096, b"M" * 1024
        self.assertNotEqual((len(wal), wal[:1]), (len(shm), shm[:1]))
        # (name, the linked folder under work, whether the catalog folder's own listing is checked)
        for name, linked, listing_checked in (("snapshot folder", ("catalogs", "shoot-a-x"), True),
                                              ("work/catalogs", ("catalogs",), True), ("work", (), False)):
            with self.subTest(name):
                self.setUp()  # a fresh synthetic tree for each layout
                for suffix, data in (("-wal", wal), ("-shm", shm)):
                    with open(self.catalog + suffix, "wb") as handle:
                        handle.write(data)
                work = os.path.join(self.base, "work")
                if not linked:  # the command reads config.json in the catalog's folder
                    shutil.move(os.path.join(work, "config.json"), os.path.join(self.cats, "config.json"))
                    shutil.rmtree(work)
                else:
                    os.makedirs(os.path.join(work, *linked[:-1]), exist_ok=True)
                link = os.path.join(work, *linked)
                self.junction(link, self.cats)
                before, names = original_files(), sorted(os.listdir(self.cats))
                held = sorted(os.listdir(os.path.join(work, "catalogs"))) if listing_checked else None
                code, err = self.run_cli("catalogs")
                self.assertEqual(code, 2)
                self.assertIn(link, err)
                self.assertEqual(original_files(), before)
                if listing_checked:
                    self.assertEqual(sorted(os.listdir(self.cats)), names)
                    self.assertEqual(sorted(os.listdir(os.path.join(work, "catalogs"))), held)

    def test_lightroom_running_refuses_catalogs_and_copies_nothing(self):
        code, err = self.run_cli("catalogs", processes=lambda: ["lightroom.exe"])
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(os.path.join(self.base, "work", "catalogs")))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM scan"), [(0,)])

    def test_steps_out_of_order_exit_with_a_message(self):
        cases = ((("inventory",), "run catalogs first"), (("hash",), "run inventory first"),
                 (("verify", self.lib), "run inventory first"), (("report",), "run inventory first"))
        for argv, message in cases:
            with self.subTest(argv=argv):
                code, err = self.run_cli(*argv)
                self.assertEqual(code, 4)
                self.assertIn(message, err)

    def test_report_after_a_newer_catalogs_scan_writes_nothing(self):
        for step in ("catalogs", "inventory", "catalogs"):
            self.assertEqual(self.run_cli(step)[0], 0)
        code, err = self.run_cli("report")
        self.assertEqual(code, 4)
        self.assertIn("run inventory again", err)
        self.assertFalse(os.path.exists(self.out_dir()))

    def test_report_with_a_library_root_never_scanned_writes_nothing(self):
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        code, _ = self.run_cli("report")
        self.assertEqual(code, 4)
        self.assertFalse(os.path.exists(self.out_dir()))


class MixedInventoryTests(CliCase):
    """`report` compares the latest files scans in walk order: library roots, then backup roots."""

    def setUp(self):
        super().setUp()
        self.lib2 = os.path.join(self.top, "lib2")
        synth.make_tree(self.lib2, {"shoot-b/IMG_0003.ARW": b"c" * 100})
        self.config = {**self.config, "library_roots": [self.lib, self.lib2]}
        self.write_config(self.config)

    def fresh_inventory(self, **injected):
        db = os.path.join(self.base, "work", "lrclean.sqlite")
        if os.path.exists(db):
            os.remove(db)
        for step in ("catalogs", "inventory"):
            self.assertEqual(self.run_cli(step, **injected)[0], 0)

    def rescan(self, *roots):
        """The files scans an interrupted second inventory run leaves: only these roots, in order."""
        conn = store.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
        try:
            for root in roots:
                scan_id = store.begin_scan(conn, root, "files", now=lambda: NOW)
                store.finish_scan(conn, scan_id, "complete", now=lambda: NOW)
        finally:
            conn.close()

    def test_report_refuses_a_root_last_scanned_by_an_earlier_inventory_run(self):
        refused = {
            "the last root is old": ((self.lib, self.lib2), self.backup),
            "only the middle root is old": ((self.lib, self.backup), self.lib2),
            "a library root is old while another was scanned again": ((self.lib,), self.lib2),
            # the third root lies between the first (oldest) and the second (newest)
            "the third root is older than the second but newer than the first": ((self.lib2,), self.backup),
        }
        for name, (rescanned, old) in refused.items():
            with self.subTest(name):
                self.fresh_inventory()
                self.rescan(*rescanned)
                code, err = self.run_cli("report")
                self.assertEqual(code, 4)
                self.assertTrue(err.startswith("run inventory again:"), err)
                self.assertIn(old, err)
                self.assertFalse(os.path.exists(self.out_dir()))

    def test_report_refuses_an_old_root_after_a_root_never_scanned(self):
        never = os.path.join(self.top, "backup2")
        self.fresh_inventory()
        self.write_config({**self.config, "library_roots": [self.lib],
                           "backup_roots": [never, self.backup]})
        self.rescan(self.lib)
        code, err = self.run_cli("report")
        self.assertEqual(code, 4)
        self.assertTrue(err.startswith("run inventory again:"), err)
        self.assertIn(self.backup, err)
        self.assertFalse(os.path.exists(self.out_dir()))

    def test_report_lists_only_the_scans_of_configured_roots(self):
        other = os.path.join(self.top, "other")
        self.fresh_inventory()
        conn = store.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
        try:
            for kind in ("files", "catalogs"):
                store.finish_scan(conn, store.begin_scan(conn, other, kind, now=lambda: NOW),
                                  "complete", now=lambda: NOW)
        finally:
            conn.close()
        self.assertEqual(self.run_cli("report"), (0, ""))
        text = pathlib.Path(self.out_dir(), "summary.md").read_text(encoding="utf-8")
        listed = re.findall(r"^- (\w+) scan (\d+) of (.+?): ", text.split("## Scans")[1], re.M)
        expected = {("catalogs", self.cats), ("files", self.lib), ("files", self.lib2),
                    ("files", self.backup)}
        ids = {(kind, root): str(scan_id) for scan_id, kind, root in
               self.rows("SELECT id, kind, root FROM scan ORDER BY id")}
        self.assertEqual(sorted(listed), sorted((k, ids[(k, r)], r) for k, r in expected))

    def test_report_does_not_refuse_a_root_never_scanned_or_not_connected(self):
        with self.subTest("a backup root never scanned"):
            self.fresh_inventory()
            self.write_config({**self.config, "backup_roots": [self.backup,
                                                               os.path.join(self.top, "backup2")]})
            self.assertEqual(self.run_cli("report"), (0, ""))
        with self.subTest("a backup root not connected during the last inventory"):
            self.write_config(self.config)
            shutil.rmtree(self.backup)
            self.fresh_inventory()
            self.assertEqual(self.run_cli("report"), (0, ""))

    def test_refused_report_gives_the_old_reports_time_and_changes_nothing(self):
        def snapshot():
            return {n: pathlib.Path(self.out_dir(), n).read_bytes() for n in os.listdir(self.out_dir())}

        then = "2026-02-02T03:04:05Z"
        self.fresh_inventory()
        self.assertEqual(self.run_cli("report", now=lambda: then), (0, ""))
        before = snapshot()
        with self.subTest("the catalogs were scanned after the files"):
            self.assertEqual(self.run_cli("catalogs")[0], 0)
            code, err = self.run_cli("report")
            self.assertEqual(code, 4)
            self.assertIn("must not be acted on", err)
            self.assertIn(f"Generated: {then}", err)
            self.assertEqual(snapshot(), before)
        with self.subTest("the inventory was interrupted: a root was last scanned by an earlier run"):
            self.fresh_inventory()
            self.rescan(self.lib)
            before = snapshot()
            code, err = self.run_cli("report")
            self.assertEqual(code, 4)
            self.assertIn("was last scanned by an earlier inventory run", err)
            self.assertIn("must not be acted on", err)
            self.assertIn(f"Generated: {then}", err)
            self.assertEqual(snapshot(), before)
        with self.subTest("a summary without a generation time"):
            path = os.path.join(self.out_dir(), "summary.md")
            text = pathlib.Path(path).read_text(encoding="utf-8")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("".join(line for line in text.splitlines(keepends=True)
                                     if not line.startswith("Generated:")))
            before = snapshot()
            code, err = self.run_cli("report")
            self.assertEqual(code, 4)
            self.assertIn("carries no generation time", err)
            self.assertEqual(snapshot(), before)
        with self.subTest("a summary that is not UTF-8 text"):
            pathlib.Path(self.out_dir(), "summary.md").write_bytes(b"\xff\xfe\xfd\n")
            before = snapshot()
            code, err = self.run_cli("report")
            self.assertEqual(code, 4)
            self.assertTrue(err.startswith("run inventory again:"), err)
            self.assertIn("carries no generation time", err)
            self.assertEqual(snapshot(), before)


class PartialRootTests(CliCase):
    def unlistable(self, *folders):
        real = os.scandir
        bad = {os.path.normcase(folder) for folder in folders}

        def scandir(path):
            if os.path.normcase(os.path.abspath(path)) in bad:
                raise PermissionError(path)
            return real(path)

        return scandir

    def unlistable_shoot_a(self):
        return self.unlistable(os.path.join(self.lib, "shoot-a"))

    def test_inventory_names_the_partial_root_and_folder_and_backup_plan_names_the_cause(self):
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        folder = os.path.join(self.lib, "shoot-a")
        self.write_config({**self.config, "archive_roots": [os.path.join(self.top, "arch1")]})
        self.assertEqual(self.run_cli("inventory", scandir=self.unlistable(folder)), (0, ""))
        partial = f"partial root {self.lib}: could not read {folder}"
        self.assertEqual(self.stdout.splitlines(), ["files scans stored: 2", partial, cli._EXCLUDE_ADVICE])
        code, err = self.run_cli("backup-plan", volume_of=lambda p: p)
        self.assertEqual(code, 4)
        self.assertIn("\n".join(["no library root has a complete files scan.", partial, cli._EXCLUDE_ADVICE]), err)
        self.assertNotIn("run inventory first", err)

    def test_the_exclude_advice_appears_once_when_any_root_named_has_an_unreadable_path(self):
        lib2 = os.path.join(self.top, "lib2")
        synth.make_tree(lib2, {"shoot-c/IMG_0010.ARW": b"c" * 100})
        self.assertNotEqual(lib2, self.lib)
        self.write_config({**self.config, "library_roots": [self.lib, lib2],
                           "archive_roots": [os.path.join(self.top, "arch1")]})
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        for roots in ([self.lib, lib2], [lib2, self.lib]):
            with self.subTest(roots=roots):
                self.write_config({**self.config, "library_roots": roots,
                                   "archive_roots": [os.path.join(self.top, "arch1")]})
                self.assertEqual(self.run_cli("inventory", scandir=self.unlistable(lib2))[0], 0)
                conn = store.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
                try:  # a scan that stopped with no unreadable path, newer than the one that named lib2
                    store.finish_scan(conn, store.begin_scan(conn, self.lib, "files", now=lambda: NOW),
                                      "partial", now=lambda: NOW)
                finally:
                    conn.close()
                code, err = self.run_cli("backup-plan", volume_of=lambda p: p)
                self.assertEqual(code, 4)
                self.assertIn(f"partial root {self.lib}: the scan did not finish. Run inventory again.", err)
                self.assertIn(f"partial root {lib2}: could not read {lib2}", err)
                self.assertEqual(err.count(cli._EXCLUDE_ADVICE), 1)

    def test_every_partial_root_of_the_run_gets_its_own_line(self):
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        a, b = (os.path.join(self.lib, name) for name in ("shoot-a", "shoot-b"))
        os.makedirs(b)
        backup_x = os.path.join(self.backup, "x")
        unlistable = self.unlistable(a, b, backup_x)

        @contextlib.contextmanager
        def scandir(path):
            with unlistable(path) as entries:
                yield sorted(entries, key=lambda entry: entry.name)

        self.assertNotEqual(self.lib, self.backup)
        self.assertEqual(self.run_cli("inventory", scandir=scandir), (0, ""))
        lines = self.stdout.splitlines()
        self.assertEqual(lines[1:3], [f"partial root {self.lib}: could not read {a}; {b}",
                                      f"partial root {self.backup}: could not read {backup_x}"])
        self.assertEqual(len(lines), 4)  # the count, the two roots, the advice once

    def test_a_file_that_cannot_be_read_is_cured_by_excluding_its_folder_not_the_file(self):
        folder = os.path.join(self.lib, "shoot-a")
        unreadable = os.path.join(folder, "IMG_0002.ARW")
        real = os.stat

        def stat(path, *args, **kwargs):
            if str(path).endswith("IMG_0002.ARW"):
                raise PermissionError(path)
            return real(path, *args, **kwargs)

        self.assertEqual(self.run_cli("catalogs")[0], 0)
        for excluded, partial in (([], True), ([unreadable], True), ([folder], False)):
            with self.subTest(exclude=excluded):
                self.write_config({**self.config, "exclude": excluded})
                with mock.patch.object(os, "stat", stat):
                    self.assertEqual(self.run_cli("inventory"), (0, ""))
                lines = self.stdout.splitlines()
                self.assertEqual(lines[0], "files scans stored: 2")
                if partial:
                    self.assertEqual(lines[1], f"partial root {self.lib}: could not read {unreadable}")
                    self.assertIn("for a file in a subfolder, the folder that holds it", lines[2])
                self.assertEqual(len(lines), 3 if partial else 1)  # a clean inventory has no advice line

    def test_verify_under_a_partial_root_names_it_and_advises_exclude_only_for_an_unreadable_path(self):
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        shoot_a = os.path.join(self.lib, "shoot-a")
        for root, folder in ((self.lib, shoot_a), (self.lib, self.lib), (shoot_a, self.lib)):
            with self.subTest(root=root, folder=folder):
                self.write_config({**self.config, "library_roots": [root]})
                self.assertEqual(self.run_cli("inventory", scandir=self.unlistable(shoot_a))[0], 0)
                code, err = self.run_cli("verify", folder)
                self.assertEqual(code, 4)
                self.assertTrue(err.startswith(f"verify stopped: no raw file under {folder} is in the latest "
                                               "files scans.\n"), err)
                self.assertIn(f"partial root {root}: could not read {shoot_a}", err)
                self.assertIn(cli._EXCLUDE_ADVICE, err)
                self.assertNotIn("run inventory first", err)

        self.write_config(self.config)
        for status in ("partial", "running"):
            with self.subTest(status=status):
                conn = store.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
                try:
                    scan_id = store.begin_scan(conn, self.lib, "files", now=lambda: NOW)
                    if status == "partial":
                        store.finish_scan(conn, scan_id, status, now=lambda: NOW)
                finally:
                    conn.close()
                code, err = self.run_cli("verify", shoot_a)
                self.assertEqual(code, 4)
                self.assertIn(f"partial root {self.lib}: the scan did not finish. Run inventory again.", err)
                self.assertNotIn("could not read", err)
                self.assertNotIn("exclude", err)

    def test_verify_of_a_folder_under_a_complete_root_names_no_partial_root(self):
        lib2 = os.path.join(self.top, "lib2")
        synth.make_tree(lib2, {"shoot-c/IMG_0010.ARW": b"c" * 100})
        empty = os.path.join(lib2, "empty")
        os.makedirs(empty)
        self.assertNotEqual(lib2, self.lib)
        self.write_config({**self.config, "library_roots": [self.lib, lib2]})
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        self.assertEqual(self.run_cli("inventory", scandir=self.unlistable_shoot_a())[0], 0)
        self.assertIn(f"partial root {self.lib}", self.stdout)  # precondition: one root is partial
        code, err = self.run_cli("verify", empty)
        self.assertEqual(code, 4)
        self.assertIn("run inventory first", err)
        self.assertNotIn("partial root", err)

    def test_a_backup_root_that_is_not_connected_is_not_reported_as_a_partial_root(self):
        shutil.rmtree(self.backup)
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        self.assertEqual(self.run_cli("inventory"), (0, ""))
        self.assertEqual(self.stdout.splitlines(), ["files scans stored: 2"])
        self.assertEqual(self.rows("SELECT status FROM scan WHERE kind = 'files' AND root = "
                                   f"'{self.backup}'"), [("not_connected",)])

    def test_a_file_directly_in_a_root_is_not_cured_by_excluding_the_root_and_the_advice_says_so(self):
        real = os.stat

        def stat(path, *args, **kwargs):
            if os.path.basename(path) == "IMG_0003.ARW":
                raise PermissionError(path)
            return real(path, *args, **kwargs)

        self.assertEqual(self.run_cli("catalogs")[0], 0)
        for root in (self.lib, self.backup):
            synth.make_tree(root, {"IMG_0003.ARW": b"c" * 100})
            self.write_config({**self.config, "exclude": [root]})
            for kind in ("file", "unlistable"):
                with self.subTest(root=os.path.basename(root), kind=kind):
                    if kind == "file":
                        with mock.patch.object(os, "stat", stat):
                            self.assertEqual(self.run_cli("inventory"), (0, ""))
                        unreadable = os.path.join(root, "IMG_0003.ARW")
                    else:
                        self.assertEqual(self.run_cli("inventory", scandir=self.unlistable(root)), (0, ""))
                        unreadable = root
                    self.assertEqual(self.stdout.splitlines(), [
                        "files scans stored: 2", f"partial root {root}: could not read {unreadable}",
                        cli._EXCLUDE_ADVICE,
                    ])
            os.remove(os.path.join(root, "IMG_0003.ARW"))

    def test_backup_plan_for_a_root_with_no_files_scan_still_says_run_inventory_first(self):
        self.write_config({**self.config, "archive_roots": [os.path.join(self.top, "arch1")]})
        code, err = self.run_cli("backup-plan", volume_of=lambda p: p)
        self.assertEqual(code, 4)
        self.assertIn("run inventory first", err)


class VerifyFolderTests(CliCase):
    def test_any_spelling_of_the_folder_finds_the_same_files_and_an_unknown_one_exits_four(self):
        synth.make_tree(self.lib, {"shoot-b/IMG_0003.ARW": b"n" * 100})  # a file with no copy
        chunk = 64 * 1024  # a sample reads the first and last chunk only: only the middle differs
        synth.make_tree(self.lib, {"shoot-a/IMG_0004.ARW": b"m" * chunk + b"p" * chunk + b"m" * chunk})
        synth.make_tree(self.backup, {"x/IMG_0004.ARW": b"m" * chunk + b"q" * chunk + b"m" * chunk})
        for step in ("catalogs", "inventory", "hash"):
            self.assertEqual(self.run_cli(step)[0], 0)
        shoot_a = os.path.join(self.lib, "shoot-a")
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.lib)
        spellings = ("shoot-a", os.path.join("shoot-b", "..", "shoot-a"), shoot_a)
        results = []
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                self.assertEqual(self.run_cli("verify", spelling), (0, ""))
                results.append(self.stdout)
        self.assertEqual(self.run_cli("verify", self.lib), (0, ""))
        whole = self.stdout
        self.assertNotEqual(results[2], whole)  # a count that ignored the folder would differ
        self.assertIn("mismatch=1", results[2])
        self.assertEqual(results[0], results[2])
        self.assertEqual(results[1], results[2])
        code, err = self.run_cli("verify", "no-such-folder")
        self.assertEqual(code, 4)
        self.assertIn("run inventory first", err)
        self.assertIn(os.path.join(self.lib, "no-such-folder"), err)

    def test_a_folder_that_holds_no_raw_file_exits_four(self):
        synth.make_tree(self.lib, {"jpg-only/IMG_0005.JPG": b"j", "xmp-only/IMG_0006.xmp": b"x"})
        for step in ("catalogs", "inventory", "hash"):
            self.assertEqual(self.run_cli(step)[0], 0)
        self.assertEqual(self.run_cli("verify", os.path.join(self.lib, "shoot-a"))[0], 0)  # the control
        self.assertFalse(os.path.exists(os.path.join(self.lib, "shoot")))
        for name in ("shoot", "jpg-only", "xmp-only"):  # a name that only starts like shoot-a, a JPG, a sidecar
            with self.subTest(folder=name):
                code, err = self.run_cli("verify", os.path.join(self.lib, name))
                self.assertEqual(code, 4)
                self.assertIn(os.path.join(self.lib, name), err)


class WiringTests(CliCase):
    def run_all(self, **injected):
        for step in ("catalogs", "inventory", "hash", "report"):
            code, err = self.run_cli(step, **injected)
            self.assertEqual((step, code, err), (step, 0, ""))

    def test_full_run_writes_eleven_files_and_summary(self):
        self.run_all()
        names = sorted(os.listdir(self.out_dir()))
        self.assertEqual(len(names), 12)
        self.assertIn("summary.md", names)
        self.assertEqual(self.run_cli("verify", self.lib)[0], 0)

    def test_base_inside_a_scanned_root_is_not_walked(self):
        self.run_all()
        under = self.rows("SELECT path_orig FROM file WHERE path_norm LIKE '%/tool/%'")
        self.assertEqual(under, [])
        self.assertTrue(self.rows("SELECT 1 FROM file WHERE path_norm LIKE '%/shoot-a/%'"))

    def test_owner_excluded_stale_catalog_does_not_limit_coverage(self):
        gone = types.SimpleNamespace(st_size=-1, st_mtime_ns=-1)

        def stat(path, **kwargs):
            return gone if str(path).endswith(".lrcat") else os.stat(path, **kwargs)

        for excluded, expected in (([], True), ([self.catalog], False)):
            with self.subTest(excluded=excluded):
                self.write_config({**self.config, "excluded_catalogs": excluded})
                self.run_all(stat=stat)
                with open(os.path.join(self.out_dir(), "summary.md"), encoding="utf-8") as handle:
                    self.assertEqual("(limits coverage)" in handle.read(), expected)
        bad = os.path.join(self.cats, "bad.lrcat")
        synth.make_unreadable(bad)
        for excluded, expected in (([self.catalog], True), ([self.catalog, bad], False)):
            with self.subTest(unreadable_excluded=bad in excluded):
                self.write_config({**self.config, "excluded_catalogs": excluded})
                self.run_all()
                with open(os.path.join(self.out_dir(), "summary.md"), encoding="utf-8") as handle:
                    self.assertEqual("(limits coverage)" in handle.read(), expected)

    def test_catalog_whose_wal_file_or_lock_changed_since_its_snapshot_limits_coverage(self):
        wal, lock = self.catalog + "-wal", self.catalog + ".lock"

        def write(path, size):
            with open(path, "wb") as handle:
                handle.write(b"x" * size)

        write(wal, 10)  # present at the snapshot, so the stored wal_bytes is 10
        for step in ("catalogs", "inventory", "hash"):
            self.assertEqual(self.run_cli(step)[0], 0)

        def limited():
            self.assertEqual(self.run_cli("report"), (0, ""))
            with open(os.path.join(self.out_dir(), "summary.md"), encoding="utf-8") as handle:
                return "(limits coverage)" in handle.read()

        with self.subTest("unchanged"):
            self.assertFalse(limited())
        with self.subTest("a .lock file appeared"):
            write(lock, 0)
            self.assertTrue(limited())
            os.remove(lock)
        with self.subTest("the -wal file grew"):
            write(wal, 20)
            self.assertTrue(limited())
        with self.subTest("a .lock file already there at the snapshot"):
            write(lock, 0)
            for step in ("catalogs", "inventory", "hash"):
                self.assertEqual(self.run_cli(step)[0], 0)
            self.assertFalse(limited())

    def test_catalog_that_appeared_after_the_catalogs_scan_limits_coverage(self):
        # IMG_0002 is a byte-identical unrecorded twin of the recorded IMG_0001: a safe candidate
        # until a catalog saved after the catalogs step records it
        with open(os.path.join(self.lib, "shoot-a", "IMG_0002.ARW"), "wb") as handle:
            handle.write(b"a" * 100)
        os.makedirs(os.path.join(self.cats, "sub"))
        for step in ("catalogs", "inventory", "hash"):
            self.assertEqual(self.run_cli(step)[0], 0)
        late = os.path.join(self.cats, "late.lrcat")
        root = self.lib.replace("\\", "/") + "/"

        def unlistable(path):
            if str(path).rstrip("/\\").endswith("sub"):
                raise PermissionError(path)
            return os.scandir(path)

        def reported(**injected):
            self.assertEqual(self.run_cli("report", **injected), (0, ""))
            with open(os.path.join(self.out_dir(), "summary.md"), encoding="utf-8") as handle:
                summary = handle.read()
            with open(os.path.join(self.out_dir(), "03_safe_candidates.csv"),
                      encoding="utf-8-sig", newline="") as handle:
                safe = len(list(csv.reader(handle))) - 1
            with open(os.path.join(self.out_dir(), "11_not_read.csv"),
                      encoding="utf-8-sig", newline="") as handle:
                items = [r["item"] for r in csv.DictReader(handle)]
            return safe, summary, items

        sub = f"{os.path.join(self.cats, 'sub')} (appeared since the catalogs scan)"
        late_cause = f"{late} (appeared since the catalogs scan)"
        with self.subTest("no new catalog"):
            self.assertEqual(reported()[0], 1)
        with self.subTest("a new catalog that is excluded does not limit coverage"):
            synth.make_catalog(late, [{"root": root, "folder": "shoot-a/", "base": "IMG_0002"}])
            self.write_config({**self.config, "excluded_catalogs": [late]})
            safe, summary, items = reported()
            self.assertEqual((safe, "appeared since" in summary, items), (1, False, []))
        with self.subTest("a new catalog limits coverage and holds back the file"):
            self.write_config(self.config)
            safe, summary, items = reported()
            self.assertEqual(safe, 0)
            self.assertIn(f"- {late_cause}", summary)
            self.assertEqual(items, [late_cause])
        with self.subTest("a folder that cannot be listed now"):
            os.remove(late)
            safe, summary, items = reported(scandir=unlistable)
            self.assertEqual(safe, 0)
            self.assertIn(f"- {sub}", summary)
            self.assertEqual(items, [sub])

    def test_catalogs_prints_how_many_are_unreadable(self):
        synth.make_unreadable(os.path.join(self.cats, "bad.lrcat"))
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        self.assertIn("(1 unreadable or partial)", self.stdout)

    def test_catalogs_prints_each_catalog_unreadable_in_this_run_and_why_and_none_from_an_earlier_run(self):
        earlier = os.path.join(self.cats, "bad.lrcat")
        synth.make_unreadable(earlier)
        other = os.path.join(self.top, "other")
        os.makedirs(other)
        self.write_config({**self.config, "catalog_roots": [self.cats, other]})
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        first = self.stdout.splitlines()
        self.assertEqual(len(first), 2)
        self.assertTrue(first[1].startswith(f"unreadable: {earlier}: SQLite cannot read"))

        self.write_config({**self.config, "catalog_roots": [other]})
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        self.assertNotIn("unreadable:", self.stdout)

        self.write_config(self.config)
        os.remove(earlier)  # gone in the second run, which holds two unreadable catalogs for different reasons
        garbage, unlucky = os.path.join(self.cats, "ugly.lrcat"), os.path.join(self.cats, "worse.lrcat")
        synth.make_unreadable(garbage)
        synth.make_unreadable(unlucky)
        copy = shutil.copyfile

        def copy_but_not_worse(source, target, **kwargs):
            if os.path.basename(source) == "worse.lrcat":
                raise OSError("disk full")
            return copy(source, target, **kwargs)

        with mock.patch("shutil.copyfile", side_effect=copy_but_not_worse):
            self.assertEqual(self.run_cli("catalogs")[0], 0)
        header, *printed = self.stdout.splitlines()
        self.assertEqual(header, "catalogs stored: 3 (2 unreadable or partial)")
        self.assertEqual(len(printed), 2)
        self.assertTrue(printed[0].startswith(f"unreadable: {garbage}: SQLite cannot read"), printed[0])
        self.assertEqual(printed[1], f"unreadable: {unlucky}: copying the catalog failed: disk full")

    def test_report_summary_holds_the_reason_a_catalog_was_unreadable(self):
        bad = os.path.join(self.cats, "bad.lrcat")
        synth.make_unreadable(bad)
        for step in ("catalogs", "inventory", "hash"):
            self.assertEqual(self.run_cli(step)[0], 0)
        (reason,) = self.rows("SELECT reason FROM catalog WHERE status = 'unreadable'")[0]
        self.assertTrue(reason)
        self.assertEqual(self.run_cli("report"), (0, ""))
        with open(os.path.join(self.out_dir(), "summary.md"), encoding="utf-8") as handle:
            self.assertIn(f"unreadable (limits coverage): {reason}", handle.read())

    def test_stale_or_owner_excluded_catalog_blocks_safe_candidates(self):
        # a byte-identical unrecorded twin of the recorded IMG_0001.ARW
        with open(os.path.join(self.lib, "shoot-a", "IMG_0002.ARW"), "wb") as handle:
            handle.write(b"a" * 100)
        gone = types.SimpleNamespace(st_size=-1, st_mtime_ns=-1)

        def stale(path, **kwargs):
            return gone if str(path).endswith(".lrcat") else os.stat(path, **kwargs)

        cases = (("fresh", {}, os.stat, 1),
                 ("stale catalog (rule 3/13, C-26)", {}, stale, 0),
                 ("owner-excluded catalog records are ignored (C-45)",
                  {"excluded_catalogs": [self.catalog]}, os.stat, 0))
        for name, change, stat, expected in cases:
            with self.subTest(name):
                self.write_config({**self.config, **change})
                self.run_all(stat=stat)
                path = os.path.join(self.out_dir(), "03_safe_candidates.csv")
                with open(path, encoding="utf-8-sig", newline="") as handle:
                    self.assertEqual(len(list(csv.reader(handle))) - 1, expected)

    def test_owner_excluded_catalog_does_not_make_a_pair_an_own_photo_pair(self):
        # IMG_0001.JPG is catalogued as its own photo next to IMG_0001.ARW (C-56)
        root = self.lib.replace("\\", "/") + "/"
        with open(os.path.join(self.lib, "shoot-a", "IMG_0001.JPG"), "wb") as handle:
            handle.write(b"j" * 10)
        os.remove(self.catalog)
        synth.make_catalog(self.catalog, [{"root": root, "folder": "shoot-a/", "base": "IMG_0001"},
                                          {"root": root, "folder": "shoot-a/", "base": "IMG_0001",
                                           "ext": "JPG"}])
        for excluded, expected in (([], "1"), ([self.catalog], "0")):
            with self.subTest(excluded=excluded):
                self.write_config({**self.config, "excluded_catalogs": excluded})
                self.run_all()
                path = os.path.join(self.out_dir(), "06_raw_jpeg_pairs.csv")
                with open(path, encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual([r["own_photo_pairs"] for r in rows], [expected])

    def test_pair_rows_say_coverage_is_incomplete_when_a_catalog_is_unreadable(self):
        with open(os.path.join(self.lib, "shoot-a", "IMG_0001.JPG"), "wb") as handle:
            handle.write(b"j" * 10)
        bad = os.path.join(self.cats, "bad.lrcat")
        synth.make_unreadable(bad)
        for excluded, expected in (([], True), ([bad], False)):
            with self.subTest(unreadable_excluded=bool(excluded)):
                self.write_config({**self.config, "excluded_catalogs": excluded})
                self.run_all()
                path = os.path.join(self.out_dir(), "06_raw_jpeg_pairs.csv")
                with open(path, encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(len(rows), 1)
                self.assertEqual(classify.NOT_RECORDED in rows[0]["how_decided"], expected)

    def test_base_that_is_a_root_is_refused_before_anything_is_created(self):
        original_base = self.base
        self.addCleanup(setattr, self, "base", original_base)
        stray = os.path.join(original_base, "stray")
        shoot_a = os.path.join(self.lib, "shoot-a")
        backup_x = os.path.join(self.backup, "x")
        for base, key, change, root in (
                (self.lib, "library_roots", {}, self.lib),
                (self.backup, "backup_roots", {}, self.backup),
                (self.cats, "catalog_roots", {}, self.cats),
                (original_base, "catalog_roots", {"catalog_roots": [stray]}, stray),
                (self.lib, "library_roots", {"library_roots": [shoot_a]}, shoot_a),
                (self.backup, "backup_roots", {"backup_roots": [backup_x]}, backup_x)):
            with self.subTest(base=base, key=key, root=root):
                self.base = base
                self.write_config({**self.config, **change})
                for step in ("catalogs", "report"):
                    code, err = self.run_cli(step)
                    self.assertEqual(code, 2)
                    self.assertIn(f"{key}: root {root} ", err)
                self.assertEqual(sorted(os.listdir(os.path.join(base, "work"))), ["config.json"])
                self.assertFalse(os.path.exists(self.out_dir()))

        self.base = self.lib
        spellings = {"trailing separator": self.lib + os.sep}
        if os.name == "nt":
            spellings["other case"] = self.lib.upper()
            spellings["forward slashes"] = self.lib.replace("\\", "/")
        for name, spelled in spellings.items():
            with self.subTest(name):
                self.write_config({**self.config, "library_roots": [spelled]})
                code, err = self.run_cli("catalogs")
                self.assertEqual(code, 2)
                self.assertIn("library_roots", err)
                self.assertIn(spelled, err)
                self.assertFalse(os.path.exists(self.out_dir()))


class BackupPlanTests(CliCase):
    def setUp(self):
        super().setUp()
        self.arch1 = os.path.join(self.top, "arch1")
        self.arch2 = os.path.join(self.top, "arch2")
        self.volumes = {self.arch1: "x:", self.arch2: "y:"}
        self.write_config({**self.config, "archive_roots": [self.arch1, self.arch2]})

    def plan(self, **injected):
        injected = {"volume_of": lambda p: self.volumes.get(p, "z:"), **injected}
        return self.run_cli("backup-plan", **injected)

    def listing(self, root):
        return sorted((d, tuple(sorted(f))) for d, _, f in os.walk(root))

    def test_issue25_a_missing_archive_drive_is_refused_before_the_database_opens(self):
        missing = "Q:\\arch2"
        self.assertNotEqual(os.path.splitdrive(os.path.abspath(self.arch1))[0] + "\\", "Q:\\")
        self.volumes = {self.arch1: "x:", missing: "y:"}
        self.write_config({**self.config, "archive_roots": [self.arch1, missing]})
        db = os.path.join(self.base, "work", "lrclean.sqlite")
        self.assertFalse(os.path.exists(db))
        code, err = self.plan(exists=lambda p: p != "Q:\\")
        self.assertEqual(code, 2)
        self.assertIn("not connected", err)
        self.assertIn("Q:", err)
        self.assertFalse(os.path.exists(db))
        self.assertFalse(os.path.exists(self.out_dir()))

    def test_config_without_archive_roots_still_runs_phase_a(self):
        self.write_config(self.config)
        self.assertEqual(self.run_cli("catalogs")[0], 0)

    def test_wrong_archive_roots_type_is_refused_for_every_subcommand(self):
        for bad in (self.arch1, [5]):
            for argv in (("catalogs",), ("inventory",), ("backup-plan",), ("verify", self.lib)):
                with self.subTest(bad=bad, argv=argv):
                    self.write_config({**self.config, "archive_roots": bad})
                    code, err = self.run_cli(*argv)
                    self.assertEqual(code, 2)
                    self.assertIn("archive_roots", err)

    def test_bad_archive_roots_are_refused_and_create_nothing(self):
        cases = {
            "missing key": self.config,
            "inside a library root": {**self.config, "archive_roots": [os.path.join(self.lib, "a")]},
            "two roots on one volume": {**self.config, "archive_roots": [self.arch1, self.arch2]},
        }
        for name, config in cases.items():
            with self.subTest(name):
                self.write_config(config)
                code, err = self.plan(volume_of=lambda p: "x:")
                self.assertEqual(code, 2)
                self.assertIn("archive_roots", err)
                self.assertFalse(os.path.exists(self.out_dir()))
                self.assertFalse(os.path.exists(self.arch1))

    def test_archive_root_inside_the_tool_folder_is_refused_by_the_own_folder_check(self):
        # a base folder outside every library root: only the own-folder check can refuse
        self.base = os.path.join(self.top, "tool-elsewhere")
        self.write_config({**self.config, "archive_roots": [os.path.join(self.base, "a")]})
        code, err = self.plan(volume_of=lambda p: "x:")
        self.assertEqual(code, 2)
        self.assertIn("archive_roots", err)
        self.assertFalse(os.path.exists(self.out_dir()))

    def test_injected_stat_attrs_of_and_free_bytes_reach_the_planner(self):
        for step in ("catalogs", "inventory"):
            self.assertEqual(self.run_cli(step)[0], 0)

        def stat(path, **kwargs):
            return types.SimpleNamespace(st_size=7, st_mtime_ns=5)

        def attrs_of(path, st):
            return 0x1000 if str(path).endswith("IMG_0002.ARW") else 0

        code, err = self.plan(stat=stat, attrs_of=attrs_of, free_bytes=lambda root: 3)
        self.assertEqual((code, err), (0, ""))
        rows = backup_plan.read_manifest(os.path.join(self.out_dir(), "backup-manifest-1.csv"))
        self.assertEqual([(os.path.basename(r.source), r.bytes, r.mtime_ns, r.action)
                          for r in rows],
                         [("IMG_0001.ARW", 7, 5, "copy"), ("IMG_0002.ARW", 7, 5, "skip_placeholder")])
        with open(os.path.join(self.out_dir(), "backup-summary.md"), encoding="utf-8") as handle:
            summary = handle.read()
        self.assertIn("- files to copy: 1; placeholders skipped, apart from them: 1", summary)
        self.assertIn("- free space: 3", summary)
        self.assertIn("- NOT ENOUGH FREE SPACE: needs 4 more bytes", summary)

    def test_plan_writes_numbered_manifests_and_summary_only_in_out(self):
        for step in ("catalogs", "inventory"):
            self.assertEqual(self.run_cli(step)[0], 0)
        before = self.listing(self.lib)
        code, err = self.plan()
        self.assertEqual((code, err), (0, ""))
        names = sorted(os.listdir(self.out_dir()))
        self.assertEqual(names, ["backup-manifest-1.csv", "backup-manifest-2.csv",
                                 "backup-summary.md"])
        with open(os.path.join(self.out_dir(), "backup-manifest-2.csv"), encoding="utf-8-sig",
                  newline="") as handle:
            dests = [row["dest"] for row in csv.DictReader(handle)]
        self.assertTrue(dests and all(d.startswith(self.arch2) for d in dests))
        first = os.path.join(self.out_dir(), "backup-manifest-1.csv")
        self.assertIn(f"{first}: folders=1 files=2 bytes=200", self.stdout.splitlines())
        self.assertFalse(os.path.exists(self.arch1))
        self.assertFalse(os.path.exists(self.arch2))
        after = [e for e in self.listing(self.lib) if not e[0].startswith(self.base)]
        self.assertEqual(after, [e for e in before if not e[0].startswith(self.base)])

    def summary(self):
        with open(os.path.join(self.out_dir(), "backup-summary.md"), encoding="utf-8") as handle:
            return handle.read()

    def test_summary_tallies_unselected_folders_by_count_then_extension(self):
        synth.make_tree(self.lib, {
            "notes/a.txt": b"a", "notes/b.txt": b"b", "notes/c.xmp": b"c",
            "more/d.zip": b"d", "more/noext": b"e"})
        for step in ("catalogs", "inventory"):
            self.assertEqual(self.run_cli(step)[0], 0)
        self.assertEqual(self.plan()[0], 0)
        text = self.summary()
        self.assertIn("- folders not selected: 2\n", text)
        tally = text.split("- folders not selected: 2\n")[1].split("\n\n")[0]
        self.assertEqual(tally.splitlines(),
                         ["  - txt: 2", "  - (none): 1", "  - xmp: 1", "  - zip: 1"])

    def test_exclude_from_the_configuration_keeps_a_folder_out_of_the_walk(self):
        synth.make_tree(self.lib, {"skipped/IMG_0003.JPG": b"j", "kept/IMG_0004.JPG": b"k"})
        self.write_config({**self.config, "archive_roots": [self.arch1, self.arch2],
                           "exclude": [os.path.join(self.lib, "skipped")]})
        for step in ("catalogs", "inventory"):
            self.assertEqual(self.run_cli(step)[0], 0)
        self.assertEqual(self.plan()[0], 0)
        rows = backup_plan.read_manifest(os.path.join(self.out_dir(), "backup-manifest-1.csv"))
        names = sorted(os.path.basename(r.source) for r in rows)
        self.assertEqual(names, ["IMG_0001.ARW", "IMG_0002.ARW", "IMG_0004.JPG"])

    def test_a_folder_that_cannot_be_listed_opens_the_summary_and_the_plan_still_exits_zero(self):
        for step in ("catalogs", "inventory"):
            self.assertEqual(self.run_cli(step)[0], 0)
        bad = os.path.join(self.lib, "shoot-a")
        real = os.scandir

        def scandir(path):
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(bad):
                raise PermissionError(path)
            return real(path)

        original = backup_plan.plan_manifests
        with mock.patch.object(backup_plan, "plan_manifests",
                               lambda *a, **kw: original(*a, scandir=scandir, **kw)):
            code, err = self.plan()
        self.assertEqual((code, err), (0, ""))
        text = self.summary()
        self.assertEqual(text.splitlines()[2], "## NOT READ: folders that could not be listed")
        self.assertIn(f"- {bad}", text)
        self.assertEqual(len(self.stdout.splitlines()), 2)

    def test_a_second_plan_and_apply_changes_nothing_in_the_archive_root(self):
        for step in ("catalogs", "inventory"):
            self.assertEqual(self.run_cli(step)[0], 0)
        manifest = os.path.join(self.out_dir(), "backup-manifest-1.csv")
        volume_of = lambda p: self.volumes.get(p, "z:")
        states = []
        for _ in range(2):
            self.assertEqual(self.plan()[0], 0)
            code, err = self.run_cli("backup-run", manifest, "--apply", volume_of=volume_of,
                                     clock=lambda: 1.0, abort=lambda code: self.fail("stall abort"))
            self.assertEqual((code, err), (0, ""))
            states.append(dict(pair.split("=") for pair in self.stdout.splitlines()[-1].split(", ")))
            if len(states) == 1:
                before = self.archive_listing()
        self.assertEqual(self.archive_listing(), before)
        self.assertEqual(states[0]["verified"], "2")
        self.assertEqual({k: v for k, v in states[1].items() if v != "0"},
                         {"verified": "2"})
        self.assertFalse([n for _, _, files in os.walk(self.arch1) for n in files
                          if n.endswith(".lrclean-part")])

    def archive_listing(self):
        found = []
        for folder, _, files in os.walk(self.arch1):
            for name in files:
                st = os.stat(os.path.join(folder, name))
                found.append((os.path.join(folder, name), st.st_size, st.st_mtime_ns))
        return sorted(found)

    def test_plan_without_a_files_scan_exits_four(self):
        code, err = self.plan()
        self.assertEqual(code, 4)
        self.assertIn("files scan", err)
        self.assertFalse(os.path.exists(self.out_dir()))


class BackupCase(CliCase):
    def setUp(self):
        super().setUp()
        self.arch1 = os.path.join(self.top, "arch1")
        self.arch2 = os.path.join(self.top, "arch2")
        self.write_config({**self.config, "archive_roots": [self.arch1, self.arch2]})
        self.manifest = os.path.join(self.top, "manifest.csv")
        self.write_manifest(["IMG_0001.ARW", "IMG_0002.ARW"])
        self.sqlite = os.path.join(self.base, "work", "lrclean.sqlite")
        self.journal_dir = os.path.join(self.base, "work", "journal")

    def write_manifest(self, names, dest_root=None):
        rows = []
        for name in names:
            source = os.path.join(self.lib, "shoot-a", name)
            st = os.stat(source)
            dest = os.path.join(dest_root or self.arch1, "shoot-a", name)
            rows.append(backup_plan.Row(source, dest, st.st_size, st.st_mtime_ns, "copy", ""))
        backup_plan.write_manifest(self.manifest, rows)

    def go(self, command, *extra, **injected):
        # a clock that never moves and an abort that records: no slow moment under load can reach os._exit
        self.aborted = []
        injected = {"volume_of": lambda p: p, "clock": lambda: 1.0, "abort": self.aborted.append, **injected}
        return self.run_cli(command, *extra, **injected)

    def tree(self):
        return sorted((d, tuple(sorted(f)), tuple(sorted(s))) for d, s, f in os.walk(self.top))


class BackupRunTests(BackupCase):
    def test_bad_archive_roots_are_refused_and_create_nothing(self):
        cases = {
            "missing key": self.config,
            "inside a library root": {**self.config, "archive_roots": [os.path.join(self.lib, "a")]},
            "inside the base folder": {**self.config,
                                       "archive_roots": [os.path.join(self.base, "a")]},
        }
        for command in ("backup-run", "backup-verify"):
            for name, config in cases.items():
                with self.subTest(command=command, case=name):
                    self.write_config(config)
                    before = self.tree()
                    code, err = self.go(command, self.manifest)
                    self.assertEqual(code, 2)
                    self.assertIn("archive_roots", err)
                    self.assertEqual(self.tree(), before)
            with self.subTest(command=command, case="two roots on one volume"):
                self.write_config({**self.config, "archive_roots": [self.arch1, self.arch2]})
                before = self.tree()
                code, err = self.go(command, self.manifest, volume_of=lambda p: "x:")
                self.assertEqual(code, 2)
                self.assertIn("archive_roots", err)
                self.assertEqual(self.tree(), before)

    def test_dry_run_creates_nothing_and_opens_no_database(self):
        before = self.tree()
        code, err = self.go("backup-run", self.manifest)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.tree(), before)
        self.assertFalse(os.path.exists(self.sqlite))
        self.assertEqual(len(self.stdout.splitlines()), 2)
        self.assertIn(" copy ", self.stdout)

    def test_apply_copies_a_clean_tree_with_exit_zero_and_a_tally(self):
        code, err = self.go("backup-run", self.manifest, "--apply")
        self.assertEqual((code, err), (0, ""))
        with open(os.path.join(self.arch1, "shoot-a", "IMG_0002.ARW"), "rb") as handle:
            self.assertEqual(handle.read(), b"b" * 100)
        self.assertIn("verified=2", self.stdout.splitlines())
        self.assertTrue(os.listdir(self.journal_dir))
        self.assertFalse(os.path.exists(self.sqlite))

    def test_apply_with_a_conflicting_destination_exits_six(self):
        synth.make_tree(self.arch1, {"shoot-a/IMG_0001.ARW": b"other"})
        code, _ = self.go("backup-run", self.manifest, "--apply")
        self.assertEqual(code, 6)
        self.assertIn("conflict=1", self.stdout)

    def test_a_refused_manifest_exits_two_and_creates_no_journal_folder(self):
        outside = os.path.join(self.top, "elsewhere")
        with self.subTest("dest outside every archive root"):
            self.write_manifest(["IMG_0001.ARW"], dest_root=outside)
            code, err = self.go("backup-run", self.manifest, "--apply")
            self.assertEqual(code, 2)
            self.assertIn("manifest", err)
        for name, text in (("malformed file", "not,a,manifest\n"),
                           ("row that is too short",
                            ",".join(backup_plan.COLUMNS) + "\nsrc,dst,1\n")):
            with self.subTest(name):
                with open(self.manifest, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                code, err = self.go("backup-run", self.manifest, "--apply")
                self.assertEqual(code, 2)
                self.assertIn("manifest", err)
        with self.subTest("manifest path that does not exist"):
            code, err = self.go("backup-run", os.path.join(self.top, "none.csv"), "--apply")
            self.assertEqual(code, 2)
            self.assertIn("manifest", err)
        self.assertFalse(os.path.exists(self.journal_dir))
        self.assertFalse(os.path.exists(outside))

    def test_a_part_file_destination_is_refused_by_both_modes_before_any_write(self):
        dest = os.path.join(self.arch1, "shoot-a", "IMG_0001.ARW")
        rows = []
        for name, target in (("IMG_0002.ARW", dest + backup.PART_SUFFIX), ("IMG_0001.ARW", dest)):
            st = os.stat(os.path.join(self.lib, "shoot-a", name))
            rows.append(backup_plan.Row(os.path.join(self.lib, "shoot-a", name), target,
                                        st.st_size, st.st_mtime_ns, "copy", ""))
        backup_plan.write_manifest(self.manifest, rows)
        os.makedirs(self.arch1)
        for extra in ((), ("--apply",)):
            with self.subTest(extra=extra):
                before = self.tree()
                code, err = self.go("backup-run", self.manifest, *extra)
                self.assertEqual(code, 2)
                self.assertIn(dest + backup.PART_SUFFIX, err)
                self.assertIn(rows[0].source, err)  # the library file a person has to rename
                self.assertEqual(self.tree(), before)
                self.assertEqual(os.listdir(self.arch1), [])
                self.assertFalse(os.path.exists(self.journal_dir))

    def test_stall_seconds_must_be_a_finite_number_above_zero(self):
        for value in ("nan", "inf", "0", "-1"):
            with self.subTest(value=value):
                before = self.tree()
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()) as err:
                    cli.main(["--base", self.base, "backup-run", self.manifest, "--apply",
                              "--stall-seconds", value], volume_of=lambda p: p,
                             opener=lambda p: self.fail("a file was read"))
                self.assertEqual(caught.exception.code, 2)
                self.assertIn("--stall-seconds", err.getvalue())
                self.assertEqual(self.tree(), before)
        with self.subTest(value="0.05"):
            aborted = []
            code, _ = self.go("backup-run", self.manifest, "--apply", "--stall-seconds", "0.05",
                              clock=lambda: 1.0, abort=aborted.append)
            self.assertEqual(code, 0)
            self.assertEqual(aborted, [])

    def test_a_manifest_bigger_than_the_free_space_exits_two_and_creates_nothing(self):
        for extra in ((), ("--apply",)):
            with self.subTest(extra=extra):
                before = self.tree()
                code, err = self.go("backup-run", self.manifest, *extra, free_bytes=lambda p: 199)
                self.assertEqual(code, 2)
                self.assertIn("manifest", err)
                self.assertFalse(os.path.exists(self.journal_dir))
                self.assertFalse(os.path.exists(self.arch1))
                self.assertEqual(self.tree(), before)

    def test_a_source_that_became_a_placeholder_is_changed_and_never_opened(self):
        opened = []

        def opener(path):
            opened.append(path)
            return open(path, "rb")

        def attrs_of(path, st):
            return 0x1000 if str(path).endswith("IMG_0002.ARW") else 0

        dest = os.path.join(self.arch1, "shoot-a", "IMG_0002.ARW")
        with self.subTest("dry run"):
            self.assertEqual(self.go("backup-run", self.manifest, attrs_of=attrs_of)[0], 0)
            self.assertIn(f"1 changed {dest}", self.stdout.splitlines())
        with self.subTest("apply"):
            code, _ = self.go("backup-run", self.manifest, "--apply", attrs_of=attrs_of,
                              opener=opener)
            self.assertEqual(code, 6)
            self.assertIn(f"1 changed {dest}", self.stdout.splitlines())
            self.assertFalse(os.path.exists(dest))
            self.assertTrue(opened)
            self.assertFalse([p for p in opened if p.endswith("IMG_0002.ARW")])

    def test_a_file_that_appears_before_the_rename_is_never_overwritten(self):
        if os.name != "nt":
            self.skipTest("only the Windows rename refuses to replace a file")
        dest = os.path.join(self.arch1, "shoot-a", "IMG_0001.ARW")

        def writer(part):
            if part.startswith(dest):
                with open(dest, "wb") as handle:
                    handle.write(b"other")
            return backup.open_part(part)

        code, _ = self.go("backup-run", self.manifest, "--apply", writer=writer)
        self.assertEqual(code, 6)
        self.assertIn(f"0 failed {dest}", self.stdout.splitlines())
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), b"other")

    def test_stall_seconds_reaches_the_watchdog(self):
        release = threading.Event()
        known = set(threading.enumerate())

        def stop_worker():  # let the blocked worker finish before the temp tree goes
            release.set()
            for thread in set(threading.enumerate()) - known:
                thread.join(timeout=5)

        self.addCleanup(stop_worker)
        aborted = []

        def opener(path):
            release.wait(10)
            raise OSError("released")

        code, err = self.go("backup-run", self.manifest, "--apply", "--stall-seconds", "0.05",
                            opener=opener, clock=time.monotonic, abort=aborted.append)
        self.assertEqual(aborted, [5])
        self.assertEqual(code, 5)
        self.assertIn("stalled", err)

    def test_a_stall_in_a_backup_run_test_is_recorded_and_the_test_run_goes_on(self):
        held, release = threading.Event(), threading.Event()
        known = set(threading.enumerate())

        def stop_worker():  # let the blocked worker finish before the temp tree goes
            release.set()
            for thread in set(threading.enumerate()) - known:
                thread.join(timeout=5)

        self.addCleanup(stop_worker)

        def opener(path):
            held.set()
            release.wait(10)
            raise OSError("released")

        # the clock jumps past the default limit once a read is held; `go` supplies the recording abort
        code, err = self.go("backup-run", self.manifest, "--apply", opener=opener,
                            clock=lambda: 10 ** 6 if held.is_set() else 1.0)
        self.assertEqual((code, self.aborted), (5, [5]))
        self.assertIn("stalled", err)

    def test_a_child_process_that_stalls_exits_five_and_journals_it(self):
        script = os.path.join(self.top, "child.py")
        root = os.path.dirname(os.path.dirname(os.path.abspath(cli.__file__)))
        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "import os, sys, threading\n"
                f"sys.path.insert(0, {root!r})\n"
                "from lrclean import cli\n"
                "def opener(path):\n"
                "    threading.Event().wait()\n"
                "try:  # only a process ended from inside main gives 5; any return or raise gives 99\n"
                f"    cli.main(['--base', {self.base!r}, 'backup-run', {self.manifest!r},\n"
                "        '--apply', '--stall-seconds', '0.5'], volume_of=lambda p: p,\n"
                "        attrs_of=lambda path, st: 0, opener=opener)\n"
                "finally:\n"
                "    os._exit(99)\n")
        child = subprocess.Popen([sys.executable, script], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        try:
            code = child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)
            self.fail("the stalled child did not exit")
        self.assertEqual(code, 5)
        (name,) = os.listdir(self.journal_dir)
        with open(os.path.join(self.journal_dir, name), encoding="utf-8") as handle:
            last = json.loads(handle.read().splitlines()[-1])
        self.assertEqual(last["event"], "stalled")


class BackupVerifyTests(BackupCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.go("backup-run", self.manifest, "--apply")[0], 0)
        shutil.rmtree(self.journal_dir)
        self.verified = os.path.join(self.out_dir(), "backup-verified-1.csv")

    def test_identical_copies_exit_zero_and_write_the_verified_list(self):
        code, err = self.go("backup-verify", self.manifest)
        self.assertEqual((code, err), (0, ""))
        with open(self.verified, encoding="utf-8-sig", newline="") as handle:
            self.assertEqual([r["state"] for r in csv.DictReader(handle)], ["identical"] * 2)
        self.assertFalse(os.path.exists(self.sqlite))
        self.assertFalse(os.path.exists(self.journal_dir))

    def test_a_changed_destination_byte_exits_six(self):
        with open(os.path.join(self.arch1, "shoot-a", "IMG_0001.ARW"), "r+b") as handle:
            handle.write(b"X")
        code, _ = self.go("backup-verify", self.manifest)
        self.assertEqual(code, 6)
        self.assertTrue(os.path.exists(self.verified))

    def test_a_destination_that_cannot_be_read_exits_six(self):
        dest = os.path.join(self.arch1, "shoot-a", "IMG_0001.ARW")

        def opener(path):
            if path == dest:
                raise OSError("unreadable")
            return open(path, "rb")

        code, _ = self.go("backup-verify", self.manifest, opener=opener)
        self.assertEqual(code, 6)

    def test_a_manifest_outside_every_archive_root_exits_two(self):
        self.write_manifest(["IMG_0001.ARW"], dest_root=os.path.join(self.top, "elsewhere"))
        code, err = self.go("backup-verify", self.manifest)
        self.assertEqual(code, 2)
        self.assertIn("manifest", err)
        self.assertFalse(os.path.exists(self.verified))

    def test_a_report_path_that_is_a_manifest_source_or_dest_is_refused_and_nothing_is_written(self):
        # A second tool folder inside the selected folder `shoot-a`, as the outside read built it.
        second = os.path.join(self.lib, "shoot-a")
        report = os.path.join(second, "out", "backup-verified-1.csv")
        synth.make_tree(self.lib, {"shoot-a/out/backup-verified-1.csv": b"field,data"})
        stat = os.stat(report)
        copied = os.path.join(self.arch1, "shoot-a", "out", "backup-verified-1.csv")
        other = os.path.join(self.lib, "shoot-a", "IMG_0001.ARW")
        source_row = backup_plan.Row(report, copied, stat.st_size, stat.st_mtime_ns, "copy", "")
        dest_row = backup_plan.Row(other, report, 100, 1, "copy", "")
        backup_plan.write_manifest(self.manifest, [source_row])
        self.assertEqual(self.go("backup-run", self.manifest, "--apply")[0], 0)
        self.base = second
        self.write_config({**self.config, "archive_roots": [self.arch1, self.arch2]})
        # No archive root can hold the tool folder, so the `dest` case needs the archive check out of the way.
        respelled = backup_plan.Row(report.upper().replace("\\", "/"), copied, stat.st_size,
                                    stat.st_mtime_ns, "copy", "")
        self.assertNotEqual(respelled.source, report)
        for name, row, check in (("source", source_row, backup_plan.archive_index),
                                 ("source in another case and slash", respelled, backup_plan.archive_index),
                                 ("dest", dest_row, lambda rows, roots: 1)):
            with self.subTest(name), mock.patch.object(backup_plan, "archive_index", check):
                backup_plan.write_manifest(self.manifest, [row])
                before = self.tree()
                code, err = self.go("backup-verify", self.manifest, opener=lambda p: self.fail("read"))
                self.assertEqual(code, 2)
                self.assertIn(report, err)
                self.assertEqual(self.tree(), before)
                with open(report, "rb") as handle:
                    self.assertEqual(handle.read(), b"field,data")


class RejectsCase(CliCase):
    """A live and a shoot catalog that both reject IMG_0001, two archive roots holding a verified copy of it."""

    def setUp(self):
        super().setUp()
        self.live = os.path.join(self.cats, "live.lrcat")
        self.arch = [os.path.join(self.top, "arch1"), os.path.join(self.top, "arch2")]
        self.volumes = {self.arch[0]: "x:", self.arch[1]: "y:"}
        self.root = self.lib.replace("\\", "/") + "/"
        os.remove(self.catalog)
        self.catalog_of(self.live, [(1, -1), (2, 0)])
        self.catalog_of(self.catalog, [(1, -1)])
        self.config = {**self.config, "live_catalog": self.live, "archive_roots": self.arch}
        self.write_config(self.config)
        st = os.stat(os.path.join(self.lib, "shoot-a", "IMG_0001.ARW"))
        source = os.path.join(self.lib, "shoot-a", "IMG_0001.ARW")
        for n, arch in enumerate(self.arch, start=1):
            dest = os.path.join(arch, "shoot-a", "IMG_0001.ARW")
            synth.make_tree(arch, {"shoot-a/IMG_0001.ARW": b"a" * 100})
            out = os.path.join(self.out_dir(), f"backup-manifest-{n}.csv")
            backup_plan.write_manifest(out, [backup_plan.Row(source, dest, 100, st.st_mtime_ns, "copy", "")])
            backup_verify.write_verified(os.path.join(self.out_dir(), f"backup-verified-{n}.csv"),
                                         [backup_verify.Verified(source, dest, 100, "0" * 64, "identical")])

    def catalog_of(self, path, picks, **extra):
        """A catalog of IMG_000<n> with the given pick (developed 0), or no pick key when `pick` is None."""
        entries = [{"root": self.root, "root_relative": None, "folder": "shoot-a/",
                    "base": f"IMG_000{n}", "developed": 0,
                    **({"pick": pick} if pick is not None else {}), **extra} for n, pick in picks]
        if os.path.exists(path):
            os.remove(path)
        synth.make_catalog(path, entries)

    def go(self, **injected):
        injected = {"volume_of": lambda p: self.volumes.get(p, "z:"), **injected}
        return self.run_cli("rejects", **injected)

    def append(self, path, data):
        with open(path, "ab") as handle:
            handle.write(data)

    def steps(self, **injected):
        self.assertEqual(self.run_cli("catalogs", **injected)[0], 0)

    def lift_tree(self, settings="S", twin_under=None):
        """IMG_0001 rejected only in the live catalog with develop work `settings`; its byte-identical twin
        IMG_0002 (under the library, or `twin_under`) is recorded by the shoot catalog and the live one."""
        twin_under = twin_under or self.lib
        twin_path = os.path.join(twin_under, "shoot-a", "IMG_0002.ARW")
        synth.make_tree(twin_under, {"shoot-a/IMG_0002.ARW": b"a" * 100})
        with_settings = {"settings": settings} if settings else {}
        def entry(n, pick, root):
            return {"root": root, "root_relative": None, "folder": "shoot-a/", "base": f"IMG_000{n}",
                    "developed": 1, "pick": pick, **with_settings}
        twin_root = twin_under.replace("\\", "/") + "/"
        for path, entries in ((self.live, [entry(1, -1, self.root), entry(2, 0, twin_root)]),
                              (self.catalog, [entry(2, 0, twin_root)])):
            os.remove(path)
            synth.make_catalog(path, entries)
        return twin_path

    def edit_work_db(self, sql):
        con = sqlite3.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
        try:
            con.executescript(sql)
            con.commit()
        finally:
            con.close()

    def snapshot(self, folder=None):
        folder = folder or self.out_dir()
        found = {}
        for where, _, names in os.walk(folder):
            for name in names:
                with open(os.path.join(where, name), "rb") as handle:
                    found[os.path.join(where, name)] = handle.read()
        return found

    def table(self, name):
        with open(os.path.join(self.out_dir(), name), encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def summary(self):
        with open(os.path.join(self.out_dir(), "rejects-summary.md"), encoding="utf-8") as handle:
            return handle.read()

    def verdicts(self):
        return [row["verdict"] for row in self.table("rejects.csv")]


class RejectsRefusalTests(RejectsCase):
    def refused(self, code, config=None, **injected):
        """Run once cleanly, then run `config` (the refusal) and check exit code, a silent out/ and no old-report note (exit 4 says it: see the stale-step test)."""
        self.write_config(self.config)
        self.steps()
        self.assertEqual(self.go()[0], 0)
        before = self.snapshot()
        if config is not None:
            self.write_config(config)
        got, err = self.go(**injected)
        self.assertEqual(got, code, err)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn("must not be acted on", err)  # a configuration refusal says nothing of the old reports
        return err

    def test_configuration_refusals_exit_two_and_change_nothing(self):
        cases = {
            "no archive roots": {k: v for k, v in self.config.items() if k != "archive_roots"},
            "one archive root": {**self.config, "archive_roots": self.arch[:1]},
            "live catalog excluded as written": {**self.config, "excluded_catalogs": [self.live]},
            "live catalog excluded in another spelling": {
                **self.config, "excluded_catalogs": [self.live.replace("\\", "/").upper()]},
        }
        for name, config in cases.items():
            with self.subTest(name):
                self.refused(2, config)

    def test_two_archive_roots_on_one_volume_exit_two_and_change_nothing(self):
        self.refused(2, volume_of=lambda p: "x:")

    def test_a_malformed_manifest_or_verified_list_exits_two_and_changes_nothing(self):
        for name in ("backup-manifest-2.csv", "backup-verified-1.csv"):
            with self.subTest(name):
                self.steps()
                with open(os.path.join(self.out_dir(), name), "w", encoding="utf-8") as handle:
                    handle.write("not,the,header\n")
                before = self.snapshot()
                code, err = self.go()
                self.assertEqual((code, self.snapshot()), (2, before))
                self.assertIn(name, err)

    def test_steps_that_are_missing_or_stale_exit_four_and_change_nothing(self):
        def live_none(): self.write_config({**self.config, "live_catalog": None})
        def grown(): self.append(self.live, b"x")
        def locked(): self.append(self.live + ".lock", b"")
        def moved_role(): self.write_config({**self.config, "old_states": [self.catalog]})
        def no_images(): self.catalog_of(self.live, []); self.steps()
        def no_picks(): self.catalog_of(self.live, [(1, None), (2, None)]); self.steps()
        for change in (live_none, grown, locked, moved_role, no_images, no_picks):
            with self.subTest(change.__name__):
                self.steps()
                self.assertEqual(self.go()[0], 0)
                before = self.snapshot()
                change()
                code, err = self.go()
                self.assertEqual((code, self.snapshot()), (4, before), err)
                self.assertIn("must not be acted on", err)
                self.write_config(self.config)
                self.catalog_of(self.live, [(1, -1), (2, 0)])
                if os.path.exists(self.live + ".lock"):
                    os.remove(self.live + ".lock")

    def test_a_first_run_without_a_database_exits_four_and_leaves_no_row(self):
        before = self.snapshot()
        code, err = self.go()
        self.assertEqual((code, self.snapshot()), (4, before), err)
        con = sqlite3.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
        try:
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            counts = [con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables]
        finally:
            con.close()
        self.assertEqual(sum(counts), 0)
        self.assertNotIn("must not be acted on", err)


class RejectsRunTests(RejectsCase):
    def test_a_reject_with_two_verified_copies_is_listed_and_three_files_are_written(self):
        self.steps()
        code, err = self.go()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(sorted(os.listdir(self.out_dir())), [
            "backup-manifest-1.csv", "backup-manifest-2.csv", "backup-verified-1.csv",
            "backup-verified-2.csv", "reject-removal-list.csv", "rejects-summary.md", "rejects.csv"])
        self.assertEqual(self.verdicts(), ["on_list"])
        self.assertEqual([r["bytes"] for r in self.table("reject-removal-list.csv")], ["100"])
        self.assertIn("1 rejects", self.stdout)

    def test_no_reject_writes_headers_only(self):
        self.catalog_of(self.live, [(1, 0), (2, 0)])
        self.catalog_of(self.catalog, [(1, 0)])
        self.steps()
        self.assertEqual(self.go()[0], 0)
        self.assertEqual((self.table("rejects.csv"), self.table("reject-removal-list.csv")), ([], []))

    def test_an_unreadable_shoot_catalog_makes_every_verdict_coverage_incomplete(self):
        synth.make_unreadable(self.catalog)
        self.steps()
        self.assertEqual(self.go()[0], 0)
        self.assertEqual(self.verdicts(), ["coverage_incomplete"])
        self.assertEqual(self.table("reject-removal-list.csv"), [])

    def test_a_shoot_catalog_in_excluded_catalogs_keeps_nothing_and_is_named(self):
        self.catalog_of(self.catalog, [(1, 0)])
        self.steps()
        self.assertEqual(self.go()[0], 0)
        self.assertEqual(self.verdicts(), ["kept"])
        self.write_config({**self.config, "excluded_catalogs": [self.catalog]})
        self.assertEqual(self.go()[0], 0)
        self.assertEqual(self.verdicts(), ["on_list"])
        self.assertIn(f"- {self.catalog}: ok", self.summary())

    def test_an_old_state_keeps_by_work_only(self):
        self.write_config({**self.config, "old_states": [self.catalog]})
        for pick, expected in ((0, "on_list"), (1, "kept_for_work")):
            with self.subTest(pick=pick):
                self.catalog_of(self.catalog, [(1, pick)])
                self.steps()
                self.assertEqual(self.go()[0], 0)
                self.assertEqual(self.verdicts(), [expected])
        with self.subTest("develop work"):
            self.catalog_of(self.catalog, [(1, 0)], developed=1)
            self.steps()
            self.assertEqual(self.go()[0], 0)
            self.assertEqual(self.verdicts(), ["kept_for_work"])

    def test_a_catalog_root_not_connected_now_makes_coverage_incomplete(self):
        held = os.path.join(self.top, "cats-held")
        empty = os.path.join(self.top, "cats-empty")
        for case, root in (("held a catalog at the scan", held), ("held none at the scan", empty)):
            with self.subTest(case):
                for folder in (held, empty):
                    os.makedirs(folder, exist_ok=True)
                self.catalog_of(self.catalog, [(1, 0)])
                shutil.move(self.catalog, os.path.join(held, "shoot-a.lrcat"))
                self.write_config({**self.config, "catalog_roots": [self.cats, held, empty]})
                self.steps()
                self.assertEqual(self.go()[0], 0)
                self.assertEqual(self.verdicts(), ["kept"])
                away = root + "-away"
                os.rename(root, away)
                self.assertEqual(self.go()[0], 0)
                self.assertEqual(self.verdicts(), ["coverage_incomplete"])
                self.assertEqual(self.table("reject-removal-list.csv"), [])
                self.assertIn(f"- limiting: {root} (not connected now)", self.summary())
                os.rename(away, root)
                self.assertEqual(self.go()[0], 0)
                self.assertEqual(self.verdicts(), ["kept"])
                shutil.rmtree(held)
                shutil.rmtree(empty)

    def test_a_catalog_put_in_a_root_after_the_scan_and_the_root_then_renamed_stays_incomplete(self):
        late = os.path.join(self.top, "cats-late")
        os.makedirs(late)
        self.write_config({**self.config, "catalog_roots": [self.cats, late]})
        self.steps()
        self.catalog_of(os.path.join(late, "late.lrcat"), [(1, 0)])
        os.rename(late, late + "-away")
        self.assertEqual(self.go()[0], 0)
        self.assertEqual(self.verdicts(), ["coverage_incomplete"])
        self.assertIn(f"- limiting: {late} (not connected now)", self.summary())

    def test_a_shoot_catalog_changed_or_appearing_after_the_scan_makes_coverage_incomplete(self):
        new = os.path.join(self.cats, "new.lrcat")
        def grown(): self.append(self.catalog, b"x")
        def appeared(): self.catalog_of(new, [(1, 0)])
        for change, cause in ((grown, f"{self.catalog} (changed since its snapshot)"),
                              (appeared, f"{new} (appeared since the catalogs scan)")):
            with self.subTest(change.__name__):
                self.steps()
                self.assertEqual(self.go()[0], 0)
                self.assertEqual(self.verdicts(), ["on_list"])
                change()
                self.assertEqual(self.go()[0], 0)
                self.assertEqual(self.verdicts(), ["coverage_incomplete"])
                self.assertEqual(self.table("reject-removal-list.csv"), [])
                self.assertIn(f"- limiting: {cause}", self.summary())
                self.catalog_of(self.catalog, [(1, -1)])
                if os.path.exists(new):
                    os.remove(new)

    def test_the_injected_stat_and_attrs_of_reach_the_verdicts(self):
        source = os.path.normcase(os.path.join(self.lib, "shoot-a", "IMG_0001.ARW"))

        def failing_stat(path, *args, **kwargs):
            if os.path.normcase(path) == source:
                raise PermissionError(path)
            return os.stat(path, *args, **kwargs)

        def recall(path, st):
            return 0x400000 if os.path.normcase(path) == source else 0
        self.steps()
        for name, injected, verdict in (("stat error", {"stat": failing_stat}, "changed_since_backup"),
                                        ("placeholder", {"attrs_of": recall}, "placeholder")):
            with self.subTest(name):
                self.assertEqual(self.go(**injected)[0], 0)
                self.assertEqual(self.verdicts(), [verdict])

    def test_a_scan_under_a_root_removed_from_the_configuration_still_keeps_and_is_listed(self):
        old = os.path.join(self.top, "cats-old")
        os.makedirs(old)
        self.catalog_of(self.catalog, [(1, 0)])
        shutil.move(self.catalog, os.path.join(old, "shoot-a.lrcat"))
        self.write_config({**self.config, "catalog_roots": [self.cats, old]})
        self.steps()
        self.write_config(self.config)
        self.assertEqual(self.go()[0], 0)
        self.assertEqual(self.verdicts(), ["kept"])
        self.assertRegex(self.summary(), rf"- catalogs scan \d+ of {re.escape(old)}: ")


class RejectsLiftWiringTests(RejectsCase):
    def hashed(self, *steps):
        for step in steps:
            self.assertEqual(self.run_cli(step)[0], 0)

    def test_the_library_hashes_lift_the_develop_hold_when_all_three_steps_ran(self):
        twin = self.lift_tree()
        self.hashed("catalogs", "inventory", "hash")
        self.assertEqual(self.go(), (0, ""))
        self.assertEqual(self.verdicts(), ["on_list"])
        self.assertEqual([r["twin"] for r in self.table("rejects.csv")], [twin])

    def test_without_a_counted_library_hash_the_reject_stays_kept_for_work_and_nothing_is_refused(self):
        partial = "UPDATE scan SET status = 'partial' WHERE kind = 'files'"
        for name, steps, damage in (("hash step not run", ("catalogs", "inventory"), ""),
                                    ("hash rows deleted", ("catalogs", "inventory", "hash"), 'DELETE FROM "hash"'),
                                    ("files scan partial", ("catalogs", "inventory", "hash"), partial)):
            with self.subTest(name):
                self.lift_tree()
                self.hashed(*steps)
                self.edit_work_db(damage or "SELECT 1")
                self.assertEqual(self.go(), (0, ""))
                self.assertEqual(self.verdicts(), ["kept_for_work"])
                self.assertEqual(self.table("reject-removal-list.csv"), [])

    def test_a_twin_held_only_under_a_backup_root_is_never_a_candidate(self):
        self.lift_tree(twin_under=self.backup)
        self.hashed("catalogs", "inventory", "hash")
        # the hash step takes only a sample of a backup file and no full hash of a lone library file: give both one
        self.edit_work_db(f'UPDATE hash SET "full" = \'{"f" * 64}\' WHERE file_id IN (SELECT id FROM file '
                          "WHERE path_orig LIKE '%lib%IMG_0001%' OR path_orig LIKE '%backup%IMG_0002%')")
        counted = self.rows("SELECT count(*) FROM hash h JOIN file f ON f.id = h.file_id "
                            "WHERE f.path_orig LIKE '%backup%IMG_0002%' AND h.\"full\" IS NOT NULL")
        self.assertEqual(counted, [(1,)])
        self.assertEqual(self.go(), (0, ""))
        self.assertEqual(self.verdicts(), ["kept_for_work"])


class RejectsReadOnlyTests(RejectsCase):
    def listings(self):
        keep = lambda where: not where.startswith(self.base)
        return [sorted((d, tuple(sorted(f))) for d, _, f in os.walk(root) if keep(d))
                for root in (self.lib, self.cats, *self.arch)]

    def test_only_out_changes_and_the_outputs_do_not_depend_on_files_scans_or_snapshots(self):
        for step in ("catalogs", "inventory", "hash"):
            self.assertEqual(self.run_cli(step)[0], 0)
        before = self.listings()
        self.assertEqual(self.go()[0], 0)
        outputs = ("rejects.csv", "reject-removal-list.csv", "rejects-summary.md")
        first = {name: self.snapshot()[os.path.join(self.out_dir(), name)] for name in outputs}
        self.assertEqual(self.listings(), before)
        self.assertEqual(self.go()[0], 0)
        self.assertEqual({n: self.snapshot()[os.path.join(self.out_dir(), n)] for n in outputs}, first)
        con = sqlite3.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
        try:
            for table in ("file", "hash"):
                con.execute(f'DELETE FROM "{table}"')
            con.commit()
        finally:
            con.close()
        for entry in os.scandir(os.path.join(self.base, "work")):
            if entry.is_dir():
                shutil.rmtree(entry.path)
        self.assertEqual(self.go()[0], 0)
        self.assertEqual({n: self.snapshot()[os.path.join(self.out_dir(), n)] for n in outputs}, first)

    def test_deleting_the_file_and_hash_rows_changes_the_verdict_only_when_a_lift_depended_on_them(self):
        for settings, before, after in (("S", "on_list", "kept_for_work"), (None, "kept_for_work", "kept_for_work")):
            with self.subTest(settings=settings):
                self.lift_tree(settings=settings)
                for step in ("catalogs", "inventory", "hash"):
                    self.assertEqual(self.run_cli(step)[0], 0)
                listed = self.listings()
                self.assertEqual(self.go(), (0, ""))
                self.assertEqual(self.verdicts(), [before])
                self.assertEqual(self.listings(), listed)
                self.edit_work_db('DELETE FROM "file"; DELETE FROM "hash";')
                self.assertEqual(self.go(), (0, ""))
                self.assertEqual(self.verdicts(), [after])
                self.assertEqual(self.listings(), listed)


class CopiesTests(RejectsCase):
    """The rejects tree plus IMG_0005, unrecorded, 100 bytes (hash H), archived and verified on both roots."""

    PURGE = "purge-20260101T000000Z.csv"

    def setUp(self):
        super().setUp()
        self.copy = os.path.join(self.lib, "shoot-a", "IMG_0005.ARW")
        synth.make_tree(self.lib, {"shoot-a/IMG_0005.ARW": b"c" * 100})
        st = os.stat(self.copy)
        for n, arch in enumerate(self.arch, start=1):
            dest = os.path.join(arch, "shoot-a", "IMG_0005.ARW")
            synth.make_tree(arch, {"shoot-a/IMG_0005.ARW": b"c" * 100})
            os.utime(dest, ns=(st.st_mtime_ns, st.st_mtime_ns))
            manifest = os.path.join(self.out_dir(), f"backup-manifest-{n}.csv")
            backup_plan.write_manifest(manifest, backup_plan.read_manifest(manifest)
                                       + [backup_plan.Row(self.copy, dest, 100, st.st_mtime_ns, "copy", "")])
            verified = os.path.join(self.out_dir(), f"backup-verified-{n}.csv")
            backup_verify.write_verified(verified, backup_verify.read_verified_list(verified)
                                         + [backup_verify.Verified(self.copy, dest, 100, "0" * 64, "identical")])

    def go(self, **injected):
        return self.run_cli("copies", **{"volume_of": lambda p: self.volumes.get(p, "z:"), **injected})

    def purge_result(self, text=None):
        sha = hashlib.sha256(b"c" * 100).hexdigest()
        text = text or f"source,kind,staged,bytes,sha256,state,reason\nx,reject,y,100,{sha},purged,\n"
        with open(os.path.join(self.out_dir(), self.PURGE), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def summary_text(self):
        with open(os.path.join(self.out_dir(), "copies-summary.md"), encoding="utf-8") as handle:
            return handle.read()

    def test_exit_0_with_a_purged_reject_row_and_with_an_empty_list(self):
        self.steps()
        self.assertEqual(self.run_cli("inventory")[0], 0)
        self.assertEqual(self.go(), (0, ""))
        self.assertIn("copies written to", self.stdout)
        self.assertIn(": 0 rows", self.stdout)
        self.assertEqual(self.table("copy-removal-list.csv"), [])
        photos = len(os.listdir(os.path.join(self.lib, "shoot-a")))  # the library's files; the tool's folder is not scanned
        line = f"- {photos} files not covered by hash: run hash"  # inventory ran, hash did not
        self.assertGreater(photos, 0)
        self.assertEqual(self.stdout.splitlines().count(line), 1)
        self.assertIn(f"{line}\n", self.summary_text())
        self.assertEqual(self.run_cli("hash")[0], 0)
        self.assertEqual(self.go(), (0, ""))
        self.assertNotIn("not covered by hash", self.stdout)  # unique files have no full hash and need none
        self.assertIn("## Not checked\n\n- none\n", self.summary_text())
        twin = os.path.join(self.lib, "shoot-a", "IMG_0006.ARW")  # a twin of IMG_0005: a group of two
        synth.make_tree(self.lib, {"shoot-a/IMG_0006.ARW": b"c" * 100})
        for step in ("inventory", "hash"):
            self.assertEqual(self.run_cli(step)[0], 0)
        self.assertEqual(self.go(), (0, ""))
        self.assertNotIn("not covered by hash", self.stdout)
        self.edit_work_db("UPDATE hash SET \"full\" = NULL WHERE file_id IN "
                          "(SELECT id FROM file WHERE path_orig LIKE '%IMG_0006%')")
        self.assertEqual(self.go(), (0, ""))
        self.assertEqual(self.stdout.splitlines().count("- 1 files not covered by hash: run hash"), 1)
        os.remove(twin)
        self.assertEqual(self.run_cli("inventory")[0], 0)
        self.assertEqual(self.run_cli("hash")[0], 0)
        self.assertEqual(self.go(), (0, ""))
        with open(os.path.join(self.out_dir(), "copy-removal-list.csv"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(self.stdout.splitlines()[-1], f"SHA-256 of the list: {digest}")
        self.assertTrue(self.summary_text().endswith(f"- SHA-256: {digest}\n"))
        self.purge_result()
        self.assertEqual(self.go(), (0, ""))
        self.assertIn(": 1 rows", self.stdout)  # the copy of the purged reject, so `build` got the result
        self.assertEqual([(r["source"], r["twin"]) for r in self.table("copy-removal-list.csv")],
                         [(self.copy, "")])
        with open(os.path.join(self.out_dir(), "copies-summary.md"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("- result files read: 1\n- purged rejects in them: 1\n", text)
        # RC-151: only a photo under photo_root with no valid hash row counts; four look-alikes do not (non-photos are .xmp sidecars: inventory keeps no other)
        outside = os.path.join(self.top, "lib2")
        synth.make_tree(self.lib, {"shoot-a/PLACE_1.ARW": b"p1", "shoot-a/PLACE_2.ARW": b"p22",
                                   "shoot-a/notes_1.xmp": b"n1", "shoot-a/notes_2.xmp": b"n22",
                                   "shoot-a/notes_3.xmp": b"n333"})
        synth.make_tree(outside, {"far_1.ARW": b"f1", "far_2.ARW": b"f22", "far_3.ARW": b"f333",
                                  "far_4.ARW": b"f4444"})
        self.write_config({**self.config, "library_roots": [self.lib, outside]})
        placeholder = lambda path, st: 0x1000 if "PLACE_" in path else 0
        self.assertEqual(self.run_cli("inventory", attrs_of=placeholder)[0], 0)
        self.assertEqual(self.run_cli("hash")[0], 0)
        self.edit_work_db('DELETE FROM "hash" WHERE file_id IN (SELECT id FROM file WHERE path_orig LIKE '
                          "'%PLACE\\_%' ESCAPE '\\' OR path_orig LIKE '%notes\\_%' ESCAPE '\\' "
                          "OR path_orig LIKE '%far\\_%' ESCAPE '\\');"
                          'UPDATE "hash" SET mtime_ns = mtime_ns + 1 WHERE file_id IN '
                          "(SELECT id FROM file WHERE path_orig LIKE '%IMG_0002%')")
        stale, placeholders, non_photos, outsiders = 1, 2, 3, 4  # all different: no two filters can be swapped
        self.assertEqual(len({stale, placeholders, non_photos, outsiders}), 4)
        self.assertEqual(self.go(), (0, ""))
        self.assertEqual(self.stdout.splitlines().count(f"- {stale} files not covered by hash: run hash"), 1)

    def test_exit_2_for_the_shared_refusals_and_an_unreadable_purge_result_and_build_is_not_called(self):
        self.steps()
        self.assertEqual(self.run_cli("inventory")[0], 0)
        self.assertEqual(self.go()[0], 0)
        self.assertEqual(self.run_cli("rejects", volume_of=lambda p: self.volumes.get(p, "z:"))[0], 0)
        before = self.snapshot()  # both summaries exist: a configuration refusal must not call them old
        messages = set()
        with mock.patch.object(cli.copies, "build", side_effect=AssertionError("build was called")):
            for command in ("rejects", "copies"):
                with self.subTest(command=command):
                    self.write_config({**self.config, "archive_roots": self.arch[:1]})
                    code, err = self.run_cli(command, volume_of=lambda p: "x:")
                    self.assertEqual(code, 2, err)
                    self.assertNotIn("older than", err)
                    messages.add(err.split("\n")[0])
            self.assertEqual(len(messages), 1, messages)  # one message, which names neither command
            self.assertIn("archive_roots must name at least two roots", messages.pop())
            self.write_config({**self.config, "excluded_catalogs": [self.live]})
            for command in ("rejects", "copies"):
                with self.subTest(command=command, refusal="excluded live catalog"):
                    code, err = self.run_cli(command, volume_of=lambda p: "x:")
                    self.assertEqual(code, 2, err)
                    self.assertNotIn("older than", err)
        self.write_config(self.config)
        self.purge_result("not,the,header\n")
        before[os.path.join(self.out_dir(), self.PURGE)] = b"not,the,header\n"
        code, err = self.go()
        self.assertEqual(code, 2, err)
        self.assertIn(self.PURGE, err)
        self.assertNotIn("older than", err)
        self.assertEqual(self.snapshot(), before)

    def test_exit_4_for_incomplete_coverage_names_the_summary_and_changes_nothing(self):
        self.steps()
        self.assertEqual(self.run_cli("inventory")[0], 0)
        self.assertEqual(self.go()[0], 0)
        synth.make_unreadable(self.catalog)
        self.steps()
        before = self.snapshot()
        code, err = self.go()
        self.assertEqual((code, self.snapshot()), (4, before), err)
        self.assertIn("out/copies-summary.md", err)
        self.assertIn("run catalogs again", err)
        self.assertIn("excluded_catalogs", err)
        self.assertIn("older than the latest scans", err)  # an exit-4 refusal still says the reports are old

    def test_both_summaries_name_the_scans_read_before_the_views(self):
        for command, name in (("copies", "copies-summary.md"), ("rejects", "rejects-summary.md")):
            with self.subTest(command=command):
                self.steps()
                self.assertEqual(self.run_cli("inventory")[0], 0)
                first = self.rows("SELECT MAX(id) FROM scan WHERE kind = 'catalogs'")[0][0]
                real = cli.rejects.latest_views

                def late(conn, **kwargs):
                    self.steps()  # a catalogs scan that completes after the helper read the scans
                    return real(conn, **kwargs)

                with mock.patch.object(cli.rejects, "latest_views", late):
                    self.assertEqual(self.run_cli(command, volume_of=lambda p: self.volumes.get(p, "z:"))[0], 0)
                self.assertGreater(self.rows("SELECT MAX(id) FROM scan WHERE kind = 'catalogs'")[0][0], first)
                with open(os.path.join(self.out_dir(), name), encoding="utf-8") as handle:
                    self.assertEqual(re.findall(r"^- catalogs scan (\d+) of ", handle.read(), re.M), [str(first)])

    def walked(self):
        """Every walked root, the tool's own folder left out."""
        return [sorted((d, tuple(sorted(f))) for d, _, f in os.walk(root) if not d.startswith(self.base))
                for root in (self.lib, self.cats, *self.arch)]

    def test_a_run_of_one_list_leaves_the_other_list_and_its_summary_as_they_were(self):
        self.steps()
        self.assertEqual(self.run_cli("inventory")[0], 0)
        self.purge_result()
        lists = (("copy-removal-list.csv", "copies-summary.md"), ("reject-removal-list.csv", "rejects-summary.md"))
        for first, second in ((0, 1), (1, 0)):
            with self.subTest(first=lists[first]):
                self.assertEqual(self.run_cli(("copies", "rejects")[first],
                                              volume_of=lambda p: self.volumes.get(p, "z:"))[0], 0)
                mine = {name: self.snapshot()[os.path.join(self.out_dir(), name)] for name in lists[first]}
                walked = self.walked()
                self.assertEqual(self.run_cli(("copies", "rejects")[second],
                                              volume_of=lambda p: self.volumes.get(p, "z:"))[0], 0)
                snapshot = self.snapshot()
                self.assertEqual({name: snapshot[os.path.join(self.out_dir(), name)] for name in lists[first]}, mine)
                self.assertEqual(self.walked(), walked)


MTIME = 1_700_000_000_000_000_000
LINKABLE = ((), ("work",), ("work", "staging"), ("work", "journal"), ("out",))  # the tool's folders (RC-103)


class MoverCase(unittest.TestCase):
    """A World with a catalog scan, a summary naming it, and a list of two archived rows (the second has no sidecar)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.w = World(tmp.name)
        self.out = os.path.join(self.w.base, "out")
        self.catalog = self.w.add("cats/shoot-a.lrcat", b"catalog", MTIME)
        st = os.stat(self.catalog)
        conn = store.connect(os.path.join(self.w.base, "work", "lrclean.sqlite"))
        scan = store.begin_scan(conn, os.path.dirname(self.catalog), "catalogs", now=lambda: "t")
        store.finish_scan(conn, scan, "complete", now=lambda: "t")
        store.insert_row(conn, "catalog", scan_id=scan, source_path=self.catalog, role="shoot", status="ok",
                         size=st.st_size, mtime_ns=st.st_mtime_ns, wal_bytes=0, has_lock=0)
        conn.commit()
        conn.close()
        self.summary = os.path.join(self.out, "rejects-summary.md")
        self.scan_text = f"# Summary\n\n## Scans\n\n- catalogs scan {scan} of {self.w.photo_root}: complete, finished t\n"
        self.first, self.second = self.add_row(1, ("xmp",)), self.add_row(2, ())
        self.list_path, self.sha = self.removal_list([self.first, self.second])

    def name_list(self, sha):
        """The summary ends with the list's SHA-256, as the reject check writes it."""
        with open(self.summary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{self.scan_text}\n## Removal list\n\n- SHA-256: {sha}\n")

    def removal_list(self, rows):
        """Write the official list and have the summary name it; returns (path, sha256)."""
        path, sha = self.w.removal_list(rows)
        self.name_list(sha)
        return path, sha

    def add_row(self, number, extras):
        stamp = MTIME + number * 10**7
        reject = self.w.add(f"shoot-a/IMG_{number:04d}.ARW", b"raw-%d" % number, stamp)
        sides = [self.w.add(f"shoot-a/IMG_{number:04d}.{ext}", b"%s-%d" % (ext.encode(), number), stamp + 10**5)
                 for ext in extras]
        for path in (reject, *sides):
            self.w.archive(path)
        rel = os.path.relpath(reject, self.w.photo_root)
        dests = tuple(os.path.join(root, rel) for root in self.w.archive_roots)
        return (reject, os.stat(reject).st_size, stamp, tuple(sides), sum(os.stat(s).st_size for s in sides),
                dests, "")

    def stage(self, *extra, sha=None, apply=True, list_path=None):
        return self.w.main("stage", list_path or self.list_path, "--approved-sha256", self.sha if sha is None else sha,
                           *(["--apply"] if apply else []), *extra)

    def journal(self, batch=BATCH):
        return os.path.join(self.w.base, "work", "journal", f"mover-{batch}.jsonl")

    def tree(self):
        found = {}
        for folder, dirs, names in os.walk(self.w.tmp):
            found.update({os.path.join(folder, name): None for name in dirs})
            for name in names:
                with open(os.path.join(folder, name), "rb") as handle:
                    found[os.path.join(folder, name)] = handle.read()
        return found

    def staged(self, original):
        return mover_files.staged_path(self.w.base, BATCH, original)

    def csv_rows(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def keywords(self, **replaced):
        return mock.patch.object(self.w, "cli_keywords", return_value={**self.w.cli_keywords(), **replaced})

    def rows_in(self, out, state):
        """How many result lines of `out` carry `state` (the tally and the closing sentence do not)."""
        return sum(f" {state} " in line for line in out.splitlines())

    def purge_sha(self, batch=BATCH):
        code, out, _err = self.w.main("purge", batch)
        self.assertIn(code, (0, 6))
        return re.search(r"purge set: ([0-9a-f]{64})", out).group(1)

    def unplace_second(self):
        """The second row's reject: its `end` line gone and its original back beside the staged file."""
        with open(self.journal(), encoding="utf-8") as handle:
            lines = handle.readlines()
        keep = [line for line in lines if not (json.loads(line)["event"] == "end"
                                               and json.loads(line)["original"] == self.second[0])]
        self.assertEqual(len(keep), len(lines) - 1)
        with open(self.journal(), "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(keep)
        shutil.copy2(self.staged(self.second[0]), self.second[0])

    def stall_on(self, path):
        """Make the first `stat` of `path` from a worker thread last 100 s of the fake clock."""
        now, workers, release = [0.0], [], threading.Event()
        real = self.w.stat

        def stat(target):
            if target == path and threading.current_thread() is not threading.main_thread() and not workers:
                workers.append(threading.current_thread())
                now[0] += 100.0
                release.wait(20)
            return real(target)

        def finish():
            release.set()
            workers[0].join(20)
        return mock.patch.multiple(self.w, stat=stat, clock=lambda: now[0]), finish


class StageCommandTests(MoverCase):
    def test_rule44_a_clean_apply_is_exit_0_and_writes_the_journal_and_the_results_from_it(self):
        code, out, err = self.stage()
        self.assertEqual((code, err), (0, ""))
        self.assertIn(self.sha, out)
        self.assertFalse(any(os.path.exists(path) for path in (self.first[0], *self.first[3], self.second[0])))
        self.assertTrue(all(os.path.exists(self.staged(path)) for path in (self.first[0], self.second[0])))
        with open(self.journal(), encoding="utf-8") as handle:
            start = json.loads(handle.readline())
        self.assertEqual((start["event"], start["op"], start["batch"], start["list_sha256"], start["approved_sha256"]),
                         ("start", "stage", BATCH, self.sha, self.sha))
        self.assertEqual(start["scans"], [["catalogs", 1]])
        self.assertEqual([row["state"] for row in self.csv_rows(f"stage-{BATCH}.csv")], ["staged"] * 3)

    def test_rule44_a_held_row_is_exit_6_and_named_and_the_other_row_is_staged(self):
        def sidecar_changed():  # its sidecar changed since the list
            os.utime(self.first[3][0], ns=(MTIME, MTIME))
            return (contextlib.nullcontext(), "changed", self.first[3][0],
                    "changed: the file (named) changed since the list was made")

        def copy_unreadable():  # its first archive copy cannot be opened
            copy, real = self.first[5][0], self.w.opener

            def opener(path):
                if os.path.normcase(path) == os.path.normcase(copy):
                    raise PermissionError(path)
                return real(path)
            return (mock.patch.object(self.w, "opener", opener), "read_error", copy,
                    "read_error: a file of the row (named) could not be read, so the row stays")

        for cause in (sidecar_changed, copy_unreadable):
            with self.subTest(cause.__name__):
                self.setUp()  # a fresh synthetic tree for each cause
                patch, reason, named, legend = cause()
                self.assertNotEqual(os.path.normcase(named), os.path.normcase(self.first[0]))
                with patch:
                    code, out, _err = self.stage()
                self.assertEqual(code, 6)
                self.assertIn(f"held {self.first[0]} {reason}", out)
                self.assertIn(f"    {named}", out)  # the held row names the file it is held on
                self.assertIn(legend, out)  # this cause's own legend line, in this cause's own output
                self.assertTrue(os.path.exists(self.first[0]) and os.path.exists(self.staged(self.second[0])))
                self.assertEqual([row["state"] for row in self.csv_rows(f"stage-{BATCH}.csv")], ["staged", "held"])

    def test_rule44_a_list_of_no_row_is_exit_0(self):
        _path, sha = self.removal_list([])
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(self.stage(sha=sha, apply=apply)[0], 0)

    def test_rule44_a_stall_is_exit_5_and_nothing_is_renamed_or_written_as_results(self):
        patch, finish = self.stall_on(self.first[0])
        before = self.w.listing()
        with patch:
            code, _out, _err = self.stage("--stall-seconds", "10")
        finish()
        self.assertEqual((code, self.w.aborted, self.w.listing()), (5, [5], before))
        self.assertFalse(os.path.exists(os.path.join(self.out, f"stage-{BATCH}.csv")))

    def test_rule42_a_dry_run_changes_nothing_and_prints_the_sha_and_what_apply_would_do(self):
        before = self.tree()
        code, out, err = self.stage(apply=False)
        self.assertEqual((code, err, self.tree()), (0, "", before))
        self.assertEqual(self.rows_in(out, "would_stage"), 2)
        self.assertIn(self.sha, out)

    def test_the_stage_dry_run_says_contents_are_hashed_only_with_apply(self):
        _code, out, _err = self.stage(apply=False)
        self.assertRegex(out, r"(?i)hashed only with --apply.*hold a row marked would_stage")

    def test_rule32_a_copy_of_the_list_with_its_own_sha_is_exit_2_and_the_opener_is_never_called(self):
        copy = os.path.join(self.out, "copy.csv")
        shutil.copy(self.list_path, copy)
        calls = []
        with mock.patch.object(self.w, "opener", lambda path: calls.append(path)):
            code, _out, err = self.stage(sha=self.sha, list_path=copy)
        self.assertEqual((code, calls), (2, []))
        self.assertIn("reject-removal-list.csv", err)
        self.assertEqual(self.stage(apply=False)[0], 0)  # the real file is accepted

    def test_rule32_an_upper_case_approval_is_accepted(self):
        self.assertEqual(self.stage(sha=self.sha.upper(), apply=False)[0], 0)
        self.assertEqual(self.stage(sha=self.sha.upper())[0], 0)
        with open(self.journal(), encoding="utf-8") as handle:
            self.assertEqual(json.loads(handle.readline())["approved_sha256"], self.sha)  # kept in lower case

    def test_rule32_a_wrong_sha_is_exit_2_with_the_computed_one_before_any_other_read(self):
        calls, opened = [], []
        real_opener, real_stat = self.w.opener, self.w.stat
        spies = {"processes": lambda: calls.append("processes") or [],
                 "stat": lambda path: calls.append(path) or real_stat(path)}
        with mock.patch.object(self.w, "opener", lambda path: opened.append(path) or real_opener(path)), \
                self.keywords(**spies):
            for wrong in ("cd" * 32, self.sha[:63], ""):
                with self.subTest(wrong=wrong):
                    code, _out, err = self.stage(sha=wrong, apply=False)
                    self.assertEqual(code, 2)
                    self.assertIn(self.sha, err)
        self.assertEqual((calls, set(opened)), ([], {self.list_path}))

    def test_rule32_a_list_that_is_missing_is_exit_2_and_changes_nothing(self):
        os.remove(self.list_path)
        before = self.tree()
        self.assertEqual((self.stage(apply=False)[0], self.tree()), (2, before))

    def test_rule32_only_is_repeatable_and_picks_rows_and_an_unknown_name_is_exit_2(self):
        code, out, _err = self.stage("--only", self.second[0], "--only", self.first[0], apply=False)
        self.assertEqual((code, self.rows_in(out, "would_stage")), (0, 2))
        code, out, _err = self.stage("--only", self.second[0], apply=False)
        self.assertEqual((code, self.rows_in(out, "would_stage")), (0, 1))
        self.assertEqual(self.stage("--only", os.path.join(self.w.photo_root, "none.ARW"), apply=False)[0], 2)

    def test_rule36_a_sidecar_shared_with_a_row_only_leaves_out_holds_the_selected_row(self):
        twin = (self.second[0], *self.second[1:3], self.first[3], *self.second[4:])
        _path, sha = self.removal_list([self.first, twin])
        code, out, _err = self.stage("--only", self.first[0], sha=sha)
        self.assertEqual(code, 6)
        self.assertIn("shared_sidecar", out)
        self.assertTrue(os.path.exists(self.first[0]))

    def test_rule33_a_source_outside_photo_root_is_exit_2_only_when_its_row_is_selected(self):
        outside = os.path.join(self.w.archive_roots[0], "elsewhere.ARW")
        with open(outside, "wb") as handle:
            handle.write(b"x")
        stray = (outside, 1, MTIME, (), 0, self.second[5], "")
        _path, sha = self.removal_list([self.first, stray])
        before = self.tree()
        code, _out, err = self.stage(sha=sha)
        self.assertEqual((code, self.tree()), (2, before))
        self.assertIn("outside photo_root", err)
        self.assertEqual(self.stage("--only", self.first[0], sha=sha, apply=False)[0], 0)

    def test_rule44_an_oserror_of_a_journal_append_is_exit_6_naming_the_journal(self):
        appended = []

        def append(path, data):
            appended.append(path)
            if len(appended) == 2:
                raise OSError("disk full")
            self.w.append(path, data)
        with self.keywords(append=append):
            code, _out, err = self.stage()
        self.assertEqual(code, 6)
        self.assertIn("disk full", err)
        self.assertIn(self.journal(), err)
        self.assertNotIn("no journal", err)

    def test_rule44_an_oserror_of_the_first_append_says_no_journal_was_written_and_nothing_moved(self):
        def append(path, data):
            raise OSError("disk full")
        before = self.w.listing()
        with self.keywords(append=append):
            code, _out, err = self.stage()
        self.assertEqual((code, self.w.listing()), (6, before))
        self.assertFalse(os.path.exists(self.journal()))
        self.assertIn("no journal was written and nothing moved", err)
        self.assertNotIn(self.journal(), err)

    def test_rule44_an_oserror_of_the_results_file_is_exit_6_naming_the_journal(self):
        def write(path, data):
            raise OSError("results refused")
        with self.keywords(write=write):
            code, _out, err = self.stage()
        self.assertEqual(code, 6)
        self.assertIn(self.journal(), err)
        self.assertTrue(os.path.exists(self.staged(self.first[0])))  # the files had moved: the journal holds that

    def test_rule43_stall_seconds_of_zero_or_not_a_number_is_refused_by_stage(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                self.assertEqual(self.stage("--stall-seconds", value, apply=False)[0], 2)

    def test_rule33_the_gate_refuses_a_running_lightroom_with_exit_3_before_anything_moves(self):
        before = self.tree()
        with self.keywords(processes=lambda: ["Lightroom.exe"]):
            self.assertEqual((self.stage()[0], self.tree()), (3, before))


    def test_rc104_an_older_list_copied_over_the_official_path_is_exit_4_and_nothing_moves(self):
        _path, older = self.w.removal_list([self.first])  # the summary still names the list rejects wrote
        before = self.tree()
        for apply in (False, True):
            with self.subTest(apply=apply):
                code, _out, err = self.stage(sha=older, apply=apply)
                self.assertEqual((code, self.tree()), (4, before))
                self.assertIn("run catalogs and rejects again", err)
        self.name_list(older)  # the same list, named by its summary, is staged
        self.assertEqual(self.stage(sha=older, apply=False)[0], 0)

    def test_rc104_a_summary_without_the_removal_list_line_is_exit_4(self):
        with open(self.summary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(self.scan_text)
        before = self.tree()
        self.assertEqual((self.stage()[0], self.tree()), (4, before))

    def test_rc103_a_linked_tool_folder_is_exit_2_for_stage_and_nothing_is_written(self):
        os.makedirs(os.path.join(self.w.base, "work", "catalogs", "shoot-a-x"))
        for parts in (*LINKABLE, ("work", "catalogs"), ("work", "catalogs", "shoot-a-x")):
            with self.subTest(folder=parts):
                link = os.path.join(self.w.base, *parts)
                self.w.links = {link}
                before = self.tree()
                code, _out, err = self.stage()
                self.assertEqual((code, self.tree()), (2, before))
                self.assertIn(link, err)
        self.w.links = set()  # work/staging does not exist yet, and that is no refusal
        self.assertEqual(self.stage()[0], 0)


class UndoPurgeCommandTests(MoverCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.stage()[0], 0)
        self.listed = self.w.listing()

    def test_rc103_a_linked_tool_folder_is_exit_2_for_undo_and_purge_and_nothing_is_written(self):
        argv = {"undo": ["undo", BATCH, "--apply"],
                "purge": ["purge", BATCH, "--apply", "--approved-sha256", self.purge_sha()]}
        for parts in LINKABLE:
            for command, args in argv.items():
                with self.subTest(folder=parts, command=command):
                    link = os.path.join(self.w.base, *parts)
                    self.w.links = {link}
                    before = self.tree()
                    code, _out, err = self.w.main(*args)
                    self.assertEqual((code, self.tree()), (2, before))
                    self.assertIn(link, err)
        self.w.links = set()
        self.assertEqual(self.w.main(*argv["undo"])[0], 0)

    def test_rule44_undo_restores_every_row_names_its_files_and_a_second_undo_is_exit_0(self):
        code, out, err = self.w.main("undo", BATCH, "--apply")
        self.assertEqual((code, err, self.rows_in(out, "restored")), (0, "", 2))
        self.assertIn(f"    {self.first[3][0]}", out)
        self.assertEqual(self.w.listing().keys(), {*self.listed, self.first[0], *self.first[3], self.second[0]})
        self.assertEqual(self.w.main("undo", BATCH, "--apply")[0], 0)

    def test_rule42_a_dry_run_of_undo_and_of_purge_changes_nothing_and_names_what_apply_would_do(self):
        before = self.tree()
        code, out, _err = self.w.main("undo", BATCH)
        self.assertEqual((code, self.rows_in(out, "would_restore"), self.tree()), (0, 2, before))
        self.assertIn(f"{self.staged(self.first[0])} -> {self.first[0]}", out)
        code, out, _err = self.w.main("purge", BATCH)
        self.assertEqual((code, self.rows_in(out, "would_purge"), self.tree()), (0, 2, before))
        self.assertNotIn(f"    {self.first[0]}", out)

    def test_rule43_stall_seconds_of_zero_or_not_a_number_is_refused_by_purge(self):
        for value in ("0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                self.assertEqual(self.w.main("purge", BATCH, "--stall-seconds", value)[0], 2)
        self.assertEqual(self.w.main("purge", BATCH, "--stall-seconds", "0.5")[0], 0)

    def test_rule33_undo_runs_with_lightroom_open_and_opens_no_database(self):
        calls = []
        with self.keywords(processes=lambda: calls.append(1) or ["Lightroom.exe"]), \
                mock.patch.object(cli.store, "connect", side_effect=AssertionError("opened the database")):
            code, _out, err = self.w.main("undo", BATCH, "--apply")
        self.assertEqual((code, err, calls), (0, "", []))

    def test_rule33_purge_with_another_purge_set_sha_is_exit_2_and_deletes_nothing(self):
        sha = self.purge_sha()
        before = self.tree()
        for approval in ("cd" * 32, sha[:63], None):
            with self.subTest(approval=approval):
                args = ["--approved-sha256", approval] if approval else []
                code, _out, err = self.w.main("purge", BATCH, "--apply", *args)
                self.assertEqual((code, sha in err, self.tree()), (2, True, before))

    def test_rule41_purge_deletes_the_approved_set_without_the_freshness_evidence_and_writes_the_results(self):
        os.remove(self.summary)  # rule 33: purge skips the freshness test
        code, _out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", self.purge_sha().upper())
        self.assertEqual((code, err), (0, ""))
        self.assertFalse(any(os.path.exists(self.staged(p)) for p in (self.first[0], *self.first[3], self.second[0])))
        self.assertEqual([row["state"] for row in self.csv_rows(f"purge-{BATCH}.csv")], ["purged"] * 3)
        self.assertEqual(self.w.main("purge", BATCH, "--apply", "--approved-sha256", self.purge_sha())[0], 0)

    def test_rule33_purge_refuses_in_the_order_roots_approval_lightroom_coverage(self):
        sha = self.purge_sha()
        config = os.path.join(self.w.base, "work", "config.json")
        with open(config, encoding="utf-8") as handle:
            cfg = json.load(handle)
        cfg["catalog_roots"] = [os.path.join(self.w.tmp, "gone")]  # not connected now: coverage incomplete
        with open(config, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(cfg, handle)
        root, hidden = self.w.archive_roots[1], self.w.archive_roots[1] + "-hidden"
        os.rename(root, hidden)
        got = []
        for active in (("roots", "approval", "lightroom"), ("approval", "lightroom"), ("lightroom",), ()):
            if "roots" not in active and not os.path.isdir(root):
                os.rename(hidden, root)
            procs = ["Lightroom.exe"] if "lightroom" in active else []
            with self.keywords(processes=lambda procs=procs: procs):
                code, _out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256",
                                              "cd" * 32 if "approval" in active else sha)
            got.append((code, sha in err))
        self.assertEqual(got, [(2, False), (2, True), (3, False), (4, False)])

    def test_rule44_an_unplaced_row_is_exit_6_and_the_other_row_is_done(self):
        self.unplace_second()
        code, out, _err = self.w.main("undo", BATCH, "--apply")
        self.assertEqual((code, os.path.exists(self.first[0])), (6, True))
        self.assertIn(f"refused {self.second[0]} unplaced", out)
        self.assertTrue(os.path.exists(self.staged(self.second[0])))

    def test_rule44_an_unplaced_row_is_held_by_purge_and_the_other_row_is_purged(self):
        self.unplace_second()
        code, out, _err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", self.purge_sha())
        self.assertEqual(code, 6)
        self.assertIn(f"held {self.second[0]} unplaced", out)
        self.assertFalse(os.path.exists(self.staged(self.first[0])))
        self.assertEqual([(row["state"], row["reason"]) for row in self.csv_rows(f"purge-{BATCH}.csv")],
                         [("purged", ""), ("purged", ""), ("held", "unplaced")])

    def test_rule44_an_invalid_journal_line_is_exit_2_for_undo_and_purge_and_changes_nothing(self):
        sha = self.purge_sha()  # the right approval, taken before the bad line: only the line can refuse purge
        with open(self.journal(), "ab") as handle:
            handle.write(b"not json\n")
        before = self.tree()
        for argv in (["undo", BATCH, "--apply"], ["purge", BATCH, "--apply", "--approved-sha256", sha]):
            with self.subTest(command=argv[0]):
                code, _out, err = self.w.main(*argv)
                self.assertEqual((code, self.tree()), (2, before))
                self.assertRegex(err, r"invalid journal line \d+")

    def journal_lines(self):
        with open(self.journal(), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def write_journal(self, lines):
        with open(self.journal(), "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(json.dumps(line) + "\n" for line in lines)

    def append_begin_of_second(self, op):
        """A `begin` of `op` for the second row's reject, with no `end`: a step cut short."""
        lines = self.journal_lines()
        begin = next(line for line in lines if line["event"] == "begin" and line["original"] == self.second[0])
        self.write_journal(lines + [{**{k: v for k, v in begin.items() if k != "family"}, "op": op}])

    @contextlib.contextmanager
    def looks(self, path, fail_first=False):
        """The `stat` calls on `path`; with `fail_first` the first one raises, as a lock or a flaky drive does."""
        calls, real = [], self.w.stat

        def stat(target):
            if os.path.normcase(target) == os.path.normcase(path):
                calls.append(target)
                if fail_first and len(calls) == 1:
                    raise PermissionError(target)
            return real(target)
        with self.keywords(stat=stat):
            yield calls

    def test_rule41_an_entry_the_first_look_could_not_place_is_out_of_the_digest_and_the_deletion(self):
        self.append_begin_of_second("purge")
        with self.looks(self.second[0], fail_first=True):
            sha = self.purge_sha()
        with self.looks(self.second[0], fail_first=True):  # the error comes at the first look only
            code, out, _err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha)
        self.assertEqual(code, 6)
        self.assertIn(f"held {self.second[0]} unplaced", out)
        self.assertTrue(os.path.exists(self.staged(self.second[0])))
        self.assertFalse(os.path.exists(self.staged(self.first[0])))
        self.assertEqual(self.starts()[-1]["purge_sha256"], sha)

    def test_rule41_each_purge_command_places_each_entry_once(self):
        self.append_begin_of_second("purge")
        sha = self.purge_sha()
        for argv in (["purge", BATCH], ["purge", BATCH, "--apply", "--approved-sha256", sha]):
            with self.subTest(apply="--apply" in argv), self.looks(self.second[0]) as calls:
                self.w.main(*argv)
                self.assertEqual(len(calls), 1)

    def test_rule39_a_row_with_no_usable_family_is_exit_2_for_purge_and_undo_restores_as_before(self):
        sha = self.purge_sha()
        original = self.journal_lines()
        rows_at = [i for i, line in enumerate(original) if line["event"] == "begin" and "family" in line]
        at = rows_at[0]
        self.assertGreater(len(rows_at), 1)
        for name, family in (("missing", None), ("zero", 0), ("string", "2"), ("below the row's files", 1)):
            for bad in rows_at:  # each row in turn, the others valid: no check of the first row only
                files = sum(line["event"] == "begin" and line["row"] == original[bad]["row"] for line in original)
                if family == 1 and files == 1:
                    continue  # a family of 1 is not below a row of one file
                with self.subTest(name, bad_line=bad):
                    lines = [dict(line) for line in original]
                    lines[bad].pop("family")
                    if family is not None:
                        lines[bad]["family"] = family
                    self.write_journal(lines)
                    before = self.tree()
                    code, _out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha)
                    self.assertEqual((code, self.tree()), (2, before))
                    self.assertIn("family", err)
                    self.assertRegex(err, r"(?is)no valid file count.*nothing was deleted.*\bundo\b")
        lines = [dict(line) for line in original]
        lines[at].pop("family")
        self.write_journal(lines)  # undo never reads `family`: it is the way back from such a journal
        code, out, _err = self.w.main("undo", BATCH, "--apply")
        self.assertEqual((code, self.rows_in(out, "restored")), (0, 2))

    def test_rule41_purge_holds_an_open_stage_or_restore_step_with_its_file_in_staging(self):
        self.assertEqual(self.w.main("undo", BATCH, "--apply")[0], 0)
        restored = self.journal_lines()
        self.append_begin_of_second("stage")  # a stage step with no `end`; its file is at the original
        code, out, _err = self.w.main("purge", BATCH)
        self.assertEqual(code, 0)  # never carried out: nothing to purge and nothing to undo
        self.assertRegex(out, r"(?is)nothing to purge.*never carried out.*original path")
        self.assertNotRegex(out, r"(?i)\bundo\b.*places")

        source = self.second[0]
        staged = self.staged(source)
        advice = "an open stage or restore step remains in staging; undo places it"
        for op in ("stage", "restore"):
            with self.subTest(op=op):
                os.replace(source, staged)
                code, out, err = self.w.main("purge", BATCH)
                self.assertEqual(code, 6)
                self.assertIn(f"held {source} family_incomplete", out)
                self.assertEqual(out.splitlines().count(advice), 1)
                self.assertLess(out.index(f"held {source}"), out.index(advice))
                self.assertIn("1 row(s) held", err)
                self.assertIn("files that --apply would delete:\ntotal 0 bytes would be freed", out)
                self.assertIn(hashlib.sha256(b"").hexdigest(), out)
            if op == "stage":
                self.write_journal(restored)
                os.replace(staged, source)
                self.append_begin_of_second("restore")

        results_name = f"purge-{BATCH}.csv"
        code, out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", self.purge_sha())
        self.assertEqual(code, 6)
        self.assertIn("1 row(s) held", err)
        self.assertTrue(os.path.exists(staged))
        self.assertFalse(os.path.exists(source))
        row, = self.csv_rows(results_name)
        self.assertEqual((row["source"], row["state"], row["reason"]), (source, "held", "family_incomplete"))
        self.assertIn(f"batch {BATCH}", out)
        self.assertIn(f"journal {self.journal()}", out)
        self.assertIn(f"results {os.path.join(self.out, results_name)}", out)

        self.assertEqual(self.w.main("undo", BATCH, "--apply")[0], 0)
        self.assertTrue(os.path.exists(source))
        self.assertFalse(os.path.exists(staged))
        code, out, _err = self.w.main("purge", BATCH)
        self.assertEqual(code, 0)
        self.assertIn(f"no file of batch {BATCH} is staged any more", out)

    def test_rule40_undo_of_a_stage_step_that_never_ran_skips_the_row_and_says_why(self):
        self.assertEqual(self.w.main("undo", BATCH, "--apply")[0], 0)
        self.append_begin_of_second("stage")  # the stalled stage: begun, its rename never ran
        for argv in (["undo", BATCH], ["undo", BATCH, "--apply"]):
            with self.subTest(command=argv):
                code, out, _err = self.w.main(*argv)
                self.assertEqual(code, 0)
                self.assertRegex(out, rf"(?m)^\d+ skipped {re.escape(self.second[0])} \S+")
                self.assertRegex(out, r"(?im)^\S+: .*never moved.*nothing to restore")
                self.assertTrue(os.path.exists(self.second[0]))

    def test_rule44_a_failed_rename_back_stops_undo_with_exit_6_naming_the_journal(self):
        def refuse(src, dst):
            raise OSError("rename refused")
        with mock.patch.object(self.w, "rename", refuse):
            code, _out, err = self.w.main("undo", BATCH, "--apply")
        self.assertEqual(code, 6)
        self.assertIn(self.journal(), err)
        self.assertIn("rename refused", err)

    def test_rule44_an_oserror_of_a_remove_stops_purge_with_exit_6_naming_the_journal(self):
        sha = self.purge_sha()

        def refuse(path):
            raise OSError("remove refused")
        with self.keywords(remove=refuse):
            code, _out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha)
        self.assertEqual(code, 6)
        self.assertIn(self.journal(), err)
        self.assertTrue(os.path.exists(self.staged(self.first[0])))

    def starts(self):
        with open(self.journal(), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if json.loads(line)["event"] == "start"]

    def test_rule41_the_start_line_of_purge_holds_the_purge_set_the_owner_approved(self):
        sha = self.purge_sha()
        self.assertEqual(self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha.upper())[0], 0)
        starts = self.starts()
        self.assertEqual([start["op"] for start in starts], ["stage", "purge"])
        self.assertEqual((starts[1]["batch"], starts[1]["purge_sha256"]), (BATCH, sha))

    def test_rule41_the_start_line_of_undo_names_its_op_and_batch(self):
        self.assertEqual(self.w.main("undo", BATCH, "--apply")[0], 0)
        starts = self.starts()
        self.assertEqual([(start["op"], start["batch"]) for start in starts], [("stage", BATCH), ("undo", BATCH)])

    def test_rule33_a_journal_original_outside_photo_root_is_exit_2_for_purge_and_deletes_nothing(self):
        outside = os.path.join(self.w.archive_roots[0], "elsewhere.ARW")
        with open(self.journal(), encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle]
        for line in lines:
            if line.get("original") == self.second[0]:
                line["original"] = outside
        with open(self.journal(), "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(json.dumps(line) + "\n" for line in lines)
        ctx = self.w.ctx()
        sha = mover_purge.purge_sha256(
            mover_purge.look_all(mover_journal.entries(mover_journal.read_journal(self.journal(), ctx)), ctx))
        before = self.tree()
        code, _out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha)
        self.assertEqual((code, self.tree()), (2, before))
        self.assertIn("outside photo_root", err)

    def test_rule33_purge_with_two_archive_roots_on_one_volume_is_exit_2_and_deletes_nothing(self):
        sha = self.purge_sha()
        before = self.tree()
        with self.keywords(volume_of=lambda path: "one"):
            code, _out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha)
        self.assertEqual((code, self.tree()), (2, before))
        self.assertIn("archive_roots", err)

    def test_rule44_a_stall_of_purge_is_exit_5_and_deletes_nothing(self):
        sha = self.purge_sha()
        target = self.staged(self.first[0])
        patch, finish = self.stall_on(target)
        with patch:
            code, out, _err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha,
                                          "--stall-seconds", "10")
        finish()
        self.assertEqual((code, self.w.aborted, os.path.exists(target)), (5, [5], True))
        self.assertIn(f"batch {BATCH}", out)  # the stalled run names the batch and the journal it left
        self.assertIn(self.journal(), out)
        self.assertFalse(os.path.exists(os.path.join(self.out, f"purge-{BATCH}.csv")))

    def test_rule44_a_bad_batch_a_missing_journal_and_a_journal_without_a_start_line_are_exit_2(self):
        other = "20260102T000000Z"
        with open(self.journal(other), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"event": "stalled", "at": "t"}) + "\n")
        shutil.copy(self.journal(), self.journal("nonsense"))  # a real journal under a name that is no batch
        before = self.tree()
        for batch in ("nonsense", "../x", "20260103T000000Z", other):
            for argv in (["undo", batch, "--apply"], ["purge", batch, "--apply", "--approved-sha256", self.sha]):
                with self.subTest(batch=batch, command=argv[0]):
                    self.assertEqual((self.w.main(*argv)[0], self.tree()), (2, before))


class MoverOutputTests(MoverCase):
    """RC-95: what a person reads to carry on: the batch, the files, the total, the reasons."""

    def files(self):
        return (self.first[0], *self.first[3], self.second[0])

    def test_stage_apply_ends_with_the_batch_the_journal_and_the_results_file(self):
        code, out, _err = self.stage()
        self.assertEqual(code, 0)
        self.assertIn(f"batch {BATCH}", out)
        self.assertIn(self.journal(), out)
        self.assertIn(os.path.join(self.out, f"stage-{BATCH}.csv"), out)

    def test_stage_dry_run_and_apply_list_every_file_of_every_row_sidecars_included(self):
        for apply in (False, True):  # the dry run changes nothing, so the apply that follows is the first batch
            code, out, _err = self.stage(apply=apply)
            self.assertEqual(code, 0)
            for path in self.files():
                with self.subTest(apply=apply, path=path):
                    self.assertIn(f"    {path}", out)

    def test_a_held_row_ends_with_a_stderr_line_and_one_sentence_for_its_reason_code(self):
        os.utime(self.first[3][0], ns=(MTIME, MTIME))
        os.utime(self.second[0], ns=(MTIME, MTIME))  # two rows held for the one reason
        code, out, err = self.stage()
        self.assertEqual(code, 6)
        self.assertRegex(err, r"^2 row")
        self.assertEqual(err.splitlines()[-1],
                         f"read out/stage-{BATCH}.csv for each row; held rows stay where they are")
        self.assertEqual(self.rows_in(out, "held"), 2)
        self.assertEqual(sum(line.startswith("changed:") for line in out.splitlines()), 1)

    def test_a_clean_run_writes_nothing_to_stderr_and_no_reason_sentence(self):
        code, out, err = self.stage()
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("changed:", out)

    def test_an_unknown_only_says_the_row_is_named_by_its_source_as_written(self):
        code, _out, err = self.stage("--only", "shoot-a/IMG_0002.ARW", apply=False)
        self.assertEqual(code, 2)
        self.assertIn("shoot-a/img_0002.arw", err)
        self.assertIn("source", err)
        self.assertIn("full path", err)

    def staged_files(self):
        return [self.staged(path) for path in self.files()]

    def test_undo_apply_ends_with_the_batch_and_the_journal(self):
        self.stage()
        code, out, _err = self.w.main("undo", BATCH, "--apply")
        self.assertEqual(code, 0)
        self.assertIn(f"batch {BATCH}", out)
        self.assertIn(self.journal(), out)

    def test_undo_dry_run_prints_each_rename_and_each_refusal(self):
        self.stage()
        code, out, _err = self.w.main("undo", BATCH)
        self.assertEqual(code, 0)
        for path in self.files():
            with self.subTest(path=path):
                self.assertIn(f"{self.staged(path)} -> {path}", out)
        self.unplace_second()
        code, out, err = self.w.main("undo", BATCH)
        self.assertEqual(code, 6)
        self.assertIn(f"refused {self.second[0]} unplaced", out)
        self.assertRegex(err, r"^1 row")
        self.assertEqual(sum(line.startswith("unplaced:") for line in out.splitlines()), 1)

    def test_undo_where_every_row_is_refused_and_still_staged_prints_no_nothing_to_undo(self):
        self.stage()
        for path in (self.first[0], self.second[0]):
            os.utime(self.staged(path), ns=(MTIME, MTIME))  # the staged file is no longer the one that was staged
        code, out, err = self.w.main("undo", BATCH)
        self.assertEqual(code, 6)
        self.assertEqual(self.rows_in(out, "refused"), 2)
        self.assertRegex(err, r"^2 row")
        self.assertNotIn("nothing to undo", out)

    def test_undo_of_a_finished_batch_says_there_is_nothing_to_do_and_gives_no_apply_hint(self):
        self.stage()
        self.w.main("undo", BATCH, "--apply")
        code, out, _err = self.w.main("undo", BATCH)
        self.assertEqual(code, 0)
        self.assertIn("nothing to undo", out)
        self.assertNotIn("--apply", out)

    def test_purge_dry_run_lists_every_staged_file_with_its_bytes_the_total_and_the_sha(self):
        self.stage()
        code, out, _err = self.w.main("purge", BATCH)
        self.assertEqual(code, 0)
        sizes = {path: os.path.getsize(path) for path in self.staged_files()}
        self.assertEqual(len(sizes), 3)  # the sidecar is a file of the set too
        for path, size in sizes.items():
            with self.subTest(path=path):
                self.assertIn(f"    {path} {size} bytes", out)
        self.assertEqual(int(re.search(r"total (\d+) bytes", out).group(1)), sum(sizes.values()))
        self.assertEqual(re.findall(r"[0-9a-f]{64}", out), [self.purge_sha()])  # the set's SHA-256, printed once

    def test_the_purge_dry_run_heads_the_delete_list_and_says_contents_are_hashed_only_with_apply(self):
        self.stage()
        code, out, _err = self.w.main("purge", BATCH)
        self.assertEqual(code, 0)
        first_file = min(out.index(f"    {path} ") for path in self.staged_files())
        self.assertRegex(out[:first_file], r"(?m)^files that --apply would delete:$")
        self.assertRegex(out, r"(?i)hashed only with --apply.*hold a row marked would_purge")

    def test_purge_dry_run_totals_only_the_would_purge_files_and_lists_held_files_apart(self):
        self.stage()
        os.utime(self.staged(self.first[0]), ns=(MTIME, MTIME))  # row 1 is held: its staged file changed
        code, out, _err = self.w.main("purge", BATCH)
        self.assertEqual(code, 6)
        kept = os.path.getsize(self.staged(self.second[0]))
        self.assertEqual(int(re.search(r"total (\d+) bytes", out).group(1)), kept)
        total_at = out.index("total ")
        self.assertLess(out.index(f"    {self.staged(self.second[0])} "), total_at)
        for path in (self.first[0], *self.first[3]):
            with self.subTest(path=path):
                held_at = out.index(f"    {self.staged(path)} ")
                self.assertGreater(held_at, total_at)
        self.assertRegex(out, r"held, not deleted: row 0 staged_changed")
        self.assertRegex(out, r"covers the whole purge set.*held rows' files included")
        self.assertEqual(re.findall(r"[0-9a-f]{64}", out), [self.purge_sha()])

    def test_purge_apply_ends_with_the_batch_the_journal_and_the_results_file(self):
        self.stage()
        sha = self.purge_sha()
        code, out, _err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha)
        self.assertEqual(code, 0)
        self.assertIn(f"batch {BATCH}", out)
        self.assertIn(self.journal(), out)
        self.assertIn(os.path.join(self.out, f"purge-{BATCH}.csv"), out)

    def test_purge_of_a_finished_batch_says_there_is_nothing_to_do_and_gives_no_approval_hint(self):
        self.stage()
        self.w.main("purge", BATCH, "--apply", "--approved-sha256", self.purge_sha())
        code, out, _err = self.w.main("purge", BATCH)
        self.assertEqual(code, 0)
        self.assertIn("nothing to purge", out)
        self.assertNotIn("open stage step", out)
        self.assertNotIn("--apply", out)

    def test_a_held_purge_ends_with_a_stderr_line_and_the_reason_sentence(self):
        self.stage()
        self.unplace_second()
        code, out, err = self.w.main("purge", BATCH, "--apply", "--approved-sha256", self.purge_sha())
        self.assertEqual(code, 6)
        self.assertRegex(err, r"^1 row")
        self.assertEqual(sum(line.startswith("unplaced:") for line in out.splitlines()), 1)


class MoverSeamsTests(MoverCase):
    def test_world_main_hands_cli_main_every_seam_of_the_world(self):
        with mock.patch.object(cli, "main", return_value=0) as patched:
            self.w.main("undo", BATCH)
        keywords = patched.call_args.kwargs
        for name in ("is_link", "remove", "rmdir", "makedirs", "listdir", "append", "write", "rename", "opener"):
            with self.subTest(seam=name):
                self.assertEqual(keywords[name], getattr(self.w, name))

    def test_main_builds_the_seams_the_commands_use_from_its_keywords(self):
        used = set()
        names = ("is_link", "remove", "rmdir", "makedirs", "listdir", "append", "write", "rename")
        for name in names:
            real = getattr(self.w, name)
            setattr(self.w, name, lambda *a, _n=name, _r=real, **k: used.add(_n) or _r(*a, **k))
        built, real_new_ctx = [], cli.mover_seams.new_ctx
        with mock.patch.object(cli.mover_seams, "new_ctx",
                               lambda seams, *rest: built.append(seams) or real_new_ctx(seams, *rest)):
            self.assertEqual(self.stage()[0], 0)
            self.assertEqual(self.w.main("undo", BATCH)[0], 0)
            os.remove(self.summary)
            sha = self.purge_sha()
            self.assertEqual(self.w.main("purge", BATCH, "--apply", "--approved-sha256", sha)[0], 0)
        self.assertEqual(used, set(names))
        self.assertTrue(built)
        for seams in built:  # all 14 fields are the World's (the wrapped ones included)
            for field in cli.mover_seams.Seams._fields:
                with self.subTest(seam=field):
                    self.assertEqual(getattr(seams, field), getattr(self.w.seams(), field))


EXTENDED = "\\\\?\\"
FILE_LIMIT, FOLDER_LIMIT = 260, 248  # an unprefixed path this long fails on a machine without long-path support


class LongPathTests(MoverCase):
    def refusing(self, links=(), **replaced):
        """The World's path seams, each refusing an unprefixed path at the MAX_PATH limits and taking the prefix off.

        `links` holds plain folder paths that are junctions; is_link sees them only through a path it can open."""
        def wrap(name, limit):
            real = getattr(self.w, name)

            def call(*args, **kwargs):
                for path in args:
                    if not path.startswith(EXTENDED) and len(path) >= limit:
                        raise FileNotFoundError(path)
                return real(*(path.removeprefix(EXTENDED) for path in args), **kwargs)
            return call
        limits = {"stat": FILE_LIMIT, "rename": FILE_LIMIT, "remove": FILE_LIMIT, "rmdir": FOLDER_LIMIT,
                  "makedirs": FOLDER_LIMIT, "listdir": FOLDER_LIMIT}
        link = self.w.is_link  # the real one reads a path it cannot open as no link
        reads = lambda path: path.startswith(EXTENDED) or len(path) < FOLDER_LIMIT  # noqa: E731
        seen = lambda path: reads(path) and (path.removeprefix(EXTENDED) in links or link(path.removeprefix(EXTENDED)))  # noqa: E731
        return self.keywords(**replaced, **{name: wrap(name, limit) for name, limit in limits.items()}, is_link=seen)

    def deep_reject(self):
        """A reject under six folders of 40 characters: the original passes 260, the staged folders pass 248 and 260."""
        folders = [chr(ord("a") + n) * 40 for n in range(6)]
        reject = self.w.add("/".join(["shoot-a", *folders, "IMG_0001.ARW"]), b"long", MTIME)
        self.w.archive(reject)
        dests = tuple(os.path.join(root, os.path.relpath(reject, self.w.photo_root)) for root in self.w.archive_roots)
        row = (reject, os.stat(reject).st_size, MTIME, (), 0, dests, "")
        self.list_path, self.sha = self.removal_list([row])
        staged = self.staged(reject)
        self.assertGreater(len(os.path.dirname(reject)), FILE_LIMIT - 20)
        self.assertGreater(len(reject), FILE_LIMIT)
        self.assertGreater(len(os.path.dirname(staged)), FILE_LIMIT)
        return reject, staged

    def test_rc108_a_deep_path_goes_through_stage_undo_stage_and_purge(self):
        reject, staged = self.deep_reject()
        with self.refusing():
            self.assertEqual(self.stage()[0], 0)
            self.assertEqual((os.path.exists(reject), os.path.exists(staged)), (False, True))
            self.assertEqual(self.w.main("undo", BATCH, "--apply")[0], 0)
            self.assertEqual((os.path.exists(reject), os.path.exists(staged)), (True, False))
        later = "20260101T000001Z"
        staged = self.staged(reject).replace(BATCH, later)
        with self.refusing(now=lambda: "2026-01-01T00:00:01Z"):  # a batch is staged once: the second stage is a new one
            self.assertEqual(self.stage()[0], 0)
            self.assertEqual((os.path.exists(reject), os.path.exists(staged)), (False, True))
            self.assertEqual(self.w.main("purge", later, "--apply", "--approved-sha256", self.purge_sha(later))[0], 0)
        self.assertFalse(os.path.exists(reject) or os.path.exists(staged))
        left = [name for _folder, _dirs, names in os.walk(os.path.join(self.w.base, "work", "staging")) for name in names]
        self.assertEqual(left, [])
        # _prune runs under suppress(OSError): a folder it could not reach is the only sign of a call without the prefix
        self.assertEqual(os.listdir(os.path.join(self.w.base, "work", "staging", later)), [])

    def test_rc108_a_link_in_a_long_folder_of_the_original_holds_the_undo_and_is_not_nothing_to_undo(self):
        reject, staged = self.deep_reject()
        links = set()
        with self.refusing(links):
            self.assertEqual(self.stage()[0], 0)
            links.add(os.path.dirname(reject))  # a junction below 248 characters would be read plainly; this one is not
            self.assertGreater(len(os.path.dirname(reject)), FOLDER_LIMIT)
            code, out, _err = self.w.main("undo", BATCH, "--apply")
        self.assertEqual(code, 6)
        self.assertIn("parent_link", out)
        self.assertNotIn("nothing to undo", out)  # the staged file is still there: _is_staged reads the long path
        self.assertEqual((os.path.exists(reject), os.path.exists(staged)), (False, True))

    def test_rc108_a_link_in_a_long_staging_folder_is_not_entered_or_removed_by_the_purge(self):
        reject, staged = self.deep_reject()
        links = set()
        with self.refusing(links):
            self.assertEqual(self.stage()[0], 0)
            links.add(os.path.dirname(staged))
            self.assertGreater(len(os.path.dirname(staged)), FOLDER_LIMIT)
            self.assertEqual(self.w.main("purge", BATCH, "--apply", "--approved-sha256", self.purge_sha())[0], 0)
        self.assertFalse(os.path.exists(staged))
        self.assertTrue(os.path.isdir(os.path.dirname(staged)))


class ExitCodeTests(RejectsCase):
    """A command never ends in a traceback where the guide promises an exit code."""

    def missing_drive(self):
        for letter in "ZYXWVUTSRQPONMLKJIHGFED":
            if not os.path.exists(f"{letter}:\\"):
                return f"{letter}:\\arch"
        self.skipTest("every drive letter exists")

    def listings(self):
        return [sorted((d, tuple(sorted(f))) for d, _, f in os.walk(root) if not d.startswith(self.base))
                for root in (self.lib, self.cats, *self.arch)]

    def occupy(self, relative, as_dir):
        """Put a folder, or a file, where the tool folder's `relative` path must be written; undone at the end."""
        path = os.path.join(self.base, relative)
        saved = None
        if os.path.exists(path):
            with open(path, "rb") as handle:
                saved = handle.read()
            os.remove(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if as_dir:
            os.makedirs(path)
        else:
            synth.make_tree(os.path.dirname(path), {os.path.basename(path): b"x"})

        def undo():
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            if saved is not None:
                synth.make_tree(os.path.dirname(path), {os.path.basename(path): saved})
        return undo

    def tasklist_fails(self, error):
        def fail(*args, **kwargs):
            raise error
        return mock.patch("lrclean.catalogs.subprocess.run", fail)

    def test_each_situation_ends_with_its_documented_code_naming_its_root_or_file(self):
        manifest = os.path.join(self.out_dir(), "backup-manifest-1.csv")
        gone = self.missing_drive()
        stranded = os.path.join(self.top, "stranded-manifest.csv")
        source = os.path.join(self.lib, "shoot-a", "IMG_0001.ARW")
        dest = os.path.join(self.arch[0], "shoot-a", "IMG_0001.ARW")
        database = os.path.join(self.base, "work", "lrclean.sqlite")

        def no_free_space_read(root):
            self.fail(f"free space requested for {root}")

        backup_plan.write_manifest(stranded, [backup_plan.Row(
            source, os.path.join(gone, "shoot-a", "IMG_0001.ARW"), 100, os.stat(source).st_mtime_ns, "copy", "")])
        cases = (
            ("backup-plan on a missing drive", ("backup-plan",), 2, gone, {"archive_roots": [gone]}, None, {}),
            ("backup-plan, only the second root on a missing drive", ("backup-plan",), 2, gone,
             {"archive_roots": [self.arch[0], gone]}, None, {}),
            ("backup-run on a missing drive", ("backup-run", stranded), 2, gone,
             {"archive_roots": [gone]}, None, {"free_bytes": no_free_space_read}),
            ("backup-run, the manifest's own second drive is missing", ("backup-run", stranded), 2, gone,
             {"archive_roots": [self.arch[0], gone]}, None, {"free_bytes": no_free_space_read}),
            ("backup-run --apply on a missing drive", ("backup-run", stranded, "--apply"), 2, gone,
             {"archive_roots": [gone]}, None, {"free_bytes": no_free_space_read}),
            ("backup-run while the other drive is missing", ("backup-run", manifest), 0,
             f"0 dest_exists {dest}", {"archive_roots": [self.arch[0], gone]}, None,
             {"free_bytes": {self.arch[0]: 1000}.__getitem__}),
            ("backup-run --apply, journal folder is a file", ("backup-run", manifest, "--apply"), 6,
             "journal", {}, ("work/journal", False), {}),
            ("backup-verify, list is a folder", ("backup-verify", manifest), 6,
             "backup-verified-1.csv", {}, ("out/backup-verified-1.csv", True), {}),
            ("rejects, output is a folder", ("rejects",), 6, "rejects.csv.tmp", {},
             ("out/rejects.csv.tmp", True), {}),
            ("catalogs, tasklist fails", ("catalogs",), 3, "cannot list processes", {}, None,
             {"processes": catalogs.running_processes, "error": subprocess.CalledProcessError(1, "tasklist")}),
            ("catalogs, tasklist times out", ("catalogs",), 3, "cannot list processes", {}, None,
             {"processes": catalogs.running_processes, "error": subprocess.TimeoutExpired("tasklist", 30)}),
            ("catalogs, tasklist fails before snapshot", ("catalogs",), 3, "cannot list processes", {}, None,
             {"processes": mock.Mock(side_effect=[["explorer.exe"], RuntimeError("cannot list processes")])}),
            ("catalogs, work database is not a database", ("catalogs",), 6, database, {},
             ("work/lrclean.sqlite", False), {}),
            ("catalogs, work database is a folder", ("catalogs",), 6, database, {},
             ("work/lrclean.sqlite", True), {}),
        )
        for name, argv, code, named, config, occupied, extra in cases:
            with self.subTest(name):
                self.write_config({**self.config, **config})
                self.steps()
                self.assertEqual(self.run_cli("inventory")[0], 0)
                undo = self.occupy(*occupied) if occupied else lambda: None
                before = self.listings()
                injected = {"volume_of": lambda p: self.volumes.get(p, "z:")}
                error = extra.get("error")
                injected.update((key, value) for key, value in extra.items() if key != "error")
                try:
                    with self.tasklist_fails(error) if error else contextlib.nullcontext():
                        got, err = self.run_cli(*argv, **injected)
                finally:
                    undo()
                self.assertEqual(got, code, err)
                if code == 0:
                    self.assertEqual(err, "")
                    self.assertEqual(self.stdout.splitlines(), [named])
                else:
                    self.assertIn(named, err)
                self.assertNotIn("Traceback", err)
                self.assertEqual(self.listings(), before)


if __name__ == "__main__":
    unittest.main()
