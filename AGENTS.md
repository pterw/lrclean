# Instructions for coding agents

## What this is

`lrclean` is a command-line tool for cleaning up a Lightroom Classic library. It reads catalog copies and the
photo drive, reports what each file is to the catalogs and to the backups, copies to archive drives, and moves
files only from a list you approve. Its first phase (Phase A: `catalogs`, `inventory`, `hash`, `report`,
`verify`) only reads photos and catalogs. Run it with `python -m lrclean`. The design, with the rules each phase
must hold, is in `docs/process/specs/2026-11-11-lr-cleanup-design.md`.

## Tests

```
python -m unittest discover -s tests
```

`tests/mutants/*.json` list planted defects. A runner that is not in this repository applies them to check that
the tests catch each one; CI does not run it.

## Rules about the code and its data

- **Phase A is read-only toward photos and catalogs.** It never deletes, moves, renames or modifies a photo, a
  sidecar, a catalog, a lock file or a write-ahead file. Its only writes are inside the tool folder (the folder
  that holds `work/`).
- **Never open an original catalog for writing, in any phase.** Copy the catalog with its `-wal` and `-shm`
  files into `work/catalogs/` and open the copy. No phase ever writes to a catalog's database.
- **Never classify anything by its folder name,** and never suggest deleting a catalog file. A catalog in daily
  use can sit in a folder whose name says "backups".
- **Private data stays local.** File, folder and catalog names can include client names. Nothing derived from
  them goes to an outside service, a tracked file, a commit message, an issue, a pull request or an agent prompt.
- **Files move only from an approved list, with a journal, after content-verified copies.**
- **Online-only placeholders are never opened.**
- **The report is for you to act on.** The tool recommends nothing it cannot show: every row of the report says
  how it was decided. What to cull is your decision, never the tool's.
- **Tests run on synthetic trees only.** Build each tree in a temporary directory, with the helpers in
  `tests/synth.py` and `tests/mover_world.py` and plainly synthetic names such as `shoot-a` and `IMG_0001.ARW`.
- **The design's numbered rules are the contract.** A behaviour that a rule of
  `docs/process/specs/2026-11-11-lr-cleanup-design.md` or a limit in `docs/guide.md` states is intended, even when
  it looks like a defect: check them before reporting one. A change to such a behaviour changes the rule, the code
  and the tests together.

## Design and tests

The code decides what leaves a disk, so a reader must be able to check every decision by reading it.

- **KISS.** Write the plainest code that meets the rule. Use no algorithm worse than linear in a growing input
  unless a comment says why.
- **YAGNI.** Add no option, flag, parameter, mode or abstraction that no command uses now.
- **Separation of concerns.** Each module has one job: reading catalogs, scanning files, deciding, writing output
  or moving files. In new or changed code, a function that decides takes facts and returns a verdict. It does not
  print, open files or read the clock. Effects go through injected seams (for example `mover_seams.Seams`), so tests
  can replace them. Older modules that mix these are split only when a task's brief says so.
- **DRY.** Each rule is decided in one place, and every command that needs it calls that place. Never copy a check
  into a second module. Never merge two things only because they look alike.
- **When these principles conflict,** the plainest reading of the decision wins, and the commit body says which
  principle gave way.
- **Fix by subtraction first.** A fix that deletes or merges code, or adds a case to an existing parametrized
  test, beats one that adds a branch or a new test.

Anti-patterns:

- A broad `except` that hides an error's cause, or turns an unrelated error into a promised exit code. Catch the
  narrowest exception, at the call that raises it; where a handler must be broad, it prints the cause it caught.
- Work done before a refusal: opening the work database, creating a folder or writing a file. Refusals come first.
- A message that tells you to run a command that cannot cure the cause it reports.
- A name the tool derives from a file or catalog name (a part file, a snapshot folder) that is never checked
  against the 255-character limit.
- A helper that only forwards to another, or one field that carries two meanings.

Tests:

- **Intended behaviour.** A test asserts what a design rule or `docs/guide.md` intends, not the code as written. A
  false green is worse than a true red.
- **One test per behaviour.** Cases that differ only in data are one parametrized test (`subTest`). Remove a test
  that only pins wording or a constant, or that repeats another test.
- **Distinct inputs.** Choose inputs so that two values that could be swapped are not equal, and assert that
  precondition in the test.
- **Fixes.** A fix extends an existing test before it adds one.
- **Planted defects.** A change to a line named by an `old` in `tests/mutants/*.json` updates that entry in the same
  change.

Working from a task brief, whatever the assistant:

- Change only the files the brief names.
- When the brief and a rule here or in the design disagree, stop and ask. Do not pick one.
- Search the code before you claim something is missing.
- Run the test files the change touches and those that import a changed module.

## Conventions

- Python 3.13, standard library only at run time, `unittest`, LF line endings.
- **Atomic commits.** Each commit makes one logical change, with its tests and its docs, and passes the whole suite
  on its own. Unrelated changes, even a one-line tidy, go in their own commit.
- **Subject.** Commit subjects are conventional, `type(scope): Subject`. The type is one of `feat`, `fix`, `docs`,
  `test`, `refactor`, `chore` or `ci`, the scope is `lrclean`, and the subject starts with a capital and is
  imperative: `fix(lrclean): Hold a family whose stage steps are all open`.
- **Message.** No line of a message is over 72 characters. The body, after a blank line, says what changes and why.
- **Wording.** A commit message names no role and carries no attribution trailer. It says what the change does.
