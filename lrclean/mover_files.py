"""Batch names, staged paths and file identity for the B2 mover (rules 35, 37, 40)."""

import os
import re

from lrclean import paths
from lrclean.mover_seams import Ctx


def batch_name(started: str) -> str:
    return started.replace("-", "").replace(":", "")


def valid_batch(text: str) -> bool:
    return re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", text) is not None


def staged_path(base: str, batch: str, original: str) -> str:
    drive = paths.volume(original)
    after = original[original.index(":") + 1:] if drive else ""
    rest = after.replace("/", "\\").strip("\\")
    if not after.startswith(("\\", "/")) or not rest or ":" in rest or ".." in rest.split("\\"):
        raise ValueError(f"not a plain absolute path with a drive letter: {original!r}")
    return os.path.join(base, "work", "staging", batch, drive[0], *rest.split("\\"))


def resolve(folder: str, name: str, ctx: Ctx) -> list[str]:
    return [entry for entry in ctx.seams.listdir(paths.fs_path(folder)) if paths.norm(entry) == paths.norm(name)]


def distinct(stats) -> bool:
    return len({(st.st_dev, st.st_ino) for st in stats}) == len(stats)


def other_volume(a, b) -> bool:
    return a.st_dev != b.st_dev


def _folders_down(photo_root: str, target: str) -> list[str] | None:
    chain, folder = [], os.path.dirname(target)
    while paths.norm(folder) != paths.norm(photo_root):
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        chain.append(folder)
        folder = parent
    return [photo_root, *reversed(chain)]


def link_in_path(photo_root: str, target: str, ctx: Ctx) -> str:
    """The first link from photo_root down; the target's absolute path when it is not strictly below photo_root."""
    folders = _folders_down(photo_root, target)
    if folders is None:
        return os.path.abspath(target)
    return next((folder for folder in folders if ctx.seams.is_link(paths.fs_path(folder))), "")
