"""Counted full hashes and twin candidates for rule 26a of the reject check.

Reads `scan`, `file` and `hash` rows and the `stat` of a few files, never a
file's content. No write.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from lrclean import paths, store

_COUNTING_ROWS = (
    'SELECT p.path_norm, p.path_orig, p.size, p.mtime_ns, p.file_index, '
    'f.volume AS hash_volume, p.volume, h.size AS h_size, h.mtime_ns AS h_mtime_ns, '
    'h.file_index AS h_file_index, h.full '
    'FROM "file" p JOIN "file" f ON f.path_norm = p.path_norm '
    'JOIN "hash" h ON h.file_id = f.id WHERE p.scan_id = ?'
)


class Hashed(NamedTuple):
    path_norm: str
    path_orig: str
    size: int
    mtime_ns: int
    file_index: int
    full: str


class HashIndex(NamedTuple):
    by_path: Mapping[str, Hashed]
    by_hash: Mapping[tuple[str, int], tuple[Hashed, ...]]


EMPTY = HashIndex({}, {})


def _counted(conn, scan_id: int):
    for row in conn.execute(_COUNTING_ROWS, (scan_id,)):
        mine = (row["size"], row["mtime_ns"], row["file_index"])
        valid = (row["h_size"], row["h_mtime_ns"], row["h_file_index"]) == mine
        if row["full"] and valid and row["hash_volume"] == row["volume"]:
            yield Hashed(row["path_norm"], row["path_orig"], *mine, row["full"])


def load_hashes(conn, library_roots: Sequence[str]) -> HashIndex:
    wanted = {paths.norm(root) for root in library_roots}
    rows: dict[str, set[Hashed]] = {}
    for scan in store.latest_scans(conn, "files"):
        if paths.norm(scan["root"]) in wanted and scan["status"] == "complete":
            for hashed in _counted(conn, scan["id"]):
                rows.setdefault(hashed.path_norm, set()).add(hashed)
    by_path = {pn: next(iter(found)) for pn, found in rows.items() if len(found) == 1}
    groups: dict[tuple[str, int], list[Hashed]] = {}
    for hashed in by_path.values():
        groups.setdefault((hashed.full, hashed.size), []).append(hashed)
    by_hash = {key: tuple(sorted(group, key=lambda h: h.path_norm)) for key, group in groups.items()}
    return HashIndex(by_path, by_hash)


def _stat_now(hashed: Hashed, stat):
    """The `stat` now of a path when it still gives the row's size, time and index, else None."""
    try:
        st = stat(paths.fs_path(hashed.path_orig))
    except OSError:
        return None
    return st if (st.st_size, st.st_mtime_ns, paths.signed64(st.st_ino)) == (hashed.size, hashed.mtime_ns, hashed.file_index) else None


def _is_placeholder(hashed: Hashed, st, attrs_of) -> bool:
    attrs = attrs_of(paths.fs_path(hashed.path_orig), st) if attrs_of else getattr(st, "st_file_attributes", 0)
    return paths.is_placeholder(attrs)


def twin_candidates(index: HashIndex, path_norm: str, *, stat=os.stat, attrs_of=None) -> list[Hashed]:
    mine = index.by_path.get(path_norm)
    mine_st = _stat_now(mine, stat) if mine else None
    if mine_st is None:
        return []
    found = []
    for other in index.by_hash[(mine.full, mine.size)]:
        st = _stat_now(other, stat) if other.path_norm != path_norm else None
        if st is not None and not _is_placeholder(other, st, attrs_of) and (st.st_dev, st.st_ino) != (mine_st.st_dev, mine_st.st_ino):
            found.append(other)
    return found
