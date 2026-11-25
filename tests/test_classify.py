import itertools
import os
import tempfile
import unittest
from types import SimpleNamespace

from lrclean import classify, paths, store

LIB = "C:/lib"
CAT = "C:/cat"
SHOOT = f"{CAT}/shoot-a.lrcat"
OTHER = f"{CAT}/shoot-b.lrcat"
_OPEN = []


def tearDownModule():
    for conn in _OPEN:
        conn.close()


class Scene:
    """Rows inserted literally, in the stored value forms."""

    def __init__(self, files_status="complete", cat_status="complete"):
        self.conn = store.connect(":memory:")
        _OPEN.append(self.conn)
        self.catalog_roots = (CAT,)
        self.index = itertools.count(1000)
        self.files_scan = store.insert_row(self.conn, "scan", root=LIB, kind="files",
                                           started="t", finished="t", status=files_status)
        self.cat_scan = store.insert_row(self.conn, "scan", root=CAT, kind="catalogs",
                                         started="t", finished="t", status=cat_status)
        self.live = {}

    def file(self, path, content="A", size=100, mtime=5, index=None, placeholder=0,
             container="", ext=None, scan=None, hashed=True, sample=None, full=None):
        name = path.rsplit("/", 1)[-1]
        ext = ext if ext is not None else name.rpartition(".")[2].lower()
        index = next(self.index) if index is None else index
        fid = store.insert_row(
            self.conn, "file", scan_id=scan or self.files_scan, volume=paths.volume(path),
            path_norm=paths.norm(path), path_orig=path, ext=ext, size=size, mtime_ns=mtime,
            attrs=0, placeholder=placeholder, container=container, file_index=index,
            nlink=1, checked_at="t")
        if hashed:
            store.put_hash(self.conn, file_id=fid, file_index=index, size=size, mtime_ns=mtime,
                           sample=sample if sample is not None else "s" + content,
                           full=full if full is not None else content * 12, hashed_at="t")
        self.live[paths.fs_path(path)] = SimpleNamespace(st_size=size, st_mtime_ns=mtime, st_ino=index)
        return fid

    def catalog(self, source, records=(), status="ok", via="absolute", scan=None, sidecar=0):
        cid = store.insert_row(
            self.conn, "catalog", scan_id=scan or self.cat_scan, source_path=source,
            snapshot_path="", role="shoot", status=status, size=1, mtime_ns=1,
            db_version="1", wal_bytes=0, has_lock=0)
        for rec in records:
            store.insert_row(self.conn, "record", catalog_id=cid, path_norm=paths.norm(rec),
                             path_orig=rec, via=via, ext=rec.rpartition(".")[2].lower(),
                             capture_time="", is_sidecar=sidecar)
        self.conn.commit()
        return cid

    def stat(self, path):
        if path not in self.live:
            raise FileNotFoundError(path)
        return self.live[path]

    def run(self, excluded=(), stale=(), unseen=(), stat=None):
        found = classify.verdicts(
            self.conn, library_roots=[LIB], catalog_roots=list(self.catalog_roots),
            excluded_catalogs=list(excluded), stale_catalogs=stale, unseen_catalogs=unseen,
            stat=stat or self.stat)
        return {v.path_orig.rsplit("/", 1)[-1] + "@" + v.path_orig.rsplit("/", 2)[-2]: v
                for v in found}


def pair(scene, **kw):
    """An unrecorded file U and a recorded identical twin T; returns the verdict of U."""
    scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", **kw)
    scene.file(f"{LIB}/shoot-b/IMG_0001.ARW", **kw)
    scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
    return scene.run()["IMG_0001.ARW@shoot-a"]


class RootStateTests(unittest.TestCase):
    def test_rule1_only_the_latest_complete_scan_is_classified(self):
        for status, expected in [("complete", "complete"), ("partial", "partial"),
                                 ("running", "partial"), ("not_connected", "not_connected")]:
            with self.subTest(status):
                scene = Scene()
                scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
                newer = store.insert_row(scene.conn, "scan", root=LIB.upper(), kind="files",
                                         started="t", finished="", status=status)
                state = classify.root_states(scene.conn, [LIB])[0]
                self.assertEqual((state.scan_id, state.status), (newer, expected))
                self.assertEqual(scene.run(), {})
                if status == "complete":
                    scene.file(f"{LIB}/shoot-b/IMG_0002.ARW", scan=newer)
                    self.assertEqual(list(scene.run()), ["IMG_0002.ARW@shoot-b"])

    def test_rule1_a_root_never_scanned_has_no_verdict(self):
        scene = Scene()
        state = classify.root_states(scene.conn, ["C:/never"])[0]
        self.assertEqual((state.scan_id, state.status), (None, "not_scanned"))

    def test_rule1_eligible_files_skip_cache_unreadable_and_sidecars(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.file(f"{LIB}/shoot-a/IMG_0001.xmp")
        scene.file(f"{LIB}/shoot-a/previews.lrdata", container="cache:c:/lib/shoot-a")
        scene.file(f"{LIB}/shoot-a/inside.jpg", container="cache:c:/lib/shoot-a")
        scene.file(f"{LIB}/shoot-a/store.lrdata", container="storage:c:/lib/shoot-a/store.lrdata")
        scene.file(f"{LIB}/shoot-a/other.lrdata", container="unknown:c:/lib/shoot-a/other.lrdata")
        scene.file(f"{LIB}/shoot-c", ext="<unreadable>", size=0)
        names = [r["path_orig"] for r in classify.eligible_files(scene.conn, library_roots=[LIB])]
        self.assertEqual(names, [f"{LIB}/shoot-a/IMG_0001.ARW"])
        everything = classify.inventoried_files(scene.conn, [LIB])
        self.assertEqual(len(everything), 2)


class RecordedTests(unittest.TestCase):
    def test_rules3_5_a_file_is_recorded_by_every_spelling_and_source(self):
        cases = {
            "case": (f"{LIB}/Shoot-A/img_0001.arw", {}),
            "slash": (f"{LIB}\\shoot-a\\IMG_0001.ARW", {}),
            "nfc": (f"{LIB}/shoot-a/Cafe\u0301.ARW", {}),
            "relative": (f"{LIB}/shoot-a/IMG_0001.ARW", {"via": "relative"}),
            "sidecar": (f"{LIB}/shoot-a/IMG_0001.ARW", {"sidecar": 1}),
            "partial catalog": (f"{LIB}/shoot-a/IMG_0001.ARW", {"status": "partial"}),
        }
        for name, (recorded_as, kw) in cases.items():
            with self.subTest(name):
                scene = Scene()
                on_disk = f"{LIB}/shoot-a/Caf\u00e9.ARW" if name == "nfc" else f"{LIB}/shoot-a/IMG_0001.ARW"
                scene.file(on_disk)
                scene.catalog(SHOOT, [recorded_as], **kw)
                self.assertTrue(next(iter(scene.run().values())).recorded)

    def test_rule3_a_second_catalog_records(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.catalog(SHOOT, [])
        scene.catalog(OTHER, [f"{LIB}/shoot-a/IMG_0001.ARW"])
        self.assertTrue(next(iter(scene.run().values())).recorded)

    def test_rule3_an_old_state_catalog_still_records(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        cid = scene.catalog(SHOOT, [f"{LIB}/shoot-a/IMG_0001.ARW"])
        scene.conn.execute('UPDATE "catalog" SET role = ? WHERE id = ?', ("old_state", cid))
        self.assertTrue(next(iter(scene.run().values())).recorded)

    def test_rule5_an_owner_excluded_catalog_does_not_record(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.catalog(SHOOT, [f"{LIB}/shoot-a/IMG_0001.ARW"])
        self.assertTrue(next(iter(scene.run().values())).recorded)
        self.assertFalse(next(iter(scene.run(excluded=[SHOOT.upper()]).values())).recorded)

    def test_rule3_same_name_elsewhere_and_a_like_named_folder_are_not_recorded(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.file(f"{LIB}/shoot-a-copy/IMG_0002.ARW", content="B")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW", f"{LIB}/shoot-a/IMG_0002.ARW"])
        self.assertFalse(any(v.recorded for v in scene.run().values()))

    def test_rule3_unrecorded_unreadable_and_empty_catalogs_record_nothing(self):
        scene = Scene()
        scene.catalog(SHOOT, [f"{LIB}/shoot-a/IMG_0001.ARW"], status="unreadable")
        scene.catalog(OTHER, [f"{LIB}/shoot-a/IMG_0002.ARW"], status="empty")
        self.assertEqual(classify.recorded_norms(scene.conn, excluded_catalogs=[]), set())


class CoverageTests(unittest.TestCase):
    def test_rule5_a_limiting_catalog_makes_the_would_be_safe_file_incomplete(self):
        for status, limits in [("unreadable", True), ("partial", True), ("empty", False), ("ok", False)]:
            with self.subTest(status):
                scene = Scene()
                scene.catalog(OTHER, [], status=status)
                found = pair(scene)
                self.assertEqual(found.klass, "coverage_incomplete" if limits else "safe_candidate")
                if limits:
                    self.assertIn(OTHER, found.how_decided)
                    self.assertTrue(found.how_decided.startswith(
                        "not recorded by the catalogs that could be read; coverage incomplete: "))

    def test_rule5_exclusion_is_read_from_the_argument_at_every_verdict(self):
        scene = Scene()
        scene.catalog(OTHER, [], status="unreadable")
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        key = "IMG_0001.ARW@shoot-a"
        self.assertEqual(scene.run()[key].klass, "coverage_incomplete")
        self.assertEqual(scene.run(excluded=[OTHER.upper()])[key].klass, "safe_candidate")

    def test_rule5_a_catalog_root_not_read_whole_limits_coverage(self):
        for status in ["partial", "not_connected", "running", None]:
            with self.subTest(status):
                scene = Scene(cat_status=status or "complete")
                if status is None:
                    scene.catalog_roots = (CAT, "C:/never")
                found = pair(scene)
                self.assertEqual(found.klass, "coverage_incomplete")
                label = {"partial": "partial", "running": "partial",
                         "not_connected": "not connected", None: "not scanned"}[status]
                root = "C:/never" if status is None else CAT
                self.assertIn(f"{root} ({label})", found.how_decided)

    def test_rule5_stale_catalogs_limit_unless_the_owner_excluded_them(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        scene.catalog(OTHER, [])
        key = "IMG_0001.ARW@shoot-a"
        stale = scene.run(stale=[OTHER])[key]
        self.assertEqual(stale.klass, "coverage_incomplete")
        self.assertIn(f"{OTHER} (changed since its snapshot)", stale.how_decided)
        self.assertEqual(scene.run(stale=[OTHER], excluded=[OTHER])[key].klass, "safe_candidate")
        cover = classify.coverage(scene.conn, catalog_roots=[CAT], excluded_catalogs=[OTHER.upper()],
                                  stale_catalogs=[OTHER])
        self.assertEqual((cover.complete, cover.limiting), (True, []))

    def test_rule5_unseen_catalogs_limit_unless_the_owner_excluded_them(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        key = "IMG_0001.ARW@shoot-a"
        self.assertEqual(scene.run()[key].klass, "safe_candidate")
        unseen = scene.run(unseen=[OTHER])[key]
        self.assertEqual(unseen.klass, "coverage_incomplete")
        self.assertIn(f"{OTHER} (appeared since the catalogs scan)", unseen.how_decided)
        self.assertEqual(scene.run(unseen=[OTHER], excluded=[OTHER.upper()])[key].klass,
                         "safe_candidate")


class IdenticalTests(unittest.TestCase):
    def test_rule8_identical_content_under_two_names_is_a_safe_candidate(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        twin = scene.file(f"{LIB}/shoot-b/IMG_0001.ARW")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        found = scene.run()["IMG_0001.ARW@shoot-a"]
        self.assertEqual(found.klass, "safe_candidate")
        self.assertFalse(found.recorded)
        self.assertEqual(found.group, "100:" + "A" * 12)
        self.assertEqual(found.twin_file_id, twin)
        self.assertIn(f"{LIB}/shoot-b/IMG_0001.ARW", found.how_decided)

    def test_rule8_same_name_with_different_content_is_not_identical(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", content="A", sample="same")
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW", content="B", sample="same")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        found = scene.run()["IMG_0001.ARW@shoot-a"]
        self.assertEqual(found.klass, "unique_unrecorded")
        self.assertEqual(found.group, "")

    def test_rule8_equal_sample_alone_with_an_unhashed_collision_is_not_compared(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", content="A")
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW", content="A", full="")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        self.assertEqual(scene.run()["IMG_0001.ARW@shoot-a"].klass, "not_compared")

    def test_rule9_a_stale_hash_row_gives_not_compared(self):
        for field in ["file_index", "size", "mtime_ns"]:
            with self.subTest(field):
                scene = Scene()
                fid = scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
                scene.file(f"{LIB}/shoot-b/IMG_0001.ARW")
                scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
                scene.conn.execute(f'UPDATE "hash" SET {field} = {field} + 1 WHERE file_id = ?', (fid,))
                self.assertEqual(scene.run()["IMG_0001.ARW@shoot-a"].klass, "not_compared")

    def test_rule9_a_sample_only_file_beside_a_fully_hashed_one_is_not_compared(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", content="A", sample="same", full="")
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW", content="B", sample="same")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        self.assertEqual(scene.run()["IMG_0001.ARW@shoot-a"].klass, "not_compared")

    def test_rule9_duplicates_nothing_needs_every_same_size_file_compared(self):
        for blind in ["never hashed", "placeholder", "other kind"]:
            with self.subTest(blind):
                scene = Scene()
                scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", content="B")
                scene.file(f"{LIB}/shoot-a/IMG_0002.ARW", content="C", size=7)
                name = "IMG_0003.JPG" if blind == "other kind" else "IMG_0003.ARW"
                scene.file(f"{LIB}/shoot-a/{name}", hashed=False, placeholder=int(blind == "placeholder"))
                found = scene.run()
                first = found["IMG_0001.ARW@shoot-a"]
                self.assertEqual(first.klass, "unique_unrecorded" if blind == "other kind" else "not_compared")
                if blind != "other kind":
                    self.assertIn("1 other file(s) of the same size", first.how_decided)
                self.assertEqual(found["IMG_0002.ARW@shoot-a"].klass, "unique_unrecorded")

    def test_rule9_a_recorded_identical_copy_with_a_stale_hash_leaves_the_file_not_compared(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        twin = scene.file(f"{LIB}/shoot-b/IMG_0001.ARW")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        scene.conn.execute('UPDATE "hash" SET size = size + 1 WHERE file_id = ?', (twin,))
        self.assertEqual(scene.run()["IMG_0001.ARW@shoot-a"].klass, "not_compared")

    def test_rule10_two_names_with_one_file_index_are_one_file(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", index=77)
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW", index=77)
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        found = scene.run()
        for v in found.values():
            self.assertEqual((v.klass, v.group), ("same_file", "c::77"))
        self.assertTrue(found["IMG_0001.ARW@shoot-b"].recorded)

    def test_rule10_file_index_zero_never_aliases(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", index=0)
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW", index=0)
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        self.assertEqual(scene.run()["IMG_0001.ARW@shoot-a"].klass, "safe_candidate")

    def test_rule8_a_placeholder_twin_is_rejected(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW", placeholder=1)
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        found = scene.run()
        self.assertEqual(found["IMG_0001.ARW@shoot-a"].klass, "not_compared")
        self.assertEqual(found["IMG_0001.ARW@shoot-b"].klass, "placeholder")


class ResumeTests(unittest.TestCase):
    def test_rule13_a_missing_or_changed_file_or_twin_is_changed_since_scan(self):
        a, b = f"{LIB}/shoot-a/IMG_0001.ARW", f"{LIB}/shoot-b/IMG_0001.ARW"
        cases = [
            ("file gone", a, None, "file"), ("twin gone", b, None, "twin"),
            ("file size", a, ("st_size", 101), "file"), ("twin size", b, ("st_size", 101), "twin"),
            ("file mtime", a, ("st_mtime_ns", 6), "file"), ("twin mtime", b, ("st_mtime_ns", 6), "twin"),
            ("file index", a, ("st_ino", 1), "file"), ("twin index", b, ("st_ino", 1), "twin"),
            ("file placeholder", a, ("st_file_attributes", 0x400000),
             "file is now an online-only placeholder"),
            ("twin placeholder", b, ("st_file_attributes", 0x1000),
             "twin is now an online-only placeholder"),
        ]
        for name, victim, change, who in cases:
            with self.subTest(name):
                scene = Scene()
                scene.file(a)
                scene.file(b)
                scene.catalog(SHOOT, [b])
                if change is None:
                    del scene.live[victim]
                else:
                    setattr(scene.live[victim], *change)
                found = scene.run()["IMG_0001.ARW@shoot-a"]
                self.assertEqual(found.klass, "changed_since_scan")
                self.assertIn(who, found.how_decided)

    def test_rule13_unchanged_files_stay_safe_and_stat_gets_the_fs_path(self):
        scene = Scene()
        long_dir = "/".join(["C:/lib", "d" * 120, "e" * 120])
        a, b = f"{long_dir}/IMG_0001.ARW", f"{LIB}/shoot-b/IMG_0001.ARW"
        scene.file(a)
        scene.file(b)
        scene.catalog(SHOOT, [b])
        asked = []

        def stat(path):
            asked.append(path)
            return scene.stat(path)

        found = scene.run(stat=stat)["IMG_0001.ARW@" + "e" * 120]
        self.assertEqual(found.klass, "safe_candidate")
        self.assertIn(paths.fs_path(a), asked)
        self.assertTrue(paths.fs_path(a).startswith("\\\\?\\"))

    def test_rule13_a_stored_index_above_2_63_matches_its_signed_stat_value(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", index=paths.signed64(2**63 + 5))
        scene.file(f"{LIB}/shoot-b/IMG_0001.ARW")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.ARW"])
        scene.live[f"{LIB}/shoot-a/IMG_0001.ARW"].st_ino = 2**63 + 5
        found = scene.run()["IMG_0001.ARW@shoot-a"]
        self.assertEqual(found.klass, "safe_candidate")

    def test_rule4_files_at_261_character_paths_stay_safe_with_real_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.abspath(tmp).replace("\\", "/")
            deep = [f"{base}/{'d' * 60}/{'e' * 60}/{'f' * 60}/g{'h' * 40}-{tag}/IMG_0001.ARW"
                    for tag in "ab"]
            self.assertGreater(len(deep[0]), paths.LONG_LIMIT)
            scene = Scene()
            for path in deep:
                os.makedirs(paths.fs_path(os.path.dirname(path).replace("/", "\\")), exist_ok=True)
                with open(paths.fs_path(path.replace("/", "\\")), "wb") as handle:
                    handle.write(b"x" * 10)
                st = os.stat(paths.fs_path(path.replace("/", "\\")))
                scene.file(path, size=st.st_size, mtime=st.st_mtime_ns, index=paths.signed64(st.st_ino))
            scene.catalog(SHOOT, [deep[1]])
            found = classify.verdicts(scene.conn, library_roots=[LIB], catalog_roots=[CAT],
                                      excluded_catalogs=[])
            self.assertEqual([v.klass for v in found], ["safe_candidate", "recorded"])


class GroupTests(unittest.TestCase):
    def three(self, recorded):
        scene = Scene()
        names = [f"{LIB}/shoot-{c}/IMG_0001.ARW" for c in "abc"]
        for name in names:
            scene.file(name)
        scene.catalog(SHOOT, [names[i] for i in recorded])
        return {k.split("@")[1]: v for k, v in scene.run().items()}

    def test_section4_three_identical_none_recorded_all_need_an_eye(self):
        found = self.three([])
        self.assertEqual({v.klass for v in found.values()}, {"needs_eye_no_recorded"})
        self.assertNotIn("unique_unrecorded", {v.klass for v in found.values()})
        self.assertEqual(len({v.group for v in found.values()}), 1)

    def test_section4_two_recorded_get_many_recorded_and_the_unrecorded_is_safe(self):
        found = self.three([0, 1])
        self.assertEqual(found["shoot-a"].klass, "needs_eye_many_recorded")
        self.assertEqual(found["shoot-b"].klass, "needs_eye_many_recorded")
        self.assertEqual(found["shoot-c"].klass, "safe_candidate")

    def test_section3_a_lone_recorded_member_with_unrecorded_copies_stays_recorded(self):
        found = self.three([0])
        self.assertEqual(found["shoot-a"].klass, "recorded")
        self.assertEqual(found["shoot-b"].klass, "safe_candidate")

    def test_rule12_a_pair_never_changes_the_raws_verdict(self):
        for jpeg_recorded in [False, True]:
            with self.subTest(jpeg_recorded=jpeg_recorded):
                raw, jpg = f"{LIB}/shoot-a/IMG_0001.ARW", f"{LIB}/shoot-a/IMG_0001.JPG"
                scene = Scene()
                scene.file(raw, content="R")
                alone = scene.run()["IMG_0001.ARW@shoot-a"]
                scene.file(f"{LIB}/shoot-a/IMG_0002.JPG", content="L")
                scene.file(jpg, content="J")
                if jpeg_recorded:
                    scene.catalog(SHOOT, [jpg], sidecar=1)
                found = scene.run()
                self.assertNotIn("paired JPEG", found["IMG_0002.JPG@shoot-a"].how_decided)
                raw_now = found["IMG_0001.ARW@shoot-a"]
                self.assertEqual((raw_now.klass, raw_now.how_decided), (alone.klass, alone.how_decided))
                self.assertIn("paired JPEG", found["IMG_0001.JPG@shoot-a"].how_decided)
                self.assertNotIn("paired JPEG", raw_now.how_decided)

    def test_rule12_a_jpeg_is_never_the_twin_of_a_raw(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW")
        scene.file(f"{LIB}/shoot-b/IMG_0001.JPG")
        scene.catalog(SHOOT, [f"{LIB}/shoot-b/IMG_0001.JPG"])
        found = scene.run()
        self.assertNotEqual(found["IMG_0001.ARW@shoot-a"].klass, "safe_candidate")
        self.assertIsNone(found["IMG_0001.ARW@shoot-a"].twin_file_id)

    def test_every_verdict_explains_itself_and_recorded_matches_the_catalogs(self):
        scene = Scene()
        scene.file(f"{LIB}/shoot-a/IMG_0001.ARW", index=1)
        scene.file(f"{LIB}/shoot-a/IMG_0002.ARW", index=1)
        scene.file(f"{LIB}/shoot-a/IMG_0003.ARW", placeholder=1)
        scene.file(f"{LIB}/shoot-a/IMG_0004.ARW", hashed=False)
        scene.file(f"{LIB}/shoot-a/IMG_0005.ARW", content="Z")
        scene.catalog(SHOOT, [f"{LIB}/shoot-a/IMG_0005.ARW"])
        norms = classify.recorded_norms(scene.conn, excluded_catalogs=[])
        found = classify.verdicts(scene.conn, library_roots=[LIB], catalog_roots=[CAT],
                                  excluded_catalogs=[], stat=scene.stat)
        self.assertEqual(len(found), 5)
        for v in found:
            self.assertTrue(v.how_decided)
            self.assertEqual(v.recorded, paths.norm(v.path_orig) in norms)


if __name__ == "__main__":
    unittest.main()
