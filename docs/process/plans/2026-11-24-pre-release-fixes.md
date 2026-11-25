# Pre-release safety fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three defects found before the public release (issues 6, 7 and 5), and bring the release design in line with the verified rows of its outside read. No new feature, option, configuration key or exit code.

**Architecture:** Three code tasks in three modules (`catalogs.py`, `backup.py`, `copies.py`), each a merge into a check the module already has (BC also widens the existing link check in `mover_gate.py` and calls it from `cli.py`), and one documentation task on the release design. The code tasks touch disjoint files.

**Tech Stack:** Python 3.13 standard library (`os`, `sqlite3`, `unittest`); Windows paths and junctions.

**Spec:** `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md` (rules 6, 18, 23 and 46 are changed; rule 48 is read; all at HEAD dba1d8a) and `docs/superpowers/specs/2026-11-24-public-release-design.md`. Rulings: workspace `context.md`, RC-153 (scope), RC-61 (safety in tests). Findings: workspace `outside-read-release/verify-V1.md` to `verify-V4.md`.

Sections: Global Constraints=brief, Review Focus=reference, Dispatch=reference, Tasks=brief, Spec coverage=reference, Controller steps=reference, Not in this plan=reference

## Global Constraints

- Branch `fix/pre-release-safety`, cut from `main` at dba1d8a. One pull request, before any release prep. It closes issues 5, 6 and 7.
- Lean (RC-153): a fix that merges into an existing check beats a new branch; a case added to an existing parametrized test beats a new test; one test per intended behaviour; no test that only pins a message. Each code task names what it reuses and what it adds nothing of.
- Python 3.13, standard library only, `unittest`, LF files; tests run as `python -m unittest discover -s tests`. No new dependency, seam parameter, configuration key or option.
- PRIVATE DATA (RC-61): every test builds its own tree in `tempfile.TemporaryDirectory()` and touches nothing outside it; fixtures are plainly synthetic (`shoot-a`, `IMG_0001.ARW`); no real path or name in code, tests, documents or reports. A test that needs a junction creates it inside its own temporary folder with `cmd /c mklink /J`, asserts that the command succeeded (as `tests/test_mover_world.py` `junction` does) and never skips silently; its cleanup removes the link only (`os.rmdir`), never the target.
- Test inputs: values that could be swapped are not equal, and the test asserts that precondition (L10: a decision that compares two fields gets a case per field).
- A new or edited decision line is one expression on one line, so a planted defect can replace it. Planted defects only where RC-153 allows: one for BD in `tests/mutants/backup.json`, one for BE in `tests/mutants/copies.json`, each a single-line `old` occurring once in its file. None for BC (`catalogs.py` has no mutants file and gets none).
- Code tasks never commit and never edit `docs/**` (constraints.md, Shared files). The numbered-rule clauses each code task gives below are applied by the controller in the task's landing commit.
- No attribution trailer. New sentences in documents never say "the owner"; they say what a change does, or name Peter.

## Review Focus

- BC: a link at the snapshot folder, at `<work>/catalogs` and at `<work>` stops `catalogs` with exit 2 before any catalog is touched; a link above the work folder that resolves into the catalog's folder leaves the catalog `unreadable` and writes nothing; the folder lists `link_refusal` tests are widened, not duplicated; a sync folder that carries the reparse attribute without being a link must still snapshot (a reviewer check: no test can build one).
- BD: a placeholder destination beside an unchanged ordinary one; a placeholder source (still `changed`, nothing opened); the journal skip, which already opens nothing.
- BE: the hold is applied with the digest the builder took, not the stored one; the file is hashed once; a rejected file of another size or digest, or a kept file with the same digest, does not hold the copy.
- BF: no new sentence says "the owner"; no row decides anything the verify file or RC-153 did not.

## Dispatch

| Wave | Tasks | Starts when | Agents |
|---|---|---|---|
| 1 | BC, BD, BE | immediately | 3 code phases + 3 nested gate-runners = 6 |
| 2 | BF | BC, BD, BE landed and the stack's gate run is green | 1 serial implementer, no gate |

Peak 6 (cap 12); reviews of BC, BD, BE overlap wave 2 and the stack's one gate run, which brings the count to at most 7. BC, BD and BE touch disjoint files (`lrdup/catalogs.py`, `lrdup/mover_gate.py` and `lrdup/cli.py`; `lrdup/backup.py`; `lrdup/copies.py`; their test files, `tests/test_mover_gate.py` and `tests/test_cli.py` among them, and two mutants files; BD and BE need no change in `cli.py` or `mover_gate.py`), so no patch needs another. Landing order: BC, BD, BE as a stack (`git apply --check --index <patch>`, `git apply --index <patch>`, one commit per task with the task's subject, no trailer), then ONE gate run at the top of the stack in a clean detached worktree (unit, `mutants-mover`, `mutants-backup`, `mutants-copies`; the `when:` lines of constraints.md select them). BF lands last. A failing gate at the top is attributed to a task by re-running the unit suite at each commit of the stack.

**Frozen interfaces.** An implementer that needs one changed returns NEEDS_CONTEXT.

- `catalogs.snapshot_catalog(source: str, work_dir: str, processes=running_processes, stat=os.stat) -> str`, `catalogs._store_catalog(...)` and `catalogs.scan_catalogs(...)` keep their signatures. The resolved-containment refusal is the existing `ValueError`.
- `mover_gate.link_refusal(base, is_link) -> Refusal | None` (exit 2, one message) is what BC widens: it gains a third parameter `listdir` (the `Seams.listdir` its callers already hold) to list `work/catalogs` through `paths.fs_path(...)`, and a missing `work/catalogs` has no entries (any `FileNotFoundError` from `listdir`; no other error is swallowed). `link_refusal(base, is_link, listdir)` has no default for `listdir`; its two callers, `cli._mover_ctx` and `_catalogs`, pass `run.seams.is_link` and `run.seams.listdir`; the existing test call is updated. `mover_gate.Refusal` and `cli._refuse` are unchanged. `mover_seams.REAL.is_link` (`os.path.islink(p) or os.path.isjunction(p)`) stays the link test; `catalogs.find_catalogs` already skips `entry.is_symlink() or entry.is_junction()`.
- `backup.copy_row`, `backup.run_manifest`, `backup.dry_run` and `backup._verdict` keep their signatures. Row states stay strings in `backup.py`; one is added: `DEST_PLACEHOLDER = "dest_placeholder"`, used by `copy_row` and, as a dry-run verdict, by `_verdict`. `backup_verify.py` and its state `unreadable` for a placeholder destination are unchanged.
- `copies.REASONS` and the keys of `copies._HOLDS` are unchanged; `copies.build` and `copies.Built` are unchanged.

Change notes: the three code tasks depend on none of each other, so landing one sends no note to the others. BF depends on no code (it edits one document) and is told only that the three landed.

Gates for the three code tasks (names from constraints.md; none is added):

| Task | Gates it needs |
|---|---|
| BC | `unit`, `mutants-mover` (its `when:` names `mover_gate.py`; no new planted defect, the file's count is unchanged) |
| BD | `unit`, `mutants-backup` (9 caught after BD, 8 today) |
| BE | `unit`, `mutants-copies` (7 caught after BE, 6 today) |
| BF | none (a document; the controller reads it) |

## Tasks

### Task BC: the catalog snapshot never writes into the catalog's own folder (issue 6, outside-read row A01)

`Wave:` 1 · `Touches:` lrdup/mover_gate.py, lrdup/cli.py, lrdup/catalogs.py, tests/test_mover_gate.py, tests/test_cli.py, tests/test_catalogs.py · `After:` none · `Mode:` code

**Behaviour (RC-155).** Two refusals that differ on purpose, both before the `catalogs` command removes, creates or copies anything (the work database, which `main` opens before every command, is the one thing already opened; see Acceptance 2 (c)). (a) `mover_gate.link_refusal` already refuses, with exit 2 and one message, a link at the tool's folder, `work`, `work/staging`, `work/journal` and `out`, for the mover commands. It also tests `work/catalogs` and every entry directly inside `work/catalogs` (each snapshot folder), listed through the `listdir` it is given. (b) The `catalogs` command calls it through `cli._refuse(mover_gate.link_refusal(run.base, run.seams.is_link, run.seams.listdir))` before it scans, as `_mover_ctx` does. So a link at `work`, `work/catalogs` or a snapshot folder stops `catalogs` with exit 2 and the existing message ("... is a link (junction or symbolic link): the tool's folders must be real folders"), before any catalog is touched. `catalogs` thereby also refuses a link at the tool's folder, `work/staging`, `work/journal` and `out` (it did not before), as the mover commands do; accepted (RC-155 (1)). No new exception class is written. Today only the `_mover` wrapper turns a `Refusal` into `_fail(exc.code, exc.message)`; `main` has no clause for it on `catalogs`, so the `Refusal` would escape as a traceback. BC therefore moves the existing `except mover_gate.Refusal as exc: return _fail(exc.code, exc.message)` clause from `_mover`'s `guarded` to `main`'s `try`, beside `ConfigError`, so there is one copy of that handling and the mover commands behave as before; this is a deviation from RC-155's "no `except` clause" wording (RC-156 (1)), forced by `main`, and the implementer names it in the report. The mover commands now also refuse a link at `work/catalogs` or a snapshot folder; that is the same check and is accepted. (c) `snapshot_catalog` gains one check, before the `os.makedirs` of the snapshot folder: when the snapshot folder with links resolved is, or lies inside, the resolved folder of the source (compared as `paths.norm` compares), it raises the existing `ValueError`. `_store_catalog` already turns a `ValueError` into a catalog stored as `unreadable` with an empty `snapshot_path`, as the work-inside-the-catalog-folder case does today. This is the defence at the point of removal for a direct call and for a link above the work folder. Cause today: `os.makedirs(..., exist_ok=True)` accepts a junction as the snapshot folder and the "clear the old snapshot" loop then deletes the original `.lrcat`, `-wal` and `-shm` through it, after which `sqlite3.connect` creates an empty file at the original's path (verify-V1, A01, lines 99-107 and 286 of `catalogs.py`). A junction at `<work>/catalogs` or at `<work>` today writes a snapshot folder into the catalog's folder (no loss, but a write).

**Reuses, adds nothing of.** The function and the wiring the mover commands already have: `mover_gate.link_refusal`, `cli._refuse` and the `Seams.is_link` and `Seams.listdir` seams; the test is the link test, not the reparse attribute alone (a sync folder carries it). No new exception class, exit code, seam or helper family; `catalogs.py` imports nothing new for the link case. The resolved containment is the same comparison as the existing `work`/`home` test at the top of `snapshot_catalog`, applied to resolved paths too. No change to `find_catalogs`. A link at one of those folders stops the run, also one that points away from the catalog's folder, as RC-155 rules (a catalog that silently becomes "not read" protects nothing).

**Rule 6 clause (the controller adds it to `docs/superpowers/specs/2026-11-11-lr-cleanup-design.md`, rule 6, after "The original is opened by nothing but the copy."):** "The `catalogs` command stops with exit `2`, naming the folder, when `work/`, `work/catalogs/` or a folder directly inside `work/catalogs/` is a link or junction, or any other folder the mover commands already refuse, before any catalog is touched. A snapshot whose folder, with links resolved, is or lies inside the catalog's own folder is refused as `unreadable` (rule 7). Before either refusal nothing is removed, created or copied by the `catalogs` command; the work database, which every command opens first, is opened (and created, if new) where `work/` leads, so with `work/` itself a link it is opened inside that folder."

**Interfaces:** consumes `mover_gate.link_refusal`, `cli._refuse`, `Seams.is_link`, `Seams.listdir`, `paths.norm`; changes `link_refusal` to `link_refusal(base, is_link, listdir)` (see Frozen interfaces); produces none.

**Acceptance (RC-155 (2); the junction rules of Global Constraints apply):**
1. `tests/test_mover_gate.py`, `LinkRefusalTest.test_rc103_each_linked_tool_folder_is_exit_2_and_a_missing_folder_is_none` is the existing test of `link_refusal`: a `subTest` loop over a `folders` list, not a parametrized table. Extend it, no new test: add `work/catalogs` and one entry inside it (the test passes a `listdir` that returns that entry's name for `work/catalogs`) to `folders`, each expecting code 2 and the folder in the message; the call `link_refusal(base, lambda path: False, ...)` still returns `None`, and a `listdir` that raises `FileNotFoundError` (no `work/catalogs`) still gives `None`. The five folders of today stay as cases.
2. `tests/test_cli.py`, one test in the class that holds the `catalogs` order tests (`OrderTests`, using `run_cli` and `CliCase`), with `subTest`s for three layouts, each in a fresh synthetic tree: the original catalog `shoot-a.lrcat` has a `-wal` file of 4096 bytes of `W` and a `-shm` file of 1024 bytes of `M` (sizes and byte values differ, asserted). (a) a snapshot folder `<base>/work/catalogs/shoot-a-x` is a junction (real `mklink /J`, command result asserted) to the catalog's folder; (b) `<base>/work/catalogs` is a junction to it; (c) `<base>/work` is a junction to it (`config.json` is put in the catalog's folder first, as the command reads it there). `run_cli("catalogs")` returns 2, the error text contains the linked folder's path, and the original's three files are byte-identical (size, mtime, SHA-256). In (a) and (b), `sorted(os.listdir(<catalog folder>))` is also unchanged, so a folder made before the refusal fails the test (`listing` walks file names only and cannot see an empty folder). In (c) the catalog's folder is `work` itself, so it also holds the tool's own `config.json` and `lrdup.sqlite` (`main` opens the work database before any command runs); that is not a failure and the listdir check is not made there.
3. `tests/test_catalogs.py`, one test over `Tmp`, `synth.make_catalog`, `listing` and a real junction: `<tmp>/up` is a junction to the catalog's folder and `work_dir = <tmp>/up/w`, with no link at `w`, `w/catalogs` or the snapshot folder (the case `link_refusal` cannot see). A direct `snapshot_catalog` call raises `ValueError` and `listing(<catalog folder>)` AND `sorted(os.listdir(<catalog folder>))` equal what they were before. The `-wal` and `-shm` files are as in 2.
4. The existing `test_work_dir_inside_the_catalog_folder_is_refused_and_nothing_created_rule_6`, `test_work_dir_inside_the_catalog_folder_gives_an_unreadable_row_rule_6`, `test_a_directory_link_is_not_entered_rule_14` and the existing ordinary snapshot tests pass unchanged (they cover "an ordinary work folder still snapshots"); the existing mover refusal tests in `tests/test_cli.py` pass unchanged. `tests/mutants/mover.json` is run by the gate; its `old` lines in `mover_gate.py` still occur once each (the implementer checks, and adds no planted defect, RC-153).

Commit subject: `fix(lrdup): Never snapshot a catalog into its own folder or via a link`

---

### Task BD: the backup copy never opens a destination that is an online-only placeholder (issue 7, row A02)

`Wave:` 1 · `Touches:` lrdup/backup.py, tests/test_backup.py, tests/mutants/backup.json · `After:` none · `Mode:` code

**Behaviour.** In `backup._copy`, in the branch where the destination exists (after `source_changed`, where it hashes both files today), the destination's attributes are read first through the `stat` and `attrs_of` seams the function already receives (`attrs_of(path, st) if attrs_of else getattr(st, "st_file_attributes", 0)`, then `paths.is_placeholder`, as `source_changed` and `backup_verify` do). When the destination is a placeholder the function returns `DEST_PLACEHOLDER, ""` before it opens either file; the destination is left as it was and no part file is made. `exit_code` counts the state with `conflict`, `changed` and `failed`: exit 6. `_verdict` (the dry run) returns `"dest_placeholder"` for a row whose destination exists, is not vouched for by the journal, and is a placeholder; where the journal vouches for the destination (`already_verified`) the run opens nothing, so that verdict stays. A source placeholder stays `changed` / `skip_placeholder`; the checks on the source are not touched.

**Reuses, adds nothing of.** One small helper in `backup.py` holds the placeholder test of a destination and is called by `_copy` and `_verdict` (the test is one expression on one line, the planted defect's target); no new seam, no change to `backup_verify.py`, `backup_plan.py` or `cli.py`, no new constant but `DEST_PLACEHOLDER`.

**Rule clauses (the controller adds them to the 2026-11-11 design):** rule 18, after "and the destination is left as it was in both cases": "A destination that is an online-only placeholder (rule 7) is never opened: the row is `dest_placeholder`, the destination is left as it was, and the dry run says `dest_placeholder`." Rule 23: add `dest_placeholder` to the states that exit `6`. Known limits, "During a comparison": replace the sentence "An existing destination that is an online-only placeholder is opened by `backup-run` and refused as `unreadable` by `backup-verify`." with "An existing destination that is an online-only placeholder is refused by `backup-run` as `dest_placeholder` and by `backup-verify` as `unreadable`; neither opens it."

**Interfaces:** produces `backup.DEST_PLACEHOLDER` and the dry-run verdict `"dest_placeholder"`; consumes `paths.is_placeholder`, `paths.fs_path`.

**Acceptance (in `tests/test_backup.py`; cases added to the existing parametrized tests where one fits):**
1. A row `dest_placeholder` in the dict of `test_rule17_20_every_state_leaves_the_source_untouched_and_only_reads_it` (`tests/test_backup.py`, state -> (row, seams)): the destination has different bytes from the source (so a conflict would be the wrong answer), and `attrs_of` returns `0x400000` for the destination path and `0` for the source (asserted unequal); `copy_row` returns `("dest_placeholder", "")` and an opener mock records that no path was opened, neither source nor destination. The same row with `attrs_of` giving ordinary attributes for both returns `conflict` (the existing rule 18 test stays as it is).
2. `ExitCodeTest.test_rule23_each_bad_state_alone_exits_6`: `dest_placeholder` is added to its tuple of states.
3. `DryRunTest.test_b8_each_verdict_arises_and_nothing_is_created`: one more row, an existing destination whose `attrs_of` is a placeholder, expects `(n, "dest_placeholder")`; the listing before and after is equal.
4. Planted defect (RC-153), one in `tests/mutants/backup.json`: name "destination placeholder test dropped"; `old` is the helper's one-line test expression, `new` makes it `False`; both the `copy_row` case and the dry-run case must fail on it. The helper's one line is not a substring of line 68 of `backup.py` (the `source_changed` placeholder test), so the new `old` occurs once. `mutate.py ... backup.json --jobs 3` run once in the worktree reports 9 caught; the 8 existing `old` lines still occur once each.

Commit subject: `fix(lrdup): Never open a backup destination that is an online-only placeholder`

---

### Task BE: the rejected-twin hold uses the digest the builder computed (issue 5, Codex P2 on PR 4)

`Wave:` 1 · `Touches:` lrdup/copies.py, tests/test_copies.py, tests/mutants/copies.json · `After:` none · `Mode:` code

**Behaviour.** For a candidate with no counted full hash whose size equals a purged reject's (class (b)(i)), `_judge` today takes the builder's digest (`_sha`) only to test the purged match; `_twin_rejected` reads the group of identical files from the stored hash, which such a candidate lacks, so the hold `twin_rejected` never applies and a copy of a file the live catalog rejects (and keeps nowhere) can go on the list. After the fix, when `_purged_match` is true, `_judge` applies the same hold with the digest it just took, before the row is accepted: the identical files are those the hash index holds under `(digest, size)`; held when the live catalog rejects one of them and keeps none; the reason is `twin_rejected`. The file is hashed once (the one `hasher` call of today), and the order of `REASONS` and the keys of `_HOLDS` do not change. A candidate that has a counted hash behaves exactly as before.

**Reuses, adds nothing of.** `_twin_rejected` and `_group` take the digest as a value (the counted hash is the digest for a counted candidate) instead of a second function; `_judge` gains one decision line, written as one expression on one line. So that the file is hashed once, `_purged_match` returns the digest or `None` (not a bool), `_judge` passes that digest to `_twin_rejected`/`_group` as a value, and `_HOLDS` keeps calling `_twin_rejected(c, w)` as today. No new reason, no new class, no new hash call.

**Rule clause (the controller adds it to the 2026-11-11 design):** rule 46, last paragraph, after "even where it would match (b)": "This holds also for a copy the builder hashed (rule 48): its identical files are those with the digest the builder took and its size, and the hold is applied before a match to a purged reject is accepted."

**Interfaces:** consumes `reject_twin.HashIndex.by_hash`, `rejects.is_rejected`, `copies._keeps`; produces none.

**Acceptance (in `tests/test_copies.py`, built on `Scene`; fixture names `X`, `TWIN`, `COPY`):**
1. One row added to the existing `CASES` table in `tests/test_copies.py` (consumed by `test_each_reason_alone_holds_the_file_and_the_clean_copy_goes`; no new test): `('twin_rejected, the copy hashed by the builder, where (b) would match', lambda s: _twin_rejected(s, 'hashed by the builder'), {'twin_rejected': 1, 'recorded': 1})`, and a `'hashed by the builder'` branch in the `_twin_rejected(scene, how)` helper: the candidate `y` is added with `full=None`, `scene.hash_of[N(y)] = 'bb'` and `scene.purged = purging(sha='bb')`, and the rejected twin `r` keeps counted hash `'bb'` and the same size (asserted: the builder's digest equals the purged digest and differs from the stored `'aa'`). In that case `len(scene.hash_calls) == 1`. One negative case in the same file, where `r` has another size (asserted unequal), so `y` is listed.
2. Without the fix the new row lists `y` and fails; the existing `twin_rejected where (b) would match` row and `test_class_b_i_needs_the_purged_size_and_sha256_and_trusts_only_a_matching_counted_hash` stay green unchanged.
3. Planted defect (RC-153), one in `tests/mutants/copies.json`: name "rejected-twin hold skipped for a builder-hashed copy"; `old` is the new `_judge` decision line, `new` makes its hold part false. `mutate.py ... copies.json --jobs 3` run once in the worktree reports 7 caught; the 6 existing `old` lines still occur once each.

Commit subject: `fix(lrdup): Apply the rejected-twin hold with the digest the builder computed`

---

### Task BF: the release design takes the verified outside-read text

`Wave:` 2 · `Touches:` docs/superpowers/specs/2026-11-24-public-release-design.md · `After:` none (lands after BC, BD and BE) · `Mode:` serial, no gate

**Behaviour.** The implementer applies the text named below to the design and decides nothing: where a row names a verify file, the sentence to use is under the row's own heading there ("Smallest change"), copied with its wording kept except where this section says otherwise. `docs/**` belongs to the controller (constraints.md); the dispatch of this task names the exception. Line numbers are the design's at dba1d8a; find each place by its text. Nothing else in the design changes, and no new sentence says "the owner" (the name is Peter). The verify files are in the private workspace and are not linked from the design.

Rows, in order (V2 = `verify-V2.md`, V3, V4 likewise; headings are `## <row>`):

| Row | Design place | Text |
|---|---|---|
| A03 | section 6, item 6 (the parenthesis) | V2 A03, the full replacement of the parenthesis (eight checks) |
| A04 | section 6, after the `docs/guide.md` sentence | V2 A04, the two-README-statements sentence. The promise in item 3 is unchanged |
| A05 | section 6, item 3, "a verified copy on each archive drive" | Narrowed, not widened (RC-153): "a verified copy on each of two archive volumes (separate drive letters and volume serials; that they are separate physical drives is yours to check, see below)" (V2 A05). Item 6's label-and-serial check stays as the place that covers it |
| A06 | same sentence as A04 | V2 A06, the clause for `excluded_catalogs`, and the key-table cell |
| A07 | section 4, the AGENTS.md bullet's parenthesis, and section 11 | V2 A07, the replacement parenthesis and the one added line in section 11 |
| A08 | section 4 table and the AGENTS.md bullet | V2 A08: add `.gitignore` and `.gitattributes` under Included; reword "A local, ignored `CLAUDE.md`" as V2 gives |
| A09 | section 4, the `docs/process/` note, and section 5 step 2 | V2 A09, the note sentence and the link-rewrite clause |
| B01 | section 7, `ci.yml` | V3 B01: the literal command `python -m unittest discover -s tests` in place of "plain `unittest` discovery" (also in section 9 risk 1 where the words occur) |
| B02 | section 5 step 2 (CI bullet) and section 9 risk 1 | RC-153 ruling: two tests (`test_cli.py` base-is-a-root, `test_catalogs.py` work-dir inside the catalog folder) need the checkout and the temporary folder on one drive; fixing them is a task of the release-prep branch's CI work, not of CI configuration. Say so in one sentence; V3 B02 gives the names and the guarded-skip pattern |
| B03 | section 7, `ci.yml` | V3 B03, as ruled (RC-154): the design says the CI job has one step that prints the registry value `LongPathsEnabled` and sets it to 1 before the tests, because three test groups create paths over 260 characters without the extended prefix |
| B04 | section 7, `ci.yml` | V3 B04, as ruled (RC-154): the design makes no claim about link coverage; it says the job's log shows the suite's summary line, skipped count included |
| B05 | section 5 step 2 and section 9 risk 1 | RC-153 ruling: a one-second wall-clock assertion in `test_backup.py` is a test fix of the CI task (V3 B05: loosen to 30 s); the design says so in the same sentence as B02 |
| B06 | section 5 step 2 (the rename bullet) | RC-153 ruling: `lrdup.sqlite` and `.lrdup-part` ARE renamed with everything else (to `lrclean.sqlite` and `.lrclean-part`); the design adds that the one existing work folder needs its database renamed once by hand and any leftover `*.lrdup-part` files removed from the archive folders before the first `lrclean` run. V3 B06 gives the facts |
| B07 | section 5 step 2 and the `docs/process/` note | V3 B07: "except `docs/process/`" in the rename sweep, and the one-line note that the command was `python -m lrdup` then and is `python -m lrclean` now |
| B08 | section 7, `release.yml` | V4 B08: the build step installs the built wheel into a fresh virtual environment outside the checkout and runs `lrclean --help`; the rehearsal fails if that fails |
| B09 | section 7, `ci.yml` (the version-agreement check) | V4 B09: the CHANGELOG heading format and the exact-string comparison |
| B10 | section 7, `release.yml` | V4 B10: narrow "an annotated `vX.Y.Z` tag" to "a `vX.Y.Z` tag", with the `fetch-depth: 0` and `git merge-base --is-ancestor` sentence |
| B11 | section 5 step 4 and section 11 | V4 B11: the environment `pypi-release` created with Peter as required reviewer before step 5, and its acceptance line |
| B12 | section 7, "Three strings" bullet and the `release.yml` bullet | V4 B12, as ruled (RC-154, RC-155): "Four strings"; the fourth is "the GitHub account `pterw` (PyPI's owner field)", which does not use the words "the owner"; plus the per-job permissions sentence |
| B13 | section 7, `release.yml` | V4 B13: separate upload and release jobs, the re-run rule, fix forward as the next version |
| B14 | section 5 step 3 and section 11 | V4 B14: the tree-id comparison through a temporary index |
| B15 | section 7, `dependabot.yml` | V4 B15: the replacement parenthesis |

Two edits besides the rows: the design's status line (lines 3-5) says the outside read's 24 rows (A01 to A09, B01 to B15) were verified on 2026-11-24, that A01 and A02 were code defects fixed in this pull request's tasks BC and BD (issues 6 and 7, with issue 5 beside them), and that the other 22 rows are applied in the text below; it no longer says the design waits for the verification. The sentence "A local, ignored `CLAUDE.md`" is changed once, by A08.

**Acceptance:**
1. Every row above has its text in the design (a reviewer ticks the 22 rows against the design; A01 and A02 are in the status line only).
2. B06, B02, B05 and A05 read as RC-153 rules them (renamed with a hand step; test fixes of the CI task; narrowed, not widened).
3. `git grep -n -i "the owner" docs/superpowers/specs/2026-11-24-public-release-design.md` finds no line this task added (the existing uses, in the Voice row of section 2 and in the `docs/process/` note, quote the rule or the unedited documents and stay).
4. No private name, no path of the real library, and no link to the workspace or the verify files.

Commit subject: `docs(lrdup): Apply the verified outside-read text to the public release design`

---

## Spec coverage

| Item | Task |
|---|---|
| RC-153 (1), issue 6, row A01 | BC |
| RC-153 (2), issue 7, row A02 | BD |
| RC-153 (3), issue 5 | BE |
| RC-153 (4), rows A03 to A09, B01 to B15 | BF |
| Rules 6, 18, 23, 46 of the 2026-11-11 design (and the Known-limits sentence) | clauses given in BC, BD, BE; applied by the controller at landing |

## Controller steps

1. At each landing, add the rule clauses of the task to the 2026-11-11 design in the same commit, re-check the mutants counts (9 for backup, 7 for copies; the mover file's count is unchanged by BC) at HEAD, and update any count site constraints.md names.
2. After the three code tasks have landed and the stack's one gate run is green, a reviewer who did not write the code hand-runs `catalogs` (the real command, through `python -m lrdup`) on a synthetic tree, three times, with the snapshot folder a junction to the catalog's folder, `<work>/catalogs` a junction to it, and `<work>` a junction to it, and a fourth time with a junction above the work folder. In the first three the command exits 2 and names the linked folder; the original catalog and its `-wal` and `-shm` files are byte-identical after each run, and in the first two the catalog's folder has no new file or folder. In the third the folder is `<work>` itself, so it also holds the tool's own `config.json` and work database; those are not a failure, and the check is exit 2 and the original's files unchanged. In the fourth the command exits 0 and reports the catalog as unreadable, and the catalog's folder is unchanged. The tree and the links are made in the reviewer's own temporary folder; no real folder is touched.
3. The pull request closes issues 5, 6 and 7 (`Closes #5`, `Closes #6`, `Closes #7`); its body passes the private-name sweep first.
4. After the pull request merges, the release-prep branch is cut from the new `main` (release design, section 5).

## Not in this plan

- Any change to `backup_verify.py`, `find_catalogs`, or the planted-defect files other than the two named.
- A planted defect for BC (RC-153), a new link seam, or a refusal that tells the user why a catalog is `unreadable` (a later feature).
- The 22 non-code rows' own follow-up work (the CI task's test fixes B02 and B05, the rename, the workflows): the release-prep branch's tasks.
