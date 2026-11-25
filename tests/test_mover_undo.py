import contextlib
import os
import tempfile
import unittest

from lrclean.mover_files import staged_path
from lrclean.mover_journal import Entry, Journal, entries, read_journal
from lrclean.mover_seams import Stopped
from lrclean.mover_undo import RowResult, run_undo
from tests.mover_world import BATCH, World

MTIME = 1_700_000_000_000_000_000
REPARSE = 0x400


class Fixture:
    """A World with a journal, and staged files recorded as the journal would hold them."""

    def __init__(self, tmp: str):
        self.world = World(tmp)
        self.ctx = self.world.ctx()
        self.journal_path = os.path.join(self.world.base, "work", "journal", f"mover-{BATCH}.jsonl")
        self.renames: list[tuple[str, str]] = []

    def use_recorder(self, inside=None) -> None:
        def rename(src, dst):
            if inside:
                inside()
            self.renames.append((src, dst))
            self.world.rename(src, dst)
        self.ctx = self.ctx._replace(seams=self.ctx.seams._replace(rename=rename))

    def deny_stat(self, path: str) -> None:
        real = self.ctx.seams.stat

        def stat(target):
            if os.path.normcase(target) == os.path.normcase(path):
                raise PermissionError(target)
            return real(target)
        self.ctx = self.ctx._replace(seams=self.ctx.seams._replace(stat=stat))

    def journal(self) -> Journal:
        return Journal(self.journal_path, self.ctx)

    def stage(self, rel: str, row: int, kind: str = "reject", family: int = 1, data: bytes = b"photo") -> Entry:
        original = self.world.add(rel, data, MTIME)
        staged = staged_path(self.world.base, BATCH, original)
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        os.rename(original, staged)
        st = os.stat(staged)
        return Entry(row, kind, original, staged, st.st_size, st.st_mtime_ns, st.st_ino, "sha", (), "", "",
                     family, "staged")

    def undo(self, items, apply=True, start=None):
        return run_undo(items, ctx=self.ctx, journal=self.journal(), start=start or {}, apply=apply)

    def moved(self, entry: Entry) -> bool:
        return os.path.exists(entry.original) and not os.path.exists(entry.staged)


@contextlib.contextmanager
def fixture():
    with tempfile.TemporaryDirectory() as tmp:
        yield Fixture(tmp)


def family(fx: Fixture, folder: str = "shoot-a"):
    reject = fx.stage(f"{folder}/IMG_0001.ARW", 0, "reject", 3)
    first = fx.stage(f"{folder}/deep/IMG_0001.xmp", 0, "sidecar", 3, b"xmp")
    second = fx.stage(f"{folder}/IMG_0001.dop", 0, "sidecar", 3, b"dop")
    return reject, first, second


class UndoRestoreTests(unittest.TestCase):
    def test_rule_40_rows_and_files_replayed_newest_first(self):
        with fixture() as fx:
            first = fx.stage("shoot-a/IMG_0001.ARW", 0, "reject", 2), fx.stage("shoot-a/IMG_0001.xmp", 0, "sidecar", 2)
            second = fx.stage("shoot-a/IMG_0002.ARW", 1, "reject", 2), fx.stage("shoot-a/IMG_0002.xmp", 1, "sidecar", 2)
            fx.use_recorder()
            results = fx.undo([*first, *second])
            self.assertEqual([r.index for r in results], [1, 0])
            self.assertEqual([r.state for r in results], ["restored", "restored"])
            self.assertEqual([dst for _src, dst in fx.renames],
                             [second[1].original, second[0].original, first[1].original, first[0].original])

    def test_rule_40_row_result_names_source_files_and_reason(self):
        with fixture() as fx:
            reject, sidecar = fx.stage("shoot-a/IMG_0001.ARW", 4, "reject", 2), fx.stage("shoot-a/x.xmp", 4, "sidecar", 2)
            result, = fx.undo([sidecar, reject])
            self.assertEqual(result, RowResult(4, reject.original, "restored", "", (reject.original, sidecar.original)))

    def test_rule_53_a_row_of_kind_copy_is_named_by_its_source_as_a_reject_row_is(self):
        for kind in ("reject", "copy"):
            with self.subTest(kind), fixture() as fx:
                main, sidecar = fx.stage("shoot-a/IMG_0001.ARW", 4, kind, 2), fx.stage("shoot-a/x.xmp", 4, "sidecar", 2)
                result, = fx.undo([sidecar, main])  # the sidecar first: only the kind tells the source
                self.assertEqual(result, RowResult(4, main.original, "restored", "", (main.original, sidecar.original)))

    def test_rule_40_files_lists_only_the_renames_of_the_row_in_rename_order(self):
        with fixture() as fx:
            reject, middle, last = family(fx)
            fx.use_recorder()
            result, = fx.undo([reject, middle._replace(state="restored"), last])
            self.assertEqual(result.files, (last.original, reject.original))
            self.assertEqual([dst for _src, dst in fx.renames], list(result.files))

    def test_rule_40_a_file_directly_in_photo_root_is_restored(self):
        with fixture() as fx:
            reject = fx.stage("IMG_0001.ARW", 0)
            result, = fx.undo([reject])
            self.assertEqual(result.state, "restored")
            self.assertTrue(fx.moved(reject))

    def test_rule_40_refusals_hold_the_whole_row_the_offender_not_first(self):
        def original_exists(fx, middle):
            with open(middle.original, "wb") as handle:
                handle.write(b"new")

        def resized(fx, middle):
            with open(middle.staged, "wb") as handle:
                handle.write(b"longer than before")
            os.utime(middle.staged, ns=(MTIME, MTIME))

        def retimed(fx, middle):
            os.utime(middle.staged, ns=(MTIME + 10**9, MTIME + 10**9))

        def reindexed(fx, middle):
            fx.world.ids[middle.staged] = (None, middle.file_index + 1)

        def gone(fx, middle):
            os.remove(middle.staged)

        def parent_gone(fx, middle):
            os.rmdir(os.path.dirname(middle.original))

        def original_unreadable(fx, middle):
            fx.deny_stat(middle.original)

        def staged_unreadable(fx, middle):
            fx.deny_stat(middle.staged)

        def parent_unreadable(fx, middle):
            fx.deny_stat(os.path.dirname(middle.original))

        def parent_is_a_file(fx, middle):
            os.rmdir(os.path.dirname(middle.original))
            with open(os.path.dirname(middle.original), "wb") as handle:
                handle.write(b"not a folder")

        # a stat that fails other than not-found holds the row: original -> original_exists, staged ->
        # staged_changed, parent -> parent_missing; a parent that is a file -> parent_missing
        cases = {"original_exists": original_exists, "staged_changed size": resized, "staged_changed time": retimed,
                 "staged_changed file index": reindexed, "staged_missing": gone, "parent_missing": parent_gone,
                 "original_exists unreadable": original_unreadable, "staged_changed unreadable": staged_unreadable,
                 "parent_missing unreadable": parent_unreadable, "parent_missing a file": parent_is_a_file}
        for name, damage in cases.items():
            with self.subTest(name), fixture() as fx:
                reject, middle, last = family(fx)
                damage(fx, middle)
                fx.use_recorder()
                result, = fx.undo([reject, middle, last])
                self.assertEqual((result.state, result.reason), ("refused", name.split(" ")[0]))
                self.assertEqual(fx.renames, [])
                self.assertTrue(os.path.exists(reject.staged) and os.path.exists(last.staged))
                if name == "parent_missing":
                    self.assertFalse(os.path.exists(os.path.dirname(middle.original)))

    def test_rule_40_a_family_with_a_purged_file_is_refused_and_stays_staged(self):
        for name, state in (("purged", "purged"), ("open_purge placed done", "open_purge")):
            with self.subTest(name), fixture() as fx:
                reject, middle, last = family(fx)
                os.remove(middle.staged)
                fx.use_recorder()
                result, = fx.undo([reject, middle._replace(state=state), last])
                self.assertEqual((result.state, result.reason), ("refused", "purged"))
                self.assertEqual(fx.renames, [])
                self.assertTrue(os.path.exists(reject.staged) and os.path.exists(last.staged))

    def test_rule_40_a_link_in_the_folders_of_the_target_refuses_the_row(self):
        cases = (("photo_root", ".", "shoot-a/sub/IMG_0001.xmp"), ("between", "shoot-a", "shoot-a/sub/IMG_0001.xmp"),
                 ("parent", "shoot-a/sub", "shoot-a/sub/IMG_0001.xmp"),
                 ("only the sidecar's folder", "shoot-a/deep", "shoot-a/deep/IMG_0001.xmp"))
        for name, link, sidecar_rel in cases:
            with self.subTest(name), fixture() as fx:
                reject = fx.stage("shoot-a/sub/IMG_0001.ARW", 0, "reject", 2)
                sidecar = fx.stage(sidecar_rel, 0, "sidecar", 2)
                fx.world.links.add(fx.world.photo_root if link == "." else fx.world.path(link))
                fx.use_recorder()
                result, = fx.undo([reject, sidecar])
                self.assertEqual((result.state, result.reason), ("refused", "parent_link"))
                self.assertEqual(fx.renames, [])

    def test_rule_40_a_folder_with_only_the_reparse_attribute_is_restored_into(self):
        with fixture() as fx:
            reject = fx.stage("shoot-a/IMG_0001.ARW", 0)
            fx.world.attrs[os.path.normcase(os.path.dirname(reject.original))] = REPARSE
            result, = fx.undo([reject])
            self.assertEqual(result.state, "restored")
            self.assertTrue(fx.moved(reject))

    def test_rule_38_a_target_outside_photo_root_is_refused_not_renamed(self):
        with fixture() as fx:
            reject = fx.stage("shoot-a/IMG_0001.ARW", 0)
            outside = os.path.join(fx.world.tmp, "elsewhere", "IMG_0001.ARW")
            os.makedirs(os.path.dirname(outside))
            fx.use_recorder()
            result, = fx.undo([reject._replace(original=outside)])
            self.assertEqual((result.state, result.reason), ("refused", "outside_folders"))
            self.assertEqual(fx.renames, [])
            self.assertFalse(os.path.exists(outside))

    def test_rule_38_a_path_outside_its_folder_refuses_its_row_whole_and_older_rows_go_on(self):
        def staged_outside(fx, entry):
            elsewhere = fx.world.add("shoot-a/other.ARW", b"photo", MTIME)
            st = os.stat(elsewhere)
            return entry._replace(staged=elsewhere, bytes=st.st_size, mtime_ns=st.st_mtime_ns, file_index=st.st_ino)

        def original_outside(fx, entry):
            return entry._replace(original=os.path.join(fx.world.tmp, "elsewhere", "IMG_0001.ARW"))

        for name, damage in (("staged", staged_outside), ("original", original_outside)):
            with self.subTest(name), fixture() as fx:
                older = fx.stage("shoot-a/IMG_0009.ARW", 0)
                reject, sidecar = fx.stage("shoot-a/IMG_0001.ARW", 1, "reject", 2), fx.stage("shoot-a/a.xmp", 1, "sidecar", 2)
                fx.use_recorder()
                new, old = fx.undo([older, damage(fx, reject), sidecar])
                self.assertEqual((new.state, new.reason), ("refused", "outside_folders"))
                self.assertEqual((old.state, old.reason), ("restored", ""))
                self.assertEqual(fx.renames, [(older.staged, older.original)])
                self.assertTrue(os.path.exists(sidecar.staged) and not os.path.exists(sidecar.original))

    def test_rule_40_a_stopped_mover_renames_nothing(self):
        with fixture() as fx:
            reject = fx.stage("shoot-a/IMG_0001.ARW", 0)
            fx.use_recorder()
            fx.ctx.stop.set()
            with self.assertRaises(Stopped):
                fx.undo([reject])
            self.assertEqual(fx.renames, [])

    def test_rule_40_the_restore_leaves_the_staging_folders_in_place(self):
        with fixture() as fx:
            reject = fx.stage("shoot-a/IMG_0001.ARW", 0)
            fx.undo([reject])
            self.assertTrue(os.path.isdir(os.path.dirname(reject.staged)))


class UndoOpenBeginTests(unittest.TestCase):
    def open_entry(self, fx, rel, row, op, original_there, staged_there):
        entry = fx.stage(rel, row)._replace(state=f"open_{op}")
        if not staged_there:
            os.remove(entry.staged)
        if original_there:
            fx.world.add(rel, b"photo", MTIME)
        return entry

    def test_rule_40_a_stage_begin_with_no_end_is_placed_by_looking(self):
        cases = {"nothing happened": (True, False, "skipped", ""), "it moved": (False, True, "restored", ""),
                 "both": (True, True, "refused", "unplaced"), "neither": (False, False, "refused", "unplaced")}
        for name, (original_there, staged_there, state, reason) in cases.items():
            with self.subTest(name), fixture() as fx:
                other = fx.stage("shoot-a/IMG_0009.ARW", 0)
                open_one = self.open_entry(fx, "shoot-a/IMG_0001.ARW", 1, "stage", original_there, staged_there)
                results = {r.index: r for r in fx.undo([other, open_one])}
                self.assertEqual((results[1].state, results[1].reason), (state, reason))
                self.assertEqual(results[0].state, "restored")
                self.assertTrue(fx.moved(other))

    def test_rule_40_a_purge_begin_with_no_end_is_placed_by_looking(self):
        cases = {"file gone": (False, "refused", "purged"), "file present": (True, "restored", "")}
        for name, (staged_there, state, reason) in cases.items():
            with self.subTest(name), fixture() as fx:
                open_one = self.open_entry(fx, "shoot-a/IMG_0001.ARW", 0, "purge", False, staged_there)
                result, = fx.undo([open_one])
                self.assertEqual((result.state, result.reason), (state, reason))

    def test_rule_40_a_restore_begin_with_no_end_is_placed_by_looking(self):
        cases = {"at the original": (True, False, "skipped", ""), "at the staged path": (False, True, "restored", ""),
                 "both": (True, True, "refused", "unplaced")}
        for name, (original_there, staged_there, state, reason) in cases.items():
            with self.subTest(name), fixture() as fx:
                open_one = self.open_entry(fx, "shoot-a/IMG_0001.ARW", 0, "restore", original_there, staged_there)
                result, = fx.undo([open_one])
                self.assertEqual((result.state, result.reason), (state, reason))

    def test_rule_40_a_reject_begin_and_end_with_family_two_restores_the_reject(self):
        with fixture() as fx:
            reject = fx.stage("shoot-a/IMG_0001.ARW", 0, "reject", 2)
            common = {"row": 0, "original": reject.original}
            lines = [{"event": "begin", "op": "stage", "kind": "reject", "staged": reject.staged, "family": 2,
                      "bytes": reject.bytes, "mtime_ns": reject.mtime_ns, "file_index": reject.file_index, **common},
                     {"event": "end", "op": "stage", "state": "staged", "bytes": reject.bytes,
                      "mtime_ns": reject.mtime_ns, "file_index": reject.file_index, **common}]
            result, = fx.undo(entries(lines))
            self.assertEqual((result.state, result.files), ("restored", (reject.original,)))
            self.assertTrue(fx.moved(reject))


class UndoJournalTests(unittest.TestCase):
    def test_rule_39_start_line_first_and_begin_on_disk_inside_the_rename(self):
        with fixture() as fx:
            reject = fx.stage("shoot-a/IMG_0001.ARW", 0)
            seen = []
            fx.use_recorder(lambda: seen.append(read_journal(fx.journal_path, fx.ctx)))
            fx.undo([reject], start={"note": "x"})
            self.assertEqual([line["event"] for line in seen[0]], ["start", "begin"])
            self.assertEqual((seen[0][0]["op"], seen[0][0]["batch"], seen[0][0]["note"]), ("undo", BATCH, "x"))
            self.assertEqual([line["event"] for line in read_journal(fx.journal_path, fx.ctx)],
                             ["start", "begin", "end"])

    def test_rule_39_the_restore_begin_holds_what_place_and_entries_read_and_the_next_undo_restores(self):
        with fixture() as fx:
            reject = fx.stage("shoot-a/IMG_0001.ARW", 0, "reject", 2)

            def fail():
                raise OSError("rename failed")
            fx.use_recorder(fail)
            with self.assertRaises(OSError):
                fx.undo([reject])
            lines = read_journal(fx.journal_path, fx.ctx)
            self.assertEqual([line["event"] for line in lines], ["start", "begin"])
            self.assertEqual(lines[1]["op"], "restore")
            interrupted, = entries(lines)  # the begin alone: row, kind, original, staged, bytes, mtime, index, sha
            self.assertEqual(interrupted, reject._replace(family=0, state="open_restore"))
            fx.use_recorder()
            result, = fx.undo([interrupted])
            self.assertEqual((result.state, result.files), ("restored", (reject.original,)))
            self.assertTrue(fx.moved(reject))

    def test_rule_40_a_second_undo_finds_nothing_to_do(self):
        with fixture() as fx:
            reject, sidecar = fx.stage("shoot-a/IMG_0001.ARW", 0, "reject", 2), fx.stage("shoot-a/a.xmp", 0, "sidecar", 2)
            self.assertEqual(fx.undo([reject, sidecar])[0].state, "restored")
            again = entries(read_journal(fx.journal_path, fx.ctx))
            self.assertEqual({e.state for e in again}, {"restored"})
            fx.use_recorder()
            result, = fx.undo(again)
            self.assertEqual((result.state, result.reason), ("skipped", ""))
            self.assertEqual(fx.renames, [])

    def test_rule_40_a_dry_run_reports_and_writes_nothing(self):
        with fixture() as fx:
            good = fx.stage("shoot-a/IMG_0001.ARW", 0)
            bad = fx.stage("shoot-a/IMG_0002.ARW", 1)
            fx.world.add("shoot-a/IMG_0002.ARW", b"new", MTIME)
            fx.use_recorder()
            results = fx.undo([good, bad], apply=False)
            self.assertEqual([(r.index, r.state, r.reason) for r in results],
                             [(1, "refused", "original_exists"), (0, "would_restore", "")])
            self.assertEqual(results[1].files, (good.original,))
            self.assertEqual(fx.renames, [])
            self.assertFalse(os.path.exists(fx.journal_path))
            self.assertTrue(os.path.exists(good.staged) and not os.path.exists(good.original))


if __name__ == "__main__":
    unittest.main()
