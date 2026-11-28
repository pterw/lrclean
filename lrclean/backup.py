"""What decides and writes in the backup (phase B1, rules 16 to 23).

Rule 16 (`check_manifest`) and rule 20 (`source_changed`) only read; each
decision is one expression on one line.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Sequence
from typing import NamedTuple

from lrclean import backup_plan, hashing, paths, store
from lrclean.backup_plan import ManifestError, Row

VERIFIED = "verified"
ALREADY_THERE = "already_there"
CONFLICT = "conflict"
CHANGED = "changed"
FAILED = "failed"
SKIPPED_PLACEHOLDER = "skipped_placeholder"
DEST_PLACEHOLDER = "dest_placeholder"
PART_SUFFIX = ".lrclean-part"
BLOCK = 1024 * 1024  # the block size `hashing.full_hash` reads


def check_manifest(
    rows: Sequence[Row],
    *,
    archive_roots: list[str],
    library_roots: list[str],
    free_bytes=backup_plan.free_space,
    exists=os.path.exists,
) -> int:
    n = backup_plan.archive_index(rows, archive_roots)
    seen: set[str] = set()
    needed = 0
    for row in rows:
        if any(backup_plan.is_inside(row.dest, library) for library in library_roots):
            raise ManifestError(f"dest is in a library root: {row.dest}")
        name = os.path.basename(os.path.normpath(row.dest))
        # Windows drops a final dot or space and reads a colon as a stream: each can open the part file.
        if name.endswith((".", " ")) or ":" in name or name.casefold().endswith(PART_SUFFIX):
            raise ManifestError(f"dest has the name of a part file: {row.dest} (source {row.source})")
        if row.bytes < 0:
            raise ManifestError(f"negative size for {row.dest}")
        key = paths.norm(os.path.normpath(row.dest))
        if key in seen:
            raise ManifestError(f"two rows share the dest {row.dest}")
        seen.add(key)
        if row.action == "copy" and not exists(paths.fs_path(row.dest)):
            needed += row.bytes
    if needed > free_bytes(archive_roots[n - 1]):
        raise ManifestError(f"the manifest needs {needed} bytes, more than the free space")
    return n


def source_changed(row: Row, *, stat=os.stat, attrs_of=None) -> bool:
    try:
        st = stat(paths.fs_path(row.source))
    except OSError:
        return True
    attrs = attrs_of(row.source, st) if attrs_of else getattr(st, "st_file_attributes", 0)
    return paths.is_placeholder(attrs) or (st.st_size, st.st_mtime_ns) != (row.bytes, row.mtime_ns)


def _dest_placeholder(row: Row, st, attrs_of) -> bool:
    return paths.is_placeholder(attrs_of(row.dest, st) if attrs_of else getattr(st, "st_file_attributes", 0))


def open_part(path: str):
    return open(paths.fs_path(path), "xb")


class _Ticked:
    def __init__(self, handle, tick):
        self._handle, self._tick = handle, tick

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def read(self, *args):
        data = self._handle.read(*args)
        self._tick()
        return data

    def __getattr__(self, name):
        return getattr(self._handle, name)


def ticked(opener, tick):
    def open_(path):
        handle = opener(path)
        try:
            tick()
        except BaseException:
            handle.close()
            raise
        return _Ticked(handle, tick)

    return open_


def _remove_part(part: str) -> None:
    if os.path.exists(paths.fs_path(part)):
        os.remove(paths.fs_path(part))


def copy_row(
    row: Row,
    *,
    tick=lambda: None,
    stat=os.stat,
    attrs_of=None,
    opener=hashing.open_binary,
    writer=open_part,
    rename=os.rename,
    utime=os.utime,
) -> tuple[str, str]:
    if row.action == "skip_placeholder":
        return SKIPPED_PLACEHOLDER, ""
    part = row.dest + PART_SUFFIX
    try:
        return _copy(row, part, tick, stat, attrs_of, ticked(opener, tick), writer, rename, utime)
    except BaseException as exc:
        try:
            _remove_part(part)
        except OSError:
            pass  # the part may stay; the next run removes it
        if isinstance(exc, OSError):
            return FAILED, ""
        raise


def _copy(row, part, tick, stat, attrs_of, open_, writer, rename, utime) -> tuple[str, str]:
    if source_changed(row, stat=stat, attrs_of=attrs_of):
        return CHANGED, ""
    if os.path.exists(paths.fs_path(row.dest)):
        if _dest_placeholder(row, stat(paths.fs_path(row.dest)), attrs_of):
            return DEST_PLACEHOLDER, ""
        src_sha = hashing.full_hash(row.source, open_)
        dest_sha = hashing.full_hash(row.dest, open_)
        return (ALREADY_THERE if src_sha == dest_sha else CONFLICT), src_sha
    os.makedirs(paths.fs_path(os.path.dirname(row.dest)), exist_ok=True)
    digest = hashlib.sha256()
    with open_(row.source) as source, writer(part) as out:
        for block in iter(lambda: source.read(BLOCK), b""):
            out.write(block)
            digest.update(block)
        out.flush()
        tick()
        os.fsync(out.fileno())
        tick()
    sha = digest.hexdigest()
    if hashing.full_hash(part, open_) != sha:
        _remove_part(part)
        return FAILED, ""
    if source_changed(row, stat=stat, attrs_of=attrs_of):
        _remove_part(part)
        return CHANGED, ""
    rename(paths.fs_path(part), paths.fs_path(row.dest))
    utime(paths.fs_path(row.dest), ns=(row.mtime_ns, row.mtime_ns))
    return VERIFIED, sha


class Result(NamedTuple):
    row: int
    source: str
    dest: str
    state: str
    sha256: str


class RunOutcome(NamedTuple):
    results: list[Result]
    stalled: bool


def exit_code(outcome: RunOutcome) -> int:
    return 5 if outcome.stalled else 6 if any(r.state in (CONFLICT, CHANGED, FAILED, DEST_PLACEHOLDER) for r in outcome.results) else 0


def journal_name(n: int, started: str) -> str:
    return f"backup-{n}-{started.replace('-', '').replace(':', '')}.jsonl"


def _verified_end(line: str):
    """((source, dest), (dest bytes, dest mtime_ns, sha256, source bytes, source mtime_ns)) of a verified `end` line."""
    try:
        entry = json.loads(line)
        if entry["event"] != "end" or entry["state"] != VERIFIED:
            return None
        key = (paths.norm(entry["source"]), paths.norm(entry["dest"]))
        sha = entry["sha256"]
        if not isinstance(sha, str):
            return None
        dest = (int(entry["dest_bytes"]), int(entry["dest_mtime_ns"]))
        return key, (*dest, sha, int(entry["source_bytes"]), int(entry["source_mtime_ns"]))
    except (ValueError, KeyError, TypeError):
        return None


def read_verified(journal_dir: str, n: int) -> dict[tuple[str, str], tuple[int, int, str, int, int]]:
    try:
        names = sorted(os.listdir(paths.fs_path(journal_dir)))
    except OSError:
        return {}
    found: dict[tuple[str, str], tuple[int, int, str, int, int]] = {}
    for name in names:
        if not (name.startswith(f"backup-{n}-") and name.endswith(".jsonl")):
            continue
        with open(paths.fs_path(os.path.join(journal_dir, name)), encoding="utf-8", errors="replace") as handle:
            for line in handle:
                hit = _verified_end(line)
                if hit:
                    found[hit[0]] = hit[1]
    return found


class Journal:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self._checked = False

    def write(self, event: str, **fields) -> None:
        line = json.dumps({"event": event, "at": store.utc_now(), **fields})
        with self.lock, open(paths.fs_path(self.path), "a+b") as handle:
            if not self._checked:
                self._checked = True
                handle.seek(0, os.SEEK_END)
                if handle.tell():
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        line = "\n" + line
            handle.write((line + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())


def _dest_stat(dest: str) -> tuple[int, int] | None:
    try:
        st = os.stat(paths.fs_path(dest))
    except OSError:
        return None  # never equals a recorded pair, so a destination that is gone is not skipped
    return st.st_size, st.st_mtime_ns


def _journal_sha(row: Row, verified, stat, attrs_of) -> str:
    """The journal's SHA-256 when it vouches for the destination as it is now, else ''."""
    recorded = verified.get((paths.norm(row.source), paths.norm(row.dest)))
    if not recorded:
        return ""
    try:
        st = stat(paths.fs_path(row.dest))
    except OSError:
        return ""  # a destination that cannot be stat'ed is not skipped
    if recorded[:2] == (st.st_size, st.st_mtime_ns) and not _dest_placeholder(row, st, attrs_of) and not source_changed(row, stat=stat, attrs_of=attrs_of):
        if recorded[3:] == (row.bytes, row.mtime_ns):  # the source the journal verified
            return recorded[2]
    return ""


def _run_copy(row: Row, **copy_args) -> tuple[str, str]:
    try:
        return copy_row(row, **copy_args)
    except Exception:
        return FAILED, ""


def run_manifest(
    rows: Sequence[Row],
    *,
    journal_dir: str,
    n: int,
    started: str,
    stall_seconds: float = 120,
    clock=time.monotonic,
    poll_seconds: float = 0.05,
    abort=os._exit,
    now=store.utc_now,
    stat=os.stat,
    attrs_of=None,
    opener=hashing.open_binary,
    writer=open_part,
    rename=os.rename
) -> RunOutcome:
    os.makedirs(paths.fs_path(journal_dir), exist_ok=True)
    if not rows:
        return RunOutcome([], False)
    verified = read_verified(journal_dir, n)
    journal = Journal(os.path.join(journal_dir, journal_name(n, started)))
    results: list[Result] = []
    last = [clock()]
    current = [(0, rows[0])]  # the row in hand, replaced whole
    finished = threading.Event()
    errors: list[BaseException] = []

    def tick():
        last[0] = clock()

    def work_row(i, row):
        tick()
        current[0] = (i, row)
        if row.action != "copy":
            state, sha = copy_row(row)
            return Result(i, row.source, row.dest, state, sha)
        fields = {"row": i, "source": row.source, "dest": row.dest}
        try:
            _remove_part(row.dest + PART_SUFFIX)
            skip_sha = _journal_sha(row, verified, stat, attrs_of)
        except Exception:
            skip_sha = None  # the row fails below, with its start and end lines
        if skip_sha:
            return Result(i, row.source, row.dest, VERIFIED, skip_sha)
        journal.write("start", at=now(), **fields)
        if skip_sha is None:
            state, sha = FAILED, ""
        else:
            state, sha = _run_copy(
                row, tick=tick, stat=stat, attrs_of=attrs_of, opener=opener, writer=writer, rename=rename
            )
        sha = sha if state in (VERIFIED, ALREADY_THERE) else ""
        size, mtime = _dest_stat(row.dest) or (0, 0)
        facts = {"dest_bytes": size, "dest_mtime_ns": mtime, "source_bytes": row.bytes, "source_mtime_ns": row.mtime_ns}
        journal.write("end", at=now(), state=state, sha256=sha, **facts, **fields)
        return Result(i, row.source, row.dest, state, sha)

    def work():
        try:
            for i, row in enumerate(rows):
                results.append(work_row(i, row))
        except BaseException as exc:  # the journal failed: the caller re-raises it
            errors.append(exc)
        finally:
            finished.set()

    threading.Thread(target=work, daemon=True).start()
    while not finished.wait(poll_seconds):
        if clock() - last[0] > stall_seconds:
            with journal.lock:
                if clock() - last[0] <= stall_seconds:
                    continue
                i, row = current[0]
                seconds = clock() - last[0]
                journal.write("stalled", at=now(), row=i, source=row.source, dest=row.dest, seconds=seconds)
                print(f"backup stalled on row {i} for {seconds:.0f} s: {row.source}", file=sys.stderr)
                abort(5)
                return RunOutcome(list(results), True)
    if errors:
        raise errors[0]
    return RunOutcome(results, False)


def dry_run(
    rows: Sequence[Row],
    *,
    journal_dir: str,
    n: int,
    stat=os.stat,
    attrs_of=None,
    exists=os.path.exists,
) -> list[tuple[int, str]]:
    verified = read_verified(journal_dir, n)
    return [(i, _verdict(row, verified, stat, attrs_of, exists)) for i, row in enumerate(rows)]


def _verdict(row: Row, verified, stat, attrs_of, exists) -> str:
    if row.action == "skip_placeholder":
        return "skip_placeholder"
    if source_changed(row, stat=stat, attrs_of=attrs_of):
        return "changed"
    if not exists(paths.fs_path(row.dest)):
        return "copy"
    recorded = verified.get((paths.norm(row.source), paths.norm(row.dest)))
    try:
        st = stat(paths.fs_path(row.dest))
    except OSError:
        return "dest_exists"
    same = recorded is not None and recorded[:2] == (st.st_size, st.st_mtime_ns)
    same = same and recorded[3:] == (row.bytes, row.mtime_ns)  # the source the journal verified
    return "dest_placeholder" if _dest_placeholder(row, st, attrs_of) else "already_verified" if same else "dest_exists"
