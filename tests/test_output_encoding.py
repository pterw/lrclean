"""Output never fails on a path outside the console's code page (#16), checked in a real process."""

import os
import subprocess
import sys
import unittest

from test_cli import CliCase

PATH_NAME = "shoot-ţ"  # a letter that cp1252 cannot encode
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OutputEncodingTests(CliCase):
    def setUp(self):
        super().setUp()
        self.base = os.path.join(self.lib, PATH_NAME)
        self.arch = os.path.join(self.top, "arch1")
        self.write_config({**self.config, "archive_roots": [self.arch]})
        self.assertEqual(self.run_cli("catalogs")[0], 0)
        self.assertEqual(self.run_cli("inventory")[0], 0)

    def lrclean(self, *argv):
        """Run the tool in a process whose streams are redirected to files in a cp1252 environment."""
        env = {**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", "PYTHONPATH": ROOT}
        out, err = os.path.join(self.top, "stdout.txt"), os.path.join(self.top, "stderr.txt")
        with open(out, "wb") as out_file, open(err, "wb") as err_file:
            done = subprocess.run([sys.executable, "-m", "lrclean", "--base", self.base, *argv],
                                  stdout=out_file, stderr=err_file, env=env, timeout=120, cwd=self.top)
        with open(out, encoding="utf-8") as out_file, open(err, encoding="utf-8") as err_file:
            return done.returncode, out_file.read(), err_file.read()

    def test_a_path_outside_the_code_page_is_printed_to_stdout(self):
        with self.assertRaises(UnicodeEncodeError):  # the premise: the code page cannot hold it
            PATH_NAME.encode("cp1252")
        code, out, _ = self.lrclean("backup-plan")
        self.assertEqual(code, 0)
        self.assertIn(os.path.join(self.base, "out"), out)

    def test_a_path_outside_the_code_page_is_printed_to_stderr(self):
        missing = os.path.join(self.top, PATH_NAME, "manifest.csv")
        code, _, err = self.lrclean("backup-run", missing)
        self.assertEqual(code, 2)
        self.assertIn(missing, err)


if __name__ == "__main__":
    unittest.main()
