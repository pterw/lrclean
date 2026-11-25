import csv
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from lrclean import backup_plan, backup_verify, cli, reject_archive, store
from mover_world import Outside, World

ROWS = [("IMG_0001.ARW", 3, 5, (), 0, ("D:/a/x", "E:/b/x"), "")]


class WorldCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        self.world = World(tmp.name)
        self.outside = os.path.join(elsewhere.name, "IMG_0001.ARW")  # never created
        self.inside = self.world.add("shoot-a/IMG_0001.ARW", b"abc", 5_000_000_000)

    def junction(self, link, target):
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.addCleanup(os.rmdir, link)  # the link only, never the target


class SeamTests(WorldCase):
    def test_a_path_outside_the_tree_raises_outside_for_each_seam(self):
        w = self.world
        s, tree = w.seams(), w.photo_root
        calls = {
            "volume_of": w.volume_of, "free_bytes": w.free_bytes,
            "add": lambda p: w.add(os.path.relpath(p, tree), b"x", 1), "archive": w.archive,
            "stat": lambda p: s.stat(p), "opener": lambda p: s.opener(p),
            "rename source": lambda p: s.rename(p, tree), "rename target": lambda p: s.rename(tree, p),
            "remove": lambda p: s.remove(p), "rmdir": lambda p: s.rmdir(p),
            "makedirs": lambda p: s.makedirs(p), "listdir": lambda p: s.listdir(p),
            "append": lambda p: s.append(p, b"x"), "write": lambda p: s.write(p, b"x"),
        }
        target = os.path.join(os.path.dirname(self.outside), "elsewhere-folder")
        os.makedirs(target)
        kept = os.path.join(target, "a.arw")
        with open(kept, "wb") as handle:
            handle.write(b"abc")
        link = os.path.join(tree, "alias")
        self.junction(link, target)
        for name, call in calls.items():
            for what, path in (("outside", self.outside), ("through a junction", os.path.join(link, "a.arw"))):
                with self.subTest(f"{name} {what}"), self.assertRaises(Outside):
                    call(path)
        self.assertFalse(os.path.exists(self.outside))
        with open(kept, "rb") as handle:
            self.assertEqual(handle.read(), b"abc")
        self.assertEqual(os.listdir(target), ["a.arw"])

    def test_write_given_the_trees_root_raises_outside_and_writes_no_sibling(self):
        sibling = self.world.tmp + ".tmp"
        with self.assertRaises(Outside):
            self.world.seams().write(self.world.tmp, b"x")
        self.assertFalse(os.path.exists(sibling))

    def test_scandir_and_exists_raise_outside_before_touching_a_path_outside_the_tree(self):
        folder = os.path.dirname(self.outside)  # exists, outside the tree
        keywords = self.world.cli_keywords()  # the seams cli.main is handed, not the methods
        for name in ("scandir", "exists"):
            with self.subTest(name), self.assertRaises(Outside):
                keywords[name](folder)
        self.assertTrue(os.path.isdir(folder))
        self.assertEqual(os.listdir(self.world.photo_root), ["shoot-a"])
        with keywords["scandir"](self.world.photo_root) as listing:
            self.assertEqual([e.name for e in listing], ["shoot-a"])
        self.assertIs(keywords["exists"](self.inside), True)
        self.assertIs(keywords["exists"](self.inside + ".missing"), False)

    def test_a_junction_inside_the_tree_to_a_folder_inside_it_is_allowed(self):
        link = os.path.join(self.world.tmp, "alias")
        self.junction(link, os.path.dirname(self.inside))
        through = os.path.join(link, "IMG_0001.ARW")
        self.assertEqual(self.world._inside(through), through)
        self.assertEqual(self.world.listdir(link), ["IMG_0001.ARW"])
        self.assertIs(self.world.exists(through), True)

    def test_a_command_with_catalog_roots_outside_the_tree_raises_outside_before_any_listing(self):
        folder = os.path.dirname(self.outside)
        config = os.path.join(self.world.base, "work", "config.json")
        with open(config, encoding="utf-8") as handle:
            cfg = json.load(handle)
        cfg["catalog_roots"] = [folder]
        with open(config, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(cfg, handle)
        conn = store.connect(os.path.join(self.world.base, "work", "lrclean.sqlite"))
        scan = store.begin_scan(conn, self.world.photo_root, "catalogs", now=lambda: "t")
        store.finish_scan(conn, scan, "complete", now=lambda: "t")
        conn.commit()
        conn.close()
        with open(os.path.join(self.world.base, "out", "rejects-summary.md"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"# Summary\n\n## Scans\n\n- catalogs scan {scan} of {self.world.photo_root}: complete, finished t\n")
        list_path, sha = self.world.removal_list([])
        listed = []
        with mock.patch("os.scandir", side_effect=lambda p: listed.append(p) or self.fail("real listing")):
            with self.assertRaises(Outside):
                self.world.main("stage", list_path, "--approved-sha256", sha)
        self.assertEqual(listed, [])

    def test_inside_is_the_tree_by_whole_path_components_after_resolving_dots(self):
        tmp = self.world.tmp
        cases = {
            "sibling with the tree's name as prefix": (tmp + "-x/f", False),
            "a climb out and into another folder": (os.path.join(tmp, "..", "other", "f"), False),
            "a climb that stays in the tree": (os.path.join(tmp, "a", "..", "f"), True),
            "the tree itself": (tmp, True),
            "forward slashes": (tmp.replace("\\", "/") + "/photos/f", True),
            "another case": (tmp.upper() + "/photos/f", True),
        }
        for name, (path, inside) in cases.items():
            with self.subTest(name):
                if inside:
                    self.assertEqual(self.world._inside(path), path)
                else:
                    with self.assertRaises(Outside):
                        self.world._inside(path)

    def test_a_path_inside_the_tree_goes_through_each_seam(self):
        s, folder = self.world.seams(), os.path.join(self.world.base, "work", "journal")
        s.makedirs(folder)
        journal, moved = os.path.join(folder, "j.jsonl"), os.path.join(folder, "moved")
        s.append(journal, b"a\n")
        s.append(journal, b"b\n")
        s.write(journal + "2", b"c")
        with s.opener(self.inside) as handle:
            content = handle.read()
        s.rename(self.inside, moved)
        s.remove(moved)
        s.makedirs(moved)
        s.rmdir(moved)
        self.assertEqual((s.stat(journal).st_size, sorted(s.listdir(folder)), content, os.path.exists(moved)),
                         (4, ["j.jsonl", "j.jsonl2"], b"abc", False))

    def test_the_two_archive_roots_and_the_photo_root_have_three_devs(self):
        other = self.world.add("shoot-a/IMG_0002.ARW", b"x", 1)
        dests = self.world.archive(self.inside)
        devs = [self.world.stat(p).st_dev for p in (self.inside, *dests)]
        self.assertEqual((len(set(devs)), self.world.stat(other).st_dev), (3, devs[0]))

    def test_ids_overrides_one_field_alone(self):
        real = os.stat(self.inside)
        base = self.world.stat(self.inside)
        other = self.world.add("shoot-a/IMG_0002.ARW", b"x", 1)
        kept = self.world.stat(other)
        for name, ids, expect in (("dev", (9, None), (9, real.st_ino)),
                                  ("ino", (None, 77), (base.st_dev, 77))):
            self.world.ids[self.inside] = ids
            with self.subTest(name):
                got = self.world.stat(self.inside)
                self.assertEqual((got.st_dev, got.st_ino), expect)
                again = self.world.stat(other)  # a second path keeps its computed pair
                self.assertEqual((again.st_dev, again.st_ino), (kept.st_dev, kept.st_ino))

    def test_is_link_is_false_unless_links_names_the_path(self):
        self.world.links.add(self.inside)
        self.assertEqual((self.world.is_link(self.inside), self.world.is_link(self.world.photo_root)),
                         (True, False))

    def test_listing_maps_each_path_to_size_and_mtime(self):
        self.assertEqual(self.world.listing(), {self.inside: (3, 5_000_000_000)})


class ArchiveTests(WorldCase):
    def test_archive_makes_archive_state_ok_for_the_source_in_both_roots(self):
        dests = self.world.archive(self.inside)
        evidence = reject_archive.load_evidence(os.path.join(self.world.base, "out"),
                                                self.world.archive_roots)
        got = [reject_archive.archive_state(e, self.inside, os.stat(self.inside)) for e in evidence]
        self.assertEqual(got, [("ok", dests[0]), ("ok", dests[1])])

    def test_archive_keeps_an_earlier_source_in_the_manifest(self):
        second = self.world.add("shoot-a/IMG_0002.ARW", b"xy", 6)
        self.world.archive(self.inside)
        self.world.archive(second)
        rows = backup_plan.read_manifest(os.path.join(self.world.base, "out", "backup-manifest-2.csv"))
        verified = backup_verify.read_verified_list(
            os.path.join(self.world.base, "out", "backup-verified-2.csv"))
        self.assertEqual(([r.source for r in rows], [v.sha256 for v in verified]),
                         ([self.inside, second],
                          [hashlib.sha256(b"abc").hexdigest(), hashlib.sha256(b"xy").hexdigest()]))
        for dest in self.world.archive_roots:  # an archive copy keeps the source's mtime
            self.assertEqual(os.stat(os.path.join(dest, "shoot-a", "IMG_0001.ARW")).st_mtime_ns,
                             5_000_000_000)

    def test_removal_list_returns_the_sha256_of_the_bytes_it_wrote(self):
        path, sha = self.world.removal_list(ROWS)
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertEqual((sha, data.startswith(b"\xef\xbb\xbf"), data.count(b"\r\n")),
                         (hashlib.sha256(data).hexdigest(), True, 2))

    def test_removal_list_has_the_reject_checks_header_and_row(self):
        path, _ = self.world.removal_list(ROWS)
        with open(path, encoding="utf-8-sig", newline="") as handle:
            records = list(csv.reader(handle))
        self.assertEqual(records, [
            ["source", "bytes", "mtime_ns", "sidecars", "sidecar_bytes", "dest_1", "dest_2", "twin"],
            ["IMG_0001.ARW", "3", "5", "", "0", "D:/a/x", "E:/b/x", ""]])


class MainTests(WorldCase):
    def run_values(self):
        w = self.world
        return cli._Run(args=None, cfg=cli.load_config(os.path.join(w.base, "work", "config.json")),
                        conn=None, base=w.base, processes=None, now=w.now, stat=w.stat,
                        attrs_of=w.attrs_of, exists=os.path.exists, volume_of=w.volume_of,
                        free_bytes=w.free_bytes, clock=w.clock, opener=w.opener, writer=None,
                        rename=w.rename, abort=w.abort, scandir=os.scandir)

    def test_the_worlds_volume_of_makes_require_archive_roots_accept_two_roots_in_one_tree(self):
        self.assertEqual(cli._require_archive_roots(self.run_values()), self.world.archive_roots)

    def test_main_hands_cli_main_the_worlds_volume_of_and_free_bytes_and_its_base(self):
        with mock.patch.object(cli, "main", return_value=0) as patched:
            self.world.main("report")
        (argv,), keywords = patched.call_args
        self.assertEqual((argv, keywords["volume_of"], keywords["free_bytes"]),
                         (["--base", self.world.base, "report"], self.world.volume_of,
                          self.world.free_bytes))

    def test_main_with_a_base_outside_the_tree_fails_before_cli_main_runs(self):
        inside, outside = self.world.base, self.outside
        forms = {
            "--base": ["--base", outside], "--base=": [f"--base={outside}"],
            "--b": ["--b", outside], "--ba": ["--ba", outside], "--bas=": [f"--bas={outside}"],
            "relative": ["--base", "base"], "relative with =": ["--base=."], "empty": ["--base="],
            "abbreviated relative": ["--ba", "rel"],
            "outside then inside": ["--base", outside, "--base", inside],
            "inside then outside": ["--base", inside, "--base", outside],
            "inside then abbreviated outside": ["--base", inside, f"--ba={outside}"],
            "an absolute argument outside": ["--base", inside, "x", outside],
            "an absolute argument outside after =": ["--base", inside, f"--x={outside}"],
            "rooted without a drive": ["--base", inside, "/x"],
            "drive-relative": ["--base", inside, "C:x"],
        }
        for form, args in forms.items():
            with self.subTest(form), mock.patch.object(cli, "main") as patched:
                with self.assertRaises(Outside):
                    self.world.main(*args, "report")
                patched.assert_not_called()

    def test_main_runs_a_command_on_the_archived_tree_through_the_seams(self):
        self.world.archive(self.inside)
        manifest = os.path.join(self.world.base, "out", "backup-manifest-1.csv")
        code, out, err = self.world.main("backup-verify", manifest)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("backup-verified-1.csv", out)


if __name__ == "__main__":
    unittest.main()
