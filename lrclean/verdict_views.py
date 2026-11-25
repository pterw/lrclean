"""Sections 3, 4 and 7: row lists built from `classify.Verdict` lists.

Pure functions: no file, no database. Rows are sorted so two runs agree.
"""

from collections import defaultdict
from collections.abc import Sequence

from lrclean import paths
from lrclean.classify import NOT_RECORDED, Verdict

SAFE = "safe_candidate"
UNIQUE = "unique_unrecorded"
NEEDS_EYE_KINDS = {
    "needs_eye_no_recorded": "no_recorded_member",
    "needs_eye_many_recorded": "many_recorded",
    "same_file": "same_file",
    "coverage_incomplete": "coverage_incomplete",
    "changed_since_scan": "changed_since_scan",
    "not_compared": "not_compared",
}


def safe_candidate_rows(verdicts: Sequence[Verdict]) -> list[dict]:
    path_of = {v.file_id: v.path_orig for v in verdicts}
    rows = []
    for v in verdicts:
        if v.klass != SAFE:
            continue
        if v.twin_file_id not in path_of:
            raise ValueError(f"safe candidate {v.path_orig} has no twin verdict "
                             f"(twin_file_id={v.twin_file_id!r}) in the list")
        rows.append({"path": v.path_orig, "folder": paths.split_path(v.path_orig)[0],
                     "bytes": v.size, "twin_path": path_of[v.twin_file_id], "group": v.group,
                     "how_decided": v.how_decided})
    return sorted(rows, key=lambda r: (r["path"], r["group"]))


def needs_eye_rows(verdicts: Sequence[Verdict]) -> list[dict]:
    rows = [{"kind": NEEDS_EYE_KINDS[v.klass], "group": v.group, "path": v.path_orig,
             "folder": paths.split_path(v.path_orig)[0], "bytes": v.size,
             "recorded": "yes" if v.recorded else "no", "how_decided": v.how_decided}
            for v in verdicts if v.klass in NEEDS_EYE_KINDS]
    return sorted(rows, key=lambda r: (r["kind"], r["group"], r["path"]))


def large_unreferenced_rows(verdicts: Sequence[Verdict], *, coverage_complete: bool) -> list[dict]:
    files: dict[str, int] = defaultdict(int)
    sizes: dict[str, int] = defaultdict(int)
    for v in verdicts:
        if v.klass == UNIQUE:
            folder = paths.split_path(v.path_orig)[0]
            files[folder] += 1
            sizes[folder] += v.size
    how = "no other library file shares its size and sample; no catalog records it"
    if not coverage_complete:
        how = NOT_RECORDED + "a catalog that could not be read may record these files"
    rows = [{"folder": f, "files": files[f], "bytes": sizes[f], "how_decided": how}
            for f in files]
    return sorted(rows, key=lambda r: (-r["bytes"], r["folder"]))
