"""Stage of the B2 mover: the rows of the approved list renamed into the staging folder (rules 36, 37, 39, 43)."""

import os
from typing import NamedTuple

from lrclean import paths
from lrclean.mover_check import FileFacts, RowCheck, check_row
from lrclean.mover_files import link_in_path, other_volume, staged_path
from lrclean.mover_journal import Journal
from lrclean.mover_list import ListRow
from lrclean.mover_seams import Ctx, check_write, guard, watch
from lrclean.mover_undo import RowResult


class StageOutcome(NamedTuple):
    results: list
    stalled: bool
    stopped: bool


class _Run(NamedTuple):
    ctx: Ctx
    journal: Journal
    apply: bool
    evidence: list
    tool: object  # the stat of the tool's folder: the volume the staging folder lies on
    tick: object


class _Refuse(Exception):
    """One file of a row cannot be staged: the row is held (and what moved is renamed back)."""

    def __init__(self, reason: str, file: str):
        super().__init__(reason)
        self.reason, self.file = reason, file


class _BackFailed(Exception):
    """A rename back failed: the run stops at once (rule 37)."""


class _Halt(Exception):
    def __init__(self, result: RowResult):
        super().__init__(result.reason)
        self.result = result


def _taken(ctx: Ctx, path: str) -> bool:
    try:
        ctx.seams.stat(paths.fs_path(path))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True  # cannot tell: fail closed
    return True


def _look(ctx: Ctx, path: str, reason: str):
    try:
        return ctx.seams.stat(paths.fs_path(path))
    except OSError as exc:
        raise _Refuse(reason, path) from exc


def _key(st) -> tuple:
    return st.st_size, st.st_mtime_ns, st.st_ino


def _original_kind(row: ListRow, file: str) -> str:
    """The kind of a file that is an original (a refusal or a shared sidecar names no archive copy)."""
    return row.kind if paths.norm(file) == paths.norm(row.source) else "sidecar"


def _held(index: int, row: ListRow, reason: str, file: str, kind: str, run: _Run) -> RowResult:
    if run.apply:
        run.journal.write("held", op="stage", row=index, source=row.source, kind=kind, reason=reason, file=file)
    return RowResult(index, row.source, "held", reason, (file,))


def _plan(check: RowCheck, ctx: Ctx) -> list[tuple[FileFacts, str]]:
    plan = [(fact, staged_path(ctx.base, ctx.batch, fact.path)) for fact in check.files]
    for fact, staged in plan:
        check_write(ctx.folders, fact.path, "source")
        check_write(ctx.folders, staged, "staging")
    return plan


def _refuse(fact: FileFacts, staged: str, run: _Run) -> None:
    if link_in_path(run.ctx.folders.photo_root, fact.path, run.ctx):
        raise _Refuse("parent_link", fact.path)
    if other_volume(_look(run.ctx, fact.path, "changed"), run.tool):
        raise _Refuse("other_volume", fact.path)
    if _taken(run.ctx, staged):
        raise _Refuse("staging_exists", fact.path)


def _stage_file(index: int, fact: FileFacts, staged: str, family: int, run: _Run, done: list) -> None:
    ctx = run.ctx
    before = _look(ctx, fact.path, "changed")
    if _key(before) != (fact.bytes, fact.mtime_ns, fact.file_index):
        raise _Refuse("changed", fact.path)
    guard(ctx, fact.path, "source")
    guard(ctx, staged, "staging")
    begin = {"op": "stage", "row": index, "kind": fact.kind, "original": fact.path, "staged": staged,
             "bytes": fact.bytes, "mtime_ns": fact.mtime_ns, "file_index": fact.file_index, "sha256": fact.sha256,
             "dests": [list(pair) for pair in fact.dests], "twin": fact.twin, "twin_sha256": fact.twin_sha256,
             "family": family}

    def move():
        try:
            ctx.seams.makedirs(paths.fs_path(os.path.dirname(staged)), exist_ok=True)
            guard(ctx, staged, "staging")  # a makedirs that returned after the `stalled` line goes no further
            ctx.seams.rename(paths.fs_path(fact.path), paths.fs_path(staged))
        except OSError as exc:
            raise _Refuse("rename_failed", fact.path) from exc
        done.append((fact, staged))
        try:
            after = ctx.seams.stat(paths.fs_path(staged))
        except OSError as exc:
            raise _Refuse("rename_failed", fact.path) from exc
        if _key(after) != _key(before):
            raise _Refuse("rename_failed", fact.path)
        return after

    run.journal.run_step(begin, move, lambda after: {
        "op": "stage", "row": index, "kind": fact.kind, "original": fact.path, "state": "staged",
        "bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "file_index": after.st_ino})


def _restore(index: int, fact: FileFacts, staged: str, run: _Run) -> None:
    ctx = run.ctx
    guard(ctx, fact.path, "source")
    guard(ctx, staged, "staging")
    facts = {"row": index, "original": fact.path, "bytes": fact.bytes, "mtime_ns": fact.mtime_ns,
             "file_index": fact.file_index}
    begin = {"op": "restore", "kind": fact.kind, "staged": staged, "sha256": fact.sha256, **facts}

    def back():
        try:
            ctx.seams.rename(paths.fs_path(staged), paths.fs_path(fact.path))
        except OSError as exc:
            raise _BackFailed from exc

    run.journal.run_step(begin, back, lambda _result: {"op": "restore", "state": "restored", **facts})


def _stage_row(index: int, row: ListRow, check: RowCheck, run: _Run) -> RowResult:
    plan, done = _plan(check, run.ctx), []
    try:
        for fact, staged in plan:
            _refuse(fact, staged, run)
        for fact, staged in (plan if run.apply else []):
            _stage_file(index, fact, staged, len(plan), run, done)
    except _Refuse as refuse:
        try:
            for fact, staged in reversed(done):
                _restore(index, fact, staged, run)
        except _BackFailed as failed:
            raise _Halt(_held(index, row, refuse.reason, refuse.file, _original_kind(row, refuse.file), run)) from failed
        return _held(index, row, refuse.reason, refuse.file, _original_kind(row, refuse.file), run)
    return RowResult(index, row.source, "staged" if run.apply else "would_stage", "",
                     tuple(fact.path for fact, _staged in plan))


def _stage_one(index: int, row: ListRow, shared: frozenset, run: _Run) -> RowResult:
    held = next((side for side in row.sidecars if paths.norm(side) in shared), "")
    if held:
        return _held(index, row, "shared_sidecar", held, "sidecar", run)
    check = check_row(row, index, evidence=run.evidence, ctx=run.ctx, tick=run.tick, hash_content=run.apply)
    if check.state == "held":
        return _held(index, row, check.reason, check.file, check.kind, run)
    return _stage_row(index, row, check, run)


def _refuse_existing(ctx: Ctx, journal: Journal) -> None:
    for path in (journal.path, os.path.join(ctx.base, "work", "staging", ctx.batch)):
        if _taken(ctx, path):
            raise ValueError(f"batch {ctx.batch} already exists: {path}")


def _tool_folder(ctx: Ctx):
    try:
        return _look(ctx, ctx.base, "changed")
    except _Refuse as refuse:
        raise ValueError(f"cannot read the tool's folder: {ctx.base}") from refuse


def run_stage(rows: list[ListRow], *, evidence: list, ctx: Ctx, journal: Journal, start: dict,
              stall_seconds: float, apply: bool, shared_paths: frozenset[str]) -> StageOutcome:
    """`shared_paths`: the `paths.norm` of every sidecar that two rows of the WHOLE approved list name (rule 36)."""
    _refuse_existing(ctx, journal)
    tool = _tool_folder(ctx)
    if apply:
        journal.write("start", op="stage", batch=ctx.batch, **start)
    results: list[RowResult] = []
    stopped: list[bool] = []

    def work(tick) -> None:
        run = _Run(ctx, journal, apply, evidence, tool, tick)
        for index, row in enumerate(rows):
            tick()
            try:
                results.append(_stage_one(index, row, shared_paths, run))
            except _Halt as halt:
                results.append(halt.result)
                stopped.append(True)
                return

    _outcome, stalled = watch(work, ctx=ctx, stall_seconds=stall_seconds,
                              journal_write=journal.write if apply else (lambda *_a, **_k: None))
    return StageOutcome(results, stalled, bool(stopped))
