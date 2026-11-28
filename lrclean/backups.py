"""External copies of library raws on backup roots (section 5) and `verify`.

Rule 1: only a backup root whose latest files scan is complete can hold a copy.
Rule 8: a copy is `copied_verified` only when both full hashes are valid and equal;
both valid and different is no copy at all.
"""

import os
import re
from collections import Counter, defaultdict

from lrclean import classify, hashing, paths, store

OUTSIDE = "(outside photo root)"
TOP_OF_PHOTO_ROOT = "(top of photo root)"
TOP_OF_ROOT = "(top of backup root)"
SAMPLED_HOW = ("equal size and sample hash on a complete backup root; verified "
               "when both full hashes are valid and equal")


class NoRawUnder(Exception):
    """`verify` found no raw file under the folder in the latest files scans."""


def _raws(conn, roots: list[str]) -> list:
    return [r for r in classify.inventoried_files(conn, roots, photos_only=True)
            if r["ext"] in paths.RAW_EXTS]


def _complete_roots(conn, backup_roots: list[str]) -> dict[int, str]:
    return {s.scan_id: s.root for s in classify.root_states(conn, backup_roots)
            if s.status == "complete"}


def _index(rows, hashes, root_of: dict[int, str]) -> dict[tuple, list]:
    index = defaultdict(list)
    for row in rows:
        h = hashes.get(row["id"])
        if h and h.sample:
            index[(row["size"], h.sample)].append((root_of[row["scan_id"]], row, h))
    return index


def _match(lib_h, copy_h) -> str:
    """'' when both full hashes are valid and differ, else the strength of the match."""
    if lib_h.full and copy_h.full:
        return "verified" if lib_h.full == copy_h.full else ""
    return "sampled"


def _candidates(lib_row, lib_h, index) -> list:
    """Same size and sample, never the library raw itself (same volume and file index)."""
    same = (lib_row["volume"], lib_row["file_index"])
    return [(root, row, h) for root, row, h in index.get((lib_row["size"], lib_h.sample), ())
            if (row["volume"], row["file_index"]) != same]


def _best_per_root(lib_row, lib_h, index) -> dict[str, str]:
    best: dict[str, str] = {}
    for root, _row, h in _candidates(lib_row, lib_h, index):
        kind = _match(lib_h, h)
        if kind and best.get(root) != "verified":
            best[root] = kind
    return best


def _rel_parts(row, base_norm: str) -> list[str]:
    """Path parts below `base_norm`, spelled as on disk (`path_norm` only counts them)."""
    count = len(row["path_norm"][len(base_norm) + 1:].split("/"))
    return re.split(r"[\\/]+", row["path_orig"])[-count:]


def _group(row, photo_norm: str, depth: int) -> str:
    if not row["path_norm"].startswith(photo_norm + "/"):
        return OUTSIDE
    folders = _rel_parts(row, photo_norm)[:-1]
    return "/".join(folders[:depth]) if len(folders) >= depth else TOP_OF_PHOTO_ROOT


def _top_folder(row, root: str) -> str:
    rest = _rel_parts(row, paths.norm(root).rstrip("/"))
    return rest[0] if len(rest) > 1 else TOP_OF_ROOT


def _roots_text(counts: Counter) -> str:
    return ";".join(f"{root}={n}" for root, n in sorted(counts.items()))


def _year_rows(lib, hashes, index, roots, photo_root, depth, note) -> list[dict]:
    photo_norm = paths.norm(photo_root).rstrip("/")
    groups = defaultdict(lambda: {"sampled": 0, "verified": 0, "none": 0, "bytes": 0,
                                  "roots": Counter(dict.fromkeys(roots, 0))})
    for row in lib:
        g = groups[_group(row, photo_norm, depth)]
        g["bytes"] += row["size"]
        h = hashes.get(row["id"])
        best = _best_per_root(row, h, index) if h and h.sample else {}
        g["roots"].update(dict.fromkeys(best, 1))
        kind = "verified" if "verified" in best.values() else "sampled" if best else "none"
        g[kind] += 1
    return [{"kind": "year", "folder": name, "backup_roots": _roots_text(g["roots"]),
             "raws": g["sampled"] + g["verified"] + g["none"],
             "copied_sampled": g["sampled"], "copied_verified": g["verified"],
             "no_copy": g["none"], "bytes": g["bytes"],
             "how_decided": SAMPLED_HOW + note} for name, g in groups.items()]


def _backup_only_rows(back, hashes, root_of, lib_by_key, lib_sizes, unsampled_sizes,
                      note) -> list[dict]:
    """A backup raw is unsure when it, or a library raw of its size, has no sample hash."""
    groups = defaultdict(lambda: {"raws": 0, "bytes": 0, "unsure": 0})
    for row in back:
        h = hashes.get(row["id"])
        has_sample = bool(h and h.sample)
        if has_sample and any(_match(lh, h) for lh in lib_by_key.get((row["size"], h.sample), ())):
            continue
        root = root_of[row["scan_id"]]
        g = groups[(root, _top_folder(row, root))]
        if row["size"] in (unsampled_sizes if has_sample else lib_sizes):
            g["unsure"] += 1
            continue
        g["raws"] += 1
        g["bytes"] += row["size"]
    return [{"kind": "backup_only", "folder": top, "backup_roots": f"{root}={g['raws']}",
             "raws": g["raws"], "copied_sampled": 0, "copied_verified": 0,
             "no_copy": g["raws"], "bytes": g["bytes"],
             "how_decided": "matches no library raw by size and sample hash; no_copy here "
                            "means no library raw matches"
                            + (f"; {g['unsure']} more not compared (no sample hash on "
                               "either side)" if g["unsure"] else "") + note}
            for (root, top), g in groups.items() if g["raws"] or g["unsure"]]


def external_copy_rows(conn, *, photo_root: str, group_depth: int, library_roots: list[str],
                       backup_roots: list[str]) -> list[dict]:
    states = classify.root_states(conn, backup_roots)
    root_of = {s.scan_id: s.root for s in states if s.status == "complete"}
    unread = [f"{s.root} ({s.status.replace('_', ' ')})" for s in states
              if s.status != "complete"]
    note = f"; backup roots not read: {', '.join(unread)}" if unread else ""
    lib = _raws(conn, library_roots)
    back = _raws(conn, backup_roots)
    hashes = classify._valid_hashes(conn, lib + back)
    index = _index(back, hashes, root_of)
    lib_by_key = defaultdict(list)
    unsampled = set()
    for row in lib:
        h = hashes.get(row["id"])
        if h and h.sample:
            lib_by_key[(row["size"], h.sample)].append(h)
        else:
            unsampled.add(row["size"])
    rows = _year_rows(lib, hashes, index, sorted(root_of.values()), photo_root,
                      group_depth, note)
    rows += _backup_only_rows(back, hashes, root_of, lib_by_key, {r["size"] for r in lib}, unsampled,
                              note)
    return sorted(rows, key=lambda r: (r["kind"], r["folder"], r["backup_roots"]))


def verify(conn, folder: str, *, library_roots: list[str], backup_roots: list[str],
           opener=hashing.open_binary, stat=os.stat, now=store.utc_now) -> dict:
    counts = dict.fromkeys(("verified", "mismatch", "no_copy", "skipped_placeholder",
                            "changed", "unreadable"), 0)
    status_of: dict[int, str] = {}

    def full_hash(row) -> bool:
        if row["id"] not in status_of:
            status = hashing.hash_one(conn, row, want_full=True, opener=opener, stat=stat,
                                      now=now)
            status_of[row["id"]] = status
            if status in counts:
                counts[status] += 1
        return status_of[row["id"]] in ("hashed", "reused")

    prefix = paths.norm(folder).rstrip("/") + "/"
    lib = [r for r in _raws(conn, library_roots) if r["path_norm"].startswith(prefix)]
    if not lib:
        raise NoRawUnder(folder)
    lib = [r for r in lib if full_hash(r)]
    root_of = _complete_roots(conn, backup_roots)
    back = _raws(conn, backup_roots)
    index = _index(back, classify._valid_hashes(conn, lib + back), root_of)
    hashes = classify._valid_hashes(conn, lib)
    copies = {}
    for row in lib:
        h = hashes.get(row["id"])
        copies[row["id"]] = [c for _r, c, _h in _candidates(row, h, index)
                             if full_hash(c)] if h and h.sample else []
    hashes = classify._valid_hashes(conn, lib + back)
    for row in lib:
        fulls = [hashes[c["id"]].full for c in copies[row["id"]] if c["id"] in hashes]
        fulls = [f for f in fulls if f]
        if not _candidates(row, hashes[row["id"]], index):
            counts["no_copy"] += 1
            continue
        counts["verified"] += hashes[row["id"]].full in fulls
        counts["mismatch"] += sum(f != hashes[row["id"]].full for f in fulls)
    return counts
