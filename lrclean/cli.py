"""Command line for the phase A report tool: catalogs, inventory, hash, report, verify."""

import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import Any, NamedTuple

from lrclean import (backup, backup_plan, backup_verify, backups, catalog_views, catalogs,
                   classify, copies, copy_out, file_views, hashing, inventory, paths, reject_archive, reject_out,
                   reject_twin, rejects, report, store, verdict_views)
from lrclean import (mover_files, mover_gate, mover_journal, mover_list, mover_purge, mover_seams,
                   mover_stage, mover_undo)

OK, CONFIG_ERROR, LIGHTROOM_RUNNING, STEP_MISSING, STALLED, HELD = 0, 2, 3, 4, 5, 6

LIST_KEYS = ("library_roots", "backup_roots", "catalog_roots", "exclude", "old_states",
             "excluded_catalogs", "archive_roots")
KEYS = ("library_roots", "backup_roots", "catalog_roots", "photo_root", "group_depth",
        "exclude", "live_catalog", "old_states", "excluded_catalogs")

AS_STORED = "; describes the scans as stored: a file deleted, changed or excluded since is not read again (run inventory and hash again)"

LIMITS = {
    1: "counts every recorded file type, whatever its extension" + AS_STORED,
    2: "photo and sidecar records only; other recorded types were not checked" + AS_STORED,
    3: "only library files with a readable, complete catalog and scan coverage can be safe; "
       "its rows are checked again when the report is written",
    4: "files whose verdict needs a person to look",
    5: "only complete backup roots can hold a copy; roots not read are listed in section 11" + AS_STORED,
    6: "library photo files on complete roots only" + AS_STORED,
    7: "library photo files on complete roots only; a catalog that could not be read may record them"
       + AS_STORED,
    8: "photo and sidecar records of the live catalog only; other recorded types were not checked"
       + AS_STORED,
    9: "library roots whose latest files scan is complete" + AS_STORED,
    10: "library photo files outside the photo root" + AS_STORED,
    11: "roots, folders, files and catalogs the scans could not read",
}


class ConfigError(Exception):
    def __init__(self, key: str, detail: str = ""):
        super().__init__(key, detail)
        self.key, self.detail = key, detail

    def __str__(self) -> str:
        return f"{self.key}: {self.detail}" if self.detail else self.key


class StepMissing(Exception):
    """A required earlier step has not run."""


class _Run(NamedTuple):
    args: argparse.Namespace
    cfg: dict
    conn: Any
    base: str
    processes: Any
    now: Any
    stat: Any
    attrs_of: Any
    exists: Any
    volume_of: Any
    free_bytes: Any
    clock: Any
    opener: Any
    writer: Any
    rename: Any
    abort: Any
    scandir: Any
    seams: Any = None  # the B2 mover's `Seams`, built by `main`
    journals: Any = None  # the paths of the mover journals a command opened (the wrapper names them)


def _valid(key: str, value) -> bool:
    if key in LIST_KEYS:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if key == "group_depth":
        return type(value) is int and value >= 1
    if key == "live_catalog":
        return value is None or isinstance(value, str)
    return isinstance(value, str)


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError(path, f"cannot read the configuration file ({exc})") from exc
    if not isinstance(data, dict):
        raise ConfigError(path, "the configuration file is not a JSON object")
    for key in KEYS:
        if key not in data or not _valid(key, data[key]):
            raise ConfigError(key, "missing or of the wrong type")
    if "archive_roots" in data and not _valid("archive_roots", data["archive_roots"]):
        raise ConfigError("archive_roots", "must be a list of strings")
    return data


def _exclude(run: _Run) -> list[str]:
    return [*run.cfg["exclude"], run.base]


def _scan_roots(run: _Run) -> list[str]:
    return [*run.cfg["library_roots"], *run.cfg["backup_roots"]]


def _library_scan_ids(conn, library_roots: list[str]) -> list[int]:
    latest = {paths.norm(s["root"]): s["id"] for s in store.latest_scans(conn, "files")}
    ids = []
    for root in library_roots:
        if paths.norm(root) not in latest:
            raise StepMissing(f"run inventory first: no files scan for {root}")
        ids.append(latest[paths.norm(root)])
    return ids


def _catalogs(run: _Run) -> int:
    _refuse(mover_gate.link_refusal(run.base, run.seams.is_link, run.seams.listdir))
    try:
        names = run.processes()
    except RuntimeError as exc:  # processes cannot be listed: refuse, as if Lightroom ran
        raise catalogs.LightroomRunning(str(exc)) from exc
    if any(name.startswith("lightroom") for name in names):
        raise catalogs.LightroomRunning("Lightroom is running: refusing to copy a catalog")
    ids = catalogs.scan_catalogs(
        run.conn, catalog_roots=run.cfg["catalog_roots"], own_dir=run.base,
        exclude=_exclude(run), work_dir=os.path.join(run.base, "work"),
        live_catalog=run.cfg["live_catalog"], old_states=run.cfg["old_states"],
        processes=run.processes, now=run.now)
    bad = sum(1 for c in _latest_catalogs(run.conn) if c["status"] in ("unreadable", "partial"))
    print(f"catalogs stored: {len(ids)} ({bad} unreadable or partial)")
    return OK


def _inventory(run: _Run) -> int:
    try:
        ids = inventory.scan_files(
            run.conn, _scan_roots(run), catalog_roots=run.cfg["catalog_roots"],
            exclude=_exclude(run), own_dir=run.base, now=run.now, attrs_of=run.attrs_of)
    except inventory.NoCatalogScan as exc:
        raise StepMissing(f"run catalogs first: {exc}") from exc
    except ValueError as exc:
        raise ConfigError("library_roots, backup_roots", str(exc)) from exc
    print(f"files scans stored: {len(ids)}")
    return OK


def _hash(run: _Run) -> int:
    _library_scan_ids(run.conn, run.cfg["library_roots"])
    counts = hashing.hash_files(
        run.conn, library_roots=run.cfg["library_roots"],
        backup_roots=run.cfg["backup_roots"], stat=run.stat, now=run.now)
    print(", ".join(f"{name}={n}" for name, n in counts.items()))
    return OK


def _verify(run: _Run) -> int:
    _library_scan_ids(run.conn, run.cfg["library_roots"])
    counts = backups.verify(
        run.conn, run.args.folder, library_roots=run.cfg["library_roots"],
        backup_roots=run.cfg["backup_roots"], stat=run.stat, now=run.now)
    print(", ".join(f"{name}={n}" for name, n in counts.items()))
    return OK


def _require_archive_roots(run: _Run) -> list[str]:
    roots = run.cfg.get("archive_roots")
    if roots is None:
        raise ConfigError("archive_roots", "missing")
    try:
        backup_plan.check_archive_roots(
            roots, library_roots=run.cfg["library_roots"], own_dir=run.base,
            volume_of=run.volume_of)
    except ValueError as exc:
        raise ConfigError("archive_roots", str(exc)) from exc
    return roots


def _require_connected(run: _Run) -> list[str]:
    """The archive roots, each on a drive that is connected (its free space cannot be read otherwise)."""
    roots = _require_archive_roots(run)
    for root in roots:
        anchor = os.path.splitdrive(os.path.abspath(root))[0]
        if anchor and not run.exists(anchor + "\\"):
            raise ConfigError("archive_roots", f"the drive of {root} is not connected")
    return roots


def _backup_plan(run: _Run) -> int:
    roots = _require_connected(run)
    try:
        plan = backup_plan.plan_manifests(
            run.conn, library_roots=run.cfg["library_roots"], photo_root=run.cfg["photo_root"],
            archive_roots=roots, own_dir=run.base, exclude=_exclude(run), attrs_of=run.attrs_of,
            stat=run.stat, free_bytes=run.free_bytes)
    except backup_plan.ManifestError as exc:
        raise StepMissing(f"run inventory first: {exc}") from exc
    written = backup_plan.write_plan(os.path.join(run.base, "out"), plan)
    for root, path in zip(plan.roots, written):
        print(f"{path}: folders={root.folders} files={root.files} bytes={root.bytes}")
    return OK


def _manifest_rows(run: _Run, check) -> tuple[list, int]:
    """Read the manifest and run `check(rows)`; any manifest fault is a configuration error."""
    try:
        rows = backup_plan.read_manifest(run.args.manifest)
        return rows, check(rows)
    except backup_plan.ManifestError as exc:
        raise ConfigError("manifest", str(exc)) from exc


def _backup_run(run: _Run) -> int:
    roots = _require_connected(run)
    rows, n = _manifest_rows(run, lambda rows: backup.check_manifest(
        rows, archive_roots=roots, library_roots=run.cfg["library_roots"],
        free_bytes=run.free_bytes, exists=run.exists))
    journal_dir = os.path.join(run.base, "work", "journal")
    if not run.args.apply:
        verdicts = backup.dry_run(rows, journal_dir=journal_dir, n=n, stat=run.stat,
                                  attrs_of=run.attrs_of, exists=run.exists)
        for i, verdict in verdicts:
            print(f"{i} {verdict} {rows[i].dest}")
        return OK
    outcome = backup.run_manifest(
        rows, journal_dir=journal_dir, n=n, started=run.now(),
        stall_seconds=run.args.stall_seconds, clock=run.clock, abort=run.abort, now=run.now,
        stat=run.stat, attrs_of=run.attrs_of, opener=run.opener, writer=run.writer,
        rename=run.rename)
    for result in outcome.results:
        print(f"{result.row} {result.state} {result.dest}")
    tally = Counter(result.state for result in outcome.results)
    print(", ".join(f"{state}={count}" for state, count in sorted(tally.items())))
    return backup.exit_code(outcome)


def _backup_verify(run: _Run) -> int:
    roots = _require_archive_roots(run)
    rows, n = _manifest_rows(run, lambda rows: backup_plan.archive_index(rows, roots))
    path = os.path.join(run.base, "out", f"backup-verified-{n}.csv")
    if any(paths.norm(path) in (paths.norm(row.source), paths.norm(row.dest)) for row in rows):
        raise ConfigError("manifest", f"the verified list would replace a manifest file: {path}")
    items = backup_verify.verify_manifest(rows, stat=run.stat, attrs_of=run.attrs_of,
                                          opener=run.opener)
    backup_verify.write_verified(path, items)
    print(f"verified list written to {path}")
    return backup_verify.verify_exit_code(items)


def _latest_catalogs(conn) -> list:
    ids = [s["id"] for s in store.latest_scans(conn, "catalogs")]
    if not ids:
        return []
    sql = f'SELECT * FROM "catalog" WHERE scan_id IN ({",".join("?" * len(ids))}) ORDER BY id'
    return conn.execute(sql, ids).fetchall()


def _check_fresh(run: _Run) -> None:
    file_ids = _library_scan_ids(run.conn, run.cfg["library_roots"])
    wanted = {paths.norm(r) for r in run.cfg["catalog_roots"]}
    catalog_ids = [s["id"] for s in store.latest_scans(run.conn, "catalogs")
                   if paths.norm(s["root"]) in wanted]
    try:
        if file_ids and catalog_ids and max(catalog_ids) > min(file_ids):
            raise StepMissing("run inventory again: the catalogs were scanned after the files")
        _check_one_inventory(run)
    except StepMissing as exc:
        raise StepMissing(f"{exc}{_old_report_note(run.base)}") from exc


def _check_one_inventory(run: _Run) -> None:
    """The latest files scans, in walk order, come from one inventory run: their ids only grow."""
    latest = {paths.norm(s["root"]): s["id"] for s in store.latest_scans(run.conn, "files")}
    seen = 0
    for root in _scan_roots(run):
        scan_id = latest.get(paths.norm(root))
        if scan_id is None:
            continue
        if scan_id < seen:
            raise StepMissing(f"run inventory again: {root} was last scanned by an earlier inventory run")
        seen = scan_id


def _old_report_note(base: str) -> str:
    summary = os.path.join(base, "out", "summary.md")
    if not os.path.exists(summary):
        return ""
    generated = report.read_generated(summary)
    when = f"Generated: {generated}" if generated else "it carries no generation time"
    return f"\nthe report in {os.path.dirname(summary)} is older than the latest scans and must not be acted on ({when})"


def _unseen_catalogs(run: _Run) -> list[str]:
    """Catalogs a listing of the catalog roots finds now that the latest catalogs scan did not store."""
    stored = {paths.norm(c["source_path"]) for c in _latest_catalogs(run.conn)}
    unseen = []
    for root in run.cfg["catalog_roots"]:
        if os.path.isdir(paths.fs_path(root)):
            found, unlisted = catalogs.find_catalogs(root, run.base, _exclude(run), run.scandir)
            unseen += [p for p in found if paths.norm(p) not in stored] + unlisted
    return unseen


def _stale_catalogs(run: _Run) -> list[str]:
    return sorted(c["source_path"] for c in _latest_catalogs(run.conn)
                  if catalogs.is_stale(c["source_path"], c["size"], c["mtime_ns"],
                                       c["wal_bytes"] or 0, c["has_lock"] or 0, run.stat))


def _coverage(run: _Run, stale: list[str], unseen: list[str]) -> tuple[dict, classify.Coverage]:
    cfg = run.cfg
    skip = {paths.norm(p) for p in cfg["excluded_catalogs"]}
    stale_set = set(stale)
    cats = []
    for cat in _latest_catalogs(run.conn):
        counted = paths.norm(cat["source_path"]) not in skip
        limits = counted and (cat["status"] in ("unreadable", "partial")
                              or cat["source_path"] in stale_set)
        cats.append({"catalog": cat["source_path"], "status": cat["status"],
                     "limits_coverage": limits})
    cov = classify.coverage(run.conn, catalog_roots=cfg["catalog_roots"],
                            excluded_catalogs=cfg["excluded_catalogs"], stale_catalogs=stale,
                            unseen_catalogs=unseen)
    roots = [{"root": s.root, "status": s.status}
             for s in classify.root_states(run.conn, _scan_roots(run))]
    wanted = {"catalogs": {paths.norm(r) for r in cfg["catalog_roots"]},
              "files": {paths.norm(r) for r in _scan_roots(run)}}
    scans = [{"kind": kind, "id": s["id"], "root": s["root"], "status": s["status"],
              "finished": s["finished"]}
             for kind in wanted for s in store.latest_scans(run.conn, kind)
             if paths.norm(s["root"]) in wanted[kind]]
    return {"roots": roots, "catalogs": cats, "limiting": cov.limiting,
            "generated": run.now(), "scans": scans}, cov


def _report(run: _Run) -> int:
    _check_fresh(run)
    cfg, conn = run.cfg, run.conn
    stale = _stale_catalogs(run)
    unseen = _unseen_catalogs(run)
    coverage, cov = _coverage(run, stale, unseen)
    library, backup = cfg["library_roots"], cfg["backup_roots"]
    excluded = cfg["excluded_catalogs"]
    verdicts = classify.verdicts(
        conn, library_roots=library, catalog_roots=cfg["catalog_roots"],
        excluded_catalogs=excluded, stale_catalogs=stale, unseen_catalogs=unseen, stat=run.stat)
    sections = {
        1: catalog_views.catalog_inventory_rows(conn, excluded_catalogs=excluded),
        2: catalog_views.shoot_catalog_rows(
            conn, library_roots=library, backup_roots=backup, excluded_catalogs=excluded,
            exists=run.exists),
        3: verdict_views.safe_candidate_rows(verdicts),
        4: verdict_views.needs_eye_rows(verdicts),
        5: backups.external_copy_rows(
            conn, photo_root=cfg["photo_root"], group_depth=cfg["group_depth"],
            library_roots=library, backup_roots=backup),
        6: file_views.pair_rows(conn, library_roots=library, excluded_catalogs=excluded,
                                coverage_complete=cov.complete),
        7: verdict_views.large_unreferenced_rows(verdicts, coverage_complete=cov.complete),
        8: catalog_views.missing_rows(
            conn, live_catalog=cfg["live_catalog"], library_roots=library,
            backup_roots=backup, drive_exists=run.exists),
        9: file_views.container_rows(conn, library_roots=library, coverage_complete=cov.complete),
        10: file_views.loose_rows(conn, library_roots=library, photo_root=cfg["photo_root"]),
        11: file_views.not_read_rows(
            conn, library_roots=library, backup_roots=backup,
            catalog_roots=cfg["catalog_roots"], excluded_catalogs=excluded,
            stale_catalogs=stale, unseen_catalogs=unseen),
    }
    out_dir = os.path.join(run.base, "out")
    report.write_report(out_dir, sections, coverage, LIMITS)
    print(f"report written to {out_dir}")
    return OK


def _old_rejects_note(base: str, name: str) -> str:
    summary = os.path.join(base, "out", name)
    if not os.path.exists(summary):
        return ""
    generated = report.read_generated(summary)
    when = f"Generated: {generated}" if generated else "it carries no generation time"
    return (f"\nthe reports in {os.path.dirname(summary)} are older than the latest scans and must not be "
            f"acted on (out/{name}; {when})")


def _rejects_coverage(run: _Run, stale: list[str]) -> classify.Coverage:
    cfg = run.cfg
    cov = classify.coverage(run.conn, catalog_roots=cfg["catalog_roots"],
                            excluded_catalogs=cfg["excluded_catalogs"], stale_catalogs=stale,
                            unseen_catalogs=_unseen_catalogs(run))
    gone = [f"{root} (not connected now)" for root in cfg["catalog_roots"]
            if not os.path.isdir(paths.fs_path(root))]
    return classify.Coverage(False, [*cov.limiting, *gone]) if gone else cov


class _Prologue(NamedTuple):
    roots: list
    views: list
    cov: classify.Coverage
    evidence: Any
    out_dir: str
    summary: reject_out.SummaryInput


def _prologue(run: _Run) -> _Prologue:
    """What `rejects` and `copies` read before they decide: the latest `catalogs` scans once (both summaries
    name that read), the views, the freshness checks, the coverage and the archive evidence."""
    cfg = run.cfg
    roots = _require_archive_roots(run)
    if len(roots) < 2:
        raise ConfigError("archive_roots", "archive_roots must name at least two roots")
    live = cfg["live_catalog"]
    if live is not None and paths.norm(live) in {paths.norm(p) for p in cfg["excluded_catalogs"]}:
        raise ConfigError("excluded_catalogs", "the live catalog is excluded and could keep nothing")
    scans = [{"kind": "catalogs", "id": s["id"], "root": s["root"], "status": s["status"],
              "finished": s["finished"]} for s in store.latest_scans(run.conn, "catalogs")]
    views = rejects.latest_views(run.conn, excluded_catalogs=cfg["excluded_catalogs"])
    stale = _stale_catalogs(run)
    expected = {paths.norm(v.source): catalogs.role_of(v.source, live, cfg["old_states"])
                for v in views}
    try:
        rejects.check_fresh(views, live_catalog=live, stale=stale, expected_roles=expected)
    except rejects.StaleFlags as exc:
        raise StepMissing(str(exc)) from exc
    cov = _rejects_coverage(run, stale)
    out_dir = os.path.join(run.base, "out")
    try:
        evidence = reject_archive.load_evidence(out_dir, roots, exists=run.exists, stat=run.stat)
    except backup_plan.ManifestError as exc:
        raise ConfigError("out", str(exc)) from exc
    return _Prologue(roots, views, cov, evidence, out_dir,
                     reject_out.SummaryInput(run.now(), scans, views, cov.limiting))


def _rejects_run(run: _Run) -> int:
    pro = _prologue(run)
    found = rejects.decide(pro.views, pro.evidence, coverage_complete=pro.cov.complete, stat=run.stat,
                           attrs_of=run.attrs_of,
                           hashes=reject_twin.load_hashes(run.conn, run.cfg["library_roots"]))
    reject_out.write_outputs(pro.out_dir, found, roots=len(pro.roots), summary=pro.summary)
    print(f"reject check written to {pro.out_dir}: {len(found)} rejects")
    return OK


def _unhashed(conn, files, hashes, photo_root: str) -> int:
    """Photos under `photo_root` that `hash` has not covered (RC-150): no valid sample, or a member of a
    (size, sample) group of two or more with no counted full hash. Placeholders are not counted."""
    rows = {h["file_id"]: h for h in conn.execute('SELECT file_id, file_index, size, mtime_ns, sample FROM "hash"')}
    groups: dict[tuple, set] = {}
    missing = set()
    for r in files:
        if (r["ext"] not in paths.PHOTO_EXTS or r["placeholder"]
                or not backup_plan.is_inside(r["path_orig"], photo_root, below_only=True)):
            continue
        h = rows.get(r["id"])
        if h and h["sample"] and (h["size"], h["mtime_ns"], h["file_index"]) == (r["size"], r["mtime_ns"], r["file_index"]):
            groups.setdefault((r["size"], h["sample"]), set()).add(r["path_norm"])
        else:
            missing.add(r["path_norm"])
    grouped = {pn for members in groups.values() if len(members) > 1 for pn in members}
    return len(missing | (grouped - hashes.by_path.keys()))


def _copies_run(run: _Run) -> int:
    pro = _prologue(run)
    cfg = run.cfg
    try:
        purged = copies.read_purged(pro.out_dir)
    except copies.PurgeResultError as exc:
        raise ConfigError("out", str(exc)) from exc
    files = classify.inventoried_files(run.conn, cfg["library_roots"])
    hashes = reject_twin.load_hashes(run.conn, cfg["library_roots"])
    try:
        built = copies.build(files, pro.views, pro.evidence, hashes, purged,
                             photo_root=cfg["photo_root"], coverage=pro.cov, stat=run.stat, attrs_of=run.attrs_of)
    except copies.Incomplete as exc:
        raise StepMissing(str(exc)) from exc
    unhashed = _unhashed(run.conn, files, hashes, cfg["photo_root"])
    list_path = copy_out.write_outputs(pro.out_dir, built, roots=len(pro.roots), summary=pro.summary,
                                       unhashed=unhashed)[0]
    print(f"copies written to {pro.out_dir}: {len(built.rows)} rows")
    with open(list_path, "rb") as handle:
        print(f"SHA-256 of the list: {mover_list.list_sha256(handle.read())}")
    if unhashed:
        print(copy_out.unhashed_line(unhashed))
    return OK


def _with_old_note(command, summary: str):
    """Run `command`; a refusal for stale or incomplete evidence (exit 4) says the reports of `summary`'s run
    must not be acted on. A configuration refusal (exit 2) says nothing of them."""
    def noted(run: _Run) -> int:
        note = _old_rejects_note(run.base, summary)
        try:
            return command(run)
        except StepMissing as exc:
            raise StepMissing(f"{exc}{note}") from exc
    return noted


def _mover(command):
    """A refusal of the B2 mover gives its own code; any other `ValueError` (a list, a journal) is exit 2;
    an `OSError` (a rename, a remove, a journal append, the results file) is exit 6 and names the journal."""
    def guarded(run: _Run) -> int:
        try:
            return command(run)
        except ValueError as exc:
            raise ConfigError(run.args.command, str(exc)) from exc
        except OSError as exc:
            return _fail(HELD, _io_stop(run, exc))
    return guarded


def _io_stop(run: _Run, exc: OSError) -> str:
    head = f"{run.args.command} stopped on an error ({exc})"
    journal = next(iter(run.journals), None)
    if journal is None or not run.exists(journal):
        return f"{head}: no journal was written and nothing moved"
    return f"{head}: the journal {journal} holds the state; the next undo or purge reads it"


def _open_journal(run: _Run, ctx, batch: str):
    journal = mover_journal.Journal(os.path.join(run.base, "work", "journal", f"mover-{batch}.jsonl"), ctx)
    run.journals.append(journal.path)
    return journal


def _refuse(refusal) -> None:
    if refusal:
        raise refusal


def _mover_ctx(run: _Run, batch: str):
    _refuse(mover_gate.link_refusal(run.base, run.seams.is_link, run.seams.listdir))
    return mover_seams.new_ctx(run.seams, mover_seams.Folders(run.base, run.cfg["photo_root"]), run.base, batch)


def _read_bytes(run: _Run, path: str) -> bytes:
    try:
        with run.opener(path) as handle:
            return handle.read()
    except OSError as exc:
        raise ConfigError(path, f"cannot be read ({exc})") from exc


_REASONS = {
    "archive_mismatch": "an archive copy (named) differs from the file, or is not on its own volume, so the row stays",
    "twin_mismatch": "the twin (named) differs from the file, or is not on its own volume, so the row stays",
    "no_archive_copy": "no verified archive copy at the path named: it is missing or differs from what "
                       "backup-verify recorded",
    "staged_changed": "the staged file (named) is not the one that was staged",
    "staged_missing": "the staged file is gone",
    "changed": "the file (named) changed since the list was made",
    "missing": "the file (named) is not there",
    "placeholder": "the file (named) is an online-only placeholder with no content on this disk",
    "name_ambiguous": "the name differs from another file's only by case, so the file cannot be told apart",
    "shared_sidecar": "a sidecar of the row belongs to another row of the list too, so the row stays",
    "other_volume": "the staging folder is on another volume than the file, so it cannot be renamed there",
    "staging_exists": "a file is already at the staged path",
    "rename_failed": "a rename failed and the row's files were put back",
    "unplaced": "the journal cannot say where a file of the row is: the row stays for you to look at",
    "family_incomplete": "not every file of the row is staged, so the row stays",
    "outside_folders": "a path of the journal lies outside the photo and staging folders",
    "purged": "the row was purged and cannot be restored",
    "parent_link": "a folder on the path of the original is a link",
    "parent_missing": "the folder of the original is gone",
    "original_exists": "a file is already at the original path",
    "never_moved": "the stage step never ran: the file is at its original path and was never moved, "
                   "so there is nothing to restore",
}


def _reasons(results: list) -> None:
    """One sentence per reason code that held, refused or skipped a row, once, in the order first seen."""
    codes = dict.fromkeys(r.reason for r in results if r.state in ("held", "refused", "skipped") and r.reason)
    for code in codes:
        print(f"{code}: {_REASONS.get(code, 'see the journal')}")


def _show(results: list, renames: dict | None = None) -> None:
    """One line per row; the files of a row where it was (or would be) staged or restored, or is held on them.
    `renames` (an undo dry run): the staged path of each (row, original), shown as `<staged> -> <original>`."""
    for result in results:
        print(f"{result.index} {result.state} {result.source}" + (f" {result.reason}" if result.reason else ""))
        if result.state in ("would_stage", "staged", "would_restore", "restored", "held"):
            for path in result.files:
                print(f"    {renames[result.index, path]} -> {path}" if renames and result.state == "would_restore"
                      else f"    {path}")
    tally = Counter(result.state for result in results)
    print(", ".join(f"{state}={count}" for state, count in sorted(tally.items())) or "no rows")
    _reasons(results)


def _held(results: list, results_name: str | None = None) -> int:
    """`results_name`: the results file this run wrote (`out/<op>-<batch>.csv`), named last when rows are held."""
    count = sum(result.state in ("held", "refused") for result in results)
    if count:
        print(f"{count} row(s) held or refused: their files stayed where they are; the reasons are listed above",
              file=sys.stderr)
        if results_name:
            print(f"read {results_name} for each row; held rows stay where they are", file=sys.stderr)
    return HELD if count else OK


def _write_results(run: _Run, op: str, batch: str, ctx, journal) -> None:
    """Write the results file and say which batch, journal and results file the run left."""
    path = os.path.join(run.base, "out", f"{op}-{batch}.csv")
    mover_journal.write_results(path, op, mover_journal.read_journal(journal.path, ctx), ctx)
    _finished(batch, journal, path)


def _finished(batch: str, journal, results_path: str | None) -> None:
    print(f"batch {batch}")
    print(f"journal {journal.path}")
    if results_path:
        print(f"results {results_path}")


def _stalled(run: _Run, batch: str, journal) -> int:
    """A stalled run's exit; it names the batch and the journal it left, when it left one."""
    if run.exists(journal.path):
        _finished(batch, journal, None)
    return STALLED


def _load_journal(run: _Run):
    batch = run.args.batch
    if not mover_files.valid_batch(batch):
        raise ConfigError("batch", f"not a batch name (YYYYMMDDTHHMMSSZ): {batch}")
    ctx = _mover_ctx(run, batch)
    journal = _open_journal(run, ctx, batch)
    try:
        lines = mover_journal.read_journal(journal.path, ctx)
    except OSError as exc:
        raise ConfigError(journal.path, f"the journal cannot be read ({exc})") from exc
    if not any(line["event"] == "start" for line in lines):
        raise ConfigError(journal.path, "the journal has no start line")
    return ctx, journal, mover_journal.entries(lines)


def _refusals(run: _Run, op: str, ctx, sources: list, approval, list_sha256: str | None = None,
              listing=mover_list.REJECTS) -> None:
    stale = _stale_catalogs(run)
    facts = mover_gate.gather(op, cfg=run.cfg, ctx=ctx, conn=run.conn, processes=run.processes, stale=stale,
                              coverage=_rejects_coverage(run, stale), sources=sources, list_sha256=list_sha256,
                              listing=listing)
    _refuse(mover_gate.first_refusal(op, facts, approval))


def _stage(run: _Run) -> int:
    args = run.args
    listing = mover_list.check_list_path(args.list, run.base)
    data = _read_bytes(run, args.list)
    computed = mover_list.list_sha256(data)
    _refuse(mover_gate.list_approved(computed, args.approved_sha256))
    roots = _require_archive_roots(run)
    rows = mover_list.read_list(data, len(roots), listing.kind)
    shared = frozenset(paths.norm(path) for path in mover_list.shared_sidecars(rows).values())
    try:
        chosen = mover_list.select(rows, args.only or [])
    except mover_list.ListError as exc:
        raise mover_list.ListError(f"{exc}; a row is named by its source as written in the list (the full path)") \
            from exc
    batch = mover_files.batch_name(run.now())
    ctx = _mover_ctx(run, batch)
    _refusals(run, "stage", ctx, [row.source for row in chosen], None, computed, listing)
    try:
        evidence = reject_archive.load_evidence(os.path.join(run.base, "out"), roots, exists=run.exists,
                                                stat=run.stat)
    except backup_plan.ManifestError as exc:
        raise ConfigError("out", str(exc)) from exc
    start = {"list_sha256": computed, "approved_sha256": mover_list.approved_sha(args.approved_sha256),
             "scans": [["catalogs", s["id"]] for s in store.latest_scans(run.conn, "catalogs")]}
    journal = _open_journal(run, ctx, batch)
    outcome = mover_stage.run_stage(chosen, evidence=evidence, ctx=ctx, journal=journal, start=start,
                                    stall_seconds=args.stall_seconds, apply=args.apply, shared_paths=shared)
    print(f"SHA-256 of the list: {computed}")
    _show(outcome.results)
    if outcome.stalled:
        return _stalled(run, batch, journal)
    if args.apply:
        _write_results(run, "stage", batch, ctx, journal)
    else:
        print("dry run: nothing was changed; --apply would stage the rows marked would_stage."
              " File contents are hashed only with --apply, so --apply can still hold a row marked would_stage")
    return _held(outcome.results, f"out/stage-{batch}.csv" if args.apply else None)


def _undo(run: _Run) -> int:
    ctx, journal, entries = _load_journal(run)
    apply = run.args.apply
    results = _say_never_moved(mover_undo.run_undo(entries, ctx=ctx, journal=journal, start={}, apply=apply),
                               entries, ctx)
    _show(results, {(entry.row, entry.original): entry.staged for entry in entries})
    todo = any(result.state in ("would_restore", "restored") for result in results)
    if not todo and not any(_is_staged(ctx, entry.staged) for entry in entries):
        print(f"nothing to undo: no file of batch {run.args.batch} is staged any more")
    if apply:
        _finished(run.args.batch, journal, None)
    elif todo:
        print("dry run: nothing was changed; --apply would restore the rows marked would_restore")
    return _held(results)


def _say_never_moved(results: list, entries: list, ctx) -> list:
    """A skipped row with an open stage step that looking places at the original path never moved: say so.
    Output only: the row's state and the files are the undo's own."""
    opened = {result.index for result in results if result.state == "skipped"}
    never = {entry.row for entry in entries if entry.row in opened and entry.state == "open_stage"
             and mover_journal.place(entry, ctx) == "not_done"}
    return [result._replace(reason="never_moved") if result.index in never else result for result in results]


def _purge(run: _Run) -> int:
    args = run.args
    ctx, journal, entries = _load_journal(run)
    roots = _require_archive_roots(run)
    looked = mover_purge.look_all(entries, ctx)  # one look per entry: the digest, the set and the run share it
    computed = mover_purge.purge_sha256(looked)
    approval = mover_gate.purge_approved(computed, args.approved_sha256) if args.apply else None
    _refusals(run, "purge", ctx, [entry.original for entry in entries], approval)
    results = mover_purge.run_purge(looked, roots=roots, ctx=ctx, journal=journal,
                                    start={"purge_sha256": computed}, stall_seconds=args.stall_seconds,
                                    apply=args.apply)
    print(f"SHA-256 of the purge set: {computed}")
    _show(results)
    if ctx.stop.is_set():
        return _stalled(run, args.batch, journal)
    if not results:
        _nothing_to_purge(args.batch, looked)
    if args.apply:
        _write_results(run, "purge", args.batch, ctx, journal)
    elif results:
        _show_purge_set(mover_purge.purge_set(looked), results)
    return _held(results)


def _nothing_to_purge(batch: str, looked: list) -> None:
    open_stage = [look for entry, look in looked if entry.state == "open_stage"]  # a stage step with no `end`
    if open_stage and all(look == "not_done" for look in open_stage):
        print(f"nothing to purge: the stage step of batch {batch} was never carried out; the file is at its "
              "original path (nothing to purge, nothing to undo)")
    elif open_stage:
        print(f"nothing to purge: no file of batch {batch} is staged, but an open stage step remains;"
              " undo places it")
    else:
        print(f"nothing to purge: no file of batch {batch} is staged any more")


def _is_staged(ctx, path: str) -> bool:
    try:
        ctx.seams.stat(paths.fs_path(path))
    except OSError:
        return False
    return True


def _show_purge_set(files: list, results: list) -> None:
    """What `purge --apply` would delete: the files of the would_purge rows with their bytes and the total they
    free; the files of held rows apart, under their row and reason (they stay, but the SHA-256 covers them)."""
    by_row = {result.index: result for result in results}
    free = [entry for entry in files if by_row[entry.row].state == "would_purge"]
    print("files that --apply would delete:")
    for entry in free:
        print(f"    {entry.staged} {entry.bytes} bytes")
    print(f"total {sum(entry.bytes for entry in free)} bytes would be freed")
    held = [entry for entry in files if by_row[entry.row].state != "would_purge"]
    last = None
    for entry in held:
        if entry.row != last:
            print(f"held, not deleted: row {entry.row} {by_row[entry.row].reason}")
            last = entry.row
        print(f"    {entry.staged} {entry.bytes} bytes")
    if held:
        print("the SHA-256 above covers the whole purge set: every staged file listed, held rows' files included")
    print("dry run: nothing was changed; --apply with this SHA-256 would purge the rows marked would_purge."
          " File contents are hashed only with --apply, so --apply can still hold a row marked would_purge")


_COMMANDS = {"catalogs": _catalogs, "inventory": _inventory, "hash": _hash,
             "report": _report, "verify": _verify,
             "rejects": _with_old_note(_rejects_run, "rejects-summary.md"),
             "copies": _with_old_note(_copies_run, copy_out.SUMMARY),
             "backup-plan": _backup_plan, "backup-run": _backup_run,
             "backup-verify": _backup_verify, "stage": _mover(_stage), "undo": _mover(_undo),
             "purge": _mover(_purge)}
_NO_DATABASE = ("backup-run", "backup-verify", "undo")


def _positive_seconds(text: str) -> float:
    value = float(text)
    if not 0 < value < float("inf"):  # false for nan, zero, negatives and inf
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lrclean")
    parser.add_argument("--base", default=".", help="the tool's folder (work/ and out/)")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("catalogs", "inventory", "hash", "report", "rejects", "copies"):
        commands.add_parser(name)
    commands.add_parser("backup-plan")
    run_parser = commands.add_parser("backup-run")
    run_parser.add_argument("manifest")
    run_parser.add_argument("--apply", action="store_true")
    run_parser.add_argument("--stall-seconds", type=_positive_seconds, default=120)
    commands.add_parser("backup-verify").add_argument("manifest")
    commands.add_parser("verify").add_argument("folder")
    stage = commands.add_parser("stage")
    stage.add_argument("list")
    stage.add_argument("--approved-sha256", required=True)
    stage.add_argument("--only", action="append")
    purge = commands.add_parser("purge")
    purge.add_argument("batch")
    purge.add_argument("--approved-sha256")
    commands.add_parser("undo").add_argument("batch")
    for name in ("stage", "purge", "undo"):
        commands.choices[name].add_argument("--apply", action="store_true")
    for name in ("stage", "purge"):
        commands.choices[name].add_argument("--stall-seconds", type=_positive_seconds, default=120)
    return parser


def _fail(code: int, message: str) -> int:
    print(message, file=sys.stderr)
    return code


def main(argv: list[str] | None = None, *, processes=catalogs.running_processes,
         now=store.utc_now, stat=os.stat, attrs_of=None, exists=os.path.exists,
         volume_of=paths.volume, free_bytes=backup_plan.free_space, clock=time.monotonic,
         opener=hashing.open_binary, writer=backup.open_part, rename=os.rename,
         abort=os._exit, scandir=os.scandir, is_link=mover_seams.REAL.is_link, remove=os.remove,
         rmdir=os.rmdir, makedirs=os.makedirs, listdir=os.listdir, append=mover_seams.append,
         write=mover_seams.write) -> int:
    for stream in (sys.stdout, sys.stderr):  # a path outside the code page must never stop the output
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = _parser().parse_args(argv)
    base =os.path.abspath(args.base)
    try:
        cfg = load_config(os.path.join(base, "work", "config.json"))
    except ConfigError as exc:
        return _fail(CONFIG_ERROR, f"configuration error: {exc}")
    for root in (*cfg["library_roots"], *cfg["backup_roots"], *cfg["catalog_roots"]):
        if paths.norm(os.path.abspath(root)) == paths.norm(base):  # a root's own entry is never pruned
            return _fail(CONFIG_ERROR, f"configuration error: the base folder is the root {root}")
    conn = None if args.command in _NO_DATABASE else store.connect(
        os.path.join(base, "work", "lrclean.sqlite"))
    run = _Run(args, cfg, conn, base, processes, now, stat, attrs_of, exists, volume_of, free_bytes,
               clock, opener, writer, rename, abort, scandir,
               mover_seams.Seams(stat, attrs_of, is_link, opener, rename, remove, rmdir, makedirs, listdir,
                                 append, write, clock, abort, now), [])
    try:
        return _COMMANDS[args.command](run)
    except OSError as exc:  # a file of the tool folder that cannot be written (the movers say more)
        return _fail(HELD, f"{args.command} stopped on an error ({exc})")
    except ConfigError as exc:
        return _fail(CONFIG_ERROR, f"configuration error: {exc}")
    except mover_gate.Refusal as exc:
        return _fail(exc.code, exc.message)
    except catalogs.LightroomRunning as exc:
        return _fail(LIGHTROOM_RUNNING, str(exc))
    except StepMissing as exc:
        return _fail(STEP_MISSING, str(exc))
    finally:
        if conn is not None:
            conn.close()
