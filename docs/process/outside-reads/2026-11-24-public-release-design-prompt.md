# Outside read: the design of the public release as `lrclean` 0.1.0 (prompt)

A ready prompt for a read-only pass by another model over the design for publishing this tool: a fresh public
repository, a rename, a package on PyPI, CI and a release workflow. Paste everything below the line into the
other model's session.

Before you start it:

- Run it in the clean clone prepared for it, at the commit named below, never in the working folder: the working
  folder holds untracked private files that no outside model may read.
- Two runs are worth more than one. Give the first run angle A and the second run angle B (see "Your angle").
- Bring back the two output files of each run. Every row is checked here before anyone acts on it.

---

You are reviewing a design by reading it and the repository it describes. You change nothing in the repository
and you run none of its code or tests.

## What to read

Repository: this folder, a clean clone. Commit: `8f9839cb3ac7a1b1c283404ac6995c4119610a2f` (check with
`git --no-pager rev-parse HEAD`; if it differs, stop and say so). Use `git --no-pager` for every git command, and
only commands that read (`log`, `show`, `ls-files`, `grep`, `diff`, `rev-parse`).

- `docs/superpowers/specs/2026-11-24-public-release-design.md`: the design under review, all eleven sections.
- `README.md`: today's README, which the design splits into a short README and `docs/guide.md`.
- `lrdup/` (the package the design renames to `lrclean`) and `tests/`, including `tests/mutants/*.json` and
  `tests/synth.py`.
- `CLAUDE.md`: the private instruction file that the design leaves out of the public repository, and the source
  of what `AGENTS.md` must carry. Its hard rules H1 to H8 and rulings R2, R3, R30 and R31 matter here.
- `docs/plans/`, `docs/outside-reads/` and `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`: the process
  documents the design publishes unedited under `docs/process/`.
- `.gitignore` and `.gitattributes`.

There is no `pyproject.toml`, no `LICENSE`, no `AGENTS.md` and no `.github/` folder at this commit: the design
says they are to be built. Do not report their absence.

## What the change is for

The tool was built for one photographer's library on Windows: it reads Lightroom Classic catalogs from copies,
backs photo folders up to two archive drives, verifies the copies, and then moves approved lists of files off the
system drive through a staging folder with an undo, before a purge. It is to be published for other
photographers as version 0.1.0 under the name `lrclean`, as it stands, with honest limits.

The design's route: finish the current work in the private repository; cut a release-prep branch there that
holds the rename, the packaging, the documents and the workflows, with CI as its last task; export the merged
tree (without `CLAUDE.md`) as the single first commit of a new public repository; run CI there, switch on the
branch rules, tag `v0.1.0`, and publish to PyPI by trusted publishing. The private repository is then frozen.

What an implementer will see: one task at a time cut from a plan that is written from this design, plus the
repository. Anything the design leaves unsaid, an implementer will guess.

## Your angle

Take the one angle you were given.

- **Angle A, what becomes public.** Find anything in the tree at this commit that should not be published, or
  that would mislead a stranger who runs the tool on their own library. Try at least these:
  - a real name, a real path, a drive label or serial, an e-mail address, a token, or text taken from a real
    library, in any tracked file: code, tests, fixtures, the planted-defect files, the process documents;
  - a tracked file the design's "Included" table does not mention, and whether it should be published;
  - a process document that names a tool, a path, a person or a private file a public reader cannot have, in a
    way that breaks the document or reveals something (as opposed to merely being unexplained);
  - text in `CLAUDE.md` that `AGENTS.md` needs and the design's list for `AGENTS.md` does not cover, and text the
    design would carry over that should stay private;
  - a safety promise in section 6 that the code at this commit does not keep, or keeps only under a condition
    the README draft does not state. Cite the code;
  - a step that is done by hand today and is not in the design's list of hand checks, so that a stranger would
    skip it;
  - a claim in the design about what was tested that the repository cannot support;
  - behaviour that is safe on the first library only because of how that library or machine is set up (one
    system drive, two archive drives, a named sync client, Windows 11, long paths enabled).
- **Angle B, the release engineering.** Find any step of sections 5 and 7 that will not work as written, or an
  order of steps that fails. Try at least these:
  - the rename `lrdup` to `lrclean`: every place the name occurs (imports, `python -m`, strings, tests, the
    planted-defect files' `file` and `pattern` fields, documents, messages a user sees), and any place a blind
    rename would break (a name that only looks like the package name, a path inside a fixture);
  - the tests under plain `python -m unittest discover` on a hosted Windows runner: a test that needs a
    privilege (creating a symbolic link), a junction, a long path, a file attribute, a second drive, a home
    folder, an environment variable set by a local runner, a fixed absolute path, a timing, or the working
    directory. Cite each test by file and name; say `unproven` where you cannot tell without running;
  - how the tests are found and whether discovery would also collect helper modules or the planted-defect
    files;
  - `pyproject.toml` as specified: the package layout it implies, the console command and `cli.py`'s entry
    point at this commit, `python -m lrclean`, data files a wheel would miss, `requires-python`;
  - the version check (the version in `pyproject.toml`, the top of `CHANGELOG.md`, the tag): where it can pass
    wrongly;
  - trusted publishing: the three strings of section 7 against what the workflow must contain, the protected
    environment, the permissions each job needs, what happens when the tag is not on `main`, a re-run after a
    partial failure, and a project name whose display form differs in case from the package name;
  - the order in section 5: the first direct push against a ruleset, a required check that has never run, the
    tag before or after the milestone, and the window in which two repositories hold the code;
  - the export: how "the merged tree minus the named exclusions" is checked, and what that check would miss
    (a file mode, line endings under `.gitattributes`, an ignored file that is tracked);
  - Dependabot for GitHub Actions alone, with actions pinned by commit, and the claim that its monthly pull
    requests replace a scheduled run.

## Evidence rules

- Read the callers and what follows a change before calling something broken.
- For a regular expression, check how it is built and its flags.
- Search where a result is reported before calling it silent.
- Cite a real shape (file, line range, the text) or mark the row `unproven`.
- Check `CLAUDE.md`'s rulings and the tests before calling something a finding: a ruling may already decide it.
- A statement about an outside service (PyPI, GitHub Actions, rulesets, Dependabot) names the documentation
  page it rests on, or is marked `from memory`.

## Already known: do not report these

- The six items of the design's section 9, as stated there. Report one of them only if you can show it is worse
  or different than stated, with evidence.
- The limits stated in section 3: the comments on the two merged pull requests were not swept again, and the
  sweep knows folder names only one and two levels under the photo folders, plus catalog names.
- The commit author's e-mail and the generic name of one catalog in an old commit of `CLAUDE.md`: accepted.
- The process documents say "the owner" throughout: accepted; they are published unedited with a note.
- The mapping from catalog format numbers to Lightroom Classic releases is not yet checked (section 9, item 3).
- The planted-defect gates stay local and use a runner that is not in this repository.
- A pending publisher exists on PyPI for the project name; it does not reserve the name.

## What to hand back

Create a folder `outside-read/` in this clone and write two files into it, and nothing anywhere else:

1. `findings-<a|b>.tsv`, written by a script from your notes, not typed: one row per finding, tab-separated, with
   the header `id`, `severity` (critical, important, minor), `confidence` (high, medium, low), `section` (the
   design section), `file`, `lines`, `claim`, `evidence`, `suggested_change`. End your report with the row count
   the script printed.
2. `report-<a|b>.md`:
   - **Coverage:** what you read, by file and line range, and what you did not read.
   - **Findings:** each row's id with one paragraph of reasoning.
   - **Tried, found nothing:** every item of your angle's list that produced no finding, with what you checked.
   - **Questions** the design leaves an implementer to guess.

Delete any scratch you made (the script included, once the table is written) and say so. Do not edit, stage or
commit anything, and do not run the tool or its tests.
