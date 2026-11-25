"""The files scan: walk each root and store one row per photo or sidecar file."""

import bisect
import os
import sys
from dataclasses import dataclass

from lrclean import paths, store

KEPT_EXTS = paths.PHOTO_EXTS | paths.SIDECAR_EXTS
UNREADABLE = "<unreadable>"
LRDATA = ".lrdata"
STORAGE_NAME = "mobile downloads.lrdata"
CACHE_SUFFIX = "previews.lrdata"
RECURSION_FLOOR = 20_000


class NoCatalogScan(RuntimeError):
    """The files scan needs a catalogs scan of every catalog root first."""


@dataclass
class _Ctx:
    conn: object
    scan_id: int
    pruned: frozenset
    lrdata_records: list
    now: object
    attrs_of: object
    scandir: object
    complete: bool = True


def _inside(path_norm: str, base_norm: str) -> bool:
    base = base_norm.rstrip("/")
    return path_norm == base_norm or path_norm.startswith(base + "/")


def _check_inputs(conn, roots, catalog_roots):
    scanned = {paths.norm(row["root"]) for row in store.latest_scans(conn, "catalogs")}
    for root in catalog_roots:
        if paths.norm(root) not in scanned:
            raise NoCatalogScan(f"no catalogs scan for root: {root}")
    seen: list[str] = []
    for root in roots:
        norm = paths.norm(root)
        if any(_inside(norm, earlier) for earlier in seen):
            raise ValueError(f"root equal to or inside an earlier root: {root}")
        seen.append(norm)


def _row(ctx, path, ext, size, mtime_ns, attrs, container, file_index, nlink):
    store.insert_row(
        ctx.conn, "file", scan_id=ctx.scan_id, volume=paths.volume(path),
        path_norm=paths.norm(path), path_orig=path, ext=ext, size=size,
        mtime_ns=mtime_ns, attrs=attrs, placeholder=int(paths.is_placeholder(attrs)),
        container=container, file_index=file_index, nlink=nlink, checked_at=ctx.now())


def _marker(ctx, path, container):
    ctx.complete = False
    _row(ctx, path, UNREADABLE, 0, 0, 0, container, 0, 0)


def _lrdata_records(conn, scan_ids):
    if not scan_ids:
        return []
    marks = ", ".join("?" * len(scan_ids))
    sql = ("SELECT r.path_norm FROM record r JOIN catalog c ON c.id = r.catalog_id "
           f"WHERE c.scan_id IN ({marks}) AND c.status IN ('ok', 'partial') "
           "AND instr(r.path_norm, '.lrdata/') > 0")
    return sorted(row[0] for row in conn.execute(sql, scan_ids))


def _has_record_under(ctx, folder_norm: str) -> bool:
    at = bisect.bisect_left(ctx.lrdata_records, folder_norm + "/")
    return at < len(ctx.lrdata_records) and ctx.lrdata_records[at] < folder_norm + "0"


def _container_kind(ctx, name: str, folder_norm: str) -> str:
    low = name.casefold()
    if low == STORAGE_NAME or _has_record_under(ctx, folder_norm):
        return "storage"
    return "cache" if low.endswith(CACHE_SUFFIX) else "unknown"


def _file(ctx, path, entry, container, inventory):
    ext = os.path.splitext(entry.name)[1][1:].casefold()
    if not (inventory and ext in KEPT_EXTS):
        sizing = bool(container) or not inventory
        if not sizing:
            return 0
        try:
            return entry.stat(follow_symlinks=False).st_size
        except OSError:
            _marker(ctx, path, container)
            return 0
    try:
        st = os.stat(paths.fs_path(path))
    except OSError:
        _marker(ctx, path, container)
        return 0
    attrs = ctx.attrs_of(path, st) if ctx.attrs_of else getattr(st, "st_file_attributes", 0)
    _row(ctx, path, ext, st.st_size, st.st_mtime_ns, attrs, container, paths.signed64(st.st_ino), st.st_nlink)
    return st.st_size


def _enter(ctx, path, name, container, inventory):
    if not (inventory and name.casefold().endswith(LRDATA)):
        return _walk(ctx, path, container, inventory)
    folder_norm = paths.norm(path)
    kind = _container_kind(ctx, name, folder_norm)
    inner = paths.make_container(kind, folder_norm)
    size = _walk(ctx, path, inner, kind != "cache")
    _row(ctx, path, "lrdata", size, 0, 0, inner, 0, 0)
    return size


def _walk(ctx, folder, container, inventory):
    try:
        with ctx.scandir(paths.fs_path(folder)) as it:
            entries = list(it)
    except OSError:
        _marker(ctx, folder, container)
        return 0
    total = 0
    for entry in entries:
        path = os.path.join(folder, entry.name)
        if entry.is_symlink() or entry.is_junction():
            continue
        if entry.is_dir(follow_symlinks=False):
            if paths.norm(path) not in ctx.pruned:
                total += _enter(ctx, path, entry.name, container, inventory)
        elif entry.is_file(follow_symlinks=False):
            total += _file(ctx, path, entry, container, inventory)
    return total


def scan_files(conn, roots, *, catalog_roots, exclude, own_dir, now=store.utc_now,
               attrs_of=None, scandir=os.scandir):
    """Scan each root; return the scan ids."""
    _check_inputs(conn, roots, catalog_roots)
    pruned = frozenset(paths.norm(p) for p in [own_dir, *exclude])
    catalog_ids = [row["id"] for row in store.latest_scans(conn, "catalogs")]
    records = _lrdata_records(conn, catalog_ids)
    ids = []
    for root in roots:
        scan_id = store.begin_scan(conn, root, "files", now)
        ids.append(scan_id)
        if not os.path.exists(paths.fs_path(root)):
            store.finish_scan(conn, scan_id, "not_connected", now)
            continue
        ctx = _Ctx(conn, scan_id, pruned, records, now, attrs_of, scandir)
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, RECURSION_FLOOR))
        try:
            _walk(ctx, root, "", True)
        except BaseException:
            store.finish_scan(conn, scan_id, "partial", now)
            raise
        finally:
            sys.setrecursionlimit(old_limit)
        store.finish_scan(conn, scan_id, "complete" if ctx.complete else "partial", now)
    return ids
