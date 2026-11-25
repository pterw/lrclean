import csv
import os
import tempfile
import threading
import types
import unittest

import synth
from lrclean import paths, store
from lrclean.backup_plan import Row
from lrclean.reject_archive import RootEvidence
from lrclean.reject_out import SummaryInput, write_outputs
from lrclean.rejects import CatalogView, decide, latest_views
from test_catalogs import Tmp

SIZE = 10
MTIME = 1_700_000_000_000_000_000
PLACEHOLDER_ATTR = 0x400000
ROOTS = ["d:/arch1", "d:/arch2", "d:/arch3"]
P = "d:/lib/shoot-a/img_0001.arw"
SIDE = "d:/lib/shoot-a/img_0001.xmp"
SIDE_ORIG = "D:/Lib/shoot-a/IMG_0001.xmp"
OK, OUTSIDE, CHANGED, NOCOPY = "ok", "outside_backup", "changed_since_backup", "no_archive_copy"


def view(source="C:/cats/a.lrcat", role="shoot", status="ok", excluded=False, picks=None,
         developed=None, photos=(), sidecars=(), orig=None):
    picks = picks or {}
    developed = developed if developed is not None else {k: [0] * len(v) for k, v in picks.items()}
    return CatalogView(source, role, status, excluded, picks, developed,
                       set(photos) | set(picks), set(sidecars), orig or {},
                       frozenset(picks), {k: len(v) for k, v in picks.items()})


class World:
    """A fake file system and the manifests of `n` archive roots, all in memory."""

    def __init__(self, n=2):
        self.n = n
        self.files = {}
        self.rows = [{} for _ in range(n)]
        self.verified = [set() for _ in range(n)]

    def stat(self, path):
        found = self.files.get(paths.norm(path))
        if found is None:
            raise FileNotFoundError(path)
        if isinstance(found, Exception):
            raise found
        size, mtime, attrs = found
        return types.SimpleNamespace(st_size=size, st_mtime_ns=mtime, st_file_attributes=attrs)

    def file(self, path, size=SIZE, attrs=0):
        self.files[paths.norm(path)] = (size, MTIME, attrs)

    def archive(self, path, root, bad=None, size=SIZE):
        """The manifest row, verified entry and destination file of `path` on root `root` (1-based)."""
        key = paths.norm(path)
        self.rows[root - 1].pop(key, None)
        self.verified[root - 1] -= {k for k in self.verified[root - 1] if k[0] == key}
        if bad == "outside":
            return
        dest = f"{ROOTS[root - 1]}/{path.rsplit('/', 1)[1]}"
        mtime = MTIME + 1 if bad == "changed" else MTIME
        self.rows[root - 1].setdefault(paths.norm(path), []).append(Row(path, dest, size, mtime, "copy", ""))
        if bad != "nocopy":
            self.verified[root - 1].add((paths.norm(path), paths.norm(dest), size))
        self.file(dest, size, PLACEHOLDER_ATTR if bad == "dest_placeholder" else 0)

    def clean(self, path, size=SIZE):
        self.file(path, size)
        for root in range(1, self.n + 1):
            self.archive(path, root, size=size)

    def evidence(self):
        return [RootEvidence(ROOTS[i], self.rows[i], frozenset(self.verified[i])) for i in range(self.n)]

    def decide(self, views, complete=True, **kw):
        out = decide(views, self.evidence(), coverage_complete=complete, stat=self.stat, **kw)
        return {r.path_norm: r for r in out}


def scene(n=2):
    """A reject P with one sidecar, both clean on every root, rejected by one shoot view."""
    world = World(n)
    world.clean(P)
    world.clean(SIDE)
    rejecting = view(picks={P: [-1]}, sidecars=[SIDE], orig={P: P, SIDE: SIDE})
    return world, [rejecting]


def verdict_of(world, views, **kw):
    return world.decide(views, **kw)[P]


# One mutation per verdict, applied to a scene; `root` is the root the archive verdicts hit.
def _live_keeps(world, views, root):
    views.append(view("C:/cats/live.lrcat", "live", picks={P: [0]}))


def _other_keeps(world, views, root):
    views.append(view("C:/cats/b.lrcat", picks={P: [0]}))


def _old_state_work(world, views, root):
    views.append(view("C:/cats/old.lrcat", "old_state", picks={P: [0]}, developed={P: [1]}))


def _missing(world, views, root):
    del world.files[paths.norm(P)]


def _side_placeholder(world, views, root):
    world.file(SIDE, attrs=PLACEHOLDER_ATTR)


def _archive(bad):
    def mutate(world, views, root):
        world.archive(P, root, bad)
    return mutate


def _incomplete(world, views, root):
    world.complete = False


MUTATORS = {
    "coverage_incomplete": _incomplete,
    "kept_in_live": _live_keeps,
    "kept": _other_keeps,
    "kept_for_work": _old_state_work,
    "missing": _missing,
    "placeholder": _side_placeholder,
    "outside_backup": _archive("outside"),
    "changed_since_backup": _archive("changed"),
    "no_archive_copy": _archive("nocopy"),
}
ORDER = list(MUTATORS)


def build(*names, roots=None):
    world, views = scene()
    world.complete = True
    for name, root in zip(names, roots or [1] * len(names)):
        MUTATORS[name](world, views, root)
    return verdict_of(world, views, complete=world.complete)


class VerdictOrderTest(unittest.TestCase):
    def test_each_verdict_alone(self):
        for name in ORDER:
            with self.subTest(name):
                self.assertEqual(build(name).verdict, name)

    def test_clean_reject_is_on_list_with_its_sidecar(self):
        world, views = scene()
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.held_by, got.archive, got.sidecars), ("on_list", "", [OK, OK], [SIDE]))

    def test_the_earlier_of_two_adjacent_verdicts_wins_rule_30(self):
        for first, second in zip(ORDER, ORDER[1:]):
            for roots in ([1, 2], [2, 1]):  # the earlier verdict on the later root too
                with self.subTest(first=first, second=second, roots=roots):
                    self.assertEqual(build(second, first, roots=roots).verdict, first)

    def test_kept_by_and_rejected_by_are_filled_when_coverage_is_incomplete(self):
        world, views = scene()
        _other_keeps(world, views, 1)
        got = verdict_of(world, views, complete=False)
        self.assertEqual((got.verdict, got.rejected_by, got.kept_by),
                         ("coverage_incomplete", [("C:/cats/a.lrcat", "shoot")], [("C:/cats/b.lrcat", "shoot")]))

    def test_placeholder_reject_itself_holds(self):
        world, views = scene()
        world.file(P, attrs=PLACEHOLDER_ATTR)
        self.assertEqual(verdict_of(world, views).verdict, "placeholder")

    def test_injected_attrs_of_decides_placeholder(self):
        world, views = scene()
        got = verdict_of(world, views, attrs_of=lambda path, st: PLACEHOLDER_ATTR if "img_0001.arw" in path else 0)
        self.assertEqual(got.verdict, "placeholder")


class SidecarHoldTest(unittest.TestCase):
    def check(self, mutate, verdict):
        world, views = scene()
        mutate(world, views)
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.held_by), (verdict, SIDE))
        return got

    def test_a_sidecar_failing_alone_holds_the_whole_row(self):
        def kept(world, views):
            views.append(view("C:/cats/b.lrcat", picks={SIDE: [0]}))

        def work(developed, pick):
            def mutate(world, views):
                views.append(view("C:/cats/old.lrcat", "old_state", picks={SIDE: [pick]}, developed={SIDE: [developed]}))
            return mutate

        def archive(bad):
            def mutate(world, views):
                world.archive(SIDE, 1, bad)
            return mutate

        cases = {
            "kept": (kept, "kept"),
            "develop work on its own path": (work(1, 0), "kept_for_work"),
            "pick on its own path": (work(0, 1), "kept_for_work"),
            "placeholder": (lambda w, v: w.file(SIDE, attrs=PLACEHOLDER_ATTR), "placeholder"),
            "outside_backup": (archive("outside"), OUTSIDE),
            "changed_since_backup": (archive("changed"), CHANGED),
            "no_archive_copy": (archive("nocopy"), NOCOPY),
        }
        for name, (mutate, verdict) in cases.items():
            with self.subTest(name):
                self.check(mutate, verdict)

    def test_an_absent_sidecar_is_ignored_and_not_listed(self):
        world, views = scene()
        del world.files[paths.norm(SIDE)]
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.sidecars, got.sidecar_bytes), ("on_list", [], 0))

    def test_a_sidecar_a_second_catalog_records_as_a_photo_it_does_not_reject_gives_kept(self):
        world, views = scene()
        views.append(view("C:/cats/b.lrcat", picks={SIDE: [-1]}))
        self.assertEqual(verdict_of(world, views).verdict, "on_list")
        views[1] = view("C:/cats/b.lrcat", photos=[SIDE])
        self.assertEqual(verdict_of(world, views).verdict, "kept")

    def test_a_sidecar_only_the_second_rejecting_view_records_holds_by_its_own_spelling(self):
        world = World()
        world.clean(P)
        world.file(SIDE_ORIG)
        world.archive(SIDE_ORIG, 1)
        world.archive(SIDE_ORIG, 2, "nocopy")
        first = view("C:/cats/a.lrcat", picks={P: [-1]})
        second = view("C:/cats/b.lrcat", picks={P: [-1]}, sidecars=[SIDE], orig={SIDE: SIDE_ORIG})
        got = verdict_of(world, [first, second])
        self.assertEqual((got.verdict, got.held_by, got.sidecars, got.archive),
                         (NOCOPY, SIDE_ORIG, [SIDE_ORIG], [OK, NOCOPY]))

    def test_the_verdict_comes_before_the_holder_a_kept_sidecar_beats_no_archive_copy(self):
        world, views = scene()
        world.archive(P, 2, "nocopy")
        views.append(view("C:/cats/b.lrcat", picks={SIDE: [0]}))
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.held_by), ("kept", SIDE))

    def test_the_reject_decides_before_its_sidecar_when_both_fail_the_same_way(self):
        world, views = scene()
        world.file(P, attrs=PLACEHOLDER_ATTR)
        world.file(SIDE, attrs=PLACEHOLDER_ATTR)
        self.assertEqual(verdict_of(world, views).held_by, "")

    def test_bytes_and_sidecar_bytes_are_exact(self):
        world = World()
        world.clean(P, 7)
        world.files[paths.norm(P)] = (7, MTIME, 0)
        world.clean(SIDE, 3)
        other = "d:/lib/shoot-a/img_0001.jpg"
        world.clean(other, 5)
        views = [view(picks={P: [-1]}, sidecars=[SIDE, other])]
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.bytes, got.mtime_ns, got.sidecar_bytes, got.sidecars),
                         ("on_list", 7, MTIME, 8, [other, SIDE]))


class ArchiveRootTest(unittest.TestCase):
    def check(self, n, bad_roots, target=P):
        world, views = scene(n)
        for root in bad_roots:
            world.archive(target, root, "nocopy")
        got = verdict_of(world, views)
        want = [NOCOPY if r in bad_roots else OK for r in range(1, n + 1)]
        self.assertEqual((got.verdict, got.archive), (NOCOPY, want))

    def test_each_root_holds_the_row_alone_first_last_and_in_the_middle_l14(self):
        for n, bad in ((2, {1}), (2, {2}), (2, {1, 2}), (3, {1}), (3, {2}), (3, {3}), (3, {1, 3})):
            with self.subTest(n=n, bad=bad):
                self.check(n, bad)

    def test_a_sidecar_lacking_evidence_on_one_root_shows_in_that_roots_state(self):
        for n, bad in ((2, {2}), (3, {2}), (3, {3})):
            with self.subTest(n=n, bad=bad):
                self.check(n, bad, SIDE)

    def test_the_root_state_is_the_first_non_ok_over_the_reject_then_its_sidecars(self):
        world, views = scene()
        for target, root, state in ((P, 1, "outside"), (SIDE, 1, "changed"), (SIDE, 2, "nocopy")):
            world.archive(target, root, state)
        self.assertEqual(verdict_of(world, views).archive, [OUTSIDE, NOCOPY])

    def test_a_reject_without_a_copy_and_a_sidecar_outside_backup_gives_outside_backup(self):
        world, views = scene()
        world.archive(P, 1, "nocopy")
        world.archive(SIDE, 2, "outside")
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.held_by), (OUTSIDE, SIDE))

    def test_an_unreadable_reject_holds_as_changed_and_is_not_missing(self):
        world, views = scene()
        world.files[paths.norm(P)] = PermissionError("denied")
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.bytes, got.mtime_ns), (CHANGED, 0, 0))

    def test_an_unreadable_sidecar_holds_the_row(self):
        world, views = scene()
        world.files[paths.norm(SIDE)] = PermissionError("denied")
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.held_by), (CHANGED, SIDE))

    def test_fewer_than_two_evidence_entries_raise_value_error(self):
        for n in (0, 1):
            with self.subTest(n=n):
                world, views = scene(1)
                with self.assertRaises(ValueError):
                    decide(views, world.evidence()[:n], coverage_complete=True, stat=world.stat)

    def test_a_destination_placeholder_gives_no_archive_copy_for_the_reject_and_for_a_sidecar(self):
        for target in (P, SIDE):
            with self.subTest(target=target):
                world, views = scene()
                world.archive(target, 2)
                dest = world.rows[1][paths.norm(target)][0].dest
                got = verdict_of(world, views, attrs_of=lambda path, st: PLACEHOLDER_ATTR if path == dest else 0)
                self.assertEqual((got.verdict, got.archive, got.held_by), (NOCOPY, [OK, NOCOPY], "" if target == P else SIDE))

    def test_the_destination_of_each_root_is_listed(self):
        world, views = scene()
        self.assertEqual(verdict_of(world, views).dests, ["d:/arch1/img_0001.arw", "d:/arch2/img_0001.arw"])


class KeepByRecordTest(unittest.TestCase):
    def test_views_that_cannot_keep_are_in_no_kept_by(self):
        cases = {
            "excluded": {"excluded": True},
            "old_state": {"role": "old_state"},
            "unreadable": {"status": "unreadable"},
        }
        for name, over in cases.items():
            with self.subTest(name):
                world, views = scene()
                views.append(view("C:/cats/b.lrcat", picks={P: [0]}, **over))
                got = verdict_of(world, views)
                self.assertEqual((got.verdict, got.kept_by), ("on_list", []))

    def test_a_partial_view_keeps(self):
        world, views = scene()
        views.append(view("C:/cats/b.lrcat", status="partial", picks={P: [0]}))
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, got.kept_by), ("kept", [("C:/cats/b.lrcat", "shoot")]))

    def test_the_live_view_keeping_wins_even_when_a_shoot_view_also_keeps(self):
        world, views = scene()
        views.append(view("C:/cats/b.lrcat", picks={P: [0]}))
        views.append(view("C:/cats/live.lrcat", "live", picks={P: [0]}))
        got = verdict_of(world, views)
        self.assertEqual((got.verdict, len(got.kept_by)), ("kept_in_live", 2))

    def test_the_live_view_not_recording_the_reject_does_not_keep_it(self):
        world, views = scene()
        views.append(view("C:/cats/live.lrcat", "live", picks={OTHER: [0]}))
        self.assertEqual(verdict_of(world, views).verdict, "on_list")

    def test_path_takes_the_orig_of_the_first_rejecting_view(self):
        world, _ = scene()
        first = view("C:/cats/a.lrcat", picks={P: [-1]}, orig={P: "D:/Lib/shoot-a/IMG_0001.ARW"})
        second = view("C:/cats/b.lrcat", picks={P: [-1]}, orig={P: "d:/lib/Shoot-A/Img_0001.arw"})
        for views, want in (([first, second], "D:/Lib/shoot-a/IMG_0001.ARW"),
                            ([second, first], "d:/lib/Shoot-A/Img_0001.arw")):
            with self.subTest(want=want):
                self.assertEqual(verdict_of(world, views).path, want)


OTHER = "d:/lib/shoot-a/img_0002.arw"


class WorkTest(unittest.TestCase):
    def check(self, views, verdict, developed_in, kept_by=()):
        world, rejecting = scene()
        got = verdict_of(world, rejecting[:1] + views)
        self.assertEqual((got.verdict, got.developed_in, got.kept_by), (verdict, developed_in, list(kept_by)))

    def test_work_that_keeps_for_work_names_the_catalog_role_and_kind(self):
        old = "C:/cats/old.lrcat"
        cases = {
            "develop work in a catalog that rejects the reject": (
                [view("C:/cats/b.lrcat", picks={P: [-1]}, developed={P: [1]})], [("C:/cats/b.lrcat", "shoot", "develop")]),
            "develop work only in an old_state catalog": (
                [view(old, "old_state", picks={P: [0]}, developed={P: [1]})], [(old, "old_state", "develop")]),
            "a pick of 1 only in another catalog": (
                [view(old, "old_state", picks={P: [1]})], [(old, "old_state", "pick")]),
            "an empty develop state": (
                [view(old, "old_state", picks={P: [0]}, developed={P: [None]})], [(old, "old_state", "develop")]),
        }
        for name, (views, developed_in) in cases.items():
            with self.subTest(name):
                self.check(views, "kept_for_work", developed_in)

    def test_no_work_when_developed_is_0_and_the_pick_is_0_or_minus_1(self):
        for pick in (0, -1):
            with self.subTest(pick=pick):
                self.check([view("C:/cats/old.lrcat", "old_state", picks={P: [pick]})], "on_list", [])

    def test_work_only_in_an_excluded_view_does_not_keep(self):
        self.check([view("C:/cats/b.lrcat", excluded=True, picks={P: [1]}, developed={P: [1]})], "on_list", [])

    def test_a_reject_kept_by_record_that_also_holds_work_gives_kept(self):
        b = "C:/cats/b.lrcat"
        self.check([view(b, picks={P: [1]}, developed={P: [1]})], "kept", [(b, "shoot", "develop+pick")], [(b, "shoot")])

    def test_images_count_the_absolute_rows_of_each_catalog_and_0_for_the_relative_form_only(self):
        world, views = scene()
        views.append(view("C:/cats/b.lrcat", picks={P: [-1, -1]}))
        c = view("C:/cats/c.lrcat", picks={P: [-1]})
        views.append(c._replace(absolute=frozenset(), absolute_images={}))
        self.assertEqual(verdict_of(world, views).images,
                         [("C:/cats/a.lrcat", 1), ("C:/cats/b.lrcat", 2), ("C:/cats/c.lrcat", 0)])


class CatalogDevelopTest(Tmp):
    """The develop rule is task D's: these cases run a real read through `catalogs` and `latest_views`."""

    ENTRY = {"base": "IMG_0001", "root_relative": None}
    PATH = "c:/shoot-a/img_0001.arw"

    def verdict(self, old_entry):
        shoot, old = self.path("shoot", "a.lrcat"), self.path("old", "b.lrcat")
        synth.make_catalog(shoot, [{**self.ENTRY, "pick": -1, "developed": 0}])
        synth.make_catalog(old, [{**self.ENTRY, **old_entry}])
        conn = store.connect(os.path.join(self.work, "work.db"))
        self.addCleanup(conn.close)
        self.scan(conn, [os.path.dirname(shoot), os.path.dirname(old)], old_states=[old])
        world = World()
        world.clean(self.PATH)
        views = latest_views(conn, excluded_catalogs=[])
        self.assertEqual([v.role for v in views], ["shoot", "old_state"])
        return world.decide(views)[self.PATH]

    def test_an_old_state_row_with_an_unusable_value_keeps_for_work_rc18(self):
        got = self.verdict({"pick": 0, "developed": "unknown", "developed_ex": 0})
        self.assertEqual((got.verdict, [w[2] for w in got.developed_in], got.kept_by), ("kept_for_work", ["develop"], []))

    def test_an_old_state_catalog_not_edited_with_minus_one_is_on_list_rc18(self):
        got = self.verdict({"pick": 0, "developed_ex": -1.0})
        self.assertEqual((got.verdict, got.developed_in), ("on_list", []))

    def test_an_old_state_catalog_with_develop_work_1_0_keeps_for_work(self):
        got = self.verdict({"pick": 0, "developed_ex": 1.0})
        self.assertEqual(got.verdict, "kept_for_work")


class FamilyHoldTest(unittest.TestCase):
    """Rule 29, RC-19: a reject that is the sidecar of a held reject takes its verdict."""

    def family(self, raw_ext="arw", jpg_ext="jpg", *, raw_bad=None, jpg_bad=None, work=False, jpg_there=True):
        raw, jpg = f"d:/lib/shoot-a/img_0001.{raw_ext}", f"d:/lib/shoot-a/img_0001.{jpg_ext}"
        world = World()
        for path, bad in ((raw, raw_bad), (jpg, jpg_bad)):
            world.file(path, attrs=PLACEHOLDER_ATTR if bad == "placeholder" else 0)
            for root in (1, 2):
                world.archive(path, root, bad if bad != "placeholder" else None)
        if not jpg_there:
            del world.files[jpg]
        live = view("C:/cats/live.lrcat", "live", picks={raw: [-1]}, sidecars=[jpg],
                    orig={raw: raw, jpg: jpg})
        shoot = view("C:/cats/a.lrcat", picks={jpg: [-1]}, orig={jpg: jpg})
        views = [live, shoot]
        if work:
            views.append(view("C:/cats/old.lrcat", "old_state", picks={raw: [0]}, developed={raw: [1]}))
        return raw, jpg, world.decide(views)

    def test_a_jpeg_that_is_the_sidecar_of_a_held_raw_takes_the_raw_verdict(self):
        cases = {"work in an old_state view": ({"work": True}, "kept_for_work"),
                 "no archive copy": ({"raw_bad": "nocopy"}, NOCOPY),
                 "placeholder": ({"raw_bad": "placeholder"}, "placeholder")}
        for name, (kw, verdict) in cases.items():
            with self.subTest(name):
                raw, jpg, got = self.family(**kw)
                self.assertEqual((got[raw].verdict, got[raw].held_by), (verdict, ""))
                self.assertEqual((got[jpg].verdict, got[jpg].held_by), (verdict, raw))

    def test_the_hold_keeps_the_jpegs_own_archive_and_work_facts(self):
        raw, jpg, got = self.family(raw_bad="nocopy")
        self.assertEqual((got[jpg].archive, got[jpg].developed_in, got[jpg].kept_by), ([OK, OK], [], []))

    def test_a_missing_jpeg_alone_is_ignored_as_a_sidecar_of_the_raw(self):
        raw, jpg, got = self.family(jpg_there=False)
        self.assertEqual((got[raw].verdict, got[raw].held_by, got[jpg].verdict), ("on_list", "", "missing"))

    def test_a_jpeg_failing_alone_holds_the_raw_too(self):
        for bad, verdict in (("nocopy", NOCOPY), ("outside", OUTSIDE), ("changed", CHANGED), ("placeholder", "placeholder")):
            with self.subTest(bad):
                raw, jpg, got = self.family(jpg_bad=bad)
                self.assertEqual((got[raw].verdict, got[raw].held_by), (verdict, jpg))
                self.assertEqual((got[jpg].verdict, got[jpg].held_by), (verdict, ""))

    def test_both_clean_are_both_on_list(self):
        raw, jpg, got = self.family()
        self.assertEqual((got[raw].verdict, got[jpg].verdict, got[jpg].held_by), ("on_list", "on_list", ""))

    def test_the_verdicts_do_not_depend_on_which_path_sorts_first(self):
        for raw_ext in ("arw", "raw"):
            with self.subTest(raw_ext=raw_ext):
                raw, jpg, got = self.family(raw_ext, raw_bad="nocopy")
                self.assertEqual((got[raw].verdict, got[jpg].verdict, got[jpg].held_by), (NOCOPY, NOCOPY, raw))

    def test_a_chain_of_two_holds_all_three_whatever_the_sort_order(self):
        for exts in (("arw", "jpg", "xmp"), ("xmp", "jpg", "arw"), ("jpg", "arw", "xmp")):
            with self.subTest(exts=exts):
                r0, r1, s = (f"d:/lib/shoot-a/img_0001.{e}" for e in exts)
                world = World()
                for path in (r0, r1, s):
                    world.clean(path)
                world.archive(r0, 2, "nocopy")
                views = [view("C:/cats/live.lrcat", "live", picks={r0: [-1]}, sidecars=[r1]),
                         view("C:/cats/a.lrcat", picks={r1: [-1]}, sidecars=[s]),
                         view("C:/cats/c.lrcat", picks={s: [-1]})]
                got = world.decide(views)
                self.assertEqual({got[r].verdict for r in (r0, r1, s)}, {NOCOPY})
                self.assertEqual((got[r0].held_by, got[r1].held_by, got[s].held_by), ("", r0, r1))

    def test_a_sidecar_with_two_held_owners_takes_the_first_in_path_order(self):
        first, second, side = (f"d:/lib/shoot-a/img_0001.{e}" for e in ("arw", "nef", "jpg"))
        for bad_first, bad_second, owner in (("nocopy", "outside", first), ("outside", "nocopy", first),
                                             (None, "nocopy", second)):
            with self.subTest(bad_first=bad_first, bad_second=bad_second):
                world = World()
                for path, bad in ((first, bad_first), (second, bad_second), (side, None)):
                    world.file(path)
                    for root in (1, 2):
                        world.archive(path, root, bad)
                views = [view("C:/cats/live.lrcat", "live", picks={first: [-1], second: [-1]}, sidecars=[side]),
                         view("C:/cats/a.lrcat", picks={side: [-1]})]
                got = world.decide(views)
                self.assertEqual(got[side].held_by, owner)
                self.assertEqual(got[side].verdict, got[owner].verdict)

    def test_a_chain_member_that_is_held_later_but_sorts_first_decides(self):
        low, mid, high = (f"d:/lib/shoot-a/img_0001.{e}" for e in ("aaa", "mmm", "zzz"))
        world = World()
        for path in (low, mid, high):
            world.clean(path)
        world.archive(high, 2, "nocopy")
        views = [view("C:/cats/live.lrcat", "live", picks={high: [-1]}, sidecars=[low, mid]),
                 view("C:/cats/a.lrcat", picks={low: [-1]}, sidecars=[mid]),
                 view("C:/cats/c.lrcat", picks={mid: [-1]})]
        got = world.decide(views)
        self.assertEqual((got[mid].verdict, got[mid].held_by), (NOCOPY, low))
        self.assertEqual((got[low].verdict, got[low].held_by), (NOCOPY, high))


class SidecarChainTest(unittest.TestCase):
    """RC-107: a reject that travels as another's sidecar brings its own sidecars along, and holds with them."""

    SIZES = {"arw": 10, "jpg": 4, "png": 2, "xmp": 1, "tif": 3}

    def path(self, ext):
        return f"d:/lib/shoot-a/img_0001.{ext}"

    def scene(self, links, bad=None, mixed=False):
        """`links` = [(view name, rejected exts, listed sidecar exts)]; every file is clean but `bad` = {ext: kind}.

        `mixed`: the catalog spells every path in mixed case (`orig`), as Lightroom does (L15)."""
        world = World()
        for ext, size in self.SIZES.items():
            world.clean(self.path(ext), size)
        for ext, kind in (bad or {}).items():
            world.archive(self.path(ext), 2, kind, size=self.SIZES[ext])
        views = [view(f"C:/cats/{name}.lrcat", "live" if name == "live" else "shoot",
                      picks={self.path(e): [-1] for e in rejected}, sidecars=[self.path(e) for e in listed],
                      orig={self.path(e): f"D:/Lib/Shoot-A/IMG_0001.{e.upper()}" for e in self.SIZES} if mixed else None)
                 for name, rejected, listed in links]
        return world, views

    def write(self, got):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "out")
        write_outputs(out, list(got.values()), roots=2, summary=SummaryInput("t", [], [], []))
        with open(os.path.join(out, "reject-removal-list.csv"), encoding="utf-8-sig", newline="") as handle:
            removal = list(csv.reader(handle))[1:]
        with open(os.path.join(out, "rejects-summary.md"), encoding="utf-8") as handle:
            return removal, handle.read()

    def test_a_sidecar_reject_brings_its_own_sidecars_into_the_row_and_the_bytes(self):
        cases = {"raw, its jpeg, the jpeg's xmp": ([("live", ["arw"], ["jpg"]), ("a", ["jpg"], ["xmp"])],
                                                    ["jpg", "xmp"], 5),
                 "a chain of three rejects": ([("live", ["arw"], ["jpg"]), ("a", ["jpg"], ["png"]),
                                               ("b", ["png"], ["xmp"])], ["jpg", "png", "xmp"], 7),
                 "an xmp listed by both the raw and the jpeg, once": ([("live", ["arw"], ["jpg", "xmp"]),
                                                                       ("a", ["jpg"], ["xmp"])], ["jpg", "xmp"], 5)}
        for name, (links, exts, side_bytes) in cases.items():
            with self.subTest(name):
                world, views = self.scene(links)
                got = world.decide(views)
                raw = got[self.path("arw")]
                self.assertEqual(([paths.norm(s) for s in raw.sidecars], raw.sidecar_bytes),
                                 ([self.path(e) for e in exts], side_bytes))
                self.assertEqual(raw.verdict, "on_list")
                removal, summary = self.write(got)
                self.assertEqual([(row[0], row[3].lower(), row[4]) for row in removal],
                                 [(self.path("arw"), ";".join(self.path(e) for e in exts), str(side_bytes))])
                self.assertIn(f"| on_list | {len(got)} | {10 + side_bytes} |", summary)

    def test_two_rejects_listing_each_other_end_and_neither_leaves_without_the_other(self):
        world, views = self.scene([("live", ["arw"], ["jpg"]), ("a", ["jpg"], ["arw"])])
        got = world.decide(views)
        a, j = got[self.path("arw")], got[self.path("jpg")]
        self.assertEqual(([paths.norm(s) for s in a.sidecars], [paths.norm(s) for s in j.sidecars]),
                         ([self.path("jpg")], [self.path("arw")]))
        removal, _ = self.write(got)
        self.assertEqual(removal, [])

    def test_a_sidecar_reject_held_through_its_own_sidecar_holds_the_raw_too(self):
        world, views = self.scene([("live", ["arw"], ["jpg"]), ("a", ["jpg"], ["xmp"])], bad={"xmp": "nocopy"})
        got = world.decide(views)
        xmp = self.path("xmp")
        self.assertEqual([(got[self.path(e)].verdict, paths.norm(got[self.path(e)].held_by)) for e in ("arw", "jpg")],
                         [(NOCOPY, xmp)] * 2)
        self.assertEqual(self.write(got)[0], [])

    def test_a_sidecar_reject_held_by_the_shared_sidecar_hold_holds_the_raw_too_rc113(self):
        chain = [("live", ["arw"], ["jpg"]), ("a", ["jpg"], ["xmp"]), ("b", ["png"], ["xmp"])]

        def siblings(holds):
            """The raw lists the jpg and the png; only `holds` shares its xmp with the held tif."""
            return [("live", ["arw"], ["jpg", "png"]), ("a", ["jpg"], ["xmp"] * (holds == "jpg")),
                    ("b", ["png"], ["xmp"] * (holds == "png")), ("c", ["tif"], ["xmp"])]

        cases = {"a chain, the hold shared through the xmp": (chain, {"png": "nocopy"}, False, "jpg"),
                 "the same chain spelled in mixed case": (chain, {"png": "nocopy"}, True, "jpg"),
                 "siblings, the second one held": (siblings("png"), {"tif": "nocopy"}, False, "png"),
                 "siblings, the first one held": (siblings("jpg"), {"tif": "nocopy"}, False, "jpg")}
        for name, (links, bad, mixed, holder) in cases.items():
            with self.subTest(name):
                world, views = self.scene(links, bad, mixed)
                got = world.decide(views)
                rejects = {e for _, rejected, _ in links for e in rejected}
                self.assertEqual({e: got[self.path(e)].verdict for e in rejects}, {e: NOCOPY for e in rejects})
                self.assertEqual(paths.norm(got[self.path("arw")].held_by), self.path(holder))
                self.assertEqual(self.write(got)[0], [])

    def test_the_travelling_rejects_own_row_stays_in_rejects_csv_rc23(self):
        world, views = self.scene([("live", ["arw"], ["jpg"]), ("a", ["jpg"], ["xmp"])])
        got = world.decide(views)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        write_outputs(tmp.name, list(got.values()), roots=2, summary=SummaryInput("t", [], [], []))
        with open(os.path.join(tmp.name, "rejects.csv"), encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        listed = {row[header.index("path")]: row[header.index("verdict")] for row in rows[1:]}
        self.assertEqual(listed, {self.path("arw"): "on_list", self.path("jpg"): "on_list"})


class SharedSidecarHoldTest(unittest.TestCase):
    """Rule 29, RC-68 A5, RC-73: rejects that share a sidecar path with a held reject are held with it."""

    VERDICT = {"nocopy": NOCOPY, "outside": OUTSIDE}
    ORDERS = (("arw", "nef", "raw"), ("raw", "nef", "arw"), ("nef", "raw", "arw"))

    def path(self, ext):
        return f"d:/lib/shoot-a/img_0001.{ext}"

    def world(self, bad, *exts):
        world = World()
        for ext in exts:
            world.clean(self.path(ext))
        for ext, kind in bad.items():
            world.archive(self.path(ext), 2, kind)
        return world

    def pair(self, bad, first="arw", second="nef", reject_side=False):
        a, b, side = self.path(first), self.path(second), self.path("jpg")
        world = self.world(bad, first, second, "jpg")
        views = [view("C:/cats/live.lrcat", "live", picks={a: [-1], b: [-1]}, sidecars=[side])]
        if reject_side:
            views.append(view("C:/cats/a.lrcat", picks={side: [-1]}))
        return a, b, side, world.decide(views)

    def chain(self, roles, extra=()):
        """`roles` = (held, shares-with-held, shares-with-that): the first shares S1 with the second, which shares S2 with the third."""
        a, b, c = (self.path(e) for e in roles)
        s1, s2 = self.path("jpg"), self.path("xmp")
        world = self.world({roles[0]: "nocopy"}, *roles, "jpg", "xmp", *extra)
        views = [view("C:/cats/live.lrcat", "live", picks={a: [-1], b: [-1]}, sidecars=[s1]),
                 view("C:/cats/a.lrcat", picks={b: [-1], c: [-1]}, sidecars=[s2])]
        return a, b, c, world, views

    def test_two_rejects_sharing_a_sidecar_that_is_not_a_reject_are_held_together(self):
        for first, second in (("arw", "nef"), ("nef", "arw")):
            with self.subTest(held=first):
                a, b, side, got = self.pair({first: "nocopy"}, "arw", "nef")
                held, other = (a, b) if first == "arw" else (b, a)
                self.assertEqual((got[held].verdict, got[held].held_by), (NOCOPY, ""))
                self.assertEqual((got[other].verdict, got[other].held_by), (NOCOPY, held))
                self.assertNotIn("on_list", {r.verdict for r in got.values()})

    def test_three_rejects_sharing_a_sidecar_path_are_all_held_by_the_first_in_path_order(self):
        a, b, c, side = (self.path(e) for e in ("arw", "nef", "raw", "jpg"))
        world = self.world({"arw": "nocopy"}, "arw", "nef", "raw", "jpg")
        got = world.decide([view("C:/cats/live.lrcat", "live", picks={a: [-1], b: [-1], c: [-1]}, sidecars=[side])])
        self.assertEqual({got[r].verdict for r in (a, b, c)}, {NOCOPY})
        self.assertEqual((got[a].held_by, got[b].held_by, got[c].held_by), ("", a, a))

    def test_a_hold_cycle_through_a_chain_of_four_pulled_in_rejects_ends(self):
        """root-a, a-b and b-c share a sidecar path; c lists d, which lists b (its grandparent d <- c <- b), and d sorts first."""
        root, a, b, c, d = (self.path(e) for e in ("zzz", "bbb", "ccc", "ddd", "aaa"))
        s1, s2, s3 = (self.path(e) for e in ("jpg", "xmp", "png"))
        world = self.world({"zzz": "nocopy"}, "zzz", "bbb", "ccc", "ddd", "aaa", "jpg", "xmp", "png")
        views = [view("C:/cats/live.lrcat", "live", picks={root: [-1]}, sidecars=[s1]),
                 view("C:/cats/a.lrcat", picks={a: [-1]}, sidecars=[s1, s2]),
                 view("C:/cats/b.lrcat", picks={b: [-1]}, sidecars=[s2, s3]),
                 view("C:/cats/c.lrcat", picks={c: [-1]}, sidecars=[s3, d]),
                 view("C:/cats/d.lrcat", picks={d: [-1]}, sidecars=[b])]
        out = []
        worker = threading.Thread(target=lambda: out.append(world.decide(views)), daemon=True)
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "the hold did not end: a held_by cycle")
        got = out[0]
        self.assertEqual({got[r].verdict for r in (root, a, b, c, d)}, {NOCOPY})
        # RC-113: d lists b, so a hold on b reaches d too; b sorts before c, so b is d's holder.
        self.assertEqual([got[r].held_by for r in (root, a, b, c, d)], ["", root, a, b, b])

    def test_a_shared_sidecar_that_is_a_reject_is_held_and_listed_by_neither(self):
        a, b, side, got = self.pair({"arw": "nocopy"}, reject_side=True)
        self.assertEqual((got[b].verdict, got[b].held_by), (NOCOPY, a))
        self.assertEqual(got[side].verdict, NOCOPY)
        self.assertNotIn("on_list", {r.verdict for r in got.values()})

    def test_a_hold_passes_down_a_chain_with_the_direct_holder_as_held_by(self):
        for roles in self.ORDERS:
            with self.subTest(roles=roles):
                a, b, c, world, views = self.chain(roles)
                got = world.decide(views)
                self.assertEqual({got[r].verdict for r in (a, b, c)}, {NOCOPY})
                self.assertEqual((got[a].held_by, got[b].held_by, got[c].held_by), ("", a, b))

    def test_a_reject_between_that_shares_nothing_is_not_held_through_the_chain(self):
        for between in ("nef", "pef"):
            with self.subTest(between=between):
                a, b, c, world, views = self.chain(("arw", "mrw", "raw"), extra=(between,))
                d, s3 = self.path(between), self.path("png")
                world.clean(s3)
                views.append(view("C:/cats/c.lrcat", picks={d: [-1]}, sidecars=[s3]))
                got = world.decide(views)
                self.assertEqual((got[c].verdict, got[c].held_by), (NOCOPY, b))
                self.assertEqual((got[d].verdict, got[d].held_by), ("on_list", ""))

    def test_a_family_that_shares_nothing_with_the_held_one_stays_on_the_list(self):
        a, b, c, world, views = self.chain(("arw", "nef", "raw"))
        d, e, s3 = self.path("srw"), self.path("tif"), self.path("png")
        for p in (d, e, s3):
            world.clean(p)
        views.append(view("C:/cats/c.lrcat", picks={d: [-1], e: [-1]}, sidecars=[s3]))
        got = world.decide(views)
        self.assertEqual({got[d].verdict, got[e].verdict}, {"on_list"})

    def test_of_two_held_rejects_the_first_in_path_order_holds_a_third_sharing_with_both(self):
        a, b, c = (self.path(e) for e in ("arw", "nef", "raw"))
        s1, s2 = self.path("jpg"), self.path("xmp")
        for bad_a, bad_b in (("nocopy", "outside"), ("outside", "nocopy")):
            with self.subTest(bad_a=bad_a, bad_b=bad_b):
                world = self.world({"arw": bad_a, "nef": bad_b}, "arw", "nef", "raw", "jpg", "xmp")
                views = [view("C:/cats/live.lrcat", "live", picks={a: [-1], c: [-1]}, sidecars=[s1]),
                         view("C:/cats/a.lrcat", picks={b: [-1], c: [-1]}, sidecars=[s2])]
                got = world.decide(views)
                self.assertEqual((got[c].verdict, got[c].held_by), (got[a].verdict, a))

    def test_a_reject_held_for_its_own_reason_keeps_it_when_a_sharer_is_held(self):
        for bad_a, bad_b in (("nocopy", "outside"), ("outside", "nocopy")):
            with self.subTest(bad_a=bad_a, bad_b=bad_b):
                a, b, side, got = self.pair({"arw": bad_a, "nef": bad_b})
                self.assertEqual((got[a].verdict, got[a].held_by), (self.VERDICT[bad_a], ""))
                self.assertEqual((got[b].verdict, got[b].held_by), (self.VERDICT[bad_b], ""))


if __name__ == "__main__":
    unittest.main()
