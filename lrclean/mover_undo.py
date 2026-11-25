"""Undo of a B2 batch from its journal, and the row result every B2 command returns (rules 38, 39, 40)."""

import os
import stat
from typing import NamedTuple

from lrclean import paths
from lrclean.mover_files import link_in_path
from lrclean.mover_journal import Entry, Journal, place
from lrclean.mover_list import MAIN_KINDS
from lrclean.mover_seams import Ctx, WriteRefused, check_write, guard

GONE, UNREADABLE = "gone", "unreadable"
# an open entry's meaning once looked at: staged (to restore), purged, restored, or idle (nothing happened)
_OPEN = {("open_stage", "done"): "staged", ("open_stage", "not_done"): "idle",
         ("open_purge", "done"): "purged", ("open_purge", "not_done"): "staged",
         ("open_restore", "done"): "restored", ("open_restore", "not_done"): "staged"}


class RowResult(NamedTuple):
    index: int
    source: str
    state: str
    reason: str
    files: tuple


def _look(ctx: Ctx, path: str):
    try:
        return ctx.seams.stat(paths.fs_path(path))
    except (FileNotFoundError, NotADirectoryError):
        return GONE
    except OSError:
        return UNREADABLE


def _effective(entry: Entry, ctx: Ctx) -> str:
    if not entry.state.startswith("open_"):
        return entry.state
    looked = place(entry, ctx)
    return "unplaced" if looked == "unplaced" else _OPEN[(entry.state, looked)]


def _outside_refusal(entry: Entry, ctx: Ctx) -> str:
    try:
        check_write(ctx.folders, entry.original, "source")
        check_write(ctx.folders, entry.staged, "staging")
    except WriteRefused:
        return "outside_folders"
    return ""


def _link_refusal(entry: Entry, ctx: Ctx) -> str:
    return "parent_link" if link_in_path(ctx.folders.photo_root, entry.original, ctx) else ""


def _parent_refusal(entry: Entry, ctx: Ctx) -> str:
    parent = _look(ctx, os.path.dirname(entry.original))
    return "parent_missing" if isinstance(parent, str) or not stat.S_ISDIR(parent.st_mode) else ""


def _original_refusal(entry: Entry, ctx: Ctx) -> str:
    return "" if _look(ctx, entry.original) == GONE else "original_exists"


def _staged_refusal(entry: Entry, ctx: Ctx) -> str:
    staged = _look(ctx, entry.staged)
    if staged == GONE:
        return "staged_missing"
    if staged == UNREADABLE:
        return "staged_changed"
    same = (staged.st_size, staged.st_mtime_ns, staged.st_ino) == (entry.bytes, entry.mtime_ns, entry.file_index)
    return "" if same else "staged_changed"


def _file_refusal(entry: Entry, ctx: Ctx) -> str:
    return (_outside_refusal(entry, ctx) or _link_refusal(entry, ctx) or _parent_refusal(entry, ctx)
            or _original_refusal(entry, ctx) or _staged_refusal(entry, ctx))


def _row_refusal(looked: list[tuple[Entry, str]], ctx: Ctx) -> str:
    states = [state for _entry, state in looked]
    if "purged" in states:
        return "purged"
    if "unplaced" in states:
        return "unplaced"
    return next((why for entry, state in looked if state == "staged" and (why := _file_refusal(entry, ctx))), "")


def _restore(entry: Entry, ctx: Ctx, journal: Journal) -> None:
    guard(ctx, entry.original, "source")
    guard(ctx, entry.staged, "staging")
    facts = {"row": entry.row, "original": entry.original, "bytes": entry.bytes, "mtime_ns": entry.mtime_ns,
             "file_index": entry.file_index}
    begin = {"op": "restore", "kind": entry.kind, "staged": entry.staged, "sha256": entry.sha256, **facts}
    journal.run_step(begin, lambda: ctx.seams.rename(paths.fs_path(entry.staged), paths.fs_path(entry.original)),
                     lambda _result: {"op": "restore", "state": "restored", **facts})


def _undo_row(group: list[Entry], ctx: Ctx, journal: Journal, apply: bool) -> RowResult:
    looked = [(entry, _effective(entry, ctx)) for entry in group]
    todo = [entry for entry, state in reversed(looked) if state == "staged"]
    reason = _row_refusal(looked, ctx)
    state = "refused" if reason else ("restored" if apply else "would_restore") if todo else "skipped"
    if apply and state == "restored":
        for entry in todo:
            _restore(entry, ctx, journal)
    source = next((entry.original for entry in group if entry.kind in MAIN_KINDS), group[0].original)
    return RowResult(group[0].row, source, state, reason, tuple(entry.original for entry in todo))


def run_undo(entries: list[Entry], *, ctx: Ctx, journal: Journal, start: dict, apply: bool) -> list[RowResult]:
    if apply:
        journal.write("start", op="undo", batch=ctx.batch, **start)
    rows: dict[int, list[Entry]] = {}
    for entry in entries:
        rows.setdefault(entry.row, []).append(entry)
    return [_undo_row(group, ctx, journal, apply) for group in reversed(list(rows.values()))]
