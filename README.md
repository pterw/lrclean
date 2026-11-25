<p align="center"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/lrclean-logo-dark.svg">
  <img src="docs/assets/lrclean-logo-light.svg" alt="LrClean" width="420">
</picture></p>

<p align="center"><strong>Free a full photo drive without losing a photo or breaking a Lightroom Classic catalog.</strong></p>

<p align="center">
<a href="https://github.com/pterw/lrclean/actions/workflows/ci.yml"><img src="https://github.com/pterw/lrclean/actions/workflows/ci.yml/badge.svg" alt="CI"></a> <a href="https://github.com/pterw/lrclean/releases/latest"><img src="https://img.shields.io/github/v/release/pterw/lrclean" alt="Latest release"></a> <img src="https://img.shields.io/badge/python-3.13-blue" alt="Python 3.13">
<img src="https://img.shields.io/badge/platform-Windows-blue" alt="Windows"> <img src="https://img.shields.io/badge/dependencies-none-brightgreen" alt="No dependencies"> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"></a>
</p>

<p align="center">
<a href="#what-it-is">What it is</a> · <a href="#how-it-keeps-your-photos-safe">Safety</a> · <a href="#quick-start">Quick start</a> · <a href="#the-phases">Phases</a> · <a href="#before-a-real-run">Before a real run</a> · <a href="#tested-with">Tested with</a> · <a href="#limits-and-the-rest">Limits</a> · <a href="docs/guide.md">Guide</a>
</p>

## What it is

`lrclean` is a command-line tool that finds the files on a full drive that one large Lightroom Classic catalog, and
many small per-shoot catalogs, point at. It reads copies of the catalogs, never the originals. It copies every photo
folder to two archive drives and verifies each copy byte for byte. It decides, file by file and with the evidence
written down, which rejects and which byte-identical unrecorded copies can leave the disk. It then moves them off in
two steps, the first reversible. Lightroom's own duplicate finder only sees photos in the open catalog; nothing in
Lightroom compares the files on disk with several catalogs and with the backups at once, and that is the gap this
tool fills.

**Who it is for:** a photographer with a backlog of raw files and JPEGs, many catalogs, duplicates across them, and a
full drive.

## How it keeps your photos safe

- **Catalogs are never written.** They are copied and read there; removing a photo from a catalog is done by you,
  in Lightroom.
- **Nothing leaves the drive without a verified copy on each of two archive volumes** (separate drive letters and
  volume serials; that they are separate physical drives is yours to check, see
  [Before a real run](#before-a-real-run)).
- **Every removal is from a list approved by its SHA-256.**
- **Every removal is staged first, and `undo` works until the `purge`.**
- **Online-only placeholders are never opened.**

The rules in full are in [the guide](docs/guide.md#the-rules-in-detail).

## Quick start

Windows, Python 3.13.

```
pipx install lrclean
```

Make a tool folder outside your photo folders, make a `work` folder inside it, and write `work/config.json` there.
These are the nine required keys, with made-up values (`archive_roots`, two folders on two archive drives, is added
before the backup phase):

```json
{
  "library_roots": ["C:\\"],
  "photo_root": "C:\\Photos",
  "catalog_roots": ["C:\\Photos"],
  "backup_roots": ["E:\\Old-backup"],
  "live_catalog": "C:\\Photos\\Main\\Main.lrcat",
  "old_states": [],
  "excluded_catalogs": [],
  "exclude": [],
  "group_depth": 1
}
```

`--base` defaults to the current folder, so run from the tool folder or put `--base <tool folder>` before the
command: `lrclean --base <tool folder> report`. Close Lightroom, then run the read-only report. It writes only inside
the tool folder (`work/` and `out/`):

```
lrclean catalogs
lrclean inventory
lrclean hash
lrclean report
```

Read `out/summary.md` first. It gives counts per section; the files themselves are listed in the numbered CSV files
beside it (for example `08_missing.csv`). Every key and command is in
[the guide](docs/guide.md#configuration-workconfigjson).

## The phases

Each is its own phase; the step-by-step is in [the guide](docs/guide.md#the-process-end-to-end).

1. **Report.** Reads every catalog it can find (from copies), walks the photo drive, hashes files and reports what each file is.
2. **Backup.** Copies every photo and video folder to two archive drives from a list you approve, and verifies every copy.
3. **Reject check.** After you cull in Lightroom, decides which rejects can safely leave and writes a removal list.
4. **Unrecorded copies.** Lists byte-identical copies that no catalog records: a second removal list, in the same format.
5. **Offload.** `stage` (a reversible move into a staging folder, with `undo`), then `purge` (a permanent delete, after everything is checked again).

## Before a real run

These checks are made by hand today:

- Lightroom is closed, and the sync client is stopped.
- The archive drives are identified by label and serial, and are two separate physical drives.
- `catalogs`, `inventory`, `hash` and `backup-verify` were run again just before this run, with both archive drives
  connected.
- `catalog_roots` is compared with the drives and folders you use.
- No junction or substituted drive points into `photo_root` from a path a catalog root uses.
- The list's configuration is not edited between `copies` and the last `stage`.
- The set of paths the main catalog records that are missing is taken before `stage` and after each
  `stage --apply`, and is unchanged: a newly missing path means you stop and look
  before going on.

## Tested with

Windows 11 and Python 3.13. The catalog format numbers the work database has recorded so far are six: 0600008,
0700003, 1303001, 1400000, 1500000 and 1504001 (an example from one library). Older formats are untested, not known
to fail.

## Working with an AI assistant and the Lightroom plug-in

[`AGENTS.md`](AGENTS.md) holds the rules for an AI assistant that works on the code.

On the first library, a third-party Lightroom plug-in (an MCP server; MIT) was used for work that is otherwise done
by hand, photo by photo: flagging duplicates as rejects only after every catalog had been checked for develop work
and picks; giving both copies of a duplicate the union of their keywords; and removing the records of purged files
from the per-shoot catalogs by id, each write proved by counts before and after.

- `lrclean` never needs the plug-in and never talks to it.
- When it is used, previews and metadata go to the model: pin a version that has been read, and keep removals behind
  a confirmation.

## Limits and the rest

- `purge` cannot be undone: only the two archive copies remain.
- A sync client deletes the cloud copy of a file that is moved out of a synced folder.
- Windows only.
- Lightroom's catalog format is private; a future version could change the tables the tool reads.

How long a pass takes depends on the drives and on how much is read again; the guide's
[Limits](docs/guide.md#limits) gives one example. Roadmap: the [0.2 milestone](https://github.com/pterw/lrclean/milestones).
How it was built: [`docs/process/README.md`](docs/process/README.md). Licence: [MIT](LICENSE).
`lrclean` is an independent project, not made or endorsed by Adobe.
