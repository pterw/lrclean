"""Rule 26a in `rejects.decide`: the develop hold of a reject is lifted by a twin that stays.

Each case is one scene (`Scene`): the reject `P` is rejected by the live catalog with develop work
(settings `S`); its twin `T` is recorded by a shoot catalog and has a live image with develop work `S`;
the two share one counted hash. A case changes one fact and names the verdict that follows.
"""

import os
import tempfile
import unittest

from lrclean import paths, store
from lrclean.reject_twin import EMPTY, Hashed, HashIndex
from lrclean.rejects import latest_views
from test_rejects_verdicts import (CHANGED, MTIME, NOCOPY, OUTSIDE, P, PLACEHOLDER_ATTR, SIDE, SIZE,
                                   World, view)

S, U = "digest-s", "digest-u"
LIVE, SHOOT, OLD = "C:/cats/live.lrcat", "C:/cats/a.lrcat", "C:/cats/old.lrcat"
T, T_ORIG = "d:/lib/shoot-b/img_0001.arw", "D:/Lib/shoot-b/IMG_0001.ARW"
T0, T0_ORIG = "d:/lib/shoot-a/img_0002.arw", "D:/Lib/shoot-a/IMG_0002.ARW"
FULL = "full-hash"
KEPT_FOR_WORK, ON_LIST, KEPT = "kept_for_work", "on_list", "kept"


class LiftWorld(World):
    """`World` whose `stat` also gives a device and one file index per path (the hash rows carry it)."""

    def __init__(self):
        super().__init__()
        self.inos = {}

    def ino(self, path):
        return self.inos.setdefault(paths.norm(path), len(self.inos) + 1)

    def stat(self, path):
        st = super().stat(path)
        st.st_dev, st.st_ino = 1, self.ino(path)
        return st

    def index(self, *groups):
        """A `HashIndex` whose groups each hold the (path_norm, path_orig) pairs that share one hash."""
        by_path, by_hash = {}, {}
        for n, group in enumerate(groups):
            rows = sorted((Hashed(norm, orig, SIZE, MTIME, self.ino(norm), f"{FULL}-{n}") for norm, orig in group),
                          key=lambda h: h.path_norm)
            by_path.update({h.path_norm: h for h in rows})
            by_hash[(f"{FULL}-{n}", SIZE)] = tuple(rows)
        return HashIndex(by_path, by_hash)


def mk(source, role, picks, developed=None, settings=None, sidecars=(), relative=(), **over):
    """A view like `view`, plus `settings` and the absolute-form (developed, settings) pairs."""
    developed = developed or {k: [0] * len(v) for k, v in picks.items()}
    settings = settings or {}
    pairs = {k: tuple(zip(developed[k], settings.get(k) or [None] * len(developed[k])))
             for k in set(picks) - set(relative)}
    return view(source, role, picks=picks, developed=developed, sidecars=sidecars, **over)._replace(
        settings=settings, absolute=frozenset(set(picks) - set(relative)), absolute_settings=pairs)


def images(spec, p=((1, S),), t=((1, S),)):
    """Set the live catalog's images of the reject and of the twin: lists of (developed, settings)."""
    spec.update(picks={P: [-1] * len(p), T: [0] * len(t)},
                developed={P: [d for d, _ in p], T: [d for d, _ in t]},
                settings={P: [s for _, s in p], T: [s for _, s in t]})


def old_state(path, developed, settings, pick=0):
    return dict(source=OLD, role="old_state", picks={path: [pick]}, developed={path: [developed]},
                settings={path: [settings]})


class Scene:
    def __init__(self):
        self.world = LiftWorld()
        self.world.clean(P)
        self.world.file(T)
        self.live = dict(source=LIVE, role="live", picks={P: [-1], T: [0]}, developed={P: [1], T: [1]},
                         settings={P: [S], T: [S]})
        self.shoot = dict(source=SHOOT, role="shoot", picks={T: [0]})
        self.extra = []
        self.twins = [(T, T_ORIG)]
        self.kw = {}

    def views(self):
        return [mk(**self.live), mk(**self.shoot)] + [mk(**spec) for spec in self.extra]

    def decide(self, views=None, hashes=None, **kw):
        hashes = self.world.index([(P, P)] + self.twins) if hashes is None else hashes
        return self.world.decide(self.views() if views is None else views, hashes=hashes, **{**self.kw, **kw})


class LiftTest(unittest.TestCase):
    def test_all_conditions_holding_lift_the_develop_hold_and_name_the_twin_and_the_work(self):
        got = Scene().decide()[P]
        self.assertEqual((got.verdict, got.twin, got.twin_settings, got.developed_in),
                         (ON_LIST, T_ORIG, S, [(LIVE, "live", "develop")]))

    def test_the_default_hashes_lift_nothing_rule_26_unchanged(self):
        scene = Scene()
        got = scene.decide(hashes=EMPTY)[P]
        self.assertEqual((got.verdict, got.twin, got.twin_settings), (KEPT_FOR_WORK, "", ""))

    def test_a_lifted_reject_held_by_another_verdict_keeps_its_twin_columns(self):
        def coverage(scene):
            scene.kw["complete"] = False

        def kept(scene):
            scene.world.clean(SIDE)
            scene.live["sidecars"] = [SIDE]
            scene.extra.append(dict(source="C:/cats/b.lrcat", role="shoot", picks={}, photos=[SIDE]))

        def kept_in_live(scene):
            scene.extra.append(dict(source="C:/cats/live2.lrcat", role="live", picks={P: [0]}))

        def archive(bad):
            return lambda scene: scene.world.archive(P, 1, bad)

        cases = {"coverage_incomplete": (coverage, "coverage_incomplete"), "kept": (kept, KEPT),
                 "kept_in_live": (kept_in_live, "kept_in_live"), "outside_backup": (archive("outside"), OUTSIDE),
                 "changed_since_backup": (archive("changed"), CHANGED), "no_archive_copy": (archive("nocopy"), NOCOPY)}
        for name, (mutate, verdict) in cases.items():
            with self.subTest(name):
                scene = Scene()
                mutate(scene)
                got = scene.decide()[P]
                self.assertEqual((got.verdict, got.twin, got.twin_settings, got.developed_in),
                                 (verdict, T_ORIG, S, [(LIVE, "live", "develop")]))

    def test_a_missing_reject_has_no_counted_hash_so_its_develop_work_still_keeps_it(self):
        scene = Scene()
        del scene.world.files[paths.norm(P)]
        got = scene.decide()[P]
        self.assertEqual((got.verdict, got.twin, got.twin_settings), (KEPT_FOR_WORK, "", ""))


def _live(**over):
    return lambda scene: scene.live.update(**over)


def _shoot(**over):
    return lambda scene: scene.shoot.update(**over)


def _images(**kw):
    return lambda scene: images(scene.live, **kw)


def _extra(spec):
    return lambda scene: scene.extra.append(spec)


class ConditionTest(unittest.TestCase):
    def check(self, cases):
        for name, (mutate, verdict) in cases.items():
            with self.subTest(name):
                scene = Scene()
                mutate(scene)
                got = scene.decide()[P]
                self.assertEqual((got.verdict, got.twin, got.twin_settings), (verdict, "", ""))

    def test_condition_2_the_twin_stays_each_fact_failing_alone(self):
        placeholder = lambda path, st: PLACEHOLDER_ATTR if "shoot-b" in path.lower() else 0

        def attrs_of(scene):
            scene.kw["attrs_of"] = placeholder

        no_t = dict(picks={P: [-1]}, developed={P: [1]}, settings={P: [S]})
        self.check({
            "twin file gone": (lambda s: s.world.files.pop(paths.norm(T)), KEPT_FOR_WORK),
            "twin placeholder by its stat attributes": (lambda s: s.world.file(T, attrs=PLACEHOLDER_ATTR), KEPT_FOR_WORK),
            "twin placeholder by attrs_of alone": (attrs_of, KEPT_FOR_WORK),
            "live records the twin only as a sidecar": (_live(**no_t, sidecars=[T]), KEPT_FOR_WORK),
            "shoot records the twin only as a sidecar": (_shoot(picks={}, sidecars=[T]), KEPT_FOR_WORK),
            "live does not record the twin": (_live(**no_t), KEPT_FOR_WORK),
            "the shoot record is in an old state only": (_shoot(role="old_state"), KEPT_FOR_WORK),
            "the shoot catalog is excluded": (_shoot(excluded=True), KEPT_FOR_WORK),
            "the shoot catalog is unreadable": (_shoot(status="unreadable"), KEPT_FOR_WORK),
            "the live image is in an old state only": (
                lambda s: (s.live.update(**no_t), s.extra.append(old_state(T, 1, S))), KEPT_FOR_WORK),
            "the live image is developed 0": (_images(t=((0, S),)), KEPT_FOR_WORK),
            "the live image is developed empty": (_images(t=((None, S),)), KEPT_FOR_WORK),
            "equal settings only in a shoot catalog": (
                lambda s: (images(s.live, t=((1, U),)), s.shoot.update(developed={T: [1]}, settings={T: [S]})), KEPT_FOR_WORK),
            "equal settings only in an old state": (
                lambda s: (images(s.live, t=((1, U),)), s.extra.append(old_state(T, 1, S))), KEPT_FOR_WORK),
            "the shoot record only through its relative form": (_shoot(relative=[T]), KEPT_FOR_WORK),
            "the live image only through its relative form": (_live(relative=[T]), KEPT_FOR_WORK),
            "developed 1 with other settings and developed 0 with the same settings, on two live images": (
                _images(t=((1, U), (0, S))), KEPT_FOR_WORK),
            "the same two images the other way round": (_images(t=((0, S), (1, U))), KEPT_FOR_WORK),
        })

    def test_a_twin_with_one_matching_live_image_among_others_stays(self):
        scene = Scene()
        images(scene.live, t=((1, U), (0, U), (1, S)))
        self.assertEqual(scene.decide()[P].twin, T_ORIG)

    def test_condition_3_a_shoot_catalog_that_records_the_reject_holds_it(self):
        both = dict(picks={T: [0], P: [-1]}, developed={T: [0], P: [0]})
        self.check({
            "records and rejects it as a photo": (_shoot(**both), KEPT_FOR_WORK),
            "records it without rejecting it": (_shoot(picks={T: [0], P: [0]}), KEPT),
            "records it as a sidecar": (_shoot(sidecars=[P]), KEPT_FOR_WORK),
            "records it through its relative form only": (_shoot(picks={T: [0], P: [0]}, relative=[P]), KEPT),
        })

    def test_condition_3_other_roles_and_unread_catalogs_do_not_hold(self):
        for name, over in {"old state": {"role": "old_state"}, "excluded": {"excluded": True},
                           "unreadable": {"status": "unreadable"}}.items():
            with self.subTest(name):
                scene = Scene()
                scene.extra.append({**dict(source="C:/cats/b.lrcat", role="shoot", picks={P: [0]}), **over})
                self.assertEqual(scene.decide()[P].twin, T_ORIG)

    def test_condition_4_a_pick_in_an_old_state_keeps_the_reject(self):
        self.check({"pick 1 in an old state": (_extra(old_state(P, 0, None, pick=1)), KEPT_FOR_WORK)})

    def test_condition_5_the_settings_of_every_image_of_the_reject_must_match_one_digest(self):
        two = ((1, S), (1, U))
        self.check({
            "other settings on the reject": (_images(p=((1, U),)), KEPT_FOR_WORK),
            "empty on the reject": (_images(p=((1, None),)), KEPT_FOR_WORK),
            "empty string on the reject": (_images(p=((1, ""),)), KEPT_FOR_WORK),
            "empty on both": (_images(p=((1, None),), t=((1, None),)), KEPT_FOR_WORK),
            "empty string on both": (_images(p=((1, ""),), t=((1, ""),)), KEPT_FOR_WORK),
            "developed empty on the reject": (_images(p=((None, S),)), KEPT_FOR_WORK),
            "a virtual copy with other settings, second": (_images(p=((1, S), (1, U))), KEPT_FOR_WORK),
            "a virtual copy with other settings, first": (_images(p=((1, U), (1, S))), KEPT_FOR_WORK),
            "an old state with other settings": (_extra(old_state(P, 1, U)), KEPT_FOR_WORK),
            "a later live image with other settings": (
                _extra(dict(source="C:/cats/live2.lrcat", role="live", picks={P: [-1]}, developed={P: [1]},
                            settings={P: [U]})), KEPT_FOR_WORK),
            "two reject images match different twin images": (_images(p=two, t=two), KEPT_FOR_WORK),
            "an old state with empty settings": (_extra(old_state(P, 1, None)), KEPT_FOR_WORK),
            "an old state developed empty": (_extra(old_state(P, None, S)), KEPT_FOR_WORK),
        })

    def test_condition_5_an_image_developed_0_is_not_counted_and_the_same_settings_everywhere_lift(self):
        cases = {"another image developed 0 with other settings": lambda s: images(s.live, p=((1, S), (0, U))),
                 "another image developed 0 with empty settings, first": lambda s: images(s.live, p=((0, None), (1, S))),
                 "an old state with the same settings": lambda s: s.extra.append(old_state(P, 1, S))}
        for name, mutate in cases.items():
            with self.subTest(name):
                scene = Scene()
                mutate(scene)
                got = scene.decide()[P]
                self.assertEqual((got.verdict, got.twin_settings), (ON_LIST, S))


class FamilyTest(unittest.TestCase):
    def test_both_copies_rejects_the_one_a_shoot_catalog_records_stays_and_the_other_goes(self):
        scene = Scene()
        scene.world.clean(T)
        scene.live.update(picks={P: [-1], T: [-1]})
        scene.shoot.update(picks={T: [-1]}, developed={T: [0]})
        got = scene.decide()
        self.assertEqual((got[P].verdict, got[P].twin), (ON_LIST, T_ORIG))
        self.assertEqual((got[T].verdict, got[T].twin), (KEPT_FOR_WORK, ""))

    def test_the_first_candidate_by_path_that_passes_condition_2_is_the_twin(self):
        for name, shoot_records_t0, want in (("the earlier one fails", False, T_ORIG), ("both pass", True, T0_ORIG)):
            with self.subTest(name):
                scene = Scene()
                scene.world.file(T0)
                scene.twins.append((T0, T0_ORIG))
                scene.live["picks"][T0], scene.live["developed"][T0], scene.live["settings"][T0] = [0], [1], [S]
                if shoot_records_t0:
                    scene.shoot["picks"][T0] = [0]
                self.assertEqual(scene.decide()[P].twin, want)

    def test_a_twin_that_is_an_existing_sidecar_of_another_lifted_reject_holds_that_row_rule_29(self):
        y, z = "d:/lib/shoot-b/img_0001.dng", "d:/lib/shoot-c/img_0001.dng"
        scene = Scene()
        for path in (y, T):
            scene.world.clean(path)
        scene.world.file(z)
        scene.live.update(picks={P: [-1], T: [0], y: [-1], z: [0]}, sidecars=[T],
                          developed={P: [1], y: [1], T: [1], z: [1]}, settings={P: [S], y: [S], T: [S], z: [S]})
        scene.shoot.update(picks={T: [0], z: [0]})
        got = scene.decide(hashes=scene.world.index([(P, P), (T, T_ORIG)], [(y, y), (z, z)]))
        self.assertEqual((got[P].verdict, got[P].twin), (ON_LIST, T_ORIG))
        self.assertEqual((got[y].verdict, got[y].held_by, got[y].twin), (KEPT, T, z))
        self.assertNotIn(T, got)

    def test_a_sidecars_own_develop_work_still_holds_the_lifted_row(self):
        scene = Scene()
        scene.world.clean(SIDE)
        scene.live["sidecars"] = [SIDE]
        scene.extra.append(old_state(SIDE, 1, None))
        got = scene.decide()[P]
        self.assertEqual((got.verdict, got.held_by, got.twin), (KEPT_FOR_WORK, SIDE, T_ORIG))


class StoredImagesTest(unittest.TestCase):
    """Cases that need a stored path with several images: rows in a temp database, read by `latest_views`."""

    def decide(self, live_p, old_p=None):
        with tempfile.TemporaryDirectory() as tmp:
            conn = store.connect(os.path.join(tmp, "work.db"))
            scan = store.begin_scan(conn, "C:/cats", "catalogs")
            for source, role, rows in ((LIVE, "live", [(P, -1, d, s) for d, s in live_p] + [(T, 0, 1, S)]),
                                       (SHOOT, "shoot", [(T, 0, 0, None)]),
                                       (OLD, "old_state", [(P, 0, d, s) for d, s in old_p or ()])):
                cat = store.insert_row(conn, "catalog", scan_id=scan, source_path=source, role=role, status="ok")
                store.insert_many(conn, "image", [dict(catalog_id=cat, path_norm=pn, via="absolute", pick=pick,
                                                       developed=d, settings=s) for pn, pick, d, s in rows])
            store.finish_scan(conn, scan, "complete")
            views = latest_views(conn, excluded_catalogs=[])
            conn.close()
        scene = Scene()
        return scene.decide(views)[P]

    def test_a_stored_path_with_two_settings_keeps_the_reject_whichever_comes_first(self):
        for name, rows in {"S first": [(1, S), (1, U)], "U first": [(1, U), (1, S)]}.items():
            with self.subTest(name):
                got = self.decide(rows)
                self.assertEqual((got.verdict, got.twin), (KEPT_FOR_WORK, ""))

    def test_one_digest_on_two_stored_images_lifts(self):
        got = self.decide([(1, S), (1, S)])
        self.assertEqual((got.verdict, got.twin, got.twin_settings), (ON_LIST, T_ORIG, S))

    def test_an_old_row_with_null_settings_keeps_and_the_same_digest_does_not(self):
        for name, settings, verdict in (("NULL", None, KEPT_FOR_WORK), ("the same digest", S, ON_LIST)):
            with self.subTest(name):
                self.assertEqual(self.decide([(1, S)], [(1, settings)]).verdict, verdict)


if __name__ == "__main__":
    unittest.main()
