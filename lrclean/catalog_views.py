"""Report sections 1, 2 and 8: catalog inventory, shoot catalogs, missing files.

Read-only. Every answer comes from stored rows; the only looks at the disk
are the injected `exists` and `drive_exists` functions.
"""

import os
from collections import defaultdict
from datetime import datetime, timezone

from lrclean import classify, paths, store

CHECKED_EXTS = paths.PHOTO_EXTS | paths.SIDECAR_EXTS

INVENTORY_HOW = "stored row of the latest catalogs scan; records counts every recorded file type"
SHOOT_HOW = (
    "photo and sidecar records only; other recorded types (video, TIFF, PNG and the like) "
    "were not checked, so none is reported missing; a photo is one file name with one "
    "capture time in one folder; records_present and records_not_read count records (one per via, "
    "so a file usually has two), the missing columns count photos; records_not_read counts "
    "every record of a photo that is not present and has a record under a root that is not "
    "complete or under no scanned root, which is neither present nor missing; missing_moved needs a "
    "present same-name, same-capture-time record in another readable, non-excluded catalog; "
    "the backup columns are a lower bound: complete backup roots only, matched by file name "
    "and capture time within whole hours"
)
MISSING_HOW = {
    "missing": "no record of this photo has an inventoried file, and every record lies under a complete scanned root",
    "drive_not_connected": "a record lies under a not-connected root or on a drive that does not exist",
    "not_read": "a record lies under a partial or running root or under no scanned root; not checked",
}


def _latest_catalogs(conn) -> list:
    scan_ids = [s["id"] for s in store.latest_scans(conn, "catalogs")]
    if not scan_ids:
        return []
    sql = f'SELECT * FROM "catalog" WHERE scan_id IN ({",".join("?" * len(scan_ids))}) ORDER BY id'
    return conn.execute(sql, scan_ids).fetchall()


def _text(value) -> str:
    return "" if value is None else str(value)


def catalog_inventory_rows(conn, *, excluded_catalogs: list[str]) -> list[dict]:
    counts = {r[0]: r[1] for r in conn.execute(
        'SELECT catalog_id, COUNT(*) FROM "record" GROUP BY catalog_id')}
    skip = {paths.norm(p) for p in excluded_catalogs}
    rows = [{
        "catalog": c["source_path"],
        "role": _text(c["role"]),
        "status": _text(c["status"]),
        "db_version": _text(c["db_version"]),
        "records": counts.get(c["id"], 0),
        "wal_bytes": c["wal_bytes"] or 0,
        "has_lock": "yes" if c["has_lock"] else "no",
        "excluded_by_owner": "yes" if paths.norm(c["source_path"]) in skip else "no",
        "how_decided": INVENTORY_HOW,
    } for c in _latest_catalogs(conn)]
    return sorted(rows, key=lambda r: (r["catalog"], r["role"], r["status"]))


def date_matches(mtime_ns: int, capture_time: str) -> bool:
    try:
        taken = datetime.fromisoformat(capture_time)
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        seen = datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc)
        diff = (seen - taken).total_seconds()
    except (ValueError, TypeError, OverflowError, OSError):
        return False
    hours = round(diff / 3600)
    return abs(hours) <= 14 and abs(diff - hours * 3600) <= 1


def _name(path_norm: str) -> str:
    return paths.split_path(path_norm)[1]


def _lookup(states: list[classify.RootState]) -> list[tuple[str, str]]:
    pairs = [(paths.norm(s.root), s.status) for s in states]
    return sorted(pairs, key=lambda p: -len(p[0]))


def _status_of(path_norm: str, lookup: list[tuple[str, str]]) -> str | None:
    for root, status in lookup:
        prefix = root if root.endswith("/") else root + "/"
        if path_norm == root or path_norm.startswith(prefix):
            return status
    return None


def _photo_records(conn, catalog_ids: list[int]) -> dict[int, dict[tuple, list]]:
    """catalog id -> (file name, capture_time, parent folder name) -> its photo and sidecar records."""
    out: dict[int, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
    if not catalog_ids:
        return out
    exts = sorted(CHECKED_EXTS)
    sql = ('SELECT * FROM "record" WHERE catalog_id IN ({}) AND ext IN ({}) ORDER BY rowid'
           .format(",".join("?" * len(catalog_ids)), ",".join("?" * len(exts))))
    for r in conn.execute(sql, [*catalog_ids, *exts]):
        folder, name = paths.split_path(r["path_norm"])
        out[r["catalog_id"]][(name, r["capture_time"], paths.norm(_name(folder)))].append(r)
    return out


def _backup_index(conn, backup_roots: list[str]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = defaultdict(list)
    for f in classify.inventoried_files(conn, backup_roots):
        index[_name(f["path_norm"])].append(f["mtime_ns"])
    return index


def _uncatalogued(conn, shoot_ids: list[int], library_roots: list[str],
                  excluded: list[str]) -> dict[int, int]:
    shoot_ids = set(shoot_ids)
    folders: dict[str, set[int]] = defaultdict(set)
    for r in conn.execute('SELECT catalog_id, path_norm FROM "catalog_root" WHERE via = ?',
                          ("absolute",)):
        if r["catalog_id"] in shoot_ids:
            folders[r["path_norm"]].add(r["catalog_id"])
    counts: dict[int, int] = defaultdict(int)
    if not folders:
        return counts
    recorded = classify.recorded_norms(conn, excluded_catalogs=excluded)
    for f in classify.inventoried_files(conn, library_roots, photos_only=True):
        if f["path_norm"] in recorded:
            continue
        found: set[int] = set()
        folder = f["path_norm"]
        while "/" in folder:
            folder = folder.rsplit("/", 1)[0]
            found |= folders.get(folder, set()) | folders.get(folder + "/", set())
        for cid in found:
            counts[cid] += 1
    return counts


def _root_folders(conn, catalog_id: int, exists) -> str:
    rows = conn.execute('SELECT path_orig FROM "catalog_root" WHERE catalog_id = ? AND via = ?',
                        (catalog_id, "absolute")).fetchall()
    return ";".join(f'{p}={"exists" if exists(paths.fs_path(p)) else "missing"}'
                    for p in sorted(r["path_orig"] for r in rows))


def shoot_catalog_rows(conn, *, library_roots: list[str], backup_roots: list[str],
                       excluded_catalogs: list[str], exists=os.path.exists) -> list[dict]:
    catalogs = _latest_catalogs(conn)
    shoots = [c for c in catalogs if c["role"] == "shoot"]
    if not shoots:
        return []
    skip = {paths.norm(p) for p in excluded_catalogs}
    readable = {c["id"] for c in catalogs
                if c["status"] in ("ok", "partial") and paths.norm(c["source_path"]) not in skip}
    records = _photo_records(conn, [c["id"] for c in catalogs])
    names = {_name(f["path_norm"]) for f in classify.inventoried_files(conn, library_roots)}
    present = {f["path_norm"]
               for f in classify.inventoried_files(conn, [*library_roots, *backup_roots])}
    moved_to: dict[tuple, set[int]] = defaultdict(set)
    for cid in readable:
        for key, recs in records[cid].items():
            if key[1] and any(r["path_norm"] in present for r in recs):
                moved_to[key[:2]].add(cid)
    backup = _backup_index(conn, backup_roots)
    lookup = _lookup(classify.root_states(conn, [*library_roots, *backup_roots]))
    uncat = _uncatalogued(conn, [c["id"] for c in shoots], library_roots, excluded_catalogs)
    out = []
    for cat in shoots:
        cid = cat["id"]
        row = dict.fromkeys(("records_present", "records_not_read", "missing_moved",
                             "missing_same_name_only", "missing_nothing",
                             "backup_same_name_only_lower_bound",
                             "backup_nothing_lower_bound"), 0)
        for key, recs in records[cid].items():
            if any(r["path_norm"] in present for r in recs):
                row["records_present"] += len(recs)
            elif any(_status_of(r["path_norm"], lookup) != "complete" for r in recs):
                row["records_not_read"] += len(recs)
            elif key[1] and moved_to[key[:2]] - {cid}:
                row["missing_moved"] += 1
            else:
                _count_leftover(row, key, names, backup)
        out.append({"catalog": cat["source_path"], **row,
                    "root_folders": _root_folders(conn, cid, exists),
                    "uncatalogued_in_tree": uncat.get(cid, 0), "how_decided": SHOOT_HOW})
    return sorted(out, key=lambda r: r["catalog"])


def _count_leftover(row: dict, key: tuple, names: set[str], backup: dict) -> None:
    name, taken, _ = key
    same_name = name in names
    row["missing_same_name_only" if same_name else "missing_nothing"] += 1
    if any(date_matches(m, taken) for m in backup.get(name, ())):
        row["backup_same_name_only_lower_bound" if same_name
            else "backup_nothing_lower_bound"] += 1


def _record_state(rec, lookup, drive_exists, drives: dict) -> str:
    status = _status_of(rec["path_norm"], lookup)
    if status == "complete":
        return "missing"
    if status == "not_connected":
        return "drive_not_connected"
    if status not in (None, "not_scanned"):
        return "not_read"
    vol = paths.volume(rec["path_orig"])
    if vol and vol not in drives:
        drives[vol] = bool(drive_exists(vol + "\\"))
    return "not_read" if not vol or drives[vol] else "drive_not_connected"


def missing_rows(conn, *, live_catalog: str | None, library_roots: list[str],
                 backup_roots: list[str], drive_exists=os.path.exists) -> list[dict]:
    if live_catalog is None:
        return []
    live = paths.norm(live_catalog)
    ids = [c["id"] for c in _latest_catalogs(conn) if paths.norm(c["source_path"]) == live]
    roots = [*library_roots, *backup_roots]
    present = {f["path_norm"] for f in classify.inventoried_files(conn, roots)}
    lookup = _lookup(classify.root_states(conn, roots))
    drives: dict[str, bool] = {}
    out = []
    for photos in _photo_records(conn, ids).values():
        for (_, taken, _), recs in photos.items():
            if any(r["path_norm"] in present for r in recs):
                continue
            states = {_record_state(r, lookup, drive_exists, drives) for r in recs}
            state = next(s for s in ("drive_not_connected", "not_read", "missing") if s in states)
            first = min(recs, key=lambda r: (r["via"], r["path_orig"]))
            out.append({"path": first["path_orig"],
                        "kind": "sidecar" if first["ext"] in paths.SIDECAR_EXTS else "photo",
                        "capture_time": _text(taken), "state": state,
                        "how_decided": MISSING_HOW[state]})
    return sorted(out, key=lambda r: (r["path"], r["kind"], r["capture_time"], r["state"]))
