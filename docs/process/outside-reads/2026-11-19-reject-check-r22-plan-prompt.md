# Outside read: the plan for rule 26a of the reject check (prompt)

A ready prompt for a read-only pass by another model over the design change and the plan that teach the reject
check one exception: a rejected copy whose only develop work is the same settings as its identical twin's may go.
Paste everything below the line into the other model's session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone outside your user
  folder if you can), never in the working folder: the working folder holds untracked private files that no
  outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here before anyone acts on it.

---

You are reviewing a plan and a design by reading them. You change nothing and you run nothing.

## What to read

Repository: this folder, a clean checkout. Commit: `b3e6273f3f8ee39d3a955d2a74e7f443fa4218ab` (check with
`git rev-parse HEAD`; if it differs, stop and say so).

- `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "The reject check": rule 26a is the new
  requirement; rules 26, 28, 29, 30 and 31, the `image` paragraph and the Verification bullets changed with it.
  The diff of the design change is `git diff 77648b6 e01c70e -- docs/superpowers/specs/`. Rules 1, 2 and 3 of
  phase A are what it builds on.
- `CLAUDE.md`, the rulings R18 to R22: what the owner decided, and the readings marked as the controller's.
- `docs/plans/2026-11-19-reject-check-r22.md`: the plan. Its tasks are what implementers will see; each gets only
  its own task text, the Global Constraints, the Dispatch section and the rulings.
- The code the plan changes or builds on: `lrdup/store.py`, `lrdup/catalogs.py`, `lrdup/rejects.py`,
  `lrdup/reject_out.py`, `lrdup/reject_archive.py`, `lrdup/classify.py`, `lrdup/hashing.py`, `lrdup/cli.py`,
  `lrdup/paths.py`, `tests/synth.py`, `tests/mutants/rejects.json`, and the test files the plan names.

## What the change is for

A photographer's main Lightroom catalog and several smaller "shoot" catalogs record the same photo files, and the
library holds byte-identical copies of many photos. The reject check (already built) decides which rejected photos
may later leave the disk; it keeps any photo with develop work in any catalog. The owner ruled (R22) that a
rejected copy may go even though it has develop work, when that work is only the same settings as its identical
twin's in the main catalog, a shoot catalog records the twin, and no catalog other than the main one and its own
copies records the rejected copy or holds develop work or a pick on it. The twin must stay. A wrong row on the
removal list can cost the only copy of a photo, or the only copy of an edit, so the rule must fail closed.

## Your angle

Take the one angle you were given.

- **Angle A, the exception.** Find any way rule 26a, as designed or as planned, lets a copy onto the removal list
  that R18 to R22 keep off: both copies of one photo going in one run or across runs; a twin that is not really
  identical (a stored hash carried by path from an older scan onto a file that changed, a path spelling, a drive
  letter that moved, two hash rows that disagree, a scan that is partial or has no row); settings compared on the
  wrong image (a virtual copy, an older copy of the main catalog, an unreadable develop state, an empty or NULL
  digest); a twin recorded only as a sidecar, only by an older copy of the main catalog, or by an excluded or
  unreadable catalog; a pick; or a sidecar of the copy that carries its own develop work.
- **Angle B, the slicing and the tests.** Find any hidden overlap of `Touches` within a wave, an `After` that
  misses a task whose function it uses, a frozen interface that two tasks read differently (`twin_candidates`,
  `load_hashes`, the `settings` column, the new CSV columns), an acceptance check that would still pass on code
  that checks only one copy or only one field, a planted defect that sits where nothing is decided or misses a
  decision, the migration of an existing database, and a synthetic catalog in `tests/synth.py` that differs from
  what Lightroom stores (the develop settings text) in a way that would hide a defect.

## Already known: do not report these

- The rule is numbered 26a so that rules 27 to 31, which code and tests cite, keep their numbers.
- A copy whose develop work sits in an older copy of the main catalog must have the same settings there too, or
  it stays (the controller's stricter reading of R22).
- All of a copy's images with develop work must share one settings value (the design, condition 5).
- `decide` defaults to an empty hash index, which lifts nothing; the command line passes the real one, and the
  plan's command-line and end-to-end tests fail if it does not.
- A missing, partial or never-run files scan lifts nothing and raises no refusal (exit codes stay 0, 2 and 4).
- Only the configured library roots are searched for a twin.
- `twin` and `twin_settings` are filled only when the exception applied.
- The check reads no file content; phase B2 hashes the copy and its twin again before anything leaves the disk.
- `lrdup/rejects.py` grows to about 360 lines; the plan is about 256 lines. Recorded.

## Rules of evidence

- Read what calls a function and what the plan says happens next before you call something a defect.
- Give a concrete case for every finding: the catalogs, their images, flags and develop settings, the files on
  disk with their sizes, times and stored hashes, the scans, and what the check would put on the list. If you
  cannot, mark it `unproven`.
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
   these columns: `id`, `file`, `line_from`, `line_to`, `rule` (26, 26a with its condition number, 28 to 31, a
   ruling R18 to R22, or `none`), `what_goes_wrong`, `concrete_case`, `evidence` (the quoted line or lines),
   `confidence` (`high`, `medium`, `low` or `unproven`), `checked_against` (the plan task, acceptance check or
   ruling you checked it against). State the row count in the report and make sure it equals the file's.
2. `report-<angle>.md`, with these sections:
   - **Coverage:** a table of every file you read with the line ranges you read in full, and the ranges you did
     not read.
   - **Findings:** the same rows as the file, most serious first, each in two or three sentences.
   - **Tried, found nothing:** each thing you looked for and did not find, with where you looked.
   - **Questions:** places where the design, the rulings and the plan disagree and you cannot tell which is meant.
   - **Scratch:** the line "scratch folder deleted", once it is.

Do not soften or pad. A short report with three real findings is better than a long one.
