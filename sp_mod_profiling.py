"""Read-only temporal stack-grouping assessment for SeePhot_Main.py."""
from __future__ import annotations

import statistics
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from astropy.io import fits
from astropy.io.fits.verify import VerifyWarning
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

PROFILING_PLUGIN_VERSION = "0.4"
DEFAULT_EXPOSURE_SECONDS = 10.0
TIMING_INTERVAL_TOLERANCE_SECONDS = 1.0
BAYER_HEADER_KEYS = ("BAYERPAT", "XBAYROFF", "YBAYROFF", "ROWORDER")
FITS_SUFFIXES = {".fit", ".fits"}
SOURCE_SCAN_ATTEMPTS = 3
SOURCE_SCAN_RETRY_DELAY_SECONDS = 0.15
FIXED_IMAGE_COUNTS = (5, 10, 15, 20)
FIXED_MAX_DURATIONS_SECONDS = (50, 100, 150, 200)
MIN_CANDIDATE_GROUPS = 2
MIN_FRAMES_PER_STACK = 2
MIN_STACK_COMPLETION_FRACTION = 0.30
GAP_BREAK_CADENCE_FACTOR = 3.0
GROUPING_BEHAVIOR_CONTINUOUS = "continuous"
GROUPING_BEHAVIOR_GAP_AWARE = "gap_aware"

@dataclass(frozen=True)
class FrameTiming:
    path: Path
    start_time: datetime
    end_time: datetime
    exptime: float
    exptime_estimated: bool
    has_bayer_header: bool
    timing_source: str = "legacy_date_obs_end"
    @property
    def midpoint(self) -> datetime:
        return self.start_time + timedelta(seconds=self.exptime / 2)

@dataclass(frozen=True)
class TemporalStackGroup:
    frames: tuple[FrameTiming, ...]
    midpoint: datetime
    span_seconds: float

@dataclass(frozen=True)
class TemporalGroupingCandidate:
    mode: str
    value: int
    label: str
    group_count: int
    gap_crossing_stack_count: int
    input_cadence_median: float
    frames_min: int
    frames_median: float
    frames_max: int
    span_median: float
    span_p90: float
    span_max: float
    cadence_median: float | None
    cadence_p90: float | None
    cadence_max: float | None
    cadence_cv: float | None
    smearing_median: float
    smearing_p90: float
    smearing_max: float
    @property
    def temporal_score(self) -> float:
        """Temporal-sampling loss: cadence irregularity, smearing, and gap bridges."""
        if self.cadence_cv is None or self.input_cadence_median <= 0:
            return float("inf")
        bridge_penalty = 100.0 * self.gap_crossing_stack_count
        resolution_loss = self.span_p90 / self.input_cadence_median
        return bridge_penalty + resolution_loss + self.cadence_cv

@dataclass(frozen=True)
class SeriesStatistics:
    source_dir: Path
    grouping_behavior: str
    source_scan_attempts: int
    fits_files_found: int
    frames: tuple[FrameTiming, ...]
    skipped_files: tuple[str, ...]
    estimated_exptime_count: int
    bayer_frame_count: int
    timestamp_order_issues: int
    duplicate_end_times: int
    series_start: datetime
    series_end: datetime
    series_seconds: float
    summed_exposure_seconds: float
    occupied_exposure_seconds: float
    exposure_efficiency_percent: float
    exposure_median: float
    cadence_median: float
    cadence_p90: float
    gap_break_seconds: float
    segment_count: int
    large_gap_count: int
    candidates: tuple[TemporalGroupingCandidate, ...]
    recommendation: TemporalGroupingCandidate | None
    recommendation_reason: str
    @property
    def cfa_compatible(self) -> bool:
        return bool(self.frames) and self.bayer_frame_count == len(self.frames)

def is_fits_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in FITS_SUFFIXES and not path.name.startswith((".", "._"))

def direct_fits_paths_with_retry(source_dir: Path) -> tuple[list[Path], int]:
    last_error: OSError | None = None
    for attempt in range(1, SOURCE_SCAN_ATTEMPTS + 1):
        try:
            paths = sorted(path for path in source_dir.iterdir() if is_fits_file(path))
        except OSError as exc:
            last_error, paths = exc, []
        if paths:
            return paths, attempt
        if attempt < SOURCE_SCAN_ATTEMPTS:
            time.sleep(SOURCE_SCAN_RETRY_DELAY_SECONDS)
    if last_error:
        raise OSError(f"Could not scan source folder after {SOURCE_SCAN_ATTEMPTS} attempts: {source_dir}: {last_error}") from last_error
    return [], SOURCE_SCAN_ATTEMPTS

def parse_fits_datetime(value: object, path: Path, key: str = "DATE-OBS") -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{key} is missing")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid {key}: {value!r}") from exc
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

def read_exposure_seconds(header: fits.Header) -> tuple[float, bool]:
    try:
        exposure = float(header.get("EXPTIME", header.get("EXPOSURE")))
    except (TypeError, ValueError):
        return DEFAULT_EXPOSURE_SECONDS, True
    return (exposure, False) if exposure > 0 else (DEFAULT_EXPOSURE_SECONDS, True)

def read_frame_timing(path: Path) -> FrameTiming:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VerifyWarning)
        header = fits.getheader(path)
    exposure, estimated = read_exposure_seconds(header)
    if "DATE-EXP" in header:
        start_time = parse_fits_datetime(header.get("DATE-OBS"), path, "DATE-OBS")
        end_time = parse_fits_datetime(header.get("DATE-EXP"), path, "DATE-EXP")
        header_exposure = (end_time - start_time).total_seconds()
        tolerance = max(TIMING_INTERVAL_TOLERANCE_SECONDS, exposure * 0.01)
        if header_exposure <= 0 or abs(header_exposure - exposure) > tolerance:
            raise ValueError(
                f"inconsistent DATE-OBS/DATE-EXP interval "
                f"({header_exposure:g}s; EXPTIME={exposure:g}s)"
            )
        timing_source = "date_obs_date_exp"
    else:
        end_time = parse_fits_datetime(header.get("DATE-OBS"), path)
        start_time = end_time - timedelta(seconds=exposure)
        timing_source = "legacy_date_obs_end"
    return FrameTiming(
        path,
        start_time,
        end_time,
        exposure,
        estimated,
        any(key in header for key in BAYER_HEADER_KEYS),
        timing_source,
    )


def timing_summary(frames: tuple[FrameTiming, ...]) -> str:
    modern = sum(frame.timing_source == "date_obs_date_exp" for frame in frames)
    legacy = sum(frame.timing_source == "legacy_date_obs_end" for frame in frames)
    parts: list[str] = []
    if modern:
        parts.append(f"{modern} modern DATE-OBS start + DATE-EXP end")
    if legacy:
        parts.append(f"{legacy} legacy DATE-OBS end (start derived from EXPTIME)")
    return "FITS timing: " + "; ".join(parts) + "."

def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)

def coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    return statistics.pstdev(values) / mean if mean > 0 else 0.0

def occupied_exposure_seconds(frames: list[FrameTiming]) -> float:
    if not frames:
        return 0.0
    intervals = sorted((frame.start_time, frame.end_time) for frame in frames)
    start, end = intervals[0]
    occupied = 0.0
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
            continue
        occupied += (end - start).total_seconds()
        start, end = next_start, next_end
    return occupied + (end - start).total_seconds()

def split_at_large_gaps(frames: list[FrameTiming], gap_break_seconds: float) -> list[list[FrameTiming]]:
    if not frames:
        return []
    segments = [[frames[0]]]
    for previous, current in zip(frames, frames[1:]):
        if (current.midpoint - previous.midpoint).total_seconds() > gap_break_seconds:
            segments.append([])
        segments[-1].append(current)
    return segments

def observation_segments(frames: list[FrameTiming]) -> list[list[FrameTiming]]:
    cadences = [(current.midpoint - previous.midpoint).total_seconds() for previous, current in zip(frames, frames[1:])]
    gap_break = GAP_BREAK_CADENCE_FACTOR * (statistics.median(cadences) if cadences else 0.0)
    return split_at_large_gaps(frames, gap_break)

def split_frames_by_count(frames: list[FrameTiming], frame_count: int) -> list[list[FrameTiming]]:
    """Mirror current fixed-frame grouping in SeePhot_CFA.py."""
    return [frames[index:index + frame_count] for index in range(0, len(frames), frame_count)]

def split_frames_by_seconds(frames: list[FrameTiming], duration_seconds: int) -> list[list[FrameTiming]]:
    """Mirror current fixed-duration grouping in SeePhot_CFA.py."""
    blocks, start_index = [], 0
    while start_index < len(frames):
        block_start, end_index = frames[start_index].start_time, start_index + 1
        while end_index < len(frames) and (frames[end_index].end_time - block_start).total_seconds() <= duration_seconds:
            end_index += 1
        blocks.append(frames[start_index:end_index])
        start_index = end_index
    return blocks

def make_temporal_group(frames: list[FrameTiming]) -> TemporalStackGroup:
    midpoint = datetime.fromtimestamp(statistics.fmean(frame.midpoint.timestamp() for frame in frames), tz=timezone.utc)
    return TemporalStackGroup(tuple(frames), midpoint, (frames[-1].end_time - frames[0].start_time).total_seconds())

def stack_would_be_written(frames: list[FrameTiming], *, mode: str, value: int) -> bool:
    """Apply the CFA stacker's pre-registration minimum-block rules."""
    if len(frames) < MIN_FRAMES_PER_STACK:
        return False
    return mode == "images" or sum(frame.exptime for frame in frames) >= value * MIN_STACK_COMPLETION_FRACTION

def split_frames_for_behavior(
    frames: list[FrameTiming], *, mode: str, value: int, grouping_behavior: str
) -> list[list[FrameTiming]]:
    if grouping_behavior == GROUPING_BEHAVIOR_CONTINUOUS:
        segments = [frames]
    elif grouping_behavior == GROUPING_BEHAVIOR_GAP_AWARE:
        segments = observation_segments(frames)
    else:
        raise ValueError(f"Unsupported grouping behavior: {grouping_behavior}")
    if mode == "all":
        return [segment for segment in segments if segment]
    splitter = split_frames_by_count if mode == "images" else split_frames_by_seconds
    return [block for segment in segments for block in splitter(segment, value)]

def build_temporal_candidate(frames: list[FrameTiming], *, mode: str, value: int, grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS) -> TemporalGroupingCandidate | None:
    """Preview exactly the blocks that the CFA stacker plans to write."""
    input_segments = observation_segments(frames)
    segment_by_frame = {frame: index for index, segment in enumerate(input_segments) for frame in segment}
    blocks = [
        block for block in split_frames_for_behavior(frames, mode=mode, value=value, grouping_behavior=grouping_behavior)
        if stack_would_be_written(block, mode=mode, value=value)
    ]
    groups = [make_temporal_group(block) for block in blocks]
    if len(groups) < MIN_CANDIDATE_GROUPS:
        return None
    counts, spans = [len(group.frames) for group in groups], [group.span_seconds for group in groups]
    group_segments = [{segment_by_frame[frame] for frame in block} for block in blocks]
    cadences = [
        (current.midpoint - previous.midpoint).total_seconds()
        for previous, current, previous_segments, current_segments in zip(groups, groups[1:], group_segments, group_segments[1:])
        if len(previous_segments) == len(current_segments) == 1 and previous_segments == current_segments
    ]
    smearings = [span / (12 ** .5) for span in spans]
    gap_crossing_stack_count = sum(len(segments) > 1 for segments in group_segments)
    cadence_median = statistics.median(cadences) if cadences else None
    cadence_p90 = percentile(cadences, .90) if cadences else None
    cadence_max = max(cadences) if cadences else None
    cadence_cv = coefficient_of_variation(cadences) if cadences else None
    input_cadences = [(current.midpoint - previous.midpoint).total_seconds() for previous, current in zip(frames, frames[1:])]
    input_cadence_median = statistics.median(input_cadences) if input_cadences else 0.0
    return TemporalGroupingCandidate(mode, value, f"{value} images" if mode == "images" else f"{value} seconds", len(groups), gap_crossing_stack_count, input_cadence_median, min(counts), statistics.median(counts), max(counts), statistics.median(spans), percentile(spans, .90), max(spans), cadence_median, cadence_p90, cadence_max, cadence_cv, statistics.median(smearings), percentile(smearings, .90), max(smearings))

def select_temporal_recommendation(candidates: tuple[TemporalGroupingCandidate, ...]) -> tuple[TemporalGroupingCandidate | None, str]:
    if not candidates:
        return None, "No tested grouping yields at least two stack points."
    scored = tuple(candidate for candidate in candidates if candidate.temporal_score != float("inf"))
    if not scored:
        return None, "No tested grouping has enough in-segment cadence points for a temporal-sampling score."
    selected = min(scored, key=lambda candidate: (candidate.temporal_score, candidate.cadence_max, candidate.span_p90, candidate.cadence_median, candidate.mode != "images", candidate.value))
    return selected, "Temporal score = in-segment output-cadence CV + P90 stack span / median input cadence + 100 per stack crossing a large observation gap. Lower is better. External gaps do not enter the cadence term; a crossing stack receives the dominant integrity penalty."

def analyze_series_directory(source_dir: Path, progress_callback=None, grouping_behavior: str = GROUPING_BEHAVIOR_CONTINUOUS) -> SeriesStatistics:
    if grouping_behavior not in {GROUPING_BEHAVIOR_CONTINUOUS, GROUPING_BEHAVIOR_GAP_AWARE}:
        raise ValueError(f"Unsupported grouping behavior: {grouping_behavior}")
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Folder not found: {source_dir}")
    paths, attempts = direct_fits_paths_with_retry(source_dir)
    if not paths:
        raise FileNotFoundError(f"No .fit/.fits files were found directly in the selected folder after {attempts} scan attempts: {source_dir}")
    file_order, skipped = [], []
    for index, path in enumerate(paths, 1):
        try:
            file_order.append(read_frame_timing(path))
        except Exception as exc:
            skipped.append(f"{path.name}: {exc}")
        if callable(progress_callback):
            progress_callback(index, len(paths))
    if not file_order:
        raise ValueError("No FITS file has a usable DATE-OBS value.")
    order_issues = sum(current.end_time < previous.end_time for previous, current in zip(file_order, file_order[1:]))
    frames = sorted(file_order, key=lambda frame: (frame.end_time, frame.path.name))
    duplicates = sum(current.end_time == previous.end_time for previous, current in zip(frames, frames[1:]))
    cadences = [(current.midpoint - previous.midpoint).total_seconds() for previous, current in zip(frames, frames[1:])]
    cadence_median = statistics.median(cadences) if cadences else 0.0
    gap_break = GAP_BREAK_CADENCE_FACTOR * cadence_median
    segments = split_at_large_gaps(frames, gap_break)
    candidates = tuple(sorted(
        (candidate for mode, values in (("images", FIXED_IMAGE_COUNTS), ("seconds", FIXED_MAX_DURATIONS_SECONDS)) for value in values if (candidate := build_temporal_candidate(frames, mode=mode, value=value, grouping_behavior=grouping_behavior)) is not None),
        key=lambda candidate: (candidate.temporal_score, candidate.cadence_max if candidate.cadence_max is not None else float("inf"), candidate.span_p90, candidate.cadence_median if candidate.cadence_median is not None else float("inf"), candidate.mode != "images", candidate.value),
    ))
    recommendation, reason = select_temporal_recommendation(candidates)
    series_seconds = (frames[-1].end_time - frames[0].start_time).total_seconds()
    occupied_seconds = occupied_exposure_seconds(frames)
    return SeriesStatistics(source_dir, grouping_behavior, attempts, len(paths), tuple(frames), tuple(skipped), sum(frame.exptime_estimated for frame in frames), sum(frame.has_bayer_header for frame in frames), order_issues, duplicates, frames[0].start_time, frames[-1].end_time, series_seconds, sum(frame.exptime for frame in frames), occupied_seconds, 100 * occupied_seconds / series_seconds if series_seconds else 0.0, statistics.median([frame.exptime for frame in frames]), cadence_median, percentile(cadences, .90), gap_break, len(segments), len(segments) - 1, candidates, recommendation, reason)

def format_duration(seconds: float) -> str:
    return f"{seconds / 60:.2f} min ({seconds:.1f} s)" if seconds >= 60 else f"{seconds:.1f} s"

def grouping_behavior_label(behavior: str) -> str:
    if behavior == GROUPING_BEHAVIOR_GAP_AWARE:
        return "Gap-aware"
    return "Continuous"

def format_score(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"

def format_cadence(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "n/a"

def continuous_conclusion_lines(gap_crossing_counts: tuple[int, ...]) -> tuple[str, ...]:
    """Return the user-facing conclusion for continuous grouping options."""
    if not gap_crossing_counts:
        return ()
    no_gap_count = sum(count == 0 for count in gap_crossing_counts)
    total_count = len(gap_crossing_counts)
    if no_gap_count == 0:
        return (
            "Conclusion:",
            "All continuous grouping options cross observation gaps.",
            "For time-based photometry, prefer Gap-aware grouping.",
        )
    if no_gap_count == total_count:
        return (
            "Conclusion:",
            "No continuous grouping option crosses an observation gap.",
            "Lower scores provide finer and more regular temporal sampling.",
            "Choose the smallest grouping that still gives sufficient SNR for reliable photometry.",
        )
    return (
        "Conclusion:",
        f"{no_gap_count} of {total_count} continuous grouping options do not cross observation gaps.",
        "Among these options, lower scores provide finer and more regular temporal sampling.",
        "Choose the smallest grouping that still gives sufficient SNR for reliable photometry.",
    )

def gap_aware_conclusion_lines(large_gap_count: int, candidate_count: int) -> tuple[str, ...]:
    """Return the user-facing conclusion for gap-aware grouping options."""
    if candidate_count == 0:
        return ()
    if large_gap_count == 0:
        return (
            "Conclusion:",
            "No observation gaps detected.",
            "Gap-aware and Continuous grouping produce identical stack boundaries.",
            "Lower scores provide finer and more regular temporal sampling.",
            "Choose the smallest grouping that still gives sufficient SNR for reliable photometry.",
        )
    gap_word = "gap" if large_gap_count == 1 else "gaps"
    return (
        "Conclusion:",
        f"All listed grouping options avoid crossing the {large_gap_count} detected observation {gap_word}.",
        "Lower scores provide finer and more regular temporal sampling.",
        "Choose the smallest grouping that still gives sufficient SNR for reliable photometry.",
    )

def format_grouping_table(candidates: tuple[TemporalGroupingCandidate, ...]) -> tuple[str, ...]:
    """Format tested grouping candidates as an aligned fixed-width table."""
    headers = (
        "Rank",
        "Group",
        "Score",
        "Stacks",
        "Bridges",
        "Images med [min-max]",
        "Span med/P90/max (s)",
        "Cadence med/max (s)",
    )
    rows = [
        (
            str(rank),
            candidate.label,
            format_score(candidate.temporal_score),
            str(candidate.group_count),
            str(candidate.gap_crossing_stack_count),
            f"{candidate.frames_median:.1f} [{candidate.frames_min}-{candidate.frames_max}]",
            f"{candidate.span_median:.1f} / {candidate.span_p90:.1f} / {candidate.span_max:.1f}",
            f"{format_cadence(candidate.cadence_median)} / {format_cadence(candidate.cadence_max)}",
        )
        for rank, candidate in enumerate(candidates, 1)
    ]
    widths = tuple(
        max((len(header), *(len(row[index]) for row in rows)))
        for index, header in enumerate(headers)
    )
    right_aligned = {0, 2, 3, 4, 5, 6, 7}

    def render(row: tuple[str, ...]) -> str:
        return "  ".join(
            value.rjust(widths[index]) if index in right_aligned else value.ljust(widths[index])
            for index, value in enumerate(row)
        )

    return (
        render(headers),
        "  ".join("-" * width for width in widths),
        *(render(row) for row in rows),
    )

def format_statistics(result: SeriesStatistics) -> str:
    source_type = "Original CFA/Bayer series" if result.cfa_compatible else f"Not uniformly CFA/Bayer ({result.bayer_frame_count}/{len(result.frames)} frames detected)"
    lines = ["TEMPORAL STACK ASSESSMENT", "Timing only: no S/N, photometry, or variable-star model is evaluated.", "", f"Folder:                 {result.source_dir}", f"Mode:                   {grouping_behavior_label(result.grouping_behavior)}", f"Source type:            {source_type}", f"Images:                 {len(result.frames)} usable / {result.fits_files_found} FITS", f"Observation span:       {format_duration(result.series_seconds)}", f"Exposure (median):      {format_duration(result.exposure_median)}", f"Exposure efficiency:    {result.exposure_efficiency_percent:.1f} % ({format_duration(result.occupied_exposure_seconds)} occupied)", f"Input cadence:          {format_duration(result.cadence_median)} median; {format_duration(result.cadence_p90)} P90", f"Segments:               {result.segment_count} ({result.large_gap_count} gaps)", f"Gap threshold:          > 3 x cadence = {format_duration(result.gap_break_seconds)}", "Bridges:                stacks crossing a gap above this threshold", "P90:                    90% of values are at or below this value", "", "TESTED CFA GROUPINGS (ranked; lower temporal score is better)", *format_grouping_table(result.candidates)]
    lines.extend(["", f"Criterion: {result.recommendation_reason}"])
    if result.estimated_exptime_count:
        lines.append(f"WARNING: {result.estimated_exptime_count} exposure value(s) use the {DEFAULT_EXPOSURE_SECONDS:g} s fallback.")
    if result.skipped_files:
        lines.append(f"Skipped FITS files: {len(result.skipped_files)}")
    if result.grouping_behavior == GROUPING_BEHAVIOR_CONTINUOUS:
        conclusion = continuous_conclusion_lines(
            tuple(candidate.gap_crossing_stack_count for candidate in result.candidates)
        )
        if conclusion:
            lines.extend(["", *conclusion])
    elif result.grouping_behavior == GROUPING_BEHAVIOR_GAP_AWARE:
        conclusion = gap_aware_conclusion_lines(
            result.large_gap_count,
            len(result.candidates),
        )
        if conclusion:
            lines.extend(["", *conclusion])
    return chr(10).join(lines)

class ProfilingWorker(QThread):
    progress = pyqtSignal(int, int)
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    def __init__(self, source_dir: Path, grouping_behavior: str, parent=None) -> None:
        super().__init__(parent)
        self.source_dir, self.grouping_behavior = source_dir, grouping_behavior
    def run(self) -> None:
        try:
            self.result_ready.emit(analyze_series_directory(self.source_dir, self.progress.emit, self.grouping_behavior))
        except Exception as exc:
            self.failed.emit(str(exc))

class ProfilingDialog(QDialog):
    def __init__(self, context: dict[str, object], parent) -> None:
        super().__init__(parent)
        self.context, self.worker, self.result = context, None, None
        self.setWindowTitle("Stack Profiling"); self.resize(860, 680)
        layout = QVBoxLayout(self)
        note = QLabel("Read-only temporal grouping assessment for original Seestar FITS frames. It does not start a stack."); note.setWordWrap(True); layout.addWidget(note)
        source_row = QHBoxLayout(); self.source_edit = QLineEdit(); self.source_edit.setPlaceholderText("Choose the folder with original Seestar FITS frames"); self.browse_button = QPushButton("Browse..."); self.browse_button.clicked.connect(self.choose_directory); source_row.addWidget(self.source_edit, stretch=1); source_row.addWidget(self.browse_button); layout.addLayout(source_row)
        behavior_row = QHBoxLayout(); self.behavior_label = QLabel("Mode"); self.behavior_label.setToolTip("Choose how observation gaps affect the preview."); self.grouping_behavior_combo = QComboBox(); self.grouping_behavior_combo.addItem("Continuous", GROUPING_BEHAVIOR_CONTINUOUS); self.grouping_behavior_combo.addItem("Gap-aware", GROUPING_BEHAVIOR_GAP_AWARE); self.grouping_behavior_combo.setCurrentIndex(self.grouping_behavior_combo.findData(GROUPING_BEHAVIOR_GAP_AWARE)); self.grouping_behavior_combo.currentIndexChanged.connect(self.update_grouping_behavior_tooltip); behavior_row.addWidget(self.behavior_label); behavior_row.addWidget(self.grouping_behavior_combo, stretch=1); layout.addLayout(behavior_row); self.update_grouping_behavior_tooltip()
        action_row = QHBoxLayout(); self.run_button = QPushButton("Analyze Timing"); self.run_button.clicked.connect(self.run_statistics); action_row.addWidget(self.run_button); action_row.addStretch(1); layout.addLayout(action_row)
        self.progress_bar = QProgressBar(); self.progress_bar.setVisible(False); layout.addWidget(self.progress_bar)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True)
        output_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        output_font.setPointSize(max(output_font.pointSize() + 2, 11))
        self.output.setFont(output_font); self.output.setPlainText("Select an original FITS folder, then analyze its timing."); layout.addWidget(self.output, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def normalized_source_path(self) -> Path | None:
        text = self.source_edit.text().strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            text = text[1:-1].strip()
        if text.startswith("file://"): text = unquote(urlparse(text).path)
        return Path(text).expanduser() if text else None
    def dialog_start_directory(self) -> str:
        path = self.normalized_source_path()
        if path is not None and path.is_dir():
            return str(path)
        if path is not None and path.parent.is_dir():
            return str(path.parent)
        return str(Path.home())
    def choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select Original Seestar FITS Folder", self.dialog_start_directory())
        if selected: self.source_edit.setText(selected); self.result = None
    def set_running(self, running: bool) -> None:
        for widget in (self.source_edit, self.browse_button, self.grouping_behavior_combo, self.run_button, self.close_button): widget.setEnabled(not running)
        self.progress_bar.setVisible(running)
        if running: self.progress_bar.setRange(0, 0)
    def run_statistics(self) -> None:
        source_dir = self.normalized_source_path()
        if source_dir is None or not source_dir.is_dir(): QMessageBox.warning(self, "Stack Profiling", "Select an existing source folder first."); return
        begin = self.context.get("begin_busy_action")
        if callable(begin) and not begin("PROFILING_RUNNING", "Reading Stack Profiling timing.", [self.run_button]): return
        self.result = None; self.output.setPlainText("Reading FITS headers..."); self.set_running(True)
        self.worker = ProfilingWorker(source_dir, self.current_grouping_behavior(), self); self.worker.progress.connect(self.update_progress); self.worker.result_ready.connect(self.on_result_ready); self.worker.failed.connect(self.on_failed); self.worker.finished.connect(self.on_worker_finished); self.worker.start()
    def current_grouping_behavior(self) -> str:
        return str(self.grouping_behavior_combo.currentData())
    def update_grouping_behavior_tooltip(self) -> None:
        text = "Gap-aware: Recommended for light-curve photometry. A large observation gap ends the current stack." if self.current_grouping_behavior() == GROUPING_BEHAVIOR_GAP_AWARE else "Continuous: stack groups may continue across an observation gap."
        self.grouping_behavior_combo.setToolTip(text)
    def update_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1)); self.progress_bar.setValue(current)
    def on_result_ready(self, result: object) -> None:
        if not isinstance(result, SeriesStatistics): self.on_failed("Profiling returned an unexpected result type."); return
        self.result = result; self.output.setPlainText(format_statistics(result))
        append_log = self.context.get("append_log")
        if callable(append_log):
            recommendation = result.recommendation.label if result.recommendation else "none"
            append_log(f"{timing_summary(result.frames)}")
            append_log(f"Stack Profiling complete: {len(result.frames)}/{result.fits_files_found} frames, segments={result.segment_count}, recommendation={recommendation}.")
    def on_failed(self, message: str) -> None:
        self.result = None; self.output.setPlainText("Analysis failed." + chr(10) + chr(10) + message)
        append_log = self.context.get("append_log")
        if callable(append_log): append_log(f"WARNING: Stack Profiling failed: {message.replace(chr(10), ' ')}")
        QMessageBox.warning(self, "Stack Profiling", "Analysis failed." + chr(10) + chr(10) + message)
    def on_worker_finished(self) -> None:
        self.set_running(False); finish = self.context.get("finish_busy_action")
        if callable(finish): finish("PROFILING_RUNNING")
        self.worker = None
    def reject(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        super().reject()

def open_profiling_dialog(context: dict[str, object], parent) -> None:
    append_log = context.get("append_log")
    if callable(append_log): append_log("Stack Profiling requested.")
    ProfilingDialog(context, parent).exec()

def open_profiling_result_dialog(context: dict[str, object], parent, source_dir: str, grouping_behavior: str) -> None:
    """Open only the result view for settings already chosen in Analyze."""
    dialog = ProfilingDialog(context, parent)
    dialog.setWindowTitle("Stack Profiling Results")
    dialog.source_edit.setText(source_dir)
    index = dialog.grouping_behavior_combo.findData(grouping_behavior)
    if index >= 0: dialog.grouping_behavior_combo.setCurrentIndex(index)
    for widget in (dialog.source_edit, dialog.browse_button, dialog.behavior_label, dialog.grouping_behavior_combo, dialog.run_button):
        widget.hide()
    dialog.run_statistics()
    dialog.exec()
