# Outside read: the built code of phase D1, as an invariant checklist (prompt for DeepSeek-V4.1 Flash)

The same read as `2026-11-23-d1-unrecorded-copies-code-prompt.md`, written as a fixed checklist for a fast model:
each invariant gets one verdict, the lines that enforce it and the test that guards it. Use it when the open-ended
prompt is refused or when a quick, strict pass is wanted. Paste everything below the line into the other model's
session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone outside your user
  folder), never in the working folder: the working folder holds untracked private files.
- Two runs of this same prompt are worth more than one: their agreement is a signal.
- Bring back the two output files of each run (rename the second run's files with a `-2` suffix).

---

You are checking that a small Python program keeps ten stated properties. You read code and tests. You change
nothing, you run nothing, and you answer every item of the checklist below, in order.

## Scope

Repository: the current folder, a clean checkout. First run `git rev-parse HEAD`. It must print
`970a0820858b863c42e5c1cb32f5ed15497bc4ac`; if it does not, stop and write only that fact to the report.

Read only these files:

- Design: `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase D1: unrecorded copies (gated)"
  (rules 46 to 54, Verification, Known limits).
- Code: `lrdup/copies.py`, `lrdup/copy_out.py`, `lrdup/cli.py` (the `copies` and `stage` parts),
  `lrdup/mover_list.py`, `lrdup/mover_gate.py`, `lrdup/mover_check.py`, `lrdup/mover_stage.py`,
  `lrdup/mover_purge.py`, `lrdup/mover_undo.py`, `lrdup/mover_journal.py`, and, only where these call into
  them, `lrdup/rejects.py`, `lrdup/reject_twin.py`, `lrdup/reject_archive.py`, `lrdup/classify.py`,
  `lrdup/hashing.py`, `lrdup/paths.py`.
- Tests: `tests/test_copies.py`, `tests/test_copy_out.py`, `tests/test_mover_check.py`, `tests/test_mover_stage.py`,
  `tests/test_mover_gate.py`, `tests/test_mover_list.py`, `tests/test_mover_purge.py`, `tests/test_mover_undo.py`,
  the `copies` test classes in `tests/test_cli.py`, the D1 test class in `tests/test_e2e.py`, and
  `tests/mutants/copies.json`, `tests/mutants/mover.json`.

## What the program does

`copies` reads catalog snapshots, file scans with full hashes, archive evidence and earlier purge result files, and
writes `out/copy-removal-list.csv`: files on the system drive that are byte-identical copies no photo catalog
records. A row is either class (a), a copy of a file the main ("live") catalog records, named in the row's `twin`
column, or class (b), a copy of a file listed as `kind=reject, state=purged` in a purge result file (no twin). The
operator approves the list by its SHA-256. `stage` then moves each row's file to a staging folder after hashing it,
its two archive copies and its twin; `undo` moves it back; `purge` deletes staged files after checking them again.

## The checklist

For each invariant, decide one verdict:

- `HOLDS`: you found the line or lines that enforce it, and following every caller you found no input that breaks
  it.
- `VIOLATED`: you can give a concrete input (files with sizes and hashes, catalog records with their flags, purge
  result rows, list rows) and the exact lines through which the program then breaks the invariant.
- `UNPROVEN`: you suspect a break but cannot give such an input, or you did not read enough to decide. Say which.

1. **I1 Recorded files stay off the list.** A file that any live or shoot catalog records, through the absolute or
   the relative path form, as a photo or as a sidecar, rejected or not, never becomes a row.
2. **I2 Work keeps a file.** A file with a pick flag or develop settings in any catalog read, older states of the
   live catalog included, never becomes a row.
3. **I3 The twin of a class (a) row is sound.** The twin is recorded by the live catalog through the absolute form
   and not rejected; it is not itself a row; it is a different file from the row's file; its full hash equals the
   row's, and a stored hash is used only when its size, mtime and file index equal the current scan row's.
4. **I4 Class (b) matches only rejects that may leave.** A class (b) row is either (i) a copy whose size and SHA-256
   equal those of a purge result row whose `kind` is `reject` and whose `state` is `purged` (no other row kind or
   state is ever used), or (ii) a copy of a file that a shoot catalog rejects and the live catalog does not record
   (design rule 46(b)(ii)); in neither case is the reject kept or worked on by another catalog.
5. **I5 A sidecar holds its photo.** A file with a sidecar (`.xmp` or similar) beside it never becomes a row.
6. **I6 Refusals come first.** With fewer than two archive roots, the live catalog excluded, or an unreadable purge
   result, `copies` exits non-zero, changes nothing under `out/`, and does not call `copies.build`. With incomplete
   catalog coverage, `copies.build` raises `Incomplete` before it reads any file (no `stat`, no hash), and `copies`
   exits 4 with nothing under `out/` changed.
7. **I7 Both archives.** Every row names, for each of the two archive roots, a copy that the archive evidence shows
   as verified for that exact file.
8. **I8 A copy row is checked no less than a reject row.** In `mover_check`, `mover_stage`, `mover_purge` and
   `mover_undo`, every check applied to a row of kind `reject` (archive copies, twin, the kind recorded for a held
   row, purge and undo order) is applied to a row of kind `copy` too.
9. **I9 Lists and summaries pair.** `stage` accepts only `out/reject-removal-list.csv` with `out/rejects-summary.md`
   and `out/copy-removal-list.csv` with `out/copies-summary.md`, and refuses every other path or pairing.
10. **I10 No move before a refusal.** No refusal of `stage`, `undo` or `purge` happens after a file of that run was
    already moved or deleted, except the per-row holds that the design names.

## Known and decided: not a violation

- Only raw and JPEG files are examined. `copies` has no stall limit.
- A record is matched by its path's spelling: a record through a junction or another alias of the path does not hold
  a copy. A sidecar written after the file scan is not seen.
- A copy whose only identical file a shoot catalog keeps (not the live catalog) is held as `no_keeper`.
- `stage` checks a class (b) row against its archive copies, not against the purged reject.
- The stage gate does not re-check `excluded_catalogs`; the operator does not edit `work/config.json` between
  `copies` and `stage`.
- Output wording found by a hand run (a stale-scans note on configuration refusals, no SHA-256 printed by `copies`,
  the "Examined" table's counting, no next step on exit 6, "Not checked: none" before `hash`) is already listed for
  a fix.

## What you may and may not do

- Read the files listed above. Run only `git rev-parse HEAD`, `git show`, `git grep` and `git log` in this folder.
- Do not edit, create or delete any tracked file. Do not commit, check out, stash or reset.
- Do not run the program, its tests or any script against it. Do not use the network. Do not read outside this
  folder.
- Put notes in one folder you create here, `outside-read-scratch/`, and delete it at the end.

## What to hand back

In a folder `outside-read/` here:

1. `checklist.tsv`, written by a small script from your notes, with exactly 10 data rows (I1 to I10, in order) and
   these columns: `id`, `verdict` (`HOLDS`, `VIOLATED` or `UNPROVEN`), `enforced_at` (file:line ranges),
   `guarded_by` (the test names that would fail if the enforcing line were broken, or `NONE`), `concrete_case` (for
   `VIOLATED`; else empty), `evidence` (quoted lines), `confidence` (`high`, `medium` or `low`).
2. `report.md` with: **Coverage** (each file read, with the line ranges read in full); **Checklist** (one short
   paragraph per invariant, same verdicts as the TSV); **Gaps** (each invariant whose `guarded_by` is `NONE`, and
   the line a test should cover); **Scratch** (the line "scratch folder deleted", once it is).

Be brief and exact. A `HOLDS` needs the enforcing lines; a `VIOLATED` needs the concrete input.
