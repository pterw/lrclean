"""The journal of the B2 mover (rules 38, 39, 40, 43): one JSON object per line, flushed before its step."""

import csv
import io
import json
import os
from typing import NamedTuple

from lrclean import paths
from lrclean.mover_seams import Ctx, Stopped, check_write, guard

EVENTS = frozenset({"start", "begin", "end", "held", "stalled", "repaired"})
MARKER = {"event": "repaired"}
REQUIRED = {"begin": {"row": int, "op": str, "original": str}, "end": {"row": int, "original": str, "state": str},
            "held": {"source": str, "kind": str, "reason": str, "op": str}}
STATES = frozenset({"staged", "restored", "purged", "open_stage", "open_purge", "open_restore"})
COLUMNS = ("source", "kind", "staged", "bytes", "sha256", "state", "reason")
_FIELDS = ("kind", "original", "staged", "bytes", "mtime_ns", "file_index", "sha256", "dests", "twin", "twin_sha256")
_BLANK = {"kind": "", "original": "", "staged": "", "bytes": 0, "mtime_ns": 0, "file_index": 0, "sha256": "",
          "dests": (), "twin": "", "twin_sha256": ""}
_DONE = {"stage": "staged", "purge": "purged"}


class JournalError(ValueError):
    pass


class Entry(NamedTuple):
    row: int
    kind: str
    original: str
    staged: str
    bytes: int
    mtime_ns: int
    file_index: int
    sha256: str
    dests: tuple
    twin: str
    twin_sha256: str
    family: int
    state: str


def _read(path: str, ctx: Ctx) -> bytes:
    with ctx.seams.opener(path) as handle:
        return handle.read()


def _parse(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:  # includes UnicodeDecodeError and JSONDecodeError
        return None


def _known(item) -> bool:
    """A known event whose fields the readers use are all there, with the right types."""
    if not isinstance(item, dict) or item.get("event") not in EVENTS:
        return False
    dests = item.get("dests", [])
    return (all(isinstance(item.get(name), kind) for name, kind in REQUIRED.get(item["event"], {}).items())
            and isinstance(dests, list) and all(isinstance(pair, list) and len(pair) == 2 for pair in dests))


def read_journal(path: str, ctx: Ctx) -> list[dict]:
    complete = _read(path, ctx).split(b"\n")[:-1]  # the last piece has no newline: torn, or empty
    parsed = [_parse(raw) for raw in complete]
    lines = []
    for i, item in enumerate(parsed):
        if i + 1 < len(parsed) and parsed[i + 1] == MARKER:
            continue  # the line just before a marker never had its newline
        if not _known(item):
            raise JournalError(f"invalid journal line {i + 1} in {path}")
        lines.append(item)
    return lines


class Journal:
    def __init__(self, path: str, ctx: Ctx):
        self.path = path
        self.ctx = ctx
        self._ready = False
        self._rows: set[int] = set()

    def _allow(self, event: str, path: str) -> None:
        if event == "stalled":
            check_write(self.ctx.folders, path, "journal")
        else:
            guard(self.ctx, path, "journal")

    def _prepare(self, event: str) -> None:
        folder = os.path.dirname(self.path)
        self._allow(event, folder)
        self.ctx.seams.makedirs(folder, exist_ok=True)
        try:
            data = _read(self.path, self.ctx)
        except FileNotFoundError:
            data = b""
        if data and not data.endswith(b"\n"):
            self.ctx.seams.append(self.path, b"\n" + json.dumps(MARKER).encode() + b"\n")
        self._ready = True

    def write(self, event: str, **fields) -> None:
        if event not in EVENTS or "at" in fields:
            raise ValueError(f"cannot write event {event!r} with fields {sorted(fields)}")
        self._allow(event, self.path)
        if not self._ready:
            self._prepare(event)
        line = json.dumps({"event": event, "at": self.ctx.seams.now(), **fields}) + "\n"
        self.ctx.seams.append(self.path, line.encode())

    def _begin_fields(self, begin: dict) -> dict:
        first = begin["op"] == "stage" and begin["row"] not in self._rows
        self._rows.add(begin["row"])
        return begin if first else {key: value for key, value in begin.items() if key != "family"}

    def run_step(self, begin: dict, do, end):
        action = do

        def do():  # the begin append may have returned after the `stalled` line (rule 43)
            if self.ctx.stop.is_set():
                raise Stopped("the stall watch stopped the mover")
            return action()

        result = (self.write("begin", **self._begin_fields(begin)), do())[1]
        self.write("end", **end(result))
        return result


def entries(lines: list[dict]) -> list[Entry]:
    files: dict[tuple, dict] = {}
    family: dict[int, int] = {}
    for line in lines:
        if line["event"] not in ("begin", "end"):
            continue
        key = (line["row"], line["original"])
        state = "open_" + line["op"] if line["event"] == "begin" else line["state"]
        if state not in STATES:
            raise JournalError(f"unknown state {state!r} in a {line['event']} line")
        if line["event"] == "end" and key not in files:
            raise JournalError(f"an end line for row {key[0]} has no begin line before it")
        item = files.setdefault(key, dict(_BLANK, row=line["row"]))
        item.update({name: line[name] for name in _FIELDS if name in line}, state=state)
        if "family" in line:
            family.setdefault(line["row"], line["family"])
    return [Entry(**dict(item, family=family.get(item["row"], 0), dests=tuple(map(tuple, item["dests"]))))
            for item in files.values()]


def _exists(ctx: Ctx, path: str):
    try:
        ctx.seams.stat(paths.fs_path(path))
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None  # cannot tell
    return True


def _stage_place(original: bool, staged: bool) -> str:
    return {(True, False): "not_done", (False, True): "done"}.get((original, staged), "unplaced")


def _restore_place(original: bool, staged: bool) -> str:
    return {(True, False): "done", (False, True): "not_done"}.get((original, staged), "unplaced")


def _purge_place(original: bool, staged: bool) -> str:
    return "not_done" if staged else "done"


_PLACERS = {"open_stage": _stage_place, "open_purge": _purge_place, "open_restore": _restore_place}


def place(entry: Entry, ctx: Ctx) -> str:
    facts = (_exists(ctx, entry.original), _exists(ctx, entry.staged))
    return "unplaced" if None in facts else _PLACERS[entry.state](*facts)


def write_results(path: str, op: str, lines: list[dict], ctx: Ctx) -> None:
    guard(ctx, path, "out")
    mine = [e for e in entries(lines) if e.state.removeprefix("open_") in (op, _DONE[op])]
    rows = [(e.original, e.kind, e.staged, e.bytes, e.sha256, e.state, "") for e in mine]
    rows += [(h["source"], h["kind"], "", "", "", "held", h["reason"]) for h in lines
             if h["event"] == "held" and h["op"] == op]
    text = io.StringIO()
    writer = csv.writer(text)  # CRLF, as the reject check's outputs
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    ctx.seams.write(path, text.getvalue().encode("utf-8-sig"))
