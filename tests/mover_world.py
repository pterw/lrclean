"""A synthetic tree for the B2 mover tests (RC-61).

`World(tmp)` builds everything in one temporary directory and hands out seams that raise `Outside`,
before the call, for a path outside it. `stat` gives each archive root its own `st_dev` and the
photo root (and the rest of the tree) another, as one volume holds the tree. Nothing here touches
a real folder.
"""

import contextlib
import csv
import hashlib
import io
import json
import os
from types import SimpleNamespace

from lrclean import backup_plan, backup_verify, cli, hashing, paths, reject_out
from lrclean.mover_seams import Ctx, Folders, Seams, new_ctx

NOW = "2026-01-01T00:00:00Z"
BATCH = "20260101T000000Z"
TREE_DEV, FREE = 1, 10**12


class Outside(AssertionError):
    """A seam was called with a path outside the World's temporary directory."""


def _flat(row) -> list:
    source, size, mtime_ns, sidecars, sidecar_bytes, dests, twin = row
    return [source, size, mtime_ns, ";".join(sidecars), sidecar_bytes, *dests, twin]


def _absolute(text: str) -> bool:
    return os.path.isabs(text) or bool(os.path.splitdrive(text)[0]) or text.startswith(("/", "\\"))


class World:
    def __init__(self, tmp: str):
        self.tmp = os.path.abspath(tmp)
        self.base = os.path.join(self.tmp, "base")
        self.photo_root = os.path.join(self.tmp, "photos")
        self.archive_roots = [os.path.join(self.tmp, "archive-1"), os.path.join(self.tmp, "archive-2")]
        self.links: set[str] = set()
        self.ids: dict[str, tuple] = {}  # path -> (dev, ino); None keeps that field's computed value
        self.attrs: dict[str, int] = {}
        self.aborted: list = []
        self.ticks = 0
        self._archived: list[tuple[str, int, int, str]] = []  # source, size, mtime_ns, sha256
        for folder in (os.path.join(self.base, "work"), os.path.join(self.base, "out"),
                       self.photo_root, *self.archive_roots):
            os.makedirs(folder)
        config = {"library_roots": [self.photo_root], "backup_roots": [], "catalog_roots": [],
                  "photo_root": self.photo_root, "group_depth": 1, "exclude": [], "live_catalog": None,
                  "old_states": [], "excluded_catalogs": [], "archive_roots": self.archive_roots}
        with open(os.path.join(self.base, "work", "config.json"), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(config, handle)

    # the tree
    def path(self, rel: str) -> str:
        return os.path.join(self.photo_root, *rel.split("/"))

    def add(self, rel: str, data: bytes, mtime_ns: int) -> str:
        path = self._inside(self.path(rel))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def archive(self, path: str) -> list[str]:
        """Copy `path` to both roots; write both manifests and verified lists into out/ (B1)."""
        st = os.stat(self._inside(path))
        rel = os.path.relpath(path, self.photo_root)
        with open(path, "rb") as handle:
            data = handle.read()
        sha = hashlib.sha256(data).hexdigest()
        dests = [os.path.join(root, rel) for root in self.archive_roots]
        for dest in dests:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as handle:
                handle.write(data)
            os.utime(dest, ns=(st.st_mtime_ns, st.st_mtime_ns))
        self._archived.append((path, st.st_size, st.st_mtime_ns, sha))
        out = os.path.join(self.base, "out")
        for number, root in enumerate(self.archive_roots, start=1):
            rows = [backup_plan.Row(src, os.path.join(root, os.path.relpath(src, self.photo_root)),
                                    size, mtime, "copy", "") for src, size, mtime, _ in self._archived]
            backup_plan.write_manifest(os.path.join(out, f"backup-manifest-{number}.csv"), rows)
            items = [backup_verify.Verified(r.source, r.dest, r.bytes, entry[3], "identical")
                     for r, entry in zip(rows, self._archived)]
            backup_verify.write_verified(os.path.join(out, f"backup-verified-{number}.csv"), items)
        return dests

    def removal_list(self, rows) -> tuple[str, str]:
        """Write out/reject-removal-list.csv (utf-8-sig, CRLF); rows are
        (source, bytes, mtime_ns, sidecars, sidecar_bytes, dests, twin). Returns (path, sha256)."""
        header = reject_out._removal_rows([], len(self.archive_roots))[0]
        text = io.StringIO()
        writer = csv.writer(text)  # its default line end is CRLF
        writer.writerow(header)
        writer.writerows(_flat(row) for row in rows)
        data = text.getvalue().encode("utf-8-sig")
        path = os.path.join(self.base, "out", reject_out.REMOVAL)
        with open(path, "wb") as handle:
            handle.write(data)
        return path, hashlib.sha256(data).hexdigest()

    def listing(self) -> dict[str, tuple[int, int]]:
        found = {}
        for folder, _dirs, names in os.walk(self.photo_root):
            for name in names:
                path = os.path.join(folder, name)
                st = os.stat(path)
                found[path] = (st.st_size, st.st_mtime_ns)
        return found

    # the seams
    def _inside(self, path) -> str:
        # by spelling and by where a link leads (a junction inside the tree to a folder outside it)
        for form in (os.path.abspath, os.path.realpath):
            if not backup_plan.is_inside(form(path), form(self.tmp)):
                raise Outside(f"outside the synthetic tree: {path}")
        return path

    def _dev(self, path: str) -> int:
        for dev, root in enumerate(self.archive_roots, start=TREE_DEV + 1):
            if backup_plan.is_inside(os.path.abspath(path), root):
                return dev
        return TREE_DEV

    def stat(self, path):
        real = os.stat(self._inside(path))
        facts = {name: getattr(real, name) for name in dir(real) if name.startswith("st_")}
        by_norm = {paths.norm(os.path.abspath(key)): pair for key, pair in self.ids.items()}
        dev, ino = by_norm.get(paths.norm(os.path.abspath(path)), (None, None))
        facts["st_dev"] = self._dev(path) if dev is None else dev
        facts["st_ino"] = real.st_ino if ino is None else ino
        return SimpleNamespace(**facts)

    def attrs_of(self, path, st=None) -> int:
        return self.attrs.get(paths.norm(path), 0)

    def is_link(self, path) -> bool:
        return paths.norm(path) in {paths.norm(link) for link in self.links}

    def opener(self, path):
        return hashing.open_binary(self._inside(path))

    def rename(self, src, dst) -> None:
        os.rename(self._inside(src), self._inside(dst))

    def remove(self, path) -> None:
        os.remove(self._inside(path))

    def rmdir(self, path) -> None:
        os.rmdir(self._inside(path))

    def makedirs(self, path, *args, **kwargs) -> None:
        os.makedirs(self._inside(path), *args, **kwargs)

    def listdir(self, path) -> list[str]:
        return os.listdir(self._inside(path))

    def append(self, path, data: bytes) -> None:
        with open(self._inside(path), "ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

    def write(self, path, data: bytes) -> None:
        temp = self._inside(self._inside(path) + ".tmp")  # the derived name too: the root's lies outside
        with open(temp, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)

    def scandir(self, path):
        return os.scandir(self._inside(path))

    def exists(self, path) -> bool:
        return os.path.exists(self._inside(path))

    def clock(self) -> float:
        self.ticks += 1
        return float(self.ticks)

    def abort(self, code) -> None:
        self.aborted.append(code)

    def now(self) -> str:
        return NOW

    def volume_of(self, path) -> str:
        return str(self._dev(self._inside(path)))

    def free_bytes(self, path) -> int:
        self._inside(path)
        return FREE

    def seams(self) -> Seams:
        return Seams(self.stat, self.attrs_of, self.is_link, self.opener, self.rename, self.remove,
                     self.rmdir, self.makedirs, self.listdir, self.append, self.write, self.clock,
                     self.abort, self.now)

    def ctx(self, batch: str = BATCH) -> Ctx:
        return new_ctx(self.seams(), Folders(self.base, self.photo_root), self.base, batch)

    # the command line
    def _checked_argv(self, argv) -> list[str]:
        """Every `--base` (also `--base=x` or an abbreviation argparse accepts: `--b`, `--ba`,
        `--bas`) must be an absolute path in the tree, and so must every other absolute path in
        argv, alone or after `=`. With no `--base`, the World's own is put first."""
        given = False
        for i, arg in enumerate(argv):
            name, equals, value = arg.partition("=")
            if len(name) >= 3 and "--base".startswith(name):  # cli.main has no other --b... option
                given = True
                for base in ([value] if equals else argv[i + 1:i + 2]):
                    if not _absolute(base):
                        raise Outside(f"a relative base counts as outside: {base!r}")
            for candidate in (arg, value):
                if _absolute(candidate):
                    self._inside(candidate)
        return list(argv) if given else ["--base", self.base, *argv]

    def main(self, *argv) -> tuple[int, str, str]:
        args = self._checked_argv(argv)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(args, **self.cli_keywords())
            except SystemExit as exc:  # argparse refused the command line
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def cli_keywords(self) -> dict:
        return {"processes": lambda: [], "now": self.now, "stat": self.stat, "attrs_of": self.attrs_of,
                "volume_of": self.volume_of, "free_bytes": self.free_bytes, "clock": self.clock,
                "opener": self.opener, "rename": self.rename, "abort": self.abort,
                "is_link": self.is_link, "remove": self.remove, "rmdir": self.rmdir,
                "makedirs": self.makedirs, "listdir": self.listdir, "append": self.append,
                "write": self.write, "scandir": self.scandir, "exists": self.exists}
