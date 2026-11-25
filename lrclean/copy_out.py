"""The two outputs of the copies pass: copy-removal-list.csv and copies-summary.md (rule 49)."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from collections.abc import Sequence

from lrclean.reject_out import SummaryInput, write_csv
from lrclean.report import GENERATED

LIST = "copy-removal-list.csv"
SUMMARY = "copies-summary.md"


def _list_rows(rows: Sequence, roots: int) -> tuple[list[str], list[list]]:
    header = (["source", "bytes", "mtime_ns", "sidecars", "sidecar_bytes"]
              + [f"dest_{n}" for n in range(1, roots + 1)] + ["twin"])
    return header, [[r.path, r.bytes, r.mtime_ns, "", 0, *r.dests, r.twin] for r in rows]


def _class_lines(title: str, rows: Sequence) -> list[str]:
    folders: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        folder = folders[os.path.dirname(r.path)]
        folder[0] += 1
        folder[1] += r.bytes
    lines = ["", f"## {title}", "", f"Files: {len(rows)}, bytes: {sum(r.bytes for r in rows)}", ""]
    return lines + ([f"- {name}: {n} files, {size} bytes" for name, (n, size) in sorted(folders.items())] or ["- none"])


def unhashed_line(unhashed: int) -> str:
    return f"- {unhashed} files not covered by hash: run hash"


def _summary(built, summary: SummaryInput, list_sha256: str, unhashed: int) -> str:
    lines = ["# Unrecorded copies", f"{GENERATED}{summary.generated}", "", "## Scans", ""]
    lines += [f"- {s['kind']} scan {s['id']} of {s['root']}: {s['status']}, finished {s['finished']}"
              for s in summary.scans] or ["- none"]
    lines += _class_lines("Class (a): copies with a kept twin", [r for r in built.rows if r.twin])
    lines += _class_lines("Class (b): copies of a reject", [r for r in built.rows if not r.twin])
    lines += ["", "## Examined", "",
              "`recorded` counts every file a catalog records, the kept twins included; "
              "held files are counted, not listed.", "", "| reason | files | bytes |", "| --- | ---: | ---: |"]
    lines += [f"| {reason} | {files} | {size} |" for reason, (files, size) in built.examined.items()]
    lines += ["", "## Purge results", "",
              f"- result files read: {built.purged.results}", f"- purged rejects in them: {built.purged.rejects}"]
    lines += ["", "## Not checked", ""]
    lines += ([f"- {v.source}: {v.status}" for v in summary.views if v.excluded]
              + [f"- limiting: {cause}" for cause in summary.limiting]
              + ([unhashed_line(unhashed)] if unhashed else [])) or ["- none"]
    lines += ["", "## Removal list", "", f"- SHA-256: {list_sha256}"]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: str, built, *, roots: int, summary: SummaryInput, unhashed: int = 0) -> list[str]:
    if any(len(r.dests) != roots for r in built.rows):
        raise ValueError("a copy does not carry one destination per root")
    targets = [os.path.join(out_dir, name) for name in (LIST, SUMMARY)]
    os.makedirs(out_dir, exist_ok=True)
    try:
        write_csv(targets[0] + ".tmp", *_list_rows(built.rows, roots))
        with open(targets[0] + ".tmp", "rb") as handle:
            list_sha256 = hashlib.sha256(handle.read()).hexdigest()
        with open(targets[1] + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_summary(built, summary, list_sha256, unhashed))
        if os.path.exists(targets[1]):
            os.remove(targets[1])  # a folder without the summary is never taken for a finished run
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
