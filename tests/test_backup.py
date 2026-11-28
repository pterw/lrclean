import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from lrclean import backup_plan, hashing, paths
from lrclean.backup import (
    PART_SUFFIX,
    Journal,
    Result,
    RunOutcome,
    check_manifest,
    copy_row,
    dry_run,
    exit_code,
    journal_name,
    open_part,
    read_verified,
    run_manifest,
    source_changed,
)
from lrclean.backup_plan import ManifestError, Row
from lrclean.paths import norm


def _listing(top):
    return sorted(os.path.join(d, n) for d, ds, fs in os.walk(top) for n in ds + fs)


class CheckManifestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.arc = os.path.join(self.top, "arc-1")
        self.arc2 = os.path.join(self.top, "arc-2")
        self.lib = os.path.join(self.top, "lib")
        for folder in (self.arc, self.arc2, self.lib):
            os.makedirs(folder)

    def _row(self, dest, size=5, action="copy"):
        return Row(os.path.join(self.lib, "shoot-a", "IMG_0001.ARW"), dest, size, 7, action, "")

    def _check(self, rows, *, archive_roots=None, library_roots=None, free=10**9, exists=os.path.exists):
        return check_manifest(
            rows,
            archive_roots=archive_roots or [self.arc],
            library_roots=library_roots or [self.lib],
            free_bytes=lambda _path: free,
            exists=exists,
        )

    def test_rule16_valid_manifest_returns_one_based_archive_number(self):
        rows = [self._row(os.path.join(self.arc2, "shoot-a", "IMG_0001.ARW"))]
        self.assertEqual(self._check(rows, archive_roots=[self.arc, self.arc2]), 2)

    def test_rule16_bad_dest_or_duplicate_raises_and_creates_nothing(self):
        good = os.path.join(self.arc, "shoot-a", "IMG_0001.ARW")
        slashed = good.replace("\\", "/")  # the other separator: a genuinely different spelling
        cases = {
            "outside every archive root": [self._row(os.path.join(self.top, "elsewhere", "a.ARW"))],
            "dotdot escape": [self._row(self.arc + "/../elsewhere/a.ARW")],
            "relative": [self._row("shoot-a/a.ARW")],
            "equal to the root": [self._row(self.arc)],
            "other archive root": [self._row(good), self._row(os.path.join(self.arc2, "a.ARW"))],
            "same dest twice": [self._row(good), self._row(good)],
            "dest differing in case": [self._row(good), self._row(good.upper())],
            "dest differing in slashes": [self._row(good), self._row(slashed)],
        }
        cases["negative size"] = [self._row(good, size=-1)]
        self.assertNotEqual(slashed, good)
        before = _listing(self.top)
        for name, rows in cases.items():
            with self.subTest(name), self.assertRaises(ManifestError):
                self._check(rows, archive_roots=[self.arc, self.arc2] if name == "other archive root" else None)
        self.assertEqual(_listing(self.top), before)

    def test_rule18_dest_named_like_a_part_file_is_refused_by_its_last_component_only(self):
        shoot = os.path.join(self.arc, "shoot-a")
        astral_fits = "\U0001F4F7" * 121
        astral_too_long = "\U0001F4F7" * 122
        cases = {
            "exact suffix": (os.path.join(shoot, "IMG_0001.ARW.lrclean-part"), True),
            "suffix in another case": (os.path.join(shoot, "IMG_0001.ARW.LRCLEAN-PART"), True),
            "text in the middle": (os.path.join(shoot, "IMG_0001.lrclean-part.bak"), False),
            "folder component with the suffix": (os.path.join(shoot, "x.lrclean-part", "IMG_0001.ARW"), False),
            "name ending in a dot": (os.path.join(shoot, "IMG_0001.ARW."), True),
            "name ending in a space": (os.path.join(shoot, "IMG_0001.ARW "), True),
            "suffix then a dot": (os.path.join(shoot, "IMG_0001.ARW.lrclean-part."), True),
            "suffix then a space": (os.path.join(shoot, "IMG_0001.ARW.lrclean-part "), True),
            "colon, a stream": (os.path.join(shoot, "a.ARW:x"), True),
            "colon, the data stream": (os.path.join(shoot, "a.ARW::$DATA"), True),
            "dot and space in the middle": (os.path.join(shoot, "my photo.v2.ARW"), False),
            "folder component ending in a dot": (os.path.join(shoot, "x.", "IMG_0001.ARW"), False),
            "243 ASCII characters": (os.path.join(shoot, "a" * 243), True),
            "242 ASCII characters": (os.path.join(shoot, "a" * 242), False),
            "121 astral characters": (os.path.join(shoot, astral_fits), False),
            "122 astral characters": (os.path.join(shoot, astral_too_long), True),
        }
        for name, (dest, refused) in cases.items():
            with self.subTest(name):
                rows = [self._row(dest)]
                if refused:
                    with self.assertRaises(ManifestError) as caught:
                        self._check(rows)
                    self.assertIn(dest, str(caught.exception))
                    self.assertIn(rows[0].source, str(caught.exception))
                else:
                    self.assertEqual(self._check(rows), 1)
        self.assertNotEqual(paths.name_units(astral_fits), len(astral_fits))
        self.assertNotEqual(paths.name_units(astral_too_long), len(astral_too_long))

    def test_rule16_dest_in_library_root_raises_even_inside_an_archive_root(self):
        # The archive root contains the library root, so only the library check refuses.
        rows = [self._row(os.path.join(self.lib, "shoot-a", "copy.ARW"))]
        with self.assertRaises(ManifestError):
            self._check(rows, archive_roots=[self.top])
        # A link in the library leading out of it: only the path text puts this dest in the library.
        self._junction(os.path.join(self.lib, "jarc"), self.arc)
        dest = os.path.join(self.lib, "jarc", "IMG_0002.ARW")
        self.assertTrue(backup_plan.is_inside(dest, self.lib))
        self.assertFalse(backup_plan.is_inside(os.path.realpath(os.path.dirname(dest)), self.lib))
        with self.assertRaises(ManifestError):
            self._check([self._row(dest)], archive_roots=[self.top])

    def _junction(self, link, target):
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(os.rmdir, link)  # the link only, never the target

    def test_rule17_dest_below_a_link_into_the_library_is_refused_and_nothing_is_written(self):
        self._junction(os.path.join(self.arc, "jlib"), self.lib)
        dest = os.path.join(self.arc, "jlib", "shoot-a", "IMG_0001.ARW")
        self.assertFalse(backup_plan.is_inside(dest, self.lib))  # the path text alone passes
        lib_before, arc_before = _listing(self.lib), _listing(self.arc)
        with self.assertRaises(ManifestError) as caught:
            self._check([self._row(dest)])
        self.assertIn(dest, str(caught.exception))
        self.assertEqual((_listing(self.lib), _listing(self.arc)), (lib_before, arc_before))

    def test_rule17_dest_below_a_link_is_refused_only_when_it_leads_out_of_the_archive_root(self):
        long_folder = os.path.join(self.arc, "shoot-long", *["d" * 50] * 5)
        os.makedirs(paths.fs_path(long_folder))
        long_dest = os.path.join(long_folder, "IMG_0004.ARW")
        jlib, jout = os.path.join(self.top, "arc-jlib"), os.path.join(self.top, "arc-jout")
        lib_link = os.path.join(self.top, "lib-link")
        self._junction(lib_link, self.lib)
        self.assertNotEqual(norm(lib_link), norm(self.lib))
        # Each row: (junction target or None, folder holding the dest, archive root or None for arc-1, refused).
        cases = {
            "library root is a link to the library": (self.lib, os.path.join(self.arc, "jlib2"), self.top, True),
            "link to a folder in the archive root": (os.path.join(self.arc, "real"), os.path.join(self.arc, "jreal"), None, False),
            "link to a folder outside both roots": (os.path.join(self.top, "elsewhere"), os.path.join(self.arc, "jout"), None, True),
            "ordinary folder": (None, os.path.join(self.arc, "shoot-a"), None, False),
            "archive root is a link into the library": (self.lib, jlib, jlib, True),
            "archive root is a link to a folder outside both roots": (os.path.join(self.top, "outside"), jout, jout, False),
        }
        for name, (target, folder, root, refused) in cases.items():
            with self.subTest(name):
                if target is not None:
                    os.makedirs(target, exist_ok=True)
                    self._junction(folder, target)
                dest = os.path.join(folder, "IMG_0001.ARW")
                roots = [root] if root else None
                libraries = [lib_link] if name == "library root is a link to the library" else None
                # Only the resolved folder can decide: the path text of the dest is outside the library.
                self.assertFalse(backup_plan.is_inside(dest, self.lib))
                self.assertFalse(backup_plan.is_inside(dest, lib_link))
                if refused:
                    with self.assertRaises(ManifestError) as caught:
                        self._check([self._row(dest)], archive_roots=roots, library_roots=libraries)
                    self.assertIn(dest, str(caught.exception))
                else:
                    self.assertEqual(self._check([self._row(dest)], archive_roots=roots, library_roots=libraries), 1)
        with self.subTest("existing folder over 260 characters"):
            self.assertGreater(len(long_dest), 260)
            self.assertEqual(self._check([self._row(long_dest)]), 1)

    def test_rule16_free_space_boundary_equal_passes_one_above_raises(self):
        rows = [
            self._row(os.path.join(self.arc, "a.ARW"), size=6),
            self._row(os.path.join(self.arc, "b.ARW"), size=4),
            self._row(os.path.join(self.arc, "c.ARW"), size=99, action="skip_placeholder"),
        ]
        self.assertEqual(self._check(rows, free=10), 1)
        with self.assertRaises(ManifestError):
            self._check(rows, free=9)

    def test_rule16_free_space_is_read_from_the_archive_root_the_manifest_targets(self):
        rows = [self._row(os.path.join(self.arc2, "a.ARW"), size=5)]
        roots = [self.arc, self.arc2]

        def check(free):
            return check_manifest(
                rows,
                archive_roots=roots,
                library_roots=[self.lib],
                free_bytes=lambda path: free[path],
            )

        with self.assertRaises(ManifestError):
            check({self.arc: 10**9, self.arc2: 4})
        self.assertEqual(check({self.arc: 0, self.arc2: 5}), 2)

    def test_rule16_existing_dest_does_not_count_toward_free_space(self):
        done = os.path.join(self.arc, "done.ARW")
        new = os.path.join(self.arc, "new.ARW")
        with open(done, "wb") as handle:
            handle.write(b"x")
        rows = [self._row(done, size=1000), self._row(new, size=3)]
        self.assertEqual(self._check(rows, free=3), 1)


class SourceChangedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "IMG_0001.ARW")
        with open(self.path, "wb") as handle:
            handle.write(b"12345")
        st = os.stat(self.path)
        self.row = Row(self.path, "D:/arc/IMG_0001.ARW", st.st_size, st.st_mtime_ns, "copy", "")

    def test_rule20_unchanged_source_is_not_changed_and_nothing_is_created(self):
        before = _listing(os.path.dirname(self.path))
        self.assertFalse(source_changed(self.row))
        self.assertEqual(_listing(os.path.dirname(self.path)), before)

    def test_rule20_gone_longer_retimed_or_placeholder_source_is_changed(self):
        longer = self.row._replace(bytes=self.row.bytes - 1)
        retimed = self.row._replace(mtime_ns=self.row.mtime_ns + 1)
        gone = self.row._replace(source=self.path + ".gone")
        self.assertTrue(source_changed(gone))
        self.assertTrue(source_changed(longer))
        self.assertTrue(source_changed(retimed))
        self.assertTrue(source_changed(self.row, attrs_of=lambda _path, _st: 0x400000))

    def test_rule20_attributes_come_from_the_stat_result_and_attrs_of_gets_source_then_stat(self):
        real = os.stat(self.path)
        placeholder = SimpleNamespace(
            st_size=real.st_size, st_mtime_ns=real.st_mtime_ns, st_file_attributes=0x400000
        )
        self.assertTrue(source_changed(self.row, stat=lambda _path: placeholder))
        calls = []

        def attrs_of(path, st):
            calls.append((path, st))
            return 0

        self.assertFalse(source_changed(self.row, attrs_of=attrs_of))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], self.path)
        self.assertEqual(calls[0][1].st_size, real.st_size)


MIB = 1024 * 1024


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _tree(top):
    """name, size, mtime_ns and SHA-256 of every file under top."""
    found = []
    for folder, _dirs, files in os.walk(top):
        for name in files:
            path = os.path.join(folder, name)
            st = os.stat(path)
            found.append((path, st.st_size, st.st_mtime_ns, _digest(path)))
    return sorted(found)


def _parts(top):
    return [n for _d, _ds, fs in os.walk(top) for n in fs if n.endswith(PART_SUFFIX)]


class _Recorder:
    """Wraps a real read-only handle and records every call made on it."""

    def __init__(self, handle, calls, fail_read=False):
        self._handle, self._calls, self._fail_read = handle, calls, fail_read

    def __enter__(self):
        self._calls.append("__enter__")
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        self._calls.append("__exit__")
        return self._handle.__exit__(*exc)

    def read(self, size=-1):
        self._calls.append("read")
        if self._fail_read:
            raise OSError("read refused")
        return self._handle.read(size)

    def __getattr__(self, name):
        self._calls.append(name)
        return getattr(self._handle, name)


class _Writer:
    """Wraps a part-file handle; flips the first byte, raises on write or flush, logs flush and fileno."""

    def __init__(self, handle, flip=False, fail="", exc=OSError, events=None):
        self._handle, self._flip, self._fail, self._exc = handle, flip, fail, exc
        self._events = [] if events is None else events

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def write(self, data):
        if self._fail == "write":
            raise self._exc("write refused")
        if self._flip:
            data = bytes([data[0] ^ 1]) + data[1:]
            self._flip = False
        return self._handle.write(data)

    def flush(self):
        self._events.append("flush")
        if self._fail == "flush":
            raise OSError("flush refused")
        return self._handle.flush()

    def fileno(self):
        self._events.append("fileno")
        return self._handle.fileno()


class CopyRowTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lib = os.path.join(tmp.name, "lib")
        self.arc = os.path.join(tmp.name, "arc-1")
        os.makedirs(self.lib)
        os.makedirs(self.arc)

    def _row(self, data=b"abcde" * 50, name="shoot-a", dest_data=None):
        source = os.path.join(self.lib, name, "IMG_0001.ARW")
        os.makedirs(os.path.dirname(source), exist_ok=True)
        with open(source, "wb") as handle:
            handle.write(data)
        st = os.stat(source)
        dest = os.path.join(self.arc, name, "IMG_0001.ARW")
        if dest_data is not None:
            os.makedirs(os.path.dirname(dest))
            with open(dest, "wb") as handle:
                handle.write(dest_data)
        return Row(source, dest, st.st_size, st.st_mtime_ns, "copy", "")

    def _recording_opener(self, calls, fail_read_of=None):
        def opener(path):
            return _Recorder(hashing.open_binary(path), calls, fail_read=path == fail_read_of)

        return opener

    def test_rule17_20_every_state_leaves_the_source_untouched_and_only_reads_it(self):
        retimed = self._row(name="c-changed")
        retimed = retimed._replace(mtime_ns=retimed.mtime_ns + 1)
        placeholder = self._row(name="c-placeholder", dest_data=b"other")  # different bytes: not a conflict
        cases = {
            "verified": (self._row(name="c-verified"), {}),
            "already_there": (self._row(name="c-there", dest_data=b"abcde" * 50), {}),
            "conflict": (self._row(name="c-conflict", dest_data=b"other"), {}),
            "changed": (retimed, {}),
            "failed": (self._row(name="c-failed"), {"rename": mock.Mock(side_effect=OSError)}),
            "dest_placeholder": (placeholder, {"attrs_of": lambda path, _st: 0x400000 * (path == placeholder.dest)}),
        }
        self.assertNotEqual(cases["dest_placeholder"][1]["attrs_of"](placeholder.dest, None), 0)
        self.assertEqual(cases["dest_placeholder"][1]["attrs_of"](placeholder.source, None), 0)
        for state, (row, seams) in cases.items():
            with self.subTest(state):
                calls = []
                before = _tree(self.lib)
                result, _sha = copy_row(row, opener=self._recording_opener(calls), **seams)
                self.assertEqual(result, state)
                if state == "dest_placeholder":
                    self.assertEqual(calls, [])
                self.assertEqual(_tree(self.lib), before)
                self.assertLessEqual(set(calls), {"read", "__enter__", "__exit__"})

    def test_rule20_gone_placeholder_longer_or_retimed_source_is_changed_and_creates_nothing(self):
        row = self._row()
        cases = {
            "gone": (row._replace(source=row.source + ".gone"), None),
            "placeholder": (row, lambda _path, _st: 0x400000),
            "longer": (row._replace(bytes=row.bytes - 1), None),
            "retimed": (row._replace(mtime_ns=row.mtime_ns + 1), None),
        }
        for name, (case, attrs_of) in cases.items():
            with self.subTest(name):
                self.assertEqual(copy_row(case, attrs_of=attrs_of), ("changed", ""))
                self.assertEqual(os.listdir(self.arc), [])

    def test_rule20_skip_placeholder_row_is_skipped_without_stat_or_open(self):
        row = self._row()._replace(action="skip_placeholder")
        stat, opener = mock.Mock(), mock.Mock()
        self.assertEqual(copy_row(row, stat=stat, opener=opener), ("skipped_placeholder", ""))
        stat.assert_not_called()
        opener.assert_not_called()
        self.assertEqual(os.listdir(self.arc), [])

    def test_rule18_existing_identical_dest_is_already_there(self):
        row = self._row(dest_data=b"abcde" * 50)
        wrong = row.mtime_ns + 5_000_000_000
        os.utime(row.dest, ns=(wrong, wrong))
        self.assertNotEqual(os.stat(row.dest).st_mtime_ns, row.mtime_ns)
        [(_path, size, _mtime, digest)] = _tree(self.arc)
        state, sha = copy_row(row)
        self.assertEqual((state, sha), ("already_there", _digest(row.source)))
        [(_path, size_after, mtime_after, digest_after)] = _tree(self.arc)
        self.assertEqual((size_after, digest_after), (size, digest))
        self.assertEqual(mtime_after, row.mtime_ns)
        self.assertEqual(_parts(self.arc), [])

    def test_rule18_existing_different_dest_is_conflict_and_untouched(self):
        big = bytes(range(256)) * (2 * MIB // 256)
        middle = bytearray(big)
        middle[MIB + MIB // 2] ^= 1
        cases = {
            "same-size": (b"abcde" * 50, b"vwxyz" * 50),
            "middle-of-large": (big, bytes(middle)),
        }
        for name, (data, dest_data) in cases.items():
            with self.subTest(name):
                row = self._row(data, name=name, dest_data=dest_data)
                wrong = row.mtime_ns + 5_000_000_000
                os.utime(row.dest, ns=(wrong, wrong))
                self.assertNotEqual(os.stat(row.dest).st_mtime_ns, row.mtime_ns)
                before = _tree(self.arc)
                self.assertEqual(copy_row(row)[0], "conflict")
                self.assertEqual(_tree(self.arc), before)
                self.assertEqual(_parts(self.arc), [])

    def test_rule19_copy_equals_source_in_bytes_and_mtime_with_no_part_left(self):
        row = self._row()
        self.assertEqual(copy_row(row), ("verified", _digest(row.source)))
        self.assertEqual(_digest(row.dest), _digest(row.source))
        self.assertEqual(os.stat(row.dest).st_mtime_ns, row.mtime_ns)
        self.assertEqual(_parts(self.arc), [])

    def test_rule19_a_flipped_byte_in_the_part_is_failed_and_leaves_no_dest_or_part(self):
        row = self._row()

        def writer(path):
            return _Writer(open_part(path), flip=True)

        self.assertEqual(copy_row(row, writer=writer), ("failed", ""))
        self.assertFalse(os.path.exists(row.dest))
        self.assertEqual(_parts(self.arc), [])

    def test_rule19_source_edited_before_the_rename_is_changed_and_leaves_no_dest(self):
        row = self._row()
        events = []

        def tick():
            if events and events[-1] == "fsync":
                st = os.stat(row.source)
                with open(row.source, "ab") as handle:
                    handle.write(b"x")
                os.utime(row.source, ns=(st.st_mtime_ns, st.st_mtime_ns))
            events.append("tick")

        with mock.patch("lrclean.backup.os.fsync", lambda _fd: events.append("fsync")):
            self.assertEqual(copy_row(row, tick=tick), ("changed", ""))
        self.assertFalse(os.path.exists(row.dest))
        self.assertEqual(_parts(self.arc), [])

    @unittest.skipUnless(os.name == "nt", "os.rename only refuses to replace on Windows")
    def test_rule19_dest_created_by_someone_else_before_the_rename_is_not_replaced(self):
        row = self._row()
        folder_when_they_wrote = []

        def tick():  # the default rename is used: the other party writes once the part exists
            if not folder_when_they_wrote and os.path.exists(row.dest + PART_SUFFIX):
                folder_when_they_wrote.append(sorted(os.listdir(os.path.dirname(row.dest))))
                with open(row.dest, "wb") as handle:
                    handle.write(b"theirs")

        self.assertEqual(copy_row(row, tick=tick), ("failed", ""))
        self.assertEqual(folder_when_they_wrote, [["IMG_0001.ARW.lrclean-part"]])  # the part's name and place
        with open(row.dest, "rb") as handle:
            self.assertEqual(handle.read(), b"theirs")
        self.assertEqual(_parts(self.arc), [])

    def test_b13_every_oserror_site_is_failed_and_leaves_no_part(self):
        def refuse_open(path):
            raise OSError("open refused")

        def failing_writer(site):
            return lambda path: _Writer(open_part(path), fail=site)

        row = self._row(name="open")
        self.assertEqual(copy_row(row, opener=refuse_open)[0], "failed")
        row = self._row(name="read")
        self.assertEqual(copy_row(row, opener=self._recording_opener([], fail_read_of=row.source))[0], "failed")
        row = self._row(name="folder")
        with open(os.path.join(self.arc, "folder"), "wb") as handle:
            handle.write(b"a file where the folder must be")
        self.assertEqual(copy_row(row)[0], "failed")
        for site in ("write", "flush"):
            row = self._row(name=site)
            self.assertEqual(copy_row(row, writer=failing_writer(site))[0], "failed", site)
        row = self._row(name="rename")
        self.assertEqual(copy_row(row, rename=mock.Mock(side_effect=OSError))[0], "failed")
        row = self._row(name="existing", dest_data=b"abcde" * 50)

        def refuse_dest(path):
            if path == row.dest:
                raise OSError("dest unreadable")
            return hashing.open_binary(path)

        self.assertEqual(copy_row(row, opener=refuse_dest)[0], "failed")
        self.assertEqual(_parts(self.arc), [])

    def test_b13_a_part_that_cannot_be_removed_still_gives_failed(self):
        row = self._row()
        writer = lambda path: _Writer(open_part(path), fail="write")  # noqa: E731
        with mock.patch("lrclean.backup.os.remove", side_effect=OSError("remove refused")):
            self.assertEqual(copy_row(row, writer=writer), ("failed", ""))

    def test_b13_a_non_oserror_removes_the_part_closes_every_handle_and_is_reraised(self):
        row = self._row()
        part = row.dest + PART_SUFFIX
        handles = []

        def opener(path):
            if path == part:
                raise ValueError("opener refused")
            handles.append(hashing.open_binary(path))
            return handles[-1]

        def writer(path):
            handles.append(open_part(path))
            return _Writer(handles[-1], fail="write", exc=ValueError)

        def tick_after(open_count):
            ticks = []

            def tick():
                ticks.append(1)
                if len(ticks) == open_count:
                    raise ValueError("tick refused")

            return tick

        cases = {
            "opener": {"opener": opener},
            "writer": {"writer": writer},
            "tick-after-the-open": {"tick": tick_after(1)},
            "tick-after-a-read": {"tick": tick_after(2)},
        }
        for name, seams in cases.items():
            with self.subTest(name):
                handles.clear()
                seams = {"opener": opener, **seams} if name != "opener" else seams
                with self.assertRaises(ValueError):
                    copy_row(row, **seams)
                self.assertEqual(_parts(self.arc), [])
                self.assertTrue(handles and all(handle.closed for handle in handles))

    def test_b13_utime_failure_keeps_the_verified_dest_and_a_rerun_finds_it_already_there(self):
        row = self._row()
        self.assertEqual(copy_row(row, utime=mock.Mock(side_effect=OSError))[0], "failed")
        self.assertEqual(_digest(row.dest), _digest(row.source))
        self.assertEqual(_parts(self.arc), [])
        wrong = row.mtime_ns + 5_000_000_000
        os.utime(row.dest, ns=(wrong, wrong))
        self.assertNotEqual(os.stat(row.dest).st_mtime_ns, row.mtime_ns)
        self.assertEqual(copy_row(row)[0], "already_there")
        self.assertEqual(os.stat(row.dest).st_mtime_ns, row.mtime_ns)

    def test_rule18_a_utime_that_fails_once_keeps_already_there_and_the_next_run_sets_the_time(self):
        row = self._row(dest_data=b"abcde" * 50)
        later, earlier = row.mtime_ns + 5_000_000_000, row.mtime_ns - 5_000_000_000
        self.assertTrue(earlier < row.mtime_ns < later)
        # Only OSError is caught: any other error from the time set propagates.
        os.utime(row.dest, ns=(later, later))
        with self.assertRaises(ValueError):
            copy_row(row, utime=mock.Mock(side_effect=ValueError))
        for wrong in (later, earlier):
            with self.subTest(wrong=wrong - row.mtime_ns):
                os.utime(row.dest, ns=(wrong, wrong))
                with mock.patch("sys.stderr", io.StringIO()) as err:
                    failing = mock.Mock(side_effect=OSError("denied"))
                    self.assertEqual(copy_row(row, utime=failing)[0], "already_there")
                self.assertIn(row.dest, err.getvalue())
                self.assertIn("denied", err.getvalue())
                self.assertEqual(os.stat(row.dest).st_mtime_ns, wrong)
                self.assertEqual(copy_row(row)[0], "already_there")
                self.assertEqual(os.stat(row.dest).st_mtime_ns, row.mtime_ns)
        unused = mock.Mock()
        self.assertEqual(copy_row(row, utime=unused)[0], "already_there")
        unused.assert_not_called()

    def test_b15_a_tick_follows_every_block_read_and_brackets_the_fsync(self):
        row = self._row(bytes(range(256)) * (3 * MIB // 256), dest_data=bytes(3 * MIB))
        events = []

        recording = self._recording_opener(events)

        def opener(path):
            events.append("open")
            return recording(path)

        def writer(path):
            return _Writer(open_part(path), events=events)

        with mock.patch("lrclean.backup.os.fsync", lambda _fd: events.append("fsync")):
            # An existing, different dest: both hashes of rule 18 go through the wrapper.
            self.assertEqual(copy_row(row, tick=lambda: events.append("tick"), opener=opener)[0], "conflict")
            reads = [i for i, e in enumerate(events) if e == "read"]
            self.assertEqual(len(reads), 8)  # three blocks and the empty end read, per file
            self.assertTrue(all(events[i + 1] == "tick" for i in reads))
            os.remove(row.dest)
            events.clear()
            tick = lambda: events.append("tick")  # noqa: E731
            self.assertEqual(copy_row(row, tick=tick, opener=opener, writer=writer)[0], "verified")
        reads = [i for i, e in enumerate(events) if e == "read"]
        self.assertEqual(len(reads), 8)  # the source read and the read-back of the part
        self.assertTrue(all(events[i + 1] == "tick" for i in reads))
        opens = [i for i, e in enumerate(events) if e == "open"]
        self.assertEqual(len(opens), 2)  # the source and the read-back of the part
        self.assertTrue(all(events[i + 1] == "tick" for i in opens))
        last_flush = len(events) - 1 - events[::-1].index("flush")
        at = events.index("fsync")
        self.assertEqual(events[last_flush : at + 2], ["flush", "tick", "fileno", "fsync", "tick"])


def _end(source, dest, state="verified", sha="aa", size=5, mtime=7, source_bytes=3, source_mtime_ns=4, drop=()):
    entry = {"event": "end", "at": "t", "row": 0, "source": source, "dest": dest, "state": state,
             "sha256": sha, "dest_bytes": size, "dest_mtime_ns": mtime,
             "source_bytes": source_bytes, "source_mtime_ns": source_mtime_ns}  # fmt: skip
    return json.dumps({key: value for key, value in entry.items() if key not in drop})


class JournalTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.path = os.path.join(self.top, "j.jsonl")

    def _lines(self):
        with open(self.path, encoding="utf-8", newline="\n") as handle:
            return handle.read().split("\n")

    def test_rule21_journal_name_has_no_dash_or_colon_in_the_time(self):
        name = journal_name(2, "2026-01-01T00:00:00Z")
        self.assertEqual(name, "backup-2-20260101T000000Z.jsonl")
        self.assertNotIn(":", name)
        self.assertNotIn("-", name.removeprefix("backup-2-"))

    def test_rule21_lines_carry_event_and_at_and_two_journals_append_to_one_file(self):
        Journal(self.path).write("start", row=0)
        Journal(self.path).write("end", row=0)
        lines = [json.loads(line) for line in self._lines() if line]
        self.assertEqual([line["event"] for line in lines], ["start", "end"])
        self.assertTrue(all(line["at"] for line in lines))
        self.assertEqual(lines[0]["row"], 0)

    def test_rule21_next_line_after_a_torn_last_line_starts_on_its_own_line(self):
        with open(self.path, "wb") as handle:
            handle.write(b'{"event": "st')
        journal = Journal(self.path)
        journal.write("start", row=1)
        journal.write("end", row=1)
        lines = self._lines()
        self.assertEqual(lines[0], '{"event": "st')
        self.assertEqual([json.loads(line)["event"] for line in lines[1:] if line], ["start", "end"])
        self.assertEqual(lines.count(""), 1)  # no blank line between whole lines

    def test_rule21_no_leading_newline_on_a_file_that_ends_in_one(self):
        with open(self.path, "wb") as handle:
            handle.write(b'{"event": "end"}\n')
        Journal(self.path).write("start", row=1)
        self.assertEqual(self._lines()[1].startswith("{"), True)

    def test_b14_a_thread_holding_the_lock_can_still_write(self):
        journal = Journal(self.path)

        def locked_write():
            with journal.lock:
                journal.write("start", row=0)

        thread = threading.Thread(target=locked_write, daemon=True)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(self._lines()[0])["event"], "start")

    def test_rule21_every_line_is_on_disk_when_write_returns(self):
        journal = Journal(self.path)
        seen = []

        def fake_fsync(_fd):
            with open(self.path, "rb") as other:
                seen.append(other.read())

        with mock.patch("lrclean.backup.os.fsync", fake_fsync):
            journal.write("start", row=0)
            journal.write("end", row=0)
        self.assertEqual(len(seen), 2)
        for data, event in zip(seen, ("start", "end")):
            self.assertTrue(data.endswith(b"\n"))
            self.assertEqual(json.loads(data.splitlines()[-1])["event"], event)

    def test_rule21_two_threads_leave_only_whole_valid_lines(self):
        journal = Journal(self.path)
        self.assertTrue(journal.lock.acquire(blocking=False))
        journal.lock.release()

        def work(tag):
            for i in range(200):
                journal.write("start", row=i, tag=tag)

        threads = [threading.Thread(target=work, args=(tag,)) for tag in "ab"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        lines = self._lines()
        self.assertEqual(lines[-1], "")
        parsed = [json.loads(line) for line in lines[:-1]]
        self.assertEqual(len(parsed), 400)
        self.assertEqual(sorted(p["tag"] for p in parsed), ["a"] * 200 + ["b"] * 200)


class ReadVerifiedTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def _file(self, name, *lines):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))

    def test_rule21_a_verified_end_gives_the_pair_and_sha_under_normed_keys(self):
        self._file("backup-1-a.jsonl", _end("C:/Lib/a.ARW", "D:/Arc/a.ARW", sha="s1", size=5, mtime=9))
        key = (norm("C:/lib/a.ARW"), norm("d:/arc/a.ARW"))
        self.assertEqual(read_verified(self.dir, 1), {key: (5, 9, "s1", 3, 4)})

    def test_rule21_failed_end_start_torn_and_non_json_lines_are_ignored(self):
        self._file(
            "backup-1-a.jsonl",
            _end("s", "d1", state="failed"),
            json.dumps({"event": "start", "source": "s", "dest": "d2"}),
            "not json at all",
            "[1, 2]",
            json.dumps({"event": "end", "state": "verified"}),
            _end("s", "d3")[:40],
            _end("s", "d4", sha=None),
            _end("s", "d5", sha=12),
            _end("s", "d6", drop=("source_bytes", "source_mtime_ns")),  # a journal from before the source facts
            _end("s", "d6a", drop=("source_bytes",)),
            _end("s", "d6b", drop=("source_mtime_ns",)),
            _end("s", "d7", source_bytes="three"),
            _end("s", "d8", source_mtime_ns=None),
        )
        self.assertEqual(read_verified(self.dir, 1), {})

    def test_rule21_the_last_verified_line_wins_across_files_of_that_n_only(self):
        self._file("backup-1-a.jsonl", _end("s", "d", sha="old"), _end("s", "d", sha="mid"))
        self._file("backup-1-b.jsonl", _end("s", "d", sha="new"))
        self._file("backup-12-a.jsonl", _end("s", "other"))
        self._file("backup-2-a.jsonl", _end("s", "other2"))
        self._file("backup-1-c.txt", _end("s", "other3"))
        self.assertEqual(read_verified(self.dir, 1), {(norm("s"), norm("d")): (5, 7, "new", 3, 4)})

    def test_rule21_a_missing_folder_gives_nothing_and_is_not_created(self):
        gone = os.path.join(self.dir, "journal")
        self.assertEqual(read_verified(gone, 1), {})
        self.assertFalse(os.path.exists(gone))


class ExitCodeTest(unittest.TestCase):
    def _code(self, states, stalled=False):
        return exit_code(RunOutcome([Result(i, "s", "d", state, "") for i, state in enumerate(states)], stalled))

    def test_rule23_clean_states_exit_0(self):
        self.assertEqual(self._code(["verified", "already_there", "skipped_placeholder"]), 0)
        self.assertEqual(self._code([]), 0)

    def test_rule23_each_bad_state_alone_exits_6(self):
        for state in ("conflict", "changed", "failed", "dest_placeholder"):
            with self.subTest(state):
                self.assertEqual(self._code(["verified", state]), 6)

    def test_rule23_a_stall_exits_5_even_with_a_failed_row(self):
        self.assertEqual(self._code(["failed"], stalled=True), 5)
        self.assertEqual(self._code([], stalled=True), 5)


class DryRunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.src = os.path.join(self.top, "lib", "shoot-a")
        self.arc = os.path.join(self.top, "arc-1")
        os.makedirs(self.src)
        os.makedirs(self.arc)
        self.journal_dir = os.path.join(self.top, "work", "journal")

    def _row(self, name, content=b"12345", action="copy"):
        source = os.path.join(self.src, name)
        with open(source, "wb") as handle:
            handle.write(content)
        st = os.stat(source)
        return Row(source, os.path.join(self.arc, name), st.st_size, st.st_mtime_ns, action, "")

    def _journal_verified(self, row):
        dest = os.stat(row.dest)
        os.makedirs(self.journal_dir, exist_ok=True)
        with open(os.path.join(self.journal_dir, "backup-1-a.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(
                _end(row.source, row.dest, size=dest.st_size, mtime=dest.st_mtime_ns,
                     source_bytes=row.bytes, source_mtime_ns=row.mtime_ns) + "\n"
            )  # fmt: skip

    def test_b8_each_verdict_arises_and_nothing_is_created(self):
        placeholder = self._row("IMG_0001.ARW", action="skip_placeholder")
        changed = self._row("IMG_0002.ARW")._replace(bytes=99)
        verified = self._row("IMG_0003.ARW")
        shutil.copyfile(verified.source, verified.dest)
        self._journal_verified(verified)
        exists = self._row("IMG_0004.ARW")
        shutil.copyfile(exists.source, exists.dest)
        new = self._row("IMG_0005.ARW")
        offline = self._row("IMG_0006.ARW")
        shutil.copyfile(offline.source, offline.dest)
        offline_verified = self._row("IMG_0007.ARW")  # verified, then turned online-only
        shutil.copyfile(offline_verified.source, offline_verified.dest)
        self._journal_verified(offline_verified)
        unstatable = self._row("IMG_0008.ARW")  # verified, but its destination's stat now fails
        shutil.copyfile(unstatable.source, unstatable.dest)
        self._journal_verified(unstatable)

        def attrs_of(path, _st):
            return 0x400000 if path in (offline.dest, offline_verified.dest) else 0

        def stat(path):
            if path == paths.fs_path(unstatable.dest):
                raise OSError("synthetic stat failure")
            return os.stat(path)

        for row in (offline, offline_verified):
            self.assertNotEqual(attrs_of(row.dest, None), attrs_of(row.source, None))
        self.assertNotEqual(paths.fs_path(unstatable.dest), paths.fs_path(unstatable.source))
        rows = [placeholder, changed, verified, exists, new, offline, offline_verified, unstatable]
        before = _listing(self.top)
        got = dry_run(rows, journal_dir=self.journal_dir, n=1, stat=stat, attrs_of=attrs_of)
        self.assertEqual(
            got,
            [
                (0, "skip_placeholder"),
                (1, "changed"),
                (2, "already_verified"),
                (3, "dest_exists"),
                (4, "copy"),
                (5, "dest_placeholder"),
                (6, "dest_placeholder"),
                (7, "dest_exists"),
            ],
        )
        self.assertEqual(_listing(self.top), before)

    def test_b8_missing_journal_folder_is_not_created_and_gives_copy(self):
        row = self._row("IMG_0001.ARW")
        self.assertEqual(dry_run([row], journal_dir=self.journal_dir, n=1), [(0, "copy")])
        self.assertFalse(os.path.exists(self.journal_dir))

    def test_rule20_dry_run_uses_source_changed_before_the_journal(self):
        row = self._row("IMG_0001.ARW")
        shutil.copyfile(row.source, row.dest)
        self._journal_verified(row)
        got = dry_run([row], journal_dir=self.journal_dir, n=1, attrs_of=lambda _p, _s: 0x400000)
        self.assertEqual(got, [(0, "changed")])

    def test_rule21_a_destination_changed_since_the_journal_is_dest_exists(self):
        row = self._row("IMG_0001.ARW")
        shutil.copyfile(row.source, row.dest)
        self._journal_verified(row)
        recorded = os.stat(row.dest)
        cases = {
            "size and time": (b"longer than before", None),
            "time only": (b"54321", recorded.st_mtime_ns + 5_000_000_000),
            "size only": (b"longer than before", recorded.st_mtime_ns),
        }
        for name, (content, mtime_ns) in cases.items():
            with self.subTest(name):
                with open(row.dest, "wb") as handle:
                    handle.write(content)
                if mtime_ns is not None:
                    os.utime(row.dest, ns=(mtime_ns, mtime_ns))
                self.assertEqual(dry_run([row], journal_dir=self.journal_dir, n=1), [(0, "dest_exists")])

    def test_r20_a_source_edited_and_planned_again_is_dest_exists_not_already_verified(self):
        row = self._row("IMG_0001.ARW")
        shutil.copyfile(row.source, row.dest)
        self._journal_verified(row)
        self.assertEqual(dry_run([row._replace()], journal_dir=self.journal_dir, n=1), [(0, "already_verified")])
        later = row.mtime_ns + 5_000_000_000
        cases = {  # each field of the recorded source facts alone, then both
            "size alone": (b"edited later", row.mtime_ns),
            "time alone": (b"54321", later),
            "size and time": (b"edited later", later),
        }
        for name, (content, mtime_ns) in cases.items():
            with self.subTest(name):
                with open(row.source, "wb") as handle:
                    handle.write(content)
                os.utime(row.source, ns=(mtime_ns, mtime_ns))
                stat = os.stat(row.source)
                replanned = row._replace(bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
                self.assertEqual(dry_run([replanned], journal_dir=self.journal_dir, n=1), [(0, "dest_exists")])

    def test_rule21_a_journaled_row_whose_dest_is_gone_is_copy(self):
        row = self._row("IMG_0001.ARW")
        shutil.copyfile(row.source, row.dest)
        self._journal_verified(row)
        os.remove(row.dest)
        self.assertEqual(dry_run([row], journal_dir=self.journal_dir, n=1), [(0, "copy")])


class _Aborted(Exception):
    pass


class _RecordingJournal(Journal):
    made = []

    def __init__(self, path):
        super().__init__(path)
        self.made.append(self)


_ABORT_LIMIT = 30  # seconds of real time the stall path may take to reach its abort


class _Blocking:
    """A source handle whose read jumps the fake clock, then blocks until the gate opens."""

    WAIT = 2 * _ABORT_LIMIT  # longer than the limit: an abort that waits for this worker cannot meet it

    def __init__(self, handle, jump, gate):
        self._handle, self._jump, self._gate = handle, jump, gate

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def read(self, size=-1):
        self._jump()
        self._gate.wait(self.WAIT)
        raise OSError("released")

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _SlowBlock:
    """A read handle whose every block takes 40 fake seconds, and waits until the watchdog has looked."""

    def __init__(self, handle, clock_now, watched):
        self._handle, self._clock_now, self._watched = handle, clock_now, watched

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def read(self, size=-1):
        self._watched.clear()
        data = self._handle.read(size)
        self._clock_now[0] += 40
        self._watched.wait(2)
        return data

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _Stuck:
    """A source handle whose first read jumps the fake clock, says so, and waits for the gate before it reads."""

    def __init__(self, handle, jump, blocked, gate):
        self._handle, self._jump, self._blocked, self._gate = handle, jump, blocked, gate

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def read(self, size=-1):
        if not self._blocked.is_set():
            self._jump()
            self._blocked.set()
            self._gate.wait(5)
        return self._handle.read(size)

    def __getattr__(self, name):
        return getattr(self._handle, name)


class RunManifestTest(unittest.TestCase):
    STARTED = "2026-01-01T00:00:00Z"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.src = os.path.join(self.top, "lib", "shoot-a")
        self.arc = os.path.join(self.top, "arc-1")
        os.makedirs(self.src)
        os.makedirs(self.arc)
        self.journal_dir = os.path.join(self.top, "work", "journal")
        self.gate = threading.Event()
        self.clock_now = [0.0]
        threads = set(threading.enumerate())
        # cleanups run last-in first-out: open the gate, then wait for the workers, then drop the folder
        self.addCleanup(lambda: [t.join(5) for t in set(threading.enumerate()) - threads])
        self.addCleanup(self.gate.set)

    def _row(self, name, content=b"12345", action="copy", mtime_ns=None):
        source = os.path.join(self.src, name)
        with open(source, "wb") as handle:
            handle.write(content)
        if mtime_ns is not None:
            os.utime(source, ns=(mtime_ns, mtime_ns))
        st = os.stat(source)
        return Row(source, os.path.join(self.arc, name), st.st_size, st.st_mtime_ns, action, "")

    def _run(self, rows, **kw):
        kw.setdefault("now", lambda: "T")
        kw.setdefault("poll_seconds", 0.01)
        return run_manifest(rows, journal_dir=self.journal_dir, n=1, started=self.STARTED, **kw)

    def _journal(self):
        path = os.path.join(self.journal_dir, journal_name(1, self.STARTED))
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def _blocked_run(self, rows, blocked, jump, abort, **kw):
        """Run rows; the source of `blocked` jumps the fake clock by `jump` seconds and blocks."""

        def opener(path):
            handle = hashing.open_binary(path)
            if path != blocked.source:
                return handle
            return _Blocking(handle, lambda: self.clock_now.__setitem__(0, self.clock_now[0] + jump), self.gate)

        with mock.patch("sys.stderr", io.StringIO()):  # the watchdog's one line
            return self._run(rows, opener=opener, clock=lambda: self.clock_now[0], abort=abort, **kw)

    def test_rule21_a_run_journals_start_and_end_and_a_rerun_is_skipped(self):
        rows = [self._row("IMG_0001.ARW"), self._row("IMG_0002.ARW", action="skip_placeholder")]
        got = self._run(rows)
        sha = hashlib.sha256(b"12345").hexdigest()
        self.assertEqual(got.results[0], Result(0, rows[0].source, rows[0].dest, "verified", sha))
        start, end = self._journal()
        fields = {"row": 0, "source": rows[0].source, "dest": rows[0].dest}
        dest = os.stat(rows[0].dest)
        self.assertEqual(start, {"event": "start", "at": "T", **fields})
        self.assertEqual(
            end,
            {"event": "end", "at": "T", **fields, "state": "verified", "sha256": sha,
             "dest_bytes": 5, "dest_mtime_ns": dest.st_mtime_ns,
             "source_bytes": rows[0].bytes, "source_mtime_ns": rows[0].mtime_ns},
        )  # fmt: skip
        before = self._journal()
        calls, stats = [], []

        def stat(path):
            stats.append(path)
            return os.stat(path)

        again = self._run(rows, opener=lambda p: calls.append(p) or hashing.open_binary(p), stat=stat)
        self.assertEqual(calls, [])
        dest_path = paths.fs_path(rows[0].dest)
        self.assertNotEqual(dest_path, paths.fs_path(rows[0].source))
        self.assertEqual(stats.count(dest_path), 1)  # a healthy verified skip stats its destination once
        self.assertEqual(self._journal(), before)  # one file, nothing appended
        self.assertEqual(again.results[0], got.results[0])
        self.assertEqual(os.listdir(self.journal_dir), [journal_name(1, self.STARTED)])

    def test_rule21_a_row_the_journal_cannot_vouch_for_is_not_skipped(self):
        def size_alone(row):
            recorded = os.stat(row.dest).st_mtime_ns
            with open(row.dest, "wb") as handle:
                handle.write(b"longer than before")
            os.utime(row.dest, ns=(recorded, recorded))
            return "conflict"

        def time_alone(row):
            later = os.stat(row.dest).st_mtime_ns + 5_000_000_000
            os.utime(row.dest, ns=(later, later))
            return "already_there"

        def source_changes(row):
            with open(row.source, "wb") as handle:
                handle.write(b"the source grew")
            return "changed"

        def dest_gone(row):  # a zero-byte file at time 0 is the pair (0, 0), which a missing dest must not match
            os.remove(row.dest)
            return "verified"

        offline, unreadable = set(), set()

        def attrs_of(path, _st):
            return 0x400000 * (path in offline)

        def stat(path):
            if path in unreadable:
                raise OSError("synthetic stat failure")
            return os.stat(path)

        def dest_becomes_placeholder(row):  # same size and time, now online-only
            offline.add(row.dest)
            self.assertNotEqual(attrs_of(row.dest, None), attrs_of(row.source, None))
            return "dest_placeholder"

        def dest_stat_fails(row):  # the one destination stat fails: the skip is declined
            unreadable.add(paths.fs_path(row.dest))
            self.assertNotIn(paths.fs_path(row.source), unreadable)
            return "failed"

        cases = [
            (size_alone, {}),
            (time_alone, {}),
            (source_changes, {}),
            (dest_gone, {"content": b"", "mtime_ns": 0}),
            (dest_becomes_placeholder, {}),
            (dest_stat_fails, {}),
        ]
        for k, (change, row_args) in enumerate(cases):
            with self.subTest(change.__name__):
                row = self._row(f"IMG_{k}.ARW", **row_args)
                self._run([row], stat=stat, attrs_of=attrs_of)
                lines = len(self._journal())
                expected = change(row)
                self.assertEqual(self._run([row], stat=stat, attrs_of=attrs_of).results[0].state, expected)
                self.assertEqual(len(self._journal()), lines + 2)
                self.assertTrue(os.path.exists(row.dest))

    def test_r20_a_source_edited_and_planned_again_is_compared_by_content_and_conflicts(self):
        row = self._row("IMG_0001.ARW")
        self._run([row])
        lines = len(self._journal())
        self.assertEqual(self._run([row._replace()]).results[0].state, "verified")  # planned again, unchanged
        self.assertEqual(len(self._journal()), lines)
        later = row.mtime_ns + 5_000_000_000
        cases = {  # each field of the recorded source facts alone, then both
            "size alone": (b"edited later", row.mtime_ns),
            "time alone": (b"54321", later),
            "size and time": (b"edited later", later),
        }
        for name, (content, mtime_ns) in cases.items():
            with self.subTest(name):
                lines = len(self._journal())
                with open(row.source, "wb") as handle:
                    handle.write(content)
                os.utime(row.source, ns=(mtime_ns, mtime_ns))
                stat = os.stat(row.source)
                replanned = row._replace(bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
                self.assertEqual(self._run([replanned]).results[0].state, "conflict")
                self.assertEqual([e["event"] for e in self._journal()[-2:]], ["start", "end"])
                self.assertEqual(len(self._journal()), lines + 2)
                with open(row.dest, "rb") as handle:
                    self.assertEqual(handle.read(), b"12345")

    def test_r20_a_verified_line_without_the_source_facts_gives_no_skip(self):
        row = self._row("IMG_0001.ARW")
        shutil.copyfile(row.source, row.dest)
        dest = os.stat(row.dest)
        os.makedirs(self.journal_dir)
        with open(os.path.join(self.journal_dir, journal_name(1, self.STARTED)), "w", encoding="utf-8") as handle:
            handle.write(_end(row.source, row.dest, size=dest.st_size, mtime=dest.st_mtime_ns,
                              drop=("source_bytes", "source_mtime_ns")) + "\n")  # fmt: skip
        self.assertEqual(self._run([row]).results[0].state, "already_there")
        self.assertEqual([e["event"] for e in self._journal()[-2:]], ["start", "end"])

    def test_rule21_a_conflict_is_not_skipped_on_a_rerun(self):
        row = self._row("IMG_0001.ARW")
        os.makedirs(self.arc, exist_ok=True)
        with open(row.dest, "wb") as handle:
            handle.write(b"other")
        for _ in range(2):
            self.assertEqual(self._run([row]).results[0].state, "conflict")
        self.assertEqual([line["event"] for line in self._journal()], ["start", "end"] * 2)

    def test_rule22_a_stall_ends_the_run_with_the_lock_held_and_the_next_run_recovers(self):
        rows = [self._row("IMG_0001.ARW"), self._row("IMG_0002.ARW")]
        seen = {}

        def abort(code):
            journal = _RecordingJournal.made[-1]
            with open(journal.path, encoding="utf-8") as handle:
                seen["lines"] = [json.loads(line) for line in handle.read().splitlines()]
            held = []
            probe = threading.Thread(target=lambda: held.append(journal.lock.acquire(blocking=False)))
            probe.start()
            probe.join(2)
            seen.update(code=code, held=held, part=os.path.exists(rows[1].dest + PART_SUFFIX))
            raise _Aborted

        def writer(path):  # leaves the part file behind, closed (as a dead process would), and never gets a write
            open_part(path).close()
            return io.BytesIO()

        self.assertGreater(_Blocking.WAIT, _ABORT_LIMIT)
        began = time.monotonic()
        with mock.patch("lrclean.backup.Journal", _RecordingJournal):
            with self.assertRaises(_Aborted):
                self._blocked_run(rows, rows[1], 130, abort, writer=writer)
        self.assertLess(time.monotonic() - began, _ABORT_LIMIT)
        self.assertEqual(seen["code"], 5)
        self.assertEqual(seen["held"], [False])
        last = seen["lines"][-1]
        self.assertEqual((last["event"], last["row"], last["source"]), ("stalled", 1, rows[1].source))
        self.assertEqual([e["event"] for e in seen["lines"] if e.get("row") == 1], ["start", "stalled"])
        self.assertTrue(seen["part"])
        got = self._run(rows)  # the blocked worker of the first run is still waiting on the gate
        self.assertEqual([r.state for r in got.results], ["verified", "verified"])
        self.assertEqual(_parts(self.arc), [])
        with open(rows[1].dest, "rb") as handle:
            self.assertEqual(handle.read(), b"12345")

    def test_rule22_an_abort_that_returns_gives_stalled_with_the_results_so_far(self):
        rows = [self._row("IMG_0001.ARW"), self._row("IMG_0002.ARW")]
        codes = []
        got = self._blocked_run(rows, rows[1], 130, codes.append)
        self.assertEqual(codes, [5])
        self.assertTrue(got.stalled)
        self.assertEqual([r.state for r in got.results], ["verified"])

    def test_b15_blocks_that_each_make_progress_are_not_a_stall(self):
        row = self._row("IMG_0001.ARW")
        watched = threading.Event()

        def clock():
            if threading.current_thread() is threading.main_thread():
                watched.set()
            return self.clock_now[0]

        def opener(path):
            return _SlowBlock(hashing.open_binary(path), self.clock_now, watched)

        codes = []
        got = self._run([row], opener=opener, clock=clock, abort=codes.append)
        self.assertEqual(codes, [])
        self.assertFalse(got.stalled)
        self.assertGreater(self.clock_now[0], 120)  # more than the limit in all
        self.assertEqual(got.results[0].state, "verified")

    def test_rule22_progress_made_before_the_lock_is_taken_is_not_a_stall(self):
        row = self._row("IMG_0001.ARW")
        blocked, moved_on = threading.Event(), threading.Event()
        looks, ticks = [0], [0]

        def clock():
            if threading.current_thread() is threading.main_thread():
                if blocked.is_set():
                    looks[0] += 1
                    if looks[0] == 2:  # the re-check under the lock: the worker makes progress just before it
                        self.gate.set()
                        moved_on.wait(2)
            elif self.gate.is_set():
                ticks[0] += 1
                if ticks[0] == 2:  # the first tick has stored its reading by now
                    moved_on.set()
            return self.clock_now[0]

        def opener(path):
            jump = lambda: self.clock_now.__setitem__(0, self.clock_now[0] + 130)  # noqa: E731
            return _Stuck(hashing.open_binary(path), jump, blocked, self.gate)

        codes = []
        got = self._run([row], opener=opener, clock=clock, abort=codes.append)
        self.assertGreaterEqual(looks[0], 2)  # the first look saw a stall, the second did not
        self.assertEqual((codes, got.stalled), ([], False))
        self.assertEqual(got.results[0].state, "verified")
        self.assertNotIn("stalled", [e["event"] for e in self._journal()])

    def test_rule22_a_long_run_of_skipped_rows_is_not_a_stall(self):
        rows = [self._row(f"IMG_{k:04d}.ARW") for k in range(6)]
        self._run(rows)  # the journal now vouches for every row
        watched = threading.Event()

        def clock():
            if threading.current_thread() is threading.main_thread():
                watched.set()
            return self.clock_now[0]

        def stat(path, *args, **kw):  # one source stat per skipped row: 40 s each, 240 s in all
            watched.clear()
            self.clock_now[0] += 40
            watched.wait(2)
            return os.stat(path, *args, **kw)

        codes = []
        calls = []
        got = self._run(
            rows, stat=stat, clock=clock, abort=codes.append, opener=lambda p: calls.append(p) or hashing.open_binary(p)
        )
        self.assertEqual(calls, [])  # every row was skipped
        self.assertEqual((codes, got.stalled), ([], False))
        self.assertEqual([r.state for r in got.results], ["verified"] * 6)
        self.assertGreater(self.clock_now[0], 120)

    def test_b13_an_error_in_one_row_is_failed_and_later_rows_still_copy(self):
        rows = [self._row("IMG_0001.ARW"), self._row("IMG_0002.ARW"), self._row("IMG_0003.ARW")]

        def opener(path):
            if path == rows[1].source:
                raise RuntimeError("boom")
            return hashing.open_binary(path)

        codes = []
        got = self._run(rows, opener=opener, abort=codes.append)
        self.assertEqual([r.state for r in got.results], ["verified", "failed", "verified"])
        self.assertEqual((codes, got.stalled), ([], False))
        ends = [(e["row"], e["state"], e["sha256"]) for e in self._journal() if e["event"] == "end"]
        self.assertEqual(ends[1], (1, "failed", ""))
        self.assertEqual(_parts(self.arc), [])

    def test_b13_a_part_file_that_cannot_be_removed_fails_the_row_and_the_run_goes_on(self):
        rows = [self._row("IMG_0001.ARW"), self._row("IMG_0002.ARW")]
        real = os.remove

        def remove(path):
            if path.endswith(PART_SUFFIX) and "IMG_0001" in path:
                raise PermissionError("held")
            return real(path)

        for row in rows:
            os.makedirs(os.path.dirname(row.dest), exist_ok=True)
        with open(rows[0].dest + PART_SUFFIX, "wb") as handle:
            handle.write(b"left over")
        with mock.patch("lrclean.backup.os.remove", remove):
            got = self._run(rows)
        self.assertEqual([r.state for r in got.results], ["failed", "verified"])
        self.assertEqual([e["event"] for e in self._journal() if e["row"] == 0], ["start", "end"])

    def test_rule21_an_error_of_the_journal_reaches_the_caller(self):
        row = self._row("IMG_0001.ARW")
        with mock.patch.object(Journal, "write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self._run([row])

    def test_rule21_placeholder_rows_give_a_result_and_no_journal_line(self):
        rows = [self._row("IMG_0001.ARW", action="skip_placeholder"), self._row("IMG_0002.ARW")]
        got = self._run(rows[:1])
        self.assertEqual(got.results, [Result(0, rows[0].source, rows[0].dest, "skipped_placeholder", "")])
        self.assertEqual(self._journal(), [])
        both = self._run(rows)
        self.assertEqual([r.row for r in both.results], [0, 1])
        self.assertEqual({e["row"] for e in self._journal()}, {1})


if __name__ == "__main__":
    unittest.main()
