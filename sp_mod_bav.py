#!/usr/bin/env python3
"""Optional BAV report tab for SeePhot_Main.py.

The main application imports this module only when it is present next to
SeePhot_Main.py.  Keep the tab factory small and import PyQt lazily so the
module can also be used by future command-line helpers without a GUI import at
module import time.
"""

from __future__ import annotations

# SPDX-License-Identifier: GPL-3.0-or-later

import csv
import json
import math
import os
import re
import sys
import traceback
import warnings
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

import sp_mod_results as _RESULT_CONTRACT

warnings.filterwarnings(
    "ignore",
    message=r"XDG_CACHE_HOME is set to .*but the default location.*already exists.*",
)


PLUGIN_TAB_LABEL = "BAV"
BAV_PLUGIN_VERSION = "0.4"
APP_NAME = "SeePhot"
CONFIG_NAME = "bav_report.json"
REPORT_TABLE_HEADER_BACKGROUND = "#4472C4"
REPORT_TABLE_HEADER_TEXT = "#FFFFFF"
REPORT_LINK_COLOR = "#0563C1"
BAV_PDF_COMMENT_MAX_LENGTH = 90
BAV_PDF_COMMENT_FONT_SIZES = (8.0, 7.5, 7.0, 6.5, 6.0, 5.5)
BAV_PDF_COMMENT_VALUE_WIDTH_MM = 57
VSX_DETAIL_URL_TEMPLATE = "https://vsx.aavso.org/index.php?oid={oid}&view=detail.top"
RESULT_KIND_LIGHTCURVE = "lightcurve"
RESULT_KIND_SINGLE_FIELD_ZP = "single_field_zp"
SINGLE_FIELD_ZP_CALIBRATION_METHODS = {
    "field_zero_point_apass_dr10_v1",
    "field-zero-point-apass-dr10-v1",
    "field_zero_point_auto_catalog_v1",
    "field-zero-point-auto-catalog-v1",
}
TELESCOPE_CHOICES = _RESULT_CONTRACT.RESULT_TELESCOPE_CHOICES
TELESCOPE_SPECS = _RESULT_CONTRACT.RESULT_TELESCOPE_SPECS


DEFAULT_CONFIG = {
    "bav_code": "",
    "observer_name": "",
    "aavso_code": "",
    "site_name": "",
    "latitude_hemisphere": "N",
    "latitude_degrees": "0",
    "latitude_minutes": "0",
    "latitude_seconds": "0.0",
    "longitude_hemisphere": "O",
    "longitude_degrees": "0",
    "longitude_minutes": "0",
    "longitude_seconds": "0.0",
    "telescope": "Seestar S50",
}
LATITUDE_RANGE = (-90.0, 90.0)
LONGITUDE_RANGE = (-180.0, 180.0)
LATITUDE_MAX_DEGREES = 90
LONGITUDE_MAX_DEGREES = 180
LIGHTCURVE_SHEET_REQUIRED_METADATA = (
    "OBJECT_NAME",
    "OBJECT_RA",
    "OBJECT_DEC",
    "OBJECT_VAR_TYPE",
    "OBJECT_MAG_RANGE",
    "OBSERVER_BAV",
    "OBSERVER_NAME",
    "OBSERVER_AAVSO",
    "OBSERVER_SITE",
    "OBSERVER_LATITUDE",
    "OBSERVER_LONGITUDE",
    "TELESCOPE",
    "SENSOR",
    "PIXEL_SCALE_ARCSEC_PX",
    "FILTER",
    "LIGHTCURVE_SOFTWARE",
    "PHOTOMETRY_METHOD",
    "UTC_START",
    "UTC_END",
    "EXPOSURE_SECONDS",
    "OBS_COUNT",
    "COMP_STARS",
)


def user_config_dir() -> Path:
    """Return an OS-appropriate per-user config directory without extra deps."""

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / APP_NAME
    return Path.home() / ".config" / APP_NAME


def config_path() -> Path:
    """Return the BAV plugin config file path."""

    return user_config_dir() / CONFIG_NAME


def load_config() -> dict[str, str]:
    """Load persisted BAV settings, falling back to defaults."""

    path = config_path()
    config = dict(DEFAULT_CONFIG)
    if not path.exists():
        return config

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return config
    if not isinstance(data, dict):
        return config

    for key, default_value in DEFAULT_CONFIG.items():
        value = data.get(key, default_value)
        config[key] = str(value) if value is not None else ""
    config["aavso_code"] = config["aavso_code"].strip().upper()
    if config["telescope"] not in TELESCOPE_CHOICES:
        config["telescope"] = DEFAULT_CONFIG["telescope"]
    return config


def save_config(config: dict[str, str]) -> Path:
    """Persist BAV settings and return the written path."""

    clean_config = dict(DEFAULT_CONFIG)
    for key in DEFAULT_CONFIG:
        clean_config[key] = str(config.get(key, "")).strip()
    clean_config["bav_code"] = clean_config["bav_code"].upper()
    clean_config["aavso_code"] = clean_config["aavso_code"].upper()
    if clean_config["telescope"] not in TELESCOPE_CHOICES:
        clean_config["telescope"] = DEFAULT_CONFIG["telescope"]

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(clean_config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


class BavConfigValidationError(ValueError):
    """Report all incomplete or invalid BAV configuration fields."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issues))


def config_file_issue(path: Path | None = None) -> str | None:
    """Return a user-facing problem with the persisted BAV config, if any."""

    path = path or config_path()
    if not path.exists():
        return f"Konfigurationsdatei fehlt: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Konfigurationsdatei kann nicht gelesen werden: {path} ({exc})"
    if not isinstance(data, dict):
        return f"Konfigurationsdatei hat kein gültiges Objektformat: {path}"
    return None


def _normalized_decimal(value: str) -> str:
    return value.strip().replace(",", ".")


def _format_decimal_degrees(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _integer_config_value(config: dict[str, str], key: str, label: str, minimum: int, maximum: int) -> int:
    text = str(config.get(key, "")).strip()
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"{label} muss eine ganze Zahl sein.") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} muss zwischen {minimum} und {maximum} liegen.")
    return value


def _seconds_config_value(config: dict[str, str], key: str, label: str) -> float:
    text = _normalized_decimal(str(config.get(key, "")))
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"{label} muss eine Dezimalzahl sein.") from exc
    if not 0.0 <= value < 60.0:
        raise ValueError(f"{label} muss zwischen 0,0 und 59,9 liegen.")
    return value


def _format_seconds_config(value: float) -> str:
    return f"{value:.1f}"


def _dms_config_to_decimal(
    config: dict[str, str],
    prefix: str,
    label: str,
    positive_hemisphere: str,
    negative_hemisphere: str,
    max_degrees: int,
) -> tuple[float, dict[str, str]]:
    hemisphere_key = f"{prefix}_hemisphere"
    degrees_key = f"{prefix}_degrees"
    minutes_key = f"{prefix}_minutes"
    seconds_key = f"{prefix}_seconds"

    hemisphere = str(config.get(hemisphere_key, "")).strip().upper()
    if hemisphere not in {positive_hemisphere, negative_hemisphere}:
        raise ValueError(
            f"{label}: Hemisphäre muss {positive_hemisphere} oder "
            f"{negative_hemisphere} sein."
        )

    degrees = _integer_config_value(config, degrees_key, f"{label}: Grad", 0, max_degrees)
    minutes = _integer_config_value(config, minutes_key, f"{label}: Minuten", 0, 59)
    seconds = _seconds_config_value(config, seconds_key, f"{label}: Sekunden")
    if degrees == max_degrees and (minutes != 0 or seconds != 0.0):
        raise ValueError(
            f"{label}: Bei {max_degrees} Grad müssen Minuten und Sekunden 0 sein."
        )

    sign = -1 if hemisphere == negative_hemisphere else 1
    decimal = sign * (degrees + minutes / 60.0 + seconds / 3600.0)
    normalized = {
        hemisphere_key: hemisphere,
        degrees_key: str(degrees),
        minutes_key: str(minutes),
        seconds_key: _format_seconds_config(seconds),
    }
    return decimal, normalized


def parse_coordinate(value: str, label: str, value_range: tuple[float, float], allowed_hemispheres: str) -> float:
    """Parse decimal degrees or DMS coordinates and validate plausible ranges."""

    original = value.strip()
    if not original:
        raise ValueError(f"{label} must not be empty.")

    text = original.upper()
    text = (
        text.replace("′", "'")
        .replace("’", "'")
        .replace("`", "'")
        .replace("″", '"')
        .replace("“", '"')
        .replace("”", '"')
        .replace(",", ".")
    )
    hemisphere_matches = re.findall(r"[NSEW]", text)
    invalid_hemispheres = [hemisphere for hemisphere in hemisphere_matches if hemisphere not in allowed_hemispheres]
    if invalid_hemispheres:
        raise ValueError(f"{label} hemisphere must be one of {', '.join(allowed_hemispheres)}.")
    if len(set(hemisphere_matches)) > 1:
        raise ValueError(f"{label} must not contain multiple hemispheres.")
    hemisphere = hemisphere_matches[0] if hemisphere_matches else ""

    number_matches = re.findall(r"[+-]?\d+(?:\.\d+)?", text)
    if not number_matches:
        raise ValueError(f"{label} must be a decimal number or DMS coordinate.")
    if len(number_matches) > 3:
        raise ValueError(f"{label} must contain at most degrees, minutes and seconds.")

    degrees = float(number_matches[0])
    minutes = float(number_matches[1]) if len(number_matches) >= 2 else 0.0
    seconds = float(number_matches[2]) if len(number_matches) >= 3 else 0.0
    if minutes < 0 or minutes >= 60:
        raise ValueError(f"{label} minutes must be between 0 and 59.")
    if seconds < 0 or seconds >= 60:
        raise ValueError(f"{label} seconds must be between 0 and 59.999.")
    if degrees < 0 and hemisphere:
        raise ValueError(f"{label} must not use both a negative sign and hemisphere.")

    sign = -1 if degrees < 0 else 1
    if hemisphere in ("S", "W"):
        sign = -1
    elif hemisphere in ("N", "E"):
        sign = 1
    coordinate = sign * (abs(degrees) + minutes / 60.0 + seconds / 3600.0)

    minimum, maximum = value_range
    if not minimum <= coordinate <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g} degrees.")
    return coordinate


def _validated_coordinate(value: str, label: str, value_range: tuple[float, float], allowed_hemispheres: str) -> str:
    coordinate = parse_coordinate(value, label, value_range, allowed_hemispheres)
    return _format_decimal_degrees(coordinate)


def _validated_decimal(value: str, label: str, value_range: tuple[float, float]) -> str:
    normalized = _normalized_decimal(value)
    try:
        numeric_value = float(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be a decimal number.") from exc

    minimum, maximum = value_range
    if not minimum <= numeric_value <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return _format_decimal_degrees(numeric_value)


def safe_filename_component(text: str, fallback: str = "target") -> str:
    """Return a compact filesystem-safe name component."""

    raw = text.strip() or fallback
    chars: list[str] = []
    last_was_separator = False
    for char in raw:
        if char.isalnum() or char in ("-", "_"):
            chars.append(char)
            last_was_separator = False
        elif not last_was_separator:
            chars.append("_")
            last_was_separator = True
    component = "".join(chars).strip("_")
    return (component or fallback)[:80]


def safe_filename_text(text: str, fallback: str = "target") -> str:
    """Return a filesystem-safe name component while preserving spaces."""

    raw = re.sub(r"\s+", " ", text.strip() or fallback)
    chars: list[str] = []
    for char in raw:
        if char.isalnum() or char in (" ", "-", "_", ".", "+"):
            chars.append(char)
        else:
            chars.append("_")
    component = "".join(chars).strip(" ._")
    return (component or fallback)[:120]


def bav_filename_object_name(text: str, fallback: str = "target") -> str:
    """Return the object-name component used in BAV filenames."""

    return safe_filename_text(text.replace("_", " "), fallback)


def bav_technical_object_name(text: str, constellation: str, fallback: str = "target") -> str:
    """Return the object-name field used with a separate BAV constellation field."""

    object_name = bav_filename_object_name(text, fallback)
    constellation_text = safe_filename_text(constellation, "").strip()
    if not constellation_text:
        return object_name
    pattern = re.compile(rf"(?:^|\s+){re.escape(constellation_text)}$", re.IGNORECASE)
    shortened = pattern.sub("", object_name).strip()
    return shortened or object_name


def parse_iso_date(value: str) -> str:
    """Return YYYY-MM-DD from common CSV/FITS timestamp strings."""

    text = value.strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if len(text) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
        return text[:10]
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        return ""


def read_result_curve_with_metadata(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Read a result CSV that may start with '# KEY=VALUE' metadata lines."""

    metadata: dict[str, str] = {}
    data_lines: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                content = line[1:].strip()
                if "=" in content:
                    key, value = content.split("=", 1)
                    metadata[key.strip()] = value.strip()
                continue
            data_lines.append(line)
    if not data_lines:
        return metadata, []

    rows = list(csv.DictReader(data_lines))
    return metadata, rows


def result_telescope_display_text(result_csv: Path | None) -> str:
    """Return the telescope recorded for the current result."""

    if result_csv is None or not result_csv.is_file():
        return "none"
    try:
        metadata, _rows = read_result_curve_with_metadata(result_csv)
    except OSError:
        return "none"
    return metadata.get("TELESCOPE", "").strip() or "none"


def report_date_from_lightcurve(result_csv: Path | None) -> str:
    """Return the report start date from UTC_START or the earliest phot_time."""

    if result_csv is None or not result_csv.exists():
        return datetime.now(timezone.utc).date().isoformat()

    metadata, rows = read_result_curve_with_metadata(result_csv)
    for key in ("REPORT_DATE", "UTC_START"):
        parsed = parse_iso_date(metadata.get(key, ""))
        if parsed:
            return parsed

    dates = [
        parsed
        for row in rows
        if (parsed := parse_iso_date(row.get("phot_time", "")))
    ]
    if dates:
        return min(dates)
    return datetime.now(timezone.utc).date().isoformat()


def lightcurve_sheet_title(target_name: str, result_csv: Path | None) -> str:
    """Return '<target> ** Lightcurve Report ** <start-date>'."""

    return f"{target_name} ** Lightcurve Report ** {report_date_from_lightcurve(result_csv)}"


def _metadata_value(metadata: dict[str, str], key: str) -> str:
    return metadata.get(key, "").strip()


def _period_text(value: str) -> str:
    text = value.strip()
    if text and not text.lower().endswith("d"):
        return f"{text}d"
    return text


def _pixel_scale_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return f'{text}"/px'


def _utc_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    return text.replace("T", " ").split(".", 1)[0]


def _exposure_text(value: str) -> str:
    numeric = _optional_float(value)
    if math.isfinite(numeric):
        if abs(numeric - round(numeric)) < 1e-6:
            return f"{int(round(numeric))} sec"
        return f"{numeric:.2f} sec"
    return value.strip()


def _positive_int(value: str) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def observation_method_and_image_count(
    metadata: dict[str, str],
    rows: list[dict[str, str]],
) -> tuple[str, str, str]:
    """Return method, used original-image count, and exposure unit for BAV."""

    provenance = _metadata_value(metadata, "SOURCE_PROVENANCE")
    binning_mode = _metadata_value(metadata, "BINNING_MODE")
    binning_value = _positive_int(_metadata_value(metadata, "BINNING_VALUE"))
    if provenance == "BINNED_RESULT" and binning_value is not None:
        if binning_mode == "count":
            method = f"Binning {binning_value} img"
        elif binning_mode == "seconds":
            method = f"Binning {binning_value} s"
        else:
            method = "Binning"
        used_images = sum(
            count
            for row in rows
            if (count := _positive_int(row.get("bin_input_count", ""))) is not None
        )
        return method, str(used_images) if used_images else "n/a", "image"
    if provenance == "SINGLE_FRAME_LG_SERIES":
        return "Single images", str(len(rows)), "image"
    if provenance == "DERIVED_FITS":
        stack_images = _positive_int(_metadata_value(metadata, "STACK_IMAGES_PER_RESULT"))
        if stack_images is not None:
            return f"Stack {stack_images} img", str(len(rows) * stack_images), "stack"
        return "Stacked images", "n/a", "stack"
    return "", "n/a", ""


def observation_details_rows(
    metadata: dict[str, str],
    rows: list[dict[str, str]],
) -> list[tuple[str, str]]:
    """Return the compact acquisition and observer rows for a BAV sheet."""

    method, image_count, exposure_unit = observation_method_and_image_count(metadata, rows)
    exposure = _exposure_text(_metadata_value(metadata, "EXPOSURE_SECONDS"))
    if exposure and exposure_unit:
        exposure = f"{exposure} / {exposure_unit}"
    return [
        ("UTC Range", _utc_text(_metadata_value(metadata, "UTC_START"))),
        ("", _utc_text(_metadata_value(metadata, "UTC_END"))),
        ("HJD Range", _hjd_range_text(metadata, rows)),
        ("Exposure", exposure),
        ("Method", method),
        ("Images used", image_count),
        ("N° Obs", _metadata_value(metadata, "OBS_COUNT")),
        ("Location", _metadata_value(metadata, "OBSERVER_SITE")),
        ("Lat / Long", _lat_long_text(metadata)),
        ("Observer", _metadata_value(metadata, "OBSERVER_NAME")),
        ("BAV", _metadata_value(metadata, "OBSERVER_BAV")),
        ("AAVSO", _metadata_value(metadata, "OBSERVER_AAVSO")),
    ]


def _format_dms(value: float, positive_suffix: str, negative_suffix: str) -> str:
    suffix = positive_suffix if value >= 0 else negative_suffix
    absolute = abs(value)
    degrees = int(absolute)
    minutes_float = (absolute - degrees) * 60.0
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60.0
    return f"{degrees}°{minutes:02d}′{seconds:04.1f}″ {suffix}"


def _lat_long_text(metadata: dict[str, str]) -> str:
    try:
        latitude = float(_metadata_value(metadata, "OBSERVER_LATITUDE"))
        longitude = float(_metadata_value(metadata, "OBSERVER_LONGITUDE"))
    except ValueError:
        return ""
    return f"{_format_dms(latitude, 'N', 'S')} / {_format_dms(longitude, 'E', 'W')}"


def _vsx_link(metadata: dict[str, str]) -> str:
    url = _metadata_value(metadata, "OBJECT_VSX_URL")
    oid = _metadata_value(metadata, "OBJECT_VSX_OID")
    if not url and oid:
        url = VSX_DETAIL_URL_TEMPLATE.format(oid=oid)
    if not url:
        return ""
    return (
        f'<link href="{escape(url, quote=True)}" color="{REPORT_LINK_COLOR}">'
        "<u>Objektseite öffnen</u></link>"
    )


def _report_table(
    title: str,
    rows: list[tuple[str, str]],
    styles: object,
    widths: tuple[float, float],
    *,
    section_break_after: frozenset[str] = frozenset(),
) -> object:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    label_style = ParagraphStyle(
        f"{title}Label",
        parent=styles["BodyText"],
        fontSize=8,
        leading=9,
    )
    value_style = ParagraphStyle(
        f"{title}Value",
        parent=styles["BodyText"],
        fontSize=8,
        leading=9,
    )
    title_style = ParagraphStyle(
        f"{title}Title",
        parent=styles["Heading3"],
        fontSize=10,
        leading=11,
        textColor=colors.HexColor(REPORT_TABLE_HEADER_TEXT),
    )
    data: list[list[object]] = [[Paragraph(f"<b>{escape(title)}</b>", title_style), ""]]
    for label, value in rows:
        data.append(
            [
                Paragraph(f"<b>{escape(label)}</b>", label_style),
                Paragraph(value or "", value_style),
            ]
        )
    table = Table(data, colWidths=list(widths), hAlign="LEFT")
    style_commands = [
                ("SPAN", (0, 0), (1, 0)),
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor(REPORT_TABLE_HEADER_BACKGROUND),
                ),
                ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#eef4fa")),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor(REPORT_TABLE_HEADER_TEXT),
                ),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#8fa8bd")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#b8c7d3")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for row_index, (label, _value) in enumerate(rows, start=1):
        if label in section_break_after:
            style_commands.append(
                ("LINEBELOW", (0, row_index), (1, row_index), 0.9, colors.HexColor("#6d8eaa"))
            )
    table.setStyle(TableStyle(style_commands))
    return table


def report_info_tables(metadata: dict[str, str], styles: object) -> object:
    """Return side-by-side Object Info and Equipment tables."""

    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    object_rows = [
        ("Name", escape(_metadata_value(metadata, "OBJECT_NAME"))),
        (
            "RA / DEC",
            escape(
                " / ".join(
                    value
                    for value in (
                        _metadata_value(metadata, "OBJECT_RA"),
                        _metadata_value(metadata, "OBJECT_DEC"),
                    )
                    if value
                )
            ),
        ),
        ("Var. Type", escape(_metadata_value(metadata, "OBJECT_VAR_TYPE"))),
        ("Mag. Range", escape(_metadata_value(metadata, "OBJECT_MAG_RANGE"))),
        ("Period", escape(_period_text(_metadata_value(metadata, "OBJECT_PERIOD")))),
        ("Epoch", escape(_metadata_value(metadata, "OBJECT_EPOCH"))),
        ("VSX", _vsx_link(metadata)),
    ]
    equipment_rows = [
        ("Telescope", escape(_metadata_value(metadata, "TELESCOPE"))),
        ("Sensor", escape(_metadata_value(metadata, "SENSOR"))),
        ("Pixel Scale", escape(_pixel_scale_text(_metadata_value(metadata, "PIXEL_SCALE_ARCSEC_PX")))),
        ("Filter", escape(_metadata_value(metadata, "FILTER"))),
        ("Software", escape(_metadata_value(metadata, "LIGHTCURVE_SOFTWARE") or _metadata_value(metadata, "SOFTWARE"))),
    ]
    object_table = _report_table("Object Info", object_rows, styles, (25 * mm, 57 * mm))
    equipment_table = _report_table("Equipment", equipment_rows, styles, (27 * mm, 55 * mm))
    outer = Table([[object_table, "", equipment_table]], colWidths=[84 * mm, 4 * mm, 84 * mm], hAlign="LEFT")
    outer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return outer


def _result_rows_jd_values(rows: list[dict[str, str]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        jd = _optional_float(row.get("jd"))
        if math.isfinite(jd):
            values.append(jd)
    return values


def _hjd_range_text(metadata: dict[str, str], rows: list[dict[str, str]]) -> str:
    jd_values = _result_rows_jd_values(rows)
    if not jd_values:
        return ""
    hjd_values = hjd_values_from_metadata(jd_values, metadata)
    return f"{min(hjd_values):.5f} – {max(hjd_values):.5f}"


def _compact_photometry_method(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    radius_match = re.search(r"radius_px=([0-9.]+)", text)
    annulus_match = re.search(r"annulus_px=([0-9.]+)-([0-9.]+)", text)
    if radius_match and annulus_match:
        radius = float(radius_match.group(1))
        annulus_inner = float(annulus_match.group(1))
        annulus_outer = float(annulus_match.group(2))
        return f"Aperture r={radius:.1f} px, sky={annulus_inner:.1f}-{annulus_outer:.1f} px"
    fwhm_match = re.search(r"reference_fwhm_px=([0-9.]+)", text)
    if fwhm_match:
        return f"Reference FWHM {float(fwhm_match.group(1)):.1f} px"
    return text


def _split_semicolon_field(row: dict[str, str], key: str) -> list[str]:
    return [item.strip() for item in row.get(key, "").split(";")]


def _short_catalog_source(value: str) -> str:
    text = value.strip()
    if text == "APASS_DR10":
        return "APASS"
    return text


def _catalog_designation(source: str, catalog_id: str) -> str:
    return " ".join(
        part for part in (_short_catalog_source(source), catalog_id.strip()) if part
    )


def _metadata_catalog_designation(entry: str) -> str:
    parts = entry.strip().split()
    if len(parts) < 2:
        return entry.strip()
    return _catalog_designation(parts[0], parts[1])


def _catalog_display_name(value: str) -> str:
    source = value.strip()
    return "APASS DR10" if source == "APASS_DR10" else source


def _catalog_sources_for_photometry(
    metadata: dict[str, str],
    rows: list[dict[str, str]],
) -> list[str]:
    sources: list[str] = []
    if rows:
        row = rows[0]
        sources.extend(_split_semicolon_field(row, "comparison_catalog_sources"))
        sources.append(row.get("check_catalog_source", "").strip())
    if not any(sources):
        for key in ("COMP_STARS", "CHECK_STAR"):
            for entry in _metadata_value(metadata, key).split(";"):
                parts = entry.strip().split()
                if parts:
                    sources.append(parts[0])
    return list(dict.fromkeys(_catalog_display_name(source) for source in sources if source))


def _comparison_star_rows(
    metadata: dict[str, str],
    rows: list[dict[str, str]],
) -> list[tuple[str, str]]:
    if rows:
        row = rows[0]
        ids = [
            catalog_id
            for catalog_id in _split_semicolon_field(row, "comparison_catalog_ids")
            if catalog_id
        ]
        designations: list[str] = []
        for index, comp_id in enumerate(ids):
            designation = comp_id.strip()
            if designation:
                designations.append(designation)
        if designations:
            return [("Comp Stars", "<br/>".join(escape(item) for item in designations))]

    raw = _metadata_value(metadata, "COMP_STARS")
    entries = [item.strip() for item in raw.split(";") if item.strip()]
    designations = [
        " ".join(entry.split()[1:2]) or _metadata_catalog_designation(entry)
        for entry in entries
    ]
    return [("Comp Stars", "<br/>".join(escape(item) for item in designations))] if designations else []


def _check_star_row(
    metadata: dict[str, str],
    rows: list[dict[str, str]],
) -> tuple[str, str] | None:
    if rows:
        row = rows[0]
        designation = _catalog_designation(
            row.get("check_catalog_source", ""),
            row.get("check_catalog_id", "") or row.get("check_object_id", ""),
        )
        if designation:
            details = [
                row.get("check_catalog_id", "").strip()
                or row.get("check_object_id", "").strip()
            ]
            magnitude = _optional_float(row.get("check_catalog_mag"))
            if math.isfinite(magnitude):
                details.append(f"Mag={magnitude:.2f}")
            b_minus_v = _optional_float(row.get("check_catalog_b_minus_v"))
            g_minus_r = _optional_float(row.get("check_catalog_g_minus_r"))
            if math.isfinite(b_minus_v):
                details.append(f"B-V={b_minus_v:.2f}")
            elif math.isfinite(g_minus_r):
                details.append(f"G-R={g_minus_r:.2f}")
            return "Check Star", escape(", ".join(details))

    raw = _metadata_value(metadata, "CHECK_STAR")
    designation = _metadata_catalog_designation(raw)
    if not designation:
        return None
    details = [" ".join(raw.split()[1:2]) or designation]
    magnitude_match = re.search(r"\\bmag=([0-9.]+)", raw)
    if magnitude_match is not None:
        details.append(f"Mag={float(magnitude_match.group(1)):.2f}")
    return "Check Star", escape(", ".join(details))


def _outer_two_column_table(left: object, right: object) -> object:
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle

    outer = Table([[left, "", right]], colWidths=[84 * mm, 4 * mm, 84 * mm], hAlign="LEFT")
    outer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return outer


def report_observation_photometry_tables(
    metadata: dict[str, str],
    rows: list[dict[str, str]],
    styles: object,
) -> object:
    """Return side-by-side Details and Photometry tables."""

    from reportlab.lib.units import mm

    observation_rows = [
        (label, escape(value))
        for label, value in observation_details_rows(metadata, rows)
    ]
    photometry_rows = _comparison_star_rows(metadata, rows)
    catalog_sources = _catalog_sources_for_photometry(metadata, rows)
    if catalog_sources:
        photometry_rows.insert(
            0,
            ("Catalog" if len(catalog_sources) == 1 else "Catalogs", escape(", ".join(catalog_sources))),
        )
    check_star = _check_star_row(metadata, rows)
    if check_star is not None:
        photometry_rows.append(check_star)
    photometry_rows.append(
        (
            "Method",
            escape(_compact_photometry_method(_metadata_value(metadata, "PHOTOMETRY_METHOD"))),
        )
    )
    observation_table = _report_table(
        "Details",
        observation_rows,
        styles,
        (25 * mm, 57 * mm),
        section_break_after=frozenset({"HJD Range", "N° Obs"}),
    )
    photometry_table = _report_table("Photometry", photometry_rows, styles, (25 * mm, 57 * mm))
    return _outer_two_column_table(observation_table, photometry_table)


def require_lightcurve_sheet_metadata(metadata: dict[str, str]) -> None:
    """Raise if the result CSV is not in the current BAV header format."""

    missing = [
        key
        for key in LIGHTCURVE_SHEET_REQUIRED_METADATA
        if not _metadata_value(metadata, key)
    ]
    if not (
        _metadata_value(metadata, "OBJECT_VSX_URL")
        or _metadata_value(metadata, "OBJECT_VSX_OID")
    ):
        missing.append("OBJECT_VSX_URL/OBJECT_VSX_OID")
    if missing:
        raise RuntimeError(
            "Result CSV is missing required BAV metadata header field(s): "
            + ", ".join(missing)
        )


def hjd_values_from_metadata(jd_values: list[float], metadata: dict[str, str]) -> list[float]:
    """Return historical HJD_UTC values from JD_UTC and CSV metadata."""

    try:
        latitude = float(_metadata_value(metadata, "OBSERVER_LATITUDE"))
        longitude = float(_metadata_value(metadata, "OBSERVER_LONGITUDE"))
    except ValueError as exc:
        raise RuntimeError("Observer latitude/longitude in result CSV are not valid decimal degrees.") from exc

    ra = _metadata_value(metadata, "OBJECT_RA")
    dec = _metadata_value(metadata, "OBJECT_DEC")
    if not ra or not dec:
        raise RuntimeError("OBJECT_RA and OBJECT_DEC are required for HJD conversion.")

    try:
        import astropy.units as u
        from astropy.coordinates import EarthLocation, SkyCoord
        from astropy.time import Time

        location = EarthLocation(lat=latitude * u.deg, lon=longitude * u.deg, height=0 * u.m)
        target = SkyCoord(ra, dec, unit=(u.hourangle, u.deg), frame="icrs")
        time_utc = Time(jd_values, format="jd", scale="utc", location=location)
        light_travel_time = time_utc.light_travel_time(target, "heliocentric")
        hjd = time_utc.utc + light_travel_time
    except Exception as exc:
        raise RuntimeError(f"HJD conversion failed: {exc}") from exc

    return [float(value) for value in hjd.jd]


def _utc_from_jd_text(jd: float) -> str:
    try:
        from astropy.time import Time

        return Time(jd, format="jd", scale="utc").iso
    except Exception as exc:
        raise RuntimeError(f"UTC conversion failed: {exc}") from exc


def _hjd_from_jd_text(jd: float, metadata: dict[str, str]) -> str:
    hjd = hjd_values_from_metadata([jd], metadata)[0]
    return f"{hjd:.3f}"


def _fit_float(fit: dict[str, object], key: str) -> float | None:
    try:
        value = float(fit.get(key, ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _fit_overlay_points(
    rows: list[dict[str, str]],
    metadata: dict[str, str],
    fit: dict[str, object],
) -> tuple[list[float], list[float], float, float, float, float]:
    """Return HJD fit curve, vertex and selected range for the PDF plot."""

    range_min = _fit_float(fit, "range_min_jd")
    range_max = _fit_float(fit, "range_max_jd")
    vertex_jd = _fit_float(fit, "jd")
    vertex_mag = _fit_float(fit, "mag")
    fit_plot_jd_raw = fit.get("fit_plot_jd")
    fit_plot_mag_raw = fit.get("fit_plot_mag")
    if (
        range_min is None
        or range_max is None
        or vertex_jd is None
        or vertex_mag is None
        or not isinstance(fit_plot_jd_raw, (list, tuple))
        or not isinstance(fit_plot_mag_raw, (list, tuple))
        or len(fit_plot_jd_raw) != len(fit_plot_mag_raw)
        or len(fit_plot_jd_raw) < 2
    ):
        raise RuntimeError(
            "Extremum fit is missing exact PDF overlay data. "
            "Run the extremum fit again with the current script version."
        )

    try:
        fit_jd = [float(value) for value in fit_plot_jd_raw]
        fit_y = [float(value) for value in fit_plot_mag_raw]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Extremum fit plot points are invalid.") from exc
    fit_hjd = hjd_values_from_metadata(fit_jd, metadata)
    vertex_hjd = hjd_values_from_metadata([vertex_jd], metadata)[0]
    range_hjd = hjd_values_from_metadata([range_min, range_max], metadata)
    return (
        fit_hjd,
        fit_y,
        vertex_hjd,
        vertex_mag,
        min(range_hjd),
        max(range_hjd),
    )


def bav_pdf_comment_text(comment: str) -> str:
    """Return width-measured markup occupying exactly two PDF comment lines."""

    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth

    normalized = " ".join(str(comment or "").split())
    if len(normalized) > BAV_PDF_COMMENT_MAX_LENGTH:
        raise ValueError(
            f"BAV PDF comment must not exceed {BAV_PDF_COMMENT_MAX_LENGTH} characters."
        )
    if not normalized:
        return "-<br/>&nbsp;"
    usable_width = BAV_PDF_COMMENT_VALUE_WIDTH_MM * mm - 10
    if stringWidth(normalized, "Helvetica", 8.0) <= usable_width:
        return f"{escape(normalized)}<br/>&nbsp;"

    whitespace_positions = [
        index for index, char in enumerate(normalized) if char == " "
    ]
    if not whitespace_positions:
        raise ValueError("BAV PDF comment is too wide and has no word boundary for wrapping.")

    selected: tuple[float, str, str] | None = None
    for font_size in BAV_PDF_COMMENT_FONT_SIZES:
        candidates: list[tuple[float, float, str, str]] = []
        for split_at in whitespace_positions:
            first_line = normalized[:split_at].rstrip()
            second_line = normalized[split_at + 1 :].lstrip()
            first_width = stringWidth(first_line, "Helvetica", font_size)
            second_width = stringWidth(second_line, "Helvetica", font_size)
            if first_width <= usable_width and second_width <= usable_width:
                candidates.append(
                    (
                        abs(first_width - second_width),
                        max(first_width, second_width),
                        first_line,
                        second_line,
                    )
                )
        if candidates:
            _balance, _maximum, first_line, second_line = min(candidates)
            selected = (font_size, first_line, second_line)
            break
    if selected is None:
        raise ValueError("BAV PDF comment is too wide for two PDF lines.")

    font_size, first_line, second_line = selected

    def nonbreaking_markup(text: str) -> str:
        return escape(text).replace(" ", "&nbsp;")

    return (
        f'<font size="{font_size:g}">{nonbreaking_markup(first_line)}</font>'
        f'<br/><font size="{font_size:g}">'
        f"{nonbreaking_markup(second_line) or '&nbsp;'}</font>"
    )


def report_results_table(
    metadata: dict[str, str],
    fit: dict[str, object],
    styles: object,
    comment: str = "",
) -> object:
    """Return the Results table from the current extremum fit."""

    from reportlab.lib.units import mm

    fit_type = str(fit.get("type", "")).strip()
    jd = _fit_float(fit, "jd")
    jd_error = _fit_float(fit, "jd_error")
    mag = _fit_float(fit, "mag")
    if jd is None:
        raise RuntimeError("Extremum fit has no valid JD.")
    hjd_text = _hjd_from_jd_text(jd, metadata)
    if jd_error is not None:
        hjd_text += f" ± {jd_error:.3f}"
    rows = [
        ("Result Type", escape(fit_type)),
        ("HJD", escape(hjd_text)),
        ("UTC", escape(_utc_from_jd_text(jd))),
        ("Mag", "" if mag is None else escape(f"{mag:.4f}")),
        ("Comment", bav_pdf_comment_text(comment)),
    ]
    return _report_table("Results", rows, styles, (25 * mm, 57 * mm))


def _optional_float(value: str | None) -> float:
    if value is None:
        return float("nan")
    try:
        numeric = float(str(value).strip())
    except ValueError:
        return float("nan")
    return numeric if math.isfinite(numeric) else float("nan")


def result_row_is_valid(row: dict[str, str]) -> bool:
    """Return whether a result row may enter a scientific BAV output."""

    if str(row.get("valid", "1")).strip().casefold() in {"0", "false", "no"}:
        return False
    return str(row.get("quality_status", "")).strip().upper() != "INVALID"


def valid_result_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return only scientifically valid result rows."""

    return [row for row in rows if result_row_is_valid(row)]


def render_lightcurve_plot_png(
    result_csv: Path,
    extremum_fit: dict[str, object] | None = None,
    *,
    export_metadata: dict[str, str] | None = None,
) -> bytes:
    """Render the result rows with the metadata used by the current export."""

    csv_metadata, rows = read_result_curve_with_metadata(result_csv)
    metadata = dict(export_metadata) if export_metadata is not None else csv_metadata
    jd_values: list[float] = []
    mag_values: list[float] = []
    err_values: list[float] = []
    check_delta_values: list[float] = []
    check_calibrated_values: list[float] = []

    for row in valid_result_rows(rows):
        jd = _optional_float(row.get("jd"))
        mag = _optional_float(row.get("target_calibrated_mag"))
        if not math.isfinite(jd) or not math.isfinite(mag):
            continue
        jd_values.append(jd)
        mag_values.append(mag)
        err_values.append(_optional_float(row.get("target_calibrated_mag_error")))
        check_delta_values.append(_optional_float(row.get("check_delta_mag")))
        check_calibrated_values.append(_optional_float(row.get("check_calibrated_mag")))

    if not jd_values:
        raise RuntimeError(f"No plottable lightcurve rows found in {result_csv.name}.")
    hjd_values = hjd_values_from_metadata(jd_values, metadata)
    fit_overlay = (
        _fit_overlay_points(rows, metadata, extremum_fit)
        if isinstance(extremum_fit, dict)
        else None
    )

    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib.figure import Figure

    fig = Figure(figsize=(7.2, 4.4), dpi=180)
    target_axis, check_axis = fig.subplots(
        2,
        1,
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.08},
    )

    yerr = [value if math.isfinite(value) and value > 0 else 0.0 for value in err_values]
    target_axis.errorbar(
        hjd_values,
        mag_values,
        yerr=yerr,
        fmt="o",
        markersize=2.8,
        linewidth=0.6,
        capsize=1.5,
        color="black",
        ecolor="#9a9a9a",
    )
    target_axis.invert_yaxis()
    target_axis.set_ylabel("Mag")
    target_axis.grid(True, alpha=0.25)
    if fit_overlay is not None:
        fit_hjd, fit_y, vertex_hjd, vertex_mag, range_min_hjd, range_max_hjd = fit_overlay
        target_axis.axvspan(
            range_min_hjd,
            range_max_hjd,
            facecolor="#8f8f8f",
            alpha=0.035,
            edgecolor="none",
            linewidth=0,
            zorder=0,
        )
        for range_edge_hjd in (range_min_hjd, range_max_hjd):
            target_axis.axvline(
                range_edge_hjd,
                color="#8f8f8f",
                linewidth=0.35,
                linestyle=":",
                alpha=0.35,
                zorder=0.5,
            )
        target_axis.plot(fit_hjd, fit_y, color="#d55e00", linewidth=1.4)
        target_axis.plot(vertex_hjd, vertex_mag, "s", color="#d55e00", markersize=4.5)
        target_axis.axvline(vertex_hjd, color="#d55e00", linewidth=0.8, linestyle="--")

    finite_check_delta = [value for value in check_delta_values if math.isfinite(value)]
    if finite_check_delta:
        check_plot_values = [
            value if math.isfinite(value) else float("nan")
            for value in check_delta_values
        ]
        check_ylabel = "Check - cat"
    else:
        finite_check_calibrated = [
            value for value in check_calibrated_values if math.isfinite(value)
        ]
        if finite_check_calibrated:
            sorted_values = sorted(finite_check_calibrated)
            mid = len(sorted_values) // 2
            if len(sorted_values) % 2:
                median_check = sorted_values[mid]
            else:
                median_check = (sorted_values[mid - 1] + sorted_values[mid]) / 2.0
            check_plot_values = [
                value - median_check if math.isfinite(value) else float("nan")
                for value in check_calibrated_values
            ]
            check_ylabel = "Check rel. mag"
        else:
            check_plot_values = []
            check_ylabel = "Check"

    if check_plot_values:
        check_axis.axhline(0.0, color="#777777", linewidth=0.6)
        check_axis.plot(
            hjd_values,
            check_plot_values,
            "o",
            markersize=2.2,
            color="#1f77b4",
        )
        finite_check_plot_values = [
            value for value in check_plot_values if math.isfinite(value)
        ]
        if finite_check_plot_values:
            max_abs = max(abs(value) for value in finite_check_plot_values)
            limit = max(0.05, max_abs * 1.15)
            check_axis.set_ylim(-limit, limit)
    else:
        check_axis.text(
            0.5,
            0.5,
            "No check star data",
            ha="center",
            va="center",
            transform=check_axis.transAxes,
            fontsize=8,
        )

    check_axis.set_xlabel("HJD")
    check_axis.set_ylabel(check_ylabel)
    check_axis.grid(True, alpha=0.25)
    fig.subplots_adjust(left=0.14, right=0.98, top=0.96, bottom=0.12, hspace=0.08)

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=180, facecolor="white")
    return buffer.getvalue()


def validate_config(config: dict[str, str]) -> dict[str, str]:
    """Validate all BAV settings and return their normalized representation."""

    issues: list[str] = []
    bav_code = config.get("bav_code", "").strip().upper()
    if not bav_code:
        issues.append("BAV-Kürzel fehlt.")
    elif not re.fullmatch(r"[A-Z]+", bav_code):
        issues.append("BAV-Kürzel darf nur Buchstaben A–Z enthalten.")

    observer_name = config.get("observer_name", "").strip()
    if not observer_name:
        issues.append("Name des Beobachters fehlt.")

    aavso_code = config.get("aavso_code", "").strip().upper()
    if not aavso_code:
        issues.append("AAVSO-Kürzel fehlt.")
    elif not re.fullmatch(r"[A-Z0-9]{1,5}", aavso_code):
        issues.append(
            "AAVSO-Kürzel muss aus 1 bis 5 Zeichen (A–Z oder 0–9) bestehen."
        )

    site_name = config.get("site_name", "").strip()
    if not site_name:
        issues.append("Standort fehlt.")

    telescope = config.get("telescope", "").strip()
    if telescope not in TELESCOPE_CHOICES:
        issues.append("Ein gültiges Teleskop muss ausgewählt werden.")

    try:
        latitude, latitude_config = _dms_config_to_decimal(
            config,
            "latitude",
            "Breitengrad",
            "N",
            "S",
            LATITUDE_MAX_DEGREES,
        )
    except ValueError as exc:
        issues.append(str(exc))
        latitude = 0.0
        latitude_config = {}
    try:
        longitude, longitude_config = _dms_config_to_decimal(
            config,
            "longitude",
            "Längengrad",
            "O",
            "W",
            LONGITUDE_MAX_DEGREES,
        )
    except ValueError as exc:
        issues.append(str(exc))
        longitude = 0.0
        longitude_config = {}

    if issues:
        raise BavConfigValidationError(issues)

    clean_config = {
        "bav_code": bav_code,
        "observer_name": observer_name,
        "aavso_code": aavso_code,
        "site_name": site_name,
        "telescope": telescope,
    }
    clean_config.update(latitude_config)
    clean_config.update(longitude_config)
    clean_config["latitude"] = _format_decimal_degrees(latitude)
    clean_config["longitude"] = _format_decimal_degrees(longitude)
    return clean_config


def bav_config_problem_text(
    file_problem: str | None,
    issues: tuple[str, ...] | list[str],
) -> str:
    """Build the complete user-facing instruction for an invalid BAV config."""

    parts = ["Die BAV-Konfiguration ist noch nicht vollständig und korrekt."]
    if file_problem:
        parts.append(file_problem)
    if issues:
        parts.append("Fehlende oder ungültige Angaben:\n" + "\n".join(f"• {issue}" for issue in issues))
    parts.append(
        "Bitte die Angaben im Bereich „Konfiguration“ ergänzen und anschließend "
        "mit „Speichern“ sichern."
    )
    return "\n\n".join(parts)


def persisted_config_validation_issues(path: Path | None = None) -> tuple[str, ...]:
    """Return all field problems in an existing BAV config document."""

    path = path or config_path()
    if config_file_issue(path) is not None:
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    persisted = {
        key: str(data.get(key, "") if data.get(key, "") is not None else "")
        for key in DEFAULT_CONFIG
    }
    try:
        validate_config(persisted)
    except BavConfigValidationError as exc:
        return exc.issues
    return ()


def bav_export_metadata(
    metadata: dict[str, str],
    settings: dict[str, str] | None = None,
) -> dict[str, str]:
    """Overlay strictly validated current BAV settings for an actual export."""

    if settings is None:
        try:
            config = validate_config(load_config())
        except Exception:
            # Preserve the standalone helper contract for older result CSVs
            # that already contain complete observer metadata. The interactive
            # BAV action always passes its current settings explicitly.
            return dict(metadata)
    else:
        config = validate_config(settings)
    result = dict(metadata)
    result.update(
        {
            "OBSERVER_BAV": config["bav_code"],
            "OBSERVER_NAME": config["observer_name"],
            "OBSERVER_AAVSO": config["aavso_code"],
            "OBSERVER_SITE": config["site_name"],
            "OBSERVER_LATITUDE": config["latitude"],
            "OBSERVER_LONGITUDE": config["longitude"],
        }
    )
    return result


def ensure_reportlab_available(context: dict[str, object]) -> None:
    """Install/import ReportLab through the host app when PDF output needs it."""

    ensure_importable_module = context.get("ensure_importable_module")
    if callable(ensure_importable_module):
        ensure_importable_module("reportlab")
        return

    try:
        import reportlab  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "ReportLab is required for BAV PDF export. Run this plugin from "
            "SeePhot_Main.py in Siril so the package can be installed automatically."
        ) from exc


def ensure_astropy_available(context: dict[str, object]) -> None:
    """Install/import Astropy through the host app when HJD conversion needs it."""

    ensure_importable_module = context.get("ensure_importable_module")
    if callable(ensure_importable_module):
        ensure_importable_module("astropy")
        return

    try:
        import astropy  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Astropy is required for BAV HJD conversion. Run this plugin from "
            "SeePhot_Main.py in Siril so the package can be installed automatically."
        ) from exc


def _context_path(context: dict[str, object], key: str) -> Path | None:
    getter = context.get(key)
    if not callable(getter):
        return None
    value = getter()
    if value is None:
        return None
    return Path(value)


def _selected_target_name(context: dict[str, object]) -> str | None:
    target_getter = context.get("get_selected_target")
    selected_target = target_getter() if callable(target_getter) else None
    catalog_object = getattr(selected_target, "catalog_object", None)
    name = getattr(catalog_object, "name", "") if catalog_object is not None else ""
    return name.strip() or None


def _current_lightcurve(context: dict[str, object]) -> tuple[Path, str] | None:
    getter = context.get("get_current_lightcurve")
    value = getter() if callable(getter) else None
    if not isinstance(value, tuple) or len(value) != 2:
        return None
    csv_path, target_name = value
    return Path(csv_path), str(target_name).strip() or "Target"


def _current_extremum_fit(context: dict[str, object]) -> dict[str, object]:
    fit_getter = context.get("get_current_extremum_fit")
    extremum_fit = fit_getter() if callable(fit_getter) else None
    if not isinstance(extremum_fit, dict):
        raise RuntimeError("No current extremum fit available. Fit Maximum/Minimum before creating the BAV file.")
    return extremum_fit


def _current_output_directory(context: dict[str, object]) -> Path | None:
    current = _current_lightcurve(context)
    if current is not None:
        result_csv, _target_name = current
        return Path(_RESULT_CONTRACT.bav_output_directory(result_csv))
    results_dir = _context_path(context, "get_results_directory")
    return results_dir / "BAV" if results_dir is not None else None


def _current_result_csv(context: dict[str, object]) -> tuple[Path, str]:
    current = _current_lightcurve(context)
    if current is not None:
        result_csv, target_name = current
        if not result_csv.exists():
            raise RuntimeError(f"Current result CSV does not exist: {result_csv}")
        return result_csv, target_name

    raise RuntimeError(
        "No current result CSV available. Open an existing Light Curve or "
        "Single Measurement result in the Photometry tab first."
    )


def result_csv_kind(result_csv: Path) -> str:
    """Return the BAV-relevant result kind for a current result CSV."""

    try:
        _metadata, rows = read_result_curve_with_metadata(result_csv)
    except Exception:
        return RESULT_KIND_LIGHTCURVE
    for row in rows:
        if is_single_field_zp_row(row):
            return RESULT_KIND_SINGLE_FIELD_ZP
    return RESULT_KIND_LIGHTCURVE


def is_single_field_zp_row(row: dict[str, str]) -> bool:
    """Return true for old APASS-only and current auto-catalog Single Field-ZP rows."""

    method = str(row.get("calibration_method", "")).strip().lower().replace("-", "_")
    return method in SINGLE_FIELD_ZP_CALIBRATION_METHODS


def current_result_kind(context: dict[str, object]) -> str:
    """Return current BAV export kind, defaulting to series lightcurve."""

    result_csv, _target_name = _current_result_csv(context)
    return result_csv_kind(result_csv)


def constellation_abbreviation(metadata: dict[str, str]) -> str:
    """Return the IAU constellation abbreviation for the object coordinates."""

    ra = _metadata_value(metadata, "OBJECT_RA")
    dec = _metadata_value(metadata, "OBJECT_DEC")
    if not ra or not dec:
        raise RuntimeError("OBJECT_RA and OBJECT_DEC are required for BAV output filenames.")
    try:
        import astropy.units as u
        from astropy.coordinates import SkyCoord

        target = SkyCoord(ra, dec, unit=(u.hourangle, u.deg), frame="icrs")
        return str(target.get_constellation(short_name=True))
    except Exception as exc:
        raise RuntimeError(f"Could not determine constellation for BAV filename: {exc}") from exc


def short_jd_filename_text(metadata: dict[str, str], fit: dict[str, object]) -> str:
    """Return extremum HJD for BAV filenames without the leading 24."""

    jd = _fit_float(fit, "jd")
    if jd is None:
        raise RuntimeError("Extremum fit has no valid JD for BAV filename.")
    text = f"{hjd_values_from_metadata([jd], metadata)[0]:.3f}"
    return text[2:] if text.startswith("24") else text


def bav_output_prefix(metadata: dict[str, str], fit: dict[str, object]) -> str:
    """Return Sternbild_Sternname_JD_BAVKennung filename prefix."""

    raw_constellation = constellation_abbreviation(metadata)
    constellation = safe_filename_text(raw_constellation, "Const")
    object_name = bav_technical_object_name(_metadata_value(metadata, "OBJECT_NAME"), raw_constellation, "target")
    short_jd = safe_filename_text(short_jd_filename_text(metadata, fit), "JD")
    bav_code = safe_filename_text(_metadata_value(metadata, "OBSERVER_BAV").upper(), "BAV")
    return f"{constellation}_{object_name}_{short_jd}_{bav_code}"


def bav_observation_date_token(result_csv: Path) -> str:
    """Return the current observation date as YYYYMMDD for overwrite prompts."""

    return report_date_from_lightcurve(result_csv).replace("-", "")


def existing_bav_files_prompt(
    metadata: dict[str, str],
    observation_date: str,
    existing_files: tuple[Path, ...],
) -> str:
    """Return the confirmation text for replacing one observation's BAV files."""

    target_name = _metadata_value(metadata, "OBJECT_NAME") or "diesen Stern"
    file_list = "\n".join(f"• {path.name}" for path in existing_files)
    return (
        f"Für {target_name} gibt es für die Beobachtung {observation_date} "
        "und den aktuellen Extremwert bereits BAV-Dateien:\n\n"
        f"{file_list}\n\n"
        "Sollen diese Dateien gelöscht und neu erzeugt werden?"
    )


def bav_output_paths(result_csv: Path, metadata: dict[str, str], fit: dict[str, object]) -> tuple[Path, Path]:
    prefix = bav_output_prefix(metadata, fit)
    output_dir = Path(_RESULT_CONTRACT.bav_output_directory(result_csv))
    return output_dir / f"{prefix}.pdf", output_dir / f"{prefix}_MiniMax.txt"


def bav_single_magnitudes_path(result_csv: Path, metadata: dict[str, str], fit: dict[str, object]) -> Path:
    prefix = bav_output_prefix(metadata, fit)
    output_dir = Path(_RESULT_CONTRACT.bav_output_directory(result_csv))
    return output_dir / f"{prefix}_Report.txt"


def existing_bav_output_files(
    result_csv: Path,
    metadata: dict[str, str],
    fit: dict[str, object],
) -> tuple[Path, ...]:
    """Return existing files belonging to exactly the current extremum fit."""

    output_pdf, minimax_path = bav_output_paths(result_csv, metadata, fit)
    report_path = bav_single_magnitudes_path(result_csv, metadata, fit)
    return tuple(
        path
        for path in (output_pdf, minimax_path, report_path)
        if path.is_file() or path.is_symlink()
    )


def bav_single_measurement_path(result_csv: Path, metadata: dict[str, str]) -> Path:
    """Return the BAV output path for one Single Measurement result."""

    object_name = bav_filename_object_name(_metadata_value(metadata, "OBJECT_NAME"), result_csv.stem)
    output_dir = Path(_RESULT_CONTRACT.bav_output_directory(result_csv))
    return output_dir / f"{object_name}_Einzelhelligkeit.txt"


def ensure_valid_cwd(preferred_directory: Path) -> None:
    """Ensure imports that call os.getcwd() survive a removed Siril cwd."""

    try:
        os.getcwd()
        return
    except (FileNotFoundError, PermissionError):
        pass

    for candidate in (preferred_directory, Path.home()):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            os.chdir(candidate)
            return
        except Exception:
            continue


def create_lightcurve_sheet_pdf(
    context: dict[str, object],
    settings: dict[str, str] | None = None,
    comment: str = "",
) -> Path:
    """Create a minimal BAV lightcurve sheet PDF for the current light curve."""

    result_csv, target_name = _current_result_csv(context)
    ensure_valid_cwd(result_csv.parent)
    ensure_reportlab_available(context)
    ensure_astropy_available(context)

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

    metadata, rows = read_result_curve_with_metadata(result_csv)
    rows = valid_result_rows(rows)
    metadata = bav_export_metadata(metadata, settings)
    metadata["OBS_COUNT"] = str(len(rows))
    require_lightcurve_sheet_metadata(metadata)
    target_name = bav_filename_object_name(metadata["OBJECT_NAME"].strip())
    report_metadata = dict(metadata)
    report_metadata["OBJECT_NAME"] = target_name
    extremum_fit = _current_extremum_fit(context)
    output_pdf, _minimax_path = bav_output_paths(result_csv, metadata, extremum_fit)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(output_pdf),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    story = [
        Paragraph(lightcurve_sheet_title(target_name, result_csv), styles["Title"]),
        Spacer(1, 4 * mm),
    ]
    plot_png = render_lightcurve_plot_png(
        result_csv,
        extremum_fit,
        export_metadata=metadata,
    )
    plot_image = Image(BytesIO(plot_png))
    plot_image._restrictSize(180 * mm, 90 * mm)
    story.append(plot_image)
    story.append(Spacer(1, 5 * mm))
    story.append(report_info_tables(report_metadata, styles))
    story.append(Spacer(1, 5 * mm))
    story.append(report_observation_photometry_tables(report_metadata, rows, styles))
    story.append(Spacer(1, 5 * mm))
    story.append(report_results_table(report_metadata, extremum_fit, styles, comment))
    doc.build(story)
    return output_pdf


def create_minimax_file(
    context: dict[str, object],
    settings: dict[str, str] | None = None,
) -> Path:
    """Create the BAV MiniMax text file for the current extremum fit."""

    result_csv, _target_name = _current_result_csv(context)
    ensure_astropy_available(context)
    metadata, rows = read_result_curve_with_metadata(result_csv)
    rows = valid_result_rows(rows)
    metadata = bav_export_metadata(metadata, settings)
    metadata["OBS_COUNT"] = str(len(rows))
    require_lightcurve_sheet_metadata(metadata)
    extremum_fit = _current_extremum_fit(context)
    _output_pdf, minimax_path = bav_output_paths(result_csv, metadata, extremum_fit)
    minimax_path.parent.mkdir(parents=True, exist_ok=True)

    jd = _fit_float(extremum_fit, "jd")
    jd_error = _fit_float(extremum_fit, "jd_error")
    mag = _fit_float(extremum_fit, "mag")
    if jd is None:
        raise RuntimeError("Extremum fit has no valid JD for MiniMax file.")
    hjd = hjd_values_from_metadata([jd], metadata)[0]
    phase_raw = str(extremum_fit.get("type", "")).strip().lower()
    phase = "max" if phase_raw.startswith("max") else "min" if phase_raw.startswith("min") else phase_raw
    jd_error_text = "" if jd_error is None else f"{abs(jd_error):.6f}"
    mag_text = "" if mag is None else f"{mag:.4f}"
    raw_constellation = constellation_abbreviation(metadata)
    fields = [
        raw_constellation,
        bav_technical_object_name(_metadata_value(metadata, "OBJECT_NAME"), raw_constellation),
        phase,
        "",
        f"{hjd:.6f}",
        jd_error_text,
        "",
        "",
        mag_text,
        "C",
        _metadata_value(metadata, "TELESCOPE"),
        _metadata_value(metadata, "FILTER"),
        _metadata_value(metadata, "OBS_COUNT"),
        _metadata_value(metadata, "OBSERVER_BAV"),
        "",
        "",
    ]
    lines = [
        "#Type=BAVMiniMax",
        "#Delim=;",
        ";".join(fields),
    ]
    minimax_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return minimax_path


def create_single_magnitudes_file(
    context: dict[str, object],
    settings: dict[str, str] | None = None,
) -> Path:
    """Create the BAV Einzelhelligkeiten text file for all light-curve points."""

    result_csv, _target_name = _current_result_csv(context)
    ensure_astropy_available(context)
    metadata, rows = read_result_curve_with_metadata(result_csv)
    metadata = bav_export_metadata(metadata, settings)
    require_lightcurve_sheet_metadata(metadata)
    extremum_fit = _current_extremum_fit(context)
    output_path = bav_single_magnitudes_path(result_csv, metadata, extremum_fit)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    jd_values: list[float] = []
    mag_values: list[float] = []
    for row in valid_result_rows(rows):
        jd = _optional_float(row.get("jd"))
        mag = _optional_float(row.get("target_calibrated_mag"))
        if math.isfinite(jd) and math.isfinite(mag):
            jd_values.append(jd)
            mag_values.append(mag)

    if not jd_values:
        raise RuntimeError("No valid target magnitudes available for Einzelhelligkeiten.")

    hjd_values = hjd_values_from_metadata(jd_values, metadata)
    filter_text = _metadata_value(metadata, "FILTER")
    lines = [
        "#BAV-Report",
        f"#Rem = -{filter_text}",
    ]
    for hjd, mag in zip(hjd_values, mag_values, strict=True):
        hjd_text = f"{hjd:.3f}"
        if hjd_text.startswith("24"):
            hjd_text = hjd_text[2:]
        lines.append(f"{hjd_text} {mag:.3f}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def create_single_measurement_bav_file(
    context: dict[str, object],
    settings: dict[str, str] | None = None,
) -> Path:
    """Create the BAV single-measurement file."""

    result_csv, _target_name = _current_result_csv(context)
    if result_csv_kind(result_csv) != RESULT_KIND_SINGLE_FIELD_ZP:
        raise RuntimeError("Current result is not a Single Measurement Field-ZP CSV.")
    metadata, rows = read_result_curve_with_metadata(result_csv)
    data_rows = [
        row
        for row in rows
        if is_single_field_zp_row(row) and result_row_is_valid(row)
    ]
    if len(data_rows) != 1:
        raise RuntimeError(
            f"Expected exactly one Single Measurement row, found {len(data_rows)}."
        )
    row = data_rows[0]
    jd = _optional_float(row.get("jd"))
    mag = _optional_float(row.get("calibrated_mag"))
    mag_error = _optional_float(row.get("calibrated_mag_error"))
    if not math.isfinite(jd) or not math.isfinite(mag):
        raise RuntimeError("Single Measurement CSV has no valid JD/calibrated magnitude.")

    settings = settings or load_config()
    bav_code = settings.get("bav_code", "").strip().upper()
    if not re.fullmatch(r"[A-Z]+", bav_code):
        raise RuntimeError("BAV code is required for BAV Einzelhelligkeit export.")
    aavso_code = settings.get("aavso_code", "").strip().upper() or "na"
    if aavso_code != "na" and not re.fullmatch(r"[A-Z0-9]{1,5}", aavso_code):
        raise RuntimeError(
            "AAVSO-Kürzel muss aus 1 bis 5 Zeichen (A–Z oder 0–9) bestehen."
        )
    target_name = _metadata_value(metadata, "OBJECT_NAME") or row.get("target_id", "").strip()
    if not target_name:
        raise RuntimeError("Single Measurement CSV has no OBJECT_NAME or target_id.")
    target_name = bav_filename_object_name(target_name)

    check_id = row.get("check_catalog_id", "").strip()
    check_source = row.get("check_catalog_source", "").strip() or "APASS_DR10"
    kname = f"{check_source}:{check_id}" if check_id else "na"
    kmag = _optional_float(row.get("check_calibrated_mag"))
    filter_text = row.get("aavso_filter", "").strip() or "na"
    nref = row.get("field_reference_used", "").strip() or "na"
    zp_scatter = row.get("field_zero_point_scatter", "").strip() or "na"
    remark = f"FieldZP APASS_DR10 nref={nref} zp_scatter={zp_scatter}"
    remark = remark[:100]

    def field(value: object) -> str:
        text = str(value).strip()
        if not text:
            return "na"
        return text.replace("|", "/").replace("\n", " ").replace("\r", " ")

    def mag_text(value: float | None, digits: int = 3) -> str:
        if value is None or not math.isfinite(value):
            return "na"
        return f"{value:.{digits}f}"

    output_path = bav_single_measurement_path(result_csv, metadata)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "#TYPE=Extended",
        f"#BAVOBS={field(bav_code)}",
        "#DELIM=|",
        "#BAVCAT=",
        "#OBSTYPE=CCD",
        f"#OBSCODE={field(aavso_code)}",
        "#DATE=JD",
        f"#SOFTWARE={field(_metadata_value(metadata, 'SOFTWARE') or APP_NAME)}",
        "|".join(
            [
                field(target_name),
                f"{jd:.4f}",
                mag_text(mag),
                mag_text(mag_error),
                field(filter_text),
                "NO",
                "STD",
                "ENSEMBLE",
                "na",
                field(kname),
                mag_text(kmag),
                "na",
                "na",
                "na",
                field(remark),
            ]
        ),
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def create_bav_tab(context: dict[str, object]) -> object:
    """Create the optional BAV report tab widget."""

    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import (
        QApplication,
        QComboBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )

    tab = QWidget()
    layout = QVBoxLayout(tab)

    config = load_config()
    config_group = QGroupBox("Konfiguration")
    config_layout = QVBoxLayout(config_group)
    form_row = QHBoxLayout()
    left_form_layout = QFormLayout()
    left_form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    left_form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    right_form_layout = QFormLayout()
    right_form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    right_form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    compact_width = 190
    medium_width = 280
    limit_fields = sys.platform.startswith("win")

    bav_code_edit = QLineEdit()
    bav_code_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
    bav_code_edit.setPlaceholderText("BAV-Kuerzel")
    bav_code_edit.setText(config["bav_code"])
    if limit_fields:
        bav_code_edit.setFixedWidth(compact_width)
    left_form_layout.addRow("BAV-Kuerzel:", bav_code_edit)

    observer_name_edit = QLineEdit()
    observer_name_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
    observer_name_edit.setPlaceholderText("Name")
    observer_name_edit.setText(config["observer_name"])
    if limit_fields:
        observer_name_edit.setFixedWidth(medium_width)
    left_form_layout.addRow("Beobachter:", observer_name_edit)

    aavso_code_edit = QLineEdit()
    aavso_code_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
    aavso_code_edit.setPlaceholderText("AAVSO-Kuerzel")
    aavso_code_edit.setToolTip(
        "Erforderlich: 1 bis 5 Zeichen, nur A–Z oder 0–9."
    )
    aavso_code_edit.setText(config["aavso_code"])

    def uppercase_aavso_code_input(text: str) -> None:
        normalized = text.upper()
        if normalized == text:
            return
        cursor_position = aavso_code_edit.cursorPosition()
        aavso_code_edit.setText(normalized)
        aavso_code_edit.setCursorPosition(cursor_position)

    aavso_code_edit.textEdited.connect(uppercase_aavso_code_input)
    if limit_fields:
        aavso_code_edit.setFixedWidth(compact_width)
    left_form_layout.addRow("AAVSO-Kuerzel:", aavso_code_edit)

    site_name_edit = QLineEdit()
    site_name_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
    site_name_edit.setPlaceholderText("Standort")
    site_name_edit.setText(config["site_name"])
    if limit_fields:
        site_name_edit.setFixedWidth(medium_width)
    left_form_layout.addRow("Standort:", site_name_edit)

    def safe_config_number(
        key: str,
        *,
        decimal: bool,
        minimum: float,
        maximum: float,
    ) -> float:
        fallback = float(_normalized_decimal(DEFAULT_CONFIG[key]))
        try:
            value = float(_normalized_decimal(config[key]))
        except (KeyError, TypeError, ValueError):
            return fallback
        if not math.isfinite(value) or not minimum <= value <= maximum:
            return fallback
        if not decimal and not value.is_integer():
            return fallback
        return value

    def build_coordinate_row(
        prefix: str,
        hemispheres: tuple[str, str],
        max_degrees: int,
    ) -> tuple[QHBoxLayout, QComboBox, QSpinBox, QSpinBox, QDoubleSpinBox]:
        row = QHBoxLayout()
        hemisphere_combo = QComboBox()
        hemisphere_combo.addItems(hemispheres)
        hemisphere_combo.setCurrentText(config[f"{prefix}_hemisphere"])
        hemisphere_combo.setFixedWidth(56)

        degrees_spin = QSpinBox()
        degrees_spin.setRange(0, max_degrees)
        degrees_spin.setValue(
            int(
                safe_config_number(
                    f"{prefix}_degrees",
                    decimal=False,
                    minimum=0,
                    maximum=max_degrees,
                )
            )
        )
        degrees_spin.setSuffix("°")
        degrees_spin.setFixedWidth(70)

        minutes_spin = QSpinBox()
        minutes_spin.setRange(0, 59)
        minutes_spin.setValue(
            int(
                safe_config_number(
                    f"{prefix}_minutes",
                    decimal=False,
                    minimum=0,
                    maximum=59,
                )
            )
        )
        minutes_spin.setSuffix("'")
        minutes_spin.setFixedWidth(64)

        seconds_spin = QDoubleSpinBox()
        seconds_spin.setRange(0.0, 59.9)
        seconds_spin.setDecimals(1)
        seconds_spin.setSingleStep(0.1)
        seconds_spin.setValue(
            safe_config_number(
                f"{prefix}_seconds",
                decimal=True,
                minimum=0.0,
                maximum=59.9,
            )
        )
        seconds_spin.setSuffix('"')
        seconds_spin.setFixedWidth(82)

        def sync_limits() -> None:
            at_limit = degrees_spin.value() == max_degrees
            minutes_spin.setMaximum(0 if at_limit else 59)
            seconds_spin.setMaximum(0.0 if at_limit else 59.9)

        degrees_spin.valueChanged.connect(sync_limits)
        sync_limits()

        row.addWidget(hemisphere_combo)
        row.addWidget(degrees_spin)
        row.addWidget(minutes_spin)
        row.addWidget(seconds_spin)
        row.addStretch(1)
        return row, hemisphere_combo, degrees_spin, minutes_spin, seconds_spin

    (
        latitude_row,
        latitude_hemisphere_combo,
        latitude_degrees_spin,
        latitude_minutes_spin,
        latitude_seconds_spin,
    ) = build_coordinate_row("latitude", ("N", "S"), LATITUDE_MAX_DEGREES)
    right_form_layout.addRow("Breite:", latitude_row)

    (
        longitude_row,
        longitude_hemisphere_combo,
        longitude_degrees_spin,
        longitude_minutes_spin,
        longitude_seconds_spin,
    ) = build_coordinate_row("longitude", ("O", "W"), LONGITUDE_MAX_DEGREES)
    right_form_layout.addRow("Länge:", longitude_row)

    telescope_label = QLabel("none")
    telescope_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    right_form_layout.addRow("Teleskop:", telescope_label)
    form_row.addLayout(left_form_layout)
    form_row.addLayout(right_form_layout)
    form_row.addStretch(1)
    config_layout.addLayout(form_row)

    action_row = QHBoxLayout()
    save_button = QPushButton("Speichern")
    refresh_button = QPushButton("Änd. verwerfen")
    status_label = QLabel()
    status_label.setWordWrap(True)
    config_layout.addWidget(status_label)
    config_layout.addStretch(1)
    action_row.addStretch(1)
    action_row.addWidget(save_button)
    action_row.addWidget(refresh_button)
    config_layout.addLayout(action_row)
    layout.addWidget(config_group)

    export_group = QGroupBox("Export")
    export_layout = QVBoxLayout(export_group)
    report_action_row = QHBoxLayout()
    bav_files_button = QPushButton("BAV-Dateien erzeugen")
    bav_files_label = QLabel("Lichtkurvenblatt, MiniMax-Datei, Report")
    open_folder_button = QPushButton("Ordner öffnen")
    bav_web_button = QPushButton("BAV Web")
    report_action_row.addWidget(bav_files_button)
    report_action_row.addWidget(open_folder_button)
    report_action_row.addStretch(1)
    report_action_row.addWidget(bav_web_button)
    export_layout.addLayout(report_action_row)
    export_layout.addWidget(bav_files_label)
    layout.addWidget(export_group)

    layout.addStretch(1)

    def append_log(message: str) -> None:
        append_log_fn = context.get("append_log")
        if callable(append_log_fn):
            append_log_fn(message)

    last_output: dict[str, Path | None] = {"path": None}

    def update_export_description(result_kind: str) -> None:
        if result_kind == RESULT_KIND_SINGLE_FIELD_ZP:
            bav_files_label.setText("Einzelhelligkeit-Datei")
        else:
            bav_files_label.setText("Lichtkurvenblatt, MiniMax-Datei, Report")

    def current_config() -> dict[str, str]:
        return {
            "bav_code": bav_code_edit.text().strip().upper(),
            "observer_name": observer_name_edit.text().strip(),
            "aavso_code": aavso_code_edit.text().strip().upper(),
            "site_name": site_name_edit.text().strip(),
            "latitude_hemisphere": latitude_hemisphere_combo.currentText().strip(),
            "latitude_degrees": str(latitude_degrees_spin.value()),
            "latitude_minutes": str(latitude_minutes_spin.value()),
            "latitude_seconds": _format_seconds_config(latitude_seconds_spin.value()),
            "longitude_hemisphere": longitude_hemisphere_combo.currentText().strip(),
            "longitude_degrees": str(longitude_degrees_spin.value()),
            "longitude_minutes": str(longitude_minutes_spin.value()),
            "longitude_seconds": _format_seconds_config(longitude_seconds_spin.value()),
            # Retain the legacy persisted value only as an instrument fallback
            # for old/malformed FITS files. It is deliberately not editable:
            # actual BAV outputs use TELESCOPE from the current result CSV.
            "telescope": config["telescope"],
        }

    try:
        initial_saved_settings: dict[str, str] | None = validate_config(config)
    except BavConfigValidationError:
        initial_saved_settings = None
    saved_settings: dict[str, dict[str, str] | None] = {
        "value": initial_saved_settings,
    }
    config_warning_state: dict[str, str | None] = {"message": None}

    def checked_config(
        *,
        require_saved_file: bool,
    ) -> tuple[dict[str, str] | None, str | None]:
        file_problem = config_file_issue() if require_saved_file else None
        persisted_issues = (
            persisted_config_validation_issues()
            if require_saved_file and file_problem is None
            else ()
        )
        try:
            settings = validate_config(current_config())
            current_issues: tuple[str, ...] = ()
        except BavConfigValidationError as exc:
            settings = None
            current_issues = exc.issues
        issues = tuple(dict.fromkeys((*persisted_issues, *current_issues)))
        if file_problem or issues:
            return None, bav_config_problem_text(file_problem, issues)
        return settings, None

    def show_config_problem(message: str, *, modal: bool) -> None:
        status_label.setText(message)
        if config_warning_state["message"] != message:
            append_log("WARNING: BAV-Konfiguration unvollständig: " + message.replace("\n", " "))
            config_warning_state["message"] = message
        if modal:
            QMessageBox.warning(tab, "BAV-Konfiguration unvollständig", message)

    def save_settings() -> None:
        settings, problem = checked_config(require_saved_file=False)
        if settings is None:
            assert problem is not None
            show_config_problem(problem, modal=True)
            return

        config_file = save_config(settings)
        saved_settings["value"] = settings
        bav_code_edit.setText(settings["bav_code"])
        aavso_code_edit.setText(settings["aavso_code"])
        update_main_observer_code = context.get(
            "set_aavso_observer_code_from_bav"
        )
        if callable(update_main_observer_code):
            update_main_observer_code(settings["aavso_code"])
        status_label.setText(f"Gespeichert: {config_file.name}")
        append_log(f"BAV settings saved: {config_file}")
        refresh()

    def refresh() -> None:
        source_dir = _context_path(context, "get_source_directory")
        results_dir = _context_path(context, "get_results_directory")
        target_getter = context.get("get_selected_target")
        selected_target = target_getter() if callable(target_getter) else None
        target_name = getattr(selected_target, "name", None) or getattr(selected_target, "identifier", None) or "-"
        current_result = _current_lightcurve(context)
        result_csv = current_result[0] if current_result is not None else None
        telescope_label.setText(result_telescope_display_text(result_csv))
        settings = current_config()
        append_log(
            "BAV tab refreshed: "
            f"config={config_path()}, "
            f"code={settings['bav_code'] or '-'}, "
            f"observer={settings['observer_name'] or '-'}, "
            f"aavso={settings['aavso_code'] or '-'}, "
            f"site={settings['site_name'] or '-'}, "
            f"lat={settings['latitude_hemisphere']} {settings['latitude_degrees']}° "
            f"{settings['latitude_minutes']}' {settings['latitude_seconds']}\" , "
            f"lon={settings['longitude_hemisphere']} {settings['longitude_degrees']}° "
            f"{settings['longitude_minutes']}' {settings['longitude_seconds']}\" , "
            f"telescope={telescope_label.text()}, "
            f"source={source_dir or '-'}, "
            f"results={results_dir or '-'}, "
            f"target={target_name}"
        )
        try:
            kind = current_result_kind(context)
        except Exception:
            mode_getter = context.get("get_photometry_mode")
            mode = mode_getter() if callable(mode_getter) else ""
            kind = RESULT_KIND_SINGLE_FIELD_ZP if mode == "single_measurement" else RESULT_KIND_LIGHTCURVE
        update_export_description(kind)
        _settings, problem = checked_config(require_saved_file=True)
        if problem is not None:
            show_config_problem(problem, modal=False)
        else:
            config_warning_state["message"] = None
            status_label.setText(f"BAV-Konfiguration vollständig: {config_path().name}")
        refresh_action_availability()

    def bav_export_available() -> bool:
        """Return whether the BAV export can start without a conflicting state."""

        get_action_availability = context.get("get_action_availability")
        if callable(get_action_availability):
            try:
                if not bool(get_action_availability().get("bav_export_base")):
                    return False
            except Exception as exc:
                append_log(f"WARNING: Could not read BAV export availability: {exc}")
                return False
        else:
            is_busy = context.get("is_busy")
            if callable(is_busy) and bool(is_busy()):
                return False

        settings, problem = checked_config(require_saved_file=True)
        if problem is not None or settings != saved_settings["value"]:
            return False
        try:
            kind = current_result_kind(context)
            if kind != RESULT_KIND_SINGLE_FIELD_ZP:
                _current_extremum_fit(context)
        except Exception:
            return False
        return True

    def refresh_action_availability() -> None:
        """Apply the shared app state plus BAV-specific export prerequisites."""

        bav_files_button.setEnabled(bav_export_available())
        output_path = last_output["path"]
        folder = output_path.parent if output_path is not None else _current_output_directory(context)
        open_folder_button.setEnabled(folder is not None and folder.is_dir())

    def prepare_bav_output_directory(settings: dict[str, str]) -> bool:
        folder = _current_output_directory(context)
        if folder is None:
            status_label.setText("Kein Ordner verfuegbar.")
            append_log("WARNING: No BAV output folder available.")
            return False
        try:
            result_csv, _target_name = _current_result_csv(context)
            metadata, _rows = read_result_curve_with_metadata(result_csv)
            metadata = bav_export_metadata(metadata, settings)
            fit = _current_extremum_fit(context)
            bav_output_paths(result_csv, metadata, fit)
            bav_single_magnitudes_path(result_csv, metadata, fit)
            existing_files = existing_bav_output_files(result_csv, metadata, fit)
            observation_date = bav_observation_date_token(result_csv)
        except Exception as exc:
            status_label.setText("BAV-Zieldateien konnten nicht bestimmt werden.")
            append_log(f"WARNING: Could not determine BAV output files: {exc}")
            return False
        if existing_files:
            delete_box = QMessageBox(tab)
            delete_box.setWindowTitle("BAV")
            delete_box.setText(
                existing_bav_files_prompt(
                    metadata,
                    observation_date,
                    existing_files,
                )
            )
            ok_button = delete_box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
            delete_box.addButton("Abbruch", QMessageBox.ButtonRole.RejectRole)
            delete_box.setDefaultButton(ok_button)
            delete_box.exec()
            if delete_box.clickedButton() is not ok_button:
                append_log("BAV output creation aborted by user.")
                return False
            for path in existing_files:
                try:
                    path.unlink()
                except Exception as exc:
                    append_log(f"WARNING: Could not delete BAV file {path}: {exc}")
                    status_label.setText("BAV-Datei konnte nicht geloescht werden.")
                    return False
            append_log(f"Deleted {len(existing_files)} existing BAV file(s) for current extremum fit from {folder}.")
        folder.mkdir(parents=True, exist_ok=True)
        return True

    def validate_bav_output_context(action_title: str) -> str | None:
        try:
            kind = current_result_kind(context)
            if kind != RESULT_KIND_SINGLE_FIELD_ZP:
                _current_extremum_fit(context)
        except Exception as exc:
            status_label.setText(f"{action_title} fehlgeschlagen.")
            append_log(f"WARNING: BAV {action_title} not created: {exc}")
            QMessageBox.critical(
                tab,
                action_title,
                f"{action_title} fehlgeschlagen.\n\n{type(exc).__name__}: {exc}",
            )
            return None
        return kind

    def log_bav_filename_name_normalization() -> None:
        try:
            result_csv, _target_name = _current_result_csv(context)
            metadata, _rows = read_result_curve_with_metadata(result_csv)
            object_name = _metadata_value(metadata, "OBJECT_NAME")
        except Exception:
            return
        normalized_name = bav_filename_object_name(object_name, "target")
        if object_name and "_" in object_name and normalized_name != object_name:
            append_log(
                "WARNING: BAV object name normalized for filename and BAV output content: "
                f"{object_name!r} -> {normalized_name!r} (underscore replaced by space). "
                "This BAV rule needs further clarification."
            )

    def bav_files_action(settings: dict[str, str]) -> None:
        result_kind = validate_bav_output_context("BAV-Dateien")
        if result_kind is None:
            return
        update_export_description(result_kind)
        pdf_comment = ""
        if result_kind != RESULT_KIND_SINGLE_FIELD_ZP:
            comment_question = QMessageBox(tab)
            comment_question.setWindowTitle("BAV-Kommentar")
            comment_question.setText("Kommentar zum Lichtkurvenblatt hinzufügen?")
            yes_button = comment_question.addButton("Ja", QMessageBox.ButtonRole.YesRole)
            no_button = comment_question.addButton("Nein", QMessageBox.ButtonRole.NoRole)
            comment_question.setDefaultButton(no_button)
            comment_question.exec()
            if comment_question.clickedButton() is yes_button:
                comment_dialog = QInputDialog(tab)
                comment_dialog.setWindowTitle("BAV-Kommentar")
                comment_dialog.setLabelText(
                    f"Kommentar eingeben (maximal {BAV_PDF_COMMENT_MAX_LENGTH} Zeichen):"
                )
                comment_dialog.setInputMode(QInputDialog.InputMode.TextInput)
                comment_dialog.setOkButtonText("OK")
                comment_dialog.setCancelButtonText("Abbrechen")
                comment_edit = comment_dialog.findChild(QLineEdit)
                if comment_edit is not None:
                    comment_edit.setMaxLength(BAV_PDF_COMMENT_MAX_LENGTH)

                def enforce_comment_limit(text: str) -> None:
                    if len(text) > BAV_PDF_COMMENT_MAX_LENGTH:
                        comment_dialog.setTextValue(text[:BAV_PDF_COMMENT_MAX_LENGTH])

                comment_dialog.textValueChanged.connect(enforce_comment_limit)
                if not comment_dialog.exec():
                    append_log("BAV output creation aborted in comment dialog.")
                    return
                pdf_comment = comment_dialog.textValue().strip()
        if (
            result_kind != RESULT_KIND_SINGLE_FIELD_ZP
            and not prepare_bav_output_directory(settings)
        ):
            return
        set_export_process_status = context.get("set_export_process_status")
        if callable(set_export_process_status):
            set_export_process_status("creating")
        created_paths: list[Path] = []
        try:
            log_bav_filename_name_normalization()
            if result_kind == RESULT_KIND_SINGLE_FIELD_ZP:
                output_report = create_single_measurement_bav_file(context, settings)
                created_paths.append(output_report)
                append_log(f"BAV Einzelhelligkeit file created: {output_report}")
            else:
                output_pdf = create_lightcurve_sheet_pdf(context, settings, pdf_comment)
                created_paths.append(output_pdf)
                append_log(f"BAV Lichtkurvenblatt PDF created: {output_pdf}")
                output_minimax = create_minimax_file(context, settings)
                created_paths.append(output_minimax)
                append_log(f"BAV MiniMax file created: {output_minimax}")
                output_report = create_single_magnitudes_file(context, settings)
                created_paths.append(output_report)
                append_log(f"BAV Einzelhelligkeiten file created: {output_report}")
        except Exception as exc:
            if callable(set_export_process_status):
                set_export_process_status("failed")
            status_label.setText("BAV-Dateien fehlgeschlagen.")
            current = _current_lightcurve(context)
            if current is None:
                append_log("WARNING: BAV current lightcurve: none")
            else:
                csv_path, target_name = current
                append_log(
                    "WARNING: BAV current lightcurve: "
                    f"csv={csv_path}, exists={csv_path.exists()}, target={target_name}"
                )
            append_log(f"WARNING: BAV file creation failed: {type(exc).__name__}: {exc}")
            for line in traceback.format_exc().rstrip().splitlines():
                append_log(f"BAV traceback: {line}")
            QMessageBox.critical(
                tab,
                "BAV-Dateien",
                "BAV-Dateien fehlgeschlagen.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                "Weitere Details stehen im SeePhot-Log.",
            )
            return
        last_output["path"] = created_paths[-1] if created_paths else None
        refresh_action_availability()
        status_label.setText("BAV-Datei erzeugt." if result_kind == RESULT_KIND_SINGLE_FIELD_ZP else "BAV-Dateien erzeugt.")
        mark_export_status = context.get("mark_export_status")
        if callable(mark_export_status):
            mark_export_status("BAV")

    def run_bav_files_action() -> None:
        settings, problem = checked_config(require_saved_file=True)
        if settings is None:
            assert problem is not None
            show_config_problem(problem, modal=True)
            return
        run_busy_action = context.get("run_busy_action")
        if callable(run_busy_action):
            run_busy_action(
                "EXPORT_RUNNING",
                "BAV-Dateien werden erzeugt.",
                lambda: bav_files_action(settings),
                [bav_files_button],
            )
            return
        bav_files_action(settings)

    def open_output_folder() -> None:
        output_path = last_output["path"]
        folder = output_path.parent if output_path is not None else _current_output_directory(context)
        if folder is None:
            status_label.setText("Kein BAV-Ausgabeordner verfügbar.")
            append_log("WARNING: No BAV output folder available.")
            QMessageBox.warning(
                tab,
                "BAV Export",
                "Bitte zuerst ein Ergebnis erzeugen oder öffnen.",
            )
            return
        if not folder.is_dir():
            status_label.setText("Kein BAV-Ausgabeordner gefunden.")
            append_log(f"WARNING: BAV output folder not found: {folder}")
            QMessageBox.warning(
                tab,
                "BAV Export",
                "Für das aktuelle Ergebnis wurden noch keine BAV-Dateien erzeugt.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            status_label.setText("Ordner konnte nicht geöffnet werden.")
            append_log(f"WARNING: Could not open BAV output folder: {folder}")
            return
        status_label.setText(f"Ordner geöffnet: {folder.name}")
        append_log(f"BAV output folder opened: {folder}")

    def open_bav_website() -> None:
        output_path = last_output["path"]
        folder = output_path.parent if output_path is not None else _current_output_directory(context)
        if folder is None:
            append_log("WARNING: No BAV path available for clipboard.")
        else:
            try:
                QApplication.clipboard().setText(os.fspath(folder))
            except Exception as exc:
                append_log(f"WARNING: Could not copy BAV path to clipboard: {exc}")
            else:
                append_log("BAV path copied to clipboard.")
        url = QUrl("https://www.bav-astro.eu/")
        if not QDesktopServices.openUrl(url):
            status_label.setText("BAV-Webseite konnte nicht geöffnet werden.")
            append_log(f"WARNING: Could not open BAV website: {url.toString()}")
            return
        append_log(f"BAV website opened: {url.toString()}")

    def reset_plugin_view() -> bool:
        last_output["path"] = None
        telescope_label.setText("none")
        status_label.setText("")
        refresh_action_availability()
        return True

    save_button.clicked.connect(save_settings)
    refresh_button.clicked.connect(refresh)
    bav_files_button.clicked.connect(run_bav_files_action)
    open_folder_button.clicked.connect(open_output_folder)
    bav_web_button.clicked.connect(open_bav_website)
    for edit in (bav_code_edit, observer_name_edit, aavso_code_edit, site_name_edit):
        edit.textChanged.connect(refresh_action_availability)
    for combo in (latitude_hemisphere_combo, longitude_hemisphere_combo):
        combo.currentTextChanged.connect(refresh_action_availability)
    for spin in (
        latitude_degrees_spin,
        latitude_minutes_spin,
        latitude_seconds_spin,
        longitude_degrees_spin,
        longitude_minutes_spin,
        longitude_seconds_spin,
    ):
        spin.valueChanged.connect(refresh_action_availability)
    setattr(tab, "reset_plugin_view", reset_plugin_view)
    setattr(tab, "refresh_plugin_view", refresh)
    setattr(tab, "refresh_action_availability", refresh_action_availability)
    refresh()
    return tab
