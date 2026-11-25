# Backup of Every Photo or Video Folder Implementation Plan (phase B1, R23)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `backup-plan` selects every folder under `photo_root` that holds a photo or a video, not only those that hold a raw, and never makes a Lightroom catalog file a row (R23). It finds the folders by walking the tree itself, because the files scans record raw and JPEG files only. The summary shows what the plan left out. Nothing else of the backup changes.

**Architecture:** The change is in `lrdup/backup_plan.py`: a names-only walk replaces the selection from scan rows, the listing skips catalog files, and the summary gains the counts the design names. `lrdup/cli.py` changes by one keyword (`exclude`), since `plan_manifests` cannot otherwise know the configuration's `exclude`. The catalog records the preview-cache test needs are read from the work database inside `plan_manifests`, so `cli.py` carries nothing else.

**Tech Stack:** Python 3.13 standard library (`os`, `re`, `unittest`); Windows paths.

**Spec:** `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase B1" at commit 5fae173 (the `backup-plan` bullets, rule 16, Verification, Known limits) and rule 11 of phase A. Owner ruling R23 (`CLAUDE.md`; it supersedes R10 for catalog files only); controller rulings RC-50 to RC-52 (workspace `context.md`).

Sections: Global Constraints=brief, Review Focus=reference, Dispatch=reference, Tasks=brief, Spec coverage=reference, Controller steps=reference, Not in this plan=reference

## Global Constraints

- Exactly what the design's `backup-plan` bullets say, and no other rule of phase B1: `backup-run` and `backup-verify` are unchanged, no new configuration key, option or exit code (RC-51). `backup_plan.py` decides no verdict and fails closed (a folder left out is `outside_backup` in the reject check), so no planted-defect file is added or edited (RC-50). The gates `mutants-backup` and `mutants-rejects` do not fire: neither `when:` names `backup_plan.py` or its tests.
- The walk reads names only, never a file's content, follows no link or junction, and keeps its own stack (no depth limit, no recursion). Every path it lists goes through `paths.fs_path`; `stat` and `scandir` stay injectable as parameters (RC-6), with the defaults they have today.
- Python 3.13, standard library only, `unittest`, LF files. PRIVATE DATA: fixtures are plainly synthetic (`shoot-a`, `IMG_0001.ARW`); every test builds its own tree in `tempfile.TemporaryDirectory()`; no real path or name anywhere. No test or run touches a real folder.
- Tests: one per behaviour, near-duplicates parametrized (RC-7). A test of the planner writes only inside its temporary directory; its archive roots are temporary folders (L4). The existing tests of `test_backup_select.py`, `test_backup_summary.py` and `test_cli.py` whose expectation the change makes false are edited, not weakened: each such edit is named in its task.
- No pre-commit configuration exists. Code tasks never commit; the controller lands each patch with the task's subject, no trailer.

## Review Focus

- A folder holding only JPEG, HEIC, PNG or video files is selected; a folder holding only a catalog, a sidecar or text is not, and appears in the tally.
- A Lightroom catalog file is never a row in a selected folder, whatever its letter case, and the summary counts it.
- A preview cache is not entered; a storage container, an unknown container, and a preview-named container in which a catalog records a file are. With no `catalogs` scan, every `.lrdata` container is walked.
- A folder that cannot be listed is named, and the summary opens with it.

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | S | immediately | 1 code phase + 1 nested gate-runner = 2 |
| 2 | T | S landed | 1 + 1 nested + review of S = 3 |

Peak 3 agents, cap 12. T edits `lrdup/cli.py` and `tests/test_cli.py`, which task Q of the R22 plan also edits (RC-52): T lands before Q is dispatched, and when T lands the controller tells Q to start from T's commit. S's new fields reach T only after S has landed. S and T touch no file of the R22 plan. Per patch: `git apply --check --index <patch>`, `git apply --index <patch>`, one commit with the task's subject, then `gate-runner` at that HEAD in a clean detached worktree (L5, L6). Review: one `sdd-reviewer` per task with the same CONTEXT as its implementer.

Change notes:

| Change | Reason |
|---|---|
| T carries the `exclude` wiring in `cli.py` | `plan_manifests` cannot otherwise know the configuration's `exclude` (RC-54) |
| T lands before R22's task Q | both edit `lrdup/cli.py` and `tests/test_cli.py` (RC-52) |
| S.1 and T.2 hold a literal extension list | a test over `BACKUP_EXTS` itself would shrink with it (RC-55) |

**Frozen interfaces.** An implementer that needs one changed returns NEEDS_CONTEXT.

*Selection (S), `lrdup/backup_plan.py`.* `BACKUP_EXTS: frozenset[str]`: `paths.RAW_EXTS`, `paths.JPEG_EXTS`, `heic heif png tif tiff psd psb gif webp bmp`, `mp4 mov m4v avi mts m2ts 3gp mkv`, all lower case; `paths.PHOTO_EXTS` is unchanged (the inventory's scans stay as they are). `CATALOG_SUFFIXES = (".lrcat", ".lrcat-wal", ".lrcat-shm", ".lrcat.lock", ".lrcat-journal")`, tested on `name.casefold().endswith(...)`. `plan_manifests` gains one keyword, `exclude: Sequence[str] = ()`, after `own_dir`; no other parameter changes. A private `_walk_photo_root(photo_root, pruned, records, scandir) -> _Walk` takes names only: `pruned` is the `_key` set of `own_dir`, `exclude` and the archive roots, matched exactly on a folder's key before it is entered (as `inventory._walk` prunes); `records` is `None` when the work database holds no `catalogs` scan, else `inventory._lrdata_records(conn, ids of store.latest_scans(conn, "catalogs"))`. A folder named like a cache (`inventory._container_kind`, called with an object holding `lrdata_records`, gives `cache`) is pruned only when `records` is not `None`. `_Walk` holds `selected: dict[str, str]` (key to path, a folder with a `BACKUP_EXTS` file directly in it, extension by `casefold`), `unread: list[str]`, `links: int` (each symlink or junction met, files and folders), `not_selected: int` (folders holding files, none selected) and `not_selected_exts: dict[str, int]` (extension, `""` for none, to a count of those folders' files). The existing `_list_folders` still lists each selected folder afresh and statted; it now skips a catalog file and counts it, treats a pruned subfolder as neither left out nor entered, and adds a folder it cannot list to `unread`.

*Plan fields (S).* `RootPlan` gains a last field `catalog_files: int = 0` (the same number for every root). `BackupPlan` gains last fields with defaults: `unread_folders: tuple[str, ...] = ()` (paths, each once, in walk order), `links: int = 0`, `not_selected: int = 0`, `not_selected_exts: tuple[tuple[str, int], ...] = ()` (count descending, then extension). `unlisted` keeps its meaning and equals `len(unread_folders)` plus the files that could not be statted. `outside_raws` and `unread_roots` are as today: the files scan is read only for them, and a plan with no library root complete still raises `ManifestError`.

*Summary (T).* `write_plan` text: when `unread_folders` is not empty, its first section after the title, `## NOT READ: folders that could not be listed`, names each folder and says the plan is not one to approve until they are listed or the owner accepts their absence. Each archive-root section gains `- catalog files left out: <n>` right after `- subfolders left out`. A new section `## Selection`, once, after the roots and before `## Outside the photo folder`: `- extensions that select a folder: <BACKUP_EXTS sorted, comma separated>`, `- links and junctions not followed: <n>`, `- folders not selected: <n>`, then one line `  - <ext or (none)>: <files>` per tally entry. Other lines, the manifest columns, the stdout line of `backup-plan` and the exit codes are unchanged.

Gates block for the workspace `constraints.md`, unchanged:

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

## Tasks

### Task S: select by walking the photo root, and leave catalog files out

`Wave:` 1 · `Touches:` lrdup/backup_plan.py, tests/test_backup_select.py · `After:` none · `Mode:` code

**Behaviour:** *Selection (S)* and *Plan fields (S)*. The scan rows no longer select a folder. A folder under `photo_root` is selected when a `BACKUP_EXTS` file sits directly in it, whatever the folder is called. Selected folders are listed afresh as today, every regular file but a catalog file becoming a row. Tests build the tree and run `inventory.scan_files` as `Base` does; the injected `scandir` of the existing cases is passed to both stages.

**Interfaces:** produces *Selection (S)* and *Plan fields (S)*; consumes `paths.RAW_EXTS`, `paths.JPEG_EXTS`, `inventory._lrdata_records`, `inventory._container_kind`, `store.latest_scans`, `classify.root_states`.

**Acceptance:**
1. Extensions (design Verification): the test holds its own literal list, written out in the test and not read from `BACKUP_EXTS` or `paths`: `arw cr2 cr3 dng nef nrw orf pef raf rw2 srw jpg jpeg heic heif png tif tiff psd psb gif webp bmp mp4 mov m4v avi mts m2ts 3gp mkv`. One parametrized case per name (a folder holding one such file is selected, the file is a row), one upper-case spelling (`.JPG`), one off-list name (`.pdf`: folder not selected, in the tally), and one assertion that `BACKUP_EXTS` equals that literal set. A dropped type fails its case and the equality; an added type fails the equality.
2. Not selected: a folder holding only `notes.txt` and `IMG_0005.xmp`, and a folder holding only `shoot-a.lrcat`: none selected, `not_selected` is 2, and the tally holds `txt`, `xmp`, `lrcat` with their counts. Selection is by content: `card.lrdata` and `odd name` select by their files.
3. Catalog files (rule 16): in a folder with `IMG_0001.ARW`, each of the five suffixes (one upper-case) is no row and counts in `catalog_files`; the raw is a row. A name that only contains or begins with a suffix (`shoot-a.lrcat.jpg`, `my-lrcat-notes.txt`) is a row and not counted.
4. Containers (rule 11, RC-51, RC-52). Cases 1 to 3 run with a `catalogs` scan stored in the temporary database (a scan row, an `ok` catalog, and one `record` row of that scan for a file elsewhere in the tree, or none), because without one every `.lrdata` is walked and the prune would go untested; only case 4 runs without one (the `Base` tree, which has no `catalogs` scan). (1) a preview cache (`x Previews.lrdata`, no record in it) is not entered and its photo is no row; (2) a storage container (`Mobile Downloads.lrdata`) and an unknown container are walked and select; (3) a preview-named container in which a catalog records a file (a `record` row of the latest `catalogs` scan, as `inventory` reads it) is walked; (4) with no `catalogs` scan at all, the same cache as case 1 is walked.
5. Pruning: a folder equal to `exclude`, to `own_dir`, and to an archive root (placed inside the walked tree by the test) is not entered, its photo is no row, and it is not counted as left out; a sibling photo folder is selected.
6. Links (not followed, counted): a directory symlink or junction, and a file symlink where the platform allows one (skip with a message as the existing test does), is no row and no folder, `links` counts each; a photo folder beside it is selected.
7. Not read: a folder `scandir` refuses (a photo folder not first, one that holds a photo and an unlistable subfolder) is in `unread_folders` once and counted in `unlisted`; the other folders still plan; a selected folder whose second listing refuses is named too. A file `stat` refuses is counted in `unlisted`, not named.
8. Nested: `shoot-a` holding a `.jpg` with `with-raw` below it holding an `.arw`: both selected, each by itself; a subfolder holding only `readme.txt` is counted in `left_out` (the listing) and also in `not_selected` and the tally (the walk): two different counts of one folder, each asserted.
9. Existing cases edited, no others: `test_selection_is_by_content_never_by_name` (`jpeg-only` is now selected), `test_a_folder_inside_own_dir_is_never_selected_even_when_a_scan_row_names_it` (the walk, not a scan row, decides; keep the case with a row inserted), `test_an_unlistable_folder_and_an_unstatable_file_are_counted_and_yield_no_row` (`unlisted` and the named folder), `test_a_subfolder_holding_a_raw_is_selected_alone_and_one_without_is_left_out` (extension names stay). Every other test of the three files passes unedited.

Commit subject: `feat(lrdup): Select every photo or video folder by walking the photo root`

---

### Task T: the summary, the `exclude` wiring and the replan

`Wave:` 2 · `Touches:` lrdup/backup_plan.py, lrdup/cli.py, tests/test_backup_summary.py, tests/test_cli.py · `After:` S · `Mode:` code

**Behaviour:** *Summary (T)*, and `cli._backup_plan` passes `exclude=_exclude(run)` (the configuration's `exclude` and the tool's folder) to `plan_manifests`. Nothing else in `cli.py` changes.

**Interfaces:** produces *Summary (T)*; consumes *Selection (S)* and *Plan fields (S)*, `cli._exclude`.

**Acceptance:**
1. Opening (RC-51): with `unread_folders` named the summary's first section after the title is the NOT READ one, with every name and the sentence that the plan is not one to approve; with none, no such section and the text is as before apart from the new lines. Exit code and stdout line are unchanged: a plan with unread folders still exits 0.
2. Lines (design `backup-plan`): `catalog files left out` per root with its own value (distinct multi-digit values, as the existing test uses), the `## Selection` section with the extension list compared to the same literal list as S.1 sorted (not read from `BACKUP_EXTS`), the link count, the not-selected count and one tally line per extension, `(none)` for no extension; the tally order is count descending then extension: a case with two different counts and a tie between two extensions at one count, so ordering by extension alone or by count ascending fails.
3. `exclude` through `cli.main` (config `exclude` holds a folder inside `photo_root` with a photo): not walked, no row; the walk sees a folder that holds only a JPEG file, selects it, and the manifest has its row.
4. Replan (design Verification): `catalogs`, `inventory`, `backup-plan`, `backup-run --apply` on manifest 1, then `backup-plan` again and `backup-run --apply` on the new manifest: every row's state is `verified` (journal skip) or `already_there`, none `conflict`, `changed` or `failed`, no `.lrdup-part` file, and the archive root's listing (names, sizes, times) is equal before and after the second run.
5. Existing cases edited, no others: the whole-text and per-root expectations of `test_each_root_section_holds_its_own_labelled_figures_and_uses_lf_only` (the new line), the literal `BackupPlan` and `RootPlan` calls of `_plan` (still valid by the defaults; add the new values). `test_injected_stat_attrs_of_and_free_bytes_reach_the_planner` needs no edit (its fixture holds `IMG_0001.ARW`); none expected. Every other test passes unedited.

Commit subject: `feat(lrdup): Show what the backup plan left out and pass exclude to the walk`

## Spec coverage

| Spec item | Task |
|---|---|
| Selection by extension list (R23), by content, nested folders | S |
| Walk by names, own stack, links counted, prune own folder, `exclude`, archive roots | S (walk), T (`exclude` wiring) |
| Preview cache as rule 11 types it; no `catalogs` scan walks every `.lrdata` | S |
| Folder not listed: named, summary opens with it | S (named), T (summary) |
| Catalog files never a row, counted per root (rule 16, R23) | S (rows, count), T (line) |
| Summary: extensions, links, folders not selected with tally | T |
| Replan of a tree already backed up writes nothing | T |
| Known limit: no stall limit on the walk | Not in this plan (stated in the design) |
| Reviewer's end-to-end run | Controller steps |

## Controller steps

1. Before dispatch: give every implementer `context.md` (RC-50 to RC-52) and the answers to the writer's questions; commit this plan.
2. After each landing: run `gate-runner` at that HEAD in a clean detached worktree. Only `unit` applies. T lands before the R22 task Q is dispatched (RC-52).
3. After T: a reviewer runs `catalogs`, `inventory`, `backup-plan`, `backup-run --apply` and `backup-verify` by hand on a synthetic tree (a JPEG-only folder, a video folder, a catalog file beside a photo, a preview cache, an unlistable folder) and reads the summary (L12, L13).
4. Edit the shared files (`CLAUDE.md` phase table if the run teaches something).
5. The real run, after the R22 build, with Lightroom closed and nothing landing (L19): keep the first backup's manifests, verified lists and journals aside (copy `out/backup-manifest-*.csv`, `out/backup-verified-*.csv` and `work/journal/` under a dated folder in `work/`); re-run `catalogs` (the preview-cache test reads its latest scan); run `inventory` only when no library root has a complete files scan (the raws-outside count needs one; RC-56), then `backup-plan`; show the owner the summary, with the extension list and the tally of folders not selected, and get the owner's word per manifest (R10); `backup-run --apply` on one small folder first (a manifest cut to that folder, one per archive root), then the rest; `backup-verify` on both manifests (about 11 hours; nothing lands meanwhile, L17, L19). The reject check is not run between the plan and the end of the verify (rule 27 holds every archive row until then). A summary that opens with a folder not read is not approved until the owner has seen the name.
6. Check with a listing before and after that nothing changed under any library root, and that the only writes are the archive roots, `out/`, the journals under `work/journal/` and the dated aside copy under `work/` (H1, R17).

## Not in this plan

- Catalogs: archiving, copying or reading a catalog or a preview cache (R5); the catalog files stay left out of every manifest.
- Removal, staging, moving, offload or undo, and everything of phase B2; any change to `backup-run`, `backup-verify` or the journal.
- Any write other than the archive roots, `out/`, the journals under `work/journal/` and the aside copy under `work/`; any write to a library root, a catalog or the plug-in.
- A stall limit on the walk (a known limit of the design); a file-type list read from the configuration; planted defects for `backup_plan.py` (RC-50).
