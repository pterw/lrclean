"""The seams, write folders, stopped flag and stall watch of the B2 mover (rules 38, 43)."""

import contextlib
import os
import tempfile
import threading
import time
from typing import NamedTuple

from lrclean import hashing, paths, store


class Seams(NamedTuple):
    stat: object
    attrs_of: object
    is_link: object
    opener: object
    rename: object
    remove: object
    rmdir: object
    makedirs: object
    listdir: object
    append: object
    write: object
    clock: object
    abort: object
    now: object


def append(path: str, data: bytes) -> None:
    with open(path, "ab") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write(path: str, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(temp)
        raise


REAL = Seams(os.stat, None, lambda p: os.path.islink(p) or os.path.isjunction(p), hashing.open_binary,
             os.rename, os.remove, os.rmdir, os.makedirs, os.listdir, append, write, time.monotonic,
             os._exit, store.utc_now)


class Folders(NamedTuple):
    base: str
    photo_root: str


class WriteRefused(ValueError):
    pass


class Stopped(RuntimeError):
    pass


class Ctx(NamedTuple):
    seams: Seams
    folders: Folders
    base: str
    batch: str
    stop: threading.Event


def new_ctx(seams: Seams, folders: Folders, base: str, batch: str) -> Ctx:
    return Ctx(seams, folders, base, batch, threading.Event())


def _key(path: str) -> str:
    return paths.norm(os.path.abspath(path))


def _folder(folders: Folders, kind: str):
    return {"journal": os.path.join(folders.base, "work", "journal"),
            "out": os.path.join(folders.base, "out"),
            "staging": os.path.join(folders.base, "work", "staging"),
            "source": folders.photo_root}.get(kind)


def allowed(folders: Folders, path: str, kind: str) -> bool:
    folder = _folder(folders, kind)
    if folder is None or not os.path.isabs(path):
        return False
    key, root = _key(path), _key(folder).rstrip("/")
    return key.startswith(root + "/") or (kind != "source" and key == root)


def check_write(folders: Folders, path: str, kind: str) -> None:
    if not allowed(folders, path, kind):
        raise WriteRefused(f"{kind} write refused outside its folder: {path}")


def guard(ctx: Ctx, path: str, kind: str) -> None:
    check_write(ctx.folders, path, kind)
    if ctx.stop.is_set():
        raise Stopped("the stall watch stopped the mover")


def watch(work, *, ctx: Ctx, stall_seconds: float, journal_write, poll: float = 0.05) -> tuple[object, bool]:
    clock = ctx.seams.clock
    last = [clock()]
    finished = threading.Event()
    outcome: list = []
    errors: list[BaseException] = []

    def tick():
        if ctx.stop.is_set():
            raise Stopped("the stall watch stopped the mover")
        last[0] = clock()

    def run():
        try:
            outcome.append(work(tick))
        except BaseException as exc:  # re-raised by the caller of watch
            errors.append(exc)
        finally:
            finished.set()

    threading.Thread(target=run, daemon=True).start()
    while not finished.wait(poll):
        if clock() - last[0] > stall_seconds:
            seconds = clock() - last[0]
            if seconds <= stall_seconds:  # a tick landed in between
                continue
            ctx.stop.set()
            journal_write("stalled", seconds=seconds)
            ctx.seams.abort(5)
            return None, True
    if errors:
        raise errors[0]
    return outcome[0], False
