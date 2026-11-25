import csv
import json
import os
import tempfile
import unittest

from lrclean import mover_journal as mj
from lrclean.mover_seams import Stopped, WriteRefused
from mover_world import BATCH, World

GOOD = {"event": "start", "at": "t", "op": "stage", "batch": BATCH}
MARKER = b'{"event": "repaired"}\n'
HELD = {"op": "stage", "source": "h", "kind": "reject", "reason": "missing"}


def line(obj) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def begin(row, original, op="stage", **more):
    staged = original.replace("photos", "staging")
    return {"event": "begin", "op": op, "row": row, "kind": "reject", "original": original, "staged": staged,
            "bytes": 3, "mtime_ns": 7, "file_index": 9, "sha256": "aa", "dests": [["d1", "bb"], ["d2", "cc"]],
            "twin": "twin-" + original, "twin_sha256": "tt", **more}


def step(*args, **more):
    """The `begin` argument of run_step: the fields, without the event."""
    return {key: value for key, value in begin(*args, **more).items() if key != "event"}


def end(row, original, state, op="stage", **more):
    return {"event": "end", "op": op, "row": row, "kind": "reject", "original": original, "state": state, **more}


class JournalCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.world = World(tmp.name)
        self.ctx = self.world.ctx()
        self.path = os.path.join(self.world.base, "work", "journal", f"mover-{BATCH}.jsonl")

    def put(self, data: bytes) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "wb") as handle:
            handle.write(data)

    def add_raw(self, data: bytes) -> None:
        with open(self.path, "ab") as handle:
            handle.write(data)

    def file_lines(self) -> list[str]:
        with open(self.path, "rb") as handle:
            return handle.read().decode().splitlines()

    def spied(self, **names):
        calls = {name: [] for name in names}
        real = self.ctx.seams
        spies = {name: (lambda *a, _n=name, **k: calls[_n].append(a) or getattr(real, _n)(*a, **k)) for name in names}
        self.ctx = self.ctx._replace(seams=real._replace(**spies))
        return calls


class RunStepTest(JournalCase):
    def test_rule_39_begin_is_on_disk_before_the_step_and_end_after(self):
        src = self.world.add("shoot-a/IMG_0001.ARW", b"abc", 5)
        dst = os.path.join(self.world.base, "work", "staging", "IMG_0001.ARW")
        os.makedirs(os.path.dirname(dst))
        journal, inside = mj.Journal(self.path, self.ctx), []

        def do():
            inside.extend(self.file_lines())
            self.ctx.seams.rename(src, dst)
            return "moved"

        result = journal.run_step(step(0, src), do, lambda r: {"op": "stage", "row": 0, "state": "staged"})
        self.assertEqual(result, "moved")
        self.assertEqual([json.loads(text)["event"] for text in inside], ["begin"])
        self.assertEqual([json.loads(text)["event"] for text in self.file_lines()], ["begin", "end"])
        self.assertTrue(os.path.exists(dst))

    def test_rule_39_a_failed_step_leaves_begin_with_no_end(self):
        def do():
            raise OSError("rename failed")

        with self.assertRaises(OSError):
            mj.Journal(self.path, self.ctx).run_step(step(0, "x"), do, lambda r: {})
        self.assertEqual([json.loads(text)["event"] for text in self.file_lines()], ["begin"])

    def test_rule_39_family_is_on_the_first_begin_of_a_stage_row_only(self):
        journal = mj.Journal(self.path, self.ctx)
        for row, name in ((0, "a"), (0, "b"), (1, "c")):
            journal.run_step(step(row, name, family=2), lambda: 1, lambda r: {"state": "staged"})
        journal.run_step(step(0, "a", op="restore", family=2), lambda: 1, lambda r: {"state": "restored"})
        begins = [json.loads(text) for text in self.file_lines() if '"begin"' in text]
        self.assertEqual([b.get("family") for b in begins], [2, None, 2, None])


class ReadTest(JournalCase):
    def test_rule_39_a_torn_last_line_is_ignored(self):
        self.put(line(GOOD) + b'{"event": "be')
        self.assertEqual(mj.read_journal(self.path, self.ctx), [GOOD])

    def test_rule_39_a_write_after_a_torn_tail_adds_newline_marker_and_line(self):
        self.put(line(GOOD) + b'{"event": "be')
        journal = mj.Journal(self.path, self.ctx)
        journal.write("held", row=1, **HELD)
        text = self.file_lines()
        self.assertEqual(text[1:3], ['{"event": "be', '{"event": "repaired"}'])
        self.assertEqual(json.loads(text[3])["event"], "held")
        self.assertEqual([x["event"] for x in mj.read_journal(self.path, self.ctx)], ["start", "repaired", "held"])
        journal.write("held", row=2, **HELD)
        self.assertEqual([x["event"] for x in mj.read_journal(self.path, self.ctx)],
                         ["start", "repaired", "held", "held"])
        self.assertEqual(self.file_lines().count('{"event": "repaired"}'), 1)

    def test_rule_39_the_line_just_before_a_marker_is_ignored_whatever_it_holds(self):
        whole = begin(4, "never-ran")
        self.put(line(GOOD) + json.dumps(whole).encode() + b"\n" + MARKER + line(GOOD))
        self.assertEqual([x["event"] for x in mj.read_journal(self.path, self.ctx)], ["start", "repaired", "start"])

    def test_rule_39_a_torn_line_then_a_write_then_an_invalid_line_is_an_error(self):
        self.put(line(GOOD) + b'{"event": "be')
        mj.Journal(self.path, self.ctx).write("held", row=1, **HELD)
        self.add_raw(b"garbage\n")
        with self.assertRaises(mj.JournalError):
            mj.read_journal(self.path, self.ctx)

    def test_rule_39_an_invalid_line_in_the_middle_is_an_error(self):
        for bad in (b"garbage", b"[1]", b"7", b'{"at": "t"}', b'{"event": "teleport"}', b"", b"\xff\xfe"):
            with self.subTest(bad=bad):
                self.put(line(GOOD) + bad + b"\n" + line(GOOD))
                with self.assertRaises(mj.JournalError):
                    mj.read_journal(self.path, self.ctx)

    def test_rule_39_a_known_event_missing_a_field_or_holding_a_wrong_type_is_an_error(self):
        held = {"event": "held", "op": "stage", "row": 1, "source": "h", "kind": "reject", "reason": "missing"}
        without = lambda obj, name: {k: v for k, v in obj.items() if k != name}  # noqa: E731
        bad_lines = [without(held, name) for name in ("source", "kind", "reason", "op")]
        bad_lines += [without(begin(0, "a"), name) for name in ("row", "op", "original")]
        bad_lines += [without(end(0, "a", "staged"), name) for name in ("row", "original", "state")]
        bad_lines += [begin("0", "a"), dict(begin(0, "a"), original=5), end(0, "a", 7), dict(held, source=None)]
        bad_lines += [begin(0, "a", dests=dests) for dests in ("ab", 5, [1], [["d1"]], [["d1", "bb", "x"]])]
        for bad in bad_lines:
            with self.subTest(bad=bad):
                self.put(line(GOOD) + line(bad) + line(GOOD))
                with self.assertRaises(mj.JournalError):
                    mj.read_journal(self.path, self.ctx)

    def test_rule_39_a_write_to_a_complete_file_adds_no_marker_and_a_new_file_none(self):
        mj.Journal(self.path, self.ctx).write("start", op="stage", batch=BATCH)
        self.assertEqual(len(self.file_lines()), 1)
        mj.Journal(self.path, self.ctx).write("held", op="stage", row=0)
        self.assertEqual([json.loads(t)["event"] for t in self.file_lines()], ["start", "held"])

    def test_rule_39_each_line_carries_event_and_at(self):
        mj.Journal(self.path, self.ctx).write("held", op="stage", row=0)
        self.assertEqual(json.loads(self.file_lines()[0])["at"], self.world.now())


class EntriesTest(unittest.TestCase):
    def test_rule_40_a_restore_after_a_stage_gives_restored_in_place(self):
        lines = [begin(0, "a"), end(0, "a", "staged", staged="s-a", bytes=3), begin(1, "b"), end(1, "b", "staged"),
                 begin(0, "a", op="restore"), end(0, "a", "restored", op="restore")]
        self.assertEqual([(e.original, e.state) for e in mj.entries(lines)], [("a", "restored"), ("b", "staged")])

    def test_rule_41_a_purge_end_gives_purged(self):
        lines = [begin(0, "a"), end(0, "a", "staged"), begin(0, "a", op="purge"), end(0, "a", "purged", op="purge")]
        self.assertEqual([e.state for e in mj.entries(lines)], ["purged"])

    def test_rule_41_a_family_partly_purged_keeps_every_state_and_the_family(self):
        lines = [begin(0, "a", family=3), end(0, "a", "staged"), begin(0, "b"), end(0, "b", "staged"),
                 begin(0, "c"), end(0, "c", "staged"), begin(1, "d", family=1), end(1, "d", "staged"),
                 begin(0, "b", op="purge"), end(0, "b", "purged", op="purge")]
        got = mj.entries(lines)
        self.assertEqual([(e.original, e.state, e.family) for e in got],
                         [("a", "staged", 3), ("b", "purged", 3), ("c", "staged", 3), ("d", "staged", 1)])

    def test_rule_39_a_begin_with_no_end_is_open_and_its_fields_are_read(self):
        got = mj.entries([begin(2, "a", family=1), {"event": "held", "op": "stage", "row": 3}, {"event": "repaired"}])
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0].state, got[0].row, got[0].dests, got[0].sha256, got[0].bytes),
                         ("open_stage", 2, (("d1", "bb"), ("d2", "cc")), "aa", 3))
        self.assertEqual(got[0].twin, "twin-a")  # L10: one assertion per field
        self.assertEqual(got[0].twin_sha256, "tt")

    def test_rule_39_an_end_takes_the_staged_files_own_size_time_and_index(self):
        got = mj.entries([begin(0, "a"), end(0, "a", "staged", bytes=11, mtime_ns=12, file_index=13)])
        self.assertEqual((got[0].bytes, got[0].mtime_ns, got[0].file_index, got[0].sha256), (11, 12, 13, "aa"))

    def test_rule_39_an_unknown_state_or_an_end_with_no_begin_is_an_error(self):
        for bad in ([begin(0, "a"), end(0, "a", "vanished")], [begin(0, "a", op="teleport")],
                    [end(0, "a", "staged")], [begin(0, "a"), end(0, "b", "staged")],
                    [begin(0, "a"), end(1, "a", "staged")]):
            with self.subTest(bad=bad), self.assertRaises(mj.JournalError):
                mj.entries(bad)


class PlaceTest(JournalCase):
    def entry(self, state):
        orig = self.world.path("shoot-a/IMG_0001.ARW")
        staged = os.path.join(self.world.base, "work", "staging", "IMG_0001.ARW")
        return mj.Entry(0, "reject", orig, staged, 3, 7, 9, "aa", (), "", "", 1, state), orig, staged

    def test_rule_40_place_decides_from_the_original_and_the_staged_file(self):
        # (original exists, staged exists) -> answer, per begin kind; L10: a case for each combination
        table = {"open_stage": {(1, 0): "not_done", (0, 1): "done", (1, 1): "unplaced", (0, 0): "unplaced"},
                 "open_purge": {(1, 0): "done", (0, 1): "not_done", (1, 1): "not_done", (0, 0): "done"},
                 "open_restore": {(1, 0): "done", (0, 1): "not_done", (1, 1): "unplaced", (0, 0): "unplaced"}}
        for state, cases in table.items():
            for (orig_there, staged_there), answer in cases.items():
                with self.subTest(state=state, original=orig_there, staged=staged_there):
                    entry, orig, staged = self.entry(state)
                    for path, there in ((orig, orig_there), (staged, staged_there)):
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        if there:
                            with open(path, "wb") as handle:
                                handle.write(b"abc")
                        elif os.path.exists(path):
                            os.remove(path)
                    self.assertEqual(mj.place(entry, self.ctx), answer)

    def test_rule_40_a_stat_that_fails_is_unplaced(self):
        def broken(path):
            raise PermissionError(path)

        entry, _orig, _staged = self.entry("open_purge")
        ctx = self.ctx._replace(seams=self.ctx.seams._replace(stat=broken))
        self.assertEqual(mj.place(entry, ctx), "unplaced")

    def test_rule_40_a_stat_that_fails_for_one_of_the_two_files_is_unplaced(self):
        # L10: one case per fact. The other file is where a stage would find it, so only the failure can answer.
        for failing in ("original", "staged"):
            with self.subTest(failing=failing):
                entry, orig, staged = self.entry("open_stage")
                os.makedirs(os.path.dirname(orig), exist_ok=True)
                os.makedirs(os.path.dirname(staged), exist_ok=True)
                with open(orig if failing == "staged" else staged, "wb") as handle:
                    handle.write(b"abc")
                real = self.ctx.seams.stat

                def stat(path, bad=orig if failing == "original" else staged):
                    if path == bad:
                        raise PermissionError(path)
                    return real(path)

                ctx = self.ctx._replace(seams=self.ctx.seams._replace(stat=stat))
                self.assertEqual(mj.place(entry, ctx), "unplaced")

    def test_rc108_an_open_stage_step_on_a_staged_path_over_260_characters_is_placed_through_the_prefix(self):
        # a crash leaves `begin` without `end`: a path the stat cannot read unprefixed would read as absent
        folders = [chr(ord("a") + n) * 40 for n in range(6)]
        entry, _orig, _staged = self.entry("open_stage")
        staged = os.path.join(self.world.base, "work", "staging", *folders, "IMG_0001.ARW")
        self.assertGreaterEqual(len(staged), 260)
        long_dir = os.path.dirname(staged)
        os.makedirs(long_dir)
        with open(staged, "wb") as handle:
            handle.write(b"abc")
        real = self.ctx.seams.stat

        def stat(path):
            if not path.startswith("\\\\?\\") and len(path) >= 260:
                raise FileNotFoundError(path)
            return real(path.removeprefix("\\\\?\\"))

        ctx = self.ctx._replace(seams=self.ctx.seams._replace(stat=stat))
        self.assertEqual(mj.place(entry._replace(staged=staged), ctx), "done")

    def test_rule_40_a_path_under_a_file_is_absent_not_unreadable(self):
        entry, orig, staged = self.entry("open_stage")
        os.makedirs(os.path.dirname(orig))
        with open(orig, "wb") as handle:
            handle.write(b"abc")
        real = self.ctx.seams.stat

        def stat(path):
            if path == staged:
                raise NotADirectoryError(path)
            return real(path)

        ctx = self.ctx._replace(seams=self.ctx.seams._replace(stat=stat))
        self.assertEqual(mj.place(entry, ctx), "not_done")


class ResultsTest(JournalCase):
    LINES = [begin(0, "a", family=2), end(0, "a", "staged", staged="s-a"),
             begin(0, "b"), end(0, "b", "staged", staged="s-b"),
             {"event": "held", "op": "stage", "row": 1, "source": "h1", "kind": "reject", "reason": "missing",
              "file": "h1"},
             begin(0, "b", op="purge", staged="s-b"), end(0, "b", "purged", op="purge"),
             {"event": "held", "op": "purge", "row": 2, "source": "h2", "kind": "sidecar",
              "reason": "family_incomplete", "file": "h2"},
             begin(3, "r"), end(3, "r", "staged"), begin(3, "r", op="restore"), end(3, "r", "restored", op="restore"),
             begin(4, "c"),  # a stage with no end
             begin(5, "p"), end(5, "p", "staged"), begin(5, "p", op="purge")]  # a purge with no end

    def results_path(self, op):
        return os.path.join(self.world.base, "out", f"{op}-{BATCH}.csv")

    def rows(self, op):
        mj.write_results(self.results_path(op), op, self.LINES, self.ctx)
        with open(self.results_path(op), encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))

    def test_rule_39_stage_results_hold_the_stage_entries_and_held_lines_only(self):
        got = self.rows("stage")
        self.assertEqual(got[0], ["source", "kind", "staged", "bytes", "sha256", "state", "reason"])
        self.assertEqual(got[1:], [["a", "reject", "s-a", "3", "aa", "staged", ""],
                                   ["c", "reject", "c", "3", "aa", "open_stage", ""],
                                   ["h1", "reject", "", "", "", "held", "missing"]])

    def test_rule_41_purge_results_hold_the_purge_entries_and_held_lines_only(self):
        got = self.rows("purge")
        self.assertEqual(got[1:], [["b", "reject", "s-b", "3", "aa", "purged", ""],
                                   ["p", "reject", "p", "3", "aa", "open_purge", ""],
                                   ["h2", "sidecar", "", "", "", "held", "family_incomplete"]])

    def test_rule_39_the_results_file_has_a_bom_and_crlf_line_ends(self):
        self.rows("stage")
        with open(self.results_path("stage"), "rb") as handle:
            raw = handle.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbfsource,kind,staged,bytes,sha256,state,reason\r\n"))
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))

    def test_rule_43_after_stop_the_results_are_not_written(self):
        calls = self.spied(write=1)
        self.ctx.stop.set()
        with self.assertRaises(Stopped):
            mj.write_results(self.results_path("stage"), "stage", self.LINES, self.ctx)
        self.assertEqual(calls["write"], [])

    def test_rule_38_a_results_path_outside_out_is_refused_before_the_seam(self):
        calls = self.spied(write=1)
        for bad in (os.path.join(self.world.base, "work", "x.csv"), os.path.join(self.world.tmp, "x.csv")):
            with self.subTest(bad=bad), self.assertRaises(WriteRefused):
                mj.write_results(bad, "stage", self.LINES, self.ctx)
        self.assertEqual(calls["write"], [])


class FoldersTest(JournalCase):
    def test_rule_38_a_journal_path_outside_the_journal_folder_is_refused_before_any_seam(self):
        calls = self.spied(append=1, makedirs=1, opener=1)
        for bad in (os.path.join(self.world.base, "work", "mover-x.jsonl"),
                    os.path.join(self.world.base, "out", "mover-x.jsonl")):
            with self.subTest(bad=bad), self.assertRaises(WriteRefused):
                mj.Journal(bad, self.ctx).write("start", op="stage", batch=BATCH)
        self.assertEqual(calls, {"append": [], "makedirs": [], "opener": []})

    def test_rule_43_after_stop_every_event_but_stalled_is_refused(self):
        journal = mj.Journal(self.path, self.ctx)
        journal.write("start", op="stage", batch=BATCH)
        self.ctx.stop.set()
        for event in ("start", "begin", "end", "held", "repaired"):
            with self.subTest(event=event), self.assertRaises(Stopped):
                journal.write(event)
        journal.write("stalled", seconds=121)
        self.assertEqual([json.loads(t)["event"] for t in self.file_lines()], ["start", "stalled"])

    def test_rule_39_write_refuses_an_unknown_event_and_a_field_named_at_before_any_write(self):
        calls = self.spied(append=1, makedirs=1, opener=1)
        for event, fields in (("teleport", {}), ("held", {"at": "t"})):
            with self.subTest(event=event, fields=fields), self.assertRaises(ValueError):
                mj.Journal(self.path, self.ctx).write(event, **fields)
        self.assertEqual(calls, {"append": [], "makedirs": [], "opener": []})

    def test_rule_43_after_stop_run_step_never_calls_the_step(self):
        self.ctx.stop.set()
        ran = []
        with self.assertRaises(Stopped):
            mj.Journal(self.path, self.ctx).run_step(step(0, "x"), lambda: ran.append(1), lambda r: {})
        self.assertEqual(ran, [])

    def test_rule_43_a_begin_append_that_returns_after_the_stop_is_followed_by_no_step(self):
        real = self.ctx.seams.append

        def append(path, data):
            real(path, data)
            self.ctx.stop.set()  # the stall watch fired while this append was in flight
        self.ctx = self.ctx._replace(seams=self.ctx.seams._replace(append=append))
        ran = []
        with self.assertRaises(Stopped):
            mj.Journal(self.path, self.ctx).run_step(step(0, "x"), lambda: ran.append(1), lambda r: {})
        self.assertEqual(ran, [])
        self.assertEqual([json.loads(text)["event"] for text in self.file_lines()], ["begin"])

    def test_rule_43_the_stalled_line_still_refuses_a_path_outside_the_journal_folder(self):
        self.ctx.stop.set()
        with self.assertRaises(WriteRefused):
            mj.Journal(os.path.join(self.world.base, "out", "x.jsonl"), self.ctx).write("stalled", seconds=1)

    def test_rule_43_a_stalled_line_after_stop_repairs_a_torn_tail_first(self):
        self.put(line(GOOD) + b'{"event": "be')
        self.ctx.stop.set()
        mj.Journal(self.path, self.ctx).write("stalled", seconds=1)
        self.assertEqual([x["event"] for x in mj.read_journal(self.path, self.ctx)], ["start", "repaired", "stalled"])


if __name__ == "__main__":
    unittest.main()
