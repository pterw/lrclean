# Outside read: the backup copy engine (prompt)

A ready prompt for a read-only pass by another model over the code that copies photo folders to the archive drives
(phase B1, rules 16 to 23 of the design). Paste everything below the line into the other model's session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone), never in the working
  folder: the working folder holds untracked private files that no outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here, by running it, before anyone acts on it.

---

You are reviewing code by reading it. You change nothing and you run nothing.

## What to read

Repository: this folder, a clean checkout. Commit: `e22f9ca06e68222f6fa94b07272170a348313386` (check with
`git rev-parse HEAD`; if it differs, stop and say so).

- `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase B1: backup copy": rules 16 to 23 are the
  requirements.
- `docs/plans/2026-11-12-phase-b1-backup.md`: the frozen interfaces and the acceptance checks of each task.
- The code: `lrdup/backup.py` (manifest check, the copy of one row, the journal, the dry run, the run loop and its
  stall watchdog, the exit code), `lrdup/backup_plan.py` (manifest format, archive-root checks, the planner),
  `lrdup/backup_verify.py`, and the helpers they call in `lrdup/paths.py`, `lrdup/hashing.py` and `lrdup/store.py`.
- The two commands that drive it, `backup-run` and `backup-verify`, in `lrdup/cli.py`.
- The tests: `tests/test_backup.py`, `tests/test_backup_plan.py`, `tests/test_backup_verify.py`, the backup tests
  of `tests/test_cli.py` and `tests/test_e2e.py`, and the planted defects in `tests/mutants/backup.json`.

## What the tool is for

It copies whole photo folders from a system drive to two external archive drives, on Windows, from a manifest a
person approved. It must never overwrite, change, move or delete anything: not a source, not an existing file on an
archive drive. A copy counts only when its bytes were read back and match. A drive that stops answering must end the
run instead of hanging it. A second run must skip what the first one verified, and only that.

## Your angle

Take the one angle you were given.

- **Angle A, safety of the data.** Find any path on which a source file or an existing destination file could be
  overwritten, truncated, renamed, deleted or modified; on which a copy is reported `verified` or `already_there`
  while its bytes differ from the source; on which a write lands outside an approved archive folder or under a
  library root; or on which a file that is an online-only placeholder is opened.
- **Angle B, failure and rerun.** Find any path on which an error, a stall, a crash between two steps or a second
  run gives a wrong result: a row skipped that was never verified, a `stalled` run reported as something else, a
  journal line lost or merged, a part file left behind and later mistaken for a copy, a thread still writing after
  the run has reported, an exit code that hides a failed row. Include what Windows does differently: long paths,
  case, trailing dots and spaces, reserved names, links and junctions, a rename onto an existing name.

## Already known: do not report these

- Containment is checked on normalised path text, not on the resolved real path. A known, stated limit.
- The time one `fsync` call takes is not covered by the stall watchdog. A known, stated limit.
- A worker stuck inside its own journal write is not detected. A known, stated limit.
- On a stall the process ends at once (`os._exit`) while the caller holds the journal lock. Intended.
- The dry run's `exists` check treats a permission error as "absent". Recorded: the copy itself never replaces a file.
- `os.rename` replaces an existing file on POSIX. The tool runs on Windows only.
- A journal line written after a torn last line may merge with it when the first write of a run fails. Recorded:
  the merged line is ignored and the rerun compares the destination by content.
- The planner reads only a library root whose latest scan is complete.
- Identity is size and modification time. A source rewritten with the same size and time, or a destination
  replaced by a file of the same size and time, is skipped by a rerun and reported `verified`; only
  `backup-verify`, which reads everything in full, sees it. A known, stated limit of the journal skip.
- If writing the `stalled` line to the journal itself fails, the run ends with a traceback, not with exit code 5.
  Recorded.
- A NUL character in a path of a hand-edited manifest ends in a traceback, not exit code 2. Recorded: a manifest
  the tool wrote cannot hold one.
- No test checks that the worker thread is a daemon thread. Recorded. What Ctrl-C does while a worker is blocked on
  a drive is worth a look, and is not on this list.

## Rules of evidence

- Read the callers of a function and what happens after a change before you call it a defect.
- For a pattern or a comparison, quote how it is built, with its flags and its operands.
- Before you call a failure silent, search where the result is reported (the journal, the exit code, the summary).
- Give a concrete input for every finding: the files on disk, the manifest row, the order of events. If you cannot,
  mark the finding `unproven`.
- Check the design section, the plan and the tests before you call something a finding. A behaviour the plan
  freezes, or a test asserts on purpose, is not a defect: at most a question.
- The implementers worked from the plan's task text and the tests, not from this prompt. A gap between the design
  rule and the plan's wording is worth reporting as such.

## What you may and may not do

- Read files. Run read-only git commands (`git show`, `git grep`, `git log`, `git blame`).
- Do not edit, create or delete any tracked file. Do not commit, stash, reset or check out anything.
- Do not run the repository's code, its tests or any script of your own against it. Do not use the network.
- Do not read anything outside this folder.
- Put your notes in one scratch folder you create inside this folder (`outside-read-scratch/`) and delete it when
  you finish. Your two output files are the only things you leave.

## What to hand back

Two files in a folder `outside-read/` inside this folder, named for your angle (`a` or `b`):

1. `findings-<angle>.tsv`, written by a small script from your notes, not typed by hand, one row per finding, with
   these columns: `id`, `file`, `line_from`, `line_to`, `rule` (16 to 23, or `none`), `what_goes_wrong`,
   `concrete_input`, `evidence` (the quoted line or lines), `confidence` (`high`, `medium`, `low` or `unproven`),
   `checked_against` (the plan task, test name or ruling you checked it against). State the row count in the
   report and make sure it equals the file's.
2. `report-<angle>.md`, with these sections:
   - **Coverage:** a table of every file you read with the line ranges you read in full, and the ranges you did
     not read.
   - **Findings:** the same rows as the file, most serious first, each in two or three sentences.
   - **Tried, found nothing:** each thing you looked for and did not find, with where you looked.
   - **Questions:** places where the design, the plan and the code disagree and you cannot tell which is meant.
   - **Scratch:** the line "scratch folder deleted", once it is.

Do not soften or pad. A short report with three real findings is better than a long one.
