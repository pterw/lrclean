"""The refusals of the whole run and the two approvals of the B2 mover (rules 32 to 34)."""

import os
import re
import stat as stat_module
from typing import NamedTuple

from lrclean import backup_plan, catalogs, mover_list, paths, store

_SCAN_LINE = re.compile(r"^- (\w+) scan (\d+) of ", re.MULTILINE)
_LIST_LINE = re.compile(r"^## Removal list\r?\n\r?\n- SHA-256: ([0-9a-f]{64})\r?$", re.MULTILINE)
_RERUN = "run catalogs and {command} again: the list is older than the latest scans and must not be acted on"


class Refusal(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class RunFacts(NamedTuple):
    """One message per refusal of rules 33 and 34; an empty one means that test passed."""
    config: str
    lightroom: str
    lock: str
    freshness: str
    coverage: str


def list_approved(computed: str, approved: str) -> Refusal | None:
    return None if computed.lower() == (approved or "").lower() else Refusal(
        2, f"the SHA-256 of the list is {computed}, not the approved one: compare it and approve this one")


def purge_approved(computed: str, approved: str) -> Refusal | None:
    return None if computed.lower() == (approved or "").lower() else Refusal(
        2, f"the SHA-256 of the purge set is {computed}, not the approved one: compare it and approve this one")


def _is_dir(path: str, stat) -> bool:
    try:
        return stat_module.S_ISDIR(stat(paths.fs_path(path)).st_mode)
    except OSError:
        return False


def _source_problem(sources, *, photo_root: str, keep_out: list[str]) -> str:
    for source in sources:
        if not backup_plan.is_inside(source, photo_root, below_only=True):
            return f"{source} lies outside photo_root"
        for root in keep_out:
            if backup_plan.is_inside(source, root):
                return f"{source} lies inside {root}"
    return ""


def _config_problem(cfg: dict, base: str, stat, sources) -> str:
    roots, photo_root = cfg.get("archive_roots") or [], cfg.get("photo_root") or ""
    live, excluded = cfg.get("live_catalog"), {paths.norm(p) for p in cfg.get("excluded_catalogs") or []}
    gone = next((root for root in roots if not _is_dir(root, stat)), "")
    checks = [
        ("archive_roots names fewer than two roots", len(roots) < 2),
        (f"the archive root {gone} is not a connected folder", bool(gone)),
        (f"photo_root {photo_root} is missing", not _is_dir(photo_root, stat)),
        ("the live catalog is named in excluded_catalogs", bool(live) and paths.norm(live) in excluded),
    ]
    found = next((text for text, bad in checks if bad), "")
    return found or _source_problem(sources, photo_root=photo_root,
                                    keep_out=[*roots, *(cfg.get("backup_roots") or []), base])


def _lightroom(processes) -> str:
    try:
        names = processes()
    except RuntimeError as exc:
        return f"the process list cannot be read, so Lightroom may run: {exc}"
    running = next((name for name in names if name.lower().startswith("lightroom")), "")
    return f"Lightroom is running ({running}): close it and run the command again" if running else ""


def _latest_catalogs(conn) -> list:
    ids = [s["id"] for s in store.latest_scans(conn, "catalogs")]
    sql = f'SELECT * FROM "catalog" WHERE scan_id IN ({",".join("?" * len(ids))}) ORDER BY id'
    return conn.execute(sql, ids).fetchall() if ids else []


def _has_lock(source: str, stat) -> bool:
    try:
        stat(paths.fs_path(source + ".lock"))
    except FileNotFoundError:
        return False
    except OSError:
        pass  # unreadable: fail closed, as if the lock were there
    return True


def _lock(cats, stat, excluded) -> str:
    """Name every catalog with a lock; an excluded catalog is never read, so its lock says nothing (RC-124)."""
    locked = [c["source_path"] for c in cats if paths.norm(c["source_path"]) not in excluded
              and _has_lock(c["source_path"], stat)]
    return (f"a .lock lies beside {', '.join(locked)}: close Lightroom; a stale lock is cleared "
            "by you in Lightroom, never by this tool") if locked else ""


def link_refusal(base: str, is_link, listdir) -> Refusal | None:
    """Exit 2 when the tool's folder or one of its own folders, `work/catalogs` and each snapshot folder in it
    included, is a link (RC-103, RC-155): a write would leave the tree."""
    work = os.path.join(base, "work")
    snapshots = os.path.join(work, "catalogs")
    folders = (base, work, snapshots, os.path.join(work, "staging"), os.path.join(work, "journal"),
               os.path.join(base, "out"))
    entries = []
    linked = next((folder for folder in folders if is_link(folder)), "")
    if not linked:
        try:
            entries = [os.path.join(snapshots, name) for name in listdir(snapshots)]
        except (FileNotFoundError, NotADirectoryError):
            entries = []
        except OSError as exc:
            return Refusal(2, f"{snapshots} cannot be listed ({exc}): its snapshot folders cannot be checked "
                              "for links")
        linked = next((folder for folder in entries if is_link(folder)), "")
    if not linked:
        return None
    try:
        where = f"points to {os.readlink(linked)}"
    except (OSError, ValueError):
        where = "points somewhere that cannot be read"
    if linked in entries:
        return Refusal(2, f"{linked}, a catalog's snapshot folder, is a link (junction or symbolic link) "
                          f"that {where}: it must be a real folder, so remove the link")
    return Refusal(2, f"{linked} is a link (junction or symbolic link) that {where}: "
                      "the tool's folders must be real folders")


def _summary_text(ctx, listing) -> str | None:
    try:
        with ctx.seams.opener(os.path.join(ctx.base, "out", listing.summary)) as handle:
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _freshness(cfg: dict, ctx, conn, cats, stale, list_sha256, listing) -> str:
    text = _summary_text(ctx, listing)
    named = None if text is None else {(kind, int(number)) for kind, number in _SCAN_LINE.findall(text)}
    listed = _LIST_LINE.search(text or "")
    latest = {("catalogs", s["id"]) for s in store.latest_scans(conn, "catalogs")}
    live, old = cfg.get("live_catalog"), cfg.get("old_states") or []
    changed = next((c["source_path"] for c in cats
                    if c["role"] != catalogs.role_of(c["source_path"], live, old)), "")
    problems = [
        ("there is no catalogs scan", not latest),
        (f"out/{listing.summary} is missing or unreadable", named is None),
        ("the scans the summary names are not the latest catalogs scans", named != latest),
        (f"the summary does not name the SHA-256 of this list, so the list is not the one {listing.command} wrote",
         not (list_sha256 and listed and listed.group(1) == list_sha256)),
        (f"a catalog changed since its snapshot: {stale[0] if stale else ''}", bool(stale)),
        (f"the role of {changed} is not the one stored", bool(changed)),
    ]
    found = next((text for text, bad in problems if bad), "")
    return f"{_RERUN.format(command=listing.command)} ({found})" if found else ""


def gather(op: str, *, cfg: dict, ctx, conn, processes, stale, coverage, sources, list_sha256,
           listing=mover_list.REJECTS) -> RunFacts:
    """Read the facts of rules 33 and 34; `purge` does not read the freshness evidence (rule 33) and gets None."""
    cats = _latest_catalogs(conn)
    stat = ctx.seams.stat
    fresh = "" if op == "purge" else _freshness(cfg, ctx, conn, cats, stale, list_sha256, listing)
    excluded = {paths.norm(p) for p in cfg.get("excluded_catalogs") or []}
    limits = "; ".join(coverage.limiting) or "unknown"
    return RunFacts(
        _config_problem(cfg, ctx.base, stat, sources), _lightroom(processes), _lock(cats, stat, excluded), fresh,
        "" if coverage.complete else f"run catalogs again: catalog coverage is incomplete ({limits})")


def _refusal(code: int, message: str) -> Refusal | None:
    return Refusal(code, message) if message else None


def first_refusal(op: str, facts: RunFacts, approval: Refusal | None) -> Refusal | None:
    config = _refusal(2, facts.config)
    lightroom, lock = _refusal(3, facts.lightroom), _refusal(3, facts.lock)
    if op == "stage":
        order = [approval, config, lightroom, lock, _refusal(4, facts.freshness), _refusal(4, facts.coverage)]
    elif op == "purge":
        order = [config, approval, lightroom, lock, _refusal(4, facts.coverage)]
    else:
        raise ValueError(f"unknown operation: {op}")
    return next((refusal for refusal in order if refusal), None)
