"""The archive evidence of the reject check (rule 27).

Reads manifests and verified lists, never a file's content. No write.
"""

from __future__ import annotations

import os
from typing import NamedTuple

from lrclean import paths
from lrclean.backup_plan import ManifestError, Row, is_inside, read_manifest
from lrclean.backup_verify import read_verified_list

OK = "ok"
OUTSIDE_BACKUP = "outside_backup"
CHANGED_SINCE_BACKUP = "changed_since_backup"
NO_ARCHIVE_COPY = "no_archive_copy"


class RootEvidence(NamedTuple):
    root: str
    rows: dict[str, list[Row]]
    verified: frozenset[tuple[str, str, int]]


def _verified_keys(manifest: str, verified: str, stat) -> frozenset[tuple[str, str, int]]:
    items = read_verified_list(verified)
    try:
        age_ok = stat(paths.fs_path(verified)).st_mtime_ns >= stat(paths.fs_path(manifest)).st_mtime_ns
    except OSError:
        return frozenset()
    if not age_ok:
        return frozenset()
    return frozenset(
        (paths.norm(item.source), paths.norm(item.dest), item.bytes)
        for item in items
        if item.state == "identical"
    )


def _root_evidence(out_dir: str, number: int, root: str, exists, stat) -> RootEvidence:
    manifest = os.path.join(out_dir, f"backup-manifest-{number}.csv")
    verified = os.path.join(out_dir, f"backup-verified-{number}.csv")
    rows: dict[str, list[Row]] = {}
    if exists(paths.fs_path(manifest)):
        try:
            records = read_manifest(manifest)
        except ManifestError as error:
            raise ManifestError(f"{manifest}: {error}") from error
        for row in records:
            rows.setdefault(paths.norm(row.source), []).append(row)
    keys = _verified_keys(manifest, verified, stat) if exists(paths.fs_path(verified)) else frozenset()
    return RootEvidence(root, rows, keys)


def load_evidence(out_dir: str, archive_roots: list[str], *, exists=os.path.exists,
                  stat=os.stat) -> list[RootEvidence]:
    return [_root_evidence(out_dir, number, root, exists, stat)
            for number, root in enumerate(archive_roots, start=1)]


def _dest_state(row: Row, key: tuple[str, str, int], evidence: RootEvidence, stat, attrs_of) -> str:
    if key not in evidence.verified:
        return NO_ARCHIVE_COPY
    try:
        dest_st = stat(paths.fs_path(row.dest))
    except OSError:
        return NO_ARCHIVE_COPY
    attrs = attrs_of(row.dest, dest_st) if attrs_of else getattr(dest_st, "st_file_attributes", 0)
    if dest_st.st_size != row.bytes:
        return NO_ARCHIVE_COPY
    if paths.is_placeholder(attrs):
        return NO_ARCHIVE_COPY
    return OK


def archive_state(evidence: RootEvidence, path: str, st, *, stat=os.stat,
                  attrs_of=None) -> tuple[str, str]:
    key = paths.norm(path)
    copies = [row for row in evidence.rows.get(key, []) if row.action == "copy"]
    if len(copies) != 1:
        return OUTSIDE_BACKUP, ""
    row = copies[0]
    if not is_inside(row.dest, evidence.root, below_only=True):
        return OUTSIDE_BACKUP, ""
    if st is None:
        return CHANGED_SINCE_BACKUP, row.dest
    if st.st_size != row.bytes:
        return CHANGED_SINCE_BACKUP, row.dest
    if st.st_mtime_ns != row.mtime_ns:
        return CHANGED_SINCE_BACKUP, row.dest
    return _dest_state(row, (key, paths.norm(row.dest), row.bytes), evidence, stat, attrs_of), row.dest

