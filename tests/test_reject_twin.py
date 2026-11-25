import os
import tempfile
import types
import unittest

from lrclean import paths, store
from lrclean.reject_twin import EMPTY, load_hashes, twin_candidates

SIZE = 100
MTIME = 1_700_000_000_000_000_000
PLACEHOLDER_ATTR = 0x400000
LIB_A = "C:/lib-a"
LIB_B = "C:/lib-b"
BAK = "C:/bak"
REJECT = "C:/lib-b/shoot-a/IMG_0001.ARW"
BAD = "C:/lib-a/shoot-b/IMG_0001.ARW"
GOOD = "C:/lib-b/shoot-c/IMG_0001.ARW"
OTHER = "C:/lib-b/shoot-d/IMG_0001.ARW"
EARLY = "C:/lib-a/shoot-a/IMG_0001.ARW"  # sorts before BAD


def _st(size=SIZE, mtime=MTIME, ino=1, dev=1, attrs=0):
    return types.SimpleNamespace(st_size=size, st_mtime_ns=mtime, st_ino=ino, st_dev=dev,
                                 st_file_attributes=attrs)


def _root_of(path):
    return LIB_A if path.startswith(LIB_A) else LIB_B


class World:
    """One temp database with scans, file and hash rows, and a fake `stat` per path."""

    def __init__(self, base):
        self.conn = store.connect(os.path.join(base, "work.db"))
        self.latest: dict[str, int] = {}
        self.index: dict[str, int] = {}
        self.stats: dict[str, object] = {}

    def new_scan(self, root, status="complete"):
        scan_id = store.begin_scan(self.conn, root, "files")
        store.finish_scan(self.conn, scan_id, status)
        self.latest[root] = scan_id
        return scan_id

    def file_index(self, path):
        return self.index.setdefault(path, len(self.index) + 10)

    def file(self, path, root, *, size=SIZE, mtime=MTIME, index=None, volume="c:", live=True):
        scan_id = self.latest.get(root) or self.new_scan(root)
        index = self.file_index(path) if index is None else index
        file_id = store.insert_row(
            self.conn, "file", scan_id=scan_id, volume=volume, path_norm=paths.norm(path),
            path_orig=path, ext="arw", size=size, mtime_ns=mtime, attrs=0, placeholder=0,
            container="", file_index=index, nlink=1, checked_at="")
        if live:
            self.stats[path] = _st(size, mtime, index)
        return file_id

    def hash(self, file_id, path, *, full="aa", size=SIZE, mtime=MTIME, index=None):
        index = self.file_index(path) if index is None else index
        store.put_hash(self.conn, file_id=file_id, file_index=index, size=size, mtime_ns=mtime,
                       sample="s", full=full, hashed_at="")

    def counted(self, path, root=None, **kw):
        self.hash(self.file(path, root or _root_of(path), **kw), path)

    def stat(self, path):
        found = self.stats[path] if path in self.stats else FileNotFoundError(path)
        if isinstance(found, OSError):
            raise found
        return found

    def candidates(self, path=REJECT, attrs_of=None, roots=(LIB_A, LIB_B)):
        index = load_hashes(self.conn, roots)
        found = twin_candidates(index, paths.norm(path), stat=self.stat, attrs_of=attrs_of)
        return [h.path_orig for h in found]


def _normal(w, path, root=None):
    w.counted(path, root)


def _hash_differs(**bad):
    def build(w, path, root=None):
        w.hash(w.file(path, root or _root_of(path)), path, **bad)
    return build


def _no_hash(w, path, root=None):
    w.file(path, root or _root_of(path))


def _other_volume(w, path, root=None):
    root = root or _root_of(path)
    w.new_scan(root)
    w.hash(w.file(path, root, volume="d:", live=False), path)
    w.new_scan(root)
    w.file(path, root)


def _live_differs(**bad):
    def build(w, path, root=None):
        w.counted(path, root)
        for field, value in bad.items():
            setattr(w.stats[path], field, value)
    return build


def _stat_raises(error):
    def build(w, path, root=None):
        w.counted(path, root)
        w.stats[path] = error
    return build


def _disagree(w, path, root=None):
    root = root or _root_of(path)
    w.new_scan(root)
    w.hash(w.file(path, root, live=False), path, full="bb")
    w.new_scan(root)
    w.counted(path, root)


def _partial(w, path, root=None):
    root = root or _root_of(path)
    w.counted(path, root)
    w.new_scan(root, "partial")
    w.counted(path, root)  # the partial scan holds the row and a counting hash: only its status stops it


def _absent_from_latest(w, path, root=None):
    w.counted(path, root)
    w.new_scan(root or _root_of(path))


def _backup_only(w, path, root=None):
    w.counted(path, BAK)


def _carried_hash(w, path, root=None):
    root = root or _root_of(path)
    w.counted(path, root)
    w.new_scan(root)  # a scan that skips the path
    w.new_scan(root)
    w.file(path, root)  # the latest scan holds the row and no hash


def _carried_hash_mtime_changed(w, path, root=None):
    # The size stays, so the path keeps its (full, size) group: only the hash row's validity stops it.
    root = root or _root_of(path)
    w.counted(path, root)
    w.new_scan(root)
    w.file(path, root, mtime=MTIME + 5)


def _build(w, reject_defect=_normal, bad_defect=_normal):
    reject_defect(w, REJECT)
    bad_defect(w, BAD)
    _normal(w, GOOD)


class WorldCase(unittest.TestCase):
    def world(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        w = World(tmp.name)
        self.addCleanup(w.conn.close)
        return w

    def assert_per_side(self, cases, bad_twin_left=(GOOD,)):
        for name, bad in cases.items():
            for side in ("reject", "twin"):
                with self.subTest(case=name, side=side):
                    w = self.world()
                    _build(w, **({"reject_defect": bad} if side == "reject" else {"bad_defect": bad}))
                    self.assertEqual(w.candidates(), [] if side == "reject" else list(bad_twin_left))


class CountedHashTests(WorldCase):
    def test_a_twin_is_returned_when_every_counting_condition_holds(self):
        w = self.world()
        _build(w)
        self.assertEqual(w.candidates(), [BAD, GOOD])

    def test_no_candidate_when_the_hash_does_not_count(self):
        self.assert_per_side({
            "size": _hash_differs(size=SIZE + 1),
            "mtime": _hash_differs(mtime=MTIME + 1),
            "index": _hash_differs(index=999),
            "empty full": _hash_differs(full=""),
            "null full": _hash_differs(full=None),
            "no hash": _no_hash,
            "other volume": _other_volume,
        })
        for name, full in (("null full", None), ("empty full", "")):  # both sides share the missing hash
            with self.subTest(case=name, side="both"):
                w = self.world()
                _build(w, _hash_differs(full=full), _hash_differs(full=full))
                self.assertEqual(w.candidates(), [])

    def test_no_candidate_when_stat_now_differs_from_the_row(self):
        self.assert_per_side({
            "size": _live_differs(st_size=SIZE + 1),
            "mtime": _live_differs(st_mtime_ns=MTIME + 1),
            "index": _live_differs(st_ino=999),
            "gone": _stat_raises(FileNotFoundError("gone")),
            "denied": _stat_raises(PermissionError("denied")),
        })

    def test_no_candidate_when_the_scans_do_not_give_one_counted_hash(self):
        self.assert_per_side({
            "latest scan partial": _partial,
            "absent from the latest scan": _absent_from_latest,
            "only in a backup root": _backup_only,
            "carried hash with another mtime": _carried_hash_mtime_changed,
        })

    def test_counting_rows_that_disagree_drop_the_path_whichever_row_is_picked(self):
        # The two rows are "aa" and "bb"; the other paths share one of them, so a wrong pick of either returns a twin.
        for full in ("aa", "bb"):
            for side in ("reject", "twin"):
                with self.subTest(others=full, side=side):
                    w = self.world()
                    offender, other = (REJECT, BAD) if side == "reject" else (BAD, REJECT)
                    _disagree(w, offender)
                    for path in (other, GOOD):
                        w.hash(w.file(path, _root_of(path)), path, full=full)
                    self.assertEqual(w.candidates(), [] if side == "reject" else [GOOD])

    def test_a_hash_carried_from_the_first_of_three_scans_counts(self):
        for side in ("reject", "twin"):
            with self.subTest(side=side):
                w = self.world()
                _build(w, **({"reject_defect": _carried_hash} if side == "reject" else {"bad_defect": _carried_hash}))
                self.assertEqual(w.candidates(), [BAD, GOOD])


class MatchTests(WorldCase):
    def test_the_empty_index_gives_nothing(self):
        self.assertEqual(twin_candidates(EMPTY, paths.norm(REJECT), stat=self.world().stat), [])

    def test_the_reject_alone_is_not_its_own_candidate(self):
        w = self.world()
        _normal(w, REJECT)
        self.assertEqual(w.candidates(), [])

    def test_paths_with_another_full_hash_or_size_are_no_twins(self):
        w = self.world()
        _normal(w, REJECT)
        w.hash(w.file(BAD, LIB_A), BAD, full="bb")
        w.hash(w.file(OTHER, LIB_B, size=SIZE + 1), OTHER, size=SIZE + 1)
        _normal(w, GOOD)
        self.assertEqual(w.candidates(), [GOOD])

    def test_two_passing_paths_come_back_in_path_norm_order(self):
        w = self.world()
        for path in (REJECT, OTHER, GOOD, BAD):
            _normal(w, path)
        self.assertEqual(w.candidates(), [BAD, GOOD, OTHER])

    def test_a_twin_that_is_gone_or_a_placeholder_is_dropped_between_a_good_one_before_and_after(self):
        for how in ("gone", "injected attrs", "stat attrs"):
            with self.subTest(how=how):
                w = self.world()
                _build(w)
                _normal(w, EARLY)
                attrs_of = None
                if how == "gone":
                    del w.stats[BAD]
                elif how == "injected attrs":
                    attrs_of = lambda path, st: PLACEHOLDER_ATTR if path == BAD else 0
                else:
                    w.stats[BAD].st_file_attributes = PLACEHOLDER_ATTR
                self.assertEqual(w.candidates(attrs_of=attrs_of), [EARLY, GOOD])


class TwoFilesTests(WorldCase):
    def test_a_junction_name_of_the_reject_is_dropped_and_a_later_path_returned(self):
        w = self.world()
        _normal(w, REJECT)
        shared = w.file_index(REJECT)
        w.hash(w.file(BAD, LIB_A, index=shared), BAD, index=shared)
        _normal(w, GOOD)
        self.assertEqual(w.candidates(), [GOOD])

    def test_one_field_of_the_pair_alone_still_leaves_a_candidate(self):
        for name, (same_index, dev) in {"same index, another device": (True, 2),
                                        "same device, another index": (False, 1)}.items():
            with self.subTest(case=name):
                w = self.world()
                _normal(w, REJECT)
                index = w.file_index(REJECT) if same_index else 777
                w.hash(w.file(BAD, LIB_A, index=index), BAD, index=index)
                w.stats[BAD].st_dev = dev
                self.assertEqual(w.candidates(), [BAD])

    def test_a_real_hard_link_of_the_reject_is_dropped_and_a_copy_returned(self):
        w = self.world()
        with tempfile.TemporaryDirectory() as lib:
            made = {name: os.path.join(lib, *name.split("/"))
                    for name in ("shoot-a/IMG_0001.ARW", "shoot-b/IMG_0001.ARW", "shoot-c/IMG_0001.ARW")}
            reject, link, copy = made.values()
            for path in made.values():
                os.makedirs(os.path.dirname(path))
            for path in (link, copy):
                with open(path, "wb") as handle:
                    handle.write(b"x" * SIZE)
            os.link(link, reject)
            scan_id = w.new_scan(lib)
            for path in made.values():
                st = os.stat(path)
                index = paths.signed64(st.st_ino)
                file_id = store.insert_row(
                    w.conn, "file", scan_id=scan_id, volume=paths.volume(path), path_norm=paths.norm(path),
                    path_orig=path, ext="arw", size=st.st_size, mtime_ns=st.st_mtime_ns, attrs=0,
                    placeholder=0, container="", file_index=index, nlink=1, checked_at="")
                w.hash(file_id, path, size=st.st_size, mtime=st.st_mtime_ns, index=index)
            found = twin_candidates(load_hashes(w.conn, [lib]), paths.norm(reject))
            self.assertEqual([h.path_orig for h in found], [copy])


if __name__ == "__main__":
    unittest.main()
