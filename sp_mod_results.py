"""Neutral result-folder discovery helpers for SeePhot light-curve CSVs."""

from __future__ import annotations

# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar


RESULT_SCAN_ATTEMPTS = 2
RESULT_SCAN_RETRY_DELAY_SECONDS = 0.05
RESULT_SCAN_PRUNED_DIRECTORY_NAMES = frozenset(
    {"aavso", "bav", "diagnostics", "siril_lightcurve_tmp"}
)
_T = TypeVar("_T")


@dataclass(frozen=True)
class LightcurveResultCandidate:
    result_csv: Path
    target_name: str
    variable_type: str
    report_date: str
    source_directory_name: str
    row_count: int
    png_exists: bool
    aavso_exists: bool
    bav_file_count: int


@dataclass(frozen=True)
class ResultScanIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class LightcurveResultScan:
    candidates: tuple[LightcurveResultCandidate, ...]
    folders_scanned: int
    result_csv_found: int
    issues: tuple[ResultScanIssue, ...]
    archive_directories_skipped: int
    symlink_directories_skipped: int
    cancelled: bool


def _retry_result_scan_io(
    operation: Callable[[], _T],
    *,
    attempts: int = RESULT_SCAN_ATTEMPTS,
) -> _T:
    """Retry a short NAS filesystem operation before exposing its error."""

    last_error: OSError | None = None
    for attempt in range(max(1, attempts)):
        try:
            return operation()
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(RESULT_SCAN_RETRY_DELAY_SECONDS)
    assert last_error is not None
    raise last_error


def _is_result_curve_filename(name: str) -> bool:
    """Return whether a filename is a supported series-result CSV name."""

    folded = name.casefold()
    return folded == "result_curve.csv" or folded.endswith("_result_curve.csv")


def read_result_metadata_header(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if not path.exists():
        return metadata
    with path.open(newline="") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            content = line[1:].strip()
            if "=" not in content:
                continue
            key, value = content.split("=", 1)
            metadata[key.strip()] = value.strip()
    return metadata


def parse_iso_date(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
        return text[:10]
    return ""


def utc_date_from_jd(value: object) -> str:
    """Return UTC calendar date for a Julian Date value."""

    try:
        jd = float(str(value).strip())
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp((jd - 2440587.5) * 86400.0, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def first_result_data_row(path: Path) -> dict[str, str]:
    """Return the first non-comment CSV data row from a result file."""

    if not path.exists():
        return {}
    with path.open(newline="") as handle:
        for line in handle:
            if not line.startswith("#"):
                fieldnames = [item.strip() for item in line.rstrip("\n\r").split(",")]
                reader = csv.DictReader(handle, fieldnames=fieldnames)
                return next(reader, {}) or {}
    return {}


def report_date_for_result(result_csv: Path, metadata: dict[str, str]) -> str:
    """Return the result date, falling back to the first measurement row."""

    for key in ("REPORT_DATE", "UTC_START"):
        parsed = parse_iso_date(metadata.get(key, ""))
        if parsed:
            return parsed

    row = first_result_data_row(result_csv)
    parsed = utc_date_from_jd(row.get("jd", ""))
    if parsed:
        return parsed
    phot_time_source = str(row.get("phot_time_source", "")).strip().upper()
    if phot_time_source.startswith("MJD"):
        try:
            mjd = float(str(row.get("phot_time", "")).strip())
        except (TypeError, ValueError):
            mjd = None
        if mjd is not None:
            parsed = utc_date_from_jd(mjd + 2400000.5)
            if parsed:
                return parsed

    return datetime.now(timezone.utc).date().isoformat()


def result_target_stem(result_csv: Path) -> str:
    """Return the target file stem for a result curve CSV."""

    suffix = "_result_curve.csv"
    if result_csv.name.casefold() == "result_curve.csv":
        return "result"
    if not result_csv.name.casefold().endswith(suffix):
        raise RuntimeError(f"Unexpected result CSV filename: {result_csv.name}")
    return result_csv.name[: -len(suffix)]


def aavso_report_path(result_csv: Path) -> Path:
    """Return the AAVSO report path used by the main lightcurve app."""

    return (
        result_csv.parent
        / "AAVSO"
        / (result_csv.stem.replace("_result_curve", "") + "_aavso_extended.txt")
    )


def normalized_target_match_text(text: str) -> str:
    """Return a loose filename-match text for target-specific sidecar files."""

    return re.sub(r"\s+", " ", text.replace("_", " ").strip()).casefold()


def bav_filename_object_text(text: str, fallback: str = "target") -> str:
    """Return the BAV object-name component without importing the BAV plugin."""

    raw = re.sub(r"\s+", " ", (text or fallback).replace("_", " ").strip())
    chars: list[str] = []
    for char in raw:
        if char.isalnum() or char in (" ", "-", "_", ".", "+"):
            chars.append(char)
        else:
            chars.append("_")
    component = "".join(chars).strip(" ._")
    return (component or fallback)[:120]


def bav_constellation_first_texts(text: str) -> set[str]:
    """Return BAV filename variants like ``Dra BK`` for ``BK Dra``."""

    raw = re.sub(r"\s+", " ", (text or "").replace("_", " ").strip())
    parts = raw.split()
    if len(parts) < 2 or len(parts[-1]) != 3:
        return set()
    constellation = parts[-1]
    if not constellation.isalpha():
        return set()
    object_name = " ".join(parts[:-1])
    return {
        f"{constellation} {object_name}",
        f"{constellation}_{object_name}",
        f"{constellation} {raw}",
        f"{constellation}_{raw}",
    }


def target_name_match_values(
    result_csv: Path,
    metadata: dict[str, str] | None = None,
    target_name: str | None = None,
) -> set[str]:
    """Return normalized target names that may appear in target-specific files."""

    values = {
        result_target_stem(result_csv),
        result_target_stem(result_csv).replace("_", " "),
    }
    if metadata is not None:
        object_name = metadata.get("OBJECT_NAME", "").strip()
        if object_name:
            values.add(object_name)
            values.add(bav_filename_object_text(object_name, result_target_stem(result_csv)))
            values.update(bav_constellation_first_texts(object_name))
    if target_name:
        values.add(target_name)
        values.add(bav_filename_object_text(target_name, result_target_stem(result_csv)))
        values.update(bav_constellation_first_texts(target_name))
    return {normalized_target_match_text(value) for value in values if value.strip()}


def target_name_matches_path(
    path: Path,
    result_csv: Path,
    metadata: dict[str, str] | None = None,
    target_name: str | None = None,
) -> bool:
    """Return whether a sidecar filename belongs to the current result target."""

    filename_text = normalized_target_match_text(path.stem)
    return any(value and value in filename_text for value in target_name_match_values(result_csv, metadata, target_name))


def bav_report_files(
    result_csv: Path,
    metadata: dict[str, str] | None = None,
    target_name: str | None = None,
    available_files: tuple[Path, ...] | None = None,
) -> list[Path]:
    """Return BAV export files for the current result target, if any."""

    if available_files is None:
        bav_dir = result_csv.parent / "BAV"
        if not bav_dir.is_dir():
            return []
        available_files = tuple(path for path in bav_dir.iterdir() if path.is_file())
    return sorted(
        path
        for path in available_files
        if target_name_matches_path(path, result_csv, metadata, target_name)
    )


def count_result_rows(result_csv: Path) -> int:
    """Return the number of data rows in a result CSV."""

    if not result_csv.exists():
        return 0
    with result_csv.open(newline="") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            fieldnames = [item.strip() for item in line.rstrip("\n\r").split(",")]
            if not any(fieldnames):
                return 0
            return sum(1 for _row in csv.DictReader(handle, fieldnames=fieldnames))
    return 0


def lightcurve_result_candidate_from_csv(
    result_csv: Path,
    *,
    metadata: dict[str, str] | None = None,
    matching_bav_files: tuple[Path, ...] | None = None,
) -> LightcurveResultCandidate | None:
    """Return one neutral result-browser row for a result CSV."""

    if not _is_result_curve_filename(result_csv.name):
        return None
    if metadata is None:
        metadata = read_result_metadata_header(result_csv)
    target_name = metadata.get("OBJECT_NAME", "").strip() or result_target_stem(result_csv).replace("_", " ")
    report_date = report_date_for_result(result_csv, metadata)
    if matching_bav_files is None:
        matching_bav_files = tuple(bav_report_files(result_csv, metadata, target_name))
    return LightcurveResultCandidate(
        result_csv=result_csv,
        target_name=target_name,
        variable_type=metadata.get("OBJECT_VAR_TYPE", "").strip(),
        report_date=report_date,
        source_directory_name=result_csv.parent.name,
        row_count=count_result_rows(result_csv),
        png_exists=result_csv.with_suffix(".png").exists(),
        aavso_exists=aavso_report_path(result_csv).exists(),
        bav_file_count=len(matching_bav_files),
    )


def _scan_result_csv_paths(
    root: Path,
    *,
    include_archive: bool,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[list[Path], dict[Path, Path], int, list[ResultScanIssue], int, int, bool]:
    """Scan explicitly so unreadable NAS directories are reported, not hidden."""

    paths: list[Path] = []
    folders_scanned = 0
    issues: list[ResultScanIssue] = []
    archive_directories_skipped = 0
    symlink_directories_skipped = 0
    cancelled = False
    bav_directories: dict[Path, Path] = {}
    pending = [root]

    while pending:
        if cancel_requested is not None and cancel_requested():
            cancelled = True
            break
        directory = pending.pop()

        def read_entries() -> list[os.DirEntry[str]]:
            with os.scandir(directory) as entries:
                return list(entries)

        try:
            entries = _retry_result_scan_io(read_entries)
        except OSError as exc:
            issues.append(
                ResultScanIssue(
                    path=directory,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        folders_scanned += 1
        child_directories: list[Path] = []
        for entry in entries:
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                issues.append(
                    ResultScanIssue(
                        path=Path(entry.path),
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            if is_directory:
                if not include_archive and entry.name == "archive":
                    archive_directories_skipped += 1
                    continue
                if entry.name.casefold() == "bav":
                    bav_directories[directory] = Path(entry.path)
                    continue
                if entry.name.casefold() in RESULT_SCAN_PRUNED_DIRECTORY_NAMES:
                    continue
                child_directories.append(Path(entry.path))
                continue

            if entry.is_symlink():
                try:
                    if entry.is_dir(follow_symlinks=True):
                        if entry.name.casefold() == "bav":
                            bav_directories[directory] = Path(entry.path)
                            continue
                        symlink_directories_skipped += 1
                        continue
                except OSError as exc:
                    issues.append(
                        ResultScanIssue(
                            path=Path(entry.path),
                            message=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    continue

            if _is_result_curve_filename(entry.name):
                paths.append(Path(entry.path))

        pending.extend(sorted(child_directories, key=lambda path: str(path).casefold(), reverse=True))
        if progress_callback is not None and (
            folders_scanned == 1 or folders_scanned % 25 == 0
        ):
            progress_callback(folders_scanned, len(paths))

    return (
        sorted(paths, key=lambda path: str(path).casefold()),
        bav_directories,
        folders_scanned,
        issues,
        archive_directories_skipped,
        symlink_directories_skipped,
        cancelled,
    )


def discover_lightcurve_results_with_diagnostics(
    results_dir: Path,
    *,
    bav_only: bool = False,
    include_archive: bool | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> LightcurveResultScan:
    """Return result rows plus evidence about incomplete filesystem scans."""

    root = Path(results_dir).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"Results folder not found: {root}")
    if include_archive is None:
        include_archive = bav_only

    (
        result_paths,
        bav_directories,
        folders_scanned,
        issues,
        archive_directories_skipped,
        symlink_directories_skipped,
        cancelled,
    ) = _scan_result_csv_paths(
        root,
        include_archive=include_archive,
        progress_callback=progress_callback,
        cancel_requested=cancel_requested,
    )

    candidates: list[LightcurveResultCandidate] = []
    bav_directory_cache: dict[Path, tuple[Path, ...]] = {}
    for result_index, result_csv in enumerate(result_paths):
        if progress_callback is not None and (
            result_index == 0 or result_index % 10 == 0
        ):
            progress_callback(folders_scanned, len(result_paths))
        if cancel_requested is not None and cancel_requested():
            cancelled = True
            break
        try:
            _retry_result_scan_io(result_csv.stat)
            metadata = _retry_result_scan_io(lambda: read_result_metadata_header(result_csv))
            target_name = (
                metadata.get("OBJECT_NAME", "").strip()
                or result_target_stem(result_csv).replace("_", " ")
            )
            matching_bav_files: tuple[Path, ...] | None = None
            if bav_only:
                bav_dir = bav_directories.get(result_csv.parent)
                if bav_dir is None:
                    continue
                if bav_dir not in bav_directory_cache:
                    def read_bav_files() -> tuple[Path, ...]:
                        with os.scandir(bav_dir) as entries:
                            return tuple(
                                Path(entry.path)
                                for entry in entries
                                if entry.is_file(follow_symlinks=True)
                            )

                    try:
                        available_bav_files = read_bav_files()
                    except (FileNotFoundError, NotADirectoryError):
                        available_bav_files = ()
                    except OSError:
                        available_bav_files = _retry_result_scan_io(read_bav_files)
                    bav_directory_cache[bav_dir] = available_bav_files
                available_bav_files = bav_directory_cache[bav_dir]
                if not available_bav_files:
                    continue
                matching_bav_files = tuple(
                    bav_report_files(
                        result_csv,
                        metadata,
                        target_name,
                        available_bav_files,
                    )
                )
                if not matching_bav_files:
                    continue

            candidate = _retry_result_scan_io(
                lambda: lightcurve_result_candidate_from_csv(
                    result_csv,
                    metadata=metadata,
                    matching_bav_files=matching_bav_files,
                )
            )
        except (OSError, UnicodeError, csv.Error) as exc:
            issues.append(
                ResultScanIssue(
                    path=result_csv,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        if candidate is not None:
            candidates.append(candidate)

    ordered_candidates = tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.target_name.casefold(),
                candidate.report_date,
                str(candidate.result_csv).casefold(),
            ),
        )
    )
    return LightcurveResultScan(
        candidates=ordered_candidates,
        folders_scanned=folders_scanned,
        result_csv_found=len(result_paths),
        issues=tuple(issues),
        archive_directories_skipped=archive_directories_skipped,
        symlink_directories_skipped=symlink_directories_skipped,
        cancelled=cancelled,
    )


def discover_lightcurve_results(results_dir: Path) -> list[LightcurveResultCandidate]:
    """Return all light-curve result CSVs below one results directory."""

    return list(
        discover_lightcurve_results_with_diagnostics(
            results_dir,
            bav_only=False,
            include_archive=False,
        ).candidates
    )


def discover_bav_lightcurve_results(results_dir: Path) -> list[LightcurveResultCandidate]:
    """Return light-curve results with target-specific BAV output files."""

    return list(
        discover_lightcurve_results_with_diagnostics(
            results_dir,
            bav_only=True,
            include_archive=True,
        ).candidates
    )
