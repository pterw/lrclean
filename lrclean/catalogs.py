"""Find Lightroom catalogs, copy them, read the copy (rules 2, 5, 6, 14)."""

import csv
import hashlib
import itertools
import os
import shutil
import sqlite3
import subprocess
from typing import NamedTuple

from lrclean import paths, store

_REQUIRED = ("AgLibraryRootFolder", "AgLibraryFolder", "AgLibraryFile",
             "Adobe_images", "Adobe_variablesTable")
_RECORD_SQL = """
SELECT r.absolutePath, r.relativePathFromCatalog, fo.pathFromRoot, f.baseName,
       f.extension, f.sidecarExtensions, {capture}
FROM AgLibraryFile f
JOIN AgLibraryFolder fo ON fo.id_local = f.folder
JOIN AgLibraryRootFolder r ON r.id_local = fo.rootFolder
{images}
ORDER BY f.id_local"""
_WITH_IMAGES = ("LEFT JOIN Adobe_images i ON i.rootFile = f.id_local "
                "LEFT JOIN Adobe_images m ON m.id_local = i.masterImage")
_SUFFIXES = ("", "-wal", "-shm")


class LightroomRunning(RuntimeError):
    pass


class CatalogRead(NamedTuple):
    status: str
    db_version: str
    records: list[dict]
    roots: list[dict]
    images: list[dict]


def running_processes() -> list[str]:
    """Lower-case image names of running processes; raises when they cannot be listed."""
    try:
        done = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                              text=True, timeout=30, check=True)
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot list processes: {exc}") from exc
    names = [row[0].lower() for row in csv.reader(done.stdout.splitlines()) if row]
    if not names:
        raise RuntimeError("cannot list processes: tasklist returned nothing")
    return names


def find_catalogs(root: str, own_dir: str, exclude: list[str],
                  scandir=os.scandir) -> tuple[list[str], list[str]]:
    skipped = [paths.norm(p) for p in (own_dir, *exclude) if p]
    found: list[str] = []
    unlisted: list[str] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        here = paths.norm(folder)
        if any(here == s or here.startswith(s + "/") for s in skipped):
            continue
        try:
            with scandir(paths.fs_path(folder)) as listing:
                entries = list(listing)
        except OSError:
            unlisted.append(folder)
            continue
        for entry in entries:
            full = os.path.join(folder, entry.name)
            if entry.is_symlink() or entry.is_junction():
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(full)
            elif entry.name.lower().endswith(".lrcat") and entry.is_file(follow_symlinks=False):
                found.append(full)
    return sorted(found), sorted(unlisted)


def snapshot_catalog(source: str, work_dir: str, processes=running_processes, stat=os.stat) -> str:
    if any(name.startswith("lightroom") for name in processes()):
        raise LightroomRunning("Lightroom is running: refusing to copy a catalog")
    work = paths.norm(os.path.abspath(work_dir))
    home = paths.norm(os.path.abspath(os.path.dirname(source)))
    if work == home or work.startswith(home.rstrip("/") + "/"):
        raise ValueError("work_dir is inside the catalog's folder")
    for suffix in _SUFFIXES:
        try:
            attrs = getattr(stat(paths.fs_path(source + suffix)), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if paths.is_placeholder(attrs):
            raise ValueError(f"{source + suffix} is an online-only placeholder: refusing to copy it")
    name = os.path.basename(source)
    digest = hashlib.sha1(paths.norm(source).encode("utf-8")).hexdigest()[:12]
    folder = os.path.join(work_dir, "catalogs", f"{os.path.splitext(name)[0]}-{digest}")
    real_folder, real_home = (paths.norm(os.path.realpath(p)) for p in (folder, os.path.dirname(source)))
    if real_folder == real_home or real_folder.startswith(real_home.rstrip("/") + "/"):
        raise ValueError("the snapshot folder, with links resolved, is inside the catalog's folder")
    os.makedirs(paths.fs_path(folder), exist_ok=True)
    for suffix in _SUFFIXES:
        old = paths.fs_path(os.path.join(folder, name + suffix))
        if os.path.exists(old):
            os.remove(old)
    for suffix in _SUFFIXES:
        if os.path.exists(paths.fs_path(source + suffix)):
            shutil.copyfile(paths.fs_path(source + suffix),
                            paths.fs_path(os.path.join(folder, name + suffix)))
    return os.path.join(folder, name)


def _exists(path: str, stat) -> bool:
    """Whether `path` is there; any error but not-found propagates."""
    try:
        stat(path)
    except FileNotFoundError:
        return False
    return True


def is_stale(source_path: str, size: int, mtime_ns: int, wal_bytes: int, has_lock: int,
             stat=os.stat) -> bool:
    """True when the .lrcat, its -wal file or a new .lock differs from the snapshot (rule 6)."""
    try:
        now = stat(paths.fs_path(source_path))
        try:
            wal_now = stat(paths.fs_path(source_path + "-wal")).st_size
        except FileNotFoundError:
            wal_now = 0
        lock_new = not has_lock and _exists(paths.fs_path(source_path + ".lock"), stat)
    except OSError:
        return True
    return (now.st_size, now.st_mtime_ns, wal_now) != (size, mtime_ns, wal_bytes) or lock_new


def _trim(path: str) -> str:
    return path if len(path) <= 3 else path.rstrip("/\\")


def _record(path: str, via: str, ext: str, capture: str, is_sidecar: int) -> dict:
    return {"path_norm": paths.norm(path), "path_orig": path, "via": via,
            "ext": ext, "capture_time": capture, "is_sidecar": is_sidecar}


def _entry_records(row, source_dir: str) -> list[dict]:
    absolute, relative, folder, base, ext, sidecars, capture = row
    exts = [((ext or "").lower(), 0)]
    exts += [(s.strip().lower(), 1) for s in (sidecars or "").split(",") if s.strip()]
    out = []
    for extension, is_sidecar in exts:
        name = f"{folder or ''}{base}.{extension}"
        if absolute:
            out.append(_record(_join(absolute, name), "absolute", extension, capture or "", is_sidecar))
        if relative is not None:
            full = os.path.normpath(os.path.join(source_dir, relative, name))
            out.append(_record(full, "relative", extension, capture or "", is_sidecar))
    return out


def _join(root: str, name: str) -> str:
    return root + name if root.endswith(("/", "\\")) else root + "/" + name


def _root_rows(con, source_dir: str) -> list[dict]:
    out = []
    for absolute, relative in con.execute(
            "SELECT absolutePath, relativePathFromCatalog FROM AgLibraryRootFolder ORDER BY id_local"):
        if absolute:
            path = _trim(absolute)
            out.append({"path_norm": paths.norm(path), "path_orig": path, "via": "absolute"})
        if relative is not None:
            path = _trim(os.path.normpath(os.path.join(source_dir, relative)))
            out.append({"path_norm": paths.norm(path), "path_orig": path, "via": "relative"})
    return out


def _read_records(con, tables: set, source_dir: str) -> tuple[list[dict], bool]:
    if not {"AgLibraryRootFolder", "AgLibraryFolder", "AgLibraryFile"} <= tables:
        return [], False
    if "Adobe_images" in tables:
        sql = _RECORD_SQL.format(capture="COALESCE(m.captureTime, i.captureTime)", images=_WITH_IMAGES)
    else:
        sql = _RECORD_SQL.format(capture="''", images="")
    records: list[dict] = []
    seen: set = set()
    try:
        for row in con.execute(sql):
            for rec in _entry_records(row, source_dir):
                key = (rec["path_norm"], rec["via"])
                if key not in seen:
                    seen.add(key)
                    records.append(rec)
    except sqlite3.Error:
        if not records:
            raise
        return records, True
    return records, False


_IMAGE_SQL = """
SELECT i.id_local, r.absolutePath, r.relativePathFromCatalog, fo.pathFromRoot, f.baseName,
       f.extension, {pick}, {develop}
FROM Adobe_images i
JOIN AgLibraryFile f ON f.id_local = i.rootFile
JOIN AgLibraryFolder fo ON fo.id_local = f.folder
JOIN AgLibraryRootFolder r ON r.id_local = fo.rootFolder
{join}
ORDER BY i.id_local"""
_DEVELOP_COLUMNS = ("hasDevelopAdjustments", "hasDevelopAdjustmentsEx")


def _columns(con, table: str) -> set:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _usable(value) -> bool:
    return value is None or (isinstance(value, (int, float)) and value in (-1, 0, 1))


def _pick_of(value) -> int | None:
    return int(value) if value is not None and _usable(value) else None


def _developed_of(values: list) -> int | None:
    """RC-18: 1 when a column equals 1; unknown (None) when it cannot be read; else 0."""
    if any(isinstance(v, (int, float)) and v == 1 for v in values):
        return 1
    if not all(_usable(v) for v in values) or all(v is None for v in values):
        return None
    return 0


def _digest(text) -> str | None:
    """The SHA-256 hex of a non-empty settings text, else None."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if isinstance(text, str) and text else None


def _settings_of(rows: list) -> str | None:
    """The one digest every settings row of an image shares; None when a row has none or they differ."""
    digests = {_digest(r[8]) for r in rows}
    return digests.pop() if len(digests) == 1 else None


def _read_images(con, tables: set, source_dir: str) -> tuple[list[dict], bool]:
    if not {"Adobe_images", "AgLibraryRootFolder", "AgLibraryFolder", "AgLibraryFile"} <= tables:
        return [], False
    images: list[dict] = []
    try:
        pick = "i.pick" if "pick" in _columns(con, "Adobe_images") else "NULL"
        have = _columns(con, "Adobe_imageDevelopSettings") if "Adobe_imageDevelopSettings" in tables else set()
        columns = [c for c in _DEVELOP_COLUMNS if c in have]
        text = "d.text" if "text" in have else "NULL"
        join = "LEFT JOIN Adobe_imageDevelopSettings d ON d.image = i.id_local" if columns or text != "NULL" else ""
        develop = ", ".join(["d.image", text] + [f"d.{c}" for c in columns]) if join else "NULL, NULL"
        for _, group in itertools.groupby(con.execute(_IMAGE_SQL.format(pick=pick, develop=develop, join=join)),
                                          key=lambda r: r[0]):
            rows = list(group)
            states = [_developed_of(list(r[9:])) if r[7] is not None else None for r in rows]
            developed = 1 if 1 in states else (None if None in states else 0)
            settings = _settings_of(rows)
            for rec in _entry_records((*rows[0][1:6], "", None), source_dir):
                images.append({"path_norm": rec["path_norm"], "via": rec["via"],
                               "pick": _pick_of(rows[0][6]), "developed": developed, "settings": settings})
    except sqlite3.Error:
        return images, True
    return images, False


def _read_open(con, source_dir: str) -> CatalogRead:
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables:
        return CatalogRead("empty", "", [], [], [])
    version = ""
    if "Adobe_variablesTable" in tables:
        row = con.execute("SELECT value FROM Adobe_variablesTable WHERE name = 'Adobe_DBVersion'").fetchone()
        version = str(row[0]) if row and row[0] is not None else ""
    roots = _root_rows(con, source_dir) if "AgLibraryRootFolder" in tables else []
    records, broken = _read_records(con, tables, source_dir)
    images, images_broken = _read_images(con, tables, source_dir)
    partial = broken or images_broken or not set(_REQUIRED) <= tables
    return CatalogRead("partial" if partial else "ok", version, records, roots, images)


def read_snapshot(snapshot_path: str, source_path: str) -> CatalogRead:
    source_dir = os.path.dirname(source_path)
    try:
        con = sqlite3.connect(paths.fs_path(snapshot_path))
        try:
            return _read_open(con, source_dir)
        finally:
            con.close()
    except sqlite3.Error:
        return CatalogRead("unreadable", "", [], [], [])


def role_of(source: str, live_catalog: str | None, old_states: list[str]) -> str:
    here = paths.norm(source)
    if live_catalog and here == paths.norm(live_catalog):
        return "live"
    return "old_state" if here in {paths.norm(p) for p in old_states} else "shoot"


def _store_catalog(conn, scan_id, source, role, processes, work_dir, stat=os.stat) -> int:
    size = mtime_ns = 0
    snapshot = ""
    result = CatalogRead("unreadable", "", [], [], [])
    wal = paths.fs_path(source + "-wal")
    wal_bytes = os.path.getsize(wal) if os.path.exists(wal) else 0
    has_lock = int(os.path.exists(paths.fs_path(source + ".lock")))
    try:
        found = stat(paths.fs_path(source))
        size, mtime_ns = found.st_size, found.st_mtime_ns
        snapshot = snapshot_catalog(source, work_dir, processes, stat)
        result = read_snapshot(snapshot, source)
    except (OSError, ValueError):
        pass
    catalog_id = store.insert_row(
        conn, "catalog", scan_id=scan_id, source_path=source, snapshot_path=snapshot,
        role=role, status=result.status, size=size, mtime_ns=mtime_ns,
        db_version=result.db_version,
        wal_bytes=wal_bytes, has_lock=has_lock)
    for rec in result.records:
        store.insert_row(conn, "record", catalog_id=catalog_id, **rec)
    for root in result.roots:
        store.insert_row(conn, "catalog_root", catalog_id=catalog_id, **root)
    for image in result.images:
        store.insert_row(conn, "image", catalog_id=catalog_id, **image)
    conn.commit()
    return catalog_id


def scan_catalogs(conn, *, catalog_roots: list[str], own_dir: str, exclude: list[str],
                  work_dir: str, live_catalog: str | None, old_states: list[str],
                  processes=running_processes, now=store.utc_now,
                  scandir=os.scandir, stat=os.stat) -> list[int]:
    ids: list[int] = []
    for root in catalog_roots:
        scan_id = store.begin_scan(conn, root, "catalogs", now)
        if not os.path.isdir(paths.fs_path(root)):
            store.finish_scan(conn, scan_id, "not_connected", now)
            continue
        try:
            sources, unlisted = find_catalogs(root, own_dir, exclude, scandir)
            for source in sources:
                role = role_of(source, live_catalog, old_states)
                ids.append(_store_catalog(conn, scan_id, source, role, processes, work_dir, stat))
        except BaseException:
            store.finish_scan(conn, scan_id, "partial", now)
            raise
        store.finish_scan(conn, scan_id, "partial" if unlisted else "complete", now)
    return ids
