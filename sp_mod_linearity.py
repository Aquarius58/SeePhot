"""Optional linearity/dynamic-range QC dialog for SeePhot_Main.py."""

from __future__ import annotations

from pathlib import Path


LINEARITY_QC_VERSION = "0.1"


def _call(context: dict[str, object], name: str):
    value = context.get(name)
    if not callable(value):
        raise RuntimeError(f"Linearity QC needs context function: {name}")
    return value


def _catalog_spec(obj: object) -> dict[str, object] | None:
    magnitude_float = getattr(obj, "magnitude_float", None)
    catalog_mag = magnitude_float() if callable(magnitude_float) else None
    if catalog_mag is None:
        return None

    value_for = getattr(obj, "value_for", None)
    catalog_source = getattr(obj, "catalog_source", "")
    catalog_id = getattr(obj, "catalog_id", "")
    ra_text = getattr(obj, "ra", "")
    dec_text = getattr(obj, "dec", "")
    try:
        ra_deg = float(ra_text)
        dec_deg = float(dec_text)
    except (TypeError, ValueError):
        return None

    def value(candidates: tuple[str, ...]) -> str:
        return str(value_for(candidates)).strip() if callable(value_for) else ""

    def number(candidates: tuple[str, ...]) -> float | None:
        text = value(candidates)
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def integer(candidates: tuple[str, ...]) -> int | None:
        text = value(candidates)
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    mag_b = number(("mag_b", "Bmag", "B"))
    mag_g = number(("mag_g", "gmag", "g"))
    mag_r = number(("mag_r", "rmag", "r"))
    return {
        "object_id": str(catalog_id or getattr(obj, "name", "")),
        "role": "linearity_reference",
        "ra_deg": ra_deg,
        "dec_deg": dec_deg,
        "catalog_mag": catalog_mag,
        "catalog_source": str(catalog_source or ""),
        "catalog_id": str(catalog_id or ""),
        "catalog_mag_error": number(("err_mag_v", "e_Vmag", "emag", "mag_error")),
        "catalog_nobs": integer(("nobs_v", "nobs", "Nobs")),
        "catalog_mag_b": mag_b,
        "catalog_mag_g": mag_g,
        "catalog_mag_r": mag_r,
        "catalog_b_minus_v": None if mag_b is None else mag_b - catalog_mag,
        "catalog_g_minus_r": None if mag_g is None or mag_r is None else mag_g - mag_r,
    }


def _linearity_rows(context: dict[str, object], progress) -> tuple[object, list[dict[str, object]]]:
    frame_getter = _call(context, "get_reference_frame")
    frame = frame_getter()
    if frame is None:
        raise RuntimeError("No reference frame available. Run field setup/detect first.")

    mode_getter = context.get("get_photometry_mode")
    mode = mode_getter() if callable(mode_getter) else "lightcurve"
    progress(f"Linearity QC mode: {mode}")
    progress(f"Analyzed frame: {getattr(frame, 'path', '-')}")

    query_catalog = _call(context, "query_comparison_catalog_region")
    catalog_objects = query_catalog(frame, None, 99.0, progress)
    specs = [spec for obj in catalog_objects if (spec := _catalog_spec(obj)) is not None]
    if not specs:
        raise RuntimeError("No catalog reference objects with V magnitude and coordinates found.")

    resolve_settings = _call(context, "resolve_aperture_settings")
    settings = resolve_settings(frame)
    measure = _call(context, "aperture_measurements_for_frame")
    measurements = measure(
        Path(getattr(frame, "path")),
        int(getattr(frame, "index", 0)),
        specs,
        getattr(settings, "aperture_radius_px"),
        getattr(settings, "annulus_inner_px"),
        getattr(settings, "annulus_outer_px"),
    )

    rows: list[dict[str, object]] = []
    for measurement in measurements:
        catalog_mag = getattr(measurement, "catalog_mag", None)
        inst_mag = getattr(measurement, "inst_mag", None)
        if catalog_mag is None or inst_mag is None:
            continue
        if not getattr(measurement, "valid", False):
            continue
        rows.append(
            {
                "catalog_mag": float(catalog_mag),
                "inst_mag": float(inst_mag),
                "catalog_id": getattr(measurement, "catalog_id", ""),
                "catalog_source": getattr(measurement, "catalog_source", ""),
                "quality_flag": getattr(measurement, "quality_flag", ""),
            }
        )
    if not rows:
        raise RuntimeError("No valid aperture measurements available for the linearity plot.")
    return frame, rows


def open_linearity_dialog(context: dict[str, object], parent=None) -> None:
    """Open the first linearity QC dialog: catalog V magnitude vs instrumental mag."""

    from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Linearity QC {LINEARITY_QC_VERSION}")
    layout = QVBoxLayout(dialog)

    status = QTextEdit()
    status.setReadOnly(True)
    status.setFixedHeight(90)
    layout.addWidget(status)

    figure = Figure(figsize=(7.5, 4.8), tight_layout=True)
    canvas = FigureCanvas(figure)
    layout.addWidget(canvas)

    button_row = QHBoxLayout()
    run_button = QPushButton("Run")
    close_button = QPushButton("Close")
    button_row.addWidget(run_button)
    button_row.addStretch(1)
    button_row.addWidget(close_button)
    layout.addLayout(button_row)

    def log(message: str) -> None:
        status.append(message)
        append_log = context.get("append_log")
        if callable(append_log):
            append_log(f"Linearity QC: {message}")

    def run() -> None:
        run_button.setEnabled(False)
        figure.clear()
        try:
            frame, rows = _linearity_rows(context, log)
            ax = figure.add_subplot(111)
            x = [float(row["catalog_mag"]) for row in rows]
            y = [float(row["inst_mag"]) for row in rows]
            offsets = [inst_mag - catalog_mag for catalog_mag, inst_mag in zip(x, y)]
            median_offset = sorted(offsets)[len(offsets) // 2]
            xmin = min(x)
            xmax = max(x)
            ax.scatter(x, y, s=8, alpha=0.45)
            ax.plot(
                [xmin, xmax],
                [xmin + median_offset, xmax + median_offset],
                color="tab:red",
                linewidth=1.2,
                label=f"slope 1, median offset {median_offset:.2f}",
            )
            ax.set_xlabel("Catalog V magnitude")
            ax.set_ylabel("Instrumental magnitude")
            ax.set_title(Path(getattr(frame, "path")).name)
            ax.grid(True, alpha=0.25)
            ax.legend(loc="best")
            canvas.draw()
            log(f"Plotted {len(rows)} valid catalog reference measurement(s).")
        except Exception as exc:
            log(f"ERROR: {exc}")
        finally:
            run_button.setEnabled(True)

    run_button.clicked.connect(run)
    close_button.clicked.connect(dialog.accept)
    dialog.resize(820, 620)
    run()
    dialog.exec()
