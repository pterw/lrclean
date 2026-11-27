"""The builder of the unrecorded-copies removal list (rules 46 to 48 and the coverage refusal of rule 50).

Reads the rows of `classify.inventoried_files`, the catalog views, the archive evidence, the counted full hashes
and the `out/purge-<batch>.csv` files. It reads no file content but through `hasher`. No write.
"""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from lrclean import backup_plan, hashing, mover_files, mover_journal, paths, reject_archive, reject_twin, rejects

REASONS = ("outside_photo_root", "placeholder", "recorded", "work", "sidecar", "twin_rejected", "changed",
           "no_archive_copy", "no_keeper")
_RECORDING_ROLES = ("live", "shoot")


class Incomplete(Exception):
    pass


class PurgeResultError(ValueError):
    pass


class CopyRow(NamedTuple):
    path: str
    bytes: int
    mtime_ns: int
    dests: tuple[str, ...]
    twin: str


class Purged(NamedTuple):
    by_size: Mapping[int, frozenset[str]]
    results: int
    rejects: int


class Built(NamedTuple):
    rows: tuple[CopyRow, ...]
    examined: Mapping[str, tuple[int, int]]
    purged: Purged


def _purged_rows(path: str):
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            table = list(csv.reader(handle))
    except (OSError, UnicodeError, csv.Error) as error:
        raise PurgeResultError(f"{path}: {error}") from error
    if not table or tuple(table[0]) != mover_journal.COLUMNS:
        raise PurgeResultError(f"{path}: the header is not {', '.join(mover_journal.COLUMNS)}")
    for row in (r for r in table[1:] if r):
        if len(row) != len(mover_journal.COLUMNS):
            raise PurgeResultError(f"{path}: a row has {len(row)} columns")
        rec = dict(zip(mover_journal.COLUMNS, row))
        if rec["kind"] == "reject" and rec["state"] == "purged":
            if not (re.fullmatch("[0-9]+", rec["bytes"]) and re.fullmatch("[0-9a-fA-F]{64}", rec["sha256"])):
                raise PurgeResultError(f"{path}: a purged reject has no valid bytes and sha256")
            yield int(rec["bytes"]), rec["sha256"].lower()


def read_purged(out_dir: str) -> Purged:
    try:
        names = sorted(n for n in os.listdir(out_dir) if n.startswith("purge-") and n.endswith(".csv")
                       and mover_files.valid_batch(n[len("purge-"):-len(".csv")]))
    except FileNotFoundError:
        return Purged({}, 0, 0)
    except OSError as error:
        raise PurgeResultError(f"{out_dir}: {error}") from error
    by_size: dict[int, set[str]] = {}
    counted = 0
    for name in names:
        for size, sha in _purged_rows(os.path.join(out_dir, name)):
            by_size.setdefault(size, set()).add(sha)
            counted += 1
    return Purged({size: frozenset(shas) for size, shas in by_size.items()}, len(names), counted)


class _Stat:
    """One `stat` per path: the answer, or the OSError, is kept and given again."""

    def __init__(self, stat):
        self._stat, self._seen = stat, {}

    def __call__(self, path):
        if path not in self._seen:
            try:
                self._seen[path] = self._stat(path)
            except OSError as error:
                self._seen[path] = error
        if isinstance(self._seen[path], OSError):
            raise self._seen[path]
        return self._seen[path]

    def get(self, path):
        try:
            return self(path)
        except OSError:
            return None


class _Cand(NamedTuple):
    pn: str
    path: str
    size: int
    mtime_ns: int
    file_index: int
    flagged: bool
    consistent: bool
    counted: reject_twin.Hashed | None


class _World(NamedTuple):
    views: Sequence[rejects.CatalogView]
    recording: list
    live: list
    shoot: list
    evidence: list
    hashes: reject_twin.HashIndex
    purged: Purged
    photo_root: str
    side_keys: frozenset
    st: _Stat
    attrs_of: object
    hasher: object


def _triple(row) -> tuple[int, int, int]:
    return row["size"], row["mtime_ns"], row["file_index"]


def _candidates(files, hashes: reject_twin.HashIndex) -> list[_Cand]:
    grouped: dict[str, list] = {}
    for row in files:
        if row["ext"] in paths.PHOTO_EXTS:
            grouped.setdefault(row["path_norm"], []).append(row)
    out = []
    for pn in sorted(grouped):
        first, *rest = grouped[pn]
        mine = _triple(first)
        hashed = hashes.by_path.get(pn)
        counted = hashed if hashed and (hashed.size, hashed.mtime_ns, hashed.file_index) == mine else None
        out.append(_Cand(pn, first["path_orig"], *mine, any(r["placeholder"] for r in grouped[pn]),
                         all(_triple(r) == mine for r in rest), counted))
    return out


def _group(c: _Cand, w: _World, digest: str | None = None) -> tuple[reject_twin.Hashed, ...]:
    """The files with the digest (the counted hash when none is given) and the candidate's size."""
    return w.hashes.by_hash.get((digest or (c.counted.full if c.counted else None), c.size), ())


def _keeps(view, pn: str) -> bool:
    return pn in view.absolute and not rejects.is_rejected(view, pn)


def _twin_rejected(c: _Cand, w: _World, digest: str | None = None) -> bool:
    names = [h.path_norm for h in _group(c, w, digest)]
    return any(rejects.is_rejected(v, n) for v in w.live for n in names) and not any(_keeps(v, n) for v in w.live for n in names)


def _placeholder(c: _Cand, w: _World) -> bool:
    st = w.st.get(paths.fs_path(c.path))
    return st is not None and paths.is_placeholder(w.attrs_of(c.path, st) if w.attrs_of else getattr(st, "st_file_attributes", 0))


def _changed(c: _Cand, w: _World) -> bool:
    st = w.st.get(paths.fs_path(c.path))
    return not c.consistent or st is None or (st.st_size, st.st_mtime_ns, paths.signed64(st.st_ino)) != (c.size, c.mtime_ns, c.file_index)


def _archive(c: _Cand, w: _World) -> list[tuple[str, str]]:
    st = w.st.get(paths.fs_path(c.path))
    return [reject_archive.archive_state(ev, c.path, st, stat=w.st, attrs_of=w.attrs_of) for ev in w.evidence]


_HOLDS = {
    "outside_photo_root": lambda c, w: not backup_plan.is_inside(c.path, w.photo_root, below_only=True),
    "placeholder": lambda c, w: c.flagged or _placeholder(c, w),
    "recorded": lambda c, w: any(c.pn in v.photos or c.pn in v.sidecars for v in w.recording),
    "work": lambda c, w: any(rejects.work_of(v, c.pn) for v in w.views),
    "sidecar": lambda c, w: bool(w.side_keys.intersection(rejects.base_index([c.pn]))),
    "twin_rejected": _twin_rejected,
    "changed": _changed,
    "no_archive_copy": lambda c, w: any(state != reject_archive.OK for state, _ in _archive(c, w)),
}


def _sha(c: _Cand, w: _World) -> str:
    return c.counted.full if c.counted else w.hasher(c.path)


def _purged_match(c: _Cand, w: _World) -> str | None:
    """The candidate's digest when it matches a purged reject's size and SHA-256, else None."""
    wanted = w.purged.by_size.get(c.size, ())
    digest = _sha(c, w) if wanted else None
    return digest if digest in wanted else None


def _shoot_reject(w: _World, n: str) -> bool:
    if not any(rejects.is_rejected(v, n) for v in w.shoot) or any(n in v.photos or n in v.sidecars for v in w.live):
        return False
    return not any(rejects.is_kept(v, n) or rejects.work_of(v, n) for v in w.views)


def _row(c: _Cand, w: _World, twin: str) -> CopyRow:
    return CopyRow(c.path, c.size, c.mtime_ns, tuple(dest for _, dest in _archive(c, w)), twin)


def _judge(c: _Cand, w: _World) -> tuple[str, CopyRow | None]:
    """The reason that holds the file, or a row (class (a) or (b)); `no_keeper` when it fits neither."""
    name = next((n for n, held in _HOLDS.items() if held(c, w)), "")
    if name:
        return name, None
    twins = reject_twin.twin_candidates(w.hashes, c.pn, stat=w.st, attrs_of=w.attrs_of) if c.counted else []
    twin = next((t for t in twins if any(_keeps(v, t.path_norm) for v in w.live)), None)
    if twin:
        return "", _row(c, w, twin.path_orig)
    try:
        matched = _purged_match(c, w)
    except OSError:
        return "changed", None
    if matched and _twin_rejected(c, w, matched):
        return "twin_rejected", None
    if matched or any(_shoot_reject(w, t.path_norm) for t in twins):
        return "", _row(c, w, "")
    return "no_keeper", None


def _sidecar_keys(files, views) -> frozenset:
    names = [r["path_norm"] for r in files if r["ext"] in paths.SIDECAR_EXTS]
    names += [n for v in views for n in v.sidecars if os.path.splitext(n)[1][1:] in paths.SIDECAR_EXTS]
    return frozenset(rejects.base_index(names))


def build(files, views, evidence, hashes, purged: Purged, *, photo_root: str, coverage, stat=os.stat,
          attrs_of=None, hasher=hashing.full_hash) -> Built:
    if not coverage.complete:
        raise Incomplete("run catalogs again, or add it to excluded_catalogs: catalog coverage is incomplete: " + "; ".join(coverage.limiting))
    recording = [v for v in views if v.role in _RECORDING_ROLES and v.status in ("ok", "partial") and not v.excluded]
    w = _World(views, recording, [v for v in views if v.role == "live"], [v for v in views if v.role == "shoot"],
               evidence, hashes, purged, photo_root, _sidecar_keys(files, views), _Stat(stat), attrs_of, hasher)
    tally = {name: (0, 0) for name in REASONS}
    out: list[CopyRow] = []
    for c in _candidates(files, hashes):
        if c.size not in purged.by_size and len(_group(c, w)) < 2:
            continue
        name, row = _judge(c, w)
        if row:
            out.append(row)
        else:
            tally[name] = (tally[name][0] + 1, tally[name][1] + c.size)
    return Built(tuple(out), tally, purged)
