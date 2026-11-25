import csv
import hashlib
import os
import tempfile
import threading
import unittest
from unittest import mock

from lrclean import paths, reject_archive
from lrclean.mover_files import staged_path
from lrclean.mover_journal import Journal, entries, read_journal, write_results
from lrclean.mover_list import ListRow
from lrclean.mover_seams import Folders, WriteRefused
from lrclean.mover_stage import StageOutcome, run_stage
from lrclean.mover_undo import RowResult, run_undo
from mover_world import BATCH, NOW, World

MTIME = 1_700_000_000_000_000_000
START = {"list_sha256": "a" * 64, "approved_sha256": "a" * 64, "scans": [["catalogs", 3]]}
NEXT_BATCH = "20260101T000001Z"


class StageCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.world = World(tmp.name)
        self.ctx = self.world.ctx()
        self.opened = 0

    def add_row(self, number, extras=("xmp",), folder="shoot-a", side_folder="") -> ListRow:
        stamp = MTIME + number * 10**7  # whole multiples of 100 ns, as the file system keeps them
        reject = self.world.add(f"{folder}/IMG_{number:04d}.ARW", b"raw-data-%d" % number, stamp)
        sides = [self.world.add(f"{side_folder or folder}/IMG_{number:04d}.{ext}", b"%s-%d" % (ext.encode(), number),
                                stamp + (i + 1) * 10**5) for i, ext in enumerate(extras)]
        for path in (reject, *sides):
            self.world.archive(path)
        rel = os.path.relpath(reject, self.world.photo_root)
        dests = tuple(os.path.join(root, rel) for root in self.world.archive_roots)
        return ListRow(reject, os.stat(reject).st_size, stamp, tuple(sides),
                       sum(os.stat(side).st_size for side in sides), dests, "")

    def journal_path(self, batch=BATCH) -> str:
        return os.path.join(self.world.base, "work", "journal", f"mover-{batch}.jsonl")

    def lines(self) -> list[dict]:
        return read_journal(self.journal_path(), self.ctx)

    def stage(self, rows, *, ctx=None, apply=True, stall=1000.0, shared=()) -> StageOutcome:
        ctx = ctx or self.ctx
        evidence = reject_archive.load_evidence(os.path.join(self.world.base, "out"), self.world.archive_roots,
                                                stat=self.world.stat)
        return run_stage(rows, evidence=evidence, ctx=ctx, journal=Journal(self.journal_path(ctx.batch), ctx),
                         start=START, stall_seconds=stall, apply=apply,
                         shared_paths=frozenset(paths.norm(path) for path in shared))

    def with_seams(self, **seams) -> None:
        self.ctx = self.ctx._replace(seams=self.ctx.seams._replace(**seams))

    def hook_stat(self, path: str, error=None) -> None:
        """`stat` of `path` raises `error`; with no error it answers as if a file existed there."""
        real = self.ctx.seams.stat

        def stat(target):
            if target != path:
                return real(target)
            if error:
                raise error
            return real(self.world.base)
        self.with_seams(stat=stat)

    def hook_rename(self, fail=(), after=None) -> list:
        """Count every rename; the calls numbered in `fail` raise; `after(n, src, dst)` runs after a good one."""
        calls: list = []

        def rename(src, dst):
            calls.append((src, dst))
            if len(calls) in fail:
                raise OSError("rename refused")
            self.world.rename(src, dst)
            if after:
                after(len(calls), src, dst)
        self.with_seams(rename=rename)
        return calls

    def hook_opener(self, on_open) -> None:
        real = self.ctx.seams.opener

        def opener(path):
            if path != self.journal_path():  # the journal reads its own file at its first line
                self.opened += 1
            on_open(path)
            return real(path)
        self.with_seams(opener=opener)

    def staged(self, path: str, batch=BATCH) -> str:
        return staged_path(self.world.base, batch, path)

    def tree(self) -> list:
        found = []
        for folder, dirs, names in os.walk(self.world.tmp):
            found += [(os.path.join(folder, name), None) for name in dirs]
            found += [(os.path.join(folder, name), os.stat(os.path.join(folder, name)).st_size,
                       os.stat(os.path.join(folder, name)).st_mtime_ns) for name in names]
        return sorted(found, key=str)

    def read(self, path: str) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    def assert_unmoved(self, row: ListRow):
        for path in (row.source, *row.sidecars):
            self.assertTrue(os.path.exists(path), path)
            self.assertFalse(os.path.exists(self.staged(path)), path)

    def assert_staged(self, row: ListRow):
        for path in (row.source, *row.sidecars):
            self.assertFalse(os.path.exists(path), path)
            self.assertTrue(os.path.exists(self.staged(path)), path)

    def steps(self) -> list[tuple]:
        return [(line["event"], line.get("op", ""), os.path.basename(line.get("original", line.get("file", ""))))
                for line in self.lines()]


class StageRowTests(StageCase):
    def test_rule_37_a_row_is_staged_reject_first_with_the_file_index_kept(self):
        row = self.add_row(1)
        inodes = [os.stat(path).st_ino for path in (row.source, *row.sidecars)]
        contents = [self.read(path) for path in (row.source, *row.sidecars)]
        calls = self.hook_rename()
        outcome = self.stage([row])
        self.assertEqual(outcome, StageOutcome([RowResult(0, row.source, "staged", "", (row.source, *row.sidecars))],
                                               False, False))
        self.assert_staged(row)
        self.assertEqual(calls, [(p, self.staged(p)) for p in (row.source, *row.sidecars)])
        staged = [self.staged(p) for p in (row.source, *row.sidecars)]
        self.assertEqual([os.stat(p).st_ino for p in staged], inodes)
        self.assertEqual([self.read(p) for p in staged], contents)

    def test_rule_39_the_end_line_has_the_staged_files_size_time_and_index(self):
        row = self.add_row(1)
        self.stage([row])
        ends = [line for line in self.lines() if line["event"] == "end"]
        for end, path in zip(ends, (row.source, *row.sidecars)):
            st = os.stat(self.staged(path))
            self.assertEqual((end["op"], end["state"], end["original"], end["bytes"], end["mtime_ns"],
                              end["file_index"]), ("stage", "staged", path, st.st_size, st.st_mtime_ns, st.st_ino))

    def test_rule_39_the_first_line_is_start_with_the_list_the_approval_and_the_scans(self):
        self.stage([self.add_row(1)])
        self.assertEqual(self.lines()[0], {"event": "start", "at": NOW, "op": "stage", "batch": BATCH, **START})

    def test_rule_39_the_journal_holds_the_begin_before_the_rename_and_family_on_the_first_only(self):
        row = self.add_row(1, ("xmp", "dop"))
        twinned = []  # rows 2 and 3 have a twin, one for each kind of list
        for number, kind in ((2, "reject"), (3, "copy")):
            twin = self.world.add(f"shoot-b/IMG_{number:04d}_twin.ARW", b"raw-data-%d" % number, MTIME)
            twinned.append(self.add_row(number, ())._replace(twin=twin, kind=kind))
        seen = []
        real = self.world.rename

        def rename(src, dst):
            seen.append(self.lines()[-1])
            real(src, dst)
        self.with_seams(rename=rename)
        self.stage([row, *twinned])
        first, second, third, *with_twin = seen
        for line, item, number in zip(with_twin, twinned, (2, 3)):
            with self.subTest(kind=item.kind):
                sha = hashlib.sha256(b"raw-data-%d" % number).hexdigest()
                self.assertEqual((line["kind"], line["twin"], line["twin_sha256"], line["sha256"]),
                                 (item.kind, item.twin, sha, sha))
        st = os.stat(self.staged(row.source))
        sha = self.lines()[1]["sha256"]
        self.assertEqual(first["family"], 3)
        self.assertEqual(first, {"event": "begin", "at": NOW, "op": "stage", "row": 0, "kind": "reject",
                                 "original": row.source, "staged": self.staged(row.source), "bytes": st.st_size,
                                 "mtime_ns": st.st_mtime_ns, "file_index": st.st_ino, "sha256": sha,
                                 "dests": [[dest, sha] for dest in row.dests], "twin": "", "twin_sha256": "",
                                 "family": 3})
        self.assertEqual([(line["event"], line["kind"], "family" in line) for line in (second, third)],
                         [("begin", "sidecar", False)] * 2)

    def test_rule_39_a_row_that_is_not_the_first_carries_its_own_family(self):
        first, second = self.add_row(1), self.add_row(2, ("xmp", "dop"))
        self.stage([first, second])
        families = [(line["row"], line.get("family")) for line in self.lines() if line["event"] == "begin"]
        self.assertEqual(families, [(0, 2), (0, None), (1, 3), (1, None), (1, None)])

    def test_rule_39_an_existing_journal_or_staging_folder_raises_before_any_write(self):
        def journal(world):
            os.makedirs(os.path.dirname(self.journal_path()))
            with open(self.journal_path(), "wb") as handle:
                handle.write(b"{}\n")

        def staging(world):
            os.makedirs(os.path.join(world.base, "work", "staging", BATCH))

        def unreadable(where):  # a `stat` that cannot tell (not "not found") fails closed
            return lambda world: self.hook_stat(where(world), PermissionError("denied"))

        for name, make in (("journal", journal), ("staging", staging),
                           ("journal_unreadable", unreadable(lambda world: self.journal_path())),
                           ("staging_unreadable",
                            unreadable(lambda world: os.path.join(world.base, "work", "staging", BATCH)))):
            for apply in (True, False):
                with self.subTest(name=name, apply=apply), tempfile.TemporaryDirectory() as tmp:
                    self.world = World(tmp)
                    self.ctx = self.world.ctx()
                    rows = [self.add_row(1)]
                    make(self.world)
                    before = self.tree()
                    with self.assertRaises(ValueError):
                        self.stage(rows, apply=apply)
                    self.assertEqual(self.tree(), before)

    def test_rule_45_only_the_named_files_move(self):
        row = self.add_row(1)
        jpeg = self.world.add("shoot-a/IMG_0001.JPG", b"jpeg", MTIME)
        other = self.world.add("shoot-a/IMG_0001.dng.xmp", b"other", MTIME)
        self.stage([row])
        self.assert_staged(row)
        self.assertEqual([self.read(jpeg), self.read(other)], [b"jpeg", b"other"])
        self.assertEqual(sorted(self.world.listing()), sorted([jpeg, other]))


class HoldTests(StageCase):
    def test_rule_36_a_row_held_by_its_check_stays_as_it_was_and_the_others_go_on(self):
        rows = [self.add_row(1), self.add_row(2), self.add_row(3)]
        stamp = os.stat(rows[1].dests[0]).st_mtime_ns
        with open(rows[1].dests[0], "wb") as handle:
            handle.write(b"x" * rows[1].bytes)  # the same size and time, other content
        os.utime(rows[1].dests[0], ns=(stamp, stamp))
        outcome = self.stage(rows)
        self.assertEqual([(r.index, r.state, r.reason, r.files) for r in outcome.results],
                         [(0, "staged", "", (rows[0].source, *rows[0].sidecars)),
                          (1, "held", "archive_mismatch", (rows[1].dests[0],)),
                          (2, "staged", "", (rows[2].source, *rows[2].sidecars))])
        self.assert_unmoved(rows[1])
        self.assert_staged(rows[2])
        held, = [line for line in self.lines() if line["event"] == "held"]
        self.assertEqual({k: held[k] for k in ("op", "row", "source", "kind", "reason", "file")},
                         {"op": "stage", "row": 1, "source": rows[1].source, "kind": "reject",
                          "reason": "archive_mismatch", "file": rows[1].dests[0]})

    def test_rule_53_a_copy_row_is_held_or_staged_as_a_reject_row_is_and_every_record_says_its_kind(self):
        for kind in ("reject", "copy"):
            with self.subTest(kind), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                other = os.path.join(self.world.archive_roots[0], "other.arw")
                wrong_dest = self.add_row(1)
                wrong_dest = wrong_dest._replace(dests=(wrong_dest.dests[0], other), kind=kind)
                wrong_twin = self.add_row(2)._replace(kind=kind, twin=self.world.add("shoot-b/twin.ARW", b"x", MTIME))
                taken = self.add_row(3)._replace(kind=kind)
                self.world.ids[taken.source] = (99, None)  # on another volume than the tool's folder
                no_twin = self.add_row(4)._replace(kind=kind)
                outcome = self.stage([wrong_dest, wrong_twin, taken, no_twin])
                self.assertEqual([(r.state, r.reason) for r in outcome.results],
                                 [("held", "no_archive_copy"), ("held", "twin_mismatch"),
                                  ("held", "other_volume"), ("staged", "")])
                self.assertEqual([r.source for r in outcome.results[:3]],
                                 [wrong_dest.source, wrong_twin.source, taken.source])
                lines = self.lines()
                self.assertEqual([(h["source"], h["kind"]) for h in lines if h["event"] == "held"],
                                 [(wrong_dest.source, kind), (wrong_twin.source, kind), (taken.source, kind)])
                self.assertEqual([line["kind"] for line in lines if line["event"] == "begin"], [kind, "sidecar"])
                results = os.path.join(self.world.base, "out", f"stage-{BATCH}.csv")
                write_results(results, "stage", lines, self.ctx)
                with open(results, encoding="utf-8-sig", newline="") as handle:
                    shown = [(cells[0], cells[1]) for cells in list(csv.reader(handle))[1:]]
                self.assertEqual(sorted(shown), sorted([(no_twin.source, kind), (no_twin.sidecars[0], "sidecar"),
                                                        (wrong_dest.source, kind), (wrong_twin.source, kind),
                                                        (taken.source, kind)]))

    def test_rule_36_a_sidecar_whose_archive_copy_fails_is_held_with_the_kind_sidecar(self):
        row = self.add_row(1)
        rel = os.path.relpath(row.sidecars[0], self.world.photo_root)
        copy = os.path.join(self.world.archive_roots[0], rel)
        stamp = os.stat(copy).st_mtime_ns
        with open(copy, "wb") as handle:
            handle.write(b"x" * os.stat(row.sidecars[0]).st_size)  # the same size and time, other content
        os.utime(copy, ns=(stamp, stamp))
        outcome = self.stage([row])
        self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                         [("held", "archive_mismatch", (copy,))])
        self.assert_unmoved(row)
        held, = [line for line in self.lines() if line["event"] == "held"]
        self.assertEqual({k: held[k] for k in ("kind", "reason", "file")},
                         {"kind": "sidecar", "reason": "archive_mismatch", "file": copy})

    def test_rule_36_two_rows_listing_one_sidecar_are_both_held_and_a_third_is_staged(self):
        first, second, third = self.add_row(1), self.add_row(2), self.add_row(3)
        second = second._replace(sidecars=first.sidecars, sidecar_bytes=first.sidecar_bytes)
        outcome = self.stage([first, second, third], shared=first.sidecars)
        self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                         [("held", "shared_sidecar", (first.sidecars[0],)),
                          ("held", "shared_sidecar", (first.sidecars[0],)),
                          ("staged", "", (third.source, *third.sidecars))])
        self.assert_unmoved(first)
        self.assertTrue(os.path.exists(second.source))
        self.assertFalse(os.path.exists(self.staged(second.source)))
        self.assert_staged(third)
        kinds = [(h["row"], h["kind"], h["file"]) for h in self.lines() if h["event"] == "held"]
        self.assertEqual(kinds, [(0, "sidecar", first.sidecars[0]), (1, "sidecar", first.sidecars[0])])

    def test_rule_36_a_sidecar_shared_with_a_row_that_is_not_passed_in_holds_the_passed_row(self):
        first, second = self.add_row(1), self.add_row(2)
        listed = first.sidecars[0].upper()  # the caller's set holds the normalised path, the row its own spelling
        outcome = self.stage([first, second], shared=[listed])
        self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                         [("held", "shared_sidecar", (first.sidecars[0],)), ("staged", "", (second.source, *second.sidecars))])
        self.assert_unmoved(first)
        self.assert_staged(second)

    def test_rule_36_a_shared_row_is_held_before_its_check_and_reads_no_content(self):
        first = self.add_row(1)
        with open(first.dests[0], "wb") as handle:
            handle.write(b"x" * first.bytes)  # its check would find another content
        self.hook_opener(lambda path: None)
        outcome = self.stage([first], shared=first.sidecars)
        self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                         [("held", "shared_sidecar", (first.sidecars[0],))])
        self.assertEqual(self.opened, 0)
        self.assert_unmoved(first)

    def test_rule_37_another_volume_holds_the_row_whichever_file_is_on_it(self):
        for which in (0, 1):
            with self.subTest(file=which), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                rows = [self.add_row(1), self.add_row(2), self.add_row(3)]
                bad = (rows[1].source, *rows[1].sidecars)[which]
                self.world.ids[bad] = (99, None)
                outcome = self.stage(rows)
                self.assertEqual([(r.state, r.reason) for r in outcome.results],
                                 [("staged", ""), ("held", "other_volume"), ("staged", "")])
                self.assertEqual(outcome.results[1].files, (bad,))
                self.assert_unmoved(rows[1])
                self.assert_staged(rows[2])

    def test_rule_40_a_link_in_a_folder_above_the_reject_or_a_sidecar_holds_the_row_before_anything_moves(self):
        for which in (0, 1):
            with self.subTest(file=which), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                rows = [self.add_row(1), self.add_row(2, folder="shoot-b" if which == 0 else "shoot-a",
                                                      side_folder="" if which == 0 else "shoot-c"),
                        self.add_row(3)]
                bad = (rows[1].source, *rows[1].sidecars)[which]
                self.world.links.add(self.world.path(os.path.relpath(os.path.dirname(bad), self.world.photo_root)))
                outcome = self.stage(rows)
                self.assertEqual([(r.state, r.reason) for r in outcome.results],
                                 [("staged", ""), ("held", "parent_link"), ("staged", "")])
                self.assertEqual(outcome.results[1].files, (bad,))
                self.assert_unmoved(rows[1])
                self.assert_staged(rows[0])
                self.assert_staged(rows[2])

    def test_rule_40_a_link_higher_than_the_parent_folder_holds_the_row_in_a_dry_run_too(self):
        for apply in (True, False):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                rows = [self.add_row(1, folder="shoot-b"), self.add_row(2, folder="shoot-a/inner"),
                        self.add_row(3, folder="shoot-c")]
                self.world.links.add(self.world.path("shoot-a"))  # a folder above the second row's parent
                before = self.tree()
                outcome = self.stage(rows, apply=apply)
                good = "staged" if apply else "would_stage"
                self.assertEqual([(r.state, r.reason) for r in outcome.results],
                                 [(good, ""), ("held", "parent_link"), (good, "")])
                self.assertEqual(outcome.results[1].files, (rows[1].source,))
                self.assert_unmoved(rows[1])
                if apply:
                    self.assert_staged(rows[0])
                    self.assert_staged(rows[2])
                else:
                    self.assertEqual(self.tree(), before)  # a dry run writes nothing

    def test_rule_37_a_staged_path_that_exists_or_cannot_be_read_holds_the_row_and_is_never_replaced(self):
        for which, error in ((0, None), (1, None), (0, PermissionError("denied")), (1, PermissionError("denied"))):
            with self.subTest(file=which, error=error), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                rows = [self.add_row(1), self.add_row(2), self.add_row(3)]
                original = (rows[1].source, *rows[1].sidecars)[which]
                # a file at the staged path outside the batch's folder (which an existing folder would refuse),
                # or a `stat` that cannot tell (not "not found"): fail closed
                self.hook_stat(self.staged(original), error)
                calls = self.hook_rename()
                outcome = self.stage(rows)
                self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results[:2]],
                                 [("staged", "", (rows[0].source, *rows[0].sidecars)),
                                  ("held", "staging_exists", (original,))])
                self.assertEqual(outcome.results[2].state, "staged")
                self.assertEqual(len(calls), 4)  # rows 0 and 2 only: nothing of row 1 moved
                self.assert_unmoved(rows[1])
                self.assert_staged(rows[2])


class FailureTests(StageCase):
    def test_rule_37_a_failed_rename_renames_back_what_moved_and_the_run_goes_on(self):
        for fail_on, expected in ((2, [("begin", "stage", "IMG_0001.ARW"), ("end", "stage", "IMG_0001.ARW"),
                                       ("begin", "stage", "IMG_0001.xmp"),
                                       ("begin", "restore", "IMG_0001.ARW"), ("end", "restore", "IMG_0001.ARW"),
                                       ("held", "stage", "IMG_0001.xmp")]),
                                  (3, [("begin", "stage", "IMG_0001.ARW"), ("end", "stage", "IMG_0001.ARW"),
                                       ("begin", "stage", "IMG_0001.xmp"), ("end", "stage", "IMG_0001.xmp"),
                                       ("begin", "stage", "IMG_0001.dop"),
                                       ("begin", "restore", "IMG_0001.xmp"), ("end", "restore", "IMG_0001.xmp"),
                                       ("begin", "restore", "IMG_0001.ARW"), ("end", "restore", "IMG_0001.ARW"),
                                       ("held", "stage", "IMG_0001.dop")])):
            with self.subTest(fail_on=fail_on), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                rows = [self.add_row(1, ("xmp", "dop")), self.add_row(2)]
                self.hook_rename(fail={fail_on})
                outcome = self.stage(rows)
                failed = (rows[0].source, *rows[0].sidecars)[fail_on - 1]
                self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                                 [("held", "rename_failed", (failed,)), ("staged", "", (rows[1].source, *rows[1].sidecars))])
                self.assertFalse(outcome.stopped)
                self.assert_unmoved(rows[0])
                self.assert_staged(rows[1])
                self.assertEqual(self.steps()[1:len(expected) + 1], expected)

    def test_rule_37_a_rename_back_that_fails_stops_the_run_where_it_is(self):
        # calls: 1 reject, 2 xmp, 3 dop (fails), then the way back: 4 xmp, 5 reject
        for back_fails, staged_names in ((4, ("IMG_0001.ARW", "IMG_0001.xmp")), (5, ("IMG_0001.ARW",))):
            with self.subTest(back_fails=back_fails), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                rows = [self.add_row(1, ("xmp", "dop")), self.add_row(2)]
                calls = self.hook_rename(fail={3, back_fails})
                outcome = self.stage(rows)
                self.assertEqual([(r.state, r.reason) for r in outcome.results], [("held", "rename_failed")])
                self.assertTrue(outcome.stopped)
                self.assertEqual(len(calls), back_fails)
                for name in ("IMG_0001.ARW", "IMG_0001.xmp", "IMG_0001.dop"):
                    original = os.path.join(os.path.dirname(rows[0].source), name)
                    self.assertEqual((os.path.exists(original), os.path.exists(self.staged(original))),
                                     (name not in staged_names, name in staged_names), name)
                self.assert_unmoved(rows[1])
                tail = self.steps()[-2:]
                self.assertEqual(tail[0][:2], ("begin", "restore"))
                self.assertEqual(tail[1][0], "held")

    def test_rule_37_the_staged_file_must_be_readable_and_equal_the_source_in_size_time_and_file_index(self):
        def resized(path):
            st = os.stat(path)
            with open(path, "ab") as handle:
                handle.write(b"more")
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # only the size differs

        def retimed(path):
            st = os.stat(path)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))

        def reindexed(path):
            self.world.ids[path] = (None, os.stat(path).st_ino + 7)

        broken = set()

        def unreadable(path):  # the staged file's `stat` fails right after the rename
            broken.add(path)

        for name, change in (("size", resized), ("time", retimed), ("file_index", reindexed), ("stat", unreadable)):
            with self.subTest(field=name), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                real = self.ctx.seams.stat

                def stat(path, real=real):
                    if path in broken:
                        raise OSError("gone")
                    return real(path)
                self.with_seams(stat=stat)
                broken.clear()
                row = self.add_row(1)
                calls = self.hook_rename(after=lambda n, src, dst: change(dst) if n == 1 else None)
                outcome = self.stage([row])
                self.assertEqual([(r.state, r.reason) for r in outcome.results], [("held", "rename_failed")])
                self.assertEqual(outcome.results[0].files, (row.source,))
                held, = [line for line in self.lines() if line["event"] == "held"]
                self.assertEqual((held["file"], held["kind"]), (row.source, "reject"))
                self.assertEqual(calls, [(row.source, self.staged(row.source)), (self.staged(row.source), row.source)])
                self.assert_unmoved(row)

    def test_rule_37_a_file_changed_in_size_time_or_file_index_between_renames_goes_back_as_changed(self):
        def resized(path):
            st = os.stat(path)
            with open(path, "ab") as handle:
                handle.write(b"edited")
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # only the size differs

        def retimed(path):
            st = os.stat(path)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))

        def reindexed(path):
            self.world.ids[path] = (None, os.stat(path).st_ino + 7)

        for name, change in (("size", resized), ("time", retimed), ("file_index", reindexed)):
            with self.subTest(field=name), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                row = self.add_row(1)
                calls = self.hook_rename(after=lambda n, src, dst: change(row.sidecars[0]) if n == 1 else None)
                outcome = self.stage([row])
                self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                                 [("held", "changed", (row.sidecars[0],))])
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[1], (self.staged(row.source), row.source))
                self.assert_unmoved(row)

    def test_rule_39_a_row_rolled_back_journals_each_restore_with_the_facts_of_the_original(self):
        row = self.add_row(1, ("xmp", "dop"))
        files = (row.source, *row.sidecars)
        facts = {path: (os.stat(path), hashlib.sha256(self.read(path)).hexdigest()) for path in files}
        self.hook_rename(fail={3})
        self.stage([row])
        restores = [line for line in self.lines() if line.get("op") == "restore"]
        self.assertEqual(len(restores), 4)
        for path, begin, end in zip(reversed(files[:2]), restores[0::2], restores[1::2]):
            st, sha = facts[path]
            base = {"at": NOW, "op": "restore", "row": 0, "original": path, "bytes": st.st_size,
                    "mtime_ns": st.st_mtime_ns, "file_index": st.st_ino}
            self.assertEqual(begin, {"event": "begin", "kind": "reject" if path == row.source else "sidecar",
                                     "staged": self.staged(path), "sha256": sha, **base})
            self.assertEqual(end, {"event": "end", "state": "restored", **base})

    def test_rule_37_undo_reads_a_journal_after_a_rolled_back_row_and_after_a_failed_rename_back(self):
        # calls: 1 reject, 2 xmp, 3 dop (fails), then the way back: 4 xmp, 5 reject
        for fail, expected in (({3}, ["skipped"]), ({3, 4}, ["restored"]), ({3, 5}, ["restored"])):
            with self.subTest(fail=sorted(fail)), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                row = self.add_row(1, ("xmp", "dop"))
                listing = self.world.listing()
                self.hook_rename(fail=fail)
                self.stage([row])
                self.hook_rename()  # the undo's renames go through
                undone = run_undo(entries(self.lines()), ctx=self.ctx, journal=Journal(self.journal_path(), self.ctx),
                                  start={}, apply=True)
                self.assertEqual([r.state for r in undone], expected)
                self.assertEqual(self.world.listing(), listing)
                self.assert_unmoved(row)

    def test_rule_37_a_file_edited_during_hashing_is_changed_and_nothing_moves(self):
        row = self.add_row(1)
        edited = []

        def edit(path):
            if path == row.sidecars[0] and not edited:
                edited.append(True)
                os.utime(row.source, ns=(MTIME, MTIME + 10**9))
        self.hook_opener(edit)
        calls = self.hook_rename()
        outcome = self.stage([row])
        self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                         [("held", "changed", (row.source,))])
        self.assertEqual(calls, [])
        self.assert_unmoved(row)


class DryRunAndStallTests(StageCase):
    def test_rule_42_a_dry_run_changes_nothing_and_reads_no_content(self):
        rows = [self.add_row(1), self.add_row(2), self.add_row(3)]
        self.world.ids[rows[1].source] = (99, None)
        self.hook_stat(self.staged(rows[2].sidecars[0]))  # a file already at a staged path
        self.hook_opener(lambda path: None)
        calls = self.hook_rename()
        before = self.tree()
        outcome = self.stage(rows, apply=False)
        self.assertEqual([(r.state, r.reason, r.files) for r in outcome.results],
                         [("would_stage", "", (rows[0].source, *rows[0].sidecars)),
                          ("held", "other_volume", (rows[1].source,)),
                          ("held", "staging_exists", (rows[2].sidecars[0],))])
        self.assertEqual((self.tree(), calls, self.opened), (before, [], 0))

    def test_rule_43_a_stall_ends_the_run_before_any_rename_and_a_dry_run_writes_no_line(self):
        for apply in (True, False):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                row = self.add_row(1)
                now, workers, release = [0.0], [], threading.Event()
                real = self.ctx.seams.stat

                def stat(path, real=real, now=now, workers=workers, release=release, source=row.source):
                    if path == source and not workers:
                        workers.append(threading.current_thread())
                        now[0] += 100.0  # the call makes no progress for 100 s
                        release.wait(20)
                    return real(path)
                self.with_seams(stat=stat, clock=lambda: now[0])
                calls = self.hook_rename()
                before = self.world.listing()
                outcome = self.stage([row], apply=apply, stall=10.0)
                self.assertEqual((outcome.stalled, outcome.stopped, outcome.results, self.world.aborted),
                                 (True, False, [], [5]))
                release.set()
                workers[0].join(20)
                self.assertFalse(workers[0].is_alive())
                written = [(line["event"], line.get("seconds")) for line in self.lines()] if apply else []
                self.assertEqual(written, [("start", None), ("stalled", 100.0)] if apply else [])
                self.assertEqual(os.path.exists(self.journal_path()), apply)
                self.assertEqual((calls, self.world.listing()), ([], before))
                self.assertFalse(os.path.exists(os.path.join(self.world.base, "work", "staging")))


    def test_rule_43_a_call_that_returns_after_the_stalled_line_is_followed_by_no_rename(self):
        for late in ("makedirs", "begin"):
            with self.subTest(late=late), tempfile.TemporaryDirectory() as tmp:
                self.world = World(tmp)
                self.ctx = self.world.ctx()
                row = self.add_row(1)
                now, workers, release = [0.0], [], threading.Event()
                seams = self.ctx.seams

                def held_late(real, hit, workers=workers, now=now, release=release):
                    def call(*args, **kwargs):
                        result = real(*args, **kwargs)
                        if hit(args) and not workers:
                            workers.append(threading.current_thread())
                            now[0] += 100.0  # the call makes no progress for 100 s
                            release.wait(20)
                        return result
                    return call

                def append(path, data, real=seams.append, workers=workers, release=release):
                    real(path, data)
                    if b'"stalled"' in data:  # the late call returns inside the `stalled` line's own append
                        release.set()
                        workers[0].join(20)
                self.with_seams(clock=lambda now=now: now[0], append=append)
                if late == "makedirs":
                    self.with_seams(makedirs=held_late(seams.makedirs, lambda args: "staging" in args[0]))
                else:
                    self.with_seams(append=held_late(append, lambda args: b'"begin"' in args[1]))
                calls = self.hook_rename()
                outcome = self.stage([row], stall=10.0)
                self.assertFalse(workers[0].is_alive())
                self.assertEqual((outcome.stalled, outcome.results, self.world.aborted), (True, [], [5]))
                self.assertEqual([line["event"] for line in self.lines()], ["start", "begin", "stalled"])
                self.assertEqual(calls, [])
                self.assert_unmoved(row)
                undone = run_undo(entries(self.lines()), ctx=self.world.ctx(), start={}, apply=False,
                                  journal=Journal(self.journal_path(), self.world.ctx()))
                self.assertEqual([r.state for r in undone], ["skipped"])


class OutsideAndRoundTripTests(StageCase):
    def test_rule_38_a_staged_path_outside_the_staging_folder_is_refused_before_any_rename(self):
        for where in ("elsewhere/x.arw", "base/work/staging-x/x.arw"):
            for bad_file in (0, 1):
                with self.subTest(path=where, file=bad_file), tempfile.TemporaryDirectory() as tmp:
                    self.world = World(tmp)
                    self.ctx = self.world.ctx()
                    row = self.add_row(1)
                    real = [self.staged(p) for p in (row.source, *row.sidecars)]
                    real[bad_file] = os.path.join(self.world.tmp, *where.split("/"))
                    calls = self.hook_rename()
                    with mock.patch("lrclean.mover_stage.staged_path", side_effect=real), \
                            self.assertRaises(WriteRefused):
                        self.stage([row])
                    self.assertEqual(calls, [])
                    self.assert_unmoved(row)
                    self.assertEqual([line["event"] for line in self.lines()], ["start"])

    def test_rule_38_a_later_file_outside_the_photo_root_is_refused_before_any_file_of_the_row_moves(self):
        row = self.add_row(1)
        other = self.world.add("shoot-b/IMG_0001.dop", b"dop-1", MTIME)
        self.world.archive(other)
        row = row._replace(sidecars=(*row.sidecars, other), sidecar_bytes=row.sidecar_bytes + os.stat(other).st_size)
        folders = Folders(self.world.base, os.path.dirname(row.source))  # holds the reject and its xmp only
        self.ctx = self.ctx._replace(folders=folders)
        calls = self.hook_rename()
        with self.assertRaises(WriteRefused):
            self.stage([row])
        self.assertEqual(calls, [])
        self.assert_unmoved(row)
        self.assertEqual([line["event"] for line in self.lines()], ["start"])

    def test_rule_38_an_unreadable_tool_folder_raises_value_error_before_any_write(self):
        row = self.add_row(1)
        self.hook_stat(self.world.base, PermissionError("denied"))
        calls = self.hook_rename()
        before = self.tree()
        for apply in (True, False):
            with self.subTest(apply=apply), self.assertRaises(ValueError):
                self.stage([row], apply=apply)
            self.assertEqual((self.tree(), calls), (before, []))

    def test_rule_37_stage_then_undo_restores_the_tree_and_a_second_stage_gives_the_same_paths(self):
        rows = [self.add_row(1, ("xmp", "dop")), self.add_row(2)]
        listing = self.world.listing()
        contents = {path: self.read(path) for path in listing}
        self.stage(rows)
        folder = os.path.dirname(rows[0].source)
        self.assertEqual(os.listdir(folder), [])
        undone = run_undo(entries(self.lines()), ctx=self.ctx, journal=Journal(self.journal_path(), self.ctx),
                          start={}, apply=True)
        self.assertEqual([r.state for r in undone], ["restored", "restored"])
        self.assertEqual(self.world.listing(), listing)
        self.assertEqual({path: self.read(path) for path in listing}, contents)
        second = self.world.ctx(NEXT_BATCH)
        outcome = self.stage(rows, ctx=second)
        self.assertEqual([r.state for r in outcome.results], ["staged", "staged"])
        for path in listing:
            self.assertEqual(self.staged(path, NEXT_BATCH).replace(NEXT_BATCH, BATCH), self.staged(path))
            self.assertTrue(os.path.exists(self.staged(path, NEXT_BATCH)))


if __name__ == "__main__":
    unittest.main()
