import csv
import os
import tempfile
import types
import unittest
from collections import Counter

from lrclean import copies, paths, reject_archive, reject_twin
from lrclean.backup_plan import Row
from lrclean.classify import Coverage
from lrclean.mover_journal import COLUMNS
from test_rejects_verdicts import view

LIB = "C:/lib"
ARCH = ["D:/arch1", "D:/arch2"]
SIZE = 10
MTIME = 1_700_000_000_000_000_000
PLACEHOLDER_ATTR = 0x400000
COMPLETE = Coverage(True, [])
TWIN = "C:/lib/shoot-live/IMG_0001.ARW"  # the kept file of the base group
COPY = "C:/lib/shoot-a/IMG_0001.ARW"  # the clean copy that goes beside every case
X = "C:/lib/shoot-x/IMG_0001.ARW"  # the file a case is about
N = paths.norm


def _forbid(*args, **kwargs):
    raise AssertionError("reached a real stat, attribute read or hash")


def run_build(files, views, evidence, hashes, purged, *, coverage=COMPLETE, stat=_forbid, attrs_of=_forbid,
              hasher=_forbid):
    return copies.build(files, views, evidence, hashes, purged, photo_root=LIB, coverage=coverage, stat=stat,
                        attrs_of=attrs_of, hasher=hasher)


def stat_of(size=SIZE, mtime=MTIME, ino=1, attrs=0):
    return types.SimpleNamespace(st_size=size, st_mtime_ns=mtime, st_ino=ino, st_dev=1, st_file_attributes=attrs)


class Scene:
    """Rows, hashes, fake stats, two archive roots and catalog views, all in memory."""

    def __init__(self):
        self.rows, self.hashed, self.views, self.stats = [], [], [], {}
        self.manifest, self.verified = [{}, {}], [set(), set()]
        self.hash_of, self.calls, self.hash_calls = {}, [], []
        self.purged = copies.Purged({}, 0, 0)

    def row(self, path, *, size=SIZE, mtime=MTIME, index=None, flag=0):
        index = len(self.rows) + 10 if index is None else index
        self.rows.append({"path_norm": N(path), "path_orig": path, "ext": path.rsplit(".", 1)[1].lower(),
                          "size": size, "mtime_ns": mtime, "file_index": index, "placeholder": flag})
        return index

    def add(self, path, *, full="aa", now=None, hash_diff=None, missing=(), attrs=0, **fields):
        """A file with a clean archive copy on both roots; `now` changes the stat, `hash_diff` the counted hash."""
        index = self.row(path, **fields)
        size, mtime = fields.get("size", SIZE), fields.get("mtime", MTIME)
        if full:
            diff = {"size": size, "mtime_ns": mtime, "file_index": index, **(hash_diff or {})}
            self.hashed.append(reject_twin.Hashed(N(path), path, diff["size"], diff["mtime_ns"], diff["file_index"], full))
        if now != "gone":
            same = {"size": size, "mtime": mtime, "ino": index, "attrs": attrs}
            self.stats[N(path)] = now if isinstance(now, Exception) else stat_of(**{**same, **(now or {})})
        for root in (r for r in (0, 1) if r not in missing):
            dest = f"{ARCH[root]}/{len(self.rows)}-{path.rsplit('/', 1)[1]}"
            self.manifest[root].setdefault(N(path), []).append(Row(path, dest, size, mtime, "copy", ""))
            self.verified[root].add((N(path), N(dest), size))
            self.stats[N(dest)] = stat_of(size, mtime)
        return index

    def cat(self, role="shoot", **kw):
        self.views.append(view(source=f"C:/cats/{len(self.views)}.lrcat", role=role, **kw))

    def stat(self, path):
        self.calls.append(N(path))
        found = self.stats.get(N(path))
        if found is None:
            raise FileNotFoundError(path)
        if isinstance(found, Exception):
            raise found
        return found

    def attrs_of(self, path, st):
        return st.st_file_attributes

    def hasher(self, path):
        self.hash_calls.append(N(path))
        if N(path) not in self.hash_of:
            raise AssertionError(f"hashed {path}")
        found = self.hash_of[N(path)]
        if isinstance(found, Exception):
            raise found
        return found

    def hashes(self):
        groups = {}
        for hashed in sorted(self.hashed, key=lambda h: h.path_norm):
            groups.setdefault((hashed.full, hashed.size), []).append(hashed)
        return reject_twin.HashIndex({h.path_norm: h for h in self.hashed}, {k: tuple(v) for k, v in groups.items()})

    def evidence(self):
        return [reject_archive.RootEvidence(ARCH[i], self.manifest[i], frozenset(self.verified[i])) for i in (0, 1)]

    def build(self, **kw):
        return run_build(self.rows, self.views, self.evidence(), self.hashes(), self.purged, stat=self.stat,
                         attrs_of=self.attrs_of, hasher=self.hasher, **kw)


def base():
    """A kept file of the live catalog and a clean copy of it, which goes."""
    scene = Scene()
    scene.add(TWIN)
    scene.add(COPY)
    scene.cat("live", picks={N(TWIN): [0]})
    return scene


def held(built):
    return Counter({name: files for name, (files, _) in built.examined.items() if files})


def listed(built):
    return [row.path for row in built.rows]


def purging(size=SIZE, sha="zz"):
    return copies.Purged({size: frozenset({sha})}, 1, 1)


def _recorded(scene, how):
    path = X.replace(".ARW", ".JPG") if how == "sidecar of a raw" else X
    if how == "sidecar of a raw":
        scene.add(path)
        scene.cat("shoot", photos=[N(X)], sidecars=[N(path)])
    else:
        role, kw = {"live": ("live", {"picks": {N(X): [0]}}), "shoot": ("shoot", {"picks": {N(X): [0]}}),
                    "relative form only": ("shoot", {"photos": [N(X)]}),
                    "live and rejected": ("live", {"picks": {N(X): [-1]}}),
                    "shoot and rejected": ("shoot", {"picks": {N(X): [-1]}})}[how]
        scene.add(X)
        scene.cat(role, **kw)


def _twin_rejected(scene, how):
    r, k, y = "C:/lib/shoot-r/IMG_0002.ARW", "C:/lib/shoot-k/IMG_0002.ARW", "C:/lib/shoot-y/IMG_0002.ARW"
    builder = how.startswith("hashed by the builder")
    scene.add(r, full="bb", size=SIZE + 1 if how.endswith("another size") else SIZE)
    scene.add(y, full=None if builder else "bb")
    scene.cat("live", picks={N(r): [-1]})
    if builder:
        scene.hash_of[N(y)] = "bb"
        scene.purged = purging(sha="bb")
        assert scene.hash_of[N(y)] == next(iter(scene.purged.by_size[SIZE])) != "aa"  # the builder's digest, not the stored one
        assert (scene.hashed[-1].size == SIZE) != how.endswith("another size")  # the twin's size is the one asked for
    if how == "relative form only":
        scene.add(k, full="bb")
        scene.cat("live", photos=[N(k)])
    if how == "kept by a shoot catalog only":
        scene.add(k, full="bb")
        scene.cat("shoot", picks={N(k): [0]})
    if how in ("where (b) would match", "kept by a shoot catalog only"):
        scene.purged = purging(sha="bb")


CASES = [  # (name, setup(scene), held reasons beside the base's one `recorded` twin)
    ("outside_photo_root", lambda s: s.add("C:/other/IMG_0001.ARW"), {"outside_photo_root": 1}),
    ("placeholder, the row's flag", lambda s: s.add(X, flag=1), {"placeholder": 1}),
    ("placeholder, the attributes", lambda s: s.add(X, attrs=PLACEHOLDER_ATTR), {"placeholder": 1}),
    ("work, a pick", lambda s: (s.add(X), s.cat("old_state", picks={N(X): [1]})), {"work": 1}),
    ("work, develop work", lambda s: (s.add(X), s.cat("old_state", picks={N(X): [0]}, developed={N(X): [1]})),
     {"work": 1}),
    ("work, an unreadable state", lambda s: (s.add(X), s.cat("old_state", picks={N(X): [0]}, developed={N(X): [None]})),
     {"work": 1}),
    ("sidecar on disk", lambda s: (s.add(X), s.row("C:/lib/shoot-x/IMG_0001.XMP")), {"sidecar": 1}),
    ("sidecar on disk, acr", lambda s: (s.add(X), s.row("C:/lib/shoot-x/IMG_0001.ACR")), {"sidecar": 1}),
    ("sidecar on disk, double extension", lambda s: (s.add(X), s.row("C:/lib/shoot-x/IMG_0001.ARW.XMP")),
     {"sidecar": 1}),
    ("sidecar on disk, a photo named with a photo extension",
     lambda s: (s.add(X + ".ARW"), s.row("C:/lib/shoot-x/IMG_0001.ARW.xmp")), {"sidecar": 1}),
    ("sidecar only in a catalog", lambda s: (s.add(X), s.cat("old_state", sidecars=[N("C:/lib/shoot-x/IMG_0001.xmp")])),
     {"sidecar": 1}),
    ("twin_rejected alone", lambda s: _twin_rejected(s, "alone"), {"twin_rejected": 1, "recorded": 1}),
    ("twin_rejected, the other file recorded by the relative form only",
     lambda s: _twin_rejected(s, "relative form only"), {"twin_rejected": 1, "recorded": 2}),
    ("twin_rejected where (b) would match", lambda s: _twin_rejected(s, "where (b) would match"),
     {"twin_rejected": 1, "recorded": 1}),
    ("twin_rejected, the copy hashed by the builder, where (b) would match",
     lambda s: _twin_rejected(s, "hashed by the builder"), {"twin_rejected": 1, "recorded": 1}),
    ("twin_rejected, another file kept by a shoot catalog only, where (b) would match",
     lambda s: _twin_rejected(s, "kept by a shoot catalog only"), {"twin_rejected": 1, "recorded": 2}),
    ("changed, size", lambda s: s.add(X, now={"size": SIZE + 1}), {"changed": 1}),
    ("changed, time", lambda s: s.add(X, now={"mtime": MTIME + 1}), {"changed": 1}),
    ("changed, index", lambda s: s.add(X, now={"ino": 999}), {"changed": 1}),
    ("changed, gone", lambda s: s.add(X, now="gone"), {"changed": 1}),
    ("changed, the stat fails", lambda s: s.add(X, now=PermissionError()), {"changed": 1}),
    ("changed, nested roots differ in size", lambda s: s.row(X, size=SIZE + 1, index=s.add(X)), {"changed": 1}),
    ("changed, nested roots differ in time", lambda s: s.row(X, mtime=MTIME + 1, index=s.add(X)), {"changed": 1}),
    ("changed, nested roots differ in index", lambda s: s.row(X, index=s.add(X) + 1), {"changed": 1}),
    ("no_archive_copy on the first root only", lambda s: (setattr(s, "purged", purging()), s.add(X, full=None, missing=(0,))),
     {"no_archive_copy": 1}),
    ("no_archive_copy on the second root only", lambda s: (setattr(s, "purged", purging()), s.add(X, full=None, missing=(1,))),
     {"no_archive_copy": 1}),
    ("no_keeper, no purged reject matches", lambda s: (setattr(s, "purged", purging()), s.add(X, full=None),
                                                       s.hash_of.update({N(X): "yy"})), {"no_keeper": 1}),
    ("no_keeper, its only identical files are unrecorded too", lambda s: (s.add(X, full="cc"), s.add("C:/lib/shoot-y/IMG_0001.ARW", full="cc")),
     {"no_keeper": 2}),
]


class ReasonTest(unittest.TestCase):
    def test_each_reason_alone_holds_the_file_and_the_clean_copy_goes(self):
        for name, setup, expected in CASES:
            with self.subTest(name):
                scene = base()
                setup(scene)
                built = scene.build()
                self.assertEqual(listed(built), [COPY])
                self.assertEqual(held(built), Counter({"recorded": 1}) + Counter(expected))

    def test_the_hold_for_a_builder_hashed_copy_needs_the_rejected_twin_of_its_size_and_hashes_once(self):
        y = "C:/lib/shoot-y/IMG_0002.ARW"
        for how, rows in (("hashed by the builder", [COPY]), ("hashed by the builder, another size", [COPY, y])):
            with self.subTest(how):
                scene = base()
                _twin_rejected(scene, how)
                built = scene.build()
                self.assertEqual(listed(built), rows)
                self.assertEqual(scene.hash_calls, [N(y)])

    def test_recorded_by_a_live_or_shoot_catalog_holds_the_file(self):
        for how in ("live", "shoot", "relative form only", "live and rejected", "shoot and rejected", "sidecar of a raw"):
            with self.subTest(how):
                scene = base()
                _recorded(scene, how)
                built = scene.build()
                self.assertEqual(listed(built), [COPY])
                self.assertEqual(held(built), Counter({"recorded": 2}))

    def test_what_is_not_a_record_or_a_sidecar_lets_the_file_go(self):
        old_state = ("old_state", {"photos": [N(X)]})
        excluded = ("shoot", {"photos": [N(X)], "excluded": True})
        other_xmp = ("old_state", {"sidecars": [N("C:/lib/shoot-x/IMG_0002.xmp")]})
        jpeg_listed = ("old_state", {"sidecars": [N(X.replace(".ARW", ".JPG"))]})
        dotted = "C:/lib/shoot-x/IMG_0001.2019.ARW"
        self.assertNotEqual(dotted, X)
        cases = {"old state": (old_state, X, ()), "excluded catalog": (excluded, X, ()),
                 "another base name": (other_xmp, X, ("C:/lib/shoot-x/IMG_0002.XMP",)),
                 "a JPEG listed as a sidecar": (jpeg_listed, X, ()),
                 "a dotted name's first part": (("old_state", {}), dotted, ("C:/lib/shoot-x/IMG_0001.XMP",))}
        for name, ((role, kw), file, extra) in cases.items():
            with self.subTest(name):
                scene = base()
                scene.add(file)
                scene.cat(role, **kw)
                for row in extra:
                    scene.row(row)
                built = scene.build()
                self.assertEqual(listed(built), [COPY, file])
                self.assertEqual(held(built), Counter({"recorded": 1}))

    def test_a_file_no_purged_reject_matches_and_no_group_holds_is_counted_nowhere_and_never_statted(self):
        scene = base()
        scene.add("C:/lib/shoot-x/IMG_0009.ARW", full="uu", size=SIZE + 5)
        built = scene.build()
        self.assertEqual(held(built), Counter({"recorded": 1}))
        self.assertNotIn(N("C:/lib/shoot-x/IMG_0009.ARW"), scene.calls)
        self.assertEqual(max(Counter(scene.calls).values()), 1)


class ClassTest(unittest.TestCase):
    def test_class_a_names_the_first_kept_twin_in_path_order(self):
        k1, k2, early = "C:/lib/shoot-k1/IMG_0001.ARW", "C:/lib/shoot-k2/IMG_0001.ARW", "C:/lib/shoot-e/IMG_0001.ARW"
        cases = (("first in path order, added last", [k2, k1], {"picks": {N(p): [0] for p in (k2, k1, TWIN)}}, k1),
                 ("a relative-only record sorts first", [early], {"picks": {N(TWIN): [0]}, "photos": [N(early)]}, TWIN))
        for name, others, kw, expected in cases:
            with self.subTest(name):
                scene = Scene()
                for path in (*others, TWIN, X):
                    scene.add(path)
                scene.cat("live", **kw)
                twins = {r.path: r.twin for r in scene.build().rows}
                self.assertEqual(twins[X], expected)

    def test_what_is_no_twin_leaves_the_copy_without_a_keeper(self):
        other = "C:/lib/shoot-e/IMG_0001.ARW"
        cases = (("a shoot catalog's record", "shoot", 0, None, 1), ("an old state's record", "old_state", 0, None, 2),
                 ("a placeholder", "live", PLACEHOLDER_ATTR, None, 1), ("the copy's own file under another path", "live", 0, 77, 1))
        for name, role, attrs, index, no_keeper in cases:
            with self.subTest(name):
                scene = Scene()
                scene.add(other, attrs=attrs, index=index)
                scene.add(X, index=index)
                scene.cat(role, picks={N(other): [0]})
                built = scene.build()
                self.assertEqual(listed(built), [])
                self.assertEqual(held(built)["no_keeper"], no_keeper)

    def test_a_class_a_copy_that_matches_a_purged_reject_keeps_its_twin_and_is_not_hashed(self):
        scene = base()
        scene.purged = purging(sha="aa")
        built = scene.build()
        self.assertEqual([(r.path, r.twin) for r in built.rows], [(COPY, TWIN)])
        self.assertEqual(scene.hash_calls, [])

    def test_class_b_i_needs_the_purged_size_and_sha256_and_trusts_only_a_matching_counted_hash(self):
        stale = {"size": {"size": SIZE + 1}, "time": {"mtime_ns": MTIME + 1}, "index": {"file_index": 5}}
        cases = [("sha256 and size match, hashed by the builder", {"full": None}, "zz", SIZE, True, 1),
                 ("a counted hash is taken, no read", {"full": "zz"}, None, SIZE, True, 0),
                 ("sha256 differs", {"full": None}, "yy", SIZE, False, 1),
                 ("size differs", {"full": None, "size": SIZE + 1}, "zz", SIZE, False, 0),
                 *((f"a stale counted hash, {k}", {"full": "zz", "hash_diff": diff}, "yy", SIZE, False, 1)
                   for k, diff in stale.items())]
        for name, kw, sha, purged_size, goes, reads in cases:
            with self.subTest(name):
                scene = Scene()
                scene.purged = purging(purged_size)
                scene.add(X, **kw)
                if sha:
                    scene.hash_of[N(X)] = sha
                built = scene.build()
                self.assertEqual(listed(built), [X] if goes else [])
                self.assertEqual(len(scene.hash_calls), reads)
                self.assertEqual([r.twin for r in built.rows], [""] * goes)

    def test_class_b_ii_goes_for_an_on_disk_reject_of_a_shoot_catalog_unless_something_keeps_it(self):
        reject = "C:/lib/shoot-r/IMG_0001.ARW"
        cases = [("goes", [("shoot", {"picks": {N(reject): [-1]}})], True),
                 ("the live catalog records it", [("shoot", {"picks": {N(reject): [-1]}}), ("live", {"photos": [N(reject)]})], False),
                 ("another catalog keeps it", [("shoot", {"picks": {N(reject): [-1]}}), ("shoot", {"picks": {N(reject): [0]}})], False),
                 ("it has work", [("shoot", {"picks": {N(reject): [-1]}}), ("old_state", {"picks": {N(reject): [1]}})], False)]
        for name, cats, goes in cases:
            with self.subTest(name):
                scene = Scene()
                scene.add(reject)
                scene.add(X)
                for role, kw in cats:
                    scene.cat(role, **kw)
                built = scene.build()
                self.assertEqual(X in listed(built), goes)

    def test_rows_come_in_path_order(self):
        scene = base()
        late, early = "C:/lib/shoot-z/IMG_0001.ARW", "C:/lib/shoot-b/IMG_0001.ARW"
        for path in (late, early):
            scene.add(path)
        self.assertEqual(listed(scene.build()), [COPY, early, late])


class RefusalTest(unittest.TestCase):
    def test_incomplete_coverage_is_refused_before_any_stat(self):
        scene = base()
        with self.assertRaises(copies.Incomplete):
            scene.build(coverage=Coverage(False, ["C:/lib has no complete scan"]))
        self.assertEqual(scene.calls, [])

    def test_a_file_that_fails_to_read_is_counted_under_changed(self):
        scene = Scene()
        scene.purged = purging()
        scene.add(X, full=None)
        scene.hash_of[N(X)] = PermissionError()
        self.assertEqual(held(scene.build()), Counter({"changed": 1}))

    def test_no_file_held_for_another_reason_and_none_without_a_purged_size_reaches_the_hasher(self):
        scene = Scene()
        scene.purged = purging()
        scene.add(X, full=None)
        scene.cat("live", picks={N(X): [0]})
        scene.add("C:/lib/shoot-y/IMG_0001.ARW", full=None, size=SIZE + 1)
        built = scene.build()
        self.assertEqual(held(built), Counter({"recorded": 1}))
        self.assertEqual(scene.hash_calls, [])
        self.assertEqual(max(Counter(scene.calls).values()), 1)

    def test_summary_counts_hold_every_reason_in_order_with_bytes(self):
        built = base().build()
        self.assertEqual(tuple(built.examined), copies.REASONS)
        self.assertEqual(built.examined["recorded"], (1, SIZE))
        self.assertEqual(built.examined["no_keeper"], (0, 0))


class PurgedTest(unittest.TestCase):
    SHA = "ab" * 32

    def write(self, folder, name, rows, header=COLUMNS):
        with open(os.path.join(folder, name), "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)

    def row(self, kind="reject", state="purged", size="10", sha=None):
        return ["C:/lib/x.arw", kind, "", size, sha if sha is not None else self.SHA.upper(), state, ""]

    def test_only_a_reject_in_state_purged_counts(self):
        with tempfile.TemporaryDirectory() as out:
            self.write(out, "purge-20260101T000000Z.csv", [
                self.row(), self.row(state="held", size="", sha=""), self.row(kind="sidecar"), self.row(kind="copy"),
                self.row(state="open_purge"), ["C:/lib/y.arw", "reject", "", "", "", "held", "reason"]])
            self.write(out, "purge-20260102T000000Z.csv", [self.row(size="20", sha="cd" * 32)])
            self.write(out, "purge-notabatch.csv", [], header=("junk",))
            purged = copies.read_purged(out)
        self.assertEqual(dict(purged.by_size), {10: frozenset({self.SHA}), 20: frozenset({"cd" * 32})})
        self.assertEqual((purged.results, purged.rejects), (2, 2))

    def test_no_result_file_gives_nothing(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(copies.read_purged(out), copies.Purged({}, 0, 0))

    def test_an_unreadable_file_a_bad_header_or_a_bad_counted_row_is_refused(self):
        header = COLUMNS[:-1]
        cases = {"header lacks a column": ([], header, None), "bytes not a number": ([self.row(size="1x")], COLUMNS, None),
                 "bytes a non-ASCII digit": ([self.row(size="\u00b2")], COLUMNS, None),
                 "sha256 too short": ([self.row(sha="ab")], COLUMNS, None), "sha256 not hex": ([self.row(sha="zz" * 32)], COLUMNS, None),
                 "undecodable": (None, COLUMNS, b"\xff\xfe\x00\xd8"), "a short row": ([["C:/lib/x.arw", "reject"]], COLUMNS, None)}
        for name, (rows, head, raw) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as out:
                if raw is not None:
                    with open(os.path.join(out, "purge-20260101T000000Z.csv"), "wb") as handle:
                        handle.write(raw)
                else:
                    self.write(out, "purge-20260101T000000Z.csv", rows, header=head)
                with self.assertRaises(copies.PurgeResultError):
                    copies.read_purged(out)


if __name__ == "__main__":
    unittest.main()
