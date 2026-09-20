"""Generic standalone analysis tab for SeePhot_Main.py.

The module deliberately has no dependency on an active photometry run or the
QC module. It gathers independent image and data analysis tools.
"""

from __future__ import annotations

import math
from pathlib import Path


ANALYZE_VERSION = "0.1-dev"
PLUGIN_TAB_LABEL = "Tools"

GAIA_V_COLOR_MIN = -0.5
GAIA_V_COLOR_MAX = 2.75
LINEARITY_DISPLAY_MIN_EXPECTED_SNR = 10.0
LINEARITY_LIMIT_MIN_REFERENCE_COUNT = 8


def gaia_v_from_g_bp_rp(gaia_g: float, bp_rp: float) -> float | None:
    """Estimate Johnson V from Gaia G and BP-RP.

    The Gaia DR2 published V-G polynomial is valid for -0.5 <= BP-RP <= 2.75.
    Its result is a diagnostic reference magnitude, not a filter-specific
    standard-photometry transformation.
    """

    if not (math.isfinite(gaia_g) and math.isfinite(bp_rp)):
        return None
    if not GAIA_V_COLOR_MIN <= bp_rp <= GAIA_V_COLOR_MAX:
        return None
    return gaia_g + 0.01760 + 0.006860 * bp_rp + 0.1732 * bp_rp**2 - 0.02050 * bp_rp**3


def _call(context: dict[str, object], name: str):
    value = context.get(name)
    if not callable(value):
        raise RuntimeError(f"Analyze needs context function: {name}")
    return value


def _linearity_specs(gaia_objects: list[object]) -> list[dict[str, object]]:
    """Convert Gaia sources into aperture-measurement specifications."""

    specs: list[dict[str, object]] = []
    for obj in gaia_objects:
        magnitude_for = getattr(obj, "gaia_magnitude_float", None)
        color_for = getattr(obj, "gaia_bp_rp_float", None)
        if not callable(magnitude_for) or not callable(color_for):
            continue
        gaia_g = magnitude_for("g")
        bp_rp = color_for()
        if gaia_g is None or bp_rp is None:
            continue
        v_mag = gaia_v_from_g_bp_rp(float(gaia_g), float(bp_rp))
        if v_mag is None:
            continue
        try:
            ra_deg = float(getattr(obj, "ra"))
            dec_deg = float(getattr(obj, "dec"))
        except (TypeError, ValueError):
            continue
        object_id = str(getattr(obj, "gaia_source_id", "") or getattr(obj, "catalog_id", ""))
        if not object_id:
            continue
        specs.append(
            {
                "object_id": object_id,
                "role": "analyze_linearity_reference",
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
                "catalog_mag": v_mag,
                "catalog_source": "Gaia_DR3_to_Johnson_V",
                "catalog_id": object_id,
                "analyze_bp_rp": bp_rp,
            }
        )
    return specs


def estimate_linearity_faint_limit(
    rows: list[dict[str, float | str | bool | None]],
) -> float | None:
    """Estimate the catalog magnitude where this image reaches useful S/N."""

    valid_rows = [
        row
        for row in rows
        if bool(row.get("valid"))
        and row.get("inst_mag") is not None
        and math.isfinite(float(row["v_mag"]))
        and math.isfinite(float(row["inst_mag"]))
    ]
    if len(valid_rows) < LINEARITY_LIMIT_MIN_REFERENCE_COUNT:
        return None
    valid_rows.sort(key=lambda row: float(row["v_mag"]))
    bright_count = max(
        LINEARITY_LIMIT_MIN_REFERENCE_COUNT,
        len(valid_rows) // 2,
    )
    bright_rows = valid_rows[:bright_count]
    offset = float(
        sorted(float(row["inst_mag"]) - float(row["v_mag"]) for row in bright_rows)[
            len(bright_rows) // 2
        ]
    )

    finite_catalog_magnitudes = sorted(
        float(row["v_mag"])
        for row in rows
        if math.isfinite(float(row["v_mag"]))
    )
    if len(finite_catalog_magnitudes) < LINEARITY_LIMIT_MIN_REFERENCE_COUNT:
        return None
    faint_start = finite_catalog_magnitudes[
        int(0.6 * (len(finite_catalog_magnitudes) - 1))
    ]
    error_rates = [
        float(row["flux_error"]) / float(row["exptime"])
        for row in rows
        if float(row["v_mag"]) >= faint_start
        and row.get("flux_error") is not None
        and row.get("exptime") is not None
        and math.isfinite(float(row["flux_error"]))
        and math.isfinite(float(row["exptime"]))
        and float(row["flux_error"]) > 0.0
        and float(row["exptime"]) > 0.0
    ]
    if not error_rates:
        return None
    error_rates.sort()
    typical_error_rate = float(error_rates[len(error_rates) // 2])
    limiting_flux_rate = LINEARITY_DISPLAY_MIN_EXPECTED_SNR * typical_error_rate
    if not math.isfinite(limiting_flux_rate) or limiting_flux_rate <= 0.0:
        return None
    limiting_inst_mag = -2.5 * math.log10(limiting_flux_rate)
    return limiting_inst_mag - offset


def linearity_plot_rows(
    rows: list[dict[str, float | str | bool | None]],
) -> tuple[list[dict[str, float | str | bool | None]], float | None]:
    """Return valid rows within the image-dependent useful linearity range."""

    faint_limit = estimate_linearity_faint_limit(rows)
    plotted = [
        row
        for row in rows
        if bool(row.get("valid"))
        and row.get("inst_mag") is not None
        and (faint_limit is None or float(row["v_mag"]) <= faint_limit)
    ]
    return plotted, faint_limit


def linearity_rows_for_image(
    context: dict[str, object], path: Path, progress,
) -> tuple[object, list[dict[str, float | str | bool | None]], int]:
    """Measure Gaia-derived V references in one independently selected image."""

    analysis_path = path
    temporary_workspace = None
    frame = _call(context, "read_reference_frame")(analysis_path, 0)
    if frame is None:
        temporary_workspace, analysis_path = _call(context, "solve_image_for_analyze")(
            path, progress
        )
        frame = _call(context, "read_reference_frame")(analysis_path, 0)
    if frame is None:
        raise RuntimeError("Automatic plate solve did not produce a usable WCS image.")
    try:
        progress(f"Analyzed image: {path}")
        gaia_objects = _call(context, "query_gaia_dr3_region")(frame, progress)
        specs = _linearity_specs(gaia_objects)
        if not specs:
            raise RuntimeError(
                "No Gaia references with a valid BP-RP to Johnson-V transformation were found."
            )
        progress(f"Gaia references: {len(specs)} usable after BP-RP filtering.")
        colors_by_catalog_id = {
            str(spec["catalog_id"]): float(spec["analyze_bp_rp"])
            for spec in specs
        }
        settings = _call(context, "resolve_aperture_settings")(frame)
        measurements = _call(context, "aperture_measurements_for_frame")(
            analysis_path,
            0,
            specs,
            getattr(settings, "aperture_radius_px"),
            getattr(settings, "annulus_inner_px"),
            getattr(settings, "annulus_outer_px"),
        )
        rows: list[dict[str, float | str | bool | None]] = []
        for measurement in measurements:
            catalog_mag = getattr(measurement, "catalog_mag", None)
            inst_mag = getattr(measurement, "inst_mag", None)
            catalog_id = str(getattr(measurement, "catalog_id", ""))
            bp_rp = colors_by_catalog_id.get(catalog_id)
            if catalog_mag is None or bp_rp is None:
                continue
            rows.append(
                {
                    "v_mag": float(catalog_mag),
                    "inst_mag": None if inst_mag is None else float(inst_mag),
                    "bp_rp": float(bp_rp),
                    "catalog_id": catalog_id,
                    "valid": bool(getattr(measurement, "valid", False)),
                    "flux_error": getattr(measurement, "flux_error", None),
                    "exptime": getattr(measurement, "exptime", None),
                }
            )
        if not any(bool(row["valid"]) and row["inst_mag"] is not None for row in rows):
            raise RuntimeError("No valid Gaia reference-star aperture measurements are available.")
        return frame, rows, len(specs)
    finally:
        if temporary_workspace is not None:
            temporary_workspace.cleanup()


def create_analyze_tab(context: dict[str, object]) -> object:
    """Create standalone analysis tools, initially Gaia linearity diagnostics."""

    from PyQt6.QtCore import QThread, pyqtSignal
    from PyQt6.QtWidgets import QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    tab = QWidget()
    layout = QVBoxLayout(tab)
    linearity_group = QGroupBox("Photometric linearity")
    linearity_layout = QVBoxLayout(linearity_group)
    controls = QHBoxLayout()
    choose_button = QPushButton("Choose FITS image...")
    controls.addWidget(choose_button)
    refresh_button = QPushButton("Refresh")
    refresh_button.setEnabled(False)
    refresh_button.setToolTip("Run the analysis again for the selected image.")
    controls.addWidget(refresh_button)
    color_filter = QComboBox()
    color_filter.addItem("All BP-RP", (None, None))
    color_filter.addItem("BP-RP ≤ 0.5", (None, 0.5))
    color_filter.addItem("BP-RP ≤ 1.0", (None, 1.0))
    color_filter.addItem("BP-RP ≤ 1.5", (None, 1.5))
    color_filter.addItem("BP-RP ≥ 0.5", (0.5, None))
    color_filter.addItem("BP-RP ≥ 1.0", (1.0, None))
    color_filter.addItem("BP-RP ≥ 1.5", (1.5, None))
    color_filter.setToolTip("Plot only Gaia references on the selected side of the BP-RP limit.")
    controls.addWidget(color_filter)
    controls.addStretch(1)
    linearity_layout.addLayout(controls)
    image_label = QLabel("No image selected")
    image_label.setWordWrap(True)
    linearity_layout.addWidget(image_label)
    layout.addWidget(linearity_group)

    profiling_group = QGroupBox("Stack Profiling")
    profiling_layout = QVBoxLayout(profiling_group)
    profiling_layout.addWidget(QLabel("Select an original FITS folder to analyze its timing."))
    profiling_row = QHBoxLayout()
    profiling_source_edit = QLineEdit()
    profiling_source_edit.setPlaceholderText("Original Seestar FITS folder")
    profiling_browse_button = QPushButton("Browse...")
    profiling_row.addWidget(profiling_source_edit, stretch=1)
    profiling_row.addWidget(profiling_browse_button)
    profiling_layout.addLayout(profiling_row)
    profiling_action_row = QHBoxLayout()
    profiling_mode = QComboBox()
    profiling_mode.addItem("Continuous", "continuous")
    profiling_mode.addItem("Gap-aware", "gap_aware")
    profiling_mode.setCurrentIndex(1)
    profiling_start_button = QPushButton("Analyze Timing")
    profiling_action_row.addWidget(QLabel("Mode"))
    profiling_action_row.addWidget(profiling_mode)
    profiling_action_row.addWidget(profiling_start_button)
    profiling_action_row.addStretch(1)
    profiling_layout.addLayout(profiling_action_row)
    layout.addWidget(profiling_group)
    layout.addStretch(1)
    active_worker: QThread | None = None
    selected_path: Path | None = None

    class LinearityWorker(QThread):
        log_message = pyqtSignal(str)
        completed = pyqtSignal(object)

        def __init__(self, image_path: Path) -> None:
            super().__init__()
            self.image_path = image_path

        def run(self) -> None:
            try:
                self.completed.emit(
                    ("ok", linearity_rows_for_image(context, self.image_path, self.log_message.emit))
                )
            except Exception as exc:
                self.completed.emit(("error", str(exc)))

    def log(message: str) -> None:
        append_log = context.get("append_log")
        if callable(append_log):
            append_log(f"Analyze: {message}")

    def open_stack_profiling() -> None:
        runner = context.get("open_profiling_runner")
        source_dir = profiling_source_edit.text().strip()
        if not source_dir:
            log("ERROR: Select an original FITS folder first.")
        elif callable(runner):
            runner(source_dir, str(profiling_mode.currentData()))
        else:
            log("ERROR: Stack Profiling is not available.")

    def choose_profiling_folder() -> None:
        selected = QFileDialog.getExistingDirectory(tab, "Select Original Seestar FITS Folder", profiling_source_edit.text().strip())
        if selected: profiling_source_edit.setText(selected)

    def show_plot(
        path: Path,
        rows: list[dict[str, float | str | bool | None]],
        reference_count: int,
    ) -> None:
        lower, upper = color_filter.currentData()
        filtered_rows = [
            row for row in rows
            if (lower is None or float(row["bp_rp"]) >= float(lower))
            and (upper is None or float(row["bp_rp"]) <= float(upper))
        ]
        plotted_rows, faint_limit = linearity_plot_rows(filtered_rows)
        if not plotted_rows:
            log("ERROR: No valid reference stars remain after the BP-RP filter.")
            return
        plot_dialog = QDialog(tab)
        plot_dialog.setWindowTitle(f"Photometric linearity – {path.name}")
        try:
            plot_layout = QVBoxLayout(plot_dialog)
            figure = Figure(figsize=(8.0, 6.0))
            canvas = FigureCanvas(figure)
            plot_layout.addWidget(canvas)
            x = [float(row["v_mag"]) for row in plotted_rows]
            y = [float(row["inst_mag"]) for row in plotted_rows]
            colors = [float(row["bp_rp"]) for row in plotted_rows]
            offsets = sorted(inst_mag - v_mag for v_mag, inst_mag in zip(x, y))
            median_offset = offsets[len(offsets) // 2]
            residuals = [inst_mag - v_mag - median_offset for v_mag, inst_mag in zip(x, y)]
            grid = figure.add_gridspec(2, 2, width_ratios=(1.0, 0.045), wspace=0.08, hspace=0.16)
            top = figure.add_subplot(grid[0, 0])
            bottom = figure.add_subplot(grid[1, 0], sharex=top)
            color_axis = figure.add_subplot(grid[:, 1])
            top.scatter(x, y, c=colors, cmap="viridis", s=14, alpha=0.7)
            xmin, xmax = min(x), max(x)
            top.plot(
                [xmin, xmax],
                [xmin + median_offset, xmax + median_offset],
                color="tab:red",
                linewidth=1.2,
                label=f"slope 1; median offset {median_offset:.2f}",
            )
            top.set_ylabel("Instrumental magnitude")
            filter_label = color_filter.currentText()
            limit_label = "" if faint_limit is None else f" · V ≤ {faint_limit:.2f}"
            top.set_title(
                f"{path.name} · {filter_label} · {len(plotted_rows)} stars{limit_label}"
            )
            top.grid(True, alpha=0.25)
            top.legend(loc="best")
            residual_points = bottom.scatter(x, residuals, c=colors, cmap="viridis", s=14, alpha=0.7)
            bottom.axhline(0.0, color="tab:red", linewidth=1.0)
            bottom.set_xlabel("Gaia-derived Johnson V magnitude")
            bottom.set_ylabel("Residual (mag)")
            bottom.grid(True, alpha=0.25)
            colorbar = figure.colorbar(residual_points, cax=color_axis)
            colorbar.set_label("Gaia BP-RP")
            canvas.draw()
            log(
                f"Plotted {len(plotted_rows)}/{len(rows)} measurement candidate(s) "
                f"from {reference_count} Gaia-derived V reference(s); {filter_label}"
                + (
                    f"; dynamic faint limit V={faint_limit:.2f} "
                    f"at expected S/N={LINEARITY_DISPLAY_MIN_EXPECTED_SNR:.0f}."
                    if faint_limit is not None
                    else "."
                )
            )
            plot_dialog.resize(900, 720)
            plot_dialog.exec()
        except Exception as exc:
            log(f"ERROR: {exc}")

    def analyze(path: Path) -> None:
        nonlocal active_worker
        if active_worker is not None:
            return
        choose_button.setEnabled(False)
        refresh_button.setEnabled(False)
        log("Starting image analysis.")
        worker = LinearityWorker(path)
        active_worker = worker
        worker.log_message.connect(log)

        def handle_result(result: object) -> None:
            nonlocal active_worker
            status_kind, payload = result
            if status_kind == "ok":
                _frame, rows, reference_count = payload
                show_plot(path, rows, reference_count)
            else:
                log(f"ERROR: {payload}")
            worker.deleteLater()
            active_worker = None
            choose_button.setEnabled(True)
            refresh_button.setEnabled(selected_path is not None)

        worker.completed.connect(handle_result)
        worker.start()

    def choose_image() -> None:
        nonlocal selected_path
        selected, _selected_filter = QFileDialog.getOpenFileName(
            tab,
            "Choose solved FITS image for photometric linearity",
            "",
            "FITS images (*.fit *.fits *.fts);;All files (*)",
        )
        if not selected:
            return
        selected_path = Path(selected)
        image_label.setText(str(selected_path))
        analyze(selected_path)

    choose_button.clicked.connect(choose_image)
    refresh_button.clicked.connect(lambda: analyze(selected_path) if selected_path is not None else None)
    profiling_browse_button.clicked.connect(choose_profiling_folder)
    profiling_start_button.clicked.connect(open_stack_profiling)
    return tab
