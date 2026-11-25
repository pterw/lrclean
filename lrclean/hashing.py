"""Resumable sample and full hashing of scanned photo files (rules 4, 7, 8, 9)."""

import hashlib
import os
from collections import defaultdict

from lrclean import paths, store

CHUNK = 64 * 1024


def open_binary(path: str):
    return open(paths.fs_path(path), "rb")


def sample_hash(path: str, size: int, opener=open_binary) -> str:
    digest = hashlib.sha256(f"{size}:".encode())
    with opener(path) as handle:
        digest.update(handle.read(CHUNK))
        if size > CHUNK:
            handle.seek(max(CHUNK, size - CHUNK))
            digest.update(handle.read(CHUNK))
    return digest.hexdigest()


def full_hash(path: str, opener=open_binary) -> str:
    digest = hashlib.sha256()
    with opener(path) as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_one(conn, file_row, *, want_full: bool, opener=open_binary, stat=os.stat,
             now=store.utc_now) -> str:
    if file_row["placeholder"]:
        return "skipped_placeholder"
    path = file_row["path_orig"]
    try:
        st = stat(paths.fs_path(path))
        if paths.is_placeholder(getattr(st, "st_file_attributes", 0)):
            return "skipped_placeholder"
        if (st.st_size, st.st_mtime_ns, paths.signed64(st.st_ino)) != (
                file_row["size"], file_row["mtime_ns"], file_row["file_index"]):
            conn.execute('DELETE FROM "hash" WHERE file_id = ?', (file_row["id"],))
            conn.commit()
            return "changed"
        old = conn.execute('SELECT * FROM "hash" WHERE file_id = ?',
                           (file_row["id"],)).fetchone()
        valid = old is not None and (old["file_index"], old["size"], old["mtime_ns"]) == (
            file_row["file_index"], file_row["size"], file_row["mtime_ns"])
        sample = old["sample"] if valid and old["sample"] else None
        full = old["full"] if valid and old["full"] else None
        if sample and (full or not want_full):
            return "reused"
        if sample is None:
            sample = sample_hash(path, file_row["size"], opener)
        if want_full and full is None:
            full = full_hash(path, opener)
    except OSError:
        return "unreadable"
    store.put_hash(conn, file_id=file_row["id"], file_index=file_row["file_index"],
                   size=file_row["size"], mtime_ns=file_row["mtime_ns"],
                   sample=sample, full=full, hashed_at=now())
    return "hashed"


def _photo_rows(conn, scan_id: int) -> list:
    rows = conn.execute('SELECT * FROM "file" WHERE scan_id = ? ORDER BY id', (scan_id,))
    return [r for r in rows
            if r["ext"] in paths.PHOTO_EXTS and not r["container"].startswith("cache:")]


def _sample_of(conn, file_id: int) -> str:
    return conn.execute('SELECT sample FROM "hash" WHERE file_id = ?',
                        (file_id,)).fetchone()["sample"]


def hash_files(conn, *, library_roots: list[str], backup_roots: list[str],
               opener=open_binary, stat=os.stat, now=store.utc_now) -> dict:
    counts = dict.fromkeys(
        ("sampled", "full", "skipped_placeholder", "changed", "unreadable", "reused"), 0)
    kinds = {"hashed": "sampled"}

    # A file that passes the sample pass but fails only in the full pass is counted
    # under "sampled" and again under "unreadable"/"changed"; this is intended.
    def run(row, want_full):
        status = hash_one(conn, row, want_full=want_full, opener=opener, stat=stat, now=now)
        if want_full and status == "reused":
            return status
        counts["full" if want_full and status == "hashed" else kinds.get(status, status)] += 1
        return status

    library = {paths.norm(r) for r in library_roots}
    backup = {paths.norm(r) for r in backup_roots}
    scans = [s for s in store.latest_scans(conn, "files") if s["status"] == "complete"]
    lib_rows = [r for s in scans if paths.norm(s["root"]) in library
                for r in _photo_rows(conn, s["id"])]
    groups = defaultdict(list)
    for row in lib_rows:
        if run(row, False) in ("hashed", "reused"):
            groups[(row["size"], _sample_of(conn, row["id"]))].append(row)
    for members in groups.values():
        if len(members) > 1:
            for row in members:
                run(row, True)
    sizes = {r["size"] for r in lib_rows}
    for s in scans:
        if paths.norm(s["root"]) in backup and paths.norm(s["root"]) not in library:
            for row in _photo_rows(conn, s["id"]):
                if row["size"] in sizes:
                    run(row, False)
    return counts
