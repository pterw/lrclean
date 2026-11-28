# Changelog

## Unreleased

- `stage` and `purge` refuse a linked tool folder, and `backup-plan` a missing archive drive, before the work
  database is opened, so nothing is created where a link leads; `undo` checks the link in `main` too (#25); a linked
  `work/catalogs` that cannot be listed is named as a link (R1).
- `backup-run`: a destination that became an online-only placeholder after it was verified is `dest_placeholder` in
  the dry run (exit 0, as for every dry run) and with `--apply` (exit 6), no longer `already_verified` or `verified`
  (#24).
- Section 5 of the report no longer counts a backup copy that became an online-only placeholder as copied, and
  `verify` no longer selects it from an old sample; its stored hash is removed when `hash` finds it (#24).
- `verify` counts each corrupted copy as a mismatch, also when another copy of the file is exact, and section 5 of
  `summary.md` totals the library's raws and the backup-only raws apart (#28).
- `purge` holds a row whose only files are still in staging under a `stage` or `restore` step that never finished
  (`family_incomplete`, exit 6) instead of dropping it with exit 0 (#33).
- `backup-run` refuses a manifest whose destination file name is 243 or more UTF-16 units, naming the file,
  instead of failing that row at every copy (#18).
- `backup-run` refuses a manifest whose destination lies below a junction or link inside an archive root that leads
  into a library root or out of the archive root, instead of writing the copy there (rule 17).

## 0.1.1 - 2026-11-27

Messages, refusals and exit codes. No verdict about which file may leave the drive changes.

- Output: the tool writes UTF-8, so a path outside the Windows code page is printed instead of stopping the command,
  also when the output is redirected to a file (#16).
- Exit codes: every situation the guide lists ends with its code and one line, never a traceback: an archive drive
  that is not connected (2), a process list that cannot be read (3), a file of the tool folder or a work database that
  cannot be written, opened or is locked (6).
- `backup-plan` refuses when any archive drive is missing; `backup-run` only when the drive of its own manifest is,
  so the archive drives can be backed up one at a time.
- Configuration: a path that would be read wrongly is refused with exit 2 and its key named: a relative path, a `.`
  or `..` segment, a control character (a single backslash in JSON), two separators in a row, a `\\?\` path with
  forward slashes, two roots of one key that overlap, a library root and a backup root that overlap, and a
  `photo_root` or any root inside the tool folder.
- Catalogs: `catalogs` names each catalog it could not read and why, and the reason is stored and shown in the report
  (#10). A catalog missing when it is copied is stored unreadable instead of empty (#34), and a `-wal` file that
  cannot be read marks only its own catalog (#35).
- Partial roots: `inventory`, `verify` and `backup-plan` name each path that could not be read and say how to cure it,
  and say when a scan did not finish. `verify <folder>` takes any spelling of the folder and names the partial root
  under or above it.
- `copies` says to run `catalogs` again, or to add an unreadable catalog to `excluded_catalogs`, when catalog coverage
  is incomplete.
- Tests: CI runs the tests verbosely and the relative-path cases run on any drive (#13).

## 0.1.0 - 2026-11-25

First release.

- Five phases: inventory and report, backup, reject check, unrecorded copies, and offload.
- Windows only.
- Tested on Lightroom Classic catalogs of six format numbers: 0600008, 0700003, 1303001, 1400000, 1500000 and 1504001.
- No dependency: the Python 3.13 standard library only.
