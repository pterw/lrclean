# Public release of the tool as `lrclean` 0.1.0: design

Status: approved by Peter on 2026-11-24, then given to an outside reader (prompt:
`docs/outside-reads/2026-11-24-public-release-design-prompt.md`). The reader's 24 rows (A01 to A09, B01 to
B15) were verified on 2026-11-24. A01 and A02 were code defects, fixed in the tasks BC and BD of the
pre-release safety pull request (issues 6 and 7, with issue 5 beside them); the other 22 rows are applied in
the text below. Nothing in it is built.

## 1. Goal

Make the tool usable by other photographers who have a backlog, several Lightroom Classic catalogs and a full
drive, without weakening what made it safe to run on one real library. Publish it as a small, honest 0.1.0:
what it does, what it was proven on, and what is still done by hand.

Tagline: "Free a full photo drive without losing a photo or breaking a Lightroom Classic catalog."

## 2. Decisions already made (2026-11-24)

| Topic | Decision |
|---|---|
| Route | A fresh public repository from one curated commit. The current private repository keeps its history and is frozen as the archive once 0.1.0 is out. |
| Name | `lrclean` for the repository, the package and the command ("LrC" is Lightroom Classic). |
| Licence | MIT. The code is standard-library Python with no third-party code in the tree. |
| Scope of 0.1.0 | The tool as it stands. Windows. No macOS (there is no Mac library to prove it on). |
| Lightroom Classic | Named plainly. The README lists the catalog format versions the tool has read and says older formats are untested, not known to fail. One line says the project is independent of Adobe. |
| History and identity | The commit e-mail and the generic name of one catalog in old history are not a concern. The public repository starts without that history anyway. |
| `CLAUDE.md` | Not published. An `AGENTS.md` is. |
| Process documents | Published as they are, as the record of how the tool was built. |
| Voice | Commit messages and public documents never say "the owner". They state what a change does, or speak in the first person. |
| Packaging | A versioned release on GitHub (built package and the automatic source zip) and on PyPI at 0.1.0. |
| Ideas | Planned additions are feature issues under a 0.2 milestone. |
| CI | Built in the private repository first (ruling R31 stands), then carried into the public one. |

## 3. Privacy pass (done 2026-11-24)

All 240 commits on every branch were swept: messages, every added line, paths and identities, against 5,849
names taken from the library listing plus drive labels and serials, and against shapes for e-mail addresses,
user-profile paths and secrets.

- Found in no commit: a client or shoot name, a folder name of the library, a user-profile path, an e-mail in
  file content, a secret, a drive label or serial.
- Found: the generic name of the main catalog in one old commit of `CLAUDE.md`, removed later; the personal
  e-mail as commit author. Both accepted.
- Not swept again: the comments on the two merged pull requests. Limit of the sweep: it knows folder names one
  and two levels under the photo folders and catalog names; a deeper file name would not be caught.

The same sweep runs on the exported tree before the first public push (section 5).

## 4. What the public repository holds

| Included | Left out |
|---|---|
| `lrclean/` (renamed from `lrdup/`) and `tests/`, with the planted-defect files | `CLAUDE.md` |
| `README.md` (short) and `docs/guide.md` (the manual) | `out/`, `work/`, `.superpowers/`, the private notes |
| `LICENSE`, `CHANGELOG.md`, `pyproject.toml` | the private repository's commit history |
| `AGENTS.md`, `.gitignore`, `.gitattributes` | |
| `.github/`: `ci.yml`, `release.yml`, `dependabot.yml` | |
| `docs/assets/`: the logo, light and dark | |
| `docs/process/`: the design, the plans and the outside-read prompts, unedited, with a short note | |

- **`AGENTS.md`** is the one instruction file for any coding agent: what the tool is, how to run the tests, the
  rules that are about the code (and its data: H1 to H4 of CLAUDE.md as written (read-only phase A; never open an
  original catalog for writing; never classify by a folder name or suggest deleting a catalog file; no private
  name in a tracked file, commit message, issue, pull request or agent prompt), the move rule R1 (files move only
  from an approved list, with a journal, after content-verified copies), placeholders never opened, and tests on
  synthetic trees only), and the conventions (Python 3.13, standard library
  only, `unittest`, commit style). A local `CLAUDE.md`, which the public `.gitignore` lists (the line is added
  in the release-prep branch, and the `.gitignore` comments are reworded to the Voice rule), may import it with
  `@AGENTS.md`.
- **The note in `docs/process/`** says the documents were written during the build, that "the owner" in them is
  Peter, and that they are kept unedited so that they still say what was reviewed. It adds that the command was
  `python -m lrdup` then and is `python -m lrclean` now, and that where these documents point to `CLAUDE.md`,
  the ledgers or `out/`, that material is not published; the rules in `CLAUDE.md` that are about the code and its data (H1 to H4, R1) are summarised in `AGENTS.md`.
- **Topics:** `lightroom-classic`, `photography`, `photo-management`, `deduplication`, `duplicate-files`,
  `backup`, `raw-photos`, `digital-asset-management`, `python`, `cli`, `windows`.

## 5. Where the work happens, and in what order

1. The unrecorded-copies run finishes on the real library; its pull request merges into `main` of the private
   repository (it closes issue 3).
2. A **release-prep branch** is cut from that `main`. Everything in section 4 is built there as ordinary
   planned, reviewed and gated tasks:
   - the rename `lrdup` to `lrclean` (package folder, `python -m`, imports, tests, the planted-defect files'
     paths, the Gates block, the documents that name the command, except `docs/process/`), from a sweeper hit
     list. The two persisted names are renamed with everything else: `lrdup.sqlite` becomes `lrclean.sqlite`
     and `.lrdup-part` becomes `.lrclean-part`. The one existing work folder needs its database renamed once by
     hand, and any leftover `*.lrdup-part` files are removed from the archive folders before the first
     `lrclean` run;
   - `pyproject.toml` (name `lrclean`, version 0.1.0, `requires-python >= 3.13`, the `lrclean` command, no
     dependencies);
   - the README split, `docs/guide.md`, `AGENTS.md`, `LICENSE`, `CHANGELOG.md`, the logo; links to documents
     that move to `docs/process/` are rewritten;
   - CI and the release workflow, as the branch's last task. Its pull request is the first to have a CI leg in
     its merge bar. Two tests need the checkout and the temporary folder on one drive (`test_cli.py`, the base
     that is a root; `test_catalogs.py`, the work folder inside the catalog folder), and one test in
     `test_backup.py` asserts one second of wall-clock time: fixing them (a guard against the `ValueError` that `os.path.relpath` raises across drives, as the other two uses already have; a limit of 30 seconds) is a task of this CI work, not part of CI configuration.
3. The public repository `lrclean` is created empty. Its first commit is an **export** of the merged tree
   without `CLAUDE.md`, authored by Peter with a plain message. Before the push: the private-name sweep over
   the exported tree, and a comparison of tree ids: the tree id of the export commit equals the tree id of the
   merged commit with `CLAUDE.md` removed (a temporary index: `git read-tree`, `git rm --cached CLAUDE.md`,
   `git write-tree`); equal ids mean the same paths, contents and file modes. `CLAUDE.md` is the only tracked
   path left out; anything else in section 4's left-out column is untracked.
4. CI runs on that first push. After it is green: the ruleset is switched on, the topics and description are
   set, the 0.2 milestone and its issues are created (each body swept first). The environment `pypi-release`
   is created in the public repository with Peter as required reviewer and self-review allowed; Peter looks at
   Settings > Environments and sees the reviewer before step 5.
5. The `v0.1.0` tag is pushed; the release workflow publishes.
6. Development moves to the public repository. The private one is frozen. The ledgers, `work/` and `out/` stay
   untracked in the working folder, and the private notes repository keeps backing them up.

While a real run is in progress, the checkout that runs the tool stays on the gated code: release prep happens
in a separate worktree.

## 6. The README

**Header**, as in Peter's other repositories: a centred `<picture>` with a light and a dark SVG lockup from
`docs/assets/`, 420 px wide; the tagline; one row of badges; a row of section links.

- **Logo:** a simple mark beside the lowercase name `lrclean`. Two or three variants are drawn and shown rendered,
  in light and dark, before one is chosen. It does not imitate Adobe's product tile.
- **Badges, six:** CI status, latest release, Python 3.13, Windows, no dependencies, MIT.

**Body, short:**

1. What it is, in about five lines.
2. Who it is for: a backlog, many catalogs, duplicates, a full drive.
3. The safety promises, as a short list: catalogs are never written; nothing leaves the drive without a verified
   copy on each of two archive volumes (separate drive letters and volume serials; that they are separate
   physical drives is yours to check, see below); every removal is from a list approved by its
   SHA-256; staging and undo come before any purge; online-only placeholders are never opened.
4. Quick start: install, write the configuration, run the read-only report.
5. The phases, one line each, with a pointer to `docs/guide.md` for the step-by-step.
6. Before a real run: the checks that are done by hand today (Lightroom closed; the sync client stopped; the
   archive drives identified by label and serial; `catalogs`, `inventory`, `hash` and `backup-verify` run latest
   with both archive drives connected; `catalog_roots` compared with the drives and folders you use; no junction
   or substituted drive pointing into `photo_root` from a path a catalog root uses; the list's configuration not
   edited between `copies` and the last `stage`; and the set of paths the main catalog records that are
   missing, taken before `stage` and after each `stage --apply`, unchanged: a newly missing path stops the
   run).
7. Tested with: the catalog format versions read, Windows 11, Python 3.13.
8. Working with an AI assistant and the Lightroom plug-in (below).
9. Limits; the roadmap (the 0.2 milestone); how it was built (`docs/process/`); licence; independence from
   Adobe.

`docs/guide.md` takes the subcommand table, every configuration key, the exit codes and the step-by-step of
each phase from today's README.
Two README statements are corrected when they move: a backup manifest is approved by reading it, and
`backup-run` refuses a manifest that breaks its rules but takes no SHA-256 of it; only removal lists and purge
sets are approved by SHA-256. The key table describes `excluded_catalogs` as: the catalog is still found and
copied by `catalogs`; its records protect nothing and it never limits coverage. The cell reads: "Catalogs whose
records protect nothing (unreadable ones, or ones you judge irrelevant). `catalogs` still copies and reads
them; only the verdicts ignore them."

**Timings.** No figure is given as the tool's speed. The text says a pass's duration depends on the drives
(type, connection, one archive drive or two) and on how much is read again, and gives one example labelled as
one real run: about 143 GB staged in 75 minutes and purged in 76 on two USB hard drives.

**The plug-in section** says three things:

- What a third-party Lightroom plug-in (an MCP server; MIT) was used for on the first library: flagging
  duplicates as rejects only after every catalog had been checked for develop work and picks, giving both copies
  of a duplicate the union of their keywords, and removing the records of purged files from the per-shoot
  catalogs by id, each write proved by counts before and after. This is work that is otherwise done by hand,
  photo by photo.
- `lrclean` never needs the plug-in and never talks to it.
- When it is used, previews and metadata go to the model: pin a version that has been read, and keep removals
  behind a confirmation.

## 7. CI, release and repository settings

- **`ci.yml`:** on every push to `main`, every pull request, manual runs and `workflow_call`. Windows, Python
  3.13. Steps: one step that prints the registry value `LongPathsEnabled` and sets it to 1 before the tests,
  because three test groups create paths over 260 characters without the extended prefix; the unit suite
  with the command `python -m unittest discover -s tests`; and a check that the version in `pyproject.toml`,
  the top entry of `CHANGELOG.md` and (on a tag) the tag agree. The CHANGELOG heading is
  `## X.Y.Z - YYYY-MM-DD`; the check reads the first such heading and compares `X.Y.Z` as an exact string with
  the `pyproject.toml` version and with the tag minus its leading `v`. Versions are plain `X.Y.Z`; a suffix
  fails the check. The job's log shows the suite's summary line, skipped count included. Actions pinned by commit; a read-only token. The planted-defect gates stay
  local.
- **`release.yml`:** on a `vX.Y.Z` tag (the tag's type is not checked): the same checks; the workflow checks
  out full history (`fetch-depth: 0`) and fails unless `git merge-base --is-ancestor <tag commit> origin/main`
  succeeds, so the tag's commit must be on `main`; build the sdist and wheel; the build step installs the built
  wheel into a fresh virtual environment outside the checkout and runs `lrclean --help`, and the rehearsal
  fails if that fails; publish to PyPI by trusted publishing from the protected environment `pypi-release`,
  which waits for Peter's approval; then create the GitHub release with the changelog section as notes and the
  built files attached. Upload and GitHub release are separate jobs, the release job needing the upload job.
  If the upload job succeeded and the release job failed, re-run only the failed job; never re-push or move
  the tag. If PyPI has the version, any fix goes out as the next version. `release.yml` sets no permissions at
  top level; only the upload job has `id-token: write`, only the GitHub-release job has `contents: write`, the
  others `contents: read`. A manual run is a rehearsal that builds and publishes nothing.
- **Four strings are fixed here** because PyPI's publisher setup must match them exactly: the GitHub account
  `pterw` (PyPI's owner field), repository `lrclean`, workflow `release.yml`, environment `pypi-release`.
  Project name on PyPI: `lrclean`.
- **`dependabot.yml`:** GitHub Actions only, monthly. No Python ecosystem (there are no dependencies) and no
  scheduled CI run (CI runs on pushes, on every pull request, including any that Dependabot opens, and on manual runs; a month without a pull request has no run).
- **Ruleset on `main`:** no deletion, no force-push, the unit job required. Switched on after the first green
  run, because a required check would block the first direct push. No required approvals.

## 8. Milestone 0.2, as feature issues

- A pre-flight command for the checks done by hand (Lightroom closed, sync client stopped, drives by serial).
- The live-catalog path check as a subcommand.
- Drive identity recorded with a backup.
- HEIC and video files in the duplicate pass.
- macOS support (needs a Mac library to prove it on).
- A report of identical groups that no catalog records.
- The inbox for loose photos outside the photo folders.
- The catalog tidy phase.
- Keywords by capture device, through the plug-in.

## 9. Risks and open points

1. **The suite on a hosted runner.** The tests have only run through a local runner on one machine. Whether
   they pass under `python -m unittest discover -s tests` on GitHub's Windows image (links, long paths, file
   attributes) is unknown until the first run; that is why CI is built in the private repository first. Two
   tests that need the checkout and the temporary folder on one drive and one test with a one-second
   wall-clock assertion are fixed in the CI work of the release-prep branch (section 5, step 2), not by CI
   configuration.
2. **The PyPI name.** `lrclean` returned "not found" on 2026-11-24. That shows it is not taken, not that PyPI
   accepts it (it refuses names too similar to existing ones). A pending publisher for the project name
   `LrClean` (which PyPI treats as `lrclean`) was created the same day, with the four strings of section 7, and
   PyPI accepted the form. A pending publisher does not reserve the name: until the first upload, another
   account can still create the project.
3. **The catalog formats to name.** The work database records three format numbers. The README must name the
   Lightroom Classic releases they belong to; that mapping is to be checked, not assumed.
4. **The rename.** It touches every import and several documents. It is done once, from a sweeper list, with
   the whole local gate set run afterwards.
5. **Two repositories for a while.** Between the export and the freeze, a fix made in one would have to be made
   in the other. The export is therefore the last change to the private repository's code.
6. **Actions minutes.** Windows runs count double on a private repository; the plan's included minutes are
   expected to cover the few runs of the release-prep branch.

## 10. Not in this design

- Any change to what the tool does to files or catalogs.
- A graphical interface, an installer, a signed executable.
- Rewriting the private repository's history.
- Publishing the ledgers, reports or anything derived from the real library.

## 11. Acceptance

- A clean machine can `pipx install lrclean`, run `lrclean --help` and the read-only report on a synthetic tree.
- CI is green on the first public commit; the ruleset is on; the release workflow's rehearsal passes before the
  tag.
- The exported tree passes the private-name sweep, and its tree id equals that of the merged commit with
  `CLAUDE.md` removed (section 5, step 3).
- The environment `pypi-release` exists with its required reviewer before the tag.
- `AGENTS.md` is read against CLAUDE.md H1 to H8 and R1 and R4 before the export.
- The README fits on a few screens, and each claim in it is either demonstrated by the guide or labelled as an
  example from one run.
- The 0.2 milestone exists with the issues of section 8.
