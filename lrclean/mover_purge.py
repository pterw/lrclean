"""Purge of a B2 batch: the staged files of the journal, checked again, then deleted (rules 38, 39, 41, 43)."""

import contextlib
import hashlib
import os
import stat

from lrclean import paths
from lrclean.mover_check import Seen, moved, verify_copies
from lrclean.mover_journal import Entry, Journal, JournalError, place
from lrclean.mover_list import MAIN_KINDS
from lrclean.mover_seams import Ctx, allowed, guard, watch
from lrclean.mover_undo import RowResult

OPEN_IN_STAGING: frozenset[tuple[str, str]] = frozenset({("open_stage", "done"), ("open_restore", "not_done")})

# an entry that counts toward its family: its `end` says staged or purged, or a purge began and was looked at
_COUNTED = ("staged", "purged", "open_purge")


def _look(entry: Entry, ctx: Ctx) -> str:
    return place(entry, ctx) if entry.state.startswith("open_") else ""


def _check_families(entries: list[Entry]) -> None:
    """Rule 39: a row's first stage `begin` gave `family`, an `int` (not a bool) of at least the row's files."""
    files: dict[int, int] = {}
    for entry in entries:
        files[entry.row] = files.get(entry.row, 0) + 1
    for entry in entries:
        if type(entry.family) is not int or entry.family < files[entry.row]:  # 0 and a missing key are below 1
            raise JournalError(f"invalid family {entry.family!r} for row {entry.row}, whose journal holds "
                               f"{files[entry.row]} files: the journal's row has no valid file count (the journal "
                               "is damaged or was edited by hand); nothing was deleted, and undo still reads "
                               "the journal")


def look_all(entries: list[Entry], ctx: Ctx) -> list[tuple[Entry, str]]:
    """Each entry placed once: the digest, the dry run and `run_purge` all read these looks (rule 41)."""
    _check_families(entries)
    return [(entry, _look(entry, ctx)) for entry in entries]


def _staged_now(entry: Entry, look: str) -> bool:
    return entry.state == "staged" or (entry.state == "open_purge" and look == "not_done")


def purge_set(looked: list[tuple[Entry, str]]) -> list[Entry]:
    return [entry for entry, look in looked if _staged_now(entry, look)]


def purge_sha256(looked: list[tuple[Entry, str]]) -> str:
    lines = sorted(f"{entry.staged}\t{entry.sha256}\n" for entry in purge_set(looked))
    return hashlib.sha256("".join(lines).encode("utf-8", "surrogatepass")).hexdigest()


def _staged_reason(entry: Entry, ctx: Ctx) -> str:
    try:
        st = ctx.seams.stat(paths.fs_path(entry.staged))
    except OSError:
        return "staged_changed"
    same = (st.st_size, st.st_mtime_ns, st.st_ino) == (entry.bytes, entry.mtime_ns, entry.file_index)
    return "" if same and entry.sha256 else "staged_changed"


def _entry_reason(entry: Entry, roots, ctx: Ctx, tick, hash_content: bool, seen: list) -> tuple[str, str]:
    """(reason, the file that failed): the staged file, an archive copy or the twin. `seen` gets the files read."""
    reason = _staged_reason(entry, ctx)
    if reason or not hash_content:
        return reason, entry.staged
    dests = [dest for dest, _sha in entry.dests]
    return verify_copies(entry.staged, entry.bytes, dests, entry.twin, expect=entry.sha256, roots=roots,
                         ctx=ctx, tick=tick, seen=seen)


def _judge(group, todo, roots, ctx: Ctx, tick, hash_content: bool) -> tuple[str, Entry | None, str]:
    """The reason that holds the row, the entry it is held on and the file that failed; ("", None, "") when
    the row may be purged."""
    unplaced = next((entry for entry, look in group if look == "unplaced"), None)
    if unplaced:
        return "unplaced", unplaced, unplaced.original
    if sum(entry.state in _COUNTED for entry, _ in group) < group[0][0].family:
        return "family_incomplete", group[0][0], group[0][0].original
    outside = next((entry for entry in todo if not allowed(ctx.folders, entry.staged, "staging")), None)
    if outside:  # before any stat or read of a path the journal supplied
        return "outside_folders", outside, outside.original
    seen: list[tuple[Entry, Seen]] = []
    for entry in todo:
        read: list[Seen] = []
        reason, failed = _entry_reason(entry, roots, ctx, tick, hash_content, read)
        if reason:
            return reason, entry, failed
        seen += [(entry, item) for item in read]
    # right before the first removal: every file the check read must still be the file it hashed
    for entry, item in seen:
        if moved(item, ctx):
            return item.reason, entry, item.path
    return "", None, ""


def _remove(entry: Entry, ctx: Ctx, journal: Journal) -> None:
    guard(ctx, entry.staged, "staging")
    facts = {"row": entry.row, "original": entry.original, "bytes": entry.bytes, "mtime_ns": entry.mtime_ns,
             "file_index": entry.file_index}
    begin = {"op": "purge", "kind": entry.kind, "staged": entry.staged, "sha256": entry.sha256, **facts}
    journal.run_step(begin, lambda: ctx.seams.remove(paths.fs_path(entry.staged)),
                     lambda _result: {"op": "purge", "state": "purged", **facts})


def _purge_row(group, roots, ctx: Ctx, journal: Journal, tick, apply: bool) -> RowResult:
    first = group[0][0]
    source = next((entry.original for entry, _ in group if entry.kind in MAIN_KINDS), first.original)
    todo = sorted((entry for entry, look in group if _staged_now(entry, look)), key=lambda e: e.kind in MAIN_KINDS)
    reason, culprit, failed = _judge(group, todo, roots, ctx, tick, apply)
    files = tuple(entry.original for entry in todo)
    if reason:
        if apply:
            journal.write("held", op="purge", row=first.row, source=source, kind=culprit.kind, reason=reason,
                          file=failed)
        return RowResult(first.row, source, "held", reason, (failed,))
    if apply:
        for entry in todo:
            _remove(entry, ctx, journal)
    return RowResult(first.row, source, "purged" if apply else "would_purge", "", files)


def _prune(folder: str, ctx: Ctx) -> None:
    """Remove the empty folders below `folder`, bottom up; a link is never entered."""
    for name in ctx.seams.listdir(paths.fs_path(folder)):
        child = os.path.join(folder, name)
        if ctx.seams.is_link(paths.fs_path(child)) or not stat.S_ISDIR(ctx.seams.stat(paths.fs_path(child)).st_mode):
            continue
        _prune(child, ctx)
        if not ctx.seams.listdir(paths.fs_path(child)):
            guard(ctx, child, "staging")
            ctx.seams.rmdir(paths.fs_path(child))


def _idle() -> None:
    pass


def _rows(looked: list[tuple[Entry, str]]) -> list[list[tuple[Entry, str]]]:
    rows: dict[int, list[tuple[Entry, str]]] = {}
    for entry, look in looked:
        rows.setdefault(entry.row, []).append((entry, look))
    return [group for group in rows.values()
            if any(_staged_now(entry, look) or look == "unplaced"
                   or (entry.state, look) in OPEN_IN_STAGING for entry, look in group)]


def run_purge(looked: list[tuple[Entry, str]], *, roots, ctx: Ctx, journal: Journal, start: dict,
              stall_seconds: float, apply: bool) -> list[RowResult]:
    groups = _rows(looked)
    results: list[RowResult] = []

    def work(tick):
        for group in groups:
            results.append(_purge_row(group, roots, ctx, journal, tick, apply))

    if not apply:
        work(_idle)
        return results
    journal.write("start", op="purge", batch=ctx.batch, **start)
    stalled = watch(work, ctx=ctx, stall_seconds=stall_seconds, journal_write=journal.write)[1]
    if not stalled:
        with contextlib.suppress(OSError):
            _prune(os.path.join(ctx.base, "work", "staging", ctx.batch), ctx)
    return list(results)
