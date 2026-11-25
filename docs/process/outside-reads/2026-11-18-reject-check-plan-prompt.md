# Outside read: the plan for the reject check (prompt)

A ready prompt for a read-only pass by another model over the plan that will build the reject check: the part of
the tool that decides which rejected photos may later leave the disk. Paste everything below the line into the
other model's session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone outside your user
  folder if you can), never in the working folder: the working folder holds untracked private files that no
  outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here before anyone acts on it.

---

You are reviewing a plan and a design by reading them. You change nothing and you run nothing.

## What to read

Repository: this folder, a clean checkout. Commit: `15e3f10e425a0fc4119c1af2eb72c5157d47e885` (check with `git rev-parse HEAD`; if it differs, stop
and say so).

- `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "The reject check": rules 24 to 31 are the
  requirements. Rules 2, 3, 5, 6 and 15 of phase A are what it builds on.
- `CLAUDE.md`, the rulings R13 to R18: what the owner decided, and the readings marked as the controller's.
- `docs/plans/2026-11-18-reject-check.md`: the plan. Its tasks are what implementers will see; each gets only its
  own task text, the Global Constraints, the Dispatch section and the rulings.
- The code the plan builds on: `lrdup/catalogs.py`, `lrdup/store.py`, `lrdup/classify.py`, `lrdup/cli.py`,
  `lrdup/backup_plan.py`, `lrdup/backup_verify.py`, `lrdup/inventory.py`, `lrdup/paths.py`, `tests/synth.py`.

## What the check is for

A photographer has one main Lightroom catalog and many smaller catalogs, often recording the same photo files.
Photos flagged as rejected may later be deleted from the disk, but only if no catalog still wants them: none
records the file without rejecting it, none holds develop settings or a pick flag for it, and the file has a
verified copy on each of two archive drives. The check writes a report and a removal list; it deletes nothing and
never writes to a catalog. A wrong row on the removal list can cost a photo that a catalog still points to, so the
removal list must fail closed.

## Your angle

Take the one angle you were given.

- **Angle A, the removal list.** Find any way the design or the plan lets a file onto the removal list that the
  rulings keep off, or keeps off one they allow: through path forms (absolute and catalog-relative), case and
  Unicode, a virtual copy, a sidecar (XMP, a camera JPEG), a catalog that is partial, excluded, stale, or an older
  copy of the main one, a develop state that is missing or unreadable, a pick, an archive copy that is missing,
  older than its manifest or on the wrong drive, a file that changed since the backup, or the order in which the
  verdicts are tested.
- **Angle B, the slicing and the tests.** Find any hidden overlap of `Touches` within a wave, an `After` that
  misses a task whose function it uses, a frozen interface that two tasks read differently, an acceptance check
  that would still pass on code that breaks the rule it guards, a planted defect that sits where nothing is
  decided or misses a decision, and a synthetic catalog in `tests/synth.py` that differs from what Lightroom
  stores in a way that would hide a defect.

## Already known: do not report these

- The check matches files by normalised path, never by name or content. Intended (rule 3).
- `out/rejects.csv` and the removal list carry no column for catalogs that were not checked; only the summary
  names them. Intended (rule 26, ruling RC-15 of the plan workspace).
- A catalog that lacks the develop table makes every photo it holds `kept_for_work`. Intended (R18 fails closed).
- Archive roots are numbered from 1, as in phase B1.
- `how_decided` counts image rows, one per image and path form, so two forms that normalise equal count twice.
  Recorded.
- The plan is about 284 lines, over the 250 its brief asked for. Recorded.

## Rules of evidence

- Read what calls a function and what the plan says happens next before you call something a defect.
- Give a concrete case for every finding: the catalogs, their records and flags, the files on disk, the manifest
  and verified rows, and what the check would put on the list. If you cannot, mark it `unproven`.
- Check the design rule, the ruling and the plan's acceptance checks before you call something a finding. A
  behaviour a ruling decides is not a defect: at most a question.
- Implementers work from the plan's task text, not from this prompt. A gap between a design rule and a task's
  wording is worth reporting as such.

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
   these columns: `id`, `file`, `line_from`, `line_to`, `rule` (24 to 31, a ruling R13 to R18, or `none`),
   `what_goes_wrong`, `concrete_case`, `evidence` (the quoted line or lines), `confidence` (`high`, `medium`,
   `low` or `unproven`), `checked_against` (the plan task, acceptance check or ruling you checked it against).
   State the row count in the report and make sure it equals the file's.
2. `report-<angle>.md`, with these sections:
   - **Coverage:** a table of every file you read with the line ranges you read in full, and the ranges you did
     not read.
   - **Findings:** the same rows as the file, most serious first, each in two or three sentences.
   - **Tried, found nothing:** each thing you looked for and did not find, with where you looked.
   - **Questions:** places where the design, the rulings and the plan disagree and you cannot tell which is meant.
   - **Scratch:** the line "scratch folder deleted", once it is.

Do not soften or pad. A short report with three real findings is better than a long one.
