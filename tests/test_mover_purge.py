import contextlib
import csv
import hashlib
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from lrclean import mover_purge, paths
from lrclean.mover_files import staged_path
from lrclean.mover_journal import Entry, Journal, JournalError, entries, read_journal, write_results
from lrclean.mover_purge import look_all, purge_set, purge_sha256, run_purge
from lrclean.mover_undo import RowResult
from mover_world import BATCH, World

MTIME = 1_700_000_000_000_000_000
WAIT = 10


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rewrite(path: str, data: bytes, mtime_ns: int) -> None:
    with open(path, "wb") as handle:
        handle.write(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))


class Fixture:
    """A World; files staged and archived on both roots, recorded as `Entry` the way the journal holds them."""

    def __init__(self, tmp: str):
        self.world = World(tmp)
        self.journal_path = os.path.join(self.world.base, "work", "journal", f"mover-{BATCH}.jsonl")
        self.removed: list[str] = []
        self.opens: list[str] = []
        self.on_remove = self.on_open = None
        self.aborted: list = []
        self.release = threading.Event()  # lets a stalled read go on once the run was aborted
        self.world.abort = self._abort
        self.ctx = self.world.ctx()
        self.use(opener=self._open, remove=self._remove)

    def use(self, **seams) -> None:
        self.ctx = self.ctx._replace(seams=self.ctx.seams._replace(**seams))

    def _abort(self, code) -> None:
        self.aborted.append(code)
        self.release.set()

    def _open(self, path):
        self.opens.append(path)
        if self.on_open:
            self.on_open(path)
        return self.world.opener(path)

    def _remove(self, path) -> None:
        if self.on_remove:
            self.on_remove(path)
        self.removed.append(path)
        self.world.remove(path)

    def deny_stat(self, path: str) -> None:
        real = self.ctx.seams.stat

        def stat(target):
            if os.path.normcase(target) == os.path.normcase(path):
                raise PermissionError(target)
            return real(target)
        self.use(stat=stat)

    def stage(self, rel: str, row: int, kind: str = "reject", family: int = 1, twin: bool = False) -> Entry:
        data = f"data of {rel}".encode()
        original = self.world.add(rel, data, MTIME)
        dests = self.world.archive(original)
        twin_path = self.world.add("twins/" + rel.replace("/", "-"), data, MTIME) if twin else ""
        staged = staged_path(self.world.base, BATCH, original)
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        os.rename(original, staged)
        st = os.stat(staged)
        sha = sha_of(data)
        return Entry(row, kind, original, staged, st.st_size, st.st_mtime_ns, st.st_ino, sha,
                     tuple((dest, sha) for dest in dests), twin_path, sha if twin else "", family, "staged")

    def family(self, row: int) -> list[Entry]:
        """A reject with a twin and two sidecars, as the journal lists them: the reject first."""
        folder = f"shoot-{row}"
        return [self.stage(f"{folder}/IMG_000{row}.ARW", row, "reject", 3, twin=True),
                self.stage(f"{folder}/IMG_000{row}.xmp", row, "sidecar", 3),
                self.stage(f"{folder}/deep/IMG_000{row}.dop", row, "sidecar", 3)]

    def journal(self) -> Journal:
        return Journal(self.journal_path, self.ctx)

    def record(self, items) -> None:
        """Write the `begin` and `end` lines of `stage` for each entry, as `stage` would have."""
        journal, seen = self.journal(), set()
        for entry in items:
            facts = {"row": entry.row, "original": entry.original, "bytes": entry.bytes,
                     "mtime_ns": entry.mtime_ns, "file_index": entry.file_index}
            begin = dict(facts, op="stage", kind=entry.kind, staged=entry.staged, sha256=entry.sha256,
                         dests=[list(pair) for pair in entry.dests], twin=entry.twin,
                         twin_sha256=entry.twin_sha256)
            if entry.row not in seen:
                begin["family"] = entry.family
            seen.add(entry.row)
            journal.write("begin", **begin)
            journal.write("end", op="stage", state="staged", **facts)

    def lines(self) -> list[dict]:
        return read_journal(self.journal_path, self.ctx) if os.path.exists(self.journal_path) else []

    def purge(self, items, **options):
        return self.purge_looked(look_all(items, self.ctx), **options)

    def purge_looked(self, looked, *, apply=True, roots=None, stall=10**9, start=None):
        return run_purge(looked, roots=roots or self.world.archive_roots, ctx=self.ctx, journal=self.journal(),
                         start={"purge_sha256": "0" * 64} if start is None else start, stall_seconds=stall,
                         apply=apply)

    def gone(self, entry: Entry) -> bool:
        return not os.path.exists(entry.staged)

    def untouched(self, items) -> bool:
        return not any(self.gone(entry) for entry in items)


@contextlib.contextmanager
def fixture():
    with tempfile.TemporaryDirectory() as tmp:
        yield Fixture(tmp)


class PurgeRowsTests(unittest.TestCase):
    def test_rule_41_a_row_is_purged_sidecars_first_and_the_reject_last(self):
        for order in ((0, 1, 2), (1, 0, 2)):  # the reject first, then between its sidecars
            with self.subTest(order=order), fixture() as fx:
                items = fx.family(0)
                result, = fx.purge([items[i] for i in order])
                self.assertEqual(fx.removed, [items[1].staged, items[2].staged, items[0].staged])
                self.assertEqual(result, RowResult(0, items[0].original, "purged", "",
                                                   (items[1].original, items[2].original, items[0].original)))
                self.assertTrue(all(fx.gone(entry) for entry in items))

    def test_rule_53_a_copy_row_is_purged_or_held_as_a_reject_row_is_and_every_record_says_its_kind(self):
        for kind in ("reject", "copy"):
            with self.subTest(kind), fixture() as fx:
                good = [fx.stage("shoot-1/IMG_0001.ARW", 1, kind, 2, twin=True),
                        fx.stage("shoot-1/IMG_0001.xmp", 1, "sidecar", 2)]
                bad = [fx.stage("shoot-2/IMG_0002.ARW", 2, kind, 2, twin=True),
                       fx.stage("shoot-2/IMG_0002.xmp", 2, "sidecar", 2)]
                rewrite(bad[0].twin, b"x" * bad[0].bytes, MTIME)  # a twin of other content
                fx.record([*good, *bad])
                first, second = fx.purge([good[1], good[0], bad[1], bad[0]])
                self.assertEqual(fx.removed, [good[1].staged, good[0].staged])
                self.assertEqual(first, RowResult(1, good[0].original, "purged", "",
                                                  (good[1].original, good[0].original)))
                self.assertEqual((second.index, second.source, second.state, second.reason),
                                 (2, bad[0].original, "held", "twin_mismatch"))
                lines = fx.lines()
                held, = [line for line in lines if line["event"] == "held"]
                self.assertEqual((held["source"], held["kind"]), (bad[0].original, kind))
                results = os.path.join(fx.world.base, "out", f"purge-{BATCH}.csv")
                write_results(results, "purge", lines, fx.ctx)
                with open(results, encoding="utf-8-sig", newline="") as handle:
                    shown = [(cells[0], cells[1], cells[5]) for cells in list(csv.reader(handle))[1:]]
                self.assertEqual(sorted(shown), sorted([(good[0].original, kind, "purged"),
                                                        (good[1].original, "sidecar", "purged"),
                                                        (bad[0].original, kind, "held")]))

    def test_rule_41_one_failure_holds_the_whole_row_and_the_command_goes_on(self):
        def at(path_of):
            return lambda fx, e: rewrite(path_of(e), b"x" * e.bytes, e.mtime_ns)

        def ids(path_of, pair_of):
            def edit(fx, e):
                fx.world.ids[path_of(e)] = pair_of(e)
            return edit

        def replaced(**fields):
            return lambda fx, e: e._replace(**fields)

        def outside(fx, e):
            copy = os.path.join(fx.world.tmp, "archive-3", "copy.xmp")
            os.makedirs(os.path.dirname(copy))
            with open(e.dests[1][0], "rb") as handle:
                rewrite(copy, handle.read(), e.mtime_ns)
            fx.world.ids[copy] = (4, None)  # on a volume of its own: only its folder is wrong
            return e._replace(dests=(e.dests[0], (copy, e.sha256)))

        def retime(fx, e):
            os.utime(e.staged, ns=(e.mtime_ns + 10**9,) * 2)

        def staged(e):
            return e.staged

        def first_copy(e):
            return e.dests[0][0]

        def second_copy(e):
            return e.dests[1][0]

        def twin(e):
            return e.twin

        # (name, index of the offender in the row, reason, edit: (fixture, entry) -> None or a new entry,
        #  the file named as failing: the staged file, an archive copy or the twin, never the library original)
        cases = [
            ("the staged file edited", 2, "staged_changed", at(staged), staged),
            ("the staged file resized", 2, "staged_changed",
             lambda fx, e: rewrite(e.staged, b"longer than before" * 5, e.mtime_ns), staged),
            ("the staged file retimed", 2, "staged_changed", retime, staged),
            ("the staged file reindexed", 2, "staged_changed",
             ids(staged, lambda e: (None, e.file_index + 1)), staged),
            ("the staged file gone", 2, "staged_changed", lambda fx, e: os.remove(e.staged), staged),
            ("the staged file unreadable", 2, "staged_changed", lambda fx, e: fx.deny_stat(e.staged), staged),
            ("the journal's hash differs from three hashes that agree", 2, "staged_changed",
             replaced(sha256="0" * 64), staged),
            ("the journal holds no hash", 2, "staged_changed", replaced(sha256=""), staged),
            ("the first archive copy edited alone", 2, "archive_mismatch", at(first_copy), first_copy),
            ("the second archive copy edited alone", 2, "archive_mismatch", at(second_copy), second_copy),
            ("an archive copy gone", 2, "archive_mismatch", lambda fx, e: os.remove(e.dests[1][0]), second_copy),
            ("a destination outside the configured roots", 2, "archive_mismatch", outside, second_copy),
            ("a destination that is the staged file", 2, "archive_mismatch",
             ids(first_copy, lambda e: (1, e.file_index)), first_copy),
            ("a destination on the staged file's volume", 2, "archive_mismatch",
             ids(first_copy, lambda e: (1, None)), first_copy),
            ("two destinations that are one file", 2, "archive_mismatch",
             ids(second_copy, lambda e: (2, os.stat(e.dests[0][0]).st_ino)), second_copy),
            ("two destinations on one volume", 2, "archive_mismatch",
             ids(second_copy, lambda e: (2, None)), second_copy),
            ("the twin edited", 0, "twin_mismatch", at(twin), twin),
            ("the twin gone", 0, "twin_mismatch", lambda fx, e: os.remove(e.twin), twin),
            ("the twin that is the staged file", 0, "twin_mismatch",
             ids(twin, lambda e: (1, e.file_index)), twin),
            ("an archive copy that became a placeholder after staging", 2, "archive_mismatch",
             lambda fx, e: fx.world.attrs.update({paths.norm(e.dests[1][0]): paths.PLACEHOLDER_MASK}), second_copy),
            ("the staged file that became a placeholder after staging", 2, "staged_changed",
             lambda fx, e: fx.world.attrs.update({paths.norm(e.staged): paths.PLACEHOLDER_MASK}), staged),
        ]
        never_opened = {"an archive copy that became a placeholder after staging",
                        "the staged file that became a placeholder after staging"}
        for name, offender, reason, edit, failing in cases:
            with self.subTest(name), fixture() as fx:
                rows = [fx.family(0), fx.family(1), fx.family(2)]
                new = edit(fx, rows[1][offender])
                if new is not None:
                    rows[1][offender] = new
                results = fx.purge([entry for row in rows for entry in row])
                self.assertEqual([(r.index, r.state, r.reason) for r in results],
                                 [(0, "purged", ""), (1, "held", reason), (2, "purged", "")])
                self.assertEqual(results[1].files, (failing(rows[1][offender]),))
                if name in never_opened:
                    self.assertNotIn(failing(rows[1][offender]), fx.opens)
                self.assertEqual(fx.removed, [rows[0][1].staged, rows[0][2].staged, rows[0][0].staged,
                                              rows[2][1].staged, rows[2][2].staged, rows[2][0].staged])
                held, = [line for line in fx.lines() if line["event"] == "held"]
                self.assertEqual((held["reason"], held["file"], held["kind"]),
                                 (reason, failing(rows[1][offender]), rows[1][offender].kind))

    def test_rule_41_a_file_that_changed_after_its_hash_holds_the_row_and_removes_none_of_its_files(self):
        def replaced(fx, e):  # another file at the staged path, same size and time
            os.rename(e.staged, e.staged + ".saved")
            rewrite(e.staged, b"x" * e.bytes, e.mtime_ns)

        # (name, when the change is made, the change, reason, the file named as failing)
        # "copy" and "copy2": as the first and the second archive copy of the reject is opened, after the reject's
        # own hash and, for "copy2", the first copy's. "stat": the twin is the last file read, so the change is
        # made just before the check's second stat of it. The reject is judged after its sidecars.
        cases = [
            ("the staged file resized", "copy",
             lambda fx, e: rewrite(e.staged, b"longer than before" * 5, e.mtime_ns), "staged_changed", "staged"),
            ("the staged file retimed", "copy",
             lambda fx, e: os.utime(e.staged, ns=(e.mtime_ns + 10**9,) * 2), "staged_changed", "staged"),
            ("the staged file on another volume", "copy",
             lambda fx, e: fx.world.ids.update({e.staged: (99, None)}), "staged_changed", "staged"),
            ("the staged file reindexed", "copy",
             lambda fx, e: fx.world.ids.update({e.staged: (None, e.file_index + 1)}), "staged_changed", "staged"),
            ("another file put at the staged path", "copy2", replaced, "staged_changed", "staged"),
            ("an archive copy removed", "copy2", lambda fx, e: os.remove(e.dests[0][0]),
             "archive_mismatch", "copy"),
            ("an archive copy resized", "copy2",
             lambda fx, e: rewrite(e.dests[0][0], b"y" * (e.bytes + 1), e.mtime_ns), "archive_mismatch", "copy"),
            ("the twin resized", "stat", lambda fx, e: rewrite(e.twin, b"z" * (e.bytes + 1), e.mtime_ns),
             "twin_mismatch", "twin"),
            # The sidecar (items[1]) is judged before the reject: a re-check per entry would miss its change.
            ("a sidecar's staged file retimed while the reject is judged", "copy",
             lambda fx, e: os.utime(e.staged, ns=(e.mtime_ns + 10**9,) * 2), "staged_changed", "staged", 1),
            ("a sidecar's archive copy resized while the reject is judged", "copy",
             lambda fx, e: rewrite(e.dests[0][0], b"y" * (e.bytes + 1), e.mtime_ns), "archive_mismatch", "copy", 1),
            # "own": as the reject's staged file is opened for its hash, after the stat taken before the read.
            ("the staged file retimed as its hash begins", "own",
             lambda fx, e: os.utime(e.staged, ns=(e.mtime_ns + 10**9,) * 2), "staged_changed", "staged"),
        ]
        for name, when, change, reason, which, *rest in cases:
            with self.subTest(name), fixture() as fx:
                items = fx.family(5)
                reject = items[0]
                victim = items[rest[0] if rest else 0]  # the entry whose file is changed
                failing = {"staged": victim.staged, "copy": victim.dests[0][0], "twin": victim.twin}[which]
                if when == "stat":
                    real, calls = fx.ctx.seams.stat, []

                    def stat(target, real=real, calls=calls, change=change, twin=reject.twin):
                        if os.path.normcase(target) == os.path.normcase(twin):
                            calls.append(target)
                            if len(calls) == 2:
                                change(fx, victim)
                        return real(target)
                    fx.use(stat=stat)
                else:
                    trigger = {"copy": reject.dests[0][0], "copy2": reject.dests[1][0], "own": reject.staged}[when]
                    fx.on_open = lambda path, change=change, trigger=trigger: (
                        change(fx, victim) if path == trigger else None)
                result, = fx.purge(items)
                self.assertEqual((result.state, result.reason, result.files), ("held", reason, (failing,)))
                held, = [line for line in fx.lines() if line["event"] == "held"]
                self.assertEqual((held["reason"], held["file"], held["kind"]), (reason, failing, victim.kind))
                self.assertEqual(fx.removed, [])
                self.assertTrue(fx.untouched(items))

    def test_rule_41_a_held_row_writes_one_held_line_and_removes_none_of_its_files(self):
        with fixture() as fx:
            items = fx.family(4)
            fx.world.ids[items[2].staged] = (None, items[2].file_index + 1)
            fx.purge(items)
            held, = [line for line in fx.lines() if line["event"] == "held"]
            self.assertEqual({key: held[key] for key in ("op", "row", "source", "kind", "reason", "file")},
                             {"op": "purge", "row": 4, "source": items[0].original, "kind": "sidecar",
                              "reason": "staged_changed", "file": items[2].staged})
            self.assertEqual(fx.removed, [])
            self.assertTrue(fx.untouched(items))


class PurgeFamilyTests(unittest.TestCase):
    def test_rule_41_a_family_of_only_the_reject_with_family_two_is_held_and_nothing_is_deleted(self):
        with fixture() as fx:
            lonely = fx.stage("shoot-a/IMG_0001.ARW", 0, "reject", 2)
            rows = [fx.family(1), [lonely], fx.family(2)]
            results = fx.purge([entry for row in rows for entry in row])
            self.assertEqual([(r.index, r.state, r.reason) for r in results],
                             [(1, "purged", ""), (0, "held", "family_incomplete"), (2, "purged", "")])
            self.assertEqual(results[1].files, (lonely.original,))
            self.assertTrue(fx.untouched([lonely]))

    def test_rule_39_a_row_whose_family_is_zero_not_an_int_or_below_its_files_is_an_invalid_journal(self):
        for name, family in (("zero", 0), ("string", "3"), ("float", 3.0), ("below the files", 2)):
            for bad in (0, 1):  # the invalid row first, then after a valid one: no check of the first row only
                with self.subTest(name, bad_row=bad), fixture() as fx:
                    rows = [fx.family(0), fx.family(1)]
                    rows[bad] = [item._replace(family=family) for item in rows[bad]]
                    items = rows[0] + rows[1]
                    with self.assertRaises(JournalError) as raised:
                        fx.purge(items)
                    self.assertIn(f"row {bad}", str(raised.exception))
                    self.assertEqual(fx.removed, [])
                    self.assertTrue(fx.untouched(items))
        with fixture() as fx:  # a bool is no count even where it equals the files: one file, family True
            lonely = fx.stage("shoot-a/IMG_0001.ARW", 0, "reject", True)
            with self.assertRaises(JournalError):
                fx.purge([lonely])

    def test_rule_41_a_family_partly_purged_is_not_held(self):
        for layout in (("purged", "purged", "staged"), ("purged", "staged", "purged"), ("staged", "purged", "purged")):
            with self.subTest(layout=layout), fixture() as fx:
                items = [item._replace(state=state) for item, state in zip(fx.family(0), layout)]
                for item in items:
                    if item.state == "purged":
                        os.remove(item.staged)
                result, = fx.purge(items)
                self.assertEqual((result.state, result.reason), ("purged", ""))
                self.assertEqual(len(fx.removed), 1)

    def test_rule_41_a_family_with_fewer_files_staged_and_purged_than_its_count_is_held(self):
        with fixture() as fx:
            reject, sidecar, _third = fx.family(0)
            os.remove(sidecar.staged)
            result, = fx.purge([reject, sidecar._replace(state="purged")])
            self.assertEqual((result.state, result.reason), ("held", "family_incomplete"))
            self.assertTrue(fx.untouched([reject]))

    def test_rule_41_the_family_count_takes_only_files_with_an_end_line(self):
        for state in ("open_stage", "open_restore"):  # looking places the open_stage sidecar as staged
            with self.subTest(state=state), fixture() as fx:
                reject, sidecar, third = fx.family(0)
                family = [reject, sidecar._replace(state=state), third]
                results = fx.purge(family)
                self.assertEqual([(r.state, r.reason) for r in results], [("held", "family_incomplete")])
                self.assertTrue(fx.untouched(family))
                self.assertEqual(fx.removed, [])

    def test_rule_41_an_unplaced_entry_holds_its_row_and_the_other_rows_are_purged(self):
        def open_purge_unreadable(fx, row):
            fx.deny_stat(row[1].staged)
            return row[1]._replace(state="open_purge")

        def open_stage_nowhere(fx, row):
            os.remove(row[1].staged)  # neither the original nor the staged file exists
            return row[1]._replace(state="open_stage")

        for name, make in (("open_purge", open_purge_unreadable), ("open_stage", open_stage_nowhere)):
            with self.subTest(name), fixture() as fx:
                rows = [fx.family(0), fx.family(1), fx.family(2)]
                rows[1][1] = make(fx, rows[1])
                results = fx.purge([entry for row in rows for entry in row])
                self.assertEqual([(r.index, r.state, r.reason) for r in results],
                                 [(0, "purged", ""), (1, "held", "unplaced"), (2, "purged", "")])
                self.assertTrue(fx.untouched([rows[1][0], rows[1][2]]))
                self.assertEqual(len(fx.removed), 6)
                held, = [line for line in fx.lines() if line["event"] == "held"]
                self.assertEqual((held["reason"], held["file"], held["kind"]),
                                 ("unplaced", rows[1][1].original, "sidecar"))  # the unplaced one, not the first

    def test_rule_41_a_row_whose_only_entry_is_unplaced_is_held_not_dropped(self):
        with fixture() as fx:
            lost = fx.stage("shoot-a/IMG_0001.ARW", 0, "reject", 1)
            os.remove(lost.staged)
            rows = [[lost._replace(state="open_stage")], fx.family(1)]
            results = fx.purge([entry for row in rows for entry in row])
            self.assertEqual([(r.index, r.state, r.reason, r.files) for r in results],
                             [(0, "held", "unplaced", (lost.original,)), (1, "purged", "", results[1].files)])
            held, = [line for line in fx.lines() if line["event"] == "held"]
            self.assertEqual((held["reason"], held["file"], held["kind"]), ("unplaced", lost.original, "reject"))

    def test_rule_41_a_row_of_open_steps_is_held_when_its_files_are_in_staging(self):
        for state, in_staging, placement in (("open_stage", True, "done"),
                                             ("open_stage", False, "not_done"),
                                             ("open_restore", True, "not_done"),
                                             ("open_restore", False, "done")):
            for size in (1, 2):
                with self.subTest(state=state, in_staging=in_staging, size=size), fixture() as fx:
                    control = fx.family(1)
                    names = [("IMG_0004.ARW", "reject"), ("IMG_0004.xmp", "sidecar")]
                    opened = [fx.stage(f"shoot-4/{name}", 4, kind, size)._replace(state=state)
                              for name, kind in names[:size]]
                    for entry in opened:
                        self.assertNotEqual(entry.row, control[0].row)
                        self.assertNotEqual(entry.family, control[0].family)
                        if not in_staging:
                            os.rename(entry.staged, entry.original)
                        self.assertEqual((os.path.exists(entry.original), os.path.exists(entry.staged)),
                                         (not in_staging, in_staging))
                    looked = look_all(opened + control, fx.ctx)
                    self.assertEqual([look for _, look in looked[:size]], [placement] * size)
                    expected = [(1, "purged", "")]
                    if in_staging:
                        expected.insert(0, (4, "held", "family_incomplete"))
                    results = fx.purge_looked(looked)
                    self.assertEqual([(r.index, r.state, r.reason) for r in results], expected)
                    self.assertTrue(all(fx.gone(entry) for entry in control))
                    held = [line for line in fx.lines() if line["event"] == "held"]
                    if in_staging:
                        reject = opened[0].original
                        self.assertEqual(results[0], RowResult(4, reject, "held", "family_incomplete", (reject,)))
                        self.assertEqual([(h["reason"], h["kind"], h["file"]) for h in held],
                                         [("family_incomplete", "reject", reject)])
                        self.assertTrue(fx.untouched(opened))
                    else:
                        self.assertEqual(held, [])
                        self.assertTrue(all(os.path.exists(entry.original) for entry in opened))

    def test_rule_41_a_second_purge_after_a_crash_that_deleted_the_sidecars_only_finishes_the_family(self):
        # the crash is at the reject's remove (both sidecars have their `end`), or after the last sidecar's
        # remove and before its `end` line (that sidecar stays an open purge whose file is gone: it still counts)
        for crash_at, states in (("reject_remove", ["open_purge", "purged", "purged"]),
                                 ("sidecar_end", ["staged", "purged", "open_purge"])):
            with self.subTest(crash_at), fixture() as fx:
                items = fx.family(0)
                fx.record(items)
                real_append = fx.ctx.seams.append

                def crash(path):
                    if path == items[0].staged and crash_at == "reject_remove":
                        raise OSError("the drive went away")

                def append(path, data):
                    line = json.loads(data)
                    if crash_at == "sidecar_end" and line["event"] == "end" and line["original"] == items[2].original:
                        raise OSError("the drive went away")
                    real_append(path, data)
                fx.on_remove = crash
                fx.use(append=append)
                with self.assertRaises(OSError):
                    fx.purge(entries(fx.lines()))
                self.assertEqual(fx.removed[:2], [items[1].staged, items[2].staged])
                fx.on_remove = None
                fx.use(append=real_append)
                left = entries(fx.lines())
                self.assertEqual([e.state for e in left], states)
                self.assertEqual([e.staged for e in purge_set(look_all(left, fx.ctx))], [items[0].staged])
                result, = fx.purge(left)
                self.assertEqual((result.state, result.reason, result.files), ("purged", "", (items[0].original,)))
                self.assertTrue(fx.gone(items[0]))
                self.assertEqual(fx.purge(entries(fx.lines())), [])  # a third finds nothing to do


class PurgeJournalTests(unittest.TestCase):
    def test_rule_39_the_journal_holds_the_begin_line_when_the_remove_runs(self):
        with fixture() as fx:
            items = fx.family(3)
            seen = []

            def inside(path):
                last = fx.lines()[-1]
                seen.append((path, last["event"], last["op"], last["original"], last["staged"], last["row"]))
            fx.on_remove = inside
            fx.purge(items)
            self.assertEqual(seen, [(e.staged, "begin", "purge", e.original, e.staged, 3)
                                    for e in (items[1], items[2], items[0])])

    def test_rule_39_purge_writes_its_own_start_line_then_a_begin_and_an_end_per_file(self):
        with fixture() as fx:
            items = fx.family(0)
            fx.purge(items, start={"purge_sha256": "ab" * 32})
            lines = fx.lines()
            self.assertEqual({k: lines[0][k] for k in ("event", "op", "batch", "purge_sha256")},
                             {"event": "start", "op": "purge", "batch": BATCH, "purge_sha256": "ab" * 32})
            self.assertEqual([(line["event"], line["op"]) for line in lines[1:]], [("begin", "purge"), ("end", "purge")] * 3)
            end = lines[2]
            self.assertEqual((end["state"], end["row"], end["original"], end["bytes"], end["mtime_ns"],
                              end["file_index"]),
                             ("purged", 0, items[1].original, items[1].bytes, items[1].mtime_ns, items[1].file_index))
            self.assertEqual([e.state for e in entries(lines)], ["purged"] * 3)


class PurgeDryRunTests(unittest.TestCase):
    def test_rule_42_a_dry_run_lists_the_files_reads_no_content_and_writes_nothing(self):
        with fixture() as fx:
            items = fx.family(0)
            rows = [items, [fx.stage("shoot-a/IMG_0009.ARW", 1, "reject", 2)]]
            flat = [entry for row in rows for entry in row]
            before = fx.world.listing()
            results = fx.purge(flat, apply=False)
            self.assertEqual([(r.index, r.state, r.reason) for r in results],
                             [(0, "would_purge", ""), (1, "held", "family_incomplete")])
            self.assertEqual(results[0].files, (items[1].original, items[2].original, items[0].original))
            self.assertEqual((fx.opens, fx.removed), ([], []))
            self.assertFalse(os.path.exists(fx.journal_path))
            self.assertEqual(fx.world.listing(), before)
            self.assertTrue(fx.untouched(flat))

    def test_rule_42_a_dry_run_holds_a_row_whose_staged_file_is_gone_or_differs_by_a_stat_alone(self):
        def retime(fx, e):
            os.utime(e.staged, ns=(e.mtime_ns + 10**9,) * 2)

        for name, edit in (("gone", lambda fx, e: os.remove(e.staged)), ("retimed", retime),
                           ("reindexed", lambda fx, e: fx.world.ids.update({e.staged: (None, e.file_index + 1)})),
                           ("resized", lambda fx, e: rewrite(e.staged, b"longer than before" * 5, e.mtime_ns))):
            with self.subTest(name), fixture() as fx:
                rows = [fx.family(0), fx.family(1)]
                edit(fx, rows[1][2])
                results = fx.purge([entry for row in rows for entry in row], apply=False)
                self.assertEqual([(r.index, r.state, r.reason) for r in results],
                                 [(0, "would_purge", ""), (1, "held", "staged_changed")])
                self.assertEqual((fx.opens, fx.removed), ([], []))

    def test_rule_41_the_set_and_its_hash_come_from_the_journal_with_no_content_read(self):
        with fixture() as fx:
            first, second = fx.stage("shoot-a/IMG_0001.ARW", 0), fx.stage("shoot-a/IMG_0002.ARW", 1)
            first, second = first._replace(sha256="b" * 64), second._replace(sha256="a" * 64)
            lines = sorted(f"{e.staged}\t{e.sha256}\n" for e in (first, second))
            expected = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
            self.assertEqual(purge_sha256(look_all([second, first], fx.ctx)), expected)
            self.assertEqual(purge_sha256(look_all([first, second], fx.ctx)), expected)
            self.assertEqual(fx.opens, [])

    def test_rule_41_purge_set_holds_an_open_purge_whose_file_is_present_and_leaves_out_one_whose_file_is_gone(self):
        with fixture() as fx:
            staged, here, gone, purged, restored, stage_open, restore_open, unreadable = [
                fx.stage(f"shoot-a/IMG_000{i}.ARW", i) for i in range(8)]
            os.remove(gone.staged)
            fx.deny_stat(unreadable.staged)  # `place` cannot tell: unplaced, so not in the set
            items = [staged, here._replace(state="open_purge"), gone._replace(state="open_purge"),
                     purged._replace(state="purged"), restored._replace(state="restored"),
                     stage_open._replace(state="open_stage"), restore_open._replace(state="open_restore"),
                     unreadable._replace(state="open_purge")]
            opened = [items[1], items[2], items[5], items[6], items[7]]
            with mock.patch.object(mover_purge, "place", wraps=mover_purge.place) as places:
                looked = look_all(items, fx.ctx)
                self.assertEqual(purge_set(looked), [items[0], items[1]])
                digest = purge_sha256(looked)
                fx.purge_looked(looked, apply=False)
            self.assertEqual([call.args[0] for call in places.call_args_list], opened)  # once each, in all three
            self.assertEqual(digest, purge_sha256(look_all([items[0], items[1]], fx.ctx)))
            self.assertNotEqual(digest, purge_sha256(look_all([items[0]], fx.ctx)))

    def test_rule_41_an_entry_whose_second_look_would_differ_is_held_by_the_look_the_digest_used(self):
        with fixture() as fx:
            keep, open_one = fx.stage("shoot-a/IMG_0001.ARW", 0), fx.stage("shoot-a/IMG_0002.ARW", 1)
            open_one = open_one._replace(state="open_purge")
            real, calls = fx.ctx.seams.stat, []

            def stat(path):  # the first look at the open entry's original cannot tell; a second one could
                if path == open_one.original:
                    calls.append(path)
                    if len(calls) == 1:
                        raise PermissionError(path)
                return real(path)
            fx.use(stat=stat)
            looked = look_all([keep, open_one], fx.ctx)
            self.assertEqual([look for _entry, look in looked], ["", "unplaced"])
            self.assertEqual(purge_set(looked), [keep])
            results = fx.purge_looked(looked)
            self.assertEqual([(r.index, r.state, r.reason) for r in results],
                             [(0, "purged", ""), (1, "held", "unplaced")])
            self.assertEqual((len(calls), fx.removed), (1, [keep.staged]))
            self.assertTrue(os.path.exists(open_one.staged))


class PurgeFoldersTests(unittest.TestCase):
    def test_rule_38_a_staged_path_outside_staging_holds_its_row_before_any_read_and_the_others_go_on(self):
        for apply in (True, False):  # the dry run says what --apply would do, so it holds the row too
            with self.subTest(apply=apply), fixture() as fx:
                rows = [fx.family(0), fx.family(1), fx.family(2)]
                offender = rows[1][2]  # not the first file of its row; the sidecars come first
                elsewhere = fx.world.path("elsewhere/IMG_0001.dop")
                os.makedirs(os.path.dirname(elsewhere))
                os.rename(offender.staged, elsewhere)
                rows[1][2] = offender._replace(staged=elsewhere)
                real, real_listdir, statted, pruning = fx.ctx.seams.stat, fx.ctx.seams.listdir, [], []

                def stat(path):
                    if not pruning:  # the prune of empty folders afterwards looks at every file
                        statted.append(path)
                    return real(path)

                def listdir(path):
                    pruning.append(path)
                    return real_listdir(path)
                fx.use(stat=stat, listdir=listdir)
                results = fx.purge([entry for row in rows for entry in row], apply=apply)
                self.assertEqual([(r.index, r.state, r.reason) for r in results],
                                 [(0, "purged" if apply else "would_purge", ""), (1, "held", "outside_folders"),
                                  (2, "purged" if apply else "would_purge", "")])
                row = {path for entry in rows[1] for path in (entry.staged, entry.twin, *(d for d, _ in entry.dests))}
                row.discard("")
                self.assertFalse(row & set(statted))  # no stat of a path of the held row
                self.assertFalse(row & set(fx.opens))  # and no read
                self.assertTrue(fx.untouched(rows[1]) and os.path.exists(elsewhere))
                held = [line for line in fx.lines() if line["event"] == "held"]
                if apply:
                    self.assertEqual([(h["reason"], h["file"], h["kind"], h["row"]) for h in held],
                                     [("outside_folders", offender.original, "sidecar", 1)])
                    self.assertEqual(fx.removed, [rows[0][1].staged, rows[0][2].staged, rows[0][0].staged,
                                                  rows[2][1].staged, rows[2][2].staged, rows[2][0].staged])
                else:
                    self.assertEqual((held, fx.removed, fx.opens), ([], [], []))

    def test_rule_41_empty_folders_below_the_batch_folder_go_and_the_batch_folder_and_others_stay(self):
        with fixture() as fx:
            staging = os.path.join(fx.world.base, "work", "staging")
            other = os.path.join(staging, "20250101T000000Z", "c", "empty")
            os.makedirs(other)
            items = fx.family(0)
            fx.purge(items)
            self.assertEqual(os.listdir(os.path.join(staging, BATCH)), [])
            self.assertTrue(os.path.isdir(other))

    def test_rule_41_a_folder_that_still_holds_a_staged_file_stays_and_leftover_empty_ones_go(self):
        with fixture() as fx:
            batch = os.path.join(fx.world.base, "work", "staging", BATCH)
            held = fx.family(1)
            fx.world.ids[held[2].staged] = (None, held[2].file_index + 1)  # row 1 stays staged
            leftover = os.path.join(batch, "c", "leftover")
            os.makedirs(os.path.join(leftover, "empty"))
            items = fx.family(0)
            fx.purge(items + held)
            self.assertFalse(os.path.exists(os.path.dirname(items[2].staged)))  # row 0's deepest folder
            self.assertFalse(os.path.exists(os.path.dirname(items[0].staged)))
            self.assertFalse(os.path.exists(leftover))
            self.assertTrue(fx.untouched(held))
            self.assertTrue(os.path.isdir(os.path.dirname(held[2].staged)))

    def test_rule_41_a_link_inside_the_batch_folder_is_not_entered(self):
        with fixture() as fx:
            batch = os.path.join(fx.world.base, "work", "staging", BATCH)
            linked = os.path.join(batch, "c", "linked")
            os.makedirs(os.path.join(linked, "inner"))
            fx.world.links.add(linked)
            fx.purge(fx.family(0))
            self.assertTrue(os.path.isdir(os.path.join(linked, "inner")))

    def test_rule_41_a_dry_run_removes_no_folder(self):
        with fixture() as fx:
            items = fx.family(0)
            fx.purge(items, apply=False)
            self.assertTrue(os.path.isdir(os.path.dirname(items[2].staged)))


class PurgeStallTests(unittest.TestCase):
    def blocked(self, fx: Fixture, items):
        """The first content read blocks until the run is aborted, then returns."""
        target = items[1].staged  # the first file read: sidecars come before the reject
        returned = threading.Event()

        def on_open(path):
            if path == target:
                fx.release.wait(WAIT)
                returned.set()
        fx.on_open = on_open
        return returned

    def test_rule_43_a_stall_in_a_hash_read_ends_the_run_before_any_removal(self):
        with fixture() as fx:
            items = fx.family(0)
            returned = self.blocked(fx, items)
            results = fx.purge(items, stall=2)
            self.assertEqual(results, [])
            self.assertEqual(fx.aborted, [5])
            self.assertEqual(fx.removed, [])
            self.assertEqual([line["event"] for line in fx.lines()], ["start", "stalled"])
            self.assertTrue(returned.wait(WAIT))

    def test_rule_43_a_read_that_returns_after_the_stalled_line_removes_nothing(self):
        with fixture() as fx:
            items = fx.family(0)
            returned = self.blocked(fx, items)
            fx.purge(items, stall=2)
            self.assertTrue(returned.wait(WAIT))
            threading.Event().wait(0.3)  # the late read gets its chance to go on
            self.assertEqual(fx.removed, [])
            self.assertTrue(fx.untouched(items))
            self.assertEqual([line["event"] for line in fx.lines()], ["start", "stalled"])

    def test_rule_43_a_run_that_keeps_reading_is_not_stalled_though_it_takes_longer_than_the_limit(self):
        with fixture() as fx:
            rows = fx.family(0) + fx.family(1)
            fx.on_open = lambda path: threading.Event().wait(0.05)  # every read is slow, none for the limit
            started = fx.world.ticks
            results = fx.purge(rows, stall=8)  # the fake clock moves once per poll of the watch
            self.assertEqual([r.state for r in results], ["purged", "purged"])
            self.assertEqual(fx.aborted, [])
            self.assertGreater(fx.world.ticks - started, 8)  # in all, longer than the limit


if __name__ == "__main__":
    unittest.main()
