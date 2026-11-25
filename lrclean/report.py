"""CSV and summary writer for the phase A report."""

import csv
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lrclean import paths

HOW = "how_decided"
TOP_FOLDERS = 10
GENERATED = "Generated: "


@dataclass(frozen=True)
class Section:
    number: int
    filename: str
    title: str
    columns: tuple[str, ...]


def _section(number: int, filename: str, title: str, columns: str) -> Section:
    return Section(number, filename, title, tuple(columns.split(", ")) + (HOW,))


SECTIONS: dict[int, Section] = {
    s.number: s
    for s in (
        _section(1, "01_catalogs.csv", "Catalogs",
                 "catalog, role, status, db_version, records, wal_bytes, has_lock, excluded_by_owner"),
        _section(2, "02_shoot_catalogs.csv", "Shoot catalogs",
                 "catalog, records_present, records_not_read, missing_moved, missing_same_name_only, "
                 "missing_nothing, root_folders, backup_same_name_only_lower_bound, "
                 "backup_nothing_lower_bound, uncatalogued_in_tree"),
        _section(3, "03_safe_candidates.csv", "Safe candidates", "path, folder, bytes, twin_path, group"),
        _section(4, "04_needs_your_eye.csv", "Needs your eye", "kind, group, path, folder, bytes, recorded"),
        _section(5, "05_external_copies.csv", "External copies",
                 "kind, folder, backup_roots, raws, copied_sampled, copied_verified, no_copy, bytes"),
        _section(6, "06_raw_jpeg_pairs.csv", "Raw and JPEG pairs",
                 "folder, pairs, jpeg_bytes, sidecar_pairs, sidecar_bytes, own_photo_pairs, own_photo_bytes, "
                 "uncatalogued_pairs, uncatalogued_bytes, same_time_pairs, same_time_bytes"),
        _section(7, "07_large_unreferenced.csv", "Large unreferenced", "folder, files, bytes"),
        _section(8, "08_missing.csv", "Missing", "path, kind, capture_time, state"),
        _section(9, "09_containers.csv", "Containers",
                 "container, type, bytes, files, catalog_beside, catalog_state, reclaimable"),
        _section(10, "10_loose_photos.csv", "Loose photos", "folder, raws, jpegs, bytes, listed"),
        _section(11, "11_not_read.csv", "Not read", "kind, item, detail"),
    )
}


def _validate(sections: Mapping[int, Sequence[Mapping]], limits: Mapping[int, str]) -> None:
    for number, section in SECTIONS.items():
        limits[number]  # a missing key is a KeyError
        expected = set(section.columns)
        for row in sections[number]:
            if set(row) != expected:
                raise ValueError(
                    f"section {number}: row keys differ from the columns: {sorted(set(row) ^ expected)}"
                )
            if not isinstance(row[HOW], str) or not row[HOW].strip():
                raise ValueError(f"section {number}: empty {HOW}")
            if "bytes" in expected:
                _bytes(row)  # a non-numeric size is a ValueError before any file is written


def _write_csv(path: str, section: Section, rows: Sequence[Mapping]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(section.columns)
        for row in rows:
            writer.writerow([row[column] for column in section.columns])


def _bytes(row: Mapping, column: str = "bytes") -> int:
    value = row[column] or 0
    if isinstance(value, float):
        raise ValueError(f"{column} is not whole: {value!r}")
    return int(value)


def _by_folder(rows: Sequence[Mapping]) -> list[str]:
    totals: dict[str, int] = {}
    spelling: dict[str, str] = {}
    for row in rows:
        key = paths.norm(row["folder"])
        spelling.setdefault(key, row["folder"])
        totals[key] = totals.get(key, 0) + _bytes(row)
    top = sorted(totals, key=lambda key: (-totals[key], key))[:TOP_FOLDERS]
    lines = ["", "By folder (ten largest):", "", "| folder | bytes |", "| --- | ---: |"]
    lines += [f"| {spelling[key]} | {totals[key]} |" for key in top]
    return lines


def _section_block(section: Section, rows: Sequence[Mapping], limit: str) -> list[str]:
    lines = [f"## {section.number}. {section.title} ({section.filename})", "", f"Rows: {len(rows)}"]
    if section.number == 9:
        reclaimable = sum(_bytes(r) for r in rows if r["reclaimable"] == "yes")
        lines += [f"Total size: {sum(_bytes(r) for r in rows)}", f"Reclaimable: {reclaimable}"]
    elif "bytes" in section.columns:
        lines.append(f"Bytes: {sum(_bytes(r) for r in rows)}")
    if section.number == 4:
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        lines += [f"Kind {kind}: {counts[kind]}" for kind in sorted(counts)]
    lines.append(f"Could not see: {limit}")
    if "folder" in section.columns and "bytes" in section.columns:
        lines += _by_folder(rows)
    return lines + [""]


def _coverage_block(coverage: Mapping) -> list[str]:
    lines = ["# Coverage", "", f"{GENERATED}{coverage['generated']}", "", "## Roots", ""]
    lines += [f"- {r['root']}: {r['status']}" for r in coverage["roots"]] or ["- none"]
    lines += ["", "## Catalogs", ""]
    for cat in coverage["catalogs"]:
        flag = " (limits coverage)" if cat["limits_coverage"] else ""
        lines.append(f"- {cat['catalog']}: {cat['status']}{flag}")
    if not coverage["catalogs"]:
        lines.append("- none")
    lines += ["", "## Limiting causes", ""]
    lines += [f"- {cause}" for cause in coverage["limiting"]] or ["- none"]
    lines += ["", "## Scans", ""]
    lines += [f"- {s['kind']} scan {s['id']} of {s['root']}: {s['status']}, finished {s['finished']}"
              for s in coverage["scans"]] or ["- none"]
    return lines + [""]


def read_generated(summary_path: str) -> str | None:
    """The `Generated:` time of a written summary.md, or None when it has no such line or cannot be read."""
    try:
        with open(summary_path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(GENERATED):
                    return line[len(GENERATED):].strip()
    except (OSError, UnicodeDecodeError):
        pass
    return None


def write_report(
    out_dir: str,
    sections: Mapping[int, Sequence[Mapping]],
    coverage: Mapping,
    limits: Mapping[int, str],
) -> None:
    _validate(sections, limits)
    lines = _coverage_block(coverage)
    for number, section in SECTIONS.items():
        lines += _section_block(section, sections[number], limits[number])
    os.makedirs(out_dir, exist_ok=True)
    pending = []  # real file names whose ".tmp" copy may exist; replaced only when all are written
    try:
        for number, section in SECTIONS.items():
            target = os.path.join(out_dir, section.filename)
            pending.append(target)
            _write_csv(target + ".tmp", section, sections[number])
        target = os.path.join(out_dir, "summary.md")
        pending.append(target)
        with open(target + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        if os.path.exists(target):
            os.remove(target)  # a folder without summary.md is never taken for a finished report
        for target in pending:
            os.replace(target + ".tmp", target)
    except OSError:
        for target in pending:
            try:
                os.remove(target + ".tmp")
            except OSError:
                pass  # already replaced, never created, or locked: the original error is raised
        raise
