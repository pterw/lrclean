# Reject Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `catalogs` store each image's pick flag and develop state, and add `python -m lrdup rejects`: it reads the flags of the live catalog and of every shoot catalog and writes `out/rejects.csv`, `out/reject-removal-list.csv` and `out/rejects-summary.md`. It changes nothing else, anywhere.

**Architecture:** One new table (`image`). `catalogs.py` stores its rows. Three new modules: `lrdup/rejects.py` decides (flags, keeps, freshness, the verdict per reject), `lrdup/reject_archive.py` holds the archive evidence of rule 27, `lrdup/reject_out.py` writes the three outputs. `cli.py` gains `rejects`. Tasks of one wave touch disjoint files.

**Tech Stack:** Python 3.13 standard library (`sqlite3`, `csv`, `os`, `unittest`); Windows paths.

**Spec:** `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "The reject check" as at commit f14275d (rules 24 to 31 cited by number), and rules 2, 3, 5, 6, 15 of phase A. Owner rulings R13 to R18 (`CLAUDE.md`); controller rulings RC-1 to RC-22 (workspace `context.md`); rulings C-1 to C-60 and B-1 to B-18 still bind the modules that exist.

Sections: Global Constraints=brief, Review Focus=reference, Dispatch=reference, Tasks=brief, Spec coverage=reference, Controller steps=reference, Not in this plan=reference

## Global Constraints

- The reject check only (RC-1). No mover, staging, removal or undo, no write to any catalog, no use of the plug-in. `rejects` writes only under `<base>/out/`, apart from opening the work database, which `cli.main` does before every command that reads it (`store.connect` creates `work/lrdup.sqlite` and its tables when they are absent); it writes no row.
- Python 3.13, standard library only, `unittest`, LF files (`newline="\n"`); CSVs `utf-8-sig`, `newline=""`. Tests run as `python -m unittest discover -s tests`; test files import `synth` and `lrdup.<module>`.
- PRIVATE DATA: no real path or name in code, tests, docs or reports. Fixtures are plainly synthetic (`shoot-a`, `IMG_0001.ARW`); every test builds its own tree in `tempfile.TemporaryDirectory()` and touches nothing outside it. Nothing runs on a real folder.
- Reuse, do not rewrite (RC-4): `paths.norm`, `paths.fs_path`, `paths.is_placeholder` with the `attrs_of(path, st) if attrs_of else st.st_file_attributes` seam of `inventory.py`, `catalogs.is_stale`, `classify.coverage`, `cli._unseen_catalogs`, `backup_plan.read_manifest`, `Row`, `is_inside`, `ManifestError`, `backup_verify.Verified`. Grep before adding a helper.
- Seams (RC-6): `stat`, `attrs_of`, `exists` and the clock (`now`) are parameters with the defaults of `cli.main`; no test needs a real drive.
- Fail closed: any doubt (a `stat` error, an absent or odd file, an unknown pick, a develop state that cannot be read) holds a row and never passes it.
- Work keeps (R18, rules 26, 28): develop work (`developed` 1, or empty, since a develop state that cannot be read keeps; an image with no `Adobe_imageDevelopSettings` row is empty; a column value is usable when NULL or a number equal to -1, 0 or 1, and -1 means not edited, RC-18) or a pick of 1, in any catalog read with status `ok` or `partial` and not excluded, whatever its role and the catalogs in `old_states` included, keeps a reject and every sidecar. It is not a keep by record: its verdict is `kept_for_work`. Every test that expects `on_list` therefore gives each image of each catalog `developed` 0.
- Tests (RC-7): one per behaviour, near-duplicates parametrized, each named for the rule it guards. A decision on several fields gets a case per field, a walk over the archive roots a case per root, and a walk over catalogs or views cases that tell the first from the largest so far from the previous one (L10, L14). `n` is 1-based everywhere (the root's position in `archive_roots` plus one), as in B1.
- Planted defects (RC-8): each decision is one expression on one line so a defect can replace it. Code tasks never commit; the controller lands each patch with the subject the task gives, no trailer. No pre-commit configuration exists; skip it.

## Review Focus

- A virtual copy that is not rejected beside a rejected master: the path is not a reject (rule 24).
- A reject whose raw is rejected but whose sidecar another catalog records as a photo it keeps: the whole row is held (rule 29).
- A flag set after the last `catalogs` run, a catalog moved into `old_states` after it: refusal with exit 4 and `out/` untouched (rule 25).
- A manifest or verified list that is absent, older than its pair, or malformed: held, held, refused (rule 27).
- Two archive roots where only one holds the copy: the row is held whichever root is the bad one.
- Develop work or a pick of 1 in any catalog, a catalog in `old_states` or a catalog that rejects the photo included, or an empty develop state, keeps the reject and its sidecars off the list (rules 26, 28, 29; verdict `kept_for_work`).
- A reject that is also an existing sidecar of a held reject (a JPEG a live catalog records only as the raw's sidecar and a shoot catalog rejects as a photo) takes the raw's verdict: a raw and its sidecars leave together or not at all (rule 29, RC-19).
- A destination that is an online-only placeholder at check time: `no_archive_copy` (rule 27, RC-20).
- The live catalog named in `excluded_catalogs`: refusal with exit 2 and `out/` untouched, so a catalog that keeps nothing can never let a shoot catalog's reject through (rule 26, RC-17).

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | A, B, C | immediately | 3 code phases + 3 nested gate-runners = 6 |
| 2 | D, E | A, B, C landed | 2 + 2 nested + reviews of A, B, C = 7 |
| 3 | F, G | D, E landed | 2 + 2 nested + reviews of D, E = 6 |
| 4 | H | F, G landed | 1 + 1 nested + reviews of F, G = 4 |
| 5 | I | H landed | 1 + 1 nested + review of H = 3 |

Peak 7, cap 12. The controller lands a wave's patches before it dispatches the next. Files changed by several tasks (`rejects.py`: E, F; `tests/mutants/rejects.json`: C, E, F) are changed in successive waves only. Landing order: A, B, C, D, E, F, G, H, I. Per patch: `git apply --check --index <patch>`, `git apply --index <patch>`, one commit with the task's subject, then `gate-runner` at that HEAD in a clean detached worktree (L5, L6). Review: one `sdd-reviewer` per task with the same CONTEXT as its implementer. `lrdup/store.py` and `lrdup/__init__.py` are controller-only again once A has landed (RC-2).

**Frozen interfaces.** An implementer that needs one changed returns NEEDS_CONTEXT.

*Schema (A).* `image(catalog_id, path_norm, via, pick, developed)`: `catalog_id`, `pick`, `developed` INTEGER (`pick` and `developed` join `_INT_COLUMNS`); `path_norm`, `via` TEXT. `pick` is -1, 0 or 1, else NULL; `developed` is 1, 0 or NULL (NULL keeps, R18). No other table changes.

*Synthetic catalogs (B).* An entry of `synth.make_catalog` may carry `pick`, `developed`, `developed_ex` and `copy_of`. `pick` (any value, stored as given) adds the `pick` column to `Adobe_images` when at least one entry has the key; with no entry having it the column is absent. The pick column is declared without a type, so SQLite keeps each value as given (a REAL -1.0 stays REAL, the text "-1" stays text). `developed` and `developed_ex` fill `hasDevelopAdjustments` and `hasDevelopAdjustmentsEx` of `Adobe_imageDevelopSettings(id_local, image, ...)`, a row per image of an entry that names either; the table has the columns that some entry names and exists only when one does. Both columns are declared without a type and store any value as given (NULL, -1, -1.0, 2, `'unknown'`, the text `'1'`). A row's `id_local` is `1000 + <image id>`, so it equals no image id of the catalog (the real layout has independent keys, RC-12), and its `image` is the entry's image id. New keyword `develop_columns: tuple[str, ...] | None = None` forces exactly those columns (and the table), so `()` gives a table with neither. `copy_of` (the `base` of another entry) makes a virtual copy: no new `AgLibraryFile` row, its `Adobe_images` row has `rootFile` of the master's file and `masterImage` of the master's image. `make_unclean` carries the new keys too: its base file gets the `pick` column and the develop table the entries ask for, so a pick and a develop state can live in the `-wal` only. Existing entries, `make_unclean` without the new keys and `omit_tables` behave as today.

*Catalog read (D).* `CatalogRead` gains `images: list[dict] = []` (keys `path_norm`, `via`, `pick`, `developed`; one dict per image and path form, never de-duplicated). `catalogs._role` becomes public `catalogs.role_of(source, live_catalog, old_states) -> str`, same body.

*Archive evidence (C), `lrdup/reject_archive.py`.* `OK = "ok"`, `OUTSIDE_BACKUP = "outside_backup"`, `CHANGED_SINCE_BACKUP = "changed_since_backup"`, `NO_ARCHIVE_COPY = "no_archive_copy"`. `RootEvidence(NamedTuple)`: `root: str`, `rows: dict[str, list[Row]]` (`paths.norm(source)` to the manifest rows, `{}` when the manifest is absent), `verified: frozenset[tuple[str, str, int]]` (`norm(source)`, `norm(dest)`, `bytes` of each `identical` row; empty when the verified list is absent, unreadable-by-stat, or older than the manifest). `load_evidence(out_dir, archive_roots, *, exists=os.path.exists, stat=os.stat) -> list[RootEvidence]`: one per root in order, root `n` reads `backup-manifest-<n>.csv` and `backup-verified-<n>.csv`; a file that exists and is unreadable or has the wrong header raises `backup_plan.ManifestError` naming it. `archive_state(evidence, path, st, *, stat=os.stat, attrs_of=None) -> tuple[str, str]`: `(state, dest)`, `st` the source's `os.stat` result or `None` when it failed; `dest` is the manifest copy row's, `""` when none; `attrs_of` is the placeholder seam of `inventory.py` (`attrs_of(path, st) if attrs_of else getattr(st, "st_file_attributes", 0)`, then `paths.is_placeholder`), applied to the destination. Also `backup_verify.read_verified_list(path) -> list[Verified]` (name distinct from `backup.read_verified`; `ManifestError` on a missing file, a header other than `VERIFIED_COLUMNS` or a non-integer `bytes`).

*Decision records (E), `lrdup/rejects.py`.* `VERDICTS = ("on_list", "coverage_incomplete", "kept_in_live", "kept", "kept_for_work", "missing", "placeholder", "outside_backup", "changed_since_backup", "no_archive_copy")`. `CatalogView(NamedTuple)`: `source: str`, `role: str`, `status: str`, `excluded: bool`, `picks: dict[str, list[int | None]]` (`path_norm` to the pick of each `image` row), `developed: dict[str, list[int | None]]` (`path_norm` to the `developed` of each `image` row, as `picks`), `photos: set[str]` (records with `is_sidecar` 0, plus every `image` path), `sidecars: set[str]` (records with `is_sidecar` 1), `orig: dict[str, str]` (`path_norm` to `path_orig`, the norm itself for an image-only path). `Reject(NamedTuple)`: `path: str`, `path_norm: str`, `bytes: int`, `mtime_ns: int` (both 0 when the file cannot be read), `verdict: str`, `rejected_by` and `kept_by: list[tuple[str, str]]` (catalog source, role; filled whatever the coverage), `archive: list[str]` (one state per root), `developed_in: list[tuple[str, str, str]]` (catalog source, role, `work_of`), `images: list[tuple[str, int]]` (catalog source, `image` rows of that path), `sidecars: list[str]` (existing ones, `path_orig`, sorted by norm), `sidecar_bytes: int`, `held_by: str` (the sidecar, or the reject whose sidecar this path is, that decided a verdict other than `on_list`, else `""`), `dests: list[str]` (per root, `""` when none). `class StaleFlags(Exception)`. Functions: `latest_views(conn, *, excluded_catalogs) -> list[CatalogView]`; `is_rejected(view, path_norm) -> bool`; `base_index(paths) -> dict[tuple[str, str], list[str]]` (`(folder, base name)` to the paths); `is_kept(view, path_norm, index=None) -> bool` (`index` is `base_index(view.photos)`, built on the spot when `None`); `work_of(view, path_norm) -> str` (R18: `""`, `"develop"`, `"pick"` or `"develop+pick"`); `reject_set(views) -> dict[str, list[CatalogView]]`; `partly_rejected(views) -> list[tuple[str, str]]`; `sidecars_of(views, path_norm, indexes=None) -> dict[str, str]` (`norm` to `orig` as the first view in view order that records the sidecar spells it; `indexes` maps a view's `source` to `base_index(view.sidecars)`); `check_fresh(views, *, live_catalog: str | None, stale: Collection[str], expected_roles: Mapping[str, str]) -> None` (raises `StaleFlags`); (F) `decide(views, evidence, *, coverage_complete: bool, stat=os.stat, attrs_of=None) -> list[Reject]`, sorted by `path_norm` (`attrs_of` also goes to `archive_state`).

*Outputs (G), `lrdup/reject_out.py`.* `SummaryInput(NamedTuple)`: `generated: str`, `scans: list[dict]` (keys `kind`, `id`, `root`, `status`, `finished`), `views: list[CatalogView]`, `limiting: list[str]` (the causes of `classify.coverage`). `write_outputs(out_dir, rejects, *, roots: int, summary: SummaryInput) -> list[str]` returns the three paths. `out/rejects.csv`: `path, bytes, verdict, rejected_by, kept_by, archive_1 .. archive_<roots>, developed_in, how_decided`, one row per reject, sorted by `paths.norm(path)`; list cells join entries as `<catalog source> (<role>)` with `;` (`developed_in`: `<catalog source> (<role>: <work>)`); `how_decided` is `images: <catalog>=<n>; ...`, plus `; held by: <path>` when `held_by`. `out/reject-removal-list.csv`: `source, bytes, mtime_ns, sidecars, sidecar_bytes, dest_1 .. dest_<roots>`, the `on_list` rows only, same order, but none for a reject that is also a sidecar of another `on_list` reject (RC-23). `out/rejects-summary.md` (LF): line 1 `# Reject check`, then `Generated: <time>`, then the sections `## Scans`, `## Per verdict`, `## Per catalog`, `## Rejected in part`, `## Kept in the live catalog`, `## Not checked`.

*Exit codes.* 0 written (also with no reject), 2 `CONFIG_ERROR` (configuration, fewer than two archive roots, `live_catalog` named in `excluded_catalogs` (RC-17), an unreadable or malformed manifest or verified list), 4 `STEP_MISSING` (rule 25). No new constant and no new `main` seam.

Change notes (sent when a task lands, to tasks running or staged):

| Task | Changes | Who is told |
|---|---|---|
| A | `store.py` has the `image` table | D, E, H |
| B | `synth.py` gains the entry keys and `develop_columns` | D, F, H, I |
| C | new `reject_archive.py`, `backup_verify.read_verified_list`, `rejects.json` (3 defects) | F, H |
| D | `catalogs.py` stores `image` rows, `CatalogRead.images`, `role_of` | F, H, I |
| E | new `rejects.py` (records, flags, keeps, `work_of`, freshness), `rejects.json` (9) | F, G, H |
| F | `rejects.py` gains `decide`; `rejects.json` (13) | H |
| G | new `reject_out.py` | H |
| H | `cli.py` gains `rejects` | I |

Gates block for the workspace `constraints.md` (the first three as already written there, plus `mutants-rejects`, RC-8):

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
- name: mutants-rejects
  run: python C:/ssw/tools/mutate.py tests/mutants/rejects.json --jobs 3 --timeout 300
  pass: exit 0
  quote: first line
  when: any of lrdup/rejects.py, lrdup/reject_archive.py, tests/test_reject_archive.py, tests/test_rejects.py, tests/test_rejects_verdicts.py, tests/mutants/rejects.json changed
```

`tests/mutants/rejects.json`: `"tests": "tests"`, `"pattern": "test_reject*.py"` (matches the four reject test files and nothing of phase A or B1), format of `backup.json`, each `old` one line occurring once in its `file`.

## Tasks

### Task A: the `image` table

`Wave:` 1 · `Touches:` lrdup/store.py, tests/test_store.py · `After:` none · `Mode:` code

**Behaviour:** `TABLES` gains `image` with the columns of the frozen schema; `pick` and `developed` join `_INT_COLUMNS`. Nothing else in `store.py` changes.

**Acceptance:**
1. Schema (RC-2): `test_schema_is_exactly_the_frozen_tables_and_columns` (its `SCHEMA` gains `image`) passes with `pick` and `developed` INTEGER columns in `PRAGMA table_info`.
2. Empty is empty (rule 24 "empty pick"): a row inserted with `pick=None`, `developed=None` reads back `None`, and `-1`, `0`, `1` read back as ints; `insert_row` refuses an unknown column of `image`. `SCHEMA` lists the six old tables unchanged.

Commit subject: `feat(lrdup): Add the image table for pick flags and develop state`

---

### Task B: synthetic pick flags, develop state and virtual copies

`Wave:` 1 · `Touches:` tests/synth.py, tests/test_synth.py · `After:` none · `Mode:` code

**Behaviour:** the entry keys and `develop_columns` of the Dispatch. `synth.py` still imports nothing from `lrdup`.

**Acceptance:**
1. Opt-in (existing tests untouched): a catalog made without the new keys has no `pick` column and no `Adobe_imageDevelopSettings` table, and `tests/test_synth.py`, `tests/test_catalogs.py` (its positional four-value insert) pass unedited.
2. `pick`: values -1, -1.0, 0, 1, None, the text "-1" and the text "x" read back with the same Python type and value (`typeof(pick)` is integer, real, integer, integer, null, text, text), and the column exists as soon as one entry has the key.
3. Develop state: `developed` alone, `developed_ex` alone, both, and `develop_columns=()` give a table with exactly the named columns and a row per naming entry only; `omit_tables` still removes it. Values NULL, -1, -1.0, 0, 1, 2, `'unknown'` and the text `'1'` in either column read back with the `typeof` they were given (integer, real, text or null; RC-18). Each settings row has `image` equal to its entry's `Adobe_images.id_local` and an `id_local` that equals no `Adobe_images.id_local` of the catalog.
4. `copy_of`: a virtual copy adds no `AgLibraryFile` row, shares its master's `rootFile`, points `masterImage` at the master's image, and carries its own `pick` and `developed`.
5. `make_unclean` with an entry `{"pick": -1, "developed": 1}`: `sqlite3.connect(path)` (which replays the `-wal`) reads pick -1 and `hasDevelopAdjustments` 1 for it, the `-wal` file is non-empty, and the base file read alone has none.

Commit subject: `test(lrdup): Add pick flags, develop state and virtual copies to synthetic catalogs`

---

### Task C: archive evidence (rule 27)

`Wave:` 1 · `Touches:` lrdup/reject_archive.py, lrdup/backup_verify.py, tests/test_reject_archive.py, tests/mutants/rejects.json · `After:` none · `Mode:` code

**Behaviour:** the evidence a manifest and a verified list give for one reject on one root, reading no file's content. `archive_state` decides in this order. (1) `OUTSIDE_BACKUP` unless `evidence.rows[norm(path)]` holds exactly one row with action `copy` whose `dest` is strictly inside the root (`is_inside(..., below_only=True)`). (2) `CHANGED_SINCE_BACKUP` when `st` is `None` or its size differs from `row.bytes` or its `st_mtime_ns` differs from `row.mtime_ns`; size and time are two separate expressions. (3) `NO_ARCHIVE_COPY` unless the key (`norm(path)`, `norm(row.dest)`, `row.bytes`) is in `evidence.verified` and `stat(paths.fs_path(dest))` succeeds with `st_size == row.bytes` and a result that is not a placeholder (the `attrs_of` seam; RC-20, rule 27, R4); any `OSError` holds. Else `OK`. `load_evidence` tests `exists` first (an absent manifest gives `rows={}`, an absent verified list `verified=frozenset()`), then reads; the verified list counts only when `stat(verified).st_mtime_ns >= stat(manifest).st_mtime_ns` (equal passes, "not older than" per rule 27; file times are the only clock both files have), and a `stat` error on either gives an empty set. Tests build manifests and verified lists with `write_manifest` and `write_verified` in a temp folder.

**Acceptance:**
1. Rule 27, fields one by one: `st` equal to the row gives `ok`; size differs alone, time differs alone (each by one unit), `st=None` each give `changed_since_backup`.
2. Rule 27, evidence: no manifest row, an action `skip_placeholder` row, a `dest` outside the root or equal to it, two copy rows for one source, and an absent manifest each give `outside_backup`; a verified row with state `different`, `missing_dest`, `unreadable`, another `bytes`, another `dest`, another `source` (an `identical` row for another source with this row's own `dest` and `bytes`, once alone and once beside the right row of a second reject), an absent list, a list one nanosecond older than its manifest, a destination that is absent, of another size or an online-only placeholder (injected `attrs_of`; a placeholder source is not the test), and a destination whose `stat` raises each give `no_archive_copy`; a list with the manifest's own time passes, and so does a verified row whose source is spelled with another case or slash direction than the manifest's (the key is compared as `paths.norm`).
3. Per root (L14): with two roots, manifest 2 absent alone leaves `evidence[0]` decided and `evidence[1].rows == {}`; manifest 1 absent alone the reverse; manifests swapped between the two files make every row `outside_backup`.
4. Refusals: a malformed manifest (header, non-integer bytes) and a verified list with a missing column or a non-integer `bytes` raise `ManifestError` naming the file; `read_verified_list` round-trips `write_verified` for every state (`identical`, `different`, `missing_source`, `missing_dest`, `source_placeholder`, `unreadable`, `skipped_placeholder`), a comma and a non-ASCII name.
5. Planted defects: `rejects.json` is created with three defects in `lrdup/reject_archive.py`: the verified-list age test, the size test and the time test; `mutate.py --jobs 3` run once in the worktree reports all caught.

Commit subject: `feat(lrdup): Add the archive evidence of the reject check`

---

### Task D: `catalogs` stores the image rows

`Wave:` 2 · `Touches:` lrdup/catalogs.py, tests/test_catalog_images.py · `After:` A, B · `Mode:` code

**Behaviour:** the spec's "What `catalogs` adds". For every image whose `rootFile` joins to a file, folder and root folder, one `image` row per path form of the photo (absolute and relative, as `_entry_records` builds them for the photo's own extension; no sidecar path). `pick`: the column's value when it is a number equal to -1, 0 or 1 (`-1.0` is -1), else `None`, and `None` when the column is missing. `developed`: from the `Adobe_imageDevelopSettings` row whose `image` is the image's `id_local` (RC-12: observed by the controller on the catalog copies of 2026-11-17). A column value is *usable* when it is NULL or a number equal to -1, 0 or 1 (an integer or a real such as `-1.0` or `1.0`; text such as `'1'` and any other number are not); Lightroom stores -1 or 0 for a photo it calls not edited and 1 for an edited one (RC-18). `developed` is 1 when either column equals 1; else `None` when the table, both columns or the image's row is missing, when some value of the row is not usable, or when every present column of the row is NULL; else 0 (R18: `None` keeps the photo). A missing `pick` column or develop table leaves the status `ok`; an `sqlite3.Error` while reading images makes the catalog `partial` and keeps what was read, as for records. The rows are stored for every role and status. `_role` is renamed `role_of`.

**Acceptance:**
1. Rule 24 data: a master and a virtual copy sharing one file give two rows per path form (four in all) each with its own pick, never merged; both forms of a path exist when the catalog records both.
2. Pick values: `-1`, `-1.0`, `0`, `1` give -1, -1, 0, 1; `NULL`, `2`, `"-1"` and `"x"` give `None`; a catalog without the column stays `ok` with every pick `None`.
3. Develop state, on Task B's layout (`settings.id_local` never equals an image id, so a join on it instead of `image` gives `None` for the `developed` 1 case and fails it): `developed` alone, `developed_ex` alone, both 0, an image without a settings row, a table with neither column and no table give 1, 1, 0, `None`, `None`, `None`, status `ok` in each. Also: both columns NULL, `'unknown'` in either column, 2 in either column and the text `'1'` in an untyped column give `None`; main 0 with `Ex` NULL gives 0; `Ex` 1 with main `'x'` gives 1; `Ex` -1.0 or -1 (not edited), alone or with main NULL or 0, gives 0, and -1 beside 1 gives 1 (RC-18). An image whose file has a sidecar extension yields no sidecar-path row.
4. Reading is on the snapshot: after the read, the source catalog's listing (name, size, mtime, SHA-256) is equal and `old_state` and `shoot` catalogs both store rows; an `unreadable` catalog stores none; the phase A record tests pass unedited. A catalog made with `synth.make_unclean` whose entry carries `pick: -1` and `developed: 1` (both only in the `-wal`) stores `image` rows with pick -1 and developed 1 and status `ok`; a reader that copied the `.lrcat` alone would store none.
5. Known limit (A-03, "Not in this plan"): a raw that declares `JPG` as a sidecar and a JPEG photo row of the same base name give one record per path form of the JPEG, `is_sidecar` 0 when its row comes first and 1 when the raw's row comes first, and an `image` row for the JPEG in both orders.

Commit subject: `feat(lrdup): Store pick flag and develop state of every image`

---

### Task E: flags, keeps, work and freshness (rules 24, 25, 26)

`Wave:` 2 · `Touches:` lrdup/rejects.py, tests/test_rejects.py, tests/mutants/rejects.json · `After:` A, C · `Mode:` code

**Behaviour:** the records of the Dispatch and the view functions; tests build views literally (`CatalogView(...)`) or fill a `store.connect` database with `insert_row`, never a real scan. `latest_views` reads the `catalog` rows of the latest `catalogs` scans (as `cli._latest_catalogs` does) with their `record` and `image` rows, and no `file` or `hash` row; `picks` and `developed` hold one entry per `image` row. `is_rejected`: role `live` or `shoot`, status `ok`, not excluded, the path has at least one pick and every pick equals -1 (rule 24). `is_kept`: role `live` or `shoot`, status `ok` or `partial`, not excluded, and either the path is in `photos` and not rejected, or it is in `sidecars` and some photo of the same view in the same folder with the same base name (`paths.split_path`, extension cut off) is not rejected by that view (rules 24, 26); it looks the sidecar up in `index`, never scanning `photos` per call (a sidecar per view per reject would be quadratic), and `decide` builds each view's indexes once. `work_of` (R18, rule 26): `""` unless the view has status `ok` or `partial`, is not excluded, whatever its role (`old_state` included), and has an `image` row of the path; then `develop` when some `developed` of it is 1 or `None`, `pick` when some pick is 1, `develop+pick` for both; `developed` 0 and a pick of 0, -1 or `None` alone give `""`. `reject_set`: path to the views that reject it, in view order. `partly_rejected`: `(source, orig path)` for every path with a pick of -1 in a live or shoot view that does not reject it, sorted. `sidecars_of(views, path_norm)`: `norm` to `orig` of the sidecar records (either path form) of every view that rejects the path (in view order; when several views record one sidecar, the `orig` of the first of them), in the same folder with the same base name and not the path itself. `check_fresh` raises `StaleFlags("run catalogs again: ...")` when: `live_catalog` is `None`, or no view has `paths.norm(source) == paths.norm(live_catalog)`; that view's status is not `ok`; its source is in `stale` (`paths.norm` compare); it has no image row; every pick of it is `None`; or any view's `role` differs from `expected_roles[paths.norm(source)]`. A decision per line, each its own expression: the every-pick test, the keep test, the work test (the expressions of `work_of` that decide a view holds work: a `developed` of 1 or `None`, or a pick of 1; each on its own line, before the label is chosen), the all-picks-empty refusal, the stale refusal, the role refusal.

**Acceptance:**
1. Rule 24 reject, one case each: every image -1 (master and copy); only the master -1; only a virtual copy -1; no image row; a pick `None` beside -1; rejected in a shoot catalog and not recorded by the live one; a `partial`, an excluded and an `old_state` view that carry -1 are not rejects; two path forms of one file are judged on their own.
2. Rules 24 and 26 keep: a recorded photo not rejected keeps; a photo record with no `image` row keeps (it is not rejected); a sidecar record of a rejected photo does not keep, of a not-rejected photo it does; excluded, `old_state` and `unreadable` keep nothing, `partial` keeps; rejected in one shoot catalog and kept by another is a reject (in `reject_set`) that only the second keeps; a walk over three views where the keeper is first, last and in the middle each gives the same keepers (L14).
3. Rule 25, one case per condition (L10): `live_catalog` `None`, live view absent, status `partial`, status `unreadable`, source in `stale`, no image row, all picks `None`, role differs for a shoot, an `old_state` and the live view; a fresh set with one non-empty pick passes, also when `live_catalog` is spelled with other case and backslashes than the stored source (as `find_catalogs` returns `root\name`); the same set with the live view's source absent still refuses.
4. `latest_views` from a database holding two scans of one root reads only the latest; sets role, status and `excluded`; `photos` holds records with `is_sidecar` 0 and image-only paths; it still works when the `file` and `hash` tables are dropped after `connect` (rule 31); `partly_rejected` and `sidecars_of` return the cases stated above, and a sidecar of another base name or folder is not returned; `sidecars_of` returns a sidecar recorded only by the second of two rejecting views, only by the first, in the middle and last of three (L14), the first view's spelling when two spell one sidecar differently, and not one recorded only by a view that does not reject the path.
5. Work (R18, rule 26), one case each: `developed` 1; `developed` `None` (empty keeps); `developed` 0 alone; a pick of 1; a pick of 0, -1 and `None` alone; the same work in a rejecting view, in an `old_state` view and in a `partial` view each holds (`work_of` non-empty); an excluded and an `unreadable` view, and a path with no `image` row, hold none; both kinds give `develop+pick`; three views with the work first, in the middle and last each give the same answer (L14). `is_kept` still ignores work.
6. Planted defects: six in `lrdup/rejects.py` (the every-pick test, the keep test, the work test, the all-empty refusal, the stale refusal, the role refusal) added to `rejects.json`; all caught. The work-test defect makes `work_of` return `""` for an empty develop state, so an empty state no longer keeps; it is on the keep decision, never on the label, and the `developed` `None` case of 5 kills it.

Commit subject: `feat(lrdup): Add the reject flags, keeps and freshness check`

---

### Task F: the verdict per reject (rules 26 to 30)

`Wave:` 3 · `Touches:` lrdup/rejects.py, tests/test_rejects_verdicts.py, tests/mutants/rejects.json · `After:` B, C, D, E · `Mode:` code

**Behaviour:** `decide`; it raises `ValueError` when `evidence` has fewer than two entries (defence in depth for rule 27; H holds the real guard). Per path of `reject_set(views)`: `path` is the `orig` of the first view in `reject_set` order; the `sidecars` are the values of `sidecars_of(views, path_norm, indexes)` as returned (each spelled as the view that records it spells it; none is looked up in the first view's `orig`); `rejected_by`, `kept_by` (views for which `is_kept` holds), `developed_in` (`(source, role, work_of)` for every view whose `work_of` is not empty, any role, live and `old_state` included), `images` as in the Dispatch. `stat` the reject once: `FileNotFoundError` is `missing`; any other `OSError` is `st=None`, which `archive_state` holds as `changed_since_backup`; `bytes` and `mtime_ns` are 0 then. Each sidecar of `sidecars_of` is statted: not found is ignored, any other error stays and fails its tests. `archive` and `dests` come from `archive_state` (with `stat` and `attrs_of`) for the reject and each sidecar, per root; a root's state for the CSV is the first non-`ok` over the reject then its sidecars. The verdict is verdict-major: for each name of `VERDICTS` in order, it holds when its test holds for the reject or for any existing sidecar (the reject first, and `held_by` names the sidecar when only a sidecar made it hold): `coverage_incomplete` (not `coverage_complete`), `kept_in_live` (the reject itself kept by the live view), `kept` (any other view keeps the reject, or any sidecar is kept by a view, a sidecar recorded as a photo the view does not reject being kept), `kept_for_work` (no view keeps it so, but some view's `work_of` is not empty for the reject's path or a sidecar's own path), `missing` (the reject only), `placeholder`, then, among `outside_backup`, `changed_since_backup`, `no_archive_copy` in that order, the first that occurs for the reject or any sidecar on any root (`held_by` names the sidecar when the reject itself is `ok` for it); else `on_list`. `bytes` is the reject's alone; `sidecar_bytes` sums the existing sidecars. Family hold (rule 29, RC-19): once every reject has its verdict, a reject S whose verdict is `on_list` is held when S is also an existing sidecar (a key of `sidecars_of(views, R.path_norm)`) of a reject R whose verdict is not `on_list`: S takes R's verdict and `held_by` is `R.path`; a worklist in `path_norm` order, built from a map of sidecar path to the rejects that have it, carries a hold on through chains (each reject is queued once per verdict change; when several held rejects have S as a sidecar, the first in that order decides), so the result does not depend on the order of `path_norm`. A reject whose own verdict is not `on_list` keeps it; the `archive`, `dests`, `developed_in` and `kept_by` of S stay its own.

**Acceptance:**
1. Rule 30 order: each of the nine non-list verdicts alone, and for each of the eight adjacent pairs of the nine non-list verdicts (`on_list` is the else result and pairs with none) a case where both hold and the earlier wins (a verdict holds when its own test holds: for `kept_for_work` `work_of` is not empty, beside a keeper that makes `kept` win; for `missing` and `placeholder` the reject is missing and a sidecar is a placeholder); a clean reject is `on_list`; `kept_by` and `rejected_by` are filled when the verdict is `coverage_incomplete`.
2. Rule 29: a sidecar failing alone (kept elsewhere, develop work or a pick on its own path giving `kept_for_work`, placeholder, outside backup, changed, no archive copy) holds the whole row with that verdict and `held_by`; an absent sidecar is ignored and not listed; a sidecar a second catalog records as a photo it does not reject gives `kept`; a sidecar recorded only by the second rejecting view, failing alone (`no_archive_copy` on the second root), holds the row with `held_by` naming it and appears in `sidecars` with its own spelling; a reject with `no_archive_copy` and a kept sidecar gets `kept` (verdict-major); `bytes` and `sidecar_bytes` are exact.
3. Rule 27 per root (L14): root 1 lacking evidence alone, root 2 alone and both, with the failing root first, last and in the middle of three, each hold the row and show the bad root's state in `archive`; an unreadable file (`stat` raising `PermissionError`) holds and is not `missing`; a reject `no_archive_copy` with a sidecar `outside_backup` on one root gives `outside_backup`; `decide` with one or no evidence entry raises `ValueError`; a destination that is a placeholder by injected `attrs_of` gives `no_archive_copy` for the reject and, in a second case, for a sidecar.
4. Rules 26 and 28 keep by record: excluded, `old_state` and unreadable views are in no `kept_by`; a reject the live view keeps gets `kept_in_live` even when a shoot view also keeps it; the live view not recording a reject does not keep it; `path` takes the `orig` of the first rejecting view when two spell it differently.
5. Work (R18, rules 26, 28), one case each: develop work in a catalog that rejects the reject; develop work only in an `old_states` catalog; a pick of 1 only in another catalog; an empty develop state; a develop row with an unusable value (`'unknown'`) in an `old_states` view; each gives `kept_for_work`, with `kept_by` empty and `developed_in` naming the catalog, its role and `develop` or `pick`; `developed` 0 with a pick of 0 or -1 gives `on_list`, and so does an `old_states` catalog whose `hasDevelopAdjustmentsEx` is -1.0 (not edited, RC-18; the last two built with `synth`, `catalogs` and `latest_views`, since the develop rule is Task D's); develop work only in an excluded view gives `on_list`, with `developed_in` empty; a reject kept by record that also holds work gives `kept`.
6. Planted defects: four in `lrdup/rejects.py` (the per-root archive walk, the sidecar test, the coverage gate, the family hold) added; thirteen in all, each caught.
7. Family (rule 29, RC-19): a reject that a live view records only as the sidecar of a held raw and a shoot view rejects as a photo takes the raw's verdict and `held_by` names the raw, with the raw held by develop work only in an `old_states` view (`kept_for_work`), by `no_archive_copy`, and by `placeholder`; the raw clean and the JPEG missing alone (an absent sidecar is ignored) leaves the raw `on_list` and the JPEG `missing`; a JPEG failing alone in any other way holds the raw too, as in acceptance 2; both clean are both `on_list`; the raw sorted before the JPEG and after it give the same verdicts; a chain of two (S a sidecar of R1, R1 a sidecar of a held R0) holds all three.

Commit subject: `feat(lrdup): Decide the verdict of each reject`

---

### Task G: the three outputs

`Wave:` 3 · `Touches:` lrdup/reject_out.py, tests/test_reject_out.py · `After:` E · `Mode:` code

**Behaviour:** `write_outputs` as in the Dispatch, on literal `Reject` and `SummaryInput` values. Summary: `## Per verdict` lists all ten verdicts (zero too) with files and bytes, a reject's bytes including its `sidecar_bytes`, and a reject that is also an existing sidecar of an `on_list` reject (its `paths.norm` is among that reject's `sidecars`) adds no bytes, since they are counted with the reject it travels with (RC-23); `## Per catalog` lists each live and shoot view with the rejects it rejects (from `rejected_by`); `## Rejected in part` lists `partly_rejected(views)`; `## Kept in the live catalog` counts rejects whose `kept_by` holds a `live` role, whatever the verdict; `## Not checked` lists each excluded view with its status, every `limiting` cause, and the sentence "Catalogs the tool never read are not known to it." Each file is written as `<name>.tmp` first. Only when all three are written, `rejects-summary.md` is deleted (as `report.write_report` deletes `summary.md`, so a folder without a summary is never taken for a finished run) and the three `.tmp` files are replaced in the order `rejects.csv`, `reject-removal-list.csv`, `rejects-summary.md`. A failure while writing a `.tmp` removes every `.tmp` and changes nothing else; a failure while replacing removes the remaining `.tmp` files, leaves no `rejects-summary.md` and raises.

**Acceptance:**
1. Columns (rule 30): two roots and three roots give the exact header of the Dispatch, `archive_<n>` and `dest_<n>` per root; one row per reject, sorted; list cells use `;`, a `developed_in` cell reads `<source> (<role>: develop+pick)`; the removal list holds only `on_list` rows (a `kept_for_work` reject is in `rejects.csv` only) and no row for an `on_list` reject that is also an existing sidecar of another `on_list` reject (RC-23: one path, one place; it keeps its `rejects.csv` row, verdict `on_list`, and appears in that reject's `sidecars`; the summary's per-verdict bytes count it once); with no reject both CSVs hold the header alone and the call returns three paths; `how_decided` reads `images: <catalog>=<n>; ...` and ends `; held by: <path>` when `held_by`; a case for RC-23: a raw and its JPEG both `on_list`, the JPEG listed in the raw's `sidecars`, give two `rejects.csv` rows, one removal-list row (the raw's) and the JPEG's bytes once in `## Per verdict`.
2. Summary (rule 30): `Generated:` on line 2; every verdict, per-catalog count, partly rejected path, the live-keep count (a `coverage_incomplete` reject the live view keeps is counted), each excluded view, each limiting cause and the never-read sentence appear; no `\r`; CSVs start with the BOM.
3. All or nothing (rule 25): with a folder standing at `out/<name>.tmp` for each of the three names in turn, the call raises, the three older files are byte-equal and no `.tmp` file remains; with a folder standing at `out/rejects.csv` (the replace step fails) the call raises, no `.tmp` file remains and `out/rejects-summary.md` is absent; a rerun with the same input gives byte-identical files.

Commit subject: `feat(lrdup): Write the reject list, removal list and summary`

---

### Task H: the `rejects` subcommand

`Wave:` 4 · `Touches:` lrdup/cli.py, tests/test_cli.py · `After:` A to G · `Mode:` code

**Behaviour:** parser `rejects` (it opens the database). `_stale_catalogs(run)` is the stale loop of `_report` moved out unchanged. `_old_rejects_note(base)` is `_old_report_note` for `out/rejects-summary.md`. `_rejects(run)` in this order: archive roots (`_require_archive_roots`, then fewer than two is `ConfigError("archive_roots", ...)`); then, when `live_catalog` is not `None`, `ConfigError("excluded_catalogs", "the live catalog is excluded and could keep nothing")` when `paths.norm(live_catalog)` equals `paths.norm` of an entry of `excluded_catalogs` (rule 26; a `None` live catalog is still exit 4 by rule 25); `latest_views`, `check_fresh` (`StaleFlags` becomes `StepMissing`, expected roles from `catalogs.role_of` over `run.cfg`); `_unseen_catalogs`; `classify.coverage` with `stale` and `unseen` (not `_coverage` or `_check_fresh`, which read files scans, rule 31); then, for each entry of `catalog_roots` that is not a directory now (`not os.path.isdir(paths.fs_path(root))`; `_unseen_catalogs` skips such a root, so a catalog put there after its scan would go unseen), append `"<root> (not connected now)"` to the causes and take `classify.Coverage(False, causes)` as the coverage (`report` is left unchanged); `load_evidence(out_dir, roots, exists=run.exists, stat=run.stat)` (`ManifestError` becomes `ConfigError("out", ...)`); `decide(..., stat=run.stat, attrs_of=run.attrs_of)`; `write_outputs` with `generated=run.now()`, every latest `catalogs` scan (the scans the views came from, whatever the configured roots; `files` scans are not listed; RC-21), `limiting=` the causes; one printed line. Every refusal message after the configuration is read ends with the note when the summary exists.

**Acceptance:**
1. Refusals (rules 25, 27, 31): no `archive_roots`, one root, two roots on one injected volume, `live_catalog` named in `excluded_catalogs` (once as written, once with another spelling that `paths.norm` folds equal, one case each; RC-17), a malformed manifest and a malformed verified list exit 2; `live_catalog` `None`, a live catalog changed after `catalogs` (size and a new `.lock`, one case each), a moved role in `old_states`, no image row and all picks empty exit 4; each leaves `out/` byte-equal to before, and a pre-existing `rejects-summary.md` makes stderr say the reports are older and must not be acted on; with no `work/lrdup.sqlite` before the run, `rejects` exits 4, leaves `out/` byte-equal, and the database it created holds no row in any table.
2. Run (rules 24, 26, 29, 30; every image carries `developed` 0 unless the case is about work, R18): after `catalogs` on a synthetic tree with picks, `rejects` exits 0 and writes three files; with no reject it exits 0 and writes headers only; an unreadable shoot catalog makes every verdict `coverage_incomplete` and the removal list header-only; a shoot catalog in `excluded_catalogs` (never the live catalog, RC-17) keeps nothing and is named in the summary; a catalog in `old_states` does not keep by record, but a pick of 1 or develop work there gives `kept_for_work`; a catalog root renamed away after `catalogs` (once with a shoot catalog stored on it at the scan, once with a root that held no catalog at the scan and received one afterwards) makes every verdict `coverage_incomplete`, leaves the removal list header-only and names the root in `## Not checked`, and the same run with the root present gives `on_list`; a catalog scanned under a root since removed from the configuration still keeps and is listed under `## Scans`.
3. Rule 31: before and after a run the listing of the library root, the catalog folders and the archive roots is equal and only `out/` changed (the tool's own `work/lrdup.sqlite` and its `-wal` and `-shm` files excluded); with every `file` and `hash` row deleted and the snapshot folders removed the three outputs are unchanged; two runs with one `now` give identical files.
4. Phase A and B1 are unchanged: the existing `tests/test_cli.py` tests pass untouched, and `report` still builds its stale list the same way.

Commit subject: `feat(lrdup): Add the rejects subcommand`

---

### Task I: end-to-end tests

`Wave:` 5 · `Touches:` tests/test_e2e.py · `After:` H · `Mode:` code

**Behaviour:** the real modules joined through `cli.main` on a synthetic tree whose catalogs carry picks, (every image `developed` 0 unless the case is about work, R18) with two temp archive roots and an injected `volume_of`: `catalogs`, `inventory`, `backup-plan`, `backup-run --apply`, `backup-verify`, then `rejects`. A new class in `tests/test_e2e.py`.

**Acceptance:**
1. Rules 29 and 30 end to end: one reject with an `.xmp` sidecar, backed up and verified on both roots, is `on_list` with the sidecar, `dest_1` and `dest_2` in the removal list; in the same run one reject for each other verdict arises (kept elsewhere, kept in live, develop work in a catalog that rejects it, a source edited after the plan, a destination changed before `backup-verify`, a source deleted, a placeholder by injected `attrs_of`, a raw in an unselected folder, a destination that became a placeholder by injected `attrs_of`, an unreadable shoot catalog in a second run); the `.xmp` is recorded by the shoot catalog only, the live catalog having the raw; and one pair (RC-19, RC-23): a raw held by develop work in an `old_states` copy whose JPEG a live catalog records only as its sidecar and a shoot catalog rejects, both `kept_for_work`, and a clean pair where the JPEG is listed in the raw's `sidecars` and has no removal-list row of its own.
2. Rule 25 end to end: a flag set in a catalog after `catalogs` exits 4 with `out/` equal; after `catalogs` runs again the flag is read.
3. Rule 31 and R17 end to end: the listing (path, size, mtime, SHA-256) of the library root, the catalogs and both archive roots is equal before and after `rejects`; no file is removed.

Commit subject: `test(lrdup): Add the reject check end-to-end tests`

---

## Spec coverage

| Spec item | Task |
|---|---|
| "What `catalogs` adds": `image` table, pick and develop columns | A, B, D |
| Rule 24 a reject; keeps; old states, `partial`, sidecars | E (`is_rejected`, `is_kept`), D (data) |
| Rule 26 develop work and picks keep (R18), `kept_for_work` | E (`work_of`), F (verdict), G, H, I |
| Rule 25 fresh flags, exit 4, `out/` untouched | E (`check_fresh`), H |
| Rule 26 kept by record; not-checked catalogs named | E, F, G (`## Not checked`) |
| Rule 27 archive evidence, exit 2 refusals | C, H |
| Rule 26 the live catalog is never excluded (exit 2), RC-17 | H |
| Rule 28 develop work and picks named | D (data), E (`work_of`), F (`developed_in`) |
| Rule 29 removal list and sidecars, work on a sidecar, family hold, one path one place (RC-19, RC-23) | F, G, I |
| Rule 30 every reject, verdict order, columns, summary, exit codes | F, G, H |
| Rule 31 read-only | E (no `file` rows), H, I |
| Verification: a test per rule with the named cases | C, D, E, F, G, H |
| Verification: planted defects `rejects.json` (13: the design's eleven, the stale refusal and the family hold counted apart) | C (3), E (6), F (4) |
| Verification: reviewer's end-to-end run | I, Controller steps |
| Verification: real run is the controller's | Controller steps |
| "Not in this check" | Not in this plan |

## Controller steps

1. Before dispatch: add the `mutants-rejects` gate and the Gates block above to the workspace `constraints.md`, name `lrdup/store.py` as unshared until A lands, and commit this plan. Give every implementer `context.md` (RC-1 to RC-22) and the answers to the report's open questions.
2. After each landing: send the change note, run `gate-runner` at that HEAD in a clean detached worktree. After C, E and F the `mutants-rejects` gate must report 3, 9, then 13 defects caught.
3. After I: a reviewer runs `catalogs`, `inventory`, `backup-plan`, `backup-run --apply`, `backup-verify` and `rejects` by hand on a synthetic tree built by `synth`, with a catalog written in the real Lightroom layout (the join column of RC-12); the controller reads one `rejects.csv`, one removal list and the summary (L12, L13).
4. Configuration (RC-9): before the real run, add the backup copy of the live catalog to `old_states` in `work/config.json` (an untracked file) and confirm `archive_roots` names the two archive drives, each a separate physical disk (the guard compares drive letters only: a SUBST or mapped letter that points at the other archive's disk would pass it and count one copy twice).
5. Real run, after the owner has culled and closed Lightroom and been told (L19): `catalogs` on the new code, then `rejects`; read the summary first (kept in the live catalog, not checked, rejected in part). Take a listing of the library root before and after; they must be equal. Show the owner the removal list and its SHA-256. A refusal with exit 4 means run `catalogs` again, never edit `out/`. Tell the owner that an unreadable or partial catalog named in `old_states` still makes coverage incomplete (rule 5 counts every non-excluded catalog) until it is put in `excluded_catalogs`; the summary names it. Tell the owner too that develop work or a pick in any catalog read, `old_states` included, and an empty develop state, keep a photo (`kept_for_work`, R18). Tell the owner that a JPEG a catalog lists as its own photo before its raw declares it a sidecar does not hold that raw.
6. Edit the shared files (`CLAUDE.md`, `docs/**`, `README.md`) for what the run teaches; none is a task's `Touches`.

## Not in this plan

- Phase B2: removal, staging, moving, offload, undo; removing photos from a catalog (done in Lightroom after the files leave the disk, R17).
- Any write to a catalog database, any use of the plug-in, any change to a flag.
- Hashing or reading file content (phase B2 hashes both sides again before anything leaves); a `rejects` option or configuration key; a new `main` seam.
- Distinguishing path spellings that `paths.norm` does not fold (8.3 aliases, junctions): a known limit of rule 3 (C-48 keeps `paths.norm` on `str.lower()`).
- Telling two drive letters that name one disk (SUBST, a mapped alias) apart: `paths.volume` compares the drive letter, as phase B1 ruled (B-5); the controller checks the configuration (Controller steps, step 4).
- A file a catalog records both as a photo and as a sidecar of another photo: `_read_records` keeps the first record of a path form (RC-13, question 9), so when the photo row comes first the file has no sidecar record and a rejected raw is not held by it (rule 29); when the sidecar row comes first it is held through its `image` row. Nothing is put on the list that a catalog keeps by record (pinned by Task D, acceptance 5).
