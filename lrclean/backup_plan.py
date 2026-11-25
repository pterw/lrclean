"""Backup manifest format and archive-root checks (phase B1).

The only write is `write_manifest`. Paths compare through `paths.norm`, not
the real path (known limit: a junction inside an archive root is not seen).
"""

from __future__ import annotations

import csv
import os
import re
import shutil
from collections import Counter
from collections.abc import Sequence
from types import SimpleNamespace
from typing import NamedTuple

from lrclean import classify, inventory, paths, store

BACKUP_EXTS = frozenset(
    paths.RAW_EXTS | paths.JPEG_EXTS
    | set("heic heif png tif tiff psd psb gif webp bmp".split())
    | set("mp4 mov m4v avi mts m2ts 3gp mkv".split()))
CATALOG_SUFFIXES = (".lrcat", ".lrcat-wal", ".lrcat-shm", ".lrcat.lock", ".lrcat-journal")
COLUMNS = ("source", "dest", "bytes", "mtime_ns", "action", "reason")
VERIFIED_COLUMNS = ("source", "dest", "bytes", "sha256", "state")
ACTIONS = ("copy", "skip_placeholder")
_DIGITS = re.compile(r"[0-9]+")
_MANIFEST_NAME = re.compile(r"backup-manifest-([0-9]+)\.csv")
_SEPARATORS =re.compile(r"[\\/]")


class Row(NamedTuple):
    source: str
    dest: str
    bytes: int
    mtime_ns: int
    action: str
    reason: str


class ManifestError(ValueError):
    pass


def write_manifest(path: str, rows: Sequence[Row]) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(paths.fs_path(folder), exist_ok=True)
    with open(paths.fs_path(path), "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)


def _parse_row(record: list[str], number: int) -> Row:
    if len(record) != len(COLUMNS):
        raise ManifestError(f"row {number}: expected {len(COLUMNS)} columns")
    source, dest, size, mtime_ns, action, reason = record
    # A file dated before 1970 has a negative time; bytes stays plain digits.
    if not (_DIGITS.fullmatch(size) and _DIGITS.fullmatch(mtime_ns.removeprefix("-"))):
        raise ManifestError(f"row {number}: bytes and mtime_ns must be ASCII digits")
    size_int, mtime_int = int(size), int(mtime_ns)
    if not source or not dest:
        raise ManifestError(f"row {number}: source and dest must not be empty")
    if action not in ACTIONS:
        raise ManifestError(f"row {number}: unknown action {action!r}")
    return Row(source, dest, size_int, mtime_int, action, reason)


def read_manifest(path: str) -> list[Row]:
    try:
        with open(paths.fs_path(path), encoding="utf-8-sig", newline="") as handle:
            records = list(csv.reader(handle))
    except (OSError, UnicodeError, csv.Error) as error:
        raise ManifestError(f"cannot read manifest {path}: {error}") from error
    if not records or tuple(records[0]) != COLUMNS:
        raise ManifestError(f"manifest {path}: header differs from {','.join(COLUMNS)}")
    return [_parse_row(record, number) for number, record in enumerate(records[1:], start=2)]


def _key(path: str) -> str:
    return paths.norm(os.path.normpath(path))


def is_inside(path: str, root: str, *, below_only: bool = False) -> bool:
    inner, outer = _key(path), _key(root)
    if inner == outer:
        return not below_only
    return inner.startswith(outer.rstrip("/") + "/")


def check_archive_roots(
    archive_roots: list[str],
    *,
    library_roots: list[str],
    own_dir: str,
    volume_of=paths.volume,
) -> None:
    if not archive_roots:
        raise ValueError("no archive root given")
    seen: dict[str, str] = {}
    for root in archive_roots:
        if not os.path.isabs(root):
            raise ValueError(f"archive root is not absolute: {root}")
        # Windows strips a trailing dot or space, so such a spelling can name another folder.
        if any(part.endswith((".", " ")) for part in _SEPARATORS.split(os.path.normpath(root))):
            raise ValueError(f"archive root has a segment ending in a dot or space: {root}")
        for protected in (*library_roots, own_dir):
            if is_inside(root, protected) or is_inside(protected, root):
                raise ValueError(f"archive root is, holds or is in a protected folder: {root}")
        volume = volume_of(os.path.abspath(root))
        if not volume or volume in seen:
            raise ValueError(f"archive root shares a volume or has none: {root}")
        seen[volume] = root


def archive_index(rows: Sequence[Row], archive_roots: list[str]) -> int:
    if not rows:
        raise ManifestError("manifest has no rows")
    found: set[int] = set()
    for row in rows:
        if not os.path.isabs(row.dest):
            raise ManifestError(f"dest is not absolute: {row.dest}")
        holders = [
            n for n, root in enumerate(archive_roots, start=1)
            if is_inside(row.dest, root, below_only=True)
        ]
        if not holders:
            raise ManifestError(f"dest is in no archive root: {row.dest}")
        found.update(holders)
    if len(found) != 1:
        raise ManifestError("dests lie in more than one archive root")
    return found.pop()


def free_space(path: str) -> int:
    probe = os.path.abspath(path)
    while not os.path.exists(paths.fs_path(probe)):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(paths.fs_path(probe)).free


class RootPlan(NamedTuple):
    archive_root: str
    rows: list[Row]
    folders: int
    files: int
    bytes: int
    placeholders: int
    left_out: int
    free: int
    catalog_files: int = 0


class BackupPlan(NamedTuple):
    roots: list[RootPlan]
    outside_raws: int
    unread_roots: list[str]
    unlisted: int
    unread_folders: tuple[str, ...] = ()
    links: int = 0
    not_selected: int = 0
    not_selected_exts: tuple[tuple[str, int], ...] = ()


class _Listing(NamedTuple):
    rows: list[Row]
    folders: int
    left_out: int
    catalog_files: int
    unread: list[str]
    unstatted: int


class _Walk(NamedTuple):
    selected: dict[str, str]
    unread: list[str]
    links: int
    not_selected: int
    not_selected_exts: dict[str, int]


def _is_cache(name: str, path: str, records) -> bool:
    """A preview cache as rule 11 types it; with no catalogs scan nothing is one."""
    if records is None or not name.casefold().endswith(inventory.LRDATA):
        return False
    ctx = SimpleNamespace(lrdata_records=records)
    return inventory._container_kind(ctx, name, paths.norm(path)) == "cache"


def _walk_photo_root(photo_root: str, pruned, records, scandir) -> _Walk:
    """Names only: select each folder that holds a BACKUP_EXTS file directly."""
    selected: dict[str, str] = {}
    unread: list[str] = []
    tally: Counter[str] = Counter()
    links = not_selected = 0
    stack = [photo_root]
    while stack:
        folder = stack.pop()
        try:
            with scandir(paths.fs_path(folder)) as it:
                entries = list(it)
        except OSError:
            unread.append(folder)
            continue
        exts: list[str] = []
        subfolders: list[str] = []
        for entry in entries:
            path = os.path.join(folder, entry.name)
            if entry.is_symlink() or entry.is_junction():
                links += 1
            elif entry.is_dir(follow_symlinks=False):
                if _key(path) not in pruned and not _is_cache(entry.name, path, records):
                    subfolders.append(path)
            elif entry.is_file(follow_symlinks=False):
                exts.append(os.path.splitext(entry.name)[1][1:].casefold())
        if any(ext in BACKUP_EXTS for ext in exts):
            selected[_key(folder)] = folder
        elif exts:
            not_selected += 1
            tally.update(exts)
        stack.extend(reversed(subfolders))
    return _Walk(selected, unread, links, not_selected, dict(tally))


def _source_row(path: str, stat, attrs_of) -> Row | None:
    """A row without destination; None when the file cannot be statted."""
    try:
        st = stat(paths.fs_path(path))
    except OSError:
        return None
    attrs = attrs_of(path, st) if attrs_of else getattr(st, "st_file_attributes", 0)
    skip = paths.is_placeholder(attrs)
    return Row(path, "", st.st_size, st.st_mtime_ns,
               "skip_placeholder" if skip else "copy", "placeholder" if skip else "")


def _list_folders(selected: dict[str, str], pruned, scandir, stat, attrs_of) -> _Listing:
    rows: list[Row] = []
    unread: list[str] = []
    listed = left_out = catalog_files = unstatted = 0
    for folder in selected.values():
        try:
            with scandir(paths.fs_path(folder)) as it:
                entries = list(it)
        except OSError:
            unread.append(folder)
            continue
        listed += 1
        for entry in entries:
            path = os.path.join(folder, entry.name)
            if entry.is_symlink() or entry.is_junction():
                continue
            if entry.is_dir(follow_symlinks=False):
                key = _key(path)
                left_out += key not in selected and key not in pruned
            elif entry.is_file(follow_symlinks=False):
                if entry.name.casefold().endswith(CATALOG_SUFFIXES):
                    catalog_files += 1
                    continue
                row = _source_row(path, stat, attrs_of)
                unstatted += row is None
                if row:
                    rows.append(row)
    rows.sort(key=lambda row: paths.norm(row.source))
    return _Listing(rows, listed, left_out, catalog_files, unread, unstatted)


def _root_plan(archive_root: str, listing: _Listing, photo_root: str, free_bytes) -> RootPlan:
    rows = [
        row._replace(dest=os.path.join(archive_root, os.path.relpath(row.source, photo_root)))
        for row in listing.rows
    ]
    return RootPlan(
        archive_root, rows, listing.folders, len(rows),
        sum(row.bytes for row in rows if row.action == "copy"),
        sum(row.action == "skip_placeholder" for row in rows),
        listing.left_out, free_bytes(archive_root), listing.catalog_files)


def plan_manifests(
    conn,
    *,
    library_roots: list[str],
    photo_root: str,
    archive_roots: list[str],
    own_dir: str,
    exclude: Sequence[str] = (),
    attrs_of=None,
    scandir=os.scandir,
    stat=os.stat,
    free_bytes=free_space,
) -> BackupPlan:
    states = classify.root_states(conn, library_roots)
    read = [s.root for s in states if s.status == "complete"]
    unread = [s.root for s in states if s.status != "complete"]
    if not read:
        raise ManifestError("no library root has a complete files scan")
    raws = [
        r for r in classify.inventoried_files(conn, read, photos_only=True)
        if r["ext"] in paths.RAW_EXTS
    ]
    outside = sum(not is_inside(r["path_orig"], photo_root) for r in raws)
    pruned = frozenset(_key(p) for p in (own_dir, *exclude, *archive_roots))
    cataloged = store.latest_scans(conn, "catalogs")
    records = inventory._lrdata_records(conn, [s["id"] for s in cataloged]) if cataloged else None
    walk = _walk_photo_root(photo_root, pruned, records, scandir)
    listing = _list_folders(walk.selected, pruned, scandir, stat, attrs_of)
    roots = [_root_plan(root, listing, photo_root, free_bytes) for root in archive_roots]
    unread_folders = (*walk.unread, *listing.unread)
    tally = tuple(sorted(walk.not_selected_exts.items(), key=lambda item: (-item[1], item[0])))
    return BackupPlan(
        roots, outside, unread, len(unread_folders) + listing.unstatted, unread_folders,
        walk.links, walk.not_selected, tally)


def _summary_text(plan: BackupPlan) -> str:
    lines = ["# Backup plan", ""]
    if plan.unread_folders:
        lines += [
            "## NOT READ: folders that could not be listed", "",
            *(f"- {folder}" for folder in plan.unread_folders), "",
            "This plan is not one to approve until these folders are listed"
            " or you accept their absence.", "",
        ]
    for n, root in enumerate(plan.roots, start=1):
        lines += [
            f"## Archive root {n}: {root.archive_root}", "",
            f"- folders: {root.folders}",
            f"- files to copy: {root.files - root.placeholders}"
            f"; placeholders skipped, apart from them: {root.placeholders}",
            f"- bytes: {root.bytes}",
            f"- subfolders left out: {root.left_out}",
            f"- catalog files left out: {root.catalog_files}",
            f"- free space: {root.free}",
        ]
        if root.bytes > root.free:
            lines.append(f"- NOT ENOUGH FREE SPACE: needs {root.bytes - root.free} more bytes")
        lines.append("")
    lines += [
        "## Selection", "",
        f"- extensions that select a folder: {', '.join(sorted(BACKUP_EXTS))}",
        f"- links and junctions not followed: {plan.links}",
        f"- folders not selected: {plan.not_selected}",
        *(f"  - {ext or '(none)'}: {count}" for ext, count in plan.not_selected_exts), "",
        "## Outside the photo folder", "",
        f"- raw files left to phase D: {plan.outside_raws}", "",
        "## Not read", "",
        f"- library roots without a complete files scan: {len(plan.unread_roots)}",
        *(f"  - {root}" for root in plan.unread_roots),
        f"- unlisted folders or files: {plan.unlisted}", "",
    ]
    return "\n".join(lines)


def write_plan(out_dir: str, plan: BackupPlan) -> list[str]:
    os.makedirs(paths.fs_path(out_dir), exist_ok=True)
    written = []
    for n, root in enumerate(plan.roots, start=1):
        path = os.path.join(out_dir, f"backup-manifest-{n}.csv")
        write_manifest(path, root.rows)
        written.append(path)
    # A manifest of an earlier plan with more roots must not stay beside the new ones.
    for name in os.listdir(paths.fs_path(out_dir)):
        match = _MANIFEST_NAME.fullmatch(name)
        if match and int(match[1]) > len(plan.roots):
            os.remove(paths.fs_path(os.path.join(out_dir, name)))
    summary = os.path.join(out_dir, "backup-summary.md")
    with open(paths.fs_path(summary), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_summary_text(plan))
    written.append(summary)
    return written
