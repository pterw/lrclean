# Outside read: the design and plan of phase D1, the unrecorded-copies pass (prompt)

A ready prompt for a read-only pass by another model over the design and the plan of the second pass of this tool
that deletes files: it lists byte-identical copies that no catalog records, and the existing mover stages, undoes
and purges that list. Paste everything below the line into the other model's session.

Before you start it:

- Run it in a clean checkout of the commit named below (a detached worktree or a fresh clone outside your user
  folder if you can), never in the working folder: the working folder holds untracked private files that no
  outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here before anyone acts on it.

---

You are reviewing a plan and a design by reading them. You change nothing and you run nothing.

## What to read

Repository: this folder, a clean checkout. Commit: `2538687046920e9f3641e644357cecb4d41c08d4` (check with
`git rev-parse HEAD`; if it differs, stop and say so).

- `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, section "Phase D1: unrecorded copies (gated)": its
  subcommands, rules 46 to 54, Verification, Known limits and "Not in this phase". The rules it cites say what it
  builds on: rule 5 (catalog coverage), rules 24 to 31 and 26a (the reject check: what a catalog records, rejects
  and keeps, the archive evidence), rules 32 to 45 (phase B2: the mover's `stage`, `undo` and `purge`).
- `CLAUDE.md`: the hard rules H1 to H8 and the rulings R1, R4, R9 to R11, R13, R17, R18, R20 and R24 to R28 (what
  the owner decided, and the readings marked as the controller's).
- `docs/plans/2026-11-23-d1-unrecorded-copies.md`: the plan. Its four tasks (AW, AX, AY, AZ) are what implementers
  will see; each gets only its own task text, the Global Constraints, the Dispatch section and the rulings.
- The code it builds on: `lrdup/rejects.py`, `lrdup/reject_twin.py`, `lrdup/reject_archive.py`,
  `lrdup/reject_out.py`, `lrdup/classify.py`, `lrdup/hashing.py`, `lrdup/cli.py`, `lrdup/mover_list.py`,
  `lrdup/mover_gate.py`, `lrdup/mover_check.py`, `lrdup/mover_stage.py`, `lrdup/mover_purge.py`,
  `lrdup/mover_undo.py`, `lrdup/mover_journal.py`, `tests/mover_world.py`, `tests/mutants/rejects.json`,
  `tests/mutants/mover.json`, and the test files the plan names.

The plan cites rulings `RC-126` to `RC-140`. They are in a private file that is not in this checkout. What they
decide is in the plan's text and in "Already known" below; do not report that you could not read them.

## What the change is for

A photographer's photos sit on the system drive, and two verified copies of each photo folder sit on two archive
drives. The photo library is recorded by one live Lightroom catalog and by several shoot catalogs. Some files are
byte-identical copies that no live or shoot catalog records: a photo imported twice, where the catalog records one
name and the other name was left behind. The owner ruled that the copy the live catalog records stays at its path,
and that an unrecorded copy of it may leave the system drive. The owner also ruled that an unrecorded copy of a
photo the owner rejected leaves as the reject did.

A new read-only command, `copies`, writes a removal list in the format of the existing reject removal list, and a
summary. The owner approves the list by its SHA-256. The existing mover then moves each listed file into a staging
folder on the same drive (`stage`), can move it back (`undo`), and, on a second approval, deletes it (`purge`).
Before staging and again before purging the mover hashes the file, both archive copies and, where the row names
one, the kept twin, in full. Deleting is the one step that cannot be undone, so every path to it must fail closed:
a file must never reach the list unless an identical file stays where the live catalog records it, or it is a copy
of a reject and both archive copies are sound.

## Your angle

Take the one angle you were given.

- **Angle A, losing a photo.** Find any way a file reaches the list, or is deleted, that the rulings mean to
  prevent. Try at least these:
  - a file some live or shoot catalog records, in either path form, as a photo or as a sidecar, rejected or not;
  - a file with a pick or develop work in any catalog read, an older state of the live catalog included;
  - a kept twin that is not the live catalog's (a shoot catalog's, an older state's), or that the live catalog
    rejects, or that is the same file under another path, a link or a placeholder;
  - a kept twin that is itself a row of the list;
  - a copy of a reject where the reject is recorded and kept by another catalog, or was held and not purged;
  - a purge result row of another kind or state read as a purged reject;
  - a copy with a sidecar beside it;
  - a list built from scans or catalogs that have since changed, or while catalog coverage is incomplete;
  - a `copy` row that the mover checks less than a `reject` row at any of the places that test the kind;
  - the reject list or its summary used where the copy list's is meant, and the reverse;
  - a refusal that is checked after something was already read or written.
- **Angle B, the slicing and the tests.** Find:
  - a hidden overlap of `Touches` within wave 1, or a function task AZ uses that no wave-1 task produces;
  - a frozen interface that two tasks read differently (the row type and the spelling of its `path` and `twin`,
    `Built`, the reasons and their order, the summary's fixed lines, `ListKind`, the exit codes);
  - an existing function, constant or line number the plan names that does not exist or does not do what the plan
    says;
  - an acceptance check that would still pass on code that checks only one field, one root, one catalog or one
    list;
  - a planted defect that sits where nothing is decided, or a decision of the builder or the mover with no
    planted defect and no test case;
  - a test or a planted-defect run that could reach a real folder;
  - a task, module, function, field or test case that nothing needs. The owner asked for a lean test suite and for
    less code where less does the job.

## Already known: do not report these

- The pass covers raw and JPEG files only. The file scan stores no HEIC, video or other type, so those are never
  examined (design, Known limits).
- `copies` has no stall limit and no exit 5. `stage` and `purge` keep theirs.
- The copy summary names only the latest `catalogs` scans. A `files` scan older than the files on disk is caught
  by the `stat` and the full hashes of `stage`.
- Identity with a purged reject is checked by the builder only: the list has no column for it, so `stage` checks
  the copy against its two archive copies and not against the purged reject (design, Known limits).
- A copy with a sidecar is held and never moved. The mover gets no refusal of a sidecar cell on the copy list.
- A copy whose only identical file is recorded and kept by a shoot catalog, and not by the live catalog, is not
  listed (`no_keeper`).
- `twin_rejected` holds a copy even where it would match a reject. Its "kept" means the kept-twin test: recorded by
  the live catalog through the absolute form and not rejected. A record through the relative form only, or as a
  sidecar, does not switch the hold off.
- A record in an older state of the live catalog keeps nothing; its pick or develop work does.
- A catalog outside the configured `catalog_roots` keeps nothing; the controller compares the roots with the
  drives before the real run (rule 54).
- The check that every path the live catalog records is present after `stage` is a controller script, not a tool
  feature.
- The refusals of the start-up code that `rejects` and `copies` share are tested through `rejects`; `copies` adds
  one representative case and its own refusals.
- The summary's "not checked" lines follow the reject summary's code (excluded catalogs and the limiting facts of
  coverage).
- A planted defect with two edits is caught when either edit is caught; the test cases hold each decision alone.
- The plan is about 340 lines, and task AW is one task of about ten minutes over six mover files.
- `lrdup/copy_out.py` is a module of its own, and its write-then-replace steps repeat about twelve lines of
  `lrdup/reject_out.py`.
- Emptied folders stay; no catalog is written; the old states of the live catalog will show missing files.

## Rules of evidence

- Read what calls a function and what the plan says happens next before you call something a defect.
- Give a concrete case for every finding: the files on the system drive and the archive drives with their sizes,
  times and hashes, what each catalog records and with which flag, the list's rows, the purge result's rows, and
  what the command would then do. If you cannot, mark it `unproven`.
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
   these columns: `id`, `file`, `line_from`, `line_to`, `rule` (46 to 54, a ruling, or `none`), `what_goes_wrong`,
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
