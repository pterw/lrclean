# Phase B1 Backup Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `backup-plan`, `backup-run` and `backup-verify` to `python -m lrdup`: a manifest of every file in each photo folder that holds a raw, a never-overwriting, hash-verified copy of it to each archive root, and a full re-read that proves the copies.

**Architecture:** Three new modules. `lrdup/backup_plan.py` holds what plans: the manifest format, the archive-root checks, the planner and the plan writer. `lrdup/backup.py` holds what decides and writes: the destination check (rule 16), the copy of one row (rules 17 to 20), the journal, the stall watchdog and the exit code (rules 21 to 23), so the planted-defect gate covers one file. `lrdup/backup_verify.py` holds `backup-verify`. No module is meant to pass about 250 lines. `cli.py` is edited by F1 and F2 only, in successive waves. Tasks of one wave touch disjoint files.

**Tech Stack:** Python 3.13 standard library (`os`, `shutil`, `hashlib`, `threading`, `csv`, `json`, `unittest`); Windows paths.

**Spec:** `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase B1: backup copy" (rules 16 to 23 are cited by number). Controller rulings B-1 to B-18 are in the workspace `context.md` (B-12 supersedes the module layout of B-3, B-14 supersedes the first stop design, B-17 supersedes B-11 where it says `cli.py` has one editing task); phase A rulings C-1 to C-60 still bind the modules that exist.

Sections: Global Constraints=brief, Review Focus=reference, Dispatch=reference, Tasks=brief, Spec coverage=reference, Controller steps=reference, Not in this plan=reference

## Global Constraints

- Phase B1 only (B-1). The frozen schema does not change and no table is added (B-2); the journal is a file. No mover, staging, removal or undo. Nothing is written under a library root other than the tool's own folder (`--base`, rule 17); a destination is written only inside an archive root.
- Python 3.13, standard library only, `unittest`, LF files (`newline="\n"`). Tests run as `python -m unittest discover -s tests`; test files import `synth` and `lrdup.<module>`.
- PRIVATE DATA: no real path or name in code, tests, docs or reports. Fixtures are plainly synthetic (`shoot-a`, `IMG_0001.ARW`); every test builds its own tree in `tempfile.TemporaryDirectory()`, and its "archive root" is a temp folder. Nothing runs on a real folder.
- Reuse, do not rewrite (B-4): every stat, open, listing and existence test of a stored path goes through `paths.fs_path`; paths compare through `paths.norm`; the placeholder test is `paths.is_placeholder` on `st_file_attributes` or the `attrs_of(path, st)` seam of `inventory.py`; hashes are `hashing.full_hash` and `hashing.open_binary` (an opener may wrap them); raw files are `paths.RAW_EXTS`; the scan rows come from `classify.inventoried_files` and `classify.root_states`. Grep before adding a helper.
- Seams (B-5, B-6, B-14, B-16): volume of a path, free bytes, the clock, the poll interval, `attrs_of`, `stat`, the file opener, the part-file writer, the rename, `utime` (copy_row only) and `abort` are parameters with plain defaults. No test sleeps; the stall test takes well under a second.
- Fail closed: any doubt about a destination, a source or a hash refuses or fails the row; nothing is ever assumed identical.
- Known limit (B-18): containment (`is_inside`) compares `normpath` and `paths.norm`, not the real path. A junction or symlink that already lies inside an archive root and points into a library root is not detected.
- Manifest and verified-file CSVs: `encoding="utf-8-sig"`, `newline=""`. Journal and `backup-summary.md`: `newline="\n"`. Output goes to `out/`, the journal to `work/journal/`, both under the tool's `--base` folder.
- KISS: one test per behaviour, parametrized near-duplicates, each naming the rule or bullet it guards; loops linear in the number of files. Code tasks never commit; the controller lands each patch with the subject the task gives, no trailer. No pre-commit configuration exists; skip it.

## Review Focus

- A folder holding a raw and a dozen other files (`.xmp`, `.jpg`, `.txt`, no extension): every regular file is a row, not only photos.
- A source edited, replaced or made a placeholder between `backup-plan` and the copy, or during it: nothing wrong lands at `dest` (C1, C2).
- Any `OSError` in a copy is `failed` with the part removed; a plain error is never reported as a stall (C2, D2).
- A stall ends the process with 5 under the journal's lock, and no copy of a big file is mistaken for a stall while blocks arrive (C2, D2, F2).
- A rerun after a crash or stall: no duplicate copy, no refusal for lack of space, a leftover part file is removed (D2).
- A `dest` that escapes with `..`, differs only in case, or lies in a library root: refused whole (C1).
- A dry run, `backup-verify` and a refused manifest create no file and no folder; the database is not opened by `backup-run` and `backup-verify` (F2).

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | A | immediately | 1 code phase + 1 nested gate-runner = 2 |
| 2 | B1, C1, E | A landed (apply, commit, gate) | 3 + 3 nested + review of A = 7 |
| 3 | B2, C2 | B1, C1, E landed | 2 + 2 nested + reviews of B1, C1, E = 7 |
| 4 | D1, F1 | B2, C2 landed | 2 + 2 nested + reviews of B2, C2 = 6 |
| 5 | D2 | D1, F1 landed | 1 + 1 nested + reviews of D1, F1 = 4 |
| 6 | F2 | D2 landed | 1 + 1 nested + review of D2 = 3 |
| 7 | G | F2 landed | 1 + 1 nested + review of F2 = 3 |

Peak 7 (waves 2 and 3), cap 12. The controller lands a wave's patches before it dispatches the next, so every worktree starts at a BASE that already holds what its task imports; the tasks that edit one file (`backup_plan.py`: A, B1, B2; `backup.py`: C1, C2, D1, D2; `cli.py`: F1, F2) are in successive waves. Tests run on a task's own `After:` closure; engine and verify tests take parsed `Row` lists built literally, and Task G joins the real planner, engine, verify and CLI.

Landing order: A, B1, C1, E, B2, C2, D1, F1, D2, F2, G. Per patch: `git apply --check --index <patch>`, `git apply --index <patch>`, one commit with the task's subject, then `gate-runner` at that HEAD. Review: one `sdd-reviewer` per task with the same CONTEXT as its implementer. `lrdup/__init__.py` and `lrdup/store.py` are controller-only. The `mutants-backup` gate runs from C1 on: its `when` lists `lrdup/backup.py`, `tests/test_backup.py`, `tests/mutants/backup.json`, and its `pattern` is exactly `test_backup.py` (not `test_backup*.py`, which would also match phase A's `tests/test_backups.py`).

**Frozen interfaces.** An implementer that needs one changed returns NEEDS_CONTEXT.

*Configuration.* `archive_roots`: list of strings. Optional: when present it is type-checked by `load_config` for every subcommand (exit 2 naming the key); it is NOT in `KEYS`. `backup-plan`, `backup-run` and `backup-verify` require it and then call `check_archive_roots`; every failure is exit 2 naming `archive_roots`. `n` is 1-based everywhere: the root's position in `archive_roots` plus one. The stall limit is the option `--stall-seconds` of `backup-run` (positive number, default 120), not a configuration key.

*Manifest* `out/backup-manifest-<n>.csv`, columns `source, dest, bytes, mtime_ns, action, reason`: `bytes` and `mtime_ns` decimal ints from a fresh `os.stat`; `action` is `copy` or `skip_placeholder`; `reason` is empty for `copy` and `placeholder` for `skip_placeholder`; rows sorted by `paths.norm(source)`; `source` is `path_orig` of the folder joined with the entry name. `backup_plan.Row` is a `NamedTuple(source: str, dest: str, bytes: int, mtime_ns: int, action: str, reason: str)`. The summary is `out/backup-summary.md`.

*Verified file* `out/backup-verified-<n>.csv`, columns `source, dest, bytes, sha256, state`. `state`: `identical`, `different`, `missing_source`, `missing_dest`, `unreadable`, `source_placeholder` (a `copy` row whose source is now a placeholder; not opened), `skipped_placeholder` (a manifest row of that action). `sha256` is the source's, empty when the source was not read; `bytes` is the manifest's.

*Row states* of `backup-run` (strings in `backup.py`): `verified`, `already_there`, `conflict`, `changed`, `failed`, `skipped_placeholder`. Every `OSError` inside `copy_row` gives `failed` and removes a part file that exists; an error that is not an `OSError` is caught by the worker, which journals `end` with `failed` (B-13).

*Rule 20* is one helper, `backup.source_changed(row, *, stat=os.stat, attrs_of=None) -> bool` (true when the source cannot be stat'd, is now a placeholder, or differs from the row in size or `mtime_ns`), used by `copy_row`, the journal skip and `dry_run`; no other code repeats the test.

*Per-row order* (copy rows): remove a leftover `<dest>.lrdup-part`; rule 20 check of the source (`changed`); journal skip (`verified`, journal says verified for this `source`+`dest` and the destination's size and mtime equal the ones the journal recorded; its `Result.sha256` is the journal's); rule 18 (`already_there` or `conflict`); rule 19 copy (`verified`, `failed`, or `changed` when the source changed during the copy). A `skip_placeholder` row is `skipped_placeholder` and is never stat'd or opened.

*Journal* `work/journal/backup-<n>-<started>.jsonl`; callers pass `started = now()` unchanged and `journal_name` strips `-` and `:` (`20260101T000000Z`). Opened for append, one JSON object per line, every line has `event` and `at` (`now()`). `start`: `row` (0-based manifest row index), `source`, `dest`. `end`: `row`, `source`, `dest`, `state`, `sha256` (empty unless `verified` or `already_there`), `dest_bytes`, `dest_mtime_ns` (the destination's `os.stat` after the row, 0 when absent). `stalled`: `row`, `source`, `dest`, `seconds`. `Journal.write` holds the journal's re-entrant `lock` for every line; its first write begins with a newline when the file is not empty and does not end in one. Every `copy` row gets `start` then `end`, except a row skipped on the journal's word, which writes nothing, and the row of a stall, which has `start` and `stalled`; `skip_placeholder` rows write nothing. A rerun reads every `work/journal/backup-<n>-*.jsonl`, ignores a line that is not valid JSON, and keys `end` lines with state `verified` by (`norm(source)`, `norm(dest)`).

*Stall (B-14, B-15).* One daemon worker thread runs the rows and is never joined; the caller is the watchdog; there is no pool and no stop event. On a stall the caller takes the journal's lock, writes `stalled`, and calls `abort(5)` while it still holds the lock; `abort` defaults to `os._exit`. Progress is `tick`, which `copy_row` calls through a wrapper around `opener` on every block it reads (the source, the read-back of the part file, both hashes of rule 18) and before and after the fsync. Limit: the fsync's own duration is not covered, so one fsync longer than `--stall-seconds` ends the run with 5; the rerun is safe and the owner raises the option.

*Exit codes* (B-7): `0` ok, `2` configuration or manifest refused (a malformed manifest file too), `3` and `4` keep their meaning (`backup-plan` uses 4 when no library root has a complete files scan), `5` stalled, `6` finished with a `conflict`, `changed` or `failed` row (`backup-verify`: any `copy` row not `identical`). A dry run exits 0 unless refused.

*`cli.main` seams (B-16).* `volume_of=paths.volume`, `free_bytes=backup_plan.free_space` (F1); `clock=time.monotonic`, `opener=hashing.open_binary`, `writer=backup.open_part`, `rename=os.rename`, `abort=os._exit` (F2); all carried in `_Run`. `main` passes its existing `stat` and `attrs_of` to the planner, the dry run, the run and the verify step.

Change notes (sent by the controller when a task lands, to tasks running or staged):

| Task | Changes | Who is told |
|---|---|---|
| A | new `lrdup/backup_plan.py`: `Row`, `ManifestError`, manifest I/O, `is_inside`, `check_archive_roots`, `archive_index`, `free_space` | B1, B2, C1, E |
| B1 | `backup_plan.py` gains `RootPlan`, `BackupPlan`, `plan_manifests` | B2, F1 |
| B2 | `backup_plan.py` gains `write_plan` | F1 |
| C1 | new `lrdup/backup.py`: states, `check_manifest`, `source_changed`; new `tests/mutants/backup.json` (two defects) | C2, D1, D2, F2 |
| C2 | `backup.py` gains `open_part`, `ticked`, `copy_row`; `backup.json` has four defects | D1, D2, F2 |
| D1 | `backup.py` gains `Result`, `RunOutcome`, `journal_name`, `read_verified`, `Journal`, `dry_run`, `exit_code`; `backup.json` gains a fifth defect | D2, F2 |
| D2 | `backup.py` gains `run_manifest` | F2 |
| E | new `lrdup/backup_verify.py`: `Verified`, `verify_manifest`, `write_verified`, `verify_exit_code` | F2 |
| F1 | `cli.py` gains the `archive_roots` check, `_require_archive_roots`, `backup-plan`, seams `volume_of`, `free_bytes` | F2 |
| F2 | `cli.py` gains `backup-run`, `backup-verify` and the other `main` seams | G |

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
- name: mutants-backup
  run: python C:/ssw/tools/mutate.py tests/mutants/backup.json --jobs 3 --timeout 300
  pass: exit 0
  quote: first line
  when: any of lrdup/backup.py, tests/test_backup.py, tests/mutants/backup.json changed
```

## Tasks

### Task A: manifest format and archive-root checks

`Wave:` 1 · `Touches:` lrdup/backup_plan.py, tests/test_backup_plan.py · `After:` none · `Mode:` code

**Behaviour:** the shared manifest vocabulary and the configuration checks, with no filesystem write other than `write_manifest`.

**Interfaces:**
- `COLUMNS = ("source", "dest", "bytes", "mtime_ns", "action", "reason")`, `VERIFIED_COLUMNS = ("source", "dest", "bytes", "sha256", "state")`, `Row` (Dispatch), `class ManifestError(ValueError)`.
- `write_manifest(path: str, rows: Sequence[Row]) -> None`: creates the parent folder, always writes the header, rows in the order given. `read_manifest(path: str) -> list[Row]`: raises `ManifestError` when the file cannot be read, the header differs from `COLUMNS`, `bytes` or `mtime_ns` is not an integer, `source` or `dest` is empty, or `action` is not `copy` or `skip_placeholder`.
- `is_inside(path: str, root: str, *, below_only: bool = False) -> bool`: both go through `os.path.normpath` (so `..` collapses) and then `paths.norm`, not the real path (known limit, Global Constraints); true when equal (unless `below_only`) or below `root` on a path-segment boundary (`c:/a/b` is not inside `c:/a/bc`).
- `check_archive_roots(archive_roots: list[str], *, library_roots: list[str], own_dir: str, volume_of=paths.volume) -> None`: raises `ValueError` naming the offending root when the list is empty, a root is not absolute, a root is equal to or inside a library root or `own_dir`, or two roots have the same `volume_of(os.path.abspath(root))` (an empty volume counts as shared: fail closed).
- `archive_index(rows: Sequence[Row], archive_roots: list[str]) -> int`: the 1-based `n` of the one archive root that holds every `dest` strictly below it; `ManifestError` for no rows, a relative `dest`, a `dest` in no archive root, or `dest`s in two roots.
- `free_space(path: str) -> int`: `shutil.disk_usage` free bytes of the nearest existing ancestor of `path`, so a root that does not exist yet still answers.

**Acceptance:**
1. Manifest I/O (`backup-plan` columns bullet): a round trip returns equal rows including a comma, a quote and a non-ASCII name; the file starts with the UTF-8 BOM; each of a wrong header, a non-integer `bytes`, an unknown `action`, an empty `dest` and a missing file raises `ManifestError`.
2. Configuration (spec "Configuration"): `check_archive_roots` refuses an empty list, a relative root, a root equal to a library root, inside a library root, equal to and inside the tool's folder (each separately, also spelled with other case, backslashes and `..`), and two roots on one volume (`volume_of` injected so two temp folders differ or agree); it accepts two distinct roots on different injected volumes; a root merely sharing a name prefix with a library root (`lib` vs `lib2`) is accepted.
3. `archive_index` and `is_inside`: the index is 1-based and follows `archive_roots` order; a `dest` of `<root>/../x`, a `dest` equal to the root, a `dest` in a sibling folder sharing the root's name prefix, and `dest`s split over two roots are each refused; `free_space` of a folder that does not exist equals that of its existing parent (compared with `shutil.disk_usage`).

Commit subject: `feat(lrdup): Add the backup manifest format and archive-root checks`

---

### Task B1: the backup planner (`backup-plan`)

`Wave:` 2 · `Touches:` lrdup/backup_plan.py, tests/test_backup_select.py · `After:` A · `Mode:` code

**Behaviour:** the `backup-plan` bullets, up to the in-memory plan. Tests build a temp tree and scan it with the real `inventory.scan_files(conn, roots, catalog_roots=[], exclude=[], own_dir=...)` (it needs no catalogs scan when `catalog_roots` is empty).

**Interfaces:**
- `class RootPlan(NamedTuple)`: `archive_root: str`, `rows: list[Row]`, `folders: int`, `files: int` (all rows), `bytes: int` (of `copy` rows), `placeholders: int`, `left_out: int` (subfolders not selected), `free: int`. `class BackupPlan(NamedTuple)`: `roots: list[RootPlan]` (in `archive_roots` order), `outside_raws: int`, `unread_roots: list[str]`, `unlisted: int`.
- `plan_manifests(conn, *, library_roots: list[str], photo_root: str, archive_roots: list[str], own_dir: str, attrs_of=None, scandir=os.scandir, stat=os.stat, free_bytes=free_space) -> BackupPlan`. Library roots whose latest files scan is not `complete` (`classify.root_states`) are listed in `unread_roots` and contribute nothing; when every library root is unread it raises `ManifestError` (the CLI maps it to exit 4). A folder is selected when it equals or lies below `photo_root` (`is_inside`), is not inside `own_dir`, and a `classify.inventoried_files(conn, <read roots>, photos_only=True)` row with `ext` in `paths.RAW_EXTS` has it as its parent folder (`paths.split_path`): by content, never by name. A photo-storage `.lrdata` folder that holds a raw may be selected like any other. Each selected folder is listed afresh through `scandir(paths.fs_path(folder))`: every entry that is a regular file and neither a symlink nor a junction becomes a row (any type); `stat` through `paths.fs_path`, attributes by `attrs_of(path, st)` or `st_file_attributes`; a placeholder is a `skip_placeholder` row with `reason` `placeholder`, never opened. A subfolder that is not itself selected and is not `own_dir` adds 1 to `left_out`; it is never descended. A folder that cannot be listed, or a file whose `stat` raises `OSError`, adds 1 to `unlisted` and yields no row. `dest` = `os.path.join(archive_root, os.path.relpath(source, photo_root))`. `outside_raws` counts raw rows of the read roots that are not inside `photo_root`. Rows are sorted by `paths.norm(source)`.

**Acceptance:**
1. Selection by content (bullet 1): a folder named like a shoot but holding no raw is not selected; an oddly named folder holding one raw is; a `.lrdata` folder holding a raw is; a raw directly in `photo_root` selects `photo_root`; a raw outside `photo_root` selects nothing and is counted in `outside_raws`; a folder inside `own_dir` is never selected even when a scan row names it (rows inserted literally).
2. Listed afresh (bullet 2): a file that was never scanned (a `.txt`, a file without extension, a `.xmp`) and one added after the scan are rows; a raw deleted after the scan is not; a subfolder holding a raw is selected on its own, one without is counted in `left_out` and none of its files is a row; a directory symlink or junction (skipped when it cannot be made) is neither a row nor a selected folder.
3. Placeholder and destination (bullets 3, 4): `attrs_of` returning `0x1000`, `0x40000` or `0x400000` gives `skip_placeholder`, and no file is opened (`builtins.open` patched to raise around `plan_manifests`); `0x20` gives `copy`; each `dest` is the archive root joined with the path relative to `photo_root`, differing per root; a path over 260 characters is planned (real file).
4. Summary figures and roots (bullet 5, rule 1): per-root `folders`, `files`, `bytes` (placeholder bytes excluded), `placeholders`, `left_out` and `free` (injected `free_bytes`) are exact; two archive roots give two `RootPlan`s with equal `source` columns; a library root whose latest scan is `partial` or never scanned is in `unread_roots` and gives no row even when an older complete scan exists; all roots unread raises `ManifestError`.
5. Nothing is copied and the library is untouched: before and after `plan_manifests` the listing (path, size, mtime_ns) of the library root is equal and no archive root was created; two plans of the same tree give equal, sorted row lists.

Commit subject: `feat(lrdup): Add the backup planner`

---

### Task B2: the plan writer

`Wave:` 3 · `Touches:` lrdup/backup_plan.py, tests/test_backup_summary.py · `After:` B1 · `Mode:` code

**Behaviour:** the files of `backup-plan`. Tests build a `BackupPlan` literally; no scan is involved.

**Interfaces:**
- `write_plan(out_dir: str, plan: BackupPlan) -> list[str]`: writes `backup-manifest-<n>.csv` per root (`write_manifest`, `n` 1-based) and `backup-summary.md` (LF) giving, per archive root, folders, files, bytes, placeholders skipped, subfolders left out and free space, then `outside_raws` (left to phase D), `unread_roots` and `unlisted`; returns the paths written, and writes nothing else.

**Acceptance:**
1. Manifests (columns bullet): two roots give `backup-manifest-1.csv` and `-2.csv` in `archive_roots` order that `read_manifest` reads back equal to the plan's rows; the same plan written twice gives byte-identical files.
2. Summary (bullet 5): the text holds each per-root figure, `outside_raws`, every unread root and `unlisted`, and no `\r`.
3. Only the listed files: the returned paths are exactly the files created under `out_dir`, which is created when absent; nothing is created under an archive root.

Commit subject: `feat(lrdup): Add the backup plan writer`

---

### Task C1: destination check and the rule 20 helper

`Wave:` 2 · `Touches:` lrdup/backup.py, tests/test_backup.py, tests/mutants/backup.json · `After:` A · `Mode:` code

**Behaviour:** rule 16 and the shared rule 20 test for parsed rows. Tests build `Row`s literally from files in a temp tree; no scan is involved. Each decision is one expression on one line of `backup.py` so a defect can replace it.

**Interfaces:**
- State constants `VERIFIED = "verified"`, `ALREADY_THERE`, `CONFLICT`, `CHANGED`, `FAILED`, `SKIPPED_PLACEHOLDER`, `PART_SUFFIX = ".lrdup-part"`.
- `check_manifest(rows: Sequence[Row], *, archive_roots: list[str], library_roots: list[str], free_bytes=backup_plan.free_space, exists=os.path.exists) -> int` returns `n` (via `archive_index`) or raises `ManifestError` before any write, naming the first offending row: a `dest` not strictly inside the one archive root (relative, `..` escape, other root); a `dest` inside a library root (`is_inside`); two rows whose `dest` is equal after `normpath` and `norm`; or the sum of `bytes` of `copy` rows whose `dest` does not exist yet (`exists` on `paths.fs_path`) exceeding `free_bytes(archive_root)` (equal passes).
- `source_changed(row, *, stat=os.stat, attrs_of=None) -> bool` (Dispatch, rule 20); a `skip_placeholder` row is not its concern.
- `tests/mutants/backup.json` (format of `tests/mutants/classify.json`): `"tests": "tests"`, `"pattern": "test_backup.py"`, two defects, each `old` one line occurring once in `lrdup/backup.py`: the destination containment of rule 16 and the changed-source guard of rule 20. Run `python C:/ssw/tools/mutate.py tests/mutants/backup.json --jobs 3` once in the worktree: both caught.

**Acceptance:**
1. Rule 16: each of a `dest` outside every archive root, with a `..` escape, relative, equal to the root, and in a library root (a test whose archive root contains the library root), and two rows with one `dest` (also differing only in case or slashes), raises `ManifestError` and nothing is created; the same manifest passes when valid.
2. Rule 16, free space: bytes one above the injected free space raises, bytes equal to it passes; rows whose `dest` already exists do not count toward the bytes (a rerun on a full drive is not refused).
3. Rule 20: `source_changed` is true for a source that is gone, now a placeholder (`attrs_of`), one byte longer, or the same size with another `mtime_ns`, and false for an unchanged one; it creates nothing.
4. Planted defects: `mutate.py` on `backup.json` reports both defects caught.

Commit subject: `feat(lrdup): Add the backup destination check`

---

### Task C2: the copy of one row

`Wave:` 3 · `Touches:` lrdup/backup.py, tests/test_backup.py, tests/mutants/backup.json · `After:` C1 · `Mode:` code

**Behaviour:** rules 17 to 20 and B-13, B-15 for one parsed row. Tests build `Row`s literally from files in a temp tree. The hash comparison of rule 19 and the overwrite guard of rule 18 are each one expression on one line.

**Interfaces:**
- `open_part(path: str)`: opens `paths.fs_path(path)` with mode `"xb"` (a part file that exists is an error). `ticked(opener, tick)` returns an opener whose handle calls `tick()` after the open and after each `read`, and forwards everything else.
- `copy_row(row: Row, *, tick=lambda: None, stat=os.stat, attrs_of=None, opener=hashing.open_binary, writer=open_part, rename=os.rename, utime=os.utime) -> tuple[str, str]` returns (state, sha256). Every file is read through `ticked(opener, tick)`. `skip_placeholder` returns `skipped_placeholder` without any stat. Otherwise: `source_changed` (`changed`, nothing written); when `dest` exists both files are read with `hashing.full_hash` and the result is `already_there` (equal) or `conflict` (different), the destination untouched; else the destination's folder is created, the source is read once in chunks while written to `<dest>.lrdup-part` and hashed, the part is flushed, `tick()` is called, the part is `os.fsync`ed, `tick()` is called, the part is closed and read back with `hashing.full_hash`, its SHA-256 must equal the source's, the source is stat'd again and must still equal the manifest (else the part is removed and the state is `changed`), then `rename(part, dest)` (the default cannot replace a file on Windows) and `utime(dest, ns=(mtime_ns, mtime_ns))`. A hash mismatch removes the part and gives `failed`. Every `OSError` anywhere in `copy_row` (source open or read, creating the folder, the part write, flush, fsync, the read-back, the rename, `utime`) gives `failed` and removes a part file that exists; after a `utime` failure the verified `dest` stays and a rerun finds it `already_there`. The source is only ever opened through the opener (read-only).
- `tests/mutants/backup.json` gains two defects: the read-back hash comparison of rule 19 and the overwrite guard of rule 18 (identical becomes `already_there`); four in all, each caught.

**Acceptance:**
1. Rules 17 and 20: for every state `copy_row` can return (`verified`, `already_there`, `conflict`, `changed`, `failed`) the listing (name, size, mtime_ns, SHA-256) of the source tree is equal before and after, and the source handle (a real `rb` file wrapped by a recorder) receives no call but `read`, `__enter__` and `__exit__`; a source that is gone, a placeholder, longer or with another `mtime_ns` is `changed` with no folder, part or `dest` created; a `skip_placeholder` row is `skipped_placeholder` and the stat and the opener are never called.
2. Rule 18: with `dest` present and identical the state is `already_there`; with the same size and different bytes, or different bytes in the middle of a large file, `conflict`; in both the destination's bytes, size and mtime are unchanged and no part file exists.
3. Rule 19: a normal copy leaves `dest` equal to the source in bytes and `mtime_ns`, no part file and the state `verified` with the source's SHA-256; a writer that flips one byte gives `failed`, removes the part and leaves no `dest`; a source edited after the read and before the rename (via `tick`) gives `changed` and no `dest`; a `dest` created by someone else before the rename (Windows only, skipped elsewhere) is not replaced and the state is `failed`.
4. Errors (B-13), one case per site, each `failed` with no part file left: a source opener raising `OSError` at open, and at `read`; the folder of `dest` that cannot be created (a file stands where the folder must be); a writer raising on write and on flush; a `rename` raising; an unreadable existing destination; and a `utime` raising, which leaves `dest` equal to the source and a second `copy_row` giving `already_there`.
5. Progress (B-15) and defects: on a file of three blocks the recorded events show a `tick` after every block read in the source read, in the read-back and in each of the two hashes of rule 18, and a `tick` before and after the fsync; `mutate.py` on `backup.json` reports all four defects caught.

Commit subject: `feat(lrdup): Add the backup copy engine`

---

### Task D1: journal, dry run and exit code

`Wave:` 4 · `Touches:` lrdup/backup.py, tests/test_backup.py, tests/mutants/backup.json · `After:` C2 · `Mode:` code

**Behaviour:** the journal file, the dry run (B-8) and rule 23 for `backup-run`, without the run loop.

**Interfaces:**
- `class Result(NamedTuple)`: `row: int`, `source: str`, `dest: str`, `state: str`, `sha256: str`. `class RunOutcome(NamedTuple)`: `results: list[Result]`, `stalled: bool`.
- `journal_name(n: int, started: str) -> str` (Dispatch format; strips `-` and `:` from `started`). `read_verified(journal_dir: str, n: int) -> dict[tuple[str, str], tuple[int, int, str]]`: (`norm(source)`, `norm(dest)`) to (`dest_bytes`, `dest_mtime_ns`, `sha256`) of the last `verified` `end` line; a missing folder gives `{}`. `class Journal(path)`: attribute `lock` (`threading.RLock`); `write(event: str, **fields)` takes the lock, appends one JSON line (after a newline when the file is not empty and does not end in one, first write only), flushes and `os.fsync`s.
- `dry_run(rows, *, journal_dir, n, stat=os.stat, attrs_of=None, exists=os.path.exists) -> list[tuple[int, str]]`: per row, in the order of the per-row rules, `skip_placeholder`, `changed` (`source_changed`), `already_verified` (journal key and destination size and time match), `dest_exists` (apply would hash both) or `copy`; it reads, stats and lists only.
- `exit_code(outcome: RunOutcome) -> int`: `5` when stalled, else `6` when any state is `conflict`, `changed` or `failed`, else `0`. Written as one expression on one line.
- `tests/mutants/backup.json` gains a fifth defect: the exit-code decision of rule 23.

**Acceptance:**
1. Rule 21 file: a `Journal` writes lines with `event` and `at`; its file name has no `:`; two journals on one path append to one file; after a torn last line (no trailing newline) the next line starts on its own line and parses; two threads writing 200 lines each leave only whole, valid lines.
2. `read_verified` returns the recorded pair and SHA-256 of a `verified` `end` line, ignores a `failed` end, a `start`, a torn line and a line that is not JSON, reads every `backup-<n>-*.jsonl` of that `n` and no other, and gives `{}` for a missing folder.
3. Rule 23 and B-8: `exit_code` is 0 for a mix of `verified`, `already_there` and `skipped_placeholder`; 6 for each of `conflict`, `changed`, `failed` alone; 5 for a stalled outcome that also holds a `failed` row; `dry_run` reports each of its five verdicts, and creates no file or folder, the journal folder included (listing of the temp base equal before and after).
4. Planted defects: `mutate.py` on `backup.json` run once in the worktree reports all five defects caught.

Commit subject: `feat(lrdup): Add the backup journal and exit code`

---

### Task D2: run loop and stall watchdog

`Wave:` 5 · `Touches:` lrdup/backup.py, tests/test_backup.py · `After:` D1 · `Mode:` code

**Behaviour:** rules 21 and 22 for `backup-run --apply` (B-6, B-13, B-14). One worker thread runs the rows; the caller is the watchdog.

**Interfaces:**
- `run_manifest(rows: Sequence[Row], *, journal_dir: str, n: int, started: str, stall_seconds: float = 120, clock=time.monotonic, poll_seconds: float = 0.05, abort=os._exit, now=store.utc_now, stat=os.stat, attrs_of=None, opener=hashing.open_binary, writer=open_part, rename=os.rename) -> RunOutcome`. Creates `journal_dir`; for each row in order: a `copy` row first removes a leftover `<dest>.lrdup-part`, then is skipped as `verified` (the journal's SHA-256) when `read_verified` holds its key, the destination's size and `mtime_ns` equal the recorded pair and `source_changed` is false, else `start` is journaled, `copy_row` runs (`tick` updates the last-progress time) and `end` is journaled. The worker is a daemon thread that is never joined; it catches any exception of `copy_row`, journals `end` with `failed`, and sets `finished` in a `finally`, so an error is never reported as a stall; an exception outside `copy_row` (the journal) is kept and re-raised by the caller. The caller waits with `finished.wait(poll_seconds)`; when `clock() - last_progress > stall_seconds` it takes `journal.lock`, writes `stalled`, prints one line to stderr and calls `abort(5)` while still holding the lock; a test `abort` records its argument and raises, and an `abort` that returns makes `run_manifest` return `stalled=True` with the results so far.

**Acceptance:**
1. Rule 21: a run writes one `start` and one `end` per copy row with the fields of the Dispatch format, `end` carrying `verified` and the SHA-256; a second run of the same manifest copies nothing, writes no line and returns `verified` rows carrying the journal's SHA-256; after the destination's size or mtime changes, after a `conflict`, and after a source change, the row is not skipped; two runs with the same injected `now` append to one file.
2. Rule 22 and B-14: a worker blocked inside a read (an opener waiting on an event) with an injected clock calls `abort` with 5 in under a second; at that moment the journal's last line is `stalled` naming the row, and the journal lock is held (a second thread finds `acquire(blocking=False)` false); the row has no `end` line; a part file left behind is removed by the next run before it copies that row and the copy is `verified`.
3. B-13 and B-15: an opener raising `RuntimeError` for one row gives `end` `failed` for it and the later rows still copy, with no `abort` call and `stalled=False`; a copy whose blocks each advance a fake clock by 40 s against a 120 s limit, 200 s in all, is not stalled, and is stalled when one block advances it by 130 s.
4. The result list holds one `Result` per row in order, `skipped_placeholder` rows included, and a `skip_placeholder` row writes no journal line.

Commit subject: `feat(lrdup): Add the backup run loop and stall watchdog`

---

### Task E: `backup-verify`

`Wave:` 2 · `Touches:` lrdup/backup_verify.py, tests/test_backup_verify.py · `After:` A · `Mode:` code

**Behaviour:** reads every source and destination of a manifest in full and trusts no journal. Tests take literal `Row`s.

**Interfaces:**
- `class Verified(NamedTuple)`: `source, dest, bytes, sha256, state` (states in Dispatch). `verify_manifest(rows: Sequence[Row], *, stat=os.stat, attrs_of=None, opener=hashing.open_binary) -> list[Verified]`: per row in order; a `skip_placeholder` row is `skipped_placeholder`; a `copy` row whose source is now a placeholder is `source_placeholder` and neither side is opened; a missing source or destination is `missing_source` or `missing_dest`; `OSError` while reading is `unreadable`; otherwise both files are hashed with `hashing.full_hash` and the state is `identical` or `different`.
- `write_verified(path: str, items: Sequence[Verified]) -> None` (`VERIFIED_COLUMNS`, `utf-8-sig`, parent folder created). `verify_exit_code(items: Sequence[Verified]) -> int`: `0` only when every `copy` row is `identical` (skipped placeholders do not count), else `6`.

**Acceptance:**
1. Spec `backup-verify`, "reads in full": a destination that differs from the source only in a byte in the middle of a large file, or in length, is `different`; an identical one is `identical` with the source's SHA-256; a journal file next to the rows changes nothing; each of the other five states arises for the case that defines it and a placeholder is never opened (an opener that raises proves it).
2. Rule 23 (`backup-verify`): `verify_exit_code` is 0 for all `identical` plus a skipped placeholder, and 6 when any one `copy` row is `different`, missing on either side, unreadable or a changed placeholder; an empty list gives 0.
3. "Writes nothing else": the source tree listing (name, size, mtime_ns, SHA-256) and the archive tree listing are equal before and after `verify_manifest`; `write_verified` round-trips through `csv.DictReader` with exactly the five columns and the BOM.

Commit subject: `feat(lrdup): Add backup verification`

---

### Task F1: configuration and `backup-plan` in `cli.py`

`Wave:` 4 · `Touches:` lrdup/cli.py, tests/test_cli.py · `After:` A, B1, B2 · `Mode:` code

**Behaviour:** `load_config` type-checks `archive_roots` when present and requires nothing new for phase A commands. `main` gains the keyword seams `volume_of=paths.volume` and `free_bytes=backup_plan.free_space` (carried in `_Run`) and passes its existing `stat` and `attrs_of` to the planner. Parser: `backup-plan`. `_require_archive_roots(run)` requires the key and calls `check_archive_roots(..., own_dir=base)`; its `ValueError` becomes `ConfigError("archive_roots", ...)`, and the wrap covers only that call (`ManifestError` is a `ValueError` and is not wrapped elsewhere). F2 reuses it.

**Interfaces:**
- `backup-plan`: `_require_archive_roots`, `plan_manifests` with the configuration, then `write_plan(<base>/out, plan)`; `ManifestError` (no library root read) is `StepMissing` (exit 4); prints per root the manifest path, folders, files and bytes. It opens the database as the other commands do.

**Acceptance:**
1. Configuration (spec "Configuration"): a config without `archive_roots` still runs `catalogs`; `archive_roots` as a string or a list holding a number exits 2 naming the key for every subcommand; `backup-plan` without the key, with a root inside a library root, inside the base folder, or two roots on one injected volume exits 2 naming `archive_roots` and creates nothing.
2. `backup-plan` (bullets): after `catalogs` and `inventory` it writes `out/backup-manifest-1.csv`, `-2.csv` and `backup-summary.md` and nothing under the archive roots; the manifest of the second root is numbered 2; with no files scan it exits 4 with a message.
3. Phase A is unchanged: the existing `tests/test_cli.py` tests pass untouched.

Commit subject: `feat(lrdup): Add the backup-plan subcommand`

---

### Task F2: `backup-run` and `backup-verify` in `cli.py`

`Wave:` 6 · `Touches:` lrdup/cli.py, tests/test_cli.py · `After:` C1, C2, D1, D2, E, F1 · `Mode:` code

**Behaviour:** `main` gains the keyword seams `clock=time.monotonic`, `opener=hashing.open_binary`, `writer=backup.open_part`, `rename=os.rename` and `abort=os._exit` (carried in `_Run`) and passes them, with its `stat` and `attrs_of`, to the dry run, the run and the verify step. Parser: `backup-run MANIFEST [--apply] [--stall-seconds S]`; `backup-verify MANIFEST`. Neither calls `store.connect` (no database file is created or touched). Each first calls `_require_archive_roots`.

**Interfaces:**
- `backup-run`: `read_manifest`, `check_manifest` (before anything, in dry-run too; `ManifestError`, a malformed file included, is exit 2 naming the row), then without `--apply` `dry_run` printed one line per row and exit 0; with `--apply` `run_manifest(journal_dir=<base>/work/journal, started=now())`, one line per result, a tally by state, and `backup.exit_code`. A refused manifest creates no journal folder.
- `backup-verify`: `read_manifest`, `archive_index`, `verify_manifest`, `write_verified(<base>/out/backup-verified-<n>.csv)`, exit `verify_exit_code`.

**Acceptance:**
1. Configuration: `backup-run` and `backup-verify` without the key, with a root inside a library root, inside the base folder, or two roots on one injected volume exit 2 naming `archive_roots` and create nothing.
2. Rule 23 and B-8: `backup-run` without `--apply` exits 0 and creates no file or folder anywhere in the temp tree (listing equal, `work/lrdup.sqlite` not created when absent); with `--apply` it exits 0 on a clean tree and 6 with a conflicting destination; a manifest with a bad `dest` and a malformed manifest file each exit 2 and create no journal folder; `--stall-seconds` reaches the watchdog (an injected `abort` records 5 for an injected opener that blocks).
3. B-14 end to end: a child Python process, started by the test with a script that calls `cli.main` on a synthetic tree with an `opener` that blocks for ever and `--stall-seconds 0.5`, exits with code 5 within a few seconds (the test bounds the wait and kills the child on timeout), and the journal's last line is `stalled`.
4. `backup-verify`: exits 0 and writes `out/backup-verified-<n>.csv` on identical copies, 6 after a destination byte is changed, 2 for a manifest outside every archive root; it creates no database and no journal.

Commit subject: `feat(lrdup): Add the backup-run and backup-verify subcommands`

---

### Task G: end-to-end tests

`Wave:` 7 · `Touches:` tests/test_e2e.py · `After:` F2 · `Mode:` code

**Behaviour:** the reviewer's run before the tool reads the real library: the real modules joined through `cli.main` on the `E2eCase` tree, with `archive_roots` of two temp folders and an injected `volume_of` that gives them different volumes. A new class in `tests/test_e2e.py`; `run_cli` gains the extra injected seams. The tool's base folder is skipped by `listing` (the one folder the tool may write under a library root).

**Acceptance:**
1. Rule 17 end to end (B-9): `catalogs`, `inventory`, `backup-plan`, `backup-run`, `backup-run --apply`, `backup-verify` all exit as specified, and `listing` of the library root (path, size, mtime, SHA-256, base folder skipped) is equal before and after the whole run; each archive root holds every file of every selected folder (a raw, a JPEG, an `.xmp`, a `.txt`) byte-equal with the source's mtime, nothing from an unselected folder, and no placeholder file.
2. Rules 18, 21 and 23 end to end: a second `--apply` exits 0 with every row skipped or `already_there`; after one destination byte is changed, `backup-verify` exits 6 with that row `different`, and `backup-run --apply` exits 6 with a `conflict` and leaves that destination unchanged.
3. Dry run and plan (B-8): the dry run changes nothing under the library or either archive root; `backup-plan` copies nothing; a source edited between `backup-plan` and `--apply` is `changed` (exit 6) and has no destination.

Commit subject: `test(lrdup): Add the backup end-to-end tests`

---

## Spec coverage

| Spec item | Task |
|---|---|
| Configuration: `archive_roots`, not inside a library root or the tool's folder, no shared volume, optional for phase A | A (`check_archive_roots`), F1 (`load_config`, `backup-plan`), F2 (the other two) |
| `backup-plan`: latest complete scan, selection by content, listed afresh, every file, subfolders, links, own folder | B1 |
| `backup-plan`: placeholder rows, destination, columns, summary, raws outside `photo_root`, nothing copied | A (columns), B1, B2, F1, G |
| `backup-run`: dry run by default, `--apply` copies | D1 (`dry_run`), D2 (`run_manifest`), F2, G |
| `backup-verify`: reads in full, `backup-verified-<n>.csv`, writes nothing else, exit 0 only when identical | E, F2, G |
| Rule 16 approved destinations, free space | C1 (`check_manifest`), F2 |
| Rule 17 sources read-only; listing equal | C2, G |
| Rule 18 never overwrite | C2, G |
| Rule 19 verified by content | C2 |
| Rule 20 changed sources | C1 (`source_changed`), C2, D1, D2, G |
| Rule 21 journal, rerun skips verified, verify trusts no journal | D1, D2, E, G |
| Rule 22 stalled drive, part file removed next run | C2 (progress), D2, F2 |
| Rule 23 exit codes 0, 2, 5, 6 | D1 (`exit_code`), E, F2 |
| Verification: one test per rule and per bullet | A to G |
| Verification: planted-defect gate `tests/mutants/backup.json` | C1 (two defects), C2 (four), D1 (fifth) |
| Verification: reviewer's end-to-end run on a synthetic tree | G, Controller steps |
| Verification: real run, pilot first | Controller steps |

## Controller steps

1. Before dispatch: confirm the workspace `constraints.md` holds the Gates block above (it does) and commit this plan. Give each implementer the `context.md` rulings (B-1 to B-18) and the answers to the report's open questions.
2. After each landing: send the change note of the Dispatch table and run `gate-runner` at that HEAD. After C1, C2 and D1 the `mutants-backup` gate must report every defect caught (2, then 4, then 5).
3. After G: a reviewer runs the three subcommands by hand on a synthetic tree (`python -m lrdup --base <temp> ...`) and the controller reads one manifest, one summary and one verified file.
4. The real run is the controller's, with Lightroom closed and the owner told first, on a `work/config.json` that gains `archive_roots`: run `catalogs` and `inventory` again so the scans are current, then `backup-plan`. Show the owner the summary (folders, files, bytes, placeholders, subfolders left out, free space, raws outside the photo root) and wait for approval; record the approved manifests' SHA-256 and run nothing from a manifest that differs.
5. Pilot: for each archive root, a filtered copy of the approved manifest holding the rows of one small folder, kept outside the tracked tree. `backup-run` (dry), the owner's go-ahead, `backup-run --apply`, then `backup-verify`; exit 0 on every root before going on.
6. Full run: `backup-run` (dry) on each approved manifest, then `--apply`; on exit 5 reconnect the drive and rerun (raise `--stall-seconds` when one very large file stalls in its fsync); on exit 6 read the journal and the verified file, never edit a destination. Finish with `backup-verify` on each full manifest (exit 0). Take a listing of path, size and time under the library root before and after the whole run; they must be equal (rule 17).
7. Edit the shared files (`CLAUDE.md`, `docs/**`, `README.md`) for what the run teaches; none is a task's `Touches`.

## Not in this plan

- Phase B2: `mover.py`, staging, copying off the system drive for space, removing a source, the owner's remove command, `undo`.
- Phases C and D: catalog tidy, loose photos, raws outside `photo_root` (counted in the summary only), paired-JPEG lists.
- A stall watchdog for `backup-verify`, parallel copies, a progress display, pruning old manifests or journals, resuming a half-written part file (it is removed and rewritten), covering the duration of one fsync with the watchdog, detecting a link inside an archive root by its real path.
- Any write to a catalog database, and any use of a catalog by B1.
