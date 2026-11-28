"""The reject check: catalog views, flags, keeps, work and freshness (rules 24, 25, 26).

Reads the `catalog`, `record` and `image` rows of the latest `catalogs` scans; reads
`file` and `hash` rows only through `reject_twin` (rule 31). No write.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Collection, Mapping
from types import MappingProxyType
from typing import NamedTuple

from lrclean import paths, reject_archive, reject_twin, store

VERDICTS = ("on_list", "coverage_incomplete", "kept_in_live", "kept", "kept_for_work", "missing",
            "placeholder", "outside_backup", "changed_since_backup", "no_archive_copy")

_FLAG_ROLES = ("live", "shoot")


class StaleFlags(Exception):
    pass


class CatalogView(NamedTuple):
    source: str
    role: str
    status: str
    excluded: bool
    picks: dict[str, list[int | None]]
    developed: dict[str, list[int | None]]
    photos: set[str]
    sidecars: set[str]
    orig: dict[str, str]
    absolute: frozenset[str] = frozenset()
    absolute_images: Mapping[str, int] = MappingProxyType({})
    settings: Mapping[str, list[str | None]] = MappingProxyType({})
    absolute_settings: Mapping[str, tuple[tuple[int | None, str | None], ...]] = MappingProxyType({})


class Reject(NamedTuple):
    path: str
    path_norm: str
    bytes: int
    mtime_ns: int
    verdict: str
    rejected_by: list[tuple[str, str]]
    kept_by: list[tuple[str, str]]
    archive: list[str]
    developed_in: list[tuple[str, str, str]]
    images: list[tuple[str, int]]
    sidecars: list[str]
    sidecar_bytes: int
    held_by: str
    dests: list[str]
    twin: str = ""
    twin_settings: str = ""


def _view(conn, cat, excluded: set[str]) -> CatalogView:
    picks: dict[str, list[int | None]] = {}
    developed: dict[str, list[int | None]] = {}
    settings: dict[str, list[str | None]] = {}
    absolute: set[str] = set()
    absolute_images: dict[str, int] = {}
    absolute_settings: dict[str, list[tuple[int | None, str | None]]] = {}
    for row in conn.execute('SELECT path_norm, via, pick, developed, settings FROM "image" WHERE catalog_id = ?',
                            (cat["id"],)):
        if row["via"] == "absolute":
            absolute.add(row["path_norm"])
            absolute_images[row["path_norm"]] = absolute_images.get(row["path_norm"], 0) + 1
            absolute_settings.setdefault(row["path_norm"], []).append((row["developed"], row["settings"]))
        picks.setdefault(row["path_norm"], []).append(row["pick"])
        developed.setdefault(row["path_norm"], []).append(row["developed"])
        settings.setdefault(row["path_norm"], []).append(row["settings"])
    photos, sidecars = set(picks), set()
    orig: dict[str, str] = {}
    for row in conn.execute('SELECT path_norm, path_orig, is_sidecar FROM "record" '
                            'WHERE catalog_id = ? ORDER BY rowid', (cat["id"],)):
        (sidecars if row["is_sidecar"] else photos).add(row["path_norm"])
        orig.setdefault(row["path_norm"], row["path_orig"])
    for name in photos:
        orig.setdefault(name, name)
    return CatalogView(cat["source_path"], cat["role"], cat["status"],
                       paths.norm(cat["source_path"]) in excluded, picks, developed,
                       photos, sidecars, orig, frozenset(absolute),
                       MappingProxyType(absolute_images), settings,
                       MappingProxyType({k: tuple(v) for k, v in absolute_settings.items()}))


def latest_views(conn, *, excluded_catalogs) -> list[CatalogView]:
    ids = [s["id"] for s in store.latest_scans(conn, "catalogs")]
    if not ids:
        return []
    excluded = {paths.norm(p) for p in excluded_catalogs}
    sql = f'SELECT * FROM "catalog" WHERE scan_id IN ({",".join("?" * len(ids))}) ORDER BY id'
    return [_view(conn, cat, excluded) for cat in conn.execute(sql, ids).fetchall()]


def is_rejected(view: CatalogView, path_norm: str) -> bool:
    if view.role not in _FLAG_ROLES or view.status != "ok" or view.excluded:
        return False
    picks = view.picks.get(path_norm)
    if not picks or path_norm not in view.absolute:
        return False
    return all(pick == -1 for pick in picks)


def _keys(path_norm: str) -> tuple[tuple[str, str], ...]:
    """The plain key `(folder, stem)`; a sidecar named after a whole photo name also gets the photo's key."""
    folder, name = paths.split_path(path_norm)
    stem, ext = os.path.splitext(name)
    keys = ((folder, stem),)
    if ext[1:] in paths.SIDECAR_EXTS and os.path.splitext(stem)[1][1:] in paths.PHOTO_EXTS:
        keys += ((folder, os.path.splitext(stem)[0]),)
    return keys


def base_index(names) -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}
    for name in names:
        for key in _keys(name):
            index.setdefault(key, []).append(name)
    return index


def is_kept(view: CatalogView, path_norm: str, index=None) -> bool:
    if view.role not in _FLAG_ROLES or view.status not in ("ok", "partial") or view.excluded:
        return False
    if path_norm in view.photos and not is_rejected(view, path_norm):
        return True
    if path_norm not in view.sidecars:
        return False
    index = base_index(view.photos) if index is None else index
    return any(not is_rejected(view, photo) for key in _keys(path_norm) for photo in index.get(key, ()))


def work_of(view: CatalogView, path_norm: str) -> str:
    if view.status not in ("ok", "partial") or view.excluded:
        return ""
    picks, developed = view.picks.get(path_norm), view.developed.get(path_norm)
    if not picks or not developed:
        return ""
    has_develop = any(d is None or d == 1 for d in developed)
    has_pick = any(pick == 1 for pick in picks)
    return "+".join(name for name, held in (("develop", has_develop), ("pick", has_pick)) if held)


def reject_set(views) -> dict[str, list[CatalogView]]:
    out: dict[str, list[CatalogView]] = {}
    for view in views:
        for path_norm in view.picks:
            if is_rejected(view, path_norm):
                out.setdefault(path_norm, []).append(view)
    return out


def partly_rejected(views) -> list[tuple[str, str]]:
    found = set()
    for view in views:
        if view.role not in _FLAG_ROLES:
            continue
        for path_norm, picks in view.picks.items():
            if -1 in picks and path_norm in view.absolute and not is_rejected(view, path_norm):
                found.add((view.source, view.orig.get(path_norm, path_norm)))
    return sorted(found)


def relative_only_flags(views) -> list[tuple[str, int]]:
    found = []
    for view in views:
        if view.role not in _FLAG_ROLES:
            continue
        count = sum(1 for path_norm, picks in view.picks.items() if -1 in picks and path_norm not in view.absolute)
        if count:
            found.append((view.source, count))
    return sorted(found)


def sidecars_of(views, path_norm: str, indexes=None, *,
                files_sidecars: Mapping[str, str] = MappingProxyType({})) -> dict[str, str]:
    """The rejecting views' recorded sidecars of `path_norm`, then the files scan's `<path_norm>.xmp`/`.acr`."""
    keys = _keys(path_norm)
    out: dict[str, str] = {}
    for view in views:
        if not is_rejected(view, path_norm):
            continue
        index = indexes[view.source] if indexes else base_index(view.sidecars)
        for side in (s for key in keys for s in index.get(key, ())):
            if side != path_norm:
                out.setdefault(side, view.orig.get(side, side))
        for side in (f"{path_norm}.xmp", f"{path_norm}.acr"):
            if side in files_sidecars:
                out.setdefault(side, files_sidecars[side])
    return out


def check_fresh(views, *, live_catalog: str | None, stale: Collection[str],
                expected_roles: Mapping[str, str]) -> None:
    live_norm = paths.norm(live_catalog) if live_catalog else None
    live = next((v for v in views if paths.norm(v.source) == live_norm), None) if live_norm else None
    if live is None:
        raise StaleFlags("run catalogs again: the live catalog is not in the latest scan")
    if live.status != "ok":
        raise StaleFlags(f"run catalogs again: the live catalog was read as {live.status}")
    if paths.norm(live.source) in {paths.norm(s) for s in stale}:
        raise StaleFlags("run catalogs again: the live catalog changed since its scan")
    if not live.picks:
        raise StaleFlags("run catalogs again: the live catalog has no image rows")
    if all(pick is None for picks in live.picks.values() for pick in picks):
        raise StaleFlags("run catalogs again: every pick of the live catalog is empty")
    for view in views:
        if view.role != expected_roles.get(paths.norm(view.source)):
            raise StaleFlags(f"run catalogs again: the role of {view.source} changed")


class _Subject(NamedTuple):
    norm: str
    orig: str
    st: object
    is_reject: bool
    gone: bool
    states: list[str]
    dests: list[str]


def _stat_of(path: str, stat):
    try:
        return stat(paths.fs_path(path)), False
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False


def _subject(evidence, norm_path, orig, st, is_reject, gone, stat, attrs_of) -> _Subject:
    found = [reject_archive.archive_state(ev, orig, st, stat=stat, attrs_of=attrs_of) for ev in evidence]
    return _Subject(norm_path, orig, st, is_reject, gone, [f[0] for f in found], [f[1] for f in found])


def _subjects(evidence, pn, path, sides, stat, attrs_of) -> list[_Subject]:
    st, gone = _stat_of(path, stat)
    out = [_subject(evidence, pn, path, st, True, gone, stat, attrs_of)]
    for norm_path, orig in sorted(sides.items()):
        side_st, side_gone = _stat_of(orig, stat)
        if not side_gone:
            out.append(_subject(evidence, norm_path, orig, side_st, False, False, stat, attrs_of))
    return out


def _is_placeholder(subject: _Subject, attrs_of) -> bool:
    if subject.st is None:
        return False
    attrs = attrs_of(subject.orig, subject.st) if attrs_of else getattr(subject.st, "st_file_attributes", 0)
    return paths.is_placeholder(attrs)


def _develop_pairs(view: CatalogView, path_norm: str):
    developed = view.developed.get(path_norm, ())
    return zip(developed, view.settings.get(path_norm) or [None] * len(developed))


def _shared_settings(path_norm: str, reads: list[CatalogView]) -> str:
    """Rule 26a, conditions 3 to 5: the one `settings` all develop work of the reject shares, else empty."""
    if any(v.role == "shoot" and (path_norm in v.photos or path_norm in v.sidecars) for v in reads):
        return ""
    if any("pick" in work_of(v, path_norm) for v in reads):
        return ""
    held = {(d, s or None) for v in reads for d, s in _develop_pairs(v, path_norm) if d is None or d == 1}
    return next((s or "" for d, s in held if d == 1), "") if len(held) == 1 else ""


def _stays(twin_norm: str, settings: str, reads: list[CatalogView]) -> bool:
    """Rule 26a, condition 2 (after the file facts): release evidence through the absolute form only."""
    return any(v.role == "shoot" and twin_norm in v.absolute for v in reads) and any(v.role == "live" and (1, settings) in v.absolute_settings.get(twin_norm, ()) for v in reads)


def _lift(path_norm, views, hashes, stat, attrs_of) -> tuple[str, str]:
    """The twin's `path_orig` and the shared `settings` when rule 26a lifts the develop hold, else two empty strings."""
    reads = [v for v in views if v.status in ("ok", "partial") and not v.excluded]
    settings = _shared_settings(path_norm, reads)
    if not settings:
        return "", ""
    twin = next((t for t in reject_twin.twin_candidates(hashes, path_norm, stat=stat, attrs_of=attrs_of) if _stays(t.path_norm, settings, reads)), None)
    return (twin.path_orig, settings) if twin else ("", "")


def _verdict(subjects, views, photo_ix, coverage_complete, attrs_of, lifted) -> tuple[str, str]:
    tests = {
        "coverage_incomplete": lambda s: not coverage_complete,
        "kept_in_live": lambda s: s.is_reject and any(v.role == "live" and is_kept(v, s.norm, photo_ix[v.source]) for v in views),
        "kept": lambda s: any(is_kept(v, s.norm, photo_ix[v.source]) for v in views),
        "kept_for_work": lambda s: not (s.is_reject and lifted) and any(work_of(v, s.norm) for v in views),
        "missing": lambda s: s.is_reject and s.gone,
        "placeholder": lambda s: _is_placeholder(s, attrs_of),
        "outside_backup": lambda s: reject_archive.OUTSIDE_BACKUP in s.states,
        "changed_since_backup": lambda s: reject_archive.CHANGED_SINCE_BACKUP in s.states,
        "no_archive_copy": lambda s: reject_archive.NO_ARCHIVE_COPY in s.states,
    }
    for name in VERDICTS[1:]:
        hit = next((s for s in subjects if tests[name](s)), None)
        if hit is not None:
            return name, "" if hit.is_reject else hit.orig
    return "on_list", ""


def _root_state(subjects, root: int) -> str:
    return next((s.states[root] for s in subjects if s.states[root] != reject_archive.OK), reject_archive.OK)


def _chain(pn: str, direct: dict[str, dict[str, str]]) -> dict[str, str]:
    """RC-107: the sidecars of `pn`, and those of every reject among them, each path once (a cycle ends)."""
    seen, out, todo = {pn}, {}, [pn]
    while todo:
        for side, orig in direct[todo.pop()].items():
            if side not in seen:
                seen.add(side)
                out[side] = orig
                if side in direct:
                    todo.append(side)
    return out


def _one(pn, rejecters, views, photo_ix, evidence, coverage_complete, stat, attrs_of, hashes,
         sides, direct) -> tuple[Reject, list[str]]:
    path = rejecters[0].orig.get(pn, pn)
    subjects = _subjects(evidence, pn, path, sides, stat, attrs_of)
    twin, twin_settings = _lift(pn, views, hashes, stat, attrs_of)
    verdict, held_by = _verdict(subjects, views, photo_ix, coverage_complete, attrs_of, bool(twin))
    head = subjects[0]
    return Reject(
        path, pn, head.st.st_size if head.st else 0, head.st.st_mtime_ns if head.st else 0, verdict,
        [(v.source, v.role) for v in rejecters],
        [(v.source, v.role) for v in views if is_kept(v, pn, photo_ix[v.source])],
        [_root_state(subjects, n) for n in range(len(evidence))],
        [(v.source, v.role, w) for v in views if (w := work_of(v, pn))],
        [(v.source, v.absolute_images.get(pn, 0)) for v in views if pn in v.picks],
        [s.orig for s in subjects[1:]], sum(s.st.st_size for s in subjects[1:] if s.st), held_by, head.dests,
        twin, twin_settings,
    ), [s.norm for s in subjects[1:] if s.norm in direct]


def _ancestors(holder: dict[str, str], pn: str) -> set[str]:
    """`pn` and every reject the hold reached it through; a reject on this chain never takes the hold back from it."""
    chain = {pn}
    while pn in holder:
        pn = holder[pn]
        chain.add(pn)
    return chain


def _hold_family(found: dict[str, Reject], owned: dict[str, list[str]], travelling: dict[str, list[str]]) -> None:
    """`travelling[pn]`: the rejects in `pn`'s chain (RC-113): a hold that reaches one of them reaches `pn` too."""
    own = {pn: r.verdict for pn, r in found.items()}
    holder: dict[str, str] = {}
    sharers: dict[str, list[str]] = {}
    for pn in sorted(found):
        for side in owned[pn]:
            sharers.setdefault(side, []).append(pn)
    reach = {pn: {s for s in owned[pn] if s in found} | {o for s in owned[pn] for o in sharers[s]} for pn in found}
    for pn, chained in travelling.items():
        for side in chained:
            reach[side].add(pn)
    queue = deque(pn for pn in sorted(found) if own[pn] != "on_list")
    while queue:
        top = found[queue.popleft()]
        ancestors = _ancestors(holder, top.path_norm)
        shared_in = reach[holder[top.path_norm]] if top.path_norm in holder and top.path_norm not in owned[holder[top.path_norm]] else set()
        for pn in sorted(reach[top.path_norm] - shared_in):
            current = found[pn]
            if own[pn] != "on_list" or holder.get(pn, top.path_norm) < top.path_norm or pn in ancestors:
                continue
            if (current.verdict, current.held_by) != (top.verdict, top.path):
                holder[pn] = top.path_norm
                found[pn] = current._replace(verdict=top.verdict, held_by=top.path)
                queue.append(pn)


def decide(views, evidence, *, coverage_complete: bool, stat=os.stat, attrs_of=None,
           hashes=reject_twin.EMPTY, files_sidecars: Mapping[str, str] = MappingProxyType({})) -> list[Reject]:
    if len(evidence) < 2:
        raise ValueError("the reject check needs the evidence of at least two archive roots")
    indexes = {v.source: base_index(v.sidecars) for v in views}
    photo_ix = {v.source: base_index(v.photos) for v in views}
    found: dict[str, Reject] = {}
    owned: dict[str, list[str]] = {}
    rejected = reject_set(views)
    direct = {pn: sidecars_of(views, pn, indexes, files_sidecars=files_sidecars) for pn in rejected}
    travelling: dict[str, list[str]] = {}
    for pn, rejecters in rejected.items():
        chain = _chain(pn, direct)
        found[pn], owned[pn] = _one(pn, rejecters, views, photo_ix, evidence, coverage_complete, stat, attrs_of,
                                    hashes, chain, direct[pn])
        travelling[pn] = [n for n in map(paths.norm, found[pn].sidecars) if n in direct]
    _hold_family(found, owned, travelling)
    return [found[pn] for pn in sorted(found)]
