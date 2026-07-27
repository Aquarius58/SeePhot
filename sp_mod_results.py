"""Neutral result-folder discovery helpers for SeePhot light-curve CSVs."""

from __future__ import annotations

# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


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

    if not result_csv.name.endswith("_result_curve.csv"):
        raise RuntimeError(f"Unexpected result CSV filename: {result_csv.name}")
    return result_csv.name.removesuffix("_result_curve.csv")


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
    """Return simple BAV filename variants like ``Dra BK`` for ``BK Dra``."""

    raw = re.sub(r"\s+", " ", (text or "").replace("_", " ").strip())
    parts = raw.split()
    if len(parts) != 2 or len(parts[1]) != 3:
        return set()
    name, constellation = parts
    if not constellation.isalpha():
        return set()
    return {
        f"{constellation} {name}",
        f"{constellation}_{name}",
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
) -> list[Path]:
    """Return BAV export files for the current result target, if any."""

    bav_dir = result_csv.parent / "BAV"
    if not bav_dir.is_dir():
        return []
    return sorted(
        path
        for path in bav_dir.iterdir()
        if path.is_file() and target_name_matches_path(path, result_csv, metadata, target_name)
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


def lightcurve_result_candidate_from_csv(result_csv: Path) -> LightcurveResultCandidate | None:
    """Return one neutral result-browser row for a result CSV."""

    if not result_csv.name.endswith("_result_curve.csv"):
        return None
    metadata = read_result_metadata_header(result_csv)
    target_name = metadata.get("OBJECT_NAME", "").strip() or result_target_stem(result_csv).replace("_", " ")
    report_date = report_date_for_result(result_csv, metadata)
    return LightcurveResultCandidate(
        result_csv=result_csv,
        target_name=target_name,
        variable_type=metadata.get("OBJECT_VAR_TYPE", "").strip(),
        report_date=report_date,
        source_directory_name=result_csv.parent.name,
        row_count=count_result_rows(result_csv),
        png_exists=result_csv.with_suffix(".png").exists(),
        aavso_exists=aavso_report_path(result_csv).exists(),
        bav_file_count=len(bav_report_files(result_csv, metadata, target_name)),
    )


def discover_lightcurve_results(results_dir: Path) -> list[LightcurveResultCandidate]:
    """Return all light-curve result CSVs below one results directory."""

    root = Path(results_dir).expanduser()
    if not root.is_dir():
        raise RuntimeError(f"Results folder not found: {root}")
    candidates: list[LightcurveResultCandidate] = []
    for result_csv in sorted(root.rglob("*_result_curve.csv")):
        if any(part == "archive" for part in result_csv.relative_to(root).parts):
            continue
        candidate = lightcurve_result_candidate_from_csv(result_csv)
        if candidate is not None:
            candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.target_name.casefold(),
            candidate.report_date,
            str(candidate.result_csv).casefold(),
        ),
    )
