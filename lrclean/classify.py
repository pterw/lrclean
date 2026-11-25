"""The verdict engine: which library files may later be called safe to move.

Read-only. Every decision comes from rows the scans stored; the only
look at the disk is the `stat` parameter used for the rule 13 recheck.
"""

import os
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Collection
from typing import NamedTuple

from lrclean import paths, store

NOT_RECORDED = "not recorded by the catalogs that could be read; coverage incomplete: "


class RootState(NamedTuple):
    root: str
    scan_id: int | None
    status: str


class Coverage(NamedTuple):
    complete: bool
    limiting: list[str]


class Verdict(NamedTuple):
    file_id: int
    path_orig: str
    size: int
    klass: str
    twin_file_id: int | None
    group: str
    recorded: bool
    how_decided: str


def _states(conn, roots: list[str], kind: str) -> list[RootState]:
    latest = {paths.norm(s["root"]): s for s in store.latest_scans(conn, kind)}
    states = []
    for root in roots:
        scan = latest.get(paths.norm(root))
        if scan is None:
            states.append(RootState(root, None, "not_scanned"))
            continue
        status = scan["status"] if scan["status"] in ("complete", "not_connected") else "partial"
        states.append(RootState(root, scan["id"], status))
    return states


def root_states(conn, roots: list[str]) -> list[RootState]:
    return _states(conn, roots, "files")


def _latest_catalogs(conn, statuses: tuple[str, ...], excluded: list[str]) -> list[sqlite3.Row]:
    scan_ids = [s["id"] for s in store.latest_scans(conn, "catalogs")]
    if not scan_ids:
        return []
    skip = {paths.norm(p) for p in excluded}
    sql = (f'SELECT * FROM "catalog" WHERE scan_id IN ({",".join("?" * len(scan_ids))})'
           f' AND status IN ({",".join("?" * len(statuses))}) ORDER BY id')
    rows = conn.execute(sql, [*scan_ids, *statuses])
    return [r for r in rows if paths.norm(r["source_path"]) not in skip]


def coverage(conn, *, catalog_roots: list[str], excluded_catalogs: list[str],
             stale_catalogs: Collection[str] = (),
             unseen_catalogs: Collection[str] = ()) -> Coverage:
    limiting = [r["source_path"] for r in
                _latest_catalogs(conn, ("unreadable", "partial"), excluded_catalogs)]
    for state in _states(conn, catalog_roots, "catalogs"):
        if state.status != "complete":
            limiting.append(f"{state.root} ({state.status.replace('_', ' ')})")
    skip = {paths.norm(p) for p in excluded_catalogs}
    limiting += [f"{p} (changed since its snapshot)" for p in stale_catalogs
                 if paths.norm(p) not in skip]
    limiting += [f"{p} (appeared since the catalogs scan)" for p in unseen_catalogs
                 if paths.norm(p) not in skip]
    return Coverage(not limiting, limiting)


def recorded_norms(conn, *, excluded_catalogs: list[str]) -> set[str]:
    ids = [r["id"] for r in _latest_catalogs(conn, ("ok", "partial"), excluded_catalogs)]
    if not ids:
        return set()
    sql = f'SELECT path_norm FROM "record" WHERE catalog_id IN ({",".join("?" * len(ids))})'
    return {r["path_norm"] for r in conn.execute(sql, ids)}


def inventoried_files(conn, roots: list[str], *, photos_only: bool = False) -> list[sqlite3.Row]:
    ids = sorted({s.scan_id for s in root_states(conn, roots) if s.status == "complete"})
    if not ids:
        return []
    sql = (f'SELECT * FROM "file" WHERE scan_id IN ({",".join("?" * len(ids))})'
           ' AND ext NOT IN (?, ?) AND COALESCE(container, \'\') NOT LIKE ? ORDER BY id')
    rows = conn.execute(sql, [*ids, "lrdata", "<unreadable>", "cache:%"]).fetchall()
    if photos_only:
        rows = [r for r in rows if r["ext"] in paths.PHOTO_EXTS]
    return rows


def eligible_files(conn, *, library_roots: list[str]) -> list[sqlite3.Row]:
    return inventoried_files(conn, library_roots, photos_only=True)


class _Hash(NamedTuple):
    sample: str
    full: str


class _Ctx(NamedTuple):
    recorded: set[str]
    cov: Coverage
    stat: object
    hashes: dict[int, _Hash]
    aliases: dict[int, str]
    group_of: dict[int, list]
    by_sample: dict[tuple, list]
    raw_stems: set[tuple[str, str]]
    unhashed: dict[tuple, int]
    memo: dict


def _valid_hashes(conn, files) -> dict[int, _Hash]:
    by_id = {f["id"]: f for f in files}
    found = {}
    for h in conn.execute('SELECT file_id, file_index, size, mtime_ns, sample, "full" FROM "hash"'):
        f = by_id.get(h["file_id"])
        if f and (h["file_index"], h["size"], h["mtime_ns"]) == (f["file_index"], f["size"], f["mtime_ns"]):
            found[f["id"]] = _Hash(h["sample"] or "", h["full"] or "")
    return found


def _kind(row) -> str:
    return "raw" if row["ext"] in paths.RAW_EXTS else "jpeg"


def _stem(row) -> tuple[str, str]:
    folder, name = paths.split_path(row["path_norm"])
    return folder, name.rpartition(".")[0] or name


def _aliases(files) -> dict[int, str]:
    names = defaultdict(list)
    for f in files:
        if f["file_index"]:
            names[(f["volume"], f["file_index"])].append(f["id"])
    return {i: f"{vol}:{idx}" for (vol, idx), ids in names.items() if len(ids) > 1 for i in ids}


def _identity_groups(files, hashes) -> dict[int, list]:
    groups = defaultdict(list)
    for f in files:
        h = hashes.get(f["id"])
        if h and h.full:
            key = (_kind(f), f["size"], h.full)
            groups[key].append(f)
    return {m["id"]: members for members in groups.values() if len(members) > 1 for m in members}


def _is_recorded(f, c: _Ctx) -> bool:
    return f["path_norm"] in c.recorded


def _changes(row, who: str, stat) -> list[str]:
    try:
        st = stat(paths.fs_path(row["path_orig"]))
    except OSError:
        return [f"{who} is gone"]
    seen = (("size", st.st_size, row["size"]), ("mtime", st.st_mtime_ns, row["mtime_ns"]),
            ("index", paths.signed64(st.st_ino), row["file_index"]))
    found = [f"{who} changed since the scan ({name})" for name, now, then in seen if now != then]
    if paths.is_placeholder(getattr(st, "st_file_attributes", 0)):
        found.append(f"{who} is now an online-only placeholder")
    return found


def _candidate(f, twin, group: str, c: _Ctx):
    if not c.cov.complete:
        return "coverage_incomplete", twin["id"], group, NOT_RECORDED + ";".join(c.cov.limiting)
    problems = _changes(f, "file", c.stat) + _changes(twin, "twin", c.stat)
    if problems:
        return "changed_since_scan", twin["id"], group, "; ".join(problems)
    return "safe_candidate", twin["id"], group, f"unrecorded; identical full hash to recorded {twin['path_orig']}"


def _grouped(f, members, c: _Ctx):
    group = f"{f['size']}:{c.hashes[f['id']].full[:12]}"
    if id(members) not in c.memo:
        recs = [m for m in members if _is_recorded(m, c)]
        c.memo[id(members)] = (recs, [m for m in recs if not m["placeholder"]])
    recs, twins = c.memo[id(members)]
    if _is_recorded(f, c):
        if len(recs) >= 2:
            return "needs_eye_many_recorded", None, group, "one of several recorded identical copies"
        return "recorded", None, group, "recorded by a catalog"
    if not recs:
        return "needs_eye_no_recorded", None, group, "identical copies, none recorded"
    if not twins:
        return "not_compared", None, group, "the only recorded identical copy is a placeholder"
    return _candidate(f, twins[0], group, c)


def _ungrouped(f, c: _Ctx):
    h = c.hashes.get(f["id"])
    if not h or not h.sample:
        return "not_compared", None, "", "no valid sample hash"
    blind = c.unhashed.get((_kind(f), f["size"]), 0)
    if blind:
        return "not_compared", None, "", f"{blind} other file(s) of the same size have no usable hash"
    others = [m for m in c.by_sample[(f["size"], h.sample)] if m["id"] != f["id"]]
    if all(h.full and c.hashes[m["id"]].full and h.full != c.hashes[m["id"]].full for m in others):
        return "unique_unrecorded", None, "", "no other library file shares its size and sample"
    return "not_compared", None, "", "collides with a file not fully hashed, or identical under another kind"


def _decide(f, c: _Ctx):
    if f["placeholder"]:
        return "placeholder", None, "", "placeholder file; its content was not read"
    if f["id"] in c.aliases:
        return "same_file", None, c.aliases[f["id"]], "another name of the same file (same file index)"
    members = c.group_of.get(f["id"])
    if members:
        return _grouped(f, members, c)
    if _is_recorded(f, c):
        return "recorded", None, "", "recorded by a catalog"
    return _ungrouped(f, c)


def verdicts(conn, *, library_roots: list[str], catalog_roots: list[str],
             excluded_catalogs: list[str], stale_catalogs: Collection[str] = (),
             unseen_catalogs: Collection[str] = (), stat=os.stat) -> list[Verdict]:
    files = eligible_files(conn, library_roots=library_roots)
    hashes = _valid_hashes(conn, files)
    by_sample = defaultdict(list)
    for f in files:
        if f["id"] in hashes and hashes[f["id"]].sample:
            by_sample[(f["size"], hashes[f["id"]].sample)].append(f)
    unhashed = Counter((_kind(f), f["size"]) for f in files
                       if f["placeholder"] or not (f["id"] in hashes and hashes[f["id"]].sample))
    ctx = _Ctx(
        recorded_norms(conn, excluded_catalogs=excluded_catalogs),
        coverage(conn, catalog_roots=catalog_roots, excluded_catalogs=excluded_catalogs,
                 stale_catalogs=stale_catalogs, unseen_catalogs=unseen_catalogs),
        stat, hashes, _aliases(files), _identity_groups(files, hashes), by_sample,
        {_stem(f) for f in files if _kind(f) == "raw"}, unhashed, {})
    out = []
    for f in files:
        klass, twin, group, how = _decide(f, ctx)
        if _kind(f) == "jpeg" and _stem(f) in ctx.raw_stems:
            how += "; paired JPEG (a same-base raw is in its folder)"
        out.append(Verdict(f["id"], f["path_orig"], f["size"], klass, twin, group,
                           _is_recorded(f, ctx), how))
    return out
