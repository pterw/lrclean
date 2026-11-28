import json
import os
import tempfile
import unittest
from unittest import mock

from lrclean import paths, store
from lrclean.classify import Coverage
from lrclean.mover_gate import Refusal, RunFacts, first_refusal, gather, link_refusal, list_approved, purge_approved
from lrclean.mover_list import LISTS, REJECTS
from mover_world import World

SHA = "ab" * 32
OTHER = "cd" * 32
COMPLETE = Coverage(True, [])
STAGE_ORDER = ["approval", "config", "lightroom", "lock", "freshness", "coverage"]
PURGE_ORDER = ["config", "approval", "lightroom", "lock", "coverage"]
CODES = {"approval": 2, "config": 2, "lightroom": 3, "lock": 3, "freshness": 4, "coverage": 4}


class Fixture:
    """A World, a work database with two catalogs (scans 1 and 2), a summary naming both."""

    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.w = World(tmp.name)
        self.conn = store.connect(os.path.join(self.w.base, "work", "gate.db"))
        test.addCleanup(self.conn.close)
        with open(os.path.join(self.w.base, "work", "config.json"), encoding="utf-8") as handle:
            self.cfg = json.load(handle)
        self.cats = [self.w.add("shoot-a/a.lrcat", b"a", 1), self.w.add("shoot-b/b.lrcat", b"b", 1)]
        self.photos = [self.w.path("shoot-a/1.arw"), self.w.path("shoot-a/2.arw")]
        for cat in self.cats:
            self.scan(cat)
        self.write_summary([1, 2])
        self.processes = lambda: ["explorer.exe"]

    def scan(self, cat, role="shoot") -> int:
        scan_id = store.begin_scan(self.conn, os.path.dirname(cat), "catalogs", now=lambda: "t")
        store.finish_scan(self.conn, scan_id, "complete", now=lambda: "t")
        store.insert_row(self.conn, "catalog", scan_id=scan_id, source_path=cat, role=role, status="ok",
                         size=1, mtime_ns=1, wal_bytes=0, has_lock=0)
        self.conn.commit()
        return scan_id

    def write_summary(self, ids, listed=SHA, name="rejects-summary.md") -> None:
        """The summary names `listed` as the list's SHA-256 (None: no `## Removal list` section)."""
        lines = ["# Summary", "", "## Scans", ""]
        lines += [f"- catalogs scan {n} of {self.w.photo_root}: complete, finished t" for n in ids]
        if listed:
            lines += ["", "## Removal list", "", f"- SHA-256: {listed}"]
        with open(os.path.join(self.w.base, "out", name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")

    def facts(self, op, *, sources=None, stale=(), coverage=COMPLETE, stat=None, list_sha=SHA,
              listing=REJECTS) -> RunFacts:
        ctx = self.w.ctx()
        if stat:
            ctx = ctx._replace(seams=ctx.seams._replace(stat=stat))
        return gather(op, cfg=self.cfg, ctx=ctx, conn=self.conn, processes=self.processes,
                      stale=list(stale), coverage=coverage, sources=self.photos if sources is None else sources,
                      list_sha256=None if op == "purge" else list_sha, listing=listing)

    def refusal(self, op, *, approval=None, **kwargs) -> Refusal | None:
        return first_refusal(op, self.facts(op, **kwargs), approval)

    def out_state(self) -> dict:
        out = os.path.join(self.w.base, "out")
        state = {}
        for name in sorted(os.listdir(out)):
            with open(os.path.join(out, name), "rb") as handle:
                state[name] = handle.read()
        return state


class ApprovalTest(unittest.TestCase):
    def test_rule32_equal_approval_gives_none_in_either_case(self):
        for check in (list_approved, purge_approved):
            for approved in (SHA, SHA.upper()):
                with self.subTest(check=check.__name__, approved=approved):
                    self.assertIsNone(check(SHA, approved))

    def test_rule32_a_different_approval_is_exit_2_and_prints_the_computed_one(self):
        for check in (list_approved, purge_approved):
            for approved in (OTHER, "", None, SHA[:63], SHA[:8], SHA + "0", " " + SHA, SHA + " "):
                with self.subTest(check=check.__name__, approved=approved):
                    refusal = check(SHA, approved)
                    self.assertEqual(refusal.code, 2)
                    self.assertIn(SHA, refusal.message)


class OrderTest(unittest.TestCase):
    def pair_first(self, op, order):
        for first, second in zip(order, order[1:]):
            facts = RunFacts(**{f: f"msg-{f}" if f in (first, second) else ""
                                for f in RunFacts._fields})
            approval = Refusal(2, "msg-approval") if "approval" in (first, second) else None
            with self.subTest(op=op, first=first, second=second):
                got = first_refusal(op, facts, approval)
                self.assertEqual((got.code, got.message), (CODES[first], f"msg-{first}"))

    def test_rule33_stage_refuses_in_the_order_of_the_rule_for_every_neighbouring_pair(self):
        self.pair_first("stage", STAGE_ORDER)

    def test_rule33_purge_refuses_in_the_order_of_the_rule_for_every_neighbouring_pair(self):
        self.pair_first("purge", PURGE_ORDER)

    def test_rule33_purge_skips_the_freshness_test_and_nothing_else(self):
        facts = RunFacts("", "", "", "msg-freshness", "")
        self.assertIsNone(first_refusal("purge", facts, None))
        self.assertEqual(first_refusal("stage", facts, None).code, 4)

    def test_rule33_nothing_wrong_gives_no_refusal(self):
        for op in ("stage", "purge"):
            self.assertIsNone(first_refusal(op, RunFacts("", "", "", "", ""), None))


class LinkRefusalTest(unittest.TestCase):
    def test_rc103_each_linked_tool_folder_is_exit_2_and_a_missing_folder_is_none(self):
        base = os.path.join(os.path.abspath(os.sep), "tool")
        work = os.path.join(base, "work")
        folders = [base, work, os.path.join(work, "staging"), os.path.join(work, "journal"),
                   os.path.join(base, "out"), os.path.join(work, "catalogs"),
                   os.path.join(work, "catalogs", "shoot-a-x")]

        def listdir(path):
            if path != os.path.join(work, "catalogs"):
                raise FileNotFoundError(path)
            return ["shoot-a-x"]

        self.assertIsNone(link_refusal(base, lambda path: False, listdir))
        for error in (FileNotFoundError, NotADirectoryError):
            def no_catalogs(path, error=error):
                raise error(path)

            with self.subTest(error.__name__):
                self.assertIsNone(link_refusal(base, lambda path: False, no_catalogs))

        def unlistable(path):
            raise PermissionError(path)

        catalogs = os.path.join(work, "catalogs")
        self.assertRaises(PermissionError, unlistable, catalogs)
        listed = []

        def spy(path):
            listed.append(path)
            return unlistable(path)

        with self.subTest("an unlinked, unlistable work/catalogs"):
            refusal = link_refusal(base, lambda path: False, spy)
            self.assertEqual(listed, [catalogs])
            self.assertEqual(refusal.code, 2)
            self.assertIn(catalogs, refusal.message)
            self.assertIn("cannot be listed", refusal.message)
        for folder in folders[:6]:
            with self.subTest("linked before listing", folder=folder):
                listed.clear()
                refusal = link_refusal(base, lambda path, linked=folder: path == linked, spy)
                self.assertEqual(listed, [])
                self.assertEqual(refusal.code, 2)
                self.assertIn(folder, refusal.message)
                self.assertIn("is a link", refusal.message)
                self.assertNotIn("cannot be listed", refusal.message)
        for folder in folders:
            with self.subTest(folder):
                refusal = link_refusal(base, lambda path, linked=folder: path == linked, listdir)
                self.assertEqual(refusal.code, 2)
                self.assertIn(folder, refusal.message)


class LinkMessageTest(unittest.TestCase):
    """Exit 2 names where a link points, and a catalog's snapshot folder gets its own sentence."""

    def setUp(self):
        self.base = os.path.join(os.path.abspath(os.sep), "tool")
        self.snapshots = os.path.join(self.base, "work", "catalogs")
        self.snapshot = os.path.join(self.snapshots, "shoot-a-x")

    def refuse(self, linked, readlink):
        with mock.patch("lrclean.mover_gate.os.readlink", readlink):
            return link_refusal(self.base, lambda path: path == linked, lambda path: ["shoot-a-x"])

    def test_rc103_a_link_names_where_it_points_for_a_tool_folder_and_a_snapshot_folder(self):
        target = os.path.join(os.path.abspath(os.sep), "elsewhere", "shoot-b")
        for linked in (os.path.join(self.base, "out"), self.snapshot):
            with self.subTest(linked):
                self.assertNotIn(target, linked)
                refusal = self.refuse(linked, lambda path: target)
                self.assertEqual(refusal.code, 2)
                self.assertIn(linked, refusal.message)
                self.assertIn(f"points to {target}", refusal.message)

    def test_rc103_a_link_whose_target_cannot_be_read_gets_a_fallback_sentence_and_no_exception(self):
        def unreadable(path):
            raise OSError("not a link here")

        for linked in (os.path.join(self.base, "out"), self.snapshot):
            with self.subTest(linked):
                refusal = self.refuse(linked, unreadable)
                self.assertEqual(refusal.code, 2)
                self.assertIn(linked, refusal.message)
                self.assertIn("points somewhere that cannot be read", refusal.message)

    def test_rc155_a_snapshot_folder_that_is_a_link_has_its_own_sentence(self):
        tool = self.refuse(os.path.join(self.base, "work", "journal"), lambda path: "x").message
        snapshot = self.refuse(self.snapshot, lambda path: "x").message
        self.assertIn("a catalog's snapshot folder", snapshot)
        self.assertNotIn("snapshot folder", tool)
        self.assertIn("the tool's folders must be real folders", tool)
        self.assertNotIn("the tool's folders must be real folders", snapshot)


class WholeRunRefusalTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)

    def test_rule33_a_clean_run_is_not_refused(self):
        for op in ("stage", "purge"):
            self.assertIsNone(self.fx.refusal(op))

    def test_rule33_configuration_faults_are_exit_2(self):
        fx = self.fx
        missing = os.path.join(fx.w.tmp, "nowhere")
        cases = {
            "one archive root": ("archive_roots", fx.w.archive_roots[:1], "fewer than two"),
            "last root not connected": ("archive_roots", [fx.w.archive_roots[0], missing], missing),
            "first root not connected": ("archive_roots", [missing, fx.w.archive_roots[1]], missing),
            "a file in place of a root": ("archive_roots", [fx.cats[0], fx.w.archive_roots[1]], "not a connected"),
            "photo_root missing": ("photo_root", missing, "photo_root"),
            "a file in place of photo_root": ("photo_root", fx.cats[0], "photo_root"),
            "live catalog excluded": ("excluded_catalogs", [fx.cats[0]], "excluded"),
            "live catalog excluded in another spelling": (
                "excluded_catalogs", [fx.cats[0].upper().replace("\\", "/")], "excluded"),
        }
        fx.cfg["live_catalog"] = fx.cats[0]
        for name, (key, value, text) in cases.items():
            for op in ("stage", "purge"):
                with self.subTest(name, op=op):
                    cfg = dict(fx.cfg)
                    fx.cfg[key] = value
                    refusal = fx.refusal(op, sources=[])
                    fx.cfg = cfg
                    self.assertEqual(refusal.code, 2)
                    self.assertIn(text, refusal.message)

    def test_rule33_a_source_that_may_not_be_touched_is_exit_2_wherever_it_stands(self):
        fx = self.fx
        fx.cfg["photo_root"] = fx.w.tmp
        fx.cfg["backup_roots"] = [os.path.join(fx.w.tmp, "backups")]
        fine = [os.path.join(fx.w.tmp, "photos", "a.arw"), os.path.join(fx.w.tmp, "photos", "b.arw")]
        bad = {
            "outside photo_root": (os.path.join(os.path.dirname(fx.w.tmp), "else", "x.arw"), "outside"),
            "inside an archive root": (os.path.join(fx.w.archive_roots[1], "x.arw"), "inside"),
            "inside a backup root": (os.path.join(fx.w.tmp, "backups", "x.arw"), "inside"),
            "inside the tool's folder": (os.path.join(fx.w.base, "work", "x.arw"), "inside"),
            "photo_root itself": (fx.w.tmp, "outside"),
        }
        for name, (offender, text) in bad.items():
            for place in range(3):  # first, between the good ones, last
                for op in ("stage", "purge"):
                    sources = [*fine[:place], offender, *fine[place:]]
                    with self.subTest(name, place=place, op=op):
                        refusal = fx.refusal(op, sources=sources)
                        self.assertEqual(refusal.code, 2)
                        self.assertIn(offender, refusal.message)
                        self.assertIn(text, refusal.message)

    def test_rule33_lightroom_running_or_an_unreadable_process_list_is_exit_3(self):
        def unreadable():
            raise RuntimeError("tasklist returned nothing")
        for name, processes in {"running, not first": lambda: ["explorer.exe", "Lightroom.exe"],
                                "unreadable": unreadable}.items():
            for op in ("stage", "purge"):
                with self.subTest(name, op=op):
                    self.fx.processes = processes
                    self.assertEqual(self.fx.refusal(op).code, 3)

    def test_rule33_a_lock_beside_any_catalog_is_exit_3_and_names_it(self):
        for index in (0, 1):  # the first catalog, and one that is not
            lock = self.fx.w.add(f"{os.path.relpath(self.fx.cats[index], self.fx.w.photo_root)}.lock", b"", 1)
            for op in ("stage", "purge"):
                with self.subTest(index=index, op=op):
                    refusal = self.fx.refusal(op)
                    self.assertEqual(refusal.code, 3)
                    self.assertIn(self.fx.cats[index], refusal.message)
            os.remove(lock)

    def test_rule33_a_lock_whose_stat_fails_for_another_reason_is_a_lock_exit_3(self):
        fx = self.fx
        for index in (0, 1):
            def stat(path, blocked=fx.cats[index] + ".lock"):
                if paths.norm(path) == paths.norm(blocked):
                    raise PermissionError("denied")
                return fx.w.stat(path)
            for op in ("stage", "purge"):
                with self.subTest(index=index, op=op):
                    refusal = first_refusal(op, fx.facts(op, stat=stat), None)
                    self.assertEqual(refusal.code, 3)
                    self.assertIn(fx.cats[index], refusal.message)

    def test_rule33_a_lock_name_too_long_to_exist_is_no_lock_and_one_that_fits_still_is_issue_18(self):
        fx = self.fx
        astral = "\U0001F4F7" * 123 + ".lrcat"

        def stat(path):
            if path.endswith(".lock"):
                raise OSError(22, "invalid name")
            return fx.w.stat(path)

        cases = {"250 units": ("c" * 244 + ".lrcat", True), "251 units": ("c" * 245 + ".lrcat", False),
                 "252 units, astral": (astral, False)}
        self.assertEqual([paths.name_units(n + ".lock") for n, _ in cases.values()], [255, 256, 257])
        self.assertEqual(len(astral + ".lock"), 134)
        for name, (catalog, locked) in cases.items():
            cat = os.path.join(os.path.dirname(fx.cats[0]), catalog)
            fx.conn.execute('DELETE FROM "catalog"')
            store.insert_row(fx.conn, "catalog", scan_id=1, source_path=cat, role="shoot", status="ok",
                             size=1, mtime_ns=1, wal_bytes=0, has_lock=0)
            fx.conn.commit()
            for op in ("stage", "purge"):
                with self.subTest(name, op=op):
                    facts = fx.facts(op, stat=stat)
                    if locked:
                        refusal = first_refusal(op, facts, None)
                        self.assertEqual(refusal.code, 3)
                        self.assertIn(cat, refusal.message)
                    else:
                        self.assertEqual(facts.lock, "")

    def test_rc124_a_lock_beside_an_excluded_catalog_refuses_nothing_in_any_spelling(self):
        fx = self.fx
        fx.w.add("shoot-b/b.lrcat.lock", b"", 1)
        for spelling in (fx.cats[1], fx.cats[1].upper(), fx.cats[1].replace("/", "\\")):
            fx.cfg["excluded_catalogs"] = [spelling]
            for op in ("stage", "purge"):
                with self.subTest(spelling=spelling, op=op):
                    self.assertEqual(fx.facts(op).lock, "")
        fx.cfg["excluded_catalogs"] = [fx.cats[0]]  # another catalog excluded: the lock still counts
        self.assertEqual(fx.refusal("stage").code, 3)

    def test_rc125_a_locked_catalog_near_an_excluded_one_is_still_refused_and_named(self):
        fx = self.fx
        a = fx.cats[0]
        longer, same_folder = a + "-2.lrcat", fx.w.path("shoot-a/other.lrcat")
        inside = fx.w.path("old/shoot-a/a.lrcat")  # holds the excluded path as a substring, not as a prefix
        for cat in (longer, same_folder):  # rows of the scan of the folder a.lrcat lies in
            fx.w.add(os.path.relpath(cat, fx.w.photo_root), b"x", 1)
            store.insert_row(fx.conn, "catalog", scan_id=1, source_path=cat, role="shoot", status="ok",
                             size=1, mtime_ns=1, wal_bytes=0, has_lock=0)
        fx.w.add("old/shoot-a/a.lrcat", b"x", 1)
        fx.scan(inside)
        cases = [("prefix", a, longer), ("same folder", a, same_folder), ("substring", a, inside),
                 ("contained in the excluded path", longer, a)]
        for name, excluded, locked in cases:
            lock = fx.w.add(f"{os.path.relpath(locked, fx.w.photo_root)}.lock", b"", 1)
            fx.cfg["excluded_catalogs"] = [excluded]
            for op in ("stage", "purge"):
                with self.subTest(case=name, op=op):
                    refusal = fx.refusal(op)
                    self.assertEqual(refusal.code, 3)
                    self.assertEqual(refusal.message.count(locked), 1)
            os.remove(lock)

    def test_rc124_every_lock_is_named_in_one_refusal(self):
        fx = self.fx
        for cat in fx.cats:
            fx.w.add(f"{os.path.relpath(cat, fx.w.photo_root)}.lock", b"", 1)
        for op in ("stage", "purge"):
            with self.subTest(op=op):
                refusal = fx.refusal(op)
                self.assertEqual(refusal.code, 3)
                for cat in fx.cats:
                    self.assertEqual(refusal.message.count(cat), 1)

    def test_rule34_a_new_lock_is_exit_3_though_the_catalog_also_counts_as_changed(self):
        fx = self.fx
        fx.w.add("shoot-b/b.lrcat.lock", b"", 1)
        self.assertEqual(fx.refusal("stage", stale=[fx.cats[1]]).code, 3)

    def test_rule33_a_refusal_changes_nothing_under_out(self):
        fx = self.fx
        fx.processes = lambda: ["lightroom.exe"]
        before = fx.out_state()
        self.assertEqual(fx.refusal("stage").code, 3)
        fx.processes = lambda: []
        self.assertEqual(fx.refusal("stage", coverage=Coverage(False, ["x"])).code, 4)
        self.assertEqual(fx.out_state(), before)


class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)

    def assert_stale(self, refusal, text):
        self.assertEqual(refusal.code, 4)
        self.assertIn("run catalogs and rejects again", refusal.message)
        self.assertIn("must not be acted on", refusal.message)
        self.assertIn(text, refusal.message)

    def purge_passes(self, **kwargs):
        self.assertIsNone(self.fx.refusal("purge", **kwargs))

    def test_rule34_no_summary_is_exit_4(self):
        os.remove(os.path.join(self.fx.w.base, "out", "rejects-summary.md"))
        self.assert_stale(self.fx.refusal("stage"), "summary")
        self.purge_passes()
        self.assertEqual(self.fx.facts("purge").freshness, "")

    def test_rule32_a_list_the_summary_does_not_name_is_exit_4_and_purge_does_not_read_it(self):
        fx = self.fx
        cases = {
            "another list's SHA-256": (SHA, OTHER),
            "no Removal list section": (None, SHA),
            "no SHA-256 asked for": (SHA, ""),
            "the SHA-256 in capitals": (SHA.upper(), SHA),
            "a truncated SHA-256 in the summary": (SHA[:63], SHA),
        }
        for name, (listed, asked) in cases.items():
            with self.subTest(name):
                fx.write_summary([1, 2], listed=listed)
                self.assert_stale(fx.refusal("stage", list_sha=asked), "SHA-256")
                self.purge_passes()
        fx.write_summary([1, 2])
        self.assertIsNone(fx.refusal("stage"))

    def test_rule53_each_list_is_paired_with_its_own_summary_and_names_its_own_command(self):
        fx = self.fx
        for listing in LISTS:
            other = next(item for item in LISTS if item != listing)
            with self.subTest(listing.kind):
                for item in LISTS:
                    path = os.path.join(fx.w.base, "out", item.summary)
                    if os.path.exists(path):
                        os.remove(path)
                fx.write_summary([1, 2], name=other.summary)  # the other list's summary alone
                refusal = fx.refusal("stage", listing=listing)
                self.assertEqual(refusal.code, 4)
                self.assertIn(f"run catalogs and {listing.command} again", refusal.message)
                self.assertIn(f"out/{listing.summary} is missing", refusal.message)
                fx.write_summary([1, 2], name=listing.summary)
                self.assertIsNone(fx.refusal("stage", listing=listing))
                fx.write_summary([1, 2], listed=OTHER, name=listing.summary)
                self.assertIn(f"not the one {listing.command} wrote", fx.refusal("stage", listing=listing).message)

    def test_rule34_a_summary_naming_another_scan_kind_is_exit_4(self):
        fx = self.fx
        path = os.path.join(fx.w.base, "out", "rejects-summary.md")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.replace("- catalogs scan 1 of", "- files scan 1 of"))
        self.assert_stale(fx.refusal("stage"), "scans")

    def test_rule34_no_catalogs_scan_at_all_is_exit_4_whatever_the_summary_says(self):
        fx = self.fx
        fx.conn.execute('DELETE FROM "catalog"')
        fx.conn.execute('DELETE FROM "scan"')
        fx.conn.commit()
        fx.write_summary([])
        self.assert_stale(fx.refusal("stage"), "no catalogs scan")

    def test_rule34_scans_the_summary_does_not_name_exactly_are_exit_4(self):
        fx = self.fx
        cases = {
            "a latest scan the summary lacks": ([1], None),
            "a root rescanned after the summary": ([1, 2], fx.cats[0]),
            "a scan that is not the latest named": ([1, 2, 9], None),
        }
        for name, (named, rescanned) in cases.items():
            with self.subTest(name):
                fx.write_summary(named)
                if rescanned:
                    fx.scan(rescanned)
                self.assert_stale(fx.refusal("stage"), "scans")
                self.purge_passes()

    def test_rule34_a_catalog_source_changed_is_exit_4_for_any_catalog(self):
        for cat in self.fx.cats:
            with self.subTest(cat=cat):
                self.assert_stale(self.fx.refusal("stage", stale=[cat]), cat)
                self.purge_passes(stale=[cat])

    def test_rule34_a_role_that_differs_from_the_stored_one_is_exit_4(self):
        fx = self.fx
        for cat in fx.cats:  # the first catalog, and one that is not
            cases = {"live": ("live_catalog", cat), "old state": ("old_states", [cat])}
            for name, (key, value) in cases.items():
                with self.subTest(name, cat=cat):
                    fx.cfg[key] = value
                    self.assert_stale(fx.refusal("stage"), cat)
                    self.purge_passes()
                    fx.cfg[key] = None if key == "live_catalog" else []

    def test_rule34_incomplete_coverage_is_exit_4_at_stage_and_at_purge(self):
        cov = Coverage(False, ["root R (not connected now)"])
        for op in ("stage", "purge"):
            with self.subTest(op=op):
                refusal = self.fx.refusal(op, coverage=cov)
                self.assertEqual(refusal.code, 4)
                self.assertIn("root R", refusal.message)

    def test_rule34_stored_roles_that_match_now_are_fresh(self):
        fx = self.fx
        fx.cfg["live_catalog"] = fx.cats[0]
        fx.cfg["old_states"] = [fx.cats[1]]
        fx.conn.execute('UPDATE "catalog" SET role = ? WHERE source_path = ?', ("live", fx.cats[0]))
        fx.conn.execute('UPDATE "catalog" SET role = ? WHERE source_path = ?', ("old_state", fx.cats[1]))
        self.assertIsNone(fx.refusal("stage"))


if __name__ == "__main__":
    unittest.main()
