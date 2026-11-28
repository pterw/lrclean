## What and why

<!-- What this pull request changes, and the problem it solves. Link the issue it closes: "Closes #N". -->

## Lane

<!-- Pick one; a change takes the lane of its riskiest line. -->

- [ ] Light: docs, comments, wording, CI cosmetics (nothing that states what the tool does to files)
- [ ] Standard: docs that state a safety promise or a command's behaviour; code that decides no move, removal,
      backup verdict or catalog read
- [ ] Strict: code that decides a move, a removal, a backup verdict or a catalog read; the journal; the
      planted-defect files

## Tests

<!-- The command you ran and its result, e.g. "python -m unittest discover -s tests: Ran 1065 tests, OK". -->

## Checklist

- [ ] Each commit is one logical change with its tests and docs, and passes the whole suite on its own.
- [ ] Commit subjects are `type(lrclean): Subject` (`feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`).
- [ ] No line of a commit message is over 72 characters, and no commit carries an attribution trailer.
- [ ] A changed behaviour updates the design rule in `docs/process/specs/2026-11-11-lr-cleanup-design.md` or
      `docs/guide.md`, and the tests, in the same change.
- [ ] A changed line named by an `old` in `tests/mutants/*.json` updates that entry.
- [ ] `CHANGELOG.md` has a line under `## Unreleased` for a change a user would notice.
- [ ] Nothing here (code, tests, docs, messages, this description) holds a real file, folder, catalog, drive or
      client name. Tests use synthetic names such as `shoot-a` and `IMG_0001.ARW`.
