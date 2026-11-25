import csv
import hashlib
import io
import os
import tempfile
import types
import unittest

from lrclean import paths
from lrclean.backup_plan import VERIFIED_COLUMNS, Row
from lrclean.backup_verify import Verified, verify_exit_code, verify_manifest, write_verified

PLACEHOLDER_ATTR = 0x400000


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _row(source, dest, size=0, action="copy"):
    return Row(source, dest, size, 0, action, "")


def _listing(root):
    out = {}
    for folder, _, names in os.walk(root):
        for name in names:
            full = os.path.join(folder, name)
            st = os.stat(full)
            with open(full, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            out[full] = (st.st_size, st.st_mtime_ns, digest)
    return out


def _boom(path):
    raise AssertionError(f"opened {path}")


class VerifyManifestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src = os.path.join(tmp.name, "lib")
        self.dst = os.path.join(tmp.name, "archive")
        self.tmp = tmp.name

    def pair(self, name, data, dest_data):
        return (_write(os.path.join(self.src, name), data),
                _write(os.path.join(self.dst, name), dest_data))

    def test_identical_has_source_sha256(self):
        data = b"raw bytes" * 100
        source, dest = self.pair("shoot-a/IMG_0001.ARW", data, data)
        out = verify_manifest([_row(source, dest, len(data))])
        self.assertEqual(out, [Verified(source, dest, len(data),
                                        hashlib.sha256(data).hexdigest(), "identical")])

    def test_middle_byte_in_large_file_and_length_are_different(self):
        big = bytes(3 * 1024 * 1024)
        flipped = bytearray(big)
        flipped[len(big) // 2] = 1
        s1, d1 = self.pair("a/IMG_0001.ARW", big, bytes(flipped))
        s2, d2 = self.pair("a/IMG_0002.ARW", b"abc", b"abcd")
        out = verify_manifest([_row(s1, d1), _row(s2, d2)])
        self.assertEqual([item.state for item in out], ["different", "different"])
        self.assertEqual(out[0].sha256, hashlib.sha256(big).hexdigest())

    def test_missing_unreadable_and_order(self):
        source, dest = self.pair("a/IMG_0003.ARW", b"x", b"x")
        no_src = _row(os.path.join(self.src, "gone.ARW"), dest)
        no_dst = _row(source, os.path.join(self.dst, "gone.ARW"))

        def opener(path):
            raise PermissionError(path)

        states = [item.state for item in verify_manifest(
            [no_src, no_dst, _row(source, dest)], opener=opener)]
        self.assertEqual(states, ["missing_source", "missing_dest", "unreadable"])

    def test_placeholders_are_never_opened(self):
        source, dest = self.pair("a/IMG_0004.ARW", b"x", b"x")
        skipped = _row("elsewhere/IMG_0005.ARW", "nowhere/IMG_0005.ARW", action="skip_placeholder")
        out = verify_manifest(
            [skipped, _row(source, dest)], opener=_boom,
            attrs_of=lambda path, st: PLACEHOLDER_ATTR)
        self.assertEqual([item.state for item in out],
                         ["skipped_placeholder", "source_placeholder"])

    def test_placeholder_detected_from_stat_attributes_without_seam(self):
        st = types.SimpleNamespace(st_size=1, st_file_attributes=PLACEHOLDER_ATTR)
        out = verify_manifest([_row("s/IMG_0001.ARW", "d/IMG_0001.ARW")],
                              stat=lambda path: st, opener=_boom)
        self.assertEqual([item.state for item in out], ["source_placeholder"])

    def test_placeholder_destination_is_never_opened_and_is_unreadable(self):
        source, dest = self.pair("a/IMG_0008.ARW", b"x", b"x")
        out = verify_manifest(
            [_row(source, dest)], opener=_boom,
            attrs_of=lambda path, st: PLACEHOLDER_ATTR if path == dest else 0)
        self.assertEqual([item.state for item in out], ["unreadable"])

    def test_destination_read_error_is_unreadable_and_run_continues(self):
        s1, d1 = self.pair("a/IMG_0009.ARW", b"x", b"x")
        s2, d2 = self.pair("a/IMG_0010.ARW", b"y", b"y")

        def opener(path):
            if path == d1:
                raise PermissionError(path)
            return open(path, "rb")

        out = verify_manifest([_row(s1, d1), _row(s2, d2)], opener=opener)
        self.assertEqual([item.state for item in out], ["unreadable", "identical"])

    def test_stat_error_other_than_not_found_is_unreadable_on_either_side(self):
        source, dest = self.pair("a/IMG_0011.ARW", b"x", b"x")
        for failing in (source, dest):
            def stat(path):
                if path == failing:
                    raise PermissionError(path)
                return os.stat(path)

            with self.subTest(failing=failing):
                out = verify_manifest([_row(source, dest)], stat=stat)
                self.assertEqual([item.state for item in out], ["unreadable"])

    def test_uses_fs_path_for_both_stats_and_both_opens(self):
        source = "C:/" + "s" * 300 + "/IMG_0001.ARW"
        dest = "C:/" + "d" * 300 + "/IMG_0001.ARW"
        stats, opens = [], []

        def stat(path):
            stats.append(path)
            return types.SimpleNamespace(st_size=1, st_file_attributes=0)

        def opener(path):
            opens.append(path)
            return io.BytesIO(b"x")

        verify_manifest([_row(source, dest)], stat=stat, opener=opener)
        expected = [paths.fs_path(source), paths.fs_path(dest)]
        self.assertNotEqual(expected, [source, dest])
        self.assertEqual((stats, opens), (expected, expected))

    def test_writes_nothing_else(self):
        source, dest = self.pair("a/IMG_0006.ARW", b"same", b"same")
        s2, d2 = self.pair("a/IMG_0007.ARW", b"one", b"two")
        before = (_listing(self.src), _listing(self.dst))
        verify_manifest([_row(source, dest), _row(s2, d2)])
        self.assertEqual((_listing(self.src), _listing(self.dst)), before)


class VerifyOutputTest(unittest.TestCase):
    def item(self, state):
        return Verified("s", "d", 1, "h", state)

    def test_exit_code_zero_only_when_every_copy_identical(self):
        ok = [self.item("identical"), self.item("skipped_placeholder")]
        self.assertEqual(verify_exit_code(ok), 0)
        self.assertEqual(verify_exit_code([]), 0)
        for bad in ("different", "missing_source", "missing_dest", "unreadable",
                    "source_placeholder"):
            with self.subTest(bad):
                self.assertEqual(verify_exit_code(ok + [self.item(bad)]), 6)

    def test_write_verified_round_trips_with_bom_and_creates_parent(self):
        items = [Verified("a,b", "d", 3, "ff", "identical"), Verified("s", "d2", 0, "", "unreadable")]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out", "verified.csv")
            write_verified(path, items)
            with open(path, "rb") as handle:
                self.assertTrue(handle.read().startswith(b"\xef\xbb\xbf"))
            with open(path, encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
        self.assertEqual(tuple(reader.fieldnames), VERIFIED_COLUMNS)
        self.assertEqual(rows[0], {"source": "a,b", "dest": "d", "bytes": "3",
                                   "sha256": "ff", "state": "identical"})
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
