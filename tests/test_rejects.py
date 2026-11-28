import os
import tempfile
import unittest

from lrclean import store
from lrclean.rejects import (
    CatalogView,
    StaleFlags,
    base_index,
    check_fresh,
    is_kept,
    is_rejected,
    latest_views,
    partly_rejected,
    relative_only_flags,
    reject_set,
    sidecars_of,
    work_of,
)

P = "d:/lib/shoot-a/img_0001.arw"
SIDE = "d:/lib/shoot-a/img_0001.xmp"
OTHER = "d:/lib/shoot-a/img_0002.arw"
LIVE = "C:\\cats\\live.lrcat"


def view(source="C:/cats/a.lrcat", role="shoot", status="ok", excluded=False, picks=None,
         developed=None, photos=(), sidecars=(), orig=None, absolute=None):
    picks = picks or {}
    developed = developed if developed is not None else {k: [0] * len(v) for k, v in picks.items()}
    absolute = set(picks if absolute is None else absolute)
    return CatalogView(source, role, status, excluded, picks, developed,
                       set(photos) | set(picks), set(sidecars), orig or {},
                       frozenset(absolute), {k: len(v) for k, v in picks.items() if k in absolute})


class RejectedTest(unittest.TestCase):
    def test_every_pick_must_be_minus_one(self):
        cases = {
            "master and copy": ([-1, -1], True),
            "only the master": ([-1, 0], False),
            "only a virtual copy": ([0, -1], False),
            "a None beside -1": ([-1, None], False),
            "a lone -1": ([-1], True),
            "a lone 1": ([1], False),
        }
        for name, (picks, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(is_rejected(view(picks={P: picks}), P), want)

    def test_no_image_row_is_not_a_reject(self):
        self.assertFalse(is_rejected(view(photos=[P]), P))

    def test_only_a_readable_live_or_shoot_view_rejects(self):
        cases = {"partial": {"status": "partial"}, "unreadable": {"status": "unreadable"},
                 "excluded": {"excluded": True}, "old_state": {"role": "old_state"}}
        for name, kw in cases.items():
            with self.subTest(name):
                self.assertFalse(is_rejected(view(picks={P: [-1]}, **kw), P))
        self.assertTrue(is_rejected(view(role="live", picks={P: [-1]}), P))

    def test_only_the_absolute_form_of_a_record_rejects(self):
        cases = {"no absolute row at all": (set(), False),
                 "relative form only, another path absolute": ({OTHER}, False),
                 "absolute form too": ({P, OTHER}, True)}
        for name, (absolute, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(is_rejected(view(picks={P: [-1]}, absolute=absolute), P), want)

    def test_each_path_norm_is_judged_on_its_own_picks(self):
        v = view(picks={P: [-1], "d:/lib/shoot-a/img_0001.arw ": [0]})
        self.assertTrue(is_rejected(v, P))
        self.assertFalse(is_rejected(v, "d:/lib/shoot-a/img_0001.arw "))


class KeptTest(unittest.TestCase):
    def test_recorded_photo_not_rejected_keeps(self):
        self.assertTrue(is_kept(view(picks={P: [0]}), P))

    def test_photo_record_without_image_row_keeps(self):
        self.assertTrue(is_kept(view(photos=[P]), P))

    def test_rejected_photo_does_not_keep_itself(self):
        self.assertFalse(is_kept(view(picks={P: [-1]}), P))

    def test_relative_form_only_photo_keeps_itself_and_its_sidecar(self):
        v = view(picks={P: [-1]}, sidecars=[SIDE], absolute={OTHER})
        self.assertTrue(is_kept(v, P))
        self.assertTrue(is_kept(v, SIDE))

    def test_sidecar_keeps_only_for_a_photo_not_rejected(self):
        self.assertFalse(is_kept(view(picks={P: [-1]}, sidecars=[SIDE]), SIDE))
        self.assertTrue(is_kept(view(picks={P: [0]}, sidecars=[SIDE]), SIDE))

    def test_sidecar_named_after_the_whole_photo_name_keeps_only_for_a_photo_not_rejected(self):
        double = P + ".xmp"
        self.assertNotEqual(double, SIDE)
        self.assertFalse(is_kept(view(picks={P: [-1]}, sidecars=[double]), double))
        self.assertTrue(is_kept(view(picks={P: [-1], "d:/lib/shoot-a/img_0001.jpg": [0]}, sidecars=[double]), double))

    def test_sidecar_named_after_the_whole_photo_name_is_kept_by_either_photo_it_names(self):
        double, longer = P + ".xmp", P + ".arw"
        self.assertNotEqual(longer, P)
        for kept, rejected in ((longer, P), (P, longer)):
            with self.subTest(kept=kept):
                self.assertTrue(is_kept(view(picks={kept: [0], rejected: [-1]}, sidecars=[double]), double))

    def test_sidecar_of_another_base_or_folder_does_not_keep(self):
        for name, photo in (("base", OTHER), ("folder", "d:/lib/shoot-b/img_0001.arw")):
            with self.subTest(name):
                self.assertFalse(is_kept(view(picks={photo: [0]}, sidecars=[SIDE]), SIDE))

    def test_sidecar_lookup_uses_the_index_given(self):
        v = view(picks={P: [0]}, sidecars=[SIDE])
        self.assertFalse(is_kept(v, SIDE, base_index([])))
        self.assertTrue(is_kept(v, SIDE, base_index(v.photos)))

    def test_unrecorded_path_keeps_nothing(self):
        for name, photo in (("another file", OTHER), ("same base, other extension", "d:/lib/shoot-a/img_0001.jpg")):
            with self.subTest(name):
                self.assertFalse(is_kept(view(picks={photo: [0]}), P))

    def test_only_readable_live_or_shoot_views_keep(self):
        cases = {"excluded": ({"excluded": True}, False), "old_state": ({"role": "old_state"}, False),
                 "unreadable": ({"status": "unreadable"}, False), "partial": ({"status": "partial"}, True),
                 "live": ({"role": "live"}, True)}
        for name, (kw, want) in cases.items():
            with self.subTest(name):
                self.assertEqual(is_kept(view(picks={P: [0]}, **kw), P), want)

    def test_keeper_position_does_not_matter(self):
        rejecting = lambda n: view(source=f"C:/cats/{n}.lrcat", picks={P: [-1]})
        keeping = view(source="C:/cats/keep.lrcat", picks={P: [0]})
        for name, views in {"first": [keeping, rejecting("x"), rejecting("y")],
                            "middle": [rejecting("x"), keeping, rejecting("y")],
                            "last": [rejecting("x"), rejecting("y"), keeping]}.items():
            with self.subTest(name):
                self.assertEqual([v.source for v in views if is_kept(v, P)], [keeping.source])
                self.assertEqual(len(reject_set(views)[P]), 2)

    def test_reject_set_lists_rejecting_views_in_view_order(self):
        a, b = view(source="C:/cats/a.lrcat", picks={P: [-1]}), view(source="C:/cats/b.lrcat", picks={P: [-1]})
        self.assertEqual(reject_set([a, view(picks={P: [0]}), b])[P], [a, b])
        self.assertNotIn(OTHER, reject_set([a]))


class WorkTest(unittest.TestCase):
    def test_label_by_develop_state_and_pick(self):
        cases = [("developed 1", [1], [0], "develop"), ("developed None keeps", [None], [0], "develop"),
                 ("developed 0 alone", [0], [0], ""), ("pick 1", [0], [1], "pick"),
                 ("pick 0", [0], [0], ""), ("pick -1", [0], [-1], ""), ("pick None", [0], [None], ""),
                 ("both", [1], [1], "develop+pick"), ("one of two images", [0, 1], [0, 0], "develop")]
        for name, dev, picks, want in cases:
            with self.subTest(name):
                v = view(picks={P: picks if len(picks) == len(dev) else picks * len(dev)},
                         developed={P: dev})
                self.assertEqual(work_of(v, P), want)

    def test_work_counts_through_the_relative_form(self):
        for name, dev, picks, want in (("develop", [1], [0], "develop"), ("pick", [0], [1], "pick")):
            with self.subTest(name):
                self.assertEqual(work_of(view(picks={P: picks}, developed={P: dev}, absolute={OTHER}), P), want)

    def test_work_counts_in_every_readable_view(self):
        cases = {"rejecting": {"picks": {P: [-1, 1]}}, "old_state": {"role": "old_state"},
                 "partial": {"status": "partial"}}
        for name, kw in cases.items():
            with self.subTest(name):
                kw.setdefault("picks", {P: [1]})
                self.assertEqual(work_of(view(**kw), P), "pick")

    def test_excluded_unreadable_and_no_image_row_hold_none(self):
        for name, v in {"excluded": view(picks={P: [1]}, excluded=True),
                        "unreadable": view(picks={P: [1]}, status="unreadable"),
                        "no image row": view(photos=[P])}.items():
            with self.subTest(name):
                self.assertEqual(work_of(v, P), "")

    def test_work_position_does_not_matter(self):
        work, none = view(picks={P: [1]}), view(picks={P: [0]})
        for name, views in {"first": [work, none, none], "middle": [none, work, none],
                            "last": [none, none, work]}.items():
            with self.subTest(name):
                self.assertEqual([work_of(v, P) for v in views].count("pick"), 1)

    def test_is_kept_ignores_work(self):
        self.assertFalse(is_kept(view(picks={P: [-1]}, developed={P: [1]}), P))


class SidecarsTest(unittest.TestCase):
    def rejecting(self, n, sidecars, orig=None, picks=None):
        return view(source=f"C:/cats/{n}.lrcat", picks=picks or {P: [-1]}, sidecars=sidecars, orig=orig)

    def test_sidecar_recorded_by_which_rejecting_view(self):
        a, b, c = (self.rejecting("a", []), self.rejecting("b", []), self.rejecting("c", []))
        withside = lambda v: v._replace(sidecars={SIDE}, orig={SIDE: "D:/Lib/shoot-a/IMG_0001.xmp"})
        cases = {"second of two": [a, withside(b)], "first of two": [withside(a), b],
                 "middle of three": [a, withside(b), c], "last of three": [a, b, withside(c)]}
        for name, views in cases.items():
            with self.subTest(name):
                self.assertEqual(sidecars_of(views, P), {SIDE: "D:/Lib/shoot-a/IMG_0001.xmp"})

    def test_first_view_spelling_wins(self):
        a = self.rejecting("a", [SIDE], {SIDE: "D:/one/IMG_0001.xmp"})
        b = self.rejecting("b", [SIDE], {SIDE: "D:/two/IMG_0001.xmp"})
        self.assertEqual(sidecars_of([a, b], P), {SIDE: "D:/one/IMG_0001.xmp"})

    def test_sidecar_of_a_view_that_does_not_reject_is_not_returned(self):
        keeper = view(picks={P: [0]}, sidecars=[SIDE])
        self.assertEqual(sidecars_of([keeper], P), {})

    def test_other_base_other_folder_and_the_path_itself_are_not_returned(self):
        v = self.rejecting("a", ["d:/lib/shoot-a/img_0002.xmp", "d:/lib/shoot-b/img_0001.xmp", P])
        self.assertEqual(sidecars_of([v], P), {})

    def test_sidecar_named_after_the_whole_photo_name_is_returned_beside_the_base_name_one(self):
        jpg = "d:/lib/shoot-a/img_0001.jpg"
        cases = {"raw .xmp only": (P, [P + ".xmp"]), "raw .xmp beside the base name one": (P, [P + ".xmp", SIDE]),
                 "raw .acr": (P, [P + ".acr"]), "jpeg .xmp": (jpg, [jpg + ".xmp"])}
        for name, (photo, sides) in cases.items():
            with self.subTest(name):
                self.assertNotIn(photo, sides)
                v = self.rejecting("a", sides, picks={photo: [-1]})
                self.assertEqual(sidecars_of([v], photo), {s: s for s in sides})

    def test_dotted_photo_name_is_not_the_sidecar_owner_of_its_first_part(self):
        dotted, short = "d:/lib/shoot-a/shoot.2019.arw", "d:/lib/shoot-a/shoot.arw"
        cases = {"dotted photo, first-part sidecar": (dotted, "d:/lib/shoot-a/shoot.xmp", {}),
                 "first-part photo, dotted sidecar": (short, "d:/lib/shoot-a/shoot.2019.xmp", {}),
                 "dotted photo, its own sidecar": (dotted, "d:/lib/shoot-a/shoot.2019.xmp",
                                                   {"d:/lib/shoot-a/shoot.2019.xmp": "d:/lib/shoot-a/shoot.2019.xmp"}),
                 "photo-named jpeg is no sidecar of the raw": (P, P + ".jpg", {}),
                 "photo named with a photo extension, its base-name sidecar": (P + ".arw", P + ".xmp", {P + ".xmp": P + ".xmp"}),
                 "photo, the sidecar named after its whole name": (P, P + ".xmp", {P + ".xmp": P + ".xmp"})}
        for name, (photo, side, want) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(photo, side)
                v = self.rejecting("a", [side], picks={photo: [-1]})
                self.assertEqual(sidecars_of([v], photo), want)

    def test_files_scan_adds_only_the_whole_name_sidecars_with_their_spelling(self):
        xmp, acr = "D:/Lib/shoot-a/IMG_0001.ARW.xmp", "D:/Lib/shoot-a/img_0001.arw.ACR"
        self.assertNotEqual(xmp.lower()[-3:], acr.lower()[-3:])
        files = {P + ".xmp": xmp, SIDE: "D:/Lib/shoot-a/IMG_0001.xmp"}
        self.assertEqual(sidecars_of([self.rejecting("a", [])], P, files_sidecars=files), {P + ".xmp": xmp})
        both = {P + ".xmp": xmp, P + ".acr": acr}
        self.assertEqual(sidecars_of([self.rejecting("a", [])], P, files_sidecars=both), both)

    def test_files_scan_sidecar_also_recorded_is_one_entry_and_needs_a_rejecting_view(self):
        double = P + ".xmp"
        files = {double: "D:/Lib/shoot-a/IMG_0001.ARW.xmp"}
        recorded = self.rejecting("a", [double], {double: "D:/Cat/IMG_0001.ARW.xmp"})
        self.assertEqual(sidecars_of([recorded], P, files_sidecars=files), {double: "D:/Cat/IMG_0001.ARW.xmp"})
        self.assertEqual(sidecars_of([view(picks={P: [0]})], P, files_sidecars=files), {})

    def test_prebuilt_indexes_are_used(self):
        v = self.rejecting("a", [SIDE])
        self.assertEqual(sidecars_of([v], P, {v.source: base_index([])}), {})
        self.assertEqual(sidecars_of([v], P, {v.source: base_index([SIDE])}), {SIDE: SIDE})


class PartlyRejectedTest(unittest.TestCase):
    def test_minus_one_not_every_image_is_listed_sorted(self):
        v = view(source="C:/cats/b.lrcat", picks={P: [-1, 0], OTHER: [-1]},
                 orig={P: "D:/Lib/IMG_0001.ARW"})
        w = view(source="C:/cats/a.lrcat", picks={P: [None, -1]})
        self.assertEqual(partly_rejected([v, w]),
                         [("C:/cats/a.lrcat", P), ("C:/cats/b.lrcat", "D:/Lib/IMG_0001.ARW")])

    def test_old_state_view_is_not_listed(self):
        self.assertEqual(partly_rejected([view(role="old_state", picks={P: [-1, 0]})]), [])

    def test_a_flag_through_the_relative_form_only_is_not_listed_but_counted(self):
        v = view(source="C:/cats/b.lrcat", picks={P: [-1, 0], OTHER: [-1, 0]}, absolute={OTHER})
        self.assertEqual(partly_rejected([v]), [("C:/cats/b.lrcat", OTHER)])
        self.assertEqual(relative_only_flags([v]), [("C:/cats/b.lrcat", 1)])

    def test_relative_only_flags_skip_views_without_such_a_path_and_old_states_and_sort(self):
        views = [view(source="C:/cats/b.lrcat", picks={P: [-1], OTHER: [-1, 0]}, absolute=set()),
                 view(source="C:/cats/a.lrcat", picks={P: [-1]}, absolute=set()),
                 view(source="C:/cats/c.lrcat", picks={P: [-1, 0]}),
                 view(source="C:/cats/d.lrcat", picks={P: [0]}, absolute=set()),
                 view(source="C:/cats/e.lrcat", role="old_state", picks={P: [-1]}, absolute=set())]
        self.assertEqual(relative_only_flags(views), [("C:/cats/a.lrcat", 1), ("C:/cats/b.lrcat", 2)])


class FreshTest(unittest.TestCase):
    def setUp(self):
        self.live = view(source="C:\\cats\\Live.lrcat", role="live", picks={P: [-1, None]})
        self.shoot = view(source="C:/cats/a.lrcat", picks={P: [None]})
        self.roles = {"c:/cats/live.lrcat": "live", "c:/cats/a.lrcat": "shoot"}
        self.kw = {"live_catalog": "c:/cats/LIVE.lrcat", "stale": [], "expected_roles": self.roles}

    def check(self, views=None, **over):
        check_fresh(views or [self.live, self.shoot], **{**self.kw, **over})

    def test_fresh_set_passes_whatever_the_spelling_of_the_live_path(self):
        self.check()
        self.check(live_catalog="C:\\cats\\live.lrcat")

    def test_each_condition_refuses(self):
        live = self.live
        cases = {
            "no live_catalog": {"live_catalog": None},
            "live view absent": {"views": [self.shoot]},
            "live view source absent": {"views": [live._replace(source="C:/cats/other.lrcat"), self.shoot]},
            "partial": {"views": [live._replace(status="partial"), self.shoot]},
            "unreadable": {"views": [live._replace(status="unreadable"), self.shoot]},
            "stale": {"stale": ["C:/cats/LIVE.lrcat"]},
            "no image row": {"views": [live._replace(picks={}, developed={}), self.shoot]},
            "all picks None": {"views": [live._replace(picks={P: [None, None]}), self.shoot]},
            "shoot role differs": {"expected_roles": {**self.roles, "c:/cats/a.lrcat": "old_state"}},
            "old_state role differs": {"views": [live, self.shoot._replace(role="old_state")]},
            "live role differs": {"expected_roles": {**self.roles, "c:/cats/live.lrcat": "shoot"}},
            "unknown view": {"views": [live, self.shoot, view(source="C:/cats/new.lrcat")]},
        }
        for name, over in cases.items():
            with self.subTest(name), self.assertRaisesRegex(StaleFlags, "^run catalogs again: "):
                self.check(**over)


class LatestViewsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = store.connect(os.path.join(self.tmp.name, "work", "lrclean.sqlite"))
        self.addCleanup(self.conn.close)

    def scan(self, root="C:/cats"):
        return store.insert_row(self.conn, "scan", root=root, kind="catalogs", started="t",
                                finished="t", status="ok")

    def cat(self, scan_id, source, role="shoot", status="ok"):
        return store.insert_row(self.conn, "catalog", scan_id=scan_id, source_path=source,
                                role=role, status=status)

    def test_reads_only_the_latest_scan_of_a_root(self):
        self.cat(self.scan(), "C:/cats/old.lrcat")
        self.cat(self.scan(), "C:/cats/new.lrcat")
        self.assertEqual([v.source for v in latest_views(self.conn, excluded_catalogs=[])],
                         ["C:/cats/new.lrcat"])

    def test_sets_role_status_excluded_and_the_record_and_image_sets(self):
        cid = self.cat(self.scan(), "C:/cats/a.lrcat", role="live", status="partial")
        self.cat(self.scan("C:/more"), "C:/more/b.lrcat")
        rec = lambda path, orig, side: store.insert_row(
            self.conn, "record", catalog_id=cid, path_norm=path, path_orig=orig, via="x",
            ext="", capture_time="", is_sidecar=side)
        rec(P, "D:/Lib/shoot-a/IMG_0001.ARW", 0)
        rec(SIDE, "D:/Lib/shoot-a/IMG_0001.xmp", 1)
        third = "c:/third"
        for path, via, pick, dev in ((P, "absolute", -1, 0), (P, "absolute", 1, 1), (P, "relative", 0, None),
                                     (OTHER, "relative", None, 1), (third, "absolute", 0, None)):
            store.insert_row(self.conn, "image", catalog_id=cid, path_norm=path, via=via,
                             pick=pick, developed=dev)
        a, b = latest_views(self.conn, excluded_catalogs=["c:\\more\\B.lrcat"])
        self.assertEqual((a.source, a.role, a.status, a.excluded), ("C:/cats/a.lrcat", "live", "partial", False))
        self.assertTrue(b.excluded)
        self.assertEqual(a.photos, {P, OTHER, third})
        self.assertEqual(a.sidecars, {SIDE})
        self.assertEqual(a.absolute, {P, third})
        self.assertEqual(dict(a.absolute_images), {P: 2, third: 1})
        self.assertEqual(dict(b.absolute_images), {})
        self.assertEqual((a.picks, a.developed), ({P: [-1, 1, 0], OTHER: [None], third: [0]},
                                            {P: [0, 1, None], OTHER: [1], third: [None]}))
        self.assertEqual((a.orig[P], a.orig[SIDE], a.orig[OTHER]),
                         ("D:/Lib/shoot-a/IMG_0001.ARW", "D:/Lib/shoot-a/IMG_0001.xmp", OTHER))

    def test_works_without_the_file_and_hash_tables(self):
        self.cat(self.scan(), "C:/cats/a.lrcat")
        self.conn.execute('DROP TABLE "file"')
        self.conn.execute('DROP TABLE "hash"')
        self.assertEqual(len(latest_views(self.conn, excluded_catalogs=[])), 1)


if __name__ == "__main__":
    unittest.main()
