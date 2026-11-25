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
- **Tests run on synthetic trees only.** Build each tree in a temporary directory, with the helpers in
  `tests/synth.py` and `tests/mover_world.py` and plainly synthetic names such as `shoot-a` and `IMG_0001.ARW`.

## Conventions

- Python 3.13, standard library only at run time, `unittest`, LF line endings.
- Commit subjects are conventional: `type(scope): Subject`, with the scope `lrclean`.
- A commit message names no role; it says what the change does.
