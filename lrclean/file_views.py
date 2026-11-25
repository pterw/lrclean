"""Sections 6, 9, 10 and 11 of the report: pairs, containers, loose photos, not read.

Read-only, from rows the scans stored. Nothing here opens a file; every
lookup is a dictionary or set built once.
"""

import re
from collections import defaultdict
from collections.abc import Collection

from lrclean import classify, paths, store

APP_FOLDER_NAMES = frozenset({"program files", "program files (x86)", "programdata",
                              "appdata", "windows", "node_modules", ".git", "site-packages"})
SAME_TIME_NS = 2_000_000_000

PAIR_HOW = ("a JPEG written together with its raw (mtimes within two seconds) is a sign of a "
            "camera JPEG; a pair with other times may be an export; one pair per JPEG, whatever the "
            "number of raws of that base name in the folder, and a pair counts as same-time if "
            "any of those raws is within two seconds")
_SEP = re.compile(r"([\\/])")


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def _complete_scan_ids(conn, roots: list[str]) -> list[int]:
    return sorted(s.scan_id for s in classify.root_states(conn, roots) if s.status == "complete")


def _recorded_kinds(conn, excluded_catalogs: Collection[str]) -> tuple[set[str], set[str]]:
    """Path norms of the sidecar records and of the non-sidecar records of the latest catalogs.

    Records of an owner-excluded catalog are left out.
    """
    ids = [s["id"] for s in store.latest_scans(conn, "catalogs")]
    sidecar, own = set(), set()
    if not ids:
        return sidecar, own
    excluded = {paths.norm(p) for p in excluded_catalogs}
    sql = ('SELECT r.path_norm, r.is_sidecar, c.source_path FROM "record" r '
           'JOIN "catalog" c ON c.id = r.catalog_id '
           f'WHERE c.scan_id IN ({",".join("?" * len(ids))})')
    for row in conn.execute(sql, ids):
        if paths.norm(row["source_path"]) not in excluded:
            (sidecar if row["is_sidecar"] else own).add(row["path_norm"])
    return sidecar, own


def pair_rows(conn, *, library_roots: list[str], excluded_catalogs: Collection[str],
              coverage_complete: bool) -> list[dict]:
    files = classify.inventoried_files(conn, library_roots, photos_only=True)
    raws, jpegs = defaultdict(list), defaultdict(list)
    for f in files:
        folder, name = paths.split_path(f["path_norm"])
        key = (folder, name.rpartition(".")[0])
        if f["ext"] in paths.RAW_EXTS:
            raws[key].append(f)
        else:
            jpegs[key].append(f)
    sidecar, own = _recorded_kinds(conn, excluded_catalogs)
    acc = defaultdict(lambda: defaultdict(int))
    for key, js in jpegs.items():
        rs = raws.get(key)
        if not rs:
            continue
        for j in js:
            kind = ("sidecar" if j["path_norm"] in sidecar
                    else "own_photo" if j["path_norm"] in own else "uncatalogued")
            row = acc[paths.split_path(j["path_orig"])[0]]
            row["pairs"] += 1
            row["jpeg_bytes"] += j["size"]
            row[kind + "_pairs"] += 1
            row[kind + "_bytes"] += j["size"]
            if any(abs(r["mtime_ns"] - j["mtime_ns"]) <= SAME_TIME_NS for r in rs):
                row["same_time_pairs"] += 1
                row["same_time_bytes"] += j["size"]
    cols = ("pairs", "jpeg_bytes", "sidecar_pairs", "sidecar_bytes", "own_photo_pairs",
            "own_photo_bytes", "uncatalogued_pairs", "uncatalogued_bytes",
            "same_time_pairs", "same_time_bytes")
    how = PAIR_HOW if coverage_complete else (
        classify.NOT_RECORDED + "a catalog that could not be read, or that changed, may record "
        "these files; " + PAIR_HOW)
    rows = [{"folder": folder, **{c: vals[c] for c in cols}, "how_decided": how}
            for folder, vals in acc.items()]
    return sorted(rows, key=lambda r: r["folder"])


def _catalogs_by_parent(conn) -> dict[str, list]:
    ids = [s["id"] for s in store.latest_scans(conn, "catalogs")]
    by_parent = defaultdict(list)
    if not ids:
        return by_parent
    sql = f'SELECT source_path, status FROM "catalog" WHERE scan_id IN ({",".join("?" * len(ids))})'
    for c in conn.execute(sql, ids):
        parent, name = paths.split_path(paths.norm(c["source_path"]))
        by_parent[parent].append((name.rpartition(".")[0], c))
    return by_parent


def container_rows(conn, *, library_roots: list[str], coverage_complete: bool) -> list[dict]:
    ids = _complete_scan_ids(conn, library_roots)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    counts = {r[0]: r[1] for r in conn.execute(
        f'SELECT container, COUNT(*) FROM "file" WHERE scan_id IN ({marks}) AND container != \'\' '
        "AND ext NOT IN ('lrdata', '<unreadable>') GROUP BY container", ids)}
    by_parent = _catalogs_by_parent(conn)
    rows = []
    for f in conn.execute(f'SELECT * FROM "file" WHERE scan_id IN ({marks}) AND ext = \'lrdata\'', ids):
        kind = (paths.split_container(f["container"]) or ("unknown", ""))[0]
        parent, name = paths.split_path(f["path_norm"])
        beside = [(stem, c) for stem, c in by_parent.get(parent, [])
                  if stem and name.startswith(stem) and name[len(stem):len(stem) + 1] in (" ", ".")]
        cat = max(beside, key=lambda t: len(t[0]))[1] if beside else None
        reclaimable = ("yes" if coverage_complete else "unverified") if kind == "cache" else "no"
        how = {"yes": "a preview cache can be rebuilt; catalog coverage is complete",
               "unverified": "a preview cache, but catalog coverage incomplete",
               "no": f"a {kind} container holds data that cannot be rebuilt"}[reclaimable]
        rows.append({
            "container": f["path_orig"], "type": kind, "bytes": f["size"],
            "files": counts.get(f["container"], 0),
            "catalog_beside": cat["source_path"] if cat else "none",
            "catalog_state": cat["status"] if cat else "", "reclaimable": reclaimable,
            "how_decided": how})
    return sorted(rows, key=lambda r: r["container"])


def _app_folder(folder_orig: str) -> str | None:
    """The folder up to its first application component, or None."""
    parts = _SEP.split(folder_orig)
    for i in range(0, len(parts), 2):
        if parts[i].casefold() in APP_FOLDER_NAMES:
            return "".join(parts[:i + 1])
    return None


def loose_rows(conn, *, library_roots: list[str], photo_root: str) -> list[dict]:
    root = paths.norm(photo_root)
    acc = {}
    for f in classify.inventoried_files(conn, library_roots, photos_only=True):
        if f["path_norm"].startswith(root.rstrip("/") + "/"):
            continue
        folder = paths.split_path(f["path_orig"])[0]
        app = _app_folder(folder)
        key = (app or folder, "no" if app else "yes")
        row = acc.setdefault(key, {"folder": key[0], "raws": 0, "jpegs": 0, "bytes": 0,
                                   "listed": key[1]})
        row["raws" if f["ext"] in paths.RAW_EXTS else "jpegs"] += 1
        row["bytes"] += f["size"]
    for row in acc.values():
        row["how_decided"] = ("photo files outside the photo root, listed per folder"
                              if row["listed"] == "yes" else
                              "inside an application folder; counted, never listed per file")
    return sorted(acc.values(), key=lambda r: (r["folder"], r["listed"]))


def not_read_rows(conn, *, library_roots: list[str], backup_roots: list[str],
                  catalog_roots: list[str], excluded_catalogs: list[str],
                  stale_catalogs: Collection[str] = (),
                  unseen_catalogs: Collection[str]) -> list[dict]:
    rows = []

    def add(kind, item, detail, how):
        rows.append({"kind": kind, "item": item, "detail": detail, "how_decided": how})

    backups = {paths.norm(r) for r in backup_roots}
    states = classify.root_states(conn, [*library_roots, *backup_roots])
    scan_ids = sorted({s.scan_id for s in states if s.scan_id is not None})
    for s in states:
        if s.status == "partial":
            add("partial_root", s.root, "scan did not finish", "the latest files scan is partial")
        elif s.status == "not_connected":
            add("not_connected_root", s.root, "not connected", "the drive was absent at the scan")
        elif s.status == "not_scanned" and paths.norm(s.root) in backups:
            add("not_scanned_root", s.root, "not scanned", "no files scan exists for this backup root")
    if scan_ids:
        sql = ('SELECT path_orig, ext, placeholder FROM "file" WHERE scan_id IN '
               f'({",".join("?" * len(scan_ids))}) AND (placeholder = 1 OR ext = \'<unreadable>\')')
        for f in conn.execute(sql, scan_ids):
            if f["ext"] == "<unreadable>":
                add("unreadable_folder", f["path_orig"], "folder could not be read",
                    "the scan marked this folder unreadable")
            else:
                add("placeholder", f["path_orig"], "content not read",
                    "a cloud placeholder; the scan never read it")
    cov = classify.coverage(conn, catalog_roots=catalog_roots, excluded_catalogs=excluded_catalogs,
                            stale_catalogs=stale_catalogs, unseen_catalogs=unseen_catalogs)
    for cause in cov.limiting:
        if cause.endswith("(appeared since the catalogs scan)"):
            add("limiting_catalog", cause, "", "found in a catalog root now; not stored by the catalogs scan")
        else:
            add("limiting_catalog", cause, "", "this catalog or catalog root limits coverage")
    return sorted(rows, key=lambda r: (r["kind"], r["item"], r["detail"]))
