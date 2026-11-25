"""The three outputs of the reject check: rejects.csv, reject-removal-list.csv, rejects-summary.md (rules 25, 30)."""

from __future__ import annotations

import csv
import hashlib
import os
from collections import Counter
from collections.abc import Sequence
from typing import NamedTuple

from lrclean import paths
from lrclean.report import GENERATED
from lrclean.rejects import VERDICTS, CatalogView, Reject, partly_rejected, relative_only_flags

REJECTS = "rejects.csv"
REMOVAL = "reject-removal-list.csv"
SUMMARY = "rejects-summary.md"
NEVER_READ = "Catalogs the tool never read are not known to it."


class SummaryInput(NamedTuple):
    generated: str
    scans: list[dict]
    views: list[CatalogView]
    limiting: list[str]


def _cat(entries) -> str:
    return ";".join(f"{source} ({role})" for source, role in entries)


def _how(reject: Reject) -> str:
    cells = (f"{source}={n}" if n else f"{source}=0 (catalog-relative form only)" for source, n in reject.images)
    text = "images: " + "; ".join(cells)
    return f"{text}; held by: {reject.held_by}" if reject.held_by else text


def _travelling(rejects: Sequence[Reject]) -> set[str]:
    """Norms of the paths that travel as a sidecar of an `on_list` reject (RC-23)."""
    return {paths.norm(side) for r in rejects if r.verdict == "on_list" for side in r.sidecars}


def _rejects_rows(rejects: Sequence[Reject], roots: int) -> tuple[list[str], list[list]]:
    header = (["path", "bytes", "verdict", "rejected_by", "kept_by"]
              + [f"archive_{n}" for n in range(1, roots + 1)] + ["developed_in", "twin", "twin_settings", "how_decided"])
    rows = [[r.path, r.bytes, r.verdict, _cat(r.rejected_by), _cat(r.kept_by), *r.archive,
             ";".join(f"{s} ({role}: {work})" for s, role, work in r.developed_in), r.twin, r.twin_settings, _how(r)]
            for r in rejects]
    return header, rows


def _removal_rows(rejects: Sequence[Reject], roots: int) -> tuple[list[str], list[list]]:
    header = (["source", "bytes", "mtime_ns", "sidecars", "sidecar_bytes"]
              + [f"dest_{n}" for n in range(1, roots + 1)] + ["twin"])
    side = _travelling(rejects)
    rows = [[r.path, r.bytes, r.mtime_ns, ";".join(r.sidecars), r.sidecar_bytes, *r.dests, r.twin]
            for r in rejects if r.verdict == "on_list" and paths.norm(r.path) not in side]
    return header, rows


def _summary(rejects: Sequence[Reject], summary: SummaryInput, list_sha256: str) -> str:
    side = _travelling(rejects)
    files: Counter = Counter(r.verdict for r in rejects)
    size: Counter = Counter()
    for r in rejects:
        size[r.verdict] += 0 if paths.norm(r.path) in side else r.bytes + r.sidecar_bytes
    rejecting = Counter(entry for r in rejects for entry in r.rejected_by)
    lines = ["# Reject check", f"{GENERATED}{summary.generated}", "", "## Scans", ""]
    lines += [f"- {s['kind']} scan {s['id']} of {s['root']}: {s['status']}, finished {s['finished']}"
              for s in summary.scans] or ["- none"]
    lines += ["", "## Per verdict", "", "| verdict | files | bytes |", "| --- | ---: | ---: |"]
    lines += [f"| {v} | {files[v]} | {size[v]} |" for v in VERDICTS]
    lines += ["", "## Per catalog", ""]
    lines += [f"- {v.source} ({v.role}): {rejecting[(v.source, v.role)]} rejects"
              for v in summary.views if v.role in ("live", "shoot")] or ["- none"]
    lines += ["", "## Rejected in part", "", "Flagged -1 but not a reject:", ""]
    lines += [f"- {source}: {path}" for source, path in partly_rejected(summary.views)] or ["- none"]
    lines += ["", "Flagged -1 only through the catalog-relative form, which rejects nothing (rule 24):", ""]
    lines += [f"- {source}: {n} paths" for source, n in relative_only_flags(summary.views)] or ["- none"]
    kept = sum(1 for r in rejects if any(role == "live" for _, role in r.kept_by))
    lines += ["", "## Kept in the live catalog", "", f"Rejects the live catalog keeps: {kept}"]
    lifted = [r for r in rejects if r.verdict == "on_list" and r.twin]
    lifted_bytes = sum(0 if paths.norm(r.path) in side else r.bytes + r.sidecar_bytes for r in lifted)
    lines += ["", "## Lifted by rule 26a", "",
              f"Rejects on the list whose develop hold rule 26a lifted: {len(lifted)} files, {lifted_bytes} bytes"]
    lines += ["", "## Not checked", ""]
    lines += ([f"- {v.source}: {v.status}" for v in summary.views if v.excluded]
              + [f"- limiting: {cause}" for cause in summary.limiting]) or ["- none"]
    lines += ["", NEVER_READ, "", "## Removal list", "", f"- SHA-256: {list_sha256}"]
    return "\n".join(lines) + "\n"


def write_csv(path: str, header: list[str], rows: list[list]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_outputs(out_dir: str, rejects: Sequence[Reject], *, roots: int, summary: SummaryInput) -> list[str]:
    if any(len(r.archive) != roots or len(r.dests) != roots for r in rejects):
        raise ValueError("a reject does not carry one archive state and one destination per root")
    ordered = sorted(rejects, key=lambda r: paths.norm(r.path))
    names = (REJECTS, REMOVAL, SUMMARY)
    targets = [os.path.join(out_dir, name) for name in names]
    os.makedirs(out_dir, exist_ok=True)
    try:
        write_csv(targets[0] + ".tmp", *_rejects_rows(ordered, roots))
        write_csv(targets[1] + ".tmp", *_removal_rows(ordered, roots))
        with open(targets[1] + ".tmp", "rb") as handle:
            list_sha256 = hashlib.sha256(handle.read()).hexdigest()
        with open(targets[2] + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_summary(ordered, summary, list_sha256))
        if os.path.exists(targets[2]):
            os.remove(targets[2])  # a folder without the summary is never taken for a finished run
        for target in targets:
            os.replace(target + ".tmp", target)
    except OSError:
        for target in targets:
            try:
                os.remove(target + ".tmp")
            except OSError:
                pass  # already replaced, never created, or a folder: the original error is raised
        raise
    return targets
