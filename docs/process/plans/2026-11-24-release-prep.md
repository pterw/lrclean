# Release prep (`lrclean` 0.1.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build everything section 4 of the public-release design lists in the private repository, on the branch `release-prep`: the rename `lrdup` to `lrclean`, packaging, the README split and manual, `AGENTS.md`, three test fixes and three role-naming strings, the logo (already committed), the move of the process documents, and CI with the release workflow. No change to what the tool does to files or catalogs.

**Architecture:** One rename task, alone and first, because it touches 75 files that every other task builds on. Then one wave of four tasks with disjoint files (packaging, README and manual, `AGENTS.md`, test fixes). Then the move of the process documents, and last the three workflow files (the logo is already committed). The export, the public repository and the tag are controller steps, not tasks.

**Tech Stack:** Python 3.13 standard library at run time; `unittest`; setuptools as the build backend (build time only); GitHub Actions; Markdown and SVG.

**Spec:** `docs/superpowers/specs/2026-11-24-public-release-design.md` (sections 4 to 7 are built; section 11 is checked; section 10 is not touched) and `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md` (read only). Rulings: workspace `context.md`, RC-163 (scope, order, rulings of this plan) and RC-61 (safety in tests). Hit list of the rename: the workspace's `sweep-lrdup-47ecd78.txt` (245 lines in 76 files; line 311 of `CLAUDE.md` stays as written, so the rename edits 244 lines in 75 files).

Sections: Global Constraints=brief, Review Focus=reference, Dispatch=reference, Tasks=brief, Spec coverage=reference, Controller steps=reference, Not in this plan=reference

## Global Constraints

- Branch `release-prep`, cut from `main` at 47ecd789 (the merge of pull request 9); this plan is its first commit; the logo (`39b43e7`) is the second. One pull request; it is the first to have a CI leg (task CH).
- Python 3.13, standard library only at run time, `unittest`, LF files; tests run as `python -m unittest discover -s tests`. The tool keeps no dependency. A build-time need (setuptools in CB, the `build` package in CB and CH) is not a dependency of the tool and never appears in `dependencies`.
- PRIVATE DATA (RC-61): every test builds its own tree in `tempfile.TemporaryDirectory()`; fixtures are plainly synthetic (`shoot-a`, `IMG_0001.ARW`). No real path, drive label, client or folder name of any library, no e-mail, and no path of a developer's machine in any file this plan adds or edits, the plan itself included. Documents and workflows use paths relative to the repository.
- Voice: a new sentence never says "the owner". It says what is done, or names Peter where the design does. The account name `pterw` is fixed by the design (section 7).
- Lean (RC-163 (8)): no new module where a line in an existing one does; one test per intended behaviour; the rename adds no test; CE edits three existing tests and two message strings in the package and adds no test. Each code task below says what it reuses and what it adds nothing of.
- Test inputs: values that could be swapped are not equal, and the test asserts that precondition (L10: a decision that compares several fields gets a case per field).
- Implementers never commit (constraints.md, Commit procedure): the controller lands each patch, or commits each serial task after review, with the subject the task gives (conventional, no trailer).
- Shared files (constraints.md): the exceptions this plan needs are named per task in Dispatch; a file that is not named there stays the controller's.

## Review Focus

- A stale stored name on the real work folder. After CA, `lrclean` looks for `work/lrclean.sqlite` and for `.lrclean-part`. With the old database left in place the first run opens a new empty one (no scan is found), and old `*.lrdup-part` files in the archive folders are no longer recognised as part files. Controller step 2 does the hand work before the first `lrclean` run; CA proves the new names in code and tests.
- A skipped gate. A `when:` that still names `lrdup/` matches nothing after CA and skips its gate without a message. The controller rewrites the Gates block at the landing and runs all six gates with `when:` ignored.
- The hosted runner is not the local machine (design risk 1). The checkout and the temporary folder may be on two drives, paths over 260 characters need the registry value, and link rights and file attributes may differ. CE fixes the three known tests; whatever else the first run shows is handled as CH says.
- The README on the PyPI page. Its header uses relative paths to `docs/assets/`, which the PyPI page is expected not to resolve, so the page may show no logo. Accepted for 0.1.0; looked at after the first upload (controller step 8).
- `--base` defaults to the current folder. An installed `lrclean` run from any folder makes `work/` and `out/` there. The quick start says to make a tool folder outside the photo folders and to run from it or pass `--base`.
- A published file that keeps a private name, a path that moved, or a statement the code does not hold. The branch-end checks of controller step 10 are commands, not reading.

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | CA | immediately | 1 code phase + 1 nested gate-runner = 2 |
| 2 | CB, CC, CD, CE | CA landed, the Gates block rewritten, and the whole gate set (`when:` ignored) green | 4 code phases + 4 nested gate-runners = 8 |
| 3 | CG | CB, CC, CD, CE landed | 1 serial writer |
| 4 | CH | CG landed | 1 code phase + 1 nested gate-runner = 2 |

Peak 8 (cap 12). One `sdd-reviewer` per task trails its code phase; a reviewer starts only after its task's code phase and gate-runner have ended, so it replaces them in the count. CG is the one serial task here: it is written by the only writer in the main checkout and left uncommitted for the controller to review and commit, and no patch is landed while CG runs (a moved HEAD stops a serial writer).

**Landing order.** CA alone (`git apply --check --index <patch>`, `git apply --index <patch>`, one commit with the task's subject, no trailer; then controller step 1: Gates block rewritten, all six gates with `when:` ignored, green). Then CB, CC, CD, CE as a stack in that order (the same two apply commands and one commit each), then ONE gate run at the top of the stack in a clean detached worktree (`unit`, `mutants-backup`, `mutants-mover`). A failing gate at the top is attributed to a task by re-running the unit suite at each commit of the stack. No later task touches a file named in the `when:` of `mutants-classify`, `mutants-rejects` or `mutants-copies`, so those three are not re-run after CA. Then CG (after its review, the controller commits it), then CH (patch, same two apply commands, one commit), then the pull request.

**Disjoint files in wave 2.** CB: `pyproject.toml`, `LICENSE`, `CHANGELOG.md`, `.gitignore`. CC: `README.md`, `docs/guide.md`. CD: `AGENTS.md`. CE: `tests/test_cli.py`, `tests/test_catalogs.py`, `tests/test_backup.py`, `lrclean/backup_plan.py`, `lrclean/mover_gate.py` (the last two are also edited by CA, which is wave 1). No file appears twice in a wave. `README.md` is edited by CA (five lines) and rewritten by CC, so CC is After CA; the three test files and the two modules are edited by CA (names) and by CE, so CE is After CA; `.gitattributes` already holds `* text=auto eol=lf` and no task touches it. CG only moves and link lines (After CB to CE), CH only `.github/`.

**Shared-files exceptions** (constraints.md lists `CLAUDE.md`, `.gitignore`, `.gitattributes`, `docs/**`, `README.md`, `lrdup/__init__.py`, `lrdup/store.py`; the dispatch of each task names these and no others):

| Task | May edit although shared |
|---|---|
| CA | `README.md` (its lines in the hit list), `lrclean/store.py` (the one import line), `lrclean/__init__.py` (moved, not edited) |
| CB | `.gitignore` |
| CC | `README.md`, `docs/guide.md` |
| CG | `docs/**` (moves, the new note, link lines), `README.md`, `docs/guide.md`, `CLAUDE.md` (link lines only) |
| CD, CE, CH | none (they touch no shared file) |

**Frozen interfaces.** An implementer that needs one changed returns NEEDS_CONTEXT, and the controller pauses the dependent tasks.

- Package `lrclean`; `python -m lrclean`; `lrclean/__main__.py` unchanged apart from the name; `lrclean.cli.main(argv=None, *, <the existing seams>) -> int` unchanged (it is the entry point of CB); `argparse` `prog="lrclean"`; the work database `work/lrclean.sqlite`; `backup.PART_SUFFIX = ".lrclean-part"`.
- Version: `version = "0.1.0"` in `pyproject.toml` is the only place the version is written. `CHANGELOG.md` headings: `## X.Y.Z - YYYY-MM-DD`.
- Files other tasks name: `docs/guide.md`; `docs/assets/lrclean-logo-light.svg` and `docs/assets/lrclean-logo-dark.svg` (committed in `39b43e7`, named by CC); `AGENTS.md`; `LICENSE`; `CHANGELOG.md`. Moved by CG: `docs/process/specs/`, `docs/process/plans/`, `docs/process/outside-reads/`, and the note `docs/process/README.md` (these names are this plan's choice; the design fixes only `docs/process/`).
- Workflows (names the controller's CI block reads): `.github/workflows/ci.yml` (workflow `CI`, job `unit`), `.github/workflows/release.yml` (workflow `Release`, jobs `checks`, `build`, `upload`, `release`), `.github/dependabot.yml`. Four strings fixed by the design: GitHub account `pterw`, repository `lrclean`, workflow `release.yml`, environment `pypi-release`; PyPI project name `lrclean`.

**Change notes.** CA changes every path and import name: the dispatch of each later task says the tree is renamed. CB tells CH where the version is and what the heading looks like. CC and CD tell CG which link lines they wrote (old paths, rewritten by CG). CE tells CH which three tests changed, and tells CC that two messages changed wording (the guide quotes neither).

**Gates each task needs** (names from constraints.md; none is added here; the controller adds a CI block after CH):

| Task | Gates |
|---|---|
| CA | `unit`, `mutants-classify`, `mutants-backup`, `mutants-rejects`, `mutants-mover`, `mutants-copies`, all six with `when:` ignored. Defects caught: classify 6 of 6, backup 9 of 9, rejects 21 of 21, mover 9 of 9, copies 7 of 7 |
| CB | `unit` |
| CC, CD | `unit` (these are documents in `code` mode so that they run in parallel with CB and CE; no code changes, and the nested gate-run that the code phase requires is this one gate) plus the task's acceptance commands, which the controller reruns |
| CG | none (a serial document task; the controller reads it and runs the task's acceptance commands) |
| CE | `unit`, `mutants-backup` (`when:` names `tests/test_backup.py`), `mutants-mover` (`when:` names `lrclean/mover_gate.py`) |
| CH | `unit` |

## Tasks

### Task CA: rename `lrdup` to `lrclean`

`Wave:` 1 · `Touches:` the folder `lrdup/` moved to `lrclean/` (all 32 tracked files); text edits in 75 files of the hit list: 30 files of `lrclean/*.py`, 39 files of `tests/` (`tests/*.py`, `tests/synth.py`, `tests/mover_world.py`), the 5 files `tests/mutants/*.json`, and `README.md` (5 lines) · `After:` none · `Mode:` code (the dispatch names the shared-file exceptions above)

**Behaviour.** What is renamed: the package folder and the name in `python -m`, every `import`, `from ... import` and `mock.patch` string, the `prog` name of the parser, the `file` path of every planted defect, the work database `lrdup.sqlite` to `lrclean.sqlite` (built in `cli.main`), the part-file suffix `.lrdup-part` to `.lrclean-part` (`backup.PART_SUFFIX`, and its upper-case spelling `.LRDUP-PART` at `tests/test_backup.py:89`), and the five `README.md` lines of the hit list (`python -m lrclean`, the title, the folder table, the work database). What is not renamed: everything under `docs/superpowers/specs`, `docs/plans` and `docs/outside-reads` (260 lines in 19 files stay as written: they are the record), line 311 of `CLAUDE.md` (a ruling's text), and git history. Order: first `git mv lrdup lrclean` (one rename, so history follows), then the text, working through the hit list in this order: `lrclean/`, `tests/`, `tests/mutants/`, `README.md`. The hit list (the workspace file `sweep-lrdup-47ecd78.txt`, a dispatch input whose absolute path the dispatch gives) is the work list; each file is read and edited with the Edit tool, `replace_all` on the exact string `lrdup` (one file needs `LRDUP` as well). No script rewrites a tracked file (constraints.md). This is more than a five-minute task and stays one task: a half-renamed tree does not import. No test is added: the rename is proved by the suite and the planted-defect gates.

**Reuses, adds nothing of.** The hit list and the existing suite. No alias module `lrdup`, no compatibility shim, no new constant, no migration code for the old stored names (controller step 2 does that by hand on the one real folder).

**Gates block (controller, at the landing).** The controller rewrites every `lrdup/` in the `when:` lines of constraints.md's Gates block (no `run:` line names it today) and the other names in constraints.md that cite `lrdup`, in the same step, then runs the whole gate set with `when:` ignored. Reason: a `when:` that names `lrdup/` matches no changed file after the rename and its gate would be skipped without a message. The nested gate-run of this task is told to ignore `when:` as well.

**Interfaces:** consumes none; produces the names in Frozen interfaces.

**Acceptance (the first two are commands; run from the repository root with the work staged):**
1. `git grep -n -i lrdup -- . ':!docs/superpowers/specs' ':!docs/plans' ':!docs/outside-reads'` prints exactly one line, `CLAUDE.md:311:...`; `git ls-files | grep -i lrdup` prints nothing (a path check, which `git grep` does not make). After CG moves the three folders the pathspec exclusions become `':!docs/process'`.
2. `git diff --cached --quiet <BASE> -- CLAUDE.md docs/superpowers docs/plans docs/outside-reads` exits 0 (the record and line 311 are untouched).
3. `python -m lrclean --help` exits 0 and its usage line says `lrclean`; `python -m lrdup`, run in the task's worktree, exits non-zero and its error text says `No module named lrdup` (a bare non-zero is also what a present module gives with no argument).
4. Planted-defect files: `grep -o lrdup tests/mutants/*.json` at BASE finds 54 occurrences, one per `file` entry (9, 6, 8, 9 and 22 in `backup`, `classify`, `copies`, `mover`, `rejects`), so no `old` or `new` text holds the name; afterwards every `file` is `lrclean/<module>.py` and exists, and every `old` still occurs once and as a single line (a violation ends a `mutate.py` run with exit 2). The five planted-defect gates catch every defect (the counts are in the Gates table of Dispatch).
5. The stored names: `git grep -n -E 'lrclean\.sqlite|\.lrclean-part|LRCLEAN-PART' -- lrclean tests` finds 22 lines (the lines of `lrdup/` and `tests/` that hold `lrdup.sqlite` or `.lrdup-part`, in any case, at BASE: `lrclean/cli.py` 1, `lrclean/backup.py` 1, and 20 in `tests/`; the README's own line is not in this pathspec), and the unit suite passes. `.lrclean-part` is two characters longer than before: a test that sits at a path-length limit would show it in the suite; the implementer names any such line and does not change an assertion to pass.

Commit subject: `refactor(lrdup): Rename the package, the command and the stored names to lrclean`

---

### Task CB: packaging (`pyproject.toml`, `LICENSE`, `CHANGELOG.md`, `.gitignore`)

`Wave:` 2 · `Touches:` pyproject.toml, LICENSE, CHANGELOG.md, .gitignore · `After:` CA · `Mode:` code (the dispatch names the `.gitignore` exception)

**Behaviour.** `pyproject.toml` (design section 5): `name = "lrclean"`, `version = "0.1.0"`, `requires-python = ">=3.13"`, `dependencies = []`, `readme = "README.md"`, the licence `MIT` as an SPDX string with `LICENSE` as its licence file, the one-line description "Free a full photo drive without losing a photo or breaking a Lightroom Classic catalog." (the design's tagline, section 1), the keywords `lightroom-classic`, `photography`, `photo-management`, `deduplication`, `duplicate-files`, `backup`, `raw-photos`, `digital-asset-management`, `python`, `cli`, `windows` (the design's topics, section 4), classifiers for Windows, Python 3.13 and console use, URLs for the repository `https://github.com/pterw/lrclean` (home, issues, changelog), and the command `[project.scripts] lrclean = "lrclean.cli:main"`. The callable exists: `lrclean.cli.main(argv=None, *, <seams>) -> int`, and `__main__.py` already runs `sys.exit(cli.main())`, so no wrapper is written and the generated command behaves as `python -m lrclean`. Build backend: setuptools (`requires = ["setuptools>=77"]`, `build-backend = "setuptools.build_meta"`; 77 is the first release that reads an SPDX `license` string, the implementer confirms it in setuptools' own changelog; there is no `License ::` classifier next to the SPDX string, because setuptools refuses both); it is needed to build the package and is not installed with it. The package list is explicit (`lrclean` only), so the wheel holds `lrclean/` only; the sdist also holds setuptools' default `tests/test*.py`, which acceptance 3 states (no `MANIFEST.in`). The copyright line of `LICENSE` and the `authors` value are given by the dispatch's context (the design gives none); the implementer writes no personal name or e-mail of its own, and returns NEEDS_CONTEXT if they are missing.

`LICENSE` is the standard MIT text. `CHANGELOG.md` opens with `# Changelog`, then one entry `## 0.1.0 - <date>` (the date is given by the dispatch) with a short list: the five phases, Windows only, the catalog formats read, no dependency. `.gitignore`: add a line `CLAUDE.md` (design section 4: a local `CLAUDE.md` may import `AGENTS.md`); reword the two comments that name a role (they become "Private or generated: never tracked" and "Locally installed agent skills"); add `build/`, `dist/` and `*.egg-info/` (build products). `.gitattributes` is untouched.

**Reuses, adds nothing of.** `cli.main` as the entry point. No `setup.py`, `setup.cfg`, `MANIFEST.in`, `__version__` constant, `--version` option or optional dependency group.

**Interfaces:** consumes `lrclean.cli:main`; produces the version field and the heading format (read by CH).

**Acceptance (with the task's files staged: `git add` by name, so that `git grep` and `git diff --cached` see the new files):**
1. `tomllib` loads `pyproject.toml` in a scratch script and the fields above hold; `dependencies` is empty; `description` equals the tagline above character for character.
2. In a scratch folder outside the checkout: the staged tree is copied there (`git checkout-index -a --prefix=<scratch>/src/`), the sdist and the wheel are built from the copy (`python -m build`; an implementer without network passes `--no-isolation`, with setuptools 77 or later installed, and reports it), a fresh virtual environment is made in the scratch folder, the wheel is installed into it, and `lrclean --help` exits 0 there with usage naming `lrclean`. `pip list` in that environment shows `lrclean` and `pip` (and nothing the tool needs).
3. The wheel's file list (read with `zipfile` in a scratch script) holds only `lrclean/` (the same 32 files as `git ls-files lrclean`) and the `lrclean-0.1.0.dist-info/` metadata; the sdist's list holds `lrclean/`, `pyproject.toml`, `README.md`, `LICENSE`, setuptools' default `tests/test*.py` (accepted) and its metadata, and none of `work/`, `out/`, `.superpowers/`, `CLAUDE.md` (which is tracked here and is not published).
4. `git check-ignore --no-index -v CLAUDE.md dist/ build/ lrclean.egg-info/` names a line for each (the trailing slash is needed for folders that do not exist); `out/`, `work/` and `.superpowers/` are still ignored; `git grep -n -i owner -- .gitignore` prints nothing.
5. The first `## ` line of `CHANGELOG.md` matches `^## 0\.1\.0 - [0-9]{4}-[0-9]{2}-[0-9]{2}$`; `LICENSE` holds "Permission is hereby granted"; `git diff --cached --stat` does not list `.gitattributes`.
6. The version is in one place: `git grep -n -F 0.1.0 -- lrclean pyproject.toml` prints lines of `pyproject.toml` only.

Commit subject: `build(lrclean): Add pyproject.toml, LICENSE and CHANGELOG for 0.1.0`

---

### Task CC: the README split and `docs/guide.md`

`Wave:` 2 · `Touches:` README.md, docs/guide.md · `After:` CA · `Mode:` code (the dispatch names both files as shared-file exceptions)

**Behaviour.** The README becomes short and the manual moves to `docs/guide.md`. Links to documents that will move still use today's paths (`docs/superpowers/specs/...`, `docs/plans/...`); CG rewrites them. Today's 13 sections go as follows:

| Today's section (line) | Goes to |
|---|---|
| Lead and "Status" (1 to 24) | README points 1, 2 and 5 (about five lines, then the phases one line each); "Status" is dropped (its claims "not packaged", "no license" are no longer true) |
| How it keeps your photos safe (26) | README point 3 as a short list; the detailed reject-check checklist and the sidecar, twin and journal bullets go to a guide section "The rules in detail" |
| Requirements (55) | README Quick start (Windows, Python 3.13) and guide "Requirements"; the paragraph "Run it from the repository folder as `python -m lrdup ...`" becomes the guide's "Running" text for `lrclean [--base <tool folder>] <command>` |
| Folders (66) | guide; only `work/` and `out/` rows stay (what each holds), the package, tests and docs rows go |
| Configuration (79) | guide, with the corrected `excluded_catalogs` cell |
| Commands (96) and Exit codes (119) | guide, unchanged except `lrclean` |
| The process, end to end (130) | guide (step 2 corrected); README point 5 keeps one line per phase |
| Reject check verdicts (164) | guide |
| Common situations (179) | guide |
| Limits (194) | README point 9 (short: purge cannot be undone, sync clients, Windows only, private catalog format) and the guide's full list |
| Tests (209) | guide: the run command, what `tests/mutants/` holds, and that the runner of the planted defects is not in the repository and CI does not run it. No test count is stated (the run prints it) |
| Before making this repository public (219) | dropped (done by this release) |

**README, in this order** (design section 6). Header: a centred `<picture>` (dark source `docs/assets/lrclean-logo-dark.svg`, light image `docs/assets/lrclean-logo-light.svg`, 420 px wide, `alt` text "LrClean", the wordmark's own spelling), the tagline "Free a full photo drive without losing a photo or breaking a Lightroom Classic catalog.", one row of six badges (CI status of `ci.yml`, latest release, Python 3.13, Windows, no dependencies, MIT; the last four static), one row of links to the README's sections and to `docs/guide.md`. Body: (1) what it is, about five lines; (2) who it is for: a backlog, many catalogs, duplicates, a full drive; (3) the safety promises as a short list: catalogs are never written; nothing leaves the drive without a verified copy on each of two archive volumes (separate drive letters and volume serials; that they are separate physical drives is yours to check, see below); every removal is from a list approved by its SHA-256; staging and undo come before any purge; online-only placeholders are never opened; (4) quick start: `pipx install lrclean`, make a tool folder outside the photo folders and write `work/config.json` in it (the nine required keys, with plainly synthetic values; `--base` defaults to the current folder, so run from the tool folder or pass `--base`), then the read-only report: `catalogs`, `inventory`, `hash`, `report`; (5) the phases, one line each, pointing to `docs/guide.md`; (6) "Before a real run", the checks done by hand today, all eight: Lightroom closed; the sync client stopped; the archive drives identified by label and serial; `catalogs`, `inventory`, `hash` and `backup-verify` run latest with both archive drives connected; `catalog_roots` compared with the drives and folders you use; no junction or substituted drive pointing into `photo_root` from a path a catalog root uses; the list's configuration not edited between `copies` and the last `stage`; and the set of paths the main catalog records that are missing, taken before `stage` and after each `stage --apply`, unchanged (a newly missing path stops the run); (7) "Tested with": Windows 11, Python 3.13, and the catalog format numbers the work database has recorded, six of them: 0600008, 0700003, 1303001, 1400000, 1500000, 1504001; older formats are untested, not known to fail; no Lightroom Classic release is named for a number (RC-163 (7): none is cited here, the design's "three" is out of date); (8) "Working with an AI assistant and the Lightroom plug-in": a pointer to `AGENTS.md` for assistants that work on the code, then the plug-in section, below; (9) limits, the roadmap (a link to the repository's 0.2 milestone), how it was built (a link to `docs/process/README.md`), licence, one line that the project is independent of Adobe.

**Plug-in section** (three points, design section 6): what a third-party Lightroom plug-in (an MCP server; MIT) was used for on the first library (flagging duplicates as rejects only after every catalog had been checked for develop work and picks; giving both copies of a duplicate the union of their keywords; removing the records of purged files from the per-shoot catalogs by id, each write proved by counts before and after; work otherwise done by hand, photo by photo); `lrclean` never needs the plug-in and never talks to it; when it is used, previews and metadata go to the model, so pin a version that has been read and keep removals behind a confirmation.

**Two corrections, word for word from the design.** Today's lines 32 to 33 and 136 say a backup manifest is approved by its SHA-256. The guide says: a backup manifest is approved by reading it, and `backup-run` refuses a manifest that breaks its rules but takes no SHA-256 of it; only removal lists and purge sets are approved by SHA-256 (the README's point 3 promise, "every removal is from a list approved by its SHA-256", is unchanged). The key table describes `excluded_catalogs` as: the catalog is still found and copied by `catalogs`; its records protect nothing and it never limits coverage. The cell (today's line 90) reads: "Catalogs whose records protect nothing (unreadable ones, or ones you judge irrelevant). `catalogs` still copies and reads them; only the verdicts ignore them."

**Timings.** No figure is given as the tool's speed: the guide's "Limits" drops "about 1.9 GB a minute". The text says a pass's duration depends on the drives (type, connection, one archive drive or two) and on how much is read again, and gives one example, labelled as one real run: about 143 GB staged in 75 minutes and purged in 76 on two USB hard drives.

**Line budget.** `README.md` is at most 150 lines (today 228); the header at most 20. `docs/guide.md` has no budget; it is the place where each README claim is shown.

**Reuses, adds nothing of.** The text of today's README, moved and trimmed; no new claim about the tool that today's README or the design does not make. No code, no test.

**Interfaces:** consumes the frozen file names (`docs/assets/lrclean-logo-light.svg`, `docs/assets/lrclean-logo-dark.svg`, `LICENSE`, `CHANGELOG.md`, `AGENTS.md`); produces the link lines CG rewrites.

**Acceptance (with the task's files staged: `git add` by name, so that `git grep` sees the new file):**
1. `wc -l README.md` prints at most 150; the nine points are present in order (a reviewer ticks them against design section 6).
2. `git grep -n -E 'each approved by their SHA-256|approve each manifest \(by SHA-256\)|never read, protect nothing|GB a minute' -- README.md docs/guide.md` prints nothing; the `excluded_catalogs` cell above occurs once in `docs/guide.md` (`git grep -c` on its first sentence).
3. `git grep -n -i -E 'lrdup|the owner' -- README.md docs/guide.md` prints nothing.
4. The report holds a claims table: each claim of the README, with the guide heading that shows it, or "example from one run" (the 143 GB, 75 and 76 minutes, and the six format numbers are the labelled ones). A reviewer reads the table against design section 11, last README bullet.
5. Every link and image path in the two files that points at a file this task does not create resolves in the worktree; the paths to `LICENSE`, `CHANGELOG.md`, `AGENTS.md` and `docs/process/README.md` are the known exceptions (made by CB, CD, CG); the two logo files exist already and are checked by CG's acceptance.

Commit subject: `docs(lrclean): Split the README and move the manual to docs/guide.md`

---

### Task CD: `AGENTS.md`

`Wave:` 2 · `Touches:` AGENTS.md · `After:` CA · `Mode:` code

**Behaviour.** One instruction file for any coding agent, at most 80 lines (design section 4): what the tool is (three lines); how to run the tests (`python -m unittest discover -s tests`; `tests/mutants/*.json` list planted defects, run by a runner that is not in the repository, and CI does not run it); the rules that are about the code and its data: H1 to H4 of `CLAUDE.md` as written (read-only phase A; never open an original catalog for writing; never classify by a folder name or suggest deleting a catalog file; no private name in a tracked file, commit message, issue, pull request or agent prompt), the move rule R1 (files move only from an approved list, with a journal, after content-verified copies), placeholders never opened, and tests on synthetic trees only; and the conventions (Python 3.13, standard library only, `unittest`, LF files, conventional commit subjects `type(scope): Subject` with the scope `lrclean`, a commit message names no role, it says what the change does). H1 to H4 are given by the dispatch's context as `CLAUDE.md` writes them (none names a role), with H3's sentence about one library's folder turned into a general example; R1 is the design's own summary (section 4). Where a sentence needs a person it says "you" or is impersonal; it never says "Peter" or "the owner". The implementer reads no other part of `CLAUDE.md`. The file does not mention `CLAUDE.md`, the ledgers, `out/` or the workspace.

**Reuses, adds nothing of.** The rules' own text; no new rule. A link to the design uses today's path (CG rewrites it).

**Interfaces:** consumes none; produces `AGENTS.md`.

**Acceptance (with the task's files staged: `git add` by name, so that `git grep` sees the new file):** `git grep -n -i -E 'lrdup|the owner|Peter|CLAUDE|\.superpowers' -- AGENTS.md` prints nothing; `wc -l AGENTS.md` at most 80; the test command in it is the one in constraints.md's `unit` gate without `-v`. The check against `CLAUDE.md` H1 to H8, R1 and R4 is controller step 3, not this task's.

Commit subject: `docs(lrclean): Add AGENTS.md for coding agents`

---

### Task CE: the three test fixes and the three role-naming strings

`Wave:` 2 · `Touches:` tests/test_cli.py, tests/test_catalogs.py, tests/test_backup.py, lrclean/backup_plan.py, lrclean/mover_gate.py · `After:` CA · `Mode:` code

**Behaviour.** Two tests need the checkout and the temporary folder on one drive; one asserts one second of wall-clock time. Each existing test is edited; none is added. (a) `tests/test_cli.py`, `test_base_that_is_a_root_is_refused_before_anything_is_created`: the spelling `os.path.relpath(self.lib)` raises `ValueError` across drives. The "relative path" spelling is added only when `relpath` works, in the way `tests/test_mover_list.py` (line 236) drops one spelling and still runs the others. (b) `tests/test_catalogs.py`, `test_work_dir_inside_the_catalog_folder_is_refused_and_nothing_created_rule_6`: the same drop for `os.path.relpath(os.path.join(home, "work"))`, with the other three spellings of `work` still run. (c) `tests/test_backup.py`, `test_rule22_a_stall_ends_the_run_with_the_lock_held_and_the_next_run_recovers`: the limit `assertLess(time.monotonic() - began, 1)` becomes 30 seconds, as ruled (RC-163 (2)). Consequence the ruling did not state, ruled in RC-164 (5): `_Blocking.read` waits at most 5 seconds on the gate, so a limit of 30 can no longer fail when the abort waits for the blocked worker, which is what the assertion guards. The blocked read's wait therefore becomes longer than the limit (one named constant used by both, and the test asserts the wait is greater than the limit); the gate is set in cleanup, so teardown does not wait. The implementer reads both callers of `_blocked_run` (lines 1088 and 1105) and the run's stall path first, picks the wait so that neither caller waits it out, and reports the number and why. The other `relpath` uses in the tests give a start folder under one tree and are not changed. (d) Wording only; no decision, exit code or file action changes (design section 10 holds). Three strings say "the owner" in text a public tool prints or a test shows: the last sentence of the plan note in `lrclean/backup_plan.py` (line 328 at BASE), the stale-lock message in `lrclean/mover_gate.py` (line 102) and the `subTest` label at `tests/test_cli.py` (line 425). Each is reworded to say "you" or to be impersonal and keeps its meaning; the implementer reads the sentence around each and writes the new text, which this plan does not dictate. No test or planted-defect `old` holds either message (checked 2026-11-25; the implementer greps again).

**Reuses, adds nothing of.** The drop-one-spelling pattern of `test_mover_list.py`. No new test, helper, skip or seam; no assertion is weakened beyond the one limit; the three strings change wording only.

**Interfaces:** consumes none; produces none.

**Acceptance (with the task's files staged: `git add` by name, so that `git grep` sees the changes):**
1. RED/GREEN for (a) and (b) in a scratch runner file (not committed): it patches `os.path.relpath` to raise `ValueError` only when `start` is omitted (`tests/test_catalogs.py:25` and `tests/mover_world.py` call it with two arguments) and runs the named test. At BASE the test errors with `ValueError`; after the edit it passes, and a `subTest` for each remaining spelling still ran (the report lists them).
2. (c): the test passes with the new limit; the report names the limit, the wait and the two callers' behaviour. RED, in a scratch run that is reported and not committed: keep the block's wait above the limit, add a bounded wait for the blocked worker in the stall path (bounded: the stall path holds the journal lock, so an unbounded wait deadlocks), and show that the edited test fails with the elapsed-time assertion as the reported failure; without it a limit of 30 seconds is not shown to guard anything.
3. The `unit` gate, which covers the three files, passes unpatched (a focused run of one file is `python -m unittest discover -s tests -p <file>`); the `mutants-backup` and `mutants-mover` gates pass (every defect caught; counts in the Gates table of Dispatch).
4. `git grep -n -i 'the owner' -- lrclean tests` prints nothing.

Commit subject: `fix(lrclean): Guard cross-drive tests and reword three role strings`

---

### Task CF: the logo (done)

Task CF (the logo): done in `39b43e7`; `docs/assets/lrclean-logo-light.svg` and `docs/assets/lrclean-logo-dark.svg` (the mark "twin frames"; the wordmark reads "LrClean", outlined). No open work.

---

### Task CG: move the process documents under `docs/process/`

`Wave:` 3 · `Touches:` docs/superpowers/specs/ → docs/process/specs/, docs/plans/ → docs/process/plans/, docs/outside-reads/ → docs/process/outside-reads/ (all `git mv`), docs/process/README.md (new), README.md, docs/guide.md, AGENTS.md, CLAUDE.md (link lines only) · `After:` CB, CC, CD, CE · `Mode:` serial, no gate

**Behaviour.** `git mv` only: the 19 documents of the three folders and this plan (20 files) move unedited, so their text, `lrdup` included, stays as written. `docs/superpowers/` disappears with its last file. `docs/process/README.md` is the note, written from design section 4: the documents were written during the build; "the owner" in them is Peter; they are kept unedited so that they still say what was reviewed; the command was `python -m lrdup` then and is `python -m lrclean` now; where they point to `CLAUDE.md`, the ledgers or `out/`, that material is not published; the rules in `CLAUDE.md` that are about the code and its data (H1 to H4, R1) are summarised in `AGENTS.md`. Every link or path to the three old folders in `README.md`, `docs/guide.md`, `AGENTS.md` and `CLAUDE.md` is rewritten to the new names; `CLAUDE.md` stays tracked here and is never exported. At BASE the `CLAUDE.md` lines are 5, 18, 204, 292 and 310 (found by their text, not their number). The README's "how it was built" link points at `docs/process/README.md`.

**Reuses, adds nothing of.** `git mv`; the note is the only new file. No edit inside a moved document, no redirect file, no script that rewrites a tracked file.

**Interfaces:** consumes the link lines of CC and CD; produces `docs/process/{specs,plans,outside-reads}/` and `docs/process/README.md`.

**Acceptance (commands, from the repository root):**
1. With the work staged (`git add` by name, so `--cached` sees it all): `git diff --cached -M --name-status <BASE-of-this-task> -- docs` lists `R100` lines for the 20 moved files, one `A docs/process/README.md`, at most one `M docs/guide.md` (its link edits), and no `D` line; a moved file shown with any similarity below 100 is a defect (an edit on the way).
2. `git grep -n -E 'docs/(superpowers|plans|outside-reads)' -- README.md docs/guide.md AGENTS.md CLAUDE.md CHANGELOG.md` prints nothing, and the same pattern in `lrclean` and `tests` prints nothing. (The moved documents are not searched: they keep their old paths on purpose.)
3. `git grep -h -o -E 'docs/[A-Za-z0-9_./-]+' -- README.md docs/guide.md AGENTS.md CHANGELOG.md | sort -u | while read -r p; do test -e "${p%.}" || echo "missing: $p"; done` prints nothing. (`CLAUDE.md` is left out of this command: it names a path of a tooling folder outside this repository, which the pattern would read as a path of this repository; its link lines are covered by command 2.)
4. The relative links and `src`/`srcset` paths of `README.md`, `docs/guide.md` and `AGENTS.md` resolve against each file's own folder: `for f in README.md docs/guide.md AGENTS.md; do git grep -h -o -E '(\]\(|src="|srcset="|href=")[^)" #]+' -- "$f" | sed -E 's/^(\]\(|src="|srcset="|href=")//' | grep -v -E '^(https?:|mailto:)' | while read -r t; do test -e "$(dirname "$f")/$t" || echo "$f: missing $t"; done; done` prints nothing (it reads `](`, `src="`, `srcset="` and `href="`).
5. `git grep -n -i lrdup -- . ':!docs/process' ':!CLAUDE.md'` prints nothing, and `git grep -n -i lrdup -- CLAUDE.md` prints line 311 only.

Commit subject: `docs(lrclean): Move the process documents under docs/process with a note`

---

### Task CH: CI, release workflow and Dependabot

`Wave:` 4 · `Touches:` .github/workflows/ci.yml, .github/workflows/release.yml, .github/dependabot.yml · `After:` CB, CE, CG (CH reads nothing CG writes; it waits only because CG is a serial writer in the main checkout and a landing moves HEAD) · `Mode:` code

**Behaviour (design section 7).** `ci.yml`, workflow `CI`, one job `unit` on a Windows runner with Python 3.13; triggers: push to `main`, every pull request, manual run, `workflow_call`. Top-level `permissions: contents: read`. Steps: (1) one step that prints the registry value `LongPathsEnabled` (under `HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem`) and sets it to 1 before the tests, because three test groups create paths over 260 characters without the extended prefix; (2) `python -m unittest discover -s tests`, so the job log shows the suite's summary line, skipped count included; (3) the version check, inline in the workflow (`shell: python`, standard library, no new file). It reads the first line of `CHANGELOG.md` that matches `^## ([0-9]+\.[0-9]+\.[0-9]+) - [0-9]{4}-[0-9]{2}-[0-9]{2}$` and the `version` of `pyproject.toml` (through `tomllib`); each must be plain `X.Y.Z` (a suffix such as `0.1.0rc1` fails); the two are compared as exact strings; on a tag the third value is the tag minus its leading `v`, compared as an exact string with both. Its inputs: the tag is taken from the environment variable `GITHUB_REF_NAME` when `GITHUB_REF_TYPE` is `tag` (any other value of `GITHUB_REF_TYPE`, or none, means no tag and two values are compared), and `CHANGELOG.md` and `pyproject.toml` are read from the working folder, so the controller can rerun acceptance 3 with a scratch folder holding the two files. No heading found, no version, or any difference exits non-zero and names the values.

`release.yml`, workflow `Release`, sets no `permissions` at top level. Triggers: a tag push whose filter is written exactly `'v[0-9]+.[0-9]+.[0-9]+'` (quoted; a literal `vX.Y.Z` would start no run on the first tag; the tag's type is not checked) and a manual run. Jobs: `checks` calls `ci.yml` (`contents: read`); `build` (needs `checks`; `contents: read`) checks out with `fetch-depth: 0`, on a tag fails unless `git merge-base --is-ancestor <tag commit> origin/main` succeeds, builds the sdist and the wheel, installs the built wheel into a fresh virtual environment outside the checkout and runs `lrclean --help` (the job fails if that fails), and keeps the built files as an artifact; `upload` (needs `build`; only on a tag; `id-token: write`; environment `pypi-release`, which waits for Peter's approval) publishes to PyPI by trusted publishing; `release` (needs `upload`; only on a tag; `contents: write`) creates the GitHub release with the section of `CHANGELOG.md` for the tag's version as notes and the built files attached; it passes the workflow token to `gh` as `GH_TOKEN`, names the repository, and checks out the tagged tree to read the tag's section of `CHANGELOG.md`. `upload` and `release` each download the build job's artifact of the same run. A manual run is a rehearsal: it runs `checks` and `build` and publishes nothing. The design says the rehearsal "builds and publishes nothing" and also that it fails if the wheel does not install; this plan reads the first as "publishes nothing", since a rehearsal that builds nothing could not install a wheel (reported). The two publishing jobs are guarded by the event (a tag push), not by the ref type, so a manual run started on a tag publishes nothing either. If `upload` succeeded and `release` failed, only `release` is re-run; a tag is never re-pushed or moved; if PyPI has the version, a fix goes out as the next version (said in a comment at the top of the file).

`dependabot.yml`: GitHub Actions only, monthly; no Python ecosystem and no scheduled CI run (CI runs on pushes, on every pull request, including those Dependabot opens, and on manual runs).

**Pins, resolved by the task, not from memory.** Every action is pinned by its full commit, with a comment naming its version. The task finds each action's latest release and its commit with `gh api repos/<owner>/<repo>/releases/latest --jq .tag_name` and `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`, and reports each command and its output. The same goes for the `build` package version (the command used is reported), the runner label (a Windows runner for `ci.yml` and `build`), and the runner the publish action needs: the task reads that action's README at the pinned commit and sets the `upload` job's runner by it. If `gh` is denied or returns nothing, the task returns NEEDS_CONTEXT and writes no SHA. Whether a manual run needs the workflow file on the default branch is checked in GitHub's documentation and the page is cited; if it does, the rehearsal is controller step 7, not this task's acceptance.

**Reuses, adds nothing of.** `ci.yml` through `workflow_call` (so `release.yml` repeats no check), `cli.main` through the entry point, `gh` on the runner for the release (no third-party release action). No script file, no cache, no matrix, no secret, no scheduled run, no planted-defect step (those gates stay local).

**What the first hosted run may show.** It is unknown (design risk 1). The task cannot push, so the first run comes after the controller's push of the pull request and is watched by the `ci-watcher`. If it fails for a reason of the runner (drive layout, path length, link rights, file attributes), the controller resumes this task as a fix round with the watcher's quoted lines. The fix is the smallest change to the workflow, or to the environment assumption of a test as CE did, that keeps what the test guards; a test outside CE's three that fails is BLOCKED and goes to the controller as a decision. No assertion is loosened, no test deleted or skipped to turn the run green.

**Interfaces:** consumes `version` and the heading format (CB), `lrclean.cli:main`; produces the three files and the names in Frozen interfaces.

**Acceptance (with the task's files staged: `git add` by name, so that `git grep` and `git ls-files` see the new files):**
1. `git ls-files .github` lists exactly the three files; every `uses:` except the local call of `ci.yml` ends in a 40-hex commit and a version comment (`git grep -n 'uses:' -- .github | grep -v -E 'uses: *[^ #]+@[0-9a-f]{40} +# +v?[0-9]'` prints only the local call).
2. Permissions: `ci.yml` has `contents: read` at top level; `release.yml` has none at top level; `id-token: write` appears only in `upload`, `contents: write` only in `release` (`git grep -n -E 'id-token|contents: write' -- .github`).
3. The version check, run from a scratch copy of its script with fixtures built in a scratch folder (the `run:` body of the step is extracted to a file and run with the fixtures as its inputs): pass for `0.1.0`, `0.1.0`, tag `v0.1.0`; a failing case per value, each changing one value to a different one and asserting it differs from the other two (`pyproject` `0.1.1`; heading `0.1.1`; tag `v0.1.1`); a suffix case where all three agree on `0.1.0rc1` and the check still fails; a changelog whose first heading is `## Unreleased` above `## 0.1.0 - 2026-11-24` passes (the first such heading is read); no tag value compares two. The inputs are the ones named in the behaviour: `GITHUB_REF_TYPE` and `GITHUB_REF_NAME` in the environment and the two files in the working folder. The report quotes each exit code. No mutants file: the check is one inline step, not a module, so there is nothing for `mutate.py` to mutate.
4. Each file parses as YAML (a parser in a scratch virtual environment outside the checkout, a test-time tool only; `actionlint` too if installed); the report says which ran.
5. Required strings, one `git grep -c -E '<pattern>' -- <file>` per line of this list, each printing the count given, and a count of 0 printing nothing (the first public tag is the first time any of this runs against PyPI, so a wrong name here is not found by a green CI run). `.github/workflows/ci.yml`: `^name: CI$` 1; `^  unit:$` 1 (the job name the ruleset requires); each trigger key once (`push:`, `pull_request:`, `workflow_dispatch:`, `workflow_call:`), so four triggers; `LongPathsEnabled` at least 1; `python -m unittest discover -s tests` 1; `contents: read` 1; `schedule:` 0. `.github/workflows/release.yml`: `^name: Release$` 1; each of `^  checks:$`, `^  build:$`, `^  upload:$`, `^  release:$` 1; triggers: a tag push (`tags:`) 1, the filter line (`git grep -c -F "'v[0-9]+.[0-9]+.[0-9]+'"`) 1 and `workflow_dispatch:` 1, `pull_request:` and `schedule:` 0; `pypi-release` 1 (PyPI matches the name exactly); `fetch-depth: 0` at least 1; `merge-base --is-ancestor` 1; `needs:.*checks`, `needs:.*build` and `needs:.*upload` each at least 1 (`build` needs `checks`, `upload` needs `build`, `release` needs `upload`); `download-artifact` 2 (the download step of `upload` and of `release`); `uses: \./\.github/workflows/ci\.yml` 1 (the `checks` job). `.github/dependabot.yml`: `package-ecosystem:` 1 and `github-actions` 1; `interval:.*monthly` 1; `pip` 0. The workflow files are written in block style with a two-space indent and one `needs:` per line, and no comment in them repeats a pattern of this list.
6. The `unit` gate passes on the unchanged code.

Commit subject: `ci(lrclean): Add the CI, release and Dependabot workflows`

---

## Spec coverage

| Design item | Delivered by |
|---|---|
| Section 4 table: `lrclean/` and `tests/` with the planted-defect files | CA |
| `README.md` (short) and `docs/guide.md` | CC |
| `LICENSE`, `CHANGELOG.md`, `pyproject.toml` | CB |
| `AGENTS.md` | CD |
| `.gitignore` | CB (`.gitattributes` is present and untouched) |
| `.github/`: `ci.yml`, `release.yml`, `dependabot.yml` | CH |
| `docs/assets/`: the logo, light and dark | done in `39b43e7` (frozen names in CC) |
| `docs/process/` with a note | CG |
| Left out: `CLAUDE.md`; `out/`, `work/`, `.superpowers/`, private notes (untracked, listed in `.gitignore`); the commit history | controller step 5 (the export); CB (the `.gitignore` line) |
| Section 4 bullet `AGENTS.md` content; the check against `CLAUDE.md` | CD; controller step 3 |
| Section 4 bullet, the `docs/process/` note | CG |
| Section 4 bullet, topics | controller step 6 |
| Section 5 step 2: the rename; the hand step for the one work folder | CA; controller step 2 |
| Section 5 step 2: the two test fixes and the time limit | CE (with the three role-naming strings) |
| Section 6 header, badges | CC (logo files: `39b43e7`) |
| Section 6 logo | done in `39b43e7` (frozen names in CC) |
| Section 6 body points 1 to 9 | CC |
| Section 6 `docs/guide.md` content; the two corrected statements and the `excluded_catalogs` cell | CC |
| Section 6 timings text | CC |
| Section 6 plug-in section (three points) | CC |
| Section 7 `ci.yml` (steps, version check, pins, read-only token, gates local) | CH |
| Section 7 `release.yml` (checks, ancestor check, build and rehearsal, upload, release, permissions, re-run rule) | CH |
| Section 7 four fixed strings | CH (workflow and environment names); controller step 6 (the environment); controller step 8 (the PyPI side) |
| Section 7 `dependabot.yml` | CH |
| Section 7 ruleset on `main` | controller step 6 |
| Section 8 milestone 0.2 and its issues | controller step 6 |
| Section 11: `pipx install lrclean`, `lrclean --help`, the read-only report | CB (wheel half); controller step 9 |
| Section 11: CI green on the first public commit; ruleset on; rehearsal before the tag | CH; controller steps 4, 6, 7 |
| Section 11: the exported tree passes the sweep, tree ids equal | controller step 5 |
| Section 11: environment `pypi-release` with its reviewer before the tag | controller step 6 |
| Section 11: `AGENTS.md` read against H1 to H8, R1, R4 | controller step 3 |
| Section 11: the README fits a few screens, each claim shown or labelled | CC (acceptance 1 and 4) |
| Section 11: the 0.2 milestone exists with the issues | controller step 6 |

## Controller steps

Where a step writes outside this repository or to the real work folder, it waits for Peter's go-ahead ("Peter's gate").

1. **At the landing of CA.** Rewrite the Gates block's `when:` paths and the other `lrdup` names in constraints.md; run `unit` and the five planted-defect gates with `when:` ignored (every defect caught; counts in the Gates table of Dispatch); delete the leftover `lrdup/` folder from the main checkout (it holds only `__pycache__`).
2. **The real work folder** (Peter's gate: it writes to the real work and archive folders). Before the first `lrclean` run, with no tool process running: rename `work/lrdup.sqlite` to `work/lrclean.sqlite` together with `work/lrdup.sqlite-wal` and `work/lrdup.sqlite-shm` (to `lrclean.sqlite-wal` and `lrclean.sqlite-shm`) when they exist, never the main file alone (an aborted run leaves a write-ahead file, and a main file renamed alone reads as empty, with no error); and remove any leftover `*.lrdup-part` files from the archive folders (Peter lists them first, then removes them). No agent reads or lists either folder.
3. **`AGENTS.md` against `CLAUDE.md`.** After CD and before the export: read `AGENTS.md` against H1 to H8, R1 and R4, and record that each is held, summarised or out of scope.
4. **CI block and first CI leg.** After CH lands, add the CI block to constraints.md (job `unit` of `ci.yml`); the pull request of this branch is the first with a CI leg; the `ci-watcher` watches the head of each push and the controller handles a runner failure as CH says.
5. **The export** (Peter's gate: the push goes outside this repository). The merged tree without `CLAUDE.md`, authored by Peter with a plain message. Before the push: the private-name sweep over the exported tree, and the tree id of the export commit equals the tree id of the merged commit with `CLAUDE.md` removed (temporary index: `git read-tree`, `git rm --cached CLAUDE.md`, `git write-tree`).
6. **The public repository** (Peter's gate). After the first green run: the ruleset on `main` (no deletion, no force-push, `unit` required, no required approvals), the topics and description, the 0.2 milestone with the issues of design section 8 and issue 8 of this repository (each body swept first), and the environment `pypi-release` with Peter as required reviewer and self-review allowed; Peter looks at Settings > Environments and sees the reviewer before the tag.
7. **The rehearsal.** A manual run of `release.yml` in the public repository after CI is green and before the tag; it must pass (and is the place where a default-branch requirement for manual runs, if any, is met).
8. **The tag** (Peter's gate). Peter is told BEFORE the tag is pushed (Peter, 2026-11-24: "Let me know when the PyPI stuff comes in"); before the push Peter confirms on PyPI that the pending publisher still shows the four fixed strings and that the project name is still free (a pending publisher reserves no name); the date of the `0.1.0` entry in `CHANGELOG.md` is checked against the tag day; the PyPI page is looked at after the upload (the header images are expected to be missing there).
9. **Clean-machine check.** After the upload: `pipx install lrclean`, `lrclean --help`, and the read-only report on a synthetic tree built in a temporary folder (design section 11, first bullet).
10. **Branch-end checks, before the export.** Re-run CG acceptance 2 to 5 (they hold the `lrdup` greps and the link checks); and, the one new check, `git grep -n -i -E 'the owner|C:/Us[e]rs' -- . ':!docs/process' ':!CLAUDE.md'` prints nothing.

## Not in this plan

- Any change to what the tool does to files or catalogs, and any code change other than the rename (design section 10).
- A migration for the old stored names in code, an alias module for `lrdup`, a `--version` option, a Python ecosystem in Dependabot, a scheduled CI run, a planted-defect step in CI.
- Naming a Lightroom Classic release for a catalog format number, until a source is cited and checked.
- Moving development to the public repository and freezing this one (design section 5, step 6).
