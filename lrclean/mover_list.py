"""The approved removal list for the B2 mover (rule 32): path check, SHA-256, parse, select."""

import csv
import hashlib
import io
import os
import re
from collections.abc import Sequence
from typing import NamedTuple

from lrclean import paths

_SHA = re.compile(r"[0-9a-fA-F]{64}")
_DRIVE = re.compile(r"[A-Za-z]:")
_SEPARATORS = re.compile(r"[\\/]")


class ListKind(NamedTuple):
    """One removal list: its file name in out/, the kind of its rows' main file, its summary and the command."""
    name: str
    kind: str
    summary: str
    command: str


REJECTS = ListKind("reject-removal-list.csv", "reject", "rejects-summary.md", "rejects")
COPIES = ListKind("copy-removal-list.csv", "copy", "copies-summary.md", "copies")
LISTS = (REJECTS, COPIES)
MAIN_KINDS = tuple(item.kind for item in LISTS)
LIST_NAME = REJECTS.name


class ListError(ValueError):
    pass


class ListRow(NamedTuple):
    source: str
    bytes: int
    mtime_ns: int
    sidecars: tuple[str, ...]
    sidecar_bytes: int
    dests: tuple[str, ...]
    twin: str
    kind: str = REJECTS.kind


def check_list_path(list_path: str, base: str) -> ListKind:
    here = paths.norm(os.path.abspath(list_path))
    for item in LISTS:
        if here == paths.norm(os.path.abspath(os.path.join(base, "out", item.name))):
            return item
    names = " or ".join(item.name for item in LISTS)
    raise ListError(f"only {names} in the tool's out folder can be read, not {list_path}")


def list_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def approved_sha(text: str) -> str:
    if not _SHA.fullmatch(text):
        raise ListError("the approved SHA-256 is 64 hexadecimal digits")
    return text.lower()


def _header(roots: int) -> list[str]:
    return (["source", "bytes", "mtime_ns", "sidecars", "sidecar_bytes"]
            + [f"dest_{n}" for n in range(1, roots + 1)] + ["twin"])


def _bad_name(path: str) -> bool:
    rest = path[2:] if _DRIVE.match(path) else path
    return ":" in rest or any(part.endswith((".", " ")) for part in _SEPARATORS.split(rest))


def _number(cell: str, what: str, signed: bool = False) -> int:
    digits = cell[1:] if signed and cell.startswith("-") else cell
    if not digits.isascii() or not digits.isdigit():
        raise ListError(f"{what} is not a whole number: {cell!r}")
    return int(cell)


def _row(cells: list[str], roots: int, kind: str) -> ListRow:
    sidecars = tuple(cells[3].split(";")) if cells[3] else ()
    row = ListRow(cells[0], _number(cells[1], "bytes"), _number(cells[2], "mtime_ns", signed=True), sidecars,
                  _number(cells[4], "sidecar_bytes"), tuple(cells[5:5 + roots]), cells[-1], kind)
    if any(_bad_name(p) for p in (row.source, *row.sidecars, *row.dests, row.twin) if p):
        raise ListError(f"a name that Windows would change or read as a stream: {row.source}")
    return row


def _check_paths(rows: list[ListRow]) -> None:
    sources = [paths.norm(r.source) for r in rows]
    if len(set(sources)) != len(sources):
        raise ListError("a path appears twice as a source")
    for i, row in enumerate(rows):
        if any(paths.norm(s) in sources[:i] + sources[i + 1:] for s in row.sidecars):
            raise ListError(f"a sidecar of {row.source} is the source of another row")
        if row.twin and paths.norm(row.twin) in sources:
            raise ListError(f"the twin of {row.source} is the source of a row")


def read_list(data: bytes, roots: int, kind: str = REJECTS.kind) -> list[ListRow]:
    if roots < 2:
        raise ListError("the list needs at least two archive roots")
    try:
        lines = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ListError(f"the list cannot be read: {exc}") from exc
    if not lines or lines[0] != _header(roots):
        raise ListError("the list's header is not the expected one for these archive roots")
    if any(len(cells) != len(lines[0]) for cells in lines[1:]):
        raise ListError("a row of the list has the wrong number of cells")
    rows = [_row(cells, roots, kind) for cells in lines[1:]]
    _check_paths(rows)
    return rows


def shared_sidecars(rows: Sequence[ListRow]) -> dict[int, str]:
    first: dict[str, tuple[int, str]] = {}
    shared: dict[int, str] = {}
    for i, row in enumerate(rows):
        for side in row.sidecars:
            at, listed = first.setdefault(paths.norm(side), (i, side))
            if at != i:
                shared.setdefault(at, listed)
                shared.setdefault(i, listed)
    return shared


def select(rows: Sequence[ListRow], only: Sequence[str]) -> list[ListRow]:
    if not only:
        return list(rows)
    wanted = {paths.norm(name) for name in only}
    unknown = wanted - {paths.norm(r.source) for r in rows}
    if unknown:
        raise ListError(f"not a row of the list: {sorted(unknown)}")
    return [r for r in rows if paths.norm(r.source) in wanted]
