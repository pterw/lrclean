# Outside read: the built code of phase D1, the unrecorded-copies pass (prompt)

A ready prompt for a read-only pass by another model over the built code of the second pass of this tool that
deletes files: `copies` writes a removal list of byte-identical copies that no catalog records, and the existing
mover stages, undoes and purges that list. It comes after the build, the reviews, the gates and a hand run on a
synthetic tree, and before any run on the real library. Paste everything below the line into the other model's
session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone outside your user
  folder if you can), never in the working folder: the working folder holds untracked private files that no
  outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here, by running it, before anyone acts on it.

---

You are reviewing built code by reading it. You change nothing and you run nothing.

## What to read

Repository: this folder, a clean checkout. Commit: `970a0820858b863c42e5c1cb32f5ed15497bc4ac` (check with
`git rev-parse HEAD`; if it differs, stop and say so).

- `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase D1: unrecorded copies (gated)": its
  subcommand, rules 46 to 54, Verification, Known limits and "Not in this phase". The rules it builds on: rule 5
  (catalog coverage), rules 24 to 31 and 26a (the reject check), rules 32 to 45 (the mover: `stage`, `undo`,
  `purge`).
- `CLAUDE.md`: the hard rules H1 to H8 and the rulings R1, R4, R9 to R11, R13, R17, R18, R20 and R24 to R28.
- `docs/plans/2026-11-23-d1-unrecorded-copies.md`: the plan the code was built from, with its frozen interfaces
  (some changed during the build: see "Decided during the build").
- The new code: `lrdup/copies.py`, `lrdup/copy_out.py`, and the `copies` and `stage` parts of `lrdup/cli.py`.
- The mover as changed for the second list: `lrdup/mover_list.py`, `lrdup/mover_gate.py`, `lrdup/mover_check.py`,
  `lrdup/mover_stage.py`, `lrdup/mover_purge.py`, `lrdup/mover_undo.py`, `lrdup/mover_journal.py`.
- What they call: `lrdup/rejects.py`, `lrdup/reject_twin.py`, `lrdup/reject_archive.py`, `lrdup/reject_out.py`,
  `lrdup/classify.py`, `lrdup/hashing.py`, `lrdup/paths.py`, `lrdup/store.py`.
- The tests: `tests/test_copies.py`, `tests/test_copy_out.py`, `tests/test_mover_*.py`, `tests/mover_world.py`, the
  `copies` classes of `tests/test_cli.py`, the D1 class and `MoverTreeCase` in `tests/test_e2e.py`, and the
  planted-defect files `tests/mutants/copies.json` and `tests/mutants/mover.json`.

## What the code is for

A photographer's photos sit on the system drive, and two verified copies of each photo folder sit on two archive
drives. One live Lightroom catalog and several shoot catalogs record the library. Some files are byte-identical
copies that no live or shoot catalog records. The owner ruled that the copy the live catalog records stays at its
path and an unrecorded copy of it may leave the system drive, and that an unrecorded copy of a reject that the
reject check let leave may leave too. `copies` writes that list and a summary; the owner approves the list by its
SHA-256; `stage` moves each listed file into a staging folder on the same drive, `undo` moves it back, and, on a
second approval, `purge` deletes it. Before staging and again before purging the mover hashes the file, both archive
copies and, where the row names one, the kept twin, in full. Deleting cannot be undone, so every path to it must
fail closed: a file must never reach the list or the delete unless an identical file stays where the live catalog
records it, or it is a copy of a reject that may leave and both archive copies are sound.

## Your angle

Take the one angle you were given.

- **Angle A, losing a photo.** Find any input or sequence for which a file reaches the copy list, or is staged or
  purged, that the rules mean to keep: a file some live or shoot catalog records (either path form, as a photo or a
  sidecar, rejected or not); a file with a pick or develop work in any catalog read, old states of the live catalog
  included; a kept twin that is not the live catalog's unrejected absolute record, that is itself a row, or that is
  the same file under another path; a copy of a reject where the reject is kept by another catalog or was not
  purged; a purge result row of another kind or state read as a purged reject; a copy with a sidecar beside it; a
  list built from scans, hashes or catalogs that have since changed, or while coverage is incomplete; a `copy` row
  checked less than a `reject` row anywhere the mover tests the kind; the copy list paired with the reject summary or
  the reverse; a refusal checked after something was already read or written.
- **Angle B, the tests and the gates.** Find any test that would still pass on code that checks only one archive
  root, one catalog, one field of a comparison, one list kind, or the first item of a sequence; any decision line of
  `copies.py` or of the mover's kind handling that no test and no planted defect would catch if it were changed; any
  planted defect caught for a wrong reason; and any test or helper that could reach a real folder or the real
  process exit.

## Decided during the plan and the build: do not report these

For `copies` (the builder, the writer, the command):
- It examines raw and JPEG files only: the file scan stores no HEIC, video or other type.
- It has no stall limit and no exit 5 (`stage` and `purge` keep theirs).
- Its summary names only the latest `catalogs` scans; a `files` scan older than the files on disk is caught by the
  `stat` and the full hashes of `stage`.
- Rows of the latest complete `files` scans that share a path are judged once; when their size, mtime or file index
  disagree, the path is held as `changed`. A stored full hash counts only when its size, mtime and file index equal
  the files row's.
- A copy whose only identical file a shoot catalog records and keeps, and the live catalog does not, is held
  `no_keeper`. `twin_rejected` holds a copy even where it would match a reject; its "kept" is the kept-twin test (the
  live catalog's absolute form, not rejected); a record through the relative form only, or as a sidecar, does not
  switch the hold off.
- A record in an older state of the live catalog keeps nothing; its pick or develop work does. A catalog outside the
  configured `catalog_roots` keeps nothing.
- A catalog record is matched by its spelling: a record through a junction or another alias of a path does not hold
  a copy (Known limits). A sidecar written after the inventory is not on the list and stays when its photo leaves.
- `build` trusts its caller for rule 50: the command refuses one archive root, an excluded live catalog and
  incomplete coverage before it calls `build`.
- A copy with a sidecar is held and never moved; the mover gets no refusal of a sidecar cell on the copy list.
- The exit-4 note names `out/copies-summary.md` only when that file exists, as `rejects` does for its own summary;
  the reject run's note now ends "(out/<name>; <when>)".

For `stage`, `undo` and `purge` of the copy list:
- Identity with a purged reject is checked by the builder only: the list has no column for it, so `stage` checks the
  copy against its two archive copies and not against the purged reject.
- The stage gate does not bind `excluded_catalogs`, as for the reject list; `work/config.json` is not edited between
  `copies` and the last `stage` of its list (a step the operator follows).
- The check that every path the live catalog records is present after `stage` is an operator's script, not a tool
  feature. Emptied folders stay; no catalog is written; old states of the live catalog will show missing files.
- A copy row has no sidecars, so the purge order within a copy row's group decides nothing.
- `check_list_path`'s refusal names both list files.

Found by the hand run on a synthetic tree, to be fixed after this read (output only):
- the note "reports older than the latest scans" is added to the configuration refusals of `copies` (one archive
  root, an unreadable purge result) even when nothing is stale;
- `copies` prints the row count but not the SHA-256 to approve (the summary's last line holds it);
- the summary's "Examined" table counts the recorded files themselves under `recorded` and names no held file;
- the exit-6 output of `stage` names the held row and reason but no next step;
- `copies` run before `hash` exits 0 with no rows and says "Not checked: none".

Interfaces changed from the plan's frozen text: `reject_out._write_csv` became `reject_out.write_csv` and
`copy_out` uses it; the end-to-end helpers moved into a base class `MoverTreeCase`.

## Rules of evidence

- Read what calls a function and what happens next before you call something a defect.
- Check how a regular expression is built, with its flags, before you say what it matches.
- Search where a result is reported (the summary, the result files, the journal, the exit code) before you call it
  silent.
- Give a concrete case for every finding: the files on the system drive and the archive drives with their sizes,
  times and hashes, what each catalog records and with which flag, the purge results, the list's rows, the journal's
  lines, and what the command would then do. If you cannot, mark it `unproven`.
- Check the design rule and "Decided during the plan and the build" before you call something a finding. A
  behaviour decided there is not a defect: at most a question.
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
   these columns: `id`, `file`, `line_from`, `line_to`, `rule` (46 to 54, 24 to 45, a ruling, or `none`),
   `what_goes_wrong`, `concrete_case`, `evidence` (the quoted line or lines), `confidence` (`high`, `medium`,
   `low` or `unproven`), `checked_against` (the rule, test or decision you checked it against). State the row
   count in the report and make sure it equals the file's.
2. `report-<angle>.md`, with these sections:
   - **Coverage:** a table of every file you read with the line ranges you read in full, and the ranges you did
     not read.
   - **Findings:** the same rows as the file, most serious first, each in two or three sentences.
   - **Tried, found nothing:** each thing you looked for and did not find, with where you looked.
   - **Questions:** places where the design, the decisions above and the code disagree and you cannot tell which is
     meant.
   - **Scratch:** the line "scratch folder deleted", once it is.

Do not soften or pad. A short report with three real findings is better than a long one.
