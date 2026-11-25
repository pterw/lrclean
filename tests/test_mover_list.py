import csv
import ctypes
import hashlib
import io
import os
import tempfile
import unittest

from lrclean import mover_list
from lrclean.mover_list import ListError, ListRow

HEADER = ["source", "bytes", "mtime_ns", "sidecars", "sidecar_bytes", "dest_1", "dest_2", "twin"]


def entry(source, sidecars="", *, nbytes="10", mtime="5", side_bytes="0", d1="E:/a/x", d2="F:/b/x", twin=""):
    return [source, nbytes, mtime, sidecars, side_bytes, d1, d2, twin]


def make(rows, header=None, *, bom=False):
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\r\n").writerows([header or HEADER, *rows])
    return (b"\xef\xbb\xbf" if bom else b"") + out.getvalue().encode("utf-8")


def read(rows, header=None, roots=2):
    return mover_list.read_list(make(rows, header), roots)


class ApprovedShaTest(unittest.TestCase):
    def test_rule32_accepts_any_case_and_returns_lowercase(self):
        low = "ab12" * 16
        self.assertEqual(mover_list.approved_sha(low), low)
        self.assertEqual(mover_list.approved_sha(low.upper()), low)
        self.assertEqual(mover_list.approved_sha("aB" * 32), "ab" * 32)

    def test_rule32_refuses_wrong_length_and_non_hex(self):
        for text in ("a" * 63, "a" * 65, "", "a" * 63 + "g", "g" + "a" * 63, "a" * 64 + "\n", "a" * 63 + "\u0661"):
            with self.subTest(text=text), self.assertRaises(ListError):
                mover_list.approved_sha(text)


class ReadListTest(unittest.TestCase):
    def test_rule32_reads_every_field_from_bytes_with_bom_and_crlf(self):
        data = make([entry("C:/p/a.arw", "C:/p/a.xmp;C:/p/a.acr", side_bytes="7", twin="C:/p/t.arw")], bom=True)
        row, = mover_list.read_list(data, 2)
        self.assertEqual(row, ListRow("C:/p/a.arw", 10, 5, ("C:/p/a.xmp", "C:/p/a.acr"), 7,
                                      ("E:/a/x", "F:/b/x"), "C:/p/t.arw"))

    def test_rule32_empty_list_gives_no_rows(self):
        self.assertEqual(read([]), [])

    def test_rule52_every_row_takes_the_kind_of_the_list_it_is_read_with(self):
        data = make([entry("C:/p/a.arw"), entry("C:/p/b.arw", "C:/p/b.xmp", side_bytes="3")])
        self.assertEqual({row.kind for row in mover_list.read_list(data, 2)}, {"reject"})
        for listing in mover_list.LISTS:
            with self.subTest(listing.kind):
                rows = mover_list.read_list(data, 2, listing.kind)
                self.assertEqual([row.kind for row in rows], [listing.kind] * 2)

    def test_rule52_a_twin_that_is_the_source_of_a_row_is_refused_in_each_list_wherever_the_rows_stand(self):
        cases = {
            "the source after the row": [entry("C:/p/z.arw"), entry("C:/p/a.arw", twin="C:/p/b.arw"),
                                         entry("C:/p/b.arw")],
            "the source before the row": [entry("C:/p/b.arw"), entry("C:/p/z.arw"),
                                          entry("C:/p/a.arw", twin="C:/p/b.arw")],
            "another spelling": [entry("C:/p/z.arw"), entry("C:/p/a.arw", twin="c:\\P\\B.ARW"), entry("C:/p/b.arw")],
        }
        for listing in mover_list.LISTS:
            for name, rows in cases.items():
                with self.subTest(f"{listing.kind}: {name}"), self.assertRaises(ListError):
                    mover_list.read_list(make(rows), 2, listing.kind)
            ok = make([entry("C:/p/z.arw"), entry("C:/p/a.arw", twin="C:/p/t.arw"), entry("C:/p/b.arw")])
            self.assertEqual(len(mover_list.read_list(ok, 2, listing.kind)), 3)

    def test_rule32_header_must_be_exactly_the_reject_checks(self):
        cases = {
            "missing column": HEADER[:-1],
            "extra column": [*HEADER, "more"],
            "renamed": [*HEADER[:3], "sidecar", *HEADER[4:]],
            "renamed dest": [*HEADER[:5], "dest_one", *HEADER[6:]],
            "order": [HEADER[1], HEADER[0], *HEADER[2:]],
        }
        for name, header in cases.items():
            with self.subTest(name), self.assertRaises(ListError):
                mover_list.read_list(make([], header), 2)

    def test_rule32_roots_must_match_the_header(self):
        data = make([entry("C:/p/a.arw")])
        with self.assertRaises(ListError):
            mover_list.read_list(data, 3)
        with self.assertRaises(ListError):
            mover_list.read_list(data, 1)
        one = [*HEADER[:6], "twin"]
        with self.assertRaises(ListError):
            mover_list.read_list(make([["C:/p/a.arw", "1", "2", "", "0", "E:/x", ""]], one), 1)
        three = [*HEADER[:7], "dest_3", "twin"]
        row, = mover_list.read_list(make([["C:/p/a.arw", "1", "2", "", "0", "E:/x", "F:/x", "G:/x", ""]], three), 3)
        self.assertEqual(row.dests, ("E:/x", "F:/x", "G:/x"))

    def test_rule32_refuses_no_header_and_undecodable_bytes(self):
        for data in (b"", b"\xff\xfe\x00bad"):
            with self.subTest(data=data), self.assertRaises(ListError):
                mover_list.read_list(data, 2)

    def test_rule32_negative_or_malformed_numbers_refused_per_column(self):
        refused = ((1, "-1"), (4, "-1"), (1, "x"), (1, ""), (2, "1.5"), (2, "-"), (2, "--1"), (4, "1e3"),
                   (1, "\u00b2"), (2, "\u00b2"), (2, "-\u00b2"), (4, "\u00b2"))
        for column, cell in refused:
            cells = entry("C:/p/a.arw")
            cells[column] = cell
            with self.subTest(column=HEADER[column], cell=cell), self.assertRaises(ListError):
                read([cells])
        row, = read([entry("C:/p/a.arw", mtime="-5000000000000000")])
        self.assertEqual(row.mtime_ns, -5000000000000000)

    def test_rule32_row_of_wrong_width_refused(self):
        for cells in (entry("C:/p/a.arw")[:-1], [*entry("C:/p/a.arw"), "extra"], []):
            with self.subTest(width=len(cells)), self.assertRaises(ListError):
                read([entry("C:/p/b.arw"), cells])

    def test_rule32_path_twice_as_source_refused_by_norm(self):
        for other in ("C:/p/a.arw", "c:\\P\\A.ARW"):
            with self.subTest(other=other), self.assertRaises(ListError):
                read([entry("C:/p/a.arw"), entry("C:/p/b.arw"), entry(other)])

    def test_rule32_source_that_is_another_rows_sidecar_refused_in_either_order(self):
        first, second = entry("C:/p/a.arw", "C:/p/a.xmp"), entry("C:/p/a.xmp")
        for rows in ([first, second], [second, first]):
            with self.assertRaises(ListError):
                read(rows)
        with self.assertRaises(ListError):
            read([entry("C:/p/z.arw"), entry("C:/p/a.arw", "c:/P/A.XMP;C:/p/q.xmp"), second, entry("C:/p/y.arw")])

    def test_rule32_bad_names_refused_in_every_path_column(self):
        bad = ("C:/p/a.arw.", "C:/p/a.arw ", "C:/p/a.arw:stream", "C:/p/dir./a.arw", "C:/p /a.arw", "C:/p/a:b.arw")
        columns = {
            "source": lambda p: entry(p),
            "sidecar": lambda p: entry("C:/p/ok.arw", "C:/p/ok.xmp;" + p),
            "dest_1": lambda p: entry("C:/p/ok.arw", d1=p),
            "dest_2": lambda p: entry("C:/p/ok.arw", d2=p),
            "twin": lambda p: entry("C:/p/ok.arw", twin=p),
        }
        for column, build in columns.items():
            for name in bad:
                with self.subTest(column=column, name=name), self.assertRaises(ListError):
                    read([build(name)])

    def test_rule32_ordinary_names_with_a_drive_colon_and_inner_dots_pass(self):
        row, = read([entry("C:/shoot.a/IMG_0001.v2.ARW", "C:/shoot.a/IMG_0001.xmp", twin="D:/t/IMG_0001.ARW")])
        self.assertEqual(row.twin, "D:/t/IMG_0001.ARW")

    def test_rule35_name_holding_semicolon_is_split_into_parts(self):
        row, = read([entry("C:/p/a.arw", "C:/p/a;b.xmp")])
        self.assertEqual(row.sidecars, ("C:/p/a", "b.xmp"))

    def test_rule32_hash_covers_the_raw_bytes_that_were_parsed(self):
        data = make([entry("C:/p/a.arw")], bom=True)
        self.assertEqual(mover_list.list_sha256(data), hashlib.sha256(data).hexdigest())
        changed = data.replace(b"\r\n", b"\n")
        self.assertNotEqual(mover_list.list_sha256(changed), mover_list.list_sha256(data))
        self.assertNotEqual(mover_list.list_sha256(data[3:]), mover_list.list_sha256(data))


class SelectTest(unittest.TestCase):
    ROWS = read([entry("C:/p/a.arw"), entry("C:/p/b.arw"), entry("D:/q/Caf\u00e9.arw"), entry("C:/p/d.arw")])

    def test_rule32_select_matches_other_case_or_composition_in_list_order(self):
        got = mover_list.select(self.ROWS, ["c:\\P\\D.ARW", "D:/Q/Cafe\u0301.ARW", "C:/p/a.arw"])
        self.assertEqual([r.source for r in got], ["C:/p/a.arw", "D:/q/Caf\u00e9.arw", "C:/p/d.arw"])

    def test_rule32_select_refuses_unknown_name_even_beside_known_ones(self):
        with self.assertRaises(ListError):
            mover_list.select(self.ROWS, ["C:/p/a.arw", "C:/p/zzz.arw"])

    def test_rule32_select_without_only_keeps_all(self):
        self.assertEqual(mover_list.select(self.ROWS, []), list(self.ROWS))
        self.assertEqual(mover_list.select([], []), [])


class SharedSidecarsTest(unittest.TestCase):
    def test_rule36_two_rows_listing_one_sidecar_are_read_and_both_named(self):
        rows = read([entry("C:/p/z.arw", "C:/p/z.xmp"), entry("C:/p/a.arw", "C:/p/Shared.xmp"),
                     entry("C:/p/b.arw", "c:\\p\\q.xmp;c:/P/SHARED.xmp"), entry("C:/p/c.arw", "C:/p/c.xmp")])
        self.assertEqual(mover_list.shared_sidecars(rows), {1: "C:/p/Shared.xmp", 2: "C:/p/Shared.xmp"})

    def test_rule36_different_sidecars_and_empty_lists_give_empty_map(self):
        rows = read([entry("C:/p/a.arw", "C:/p/a.xmp"), entry("C:/p/b.arw", "C:/p/b.xmp"), entry("C:/p/c.arw"),
                     entry("C:/p/d.arw")])
        self.assertEqual([r.sidecars for r in rows[2:]], [(), ()])
        self.assertEqual(mover_list.shared_sidecars(rows), {})
        self.assertEqual(mover_list.shared_sidecars([]), {})

    def test_rule36_a_sidecar_twice_in_one_row_is_not_shared(self):
        rows = read([entry("C:/p/a.arw", "C:/p/a.xmp;C:/p/A.XMP"), entry("C:/p/b.arw")])
        self.assertEqual(mover_list.shared_sidecars(rows), {})

    def test_rule36_three_rows_one_path_all_named_with_the_first_listing(self):
        rows = read([entry("C:/p/a.arw", "C:/p/One.xmp"), entry("C:/p/b.arw"), entry("C:/p/c.arw", "c:/p/one.xmp"),
                     entry("C:/p/d.arw", "C:/P/ONE.XMP")])
        self.assertEqual(mover_list.shared_sidecars(rows), {0: "C:/p/One.xmp", 2: "C:/p/One.xmp", 3: "C:/p/One.xmp"})


def _short_name(path):
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
    return buf.value


class CheckListPathTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = os.path.join(tmp.name, "tool-folder-with-a-long-name")
        out = os.path.join(self.base, "out")
        os.makedirs(os.path.join(self.base, "work", "out"))
        os.makedirs(out)
        self.good = os.path.join(out, mover_list.LIST_NAME)
        for path in (self.good, os.path.join(out, "other.csv"), os.path.join(out, mover_list.LIST_NAME + ".bak"),
                     os.path.join(self.base, "work", "out", mover_list.LIST_NAME),
                     os.path.join(self.base, mover_list.LIST_NAME)):
            with open(path, "w", newline="\n") as handle:
                handle.write("x")

    def test_rule32_accepts_the_list_in_another_case_or_spelling(self):
        out = os.path.join(self.base, "out")
        spellings = [
            self.good,
            self.good.upper(),
            os.path.join(self.base, "work", "..", "out", mover_list.LIST_NAME),
            self.good.replace("\\", "/"),
            os.path.join(out, mover_list.LIST_NAME.upper()),
            self.good + ".",
            self.good + " ",
        ]
        try:
            spellings.append(os.path.relpath(self.good))
        except ValueError:  # another drive than the current directory: no relative spelling
            pass
        for path in spellings:
            with self.subTest(path=path):
                mover_list.check_list_path(path, self.base)
        mover_list.check_list_path(self.good, self.base.replace("\\", "/") + "/work/..")

    def test_rule32_refuses_a_copy_beside_or_elsewhere_and_other_names(self):
        refused = {
            "copy beside the out folder": os.path.join(self.base, mover_list.LIST_NAME),
            "copy under work": os.path.join(self.base, "work", "out", mover_list.LIST_NAME),
            "copy in another folder": os.path.join(os.path.dirname(self.base), mover_list.LIST_NAME),
            "other name in out": os.path.join(self.base, "out", "other.csv"),
            "suffix on the name": self.good + ".bak",
            "stream of the list": self.good + ":stream",
        }
        for name, path in refused.items():
            with self.subTest(name), self.assertRaises(ListError):
                mover_list.check_list_path(path, self.base)

    def test_rule52_each_list_name_gives_its_kind_and_a_third_name_or_the_copy_list_outside_out_is_refused(self):
        for listing in mover_list.LISTS:
            with self.subTest(listing.kind):
                found = mover_list.check_list_path(os.path.join(self.base, "out", listing.name.upper()), self.base)
                self.assertEqual(found, listing)
        refused = {
            "a third name": os.path.join(self.base, "out", "removal-list.csv"),
            "the copy list beside the out folder": os.path.join(self.base, mover_list.COPIES.name),
            "the copy list under work": os.path.join(self.base, "work", "out", mover_list.COPIES.name),
        }
        for name, path in refused.items():
            with self.subTest(name), self.assertRaises(ListError):
                mover_list.check_list_path(path, self.base)

    def test_rule32_refuses_the_short_name_alias_of_the_folder(self):
        # A volume that makes no 8.3 names gives back the long name; then the alias is built as text.
        short = _short_name(self.base) if os.name == "nt" else ""
        generated = bool(short) and short.lower() != self.base.lower()
        alias_base = short if generated else os.path.join(os.path.dirname(self.base), "TOOL-F~1")
        self.assertNotEqual(alias_base.lower(), self.base.lower())
        with self.assertRaises(ListError):
            mover_list.check_list_path(os.path.join(alias_base, "out", mover_list.LIST_NAME), self.base)


if __name__ == "__main__":
    unittest.main()
