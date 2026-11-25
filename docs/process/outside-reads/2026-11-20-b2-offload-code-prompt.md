# Outside read: the built code of phase B2, removal from the system drive (prompt)

A ready prompt for a read-only pass by another model over the built code of the one phase of this tool that
deletes files: `stage`, `undo` and `purge` of the reject check's approved removal list. It is step 4 of the plan's
controller steps: an outside read of the built code before any run on the real library. Paste everything below the
line into the other model's session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone outside your user
  folder if you can), never in the working folder: the working folder holds untracked private files that no
  outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here, by running it, before anyone acts on it.

---

You are reviewing built code by reading it. You change nothing and you run nothing.

## What to read

Repository: this folder, a clean checkout. Commit: `22b93fdba69dbc52eae30375da2a453889b8125d` (check with
`git rev-parse HEAD`; if it differs, stop and say so).

- `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase B2: offload (gated)": its subcommands,
  rules 32 to 45, Verification, Known limits and "Not in this phase"; and the reject check's rules 24 to 31 (rule 29
  is the hold of a family and of a shared sidecar).
- `CLAUDE.md`: the hard rules H1 to H8 and the rulings R1, R4, R10, R11, R14 and R16 to R18.
- `docs/plans/2026-11-19-b2-offload.md`: the plan the code was built from, with its frozen interfaces (some changed
  during the build: see "Decided during the build").
- The code: `lrdup/mover_list.py`, `lrdup/mover_seams.py`, `lrdup/mover_journal.py`, `lrdup/mover_files.py`,
  `lrdup/mover_gate.py`, `lrdup/mover_check.py`, `lrdup/mover_undo.py`, `lrdup/mover_stage.py`,
  `lrdup/mover_purge.py`, the `stage`, `undo` and `purge` parts of `lrdup/cli.py`, and `_hold_family` in
  `lrdup/rejects.py`. What they call: `lrdup/reject_archive.py`, `lrdup/reject_out.py`, `lrdup/backup.py`,
  `lrdup/backup_plan.py`, `lrdup/hashing.py`, `lrdup/paths.py`, `lrdup/store.py`.
- The tests: `tests/mover_world.py` (the synthetic tree whose seams refuse any path outside it),
  `tests/test_mover_*.py`, the mover classes of `tests/test_cli.py`, `MoverEndToEndTests` in `tests/test_e2e.py`,
  `tests/test_rejects_verdicts.py`, and the planted-defect files `tests/mutants/mover.json` and
  `tests/mutants/rejects.json`.

## What the code is for

A photographer's photos sit on the system drive, and two verified copies of each photo folder sit on two archive
drives. The reject check lists the rejected photos that may leave the system drive, and the owner approves that list
by its SHA-256. `stage` then moves each listed file and its sidecars into a staging folder on the same drive, `undo`
moves them back, and, on a second approval of the staged set's SHA-256, `purge` deletes them. Before staging and
again before purging it hashes the file, both archive copies and, for some rows, a kept twin, in full. Deleting is
the one step that cannot be undone, so every path to it must fail closed: a wrong delete loses the only copy on the
system drive, and a delete whose archive copies are not really separate, sound copies loses the photo.

## Your angle

Take the one angle you were given.

- **Angle A, losing a photo.** Find any input or sequence for which the code deletes a file, or leaves one nowhere,
  that the rules mean to keep: an archive copy that is the same file as the source or on its volume, changed after
  it was verified, or missing; a row or file the owner did not approve; a list or purge set that differs from what
  was approved; a crash or a stalled drive at any point of `stage`, `undo` or `purge` and what the next run does (a
  torn journal line, a `begin` with no `end`, a family half purged); a sidecar left behind or deleted without its
  raw, or a sidecar two rows list; a name whose case or Unicode form differs from the list's; `undo` overwriting a
  file that came back; a write, rename or delete outside the folders rule 38 allows; a refusal checked after
  something was already read or written; a hold of the reject check (rule 29) that lets a row onto the list.
- **Angle B, the tests and the gates.** Find any test that would still pass on code that checks only one archive
  copy, one file of a family, one field of a comparison, or the first item of a sequence; any decision line that no
  test and no planted defect would catch if it were changed; any planted defect caught for a wrong reason; and any
  test or helper that could reach a real folder or the real process exit (the seams of `tests/mover_world.py`, the
  `abort` of the stall watch, `cli.main`'s defaults).

## Decided during the build: do not report these

- Removal of the photos from a catalog stays in Lightroom, by the owner, after the files leave the disk.
- `purge` skips the freshness test of rule 34 (it keeps the coverage refusal) and runs the configuration refusals
  of rule 33, with the journal's original paths as its sources; a `.lock` beside a catalog counts as its Lightroom
  refusal.
- The archive copy is compared by size and content, not by time.
- A family partly purged after a crash cannot be restored by `undo`; a second `purge` finishes it.
- The separate same-file test between a file and its archive copies was dropped: the every-pair volume test (each
  copy on its own volume, unlike the file's and the other copy's) refuses every such pair with the same reason.
- A failed same-file, volume or content test reports `archive_mismatch` (`twin_mismatch` for the twin), and the
  held row names the copy, twin or staged file that failed. Every archive state but a sound one reads as
  `no_archive_copy`.
- `stage` takes the shared sidecars of the WHOLE approved list (computed before `--only` selects rows), so a sidecar
  shared with an unselected row holds the selected one.
- In `undo` and `purge`, a row whose staged or original path (read from the journal) lies outside the allowed
  folders is refused alone (`outside_folders`), before anything of it moves. In `stage`, a source outside the photo
  root refuses the whole run before anything moves (rule 33, exit 2). An `OSError` during `--apply` (a rename, a
  removal, a journal line) stops the command with exit 6, naming the journal that holds the state.
- A dry run reports `would_stage`, `would_restore` and `would_purge`; the purge dry run lists every staged file and
  size, and its total counts only the rows it would purge, while the SHA-256 covers the whole staged set. A dry run
  reads no file content (rule 42), so a row shown `would_stage` can still be held at `--apply` by its hashes.
- Interfaces changed from the plan's frozen text: `RowResult.state` gains `would_restore` and `would_purge`, and the
  undo and purge reasons gain `outside_folders`; `run_stage` takes a required `shared_paths`; `RowCheck` gains a
  trailing `kind` (the failing member's); `verify_copies` returns `(reason, failing_path)` on a failure; the result
  files' `state` can read `open_<op>` for a step with a `begin` and no `end`; a journal line of a known event that
  lacks a field its readers use is an error; an `end` with no `begin` is an error.
- Rule 29 (reject check): a reject that lists, as its sidecar, a reject held only through that sidecar's own family
  is not held (the sidecar passed its own tests, so no file leaves without its archive copies); after a rare
  re-parenting, a held reject's `held_by` and verdict can differ from its holder's.
- A crash inside the repair of a torn journal line (the newline written, the marker not) leaves a journal that reads
  as an error (exit 2); the owner repairs it by hand.
- `main` opens the work database before the list path is checked, so a refused `stage` can create it when absent.
- Result files are snapshots of their run; paths in the outputs mix the list's spelling with the name as on disk;
  staged paths are long (they embed the whole original path).
- Tests that keep the default stall limit of 120 s could reach the real process exit after a 120 s pause; the one
  test with a short limit now freezes its clock.

## Rules of evidence

- Read what calls a function and what happens next before you call something a defect.
- Give a concrete case for every finding: the list's rows, the files on the system drive and the archive drives
  with their sizes, times and hashes, the journal's lines, and what the command would then do. If you cannot, mark
  it `unproven`.
- Check the design rule and "Decided during the build" before you call something a finding. A behaviour decided
  there is not a defect: at most a question.
- For angle B, name the exact line a defect would change and the test you expect to fail or pass.

## What you may and may not do

- Read files. Run read-only git commands (`git show`, `git grep`, `git log`, `git blame`, `git diff`).
- Do not edit, create or delete any tracked file. Do not commit, stash, reset or check out anything.
- Do not run the repository's code, its tests or any script of your own against it. Do not use the network.
- Do not read anything outside this folder.
- Put your notes in one scratch folder you create inside this folder (`outside-read-scratch/`) and delete it when
  you finish. Your two output files are the only things you leave.

## What to hand back

Two files in a folder `outside-read/` inside this folder, named for your angle (`a` or `b`):

1. `findings-<angle>.tsv`, written by a small script from your notes, not typed by hand, one row per finding, with
   these columns: `id`, `file`, `line_from`, `line_to`, `rule` (24 to 45, a ruling, or `none`), `what_goes_wrong`,
   `concrete_case`, `evidence` (the quoted line or lines), `confidence` (`high`, `medium`, `low` or `unproven`),
   `checked_against` (the rule, test or decision you checked it against). State the row count in the report and
   make sure it equals the file's.
2. `report-<angle>.md`, with these sections:
   - **Coverage:** a table of every file you read with the line ranges you read in full, and the ranges you did
     not read.
   - **Findings:** the same rows as the file, most serious first, each in two or three sentences.
   - **Tried, found nothing:** each thing you looked for and did not find, with where you looked.
   - **Questions:** places where the design, the decisions above and the code disagree and you cannot tell which is
     meant.
   - **Scratch:** the line "scratch folder deleted", once it is.

Do not soften or pad. A short report with three real findings is better than a long one.
