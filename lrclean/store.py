"""The work database: frozen schema and the few write helpers."""

import os
import sqlite3
from datetime import datetime, timezone

from lrclean import paths

TABLES: dict[str, tuple[str, ...]] = {
    "scan": ("id", "root", "kind", "started", "finished", "status"),
    "catalog": ("id", "scan_id", "source_path", "snapshot_path", "role", "status",
                "size", "mtime_ns", "db_version", "wal_bytes", "has_lock"),
    "record": ("catalog_id", "path_norm", "path_orig", "via", "ext",
               "capture_time", "is_sidecar"),
    "catalog_root": ("catalog_id", "path_norm", "path_orig", "via"),
    "file": ("id", "scan_id", "volume", "path_norm", "path_orig", "ext", "size",
             "mtime_ns", "attrs", "placeholder", "container", "file_index",
             "nlink", "checked_at"),
    "hash": ("file_id", "file_index", "size", "mtime_ns", "sample", "full",
             "hashed_at"),
    "image": ("catalog_id", "path_norm", "via", "pick", "developed", "settings"),
}

_INT_COLUMNS = {
    "id", "scan_id", "catalog_id", "file_id", "size", "mtime_ns", "wal_bytes",
    "has_lock", "is_sidecar", "attrs", "placeholder", "file_index", "nlink",
    "pick", "developed",
}


def _kind(col: str) -> str:
    return "INTEGER" if col in _INT_COLUMNS else "TEXT"


def _create_sql(table: str, columns: tuple[str, ...]) -> str:
    parts = []
    for col in columns:
        kind = _kind(col)
        is_key = col == "id" or (table == "hash" and col == "file_id")
        parts.append(f'"{col}" {kind}' + (" PRIMARY KEY" if is_key else ""))
    return f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(parts)})'


def connect(db_path: str) -> sqlite3.Connection:
    folder = os.path.dirname(db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    for table, columns in TABLES.items():
        conn.execute(_create_sql(table, columns))
        have = {r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        for col in columns:
            if col not in have:
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {_kind(col)}')
    conn.commit()
    return conn


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def begin_scan(conn, root: str, kind: str, now=utc_now) -> int:
    scan_id = insert_row(conn, "scan", root=root, kind=kind, started=now(),
                         finished="", status="running")
    conn.commit()
    return scan_id


def finish_scan(conn, scan_id: int, status: str, now=utc_now) -> None:
    conn.execute('UPDATE "scan" SET status = ?, finished = ? WHERE id = ?',
                 (status, now(), scan_id))
    conn.commit()


def latest_scans(conn, kind: str) -> list[sqlite3.Row]:
    newest: dict[str, sqlite3.Row] = {}
    for row in conn.execute('SELECT * FROM "scan" WHERE kind = ? ORDER BY id', (kind,)):
        newest[paths.norm(row["root"])] = row
    return sorted(newest.values(), key=lambda r: r["id"])


def _check(table: str, values: dict) -> None:
    if table not in TABLES:
        raise ValueError(f"unknown table: {table}")
    unknown = sorted(set(values) - set(TABLES[table]))
    if unknown:
        raise ValueError(f"unknown column for {table}: {unknown[0]}")


def _insert(conn, verb: str, table: str, values: dict) -> int:
    _check(table, values)
    cols = list(values)
    names = ", ".join(f'"{c}"' for c in cols)
    marks = ", ".join("?" * len(cols))
    sql = f'{verb} INTO "{table}" ({names}) VALUES ({marks})'
    return conn.execute(sql, [values[c] for c in cols]).lastrowid


def insert_row(conn, table: str, **values) -> int:
    return _insert(conn, "INSERT", table, values)


def insert_many(conn, table: str, rows: list[dict]) -> None:
    for row in rows:
        _check(table, row)
    for row in rows:
        _insert(conn, "INSERT", table, row)
    conn.commit()


def put_hash(conn, **values) -> None:
    if "file_id" not in values:
        raise ValueError("put_hash needs file_id")
    _insert(conn, "INSERT OR REPLACE", "hash", values)
    conn.commit()
