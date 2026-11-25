"""End to end: the real modules joined on one synthetic tree, through `cli.main`."""

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import unittest

import synth
from lrclean import backup, cli, file_views, paths, report
from mover_world import BATCH, World

NOW = "2026-01-01T00:00:00Z"
PLACEHOLDER = 0x400000
SIZE_A, SIZE_B = 200, 150
CACHE_BYTES = 75


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _raise(error):
    raise error


def listing(roots, skip):
    """(path, size, mtime, sha256) of every file and folder below the roots, leaving out `skip`."""
    seen = []
    for root in roots:
        for here, folders, names in os.walk(root, onerror=_raise):
            folders[:] = [d for d in folders if os.path.join(here, d) != skip]
            for name in folders + names:
                full = os.path.join(here, name)
                st = os.stat(full)
                if os.path.isdir(full):
                    digest = ""
                else:
                    with open(full, "rb") as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                seen.append((full, st.st_size, st.st_mtime_ns, digest))
    return sorted(seen)


class E2eCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        top = self.top = tmp.name
        self.lib = os.path.join(top, "lib")
        self.backup = os.path.join(top, "backup")
        self.cats = os.path.join(top, "cats")
        self.base = os.path.join(self.lib, "tool")  # inside a scanned root (rule 14)
        self.photos = os.path.join(self.lib, "photos")
        self.live = os.path.join(self.cats, "live.lrcat")
        self.shoot = os.path.join(self.cats, "shoot-a.lrcat")
        self.broken = os.path.join(self.cats, "broken.lrcat")
        self.keeper = os.path.join(self.photos, "shoot-a", "IMG_0001.ARW")
        self.twin = os.path.join(self.photos, "other", "IMG_0001.ARW")
        self.placeholder = os.path.join(self.photos, "misc", "IMG_0300.ARW")
        self.build_tree()
        self.config = {
            "library_roots": [self.lib], "backup_roots": [self.backup],
            "catalog_roots": [self.cats], "photo_root": self.photos, "group_depth": 1,
            "exclude": [], "live_catalog": self.live, "old_states": [],
            "excluded_catalogs": [self.broken],
        }
        self.write_config(self.config)

    def build_tree(self):
        pair = os.path.join(self.photos, "pairs")
        synth.make_tree(self.photos, {
            "shoot-a/IMG_0001.ARW": b"a" * SIZE_A,           # recorded keeper
            "other/IMG_0001.ARW": b"a" * SIZE_A,             # unrecorded identical twin
            "shoot-a/IMG_0002.ARW": b"b" * SIZE_B,           # recorded, with a hard link
            "pairs/IMG_0100.ARW": b"p" * 120,                # raw+JPEG pair
            "pairs/IMG_0100.JPG": b"q" * 40,
            "misc/IMG_0300.ARW": b"c" * 80,                  # placeholder (through attrs_of)
            "mobile/Mobile Downloads.lrdata/IMG_0400.JPG": b"m" * 30,
            "shoot-a/Lightroom Previews.lrdata/p1.lrprev": b"x" * 50,
            "shoot-a/Lightroom Previews.lrdata/p2.lrprev": b"y" * (CACHE_BYTES - 50),
        })
        synth.make_tree(self.lib, {"loose/IMG_0200.JPG": b"l" * 60})
        synth.make_tree(self.backup, {"2024/IMG_0001.ARW": b"a" * SIZE_A})
        os.makedirs(os.path.join(self.photos, "links"))
        os.link(os.path.join(self.photos, "shoot-a", "IMG_0002.ARW"),
                os.path.join(self.photos, "links", "IMG_0002-link.ARW"))
        stamp = 1_700_000_000_000_000_000
        for name in ("IMG_0100.ARW", "IMG_0100.JPG"):
            os.utime(os.path.join(pair, name), ns=(stamp, stamp))
        os.makedirs(self.cats)
        root = self.lib.replace("\\", "/") + "/"

        def entry(folder, base):
            return {"root": root, "root_relative": None, "folder": folder, "base": base}

        synth.make_catalog(self.shoot, [entry("photos/shoot-a/", "IMG_0001"),
                                        entry("photos/shoot-a/", "IMG_0002")])
        synth.make_catalog(self.live, [entry("photos/gone/", "IMG_0900")])
        synth.make_unreadable(self.broken)
        self.unclean = os.path.join(self.cats, "unclean.lrcat")
        synth.make_unclean(self.unclean, [entry("photos/shoot-a/", "IMG_0001")])

    def write_config(self, config):
        os.makedirs(os.path.join(self.base, "work"), exist_ok=True)
        with open(os.path.join(self.base, "work", "config.json"), "w", encoding="utf-8",
                  newline="\n") as handle:
            json.dump(config, handle)

    def attrs_of(self, path, st):
        return PLACEHOLDER if path == self.placeholder else 0

    def run_cli(self, *argv, **seams):
        injected = {"processes": lambda: ["explorer.exe"], "now": lambda: NOW,
                    "attrs_of": self.attrs_of, **seams}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--base", self.base, *argv], **injected)
        self.stdout = out.getvalue()
        return code, err.getvalue()

    def run_steps(self, *steps):
        for step in steps:
            self.assertEqual(self.run_cli(*step.split()), (0, ""), step)

    def run_to_hash(self):
        self.run_steps("catalogs", "inventory", "hash")

    def run_everything(self):
        self.run_to_hash()
        self.run_steps("report")
        self.assertEqual(self.run_cli("verify", os.path.join(self.photos, "shoot-a")), (0, ""))
        self.run_steps("report")

    def out(self, *parts):
        return os.path.join(self.base, "out", *parts)

    def section(self, number):
        return read_csv(self.out(report.SECTIONS[number].filename))

    def only(self, number, **match):
        rows = [r for r in self.section(number) if all(r[k] == v for k, v in match.items())]
        self.assertEqual(len(rows), 1, (number, match, self.section(number)))
        return rows[0]

    def out_bytes(self):
        found = {}
        for name in sorted(os.listdir(self.out())):
            with open(self.out(name), "rb") as handle:
                found[name] = handle.read()
        return found

    def summary(self):
        with open(self.out("summary.md"), encoding="utf-8") as handle:
            return handle.read()

    def query(self, sql, *args):
        con = sqlite3.connect(os.path.join(self.base, "work", "lrclean.sqlite"))
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()


class FullRunTests(E2eCase):
    def test_every_subcommand_then_one_expected_row_in_each_csv(self):
        self.run_to_hash()
        self.run_steps("report")
        self.assertEqual(len(os.listdir(self.out())), 12)
        self.assertEqual(self.only(5, kind="year", folder="shoot-a")["copied_sampled"], "1")
        self.assertEqual(self.run_cli("verify", os.path.join(self.photos, "shoot-a")), (0, ""))
        self.run_steps("report")

        self.assertEqual(self.only(1, catalog=self.broken)["status"], "unreadable")
        self.assertEqual(self.only(1, catalog=self.broken)["excluded_by_owner"], "yes")
        shoot_row = self.only(1, catalog=self.shoot)
        self.assertEqual((shoot_row["role"], shoot_row["status"], shoot_row["records"],
                          shoot_row["excluded_by_owner"]), ("shoot", "ok", "2", "no"))
        self.assertEqual(self.only(1, catalog=self.live)["records"], "1")
        self.assertEqual(self.only(2, catalog=self.shoot)["records_present"], "2")
        safe = self.only(3)
        self.assertEqual((safe["path"], safe["twin_path"], safe["bytes"]),
                         (self.twin, self.keeper, str(SIZE_A)))
        linked = [r for r in self.section(4) if r["kind"] == "same_file"]
        self.assertEqual(sorted(os.path.basename(r["path"]) for r in linked),
                         ["IMG_0002-link.ARW", "IMG_0002.ARW"])
        self.assertEqual(len({r["group"] for r in linked}), 1)
        year = self.only(5, kind="year", folder="shoot-a")
        self.assertEqual((year["copied_verified"], year["copied_sampled"], year["no_copy"]),
                         ("1", "0", "1"))
        pair = self.only(6, folder=os.path.join(self.photos, "pairs"))
        self.assertEqual((pair["pairs"], pair["jpeg_bytes"], pair["own_photo_pairs"],
                          pair["sidecar_pairs"], pair["uncatalogued_pairs"], pair["same_time_pairs"]),
                         ("1", "40", "0", "0", "1", "1"))
        self.assertEqual(self.only(7, folder=os.path.join(self.photos, "pairs"))["bytes"], "160")
        missing = self.only(8)
        self.assertEqual((missing["kind"], missing["state"]), ("photo", "missing"))
        self.assertTrue(missing["path"].endswith("/photos/gone/IMG_0900.arw"))
        cache = self.only(9, type="cache")
        self.assertEqual((cache["bytes"], cache["reclaimable"]), (str(CACHE_BYTES), "yes"))
        self.assertEqual(self.only(9, type="storage")["reclaimable"], "no")
        # a temp folder under an application folder (AppData, Windows, ...) is counted, never listed
        parts = re.split(r"[\\/]", self.lib)
        app_at = next((i for i, p in enumerate(parts) if p.casefold() in file_views.APP_FOLDER_NAMES), None)
        expected_folder = os.path.join(self.lib, "loose") if app_at is None else os.sep.join(parts[:app_at + 1])
        loose = self.only(10, bytes="60")
        self.assertEqual((loose["jpegs"], loose["raws"], loose["listed"], loose["folder"]),
                         ("1", "0", "no" if app_at is not None else "yes", expected_folder))
        self.assertEqual(self.only(11, kind="placeholder")["item"], self.placeholder)

    def test_catalog_not_owner_excluded_limits_coverage_rule_5(self):
        self.run_to_hash()
        self.write_config({**self.config, "excluded_catalogs": []})
        self.run_steps("report")
        self.assertEqual(self.section(3), [])
        row = self.only(4, kind="coverage_incomplete", path=self.twin)
        self.assertIn(self.broken, row["how_decided"])
        self.assertIn(f"- {self.broken}: unreadable (limits coverage)", self.summary())

    def test_changed_live_catalog_leaves_no_safe_candidate_rule_5(self):
        self.run_to_hash()
        st = os.stat(self.live)
        os.utime(self.live, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.run_steps("report")
        self.assertEqual(self.section(3), [])
        self.assertEqual(self.only(4, path=self.twin)["kind"], "coverage_incomplete")
        self.assertEqual(self.only(11, kind="limiting_catalog")["item"],
                         f"{self.live} (changed since its snapshot)")

    def test_catalog_saved_after_the_catalogs_step_leaves_no_safe_candidate(self):
        self.run_to_hash()
        late = os.path.join(self.cats, "late.lrcat")
        root = self.lib.replace("\\", "/") + "/"
        synth.make_catalog(late, [{"root": root, "root_relative": None,
                                   "folder": "photos/other/", "base": "IMG_0001"}])
        self.run_steps("report")
        cause = f"{late} (appeared since the catalogs scan)"
        self.assertEqual(self.section(3), [])
        self.assertEqual(self.only(4, path=self.twin)["kind"], "coverage_incomplete")
        self.assertIn(f"- {cause}", self.summary())
        self.assertEqual(self.only(11, kind="limiting_catalog", item=cause)["detail"], "")

    def test_keeper_deleted_after_hash_is_changed_since_scan_rule_13(self):
        self.run_to_hash()
        os.remove(self.keeper)
        self.run_steps("report")
        self.assertEqual(self.section(3), [])
        self.assertEqual(self.only(4, path=self.twin)["kind"], "changed_since_scan")


class ReadOnlyTests(E2eCase):
    def test_nothing_outside_the_base_changes_and_a_second_run_repeats_rules_14_15(self):
        roots = [self.top]
        before = listing(roots, self.base)
        listed = [row[0] for row in before]
        for suffix in ("-wal", "-shm", ".lock"):  # left behind, so a change to them is seen
            self.assertIn(self.unclean + suffix, listed)
        self.run_everything()
        first = self.out_bytes()
        self.run_everything()
        second = self.out_bytes()
        self.assertEqual(listing(roots, self.base), before)
        for found in (first, second):  # the summary names its scans by id, and a second run takes new ids
            found["summary.md"] = re.sub(rb"scan \d+ of", b"scan N of", found["summary.md"])
        self.assertEqual(second, first)
        self.assertEqual(self.query("SELECT COUNT(*) FROM file WHERE path_norm LIKE '%/tool/%'"),
                         [(0,)])
        ids = [r[0] for r in self.query("SELECT id FROM scan WHERE kind = 'catalogs' ORDER BY id")]
        self.assertEqual(len(ids), 2)
        sql = ("SELECT source_path, role, status, size, mtime_ns, db_version, "
               "(SELECT COUNT(*) FROM record r WHERE r.catalog_id = c.id) "
               "FROM catalog c WHERE scan_id = ? ORDER BY source_path")
        self.assertEqual(self.query(sql, ids[0]), self.query(sql, ids[1]))


SELECTED_FILES = (
    "shoot-a/IMG_0001.ARW", "shoot-a/IMG_0002.ARW", "shoot-a/IMG_0001.xmp", "shoot-a/notes.txt",
    "other/IMG_0001.ARW", "pairs/IMG_0100.ARW", "pairs/IMG_0100.JPG", "links/IMG_0002-link.ARW",
    "nonraw/IMG_0500.JPG", "mobile/Mobile Downloads.lrdata/IMG_0400.JPG",
)  # every file of every folder that holds a photo or video, the storage container included
STAMP = 1_650_000_000_000_000_000


class BackupEndToEndTests(E2eCase):
    def setUp(self):
        super().setUp()
        synth.make_tree(self.photos, {
            "shoot-a/IMG_0001.xmp": b"x" * 10, "shoot-a/notes.txt": b"n" * 12,
            "nonraw/IMG_0500.JPG": b"j" * 20,  # a folder with only a photo: selected
        })
        for here, _, names in os.walk(self.photos):
            for name in names:
                os.utime(os.path.join(here, name), ns=(STAMP, STAMP))
        self.archives = [os.path.join(self.top, "archive-one"), os.path.join(self.top, "archive-two")]
        for root in self.archives:
            os.makedirs(root)
        self.write_config({**self.config, "archive_roots": self.archives})
        self.writes = []  # every part file the engine opens for writing
        self.roots = [self.lib, self.cats, self.backup]  # what must never change, the base apart

    def volume_of(self, path):
        return "vol-1" if os.path.normcase(path).startswith(os.path.normcase(self.archives[0])) else "vol-2"

    def part_writer(self, path):
        self.writes.append(path)
        return backup.open_part(path)

    def backup_cli(self, *argv, **seams):
        self.aborted = []  # a clock that never moves and a recording abort: no stall can reach os._exit
        seams = {"volume_of": self.volume_of, "free_bytes": lambda path: 10 ** 12,
                 "writer": self.part_writer, "clock": lambda: 1.0, "abort": self.aborted.append, **seams}
        return self.run_cli(*argv, **seams)

    def manifest(self, n):
        return self.out(f"backup-manifest-{n}.csv")

    def plan(self):
        self.run_steps("catalogs", "inventory")
        self.assertEqual(self.backup_cli("backup-plan"), (0, ""))

    def apply(self, n, *extra):
        code = self.backup_cli("backup-run", self.manifest(n), "--apply", *extra)[0]
        rows = [line.split(" ", 2) for line in self.stdout.splitlines() if line[:1].isdigit()]
        return code, {dest: state for _, state, dest in rows}

    def archive_files(self, root):
        return sorted(os.path.relpath(os.path.join(here, name), root).replace(os.sep, "/")
                      for here, _, names in os.walk(root) for name in names)

    def test_whole_run_leaves_the_library_unchanged_and_copies_every_selected_file_rule_17(self):
        before = listing(self.roots, self.base)
        self.plan()
        for n in (1, 2):
            self.assertEqual(self.backup_cli("backup-run", self.manifest(n))[0], 0)
            self.assertEqual(self.apply(n)[0], 0)
            self.assertEqual(self.backup_cli("backup-verify", self.manifest(n))[0], 0)
            states = {r["state"] for r in read_csv(self.out(f"backup-verified-{n}.csv"))}
            self.assertEqual(states, {"identical", "skipped_placeholder"})
        self.assertEqual(listing(self.roots, self.base), before)
        for root in self.archives:
            self.assertEqual(self.archive_files(root), sorted(SELECTED_FILES))
            for name in SELECTED_FILES:
                source, copy = os.path.join(self.photos, name), os.path.join(root, name)
                with open(source, "rb") as a, open(copy, "rb") as b:
                    self.assertEqual(a.read(), b.read(), name)
                self.assertEqual(os.stat(copy).st_mtime_ns, os.stat(source).st_mtime_ns, name)
        self.assertFalse(os.path.exists(os.path.join(self.archives[0], "misc")))  # the placeholder

    def test_second_apply_copies_nothing_and_a_changed_destination_is_a_conflict_rules_18_21_23(self):
        self.plan()
        self.assertEqual(self.apply(1)[0], 0)
        copied = len(self.writes)
        self.assertEqual(copied, len(SELECTED_FILES))
        code, states = self.apply(1)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.writes), copied)  # nothing copied again
        self.assertLessEqual(set(states.values()),
                             {"verified", "already_there", "skipped_placeholder"})
        target = os.path.join(self.archives[0], "pairs", "IMG_0100.ARW")
        with open(target, "r+b") as handle:
            handle.write(b"Z")
        with open(target, "rb") as handle:
            damaged = handle.read()
        self.assertEqual(self.backup_cli("backup-verify", self.manifest(1))[0], 6)
        different = [r["dest"] for r in read_csv(self.out("backup-verified-1.csv"))
                     if r["state"] == "different"]
        self.assertEqual(different, [target])
        code, states = self.apply(1)
        self.assertEqual(code, 6)
        self.assertEqual(states[target], "conflict")
        self.assertEqual(len(self.writes), copied)
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), damaged)

    def test_dry_run_and_plan_copy_nothing_rule_8(self):
        before = listing(self.roots, self.base)
        self.run_steps("catalogs", "inventory")
        self.assertEqual(listing(self.archives, ""), [])
        self.assertEqual(self.backup_cli("backup-plan"), (0, ""))
        self.assertEqual(listing(self.archives, ""), [])
        base_before = listing([self.base], "")
        self.assertEqual(self.backup_cli("backup-run", self.manifest(1))[0], 0)
        verdicts = {line.split(" ", 2)[1] for line in self.stdout.splitlines()}
        self.assertEqual(verdicts, {"copy", "skip_placeholder"})
        self.assertEqual(listing(self.archives, ""), [])
        self.assertEqual(listing([self.base], ""), base_before)  # no journal folder either
        self.assertFalse(os.path.exists(os.path.join(self.base, "work", "journal")))
        self.assertEqual(self.writes, [])
        self.assertEqual(listing(self.roots, self.base), before)

    def test_source_edited_after_the_plan_is_changed_and_has_no_destination_rule_21(self):
        self.plan()
        edited = os.path.join(self.photos, "other", "IMG_0001.ARW")
        with open(edited, "ab") as handle:
            handle.write(b"more")
        code, states = self.apply(1)
        self.assertEqual(code, 6)
        dest = os.path.join(self.archives[0], "other", "IMG_0001.ARW")
        self.assertEqual(states[dest], "changed")
        self.assertFalse(os.path.exists(dest))
        self.assertEqual({s for d, s in states.items() if d != dest} - {"verified", "skipped_placeholder"},
                         set())

    def test_source_edited_and_planned_again_is_a_conflict_not_verified_rule_21(self):
        self.plan()
        self.assertEqual(self.apply(1)[0], 0)
        edited = os.path.join(self.photos, "shoot-a", "notes.txt")
        with open(edited, "wb") as handle:
            handle.write(b"edited, and longer")
        os.utime(edited, ns=(STAMP + 5_000_000_000, STAMP + 5_000_000_000))
        self.assertEqual(self.backup_cli("backup-plan"), (0, ""))  # as a person must after a `changed` row
        dest = os.path.join(self.archives[0], "shoot-a", "notes.txt")
        with open(dest, "rb") as handle:
            old = handle.read()
        self.assertEqual(self.backup_cli("backup-run", self.manifest(1))[0], 0)
        dry = {d: s for _, s, d in (line.split(" ", 2) for line in self.stdout.splitlines() if line[:1].isdigit())}
        self.assertEqual(dry[dest], "dest_exists")
        code, states = self.apply(1)
        self.assertEqual(code, 6)
        self.assertEqual(states[dest], "conflict")
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), old)
        self.assertEqual(self.backup_cli("backup-verify", self.manifest(1))[0], 6)


SHOOT = "shoot-a"
SIDE_BYTES = 10
RAW_SIZES = {  # raws of `photos/shoot-a/`, one behaviour each, by base name
    "IMG_0001": 101, "IMG_0002": 102, "IMG_0003": 103, "IMG_0004": 104, "IMG_0005": 105,
    "IMG_0006": 106, "IMG_0007": 107, "IMG_0008": 108, "IMG_0010": 110,
    "IMG_0011": 111,
}


class RejectCheckEndToEndTests(E2eCase):
    """`catalogs` to `rejects` on one tree whose catalogs store their records as Lightroom does:
    one image row per file, `root_relative` None (the default "" adds a second path under the
    catalogs folder), a REAL pick, and the develop state of RC-18 (`hasDevelopAdjustments` NULL,
    `hasDevelopAdjustmentsEx` -1.0 for not edited, 1.0 for edited)."""

    def setUp(self):
        self.placeholders = set()  # normalised paths `attrs_of` reports as online-only
        super().setUp()
        self.archives = [os.path.join(self.top, "archive-one"), os.path.join(self.top, "archive-two")]
        for root in self.archives:
            os.makedirs(root)
        self.write_config({**self.config, "archive_roots": self.archives, "old_states": [self.old],
                           "excluded_catalogs": []})

    def build_tree(self):
        sizes = {f"{SHOOT}/{base}.ARW": b"r" * size for base, size in RAW_SIZES.items()}
        sizes[f"{SHOOT}/IMG_0001.xmp"] = b"x" * SIDE_BYTES
        for base, fill in (("IMG_0100", b"p"), ("IMG_0101", b"q")):
            sizes[f"pairs/{base}.ARW"] = fill * 120
            sizes[f"pairs/{base}.JPG"] = fill * 40
        synth.make_tree(self.photos, sizes)
        synth.make_tree(self.lib, {"elsewhere/IMG_0009.ARW": b"e" * 109})  # a raw outside the photo root
        os.makedirs(self.cats)
        self.live = os.path.join(self.cats, "live.lrcat")
        self.shoot_b = os.path.join(self.cats, "shoot-b.lrcat")
        self.old = os.path.join(self.cats, "live-copy.lrcat")
        root = self.lib.replace("\\", "/") + "/"

        def entry(folder, base, *, ext="ARW", pick=0.0, work=False, sidecars=""):
            return {"root": root, "root_relative": None, "folder": f"{folder}/", "base": base,
                    "ext": ext, "sidecars": sidecars, "pick": pick, "developed": None,
                    "developed_ex": 1.0 if work else -1.0}

        shoot = f"photos/{SHOOT}"
        synth.make_catalog(self.shoot, [
            entry(shoot, "IMG_0001", pick=-1.0, sidecars="xmp"),  # the `.xmp` is recorded here only
            entry(shoot, "IMG_0002", pick=-1.0), entry(shoot, "IMG_0003", pick=-1.0),
            entry(shoot, "IMG_0004", pick=-1.0, work=True),  # develop work in the catalog that rejects it
            *(entry(shoot, base, pick=-1.0) for base in ("IMG_0005", "IMG_0006", "IMG_0007",
                                                         "IMG_0008", "IMG_0010", "IMG_0011")),
            entry("elsewhere", "IMG_0009", pick=-1.0),
            *(entry("photos/pairs", base, ext=ext, pick=-1.0)
              for base in ("IMG_0100", "IMG_0101") for ext in ("ARW", "JPG")),
        ])
        synth.make_catalog(self.live, [  # the live catalog records the raws, and the JPEGs only as sidecars
            entry(shoot, "IMG_0001", pick=-1.0),
            entry(shoot, "IMG_0003", pick=0.0),  # kept in live
            *(entry("photos/pairs", base, pick=-1.0, sidecars="JPG") for base in ("IMG_0100", "IMG_0101")),
        ])
        synth.make_catalog(self.shoot_b, [entry(shoot, "IMG_0002", pick=0.0)])  # kept elsewhere
        synth.make_catalog(self.old, [entry("photos/pairs", "IMG_0100", work=True),  # an `old_states` copy
                                      entry(shoot, "IMG_0011", pick=1.0)])  # a pick of 1 only, no develop work

    def attrs_of(self, path, st):
        return PLACEHOLDER if paths.norm(path) in self.placeholders else 0

    def volume_of(self, path):
        return "vol-1" if os.path.normcase(path).startswith(os.path.normcase(self.archives[0])) else "vol-2"

    def backup_cli(self, *argv, **seams):
        self.aborted = []  # a clock that never moves and a recording abort: no stall can reach os._exit
        seams = {"volume_of": self.volume_of, "free_bytes": lambda path: 10 ** 12,
                 "clock": lambda: 1.0, "abort": self.aborted.append, **seams}
        return self.run_cli(*argv, **seams)

    def source(self, name):
        return os.path.join(self.photos, *name.split("/"))

    def dest(self, n, name):
        return os.path.join(self.archives[n - 1], *name.split("/"))

    def back_up(self):
        """catalogs, inventory, plan, then apply and verify on both roots, with the edits the cases need."""
        self.run_steps("catalogs", "inventory")
        self.assertEqual(self.backup_cli("backup-plan"), (0, ""))
        with open(self.source(f"{SHOOT}/IMG_0005.ARW"), "ab") as handle:  # edited after the plan
            handle.write(b"more")
        for n in (1, 2):
            manifest = self.out(f"backup-manifest-{n}.csv")
            self.assertEqual(self.backup_cli("backup-run", manifest, "--apply")[0], 6)  # the edited source
            if n == 2:
                with open(self.dest(2, f"{SHOOT}/IMG_0006.ARW"), "r+b") as handle:  # changed before the verify
                    handle.write(b"Z")
            self.assertEqual(self.backup_cli("backup-verify", manifest)[0], 6)  # no copy of the edited source

    def back_up_then_spoil(self):
        """Back up, then delete one source and make one source and one destination online-only."""
        self.back_up()
        os.remove(self.source(f"{SHOOT}/IMG_0007.ARW"))
        self.placeholders = {paths.norm(self.source(f"{SHOOT}/IMG_0008.ARW")),
                             paths.norm(self.dest(1, f"{SHOOT}/IMG_0010.ARW"))}

    def check_rejects(self):
        code, err = self.backup_cli("rejects")
        self.assertEqual((code, err), (0, ""))
        return {os.path.basename(r["path"]).upper(): r for r in read_csv(self.out("rejects.csv"))}

    def removal(self):
        return {os.path.basename(r["source"]).upper(): r for r in read_csv(self.out("reject-removal-list.csv"))}

    def test_each_verdict_arises_from_its_own_reject_rules_29_30(self):
        self.back_up_then_spoil()
        found = self.check_rejects()
        verdicts = {name: row["verdict"] for name, row in found.items()}
        self.assertEqual(verdicts, {
            "IMG_0001.ARW": "on_list", "IMG_0002.ARW": "kept", "IMG_0003.ARW": "kept_in_live",
            "IMG_0004.ARW": "kept_for_work", "IMG_0005.ARW": "changed_since_backup",
            "IMG_0006.ARW": "no_archive_copy", "IMG_0007.ARW": "missing", "IMG_0008.ARW": "placeholder",
            "IMG_0009.ARW": "outside_backup", "IMG_0010.ARW": "no_archive_copy",
            "IMG_0011.ARW": "kept_for_work", "IMG_0100.ARW": "kept_for_work", "IMG_0100.JPG": "kept_for_work",
            "IMG_0101.ARW": "on_list", "IMG_0101.JPG": "on_list",
        })
        states = {name: (row["archive_1"], row["archive_2"]) for name, row in found.items()}
        self.assertEqual(states["IMG_0006.ARW"], ("ok", "no_archive_copy"))  # the changed destination
        self.assertEqual(states["IMG_0010.ARW"], ("no_archive_copy", "ok"))  # the placeholder destination
        self.assertEqual(found["IMG_0011.ARW"]["developed_in"], f"{self.old} (old_state: pick)")  # a pick of 1.0 alone
        self.assertEqual(states["IMG_0001.ARW"], ("ok", "ok"))
        self.assertEqual(states["IMG_0005.ARW"], ("changed_since_backup",) * 2)
        self.assertEqual(states["IMG_0009.ARW"], ("outside_backup",) * 2)

    def test_on_list_reject_carries_its_sidecar_and_both_destinations_rules_29_30(self):
        self.back_up_then_spoil()
        self.check_rejects()
        removal = self.removal()
        self.assertEqual(sorted(removal), ["IMG_0001.ARW", "IMG_0101.ARW"])
        row = removal["IMG_0001.ARW"]
        self.assertEqual(paths.norm(row["source"]), paths.norm(self.source(f"{SHOOT}/IMG_0001.ARW")))
        self.assertEqual([paths.norm(p) for p in row["sidecars"].split(";")],
                         [paths.norm(self.source(f"{SHOOT}/IMG_0001.xmp"))])
        self.assertEqual((row["bytes"], row["sidecar_bytes"]), (str(RAW_SIZES["IMG_0001"]), str(SIDE_BYTES)))
        self.assertEqual(row["mtime_ns"], str(os.stat(self.source(f"{SHOOT}/IMG_0001.ARW")).st_mtime_ns))
        for n in (1, 2):
            self.assertEqual(paths.norm(row[f"dest_{n}"]), paths.norm(self.dest(n, f"{SHOOT}/IMG_0001.ARW")))

    def test_pair_held_by_old_state_work_travels_together_and_a_clean_pair_has_one_row_rules_29_30_rc19_rc23(self):
        self.back_up()
        found = self.check_rejects()
        raw, jpeg = found["IMG_0100.ARW"], found["IMG_0100.JPG"]
        self.assertEqual(raw["developed_in"], f"{self.old} (old_state: develop)")
        self.assertEqual(jpeg["developed_in"], "")  # the JPEG has no work of its own, only the family hold
        self.assertIn(f"held by: {paths.norm(self.source('pairs/IMG_0100.ARW'))}",
                      paths.norm(jpeg["how_decided"]))
        clean, side = found["IMG_0101.ARW"], found["IMG_0101.JPG"]
        self.assertEqual((clean["verdict"], side["verdict"]), ("on_list", "on_list"))
        removal = self.removal()
        self.assertNotIn("IMG_0101.JPG", removal)  # travels in the raw's sidecars, never a row of its own
        self.assertNotIn("IMG_0100.ARW", removal)
        self.assertEqual([paths.norm(p) for p in removal["IMG_0101.ARW"]["sidecars"].split(";")],
                         [paths.norm(self.source("pairs/IMG_0101.JPG"))])

    def test_unreadable_shoot_catalog_in_a_second_run_lists_nothing_rule_5(self):
        self.back_up()
        self.assertEqual(self.check_rejects()["IMG_0001.ARW"]["verdict"], "on_list")
        synth.make_unreadable(self.shoot_b)
        self.run_steps("catalogs")
        found = self.check_rejects()
        self.assertEqual({row["verdict"] for row in found.values()}, {"coverage_incomplete"})
        self.assertEqual(self.removal(), {})
        with open(self.out("rejects-summary.md"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertTrue([line for line in lines if line.startswith("- limiting:") and self.shoot_b in line], lines)

    def test_flag_set_after_the_catalogs_step_exits_4_until_catalogs_runs_again_rule_25(self):
        self.run_steps("catalogs")
        self.assertEqual(self.check_rejects()["IMG_0001.ARW"]["verdict"], "outside_backup")  # `out/` is not empty
        before = self.out_bytes()
        con = sqlite3.connect(self.live)
        try:
            with con:
                con.execute("UPDATE Adobe_images SET pick = 0.0 WHERE id_local = 1")  # IMG_0001: kept in live
        finally:
            con.close()
        st = os.stat(self.live)
        os.utime(self.live, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        code, err = self.backup_cli("rejects")
        self.assertEqual(code, 4)
        self.assertIn("run catalogs again: the live catalog changed since its scan", err)
        self.assertEqual(self.out_bytes(), before)
        self.run_steps("catalogs")
        self.assertEqual(self.check_rejects()["IMG_0001.ARW"]["verdict"], "kept_in_live")

    def test_library_catalogs_and_archives_are_unchanged_and_nothing_is_removed_rule_31_r17(self):
        self.back_up_then_spoil()
        roots = [self.lib, self.cats, *self.archives]
        before = listing(roots, self.base)
        self.check_rejects()
        self.assertEqual(listing(roots, self.base), before)
        for row in self.removal().values():
            for column in ("source", "dest_1", "dest_2"):
                self.assertTrue(os.path.exists(row[column]), row[column])


SETTINGS_TEXT = "synthetic develop settings"
SETTINGS_DIGEST = hashlib.sha256(SETTINGS_TEXT.encode("utf-8")).hexdigest()
TWIN_BYTES = b"t" * 120


class RejectTwinLiftEndToEndTests(E2eCase):
    """Rule 26a on its own tree: a reject only the live catalog rejects, with develop work, and a
    byte-identical twin the shoot catalog and the live catalog record with the same `settings`. A second
    identical file nobody records sorts before the twin, so the lift must pass over it."""

    REJECT, TWIN, SPARE = "other/IMG_0002.ARW", "shoot-a/IMG_0001.ARW", "a-spare/IMG_0003.ARW"

    def setUp(self):
        super().setUp()
        self.archives = [os.path.join(self.top, "archive-one"), os.path.join(self.top, "archive-two")]
        for root in self.archives:
            os.makedirs(root)
        self.write_config({**self.config, "archive_roots": self.archives, "excluded_catalogs": []})

    def build_tree(self):
        synth.make_tree(self.photos, {
            self.REJECT: TWIN_BYTES, self.TWIN: TWIN_BYTES, self.SPARE: TWIN_BYTES,
            "shoot-a/IMG_0000.ARW": b"d" * 90,  # a recorded file of its own, ahead of the twin
        })
        os.makedirs(self.cats)
        root = self.lib.replace("\\", "/") + "/"

        def entry(path, pick=0.0, work=True):
            folder, name = path.rsplit("/", 1)
            return {"root": root, "root_relative": None, "folder": f"photos/{folder}/", "base": name[:-4],
                    "ext": "ARW", "sidecars": "", "pick": pick, "developed": None,
                    "developed_ex": 1.0 if work else -1.0, "settings": SETTINGS_TEXT if work else None}

        self.reject_entry = entry(self.REJECT, pick=-1.0)
        self.shoot_entries = [entry("shoot-a/IMG_0000.ARW", work=False), entry(self.TWIN)]
        synth.make_catalog(self.shoot, self.shoot_entries)
        synth.make_catalog(self.live, [entry(self.TWIN), self.reject_entry])

    def volume_of(self, path):
        return "vol-1" if os.path.normcase(path).startswith(os.path.normcase(self.archives[0])) else "vol-2"

    def backup_cli(self, *argv, **seams):
        self.aborted = []  # a clock that never moves and a recording abort: no stall can reach os._exit
        seams = {"volume_of": self.volume_of, "free_bytes": lambda path: 10 ** 12,
                 "clock": lambda: 1.0, "abort": self.aborted.append, **seams}
        return self.run_cli(*argv, **seams)

    def source(self, name):
        return os.path.join(self.photos, *name.split("/"))

    def run_chain(self):
        """catalogs, inventory, hash, backup-plan, then backup-run --apply and backup-verify on both roots."""
        self.run_to_hash()
        self.assertEqual(self.backup_cli("backup-plan"), (0, ""))
        for n in (1, 2):
            manifest = self.out(f"backup-manifest-{n}.csv")
            self.assertEqual(self.backup_cli("backup-run", manifest, "--apply")[0], 0)
            self.assertEqual(self.backup_cli("backup-verify", manifest)[0], 0)

    def reject_row(self):
        self.assertEqual(self.backup_cli("rejects"), (0, ""))
        rows = {paths.norm(r["path"]): r for r in read_csv(self.out("rejects.csv"))}
        self.assertEqual(list(rows), [paths.norm(self.source(self.REJECT))])
        return rows[paths.norm(self.source(self.REJECT))]

    def test_rule_26a_lifts_the_hold_on_a_reject_whose_twin_shares_its_settings(self):
        before = listing([self.lib, self.cats], self.base)
        self.run_chain()
        archives = listing(self.archives, "")
        row = self.reject_row()
        self.assertEqual((row["verdict"], row["twin_settings"]), ("on_list", SETTINGS_DIGEST))
        self.assertEqual(paths.norm(row["twin"]), paths.norm(self.source(self.TWIN)))
        removal = read_csv(self.out("reject-removal-list.csv"))
        self.assertEqual([(paths.norm(r["source"]), paths.norm(r["twin"])) for r in removal],
                         [(paths.norm(self.source(self.REJECT)), paths.norm(self.source(self.TWIN)))])
        with open(self.out("rejects-summary.md"), encoding="utf-8") as handle:
            self.assertIn("Rejects on the list whose develop hold rule 26a lifted: 1 files, "
                          f"{len(TWIN_BYTES)} bytes", handle.read())
        self.assertEqual(listing([self.lib, self.cats], self.base), before)  # rule 31, R17
        self.assertEqual(listing(self.archives, ""), archives)
        self.assertTrue(os.path.exists(self.source(self.REJECT)))

    def test_twin_changed_after_the_hash_keeps_the_reject_for_work_rule_26a_1(self):
        self.run_chain()
        with open(self.source(self.TWIN), "r+b") as handle:
            handle.write(b"Z")
        st = os.stat(self.source(self.TWIN))
        os.utime(self.source(self.TWIN), ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        row = self.reject_row()
        self.assertEqual((row["verdict"], row["twin"], row["twin_settings"]), ("kept_for_work", "", ""))
        self.assertEqual(read_csv(self.out("reject-removal-list.csv")), [])

    def test_a_shoot_catalog_that_rejects_it_too_keeps_the_reject_for_work_rule_26a_3(self):
        os.remove(self.shoot)
        synth.make_catalog(self.shoot, [*self.shoot_entries, self.reject_entry])
        self.run_chain()
        row = self.reject_row()
        self.assertEqual((row["verdict"], row["twin"], row["twin_settings"]), ("kept_for_work", "", ""))
        self.assertEqual(read_csv(self.out("reject-removal-list.csv")), [])


SECOND_BATCH_NOW = "2026-01-01T00:01:00Z"
SECOND_BATCH = "20260101T000100Z"
DIGEST = re.compile(r"\b[0-9a-f]{64}\b")


class MoverTreeCase(E2eCase):
    """What the two mover trees share: the real commands up to the verified backups, then `stage`, `undo` and
    `purge` through the World's seams, and listings. A subclass lays out its own tree in `setUp` and sets
    `list_path`."""

    def attrs_of(self, path, st):
        return self.world.attrs_of(path, st)

    def backup_cli(self, *argv, **seams):
        self.aborted = []  # a clock that never moves and a recording abort: no stall can reach os._exit
        seams = {"volume_of": self.world.volume_of, "free_bytes": self.world.free_bytes,
                 "clock": lambda: 1.0, "abort": self.aborted.append, **seams}
        return self.run_cli(*argv, **seams)

    def archived(self):
        """The real commands, `catalogs` to `backup-verify`: every file backed up and verified on both roots."""
        self.run_steps("catalogs", "inventory", "hash")
        self.assertEqual(self.backup_cli("backup-plan"), (0, ""))
        for n in (1, 2):
            manifest = self.out(f"backup-manifest-{n}.csv")
            self.assertEqual(self.backup_cli("backup-run", manifest, "--apply"), (0, ""))
            self.assertEqual(self.backup_cli("backup-verify", manifest), (0, ""))

    def approval(self):
        with open(self.list_path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def stage(self, approval=None, *flags):
        return self.world.main("stage", self.list_path, "--approved-sha256", approval or self.approval(), *flags)

    def files(self, *roots):
        found = listing([root for root in roots if os.path.isdir(root)], None)
        return {paths.norm(path): rest for path, *rest in found if rest[-1]}  # files only: a folder has no digest

    def tree(self):
        return self.files(self.photos, self.cats, self.backup, self.staging, *self.archives)

    def outside(self):
        return self.files(self.cats, self.backup, *self.archives)

    def purge_with_approval(self, batch):
        """The dry run names the purge set's digest (the one 64-digit token it prints); `--apply` approves it."""
        code, out, err = self.world.main("purge", batch)
        self.assertEqual((code, err), (0, ""))
        digests = DIGEST.findall(out)
        self.assertEqual(len(digests), 1, out)
        return self.world.main("purge", batch, "--apply", "--approved-sha256", digests[0])[0]

    def journal(self, batch):
        path = os.path.join(self.base, "work", "journal", f"mover-{batch}.jsonl")
        with open(path, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle]
        for line in lines:  # the journal's spelling of a path is not the one `os.walk` gives
            line.update({key: paths.norm(line[key]) for key in ("original", "staged") if key in line})
        return lines

    def kinds(self, lines, op):
        """The `kind` of each file's `begin` of `op`; every line that carries a `kind` agrees with it."""
        found = {line["original"]: line["kind"] for line in lines if line["event"] == "begin" and line["op"] == op}
        for line in lines:
            if "kind" in line and line["event"] in ("begin", "end") and line["op"] == op:
                self.assertEqual(line["kind"], found[line["original"]], line)
        return found


class MoverEndToEndTests(MoverTreeCase):
    """B2 on its own tree: the real commands from `catalogs` to `rejects`, then `stage`, `undo`, `stage` and
    `purge` through the World's seams. Three raws are rejected (one with an `.xmp`); a JPEG sits next to
    one of them and no catalog records it, one raw is kept, and a backup root holds one other file."""

    def setUp(self):  # no `super().setUp()`: the World lays out this tree
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.world = World(self.top)
        self.base, self.photos, self.archives = self.world.base, self.world.photo_root, self.world.archive_roots
        self.cats = os.path.join(self.top, "cats")
        self.backup = os.path.join(self.top, "backup")
        synth.make_tree(self.backup, {"2024/IMG_0900.ARW": b"o" * 90})
        self.live, self.shoot = os.path.join(self.cats, "live.lrcat"), os.path.join(self.cats, "shoot-a.lrcat")
        self.staging = os.path.join(self.base, "work", "staging")
        self.raws = [self.world.path(rel) for rel in ("shoot-a/IMG_0001.ARW", "shoot-a/IMG_0002.ARW",
                                                     "other/IMG_0004.ARW")]
        self.sidecar = self.world.path("shoot-a/IMG_0001.xmp")
        self.jpeg = self.world.path("shoot-a/IMG_0002.JPG")
        self.kept = self.world.path("shoot-a/IMG_0003.ARW")
        for number, (rel, data) in enumerate({
                "shoot-a/IMG_0001.ARW": b"1" * 101, "shoot-a/IMG_0001.xmp": b"x" * 10,
                "shoot-a/IMG_0002.ARW": b"2" * 102, "shoot-a/IMG_0002.JPG": b"j" * 40,
                "shoot-a/IMG_0003.ARW": b"3" * 103, "other/IMG_0004.ARW": b"4" * 104}.items(), start=1):
            self.world.add(rel, data, 1_700_000_000_000_000_000 + number * 1_000_000_000)
        os.makedirs(self.cats)
        root = self.photos.replace("\\", "/") + "/"

        def entry(folder, base, *, pick=-1.0, sidecars=""):
            return {"root": root, "root_relative": None, "folder": f"{folder}/", "base": base, "ext": "ARW",
                    "sidecars": sidecars, "pick": pick, "developed": None, "developed_ex": -1.0}

        recorded = [entry("shoot-a", "IMG_0001", sidecars="xmp"), entry("shoot-a", "IMG_0002"),
                    entry("shoot-a", "IMG_0003", pick=0.0), entry("other", "IMG_0004")]
        synth.make_catalog(self.shoot, recorded)
        synth.make_catalog(self.live, recorded)
        self.write_config({
            "library_roots": [self.photos], "backup_roots": [self.backup], "catalog_roots": [self.cats],
            "photo_root": self.photos, "group_depth": 1, "exclude": [], "live_catalog": self.live,
            "old_states": [], "excluded_catalogs": [], "archive_roots": self.archives})
        self.list_path = self.out("reject-removal-list.csv")

    def check_rejects(self):
        """The real commands, `catalogs` to `rejects`, with every file backed up and verified on both roots."""
        self.archived()
        self.assertEqual(self.backup_cli("rejects"), (0, ""))

    def test_a_list_edited_by_one_byte_is_exit_2_with_the_old_approval_rule_32(self):
        self.check_rejects()
        approved, before = self.approval(), self.tree()
        with open(self.list_path, "rb") as handle:
            original = handle.read()
        edited = original.replace(b",101,", b",102,", 1)  # still a list that reads: the size of one raw, one digit off
        self.assertEqual(sum(a != b for a, b in zip(original, edited)), 1)
        with open(self.list_path, "wb") as handle:
            handle.write(edited)
        self.assertEqual(self.stage(approved, "--apply")[0], 2)
        self.assertEqual(self.tree(), before)

    def test_rc104_an_older_list_over_the_one_rejects_wrote_is_exit_4_and_the_real_one_stages(self):
        self.check_rejects()
        real = self.approval()
        with open(self.out("rejects-summary.md"), encoding="utf-8") as handle:
            self.assertIn(f"- SHA-256: {real}\n", handle.read())  # the line the real reject check writes
        with open(self.list_path, "rb") as handle:
            data = handle.read()
        older = data.replace(b",101,", b",102,", 1)  # an edited copy stands for an older list
        with open(self.list_path, "wb") as handle:
            handle.write(older)
        before = self.tree()
        self.assertEqual(self.stage(hashlib.sha256(older).hexdigest(), "--apply")[0], 4)
        self.assertEqual(self.tree(), before)
        with open(self.list_path, "wb") as handle:
            handle.write(data)
        self.assertEqual(self.stage()[0], 0)

    def test_a_catalogs_scan_after_rejects_is_exit_4_rule_34(self):
        self.check_rejects()
        before = self.tree()
        self.assertEqual(self.stage()[0], 0)  # the real list and summary pass
        self.run_steps("catalogs")
        code, _out, err = self.stage(None, "--apply")
        self.assertEqual(code, 4, err)
        self.assertEqual(self.tree(), before)

    def test_stage_undo_stage_purge_move_exactly_the_listed_files_rules_37_to_41(self):
        self.check_rejects()
        listed = [paths.norm(p) for p in (*self.raws, self.sidecar)]
        before = self.tree()
        self.assertEqual(self.stage()[0], 0)  # a dry run
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.stage(None, "--apply")[0], 0)
        first = self.journal(BATCH)
        self.assertEqual(self.kinds(first, "stage"), {**dict.fromkeys(listed[:3], "reject"), listed[3]: "sidecar"})
        staged = {line["original"]: line["staged"] for line in first if line["event"] == "begin"}
        self.assertEqual(sorted(staged), sorted(listed))
        self.assertEqual(self.tree(), {**{p: v for p, v in before.items() if p not in listed},
                                       **{staged[p]: before[p] for p in listed}})
        self.assertEqual(self.world.main("undo", BATCH, "--apply")[0], 0)
        self.assertEqual(self.tree(), before)  # listing and contents equal
        undone = self.journal(BATCH)
        self.assertEqual(self.kinds(undone, "restore"), self.kinds(first, "stage"))
        self.world.now = lambda: SECOND_BATCH_NOW
        self.assertEqual(self.stage(None, "--apply")[0], 0)
        again = self.journal(SECOND_BATCH)
        staged_again = {line["original"]: line["staged"] for line in again if line["event"] == "begin"}
        self.assertNotEqual(staged_again, staged)  # a new batch folder
        self.assertEqual(set(staged_again), set(listed))
        staged_files = self.tree()
        self.assertEqual(self.purge_with_approval(SECOND_BATCH), 0)
        self.assertEqual(self.tree(), {p: v for p, v in staged_files.items() if p not in staged_again.values()})
        purged = self.journal(SECOND_BATCH)
        self.assertEqual(self.kinds(purged, "purge"), self.kinds(first, "stage"))
        self.assert_each_file_begins_before_it_ends(first, "stage", "staged", listed)
        self.assert_each_file_begins_before_it_ends(undone, "restore", "restored", listed)
        self.assert_each_file_begins_before_it_ends(purged, "purge", "purged", listed)
        self.assert_family_on_each_rows_first_begin(first)
        self.assert_family_on_each_rows_first_begin(again)

    def assert_each_file_begins_before_it_ends(self, lines, op, state, listed):
        for path in listed:
            marks = [(i, line["event"]) for i, line in enumerate(lines)
                     if line.get("original") == path and line["event"] in ("begin", "end")
                     and (line["op"] == op if line["event"] == "begin" else line["state"] == state)]
            self.assertEqual([event for _i, event in marks], ["begin", "end"], (op, path))

    def assert_family_on_each_rows_first_begin(self, lines):
        begins = [line for line in lines if line["event"] == "begin" and line["op"] == "stage"]
        for row in {line["row"] for line in begins}:
            mine = [line for line in begins if line["row"] == row]
            self.assertEqual(mine[0].get("family"), len(mine))  # the number of files the row renames
            self.assertTrue(all("family" not in line for line in mine[1:]), row)

    def test_a_jpeg_beside_a_listed_raw_and_not_on_the_list_is_untouched_rule_45(self):
        self.check_rejects()
        rows = read_csv(self.list_path)
        self.assertEqual(sorted(paths.norm(row["source"]) for row in rows), sorted(paths.norm(p) for p in self.raws))
        self.assertNotIn(paths.norm(self.jpeg), [paths.norm(p) for row in rows for p in row["sidecars"].split(";")])
        unlisted = [paths.norm(p) for p in (self.jpeg, self.kept)]
        before = {p: self.tree().get(p) for p in unlisted}
        self.assertNotIn(None, before.values())
        self.assertEqual(self.stage(None, "--apply")[0], 0)
        self.assertEqual({p: self.tree().get(p) for p in unlisted}, before)
        self.assertEqual(self.purge_with_approval(BATCH), 0)
        self.assertEqual({p: self.tree().get(p) for p in unlisted}, before)

    def test_catalogs_archives_and_backup_roots_are_unchanged_by_stage_undo_and_purge_h2_r11(self):
        self.check_rejects()
        before = self.outside()
        for root in (self.cats, self.backup, *self.archives):  # each root holds something to keep unchanged
            self.assertTrue([p for p in before if p.startswith(paths.norm(root) + "/")], root)
        self.assertEqual(self.stage(None, "--apply")[0], 0)
        self.assertEqual(self.outside(), before)
        self.assertEqual(self.world.main("undo", BATCH, "--apply")[0], 0)
        self.assertEqual(self.outside(), before)
        self.world.now = lambda: SECOND_BATCH_NOW
        self.assertEqual(self.stage(None, "--apply")[0], 0)
        self.assertEqual(self.purge_with_approval(SECOND_BATCH), 0)
        self.assertEqual(self.outside(), before)


class CopiesEndToEndTests(MoverTreeCase):
    """D1 on its own tree: a live and a shoot catalog; two identical raws of which the live catalog records one
    (the other is a class (a) copy); a raw that only the shoot catalog records, and rejects, with an unrecorded
    identical copy (class (b)(ii)); a backup root; two archive roots."""

    def setUp(self):  # no `super().setUp()`: the World lays out this tree
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.world = World(self.top)
        self.base, self.photos, self.archives = self.world.base, self.world.photo_root, self.world.archive_roots
        self.cats = os.path.join(self.top, "cats")
        self.backup = os.path.join(self.top, "backup")
        self.staging = os.path.join(self.base, "work", "staging")
        synth.make_tree(self.backup, {"2024/IMG_0900.ARW": b"o" * 90})
        self.live, self.shoot = os.path.join(self.cats, "live.lrcat"), os.path.join(self.cats, "shoot-a.lrcat")
        self.keeper, self.copy_a, self.rejected, self.copy_b = (
            self.world.add(rel, data, 1_700_000_000_000_000_000 + number * 1_000_000_000)
            for number, (rel, data) in enumerate((("shoot-a/IMG_0001.ARW", b"1" * 101),
                                                  ("other/IMG_0001.ARW", b"1" * 101),
                                                  ("shoot-a/IMG_0002.ARW", b"2" * 102),
                                                  ("other/IMG_0002.ARW", b"2" * 102)), start=1))
        os.makedirs(self.cats)
        root = self.photos.replace("\\", "/") + "/"

        def entry(base, pick):
            return {"root": root, "root_relative": None, "folder": "shoot-a/", "base": base, "ext": "ARW",
                    "sidecars": "", "pick": pick, "developed": None, "developed_ex": -1.0}

        synth.make_catalog(self.live, [entry("IMG_0001", 0.0)])
        synth.make_catalog(self.shoot, [entry("IMG_0001", 0.0), entry("IMG_0002", -1.0)])
        self.write_config({
            "library_roots": [self.photos], "backup_roots": [self.backup], "catalog_roots": [self.cats],
            "photo_root": self.photos, "group_depth": 1, "exclude": [], "live_catalog": self.live,
            "old_states": [], "excluded_catalogs": [], "archive_roots": self.archives})
        self.list_path = self.out("copy-removal-list.csv")

    def check_copies(self):
        self.archived()
        self.assertEqual(self.backup_cli("copies"), (0, ""))

    def test_the_list_holds_one_class_a_and_one_class_b_row_rules_46_to_49(self):
        self.check_copies()
        self.assertIn("2 rows", self.stdout)
        self.assertEqual({paths.norm(row["source"]): paths.norm(row["twin"]) if row["twin"] else ""
                          for row in read_csv(self.list_path)},
                         {paths.norm(self.copy_a): paths.norm(self.keeper), paths.norm(self.copy_b): ""})
        with open(self.out("copies-summary.md"), encoding="utf-8") as handle:
            self.assertIn(f"- SHA-256: {self.approval()}\n", handle.read())

    def test_stage_undo_stage_purge_remove_the_two_copies_and_leave_the_twin_and_the_catalogs_rules_50_to_53(self):
        self.check_copies()
        listed = [paths.norm(self.copy_a), paths.norm(self.copy_b)]
        before, catalogs = self.tree(), self.files(self.cats)
        self.assertEqual(self.stage()[0], 0)  # a dry run
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.stage(None, "--apply")[0], 0)
        first = self.journal(BATCH)
        self.assertEqual(self.kinds(first, "stage"), dict.fromkeys(listed, "copy"))
        staged = {line["original"]: line["staged"] for line in first if line["event"] == "begin"}
        self.assertEqual(self.tree(), {**{p: v for p, v in before.items() if p not in listed},
                                       **{staged[p]: before[p] for p in listed}})
        self.assertEqual(self.world.main("undo", BATCH, "--apply")[0], 0)
        self.assertEqual(self.tree(), before)  # listing and contents equal
        self.world.now = lambda: SECOND_BATCH_NOW
        self.assertEqual(self.stage(None, "--apply")[0], 0)
        staged_again = {line["staged"] for line in self.journal(SECOND_BATCH)
                        if line["event"] == "begin" and line["op"] == "stage"}
        staged_files = self.tree()
        self.assertEqual(self.purge_with_approval(SECOND_BATCH), 0)
        after = self.tree()
        self.assertEqual(after, {p: v for p, v in staged_files.items() if p not in staged_again})
        for path in (self.keeper, self.rejected):
            self.assertIn(paths.norm(path), after)
        for path in listed:
            self.assertNotIn(path, after)
        for name in (f"stage-{SECOND_BATCH}.csv", f"purge-{SECOND_BATCH}.csv"):
            self.assertEqual({row["kind"] for row in read_csv(self.out(name))}, {"copy"}, name)
        self.assertEqual(self.files(self.cats), catalogs)  # no catalog's size, time or bytes changed


if __name__ == "__main__":
    unittest.main()
