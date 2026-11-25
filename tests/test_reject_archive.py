import os
import tempfile
import types
import unittest

from lrclean import paths
from lrclean.backup_plan import VERIFIED_COLUMNS, ManifestError, Row, write_manifest
from lrclean.backup_verify import Verified, read_verified_list, write_verified
from lrclean.reject_archive import (
    CHANGED_SINCE_BACKUP,
    NO_ARCHIVE_COPY,
    OK,
    OUTSIDE_BACKUP,
    RootEvidence,
    archive_state,
    load_evidence,
)

SIZE = 10
MTIME = 1_700_000_000_000_000_000
PLACEHOLDER_ATTR = 0x400000
STATES = ("identical", "different", "missing_source", "missing_dest", "source_placeholder",
          "unreadable", "skipped_placeholder")


def _st(size=SIZE, mtime=MTIME):
    return types.SimpleNamespace(st_size=size, st_mtime_ns=mtime)


class World:
    """One temp tree: two archive roots, a source name, and the out folder."""

    def __init__(self, base):
        self.out = os.path.join(base, "out")
        os.makedirs(self.out)
        self.roots = [os.path.join(base, "arch1"), os.path.join(base, "arch2")]
        self.source = os.path.join(base, "src", "shoot-a", "IMG_0001.ARW")
        self.dest = self.put(1, "shoot-a/IMG_0001.ARW", SIZE)

    def put(self, n, name, size):
        path = os.path.join(self.roots[n - 1], *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def row(self, source=None, dest=None, action="copy", size=SIZE):
        return Row(source or self.source, dest or self.dest, size, MTIME, action, "")

    def verified(self, source=None, dest=None, size=SIZE, state="identical"):
        return Verified(source or self.source, dest or self.dest, size, "0" * 64, state)

    def write(self, n, rows=None, verified=None, manifest_time=None, verified_time=None):
        manifest = os.path.join(self.out, f"backup-manifest-{n}.csv")
        listing = os.path.join(self.out, f"backup-verified-{n}.csv")
        if rows is not None:
            write_manifest(manifest, rows)
        if verified is not None:
            write_verified(listing, verified)
        return manifest, listing

    def evidence(self, n=1, **kwargs):
        return load_evidence(self.out, self.roots, **kwargs)[n - 1]

    def state(self, rows, verified, st=None, **kwargs):
        self.write(1, rows, verified)
        return archive_state(self.evidence(), self.source, _st() if st is None else st, **kwargs)[0]


class ArchiveStateFieldTests(unittest.TestCase):
    def test_source_facts_are_compared_field_by_field(self):
        cases = [("equal", _st(), OK),
                 ("size differs alone", _st(size=SIZE + 1), CHANGED_SINCE_BACKUP),
                 ("time differs alone", _st(mtime=MTIME + 1), CHANGED_SINCE_BACKUP),
                 ("time differs below", _st(mtime=MTIME - 1), CHANGED_SINCE_BACKUP)]
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            for label, st, expected in cases:
                with self.subTest(label):
                    self.assertEqual(world.state([world.row()], [world.verified()], st), expected)

    def test_source_stat_failure_is_changed_since_backup(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            world.write(1, [world.row()], [world.verified()])
            state, dest = archive_state(world.evidence(), world.source, None)
            self.assertEqual((state, dest), (CHANGED_SINCE_BACKUP, world.dest))


class ArchiveStateEvidenceTests(unittest.TestCase):
    def test_manifest_without_a_copy_inside_the_root_is_outside_backup(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            other = world.put(1, "shoot-a/IMG_0002.ARW", SIZE)
            cases = {
                "no row for the source": [world.row(source=world.source + "2")],
                "skip_placeholder row": [world.row(action="skip_placeholder")],
                "dest outside the root": [world.row(dest=os.path.join(base, "elsewhere", "a.ARW"))],
                "dest equal to the root": [world.row(dest=world.roots[0])],
                "two copy rows": [world.row(), world.row(dest=other)],
            }
            for label, rows in cases.items():
                with self.subTest(label):
                    self.assertEqual(world.state(rows, [world.verified()]), OUTSIDE_BACKUP)

    def test_absent_manifest_is_outside_backup(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            evidence = world.evidence()
            self.assertEqual(evidence.rows, {})
            self.assertEqual(archive_state(evidence, world.source, _st()), (OUTSIDE_BACKUP, ""))

    def test_a_verified_row_that_does_not_vouch_for_this_copy_is_no_archive_copy(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            other_dest = world.put(1, "shoot-a/IMG_0009.ARW", SIZE)
            foreign = world.verified(source=world.source + "2")
            cases = {
                "state different": [world.verified(state="different")],
                "state missing_dest": [world.verified(state="missing_dest")],
                "state unreadable": [world.verified(state="unreadable")],
                "other bytes": [world.verified(size=SIZE + 1)],
                "other dest": [world.verified(dest=other_dest)],
                "other source": [foreign],
                "other source beside the right row of a second reject":
                    [foreign, world.verified(source=world.source + "3", dest=other_dest)],
            }
            for label, listing in cases.items():
                with self.subTest(label):
                    self.assertEqual(world.state([world.row()], listing), NO_ARCHIVE_COPY)

    def test_absent_verified_list_is_no_archive_copy(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            self.assertEqual(world.state([world.row()], None), NO_ARCHIVE_COPY)

    def test_verified_list_age_against_its_manifest(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            manifest, listing = world.write(1, [world.row()], [world.verified()])
            for label, delta, expected in (("one ns older", -1, NO_ARCHIVE_COPY),
                                           ("same time", 0, OK), ("newer", 1, OK)):
                with self.subTest(label):
                    def stat(path, delta=delta):
                        return _st(mtime=MTIME + (delta if path == listing else 0))
                    evidence = world.evidence(stat=stat)
                    self.assertEqual(archive_state(evidence, world.source, _st())[0], expected)

    def test_stat_error_on_either_list_file_gives_an_empty_verified_set(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            manifest, listing = world.write(1, [world.row()], [world.verified()])
            for broken in (manifest, listing):
                with self.subTest(os.path.basename(broken)):
                    def stat(path, broken=broken):
                        if path == broken:
                            raise PermissionError(path)
                        return _st()
                    self.assertEqual(world.evidence(stat=stat).verified, frozenset())

    def test_verified_key_is_compared_as_normalised_path(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            respelled = world.verified(source=world.source.upper().replace("\\", "/"))
            self.assertEqual(world.state([world.row()], [respelled]), OK)

    def test_destination_is_checked_on_disk(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            world.write(1, [world.row()], [world.verified()])
            evidence = world.evidence()

            def boom(path):
                raise PermissionError(path)

            cases = {
                "placeholder": dict(attrs_of=lambda path, st: PLACEHOLDER_ATTR),
                "stat raises": dict(stat=boom),
            }
            for label, kwargs in cases.items():
                with self.subTest(label):
                    self.assertEqual(archive_state(evidence, world.source, _st(), **kwargs)[0],
                                     NO_ARCHIVE_COPY)
            os.remove(world.dest)
            self.assertEqual(archive_state(evidence, world.source, _st())[0], NO_ARCHIVE_COPY)

    def test_destination_of_another_size_is_no_archive_copy(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            world.put(1, "shoot-a/IMG_0001.ARW", SIZE + 5)
            self.assertEqual(world.state([world.row()], [world.verified()]), NO_ARCHIVE_COPY)

    def test_destination_online_only_placeholder_with_the_default_attribute_seam(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            world.write(1, [world.row()], [world.verified()])

            def stat(path):
                real = os.stat(path)
                return types.SimpleNamespace(st_size=real.st_size, st_file_attributes=PLACEHOLDER_ATTR)

            self.assertEqual(archive_state(world.evidence(), world.source, _st(), stat=stat)[0],
                             NO_ARCHIVE_COPY)


class PerRootTests(unittest.TestCase):
    def _two_roots(self, world):
        dest2 = world.put(2, "shoot-a/IMG_0001.ARW", SIZE)
        world.write(1, [world.row()], [world.verified()])
        world.write(2, [world.row(dest=dest2)], [world.verified(dest=dest2)])

    def test_each_root_reads_its_own_numbered_files(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            self._two_roots(world)
            first, second = load_evidence(world.out, world.roots)
            self.assertEqual(archive_state(first, world.source, _st())[0], OK)
            self.assertEqual(archive_state(second, world.source, _st())[0], OK)

    def test_one_absent_manifest_leaves_the_other_root_decided(self):
        for absent in (1, 2):
            with self.subTest(f"manifest {absent} absent"):
                with tempfile.TemporaryDirectory() as base:
                    world = World(base)
                    self._two_roots(world)
                    os.remove(os.path.join(world.out, f"backup-manifest-{absent}.csv"))
                    evidence = load_evidence(world.out, world.roots)
                    kept = evidence[2 - absent]
                    self.assertEqual(evidence[absent - 1].rows, {})
                    self.assertEqual(archive_state(kept, world.source, _st())[0], OK)

    def test_manifests_swapped_between_the_files_are_outside_backup(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            self._two_roots(world)
            one = os.path.join(world.out, "backup-manifest-1.csv")
            two = os.path.join(world.out, "backup-manifest-2.csv")
            os.replace(one, one + ".tmp")
            os.replace(two, one)
            os.replace(one + ".tmp", two)
            for evidence in load_evidence(world.out, world.roots):
                self.assertEqual(archive_state(evidence, world.source, _st())[0], OUTSIDE_BACKUP)


class RefusalTests(unittest.TestCase):
    def _write_text(self, path, text):
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(text)

    def test_malformed_manifest_raises_naming_the_file(self):
        header = "source,dest,bytes,mtime_ns,action,reason\n"
        for label, text in (("header", "a,b\n"), ("bytes", header + "s,d,ten,1,copy,\n")):
            with self.subTest(label), tempfile.TemporaryDirectory() as base:
                world = World(base)
                path = os.path.join(world.out, "backup-manifest-1.csv")
                self._write_text(path, text)
                with self.assertRaisesRegex(ManifestError, "backup-manifest-1"):
                    world.evidence()

    def test_malformed_verified_list_raises_naming_the_file(self):
        header = ",".join(VERIFIED_COLUMNS)
        cases = (("missing column", "source,dest,bytes,sha256\ns,d,1,h\n"),
                 ("non-integer bytes", header + "\ns,d,ten,h,identical\n"),
                 ("short row", header + "\ns,d,1,h\n"))
        for label, text in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as base:
                world = World(base)
                world.write(1, [world.row()])
                self._write_text(os.path.join(world.out, "backup-verified-1.csv"), text)
                with self.assertRaisesRegex(ManifestError, "backup-verified-1"):
                    world.evidence()

    def test_missing_verified_list_raises_from_the_reader(self):
        with tempfile.TemporaryDirectory() as base:
            with self.assertRaises(ManifestError):
                read_verified_list(os.path.join(base, "absent.csv"))

    def test_read_verified_list_round_trips_every_state(self):
        items = [Verified(f"C:/src/shoot-a/IMG,{n}-é.ARW", f"D:/arch/IMG_{n}.ARW", n, "ab" * 32, state)
                 for n, state in enumerate(STATES)]
        with tempfile.TemporaryDirectory() as base:
            path = os.path.join(base, "verified.csv")
            write_verified(path, items)
            self.assertEqual(read_verified_list(path), items)


class EvidenceShapeTests(unittest.TestCase):
    def test_rows_are_keyed_by_normalised_source(self):
        with tempfile.TemporaryDirectory() as base:
            world = World(base)
            world.write(1, [world.row()])
            self.assertEqual(list(world.evidence().rows), [paths.norm(world.source)])
            self.assertIsInstance(world.evidence(), RootEvidence)


if __name__ == "__main__":
    unittest.main()
