"""backup-verify: read every source and destination of a manifest in full.

No journal is trusted. The only write is `write_verified`.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Sequence
from typing import NamedTuple

from lrclean import hashing, paths
from lrclean.backup_plan import VERIFIED_COLUMNS, ManifestError, Row

OK_STATES = ("identical", "skipped_placeholder")


class Verified(NamedTuple):
    source: str
    dest: str
    bytes: int
    sha256: str
    state: str


def _verify_row(row: Row, stat, attrs_of, opener) -> Verified:
    def result(state: str, size: int | None = None, sha256: str = "") -> Verified:
        return Verified(row.source, row.dest, row.bytes if size is None else size, sha256, state)

    def is_placeholder(path: str, st) -> bool:
        attrs = attrs_of(path, st) if attrs_of else getattr(st, "st_file_attributes", 0)
        return paths.is_placeholder(attrs)

    if row.action == "skip_placeholder":
        return result("skipped_placeholder")
    try:
        st = stat(paths.fs_path(row.source))
    except FileNotFoundError:
        return result("missing_source")
    except OSError:
        return result("unreadable")
    if is_placeholder(row.source, st):
        return result("source_placeholder")
    try:
        dest_st = stat(paths.fs_path(row.dest))
    except FileNotFoundError:
        return result("missing_dest")
    except OSError:
        return result("unreadable")
    if is_placeholder(row.dest, dest_st):
        return result("unreadable")

    def open_fs(path):
        return opener(paths.fs_path(path))

    try:
        source_hash = hashing.full_hash(row.source, open_fs)
        dest_hash = hashing.full_hash(row.dest, open_fs)
    except OSError:
        return result("unreadable")
    return result("identical" if source_hash == dest_hash else "different",
                  st.st_size, source_hash)


def verify_manifest(rows: Sequence[Row], *, stat=os.stat, attrs_of=None,
                    opener=hashing.open_binary) -> list[Verified]:
    return [_verify_row(row, stat, attrs_of, opener) for row in rows]


def write_verified(path: str, items: Sequence[Verified]) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(paths.fs_path(folder), exist_ok=True)
    with open(paths.fs_path(path), "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(VERIFIED_COLUMNS)
        writer.writerows(items)


def read_verified_list(path: str) -> list[Verified]:
    try:
        with open(paths.fs_path(path), encoding="utf-8-sig", newline="") as handle:
            records = list(csv.reader(handle))
    except (OSError, UnicodeError, csv.Error) as error:
        raise ManifestError(f"cannot read verified list {path}: {error}") from error
    if not records or tuple(records[0]) != VERIFIED_COLUMNS:
        raise ManifestError(f"verified list {path}: header differs from {','.join(VERIFIED_COLUMNS)}")
    items = []
    for number, record in enumerate(records[1:], start=2):
        if len(record) != len(VERIFIED_COLUMNS) or not record[2].isascii() or not record[2].isdigit():
            raise ManifestError(f"verified list {path}: row {number} is malformed")
        items.append(Verified(record[0], record[1], int(record[2]), record[3], record[4]))
    return items


def verify_exit_code(items: Sequence[Verified]) -> int:
    return 0 if all(item.state in OK_STATES for item in items) else 6
