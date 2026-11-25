# Reject Check R22 Implementation Plan (rule 26a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reject whose only hold is its own develop work no longer stays when it is a byte-identical copy of a twin that stays, with the same develop settings (R22, rule 26a). `catalogs` stores a digest of each image's develop settings, a new module finds the twin through the stored full hashes, `rejects` lifts the develop hold under five conditions, and the outputs name the twin. Nothing else changes, and nothing of phase B2 is built.

**Architecture:** One new `image` column (`settings`) with a generic column migration in `store.connect`. New `lrdup/reject_twin.py` holds the counted full hash and the twin candidates (the file facts); `lrdup/rejects.py` holds the view facts and the lift. `reject_out.py` and `cli.py` follow. Tasks of one wave touch disjoint files.

**Tech Stack:** Python 3.13 standard library (`sqlite3`, `hashlib`, `os`, `unittest`); Windows paths.

**Spec:** `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "The reject check" at commit bed0cef (the `image` paragraph, rules 26, 26a, 28 to 31, the Frozen schema line, the Verification, "Not in this check"; the strict reading of condition 5 and the outside read's rulings are in it). Owner ruling R22 (`CLAUDE.md`); controller rulings RC-1 to RC-47 (workspace `context.md`; RC-29 to RC-47 are this change, RC-42 to RC-47 follow the outside read of this plan; RC-37 and RC-38 supersede parts of `constraints.md` and RC-16).

Sections: Global Constraints=brief, Review Focus=reference, Dispatch=reference, Tasks=brief, Sweep=reference, Spec coverage=reference, Controller steps=reference, Not in this plan=reference

## Global Constraints

- Rule 26a and what it needs (RC-29); the reject check as built otherwise unchanged. No mover, staging, removal or undo, no write to any catalog, no use of the plug-in, no new configuration key, option, exit code or `main` seam (RC-5). `rejects` still writes only under `<base>/out/`, apart from the one write outside it: the column migration `store.connect` makes when it opens a work database made before `settings` (RC-32, RC-46).
- Release evidence (RC-43, rule 26a.2): what lets a copy go (the twin's records by the shoot and the live catalog, the twin's matching live image) is read only through the absolute path form; what keeps a copy (the reject's develop work, picks and its shoot record, conditions 3 to 5) counts in either form.
- Python 3.13, standard library only, `unittest`, LF files; tests run as `python -m unittest discover -s tests`. PRIVATE DATA: fixtures are plainly synthetic (`shoot-a`, `IMG_0001.ARW`), every test builds its own tree in `tempfile.TemporaryDirectory()`; no real path or name anywhere.
- Fail closed: any doubt (an empty `settings`, an unreadable develop state, a `stat` error, a hash that does not count, a scan that is not complete) keeps the reject as rule 26 does today. An empty `settings` (NULL, `""`) never equals another, empty or not.
- Seams (RC-6): `stat` and `attrs_of` reach the new code as parameters, `attrs_of(path, st) if attrs_of else getattr(st, "st_file_attributes", 0)` then `paths.is_placeholder`. The file index from `stat` is `paths.signed64(st.st_ino)`, compared as `classify._changes` does; the two-files test (RC-42) compares the raw (`st.st_dev`, `st.st_ino`) of two `stat` results.
- Only what rule 26a lifts changes: a pick always keeps, a sidecar's own develop work still holds the row, coverage (rule 5), archive evidence (rule 27) and sidecars (rule 29) are tested as before. RC-38 reads RC-16 through rule 26a.
- Tests (RC-7, L10, L14): one per behaviour, near-duplicates parametrized, a case per field of a multi-field comparison and per side (reject, twin), the offending item not first and not only in the live catalog. New and edited decision lines are one expression on one line, so a planted defect can replace them. Code tasks never commit; the controller lands each patch with the task's subject, no trailer. No pre-commit configuration exists.

## Review Focus

- A twin that is itself a reject, or a sidecar of a reject that is lifted: it never goes on the list (rule 26a.2, rule 29).
- A hash that was true when `hash` ran and is stale now (file changed, replaced, a placeholder): no counted hash, the reject stays.
- Equal settings only in an old state or a shoot catalog, a virtual copy with other settings, an empty digest: the reject stays.
- A path whose catalog record is only a sidecar or only the relative form: still a record for condition 3, never evidence for condition 2 (RC-43).
- A twin that is the reject's own file under another path (a junction, a hard link): the reject stays (RC-42).
- A work database made before `settings` existed: opens, keeps its rows, lifts nothing.

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | L, M | immediately | 2 code phases + 2 nested gate-runners = 4 |
| 2 | N, O | L, M landed | 2 + 2 nested + reviews of L, M = 6 |
| 3 | P, Q | N, O landed | 2 + 2 nested + reviews of N, O = 6 |
| 4 | R | P, Q landed | 1 + 1 nested + reviews of P, Q = 4 |

Peak 6, cap 12. The controller lands a wave's patches before it dispatches the next. Files several tasks change are changed in successive waves only (`tests/mutants/rejects.json`: M, O). `lrdup/store.py` is touched by L alone, by RC-37, and is controller-only again once L has landed. Landing order: L, M, N, O, P, Q, R. Per patch: `git apply --check --index <patch>`, `git apply --index <patch>`, one commit with the task's subject, then `gate-runner` at that HEAD in a clean detached worktree (L5, L6). Review: one `sdd-reviewer` per task with the same CONTEXT as its implementer. Each task's tests run on its own `After:` closure; a test that needs another task's output as data builds it literally (the `HashIndex` for O, a `Reject` for P) and R joins the real producers to the real consumers.

**Frozen interfaces.** An implementer that needs one changed returns NEEDS_CONTEXT.

*Schema and migration (L).* `TABLES["image"] = ("catalog_id", "path_norm", "via", "pick", "developed", "settings")`; `settings` is TEXT (not in `_INT_COLUMNS`). `store.connect` after its `CREATE TABLE IF NOT EXISTS` loop adds every column of `TABLES` that an existing table lacks with `ALTER TABLE "<t>" ADD COLUMN "<c>" <kind>` (the kind rule of `_create_sql`, no key clause), then commits; old rows hold NULL. An empty `settings` is NULL or `""` and every reader treats both alike.

*Settings digest (N).* `catalogs` stores `settings` as the lowercase hex SHA-256 of `Adobe_imageDevelopSettings.text` encoded as UTF-8, else NULL. `CatalogRead.images` dicts gain key `settings` (`str | None`). Synthetic catalogs (N): an entry key `settings` (stored as given in the column `text` of `Adobe_imageDevelopSettings`); the table gets `text` when some entry has the key, and `develop_columns` accepts `"text"`; `make_unclean` carries it. Existing entries and tests behave as today (no key, no `text` column, `settings` NULL).

*Twin lookup (M), `lrdup/reject_twin.py`.* `Hashed(NamedTuple)`: `path_norm`, `path_orig` (the path's `file` row), `size`, `mtime_ns`, `file_index` (ints), `full: str`. `HashIndex(NamedTuple)`: `by_path: Mapping[str, Hashed]` (`path_norm` to its counted full hash, `stat` not yet checked), `by_hash: Mapping[tuple[str, int], tuple[Hashed, ...]]` (`(full, size)` to the paths holding it, sorted by `path_norm`). `EMPTY = HashIndex({}, {})`. `load_hashes(conn, library_roots: Sequence[str]) -> HashIndex`: rows of the latest `files` scan of each configured library root whose status is `complete`; a `hash` row counts for a path when its `full` is non-empty, its `file` row (any scan) has the path and the volume of the path's row, and its `size`, `mtime_ns` and `file_index` equal the path's row; a path is in the index when at least one row counts and every counting row holds one `full`. It raises nothing for a missing or incomplete scan (the path is just absent). `twin_candidates(index, path_norm, *, stat=os.stat, attrs_of=None) -> list[Hashed]`: `[]` unless `path_norm` is in `by_path` and `stat` now gives its row's size, mtime and file index; else every other path of `by_hash[(full, size)]` whose own `stat` stands the same way, is not an online-only placeholder, and gives a (`st_dev`, `st_ino`) that differs from the one the reject's `stat` now gave (two names of one file, a junction or a hard link, are never twins, RC-42), in `path_norm` order. Any `OSError` of a `stat` excludes that path. It reads no file content. Signature, `Hashed` and `HashIndex` are as above; RC-42 changes only which paths come back.

*Decision (O), `lrdup/rejects.py`.* New last fields with defaults: `CatalogView.settings: Mapping[str, list[str | None]]` (`path_norm` to the `settings` of each image row of either path form, aligned with `developed`; default empty, which reads as empty `settings` for every image); the existing `CatalogView.absolute` (paths with an image row of path form `absolute`, RC-25) is the absolute-form record (RC-48), and `CatalogView.absolute_settings: Mapping[str, tuple[tuple[int | None, str | None], ...]]` (`path_norm` to the (`developed`, `settings`) pair of each image row of path form `absolute`, one pair per image) defaults empty, so a view built without it gives no release evidence (RC-43). `Reject.twin: str = ""` (the twin's `path_orig`), `Reject.twin_settings: str = ""` (the shared digest); both set only when rule 26a lifted the reject's develop hold, whatever the final verdict. `decide(views, evidence, *, coverage_complete, stat=os.stat, attrs_of=None, hashes=reject_twin.EMPTY)`: with the default nothing is lifted, so every existing caller is unchanged; `decide` passes its own `stat` and `attrs_of` on to `twin_candidates(hashes, path_norm, stat=stat, attrs_of=attrs_of)`. `VERDICTS` is unchanged. `rejects` reads `file` and `hash` rows only through `load_hashes` (rule 31).

*Outputs (P), `reject_out.py`.* `out/rejects.csv`: `path, bytes, verdict, rejected_by, kept_by, archive_1 .. archive_<roots>, developed_in, twin, twin_settings, how_decided`. `out/reject-removal-list.csv`: `source, bytes, mtime_ns, sidecars, sidecar_bytes, dest_1 .. dest_<roots>, twin` (last). Summary: a new section `## Lifted by rule 26a` between `## Kept in the live catalog` and `## Not checked`, one line `Rejects on the list whose develop hold rule 26a lifted: <n> files, <b> bytes`, counted as the per-verdict table counts (an `on_list` reject with a `twin`, sidecar bytes included).

*Wiring (Q).* `cli._rejects_run` passes `hashes=reject_twin.load_hashes(run.conn, cfg["library_roots"])` (library roots only, not backup roots) to `decide`. No refusal for a missing or partial files scan: no counted hash, nothing lifted. The stdout line and exit codes are unchanged.

Change notes (sent when a task lands, to tasks running or staged):

| Task | Changes | Who is told |
|---|---|---|
| L | `image.settings`, generic migration in `store.py` | N, O, Q, R |
| M | new `reject_twin.py` (two-files test included); `rejects.json` (17) | O, Q, R |
| N | `catalogs.py` stores `settings`; `synth.py` key `settings` | Q, R |
| O | `CatalogView.settings`, `.absolute_settings`, `Reject.twin`, `Reject.twin_settings`, `decide(..., hashes=)`; `rejects.json` (20) | P, Q, R |
| P | the output columns and summary section | R |
| Q | `cli.py` passes the hashes | R |

Gates block for the workspace `constraints.md` (the `when:` of `mutants-rejects` gains the new module and both new test files, RC-33):

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
  when: any of lrdup/rejects.py, lrdup/reject_archive.py, lrdup/reject_twin.py, tests/test_rejects.py, tests/test_rejects_verdicts.py, tests/test_rejects_lift.py, tests/test_reject_archive.py, tests/test_reject_twin.py, tests/mutants/rejects.json changed
```

`mutants-rejects` reports 14 caught today, 17 after M, 20 after O (14 + 6: hash equality, carried hash's validity, two files (M); twin-stays, no shoot record, settings over every catalog read (O); no pick defect, RC-47). The `pattern` `test_reject*.py` already matches both new test files; each `old` is one line occurring once in its `file`, and the 14 existing `old` lines must still occur once after O.

## Tasks

### Task L: the `settings` column and the migration

`Wave:` 1 · `Touches:` lrdup/store.py, tests/test_store.py · `After:` none · `Mode:` code

**Behaviour:** the schema and migration of the Dispatch. Nothing else in `store.py` changes.

**Interfaces:** produces *Schema and migration (L)*; consumes none.

**Acceptance:**
1. Schema (Frozen schema line): `test_schema_is_exactly_the_frozen_tables_and_columns` (its `SCHEMA` gains `image.settings` TEXT) passes; the other tables are unchanged.
2. Migration (RC-32): a database made by hand whose `image` table has the five old columns and one row, opened with `connect`, gains `settings`, keeps the row, and reads `settings` NULL; a second `connect` changes nothing.
3. Generic (RC-32): a hand-made `catalog` table lacking one non-key column gains that column too, so no one-off migration exists; a database already current is untouched.

Commit subject: `feat(lrdup): Add the settings column to image and migrate older work databases`

---

### Task M: counted full hashes and twin candidates (rule 26a.1 and the file facts of 26a.2)

`Wave:` 1 · `Touches:` lrdup/reject_twin.py, tests/test_reject_twin.py, tests/mutants/rejects.json · `After:` none · `Mode:` code

**Behaviour:** `load_hashes` and `twin_candidates` as frozen. A path's counted hash comes from the latest complete `files` scan of its library root and `hash` rows carried by path from any earlier scan; `stat` now must give the row's size, time and index, and the reject and a candidate must be two files: the `stat` now of each gives a different (`st_dev`, `st_ino`) (RC-42; M decides it, so no other task changes). Tests build `scan`, `file` and `hash` rows with `store` in a temp database and inject `stat` and `attrs_of`.

**Interfaces:** produces *Twin lookup (M)*; consumes the existing `scan`, `file` and `hash` tables, `store.latest_scans`, `paths.norm`, `paths.fs_path`, `paths.signed64`, `paths.is_placeholder`.

**Acceptance:**
1. Counting, rule 26a.1, one case per field and per side (reject, twin): a `hash` row whose size, mtime or file index differs from the path's row; a `hash` row whose `file` row has another volume; an empty `full`; no `hash` row; each leaves no candidate, beside a case where all hold and the twin is returned.
2. `stat` now, rule 26a.1, per side: size, mtime and file index each differing alone; `stat` raising `FileNotFoundError` and `PermissionError`; each leaves no candidate.
3. Scans, rule 26a.1, per side: two counting rows of one path that disagree; the latest scan of the root `partial`; the path absent from the latest scan; the path only in a backup-root scan; a hash made on the first of three scans (a later scan holds none, a skipped scan between) counts, while the same row with a size that differs from the latest row does not.
4. Match, rule 26a.1 and 26a.2 file facts: full hashes differ; sizes differ with one hash; the reject itself is never its own candidate; two passing paths come back in `path_norm` order; a twin that is gone or a placeholder (injected `attrs_of`) is dropped while a later one is returned.
5. Two files, rule 26a.1 (RC-42), on the twin's side, the offending path not first: a candidate path whose `stat` gives the reject's (`st_dev`, `st_ino`) (a junction name, by an injected `stat`) is dropped while a later path is returned; the same through a real hard link made with `os.link` in the temp tree and the real `os.stat`; one case per field of the pair (same `st_ino` with another `st_dev`, and the reverse, each still a candidate).
6. Planted defects (RC-33): three added to `rejects.json` in `lrdup/reject_twin.py`, the hash equality test (size alone groups paths), the carried hash's validity test and the two-files test (`st_dev`, `st_ino`); `mutate.py --jobs 3` run once in the worktree reports 17 caught.

Commit subject: `feat(lrdup): Add counted full hashes and twin candidates for rule 26a`

---

### Task N: `catalogs` stores the settings digest

`Wave:` 2 · `Touches:` lrdup/catalogs.py, tests/synth.py, tests/test_synth.py, tests/test_catalog_images.py · `After:` L · `Mode:` code

**Behaviour:** the digest and synthetic key of the Dispatch. `_read_images` joins `Adobe_imageDevelopSettings` when it has a flag column or `text`, and reads `text` when the column exists; `developed` is unchanged. `settings` is NULL when the table, the column or the image's row is missing, or the text is NULL, empty or not a `str`; an image with several settings rows has a digest only when all their texts hash alike. `synth.py` still imports nothing from `lrdup`.

**Interfaces:** produces *Settings digest (N)*; consumes the `settings` column of L.

**Acceptance:**
1. Digest, `image` paragraph: text `"x"` gives the SHA-256 hex of `b"x"`, a non-ASCII text the digest of its UTF-8 bytes, and every path form of one image carries the same digest.
2. Empty is empty, one case each: text NULL, `""`, a number; the `text` column missing; the table missing; the image with no settings row; two rows with different texts; one row NULL and another with text (a digest exists only when all rows hash alike); each stores NULL, and `developed` is what it was without `text` (RC-18 cases unchanged).
3. Virtual copies: a master and a copy with other texts store their own digests; equal texts store equal digests.
4. Opt-in: a catalog made without the key has no `text` column; `tests/test_synth.py` gains `settings` cases (stored as given with its `typeof`, `develop_columns=("text",)`, `make_unclean` reads it from the `-wal`), and `tests/test_catalogs.py` passes unedited.

Commit subject: `feat(lrdup): Store a digest of each image's develop settings`

---

### Task O: the lift (rules 26, 26a, 28, 29)

`Wave:` 2 · `Touches:` lrdup/rejects.py, tests/test_rejects_lift.py, tests/mutants/rejects.json · `After:` L, M · `Mode:` code

**Behaviour:** `_view` reads `settings` and `absolute_settings`; `decide` takes `hashes`. For a reject that holds develop work in some catalog read, in this order: condition 3 (no catalog read with role `shoot` records it, either path form, photo or sidecar), 4 (`work_of` names no pick in any catalog read), 5 (every image of it with `developed` 1 or empty, in any catalog read, old states included, has `developed` 1 and one same non-empty `settings` S, an image with `developed` 0 not counted; all such images share the one S, the design's "the shared `settings`"), then `twin_candidates` and, per candidate in order, condition 2 (a catalog read with role `shoot` records it as a photo, and the live catalog has an image of it with `developed` 1 and `settings` S, both through the absolute form only: `absolute` and one (1, S) pair of `absolute_settings`, RC-43; conditions 3 to 5 keep reading either form). The first passing candidate is the twin: `Reject.twin` its `path_orig`, `twin_settings` S. A lifted reject's own `kept_for_work` test is skipped; a sidecar subject's is not. The module docstring (line 4, the sweep) says `file` and `hash` rows are read only through `reject_twin` (rule 31). `decide` hands its `stat` and `attrs_of` to `twin_candidates`. Tests use a fake `stat` giving `st_ino` equal to each row's `file_index` and a literal `HashIndex`; they import `view` and `World` from `test_rejects_verdicts`, build views with `view(...)._replace(settings=..., absolute=..., absolute_settings=...)` or a literal `CatalogView` (its `view` has none of these parameters), and give `World.stat` an `st_dev` and an `st_ino` per path (its default has neither; a different `st_ino` per path keeps the two-files test of M quiet). The cases that need a stored path with several images are built in a temp database with `store` and read through `latest_views`.

**Interfaces:** produces *Decision (O)*; consumes *Schema (L)* and *Twin lookup (M)* (`HashIndex`, `Hashed`, `EMPTY`, `twin_candidates`).

**Acceptance:**
1. Rule 26a all hold: the reject goes `on_list` with `twin`, `twin_settings` and `developed_in` still naming the work (rule 28); the same views with the default `hashes` give `kept_for_work` (rule 26 unchanged); a lifted reject held by `kept`, `coverage_incomplete`, `kept_in_live` or an archive state keeps its twin columns and its other verdict (one parametrized case; `missing` is not in it: a reject with no file has no counted hash, so nothing is lifted and its twin cells are empty, RC-45).
2. Conditions 2, 3, 4, one case each failing alone: twin not an existing photo (file gone; a placeholder by the fake `stat`'s `st_file_attributes`; a placeholder by an injected `attrs_of` alone, which fails if `decide` does not pass `attrs_of` on), recorded only as a sidecar by the live catalog or by the shoot catalog, not recorded by the live catalog, recorded only by an old state or by a shoot catalog that is excluded or `unreadable`, its live image `developed` 0 or empty, equal settings only in a shoot catalog or an old state; its record by the shoot catalog only through the relative form, its live image only through the relative form (RC-43: a record or a matching live image of the twin seen only there keeps the reject); a twin with two live images, one `developed` 1 with other settings and one `developed` 0 with S (the developed flag and the digest must sit on one image, RC-45); a shoot catalog records and rejects the reject as a photo (condition 3 alone holds the row), records it without rejecting it, records it as a sidecar, records it through its relative form only (this one keeps the reject, as before); a pick in an old state (RC-47: a pick in the live catalog makes the live catalog keep the reject first, so it isolates nothing here and has no case). Each leaves `twin` empty and the verdict is not `on_list`: `kept_for_work` for the cases whose only hold is the work, `kept` where the shoot catalog keeps it by record (rule 24), so deleting a condition makes its case fail on `twin`.
3. Condition 5 (L10, L14): other settings; empty on the reject's image; empty on both; `developed` empty; a virtual copy, then an old state, then a later live image each with other settings (the offending image not first); two reject images matching different twin images; an old row with NULL read through `latest_views`; none lifts. A stored path with two images of different `settings` read through `latest_views` and `decide` (rows inserted in a temp database, digests S and U, one case with S first and one with U first) keeps the reject, so a loader that repeats or drops a digest fails (RC-45). Another reject image with `developed` 0 does not block.
4. Rule 26a, also: both copies are rejects, one rejected and recorded by a shoot catalog, the other recorded by none: only the second is `on_list`, the first `kept_for_work`; two twins where the first by `path_norm` fails condition 2 and the later passes (the later is named); the twin, an existing sidecar of another reject that is lifted, holds that reject's row (rule 29); a sidecar's own develop work still holds the row.
5. Planted defects (RC-33): three added in `lrdup/rejects.py`: the twin-stays test (shoot record and live `developed` 1), the reject's no-shoot-record test, and the settings test over every catalog read (live catalog only); `mutate.py --jobs 3` run once reports 20 caught. No pick defect is planted (RC-47): the existing tests of rule 26 already fail when the pick hold is dropped. The existing `test_rejects*.py` files pass unedited: their develop-work cases use the default `hashes` (sweep, below).

Commit subject: `feat(lrdup): Lift the develop hold on a reject whose twin shares its settings`

---

### Task P: the outputs name the twin (rules 28, 30)

`Wave:` 3 · `Touches:` lrdup/reject_out.py, tests/test_reject_out.py · `After:` O · `Mode:` code

**Behaviour:** the columns and summary section of the Dispatch. `twin` and `twin_settings` hold the `Reject` fields as they are; the removal list repeats `twin` for its rows. Tests edit, in the existing file, for the two new `rejects.csv` cells and the new last cell of the removal list: the whole-row expectation (lines 61 to 66), the expected headers of both files (75, 77 to 78), `rows[2][-2]` (90, now `twin_settings`; `developed_in` moves to `[-4]`) and the removal row (98, a trailing empty cell). Line 91 (`[-1]`, `how_decided`) stays; so do its literal `kept_for_work` rows (94, 210).

**Interfaces:** produces *Outputs (P)*; consumes `Reject.twin` and `Reject.twin_settings` of O.

**Acceptance:**
1. `rejects.csv`, rule 30: the header order, a lifted row with both cells, an unlifted row with both empty, a lifted row held by another verdict still showing them.
2. Removal list, rule 30: `twin` is the last column after `dest_<roots>`, filled for a lifted row, empty for another; a reject that travels as a sidecar has no row (RC-23).
3. Summary, rule 30: the section sits between `## Kept in the live catalog` and `## Not checked`; with no lifted reject the line says `0 files, 0 bytes`; a lifted `on_list` reject counts with its sidecar bytes; a lifted reject held by another verdict is not counted.
4. Read-through: a `Reject` built without the new fields writes both cells empty.

Commit subject: `feat(lrdup): Report the twin of a lifted reject in the outputs and the summary`

---

### Task Q: `rejects` passes the library hashes (rule 31)

`Wave:` 3 · `Touches:` lrdup/cli.py, tests/test_cli.py · `After:` M, N, O · `Mode:` code

**Behaviour:** the wiring of the Dispatch. The test `test_only_out_changes_and_the_outputs_do_not_depend_on_files_scans_or_snapshots` (lines 1177 to 1198, a sibling of the rule 31 sweep hit) stays as it is: it already proves that a run with no lift does not depend on the `file` and `hash` rows. Tests run `catalogs`, `inventory`, `hash` and `rejects` through `cli.main` on a tree with a reject and a byte-identical twin.

**Interfaces:** produces *Wiring (Q)*; consumes `load_hashes` (M), the synthetic key `settings` (N), `decide(..., hashes=)` (O).

**Acceptance:**
1. Wiring, rule 26a: after the three steps the reject is `on_list`; with the `hash` step not run, with its `hash` rows deleted, and with the library root's files scan `partial`, it is `kept_for_work`, exit 0, and no new refusal. The deletion case is the rule 31 counterpart: with the lift, deleting `file` and `hash` rows changes the verdict from `on_list` to `kept_for_work`, and the same run with no lift (no `settings`) changes nothing; one parametrized test; only `out/` changes.
2. Library roots only: a twin held only under a backup root (recorded by a catalog, so the test builds it) is never a candidate.

Commit subject: `feat(lrdup): Pass the library hashes to the reject check`

---

### Task R: end-to-end test

`Wave:` 4 · `Touches:` tests/test_e2e.py · `After:` L, M, N, O, P, Q · `Mode:` code

**Behaviour:** one new test class beside `RejectCheckEndToEndTests` on its own synthetic tree: two byte-identical raws under the library root, the reject recorded and rejected by the live catalog alone with `settings`, the twin recorded by a shoot catalog and the live catalog with the same `settings`; the real `catalogs`, `inventory`, `hash`, `backup-plan`, `backup-run --apply`, `backup-verify` and `rejects` through `cli.main`, a catalog in the real layout (`Adobe_imageDevelopSettings.text`). The existing verdict test (lines 536 to 550) stays: its fixtures give no `settings`.

**Interfaces:** produces nothing; consumes every frozen paragraph above through the real commands.

**Acceptance:**
1. Rule 26a through the real chain: the reject is `on_list`, `rejects.csv` shows `twin` and `twin_settings` (the SHA-256 of the settings text), the removal list shows `twin`, the summary line counts 1 file; the twin is not on the list.
2. The same tree with one byte of the twin changed after `hash`, and the test sets `os.utime` on the twin to a different time so the stat check cannot pass by equal timestamps: `kept_for_work` (rule 26a.1).
3. Shoot record: a shoot catalog that records and rejects the reject gives `kept_for_work` with no `twin` (rule 26a.3; unit cases cover the other settings and records).
4. Library, catalogs and archives unchanged and nothing removed (rule 31, R17).

Commit subject: `test(lrdup): Run rule 26a end to end on a synthetic tree`

## Sweep (RC-34)

Moved: `lrdup/rejects.py:4` and `:245` (O), `tests/test_reject_out.py:61, 65` (P, expected cells; P also edits 75, 77 to 78, 90, 98). Stay, because each runs with the default `hashes` (no counted hash) or builds a literal `Reject`, where rule 26 holds unchanged and the test still guards it: `rejects.py:17` (`VERDICTS` is unchanged), `tests/test_cli.py:1074, 1084`, `tests/test_e2e.py:542, 545`, `tests/test_reject_out.py:94, 210`, `tests/test_rejects_verdicts.py:127, 204, 205, 396, 440, 448, 472`. Found beyond the sweep: `tests/test_cli.py:1177 to 1198` (stays: no lift, nothing depends on `file` and `hash`; Q.1 holds the lift counterpart). Point-in-time, untouched: `docs/plans/2026-11-18-reject-check.md`, the outside-read prompt.

## Spec coverage

| Spec item | Task |
|---|---|
| `image` paragraph: `settings` digest; Frozen schema line; migration in `store.connect` | L, N |
| Rule 26a.1 identical, counted full hash, carried hash, both sides | M |
| Rule 26a.1 two files (`st_dev`, `st_ino`): a junction, a hard link (RC-42) | M |
| Rule 26a.2 the twin stays: file facts (M), shoot record and live `developed` 1 and settings, absolute form only (RC-43) (O) | M, O |
| Rule 26a.3 no shoot record; 26a.4 no pick; 26a.5 same settings, every catalog read | O |
| Rule 26a also: both copies rejects, several twins, twin as sidecar, sidecar's own work, old row lets nothing go | O, L |
| Rule 26 (develop work stops keeping only as 26a says; a pick always keeps) | O |
| Rule 28 names the twin and shared settings | O, P |
| Rule 29 removal list (work lifted; sidecar's work holds) | O, P |
| Rule 30 `twin`, `twin_settings`, `twin` on the removal list, the summary line, `kept_for_work` wording | P |
| Rule 31 reads `file` and `hash` only for 26a; the one write outside `out/` is the migration (RC-46) | M, Q, O (docstring), L |
| Verification, rule 26a bullets (1, 2, 3, 4, 5, also) | M (1), O (2 to 5, also), L (migration) |
| "Not in this check": the two known limits (RC-44) | Not in this plan, Controller steps |
| Planted defects: hash equality, carried hash's validity, two files | M |
| Planted defects: twin-stays, reject's no-shoot-record, settings over every catalog read | O |
| Verification: reviewer's end-to-end run | R, Controller steps |

## Controller steps

1. Before dispatch: replace the `mutants-rejects` gate in `constraints.md` with the Gates block above; give every implementer `context.md` (RC-1 to RC-47) and the answers to the writer's questions; commit this plan.
2. After each landing: send the change note, run `gate-runner` at that HEAD in a clean detached worktree. `mutants-rejects` must report 17 after M and 20 after O. After L, `lrdup/store.py` is a shared file again.
3. After R: a reviewer runs `catalogs`, `inventory`, `hash`, `backup-plan`, `backup-run --apply`, `backup-verify` and `rejects` by hand on a synthetic tree with a catalog in the real layout and a twin pair; the controller reads one `rejects.csv`, one removal list and the summary (L12, L13).
4. Edit the shared files: `CLAUDE.md` line 202 ("The reject check as built still keeps such a copy for its develop work") is false once O has landed; update it with what the run teaches.
5. Configuration (RC-36), in the untracked configuration: the backup copy of the live catalog that is still listed with role `shoot` goes into `old_states` by path.
6. Real run, after the owner has culled and closed Lightroom (L19): `catalogs` on the new code (older `image` rows hold an empty `settings` and lift nothing), then `rejects`; no re-run of `inventory` or `hash` is required (RC-40), and a missing or partial scan leaves no counted hash, lifts nothing and raises no refusal. Every reject that rule 26a lets onto the list must be one of the copies the owner ruled on (the list kept in `work/`); each such row shows its `twin` and `twin_settings` (H8). Any other lifted row stops the run until the owner sees it. Before the run, identify the drives by label and serial (RC-44: the tool stores no device identity). Take a library listing before and after (equal); show the owner the removal list and its SHA-256.

## Not in this plan

- Phase B2: removal, staging, moving, offload, undo; removing photos from a catalog; hashing the reject and its twin again before anything leaves.
- Any write to a catalog, any use of the plug-in, any change to a flag.
- Hashing or reading file content: `rejects` reads `hash` rows that `hash` stored, never a file.
- A new configuration key, option or exit code; lifting a pick; lifting a sidecar's own develop work; a twin outside the configured library roots.
- Two known limits of rule 26a (RC-44, design "Not in this check"): a drive letter reassigned between `hash` and `rejects` is not detected (no device column; phase B2 hashes both copies again, and the controller identifies the drives by label and serial before the real run); the tool reads no Lightroom stack, and a JPEG export of a raw is never byte-identical to it, so rule 26a cannot let a raw go through its export.
