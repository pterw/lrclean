# LrClean guide

This is the manual. The [README](../README.md) is the short version: what the tool is, its promises and a quick
start. The promises are shown in [The rules in detail](#the-rules-in-detail). The checks under "Before a real run" are
done by hand and are not repeated here.

Contents: [Requirements](#requirements) · [Running](#running) · [Folders](#folders) ·
[Configuration](#configuration-workconfigjson) · [Commands](#commands) · [Exit codes](#exit-codes) ·
[The process, end to end](#the-process-end-to-end) · [Reject check verdicts](#reject-check-verdicts) ·
[The rules in detail](#the-rules-in-detail) · [Common situations](#common-situations) · [Limits](#limits) ·
[Tests](#tests)

## Requirements

- Windows (tested on Windows 11). Paths, long-path handling and link tests are Windows-specific.
- Python 3.13 or later. Standard library only: the tool has no dependency.
- Lightroom Classic catalogs (`.lrcat`, SQLite). The catalog schema is undocumented; the tool was built against
  catalogs of 2024 to 2026 and reads only a few tables.
- For the backup and offload phases: two external archive drives, on two different volumes.

## Running

Run it as `lrclean [--base <tool folder>] <command> ...` (`python -m lrclean ...` does the same in a Python that has
`lrclean` installed). `--base` is the folder that holds `work/` and `out/`; it is the current folder by default, so
run the tool from its folder or pass `--base`.

## Folders

| Folder | Tracked | Holds |
|---|---|---|
| `work/` | no | `config.json`, the work database `lrclean.sqlite`, catalog snapshots, journals, the staging folder |
| `out/` | no | Reports, manifests, verified lists, the reject check's outputs, stage and purge results |

`work/` and `out/` hold real file and folder names, so they are never committed. The tool's own folder is never
scanned, staged from or backed up. Make it outside the photo folders.

## Configuration: `work/config.json`

| Key | Meaning |
|---|---|
| `library_roots` | Folders on the system drive to inventory (the whole drive is fine). Add `C:\System Volume Information` to `exclude` and see `exclude` for other paths `inventory` names as unreadable. |
| `photo_root` | The folder you treat as the photo library; only files under it are backed up or offloaded |
| `catalog_roots` | Folders searched for `.lrcat` files (every connected drive is fine) |
| `backup_roots` | Folders on external drives that may already hold copies (read for the report) |
| `archive_roots` | The new top folder on each archive drive (at least two, on different volumes) |
| `live_catalog` | The path of your main catalog |
| `old_states` | Older states or backup copies of the live catalog: they neither reject nor keep, but their develop work and picks still keep a photo |
| `excluded_catalogs` | Catalogs whose records protect nothing (unreadable ones, or ones you judge irrelevant). `catalogs` still copies and reads them; only the verdicts ignore them. |
| `exclude` | Folders skipped during walks. An unreadable path makes its root's scan `partial`: add the unreadable subfolder (for a file, the folder that holds it) here and run `inventory` again. Excluding a root does not help; `inventory` says what to do instead. |
| `group_depth` | How many folder levels under `photo_root` make one group (a year folder) in the report |

A catalog's role is always given here, never guessed from its name or folder.

The first nine keys (all but `archive_roots`) are required by every command. `archive_roots` is read only by
`backup-plan`, `backup-run`, `backup-verify`, `rejects`, `copies`, `stage` and `purge`; add it before `backup-plan`.
The report (`catalogs`, `inventory`, `hash`, `report`) runs without it.

Every command checks the configuration before it runs and names the key on refusal (exit 2). Use absolute paths
without `.` or `..` segments or control characters. Write each backslash twice in JSON or use forward slashes.
Extended paths starting with `\\?\` require backslashes. Doubled separators are allowed only at the start of UNC and
extended paths. A single trailing separator and another letter case are fine.

Within `library_roots`, `backup_roots` or `catalog_roots`, keep only the outer folder of an overlapping pair. Library
and backup roots must be separate. A catalog root inside a library root is fine for a whole-drive layout. The tool
folder can sit inside a root. Place `library_roots`, `backup_roots`, `catalog_roots` and `photo_root` outside the tool folder.

The tool folder must be on the same drive as the files `stage` moves: a file on another drive is held
`other_volume` and stays where it is.

## Commands

Every command that changes something is a dry run unless given `--apply`.

| Phase | Command | Reads | Writes |
|---|---|---|---|
| Report | `catalogs` | Every `.lrcat` under `catalog_roots` (as copies) | Snapshots in `work/catalogs/`, rows in the work database |
| Report | `inventory` | The files under `library_roots` and `backup_roots` | Rows in the work database |
| Report | `hash` | Files that need a sample or full hash (resumable) | Rows in the work database |
| Report | `report` | The work database | `out/01_catalogs.csv` to `out/11_not_read.csv` and `out/summary.md` |
| Report | `verify <folder>` | The raw files of one folder (any spelling of it) and their copies on the backup roots, in full | Rows in the work database |
| Backup | `backup-plan` | The tree under `photo_root` | `out/backup-manifest-<n>.csv` per archive root and a summary |
| Backup | `backup-run <manifest> [--apply]` | The manifest you approved | Copies on the archive drive, a journal |
| Backup | `backup-verify <manifest>` | Every source and copy, in full | `out/backup-verified-<n>.csv` |
| Reject check | `rejects` | Fresh catalog snapshots, the verified backup lists | `out/rejects.csv`, `out/reject-removal-list.csv`, `out/rejects-summary.md` |
| Unrecorded copies | `copies` | Fresh catalog snapshots, the latest file scans and hashes, the verified backup lists, `out/purge-*.csv` | `out/copy-removal-list.csv`, `out/copies-summary.md` |
| Offload | `stage <list> --approved-sha256 <hex> [--only <source>]... [--apply]` | Either removal list (`out/reject-removal-list.csv` or `out/copy-removal-list.csv`) | Renames into `work/staging/<batch>/`, `work/journal/mover-<batch>.jsonl`, `out/stage-<batch>.csv` |
| Offload | `undo <batch> [--apply]` | The batch journal | Renames back, journal lines |
| Offload | `purge <batch> [--approved-sha256 <hex>] [--apply]` | The batch journal | Deletions from staging, journal lines, `out/purge-<batch>.csv` |

`backup-run`, `stage` and `purge` take `--stall-seconds <n>` (default 120): a read or write that makes no progress
for that long ends the run with exit 5.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Done (or nothing to do) |
| 2 | Configuration, list, journal or approval refused; nothing was changed |
| 3 | Lightroom is running, the list of processes cannot be read, or a lock file lies beside a catalog that is read |
| 4 | Evidence is stale or incomplete: the message says what to run again (`catalogs`, `inventory`, `rejects`, `copies`) |
| 5 | A drive stalled; `stage` and `purge` print the batch and the journal that says where the run stopped (on stderr), `backup-run` the row it stopped on |
| 6 | Finished, but at least one row was held or refused (each named with its reason), or a file-system or work database error stopped a command (the message names the file when the system reports one) |

## The process, end to end

This is the order the tool was used in. Lightroom is closed for every step unless a step says otherwise. A pass's
duration depends on the drives (their type, their connection, one archive drive or two) and on how much is read
again; see [Limits](#limits) for one example.

1. **Report.** `catalogs`, `inventory`, `hash`, `report`. Read `out/summary.md` first: it opens with what could and
   could not be read. Nothing outside the tool folder is changed by this phase. `hash`, `verify` and the report's
   verdicts skip file rows from partial scans. `verify` prints `mismatch` once for each backup copy that differs
   from its file, so a file with an exact and a corrupted copy counts in `verified` and in `mismatch`.
2. **Backup.** `backup-plan`, then read each manifest and approve it by reading it: `backup-run` refuses a manifest
   that breaks its rules but takes no SHA-256 of it. `backup-run <manifest>` as a dry run, then with `--apply`;
   then `backup-verify <manifest>`. A rerun copies only what is missing. Planning needs at least one complete
   library scan and walks `photo_root` directly. `backup-plan` refuses if any archive drive is missing.
   `backup-run` refuses if its manifest's drive is missing.
3. **Cull.** In Lightroom, flag the photos you want gone as rejected, in the live catalog or in any shoot catalog.
   Then close Lightroom. Do not use "Delete Rejected Photos" and do not move or rename files yourself.
4. **Reject check.** `catalogs`, then `rejects`. Read `out/rejects-summary.md`: the count per verdict, what each
   catalog rejected, and, as its last line, the SHA-256 of `out/reject-removal-list.csv`. `out/rejects.csv` gives
   every reject with its verdict and the evidence (`how_decided`). `rejects` selects rejects from the catalogs
   and uses hashes only from complete library scans when looking for twins. For a sidecar named after the full
   photo name (`IMG_0001.ARW.xmp`), run `inventory` before `rejects` and check that the library scan is complete:
   `rejects` finds such a sidecar, which no catalog records, only in the latest complete files scan. With a missing
   or partial scan, or a sidecar added since the scan, an unrecorded sidecar can be left out of the list.
5. **Pilot.** Pick a few rows (one with a sidecar, one with a twin if any). `stage <list> --approved-sha256 <hex>
   --only <source> ...` as a dry run, then with `--apply`. Open Lightroom: those photos show as missing. Close it,
   `undo <batch> --apply`, and check that nothing else changed. Opening Lightroom changes the catalogs, so run
   `catalogs` and `rejects` again before the next `stage`.
6. **Stage the list.** `stage` the whole list (dry run, then `--apply`). Files that fail a check are held, stay
   where they are, and are named with the reason.
7. **Remove from the catalogs.** In Lightroom, select the staged photos (they show as missing) and remove them from
   the catalog (Photo > Remove Photos, "Remove"). Close Lightroom.
8. **Purge.** `catalogs` again (your removal changed the catalog), then `purge <batch>` as a dry run: it lists every
   file it would delete, the space it frees and the purge set's SHA-256. Approve that SHA-256 and run
   `purge <batch> --approved-sha256 <hex> --apply`. After this, the two archive copies are the only copies.
9. **Unrecorded copies.** `catalogs`, `inventory` and `hash` (a fresh `inventory` makes `hash` read every file
   again, which takes hours on a large library), then `copies`. Read `out/copies-summary.md`: the files examined
   and held per reason, the copies per folder, and, as its last line, the SHA-256 of `out/copy-removal-list.csv`.
   Then steps 5 to 8 with that list. Do not edit `work/config.json` between `copies` and the last `stage` of its
   list; after an edit, run `copies` again.

If a run is interrupted, run the same command again: the journal records every step before it happens, and
`undo` and `purge` place an unfinished step by looking at the disk. `purge` never deletes a file whose `stage` or
`restore` step did not finish and that is still in staging: it holds the row (exit 6) and says that `undo` places it.

## Reject check verdicts

| Verdict | Meaning |
|---|---|
| `on_list` | Passes every check: on the removal list |
| `kept_in_live` | Rejected in a shoot catalog, but the live catalog records it without rejecting it |
| `kept` | Another catalog records it without rejecting it |
| `kept_for_work` | Some catalog holds develop work or a pick for it |
| `missing` | Rejected, but the file is no longer on disk |
| `placeholder` | An online-only file: never opened, never moved |
| `outside_backup` | Not inside the backed-up folders |
| `changed_since_backup` | Changed after its backup copy was verified |
| `no_archive_copy` | No verified copy on one of the archive drives |
| `coverage_incomplete` | Some catalog could not be read, so nobody can say no catalog keeps it |

## The rules in detail

These rules are enforced by the code, and each has a test that names it. The README lists the promises; this is
the full text.

- **Catalogs are never written.** A catalog is copied, with its `-wal` and `-shm` files, into `work/catalogs/` and
  read there. Removing a photo from a catalog is always done by you, in Lightroom.
- **Nothing moves without your approval of the exact list.** A removal list and a purge set are approved by their
  SHA-256, and the command refuses any other file. A backup manifest is approved by reading it: `backup-run`
  refuses a manifest that breaks its rules but takes no SHA-256 of it.
- **Backups come first.** A file leaves the drive only when a verified copy of it exists on each of two archive
  drives (verified by full SHA-256, not by size or name). The tool refuses two archive roots on one volume, and at
  `stage` and `purge` it refuses a file whose archive copy is on the same volume (by volume serial) as the file or as
  the other copy. That the two archive drives are separate physical drives is yours to check.
- **A reject leaves only when every check passes:**
  - every catalog that records it has rejected it (the copies of the live catalog excepted);
  - no catalog holds develop work or a pick for it, with one narrow exception: a byte-identical copy whose
    develop settings equal those of a twin that stays;
  - it exists, is not an online-only placeholder, and lies inside the backup;
  - its sidecars pass the same tests, because a raw and its sidecars leave together or not at all (a sidecar is
    `IMG_0001.xmp`, `IMG_0001.ARW.xmp` or the same names with `.acr`; `rejects` finds the whole-name ones in the
    files scan; `IMG_0001.ARW.xmp` also belongs to a photo named `IMG_0001.ARW.ARW`, and stays when either photo
    is kept);
  - every catalog that could record it was read.
- **A copy leaves only when its twin stays.** A file on the copy list is recorded by no live or shoot catalog, has
  no sidecar, no pick and no develop work in any catalog read, and is byte-identical to a file the live catalog
  records (not rejected) at its own path, or to a reject that the reject check's rules let leave. `stage` and
  `purge` hash the copy, both of its archive copies and, where the row names one, its twin again.
- **Two steps, the first reversible.** `stage` renames files into `work/staging/<batch>/` on the same drive and
  writes a journal line before every step. `undo` renames them back. `purge` hashes every file and both of its
  archive copies again, checks them once more right before it deletes, and deletes only the set you approved.
- **It stops rather than guesses.** A running Lightroom, a catalog lock, a changed catalog, an incomplete scan, a
  stalled drive or a damaged journal stops the command with a distinct exit code, before it changes anything.
- **Placeholders are never opened.** Online-only files of a cloud sync client are listed and left alone. Keep the
  sync client off while the tool works.

## Common situations

- **Exit 3, "a .lock lies beside ..."**: a catalog was not closed cleanly. The message names every such catalog.
  Open each in Lightroom and quit; the lock goes away. The tool never removes a lock. A catalog that cannot be
  opened can be added to `excluded_catalogs`, after checking that it records nothing you care about. Opening
  catalogs changes them, so run `catalogs` and `rejects` again afterwards.
- **A catalog that cannot be read**: `catalogs` prints `unreadable: <catalog>: <reason>` for each one it could not
  read in that run; the reason is also in the Coverage section of `out/summary.md` and in `out/01_catalogs.csv`. It
  limits coverage until it can be read or you add it to `excluded_catalogs`.
- **Exit 4 from `purge` right after you removed the staged photos in Lightroom**: expected. Run `catalogs` (not
  `rejects`), then `purge` again.
- **Exit 4, "run catalogs again"**: a catalog changed since its snapshot (you opened Lightroom). Run `catalogs`,
  and `rejects` if the message asks.
- **A row held at `stage` or `purge`**: the file, a sidecar, an archive copy or the twin changed, is gone or could
  not be read (`read_error`: another program holds it, a bad sector, a drive removed during the read). The row stays
  where it is and the other rows go on; read the named file and reason.
- **A photo you expected on the list is kept**: `out/rejects.csv` says which catalog keeps it and why. Develop
  settings changed in Lightroom (even by opening a photo in Develop) can end a twin's match.

## Limits

- **Slow on purpose.** Each file is read three times at `stage` (system drive and both archive drives) and again at
  `purge`, one file at a time. How long a pass takes depends on the drives (their type, their connection, one
  archive drive or two) and on how much is read again. One real run, as an example and not as the tool's speed:
  about 143 GB staged in 75 minutes and purged in 76 on two USB hard drives.
- **`purge` cannot be undone.** Only the two archive copies remain. Restoring from them is done by hand.
- **Sync clients.** A file moved out of a synced folder is deleted from the cloud copy when the sync client next
  runs. Keep it off from `stage` until `purge` has finished, and decide before you turn it back
  on.
- **A sidecar whose name holds `;`** keeps its photo from leaving through a removal list: the list joins sidecars
  with `;`, so the row is held.
- **A catalog whose file name is 248 UTF-16 units or longer** may not be readable on Windows: SQLite could not open
  its snapshot copy in one test (SQLite 3.49). The snapshot folder fits, but the catalog is reported as `unreadable`
  with SQLite's reason and limits coverage, so `stage` and `purge` can refuse with exit 4.
- **A destination name of 243 UTF-16 code units or more.** `backup-plan` can list it, but `backup-run` refuses
  the manifest and names the file (exit 2). The part suffix adds 13 units to the destination name, and Windows
  allows 255 units per component. A character outside the Basic Multilingual Plane, such as an emoji, takes two
  units. Rename the file in the library and plan again.
- **A read-only photo** stops `purge` with exit 6 after its sidecars are deleted (their archive copies were checked
  just before). Clear the read-only flag, run `purge` as a dry run again and approve the new SHA-256: the old one is
  refused (exit 2) because the sidecars are gone. A read-only sidecar stops `purge` at that sidecar, as sidecars go
  first.
- **Time of check.** A change made in the moment between the last check and a rename or a delete is not seen.
- **Windows only.** See [Requirements](#requirements).
- **Lightroom's catalog format is private.** A future version could change the tables the tool reads. What the
  tool cannot read it reports as unreadable or unknown, and an unknown develop state keeps the photo.

The full list, and every rule the code holds, is in the design: [`docs/process/specs/2026-11-11-lr-cleanup-design.md`](process/specs/2026-11-11-lr-cleanup-design.md)
(sections "Known limits" and "Rules the code must hold").

## Tests

```
python -m unittest discover -s tests
```

The tests run on synthetic photo trees and synthetic `.lrcat` files built in temporary folders; none touches a real
folder, and the run prints how many there are. The files in `tests/mutants/` list planted defects in the decision
code; they are run by a separate mutation runner that is not part of this repository, and CI does not run it.
