"""Neutral result-folder discovery helpers for SeePhot photometry CSVs."""

from __future__ import annotations

# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar


RESULT_SCAN_ATTEMPTS = 2
RESULT_SCAN_RETRY_DELAY_SECONDS = 0.05
RESULT_KIND_LIGHTCURVE = "lightcurve"
RESULT_KIND_SINGLE = "single_measurement"
RESULT_VARIANT_RAW = "raw"
RESULT_VARIANT_BINNED = "binned"
RESULT_VARIANT_SINGLE = "single"
DEFAULT_RESULT_TELESCOPE = "Seestar S50"
RESULT_TELESCOPE_SPECS: dict[str, dict[str, object]] = {
    "Seestar S30": {
        "sensor": "Sony IMX662",
        "pixel_scale_arcsec_px": 3.99,
    },
    "Seestar S30pro": {
        "sensor": "Sony IMX585",
        "pixel_scale_arcsec_px": 3.76,
    },
    "Seestar S50": {
        "sensor": "Sony IMX462",
        "pixel_scale_arcsec_px": 2.39,
    },
    "Seestar S50Pro": {
        "sensor": "OmniVision OS08B10",
        "pixel_scale_arcsec_px": 2.30,
    },
}
RESULT_TELESCOPE_CHOICES = tuple(RESULT_TELESCOPE_SPECS)
BINNED_RESULT_NAME_RE = re.compile(
    r"^(?P<target>.+)_result_curve_bin(?P<value>[1-9][0-9]*)(?P<unit>img|sec)\.csv$",
    re.IGNORECASE,
)
RAW_RESULT_NAME_RE = re.compile(
    r"^(?P<target>.+)_result_curve\.csv$",
    re.IGNORECASE,
)
SINGLE_RESULT_NAME_RE = re.compile(
    r"^(?P<target>.+)_single_field_zp_measurement\.csv$",
    re.IGNORECASE,
)
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
    result_kind: str = RESULT_KIND_LIGHTCURVE
    quality_status: str = ""
    quality_flag: str = ""
    calibrated_mag: str = ""
    calibrated_mag_error: str = ""
    field_reference_used: str = ""
    check_delta_mag: str = ""
    has_calibrated_check: bool = False
    result_variant: str = RESULT_VARIANT_RAW
    result_variant_label: str = "Raw"


@dataclass(frozen=True)
class ResultFilename:
    """Canonical interpretation of one supported SeePhot result filename."""

    result_kind: str
    target_stem: str
    variant: str
    variant_label: str
    binning_mode: str = ""
    binning_value: int | None = None


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
    invalid_results: tuple[ResultScanIssue, ...]
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


def parse_result_filename(path_or_name: Path | str) -> ResultFilename | None:
    """Return the single central filename contract for SeePhot result CSVs."""

    name = Path(path_or_name).name
    if name.casefold() == "result_curve.csv":
        return ResultFilename(
            RESULT_KIND_LIGHTCURVE,
            "result",
            RESULT_VARIANT_RAW,
            "Raw",
        )
    match = BINNED_RESULT_NAME_RE.fullmatch(name)
    if match is not None:
        value = int(match.group("value"))
        unit = match.group("unit").casefold()
        mode = "count" if unit == "img" else "seconds"
        label = f"bin{value}{unit}"
        return ResultFilename(
            RESULT_KIND_LIGHTCURVE,
            match.group("target"),
            RESULT_VARIANT_BINNED,
            label,
            mode,
            value,
        )
    match = RAW_RESULT_NAME_RE.fullmatch(name)
    if match is not None:
        return ResultFilename(
            RESULT_KIND_LIGHTCURVE,
            match.group("target"),
            RESULT_VARIANT_RAW,
            "Raw",
        )
    match = SINGLE_RESULT_NAME_RE.fullmatch(name)
    if match is not None:
        return ResultFilename(
            RESULT_KIND_SINGLE,
            match.group("target"),
            RESULT_VARIANT_SINGLE,
            "Single",
        )
    return None


def _is_result_curve_filename(name: str) -> bool:
    """Return whether a filename is a supported series-result CSV name."""

    parsed = parse_result_filename(name)
    return parsed is not None and parsed.result_kind == RESULT_KIND_LIGHTCURVE


def _is_single_measurement_filename(name: str) -> bool:
    """Return whether a filename is a supported Single Measurement CSV name."""

    parsed = parse_result_filename(name)
    return parsed is not None and parsed.result_kind == RESULT_KIND_SINGLE


def _is_photometry_result_filename(name: str) -> bool:
    return _is_result_curve_filename(name) or _is_single_measurement_filename(name)


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
    """Return the target file stem for a supported photometry result CSV."""

    parsed = parse_result_filename(result_csv)
    if parsed is not None:
        return parsed.target_stem
    raise RuntimeError(f"Unexpected result CSV filename: {result_csv.name}")


def result_variant_label(result_csv: Path) -> str:
    """Return a short user-facing label for the result variant."""

    parsed = parse_result_filename(result_csv)
    if parsed is None:
        raise RuntimeError(f"Unexpected result CSV filename: {result_csv.name}")
    return parsed.variant_label


def result_output_stem(result_csv: Path) -> str:
    """Return the filename stem used by result-specific derivative files."""

    parsed = parse_result_filename(result_csv)
    if parsed is None:
        raise RuntimeError(f"Unexpected result CSV filename: {result_csv.name}")
    if parsed.result_kind == RESULT_KIND_SINGLE:
        return result_csv.stem
    return result_csv.stem.replace("_result_curve", "", 1)


def result_variant_token(result_csv: Path) -> str:
    """Return the stable filesystem token for a derived result variant."""

    parsed = parse_result_filename(result_csv)
    if parsed is None:
        raise RuntimeError(f"Unexpected result CSV filename: {result_csv.name}")
    if parsed.variant != RESULT_VARIANT_BINNED:
        return ""
    unit = "img" if parsed.binning_mode == "count" else "sec"
    return f"bin{parsed.binning_value}{unit}"


def bav_output_directory(result_csv: Path) -> Path:
    """Return an isolated BAV directory for the selected result variant."""

    base = result_csv.parent / "BAV"
    token = result_variant_token(result_csv)
    return base / token if token else base


def raw_lightcurve_result_path(output_dir: Path, target_stem: str) -> Path:
    """Return the canonical raw light-curve result path."""

    return Path(output_dir) / f"{target_stem}_result_curve.csv"


def single_measurement_result_path(output_dir: Path, target_stem: str) -> Path:
    """Return the canonical Single Measurement result path."""

    return Path(output_dir) / f"{target_stem}_single_field_zp_measurement.csv"


def binned_result_path(source_result_csv: Path, mode: str, value: int) -> Path:
    """Return the canonical binned-result path for one raw light curve."""

    parsed = parse_result_filename(source_result_csv)
    if (
        parsed is None
        or parsed.result_kind != RESULT_KIND_LIGHTCURVE
        or parsed.variant != RESULT_VARIANT_RAW
    ):
        raise ValueError("Binning output requires a canonical raw light-curve filename.")
    if mode not in {"count", "seconds"}:
        raise ValueError("Binning mode must be 'count' or 'seconds'.")
    if value < 2:
        raise ValueError("A bin needs at least two input measurements.")
    unit = "img" if mode == "count" else "sec"
    return source_result_csv.with_name(
        f"{parsed.target_stem}_result_curve_bin{value}{unit}.csv"
    )


def result_kind_for_csv(
    result_csv: Path,
    metadata: dict[str, str] | None = None,
    first_row: dict[str, str] | None = None,
) -> str | None:
    """Classify a result by metadata/content, with its canonical name as fallback."""

    if metadata is None:
        metadata = read_result_metadata_header(result_csv)
    mode = metadata.get("MODE", "").strip().casefold().replace("-", "_")
    if mode in {"single", "single_measurement"}:
        return RESULT_KIND_SINGLE
    if mode in {"curve", "lightcurve", "light_curve"}:
        return RESULT_KIND_LIGHTCURVE
    if first_row is None:
        first_row = first_result_data_row(result_csv)
    method = first_row.get("calibration_method", "").strip().casefold().replace("-", "_")
    if method in {"single_field_zp", "field_zp", "field_zero_point"}:
        return RESULT_KIND_SINGLE
    if _is_single_measurement_filename(result_csv.name):
        return RESULT_KIND_SINGLE
    if _is_result_curve_filename(result_csv.name):
        return RESULT_KIND_LIGHTCURVE
    return None


def aavso_report_path(result_csv: Path) -> Path:
    """Return the AAVSO report path used by the main lightcurve app."""

    return (
        result_csv.parent
        / "AAVSO"
        / f"{result_output_stem(result_csv)}_aavso_extended.txt"
    )


def instrumental_csv_for_result_csv(result_csv: Path) -> Path:
    """Return the instrumental measurement source for a supported result."""

    parsed = parse_result_filename(result_csv)
    if parsed is None:
        raise RuntimeError(f"Unexpected result CSV filename: {result_csv.name}")
    diagnostics_dir = result_csv.parent / "diagnostics"
    if parsed.result_kind == RESULT_KIND_SINGLE:
        filename = f"{parsed.target_stem}_single_instrumental_photometry.csv"
    else:
        filename = f"{parsed.target_stem}_instrumental_photometry.csv"
    new_path = diagnostics_dir / filename
    old_path = result_csv.parent / filename
    return new_path if new_path.exists() or not old_path.exists() else old_path


def single_references_csv_for_result_csv(result_csv: Path) -> Path:
    """Return the Field-ZP reference CSV associated with a Single result."""

    parsed = parse_result_filename(result_csv)
    if parsed is None or parsed.result_kind != RESULT_KIND_SINGLE:
        raise ValueError("Field-ZP references require a Single Measurement result.")
    filename = f"{parsed.target_stem}_single_field_zp_references.csv"
    new_path = result_csv.parent / "diagnostics" / filename
    old_path = result_csv.parent / filename
    return new_path if new_path.exists() or not old_path.exists() else old_path


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


def target_name_matches_path(
    path: Path,
    result_csv: Path,
    metadata: dict[str, str] | None = None,
    target_name: str | None = None,
) -> bool:
    """Match the complete object field of a current BAV output filename."""

    parsed = parse_result_filename(result_csv)
    if parsed is None:
        return False
    object_name = (
        (metadata or {}).get("OBJECT_NAME", "").strip()
        or str(target_name or "").strip()
        or parsed.target_stem
    )
    expected_object = bav_filename_object_text(object_name)
    if parsed.result_kind == RESULT_KIND_SINGLE:
        return path.name.casefold() == f"{expected_object}_Einzelhelligkeit.txt".casefold()

    match = re.fullmatch(
        r"(?P<constellation>[A-Za-z]{3})_(?P<object>.+)_"
        r"[0-9]+(?:\.[0-9]+)?_[A-Za-z]+(?:\.pdf|_(?:MiniMax|Report)\.txt)",
        path.name,
        re.IGNORECASE,
    )
    if match is None:
        return False
    # The writer removes a trailing constellation token from the technical
    # object field, e.g. R Dra -> Dra_R_<HJD>_<observer>.pdf.
    constellation = match.group("constellation")
    shortened = re.sub(
        rf"(?:^|\s+){re.escape(constellation)}$",
        "",
        expected_object,
        flags=re.IGNORECASE,
    ).strip()
    expected_object = shortened or expected_object
    return match.group("object").casefold() == expected_object.casefold()


def bav_report_files(
    result_csv: Path,
    metadata: dict[str, str] | None = None,
    target_name: str | None = None,
    available_files: tuple[Path, ...] | None = None,
) -> list[Path]:
    """Return BAV export files for the current result target, if any."""

    if available_files is None:
        bav_dir = bav_output_directory(result_csv)
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


def result_csv_has_calibrated_check_delta(result_csv: Path) -> bool:
    """Return whether a valid row can be plotted for the independent check star."""

    if not result_csv.exists():
        return False
    with result_csv.open(newline="") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            fieldnames = [item.strip() for item in line.rstrip("\n\r").split(",")]
            if not any(fieldnames):
                return False
            for row in csv.DictReader(handle, fieldnames=fieldnames):
                if row.get("valid", "1").strip().casefold() in {"0", "false", "no"}:
                    continue
                if row.get("quality_status", "").strip().upper() == "INVALID":
                    continue
                try:
                    value = float(row.get("check_delta_mag", ""))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    return True
            return False
    return False


def lightcurve_result_candidate_from_csv(
    result_csv: Path,
    *,
    metadata: dict[str, str] | None = None,
    matching_bav_files: tuple[Path, ...] | None = None,
) -> LightcurveResultCandidate | None:
    """Return one neutral result-browser row for a supported result CSV."""

    if not _is_photometry_result_filename(result_csv.name):
        return None
    if metadata is None:
        metadata = read_result_metadata_header(result_csv)
    first_row = first_result_data_row(result_csv)
    result_kind = result_kind_for_csv(result_csv, metadata, first_row)
    if result_kind is None:
        return None
    row_count = count_result_rows(result_csv)
    if row_count == 0:
        return None
    target_name = metadata.get("OBJECT_NAME", "").strip() or result_target_stem(result_csv).replace("_", " ")
    report_date = report_date_for_result(result_csv, metadata)
    if matching_bav_files is None:
        matching_bav_files = tuple(bav_report_files(result_csv, metadata, target_name))
    quality_status = first_row.get("quality_status", "").strip().upper()
    if first_row.get("valid", "1").strip().casefold() in {"0", "false", "no"}:
        quality_status = "INVALID"
    parsed_filename = parse_result_filename(result_csv)
    if parsed_filename is None:
        return None
    if parsed_filename.variant == RESULT_VARIANT_BINNED:
        variant_label = parsed_filename.variant_label
    elif (
        metadata.get("SOURCE_PROVENANCE") == "SINGLE_FRAME_LG_SERIES"
        and metadata.get("BINNING_ALLOWED") == "1"
    ):
        variant_label = "Raw"
    else:
        variant_label = ""
    return LightcurveResultCandidate(
        result_csv=result_csv,
        target_name=target_name,
        variable_type=metadata.get("OBJECT_VAR_TYPE", "").strip(),
        report_date=report_date,
        source_directory_name=result_csv.parent.name,
        row_count=row_count,
        png_exists=result_csv.with_suffix(".png").exists(),
        aavso_exists=aavso_report_path(result_csv).exists(),
        bav_file_count=len(matching_bav_files),
        result_kind=result_kind,
        quality_status=quality_status,
        quality_flag=first_row.get("quality_flag", "").strip(),
        calibrated_mag=(
            first_row.get("target_calibrated_mag", "").strip()
            or first_row.get("calibrated_mag", "").strip()
        ),
        calibrated_mag_error=(
            first_row.get("target_calibrated_mag_error", "").strip()
            or first_row.get("calibrated_mag_error", "").strip()
        ),
        field_reference_used=first_row.get("field_reference_used", "").strip(),
        check_delta_mag=first_row.get("check_delta_mag", "").strip(),
        has_calibrated_check=result_csv_has_calibrated_check_delta(result_csv),
        result_variant=parsed_filename.variant,
        result_variant_label=variant_label,
    )


def _scan_result_csv_paths(
    root: Path,
    *,
    include_archive: bool,
    result_kind: str = RESULT_KIND_LIGHTCURVE,
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

            filename_matches = (
                _is_single_measurement_filename(entry.name)
                if result_kind == RESULT_KIND_SINGLE
                else _is_result_curve_filename(entry.name)
            )
            if filename_matches:
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
    result_kind: str = RESULT_KIND_LIGHTCURVE,
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
    if result_kind not in {RESULT_KIND_LIGHTCURVE, RESULT_KIND_SINGLE}:
        raise ValueError(f"Unsupported result kind: {result_kind}")
    if bav_only and result_kind != RESULT_KIND_LIGHTCURVE:
        raise ValueError("BAV result discovery supports light curves only")

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
        result_kind=result_kind,
        progress_callback=progress_callback,
        cancel_requested=cancel_requested,
    )

    candidates: list[LightcurveResultCandidate] = []
    invalid_results: list[ResultScanIssue] = []
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
                base_bav_dir = bav_directories.get(result_csv.parent)
                if base_bav_dir is None:
                    continue
                bav_dir = bav_output_directory(result_csv)
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
        if candidate is not None and candidate.result_kind == result_kind:
            candidates.append(candidate)
        elif candidate is None:
            invalid_results.append(
                ResultScanIssue(
                    path=result_csv,
                    message="Result CSV has no data rows and is not loadable.",
                )
            )

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
        invalid_results=tuple(invalid_results),
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


def discover_single_measurement_results(results_dir: Path) -> list[LightcurveResultCandidate]:
    """Return all Single Measurement result CSVs below one results directory."""

    return list(
        discover_lightcurve_results_with_diagnostics(
            results_dir,
            bav_only=False,
            result_kind=RESULT_KIND_SINGLE,
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
