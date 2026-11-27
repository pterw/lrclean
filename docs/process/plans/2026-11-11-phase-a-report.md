# Phase A Report Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `python -m lrdup`, the read-only inventory and report tool of phase A: it snapshots and reads Lightroom catalogs, walks the library and backup roots, hashes, classifies every file, and writes eleven CSV sections plus `summary.md`.

**Architecture:** A small foundation (task A: `paths.py`, `store.py`; task K: `tests/synth.py`) that every other task imports and none re-implements. Then one module per task, each tested against rows or trees the test builds itself: readers (B catalogs, C inventory, D hashing), the verdict engine (E classify), the section builders (F, G, H, M) and the writer (I). Task J wires the subcommands; task L joins the real modules in one end-to-end test.

**Tech Stack:** Python 3.13 standard library (`sqlite3`, `hashlib`, `os`, `csv`, `unittest`); Windows paths.

**Spec:** `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase A". Rules 1 to 15, report sections 1 to 11 and the verification bullets are cited by number below.

Sections: Global Constraints=brief, Dispatch=reference, Tasks=brief, Spec coverage=reference, Controller steps=reference, Not in this plan=reference

## Global Constraints

- Phase A only, read-only. No task moves, renames, deletes or writes a file outside the tool's own `work/` and `out/` folders. No `mover.py`. No write to a catalog's database; the original catalog is opened by nothing but the copy (rule 6).
- Python 3.13, standard library only, `unittest`, LF files (`newline="\n"`). Tests run as `python -m unittest discover -s tests` from the repository root; test files import `synth` and `lrdup.<module>`; `tests/` has no `__init__.py`.
- PRIVATE DATA: no real path or name in code, tests, docs or reports. Fixtures are plainly synthetic (`shoot-a`, `IMG_0001.ARW`). Every test builds its own tree in `tempfile.TemporaryDirectory()`. Nothing runs on a real folder.
- Tests never need the machine: the process list (rule 6), file attributes (rule 7), the clock, `os.stat` at report time (rule 13), `os.scandir`, `os.path.exists` and the file opener are parameters with plain defaults. A hard link (rule 10), a junction (skipped when it cannot be made) and a path over 260 characters (rule 4) are made for real inside the temp tree.
- Rule 4 everywhere: every `os.stat`, `open`, `scandir` or `exists` of a stored `path_orig` goes through `paths.fs_path`.
- Frozen: the schema, `paths.py`, `store.py`, every Interfaces block below and the section columns. An implementer that needs one changed returns NEEDS_CONTEXT; a schema change is never an edit.
- Output: CSV with `encoding="utf-8-sig"` and `newline=""`; `summary.md` with `newline="\n"`. Every row of every section has `how_decided`.
- Configuration is `work/config.json`, loaded only by `cli.py`, which passes plain values to modules. A missing key is an error naming the key; no silent default for a root. Owner exclusion (`excluded_catalogs`) is read from the configuration at every verdict and never stored with a scan.
- Verdicts and sections 3, 4, 6, 7, 9 and 10 are about files on library roots only. Files on backup roots appear only in sections 2, 5 and 11.
- KISS: the simplest code that meets Behaviour and Acceptance; one test per behaviour, near-duplicates parametrized, each naming the behaviour (and the rule number) it guards; loops linear in the number of files; every test that can hang has a timeout. A task is about 5 minutes of work.
- Code tasks never commit; the controller lands each patch. Conventional commit subjects, no trailer, never `--amend`, rebase, reset, push or `--no-verify`. No pre-commit configuration exists; skip it.

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | A, K | immediately | 2 code phases + up to 2 nested gate-runners = 4 |
| 2 | B, C, D, E, I | wave 1 landed (apply, commit, gate) | 5 code phases + up to 5 nested + reviews of A, K = 12 |
| 3 | F, G, H, M | wave 2 landed | 4 code phases + up to 4 nested + reviews of B, C, D, E, I = 13; the fifth review waits |
| 4 | J | wave 3 landed | 1 code phase + nested + reviews of F, G, H, M and any waiting = at most 7 |
| 5 | L | J landed | 1 code phase + nested + review of J = 3 |

Peak 12, counting every nested gate-runner and every trailing review (wave 2 reaches 12; wave 3 would reach 13, so its last review starts only when fewer than 12 agents run). The cap is 12. The controller lands a wave's patches before it dispatches the next wave, so every worktree starts at a BASE that already holds what its task imports and every patch holds only its own files. Reviews trail and may overlap the next wave. A task's tests run on its own `After:` closure only: where a test needs another task's output only as data, it builds that data literally (rows through `store.insert_row`, section rows as dicts); task L joins the real producers to the real consumers. `After:` names the tasks whose code the task or its tests import directly.

Landing order: A, K, B, C, D, E, I, F, G, H, M, J, L. Per patch the controller runs `git apply --check --index <patch>`, `git apply --index <patch>`, one commit with the task's subject, then `gate-runner` at that HEAD. After A lands, `lrdup/__init__.py` and `lrdup/store.py` are controller-only files. Review: one `sdd-reviewer` per task with the same CONTEXT as its implementer; only E has a planted-defect file, so the other reviewers judge tests by reading them.

**Frozen interfaces.** Every task's Interfaces block, and the items below, are fixed before dispatch.

*Schema* (spec "Frozen schema", integers are `int`, flags are 0 or 1, other columns text): `scan(id, root, kind, started, finished, status)`; `catalog(id, scan_id, source_path, snapshot_path, role, status, size, mtime_ns, db_version, wal_bytes, has_lock, reason)` (`reason` since 0.1.1); `record(catalog_id, path_norm, path_orig, via, ext, capture_time, is_sidecar)`; `catalog_root(catalog_id, path_norm, path_orig, via)` (one row per `AgLibraryRootFolder` of a catalog for its `absolutePath`, `via='absolute'`, and one more, `via='relative'`, for the path built from the catalog's own folder and `relativePathFromCatalog`; none for a NULL `relativePathFromCatalog`; both without a trailing slash); `file(id, scan_id, volume, path_norm, path_orig, ext, size, mtime_ns, attrs, placeholder, container, file_index, nlink, checked_at)`; `hash(file_id, file_index, size, mtime_ns, sample, full, hashed_at)`. Conventions the schema leaves open: `file.container` is `''` or `<type>:<folder path_norm>` with type `storage`, `cache` or `unknown`, and for a file in nested containers it is the innermost; a preview-cache folder is ONE `file` row (`ext='lrdata'`, `size` = sum of its contents, `path_orig` = the folder); an unreadable folder is one `file` row (`ext='<unreadable>'`, `size=0`) and its scan is `partial`.

*Stored value forms.* `file.ext` and `record.ext` are lower case without a dot; `file.path_norm = paths.norm(path_orig)`; `file.volume = paths.volume(path_orig)`; `placeholder`, `is_sidecar` and `has_lock` are 0 or 1; `attrs`, `size`, `mtime_ns`, `file_index`, `nlink` are ints; `checked_at`, `started`, `finished`, `hashed_at` are `store.utc_now()`; `scan.root` is the configured root string unchanged and every consumer compares roots with `paths.norm`; `catalog.source_path` is in `path_orig` form and is the value of every "catalog" column; `capture_time` is the catalog's text and `date_matches` parses it with `datetime.fromisoformat`, returning False when it cannot. A file belongs to the root of its scan.

*Commits.* `store.begin_scan`, `finish_scan`, `insert_many` and `put_hash` commit before returning; `insert_row` does not (callers commit). `latest_scans` groups roots by `paths.norm(root)`.

*Row forms for CSV.* Booleans are `yes` or `no`, integers decimal, paths `path_orig`, empty is `''`. Every builder returns rows sorted by its first column ascending (ties by the next columns in order; `bytes` descending where the section says largest first), so two runs are byte-identical.

*Section files and columns* (`how_decided` ends every row):

| # | File | Columns before `how_decided` | Notes |
|---|---|---|---|
| 1 | `01_catalogs.csv` | `catalog, role, status, reason, db_version, records, wal_bytes, has_lock, excluded_by_owner` (`reason` since 0.1.1) | `records` counts every recorded file type; `excluded_by_owner` is yes or no from the current `excluded_catalogs` |
| 2 | `02_shoot_catalogs.csv` | `catalog, records_present, records_not_read, missing_moved, missing_same_name_only, missing_nothing, root_folders, backup_same_name_only_lower_bound, backup_nothing_lower_bound, uncatalogued_in_tree` | photo and sidecar types only; `records_not_read` counts photo and sidecar records whose path lies under a root that is not `complete`, or under no scanned root (in neither `records_present` nor any missing column) |
| 3 | `03_safe_candidates.csv` | `path, folder, bytes, twin_path, group` | safe candidates and nothing else |
| 4 | `04_needs_your_eye.csv` | `kind, group, path, folder, bytes, recorded` | `kind`: `no_recorded_member`, `many_recorded`, `same_file` (`group` = `<volume>:<file_index>`), `coverage_incomplete`, `changed_since_scan`, `not_compared` |
| 5 | `05_external_copies.csv` | `kind, folder, backup_roots, raws, copied_sampled, copied_verified, no_copy, bytes` | `kind`: `year` or `backup_only` |
| 6 | `06_raw_jpeg_pairs.csv` | `folder, pairs, jpeg_bytes, sidecar_pairs, sidecar_bytes, own_photo_pairs, own_photo_bytes, uncatalogued_pairs, uncatalogued_bytes, same_time_pairs, same_time_bytes` | `same_time_pairs` counts pairs (of any of the three kinds) whose raw and JPEG `mtime_ns` differ by at most two seconds; `same_time_bytes` is the bytes of their JPEG side |
| 7 | `07_large_unreferenced.csv` | `folder, files, bytes` | largest first |
| 8 | `08_missing.csv` | `path, kind, capture_time, state` | `kind` photo or sidecar; `state` missing, drive_not_connected or not_read |
| 9 | `09_containers.csv` | `container, type, bytes, files, catalog_beside, catalog_state, reclaimable` | `reclaimable`: yes, no, or unverified |
| 10 | `10_loose_photos.csv` | `folder, raws, jpegs, bytes, listed` | `listed` yes or no |
| 11 | `11_not_read.csv` | `kind, item, detail` | `kind`: `placeholder`, `unreadable_folder`, `partial_root`, `not_connected_root`, `not_scanned_root` (a backup root with no files scan; a library root with no files scan makes `report` exit 4), `limiting_catalog` (`item` = the cause text) |

*Verdict classes* (strings in `classify.py`): `recorded`, `safe_candidate`, `coverage_incomplete`, `unique_unrecorded`, `not_compared`, `needs_eye_no_recorded`, `needs_eye_many_recorded`, `same_file`, `placeholder`, `changed_since_scan`.

Change notes (sent by the controller when a task lands, to tasks running or staged):

| Task | Changes | Who is told |
|---|---|---|
| A | new `paths.py`, `store.py` | everyone running or staged |
| K | new `tests/synth.py` | B, C, D, J, L |
| D | new `hashing.py`: `open_binary`, `sample_hash`, `full_hash`, `hash_one`, `hash_files` | G, J |
| E | new `classify.py`: `root_states`, `coverage`, `recorded_norms`, `inventoried_files`, `eligible_files`, `verdicts` | F, G, H, M, J |
| B, C, F, G, H, I, M | new module files | J |
| J | new `cli.py` | L |

Gates block for the workspace `constraints.md` (as already written there):

```gates
- name: unit
  run: timeout 600 python -m unittest discover -s tests -v
  pass: exit 0
  quote: last line
- name: mutants-classify
  run: python C:/ssw/tools/mutate.py tests/mutants/classify.json --jobs 3 --timeout 300
  pass: exit 0
  quote: first line
  when: any of lrdup/classify.py, tests/test_classify.py, tests/mutants/classify.json changed
```

---

### Task A: foundation (`paths.py`, `store.py`)

`Wave:` 1 · `Touches:` lrdup/__init__.py (empty), lrdup/paths.py, lrdup/store.py, tests/test_paths.py, tests/test_store.py · `After:` none · `Mode:` code

**Behaviour:** the shared helpers and the database exist once.

**Interfaces:**
- `paths.py`: `RAW_EXTS` (frozenset, lower case, no dot: `arw cr2 cr3 dng nef nrw orf pef raf rw2 srw`), `JPEG_EXTS` (`jpg jpeg`), `PHOTO_EXTS = RAW_EXTS | JPEG_EXTS`, `SIDECAR_EXTS` (`xmp acr`); `PLACEHOLDER_MASK = 0x1000 | 0x40000 | 0x400000`; `LONG_LIMIT = 260`. `norm(path: str) -> str`: strip a `\\?\` prefix, NFC, one slash direction (`/`), case fold, NFC again, no trailing slash except a drive root; it does not collapse `..` (callers use `os.path.normpath` first). `fs_path(path: str) -> str`: the path with the `\\?\` prefix (and `\\?\UNC\` for a UNC path) exactly when `len(path) > 260`, else unchanged. `is_placeholder(attrs: int) -> bool`. `volume(path: str) -> str` (lower-case drive such as `c:`, else `''`). `split_path(path: str) -> tuple[str, str]` (folder, name; split at the last `/` or `\`, folder `''` when there is none). `make_container(kind: str, folder_norm: str) -> str` and `split_container(value: str) -> tuple[str, str] | None` (`None` for `''`).
- `store.py`: `TABLES: dict[str, tuple[str, ...]]` (table to column names, as in the schema; six tables, `catalog_root` included); `connect(db_path: str) -> sqlite3.Connection` (creates the folder and missing tables; `sqlite3.Row` rows); `utc_now() -> str` (ISO 8601 UTC, seconds); `begin_scan(conn, root: str, kind: str, now=utc_now) -> int` (status `running`); `finish_scan(conn, scan_id: int, status: str, now=utc_now) -> None`; `latest_scans(conn, kind: str) -> list[sqlite3.Row]` (the highest-id scan of each root, roots grouped by `paths.norm`); `insert_row(conn, table: str, **values) -> int` (returns the new id; `ValueError` for an unknown table or column); `insert_many(conn, table: str, rows: list[dict]) -> None`; `put_hash(conn, **values) -> None` (insert or replace by `file_id`). Commit behaviour as in Dispatch.

**Acceptance:**
1. Rules 3 and 4: `norm` equates `C:\Shoot-A\IMG_0001.ARW`, `c:/shoot-a/img_0001.arw`, the NFD spelling of an accented name and the `\\?\` form, and keeps `IMG_0001` and `IMG_0002` apart; `fs_path` adds the prefix at 261 characters and not at 260; a file at a real path over 260 characters is created and read back through `fs_path`; `split_path` splits `c:/a\b/IMG_0001.ARW` into folder `c:/a\b` and name `IMG_0001.ARW`, and a bare name into `''` and the name.
2. Rule 7: `is_placeholder` is true for each of the three bits alone and together, false for `0x20` and `0`.
3. Frozen schema: `connect` makes exactly the six tables (`scan`, `catalog`, `record`, `catalog_root`, `file`, `hash`) with exactly the spec's columns (compared with a literal list; `catalog` has no `excluded_by_owner`); `insert_row` rejects an unknown table and an unknown column; `latest_scans` returns only the newest scan of each root, whatever the other roots' ids, and treats `C:\Shoot-A` and `c:/shoot-a` as one root.
4. Commits: after `begin_scan`, `finish_scan`, `insert_many` and `put_hash` a second connection to the same file sees the change; after `insert_row` alone it does not until the caller commits.

Commit subject: `feat(lrdup): Add the shared path helpers and the work database`

---

### Task K: `tests/synth.py`

`Wave:` 1 · `Touches:` tests/synth.py, tests/test_synth.py · `After:` none · `Mode:` code

**Behaviour:** the synthetic trees and catalogs every test builds from. It imports nothing from `lrdup`.

**Interfaces:** `make_tree(root: str, files: dict[str, bytes]) -> None` (relative path to content). `make_catalog(path: str, entries: list[dict], db_version: str = "1300014", omit_tables: tuple[str, ...] = ()) -> None` builds the C-6 tables (`AgLibraryRootFolder(id_local, absolutePath, relativePathFromCatalog)`, `AgLibraryFolder(id_local, pathFromRoot, rootFolder)`, `AgLibraryFile(id_local, baseName, extension, folder, sidecarExtensions)`, `Adobe_images(id_local, rootFile, captureTime, masterImage)`, `Adobe_variablesTable(name, value)` with `Adobe_DBVersion`); an entry is `{"root": "c:/shoot-a/", "root_relative": "", "folder": "day-1/", "base": "IMG_0001", "ext": "ARW", "capture": "2024-05-01T10:00:00", "sidecars": "", "master": None}` (`master` names another entry's base to make a virtual copy; `root_relative` is the root folder's `relativePathFromCatalog`; `None` stores NULL). `make_empty_shell(path)`, `make_unreadable(path, size: int = 65536)` (garbage bytes), `make_unclean(path, entries)` (write-ahead mode, committed rows not checkpointed, left by a child process that exits without closing, plus `<path>.lock`). Forward slashes, trailing slash on `absolutePath` and `pathFromRoot`.

**Acceptance:**
1. `make_tree` creates exactly the given files with their bytes, including nested folders.
2. Spec "Run configuration" fixtures: `make_catalog` read back with plain `sqlite3` has the C-6 columns and `Adobe_DBVersion`, `omit_tables` omits exactly those tables, and a virtual-copy entry points at its master's image.
3. The unclean catalog's committed rows are visible when its `-wal` is copied beside it and absent without it, and `<path>.lock` exists; the shell has no tables; the unreadable file is garbage of the given size.

Commit subject: `test(lrdup): Add the synthetic tree and catalog builder`

---

### Task B: `catalogs.py`

`Wave:` 2 · `Touches:` lrdup/catalogs.py, tests/test_catalogs.py · `After:` A, K · `Mode:` code

**Behaviour:** rules 2, 5 (statuses), 6 and 14. Finds each `.lrcat` under `catalog_roots` (never entering `own_dir` or `exclude`, never following links), copies it with its `-wal` and `-shm`, reads the copy, stores one `catalog` row and its `record` rows. A root that does not exist gets a `not_connected` scan and no rows. One unreadable catalog never stops the run.

**Interfaces:**
- `class LightroomRunning(RuntimeError)`. `running_processes() -> list[str]` (lower-case image names; bounded by a 30 s timeout; if the list cannot be read it raises, so the copy is refused: fail closed).
- `find_catalogs(root: str, own_dir: str, exclude: list[str], scandir=os.scandir) -> tuple[list[str], list[str]]`: sorted source paths (original case) and the folders that could not be listed. Excluded folders are matched by whole path component after `paths.norm`.
- `snapshot_catalog(source: str, work_dir: str, processes=running_processes) -> str` returns the snapshot `.lrcat` path inside `<work_dir>/catalogs/<stem>-<12 hex of sha1(norm(source))>/`; raises `LightroomRunning` before copying anything when a name starting `lightroom` is listed; it first deletes `<name>`, `<name>-wal` and `<name>-shm` from its own snapshot folder, and nothing else.
- `class CatalogRead(NamedTuple)`: `status: str` (`ok`, `empty`, `partial`, `unreadable`), `db_version: str`, `records: list[dict]` (keys `path_norm, path_orig, via, ext, capture_time, is_sidecar`), `roots: list[dict]` (keys `path_norm, path_orig, via`: one `via='absolute'` row per root folder for its `absolutePath` and one `via='relative'` row for the path built as below; a root folder whose `relativePathFromCatalog` is NULL gets no `via='relative'` root row and no `via='relative'` record; an empty string is joined to the catalog's own folder; no trailing slash). `read_snapshot(snapshot_path: str, source_path: str) -> CatalogRead`: opened without `immutable`; `empty` = no tables; `partial` = some of the five tables missing, or a read error after some records; extensions lower case, `capture_time` from the master image (`masterImage`, else the image itself), `''` when unknown. The `via='relative'` path is `relativePathFromCatalog` joined to the source's own folder and passed through `os.path.normpath` before `paths.norm`.
- `is_stale(source_path: str, size: int, mtime_ns: int, stat=os.stat) -> bool` (true when size or time differs, or when the source cannot be statted).
- `scan_catalogs(conn, *, catalog_roots: list[str], own_dir: str, exclude: list[str], work_dir: str, live_catalog: str | None, old_states: list[str], processes=running_processes, now=store.utc_now, scandir=os.scandir) -> list[int]` (catalog ids). One `kind='catalogs'` scan per root: `complete` when the walk finished (catalog statuses are separate), `partial` when any folder could not be listed or an exception (including `LightroomRunning`) interrupted it, then re-raised, `not_connected` for a missing root. `role` is `live` or `old_state` only by `norm` equality with the given paths, else `shoot`; `size` and `mtime_ns` are the source's at snapshot time; `has_lock` = `<source>.lock` exists; `wal_bytes` = size of `<source>-wal`. It stores `CatalogRead.records` as `record` rows and `CatalogRead.roots` as `catalog_root` rows. Nothing is stored about owner exclusion.

**Acceptance:**
1. Rule 2 (and the sidecar-JPEG and moved-record bullets): an entry yields the absolute record, the `via='relative'` record, and one `is_sidecar=1` record per entry of `sidecarExtensions` for each of `JPG`, `xmp`, `jpg,xmp`, `xmp,acr`; a virtual copy takes its master's capture time; both vias are stored; the catalog's root folder has `relativePathFromCatalog` `../photos/` and the source catalog sits in a different folder from its snapshot, and the stored relative `path_norm` equals `norm(<source folder>/../photos/<folder><base>.<ext>)` with the `..` collapsed (so it equals the path of a real file the test creates there).
2. Rule 6: two sources with the same file name in different folders get different snapshot folders, each with `-wal` and `-shm` copied; a listed Lightroom process raises `LightroomRunning` with nothing copied; a stale `.lock` does not refuse and gives `has_lock=1`; the source folder's listing (name, size, mtime_ns and SHA-256 of every file, including `-wal`, `-shm` and `.lock`) is identical before and after `snapshot_catalog` and `read_snapshot`, and no file other than the snapshot folder is created anywhere under the source's folder; rows committed but not checkpointed (the unclean catalog) are read; `is_stale` is true after the source changes in size or in time or cannot be statted, false otherwise.
3. Rule 5 statuses and the unreadable-catalog bullet: unreadable garbage, an empty shell, a catalog missing `Adobe_images` (partial, still yielding records) and an ok catalog in one run give four rows with those statuses; the run finishes and the ok catalog's records are stored.
4. Rule 14 and roles: an injected `scandir` records every folder it is asked to list; `own_dir` and each excluded folder are not among them, given with a different case and with backslashes, and an excluded folder `x` does not prune a sibling `x2`; with `own_dir` and its snapshots inside the searched root, two runs return the same source paths and none under `own_dir`; a catalog in a folder named `live` is a `shoot` unless given as `live_catalog`.
5. Rule 5 root states: a missing root is a `not_connected` scan with no catalog row; a `scandir` that raises for one folder lists it in the second return value of `find_catalogs` and gives a `partial` scan that still stores the other catalogs; `LightroomRunning` during a run leaves the scan `partial` and is re-raised.
6. Root folders (C-35, C-41): a catalog with two root folders, one with `relativePathFromCatalog` `../photos/`, gets four `catalog_root` rows (`absolute` and `relative` for each) with no trailing slash and the `relative` `path_norm` collapsed; a root folder whose `relativePathFromCatalog` is NULL gets its `absolute` row only, and no `via='relative'` `catalog_root` row and no `via='relative'` record; one whose `relativePathFromCatalog` is the empty string gets a `relative` row equal to the source's own folder; the stored rows equal `CatalogRead.roots`.

Commit subject: `feat(lrdup): Add catalog discovery, snapshots and record reading`

---

### Task C: `inventory.py`

`Wave:` 2 · `Touches:` lrdup/inventory.py, tests/test_inventory.py · `After:` A, K · `Mode:` code

**Behaviour:** rules 1 (scan records), 4, 7 (attribute recorded, never opened), 10, 11, 14, 15. Walks each root with `os.scandir`, pruning `own_dir` and `exclude` before descending, never following links or junctions, and stores one `file` row per file whose extension is in `PHOTO_EXTS | SIDECAR_EXTS`, using `os.stat` for `file_index` and `nlink`. Container typing needs the catalogs' records, so it refuses to run without a catalogs scan of every configured catalog root.

**Interfaces:**
- `class NoCatalogScan(RuntimeError)`.
- `scan_files(conn, roots: list[str], *, catalog_roots: list[str], exclude: list[str], own_dir: str, now=store.utc_now, attrs_of=None, scandir=os.scandir) -> list[int]` (scan ids, `kind='files'`). Before anything is stored it raises `NoCatalogScan` naming the root unless `latest_scans(conn, 'catalogs')` holds one scan for each of `catalog_roots` (compared with `paths.norm`). A root equal to or inside an earlier root of the same call raises `ValueError`. `attrs_of(path: str, st: os.stat_result) -> int`; `None` means `st.st_file_attributes`. A root that does not exist: scan `not_connected`, no rows. A folder that cannot be listed, or a file whose `os.stat` raises `OSError`: the marker row of the schema conventions, scan `partial`. An exception (including `KeyboardInterrupt`) during the walk: scan `partial`, rows already stored kept, then re-raised. Otherwise `complete`. Everything is read through `paths.fs_path`; `path_orig` is stored without the prefix. A directory is entered only when `entry.is_dir(follow_symlinks=False)` is true and `entry.is_symlink()` and `entry.is_junction()` are false.
- Container typing (rule 11), by `.lrdata` folder name compared case-folded: `mobile downloads.lrdata`, or a folder under which any `record` of an `ok` or `partial` catalog of the latest catalogs scans lies: `storage`; a name ending `previews.lrdata` or `smart previews.lrdata` with no record under it: `cache` (one summed row, contents not inventoried); anything else: `unknown` (sized, photos inventoried). Photos inside a container carry its `container` value.

**Acceptance:**
1. Rule 1 and the interrupted-scan bullet: a missing root is `not_connected` with no rows; an `attrs_of` that raises `KeyboardInterrupt` after two files leaves the scan `partial` with those rows kept; a `scandir` that raises for one folder gives `partial` plus the marker row and the other folders' rows; a file whose `os.stat` raises `OSError` gives the marker row and `partial`; a clean walk is `complete`; a second root inside the first raises `ValueError` and stores nothing.
2. Rule 10: a real hard link gives two rows with the same `volume` and `file_index` and `nlink == 2` (values from `os.stat`, not `DirEntry.stat`).
3. Rules 4 and 7: a file at a 261-character path is inventoried with an unprefixed `path_orig`; an `attrs_of` returning `0x1000`, `0x40000` or `0x400000` stores `placeholder=1` and `attrs`, and `0x20` stores 0.
4. Rule 11 and the outside-review bullet: a raw in `Mobile Downloads.lrdata` is inventoried with `storage`; a `Shoot Previews.lrdata` with no record is one `cache` row with the summed size and no row per content file; the same folder with a record under it is `storage`; `Other.lrdata` is `unknown` with its photos inventoried; a raw in a container inside a container carries the innermost one.
5. Rules 14 and 15: an injected `scandir` records every folder it is asked to list, and `own_dir` and each excluded folder are not among them (given with a different case and with backslashes; `x` does not prune `x2`); files under `own_dir` (inside a scanned root) never appear; a directory symlink and a junction made with `mklink /J` (skip each when it cannot be made) are neither entered nor stored; the listing of path, size and mtime under the root is equal before and after a scan.
6. Order (spec rule 1): `scan_files` with no catalogs scan, or with one catalog root of two scanned, raises `NoCatalogScan` and stores no scan row.

Commit subject: `feat(lrdup): Add the inventory walk`

---

### Task D: `hashing.py`

`Wave:` 2 · `Touches:` lrdup/hashing.py, tests/test_hashing.py · `After:` A, K · `Mode:` code

**Behaviour:** rules 4, 7, 8 and 9. Sample hash (size, first 64 KB, last 64 KB) filters; a full SHA-256 is computed only where the rules below say so. The run is resumable. A placeholder is never opened. `hash_one` is the only code that writes a `hash` row.

**Interfaces:**
- `open_binary(path: str)` (opens `paths.fs_path(path)` read-only). `sample_hash(path: str, size: int, opener=open_binary) -> str`; `full_hash(path: str, opener=open_binary) -> str` (hex SHA-256).
- `hash_one(conn, file_row, *, want_full: bool, opener=open_binary, stat=os.stat, now=store.utc_now) -> str` returns `hashed`, `reused`, `skipped_placeholder`, `changed` or `unreadable`. Order: a placeholder row returns `skipped_placeholder` before anything is opened; the file is re-statted through `paths.fs_path`; when `paths.is_placeholder(getattr(st, 'st_file_attributes', 0))` is true it returns `skipped_placeholder` and opens nothing; otherwise size, mtime or `file_index` differing from the `file` row returns `changed` and writes nothing; a `hash` row whose `file_index, size, mtime_ns` equal the file row is valid and returns `reused` when it holds what `want_full` asks for; otherwise the missing hash is computed and written with `store.put_hash` (the sample is always set when the full is) and `hashed` returned; `OSError` returns `unreadable` and writes nothing. `KeyboardInterrupt` propagates after the rows before it are committed.
- `hash_files(conn, *, library_roots: list[str], backup_roots: list[str], opener=open_binary, stat=os.stat, now=store.utc_now) -> dict` with integer counts `sampled, full, skipped_placeholder, changed, unreadable, reused`. Only a root whose LATEST scan (`store.latest_scans`) is `complete` is hashed; a scan root in neither list is skipped. Files taken are photo extensions only, no `cache` or marker rows. On a library root every file is sampled and a file that collides on `(size, sample)` with another library file is hashed in full. On a backup root a file is sampled only when its size equals the size of some library photo file and is never hashed in full; a library file and its backup copy colliding trigger no full hash of either. Every file goes through `hash_one`.

**Acceptance:**
1. Rule 8: two files of equal size that differ only between the first and last 64 KB share a sample hash and differ in full hash; two files of equal size with different first bytes differ in sample; files up to 128 KB are covered whole; `full_hash` equals `hashlib.sha256` of the bytes.
2. Rule 7: a placeholder row is never opened (an opener that raises on it proves it), gets no hash row and is counted `skipped_placeholder`; a file whose row says `placeholder=0` but whose injected `stat` reports `st_file_attributes=0x400000` is counted `skipped_placeholder` and never opened.
3. Rule 9 and resume: a second run reads nothing for valid rows (`reused`); an opener that raises on the third file, then a rerun, completes only the missing files; a file replaced by another of the same size and mtime (`os.utime`) but a new `file_index` is `changed` and its old row is not trusted.
4. Rules 8 and 9 economy (C-16): unique library files get a sample hash and `full IS NULL`; library files that collide on `(size, sample)` get a full hash; same size alone triggers nothing; a backup file whose size matches no library photo is not opened; a backup file that does match is sampled and never full-hashed, even when it collides with a library file; a root not in either list, and a root whose latest scan is not `complete` (an older complete scan does not stand in), contribute nothing.
5. Rules 4, 7 and 9 (D5): `open_binary` and the re-stat go through `paths.fs_path` (a file at a 261-character path is hashed); the stream `open_binary` returns is read-only (its write raises `io.UnsupportedOperation`); an opener raising `OSError` for one file counts it `unreadable`, writes no row and the run continues; an opener raising `KeyboardInterrupt` stops the run after committing the files before it; `hash_one` returns each of its five statuses for the case that defines it.

Commit subject: `feat(lrdup): Add resumable sample and full hashing`

---

### Task E: `classify.py` and its planted defects

`Wave:` 2 · `Touches:` lrdup/classify.py, tests/test_classify.py, tests/mutants/classify.json · `After:` A · `Mode:` code

**Behaviour:** rules 1, 3, 5, 8, 9, 10, 12, 13. The only module that decides a verdict a later move depends on. Tests insert `scan`, `catalog`, `record`, `file` and `hash` rows literally; they import no other task.

**Interfaces:**
- `class RootState(NamedTuple)`: `root, scan_id (int | None), status` (`complete`, `partial`, `not_connected`, `not_scanned`; a `running` latest scan is `partial`). `root_states(conn, roots: list[str]) -> list[RootState]`, one per configured root in the given order, from the latest file scan of the root (compared with `paths.norm`); a root with no file scan is `not_scanned`; an older complete scan never stands in for a newer partial one.
- `class Coverage(NamedTuple)`: `complete: bool, limiting: list[str]`. `coverage(conn, *, catalog_roots: list[str], excluded_catalogs: list[str], stale_catalogs: Collection[str] = ()) -> Coverage`. Limiting causes, each a readable string: the `source_path` of each catalog of the latest catalogs scans with status `unreadable` or `partial` whose `paths.norm` is not in `excluded_catalogs`; a configured catalog root with no catalogs scan (`<root> (not scanned)`) or whose latest scan is `partial` or `not_connected` (`<root> (partial)`, `<root> (not connected)`); each path in `stale_catalogs` whose `paths.norm` is not in `excluded_catalogs` (`<path> (changed since its snapshot)`; an owner-excluded catalog never limits, stale or not). `empty` catalogs never limit.
- `recorded_norms(conn, *, excluded_catalogs: list[str]) -> set[str]`: `path_norm` of the records of `ok` or `partial` catalogs of the latest catalogs scans whose `source_path` is not in `excluded_catalogs` (compared with `paths.norm`), older states included.
- `inventoried_files(conn, roots: list[str], *, photos_only: bool = False) -> list[sqlite3.Row]`: `file` rows of the latest scan of each given root that is `complete`, excluding `cache` rows and `<unreadable>` markers; `photos_only` keeps `ext` in `PHOTO_EXTS` (sidecar rows are used only by F and H). `eligible_files(conn, *, library_roots: list[str]) -> list[sqlite3.Row]` is `inventoried_files(conn, library_roots, photos_only=True)`. A hash row is valid only when its `file_index, size, mtime_ns` equal the file row.
- `class Verdict(NamedTuple)`: `file_id, path_orig, size, klass, twin_file_id (int | None), group, recorded (bool), how_decided`; `group` is `<size>:<first 12 hex of the full hash>`, `<volume>:<file_index>` for `same_file`, or `''`. `verdicts(conn, *, library_roots, catalog_roots, excluded_catalogs, stale_catalogs=(), stat=os.stat) -> list[Verdict]`, one per eligible file; `stat` is called on `paths.fs_path(path_orig)`.
- Decisions: names sharing `(volume, file_index)` with a non-zero index are one file (`same_file`, never a duplicate of each other; `file_index` 0 never aliases). A file with a valid full hash and another file of equal size and full hash is identical (rule 8; equal sample alone is not). An unrecorded file whose identical twin is recorded, present, not a placeholder, not the same file and hash-valid is `safe_candidate` only when `coverage().complete`, else `coverage_incomplete` with `how_decided` "not recorded by the catalogs that could be read; coverage incomplete: " followed by the limiting causes joined by `;`. An unrecorded file with unhashed or stale-hashed collisions, or with no valid sample hash, is `not_compared`; a placeholder is `placeholder`; an unrecorded file that collides with no other library file on `(size, sample)`, or whose every `(size, sample)` collision has a valid full hash different from its own, is `unique_unrecorded`; identical groups with no recorded member give `needs_eye_no_recorded` to all and with two or more recorded members give `needs_eye_many_recorded` to those members (an unrecorded member of such a group is a `safe_candidate`). Rule 13: for each `safe_candidate`, the file and its twin are read with `stat`; a missing file, or a changed size, mtime or `st_ino`, gives `changed_since_scan` with a `how_decided` that says which of the file or its twin changed or is gone. A JPEG that has a same-base raw in its folder has "paired JPEG" in its `how_decided`; a pair never changes the raw's verdict (rule 12) and a JPEG is never the twin of a raw.
- `tests/mutants/classify.json`: `{"tests": "tests", "pattern": "test_classify*.py", "mutants": [{"name": "...", "edits": [{"file": "lrdup/classify.py", "old": "<one line>", "new": "<one line>"}]}]}`; five defects, each `old` a single line that occurs exactly once in its file, one per decision: the coverage guard, `recorded` membership, the identical test (full hash replaced by sample), the same-file guard, the placeholder guard on the keeper twin.

**Acceptance:**
1. Rule 1: a file under a root whose latest scan is `partial`, `running` or `not_connected`, or a root never scanned (`root_states` gives `not_scanned`), has no verdict even when an older complete scan exists; only the latest complete scan's files are classified; `eligible_files` returns no `cache` row, no `<unreadable>` marker and no sidecar row.
2. Rules 3 and 5 and the recorded bullets: a file is recorded by a case, slash or NFC variant, by the `relative` via, by a second catalog only, by a sidecar record, by an older-state catalog and by a `partial` catalog; not by an owner-excluded catalog; a same-named file in another folder, and a file in a folder named like a recorded one, are not recorded; an unreadable un-excluded catalog turns every would-be `safe_candidate` into `coverage_incomplete` naming it; the same catalog excluded by the owner, or an `empty` one, does not; a configured catalog root with no catalogs scan, or whose latest scan is `not_connected` or `partial`, makes coverage incomplete and every would-be `safe_candidate` `coverage_incomplete`; exclusion is taken from the `excluded_catalogs` argument: the same stored rows give a different verdict when the list changes; each path in `stale_catalogs` is a limiting cause and gives `coverage_incomplete`, except a path of a catalog in `excluded_catalogs`, which limits nothing.
3. Rules 8, 9, 10 and the identical bullets: identical content under two names is one group; same name with different content is not; same size and sample but different full hash is not a candidate; a stale hash row (changed index, size or time) gives `not_compared`; two names with the same non-zero `file_index` are `same_file` with group `<volume>:<file_index>` and never a candidate, while `file_index` 0 never aliases; a placeholder twin is rejected; a recorded copy is never a candidate.
4. Rules 4 and 13 and the resume bullets: with an injected `stat`, a missing twin, a changed size, mtime or index on the candidate or the twin, gives `changed_since_scan`; unchanged gives `safe_candidate`; a file replaced with the same size and time but a new index gives no safe verdict; a safe candidate and its twin at 261-character paths (real files, real `os.stat`) stay `safe_candidate`.
5. Sections 3, 4, 7 and rule 12: a group of three identical files with none recorded gives `needs_eye_no_recorded` to all three; with two recorded and one unrecorded the two get `needs_eye_many_recorded` and the unrecorded one is a `safe_candidate`; an unrecorded unique file is `unique_unrecorded`, an unrecorded file whose identical twin is also unrecorded and any `not_compared` file are not; an unrecorded raw whose same-base JPEG is recorded (as sidecar) and a raw whose same-base JPEG is unrecorded both keep the verdict they have with no JPEG beside them, and the JPEG's `how_decided` contains "paired JPEG"; every verdict has a non-empty `how_decided`; `Verdict.recorded` is true exactly for a file whose `path_norm` is in `recorded_norms`, and a JPEG is never the twin of a raw (a raw and a JPEG of equal size and hash stay unpaired as twins).
6. Planted-defect gate (E6): `python C:/ssw/tools/mutate.py tests/mutants/classify.json --jobs 3` run once in the task's worktree reports all five defects caught.

Commit subject: `feat(lrdup): Add the verdict engine with a planted-defect gate`

---

### Task F: `catalog_views.py` (sections 1, 2, 8)

`Wave:` 3 · `Touches:` lrdup/catalog_views.py, tests/test_catalog_views.py · `After:` A, E · `Mode:` code

**Interfaces:**
- `catalog_inventory_rows(conn, *, excluded_catalogs: list[str]) -> list[dict]` (section 1; the catalogs of the latest catalogs scans; `records` = count of every `record` row, whatever its type; `excluded_by_owner` yes or no by `paths.norm` comparison with `excluded_catalogs`).
- `date_matches(mtime_ns: int, capture_time: str) -> bool`: true when the difference is within one second of a whole number of hours, up to 14 hours either way; false for a capture time `datetime.fromisoformat` cannot parse.
- `shoot_catalog_rows(conn, *, library_roots: list[str], backup_roots: list[str], excluded_catalogs: list[str], exists=os.path.exists) -> list[dict]` (section 2). Catalogs with role `shoot` only; only records whose extension is in `PHOTO_EXTS` or `SIDECAR_EXTS` are considered and the `how_decided` text says other recorded types (video, TIFF, PNG and the like) were not checked, so such a record is never reported missing. A photo is the set of records of one catalog with one file name and `capture_time`; it is present when any of its records (either via) has an inventoried file on a complete library root (`classify.inventoried_files`); a photo under a root that is not complete, or under no scanned root, is neither present nor missing; its records are counted in `records_not_read` (photo and sidecar records only, in neither `records_present` nor any missing column) and the `how_decided` says so. A missing photo is classed in order: `missing_moved` (another catalog that is `ok` or `partial`, not owner-excluded, has a present record of the same file name with equal `capture_time`), `missing_same_name_only` (a file of that name is inventoried on a library root), else `missing_nothing`. Both leftover classes are looked up on the complete backup roots by file name and `date_matches`, counted in `backup_same_name_only_lower_bound` and `backup_nothing_lower_bound` (the `how_decided` text says "lower bound"); `uncatalogued_in_tree` = photo files on library roots under that catalog's root folders that are in no `classify.recorded_norms`; `root_folders` lists each `via='absolute'` row of `catalog_root` for the catalog as `path=exists` or `path=missing` (`exists` on its `path_orig`), joined by `;`.
- `missing_rows(conn, *, live_catalog: str | None, library_roots: list[str], backup_roots: list[str], drive_exists=os.path.exists) -> list[dict]` (section 8). A photo of the live catalog (the catalog whose `source_path` equals `live_catalog` by `norm`; same catalog, file name and `capture_time`, either via) is missing only when none of its records has an inventoried file on a complete root. `state` is `drive_not_connected` when the record's path lies under a root whose latest scan is `not_connected`, or its drive letter does not exist (`drive_exists` on the drive root); `not_read` when it lies under a partial or running root or under no scanned root (library and backup roots both count as scanned); `missing` only when it lies under a complete scanned root and no file exists for it. Only photo and sidecar types are checked.

**Acceptance:**
1. Section 1: every catalog is a row, the unreadable and the empty ones included, with role, status, version, record count (every type), `wal_bytes` and `has_lock` taken from the stored row; `excluded_by_owner` is `yes` only for a path in the list.
2. Section 2 and the moved-record bullet: counts split exactly across the three missing classes; a record under a `partial` root, or under no scanned root, is counted in `records_not_read` and in no other count column; a same-named file with a different capture time is `missing_same_name_only`, never moved; a missing record with a present same-name, same-capture-time record in a second catalog is `missing_moved`, but not when that catalog is owner-excluded, unreadable, or its file is not inventoried; `date_matches` accepts +1 h and -3 h offsets and rejects 30 minutes and an unparseable text; each `via='absolute'` `catalog_root` row shows its existence (a `via='relative'` row is not listed); backup lookup only for the two leftover classes, in their own columns; `uncatalogued_in_tree` counts unrecorded photo files under each root folder; the `how_decided` of the backup columns contains "lower bound"; a video or TIFF record is never missing and the `how_decided` says other types were not checked.
3. Section 8 and the missing bullet: a live-catalog record with no file is `missing`; one on a not-connected root is `drive_not_connected`, kept apart; a record under no scanned root, or on a drive letter that does not exist (injected `drive_exists`), is `drive_not_connected` or `not_read`, never `missing`; a photo present at its relative path is not missing; records of non-live catalogs are not listed.

Commit subject: `feat(lrdup): Add catalog inventory, shoot catalog and missing views`

---

### Task G: `backups.py` (section 5 and `verify`)

`Wave:` 3 · `Touches:` lrdup/backups.py, tests/test_backups.py · `After:` A, D, E · `Mode:` code

**Interfaces:**
- `external_copy_rows(conn, *, photo_root: str, group_depth: int, library_roots: list[str], backup_roots: list[str]) -> list[dict]` (section 5). `year` rows: raws on library roots grouped by the first `group_depth` folder levels under `photo_root`; raws on a library root outside `photo_root` form one group `(outside photo root)`; a raw with fewer than `group_depth` levels under `photo_root` goes to `(top of photo root)`. A raw has a copy on a complete backup root when size and sample hash are equal (`copied_sampled`), upgraded to `copied_verified` when both rows hold a valid, equal full hash; both full hashes valid and different means no copy; `raws = copied_sampled + copied_verified + no_copy`; `backup_roots` text is `root=count` joined by `;`. `backup_only` rows: per backup root and top folder, raws that match no library raw.
- `verify(conn, folder: str, *, library_roots: list[str], backup_roots: list[str], opener=hashing.open_binary, stat=os.stat, now=store.utc_now) -> dict` with counts `verified, mismatch, no_copy, skipped_placeholder, changed, unreadable`: calls `hashing.hash_one(want_full=True)` for the library raws under `folder` and their sample-matched copies on backup roots and for nothing else; it writes no `hash` row itself.

**Acceptance:**
1. Section 5 sampled: equal size and sample is `copied_sampled`; equal size with a different sample, or equal size alone, is `no_copy`; counts are per group and name the backup root; `group_depth` 1 and 2 give different groups for the same tree; a raw outside `photo_root` and one with too few levels land in the two named groups.
2. Rule 8 in `verify`: a copy equal in size and sample but different in the middle bytes is `mismatch`, not `verified`, and the next `external_copy_rows` counts it `no_copy`; an exact copy becomes `copied_verified` in the next `external_copy_rows`; a raw outside `folder` is not hashed.
3. Rule 1: a `not_connected`, `partial` or never-scanned backup root contributes no copy and is not counted as "no copy found"; its raws are not in `backup_only`; a raw whose only possible copy would be on such a root is counted in `no_copy` and the `how_decided` of the row names the unread backup roots.
4. Rules 4, 7 and 9 and `backup_only` (G4): `verify` never opens a placeholder (counted `skipped_placeholder`), re-stats each file through `paths.fs_path` and skips a changed one (counted `changed`), and counts an `OSError` as `unreadable`; a `backup_only` row is produced for a backup raw that matches no library raw, with its top folder and bytes.

Commit subject: `feat(lrdup): Add external-copy view and verify`

---

### Task H: `file_views.py` (sections 6, 9, 10, 11)

`Wave:` 3 · `Touches:` lrdup/file_views.py, tests/test_file_views.py · `After:` A, E · `Mode:` code

**Interfaces:**
- Sections 6, 9 and 10 read `file` rows only of the latest scan of each library root, and only when `classify.root_states` gives `complete`.
- `pair_rows(conn, *, library_roots: list[str]) -> list[dict]` (section 6): per folder, a JPEG and a raw with the same case-folded base name; the JPEG side is `sidecar` when a record with `is_sidecar=1` has its path, `own_photo` when a record with `is_sidecar=0` has it, else `uncatalogued`. `same_time_pairs` and `same_time_bytes` count the pairs (of any of the three kinds) whose raw and JPEG `mtime_ns` differ by at most two seconds, and the bytes of their JPEG side; no file is opened for this. The `how_decided` says a pair written together is a sign of a camera JPEG and that a pair with other times may be an export.
- `container_rows(conn, *, library_roots: list[str], coverage_complete: bool) -> list[dict]` (section 9): containers found on library roots only; `reclaimable` is `yes` only for `cache` while `coverage_complete`, `unverified` for a `cache` while it is not (the `how_decided` then says coverage incomplete), else `no`; `catalog_beside` is the catalog whose stem begins the container name in the same parent, with its stored status, else `none`.
- `loose_rows(conn, *, library_roots: list[str], photo_root: str) -> list[dict]` (section 10): photo files (raw or JPEG) on a library root outside `photo_root`, per folder; any path with a component in `APP_FOLDER_NAMES` (`program files`, `program files (x86)`, `programdata`, `appdata`, `windows`, `node_modules`, `.git`, `site-packages`, case-folded) is one row per such folder with `listed='no'`.
- `not_read_rows(conn, *, library_roots: list[str], backup_roots: list[str], catalog_roots: list[str], excluded_catalogs: list[str], stale_catalogs: Collection[str] = ()) -> list[dict]` (section 11): placeholders and `<unreadable>` marker rows of the latest file scans of those roots, each root from `classify.root_states` that is `partial` (`partial_root`) or `not_connected` (`not_connected_root`), each backup root that is `not_scanned` (`not_scanned_root`, detail `not scanned`; a library root with no files scan never reaches this function, `report` exits 4 first), and one `limiting_catalog` row per cause in `classify.coverage(...).limiting`. It takes no verdicts.

**Acceptance:**
1. Rule 12 and section 6: pairs are found case-insensitively; a JPEG with another base name or in another folder is not a pair; a pair on a backup root is not listed; the three-way split sums to the pair count and bytes; a pair whose raw and JPEG `mtime_ns` are one second apart is counted in `same_time_pairs` and `same_time_bytes`, a pair one hour apart is not, and both stay in `pairs`.
2. Rule 11 and section 9: the `storage` and `unknown` containers are `reclaimable=no`, a `cache` is `yes` with its summed bytes; with `coverage_complete=False` the cache reads `unverified`; a cache on a backup root is never listed; the catalog beside carries its status.
3. Section 10: a raw inside `photo_root` is not loose, one outside is; a file under a backup root is never loose; application folders are counted with `listed='no'` and never listed per file.
4. Rule 7 and section 11: a placeholder, an unreadable-folder marker, a `partial` root, a backup root never scanned (`not_scanned_root`, never a `partial_root`), a `not_connected` root and a limiting catalog (including a not-scanned catalog root and a stale catalog) each give one row of their `kind`; an `empty` catalog gives none.

Commit subject: `feat(lrdup): Add pair, container, loose-photo and not-read views`

---

### Task I: `report.py`

`Wave:` 2 · `Touches:` lrdup/report.py, tests/test_report.py · `After:` A · `Mode:` code

**Interfaces:** `SECTIONS: dict[int, Section]` where `Section(number: int, filename: str, title: str, columns: tuple[str, ...])` carries the file names and columns frozen in Dispatch (each with `how_decided` last). `write_report(out_dir: str, sections: Mapping[int, Sequence[Mapping]], coverage: Mapping, limits: Mapping[int, str]) -> None`. `coverage` is `{"roots": [{"root", "status"}], "catalogs": [{"catalog", "status", "limits_coverage": bool}], "limiting": [str]}`; `limits` holds, per section number 1 to 11, a sentence on what it could not see (a missing key is `KeyError`). Rows keep the order given. The writer validates every section first and raises `ValueError`, writing no file at all, for a row whose keys differ from the columns or whose `how_decided` is empty. `summary.md` opens with a "Coverage" heading (roots and catalogs read, not read, limiting causes), then one block per section with its row count, the sum of its `bytes` column when it has one, and its limits sentence. For a section with `folder` and `bytes` columns the block adds a "by folder" table of its ten largest folders (bytes summed per `paths.norm(folder)`, shown in the first spelling, largest first); for section 9 it prints the total size and, separately, "reclaimable" = the sum of `bytes` of rows with `reclaimable=yes` only; for section 4 it prints a count per `kind`.

**Acceptance:**
1. Output rules: each CSV starts with the UTF-8 BOM, a non-ASCII file name round-trips through `csv.reader`, `summary.md` contains no `\r`, and exactly the eleven named files plus `summary.md` appear.
2. Every row has a non-empty `how_decided`; a row with a missing, extra or renamed column, or an empty `how_decided`, in any one section raises `ValueError` and no file at all is written.
3. The summary's first heading is coverage and names a `partial` root, a limiting catalog and a limiting cause; an empty section still prints totals of 0 and its limits sentence; the totals equal the sums of the rows given.
4. Section totals: section 9's summary gives the total size and, separately, the reclaimable total = sum of `bytes` of `reclaimable=yes` rows only (a `storage`, `unknown` or `unverified` row adds nothing); a section with `folder` and `bytes` gets a by-folder table of its ten largest folders (an eleventh folder is absent; folders differing only by case add up); section 3's by-folder table is present; section 4 prints a count per `kind`.
5. Two writes of the same input are byte-identical.

Commit subject: `feat(lrdup): Add the CSV and summary writer`

---

### Task M: `verdict_views.py` (sections 3, 4, 7)

`Wave:` 3 · `Touches:` lrdup/verdict_views.py, tests/test_verdict_views.py · `After:` A, E · `Mode:` code

**Behaviour:** turns `classify.Verdict` lists into the three section row lists. Tests build `Verdict` tuples literally.

**Interfaces:** `safe_candidate_rows(verdicts: Sequence[Verdict]) -> list[dict]` (section 3: `safe_candidate` verdicts only; `folder` from `paths.split_path`; `twin_path` from the verdict whose `file_id` is `twin_file_id`). `needs_eye_rows(verdicts) -> list[dict]` (section 4: every verdict whose class is `needs_eye_no_recorded` (`no_recorded_member`), `needs_eye_many_recorded` (`many_recorded`), `same_file`, `coverage_incomplete`, `changed_since_scan` or `not_compared`, with `kind` the name in brackets or the class; `recorded` yes or no from `Verdict.recorded`). `large_unreferenced_rows(verdicts, *, coverage_complete: bool) -> list[dict]` (section 7: per folder, largest first, of `unique_unrecorded` files only; while coverage is incomplete every `how_decided` says so). Each row's `how_decided` is the verdict's.

**Acceptance:**
1. Section 3: only `safe_candidate` verdicts appear, with exactly the frozen columns, the twin's path and the folder; no other class leaks in.
2. Section 4 (rule 10 and rule 5): each of the six kinds appears for a verdict of its class and no other class does; a `same_file` row's `group` is `<volume>:<file_index>`; a `coverage_incomplete` row's `how_decided` starts "not recorded by the catalogs that could be read; coverage incomplete".
3. Section 7: per-folder `files` and `bytes` are right and largest first; a `recorded`, `safe_candidate`, `needs_eye_*` or `not_compared` verdict adds nothing; with `coverage_complete=False` every row says coverage incomplete.
4. Every row has a non-empty `how_decided`; two calls give equal output.

Commit subject: `feat(lrdup): Add the safe-candidate, needs-eye and large-unreferenced views`

---

### Task J: `cli.py` and `__main__.py`

`Wave:` 4 · `Touches:` lrdup/cli.py, lrdup/__main__.py, tests/test_cli.py · `After:` A, B, C, D, E, F, G, H, I, K, M · `Mode:` code

**Interfaces:** `load_config(path: str) -> dict` requires the nine keys `library_roots, backup_roots, catalog_roots, photo_root, group_depth, exclude, live_catalog, old_states, excluded_catalogs` (`live_catalog` may be `null`; `group_depth` is an integer of at least 1 and a boolean is refused) and raises `ConfigError(key)` naming the first missing or mistyped key. `main(argv: list[str] | None = None, *, processes=catalogs.running_processes, now=store.utc_now, stat=os.stat, attrs_of=None, exists=os.path.exists) -> int`. Global option `--base DIR` (default the current folder): the tool's folder, holding `work/config.json`, `work/lrdup.sqlite`, `work/catalogs/` and `out/`, always added to `exclude` and used as `own_dir`. Subcommands: `catalogs`, `inventory`, `hash`, `report`, `verify FOLDER`; exit 0 ok, 2 configuration error (a missing, unreadable or non-JSON file too) naming the key or the file, 3 Lightroom running, 4 a required earlier step has not run (message names it). `inventory` passes `catalog_roots` to `scan_files` and maps `NoCatalogScan` to exit 4. `report`: computes `stale_catalogs` with `catalogs.is_stale` for every stored catalog of the latest catalogs scans (a source that cannot be statted counts as stale); exits 4 with the message "run inventory again" when the id of the latest catalogs scan of any catalog root is greater than the id of the latest files scan of any library root, or a library root has no files scan; assembles the eleven sections (E verdicts, M, F, G, H) with `coverage` built from `classify.root_states` and `classify.coverage`, where `catalogs` has one entry per catalog of the latest catalogs scans and `limits_coverage` is true for an `unreadable` or `partial` catalog not in `excluded_catalogs` and for a catalog whose `source_path` is in `stale_catalogs`; gives each section a `limits` sentence (section 1 says it counts every recorded file type; sections 2 and 8 say other recorded types were not checked); calls `report.write_report`. `__main__.py` is `sys.exit(cli.main())`.

**Acceptance:**
1. Configuration (C-10): for each key removed in turn `main` exits 2 and the message names that key; a root given as a string instead of a list is refused; a boolean or 0 for `group_depth` is refused; a missing, unreadable or non-JSON config file exits 2 with a message; with a complete file nothing is defaulted.
2. Order (spec rule 1): `catalogs` exits 3 and writes no snapshot while `processes` lists Lightroom; `inventory` before `catalogs` (no catalogs scan of every configured catalog root) exits 4 with a message, not a traceback; `verify` and `report` before `inventory` exit non-zero with a message.
3. Rule 1 (C-26): after `catalogs`, `inventory`, then `catalogs` again, `report` exits 4 with "run inventory again" and writes nothing (the comparison is of scan ids, with `now` injected and equal for all scans); a library root with no files scan also gives exit 4 and no output.
4. Wiring: with injected `processes`, `now`, `stat`, `attrs_of` and `exists`, a small synthetic tree with one catalog runs `catalogs`, `inventory`, `hash`, `report` and exits 0 each time, `out/` holds the eleven files and `summary.md`, and `--base` inside a scanned root is excluded from the walk (rule 14).

Commit subject: `feat(lrdup): Add the command line`

---

### Task L: end-to-end tests

`Wave:` 5 · `Touches:` tests/test_e2e.py · `After:` J, K · `Mode:` code

**Behaviour:** the reviewer's run before the tool reads the real library: the real modules joined on one synthetic tree, through `cli.main`.

**Acceptance:**
1. End to end (C-13): one synthetic tree with a live and a shoot catalog, an unreadable catalog listed in `excluded_catalogs` (so section 3 can have its row), an identical pair (one recorded), a hard link, a Mobile Downloads container, a preview cache, a raw+JPEG pair, a loose photo, a placeholder (through `attrs_of`) and a backup root holding a copy; every subcommand in order; one expected row is asserted in each of the eleven CSVs, and `verify` turns the copy to "verified".
2. Rule 5 end to end: a second `report` with `excluded_catalogs` emptied gives an empty section 3, the same file in section 4 as `coverage_incomplete` naming that catalog, and a summary that names it; after the mtime of the live (readable, not excluded) catalog's source changes, `report` has no safe candidate and section 11 names the catalog (`limiting_catalog`).
3. Rule 13 end to end: after `hash`, delete the keeper twin of a safe candidate (real file system, real `os.stat`), then `report`: the row is not in section 3 and appears in section 4 as `changed_since_scan`.
4. Rules 14 and 15: the listing (path, size, mtime) of every library, backup and catalog root, excluding the base folder, is equal before and after the whole run, with the base folder inside a scanned root; a second full run gives the same catalogs, no snapshot in the inventory and byte-identical CSVs.

Commit subject: `test(lrdup): Add the end-to-end tests`

---

## Spec coverage

| Spec item | Task |
|---|---|
| Rule 1 latest complete scan; never-scanned root; inventory before catalogs | E (verdicts, `root_states`), C (scan status, order), D (hashing), G (backup roots), J, L |
| Rule 2 record paths, sidecars, capture time, `..` collapsed | B |
| Rule 3 recorded = normalised equality, never by name | A (`norm`), E |
| Rule 4 long paths through `fs_path` | A, C, D, E (rule-13 stat), F (`exists`), G (`verify`) |
| Rule 5 catalog coverage (unreadable, not scanned, unlistable, stale) | B (statuses, `find_catalogs`), E (`coverage`), H (section 11), J (stale), L |
| Rule 6 snapshots, staleness | B, J (stale check at report time) |
| Rule 7 placeholders | A (`is_placeholder`), C, D (`hash_one`), G (`verify`), H |
| Rule 8 identical = size + full hash | D, E, G |
| Rule 9 hash rows are an optimisation | D, E |
| Rule 10 same file, two names, reported | C, E, M (section 4 `same_file`) |
| Rule 11 `.lrdata` containers | C, H |
| Rule 12 RAW+JPEG pair | E, H |
| Rule 13 fresh at report time | E (injected `stat`), L (real `os.stat`) |
| Rule 14 own folder, pruned before the walk | B, C, J, L |
| Rule 15 read-only, links and junctions | C, L |
| Sections 1, 2, 8 | F |
| Sections 3, 4, 7 | E (decisions), M (rows) |
| Section 5 and `verify` | G |
| Sections 6, 9, 10, 11 | H |
| CSV, `summary.md`, coverage opening, totals, by-folder tables, what could not be seen | I, J |
| Run configuration, own folder always excluded, `group_depth` | J |
| Frozen schema (six tables, `catalog_root` included) | A (tables), B (writes `record` and `catalog_root`) |
| Synthetic trees and catalogs (spec module table: `tests/synth.py`; C-6 fixtures) | K |
| Bullets: identical under two names; same name different content; recorded copy never a candidate; second catalog; sidecar JPEG | E (B for the records) |
| Bullets: missing record; moved record by name and capture time | F |
| Bullet: unreadable catalog listed, run continues | B |
| Review bullets: Mobile Downloads vs preview container | C, H |
| Review bullets: second catalog unreadable gives no safe row | E, L |
| Review bullets: tool folder inside a scanned root | B, C, L |
| Review bullets: resumed run, interrupted scan, replaced file | D, E, C, L |
| Planted-defect gate | E (E6) |
| Reviewer's end-to-end run on the synthetic tree | L |
| Real run, listings, comparison with the probe | Controller steps |

## Controller steps

1. Before dispatch, write this plan's Gates block (unchanged) into the workspace `constraints.md`, commit the plan, and give each implementer the `context.md` rulings.
2. After each landing: send the change note of the Dispatch table, run `gate-runner` at that HEAD. Land a wave's patches (apply, commit, gate) before dispatching the next wave. After E lands, the `mutants-classify` gate must report every defect caught.
3. After L lands: the reviewer runs the end-to-end tests and one manual run on a synthetic tree; the controller reads the sections for one row each and checks the `how_decided` text is plain.
4. The real run, made by the controller with `work/config.json`, only with Lightroom closed and the owner told first: `catalogs`, `inventory`, `hash`, `verify` on a folder tree the owner picks, then `report`. Take a listing of path, size and time under every walked root before and after; they must be equal.
5. Compare the report's totals with the probe of 2026-11-11 (kept untracked); explain every difference before handing the report over. Cross-check with a general duplicate finder on one folder tree and note any group it finds that the report calls neither safe nor "needs your eye".
6. Edit the shared files (`CLAUDE.md`, `docs/**`, `README.md`) for anything the run teaches; none of them is a task's Touches.

## Not in this plan

- Phases B to D: no `mover.py`, no staging, copying, removing, catalog tidy or loose-photo move; no write to a catalog database.
- Moving the library out of a synced folder, choosing a cloud backup, a size survey of non-photo data.
- Any use of Lightroom's own duplicate finder (section 4 only names the work for it).
- An Adoption section, a TOOL line or any change to what a session does: those belong to the control-plane repository.
