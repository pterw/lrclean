import unittest

from lrclean import verdict_views as vv
from lrclean.classify import NOT_RECORDED, Verdict

ALL_CLASSES = ["recorded", "safe_candidate", "coverage_incomplete", "unique_unrecorded",
               "not_compared", "needs_eye_no_recorded", "needs_eye_many_recorded",
               "same_file", "placeholder", "changed_since_scan"]


def verdict(i, klass, path=None, size=10, twin=None, group="", recorded=False, how="because"):
    return Verdict(i, path or f"C:/lib/shoot-a/IMG_{i:04d}.ARW", size, klass, twin, group, recorded, how)


class SafeCandidateTests(unittest.TestCase):
    def test_only_safe_candidates_with_twin_path_and_folder(self):
        twin = verdict(1, "recorded", "C:/lib/shoot-b/IMG_0001.ARW", recorded=True)
        safe = verdict(2, "safe_candidate", "C:/lib/shoot-a/IMG_0002.ARW", 7, twin=1, group="7:abc")
        others = [verdict(10 + n, k) for n, k in enumerate(ALL_CLASSES) if k != "safe_candidate"]
        rows = vv.safe_candidate_rows([twin, safe, *others])
        self.assertEqual(rows, [{
            "path": "C:/lib/shoot-a/IMG_0002.ARW", "folder": "C:/lib/shoot-a", "bytes": 7,
            "twin_path": "C:/lib/shoot-b/IMG_0001.ARW", "group": "7:abc", "how_decided": "because"}])

    def test_rows_are_in_path_order(self):
        twin = verdict(1, "recorded")
        later = verdict(3, "safe_candidate", "C:/lib/b/IMG.ARW", twin=1)
        earlier = verdict(2, "safe_candidate", "C:/lib/a/IMG.ARW", twin=1)
        rows = vv.safe_candidate_rows([twin, later, earlier])
        self.assertEqual([r["path"] for r in rows], ["C:/lib/a/IMG.ARW", "C:/lib/b/IMG.ARW"])

    def test_safe_candidate_without_its_twin_verdict_raises(self):
        with self.assertRaises(ValueError):
            vv.safe_candidate_rows([verdict(2, "safe_candidate", twin=99)])

    def test_no_safe_verdict_gives_no_rows(self):
        self.assertEqual(vv.safe_candidate_rows([verdict(1, "unique_unrecorded")]), [])


class NeedsEyeTests(unittest.TestCase):
    def test_each_kind_appears_for_its_class_and_no_other_class_does(self):
        expected = {"needs_eye_no_recorded": "no_recorded_member",
                    "needs_eye_many_recorded": "many_recorded", "same_file": "same_file",
                    "coverage_incomplete": "coverage_incomplete",
                    "changed_since_scan": "changed_since_scan", "not_compared": "not_compared"}
        vs = [verdict(n, k) for n, k in enumerate(ALL_CLASSES)]
        rows = vv.needs_eye_rows(vs)
        kind_of_path = {r["path"]: r["kind"] for r in rows}
        want = {v.path_orig: expected[v.klass] for v in vs if v.klass in expected}
        self.assertEqual(kind_of_path, want)
        self.assertEqual(len(rows), len(expected))

    def test_same_file_group_and_recorded_flag(self):
        rows = vv.needs_eye_rows([verdict(1, "same_file", group="c::42", recorded=True),
                                  verdict(2, "not_compared")])
        by_kind = {r["kind"]: r for r in rows}
        self.assertEqual(by_kind["same_file"]["group"], "c::42")
        self.assertEqual(by_kind["same_file"]["recorded"], "yes")
        self.assertEqual(by_kind["not_compared"]["recorded"], "no")

    def test_coverage_incomplete_row_keeps_the_verdict_how_decided(self):
        rows = vv.needs_eye_rows([verdict(1, "coverage_incomplete", how=NOT_RECORDED + "x")])
        self.assertTrue(rows[0]["how_decided"].startswith(
            "not recorded by the catalogs that could be read; coverage incomplete"))


class LargeUnreferencedTests(unittest.TestCase):
    def setUp(self):
        self.verdicts = [
            verdict(1, "unique_unrecorded", "C:/lib/a-small/IMG_1.ARW", 5),
            verdict(2, "unique_unrecorded", "C:/lib/b-big/IMG_2.ARW", 100),
            verdict(3, "unique_unrecorded", "C:/lib/b-big/IMG_3.ARW", 50),
            *[verdict(10 + n, k, "C:/lib/other/IMG.ARW", 999)
              for n, k in enumerate(ALL_CLASSES) if k != "unique_unrecorded"],
        ]

    def test_per_folder_totals_largest_first_and_only_unique_unrecorded(self):
        rows = vv.large_unreferenced_rows(self.verdicts, coverage_complete=True)
        self.assertEqual([(r["folder"], r["files"], r["bytes"]) for r in rows],
                         [("C:/lib/b-big", 2, 150), ("C:/lib/a-small", 1, 5)])

    def test_incomplete_coverage_is_said_on_every_row(self):
        rows = vv.large_unreferenced_rows(self.verdicts, coverage_complete=False)
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("coverage incomplete", r["how_decided"])

    def test_complete_coverage_does_not_claim_incomplete(self):
        for r in vv.large_unreferenced_rows(self.verdicts, coverage_complete=True):
            self.assertNotIn("coverage incomplete", r["how_decided"])


class CommonTests(unittest.TestCase):
    def test_rows_have_how_decided_and_are_deterministic(self):
        vs = [verdict(n, k, how="why", twin=0 if k == "safe_candidate" else None)
              for n, k in enumerate(ALL_CLASSES)]
        calls = (lambda: vv.safe_candidate_rows(vs), lambda: vv.needs_eye_rows(vs),
                 lambda: vv.large_unreferenced_rows(vs, coverage_complete=True))
        for call in calls:
            first = call()
            self.assertTrue(first)
            self.assertTrue(all(r["how_decided"] for r in first))
            self.assertEqual(first, call())


if __name__ == "__main__":
    unittest.main()
