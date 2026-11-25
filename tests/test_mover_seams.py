import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from lrclean import hashing, mover_seams, store
from lrclean.mover_seams import Stopped, WriteRefused
from mover_world import Outside, World

WAIT = 10  # seconds a test waits for a thread before it fails


class WorldCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.world = World(tmp.name)
        self.ctx = self.world.ctx()
        self.folders = self.ctx.folders
        base = self.world.base
        self.dirs = {"journal": os.path.join(base, "work", "journal"), "out": os.path.join(base, "out"),
                     "staging": os.path.join(base, "work", "staging"), "source": self.world.photo_root}


class AllowedTests(WorldCase):
    def test_rule_38_each_kind_allows_a_file_in_its_own_folder(self):
        for kind, folder in self.dirs.items():
            with self.subTest(kind):
                self.assertTrue(mover_seams.allowed(self.folders, os.path.join(folder, "a", "f.txt"), kind))

    def test_rule_38_each_kind_refuses_a_path_of_another_kind(self):
        for kind in self.dirs:
            for other, folder in self.dirs.items():
                if other != kind:
                    with self.subTest(kind=kind, other=other):
                        self.assertFalse(mover_seams.allowed(self.folders, os.path.join(folder, "f"), kind))

    def test_rule_38_a_sibling_folder_with_a_common_prefix_is_not_under_the_folder(self):
        for kind, folder in self.dirs.items():
            with self.subTest(kind):
                self.assertFalse(mover_seams.allowed(self.folders, folder + "-x/f", kind))

    def test_rule_38_the_write_folder_itself_is_allowed_but_the_photo_root_itself_is_not_and_a_climb_is_refused(self):
        for kind, folder in self.dirs.items():
            with self.subTest(kind=kind, case="folder"):
                self.assertEqual(mover_seams.allowed(self.folders, folder, kind), kind != "source")
            with self.subTest(kind=kind, case="climb"):
                self.assertFalse(mover_seams.allowed(self.folders, os.path.join(folder, "..", "..", "f"), kind))

    def test_rule_38_a_path_that_is_not_absolute_is_refused_even_if_it_resolves_inside_the_folder(self):
        for kind, folder in self.dirs.items():
            with self.subTest(kind=kind, case="bare"):
                self.assertFalse(mover_seams.allowed(self.folders, "f", kind))
        inside = os.path.join(self.dirs["journal"], "f")
        try:
            relative = os.path.relpath(inside)
        except ValueError:
            self.skipTest("the temporary folder is on another drive than the working folder")
        self.assertFalse(os.path.isabs(relative))
        self.assertFalse(mover_seams.allowed(self.folders, relative, "journal"))

    def test_rule_38_a_name_in_another_case_or_with_forward_slashes_is_the_same_folder(self):
        for kind, folder in self.dirs.items():
            with self.subTest(kind=kind, case="upper"):
                self.assertTrue(mover_seams.allowed(self.folders, os.path.join(folder.upper(), "f"), kind))
            with self.subTest(kind=kind, case="slashes"):
                self.assertTrue(mover_seams.allowed(self.folders, folder.replace("\\", "/") + "/f", kind))

    def test_rule_38_an_archive_root_and_a_backup_root_are_refused_for_every_kind(self):
        backup_root = os.path.join(self.world.tmp, "backup-1")
        for root in (*self.world.archive_roots, backup_root):
            for kind in self.dirs:
                with self.subTest(root=os.path.basename(root), kind=kind):
                    self.assertFalse(mover_seams.allowed(self.folders, os.path.join(root, "f"), kind))

    def test_rule_38_source_refuses_a_path_outside_photo_root(self):
        outside = os.path.join(self.world.tmp, "elsewhere", "f")
        self.assertFalse(mover_seams.allowed(self.folders, outside, "source"))

    def test_rule_38_an_unknown_kind_is_refused(self):
        self.assertFalse(mover_seams.allowed(self.folders, os.path.join(self.dirs["out"], "f"), "other"))

    def test_rule_38_check_write_raises_write_refused_for_a_refused_path_only(self):
        mover_seams.check_write(self.folders, os.path.join(self.dirs["out"], "f"), "out")
        with self.assertRaises(WriteRefused):
            mover_seams.check_write(self.folders, os.path.join(self.dirs["out"], "f"), "staging")


class GuardTests(WorldCase):
    def test_rule_43_guard_passes_while_running_and_refuses_a_bad_path(self):
        mover_seams.guard(self.ctx, os.path.join(self.dirs["staging"], "f"), "staging")
        with self.assertRaises(WriteRefused):
            mover_seams.guard(self.ctx, os.path.join(self.dirs["out"], "f"), "staging")

    def test_rule_43_guard_raises_stopped_once_stop_is_set(self):
        self.ctx.stop.set()
        for kind, folder in self.dirs.items():
            with self.subTest(kind), self.assertRaises(Stopped):
                mover_seams.guard(self.ctx, os.path.join(folder, "f"), kind)

    def test_rule_43_a_stopped_guard_still_refuses_a_bad_path_as_write_refused(self):
        self.ctx.stop.set()
        with self.assertRaises(WriteRefused):
            mover_seams.guard(self.ctx, os.path.join(self.dirs["out"], "f"), "staging")

    def test_rule_43_check_write_alone_passes_once_stop_is_set(self):
        self.ctx.stop.set()
        mover_seams.check_write(self.folders, os.path.join(self.dirs["journal"], "f"), "journal")

    def test_rule_43_new_ctx_gives_each_ctx_its_own_unset_event(self):
        other = self.world.ctx()
        self.ctx.stop.set()
        self.assertFalse(other.stop.is_set())


class WatchTests(WorldCase):
    """`watch` runs `work` on a thread; a fake clock and a fake abort stand in for time and `os._exit`."""

    def setUp(self):
        super().setUp()
        self.events = []
        self.release = threading.Event()  # lets a stalled work go on once abort was called
        self.after = threading.Event()  # set when work has run to its end
        self.late = []
        self.fields = []

        def abort(code):
            self.events.append(("abort", code))
            self.release.set()

        self.world.abort = abort
        self.ctx = self.world.ctx()

    def journal_write(self, event, **fields):
        self.events.append((event, self.ctx.stop.is_set(), sorted(fields)))
        self.fields.append(fields)

    def scripted_clock(self):
        """A clock the work moves by hand; `reads` counts its calls, so work can wait for the watcher to poll."""
        self.now = [0.0]
        self.reads = []

        def clock():
            self.reads.append(self.now[0])
            return self.now[0]

        self.world.clock = clock
        self.ctx = self.world.ctx()

    def wait_for_polls(self, count):
        """Blocks the work thread until the clock was read `count` more times (or the watcher aborted)."""
        target = len(self.reads) + count
        for _ in range(WAIT * 1000):
            if len(self.reads) >= target or self.release.wait(0.001):
                return

    def watch(self, work, stall_seconds=3):
        return mover_seams.watch(work, ctx=self.ctx, stall_seconds=stall_seconds,
                                 journal_write=self.journal_write, poll=0.001)

    def late_work(self, ticks=0):
        """Work that ticks `ticks` times, waits for the abort, then tries every late step."""
        def work(tick):
            for _ in range(ticks):
                tick()
            self.assertTrue(self.release.wait(WAIT))
            try:
                for name, step in (
                    ("tick", tick),
                    ("journal", lambda: mover_seams.guard(self.ctx, os.path.join(self.dirs["journal"], "f"), "journal")),
                    ("rename", lambda: mover_seams.guard(self.ctx, os.path.join(self.dirs["staging"], "f"), "staging")),
                    ("remove", lambda: mover_seams.guard(self.ctx, os.path.join(self.dirs["source"], "f"), "source")),
                ):
                    try:
                        step()
                        self.late.append((name, "passed"))
                    except Stopped:
                        self.late.append((name, "Stopped"))
            finally:
                self.after.set()
        return work

    def test_rule_43_a_work_that_never_ticks_is_cut_at_the_limit(self):
        self.world.ticks = 0  # the clock advances one second per call
        result = self.watch(self.late_work(), stall_seconds=3)
        self.assertEqual(result, (None, True))
        self.assertTrue(self.after.wait(WAIT))
        self.assertEqual(self.events[-1], ("abort", 5))

    def test_rule_43_the_stop_flag_is_set_before_the_stalled_line_and_the_line_before_abort(self):
        self.watch(self.late_work())
        self.assertEqual(self.events, [("stalled", True, ["seconds"]), ("abort", 5)])
        self.assertTrue(self.after.wait(WAIT))

    def test_rule_43_a_work_that_ticks_then_stops_is_cut(self):
        result = self.watch(self.late_work(ticks=4))
        self.assertEqual(result, (None, True))
        self.assertEqual([event[0] for event in self.events], ["stalled", "abort"])
        self.assertTrue(self.after.wait(WAIT))

    def test_rule_43_a_work_that_ticks_to_the_end_finishes(self):
        self.scripted_clock()

        def work(tick):
            for _ in range(4):  # 8 s in all against a 3 s limit, but 2 s between two ticks
                self.now[0] += 2
                tick()
                self.wait_for_polls(3)  # the watcher polls (tick reads the clock once too) before the next tick
            return "done"

        self.assertEqual(self.watch(work), ("done", False))
        self.assertEqual(self.events, [])
        self.assertGreaterEqual(len(self.reads), 12)

    def test_rule_43_the_stalled_line_carries_the_seconds_since_the_last_tick(self):
        self.scripted_clock()

        def work(tick):
            self.now[0] = 7.0
            self.assertTrue(self.release.wait(WAIT))
            self.after.set()

        self.assertEqual(self.watch(work, stall_seconds=3), (None, True))
        self.assertTrue(self.after.wait(WAIT))
        self.assertEqual(self.fields, [{"seconds": 7.0}])

    def test_rule_43_a_gap_of_exactly_the_limit_is_not_a_stall_as_in_the_backup(self):
        self.scripted_clock()

        def work(tick):
            self.now[0] = 3.0  # exactly stall_seconds since the start
            self.wait_for_polls(3)
            return "done"

        self.assertEqual(self.watch(work, stall_seconds=3), ("done", False))
        self.assertEqual(self.events, [])

    def test_rule_43_a_tick_landing_after_the_first_look_at_the_clock_cancels_the_stall(self):
        reads, looked, ticked = [], threading.Event(), threading.Event()
        now = [0.0]

        def clock():
            reads.append(1)
            if len(reads) == 2:  # the watcher's first look sees 10 s of silence
                now[0] = 10.0
            if len(reads) == 3:  # a tick lands before the watcher's second look at the clock
                looked.set()
                self.assertTrue(ticked.wait(WAIT))
            return now[0]

        self.world.clock = clock
        self.ctx = self.world.ctx()

        def work(tick):
            self.assertTrue(looked.wait(WAIT))
            tick()
            ticked.set()
            return "done"

        self.assertEqual(self.watch(work, stall_seconds=3), ("done", False))
        self.assertEqual(self.events, [])
        self.assertFalse(self.ctx.stop.is_set())

    def test_rule_43_after_the_stall_tick_and_every_guard_raise_stopped_and_nothing_is_written(self):
        self.watch(self.late_work())
        self.assertTrue(self.after.wait(WAIT))
        self.assertEqual(self.late, [("tick", "Stopped"), ("journal", "Stopped"),
                                     ("rename", "Stopped"), ("remove", "Stopped")])

    def test_rule_43_an_exception_of_work_is_re_raised_and_nothing_is_aborted(self):
        def work(tick):
            tick()
            raise KeyError("boom")

        with self.assertRaises(KeyError):
            self.watch(work)
        self.assertEqual(self.events, [])
        self.assertFalse(self.ctx.stop.is_set())


class WriterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_rule_43_append_appends_and_fsyncs(self):
        path = os.path.join(self.dir, "journal.jsonl")
        with mock.patch.object(mover_seams.os, "fsync") as fsync:
            mover_seams.append(path, b"one\n")
            mover_seams.append(path, b"two\n")
        self.assertEqual(fsync.call_count, 2)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"one\ntwo\n")

    def test_rule_43_write_replaces_the_file_whole_and_leaves_no_temporary_file(self):
        path = os.path.join(self.dir, "out.csv")
        mover_seams.write(path, b"old")
        mover_seams.write(path, b"new")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"new")
        self.assertEqual(os.listdir(self.dir), ["out.csv"])
        sources = []
        real_replace = os.replace

        def replace(source, target):
            sources.append(source)
            real_replace(source, target)

        with mock.patch.object(mover_seams.os, "replace", side_effect=replace):
            mover_seams.write(path, b"third")
        self.assertEqual([os.path.normcase(os.path.dirname(s)) for s in sources],
                         [os.path.normcase(self.dir)])

    def test_rule_43_write_leaves_no_temporary_file_and_keeps_the_old_one_when_it_fails(self):
        path = os.path.join(self.dir, "out.csv")
        mover_seams.write(path, b"old")
        with mock.patch.object(mover_seams.os, "replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                mover_seams.write(path, b"new")
        self.assertEqual(os.listdir(self.dir), ["out.csv"])
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"old")

    def test_rule_43_write_never_touches_a_file_that_is_already_named_like_its_temporary_file(self):
        path = os.path.join(self.dir, "out.csv")
        other = path + ".tmp"
        with open(other, "wb") as handle:
            handle.write(b"not ours")
        mover_seams.write(path, b"new")
        with mock.patch.object(mover_seams.os, "replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                mover_seams.write(path, b"newer")
        self.assertEqual(sorted(os.listdir(self.dir)), ["out.csv", "out.csv.tmp"])
        with open(other, "rb") as handle:
            self.assertEqual(handle.read(), b"not ours")

    def test_rule_43_write_fsyncs_the_temporary_file_before_it_replaces_the_target(self):
        calls = []
        real_replace = os.replace

        def replace(source, target):
            calls.append("replace")
            real_replace(source, target)

        with mock.patch.object(mover_seams.os, "fsync", side_effect=lambda fd: calls.append("fsync")), \
                mock.patch.object(mover_seams.os, "replace", side_effect=replace):
            mover_seams.write(os.path.join(self.dir, "out.csv"), b"x")
        self.assertEqual(calls, ["fsync", "replace"])


class RealSeamsTests(unittest.TestCase):
    def test_rule_38_real_members_are_the_frozen_objects(self):
        real = mover_seams.REAL
        self.assertEqual(
            (real.stat, real.attrs_of, real.opener, real.rename, real.remove, real.rmdir, real.makedirs,
             real.listdir, real.append, real.write, real.clock, real.abort, real.now),
            (os.stat, None, hashing.open_binary, os.rename, os.remove, os.rmdir, os.makedirs, os.listdir,
             mover_seams.append, mover_seams.write, time.monotonic, os._exit, store.utc_now))

    def make_dirs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = os.path.join(tmp.name, "target")
        os.mkdir(target)
        return tmp.name, target

    def test_rule_70_is_link_is_false_for_a_plain_folder(self):
        _, target = self.make_dirs()
        self.assertFalse(mover_seams.REAL.is_link(target))

    @unittest.skipUnless(os.name == "nt", "junctions exist on Windows only")
    def test_rule_70_is_link_is_true_for_a_junction(self):
        tmp, target = self.make_dirs()
        link = os.path.join(tmp, "junction")
        subprocess.run(["cmd", "/c", "mklink", "/J", link, target], check=True, capture_output=True, timeout=30)
        self.addCleanup(os.rmdir, link)  # runs before the temporary folder is removed
        self.assertTrue(mover_seams.REAL.is_link(link))

    def test_rule_70_is_link_is_true_for_a_symbolic_link(self):
        tmp, target = self.make_dirs()
        link = os.path.join(tmp, "symlink")
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"creating a symbolic link is not permitted here: {exc}")
        self.addCleanup(os.rmdir, link)
        self.assertTrue(mover_seams.REAL.is_link(link))


class WorldSwapTests(WorldCase):
    def test_rule_38_world_ctx_still_raises_outside_for_each_seam_after_the_swap(self):
        outside = os.path.join(os.path.dirname(self.world.tmp), "not-the-tree", "IMG_0001.ARW")
        seams, tree = self.ctx.seams, self.world.photo_root
        self.assertIsInstance(self.ctx.seams, mover_seams.Seams)
        self.assertFalse(self.ctx.stop.is_set())
        calls = {
            "stat": seams.stat, "opener": seams.opener, "remove": seams.remove, "rmdir": seams.rmdir,
            "makedirs": seams.makedirs, "listdir": seams.listdir,
            "rename source": lambda p: seams.rename(p, tree), "rename target": lambda p: seams.rename(tree, p),
            "append": lambda p: seams.append(p, b"x"), "write": lambda p: seams.write(p, b"x"),
        }
        for name, call in calls.items():
            with self.subTest(name), self.assertRaises(Outside):
                call(outside)


if __name__ == "__main__":
    unittest.main()
