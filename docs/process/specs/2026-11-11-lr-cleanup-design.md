# Lightroom library cleanup: design

Approved by the owner on 2026-11-11. This file is tracked, so it carries no measurement, no folder or catalog
name from the real library and no drive inventory. Those are in `out/probe-2026-11-11.md`, which is never
tracked. The rules and rulings (H1 to H8, R1 to R18) are in `CLAUDE.md`.

## Purpose

A photographer's system drive is nearly full. Most of it is raw files and JPEGs used by one large Lightroom
Classic catalog and many small per-shoot catalogs, with copies of some shoots on external drives. The owner wants
space back, a tidy home for every catalog, and no broken Lightroom path.

A probe showed that almost every raw file on the system drive is recorded by a catalog. So "delete the
duplicates nobody uses" frees little. The useful product is a report that says, with evidence, what each file is
to the catalogs and to the backups, so the owner can choose what leaves the drive.

## What Lightroom Classic already does (research, 2026-11-11)

- Since 15.4 it has a duplicate finder. It sees only photos in the open catalog: right for "both copies are
  catalogued", useless for a file no catalog knows.
- "Find Missing Folder" relinks at the highest missing parent; "Synchronize Folder" and "Find All Missing Photos"
  are its spot checks. These are the supported way to repair a path.
- A catalog moves as a whole folder, with Lightroom closed: `.lrcat`, `.lrcat-data`, `-wal`, `-shm`,
  `Previews.lrdata`, `Smart Previews.lrdata`, `Helper.lrdata`, `Sync.lrdata`. `Mobile Downloads.lrdata` holds
  downloaded originals and stays where it is. (Expert source, not Adobe.)
- How Lightroom chooses between a root folder's `absolutePath` and its `relativePathFromCatalog` is not
  documented anywhere we found. So both count as recorded, and a catalog move is piloted on a copy first.
- No existing project compares files on disk with several catalogs. A general duplicate finder stays useful as an
  independent cross-check.

## Phases

| Phase | What | Writes to photos or catalogs | Opens on |
|---|---|---|---|
| A | Inventory and report | none | approved |
| B1 | Backup copy of the photo folders to each archive root | copies only, from an approved manifest | planning approved 2026-11-12; first backup, of the folders that hold a raw, verified 2026-11-18; the second, of every photo or video folder, built 2026-11-19 (98562d2), verified on both roots 2026-11-19 |
| Reject check | The owner's rejects in the live and shoot catalogs, with the evidence for each and a removal list | none | shape approved 2026-11-18 (R14); built; rule 26a (R22) built 2026-11-19 (2208da1); the symmetric hold of a shared sidecar (rule 29) 2026-11-20 (a26ed91) |
| B2 | Stage the reject check's approved removal list, verify against both archive copies, purge from the system drive | from approved lists | design and plan 2026-11-19; built 2026-11-20 on the owner's word (4acace8); run on the library 2026-11-20: a ten-row pilot staged, undone, staged and purged, then the whole approved list staged and purged |
| C | Catalog tidy, pilot first | yes | the owner's word |
| D1 | Unrecorded copies: byte-identical copies that no live or shoot catalog records, removed by B2's mover | yes | shape approved 2026-11-23 (R24 to R28); design and plan first; nothing built or run |
| D | Loose photos and the larger levers the report sizes | yes | the owner's word |

Only a move off the system drive frees space. Staging on the same drive, the catalog tidy and the consolidation
free none. Phases A and B1, the reject check, B2 and D1 are specified below; C and D are stated briefly and
detailed when their gate opens.

## Phase A: the report tool

Python 3.13, standard library only, `unittest`. Package `lrdup`, run as `python -m lrdup <subcommand>`.

### Modules

| File | Does | Depends on |
|---|---|---|
| `lrdup/store.py` | The work database `work/lrdup.sqlite` and its schema | none |
| `tests/synth.py` | Builds a synthetic photo tree and synthetic `.lrcat` files | none |
| `lrdup/catalogs.py` | Finds `.lrcat` files, snapshots each, reads the snapshot, stores status and records | store |
| `lrdup/inventory.py` | Walks roots under a scan record and stores one row per file of interest | store |
| `lrdup/hashing.py` | Sample and full hashes, resumable, never opens a placeholder | store |
| `lrdup/classify.py` | Joins records, files and hashes into the report's classes | store |
| `lrdup/report.py` | One CSV per section and `summary.md`; every row carries `how_decided` | classify |
| `lrdup/cli.py`, `lrdup/__main__.py` | Subcommands: `catalogs`, `inventory`, `hash`, `report`, `verify` (phase A); `backup-plan`, `backup-run`, `backup-verify` (phase B1); `rejects` (the reject check) | all |

### Run configuration

Real paths are private, so they live in an untracked JSON file, `work/config.json`, read by every subcommand:

- `library_roots`: folders on the system drive to walk (the real run gives the whole drive).
- `backup_roots`: folders on external drives.
- `catalog_roots`: folders searched for `.lrcat` (the real run gives every connected drive).
- `photo_root`: the folder the owner treats as the photo library; files outside it are "loose".
- `group_depth`: how many folder levels under `photo_root` make one group (a year folder) in section 5.
- `exclude`: folders never walked (the real run names the operating system folder; the report says so).
- `live_catalog`, `old_states`: which catalog is live and which are older states or backup copies of it; the latter
  neither reject nor keep in the reject check (R13), except for develop work and picks (R18, rule 26). A role is
  given here, never guessed from a name or a folder.
- `excluded_catalogs`: catalogs the owner has excluded from protection, by path. The list is read from the
  configuration every time a verdict is made; it is never stored with a scan.

The tool's own folder is always excluded, whatever the configuration says. Verdicts (sections 3, 4, 6, 7, 9 and
10) are about files on library roots only; files on backup roots appear only in sections 2, 5 and 11.

### Frozen schema

These tables cross tasks. A task that needs one changed returns NEEDS_CONTEXT.

- `scan(id, root, kind, started, finished, status)`. `kind` is `files` or `catalogs`. `status` is `running`,
  `complete`, `partial` or `not_connected`.
- `catalog(id, scan_id, source_path, snapshot_path, role, status, size, mtime_ns, db_version, wal_bytes,
  has_lock, reason)`. `role` is `live`, `old_state` or `shoot`. `status` is `ok`, `empty`, `partial` or
  `unreadable`. `reason` is one plain line saying why for an `unreadable` catalog, and empty otherwise (0.1.1).
- `catalog_root(catalog_id, path_norm, path_orig, via)`: each root folder a catalog records, by its absolute
  path and by its catalog-relative path.
- `record(catalog_id, path_norm, path_orig, via, ext, capture_time, is_sidecar)`. `via` is `absolute` or
  `relative`.
- `file(id, scan_id, volume, path_norm, path_orig, ext, size, mtime_ns, attrs, placeholder, container, file_index,
  nlink, checked_at)`.
- `hash(file_id, file_index, size, mtime_ns, sample, full, hashed_at)`.
- `image(catalog_id, path_norm, via, pick, developed, settings)`: added for the reject check (see there);
  `settings` added for R22 (rule 26a).

### Rules the code must hold

Each rule gets one test that names it.

1. **A verdict uses only the latest complete scan of each root.** A root whose latest scan is `partial` or
   `not_connected`, and a configured root that was never scanned, is reported as such and contributes no file to
   any verdict. A drive that is off is "not connected", never empty. The inventory needs the catalogs' records to
   type containers, so `inventory` refuses to run before `catalogs`, and `report` refuses when a catalogs scan is
   newer than the inventory of a library root. `report` also refuses when the latest files scans of the
   configured roots, read in the order `inventory` walks them (library roots, then backup roots), are not from
   one inventory run: an interrupted run leaves the roots it did not reach with the scan of an earlier run.
2. **Path of a record.** `absolutePath + pathFromRoot + baseName + "." + extension`, from `AgLibraryFile` joined
   to `AgLibraryFolder` and `AgLibraryRootFolder`. A second path is built from the folder of the catalog's
   original location plus `relativePathFromCatalog`, with `..` collapsed. Both are stored (`via`) and both count as recorded. Each
   entry of `sidecarExtensions` (a comma-separated list such as `JPG`, `xmp`, `jpg,xmp`, `xmp,acr`) yields a
   sidecar record. The capture time comes from `Adobe_images.captureTime` of the file's master image.
3. **Recorded** means equality of normalised paths (case folded, one slash direction, Unicode NFC) with a record
   of any catalog that is `ok` or `partial` and not excluded by the owner, older states included. Never by a
   folder or file name.
4. **Long paths.** A path over 260 characters is read with the `\\?\` prefix, never skipped silently. Every
   `stat`, open and listing of a stored path goes through that one helper.
5. **Catalog coverage.** Reference status is unknown, not "unreferenced", while any of these holds: a catalog is
   `unreadable` or `partial` and the owner has not excluded it by path; a configured catalog root was never
   scanned, is not connected, or had a folder that could not be listed; a catalog's source has changed since its
   snapshot; a catalog is found in a catalog root when the report is written that the catalogs scan did not
   store, or a folder of a catalog root cannot be listed then. While one holds, no file is a safe candidate: the same rows are reported in section 4 as "not
   recorded by the catalogs that could be read; coverage incomplete", with the cause named. An `empty` catalog
   (no tables) does not count against coverage. A `partial` catalog still adds the records it yielded.
6. **Catalog snapshots.** A catalog is copied with its `-wal` and `-shm` into its own folder under
   `work/catalogs/`, named from the source path so that catalogs sharing a file name never collide. The
   folder's name is the catalog's name without its extension, a dash and 12 hex digits; when that would exceed
   255 UTF-16 code units the name part is cut, so the folder always fits. The copy is refused while a Lightroom
   process is running, and while the list of processes cannot be read (exit `3`; the message says the list could not
   be read, not that Lightroom runs); a stale `.lock` file is not that signal and is only reported.
   The copy is opened without `immutable`, so committed write-ahead content is read. The original is opened by
   nothing but the copy. In a scan, the Lightroom check comes after the catalog's own attributes are read and
   before its `-wal` file is read. The `catalogs` command stops with exit `2`, naming the folder, when `work/`,
   `work/catalogs/` or a folder directly inside `work/catalogs/` is a link or junction, or any other folder the
   mover commands already refuse, before any catalog is touched. It stops the same way when `work/catalogs/`
   exists and cannot be listed, since its folders cannot then be checked; a file in its place is not that
   case, and each catalog is then `unreadable`. A snapshot whose folder, with links resolved,
   is or lies inside the catalog's own folder is refused as `unreadable` (rule 7). Before either refusal nothing
   is removed, created or copied by the `catalogs` command; the work database, which `main` opens before
   `catalogs` runs (`stage` and `purge` refuse a link before they open it, rule 38), is opened (and created, if new) where `work/` leads, so with `work/` itself a link it is opened inside that
   folder. A catalog whose `.lrcat`, `-wal` or `-shm` file is an online-only placeholder, or has
   attributes that cannot be read, is not copied: it is stored as `unreadable` (rule 7). A `-wal`, `-shm` or
   `.lock` name over 255 units cannot exist beside its catalog and is read as absent; its stat failing in any
   other case is handled as before. Known limit: on Windows, a snapshot copy of a catalog whose file name is 248
   UTF-16 units or longer failed to open with SQLite 3.49 (a `disk I/O error`; the threshold can differ by
   version). The snapshot folder fits, but the catalog is stored `unreadable` with SQLite's reason and limits
   coverage (rule 5). A snapshot is stale when
   its source's size or time has changed, when the size of its `-wal` file differs from the size recorded just
   before the copy, or when a `.lock` file has appeared since; this is checked again when the report is written,
   and a stale snapshot limits coverage (rule 5). Known limit: a `-wal` file rewritten at the same size, with the
   catalog file and the lock unchanged, is not seen.
7. **Placeholders.** A file with attribute `0x1000`, `0x40000` or `0x400000` is listed and never opened.
8. **Identical** means same size and same full SHA-256. A sample hash (size, first 64 KB, last 64 KB) only
   filters. Same size alone means nothing: uncompressed raws from one camera share a size.
9. **Hash rows are an optimisation.** A row is stale when the file's index, size or time no longer match. A file
   found to be an online-only placeholder when it is hashed again loses its row, so a backup copy that became a
   placeholder no longer counts as copied. Nothing in phase A treats a stored hash as proof for a later move. On a library root every photo file is sampled, and
   files that collide with another library file on size and sample are hashed in full. On a backup root a file is
   sampled only when its size equals that of some library photo file, and is hashed in full only by `verify`.
10. **Same file, two names.** Two paths with the same volume and file index are one file. They are reported as
    such and never as duplicates. `file_index` and `nlink` come from `os.stat`, because `os.DirEntry.stat`
    returns zero for both on Windows.
11. **`.lrdata` containers** are entered like any folder and typed by evidence. `Mobile Downloads.lrdata`, and any
    container in which a catalog records a file, is photo storage: its photos are inventoried. A folder named
    `<catalog> Previews.lrdata` or `<catalog> Smart Previews.lrdata` in which no catalog records a file is a
    preview cache: its size is summed and its contents are not photos. Anything else is an unknown container:
    sized, its photos inventoried, never called cache.
12. **RAW+JPEG pair.** A JPEG in the same folder as a raw with the same base name. Lightroom's own sidecar record
    is shown when present. A pair whose two files were written within two seconds of each other is counted as
    written together, a sign of a camera JPEG; a pair with other times may be an export. A pair never makes the
    raw a candidate; the raw is the keeper.
13. **Fresh at report time.** Every file in an actionable row, and its keeper twin, is read again with `os.stat`
    when the report is written. A row whose file is gone, changed or now an online-only placeholder moves to
    "changed since scan". The other sections describe the scans as stored: a file deleted, changed or excluded in
    the configuration since the scans is not read again, and each of those sections says so in its limits.
14. **Own folder.** The tool's folder is pruned before the walk descends into it, so two runs find the same
    original catalogs and never a snapshot.
15. **Read-only.** A listing of path, size and time under every walked root is equal before and after a run.
    Links and junctions are not followed.

### Report sections

1. **Catalog inventory.** Every `.lrcat`: role, status, version, records, pending write-ahead bytes, stale lock.
2. **Shoot catalogs.** Per catalog: records present; missing records split into "same photo (file name and
   capture time) now at a path another catalog records", "only a same-named file exists", "nothing of that name
   on a library root"; each recorded root folder and whether it exists. Both leftover classes are also looked up
   on the backup roots by file name and date, and set beside the uncatalogued files of the same folder tree. The
   date test (file time equals capture time, whole-hour offsets allowed) is a lower bound and is labelled so.
   Only photo and sidecar file types are checked here and in section 8; a record of another type (video, TIFF,
   PNG) is never called missing, and the section says those types were not checked.
3. **Safe candidates.** An unrecorded file with an identical twin that is recorded and present. This file holds
   safe candidates and nothing else, and is empty while catalog coverage is incomplete. Space per folder.
4. **Needs your eye.** Identical groups with no recorded member; identical groups with two or more recorded
   members (work for Lightroom's own duplicate finder); two names of one file; would-be candidates held back
   because coverage is incomplete; candidates whose file or twin changed since the scan; files that could not be
   compared because a hash is missing or stale.
5. **Copies on external drives.** Per year folder (`group_depth` levels under the photo root): raws, how many
   have a copy on which backup root, how many have none. A copy matched by size and sample hash is labelled
   "sampled". `verify` hashes one folder tree and its copies in full and upgrades those rows to "verified". It
   counts each file with an exact copy as `verified` and each copy whose full hash differs from its file's as
   `mismatch` (a file with one exact and one corrupted copy counts in both, so the counts need not add up to the
   files read). Also: raws on each backup root that are on no library root, per top folder. The summary totals
   the bytes of the two kinds of row, `year` and `backup_only`, apart, each with its own "By folder" table.
6. **RAW+JPEG pairs.** Per folder: pairs and the space of the JPEG side, split into "recorded as the raw's
   sidecar", "catalogued as its own photo" and "uncatalogued". While catalog coverage is incomplete (rule 5),
   every row says that a catalog that could not be read, or that changed, may record these files.
7. **Large and unreferenced.** Folders of files that no catalog records and that duplicate nothing, largest first.
8. **Missing.** Records of the live catalog with no file; "drive not connected" kept apart from "missing".
9. **Containers.** Each `.lrdata` container: type, size, state of the catalog beside it. Only preview caches
   count toward reclaimable space.
10. **Loose photos.** Raw and camera JPEG files on a library root outside the photo root. Application, program
    and repository folders are counted, not listed.
11. **Not read.** Placeholders, unreadable folders, partial or unconnected roots, catalogs that limit coverage.

Every section states its totals and what it could not see. The summary opens with coverage: which roots and
catalogs the run could and could not read. It says when it was written (`Generated:`) and names the scans it
rests on: kind, id, root, status and finished time. A refused report changes nothing in `out/`; when an older
report is there, the refusal says that it is older than the latest scans and must not be acted on.

### Verification

- One unit test per rule above, and one per behaviour: identical content under two names is one group; the same
  name with different content is not; a recorded copy is never a candidate; a file recorded only by a second
  catalog is recorded; a sidecar JPEG is recorded; a missing record is reported; an unreadable catalog is listed
  and the run continues; a moved record is matched by name and capture time.
- From the outside review of 2026-11-11: a raw inside `Mobile Downloads.lrdata` is in the photo inventory and
  never in reclaimable totals, while a preview container is summed; with a second catalog unreadable, no row is
  a safe candidate; with the tool's folder inside a scanned root, two runs return the same catalogs and no
  snapshot; a resumed run after the keeper twin disappears, an interrupted scan, and a file replaced with the
  same size and time each fail to produce a safe verdict.
- `classify.py` decides verdicts, so it ships a planted-defect gate (`tests/mutants/classify.json`, about five
  decision-level defects, run by `C:/ssw/tools/mutate.py`).
- A reviewer runs the tool end to end on the synthetic tree before it reads the real library.
- The real run is made by the controller with Lightroom closed and the owner told first. Before-and-after
  listings must be equal, and totals are compared with the probe of 2026-11-11; any difference is explained
  before the report is handed over.

## Phase B1: backup copy (planning approved 2026-11-12; first backup verified 2026-11-18)

The first real run showed that most raws have no copy on any other drive. So the first job of phase B is a
backup, not an offload: every folder of the photo root that holds a photo or a video is copied to each archive
root. It copies and never moves, frees no space, and needs no catalog, so a stale catalog snapshot does not
block it. (The first backup, verified 2026-11-18, selected the folders that hold a raw; on 2026-11-19 the owner
widened the selection to folders that hold only JPEG files, to HEIC and PNG files and to videos, and left
Lightroom catalogs and their previews out.)

### Configuration

One new key in `work/config.json`: `archive_roots`, a list of folders, each the new top folder on one archive
drive. An archive root may not lie inside a library root or the tool's folder, and no two may share a volume.
The key is optional for the phase A subcommands and required by the three below.

### Subcommands

- `backup-plan` writes one manifest per archive root, `out/backup-manifest-<n>.csv` (`n` is the root's position
  in `archive_roots`), and a summary. It finds the folders by listing the tree under `photo_root` itself (the
  files scans record raw and JPEG files only), and reads the latest complete files scan of each library root
  only to count the raws outside `photo_root`. It refuses, exit 2, naming the root, when the drive of an archive
  root is not connected, before the work database is opened and before anything is written.
  - A folder is selected when it lies under `photo_root` and a photo or video file sits directly in it: its
    extension, ignoring case, is a raw one (`paths.RAW_EXTS`); `jpg` or `jpeg`; `heic`, `heif`, `png`, `tif`,
    `tiff`, `psd`, `psb`, `gif`, `webp` or `bmp`; or `mp4`, `mov`, `m4v`, `avi`, `mts`, `m2ts`, `3gp` or `mkv`.
    The owner named JPEG, HEIC, PNG and videos (R23); the other types are the controller's reading, shown to the
    owner with the manifest. Selection is by the folder's content.
  - The listing reads names only, never a file's content, and keeps its own stack (no depth limit). It follows
    no link or junction and counts each one. It does not enter the tool's own folder, a folder named in
    `exclude`, an archive root, or a preview cache as rule 11 types it (a name ending in `previews.lrdata`,
    ignoring case, in which no catalog records a file); every other `.lrdata` container is walked like any folder,
    as rule 11 says. "A catalog records a file in it" is read from the `record` rows of the latest `catalogs`
    scans, as `inventory` reads it; when there is no `catalogs` scan, every `.lrdata` container is walked (a
    preview cache holds no photo file, so walking it costs time and selects nothing). A folder that cannot be
    listed is counted as not read and named in the summary, which then opens with that: such a plan is not one
    to approve until the folder is listed or the owner accepts its absence.
  - Each selected folder is listed afresh. Every regular file directly in it becomes a row, whatever its type,
    except a Lightroom catalog file (a name ending in `.lrcat`, `.lrcat-wal`, `.lrcat-shm`, `.lrcat.lock` or
    `.lrcat-journal`, ignoring case): it is never a row, and the summary counts it. Catalogs are archived by their
    own rule (R5, R23).
    Subfolders are not descended: one that holds a photo or a video is selected by itself, any other is
    counted in the summary as left out. Links and junctions are not followed. The tool's own folder is never
    selected.
  - A plan is made afresh and holds every selected folder, those an earlier backup copied included. A rerun of
    `backup-run` then copies only what is not there yet (rules 18 and 20), and `backup-verify` reads every row
    again, so the verified list is not older than its manifest, as rule 27 of the reject check asks. Before a
    new plan replaces them, the controller keeps the earlier manifests, verified lists and journals aside; from
    the new plan until `backup-verify` has run on every new manifest, rule 27 holds every archive row, and the
    reject check is not run.
  - A placeholder (rule 7) becomes a row with action `skip_placeholder` and is never opened.
  - The destination is the archive root joined with the file's path relative to `photo_root`.
  - Columns: `source`, `dest`, `bytes`, `mtime_ns`, `action` (`copy` or `skip_placeholder`), `reason`.
  - The summary gives, per archive root: folders, files, bytes, placeholders skipped, subfolders left out,
    catalog files left out, and the free space of the destination volume. Once for the plan it gives the
    extensions that select a folder, the folders not read (named), the links and junctions not followed, and the
    folders under `photo_root` that hold
    files but were not selected, with a tally of those files' extensions, so a missing type is seen before the
    owner approves the manifest. Raws on a library root outside
    `photo_root` are counted and left to phase D. The manifest is the list the owner approves; nothing is copied
    by this subcommand.
- `backup-run <manifest>` is a dry run that prints what it would do; with `--apply` it copies.
- `backup-verify <manifest>` reads every source and destination of the manifest in full, writes
  `out/backup-verified-<n>.csv` (`source`, `dest`, `bytes`, `sha256`, `state`), and writes nothing else.

### Rules the code must hold

Each rule gets one test that names it.

16. **Approved destinations only.** `backup-run` refuses the whole manifest, before any write, when a `dest` is
    not inside a configured archive root, is inside a library root, or when two rows share a `dest`; and when
    the bytes to copy exceed the free space of the destination volume. It also refuses when the last component
    of a `dest` ends in `.lrclean-part` (in any letter case), ends in a dot or a space, or holds a colon: Windows
    opens each of those as another file, among them the part file of rule 19, whose cleanup would then delete a
    destination. It also refuses a `dest` whose last component plus `.lrclean-part` is longer than 255 UTF-16 code
    units (a name of 243 units or more; a character outside the Basic Multilingual Plane counts 2): the part file
    of rule 19 could not be created. The planner still lists every file but a catalog file (a folder is copied
    whole), so such a file must be renamed in the library before its manifest can run. `backup-run` also refuses
    a `dest` whose folder, with links resolved (a junction or a symbolic link followed in the part of the path
    that exists), is not inside its archive root or is inside a library root, both also with links resolved: the
    copy would be written where the link leads.
17. **Sources are read-only.** A source is opened for reading and nothing under a library root is created,
    changed, renamed or removed. The before-and-after listing of rule 15 holds for a backup run under the photo
    root; outside it, see Verification. This holds through links present when the manifest is checked: a
    junction inside an archive root that leads into a library root is refused by rule 16, and nothing is created
    there. A link made after that check is not seen.
    `backup-verify` refuses, before it reads anything, when the report it would write is a `source` or a `dest`
    of the manifest.
18. **Never overwrite.** When `dest` exists, both files are hashed in full: identical is `already_there`,
    different is `conflict`. A conflicting destination is left as it was. An identical one is given the source's
    modification time when its own differs (rule 19); when that time cannot be set the row is still
    `already_there` and one line naming the destination goes to stderr. A destination that is an
    online-only placeholder (rule 7) is never opened: the row is `dest_placeholder`, the destination is left
    as it was, and the dry run says `dest_placeholder`. This holds also for a row the journal records as verified:
    the placeholder test comes before the journal skip.
19. **A copy is verified by content.** The file is written to `<dest>.lrclean-part` in the destination folder
    while the source is hashed, then flushed to disk and closed; the part file is read again in full and its
    SHA-256 must equal the source's; the source's size and time must still equal the manifest's; only then is
    the part file renamed to `dest` (a rename that cannot replace a file) and given the source's time. If that
    time cannot be set the row is `failed`, and the next run finds the destination identical and sets it (rule
    18). A mismatch removes the part file and the row is `failed`.
20. **Changed sources are not copied.** A source that is gone, is now a placeholder, or whose size or time
    differs from the manifest is `changed`, and nothing is written for it.
21. **Journal.** `work/journal/backup-<n>-<started>.jsonl` gets one line before a copy starts and one when it
    ends, with the state and the SHA-256; the end line also records the source's size and time as the manifest
    gave them. A rerun skips a row the journal records as verified only when the destination is not an
    online-only placeholder (rule 18), its size and time still match AND the row's source size and time equal the
    recorded ones; a journal line without them gives no skip. So a source that was edited and planned again is compared by content and ends `conflict`, never
    `verified`. `backup-verify` trusts no journal.
22. **A stalled drive ends the run.** When a read or write makes no progress for `stall_seconds` (default 120),
    the run writes the fact to the journal and exits. A part file left behind is removed by the next run before
    it copies that row. `--stall-seconds` takes only a finite number greater than 0; anything else is a usage
    error before the manifest is read.
23. **Exit codes.** `0` every row is verified, already there or a skipped placeholder; `2` configuration or
    manifest refused; `5` stalled; `6` finished with a `conflict`, `changed`, `failed` or `dest_placeholder`
    row. `backup-verify` exits `0` only when every `copy` row is identical on both sides.

### Verification

- One unit test per rule 16 to 23 and one per bullet of `backup-plan`, on a synthetic tree. The selection is one
  parametrized test over every extension of the list (and one upper-case extension), so a dropped type fails;
  then: a folder that holds only other files (in the tally), a folder that holds only a catalog, each catalog
  file beside photos (never a row), a preview cache (not entered), a storage container and an unknown container
  (walked), a folder in `exclude` and an archive root (not entered), a junction (not followed, counted), a folder
  that cannot be listed (named as not read), a nested photo folder (selected by itself), and a replan of a tree
  already backed up (every row `already_there` or skipped by the journal, no file content written).
- The copy engine decides "verified", so it ships a planted-defect gate (`tests/mutants/backup.json`).
- A reviewer runs the three subcommands end to end on a synthetic tree before the real one.
- The real run is the controller's, from a manifest the owner approved: one small folder to each archive root
  first, checked by `backup-verify`, then the rest.
- A read made right after a copy can be answered from the operating system's cache, not from the drive. The
  run's `verified` shows that what was read back matched the source; that read-back may come from the cache.
  Only a verify of more data than fits in memory, or one made after the drive was reconnected, reads the
  drive. `backup-verify` after the full run is that check when the run is that large, and the backup is called
  verified only when it exits 0 on every manifest.
- The listing of rule 17 is compared strictly under the photo root: nothing gone, new or changed. A library
  root that also holds the settings of running programs changes during a run of hours, so every difference
  outside the photo root is read by name and reported to the owner; none may be a photo, a sidecar or a catalog,
  or lie under a catalog, backup or archive root.
- While a run or a verify is going, the tool's folder is left as it is: no code lands, no scan runs, and the
  configuration and the manifests are not edited.

### Known limits

- **The walk of the photo root has no stall limit.** It reads names only, on the system drive, as the inventory
  does; listing an online-only folder can make the sync client fill in its names (R4 keeps the client off).

Found by an outside read of the copy engine on 2026-11-17, each reproduced by running it, and not built against:

- **One run per manifest.** Nothing stops two `backup-run --apply` runs from writing to one archive root at the same
  time, and two runs that name the same destination can end with a wrong `verified`. Each manifest's run is
  started once, by hand. Runs of different manifests write to different archive roots and share only the
  sources, which they read, and the tool's own folder, where each writes its own journal and report. The first
  full backup ran both at the same time without a fault, after a pilot of one folder on both manifests at once.
- **The rerun skip trusts the journal.** The `end` line records the destination's size and time as read just
  after the copy. A program that changes the new file in that instant is not seen by a rerun; `backup-verify`,
  which reads every file in full, sees it. The run ends with `backup-verify` on every manifest.
- **Ctrl-C.** The command ends with the interrupt and leaves a part file, which the next run removes. A program
  that calls `cli.main` and catches the interrupt lets the copy thread go on.
- **A nearly full archive drive.** A part file left behind counts against the free space, so a rerun can be
  refused for space until that file is removed by hand; the message does not name it.
- **Planning again with fewer archive roots** removes the manifests of the higher numbers from `out/`.
- **Names.** A short 8.3 alias of a part file's name is not recognised. A file name of 243 UTF-16 code units or more
  cannot be copied (the part file's name would be too long): `backup-run` refuses the manifest and names the file
  (rule 16).
- **During a comparison.** A source that grows while an existing destination is being compared is reported
  `already_there` in that run and `changed` in the next. An existing destination that is an online-only
  placeholder is refused by `backup-run` as `dest_placeholder` and by `backup-verify` as `unreadable`;
  neither opens it.

## The reject check (shape approved by the owner 2026-11-18, R14; design by the controller)

The owner culls in Lightroom: rejects are flagged there, by hand, with Lightroom's own tools or through the model's
plug-in. The tool never uses the plug-in. After culling the owner closes Lightroom, `catalogs` runs again, and the
new subcommand `rejects` (module `lrdup/rejects.py`) reads the flags from the fresh snapshots of the live catalog
and of every shoot catalog (R17). It writes evidence and a removal list and changes nothing anywhere. Removing a
file from the disk is a later phase with its own plan and the owner's word.

### What `catalogs` adds

For every image of every catalog it reads, `catalogs` also stores its pick flag, whether it has develop work and a
digest of its develop settings, in one new table `image(catalog_id, path_norm, via, pick, developed, settings)`: one
row per image and per path form of its file (rule 2), so that an image is found by the same paths as its record. An
image is joined to its file through `Adobe_images.rootFile`; a master and its virtual copies share one file, so one
path can have several images. `pick` is `Adobe_images.pick` as an integer: `-1` rejected (`-1.0` counts as `-1`),
`0` none, `1` picked; empty when the column is missing or holds another value. `developed` comes from the image's
row of `Adobe_imageDevelopSettings` (joined on its `image` column). A value of `hasDevelopAdjustments` or
`hasDevelopAdjustmentsEx` is usable when it is NULL or a number equal to -1, 0 or 1; Lightroom stores -1 or 0 for a
photo it calls not edited and 1 for an edited one (checked on 2026-11-18 against Lightroom's own count of edited
photos). `developed` is `1` when either column equals 1; empty when the table, both columns or the image's row is
missing, when a value is not usable, or when every present column is NULL; `0` otherwise. Both are INTEGER columns.
`settings` (TEXT, for R22 and rule 26a) is the lowercase hex SHA-256 of the image's
`Adobe_imageDevelopSettings.text`, encoded as UTF-8; it is empty when the table, the column or the image's row is
missing, or the text is NULL or empty. An `image` table made before `settings` existed gains the column when the
work database is opened (`store.connect` adds a missing column of a frozen table); its old rows hold an empty
`settings`, which lets nothing go under rule 26a. No other table changes.

The catalogs named in `old_states` are the copies of the live catalog: its older states and any backup copy of it.
They neither reject nor keep (R13, R17), except that their develop work and picks keep a photo (R18). Naming a
backup copy there moves it out of the shoot-catalog section of the phase A report; no phase A verdict reads the
role. No configuration key is added.

### Rules the code must hold

24. **A reject.** A catalog *rejects* a path when it has at least one `image` row for it and every such row has pick
    `-1` (a master and each virtual copy; images are grouped by `path_norm`); an empty pick is not rejected, and a
    catalog rejects only when its status is `ok` and it is not in `excluded_catalogs`. A catalog *keeps* a path when
    it records it as a photo and does not reject it, or records it as a sidecar of a photo in the same folder with
    the same base name that it does not reject. Only the live catalog and the shoot catalogs count: the catalogs in
    `old_states` neither reject nor keep, except for develop work and picks (R18, rule 26). A path is a reject when
    some catalog rejects it as a photo (rule 2, not as a sidecar); it can leave only when no catalog keeps it (rule
    26). So the live catalog reflects a reject (the controller's reading, R17) when it rejects the photo too or does
    not record it. A catalog rejects a path only through the absolute form of its records (rule 2); the
    catalog-relative form counts for keeping, develop work and picks, because a moved catalog's relative form can
    point at another file. The tool does not ask who set a flag: the owner, Lightroom's own tools, or the model when
    the owner asks for it (R16).
25. **Fresh flags.** `rejects` refuses with exit 4 ("run catalogs again") when no latest `catalogs` scan holds the
    live catalog (by `live_catalog`), its status is not `ok`, its source has changed since its snapshot (size,
    time, `-wal` size, a new `.lock`), it has no `image` row, every pick of it is empty, or the role stored for any
    catalog in the latest scans differs from the one `live_catalog` and `old_states` give now. A flag set after the
    last `catalogs` run is never read as "not rejected". A refusal changes nothing in `out/` and says that any
    reject report and removal list there are older than the latest scans and must not be acted on. A shoot
    catalog whose source changed since its snapshot is covered by rule 5: coverage is incomplete.
26. **Kept (R13, R17).** A reject is kept while a catalog with status `ok` or `partial`, not in `excluded_catalogs`
    and not in `old_states`, keeps it (rule 24, paths compared as in rule 3). It is also kept, as work (R18), while
    any catalog read with status `ok` or `partial` and not in `excluded_catalogs`, whatever its role and the
    catalogs in `old_states` included, has an image of that path with `developed` `1` or empty (a develop state that
    cannot be read keeps the photo), or with pick `1`; develop work stops keeping it only as rule 26a says, and a
    pick always keeps it. A kept reject never goes on the list and is never removed from a catalog. `rejects`
    refuses with exit 2 when `live_catalog` is named in `excluded_catalogs`: the live catalog must always be able to
    keep a photo. A catalog that is excluded or was never read keeps nothing, and the summary names it as not
    checked.
26a. **Develop settings shared with a twin that stays (R22).** A reject's develop work does not keep it when, for
    some other path, the twin, all of these hold; otherwise rule 26 holds unchanged. "A catalog read" below means
    one with status `ok` or `partial`, not in `excluded_catalogs`. An empty `settings` (NULL included) never equals
    another `settings`, empty or not.
    1. *Identical.* The reject and the twin each have a counted full hash, the two are equal, and their sizes are
       equal. The path's row is its `file` row in the latest `files` scan of the configured library root that holds
       it; that scan must be `complete` (rule 1), so a path whose root's latest scan is not, or that has no row in
       it, has no counted full hash. A `hash` row counts for the path when its `full` is non-empty, its `file` row
       has the path (paths compared as in rule 3) and the volume of the path's row, and the `hash` row's own size,
       modification time and file index equal those of the path's row. The path has the counted full hash `H` when
       at least one `hash` row counts, every one that counts holds `H`, and the file's `stat` now gives the path
       row's size, modification time and file index. A `hash` row belongs to the `files` scan it
       was made on, so a later scan may hold none; the hash is carried by path. The reject and the twin are two
       files: their `stat` now gives a different (`st_dev`, `st_ino`), so a junction or a hard link that gives one
       file two paths keeps the reject. Phase B2 hashes the reject and its twin again before anything leaves.
    2. *The twin stays.* The twin exists, is not an online-only placeholder (R4), and is recorded as a photo (not
       only as a sidecar) by a catalog read with role `shoot` and by the live catalog, where an image of it has
       `developed` `1` and the `settings` condition 5 compares. These records and that image count only through
       the absolute path form (rule 2): what lets a copy go is never read through a catalog-relative form, which a
       moved catalog can point at another file (as rule 24 holds for a reject). What keeps a copy (conditions 3 to
       5) counts in either form. So rule 26 keeps the twin for its own work, and as
       an existing sidecar of another reject it holds that reject's row (rule 29); and because a shoot catalog
       records it, condition 3 never holds for the twin, so this rule never lets it go. The twin therefore never
       goes on the list, its verdict does not depend on this rule, and two copies cannot let each other go.
    3. *No other catalog has the reject.* No catalog read with role `shoot` records the reject (either path form,
       as a photo or as a sidecar).
    4. *No pick.* No catalog read has an image of the reject with pick `1` (rule 26: a pick always keeps).
    5. *Same settings.* Every image of the reject that holds develop work by rule 26 (`developed` `1` or empty), in
       any catalog read, the catalogs in `old_states` included, has `developed` `1` and a `settings` equal to the
       `settings` of an image of the twin in the live catalog with `developed` `1`; all those images of the reject
       share that one `settings`, which `rejects.csv` reports as `twin_settings`. A develop state that cannot be
       read, an empty `settings`, or a virtual copy or an older state with other settings keeps the reject; an image
       of the reject with `developed` `0` holds no develop work and is not compared. (The controller's reading of
       R22 for the catalogs in `old_states`: the stricter one.)

    The twin named is the first by `path_norm` among the paths for which all five hold. The rule lifts only the
    develop hold on the reject itself: coverage (rule 5), archive evidence (rule 27) and its sidecars (rule 29,
    whose own develop work or pick still holds the row) are tested as for any reject.
27. **Archive evidence.** `rejects` refuses with exit 2 unless `archive_roots` names at least two roots. A reject
    has a verified copy on root `n` (its position in `archive_roots`) when: its path is a `source` of
    `out/backup-manifest-<n>.csv` (compared as in rule 3) with action `copy` and a `dest` inside root `n`;
    `out/backup-verified-<n>.csv` is not older than that manifest and has a row with the same `source`, `dest` and
    `bytes` and state `identical`; the file's size and modification time now equal the manifest row; and the
    destination exists with that size and is not an online-only placeholder (R4). The check reads no file's content:
    phase B2 hashes both again before anything is removed. A manifest or verified list that is absent holds every
    row it would decide; one that is unreadable or lacks a column of its header is refused with exit 2. An error
    from `stat` holds the row, never passes it.
28. **Develop work and picks.** For each reject, every catalog that has an image of that path with `developed` `1`
    or empty, or with pick `1`, is named with its role and which of the two it holds, the live catalog included.
    Either keeps the reject (rule 26), except develop work that rule 26a lifts; such a reject also names its twin and
    the shared `settings`.
29. **The removal list.** A reject goes on `out/reject-removal-list.csv` only when catalog coverage is complete
    (rule 5), no catalog keeps it (rule 26: develop work and picks included, except as rule 26a lifts develop work),
    it exists and is not a placeholder (R4), and it has a verified copy on every archive root (rule 27). Its
    sidecars are the sidecar records (`is_sidecar` 1, either path form) of each catalog that rejects the reject, in
    the same folder with the same base name as the reject. The base name of a sidecar named after the whole photo
    name is the photo's (`IMG_0001.ARW.xmp` has the base name `IMG_0001`). Its sidecars also include the file
    `<the reject's name>.xmp` or `.acr` of the latest complete files scan of the library roots, which no catalog
    need record (its spelling is the files scan's); only that exact name is read from the files scan. A sidecar
    that does not exist is ignored. Each existing sidecar must pass the same tests as the reject (not kept, rule
    26 included, so no develop work or pick on the sidecar's own path; not a placeholder; archive evidence), and
    one that fails holds the whole row with its reason: a sidecar path that a catalog records as a photo it does
    not reject is kept. A raw and its sidecars
    leave together or not at all: a reject that is also an existing sidecar of a reject that is held, by any reason
    including this one, is held with it and takes its verdict. The hold is symmetric for a shared sidecar: when any
    reject is held, every reject whose existing sidecars include a path that the held reject also lists (the shared
    path need not itself be a reject) is held with it, takes its verdict, has the holder's path as its `held_by` (as
    for a held sidecar), and passes the hold on in the same way until none is left to hold; of several held rejects
    the first in path order is the holder. A reject that is also an existing sidecar of a reject on the list is
    listed once, among that reject's sidecars, and its own existing sidecars travel with it: they join that row's
    sidecars, each path once, and pass the same tests. The hold follows the same chain: a hold that reaches a reject
    travelling in another reject's row, by any reason, holds that row's reject.
30. **Every reject is reported.** `out/rejects.csv` has one row per reject: path, bytes, the verdict, `rejected_by`
    and `kept_by` (the catalogs, each with its role), the archive state per root, develop work (rule 28), `twin` and
    `twin_settings` (the twin's path and the shared `settings` when rule 26a lifted the develop hold, else empty), and
    `how_decided` (the number of images per catalog, counted by their absolute form, rule 2; a catalog that holds
    the path only through its catalog-relative form shows `0 (catalog-relative form only)`). The verdict is
    `on_list` or the first of these that holds, tested over the reject and then its sidecars: `coverage_incomplete`
    (rule 5 does not hold), `kept_in_live` (the live catalog keeps it), `kept` (another catalog keeps it, rule 24
    and the first sentence of rule 26, or a sidecar as in rule 29), `kept_for_work` (no catalog keeps it so, but
    some catalog holds develop work that rule 26a does not lift or a pick, the second sentence of rule 26, on the
    reject or on a sidecar),
    `missing` (the file does not exist),
    `placeholder` (R4), `outside_backup` (on some root no manifest row with action `copy` and a `dest` inside that
    root), `changed_since_backup` (on some root the size or modification time differs from the manifest row),
    `no_archive_copy` (on some root the verified row is absent, not `identical`, or older than its manifest, or the
    destination is absent, of another size or an online-only placeholder). The removal list has one row per reject
    on the list: `source`, `bytes`, `mtime_ns`, `sidecars` (their paths, `;` between them), `sidecar_bytes`,
    `dest_<n>` per root, and `twin` (as in `out/rejects.csv`; phase B2 hashes it with the reject). The summary is
    `out/rejects-summary.md`. It opens with `Generated:` and the scans it rests on (kind, id, root, status,
    finished), and gives files and bytes per verdict (a reject's bytes include its
    existing sidecars), the rejects per catalog that rejects them, the paths with a rejected image in a catalog that
    records them through the absolute form and does not reject them, then per catalog the number of paths it flags
    -1 only through its catalog-relative form (such a flag rejects nothing, rule 24), the rejects the live catalog
    keeps (counted before the coverage test), the rejects on the list whose develop hold rule 26a lifted (files
    and bytes), and every catalog not checked, or `- none`: the excluded ones (R9),
    the `unreadable` and `partial` ones, and every catalog root whose latest scan is not complete. Catalogs the
    tool never read (R11) are not known to it, and the summary says so. `rejects` exits 0 when it wrote its files,
    also when there is no reject; 2 when the configuration or an input file is refused; 4 by rule 25.
31. **Read-only.** `rejects` writes only under `out/`, apart from the column a work database made before
    `settings` gains when it is opened (`store.connect`, see the `image` paragraph). It reads the work database
    (the `catalog`, `record` and `image` tables and the latest `catalogs` scans), the configuration, the
    manifests, the verified lists, the `stat` of files and of each catalog source with its `-wal` and `.lock`, and
    the listing of the catalog roots that rule 5 asks for. It reads `file` rows for the sidecar names of rule 29
    and `file` and `hash` rows for rule 26a, with the `stat` of each reject and of each path it tests as a twin;
    it opens no file or catalog. The listing of rule 15 holds.

### Verification

- At least one test per rule 24 to 31, on a synthetic tree whose synthetic catalogs gain pick flags, develop state
  and virtual copies that share their master's file. A rule that compares several fields or walks the roots gets a
  case per field and per root: rule 24 (every image rejected; only the master; only a virtual copy; no image; empty
  pick; rejected in a shoot catalog and not recorded by the live one; rejected in a shoot catalog and kept by the
  live one; a catalog in `old_states` that records it is ignored; a `partial` catalog does not reject; a sidecar
  record of a rejected photo does not keep it; rejected in one shoot catalog and kept by another); rule 26 (develop
  work in a catalog that rejects the photo; develop work only in a catalog in `old_states`; a pick in another
  catalog; a develop state that cannot be read); rule 27 (size differs; time differs; the first root missing alone;
  the second root missing alone; not `identical`; destination absent or of another size; verified list older than
  its manifest; fewer than two roots); rule 29 (a sidecar failing alone; an absent sidecar ignored; a sidecar
  recorded as a photo elsewhere; a sidecar with develop work or a pick alone; a sidecar shared by two rejects, one
  held for `no_archive_copy` and the other otherwise on the list: both are held, the second with the first's verdict
  and the first's path as its `held_by`, and neither lists the shared sidecar; the same where the shared sidecar is
  not a reject itself and the held reject comes second in path order; a hold passed on to a third reject through
  another shared sidecar); rule 30 (each verdict, and the order when two hold).
- Rule 26a, one case per condition failing alone, each against a case where all hold and the reject goes on the
  list with its `twin` and `twin_settings`:
  - 1, on the reject's side and on the twin's side, one case each: no full hash; the `hash` row's own size, time
    or file index differs from the path's row (one case per field); its `file` row's volume differs; the file's
    size, time or file index now differs from the path's row (one case per field); two counting hash rows that
    disagree; the root's latest `files` scan is `partial`; the path has no row in it. Also: the full hashes
    differ; the sizes differ; a hash carried from an earlier scan counts; the twin is the reject's own file under
    another path, through a junction and through a hard link.
  - 2: the twin missing; a placeholder; recorded only as a sidecar by the live catalog; only as a sidecar by the
    shoot catalog; not recorded by the live catalog; recorded only by a catalog in `old_states`, or by a shoot
    catalog that is `excluded` or `unreadable`; its matching live image with `developed` `0`; with `developed`
    empty; its equal settings only in a shoot catalog or an old state, not in the live catalog; its record or its
    matching live image only through the catalog-relative form; two live images, one with `developed` `1` and
    other settings, one with `developed` `0` and the reject's settings.
  - 3: a shoot catalog records the reject as a photo; as a sidecar; through its catalog-relative form only.
  - 4: a pick in an old state. (A pick in the live catalog makes the live catalog keep the reject first.)
  - 5: other settings; an empty `settings` on the reject's image; empty on both images; `developed` empty; a
    virtual copy with other settings; an old state with other settings. Not blocking: another image of the reject
    with `developed` `0`.
  - Also: both copies are rejects, one recorded and rejected by a shoot catalog, the other recorded by none; only
    the second goes on the list, and the first stays `kept_for_work`. Several twins, where the first by `path_norm`
    fails condition 2 and a later one passes (the later one is named). The twin stays off the list when it is an
    existing sidecar of another reject that rule 26a lets go. A sidecar's own develop work still holds the row.
    `store.connect` on a database whose `image` table lacks `settings` adds it and keeps its rows, and such an old
    row lets nothing go.
- The removal list decides which files may leave the disk, so `rejects` ships a planted-defect gate
  (`tests/mutants/rejects.json`): the every-image test of rule 24, the keep test, the develop and pick test, the
  per-root archive test, the verified-list age test, the size and the time tests separately, the sidecar test, the
  coverage gate, the freshness refusal and the role refusal; and for rule 26a the hash equality test, the carried
  hash's validity test, the two-files test (`st_dev`, `st_ino`), the twin-stays test (a shoot record and
  `developed` `1` in the live catalog), the reject's no-shoot-record test and the settings test over every catalog
  read. A pick needs none of its own: rule 26a never lifts the pick hold, and the existing tests of rule 26 fail
  when that hold is dropped.
- A reviewer runs `catalogs` and `rejects` end to end on a synthetic tree before the real one.
- The real run is the controller's, after the owner has culled and closed Lightroom.

### Not in this check

Removing, staging or moving any file; removing photos from a catalog (done in Lightroom after the files leave
the disk, R17); any use of the plug-in by the tool. Two known limits of rule 26a: a drive letter reassigned
between `hash` and `rejects` is not detected (the tool stores no device identity; phase B2 hashes both copies
again, and the controller identifies the drives by label and serial before the real run); and the tool reads no
Lightroom stack, so a raw stacked with its JPEG export is tied only through byte identity, which an export never
has.

## Phase B2: offload (gated)

Phase B2 takes the files of the reject check's removal list off their place in the photo folders, in two steps that
the owner starts one after the other. `stage` renames each listed file and its sidecars, on the same volume, into
a staging folder inside the tool's own folder; nothing is copied, deleted or freed, and `undo` renames every file
back. `purge` is a separate owner command that deletes the staged files, and is the only step that frees space and
the only one that cannot be undone. Both archive copies of every file were made and verified by phase B1, so B2
copies nothing (the first sketch of this phase copied the file to the archive drive; that step is B1's and is
dropped here). B2 trusts no stored result: before it stages, and again before it purges, it hashes every file
involved in full. It never touches a catalog (H2): the owner removes the photos from each catalog in Lightroom
after `stage`, one click each (R16, R17). The input is `out/reject-removal-list.csv`, approved by the owner by its
SHA-256; only the rows of that exact file are ever moved. The design is the controller's, from the rulings of
2026-11-19; the plan, an outside read of it and the owner's word come before any code is built or run.

### Configuration

No new key. B2 reads `archive_roots` (at least two, as for rule 27), `photo_root`, `library_roots`,
`catalog_roots`, `live_catalog`, `old_states` and `excluded_catalogs`, as the reject check does. The staging
folder is fixed, `work/staging/<batch>/`, and the journal is `work/journal/mover-<batch>.jsonl`, both inside the
tool's folder (rule 14 keeps every scan out of it). `<batch>` is the start time of a `stage --apply` run,
`YYYYMMDDTHHMMSSZ`; a `<batch>` given on the command line that does not have that form is refused (exit 2), and
`stage --apply` refuses (exit 2) when the batch's journal or staging folder exists already. The owner's approval
is given on the command line, never stored in the configuration.

### Subcommands

Each is a dry run unless it has `--apply`. A dry run writes nothing: no journal, no staging folder, no file in
`out/`. It runs the refusals of rule 33 and the cheap tests of rule 35 (`stat` and placeholder tests, the archive
evidence of rule 27), reads no file's content, and prints what `--apply` would do and what it would hold.

- `stage <list> --approved-sha256 <hex> [--only <source>]... [--apply] [--stall-seconds <n>]`. `<list>` is
  `out/reject-removal-list.csv` or, with rules 52 and 53, `out/copy-removal-list.csv` (rule 32). Reads the list
  once, in memory, and its SHA-256 must equal the approved one (rule 32). A dry run prints that SHA-256, so the
  owner can compare it with the one approved. With `--only`, which names rows of the list by their `source`
  (compared as in rule 3), only those rows are processed, which is how the pilot picks its ten rows; a name that is
  no row of the list is refused. Per row, in the order of the list: check (rule 35), then rename (rule 37). With
  `--apply` it writes the journal and `out/stage-<batch>.csv`: `source`, `kind` (`reject`, `copy`, `sidecar`),
  `staged`, `bytes`, `sha256`, `state` (`staged`, `held`, or `open_stage` for a step begun and not ended),
  `reason`. A dry run hashes nothing (rule 42): a row it marks `would_stage` can still be held at `--apply`.
- `purge <batch> [--approved-sha256 <hex>] [--apply] [--stall-seconds <n>]`. Reads the journal of the batch
  and deletes the staged files that are still staged (rule 41). A dry run prints the staged files with their sizes,
  the space `--apply` would free, and the purge set's SHA-256; `--apply` needs that SHA-256 as
  `--approved-sha256`. With `--apply` it writes `out/purge-<batch>.csv` (the same columns, `state` `purged`,
  `held` or `open_purge`). A dry run hashes nothing: a row it marks `would_purge` can still be held at `--apply`.
- `undo <batch> [--apply]`. Reads the journal of the batch and renames every file that is still staged back to
  its original path (rule 40). A dry run prints each rename and each refusal.

### Rules the code must hold

Each rule gets one test that names it. The rules of the reject check that the removal list rests on (rules 5, 24
to 27, 29 and 30) are not repeated here; B2 checks their result again but does not decide it a second time.

32. **Only the approved list.** `stage` reads only `out/reject-removal-list.csv` (`<list>`, compared as in rule 3,
    must name that file; any other path is refused, exit 2, before a byte is read), which the reject check always
    writes with the latest summary, and that summary names the list's SHA-256 (rule 34), so an older copy of a list,
    at any path, cannot be run against a newer summary. It reads the file once into memory, takes its SHA-256 and
    parses that same buffer, so a file swapped between the hash and the parse is never read. The SHA-256 must equal
    `--approved-sha256` (64 hexadecimal digits, any case), else exit 2 before anything else is read. The header must
    be exactly `source`, `bytes`, `mtime_ns`, `sidecars`, `sidecar_bytes`, `dest_1` to `dest_<n>` and `twin`, with
    `n` the number of `archive_roots` (at least two); any other header, an unreadable row or a negative size is
    refused with exit 2. A list in which one path (compared as in rule 35) appears twice as a `source`, or as the
    `source` of one row and a sidecar of another, is refused the same way. A sidecar that two rows both list holds
    both rows (`shared_sidecar`, rule 36); the other rows go on. A name ending in a dot or a space, or holding a
    colon, cannot reach the list (B1 rule 16 gives it no archive copy); if one does, the list is refused. B2 never
    edits, sorts or extends the list, and never moves a path the list does not name (a file or a sidecar of a row,
    nothing else). Rule 52 admits one more list, `out/copy-removal-list.csv`, and rule 53 one more refusal. A list
    of no row stages nothing and exits 0.
33. **Refusals of the whole run.** `stage` and `purge` refuse the whole run, before they read a file's content or
    write anything, in this order, with the exit code named: the list is not `out/reject-removal-list.csv` or its
    SHA-256 is not the approved one (2, rule 32, before any other read; the message prints the SHA-256 computed, so
    the owner can compare it); `archive_roots` names fewer than two roots, a root is not a connected folder,
    `photo_root` is missing, `live_catalog` is named in `excluded_catalogs`, or a source lies outside `photo_root`
    or inside an archive root, a backup root or the tool's folder (2); a Lightroom process is running, or a `.lock`
    file lies beside any catalog source of the latest `catalogs` scans that is not in `excluded_catalogs` (an
    excluded catalog is never read and protects nothing, R9) (3, the code of the phase A refusal; the message names
    the process or every such lock, and a stale lock is cleared by the owner in Lightroom, never by the tool); the
    evidence is not fresh or coverage is incomplete (4, rule 34; a new `.lock` is exit 3, never 4). `purge` makes
    only the refusals that concern it, in this order: the archive roots (2), the approved SHA-256 of its purge set
    (2), Lightroom (3) and incomplete coverage (4, read from the latest `catalogs` scan as `stage` reads it); it
    skips the freshness test of rule 34 and nothing else of it, and its sources for the path tests are the original
    paths its journal records. `undo` makes none of the whole-run ones but the link test of rule 38 (rule 40). A
    refusal changes nothing, in `out/` included, and says what to run. The link test of rule 38 comes before every
    refusal above, and, for `stage` and `purge`, before the work database is opened.
34. **Fresh evidence (rule 25 again).** A run is stale when the removal list was made from scans that are not the
    latest ones. `stage` therefore requires `out/rejects-summary.md` to exist (the reject check removes it before it
    replaces the list, so a run that stopped half way leaves none) and the scans it names (kind, id) to be exactly
    the latest `catalogs` scans now; the summary's last section, `## Removal list`, to name in its one line `-
    SHA-256: <64 lower-case hex>` the SHA-256 of the list `stage` read (the reject check writes the SHA-256 of the
    list's bytes as written); no catalog source to have changed since its snapshot (size, time, `-wal` size, a new
    `.lock`); the roles stored for the catalogs to be the ones `live_catalog` and `old_states` give now; and catalog
    coverage to be complete (rule 5, built as `rejects` builds it). Any failure is exit 4 ("run catalogs and rejects
    again"), except a new `.lock`, which rule 33 refuses first with exit 3; it says that the list is older than the
    latest scans and must not be acted on. The decision of the reject check is not made again: the same scans give
    the same decision, and any scan that differs is a stale run. `purge` makes the coverage test of this rule and
    not the others (rule 33). For `out/copy-removal-list.csv` the summary is `out/copies-summary.md` (rule 52).
35. **Trust nothing stored: every row is checked again, in content.** For each row, for the reject and for each
    sidecar the list names, a failed test holds the whole row (rule 36) with the reason named:
    - the file exists, is not an online-only placeholder (R4) and its size and time equal the list's `bytes` and
      `mtime_ns` (a sidecar's equal its row in the manifests, which the list does not carry). Its name is looked
      up in its folder by comparing `paths.norm` of the names (NFC and case, the function `--only` uses) and must
      match exactly one entry, whose spelling is the one used from here on (the list's `source` is not the
      spelling on disk: `catalogs` lower-cases the extension). Reasons `missing`, `placeholder`, `changed`,
      `name_ambiguous`. Every sidecar the list names must be found (else `missing`), and the sum of the sizes of
      the sidecars found must equal `sidecar_bytes` (else `changed`). The list joins sidecars with `;`, so a name
      holding a `;` is split and its parts are not found: the row is held;
    - the archive evidence of rule 27 is read again for the file on every root (the manifest row with action
      `copy`, the verified row `identical` and not older than its manifest, the destination present and of that
      size), and the destination equals the list's `dest_<n>` (for a sidecar, the manifest's): reason
      `no_archive_copy`. A sidecar's destination is read from the manifests, never from the list;
    - the file is hashed in full (SHA-256), and so is every destination; every one of them must equal the file's
      hash and have its size. The file, each destination and, for a rule 26a row, the twin are pairwise different
      files (their (`st_dev`, `st_ino`) differ; RC-42, L15), and the destinations lie on three different volumes
      with the file: each on another `st_dev` than the file and than the other destination of the same file
      (with more than two roots, every pair): reason `archive_mismatch` (`twin_mismatch` for the twin). The times
      of the destinations are not compared (an archive volume may round them); their content is;
    - a row with a `twin` (rule 26a): the twin exists, is not a placeholder, is another file than the reject
      (a different (`st_dev`, `st_ino`)), has the reject's size, and its full hash equals the reject's: reason
      `twin_mismatch`. A reject without a `twin` has no twin to check, and the two archive copies are its keepers.
    - a read of any of these files (the file, a sidecar, a destination, the twin) fails with an operating-system
      error (another program holds it, a bad sector, a drive removed during the read): reason `read_error`; the
      file named is the one whose read failed, and the run goes on with the next row.
    The hash of the file is taken first, then every destination; the file's `stat` is read again just before its
    rename (rule 37), so a file edited during the hashing is `changed`. A read that makes no progress for
    `stall_seconds` ends the run (rule 43).
36. **A family leaves together.** A row is a reject and its sidecars (rule 29). Every file of the family passes the
    tests of rule 35 before any file of it moves; one that fails holds the family, and the reason names the file.
    A row held at `stage` is left exactly as it was and is reported; B2 never stages a part of a family. A sidecar
    that two rows list holds both (`shared_sidecar`, rule 32).
37. **Stage.** For a row whose tests passed, the staged path of a file is `work/staging/<batch>/<drive>/<rest>`: the
    file's path with the volume letter as a folder (`C:\a\b.arw` is `<drive>` `c` and `<rest>` `a\b.arw`), spelled
    as found on disk. The staging folder and the file's folder must lie on the same volume: otherwise the row is
    held (`other_volume`), because a rename across volumes would be a copy. The tool creates the folders of the
    staged path inside `work/staging/<batch>/` and nothing under a library root. A staged path that exists is never
    replaced: the row is held (`staging_exists`). A row whose reject or a sidecar has a link in a folder from
    `photo_root` down to its parent (tested as `undo` tests it, rule 40) is held (`parent_link`) before anything of
    it moves, in a dry run too, so every staged row can be undone. The rename is `os.rename`, which on Windows does
    not replace; the reject first, then its sidecars. After each rename the staged file's size, time and file index
    must equal what the source had just before; if a rename fails or this differs, the files of the family already
    renamed are renamed back, each one journaled, the row is held (`rename_failed`) and the run goes on with the
    next row; if a rename back fails too, the run stops at once, exit 6, and the journal tells `undo` where each
    file is. The source's folder is left as it is, empty or not.
38. **Where B2 may write.** Only: the staging folder, the journal and `out/` inside the tool's folder; and, for a
    rename out of a source or back into it by `undo`, the folder of a path that lies under `photo_root` on the
    volume of the staging folder (R1). It never writes to an archive root, a backup root or the unreliable drive
    (R11): every other file is opened for reading only, and `undo` refuses to write outside the folders above even
    for a journal that names one. Every path B2 writes is tested against these folders before the call, so a wrong
    path is a refusal, not a write. That test reads the spelling of a path, so the folders themselves must be what
    they say: when the tool's folder, `work`, `work/staging`, `work/journal` or `out` exists and is a junction or a
    symbolic link, `stage`, `undo` and `purge` refuse the run, exit 2, before anything is written; `stage` and
    `purge` do so before the work database is opened (`undo` never opens it; `catalogs` opens it first, rule 6).
    The six folders (these five and `work/catalogs`, rule 6) are tested for a link before `work/catalogs` is
    listed, so a link is named as a link even when its target cannot be listed; a folder that is no link and
    cannot be listed is refused as in rule 6.
39. **Journal.** `work/journal/mover-<batch>.jsonl` is created when `stage --apply` starts, never rewritten, only
    appended, and each line is flushed to disk before the step it announces (the writer of rule 21). Its first line
    records the batch, the list's SHA-256 and the approved one, the scan ids of rule 34 and the start time. Per file
    of a row it gets a `begin` line before the rename (`op` `stage`, `row`, `kind`, the original path as spelled on
    disk, and, on the first `begin` of a row only, `family`, the number of files of the row that will be renamed, so
    that a crash before a sidecar's `begin` leaves a trace of that sidecar; `purge` refuses, exit 2, a journal in
    which a row's `family` is missing, not an integer, below 1 or below the number of the row's files, while `undo`,
    which does not use it, reads such a journal; then the staged path, size, time, file index, the SHA-256 of the
    file, each destination with its SHA-256, the twin with its SHA-256) and an `end` line after it (`state`, the
    staged file's size, time and file index). A held row gets one `held` line with its reason. `purge` and `undo`
    append their own `begin` and `end` lines (`op` `purge`, `restore`), with the same row and file. A last line
    without its closing newline is torn by a crash while it was written; it announced nothing, because a line is
    flushed before its step, so every reader ignores it. A writer that finds a torn last line appends a newline and
    then a marker line, `{"event": "repaired"}`, before its own line, so the fragment never becomes a line that a
    later reader mistakes for damage. A reader ignores the line just before a marker, whatever it holds (complete
    JSON included: it never had its newline, so its step never ran), and a last line without a newline, as before;
    any other invalid line, and any line whose event is unknown, stops the command that reads it, exit 2. That is
    the only journal fault that ends a command: a `begin` with no `end` that cannot be placed (rule 40) holds or
    refuses its row (`unplaced`, exit 6, rule 44) and the command goes on with the other rows. `purge` and `undo`
    read the journal and nothing else of `stage`: they trust no earlier output.
40. **Undo.** `undo <batch>` reads the journal and replays it backwards, newest file first, row by row. A row is
    restored only when every one of its files is still staged: the staged file exists with the journal's size, time
    and file index, and its original path does not exist (nothing is ever overwritten); otherwise the whole row is
    refused with the reason (`original_exists`, `staged_missing`, `staged_changed`, `parent_missing`, `parent_link`,
    `purged`, `outside_folders`) and left as it is. The files of a row are those the journal has a `begin` for: a
    row of which a crash left only the reject staged (rule 41, `family`) is restored with what is staged.
    `parent_missing` is a folder that is gone: `undo` creates no folder under a library root. `parent_link` is a
    target with a folder between `photo_root` and its parent, `photo_root` and the parent included, that is a
    junction or a symbolic link (tested as `backup_plan` tests a folder, `is_symlink` or `is_junction`; never by the
    reparse attribute alone, which a sync client's folders may carry), which would send the file to a folder that
    rule 38 never allowed. A file already restored is skipped (a second `undo` finds nothing to do and exits 0). A
    `begin` of `stage` with no `end` (a crash between the journal line and the rename) is placed by looking: the
    original exists and the staged does not (nothing happened: skipped), the staged exists and the original does not
    (it moved: restored as any staged file, with the journal's size and time), both or neither (its row is refused,
    `unplaced`, reported, exit 6; `undo` goes on with the other rows). A `begin` of `purge` or `restore` with no
    `end` is placed the same way: for `purge`, the file is gone (done) or present (not done); for `restore`, the
    file is at the original path (done) or at the staged path (not done). A purged file is gone: `undo` refuses
    every row of a family any file of which was purged (`purged`) and leaves its staged files staged; the owner
    restores the purged files from an archive root by hand, and B2 never writes to an archive root and has no
    restore from one. Each rename back is a `begin` and an `end` line of the same journal, the staged file's folders
    are left in place, and Lightroom need not be closed (a restore changes no catalog). A restored row is not a
    reject check: the owner's next `catalogs` and `rejects` run decides what the restored files are.
41. **Purge.** `purge <batch>` takes from the journal the files that are staged: a file whose `end` line says
    `staged` and has no `restore` or `purge` `end` after it. A second `purge` of a batch therefore finishes a family
    that a crash left partly purged: it lists what is still staged, the owner approves that set's SHA-256 as before,
    and every check below runs again for the files left. Every file of the family that is left is checked first, in
    full: the staged file exists with the journal's size, time and file index and is not an online-only placeholder
    (R4), its SHA-256 is the journal's, every
    destination named by the journal is a file inside its archive root, now of that size and hashing to the same
    SHA-256, and the twin, when the row has one, still exists with that hash. The staged file, each destination and
    the twin are pairwise different files (`st_dev`, `st_ino`), and each destination lies on another volume than the
    staged file and than every other destination, as at `stage` (rule 35). The first `begin` of a row (rule 39) gave
    the number of files of its family: a family of which fewer files are staged (as defined at the top of this rule)
    or were purged by an earlier `purge` than that number is held (`family_incomplete`), as after a crash between
    the reject's rename and a sidecar's, and nothing of it is deleted; a `stage` `begin` with no `end` counts as not
    staged even when looking places it as done, so its family is held. The same holds for a `restore` `begin` with
    no `end` that looking places as not done (the file is still staged): its row is held `family_incomplete`, exit
    `6`, and `purge` says that `undo` places the step; neither is dropped as nothing to purge. A `stage` `begin`
    placed as not done and a `restore` `begin` placed as done leave the file at its original path. Neither entry
    on its own brings a row into `purge`'s checks. A staged file, an open step whose file is in staging, or an
    unplaced entry still brings the row into the checks. A `begin` that cannot be placed (rule 40)
    holds its row (`unplaced`). One failure holds the whole row (`staged_changed`, `archive_mismatch`,
    `twin_mismatch`, `read_error`, `family_incomplete`, `unplaced`, `outside_folders`) and nothing of it is
    deleted; the command goes on with the other rows. Right before the first deletion of a row, every file its checks read (each staged
    file, each destination, the twin) is stat-ed again and must have the size, time, `st_dev` and `st_ino` it had
    when it was hashed; otherwise the row is held with that file's reason and nothing of it is deleted. Then, per
    row, the sidecars are deleted first and the reject last, each as a `begin` line, `os.remove`, an `end` line: the
    deletion is permanent, not a trip to the Recycle Bin (which would free no space). Empty folders below
    `work/staging/<batch>/` are then removed, and nothing else. The purge set's SHA-256 is that of the sorted lines
    `<staged path>\t<sha256>\n` of the files the dry run lists; `--apply` refuses (exit 2) when it differs from
    `--approved-sha256`, so what is deleted is what the owner read. Each command places each journal entry once
    (rule 40); the files listed, the SHA-256 and the deletions all use those same placements.
42. **A dry run changes nothing.** Without `--apply`, B2 creates no file or folder anywhere and renames and
    removes nothing; the one write left is the column migration `store.connect` makes when `stage` or `purge`
    opens the work database (as rule 31). The listing of rule 15 holds for every dry run.
43. **A stalled drive ends the run.** The full reads of rule 35 and rule 41 follow rule 22: no progress for
    `stall_seconds` (default 120; `--stall-seconds` takes only a finite number greater than 0) writes the fact to
    the journal and ends the run, exit 5, with the row in hand untouched (it has not yet been renamed or deleted).
    Once the stall is written the run is stopped for good: a stopped flag, set before the `stalled` line is written,
    makes every later journal write (except that line) and every later step (rename, remove) refuse, and is tested
    again after a step's `begin` line and between the creation of the staged file's folders and its rename, so a
    call that returns late cannot go on to a `begin`, a rename or a removal. The call that stalled may itself still
    complete after the `stalled` line; a step whose `begin` has no `end` is placed by looking (rule 40).
    When the stall ends the run, `stage` and `purge` first write to standard error the line `batch <batch>` and, in an
    apply run, the line `journal <path>` (a dry run of `stage` has no journal and writes only the batch line; neither
    line is preceded by a file lookup, because the stalled drive may be the journal's); standard output is not used,
    because a process ended this way loses what it has not flushed when its output is redirected.
44. **Exit codes.** `0` every processed row was staged, purged or restored (or nothing was to do); `2`
    configuration, list, an invalid journal line (rule 39) or approved SHA-256 refused, or a list file, a batch's
    journal or its `start` line missing; `3` Lightroom is running or a catalog is locked; `4` the evidence is not
    fresh or coverage is incomplete ("run catalogs and rejects again"); `5` stalled; `6` the run finished with at
    least one held or refused row (an `unplaced` one included, rule 40), or stopped on a failed rename back (the
    output names each row), or stopped on a file-system error during `--apply` (a rename, a removal or a journal
    line that fails: the output names the journal that holds the state, or says that none was written and nothing
    moved; the next `undo` or `purge` places an open step by looking, rule 40). A held row is expected and is not
    a fault of the tool: the owner reads it before the next step.
45. **Paired JPEGs (R7) are not in this phase.** B2 moves only the rows of the reject check's list and their
    sidecars (a JPEG that Lightroom records as a raw's sidecar travels with that raw only through rule 29). It
    infers no pairing, and the paired-JPEG list that R7's note asks for is a separate list, with its own plan,
    its own go-ahead from the owner and its own pilot.

### Verification

- At least one test per rule 32 to 45, every one on a temporary tree the test builds, with the archive roots,
  the staging folder and the manifests inside it. Cases per field and per root, as the reject check's: rule 32
  (a copy of an older list, with its own SHA-256 as the approval, is exit 2 and nothing is read; the real file is
  accepted), rule 33
  (each refusal alone, and the order of two), rule 34 (no summary; a scan newer than the summary's; a catalog
  source changed; a role that differs; coverage incomplete), rule 35 (size differs; time differs; placeholder;
  name differs in case; two entries of one casefold name; the first root's copy differs alone; the second root's
  alone; the two copies equal each other but not the source; a destination of another size; the two destinations
  the same file; a destination that is the source; a destination on the source's volume; the two destinations on
  one volume, another than the source's; a twin that differs by
  content, by size, missing, a placeholder, the same file; a sidecar failing
  alone, with the reject sound), rule 36 (each file of a family failing alone leaves every file of it in place),
  rule 37 (another volume; the staged path exists; a rename that fails on the second file rolls back the first; one
  that fails again on the way back stops the run; the staged file's file index equals the source's), rule 38 (a
  path outside the allowed folders is refused for each write: the journal, the staged path, an undo target, a
  purge), rule 39 (the journal line is on disk before the rename, tested by an injected rename that reads the
  journal; the first `begin` of a row has `family`; a torn last line is ignored by `undo` and `purge`; a torn
  last line, then an append, then a read gives the marker line and no error, and again after a second append; a
  torn last line, an append, then a second invalid line is exit 2; an invalid line in the middle with no marker
  after it is exit 2; an unknown event is exit 2), rule 40 (the original exists again; the staged file changed;
  the staged file missing; a purged row; a family partly purged is refused and left staged; a missing parent
  folder; the parent folder, a folder higher up below `photo_root`, and `photo_root` itself, each a junction or a
  link through an injected link test, is `parent_link` and moves; a folder that carries only the reparse attribute
  is not `parent_link`, and moves
  nothing of its row; an unplaced `begin` refuses its row, exit 6, and the other rows are still restored; a
  `purge` or `restore` `begin` with no `end`; a journal of only the reject's `begin` and `end` with `family` 2
  restores the reject; a second `undo`), rule 41 (a journal of only the reject's `begin` and `end` with `family`
  2 is held `family_incomplete` and deletes nothing, while a family partly purged still has its two files
  counted; an unplaced `begin` holds its row and the others are purged; the staged file
  edited; each archive copy edited alone; the twin edited; the purge set's SHA-256 differs; a held row deletes
  nothing of its family; the order sidecars then reject; a second `purge` after a crash that deleted the sidecars
  only; a destination that is the staged file; two destinations that are one file; a destination on the staged
  file's volume; two destinations on one volume), rule 42 (a dry run of each
  command leaves the tree, `out/` and the work folder as they were), rule 43 (a stall in a hash read ends the run
  before the rename; a read that returns after the `stalled` line is written writes nothing more and renames or
  removes nothing), rule 44 (an unplaced row is exit 6 and an invalid journal line is exit 2).
- Round trips on the synthetic tree: `stage --apply` then `undo --apply` leaves a listing of path, size and time
  under the library root equal to the one before, and every file's content equal; a `stage` of the whole list,
  `undo`, then `stage` again gives the same list of staged paths; `purge` removes exactly the staged files and
  the listing then differs from the original by exactly those files.
- Safety of the tests. Every test and every planted-defect run works on a temporary tree. The mover takes
  `rename`, `remove`, `rmdir`, `makedirs`, `listdir`, `append`, `write`, the opener and `stat` as parameters, and
  each test hands it wrappers that raise before the call when a path lies outside the test's own temporary
  folder, so a defect that sends a write to a real folder fails the test first and never reaches the folder. A
  test that builds a path outside its temporary tree fails. The temporary tree lies on one volume, so the tests
  give the injected `stat` its own `st_dev` to each archive root, and another to the source (rule 35's volume
  test would otherwise hold every row); the reviewer's end-to-end run does the same.
- The mover (`lrdup/mover_*.py`, split by the plan) decides what leaves the disk, so it ships a planted-defect
  gate (`tests/mutants/mover.json`, run by `C:/ssw/tools/mutate.py`). It covers at the least these decisions:
  hash equality (source against each archive copy, and `purge`'s hash tests), the same-file test, the list
  approval, the purge-set approval, journal-before-step, and `undo`'s refusal of an existing original. The plan
  picks about five decision-level planted defects from them and from the other rules (the freshness and
  Lightroom refusals, the same-volume test, the family test, the write-folders test), not one per field; each
  defect is one decision, run against the tests that name it. The acceptance cases of rules 32, 44 and 45 are
  the plan's.
- A reviewer runs `stage`, `undo` and `purge` end to end on a synthetic tree, and reads the journal, before the
  real run.
- The hands-on pilot comes before any real batch and has the owner's word for each step: ten rows picked with
  `--only` (including one with a sidecar and, if the list has one, one with a `twin`), a dry run read by the owner,
  `stage --apply`, a listing compared with the one before, the owner's check in Lightroom (the ten are shown as
  missing there; nothing is removed), `undo --apply`, and the listing equal to the first. Opening Lightroom
  changes a catalog source (rule 34), so then `catalogs` and `rejects` run again and give a new list, a new
  SHA-256 and a new approval by the owner; with it `stage` again of the same ten, and, only when the owner says
  so, `purge` of them. The real batch follows only after the owner has seen both, and needs the same: `catalogs`
  and `rejects` again after any opening of Lightroom, and a new approval.
- The real run is the controller's, with the owner told first, Lightroom closed, both archive drives connected and
  the list's SHA-256 the one the owner approved (the list of the last `catalogs` and `rejects` run). The removal
  of the photos from each catalog is made afterwards in
  Lightroom (R17), one owner's click each (R16), from the `rejected_by` column of `out/rejects.csv`; B2 prints
  nothing about catalogs beyond that pointer.

### Known limits

- **Hashing takes hours.** Each file of a row is read in full, once from the system drive and once from each
  archive drive, and again at `purge`, so a large batch reads about six times its bytes. The dry run does not
  read content: the pilot and the first `--apply` of a batch are where the cost is seen.
- **`purge` is not undone.** The two verified archive copies are then the only ones (the twin of a rule 26a row
  stays on the system drive). Between `stage` and `purge` the owner should have removed the photos in Lightroom and
  looked at the staged list; `purge` makes no freshness check (only rule 34's coverage test), because a run of
  `catalogs` after the owner's
  removals would make the list older than the latest scans (rule 34) and could then never be purged. What
  protects the purge is the hash of every file again, the archive roots connected, the owner's approved SHA-256 of
  the purge set and Lightroom closed.
- **A family partly purged cannot be restored by `undo`.** After a crash in `purge` the sidecars may be deleted
  and the reject still staged: `undo` refuses every row of that family and leaves its staged files staged, and a
  second `purge` finishes the family. The purged files live on only as the two archive copies. If that second
  `purge` holds the row (the reject or an archive copy changed), the reject stays staged, refused by `undo` and
  held by `purge`, and the owner settles it by hand.
- **A crash that left only the reject staged.** The journal's `family` count lets `purge` hold such a row
  (`family_incomplete`) and `undo` restore the reject; the sidecar stays in the library. The owner runs `undo`.
- **A crash while the journal is repaired.** A crash after the repair's newline and before its marker leaves the
  fragment as a line with no marker after it, and the reader refuses the journal (exit 2, nothing moves). If the
  marker line itself is torn, the next writer repairs again,
  and the first torn fragment is then followed by an invalid line, not by the marker: the reader refuses the
  journal (exit 2, nothing moves) and the owner settles that batch by hand, as B2 never edits a journal.
- **A drive letter reassigned.** The archive roots are known by the configured path, not by a device. A
  different drive at that letter holds nothing that hashes equal, so every row is held; the controller identifies
  the drives by label and serial before the real run, as for rule 26a.
- **Folders left behind.** `stage` leaves a source folder as it is, empty or not; `undo` leaves the staging
  folders. Neither deletes a folder under a library root.
- **A file open elsewhere.** A program other than Lightroom that holds a file open makes its rename fail; the row
  is `rename_failed` and nothing of it moved.
- **Time of check.** A change made between a file's last hash and its rename is seen only by the `stat` taken just
  before the rename (size, time, file index), as in rule 20. A change that keeps all three, made by a program
  that sets the time back, is not seen; the staged file is hashed again by `purge`, which is where it is seen.
- **Time of check at `purge`.** A change made between the `stat` taken right before a row's deletions (rule 41)
  and a deletion is not seen.
- **Several runs.** Two runs on one list are safe by construction (a rename and a delete of a file already gone are
  refused, never repeated), but they are not meant to run together; each batch is started by hand, once.
- **No record of who flagged.** B2 reads the list and nothing of the flags (rule 24): the owner's approval is of
  the file.

### Not in this phase

Copying a file to an archive drive (B1 did it); removing a photo from a catalog, or any write to a catalog (H2,
R17); the paired JPEG list (rule 45); the loose photos and every other lever (phase D); deleting a catalog file
or an empty folder under a library root; restoring a purged file from an archive drive; a Recycle Bin purge;
changing the reject check (apart from the symmetric hold of a shared sidecar in rule 29, a change to the
reject check made by its own task). Each of these needs its own plan and the owner's word.

## Phase C: catalog tidy (gated)

One folder for the live catalog, one folder per shoot catalog moved whole and unopened, one folder for older
states, unreadable catalogs and empty shells. Nothing is deleted. A copy of the smallest intact catalog is moved
and opened in Lightroom first; the live catalog moves last, and the owner re-opens it from a checklist.

## Phase D1: unrecorded copies (gated)

Phase D1 is the first of the larger levers (R24 to R28). An *unrecorded copy* is a raw or camera JPEG file under
`photo_root` that no live or shoot catalog records and that is byte-identical to another file. The owner keeps the
copy the live catalog records, so no path a catalog records ever changes, and a copy of a reject leaves as the
reject does. A new read-only subcommand `copies` (module `lrdup/copies.py`) writes a removal list in the reject
list's format, `out/copy-removal-list.csv`, and its summary, `out/copies-summary.md`. B2's `stage`, `undo` and
`purge` move that list as they move the reject list (rules 52 and 53), with the same pilot and the same two owner
approvals. Nothing is built or run until the plan is reviewed, read from outside and the owner says so (H5, R28).
The design is the controller's, from the rulings of 2026-11-23. An unrecorded copy lies under `photo_root`; a file
outside it is another matter (report section 10, phase D).

### Subcommands

- `copies`. Reads the work database (the latest `catalogs` and `files` scans, `record`, `image`, `file` and `hash`
  rows), the manifests and verified lists of the archive roots (rule 27), the result files
  `out/purge-<batch>.csv` (rule 46), and the `stat` of the files it tests. It hashes a file in full only as
  rule 48 says. It writes `out/copy-removal-list.csv` and `out/copies-summary.md` (rule 49) and nothing else. It
  refuses as rule 50 says. The owner approves nothing here: the owner approves the list's SHA-256 at `stage`.
- `stage <list> --approved-sha256 <hex> [--only <source>]... [--apply] [--stall-seconds <n>]` takes
  `out/reject-removal-list.csv` or `out/copy-removal-list.csv` (rules 52 and 53). The owner approves the list's
  SHA-256.
- `undo` and `purge` do not change. A batch holds rows of one list; `purge` and its set's SHA-256 are the owner's
  second approval, as in rule 41.

### Rules the code must hold

Each of rules 46 to 53 gets one test that names it; rule 54 cites rules 5, 35 and 46 to 48 and adds no behaviour,
so it gets none. The rules of the reject check and of B2 that the list rests on are cited, not repeated: rules 1 to
3, 5 (coverage), 8, 24 to 27 and 31 for `copies`; rules 32 to 45 for the mover. A rule below says which existing
rule it could not reuse.

46. **A copy and its keeper (R24, R25).** A file is examined when `classify.eligible_files` gives it (a raw or a
    JPEG row in the latest complete `files` scan of a library root, rule 1, not in a preview cache), and either
    shares its size and counted full hash with another file (`reject_twin.load_hashes`, rule 26a condition 1) or,
    for class (b)(i), has the size of a purged reject. It is a *copy* when it passes rule 47. A copy is in one
    class, the first that holds:
    - (a) *A copy with a kept twin.* Another file with its counted full hash and size, that the live catalog
      records as a photo through its absolute form (rule 2) and has not rejected (rule 24), and that exists, is not
      a placeholder and is another file (`reject_twin.twin_candidates`, rule 26a condition 1 and `stat`). The row's
      `twin` is the first such file in path order. A kept twin is never read through the catalog-relative form
      (as rule 26a condition 2).
    - (b) *A copy of a reject.* The row has no `twin`; its two archive copies are its keepers (rule 35). The copy
      is byte-identical (size and SHA-256) either (i) to a reject that `purge` removed: a row of kind `reject` in
      state `purged` in some `out/purge-<batch>.csv`, whose `bytes` and `sha256` the copy's size and full hash
      equal; or (ii) to a file on disk that a catalog with role `shoot` rejects (rule 24), that the live catalog
      does not record in either form, that no catalog keeps (rule 26) and on which no catalog read holds develop
      work or a pick (rule 26, second sentence; rule 26a is not applied). A row of any other kind in a purge
      result, a `copy` row included (rule 53), is never read as a purged reject.
    A copy whose identical files include one the live catalog records and rejects, and none it records and keeps,
    is held `twin_rejected` and is in no class, even where it would match (b). This holds also for a copy the
    builder hashed (rule 48): its identical files are those with the digest the builder took and its size, and
    the hold is applied before a match to a purged reject is accepted. Not rule 26a: that rule lifts a develop
    hold for a reject; a copy has no catalog record and no work, and its twin is the live catalog's own.
47. **What keeps a file off the list (R4, R13, R18, R26).** Each file examined is counted in the summary under the
    first of these that holds, and the list holds none of them. `outside_photo_root`: it lies outside `photo_root`.
    `placeholder` (R4). `recorded`: a catalog read with role `live` or `shoot`, status `ok` or `partial`, not in
    `excluded_catalogs`, records its path in either form, as a photo or as a sidecar, whether it rejects it or not
    (the records of rule 24, not its `keeps` alone; the copies of the live catalog in `old_states` keep nothing,
    R13). `work`: any catalog read, the catalogs in `old_states` included, has an image of it with a pick of 1,
    with develop work, or with a develop state that cannot be read (rule 26, second sentence; rule 26a is not
    applied). `sidecar`: a file with extension `xmp` or `acr`, in its folder with its base name, or named after
    its whole file name plus that extension, is in the same complete `files` scan or recorded as a sidecar by any
    catalog read (either form). `twin_rejected` (rule 46).
    `changed`: its `stat` now differs from its `files` row (size, time, file index) or its file is gone.
    `no_archive_copy`: it lacks a verified copy on every archive root (rule 27, read by
    `reject_archive.archive_state` with that `stat`, all its states under this one name). Last, `no_keeper`: it
    fits no class of rule 46 (its only identical files are unrecorded too, or no purged reject matches it). There
    is no family, because a copy with a sidecar is held, not moved (R26). Not rule 29: that rule moves sidecars
    with a reject; a copy never has one.
48. **Hashes.** Identity is by counted full hash (rule 26a condition 1) or by a full hash the builder takes: for a
    class (b)(i) candidate only, which is a file that passes every test of rule 47 before `no_keeper`, whose size
    equals the `bytes` of some purged reject and that has no counted full hash. It is hashed with
    `hashing.full_hash` after those tests, so no file that is held for another reason is read. A file that cannot be
    read when it is hashed is counted under `changed`. A stored sample hash is never enough (rule 8, R20). `copies`
    never hashes a file whose size no purged reject has and that has no identical counted full hash. Not rule
    26a's hash rule alone: that rule has no purged files, which leave no row in the scans.
49. **The list and the summary.** `out/copy-removal-list.csv` has exactly the header of rule 32 (`source`, `bytes`,
    `mtime_ns`, `sidecars`, `sidecar_bytes`, `dest_1` to `dest_<n>`, `twin`), one row per copy on the list, in path
    order; `sidecars` is empty and `sidecar_bytes` is 0 in every row; `twin` is set for class (a) and empty for
    class (b). `out/copies-summary.md` opens with `Generated:` and the latest `catalogs` scans (kind, id, root,
    status, finished), as the reject summary does, and has no other line of the form `- <kind> scan <id> of`,
    because rule 34 compares every such line with the latest `catalogs` scans; a file that changed or is gone
    since its `files` scan is caught by the `stat` and the hashes of rule 35. It ends with the section
    `## Removal list` and the one line `- SHA-256: <hex>` of the list as written (rule 34). Between them it
    gives, apart for class (a) and class (b), the files and bytes on the list per folder of the copy (the folder,
    then the class totals); the examined files per reason of rule 47 with their bytes; the purge results read and
    the number of purged rejects they hold; and every catalog and root not checked, or `- none`, as rule 30
    does. The summary is written last and removed first (rule 34), so a run that stopped half way leaves none.
    `copies` writes `.tmp` files and replaces them as `write_outputs` does. Not rule 30: the reject check's
    `rejects.csv`, verdicts and sidecar columns have no counterpart here.
50. **Refusals and exit codes of `copies`.** Exit 4 ("run catalogs again"), with nothing in `out/` changed and the
    message of rule 25 about older lists, naming `out/copies-summary.md`, when rule 25 holds (no fresh live
    catalog, a changed source, a changed role) or when catalog coverage is incomplete (rule 5, built as `rejects`
    builds it), because no copy can be called unrecorded then. Exit 2 when `archive_roots` names fewer than two
    roots, `live_catalog` is in `excluded_catalogs`, or a manifest, a verified list or a `purge-<batch>.csv` is
    unreadable or lacks a column of its header (rule 27: an absent manifest or verified list holds every row it
    would decide; an absent purge result means no purged reject). Exit 0 when the files were written, also with an
    empty list. Rule 25's refusal is reused, not rewritten: `copies` calls the same check.
51. **The two lists apart (rule 31).** Rule 31 holds for `copies`: it writes only the two files of rule 49 and
    their `.tmp` files. A run of `rejects` neither makes the copy list stale nor rewrites it, and a run of `copies`
    does the same for the reject list: only a scan that is not the latest makes a list stale (rule 34).
52. **One mover, two lists.** `stage` accepts exactly two names, `out/reject-removal-list.csv` and
    `out/copy-removal-list.csv` (rule 32, which names one; any other path is still exit 2 before a byte is read).
    Each list is paired with its own summary, `out/rejects-summary.md` and `out/copies-summary.md`, and rule 34 is
    made against the summary of the list given. The header, the SHA-256, the approval, `--only`, rule 33 (its
    list name read as this rule says) and rules 35 to 45 are unchanged in what they require. A row of
    `out/copy-removal-list.csv` is a copy: rule 35's twin test applies to it as to a reject (the copy, its twin if
    any, and both archive copies are hashed in full and pairwise different files), and a row with no `twin` is
    checked against its two archive copies alone. The twin is the keeper that stays where it is: it is never a
    source (rule 53).
53. **What the mover refuses or records for a copy.** (1) A list, either list, in which a `twin` is also a
    `source` is refused, exit 2, before any content is read (an addition to rule 32). (2) The mover takes the kind
    of a row from the list's name: the rows of `out/copy-removal-list.csv` carry the kind `copy` in the journal
    (rule 39), in `out/stage-<batch>.csv` and in `out/purge-<batch>.csv`, where the reject list carries `reject`;
    every place of the mover that names the kind `reject` (the first file of a row and the held kind in
    `mover_check` and `mover_stage`; the list's `dest_<n>` test and the twin test of rule 35; the main file of a
    row in `mover_purge` and `mover_undo`) tests `reject` or `copy`, so a `copy` row is checked as a `reject` row
    is and can never be read as a purged reject (rule 46(b)(i)). The archive evidence, the full hashes, the family
    rule, staging, the journal, `undo` and `purge` are not changed. Not rule 36: a copy row has one file, as the
    builder writes it.
54. **R20 here.** Identity is rules 35 and 48; which copy goes is rules 46 and 47. The tool's search for catalogs
    that the last scan did not see (rule 5) covers only the configured `catalog_roots` (R11), so before the real
    run the controller compares those roots with the drives and folders the owner uses. No catalog is written:
    the check of the live catalog after `stage` is the controller's, with a script named in the plan.

### Verification

- At least one test per rule 46 to 53, on a synthetic tree whose synthetic catalogs and archive roots live in the
  test's temporary folder. Cases per field and per root as the reject check's: rule 46 (a copy with a recorded twin
  goes with its `twin`, the first in path order; the twin rejected in the live catalog holds `twin_rejected`; a twin
  recorded only through the catalog-relative form is no twin; a twin that is a placeholder, or the copy's own file
  under another path, is no twin; a copy of a purged reject goes; one whose size or SHA-256 differs from the purge
  row's does not; a purge row in state `held`, of kind `sidecar`, or of kind `copy` is not read; a copy of an on-disk
  reject of a shoot catalog goes; the same reject recorded by the live catalog, kept for work, or kept by another
  catalog does not make a class (b) copy; a class (a) copy that also matches a purged reject has its `twin`);
  rule 47 (one case per reason alone, each against a case where none holds and the copy goes: outside
  `photo_root`; a placeholder; recorded by the live catalog; by a shoot catalog; recorded and rejected by a shoot
  catalog only; through the catalog-relative form only; recorded only by a catalog in `old_states`, which keeps
  nothing; a pick or develop work in an old state, a state that cannot be read; a sidecar on disk, a sidecar only
  in a catalog; no keeper; no verified copy on one root; the file changed since its scan, or gone); rule 48 (a
  file whose size no purged reject has is never hashed by the builder; a file held for another reason is not
  hashed); rule 49 (the header; the classes shown apart with counts per folder; the summary's last line is the
  SHA-256 of the list as written; the summary names no scan but the latest catalogs scans; a run that fails
  leaves no summary); rule 50 (each refusal alone, and the exit code of each); rule 51 (a listing of every walked
  root is equal before and after, and a run of `rejects` between two runs of `copies` leaves the copy list and its
  summary as they were); rule 52 (`stage` of each list with its own summary; of the copy list against the reject
  summary is exit 4; any third name is exit 2); rule 53 (a `twin` that is also a source, in each list; the kind
  `copy` in the journal, the stage and purge results; the twin test on a `copy` row, with a twin and without one).
- A planted-defect gate for the builder, `tests/mutants/copies.json`, one defect per decision: the recorded-by-live-
  or-shoot test, the pick and develop test over every catalog read, the kept-twin test (absolute form, not
  rejected, and the `twin_rejected` hold), the purged-reject test (kind, state, size and SHA-256 together, one
  case per field), the coverage refusal, and the sidecar test. The archive test and rule 25's refusal are reused,
  and the gate of the reject check (`tests/mutants/rejects.json`) already covers them; the sidecar test of rule 47
  is new and gets one defect in `copies.json`. In `tests/mutants/mover.json`, one defect for the twin-is-a-source
  refusal, one for the list-and-summary pairing and one for the kind test of the `dest_<n>` check; nothing else of
  the mover changes.
- A reviewer runs `copies`, then `stage`, `undo` and `purge`, end to end on a synthetic tree before the real one.
- The pilot, with the owner's word at each step as in B2: ten rows picked with `--only` that include a class (a)
  and a class (b) row, a dry run read by the owner, `stage --apply`, a listing compared with the one before,
  the controller's check of the latest live snapshot with the script named in the plan (every path the live catalog
  records is still present; nothing is removed; the owner may also look in Lightroom), `undo --apply`, a listing
  equal to the first; then, if Lightroom was opened, `catalogs` and `copies` again (rule 34), a new approval,
  `stage` again of the ten and, only when the owner says so, `purge`. The whole list is staged after that, every
  path the live catalog records is checked present by the same script, and the purge follows on the owner's word
  (R28). The real run is the controller's, with Lightroom closed and both archive drives connected.

### Known limits

- **File types.** The `files` scan stores raw, JPEG and sidecar files only (`inventory.KEPT_EXTS`), so a HEIC, a
  video or any other type has no row and is never a copy here. The unrecorded-copies pass as ruled (R24, R26) says
  "photo or video file"; extending it needs the scan, `hash` and the report to take those types (phase A rules 8
  and 9), which is a separate change with the owner's word.
- **Identity with a purged reject** rests on the SHA-256 that `purge` recorded and on the builder's full hash of
  the copy; the list does not carry it, so `stage` checks the copy against its two archive copies (rule 35) and not
  against the purged reject.
- **A drive letter reassigned**, and a change made between a hash and the rename, as B2's limits say (rule 35,
  rule 26a). The twin of a class (a) row stays on the system drive; for a class (b) row the two archive copies
  are the only copies left after `purge`.
- **A catalog the tool does not know** (outside `catalog_roots`, R11) cannot keep a file (rule 54).
- **A path alias.** Catalog records and the `files` scan are matched by their spelling (rule 2). A catalog that
  records a file through a junction, a link or a mapped or substituted drive letter that the scan does not follow
  does not hold that file; the live catalog's case is checked present after `stage` (Controller step 6) before
  `purge`, a shoot catalog's is not.
- **A sidecar that appears after the last `inventory`** is in no `files` row and no catalog record, so `copies`
  does not hold the copy and `stage` moves the raw alone; the sidecar stays when its photo leaves.

### Not in this phase

Any write to a catalog (H2); removing a catalog file (R27, phase C); removing an emptied folder; groups of identical
files none of which a catalog records; files outside `photo_root`; a copy with a sidecar (R26); the inbox folder;
the check of the live catalog after `stage` (a controller step with a script, named in the plan, not a tool
feature); the paired JPEG list (R7, rule 45). Each of these needs its own plan and the owner's word.

## Phase D: loose photos and larger levers (gated)

Loose photos move into an inbox folder under the photo root. The larger levers are the owner's choice from the
report: years with a verified external copy, paired camera JPEGs, phone folders, preview caches, in-catalog
duplicates through Lightroom's own finder. A year with no external copy is a backup question before it is a
space question. Phase D1, above, is the first of these levers.

## Not in scope

- Moving the library out of a synced folder, or choosing a cloud backup.
- Any write by the tool to a catalog's database.
- A size survey of non-photo data on the system drive.
