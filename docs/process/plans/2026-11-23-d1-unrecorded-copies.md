# Phase D1 Implementation Plan: the unrecorded-copies pass

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A read-only subcommand `copies` writes `out/copy-removal-list.csv` and `out/copies-summary.md` for the
unrecorded copies of the design (rules 46 to 51); B2's `stage`, `undo` and `purge` move that list as they move the
reject list (rules 52, 53). Nothing of phase C or D is built; nothing is run on a real folder (RC-126).

**Architecture:** `copies.py` decides (pure: `cli.py` loads the rows), `copy_out.py` writes, `mover_list.py` holds
the two lists (the list's name decides its summary, command and the kind of a row's main file), and one task wires
`cli.py` and joins everything in one end-to-end test.

**Tech Stack:** Python 3.13 standard library (`csv`, `hashlib`, `os`, `unittest`); Windows paths.

**Spec:** `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, "Phase D1: unrecorded copies (gated)" at commit
fab61a2 (rules 46 to 54, Verification, Known limits, Not in this phase). Rulings R24 to R28 (`CLAUDE.md`);
controller rulings RC-126 to RC-141 (workspace `context.md`; RC-133 lean, RC-136 the mover).

Sections: Global Constraints=brief, Dispatch=reference, Tasks=brief, Sweep=reference, Spec coverage=reference,
Controller steps=reference, Not in this plan=reference

## Global Constraints

- Lean (RC-133): reuse before new code; each task names what it reuses and leaves unwritten. One test per intended
  behaviour, the design's Verification cases as `subTest` cases of it; no test of wording, none that repeats a
  test of the reject check or B2; rule 54 adds no behaviour and has no test. No `--stall-seconds` (RC-134).
- Python 3.13, standard library only, `unittest`, LF files; tests run as `python -m unittest discover -s tests`.
  No new dependency, option, configuration key or `main` seam.
- PRIVATE DATA and test safety (RC-61): every test builds its tree in `tempfile.TemporaryDirectory()` and touches
  nothing outside it (the mover's `World` fails a test that reaches outside); fixtures are plainly synthetic
  (`shoot-a`, `IMG_0001.ARW`); no real path or name anywhere. A planted defect runs only in the gate's tree copy.
- H2, R1, H3, R4: no catalog is written, no photo moves outside `stage`, nothing is classified by a folder name,
  placeholders are never opened. `copies` writes only the two files of rule 49 and their `.tmp` files.
- Fail closed: any doubt (a `stat` error, an unreadable file, a hash that does not count, missing evidence) holds
  the file under its reason; no reason is dropped silently (RC-129).
- Decision lines are one expression on one line, so a planted defect can replace them; each `old` occurs once.
- Code tasks never commit; the controller lands each patch with the task's subject, no trailer. No pre-commit.

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | AW, AX, AY | the owner's word (Controller steps 1) | 3 code phases + 3 nested gate-runners = 6 |
| 2 | AZ | AW, AX, AY landed | 1 + 1 nested + reviews of AW, AX, AY = 5 |

Peak 6, cap 12. Wave 1 files are disjoint; `cli.py`, `test_cli.py`, `test_e2e.py` are changed by AZ alone,
`mover.json` by AW alone, `copies.json` by AX alone. Landing order AW, AX, AY, AZ. Per patch: `git apply --check
--index`, `git apply --index`, one commit with the task's subject, then `gate-runner` at that HEAD in a clean
detached worktree (L5, L6). One `sdd-reviewer` per task with the same CONTEXT as its implementer. AW and AZ both
land before any `stage` runs on a copy list.

**Frozen interfaces.** An implementer that needs one changed returns NEEDS_CONTEXT.

*Lists (AW), `mover_list.py`.* `ListKind(NamedTuple)`: `name, kind, summary, command`, one line each:
`REJECTS = ListKind("reject-removal-list.csv", "reject", "rejects-summary.md", "rejects")`, `COPIES =
ListKind("copy-removal-list.csv", "copy", "copies-summary.md", "copies")`; `LISTS = (REJECTS, COPIES)`;
`MAIN_KINDS = tuple(item.kind for item in LISTS)`; `LIST_NAME = REJECTS.name` stays. `check_list_path(list_path,
base) -> ListKind` (the one whose `out/<name>` it is; any other path `ListError`, exit 2). `ListRow` gains the last
field `kind`, and `read_list(data, roots, kind)` sets it on every row; both default to `REJECTS.kind`, so the
literal is written once. `_check_paths` also
refuses a row whose `twin` (`paths.norm`) is the `source` of any row of the list (`ListError`, exit 2).
*Gate (AW), `mover_gate.py`.* `gather(..., list_sha256, listing=mover_list.REJECTS)`; the summary read, the pairing
of rule 34 and the freshness messages use `listing.summary` and `listing.command`; `reject_out` is not imported.
*Kinds (AW).* A row's main file has kind `row.kind` (`mover_check._family`, `_Hold`; `mover_stage._original_kind`);
every test of a main file (`mover_check._archived` dest test, `_facts` twin test, `mover_purge` and `mover_undo`
source and sort) reads `kind in MAIN_KINDS`; `"sidecar"` stays a literal.

*Builder (AX), `copies.py`.* `REASONS = ("outside_photo_root", "placeholder", "recorded", "work", "sidecar",
"twin_rejected", "changed", "no_archive_copy", "no_keeper")`, the order of rule 47. `CopyRow(NamedTuple)`: `path`
(the files row's `path_orig`), `bytes`, `mtime_ns`, `dests: tuple[str, ...]` (one per root, from
`reject_archive.archive_state`), `twin: str` (`path_orig`; empty for class (b)). `Purged(NamedTuple)`: `by_size:
Mapping[int, frozenset[str]]` (`bytes` of a purged reject to its SHA-256 values), `results: int` (files read),
`rejects: int`. `Built(NamedTuple)`: `rows: tuple[CopyRow, ...]` in `path_norm` order; `examined: Mapping[str,
tuple[int, int]]` (every reason of `REASONS`, in order, to (files, bytes), zero too); `purged: Purged`. Errors:
`Incomplete(Exception)`, `PurgeResultError(ValueError)`. `read_purged(out_dir) -> Purged` reads each
`purge-<batch>.csv` (`mover_files.valid_batch`; header exactly `mover_journal.COLUMNS`; `utf-8-sig`); a row counts
when `kind == "reject"` and `state == "purged"` (never `held`, `sidecar` or `copy`); only a counted row's `bytes`
must be a whole number and its `sha256` 64 hexadecimal digits (kept in lower case), else `PurgeResultError`; the
empty `bytes` of a `held` row is never read; an unreadable file or a missing column raises `PurgeResultError`; no
file gives `Purged({}, 0, 0)`.
`build(files, views, evidence, hashes, purged, *, photo_root, coverage, stat=os.stat, attrs_of=None,
hasher=hashing.full_hash) -> Built`: `files` are the rows of `classify.inventoried_files` (mappings with
`path_norm, path_orig, ext, size, mtime_ns, file_index, placeholder`), `views` of `rejects.latest_views`,
`evidence` of `reject_archive.load_evidence`, `hashes` of `reject_twin.load_hashes`, `coverage` a
`classify.Coverage`. It
raises `Incomplete(message)` first, before any `stat`, when `coverage.complete` is false.

*Writer (AY), `copy_out.py`.* `LIST = "copy-removal-list.csv"`, `SUMMARY = "copies-summary.md"`;
`write_outputs(out_dir, built, *, roots: int, summary) -> list[str]` returns the list and summary paths; `summary` is
`reject_out.SummaryInput`. `ValueError` when a row's `dests` is not one per root. List header: `source, bytes,
mtime_ns, sidecars, sidecar_bytes, dest_1 .. dest_<roots>, twin`; `sidecars` empty, `sidecar_bytes` 0, `utf-8-sig`.
Summary lines the mover reads (rule 49): `# Unrecorded copies`, `Generated: <time>` (`report.GENERATED`), the
latest `catalogs` scans as `- catalogs scan <id> of <root>: <status>, finished <time>` and no other line matching
`^- \w+ scan \d+ of `, and last `## Removal list`, a blank line, `- SHA-256: <64 hex>` of the list's bytes as
written (`mover_gate._SCAN_LINE` and `_LIST_LINE`).

*Wiring (AZ), `cli.py`.* Subcommand `copies` (no argument), `_COMMANDS["copies"]`. One helper holds the prologue
that `_rejects_run` and `_copies_run` share (two archive roots, live catalog not excluded, views,
`rejects.check_fresh`, coverage, evidence). The helper reads `store.latest_scans(conn, "catalogs")` once, before
`rejects.latest_views`, and both summaries name that read, not a later one; a scan that completes after it makes the
summary name an older id than the latest, and `stage` then refuses (rule 34). Exit 0: files written, an empty
list too. The prologue's refusal for fewer than two archive roots says `archive_roots must name at least two
roots`, which names no command (it replaces "the reject check needs two archive roots", cli.py:394; no test
asserts the old text). Exit 2 (`ConfigError`):
fewer than two roots, live catalog excluded, `ManifestError`, `PurgeResultError`. Exit 4 (`StepMissing`):
`check_fresh` or `copies.Incomplete`, nothing in `out/` changed, with the old-summary note naming
`out/copies-summary.md`. `stage` passes `listing.kind` to `read_list` and `listing` to `_refusals`.

| Task | Change note, sent when it lands | Who is told |
|---|---|---|
| AW | `mover_list` lists, kind, twin refusal; `gather(listing=)`; `mover.json` 9 | AZ |
| AX | new `copies.py`; `copies.json` 6 | AY (the row type), AZ |
| AY | new `copy_out.py`; `reject_out._write_csv` is `write_csv` | AZ |

Gates entry for `constraints.md`; the five existing entries stay unchanged and `mutants-mover` runs again
(`lrdup/mover_*.py` change). Jobs together: 4 x 3 + 3 = 15, at most 16. The `when:` is one line, as the others.

```gates
- name: mutants-copies
  run: python C:/ssw/tools/mutate.py tests/mutants/copies.json --jobs 3 --timeout 300
  pass: exit 0
  quote: first line
  when: any of lrdup/copies.py, lrdup/copy_out.py, tests/test_copies.py, tests/test_copy_out.py, tests/mutants/copies.json changed
```

`mutants-mover` reports 6 caught today, 9 after AW (its six `old` lines still occur once); `mutants-copies` 6.

## Tasks

### Task AW: the mover takes the list's kind and summary (rules 32, 34, 52, 53)

`Wave:` 1 | `Touches:` lrdup/mover_list.py, lrdup/mover_gate.py, lrdup/mover_check.py, lrdup/mover_stage.py,
lrdup/mover_purge.py, lrdup/mover_undo.py, tests/test_mover_list.py, tests/test_mover_gate.py,
tests/test_mover_check.py, tests/test_mover_stage.py, tests/test_mover_purge.py, tests/test_mover_undo.py,
tests/mutants/mover.json | `After:` none | `Mode:` code

**Behaviour:** the Dispatch's lists, gate and kinds. File by file, and nothing else: `mover_list` (table,
`check_list_path`, `ListRow.kind`, `read_list`, the twin test, the header message no longer says "the reject
check's"); `mover_gate` (`_RERUN` built from `listing.command`, line 135's "the one rejects wrote" likewise,
`_summary_text` and `_freshness` take the listing, the `reject_out` import goes); `mover_check` (`_family`,
`_archived`, `_facts`); `mover_stage._original_kind`; `mover_purge` 109 and 110; `mover_undo` 107. Hashes, archive
evidence, family rule, journal, staging and the refusal order stay. Reuses `check_row`, `run_stage`, `run_undo`,
`run_purge` as they are; leaves unwritten a per-list branch and a refusal of a sidecar cell (RC-134). The reject
list's messages stay word for word; `ListRow(...)` calls without `kind` still work. `tests/mover_world.py` is not
touched; a test writes its copy list by hand.

**Interfaces:** produces *Lists*, *Gate*, *Kinds*; consumes none.

**Acceptance (one test per behaviour, cases by `subTest`; existing tests pass unedited):**
1. Lists: each name gives its `ListKind` and rows of kind `reject` or `copy`; a third name, and the copy name
   outside `out/`, is `ListError`; a `twin` that is a `source` is refused in each list, the row not first.
2. Pairing: with the copy list's SHA-256, freshness against a `rejects-summary.md` alone is refused with
   `run catalogs and copies again`, against `copies-summary.md` it passes; the reject list the same the other way
   with today's message.
3. Kind `copy` through `check_row`, `run_stage`, `run_undo`, `run_purge` (`mover_world.World`), one case per test,
   the same row as kind `reject` in each: a listed `dest_2` that is not the evidence's is held `no_archive_copy`; a
   wrong twin is held `twin_mismatch`; a row with no twin is checked against its two archive copies alone; a held
   copy row journals kind `copy` and is named by its source; the `begin` line of a staged
   row with a twin carries that `twin` and `twin_sha256` (the existing begin-line test of `test_mover_stage`, one
   `subTest` with a twin for kinds `reject` and `copy`); the journal, `out/stage-<batch>.csv` and
   `out/purge-<batch>.csv` say `copy`; a sidecar of a copy row still has kind `sidecar`.
4. Planted defects (`mover.json`, pattern `test_mover_*.py`, one per decision): the twin-is-a-source test
   (replaced by `False`); the list-and-summary pairing (the one-line `COPIES` constant given the reject list's
   summary and command); the kind test of the dest check (the `dest_<n>` test of `mover_check._archived` made to
   hold for `reject` only). `mutate.py --jobs 3` run once in the worktree reports 9 caught.

Commit subject: `feat(lrdup): Let the mover take a copy removal list with its own summary and kind`

---

### Task AX: the builder (rules 46 to 48; the coverage refusal of rule 50)

`Wave:` 1 | `Touches:` lrdup/copies.py, tests/test_copies.py, tests/mutants/copies.json | `After:` none |
`Mode:` code

**Behaviour:** `read_purged` and `build` as frozen. Examined: `files` rows whose `ext` is in `paths.PHOTO_EXTS` and
that either sit in a `hashes.by_hash` group of two or more or have a size in `purged.by_size`; any other file is
counted nowhere. Rows of the latest complete `files` scans that share a `path_norm` (a path inside two library
roots) are one candidate, judged once; when their size, `mtime_ns` or `file_index` disagree, the path is counted
under `changed` and not listed. Each is counted under the first reason of `REASONS` that holds (rule 47);
otherwise it is a copy of class (a) or (b) (rule 46), or `no_keeper`; `Built.rows` hold copies only; a `stat` is
taken once per file.
`outside_photo_root`: `backup_plan.is_inside(path, photo_root, below_only=True)` fails. `placeholder`: the row's
flag or `paths.is_placeholder` of the `stat` attributes (`attrs_of` as `reject_archive`). `recorded`: a view with
role `live` or `shoot`, status `ok` or `partial`, not excluded, holds the path in `photos` or `sidecars`. `work`:
`rejects.work_of` is non-empty in any view. `sidecar`: an `xmp` or `acr` row of the same folder and base name is in
`files`, or a view's `sidecars` holds one (`rejects.base_index`). `twin_rejected`: of its
`hashes.by_hash` group the live view rejects one (`rejects.is_rejected`) and none passes class (a)'s test (in the
live view's `absolute` and not rejected); a live record through the relative form only, or as a sidecar, does not
switch the hold off (RC-139). `changed`: the `stat` fails or differs from the row in size, time or
`paths.signed64(st_ino)`. `no_archive_copy`: some root's `reject_archive.archive_state` is not `OK` (its second
values are the dests).
Class (a): `reject_twin.twin_candidates`, the first in `path_norm` order that the live view holds in `absolute`
and does not reject. Class (b), only without a twin: (i) its size is a key of `purged.by_size` and its SHA-256 (the
counted `by_path` full hash when its size, `mtime_ns` and `file_index` equal the files row's, else `hasher(path)`
taken after every reason before `no_keeper`; an `OSError` counts it under `changed`) is in that set; or (ii) a file
of its `reject_twin.twin_candidates` list (it exists, is no placeholder, is another file) is rejected by a `shoot`
view, is in no live view's `photos` or `sidecars`, is kept by no view (`rejects.is_kept`) and has no `work_of` in
any view.
Reuses the `rejects` view functions, `reject_twin`, `reject_archive`, `hashing.full_hash`; leaves unwritten a
second twin finder, family logic and rule 26a. The builder reads no file but through `hasher`, which tests fake.

**Interfaces:** produces *Builder*; consumes `rejects`, `reject_twin`, `reject_archive`, `hashing`, `paths`,
`backup_plan`, `mover_files.valid_batch`, `mover_journal.COLUMNS`. Tests build `CatalogView` literals (the `view`
helper of `tests/test_rejects_verdicts.py`), a literal `HashIndex`, `file` mappings, `stat`, `attrs_of` and
`hasher` fakes; the purge reader is tested on files in a temp folder. The test helper that calls `build` gives
`stat`, `attrs_of` and `hasher` defaults that raise `AssertionError`, so a test that forgets one reaches no real
path.

**Acceptance (L10, L14: one case per field of a multi-field comparison, the offending item not first):**
1. Reasons, each alone beside a clean case that goes: `outside_photo_root`; `placeholder` (the row's flag, and the
   attributes of the `stat`; `hasher` not called for it); `recorded` by the live catalog, by a shoot catalog, by
   either one that also rejects the file (recorded is not kept), through the relative form only, as the sidecar of a
   raw only (a JPEG a catalog lists in `sidecars` with no `photos` record), and not when only
   an `old_states` or an excluded catalog records it; `work` by a pick, develop work, an unreadable state, in an
   old state; `sidecar` on disk and only in a catalog; `twin_rejected` alone; `changed` per field (size, time,
   index, gone; two rows of one path from nested roots that differ in `mtime_ns`, held `changed`, and two equal
   rows, one list row); `no_archive_copy` on the first root only and on the second root only, one `subTest` row each
   (the other root sound; `hasher` not called for it); `no_keeper`; a unique file with no purged size is
   counted under no reason and never statted.
2. Classes: class (a) names the first twin in path order; `twin_rejected` holds even where (b) would match; a twin
   recorded only through the relative form, a twin recorded only by a shoot catalog or only by an old state, a twin
   the live catalog rejects (the copy is `twin_rejected`), a placeholder twin, the copy's own file under another
   path are no twin; a class (a) copy that also matches a purged reject keeps its `twin`; (b)(i) needs `bytes` and
   SHA-256 (one case per field; one row: a `by_path` hash whose `mtime_ns` differs from the files row's is not
   taken, and a faked `hasher` with another hash gives `no_keeper`) and a row `purged` of kind `reject` (cases
   `held`, `sidecar`, `copy`, and a `reject` row in state `open_purge` with valid `bytes` and `sha256`); (b)(ii)
   goes, and does not when the live catalog records the reject, another catalog keeps it, or it has work;
   `Built.rows` come in `path_norm` order; a group with one live-rejected file and one file the live catalog
   records only through the relative form is `twin_rejected`.
3. Hashes: a file whose size no purged reject has, and a file held for another reason, never reaches `hasher`; a
   file that fails to read is counted under `changed`.
4. Refusals: `Incomplete` with no `stat` made; `read_purged` on an unreadable file and on a header lacking a
   column raises `PurgeResultError`; the counts `results` and `rejects`; a result that holds a `held` row (empty
   `bytes`) is read and counts nothing for that row.
5. Planted defects (`copies.json`, pattern `test_copies.py`, six, one per decision; a decision that sits on two
   lines is one defect with two edits, as `rejects.json` allows): the recorded-by-live-or-shoot test, limited to
   `photos` (the `sidecars` half dropped, which the sidecar-only case catches); the work test
   over every catalog read; the kept-twin test (the absolute form and not rejected in class (a), and the
   `twin_rejected` hold); the purged-reject test (the row test of `read_purged`, kind and state, and the size and
   SHA-256 match of `build`); the coverage refusal; the sidecar test. `mutate.py --jobs 3` run once reports 6
   caught.

Commit subject: `feat(lrdup): Add the builder of the unrecorded-copies removal list`

---

### Task AY: the list and the summary (rule 49)

`Wave:` 1 | `Touches:` lrdup/copy_out.py, lrdup/reject_out.py, tests/test_copy_out.py | `After:` none |
`Mode:` code

**Behaviour:** `write_outputs` as frozen. It writes the rows as given, `twin` empty for class (b). After the lines
the Dispatch fixes, the summary shows class (a) and class (b) apart, each with its totals and its files and bytes
per folder of the copy (no such line matches `^- \w+ scan \d+ of `); the examined files per reason of `REASONS`
with their bytes; the purge results read and the purged rejects in them; the catalogs not checked (`excluded`
views, `limiting`) or `- none`. `.tmp` files are written, the summary last and removed first, then all replaced;
an `OSError` removes every `.tmp` and is raised, as `reject_out.write_outputs` does. `reject_out._write_csv` is
renamed `write_csv` (two call sites) and reused; the `.tmp` and replace steps are written once more here.

**Interfaces:** produces *Writer*; consumes `Built` and `CopyRow`, `reject_out.SummaryInput`,
`reject_out.write_csv`, `report.GENERATED`. `lrdup/copies.py` is not in AY's checkout (AX lands first), so the tests
define local `NamedTuple`s with the frozen field names, which the writer reads by attribute; AZ's end-to-end test
joins the real types. AY's `After` stays none.

**Acceptance:**
1. List: the header for two and three roots; `sidecars` empty and `sidecar_bytes` 0; class (a) with its `twin`,
   class (b) without; an empty list is the header alone.
2. Summary: the last two lines are the section and the SHA-256 of the list file's bytes; the only scan lines are
   the `catalogs` scans given; classes apart with per-folder counts; every reason shown, zero too.
3. Failure: a failed write (a folder where a target belongs) leaves no summary and no `.tmp`; a second run
   replaces the first. `tests/test_reject_out.py` passes unedited.

Commit subject: `feat(lrdup): Write the copy removal list and its summary`

---

### Task AZ: the `copies` subcommand and the two-list `stage`, end to end (rules 50 to 53)

`Wave:` 2 | `Touches:` lrdup/cli.py, tests/test_cli.py, tests/test_e2e.py | `After:` AW, AX, AY | `Mode:` code

**Behaviour:** the Wiring. `_rejects_run`'s prologue becomes the shared helper (rejects unchanged in output and
exit codes, except that the two-roots message of the shared helper names no command); `copies` loads
`classify.inventoried_files(conn, library_roots)`, `reject_twin.load_hashes` and `copies.read_purged`, builds,
writes and prints `copies written to <out>: <n> rows`. `_old_rejects_note` takes the summary's name and wraps both
commands. Reuses `_rejects_coverage`, `_stale_catalogs`, `_require_archive_roots` and the `_mover` wrapper; leaves
unwritten a second prologue. The e2e class sits beside `MoverEndToEndTests` on its
own synthetic tree: a live and a shoot catalog in the real layout, two identical raws of which the live catalog
records one, a copy of a file that the shoot catalog rejects and the live catalog does not record (class (b)(ii);
the purge result of class (b)(i) is a hand-written file in AX's tests), two archive roots; the real `catalogs`,
`inventory`, `hash`, `backup-plan`, `backup-run --apply`, `backup-verify`, `copies`, `stage`, `undo`, `purge`
through `cli.main`.

**Interfaces:** produces *Wiring*; consumes every frozen paragraph.

**Acceptance:**
1. Exit codes of `copies` (rule 50; the refusals of the shared helper are held by the tests `rejects` has,
   RC-139): 0 with rows and with an empty list (the "0 with rows" case carries one hand-written
   `out/purge-<batch>.csv` with one `reject`/`purged` row of 100 bytes and SHA-256 H, beside an unrecorded file of 100
   bytes and hash H with two sound archive copies and no twin: the printed row count includes it, and the summary's
   purge-results line reads 1 file and 1 purged reject, so the command passes the result it read to `build`); 2
   for one archive root, for `rejects` and for `copies`, with the same message that names neither command; 2 for an
   unreadable purge result; 4 for incomplete coverage, with a
   listing of `out/` equal before and after and the message naming `out/copies-summary.md`. In the same `copies`
   test, one case: a `catalogs` scan added after the helper's read (patching the build step) leaves a summary that
   names the older scan id; if that needs a new seam, the sentence of the Wiring stands as behaviour and the
   re-review says so.
2. The two lists apart (rule 51): a run of `rejects` between two `copies` runs leaves the copy list and summary as
   they were, and the reverse (a listing of every walked root is equal before and after).
3. End to end: the list holds one class (a) and one class (b) row; `stage --apply`, `undo --apply` (listing equal
   to the first), `stage --apply` again and `purge --apply` remove them; the twin stays; the results say `copy`;
   no catalog's size or time changes.

Commit subject: `feat(lrdup): Add copies and let stage take the copy removal list`

## Sweep

Messages that say "rejects" for a copy list (RC-136): `mover_gate._RERUN` and line 135, `mover_list.py` line 92
(AW), `cli._old_rejects_note` (AZ); the reject list's wording stays, so its assertions stay. Literal `"reject"`
kinds (AW): `mover_check` 152 to 159, 172, 188; `mover_stage` 71; `mover_purge` 109, 110; `mover_undo` 107; the
journal has none. `LIST_NAME` stays for its tests.

## Spec coverage

Rules 46 to 48 and the coverage refusal of 50: AX. Rule 49: AY. Rules 50 (exit codes) and 51: AZ. Rules 52 and 53:
AW, joined in AZ. Rule 54, the reviewer's run and the pilot: Controller steps. Planted defects: 6 in AX, 3 in AW.

## Controller steps

1. Before any dispatch: the owner's outside read of this plan (R28) and the owner's word (RC-126, RC-138 10).
   Nothing of D1 is dispatched, built or run before both.
2. Then, before dispatch: add the `mutants-copies` entry to `constraints.md`; give every implementer `context.md`
   (RC-126 to RC-141).
3. After each landing: the change note, then `gate-runner` at that HEAD in a clean detached worktree
   (`mutants-mover` reports 9 after AW, `mutants-copies` 6 after AX).
4. After AZ: a reviewer runs `copies`, `stage`, `undo` and `purge` by hand on a synthetic tree with a catalog in
   the real layout; the controller reads one list, its summary and one result file (L12, L13).
5. Before the real run (rule 54): no Lightroom, no sync client; the drives identified by label and serial (the tool
   stores no device identity); the configured `catalog_roots` compared with the drives and folders the owner uses
   (a catalog outside them keeps nothing, R11); `catalogs`, `inventory`, `hash` and `backup-verify` the latest,
   both archive drives connected. A fresh `inventory` makes `hash` read every file again; the owner is told. The
   controller lists the junctions and substituted drives on the system drive and checks that none points into
   `photo_root` from a path a catalog root uses. `work/config.json` is not edited between `copies` and the last
   `stage` of its list; after an edit, `copies` runs again (the stage gate does not bind `excluded_catalogs`).
6. The controller's script (RC-132; read-only, in `controller-scripts/`, not a tool feature): it opens the work
   database with `mode=ro`, takes the live catalog's row of the latest `catalogs` scan, prints its `snapshot_path`
   and `finished`, reads that row's non-sidecar `record` paths (what `catalogs` read from that snapshot), stats
   each, and prints the counts recorded, present and missing and every missing path. Run before `stage` and after
   each `stage --apply`: the missing sets must be equal (the live catalog may already record missing paths); a
   newly missing path stops the run. It opens no catalog file.
7. The pilot, the owner's word at each step: ten rows by `--only` with a class (a) and a class (b) row; the dry run
   read by the owner; `stage --apply`; a listing compared with the one before; the script of step 6; `undo
   --apply`; a listing equal to the first. If Lightroom was opened: `catalogs` and `copies` again (rule 34), a new
   approval, `stage` of the ten, and `purge` only on the owner's word.
8. The whole list, the script of step 6 again, and `purge` on the owner's word (R28). The list's SHA-256 and the
   purge set's SHA-256 are the owner's two approvals.
9. Docs after the build (the controller's): the README's subcommand and safety lines for `copies`, the README line
   that a fresh `inventory` makes `hash` read every file again, the phase rows. No private name.

## Not in this plan

- The design's "Not in this phase": any write to a catalog (H2); removing a catalog file (R27, phase C); removing
  an emptied folder; groups of identical files none of which a catalog records; files outside `photo_root`; a copy
  with a sidecar (R26); the inbox folder; the check of the live catalog after `stage` as a tool feature; the paired
  JPEG list (R7).
- Widening the `files` scan to HEIC or video files (it keeps raw, JPEG and sidecar files only).
- A check of a copy against the purged reject at `stage` (a known limit); `--stall-seconds` and exit 5 for
  `copies`.
