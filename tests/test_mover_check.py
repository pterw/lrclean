import hashlib
import os
import tempfile
import unittest

from lrclean import mover_check, paths, reject_archive
from lrclean.mover_list import ListRow
from mover_world import TREE_DEV, World

MTIME = 1_700_000_000_000_000_000
RAW_DATA = b"raw-data-0001"


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FailingRead:
    """A handle that opened, whose read() fails as a bad sector would; `reads` counts the attempts."""
    def __init__(self, handle):
        self.handle, self.reads = handle, 0

    def read(self, size=-1):
        self.reads += 1
        raise OSError(5, "a bad sector")

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class CheckCase(unittest.TestCase):
    """A reject, two sidecars and a twin, archived on both roots."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.world = World(tmp.name)
        self.ctx = self.world.ctx()
        self.ticks = 0
        self.reject = self.world.add("shoot-a/IMG_0001.ARW", RAW_DATA, MTIME)
        self.side1 = self.world.add("shoot-a/IMG_0001.xmp", b"<xmp>one</xmp>", MTIME + 1_000_000)
        self.side2 = self.world.add("shoot-a/IMG_0001.dop", b"dop-two", MTIME + 2_000_000)
        self.twin = self.world.add("shoot-b/IMG_0001_copy.ARW", RAW_DATA, MTIME + 5_000_000)
        for path in (self.reject, self.side1, self.side2):
            self.world.archive(path)
        self.row = ListRow(self.reject, len(RAW_DATA), MTIME, (self.side1, self.side2),
                           len(b"<xmp>one</xmp>") + len(b"dop-two"), tuple(self.dests(self.reject)), self.twin)

    def tick(self):
        self.ticks += 1

    def dests(self, path):
        rel = os.path.relpath(path, self.world.photo_root)
        return [os.path.join(root, rel) for root in self.world.archive_roots]

    def evidence(self):
        return reject_archive.load_evidence(os.path.join(self.world.base, "out"), self.world.archive_roots,
                                            stat=self.world.stat)

    def check(self, row=None, ctx=None, **keywords):
        return mover_check.check_row(row or self.row, 3, evidence=self.evidence(), ctx=ctx or self.ctx,
                                     tick=self.tick, **keywords)

    def with_seams(self, **seams):
        return self.ctx._replace(seams=self.ctx.seams._replace(**seams))

    def rewrite(self, path, data, mtime_ns=None):
        with open(path, "wb") as handle:
            handle.write(data)
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))

    def restore(self, path):
        with open(path, "rb") as handle:
            data = handle.read()
        for dest in self.dests(path):
            self.rewrite(dest, data)

    def placeholder(self, path):
        self.world.attrs[paths.norm(path)] = paths.PLACEHOLDER_MASK

    def assert_held(self, result, reason, file):
        sidecars = {os.path.basename(self.side1), os.path.basename(self.side2)}
        kind = "sidecar" if os.path.basename(file) in sidecars else "reject"
        self.assertEqual((result.state, result.reason, result.file, result.files, result.kind),
                         ("held", reason, file, (), kind))


class RowFieldTests(CheckCase):
    def test_rule_35_a_sound_row_passes_with_the_facts_of_every_file(self):
        result = self.check()
        self.assertEqual((result.index, result.row, result.state, result.reason, result.file, result.kind),
                         (3, self.row, "ok", "", "", ""))
        self.assertEqual([(f.kind, f.path) for f in result.files],
                         [("reject", self.reject), ("sidecar", self.side1), ("sidecar", self.side2)])
        first = result.files[0]
        st = os.stat(self.reject)
        self.assertEqual((first.bytes, first.mtime_ns, first.file_index, first.sha256, first.twin, first.twin_sha256),
                         (len(RAW_DATA), MTIME, st.st_ino, sha_of(RAW_DATA), self.twin, sha_of(RAW_DATA)))
        self.assertEqual(first.dests, tuple((d, sha_of(RAW_DATA)) for d in self.row.dests))
        self.assertEqual((result.files[1].twin, result.files[1].twin_sha256), ("", ""))
        self.assertEqual(result.files[1].dests, tuple((d, sha_of(b"<xmp>one</xmp>")) for d in self.dests(self.side1)))

    def test_rule_53_a_row_of_kind_copy_is_checked_as_one_of_kind_reject_and_keeps_its_kind(self):
        other = os.path.join(self.world.archive_roots[0], "other.arw")
        for kind in ("reject", "copy"):
            row = self.row._replace(kind=kind)
            with self.subTest(kind, case="sound"):
                result = self.check(row)
                self.assertEqual(result.state, "ok")
                self.assertEqual([f.kind for f in result.files], [kind, "sidecar", "sidecar"])
                self.assertEqual((result.files[0].twin, result.files[0].twin_sha256), (self.twin, sha_of(RAW_DATA)))
                self.assertEqual((result.files[1].twin, result.files[1].twin_sha256), ("", ""))
            with self.subTest(kind, case="a listed dest_2 that is not the evidence's"):
                result = self.check(row._replace(dests=(row.dests[0], other)))
                self.assertEqual((result.state, result.reason, result.file, result.kind),
                                 ("held", "no_archive_copy", self.dests(self.reject)[1], kind))
            with self.subTest(kind, case="a twin of other content"):
                self.rewrite(self.twin, b"raw-data-XXXX", MTIME + 5_000_000)
                result = self.check(row)
                self.assertEqual((result.state, result.reason, result.file, result.kind),
                                 ("held", "twin_mismatch", self.twin, kind))
                self.rewrite(self.twin, RAW_DATA, MTIME + 5_000_000)
            with self.subTest(kind, case="no twin: the two archive copies alone"):
                result = self.check(row._replace(twin=""))
                self.assertEqual((result.state, result.files[0].kind, result.files[0].twin), ("ok", kind, ""))
            with self.subTest(kind, case="a listed size one off is changed"):
                result = self.check(row._replace(bytes=len(RAW_DATA) + 1))
                self.assertEqual((result.state, result.reason, result.file, result.kind),
                                 ("held", "changed", self.reject, kind))
            with self.subTest(kind, case="a source that is gone is missing"):
                os.remove(self.reject)
                result = self.check(row)
                self.assertEqual((result.state, result.reason, result.kind), ("held", "missing", kind))
                self.rewrite(self.reject, RAW_DATA, MTIME)

    def test_rule_35_a_row_without_a_twin_needs_none(self):
        result = self.check(self.row._replace(twin=""))
        self.assertEqual((result.state, result.files[0].twin, result.files[0].twin_sha256), ("ok", "", ""))

    def test_rule_35_the_size_or_the_time_of_the_reject_differing_alone_holds_the_row(self):
        cases = {"size": self.row._replace(bytes=len(RAW_DATA) + 1), "time": self.row._replace(mtime_ns=MTIME + 100)}
        for field, row in cases.items():
            with self.subTest(field):
                self.assert_held(self.check(row), "changed", self.reject)

    def test_rule_35_a_reject_or_sidecar_that_is_gone_or_unreadable_is_missing(self):
        def unreadable(path):
            def stat(target):
                if paths.norm(target) == paths.norm(path):
                    raise PermissionError(target)
                return self.world.stat(target)
            return stat

        for path in (self.reject, self.side1, self.side2):
            with self.subTest(path=path, how="unreadable"):
                ctx = self.with_seams(stat=unreadable(path))
                self.assert_held(self.check(ctx=ctx), "missing", path)
        os.remove(self.side2)
        self.assert_held(self.check(), "missing", self.side2)
        os.remove(self.reject)
        self.assert_held(self.check(), "missing", self.reject)

    def test_rule_35_a_folder_that_cannot_be_listed_holds_its_row_as_missing(self):
        elsewhere = os.path.join(self.world.photo_root, "shoot-c", "IMG_0001.xmp")
        cases = {"reject": (self.row, os.path.dirname(self.reject), self.reject),
                 "sidecar": (self.row._replace(sidecars=(self.side1, elsewhere)), os.path.dirname(elsewhere),
                             elsewhere)}
        for case, (row, folder, path) in cases.items():
            with self.subTest(case):
                def listdir(target):
                    if paths.norm(target) == paths.norm(folder):
                        raise PermissionError(target)
                    return self.world.listdir(target)

                self.assert_held(self.check(row, ctx=self.with_seams(listdir=listdir)), "missing", path)

    def test_rule_35_a_placeholder_holds_the_row_whichever_file_it_is(self):
        for path in (self.reject, self.side1, self.side2):
            with self.subTest(path=path):
                self.world.attrs.clear()
                self.placeholder(path)
                self.assert_held(self.check(), "placeholder", path)

    def test_rule_35_a_name_differing_in_case_is_found_and_its_spelling_on_disk_is_kept(self):
        def respell(path, name):
            return os.path.join(os.path.dirname(path), name)

        row = self.row._replace(source=respell(self.reject, "img_0001.arw"),
                                sidecars=(respell(self.side1, "IMG_0001.XMP"), self.side2))
        result = self.check(row)
        self.assertEqual(result.state, "ok")
        self.assertEqual([f.path for f in result.files], [self.reject, self.side1, self.side2])

    def test_rule_35_two_entries_of_one_casefold_name_are_ambiguous(self):
        for path in (self.reject, self.side1):
            with self.subTest(path=path):
                extra = os.path.basename(path).lower()
                ctx = self.with_seams(listdir=lambda target: [*self.world.listdir(target), extra])
                self.assert_held(self.check(ctx=ctx), "name_ambiguous", path)

    def test_rule_35_a_sidecar_name_holding_a_semicolon_is_split_and_its_parts_are_not_found(self):
        stem, tail = os.path.join(self.world.photo_root, "shoot-a", "IMG_0001;1.xmp").split(";")
        for case, sidecars in (("first part", (stem, tail)), ("second part alone", (self.side1, tail))):
            with self.subTest(case):
                row = self.row._replace(sidecars=sidecars)
                result = self.check(row)
                self.assertEqual((result.state, result.reason, result.files), ("held", "missing", ()))

    def test_rule_35_sidecar_sizes_must_sum_to_sidecar_bytes(self):
        for delta in (1, -1):
            with self.subTest(delta=delta):
                row = self.row._replace(sidecar_bytes=self.row.sidecar_bytes + delta)
                self.assert_held(self.check(row), "changed", self.reject)

    def test_rule_35_a_sidecar_that_differs_from_its_manifest_row_is_changed(self):
        for how, change in (("size", lambda: self.rewrite(self.side2, b"dop-twoX", MTIME + 2_000_000)),
                            ("time", lambda: self.rewrite(self.side2, b"dop-two", MTIME + 3_000_000))):
            with self.subTest(how):
                change()
                size = os.stat(self.side2).st_size
                row = self.row._replace(sidecar_bytes=len(b"<xmp>one</xmp>") + size)
                self.assert_held(self.check(row), "changed", self.side2)

    def test_rule_35_the_dests_must_equal_the_manifests_in_each_position(self):
        other = os.path.join(self.world.archive_roots[0], "other.arw")
        for i in (0, 1):
            with self.subTest(position=i):
                dests = list(self.row.dests)
                dests[i] = other
                self.assert_held(self.check(self.row._replace(dests=tuple(dests))), "no_archive_copy",
                                 self.dests(self.reject)[i])

    def test_rule_35_a_dest_spelled_in_another_case_or_with_forward_slashes_is_still_matched(self):
        for how, respell in (("case", str.swapcase), ("slashes", lambda d: d.replace("\\", "/"))):
            with self.subTest(how):
                dests = tuple(respell(d) for d in self.row.dests)
                self.assertNotEqual(dests, self.row.dests)
                self.assertEqual(self.check(self.row._replace(dests=dests)).state, "ok")

    def test_rule_35_every_file_passes_the_checks_without_content_before_any_file_is_hashed(self):
        calls = []

        def counting(path):
            calls.append(path)
            return self.world.opener(path)

        for dest in self.dests(self.side2):
            os.remove(dest)
        result = self.check(ctx=self.with_seams(opener=counting))
        self.assert_held(result, "no_archive_copy", self.dests(self.side2)[0])
        self.assertEqual(calls, [])

    def test_rule_35_a_file_with_no_archive_copy_is_held_whichever_file_it_is(self):
        for path in (self.reject, self.side1, self.side2):
            with self.subTest(path=path):
                for dest in self.dests(path):
                    os.remove(dest)
                self.assert_held(self.check(), "no_archive_copy", self.dests(path)[0])
                self.restore(path)

    def test_rule_35_a_sidecar_the_backup_never_recorded_is_held_naming_the_sidecar_itself(self):
        added = self.world.add("shoot-a/IMG_0001.pp3", b"added-later", MTIME + 3_000_000)
        row = self.row._replace(sidecars=(self.side1, added),
                                sidecar_bytes=len(b"<xmp>one</xmp>") + len(b"added-later"))
        result = self.check(row)
        self.assertEqual((result.state, result.reason, result.file, result.kind),
                         ("held", "no_archive_copy", added, "sidecar"))

    def test_rule_35_a_reject_the_backup_never_recorded_is_held_naming_the_reject_itself(self):
        added = self.world.add("shoot-a/IMG_0002.ARW", b"raw-added", MTIME + 4_000_000)
        row = ListRow(added, len(b"raw-added"), MTIME + 4_000_000, (), 0, (), "")
        result = self.check(row)
        self.assertEqual((result.state, result.reason, result.file, result.kind),
                         ("held", "no_archive_copy", added, "reject"))

    def test_rule_35_an_archive_copy_of_another_size_is_no_archive_copy(self):
        for i in (0, 1):
            for case, data in (("longer", RAW_DATA + b"x"), ("truncated", RAW_DATA[:-1])):
                with self.subTest(root=i + 1, case=case):
                    self.rewrite(self.dests(self.reject)[i], data)
                    self.assert_held(self.check(), "no_archive_copy", self.dests(self.reject)[i])
                    self.restore(self.reject)

    def test_rule_35_an_archive_copy_that_is_a_placeholder_is_no_archive_copy(self):
        self.placeholder(self.dests(self.reject)[0])
        self.assert_held(self.check(), "no_archive_copy", self.dests(self.reject)[0])


class CopyTests(CheckCase):
    def test_rule_35_the_copy_of_either_root_differing_alone_is_an_archive_mismatch(self):
        for i in (0, 1):
            with self.subTest(root=i + 1):
                self.rewrite(self.dests(self.reject)[i], b"raw-data-XXXX")
                self.assert_held(self.check(), "archive_mismatch", self.dests(self.reject)[i])
                self.restore(self.reject)

    def test_rule_35_two_equal_copies_that_differ_from_the_source_are_an_archive_mismatch(self):
        self.rewrite(self.reject, b"raw-data-YYYY", MTIME)
        self.assert_held(self.check(), "archive_mismatch", self.dests(self.reject)[0])

    def test_rule_36_each_file_of_a_family_failing_alone_holds_the_family_and_no_file_is_listed(self):
        for path in (self.reject, self.side1, self.side2):
            for i in (0, 1):
                with self.subTest(path=path, root=i + 1):
                    self.rewrite(self.dests(path)[i], b"x" * os.path.getsize(path))
                    self.assert_held(self.check(), "archive_mismatch", self.dests(path)[i])
                    self.restore(path)

    def test_rule_35_a_destination_on_the_volume_of_the_source_is_an_archive_mismatch(self):
        for i in (0, 1):
            with self.subTest(root=i + 1):
                self.world.ids.clear()
                self.world.ids[self.dests(self.reject)[i]] = (TREE_DEV, None)
                self.assert_held(self.check(), "archive_mismatch", self.dests(self.reject)[i])

    def test_rule_35_two_destinations_on_one_volume_other_than_the_sources_are_an_archive_mismatch(self):
        dev_1 = self.world.stat(self.dests(self.reject)[0]).st_dev
        self.world.ids[self.dests(self.reject)[1]] = (dev_1, None)
        self.assert_held(self.check(), "archive_mismatch", self.dests(self.reject)[1])

    def test_rule_35_a_sidecar_with_both_copies_on_one_volume_holds_the_family(self):
        dev_1 = self.world.stat(self.dests(self.side2)[0]).st_dev
        self.world.ids[self.dests(self.side2)[1]] = (dev_1, None)
        self.assert_held(self.check(), "archive_mismatch", self.dests(self.side2)[1])

    def test_rule_35_two_destinations_or_a_destination_and_the_source_being_one_file_is_an_archive_mismatch(self):
        source = self.world.stat(self.reject)
        dest_1 = self.world.stat(self.dests(self.reject)[0])
        cases = {"two destinations": (self.dests(self.reject)[1], (dest_1.st_dev, dest_1.st_ino)),
                 "a destination that is the source": (self.dests(self.reject)[0], (source.st_dev, source.st_ino))}
        for case, (path, pair) in cases.items():
            with self.subTest(case):
                self.world.ids.clear()
                self.world.ids[path] = pair
                self.assert_held(self.check(), "archive_mismatch", path)

    def test_rule_35_a_read_error_holds_the_row_at_the_file_whose_read_failed(self):
        def failing(path):
            def opener(target):
                if paths.norm(target) == paths.norm(path):
                    raise PermissionError(target)
                return self.world.opener(target)
            return self.with_seams(opener=opener)

        files = [self.reject, self.side1, self.side2, *self.dests(self.reject), self.dests(self.side2)[1], self.twin]
        self.assertEqual(len({paths.norm(path) for path in files}), 7)
        for path in files:
            with self.subTest(path=path):
                self.assert_held(self.check(ctx=failing(path)), "read_error", path)
        with self.subTest("a row without a twin"):
            self.assert_held(self.check(self.row._replace(twin=""), ctx=failing(self.reject)),
                             "read_error", self.reject)
        with self.subTest("a read failing after the open"):
            self.assert_read_fails_after_the_open()
        with self.subTest("an error that is not an OSError propagates, never held as read_error"):
            def broken(target):
                raise ValueError(target)
            with self.assertRaises(ValueError):
                self.check(ctx=self.with_seams(opener=broken))

    def assert_read_fails_after_the_open(self):
        opened, handles = [], []

        def opener(target):
            handle = self.world.opener(target)
            if paths.norm(target) != paths.norm(self.side1):
                return handle
            opened.append(target)
            handles.append(FailingRead(handle))
            return handles[-1]

        result = self.check(self.row._replace(twin=""), ctx=self.with_seams(opener=opener))
        self.assertEqual((len(opened), handles[0].reads), (1, 1))
        self.assert_held(result, "read_error", self.side1)


class TwinTests(CheckCase):
    def test_rule_35_a_twin_differing_by_content_by_size_or_missing_is_a_twin_mismatch(self):
        cases = {"content": lambda: self.rewrite(self.twin, b"raw-data-XXXX"),
                 "size": lambda: self.rewrite(self.twin, RAW_DATA + b"x"),
                 "missing": lambda: os.remove(self.twin)}
        for case, change in cases.items():
            with self.subTest(case):
                change()
                self.assert_held(self.check(), "twin_mismatch", self.twin)
                self.rewrite(self.twin, RAW_DATA)

    def test_rule_35_a_twin_that_is_a_placeholder_is_a_twin_mismatch(self):
        self.placeholder(self.twin)
        self.assert_held(self.check(), "twin_mismatch", self.twin)

    def test_rule_35_a_twin_that_is_the_reject_or_a_destination_is_a_twin_mismatch(self):
        source = self.world.stat(self.reject)
        dest_1 = self.world.stat(self.dests(self.reject)[0])
        for case, pair in (("the reject", (source.st_dev, source.st_ino)),
                           ("a destination", (dest_1.st_dev, dest_1.st_ino))):
            with self.subTest(case):
                self.world.ids[self.twin] = pair
                self.assert_held(self.check(), "twin_mismatch", self.twin)


class DryRunTests(CheckCase):
    def test_rule_42_without_hashing_no_content_is_read_and_the_hashes_are_empty(self):
        def refuse(path):
            raise AssertionError(f"content read: {path}")

        self.rewrite(self.dests(self.reject)[0], b"raw-data-XXXX")  # same size: only a hash would tell
        result = self.check(ctx=self.with_seams(opener=refuse), hash_content=False)
        self.assertEqual(result.state, "ok")
        self.assertEqual({f.sha256 for f in result.files} | {f.twin_sha256 for f in result.files}
                         | {h for f in result.files for _, h in f.dests}, {""})
        self.assertEqual(self.ticks, 0)

    def test_rule_42_without_hashing_the_stat_tests_still_hold_the_row(self):
        def refuse(path):
            raise AssertionError(f"content read: {path}")

        ctx = self.with_seams(opener=refuse)
        self.world.ids[self.dests(self.reject)[0]] = (TREE_DEV, None)
        self.assert_held(self.check(ctx=ctx, hash_content=False), "archive_mismatch", self.dests(self.reject)[0])
        self.world.ids.clear()
        self.rewrite(self.twin, RAW_DATA + b"x")
        self.assert_held(self.check(ctx=ctx, hash_content=False), "twin_mismatch", self.twin)

    def test_rule_43_every_read_of_content_ticks(self):
        self.check()
        self.assertGreater(self.ticks, 0)


class VerifyCopiesTests(CheckCase):
    def verify(self, *, path=None, size=len(RAW_DATA), dests=None, twin=None, expect="", roots=None, seen=None):
        return mover_check.verify_copies(path or self.reject, size, self.dests(self.reject) if dests is None else dests,
                                         self.twin if twin is None else twin, expect=expect,
                                         roots=roots or self.world.archive_roots, ctx=self.ctx, tick=self.tick,
                                         seen=[] if seen is None else seen)

    def test_rule_41_sound_copies_give_the_hash_and_no_reason(self):
        self.assertEqual(self.verify(), ("", sha_of(RAW_DATA)))
        self.assertEqual(self.verify(expect=sha_of(RAW_DATA)), ("", sha_of(RAW_DATA)))
        self.assertEqual(self.verify(twin=""), ("", sha_of(RAW_DATA)))

    def test_rule_41_a_hash_differing_from_expect_while_the_copies_agree_is_staged_changed(self):
        self.assertEqual(self.verify(expect=sha_of(b"something else")), ("staged_changed", self.reject))

    def test_rule_41_a_file_of_another_size_is_changed_at_stage_and_staged_changed_at_purge(self):
        self.assertEqual(self.verify(size=len(RAW_DATA) + 1), ("changed", self.reject))
        self.assertEqual(self.verify(size=len(RAW_DATA) + 1, expect=sha_of(RAW_DATA)),
                         ("staged_changed", self.reject))

    def test_rule_41_a_destination_outside_its_root_is_an_archive_mismatch(self):
        first, second = self.dests(self.reject)
        root_1, root_2 = self.world.archive_roots
        cases = {"swapped": ([second, first], second), "the root itself": ([first, root_2], root_2),
                 "one destination for two roots": ([first], self.reject)}
        for case, (dests, failing) in cases.items():
            with self.subTest(case):
                self.assertEqual(self.verify(dests=dests, roots=[root_1, root_2]), ("archive_mismatch", failing))

    def test_rule_41_each_copy_that_differs_or_is_of_another_size_is_an_archive_mismatch(self):
        for i in (0, 1):
            for case, data in (("content", b"raw-data-XXXX"), ("size", RAW_DATA + b"x")):
                with self.subTest(root=i + 1, case=case):
                    self.rewrite(self.dests(self.reject)[i], data)
                    self.assertEqual(self.verify(), ("archive_mismatch", self.dests(self.reject)[i]))
                    self.restore(self.reject)

    def test_rule_41_two_destinations_on_one_volume_or_the_volume_of_the_file_are_an_archive_mismatch(self):
        first, second = self.dests(self.reject)
        self.world.ids[second] = (self.world.stat(first).st_dev, None)
        self.assertEqual(self.verify(), ("archive_mismatch", second))
        self.world.ids.clear()
        self.world.ids[first] = (TREE_DEV, None)
        self.assertEqual(self.verify(), ("archive_mismatch", first))

    def test_rule_41_a_twin_that_differs_is_a_twin_mismatch(self):
        self.rewrite(self.twin, b"raw-data-XXXX")
        self.assertEqual(self.verify(), ("twin_mismatch", self.twin))

    def test_rule_41_every_file_read_is_handed_back_with_its_stat_and_the_reason_that_holds_it(self):
        seen = []
        self.verify(expect=sha_of(RAW_DATA), seen=seen)
        first, second = self.dests(self.reject)
        self.assertEqual([(item.path, item.reason) for item in seen],
                         [(self.reject, "staged_changed"), (first, "archive_mismatch"),
                          (second, "archive_mismatch"), (self.twin, "twin_mismatch")])
        self.assertTrue(all(not mover_check.moved(item, self.ctx) for item in seen))

    def test_rule_43_a_tick_follows_every_block_read(self):
        data = bytes(range(256)) * (3 * 1024 * 1024 // 256)  # three blocks of the 1 MiB read
        for path in (self.reject, self.twin, *self.dests(self.reject)):
            self.rewrite(path, data)
        events = []
        self.tick = lambda: events.append("tick")  # `verify` reads self.tick when it is called

        class Recording:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *exc):
                return self.handle.__exit__(*exc)

            def read(self, *args):
                events.append("read")
                return self.handle.read(*args)

        self.ctx = self.with_seams(opener=lambda path: Recording(self.world.opener(path)))
        self.assertEqual(self.verify(size=len(data)), ("", sha_of(data)))
        reads = [i for i, event in enumerate(events) if event == "read"]
        self.assertEqual(len(reads), 16)  # four files, three blocks and the empty end read each
        self.assertTrue(all(events[i + 1] == "tick" for i in reads))

