"""Synthetic trees and Lightroom-like catalogs for tests. Imports nothing from lrclean."""

import os
import sqlite3
import subprocess
import sys

_SCHEMA = (
    "CREATE TABLE AgLibraryRootFolder(id_local INTEGER PRIMARY KEY, absolutePath TEXT, relativePathFromCatalog TEXT)",
    "CREATE TABLE AgLibraryFolder(id_local INTEGER PRIMARY KEY, pathFromRoot TEXT, rootFolder INTEGER)",
    "CREATE TABLE AgLibraryFile(id_local INTEGER PRIMARY KEY, baseName TEXT, extension TEXT, folder INTEGER, sidecarExtensions TEXT)",
    "CREATE TABLE Adobe_images(id_local INTEGER PRIMARY KEY, rootFile INTEGER, captureTime TEXT, masterImage INTEGER)",
    "CREATE TABLE Adobe_variablesTable(name TEXT, value TEXT)",
)
_TABLE_NAMES = ("AgLibraryRootFolder", "AgLibraryFolder", "AgLibraryFile", "Adobe_images", "Adobe_variablesTable")
_DEFAULTS = {
    "root": "c:/shoot-a/",
    "root_relative": "",
    "folder": "",
    "base": "IMG_0001",
    "ext": "ARW",
    "capture": "2024-05-01T10:00:00",
    "sidecars": "",
    "master": None,
}


def make_tree(root: str, files: dict[str, bytes]) -> None:
    """Create each relative path in `files` under `root` with its bytes."""
    for rel, data in files.items():
        full = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(data)


_DEVELOP_TABLE = "Adobe_imageDevelopSettings"
_DEVELOP_KEY_OF = {"hasDevelopAdjustments": "developed", "hasDevelopAdjustmentsEx": "developed_ex", "text": "settings"}


def _develop_layout(entries: list[dict], develop_columns: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """The columns of the develop table, or None when it does not exist. `develop_columns` forces them by SQL name."""
    if develop_columns is not None:
        unknown = set(develop_columns) - set(_DEVELOP_KEY_OF)
        if unknown:
            raise ValueError(f"unknown develop columns {sorted(unknown)}")
        return develop_columns
    named = tuple(col for col, key in _DEVELOP_KEY_OF.items() if any(key in entry for entry in entries))
    return named or None


def _names_develop(entry: dict) -> bool:
    return any(key in entry for key in _DEVELOP_KEY_OF.values())


def _create_schema(
    con: sqlite3.Connection,
    db_version: str,
    omit_tables: tuple[str, ...],
    entries: list[dict],
    develop_columns: tuple[str, ...] | None,
) -> None:
    has_pick = any("pick" in entry for entry in entries)
    for name, ddl in zip(_TABLE_NAMES, _SCHEMA):
        if name not in omit_tables:
            con.execute(ddl[:-1] + ", pick)" if has_pick and name == "Adobe_images" else ddl)
    if "Adobe_variablesTable" not in omit_tables:
        con.execute("INSERT INTO Adobe_variablesTable VALUES('Adobe_DBVersion', ?)", (db_version,))
    layout = _develop_layout(entries, develop_columns)
    if layout is not None and _DEVELOP_TABLE not in omit_tables:
        extra = "".join(f", {col}" for col in layout)
        con.execute(f"CREATE TABLE {_DEVELOP_TABLE}(id_local INTEGER PRIMARY KEY, image INTEGER{extra})")


def _insert_entries(
    con: sqlite3.Connection,
    entries: list[dict],
    omit_tables: tuple[str, ...],
    develop_columns: tuple[str, ...] | None = None,
) -> None:
    rows = [{**_DEFAULTS, **entry} for entry in entries]
    has_pick = any("pick" in entry for entry in entries)
    layout = _develop_layout(entries, develop_columns)
    roots: dict[tuple, int] = {}
    folders: dict[tuple, int] = {}
    file_of_image: dict[int, int] = {}
    image_of_base: dict[str, int] = {}
    for image_id, row in enumerate(rows, start=1):
        if "copy_of" in row:
            continue  # a virtual copy shares its master's file: no root, folder or file row
        root_key = (row["root"], row["root_relative"])
        if root_key not in roots:
            roots[root_key] = len(roots) + 1
            if "AgLibraryRootFolder" not in omit_tables:
                con.execute("INSERT INTO AgLibraryRootFolder VALUES(?,?,?)", (roots[root_key], *root_key))
        folder_key = (roots[root_key], row["folder"])
        if folder_key not in folders:
            folders[folder_key] = len(folders) + 1
            if "AgLibraryFolder" not in omit_tables:
                con.execute("INSERT INTO AgLibraryFolder VALUES(?,?,?)", (folders[folder_key], row["folder"], roots[root_key]))
        file_id = len(file_of_image) + 1
        file_of_image[image_id] = file_id
        image_of_base.setdefault(row["base"], image_id)
        if "AgLibraryFile" not in omit_tables:
            con.execute(
                "INSERT INTO AgLibraryFile VALUES(?,?,?,?,?)",
                (file_id, row["base"], row["ext"], folders[folder_key], row["sidecars"]),
            )
    if "Adobe_images" not in omit_tables:
        for image_id, row in enumerate(rows, start=1):
            _insert_image(con, image_id, row, file_of_image, image_of_base, has_pick)
    if layout is not None and _DEVELOP_TABLE not in omit_tables:
        for image_id, row in enumerate(rows, start=1):
            if _names_develop(row):
                values = [row.get(_DEVELOP_KEY_OF[col]) for col in layout]
                marks = "".join(",?" for _ in layout)
                con.execute(f"INSERT INTO {_DEVELOP_TABLE} VALUES(?,?{marks})", (1000 + image_id, image_id, *values))


def _insert_image(
    con: sqlite3.Connection,
    image_id: int,
    row: dict,
    file_of_image: dict[int, int],
    image_of_base: dict[str, int],
    has_pick: bool,
) -> None:
    if "copy_of" in row:
        master = image_of_base[row["copy_of"]]
        root_file = file_of_image[master]
    else:
        root_file = file_of_image[image_id]
        master = image_of_base[row["master"]] if row["master"] is not None else None
    values = (image_id, root_file, row["capture"], master)
    if has_pick:
        con.execute("INSERT INTO Adobe_images VALUES(?,?,?,?,?)", (*values, row.get("pick")))
    else:
        con.execute("INSERT INTO Adobe_images VALUES(?,?,?,?)", values)


def make_catalog(
    path: str,
    entries: list[dict],
    db_version: str = "1300014",
    omit_tables: tuple[str, ...] = (),
    develop_columns: tuple[str, ...] | None = None,
) -> None:
    """Write a catalog with the C-6 tables; `omit_tables` leaves those tables out. Paths are stored exactly as given: callers pass forward slashes and trailing slashes.

    Entry keys `pick` (adds the untyped column `Adobe_images.pick`), `developed` and `developed_ex` (columns
    `hasDevelopAdjustments` and `hasDevelopAdjustmentsEx` of `Adobe_imageDevelopSettings`, one row per naming entry,
    `id_local` = 1000 + image id), `settings` (column `text` of the same table, stored as given) and `copy_of` (the
    base of the master of a virtual copy) are opt-in.
    `develop_columns` forces exactly those column names (and the table), `()` for neither."""
    con = sqlite3.connect(path)
    try:
        _create_schema(con, db_version, omit_tables, entries, develop_columns)
        _insert_entries(con, entries, omit_tables, develop_columns)
        con.commit()
    finally:
        con.close()


def make_empty_shell(path: str) -> None:
    """A valid SQLite file with no tables."""
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 0")
    con.close()


def make_unreadable(path: str, size: int = 65536) -> None:
    """A file of garbage that is not a database."""
    with open(path, "wb") as handle:
        handle.write(bytes((i * 7 + 13) % 251 for i in range(size)))


def _unclean_child(path: str, entries_json: str) -> None:
    import json

    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    _insert_entries(con, json.loads(entries_json), ())
    con.commit()
    os._exit(0)  # leave without closing: the rows stay in the -wal file


def make_unclean(path: str, entries: list[dict]) -> None:
    """A catalog in write-ahead mode whose committed rows are only in `<path>-wal`, plus `<path>.lock`."""
    import json

    con = sqlite3.connect(path)
    try:
        _create_schema(con, "1300014", (), entries, None)  # the columns and tables the entries ask for, no rows
        con.commit()
    finally:
        con.close()
    here = os.path.dirname(os.path.abspath(__file__))
    code = "import sys; sys.path.insert(0, sys.argv[1]); import synth; synth._unclean_child(sys.argv[2], sys.argv[3])"
    subprocess.run(
        [sys.executable, "-c", code, here, path, json.dumps(entries)],
        check=True,
        timeout=60,
    )
    with open(path + ".lock", "wb"):
        pass
