# Outside read: the design and plan of phase B2, removal from the system drive (prompt)

A ready prompt for a read-only pass by another model over the design and the plan of the one phase of this tool
that deletes files: it stages, undoes and purges the rows of the reject check's approved removal list. Paste
everything below the line into the other model's session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone outside your user
  folder if you can), never in the working folder: the working folder holds untracked private files that no
  outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here before anyone acts on it.

---

You are reviewing a plan and a design by reading them. You change nothing and you run nothing.

## What to read

Repository: this folder, a clean checkout. Commit: `8bdaf40a6c32607c55ad4154215c181f743ed7d5` (check with `git rev-parse HEAD`; if it
differs, stop and say so).

- `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase B2: offload (gated)": its
  subcommands, rules 32 to 45, Verification, Known limits and "Not in this phase". The reject check's rules 24 to
  31 say what the removal list holds; phase B1 (rules 16 to 23) says how the two archive copies were made and
  verified.
- `CLAUDE.md`: the hard rules H1 to H8 and the rulings R1, R4, R10, R11, R14 and R16 to R18 (what the owner decided,
  and the readings marked as the controller's).
- `docs/plans/2026-11-19-b2-offload.md`: the plan. Its tasks are what implementers will see; each gets only its own
  task text, the Global Constraints, the Dispatch section and the rulings.
- The code it builds on: `lrdup/reject_out.py`, `lrdup/rejects.py`, `lrdup/reject_archive.py`, `lrdup/backup.py`,
  `lrdup/hashing.py`, `lrdup/paths.py`, `lrdup/catalogs.py`, `lrdup/cli.py`, `lrdup/store.py`, `tests/synth.py`,
  `tests/mutants/rejects.json`, and the test files the plan names.

## What the change is for

A photographer's photos sit on the system drive, and two verified copies of each photo folder sit on two archive
drives. The reject check (built) lists the rejected photos that may leave the system drive, and the owner approves
that list by its SHA-256. Phase B2 then moves each listed file and its sidecars into a staging folder on the same
drive (`stage`), can move them back (`undo`), and, on a second approval, deletes them (`purge`). Before staging and
again before purging it hashes the file, both archive copies and, for some rows, a kept twin, in full. Deleting is
the one step that cannot be undone, so every path to it must fail closed: a wrong delete loses the only copy on
the system drive, and a delete whose archive copies are not really separate, sound copies loses the photo.

## Your angle

Take the one angle you were given.

- **Angle A, losing a photo.** Find any way a file is deleted, or left nowhere, that the rules mean to prevent: an
  archive copy that is the same file as the source (a link, a junction, two roots on one volume), changed after it
  was verified, or missing; a row the owner did not approve, or a list edited after approval; a crash or a
  stalled drive at any point of `stage`, `undo` or `purge` and what the next run then does (a torn journal line, a
  `begin` with no `end`, a family half purged); a sidecar left behind or deleted without its raw; a name whose case
  or Unicode form differs from the list's; `undo` overwriting a file that came back; a write outside the folders
  rule 38 allows; a refusal that is checked after something was already read or written.
- **Angle B, the slicing and the tests.** Find any hidden overlap of `Touches` within a wave, an `After` that
  misses a task whose function it uses, a frozen interface that two tasks read differently (the parsed row, the
  check result and its reasons, the journal line, the staged path, the seams, the exit codes), an acceptance check
  that would still pass on code that checks only one archive copy, one file of a family or one field, a planted
  defect that sits where nothing is decided or misses one of the six decisions it must cover, and any test or
  planted-defect run that could reach a real folder (the seams and the path guard).

## Already known: do not report these

- Removal of the photos from a catalog stays in Lightroom, by the owner, after the files leave the disk.
- `purge` skips the freshness test of rule 34 (it keeps the coverage refusal): the owner's removals in Lightroom
  between `stage` and `purge` would otherwise make every list stale.
- The archive copy is compared by size and content, not by time (an archive volume may round times).
- A family partly purged after a crash cannot be restored by `undo`; a second `purge` finishes it, and a row that
  second `purge` holds stays staged for the owner.
- The mover is split into nine `lrdup/mover_*.py` files so that each task stays near five minutes of work.
- `mover.json` carries six planted defects with one file-wide `pattern` (the tool takes one per file).
- A failed same-file or other-volume test reports `archive_mismatch` (`twin_mismatch` for the twin).
- The tests fake another device number for the archive roots, because a temporary tree lies on one volume.
- Rule 34 reads the scan ids from the reject check's summary text; the end-to-end task joins the real output.
- The plan is about 560 lines once wrapped. Recorded.

## Rules of evidence

- Read what calls a function and what the plan says happens next before you call something a defect.
- Give a concrete case for every finding: the list's rows, the files on the system drive and the archive drives
  with their sizes, times and hashes, the journal's lines, and what the command would then do. If you cannot, mark
  it `unproven`.
- Check the design rule, the ruling and the plan's acceptance checks before you call something a finding. A
  behaviour a ruling decides is not a defect: at most a question.
- Implementers work from the plan's task text, not from this prompt. A gap between a design rule and a task's
  wording is worth reporting as such.

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
   these columns: `id`, `file`, `line_from`, `line_to`, `rule` (32 to 45, a ruling, or `none`), `what_goes_wrong`,
   `concrete_case`, `evidence` (the quoted line or lines), `confidence` (`high`, `medium`, `low` or `unproven`),
   `checked_against` (the plan task, acceptance check or ruling you checked it against). State the row count in
   the report and make sure it equals the file's.
2. `report-<angle>.md`, with these sections:
   - **Coverage:** a table of every file you read with the line ranges you read in full, and the ranges you did
     not read.
   - **Findings:** the same rows as the file, most serious first, each in two or three sentences.
   - **Tried, found nothing:** each thing you looked for and did not find, with where you looked.
   - **Questions:** places where the design, the rulings and the plan disagree and you cannot tell which is meant.
   - **Scratch:** the line "scratch folder deleted", once it is.

Do not soften or pad. A short report with three real findings is better than a long one.
