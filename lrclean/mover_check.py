"""The row check of the B2 mover (rules 35, 36): every file of a removal row against its archive copies."""

import os
from itertools import combinations
from typing import NamedTuple

from lrclean import paths
from lrclean.backup import ticked
from lrclean.backup_plan import is_inside
from lrclean.hashing import full_hash
from lrclean.mover_files import distinct, other_volume, resolve
from lrclean.mover_list import MAIN_KINDS, ListRow
from lrclean.mover_seams import Ctx
from lrclean.reject_archive import CHANGED_SINCE_BACKUP, OK, RootEvidence, archive_state


class FileFacts(NamedTuple):
    kind: str
    path: str
    bytes: int
    mtime_ns: int
    file_index: int
    sha256: str
    dests: tuple[tuple[str, str], ...]
    twin: str
    twin_sha256: str


class RowCheck(NamedTuple):
    index: int
    row: ListRow
    state: str
    reason: str
    file: str
    files: tuple[FileFacts, ...]
    kind: str  # the kind of the family member whose test failed; "" for an ok row


class Seen(NamedTuple):
    """A file the content check read, the reason that holds a row when it changes, and its stat before the read."""
    path: str
    reason: str
    st: object


class _Hold(Exception):
    def __init__(self, reason: str, file: str, kind: str):
        super().__init__(reason)
        self.reason, self.file, self.kind = reason, file, kind


def _stat(ctx: Ctx, path: str):
    try:
        return ctx.seams.stat(paths.fs_path(path))
    except OSError:
        return None


def _placeholder(ctx: Ctx, path: str, st) -> bool:
    attrs = ctx.seams.attrs_of(path, st) if ctx.seams.attrs_of else getattr(st, "st_file_attributes", 0)
    return paths.is_placeholder(attrs)


def _sound_copy(ctx: Ctx, path: str, size: int, digest, sha: str):
    """The stat of a file of that size, no placeholder and that content, else None."""
    st = _stat(ctx, path)
    sound = st is not None and st.st_size == size and not _placeholder(ctx, path, st) and digest(path) == sha
    return st if sound else None


def _identity(st) -> tuple:
    return st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino


def moved(item: Seen, ctx: Ctx) -> bool:
    """True when the file is gone or its stat differs from the one taken before it was read."""
    now = _stat(ctx, item.path)
    return now is None or _identity(now) != _identity(item.st)


def _verify(path, size, dests, twin, *, expect, roots, ctx, tick, hash_content, seen) -> tuple[str, str]:
    """(reason, the file that failed) or ("", sha): the archive copy, twin or staged file at fault.
    `seen` receives each file whose content was read, with its stat from before the read."""
    changed = "staged_changed" if expect else "changed"
    st = _stat(ctx, path)
    if st is None or st.st_size != size or _placeholder(ctx, path, st):
        return changed, path
    seen.append(Seen(path, changed, st))
    opener = ticked(ctx.seams.opener, tick)

    def digest(target):
        return full_hash(target, opener) if hash_content else ""

    sha = digest(path)
    if expect and sha != expect:
        return "staged_changed", path
    stray = next((d for d, r in zip(dests, roots) if not is_inside(d, r, below_only=True)), "")
    if len(dests) != len(roots) or stray:
        return "archive_mismatch", stray or path
    copies = []
    for dest in dests:
        copy = _sound_copy(ctx, dest, size, digest, sha)
        if copy is None:
            return "archive_mismatch", dest
        copies.append(copy)
        seen.append(Seen(dest, "archive_mismatch", copy))
    twin_st = _sound_copy(ctx, twin, size, digest, sha) if twin else None
    if twin and twin_st is None:
        return "twin_mismatch", twin
    if twin:
        seen.append(Seen(twin, "twin_mismatch", twin_st))
    group = [st, *copies]
    clash = next((b for (_, a_st), (b, b_st) in combinations(zip([path, *dests], group), 2)
                  if not other_volume(a_st, b_st)), "")  # also tells one file from two
    if clash:
        return "archive_mismatch", clash
    if twin and not distinct([*group, twin_st]):
        return "twin_mismatch", twin
    return "", sha


def verify_copies(path: str, size: int, dests, twin: str, *, expect: str, roots, ctx: Ctx, tick,
                  seen: list) -> tuple[str, str]:
    return _verify(path, size, dests, twin, expect=expect, roots=roots, ctx=ctx, tick=tick, hash_content=True,
                   seen=seen)


def _locate(path: str, ctx: Ctx, kind: str) -> str:
    folder, name = os.path.split(path)
    try:
        names = resolve(folder, name, ctx) if folder else []
    except OSError:
        names = []
    if len(names) > 1:
        raise _Hold("name_ambiguous", path, kind)
    if not names:
        raise _Hold("missing", path, kind)
    return os.path.join(folder, names[0])


def _found(path: str, ctx: Ctx, kind: str):
    on_disk = _locate(path, ctx, kind)
    st = _stat(ctx, on_disk)
    if st is None:
        raise _Hold("missing", on_disk, kind)
    if _placeholder(ctx, on_disk, st):
        raise _Hold("placeholder", on_disk, kind)
    return on_disk, st


def _family(row: ListRow, ctx: Ctx) -> list[tuple[str, str, object]]:
    """Name and stat tests of every file, the main file first: (kind, path as on disk, stat)."""
    main, st = _found(row.source, ctx, row.kind)
    if (st.st_size, st.st_mtime_ns) != (row.bytes, row.mtime_ns):
        raise _Hold("changed", main, row.kind)
    sides = [_found(side, ctx, "sidecar") for side in row.sidecars]
    if sum(side_st.st_size for _, side_st in sides) != row.sidecar_bytes:
        raise _Hold("changed", main, row.kind)
    return [(row.kind, main, st), *(("sidecar", path, side_st) for path, side_st in sides)]


def _keys(names) -> list[str]:
    return [paths.norm(name) for name in names]


def _archived(kind, path, st, row, evidence, ctx) -> tuple[str, ...]:
    """The destinations of a file by the evidence, no content read."""
    states = [archive_state(ev, path, st, stat=ctx.seams.stat, attrs_of=ctx.seams.attrs_of) for ev in evidence]
    dests = tuple(dest for _, dest in states)
    if kind == "sidecar" and any(state == CHANGED_SINCE_BACKUP for state, _ in states):
        raise _Hold("changed", path, kind)
    listed = row.dests if kind in MAIN_KINDS else dests
    if any(state != OK for state, _ in states) or _keys(dests) != _keys(listed):
        raise _Hold("no_archive_copy", _unarchived(states, listed, path), kind)
    return dests


def _unarchived(states, listed, path: str) -> str:
    """The first destination without a sound copy or off the list, else the file itself."""
    keys = _keys(listed)
    for i, (state, dest) in enumerate(states):
        if state != OK or i >= len(keys) or paths.norm(dest) != keys[i]:
            return dest or (listed[i] if i < len(listed) else "") or path
    return listed[len(states)] if len(listed) > len(states) else path


def _facts(kind, path, st, dests, row, evidence, ctx, tick, hash_content) -> FileFacts:
    twin = row.twin if kind in MAIN_KINDS else ""
    reason, sha = _verify(path, st.st_size, dests, twin, expect="", roots=[ev.root for ev in evidence],
                          ctx=ctx, tick=tick, hash_content=hash_content, seen=[])
    if reason:
        raise _Hold(reason, sha, kind)  # on a failure the second value is the file at fault
    return FileFacts(kind, path, st.st_size, st.st_mtime_ns, st.st_ino, sha, tuple((d, sha) for d in dests),
                     twin, sha if twin else "")


def check_row(row: ListRow, index: int, *, evidence: list[RootEvidence], ctx: Ctx, tick,
              hash_content: bool = True) -> RowCheck:
    try:
        family = _family(row, ctx)
        archived = [_archived(kind, path, st, row, evidence, ctx) for kind, path, st in family]
        files = tuple(_facts(kind, path, st, dests, row, evidence, ctx, tick, hash_content)
                      for (kind, path, st), dests in zip(family, archived))
    except _Hold as hold:
        return RowCheck(index, row, "held", hold.reason, hold.file, (), hold.kind)
    return RowCheck(index, row, "ok", "", "", files, "")
