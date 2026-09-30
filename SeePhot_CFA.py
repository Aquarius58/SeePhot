"""Seestar Photometry Stack

Author: Thomas Rudolph (Aquarius58)
Contact: https://github.com/Aquarius58/siril-seestar-stack/issues
Repository: https://github.com/Aquarius58/siril-seestar-stack
Copyright (c) 2026 Thomas Rudolph (Aquarius58)
SPDX-License-Identifier: GPL-3.0-or-later

Overview
--------
This Siril Python script prepares and stacks original Seestar `.fit` frames for
downstream photometry workflows. It is a preprocessing tool; it does not perform
photometric measurement.

Usage
-----
Run the script from Siril's Python script menu, select the folder containing the
original Seestar FITS frames, choose the CFA photometry channels, select Stack
Groups by seconds, frame count, or ALL, then click Start.

Inputs
------
Input files are the original Seestar `.fit` frames located directly in the
selected folder. `DATE-OBS` is required; files without it are skipped. Seestar
frames with `DATE-EXP` use `DATE-OBS` as the exposure start and `DATE-EXP` as
the exposure end; without `DATE-EXP`, `DATE-OBS` is treated as the exposure
end. If `EXPTIME` is missing, the script uses a 10 second fallback.

Outputs
-------
Original input files are never modified. Result folders are written next to the
source folder. CFA channel folders use suffixes such as `_l`, `_r`, `_g`, `_b`;
stack folders add a stack suffix such as `-stack100sec`, `-stack10img`, or
`-stackall`. Temporary folders use `<source>-tmp...` names and are removed when
the run ends.

Overwrite safety
----------------
If result folders already exist, the script lists them before the run starts and
asks for confirmation. Existing result folders are cleared only after explicit
confirmation.

CFA photometry channels
-----------------------
For CFA/Bayer input, the script uses Siril to split measured CFA samples without
debayer interpolation. L/R/G/B channel products keep the original Seestar image
size by replicating each measured Bayer-cell value over its original 2x2 block.
L is the mean of R, G1, G2, and B; G is the mean of G1 and G2; R and B use the
measured red and blue CFA samples. `FILTER` and `CHANMODE` document the derived
channel in the FITS header.

Stacking and FITS timing
------------------------
Frames are registered before stacking. Stack Groups can be defined by duration,
frame count, or ALL. Stack blocks must meet the configured minimum number of
registered frames; time-based groups must also reach a configured fraction of the
requested duration. Generated files use `DATE-OBS` as UTC exposure start,
`DATE-END` as UTC exposure end, and `DATE-AVG` / `MJD-AVG` as the exposure-
weighted midpoint for photometry. `EXPTIME` is the summed exposure time of the
used frames, excluding gaps. `NCOMBINE` is the number of frames included in the
stack.

Compatibility and requirements
------------------------------
This script must be run inside Siril. It requires Siril 1.3.0 or newer,
sirilpy 1.0.13 or newer, and Python packages PyQt6, astropy, and numpy. Missing
Python packages are installed through Siril when possible.

Limitations
-----------
The script does not calibrate with dark, flat, or bias frames, does not perform
photometric measurement, and expects a single folder containing original Seestar
frames.
"""

from __future__ import annotations

# Seestar Photometry Stack
# Author: Thomas Rudolph (Aquarius58)
# Contact: https://github.com/Aquarius58/siril-seestar-stack/issues
# Repository: https://github.com/Aquarius58/siril-seestar-stack
# Copyright (c) 2026 Thomas Rudolph (Aquarius58)
# SPDX-License-Identifier: GPL-3.0-or-later

import shutil
import sys
import time
import importlib.util
import os
import statistics
import tempfile
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import sirilpy as s

if not s.utility.check_module_version(">=1.0.13"):
    print("Error: sirilpy module is too old and does not support this script.")
    sys.exit(1)

def ensure_importable_module(import_name: str, package_name: str | None = None) -> None:
    """Install a package through Siril only when its import module is missing."""

    if importlib.util.find_spec(import_name) is not None:
        return
    s.ensure_installed(package_name or import_name)


ensure_importable_module("PyQt6")
ensure_importable_module("astropy")
ensure_importable_module("numpy")

import numpy as np  # noqa: E402
from astropy.io import fits  # noqa: E402
from astropy.io.fits.verify import VerifyWarning  # noqa: E402
from astropy.time import Time  # noqa: E402
from astropy.utils.exceptions import AstropyUserWarning  # noqa: E402
from PyQt6.QtCore import QThread, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QPalette  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyleFactory,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

warnings.filterwarnings(
    "ignore",
    message=r"The following header keyword is invalid or follows an unrecognized non-standard convention:.*",
    category=AstropyUserWarning,
)

# User settings
SCRIPT_VERSION = "0.5.0"
SIRIL_REQUIRES = "1.3.0"
OUTPUT_BITS_COMMAND = "set16bits"
SSAP_HEADER_VALUE = "SeePhot_CFA.py"

DEFAULT_PLAN_MODE = "time"
DEFAULT_DURATION_PLANS = (100, 1000)
DEFAULT_FRAME_PLANS = (10, 100)
ALL_PLAN_NAME = "ALL"
GROUPING_BEHAVIOR_CONTINUOUS = "continuous"
GROUPING_BEHAVIOR_GAP_AWARE = "gap_aware"

STACK_METHOD = "rej"
REJECTION_LOW = 3.0
REJECTION_HIGH = 3.0
USE_REJECTION_MAPS = True
NONORM = True
MIN_FRAMES_PER_STACK = 2
MIN_STACK_COMPLETION_FRACTION = 0.30

REGISTRATION_TRANSFORM = "similarity"
REGISTRATION_INTERPOLATION = "lanczos4"
REGISTRATION_TWO_PASS = True
REGISTRATION_MAXSTARS = 1000
REGISTRATION_MINPAIRS = 10

TEMP_BASENAME = "tmp"
DEFAULT_SUBFRAME_EXPOSURE = 10.0
TIMING_INTERVAL_TOLERANCE_SECONDS = 1.0
SPLIT_CFA_TO_PHOTOMETRY_CHANNELS = True
KEEP_CHANNEL_IMAGES = True
DEFAULT_OVERWRITE_RESULTS = False
KEEP_TEMP_ON_ERROR = False
CLEANUP_RETRIES = 6
CLEANUP_RETRY_DELAY_SECONDS = 0.5
VERBOSE_COMMAND_LOG = False
VERBOSE_FRAME_LOG = False
PROGRESS_LOG_INTERVAL = 50

WINDOW_TITLE = f"SeePhot Stack {SCRIPT_VERSION}"
MAIN_WINDOW_OBJECT_NAME = "seephot_seestar_stack_main_window"
SINGLE_INSTANCE_LOCK_PATH = Path(tempfile.gettempdir()) / "seephot_seestar.lock"
SIRIL_CONNECT_ATTEMPTS = 6
SIRIL_CONNECT_RETRY_DELAY_SECONDS = 0.2
WINDOW_WIDTH = 380
FULL_CHANNEL_WINDOW_WIDTH = 430
WINDOW_HEIGHT = 520
APP_DARK_STYLESHEET = """
QWidget {
    background-color: #202124;
    color: #eceff4;
    selection-background-color: #2f6fed;
    selection-color: #ffffff;
}
QDialog, QMessageBox {
    background-color: #202124;
}
QGroupBox {
    border: 1px solid #3c4048;
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    background-color: #202124;
    color: #d8dee9;
}
QLabel {
    background: transparent;
}
QLineEdit, QTextEdit, QSpinBox {
    background-color: #17191d;
    border: 1px solid #3c4048;
    color: #eceff4;
}
QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled {
    background-color: #1b202a;
    color: #9aa3b2;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #6b7280;
    border-radius: 3px;
    background-color: #17191d;
}
QCheckBox::indicator:hover {
    border-color: #8ab4ff;
}
QCheckBox::indicator:checked {
    background-color: #2f6fed;
    border-color: #4f8cff;
}
QCheckBox::indicator:checked:hover {
    background-color: #3b7cff;
}
QCheckBox::indicator:checked:disabled {
    background-color: #32405a;
    border-color: #4b5563;
}
QCheckBox::indicator:unchecked:disabled {
    background-color: #1b202a;
    border-color: #3c4048;
}
QCheckBox:disabled {
    color: #9aa3b2;
}
QPushButton {
    background-color: #2f6fed;
    border: 1px solid #4f8cff;
    border-radius: 5px;
    color: #ffffff;
    padding: 5px 10px;
}
QPushButton:hover {
    background-color: #3b7cff;
}
QPushButton:pressed {
    background-color: #255bc7;
}
QPushButton:disabled {
    background-color: #2a2d33;
    border-color: #3c4048;
    color: #8b94a3;
}
QToolTip {
    color: #111111;
    background-color: #fff8c6;
    border: 1px solid #8a7a2f;
}
"""


def configure_app_theme(app: QApplication) -> None:
    """Apply the local dark Qt theme used on every platform."""

    if sys.platform.startswith("win"):
        try:
            app.setStyle(QStyleFactory.create("Fusion"))
        except Exception:
            pass

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#eceff4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#17191d"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#24272d"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#fff8c6"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#eceff4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2a2d33"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#eceff4"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#9aa3b2"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#9aa3b2"))
    app.setPalette(palette)
    app.setStyleSheet(APP_DARK_STYLESHEET)

CFA_CHANNELS = {
    "L": {
        "suffix": "_l",
        "filter": "L",
        "chanmode": "CFA_L_MEAN",
        "label": "L",
        "comment": "CFA luminance mean",
        "mode_comment": "Mean of R, G1, G2, B CFA samples",
    },
    "R": {
        "suffix": "_r",
        "filter": "R",
        "chanmode": "CFA_R_RAW",
        "label": "R",
        "comment": "CFA red sample",
        "mode_comment": "Measured red CFA sample",
    },
    "G": {
        "suffix": "_g",
        "filter": "G",
        "chanmode": "CFA_G_MEAN",
        "label": "G",
        "comment": "CFA green mean",
        "mode_comment": "Mean of measured G1 and G2 CFA samples",
    },
    "B": {
        "suffix": "_b",
        "filter": "B",
        "chanmode": "CFA_B_RAW",
        "label": "B",
        "comment": "CFA blue sample",
        "mode_comment": "Measured blue CFA sample",
    },
}
DEFAULT_CFA_CHANNELS = ("L",)
BAYER_HEADER_KEYS = ("BAYERPAT", "XBAYROFF", "YBAYROFF", "ROWORDER")
CFA_REQUIRED_COLORS = ("R", "G1", "G2", "B")
LIGHTCURVE_IMAGE_SOURCES = frozenset(DEFAULT_CFA_CHANNELS + ("G",))

@dataclass(frozen=True)
class StackPlan:
    name: str
    suffix: str
    block_size: int | None
    duration_seconds: int | None


@dataclass(frozen=True)
class FitsFrame:
    path: Path
    end_time: datetime
    exptime: float
    timing_source: str = "legacy_date_obs_end"


@dataclass(frozen=True)
class ExposureTimeMetadata:
    start_time: datetime
    end_time: datetime
    avg_time: datetime
    total_exposure: float
    frame_count: int


@dataclass
class RunStats:
    source_frames: int = 0
    skipped_input_files: int = 0
    channel_frames_written: int = 0
    channel_frames_preserved: int = 0
    stack_results_written: int = 0
    skipped_stack_blocks: int = 0
    warnings: int = 0


def is_hidden_fits(path: Path) -> bool:
    return path.name.startswith(".") or path.name.startswith("._")


def is_fits_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in {".fit", ".fits"} and not is_hidden_fits(path)


def parse_fits_time(value: object, path: Path, key: str) -> datetime:
    if value is None:
        raise ValueError(f"{path.name} lacks {key}")

    text = str(value).strip()
    if not text:
        raise ValueError(f"{path.name} has empty {key}")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date_obs(value: object, path: Path) -> datetime:
    return parse_fits_time(value, path, "DATE-OBS")


def parse_exptime(header: fits.Header, path: Path, log_callback) -> float:
    raw_value = header.get("EXPTIME", header.get("EXPOSURE"))
    if raw_value is None:
        log_callback(
            f"[WARN] {path.name}: EXPTIME is missing; using {DEFAULT_SUBFRAME_EXPOSURE:g}s."
        )
        return DEFAULT_SUBFRAME_EXPOSURE

    try:
        exptime = float(raw_value)
    except (TypeError, ValueError):
        log_callback(
            f"[WARN] {path.name}: EXPTIME is invalid; using {DEFAULT_SUBFRAME_EXPOSURE:g}s."
        )
        return DEFAULT_SUBFRAME_EXPOSURE

    if exptime <= 0:
        log_callback(
            f"[WARN] {path.name}: EXPTIME <= 0; using {DEFAULT_SUBFRAME_EXPOSURE:g}s."
        )
        return DEFAULT_SUBFRAME_EXPOSURE

    return exptime


def parse_exptime_for_output(header: fits.Header) -> float:
    raw_value = header.get("EXPTIME", header.get("EXPOSURE"))
    try:
        exptime = float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_SUBFRAME_EXPOSURE
    if exptime <= 0:
        return DEFAULT_SUBFRAME_EXPOSURE
    return exptime


def read_frame(path: Path, log_callback) -> FitsFrame:
    with fits.open(path) as hdul:
        header = hdul[0].header
        exptime = parse_exptime(header, path, log_callback)
        if "DATE-EXP" in header:
            start_time = parse_fits_time(header.get("DATE-OBS"), path, "DATE-OBS")
            end_time = parse_fits_time(header.get("DATE-EXP"), path, "DATE-EXP")
            header_exposure = (end_time - start_time).total_seconds()
            tolerance = max(TIMING_INTERVAL_TOLERANCE_SECONDS, exptime * 0.01)
            if header_exposure <= 0 or abs(header_exposure - exptime) > tolerance:
                raise ValueError(
                    f"{path.name} has inconsistent DATE-OBS/DATE-EXP interval "
                    f"({header_exposure:g}s; EXPTIME={exptime:g}s)"
                )
            return FitsFrame(path, end_time, exptime, "date_obs_date_exp")
        if "DATE-END" in header:
            return FitsFrame(
                path,
                parse_fits_time(header.get("DATE-END"), path, "DATE-END"),
                exptime,
                "normalized_date_end",
            )
        return FitsFrame(
            path,
            parse_date_obs(header.get("DATE-OBS"), path),
            exptime,
            "legacy_date_obs_end",
        )


def timing_summary(frames: list[FitsFrame]) -> str:
    counts = {
        source: sum(frame.timing_source == source for frame in frames)
        for source in ("date_obs_date_exp", "legacy_date_obs_end", "normalized_date_end")
    }
    parts: list[str] = []
    if counts["date_obs_date_exp"]:
        parts.append(
            f"{counts['date_obs_date_exp']} modern DATE-OBS start + DATE-EXP end"
        )
    if counts["legacy_date_obs_end"]:
        parts.append(
            f"{counts['legacy_date_obs_end']} legacy DATE-OBS end (start derived from EXPTIME)"
        )
    if counts["normalized_date_end"]:
        parts.append(
            f"{counts['normalized_date_end']} normalized DATE-OBS start + DATE-END end"
        )
    return "FITS timing: " + "; ".join(parts) + "."


def image_has_bayer_header(path: Path) -> bool:
    with fits.open(path) as hdul:
        header = hdul[0].header
        return any(key in header for key in BAYER_HEADER_KEYS)


def mixed_cfa_input_message(
    cfa_frames: list[FitsFrame],
    non_cfa_frames: list[FitsFrame],
) -> str:
    """Explain why original CFA and processed FITS cannot share one run."""

    source_dir = cfa_frames[0].path.parent
    backup_count = sum(
        frame.path.with_suffix(".bfit").is_file()
        for frame in non_cfa_frames
    )
    examples = ", ".join(frame.path.name for frame in non_cfa_frames[:3])
    backup_note = (
        f" {backup_count} corresponding original CFA backup(s) with the .bfit "
        "extension are present but are not used automatically."
        if backup_count
        else ""
    )
    return (
        f"Mixed input folder: {len(cfa_frames)} original CFA frame(s) and "
        f"{len(non_cfa_frames)} non-CFA or already processed FITS frame(s) were "
        f"found. CFA extraction cannot safely combine these files.{backup_note} "
        "Keep only original CFA .fit/.fits frames in the selected folder, then "
        f"start again. Example processed file(s): {examples}. "
        f"Source folder: {source_dir}"
    )


def frames_need_cfa_split(frames: list[FitsFrame]) -> bool:
    if not SPLIT_CFA_TO_PHOTOMETRY_CHANNELS or not frames:
        return False

    cfa_frames: list[FitsFrame] = []
    non_cfa_frames: list[FitsFrame] = []
    for frame in frames:
        try:
            has_bayer_header = image_has_bayer_header(frame.path)
        except Exception:
            has_bayer_header = False
        (cfa_frames if has_bayer_header else non_cfa_frames).append(frame)

    if cfa_frames and non_cfa_frames:
        raise ValueError(mixed_cfa_input_message(cfa_frames, non_cfa_frames))
    return bool(cfa_frames)


def normalize_image_source(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text in LIGHTCURVE_IMAGE_SOURCES:
        return text
    if "CFA_L" in text:
        return "L"
    if "CFA_G" in text:
        return "G"
    return ""


def image_source_from_header(header: fits.Header) -> str:
    sources: set[str] = set()
    for key in ("FILTER", "CHANMODE", "CHANNEL", "CFA_CHAN", "CFA_CHANNEL"):
        raw_value = header.get(key)
        if raw_value is None or str(raw_value).strip() == "":
            continue
        source = normalize_image_source(raw_value)
        if not source:
            return ""
        sources.add(source)
    if len(sources) == 1:
        return next(iter(sources))
    return ""


def image_source_for_path(path: Path) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VerifyWarning)
        return image_source_from_header(fits.getheader(path))


def supported_lightcurve_image_source_for_frames(frames: list[FitsFrame]) -> str:
    sources: set[str] = set()
    for frame in frames[:3]:
        source = image_source_for_path(frame.path)
        if not source:
            return ""
        sources.add(source)
    if len(sources) == 1:
        return next(iter(sources))
    return ""


def require_supported_lightcurve_source(frames: list[FitsFrame], source_dir: Path) -> str:
    image_source = supported_lightcurve_image_source_for_frames(frames)
    if image_source:
        return image_source
    raise ValueError(
        "Selected input is not CFA/Bayer data and does not carry unambiguous "
        "Light Curve channel metadata. Select original Seestar CFA frames, or "
        "use an already prepared L/G folder with FITS FILTER=L or FILTER=G. "
        f"Source folder: {source_dir}"
    )


def collect_valid_fits(source_dir: Path, log_callback, skipped_callback=None) -> list[FitsFrame]:
    frames: list[FitsFrame] = []
    for path in sorted(source_dir.iterdir()):
        if not is_fits_file(path):
            continue
        try:
            frames.append(read_frame(path, log_callback))
        except Exception as exc:
            if skipped_callback is not None:
                skipped_callback()
            log_callback(f"[WARN] Skipping {path.name}: {exc}")
    return sorted(frames, key=lambda frame: frame.end_time)


def build_stack_plans(mode: str, values: tuple[int, int]) -> tuple[StackPlan, StackPlan, StackPlan]:
    if mode == "frames":
        return (
            StackPlan(str(values[0]), f"{values[0]}img", values[0], None),
            StackPlan(str(values[1]), f"{values[1]}img", values[1], None),
            StackPlan(ALL_PLAN_NAME, "all", None, None),
        )
    if mode == "time":
        return (
            StackPlan(f"{values[0]}s", f"{values[0]}sec", None, values[0]),
            StackPlan(f"{values[1]}s", f"{values[1]}sec", None, values[1]),
            StackPlan(ALL_PLAN_NAME, "all", None, None),
        )
    raise ValueError(f"Unsupported plan mode: {mode}")


def split_into_blocks(
    frames: list[FitsFrame],
    plan: StackPlan,
    behavior: str = GROUPING_BEHAVIOR_CONTINUOUS,
) -> list[tuple[int, list[FitsFrame]]]:
    """Create CFA stack blocks without requiring any external SeePhot module."""
    if behavior not in {GROUPING_BEHAVIOR_CONTINUOUS, GROUPING_BEHAVIOR_GAP_AWARE}:
        raise ValueError(f"Unsupported grouping behavior: {behavior}")
    if not frames:
        return []
    # ALL has one unambiguous meaning: stack every selected frame. Observation
    # gaps only affect the finite frame-count and duration-based plans.
    if plan.block_size is None and plan.duration_seconds is None:
        return [(0, frames)]

    segment_starts = [0]
    if behavior == GROUPING_BEHAVIOR_GAP_AWARE:
        midpoints = [
            frame.end_time - timedelta(seconds=frame.exptime / 2)
            for frame in frames
        ]
        cadences = [
            (current - previous).total_seconds()
            for previous, current in zip(midpoints, midpoints[1:])
        ]
        pause_threshold = 3 * (statistics.median(cadences) if cadences else 0.0)
        for index, (previous, current) in enumerate(zip(midpoints, midpoints[1:]), start=1):
            if (current - previous).total_seconds() > pause_threshold:
                segment_starts.append(index)
    segment_starts.append(len(frames))

    blocks: list[tuple[int, list[FitsFrame]]] = []
    for start, stop in zip(segment_starts, segment_starts[1:]):
        segment = frames[start:stop]
        if plan.block_size is not None:
            blocks.extend(
                (start + index, segment[index:index + plan.block_size])
                for index in range(0, len(segment), plan.block_size)
            )
            continue

        assert plan.duration_seconds is not None
        index = 0
        while index < len(segment):
            first_frame = segment[index]
            block_start = first_frame.end_time - timedelta(seconds=first_frame.exptime)
            end_index = index + 1
            while end_index < len(segment):
                covered_seconds = (segment[end_index].end_time - block_start).total_seconds()
                if covered_seconds > plan.duration_seconds:
                    break
                end_index += 1
            blocks.append((start + index, segment[index:end_index]))
            index = end_index
    return blocks


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def mjd_utc(value: datetime) -> float:
    return float(Time(value.astimezone(timezone.utc)).mjd)


def compute_exposure_time_metadata(frames: list[FitsFrame]) -> ExposureTimeMetadata:
    if not frames:
        raise ValueError("Cannot compute exposure metadata for an empty frame list")

    # FitsFrame stores the end time after resolving the original-header variant.
    start_times = [
        frame.end_time - timedelta(seconds=frame.exptime)
        for frame in frames
    ]
    end_times = [frame.end_time for frame in frames]
    total_exposure = sum(frame.exptime for frame in frames)
    if total_exposure <= 0:
        raise ValueError("Non-positive total exposure computed for block")

    weighted_midpoint_timestamp = sum(
        (
            (start_time + timedelta(seconds=frame.exptime / 2)).timestamp()
            * frame.exptime
        )
        for frame, start_time in zip(frames, start_times, strict=True)
    ) / total_exposure

    return ExposureTimeMetadata(
        start_time=min(start_times).astimezone(timezone.utc),
        end_time=max(end_times).astimezone(timezone.utc),
        avg_time=datetime.fromtimestamp(weighted_midpoint_timestamp, tz=timezone.utc),
        total_exposure=total_exposure,
        frame_count=len(frames),
    )


def minimum_stack_exposure(plan: StackPlan) -> float:
    if plan.duration_seconds is None:
        return 0.0
    return plan.duration_seconds * MIN_STACK_COMPLETION_FRACTION


def stack_meets_minimum(frames: list[FitsFrame], plan: StackPlan) -> tuple[bool, float, float]:
    total_exposure = sum(frame.exptime for frame in frames)
    minimum_exposure = minimum_stack_exposure(plan)
    return (
        len(frames) >= MIN_FRAMES_PER_STACK
        and total_exposure >= minimum_exposure
    ), total_exposure, minimum_exposure


def stack_plans_for_input(
    plans: tuple[StackPlan, ...], frames: list[FitsFrame], needs_cfa_split: bool,
) -> tuple[StackPlan, ...]:
    """A lone CFA exposure produces channels, but cannot produce a stack."""

    return () if needs_cfa_split and len(frames) == 1 else plans


def stack_minimum_text(plan: StackPlan) -> str:
    minimum_exposure = minimum_stack_exposure(plan)
    if minimum_exposure <= 0:
        return f"{MIN_FRAMES_PER_STACK} frames"
    return f"{MIN_FRAMES_PER_STACK} frames and {minimum_exposure:g}s total exposure"


def write_exposure_time_header(
    path: Path,
    metadata: ExposureTimeMetadata,
    source_path: Path | None = None,
) -> None:
    with fits.open(path, mode="update") as hdul:
        header = hdul[0].header
        source_header = None
        if source_path is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", VerifyWarning)
                source_header = fits.getheader(source_path)
        image_source = image_source_from_header(source_header) if source_header is not None else ""
        header["SSAP"] = (SSAP_HEADER_VALUE, "Created by Siril Seestar stack app")
        header["SPORIGIN"] = ("CFA_STACK", "SeePhot provenance: CFA stack result")
        header["DATE-OBS"] = (iso_utc(metadata.start_time), "Start of first used exposure (UTC)")
        header["DATE-END"] = (iso_utc(metadata.end_time), "End of last used exposure (UTC)")
        header["DATE-AVG"] = (iso_utc(metadata.avg_time), "Exposure-weighted midpoint (UTC)")
        header["MJD-AVG"] = (mjd_utc(metadata.avg_time), "Exposure-weighted midpoint (MJD)")
        header["EXPTIME"] = (float(metadata.total_exposure), "Summed exposure time in seconds")
        header["NCOMBINE"] = (metadata.frame_count, "Frames stacked")
        if image_source:
            channel = CFA_CHANNELS[image_source]
            header["FILTER"] = (channel["filter"], channel["comment"])
            source_chanmode = str(source_header.get("CHANMODE", "")).strip()
            if normalize_image_source(source_chanmode) == image_source:
                header["CHANMODE"] = source_chanmode
        hdul.flush()


def empty_dir(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    try:
        entries = list(path.iterdir())
    except FileNotFoundError:
        return
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


def remove_dir(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def cleanup_empty_dir(path: Path, log_callback) -> None:
    cleanup_path(path, lambda: empty_dir(path), log_callback)


def cleanup_remove_dir(path: Path, log_callback) -> None:
    cleanup_path(path, lambda: remove_dir(path), log_callback)


def retry_empty_dir(path: Path) -> None:
    retry_path(lambda: empty_dir(path))


def cleanup_path(path: Path, operation, log_callback) -> None:
    last_exc = retry_path(operation, raise_on_failure=False)
    if last_exc is None:
        return

    log_callback(
        f"[WARN] Temporary cleanup failed for {path}: {last_exc}. "
        "The stack results were already written; remove the temporary folder manually later."
    )


def retry_path(operation, raise_on_failure: bool = True) -> Exception | None:
    last_exc: Exception | None = None
    for attempt in range(1, CLEANUP_RETRIES + 1):
        try:
            operation()
            return None
        except FileNotFoundError:
            return None
        except (PermissionError, OSError) as exc:
            last_exc = exc
            if attempt < CLEANUP_RETRIES:
                time.sleep(CLEANUP_RETRY_DELAY_SECONDS)

    if raise_on_failure and last_exc is not None:
        raise last_exc
    return last_exc


def siril_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def cfa_position_color_map(header: fits.Header, path: Path) -> dict[str, int]:
    pattern = str(header.get("BAYERPAT", "")).strip().upper()
    if len(pattern) != 4 or any(color not in {"R", "G", "B"} for color in pattern):
        raise ValueError(f"{path.name}: unsupported or missing BAYERPAT: {pattern!r}")

    green_count = 0
    color_to_position: dict[str, int] = {}
    for position, color in enumerate(pattern):
        if color == "G":
            green_count += 1
            color_to_position[f"G{green_count}"] = position
        else:
            color_to_position[color] = position

    missing = [color for color in CFA_REQUIRED_COLORS if color not in color_to_position]
    if missing:
        raise ValueError(f"{path.name}: BAYERPAT {pattern!r} lacks CFA samples: {', '.join(missing)}")
    return color_to_position


def build_output_header(source_path: Path, channel_key: str) -> fits.Header:
    channel = CFA_CHANNELS[channel_key]
    header = fits.getheader(source_path).copy()
    source_filter = header.get("FILTER")
    source_bayerpat = header.get("BAYERPAT")
    source_frame = read_frame(source_path, lambda _message: None)
    time_metadata = compute_exposure_time_metadata([
        source_frame
    ])
    for key in BAYER_HEADER_KEYS:
        if key in header:
            del header[key]
    if source_filter:
        header["SRCFILT"] = (str(source_filter), "Original source filter")
    if source_bayerpat:
        header["CFAORIG"] = (str(source_bayerpat), "Original CFA pattern")
    header["SSAP"] = (SSAP_HEADER_VALUE, "Created by Siril Seestar stack app")
    header["SPORIGIN"] = ("CFA_CHANNEL", "SeePhot provenance: CFA channel result")
    header["DATE-OBS"] = (iso_utc(time_metadata.start_time), "Start of exposure (UTC)")
    header["DATE-END"] = (iso_utc(time_metadata.end_time), "End of exposure (UTC)")
    header["DATE-AVG"] = (iso_utc(time_metadata.avg_time), "Exposure midpoint (UTC)")
    header["MJD-AVG"] = (mjd_utc(time_metadata.avg_time), "Exposure midpoint (MJD)")
    header["EXPTIME"] = (float(time_metadata.total_exposure), "Exposure time in seconds")
    header["NCOMBINE"] = (time_metadata.frame_count, "Frames combined")
    header["FILTER"] = (channel["filter"], channel["comment"])
    header["CHANMODE"] = (channel["chanmode"], channel["mode_comment"])
    header["CFAINT"] = (False, "No CFA interpolation used")
    header["CFASCALE"] = (1, "Output keeps original image geometry")
    header["CFABLOCK"] = (2, "CFA value replicated over each 2x2 block")
    header["DERIVED"] = (True, "Derived from measured CFA samples")
    return header


def cfa_channel_suffix(channel_key: str) -> str:
    return str(CFA_CHANNELS[channel_key]["suffix"])


def cfa_channel_label(channel_key: str) -> str:
    return str(CFA_CHANNELS[channel_key]["label"])


def grouping_behavior_suffix(behavior: str) -> str:
    if behavior == GROUPING_BEHAVIOR_CONTINUOUS:
        return ""
    if behavior == GROUPING_BEHAVIOR_GAP_AWARE:
        return "_ga"
    raise ValueError(f"Unsupported grouping behavior: {behavior}")


def plan_result_dir(
    source_dir: Path,
    plan: StackPlan,
    channel_key: str | None = None,
    grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS,
) -> Path:
    channel_suffix = cfa_channel_suffix(channel_key) if channel_key is not None else ""
    return source_dir.parent / f"{source_dir.name}{channel_suffix}-stack{plan.suffix}{grouping_behavior_suffix(grouping_behavior)}"


def plan_temp_dir(
    source_dir: Path,
    plan: StackPlan,
    grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS,
) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmp{plan.suffix}{grouping_behavior_suffix(grouping_behavior)}"


def cfa_split_temp_dir(source_dir: Path) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmpcfa"


def channel_temp_dir(source_dir: Path, channel_key: str) -> Path:
    return source_dir.parent / f"{source_dir.name}-tmp{cfa_channel_suffix(channel_key)}"


def channel_result_dir(source_dir: Path, channel_key: str) -> Path:
    return source_dir.parent / f"{source_dir.name}{cfa_channel_suffix(channel_key)}"


def result_dirs_for_run(
    source_dir: Path,
    selected_plans: tuple[StackPlan, ...],
    selected_channels: tuple[str, ...],
    needs_cfa_split: bool,
    grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS,
) -> list[Path]:
    result_dirs: list[Path] = []
    result_channels: tuple[str | None, ...] = selected_channels if needs_cfa_split else (None,)
    for plan in selected_plans:
        for channel_key in result_channels:
            result_dirs.append(plan_result_dir(source_dir, plan, channel_key, grouping_behavior))

    if needs_cfa_split and KEEP_CHANNEL_IMAGES:
        for channel_key in selected_channels:
            result_dirs.append(channel_result_dir(source_dir, channel_key))

    return result_dirs


def existing_result_dirs_for_run(
    source_dir: Path,
    selected_plans: tuple[StackPlan, ...],
    selected_channels: tuple[str, ...],
    needs_cfa_split: bool,
    grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS,
) -> list[Path]:
    return [
        path
        for path in result_dirs_for_run(source_dir, selected_plans, selected_channels, needs_cfa_split, grouping_behavior)
        if path.exists()
    ]


def assert_can_create_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(f"Path exists but is not a directory: {path}")
        return
    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"Target parent directory does not exist: {parent}")
    try:
        path.mkdir()
        path.rmdir()
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot create a directory next to the selected image folder: {path}\n"
            f"Select the specific folder containing the original Seestar FITS frames, not a "
            f"higher-level system or user directory."
        ) from exc
    except OSError as exc:
        raise OSError(f"Cannot create directory: {path} ({exc})") from exc


def build_register_command(log_callback) -> str:
    parts = [f"register {TEMP_BASENAME}_"]
    if REGISTRATION_TWO_PASS:
        parts.append("-2pass")
    parts.append(f"-transf={REGISTRATION_TRANSFORM}")
    parts.append(f"-maxstars={REGISTRATION_MAXSTARS}")
    parts.append(f"-minpairs={REGISTRATION_MINPAIRS}")

    interpolation = REGISTRATION_INTERPOLATION
    if REGISTRATION_TRANSFORM != "shift" and interpolation == "none":
        interpolation = "lanczos4"
        if VERBOSE_COMMAND_LOG:
            log_callback(
                "[INFO] Interpolation automatically changed to lanczos4 because "
                "'none' is only appropriate for shift registration."
            )
    parts.append(f"-interp={interpolation}")
    return " ".join(parts)


def build_seqapplyreg_command() -> str:
    parts = [f"seqapplyreg {TEMP_BASENAME}_"]
    interpolation = REGISTRATION_INTERPOLATION
    if REGISTRATION_TRANSFORM != "shift" and interpolation == "none":
        interpolation = "lanczos4"
    if interpolation != "none":
        parts.append(f"-interp={interpolation}")
    return " ".join(parts)


def build_stack_command(outname: str) -> str:
    registered_sequence = f"r_{TEMP_BASENAME}_"
    parts = [f"stack {registered_sequence}", STACK_METHOD]
    if STACK_METHOD in {"rej", "mean"}:
        parts.extend([f"w {REJECTION_LOW:g} {REJECTION_HIGH:g}"])
    if NONORM:
        parts.append("-nonorm")
    if USE_REJECTION_MAPS and STACK_METHOD == "rej":
        parts.append("-rejmaps")
    parts.append(f"-out={outname}")
    return " ".join(parts)


def collect_registered_files(tmp_dir: Path) -> list[Path]:
    return sorted(path for path in tmp_dir.glob(f"r_{TEMP_BASENAME}_*.fit") if not is_hidden_fits(path))


def registered_frame_indices(registered_files: list[Path], frame_count: int) -> list[int]:
    indices: list[int] = []
    prefix = f"r_{TEMP_BASENAME}_"
    for path in registered_files:
        stem = path.stem
        if not stem.startswith(prefix):
            continue
        try:
            sequence_number = int(stem.removeprefix(prefix))
        except ValueError:
            continue
        index = sequence_number - 1
        if 0 <= index < frame_count:
            indices.append(index)
    return sorted(set(indices))


def registered_stack_metadata(
    block_frames: list[FitsFrame],
    registered_files: list[Path],
) -> ExposureTimeMetadata:
    indices = registered_frame_indices(registered_files, len(block_frames))
    if len(indices) != len(registered_files):
        raise ValueError(
            f"Cannot map registered frames unambiguously to source frames "
            f"({len(indices)}/{len(registered_files)})."
        )
    registered_frames = [block_frames[index] for index in indices]
    return compute_exposure_time_metadata(registered_frames)


def should_log_progress(index: int, total: int) -> bool:
    return index == 1 or index == total or index % PROGRESS_LOG_INTERVAL == 0


def connect_siril_interface(
    siril: object,
    log: Callable[[str], None] | None = None,
) -> None:
    """Connect to Siril, tolerating a connection still being released."""

    for attempt in range(1, SIRIL_CONNECT_ATTEMPTS + 1):
        try:
            siril.connect()
            return
        except Exception as exc:
            message = str(exc).lower()
            transient_connection = (
                "already connected to siril" in message
                or (os.name == "nt" and "pipe is busy" in message)
            )
            if not transient_connection or attempt >= SIRIL_CONNECT_ATTEMPTS:
                raise
            if log is not None:
                log(
                    "[INFO] The previous Siril connection is still being released; "
                    f"retrying ({attempt}/{SIRIL_CONNECT_ATTEMPTS - 1})."
                )
            time.sleep(SIRIL_CONNECT_RETRY_DELAY_SECONDS)


class StackWorker(QThread):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str)

    def __init__(
        self,
        source_dir: str,
        selected_plans: tuple[StackPlan, ...],
        selected_channels: tuple[str, ...],
        allow_overwrite: bool = DEFAULT_OVERWRITE_RESULTS,
        require_lightcurve_filter: bool = False,
        grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS,
    ):
        super().__init__()
        self.source_dir = Path(source_dir).expanduser()
        self.selected_plans = selected_plans
        self.selected_channels = selected_channels
        self.allow_overwrite = allow_overwrite
        self.require_lightcurve_filter = require_lightcurve_filter
        self.grouping_behavior = grouping_behavior
        self.cfa_split_dirs: tuple[Path, ...] | None = None
        self.plan_temp_dirs = tuple(plan_temp_dir(self.source_dir, plan, grouping_behavior) for plan in selected_plans)
        self.stats = RunStats()

    def emit_log(self, message: str) -> None:
        if message.startswith("[WARN]"):
            self.stats.warnings += 1
        print(message)
        self.log.emit(message)

    def count_skipped_input_file(self) -> None:
        self.stats.skipped_input_files += 1

    def emit_summary(self) -> None:
        self.emit_log("[SUMMARY] Run summary")
        self.emit_log(f"[SUMMARY] Source frames used: {self.stats.source_frames}")
        self.emit_log(f"[SUMMARY] Input FITS files skipped: {self.stats.skipped_input_files}")
        self.emit_log(f"[SUMMARY] Channel frames written: {self.stats.channel_frames_written}")
        self.emit_log(f"[SUMMARY] Channel frames preserved: {self.stats.channel_frames_preserved}")
        self.emit_log(f"[SUMMARY] Stack results written: {self.stats.stack_results_written}")
        self.emit_log(f"[SUMMARY] Stack blocks skipped: {self.stats.skipped_stack_blocks}")
        self.emit_log(f"[SUMMARY] Warnings: {self.stats.warnings}")

    def run_cmd(self, siril: s.SirilInterface, command: str) -> None:
        if VERBOSE_COMMAND_LOG:
            self.emit_log(f"> {command}")
        siril.cmd(command)

    def close_siril_display_context(self, siril: s.SirilInterface) -> None:
        try:
            siril.cmd("close")
        except Exception:
            pass

    def move_siril_to_safe_directory(self, siril: s.SirilInterface) -> None:
        safe_dir = self.source_dir.parent
        self.close_siril_display_context(siril)
        self.run_cmd(siril, f'cd "{siril_path(safe_dir)}"')

    def run(self) -> None:
        try:
            result_dir = self.run_stack()
            self.emit_summary()
            if self.selected_plans:
                message = f"Stacking complete. Latest results are in {result_dir}"
            else:
                message = f"Channel export complete. Results are in {result_dir}"
            self.finished.emit(True, message, str(result_dir))
        except Exception as exc:
            self.finished.emit(False, str(exc), "")

    def run_stack(self) -> Path:
        if not self.source_dir.is_dir():
            raise FileNotFoundError(f"Source folder not found: {self.source_dir}")

        source_frames = collect_valid_fits(
            self.source_dir,
            self.emit_log,
            self.count_skipped_input_file,
        )
        if not source_frames:
            raise FileNotFoundError(
                f"No valid .fit/.fits files were found directly in the selected source folder: "
                f"{self.source_dir}\n"
                f"Select the folder that contains the individual original Seestar FITS frames."
            )
        self.stats.source_frames = len(source_frames)
        self.emit_log(f"[INFO] {timing_summary(source_frames)}")

        needs_cfa_split = frames_need_cfa_split(source_frames)
        if needs_cfa_split and not self.selected_channels:
            raise ValueError("Select at least one CFA output channel.")
        effective_plans = stack_plans_for_input(
            self.selected_plans, source_frames, needs_cfa_split,
        )
        if needs_cfa_split and len(source_frames) == 1:
            self.emit_log(
                "[INFO] One CFA frame: exporting selected channels without stacking."
            )
        self.selected_plans = effective_plans
        if self.require_lightcurve_filter and not needs_cfa_split:
            image_source = require_supported_lightcurve_source(source_frames, self.source_dir)
            self.emit_log(
                f"[INFO] Input is already prepared as FILTER={image_source}; stacking without CFA split."
            )
        self.preflight_output_dirs(needs_cfa_split)

        siril = s.SirilInterface()
        connect_siril_interface(siril, self.emit_log)
        if VERBOSE_COMMAND_LOG:
            self.emit_log("[OK] Connected to Siril.")
        self.move_siril_to_safe_directory(siril)

        final_result_dir: Path | None = None
        completed = False
        try:
            frames_by_channel = self.prepare_processing_frames(siril, source_frames, needs_cfa_split)
            self.keep_channel_images(frames_by_channel)
            for channel_key, frames in frames_by_channel.items():
                for plan in self.selected_plans:
                    final_result_dir = self.run_stack_plan(siril, plan, frames, channel_key)
            completed = True
        finally:
            try:
                self.move_siril_to_safe_directory(siril)
                siril.disconnect()
            except Exception:
                pass
            if completed or not KEEP_TEMP_ON_ERROR:
                for directory in self.plan_temp_dirs:
                    cleanup_remove_dir(directory, self.emit_log)
                if self.cfa_split_dirs is not None:
                    for directory in self.cfa_split_dirs:
                        cleanup_remove_dir(directory, self.emit_log)

        if final_result_dir is None:
            if needs_cfa_split and KEEP_CHANNEL_IMAGES:
                return channel_result_dir(self.source_dir, self.selected_channels[-1])
            raise RuntimeError(
                "No stack results were created. Select at least one stack group."
            )
        return final_result_dir

    def prepare_processing_frames(
        self,
        siril: s.SirilInterface,
        source_frames: list[FitsFrame],
        needs_cfa_split: bool,
    ) -> dict[str | None, list[FitsFrame]]:
        if not needs_cfa_split:
            if VERBOSE_FRAME_LOG:
                self.emit_log("[INFO] No CFA/Bayer header keywords found; stacking input frames directly.")
            return {None: source_frames}

        if not self.selected_channels:
            raise ValueError("Select at least one CFA output channel.")

        if VERBOSE_FRAME_LOG:
            channel_labels = ", ".join(cfa_channel_label(channel_key) for channel_key in self.selected_channels)
            self.emit_log(f"[INFO] CFA/Bayer header keywords found; creating channels: {channel_labels}.")
        cfa_split_dir = cfa_split_temp_dir(self.source_dir)
        channel_dirs = {
            channel_key: channel_temp_dir(self.source_dir, channel_key)
            for channel_key in self.selected_channels
        }
        self.cfa_split_dirs = (cfa_split_dir, *channel_dirs.values())

        if cfa_split_dir.exists():
            empty_dir(cfa_split_dir)
        else:
            cfa_split_dir.mkdir(parents=True)
        for channel_dir in channel_dirs.values():
            if channel_dir.exists():
                empty_dir(channel_dir)
            else:
                channel_dir.mkdir(parents=True)

        try:
            frame_count = len(source_frames)
            channel_labels = ", ".join(cfa_channel_label(channel_key) for channel_key in self.selected_channels)
            if frame_count == 1:
                self.emit_log("[INFO] Extracting one CFA frame directly; Siril needs a sequence for seqsplit_cfa.")
            else:
                self.emit_log(f"[INFO] Copying CFA source frames: {frame_count} files.")
                for index, frame in enumerate(source_frames, start=1):
                    shutil.copy2(frame.path, cfa_split_dir / frame.path.name)
                    if should_log_progress(index, frame_count):
                        self.emit_log(f"[INFO] CFA source frames copied: {index}/{frame_count}.")

                self.run_cmd(siril, f"requires {SIRIL_REQUIRES}")
                self.run_cmd(siril, OUTPUT_BITS_COMMAND)
                self.run_cmd(siril, f'cd "{siril_path(cfa_split_dir)}"')
                self.emit_log(f"[INFO] Running Siril CFA split on {frame_count} frames.")
                self.run_cmd(siril, "link tmp -out=.")
                self.run_cmd(siril, "seqsplit_cfa tmp")
                self.emit_log(f"[OK] Siril CFA split complete: {frame_count} frames.")

            for index, frame in enumerate(source_frames, start=1):
                suffix = f"{index:05d}"
                cfa_data = (
                    self.read_single_cfa_samples(frame.path)
                    if frame_count == 1 else self.read_cfa_split(cfa_split_dir, suffix)
                )
                for channel_key, channel_dir in channel_dirs.items():
                    output_path = self.write_cfa_photometry_channel_output(
                        frame.path,
                        channel_dir,
                        channel_key,
                        cfa_data,
                    )
                    if VERBOSE_FRAME_LOG:
                        self.emit_log(f"[OK] {cfa_channel_label(channel_key)} channel written: {output_path.name}")
                if should_log_progress(index, frame_count):
                    self.emit_log(
                        f"[INFO] CFA channel extraction {channel_labels}: "
                        f"{index}/{frame_count}."
                    )

            frames_by_channel: dict[str | None, list[FitsFrame]] = {}
            for channel_key, channel_dir in channel_dirs.items():
                channel_frames = collect_valid_fits(channel_dir, self.emit_log)
                if not channel_frames:
                    raise FileNotFoundError(
                        f"No {cfa_channel_label(channel_key)} FITS files were created in {channel_dir}"
                    )
                frames_by_channel[channel_key] = channel_frames
                self.stats.channel_frames_written += len(channel_frames)
                self.emit_log(
                    f"[OK] {cfa_channel_label(channel_key)} channel frames created: "
                    f"{len(channel_frames)} files."
                )
            return frames_by_channel
        finally:
            if not KEEP_TEMP_ON_ERROR:
                try:
                    self.move_siril_to_safe_directory(siril)
                except Exception:
                    pass
                cleanup_remove_dir(cfa_split_dir, self.emit_log)

    def read_cfa_split(self, workdir: Path, suffix: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return tuple(
            fits.getdata(workdir / f"CFA{position}_tmp_{suffix}.fit").astype(np.float32)
            for position in range(4)
        )

    def read_single_cfa_samples(self, path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Extract one Bayer frame in Siril's CFA0..CFA3 pixel order."""

        data = fits.getdata(path)
        if data.ndim != 2 or data.shape[0] % 2 or data.shape[1] % 2:
            raise ValueError(f"{path.name}: CFA channel extraction needs an even-sized 2D image.")
        return tuple(
            data[row::2, column::2].astype(np.float32)
            for row, column in ((0, 0), (0, 1), (1, 0), (1, 1))
        )

    def expand_cfa_blocks_to_full_resolution(self, data: np.ndarray) -> np.ndarray:
        return np.repeat(np.repeat(data, 2, axis=0), 2, axis=1)

    def write_cfa_photometry_channel_output(
        self,
        source_path: Path,
        output_dir: Path,
        channel_key: str,
        cfa_data: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    ) -> Path:
        header = fits.getheader(source_path)
        color_positions = cfa_position_color_map(header, source_path)
        r = cfa_data[color_positions["R"]]
        g1 = cfa_data[color_positions["G1"]]
        g2 = cfa_data[color_positions["G2"]]
        b = cfa_data[color_positions["B"]]

        if channel_key == "L":
            data = np.rint((r + g1 + g2 + b) / 4.0)
        elif channel_key == "R":
            data = r
        elif channel_key == "G":
            data = np.rint((g1 + g2) / 2.0)
        elif channel_key == "B":
            data = b
        else:
            raise ValueError(f"Unknown CFA channel: {channel_key}")

        data = self.expand_cfa_blocks_to_full_resolution(data)
        data = np.clip(data, 0, 65535).astype(np.uint16)
        output_path = output_dir / f"{source_path.stem}{cfa_channel_suffix(channel_key)}.fit"
        fits.writeto(output_path, data, header=build_output_header(source_path, channel_key), overwrite=True)
        return output_path

    def preflight_output_dirs(self, needs_cfa_split: bool) -> None:
        if VERBOSE_COMMAND_LOG:
            self.emit_log(f"[INFO] Source folder: {self.source_dir}")
            self.emit_log("[INFO] Result and temporary folders will be created next to the source folder.")

        for result_dir in result_dirs_for_run(
            self.source_dir,
            self.selected_plans,
            self.selected_channels,
            needs_cfa_split,
            self.grouping_behavior,
        ):
            if result_dir.exists() and not self.allow_overwrite:
                raise FileExistsError(
                    f"Result folder already exists: {result_dir}. "
                    "Confirm overwrite before starting the run."
                )
            assert_can_create_directory(result_dir)

        for plan in self.selected_plans:
            assert_can_create_directory(plan_temp_dir(self.source_dir, plan, self.grouping_behavior))

        if needs_cfa_split:
            assert_can_create_directory(cfa_split_temp_dir(self.source_dir))
            for channel_key in self.selected_channels:
                assert_can_create_directory(channel_temp_dir(self.source_dir, channel_key))

    def prepare_result_dir(self, result_dir: Path) -> None:
        if result_dir.exists():
            if not self.allow_overwrite:
                raise FileExistsError(f"Result folder already exists: {result_dir}")
            empty_dir(result_dir)
        else:
            result_dir.mkdir(parents=True)

    def keep_channel_images(self, frames_by_channel: dict[str | None, list[FitsFrame]]) -> None:
        if not KEEP_CHANNEL_IMAGES or self.cfa_split_dirs is None:
            return

        for channel_key, frames in frames_by_channel.items():
            if channel_key is None:
                continue

            channel_dir = channel_temp_dir(self.source_dir, channel_key)
            channel_frames = [frame for frame in frames if frame.path.parent == channel_dir]
            if not channel_frames:
                continue

            result_dir = channel_result_dir(self.source_dir, channel_key)
            self.prepare_result_dir(result_dir)
            frame_count = len(channel_frames)
            channel_label = cfa_channel_label(channel_key)
            self.emit_log(f"[INFO] Keeping {channel_label} channel frames: {frame_count} files.")
            for index, frame in enumerate(channel_frames, start=1):
                shutil.copy2(frame.path, result_dir / frame.path.name)
                self.stats.channel_frames_preserved += 1
                if should_log_progress(index, frame_count):
                    self.emit_log(
                        f"[INFO] {channel_label} channel frames copied: "
                        f"{index}/{frame_count}."
                    )
            self.emit_log(
                f"[OK] {channel_label} channel frames preserved: "
                f"{frame_count} files in {result_dir.name}."
            )

    def run_stack_plan(
        self,
        siril: s.SirilInterface,
        plan: StackPlan,
        frames: list[FitsFrame],
        channel_key: str | None,
    ) -> Path:
        tmp_dir = plan_temp_dir(self.source_dir, plan, self.grouping_behavior)
        result_dir = plan_result_dir(self.source_dir, plan, channel_key, self.grouping_behavior)
        completed = False

        self.prepare_result_dir(result_dir)
        self.move_siril_to_safe_directory(siril)
        if tmp_dir.exists():
            retry_path(lambda: remove_dir(tmp_dir))
        else:
            tmp_dir.mkdir(parents=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        try:
            channel_label = cfa_channel_label(channel_key) if channel_key is not None else "Input"
            self.emit_log(f"[INFO] Starting stack group {plan.name} for {channel_label}.")
            self.run_cmd(siril, f"requires {SIRIL_REQUIRES}")
            self.run_cmd(siril, OUTPUT_BITS_COMMAND)

            blocks = split_into_blocks(frames, plan, self.grouping_behavior)
            total_blocks = len(blocks)
            for block_number, (index, block) in enumerate(blocks, start=1):
                if not block:
                    continue

                start_idx = index + 1
                end_idx = index + len(block)
                outname = f"stack_{plan.suffix}_{start_idx:05d}-{end_idx:05d}"
                block_tmp_dir = tmp_dir / f"{outname}_work"

                meets_minimum, block_exposure, minimum_exposure = stack_meets_minimum(block, plan)
                if not meets_minimum:
                    self.stats.skipped_stack_blocks += 1
                    self.emit_log(
                        f"[WARN] Skipping {outname}: only {len(block)} frames / "
                        f"{block_exposure:g}s; requires at least {stack_minimum_text(plan)}."
                    )
                    continue

                self.emit_log(
                    f"[INFO] Stack {channel_label} Group {plan.name} "
                    f"Block {block_number}/{total_blocks}: "
                    f"Frames {start_idx:05d}-{end_idx:05d}."
                )
                if block_tmp_dir.exists():
                    retry_path(lambda: remove_dir(block_tmp_dir))
                block_tmp_dir.mkdir(parents=True)
                for frame_index, frame in enumerate(block, start=1):
                    shutil.copy2(frame.path, block_tmp_dir / f"source_{frame_index:05d}.fit")

                self.run_cmd(siril, f'cd "{siril_path(block_tmp_dir)}"')
                self.run_cmd(siril, f"link {TEMP_BASENAME} -out=.")

                try:
                    self.run_cmd(siril, build_register_command(self.emit_log))
                    if REGISTRATION_TWO_PASS:
                        self.run_cmd(siril, build_seqapplyreg_command())
                except Exception as exc:
                    self.stats.skipped_stack_blocks += 1
                    self.emit_log(
                        f"[WARN] Skipping {outname}: registration failed ({exc})."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue

                registered_files = collect_registered_files(block_tmp_dir)
                if not registered_files:
                    self.stats.skipped_stack_blocks += 1
                    self.emit_log(
                        f"[WARN] Skipping {outname}: registration failed; "
                        f"no registered r_{TEMP_BASENAME}_*.fit files were found."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue
                if len(registered_files) < MIN_FRAMES_PER_STACK:
                    self.stats.skipped_stack_blocks += 1
                    self.emit_log(
                        f"[WARN] Skipping {outname}: only {len(registered_files)} of "
                        f"{len(block)} frames registered; requires at least {stack_minimum_text(plan)}."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue
                if len(registered_files) < len(block):
                    self.emit_log(
                        f"[WARN] {outname}: {len(registered_files)} of {len(block)} frames registered; "
                        "stacking the registered frames only."
                    )

                stack_time = registered_stack_metadata(
                    block,
                    registered_files,
                )
                if stack_time.total_exposure < minimum_exposure:
                    self.stats.skipped_stack_blocks += 1
                    self.emit_log(
                        f"[WARN] Skipping {outname}: registered frames provide only "
                        f"{stack_time.total_exposure:g}s total exposure; requires at least "
                        f"{minimum_exposure:g}s."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue
                try:
                    self.run_cmd(siril, build_stack_command(outname))
                except Exception as exc:
                    self.stats.skipped_stack_blocks += 1
                    self.emit_log(
                        f"[WARN] Skipping {outname}: stacking failed ({exc})."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue

                output_fit = block_tmp_dir / f"{outname}.fit"
                if not output_fit.exists():
                    self.stats.skipped_stack_blocks += 1
                    self.emit_log(
                        f"[WARN] Skipping {outname}: Siril did not create a stack file."
                    )
                    self.move_siril_to_safe_directory(siril)
                    cleanup_remove_dir(block_tmp_dir, self.emit_log)
                    continue

                try:
                    source_header_path = block[0].path if block else None
                    write_exposure_time_header(output_fit, stack_time, source_header_path)
                except Exception as exc:
                    raise RuntimeError(
                        f"Stack file was created, but FITS header update failed for {outname}: {exc}"
                    ) from exc
                shutil.move(str(output_fit), result_dir / output_fit.name)
                self.stats.stack_results_written += 1
                self.emit_log(
                    f"[OK] Group {plan.name} {start_idx:05d}-{end_idx:05d}: "
                    f"{output_fit.name} saved ({stack_time.frame_count}/{len(block)} frames, "
                    f"EXPTIME={stack_time.total_exposure:g}s)."
                )
                self.move_siril_to_safe_directory(siril)
                cleanup_remove_dir(block_tmp_dir, self.emit_log)

            completed = True
            return result_dir
        finally:
            if completed:
                try:
                    self.move_siril_to_safe_directory(siril)
                except Exception:
                    pass
                cleanup_remove_dir(tmp_dir, self.emit_log)


class StackWindow(QWidget):
    stack_result_ready = pyqtSignal(str)

    def __init__(
        self,
        allowed_channels: tuple[str, ...] | None = None,
        lightcurve_mode: bool = False,
        busy_context: dict[str, object] | None = None,
        default_grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS,
    ):
        super().__init__()
        self.setObjectName(MAIN_WINDOW_OBJECT_NAME)
        self.worker: StackWorker | None = None
        self.source_dir = str(Path.home())
        self.lightcurve_mode = lightcurve_mode
        self.allowed_channels = self.normalized_allowed_channels(allowed_channels)
        self.busy_context = busy_context or {}
        if default_grouping_behavior not in {
            GROUPING_BEHAVIOR_CONTINUOUS,
            GROUPING_BEHAVIOR_GAP_AWARE,
        }:
            raise ValueError(f"Unsupported grouping behavior: {default_grouping_behavior}")
        self.default_grouping_behavior = default_grouping_behavior
        self.init_ui()

    def normalized_allowed_channels(self, allowed_channels: tuple[str, ...] | None) -> tuple[str, ...]:
        if allowed_channels is None:
            return tuple(CFA_CHANNELS)
        normalized = tuple(
            channel_key.strip().upper()
            for channel_key in allowed_channels
            if channel_key.strip().upper() in CFA_CHANNELS
        )
        if not normalized:
            raise ValueError("allowed_channels does not contain any known CFA channel")
        return normalized

    def init_ui(self) -> None:
        self.setWindowTitle(WINDOW_TITLE)
        self.setStyleSheet(APP_DARK_STYLESHEET)
        window_width = (
            FULL_CHANNEL_WINDOW_WIDTH
            if len(self.allowed_channels) > 2
            else WINDOW_WIDTH
        )
        self.resize(window_width, WINDOW_HEIGHT)
        self.setMinimumWidth(window_width)

        layout = QVBoxLayout()

        intro = QLabel(
            "Select before starting:\n"
            "- Original Seestar .fit folder\n"
            "- CFA photometry channels\n"
            "- Stack Groups"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if self.lightcurve_mode:
            lightcurve_note = QLabel(
                "SeePhot: Only L or G outputs are used for V-calibrated Light Curves."
            )
            lightcurve_note.setWordWrap(True)
            layout.addWidget(lightcurve_note)

        row = QHBoxLayout()
        self.source_edit = QLineEdit(self.source_dir)
        self.source_edit.setPlaceholderText("Paste or choose the folder with original Seestar FITS frames")
        self.source_edit.editingFinished.connect(self.update_source_dir_from_edit)
        self.browse_button = QPushButton("Choose Folder")
        self.browse_button.clicked.connect(self.choose_directory)
        row.addWidget(self.source_edit)
        row.addWidget(self.browse_button)
        layout.addLayout(row)

        plan_group = QGroupBox("Stack Groups")
        plan_layout = QGridLayout()
        plan_layout.setColumnStretch(3, 1)
        plan_layout.setHorizontalSpacing(12)
        plan_layout.setVerticalSpacing(8)

        self.plan_mode_combo = QComboBox()
        self.plan_mode_combo.addItem("Seconds", "time")
        self.plan_mode_combo.addItem("Frames", "frames")
        if DEFAULT_PLAN_MODE == "frames":
            self.plan_mode_combo.setCurrentIndex(1)
        self.plan_mode_combo.currentIndexChanged.connect(self.update_plan_spinboxes)

        self.grouping_behavior_combo = QComboBox()
        self.grouping_behavior_combo.addItem("Continuous", GROUPING_BEHAVIOR_CONTINUOUS)
        self.grouping_behavior_combo.addItem("Gap-aware", GROUPING_BEHAVIOR_GAP_AWARE)
        self.grouping_behavior_combo.setCurrentIndex(
            self.grouping_behavior_combo.findData(self.default_grouping_behavior)
        )
        self.grouping_behavior_combo.currentIndexChanged.connect(self.update_grouping_behavior_tooltip)

        self.plan_one_spin = QSpinBox()
        self.plan_one_spin.setRange(1, 100000)
        self.plan_two_spin = QSpinBox()
        self.plan_two_spin.setRange(1, 100000)

        self.plan_one_check = QCheckBox()
        self.plan_two_check = QCheckBox()
        self.plan_all_check = QCheckBox()
        for check in (self.plan_one_check, self.plan_two_check, self.plan_all_check):
            check.setChecked(True)
            check.setText("")

        self.plan_one_spin.setFixedWidth(120)
        self.plan_two_spin.setFixedWidth(120)

        plan_layout.addWidget(QLabel("Grouping"), 0, 0)
        plan_layout.addWidget(self.plan_mode_combo, 0, 1)
        plan_layout.addWidget(QLabel("Group 1"), 1, 0)
        plan_layout.addWidget(self.plan_one_spin, 1, 1)
        plan_layout.addWidget(self.plan_one_check, 1, 2)
        plan_layout.addWidget(QLabel("Group 2"), 2, 0)
        plan_layout.addWidget(self.plan_two_spin, 2, 1)
        plan_layout.addWidget(self.plan_two_check, 2, 2)
        all_label = QLabel(ALL_PLAN_NAME)
        all_label.setFixedWidth(120)
        plan_layout.addWidget(QLabel("Group 3"), 3, 0)
        plan_layout.addWidget(all_label, 3, 1)
        plan_layout.addWidget(self.plan_all_check, 3, 2)
        behavior_label = QLabel("Mode")
        behavior_label.setToolTip("Choose how observation pauses affect stack boundaries.")
        plan_layout.addWidget(behavior_label, 4, 0)
        plan_layout.addWidget(self.grouping_behavior_combo, 4, 1, 1, 2)
        plan_group.setLayout(plan_layout)

        cfa_group = QGroupBox("CFA Photometry Channels")
        cfa_layout = QHBoxLayout()
        cfa_layout.setSpacing(18)
        self.channel_checks: dict[str, QCheckBox] = {}
        for channel_key in self.allowed_channels:
            channel = CFA_CHANNELS[channel_key]
            check = QCheckBox(str(channel["label"]))
            check.setChecked(channel_key in DEFAULT_CFA_CHANNELS)
            self.channel_checks[channel_key] = check
            cfa_layout.addWidget(check)
        cfa_layout.addStretch(1)
        cfa_group.setLayout(cfa_layout)

        layout.addWidget(cfa_group)
        layout.addWidget(plan_group)

        button_row = QHBoxLayout()
        self.overview_button = QPushButton("Overview")
        self.overview_button.clicked.connect(self.show_overview)
        self.help_button = QPushButton("Help")
        self.help_button.clicked.connect(self.show_help)
        self.stack_button = QPushButton("Start")
        self.stack_button.clicked.connect(self.start_stack)
        button_row.addWidget(self.stack_button)
        button_row.addStretch(1)
        button_row.addWidget(self.overview_button)
        button_row.addWidget(self.help_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)

        self.setLayout(layout)
        self.update_plan_spinboxes()
        self.update_grouping_behavior_tooltip()
        self.append_log(f"SeePhot CFA version: {SCRIPT_VERSION}")

    def set_run_controls_enabled(self, enabled: bool) -> None:
        self.source_edit.setEnabled(enabled)
        self.browse_button.setEnabled(enabled)
        self.plan_mode_combo.setEnabled(enabled)
        self.grouping_behavior_combo.setEnabled(enabled)
        self.plan_one_spin.setEnabled(enabled)
        self.plan_two_spin.setEnabled(enabled)
        self.plan_one_check.setEnabled(enabled)
        self.plan_two_check.setEnabled(enabled)
        self.plan_all_check.setEnabled(enabled)
        for check in self.channel_checks.values():
            check.setEnabled(enabled)
        self.stack_button.setEnabled(enabled)
        self.close_button.setEnabled(enabled)

    def normalized_source_text(self) -> str:
        text = self.source_edit.text().strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1].strip()
        if text.startswith("file://"):
            parsed = urlparse(text)
            text = unquote(parsed.path)
        return text

    def source_path_from_edit(self) -> Path | None:
        text = self.normalized_source_text()
        if not text:
            return None
        return Path(text).expanduser()

    def sync_source_dir_from_edit(self) -> Path | None:
        path = self.source_path_from_edit()
        if path is None:
            return None
        self.source_dir = str(path)
        self.source_edit.setText(self.source_dir)
        return path

    def set_source_directory(self, source_dir: str | Path) -> bool:
        """Select an existing source folder supplied by another SeePhot step."""

        path = Path(source_dir).expanduser()
        if not path.is_dir():
            return False
        self.source_dir = str(path.resolve())
        self.source_edit.setText(self.source_dir)
        return True

    def set_grouping_behavior(self, behavior: str) -> bool:
        index = self.grouping_behavior_combo.findData(behavior)
        if index < 0:
            return False
        self.grouping_behavior_combo.setCurrentIndex(index)
        return True

    def dialog_start_directory(self) -> str:
        current_path = self.sync_source_dir_from_edit()
        if current_path is None:
            current_path = Path(self.source_dir).expanduser()
        if current_path.is_dir():
            return str(current_path)
        if current_path.parent.is_dir():
            return str(current_path.parent)
        return str(Path.home())

    def choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select Folder with Original Seestar FITS Frames",
            self.dialog_start_directory(),
        )
        if selected:
            self.source_dir = selected
            self.source_edit.setText(selected)

    def update_source_dir_from_edit(self) -> None:
        self.sync_source_dir_from_edit()

    def append_log(self, message: str) -> None:
        self.log_view.append(message)

    def current_plan_values(self) -> tuple[int, int]:
        return self.plan_one_spin.value(), self.plan_two_spin.value()

    def current_plan_mode(self) -> str:
        return str(self.plan_mode_combo.currentData())

    def current_grouping_behavior(self) -> str:
        return str(self.grouping_behavior_combo.currentData())

    def update_grouping_behavior_tooltip(self) -> None:
        if self.current_grouping_behavior() == GROUPING_BEHAVIOR_GAP_AWARE:
            text = (
                "Gap-aware: Recommended for light-curve photometry. A gap longer than 3 × median input cadence "
                "ends the current stack. No stack crosses that pause."
            )
        else:
            text = (
                "Continuous: frames continue into the next stack "
                "even when an observation pause occurs."
            )
        self.grouping_behavior_combo.setToolTip(text)

    def build_available_plans(self) -> tuple[StackPlan, StackPlan, StackPlan]:
        return build_stack_plans(self.current_plan_mode(), self.current_plan_values())

    def build_selected_plans(self) -> tuple[StackPlan, ...]:
        available = self.build_available_plans()
        selected: list[StackPlan] = []
        if self.plan_one_check.isChecked():
            selected.append(available[0])
        if self.plan_two_check.isChecked():
            selected.append(available[1])
        if self.plan_all_check.isChecked():
            selected.append(available[2])
        return tuple(selected)

    def build_selected_channels(self) -> tuple[str, ...]:
        return tuple(
            channel_key
            for channel_key in self.channel_checks
            if self.channel_checks[channel_key].isChecked()
        )

    def update_plan_spinboxes(self) -> None:
        mode = self.current_plan_mode()
        if mode == "time":
            values = DEFAULT_DURATION_PLANS
            suffix = " s"
        else:
            values = DEFAULT_FRAME_PLANS
            suffix = " Frames"

        self.plan_one_spin.blockSignals(True)
        self.plan_two_spin.blockSignals(True)
        self.plan_one_spin.setSuffix(suffix)
        self.plan_two_spin.setSuffix(suffix)
        self.plan_one_spin.setValue(values[0])
        self.plan_two_spin.setValue(values[1])
        self.plan_one_spin.blockSignals(False)
        self.plan_two_spin.blockSignals(False)

    def start_stack(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        source_path = self.sync_source_dir_from_edit()
        if source_path is None:
            QMessageBox.critical(self, "Missing Folder", "Select a source folder first.")
            return
        source_dir = str(source_path)

        selected_plans = self.build_selected_plans()
        selected_channels = self.build_selected_channels()

        try:
            if not source_path.is_dir():
                raise FileNotFoundError(f"Source folder not found: {source_path}")
            source_frames = collect_valid_fits(source_path, lambda _message: None)
            if not source_frames:
                raise FileNotFoundError(
                    f"No valid .fit/.fits files were found directly in the selected source folder: "
                    f"{source_path}\n"
                    f"Select the folder that contains the individual original Seestar FITS frames."
                )
            needs_cfa_split = frames_need_cfa_split(source_frames)
            if needs_cfa_split and not selected_channels:
                raise ValueError("Select at least one CFA output channel.")
            if self.lightcurve_mode and not needs_cfa_split:
                require_supported_lightcurve_source(source_frames, source_path)
            selected_plans = stack_plans_for_input(
                selected_plans, source_frames, needs_cfa_split,
            )
        except Exception as exc:
            self.append_log(f"Cannot start stacking: {exc}")
            QMessageBox.critical(
                self,
                "Cannot Start",
                f"Cannot start stacking.\n\n{exc}",
            )
            return

        allow_overwrite = DEFAULT_OVERWRITE_RESULTS
        existing_dirs = existing_result_dirs_for_run(
            source_path,
            selected_plans,
            selected_channels,
            needs_cfa_split,
            self.current_grouping_behavior(),
        )
        if existing_dirs:
            reply = QMessageBox.question(
                self,
                "Overwrite Result Folders?",
                "Existing result folders will be overwritten.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            allow_overwrite = True

        begin_busy_action = self.busy_context.get("begin_busy_action")
        if callable(begin_busy_action):
            if not begin_busy_action(
                "STACK_RUNNING",
                "Running CFA Channels / Stack.",
                [self.stack_button],
            ):
                return

        self.log_view.clear()
        self.append_log(f"SeePhot CFA version: {SCRIPT_VERSION}")
        self.append_log(f"[INFO] Stack mode: {self.grouping_behavior_combo.currentText()}.")
        self.set_run_controls_enabled(False)
        self.worker = StackWorker(
            source_dir,
            selected_plans,
            selected_channels,
            allow_overwrite,
            require_lightcurve_filter=self.lightcurve_mode,
            grouping_behavior=self.current_grouping_behavior(),
        )
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def build_overview_html(self) -> str:
        lightcurve_note = ""
        if self.lightcurve_mode:
            lightcurve_note = """
            <div class="note">
                This window was opened from SeePhot. Only <b>L</b> or <b>G</b>
                outputs are used for V-calibrated Light Curves.
            </div>
            """
        return f"""
        <html><head><style>
            body {{ color: #eceff4; }}
            h2, h3 {{ color: #d8dee9; }}
            h2 {{ margin-bottom: 4px; }}
            h3 {{ margin-top: 18px; margin-bottom: 4px; }}
            p {{ margin-top: 4px; margin-bottom: 7px; }}
            ul, ol {{ margin-top: 4px; margin-bottom: 8px; }}
            li {{ margin-bottom: 3px; }}
            .note {{ background-color: #111827; border-left: 3px solid #4f8cff;
                padding: 8px; margin-top: 7px; margin-bottom: 9px; }}
            code {{ color: #d8dee9; }}
        </style></head><body>
            <h2>SeePhot Stack Overview</h2>
            <p>This tool prepares and stacks original Seestar FITS frames for photometry.</p>
            {lightcurve_note}

            <h3>Workflow</h3>
            <ol>
                <li>Select the folder containing the original Seestar <code>.fit</code> frames.</li>
                <li>Select stack groups by seconds, by frame count, or <b>ALL</b>.</li>
                <li>Select CFA output channels if the input frames are raw Bayer/CFA data.</li>
                <li>Click <b>Start</b>. Results are written next to the selected source folder.</li>
            </ol>

            <h3>CFA / channel output</h3>
            <ul>
                <li>CFA/Bayer input is split into measured CFA samples without interpolation.</li>
                <li>Output channels keep the original Seestar image size for downstream compatibility.</li>
                <li>Each measured CFA value is replicated over its original 2x2 Bayer block.</li>
                <li>L is the mean of one Bayer cell: (R + G1 + G2 + B) / 4.</li>
                <li>G is the mean of the two green samples: (G1 + G2) / 2.</li>
                <li>R and B use the measured red and blue CFA samples.</li>
                <li><code>FILTER</code> and <code>CHANMODE</code> document the derived channel
                in the FITS header.</li>
                <li>Non-CFA input is stacked directly.</li>
            </ul>

            <h3>Results and temporary files</h3>
            <ul>
                <li>Original input frames are never modified.</li>
                <li>Results are written next to the source folder, e.g.
                <code>&lt;source&gt;_l</code> or <code>&lt;source&gt;_g-stack100sec</code>.</li>
                <li>Non-CFA stack results use no channel suffix, e.g.
                <code>&lt;source&gt;-stack100sec</code>.</li>
                <li>Gap-aware stack result folders end in <code>_ga</code>, e.g.
                <code>&lt;source&gt;_g-stack100sec_ga</code> or
                <code>&lt;source&gt;-stack100sec_ga</code>.</li>
                <li>Temporary folders use <code>&lt;source&gt;-tmp...</code> names and are
                removed when the run ends.</li>
            </ul>

            <div class="note"><b>Overwrite safety:</b> Existing result folders are listed
            before the run starts. They are cleared only after explicit confirmation.</div>

            <h3>Stack acceptance</h3>
            <ul>
                <li>A stack block must have at least {MIN_FRAMES_PER_STACK} registered frames.</li>
                <li>With one CFA frame, selected channels are exported without stacking,
                even when ALL is selected.</li>
                <li>Time-based blocks also need at least
                {MIN_STACK_COMPLETION_FRACTION * 100:g}% of the requested duration.</li>
                <li>End-of-sequence blocks are accepted when these criteria are met.</li>
            </ul>

            <h3>Stack mode</h3>
            <ul>
                <li><b>Continuous</b> keeps grouping across observation pauses.</li>
                <li><b>Gap-aware</b> ends a stack at a pause longer than
                3 × median input cadence.</li>
            </ul>

            <h3>FITS headers</h3>
            <ul>
                <li><code>DATE-OBS</code> is required; files without it are skipped.</li>
                <li>With <code>DATE-EXP</code>, <code>DATE-OBS</code> is the exposure start
                and <code>DATE-EXP</code> the end.</li>
                <li>Without <code>DATE-EXP</code>, <code>DATE-OBS</code> is treated as the
                exposure end.</li>
                <li>This distinction reflects a change by ZWO in the meaning of
                <code>DATE-OBS</code> in Seestar firmware.</li>
                <li>Generated files use <code>DATE-OBS</code> = UTC exposure start and
                <code>DATE-END</code> = UTC exposure end.</li>
                <li><code>DATE-AVG</code> and <code>MJD-AVG</code> store the exposure-weighted
                midpoint for photometry.</li>
                <li>Stack <code>EXPTIME</code> is the summed exposure time of the used frames,
                excluding gaps.</li>
                <li><code>NCOMBINE</code> is the number of frames actually included in the stack.</li>
                <li>Missing <code>EXPTIME</code> falls back to
                {DEFAULT_SUBFRAME_EXPOSURE:g}s.</li>
            </ul>

            <div class="note">The log ends with a <b>[SUMMARY]</b> section listing input
            frames, skipped files, channel frames, stack results, skipped blocks and warnings.</div>
        </body></html>
        """

    def build_help_html(self) -> str:
        return """
        <html><head><style>
            body { color: #eceff4; }
            h2 { color: #d8dee9; margin-bottom: 8px; }
            p { margin-top: 4px; margin-bottom: 8px; }
            .note { background-color: #111827; border-left: 3px solid #4f8cff;
                padding: 9px; }
        </style></head><body>
            <h2>SeePhot Stack Help</h2>
            <div class="note">
                <p>Select the folder containing the original Seestar FITS frames. Choose
                grouping by seconds or frames, enable the required groups and CFA channels,
                then click <b>Start</b>.</p>
                <p><b>Continuous</b> grouping spans observation pauses; <b>Gap-aware</b>
                grouping starts a new block after a long pause. Existing result folders
                are changed only after you confirm the overwrite prompt.</p>
            </div>
            <p>Progress, skipped inputs, warnings, and the final summary appear in the log.
            For details about outputs, timing, and CFA processing, click <b>Overview</b>.</p>
        </body></html>
        """

    def show_overview(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("SeePhot Stack Overview")
        dialog.resize(760, 520)

        layout = QVBoxLayout(dialog)
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setHtml(self.build_overview_html())
        layout.addWidget(text_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("SeePhot Stack Help")
        dialog.resize(620, 320)

        layout = QVBoxLayout(dialog)
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setHtml(self.build_help_html())
        layout.addWidget(text_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def on_finished(self, success: bool, message: str, result_dir: str = "") -> None:
        self.set_run_controls_enabled(True)
        finish_busy_action = self.busy_context.get("finish_busy_action")
        if callable(finish_busy_action):
            finish_busy_action("STACK_RUNNING")
        self.append_log(message)
        if success:
            if result_dir:
                self.stack_result_ready.emit(result_dir)
            QMessageBox.information(self, "Stack", "Stack complete.")
        else:
            QMessageBox.critical(self, "Stack", "Stack failed.\n\nSee the log for details.")
        self.worker = None

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(
                self,
                "Close",
                "Stacking is still running.",
            )
            event.ignore()
            return
        super().closeEvent(event)


def activate_existing_main_window(app: QApplication) -> bool:
    """Bring an already open SeePhot Stack window to front if one exists."""

    for widget in app.topLevelWidgets():
        if widget.objectName() != MAIN_WINDOW_OBJECT_NAME:
            continue
        if not widget.isVisible():
            continue
        if widget.isMinimized():
            widget.showNormal()
        else:
            widget.show()
        widget.raise_()
        widget.activateWindow()
        return True
    return False


class SingleInstanceLock:
    """Small cross-platform lock based on exclusive lock-file creation."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd = fd
        self.locked = True

    def unlock(self, *_args: object) -> None:
        if not self.locked:
            return
        self.locked = False
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def process_is_running(pid: int) -> bool:
    """Return whether a PID exists without signalling it on Windows."""

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            # Access denied still proves that a process owns the PID.
            return ctypes.get_last_error() == 5
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_single_instance_lock() -> SingleInstanceLock | None:
    """Acquire the per-user SeePhot Stack instance lock, if available."""

    def lock_is_stale() -> bool:
        try:
            pid_text = SINGLE_INSTANCE_LOCK_PATH.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return False
        except OSError:
            return True
        try:
            pid = int(pid_text.splitlines()[0])
        except (ValueError, IndexError):
            return True
        if pid <= 0:
            return True
        return not process_is_running(pid)

    for _attempt in range(2):
        try:
            fd = os.open(SINGLE_INSTANCE_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not lock_is_stale():
                return None
            try:
                SINGLE_INSTANCE_LOCK_PATH.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return None
            continue
        os.write(fd, f"{os.getpid()}\n{time.time():.3f}\n".encode("ascii"))
        return SingleInstanceLock(SINGLE_INSTANCE_LOCK_PATH, fd)
    return None


def run_app() -> None:
    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(sys.argv)

    configure_app_theme(app)
    if activate_existing_main_window(app):
        return
    single_instance_lock = acquire_single_instance_lock()
    if single_instance_lock is None:
        activate_existing_main_window(app)
        QMessageBox.information(
            None,
            "SeePhot Stack",
            "SeePhot Stack is already running.",
        )
        return

    window = StackWindow()
    window._single_instance_lock = single_instance_lock
    window.destroyed.connect(single_instance_lock.unlock)
    window.show()

    if owns_app:
        app.exec()


def main() -> None:
    run_app()


if __name__ == "__main__":
    main()
