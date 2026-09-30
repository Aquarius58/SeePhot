from __future__ import annotations

# SeePhot
# Author: Thomas Rudolph (Aquarius58)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This script is intended to run inside Siril from the Python scripts menu.
# It uses Siril's embedded Python environment and sirilpy helper functions.

import sys
import csv
import hashlib
import html
import importlib.util
import math
import os
import re
import subprocess
import shutil
import stat
import time
import traceback
import tempfile
import urllib.parse
import urllib.request
import warnings
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

# The result contract has no Siril or Qt dependency and supplies the shared
# instrument definitions needed by Main before optional packages are prepared.
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))
import sp_mod_results as lightcurve_results

warnings.filterwarnings(
    "ignore",
    message=r"XDG_CACHE_HOME is set to .*but the default location.*already exists.*",
)

import sirilpy as s  # noqa: E402


SCRIPT_VERSION = "0.8.60"
SIRILPY_REQUIRES = ">=1.0.13"
APP_DISPLAY_NAME = "SeePhot"
SOFTWARE_NAME = f"{APP_DISPLAY_NAME} {SCRIPT_VERSION}"


# ---------------------------------------------------------------------------
# Stage 1 configuration
# ---------------------------------------------------------------------------
# These constants are intentionally kept near the top of the monolithic Siril
# script. They are developer/operator defaults, not yet user-facing settings.

# GUI window defaults.
WINDOW_TITLE = f"{APP_DISPLAY_NAME} {SCRIPT_VERSION}"
MAIN_WINDOW_OBJECT_NAME = "seephot_seestar_lightcurve_main_window"
SINGLE_INSTANCE_LOCK_PATH = Path(tempfile.gettempdir()) / "seephot_seestar.lock"
SIRIL_CONNECT_ATTEMPTS = 6
SIRIL_CONNECT_RETRY_DELAY_SECONDS = 0.2
WINDOW_WIDTH = 860
WINDOW_HEIGHT = 700
MAIN_SPLITTER_START_SIZES = (420, 210)
COMPACT_COMBO_WIDTH = 190
SHORT_EDIT_WIDTH = 95
THEME_COLORS = {
    "window": "#202124",
    "base": "#17191d",
    "panel": "#24272d",
    "status": "#111827",
    "border": "#3c4048",
    "text": "#eceff4",
    "muted_text": "#9aa3b2",
    "title_text": "#d8dee9",
    "accent": "#2f6fed",
    "accent_hover": "#3b7cff",
    "accent_pressed": "#255bc7",
    "status_accent": "#4f8cff",
    "warning": "#d99a28",
}

# Input defaults.
# Keep empty for normal use so a restarted app cannot accidentally plot stale results.
DEFAULT_FITS_DIRECTORY: Path | None = None

# Developer diagnostic switch. When enabled, a rejected manual Light Curve run
# may offer a separate, non-exportable curve for a modeled contaminated target.
# This never changes the normal measurement or quality decision and is ignored
# by Batch mode.
ENABLE_CONTAMINATED_TARGET_DIAGNOSTIC_CURVE = False
CONTAMINATED_TARGET_DIAGNOSTIC_PURPOSE = "REJECTED_TARGET_CONTAMINATION_DIAGNOSTIC"

FITS_SUFFIXES = {".fit", ".fits"}
MODE_LIGHTCURVE = "lightcurve"
MODE_SINGLE_MEASUREMENT = "single_measurement"
EXTREMUM_MODEL_ROBUST_GCV_SPLINE = "robust_gcv_spline"
EXTREMUM_MODEL_AUTOMATIC = "automatic"
EXTREMUM_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Robust cubic spline (recommended)", EXTREMUM_MODEL_ROBUST_GCV_SPLINE),
    ("Parabola", "parabola"),
    ("Asymptotic parabola", "asymptotic_parabola"),
    ("Parabolic spline", "parabolic_spline"),
    ("Automatic model comparison (classic)", EXTREMUM_MODEL_AUTOMATIC),
)
SERIES_MODE_VSX_TYPE_SUGGESTION = "EA*,EB*,EW*,RR*,HADS*"
SINGLE_MODE_VSX_TYPE_SUGGESTION = (
    "M,RVA*,RVB*,CWA*,DCEP*,N,NA*,NB*,NC*,NR*"
)
VSX_FILTER_PRESETS: tuple[tuple[str, str], ...] = (
    ("No filter", ""),
    ("Short period", SERIES_MODE_VSX_TYPE_SUGGESTION),
    ("Long period", SINGLE_MODE_VSX_TYPE_SUGGESTION),
)
BUSY_IDLE = "IDLE"
QUALITY_STATUS_OK = "OK"
QUALITY_STATUS_WARNING = "WARNING"
QUALITY_STATUS_INVALID = "INVALID"

# Siril sequence and output layout.
# The selected FITS directory remains read-only. Siril work files are written
# to ../siril_lightcurve_tmp/work, persistent outputs to ../results/<input-directory-name>.
PLATE_SOLVE_SEQUENCE_NAME = "seestar_lightcurve_ps"
WORK_SEQUENCE_NAME = f"{PLATE_SOLVE_SEQUENCE_NAME}_"
LEGACY_REGISTERED_SEQUENCE_NAME = f"r_{PLATE_SOLVE_SEQUENCE_NAME}_"
TMP_DIRECTORY_NAME = "siril_lightcurve_tmp"
RESULTS_DIRECTORY_NAME = "results"
DIAGNOSTICS_DIRECTORY_NAME = "diagnostics"
TEMP_CLEANUP_RETRY_DELAYS_SECONDS = (0.05, 0.1, 0.2, 0.4, 0.8)
DEFAULT_RESULT_TELESCOPE = lightcurve_results.DEFAULT_RESULT_TELESCOPE
RESULT_TELESCOPE_SPECS = lightcurve_results.RESULT_TELESCOPE_SPECS

# VSX target search defaults.
# Limit magnitude is passed to Siril's VSX conesearch.
DEFAULT_VSX_LIMIT_MAG = 16.0
VSX_QUERY_ATTEMPTS = 2
VSX_QUERY_RETRY_DELAY_SECONDS = 1.0

# APASS comparison-star defaults.
# dVmag filters comparison stars by magnitude distance from the selected
# target. dB-V and emag are retained as Stage 1 parameters for later filtering.
DEFAULT_COMP_DVMAG = 3.0
DEFAULT_COMP_DBV = 0.5
DEFAULT_COMP_EMAG = 0.15
DEFAULT_COMP_COUNT = 8
COMP_SELECTION_PREFILTER_LIMIT = 160
COMP_SELECTION_POOL_LIMIT = 40
COMP_SELECTION_SAMPLE_FRAMES = 15
COMP_SELECTION_MIN_VALID_RATE = 0.75
COMP_SELECTION_MAX_ZERO_POINT_OFFSET = 0.75
COMP_SELECTION_MAX_ROBUST_SCATTER = 0.30
CHECK_SELECTION_MIN_VALID_RATE = 0.75
CHECK_SELECTION_MAX_ABS_MEDIAN_DELTA = 0.12
COMP_SELECTION_RULE_ID = "compcheck-a-v5"
COMP_SELECTION_RULE_STRUCTURE_ID = "photometric-vetting-check-delta-gated-v1"
SERIES_COMP_VISIBILITY_MARGIN_FRACTION = 0.08
SERIES_COMP_ADAPTIVE_VISIBILITY_FRACTIONS = (0.98, 0.95, 0.90)
COMP_COLOR_BV_PREFERRED_MIN = 0.20
COMP_COLOR_BV_PREFERRED_MAX = 1.10
COMP_COLOR_BV_SOFT_MIN = -0.10
COMP_COLOR_BV_SOFT_MAX = 1.30
COMP_COLOR_GR_PREFERRED_MIN = -0.10
COMP_COLOR_GR_PREFERRED_MAX = 1.10
COMP_COLOR_GR_SOFT_MIN = -0.30
COMP_COLOR_GR_SOFT_MAX = 1.30
COMP_SCORE_COLOR_WEIGHT = 0.05
COMP_SCORE_EXTREME_COLOR_PENALTY = 2.5
CHECK_SCORE_COLOR_WEIGHT = 0.75
CHECK_SCORE_EXTREME_COLOR_PENALTY = 10.0
COMP_SCORE_ZP_OFFSET_WEIGHT = 7.0
COMP_SCORE_ROBUST_SCATTER_WEIGHT = 12.0
COMP_SCORE_INST_ERROR_WEIGHT = 5.0
COMP_SCORE_INVALID_RATE_WEIGHT = 4.0
COMP_SCORE_LOW_SNR_WEIGHT = 2.0
COMP_SCORE_LOW_SNR_REFERENCE = 25.0
COMP_SCORE_TARGET_MAG_DISTANCE_WEIGHT = 0.05
COMP_SCORE_TARGET_DISTANCE_WEIGHT = 0.05
COMP_SCORE_LOW_NOBS_PENALTY = 0.25
COMP_SCORE_CATALOG_ERROR_WEIGHT = 1.5
COMP_SCORE_CATALOG_ERROR_CAP = 0.30
COMP_SCORE_FALLBACK_MAG_DISTANCE = 3.0
COMP_SCORE_FALLBACK_INST_ERROR = 0.20
CHECK_SCORE_DELTA_WEIGHT = 3.0
CHECK_SCORE_ROBUST_SCATTER_WEIGHT = 10.0
CHECK_SCORE_INST_ERROR_WEIGHT = 4.0
CHECK_SCORE_INVALID_RATE_WEIGHT = 5.0
CHECK_SCORE_LOW_SNR_WEIGHT = 2.0
CHECK_SCORE_LOW_SNR_REFERENCE = 25.0
CHECK_SCORE_CATALOG_ERROR_WEIGHT = 1.0
CHECK_SCORE_LOW_NOBS_PENALTY = 0.20
CHECK_SCORE_CATALOG_ERROR_CAP = 0.30
CHECK_SCORE_FALLBACK_INST_ERROR = 0.20
SERIES_RINGSET_APERTURE_FWHM_FACTORS = (1.2, 1.4, 1.6, 1.8, 2.1)
SERIES_RINGSET_ANNULUS_INNER_FWHM_FACTORS = (2.4, 2.8, 3.2, 3.6)
SERIES_RINGSET_ANNULUS_WIDTH_FWHM_FACTORS = (1.2, 1.6, 2.0, 2.6)
SINGLE_FIELD_ZP_METHOD_ID = "FIELD_ZERO_POINT_AUTO_CATALOG_V1"
CURRENT_RESULT_METADATA_VERSION = 1
FIELD_FOOTPRINT_ORDER = "x0_y0;xmax_y0;xmax_ymax;x0_ymax"
SINGLE_FIELD_ZP_LEGACY_METHOD_ID = "FIELD_ZERO_POINT_APASS_DR10_V1"
SINGLE_FIELD_ZP_METHOD_KEYS = {
    SINGLE_FIELD_ZP_METHOD_ID.lower(),
    SINGLE_FIELD_ZP_METHOD_ID.lower().replace("_", "-"),
    SINGLE_FIELD_ZP_LEGACY_METHOD_ID.lower(),
    SINGLE_FIELD_ZP_LEGACY_METHOD_ID.lower().replace("_", "-"),
}
SINGLE_FIELD_ZP_MIN_USED_REFERENCES = 5
SINGLE_FIELD_ZP_SIGMA_CLIP = 3.0
SINGLE_FIELD_ZP_MAX_ITERATIONS = 5
SINGLE_FIELD_ZP_EDGE_MARGIN_PX = 180.0
SINGLE_FIELD_ZP_MIN_CATALOG_ERR_MAG = 0.001
SINGLE_FIELD_ZP_MAX_CATALOG_ERR_MAG = 0.12
SINGLE_FIELD_ZP_MAX_REFERENCES = 300
SINGLE_FIELD_ZP_MIN_SNR = 15.0
SINGLE_FIELD_ZP_INFORMATIONAL_MIN_SNR = 10.0
SINGLE_FIELD_ZP_MAX_INST_MAG_ERROR = 0.12
SINGLE_FIELD_ZP_MAX_B_MINUS_V = 1.35
SINGLE_FIELD_ZP_MAX_G_MINUS_R = 1.35
SINGLE_FIELD_ZP_NEIGHBOR_RADIUS_FACTOR = 1.25
SINGLE_FIELD_ZP_NEIGHBOR_MAX_MAG_DELTA = 3.0
TARGET_BLEND_SAME_SOURCE_MAX_SEPARATION_ARCSEC = 2.0
TARGET_BLEND_ERROR_LIMIT_MAG = 0.05
SERIES_TARGET_BLEND_WARNING_LIMIT_MAG = 0.05
SERIES_TARGET_BLEND_ERROR_LIMIT_MAG = 0.10
TARGET_BLEND_HISTORICAL_MAGNITUDE_MISMATCH_MAG = 3.0
TARGET_BLEND_GAIA_QUERY_MAX_ROWS = 1000
GAIA_TARGET_MATCH_SHADOW_NO_MATCH = "NO_MATCH"
GAIA_TARGET_MATCH_SHADOW_MATCHED = "MATCHED"
GAIA_TARGET_MATCH_SHADOW_AMBIGUOUS = "AMBIGUOUS"
GAIA_TARGET_MATCH_SHADOW_INVALID_TARGET = "INVALID_TARGET"
GAIA_MAGNITUDE_SHADOW_TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
GAIA_MAGNITUDE_SHADOW_TARGET_G_MISSING = "TARGET_G_MISSING"
GAIA_MAGNITUDE_SHADOW_NO_NEIGHBORS = "NO_NEIGHBORS"
GAIA_MAGNITUDE_SHADOW_NO_COMPARABLE_NEIGHBORS = "NO_COMPARABLE_NEIGHBORS"
GAIA_MAGNITUDE_SHADOW_PARTIAL = "PARTIAL"
GAIA_MAGNITUDE_SHADOW_COMPARABLE = "COMPARABLE"
GAIA_NEIGHBOR_MAGNITUDE_SHADOW_COMPARABLE = "COMPARABLE_G"
GAIA_NEIGHBOR_MAGNITUDE_SHADOW_TARGET_G_MISSING = "TARGET_G_MISSING"
GAIA_NEIGHBOR_MAGNITUDE_SHADOW_NEIGHBOR_G_MISSING = "NEIGHBOR_G_MISSING"
GAIA_BLEND_SHADOW_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
GAIA_BLEND_SHADOW_NO_COMPARABLE_NEIGHBORS = "NO_COMPARABLE_NEIGHBORS"
GAIA_BLEND_SHADOW_PARTIAL = "PARTIAL"
GAIA_BLEND_SHADOW_MODELED = "MODELED"
GAIA_BLEND_CONTRIBUTION_MODELED = "MODELED"
GAIA_BLEND_CONTRIBUTION_MAGNITUDE_UNAVAILABLE = "MAGNITUDE_UNAVAILABLE"
GAIA_BLEND_CONTRIBUTION_GEOMETRY_UNAVAILABLE = "GEOMETRY_UNAVAILABLE"
GAIA_BLEND_PSF_INTEGRATION_STEPS = 2048
SINGLE_FIELD_ZP_CHECK_MIN_EXTRA_REFERENCES = 1
SINGLE_FIELD_ZP_CHECK_PREFERRED_MAX_CATALOG_ERR_MAG = 0.08
SINGLE_FIELD_ZP_CHECK_PREFERRED_B_MINUS_V = 0.65
SINGLE_FIELD_ZP_CHECK_PREFERRED_G_MINUS_R = 0.65
SINGLE_FIELD_ZP_CHECK_MIN_MAG = 10.0
SINGLE_FIELD_ZP_CHECK_MAX_MAG = 14.8
SINGLE_FIELD_ZP_CHECK_MIN_SNR = 30.0
SINGLE_FIELD_ZP_CHECK_MIN_RESIDUAL_LIMIT = 0.08
SERIES_RINGSET_RULE_ID = "series-ringset-a-v2"
DEFAULT_RINGSET_RULE_ID = "fixed-fwhm-ringset-qc-20260617"
SERIES_RINGSET_SAMPLE_FRAMES = 11
SERIES_RINGSET_SCORE_TARGET_INVALID_WEIGHT = 25.0
SERIES_RINGSET_SCORE_COMP_INVALID_WEIGHT = 20.0
SERIES_RINGSET_SCORE_TARGET_ERROR_WEIGHT = 8.0
SERIES_RINGSET_SCORE_ZP_SCATTER_WEIGHT = 12.0
SERIES_RINGSET_SCORE_CHECK_SCATTER_WEIGHT = 4.0
SERIES_RINGSET_SCORE_CHECK_DELTA_WEIGHT = 0.4
APASS_DR10_TAP_URL = "https://dc.g-vo.org/tap/sync"
COMPARISON_CATALOG_CSV_PREFIX = "comparison_catalog_candidates"
SINGLE_FIELD_ZP_LEGACY_CATALOG_CACHE_NAME = "single_field_zp_apass_dr10_field.csv"
SINGLE_FIELD_ZP_CATALOG_CACHE_NAME = "single_field_zp_auto_catalog_field.csv"
APASS_DR10_SOURCE_NAME = "APASS_DR10"
UCAC4_TAP_URL = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
UCAC4_SOURCE_NAME = "UCAC4"
GAIA_DR3_SOURCE_NAME = "Gaia_DR3"
GAIA_ARI_TAP_URL = "https://gaia.ari.uni-heidelberg.de/tap"
GAIA_ARI_SOURCE_TABLE = "gaiadr3.gaia_source_lite"
GAIA_FIELD_MAXREC = 5_000_000
GAIA_LINEARITY_SAMPLE_SIZE = 750
GAIA_FIELD_MAG_BIN_EDGES = (5.0, 8.0, 10.0, 12.0, 14.0, 15.0, 16.0, 17.0, 18.0, 18.5)
GAIA_FIELD_COLOR_BIN_EDGES = (-0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.75)
COMPARISON_CATALOG_AUTO_NAME = "Auto (APASS_DR10 -> UCAC4)"
COMPARISON_CATALOG_QUERY_CHOICES = (APASS_DR10_SOURCE_NAME, UCAC4_SOURCE_NAME)
# Comparison/check-star catalog selection for series photometry.
# None = auto: try APASS_DR10 first, then UCAC4 when APASS has too few raw or
# fully vetted candidates.
# To force one catalog, set this to APASS_DR10_SOURCE_NAME or UCAC4_SOURCE_NAME.
COMPARISON_CATALOG_SOURCE: str | None = None
COMPARISON_CATALOG_FALLBACK_MIN_CANDIDATES = DEFAULT_COMP_COUNT + 1
APASS_DR10_MIN_MAG = 8.0
APASS_DR10_MAX_MAG = 16.5
APASS_DR10_MAX_QUERY_ROWS = 5000
APASS_DR10_QUERY_TIMEOUT_SECONDS = 90
APASS_DR10_QUERY_RETRIES = 3
UCAC4_QUERY_TIMEOUT_SECONDS = 60
TARGET_BLEND_VIZIER_QUERY_TIMEOUT_SECONDS = 20
TARGET_BLEND_VIZIER_QUERY_RETRIES = 2
TARGET_BLEND_VIZIER_QUERY_RETRY_DELAY_SECONDS = 2.0
COMPARISON_CATALOG_COLUMNS = (
    "id",
    "APASS_DR10_ID",
    "UCAC4",
    "catalog_source",
    "ra",
    "dec",
    "mag_v",
    "err_mag_v",
    "nobs_v",
    "mag_b",
    "err_mag_b",
    "mag_g",
    "err_mag_g",
    "mag_r",
    "err_mag_r",
)

# Result CSV filter metadata.
# SeePhot_CFA.py writes FILTER=L/G for derived light-curve inputs. Native
# Johnson V frames retain V through measurement, result CSV and AAVSO export.
IMAGE_SOURCE_TO_AAVSO_FILTER = {
    "L": "CV",
    "G": "TG",
    "V": "V",
}
VALID_AAVSO_FILTERS = frozenset(IMAGE_SOURCE_TO_AAVSO_FILTER.values())
BINNING_IMAGE_SOURCES = frozenset({"L", "G"})
AAVSO_APPS_URL = "https://apps.aavso.org/v2/"

# Aperture photometry geometry.
# Stage 1 keeps this out of the GUI on purpose: the app stays simple, while
# these constants allow quick tuning during real-data validation.
AUTO_APERTURE_FROM_FWHM = True
AUTO_APERTURE_RADIUS_FWHM_PERCENT = 120.0
AUTO_ANNULUS_INNER_FWHM_PERCENT = 280.0
AUTO_ANNULUS_OUTER_FWHM_PERCENT = 480.0
MANUAL_APERTURE_RADIUS_PX = 6.0
MANUAL_ANNULUS_INNER_PX = 12.0
MANUAL_ANNULUS_OUTER_PX = 18.0
MIN_AUTO_FWHM_PX = 1.0
MAX_AUTO_FWHM_PX = 12.0
MAX_FWHM_SAMPLE_STARS = 80

# Photometry quality policy.
# Keep all central OK/WARNING/INVALID thresholds together. The main evaluator is
# evaluate_photometry_quality(); comp/check selection may use stricter automatic
# rejection than targets, because bad reference stars should silently drop out.
MIN_VALID_COMP_STARS = 3
MIN_PHOTOMETRY_SNR = 5.0
SINGLE_TARGET_LOW_SNR_WARNING = 15.0
MAX_INSTRUMENTAL_MAG_ERROR = 0.25
MAX_CENTROID_OFFSET_FRACTION = 0.75
MIN_CENTROID_OFFSET_PX = 3.0
PHOTOMETRY_EDGE_MARGIN_EXTRA_PX = 5.0
SATURATION_MARGIN_ADU = 5.0
# Practical Seestar high-pixel guard for bright 16-bit stack values; this is a
# policy threshold, not a claim that the FITS dtype is physically saturated.
PHOTOMETRY_HIGH_PIXEL_ADU = 60000.0
TARGET_HIGH_PIXEL_INVALID_COUNT = 3
BRIGHT_LINEARITY_EFFECT_LIMIT_MAG = 0.010
BRIGHT_LINEARITY_GROUP_SIZE = 5
BRIGHT_LINEARITY_MIN_CONTROL_COUNT = 3
BRIGHT_LINEARITY_CONFIDENCE_SIGMA = 1.96
LINEARITY_MIN_REFERENCE_COUNT = BRIGHT_LINEARITY_GROUP_SIZE + BRIGHT_LINEARITY_MIN_CONTROL_COUNT

# Annulus/background contamination policy used for automatic comp/check vetting.
SERIES_COMP_MAX_ANNULUS_PEAK_RATIO = 0.35
SERIES_COMP_ANNULUS_CONTAMINATION_SIGMA = 6.0
SERIES_COMP_ANNULUS_MIN_ISLAND_PIXELS = 4
SERIES_COMP_ANNULUS_MAX_BRIGHT_ISLANDS = 0
REFERENCE_ANNULUS_IMPACT_WARNING_MAG = 0.02
REFERENCE_ANNULUS_IMPACT_INVALID_MAG = 0.05
TARGET_ANNULUS_IMPACT_WARNING_MAG = 0.05
TARGET_ANNULUS_IMPACT_INVALID_MAG = 0.10
# A single-image target may mask isolated bright annulus islands only after an
# explicit user confirmation.  At least roughly two thirds of the ring must
# remain, keeping the background estimate supported by hundreds of pixels for
# the normal SeePhot ringset while also covering crowded single-image fields.
SINGLE_ANNULUS_MASK_MIN_REMAINING_FRACTION = 0.65
SINGLE_ANNULUS_MASK_DILATION_PX = 2

MAX_REFERENCE_FRAME_CANDIDATES = 31

# Plot defaults.
PLOT_RUNNING_MEAN_FRACTION = 0.08
MIN_PLOT_RUNNING_MEAN_WINDOW = 5

# Calibration quality filtering.
# Reject frames whose comparison-star zero-point scatter is a strong robust
# outlier for the current run. The scatter is measured per frame from the
# comparison-star zero points; the run-level limit is median + N * robust sigma.
# This keeps the threshold adaptive for both clean and noisy sessions.
ZERO_POINT_SCATTER_OUTLIER_SIGMA = 8.0
MIN_ZERO_POINT_SCATTER_FILTER_FRAMES = 12

# Fast series-level guard against broad, spatially limited obstructions.  The
# target annulus background and comparison-star annulus backgrounds already
# exist after aperture photometry, so this adds no FITS reads or measurements.
SERIES_LOCAL_BACKGROUND_MIN_FRAMES = 12
SERIES_LOCAL_BACKGROUND_MIN_COMPS = 3
SERIES_LOCAL_BACKGROUND_WARNING_FRACTION = 0.10
SERIES_LOCAL_BACKGROUND_INVALID_FRACTION = 0.20
SERIES_LOCAL_BACKGROUND_WARNING_SIGMA = 5.0
SERIES_LOCAL_BACKGROUND_INVALID_SIGMA = 8.0
SERIES_LOCAL_BACKGROUND_SIGMA_FLOOR_FRACTION = 0.005
SERIES_LOCAL_BACKGROUND_METHOD_ID = "target-comp-background-ratio-v1"

# Reject only isolated, physically implausible one-frame target impulses.  The
# two bracketing measurements must agree, so sustained or monotonic changes
# are not treated as artifacts.
SERIES_TEMPORAL_SPIKE_MIN_MAG = 1.00
SERIES_TEMPORAL_SPIKE_MIN_SIGMA = 8.0
SERIES_TEMPORAL_SPIKE_MAX_NEIGHBOR_DELTA_MAG = 0.20
SERIES_TEMPORAL_SPIKE_MAX_GAP_FACTOR = 3.0
SERIES_TEMPORAL_SPIKE_METHOD_ID = "bracketed-single-frame-calibrated-mag-v1"

# FITS WCS keywords that must not survive into registered work frames before
# a fresh plate-solve run. Registration changes the pixel grid, so inherited
# astrometry can make failed solves look successful in post-run statistics.
WCS_HEADER_PREFIXES = (
    "A_",
    "AP_",
    "B_",
    "BP_",
    "CDELT",
    "CROTA",
    "CRPIX",
    "CRVAL",
    "CTYPE",
    "CUNIT",
)
WCS_MATRIX_PREFIXES = ("CD", "PC", "PS", "PV")
WCS_HEADER_KEYS = {
    "A_ORDER",
    "AP_ORDER",
    "B_ORDER",
    "BP_ORDER",
    "EQUINOX",
    "LATPOLE",
    "LONPOLE",
    "PLTSOLVD",
    "RADESYS",
    "RADESYSA",
    "WCSAXES",
}


if not s.utility.check_module_version(SIRILPY_REQUIRES):
    print(
        f"Error: sirilpy module is too old. This script requires sirilpy {SIRILPY_REQUIRES}."
    )
    sys.exit(1)

def ensure_importable_module(import_name: str, package_name: str | None = None) -> None:
    """Install a package through Siril only when its import module is missing."""

    if importlib.util.find_spec(import_name) is not None:
        return
    s.ensure_installed(package_name or import_name)


ensure_importable_module("PyQt6")
ensure_importable_module("astropy")
ensure_importable_module("numpy")
ensure_importable_module("scipy")
ensure_importable_module("matplotlib")
ensure_importable_module("pyvo")

import astropy.units as u  # noqa: E402
import numpy as np  # noqa: E402
import pyvo  # noqa: E402
from astropy.coordinates import Angle, SkyCoord  # noqa: E402
from astropy.io import fits  # noqa: E402
from astropy.io.fits.verify import VerifyWarning  # noqa: E402
from astropy.time import Time  # noqa: E402
from astropy.utils.exceptions import AstropyUserWarning  # noqa: E402
from astropy.wcs import FITSFixedWarning, WCS  # noqa: E402

warnings.filterwarnings(
    "ignore",
    message=r"'datfix' made the change .*",
    category=FITSFixedWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r"The following header keyword is invalid or follows an unrecognized non-standard convention:.*",
    category=AstropyUserWarning,
)

_FRAME_WCS_CACHE: dict[tuple[str, int], WCS] = {}

from matplotlib.backends.backend_qtagg import (  # noqa: E402
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.widgets import SpanSelector  # noqa: E402
from PyQt6.QtCore import QEventLoop, Qt, QThread, QTimer, QUrl, pyqtSignal  # noqa: E402
from PyQt6.QtGui import QColor, QDesktopServices, QPalette  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QCheckBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyleFactory,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# These are required SeePhot components.  The script directory was added above
# because Siril's embedded interpreter need not start there.
import SeePhot_CFA as cfa_stack_app  # noqa: E402
import sp_mod_analyze as analyze_tools  # noqa: E402
import sp_mod_auto as automatic_tools  # noqa: E402
import sp_mod_batch as batch_tools  # noqa: E402
import sp_mod_binning as measurement_binning  # noqa: E402
import sp_mod_linearity as linearity_tools  # noqa: E402
import sp_mod_profiling as profiling_tools  # noqa: E402


def configure_app_theme(app: QApplication) -> None:
    """Apply the tested dark palette without changing Qt widget metrics."""

    if sys.platform.startswith("win"):
        try:
            app.setStyle(QStyleFactory.create("Fusion"))
        except Exception:
            pass

    colors = THEME_COLORS
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["base"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["panel"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#fff8c6"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["panel"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors["muted_text"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(colors["muted_text"]))
    app.setPalette(palette)
    app.setStyleSheet(
        f"""
        QFrame#overallStatusFrame {{
            background: {colors["status"]};
            border: 1px solid {colors["status_accent"]};
            border-radius: 5px;
        }}
        QLabel#overallStatusLabel {{
            color: {colors["title_text"]};
            font-weight: 600;
        }}
        QLabel#noticeLabel {{
            color: {colors["warning"]};
            font-weight: 600;
        }}
        QCheckBox {{
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid #6b7280;
            border-radius: 3px;
            background-color: {colors["base"]};
        }}
        QCheckBox::indicator:hover {{
            border-color: #8ab4ff;
        }}
        QCheckBox::indicator:checked {{
            background-color: {colors["accent"]};
            border-color: {colors["accent_hover"]};
        }}
        QCheckBox::indicator:checked:hover {{
            background-color: {colors["accent_hover"]};
        }}
        QCheckBox::indicator:checked:disabled {{
            background-color: #32405a;
            border-color: #4b5563;
        }}
        QCheckBox::indicator:unchecked:disabled {{
            background-color: #1b202a;
            border-color: {colors["border"]};
        }}
        QCheckBox:disabled {{
            color: {colors["muted_text"]};
        }}
        QPushButton {{
            background-color: {colors["accent"]};
            border: 1px solid {colors["accent_hover"]};
            border-radius: 5px;
            color: #ffffff;
            padding: 5px 10px;
        }}
        QPushButton:hover {{
            background-color: {colors["accent_hover"]};
        }}
        QPushButton:pressed {{
            background-color: {colors["accent_pressed"]};
        }}
        QPushButton:disabled {{
            background-color: #2a2d33;
            border-color: {colors["border"]};
            color: #8b94a3;
        }}
        QToolTip {{
            color: #111111;
            background-color: #fff8c6;
            border: 1px solid #8a7a2f;
        }}
        """
    )


@dataclass(frozen=True)
class InputScan:
    """Summary of a selected FITS input directory."""

    directory: Path
    fits_count: int
    first_fits: Path | None = None
    selected_fits: Path | None = None


@dataclass(frozen=True)
class PlateSolveHints:
    """Header values needed by Siril's plate solver."""

    ra_deg: float
    dec_deg: float
    focal_mm: float
    pixel_size_um: float
    source_file: Path


@dataclass(frozen=True)
class PlateSolveStats:
    """Summary of WCS headers after a plate solve attempt."""

    total: int
    solved: int
    failed_files: tuple[str, ...]

    @property
    def failed(self) -> int:
        return len(self.failed_files)

    @property
    def has_any_solution(self) -> bool:
        return self.solved > 0

    @property
    def is_complete(self) -> bool:
        return self.total > 0 and self.solved == self.total


@dataclass(frozen=True)
class ReferenceFrame:
    """Reference frame selected for display and catalog overlay."""

    path: Path
    index: int
    ra_deg: float
    dec_deg: float
    width: int
    height: int


@dataclass(frozen=True)
class ReferenceFrameQuality:
    """Fast image-quality metrics used to choose a catalog reference frame."""

    path: Path
    index: int
    fwhm_px: float | None
    star_count: int
    background_tile_scatter: float
    center_distance: float
    score: float


@dataclass(frozen=True)
class CatalogObject:
    """One generic catalog object used by the GUI and photometry pipeline."""

    values: dict[str, str]

    def value_for(self, candidates: tuple[str, ...]) -> str:
        lower_map = {key.lower(): value for key, value in self.values.items()}
        for candidate in candidates:
            value = lower_map.get(candidate.lower())
            if value:
                return value
        return ""

    @property
    def name(self) -> str:
        return self.value_for(
            ("Name", "name", "main_id", "APASS_DR10_ID", "apass_dr10_id", "UCAC4", "id", "VSX", "AUID")
        )

    @property
    def catalog_id(self) -> str:
        return self.value_for(("id", "APASS_DR10_ID", "apass_dr10_id", "UCAC4", "Name", "name"))

    @property
    def catalog_source(self) -> str:
        return self.value_for(("catalog_source", "CatalogSource", "source", "Source"))

    @property
    def ra(self) -> str:
        return self.value_for(("RAJ2000", "RA", "ra", "_RAJ2000"))

    @property
    def dec(self) -> str:
        return self.value_for(("DEJ2000", "DEC", "Dec", "dec", "_DEJ2000"))

    @property
    def object_type(self) -> str:
        return self.value_for(
            (
                "Type",
                "VarType",
                "Var_Type",
                "Var Type",
                "vartype",
                "type",
                "OTYPE",
                "otype",
                "Var",
            )
        )

    @property
    def magnitude(self) -> str:
        return self.value_for(("Vmag", "mag_v", "MagV", "V", "mag", "Mag", "max", "Max"))

    @property
    def period(self) -> str:
        return self.value_for(("Period", "period"))

    def magnitude_float(self) -> float | None:
        value = self.magnitude.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def magnitude_error_float(self) -> float | None:
        value = self.value_for(("err_mag_v", "e_Vmag", "emag", "mag_error")).strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def nobs_v_int(self) -> int | None:
        value = self.value_for(("nobs_v", "nobs", "Nobs")).strip()
        if not value:
            return None
        try:
            return int(float(value))
        except ValueError:
            return None

    def band_float(self, key: str) -> float | None:
        value = self.value_for((key,)).strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def color_index_float(self, blue_key: str, red_key: str) -> float | None:
        blue = self.band_float(blue_key)
        red = self.band_float(red_key)
        if blue is None or red is None:
            return None
        return blue - red

    @property
    def gaia_source_id(self) -> str:
        """Return the explicit Gaia DR3 source identifier, if present."""

        return self.value_for(("gaia_source_id",))

    def gaia_magnitude_float(self, band: str) -> float | None:
        """Return one explicitly named Gaia DR3 magnitude."""

        keys = {
            "g": "gaia_g_mag",
            "bp": "gaia_bp_mag",
            "rp": "gaia_rp_mag",
        }
        key = keys.get(band.strip().casefold())
        return self.band_float(key) if key is not None else None

    def gaia_duplicated_source_bool(self) -> bool | None:
        """Return Gaia duplicated_source as a tri-state value."""

        value = self.value_for(("gaia_duplicated_source",)).strip().casefold()
        if value in {"1", "true", "yes"}:
            return True
        if value in {"0", "false", "no"}:
            return False
        return None

    def gaia_ipd_fraction_float(self, metric: str) -> float | None:
        """Return one Gaia IPD percentage without applying a quality threshold."""

        keys = {
            "multi_peak": "gaia_ipd_frac_multi_peak",
            "odd_win": "gaia_ipd_frac_odd_win",
        }
        key = keys.get(metric.strip().casefold())
        return self.band_float(key) if key is not None else None

    def gaia_bp_rp_float(self) -> float | None:
        """Return the Gaia BP-RP color index, if both bands are available."""

        bp_mag = self.gaia_magnitude_float("bp")
        rp_mag = self.gaia_magnitude_float("rp")
        if bp_mag is None or rp_mag is None:
            return None
        return bp_mag - rp_mag


VSX_CATALOG_INDEX_ROLE = 0x0100
VSX_MAG_SORT_ROLE = 0x0101
VSX_MAG_COLUMN = 3


class VsxTreeWidgetItem(QTreeWidgetItem):
    """Tree item with numeric sorting for the VSX magnitude column."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        if column == VSX_MAG_COLUMN:
            self_mag = self.data(VSX_MAG_COLUMN, VSX_MAG_SORT_ROLE)
            other_mag = other.data(VSX_MAG_COLUMN, VSX_MAG_SORT_ROLE)
            self_value = float(self_mag) if isinstance(self_mag, (int, float)) else math.inf
            other_value = float(other_mag) if isinstance(other_mag, (int, float)) else math.inf
            if self_value != other_value:
                return self_value < other_value
        return super().__lt__(other)


class ResultBrowserTableItem(QTableWidgetItem):
    """Keep formatted result values while sorting numeric columns numerically."""

    def __init__(self, text: str, *, numeric: bool = False) -> None:
        super().__init__(text)
        if numeric:
            try:
                self.sort_value = float(text)
            except (TypeError, ValueError):
                self.sort_value = math.inf
            if not math.isfinite(self.sort_value):
                self.sort_value = math.inf
        else:
            self.sort_value = text.casefold()

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, ResultBrowserTableItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


@dataclass(frozen=True)
class SelectedTarget:
    """Target variable selected by the user from the VSX object list."""

    catalog_object: CatalogObject
    reference_frame: ReferenceFrame


@dataclass(frozen=True)
class LightCurveInputs:
    """Target and comparison objects used for light-curve photometry."""

    target: CatalogObject
    comparison_stars: tuple[CatalogObject, ...]


@dataclass(frozen=True)
class ApertureSettings:
    """Effective aperture and sky annulus radii for one photometry run."""

    aperture_radius_px: float
    annulus_inner_px: float
    annulus_outer_px: float
    mode: str
    fwhm_px: float | None = None
    note: str = ""


@dataclass(frozen=True)
class SeriesRingsetOptimizationResult:
    """Score for one tested sequence-wide aperture/annulus ringset."""

    settings: ApertureSettings
    score: float
    sample_count: int
    calibrated_frame_count: int
    target_valid_rate: float
    comp_valid_frame_rate: float
    median_target_snr: float | None
    median_target_error: float | None
    median_zero_point_scatter: float | None
    p90_zero_point_scatter: float | None
    check_delta_mad_sigma: float | None
    median_abs_check_delta: float | None


@dataclass(frozen=True)
class SequenceWcsFrame:
    """WCS transform and dimensions for one solved sequence frame."""

    path: Path
    width: int
    height: int
    wcs: WCS


@dataclass(frozen=True)
class CandidateVisibilityStats:
    """Visibility count for one candidate over a solved WCS sequence."""

    candidate: CatalogObject
    visible_count: int
    frame_count: int

    @property
    def visible_fraction(self) -> float:
        if self.frame_count <= 0:
            return 0.0
        return self.visible_count / float(self.frame_count)


@dataclass(frozen=True)
class ApertureMeasurement:
    """One instrumental aperture photometry measurement."""

    frame_index: int
    filename: str
    object_id: str
    role: str
    catalog_mag: float | None
    catalog_source: str
    catalog_id: str
    catalog_mag_error: float | None
    catalog_nobs: int | None
    ra_deg: float
    dec_deg: float
    x: float
    y: float
    phot_time: str
    phot_time_source: str
    jd: float
    exptime: float
    aperture_sum: float
    background_median: float
    net_flux: float
    inst_mag: float | None
    aperture_max: float
    aperture_satpix: int
    annulus_max: float
    annulus_satpix: int
    quality_flag: str
    valid: bool
    note: str = ""
    aperture_highpix: int = 0
    annulus_highpix: int = 0
    annulus_contamination: str = ""
    annulus_clean_background_median: float = float("nan")
    annulus_delta_inst_mag: float = float("nan")
    annulus_rejected_pixel_count: int = 0
    annulus_rejected_pixel_fraction: float = 0.0
    annulus_masked: bool = False
    annulus_masked_pixel_count: int = 0
    annulus_masked_pixel_fraction: float = 0.0
    flux_error: float = float("nan")
    snr: float = float("nan")
    inst_mag_error: float | None = None
    centroid_x: float = float("nan")
    centroid_y: float = float("nan")
    centroid_offset_px: float = float("nan")
    catalog_mag_b: float | None = None
    catalog_mag_g: float | None = None
    catalog_mag_r: float | None = None
    catalog_b_minus_v: float | None = None
    catalog_g_minus_r: float | None = None
    image_source: str = ""
    aavso_filter: str = ""
    quality_status: str = ""


@dataclass(frozen=True)
class SingleAnnulusMaskCandidate:
    """One safe automatic exclusion mask for a single target annulus."""

    exclusion_mask: np.ndarray
    island_count: int
    masked_pixel_count: int
    masked_pixel_fraction: float
    remaining_pixel_fraction: float


@dataclass(frozen=True)
class PhotometryQualityDecision:
    """Central quality decision for one aperture measurement."""

    status: str
    flag: str
    valid: bool
    note: str = ""


@dataclass(frozen=True)
class SingleFieldZpReference:
    """One catalog reference measured for single-image field calibration."""

    measurement: ApertureMeasurement
    used_for_fit: bool
    reject_reason: str

    def zero_point(self) -> float | None:
        """Return catalog minus instrumental magnitude for this reference."""

        if (
            not self.measurement.valid
            or self.measurement.catalog_mag is None
            or self.measurement.inst_mag is None
        ):
            return None
        return float(self.measurement.catalog_mag) - float(self.measurement.inst_mag)


@dataclass(frozen=True)
class SingleFieldZpFit:
    """Robust field-zero-point calibration for one single-image measurement."""

    success: bool
    zero_point: float | None
    scatter: float | None
    zero_point_error: float | None
    measured_count: int
    usable_count: int
    used_count: int
    rejected_clip_count: int
    iterations: int
    status: str
    used_object_ids: tuple[str, ...]


@dataclass(frozen=True)
class TargetBlendAssessment:
    """Catalog-neighbor check for target aperture blending."""

    status: str
    flag: str
    blocking: bool
    message: str
    neighbor: CatalogObject | None = None
    separation_px: float | None = None
    separation_arcsec: float | None = None
    neighbor_mag: float | None = None
    target_mag: float | None = None
    summed_flux_ratio: float | None = None
    magnitude_impact_mag: float | None = None
    evidence_complete: bool = False


@dataclass(frozen=True)
class GaiaTargetMatchShadowAssessment:
    """Position-only Gaia target-source association for blend assessment."""

    status: str
    target_source: CatalogObject | None
    target_separation_arcsec: float | None
    match_sources: tuple[CatalogObject, ...]
    neighbor_sources: tuple[CatalogObject, ...]
    invalid_source_count: int
    message: str


@dataclass(frozen=True)
class GaiaNeighborMagnitudeShadowComparison:
    """One same-system Gaia-G comparison with optional color diagnostics."""

    source: CatalogObject
    status: str
    target_g_mag: float | None
    neighbor_g_mag: float | None
    delta_g_mag: float | None
    target_bp_rp: float | None
    neighbor_bp_rp: float | None
    delta_bp_rp: float | None


@dataclass(frozen=True)
class GaiaMagnitudeSystemShadowAssessment:
    """Same-system Gaia-G inputs for quantitative blend policy."""

    status: str
    magnitude_system: str
    target_source: CatalogObject | None
    target_g_mag: float | None
    comparisons: tuple[GaiaNeighborMagnitudeShadowComparison, ...]
    comparable_count: int
    missing_count: int
    message: str


@dataclass(frozen=True)
class GaiaBlendShadowContribution:
    """Modeled contribution of one Gaia neighbor to the target aperture."""

    source: CatalogObject
    status: str
    separation_px: float | None
    delta_g_mag: float | None
    total_flux_ratio: float | None
    neighbor_aperture_fraction: float | None
    target_aperture_fraction: float | None
    aperture_flux_ratio: float | None
    duplicated_source: bool | None
    ipd_frac_multi_peak: float | None
    ipd_frac_odd_win: float | None


@dataclass(frozen=True)
class GaiaBlendShadowAssessment:
    """Summed Gaussian-PSF blend estimate used by the quality policy."""

    status: str
    psf_model: str
    fwhm_px: float | None
    aperture_radius_px: float
    target_aperture_fraction: float | None
    contributions: tuple[GaiaBlendShadowContribution, ...]
    modeled_count: int
    unavailable_count: int
    total_aperture_flux_ratio: float | None
    magnitude_bias_mag: float | None
    magnitude_impact_mag: float | None
    message: str


@dataclass(frozen=True)
class CalibrationWriteStats:
    """Summary of calibrated light-curve rows written to CSV."""

    rows_written: int
    rejected_high_scatter: int
    scatter_limit: float | None
    median_scatter: float | None
    robust_scatter_sigma: float | None


@dataclass(frozen=True)
class SeriesLocalBackgroundAssessment:
    """Summary of the target-to-comparison background-ratio quality guard."""

    method: str
    eligible_count: int
    warning_count: int
    invalid_count: int
    baseline_ratio: float | None
    robust_sigma: float | None


@dataclass(frozen=True)
class SeriesTemporalSpikeAssessment:
    """Summary of the bracketed one-frame target impulse guard."""

    method: str
    eligible_count: int
    rejected_count: int
    median_cadence_seconds: float | None


@dataclass(frozen=True)
class BrightLinearityAssessment:
    """Coarse live estimate of whether a target is brighter than the calibrated range."""

    status: str
    flag: str
    valid: bool
    note: str
    reference_count: int
    bright_limit_catalog_mag: float | None
    target_catalog_mag: float | None
    margin_mag: float | None


@dataclass(frozen=True)
class ComparisonCandidateQuality:
    """Photometric quality metrics for one comparison-star candidate."""

    candidate: CatalogObject
    valid_count: int
    sample_count: int
    median_zero_point: float
    zero_point_offset: float
    robust_scatter: float
    median_snr: float
    median_inst_mag_error: float
    median_centroid_offset: float
    score: float


@dataclass(frozen=True)
class CheckCandidateQuality:
    """Quality metrics for a check-star candidate against a fixed comp ensemble."""

    candidate: CatalogObject
    valid_count: int
    sample_count: int
    median_delta: float
    abs_median_delta: float
    robust_scatter: float
    median_snr: float
    median_inst_mag_error: float
    score: float


@dataclass(frozen=True)
class ComparisonVettingResult:
    """Photometric vetting results plus reusable sample-frame measurements."""

    qualities: tuple[ComparisonCandidateQuality, ...]
    sample_count: int
    sample_frame_indices: tuple[int, ...]
    measurements_by_frame: dict[int, dict[str, ApertureMeasurement]]


def is_hidden_fits(path: Path) -> bool:
    """Return True for macOS metadata files and hidden FITS-like files."""

    return path.name.startswith(".") or path.name.startswith("._")


def is_fits_file(path: Path) -> bool:
    """Return True if path is a visible FITS image file."""

    return (
        path.is_file()
        and path.suffix.lower() in FITS_SUFFIXES
        and not is_hidden_fits(path)
    )


def normalized_path_text(text: str) -> str:
    """Normalize path text pasted from Finder, shell snippets, or file URLs."""

    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if text.startswith("file://"):
        parsed = urllib.parse.urlparse(text)
        text = urllib.parse.unquote(parsed.path)
    return text


def path_from_text(text: str) -> Path | None:
    text = normalized_path_text(text)
    if not text:
        return None
    return Path(text).expanduser()


def same_filesystem_path(left: Path, right: Path) -> bool:
    """Compare paths after best-effort normalization without requiring existence."""

    try:
        return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)
    except Exception:
        return left.expanduser().absolute() == right.expanduser().absolute()


def is_plate_solve_artifact(path: Path) -> bool:
    """Return True for sequence files created by this script's plate solve step."""

    return (
        path.name == f"{WORK_SEQUENCE_NAME}.seq"
        or path.name.startswith(WORK_SEQUENCE_NAME)
        or path.name == f"{LEGACY_REGISTERED_SEQUENCE_NAME}.seq"
        or path.name.startswith(LEGACY_REGISTERED_SEQUENCE_NAME)
    )


def scan_fits_directory(directory: Path) -> InputScan:
    """Count visible FITS files directly inside the selected directory."""

    if not directory.exists():
        raise FileNotFoundError(f"Directory does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    fits_files = sorted(
        path
        for path in directory.iterdir()
        if is_fits_file(path) and not is_plate_solve_artifact(path)
    )
    first_fits = fits_files[0] if fits_files else None
    return InputScan(directory=directory, fits_count=len(fits_files), first_fits=first_fits)


def normalize_image_source(value: object) -> str:
    """Return canonical source channel L/G/V from FITS header metadata."""

    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text in IMAGE_SOURCE_TO_AAVSO_FILTER:
        return text
    if "CFA_L" in text:
        return "L"
    if "CFA_G" in text:
        return "G"
    return ""


def aavso_filter_for_image_source(image_source: str) -> str:
    """Return the AAVSO filter code for the internal image source."""

    return IMAGE_SOURCE_TO_AAVSO_FILTER.get(image_source.strip().upper(), "")


def image_source_from_header(header: fits.Header) -> str:
    """Infer a single unambiguous source channel from FITS header metadata."""

    sources: set[str] = set()
    for key in ("FILTER", "CHANMODE", "CHANNEL", "CFA_CHAN", "CFA_CHANNEL"):
        raw_value = header.get(key)
        if raw_value is None or str(raw_value).strip() == "":
            continue
        source = normalize_image_source(header.get(key))
        if not source:
            return ""
        sources.add(source)
    if len(sources) == 1:
        return next(iter(sources))
    return ""


def infer_image_filter_metadata(
    source_dir: Path | None,
    sample_files: list[Path] | tuple[Path, ...] = (),
) -> tuple[str, str, str] | None:
    """Return image source and AAVSO filter for the current light-curve data."""

    sample_sources: set[str] = set()
    origin_markers: set[str] = set()
    for path in sample_files:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                warnings.simplefilter("ignore", VerifyWarning)
                header = fits.getheader(path)
            source = image_source_from_header(header)
            if not source:
                return None
            sample_sources.add(source)
            origin = str(header.get("SPORIGIN", header.get("SSAP", ""))).strip()
            if origin:
                origin_markers.add(origin)
        except Exception:
            continue
    if len(sample_sources) == 1:
        source = next(iter(sample_sources))
        origin = (
            next(iter(origin_markers))
            if len(origin_markers) == 1
            else ("MIXED_DERIVED_ORIGIN" if len(origin_markers) > 1 else "")
        )
        return source, aavso_filter_for_image_source(source), origin
    if len(sample_sources) > 1:
        return None
    if source_dir is not None:
        try:
            scan = scan_fits_directory(source_dir)
            first_fits = scan.first_fits
        except Exception:
            first_fits = None
        if first_fits is not None:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FITSFixedWarning)
                    warnings.simplefilter("ignore", VerifyWarning)
                    header = fits.getheader(first_fits)
                source = image_source_from_header(header)
                if source:
                    origin = str(header.get("SPORIGIN", header.get("SSAP", ""))).strip()
                    return source, aavso_filter_for_image_source(source), origin
            except Exception:
                pass
    return None


def frame_series_provenance(sample_files: list[Path] | tuple[Path, ...]) -> str:
    """Classify a series without relying on its directory or file name.

    Measurement binning accepts only photometry-ready L/G images that each
    represent exactly one input exposure. Raw CFA is not a photometry input;
    CFA stacks are rejected because NCOMBINE is greater than one.
    """

    if not sample_files:
        return "UNVERIFIED"
    kinds: set[str] = set()
    for path in sample_files:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                warnings.simplefilter("ignore", VerifyWarning)
                header = fits.getheader(path)
        except Exception:
            return "UNVERIFIED"
        origin = str(header.get("SPORIGIN", "")).strip().upper()
        image_source = image_source_from_header(header)
        try:
            ncombine = int(header.get("NCOMBINE", 0))
        except (TypeError, ValueError):
            ncombine = 0
        if image_source not in BINNING_IMAGE_SOURCES:
            kinds.add("UNVERIFIED")
        elif origin == "CFA_STACK" or ncombine != 1:
            kinds.add("DERIVED_FITS")
        else:
            kinds.add("SINGLE_FRAME_LG_SERIES")
    return next(iter(kinds)) if len(kinds) == 1 else "UNVERIFIED"


def uniform_stack_image_count(sample_files: list[Path] | tuple[Path, ...]) -> int | None:
    """Return the common original-image count of one CFA stack, if known."""

    counts: set[int] = set()
    for path in sample_files:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                warnings.simplefilter("ignore", VerifyWarning)
                header = fits.getheader(path)
            count = int(header.get("NCOMBINE", 0))
        except (OSError, TypeError, ValueError):
            return None
        if count <= 1:
            return None
        counts.add(count)
    return next(iter(counts)) if len(counts) == 1 else None


def input_series_fingerprint(paths: list[Path] | tuple[Path, ...]) -> str:
    """Return a stable fingerprint of the selected FITS input series.

    The fingerprint records each input's name, byte size and nanosecond mtime.
    It is intentionally cheap enough for a full Seestar series and is checked
    before a later binning run; a changed source requires fresh photometry.
    """

    digest = hashlib.sha256()
    for path in sorted(paths):
        stat_result = path.stat()
        digest.update(
            f"{path.name}\x1f{stat_result.st_size}\x1f{stat_result.st_mtime_ns}\n".encode("utf-8")
        )
    return digest.hexdigest()


def read_float_header_value(header: fits.Header, keys: tuple[str, ...], label: str) -> float:
    """Read the first available numeric FITS header value from a list of keys."""

    for key in keys:
        if key in header:
            try:
                return float(header[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Header key {key} is not numeric: {header[key]!r}") from exc
    raise KeyError(f"Missing FITS header value for {label}: tried {', '.join(keys)}")


def read_plate_solve_center_deg(header: fits.Header) -> tuple[float, float]:
    """Return the approximate image center in decimal degrees.

    AstroDrive/OSL headers store ``RA`` in decimal hours while also supplying
    the same telescope position as ``RA-TEL`` in decimal degrees.  Recognize
    that relationship explicitly; otherwise retain SeePhot's established
    interpretation of numeric ``RA``/``DEC`` values as decimal degrees.
    """

    ra_value = read_float_header_value(header, ("RA", "OBJCTRA"), "right ascension")
    dec_value = read_float_header_value(header, ("DEC", "OBJCTDEC"), "declination")

    if "RA-TEL" in header and "DEC-TEL" in header:
        try:
            telescope_ra_deg = float(header["RA-TEL"])
            telescope_dec_deg = float(header["DEC-TEL"])
        except (TypeError, ValueError):
            pass
        else:
            ra_from_hours_deg = (ra_value * 15.0) % 360.0
            ra_difference_deg = (
                (telescope_ra_deg - ra_from_hours_deg + 180.0) % 360.0
            ) - 180.0
            if (
                0.0 <= ra_value <= 24.0
                and 0.0 <= telescope_ra_deg < 360.0
                and -90.0 <= telescope_dec_deg <= 90.0
                and math.isclose(ra_difference_deg, 0.0, abs_tol=1e-4)
                and math.isclose(telescope_dec_deg, dec_value, abs_tol=1e-4)
            ):
                return telescope_ra_deg, telescope_dec_deg

    return ra_value, dec_value


def read_plate_solve_hints(path: Path) -> PlateSolveHints:
    """Read plate solving hints from a Seestar FITS header."""

    with fits.open(path) as hdul:
        header = hdul[0].header

    ra_deg, dec_deg = read_plate_solve_center_deg(header)

    return PlateSolveHints(
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        focal_mm=read_float_header_value(
            header,
            ("FOCALLEN", "FOCALLENGTH", "FOCAL"),
            "focal length",
        ),
        pixel_size_um=read_float_header_value(
            header,
            ("XPIXSZ", "PIXSIZE1", "PIXSIZE"),
            "pixel size",
        ),
        source_file=path,
    )


def pixel_scale_arcsec_per_pixel(hints: PlateSolveHints) -> float:
    """Return the nominal image scale from focal length and pixel size."""

    return 206.265 * hints.pixel_size_um / hints.focal_mm


def normalize_result_telescope(value: object) -> str:
    """Return a stable telescope label from a FITS TELESCOP value."""

    text = str(value or "").strip()
    if not text or text.casefold() in {"unknown", "n/a", "none"}:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", text.casefold())
    if "s30pro" in compact:
        return "Seestar S30pro"
    if "s50pro" in compact:
        return "Seestar S50Pro"
    if "s30" in compact:
        return "Seestar S30"
    if "s50" in compact:
        return "Seestar S50"
    return text


def normalize_result_sensor(value: object) -> str:
    """Return a stable sensor label from a FITS INSTRUME value."""

    text = str(value or "").strip()
    if not text or text.casefold() in {"unknown", "n/a", "none"}:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", text.casefold())
    for model, manufacturer in (
        ("IMX585", "Sony"),
        ("IMX662", "Sony"),
        ("IMX462", "Sony"),
        ("OS08B10", "OmniVision"),
    ):
        if model.casefold() in compact:
            return f"{manufacturer} {model}"
    return text


def result_pixel_scale_from_header(header: fits.Header) -> float | None:
    """Return the solved or nominal pixel scale encoded in a FITS header."""

    try:
        wcs = WCS(header).celestial
        if wcs.has_celestial:
            matrix = np.asarray(wcs.pixel_scale_matrix, dtype=np.float64)
            if matrix.shape == (2, 2):
                scale = math.sqrt(abs(float(np.linalg.det(matrix)))) * 3600.0
                if np.isfinite(scale) and 0.01 <= scale <= 120.0:
                    return scale
    except Exception:
        pass

    try:
        focal_mm = read_float_header_value(
            header,
            ("FOCALLEN", "FOCALLENGTH", "FOCAL"),
            "focal length",
        )
        pixel_size_um = read_float_header_value(
            header,
            ("XPIXSZ", "PIXSIZE1", "PIXSIZE"),
            "pixel size",
        )
        scale = 206.265 * pixel_size_um / focal_mm
        if np.isfinite(scale) and 0.01 <= scale <= 120.0:
            return scale
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def result_instrument_metadata_from_header(
    header: fits.Header,
    fallback_telescope: str,
    telescope_specs: dict[str, object],
    fallback_source: str = "config fallback",
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve result instrument metadata, using the supplied fallback if needed."""

    fallback_telescope = str(fallback_telescope or "").strip()
    header_telescope = normalize_result_telescope(header.get("TELESCOP", ""))
    telescope = header_telescope or fallback_telescope
    telescope_source = "FITS TELESCOP" if header_telescope else fallback_source

    telescope_spec = telescope_specs.get(telescope, {})
    if not isinstance(telescope_spec, dict):
        telescope_spec = {}
    fallback_spec = telescope_specs.get(fallback_telescope, {})
    if not isinstance(fallback_spec, dict):
        fallback_spec = {}

    header_sensor = normalize_result_sensor(header.get("INSTRUME", ""))
    if header_sensor:
        sensor = header_sensor
        sensor_source = "FITS INSTRUME"
    elif header_telescope and telescope_spec.get("sensor"):
        sensor = str(telescope_spec["sensor"])
        sensor_source = "FITS telescope model"
    else:
        sensor = str(fallback_spec.get("sensor", ""))
        sensor_source = fallback_source

    header_scale = result_pixel_scale_from_header(header)
    if header_scale is not None:
        pixel_scale = f"{header_scale:.6f}".rstrip("0").rstrip(".")
        scale_source = "FITS WCS/optics"
    elif header_telescope and telescope_spec.get("pixel_scale_arcsec_px") not in (None, ""):
        pixel_scale = str(telescope_spec["pixel_scale_arcsec_px"])
        scale_source = "FITS telescope model"
    else:
        pixel_scale = str(fallback_spec.get("pixel_scale_arcsec_px", ""))
        scale_source = fallback_source

    return (
        {
            "TELESCOPE": telescope,
            "SENSOR": sensor,
            "PIXEL_SCALE_ARCSEC_PX": pixel_scale,
        },
        {
            "TELESCOPE": telescope_source,
            "SENSOR": sensor_source,
            "PIXEL_SCALE_ARCSEC_PX": scale_source,
        },
    )


def siril_path(path: Path) -> str:
    """Return a path string that is safe to place inside a quoted Siril command."""

    return str(path).replace('"', '\\"')


def external_siril_binary_path() -> Path | None:
    """Return a usable standalone Siril executable for background scripts."""

    override = os.getenv("SEEPHOT_SIRIL_BINARY", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate

    if sys.platform == "darwin":
        candidate = Path("/Applications/Siril.app/Contents/MacOS/siril")
        if candidate.is_file():
            return candidate

    for command_name in ("siril-cli", "siril"):
        executable = shutil.which(command_name)
        if executable:
            return Path(executable)
    return None


def appimage_root_for_siril_binary(siril_binary: Path) -> Path | None:
    """Return the AppImage mount containing a Siril executable, if applicable."""

    if not sys.platform.startswith("linux"):
        return None

    lexical_binary_path = siril_binary.expanduser().absolute()
    resolved_binary_path = lexical_binary_path.resolve()
    binary_paths = tuple(dict.fromkeys((lexical_binary_path, resolved_binary_path)))
    app_dir: Path | None = None
    configured_app_dir = os.environ.get("APPDIR", "").strip()
    if configured_app_dir:
        candidate = Path(configured_app_dir).expanduser().resolve()
        if any(binary_path.is_relative_to(candidate) for binary_path in binary_paths):
            app_dir = candidate

    if app_dir is None:
        for binary_path in binary_paths:
            for candidate in binary_path.parents:
                app_run = candidate / "AppRun"
                has_appimage_layout = (
                    candidate.name.startswith(".mount_")
                    and binary_path.parent == candidate / "usr" / "bin"
                    and (candidate / "usr" / "lib").is_dir()
                )
                if (
                    (app_run.is_file() or app_run.is_symlink())
                    and (candidate / "usr").is_dir()
                ) or has_appimage_layout:
                    app_dir = candidate
                    break
            if app_dir is not None:
                break

    return app_dir


def external_siril_command(siril_binary: Path, script_path: Path) -> list[str]:
    """Return the supported standalone command for an installed or AppImage Siril."""

    app_dir = appimage_root_for_siril_binary(siril_binary)
    if app_dir is not None:
        app_run = app_dir / "AppRun"
        if app_run.is_file() or app_run.is_symlink():
            return [str(app_run), "siril-cli", "-s", str(script_path)]
    return [str(siril_binary), "-s", str(script_path)]


def external_siril_environment(siril_binary: Path) -> dict[str, str]:
    """Return a fallback environment for a directly invoked AppImage binary."""

    environment = os.environ.copy()
    app_dir = appimage_root_for_siril_binary(siril_binary)

    if app_dir is None:
        return environment

    library_dirs: list[Path] = []
    for base in (app_dir / "lib", app_dir / "usr" / "lib", app_dir / "usr" / "lib64"):
        if not base.is_dir():
            continue
        library_dirs.append(base)
        library_dirs.extend(sorted(path for path in base.iterdir() if path.is_dir()))

    existing_dirs = environment.get("LD_LIBRARY_PATH", "").split(os.pathsep)
    combined_dirs = [str(path) for path in library_dirs]
    combined_dirs.extend(path for path in existing_dirs if path)
    environment["APPDIR"] = str(app_dir)
    environment["LD_LIBRARY_PATH"] = os.pathsep.join(dict.fromkeys(combined_dirs))
    return environment


def close_siril_image_and_change_cwd(
    siril: s.SirilInterface,
    safe_directory: Path,
) -> None:
    """Close loaded data, then change only Siril's internal working directory."""

    close_error: Exception | None = None
    try:
        siril.cmd("close")
    except Exception as exc:
        close_error = exc
    siril.cmd(f'cd "{siril_path(safe_directory.resolve())}"')
    if close_error is not None:
        raise close_error


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


def catalog_object_filename_stem(obj: CatalogObject) -> str:
    """Return the file-name stem used for target-specific photometry outputs."""

    name = obj.name.strip()
    if name:
        return safe_filename_component(name)
    try:
        ra = float(obj.ra)
        dec = float(obj.dec)
        return safe_filename_component(f"target_RA{ra:.5f}_Dec{dec:.5f}")
    except ValueError:
        return "target"


def csv_data_dict_reader(handle) -> csv.DictReader:
    """Return a DictReader that skips result-CSV metadata header lines."""

    return csv.DictReader(line for line in handle if not line.startswith("#"))


@dataclass(frozen=True)
class LightCurvePlotData:
    """Validated result-CSV values shared by normal and preview plots."""

    jd_values: np.ndarray
    mag_values: np.ndarray
    error_values: np.ndarray
    check_delta_values: np.ndarray
    filter_label: str
    exposure_seconds: float | None


@dataclass(frozen=True)
class LightCurveTrimPlan:
    """Complete replacement content for trimming one current result CSV."""

    result_csv: Path
    metadata: dict[str, object]
    fieldnames: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    original_row_count: int
    range_min_jd: float
    range_max_jd: float
    source_size: int
    source_mtime_ns: int


@dataclass(frozen=True)
class ExtremumFitCalculation:
    """Headless result of the current extremum-fit algorithm."""

    accepted: bool
    reason: str
    message: str
    metrics: dict[str, object]
    reject_category: str = ""
    coefficients: tuple[float, ...] | None = None
    covariance: object | None = None
    x0: float = float("nan")
    selected_x: object | None = None
    selected_y: object | None = None
    selected_yerr: object | None = None
    sigma: object | None = None
    inlier_mask: object | None = None
    vertex_jd: float = float("nan")
    vertex_mag: float = float("nan")
    vertex_jd_error: float = float("nan")
    vertex_mag_error: float = float("nan")
    fit_plot_jd: tuple[float, ...] = ()
    fit_plot_mag: tuple[float, ...] = ()
    warnings: tuple[str, ...] = ()
    extremum_type: str = ""
    result: dict[str, object] | None = None
    show_attempt: bool = False
    model_name: str = ""
    model_checks: tuple[dict[str, object], ...] = ()


EXTREMUM_MINIMUM_FIT_POINTS = 5
EXTREMUM_MINIMUM_SIDE_POINTS = 2
EXTREMUM_MINIMUM_SIDE_COVERAGE_FRACTION = 0.12
EXTREMUM_MINIMUM_TIME_BALANCE = 0.35
EXTREMUM_MINIMUM_PROMINENCE_BALANCE = 0.20
EXTREMUM_MINIMUM_PROMINENCE_FLOOR = 0.01
EXTREMUM_MINIMUM_PROMINENCE_RMS_FACTOR = 0.50
EXTREMUM_FLANK_MEAN_MINIMUM_BLOCK_POINTS = 3
EXTREMUM_FLANK_MEAN_MINIMUM_WEAK_SIGNIFICANCE = 1.0
EXTREMUM_FLANK_MEAN_MINIMUM_STRONG_SIGNIFICANCE = 2.5
EXTREMUM_SMALL_WINDOW_MAX_POINTS = 10
EXTREMUM_SMALL_WINDOW_MAX_CADENCE_SPAN = 10.0
EXTREMUM_CONTEXT_WIDTH_FACTOR = 1.0
EXTREMUM_CONTEXT_MAX_LOCAL_POINTS = 30
EXTREMUM_CONTEXT_MIN_PROMINENCE_TO_SCATTER = 0.90
EXTREMUM_CONTEXT_RELAXED_MIN_POINTS = 16
EXTREMUM_CONTEXT_RELAXED_MIN_PROMINENCE_TO_SCATTER = 0.70
EXTREMUM_VERTEX_EXTREME_MAG_TOLERANCE_FLOOR = 0.02
EXTREMUM_VERTEX_EXTREME_ERROR_FACTOR = 1.5
EXTREMUM_SPLINE_DENSE_SAMPLES = 1200
EXTREMUM_SPLINE_WRONG_DIRECTION_MIN_POINTS = 20
EXTREMUM_SPLINE_MAX_WRONG_DIRECTION_FRACTION = 0.43
EXTREMUM_SPLINE_REQUIRED_EXTREMUM_COUNT = 1
EXTREMUM_MAX_VERTEX_GAP_CADENCES = 8.0
EXTREMUM_ROBUST_GCV_MIN_POINTS = 5
EXTREMUM_ROBUST_GCV_ITERATIONS = 4
EXTREMUM_ROBUST_GCV_HUBER_THRESHOLD = 1.5
EXTREMUM_ASYMPTOTIC_PARABOLA_MIN_POINTS = 7
EXTREMUM_PARABOLIC_SPLINE_MIN_POINTS = 15
EXTREMUM_MODEL_MIN_OUTER_POINTS = 2
EXTREMUM_PARABOLIC_SPLINE_MIN_OUTER_POINTS = 3
EXTREMUM_MODEL_SELECTION_EPSILON = 1e-12
EXTREMUM_MODEL_INVALID_OBJECTIVE_PENALTY = 1e100
EXTREMUM_MODEL_BOOTSTRAP_SAMPLES = 120
EXTREMUM_MODEL_BOOTSTRAP_MIN_SUCCESS_FRACTION = 0.70
EXTREMUM_MODEL_BOOTSTRAP_EQUIVALENCE_RATIO = 1.10
EXTREMUM_MODEL_BOOTSTRAP_RANDOM_SEED = 20260729
EXTREMUM_MODEL_BOOTSTRAP_KNOT_MAX_ITERATIONS = 60
EXTREMUM_ERROR_FALLBACK_RANGE_FRACTION = 0.25
EXTREMUM_REJECT_HARD = "hard"
EXTREMUM_REJECT_GEOMETRY = "geometry"
EXTREMUM_REJECT_PROMINENCE = "prominence"
EXTREMUM_REJECT_PLAUSIBILITY = "plausibility"


@dataclass(frozen=True)
class ExtremumFitInput:
    """Selected finite light-curve points used by the current fit."""

    x: object
    y: object
    yerr: object
    xmin: float
    xmax: float


@dataclass(frozen=True)
class ExtremumParabolaFit:
    """Current weighted parabola fit plus its derived vertex."""

    coefficients: tuple[float, float, float]
    covariance: object
    x0: float
    sigma: object
    inlier_mask: object
    vertex_centered: float
    vertex_jd: float
    vertex_mag: float


@dataclass(frozen=True)
class ExtremumFitQualityMetrics:
    """Current derived metrics used by the extremum-fit acceptance rules."""

    left_x: object
    right_x: object
    left_coverage: float
    right_coverage: float
    time_balance: float
    rms: float
    weighted_rms: float
    left_prominence: float
    right_prominence: float
    minimum_prominence: float
    prominence_balance: float
    inlier_y_min: float
    inlier_y_max: float
    inlier_y_span: float
    vertex_mag_tolerance: float


@dataclass(frozen=True)
class ExtremumSupportAssessment:
    """Decision about whether the selected measurements support an extremum."""

    accepted: bool
    reason: str
    message: str
    reject_category: str
    metrics: dict[str, object]
    fit_input: ExtremumFitInput
    parabola: ExtremumParabolaFit | None = None
    quality: ExtremumFitQualityMetrics | None = None
    anchor: "ExtremumPchipAnchor | None" = None
    show_attempt: bool = False


@dataclass(frozen=True)
class ExtremumFitContextQuality:
    """Context metrics around an otherwise accepted fit."""

    context_points: int
    context_min_jd: float
    context_max_jd: float
    point_to_point_scatter: float
    minimum_fit_prominence: float
    prominence_to_scatter: float


@dataclass(frozen=True)
class ExtremumCurveCandidate:
    """One fitted curve model considered for the final extremum result."""

    model_name: str
    parameter_count: int
    vertex_jd: float
    vertex_mag: float
    vertex_jd_error: float
    vertex_mag_error: float
    fit_plot_jd: tuple[float, ...]
    fit_plot_mag: tuple[float, ...]
    rms: float
    weighted_rms: float
    left_points: int
    right_points: int
    left_coverage: float
    right_coverage: float
    metadata: dict[str, object]


@dataclass(frozen=True)
class ExtremumPchipAnchor:
    """Shape-preserving anchor used to plausibility-check fit candidates."""

    anchor_jd: float
    anchor_mag: float
    observed_extreme_jd: float
    observed_extreme_mag: float
    time_tolerance: float
    mag_tolerance: float
    left_points: int
    right_points: int




def select_extremum_fit_points(
    jd_values: object,
    mag_values: object,
    mag_errors: object,
    xmin: float,
    xmax: float,
) -> ExtremumFitInput:
    """Return selected finite points exactly as the GUI fit used them."""

    mask = (jd_values >= xmin) & (jd_values <= xmax)
    x = jd_values[mask]
    y = mag_values[mask]
    yerr = mag_errors[mask]
    finite_mask = np.isfinite(x) & np.isfinite(y)
    return ExtremumFitInput(
        x=x[finite_mask],
        y=y[finite_mask],
        yerr=yerr[finite_mask],
        xmin=xmin,
        xmax=xmax,
    )


def measure_extremum_fit_context_quality(
    jd_values: object,
    mag_values: object,
    xmin: float,
    xmax: float,
    calculation: ExtremumFitCalculation,
) -> ExtremumFitContextQuality | None:
    """Measure whether the fitted extremum stands out in a larger context."""

    selected_width = float(xmax - xmin)
    if selected_width <= 0:
        return None
    context_min_jd = max(float(np.nanmin(jd_values)), xmin - EXTREMUM_CONTEXT_WIDTH_FACTOR * selected_width)
    context_max_jd = min(float(np.nanmax(jd_values)), xmax + EXTREMUM_CONTEXT_WIDTH_FACTOR * selected_width)
    context_mask = (
        (jd_values >= context_min_jd)
        & (jd_values <= context_max_jd)
        & np.isfinite(jd_values)
        & np.isfinite(mag_values)
    )
    context_x = jd_values[context_mask]
    context_y = mag_values[context_mask]
    if len(context_y) < 6:
        return None
    sort_order = np.argsort(context_x)
    sorted_y = context_y[sort_order]
    point_diffs = np.diff(sorted_y)
    if len(point_diffs) < 3:
        return None
    diff_median = float(np.median(point_diffs))
    diff_mad = float(np.median(np.abs(point_diffs - diff_median)))
    point_to_point_scatter = float(1.4826 * diff_mad / np.sqrt(2.0))
    minimum_fit_prominence = float(
        min(
            calculation.metrics.get("left_prominence", float("nan")),
            calculation.metrics.get("right_prominence", float("nan")),
        )
    )
    if (
        not np.isfinite(point_to_point_scatter)
        or point_to_point_scatter <= 0
        or not np.isfinite(minimum_fit_prominence)
    ):
        return None
    return ExtremumFitContextQuality(
        context_points=int(len(context_y)),
        context_min_jd=float(context_min_jd),
        context_max_jd=float(context_max_jd),
        point_to_point_scatter=point_to_point_scatter,
        minimum_fit_prominence=minimum_fit_prominence,
        prominence_to_scatter=float(minimum_fit_prominence / point_to_point_scatter),
    )


def measure_current_extremum_fit_quality(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
) -> ExtremumFitQualityMetrics:
    """Compute the current geometry, residual, and prominence metrics."""

    x = fit_input.x
    y = fit_input.y
    a, b, c = parabola.coefficients
    centered_x = x - parabola.x0
    inlier_mask = parabola.inlier_mask
    inlier_x = x[inlier_mask]
    left_x = inlier_x[inlier_x < parabola.vertex_jd]
    right_x = inlier_x[inlier_x > parabola.vertex_jd]
    left_coverage = float(parabola.vertex_jd - np.min(left_x))
    right_coverage = float(np.max(right_x) - parabola.vertex_jd)
    time_balance = min(left_coverage, right_coverage) / max(left_coverage, right_coverage)
    fit_y = a * centered_x * centered_x + b * centered_x + c
    inlier_residuals = y[inlier_mask] - fit_y[inlier_mask]
    rms = float(np.sqrt(np.mean(inlier_residuals**2)))
    weighted_rms = float(
        np.sqrt(np.average(inlier_residuals**2, weights=1.0 / parabola.sigma[inlier_mask] ** 2))
    )
    inlier_y = y[inlier_mask]
    inlier_y_min = float(np.min(inlier_y))
    inlier_y_max = float(np.max(inlier_y))
    inlier_y_span = inlier_y_max - inlier_y_min

    def fitted_mag_at(jd_value: float) -> float:
        centered = jd_value - parabola.x0
        return float(a * centered * centered + b * centered + c)

    left_edge_mag = fitted_mag_at(float(np.min(left_x)))
    right_edge_mag = fitted_mag_at(float(np.max(right_x)))
    left_prominence = abs(left_edge_mag - parabola.vertex_mag)
    right_prominence = abs(right_edge_mag - parabola.vertex_mag)
    minimum_prominence = max(
        EXTREMUM_MINIMUM_PROMINENCE_FLOOR,
        EXTREMUM_MINIMUM_PROMINENCE_RMS_FACTOR * rms,
    )
    prominence_balance = min(left_prominence, right_prominence) / max(left_prominence, right_prominence)
    vertex_mag_tolerance = max(0.05, 2.0 * rms, 0.25 * inlier_y_span)
    return ExtremumFitQualityMetrics(
        left_x=left_x,
        right_x=right_x,
        left_coverage=left_coverage,
        right_coverage=right_coverage,
        time_balance=time_balance,
        rms=rms,
        weighted_rms=weighted_rms,
        left_prominence=left_prominence,
        right_prominence=right_prominence,
        minimum_prominence=minimum_prominence,
        prominence_balance=prominence_balance,
        inlier_y_min=inlier_y_min,
        inlier_y_max=inlier_y_max,
        inlier_y_span=inlier_y_span,
        vertex_mag_tolerance=vertex_mag_tolerance,
    )


def _nearest_shape_crossing_delta(
    jd_values: object,
    mag_values: object,
    *,
    vertex_jd: float,
    vertex_mag: float,
    sigma_mag: float,
    side: str,
) -> float:
    """Return the nearest time delta where the fit differs by sigma_mag."""

    jd_array = np.asarray(jd_values, dtype=np.float64)
    mag_array = np.asarray(mag_values, dtype=np.float64)
    finite_mask = np.isfinite(jd_array) & np.isfinite(mag_array)
    jd_array = jd_array[finite_mask]
    mag_array = mag_array[finite_mask]
    if len(jd_array) < 2 or not np.isfinite(sigma_mag) or sigma_mag <= 0:
        return float("nan")
    if side == "left":
        side_mask = jd_array <= vertex_jd
    else:
        side_mask = jd_array >= vertex_jd
    side_jd = jd_array[side_mask]
    side_delta_mag = np.abs(mag_array[side_mask] - vertex_mag)
    if len(side_jd) < 2:
        return float("nan")
    order = np.argsort(np.abs(side_jd - vertex_jd))
    side_jd = side_jd[order]
    side_delta_mag = side_delta_mag[order]
    for index in range(1, len(side_jd)):
        previous_delta = float(side_delta_mag[index - 1])
        current_delta = float(side_delta_mag[index])
        if current_delta < sigma_mag:
            continue
        previous_time_delta = abs(float(side_jd[index - 1]) - vertex_jd)
        current_time_delta = abs(float(side_jd[index]) - vertex_jd)
        if current_delta == previous_delta:
            return current_time_delta
        fraction = (sigma_mag - previous_delta) / (current_delta - previous_delta)
        fraction = min(max(float(fraction), 0.0), 1.0)
        return previous_time_delta + fraction * (current_time_delta - previous_time_delta)
    return float("nan")


def extremum_shape_based_jd_error(
    *,
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    vertex_jd: float,
    vertex_mag: float,
    fit_plot_jd: tuple[float, ...],
    fit_plot_mag: tuple[float, ...],
) -> dict[str, float | str]:
    """Estimate JD uncertainty from local curve shape and measured scatter.

    The covariance error is only the formal uncertainty of the chosen model.  For
    noisy or flat extrema the practical timing uncertainty is the interval over
    which the fitted curve cannot be distinguished from the extremum by the
    real residual scatter.  This method keeps the user-selected fit range fixed:
    it measures residual scatter in that range and asks where the accepted fit
    curve has moved by that scatter on either side of the vertex.
    """

    x = np.asarray(fit_input.x, dtype=np.float64)
    y = np.asarray(fit_input.y, dtype=np.float64)
    yerr = np.asarray(fit_input.yerr, dtype=np.float64)
    inlier_mask = np.asarray(parabola.inlier_mask, dtype=bool)
    finite_mask = np.isfinite(x) & np.isfinite(y) & inlier_mask
    fit_jd = np.asarray(fit_plot_jd, dtype=np.float64)
    fit_mag = np.asarray(fit_plot_mag, dtype=np.float64)
    fit_finite_mask = np.isfinite(fit_jd) & np.isfinite(fit_mag)
    fit_jd = fit_jd[fit_finite_mask]
    fit_mag = fit_mag[fit_finite_mask]
    if len(fit_jd) < 2 or not np.isfinite(vertex_jd) or not np.isfinite(vertex_mag):
        fallback = max(float(fit_input.xmax - fit_input.xmin) * EXTREMUM_ERROR_FALLBACK_RANGE_FRACTION, 0.0)
        return {
            "jd_error_shape": float("nan"),
            "jd_error_shape_left": float("nan"),
            "jd_error_shape_right": float("nan"),
            "jd_error_shape_sigma_mag": float("nan"),
            "jd_error_shape_method": "unavailable",
            "jd_error_time_resolution": float("nan"),
            "jd_error_fallback": fallback,
        }

    sort_order = np.argsort(fit_jd)
    fit_jd = fit_jd[sort_order]
    fit_mag = fit_mag[sort_order]
    selected_x = x[finite_mask]
    selected_y = y[finite_mask]
    selected_yerr = yerr[finite_mask]
    model_at_points = np.interp(selected_x, fit_jd, fit_mag)
    residuals = selected_y - model_at_points
    residuals = residuals[np.isfinite(residuals)]
    residual_rms = float("nan")
    residual_mad_sigma = float("nan")
    if len(residuals) >= 2:
        centered_residuals = residuals - float(np.median(residuals))
        residual_rms = float(np.sqrt(np.mean(centered_residuals**2)))
        residual_mad_sigma = 1.4826 * float(np.median(np.abs(centered_residuals)))
    valid_errors = selected_yerr[np.isfinite(selected_yerr) & (selected_yerr > 0)]
    median_point_error = float(np.median(valid_errors)) if len(valid_errors) else float("nan")
    scatter_candidates = [
        value
        for value in (residual_rms, residual_mad_sigma, median_point_error)
        if np.isfinite(value) and value > 0
    ]
    sigma_mag = max(scatter_candidates) if scatter_candidates else float("nan")

    left_delta = _nearest_shape_crossing_delta(
        fit_jd,
        fit_mag,
        vertex_jd=vertex_jd,
        vertex_mag=vertex_mag,
        sigma_mag=sigma_mag,
        side="left",
    )
    right_delta = _nearest_shape_crossing_delta(
        fit_jd,
        fit_mag,
        vertex_jd=vertex_jd,
        vertex_mag=vertex_mag,
        sigma_mag=sigma_mag,
        side="right",
    )
    shape_candidates = [
        value
        for value in (left_delta, right_delta)
        if np.isfinite(value) and value > 0
    ]
    shape_error = max(shape_candidates) if shape_candidates else float("nan")

    sorted_x = np.sort(selected_x)
    positive_cadence = np.diff(sorted_x)
    positive_cadence = positive_cadence[positive_cadence > 0]
    time_resolution = 0.5 * float(np.median(positive_cadence)) if len(positive_cadence) else float("nan")
    fallback = max(float(fit_input.xmax - fit_input.xmin) * EXTREMUM_ERROR_FALLBACK_RANGE_FRACTION, 0.0)
    if not np.isfinite(shape_error) or shape_error <= 0:
        fallback_error = fallback
        method = "fallback_range_fraction"
    else:
        fallback_error = float("nan")
        method = "residual_scatter_curve_width"
    return {
        "jd_error_shape": shape_error,
        "jd_error_shape_left": left_delta,
        "jd_error_shape_right": right_delta,
        "jd_error_shape_sigma_mag": sigma_mag,
        "jd_error_shape_residual_rms": residual_rms,
        "jd_error_shape_residual_mad_sigma": residual_mad_sigma,
        "jd_error_shape_median_point_error": median_point_error,
        "jd_error_shape_method": method,
        "jd_error_time_resolution": time_resolution,
        "jd_error_fallback": fallback_error,
    }


def accepted_extremum_fit(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    quality: ExtremumFitQualityMetrics,
    *,
    model_name: str = "parabola",
    vertex_jd: float | None = None,
    vertex_mag: float | None = None,
    vertex_jd_error: float | None = None,
    vertex_mag_error: float | None = None,
    bootstrap_vertex_jd_error: float | None = None,
    bootstrap_vertex_mag_error: float | None = None,
    fit_plot_jd: tuple[float, ...] | None = None,
    fit_plot_mag: tuple[float, ...] | None = None,
    rms: float | None = None,
    weighted_rms: float | None = None,
    model_metrics: dict[str, object] | None = None,
    result_model_metadata: dict[str, object] | None = None,
    model_checks: tuple[dict[str, object], ...] = (),
) -> ExtremumFitCalculation:
    """Build the accepted fit result using the existing result-dict contract."""

    x = fit_input.x
    a, b, c = parabola.coefficients
    fit_vertex_jd = float(parabola.vertex_jd) if vertex_jd is None else float(vertex_jd)
    fit_vertex_mag = float(parabola.vertex_mag) if vertex_mag is None else float(vertex_mag)
    fit_vertex_jd_error = float("nan")
    fit_vertex_mag_error = float("nan")
    if vertex_jd_error is None or vertex_mag_error is None:
        try:
            grad_x = np.array([b / (2.0 * a * a), -1.0 / (2.0 * a), 0.0], dtype=np.float64)
            vertex_centered_var = float(grad_x @ parabola.covariance @ grad_x)
            if vertex_centered_var >= 0:
                fit_vertex_jd_error = float(np.sqrt(vertex_centered_var))
            grad_y = np.array([parabola.vertex_centered**2, parabola.vertex_centered, 1.0], dtype=np.float64)
            vertex_mag_var = float(grad_y @ parabola.covariance @ grad_y)
            if vertex_mag_var >= 0:
                fit_vertex_mag_error = float(np.sqrt(vertex_mag_var))
        except Exception:
            pass
    if vertex_jd_error is not None:
        fit_vertex_jd_error = float(vertex_jd_error)
    if vertex_mag_error is not None:
        fit_vertex_mag_error = float(vertex_mag_error)

    warnings_text: list[str] = []
    inlier_count = (
        int(len(x))
        if model_name == "robust_gcv_spline"
        else int(np.count_nonzero(parabola.inlier_mask))
    )
    rejected_count = int(len(x) - inlier_count)
    if rejected_count:
        warnings_text.append(f"{rejected_count} outlier(s) rejected")
    if model_name != "parabola":
        warnings_text.append(f"model={model_name}")
    if model_metrics and model_name == "robust_gcv_spline":
        downweighted = int(model_metrics.get("robust_downweighted_points", 0))
        if downweighted:
            warnings_text.append(
                f"{downweighted} measurement(s) robustly downweighted, none removed"
            )
    if model_metrics and model_metrics.get("model_bootstrap_stable") is False:
        warnings_text.append(
            "bootstrap timing is unstable; reported uncertainty is provisional"
        )
    extremum_type = "Maximum" if a > 0 else "Minimum"
    if fit_plot_jd is None or fit_plot_mag is None:
        fit_x_plot = np.linspace(float(np.min(x)), float(np.max(x)), 160)
        fit_centered = fit_x_plot - parabola.x0
        fit_y_plot = a * fit_centered * fit_centered + b * fit_centered + c
        result_fit_plot_jd = tuple(float(value) for value in fit_x_plot)
        result_fit_plot_mag = tuple(float(value) for value in fit_y_plot)
    else:
        result_fit_plot_jd = tuple(float(value) for value in fit_plot_jd)
        result_fit_plot_mag = tuple(float(value) for value in fit_plot_mag)
    result_rms = float(quality.rms if rms is None else rms)
    result_weighted_rms = float(quality.weighted_rms if weighted_rms is None else weighted_rms)
    formal_vertex_jd_error = float(fit_vertex_jd_error)
    formal_vertex_mag_error = float(fit_vertex_mag_error)
    bootstrap_jd_error = (
        float(bootstrap_vertex_jd_error)
        if bootstrap_vertex_jd_error is not None
        else float("nan")
    )
    bootstrap_mag_error = (
        float(bootstrap_vertex_mag_error)
        if bootstrap_vertex_mag_error is not None
        else float("nan")
    )
    shape_error = extremum_shape_based_jd_error(
        fit_input=fit_input,
        parabola=parabola,
        vertex_jd=fit_vertex_jd,
        vertex_mag=fit_vertex_mag,
        fit_plot_jd=result_fit_plot_jd,
        fit_plot_mag=result_fit_plot_mag,
    )
    candidate_jd_errors = [
        value
        for value in (
            formal_vertex_jd_error,
            bootstrap_jd_error,
            shape_error["jd_error_shape"],
            shape_error["jd_error_time_resolution"],
            shape_error["jd_error_fallback"],
        )
        if np.isfinite(value) and value > 0
    ]
    if candidate_jd_errors:
        fit_vertex_jd_error = max(candidate_jd_errors)
    candidate_mag_errors = [
        value
        for value in (formal_vertex_mag_error, bootstrap_mag_error)
        if np.isfinite(value) and value > 0
    ]
    if candidate_mag_errors:
        fit_vertex_mag_error = max(candidate_mag_errors)
    if np.isfinite(fit_vertex_jd_error) and np.isfinite(formal_vertex_jd_error):
        if fit_vertex_jd_error > formal_vertex_jd_error:
            warnings_text.append("JD error includes bootstrap/curve-shape uncertainty")
    elif np.isfinite(fit_vertex_jd_error):
        warnings_text.append("JD error estimated from curve shape")
    else:
        warnings_text.append("JD error unavailable")
    result = {
        "type": extremum_type,
        "jd": fit_vertex_jd,
        "jd_error": float(fit_vertex_jd_error),
        "jd_error_formal": formal_vertex_jd_error,
        "jd_error_bootstrap": bootstrap_jd_error,
        **shape_error,
        "mag": fit_vertex_mag,
        "mag_error": float(fit_vertex_mag_error),
        "mag_error_formal": formal_vertex_mag_error,
        "mag_error_bootstrap": bootstrap_mag_error,
        "point_count": inlier_count,
        "selected_point_count": int(len(x)),
        "rms": result_rms,
        "weighted_rms": result_weighted_rms,
        "range_min_jd": float(fit_input.xmin),
        "range_max_jd": float(fit_input.xmax),
        "fit_min_jd": float(np.min(x)),
        "fit_max_jd": float(np.max(x)),
        "fit_x0_jd": float(parabola.x0),
        "fit_coefficients": parabola.coefficients,
        "fit_model": model_name,
        "fit_model_candidates": ("parabola", "spline"),
        "fit_anchor_model": "pchip",
        "fit_plot_jd": result_fit_plot_jd,
        "fit_plot_mag": result_fit_plot_mag,
        "warnings": tuple(warnings_text),
    }
    if result_model_metadata:
        result.update(result_model_metadata)
    metrics = {
        "model": model_name,
        "selected_points": len(x),
        "inliers": inlier_count,
        "vertex_jd": fit_vertex_jd,
        "vertex_mag": fit_vertex_mag,
        "left_points": len(quality.left_x),
        "right_points": len(quality.right_x),
        "left_coverage": quality.left_coverage,
        "right_coverage": quality.right_coverage,
        "time_balance": quality.time_balance,
        "rms": result_rms,
        "weighted_rms": result_weighted_rms,
        "left_prominence": quality.left_prominence,
        "right_prominence": quality.right_prominence,
        "minimum_prominence": quality.minimum_prominence,
        "marginal_asymmetric_prominence": (
            min(quality.left_prominence, quality.right_prominence) < quality.minimum_prominence
            <= max(quality.left_prominence, quality.right_prominence)
            <= 1.25 * quality.minimum_prominence
        ),
        "prominence_balance": quality.prominence_balance,
        "jd_error_formal": formal_vertex_jd_error,
        "jd_error_bootstrap": bootstrap_jd_error,
        "jd_error_shape": shape_error["jd_error_shape"],
        "jd_error_shape_sigma_mag": shape_error["jd_error_shape_sigma_mag"],
        "jd_error_time_resolution": shape_error["jd_error_time_resolution"],
        "mag_error_formal": formal_vertex_mag_error,
        "mag_error_bootstrap": bootstrap_mag_error,
    }
    if model_metrics:
        metrics.update(model_metrics)
    return ExtremumFitCalculation(
        accepted=True,
        reason="ok",
        message="",
        metrics=metrics,
        reject_category="",
        coefficients=parabola.coefficients,
        covariance=parabola.covariance,
        x0=parabola.x0,
        selected_x=x,
        selected_y=fit_input.y,
        selected_yerr=fit_input.yerr,
        sigma=parabola.sigma,
        inlier_mask=parabola.inlier_mask,
        vertex_jd=fit_vertex_jd,
        vertex_mag=fit_vertex_mag,
        vertex_jd_error=float(fit_vertex_jd_error),
        vertex_mag_error=float(fit_vertex_mag_error),
        fit_plot_jd=result_fit_plot_jd,
        fit_plot_mag=result_fit_plot_mag,
        warnings=tuple(warnings_text),
        extremum_type=extremum_type,
        result=result,
        model_name=model_name,
        model_checks=model_checks,
    )


def rejected_extremum_fit(
    fit_input: ExtremumFitInput,
    message: str,
    reason: str,
    metrics: dict[str, object],
    *,
    reject_category: str,
    show_attempt: bool = False,
    parabola: ExtremumParabolaFit | None = None,
    model_checks: tuple[dict[str, object], ...] = (),
) -> ExtremumFitCalculation:
    """Build a rejected headless fit result without GUI side effects."""

    return ExtremumFitCalculation(
        accepted=False,
        reason=reason,
        message=message,
        metrics=metrics,
        reject_category=reject_category,
        coefficients=None if parabola is None else parabola.coefficients,
        covariance=None if parabola is None else parabola.covariance,
        x0=float("nan") if parabola is None else parabola.x0,
        selected_x=fit_input.x,
        selected_y=fit_input.y,
        selected_yerr=fit_input.yerr,
        sigma=None if parabola is None else parabola.sigma,
        inlier_mask=None if parabola is None else parabola.inlier_mask,
        vertex_jd=float("nan") if parabola is None else parabola.vertex_jd,
        vertex_mag=float("nan") if parabola is None else parabola.vertex_mag,
        show_attempt=show_attempt,
        model_checks=model_checks,
    )


def fit_extremum_parabola_with_current_clipping(
    fit_input: ExtremumFitInput,
) -> ExtremumParabolaFit:
    """Run the existing weighted parabola fit and one-pass MAD clipping."""

    x = fit_input.x
    y = fit_input.y
    yerr = fit_input.yerr
    x0 = float(np.mean(x))
    centered_x = x - x0
    valid_errors = yerr[np.isfinite(yerr) & (yerr > 0)]
    if len(valid_errors) > 0:
        error_floor = max(float(np.median(valid_errors)) * 0.5, 1e-4)
    else:
        error_floor = 1.0
    sigma = np.where(np.isfinite(yerr) & (yerr > 0), yerr, error_floor)
    sigma = np.maximum(sigma, error_floor)
    inlier_mask = np.ones(len(x), dtype=bool)

    coeffs, covariance = np.polyfit(
        centered_x,
        y,
        2,
        w=1.0 / sigma,
        cov=True,
    )
    preliminary_fit = np.polyval(coeffs, centered_x)
    residuals = y - preliminary_fit
    residual_median = float(np.median(residuals))
    residual_mad = float(np.median(np.abs(residuals - residual_median)))
    robust_residual_sigma = 1.4826 * residual_mad
    if np.isfinite(robust_residual_sigma) and robust_residual_sigma > 0:
        inlier_mask = np.abs(residuals - residual_median) <= 4.0 * robust_residual_sigma
        if int(np.count_nonzero(inlier_mask)) >= EXTREMUM_MINIMUM_FIT_POINTS and not np.all(inlier_mask):
            coeffs, covariance = np.polyfit(
                centered_x[inlier_mask],
                y[inlier_mask],
                2,
                w=1.0 / sigma[inlier_mask],
                cov=True,
            )
        else:
            inlier_mask = np.ones(len(x), dtype=bool)

    a, b, c = (float(coeffs[0]), float(coeffs[1]), float(coeffs[2]))
    vertex_centered = -b / (2.0 * a)
    vertex_jd = x0 + vertex_centered
    vertex_mag = a * vertex_centered * vertex_centered + b * vertex_centered + c
    return ExtremumParabolaFit(
        coefficients=(a, b, c),
        covariance=covariance,
        x0=x0,
        sigma=sigma,
        inlier_mask=inlier_mask,
        vertex_centered=float(vertex_centered),
        vertex_jd=float(vertex_jd),
        vertex_mag=float(vertex_mag),
    )




def _weighted_linear_curve_fit(
    design: object,
    values: object,
    sigma: object,
) -> tuple[object, object, object, float, float]:
    """Solve one weighted linear subproblem used by the piecewise models."""

    design_array = np.asarray(design, dtype=np.float64)
    values_array = np.asarray(values, dtype=np.float64)
    sigma_array = np.asarray(sigma, dtype=np.float64)
    weighted_design = design_array / sigma_array[:, None]
    weighted_values = values_array / sigma_array
    coefficients, _residuals, rank, _singular = np.linalg.lstsq(
        weighted_design,
        weighted_values,
        rcond=None,
    )
    if int(rank) != design_array.shape[1]:
        raise ValueError("rank-deficient piecewise fit")
    fitted = design_array @ coefficients
    residuals = values_array - fitted
    chi_square = float(np.sum((residuals / sigma_array) ** 2))
    degrees_of_freedom = max(len(values_array) - design_array.shape[1], 1)
    covariance = np.linalg.pinv(weighted_design.T @ weighted_design)
    covariance = covariance * (chi_square / degrees_of_freedom)
    rms = float(np.sqrt(np.mean(residuals**2)))
    weighted_rms = float(
        np.sqrt(np.average(residuals**2, weights=1.0 / sigma_array**2))
    )
    return coefficients, covariance, fitted, rms, weighted_rms


def _candidate_side_support(
    x: object,
    vertex_jd: float,
) -> tuple[int, int, float, float] | None:
    """Return point counts and coverage on both sides of a candidate vertex."""

    x_array = np.asarray(x, dtype=np.float64)
    left_x = x_array[x_array < vertex_jd]
    right_x = x_array[x_array > vertex_jd]
    if len(left_x) == 0 or len(right_x) == 0:
        return None
    return (
        int(len(left_x)),
        int(len(right_x)),
        float(vertex_jd - np.min(left_x)),
        float(np.max(right_x) - vertex_jd),
    )


def _refine_piecewise_knots(
    objective,
    initial_knots: tuple[float, float],
    bounds: tuple[tuple[float, float], tuple[float, float]],
    *,
    max_iterations: int = 100,
) -> tuple[float, float]:
    """Refine the best discrete knot pair without requiring SciPy at import."""

    try:
        from scipy.optimize import minimize

        def finite_objective(values: object) -> float:
            objective_value = float(objective(values))
            return (
                objective_value
                if np.isfinite(objective_value)
                else EXTREMUM_MODEL_INVALID_OBJECTIVE_PENALTY
            )

        result = minimize(
            finite_objective,
            np.asarray(initial_knots, dtype=np.float64),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": max_iterations, "ftol": 1e-12},
        )
    except Exception:
        return initial_knots
    if not result.success or len(result.x) != 2 or not np.all(np.isfinite(result.x)):
        return initial_knots
    return float(result.x[0]), float(result.x[1])


def _asymptotic_parabola_design(
    centered_minutes: object,
    left_knot: float,
    right_knot: float,
) -> object:
    """Return the published 1-2-1 asymptotic-parabola design matrix."""

    time_values = np.asarray(centered_minutes, dtype=np.float64)
    half_width = 0.5 * (right_knot - left_knot)
    midpoint = 0.5 * (right_knot + left_knot)
    relative_time = time_values - midpoint
    quadratic_basis = np.where(
        time_values < left_knot,
        (-2.0 * relative_time - half_width) * half_width,
        np.where(
            time_values <= right_knot,
            relative_time**2,
            (2.0 * relative_time - half_width) * half_width,
        ),
    )
    return np.column_stack(
        (
            np.ones(len(time_values), dtype=np.float64),
            quadratic_basis,
            relative_time,
        )
    )


def fit_extremum_asymptotic_parabola_candidate(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
) -> ExtremumCurveCandidate | None:
    """Fit the established line-parabola-line model for asymmetric extrema."""

    inlier_x = np.asarray(fit_input.x[parabola.inlier_mask], dtype=np.float64)
    inlier_y = np.asarray(fit_input.y[parabola.inlier_mask], dtype=np.float64)
    sigma = np.asarray(parabola.sigma[parabola.inlier_mask], dtype=np.float64)
    if len(inlier_x) < EXTREMUM_ASYMPTOTIC_PARABOLA_MIN_POINTS:
        return None
    sort_order = np.argsort(inlier_x)
    inlier_x = inlier_x[sort_order]
    inlier_y = inlier_y[sort_order]
    sigma = sigma[sort_order]
    if len(np.unique(inlier_x)) != len(inlier_x):
        return None

    x0 = float(np.mean(inlier_x))
    centered_minutes = (inlier_x - x0) * 1440.0
    expected_sign = 1.0 if parabola.coefficients[0] > 0 else -1.0
    point_count = len(inlier_x)
    best: tuple[
        float,
        int,
        int,
        float,
        float,
        object,
        object,
        float,
        float,
    ] | None = None

    def solve_knots(left_knot: float, right_knot: float):
        if not np.isfinite(left_knot) or not np.isfinite(right_knot) or left_knot >= right_knot:
            return None
        try:
            design = _asymptotic_parabola_design(
                centered_minutes,
                left_knot,
                right_knot,
            )
            coefficients, covariance, _fitted, rms, weighted_rms = _weighted_linear_curve_fit(
                design,
                inlier_y,
                sigma,
            )
        except Exception:
            return None
        curvature = float(coefficients[1])
        slope = float(coefficients[2])
        if not np.isfinite(curvature) or expected_sign * curvature <= 0:
            return None
        half_width = 0.5 * (right_knot - left_knot)
        vertex_relative = -slope / (2.0 * curvature)
        if not np.isfinite(vertex_relative) or not -half_width <= vertex_relative <= half_width:
            return None
        chi_square = float(
            np.sum(
                (
                    (
                        inlier_y
                        - design @ coefficients
                    )
                    / sigma
                )
                ** 2
            )
        )
        return (
            chi_square,
            coefficients,
            covariance,
            rms,
            weighted_rms,
        )

    minimum_outer = EXTREMUM_MODEL_MIN_OUTER_POINTS
    for left_index in range(minimum_outer, point_count - minimum_outer - 2):
        for right_index in range(left_index + 2, point_count - minimum_outer):
            left_knot = float(centered_minutes[left_index])
            right_knot = float(centered_minutes[right_index])
            solved = solve_knots(left_knot, right_knot)
            if solved is None:
                continue
            chi_square, coefficients, covariance, rms, weighted_rms = solved
            if best is None or chi_square < best[0]:
                best = (
                    chi_square,
                    left_index,
                    right_index,
                    left_knot,
                    right_knot,
                    coefficients,
                    covariance,
                    rms,
                    weighted_rms,
                )
    if best is None:
        return None

    (
        _chi_square,
        left_index,
        right_index,
        left_knot,
        right_knot,
        _coefficients,
        _covariance,
        _rms,
        _weighted_rms,
    ) = best
    left_bounds = (
        0.5 * float(centered_minutes[left_index - 1] + centered_minutes[left_index]),
        0.5 * float(centered_minutes[left_index] + centered_minutes[left_index + 1]),
    )
    right_bounds = (
        0.5 * float(centered_minutes[right_index - 1] + centered_minutes[right_index]),
        0.5 * float(centered_minutes[right_index] + centered_minutes[right_index + 1]),
    )

    def knot_objective(values: object) -> float:
        knots = np.asarray(values, dtype=np.float64)
        solved = solve_knots(float(knots[0]), float(knots[1]))
        return float("inf") if solved is None else float(solved[0])

    refined_left, refined_right = _refine_piecewise_knots(
        knot_objective,
        (left_knot, right_knot),
        (left_bounds, right_bounds),
    )
    refined = solve_knots(refined_left, refined_right)
    if refined is None or refined[0] > best[0]:
        refined_left, refined_right = left_knot, right_knot
        refined = solve_knots(refined_left, refined_right)
    if refined is None:
        return None
    chi_square, coefficients, covariance, rms, weighted_rms = refined
    curvature = float(coefficients[1])
    slope = float(coefficients[2])
    midpoint = 0.5 * (refined_left + refined_right)
    vertex_relative = -slope / (2.0 * curvature)
    vertex_minutes = midpoint + vertex_relative
    vertex_jd = float(x0 + vertex_minutes / 1440.0)
    vertex_mag = float(coefficients[0] - slope * slope / (4.0 * curvature))
    side_support = _candidate_side_support(inlier_x, vertex_jd)
    if side_support is None:
        return None
    left_points, right_points, left_coverage, right_coverage = side_support

    vertex_jd_error = float("nan")
    vertex_mag_error = float("nan")
    try:
        vertex_gradient = np.array(
            [
                0.0,
                slope / (2.0 * curvature * curvature),
                -1.0 / (2.0 * curvature),
            ],
            dtype=np.float64,
        )
        vertex_variance = float(vertex_gradient @ covariance @ vertex_gradient)
        if vertex_variance >= 0:
            vertex_jd_error = float(np.sqrt(vertex_variance) / 1440.0)
        magnitude_gradient = np.array(
            [
                1.0,
                slope * slope / (4.0 * curvature * curvature),
                -slope / (2.0 * curvature),
            ],
            dtype=np.float64,
        )
        magnitude_variance = float(magnitude_gradient @ covariance @ magnitude_gradient)
        if magnitude_variance >= 0:
            vertex_mag_error = float(np.sqrt(magnitude_variance))
    except Exception:
        pass

    dense_minutes = np.linspace(
        float(np.min(centered_minutes)),
        float(np.max(centered_minutes)),
        EXTREMUM_SPLINE_DENSE_SAMPLES,
    )
    dense_design = _asymptotic_parabola_design(
        dense_minutes,
        refined_left,
        refined_right,
    )
    dense_mag = dense_design @ coefficients
    return ExtremumCurveCandidate(
        model_name="asymptotic_parabola",
        parameter_count=5,
        vertex_jd=vertex_jd,
        vertex_mag=vertex_mag,
        vertex_jd_error=vertex_jd_error,
        vertex_mag_error=vertex_mag_error,
        fit_plot_jd=tuple(float(x0 + value / 1440.0) for value in dense_minutes),
        fit_plot_mag=tuple(float(value) for value in dense_mag),
        rms=float(rms),
        weighted_rms=float(weighted_rms),
        left_points=left_points,
        right_points=right_points,
        left_coverage=left_coverage,
        right_coverage=right_coverage,
        metadata={
            "left_knot_jd": float(x0 + refined_left / 1440.0),
            "right_knot_jd": float(x0 + refined_right / 1440.0),
            "chi_square": float(chi_square),
            "linear_coefficients": tuple(float(value) for value in coefficients),
            "formal_error_conditioning": "fixed optimized knots",
        },
    )


def _parabolic_spline_design(
    centered_minutes: object,
    left_knot: float,
    right_knot: float,
) -> object:
    """Return the published three-parabola, defect-one spline design."""

    time_values = np.asarray(centered_minutes, dtype=np.float64)
    return np.column_stack(
        (
            np.ones(len(time_values), dtype=np.float64),
            time_values,
            time_values**2,
            np.where(time_values < left_knot, (left_knot - time_values) ** 2, 0.0),
            np.where(time_values > right_knot, (time_values - right_knot) ** 2, 0.0),
        )
    )


def fit_extremum_parabolic_spline_candidate(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
) -> ExtremumCurveCandidate | None:
    """Fit the published quadratic C1 spline with two optimized knots."""

    inlier_x = np.asarray(fit_input.x[parabola.inlier_mask], dtype=np.float64)
    inlier_y = np.asarray(fit_input.y[parabola.inlier_mask], dtype=np.float64)
    sigma = np.asarray(parabola.sigma[parabola.inlier_mask], dtype=np.float64)
    if len(inlier_x) < EXTREMUM_PARABOLIC_SPLINE_MIN_POINTS:
        return None
    sort_order = np.argsort(inlier_x)
    inlier_x = inlier_x[sort_order]
    inlier_y = inlier_y[sort_order]
    sigma = sigma[sort_order]
    if len(np.unique(inlier_x)) != len(inlier_x):
        return None

    x0 = float(np.mean(inlier_x))
    centered_minutes = (inlier_x - x0) * 1440.0
    expected_sign = 1.0 if parabola.coefficients[0] > 0 else -1.0
    point_count = len(inlier_x)
    best: tuple[
        float,
        int,
        int,
        float,
        float,
        object,
        object,
        float,
        float,
    ] | None = None

    def solve_knots(left_knot: float, right_knot: float):
        if not np.isfinite(left_knot) or not np.isfinite(right_knot) or left_knot >= right_knot:
            return None
        try:
            design = _parabolic_spline_design(
                centered_minutes,
                left_knot,
                right_knot,
            )
            coefficients, covariance, _fitted, rms, weighted_rms = _weighted_linear_curve_fit(
                design,
                inlier_y,
                sigma,
            )
        except Exception:
            return None
        curvature = float(coefficients[2])
        linear = float(coefficients[1])
        if not np.isfinite(curvature) or expected_sign * curvature <= 0:
            return None
        vertex_minutes = -linear / (2.0 * curvature)
        if not np.isfinite(vertex_minutes) or not left_knot <= vertex_minutes <= right_knot:
            return None
        chi_square = float(np.sum(((inlier_y - design @ coefficients) / sigma) ** 2))
        return (
            chi_square,
            coefficients,
            covariance,
            rms,
            weighted_rms,
        )

    minimum_outer = EXTREMUM_PARABOLIC_SPLINE_MIN_OUTER_POINTS
    for left_index in range(minimum_outer, point_count - minimum_outer - 2):
        for right_index in range(left_index + 2, point_count - minimum_outer):
            left_knot = float(centered_minutes[left_index])
            right_knot = float(centered_minutes[right_index])
            solved = solve_knots(left_knot, right_knot)
            if solved is None:
                continue
            chi_square, coefficients, covariance, rms, weighted_rms = solved
            if best is None or chi_square < best[0]:
                best = (
                    chi_square,
                    left_index,
                    right_index,
                    left_knot,
                    right_knot,
                    coefficients,
                    covariance,
                    rms,
                    weighted_rms,
                )
    if best is None:
        return None

    (
        _chi_square,
        left_index,
        right_index,
        left_knot,
        right_knot,
        _coefficients,
        _covariance,
        _rms,
        _weighted_rms,
    ) = best
    left_bounds = (
        0.5 * float(centered_minutes[left_index - 1] + centered_minutes[left_index]),
        0.5 * float(centered_minutes[left_index] + centered_minutes[left_index + 1]),
    )
    right_bounds = (
        0.5 * float(centered_minutes[right_index - 1] + centered_minutes[right_index]),
        0.5 * float(centered_minutes[right_index] + centered_minutes[right_index + 1]),
    )

    def knot_objective(values: object) -> float:
        knots = np.asarray(values, dtype=np.float64)
        solved = solve_knots(float(knots[0]), float(knots[1]))
        return float("inf") if solved is None else float(solved[0])

    refined_left, refined_right = _refine_piecewise_knots(
        knot_objective,
        (left_knot, right_knot),
        (left_bounds, right_bounds),
    )
    refined = solve_knots(refined_left, refined_right)
    if refined is None or refined[0] > best[0]:
        refined_left, refined_right = left_knot, right_knot
        refined = solve_knots(refined_left, refined_right)
    if refined is None:
        return None
    chi_square, coefficients, covariance, rms, weighted_rms = refined
    linear = float(coefficients[1])
    curvature = float(coefficients[2])
    vertex_minutes = -linear / (2.0 * curvature)
    vertex_jd = float(x0 + vertex_minutes / 1440.0)
    vertex_mag = float(coefficients[0] - linear * linear / (4.0 * curvature))
    side_support = _candidate_side_support(inlier_x, vertex_jd)
    if side_support is None:
        return None
    left_points, right_points, left_coverage, right_coverage = side_support

    vertex_jd_error = float("nan")
    vertex_mag_error = float("nan")
    try:
        vertex_gradient = np.array(
            [
                0.0,
                -1.0 / (2.0 * curvature),
                linear / (2.0 * curvature * curvature),
                0.0,
                0.0,
            ],
            dtype=np.float64,
        )
        vertex_variance = float(vertex_gradient @ covariance @ vertex_gradient)
        if vertex_variance >= 0:
            vertex_jd_error = float(np.sqrt(vertex_variance) / 1440.0)
        magnitude_gradient = np.array(
            [
                1.0,
                -linear / (2.0 * curvature),
                linear * linear / (4.0 * curvature * curvature),
                0.0,
                0.0,
            ],
            dtype=np.float64,
        )
        magnitude_variance = float(magnitude_gradient @ covariance @ magnitude_gradient)
        if magnitude_variance >= 0:
            vertex_mag_error = float(np.sqrt(magnitude_variance))
    except Exception:
        pass

    dense_minutes = np.linspace(
        float(np.min(centered_minutes)),
        float(np.max(centered_minutes)),
        EXTREMUM_SPLINE_DENSE_SAMPLES,
    )
    dense_design = _parabolic_spline_design(
        dense_minutes,
        refined_left,
        refined_right,
    )
    dense_mag = dense_design @ coefficients
    return ExtremumCurveCandidate(
        model_name="parabolic_spline",
        parameter_count=7,
        vertex_jd=vertex_jd,
        vertex_mag=vertex_mag,
        vertex_jd_error=vertex_jd_error,
        vertex_mag_error=vertex_mag_error,
        fit_plot_jd=tuple(float(x0 + value / 1440.0) for value in dense_minutes),
        fit_plot_mag=tuple(float(value) for value in dense_mag),
        rms=float(rms),
        weighted_rms=float(weighted_rms),
        left_points=left_points,
        right_points=right_points,
        left_coverage=left_coverage,
        right_coverage=right_coverage,
        metadata={
            "left_knot_jd": float(x0 + refined_left / 1440.0),
            "right_knot_jd": float(x0 + refined_right / 1440.0),
            "chi_square": float(chi_square),
            "linear_coefficients": tuple(float(value) for value in coefficients),
            "formal_error_conditioning": "fixed optimized knots",
        },
    )


def observed_extreme_metrics(
    fit_input: ExtremumFitInput,
    inlier_mask: object,
    extremum_type: str,
    vertex_mag: float,
) -> dict[str, object]:
    """Compare a fitted extremum to the brightest/faintest measured point."""

    inlier_y = fit_input.y[inlier_mask]
    inlier_yerr = fit_input.yerr[inlier_mask]
    inlier_x = fit_input.x[inlier_mask]
    if extremum_type == "Maximum":
        observed_extreme_index = int(np.argmin(inlier_y))
        observed_extreme_mag = float(inlier_y[observed_extreme_index])
        vertex_extreme_delta = float(vertex_mag - observed_extreme_mag)
        extreme_kind = "brightest"
    else:
        observed_extreme_index = int(np.argmax(inlier_y))
        observed_extreme_mag = float(inlier_y[observed_extreme_index])
        vertex_extreme_delta = float(observed_extreme_mag - vertex_mag)
        extreme_kind = "faintest"
    observed_extreme_error = float(inlier_yerr[observed_extreme_index])
    if not np.isfinite(observed_extreme_error) or observed_extreme_error <= 0:
        observed_extreme_error = 0.0
    vertex_extreme_tolerance = max(
        EXTREMUM_VERTEX_EXTREME_MAG_TOLERANCE_FLOOR,
        EXTREMUM_VERTEX_EXTREME_ERROR_FACTOR * observed_extreme_error,
    )
    return {
        "observed_extreme_jd": float(inlier_x[observed_extreme_index]),
        "observed_extreme_mag": observed_extreme_mag,
        "observed_extreme_error": observed_extreme_error,
        "vertex_extreme_delta": vertex_extreme_delta,
        "vertex_extreme_tolerance": vertex_extreme_tolerance,
        "extreme_kind": extreme_kind,
    }




def _fit_robust_gcv_spline_curve(
    x_values: object,
    y_values: object,
    error_values: object,
):
    """Fit the reviewed natural cubic GCV spline with robust reweighting."""

    from scipy.interpolate import make_smoothing_spline

    x = np.asarray(x_values, dtype=np.float64)
    y = np.asarray(y_values, dtype=np.float64)
    errors = np.asarray(error_values, dtype=np.float64)
    if len(x) < EXTREMUM_ROBUST_GCV_MIN_POINTS or y.shape != x.shape or errors.shape != x.shape:
        raise ValueError(
            f"At least {EXTREMUM_ROBUST_GCV_MIN_POINTS} matching measurements are required."
        )
    order = np.argsort(x, kind="stable")
    x, y, errors = x[order], y[order], errors[order]
    if len(np.unique(x)) != len(x):
        raise ValueError("Measurement times must be distinct.")
    center = float(np.mean(x))
    span = float(np.ptp(x))
    if not np.isfinite(span) or span <= 0:
        raise ValueError("The selected measurements have no usable time span.")
    normalized_x = (x - center) / span
    valid_errors = np.isfinite(errors) & (errors > 0)
    typical_error = (
        float(np.median(errors[valid_errors])) if np.any(valid_errors) else 1.0
    )
    safe_errors = np.where(valid_errors, errors, typical_error)
    base_weights = 1.0 / np.square(safe_errors)
    base_weights /= float(np.median(base_weights))
    robust_weights = np.ones_like(y)
    spline = None
    fitted_robust_weights = None
    residual_scale = float("nan")
    for _iteration in range(EXTREMUM_ROBUST_GCV_ITERATIONS):
        fitted_robust_weights = robust_weights.copy()
        spline = make_smoothing_spline(
            normalized_x,
            y,
            w=base_weights * robust_weights,
            lam=None,
        )
        residuals = y - spline(normalized_x)
        residual_center = float(np.median(residuals))
        residual_scale = 1.4826 * float(
            np.median(np.abs(residuals - residual_center))
        )
        if not residual_scale > np.finfo(np.float64).eps:
            break
        standardized = np.abs(residuals - residual_center) / (
            EXTREMUM_ROBUST_GCV_HUBER_THRESHOLD * residual_scale
        )
        robust_weights = np.ones_like(standardized)
        outside = standardized > 1.0
        robust_weights[outside] = 1.0 / standardized[outside]
    if spline is None:
        raise ValueError("The automatic smoothing spline could not be fitted.")
    if fitted_robust_weights is not None and not np.array_equal(
        fitted_robust_weights,
        robust_weights,
    ):
        spline = make_smoothing_spline(
            normalized_x,
            y,
            w=base_weights * robust_weights,
            lam=None,
        )
        residuals = y - spline(normalized_x)
        residual_center = float(np.median(residuals))
        residual_scale = 1.4826 * float(
            np.median(np.abs(residuals - residual_center))
        )
    return spline, x, y, errors, center, span, base_weights, robust_weights, residual_scale


def _robust_gcv_spline_extrema(spline, center: float, span: float):
    """Return all strictly interior stationary points of a fitted GCV spline."""

    from scipy.interpolate import PPoly

    polynomial = PPoly.from_spline(spline)
    roots = polynomial.derivative().roots(extrapolate=False)
    lower = float(spline.t[spline.k])
    upper = float(spline.t[-spline.k - 1])
    extrema: list[tuple[float, float, float]] = []
    for root in np.unique(roots[np.isfinite(roots)]):
        root = float(root)
        if not lower < root < upper:
            continue
        curvature = float(polynomial.derivative(2)(root))
        if not np.isfinite(curvature) or abs(curvature) <= 1e-12:
            continue
        extrema.append(
            (
                float(center + span * root),
                float(spline(root)),
                curvature,
            )
        )
    return tuple(extrema)


def fit_extremum_robust_gcv_spline_candidate(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
) -> ExtremumCurveCandidate | None:
    """Return the expected extremum of the automatic robust GCV spline."""

    try:
        (
            spline,
            x,
            y,
            _errors,
            center,
            span,
            base_weights,
            robust_weights,
            residual_scale,
        ) = _fit_robust_gcv_spline_curve(
            fit_input.x,
            fit_input.y,
            fit_input.yerr,
        )
    except (ImportError, ValueError, np.linalg.LinAlgError):
        return None
    extrema = _robust_gcv_spline_extrema(spline, center, span)
    expected_sign = 1.0 if parabola.coefficients[0] > 0 else -1.0
    matching = [item for item in extrema if expected_sign * item[2] > 0]
    if not matching:
        return None
    vertex_jd, vertex_mag, _curvature = (
        min(matching, key=lambda item: item[1])
        if expected_sign > 0
        else max(matching, key=lambda item: item[1])
    )
    side_support = _candidate_side_support(x, vertex_jd)
    if side_support is None:
        return None
    left_points, right_points, left_coverage, right_coverage = side_support
    normalized_x = (x - center) / span
    residuals = y - spline(normalized_x)
    combined_weights = base_weights * robust_weights
    dense_normalized = np.linspace(
        float((x[0] - center) / span),
        float((x[-1] - center) / span),
        EXTREMUM_SPLINE_DENSE_SAMPLES,
    )
    dense_jd = center + span * dense_normalized
    dense_mag = spline(dense_normalized)
    return ExtremumCurveCandidate(
        model_name="robust_gcv_spline",
        parameter_count=int(len(spline.c)),
        vertex_jd=float(vertex_jd),
        vertex_mag=float(vertex_mag),
        vertex_jd_error=float("nan"),
        vertex_mag_error=float("nan"),
        fit_plot_jd=tuple(float(value) for value in dense_jd),
        fit_plot_mag=tuple(float(value) for value in dense_mag),
        rms=float(np.sqrt(np.mean(residuals**2))),
        weighted_rms=float(
            np.sqrt(np.sum(combined_weights * residuals**2) / np.sum(combined_weights))
        ),
        left_points=left_points,
        right_points=right_points,
        left_coverage=left_coverage,
        right_coverage=right_coverage,
        metadata={
            "smoothing_selection": "gcv",
            "spline_center_jd": float(center),
            "spline_time_span_days": float(span),
            "robust_iterations": EXTREMUM_ROBUST_GCV_ITERATIONS,
            "robust_residual_scale": float(residual_scale),
            "robust_downweighted_points": int(np.count_nonzero(robust_weights < 0.8)),
            "robust_weights": tuple(float(value) for value in robust_weights),
        },
    )


def spline_wrong_direction_metrics(
    spline: ExtremumCurveCandidate,
    extremum_type: str,
) -> dict[str, object]:
    """Return how often a spline fit moves against the expected extremum flank."""

    fit_jd = np.array(spline.fit_plot_jd, dtype=np.float64)
    fit_mag = np.array(spline.fit_plot_mag, dtype=np.float64)
    if len(fit_jd) < 3:
        return {
            "spline_wrong_direction_steps": 0,
            "spline_direction_steps": 0,
            "spline_wrong_direction_fraction": 0.0,
        }

    left_mag = fit_mag[fit_jd < spline.vertex_jd]
    right_mag = fit_mag[fit_jd > spline.vertex_jd]
    left_delta = np.diff(left_mag)
    right_delta = np.diff(right_mag)
    if extremum_type == "Minimum":
        left_bad = int(np.count_nonzero(left_delta < -1e-5))
        right_bad = int(np.count_nonzero(right_delta > 1e-5))
    else:
        left_bad = int(np.count_nonzero(left_delta > 1e-5))
        right_bad = int(np.count_nonzero(right_delta < -1e-5))
    total_steps = int(len(left_delta) + len(right_delta))
    bad_steps = left_bad + right_bad
    bad_fraction = float(bad_steps / total_steps) if total_steps else 0.0
    return {
        "spline_wrong_direction_steps": bad_steps,
        "spline_direction_steps": total_steps,
        "spline_wrong_direction_fraction": bad_fraction,
        "spline_wrong_direction_left_steps": left_bad,
        "spline_wrong_direction_right_steps": right_bad,
    }


def spline_shape_metrics(
    spline: ExtremumCurveCandidate,
    edge_excursion_minimum: float,
) -> dict[str, object]:
    """Report raw, edge-filtered, and noise-prominent turning-point counts."""

    fit_mag = np.array(spline.fit_plot_mag, dtype=np.float64)
    finite_mag = fit_mag[np.isfinite(fit_mag)]
    if len(finite_mag) < 3:
        return {
            "spline_extremum_count": 0,
            "spline_raw_extremum_count": 0,
            "spline_prominent_extremum_count": 0,
            "spline_relevant_extremum_count": 0,
            "spline_ignored_edge_extremum_count": 0,
            "spline_ignored_edge_excursion_max": 0.0,
            "spline_ignored_subnoise_extremum_count": 0,
            "spline_edge_excursion_minimum": float(edge_excursion_minimum),
            "spline_shape_epsilon": float("nan"),
        }
    magnitude_scale = max(1.0, float(np.max(np.abs(finite_mag))))
    epsilon = max(1e-12, np.finfo(np.float64).eps * magnitude_scale * 32.0)
    deltas = np.diff(fit_mag)
    signed_steps: list[tuple[int, int]] = []
    for index, delta in enumerate(deltas):
        if not np.isfinite(delta) or abs(float(delta)) <= epsilon:
            continue
        signed_steps.append((index, 1 if delta > 0 else -1))
    if len(signed_steps) < 2:
        return {
            "spline_extremum_count": 0,
            "spline_raw_extremum_count": 0,
            "spline_prominent_extremum_count": 0,
            "spline_relevant_extremum_count": 0,
            "spline_ignored_edge_extremum_count": 0,
            "spline_ignored_edge_excursion_max": 0.0,
            "spline_ignored_subnoise_extremum_count": 0,
            "spline_edge_excursion_minimum": float(edge_excursion_minimum),
            "spline_shape_epsilon": epsilon,
        }
    turning_indices = [
        current_index
        for (_previous_index, previous_sign), (current_index, current_sign) in zip(
            signed_steps,
            signed_steps[1:],
            strict=False,
        )
        if previous_sign != current_sign
    ]
    from scipy.signal import find_peaks

    significant_maxima, _maxima_properties = find_peaks(
        fit_mag,
        prominence=float(edge_excursion_minimum),
    )
    significant_minima, _minima_properties = find_peaks(
        -fit_mag,
        prominence=float(edge_excursion_minimum),
    )
    significant_turning_indices = {
        min(turning_indices, key=lambda turning: abs(turning - int(index)))
        for index in (*significant_maxima, *significant_minima)
    }
    primary_turning_index = min(
        turning_indices,
        key=lambda index: abs(float(spline.fit_plot_jd[index]) - spline.vertex_jd),
    )
    relevant_turning_indices = significant_turning_indices | {primary_turning_index}
    meaningful_turning_indices = set(turning_indices)
    ignored_edge_excursions: list[float] = []
    if len(turning_indices) > 1:
        first_index = turning_indices[0]
        left_edge_excursion = abs(float(fit_mag[first_index] - fit_mag[0]))
        if (
            np.isfinite(left_edge_excursion)
            and left_edge_excursion < edge_excursion_minimum
        ):
            meaningful_turning_indices.discard(first_index)
            ignored_edge_excursions.append(left_edge_excursion)

        last_index = turning_indices[-1]
        right_edge_excursion = abs(float(fit_mag[-1] - fit_mag[last_index]))
        if (
            last_index in meaningful_turning_indices
            and np.isfinite(right_edge_excursion)
            and right_edge_excursion < edge_excursion_minimum
        ):
            meaningful_turning_indices.discard(last_index)
            ignored_edge_excursions.append(right_edge_excursion)

    return {
        "spline_extremum_count": len(meaningful_turning_indices),
        "spline_raw_extremum_count": len(turning_indices),
        "spline_prominent_extremum_count": len(significant_turning_indices),
        "spline_relevant_extremum_count": len(relevant_turning_indices),
        "spline_ignored_edge_extremum_count": len(ignored_edge_excursions),
        "spline_ignored_edge_excursion_max": (
            max(ignored_edge_excursions)
            if ignored_edge_excursions
            else 0.0
        ),
        "spline_ignored_subnoise_extremum_count": max(
            0,
            len(turning_indices) - len(relevant_turning_indices),
        ),
        "spline_edge_excursion_minimum": float(edge_excursion_minimum),
        "spline_shape_epsilon": epsilon,
    }


def fit_extremum_pchip_anchor(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    preferred_jd: float | None = None,
) -> ExtremumPchipAnchor | None:
    """Return a shape-preserving extremum anchor for candidate checks.

    ``preferred_jd`` is an independently found nightly candidate, when one is
    available. It prevents a raw PCHIP wiggle at an isolated noisy point from
    replacing the selected event.
    """

    try:
        from scipy.interpolate import PchipInterpolator
    except Exception:
        return None

    x = fit_input.x[parabola.inlier_mask]
    y = fit_input.y[parabola.inlier_mask]
    if len(x) < EXTREMUM_MINIMUM_FIT_POINTS:
        return None
    sort_order = np.argsort(x)
    x = x[sort_order]
    y = y[sort_order]
    if len(np.unique(x)) != len(x):
        return None

    a, _b, _c = parabola.coefficients
    extremum_type = "Maximum" if a > 0 else "Minimum"
    x0 = float(np.mean(x))
    centered_minutes = (x - x0) * 1440.0
    try:
        pchip = PchipInterpolator(centered_minutes, y, extrapolate=False)
        dense_minutes = np.linspace(
            float(np.min(centered_minutes)),
            float(np.max(centered_minutes)),
            EXTREMUM_SPLINE_DENSE_SAMPLES,
        )
        dense_mag = pchip(dense_minutes)
    except Exception:
        return None
    dense_deltas = np.diff(dense_mag)
    magnitude_scale = (
        max(1.0, float(np.max(np.abs(dense_mag[np.isfinite(dense_mag)]))))
        if np.isfinite(dense_mag).any()
        else 1.0
    )
    epsilon = max(1e-12, np.finfo(np.float64).eps * magnitude_scale * 32.0)
    signed_steps = np.zeros(len(dense_deltas), dtype=np.int8)
    signed_steps[dense_deltas > epsilon] = 1
    signed_steps[dense_deltas < -epsilon] = -1
    turning_candidates: list[int] = []
    previous_sign = 0
    for index, sign in enumerate(signed_steps):
        current_sign = int(sign)
        if current_sign == 0:
            continue
        if extremum_type == "Maximum":
            is_matching_turn = previous_sign < 0 and current_sign > 0
        else:
            is_matching_turn = previous_sign > 0 and current_sign < 0
        if is_matching_turn:
            candidate_jd = float(x0 + float(dense_minutes[index]) / 1440.0)
            if (
                np.count_nonzero(x < candidate_jd) >= EXTREMUM_MINIMUM_SIDE_POINTS
                and np.count_nonzero(x > candidate_jd) >= EXTREMUM_MINIMUM_SIDE_POINTS
            ):
                turning_candidates.append(index)
        previous_sign = current_sign
    if turning_candidates:
        target_jd = (
            float(preferred_jd)
            if preferred_jd is not None and np.isfinite(preferred_jd)
            else float(parabola.vertex_jd)
        )
        dense_index = min(
            turning_candidates,
            key=lambda index: abs(
                float(x0 + float(dense_minutes[index]) / 1440.0)
                - target_jd
            ),
        )
    elif extremum_type == "Maximum":
        dense_index = int(np.nanargmin(dense_mag))
    else:
        dense_index = int(np.nanargmax(dense_mag))
    anchor_jd = float(x0 + float(dense_minutes[dense_index]) / 1440.0)
    anchor_mag = float(dense_mag[dense_index])
    observed_index = int(np.argmin(np.abs(x - anchor_jd)))
    observed_extreme_jd = float(x[observed_index])
    observed_extreme_mag = float(y[observed_index])

    left_points = int(np.count_nonzero(x < anchor_jd))
    right_points = int(np.count_nonzero(x > anchor_jd))
    positive_cadence = np.diff(x)
    positive_cadence = positive_cadence[positive_cadence > 0]
    median_cadence = float(np.median(positive_cadence)) if len(positive_cadence) else 0.0
    selected_width = float(fit_input.xmax - fit_input.xmin)
    return ExtremumPchipAnchor(
        anchor_jd=anchor_jd,
        anchor_mag=anchor_mag,
        observed_extreme_jd=observed_extreme_jd,
        observed_extreme_mag=observed_extreme_mag,
        time_tolerance=max(2.5 * median_cadence, 0.15 * selected_width),
        mag_tolerance=max(EXTREMUM_VERTEX_EXTREME_MAG_TOLERANCE_FLOOR, quality_vertex_mag_tolerance(fit_input, parabola)),
        left_points=left_points,
        right_points=right_points,
    )


def quality_vertex_mag_tolerance(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
) -> float:
    """Return the broad magnitude plausibility tolerance used for model anchors."""

    y = fit_input.y[parabola.inlier_mask]
    if len(y) == 0:
        return EXTREMUM_VERTEX_EXTREME_MAG_TOLERANCE_FLOOR
    y_span = float(np.max(y) - np.min(y))
    return max(EXTREMUM_VERTEX_EXTREME_MAG_TOLERANCE_FLOOR, 0.25 * y_span)

def _parabola_curve_candidate(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    quality: ExtremumFitQualityMetrics,
) -> ExtremumCurveCandidate:
    """Expose the reference parabola through the common candidate contract."""

    vertex_jd_error = float("nan")
    vertex_mag_error = float("nan")
    a, b, c = parabola.coefficients
    try:
        vertex_gradient = np.array(
            [b / (2.0 * a * a), -1.0 / (2.0 * a), 0.0],
            dtype=np.float64,
        )
        vertex_variance = float(vertex_gradient @ parabola.covariance @ vertex_gradient)
        if vertex_variance >= 0:
            vertex_jd_error = float(np.sqrt(vertex_variance))
        magnitude_gradient = np.array(
            [parabola.vertex_centered**2, parabola.vertex_centered, 1.0],
            dtype=np.float64,
        )
        magnitude_variance = float(
            magnitude_gradient @ parabola.covariance @ magnitude_gradient
        )
        if magnitude_variance >= 0:
            vertex_mag_error = float(np.sqrt(magnitude_variance))
    except Exception:
        pass
    fit_jd = np.linspace(
        float(np.min(fit_input.x)),
        float(np.max(fit_input.x)),
        EXTREMUM_SPLINE_DENSE_SAMPLES,
    )
    centered = fit_jd - parabola.x0
    fit_mag = a * centered**2 + b * centered + c
    return ExtremumCurveCandidate(
        model_name="parabola",
        parameter_count=3,
        vertex_jd=float(parabola.vertex_jd),
        vertex_mag=float(parabola.vertex_mag),
        vertex_jd_error=vertex_jd_error,
        vertex_mag_error=vertex_mag_error,
        fit_plot_jd=tuple(float(value) for value in fit_jd),
        fit_plot_mag=tuple(float(value) for value in fit_mag),
        rms=float(quality.rms),
        weighted_rms=float(quality.weighted_rms),
        left_points=int(len(quality.left_x)),
        right_points=int(len(quality.right_x)),
        left_coverage=float(quality.left_coverage),
        right_coverage=float(quality.right_coverage),
        metadata={},
    )




def _bootstrap_weighted_linear_solution(
    design: object,
    values: object,
    sigma: object,
) -> tuple[object, float] | None:
    """Solve a lightweight weighted linear fit for one bootstrap replicate."""

    design_array = np.asarray(design, dtype=np.float64)
    values_array = np.asarray(values, dtype=np.float64)
    sigma_array = np.asarray(sigma, dtype=np.float64)
    try:
        weighted_design = design_array / sigma_array[:, None]
        weighted_values = values_array / sigma_array
        coefficients, _residuals, rank, _singular = np.linalg.lstsq(
            weighted_design,
            weighted_values,
            rcond=None,
        )
    except Exception:
        return None
    if int(rank) != design_array.shape[1]:
        return None
    residuals = values_array - design_array @ coefficients
    chi_square = float(np.sum((residuals / sigma_array) ** 2))
    if not np.isfinite(chi_square):
        return None
    return coefficients, chi_square


def _bootstrap_piecewise_extremum(
    model_name: str,
    centered_minutes: object,
    values: object,
    sigma: object,
    candidate: ExtremumCurveCandidate,
    expected_sign: float,
) -> tuple[float, float] | None:
    """Refit AP or PS, including both knot positions, for one bootstrap sample."""

    time_values = np.asarray(centered_minutes, dtype=np.float64)
    sample_values = np.asarray(values, dtype=np.float64)
    sigma_values = np.asarray(sigma, dtype=np.float64)
    left_knot_jd = candidate.metadata.get("left_knot_jd")
    right_knot_jd = candidate.metadata.get("right_knot_jd")
    if left_knot_jd is None or right_knot_jd is None:
        return None
    x0 = float(candidate.metadata.get("bootstrap_x0_jd", float("nan")))
    if not np.isfinite(x0):
        return None
    initial_left = (float(left_knot_jd) - x0) * 1440.0
    initial_right = (float(right_knot_jd) - x0) * 1440.0

    if model_name == "asymptotic_parabola":
        design_function = _asymptotic_parabola_design
        minimum_outer = EXTREMUM_MODEL_MIN_OUTER_POINTS
    elif model_name == "parabolic_spline":
        design_function = _parabolic_spline_design
        minimum_outer = EXTREMUM_PARABOLIC_SPLINE_MIN_OUTER_POINTS
    else:
        return None
    if len(time_values) < 2 * minimum_outer + 3:
        return None

    def solve_knots(left_knot: float, right_knot: float):
        if (
            not np.isfinite(left_knot)
            or not np.isfinite(right_knot)
            or left_knot >= right_knot
            or int(np.count_nonzero(time_values < left_knot)) < minimum_outer
            or int(np.count_nonzero(time_values > right_knot)) < minimum_outer
            or int(
                np.count_nonzero(
                    (time_values >= left_knot) & (time_values <= right_knot)
                )
            )
            < 1
        ):
            return None
        solved = _bootstrap_weighted_linear_solution(
            design_function(time_values, left_knot, right_knot),
            sample_values,
            sigma_values,
        )
        if solved is None:
            return None
        coefficients, chi_square = solved
        if model_name == "asymptotic_parabola":
            curvature = float(coefficients[1])
            linear = float(coefficients[2])
            midpoint = 0.5 * (left_knot + right_knot)
            vertex_minutes = midpoint - linear / (2.0 * curvature)
            vertex_mag = float(
                coefficients[0] - linear * linear / (4.0 * curvature)
            )
        else:
            curvature = float(coefficients[2])
            linear = float(coefficients[1])
            vertex_minutes = -linear / (2.0 * curvature)
            vertex_mag = float(
                coefficients[0] - linear * linear / (4.0 * curvature)
            )
        if (
            not np.isfinite(curvature)
            or expected_sign * curvature <= 0
            or not np.isfinite(vertex_minutes)
            or not left_knot <= vertex_minutes <= right_knot
            or not np.isfinite(vertex_mag)
        ):
            return None
        return chi_square, float(vertex_minutes), vertex_mag

    left_bounds = (
        float(time_values[minimum_outer - 1]),
        float(time_values[-minimum_outer - 2]),
    )
    right_bounds = (
        float(time_values[minimum_outer + 1]),
        float(time_values[-minimum_outer]),
    )

    def knot_objective(values_pair: object) -> float:
        knots = np.asarray(values_pair, dtype=np.float64)
        solved = solve_knots(float(knots[0]), float(knots[1]))
        return (
            EXTREMUM_MODEL_INVALID_OBJECTIVE_PENALTY
            if solved is None
            else float(solved[0])
        )

    initial = solve_knots(initial_left, initial_right)
    if initial is None:
        return None
    refined_left, refined_right = _refine_piecewise_knots(
        knot_objective,
        (initial_left, initial_right),
        (left_bounds, right_bounds),
        max_iterations=EXTREMUM_MODEL_BOOTSTRAP_KNOT_MAX_ITERATIONS,
    )
    refined = solve_knots(refined_left, refined_right)
    if refined is None or refined[0] > initial[0]:
        refined = initial
    return float(refined[1]), float(refined[2])


def _bootstrap_refit_extremum_candidate(
    candidate: ExtremumCurveCandidate,
    x: object,
    values: object,
    sigma: object,
    expected_sign: float,
) -> tuple[float, float] | None:
    """Return the refitted vertex for one candidate and bootstrap sample."""

    x_values = np.asarray(x, dtype=np.float64)
    sample_values = np.asarray(values, dtype=np.float64)
    sigma_values = np.asarray(sigma, dtype=np.float64)
    x0 = float(np.mean(x_values))
    if candidate.model_name == "parabola":
        centered_days = x_values - x0
        try:
            coefficients = np.polyfit(
                centered_days,
                sample_values,
                2,
                w=1.0 / sigma_values,
            )
        except Exception:
            return None
        curvature, linear, constant = (
            float(coefficients[0]),
            float(coefficients[1]),
            float(coefficients[2]),
        )
        if not np.isfinite(curvature) or expected_sign * curvature <= 0:
            return None
        vertex_centered = -linear / (2.0 * curvature)
        return (
            float(x0 + vertex_centered),
            float(
                curvature * vertex_centered * vertex_centered
                + linear * vertex_centered
                + constant
            ),
        )
    if candidate.model_name in {"asymptotic_parabola", "parabolic_spline"}:
        bootstrap_metadata = dict(candidate.metadata)
        bootstrap_metadata["bootstrap_x0_jd"] = x0
        bootstrap_candidate = ExtremumCurveCandidate(
            model_name=candidate.model_name,
            parameter_count=candidate.parameter_count,
            vertex_jd=candidate.vertex_jd,
            vertex_mag=candidate.vertex_mag,
            vertex_jd_error=candidate.vertex_jd_error,
            vertex_mag_error=candidate.vertex_mag_error,
            fit_plot_jd=candidate.fit_plot_jd,
            fit_plot_mag=candidate.fit_plot_mag,
            rms=candidate.rms,
            weighted_rms=candidate.weighted_rms,
            left_points=candidate.left_points,
            right_points=candidate.right_points,
            left_coverage=candidate.left_coverage,
            right_coverage=candidate.right_coverage,
            metadata=bootstrap_metadata,
        )
        centered_minutes = (x_values - x0) * 1440.0
        piecewise = _bootstrap_piecewise_extremum(
            candidate.model_name,
            centered_minutes,
            sample_values,
            sigma_values,
            bootstrap_candidate,
            expected_sign,
        )
        if piecewise is None:
            return None
        return float(x0 + piecewise[0] / 1440.0), float(piecewise[1])
    if candidate.model_name == "robust_gcv_spline":
        try:
            spline, fitted_x, _y, _errors, center, span, *_rest = (
                _fit_robust_gcv_spline_curve(x_values, sample_values, sigma_values)
            )
            extrema = _robust_gcv_spline_extrema(spline, center, span)
        except Exception:
            return None
        matching = [item for item in extrema if expected_sign * item[2] > 0]
        if not matching:
            return None
        selected = (
            min(matching, key=lambda item: item[1])
            if expected_sign > 0
            else max(matching, key=lambda item: item[1])
        )
        if not float(fitted_x[0]) < selected[0] < float(fitted_x[-1]):
            return None
        return float(selected[0]), float(selected[1])
    return None


def estimate_extremum_candidate_bootstrap(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    candidate: ExtremumCurveCandidate,
) -> dict[str, object]:
    """Estimate extremum accuracy by deterministic full-refit bootstrap."""

    inlier_mask = (
        np.ones(len(fit_input.x), dtype=bool)
        if candidate.model_name == "robust_gcv_spline"
        else np.asarray(parabola.inlier_mask, dtype=bool)
    )
    inlier_x = np.asarray(fit_input.x[inlier_mask], dtype=np.float64)
    inlier_y = np.asarray(fit_input.y[inlier_mask], dtype=np.float64)
    inlier_yerr = np.asarray(fit_input.yerr[inlier_mask], dtype=np.float64)
    if candidate.model_name == "robust_gcv_spline":
        valid_errors = np.isfinite(inlier_yerr) & (inlier_yerr > 0)
        typical_error = (
            float(np.median(inlier_yerr[valid_errors]))
            if np.any(valid_errors)
            else 1.0
        )
        sigma = np.where(valid_errors, inlier_yerr, typical_error)
    else:
        sigma = np.asarray(parabola.sigma[inlier_mask], dtype=np.float64)
    sort_order = np.argsort(inlier_x)
    inlier_x = inlier_x[sort_order]
    inlier_y = inlier_y[sort_order]
    sigma = sigma[sort_order]
    inlier_yerr = inlier_yerr[sort_order]
    model_values = np.interp(
        inlier_x,
        np.asarray(candidate.fit_plot_jd, dtype=np.float64),
        np.asarray(candidate.fit_plot_mag, dtype=np.float64),
    )
    residuals = inlier_y - model_values
    degrees_of_freedom = max(len(inlier_x) - candidate.parameter_count, 1)
    has_reported_errors = bool(
        np.any(np.isfinite(inlier_yerr) & (inlier_yerr > 0))
    )
    reduced_chi_square = float("nan")
    if has_reported_errors:
        if candidate.model_name == "robust_gcv_spline":
            residual_scale = float(
                candidate.metadata.get("robust_residual_scale", float("nan"))
            )
            residual_floor = (
                residual_scale
                if np.isfinite(residual_scale) and residual_scale > 0
                else 0.0
            )
            noise_sigma = np.maximum(sigma, residual_floor)
            noise_multiplier = float(np.median(noise_sigma / sigma))
            noise_method = "reported_errors_with_robust_residual_floor"
        else:
            reduced_chi_square = float(
                np.sum((residuals / sigma) ** 2) / degrees_of_freedom
            )
            noise_multiplier = max(
                1.0,
                float(np.sqrt(reduced_chi_square))
                if np.isfinite(reduced_chi_square) and reduced_chi_square > 0
                else 1.0,
            )
            noise_sigma = sigma * noise_multiplier
            noise_method = "reported_errors_scaled_by_reduced_chi_square"
    else:
        centered_residuals = residuals - float(np.median(residuals))
        residual_rms = float(np.sqrt(np.mean(centered_residuals**2)))
        residual_mad_sigma = 1.4826 * float(
            np.median(np.abs(centered_residuals))
        )
        noise_scale = max(
            residual_rms if np.isfinite(residual_rms) else 0.0,
            residual_mad_sigma if np.isfinite(residual_mad_sigma) else 0.0,
            1e-4,
        )
        noise_sigma = np.full(len(inlier_x), noise_scale, dtype=np.float64)
        noise_multiplier = 1.0
        noise_method = "residual_scatter"

    expected_sign = 1.0 if parabola.coefficients[0] > 0 else -1.0
    rng = np.random.default_rng(EXTREMUM_MODEL_BOOTSTRAP_RANDOM_SEED)
    sample_jd: list[float] = []
    sample_mag: list[float] = []
    selected_width = float(fit_input.xmax - fit_input.xmin)
    minimum_side_coverage = (
        EXTREMUM_MINIMUM_SIDE_COVERAGE_FRACTION * selected_width
    )
    for _sample_index in range(EXTREMUM_MODEL_BOOTSTRAP_SAMPLES):
        sampled_y = model_values + expected_sign * rng.normal(0.0, noise_sigma)
        refitted = _bootstrap_refit_extremum_candidate(
            candidate,
            inlier_x,
            sampled_y,
            sigma,
            expected_sign,
        )
        if refitted is None:
            continue
        vertex_jd, vertex_mag = refitted
        side_support = _candidate_side_support(inlier_x, vertex_jd)
        if (
            side_support is None
            or not fit_input.xmin <= vertex_jd <= fit_input.xmax
            or side_support[0] < EXTREMUM_MINIMUM_SIDE_POINTS
            or side_support[1] < EXTREMUM_MINIMUM_SIDE_POINTS
            or side_support[2] < minimum_side_coverage
            or side_support[3] < minimum_side_coverage
            or not np.isfinite(vertex_mag)
        ):
            continue
        sample_jd.append(float(vertex_jd))
        sample_mag.append(float(vertex_mag))

    success_count = len(sample_jd)
    success_fraction = success_count / EXTREMUM_MODEL_BOOTSTRAP_SAMPLES
    minimum_success_count = max(
        2,
        int(
            np.ceil(
                EXTREMUM_MODEL_BOOTSTRAP_MIN_SUCCESS_FRACTION
                * EXTREMUM_MODEL_BOOTSTRAP_SAMPLES
            )
        ),
    )
    stable = success_count >= minimum_success_count
    if success_count:
        jd_array = np.asarray(sample_jd, dtype=np.float64)
        mag_array = np.asarray(sample_mag, dtype=np.float64)
        jd_deltas = jd_array - candidate.vertex_jd
        mag_deltas = mag_array - candidate.vertex_mag
        timing_error = float(np.sqrt(np.mean(jd_deltas**2)))
        magnitude_error = float(np.sqrt(np.mean(mag_deltas**2)))
        timing_bias = float(np.mean(jd_deltas))
        magnitude_bias = float(np.mean(mag_deltas))
        jd_low, jd_high = (
            float(value) for value in np.percentile(jd_array, [16.0, 84.0])
        )
    else:
        timing_error = float("nan")
        magnitude_error = float("nan")
        timing_bias = float("nan")
        magnitude_bias = float("nan")
        jd_low = float("nan")
        jd_high = float("nan")
    return {
        "bootstrap_method": "parametric_full_refit",
        "bootstrap_samples": EXTREMUM_MODEL_BOOTSTRAP_SAMPLES,
        "bootstrap_successes": success_count,
        "bootstrap_success_fraction": success_fraction,
        "bootstrap_stable": stable,
        "bootstrap_timing_error": timing_error,
        "bootstrap_timing_error_seconds": timing_error * 86400.0,
        "bootstrap_timing_bias": timing_bias,
        "bootstrap_magnitude_error": magnitude_error,
        "bootstrap_magnitude_bias": magnitude_bias,
        "bootstrap_jd_p16": jd_low,
        "bootstrap_jd_p84": jd_high,
        "bootstrap_noise_method": noise_method,
        "bootstrap_noise_multiplier": noise_multiplier,
        "bootstrap_noise_sigma_median": float(np.median(noise_sigma)),
        "bootstrap_reduced_chi_square": reduced_chi_square,
        "bootstrap_input_points": int(len(inlier_x)),
    }


def fit_extremum_curve_candidates(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    quality: ExtremumFitQualityMetrics,
) -> tuple[tuple[ExtremumCurveCandidate, ...], tuple[dict[str, object], ...]]:
    """Calculate models without deciding whether the selected data are usable."""

    candidates: list[ExtremumCurveCandidate] = [
        _parabola_curve_candidate(fit_input, parabola, quality)
    ]
    availability: list[dict[str, object]] = []
    model_fitters = (
        (
            "asymptotic_parabola",
            fit_extremum_asymptotic_parabola_candidate,
            EXTREMUM_ASYMPTOTIC_PARABOLA_MIN_POINTS,
        ),
        (
            "parabolic_spline",
            fit_extremum_parabolic_spline_candidate,
            EXTREMUM_PARABOLIC_SPLINE_MIN_POINTS,
        ),
    )
    for model_name, fitter, minimum_points in model_fitters:
        candidate = fitter(fit_input, parabola)
        if candidate is None:
            availability.append(
                {
                    "model": model_name,
                    "accepted": False,
                    "decision": "unavailable",
                    "reason": (
                        "model fit unavailable or insufficiently supported "
                        f"(minimum points: {minimum_points})"
                    ),
                }
            )
        else:
            candidates.append(candidate)

    robust_spline = fit_extremum_robust_gcv_spline_candidate(fit_input, parabola)
    if robust_spline is None:
        availability.append(
            {
                "model": "robust_gcv_spline",
                "accepted": False,
                "decision": "unavailable",
                "reason": "automatic robust GCV spline unavailable or unsupported",
            }
        )
    else:
        candidates.append(robust_spline)
    return tuple(candidates), tuple(availability)




def _extremum_candidate_information_score(
    candidate: ExtremumCurveCandidate,
    point_count: int,
) -> float:
    """Return a BIC-style score that penalizes unnecessary flexibility."""

    if (
        point_count <= 0
        or not np.isfinite(candidate.weighted_rms)
        or candidate.weighted_rms < 0
    ):
        return float("inf")
    residual_variance = max(
        candidate.weighted_rms * candidate.weighted_rms,
        EXTREMUM_MODEL_SELECTION_EPSILON,
    )
    return float(
        point_count * np.log(residual_variance)
        + candidate.parameter_count * np.log(point_count)
    )


def validate_extremum_candidate(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    quality: ExtremumFitQualityMetrics,
    candidate: ExtremumCurveCandidate,
    anchor: ExtremumPchipAnchor | None,
) -> dict[str, object]:
    """Apply the same mathematical plausibility rules to every curve model."""

    extremum_type = "Maximum" if parabola.coefficients[0] > 0 else "Minimum"
    observed = observed_extreme_metrics(
        fit_input,
        parabola.inlier_mask,
        extremum_type,
        candidate.vertex_mag,
    )
    if anchor is not None:
        inlier_x = np.asarray(fit_input.x[parabola.inlier_mask], dtype=np.float64)
        inlier_yerr = np.asarray(
            fit_input.yerr[parabola.inlier_mask],
            dtype=np.float64,
        )
        observed_index = int(np.argmin(np.abs(inlier_x - anchor.observed_extreme_jd)))
        observed_error = float(inlier_yerr[observed_index])
        if not np.isfinite(observed_error) or observed_error <= 0:
            observed_error = 0.0
        if extremum_type == "Maximum":
            vertex_extreme_delta = (
                candidate.vertex_mag - anchor.observed_extreme_mag
            )
            extreme_kind = "brightest local"
        else:
            vertex_extreme_delta = (
                anchor.observed_extreme_mag - candidate.vertex_mag
            )
            extreme_kind = "faintest local"
        observed = {
            "observed_extreme_jd": anchor.observed_extreme_jd,
            "observed_extreme_mag": anchor.observed_extreme_mag,
            "observed_extreme_error": observed_error,
            "vertex_extreme_delta": float(vertex_extreme_delta),
            "vertex_extreme_tolerance": max(
                EXTREMUM_VERTEX_EXTREME_MAG_TOLERANCE_FLOOR,
                EXTREMUM_VERTEX_EXTREME_ERROR_FACTOR * observed_error,
            ),
            "extreme_kind": extreme_kind,
        }
    if anchor is None:
        anchor_jd = float(observed["observed_extreme_jd"])
        anchor_mag = float(observed["observed_extreme_mag"])
        sorted_x = np.sort(np.asarray(fit_input.x, dtype=np.float64))
        positive_cadence = np.diff(sorted_x)
        positive_cadence = positive_cadence[positive_cadence > 0]
        median_cadence = (
            float(np.median(positive_cadence))
            if len(positive_cadence)
            else 0.0
        )
        anchor_time_tolerance = max(
            2.5 * median_cadence,
            0.15 * float(fit_input.xmax - fit_input.xmin),
        )
        anchor_mag_tolerance = quality.vertex_mag_tolerance
    else:
        anchor_jd = float(anchor.anchor_jd)
        anchor_mag = float(anchor.anchor_mag)
        anchor_time_tolerance = float(anchor.time_tolerance)
        anchor_mag_tolerance = float(anchor.mag_tolerance)
    if np.isfinite(candidate.rms):
        anchor_mag_tolerance = max(
            anchor_mag_tolerance,
            3.0 * float(candidate.rms),
        )

    anchor_time_delta = abs(candidate.vertex_jd - anchor_jd)
    anchor_mag_delta = abs(candidate.vertex_mag - anchor_mag)
    underfits_observed_extreme = (
        float(observed["vertex_extreme_delta"])
        > float(observed["vertex_extreme_tolerance"])
    )
    if extremum_type == "Maximum":
        overshoots_observed_extreme = (
            candidate.vertex_mag < anchor_mag - anchor_mag_tolerance
        )
    else:
        overshoots_observed_extreme = (
            candidate.vertex_mag > anchor_mag + anchor_mag_tolerance
        )
    vertex_in_range = bool(
        np.isfinite(candidate.vertex_jd)
        and fit_input.xmin <= candidate.vertex_jd <= fit_input.xmax
    )
    magnitude_in_range = bool(
        np.isfinite(candidate.vertex_mag)
        and candidate.vertex_mag >= quality.inlier_y_min - quality.vertex_mag_tolerance
        and candidate.vertex_mag <= quality.inlier_y_max + quality.vertex_mag_tolerance
    )
    selected_width = float(fit_input.xmax - fit_input.xmin)
    minimum_side_coverage = EXTREMUM_MINIMUM_SIDE_COVERAGE_FRACTION * selected_width
    side_support_ok = bool(
        candidate.left_points >= EXTREMUM_MINIMUM_SIDE_POINTS
        and candidate.right_points >= EXTREMUM_MINIMUM_SIDE_POINTS
        and candidate.left_coverage >= minimum_side_coverage
        and candidate.right_coverage >= minimum_side_coverage
    )
    sorted_x = np.unique(np.sort(np.asarray(fit_input.x, dtype=np.float64)))
    positive_cadence = np.diff(sorted_x)
    positive_cadence = positive_cadence[positive_cadence > 0]
    median_cadence = (
        float(np.median(positive_cadence)) if len(positive_cadence) else 0.0
    )
    insertion_index = int(np.searchsorted(sorted_x, candidate.vertex_jd))
    if 0 < insertion_index < len(sorted_x) and median_cadence > 0:
        vertex_gap = float(
            sorted_x[insertion_index] - sorted_x[insertion_index - 1]
        )
        vertex_gap_cadences = vertex_gap / median_cadence
    else:
        vertex_gap = float("nan")
        vertex_gap_cadences = float("inf")
    vertex_gap_ok = bool(
        np.isfinite(vertex_gap_cadences)
        and vertex_gap_cadences <= EXTREMUM_MAX_VERTEX_GAP_CADENCES
    )
    valid_shape_errors = parabola.sigma[
        parabola.inlier_mask
        & np.isfinite(parabola.sigma)
        & (parabola.sigma > 0)
    ]
    median_shape_error = (
        float(np.median(valid_shape_errors))
        if len(valid_shape_errors)
        else 0.0
    )
    edge_excursion_minimum = max(
        EXTREMUM_MINIMUM_PROMINENCE_FLOOR,
        candidate.rms if np.isfinite(candidate.rms) else 0.0,
        median_shape_error,
    )
    shape = spline_shape_metrics(candidate, edge_excursion_minimum)
    direction = spline_wrong_direction_metrics(candidate, extremum_type)
    single_extremum = bool(
        int(shape["spline_relevant_extremum_count"])
        == EXTREMUM_SPLINE_REQUIRED_EXTREMUM_COUNT
    )
    direction_ok = bool(
        len(fit_input.x) < EXTREMUM_SPLINE_WRONG_DIRECTION_MIN_POINTS
        or float(direction["spline_wrong_direction_fraction"])
        <= EXTREMUM_SPLINE_MAX_WRONG_DIRECTION_FRACTION
    )
    anchor_time_ok = bool(
        np.isfinite(anchor_time_delta)
        and np.isfinite(anchor_time_tolerance)
        and anchor_time_delta <= anchor_time_tolerance
    )
    anchor_validation = "required"
    if candidate.model_name == "robust_gcv_spline":
        # The selected spline is itself the form model.  A noisy local PCHIP
        # remains useful diagnostics, but must not veto a single, well-supported
        # spline extremum merely because its vertex is slightly displaced.
        anchor_time_ok = True
        anchor_validation = "diagnostic_only"
    anchor_mag_ok = bool(
        np.isfinite(anchor_mag_delta)
        and np.isfinite(anchor_mag_tolerance)
        and anchor_mag_delta <= anchor_mag_tolerance
    )
    accepted = bool(
        vertex_in_range
        and magnitude_in_range
        and side_support_ok
        and vertex_gap_ok
        and single_extremum
        and direction_ok
        and anchor_time_ok
        and anchor_mag_ok
    )
    reasons: list[str] = []
    if not vertex_in_range:
        reasons.append("vertex outside selected range")
    if not magnitude_in_range:
        reasons.append("vertex magnitude outside measured range")
    if not side_support_ok:
        reasons.append("insufficient flank support")
    if not vertex_gap_ok:
        reasons.append("extremum lies in an unobserved time gap")
    if not single_extremum:
        reasons.append("not exactly one significant interior extremum")
    if not direction_ok:
        reasons.append("wrong-direction flank structure")
    if not anchor_time_ok:
        reasons.append("extremum too far from shape-preserving anchor")
    if not anchor_mag_ok:
        reasons.append("extremum magnitude too far from shape-preserving anchor")
    information_score = _extremum_candidate_information_score(
        candidate,
        int(np.count_nonzero(parabola.inlier_mask)),
    )
    return {
        "model": candidate.model_name,
        "accepted": accepted,
        "decision": "candidate" if accepted else "rejected",
        "reason": "valid candidate" if accepted else "; ".join(reasons),
        "parameter_count": candidate.parameter_count,
        "information_score": information_score,
        "vertex_jd": candidate.vertex_jd,
        "vertex_mag": candidate.vertex_mag,
        "vertex_jd_error": candidate.vertex_jd_error,
        "vertex_mag_error": candidate.vertex_mag_error,
        "rms": candidate.rms,
        "weighted_rms": candidate.weighted_rms,
        "left_points": candidate.left_points,
        "right_points": candidate.right_points,
        "left_coverage": candidate.left_coverage,
        "right_coverage": candidate.right_coverage,
        "minimum_side_coverage": minimum_side_coverage,
        "observed_extreme_jd": observed["observed_extreme_jd"],
        "observed_extreme_mag": observed["observed_extreme_mag"],
        "observed_point_mag_delta": observed["vertex_extreme_delta"],
        "observed_point_noise_scale": observed["vertex_extreme_tolerance"],
        "underfits_single_observed_point": underfits_observed_extreme,
        "overshoots_single_observed_point": overshoots_observed_extreme,
        "anchor_jd": anchor_jd,
        "anchor_mag": anchor_mag,
        "anchor_time_delta": anchor_time_delta,
        "anchor_time_tolerance": anchor_time_tolerance,
        "anchor_mag_delta": anchor_mag_delta,
        "anchor_mag_tolerance": anchor_mag_tolerance,
        "anchor_mag_ok": anchor_mag_ok,
        "anchor_validation": anchor_validation,
        "vertex_in_range": vertex_in_range,
        "magnitude_in_range": magnitude_in_range,
        "side_support_ok": side_support_ok,
        "vertex_gap": vertex_gap,
        "vertex_gap_cadences": vertex_gap_cadences,
        "maximum_vertex_gap_cadences": EXTREMUM_MAX_VERTEX_GAP_CADENCES,
        "vertex_gap_ok": vertex_gap_ok,
        "single_extremum": single_extremum,
        "direction_ok": direction_ok,
        **shape,
        **direction,
        **candidate.metadata,
    }


def select_extremum_candidate(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    quality: ExtremumFitQualityMetrics,
    preferred_anchor_jd: float | None = None,
    required_model_name: str | None = None,
) -> tuple[
    ExtremumCurveCandidate | None,
    tuple[dict[str, object], ...],
    ExtremumPchipAnchor | None,
]:
    """Validate all fitted models uniformly, then select the best valid one."""

    anchor = fit_extremum_pchip_anchor(
        fit_input,
        parabola,
        preferred_jd=preferred_anchor_jd,
    )
    if required_model_name is None:
        candidates, unavailable_checks = fit_extremum_curve_candidates(
            fit_input,
            parabola,
            quality,
        )
    elif required_model_name in {
        "parabola",
        "asymptotic_parabola",
        "parabolic_spline",
        "robust_gcv_spline",
    }:
        required_fitters = {
            "parabola": lambda: _parabola_curve_candidate(
                fit_input, parabola, quality
            ),
            "asymptotic_parabola": lambda: (
                fit_extremum_asymptotic_parabola_candidate(fit_input, parabola)
            ),
            "parabolic_spline": lambda: (
                fit_extremum_parabolic_spline_candidate(fit_input, parabola)
            ),
            "robust_gcv_spline": lambda: (
                fit_extremum_robust_gcv_spline_candidate(fit_input, parabola)
            ),
        }
        required_candidate = required_fitters[required_model_name]()
        candidates = (() if required_candidate is None else (required_candidate,))
        unavailable_checks = (
            (
                {
                    "model": required_model_name,
                    "accepted": False,
                    "decision": "unavailable",
                    "reason": (
                        f"requested model {required_model_name} unavailable or "
                        "insufficiently supported"
                    ),
                },
            )
            if required_candidate is None
            else ()
        )
    else:
        raise ValueError(f"unsupported required extremum model: {required_model_name}")
    checks: list[dict[str, object]] = []
    valid: list[
        tuple[float, int, int, ExtremumCurveCandidate, dict[str, object]]
    ] = []
    for order, candidate in enumerate(candidates):
        check = validate_extremum_candidate(
            fit_input,
            parabola,
            quality,
            candidate,
            anchor,
        )
        checks.append(check)
        if bool(check["accepted"]):
            bootstrap = estimate_extremum_candidate_bootstrap(
                fit_input,
                parabola,
                candidate,
            )
            check.update(bootstrap)
            timing_error = float(
                bootstrap.get("bootstrap_timing_error", float("nan"))
            )
            anchor_time_delta = float(
                check.get("anchor_time_delta", float("nan"))
            )
            timing_accuracy = (
                float(np.hypot(timing_error, anchor_time_delta))
                if np.isfinite(timing_error) and np.isfinite(anchor_time_delta)
                else float("nan")
            )
            check["bootstrap_timing_accuracy"] = timing_accuracy
            check["bootstrap_timing_accuracy_seconds"] = (
                timing_accuracy * 86400.0
            )
            selection_eligible = bool(
                required_model_name is not None
                or (
                    bootstrap.get("bootstrap_stable")
                    and np.isfinite(timing_accuracy)
                    and timing_accuracy >= 0
                )
            )
            check["selection_eligible"] = selection_eligible
            check["manual_model_bootstrap_warning"] = bool(
                required_model_name is not None
                and not bootstrap.get("bootstrap_stable")
            )
            if selection_eligible:
                valid.append(
                    (
                        (
                            max(timing_accuracy, EXTREMUM_MODEL_SELECTION_EPSILON)
                            if np.isfinite(timing_accuracy)
                            else float("inf")
                        ),
                        candidate.parameter_count,
                        order,
                        candidate,
                        check,
                    )
                )
            else:
                check["decision"] = "uncertainty_unavailable"
                check["reason"] = (
                    "valid curve but bootstrap timing uncertainty is unstable"
                )
    checks.extend(unavailable_checks)
    if not valid:
        return None, tuple(checks), anchor
    best_timing_error = min(item[0] for item in valid)
    equivalent_limit = max(
        best_timing_error * EXTREMUM_MODEL_BOOTSTRAP_EQUIVALENCE_RATIO,
        best_timing_error + EXTREMUM_MODEL_SELECTION_EPSILON,
    )
    preferred_gcv = next(
        (
            item
            for item in valid
            if required_model_name is None
            and item[3].model_name == "robust_gcv_spline"
        ),
        None,
    )
    if preferred_gcv is not None:
        (
            selected_timing_error,
            _parameter_count,
            _order,
            selected,
            selected_check,
        ) = preferred_gcv
        automatic_selection_metric = "preferred_stable_robust_gcv_spline"
    else:
        equivalent = [item for item in valid if item[0] <= equivalent_limit]
        (
            selected_timing_error,
            _parameter_count,
            _order,
            selected,
            selected_check,
        ) = min(
            equivalent,
            key=lambda item: (item[1], item[0], item[2]),
        )
        automatic_selection_metric = "bootstrap_timing_accuracy"
    for check in checks:
        check["selection_metric"] = (
            "manual_model"
            if required_model_name is not None
            else automatic_selection_metric
        )
        check["best_bootstrap_timing_accuracy"] = best_timing_error
        check["bootstrap_equivalence_limit"] = equivalent_limit
        if check is selected_check:
            check["decision"] = "selected"
            if required_model_name is not None:
                check["reason"] = (
                    "requested model passed geometry checks; bootstrap timing is unstable"
                    if check.get("manual_model_bootstrap_warning")
                    else "requested model passed geometry and stability checks"
                )
            elif preferred_gcv is not None:
                check["reason"] = "preferred stable robust GCV spline"
            elif selected_timing_error <= best_timing_error:
                check["reason"] = "smallest stable bootstrap timing accuracy"
            else:
                check["reason"] = (
                    "simplest model within the bootstrap timing-accuracy "
                    "equivalence range"
                )
        elif check.get("selection_eligible"):
            check["decision"] = "not_selected"
            check["reason"] = (
                "valid but stable robust GCV spline is preferred"
                if preferred_gcv is not None
                else (
                    "valid but outside the selected bootstrap timing-accuracy "
                    "rule"
                )
            )
    return selected, tuple(checks), anchor


def measure_extremum_support_quality(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    reference_jd: float,
    reference_mag: float,
) -> ExtremumFitQualityMetrics:
    """Measure selection support around a shape anchor, before model choice."""

    x = np.asarray(fit_input.x, dtype=np.float64)
    y = np.asarray(fit_input.y, dtype=np.float64)
    inlier_mask = np.asarray(parabola.inlier_mask, dtype=bool)
    inlier_x = x[inlier_mask]
    left_x = inlier_x[inlier_x < reference_jd]
    right_x = inlier_x[inlier_x > reference_jd]
    if len(left_x) == 0 or len(right_x) == 0:
        raise ValueError("reference extremum lacks one flank")
    left_coverage = float(reference_jd - np.min(left_x))
    right_coverage = float(np.max(right_x) - reference_jd)
    time_balance = (
        min(left_coverage, right_coverage) / max(left_coverage, right_coverage)
        if max(left_coverage, right_coverage) > 0
        else 0.0
    )
    a, b, c = parabola.coefficients
    centered_x = x - parabola.x0
    fitted_y = a * centered_x**2 + b * centered_x + c
    residuals = y[inlier_mask] - fitted_y[inlier_mask]
    rms = float(np.sqrt(np.mean(residuals**2)))
    weighted_rms = float(
        np.sqrt(
            np.average(
                residuals**2,
                weights=1.0 / parabola.sigma[inlier_mask] ** 2,
            )
        )
    )

    def fitted_mag_at(jd_value: float) -> float:
        centered = jd_value - parabola.x0
        return float(a * centered**2 + b * centered + c)

    left_edge_mag = fitted_mag_at(float(np.min(left_x)))
    right_edge_mag = fitted_mag_at(float(np.max(right_x)))
    if a > 0:
        left_prominence = left_edge_mag - reference_mag
        right_prominence = right_edge_mag - reference_mag
    else:
        left_prominence = reference_mag - left_edge_mag
        right_prominence = reference_mag - right_edge_mag
    left_prominence = max(0.0, float(left_prominence))
    right_prominence = max(0.0, float(right_prominence))
    minimum_prominence = max(
        EXTREMUM_MINIMUM_PROMINENCE_FLOOR,
        EXTREMUM_MINIMUM_PROMINENCE_RMS_FACTOR * rms,
    )
    maximum_prominence = max(left_prominence, right_prominence)
    prominence_balance = (
        min(left_prominence, right_prominence) / maximum_prominence
        if maximum_prominence > 0
        else 0.0
    )
    inlier_y = y[inlier_mask]
    inlier_y_min = float(np.min(inlier_y))
    inlier_y_max = float(np.max(inlier_y))
    inlier_y_span = inlier_y_max - inlier_y_min
    vertex_mag_tolerance = max(0.05, 2.0 * rms, 0.25 * inlier_y_span)
    return ExtremumFitQualityMetrics(
        left_x=left_x,
        right_x=right_x,
        left_coverage=left_coverage,
        right_coverage=right_coverage,
        time_balance=float(time_balance),
        rms=rms,
        weighted_rms=weighted_rms,
        left_prominence=left_prominence,
        right_prominence=right_prominence,
        minimum_prominence=minimum_prominence,
        prominence_balance=float(prominence_balance),
        inlier_y_min=inlier_y_min,
        inlier_y_max=inlier_y_max,
        inlier_y_span=inlier_y_span,
        vertex_mag_tolerance=vertex_mag_tolerance,
    )


def measure_extremum_flank_mean_evidence(
    fit_input: ExtremumFitInput,
    parabola: ExtremumParabolaFit,
    reference_jd: float,
) -> dict[str, object]:
    """Measure a weak extremum from averaged inner and outer flank blocks."""

    x = np.asarray(fit_input.x, dtype=np.float64)
    y = np.asarray(fit_input.y, dtype=np.float64)
    inlier_mask = np.asarray(parabola.inlier_mask, dtype=bool)
    direction = 1.0 if float(parabola.coefficients[0]) > 0 else -1.0

    side_results: dict[str, object] = {}
    significance_values: list[float] = []
    supported = True
    for side, side_mask in (
        ("left", inlier_mask & (x < reference_jd)),
        ("right", inlier_mask & (x > reference_jd)),
    ):
        side_indices = np.flatnonzero(side_mask)
        side_indices = side_indices[
            np.argsort(np.abs(x[side_indices] - reference_jd), kind="stable")
        ]
        block_points = int(len(side_indices) // 2)
        side_results[f"flank_mean_{side}_points"] = int(len(side_indices))
        side_results[f"flank_mean_{side}_block_points"] = block_points
        if block_points < EXTREMUM_FLANK_MEAN_MINIMUM_BLOCK_POINTS:
            supported = False
            side_results[f"flank_mean_{side}_excursion"] = float("nan")
            side_results[f"flank_mean_{side}_standard_error"] = float("nan")
            side_results[f"flank_mean_{side}_significance"] = float("nan")
            continue

        inner_values = y[side_indices[:block_points]]
        outer_values = y[side_indices[-block_points:]]
        signed_excursion = direction * float(
            np.mean(outer_values) - np.mean(inner_values)
        )
        standard_error = float(
            np.hypot(
                np.std(inner_values, ddof=1) / np.sqrt(block_points),
                np.std(outer_values, ddof=1) / np.sqrt(block_points),
            )
        )
        if standard_error > np.finfo(np.float64).eps:
            significance = signed_excursion / standard_error
        elif signed_excursion > 0:
            significance = float("inf")
        else:
            significance = 0.0
        side_results[f"flank_mean_{side}_excursion"] = signed_excursion
        side_results[f"flank_mean_{side}_standard_error"] = standard_error
        side_results[f"flank_mean_{side}_significance"] = float(significance)
        significance_values.append(float(significance))
        supported = supported and signed_excursion > 0

    if len(significance_values) != 2:
        supported = False
    else:
        supported = bool(
            supported
            and min(significance_values)
            >= EXTREMUM_FLANK_MEAN_MINIMUM_WEAK_SIGNIFICANCE
            and max(significance_values)
            >= EXTREMUM_FLANK_MEAN_MINIMUM_STRONG_SIGNIFICANCE
        )
    side_results.update(
        {
            "flank_mean_minimum_block_points": (
                EXTREMUM_FLANK_MEAN_MINIMUM_BLOCK_POINTS
            ),
            "flank_mean_minimum_weak_significance": (
                EXTREMUM_FLANK_MEAN_MINIMUM_WEAK_SIGNIFICANCE
            ),
            "flank_mean_minimum_strong_significance": (
                EXTREMUM_FLANK_MEAN_MINIMUM_STRONG_SIGNIFICANCE
            ),
            "flank_mean_support": supported,
        }
    )
    return side_results


def assess_extremum_support(
    jd_values: object,
    mag_values: object,
    mag_errors: object,
    xmin: float,
    xmax: float,
    preferred_reference_jd: float | None = None,
) -> ExtremumSupportAssessment:
    """Reject unsupported selections without choosing or preferring a model."""

    fit_input = select_extremum_fit_points(
        jd_values,
        mag_values,
        mag_errors,
        xmin,
        xmax,
    )
    x = np.asarray(fit_input.x, dtype=np.float64)

    def rejected(
        message: str,
        reason: str,
        metrics: dict[str, object],
        category: str,
        *,
        parabola: ExtremumParabolaFit | None = None,
        quality: ExtremumFitQualityMetrics | None = None,
        anchor: ExtremumPchipAnchor | None = None,
        show_attempt: bool = False,
    ) -> ExtremumSupportAssessment:
        return ExtremumSupportAssessment(
            accepted=False,
            reason=reason,
            message=message,
            reject_category=category,
            metrics=metrics,
            fit_input=fit_input,
            parabola=parabola,
            quality=quality,
            anchor=anchor,
            show_attempt=show_attempt,
        )

    if len(x) < EXTREMUM_MINIMUM_FIT_POINTS:
        return rejected(
            (
                f"Too few points. Select at least {EXTREMUM_MINIMUM_FIT_POINTS}; "
                f"current: {len(x)}."
            ),
            f"too few points ({len(x)}/{EXTREMUM_MINIMUM_FIT_POINTS})",
            {
                "selected_points": int(len(x)),
                "minimum_points": EXTREMUM_MINIMUM_FIT_POINTS,
            },
            EXTREMUM_REJECT_HARD,
        )
    try:
        parabola = fit_extremum_parabola_with_current_clipping(fit_input)
    except Exception as exc:
        return rejected(
            f"Reference fit failed: {exc}",
            f"reference fit failed: {exc}",
            {"selected_points": int(len(x))},
            EXTREMUM_REJECT_HARD,
        )
    a = float(parabola.coefficients[0])
    inlier_count = int(np.count_nonzero(parabola.inlier_mask))
    if not np.isfinite(a) or abs(a) < 1e-8:
        return rejected(
            "Curve too linear. Select a clear minimum or maximum.",
            "selected points too close to linear",
            {
                "selected_points": int(len(x)),
                "inliers": inlier_count,
                "quadratic_coefficient": a,
            },
            EXTREMUM_REJECT_HARD,
            parabola=parabola,
        )

    anchor = fit_extremum_pchip_anchor(
        fit_input,
        parabola,
        preferred_jd=preferred_reference_jd,
    )
    if (
        preferred_reference_jd is not None
        and np.isfinite(preferred_reference_jd)
        and xmin <= preferred_reference_jd <= xmax
    ):
        reference_jd = float(preferred_reference_jd)
        centered_reference = reference_jd - parabola.x0
        a, b, c = parabola.coefficients
        reference_mag = float(
            a * centered_reference**2 + b * centered_reference + c
        )
        reference_source = "night_candidate"
    elif anchor is not None and xmin <= anchor.anchor_jd <= xmax:
        reference_jd = float(anchor.anchor_jd)
        reference_mag = float(anchor.anchor_mag)
        reference_source = "pchip_local_extremum"
    else:
        reference_jd = float(parabola.vertex_jd)
        reference_mag = float(parabola.vertex_mag)
        reference_source = "reference_parabola"
    if not np.isfinite(reference_jd) or not xmin <= reference_jd <= xmax:
        return rejected(
            "No supported extremum inside the selection.",
            "reference extremum outside selected range",
            {
                "selected_points": int(len(x)),
                "inliers": inlier_count,
                "reference_jd": reference_jd,
                "reference_source": reference_source,
                "parabola_vertex_jd": float(parabola.vertex_jd),
            },
            EXTREMUM_REJECT_GEOMETRY,
            parabola=parabola,
            anchor=anchor,
            show_attempt=True,
        )
    try:
        centered_reference = reference_jd - parabola.x0
        a, b, c = parabola.coefficients
        support_curve_mag = float(
            a * centered_reference**2 + b * centered_reference + c
        )
        quality = measure_extremum_support_quality(
            fit_input,
            parabola,
            reference_jd,
            support_curve_mag,
        )
    except Exception:
        return rejected(
            "Too few points on one flank. Include both sides of the extremum.",
            "reference extremum lacks one flank",
            {
                "selected_points": int(len(x)),
                "inliers": inlier_count,
                "reference_jd": reference_jd,
                "reference_source": reference_source,
            },
            EXTREMUM_REJECT_GEOMETRY,
            parabola=parabola,
            anchor=anchor,
            show_attempt=True,
        )
    metrics: dict[str, object] = {
        "selected_points": int(len(x)),
        "inliers": inlier_count,
        "reference_jd": reference_jd,
        "reference_mag": reference_mag,
        "reference_source": reference_source,
        "left_points": int(len(quality.left_x)),
        "right_points": int(len(quality.right_x)),
        "left_coverage": quality.left_coverage,
        "right_coverage": quality.right_coverage,
        "time_balance": quality.time_balance,
        "rms": quality.rms,
        "weighted_rms": quality.weighted_rms,
        "left_prominence": quality.left_prominence,
        "right_prominence": quality.right_prominence,
        "minimum_prominence": quality.minimum_prominence,
        "prominence_balance": quality.prominence_balance,
    }
    flank_mean_evidence = measure_extremum_flank_mean_evidence(
        fit_input,
        parabola,
        reference_jd,
    )
    metrics.update(flank_mean_evidence)
    if (
        len(quality.left_x) < EXTREMUM_MINIMUM_SIDE_POINTS
        or len(quality.right_x) < EXTREMUM_MINIMUM_SIDE_POINTS
    ):
        return rejected(
            "Too few points on one flank. Include both sides of the extremum.",
            (
                "insufficient side points "
                f"(left={len(quality.left_x)}, right={len(quality.right_x)})"
            ),
            metrics,
            EXTREMUM_REJECT_GEOMETRY,
            parabola=parabola,
            quality=quality,
            anchor=anchor,
            show_attempt=True,
        )
    selected_width = float(xmax - xmin)
    minimum_side_coverage = (
        EXTREMUM_MINIMUM_SIDE_COVERAGE_FRACTION * selected_width
    )
    metrics["minimum_side_coverage"] = minimum_side_coverage
    if (
        selected_width <= 0
        or quality.left_coverage < minimum_side_coverage
        or quality.right_coverage < minimum_side_coverage
    ):
        return rejected(
            "One flank too short. Include both sides of the extremum.",
            (
                "insufficient side coverage "
                f"(left={quality.left_coverage:.8f}, "
                f"right={quality.right_coverage:.8f}, "
                f"required={minimum_side_coverage:.8f})"
            ),
            metrics,
            EXTREMUM_REJECT_GEOMETRY,
            parabola=parabola,
            quality=quality,
            anchor=anchor,
            show_attempt=True,
        )
    if quality.time_balance < EXTREMUM_MINIMUM_TIME_BALANCE:
        return rejected(
            "Time coverage unbalanced. Select comparable coverage on both sides.",
            (
                "unbalanced time coverage "
                f"(ratio={quality.time_balance:.2f}, "
                f"required={EXTREMUM_MINIMUM_TIME_BALANCE:.2f})"
            ),
            metrics,
            EXTREMUM_REJECT_GEOMETRY,
            parabola=parabola,
            quality=quality,
            anchor=anchor,
            show_attempt=True,
        )
    edge_prominence_supported = bool(
        min(quality.left_prominence, quality.right_prominence)
        >= 0.98 * quality.minimum_prominence
    )
    mean_prominence_supported = bool(
        flank_mean_evidence["flank_mean_support"]
    )
    metrics["prominence_support_method"] = (
        "edge_amplitude"
        if edge_prominence_supported
        else "flank_block_means" if mean_prominence_supported else "none"
    )
    if not edge_prominence_supported and not mean_prominence_supported:
        return rejected(
            "Extremum too weak. Include both flanks clearly.",
            (
                "insufficient prominence "
                f"(left={quality.left_prominence:.4f}, "
                f"right={quality.right_prominence:.4f}, "
                f"required={quality.minimum_prominence:.4f})"
            ),
            metrics,
            EXTREMUM_REJECT_PROMINENCE,
            parabola=parabola,
            quality=quality,
            anchor=anchor,
            show_attempt=True,
        )
    if quality.prominence_balance < EXTREMUM_MINIMUM_PROMINENCE_BALANCE:
        return rejected(
            "One flank has no clear prominence. Include both flanks.",
            (
                "unbalanced prominence "
                f"(ratio={quality.prominence_balance:.2f}, "
                f"required={EXTREMUM_MINIMUM_PROMINENCE_BALANCE:.2f})"
            ),
            metrics,
            EXTREMUM_REJECT_PROMINENCE,
            parabola=parabola,
            quality=quality,
            anchor=anchor,
            show_attempt=True,
        )

    sorted_x = np.sort(x)
    positive_cadence = np.diff(sorted_x)
    positive_cadence = positive_cadence[positive_cadence > 0]
    if len(positive_cadence):
        selected_span = float(np.max(sorted_x) - np.min(sorted_x))
        median_cadence = float(np.median(positive_cadence))
        cadence_span = (
            float("inf")
            if median_cadence <= 0
            else selected_span / median_cadence
        )
        metrics.update(
            {
                "selected_span": selected_span,
                "median_cadence": median_cadence,
                "cadence_span": cadence_span,
            }
        )
        if (
            len(x) <= EXTREMUM_SMALL_WINDOW_MAX_POINTS
            and np.isfinite(cadence_span)
            and cadence_span <= EXTREMUM_SMALL_WINDOW_MAX_CADENCE_SPAN
        ):
            return rejected(
                "Fit range too small. Select a wider range with clear flanks.",
                (
                    "small unstable fit window "
                    f"(points={len(x)}, cadence_span={cadence_span:.2f})"
                ),
                metrics,
                EXTREMUM_REJECT_PLAUSIBILITY,
                parabola=parabola,
                quality=quality,
                anchor=anchor,
                show_attempt=True,
            )

    context_calculation = ExtremumFitCalculation(
        accepted=True,
        reason="support",
        message="",
        metrics=metrics,
    )
    context_quality = measure_extremum_fit_context_quality(
        jd_values,
        mag_values,
        xmin,
        xmax,
        context_calculation,
    )
    marginal_asymmetric_prominence = (
        min(quality.left_prominence, quality.right_prominence)
        < quality.minimum_prominence
        <= max(quality.left_prominence, quality.right_prominence)
        <= 1.25 * quality.minimum_prominence
    )
    if (
        context_quality is not None
        and not marginal_asymmetric_prominence
        and len(x) <= EXTREMUM_CONTEXT_MAX_LOCAL_POINTS
        and context_quality.prominence_to_scatter
        < (
            EXTREMUM_CONTEXT_RELAXED_MIN_PROMINENCE_TO_SCATTER
            if len(x) >= EXTREMUM_CONTEXT_RELAXED_MIN_POINTS
            else EXTREMUM_CONTEXT_MIN_PROMINENCE_TO_SCATTER
        )
    ):
        required_ratio = (
            EXTREMUM_CONTEXT_RELAXED_MIN_PROMINENCE_TO_SCATTER
            if len(x) >= EXTREMUM_CONTEXT_RELAXED_MIN_POINTS
            else EXTREMUM_CONTEXT_MIN_PROMINENCE_TO_SCATTER
        )
        metrics.update(
            {
                "context_points": context_quality.context_points,
                "context_point_to_point_scatter": (
                    context_quality.point_to_point_scatter
                ),
                "context_prominence_to_scatter": (
                    context_quality.prominence_to_scatter
                ),
                "minimum_context_prominence_to_scatter": required_ratio,
            }
        )
        return rejected(
            "Extremum weak against local scatter. Select a wider range.",
            (
                "weak context prominence "
                f"(ratio={context_quality.prominence_to_scatter:.2f}, "
                f"required={required_ratio:.2f})"
            ),
            metrics,
            EXTREMUM_REJECT_PLAUSIBILITY,
            parabola=parabola,
            quality=quality,
            anchor=anchor,
            show_attempt=True,
        )

    return ExtremumSupportAssessment(
        accepted=True,
        reason="supported",
        message="",
        reject_category="",
        metrics=metrics,
        fit_input=fit_input,
        parabola=parabola,
        quality=quality,
        anchor=anchor,
    )


def calculate_extremum_fit(
    jd_values: object,
    mag_values: object,
    mag_errors: object,
    xmin: float,
    xmax: float,
    preferred_reference_jd: float | None = None,
    required_model_name: str | None = None,
) -> ExtremumFitCalculation:
    """Run extremum support assessment, model fitting, and model selection."""

    support = assess_extremum_support(
        jd_values,
        mag_values,
        mag_errors,
        xmin,
        xmax,
        preferred_reference_jd=preferred_reference_jd,
    )
    if not support.accepted:
        return rejected_extremum_fit(
            support.fit_input,
            support.message,
            support.reason,
            support.metrics,
            reject_category=support.reject_category,
            show_attempt=support.show_attempt,
            parabola=support.parabola,
        )
    parabola = support.parabola
    quality = support.quality
    if parabola is None or quality is None:
        return rejected_extremum_fit(
            support.fit_input,
            "Extremum support assessment is incomplete.",
            "incomplete support assessment",
            support.metrics,
            reject_category=EXTREMUM_REJECT_HARD,
        )
    selected, model_checks, anchor = select_extremum_candidate(
        support.fit_input,
        parabola,
        quality,
        preferred_anchor_jd=preferred_reference_jd,
        required_model_name=required_model_name,
    )
    if selected is None:
        has_valid_curve = any(
            bool(check.get("accepted"))
            for check in model_checks
        )
        candidate_reasons = tuple(
            str(check.get("reason", ""))
            for check in model_checks
            if check.get("decision") in {"rejected", "uncertainty_unavailable"}
        )
        metrics = dict(support.metrics)
        metrics.update(
            {
                "candidate_count": sum(
                    1
                    for check in model_checks
                    if check.get("decision") != "unavailable"
                ),
                "candidate_reasons": candidate_reasons,
            }
        )
        return rejected_extremum_fit(
            support.fit_input,
            (
                f"The selected curve model ({required_model_name}) does not yield "
                "one plausible extremum for this selection."
                if required_model_name is not None
                else (
                    "No curve model yields a stable extremum time for this selection."
                    if has_valid_curve
                    else "No curve model yields a plausible extremum for this selection."
                )
            ),
            (
                "no model with stable bootstrap timing accuracy"
                if has_valid_curve
                else "no valid extremum model"
            ),
            metrics,
            reject_category=EXTREMUM_REJECT_PLAUSIBILITY,
            show_attempt=True,
            parabola=parabola,
            model_checks=model_checks,
        )

    candidate_names = tuple(
        str(check["model"])
        for check in model_checks
        if check.get("decision") != "unavailable"
    )
    selection_method = (
        "manual_model"
        if required_model_name is not None
        else (
            "preferred_stable_robust_gcv_spline"
            if selected.model_name == "robust_gcv_spline"
            else "minimum_stable_bootstrap_timing_accuracy"
        )
    )
    result_metadata: dict[str, object] = {
        "fit_model_candidates": candidate_names,
        "fit_model_parameter_count": selected.parameter_count,
        "fit_model_selection": selection_method,
        "fit_required_model": required_model_name or "",
        "fit_model_bootstrap_samples": EXTREMUM_MODEL_BOOTSTRAP_SAMPLES,
        "fit_model_bootstrap_equivalence_ratio": (
            EXTREMUM_MODEL_BOOTSTRAP_EQUIVALENCE_RATIO
        ),
        "fit_coefficients": selected.metadata.get(
            "linear_coefficients",
            None if selected.model_name == "robust_gcv_spline" else parabola.coefficients,
        ),
        "fit_x0_jd": selected.metadata.get("spline_center_jd", parabola.x0),
        "fit_support_reference_jd": support.metrics["reference_jd"],
        "fit_support_reference_mag": support.metrics["reference_mag"],
        "fit_support_reference_source": support.metrics["reference_source"],
    }
    if anchor is not None:
        result_metadata.update(
            {
                "fit_anchor_jd": anchor.anchor_jd,
                "fit_anchor_mag": anchor.anchor_mag,
                "fit_anchor_observed_jd": anchor.observed_extreme_jd,
                "fit_anchor_observed_mag": anchor.observed_extreme_mag,
                "fit_anchor_time_tolerance": anchor.time_tolerance,
                "fit_anchor_mag_tolerance": anchor.mag_tolerance,
            }
        )
    selected_check = next(
        (
            check
            for check in model_checks
            if check.get("model") == selected.model_name
            and check.get("decision") == "selected"
        ),
        {},
    )
    result_metadata.update(
        {
            "fit_model_bootstrap_timing_error": selected_check.get(
                "bootstrap_timing_error",
                float("nan"),
            ),
            "fit_model_bootstrap_timing_accuracy": selected_check.get(
                "bootstrap_timing_accuracy",
                float("nan"),
            ),
            "fit_model_bootstrap_success_fraction": selected_check.get(
                "bootstrap_success_fraction",
                float("nan"),
            ),
        }
    )
    model_metrics = {
        **support.metrics,
        "model": selected.model_name,
        "model_parameter_count": selected.parameter_count,
        "model_information_score": selected_check.get(
            "information_score",
            float("nan"),
        ),
        "model_bootstrap_timing_error": selected_check.get(
            "bootstrap_timing_error",
            float("nan"),
        ),
        "model_bootstrap_timing_error_seconds": selected_check.get(
            "bootstrap_timing_error_seconds",
            float("nan"),
        ),
        "model_bootstrap_timing_accuracy": selected_check.get(
            "bootstrap_timing_accuracy",
            float("nan"),
        ),
        "model_bootstrap_timing_accuracy_seconds": selected_check.get(
            "bootstrap_timing_accuracy_seconds",
            float("nan"),
        ),
        "model_bootstrap_success_fraction": selected_check.get(
            "bootstrap_success_fraction",
            float("nan"),
        ),
        "model_bootstrap_stable": selected_check.get(
            "bootstrap_stable",
            False,
        ),
        "model_selection_metric": selection_method,
        **selected.metadata,
    }
    return accepted_extremum_fit(
        support.fit_input,
        parabola,
        quality,
        model_name=selected.model_name,
        vertex_jd=selected.vertex_jd,
        vertex_mag=selected.vertex_mag,
        vertex_jd_error=selected.vertex_jd_error,
        vertex_mag_error=selected.vertex_mag_error,
        bootstrap_vertex_jd_error=float(
            selected_check.get("bootstrap_timing_accuracy", float("nan"))
        ),
        bootstrap_vertex_mag_error=float(
            selected_check.get("bootstrap_magnitude_error", float("nan"))
        ),
        fit_plot_jd=selected.fit_plot_jd,
        fit_plot_mag=selected.fit_plot_mag,
        rms=selected.rms,
        weighted_rms=selected.weighted_rms,
        model_metrics=model_metrics,
        result_model_metadata=result_metadata,
        model_checks=model_checks,
    )




def extremum_fit_solid_plot_values(fit_jd, fit_mag, observed_jd):
    """Hide only the solid stroke across gaps; a dashed model remains continuous."""
    x = np.asarray(fit_jd, dtype=float)
    solid = np.asarray(fit_mag, dtype=float).copy()
    observed = np.asarray(observed_jd, dtype=float)
    if len(observed) >= 2:
        cadence = float(np.median(np.diff(observed)))
        for left, right in zip(observed[:-1], observed[1:]):
            if right - left > 3 * cadence:
                solid[(x > left) & (x < right)] = np.nan
    return solid


def read_result_metadata_header(path: Path) -> dict[str, str]:
    """Read '# KEY=VALUE' metadata from a result CSV header."""

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


def result_metadata_is_diagnostic(metadata: dict[str, str]) -> bool:
    """Return whether result metadata marks a rejected-target diagnostic curve."""

    return (
        metadata.get("RESULT_PURPOSE", "").strip().upper()
        == CONTAMINATED_TARGET_DIAGNOSTIC_PURPOSE
    )


def result_csv_is_diagnostic(path: Path) -> bool:
    """Return whether a result CSV is explicitly diagnostic and non-scientific."""

    try:
        return result_metadata_is_diagnostic(read_result_metadata_header(path))
    except OSError:
        return False


def result_metadata_allows_export(metadata: dict[str, str]) -> bool:
    """Return whether a result is allowed to enter scientific export paths."""

    if result_metadata_is_diagnostic(metadata):
        return False
    return metadata.get("EXPORT_ALLOWED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def aperture_settings_from_result_metadata(metadata: dict[str, str]) -> ApertureSettings | None:
    """Return aperture settings encoded in result CSV metadata, if present."""

    method = metadata.get("PHOTOMETRY_METHOD", "")
    radius_match = re.search(r"radius_px=([0-9.]+)", method)
    annulus_match = re.search(r"annulus_px=([0-9.]+)-([0-9.]+)", method)
    if radius_match is None or annulus_match is None:
        return None
    try:
        return ApertureSettings(
            float(radius_match.group(1)),
            float(annulus_match.group(1)),
            float(annulus_match.group(2)),
            "result metadata",
            None,
            metadata.get("RINGSET_NOTE", ""),
        )
    except ValueError:
        return None


def write_result_metadata_header(handle, metadata: dict[str, object]) -> None:
    """Write '# KEY=VALUE' metadata lines before a result CSV table."""

    for key, value in metadata.items():
        text = "" if value is None else str(value).strip()
        handle.write(f"# {key}={text.replace(chr(10), ' ')}\n")


def sexagesimal_ra_dec(obj: CatalogObject) -> tuple[str, str]:
    """Return target coordinates formatted for reports."""

    coord = sky_coord_for_object(obj)
    if coord is None:
        return "", ""
    ra = coord.ra.to_string(unit=u.hour, sep="hms", precision=2, pad=True)
    dec = coord.dec.to_string(unit=u.deg, sep="dms", precision=2, alwayssign=True, pad=True)
    return ra, dec


def vsx_mag_range(obj: CatalogObject) -> tuple[str, str, str, str]:
    """Return display range and raw max/min/band values from VSX metadata."""

    max_parts = [
        obj.value_for(("l_max",)),
        obj.value_for(("max",)),
        obj.value_for(("u_max",)),
        obj.value_for(("n_max",)),
    ]
    min_parts = [
        obj.value_for(("f_min",)),
        obj.value_for(("l_min",)),
        obj.value_for(("min",)),
        obj.value_for(("u_min",)),
        obj.value_for(("n_min",)),
    ]
    max_raw = " ".join(part.strip() for part in max_parts if part.strip())
    min_raw = " ".join(part.strip() for part in min_parts if part.strip())
    band = obj.value_for(("n_max", "n_min")).strip()
    if max_raw and min_raw:
        display = f"{max_raw} - {min_raw}"
    else:
        display = max_raw or min_raw
    return display, max_raw, min_raw, band


def vsx_magnitude_display(obj: CatalogObject) -> str:
    """Return the VSX magnitude text including passband when available."""

    display, _max_raw, _min_raw, _band = vsx_mag_range(obj)
    return display or obj.magnitude


def result_target_metadata(target: CatalogObject) -> dict[str, str]:
    """Return the complete, portable target identity stored in every result."""

    target_ra, target_dec = sexagesimal_ra_dec(target)
    vsx_oid = target.value_for(("OID", "oid")).strip()
    mag_range, mag_max_raw, mag_min_raw, mag_band = vsx_mag_range(target)
    return {
        "OBJECT_NAME": target.name.strip(),
        "OBJECT_CATALOG_SOURCE": target.catalog_source if is_added_target(target) else "VSX",
        "OBJECT_CATALOG_ID": (
            target.catalog_id if is_added_target(target)
            else target.value_for(("OID", "oid")) or target.name
        ),
        "OBJECT_RA": target_ra,
        "OBJECT_DEC": target_dec,
        "OBJECT_VAR_TYPE": target.object_type.strip(),
        "OBJECT_MAG_RANGE": mag_range,
        "OBJECT_MAG_MAX_RAW": mag_max_raw,
        "OBJECT_MAG_MIN_RAW": mag_min_raw,
        "OBJECT_MAG_BAND": mag_band,
        "OBJECT_PERIOD": normalize_period(target.period).strip(),
        "OBJECT_EPOCH": target.value_for(("Epoch", "epoch")).strip(),
        "OBJECT_VSX_OID": vsx_oid,
        "OBJECT_VSX_URL": aavso_vsx_detail_url(vsx_oid) if vsx_oid else "",
    }


ADDED_TARGET_SOURCES = frozenset({"SIMBAD", "GAIA_DR3", "MANUAL"})


def is_added_target(target: CatalogObject | None) -> bool:
    return target is not None and target.catalog_source in ADDED_TARGET_SOURCES


def parse_added_target_coordinates(ra_text: str, dec_text: str) -> tuple[float, float]:
    """Read ICRS degrees or RA hourangle/Dec degree sexagesimal coordinates."""

    try:
        ra_value = ra_text.strip().replace(",", ".")
        dec_value = dec_text.strip().replace(",", ".")
        ra_deg = (
            float(Angle(ra_value, unit=u.hourangle).degree)
            if ":" in ra_value or "h" in ra_value.lower()
            else float(ra_value)
        )
        dec_deg = (
            float(Angle(dec_value, unit=u.deg).degree)
            if ":" in dec_value or "d" in dec_value.lower()
            else float(dec_value)
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("Use ICRS decimal degrees or RA hh:mm:ss / Dec ±dd:mm:ss.") from exc
    if not math.isfinite(ra_deg) or not 0 <= ra_deg < 360:
        raise ValueError("RA must be in decimal degrees from 0 (inclusive) to 360 (exclusive).")
    if not math.isfinite(dec_deg) or not -90 <= dec_deg <= 90:
        raise ValueError("Dec must be in decimal degrees from -90 to +90.")
    return ra_deg, dec_deg


def added_target_object(
    name: str, source: str, catalog_id: str, ra_deg: float, dec_deg: float,
    object_type: str = "",
) -> CatalogObject:
    if not name.strip() or source not in ADDED_TARGET_SOURCES:
        raise ValueError("Added target needs a name and a supported coordinate source.")
    ra_deg, dec_deg = parse_added_target_coordinates(str(ra_deg), str(dec_deg))
    return CatalogObject({
        "Name": name.strip(), "id": catalog_id.strip(), "catalog_source": source,
        "RA": f"{ra_deg:.10f}", "DEC": f"{dec_deg:.10f}", "Type": object_type.strip(),
    })


def resolve_simbad_target(identifier: str, object_type: str = "") -> CatalogObject:
    """Resolve one literal identifier using SIMBAD's documented degree columns."""

    identifier = identifier.strip()
    if not identifier or any(char in identifier for char in "*?[]"):
        raise ValueError("Enter one complete SIMBAD identifier without wildcards.")
    ensure_importable_module("astroquery")
    from astroquery.simbad import Simbad

    rows = Simbad.query_object(identifier, wildcard=False)
    if rows is None or len(rows) != 1:
        raise ValueError(f"SIMBAD did not resolve exactly one object for {identifier!r}.")
    row = rows[0]
    main_id = str(row["main_id"]).strip()
    return added_target_object(
        identifier, "SIMBAD", main_id, float(row["ra"]), float(row["dec"]), object_type,
    )


def resolve_gaia_dr3_target(identifier: str, object_type: str = "") -> CatalogObject:
    """Resolve a DR3 source_id in the release-specific Gaia source table."""

    match = re.fullmatch(r"(?:Gaia\s+DR3\s+)?([0-9]{1,19})", identifier.strip(), re.I)
    if match is None:
        raise ValueError("Enter a Gaia DR3 source ID as digits or 'Gaia DR3 <digits>'.")
    source_id = int(match.group(1))
    if not 0 < source_id <= 2**63 - 1:
        raise ValueError("Gaia DR3 source ID is outside the valid 64-bit range.")
    ensure_importable_module("astroquery")
    from astroquery.gaia import Gaia

    job = Gaia.launch_job(
        "SELECT source_id, ra, dec FROM gaiadr3.gaia_source "
        f"WHERE source_id = {source_id}"
    )
    rows = job.get_results()
    if len(rows) != 1 or int(rows[0]["source_id"]) != source_id:
        raise ValueError(f"Gaia DR3 source {source_id} was not found.")
    return added_target_object(
        f"Gaia DR3 {source_id}", "GAIA_DR3", str(source_id),
        float(rows[0]["ra"]), float(rows[0]["dec"]), object_type,
    )


def exclude_target_from_field_references(
    candidates: list[CatalogObject], target: CatalogObject,
    frame: ReferenceFrame, minimum_distance_px: float,
) -> list[CatalogObject]:
    """Never let a manually added target calibrate its own measurement."""

    target_x, target_y, problem = object_pixel_position_in_frame(target, frame)
    if problem is not None:
        raise ValueError(f"Target position is unusable: {problem}")
    separated = []
    for candidate in candidates:
        x, y, problem = object_pixel_position_in_frame(candidate, frame)
        if problem is None and math.hypot(x - target_x, y - target_y) > minimum_distance_px:
            separated.append(candidate)
    return separated


def assess_single_target_catalog_blend(
    target: CatalogObject, frame: ReferenceFrame,
    aperture_settings: ApertureSettings, progress: Callable[[str], None] | None = None,
) -> TargetBlendAssessment:
    """Keep historical catalog fluxes from rejecting a newly added transient."""

    if is_added_target(target):
        return TargetBlendAssessment(
            QUALITY_STATUS_WARNING, "TARGET_BLEND_EVIDENCE_INCOMPLETE", False,
            "Gaia blend model skipped: an added target may have changed brightness "
            "since Gaia DR3; inspect nearby sources in the image.",
            evidence_complete=False,
        )
    return assess_target_catalog_blend(target, frame, aperture_settings, progress)


def catalog_object_report_entry(obj: CatalogObject) -> str:
    """Return a compact star entry for report metadata."""

    ra, dec = sexagesimal_ra_dec(obj)
    catalog_source = obj.catalog_source or "CATALOG"
    catalog_id = obj.catalog_id or obj.name
    parts = [f"{catalog_source} {catalog_id}".strip()]
    if ra or dec:
        parts.append(f"{ra} {dec}".strip())
    if obj.magnitude:
        parts.append(f"mag={obj.magnitude}")
    if obj.magnitude_error_float() is not None:
        parts.append(f"e_mag={obj.magnitude_error_float():.6f}")
    if obj.nobs_v_int() is not None:
        parts.append(f"nobs={obj.nobs_v_int()}")
    return " ".join(part for part in parts if part)


def load_optional_observer_result_settings() -> dict[str, str]:
    """Return optional observer metadata from the current legacy config source."""

    settings = {
        "TELESCOPE": DEFAULT_RESULT_TELESCOPE,
        "TELESCOPE_SOURCE": "built-in default",
        "OBSERVER_BAV": "",
        "OBSERVER_NAME": "",
        "OBSERVER_AAVSO": "",
        "OBSERVER_SITE": "",
        "OBSERVER_LATITUDE": "",
        "OBSERVER_LONGITUDE": "",
    }

    try:
        plugin_path = Path(__file__).with_name("sp_mod_bav.py")
        if not plugin_path.exists():
            return settings
        spec = importlib.util.spec_from_file_location(
            "sp_mod_bav_metadata",
            plugin_path,
        )
        if spec is None or spec.loader is None:
            return settings
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return settings
    load_config = getattr(module, "load_config", None)
    validate_config = getattr(module, "validate_config", None)
    if not callable(load_config):
        return settings
    try:
        raw_config = load_config()
    except Exception:
        return settings
    if not isinstance(raw_config, dict):
        return settings

    telescope = str(raw_config.get("telescope", "") or "").strip()
    if telescope in RESULT_TELESCOPE_SPECS:
        settings["TELESCOPE"] = telescope
        settings["TELESCOPE_SOURCE"] = "BAV telescope preference"

    settings["OBSERVER_NAME"] = str(
        raw_config.get("observer_name", "") or ""
    ).strip()
    settings["OBSERVER_SITE"] = str(raw_config.get("site_name", "") or "").strip()
    bav_code = str(raw_config.get("bav_code", "") or "").strip().upper()
    if re.fullmatch(r"[A-Z]+", bav_code):
        settings["OBSERVER_BAV"] = bav_code
    aavso_code = str(raw_config.get("aavso_code", "") or "").strip().upper()
    if re.fullmatch(r"[A-Z0-9]{1,5}", aavso_code):
        settings["OBSERVER_AAVSO"] = aavso_code

    # A fully valid BAV config can also supply normalized decimal coordinates.
    # Validation failure is intentionally ignored here: BAV is optional for
    # photometry and remains strictly validated only by the BAV workflow.
    if callable(validate_config):
        try:
            validated_config = validate_config(raw_config)
        except Exception:
            validated_config = None
        if isinstance(validated_config, dict):
            settings["OBSERVER_LATITUDE"] = str(
                validated_config.get("latitude", "") or ""
            ).strip()
            settings["OBSERVER_LONGITUDE"] = str(
                validated_config.get("longitude", "") or ""
            ).strip()
    return settings


def result_instrument_defaults() -> tuple[str, dict[str, dict[str, object]], str]:
    """Return the neutral instrument fallback used by all result writers."""

    settings = load_optional_observer_result_settings()
    return (
        settings["TELESCOPE"],
        RESULT_TELESCOPE_SPECS,
        settings["TELESCOPE_SOURCE"],
    )


def load_lightcurve_results_module() -> object:
    """Return the required, centrally imported result-file contract."""

    return lightcurve_results


def load_measurement_binning_module() -> object:
    """Return the required, centrally imported binning implementation."""

    return measurement_binning


def result_row_float(row: dict[str, object], key: str) -> float | None:
    """Return a finite float from a result row."""

    try:
        value = float(str(row.get(key, "")).strip())
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def augment_result_metadata_from_rows(
    metadata: dict[str, object],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    """Add row-derived result metadata before writing the CSV header."""

    result = dict(metadata)
    result["OBS_COUNT"] = len(rows)

    jd_values = [
        value
        for row in rows
        if (value := result_row_float(row, "jd")) is not None
    ]
    if jd_values:
        jd_start = min(jd_values)
        jd_end = max(jd_values)
        result["JD_START"] = f"{jd_start:.8f}"
        result["JD_END"] = f"{jd_end:.8f}"
        result["UTC_START"] = Time(jd_start, format="jd", scale="utc").isot
        result["UTC_END"] = Time(jd_end, format="jd", scale="utc").isot
        result["REPORT_DATE"] = str(result["UTC_START"])[:10]

    filters = sorted(
        {
            str(row.get("aavso_filter", "")).strip()
            for row in rows
            if str(row.get("aavso_filter", "")).strip()
        }
    )
    result["FILTER"] = filters[0] if len(filters) == 1 else ";".join(filters)
    return result


def build_lightcurve_trim_plan(
    result_csv: Path,
    range_min_jd: float,
    range_max_jd: float,
) -> LightCurveTrimPlan:
    """Return the exact current-format CSV content kept by a JD trim."""

    if not result_csv.is_file():
        raise FileNotFoundError(f"Result CSV not found: {result_csv}")
    lower = min(float(range_min_jd), float(range_max_jd))
    upper = max(float(range_min_jd), float(range_max_jd))
    if not np.isfinite(lower) or not np.isfinite(upper) or lower >= upper:
        raise ValueError("Select a valid JD range first.")

    metadata = read_result_metadata_header(result_csv)
    with result_csv.open(newline="") as handle:
        reader = csv_data_dict_reader(handle)
        fieldnames = tuple(reader.fieldnames or ())
        source_rows = list(reader)
    if not fieldnames or "jd" not in fieldnames:
        raise ValueError("The current result CSV has no usable jd column.")

    kept_rows = tuple(
        row
        for row in source_rows
        if (
            (jd := result_row_float(row, "jd")) is not None
            and lower <= jd <= upper
        )
    )
    if len(kept_rows) < 2:
        raise ValueError("The selected range must keep at least two light-curve rows.")
    if len(kept_rows) == len(source_rows):
        raise ValueError("The selected range does not remove any light-curve rows.")

    original_count_text = metadata.get("LIGHTCURVE_ORIGINAL_OBS_COUNT", "").strip()
    try:
        original_count = int(original_count_text)
    except ValueError:
        original_count = len(source_rows)
    original_count = max(original_count, len(source_rows))

    trimmed_metadata = augment_result_metadata_from_rows(metadata, list(kept_rows))
    trimmed_metadata.update(
        {
            "LIGHTCURVE_TRIMMED": 1,
            "LIGHTCURVE_ORIGINAL_OBS_COUNT": original_count,
            "LIGHTCURVE_TRIM_JD_START": f"{lower:.8f}",
            "LIGHTCURVE_TRIM_JD_END": f"{upper:.8f}",
        }
    )
    source_stat = result_csv.stat()
    return LightCurveTrimPlan(
        result_csv=result_csv,
        metadata=trimmed_metadata,
        fieldnames=fieldnames,
        rows=kept_rows,
        original_row_count=len(source_rows),
        range_min_jd=lower,
        range_max_jd=upper,
        source_size=source_stat.st_size,
        source_mtime_ns=source_stat.st_mtime_ns,
    )


def write_lightcurve_trim_plan(plan: LightCurveTrimPlan) -> None:
    """Atomically replace a result CSV with one previously reviewed trim plan."""

    current_stat = plan.result_csv.stat()
    if (
        current_stat.st_size != plan.source_size
        or current_stat.st_mtime_ns != plan.source_mtime_ns
    ):
        raise RuntimeError("The result CSV changed after the trim preview. Select the range again.")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{plan.result_csv.name}.",
            suffix=".tmp",
            dir=plan.result_csv.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            write_result_metadata_header(handle, plan.metadata)
            writer = csv.DictWriter(handle, fieldnames=plan.fieldnames)
            writer.writeheader()
            writer.writerows(plan.rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, stat.S_IMODE(current_stat.st_mode))
        os.replace(temporary_path, plan.result_csv)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def apply_lightcurve_trim_plan(
    plan: LightCurveTrimPlan,
    export_files: tuple[Path, ...],
) -> tuple[tuple[Path, ...], tuple[tuple[Path, OSError], ...]]:
    """Commit the trimmed CSV before removing local exports derived from it."""

    write_lightcurve_trim_plan(plan)
    deleted: list[Path] = []
    failed: list[tuple[Path, OSError]] = []
    for path in export_files:
        try:
            path.unlink()
            deleted.append(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            failed.append((path, exc))
    return tuple(deleted), tuple(failed)


def local_derived_export_files(
    result_csv: Path,
    target_name: str,
) -> tuple[Path, ...]:
    """Return local AAVSO/BAV derivatives without any archive or DB semantics."""

    matches: list[Path] = []
    results_module = load_lightcurve_results_module()
    aavso_path_builder = getattr(results_module, "aavso_report_path", None)
    if not callable(aavso_path_builder):
        raise RuntimeError("Central result-file contract is unavailable.")
    aavso_path = Path(aavso_path_builder(result_csv))
    if aavso_path.is_file() or aavso_path.is_symlink():
        matches.append(aavso_path)

    bav_directory = result_csv.parent / "BAV"
    if bav_directory.is_dir():
        bav_files = getattr(results_module, "bav_report_files", None)
        if not callable(bav_files):
            raise RuntimeError("Could not safely identify local BAV files for this result.")
        metadata = read_result_metadata_header(result_csv)
        matches.extend(
            path
            for path in bav_files(result_csv, metadata, target_name)
            if (
                path.suffix.casefold() == ".pdf"
                or path.name.casefold().endswith("_minimax.txt")
                or path.name.casefold().endswith("_report.txt")
            )
        )

    unique: dict[Path, Path] = {}
    for path in matches:
        unique[path.resolve()] = path
    return tuple(sorted(unique.values(), key=lambda path: str(path).casefold()))


def cleanup_plate_solve_artifacts(directory: Path) -> None:
    """Remove stale sequence artifacts produced by a previous plate solve attempt."""

    if not directory.exists():
        return
    for path in directory.iterdir():
        if is_plate_solve_artifact(path):
            path.unlink()


def temp_directory_for(source_dir: Path) -> Path:
    """Return the temporary work directory beside the source FITS directory."""

    return source_dir.parent / TMP_DIRECTORY_NAME


def temp_work_directory_for(source_dir: Path) -> Path:
    """Return the temporary Siril work-sequence directory for a source directory."""

    return temp_directory_for(source_dir) / "work"


def results_directory_for(source_dir: Path) -> Path:
    """Return the persistent results directory for one source FITS directory."""

    return source_dir.parent / RESULTS_DIRECTORY_NAME / source_dir.name


def diagnostics_directory_for_results_dir(results_dir: Path) -> Path:
    """Return the diagnostics directory below one persistent results directory."""

    return results_dir / DIAGNOSTICS_DIRECTORY_NAME


def diagnostics_directory_for_result_csv(result_csv: Path) -> Path:
    """Return the diagnostics directory for a result CSV."""

    return diagnostics_directory_for_results_dir(result_csv.parent)


def _retry_readonly_removal(function, path: str, exc_info) -> None:
    """Make a read-only entry writable and retry its removal."""

    error = exc_info[1]
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    function(path)


def _move_process_cwd_outside(directory: Path, safe_directory: Path) -> None:
    """Change only Python's cwd before deleting a tree, which Windows requires."""

    try:
        cwd = Path.cwd().resolve()
        directory = directory.resolve()
    except OSError:
        os.chdir(safe_directory)
        return
    if cwd == directory or directory in cwd.parents:
        os.chdir(safe_directory)


def remove_temp_directory_tree(tmp_dir: Path, safe_directory: Path) -> None:
    """Strictly remove one temp tree, retrying transient filesystem locks."""

    if not tmp_dir.exists() and not tmp_dir.is_symlink():
        return
    if tmp_dir.is_symlink():
        tmp_dir.unlink()
        return
    safe_directory = safe_directory.resolve()
    if not safe_directory.is_dir():
        raise FileNotFoundError(f"Safe cleanup directory does not exist: {safe_directory}")
    _move_process_cwd_outside(tmp_dir, safe_directory)

    last_error: OSError | None = None
    retry_delays = (0.0, *TEMP_CLEANUP_RETRY_DELAYS_SECONDS)
    for attempt, retry_delay in enumerate(retry_delays):
        if retry_delay:
            time.sleep(retry_delay)
        try:
            shutil.rmtree(tmp_dir, onerror=_retry_readonly_removal)
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 == len(retry_delays):
                break
        else:
            if not tmp_dir.exists() and not tmp_dir.is_symlink():
                return
            last_error = OSError(f"Temporary directory still exists after removal: {tmp_dir}")
    assert last_error is not None
    raise last_error


def reset_temp_directory(source_dir: Path) -> Path:
    """Delete and recreate the temporary work directory for a source directory."""

    tmp_dir = temp_directory_for(source_dir)
    remove_temp_directory_tree(tmp_dir, tmp_dir.parent)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir


def prepare_temp_work_directory(source_dir: Path) -> tuple[Path, int]:
    """Create a clean temporary work directory and count source FITS files."""

    tmp_dir = reset_temp_directory(source_dir)
    work_dir = tmp_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    fits_files = sorted(
        path
        for path in source_dir.iterdir()
        if is_fits_file(path) and not is_plate_solve_artifact(path)
    )
    return work_dir, len(fits_files)


def prepare_single_fits_work_directory(source_dir: Path, fits_file: Path) -> tuple[Path, Path]:
    """Create a clean temporary work directory with one selected FITS input file."""

    if not is_fits_file(fits_file):
        raise FileNotFoundError(f"Not a visible FITS file: {fits_file}")
    tmp_dir = reset_temp_directory(source_dir)
    single_input_dir = tmp_dir / "single_input"
    work_dir = tmp_dir / "work"
    single_input_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    input_copy = single_input_dir / fits_file.name
    shutil.copy2(fits_file, input_copy)
    return work_dir, single_input_dir


def ensure_results_directory(source_dir: Path) -> Path:
    """Create and return the persistent results directory for a source directory."""

    results_dir = results_directory_for(source_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir


def cleanup_temp_directory(source_dir: Path | None) -> None:
    """Delete this script's temporary work directory if it exists."""

    if source_dir is None:
        return
    tmp_dir = temp_directory_for(source_dir)
    remove_temp_directory_tree(tmp_dir, tmp_dir.parent)


def is_wcs_header_key(key: str) -> bool:
    """Return True for FITS header keys that belong to astrometric WCS data."""

    if key in WCS_HEADER_KEYS or key.startswith(WCS_HEADER_PREFIXES):
        return True
    return any(
        key.startswith(prefix)
        and len(key) > len(prefix)
        and key[len(prefix)].isdigit()
        for prefix in WCS_MATRIX_PREFIXES
    )


def remove_wcs_from_fits(path: Path) -> int:
    """Remove inherited WCS headers from one FITS file and return key count."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", VerifyWarning)
        warnings.simplefilter("ignore", FITSFixedWarning)
        with fits.open(path, mode="update") as hdul:
            header = hdul[0].header
            keys = [key for key in header.keys() if is_wcs_header_key(key)]
            for key in keys:
                del header[key]
            if keys:
                header.add_history(
                    "Removed inherited WCS before Seestar lightcurve plate solve."
                )
                hdul.flush()
            return len(keys)


def remove_inherited_wcs_from_sequence(
    directory: Path,
    sequence_name: str,
) -> tuple[int, int]:
    """Clear stale WCS headers from linked work-sequence FITS files."""

    touched_files = 0
    removed_keys = 0
    for path in sorted(directory.glob(f"{sequence_name}*.fit")):
        if not is_fits_file(path):
            continue
        count = remove_wcs_from_fits(path)
        if count:
            touched_files += 1
            removed_keys += count
    return touched_files, removed_keys


def fits_has_wcs(path: Path) -> bool:
    """Return True if the FITS primary header contains the core WCS keys."""

    with fits.open(path) as hdul:
        header = hdul[0].header
    required = ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2")
    return all(key in header for key in required)


def read_reference_frame(path: Path, index: int) -> ReferenceFrame | None:
    """Read reference-frame metadata from a solved FITS file."""

    with fits.open(path) as hdul:
        header = hdul[0].header
    required = ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "NAXIS1", "NAXIS2")
    if not all(key in header for key in required):
        return None
    return ReferenceFrame(
        path=path,
        index=index,
        ra_deg=float(header["CRVAL1"]),
        dec_deg=float(header["CRVAL2"]),
        width=int(header["NAXIS1"]),
        height=int(header["NAXIS2"]),
    )


def sequence_fits_files(directory: Path, sequence_name: str) -> list[Path]:
    """Return visible FITS files belonging to one Siril sequence."""

    return sorted(
        path
        for path in directory.glob(f"{sequence_name}*.fit")
        if is_fits_file(path)
    )


def solved_sequence_fits_files(directory: Path, sequence_name: str) -> list[Path]:
    """Return sequence FITS files that have usable plate-solve WCS headers."""

    solved_files: list[Path] = []
    for path in sequence_fits_files(directory, sequence_name):
        try:
            if fits_has_wcs(path):
                solved_files.append(path)
        except Exception:
            continue
    return solved_files


def collect_plate_solve_stats(directory: Path, sequence_name: str) -> PlateSolveStats:
    """Count solved and unsolved files in this script's linked plate-solve sequence."""

    sequence_files = sequence_fits_files(directory, sequence_name)
    failed_files: list[str] = []
    solved = 0
    for path in sequence_files:
        try:
            if fits_has_wcs(path):
                solved += 1
            else:
                failed_files.append(path.name)
        except Exception:
            failed_files.append(path.name)
    return PlateSolveStats(
        total=len(sequence_files),
        solved=solved,
        failed_files=tuple(failed_files),
    )


def sequence_index(path: Path) -> int | None:
    """Return the numeric sequence index from seestar_lightcurve_ps_00001.fit."""

    for prefix in (WORK_SEQUENCE_NAME, LEGACY_REGISTERED_SEQUENCE_NAME):
        if path.name.startswith(prefix):
            try:
                return int(path.stem.removeprefix(prefix))
            except ValueError:
                return None
    return None


def sample_paths_evenly(paths: list[Path], max_count: int) -> list[Path]:
    """Return up to max_count paths sampled evenly over the full sequence."""

    if len(paths) <= max_count:
        return paths
    indices = np.linspace(0, len(paths) - 1, max_count)
    selected_indices = sorted({int(round(value)) for value in indices})
    return [paths[index] for index in selected_indices]


def reference_frame_quality(path: Path, index: int, center_index: float) -> ReferenceFrameQuality | None:
    """Estimate quality of one candidate reference frame."""

    try:
        with fits.open(path) as hdul:
            data = np.asarray(hdul[0].data, dtype=np.float64)
    except Exception:
        return None

    if data.ndim != 2:
        return None

    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return None

    background = float(np.median(finite))
    mad = float(np.median(np.abs(finite - background)))
    sigma = 1.4826 * mad if mad > 0 else float(np.std(finite))
    if not np.isfinite(sigma) or sigma <= 0:
        return None

    height, width = data.shape
    border = 12
    if height <= border * 2 or width <= border * 2:
        return None

    work = data.copy()
    work[~np.isfinite(work)] = background
    threshold = background + 8.0 * sigma
    candidates = np.argwhere(work[border:-border, border:-border] > threshold)
    if candidates.size == 0:
        return ReferenceFrameQuality(path, index, None, 0, float("inf"), 1.0, float("-inf"))

    candidates[:, 0] += border
    candidates[:, 1] += border
    intensities = work[candidates[:, 0], candidates[:, 1]]
    order = np.argsort(intensities)[::-1]

    fwhm_values: list[float] = []
    accepted_positions: list[tuple[int, int]] = []
    half_box = 7
    for candidate_index in order:
        y = int(candidates[candidate_index, 0])
        x = int(candidates[candidate_index, 1])
        if any((x - old_x) ** 2 + (y - old_y) ** 2 < 12**2 for old_x, old_y in accepted_positions):
            continue
        patch = work[y - half_box : y + half_box + 1, x - half_box : x + half_box + 1]
        if patch.shape != (half_box * 2 + 1, half_box * 2 + 1):
            continue

        local_bg = float(np.median(np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1]))))
        weights = patch - local_bg
        weights[weights < 0] = 0
        total = float(np.sum(weights))
        peak = float(np.max(patch) - local_bg)
        if total <= 0 or peak < 10.0 * sigma:
            continue

        yy, xx = np.indices(patch.shape)
        x_centroid = float(np.sum(xx * weights) / total)
        y_centroid = float(np.sum(yy * weights) / total)
        var_x = float(np.sum(((xx - x_centroid) ** 2) * weights) / total)
        var_y = float(np.sum(((yy - y_centroid) ** 2) * weights) / total)
        if var_x <= 0 or var_y <= 0:
            continue

        fwhm = 2.355 * float(np.sqrt((var_x + var_y) / 2.0))
        if MIN_AUTO_FWHM_PX <= fwhm <= MAX_AUTO_FWHM_PX:
            fwhm_values.append(fwhm)
            accepted_positions.append((x, y))
        if len(fwhm_values) >= MAX_FWHM_SAMPLE_STARS:
            break

    fwhm = float(np.median(np.array(fwhm_values, dtype=np.float64))) if fwhm_values else None
    tile_values: list[float] = []
    grid = 6
    for y0 in np.linspace(0, height, grid + 1, dtype=int)[:-1]:
        y1 = min(height, y0 + max(height // grid, 1))
        for x0 in np.linspace(0, width, grid + 1, dtype=int)[:-1]:
            x1 = min(width, x0 + max(width // grid, 1))
            tile = data[y0:y1, x0:x1]
            tile_finite = tile[np.isfinite(tile)]
            if tile_finite.size:
                tile_values.append(float(np.median(tile_finite)))
    if len(tile_values) >= 2:
        tile_array = np.array(tile_values, dtype=np.float64)
        background_tile_scatter = float(np.std(tile_array) / max(abs(background), 1.0))
    else:
        background_tile_scatter = 0.0

    center_distance = abs(index - center_index) / max(center_index, 1.0)
    star_score = np.log1p(len(fwhm_values))
    fwhm_penalty = 0.0 if fwhm is None else max(0.0, fwhm - 2.0) * 0.25
    missing_fwhm_penalty = 2.0 if fwhm is None else 0.0
    background_penalty = min(background_tile_scatter * 8.0, 4.0)
    center_penalty = center_distance * 0.25
    score = star_score - fwhm_penalty - missing_fwhm_penalty - background_penalty - center_penalty

    return ReferenceFrameQuality(
        path=path,
        index=index,
        fwhm_px=fwhm,
        star_count=len(fwhm_values),
        background_tile_scatter=background_tile_scatter,
        center_distance=center_distance,
        score=score,
    )


def find_best_reference_frame(
    directory: Path,
    progress: Callable[[str], None] | None = None,
) -> ReferenceFrame | None:
    """Return the best sampled solved frame for catalog search and annotation."""

    solved_files = solved_sequence_fits_files(directory, WORK_SEQUENCE_NAME)
    if not solved_files:
        return None

    indexed_paths = [
        (path, sequence_index(path))
        for path in solved_files
    ]
    indexed_paths = [
        (path, index)
        for path, index in indexed_paths
        if index is not None
    ]
    if not indexed_paths:
        return None

    center_index = float(np.median(np.array([index for _, index in indexed_paths], dtype=np.float64)))
    sampled_paths = sample_paths_evenly([path for path, _ in indexed_paths], MAX_REFERENCE_FRAME_CANDIDATES)
    if progress is not None:
        progress(
            f"Selecting reference frame from {len(sampled_paths)}/{len(indexed_paths)} "
            "sampled solved frame(s)."
        )

    qualities: list[ReferenceFrameQuality] = []
    for path in sampled_paths:
        index = sequence_index(path)
        if index is None:
            continue
        quality = reference_frame_quality(path, index, center_index)
        if quality is not None:
            qualities.append(quality)
            if progress is not None:
                progress(
                    "Reference candidate "
                    f"{path.name}: score={quality.score:.3f}, "
                    f"stars={quality.star_count}, "
                    f"fwhm={'n/a' if quality.fwhm_px is None else f'{quality.fwhm_px:.2f}'}, "
                    f"bg_scatter={quality.background_tile_scatter:.4f}"
                )

    if not qualities:
        return None

    best = max(qualities, key=lambda item: item.score)
    if progress is not None:
        progress(
            "Selected reference frame "
            f"{best.path.name}: score={best.score:.3f}, stars={best.star_count}, "
            f"fwhm={'n/a' if best.fwhm_px is None else f'{best.fwhm_px:.2f}'}, "
            f"bg_scatter={best.background_tile_scatter:.4f}"
        )
    return read_reference_frame(best.path, best.index)


def work_sequence_exists(directory: Path) -> bool:
    """Return True if this script's linked Siril work sequence exists."""

    return (directory / f"{WORK_SEQUENCE_NAME}.seq").exists() or any(
        directory.glob(f"{WORK_SEQUENCE_NAME}*.fit")
    )


def read_catalog_objects(path: Path) -> list[CatalogObject]:
    """Read a generic catalog CSV into catalog rows."""

    with path.open(newline="") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        reader = csv.DictReader(fh, dialect=dialect)
        return [CatalogObject({key: value for key, value in row.items() if key}) for row in reader]


def write_catalog_objects(path: Path, objects: list[CatalogObject], fieldnames: tuple[str, ...]) -> None:
    """Write generic catalog rows with a stable field order."""

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for obj in objects:
            writer.writerow({field: obj.values.get(field, "") for field in fieldnames})


def normalize_period(value: str) -> str:
    """Return a compact period string, hiding empty and zero VSX periods."""

    text = (value or "").strip()
    if not text:
        return ""
    try:
        period = float(text)
    except ValueError:
        return text
    if period <= 0:
        return ""
    return f"{period:.8g}"


def reference_frame_search_radius_arcmin(frame: ReferenceFrame) -> float:
    """Estimate the cone-search radius needed to cover a reference frame."""

    center = SkyCoord(frame.ra_deg * u.deg, frame.dec_deg * u.deg, frame="icrs")
    try:
        separations = [
            center.separation(corner).arcmin
            for corner in reference_frame_icrs_corners(frame)
        ]
        radius = max(separations) * 1.05
    except Exception:
        radius = reference_frame_fallback_radius_arcmin(frame)
    return max(radius, 5.0)


def reference_frame_icrs_corners(frame: ReferenceFrame) -> tuple[SkyCoord, ...]:
    """Return reference-frame corners in stable pixel-coordinate order."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        with fits.open(frame.path) as hdul:
            wcs = WCS(hdul[0].header).celestial
    pixels = (
        (0, 0),
        (max(frame.width - 1, 0), 0),
        (max(frame.width - 1, 0), max(frame.height - 1, 0)),
        (0, max(frame.height - 1, 0)),
    )
    corners = tuple(wcs.pixel_to_world(x, y) for x, y in pixels)
    if any(
        not np.isfinite(float(corner.ra.deg))
        or not np.isfinite(float(corner.dec.deg))
        for corner in corners
    ):
        raise ValueError("Reference-frame WCS produced a non-finite sky footprint.")
    return corners


def reference_frame_field_metadata(frame: ReferenceFrame) -> dict[str, str]:
    """Serialize the solved reference field used for catalog queries."""

    corners = reference_frame_icrs_corners(frame)
    footprint = ";".join(
        f"{float(corner.ra.deg):.8f},{float(corner.dec.deg):.8f}"
        for corner in corners
    )
    return {
        "FIELD_CENTER_RA_DEG": f"{frame.ra_deg:.8f}",
        "FIELD_CENTER_DEC_DEG": f"{frame.dec_deg:.8f}",
        "FIELD_FOOTPRINT_ICRS": footprint,
        "FIELD_FOOTPRINT_ORDER": FIELD_FOOTPRINT_ORDER,
        "FIELD_SEARCH_RADIUS_ARCMIN": f"{reference_frame_search_radius_arcmin(frame):.6f}",
    }


def reference_frame_fallback_radius_arcmin(frame: ReferenceFrame) -> float:
    """Estimate reference-frame search radius from image size and pixel scale."""

    scale_arcsec = 4.0
    try:
        with fits.open(frame.path) as hdul:
            header = hdul[0].header
        cdelt1 = header.get("CDELT1")
        cdelt2 = header.get("CDELT2")
        if cdelt1 not in (None, 0) and cdelt2 not in (None, 0):
            scale_arcsec = (abs(float(cdelt1)) + abs(float(cdelt2))) * 1800.0
        else:
            cd11 = header.get("CD1_1")
            cd12 = header.get("CD1_2", 0.0)
            cd21 = header.get("CD2_1", 0.0)
            cd22 = header.get("CD2_2")
            if cd11 is not None and cd22 is not None:
                scale_x = math.hypot(float(cd11), float(cd12)) * 3600.0
                scale_y = math.hypot(float(cd21), float(cd22)) * 3600.0
                scale_arcsec = (scale_x + scale_y) / 2.0
    except Exception:
        pass
    half_diagonal_px = math.hypot(max(frame.width, 1), max(frame.height, 1)) / 2.0
    return half_diagonal_px * scale_arcsec / 60.0 * 1.05


def reference_frame_pixel_scale_arcsec(frame: ReferenceFrame) -> float:
    """Return an approximate WCS pixel scale for small-radius catalog checks."""

    try:
        with fits.open(frame.path) as hdul:
            header = hdul[0].header
        cdelt1 = header.get("CDELT1")
        cdelt2 = header.get("CDELT2")
        if cdelt1 not in (None, 0) and cdelt2 not in (None, 0):
            return (abs(float(cdelt1)) + abs(float(cdelt2))) * 1800.0
        cd11 = header.get("CD1_1")
        cd12 = header.get("CD1_2", 0.0)
        cd21 = header.get("CD2_1", 0.0)
        cd22 = header.get("CD2_2")
        if cd11 is not None and cd22 is not None:
            scale_x = math.hypot(float(cd11), float(cd12)) * 3600.0
            scale_y = math.hypot(float(cd21), float(cd22)) * 3600.0
            return (scale_x + scale_y) / 2.0
    except Exception:
        pass
    return 4.0


def parse_vizier_tap_tsv(payload: str) -> list[CatalogObject]:
    """Parse VizieR TAP TSV results into catalog objects."""

    lines = [
        line
        for line in payload.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not lines:
        return []
    reader = csv.DictReader(lines, delimiter="\t")
    rows: list[CatalogObject] = []
    for row in reader:
        values = {
            key: (value or "").strip().strip('"')
            for key, value in row.items()
            if key
        }
        rows.append(CatalogObject(values))
    return rows


def parse_apass_dr10_tsv(payload: str) -> list[CatalogObject]:
    """Parse APASS DR10 TAP TSV rows into catalog objects."""

    source_columns = (
        "id",
        "ra",
        "dec",
        "mag_v",
        "err_mag_v",
        "nobs_v",
        "mag_b",
        "err_mag_b",
        "mag_g",
        "err_mag_g",
        "mag_r",
        "err_mag_r",
    )
    rows: list[CatalogObject] = []
    for line in payload.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip().strip('"') for part in line.split("\t")]
        if parts and parts[0].lower() == "id":
            continue
        if len(parts) < len(source_columns):
            continue
        raw_values = dict(zip(source_columns, parts, strict=False))
        apass_id = raw_values.get("id", "").strip()
        if not apass_id:
            continue
        values = {
            "id": apass_id,
            "APASS_DR10_ID": apass_id,
            "catalog_source": APASS_DR10_SOURCE_NAME,
        }
        for key in source_columns:
            if key == "id":
                continue
            value = raw_values.get(key, "").strip()
            if value in {"", "null", "NULL", "NaN", "nan"}:
                value = ""
            values[key] = value
        rows.append(CatalogObject(values))
    return rows


def ucac4_error_to_mag(value: str) -> str:
    """Return UCAC4 millimag-style error columns as magnitudes."""

    text = (value or "").strip()
    if text in {"", "null", "NULL", "NaN", "nan"}:
        return ""
    try:
        error = float(text)
    except ValueError:
        return ""
    if error > 1.0:
        error /= 1000.0
    return f"{error:.6f}"


def parse_ucac4_tsv(payload: str) -> list[CatalogObject]:
    """Parse VizieR UCAC4 TAP TSV rows into the app's comparison-catalog shape."""

    source_columns = (
        "UCAC4",
        "RAJ2000",
        "DEJ2000",
        "Vmag",
        "e_Vmag",
        "Bmag",
        "e_Bmag",
        "gmag",
        "e_gmag",
        "rmag",
        "e_rmag",
    )
    rows: list[CatalogObject] = []
    for line in payload.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip().strip('"') for part in line.split("\t")]
        if parts and parts[0] == "UCAC4":
            continue
        if len(parts) < len(source_columns):
            continue
        raw_values = dict(zip(source_columns, parts, strict=False))
        ucac4_id = raw_values.get("UCAC4", "").strip()
        if not ucac4_id:
            continue
        values = {
            "id": ucac4_id,
            "UCAC4": ucac4_id,
            "catalog_source": UCAC4_SOURCE_NAME,
            "ra": raw_values.get("RAJ2000", "").strip(),
            "dec": raw_values.get("DEJ2000", "").strip(),
            "mag_v": raw_values.get("Vmag", "").strip(),
            "err_mag_v": ucac4_error_to_mag(raw_values.get("e_Vmag", "")),
            "nobs_v": "",
            "mag_b": raw_values.get("Bmag", "").strip(),
            "err_mag_b": ucac4_error_to_mag(raw_values.get("e_Bmag", "")),
            "mag_g": raw_values.get("gmag", "").strip(),
            "err_mag_g": ucac4_error_to_mag(raw_values.get("e_gmag", "")),
            "mag_r": raw_values.get("rmag", "").strip(),
            "err_mag_r": ucac4_error_to_mag(raw_values.get("e_rmag", "")),
        }
        rows.append(CatalogObject(values))
    return rows


def parse_gaia_dr3_target_blend_tsv(payload: str) -> list[CatalogObject]:
    """Parse VizieR Gaia DR3 rows for target-neighbor blend detection."""

    source_columns = (
        "Source",
        "RA_ICRS",
        "DE_ICRS",
        "Gmag",
        "BPmag",
        "RPmag",
        "Dup",
        "IPDfmp",
        "IPDfow",
    )
    rows: list[CatalogObject] = []
    for line in payload.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = [part.strip().strip('"') for part in line.split("\t")]
        if parts and parts[0] == "Source":
            continue
        if len(parts) < len(source_columns):
            continue
        raw_values = dict(zip(source_columns, parts, strict=False))
        source_id = raw_values.get("Source", "").strip()
        if not source_id:
            continue
        values = {
            "id": source_id,
            "Name": source_id,
            "gaia_source_id": source_id,
            "catalog_source": GAIA_DR3_SOURCE_NAME,
            "ra": raw_values.get("RA_ICRS", "").strip(),
            "dec": raw_values.get("DE_ICRS", "").strip(),
            "gaia_g_mag": raw_values.get("Gmag", "").strip(),
            "gaia_bp_mag": raw_values.get("BPmag", "").strip(),
            "gaia_rp_mag": raw_values.get("RPmag", "").strip(),
            "gaia_duplicated_source": raw_values.get("Dup", "").strip(),
            "gaia_ipd_frac_multi_peak": raw_values.get("IPDfmp", "").strip(),
            "gaia_ipd_frac_odd_win": raw_values.get("IPDfow", "").strip(),
        }
        rows.append(CatalogObject(values))
    return rows


def query_apass_dr10_region(
    frame: ReferenceFrame,
    target_mag: float | None,
    dvmag: float,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Query APASS DR10 candidate stars covering the prepared reference frame."""

    radius_deg = reference_frame_search_radius_arcmin(frame) / 60.0
    if target_mag is None:
        min_mag = APASS_DR10_MIN_MAG
        max_mag = APASS_DR10_MAX_MAG
        order_clause = "ORDER BY mag_v"
    else:
        min_mag = max(APASS_DR10_MIN_MAG, target_mag - dvmag)
        max_mag = min(APASS_DR10_MAX_MAG, target_mag + dvmag)
        order_clause = f"ORDER BY ABS(mag_v - {target_mag:.4f})"
    query = f"""
SELECT TOP {APASS_DR10_MAX_QUERY_ROWS} id, ra, dec, mag_v, err_mag_v, nobs_v,
       mag_b, err_mag_b, mag_g, err_mag_g, mag_r, err_mag_r
FROM apass.dr10
WHERE 1=CONTAINS(
  POINT(ra, dec),
  CIRCLE({frame.ra_deg:.8f}, {frame.dec_deg:.8f}, {radius_deg:.8f})
)
  AND mag_v IS NOT NULL
  AND mag_v BETWEEN {min_mag:.2f} AND {max_mag:.2f}
  AND nobs_v IS NOT NULL
  AND nobs_v >= 1
{order_clause}
"""
    payload = urllib.parse.urlencode(
        {
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "tsv",
            "MAXREC": "100000",
            "QUERY": query,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        APASS_DR10_TAP_URL,
        data=payload,
        headers={"User-Agent": f"SeePhot/{SCRIPT_VERSION}"},
    )
    last_exc: Exception | None = None
    for attempt in range(1, APASS_DR10_QUERY_RETRIES + 1):
        if progress is not None:
            progress(
                "APASS DR10 TAP request "
                f"{attempt}/{APASS_DR10_QUERY_RETRIES} "
                f"(timeout {APASS_DR10_QUERY_TIMEOUT_SECONDS}s)."
            )
        try:
            with urllib.request.urlopen(
                request,
                timeout=APASS_DR10_QUERY_TIMEOUT_SECONDS,
            ) as response:
                text = response.read().decode("utf-8", "replace")
            return parse_apass_dr10_tsv(text)
        except Exception as exc:
            last_exc = exc
            if progress is not None:
                progress(f"WARNING: APASS DR10 TAP request {attempt} failed: {exc}")
            if attempt < APASS_DR10_QUERY_RETRIES:
                time.sleep(2.0 * attempt)
    raise RuntimeError(
        "APASS DR10 TAP query failed after "
        f"{APASS_DR10_QUERY_RETRIES} attempt(s): {last_exc}"
    ) from last_exc


def query_ucac4_region(
    frame: ReferenceFrame,
    target_mag: float | None,
    dvmag: float,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Query UCAC4 candidate stars from VizieR for fields where APASS DR10 is empty."""

    radius_deg = reference_frame_search_radius_arcmin(frame) / 60.0
    if target_mag is None:
        min_mag = APASS_DR10_MIN_MAG
        max_mag = APASS_DR10_MAX_MAG
    else:
        min_mag = max(APASS_DR10_MIN_MAG, target_mag - dvmag)
        max_mag = min(APASS_DR10_MAX_MAG, target_mag + dvmag)
    query = f"""
SELECT TOP {APASS_DR10_MAX_QUERY_ROWS} UCAC4, RAJ2000, DEJ2000,
       Vmag, e_Vmag, Bmag, e_Bmag, gmag, e_gmag, rmag, e_rmag
FROM "I/322A/out"
WHERE 1=CONTAINS(
  POINT('ICRS', RAJ2000, DEJ2000),
  CIRCLE('ICRS', {frame.ra_deg:.8f}, {frame.dec_deg:.8f}, {radius_deg:.8f})
)
  AND Vmag IS NOT NULL
  AND Vmag BETWEEN {min_mag:.2f} AND {max_mag:.2f}
ORDER BY Vmag
"""
    payload = urllib.parse.urlencode(
        {
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "tsv",
            "MAXREC": "100000",
            "QUERY": query,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        UCAC4_TAP_URL,
        data=payload,
        headers={"User-Agent": f"SeePhot/{SCRIPT_VERSION}"},
    )
    if progress is not None:
        progress(
            "UCAC4 VizieR TAP request "
            f"(timeout {UCAC4_QUERY_TIMEOUT_SECONDS}s)."
        )
    with urllib.request.urlopen(
        request,
        timeout=UCAC4_QUERY_TIMEOUT_SECONDS,
    ) as response:
        text = response.read().decode("utf-8", "replace")
    objects = parse_ucac4_tsv(text)
    if target_mag is not None:
        objects.sort(
            key=lambda candidate: abs(
                (candidate.magnitude_float() or target_mag) - target_mag
            )
        )
    return objects


def query_comparison_catalog_region(
    frame: ReferenceFrame,
    target_mag: float | None,
    dvmag: float,
    progress: Callable[[str], None] | None = None,
    catalog_source: str | None = None,
) -> list[CatalogObject]:
    """Query comparison candidates from the selected catalog or the internal auto mode."""

    if catalog_source == UCAC4_SOURCE_NAME:
        return query_ucac4_region(frame, target_mag, dvmag, progress)
    if catalog_source == APASS_DR10_SOURCE_NAME:
        return query_apass_dr10_region(frame, target_mag, dvmag, progress)

    try:
        objects = query_apass_dr10_region(frame, target_mag, dvmag, progress)
    except Exception as exc:
        if progress is not None:
            progress(f"WARNING: APASS DR10 query failed; falling back to UCAC4: {exc}")
        objects = []
    if len(objects) >= COMPARISON_CATALOG_FALLBACK_MIN_CANDIDATES:
        return objects
    if progress is not None:
        progress(
            "APASS DR10 returned "
            f"{len(objects)} candidate(s), fewer than "
            f"{COMPARISON_CATALOG_FALLBACK_MIN_CANDIDATES}; falling back to UCAC4 via VizieR."
        )
    return query_ucac4_region(frame, target_mag, dvmag, progress)


def should_try_post_vetting_ucac4(
    catalog_source: str | None,
    objects: list[CatalogObject],
    vetted_count: int,
) -> bool:
    """Return whether auto mode should retry a weak APASS result with UCAC4."""

    if catalog_source is not None or vetted_count >= COMPARISON_CATALOG_FALLBACK_MIN_CANDIDATES:
        return False
    sources = {obj.catalog_source for obj in objects if obj.catalog_source}
    return sources == {APASS_DR10_SOURCE_NAME}


def query_gaia_dr3_target_neighbors(
    ra_deg: float,
    dec_deg: float,
    radius_arcsec: float,
    target_mag: float | None,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Query Gaia DR3 in a small target-centered radius for blend detection."""

    radius_deg = max(radius_arcsec, 1.0) / 3600.0
    # The quantitative model needs the full useful Gaia range so individually
    # faint neighbors can be summed. target_mag remains in the public query
    # signature for callers, but no longer truncates the neighbor population.
    min_mag = 0.0
    max_mag = 21.0
    query = f"""
SELECT TOP {TARGET_BLEND_GAIA_QUERY_MAX_ROWS} Source, RA_ICRS, DE_ICRS, Gmag, BPmag, RPmag,
       Dup, IPDfmp, IPDfow,
       DISTANCE(
         POINT('ICRS', RA_ICRS, DE_ICRS),
         POINT('ICRS', {ra_deg:.8f}, {dec_deg:.8f})
       ) AS target_distance_deg
FROM "I/355/gaiadr3"
WHERE 1=CONTAINS(
  POINT('ICRS', RA_ICRS, DE_ICRS),
  CIRCLE('ICRS', {ra_deg:.8f}, {dec_deg:.8f}, {radius_deg:.8f})
)
  AND Gmag IS NOT NULL
  AND Gmag BETWEEN {min_mag:.2f} AND {max_mag:.2f}
ORDER BY target_distance_deg
"""
    payload = urllib.parse.urlencode(
        {
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "tsv",
            "MAXREC": "1000",
            "QUERY": query,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        UCAC4_TAP_URL,
        data=payload,
        headers={"User-Agent": f"SeePhot/{SCRIPT_VERSION}"},
    )
    last_exc: Exception | None = None
    for attempt in range(1, TARGET_BLEND_VIZIER_QUERY_RETRIES + 1):
        if progress is not None:
            progress(
                "Gaia DR3 target-blend request "
                f"{attempt}/{TARGET_BLEND_VIZIER_QUERY_RETRIES} "
                f"(radius={radius_arcsec:.1f} arcsec, "
                f"timeout {TARGET_BLEND_VIZIER_QUERY_TIMEOUT_SECONDS}s)."
            )
        try:
            with urllib.request.urlopen(
                request,
                timeout=TARGET_BLEND_VIZIER_QUERY_TIMEOUT_SECONDS,
            ) as response:
                text = response.read().decode("utf-8", "replace")
            return parse_gaia_dr3_target_blend_tsv(text)
        except Exception as exc:
            last_exc = exc
            if progress is not None:
                progress(
                    "WARNING: Gaia DR3 target-blend request "
                    f"{attempt}/{TARGET_BLEND_VIZIER_QUERY_RETRIES} failed: {exc}"
                )
            if attempt < TARGET_BLEND_VIZIER_QUERY_RETRIES:
                time.sleep(TARGET_BLEND_VIZIER_QUERY_RETRY_DELAY_SECONDS)
    raise RuntimeError(
        "Gaia DR3 target-blend query failed after "
        f"{TARGET_BLEND_VIZIER_QUERY_RETRIES} attempt(s): {last_exc}"
    ) from last_exc


def gaia_field_query_radius_deg(frame: ReferenceFrame) -> float:
    """Return a cone radius that encloses the complete WCS pixel footprint."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        with fits.open(frame.path) as hdul:
            wcs = WCS(hdul[0].header).celestial
    center = SkyCoord(frame.ra_deg * u.deg, frame.dec_deg * u.deg, frame="icrs")
    corners = wcs.pixel_to_world(
        np.asarray([0.0, frame.width - 1.0, frame.width - 1.0, 0.0]),
        np.asarray([0.0, 0.0, frame.height - 1.0, frame.height - 1.0]),
    )
    radius_deg = float(np.max(center.separation(corners).deg))
    return radius_deg * 1.000001 + 1e-9


def build_gaia_ari_field_adql(
    frame: ReferenceFrame,
    min_g_mag: float,
    max_g_mag: float,
) -> str:
    """Build the ARI Gaia sync query used by the linearity tool."""

    radius_deg = gaia_field_query_radius_deg(frame)
    return f"""
SELECT
    source_id,
    ra,
    dec,
    phot_g_mean_mag,
    phot_bp_mean_mag,
    phot_rp_mean_mag,
    bp_rp
FROM {GAIA_ARI_SOURCE_TABLE}
WHERE
    1 = CONTAINS(
        POINT('ICRS', ra, dec),
        CIRCLE('ICRS', {frame.ra_deg:.10f}, {frame.dec_deg:.10f}, {radius_deg:.10f})
    )
    AND phot_g_mean_mag BETWEEN {min_g_mag:.6f} AND {max_g_mag:.6f}
    AND phot_bp_mean_mag IS NOT NULL
    AND phot_rp_mean_mag IS NOT NULL
""".strip()


def _gaia_ari_row_value(row: object, name: str) -> object | None:
    value = row[name]
    if np.ma.is_masked(value):
        return None
    return value


def gaia_objects_from_ari_table(table: object) -> list[CatalogObject]:
    """Convert the Gaia archive column contract into SeePhot catalog objects."""

    required_columns = (
        "source_id",
        "ra",
        "dec",
        "phot_g_mean_mag",
        "phot_bp_mean_mag",
        "phot_rp_mean_mag",
        "bp_rp",
    )
    column_names = tuple(getattr(table, "colnames", ()))
    missing = [name for name in required_columns if name not in column_names]
    if missing:
        raise RuntimeError(f"ARI Gaia TAP response is missing column(s): {', '.join(missing)}")
    objects: list[CatalogObject] = []
    for row in table:
        values = {name: _gaia_ari_row_value(row, name) for name in required_columns}
        if any(values[name] is None for name in required_columns):
            continue
        source_id = str(values["source_id"])
        objects.append(
            CatalogObject(
                {
                    "id": source_id,
                    "Name": source_id,
                    "gaia_source_id": source_id,
                    "catalog_source": GAIA_DR3_SOURCE_NAME,
                    "ra": str(values["ra"]),
                    "dec": str(values["dec"]),
                    "gaia_g_mag": str(values["phot_g_mean_mag"]),
                    "gaia_bp_mag": str(values["phot_bp_mean_mag"]),
                    "gaia_rp_mag": str(values["phot_rp_mean_mag"]),
                }
            )
        )
    return objects


def filter_and_sample_gaia_ari_table(
    frame: ReferenceFrame,
    table: object,
    max_sources: int = GAIA_LINEARITY_SAMPLE_SIZE,
    isolation_radius_px: float | None = None,
    isolation_max_g_mag_delta: float = 3.0,
) -> tuple[object, int]:
    """Fill a balanced sample with isolated sources from the WCS footprint."""

    if max_sources < 1:
        raise ValueError("Gaia linearity sample size must be positive.")
    gaia_objects_from_ari_table(table[:0])
    if len(table) == 0:
        return table, 0
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        with fits.open(frame.path) as hdul:
            wcs = WCS(hdul[0].header).celestial
    ra = np.asarray(np.ma.filled(table["ra"], np.nan), dtype=float)
    dec = np.asarray(np.ma.filled(table["dec"], np.nan), dtype=float)
    g_mag = np.asarray(np.ma.filled(table["phot_g_mean_mag"], np.nan), dtype=float)
    bp_rp = np.asarray(np.ma.filled(table["bp_rp"], np.nan), dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        x, y = wcs.world_to_pixel_values(ra, dec)
    valid = (
        np.isfinite(x)
        & np.isfinite(y)
        & np.isfinite(g_mag)
        & np.isfinite(bp_rp)
        & (x >= 0.0)
        & (x < frame.width)
        & (y >= 0.0)
        & (y < frame.height)
        & (bp_rp >= GAIA_FIELD_COLOR_BIN_EDGES[0])
        & (bp_rp <= GAIA_FIELD_COLOR_BIN_EDGES[-1])
    )
    inside_indices = np.flatnonzero(valid)
    inside_count = int(inside_indices.size)
    if inside_count <= max_sources:
        candidate_indices = inside_indices
    else:
        x_bin = np.minimum(3, np.asarray(x[inside_indices] * 4.0 / frame.width, dtype=int))
        y_bin = np.minimum(3, np.asarray(y[inside_indices] * 4.0 / frame.height, dtype=int))
        magnitude_bin = np.clip(
            np.digitize(g_mag[inside_indices], GAIA_FIELD_MAG_BIN_EDGES) - 1,
            0,
            len(GAIA_FIELD_MAG_BIN_EDGES) - 2,
        )
        color_bin = np.clip(
            np.digitize(bp_rp[inside_indices], GAIA_FIELD_COLOR_BIN_EDGES) - 1,
            0,
            len(GAIA_FIELD_COLOR_BIN_EDGES) - 2,
        )
        stratum = (
            ((y_bin * 4 + x_bin) * (len(GAIA_FIELD_MAG_BIN_EDGES) - 1) + magnitude_bin)
            * (len(GAIA_FIELD_COLOR_BIN_EDGES) - 1)
            + color_bin
        )
        source_ids = np.asarray(table["source_id"])[inside_indices]
        by_stratum = np.lexsort((source_ids, stratum))
        sorted_strata = stratum[by_stratum]
        group_starts = np.r_[True, sorted_strata[1:] != sorted_strata[:-1]]
        start_positions = np.maximum.accumulate(
            np.where(group_starts, np.arange(inside_count), 0)
        )
        levels = np.arange(inside_count) - start_positions
        balanced_order = np.lexsort((sorted_strata, levels))
        candidate_indices = inside_indices[by_stratum[balanced_order]]

    if isolation_radius_px is None:
        selected_indices = candidate_indices[:max_sources]
    else:
        if not np.isfinite(isolation_radius_px) or isolation_radius_px <= 0:
            raise ValueError("Gaia isolation radius must be finite and positive.")
        from scipy.spatial import cKDTree

        neighbor_indices = np.flatnonzero(
            np.isfinite(x) & np.isfinite(y) & np.isfinite(g_mag)
        )
        tree = cKDTree(np.column_stack((x[neighbor_indices], y[neighbor_indices])))
        all_ids = np.asarray(table["source_id"])
        selected: list[int] = []
        for start in range(0, len(candidate_indices), 512):
            chunk = candidate_indices[start : start + 512]
            nearby = tree.query_ball_point(
                np.column_stack((x[chunk], y[chunk])), isolation_radius_px
            )
            for index, neighbors in zip(chunk, nearby):
                if not any(
                    all_ids[neighbor_indices[j]] != all_ids[index]
                    and g_mag[neighbor_indices[j]] - g_mag[index]
                    <= isolation_max_g_mag_delta
                    for j in neighbors
                ):
                    selected.append(int(index))
                    if len(selected) >= max_sources:
                        break
            if len(selected) >= max_sources:
                break
        selected_indices = np.asarray(selected, dtype=int)
    return table[selected_indices], inside_count


def query_gaia_dr3_region(
    frame: ReferenceFrame,
    progress: Callable[[str], None] | None = None,
    min_g_mag: float = 5.0,
    max_g_mag: float = 18.5,
    maxrec: int = GAIA_FIELD_MAXREC,
    isolation_radius_px: float | None = None,
    isolation_max_g_mag_delta: float = 3.0,
) -> list[CatalogObject]:
    """Query Gaia DR3 synchronously through ARI TAP for one solved image."""

    if not min_g_mag < max_g_mag:
        raise ValueError("Gaia magnitude limits must satisfy min_g_mag < max_g_mag.")
    if maxrec < 1:
        raise ValueError("Gaia TAP MAXREC must be positive.")
    query = build_gaia_ari_field_adql(frame, min_g_mag, max_g_mag)
    if progress is not None:
        progress(
            "Querying Gaia DR3 through ARI TAP sync "
            f"(G={min_g_mag:.1f}..{max_g_mag:.1f}, MAXREC={maxrec})."
        )
    try:
        service = pyvo.dal.TAPService(GAIA_ARI_TAP_URL)
        result = service.run_sync(query, maxrec=maxrec)
        table = result.to_table()
    except Exception as exc:
        raise RuntimeError(f"ARI Gaia DR3 TAP sync query failed: {exc}") from exc
    if progress is not None:
        progress(f"ARI Gaia DR3 query complete: {len(table)} source(s); filtering locally.")
    selected_table, inside_count = filter_and_sample_gaia_ari_table(
        frame,
        table,
        isolation_radius_px=isolation_radius_px,
        isolation_max_g_mag_delta=isolation_max_g_mag_delta,
    )
    objects = gaia_objects_from_ari_table(selected_table)
    if progress is not None:
        progress(
            f"Gaia footprint contains {inside_count} usable source(s); "
            f"selected {len(objects)} balanced, isolated reference(s) for aperture measurement."
        )
    return objects


def query_target_blend_catalog(
    ra_deg: float,
    dec_deg: float,
    radius_arcsec: float,
    target_mag: float | None,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Return small-radius target-neighbor candidates from Gaia DR3."""

    return query_gaia_dr3_target_neighbors(
        ra_deg,
        dec_deg,
        radius_arcsec,
        target_mag,
        progress,
    )


def catalog_query_error_message(catalog_source: str, exc: Exception) -> str:
    """Return a user-facing message that distinguishes no response from empty results."""

    text = str(exc)
    lower_text = text.lower()
    if "timed out" in lower_text or "timeout" in lower_text:
        return (
            f"{catalog_source} catalog query timed out. This is no catalog response, "
            "not a valid 0-star result."
        )
    return f"{catalog_source} catalog query failed: {text}"


def vsx_api_magnitude_parts(value: str) -> tuple[str, str, str]:
    """Split one live-VSX magnitude into number, qualifier, and passband."""

    match = re.match(r"^\s*([<>=:]?)\s*(\d+(?:\.\d*)?|\.\d+)\s*([<>=:]?)\s*(.*?)\s*$", value)
    if match is None:
        return value.strip(), "", ""
    leading, magnitude, trailing, band = match.groups()
    return magnitude, leading or trailing, band.strip()


def parse_aavso_vsx_api_xml(payload: str) -> list[CatalogObject]:
    """Convert direct AAVSO VSX XML to the internal VSX object shape."""

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError(f"AAVSO VSX returned invalid XML: {exc}") from exc
    rows: list[CatalogObject] = []
    for element in root.findall("VSXObject"):
        source = {child.tag: (child.text or "").strip() for child in element}
        max_mag, max_qualifier, max_band = vsx_api_magnitude_parts(source.get("MaxMag", ""))
        min_mag, min_qualifier, min_band = vsx_api_magnitude_parts(source.get("MinMag", ""))
        rows.append(CatalogObject({
            "OID": source.get("OID", ""), "Name": source.get("Name", ""),
            "Type": source.get("VariabilityType", ""), "Period": source.get("Period", ""),
            "Epoch": source.get("Epoch", ""), "max": max_mag, "u_max": max_qualifier,
            "n_max": max_band, "min": min_mag, "u_min": min_qualifier,
            "n_min": min_band, "RAJ2000": source.get("RA2000", ""),
            "DEJ2000": source.get("Declination2000", ""),
        }))
    return rows


def query_aavso_vsx_region(frame: ReferenceFrame) -> list[CatalogObject]:
    """Query the live AAVSO VSX field API with the maximum-magnitude limit."""

    radius_deg = reference_frame_search_radius_arcmin(frame) / 60.0
    url = "https://vsx.aavso.org/index.php?" + urllib.parse.urlencode({
        "view": "api.list", "ra": f"{frame.ra_deg:.8f}", "dec": f"{frame.dec_deg:.8f}",
        "radius": f"{radius_deg:.8f}", "tomag": f"{DEFAULT_VSX_LIMIT_MAG:.2f}", "format": "xml",
    })
    request = urllib.request.Request(url, headers={"User-Agent": f"SeePhot/{SCRIPT_VERSION}"})
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = response.read().decode("utf-8", "replace")
    return parse_aavso_vsx_api_xml(payload)


def query_vizier_vsx_region_tap(frame: ReferenceFrame) -> list[CatalogObject]:
    """Query the remote CDS VizieR VSX mirror via TAP."""

    radius_deg = reference_frame_search_radius_arcmin(frame) / 60.0
    query = f"""
SELECT OID, Name, Type, Period, Epoch, l_max, max, u_max, n_max, f_min, l_min, min, u_min, n_min, RAJ2000, DEJ2000
FROM "B/vsx/vsx"
WHERE 1=CONTAINS(
  POINT('ICRS', RAJ2000, DEJ2000),
  CIRCLE('ICRS', {frame.ra_deg:.8f}, {frame.dec_deg:.8f}, {radius_deg:.8f})
)
  AND max <= {DEFAULT_VSX_LIMIT_MAG:.2f}
"""
    payload = urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "tsv", "QUERY": query}
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync",
        data=payload,
        headers={"User-Agent": f"SeePhot/{SCRIPT_VERSION}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8", "replace")
    return parse_vizier_tap_tsv(text)


def query_vizier_vsx_region_astroquery(frame: ReferenceFrame) -> list[CatalogObject]:
    """Query the remote CDS VizieR VSX mirror through Astroquery."""

    try:
        ensure_importable_module("astroquery")
        from astroquery.vizier import Vizier
    except Exception as exc:
        raise RuntimeError(f"astroquery.vizier is not available: {exc}") from exc
    radius_deg = reference_frame_search_radius_arcmin(frame) / 60.0
    center = SkyCoord(frame.ra_deg * u.deg, frame.dec_deg * u.deg, frame="icrs")
    vizier = Vizier(
        columns=[
            "OID", "Name", "Type", "Period", "Epoch", "l_max", "max", "u_max",
            "n_max", "f_min", "l_min", "min", "u_min", "n_min", "RAJ2000", "DEJ2000",
        ],
        column_filters={"max": f"<={DEFAULT_VSX_LIMIT_MAG:.2f}"},
        row_limit=-1,
    )
    tables = vizier.query_region(center, radius=radius_deg * u.deg, catalog="B/vsx/vsx")
    if not tables:
        return []
    rows: list[CatalogObject] = []
    for table_row in tables[0]:
        values: dict[str, str] = {}
        for column in table_row.colnames:
            value = table_row[column]
            values[column] = "" if hasattr(value, "mask") and bool(value.mask) else str(value).strip()
        rows.append(CatalogObject(values))
    return rows


def query_vsx_region(
    frame: ReferenceFrame,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Query live AAVSO VSX, then two independent remote VizieR routes."""

    methods = (
        ("live AAVSO VSX", query_aavso_vsx_region),
        ("remote VizieR VSX TAP", query_vizier_vsx_region_tap),
        ("remote VizieR VSX Astroquery", query_vizier_vsx_region_astroquery),
    )
    errors: list[str] = []
    received_empty_response = False
    for source_name, method in methods:
        for attempt in range(1, VSX_QUERY_ATTEMPTS + 1):
            try:
                rows = method(frame)
                if rows:
                    if progress is not None:
                        progress(f"{source_name} returned {len(rows)} object(s).")
                    return rows
                received_empty_response = True
                if progress is not None:
                    progress(
                        f"WARNING: {source_name} returned no catalog rows on request "
                        f"{attempt}/{VSX_QUERY_ATTEMPTS}."
                    )
            except Exception as exc:
                errors.append(f"{source_name}: {exc}")
                if progress is not None:
                    progress(
                        f"WARNING: {source_name} request {attempt}/{VSX_QUERY_ATTEMPTS} failed: {exc}"
                    )
            if attempt < VSX_QUERY_ATTEMPTS:
                time.sleep(VSX_QUERY_RETRY_DELAY_SECONDS)
    if received_empty_response:
        return []
    raise RuntimeError("VSX catalog unavailable after all online retries: " + "; ".join(errors))


def query_vizier_vsx_oid(obj: CatalogObject) -> str:
    """Return the AAVSO VSX OID for one object using a small coordinate lookup."""

    coord = sky_coord_for_object(obj)
    if coord is None:
        return ""

    radius_deg = 5.0 / 3600.0
    query = f"""
SELECT TOP 5 OID, Name, RAJ2000, DEJ2000
FROM "B/vsx/vsx"
WHERE 1=CONTAINS(
  POINT('ICRS', RAJ2000, DEJ2000),
  CIRCLE('ICRS', {coord.ra.deg:.8f}, {coord.dec.deg:.8f}, {radius_deg:.8f})
)
"""
    payload = urllib.parse.urlencode(
        {
            "REQUEST": "doQuery",
            "LANG": "ADQL",
            "FORMAT": "tsv",
            "QUERY": query,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync",
        data=payload,
        headers={"User-Agent": f"SeePhot/{SCRIPT_VERSION}"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        text = response.read().decode("utf-8", "replace")

    rows = parse_vizier_tap_tsv(text)
    nearest_oid = ""
    nearest_sep: float | None = None
    for row in rows:
        row_coord = sky_coord_for_object(row)
        if row_coord is None:
            continue
        separation = coord.separation(row_coord).arcsec
        if separation <= 5.0 and (nearest_sep is None or separation < nearest_sep):
            nearest_oid = row.value_for(("OID", "oid"))
            nearest_sep = separation
    return nearest_oid


def aavso_vsx_detail_url(oid: str) -> str:
    """Build the public AAVSO VSX detail URL for an object id."""

    return (
        "https://vsx.aavso.org/index.php?"
        f"oid={urllib.parse.quote(str(oid).strip())}&view=detail.top"
    )


def current_result_vsx_url(metadata: dict[str, str]) -> str:
    """Return the VSX URL from the one supported current result format."""

    if metadata.get("RESULT_METADATA_VERSION", "").strip() != str(
        CURRENT_RESULT_METADATA_VERSION
    ):
        raise ValueError("The result is not in the current result format.")
    required = (
        "OBJECT_NAME",
        "OBJECT_RA",
        "OBJECT_DEC",
        "OBJECT_VSX_OID",
        "OBJECT_VSX_URL",
    )
    missing = [key for key in required if not metadata.get(key, "").strip()]
    if missing:
        raise ValueError(
            "The current result format has incomplete VSX target metadata: "
            + ", ".join(missing)
        )
    return metadata["OBJECT_VSX_URL"].strip()


def vizier_vsx_objects_for_table(
    reference_frame: ReferenceFrame,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Return live AAVSO VSX objects in the shape the table expects."""

    rows = query_vsx_region(reference_frame, progress)
    objects: list[CatalogObject] = []
    for row in rows:
        values = dict(row.values)
        values.setdefault("Name", row.name)
        values.setdefault("Type", row.object_type)
        values.setdefault("Period", normalize_period(row.period))
        values.setdefault("Vmag", row.value_for(("max", "Max", "MaxMag", "mag")))
        values.setdefault("RA", row.ra)
        values.setdefault("DEC", row.dec)
        objects.append(CatalogObject(values))
    return objects


def filter_comparison_candidates_with_vetting(
    candidates: list[CatalogObject],
    target: CatalogObject,
    dvmag: float,
    max_emag: float,
    max_count: int,
    sequence_dir: Path | None = None,
    reference_frame: ReferenceFrame | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[CatalogObject], ComparisonVettingResult | None]:
    """Return catalog-filtered candidates plus reusable photometric vetting data."""

    target_mag = target.magnitude_float()
    if target_mag is None:
        return candidates, None

    if progress is not None:
        progress(f"Filtering {len(candidates)} comparison-catalog candidate(s) for target magnitude.")
    filtered: list[CatalogObject] = []
    target_coord = sky_coord_for_object(target)
    rejected_bad_error = 0
    for candidate in candidates:
        candidate_coord = sky_coord_for_object(candidate)
        if target_coord is not None and candidate_coord is not None:
            if target_coord.separation(candidate_coord).arcsec < 20.0:
                continue
        mag = candidate.magnitude_float()
        if mag is None:
            continue
        mag_error = candidate.magnitude_error_float()
        if mag_error is None or not np.isfinite(mag_error) or mag_error > max_emag:
            rejected_bad_error += 1
            continue
        if abs(mag - target_mag) <= dvmag:
            filtered.append(candidate)
    filtered = sorted(
        filtered,
        key=lambda candidate: abs((candidate.magnitude_float() or target_mag) - target_mag),
    )
    if progress is not None:
        progress(
            f"Comp Star catalog magnitude filter kept {len(filtered)} candidate(s) "
            f"(dV={dvmag:g}, eV<={max_emag:g}; rejected {rejected_bad_error} by eV)."
        )

    if sequence_dir is None:
        return filtered[:max_count], None

    if progress is not None:
        progress("Reading solved WCS frames for Comp Star visibility filtering.")
    frames = read_sequence_wcs_frames(sequence_dir, WORK_SEQUENCE_NAME)
    if not frames:
        if progress is not None:
            progress("No solved WCS frames available for Comp Star visibility filtering.")
        return [], None
    if progress is not None:
        progress(
            f"Checking Comp Star visibility in {len(frames)} solved frame(s); "
            f"need {max_count} final candidate(s)."
        )

    min_frame_size_px = min(min(frame.width, frame.height) for frame in frames)
    visibility_margin_px = max(8.0, min_frame_size_px * SERIES_COMP_VISIBILITY_MARGIN_FRACTION)
    prefilter_count = max(max_count, min(len(filtered), COMP_SELECTION_PREFILTER_LIMIT))
    visible_candidates: list[CatalogObject] = []
    for index, candidate in enumerate(filtered, start=1):
        if object_visible_in_all_frames(candidate, frames, visibility_margin_px):
            visible_candidates.append(candidate)
            if len(visible_candidates) >= prefilter_count:
                break
        if progress is not None and (index % 100 == 0 or index == len(filtered)):
            progress(
                f"Visibility checked {index}/{len(filtered)} comparison-catalog candidate(s); "
                f"found {len(visible_candidates)}/{prefilter_count} prefilter candidate(s)."
            )
    if progress is not None:
        progress(
            "Comparison-star visibility filter selected "
            f"{len(visible_candidates)}/{prefilter_count} prefilter candidate(s) "
            f"with margin {visibility_margin_px:.1f}px "
            f"({SERIES_COMP_VISIBILITY_MARGIN_FRACTION:.0%} of the smaller frame edge)."
        )
    if not visible_candidates:
        strict_vetting_result = None
    else:
        strict_vetting_result = vet_comparison_candidates_by_photometry_with_measurements(
            visible_candidates,
            target,
            sequence_dir,
            reference_frame,
            progress,
        )
        if len(strict_vetting_result.qualities) >= MIN_VALID_COMP_STARS:
            return (
                [quality.candidate for quality in strict_vetting_result.qualities[:max_count]],
                strict_vetting_result,
            )
        if not strict_vetting_result.qualities:
            if progress is not None:
                progress("WARNING: Photometric compstar vetting found no stable candidate.")
        elif progress is not None:
            progress(
                "Strict 100% visibility kept only "
                f"{len(strict_vetting_result.qualities)} photometrically vetted candidate(s); "
                "trying adaptive visibility fallback."
            )

    adaptive_result = adaptive_visibility_vetting_fallback(
        filtered,
        target,
        max_count,
        sequence_dir,
        reference_frame,
        frames,
        visibility_margin_px,
        prefilter_count,
        progress,
    )
    if adaptive_result is not None:
        adaptive_candidates, adaptive_vetting = adaptive_result
        return adaptive_candidates, adaptive_vetting

    if strict_vetting_result is None:
        return [], None
    if not strict_vetting_result.qualities:
        if progress is not None:
            progress("WARNING: Adaptive visibility fallback found no stable candidate.")
        return [], strict_vetting_result
    return (
        [quality.candidate for quality in strict_vetting_result.qualities[:max_count]],
        strict_vetting_result,
    )


def filter_comparison_candidates(
    candidates: list[CatalogObject],
    target: CatalogObject,
    dvmag: float,
    max_emag: float,
    max_count: int,
    sequence_dir: Path | None = None,
    reference_frame: ReferenceFrame | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Return catalog-filtered and photometrically vetted comparison candidates."""

    selected, _vetting_result = filter_comparison_candidates_with_vetting(
        candidates,
        target,
        dvmag,
        max_emag,
        max_count,
        sequence_dir,
        reference_frame,
        progress,
    )
    return selected


def adaptive_visibility_vetting_fallback(
    candidates: list[CatalogObject],
    target: CatalogObject,
    max_count: int,
    sequence_dir: Path,
    reference_frame: ReferenceFrame | None,
    frames: list[SequenceWcsFrame],
    visibility_margin_px: float,
    prefilter_count: int,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[CatalogObject], ComparisonVettingResult] | None:
    """Try relaxed visibility fractions only after strict all-frame visibility fails."""

    if not candidates or not frames:
        return None

    if progress is not None:
        progress(
            "Adaptive Comp Star visibility fallback: measuring candidate visibility "
            f"over {len(frames)} solved frame(s)."
        )
    stats: list[CandidateVisibilityStats] = []
    minimum_visibility_fraction = min(SERIES_COMP_ADAPTIVE_VISIBILITY_FRACTIONS)
    for index, candidate in enumerate(candidates, start=1):
        visible_count = object_visible_count_in_frames(
            candidate,
            frames,
            visibility_margin_px,
            minimum_visibility_fraction,
        )
        if visible_count > 0:
            stats.append(
                CandidateVisibilityStats(
                    candidate=candidate,
                    visible_count=visible_count,
                    frame_count=len(frames),
                )
            )
        if progress is not None and (index % 100 == 0 or index == len(candidates)):
            progress(
                "Adaptive visibility checked "
                f"{index}/{len(candidates)} comparison-catalog candidate(s)."
            )

    for visibility_fraction in SERIES_COMP_ADAPTIVE_VISIBILITY_FRACTIONS:
        adaptive_candidates = [
            item.candidate
            for item in stats
            if item.visible_fraction >= visibility_fraction
        ][:prefilter_count]
        if progress is not None:
            progress(
                "Adaptive visibility threshold "
                f"{visibility_fraction:.0%}: {len(adaptive_candidates)}/"
                f"{prefilter_count} prefilter candidate(s)."
            )
        if len(adaptive_candidates) < MIN_VALID_COMP_STARS:
            continue

        vetting_result = vet_comparison_candidates_by_photometry_with_measurements(
            adaptive_candidates,
            target,
            sequence_dir,
            reference_frame,
            progress,
        )
        if len(vetting_result.qualities) >= MIN_VALID_COMP_STARS:
            if progress is not None:
                progress(
                    "Adaptive Comp Star visibility accepted "
                    f"{visibility_fraction:.0%} threshold with "
                    f"{len(vetting_result.qualities)} photometrically vetted candidate(s). "
                    f"Frames still require at least {MIN_VALID_COMP_STARS} valid Comp Stars."
                )
            return (
                [quality.candidate for quality in vetting_result.qualities[:max_count]],
                vetting_result,
            )
        if progress is not None:
            progress(
                "Adaptive visibility threshold "
                f"{visibility_fraction:.0%} kept only "
                f"{len(vetting_result.qualities)} photometrically vetted candidate(s)."
            )

    return None


def coordinate_pair(obj: CatalogObject, label: str) -> tuple[float, float]:
    """Return RA/Dec in decimal degrees from a catalog object."""

    try:
        return float(obj.ra), float(obj.dec)
    except ValueError as exc:
        raise ValueError(f"{label} has invalid coordinates: RA={obj.ra!r}, Dec={obj.dec!r}") from exc


def sky_coord_for_object(obj: CatalogObject) -> SkyCoord | None:
    """Return a SkyCoord for a catalog object, or None for invalid coordinates."""

    try:
        return SkyCoord(float(obj.ra) * u.deg, float(obj.dec) * u.deg, frame="icrs")
    except ValueError:
        return None


def catalog_has_close_neighbor(
    candidate: CatalogObject,
    catalog_rows: list[CatalogObject],
    max_separation_arcsec: float,
    max_mag_delta: float,
) -> bool:
    """Return True if a catalog source has a close, not much fainter neighbor."""

    candidate_coord = sky_coord_for_object(candidate)
    candidate_mag = candidate.magnitude_float()
    if candidate_coord is None or candidate_mag is None:
        return False
    candidate_key = candidate.catalog_id or candidate.name
    for other in catalog_rows:
        other_key = other.catalog_id or other.name
        if other_key == candidate_key:
            continue
        other_coord = sky_coord_for_object(other)
        other_mag = other.magnitude_float()
        if other_coord is None or other_mag is None:
            continue
        if candidate_coord.separation(other_coord).arcsec >= max_separation_arcsec:
            continue
        if other_mag - candidate_mag <= max_mag_delta:
            return True
    return False


def catalog_has_pixel_neighbor(
    candidate: CatalogObject,
    catalog_rows: list[CatalogObject],
    frame: ReferenceFrame,
    max_separation_px: float,
    max_mag_delta: float,
) -> bool:
    """Return True if a catalog source has a not-much-fainter neighbor in pixel radius."""

    candidate_mag = candidate.magnitude_float()
    if candidate_mag is None:
        return False
    candidate_x, candidate_y, problem = object_pixel_position_in_frame(candidate, frame)
    if problem is not None or not np.isfinite(candidate_x) or not np.isfinite(candidate_y):
        return False
    candidate_key = candidate.catalog_id or candidate.name
    for other in catalog_rows:
        other_key = other.catalog_id or other.name
        if other_key == candidate_key:
            continue
        other_mag = other.magnitude_float()
        if other_mag is None or other_mag - candidate_mag > max_mag_delta:
            continue
        other_x, other_y, other_problem = object_pixel_position_in_frame(other, frame)
        if other_problem is not None or not np.isfinite(other_x) or not np.isfinite(other_y):
            continue
        if math.hypot(other_x - candidate_x, other_y - candidate_y) < max_separation_px:
            return True
    return False


def object_pixel_position_in_frame(
    obj: CatalogObject,
    frame: ReferenceFrame,
    margin_px: float = 0.0,
) -> tuple[float, float, str | None]:
    """Return object pixel coordinates in a reference frame plus an optional problem."""

    coord = sky_coord_for_object(obj)
    if coord is None:
        return float("nan"), float("nan"), (
            f"invalid coordinates RA={obj.ra!r}, Dec={obj.dec!r}"
        )
    try:
        path = Path(frame.path)
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        cache_key = (str(path), mtime_ns)
        wcs = _FRAME_WCS_CACHE.get(cache_key)
        if wcs is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                with fits.open(path) as hdul:
                    wcs = WCS(hdul[0].header)
            if len(_FRAME_WCS_CACHE) > 16:
                _FRAME_WCS_CACHE.clear()
            _FRAME_WCS_CACHE[cache_key] = wcs
        x, y = wcs.world_to_pixel(coord)
        x = float(x)
        y = float(y)
    except Exception as exc:
        return float("nan"), float("nan"), f"WCS conversion failed: {exc}"
    if not np.isfinite(x) or not np.isfinite(y):
        return x, y, "WCS conversion returned non-finite pixel coordinates"
    if x < margin_px or y < margin_px or x >= frame.width - margin_px or y >= frame.height - margin_px:
        return x, y, (
            f"outside usable reference-frame area at x={x:.1f}, y={y:.1f} "
            f"(image size {frame.width}x{frame.height}, required margin {margin_px:.1f}px)"
        )
    return x, y, None


def catalog_object_short_label(obj: CatalogObject) -> str:
    """Return a compact catalog label for logs and warnings."""

    prefix = obj.catalog_source.strip() or "catalog"
    ident = obj.catalog_id.strip() or obj.name.strip() or "(unnamed)"
    return f"{prefix}:{ident}"


def assess_gaia_target_source_match_shadow(
    target: CatalogObject,
    candidates: list[CatalogObject],
    match_radius_arcsec: float = TARGET_BLEND_SAME_SOURCE_MAX_SEPARATION_ARCSEC,
) -> GaiaTargetMatchShadowAssessment:
    """Associate one Gaia target source by position for blend assessment."""

    target_coord = sky_coord_for_object(target)
    if target_coord is None:
        return GaiaTargetMatchShadowAssessment(
            GAIA_TARGET_MATCH_SHADOW_INVALID_TARGET,
            None,
            None,
            (),
            (),
            len(candidates),
            "Gaia target match: INVALID_TARGET; target coordinates are invalid.",
        )

    ranked_sources: list[tuple[float, CatalogObject]] = []
    invalid_source_count = 0
    for candidate in candidates:
        candidate_coord = sky_coord_for_object(candidate)
        if candidate_coord is None:
            invalid_source_count += 1
            continue
        separation_arcsec = float(target_coord.separation(candidate_coord).arcsec)
        if not np.isfinite(separation_arcsec):
            invalid_source_count += 1
            continue
        ranked_sources.append((separation_arcsec, candidate))
    ranked_sources.sort(key=lambda item: item[0])

    matches = [
        (separation_arcsec, source)
        for separation_arcsec, source in ranked_sources
        if separation_arcsec <= match_radius_arcsec
    ]
    if not matches:
        nearest_text = (
            "n/a"
            if not ranked_sources
            else f"{ranked_sources[0][0]:.3f} arcsec"
        )
        return GaiaTargetMatchShadowAssessment(
            GAIA_TARGET_MATCH_SHADOW_NO_MATCH,
            None,
            None,
            (),
            tuple(source for _separation, source in ranked_sources),
            invalid_source_count,
            (
                "Gaia target match: NO_MATCH; "
                f"sources_within_{match_radius_arcsec:.1f}arcsec=0; "
                f"nearest={nearest_text}; neighbors={len(ranked_sources)}; "
                f"invalid_sources={invalid_source_count}."
            ),
        )

    target_separation_arcsec, target_source = matches[0]
    status = (
        GAIA_TARGET_MATCH_SHADOW_MATCHED
        if len(matches) == 1
        else GAIA_TARGET_MATCH_SHADOW_AMBIGUOUS
    )
    match_sources = tuple(source for _separation, source in matches)
    neighbor_sources = tuple(
        source
        for _separation, source in ranked_sources
        if source is not target_source
    )
    return GaiaTargetMatchShadowAssessment(
        status,
        target_source,
        target_separation_arcsec,
        match_sources,
        neighbor_sources,
        invalid_source_count,
        (
            f"Gaia target match: {status}; "
            f"target_source={target_source.gaia_source_id or target_source.catalog_id}; "
            f"target_separation={target_separation_arcsec:.3f} arcsec; "
            f"sources_within_{match_radius_arcsec:.1f}arcsec={len(matches)}; "
            f"neighbors={len(neighbor_sources)}; "
            f"invalid_sources={invalid_source_count}; position-only association."
        ),
    )


def assess_gaia_magnitude_system_shadow(
    target_match: GaiaTargetMatchShadowAssessment,
) -> GaiaMagnitudeSystemShadowAssessment:
    """Compare target and neighbor brightness only within the Gaia-G system."""

    target_source = target_match.target_source
    if target_source is None:
        return GaiaMagnitudeSystemShadowAssessment(
            GAIA_MAGNITUDE_SHADOW_TARGET_UNAVAILABLE,
            "Gaia DR3 G",
            None,
            None,
            (),
            0,
            len(target_match.neighbor_sources),
            (
                "Gaia magnitude assessment: TARGET_UNAVAILABLE; no Gaia target source "
                "was associated; VSX magnitude not used."
            ),
        )

    target_g_mag = target_source.gaia_magnitude_float("g")
    target_bp_rp = target_source.gaia_bp_rp_float()
    comparisons: list[GaiaNeighborMagnitudeShadowComparison] = []
    for neighbor in target_match.neighbor_sources:
        neighbor_g_mag = neighbor.gaia_magnitude_float("g")
        neighbor_bp_rp = neighbor.gaia_bp_rp_float()
        if target_g_mag is None:
            status = GAIA_NEIGHBOR_MAGNITUDE_SHADOW_TARGET_G_MISSING
            delta_g_mag = None
        elif neighbor_g_mag is None:
            status = GAIA_NEIGHBOR_MAGNITUDE_SHADOW_NEIGHBOR_G_MISSING
            delta_g_mag = None
        else:
            status = GAIA_NEIGHBOR_MAGNITUDE_SHADOW_COMPARABLE
            delta_g_mag = neighbor_g_mag - target_g_mag
        delta_bp_rp = (
            neighbor_bp_rp - target_bp_rp
            if target_bp_rp is not None and neighbor_bp_rp is not None
            else None
        )
        comparisons.append(
            GaiaNeighborMagnitudeShadowComparison(
                neighbor,
                status,
                target_g_mag,
                neighbor_g_mag,
                delta_g_mag,
                target_bp_rp,
                neighbor_bp_rp,
                delta_bp_rp,
            )
        )

    comparable_count = sum(
        1
        for comparison in comparisons
        if comparison.status == GAIA_NEIGHBOR_MAGNITUDE_SHADOW_COMPARABLE
    )
    missing_count = len(comparisons) - comparable_count
    if target_g_mag is None:
        status = GAIA_MAGNITUDE_SHADOW_TARGET_G_MISSING
    elif not comparisons:
        status = GAIA_MAGNITUDE_SHADOW_NO_NEIGHBORS
    elif comparable_count == 0:
        status = GAIA_MAGNITUDE_SHADOW_NO_COMPARABLE_NEIGHBORS
    elif missing_count:
        status = GAIA_MAGNITUDE_SHADOW_PARTIAL
    else:
        status = GAIA_MAGNITUDE_SHADOW_COMPARABLE

    color_deltas = [
        abs(comparison.delta_bp_rp)
        for comparison in comparisons
        if comparison.delta_bp_rp is not None
    ]
    max_color_delta_text = (
        "n/a" if not color_deltas else f"{max(color_deltas):.3f} mag"
    )
    return GaiaMagnitudeSystemShadowAssessment(
        status,
        "Gaia DR3 G",
        target_source,
        target_g_mag,
        tuple(comparisons),
        comparable_count,
        missing_count,
        (
            f"Gaia magnitude assessment: {status}; system=Gaia DR3 G; "
            f"target_G={format_optional_float(target_g_mag)}; "
            f"comparable_neighbors={comparable_count}; "
            f"missing_neighbors={missing_count}; "
            f"max_abs_delta_BP-RP={max_color_delta_text}; "
            "VSX magnitude not used; color is diagnostic only."
        ),
    )


def gaussian_psf_aperture_fraction(
    separation_px: float,
    aperture_radius_px: float,
    fwhm_px: float,
    integration_steps: int = GAIA_BLEND_PSF_INTEGRATION_STEPS,
) -> float:
    """Return the fraction of a circular Gaussian PSF inside an offset aperture."""

    if (
        not np.isfinite(separation_px)
        or not np.isfinite(aperture_radius_px)
        or not np.isfinite(fwhm_px)
        or separation_px < 0
        or aperture_radius_px <= 0
        or fwhm_px <= 0
        or integration_steps < 32
    ):
        return float("nan")
    sigma_px = fwhm_px / (2.0 * math.sqrt(2.0 * math.log(2.0)))
    if separation_px <= 1e-12:
        return float(
            1.0 - math.exp(-(aperture_radius_px**2) / (2.0 * sigma_px**2))
        )

    radial_min = max(0.0, separation_px - aperture_radius_px)
    radial_max = separation_px + aperture_radius_px
    radial_step = (radial_max - radial_min) / float(integration_steps)
    radii = radial_min + (np.arange(integration_steps, dtype=np.float64) + 0.5) * radial_step
    cosine_limit = (
        aperture_radius_px**2 - radii**2 - separation_px**2
    ) / (2.0 * radii * separation_px)
    angular_fraction = np.where(
        cosine_limit >= 1.0,
        1.0,
        np.where(
            cosine_limit <= -1.0,
            0.0,
            1.0 - np.arccos(np.clip(cosine_limit, -1.0, 1.0)) / math.pi,
        ),
    )
    radial_density = (
        radii / sigma_px**2 * np.exp(-(radii**2) / (2.0 * sigma_px**2))
    )
    fraction = float(np.sum(radial_density * angular_fraction) * radial_step)
    return min(1.0, max(0.0, fraction))


def format_practical_blend_magnitude(value: float | None) -> str:
    """Format modeled blend impact without implying unrealistic precision."""

    if value is None or not np.isfinite(value):
        return "n/a"
    if abs(value) < 0.001:
        return "<0.001 mag"
    return f"{value:.3f} mag"


def format_practical_blend_flux_ratio(value: float | None) -> str:
    """Format a modeled relative flux contribution as a practical percent."""

    if value is None or not np.isfinite(value):
        return "n/a"
    percent = 100.0 * value
    if abs(percent) < 0.001:
        return "<0.001%"
    return f"{percent:.3f}%"


def assess_gaia_blend_model_shadow(
    magnitude_assessment: GaiaMagnitudeSystemShadowAssessment,
    frame: ReferenceFrame,
    aperture_settings: ApertureSettings,
) -> GaiaBlendShadowAssessment:
    """Estimate summed Gaia-neighbor flux for the quantitative policy."""

    psf_model = "circular Gaussian; sigma=FWHM/(2*sqrt(2*ln(2)))"
    fwhm_px = aperture_settings.fwhm_px
    target_source = magnitude_assessment.target_source
    if (
        target_source is None
        or fwhm_px is None
        or not np.isfinite(fwhm_px)
        or fwhm_px <= 0
    ):
        return GaiaBlendShadowAssessment(
            GAIA_BLEND_SHADOW_MODEL_UNAVAILABLE,
            psf_model,
            fwhm_px,
            aperture_settings.aperture_radius_px,
            None,
            (),
            0,
            len(magnitude_assessment.comparisons),
            None,
            None,
            None,
            (
                "Gaia blend model: MODEL_UNAVAILABLE; Gaia target source or "
                "reference FWHM unavailable."
            ),
        )

    target_x, target_y, target_problem = object_pixel_position_in_frame(
        target_source,
        frame,
    )
    target_aperture_fraction = gaussian_psf_aperture_fraction(
        0.0,
        aperture_settings.aperture_radius_px,
        fwhm_px,
    )
    if (
        target_problem is not None
        or not np.isfinite(target_x)
        or not np.isfinite(target_y)
        or not np.isfinite(target_aperture_fraction)
        or target_aperture_fraction <= 0
    ):
        return GaiaBlendShadowAssessment(
            GAIA_BLEND_SHADOW_MODEL_UNAVAILABLE,
            psf_model,
            fwhm_px,
            aperture_settings.aperture_radius_px,
            None,
            (),
            0,
            len(magnitude_assessment.comparisons),
            None,
            None,
            None,
            (
                "Gaia blend model: MODEL_UNAVAILABLE; target pixel geometry "
                f"unavailable ({target_problem or 'invalid aperture fraction'})."
            ),
        )

    contributions: list[GaiaBlendShadowContribution] = []
    for comparison in magnitude_assessment.comparisons:
        source = comparison.source
        duplicated_source = source.gaia_duplicated_source_bool()
        ipd_frac_multi_peak = source.gaia_ipd_fraction_float("multi_peak")
        ipd_frac_odd_win = source.gaia_ipd_fraction_float("odd_win")
        neighbor_x, neighbor_y, neighbor_problem = object_pixel_position_in_frame(
            source,
            frame,
        )
        separation_px = (
            float(math.hypot(neighbor_x - target_x, neighbor_y - target_y))
            if neighbor_problem is None
            and np.isfinite(neighbor_x)
            and np.isfinite(neighbor_y)
            else None
        )
        if comparison.delta_g_mag is None:
            contributions.append(
                GaiaBlendShadowContribution(
                    source,
                    GAIA_BLEND_CONTRIBUTION_MAGNITUDE_UNAVAILABLE,
                    separation_px,
                    None,
                    None,
                    None,
                    target_aperture_fraction,
                    None,
                    duplicated_source,
                    ipd_frac_multi_peak,
                    ipd_frac_odd_win,
                )
            )
            continue
        if separation_px is None:
            contributions.append(
                GaiaBlendShadowContribution(
                    source,
                    GAIA_BLEND_CONTRIBUTION_GEOMETRY_UNAVAILABLE,
                    None,
                    comparison.delta_g_mag,
                    None,
                    None,
                    target_aperture_fraction,
                    None,
                    duplicated_source,
                    ipd_frac_multi_peak,
                    ipd_frac_odd_win,
                )
            )
            continue

        total_flux_ratio = float(10.0 ** (-0.4 * comparison.delta_g_mag))
        neighbor_aperture_fraction = gaussian_psf_aperture_fraction(
            separation_px,
            aperture_settings.aperture_radius_px,
            fwhm_px,
        )
        aperture_flux_ratio = (
            total_flux_ratio
            * neighbor_aperture_fraction
            / target_aperture_fraction
        )
        contributions.append(
            GaiaBlendShadowContribution(
                source,
                GAIA_BLEND_CONTRIBUTION_MODELED,
                separation_px,
                comparison.delta_g_mag,
                total_flux_ratio,
                neighbor_aperture_fraction,
                target_aperture_fraction,
                aperture_flux_ratio,
                duplicated_source,
                ipd_frac_multi_peak,
                ipd_frac_odd_win,
            )
        )

    modeled = [
        contribution
        for contribution in contributions
        if contribution.status == GAIA_BLEND_CONTRIBUTION_MODELED
        and contribution.aperture_flux_ratio is not None
    ]
    modeled_count = len(modeled)
    unavailable_count = len(contributions) - modeled_count
    total_aperture_flux_ratio = (
        float(sum(contribution.aperture_flux_ratio or 0.0 for contribution in modeled))
        if modeled
        else None
    )
    magnitude_bias_mag = (
        float(-2.5 * math.log10(1.0 + total_aperture_flux_ratio))
        if total_aperture_flux_ratio is not None
        else None
    )
    magnitude_impact_mag = (
        abs(magnitude_bias_mag) if magnitude_bias_mag is not None else None
    )
    if not modeled:
        status = GAIA_BLEND_SHADOW_NO_COMPARABLE_NEIGHBORS
    elif unavailable_count:
        status = GAIA_BLEND_SHADOW_PARTIAL
    else:
        status = GAIA_BLEND_SHADOW_MODELED

    strongest = sorted(
        modeled,
        key=lambda contribution: contribution.aperture_flux_ratio or 0.0,
        reverse=True,
    )[:3]
    strongest_text = ", ".join(
        f"{item.source.gaia_source_id or item.source.catalog_id}:"
        f"{format_practical_blend_magnitude(2.5 * math.log10(1.0 + item.aperture_flux_ratio))}"
        for item in strongest
        if item.aperture_flux_ratio is not None
    ) or "none"
    duplicated_count = sum(item.duplicated_source is True for item in contributions)
    multi_peak_values = [
        item.ipd_frac_multi_peak
        for item in contributions
        if item.ipd_frac_multi_peak is not None
    ]
    odd_win_values = [
        item.ipd_frac_odd_win
        for item in contributions
        if item.ipd_frac_odd_win is not None
    ]
    return GaiaBlendShadowAssessment(
        status,
        psf_model,
        fwhm_px,
        aperture_settings.aperture_radius_px,
        target_aperture_fraction,
        tuple(contributions),
        modeled_count,
        unavailable_count,
        total_aperture_flux_ratio,
        magnitude_bias_mag,
        magnitude_impact_mag,
        (
            f"Gaia blend model: {status}; model={psf_model}; "
            f"modeled_neighbors={modeled_count}; unavailable={unavailable_count}; "
            "summed_aperture_flux="
            f"{format_practical_blend_flux_ratio(total_aperture_flux_ratio)}; "
            f"magnitude_impact={format_practical_blend_magnitude(magnitude_impact_mag)}; "
            f"strongest={strongest_text}; duplicated_sources={duplicated_count}; "
            f"max_IPDfmp={format_optional_float(max(multi_peak_values) if multi_peak_values else None)}; "
            f"max_IPDfow={format_optional_float(max(odd_win_values) if odd_win_values else None)}; "
            "Gaia quality fields are diagnostic only; no policy effect."
        ),
    )


def target_blend_policy_assessment(
    target_match: GaiaTargetMatchShadowAssessment,
    magnitude_assessment: GaiaMagnitudeSystemShadowAssessment,
    blend_assessment: GaiaBlendShadowAssessment,
    pixel_scale_arcsec: float,
    target_catalog_mag: float | None = None,
    warning_limit_mag: float | None = None,
    invalid_limit_mag: float = TARGET_BLEND_ERROR_LIMIT_MAG,
) -> TargetBlendAssessment:
    """Convert the summed Gaia model into the selected quality policy."""

    strongest = max(
        (
            contribution
            for contribution in blend_assessment.contributions
            if contribution.aperture_flux_ratio is not None
        ),
        key=lambda contribution: contribution.aperture_flux_ratio or 0.0,
        default=None,
    )
    neighbor = None if strongest is None else strongest.source
    separation_px = None if strongest is None else strongest.separation_px
    separation_arcsec = (
        separation_px * pixel_scale_arcsec
        if separation_px is not None and np.isfinite(pixel_scale_arcsec)
        else None
    )
    neighbor_mag = None if neighbor is None else neighbor.gaia_magnitude_float("g")
    target_mag = magnitude_assessment.target_g_mag

    if target_match.status == GAIA_TARGET_MATCH_SHADOW_NO_MATCH:
        return TargetBlendAssessment(
            QUALITY_STATUS_OK,
            "OK",
            False,
            (
                "Target blend assessment: INFO; no Gaia DR3 target source was "
                "positionally associated. The Gaia blend check is skipped; "
                "measurement remains usable."
            ),
            evidence_complete=False,
        )

    if (
        target_catalog_mag is not None
        and np.isfinite(target_catalog_mag)
        and target_mag is not None
        and np.isfinite(target_mag)
        and abs(float(target_catalog_mag) - float(target_mag))
        >= TARGET_BLEND_HISTORICAL_MAGNITUDE_MISMATCH_MAG
    ):
        return TargetBlendAssessment(
            QUALITY_STATUS_OK,
            "OK",
            False,
            (
                "Target blend assessment: INFO; the historical Gaia brightness "
                "differs strongly from the configured current target brightness. "
                "The Gaia blend check is skipped for this potentially strongly "
                "variable object; measurement remains usable."
            ),
            neighbor,
            separation_px,
            separation_arcsec,
            neighbor_mag,
            target_mag,
            evidence_complete=False,
        )

    no_neighbors = (
        target_match.status == GAIA_TARGET_MATCH_SHADOW_MATCHED
        and magnitude_assessment.status == GAIA_MAGNITUDE_SHADOW_NO_NEIGHBORS
        and not magnitude_assessment.comparisons
    )
    if no_neighbors:
        return TargetBlendAssessment(
            QUALITY_STATUS_OK,
            "OK",
            False,
            (
                "Target blend assessment: OK; Gaia target source is uniquely "
                "matched and no Gaia neighbors are present; summed impact=<0.001 mag."
            ),
            summed_flux_ratio=0.0,
            magnitude_impact_mag=0.0,
            evidence_complete=True,
        )

    quantitative_model_complete = (
        magnitude_assessment.status == GAIA_MAGNITUDE_SHADOW_COMPARABLE
        and blend_assessment.status == GAIA_BLEND_SHADOW_MODELED
        and blend_assessment.unavailable_count == 0
        and blend_assessment.total_aperture_flux_ratio is not None
        and blend_assessment.magnitude_impact_mag is not None
    )
    evidence_complete = (
        target_match.status == GAIA_TARGET_MATCH_SHADOW_MATCHED
        and quantitative_model_complete
    )
    if quantitative_model_complete:
        impact = float(blend_assessment.magnitude_impact_mag)
        summed_flux_ratio = float(blend_assessment.total_aperture_flux_ratio)
        if impact >= invalid_limit_mag:
            ambiguity_note = (
                " The Gaia target association is ambiguous, but every additional "
                "matched source is retained as a modeled neighbor; ambiguity must "
                "not suppress a conservative contamination rejection."
                if target_match.status == GAIA_TARGET_MATCH_SHADOW_AMBIGUOUS
                else ""
            )
            return TargetBlendAssessment(
                QUALITY_STATUS_INVALID,
                "TARGET_BLEND_MODELED_CONTAMINATION",
                False,
                (
                    "Target blend assessment: INVALID after measurement; summed "
                    "Gaia-neighbor impact="
                    f"{format_practical_blend_magnitude(impact)} reaches the unchanged "
                    f"error limit {invalid_limit_mag:.2f} mag "
                    "(summed aperture flux="
                    f"{format_practical_blend_flux_ratio(summed_flux_ratio)})."
                    f"{ambiguity_note}"
                ),
                neighbor,
                separation_px,
                separation_arcsec,
                neighbor_mag,
                target_mag,
                summed_flux_ratio,
                impact,
                evidence_complete,
            )
    if not evidence_complete:
        return TargetBlendAssessment(
            QUALITY_STATUS_WARNING,
            "TARGET_BLEND_EVIDENCE_INCOMPLETE",
            False,
            (
                "Target blend assessment: WARNING; quantitative Gaia evidence "
                f"is incomplete (match={target_match.status}, "
                f"magnitudes={magnitude_assessment.status}, "
                f"model={blend_assessment.status}); measurement continues and "
                "no hard contamination decision is made."
            ),
            neighbor,
            separation_px,
            separation_arcsec,
            neighbor_mag,
            target_mag,
            blend_assessment.total_aperture_flux_ratio,
            blend_assessment.magnitude_impact_mag,
            False,
        )

    impact = float(blend_assessment.magnitude_impact_mag)
    summed_flux_ratio = float(blend_assessment.total_aperture_flux_ratio)
    if warning_limit_mag is not None and impact >= warning_limit_mag:
        return TargetBlendAssessment(
            QUALITY_STATUS_WARNING,
            "TARGET_BLEND_MODELED_CONTAMINATION_WARNING",
            False,
            (
                "Target blend assessment: WARNING after measurement; summed "
                "Gaia-neighbor impact="
                f"{format_practical_blend_magnitude(impact)} reaches the "
                f"warning limit {warning_limit_mag:.2f} mag but remains below "
                f"the error limit {invalid_limit_mag:.2f} mag "
                "(summed aperture flux="
                f"{format_practical_blend_flux_ratio(summed_flux_ratio)})."
            ),
            neighbor,
            separation_px,
            separation_arcsec,
            neighbor_mag,
            target_mag,
            summed_flux_ratio,
            impact,
            True,
        )

    return TargetBlendAssessment(
        QUALITY_STATUS_OK,
        "OK",
        False,
        (
            "Target blend assessment: OK; summed Gaia-neighbor "
            f"impact={format_practical_blend_magnitude(impact)} is below the error limit "
            f"{invalid_limit_mag:.2f} mag "
            "(summed aperture flux="
            f"{format_practical_blend_flux_ratio(summed_flux_ratio)})."
        ),
        neighbor,
        separation_px,
        separation_arcsec,
        neighbor_mag,
        target_mag,
        summed_flux_ratio,
        impact,
        True,
    )


def assess_target_catalog_blend(
    target: CatalogObject,
    frame: ReferenceFrame,
    aperture_settings: ApertureSettings,
    progress: Callable[[str], None] | None = None,
    warning_limit_mag: float | None = None,
    invalid_limit_mag: float = TARGET_BLEND_ERROR_LIMIT_MAG,
) -> TargetBlendAssessment:
    """Assess the summed Gaia-neighbor impact without skipping measurement."""

    target_coord = sky_coord_for_object(target)
    if target_coord is None:
        return TargetBlendAssessment(
            QUALITY_STATUS_WARNING,
            "TARGET_BLEND_EVIDENCE_INCOMPLETE",
            False,
            "Target blend assessment: WARNING; target coordinates are invalid; measurement continues.",
        )

    pixel_scale = reference_frame_pixel_scale_arcsec(frame)
    search_radius_arcsec = max(
        30.0,
        aperture_settings.annulus_outer_px * pixel_scale * 1.2,
    )
    try:
        candidates = query_target_blend_catalog(
            float(target_coord.ra.deg),
            float(target_coord.dec.deg),
            search_radius_arcsec,
            target.magnitude_float(),
            progress,
        )
    except Exception as exc:
        return TargetBlendAssessment(
            QUALITY_STATUS_WARNING,
            "TARGET_BLEND_EVIDENCE_INCOMPLETE",
            False,
            (
                "Target blend assessment: WARNING; Gaia query failed "
                f"({exc}); measurement continues."
            ),
        )
    target_match = assess_gaia_target_source_match_shadow(target, candidates)
    magnitude_assessment = assess_gaia_magnitude_system_shadow(target_match)
    blend_assessment = assess_gaia_blend_model_shadow(
        magnitude_assessment,
        frame,
        aperture_settings,
    )
    if progress is not None:
        progress(target_match.message)
        progress(magnitude_assessment.message)
        progress(blend_assessment.message)
    return target_blend_policy_assessment(
        target_match,
        magnitude_assessment,
        blend_assessment,
        pixel_scale,
        target.magnitude_float(),
        warning_limit_mag,
        invalid_limit_mag,
    )


def filter_objects_inside_reference_frame(
    objects: list[CatalogObject],
    frame: ReferenceFrame,
    margin_px: float = 0.0,
) -> tuple[list[CatalogObject], int]:
    """Keep only catalog objects inside the reference image with a usable edge margin."""

    visible: list[CatalogObject] = []
    rejected = 0
    for obj in objects:
        _, _, problem = object_pixel_position_in_frame(obj, frame, margin_px)
        if problem is None:
            visible.append(obj)
        else:
            rejected += 1
    return visible, rejected


def read_sequence_wcs_frames(directory: Path, sequence_name: str) -> list[SequenceWcsFrame]:
    """Read WCS transforms for solved frames in a sequence."""

    frames: list[SequenceWcsFrame] = []
    for path in solved_sequence_fits_files(directory, sequence_name):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            try:
                with fits.open(path) as hdul:
                    header = hdul[0].header
                    if not all(key in header for key in ("CTYPE1", "CTYPE2", "NAXIS1", "NAXIS2")):
                        continue
                    frames.append(
                        SequenceWcsFrame(
                            path=path,
                            width=int(header["NAXIS1"]),
                            height=int(header["NAXIS2"]),
                            wcs=WCS(header),
                        )
                    )
            except Exception:
                continue
    return frames


def object_visible_in_all_frames(
    obj: CatalogObject,
    frames: list[SequenceWcsFrame],
    margin_px: float = 8.0,
) -> bool:
    """Return True if an object stays inside every solved frame."""

    coord = sky_coord_for_object(obj)
    if coord is None:
        return False
    for frame in frames:
        if not object_visible_in_frame(coord, frame, margin_px):
            return False
    return True


def object_visible_count_in_frames(
    obj: CatalogObject,
    frames: list[SequenceWcsFrame],
    margin_px: float = 8.0,
    minimum_fraction: float | None = None,
) -> int:
    """Return how many solved frames contain the object with the required margin."""

    coord = sky_coord_for_object(obj)
    if coord is None:
        return 0
    visible_count = 0
    minimum_visible_count = 0
    if minimum_fraction is not None:
        minimum_visible_count = int(math.ceil(len(frames) * minimum_fraction))
    for index, frame in enumerate(frames):
        if object_visible_in_frame(coord, frame, margin_px):
            visible_count += 1
        remaining = len(frames) - index - 1
        if minimum_visible_count and visible_count + remaining < minimum_visible_count:
            break
    return visible_count


def object_visible_in_frame(
    coord: SkyCoord,
    frame: SequenceWcsFrame,
    margin_px: float,
) -> bool:
    """Return True if a coordinate projects inside one solved frame."""

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "error",
            message=".*WCS\\.all_world2pix.*failed to converge.*",
            category=UserWarning,
        )
        try:
            x, y = frame.wcs.world_to_pixel(coord)
        except Exception:
            return False
    x = float(x)
    y = float(y)
    return bool(
        margin_px <= x < frame.width - margin_px
        and margin_px <= y < frame.height - margin_px
    )


def evenly_sample_paths(paths: list[Path], max_count: int) -> list[Path]:
    """Return up to max_count paths spread over the full sequence."""

    if max_count <= 0 or len(paths) <= max_count:
        return paths
    indices = np.linspace(0, len(paths) - 1, max_count)
    selected_indices = sorted({int(round(index)) for index in indices})
    return [paths[index] for index in selected_indices]


def finite_median(values: list[float]) -> float:
    """Return finite median or NaN."""

    array = np.array([value for value in values if np.isfinite(value)], dtype=np.float64)
    if len(array) == 0:
        return float("nan")
    return float(np.median(array))


def format_counter(counter: Counter[str]) -> str:
    """Return a compact counter summary for logs."""

    if not counter:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in counter.most_common())


def catalog_source_summary(sources: Iterable[str]) -> str:
    """Return stable semicolon-separated catalog source names."""

    unique_sources = sorted({str(source).strip() for source in sources if str(source).strip()})
    return ";".join(unique_sources) if unique_sources else "CATALOG"


def candidate_catalog_spec(candidate: CatalogObject) -> dict[str, object]:
    """Return an aperture-measurement object spec for one catalog candidate."""

    return {
        "object_id": candidate.catalog_id or candidate.name,
        "role": "comparison",
        "ra_deg": float(candidate.ra),
        "dec_deg": float(candidate.dec),
        "catalog_mag": candidate.magnitude_float(),
        "catalog_source": candidate.catalog_source or APASS_DR10_SOURCE_NAME,
        "catalog_id": candidate.catalog_id,
        "catalog_mag_error": candidate.magnitude_error_float(),
        "catalog_nobs": candidate.nobs_v_int(),
        "catalog_mag_b": candidate.band_float("mag_b"),
        "catalog_mag_g": candidate.band_float("mag_g"),
        "catalog_mag_r": candidate.band_float("mag_r"),
        "catalog_b_minus_v": candidate.color_index_float("mag_b", "mag_v"),
        "catalog_g_minus_r": candidate.color_index_float("mag_g", "mag_r"),
    }


def field_zp_catalog_spec(candidate: CatalogObject) -> dict[str, object]:
    """Return an aperture-measurement spec for a field-ZP reference."""

    spec = candidate_catalog_spec(candidate)
    spec["role"] = "field_zp_reference"
    return spec


def select_single_field_zp_candidates(
    objects: list[CatalogObject],
    reference_frame: ReferenceFrame,
    aperture_settings: ApertureSettings,
    progress: Callable[[str], None] | None = None,
) -> list[CatalogObject]:
    """Select isolated field references before measuring them."""

    margin = max(SINGLE_FIELD_ZP_EDGE_MARGIN_PX, aperture_settings.annulus_outer_px + 30.0)
    rejected: Counter[str] = Counter()
    accepted: list[tuple[CatalogObject, float, float, float | None]] = []
    for obj in objects:
        mag = obj.magnitude_float()
        if mag is None:
            rejected["missing_v"] += 1
            continue
        mag_error = obj.magnitude_error_float()
        if (
            mag_error is None
            or mag_error < SINGLE_FIELD_ZP_MIN_CATALOG_ERR_MAG
            or mag_error > SINGLE_FIELD_ZP_MAX_CATALOG_ERR_MAG
        ):
            rejected["catalog_error"] += 1
            continue
        x, y, problem = object_pixel_position_in_frame(obj, reference_frame, margin)
        if problem is not None:
            rejected["edge_or_wcs"] += 1
            continue
        accepted.append((obj, x, y, mag))

    neighbor_radius = max(
        aperture_settings.annulus_outer_px * SINGLE_FIELD_ZP_NEIGHBOR_RADIUS_FACTOR,
        aperture_settings.aperture_radius_px + 4.0,
    )
    isolated: list[CatalogObject] = []
    for index, (obj, x, y, mag) in enumerate(accepted):
        has_neighbor = False
        for other_index, (_other, other_x, other_y, other_mag) in enumerate(accepted):
            if other_index == index:
                continue
            if math.hypot(other_x - x, other_y - y) > neighbor_radius:
                continue
            if other_mag is None or mag is None or other_mag - mag <= SINGLE_FIELD_ZP_NEIGHBOR_MAX_MAG_DELTA:
                has_neighbor = True
                break
        if has_neighbor:
            rejected["neighbor"] += 1
            continue
        isolated.append(obj)

    isolated.sort(
        key=lambda obj: (
            obj.magnitude_error_float() if obj.magnitude_error_float() is not None else 9.0,
            obj.magnitude_float() if obj.magnitude_float() is not None else 99.0,
            obj.catalog_id or obj.name,
        )
    )
    selected = isolated[:SINGLE_FIELD_ZP_MAX_REFERENCES]
    if progress is not None:
        progress(
            "Single field-ZP reference prefilter: "
            f"{len(selected)}/{len(objects)} selected "
            f"({len(isolated)} suitable, cap {SINGLE_FIELD_ZP_MAX_REFERENCES}; "
            f"rejected {format_counter(rejected)})."
        )
    return selected


def select_single_field_zp_candidates_with_ucac4_fallback(
    objects: list[CatalogObject],
    reference_frame: ReferenceFrame,
    aperture_settings: ApertureSettings,
    progress: Callable[[str], None] | None = None,
    ucac4_query: Callable[
        [ReferenceFrame, float | None, float, Callable[[str], None] | None],
        list[CatalogObject],
    ] | None = None,
) -> list[CatalogObject]:
    """Select Field-ZP references, trying UCAC4 after a weak APASS prefilter.

    A full APASS cone result can still leave too few sources inside the usable
    image area.  In Auto mode, evaluate UCAC4 with the identical field and
    quality filters and retain the stronger of the two independent pools.
    """

    candidates = select_single_field_zp_candidates(
        objects,
        reference_frame,
        aperture_settings,
        progress,
    )
    minimum_independent_references = (
        SINGLE_FIELD_ZP_MIN_USED_REFERENCES + SINGLE_FIELD_ZP_CHECK_MIN_EXTRA_REFERENCES
    )
    if (
        len(candidates) >= minimum_independent_references
        or not should_try_post_vetting_ucac4(None, objects, 0)
    ):
        return candidates

    if progress is not None:
        progress(
            "APASS DR10 Field-ZP prefilter left only "
            f"{len(candidates)} suitable reference(s); trying UCAC4."
        )
    query = ucac4_query or query_ucac4_region
    try:
        ucac4_objects = query(reference_frame, None, 99.0, progress)
    except Exception as exc:
        if progress is not None:
            progress(f"WARNING: UCAC4 Field-ZP fallback failed: {exc}")
        return candidates

    ucac4_progress = (
        (lambda message: progress(f"UCAC4 {message}"))
        if progress is not None
        else None
    )
    ucac4_candidates = select_single_field_zp_candidates(
        ucac4_objects,
        reference_frame,
        aperture_settings,
        ucac4_progress,
    )
    if len(ucac4_candidates) > len(candidates):
        if progress is not None:
            progress(
                "Using UCAC4 Field-ZP references after prefilter: "
                f"{len(ucac4_candidates)} vs APASS DR10 {len(candidates)}."
            )
        return ucac4_candidates
    if progress is not None:
        progress(
            "Keeping APASS DR10 Field-ZP references after UCAC4 prefilter: "
            f"{len(candidates)} vs UCAC4 {len(ucac4_candidates)}."
        )
    return candidates


def single_field_zp_reference_quality(
    measurement: ApertureMeasurement,
    min_snr: float = SINGLE_FIELD_ZP_MIN_SNR,
) -> tuple[bool, str]:
    """Return whether one measured field reference can be used for the ZP fit."""

    reasons: list[str] = []
    if not measurement.valid:
        reasons.append(measurement.quality_flag)
    if measurement.inst_mag is None:
        reasons.append("NO_INST_MAG")
    if not np.isfinite(measurement.snr) or measurement.snr < min_snr:
        reasons.append("LOW_SNR")
    if (
        measurement.inst_mag_error is None
        or not np.isfinite(measurement.inst_mag_error)
        or measurement.inst_mag_error > SINGLE_FIELD_ZP_MAX_INST_MAG_ERROR
    ):
        reasons.append("HIGH_INST_MAG_ERROR")
    if (
        measurement.catalog_mag_error is None
        or measurement.catalog_mag_error < SINGLE_FIELD_ZP_MIN_CATALOG_ERR_MAG
        or measurement.catalog_mag_error > SINGLE_FIELD_ZP_MAX_CATALOG_ERR_MAG
    ):
        reasons.append("HIGH_CATALOG_ERROR")
    if measurement.catalog_b_minus_v is not None and abs(measurement.catalog_b_minus_v) > SINGLE_FIELD_ZP_MAX_B_MINUS_V:
        reasons.append("EXTREME_BV")
    if measurement.catalog_g_minus_r is not None and abs(measurement.catalog_g_minus_r) > SINGLE_FIELD_ZP_MAX_G_MINUS_R:
        reasons.append("EXTREME_GR")
    return not reasons, "|".join(dict.fromkeys(reasons))


def single_field_zp_references_from_measurements(
    measurements: list[ApertureMeasurement],
    min_snr: float = SINGLE_FIELD_ZP_MIN_SNR,
) -> list[SingleFieldZpReference]:
    """Build field-ZP reference rows from raw aperture measurements."""

    references: list[SingleFieldZpReference] = []
    for measurement in measurements:
        used_for_fit, reject_reason = single_field_zp_reference_quality(measurement, min_snr)
        references.append(SingleFieldZpReference(measurement, used_for_fit, reject_reason))
    return references


def single_field_zp_check_score(
    reference: SingleFieldZpReference,
    provisional_residual: float | None = None,
) -> float:
    """Return a quality score for reserving one field reference as check star."""

    measurement = reference.measurement
    catalog_error = (
        measurement.catalog_mag_error
        if measurement.catalog_mag_error is not None and np.isfinite(measurement.catalog_mag_error)
        else SINGLE_FIELD_ZP_MAX_CATALOG_ERR_MAG
    )
    inst_error = (
        measurement.inst_mag_error
        if measurement.inst_mag_error is not None and np.isfinite(measurement.inst_mag_error)
        else SINGLE_FIELD_ZP_MAX_INST_MAG_ERROR
    )
    snr_penalty = 1.0 / max(float(measurement.snr), 1.0) if np.isfinite(measurement.snr) else 1.0
    bv_penalty = (
        abs(measurement.catalog_b_minus_v - SINGLE_FIELD_ZP_CHECK_PREFERRED_B_MINUS_V)
        if measurement.catalog_b_minus_v is not None
        else 0.5
    )
    gr_penalty = (
        abs(measurement.catalog_g_minus_r - SINGLE_FIELD_ZP_CHECK_PREFERRED_G_MINUS_R)
        if measurement.catalog_g_minus_r is not None
        else 0.5
    )
    mag = measurement.catalog_mag
    bright_penalty = (
        max(0.0, SINGLE_FIELD_ZP_CHECK_MIN_MAG - mag) * 2.0
        if mag is not None
        else 1.0
    )
    faint_penalty = (
        max(0.0, mag - SINGLE_FIELD_ZP_CHECK_MAX_MAG) * 0.8
        if mag is not None
        else 1.0
    )
    residual_penalty = (
        abs(provisional_residual) * 4.0
        if provisional_residual is not None and np.isfinite(provisional_residual)
        else 0.0
    )
    catalog_error_penalty = max(
        0.0,
        catalog_error - SINGLE_FIELD_ZP_CHECK_PREFERRED_MAX_CATALOG_ERR_MAG,
    ) * 5.0
    nobs_penalty = 0.15 if measurement.catalog_nobs is not None and measurement.catalog_nobs <= 1 else 0.0
    return (
        catalog_error * 8.0
        + inst_error * 6.0
        + snr_penalty * 2.0
        + bv_penalty * 0.15
        + gr_penalty * 0.10
        + bright_penalty
        + faint_penalty
        + residual_penalty
        + catalog_error_penalty
        + nobs_penalty
    )


def reserve_single_field_zp_check_reference(
    references: list[SingleFieldZpReference],
) -> tuple[list[SingleFieldZpReference], SingleFieldZpReference | None]:
    """Reserve one good field reference as independent check star before fitting ZP."""

    candidates = [
        reference
        for reference in references
        if reference.used_for_fit and reference.zero_point() is not None
    ]
    if len(candidates) < SINGLE_FIELD_ZP_MIN_USED_REFERENCES + SINGLE_FIELD_ZP_CHECK_MIN_EXTRA_REFERENCES:
        return references, None

    provisional_fit = fit_single_field_zero_point(references)
    residual_by_id: dict[str, float] = {}
    residual_limit: float | None = None
    if provisional_fit.success and provisional_fit.zero_point is not None:
        for reference in candidates:
            zero_point = reference.zero_point()
            if zero_point is not None and np.isfinite(zero_point):
                residual_by_id[reference.measurement.object_id] = zero_point - provisional_fit.zero_point
        used_abs_residuals = [
            abs(residual_by_id[reference.measurement.object_id])
            for reference in candidates
            if reference.measurement.object_id in provisional_fit.used_object_ids
            and reference.measurement.object_id in residual_by_id
        ]
        if used_abs_residuals:
            residual_limit = max(
                SINGLE_FIELD_ZP_CHECK_MIN_RESIDUAL_LIMIT,
                float(np.median(np.array(used_abs_residuals, dtype=np.float64))),
            )

    strict_candidates = []
    for reference in candidates:
        measurement = reference.measurement
        if measurement.catalog_mag is None:
            continue
        if not (SINGLE_FIELD_ZP_CHECK_MIN_MAG <= measurement.catalog_mag <= SINGLE_FIELD_ZP_CHECK_MAX_MAG):
            continue
        if not np.isfinite(measurement.snr) or measurement.snr < SINGLE_FIELD_ZP_CHECK_MIN_SNR:
            continue
        if (
            measurement.catalog_mag_error is None
            or measurement.catalog_mag_error > SINGLE_FIELD_ZP_CHECK_PREFERRED_MAX_CATALOG_ERR_MAG
        ):
            continue
        residual = residual_by_id.get(measurement.object_id)
        if residual_limit is not None and residual is not None and abs(residual) > residual_limit:
            continue
        strict_candidates.append(reference)

    pool = strict_candidates or candidates
    check = min(
        pool,
        key=lambda reference: single_field_zp_check_score(
            reference,
            residual_by_id.get(reference.measurement.object_id),
        ),
    )
    updated: list[SingleFieldZpReference] = []
    for reference in references:
        if reference.measurement.object_id == check.measurement.object_id:
            updated.append(
                SingleFieldZpReference(
                    reference.measurement,
                    False,
                    "CHECK_RESERVED",
                )
            )
        else:
            updated.append(reference)
    return updated, check


def fit_single_field_zero_point(
    references: list[SingleFieldZpReference],
) -> SingleFieldZpFit:
    """Fit a robust field zero point for single-image calibration."""

    usable: list[tuple[SingleFieldZpReference, float]] = []
    for reference in references:
        if not reference.used_for_fit:
            continue
        zero_point = reference.zero_point()
        if zero_point is not None and np.isfinite(zero_point):
            usable.append((reference, float(zero_point)))

    measured_count = len(references)
    usable_count = len(usable)
    if usable_count < SINGLE_FIELD_ZP_MIN_USED_REFERENCES:
        return SingleFieldZpFit(
            False,
            None,
            None,
            None,
            measured_count,
            usable_count,
            0,
            0,
            0,
            "TOO_FEW_REFERENCES",
            (),
        )

    current = usable
    iterations = 0
    for iterations in range(1, SINGLE_FIELD_ZP_MAX_ITERATIONS + 1):
        values = [zero_point for _reference, zero_point in current]
        center = finite_median(values)
        scatter = robust_scatter(values)
        if scatter is None or not np.isfinite(scatter) or scatter <= 0.0:
            break
        limit = SINGLE_FIELD_ZP_SIGMA_CLIP * scatter
        clipped = [item for item in current if abs(item[1] - center) <= limit]
        if len(clipped) == len(current):
            break
        if len(clipped) < SINGLE_FIELD_ZP_MIN_USED_REFERENCES:
            break
        current = clipped

    values = [zero_point for _reference, zero_point in current]
    zero_point = finite_median(values)
    scatter = robust_scatter(values)
    if scatter is None or not np.isfinite(scatter):
        scatter = float(np.std(np.array(values, dtype=np.float64))) if values else None
    zero_point_error = (
        float(scatter) / math.sqrt(len(values))
        if scatter is not None and np.isfinite(scatter) and values
        else None
    )
    success = bool(len(values) >= SINGLE_FIELD_ZP_MIN_USED_REFERENCES and np.isfinite(zero_point))
    return SingleFieldZpFit(
        success,
        float(zero_point) if np.isfinite(zero_point) else None,
        float(scatter) if scatter is not None and np.isfinite(scatter) else None,
        zero_point_error,
        measured_count,
        usable_count,
        len(values),
        usable_count - len(values),
        iterations,
        "OK" if success else "TOO_FEW_REFERENCES_AFTER_CLIP",
        tuple(reference.measurement.object_id for reference, _zero_point in current),
    )


def color_range_penalty(
    value: float | None,
    preferred_min: float,
    preferred_max: float,
    soft_min: float,
    soft_max: float,
) -> tuple[float, bool]:
    """Return a color penalty and whether the value is outside the soft range."""

    if value is None or not np.isfinite(value):
        return 0.0, False
    if soft_min <= value <= soft_max:
        if preferred_min <= value <= preferred_max:
            return 0.0, False
        return min(abs(value - preferred_min), abs(value - preferred_max)), False
    if value < soft_min:
        return (soft_min - value) + (preferred_min - soft_min), True
    return (value - soft_max) + (soft_max - preferred_max), True


def catalog_color_selection_penalty(candidate: CatalogObject) -> tuple[float, bool]:
    """Return combined B-V/g-r color penalty for automatic star selection."""

    b_minus_v = candidate.color_index_float("mag_b", "mag_v")
    g_minus_r = candidate.color_index_float("mag_g", "mag_r")
    bv_penalty, bv_extreme = color_range_penalty(
        b_minus_v,
        COMP_COLOR_BV_PREFERRED_MIN,
        COMP_COLOR_BV_PREFERRED_MAX,
        COMP_COLOR_BV_SOFT_MIN,
        COMP_COLOR_BV_SOFT_MAX,
    )
    gr_penalty, gr_extreme = color_range_penalty(
        g_minus_r,
        COMP_COLOR_GR_PREFERRED_MIN,
        COMP_COLOR_GR_PREFERRED_MAX,
        COMP_COLOR_GR_SOFT_MIN,
        COMP_COLOR_GR_SOFT_MAX,
    )
    return bv_penalty + gr_penalty, bv_extreme or gr_extreme


def catalog_color_summary(candidate: CatalogObject) -> str:
    """Return compact color information for selection logs."""

    b_minus_v = candidate.color_index_float("mag_b", "mag_v")
    g_minus_r = candidate.color_index_float("mag_g", "mag_r")
    bv_text = "n/a" if b_minus_v is None else f"{b_minus_v:.3f}"
    gr_text = "n/a" if g_minus_r is None else f"{g_minus_r:.3f}"
    return f"B-V={bv_text}, g-r={gr_text}"


def format_optional_float(value: float | None, digits: int = 3, signed: bool = False) -> str:
    """Format an optional finite float for compact GUI summaries."""

    if value is None or not np.isfinite(value):
        return "n/a"
    sign = "+" if signed else ""
    return f"{value:{sign}.{digits}f}"


def format_csv_float(value: float | int | None, digits: int = 8) -> str:
    """Format an optional finite float for CSV output."""

    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(numeric):
        return ""
    return f"{numeric:.{digits}f}".rstrip("0").rstrip(".")


def color_delta_text(first: float | None, second: float | None) -> str:
    """Return a signed color-index difference or n/a."""

    if first is None or second is None or not np.isfinite(first) or not np.isfinite(second):
        return "n/a"
    return f"{first - second:+.3f}"


def low_snr_selection_penalty(snr: float, reference_snr: float) -> float:
    """Return a bounded penalty for candidates below the reference SNR."""

    if not np.isfinite(snr) or reference_snr <= 0:
        return 1.0
    return max(0.0, min(1.0, (reference_snr - snr) / reference_snr))


def score_candidate_quality(
    quality: ComparisonCandidateQuality,
    target: CatalogObject,
) -> float:
    """Return a lower-is-better selection score for one vetted candidate."""

    target_mag = target.magnitude_float()
    candidate_mag = quality.candidate.magnitude_float()
    mag_distance = (
        abs(candidate_mag - target_mag)
        if target_mag is not None and candidate_mag is not None
        else COMP_SCORE_FALLBACK_MAG_DISTANCE
    )
    target_coord = sky_coord_for_object(target)
    candidate_coord = sky_coord_for_object(quality.candidate)
    distance_penalty = 0.0
    if target_coord is not None and candidate_coord is not None:
        distance_penalty = min(target_coord.separation(candidate_coord).arcmin / 60.0, 2.0)
    nobs_penalty = COMP_SCORE_LOW_NOBS_PENALTY if (quality.candidate.nobs_v_int() or 0) <= 1 else 0.0
    catalog_error = quality.candidate.magnitude_error_float()
    catalog_error_penalty = min(
        catalog_error if catalog_error is not None else DEFAULT_COMP_EMAG,
        COMP_SCORE_CATALOG_ERROR_CAP,
    )
    inst_error_penalty = (
        quality.median_inst_mag_error
        if np.isfinite(quality.median_inst_mag_error)
        else COMP_SCORE_FALLBACK_INST_ERROR
    )
    color_penalty, extreme_color = catalog_color_selection_penalty(quality.candidate)
    invalid_rate = 1.0 - (quality.valid_count / float(quality.sample_count))
    low_snr_penalty = low_snr_selection_penalty(
        quality.median_snr,
        COMP_SCORE_LOW_SNR_REFERENCE,
    )
    return (
        quality.zero_point_offset * COMP_SCORE_ZP_OFFSET_WEIGHT
        + quality.robust_scatter * COMP_SCORE_ROBUST_SCATTER_WEIGHT
        + inst_error_penalty * COMP_SCORE_INST_ERROR_WEIGHT
        + invalid_rate * COMP_SCORE_INVALID_RATE_WEIGHT
        + low_snr_penalty * COMP_SCORE_LOW_SNR_WEIGHT
        + mag_distance * COMP_SCORE_TARGET_MAG_DISTANCE_WEIGHT
        + distance_penalty * COMP_SCORE_TARGET_DISTANCE_WEIGHT
        + nobs_penalty
        + catalog_error_penalty * COMP_SCORE_CATALOG_ERROR_WEIGHT
        + color_penalty * COMP_SCORE_COLOR_WEIGHT
        + (COMP_SCORE_EXTREME_COLOR_PENALTY if extreme_color else 0.0)
    )


def score_check_candidate_quality(quality: CheckCandidateQuality) -> float:
    """Return a lower-is-better score for one check-star candidate."""

    invalid_rate = 1.0 - (quality.valid_count / float(quality.sample_count))
    catalog_error = quality.candidate.magnitude_error_float()
    catalog_error_penalty = min(
        catalog_error if catalog_error is not None else DEFAULT_COMP_EMAG,
        CHECK_SCORE_CATALOG_ERROR_CAP,
    )
    nobs_penalty = CHECK_SCORE_LOW_NOBS_PENALTY if (quality.candidate.nobs_v_int() or 0) <= 1 else 0.0
    inst_error_penalty = (
        quality.median_inst_mag_error
        if np.isfinite(quality.median_inst_mag_error)
        else CHECK_SCORE_FALLBACK_INST_ERROR
    )
    color_penalty, extreme_color = catalog_color_selection_penalty(quality.candidate)
    low_snr_penalty = low_snr_selection_penalty(
        quality.median_snr,
        CHECK_SCORE_LOW_SNR_REFERENCE,
    )
    return (
        quality.abs_median_delta * CHECK_SCORE_DELTA_WEIGHT
        + quality.robust_scatter * CHECK_SCORE_ROBUST_SCATTER_WEIGHT
        + inst_error_penalty * CHECK_SCORE_INST_ERROR_WEIGHT
        + invalid_rate * CHECK_SCORE_INVALID_RATE_WEIGHT
        + low_snr_penalty * CHECK_SCORE_LOW_SNR_WEIGHT
        + catalog_error_penalty * CHECK_SCORE_CATALOG_ERROR_WEIGHT
        + nobs_penalty
        + color_penalty * CHECK_SCORE_COLOR_WEIGHT
        + (CHECK_SCORE_EXTREME_COLOR_PENALTY if extreme_color else 0.0)
    )


def measurement_edge_distance_px(measurement: ApertureMeasurement, frame: ReferenceFrame) -> float:
    """Return the measured object's minimum distance to the image edge."""

    if not np.isfinite(measurement.x) or not np.isfinite(measurement.y):
        return 0.0
    return float(
        min(
            measurement.x,
            measurement.y,
            frame.width - 1 - measurement.x,
            frame.height - 1 - measurement.y,
        )
    )


def comp_selection_rule_metadata() -> dict[str, object]:
    """Return machine-readable comp/check selection rule metadata."""

    return {
        "rule_id": COMP_SELECTION_RULE_ID,
        "structure_id": COMP_SELECTION_RULE_STRUCTURE_ID,
        "default_comp_dvmag": DEFAULT_COMP_DVMAG,
        "default_comp_emag": DEFAULT_COMP_EMAG,
        "default_comp_count": DEFAULT_COMP_COUNT,
        "prefilter_limit": COMP_SELECTION_PREFILTER_LIMIT,
        "pool_limit": COMP_SELECTION_POOL_LIMIT,
        "sample_frames": COMP_SELECTION_SAMPLE_FRAMES,
        "min_valid_rate": COMP_SELECTION_MIN_VALID_RATE,
        "max_zero_point_offset": COMP_SELECTION_MAX_ZERO_POINT_OFFSET,
        "max_robust_scatter": COMP_SELECTION_MAX_ROBUST_SCATTER,
        "check_min_valid_rate": CHECK_SELECTION_MIN_VALID_RATE,
        "check_max_abs_median_delta": CHECK_SELECTION_MAX_ABS_MEDIAN_DELTA,
        "min_valid_comp_stars": MIN_VALID_COMP_STARS,
        "color_bv_preferred_min": COMP_COLOR_BV_PREFERRED_MIN,
        "color_bv_preferred_max": COMP_COLOR_BV_PREFERRED_MAX,
        "color_bv_soft_min": COMP_COLOR_BV_SOFT_MIN,
        "color_bv_soft_max": COMP_COLOR_BV_SOFT_MAX,
        "color_gr_preferred_min": COMP_COLOR_GR_PREFERRED_MIN,
        "color_gr_preferred_max": COMP_COLOR_GR_PREFERRED_MAX,
        "color_gr_soft_min": COMP_COLOR_GR_SOFT_MIN,
        "color_gr_soft_max": COMP_COLOR_GR_SOFT_MAX,
        "comp_score_color_weight": COMP_SCORE_COLOR_WEIGHT,
        "comp_score_extreme_color_penalty": COMP_SCORE_EXTREME_COLOR_PENALTY,
        "check_score_color_weight": CHECK_SCORE_COLOR_WEIGHT,
        "check_score_extreme_color_penalty": CHECK_SCORE_EXTREME_COLOR_PENALTY,
        "comp_score_zp_offset_weight": COMP_SCORE_ZP_OFFSET_WEIGHT,
        "comp_score_robust_scatter_weight": COMP_SCORE_ROBUST_SCATTER_WEIGHT,
        "comp_score_inst_error_weight": COMP_SCORE_INST_ERROR_WEIGHT,
        "comp_score_invalid_rate_weight": COMP_SCORE_INVALID_RATE_WEIGHT,
        "comp_score_low_snr_weight": COMP_SCORE_LOW_SNR_WEIGHT,
        "comp_score_low_snr_reference": COMP_SCORE_LOW_SNR_REFERENCE,
        "comp_score_target_mag_distance_weight": COMP_SCORE_TARGET_MAG_DISTANCE_WEIGHT,
        "comp_score_target_distance_weight": COMP_SCORE_TARGET_DISTANCE_WEIGHT,
        "comp_score_low_nobs_penalty": COMP_SCORE_LOW_NOBS_PENALTY,
        "comp_score_catalog_error_weight": COMP_SCORE_CATALOG_ERROR_WEIGHT,
        "comp_score_catalog_error_cap": COMP_SCORE_CATALOG_ERROR_CAP,
        "comp_score_fallback_mag_distance": COMP_SCORE_FALLBACK_MAG_DISTANCE,
        "comp_score_fallback_inst_error": COMP_SCORE_FALLBACK_INST_ERROR,
        "check_score_delta_weight": CHECK_SCORE_DELTA_WEIGHT,
        "check_score_robust_scatter_weight": CHECK_SCORE_ROBUST_SCATTER_WEIGHT,
        "check_score_inst_error_weight": CHECK_SCORE_INST_ERROR_WEIGHT,
        "check_score_invalid_rate_weight": CHECK_SCORE_INVALID_RATE_WEIGHT,
        "check_score_low_snr_weight": CHECK_SCORE_LOW_SNR_WEIGHT,
        "check_score_low_snr_reference": CHECK_SCORE_LOW_SNR_REFERENCE,
        "check_score_catalog_error_weight": CHECK_SCORE_CATALOG_ERROR_WEIGHT,
        "check_score_low_nobs_penalty": CHECK_SCORE_LOW_NOBS_PENALTY,
        "check_score_catalog_error_cap": CHECK_SCORE_CATALOG_ERROR_CAP,
        "check_score_fallback_inst_error": CHECK_SCORE_FALLBACK_INST_ERROR,
    }


def vet_comparison_candidates_by_photometry_with_measurements(
    candidates: list[CatalogObject],
    target: CatalogObject,
    sequence_dir: Path,
    reference_frame: ReferenceFrame | None,
    progress: Callable[[str], None] | None = None,
) -> ComparisonVettingResult:
    """Measure candidate stars on sample frames and return stable candidates plus measurements."""

    sequence_files = solved_sequence_fits_files(sequence_dir, WORK_SEQUENCE_NAME)
    sample_files = evenly_sample_paths(sequence_files, COMP_SELECTION_SAMPLE_FRAMES)
    if not sample_files or not candidates:
        return ComparisonVettingResult((), len(sample_files), (), {})

    if reference_frame is None:
        return ComparisonVettingResult((), len(sample_files), (), {})
    aperture_settings = require_fixed_fwhm_aperture_settings(reference_frame)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            reference_data = fits.getdata(reference_frame.path)
        reference_data = np.asarray(np.squeeze(reference_data), dtype=np.float64)
        if reference_data.ndim != 2:
            raise ValueError(f"reference frame data is not 2D: shape={reference_data.shape}")
    except Exception as exc:
        if progress is not None:
            progress(f"WARNING: Could not read reference frame for annulus contamination check: {exc}")
        return ComparisonVettingResult((), len(sample_files), (), {})
    if progress is not None:
        progress(
            "Photometric compstar vetting: "
            f"{len(candidates)} candidate(s), {len(sample_files)} sample frame(s), "
            f"{DEFAULT_RINGSET_RULE_ID}, "
            f"aperture={aperture_settings.aperture_radius_px:.2f}px, "
            f"annulus={aperture_settings.annulus_inner_px:.2f}-"
            f"{aperture_settings.annulus_outer_px:.2f}px, "
            f"fwhm={aperture_settings.fwhm_px:.2f}px."
        )

    specs = [candidate_catalog_spec(candidate) for candidate in candidates]
    by_id: dict[str, list[ApertureMeasurement]] = {
        str(spec["object_id"]): []
        for spec in specs
    }
    measurements_by_frame: dict[int, dict[str, ApertureMeasurement]] = {}
    sample_frame_indices: list[int] = []
    frame_zero_points: dict[int, float] = {}
    for sample_path in sample_files:
        frame_index = sequence_index(sample_path)
        if frame_index is None:
            continue
        sample_frame_indices.append(frame_index)
        measurements = aperture_measurements_for_frame(
            sample_path,
            frame_index,
            specs,
            aperture_settings.aperture_radius_px,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
        )
        zero_points: list[float] = []
        frame_measurements: dict[str, ApertureMeasurement] = {}
        for measurement in measurements:
            by_id.setdefault(measurement.object_id, []).append(measurement)
            frame_measurements[measurement.object_id] = measurement
            if (
                measurement.valid
                and measurement.catalog_mag is not None
                and measurement.inst_mag is not None
            ):
                zero_points.append(float(measurement.catalog_mag) - float(measurement.inst_mag))
        measurements_by_frame[frame_index] = frame_measurements
        if len(zero_points) >= MIN_VALID_COMP_STARS:
            frame_zero_points[frame_index] = finite_median(zero_points)

    candidate_measurements: dict[str, list[ApertureMeasurement]] = {}
    candidate_zero_point_medians: list[float] = []
    for candidate in candidates:
        object_id = candidate.catalog_id or candidate.name
        valid_measurements = [
            measurement
            for measurement in by_id.get(object_id, [])
            if measurement.valid
            and measurement.catalog_mag is not None
            and measurement.inst_mag is not None
        ]
        candidate_measurements[object_id] = valid_measurements
        zero_points = [
            float(measurement.catalog_mag) - float(measurement.inst_mag)
            for measurement in valid_measurements
        ]
        if zero_points:
            candidate_zero_point_medians.append(finite_median(zero_points))

    ensemble_center = finite_median(candidate_zero_point_medians)
    if not np.isfinite(ensemble_center):
        return ComparisonVettingResult(
            (),
            len(sample_files),
            tuple(sample_frame_indices),
            measurements_by_frame,
        )

    qualities: list[ComparisonCandidateQuality] = []
    rejected_low_valid = 0
    rejected_offset = 0
    rejected_scatter = 0
    rejected_annulus_contaminated = 0
    for candidate in candidates:
        object_id = candidate.catalog_id or candidate.name
        valid_measurements = candidate_measurements.get(object_id, [])
        valid_count = len(valid_measurements)
        valid_rate = valid_count / float(len(sample_files))
        if valid_rate < COMP_SELECTION_MIN_VALID_RATE:
            rejected_low_valid += 1
            continue

        zero_points = [
            float(measurement.catalog_mag) - float(measurement.inst_mag)
            for measurement in valid_measurements
        ]
        median_zero_point = finite_median(zero_points)
        zero_point_offset = abs(median_zero_point - ensemble_center)
        if zero_point_offset > COMP_SELECTION_MAX_ZERO_POINT_OFFSET:
            rejected_offset += 1
            continue

        residuals = []
        for measurement in valid_measurements:
            frame_zero_point = frame_zero_points.get(measurement.frame_index)
            if frame_zero_point is None or measurement.catalog_mag is None or measurement.inst_mag is None:
                continue
            residuals.append(float(measurement.catalog_mag) - float(measurement.inst_mag) - frame_zero_point)
        scatter = robust_scatter(residuals) or float("inf")
        if scatter > COMP_SELECTION_MAX_ROBUST_SCATTER:
            rejected_scatter += 1
            continue

        ref_x, ref_y, ref_problem = object_pixel_position_in_frame(candidate, reference_frame)
        if ref_problem is not None or not np.isfinite(ref_x) or not np.isfinite(ref_y):
            rejected_annulus_contaminated += 1
            continue
        contamination_problem = annulus_contamination_problem(
            reference_data,
            ref_x,
            ref_y,
            aperture_settings.aperture_radius_px,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
            SERIES_COMP_MAX_ANNULUS_PEAK_RATIO,
            SERIES_COMP_ANNULUS_CONTAMINATION_SIGMA,
            SERIES_COMP_ANNULUS_MIN_ISLAND_PIXELS,
            SERIES_COMP_ANNULUS_MAX_BRIGHT_ISLANDS,
        )
        if contamination_problem is not None:
            rejected_annulus_contaminated += 1
            if progress is not None and rejected_annulus_contaminated <= 12:
                progress(
                    "Compstar candidate rejected by reference annulus check: "
                    f"{candidate.catalog_id or candidate.name}: {contamination_problem}."
                )
            continue

        snr = finite_median([measurement.snr for measurement in valid_measurements])
        inst_errors = [
            measurement.inst_mag_error
            for measurement in valid_measurements
            if measurement.inst_mag_error is not None
        ]
        inst_mag_error = finite_median(inst_errors)
        centroid_offset = finite_median(
            [measurement.centroid_offset_px for measurement in valid_measurements]
        )
        provisional = ComparisonCandidateQuality(
            candidate=candidate,
            valid_count=valid_count,
            sample_count=len(sample_files),
            median_zero_point=median_zero_point,
            zero_point_offset=zero_point_offset,
            robust_scatter=scatter,
            median_snr=snr,
            median_inst_mag_error=inst_mag_error,
            median_centroid_offset=centroid_offset,
            score=0.0,
        )
        qualities.append(
            ComparisonCandidateQuality(
                candidate=candidate,
                valid_count=provisional.valid_count,
                sample_count=provisional.sample_count,
                median_zero_point=provisional.median_zero_point,
                zero_point_offset=provisional.zero_point_offset,
                robust_scatter=provisional.robust_scatter,
                median_snr=provisional.median_snr,
                median_inst_mag_error=provisional.median_inst_mag_error,
                median_centroid_offset=provisional.median_centroid_offset,
                score=score_candidate_quality(provisional, target),
            )
        )

    qualities.sort(key=lambda quality: quality.score)
    if progress is not None:
        progress(
            "Photometric compstar vetting kept "
            f"{len(qualities)}/{len(candidates)} candidate(s); "
            f"rejected {rejected_low_valid} low-valid, "
            f"{rejected_offset} zero-point outlier, "
            f"{rejected_scatter} high-scatter, "
            f"{rejected_annulus_contaminated} annulus-contaminated."
        )
        for quality in qualities[: min(12, len(qualities))]:
            progress(
                "Compstar candidate "
                f"{quality.candidate.catalog_id or quality.candidate.name}: "
                f"score={quality.score:.3f}, "
                f"n={quality.valid_count}/{quality.sample_count}, "
                f"zp_offset={quality.zero_point_offset:.3f}, "
                f"scatter={quality.robust_scatter:.3f}, "
                f"SNR={quality.median_snr:.1f}, "
                f"{catalog_color_summary(quality.candidate)}."
            )
        progress("Photometric compstar vetting finished.")
    return ComparisonVettingResult(
        tuple(qualities),
        len(sample_files),
        tuple(sample_frame_indices),
        measurements_by_frame,
    )


def vet_comparison_candidates_by_photometry(
    candidates: list[CatalogObject],
    target: CatalogObject,
    sequence_dir: Path,
    reference_frame: ReferenceFrame | None,
    progress: Callable[[str], None] | None = None,
) -> list[ComparisonCandidateQuality]:
    """Measure candidate stars on sample frames and return stable candidates."""

    return list(
        vet_comparison_candidates_by_photometry_with_measurements(
            candidates,
            target,
            sequence_dir,
            reference_frame,
            progress,
        ).qualities
    )


def select_check_star_against_ensemble(
    comparison_stars: list[CatalogObject],
    check_candidates: list[CatalogObject],
    sequence_dir: Path,
    reference_frame: ReferenceFrame | None,
    progress: Callable[[str], None] | None = None,
    vetting_result: ComparisonVettingResult | None = None,
) -> CheckCandidateQuality | None:
    """Return the best check star measured against a fixed comparison ensemble."""

    if len(comparison_stars) < 3 or not check_candidates:
        return None

    comp_ids = {candidate.catalog_id or candidate.name for candidate in comparison_stars}
    candidates = [
        candidate
        for candidate in check_candidates
        if (candidate.catalog_id or candidate.name) not in comp_ids
    ]
    if not candidates:
        return None

    sequence_files = solved_sequence_fits_files(sequence_dir, WORK_SEQUENCE_NAME)
    sample_files = evenly_sample_paths(sequence_files, COMP_SELECTION_SAMPLE_FRAMES)
    if not sample_files:
        return None

    aperture_settings = require_fixed_fwhm_aperture_settings(reference_frame)
    comp_specs = [candidate_catalog_spec(candidate) for candidate in comparison_stars]
    check_specs = [candidate_catalog_spec(candidate) for candidate in candidates]
    for spec in comp_specs:
        spec["role"] = "comparison"
    for spec in check_specs:
        spec["role"] = "check"

    if progress is not None:
        progress(
            "Check Star vetting: "
            f"testing {len(candidates)} candidate(s) against "
            f"{len(comparison_stars)} compstar(s) on {len(sample_files)} sample frame(s), "
            f"{DEFAULT_RINGSET_RULE_ID}, "
            f"aperture={aperture_settings.aperture_radius_px:.2f}px, "
            f"annulus={aperture_settings.annulus_inner_px:.2f}-"
            f"{aperture_settings.annulus_outer_px:.2f}px, "
            f"fwhm={aperture_settings.fwhm_px:.2f}px."
        )

    deltas_by_id: dict[str, list[float]] = {
        str(spec["object_id"]): []
        for spec in check_specs
    }
    snr_by_id: dict[str, list[float]] = {
        str(spec["object_id"]): []
        for spec in check_specs
    }
    error_by_id: dict[str, list[float]] = {
        str(spec["object_id"]): []
        for spec in check_specs
    }
    min_valid_comps = min(MIN_VALID_COMP_STARS, len(comparison_stars))
    comp_object_ids = [str(spec["object_id"]) for spec in comp_specs]
    check_object_ids = [str(spec["object_id"]) for spec in check_specs]

    cached_frame_indices = (
        tuple(vetting_result.sample_frame_indices)
        if vetting_result is not None
        and vetting_result.sample_count == len(sample_files)
        else ()
    )
    sample_frame_indices = [
        frame_index
        for sample_path in sample_files
        if (frame_index := sequence_index(sample_path)) is not None
    ]
    use_cached_measurements = bool(
        cached_frame_indices
        and set(cached_frame_indices) == set(sample_frame_indices)
        and all(
            all(
                object_id in vetting_result.measurements_by_frame.get(frame_index, {})
                for object_id in comp_object_ids
            )
            and all(
                object_id in vetting_result.measurements_by_frame.get(frame_index, {})
                for object_id in check_object_ids
            )
            for frame_index in sample_frame_indices
        )
    )
    if progress is not None and use_cached_measurements:
        progress("Check Star vetting reuses Comp Star sample-frame measurements.")

    for sample_path in sample_files:
        frame_index = sequence_index(sample_path)
        if frame_index is None:
            continue
        if use_cached_measurements:
            frame_measurements = vetting_result.measurements_by_frame.get(frame_index, {})
            measurements = [
                frame_measurements[object_id]
                for object_id in (*comp_object_ids, *check_object_ids)
                if object_id in frame_measurements
            ]
        else:
            measurements = aperture_measurements_for_frame(
                sample_path,
                frame_index,
                comp_specs + check_specs,
                aperture_settings.aperture_radius_px,
                aperture_settings.annulus_inner_px,
                aperture_settings.annulus_outer_px,
            )
        comp_zero_points = [
            float(item.catalog_mag) - float(item.inst_mag)
            for item in measurements
            if item.object_id in comp_object_ids
            and item.valid
            and item.catalog_mag is not None
            and item.inst_mag is not None
        ]
        if len(comp_zero_points) < min_valid_comps:
            continue
        frame_zero_point = float(np.median(np.array(comp_zero_points, dtype=np.float64)))
        for item in measurements:
            if (
                item.object_id not in check_object_ids
                or not item.valid
                or item.catalog_mag is None
                or item.inst_mag is None
            ):
                continue
            delta = float(item.inst_mag) + frame_zero_point - float(item.catalog_mag)
            deltas_by_id.setdefault(item.object_id, []).append(delta)
            if np.isfinite(item.snr):
                snr_by_id.setdefault(item.object_id, []).append(float(item.snr))
            if item.inst_mag_error is not None and np.isfinite(item.inst_mag_error):
                error_by_id.setdefault(item.object_id, []).append(float(item.inst_mag_error))

    qualities: list[CheckCandidateQuality] = []
    rejected_low_valid = 0
    rejected_no_scatter = 0
    rejected_delta = 0
    for candidate in candidates:
        object_id = candidate.catalog_id or candidate.name
        deltas = deltas_by_id.get(object_id, [])
        valid_count = len(deltas)
        valid_rate = valid_count / float(len(sample_files))
        if valid_rate < CHECK_SELECTION_MIN_VALID_RATE:
            rejected_low_valid += 1
            continue
        scatter = robust_scatter(deltas)
        if scatter is None or not np.isfinite(scatter):
            rejected_no_scatter += 1
            continue
        median_delta = finite_median(deltas)
        abs_median_delta = abs(median_delta)
        if abs_median_delta > CHECK_SELECTION_MAX_ABS_MEDIAN_DELTA:
            rejected_delta += 1
            continue
        provisional = CheckCandidateQuality(
            candidate=candidate,
            valid_count=valid_count,
            sample_count=len(sample_files),
            median_delta=median_delta,
            abs_median_delta=abs_median_delta,
            robust_scatter=scatter,
            median_snr=finite_median(snr_by_id.get(object_id, [])),
            median_inst_mag_error=finite_median(error_by_id.get(object_id, [])),
            score=0.0,
        )
        qualities.append(
            CheckCandidateQuality(
                candidate=candidate,
                valid_count=provisional.valid_count,
                sample_count=provisional.sample_count,
                median_delta=provisional.median_delta,
                abs_median_delta=provisional.abs_median_delta,
                robust_scatter=provisional.robust_scatter,
                median_snr=provisional.median_snr,
                median_inst_mag_error=provisional.median_inst_mag_error,
                score=score_check_candidate_quality(provisional),
            )
        )

    qualities.sort(key=lambda quality: quality.score)
    if progress is not None:
        progress(
            "Check Star vetting kept "
            f"{len(qualities)}/{len(candidates)} candidate(s); "
            f"rejected {rejected_low_valid} low-valid, "
            f"{rejected_no_scatter} without scatter estimate, "
            f"{rejected_delta} with |delta|>{CHECK_SELECTION_MAX_ABS_MEDIAN_DELTA:.2f} mag."
        )
        for quality in qualities[: min(10, len(qualities))]:
            progress(
                "Check Star candidate "
                f"{quality.candidate.catalog_id or quality.candidate.name}: "
                f"score={quality.score:.3f}, "
                f"delta={quality.median_delta:+.3f}, "
                f"scatter={quality.robust_scatter:.3f}, "
                f"n={quality.valid_count}/{quality.sample_count}, "
                f"SNR={quality.median_snr:.1f}, "
                f"{catalog_color_summary(quality.candidate)}."
            )

    return qualities[0] if qualities else None


def parse_photometry_time_to_jd(header: fits.Header) -> tuple[str, str, float]:
    """Return the best photometric timestamp, its source key and Julian Date."""

    mjd_avg = header.get("MJD-AVG")
    if mjd_avg is not None:
        try:
            mjd_value = float(mjd_avg)
            if np.isfinite(mjd_value):
                return str(mjd_avg), "MJD-AVG", float(Time(mjd_value, format="mjd", scale="utc").jd)
        except (TypeError, ValueError):
            pass

    date_key = "DATE-AVG" if "DATE-AVG" in header else "DATE-OBS"
    date_value = str(header.get(date_key, "")).strip()
    if not date_value:
        return "", "", float("nan")
    try:
        return date_value, date_key, float(Time(date_value, format="isot", scale="utc").jd)
    except Exception:
        try:
            return date_value, date_key, float(Time(date_value).jd)
        except Exception:
            return date_value, date_key, float("nan")


def read_exposure_seconds(header: fits.Header) -> float:
    """Return exposure time in seconds from common FITS header keys."""

    for key in ("EXPTIME", "EXPOSURE"):
        if key in header:
            try:
                value = float(header[key])
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    return 1.0


def saturation_threshold_for_data(data: np.ndarray) -> float | None:
    """Return a conservative saturation threshold for integer FITS image data."""

    if not np.issubdtype(data.dtype, np.integer):
        return None
    info = np.iinfo(data.dtype)
    return float(info.max) - SATURATION_MARGIN_ADU


def saturation_count(values: np.ndarray, threshold: float | None) -> int:
    if threshold is None or values.size == 0:
        return 0
    return int(np.count_nonzero(values >= threshold))


def circular_mask(shape: tuple[int, int], x: float, y: float, radius: float) -> np.ndarray:
    """Return a boolean circular mask for a 2D image."""

    yy, xx = np.ogrid[: shape[0], : shape[1]]
    return (xx - x) ** 2 + (yy - y) ** 2 <= radius**2


def annulus_mask(
    shape: tuple[int, int],
    x: float,
    y: float,
    inner_radius: float,
    outer_radius: float,
) -> np.ndarray:
    """Return a boolean annulus mask for a 2D image."""

    yy, xx = np.ogrid[: shape[0], : shape[1]]
    distance2 = (xx - x) ** 2 + (yy - y) ** 2
    return (inner_radius**2 <= distance2) & (distance2 <= outer_radius**2)


def image_cutout_for_radius(
    data: np.ndarray,
    x: float,
    y: float,
    radius: float,
) -> tuple[np.ndarray, float, float, int, int]:
    """Return the smallest safe pixel cutout around one fractional position."""

    if data.ndim != 2:
        raise ValueError(f"image data is not 2D: shape={data.shape}")
    x0 = max(0, int(np.floor(x - radius)))
    x1 = min(data.shape[1], int(np.ceil(x + radius)) + 1)
    y0 = max(0, int(np.floor(y - radius)))
    y1 = min(data.shape[0], int(np.ceil(y + radius)) + 1)
    return data[y0:y1, x0:x1], x - x0, y - y0, x0, y0


def aperture_fits_inside_image(
    shape: tuple[int, int],
    x: float,
    y: float,
    outer_radius: float,
) -> bool:
    """Return whether the full aperture annulus passes the existing edge rule."""

    height, width = shape
    return not (
        x < outer_radius
        or y < outer_radius
        or x >= width - outer_radius
        or y >= height - outer_radius
    )


def aperture_peak_offset_px(data: np.ndarray, x: float, y: float, radius: float) -> float:
    """Return distance between WCS position and brightest aperture pixel."""

    if radius <= 0 or data.ndim != 2:
        return float("nan")
    cutout, local_x, local_y, x0, y0 = image_cutout_for_radius(data, x, y, radius)
    mask = circular_mask(cutout.shape, local_x, local_y, radius)
    if not np.any(mask):
        return float("nan")
    work = np.asarray(cutout, dtype=np.float64).copy()
    work[~mask] = -np.inf
    if not np.any(np.isfinite(work)):
        return float("nan")
    peak_y, peak_x = np.unravel_index(int(np.nanargmax(work)), work.shape)
    return float(
        np.hypot(
            float(peak_x + x0) - x,
            float(peak_y + y0) - y,
        )
    )


def aperture_annulus_peak_diagnostics(
    data: np.ndarray,
    x: float,
    y: float,
    aperture_radius: float,
    annulus_inner: float,
    annulus_outer: float,
) -> tuple[float, float, float]:
    """Return aperture/annulus peak prominence diagnostics in local background sigma."""

    if data.ndim != 2:
        return float("nan"), float("nan"), float("nan")
    cutout, local_x, local_y, _x0, _y0 = image_cutout_for_radius(
        data,
        x,
        y,
        annulus_outer,
    )
    aperture = circular_mask(cutout.shape, local_x, local_y, aperture_radius)
    annulus = annulus_mask(cutout.shape, local_x, local_y, annulus_inner, annulus_outer)
    aperture_values = cutout[aperture]
    annulus_values = cutout[annulus]
    aperture_values = aperture_values[np.isfinite(aperture_values)]
    annulus_values = annulus_values[np.isfinite(annulus_values)]
    if aperture_values.size == 0 or annulus_values.size == 0:
        return float("nan"), float("nan"), float("nan")
    background = float(np.median(annulus_values))
    sigma = robust_background_sigma(annulus_values)
    if not np.isfinite(sigma) or sigma <= 0:
        return float("nan"), float("nan"), float("nan")
    aperture_peak_sigma = float((np.max(aperture_values) - background) / sigma)
    annulus_peak_sigma = float((np.max(annulus_values) - background) / sigma)
    peak_ratio = (
        annulus_peak_sigma / aperture_peak_sigma
        if np.isfinite(aperture_peak_sigma) and aperture_peak_sigma > 0
        else float("nan")
    )
    return aperture_peak_sigma, annulus_peak_sigma, peak_ratio


def count_bright_pixel_islands(mask: np.ndarray) -> tuple[int, int]:
    """Return number and largest size of 8-connected True islands."""

    if mask.size == 0:
        return 0, 0
    visited = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    island_count = 0
    largest = 0
    for start_y, start_x in np.argwhere(mask):
        if visited[start_y, start_x]:
            continue
        island_count += 1
        size = 0
        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        while stack:
            y, x = stack.pop()
            size += 1
            for ny in range(max(0, y - 1), min(height, y + 2)):
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    if visited[ny, nx] or not mask[ny, nx]:
                        continue
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        largest = max(largest, size)
    return island_count, largest


def annulus_bright_island_diagnostics(
    data: np.ndarray,
    x: float,
    y: float,
    inner_radius: float,
    outer_radius: float,
    sigma_threshold: float = SERIES_COMP_ANNULUS_CONTAMINATION_SIGMA,
    min_island_pixels: int = SERIES_COMP_ANNULUS_MIN_ISLAND_PIXELS,
) -> tuple[int, int, float, int]:
    """Return compact bright-island diagnostics for one annulus."""

    outer = int(np.ceil(outer_radius)) + 2
    cx = int(round(x))
    cy = int(round(y))
    x0 = max(0, cx - outer)
    x1 = min(data.shape[1], cx + outer + 1)
    y0 = max(0, cy - outer)
    y1 = min(data.shape[0], cy + outer + 1)
    cutout = data[y0:y1, x0:x1]
    if cutout.size == 0:
        return 0, 0, float("nan"), 0
    local_x = x - x0
    local_y = y - y0
    mask = annulus_mask(cutout.shape, local_x, local_y, inner_radius, outer_radius)
    values = cutout[mask]
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0, 0, float("nan"), 0
    median = float(np.median(values))
    sigma = robust_background_sigma(values)
    if not np.isfinite(sigma) or sigma <= 0:
        return 0, 0, float("nan"), int(values.size)
    bright = np.zeros(cutout.shape, dtype=bool)
    bright[mask] = cutout[mask] > median + sigma_threshold * sigma
    island_count, largest = count_bright_pixel_islands(bright)
    significant = 0
    if island_count:
        # Count only islands large enough to be star-like rather than isolated hot pixels.
        visited = np.zeros(bright.shape, dtype=bool)
        for start_y, start_x in np.argwhere(bright):
            if visited[start_y, start_x]:
                continue
            size = 0
            stack = [(int(start_y), int(start_x))]
            visited[start_y, start_x] = True
            while stack:
                yy, xx = stack.pop()
                size += 1
                for ny in range(max(0, yy - 1), min(bright.shape[0], yy + 2)):
                    for nx in range(max(0, xx - 1), min(bright.shape[1], xx + 2)):
                        if visited[ny, nx] or not bright[ny, nx]:
                            continue
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            if size >= min_island_pixels:
                significant += 1
    max_sigma = float((np.nanmax(cutout[mask]) - median) / sigma)
    return significant, largest, max_sigma, int(values.size)


def annulus_contamination_problem(
    data: np.ndarray,
    x: float,
    y: float,
    aperture_radius: float,
    annulus_inner: float,
    annulus_outer: float,
    max_annulus_peak_ratio: float,
    contamination_sigma: float,
    min_island_pixels: int,
    max_bright_islands: int,
) -> str | None:
    """Return a rejection reason if the background annulus contains bright structure."""

    aperture_peak_sigma, annulus_peak_sigma, annulus_peak_ratio = aperture_annulus_peak_diagnostics(
        data,
        x,
        y,
        aperture_radius,
        annulus_inner,
        annulus_outer,
    )
    if (
        np.isfinite(annulus_peak_sigma)
        and np.isfinite(annulus_peak_ratio)
        and annulus_peak_sigma > contamination_sigma
        and annulus_peak_ratio > max_annulus_peak_ratio
    ):
        return (
            "annulus peak contamination "
            f"(annulus_peak={annulus_peak_sigma:.1f} sigma, "
            f"ratio={annulus_peak_ratio:.2f}, "
            f"limit={max_annulus_peak_ratio:.2f})"
        )

    island_count, largest_island, max_sigma, _annulus_pixels = annulus_bright_island_diagnostics(
        data,
        x,
        y,
        annulus_inner,
        annulus_outer,
        contamination_sigma,
        min_island_pixels,
    )
    if island_count > max_bright_islands:
        return (
            "annulus bright island contamination "
            f"(islands={island_count}, largest={largest_island}px, "
            f"max={max_sigma:.1f} sigma)"
        )
    return None


def annulus_contamination_problem_from_values(
    data: np.ndarray,
    x: float,
    y: float,
    aperture_radius: float,
    annulus_inner: float,
    annulus_outer: float,
    aperture_values: np.ndarray,
    annulus_values: np.ndarray,
    background: float,
    background_sigma: float,
    max_annulus_peak_ratio: float,
    contamination_sigma: float,
    min_island_pixels: int,
    max_bright_islands: int,
) -> str:
    """Return annulus contamination diagnostics while reusing measured pixels."""

    aperture_values = aperture_values[np.isfinite(aperture_values)]
    annulus_values = annulus_values[np.isfinite(annulus_values)]
    if (
        aperture_values.size > 0
        and annulus_values.size > 0
        and np.isfinite(background_sigma)
        and background_sigma > 0
    ):
        aperture_peak_sigma = float((np.max(aperture_values) - background) / background_sigma)
        annulus_peak_sigma = float((np.max(annulus_values) - background) / background_sigma)
        annulus_peak_ratio = (
            annulus_peak_sigma / aperture_peak_sigma
            if np.isfinite(aperture_peak_sigma) and aperture_peak_sigma > 0
            else float("nan")
        )
        if (
            np.isfinite(annulus_peak_sigma)
            and np.isfinite(annulus_peak_ratio)
            and annulus_peak_sigma > contamination_sigma
            and annulus_peak_ratio > max_annulus_peak_ratio
        ):
            return (
                "annulus peak contamination "
                f"(annulus_peak={annulus_peak_sigma:.1f} sigma, "
                f"ratio={annulus_peak_ratio:.2f}, "
                f"limit={max_annulus_peak_ratio:.2f})"
            )

    island_count, largest_island, max_sigma, _annulus_pixels = annulus_bright_island_diagnostics(
        data,
        x,
        y,
        annulus_inner,
        annulus_outer,
        contamination_sigma,
        min_island_pixels,
    )
    if island_count > max_bright_islands:
        return (
            "annulus bright island contamination "
            f"(islands={island_count}, largest={largest_island}px, "
            f"max={max_sigma:.1f} sigma)"
        )
    return ""


def single_annulus_mask_candidate(
    cutout: np.ndarray,
    annulus_pixels: np.ndarray,
    background: float,
    sigma_threshold: float = SERIES_COMP_ANNULUS_CONTAMINATION_SIGMA,
    min_island_pixels: int = SERIES_COMP_ANNULUS_MIN_ISLAND_PIXELS,
    dilation_px: int = SINGLE_ANNULUS_MASK_DILATION_PX,
    min_remaining_fraction: float = SINGLE_ANNULUS_MASK_MIN_REMAINING_FRACTION,
) -> SingleAnnulusMaskCandidate | None:
    """Return a conservative bright-island mask, or ``None`` when unsafe.

    All sufficiently large connected bright islands are included.  The dilation
    excludes visible star wings as well as detected cores, and the shared area
    limit prevents a crowded or structured ring from being reduced too far.
    """

    values = cutout[annulus_pixels]
    values = values[np.isfinite(values)]
    sigma = robust_background_sigma(values)
    if values.size == 0 or not np.isfinite(sigma) or sigma <= 0 or not np.isfinite(background):
        return None
    bright = annulus_pixels & np.isfinite(cutout) & (cutout > background + sigma_threshold * sigma)
    visited = np.zeros(bright.shape, dtype=bool)
    islands: list[list[tuple[int, int]]] = []
    for start_y, start_x in np.argwhere(bright):
        if visited[start_y, start_x]:
            continue
        component: list[tuple[int, int]] = []
        stack = [(int(start_y), int(start_x))]
        visited[start_y, start_x] = True
        while stack:
            yy, xx = stack.pop()
            component.append((yy, xx))
            for ny in range(max(0, yy - 1), min(bright.shape[0], yy + 2)):
                for nx in range(max(0, xx - 1), min(bright.shape[1], xx + 2)):
                    if not visited[ny, nx] and bright[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
        if len(component) >= min_island_pixels:
            islands.append(component)
    if not islands:
        return None

    excluded = np.zeros_like(annulus_pixels, dtype=bool)
    for island in islands:
        for yy, xx in island:
            y0, y1 = max(0, yy - dilation_px), min(excluded.shape[0], yy + dilation_px + 1)
            x0, x1 = max(0, xx - dilation_px), min(excluded.shape[1], xx + dilation_px + 1)
            excluded[y0:y1, x0:x1] = True
    excluded &= annulus_pixels
    annulus_count = int(np.count_nonzero(annulus_pixels))
    masked_count = int(np.count_nonzero(excluded))
    if annulus_count == 0 or masked_count == 0:
        return None
    remaining_fraction = (annulus_count - masked_count) / float(annulus_count)
    if remaining_fraction < min_remaining_fraction:
        return None
    return SingleAnnulusMaskCandidate(
        excluded,
        len(islands),
        masked_count,
        masked_count / float(annulus_count),
        remaining_fraction,
    )


def estimate_reference_fwhm(path: Path) -> float | None:
    """Estimate a robust stellar FWHM from the reference frame data."""

    try:
        with fits.open(path) as hdul:
            data = np.asarray(hdul[0].data, dtype=np.float64)
    except Exception:
        return None

    if data.ndim != 2:
        return None

    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return None

    background = float(np.median(finite))
    mad = float(np.median(np.abs(finite - background)))
    sigma = 1.4826 * mad if mad > 0 else float(np.std(finite))
    if not np.isfinite(sigma) or sigma <= 0:
        return None

    height, width = data.shape
    border = 12
    if height <= border * 2 or width <= border * 2:
        return None

    work = data.copy()
    work[~np.isfinite(work)] = background
    threshold = background + 8.0 * sigma
    candidates = np.argwhere(work[border:-border, border:-border] > threshold)
    if candidates.size == 0:
        return None

    candidates[:, 0] += border
    candidates[:, 1] += border
    intensities = work[candidates[:, 0], candidates[:, 1]]
    order = np.argsort(intensities)[::-1]

    fwhm_values: list[float] = []
    accepted_positions: list[tuple[int, int]] = []
    half_box = 7
    for candidate_index in order:
        y = int(candidates[candidate_index, 0])
        x = int(candidates[candidate_index, 1])
        if any((x - old_x) ** 2 + (y - old_y) ** 2 < 12**2 for old_x, old_y in accepted_positions):
            continue
        patch = work[y - half_box : y + half_box + 1, x - half_box : x + half_box + 1]
        if patch.shape != (half_box * 2 + 1, half_box * 2 + 1):
            continue

        local_bg = float(np.median(np.concatenate((patch[0], patch[-1], patch[:, 0], patch[:, -1]))))
        weights = patch - local_bg
        weights[weights < 0] = 0
        total = float(np.sum(weights))
        peak = float(np.max(patch) - local_bg)
        if total <= 0 or peak < 10.0 * sigma:
            continue

        yy, xx = np.indices(patch.shape)
        x_centroid = float(np.sum(xx * weights) / total)
        y_centroid = float(np.sum(yy * weights) / total)
        var_x = float(np.sum(((xx - x_centroid) ** 2) * weights) / total)
        var_y = float(np.sum(((yy - y_centroid) ** 2) * weights) / total)
        if var_x <= 0 or var_y <= 0:
            continue

        fwhm = 2.355 * float(np.sqrt((var_x + var_y) / 2.0))
        if MIN_AUTO_FWHM_PX <= fwhm <= MAX_AUTO_FWHM_PX:
            fwhm_values.append(fwhm)
            accepted_positions.append((x, y))
        if len(fwhm_values) >= MAX_FWHM_SAMPLE_STARS:
            break

    if len(fwhm_values) < 3:
        return None
    return float(np.median(np.array(fwhm_values, dtype=np.float64)))


def resolve_aperture_settings(reference_frame: ReferenceFrame | None) -> ApertureSettings:
    """Return photometry radii from config, optionally scaled by reference FWHM."""

    manual = ApertureSettings(
        MANUAL_APERTURE_RADIUS_PX,
        MANUAL_ANNULUS_INNER_PX,
        MANUAL_ANNULUS_OUTER_PX,
        "manual",
    )
    if not AUTO_APERTURE_FROM_FWHM:
        return manual
    if reference_frame is None:
        return ApertureSettings(
            manual.aperture_radius_px,
            manual.annulus_inner_px,
            manual.annulus_outer_px,
            "manual fallback",
            None,
            "no reference frame",
        )

    fwhm = estimate_reference_fwhm(reference_frame.path)
    if fwhm is None:
        return ApertureSettings(
            manual.aperture_radius_px,
            manual.annulus_inner_px,
            manual.annulus_outer_px,
            "manual fallback",
            None,
            "reference FWHM could not be estimated",
        )

    return ApertureSettings(
        fwhm * AUTO_APERTURE_RADIUS_FWHM_PERCENT / 100.0,
        fwhm * AUTO_ANNULUS_INNER_FWHM_PERCENT / 100.0,
        fwhm * AUTO_ANNULUS_OUTER_FWHM_PERCENT / 100.0,
        "auto from reference FWHM",
        fwhm,
        (
            f"{DEFAULT_RINGSET_RULE_ID}; "
            f"ap={AUTO_APERTURE_RADIUS_FWHM_PERCENT / 100.0:.2f}fwhm; "
            f"ann={AUTO_ANNULUS_INNER_FWHM_PERCENT / 100.0:.2f}"
            f"+{(AUTO_ANNULUS_OUTER_FWHM_PERCENT - AUTO_ANNULUS_INNER_FWHM_PERCENT) / 100.0:.2f}fwhm"
        ),
    )


def require_fixed_fwhm_aperture_settings(reference_frame: ReferenceFrame | None) -> ApertureSettings:
    """Return the fixed FWHM ringset or raise if only fallback pixel values exist."""

    settings = resolve_aperture_settings(reference_frame)
    if settings.fwhm_px is None:
        raise RuntimeError(
            "Reference-frame FWHM could not be estimated; fixed FWHM ringset "
            f"{DEFAULT_RINGSET_RULE_ID} cannot be applied."
        )
    return settings


def series_ringset_candidate_settings(default_settings: ApertureSettings) -> list[ApertureSettings]:
    """Return candidate aperture/annulus ringsets for one full-sequence measurement."""

    candidates: list[ApertureSettings] = []
    seen: set[tuple[float, float, float]] = set()

    def add_candidate(settings: ApertureSettings) -> None:
        key = (
            round(settings.aperture_radius_px, 3),
            round(settings.annulus_inner_px, 3),
            round(settings.annulus_outer_px, 3),
        )
        if key in seen:
            return
        if (
            settings.aperture_radius_px <= 0
            or settings.annulus_inner_px <= settings.aperture_radius_px
            or settings.annulus_outer_px <= settings.annulus_inner_px
        ):
            return
        seen.add(key)
        candidates.append(settings)

    add_candidate(default_settings)
    fwhm = default_settings.fwhm_px
    if fwhm is None or not np.isfinite(fwhm) or fwhm <= 0:
        return candidates

    for aperture_factor in SERIES_RINGSET_APERTURE_FWHM_FACTORS:
        aperture_radius = max(1.5, fwhm * aperture_factor)
        for inner_factor in SERIES_RINGSET_ANNULUS_INNER_FWHM_FACTORS:
            annulus_inner = fwhm * inner_factor
            if annulus_inner <= aperture_radius + 0.5:
                continue
            for width_factor in SERIES_RINGSET_ANNULUS_WIDTH_FWHM_FACTORS:
                annulus_outer = annulus_inner + fwhm * width_factor
                if annulus_outer <= annulus_inner + 1.0:
                    continue
                add_candidate(
                    ApertureSettings(
                        aperture_radius,
                        annulus_inner,
                        annulus_outer,
                        "series optimized",
                        fwhm,
                        f"{SERIES_RINGSET_RULE_ID}; "
                        f"ap={aperture_factor:.2f}fwhm; "
                        f"ann={inner_factor:.2f}+{width_factor:.2f}fwhm",
                    )
                )
    return candidates


def score_series_ringset_measurements(
    settings: ApertureSettings,
    sample_measurements: list[list[ApertureMeasurement]],
) -> SeriesRingsetOptimizationResult:
    """Return a lower-is-better score for one sequence-wide ringset."""

    sample_count = len(sample_measurements)
    target_valid_count = 0
    comp_valid_frame_count = 0
    calibrated_frame_count = 0
    target_snrs: list[float] = []
    target_errors: list[float] = []
    zero_point_scatters: list[float] = []
    check_deltas: list[float] = []

    for measurements in sample_measurements:
        target = next(
            (
                item
                for item in measurements
                if item.role == "target"
                and item.valid
                and item.inst_mag is not None
            ),
            None,
        )
        comp_items = [
            item
            for item in measurements
            if item.role == "comparison"
            and item.valid
            and item.inst_mag is not None
            and item.catalog_mag is not None
        ]
        if target is not None:
            target_valid_count += 1
            if np.isfinite(target.snr):
                target_snrs.append(float(target.snr))
            if target.inst_mag_error is not None and np.isfinite(target.inst_mag_error):
                target_errors.append(float(target.inst_mag_error))
        if len(comp_items) >= MIN_VALID_COMP_STARS:
            comp_valid_frame_count += 1
            zero_points = np.array(
                [float(item.catalog_mag) - float(item.inst_mag) for item in comp_items],
                dtype=np.float64,
            )
            zero_point = float(np.median(zero_points))
            scatter = float(np.std(zero_points, ddof=1)) if len(zero_points) > 1 else 0.0
            zero_point_scatters.append(scatter)
            if target is not None:
                calibrated_frame_count += 1
            check = next((item for item in measurements if item.role == "check"), None)
            if (
                check is not None
                and check.valid
                and check.inst_mag is not None
                and check.catalog_mag is not None
            ):
                check_deltas.append(float(check.inst_mag) + zero_point - float(check.catalog_mag))

    target_valid_rate = target_valid_count / float(sample_count) if sample_count else 0.0
    comp_valid_frame_rate = comp_valid_frame_count / float(sample_count) if sample_count else 0.0
    median_target_snr = finite_median(target_snrs) if target_snrs else float("nan")
    median_target_error = finite_median(target_errors) if target_errors else float("nan")
    median_zero_point_scatter = finite_median(zero_point_scatters) if zero_point_scatters else float("nan")
    p90_zero_point_scatter = (
        float(np.percentile(np.array(zero_point_scatters, dtype=np.float64), 90))
        if zero_point_scatters
        else float("nan")
    )
    check_delta_mad_sigma = robust_scatter(check_deltas)
    median_abs_check_delta = finite_median([abs(value) for value in check_deltas]) if check_deltas else float("nan")

    score = 0.0
    score += (1.0 - target_valid_rate) * SERIES_RINGSET_SCORE_TARGET_INVALID_WEIGHT
    score += (1.0 - comp_valid_frame_rate) * SERIES_RINGSET_SCORE_COMP_INVALID_WEIGHT
    score += (
        median_target_error
        if np.isfinite(median_target_error)
        else MAX_INSTRUMENTAL_MAG_ERROR
    ) * SERIES_RINGSET_SCORE_TARGET_ERROR_WEIGHT
    score += (
        median_zero_point_scatter
        if np.isfinite(median_zero_point_scatter)
        else 1.0
    ) * SERIES_RINGSET_SCORE_ZP_SCATTER_WEIGHT
    score += (
        p90_zero_point_scatter
        if np.isfinite(p90_zero_point_scatter)
        else 1.0
    ) * (SERIES_RINGSET_SCORE_ZP_SCATTER_WEIGHT * 0.5)
    if check_delta_mad_sigma is not None and np.isfinite(check_delta_mad_sigma):
        score += check_delta_mad_sigma * SERIES_RINGSET_SCORE_CHECK_SCATTER_WEIGHT
    if np.isfinite(median_abs_check_delta):
        score += min(median_abs_check_delta, 1.0) * SERIES_RINGSET_SCORE_CHECK_DELTA_WEIGHT

    return SeriesRingsetOptimizationResult(
        settings,
        score,
        sample_count,
        calibrated_frame_count,
        target_valid_rate,
        comp_valid_frame_rate,
        median_target_snr if np.isfinite(median_target_snr) else None,
        median_target_error if np.isfinite(median_target_error) else None,
        median_zero_point_scatter if np.isfinite(median_zero_point_scatter) else None,
        p90_zero_point_scatter if np.isfinite(p90_zero_point_scatter) else None,
        check_delta_mad_sigma,
        median_abs_check_delta if np.isfinite(median_abs_check_delta) else None,
    )


def format_series_ringset_result(result: SeriesRingsetOptimizationResult) -> str:
    """Return a compact one-line summary for a series ringset optimization result."""

    snr_text = "n/a" if result.median_target_snr is None else f"{result.median_target_snr:.1f}"
    error_text = "n/a" if result.median_target_error is None else f"{result.median_target_error:.3f}"
    scatter_text = (
        "n/a"
        if result.median_zero_point_scatter is None
        else f"{result.median_zero_point_scatter:.3f}"
    )
    p90_scatter_text = (
        "n/a"
        if result.p90_zero_point_scatter is None
        else f"{result.p90_zero_point_scatter:.3f}"
    )
    check_scatter_text = (
        "n/a"
        if result.check_delta_mad_sigma is None
        else f"{result.check_delta_mad_sigma:.3f}"
    )
    check_delta_text = (
        "n/a"
        if result.median_abs_check_delta is None
        else f"{result.median_abs_check_delta:.3f}"
    )
    return (
        f"score={result.score:.3f}, "
        f"ap={result.settings.aperture_radius_px:.2f}px, "
        f"ann={result.settings.annulus_inner_px:.2f}-"
        f"{result.settings.annulus_outer_px:.2f}px, "
        f"frames={result.calibrated_frame_count}/{result.sample_count}, "
        f"target_valid={result.target_valid_rate:.2f}, "
        f"comp_valid={result.comp_valid_frame_rate:.2f}, "
        f"target_snr_med={snr_text}, "
        f"target_err_med={error_text}, "
        f"zp_scatter_med={scatter_text}, "
        f"zp_scatter_p90={p90_scatter_text}, "
        f"check_mad={check_scatter_text}, "
        f"check_abs_med={check_delta_text}"
    )


def robust_background_sigma(values: np.ndarray) -> float:
    """Return a robust estimate of per-pixel background scatter."""

    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return float("nan")
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    if mad > 0:
        sigma = 1.4826 * mad
    elif finite.size > 1:
        sigma = float(np.std(finite, ddof=1))
    else:
        sigma = 0.0
    return sigma if np.isfinite(sigma) and sigma >= 0 else float("nan")


def aperture_flux_uncertainty(
    net_flux: float,
    background_sigma: float,
    aperture_pixel_count: int,
    annulus_pixel_count: int,
) -> float:
    """Estimate net-flux uncertainty from source flux and local background noise."""

    if (
        not np.isfinite(net_flux)
        or not np.isfinite(background_sigma)
        or aperture_pixel_count <= 0
        or annulus_pixel_count <= 0
    ):
        return float("nan")
    source_variance = max(net_flux, 0.0)
    aperture_background_variance = float(aperture_pixel_count) * background_sigma**2
    median_background_variance = (
        (float(aperture_pixel_count) ** 2)
        * (1.253 * background_sigma) ** 2
        / float(annulus_pixel_count)
    )
    variance = source_variance + aperture_background_variance + median_background_variance
    return float(np.sqrt(variance)) if variance > 0 and np.isfinite(variance) else float("nan")


def aperture_centroid_offset(
    data_shape: tuple[int, int],
    x: float,
    y: float,
    aperture_radius: float,
    aperture_values: np.ndarray,
    background: float,
    coordinate_offset: tuple[int, int] = (0, 0),
) -> tuple[float, float, float]:
    """Return background-subtracted aperture centroid and offset from the WCS position."""

    yy, xx = np.indices(data_shape)
    mask = circular_mask(data_shape, x, y, aperture_radius)
    x_values = xx[mask] + coordinate_offset[0]
    y_values = yy[mask] + coordinate_offset[1]
    weights = np.asarray(aperture_values, dtype=np.float64) - background
    weights = np.where(np.isfinite(weights) & (weights > 0), weights, 0.0)
    weight_sum = float(np.sum(weights))
    if weight_sum <= 0 or len(weights) != len(x_values):
        return float("nan"), float("nan"), float("nan")
    centroid_x = float(np.sum(x_values * weights) / weight_sum)
    centroid_y = float(np.sum(y_values * weights) / weight_sum)
    x += coordinate_offset[0]
    y += coordinate_offset[1]
    offset = float(np.hypot(centroid_x - x, centroid_y - y))
    return centroid_x, centroid_y, offset


def annulus_contamination_impact(
    annulus_values: np.ndarray,
    background: float,
    aperture_sum: float,
    aperture_pixel_count: int,
    net_flux: float,
    exptime: float,
    sigma_threshold: float = SERIES_COMP_ANNULUS_CONTAMINATION_SIGMA,
) -> tuple[float, float, int, float]:
    """Estimate magnitude impact from removing bright annulus pixels."""

    values = annulus_values[np.isfinite(annulus_values)]
    if (
        values.size == 0
        or aperture_pixel_count <= 0
        or net_flux <= 0
        or exptime <= 0
        or not np.isfinite(net_flux)
    ):
        return float("nan"), float("nan"), 0, 0.0
    sigma = robust_background_sigma(values)
    if np.isfinite(sigma) and sigma > 0:
        clip_limit = background + sigma_threshold * sigma
        clean_values = values[values <= clip_limit]
    else:
        clean_values = values
    if clean_values.size == 0:
        clean_values = values
    clean_background = float(np.median(clean_values))
    rejected_count = int(values.size - clean_values.size)
    rejected_fraction = rejected_count / float(values.size)
    clean_net_flux = aperture_sum - clean_background * float(aperture_pixel_count)
    if clean_net_flux <= 0 or not np.isfinite(clean_net_flux):
        return clean_background, float("nan"), rejected_count, rejected_fraction
    original_inst_mag = float(-2.5 * np.log10(net_flux / exptime))
    clean_inst_mag = float(-2.5 * np.log10(clean_net_flux / exptime))
    return clean_background, clean_inst_mag - original_inst_mag, rejected_count, rejected_fraction


def evaluate_photometry_quality(
    *,
    role: str,
    aperture_satpix: int,
    annulus_satpix: int,
    aperture_highpix: int,
    annulus_highpix: int,
    annulus_contamination: str,
    annulus_delta_inst_mag: float,
    net_flux: float,
    snr: float,
    inst_mag_error: float | None,
    centroid_offset: float,
    aperture_radius: float,
) -> PhotometryQualityDecision:
    """Return the central OK/WARNING/INVALID decision for one measurement."""

    if aperture_satpix > 0:
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "SATURATED_APERTURE",
            False,
            f"aperture contains {aperture_satpix} saturated pixel(s)",
        )
    if annulus_satpix > 0:
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "SATURATED_ANNULUS",
            False,
            f"annulus contains {annulus_satpix} saturated pixel(s)",
        )
    reference_role = role in {
        "comparison", "check", "field_zp_reference", "analyze_linearity_reference"
    }
    if reference_role and aperture_highpix > 0:
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "HIGH_PIXEL_APERTURE",
            False,
            (
                f"reference aperture contains {aperture_highpix} pixel(s) "
                f">= {PHOTOMETRY_HIGH_PIXEL_ADU:.0f} ADU"
            ),
        )
    if role == "target" and aperture_highpix >= TARGET_HIGH_PIXEL_INVALID_COUNT:
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "HIGH_PIXEL_APERTURE",
            False,
            (
                f"target aperture contains {aperture_highpix} pixel(s) "
                f">= {PHOTOMETRY_HIGH_PIXEL_ADU:.0f} ADU"
            ),
        )
    if reference_role and annulus_highpix > 0:
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "HIGH_PIXEL_ANNULUS",
            False,
            (
                f"reference annulus contains {annulus_highpix} pixel(s) "
                f">= {PHOTOMETRY_HIGH_PIXEL_ADU:.0f} ADU"
            ),
        )
    if net_flux <= 0 or not np.isfinite(net_flux):
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "NON_POSITIVE_FLUX",
            False,
            "non-positive net flux",
        )
    if not np.isfinite(snr):
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "INVALID_SNR",
            False,
            "could not estimate photometric SNR",
        )
    if snr < MIN_PHOTOMETRY_SNR:
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "LOW_SNR",
            False,
            f"SNR {snr:.2f} below {MIN_PHOTOMETRY_SNR:.1f}",
        )
    if inst_mag_error is None or inst_mag_error > MAX_INSTRUMENTAL_MAG_ERROR:
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "HIGH_MAG_ERROR",
            False,
            (
                "instrumental magnitude error "
                f"{inst_mag_error:.3f} exceeds {MAX_INSTRUMENTAL_MAG_ERROR:.3f}"
                if inst_mag_error is not None
                else "instrumental magnitude error unavailable"
            ),
        )
    if (
        np.isfinite(centroid_offset)
        and centroid_offset > max(MIN_CENTROID_OFFSET_PX, aperture_radius * MAX_CENTROID_OFFSET_FRACTION)
    ):
        limit = max(MIN_CENTROID_OFFSET_PX, aperture_radius * MAX_CENTROID_OFFSET_FRACTION)
        return PhotometryQualityDecision(
            QUALITY_STATUS_INVALID,
            "CENTROID_OFFSET",
            False,
            f"source centroid offset {centroid_offset:.2f}px exceeds {limit:.2f}px",
        )
    if annulus_contamination:
        if np.isfinite(annulus_delta_inst_mag):
            abs_delta = abs(annulus_delta_inst_mag)
            if reference_role and abs_delta >= REFERENCE_ANNULUS_IMPACT_INVALID_MAG:
                return PhotometryQualityDecision(
                    QUALITY_STATUS_INVALID,
                    "ANNULUS_CONTAMINATION",
                    False,
                    (
                        f"{annulus_contamination}; "
                        f"estimated impact {abs_delta:.4f} mag >= "
                        f"{REFERENCE_ANNULUS_IMPACT_INVALID_MAG:.2f} mag"
                    ),
                )
            if role == "target" and abs_delta >= TARGET_ANNULUS_IMPACT_INVALID_MAG:
                return PhotometryQualityDecision(
                    QUALITY_STATUS_INVALID,
                    "ANNULUS_CONTAMINATION",
                    False,
                    (
                        f"{annulus_contamination}; "
                        f"estimated impact {abs_delta:.4f} mag >= "
                        f"{TARGET_ANNULUS_IMPACT_INVALID_MAG:.2f} mag"
                    ),
                )
            if reference_role and abs_delta >= REFERENCE_ANNULUS_IMPACT_WARNING_MAG:
                return PhotometryQualityDecision(
                    QUALITY_STATUS_WARNING,
                    "ANNULUS_CONTAMINATION_WARNING",
                    True,
                    (
                        f"{annulus_contamination}; "
                        f"estimated impact {abs_delta:.4f} mag >= "
                        f"{REFERENCE_ANNULUS_IMPACT_WARNING_MAG:.2f} mag"
                    ),
                )
            if role == "target" and abs_delta >= TARGET_ANNULUS_IMPACT_WARNING_MAG:
                return PhotometryQualityDecision(
                    QUALITY_STATUS_WARNING,
                    "ANNULUS_CONTAMINATION_WARNING",
                    True,
                    (
                        f"{annulus_contamination}; "
                        f"estimated impact {abs_delta:.4f} mag >= "
                        f"{TARGET_ANNULUS_IMPACT_WARNING_MAG:.2f} mag"
                    ),
                )
        else:
            return PhotometryQualityDecision(
                QUALITY_STATUS_WARNING,
                "ANNULUS_CONTAMINATION_UNQUANTIFIED",
                True,
                f"{annulus_contamination}; estimated impact unavailable",
            )
    if role == "target" and aperture_highpix > 0:
        return PhotometryQualityDecision(
            QUALITY_STATUS_WARNING,
            "HIGH_PIXEL_APERTURE_WARNING",
            True,
            (
                f"target aperture contains {aperture_highpix} pixel(s) "
                f">= {PHOTOMETRY_HIGH_PIXEL_ADU:.0f} ADU"
            ),
        )
    if role == "target" and annulus_highpix > 0:
        return PhotometryQualityDecision(
            QUALITY_STATUS_WARNING,
            "HIGH_PIXEL_ANNULUS_WARNING",
            True,
            (
                f"target annulus contains {annulus_highpix} pixel(s) "
                f">= {PHOTOMETRY_HIGH_PIXEL_ADU:.0f} ADU"
            ),
        )
    if role == "target" and snr < SINGLE_TARGET_LOW_SNR_WARNING:
        return PhotometryQualityDecision(
            QUALITY_STATUS_WARNING,
            "LOW_SNR_WARNING",
            True,
            f"target SNR {snr:.2f} below warning level {SINGLE_TARGET_LOW_SNR_WARNING:.1f}",
        )
    return PhotometryQualityDecision(QUALITY_STATUS_OK, "OK", True, "")


def measurement_quality_status(measurement: ApertureMeasurement) -> str:
    """Return a normalized OK/WARNING/INVALID status for existing measurements."""

    status = measurement.quality_status.strip().upper()
    if status in {QUALITY_STATUS_OK, QUALITY_STATUS_WARNING, QUALITY_STATUS_INVALID}:
        return status
    return QUALITY_STATUS_OK if measurement.valid else QUALITY_STATUS_INVALID


def combine_quality_flag(existing: str, extra: str) -> str:
    """Append a quality flag while preserving existing non-OK flags."""

    existing = existing.strip() or "OK"
    extra = extra.strip() or "OK"
    if extra == "OK":
        return existing
    if existing == "OK":
        return extra
    parts = existing.split("|")
    if extra not in parts:
        parts.append(extra)
    return "|".join(parts)


def combine_quality_note(existing: str, extra: str) -> str:
    """Append a quality note without producing empty punctuation."""

    existing = existing.strip()
    extra = extra.strip()
    if not existing:
        return extra
    if not extra:
        return existing
    return f"{existing}; {extra}"


def apply_series_local_background_quality(
    measurements: list[ApertureMeasurement],
) -> tuple[list[ApertureMeasurement], SeriesLocalBackgroundAssessment]:
    """Reject broad local shading from already measured annulus backgrounds.

    The per-frame target background is divided by the median background of the
    valid comparison stars.  A robust per-series baseline removes persistent
    field gradients and target nebulosity.  Only large fractional departures
    that are also robust statistical outliers affect quality.
    """

    by_frame: dict[int, list[tuple[int, ApertureMeasurement]]] = defaultdict(list)
    for index, measurement in enumerate(measurements):
        by_frame[measurement.frame_index].append((index, measurement))

    samples: list[tuple[int, float, int]] = []
    for frame_items in by_frame.values():
        targets = [
            (index, item)
            for index, item in frame_items
            if item.role == "target"
            and item.valid
            and np.isfinite(item.background_median)
            and item.background_median > 0
        ]
        comparisons = [
            item
            for _index, item in frame_items
            if item.role == "comparison"
            and item.valid
            and np.isfinite(item.background_median)
            and item.background_median > 0
        ]
        if len(targets) != 1 or len(comparisons) < SERIES_LOCAL_BACKGROUND_MIN_COMPS:
            continue
        target_index, target = targets[0]
        comparison_background = float(
            np.median(
                np.array(
                    [item.background_median for item in comparisons],
                    dtype=np.float64,
                )
            )
        )
        if not np.isfinite(comparison_background) or comparison_background <= 0:
            continue
        samples.append(
            (
                target_index,
                float(target.background_median / comparison_background),
                len(comparisons),
            )
        )

    empty = SeriesLocalBackgroundAssessment(
        method=SERIES_LOCAL_BACKGROUND_METHOD_ID,
        eligible_count=len(samples),
        warning_count=0,
        invalid_count=0,
        baseline_ratio=None,
        robust_sigma=None,
    )
    if len(samples) < SERIES_LOCAL_BACKGROUND_MIN_FRAMES:
        return measurements, empty

    ratios = np.array([ratio for _index, ratio, _count in samples], dtype=np.float64)
    baseline = float(np.median(ratios))
    mad = float(np.median(np.abs(ratios - baseline)))
    robust_sigma = max(
        1.4826 * mad,
        abs(baseline) * SERIES_LOCAL_BACKGROUND_SIGMA_FLOOR_FRACTION,
    )
    if not np.isfinite(baseline) or baseline <= 0 or not np.isfinite(robust_sigma):
        return measurements, empty

    assessed = list(measurements)
    warning_count = 0
    invalid_count = 0
    for target_index, ratio, comparison_count in samples:
        fractional_departure = abs(ratio / baseline - 1.0)
        sigma_departure = abs(ratio - baseline) / robust_sigma
        invalid = (
            fractional_departure >= SERIES_LOCAL_BACKGROUND_INVALID_FRACTION
            and sigma_departure >= SERIES_LOCAL_BACKGROUND_INVALID_SIGMA
        )
        warning = (
            fractional_departure >= SERIES_LOCAL_BACKGROUND_WARNING_FRACTION
            and sigma_departure >= SERIES_LOCAL_BACKGROUND_WARNING_SIGMA
        )
        if not invalid and not warning:
            continue
        target = assessed[target_index]
        if invalid:
            status = QUALITY_STATUS_INVALID
            flag = "LOCAL_BACKGROUND_ANOMALY"
            invalid_count += 1
        else:
            status = QUALITY_STATUS_WARNING
            flag = "LOCAL_BACKGROUND_ANOMALY_WARNING"
            warning_count += 1
        note = (
            "local target/comparison background ratio anomaly: "
            f"ratio={ratio:.4f}, baseline={baseline:.4f}, "
            f"departure={fractional_departure * 100.0:.1f}%, "
            f"robust_z={sigma_departure:.1f}, comparisons={comparison_count}"
        )
        assessed[target_index] = replace(
            target,
            valid=target.valid and not invalid,
            quality_status=status,
            quality_flag=combine_quality_flag(target.quality_flag, flag),
            note=combine_quality_note(target.note, note),
        )

    return assessed, SeriesLocalBackgroundAssessment(
        method=SERIES_LOCAL_BACKGROUND_METHOD_ID,
        eligible_count=len(samples),
        warning_count=warning_count,
        invalid_count=invalid_count,
        baseline_ratio=baseline,
        robust_sigma=robust_sigma,
    )


def apply_series_temporal_spike_quality(
    measurements: list[ApertureMeasurement],
) -> tuple[list[ApertureMeasurement], SeriesTemporalSpikeAssessment]:
    """Reject isolated calibrated target impulses bracketed by agreeing neighbors."""

    by_frame: dict[int, list[tuple[int, ApertureMeasurement]]] = defaultdict(list)
    for index, measurement in enumerate(measurements):
        by_frame[measurement.frame_index].append((index, measurement))

    samples: list[tuple[int, int, float, float, float]] = []
    for frame_index, frame_items in by_frame.items():
        targets = [
            (index, item)
            for index, item in frame_items
            if item.role == "target"
            and item.valid
            and item.inst_mag is not None
            and item.inst_mag_error is not None
            and np.isfinite(item.jd)
            and np.isfinite(item.inst_mag)
            and np.isfinite(item.inst_mag_error)
            and item.inst_mag_error > 0
        ]
        comparisons = [
            item
            for _index, item in frame_items
            if item.role == "comparison"
            and item.valid
            and item.catalog_mag is not None
            and item.inst_mag is not None
            and np.isfinite(item.catalog_mag)
            and np.isfinite(item.inst_mag)
        ]
        if len(targets) != 1 or len(comparisons) < MIN_VALID_COMP_STARS:
            continue
        target_index, target = targets[0]
        zero_points = np.array(
            [float(item.catalog_mag) - float(item.inst_mag) for item in comparisons],
            dtype=np.float64,
        )
        zero_point = float(np.median(zero_points))
        scatter = float(np.std(zero_points, ddof=1)) if len(zero_points) > 1 else 0.0
        zero_point_error = scatter / float(np.sqrt(len(zero_points)))
        calibrated_error = float(
            np.sqrt(float(target.inst_mag_error) ** 2 + zero_point_error**2)
        )
        calibrated_mag = float(target.inst_mag) + zero_point
        if not np.isfinite(calibrated_mag) or not np.isfinite(calibrated_error):
            continue
        samples.append(
            (target_index, frame_index, float(target.jd), calibrated_mag, calibrated_error)
        )

    samples.sort(key=lambda item: item[2])
    deltas = [
        later[2] - earlier[2]
        for earlier, later in zip(samples, samples[1:])
        if later[2] > earlier[2]
    ]
    median_cadence = float(np.median(np.array(deltas))) if deltas else None
    empty = SeriesTemporalSpikeAssessment(
        method=SERIES_TEMPORAL_SPIKE_METHOD_ID,
        eligible_count=len(samples),
        rejected_count=0,
        median_cadence_seconds=(
            None if median_cadence is None else median_cadence * 86400.0
        ),
    )
    if len(samples) < 3 or median_cadence is None or median_cadence <= 0:
        return measurements, empty

    assessed = list(measurements)
    rejected_count = 0
    max_gap = SERIES_TEMPORAL_SPIKE_MAX_GAP_FACTOR * median_cadence
    for previous, current, following in zip(samples, samples[1:], samples[2:]):
        previous_gap = current[2] - previous[2]
        following_gap = following[2] - current[2]
        if previous_gap <= 0 or following_gap <= 0:
            continue
        if previous_gap > max_gap or following_gap > max_gap:
            continue
        neighbor_delta = abs(previous[3] - following[3])
        if neighbor_delta > SERIES_TEMPORAL_SPIKE_MAX_NEIGHBOR_DELTA_MAG:
            continue
        fraction = previous_gap / (previous_gap + following_gap)
        predicted_mag = previous[3] + fraction * (following[3] - previous[3])
        residual = current[3] - predicted_mag
        residual_error = float(
            np.sqrt(
                current[4] ** 2
                + ((1.0 - fraction) * previous[4]) ** 2
                + (fraction * following[4]) ** 2
            )
        )
        if not np.isfinite(residual_error) or residual_error <= 0:
            continue
        significance = abs(residual) / residual_error
        if (
            abs(residual) < SERIES_TEMPORAL_SPIKE_MIN_MAG
            or significance < SERIES_TEMPORAL_SPIKE_MIN_SIGMA
        ):
            continue
        target = assessed[current[0]]
        note = (
            "isolated temporal target impulse: "
            f"residual={residual:+.4f} mag, significance={significance:.1f} sigma, "
            f"bracketing neighbor delta={neighbor_delta:.4f} mag, "
            f"gaps={previous_gap * 86400.0:.1f}/{following_gap * 86400.0:.1f} s"
        )
        assessed[current[0]] = replace(
            target,
            valid=False,
            quality_status=QUALITY_STATUS_INVALID,
            quality_flag=combine_quality_flag(
                target.quality_flag,
                "TEMPORAL_TARGET_SPIKE",
            ),
            note=combine_quality_note(target.note, note),
        )
        rejected_count += 1

    return assessed, SeriesTemporalSpikeAssessment(
        method=SERIES_TEMPORAL_SPIKE_METHOD_ID,
        eligible_count=len(samples),
        rejected_count=rejected_count,
        median_cadence_seconds=median_cadence * 86400.0,
    )


def apply_target_blend_assessment(
    measurement: ApertureMeasurement,
    assessment: TargetBlendAssessment,
) -> ApertureMeasurement:
    """Apply only the catalog-blend quality decision to a completed measurement."""

    if measurement.role != "target":
        return measurement
    if assessment.status == QUALITY_STATUS_OK:
        if assessment.flag == "OK":
            return measurement
        return replace(
            measurement,
            quality_flag=combine_quality_flag(measurement.quality_flag, assessment.flag),
            note=combine_quality_note(measurement.note, assessment.message),
        )
    current_status = measurement_quality_status(measurement)
    if current_status == QUALITY_STATUS_INVALID:
        return replace(
            measurement,
            quality_flag=combine_quality_flag(measurement.quality_flag, assessment.flag),
            note=combine_quality_note(measurement.note, assessment.message),
        )
    is_invalid = assessment.status == QUALITY_STATUS_INVALID
    return replace(
        measurement,
        valid=measurement.valid and not is_invalid,
        quality_status=(
            QUALITY_STATUS_INVALID if is_invalid else QUALITY_STATUS_WARNING
        ),
        quality_flag=combine_quality_flag(measurement.quality_flag, assessment.flag),
        note=combine_quality_note(measurement.note, assessment.message),
    )


def apply_target_blend_assessment_to_measurements(
    measurements: list[ApertureMeasurement],
    assessment: TargetBlendAssessment,
) -> list[ApertureMeasurement]:
    """Apply one field-level blend assessment after all raw measurements exist."""

    return [
        apply_target_blend_assessment(measurement, assessment)
        for measurement in measurements
    ]


def diagnostic_target_blend_measurements(
    measured: list[ApertureMeasurement],
    assessed: list[ApertureMeasurement],
) -> list[ApertureMeasurement]:
    """Restore only pre-blend-valid targets for diagnostic calibration."""

    measured_valid_targets = {
        (item.frame_index, item.filename, item.object_id)
        for item in measured
        if item.role == "target" and item.valid and item.inst_mag is not None
    }
    diagnostic: list[ApertureMeasurement] = []
    for item in assessed:
        identity = (item.frame_index, item.filename, item.object_id)
        blend_rejected = (
            item.role == "target"
            and "TARGET_BLEND_MODELED_CONTAMINATION" in item.quality_flag.split("|")
        )
        if blend_rejected and identity in measured_valid_targets:
            diagnostic.append(replace(item, valid=True))
        else:
            diagnostic.append(item)
    return diagnostic


def bright_end_residual_statistics(
    reference_rows: Iterable[tuple[float, float]],
    *,
    group_size: int = BRIGHT_LINEARITY_GROUP_SIZE,
    min_control_count: int = BRIGHT_LINEARITY_MIN_CONTROL_COUNT,
) -> tuple[int, float, float, float, float] | None:
    """Return count, bright limits, median ZP shift and its robust uncertainty."""

    rows = sorted(
        (
            (float(inst_mag), float(catalog_mag) - float(inst_mag))
            for inst_mag, catalog_mag in reference_rows
            if np.isfinite(inst_mag) and np.isfinite(catalog_mag)
        ),
        key=lambda row: row[0],
    )
    if len(rows) < group_size + min_control_count:
        return None
    bright_rows = rows[:group_size]
    control_rows = rows[group_size : group_size + group_size]
    if len(control_rows) < min_control_count:
        return None
    bright_zero_points = [zero_point for _inst_mag, zero_point in bright_rows]
    control_zero_points = [zero_point for _inst_mag, zero_point in control_rows]
    shift = finite_median(bright_zero_points) - finite_median(control_zero_points)
    bright_scatter = robust_scatter(bright_zero_points) or 0.0
    control_scatter = robust_scatter(control_zero_points) or 0.0
    uncertainty = float(
        np.sqrt(
            (bright_scatter / np.sqrt(len(bright_zero_points))) ** 2
            + (control_scatter / np.sqrt(len(control_zero_points))) ** 2
        )
    )
    return (
        len(rows),
        bright_rows[0][0],
        bright_rows[-1][0],
        shift,
        uncertainty,
    )


def assess_bright_instrumental_range(
    target: ApertureMeasurement,
    references: Iterable[ApertureMeasurement],
    *,
    effect_limit_mag: float = BRIGHT_LINEARITY_EFFECT_LIMIT_MAG,
    confidence_sigma: float = BRIGHT_LINEARITY_CONFIDENCE_SIGMA,
    min_reference_count: int = LINEARITY_MIN_REFERENCE_COUNT,
) -> BrightLinearityAssessment:
    """Assess a target against the measured bright-end reference response."""

    if target.inst_mag is None or not np.isfinite(target.inst_mag):
        return BrightLinearityAssessment(
            QUALITY_STATUS_OK,
            "OK",
            True,
            "target has no finite instrumental magnitude for bright-linearity check",
            0,
            None,
            target.catalog_mag,
            None,
        )

    reference_rows = [
        (float(item.inst_mag), float(item.catalog_mag))
        for item in references
        if item.valid
        and item.inst_mag is not None
        and item.catalog_mag is not None
        and np.isfinite(item.inst_mag)
        and np.isfinite(item.catalog_mag)
    ]
    brightest_reference_inst_mag = (
        min(inst_mag for inst_mag, _catalog_mag in reference_rows)
        if reference_rows
        else None
    )
    margin = (
        brightest_reference_inst_mag - float(target.inst_mag)
        if brightest_reference_inst_mag is not None
        else None
    )
    if margin is not None and margin > 0.0:
        return BrightLinearityAssessment(
            QUALITY_STATUS_WARNING,
            "BRIGHT_LINEARITY_UNVERIFIED",
            True,
            (
                f"target inst_mag={float(target.inst_mag):.3f} is {margin:.2f} mag brighter than "
                f"brightest measured reference inst_mag={brightest_reference_inst_mag:.3f}; "
                "the target is outside the empirically tested bright range"
            ),
            len(reference_rows),
            brightest_reference_inst_mag,
            target.catalog_mag,
            margin,
        )
    statistics = bright_end_residual_statistics(reference_rows)
    if statistics is None or len(reference_rows) < min_reference_count:
        return BrightLinearityAssessment(
            QUALITY_STATUS_OK,
            "OK",
            True,
            f"bright-end residual check skipped: only {len(reference_rows)} usable reference(s)",
            len(reference_rows),
            brightest_reference_inst_mag,
            target.catalog_mag,
            margin,
        )
    reference_count, bright_limit, bright_group_faint_limit, shift, uncertainty = statistics
    if float(target.inst_mag) > bright_group_faint_limit:
        return BrightLinearityAssessment(
            QUALITY_STATUS_OK,
            "OK",
            True,
            (
                f"target inst_mag={float(target.inst_mag):.3f} is fainter than the tested "
                f"bright group ending at inst_mag={bright_group_faint_limit:.3f}"
            ),
            reference_count,
            bright_limit,
            target.catalog_mag,
            margin,
        )
    effect = abs(shift)
    note = (
        f"bright-end median residual shift={shift:+.4f} mag, "
        f"robust uncertainty={uncertainty:.4f} mag, practical limit={effect_limit_mag:.3f} mag"
    )
    if effect <= effect_limit_mag:
        return BrightLinearityAssessment(
            QUALITY_STATUS_OK,
            "OK",
            True,
            note,
            reference_count,
            bright_limit,
            target.catalog_mag,
            margin,
        )
    lower_effect_bound = effect - confidence_sigma * uncertainty
    if lower_effect_bound <= effect_limit_mag:
        return BrightLinearityAssessment(
            QUALITY_STATUS_WARNING,
            "BRIGHT_LINEARITY_WARNING",
            True,
            f"{note}; effect above 10 mmag is not statistically secure",
            reference_count,
            bright_limit,
            target.catalog_mag,
            margin,
        )
    return BrightLinearityAssessment(
        QUALITY_STATUS_WARNING,
        "BRIGHT_LINEARITY_WARNING",
        True,
        (
            f"{note}; lower 95% effect bound={lower_effect_bound:.4f} mag exceeds 10 mmag; "
            "diagnostic warning only, without direct saturation or high-pixel evidence"
        ),
        reference_count,
        bright_limit,
        target.catalog_mag,
        margin,
    )


def most_severe_linearity_assessment(
    assessments: Iterable[BrightLinearityAssessment],
) -> BrightLinearityAssessment:
    """Return the most severe bright-linearity assessment."""

    ordered = list(assessments)
    severity = {
        QUALITY_STATUS_INVALID: 2,
        QUALITY_STATUS_WARNING: 1,
        QUALITY_STATUS_OK: 0,
    }
    if not ordered:
        return BrightLinearityAssessment(QUALITY_STATUS_OK, "OK", True, "", 0, None, None, None)
    return max(ordered, key=lambda item: severity.get(item.status, 0))


def apply_bright_linearity_assessment(
    target: ApertureMeasurement,
    assessment: BrightLinearityAssessment,
) -> ApertureMeasurement:
    """Return target measurement with a bright-linearity decision applied."""

    if assessment.flag == "OK":
        return target
    current_status = measurement_quality_status(target)
    if current_status == QUALITY_STATUS_INVALID:
        return replace(
            target,
            quality_flag=combine_quality_flag(target.quality_flag, assessment.flag),
            note=combine_quality_note(target.note, assessment.note),
        )
    status = (
        QUALITY_STATUS_INVALID
        if assessment.status == QUALITY_STATUS_INVALID
        else QUALITY_STATUS_WARNING
    )
    return replace(
        target,
        valid=assessment.valid,
        quality_status=status,
        quality_flag=combine_quality_flag(target.quality_flag, assessment.flag),
        note=combine_quality_note(target.note, assessment.note),
    )


def apply_frame_bright_linearity_check(
    measurements: list[ApertureMeasurement],
) -> tuple[list[ApertureMeasurement], BrightLinearityAssessment | None]:
    """Apply frame-local bright-linearity check to the target measurement."""

    target = next((item for item in measurements if item.role == "target"), None)
    if target is None:
        return measurements, None
    references = [
        item
        for item in measurements
        if item.role in {"comparison", "check"}
    ]
    assessment = most_severe_linearity_assessment(
        (assess_bright_instrumental_range(target, references),)
    )
    updated_target = apply_bright_linearity_assessment(target, assessment)
    if updated_target is target:
        return measurements, assessment
    return [
        updated_target if item is target else item
        for item in measurements
    ], assessment


def measure_series_sequence(
    sequence_files: list[Path],
    object_specs: list[dict[str, object]],
    aperture_settings: ApertureSettings,
    progress: Callable[[str], None] | None = None,
    event_pump: Callable[[], None] | None = None,
) -> tuple[list[ApertureMeasurement], Counter[str]]:
    """Measure a solved series with the same core path used by app and reference QC."""

    measurements: list[ApertureMeasurement] = []
    frame_linearity_counts: Counter[str] = Counter()
    for file_number, path in enumerate(sequence_files, start=1):
        frame_index = sequence_index(path)
        if frame_index is None:
            continue
        report_progress = (
            file_number == 1
            or file_number % 10 == 0
            or file_number == len(sequence_files)
        )
        if progress is not None and report_progress:
            progress(
                f"Photometry progress: frame {file_number}/{len(sequence_files)} "
                f"({path.name})."
            )
        if event_pump is not None and (report_progress or file_number % 5 == 0):
            event_pump()
        frame_measurements = aperture_measurements_for_frame(
            path,
            frame_index,
            object_specs,
            aperture_settings.aperture_radius_px,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
        )
        frame_measurements, frame_linearity = apply_frame_bright_linearity_check(
            frame_measurements
        )
        if frame_linearity is not None and frame_linearity.flag != "OK":
            frame_linearity_counts[frame_linearity.flag] += 1
            if progress is not None and frame_linearity_counts[frame_linearity.flag] <= 5:
                progress(
                    "Series frame bright-linearity check: "
                    f"{frame_linearity.status}/{frame_linearity.flag}; "
                    f"frame={frame_index}; {frame_linearity.note}."
                )
        measurements.extend(frame_measurements)
    return measurements, frame_linearity_counts


def read_loaded_photometry_frame(path: Path) -> tuple[fits.Header, np.ndarray, WCS, float]:
    """Read one FITS frame once for one or more aperture measurements."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        with fits.open(path) as hdul:
            header = hdul[0].header
            raw_data = np.asarray(hdul[0].data)
            saturation_threshold = saturation_threshold_for_data(raw_data)
            data = raw_data.astype(np.float64)
            wcs = WCS(header)
    return header, data, wcs, saturation_threshold


def aperture_read_error_measurement(
    path: Path,
    frame_index: int,
    object_id: str,
    role: str,
    ra_deg: float,
    dec_deg: float,
    catalog_mag: float | None,
    catalog_source: str = "",
    catalog_id: str = "",
    catalog_mag_error: float | None = None,
    catalog_nobs: int | None = None,
    aperture_radius: float = MANUAL_APERTURE_RADIUS_PX,
    annulus_inner: float = MANUAL_ANNULUS_INNER_PX,
    annulus_outer: float = MANUAL_ANNULUS_OUTER_PX,
    message: str = "",
    catalog_mag_b: float | None = None,
    catalog_mag_g: float | None = None,
    catalog_mag_r: float | None = None,
    catalog_b_minus_v: float | None = None,
    catalog_g_minus_r: float | None = None,
    image_source: str = "",
    aavso_filter: str = "",
) -> ApertureMeasurement:
    """Return a read-error measurement row for one object."""

    return ApertureMeasurement(
        frame_index,
        path.name,
        object_id,
        role,
        catalog_mag,
        catalog_source,
        catalog_id,
        catalog_mag_error,
        catalog_nobs,
        ra_deg,
        dec_deg,
        float("nan"),
        float("nan"),
        "",
        "",
        float("nan"),
        1.0,
        float("nan"),
        float("nan"),
        float("nan"),
        None,
        float("nan"),
        0,
        float("nan"),
        0,
        "READ_ERROR",
        False,
        f"read error: {message}" if message else "read error",
        catalog_mag_b=catalog_mag_b,
        catalog_mag_g=catalog_mag_g,
        catalog_mag_r=catalog_mag_r,
        catalog_b_minus_v=catalog_b_minus_v,
        catalog_g_minus_r=catalog_g_minus_r,
        image_source=image_source,
        aavso_filter=aavso_filter,
    )


def aperture_measurement_from_loaded_frame(
    path: Path,
    header: fits.Header,
    data: np.ndarray,
    wcs: WCS,
    saturation_threshold: float,
    frame_index: int,
    object_id: str,
    role: str,
    ra_deg: float,
    dec_deg: float,
    catalog_mag: float | None,
    catalog_source: str = "",
    catalog_id: str = "",
    catalog_mag_error: float | None = None,
    catalog_nobs: int | None = None,
    aperture_radius: float = MANUAL_APERTURE_RADIUS_PX,
    annulus_inner: float = MANUAL_ANNULUS_INNER_PX,
    annulus_outer: float = MANUAL_ANNULUS_OUTER_PX,
    catalog_mag_b: float | None = None,
    catalog_mag_g: float | None = None,
    catalog_mag_r: float | None = None,
    catalog_b_minus_v: float | None = None,
    catalog_g_minus_r: float | None = None,
    image_source: str = "",
    aavso_filter: str = "",
    annulus_exclusion_mask: np.ndarray | None = None,
) -> ApertureMeasurement:
    """Measure one object in an already-loaded FITS frame."""

    phot_time, phot_time_source, jd = parse_photometry_time_to_jd(header)
    exptime = read_exposure_seconds(header)
    try:
        x, y = wcs.world_to_pixel(SkyCoord(ra_deg * u.deg, dec_deg * u.deg, frame="icrs"))
        x = float(x)
        y = float(y)
    except Exception as exc:
        return ApertureMeasurement(
            frame_index,
            path.name,
            object_id,
            role,
            catalog_mag,
            catalog_source,
            catalog_id,
            catalog_mag_error,
            catalog_nobs,
            ra_deg,
            dec_deg,
            float("nan"),
            float("nan"),
            phot_time,
            phot_time_source,
            jd,
            exptime,
            float("nan"),
            float("nan"),
            float("nan"),
            None,
            float("nan"),
            0,
            float("nan"),
            0,
            "WCS_ERROR",
            False,
            f"WCS error: {exc}",
            image_source=image_source,
            aavso_filter=aavso_filter,
        )

    if not aperture_fits_inside_image(data.shape, x, y, annulus_outer):
        return ApertureMeasurement(
            frame_index,
            path.name,
            object_id,
            role,
            catalog_mag,
            catalog_source,
            catalog_id,
            catalog_mag_error,
            catalog_nobs,
            ra_deg,
            dec_deg,
            x,
            y,
            phot_time,
            phot_time_source,
            jd,
            exptime,
            float("nan"),
            float("nan"),
            float("nan"),
            None,
            float("nan"),
            0,
            float("nan"),
            0,
            "APERTURE_OUTSIDE_IMAGE",
            False,
            "aperture or annulus outside image",
            image_source=image_source,
            aavso_filter=aavso_filter,
        )

    cutout, local_x, local_y, cutout_x0, cutout_y0 = image_cutout_for_radius(
        data,
        x,
        y,
        annulus_outer,
    )
    ap_mask = circular_mask(cutout.shape, local_x, local_y, aperture_radius)
    bg_mask = annulus_mask(
        cutout.shape,
        local_x,
        local_y,
        annulus_inner,
        annulus_outer,
    )
    masked_pixel_count = 0
    masked_pixel_fraction = 0.0
    if annulus_exclusion_mask is not None:
        if annulus_exclusion_mask.shape != bg_mask.shape:
            raise ValueError("annulus exclusion mask does not match the photometry cutout")
        masked_pixel_count = int(np.count_nonzero(bg_mask & annulus_exclusion_mask))
        masked_pixel_fraction = masked_pixel_count / float(np.count_nonzero(bg_mask))
        bg_mask &= ~annulus_exclusion_mask
    ap_values = cutout[ap_mask]
    bg_values = cutout[bg_mask]
    ap_values = ap_values[np.isfinite(ap_values)]
    bg_values = bg_values[np.isfinite(bg_values)]
    if len(ap_values) == 0 or len(bg_values) == 0:
        return ApertureMeasurement(
            frame_index,
            path.name,
            object_id,
            role,
            catalog_mag,
            catalog_source,
            catalog_id,
            catalog_mag_error,
            catalog_nobs,
            ra_deg,
            dec_deg,
            x,
            y,
            phot_time,
            phot_time_source,
            jd,
            exptime,
            float("nan"),
            float("nan"),
            float("nan"),
            None,
            float("nan"),
            0,
            float("nan"),
            0,
            "EMPTY_APERTURE_OR_ANNULUS",
            False,
            "empty aperture or annulus",
            image_source=image_source,
            aavso_filter=aavso_filter,
        )

    aperture_max = float(np.max(ap_values))
    annulus_max = float(np.max(bg_values))
    aperture_satpix = saturation_count(ap_values, saturation_threshold)
    annulus_satpix = saturation_count(bg_values, saturation_threshold)
    aperture_highpix = saturation_count(ap_values, PHOTOMETRY_HIGH_PIXEL_ADU)
    annulus_highpix = saturation_count(bg_values, PHOTOMETRY_HIGH_PIXEL_ADU)
    aperture_sum = float(np.sum(ap_values))
    background = float(np.median(bg_values))
    net_flux = aperture_sum - background * float(len(ap_values))
    centroid_x, centroid_y, centroid_offset = aperture_centroid_offset(
        cutout.shape,
        local_x,
        local_y,
        aperture_radius,
        ap_values,
        background,
        (cutout_x0, cutout_y0),
    )
    background_sigma = robust_background_sigma(bg_values)
    annulus_contamination = annulus_contamination_problem_from_values(
        data,
        x,
        y,
        aperture_radius,
        annulus_inner,
        annulus_outer,
        ap_values,
        bg_values,
        background,
        background_sigma,
        SERIES_COMP_MAX_ANNULUS_PEAK_RATIO,
        SERIES_COMP_ANNULUS_CONTAMINATION_SIGMA,
        SERIES_COMP_ANNULUS_MIN_ISLAND_PIXELS,
        SERIES_COMP_ANNULUS_MAX_BRIGHT_ISLANDS,
    )
    (
        annulus_clean_background,
        annulus_delta_inst_mag,
        annulus_rejected_count,
        annulus_rejected_fraction,
    ) = annulus_contamination_impact(
        bg_values,
        background,
        aperture_sum,
        len(ap_values),
        net_flux,
        exptime,
    ) if annulus_contamination else (float("nan"), float("nan"), 0, 0.0)
    flux_error = aperture_flux_uncertainty(
        net_flux,
        background_sigma,
        len(ap_values),
        len(bg_values),
    )
    snr = net_flux / flux_error if flux_error > 0 and np.isfinite(flux_error) else float("nan")
    inst_mag_error = (
        float(2.5 / np.log(10.0) / snr)
        if snr > 0 and np.isfinite(snr)
        else None
    )
    quality = evaluate_photometry_quality(
        role=role,
        aperture_satpix=aperture_satpix,
        annulus_satpix=annulus_satpix,
        aperture_highpix=aperture_highpix,
        annulus_highpix=annulus_highpix,
        annulus_contamination=annulus_contamination,
        annulus_delta_inst_mag=annulus_delta_inst_mag,
        net_flux=net_flux,
        snr=snr,
        inst_mag_error=inst_mag_error,
        centroid_offset=centroid_offset,
        aperture_radius=aperture_radius,
    )
    if masked_pixel_count and quality.valid:
        quality = PhotometryQualityDecision(
            QUALITY_STATUS_WARNING,
            "BACKGROUND_MASKED",
            True,
            (
                "bright annulus source region(s) masked after user confirmation; "
                f"{masked_pixel_count} pixel(s) ({masked_pixel_fraction:.1%}) excluded"
            ),
        )
    if quality.valid:
        inst_mag = float(-2.5 * np.log10(net_flux / exptime))
    else:
        inst_mag = None

    return ApertureMeasurement(
        frame_index,
        path.name,
        object_id,
        role,
        catalog_mag,
        catalog_source,
        catalog_id,
        catalog_mag_error,
        catalog_nobs,
        ra_deg,
        dec_deg,
        x,
        y,
        phot_time,
        phot_time_source,
        jd,
        exptime,
        aperture_sum,
        background,
        net_flux,
        inst_mag,
        aperture_max,
        aperture_satpix,
        annulus_max,
        annulus_satpix,
        quality.flag,
        quality.valid,
        quality.note,
        aperture_highpix=aperture_highpix,
        annulus_highpix=annulus_highpix,
        annulus_contamination=annulus_contamination,
        annulus_clean_background_median=annulus_clean_background,
        annulus_delta_inst_mag=annulus_delta_inst_mag,
        annulus_rejected_pixel_count=annulus_rejected_count,
        annulus_rejected_pixel_fraction=annulus_rejected_fraction,
        annulus_masked=bool(masked_pixel_count),
        annulus_masked_pixel_count=masked_pixel_count,
        annulus_masked_pixel_fraction=masked_pixel_fraction,
        flux_error=flux_error,
        snr=snr,
        inst_mag_error=inst_mag_error,
        centroid_x=centroid_x,
        centroid_y=centroid_y,
        centroid_offset_px=centroid_offset,
        catalog_mag_b=catalog_mag_b,
        catalog_mag_g=catalog_mag_g,
        catalog_mag_r=catalog_mag_r,
        catalog_b_minus_v=catalog_b_minus_v,
        catalog_g_minus_r=catalog_g_minus_r,
        image_source=image_source,
        aavso_filter=aavso_filter,
        quality_status=quality.status,
    )


def aperture_measurement(
    path: Path,
    frame_index: int,
    object_id: str,
    role: str,
    ra_deg: float,
    dec_deg: float,
    catalog_mag: float | None,
    catalog_source: str = "",
    catalog_id: str = "",
    catalog_mag_error: float | None = None,
    catalog_nobs: int | None = None,
    aperture_radius: float = MANUAL_APERTURE_RADIUS_PX,
    annulus_inner: float = MANUAL_ANNULUS_INNER_PX,
    annulus_outer: float = MANUAL_ANNULUS_OUTER_PX,
    catalog_mag_b: float | None = None,
    catalog_mag_g: float | None = None,
    catalog_mag_r: float | None = None,
    catalog_b_minus_v: float | None = None,
    catalog_g_minus_r: float | None = None,
    image_source: str = "",
    aavso_filter: str = "",
) -> ApertureMeasurement:
    """Measure one object in one FITS frame using WCS-based aperture photometry."""

    try:
        header, data, wcs, saturation_threshold = read_loaded_photometry_frame(path)
    except Exception as exc:
        return aperture_read_error_measurement(
            path,
            frame_index,
            object_id,
            role,
            ra_deg,
            dec_deg,
            catalog_mag,
            catalog_source,
            catalog_id,
            catalog_mag_error,
            catalog_nobs,
            aperture_radius,
            annulus_inner,
            annulus_outer,
            str(exc),
            catalog_mag_b,
            catalog_mag_g,
            catalog_mag_r,
            catalog_b_minus_v,
            catalog_g_minus_r,
            image_source,
            aavso_filter,
        )
    return aperture_measurement_from_loaded_frame(
        path,
        header,
        data,
        wcs,
        saturation_threshold,
        frame_index,
        object_id,
        role,
        ra_deg,
        dec_deg,
        catalog_mag,
        catalog_source,
        catalog_id,
        catalog_mag_error,
        catalog_nobs,
        aperture_radius,
        annulus_inner,
        annulus_outer,
        catalog_mag_b,
        catalog_mag_g,
        catalog_mag_r,
        catalog_b_minus_v,
        catalog_g_minus_r,
        image_source,
        aavso_filter,
    )


def aperture_measurements_from_loaded_frame(
    path: Path,
    frame_index: int,
    header: fits.Header,
    data: np.ndarray,
    wcs: WCS,
    saturation_threshold: float,
    object_specs: list[dict[str, object]],
    aperture_radius: float = MANUAL_APERTURE_RADIUS_PX,
    annulus_inner: float = MANUAL_ANNULUS_INNER_PX,
    annulus_outer: float = MANUAL_ANNULUS_OUTER_PX,
    annulus_exclusion_masks: dict[str, np.ndarray] | None = None,
) -> list[ApertureMeasurement]:
    """Measure multiple objects from one already loaded FITS frame."""

    return [
        aperture_measurement_from_loaded_frame(
            path,
            header,
            data,
            wcs,
            saturation_threshold,
            frame_index,
            str(spec.get("object_id", "")),
            str(spec.get("role", "")),
            float(spec.get("ra_deg", float("nan"))),
            float(spec.get("dec_deg", float("nan"))),
            spec.get("catalog_mag") if isinstance(spec.get("catalog_mag"), float) else None,
            str(spec.get("catalog_source", "")),
            str(spec.get("catalog_id", "")),
            spec.get("catalog_mag_error") if isinstance(spec.get("catalog_mag_error"), float) else None,
            spec.get("catalog_nobs") if isinstance(spec.get("catalog_nobs"), int) else None,
            aperture_radius,
            annulus_inner,
            annulus_outer,
            spec.get("catalog_mag_b") if isinstance(spec.get("catalog_mag_b"), float) else None,
            spec.get("catalog_mag_g") if isinstance(spec.get("catalog_mag_g"), float) else None,
            spec.get("catalog_mag_r") if isinstance(spec.get("catalog_mag_r"), float) else None,
            spec.get("catalog_b_minus_v") if isinstance(spec.get("catalog_b_minus_v"), float) else None,
            spec.get("catalog_g_minus_r") if isinstance(spec.get("catalog_g_minus_r"), float) else None,
            str(spec.get("image_source", "")),
            str(spec.get("aavso_filter", "")),
            None if annulus_exclusion_masks is None else annulus_exclusion_masks.get(str(spec.get("object_id", ""))),
        )
        for spec in object_specs
    ]


def aperture_measurements_for_frame(
    path: Path,
    frame_index: int,
    object_specs: list[dict[str, object]],
    aperture_radius: float = MANUAL_APERTURE_RADIUS_PX,
    annulus_inner: float = MANUAL_ANNULUS_INNER_PX,
    annulus_outer: float = MANUAL_ANNULUS_OUTER_PX,
    annulus_exclusion_masks: dict[str, np.ndarray] | None = None,
) -> list[ApertureMeasurement]:
    """Measure multiple objects from one FITS frame with a single file read."""

    try:
        header, data, wcs, saturation_threshold = read_loaded_photometry_frame(path)
    except Exception as exc:
        return [
            aperture_read_error_measurement(
                path,
                frame_index,
                str(spec.get("object_id", "")),
                str(spec.get("role", "")),
                float(spec.get("ra_deg", float("nan"))),
                float(spec.get("dec_deg", float("nan"))),
                spec.get("catalog_mag") if isinstance(spec.get("catalog_mag"), float) else None,
                str(spec.get("catalog_source", "")),
                str(spec.get("catalog_id", "")),
                spec.get("catalog_mag_error") if isinstance(spec.get("catalog_mag_error"), float) else None,
                spec.get("catalog_nobs") if isinstance(spec.get("catalog_nobs"), int) else None,
                aperture_radius,
                annulus_inner,
                annulus_outer,
                str(exc),
                spec.get("catalog_mag_b") if isinstance(spec.get("catalog_mag_b"), float) else None,
                spec.get("catalog_mag_g") if isinstance(spec.get("catalog_mag_g"), float) else None,
                spec.get("catalog_mag_r") if isinstance(spec.get("catalog_mag_r"), float) else None,
                spec.get("catalog_b_minus_v") if isinstance(spec.get("catalog_b_minus_v"), float) else None,
                spec.get("catalog_g_minus_r") if isinstance(spec.get("catalog_g_minus_r"), float) else None,
                str(spec.get("image_source", "")),
                str(spec.get("aavso_filter", "")),
            )
            for spec in object_specs
        ]

    return aperture_measurements_from_loaded_frame(
        path,
        frame_index,
        header,
        data,
        wcs,
        saturation_threshold,
        object_specs,
        aperture_radius,
        annulus_inner,
        annulus_outer,
        annulus_exclusion_masks,
    )


def write_instrumental_photometry(
    path: Path,
    measurements: list[ApertureMeasurement],
    metadata: dict[str, object] | None = None,
) -> None:
    """Write raw instrumental photometry measurements to CSV."""

    fieldnames = [
        "frame_index",
        "filename",
        "image_source",
        "aavso_filter",
        "object_id",
        "role",
        "catalog_mag",
        "catalog_source",
        "catalog_id",
        "catalog_mag_error",
        "catalog_nobs",
        "catalog_mag_b",
        "catalog_mag_g",
        "catalog_mag_r",
        "catalog_b_minus_v",
        "catalog_g_minus_r",
        "ra_deg",
        "dec_deg",
        "x",
        "y",
        "centroid_x",
        "centroid_y",
        "centroid_offset_px",
        "phot_time",
        "phot_time_source",
        "jd",
        "exptime",
        "aperture_sum",
        "background_median",
        "net_flux",
        "flux_error",
        "snr",
        "inst_mag",
        "inst_mag_error",
        "aperture_max",
        "aperture_satpix",
        "aperture_highpix",
        "annulus_max",
        "annulus_satpix",
        "annulus_highpix",
        "annulus_contamination",
        "annulus_clean_background_median",
        "annulus_delta_inst_mag",
        "annulus_rejected_pixel_count",
        "annulus_rejected_pixel_fraction",
        "annulus_masked",
        "annulus_masked_pixel_count",
        "annulus_masked_pixel_fraction",
        "quality_status",
        "quality_flag",
        "valid",
        "note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        if metadata:
            write_result_metadata_header(fh, metadata)
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in measurements:
            writer.writerow(
                {
                    "frame_index": item.frame_index,
                    "filename": item.filename,
                    "image_source": item.image_source,
                    "aavso_filter": item.aavso_filter,
                    "object_id": item.object_id,
                    "role": item.role,
                    "catalog_mag": "" if item.catalog_mag is None else f"{item.catalog_mag:.6f}",
                    "catalog_source": item.catalog_source,
                    "catalog_id": item.catalog_id,
                    "catalog_mag_error": (
                        ""
                        if item.catalog_mag_error is None
                        else f"{item.catalog_mag_error:.6f}"
                    ),
                    "catalog_nobs": "" if item.catalog_nobs is None else item.catalog_nobs,
                    "catalog_mag_b": "" if item.catalog_mag_b is None else f"{item.catalog_mag_b:.6f}",
                    "catalog_mag_g": "" if item.catalog_mag_g is None else f"{item.catalog_mag_g:.6f}",
                    "catalog_mag_r": "" if item.catalog_mag_r is None else f"{item.catalog_mag_r:.6f}",
                    "catalog_b_minus_v": (
                        "" if item.catalog_b_minus_v is None else f"{item.catalog_b_minus_v:.6f}"
                    ),
                    "catalog_g_minus_r": (
                        "" if item.catalog_g_minus_r is None else f"{item.catalog_g_minus_r:.6f}"
                    ),
                    "ra_deg": f"{item.ra_deg:.8f}",
                    "dec_deg": f"{item.dec_deg:.8f}",
                    "x": f"{item.x:.3f}",
                    "y": f"{item.y:.3f}",
                    "centroid_x": (
                        f"{item.centroid_x:.3f}" if np.isfinite(item.centroid_x) else ""
                    ),
                    "centroid_y": (
                        f"{item.centroid_y:.3f}" if np.isfinite(item.centroid_y) else ""
                    ),
                    "centroid_offset_px": (
                        f"{item.centroid_offset_px:.3f}"
                        if np.isfinite(item.centroid_offset_px)
                        else ""
                    ),
                    "phot_time": item.phot_time,
                    "phot_time_source": item.phot_time_source,
                    "jd": f"{item.jd:.8f}" if np.isfinite(item.jd) else "",
                    "exptime": f"{item.exptime:.6f}",
                    "aperture_sum": f"{item.aperture_sum:.6f}" if np.isfinite(item.aperture_sum) else "",
                    "background_median": f"{item.background_median:.6f}" if np.isfinite(item.background_median) else "",
                    "net_flux": f"{item.net_flux:.6f}" if np.isfinite(item.net_flux) else "",
                    "flux_error": f"{item.flux_error:.6f}" if np.isfinite(item.flux_error) else "",
                    "snr": f"{item.snr:.6f}" if np.isfinite(item.snr) else "",
                    "inst_mag": "" if item.inst_mag is None else f"{item.inst_mag:.6f}",
                    "inst_mag_error": (
                        ""
                        if item.inst_mag_error is None
                        else f"{item.inst_mag_error:.6f}"
                    ),
                    "aperture_max": f"{item.aperture_max:.6f}" if np.isfinite(item.aperture_max) else "",
                    "aperture_satpix": item.aperture_satpix,
                    "aperture_highpix": item.aperture_highpix,
                    "annulus_max": f"{item.annulus_max:.6f}" if np.isfinite(item.annulus_max) else "",
                    "annulus_satpix": item.annulus_satpix,
                    "annulus_highpix": item.annulus_highpix,
                    "annulus_contamination": item.annulus_contamination,
                    "annulus_clean_background_median": (
                        f"{item.annulus_clean_background_median:.6f}"
                        if np.isfinite(item.annulus_clean_background_median)
                        else ""
                    ),
                    "annulus_delta_inst_mag": (
                        f"{item.annulus_delta_inst_mag:.10f}"
                        if np.isfinite(item.annulus_delta_inst_mag)
                        else ""
                    ),
                    "annulus_rejected_pixel_count": item.annulus_rejected_pixel_count,
                    "annulus_rejected_pixel_fraction": (
                        f"{item.annulus_rejected_pixel_fraction:.10f}"
                        if np.isfinite(item.annulus_rejected_pixel_fraction)
                        else ""
                    ),
                    "annulus_masked": "1" if item.annulus_masked else "0",
                    "annulus_masked_pixel_count": item.annulus_masked_pixel_count,
                    "annulus_masked_pixel_fraction": (
                        f"{item.annulus_masked_pixel_fraction:.10f}"
                        if np.isfinite(item.annulus_masked_pixel_fraction)
                        else ""
                    ),
                    "quality_status": measurement_quality_status(item),
                    "quality_flag": item.quality_flag,
                    "valid": "1" if item.valid else "0",
                    "note": item.note,
                }
            )


def dynamic_zero_point_scatter_limit(scatter_values: list[float]) -> tuple[float, float, float] | None:
    """Return a robust per-run scatter limit from comparison-star zero-point scatter."""

    values = np.array(
        [value for value in scatter_values if np.isfinite(value)],
        dtype=np.float64,
    )
    if len(values) < MIN_ZERO_POINT_SCATTER_FILTER_FRAMES:
        return None

    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    robust_sigma = 1.4826 * mad
    if not np.isfinite(robust_sigma) or robust_sigma <= 0:
        q1, q3 = np.percentile(values, [25, 75])
        iqr = float(q3 - q1)
        if not np.isfinite(iqr) or iqr <= 0:
            return None
        robust_sigma = iqr / 1.349

    return (
        median + ZERO_POINT_SCATTER_OUTLIER_SIGMA * robust_sigma,
        median,
        robust_sigma,
    )


def write_calibrated_light_curve(
    path: Path,
    measurements: list[ApertureMeasurement],
    metadata: dict[str, object],
    min_valid_comps: int = MIN_VALID_COMP_STARS,
    *,
    result_rows_valid: bool = True,
) -> CalibrationWriteStats:
    """Write calibrated target magnitudes from instrumental measurements."""

    by_frame: dict[int, list[ApertureMeasurement]] = {}
    for item in measurements:
        by_frame.setdefault(item.frame_index, []).append(item)

    fieldnames = [
        "frame_index",
        "filename",
        "image_source",
        "aavso_filter",
        "phot_time",
        "phot_time_source",
        "jd",
        "target_inst_mag",
        "target_snr",
        "target_inst_mag_error",
        "target_quality_status",
        "target_quality_flag",
        "target_quality_note",
        "target_aperture_satpix",
        "target_aperture_highpix",
        "target_annulus_satpix",
        "target_annulus_highpix",
        "target_annulus_contamination",
        "target_annulus_delta_inst_mag",
        "target_annulus_rejected_pixel_count",
        "zero_point",
        "target_calibrated_mag",
        "target_calibrated_mag_error",
        "comparison_catalog_sources",
        "comparison_catalog_ids",
        "comparison_catalog_mags",
        "comparison_catalog_mag_errors",
        "comparison_catalog_nobs",
        "comparison_catalog_b_minus_v",
        "comparison_catalog_g_minus_r",
        "check_object_id",
        "check_catalog_source",
        "check_catalog_id",
        "check_inst_mag",
        "check_snr",
        "check_inst_mag_error",
        "check_catalog_mag",
        "check_catalog_mag_error",
        "check_catalog_nobs",
        "check_catalog_b_minus_v",
        "check_catalog_g_minus_r",
        "check_calibrated_mag",
        "check_delta_mag",
        "check_quality_status",
        "check_quality_flag",
        "valid_comp_count",
        "comp_zero_point_scatter",
        "comp_zero_point_error",
        "valid",
        "quality_status",
        "quality_flag",
        "quality_note",
    ]
    pending_rows: list[tuple[float, dict[str, str | int]]] = []
    scatter_values: list[float] = []
    for frame_index in sorted(by_frame):
        frame_items = by_frame[frame_index]
        target_items = [item for item in frame_items if item.role == "target" and item.valid]
        comp_items = [
            item
            for item in frame_items
            if item.role == "comparison"
            and item.valid
            and item.catalog_mag is not None
            and item.inst_mag is not None
        ]
        check_items = [
            item
            for item in frame_items
            if item.role == "check"
        ]
        if not target_items or len(comp_items) < min_valid_comps:
            continue
        target = target_items[0]
        zero_points = np.array(
            [float(item.catalog_mag) - float(item.inst_mag) for item in comp_items],
            dtype=np.float64,
        )
        zero_point = float(np.median(zero_points))
        scatter = float(np.std(zero_points, ddof=1)) if len(zero_points) > 1 else 0.0
        standard_error = scatter / float(np.sqrt(len(zero_points))) if len(zero_points) > 0 else 0.0
        calibrated_mag = float(target.inst_mag) + zero_point
        target_calibrated_mag_error = (
            float(
                np.sqrt(
                    (target.inst_mag_error or 0.0) ** 2
                    + standard_error**2
                )
            )
            if target.inst_mag_error is not None and np.isfinite(standard_error)
            else None
        )
        check = check_items[0] if check_items else None
        check_calibrated_mag = (
            float(check.inst_mag) + zero_point
            if check is not None and check.valid and check.inst_mag is not None
            else None
        )
        check_delta_mag = (
            check_calibrated_mag - float(check.catalog_mag)
            if check_calibrated_mag is not None and check.catalog_mag is not None
            else None
        )
        scatter_values.append(scatter)
        pending_rows.append(
            (
                scatter,
                {
                    "frame_index": frame_index,
                    "filename": target.filename,
                    "image_source": target.image_source,
                    "aavso_filter": target.aavso_filter,
                    "phot_time": target.phot_time,
                    "phot_time_source": target.phot_time_source,
                    "jd": f"{target.jd:.8f}" if np.isfinite(target.jd) else "",
                    "target_inst_mag": f"{target.inst_mag:.6f}",
                    "target_snr": f"{target.snr:.6f}" if np.isfinite(target.snr) else "",
                    "target_inst_mag_error": (
                        ""
                        if target.inst_mag_error is None
                        else f"{target.inst_mag_error:.6f}"
                    ),
                    "target_quality_status": measurement_quality_status(target),
                    "target_quality_flag": target.quality_flag,
                    "target_quality_note": target.note,
                    "target_aperture_satpix": target.aperture_satpix,
                    "target_aperture_highpix": target.aperture_highpix,
                    "target_annulus_satpix": target.annulus_satpix,
                    "target_annulus_highpix": target.annulus_highpix,
                    "target_annulus_contamination": target.annulus_contamination,
                    "target_annulus_delta_inst_mag": (
                        f"{target.annulus_delta_inst_mag:.10f}"
                        if np.isfinite(target.annulus_delta_inst_mag)
                        else ""
                    ),
                    "target_annulus_rejected_pixel_count": target.annulus_rejected_pixel_count,
                    "zero_point": f"{zero_point:.6f}",
                    "target_calibrated_mag": f"{calibrated_mag:.6f}",
                    "target_calibrated_mag_error": (
                        ""
                        if target_calibrated_mag_error is None
                        else f"{target_calibrated_mag_error:.6f}"
                    ),
                    "comparison_catalog_sources": ";".join(
                        item.catalog_source for item in comp_items
                    ),
                    "comparison_catalog_ids": ";".join(
                        item.catalog_id or item.object_id for item in comp_items
                    ),
                    "comparison_catalog_mags": ";".join(
                        "" if item.catalog_mag is None else f"{item.catalog_mag:.6f}"
                        for item in comp_items
                    ),
                    "comparison_catalog_mag_errors": ";".join(
                        "" if item.catalog_mag_error is None else f"{item.catalog_mag_error:.6f}"
                        for item in comp_items
                    ),
                    "comparison_catalog_nobs": ";".join(
                        "" if item.catalog_nobs is None else str(item.catalog_nobs)
                        for item in comp_items
                    ),
                    "comparison_catalog_b_minus_v": ";".join(
                        "" if item.catalog_b_minus_v is None else f"{item.catalog_b_minus_v:.6f}"
                        for item in comp_items
                    ),
                    "comparison_catalog_g_minus_r": ";".join(
                        "" if item.catalog_g_minus_r is None else f"{item.catalog_g_minus_r:.6f}"
                        for item in comp_items
                    ),
                    "check_object_id": "" if check is None else check.object_id,
                    "check_catalog_source": "" if check is None else check.catalog_source,
                    "check_catalog_id": "" if check is None else check.catalog_id,
                    "check_inst_mag": (
                        ""
                        if check is None or check.inst_mag is None
                        else f"{check.inst_mag:.6f}"
                    ),
                    "check_snr": (
                        ""
                        if check is None or not np.isfinite(check.snr)
                        else f"{check.snr:.6f}"
                    ),
                    "check_inst_mag_error": (
                        ""
                        if check is None or check.inst_mag_error is None
                        else f"{check.inst_mag_error:.6f}"
                    ),
                    "check_catalog_mag": (
                        ""
                        if check is None or check.catalog_mag is None
                        else f"{check.catalog_mag:.6f}"
                    ),
                    "check_catalog_mag_error": (
                        ""
                        if check is None or check.catalog_mag_error is None
                        else f"{check.catalog_mag_error:.6f}"
                    ),
                    "check_catalog_nobs": (
                        "" if check is None or check.catalog_nobs is None else check.catalog_nobs
                    ),
                    "check_catalog_b_minus_v": (
                        ""
                        if check is None or check.catalog_b_minus_v is None
                        else f"{check.catalog_b_minus_v:.6f}"
                    ),
                    "check_catalog_g_minus_r": (
                        ""
                        if check is None or check.catalog_g_minus_r is None
                        else f"{check.catalog_g_minus_r:.6f}"
                    ),
                    "check_calibrated_mag": (
                        ""
                        if check_calibrated_mag is None
                        else f"{check_calibrated_mag:.6f}"
                    ),
                    "check_delta_mag": (
                        ""
                        if check_delta_mag is None
                        else f"{check_delta_mag:.6f}"
                    ),
                    "check_quality_flag": "" if check is None else check.quality_flag,
                    "check_quality_status": "" if check is None else measurement_quality_status(check),
                    "valid_comp_count": len(comp_items),
                    "comp_zero_point_scatter": f"{scatter:.6f}",
                    "comp_zero_point_error": f"{standard_error:.6f}",
                    "valid": "1" if result_rows_valid else "0",
                    "quality_status": measurement_quality_status(target),
                    "quality_flag": target.quality_flag,
                    "quality_note": target.note,
                },
            )
        )

    scatter_limit_info = dynamic_zero_point_scatter_limit(scatter_values)
    scatter_limit = scatter_limit_info[0] if scatter_limit_info is not None else None
    median_scatter = scatter_limit_info[1] if scatter_limit_info is not None else None
    robust_scatter_sigma = scatter_limit_info[2] if scatter_limit_info is not None else None

    rejected_high_scatter = 0
    rejected_frame_indices: list[int] = []
    accepted_rows: list[dict[str, object]] = []
    for scatter, row in pending_rows:
        if scatter_limit is not None and scatter > scatter_limit:
            rejected_high_scatter += 1
            rejected_frame_indices.append(int(row["frame_index"]))
            continue
        accepted_rows.append(row)

    final_metadata = augment_result_metadata_from_rows(metadata, accepted_rows)
    final_metadata["ZERO_POINT_SCATTER_REJECTED_FRAME_INDICES"] = ",".join(
        str(index) for index in rejected_frame_indices
    )
    with path.open("w", newline="") as fh:
        write_result_metadata_header(fh, final_metadata)
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in accepted_rows:
            writer.writerow(row)
    return CalibrationWriteStats(
        rows_written=len(accepted_rows),
        rejected_high_scatter=rejected_high_scatter,
        scatter_limit=scatter_limit,
        median_scatter=median_scatter,
        robust_scatter_sigma=robust_scatter_sigma,
    )


def median_zero_point_scatter(path: Path) -> float | None:
    """Return the median zero-point scatter from a generated result_curve.csv."""

    if not path.exists():
        return None
    values: list[float] = []
    with path.open(newline="") as fh:
        reader = csv_data_dict_reader(fh)
        for row in reader:
            try:
                value = float(row.get("comp_zero_point_scatter", ""))
            except (TypeError, ValueError):
                continue
            if np.isfinite(value):
                values.append(value)
    if not values:
        return None
    return float(np.median(np.array(values, dtype=np.float64)))


def check_star_summary_lines(path: Path) -> list[str]:
    """Return compact check-star residual summary lines from a result CSV."""

    if not path.exists():
        return []
    values: list[float] = []
    object_id = ""
    with path.open(newline="") as fh:
        reader = csv_data_dict_reader(fh)
        for row in reader:
            object_id = row.get("check_object_id", object_id) or object_id
            try:
                value = float(row.get("check_delta_mag", ""))
            except (TypeError, ValueError):
                continue
            if np.isfinite(value):
                values.append(value)
    if not values:
        return ["Check Star: no valid calibrated check measurements."]

    data = np.array(values, dtype=np.float64)
    median = float(np.median(data))
    robust_scatter = 1.4826 * float(np.median(np.abs(data - median)))
    rms = float(np.sqrt(np.mean((data - median) ** 2)))
    label = object_id or "check"
    return [
        (
            f"Check Star {label}: n={len(values)}, median delta={median:.4f} mag, "
            f"robust scatter={robust_scatter:.4f} mag, rms={rms:.4f} mag."
        )
    ]


def write_single_field_zp_measurement_csv(
    path: Path,
    metadata: dict[str, object],
    target: ApertureMeasurement,
    check: ApertureMeasurement | None,
    fit: SingleFieldZpFit,
    calibrated_mag: float,
    calibrated_error: float,
    check_calibrated_mag: float | None,
    check_delta_mag: float | None,
) -> None:
    """Write one single-image result calibrated by field-wide catalog ZP."""

    field_catalog_sources = str(metadata.get("FIELD_ZP_CATALOG_SOURCES", "")).strip()

    fieldnames = [
        "filename",
        "image_source",
        "aavso_filter",
        "phot_time",
        "phot_time_source",
        "jd",
        "target_id",
        "target_inst_mag",
        "target_snr",
        "target_inst_mag_error",
        "target_aperture_satpix",
        "target_aperture_highpix",
        "target_annulus_satpix",
        "target_annulus_highpix",
        "target_annulus_contamination",
        "target_annulus_delta_inst_mag",
        "target_annulus_rejected_pixel_count",
        "target_annulus_masked",
        "target_annulus_masked_pixel_count",
        "target_annulus_masked_pixel_fraction",
        "calibrated_mag",
        "calibrated_mag_error",
        "calibration_method",
        "field_zero_point",
        "field_zero_point_scatter",
        "field_zero_point_error",
        "field_reference_measured",
        "field_reference_usable",
        "field_reference_used",
        "field_reference_clipped",
        "field_catalog_sources",
        "check_catalog_source",
        "check_catalog_id",
        "check_catalog_mag",
        "check_inst_mag",
        "check_snr",
        "check_calibrated_mag",
        "check_delta_mag",
        "check_quality_status",
        "check_quality_flag",
        "valid",
        "quality_status",
        "quality_flag",
        "quality_note",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        for key, value in metadata.items():
            fh.write(f"# {key}={value}\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "filename": target.filename,
                "image_source": target.image_source,
                "aavso_filter": target.aavso_filter,
                "phot_time": target.phot_time,
                "phot_time_source": target.phot_time_source,
                "jd": format_csv_float(target.jd),
                "target_id": target.object_id,
                "target_inst_mag": format_csv_float(target.inst_mag),
                "target_snr": format_csv_float(target.snr),
                "target_inst_mag_error": format_csv_float(target.inst_mag_error),
                "target_aperture_satpix": target.aperture_satpix,
                "target_aperture_highpix": target.aperture_highpix,
                "target_annulus_satpix": target.annulus_satpix,
                "target_annulus_highpix": target.annulus_highpix,
                "target_annulus_contamination": target.annulus_contamination,
                "target_annulus_delta_inst_mag": format_csv_float(target.annulus_delta_inst_mag, 10),
                "target_annulus_rejected_pixel_count": target.annulus_rejected_pixel_count,
                "target_annulus_masked": int(target.annulus_masked),
                "target_annulus_masked_pixel_count": target.annulus_masked_pixel_count,
                "target_annulus_masked_pixel_fraction": format_csv_float(
                    target.annulus_masked_pixel_fraction,
                    10,
                ),
                "calibrated_mag": format_csv_float(calibrated_mag),
                "calibrated_mag_error": format_csv_float(calibrated_error),
                "calibration_method": SINGLE_FIELD_ZP_METHOD_ID,
                "field_zero_point": format_csv_float(fit.zero_point),
                "field_zero_point_scatter": format_csv_float(fit.scatter),
                "field_zero_point_error": format_csv_float(fit.zero_point_error),
                "field_reference_measured": fit.measured_count,
                "field_reference_usable": fit.usable_count,
                "field_reference_used": fit.used_count,
                "field_reference_clipped": fit.rejected_clip_count,
                "field_catalog_sources": field_catalog_sources,
                "check_catalog_source": "" if check is None else check.catalog_source,
                "check_catalog_id": "" if check is None else check.catalog_id,
                "check_catalog_mag": "" if check is None else format_csv_float(check.catalog_mag),
                "check_inst_mag": "" if check is None else format_csv_float(check.inst_mag),
                "check_snr": "" if check is None else format_csv_float(check.snr),
                "check_calibrated_mag": format_csv_float(check_calibrated_mag),
                "check_delta_mag": format_csv_float(check_delta_mag),
                "check_quality_status": "" if check is None else measurement_quality_status(check),
                "check_quality_flag": "" if check is None else check.quality_flag,
                "valid": int(target.valid),
                "quality_status": measurement_quality_status(target),
                "quality_flag": target.quality_flag,
                "quality_note": target.note,
            }
        )


def write_single_field_zp_references_csv(
    path: Path,
    metadata: dict[str, object],
    references: list[SingleFieldZpReference],
    fit: SingleFieldZpFit,
) -> None:
    """Write field-wide catalog reference measurements for a single target."""

    fieldnames = [
        "object_id",
        "catalog_source",
        "catalog_mag",
        "catalog_mag_error",
        "catalog_nobs",
        "catalog_b_minus_v",
        "catalog_g_minus_r",
        "ra_deg",
        "dec_deg",
        "x",
        "y",
        "inst_mag",
        "snr",
        "inst_mag_error",
        "aperture_satpix",
        "aperture_highpix",
        "annulus_satpix",
        "annulus_highpix",
        "annulus_contamination",
        "annulus_delta_inst_mag",
        "annulus_rejected_pixel_count",
        "reference_zero_point",
        "residual",
        "fit_candidate",
        "used_in_fit",
        "quality_status",
        "quality_flag",
        "reject_reason",
    ]
    used_ids = set(fit.used_object_ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        for key, value in metadata.items():
            fh.write(f"# {key}={value}\n")
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for reference in references:
            measurement = reference.measurement
            reference_zp = reference.zero_point()
            residual = (
                reference_zp - fit.zero_point
                if reference_zp is not None and fit.zero_point is not None
                else None
            )
            writer.writerow(
                {
                    "object_id": measurement.object_id,
                    "catalog_source": measurement.catalog_source,
                    "catalog_mag": format_csv_float(measurement.catalog_mag),
                    "catalog_mag_error": format_csv_float(measurement.catalog_mag_error),
                    "catalog_nobs": "" if measurement.catalog_nobs is None else measurement.catalog_nobs,
                    "catalog_b_minus_v": format_csv_float(measurement.catalog_b_minus_v),
                    "catalog_g_minus_r": format_csv_float(measurement.catalog_g_minus_r),
                    "ra_deg": format_csv_float(measurement.ra_deg),
                    "dec_deg": format_csv_float(measurement.dec_deg),
                    "x": format_csv_float(measurement.x),
                    "y": format_csv_float(measurement.y),
                    "inst_mag": format_csv_float(measurement.inst_mag),
                    "snr": format_csv_float(measurement.snr),
                    "inst_mag_error": format_csv_float(measurement.inst_mag_error),
                    "aperture_satpix": measurement.aperture_satpix,
                    "aperture_highpix": measurement.aperture_highpix,
                    "annulus_satpix": measurement.annulus_satpix,
                    "annulus_highpix": measurement.annulus_highpix,
                    "annulus_contamination": measurement.annulus_contamination,
                    "annulus_delta_inst_mag": format_csv_float(
                        measurement.annulus_delta_inst_mag,
                        10,
                    ),
                    "annulus_rejected_pixel_count": measurement.annulus_rejected_pixel_count,
                    "reference_zero_point": format_csv_float(reference_zp),
                    "residual": format_csv_float(residual),
                    "fit_candidate": "yes" if reference.used_for_fit else "no",
                    "used_in_fit": "yes" if measurement.object_id in used_ids else "no",
                    "quality_status": measurement_quality_status(measurement),
                    "quality_flag": measurement.quality_flag,
                    "reject_reason": reference.reject_reason,
                }
            )


AAVSO_EXTENDED_FIELDS = (
    "NAME",
    "DATE",
    "MAG",
    "MERR",
    "FILT",
    "TRANS",
    "MTYPE",
    "CNAME",
    "CMAG",
    "KNAME",
    "KMAG",
    "AMASS",
    "GROUP",
    "CHART",
    "NOTES",
)


def csv_text(value: str | None, fallback: str = "na") -> str:
    """Return a compact non-empty CSV field value."""

    text = str(value or "").strip()
    return text if text else fallback


def aavso_magnitude_text(value: str | float | int | None) -> str:
    """Return an AAVSO magnitude field limited to at most 8 characters."""

    if value is None:
        return "na"
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        return "na"
    if not np.isfinite(numeric):
        return "na"
    for digits in (3, 2, 1, 0):
        text = f"{numeric:.{digits}f}"
        if len(text) <= 8:
            return text
    return "na"



def aavso_safe_id(catalog_source: str | None, catalog_id: str | None, fallback: str | None = None) -> str:
    """Return a compact catalog identifier for AAVSO report fields."""

    source = str(catalog_source or "").strip()
    ident = str(catalog_id or fallback or "").strip()
    if not ident:
        return "na"
    return f"{source}:{ident}" if source else ident


def first_result_row_float(row: dict[str, str], *keys: str) -> float:
    """Read the first finite float from one result row."""

    for key in keys:
        try:
            value = float(row.get(key, ""))
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            return value
    return float("nan")


def is_single_field_zp_result_row(row: dict[str, str]) -> bool:
    """Return True for the single-measurement Field-ZP result CSV format."""

    method = row.get("calibration_method", "").strip().lower().replace("-", "_")
    return method in SINGLE_FIELD_ZP_METHOD_KEYS


def result_row_is_valid(row: dict[str, str]) -> bool:
    """Return whether a result row is usable, including legacy CSV schemas."""

    if row.get("valid", "1").strip().lower() in {"0", "false", "no"}:
        return False
    return row.get("quality_status", "").strip().upper() != QUALITY_STATUS_INVALID


def result_csv_contains_single_field_zp_rows(result_csv: Path) -> bool:
    """Return true when a result CSV is a Single Measurement Field-ZP result."""

    try:
        results_module = load_lightcurve_results_module()
        result_kind = results_module.result_kind_for_csv(result_csv)
        return result_kind == results_module.RESULT_KIND_SINGLE
    except Exception:
        return False


def aavso_instrument_from_result_csv(result_csv: Path) -> str:
    """Return the mandatory AAVSO instrument from result metadata."""

    instrument = read_result_metadata_header(result_csv).get("TELESCOPE", "").strip()
    if not instrument:
        raise ValueError(f"{result_csv.name}: missing TELESCOPE result metadata")
    return instrument


def aavso_instrument_display_text(result_csv: Path | None) -> str:
    """Return the current result telescope for the AAVSO tab."""

    if result_csv is None or not result_csv.is_file():
        return "none"
    try:
        return aavso_instrument_from_result_csv(result_csv)
    except (OSError, ValueError):
        return "none"


def validated_aavso_observer_code(value: object) -> str:
    """Return one normalized AAVSO observer code or raise a user-facing error."""

    observer_code = str(value or "").strip().upper()
    if not observer_code:
        raise ValueError("Enter your AAVSO observer code first.")
    if not re.fullmatch(r"[A-Z0-9]{1,5}", observer_code):
        raise ValueError(
            "AAVSO observer code must contain 1 to 5 letters A-Z or digits 0-9 only."
        )
    return observer_code


def aavso_notes_from_row(row: dict[str, str], instrument: str) -> str:
    """Build compact AAVSO notes using AAVSO-style pipe subfields."""

    parts = [
        f"APP={WINDOW_TITLE}",
        f"INSTRUMENT={instrument}",
        f"SOURCE={csv_text(row.get('image_source'))}",
    ]
    quality_status = csv_text(row.get("quality_status"), "").upper()
    if quality_status and quality_status != QUALITY_STATUS_OK:
        parts.append(f"QUALITY={quality_status}")
        quality_flag = csv_text(row.get("quality_flag"), "")
        if quality_flag:
            parts.append(f"QUALITY_FLAG={quality_flag}")
    if is_single_field_zp_result_row(row):
        field_catalog_sources = csv_text(row.get("field_catalog_sources"), APASS_DR10_SOURCE_NAME)
        parts.extend(
            [
                f"ENSEMBLE={field_catalog_sources}",
                f"NREF={csv_text(row.get('field_reference_used'))}",
                f"ZP_SCATTER={csv_text(row.get('field_zero_point_scatter'))}",
                f"ZP_ERROR={csv_text(row.get('field_zero_point_error'))}",
            ]
        )
        check_id = aavso_safe_id(
            row.get("check_catalog_source"),
            row.get("check_catalog_id"),
        )
        if check_id != "na":
            parts.append(f"CHECK={check_id}")
        check_mag = csv_text(row.get("check_catalog_mag"))
        if check_mag != "na":
            parts.append(f"CHECK_V={check_mag}")
        check_delta = csv_text(row.get("check_delta_mag"))
        if check_delta != "na":
            parts.append(f"CHECK_DELTA={check_delta}")
        return "|" + "|".join(f"{part};" for part in parts)

    comp_sources = [item.strip() for item in csv_text(row.get("comparison_catalog_sources")).split(";")]
    comp_ids_raw = [item.strip() for item in csv_text(row.get("comparison_catalog_ids")).split(";")]
    comp_refs = []
    for index, comp_id in enumerate(comp_ids_raw):
        if not comp_id or comp_id == "na":
            continue
        source = comp_sources[index] if index < len(comp_sources) and comp_sources[index] else "CATALOG"
        comp_refs.append(f"{source}:{comp_id}")
    if comp_refs:
        parts.append(f"COMP={','.join(comp_refs)}")
    comp_mags = csv_text(row.get("comparison_catalog_mags"))
    if comp_mags != "na":
        parts.append(f"COMP_V={comp_mags}")
    check_source = csv_text(row.get("check_catalog_source"))
    check_id = csv_text(row.get("check_catalog_id") or row.get("check_object_id"))
    if check_id != "na":
        if check_source == "na":
            parts.append(f"CHECK={check_id}")
        else:
            parts.append(f"CHECK={check_source}:{check_id}")
    check_mag = csv_text(row.get("check_catalog_mag"))
    if check_mag != "na":
        parts.append(f"CHECK_V={check_mag}")
    return "|" + "|".join(f"{part};" for part in parts)


def result_csv_has_calibrated_check(result_csv: Path) -> bool:
    """Return true when at least one valid result row has a calibrated check magnitude."""

    with result_csv.open(newline="") as input_fh:
        reader = csv_data_dict_reader(input_fh)
        for row in reader:
            if not result_row_is_valid(row):
                continue
            try:
                check_mag = float(row.get("check_calibrated_mag", ""))
            except ValueError:
                continue
            if np.isfinite(check_mag):
                return True
    return False


def write_aavso_extended_report(
    result_csv: Path,
    output_path: Path,
    target_name: str,
    observer_code: str,
    instrument: str,
) -> int:
    """Write an AAVSO Extended Format report from a result_curve CSV."""

    observer_code = validated_aavso_observer_code(observer_code)
    rows_written = 0
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with result_csv.open(newline="") as input_fh, output_path.open("w", newline="") as output_fh:
        reader = csv_data_dict_reader(input_fh)
        output_fh.write("#TYPE=Extended\n")
        output_fh.write(f"#OBSCODE={observer_code}\n")
        output_fh.write(f"#SOFTWARE={WINDOW_TITLE}\n")
        output_fh.write("#DELIM=,\n")
        output_fh.write("#DATE=JD\n")
        output_fh.write(f"#GENERATED={generated}\n")
        output_fh.write("#" + ",".join(AAVSO_EXTENDED_FIELDS) + "\n")
        writer = csv.writer(output_fh, lineterminator="\n")
        for row in reader:
            if not result_row_is_valid(row):
                continue
            jd = first_result_row_float(row, "jd")
            mag = first_result_row_float(row, "target_calibrated_mag", "calibrated_mag")
            if not np.isfinite(jd) or not np.isfinite(mag):
                continue
            mag_error = first_result_row_float(
                row,
                "target_calibrated_mag_error",
                "calibrated_mag_error",
            )
            filt = row.get("aavso_filter", "").strip()
            if not filt:
                raise ValueError(f"{result_csv.name}: missing aavso_filter column value")
            if filt not in VALID_AAVSO_FILTERS:
                raise ValueError(
                    f"{result_csv.name}: unsupported aavso_filter {filt!r}; "
                    "only CV, TG and V are supported"
                )
            single_field_zp = is_single_field_zp_result_row(row)
            check_name = aavso_safe_id(
                row.get("check_catalog_source"),
                row.get("check_catalog_id"),
                row.get("check_object_id"),
            )
            if single_field_zp:
                field_catalog_sources = csv_text(row.get("field_catalog_sources"), APASS_DR10_SOURCE_NAME)
                chart = f"{field_catalog_sources.replace(';', '+')} FIELD ZP"
            else:
                chart = "AUTO CATALOG"
            writer.writerow(
                [
                    target_name,
                    f"{jd:.8f}",
                    f"{mag:.3f}",
                    f"{mag_error:.3f}" if np.isfinite(mag_error) and mag_error > 0 else "na",
                    filt,
                    "NO",
                    "STD",
                    "ENSEMBLE",
                    "na",
                    check_name,
                    aavso_magnitude_text(row.get("check_calibrated_mag")),
                    "na",
                    "na",
                    chart,
                    aavso_notes_from_row(row, instrument),
                ]
            )
            rows_written += 1
    return rows_written


def photometry_summary_lines(
    frame_count: int,
    rows_written: int,
    measurements: list[ApertureMeasurement],
    result_csv: Path,
) -> list[str]:
    """Build a compact user-facing summary for the GUI log."""

    target_measurements = [item for item in measurements if item.role == "target"]
    comparison_measurements = [item for item in measurements if item.role == "comparison"]
    valid_target = sum(1 for item in target_measurements if item.valid)
    valid_comp = sum(1 for item in comparison_measurements if item.valid)
    invalid = [item for item in measurements if not item.valid]
    reason_counts = compact_photometry_reason_counts(
        invalid,
        "invalid measurement",
    )
    warnings = [
        item
        for item in measurements
        if item.valid and measurement_quality_status(item) == QUALITY_STATUS_WARNING
    ]
    warning_counts = compact_photometry_reason_counts(
        warnings,
        "warning measurement",
    )

    by_frame: dict[int, list[ApertureMeasurement]] = {}
    for item in measurements:
        by_frame.setdefault(item.frame_index, []).append(item)

    target_missing_frames = 0
    too_few_comp_frames = 0
    for frame_items in by_frame.values():
        has_target = any(item.role == "target" and item.valid for item in frame_items)
        valid_comps = sum(1 for item in frame_items if item.role == "comparison" and item.valid)
        if not has_target:
            target_missing_frames += 1
        if valid_comps < MIN_VALID_COMP_STARS:
            too_few_comp_frames += 1

    scatter = median_zero_point_scatter(result_csv)
    scatter_text = "n/a" if scatter is None else f"{scatter:.4f} mag"

    lines = [
        "",
        "========== Photometry Summary ==========",
        f"Frames found: {frame_count}",
        f"Light-curve points written: {rows_written}",
        f"Valid target measurements: {valid_target}/{len(target_measurements)}",
        f"Valid comparison measurements: {valid_comp}/{len(comparison_measurements)}",
        f"Warning measurements: {len(warnings)}",
        f"Median comparison zero-point scatter: {scatter_text}",
        "",
        "Warning Measurements",
    ]
    if warning_counts:
        for reason, count in warning_counts.most_common():
            lines.append(f"{reason}: {count}")
    else:
        lines.append("No warning aperture measurements.")
    lines.extend(
        [
            "",
            "Rejected Measurements",
            f"Frames without a valid target measurement: {target_missing_frames}",
            f"Frames with fewer than {MIN_VALID_COMP_STARS} valid Comp Stars: {too_few_comp_frames}",
        ]
    )
    if reason_counts:
        for reason, count in reason_counts.most_common():
            lines.append(f"{reason}: {count}")
    else:
        lines.append("No rejected aperture measurements.")
    lines.append("========================================")
    return lines


def compact_photometry_reason_counts(
    measurements: Iterable[ApertureMeasurement],
    fallback: str,
) -> Counter[str]:
    """Group summary reasons by stable quality flag instead of per-frame notes."""

    reasons: list[str] = []
    for measurement in measurements:
        quality_flag = measurement.quality_flag.strip()
        if quality_flag and quality_flag != "OK":
            reasons.append(quality_flag)
            continue
        reasons.append(measurement.note.strip() or quality_flag or fallback)
    return Counter(reasons)


def comparison_star_selection_failure_message(
    raw_count: int,
    usable_count: int,
) -> str:
    """Describe a final Comp-Star shortfall without changing selection policy."""

    return (
        "Comparison-star selection failed: "
        f"{usable_count} usable Comp Stars remain from {raw_count} catalog "
        "candidates after magnitude/error, visibility, and photometric quality "
        f"filtering; at least {MIN_VALID_COMP_STARS} are required."
    )


def target_contamination_failure_message() -> str:
    """Return the compact user-facing reason for a rejected target measurement."""

    return "Rejected: Target contamination"


def all_target_measurements_rejected_by_annulus_contamination(
    measurements: Iterable[ApertureMeasurement],
) -> bool:
    """Return whether annulus contamination rejected every target measurement."""

    targets = [measurement for measurement in measurements if measurement.role == "target"]
    return bool(targets) and all(
        not measurement.valid
        and "ANNULUS_CONTAMINATION" in measurement.quality_flag.split("|")
        for measurement in targets
    )


def annulus_contamination_failure_message() -> str:
    """Return the compact user-facing reason for rejected target annuli."""

    return "Rejected: Annulus contamination"


def photometry_quality_summary(
    frame_count: int,
    rows_written: int,
    measurements: list[ApertureMeasurement],
) -> dict[str, object]:
    """Return compact quality counts for status text and message boxes."""

    target = [item for item in measurements if item.role == "target"]
    comparison = [item for item in measurements if item.role == "comparison"]
    check = [item for item in measurements if item.role == "check"]
    target_invalid = [item for item in target if not item.valid]
    target_warning = [
        item
        for item in target
        if item.valid and measurement_quality_status(item) == QUALITY_STATUS_WARNING
    ]
    comp_invalid = [item for item in comparison if not item.valid]
    comp_warning = [
        item
        for item in comparison
        if item.valid and measurement_quality_status(item) == QUALITY_STATUS_WARNING
    ]
    check_invalid = [item for item in check if not item.valid]
    check_warning = [
        item
        for item in check
        if item.valid and measurement_quality_status(item) == QUALITY_STATUS_WARNING
    ]

    by_frame: dict[int, list[ApertureMeasurement]] = {}
    for item in measurements:
        by_frame.setdefault(item.frame_index, []).append(item)
    target_missing_frames = 0
    too_few_comp_frames = 0
    for frame_items in by_frame.values():
        if not any(item.role == "target" and item.valid for item in frame_items):
            target_missing_frames += 1
        valid_comps = sum(1 for item in frame_items if item.role == "comparison" and item.valid)
        if valid_comps < MIN_VALID_COMP_STARS:
            too_few_comp_frames += 1

    return {
        "frame_count": frame_count,
        "rows_written": rows_written,
        "target_count": len(target),
        "target_valid": len(target) - len(target_invalid),
        "target_invalid": len(target_invalid),
        "target_warning": len(target_warning),
        "target_warning_reasons": Counter(item.quality_flag for item in target_warning),
        "target_invalid_reasons": Counter(item.quality_flag for item in target_invalid),
        "comp_invalid": len(comp_invalid),
        "comp_warning": len(comp_warning),
        "check_invalid": len(check_invalid),
        "check_warning": len(check_warning),
        "target_missing_frames": target_missing_frames,
        "too_few_comp_frames": too_few_comp_frames,
    }


def photometry_quality_summary_text(summary: dict[str, object]) -> str:
    """Return short human-readable quality summary text."""

    lines = [
        f"Light-curve points: {summary['rows_written']}/{summary['frame_count']}",
        (
            "Target: "
            f"{summary['target_invalid']} error(s), "
            f"{summary['target_warning']} warning(s)"
        ),
        (
            "References: "
            f"{summary['comp_invalid']} comp error(s), "
            f"{summary['comp_warning']} comp warning(s), "
            f"{summary['check_invalid']} check error(s), "
            f"{summary['check_warning']} check warning(s)"
        ),
        (
            "Dropped frames: "
            f"{summary['target_missing_frames']} without target, "
            f"{summary['too_few_comp_frames']} with too few compstars"
        ),
    ]
    target_warning_reasons = summary.get("target_warning_reasons")
    if isinstance(target_warning_reasons, Counter) and target_warning_reasons:
        lines.append(
            "Target warning reasons: "
            + ", ".join(f"{reason}={count}" for reason, count in target_warning_reasons.most_common())
        )
    target_invalid_reasons = summary.get("target_invalid_reasons")
    if isinstance(target_invalid_reasons, Counter) and target_invalid_reasons:
        lines.append(
            "Target error reasons: "
            + ", ".join(f"{reason}={count}" for reason, count in target_invalid_reasons.most_common())
        )
    return "\n".join(lines)


def photometry_summary_needs_warning_dialog(summary: dict[str, object]) -> bool:
    """Return whether the completion dialog should be a warning."""

    return any(
        int(summary.get(key, 0)) > 0
        for key in (
            "target_invalid",
            "target_warning",
            "target_missing_frames",
            "too_few_comp_frames",
        )
    )


def robust_scatter(values: list[float]) -> float | None:
    """Return 1.4826 * MAD for a list of values."""

    if not values:
        return None
    array = np.array(values, dtype=np.float64)
    array = array[np.isfinite(array)]
    if len(array) == 0:
        return None
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    return 1.4826 * mad


def comparison_star_stability_lines(
    frame_count: int,
    measurements: list[ApertureMeasurement],
) -> list[str]:
    """Build a compact comparison-star stability report for the GUI log."""

    frame_zero_points: dict[int, float] = {}
    zero_points_by_frame: dict[int, list[tuple[str, float]]] = {}
    for item in measurements:
        if (
            item.role != "comparison"
            or not item.valid
            or item.catalog_mag is None
            or item.inst_mag is None
        ):
            continue
        zero_point = float(item.catalog_mag) - float(item.inst_mag)
        zero_points_by_frame.setdefault(item.frame_index, []).append((item.object_id, zero_point))

    for frame_index, frame_values in zero_points_by_frame.items():
        if len(frame_values) >= MIN_VALID_COMP_STARS:
            frame_zero_points[frame_index] = float(
                np.median(np.array([value for _object_id, value in frame_values], dtype=np.float64))
            )

    by_star: dict[str, list[tuple[float, float]]] = {}
    for frame_index, frame_values in zero_points_by_frame.items():
        frame_zero_point = frame_zero_points.get(frame_index)
        if frame_zero_point is None:
            continue
        for object_id, zero_point in frame_values:
            residual = zero_point - frame_zero_point
            by_star.setdefault(object_id, []).append((zero_point, residual))

    rows: list[tuple[str, int, float, float | None, str]] = []
    scatters: list[float] = []
    for object_id, values in sorted(by_star.items()):
        zero_points = [zero_point for zero_point, _residual in values]
        residuals = [residual for _zero_point, residual in values]
        median_zero_point = float(np.median(np.array(zero_points, dtype=np.float64)))
        scatter = robust_scatter(residuals)
        if scatter is not None:
            scatters.append(scatter)
        rows.append((object_id, len(zero_points), median_zero_point, scatter, "ok"))

    median_scatter = float(np.median(np.array(scatters, dtype=np.float64))) if scatters else None
    warning_threshold = None if median_scatter is None else 2.0 * median_scatter
    min_expected = max(1, int(np.ceil(0.95 * frame_count)))

    lines = [
        "",
        "========== Comparison Star Stability ==========",
        "Robust scatter is 1.4826 * MAD of residuals against each frame's median zero point.",
    ]
    if median_scatter is None:
        lines.append("No valid Comp Star zero points available.")
        lines.append("================================================")
        return lines

    lines.append(f"Median robust scatter: {median_scatter:.4f} mag")
    for object_id, count, median_zero_point, scatter, _status in rows:
        status_parts: list[str] = []
        if count < min_expected:
            status_parts.append("low n")
        if scatter is not None and warning_threshold is not None and scatter > warning_threshold:
            status_parts.append("high scatter")
        status = "check: " + ", ".join(status_parts) if status_parts else "ok"
        scatter_text = "n/a" if scatter is None else f"{scatter:.4f}"
        lines.append(
            f"{object_id}  n={count}/{frame_count}  "
            f"zp_median={median_zero_point:.4f}  robust_scatter={scatter_text}  {status}"
        )
    lines.append("================================================")
    return lines


def running_mean(values: np.ndarray, window_size: int) -> np.ndarray:
    """Return a centered running mean with partial windows at the edges."""

    if window_size <= 1 or len(values) == 0:
        return values.copy()
    half_window = window_size // 2
    result = np.empty_like(values, dtype=np.float64)
    for index in range(len(values)):
        start = max(0, index - half_window)
        stop = min(len(values), index + half_window + 1)
        result[index] = float(np.mean(values[start:stop]))
    return result


def running_mean_window_size(point_count: int) -> int:
    """Return an odd running-mean window from the configured point fraction."""

    if point_count <= 0:
        return 1
    window = max(MIN_PLOT_RUNNING_MEAN_WINDOW, int(round(point_count * PLOT_RUNNING_MEAN_FRACTION)))
    window = min(window, point_count)
    if window % 2 == 0:
        window = window - 1 if window == point_count else window + 1
    return max(1, window)


def load_light_curve_plot_data(
    result_csv: Path,
    *,
    include_invalid: bool = False,
) -> LightCurvePlotData:
    """Read and validate the values used by all light-curve plot variants."""

    if not result_csv.exists():
        raise FileNotFoundError("Light Curve CSV not found.")
    result_metadata = read_result_metadata_header(result_csv)
    with result_csv.open(newline="") as handle:
        rows = list(csv_data_dict_reader(handle))

    image_source = ""
    aavso_filter = ""
    for row in rows:
        image_source = row.get("image_source", "").strip()
        aavso_filter = row.get("aavso_filter", "").strip()
        if image_source or aavso_filter:
            break
    points: list[tuple[float, float, float | None, float | None]] = []
    for row in rows:
        if not include_invalid and not result_row_is_valid(row):
            continue
        try:
            jd = float(row["jd"])
            mag = float(row["target_calibrated_mag"])
        except (KeyError, TypeError, ValueError):
            continue
        magnitude_error: float | None = None
        for error_column in ("target_calibrated_mag_error", "comp_zero_point_error"):
            try:
                candidate_error = float(row.get(error_column, ""))
            except ValueError:
                continue
            if np.isfinite(candidate_error) and candidate_error > 0:
                magnitude_error = candidate_error
                break
        try:
            check_delta = float(row.get("check_delta_mag", ""))
        except ValueError:
            check_delta = None
        points.append((jd, mag, magnitude_error, check_delta))

    if not points:
        raise ValueError("No plottable rows found.")
    if aavso_filter not in VALID_AAVSO_FILTERS:
        raise ValueError("Unsupported result filter.\n\nOnly CV, TG and V are supported.")
    if image_source and image_source not in IMAGE_SOURCE_TO_AAVSO_FILTER:
        raise ValueError("Unsupported image source.\n\nOnly L, G and V are supported.")
    if aavso_filter and image_source:
        filter_label = f"{aavso_filter} ({image_source})"
    else:
        filter_label = aavso_filter or image_source
    points.sort(key=lambda item: item[0])
    jd_values = np.array([item[0] for item in points], dtype=np.float64)
    mag_values = np.array([item[1] for item in points], dtype=np.float64)
    error_values = np.array(
        [float("nan") if item[2] is None else item[2] for item in points],
        dtype=np.float64,
    )
    check_delta_values = np.array(
        [float("nan") if item[3] is None else item[3] for item in points],
        dtype=np.float64,
    )
    try:
        candidate_exposure = float(result_metadata.get("EXPOSURE_SECONDS", ""))
    except ValueError:
        candidate_exposure = float("nan")
    exposure_seconds = (
        candidate_exposure
        if np.isfinite(candidate_exposure) and candidate_exposure > 0
        else None
    )
    return LightCurvePlotData(
        jd_values=jd_values,
        mag_values=mag_values,
        error_values=error_values,
        check_delta_values=check_delta_values,
        filter_label=filter_label,
        exposure_seconds=exposure_seconds,
    )


def draw_light_curve_figure(
    figure: Figure,
    data: LightCurvePlotData,
    target_name: str,
    source_label: str,
    *,
    show_running_mean: bool,
    include_check: bool,
) -> tuple[object, str]:
    """Draw the common normal/preview light-curve figure."""

    figure.clear()
    has_check = include_check and np.isfinite(data.check_delta_values).any()
    if has_check:
        axis = figure.add_subplot(211)
        check_axis = figure.add_subplot(212, sharex=axis)
    else:
        axis = figure.add_subplot(111)
        check_axis = None
    if np.isfinite(data.error_values).any():
        axis.errorbar(
            data.jd_values,
            data.mag_values,
            yerr=data.error_values,
            fmt="o",
            markersize=3,
            ecolor=(0.45, 0.45, 0.45, 0.5),
            elinewidth=0.5,
            capsize=2,
        )
    else:
        axis.plot(data.jd_values, data.mag_values, "o", markersize=3)

    legend_needed = False
    if show_running_mean and len(data.mag_values) >= 2:
        mean_window = running_mean_window_size(len(data.mag_values))
        mean_values = running_mean(data.mag_values, mean_window)
        axis.plot(
            data.jd_values,
            mean_values,
            color="#d55e00",
            linewidth=1.4,
            label=f"{mean_window}-point running mean",
        )
        legend_needed = True

    if legend_needed:
        axis.legend(loc="best", fontsize=8)

    axis.invert_yaxis()
    ylabel = "Calibrated magnitude"
    if data.filter_label:
        ylabel = f"{ylabel} [{data.filter_label}]"
    axis.set_ylabel(ylabel)
    title = target_name or "Target"
    if data.filter_label:
        title = f"{title} - {data.filter_label}"
    axis.set_title(title)
    axis.grid(True, alpha=0.3)
    if check_axis is not None:
        check_axis.axhline(0.0, color=(0.4, 0.4, 0.4, 0.7), linewidth=0.8)
        check_axis.plot(
            data.jd_values,
            data.check_delta_values,
            "o",
            markersize=3,
            color="#4c78a8",
        )
        check_axis.set_xlabel("Julian Date")
        check_axis.set_ylabel("Check - catalog mag")
        check_axis.grid(True, alpha=0.3)
    else:
        axis.set_xlabel("Julian Date")
        if include_check:
            axis.text(
                0.99,
                0.01,
                "No calibrated Check Star stored in this result",
                transform=axis.transAxes,
                ha="right",
                va="bottom",
                fontsize=8,
                color="#a65f00",
            )
    if len(data.jd_values) > 1:
        x_min = float(np.min(data.jd_values))
        x_max = float(np.max(data.jd_values))
        x_pad = max((x_max - x_min) * 0.03, 1e-6)
        axis.set_xlim(x_min - x_pad, x_max + x_pad)
    return axis, ""


def connect_siril_interface(
    siril: object,
    log: Callable[[str], None] | None = None,
) -> None:
    """Connect to Siril, tolerating a connection still being released."""

    attempts = SIRIL_CONNECT_ATTEMPTS
    for attempt in range(1, attempts + 1):
        try:
            siril.connect()
            return
        except Exception as exc:
            message = str(exc).lower()
            transient_connection = (
                "already connected to siril" in message
                or (os.name == "nt" and "pipe is busy" in message)
            )
            if not transient_connection or attempt >= attempts:
                raise
            if log is not None:
                log(
                    "The previous Siril connection is still being released; "
                    f"retrying ({attempt}/{attempts - 1})."
                )
            time.sleep(SIRIL_CONNECT_RETRY_DELAY_SECONDS)


class PlateSolveWorker(QThread):
    """Run Siril sequence plate solving without blocking the GUI."""

    log = pyqtSignal(str)

    def __init__(self, work_dir: Path, hints: PlateSolveHints) -> None:
        super().__init__()
        self.work_dir = work_dir
        self.hints = hints
        self.result: tuple[bool, str] | None = None

    def emit_log(self, message: str) -> None:
        try:
            print(message)
        except OSError:
            pass
        self.log.emit(message)

    def plate_solve_options(self, ra_deg: float, dec_deg: float) -> str:
        return (
            f"{ra_deg:.8f},{dec_deg:.8f} "
            "-force "
            f"-focal={self.hints.focal_mm:.6g} "
            f"-pixelsize={self.hints.pixel_size_um:.6g} "
            "-catalog=gaia"
        )

    def run_connected_header_solve(self, stats: PlateSolveStats) -> None:
        """Plate-solve frames through the Siril instance that launched SeePhot."""

        if not stats.failed_files:
            return
        self.emit_log(
            f"Running header-hint plate solve through the current Siril instance "
            f"for {stats.failed} image(s)."
        )
        start_time = time.monotonic()
        siril = s.SirilInterface()
        connected = False
        attempted = 0
        try:
            connect_siril_interface(siril, self.emit_log)
            connected = True
            siril.cmd(f'cd "{siril_path(self.work_dir)}"')
            for index, name in enumerate(stats.failed_files, start=1):
                path = self.work_dir / name
                try:
                    hints = read_plate_solve_hints(path)
                    siril.cmd(f'load "{name}"')
                    siril.cmd(
                        "platesolve "
                        + self.plate_solve_options(hints.ra_deg, hints.dec_deg)
                    )
                    siril.cmd(f'save "{name}"')
                except Exception as exc:
                    self.emit_log(
                        f"WARNING: Plate solve failed for {name}: {exc}"
                    )
                attempted = index
                if index % 10 == 0 or index == stats.failed:
                    self.emit_log(
                        f"Plate solve progress: {index}/{stats.failed} image(s) attempted."
                    )
        except Exception as exc:
            self.emit_log(
                f"WARNING: Could not use the current Siril instance for plate solving: {exc}"
            )
        finally:
            if connected:
                try:
                    siril.disconnect()
                except Exception:
                    pass

        elapsed = time.monotonic() - start_time
        self.emit_log(
            f"Current-instance plate solve attempted {attempted}/{stats.failed} image(s) "
            f"in {elapsed:.1f}s."
        )

    def write_external_header_solve_script(self, stats: PlateSolveStats) -> Path:
        """Write the standalone Siril script used on macOS and Unix."""

        script_path = self.work_dir / "seestar_lightcurve_header_platesolve.ssf"
        lines = ["requires 1.0.0", f'cd "{siril_path(self.work_dir)}"']
        for name in stats.failed_files:
            hints = read_plate_solve_hints(self.work_dir / name)
            safe_name = siril_path(Path(name))
            lines.extend(
                [
                    f'load "{safe_name}"',
                    "platesolve "
                    + self.plate_solve_options(hints.ra_deg, hints.dec_deg),
                    f'save "{safe_name}"',
                ]
            )
        script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return script_path

    def run_external_header_solve(
        self,
        stats: PlateSolveStats,
        siril_binary: Path,
    ) -> None:
        """Plate-solve in a separate Siril process without owning a sirilpy socket."""

        if not stats.failed_files:
            return
        try:
            script_path = self.write_external_header_solve_script(stats)
        except Exception as exc:
            self.emit_log(f"WARNING: Could not create external plate-solve script: {exc}")
            return

        log_path = self.work_dir / "seestar_lightcurve_header_platesolve.log"
        self.emit_log(
            f"Running standalone Siril header-hint plate solve for "
            f"{stats.failed} image(s): {siril_binary}"
        )
        start_time = time.monotonic()
        try:
            command = external_siril_command(siril_binary, script_path)
            uses_app_run = Path(command[0]).name == "AppRun"
            child_environment = (
                os.environ.copy()
                if uses_app_run
                else external_siril_environment(siril_binary)
            )
            if sys.platform.startswith("linux"):
                self.emit_log(
                    f"Standalone Siril command: {' '.join(command)}"
                )
                if uses_app_run:
                    self.emit_log(
                        "Standalone Siril launch mode: AppImage AppRun owns "
                        "the runtime loader and library paths."
                    )
                else:
                    self.emit_log(
                        "Standalone Siril fallback environment: "
                        f"APPDIR={child_environment.get('APPDIR', '<unset>')}; "
                        "LD_LIBRARY_PATH="
                        f"{child_environment.get('LD_LIBRARY_PATH', '<unset>')}"
                    )
            with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
                result = subprocess.run(
                    command,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=child_environment,
                    check=False,
                )
        except Exception as exc:
            self.emit_log(
                f"WARNING: Standalone Siril plate solve could not be started: {exc}"
            )
            return

        elapsed = time.monotonic() - start_time
        self.emit_log(
            f"Standalone Siril plate solve finished with exit code "
            f"{result.returncode} in {elapsed:.1f}s; log: {log_path}"
        )

    def run_header_solve(self, stats: PlateSolveStats) -> None:
        """Select a backend that does not regress the macOS Siril connection."""

        if os.name != "nt":
            siril_binary = external_siril_binary_path()
            if siril_binary is not None:
                self.run_external_header_solve(stats, siril_binary)
                return
            self.emit_log(
                "WARNING: No standalone Siril executable found; falling back to "
                "the current Siril instance."
            )
        self.run_connected_header_solve(stats)

    def run(self) -> None:
        try:
            touched_files, removed_keys = remove_inherited_wcs_from_sequence(
                self.work_dir,
                WORK_SEQUENCE_NAME,
            )
            self.emit_log(
                "Removed inherited WCS from "
                f"{touched_files} work image(s) before plate solving "
                f"({removed_keys} FITS header key(s))."
            )

            stats = collect_plate_solve_stats(self.work_dir, WORK_SEQUENCE_NAME)
            self.emit_log(
                f"Starting per-frame plate solve for {stats.failed}/{stats.total} image(s)."
            )
            if stats.failed_files:
                self.emit_log(
                    "Images to solve: "
                    + ", ".join(stats.failed_files[:20])
                    + (" ..." if len(stats.failed_files) > 20 else "")
                )

            if stats.failed_files:
                self.run_header_solve(stats)
                stats = collect_plate_solve_stats(self.work_dir, WORK_SEQUENCE_NAME)
                self.emit_log(
                    f"Plate solve result after per-frame solve: "
                    f"{stats.solved}/{stats.total} images solved."
                )

            if stats.is_complete:
                self.result = (
                    True,
                    f"Prepare complete: {stats.solved}/{stats.total} images aligned and plate solved.",
                )
            elif stats.has_any_solution:
                self.result = (
                    True,
                    f"Prepare partially complete: {stats.solved}/{stats.total} images aligned and plate solved; "
                    f"{stats.failed} failed.",
                )
            else:
                self.result = (
                    False,
                    "Prepare failed: no plate-solved images were written.",
                )
        except Exception as exc:
            self.result = (False, f"Prepare failed: {exc}")


def solve_image_for_analyze(path: Path, progress: Callable[[str], None]) -> tuple[object, Path]:
    """Plate-solve an Analyze image in a disposable copy, never in the original."""

    hints = read_plate_solve_hints(path)
    workspace = tempfile.TemporaryDirectory(prefix="seephot_analyze_platesolve_")
    work_dir = Path(workspace.name)
    copied_path = work_dir / f"{WORK_SEQUENCE_NAME}00001.fit"
    try:
        shutil.copy2(path, copied_path)
        progress(f"No WCS found; plate-solving a temporary copy of {path.name}.")
        worker = PlateSolveWorker(work_dir, hints)
        worker.log.connect(progress)
        worker.run()
        success, message = worker.result or (False, "Analyze plate solve ended without a result.")
        progress(message)
        if not success or read_reference_frame(copied_path, 0) is None:
            raise RuntimeError("Automatic plate solve did not produce a usable WCS image.")
        return workspace, copied_path
    except Exception:
        workspace.cleanup()
        raise


class LightCurveWindow(QWidget):
    """Main GUI window for the first development milestone."""

    automatic_prepare_finished = pyqtSignal(bool, str)
    automatic_lightcurve_finished = pyqtSignal(bool, str)
    automatic_stack_finished = pyqtSignal(bool, str, object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(MAIN_WINDOW_OBJECT_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(WINDOW_WIDTH, 620)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.source_dir_edit = QLineEdit()
        self.source_dir_edit.setPlaceholderText("Select the folder containing FITS images")
        self.source_dir_edit.editingFinished.connect(self.scan_entered_directory)
        self.source_dir_edit.returnPressed.connect(self.scan_entered_directory)
        self.is_closing = False
        self.local_event_loop: QEventLoop | None = None
        self.last_source_dialog_directory: Path | None = None
        if DEFAULT_FITS_DIRECTORY is not None and DEFAULT_FITS_DIRECTORY.is_dir():
            self.source_dir_edit.setText(str(DEFAULT_FITS_DIRECTORY))
            self.last_source_dialog_directory = DEFAULT_FITS_DIRECTORY

        self.photometry_mode = MODE_LIGHTCURVE
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Light Curve", MODE_LIGHTCURVE)
        self.mode_combo.addItem("Single Measurement", MODE_SINGLE_MEASUREMENT)
        self.mode_combo.setFixedWidth(COMPACT_COMBO_WIDTH)
        self.mode_combo.currentIndexChanged.connect(self.on_photometry_mode_changed)

        self.scan_label = QLabel("No input directory selected.")
        self.current_scan: InputScan | None = None
        self.solve_worker: PlateSolveWorker | None = None
        self.prepare_completed = False
        self.reference_frame: ReferenceFrame | None = None
        self.catalog_objects: list[CatalogObject] = []
        self.filtered_catalog_objects: list[CatalogObject] = []
        self.selected_target: SelectedTarget | None = None
        self.pending_single_vsx_selection: CatalogObject | None = None
        self.single_target_precheck_ready = False
        self.comparison_stars: list[CatalogObject] = []
        self.check_star: CatalogObject | None = None
        self.comparison_selection_failure_reason = ""
        self.series_optimized_aperture_settings: ApertureSettings | None = None
        self.session_temp_directories: set[Path] = set()
        self.log_lines: list[str] = []

        self.target_label = QLabel("Selected target: none")
        self.photometry_target_label = QLabel("Target: none")
        self.photometry_target_label.setMaximumWidth(320)
        self.photometry_target_label.setToolTip("Target: none")

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(120)
        self.overall_status_label = QLabel()
        self.overall_status_label.setObjectName("overallStatusLabel")
        self.overall_status_label.setTextFormat(Qt.TextFormat.RichText)
        self.overall_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.overall_status_frame = QFrame()
        self.overall_status_frame.setObjectName("overallStatusFrame")
        self.overall_status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        overall_status_frame_layout = QHBoxLayout(self.overall_status_frame)
        overall_status_frame_layout.setContentsMargins(4, 2, 4, 2)
        overall_status_frame_layout.addWidget(self.overall_status_label)

        self.vsx_tree = QTreeWidget()
        self.vsx_tree.setColumnCount(6)
        self.vsx_tree.setHeaderLabels(["Name", "Type", "Period", "Magnitude", "RA", "Dec"])
        self.vsx_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.vsx_tree.setSortingEnabled(True)
        self.vsx_tree.itemSelectionChanged.connect(self.on_vsx_selection_changed)
        self.vsx_tree.itemDoubleClicked.connect(self.on_vsx_double_clicked)
        self.vsx_preview_timer = QTimer(self)
        self.vsx_preview_timer.setSingleShot(True)
        self.vsx_preview_timer.timeout.connect(self.preview_selected_vsx_in_siril)
        self.vsx_filter_status_label = QLabel("Showing 0 / 0 VSX Objects")

        self.comp_tree = QTreeWidget()
        self.comp_tree.setColumnCount(7)
        self.comp_tree.setHeaderLabels(["Catalog ID", "V", "e_V", "nobs", "RA", "Dec", "Raw"])
        self.comp_tree.itemSelectionChanged.connect(self.on_comp_selection_changed)

        self.lightcurve_status_label = QLabel("No Light Curve generated yet.")
        self.single_result_target_label = QLabel("Target: n/a")
        self.single_result_check_label = QLabel("Check: n/a")
        self.extremum_fit_label = QLabel("Min/Max fit: none")
        self.lightcurve_figure = Figure(figsize=(9.5, 6.0), tight_layout=True)
        self.lightcurve_canvas = FigureCanvas(self.lightcurve_figure)
        self.plot_dialog: QDialog | None = None
        self.lightcurve_toolbar: NavigationToolbar | None = None
        self.compstars_dialog: QDialog | None = None
        self.compstars_status_label: QLabel | None = None
        self.plot_extremum_fit_label: QLabel | None = None
        self.lightcurve_axis = None
        self.lightcurve_jd_values: np.ndarray | None = None
        self.lightcurve_mag_values: np.ndarray | None = None
        self.lightcurve_mag_errors: np.ndarray | None = None
        self.lightcurve_selected_range: tuple[float, float] | None = None
        self.lightcurve_span_selector: SpanSelector | None = None
        self.lightcurve_fit_artists: list[object] = []
        self.current_extremum_fit_result: dict[str, object] | None = None
        self.current_extremum_fit_csv: Path | None = None
        self.extremum_fit_attempt_counter = 0
        self.loaded_lightcurve_csv: Path | None = None
        self.loaded_lightcurve_target_name = "Target"
        self.loaded_lightcurve_source_label = ""
        self.current_result_origin = "none"
        self.varstars_status = "none"
        self.last_vsx_failure_message = ""
        self.comp_status = "none"
        self.result_status = "none"
        self.fit_status = "none"
        self.export_status = "none"
        self.current_exports: set[str] = set()
        self.busy_state = BUSY_IDLE
        self.busy_message = ""
        self._busy_locked_buttons: dict[QWidget, bool] = {}
        self.cfa_stack_window: QWidget | None = None
        self.automatic_stack_worker: QThread | None = None
        self.cfa_stack_signal_connected = False
        self.lightcurve_results_module = lightcurve_results
        self.result_browser_dialog: QDialog | None = None
        self.bav_result_browser_dialog: QDialog | None = None
        self.batch_tab: QWidget | None = None
        self.batch_dialog: QDialog | None = None
        self.bav_tab: QWidget | None = None
        self.archive_tab: QWidget | None = None
        self.ext_scopes_tab: QWidget | None = None

        version_log_line = (
            f"{datetime.now().strftime('%H:%M:%S')}  SeePhot version: {SCRIPT_VERSION}"
        )
        self.log_lines.append(version_log_line)
        self.log_view.append(version_log_line)
        self._build_ui()
        self.update_mode_dependent_controls()
        self.refresh_action_availability()
        self.append_log("Running inside Siril Python.")
        self.close_siril_display_context("startup")
        if DEFAULT_FITS_DIRECTORY is not None and DEFAULT_FITS_DIRECTORY.is_dir():
            self.append_log(f"Default input directory: {DEFAULT_FITS_DIRECTORY}")
            self.scan_selected_directory()
        else:
            self.append_log("Select a FITS input directory to start.")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter = splitter
        splitter.setHandleWidth(8)
        root.addWidget(splitter, stretch=1)

        tabs = QTabWidget()
        self.tabs = tabs
        tabs.tabBar().setExpanding(False)
        tabs.setDocumentMode(True)
        tabs.setMinimumHeight(240)
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        splitter.addWidget(tabs)

        input_tab = QWidget()
        input_layout = QVBoxLayout(input_tab)

        prepare_actions = QWidget()
        prepare_actions_layout = QHBoxLayout(prepare_actions)
        prepare_actions_layout.setContentsMargins(0, 0, 0, 0)
        prepare_actions_layout.setSpacing(8)
        cfa_group = QGroupBox("Process single directory")
        cfa_layout = QHBoxLayout(cfa_group)
        self.cfa_stack_button = QPushButton("CFA Channels / Stack")
        self.cfa_stack_button.clicked.connect(
            lambda: self.run_busy_action(
                "STACK_WINDOW_OPENING",
                "Opening CFA Channels / Stack window.",
                self.open_cfa_stack_window,
            )
        )
        cfa_layout.addWidget(self.cfa_stack_button)
        cfa_layout.addStretch(1)
        prepare_actions_layout.addWidget(cfa_group)
        self.append_log("Required CFA/Stack component loaded.")
        automatic_group = QGroupBox("Process multiple directories")
        automatic_layout = QHBoxLayout(automatic_group)
        self.automatic_button = QPushButton("Batch Mode")
        self.automatic_button.clicked.connect(self.open_automatic_runner)
        automatic_layout.addWidget(self.automatic_button)
        automatic_layout.addStretch(1)
        prepare_actions_layout.addWidget(automatic_group)
        self.append_log("Required CFA/Stack Batch component loaded.")
        prepare_actions_layout.addStretch(1)
        input_layout.addWidget(prepare_actions)

        source_group = QGroupBox("Detect Variables")
        self.source_group = source_group
        source_layout = QFormLayout(source_group)
        source_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        source_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        source_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        source_layout.addRow("Mode:", self.mode_combo)

        source_row = QHBoxLayout()
        self.source_dir_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.source_dir_edit.setMinimumWidth(420)
        source_row.addWidget(self.source_dir_edit, stretch=1)

        self.source_browse_button = QPushButton("Browse...")
        self.source_browse_button.clicked.connect(self.choose_source_input)
        source_row.addWidget(self.source_browse_button)

        self.source_path_label = QLabel("FITS directory:")
        source_layout.addRow(self.source_path_label, source_row)
        source_layout.addRow("Status:", self.scan_label)

        detect_layout = QHBoxLayout()
        self.prepare_button = QPushButton("Run")
        self.prepare_button.setEnabled(False)
        self.prepare_button.clicked.connect(self.prepare_sequence)
        detect_layout.addWidget(self.prepare_button)
        self.prepare_description_label = QLabel(
            "Register frames · Plate solve · Load reference · Query VSX"
        )
        self.prepare_description_label.setWordWrap(True)
        self.prepare_description_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        detect_layout.addWidget(self.prepare_description_label, stretch=1)
        source_layout.addRow(detect_layout)
        input_layout.addWidget(source_group)

        input_layout.addStretch(1)

        varstar_tab = QWidget()
        self.varstar_tab = varstar_tab
        varstar_layout = QVBoxLayout(varstar_tab)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_label)
        target_row.addStretch(1)
        self.add_target_button = QPushButton("Add Target")
        self.add_target_button.clicked.connect(self.add_target)
        target_row.addWidget(self.add_target_button)

        self.select_target_button = QPushButton("Select Target")
        self.select_target_button.setEnabled(False)
        self.select_target_button.clicked.connect(self.select_target)
        target_row.addWidget(self.select_target_button)

        self.vsx_selected_button = QPushButton("Open VSX")
        self.vsx_selected_button.setEnabled(False)
        self.vsx_selected_button.clicked.connect(self.open_selected_vsx_page)
        target_row.addWidget(self.vsx_selected_button)

        varstar_layout.addLayout(target_row)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        name_filter_group = QWidget()
        name_filter_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        name_filter_layout = QHBoxLayout(name_filter_group)
        name_filter_layout.setContentsMargins(0, 0, 0, 0)
        name_filter_layout.setSpacing(2)
        self.vsx_name_filter_edit = QLineEdit()
        self.vsx_name_filter_edit.setPlaceholderText("Name filter")
        self.vsx_name_filter_edit.setFixedWidth(
            self.vsx_name_filter_edit.fontMetrics().horizontalAdvance("M" * 10) + 18
        )
        name_filter_label = QLabel("Name:")
        name_filter_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        name_filter_layout.addWidget(name_filter_label)
        name_filter_layout.addWidget(self.vsx_name_filter_edit)
        filter_row.addWidget(name_filter_group)

        type_filter_group = QWidget()
        type_filter_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        type_filter_layout = QHBoxLayout(type_filter_group)
        type_filter_layout.setContentsMargins(0, 0, 0, 0)
        type_filter_layout.setSpacing(2)
        self.vsx_type_filter_edit = QLineEdit()
        self.vsx_type_filter_edit.setPlaceholderText("RR*,EA,EB")
        self.vsx_type_filter_edit.setFixedWidth(
            self.vsx_type_filter_edit.fontMetrics().horizontalAdvance("M" * 20) + 18
        )
        type_filter_label = QLabel("Type:")
        type_filter_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        type_filter_layout.addWidget(type_filter_label)
        type_filter_layout.addWidget(self.vsx_type_filter_edit)
        filter_row.addWidget(type_filter_group)

        self.vsx_type_preset_combo = QComboBox()
        for label, type_filter in VSX_FILTER_PRESETS:
            self.vsx_type_preset_combo.addItem(label, type_filter)
        self.vsx_type_preset_combo.setFixedWidth(
            self.vsx_type_preset_combo.fontMetrics().horizontalAdvance("Short period") + 72
        )
        self.vsx_type_preset_combo.setToolTip(
            "Seestar recommended variable-type presets for the VSX type filter."
        )
        self.vsx_type_preset_combo.currentIndexChanged.connect(self.on_vsx_type_preset_changed)
        filter_row.addWidget(self.vsx_type_preset_combo)

        mag_filter_group = QWidget()
        mag_filter_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        mag_filter_layout = QHBoxLayout(mag_filter_group)
        mag_filter_layout.setContentsMargins(0, 0, 0, 0)
        mag_filter_layout.setSpacing(2)
        self.vsx_mag_filter_edit = QLineEdit()
        self.vsx_mag_filter_edit.setPlaceholderText("Max mag")
        self.vsx_mag_filter_edit.setFixedWidth(
            self.vsx_mag_filter_edit.fontMetrics().horizontalAdvance("M" * 4) + 18
        )
        mag_filter_label = QLabel("Mag <=:")
        mag_filter_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        mag_filter_layout.addWidget(mag_filter_label)
        mag_filter_layout.addWidget(self.vsx_mag_filter_edit)
        filter_row.addWidget(mag_filter_group)
        filter_row.addStretch(1)
        for edit in (
            self.vsx_name_filter_edit,
            self.vsx_type_filter_edit,
            self.vsx_mag_filter_edit,
        ):
            edit.returnPressed.connect(self.apply_vsx_filter)

        varstar_layout.addLayout(filter_row)
        varstar_layout.addWidget(self.vsx_filter_status_label)

        vsx_group = QWidget()
        vsx_layout = QVBoxLayout(vsx_group)
        vsx_layout.setContentsMargins(0, 0, 0, 0)
        vsx_layout.addWidget(self.vsx_tree)
        varstar_layout.addWidget(vsx_group, stretch=1)

        self.show_selected_comp_button = QPushButton("Show Selected")
        self.show_selected_comp_button.setEnabled(False)
        self.show_selected_comp_button.clicked.connect(self.show_selected_compstar)

        self.show_all_comp_button = QPushButton("Show All")
        self.show_all_comp_button.setEnabled(False)
        self.show_all_comp_button.clicked.connect(self.show_all_compstars)

        self.show_compstars_button = QPushButton("Show Comparison Stars")
        self.show_compstars_button.setEnabled(False)
        self.show_compstars_button.clicked.connect(self.show_compstars_dialog)

        lightcurve_tab = QWidget()
        self.lightcurve_tab = lightcurve_tab
        lightcurve_layout = QVBoxLayout(lightcurve_tab)
        self.lightcurve_layout = lightcurve_layout

        target_actions = QWidget()
        target_actions_layout = QHBoxLayout(target_actions)
        target_actions_layout.setContentsMargins(0, 0, 0, 0)
        target_actions_layout.setSpacing(8)
        self.target_actions_layout = target_actions_layout

        run_lightcurve_group = QGroupBox("Single target")
        self.target_run_group = run_lightcurve_group
        run_lightcurve_layout = QVBoxLayout(run_lightcurve_group)
        run_lightcurve_controls = QHBoxLayout()
        self.target_run_controls = run_lightcurve_controls
        run_lightcurve_controls.addWidget(self.photometry_target_label)
        self.run_light_curve_button = QPushButton("Create Light Curve")
        self.run_light_curve_button.setEnabled(False)
        self.run_light_curve_button.clicked.connect(
            lambda: self.run_busy_action(
                "PHOTOMETRY_RUNNING",
                "Creating Light Curve.",
                self.run_light_curve,
            )
        )
        run_lightcurve_controls.addWidget(self.run_light_curve_button)
        run_lightcurve_controls.addWidget(self.show_compstars_button)

        run_lightcurve_controls.addStretch(1)
        run_lightcurve_layout.addLayout(run_lightcurve_controls)
        target_actions_layout.addWidget(run_lightcurve_group)
        target_actions_layout.addStretch(1)
        lightcurve_layout.addWidget(target_actions)

        single_result_group = QGroupBox("Result")
        self.single_result_group = single_result_group
        single_result_layout = QVBoxLayout(single_result_group)
        self.single_result_status_label = QLabel("MEASUREMENT STATUS: N/A")
        self.single_result_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.single_result_status_label.setWordWrap(True)
        single_result_layout.addWidget(self.single_result_status_label)
        for label in (self.single_result_target_label, self.single_result_check_label):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            single_result_layout.addWidget(label)
        self.set_single_result_quality_banner("", "")
        single_result_group.setVisible(False)
        lightcurve_layout.addWidget(single_result_group)

        open_lightcurve_group = QGroupBox("Open")
        self.open_lightcurve_group = open_lightcurve_group
        open_lightcurve_layout = QHBoxLayout(open_lightcurve_group)
        self.open_lightcurve_button = QPushButton("Light Curve CSV")
        self.open_lightcurve_button.clicked.connect(self.open_light_curve_csv)
        open_lightcurve_layout.addWidget(self.open_lightcurve_button)
        self.open_result_button = QPushButton("Results Folder")
        self.open_result_button.clicked.connect(self.open_result_browser)
        open_lightcurve_layout.addWidget(self.open_result_button)
        self.open_result_vsx_button = QPushButton("VSX")
        self.open_result_vsx_button.clicked.connect(self.open_current_result_vsx_page)
        open_lightcurve_layout.addWidget(self.open_result_vsx_button)

        open_lightcurve_layout.addStretch(1)
        binning_group = QGroupBox("Photometric Binning")
        binning_layout = QHBoxLayout(binning_group)
        self.binning_mode_combo = QComboBox()
        self.binning_mode_combo.addItem("N images", "count")
        self.binning_mode_combo.addItem("Duration", "seconds")
        self.binning_mode_combo.setFixedWidth(COMPACT_COMBO_WIDTH)
        binning_layout.addWidget(self.binning_mode_combo)
        self.binning_value_spin = QSpinBox()
        self.binning_value_spin.setRange(2, 999999)
        self.binning_value_spin.setValue(10)
        self.binning_value_spin.setSuffix(" images")
        self.binning_value_spin.setFixedWidth(SHORT_EDIT_WIDTH)
        self.binning_mode_combo.currentIndexChanged.connect(self.update_binning_controls)
        binning_layout.addWidget(self.binning_value_spin)
        self.create_binned_curve_button = QPushButton("Create Binned Curve")
        self.create_binned_curve_button.setToolTip(
            "Create a separate flux-binned result from the complete instrumental CSV. "
            "Only an explicitly marked original-image series is eligible; bins never cross gaps."
        )
        self.create_binned_curve_button.clicked.connect(self.create_binned_curve)
        binning_layout.addWidget(self.create_binned_curve_button)
        self.use_raw_curve_button = QPushButton("Use Raw Curve")
        self.use_raw_curve_button.setToolTip("Make the unmodified source result the active curve again.")
        self.use_raw_curve_button.clicked.connect(self.activate_raw_curve)
        binning_layout.addWidget(self.use_raw_curve_button)
        self.binning_eligibility_label = QLabel()
        binning_layout.addWidget(self.binning_eligibility_label, 1)
        lightcurve_layout.addWidget(binning_group)

        plot_lightcurve_group = QGroupBox("Plot / Fit")
        self.plot_lightcurve_group = plot_lightcurve_group
        plot_lightcurve_layout = QVBoxLayout(plot_lightcurve_group)
        plot_lightcurve_controls = QHBoxLayout()
        self.plot_light_curve_button = QPushButton("Plot Light Curve")
        self.plot_light_curve_button.setEnabled(False)
        self.plot_light_curve_button.clicked.connect(self.plot_light_curve)
        plot_lightcurve_controls.addWidget(self.plot_light_curve_button)

        self.show_running_mean_checkbox = QCheckBox("Running mean")
        self.show_running_mean_checkbox.setToolTip(
            f"Show a running mean using {PLOT_RUNNING_MEAN_FRACTION:.0%} of the plotted points."
        )
        plot_lightcurve_controls.addWidget(self.show_running_mean_checkbox)

        self.trim_lightcurve_button = QPushButton("Trim to Selection")
        self.trim_lightcurve_button.setEnabled(False)
        self.trim_lightcurve_button.setToolTip(
            "Keep only CSV rows inside the selected JD range. This permanently updates "
            "the current light curve used by plots, fits, AAVSO/BAV exports, and saved results."
        )
        self.trim_lightcurve_button.clicked.connect(
            lambda: self.run_busy_action(
                "TRIM_RUNNING",
                "Trimming the selected light-curve range.",
                self.trim_lightcurve_to_selection,
                [self.trim_lightcurve_button],
            )
        )
        plot_lightcurve_controls.addWidget(self.trim_lightcurve_button)

        plot_lightcurve_controls.addStretch(1)
        plot_lightcurve_layout.addLayout(plot_lightcurve_controls)

        fit_lightcurve_controls = QHBoxLayout()

        fit_lightcurve_controls.addWidget(QLabel("Curve model:"))
        self.extremum_model_combo = QComboBox()
        for model_label, model_name in EXTREMUM_MODEL_OPTIONS:
            self.extremum_model_combo.addItem(model_label, model_name)
        self.extremum_model_combo.setToolTip(
            "The recommended robust cubic spline chooses its smoothing automatically. "
            "Other entries fit exactly the selected model; no silent fallback occurs."
        )
        fit_lightcurve_controls.addWidget(self.extremum_model_combo)

        self.fit_extremum_button = QPushButton("Fit Min/Max")
        self.fit_extremum_button.setEnabled(False)
        self.fit_extremum_button.setToolTip("Drag a JD range in the plot, then fit the extremum in that range.")
        self.fit_extremum_button.clicked.connect(
            lambda: self.run_busy_action(
                "FIT_RUNNING",
                "Fitting selected min/max.",
                self.fit_selected_extremum,
            )
        )
        fit_lightcurve_controls.addWidget(self.fit_extremum_button)

        self.clear_extremum_button = QPushButton("Clear Fit")
        self.clear_extremum_button.setEnabled(False)
        self.clear_extremum_button.clicked.connect(lambda: self.clear_extremum_fit())
        fit_lightcurve_controls.addWidget(self.clear_extremum_button)

        fit_lightcurve_controls.addStretch(1)
        plot_lightcurve_layout.addLayout(fit_lightcurve_controls)
        lightcurve_layout.addWidget(plot_lightcurve_group)
        lightcurve_layout.addWidget(open_lightcurve_group)
        lightcurve_layout.addStretch(1)

        export_tab = QWidget()
        export_layout = QVBoxLayout(export_tab)

        export_config_group = QGroupBox("Config")
        export_form = QFormLayout(export_config_group)
        export_form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        export_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.aavso_observer_code_edit = QLineEdit()
        self.aavso_observer_code_edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.aavso_observer_code_edit.setPlaceholderText("AAVSO observer code")
        self.aavso_observer_code_edit.setToolTip(
            "Required: 1 to 5 letters A-Z or digits 0-9."
        )
        self.aavso_observer_code_edit.setFixedWidth(COMPACT_COMBO_WIDTH)
        try:
            observer_settings = load_optional_observer_result_settings()
            bav_observer_code = str(
                observer_settings.get("OBSERVER_AAVSO", "") or ""
            ).strip().upper()
        except Exception:
            bav_observer_code = ""
        self._aavso_bav_prefill = bav_observer_code
        self.aavso_observer_code_edit.setText(bav_observer_code)
        self.aavso_observer_code_edit.textEdited.connect(
            self.uppercase_aavso_observer_code_input
        )
        export_form.addRow("Observer code:", self.aavso_observer_code_edit)

        self.aavso_instrument_label = QLabel("none")
        export_form.addRow("Telescope:", self.aavso_instrument_label)
        export_layout.addWidget(export_config_group)

        export_action_group = QGroupBox("Export")
        export_action_row = QHBoxLayout(export_action_group)
        self.export_aavso_button = QPushButton("Export AAVSO Report")
        self.export_aavso_button.clicked.connect(
            lambda: self.run_busy_action(
                "EXPORT_RUNNING",
                "Exporting AAVSO report.",
                self.export_aavso_report,
            )
        )
        export_action_row.addWidget(self.export_aavso_button)

        self.open_aavso_folder_button = QPushButton("Open Folder")
        self.open_aavso_folder_button.clicked.connect(self.open_aavso_export_folder)
        export_action_row.addWidget(self.open_aavso_folder_button)

        export_action_row.addStretch(1)
        self.open_aavso_apps_button = QPushButton("AAVSO apps")
        self.open_aavso_apps_button.clicked.connect(self.open_aavso_apps)
        export_action_row.addWidget(self.open_aavso_apps_button)
        export_layout.addWidget(export_action_group)

        self.export_status_label = QLabel("Exports the selected or opened Light Curve CSV as AAVSO Extended Format.")
        export_layout.addStretch(1)

        log_group = QWidget()
        log_group.setMinimumHeight(120)
        log_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        log_layout_root = QVBoxLayout(log_group)
        log_layout_root.addWidget(self.log_view)
        splitter.addWidget(log_group)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 2)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes(list(MAIN_SPLITTER_START_SIZES))

        global_action_row = QHBoxLayout()
        global_action_row.setContentsMargins(14, 4, 14, 4)
        global_action_row.setSpacing(8)

        footer_current_status = QWidget()
        self.footer_current_status = footer_current_status
        footer_current_status.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        footer_current_status_layout = QVBoxLayout(footer_current_status)
        footer_current_status_layout.setContentsMargins(0, 0, 0, 0)
        footer_current_status_layout.setSpacing(0)
        self.footer_current_target_label = QLabel()
        self.footer_current_curve_label = QLabel()
        for label in (
            self.footer_current_target_label,
            self.footer_current_curve_label,
        ):
            label.setFixedWidth(340)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            footer_current_status_layout.addWidget(label)
        self.update_footer_current_status_labels()
        global_action_row.addWidget(footer_current_status)
        global_action_row.addStretch(1)

        overview_button = QPushButton("Overview")
        overview_button.clicked.connect(self.show_overview)
        global_action_row.addWidget(overview_button)

        help_button = QPushButton("Help")
        help_button.clicked.connect(self.show_help)
        global_action_row.addWidget(help_button)

        reset_button = QPushButton("Reset")
        reset_button.setToolTip("Clear the current run state and delete this script's temporary files.")
        reset_button.clicked.connect(self.reset_application_state)
        global_action_row.addWidget(reset_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        global_action_row.addWidget(close_button)

        root.addLayout(global_action_row)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(14, 0, 14, 4)
        status_row.addWidget(self.overall_status_frame)
        root.addLayout(status_row)

        tabs.addTab(input_tab, "Prepare")
        tabs.addTab(varstar_tab, "Variables")
        self.add_required_batch_runner()
        tabs.addTab(lightcurve_tab, "Photometry")
        tabs.addTab(export_tab, "Export")
        self.add_optional_qc_tab()
        self.add_optional_bav_tab()
        self.add_optional_archive_tab()
        self.add_optional_ext_scopes_tab()
        self.add_required_analyze_tab()
        self.footer_current_status_tabs = tuple(
            tab
            for tab in (lightcurve_tab, export_tab, self.bav_tab, self.archive_tab)
            if tab is not None
        )
        tabs.currentChanged.connect(self.update_footer_current_status_visibility)
        self.update_footer_current_status_visibility()

    def update_footer_current_status_visibility(self, _index: int | None = None) -> None:
        """Show current-result details only where they are actionable."""

        footer_status = getattr(self, "footer_current_status", None)
        tabs = getattr(self, "tabs", None)
        visible_tabs = getattr(self, "footer_current_status_tabs", ())
        if footer_status is not None and tabs is not None:
            footer_status.setVisible(tabs.currentWidget() in visible_tabs)

    def add_required_analyze_tab(self) -> None:
        """Add the required standalone Tools tab."""

        analyze_tab = analyze_tools.create_analyze_tab(self.analyze_plugin_context())
        if not isinstance(analyze_tab, QWidget):
            raise RuntimeError("Required Tools component did not return a QWidget")
        label = str(analyze_tools.PLUGIN_TAB_LABEL).strip() or "Tools"
        self.tabs.addTab(analyze_tab, label)
        self.append_log("Required Tools component loaded.")

    def analyze_plugin_context(self) -> dict[str, object]:
        """Return the narrow API surface for independent Analyze tools."""

        return {
            "append_log": self.append_log,
            "read_reference_frame": read_reference_frame,
            "solve_image_for_analyze": solve_image_for_analyze,
            "open_profiling_runner": self.open_profiling_runner,
            "query_gaia_dr3_region": query_gaia_dr3_region,
            "resolve_aperture_settings": resolve_aperture_settings,
            "aperture_measurements_for_frame": aperture_measurements_for_frame,
        }

    def add_optional_ext_scopes_tab(self) -> None:
        """Load an optional external telescope/FITS tools tab if present."""

        plugin_path = Path(__file__).with_name("sp_mod_ext_scopes.py")
        if not plugin_path.exists():
            return

        try:
            spec = importlib.util.spec_from_file_location("sp_mod_ext_scopes", plugin_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not create import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
            create_ext_scopes_tab = getattr(module, "create_ext_scopes_tab", None)
            if not callable(create_ext_scopes_tab):
                raise RuntimeError("missing create_ext_scopes_tab(context)")

            ext_scopes_tab = create_ext_scopes_tab(self.ext_scopes_plugin_context())
            if not isinstance(ext_scopes_tab, QWidget):
                raise RuntimeError("create_ext_scopes_tab(context) did not return a QWidget")

            label = str(getattr(module, "PLUGIN_TAB_LABEL", "Ext_Tools")).strip() or "Ext_Tools"
            self.ext_scopes_tab = ext_scopes_tab
            self.tabs.addTab(ext_scopes_tab, label)
            version = str(getattr(module, "EXT_SCOPES_VERSION", "")).strip()
            version_text = f" (version {version})" if version else ""
            self.append_log(
                f"Optional Ext_Tools module loaded: {plugin_path.name}{version_text}."
            )
        except Exception as exc:
            self.append_log(f"WARNING: Optional Ext_Tools tab could not be loaded: {exc}")

    def ext_scopes_plugin_context(self) -> dict[str, object]:
        """Return the narrow API surface for external telescope/FITS tools."""

        return {"append_log": self.append_log}

    def add_optional_qc_tab(self) -> None:
        """Load an optional QC tab from sp_mod_qc.py if present."""

        plugin_path = Path(__file__).with_name("sp_mod_qc.py")
        if not plugin_path.exists():
            return

        try:
            spec = importlib.util.spec_from_file_location("sp_mod_qc", plugin_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not create import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
            create_qc_tab = getattr(module, "create_qc_tab", None)
            if not callable(create_qc_tab):
                raise RuntimeError("missing create_qc_tab(context)")

            qc_tab = create_qc_tab(self.qc_plugin_context())
            if not isinstance(qc_tab, QWidget):
                raise RuntimeError("create_qc_tab(context) did not return a QWidget")

            label = str(getattr(module, "PLUGIN_TAB_LABEL", "QC")).strip() or "QC"
            self.tabs.addTab(qc_tab, label)
            self.append_log(f"Optional QC module loaded: {plugin_path.name}.")
        except Exception as exc:
            self.append_log(f"WARNING: Optional QC tab could not be loaded: {exc}")

    def add_optional_bav_tab(self) -> None:
        """Load an optional BAV report tab from sp_mod_bav.py if present."""

        plugin_path = Path(__file__).with_name("sp_mod_bav.py")
        if not plugin_path.exists():
            return

        try:
            spec = importlib.util.spec_from_file_location("sp_mod_bav", plugin_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not create import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
            create_bav_tab = getattr(module, "create_bav_tab", None)
            if not callable(create_bav_tab):
                raise RuntimeError("missing create_bav_tab(context)")

            bav_tab = create_bav_tab(self.qc_plugin_context())
            if not isinstance(bav_tab, QWidget):
                raise RuntimeError("create_bav_tab(context) did not return a QWidget")

            bav_tab.setProperty("context_help_body", module.PLUGIN_HELP_BODY)
            bav_tab.setProperty("context_help_footer", module.PLUGIN_HELP_FOOTER)
            label = str(getattr(module, "PLUGIN_TAB_LABEL", "BAV")).strip() or "BAV"
            self.bav_tab = bav_tab
            self.tabs.addTab(bav_tab, label)
            version = str(getattr(module, "BAV_PLUGIN_VERSION", "")).strip()
            version_text = f" (version {version})" if version else ""
            self.append_log(f"Optional BAV module loaded: {plugin_path.name}{version_text}.")
        except Exception as exc:
            self.append_log(f"WARNING: Optional BAV tab could not be loaded: {exc}")

    def add_optional_archive_tab(self) -> None:
        """Load an optional Archive tab from sp_mod_archive.py if present."""

        plugin_path = Path(__file__).with_name("sp_mod_archive.py")
        if not plugin_path.exists():
            return

        try:
            spec = importlib.util.spec_from_file_location("sp_mod_archive", plugin_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("could not create import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
            create_archive_tab = getattr(module, "create_archive_tab", None)
            if not callable(create_archive_tab):
                raise RuntimeError("missing create_archive_tab(context)")

            archive_tab = create_archive_tab(self.qc_plugin_context())
            if not isinstance(archive_tab, QWidget):
                raise RuntimeError("create_archive_tab(context) did not return a QWidget")

            label = str(getattr(module, "PLUGIN_TAB_LABEL", "Archive")).strip() or "Archive"
            self.archive_tab = archive_tab
            self.tabs.addTab(archive_tab, label)
            version = str(getattr(module, "ARCHIVE_PLUGIN_VERSION", "")).strip()
            version_text = f" (version {version})" if version else ""
            self.append_log(f"Optional Archive module loaded: {plugin_path.name}{version_text}.")
        except Exception as exc:
            self.append_log(f"WARNING: Optional Archive tab could not be loaded: {exc}")

    def add_required_batch_runner(self) -> None:
        """Add the required multi-target runner beside the single-target action."""

        batch_tab = batch_tools.create_batch_tab(self.qc_plugin_context())
        if not isinstance(batch_tab, QWidget):
            raise RuntimeError("Required Batch component did not return a QWidget")

        self.batch_tab = batch_tab
        batch_dialog = QDialog(self)
        batch_dialog.setWindowTitle("Multiple targets")
        batch_dialog_layout = QVBoxLayout(batch_dialog)
        batch_dialog_layout.addWidget(batch_tab)
        batch_dialog.resize(900, 620)
        self.batch_dialog = batch_dialog

        batch_group = QGroupBox("Multiple targets")
        batch_layout = QHBoxLayout(batch_group)
        self.batch_run_button = QPushButton("Select targets")
        self.batch_run_button.clicked.connect(self.open_batch_runner)
        batch_layout.addWidget(self.batch_run_button)
        batch_layout.addStretch(1)
        self.target_actions_layout.insertWidget(1, batch_group)
        self.append_log("Required Batch component loaded.")

    def open_batch_runner(self) -> None:
        """Show the multi-target photometry runner."""

        if self.batch_dialog is None:
            return
        self.batch_tab.sync_visible_targets()
        self.batch_dialog.show()
        self.batch_dialog.raise_()
        self.batch_dialog.activateWindow()

    def qc_plugin_context(self) -> dict[str, object]:
        """Return the small API surface exposed to the optional QC module."""

        def resolve_current_aperture_settings(frame: ReferenceFrame | None) -> ApertureSettings:
            if self.photometry_mode == MODE_LIGHTCURVE and self.series_optimized_aperture_settings is not None:
                return self.series_optimized_aperture_settings
            return resolve_aperture_settings(frame)

        def query_selected_comparison_catalog(
            frame: ReferenceFrame,
            target_mag: float | None,
            dvmag: float,
            progress: Callable[[str], None] | None = None,
        ) -> list[CatalogObject]:
            return query_comparison_catalog_region(
                frame,
                target_mag,
                dvmag,
                progress,
                self.selected_comparison_catalog_source(),
            )

        return {
            "get_source_directory": self.current_source_directory,
            "get_temp_directory": self.current_temp_directory,
            "get_work_directory": self.current_work_directory,
            "get_results_directory": self.current_results_directory,
            "get_reference_frame": lambda: self.reference_frame,
            "get_selected_target": lambda: self.selected_target,
            "get_comparison_stars": lambda: tuple(self.comparison_stars),
            "get_check_star": lambda: self.check_star,
            "append_log": self.append_log,
            "log_geometry": self.log_geometry,
            "mark_export_status": self.mark_export_status,
            "set_export_process_status": self.set_export_process_status,
            "script_version": SCRIPT_VERSION,
            "comp_selection_rule": comp_selection_rule_metadata(),
            "plate_solve_sequence_name": PLATE_SOLVE_SEQUENCE_NAME,
            "work_sequence_name": WORK_SEQUENCE_NAME,
            "temp_work_directory_for": temp_work_directory_for,
            "results_directory_for": results_directory_for,
            "solved_sequence_fits_files": solved_sequence_fits_files,
            "read_reference_frame": read_reference_frame,
            "sequence_index": sequence_index,
            "query_apass_dr10_region": query_apass_dr10_region,
            "query_comparison_catalog_region": query_selected_comparison_catalog,
            "object_pixel_position_in_frame": object_pixel_position_in_frame,
            "resolve_aperture_settings": resolve_current_aperture_settings,
            "series_ringset_candidate_settings": series_ringset_candidate_settings,
            "read_loaded_photometry_frame": read_loaded_photometry_frame,
            "aperture_measurements_from_loaded_frame": aperture_measurements_from_loaded_frame,
            "score_series_ringset_measurements": score_series_ringset_measurements,
            "format_series_ringset_result": format_series_ringset_result,
            "annulus_bright_island_diagnostics": annulus_bright_island_diagnostics,
            "aperture_measurement": aperture_measurement,
            "aperture_measurements_for_frame": aperture_measurements_for_frame,
            "measure_series_sequence": measure_series_sequence,
            "write_instrumental_photometry": write_instrumental_photometry,
            "write_calibrated_light_curve": write_calibrated_light_curve,
            "result_instrument_defaults": result_instrument_defaults,
            "result_instrument_metadata_from_header": result_instrument_metadata_from_header,
            "ensure_importable_module": ensure_importable_module,
            "get_photometry_mode": self.selected_photometry_mode,
            "get_current_lightcurve": self.current_export_lightcurve,
            "set_current_lightcurve": self.set_current_lightcurve_from_plugin,
            "set_aavso_observer_code_from_bav": self.set_aavso_observer_code_from_bav,
            "open_bav_results_folder": self.open_bav_result_browser,
            "open_linearity_dialog": linearity_tools.open_linearity_dialog,
            "get_diagnostic_result_csv": self.diagnostic_result_csv,
            "instrumental_csv_for_result_csv": self.instrumental_csv_for_result_csv,
            "frame_search_directories_for_result_csv": self.frame_search_directories_for_result_csv,
            "current_display_aperture_settings": self.current_display_aperture_settings,
            "get_current_extremum_fit": self.current_extremum_fit,
            "get_action_availability": self.action_availability,
            "is_busy": self.is_busy,
            "begin_busy_action": self.begin_busy_action,
            "finish_busy_action": self.finish_busy_action,
            "run_busy_action": self.run_busy_action,
            "set_source_directory": self.set_source_directory_for_automation,
            "mark_image_source_archived": self.mark_image_source_archived,
            "start_cfa_stack": self.start_cfa_stack_for_automation,
            "start_detect_varstars": self.prepare_sequence,
            "select_target_by_name": self.select_target_by_name_for_automation,
            "get_selected_vsx_targets": self.selected_vsx_targets_for_batch,
            "get_visible_vsx_targets": self.visible_vsx_targets_for_batch,
            "select_target_object": self.set_selected_target_object,
            "start_batch_measurement": self.start_measurement_for_batch,
            "start_light_curve": self.start_light_curve_for_batch,
            "set_batch_result_status": self.set_batch_result_status,
            "create_light_curve": self.run_light_curve,
            "describe_batch_result": self.describe_batch_result,
            "show_batch_result": self.show_result_from_csv_for_batch,
            "plot_light_curve_from_csv": self.plot_light_curve_from_csv_for_batch,
            "automatic_stack_finished": self.automatic_stack_finished,
            "automatic_prepare_finished": self.automatic_prepare_finished,
            "automatic_lightcurve_finished": self.automatic_lightcurve_finished,
            "open_cfa_stack_for_source": self.open_cfa_stack_for_profiling,
        }

    def busy_action_buttons(self) -> list[QPushButton]:
        """Return main-window action buttons that should be locked while busy."""

        names = (
            "cfa_stack_button",
            "automatic_button",
            "profiling_button",
            "prepare_button",
            "run_light_curve_button",
            "export_aavso_button",
            "fit_extremum_button",
        )
        buttons: list[QPushButton] = []
        for name in names:
            button = getattr(self, name, None)
            if isinstance(button, QPushButton):
                buttons.append(button)
        return buttons

    def is_busy(self) -> bool:
        return self.busy_state != BUSY_IDLE

    def action_availability(self) -> dict[str, bool]:
        """Return availability derived from the current main-window state.

        This is deliberately a small public UI contract for optional modules.
        It does not replace validation in an action handler.
        """

        busy = self.is_busy()
        prepared_field = self.prepare_completed and self.reference_frame is not None
        selected_object = self.current_vsx_selection()
        selected_vsx = selected_object is not None and not is_added_target(selected_object)
        selected_for_mode = selected_object is not None and (
            self.photometry_mode == MODE_SINGLE_MEASUREMENT or selected_vsx
        )
        selected_target = self.selected_target is not None
        has_compstars = bool(self.comparison_stars)
        has_result = self.current_export_lightcurve() is not None
        result_metadata: dict[str, str] = {}
        current_result = self.current_export_lightcurve()
        if current_result is not None:
            try:
                result_metadata = read_result_metadata_header(current_result[0])
            except OSError:
                pass
        can_create_binned_curve = (
            has_result
            and result_metadata.get("SOURCE_PROVENANCE") == "SINGLE_FRAME_LG_SERIES"
            and result_metadata.get("BINNING_ALLOWED") == "1"
        )
        can_use_raw_curve = (
            has_result
            and result_metadata.get("SOURCE_PROVENANCE") == "BINNED_RESULT"
            and bool(result_metadata.get("BIN_SOURCE_RESULT", "").strip())
        )
        aavso_folder = self.current_aavso_export_directory()
        has_aavso_folder = aavso_folder is not None and aavso_folder.is_dir()
        can_plot_lightcurve = has_result and self.current_result_origin != "single"
        has_selected_plot_range = (
            can_plot_lightcurve
            and self.lightcurve_axis is not None
            and self.lightcurve_selected_range is not None
        )
        lightcurve_mode = self.photometry_mode == MODE_LIGHTCURVE
        can_run_photometry = (
            not busy
            and prepared_field
            and selected_target
            and (not lightcurve_mode or not is_added_target(self.selected_target.catalog_object))
            and (lightcurve_mode or self.single_target_precheck_ready)
        )
        return {
            "prepare": not busy and self.current_scan is not None,
            "select_target": not busy and prepared_field and selected_for_mode,
            "add_target": not busy and prepared_field and self.photometry_mode == MODE_SINGLE_MEASUREMENT,
            "open_selected_vsx": not busy and prepared_field and selected_vsx,
            "create_light_curve": can_run_photometry,
            "show_comparison_stars": not busy and has_compstars,
            "show_all_comparison_stars": not busy and has_compstars and selected_target,
            "show_selected_comparison_star": (
                not busy
                and selected_target
                and self.current_comp_selection() is not None
            ),
            "select_batch_targets": not busy and prepared_field,
            "plot_light_curve": not busy and can_plot_lightcurve,
            "fit_extremum": not busy and has_selected_plot_range,
            "trim_lightcurve": not busy and has_selected_plot_range,
            "clear_extremum_fit": not busy and (
                bool(self.lightcurve_fit_artists) or has_selected_plot_range
            ),
            "export_aavso": not busy and has_result,
            "open_aavso_folder": not busy and has_aavso_folder,
            "create_binned_curve": not busy and can_create_binned_curve,
            "use_raw_curve": not busy and can_use_raw_curve,
            "bav_export_base": not busy and has_result,
        }

    def refresh_action_availability(self) -> None:
        """Apply the central availability state to main and opted-in module actions."""

        state = self.action_availability()
        buttons = {
            "prepare_button": "prepare",
            "select_target_button": "select_target",
            "add_target_button": "add_target",
            "vsx_selected_button": "open_selected_vsx",
            "run_light_curve_button": "create_light_curve",
            "show_compstars_button": "show_comparison_stars",
            "show_all_comp_button": "show_all_comparison_stars",
            "show_selected_comp_button": "show_selected_comparison_star",
            "batch_run_button": "select_batch_targets",
            "plot_light_curve_button": "plot_light_curve",
            "fit_extremum_button": "fit_extremum",
            "trim_lightcurve_button": "trim_lightcurve",
            "clear_extremum_button": "clear_extremum_fit",
            "export_aavso_button": "export_aavso",
            "open_aavso_folder_button": "open_aavso_folder",
            "create_binned_curve_button": "create_binned_curve",
            "use_raw_curve_button": "use_raw_curve",
        }
        for name, action in buttons.items():
            button = getattr(self, name, None)
            if isinstance(button, QPushButton):
                button.setEnabled(state[action])

        for name in ("binning_mode_combo", "binning_value_spin"):
            widget = getattr(self, name, None)
            if isinstance(widget, QWidget):
                widget.setEnabled(state["create_binned_curve"])

        refresh_bav = getattr(self.bav_tab, "refresh_action_availability", None)
        if callable(refresh_bav):
            try:
                refresh_bav()
            except Exception as exc:
                self.append_log(f"WARNING: BAV action availability refresh failed: {exc}")

    def busy_context_change_message(self, action: str) -> str | None:
        """Return and log why a result/source/mode change is currently blocked."""

        if not self.is_busy():
            return None
        active = self.busy_message or self.busy_state
        message = f"Cannot {action} while another operation is running: {active}"
        self.append_log(f"Blocked context change while busy: {action}; active={active}.")
        return message

    def busy_context_widgets(self) -> list[QWidget]:
        """Return main context selectors that must stay fixed during an operation."""

        widgets: list[QWidget] = []
        for name in ("source_group", "open_lightcurve_group"):
            widget = getattr(self, name, None)
            if isinstance(widget, QWidget):
                widgets.append(widget)
        return widgets

    def begin_busy_action(
        self,
        state: str,
        message: str,
        buttons: Iterable[QPushButton] | None = None,
    ) -> bool:
        """Start a GUI action lock for a long-running action."""

        if self.is_busy():
            active = self.busy_message or self.busy_state
            self.append_log(f"Blocked action while busy: {active}.")
            if state == "PHOTOMETRY_RUNNING":
                if self.batch_tab is not None and getattr(self.batch_tab, "running", False):
                    self.fail_lightcurve_run(f"Another operation is still running: {active}")
                self.show_lightcurve_failure_dialog(
                    f"Another operation is still running:\n{active}",
                    title="Operation Running",
                )
            else:
                QMessageBox.information(
                    self,
                    "Operation Running",
                    f"Another operation is still running:\n{active}",
                )
            return False

        self.busy_state = state
        self.busy_message = message
        self.vsx_preview_timer.stop()
        self._busy_locked_buttons = {}
        # Snapshot and lock child action buttons before disabling their parent
        # context groups. Otherwise QWidget.isEnabled() already reports False
        # for the child and the busy-state restore leaves it disabled.
        lock_widgets: list[QWidget] = [
            *self.busy_action_buttons(),
            *self.busy_context_widgets(),
        ]
        if buttons is not None:
            lock_widgets.extend(button for button in buttons if isinstance(button, QPushButton))
        for widget in dict.fromkeys(lock_widgets):
            self._busy_locked_buttons[widget] = widget.isEnabled()
            widget.setEnabled(False)
        self.append_log(message)
        self.refresh_action_availability()
        self.update_overall_status()
        QApplication.processEvents()
        return True

    def finish_busy_action(self, state: str | None = None) -> None:
        """Release the current GUI action lock."""

        if state is not None and self.busy_state not in {state, BUSY_IDLE}:
            self.append_log(
                f"WARNING: Busy state changed while finishing {state}: {self.busy_state}."
            )
        for widget, was_enabled in list(self._busy_locked_buttons.items()):
            if not widget.isEnabled():
                widget.setEnabled(was_enabled)
        self._busy_locked_buttons.clear()
        self.busy_state = BUSY_IDLE
        self.busy_message = ""
        self.refresh_action_availability()
        self.update_overall_status()
        QApplication.processEvents()

    def run_busy_action(
        self,
        state: str,
        message: str,
        action: Callable[[], None],
        buttons: Iterable[QPushButton] | None = None,
    ) -> None:
        if not self.begin_busy_action(state, message, buttons):
            return
        try:
            action()
        except Exception as exc:
            self.append_log(f"ERROR: {message} failed: {exc}")
            for line in traceback.format_exc().rstrip().splitlines():
                self.append_log(line)
            if state == "PHOTOMETRY_RUNNING":
                self.fail_lightcurve_run(f"{message} failed: {exc}")
                self.show_lightcurve_failure_dialog(
                    f"Task failed.\n\n{exc}",
                    title="Operation Failed",
                    critical=True,
                )
            else:
                QMessageBox.critical(
                    self,
                    "Operation Failed",
                    f"Task failed.\n\n{exc}",
                )
        finally:
            self.finish_busy_action(state)

    def open_cfa_stack_window(self, initial_source_dir: str | Path | None = None, grouping_behavior: str | None = None) -> bool:
        """Open the required CFA/Stack preprocessing window."""

        try:
            stack_window_class = getattr(cfa_stack_app, "StackWindow", None)
            if not callable(stack_window_class):
                raise RuntimeError("SeePhot_CFA.py has no importable StackWindow class")

            if self.cfa_stack_window is None or not self.cfa_stack_window.isVisible():
                self.cfa_stack_window = stack_window_class(
                    allowed_channels=("L", "G"),
                    lightcurve_mode=True,
                    busy_context=self.qc_plugin_context(),
                    default_grouping_behavior="gap_aware",
                )
                self.cfa_stack_signal_connected = False

            if initial_source_dir is not None:
                set_source_directory = getattr(self.cfa_stack_window, "set_source_directory", None)
                if not callable(set_source_directory) or not set_source_directory(initial_source_dir):
                    raise FileNotFoundError(
                        f"Profiling source folder not found: {Path(initial_source_dir).expanduser()}"
                    )

            if grouping_behavior is not None:
                set_grouping_behavior = getattr(self.cfa_stack_window, "set_grouping_behavior", None)
                if not callable(set_grouping_behavior) or not set_grouping_behavior(grouping_behavior):
                    raise ValueError(f"Unsupported CFA/Stack grouping behavior: {grouping_behavior}")

            result_signal = getattr(self.cfa_stack_window, "stack_result_ready", None)
            if result_signal is not None and not self.cfa_stack_signal_connected:
                result_signal.connect(self.on_cfa_stack_result_ready)
                self.cfa_stack_signal_connected = True

            self.cfa_stack_window.show()
            self.cfa_stack_window.raise_()
            self.cfa_stack_window.activateWindow()
            self.append_log("Opened CFA Channels / Stack window.")
            return True
        except Exception as exc:
            message = f"Could not open CFA Channels / Stack.\n\n{exc}"
            self.append_log(f"WARNING: {message.replace(chr(10), ' ')}")
            QMessageBox.warning(self, "CFA Channels / Stack", message)
            return False

    def open_cfa_stack_for_profiling(self, source_dir: str, grouping_behavior: str) -> bool:
        """Open CFA/Stack with a source explicitly confirmed by Stack Profiling."""

        if self.is_busy():
            active = self.busy_message or self.busy_state
            QMessageBox.information(
                self,
                "Operation Running",
                f"Another operation is still running:\n{active}",
            )
            return False
        return self.open_cfa_stack_window(source_dir, grouping_behavior)

    def open_profiling_runner(self, source_dir: str | None = None, grouping_behavior: str | None = None) -> None:
        """Open the required read-only Stack Profiling dialog."""

        try:
            dialog_name = "open_profiling_result_dialog" if source_dir is not None else "open_profiling_dialog"
            open_dialog = getattr(profiling_tools, dialog_name, None)
            if not callable(open_dialog):
                raise RuntimeError(
                    "Required Stack Profiling component has no dialog entry point"
                )
            if source_dir is None:
                open_dialog(self.qc_plugin_context(), self)
            else:
                open_dialog(self.qc_plugin_context(), self, source_dir, grouping_behavior or "gap_aware")
        except Exception as exc:
            message = f"Could not open Stack Profiling.\n\n{exc}"
            self.append_log(f"WARNING: {message.replace(chr(10), ' ')}")
            QMessageBox.warning(self, "Stack Profiling", message)

    def open_automatic_runner(self) -> None:
        """Open the required CFA/Stack Batch dialog."""

        try:
            open_dialog = getattr(automatic_tools, "open_automatic_dialog", None)
            if not callable(open_dialog):
                raise RuntimeError("Required CFA/Stack Batch component has no dialog entry point")

            open_dialog(self.qc_plugin_context(), self)
        except Exception as exc:
            message = f"Could not open CFA/Stack Batch.\n\n{exc}"
            self.append_log(f"WARNING: {message.replace(chr(10), ' ')}")
            QMessageBox.warning(self, "CFA/Stack Batch", message)

    def on_cfa_stack_result_ready(self, result_dir: str) -> None:
        """Use a completed CFA/stack result as the selected FITS directory."""

        busy_message = self.busy_context_change_message("load another source")
        if busy_message is not None:
            QMessageBox.information(self, "Operation Running", busy_message)
            return
        path = Path(result_dir).expanduser()
        if not path.is_dir():
            self.append_log(f"WARNING: CFA Channels / Stack result folder not found: {path}")
            return
        if self.photometry_mode != MODE_LIGHTCURVE:
            index = self.mode_combo.findData(MODE_LIGHTCURVE)
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)
        self.remember_source_dialog_directory(path)
        self.source_dir_edit.setText(str(path))
        self.append_log(f"CFA Channels / Stack result selected as FITS folder: {path}")
        self.scan_selected_directory(force=True)

    def start_cfa_stack_for_automation(
        self,
        source_dir: str | Path,
        selected_channels: Iterable[str],
        plan_mode: str,
        plan_values: Iterable[int],
        selected_plan_suffixes: Iterable[str],
        allow_overwrite: bool = False,
        grouping_behavior: str = "continuous",
    ) -> bool:
        """Start CFA/stack preprocessing for the required multi-folder batch."""

        if self.automatic_stack_worker is not None and self.automatic_stack_worker.isRunning():
            message = "CFA/Stack Batch is already running."
            self.append_log(f"ERROR: {message}")
            self.automatic_stack_finished.emit(False, message, {})
            return False

        try:
            stack_worker_class = getattr(cfa_stack_app, "StackWorker", None)
            if not callable(stack_worker_class):
                raise RuntimeError("SeePhot_CFA.py has no importable StackWorker class")
            build_stack_plans = getattr(cfa_stack_app, "build_stack_plans")
            collect_valid_fits = getattr(cfa_stack_app, "collect_valid_fits")
            frames_need_cfa_split = getattr(cfa_stack_app, "frames_need_cfa_split")
            existing_result_dirs_for_run = getattr(cfa_stack_app, "existing_result_dirs_for_run")

            source_path = Path(source_dir).expanduser()
            if not source_path.is_dir():
                raise FileNotFoundError(f"Source folder not found: {source_path}")

            channels = tuple(
                str(channel).strip().upper()
                for channel in selected_channels
                if str(channel).strip().upper() in {"L", "G"}
            )
            if not channels:
                raise ValueError("Select at least one CFA output channel.")

            values = tuple(int(value) for value in plan_values)
            if len(values) != 2:
                raise ValueError("CFA/Stack Batch plan values must contain exactly two numbers.")
            available_plans = build_stack_plans(str(plan_mode), values)
            requested_suffixes = {str(suffix) for suffix in selected_plan_suffixes}
            selected_plans = tuple(
                plan for plan in available_plans if plan.suffix in requested_suffixes
            )
            if not selected_plans:
                raise ValueError("Select at least one stack group.")

            source_frames = collect_valid_fits(source_path, lambda _message: None)
            if not source_frames:
                raise FileNotFoundError(
                    "No valid .fit/.fits files were found directly in the selected source folder: "
                    f"{source_path}"
                )
            needs_cfa_split = frames_need_cfa_split(source_frames)
            if not needs_cfa_split:
                self.append_log(
                    "WARNING: CFA/Stack Batch source does not look like CFA/Bayer input; "
                    "stack result will not use a channel suffix."
                )
            existing_dirs = existing_result_dirs_for_run(
                source_path,
                selected_plans,
                channels,
                needs_cfa_split,
                grouping_behavior,
            )
            if existing_dirs and not allow_overwrite:
                shown = ", ".join(path.name for path in existing_dirs[:6])
                if len(existing_dirs) > 6:
                    shown += f", ... ({len(existing_dirs)} total)"
                raise FileExistsError(
                    "Stack result folder(s) already exist. Enable overwrite or remove them first: "
                    f"{shown}"
                )

            if not self.begin_busy_action(
                "STACK_RUNNING",
                "CFA/Stack Batch is running.",
            ):
                message = "Main app is busy."
                self.automatic_stack_finished.emit(False, message, {})
                return False

            worker = stack_worker_class(
                str(source_path),
                selected_plans,
                channels,
                bool(allow_overwrite),
                True,
                grouping_behavior=grouping_behavior,
            )
            self.automatic_stack_worker = worker
            worker.log.connect(lambda message: self.append_log(f"CFA/Stack Batch: {message}"))
            worker.finished.connect(self.on_automatic_stack_finished)
            self.append_log(
                "CFA/Stack Batch selected: "
                f"channels={', '.join(channels)}, "
                f"groups={', '.join(plan.name for plan in selected_plans)}, "
                f"behavior={grouping_behavior}."
            )
            worker.start()
            return True
        except Exception as exc:
            message = str(exc)
            self.append_log(f"ERROR: CFA/Stack Batch could not start: {message}")
            self.automatic_stack_finished.emit(False, message, {})
            return False

    def on_automatic_stack_finished(self, success: bool, message: str, latest_result_dir: str = "") -> None:
        """Report completion of a CFA/Stack Batch run."""

        self.automatic_stack_worker = None
        self.finish_busy_action("STACK_RUNNING")

        payload: dict[str, str] = {}
        result_dir = Path(latest_result_dir).expanduser() if latest_result_dir else None
        if success and result_dir is not None and result_dir.is_dir():
            payload["result_dir"] = str(result_dir)
            self.append_log(f"CFA/Stack Batch finished: {result_dir}")
            self.automatic_stack_finished.emit(True, str(result_dir), payload)
            return

        if success:
            message = "CFA Channels / Stack finished, but no result folder was found."
        self.append_log(f"ERROR: CFA/Stack Batch failed: {message}")
        self.automatic_stack_finished.emit(False, message, payload)

    def current_extremum_fit(self) -> dict[str, object] | None:
        """Return the current structured extremum fit result for plugins."""

        if self.current_extremum_fit_result is None:
            return None
        current = self.current_export_lightcurve()
        if current is None:
            return None
        current_csv, _target_name = current
        if self.current_extremum_fit_csv != current_csv:
            return None
        return dict(self.current_extremum_fit_result)

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"{timestamp}  {message}"
        self.log_lines.append(line)
        if self.is_closing:
            return
        self.log_view.append(line)
        self.update_overall_status()
        QApplication.processEvents()
        scroll_bar = self.log_view.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
        self.log_view.repaint()
        self.autosave_run_log()

    def log_geometry(self) -> None:
        """Write current window and splitter geometry to the app log."""

        window_size = self.size()
        window_hint = self.sizeHint()
        window_min_hint = self.minimumSizeHint()
        tab_index = self.tabs.currentIndex() if hasattr(self, "tabs") else -1
        tab_name = self.tabs.tabText(tab_index) if tab_index >= 0 else "-"
        tab_widget = self.tabs.currentWidget() if tab_index >= 0 else None
        tab_hint = tab_widget.sizeHint() if tab_widget is not None else None
        tab_min_hint = tab_widget.minimumSizeHint() if tab_widget is not None else None
        splitter = getattr(self, "main_splitter", None)
        splitter_sizes = splitter.sizes() if isinstance(splitter, QSplitter) else []
        tab_hint_text = (
            f"{tab_hint.width()}x{tab_hint.height()}"
            if tab_hint is not None
            else "-"
        )
        tab_min_hint_text = (
            f"{tab_min_hint.width()}x{tab_min_hint.height()}"
            if tab_min_hint is not None
            else "-"
        )
        self.append_log(
            "[GEOMETRY] "
            f"window={window_size.width()}x{window_size.height()} "
            f"sizeHint={window_hint.width()}x{window_hint.height()} "
            f"minHint={window_min_hint.width()}x{window_min_hint.height()} "
            f"splitter={','.join(str(size) for size in splitter_sizes) or '-'} "
            f"tab={tab_name} "
            f"tabHint={tab_hint_text} "
            f"tabMinHint={tab_min_hint_text}"
        )

    def update_overall_status(self) -> None:
        """Refresh the compact global status line above the log."""

        if getattr(self, "is_closing", False):
            return
        if not hasattr(self, "overall_status_label"):
            return

        if self.current_scan is None or self.current_scan.fits_count <= 0:
            source_text = "Src: -"
        else:
            if (
                self.photometry_mode == MODE_SINGLE_MEASUREMENT
                and self.current_scan.selected_fits is not None
            ):
                source_text = "Src: 1"
            else:
                source_text = f"Src: {self.current_scan.fits_count}"

        mode_text = (
            "Mode: Single"
            if self.photometry_mode == MODE_SINGLE_MEASUREMENT
            else "Mode: Curve"
        )
        if self.varstars_status == "detecting" or self.solve_worker is not None:
            varstars_text = "Vars: ..."
        elif self.varstars_status == "failed":
            varstars_text = "Vars: ERR"
        elif self.catalog_objects:
            varstars_text = f"Vars: {len(self.catalog_objects)}"
        else:
            varstars_text = "Vars: -"

        if self.selected_target is None:
            target_text = "Target: -"
        else:
            target_text = "Target: OK"

        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            comp_text = "Comp: -"
        elif self.comp_status == "selecting":
            comp_text = "Comp: ..."
        elif self.comp_status == "failed":
            comp_text = "Comp: ERR"
        elif self.comp_status == "ready":
            comp_text = f"Comp: {len(self.comparison_stars)}"
        else:
            comp_text = "Comp: -"
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            check_text = "Check: -"
        elif self.comp_status == "selecting":
            check_text = "Check: ..."
        elif self.comp_status == "failed":
            check_text = "Check: ERR"
        else:
            check_text = f"Check: {'OK' if self.check_star is not None else '-'}"
        fit_text = f"Fit: {self.display_fit_status()}"

        result_text = f"Result: {self.display_result_status()}"
        if self.export_status == "creating":
            export_text = "Export: ..."
        elif self.export_status == "failed":
            export_text = "Export: ERR"
        elif self.current_exports:
            export_text = "Export: " + "+".join(
                label for label in ("AAVSO", "BAV") if label in self.current_exports
            )
        else:
            export_text = "Export: -"

        self.overall_status_label.setText(
            " <span style='color:#808893'>|</span> ".join(
                [
                    self.status_part(mode_text),
                    self.status_part(source_text),
                    self.status_part(varstars_text),
                    self.status_part(target_text),
                    self.status_part(comp_text),
                    self.status_part(check_text),
                    self.status_part(result_text),
                    self.status_part(fit_text),
                    self.status_part(export_text),
                ]
            )
        )
        self.update_current_result_displays()

    def update_current_result_displays(self) -> None:
        """Refresh the current-result displays after its active path changes."""

        self.update_footer_current_status_labels()

        instrument_label = getattr(self, "aavso_instrument_label", None)
        if instrument_label is not None:
            export_result = (
                None
                if self.current_result_origin == "diagnostic"
                else self.loaded_lightcurve_csv
            )
            instrument_label.setText(
                aavso_instrument_display_text(export_result)
            )

    def display_result_status(self) -> str:
        """Return the user-facing result state for the compact status line."""

        labels = {
            "none": "-",
            "creating": "...",
            "loaded": "OK",
            "created": "OK",
            "warning": "WARN",
            "failed": "ERR",
        }
        return labels.get(self.result_status, self.result_status)

    def display_fit_status(self) -> str:
        """Return the user-facing fit state for the compact status line."""

        labels = {
            "none": "-",
            "fitting": "...",
            "selected": "OK",
            "failed": "ERR",
        }
        return labels.get(self.fit_status, self.fit_status)

    def current_display_aperture_settings(self) -> ApertureSettings:
        """Return aperture settings currently relevant for display/diagnostics."""

        if (
            self.photometry_mode == MODE_LIGHTCURVE
            and self.series_optimized_aperture_settings is not None
        ):
            return self.series_optimized_aperture_settings
        return resolve_aperture_settings(self.reference_frame)

    def status_part(self, text: str) -> str:
        """Return one color-coded rich-text status segment."""

        _label, separator, raw_value = text.partition(":")
        value = raw_value.strip() if separator else text.strip()
        lowered = value.lower()
        if lowered in {"-", "err"}:
            color = "#d64242"
        elif lowered == "...":
            color = "#ffeb3b"
        elif lowered == "warn":
            color = "#c48700"
        else:
            color = "#2f9e44"
        return (
            f"<span>{html.escape(_label)}{html.escape(separator)} "
            f"<span style='color:{color}'>{html.escape(value)}</span></span>"
        )

    def reset_export_status(self) -> None:
        """Clear export markers for a new or changed result context."""

        self.current_exports.clear()
        self.export_status = "none"
        self.update_overall_status()

    def mark_export_status(self, label: str) -> None:
        """Mark one export type as successfully created."""

        clean_label = label.strip().upper()
        if clean_label in {"AAVSO", "BAV"}:
            self.current_exports.add(clean_label)
            self.export_status = "done"
            self.update_overall_status()

    def set_varstars_status(self, status: str) -> None:
        if status in {"none", "detecting", "ready", "failed"}:
            self.varstars_status = status
            self.update_overall_status()

    def set_result_status(self, status: str) -> None:
        if status in {"none", "creating", "loaded", "created", "warning", "failed"}:
            self.result_status = status
            self.update_overall_status()

    def set_batch_result_status(self, status: str) -> None:
        """Map an aggregate Batch result to the compact global status."""

        result_status = {
            "OK": "created",
            "WARN": "warning",
            "ERR": "failed",
        }.get(str(status).strip().upper())
        if result_status is not None:
            self.set_result_status(result_status)

    def fail_lightcurve_run(self, message: str) -> None:
        """Mark the current light-curve run as failed and notify automation hooks."""

        self.set_result_status("failed")
        self.automatic_lightcurve_finished.emit(False, message)

    def show_lightcurve_failure_dialog(
        self,
        message: str,
        *,
        title: str = "Light Curve",
        critical: bool = False,
    ) -> None:
        """Show a manual-run failure without blocking an active target batch."""

        if self.batch_tab is not None and getattr(self.batch_tab, "running", False):
            return
        if critical:
            QMessageBox.critical(self, title, message)
        else:
            QMessageBox.warning(self, title, message)

    def set_comp_status(self, status: str) -> None:
        if status in {"none", "selecting", "ready", "failed"}:
            self.comp_status = status
            self.update_overall_status()
            QApplication.processEvents()

    def set_fit_status(self, status: str) -> None:
        if status in {"none", "fitting", "selected", "failed"}:
            self.fit_status = status
            self.update_overall_status()

    def set_export_process_status(self, status: str) -> None:
        if status in {"none", "creating", "failed"}:
            self.export_status = status
            self.update_overall_status()

    def autosave_run_log(self) -> None:
        """Persist the GUI log when a results directory is known."""

        output_dir = self.current_results_directory()
        if output_dir is None and self.loaded_lightcurve_csv is not None:
            output_dir = self.loaded_lightcurve_csv.parent
        if output_dir is None:
            return
        try:
            self.write_run_log(output_dir)
        except Exception:
            pass

    def write_run_log(self, output_dir: Path) -> Path:
        """Persist the current GUI log to the diagnostics directory."""

        diagnostics_dir = diagnostics_directory_for_results_dir(output_dir)
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        path = diagnostics_dir / "run.log"
        path.write_text("\n".join(self.log_lines) + "\n", encoding="utf-8")
        return path

    def ensure_plot_dialog(self) -> None:
        """Create and show the separate light-curve plot window."""

        if self.plot_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Light Curve Plot")
            dialog.resize(1100, 720)
            layout = QVBoxLayout(dialog)
            toolbar = NavigationToolbar(self.lightcurve_canvas, dialog)
            self.lightcurve_toolbar = toolbar
            layout.addWidget(toolbar)
            layout.addWidget(self.lightcurve_canvas, stretch=1)
            trim_hint = QLabel(
                "Before performing a Min/Max fit or export, you may select a suitable "
                "range of the light curve and use \"Trim to Selection\" to make it "
                "the new result range."
            )
            trim_hint.setWordWrap(True)
            layout.addWidget(trim_hint)
            self.plot_extremum_fit_label = QLabel(self.extremum_fit_label.text())
            self.plot_extremum_fit_label.setWordWrap(True)
            self.plot_extremum_fit_label.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            layout.addWidget(self.plot_extremum_fit_label)
            self.plot_dialog = dialog

        self.plot_dialog.show()
        self.plot_dialog.raise_()
        self.plot_dialog.activateWindow()
        self.reset_lightcurve_plot_navigation()

    def reset_lightcurve_plot_navigation(self) -> None:
        """Return the plot toolbar to selection mode so SpanSelector receives drags."""

        toolbar = self.lightcurve_toolbar
        if toolbar is None:
            return
        mode = getattr(toolbar, "mode", "")
        mode_name = str(getattr(mode, "name", mode)).lower()
        if "zoom" in mode_name:
            toolbar.zoom()
        elif "pan" in mode_name:
            toolbar.pan()
        try:
            self.lightcurve_canvas.setCursor(Qt.CursorShape.ArrowCursor)
        except Exception:
            pass

    def set_extremum_fit_text(self, text: str) -> None:
        """Show the current extremum fit text in the tab and plot dialog."""

        self.extremum_fit_label.setText(text)
        if self.plot_extremum_fit_label is not None:
            self.plot_extremum_fit_label.setText(text)

    def clear_single_result_summary(self) -> None:
        """Reset the single-measurement result summary."""

        if hasattr(self, "single_result_target_label"):
            self.set_single_result_quality_banner("", "")
            self.single_result_target_label.setText("Target: n/a")
            self.single_result_check_label.setText("Check: n/a")

    def set_single_result_quality_banner(self, status: str, quality_flag: str) -> None:
        """Show an unmistakable status banner above a Single Measurement result."""

        if not hasattr(self, "single_result_status_label"):
            return
        normalized = str(status or "").strip().upper()
        flags = str(quality_flag or "").strip()
        if "INFORMATIONAL_FIELD_ZP" in flags.split("|"):
            text = (
                "INFORMATIONAL ONLY — FIELD-ZP REFERENCES BELOW EXPORT SNR\n"
                "DIAGNOSTIC VALUE — NOT A VALID MEASUREMENT — EXPORT DISABLED"
            )
            style = (
                "color: #1f1600; background-color: #e0a72f; "
                "border: 2px solid #ffd166; border-radius: 5px; "
                "font-size: 15px; font-weight: 700; padding: 10px;"
            )
        elif normalized == QUALITY_STATUS_INVALID:
            if "TARGET_BLEND_MODELED_CONTAMINATION" in flags.split("|"):
                reason = "TARGET CONTAMINATION"
            elif "ANNULUS_CONTAMINATION" in flags.split("|"):
                reason = "ANNULUS CONTAMINATION"
            else:
                reason = flags or "INVALID TARGET MEASUREMENT"
            text = (
                f"REJECTED / INVALID — {reason}\n"
                "DIAGNOSTIC VALUE ONLY — NOT A VALID MEASUREMENT — EXPORT DISABLED"
            )
            style = (
                "color: #ffffff; background-color: #8b1e1e; "
                "border: 2px solid #ff6b6b; border-radius: 5px; "
                "font-size: 16px; font-weight: 700; padding: 10px;"
            )
        elif normalized == QUALITY_STATUS_WARNING:
            text = f"WARNING — {flags or 'CHECK QUALITY DETAILS'}"
            style = (
                "color: #1f1600; background-color: #e0a72f; "
                "border: 2px solid #ffd166; border-radius: 5px; "
                "font-size: 15px; font-weight: 700; padding: 8px;"
            )
        elif normalized == QUALITY_STATUS_OK:
            text = "MEASUREMENT STATUS: OK"
            style = (
                "color: #ffffff; background-color: #276749; "
                "border: 1px solid #68d391; border-radius: 5px; "
                "font-size: 14px; font-weight: 700; padding: 7px;"
            )
        else:
            text = "MEASUREMENT STATUS: N/A"
            style = (
                "color: #d8dee9; background-color: #353941; "
                "border: 1px solid #555b66; border-radius: 5px; "
                "font-weight: 600; padding: 6px;"
            )
        self.single_result_status_label.setText(text)
        self.single_result_status_label.setStyleSheet(style)

    def set_loaded_single_result_summary(self, result_csv: Path, rows: list[dict[str, str]]) -> None:
        """Show a compact summary for a loaded Single Measurement result CSV."""

        if not hasattr(self, "single_result_target_label"):
            return
        row = next(
            (
                item
                for item in rows
                if result_row_is_valid(item)
            ),
            rows[0] if rows else {},
        )
        mag = format_optional_float(first_result_row_float(row, "target_calibrated_mag", "calibrated_mag"), 4)
        err = format_optional_float(
            first_result_row_float(row, "target_calibrated_mag_error", "calibrated_mag_error"),
            4,
        )
        target_snr = format_optional_float(first_result_row_float(row, "target_snr"), 1)
        references = csv_text(row.get("field_reference_used"), "n/a")
        zp_scatter = csv_text(row.get("field_zero_point_scatter"), "n/a")
        image_source = csv_text(row.get("image_source"), "n/a")
        aavso_filter = csv_text(row.get("aavso_filter"), "n/a")
        row_valid = result_row_is_valid(row)
        quality_status = csv_text(row.get("quality_status"), QUALITY_STATUS_OK).upper()
        quality_flag = csv_text(row.get("quality_flag"), "")
        if not row_valid:
            quality_status = QUALITY_STATUS_INVALID
        self.set_single_result_quality_banner(quality_status, quality_flag)
        target_prefix = "Target: " if row_valid else "Diagnostic value only: "
        self.single_result_target_label.setText(
            f"{target_prefix}{mag} +/- {err} mag, SNR={target_snr}, refs={references}, "
            f"ZP scatter={zp_scatter} mag, filter={aavso_filter} ({image_source})"
        )

        check_id = aavso_safe_id(
            row.get("check_catalog_source"),
            row.get("check_catalog_id"),
            row.get("check_object_id"),
        )
        check_mag = csv_text(row.get("check_calibrated_mag"), "n/a")
        check_delta = csv_text(row.get("check_delta_mag"), "n/a")
        self.single_result_check_label.setText(
            f"Check: {check_id}, measured={check_mag}, delta={check_delta} mag"
        )
        self.append_log(f"Loaded Single Measurement summary from {result_csv.name}.")

    def set_single_result_summary(
        self,
        target: ApertureMeasurement,
        check: ApertureMeasurement | None,
        calibrated_mag: float,
        calibrated_error: float,
        check_calibrated_mag: float | None,
        check_delta_mag: float | None,
        zero_point_scatter: float,
        valid_comp_count: int,
        reference_label: str = "comps",
    ) -> None:
        """Show the important single-measurement result values in the GUI."""

        target_obj = self.selected_target.catalog_object if self.selected_target is not None else None
        target_bv = target_obj.color_index_float("mag_b", "mag_v") if target_obj is not None else None
        target_gr = target_obj.color_index_float("mag_g", "mag_r") if target_obj is not None else None
        check_bv = check.catalog_b_minus_v if check is not None else None
        check_gr = check.catalog_g_minus_r if check is not None else None
        check_id = "n/a" if check is None else (check.catalog_id or check.object_id or "n/a")
        check_catalog_mag = None if check is None else check.catalog_mag
        check_snr = None if check is None or not np.isfinite(check.snr) else check.snr

        target_status = measurement_quality_status(target)
        self.set_single_result_quality_banner(target_status, target.quality_flag)
        target_prefix = (
            "Diagnostic value only: "
            if target_status == QUALITY_STATUS_INVALID
            else "Target: "
        )

        self.single_result_target_label.setText(
            f"{target_prefix}{calibrated_mag:.4f} +/- {calibrated_error:.4f} mag, "
            f"SNR={target.snr:.1f}, {reference_label}={valid_comp_count}, "
            f"ZP scatter={zero_point_scatter:.4f} mag, "
            f"B-V={format_optional_float(target_bv)}, g-r={format_optional_float(target_gr)}"
        )
        self.single_result_check_label.setText(
            "Check: "
            f"{check_id}, "
            f"catalog V={format_optional_float(check_catalog_mag)}, "
            f"measured={format_optional_float(check_calibrated_mag, 4)}, "
            f"delta={format_optional_float(check_delta_mag, signed=True)} mag, "
            f"SNR={format_optional_float(check_snr, 1)}, "
            f"B-V={format_optional_float(check_bv)}, g-r={format_optional_float(check_gr)}, "
            f"dB-V target-check={color_delta_text(target_bv, check_bv)}, "
            f"dg-r target-check={color_delta_text(target_gr, check_gr)}"
        )

    def current_source_directory(self) -> Path | None:
        if self.current_scan is not None:
            return self.current_scan.directory
        path = self.source_path_from_edit()
        if path is not None:
            if self.photometry_mode == MODE_SINGLE_MEASUREMENT and path.is_file():
                return path.parent
            if self.photometry_mode != MODE_SINGLE_MEASUREMENT and path.is_dir():
                return path
        return None

    def current_source_fits_file(self) -> Path | None:
        if self.current_scan is not None and self.current_scan.selected_fits is not None:
            return self.current_scan.selected_fits
        if self.photometry_mode != MODE_SINGLE_MEASUREMENT:
            return None
        path = self.source_path_from_edit()
        if path is None:
            return None
        return path if is_fits_file(path) else None

    def original_fits_for_reference_frame(self) -> Path | None:
        """Return the original source FITS that corresponds to the current reference frame."""

        if self.current_scan is None or self.reference_frame is None:
            return None
        source_dir = self.current_scan.directory
        if not source_dir.is_dir():
            return None
        reference_index = self.reference_frame.index
        if reference_index < 1:
            return None
        original_fits_files = sorted(
            path
            for path in source_dir.iterdir()
            if is_fits_file(path) and not is_plate_solve_artifact(path)
        )
        original_index = reference_index - 1
        if original_index >= len(original_fits_files):
            return None
        return original_fits_files[original_index]

    def source_path_from_edit(self) -> Path | None:
        return path_from_text(self.source_dir_edit.text())

    def sync_source_edit_path(self) -> Path | None:
        path = self.source_path_from_edit()
        if path is None:
            return None
        self.source_dir_edit.setText(str(path))
        return path

    def remember_source_dialog_directory(self, path: Path | None) -> None:
        """Remember a sensible start directory for the next source picker."""

        if path is None:
            return
        if path.is_file():
            path = path.parent
        if path.is_dir():
            self.last_source_dialog_directory = path

    def source_dialog_start_directory(self, *, selecting_file: bool) -> str:
        current_path = self.sync_source_edit_path()
        if current_path is None:
            if self.last_source_dialog_directory is not None and self.last_source_dialog_directory.is_dir():
                return str(self.last_source_dialog_directory)
            return str(Path.home())
        if selecting_file:
            if current_path.is_file():
                return str(current_path.parent)
            if current_path.is_dir():
                return str(current_path)
            if current_path.parent.is_dir():
                return str(current_path.parent)
            return str(Path.home())
        if current_path.is_dir():
            return str(current_path)
        if current_path.parent.is_dir():
            return str(current_path.parent)
        return str(Path.home())

    def current_temp_directory(self) -> Path | None:
        source_dir = self.current_source_directory()
        return temp_directory_for(source_dir) if source_dir is not None else None

    def current_work_directory(self) -> Path | None:
        source_dir = self.current_source_directory()
        return temp_work_directory_for(source_dir) if source_dir is not None else None

    def current_results_directory(self) -> Path | None:
        source_dir = self.current_source_directory()
        return results_directory_for(source_dir) if source_dir is not None else None

    def mark_image_source_archived(self, source_dir: str | Path, archived_source_dir: str | Path) -> None:
        """Rebase the active source state after Archive Images renames a parent folder."""

        source_root = Path(source_dir).expanduser().resolve()
        archived_root = Path(archived_source_dir).expanduser().resolve()

        def rebase(path: Path | None) -> Path | None:
            if path is None:
                return None
            resolved = Path(path).expanduser()
            try:
                relative = resolved.resolve().relative_to(source_root)
            except ValueError:
                return path
            return archived_root / relative

        if self.current_scan is not None:
            rebased_directory = rebase(self.current_scan.directory)
            rebased_first_fits = rebase(self.current_scan.first_fits)
            rebased_selected_fits = rebase(self.current_scan.selected_fits)
            if (
                rebased_directory != self.current_scan.directory
                or rebased_first_fits != self.current_scan.first_fits
                or rebased_selected_fits != self.current_scan.selected_fits
            ):
                self.current_scan = replace(
                    self.current_scan,
                    directory=rebased_directory or self.current_scan.directory,
                    first_fits=rebased_first_fits,
                    selected_fits=rebased_selected_fits,
                )

        rebased_lightcurve_csv = rebase(self.loaded_lightcurve_csv)
        if rebased_lightcurve_csv is not None and rebased_lightcurve_csv != self.loaded_lightcurve_csv:
            self.loaded_lightcurve_csv = rebased_lightcurve_csv
            self.export_status_label.setText(f"Current export source: {rebased_lightcurve_csv}")
            self.lightcurve_status_label.setText(f"Light curve loaded: {rebased_lightcurve_csv}")
            self.update_current_result_displays()

        edit_path = self.source_path_from_edit()
        rebased_edit_path = rebase(edit_path)
        if rebased_edit_path is not None and rebased_edit_path != edit_path:
            self.source_dir_edit.setText(str(rebased_edit_path))
            self.remember_source_dialog_directory(rebased_edit_path)

    def selected_photometry_mode(self) -> str:
        mode = self.mode_combo.currentData() if hasattr(self, "mode_combo") else self.photometry_mode
        return mode if mode in {MODE_LIGHTCURVE, MODE_SINGLE_MEASUREMENT} else MODE_LIGHTCURVE

    def on_photometry_mode_changed(self) -> None:
        busy_message = self.busy_context_change_message("change the photometry mode")
        if busy_message is not None:
            previous_index = self.mode_combo.findData(self.photometry_mode)
            if previous_index >= 0:
                self.mode_combo.blockSignals(True)
                try:
                    self.mode_combo.setCurrentIndex(previous_index)
                finally:
                    self.mode_combo.blockSignals(False)
            QMessageBox.information(self, "Operation Running", busy_message)
            return

        previous_mode = self.photometry_mode
        next_mode = self.selected_photometry_mode()
        carried_single_fits: Path | None = None
        carried_vsx_selection: CatalogObject | None = None
        if (
            previous_mode == MODE_LIGHTCURVE
            and next_mode == MODE_SINGLE_MEASUREMENT
            and self.reference_frame is not None
            and self.reference_frame.path.exists()
        ):
            carried_single_fits = self.original_fits_for_reference_frame()
            if carried_single_fits is not None:
                current_vsx = self.current_vsx_selection()
                if current_vsx is not None:
                    carried_vsx_selection = CatalogObject(dict(current_vsx.values))
                elif self.selected_target is not None:
                    carried_vsx_selection = CatalogObject(dict(self.selected_target.catalog_object.values))

        self.photometry_mode = next_mode
        self.pending_single_vsx_selection = carried_vsx_selection
        if self.result_browser_dialog is not None:
            self.result_browser_dialog.close()
        if not self.is_busy() and carried_single_fits is None:
            self.close_siril_display_context(
                "mode change",
                safe_directory=self.current_source_directory(),
            )
        self.reset_scan_state()
        self.source_dir_edit.clear()
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            self.source_path_label.setText("FITS file:")
            self.source_dir_edit.setPlaceholderText("Select one plate-solved or solvable FITS image")
            self.scan_label.setText("No FITS file selected.")
            if carried_single_fits is not None:
                self.source_dir_edit.setText(str(carried_single_fits))
                self.append_log(
                    "Mode changed: Single Measurement; original source FITS for the current "
                    "reference frame selected as input."
                )
                self.scan_selected_directory(force=True)
            else:
                self.append_log("Mode changed: Single Measurement.")
        else:
            self.source_path_label.setText("FITS directory:")
            self.source_dir_edit.setPlaceholderText("Select the folder containing FITS images")
            self.scan_label.setText("No input directory selected.")
            self.append_log("Mode changed: Light Curve.")
        self.update_mode_dependent_controls()
        self.refresh_plugin_tab_views()
        self.update_overall_status()

    def update_mode_dependent_controls(self) -> None:
        """Enable controls that only make sense for the current photometry mode."""

        if not hasattr(self, "open_lightcurve_button"):
            return
        lightcurve_mode = self.photometry_mode == MODE_LIGHTCURVE
        self.open_lightcurve_button.setEnabled(True)
        self.open_lightcurve_button.setText(
            "Light Curve CSV" if lightcurve_mode else "Measurement CSV"
        )
        self.open_lightcurve_group.setEnabled(True)
        self.set_result_display_kind(single=not lightcurve_mode)
        if not lightcurve_mode:
            self.clear_comparison_star_state()
        self.run_light_curve_button.setText(
            "Create Light Curve" if lightcurve_mode else "Run Measurement"
        )
        self.refresh_action_availability()

    def set_result_display_kind(self, *, single: bool) -> None:
        """Show controls for the loaded/created result without changing acquisition mode."""

        if hasattr(self, "single_result_group"):
            self.single_result_group.setVisible(single)
        if hasattr(self, "plot_lightcurve_group"):
            self.plot_lightcurve_group.setVisible(not single)

    def clear_comparison_star_state(self) -> None:
        """Clear series-only comparison/check-star state."""

        self.comparison_stars = []
        self.check_star = None
        self.comparison_selection_failure_reason = ""
        self.series_optimized_aperture_settings = None
        self.comp_status = "none"
        if hasattr(self, "comp_tree"):
            self.comp_tree.clear()
        for button_name in (
            "show_selected_comp_button",
            "show_all_comp_button",
            "show_compstars_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(False)
        if self.compstars_status_label is not None:
            self.compstars_status_label.setText(self.compstars_summary_text())
        self.refresh_action_availability()

    def reset_scan_state(self) -> None:
        self.current_scan = None
        self.prepare_completed = False
        self.reference_frame = None
        self.clear_visible_vsx_targets()
        self.selected_target = None
        self.single_target_precheck_ready = False
        self.clear_comparison_star_state()
        self.set_selected_target_labels()
        self.prepare_button.setText("Run")
        self.prepare_description_label.setText(
            "Register frames · Plate solve · Load reference · Query VSX"
        )
        self.lightcurve_status_label.setText("No Light Curve generated yet.")
        self.export_status_label.setText("Exports the selected or opened Light Curve CSV as AAVSO Extended Format.")
        self.show_running_mean_checkbox.setChecked(False)
        self.binning_mode_combo.setCurrentIndex(0)
        self.binning_value_spin.setValue(10)
        self.binning_eligibility_label.clear()
        self.clear_single_result_summary()
        self.lightcurve_figure.clear()
        self.lightcurve_axis = None
        self.lightcurve_jd_values = None
        self.lightcurve_mag_values = None
        self.lightcurve_mag_errors = None
        self.current_extremum_fit_result = None
        self.current_extremum_fit_csv = None
        self.set_extremum_fit_text("Min/Max fit: none")
        self.loaded_lightcurve_csv = None
        self.loaded_lightcurve_target_name = "Target"
        self.loaded_lightcurve_source_label = ""
        self.current_result_origin = "none"
        self.varstars_status = "none"
        self.last_vsx_failure_message = ""
        self.result_status = "none"
        self.fit_status = "none"
        self.export_status = "none"
        self.current_exports.clear()
        self.lightcurve_selected_range = None
        self.lightcurve_span_selector = None
        self.lightcurve_fit_artists = []
        self.lightcurve_canvas.draw()
        self.refresh_action_availability()
        self.update_overall_status()

    def clear_visible_vsx_targets(self) -> None:
        """Clear Variables and its separate multi-target selection together."""

        self.catalog_objects = []
        self.filtered_catalog_objects = []
        self.vsx_tree.clear()
        self.vsx_filter_status_label.setText("Showing 0 / 0 VSX Objects")
        batch_tab = getattr(self, "batch_tab", None)
        if batch_tab is not None:
            batch_tab.reset_plugin_view()

    def close_siril_display_context(
        self,
        context: str,
        safe_directory: Path | None = None,
        log_success: bool = False,
    ) -> None:
        """Close the display and change only Siril's internal cwd out of temp."""

        siril = s.SirilInterface()
        connected = False
        try:
            connect_siril_interface(siril, self.append_log)
            connected = True
            if safe_directory is not None and safe_directory.is_dir():
                close_siril_image_and_change_cwd(siril, safe_directory)
            else:
                siril.cmd("close")
            if log_success:
                self.append_log(f"Siril display closed during {context}.")
        except Exception as exc:
            if log_success:
                self.append_log(f"WARNING: Could not close Siril display during {context}: {exc}")
        finally:
            if connected:
                try:
                    siril.disconnect()
                except Exception:
                    pass

    def reset_application_state(self) -> None:
        """Return the GUI to its initial state and remove temporary work files."""

        if self.batch_tab is not None and getattr(self.batch_tab, "running", False):
            QMessageBox.warning(self, "Reset", "Multiple-target photometry is still running.")
            return
        if self.solve_worker is not None and self.solve_worker.isRunning():
            QMessageBox.warning(
                self,
                "Reset",
                "Plate solving is still running.",
            )
            return
        if self.is_busy():
            QMessageBox.warning(
                self,
                "Reset",
                f"Another task is still running.\n\n{self.busy_message or self.busy_state}",
            )
            return

        reply = QMessageBox.question(
            self,
            "Reset",
            "Reset app and delete temporary files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        source_dir = self.current_source_directory()
        cleanup_directories = set(self.session_temp_directories)
        if source_dir is not None:
            cleanup_directories.add(temp_directory_for(source_dir))
        self.solve_worker = None
        self.close_siril_display_context(
            "reset",
            safe_directory=source_dir,
            log_success=True,
        )
        removed_temp_count = 0
        for tmp_dir in sorted(cleanup_directories, key=lambda path: str(path)):
            try:
                remove_temp_directory_tree(tmp_dir, tmp_dir.parent)
                self.session_temp_directories.discard(tmp_dir)
                removed_temp_count += 1
            except Exception as exc:
                self.append_log(
                    "WARNING: Could not remove temporary directory during reset "
                    f"for {tmp_dir}: {exc}"
                )

        if self.plot_dialog is not None:
            self.plot_dialog.close()
            self.plot_dialog = None
            self.plot_extremum_fit_label = None
        if self.compstars_dialog is not None:
            self.compstars_dialog.close()

        self.source_dir_edit.clear()
        self.scan_label.setText(
            "No FITS file selected."
            if self.photometry_mode == MODE_SINGLE_MEASUREMENT
            else "No input directory selected."
        )
        self.clear_vsx_filter(update_tree=False)
        self.reset_scan_state()
        self.reset_plugin_tab_views()
        self.update_mode_dependent_controls()
        self.tabs.setCurrentIndex(0)
        self.append_log(
            "Reset complete"
            + (
                f"; {removed_temp_count} temporary director"
                f"{'y' if removed_temp_count == 1 else 'ies'} removed."
                if removed_temp_count
                else "."
            )
        )

    def reset_plugin_tab_views(self) -> None:
        """Reset plugin views, including the separate multi-target dialog."""

        widgets = [self.tabs.widget(index) for index in range(self.tabs.count())]
        if self.batch_tab is not None:
            widgets.append(self.batch_tab)
        for widget in widgets:
            reset_view = getattr(widget, "reset_plugin_view", None)
            if not callable(reset_view):
                reset_view = getattr(widget, "reset_qc_view", None)
            if callable(reset_view):
                try:
                    reset_view()
                except Exception as exc:
                    self.append_log(f"WARNING: Plugin-tab reset failed: {exc}")

    def refresh_plugin_tab_views(self) -> None:
        """Refresh plugin-tab views that expose a refresh hook."""

        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            refresh_view = getattr(widget, "refresh_plugin_view", None)
            if callable(refresh_view):
                try:
                    refresh_view()
                except Exception as exc:
                    self.append_log(f"WARNING: Plugin-tab refresh failed: {exc}")

    def refresh_bav_tab_view(self) -> None:
        """Refresh the BAV view after the current result has changed."""

        refresh_view = getattr(self.bav_tab, "refresh_plugin_view", None)
        if callable(refresh_view):
            try:
                refresh_view()
            except Exception as exc:
                self.append_log(f"WARNING: BAV tab refresh failed: {exc}")

    def set_selected_target_labels(self, target_name: str | None = None) -> None:
        """Refresh the selected-target and current-result displays."""

        clean_name = str(target_name or "").strip()
        display_name = clean_name or "none"
        self.target_label.setText(f"Selected target: {display_name}")
        photometry_text = f"Target: {display_name}"
        elided_text = self.photometry_target_label.fontMetrics().elidedText(
            photometry_text,
            Qt.TextElideMode.ElideMiddle,
            self.photometry_target_label.maximumWidth(),
        )
        self.photometry_target_label.setText(elided_text)
        self.photometry_target_label.setToolTip(photometry_text)
        self.update_footer_current_status_labels(display_name)

    def update_footer_current_status_labels(
        self, target_name: str | None = None
    ) -> None:
        """Refresh the compact two-line current-target/current-curve footer."""

        target_label = getattr(self, "footer_current_target_label", None)
        curve_label = getattr(self, "footer_current_curve_label", None)
        if target_label is None or curve_label is None:
            return

        if target_name is None:
            # A loaded or newly created result remains the current target even
            # when no VSX target is selected in the present session.
            if self.loaded_lightcurve_csv is not None:
                target_name = self.loaded_lightcurve_target_name
            elif self.selected_target is not None:
                target_name = self.selected_target.catalog_object.name
            else:
                target_name = "none"
        target_display_name = str(target_name or "none").strip() or "none"
        target_prefix = "Current target: "
        curve_path = self.loaded_lightcurve_csv
        curve_prefix = "Current curve: "
        curve_display_name = "none" if curve_path is None else Path(curve_path).name
        for label, prefix, value, has_current_value in (
            (
                target_label,
                target_prefix,
                target_display_name,
                target_display_name != "none",
            ),
            (curve_label, curve_prefix, curve_display_name, curve_path is not None),
        ):
            value_width = max(
                0,
                label.width() - label.fontMetrics().horizontalAdvance(prefix),
            )
            compact_value = label.fontMetrics().elidedText(
                value, Qt.TextElideMode.ElideMiddle, value_width
            )
            if has_current_value:
                label.setText(
                    f"{html.escape(prefix)}<span style='color:#2f9e44'>"
                    f"{html.escape(compact_value)}</span>"
                )
            else:
                label.setText(f"{html.escape(prefix)}{html.escape(compact_value)}")
            if label is curve_label and curve_path is not None:
                label.setToolTip(str(curve_path))
            else:
                label.setToolTip(value if compact_value != value else "")

    def choose_source_input(self) -> None:
        busy_message = self.busy_context_change_message("load another source")
        if busy_message is not None:
            QMessageBox.information(self, "Operation Running", busy_message)
            return
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            self.choose_source_fits_file()
        else:
            self.choose_source_directory()

    def choose_source_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select FITS Input Directory",
            self.source_dialog_start_directory(selecting_file=False),
        )
        if not selected:
            return

        self.remember_source_dialog_directory(Path(selected))
        self.source_dir_edit.setText(selected)
        self.append_log(f"Selected input directory: {selected}")
        self.scan_selected_directory(force=True)

    def choose_source_fits_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select FITS Input File",
            self.source_dialog_start_directory(selecting_file=True),
            "FITS files (*.fit *.fits);;All files (*)",
        )
        if not selected:
            return

        self.remember_source_dialog_directory(Path(selected))
        self.source_dir_edit.setText(selected)
        self.append_log(f"Selected input FITS file: {selected}")
        self.scan_selected_directory(force=True)

    def scan_selected_directory(self, force: bool = False) -> None:
        if self.busy_context_change_message("load another source") is not None:
            return
        input_path = self.sync_source_edit_path()
        if input_path is None:
            if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
                QMessageBox.warning(self, "No FITS File", "Select a FITS input file first.")
            else:
                QMessageBox.warning(self, "No Directory", "Select a FITS folder first.")
            return

        try:
            if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
                selected_fits = input_path
                if not is_fits_file(selected_fits):
                    raise FileNotFoundError(f"Not a visible FITS file: {selected_fits}")
                if not force and self.current_scan is not None and self.current_scan.selected_fits is not None:
                    if same_filesystem_path(selected_fits, self.current_scan.selected_fits):
                        return
                scan = InputScan(
                    directory=selected_fits.parent,
                    fits_count=1,
                    first_fits=selected_fits,
                    selected_fits=selected_fits,
                )
            else:
                source_dir = input_path
                if not force and self.current_scan is not None:
                    if same_filesystem_path(source_dir, self.current_scan.directory):
                        return
                scan = scan_fits_directory(source_dir)
        except Exception as exc:
            self.reset_scan_state()
            self.scan_label.setText(
                "Input FITS file is not usable."
                if self.photometry_mode == MODE_SINGLE_MEASUREMENT
                else "Input directory is not usable."
            )
            self.append_log(f"ERROR: {exc}")
            QMessageBox.critical(
                self,
                "FITS File Error" if self.photometry_mode == MODE_SINGLE_MEASUREMENT else "Directory Error",
                str(exc),
            )
            return

        if scan.fits_count == 0:
            self.reset_scan_state()
            self.scan_label.setText("No .fit or .fits files found.")
            self.append_log(f"WARNING: No FITS files found in {scan.directory}")
            return

        self.current_scan = scan
        self.session_temp_directories.add(temp_directory_for(scan.directory))
        self.remember_source_dialog_directory(
            scan.selected_fits if scan.selected_fits is not None else scan.directory
        )
        tmp_dir = temp_directory_for(scan.directory)
        results_dir = results_directory_for(scan.directory)
        self.prepare_completed = False
        self.reference_frame = None
        self.clear_visible_vsx_targets()
        self.selected_target = None
        self.comparison_stars = []
        self.check_star = None
        self.series_optimized_aperture_settings = None
        self.set_selected_target_labels()
        self.comp_tree.clear()
        self.prepare_button.setText("Run")
        self.prepare_description_label.setText(
            "Register frames · Plate solve · Load reference · Query VSX"
        )
        self.lightcurve_status_label.setText(
            f"No target selected for {scan.directory.name}."
        )
        self.loaded_lightcurve_csv = None
        self.loaded_lightcurve_target_name = "Target"
        self.loaded_lightcurve_source_label = ""
        self.current_result_origin = "none"
        self.varstars_status = "none"
        self.comp_status = "none"
        self.result_status = "none"
        self.fit_status = "none"
        self.export_status = "none"
        self.current_exports.clear()
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT and scan.selected_fits is not None:
            self.scan_label.setText(f"Selected FITS file: {scan.selected_fits.name}")
            self.append_log(f"Selected one FITS file for single measurement: {scan.selected_fits}")
        else:
            self.scan_label.setText(f"Found {scan.fits_count} FITS file(s).")
            self.append_log(f"Found {scan.fits_count} FITS file(s) in {scan.directory}")
        self.append_log(f"Temporary work directory: {tmp_dir}")
        self.append_log(f"Results directory: {results_dir}")
        self.refresh_action_availability()

    def scan_entered_directory(self) -> None:
        if self.source_path_from_edit() is None:
            return
        self.scan_selected_directory()

    def set_source_directory_for_automation(self, directory: str | Path) -> bool:
        """Select the last successful CFA/Stack Batch result as input."""

        if self.busy_context_change_message("load another source") is not None:
            return False
        path = Path(directory).expanduser()
        if not path.is_dir():
            self.append_log(f"ERROR: CFA/Stack Batch result directory not found: {path}")
            return False
        if self.photometry_mode != MODE_LIGHTCURVE:
            index = self.mode_combo.findData(MODE_LIGHTCURVE)
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)
        self.remember_source_dialog_directory(path)
        self.source_dir_edit.setText(str(path))
        self.append_log(f"CFA/Stack Batch result directory selected: {path}")
        self.scan_selected_directory(force=True)
        return self.current_scan is not None and self.current_scan.first_fits is not None

    def prepare_sequence(self) -> None:
        if self.batch_tab is not None and getattr(self.batch_tab, "running", False):
            QMessageBox.information(
                self, "Detect Variables", "Multiple-target photometry is still running."
            )
            return
        if self.solve_worker is not None:
            QMessageBox.information(self, "Detect Variables", "Plate solving is already running.")
            return

        if self.current_scan is None or self.current_scan.first_fits is None:
            self.scan_selected_directory()
            if self.current_scan is None or self.current_scan.first_fits is None:
                return

        if (
            self.prepare_completed
            and self.reference_frame is not None
            and self.reference_frame.path.exists()
        ):
            self.retry_vsx_query()
            return

        try:
            hints = read_plate_solve_hints(self.current_scan.first_fits)
        except Exception as exc:
            self.append_log(f"ERROR: Cannot read plate solving hints: {exc}")
            QMessageBox.critical(self, "Detect Variables", str(exc))
            return

        if not self.begin_busy_action(
            "FIELD_SETUP_RUNNING",
            "Detecting variables and plate-solving the work sequence.",
        ):
            return

        self.prepare_completed = False
        self.loaded_lightcurve_csv = None
        self.loaded_lightcurve_target_name = "Target"
        self.loaded_lightcurve_source_label = ""
        self.current_result_origin = "none"
        self.varstars_status = "detecting"
        self.last_vsx_failure_message = ""
        self.comp_status = "none"
        self.result_status = "none"
        self.fit_status = "none"
        self.export_status = "none"
        self.current_exports.clear()
        self.reference_frame = None
        self.clear_visible_vsx_targets()
        self.selected_target = None
        self.comparison_stars = []
        self.check_star = None
        self.series_optimized_aperture_settings = None
        self.set_selected_target_labels()
        self.comp_tree.clear()
        self.append_log("Detect Variables requested.")
        if not self.register_sequence(show_success=False):
            self.set_varstars_status("failed")
            self.finish_busy_action("FIELD_SETUP_RUNNING")
            return
        self.start_plate_solve(confirm=False, hints=hints)
        if self.solve_worker is None:
            self.finish_busy_action("FIELD_SETUP_RUNNING")

    def retry_vsx_query(self) -> None:
        """Refresh VSX while retaining the already registered and solved sequence."""

        added_targets = [obj for obj in self.catalog_objects if is_added_target(obj)]
        if not self.begin_busy_action(
            "FIELD_SETUP_RUNNING",
            "Retrying VSX query with the prepared reference frame.",
        ):
            return
        self.clear_visible_vsx_targets()
        self.selected_target = None
        self.single_target_precheck_ready = False
        self.set_selected_target_labels()
        self.set_varstars_status("detecting")
        self.last_vsx_failure_message = ""
        self.append_log(
            "Reusing registered and plate-solved work files; registration, alignment, "
            "and plate solving are not repeated."
        )
        success = False
        message = "VSX query failed."
        try:
            if self.load_reference_frame() and self.show_vsx_overlay():
                self.set_varstars_status("ready")
                success = True
                message = "VSX objects loaded from the prepared reference frame."
            else:
                self.set_varstars_status("failed")
                message = self.last_vsx_failure_message or (
                    "VSX query failed; prepared work files were retained."
                )
                self.append_log(message)
        finally:
            self.catalog_objects.extend(added_targets)
            if added_targets:
                self.apply_vsx_filter()
            self.finish_busy_action("FIELD_SETUP_RUNNING")
        self.automatic_prepare_finished.emit(success, message)

    def start_plate_solve(
        self,
        confirm: bool = True,
        hints: PlateSolveHints | None = None,
    ) -> None:
        if self.solve_worker is not None:
            QMessageBox.information(self, "Detect Variables", "Plate solving is already running.")
            return

        if self.current_scan is None or self.current_scan.first_fits is None:
            self.scan_selected_directory()
            if self.current_scan is None or self.current_scan.first_fits is None:
                return
        work_dir = temp_work_directory_for(self.current_scan.directory)
        if not work_sequence_exists(work_dir):
            QMessageBox.warning(
                self,
                "Detect Variables",
                "Prepare the FITS sequence first.",
            )
            return

        if hints is None:
            try:
                hints = read_plate_solve_hints(self.current_scan.first_fits)
            except Exception as exc:
                self.append_log(f"ERROR: Cannot read plate solving hints: {exc}")
                QMessageBox.critical(self, "Detect Variables", str(exc))
                return

        if confirm:
            message = (
                "Detect variables in this FITS sequence?\n\n"
                "Temporary work files may be updated."
            )
            reply = QMessageBox.question(
                self,
                "Detect Variables",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.append_log("Starting per-frame plate solve.")
        self.append_log(
            "Using solver scale hints from "
            f"{hints.source_file.name}: focal={hints.focal_mm:g}, "
            f"pixel={hints.pixel_size_um:g}, "
            f"scale={pixel_scale_arcsec_per_pixel(hints):.3f} arcsec/pixel. "
            "RA/Dec are read per frame."
        )

        self.solve_worker = PlateSolveWorker(work_dir, hints)
        self.solve_worker.log.connect(self.append_log)
        self.solve_worker.finished.connect(self.on_plate_solve_thread_finished)
        self.solve_worker.start()

    def on_plate_solve_thread_finished(self) -> None:
        """Continue in the GUI only after the worker released its Siril connection."""

        worker = self.solve_worker
        if worker is None:
            return
        worker.wait()
        result = worker.result or (
            False,
            "Prepare failed: plate-solve worker ended without a result.",
        )
        worker.deleteLater()
        self.solve_worker = None
        self.on_plate_solve_finished(*result)

    def on_plate_solve_finished(self, success: bool, message: str) -> None:
        self.append_log(message)
        work_dir = (
            temp_work_directory_for(self.current_scan.directory)
            if self.current_scan is not None
            else None
        )
        reference_frame = (
            find_best_reference_frame(work_dir, self.append_log)
            if self.current_scan is not None and work_dir is not None
            else None
        )
        self.reference_frame = reference_frame
        self.prepare_completed = success and reference_frame is not None
        if success and reference_frame is None:
            self.append_log(
                "WARNING: Prepare finished without a usable WCS reference frame."
            )
        if success and self.prepare_completed:
            self.prepare_button.setText("Query VSX again")
            self.prepare_description_label.setText(
                "Prepared frames retained · refreshes only the VSX catalog"
            )
            self.append_log("Loading variable table after field setup.")
            if self.load_reference_frame() and self.show_vsx_overlay():
                self.set_varstars_status("ready")
            else:
                self.set_varstars_status("failed")
                self.append_log(
                    "VSX loading failed; prepared work files retained for a "
                    "catalog-only retry."
                )
        elif success:
            self.set_varstars_status("failed")
            QMessageBox.warning(self, "Detect Variables", "No usable reference frame found.")
        else:
            self.set_varstars_status("failed")
            QMessageBox.critical(self, "Detect Variables", f"Detect Variables failed.\n\n{message}")
        self.finish_busy_action("FIELD_SETUP_RUNNING")
        completion_message = (
            message
            if self.varstars_status == "ready"
            else self.last_vsx_failure_message or message
        )
        self.automatic_prepare_finished.emit(
            bool(success and self.prepare_completed and self.varstars_status == "ready"),
            completion_message,
        )

    def register_sequence(self, show_success: bool = True) -> bool:
        self.append_log("Register requested.")
        if self.current_scan is None:
            self.scan_selected_directory()
            if self.current_scan is None:
                return False

        siril = s.SirilInterface()
        connected = False
        try:
            connect_siril_interface(siril, self.append_log)
            connected = True
            close_siril_image_and_change_cwd(siril, self.current_scan.directory)
            if (
                self.photometry_mode == MODE_SINGLE_MEASUREMENT
                and self.current_scan.selected_fits is not None
            ):
                self.append_log("Linking selected FITS file for single measurement.")
                work_dir, link_source_dir = prepare_single_fits_work_directory(
                    self.current_scan.directory,
                    self.current_scan.selected_fits,
                )
                input_count = 1
                run_register = False
            else:
                self.append_log("Linking and registering input sequence.")
                work_dir, input_count = prepare_temp_work_directory(
                    self.current_scan.directory
                )
                link_source_dir = self.current_scan.directory
                run_register = True
            ensure_results_directory(self.current_scan.directory)
            self.append_log(
                f"Prepared clean work directory for {input_count} input FITS file(s): {work_dir}"
            )
            work_dir.mkdir(parents=True, exist_ok=True)
            if not work_dir.is_dir():
                raise FileNotFoundError(f"Work directory was not created: {work_dir}")
            self.append_log(f"Work directory exists: {work_dir}")
            self.append_log(f"> cd \"{siril_path(link_source_dir)}\"")
            siril.cmd(f'cd "{siril_path(link_source_dir)}"')
            link_output = "../work" if not run_register else f"../{TMP_DIRECTORY_NAME}/work"
            self.append_log(f"> link {PLATE_SOLVE_SEQUENCE_NAME} -out={link_output}")
            siril.cmd(f"link {PLATE_SOLVE_SEQUENCE_NAME} -out={link_output}")
            self.append_log(f"> cd \"{siril_path(work_dir)}\"")
            siril.cmd(f'cd "{siril_path(work_dir)}"')
            if run_register:
                self.append_log(f"> register {WORK_SEQUENCE_NAME} -2pass")
                siril.cmd(f"register {WORK_SEQUENCE_NAME} -2pass")
            else:
                self.append_log("Single Measurement mode: skipping sequence registration.")
        except Exception as exc:
            self.append_log(f"ERROR: register failed: {exc}")
            QMessageBox.critical(self, "Prepare", f"Could not prepare FITS sequence.\n\n{exc}")
            return False
        finally:
            if connected:
                try:
                    siril.disconnect()
                except Exception:
                    pass

        work_dir = temp_work_directory_for(self.current_scan.directory)
        if work_sequence_exists(work_dir):
            setup_label = (
                "Single-image work sequence ready"
                if self.photometry_mode == MODE_SINGLE_MEASUREMENT
                else "Registration metadata complete"
            )
            self.append_log(
                f"{setup_label} for work sequence: {WORK_SEQUENCE_NAME}"
            )
            self.append_log(f"Work sequence directory: {work_dir}")
            self.prepare_completed = False
            if show_success:
                QMessageBox.information(
                    self,
                    "Prepare",
                    "FITS sequence prepared.",
                )
            return True
        else:
            message = f"Siril did not create work sequence {WORK_SEQUENCE_NAME}"
            self.append_log(f"ERROR: {message}")
            QMessageBox.warning(self, "Prepare", "Could not create FITS sequence.")
            return False

    def load_reference_frame(self) -> bool:
        if self.current_scan is None:
            self.scan_selected_directory()
            if self.current_scan is None:
                return False

        if not self.prepare_completed:
            message = "Field setup has not completed successfully. Run Detect Variables first."
            self.append_log(f"WARNING: {message}")
            QMessageBox.warning(self, "Detect Variables", "Run Detect Variables first.")
            return False

        frame = self.reference_frame
        if frame is not None and frame.path.exists():
            self.append_log(f"Using prepared reference frame: {frame.path.name}")
        else:
            work_dir = temp_work_directory_for(self.current_scan.directory)
            frame = find_best_reference_frame(work_dir, self.append_log)
        if frame is None:
            message = (
                "Field setup completed, but no prepared WCS reference frame is available. "
                "Run Detect Variables again."
            )
            self.append_log(f"ERROR: {message}")
            QMessageBox.warning(
                self,
                "Detect Variables",
                "No usable reference frame found.\n\nRun Detect Variables again.",
            )
            self.prepare_completed = False
            return False

        siril = s.SirilInterface()
        connected = False
        try:
            connect_siril_interface(siril, self.append_log)
            connected = True
            self.append_log(f"Loading reference frame: {frame.path.name}")
            siril.cmd(f'load "{siril_path(frame.path)}"')
            siril.cmd("autostretch")
        except Exception as exc:
            self.append_log(f"ERROR: Could not load reference frame: {exc}")
            QMessageBox.critical(self, "Detect Variables", f"Could not load reference frame.\n\n{exc}")
            return False
        finally:
            if connected:
                try:
                    siril.disconnect()
                except Exception:
                    pass

        self.reference_frame = frame
        self.append_log(
            f"Reference frame loaded: {frame.path.name}; "
            f"index={frame.index:05d}; size={frame.width}x{frame.height}; "
            f"WCS center RA={frame.ra_deg:.8f}, Dec={frame.dec_deg:.8f}"
        )
        return True

    def show_vsx_overlay(self) -> bool:
        self.last_vsx_failure_message = ""
        if self.reference_frame is None:
            message = "No reference frame loaded. Run Detect Variables first."
            self.last_vsx_failure_message = message
            self.append_log(f"WARNING: {message}")
            QMessageBox.warning(self, "Detect Variables", "Run Detect Variables first.")
            return False

        try:
            self.append_log("Querying live AAVSO VSX for variable table.")
            objects = vizier_vsx_objects_for_table(self.reference_frame, self.append_log)
        except Exception as exc:
            message = f"VSX catalog unavailable: {exc}"
            self.last_vsx_failure_message = message
            self.append_log(f"ERROR: {message}")
            QMessageBox.critical(
                self,
                "VSX Catalog Unavailable",
                "The VSX variable-star catalog could not be reached after all retries.\n\n"
                f"{exc}\n\n"
                "The prepared frames were retained; click Query VSX again to retry.",
            )
            return False

        raw_count = len(objects)
        try:
            aperture_settings = require_fixed_fwhm_aperture_settings(self.reference_frame)
        except RuntimeError as exc:
            message = str(exc)
            self.last_vsx_failure_message = message
            self.append_log(f"ERROR: {message}")
            QMessageBox.warning(
                self,
                "Detect Variables",
                f"Could not prepare photometry settings.\n\n{message}",
            )
            return False
        usable_margin_px = aperture_settings.annulus_outer_px + PHOTOMETRY_EDGE_MARGIN_EXTRA_PX
        objects, outside_count = filter_objects_inside_reference_frame(
            objects,
            self.reference_frame,
            usable_margin_px,
        )
        if outside_count:
            self.append_log(
                f"VSX footprint filter removed {outside_count}/{raw_count} object(s) "
                f"outside the usable reference image area "
                f"(margin {usable_margin_px:.1f}px)."
            )

        if not objects:
            if raw_count == 0:
                message = (
                    "The VSX catalog query completed but returned no variable-star rows "
                    "after all retries."
                )
                dialog_message = (
                    "The VSX catalog returned no variable-star rows after all retries.\n\n"
                    "This does not mean that the plate-solved image contains no stars.\n\n"
                    "The prepared frames were retained; click Query VSX again to retry."
                )
                dialog_title = "No VSX Variables Returned"
            else:
                message = (
                    "No VSX objects fall inside the usable photometry area of the "
                    "reference image."
                )
                dialog_message = "No variables found in the usable image area."
                dialog_title = "Detect Variables"
            self.last_vsx_failure_message = message
            self.append_log(f"WARNING: {message}")
            QMessageBox.warning(
                self,
                dialog_title,
                dialog_message,
            )
            return False

        self.catalog_objects = objects
        self.filtered_catalog_objects = objects
        self.selected_target = None
        self.comparison_stars = []
        self.check_star = None
        self.series_optimized_aperture_settings = None
        self.set_selected_target_labels()
        self.comp_tree.clear()
        self.clear_vsx_filter(update_tree=False)
        self.apply_vsx_filter()
        self.restore_pending_vsx_selection()
        self.append_log(f"VSX table populated: {self.vsx_tree.topLevelItemCount()} row(s).")
        self.tabs.setCurrentWidget(self.varstar_tab)
        self.append_log(
            f"VSX objects loaded: {len(objects)} from live AAVSO VSX "
            f"(limit magnitude {DEFAULT_VSX_LIMIT_MAG:g}; "
            f"{outside_count} outside the usable reference area filtered out)"
        )
        self.refresh_plugin_tab_views()
        return True

    def populate_vsx_tree(self, objects: list[CatalogObject]) -> None:
        self.filtered_catalog_objects = objects
        self.vsx_tree.setSortingEnabled(False)
        self.vsx_tree.clear()
        catalog_index_by_id = {id(obj): index for index, obj in enumerate(self.catalog_objects)}
        for obj in objects:
            item = VsxTreeWidgetItem(
                [
                    f"{obj.name} [{obj.catalog_source}]" if is_added_target(obj) else obj.name,
                    obj.object_type,
                    obj.period,
                    vsx_magnitude_display(obj),
                    obj.ra,
                    obj.dec,
                ]
            )
            item.setData(0, VSX_CATALOG_INDEX_ROLE, catalog_index_by_id.get(id(obj), -1))
            magnitude = obj.magnitude_float()
            if magnitude is not None:
                item.setData(VSX_MAG_COLUMN, VSX_MAG_SORT_ROLE, magnitude)
            self.vsx_tree.addTopLevelItem(item)
        self.vsx_tree.setSortingEnabled(True)
        for column in range(self.vsx_tree.columnCount()):
            self.vsx_tree.resizeColumnToContents(column)
        self.vsx_filter_status_label.setText(
            f"Showing {len(objects)} / {len(self.catalog_objects)} Targets"
        )

    def restore_pending_vsx_selection(self) -> None:
        """Restore a remembered VSX row after switching from series to single mode."""

        pending = self.pending_single_vsx_selection
        if pending is None:
            return
        self.pending_single_vsx_selection = None
        for row in range(self.vsx_tree.topLevelItemCount()):
            item = self.vsx_tree.topLevelItem(row)
            catalog_index = item.data(0, VSX_CATALOG_INDEX_ROLE)
            if not isinstance(catalog_index, int) or catalog_index < 0:
                continue
            if catalog_index >= len(self.catalog_objects):
                continue
            candidate = self.catalog_objects[catalog_index]
            if self.vsx_objects_match_for_restore(candidate, pending):
                self.vsx_tree.setCurrentItem(item)
                self.vsx_tree.scrollToItem(item)
                self.append_log(
                    "Restored VSX selection after mode change: "
                    f"{candidate.name or '(unnamed)'}."
                )
                return
        self.append_log(
            "Previous VSX selection was not found in the reloaded single-measurement field."
        )

    def vsx_objects_match_for_restore(self, candidate: CatalogObject, pending: CatalogObject) -> bool:
        """Return True if two VSX catalog objects identify the same row."""

        candidate_oid = candidate.value_for(("OID", "oid")).strip()
        pending_oid = pending.value_for(("OID", "oid")).strip()
        if candidate_oid and pending_oid:
            return candidate_oid == pending_oid

        if candidate.name.strip().casefold() != pending.name.strip().casefold():
            return False
        return self.catalog_coordinate_texts_match(
            candidate.ra,
            pending.ra,
        ) and self.catalog_coordinate_texts_match(candidate.dec, pending.dec)

    @staticmethod
    def catalog_coordinate_texts_match(first: str, second: str) -> bool:
        """Compare catalog coordinate text robustly enough for a reloaded VSX row."""

        first_text = first.strip()
        second_text = second.strip()
        if not first_text or not second_text:
            return first_text == second_text
        try:
            return abs(float(first_text) - float(second_text)) <= 1e-7
        except ValueError:
            return first_text == second_text

    def filter_token_matches(
        self,
        value: str,
        pattern_text: str,
        *,
        allow_substring_match: bool = True,
    ) -> bool:
        patterns = [
            pattern.strip().lower()
            for pattern in pattern_text.split(",")
            if pattern.strip()
        ]
        if not patterns:
            return True
        normalized = value.lower()
        for pattern in patterns:
            if pattern.endswith("*"):
                if normalized.startswith(pattern[:-1]):
                    return True
            elif normalized == pattern:
                return True
            elif allow_substring_match and pattern in normalized:
                return True
        return False

    @staticmethod
    def vsx_type_is_certain(value: str) -> bool:
        """Return whether VSX reports one definite variability classification."""

        normalized = value.strip()
        return bool(normalized) and ":" not in normalized and "|" not in normalized

    def apply_vsx_filter(self) -> None:
        name_filter = self.vsx_name_filter_edit.text().strip()
        type_filter = self.vsx_type_filter_edit.text().strip()
        certain_types_only = (
            str(self.vsx_type_preset_combo.currentData() or "")
            == SINGLE_MODE_VSX_TYPE_SUGGESTION
            and type_filter == SINGLE_MODE_VSX_TYPE_SUGGESTION
        )
        mag_text = self.vsx_mag_filter_edit.text().strip().replace(",", ".")
        max_mag: float | None = None
        if mag_text:
            try:
                max_mag = float(mag_text)
            except ValueError:
                QMessageBox.warning(self, "VSX Filter", "Invalid magnitude limit.")
                return

        filtered: list[CatalogObject] = []
        for obj in self.catalog_objects:
            if is_added_target(obj):
                filtered.append(obj)
                continue
            if name_filter and not self.filter_token_matches(obj.name, name_filter):
                continue
            if type_filter and not self.filter_token_matches(
                obj.object_type,
                type_filter,
                allow_substring_match=False,
            ):
                continue
            if certain_types_only and not self.vsx_type_is_certain(obj.object_type):
                continue
            if max_mag is not None:
                mag = obj.magnitude_float()
                if mag is None or mag > max_mag:
                    continue
            filtered.append(obj)

        self.selected_target = None
        self.comparison_stars = []
        self.check_star = None
        self.series_optimized_aperture_settings = None
        self.set_selected_target_labels()
        self.comp_tree.clear()
        self.populate_vsx_tree(filtered)
        self.refresh_batch_targets_from_vsx_filter()
        self.append_log(
            f"VSX filter applied: showing {len(filtered)}/{len(self.catalog_objects)} object(s)."
        )
        self.refresh_action_availability()

    def refresh_batch_targets_from_vsx_filter(self) -> None:
        """Let the required Batch component mirror the current visible VSX rows."""

        batch_tab = self.batch_tab
        if batch_tab is None:
            return
        refresh = getattr(batch_tab, "sync_visible_targets", None)
        if callable(refresh):
            refresh()

    def on_vsx_type_preset_changed(self, _index: int) -> None:
        """Apply the selected VSX type-filter preset."""

        preset_text = str(self.vsx_type_preset_combo.currentData() or "")
        if not preset_text:
            self.vsx_name_filter_edit.clear()
            self.vsx_mag_filter_edit.clear()
        self.vsx_type_filter_edit.setText(preset_text)
        self.apply_vsx_filter()

    def clear_vsx_filter(self, *, update_tree: bool = True) -> None:
        self.vsx_name_filter_edit.clear()
        self.vsx_type_filter_edit.clear()
        self.vsx_mag_filter_edit.clear()
        if hasattr(self, "vsx_type_preset_combo"):
            was_blocked = self.vsx_type_preset_combo.blockSignals(True)
            self.vsx_type_preset_combo.setCurrentIndex(0)
            self.vsx_type_preset_combo.blockSignals(was_blocked)
        if update_tree:
            self.apply_vsx_filter()

    def annotation_label(
        self,
        obj: CatalogObject,
        fallback: str,
    ) -> str:
        label = obj.name.strip() or fallback
        if obj.object_type:
            label = f"{label} ({obj.object_type.strip()})"
        return label

    def write_annotation_csv(
        self,
        objects: list[tuple[str, CatalogObject]],
        filename: str,
    ) -> Path:
        if self.reference_frame is None:
            raise RuntimeError("No reference frame is loaded.")
        output_path = self.reference_frame.path.parent / filename
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("name", "ra", "dec"))
            writer.writeheader()
            for fallback, obj in objects:
                writer.writerow(
                    {
                        "name": self.annotation_label(obj, fallback),
                        "ra": obj.ra,
                        "dec": obj.dec,
                    }
                )
        return output_path

    def show_annotation_objects(
        self,
        objects: list[tuple[str, CatalogObject]],
        title: str,
        filename: str,
        log_success: bool = True,
    ) -> None:
        busy_message = self.busy_context_change_message("show Siril annotations")
        if busy_message is not None:
            if title != "VSX Preview":
                QMessageBox.information(self, "Operation Running", busy_message)
            return
        if self.reference_frame is None:
            QMessageBox.warning(self, title, "Run Detect Variables first.")
            return
        if not objects:
            QMessageBox.warning(self, title, "No objects to show.")
            return

        for fallback, obj in objects:
            aperture_settings = resolve_aperture_settings(self.reference_frame)
            usable_margin_px = aperture_settings.annulus_outer_px + PHOTOMETRY_EDGE_MARGIN_EXTRA_PX
            x, y, problem = object_pixel_position_in_frame(
                obj,
                self.reference_frame,
                usable_margin_px,
            )
            if problem is not None:
                label = self.annotation_label(obj, fallback)
                message = (
                    f"Cannot annotate {label}: {problem}. "
                    "The object is outside the usable photometry area of the reference image."
                )
                self.append_log(f"WARNING: {message}")
                QMessageBox.warning(self, title, "Object is outside the usable image area.")
                return

        try:
            output_path = self.write_annotation_csv(objects, filename)
        except Exception as exc:
            self.append_log(f"ERROR: Could not write annotation list: {exc}")
            QMessageBox.critical(self, title, f"Could not write annotation list.\n\n{exc}")
            return

        siril = s.SirilInterface()
        connected = False
        try:
            connect_siril_interface(siril, self.append_log)
            connected = True
            siril.cmd(f'load "{siril_path(self.reference_frame.path)}"')
            siril.cmd("autostretch")
            siril.cmd(f"show -clear -list={output_path.name}")
        except Exception as exc:
            message = (
                f"Could not show annotations in Siril from {output_path.name}: {exc}. "
                "Coordinates were valid in the reference WCS; this is likely a Siril display command error."
            )
            self.append_log(f"ERROR: {message}")
            QMessageBox.critical(self, title, f"Could not show annotations in Siril.\n\n{exc}")
            return
        finally:
            if connected:
                try:
                    siril.disconnect()
                except Exception:
                    pass

        if log_success:
            self.append_log(f"Displayed {len(objects)} annotation(s) in Siril from {output_path.name}.")

    def open_selected_vsx_page(self) -> None:
        obj = self.current_vsx_selection()
        if obj is None:
            QMessageBox.warning(self, "Open VSX", "Select a variable first.")
            return
        if is_added_target(obj):
            QMessageBox.warning(self, "Open VSX", "This target has no VSX entry.")
            return

        oid = obj.value_for(("OID", "oid")).strip()
        if not oid:
            self.append_log(
                f"VSX OID missing for {obj.name or '(unnamed)'}; querying VizieR by position."
            )
            try:
                oid = query_vizier_vsx_oid(obj)
            except Exception as exc:
                self.append_log(f"ERROR: Could not query VSX OID: {exc}")
                QMessageBox.warning(self, "Open VSX", f"Could not query VSX OID: {exc}")
                return

            if oid:
                updated_values = dict(obj.values)
                updated_values["OID"] = oid
                updated_obj = CatalogObject(updated_values)
                selected_items = self.vsx_tree.selectedItems()
                if selected_items:
                    row_index = selected_items[0].data(0, VSX_CATALOG_INDEX_ROLE)
                    if isinstance(row_index, int) and 0 <= row_index < len(self.catalog_objects):
                        self.catalog_objects[row_index] = updated_obj

        if not oid:
            QMessageBox.warning(
                self,
                "Open VSX",
                "Could not find the VSX page.",
            )
            return

        url = aavso_vsx_detail_url(oid)
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "Open VSX", "Could not open VSX page.")
            return
        self.append_log(f"Opened AAVSO VSX page for {obj.name or '(unnamed)'}: {url}")

    def open_current_result_vsx_page(self) -> None:
        """Open the VSX page for the currently loaded or generated result."""

        current = self.current_display_result()
        if current is None:
            QMessageBox.warning(self, "Open VSX", "Open a result first.")
            return

        result_csv, target_name = current
        metadata = read_result_metadata_header(result_csv)
        try:
            url = current_result_vsx_url(metadata)
        except ValueError as exc:
            self.append_log(f"WARNING: Result has no current VSX metadata: {exc}")
            QMessageBox.warning(
                self,
                "Open VSX",
                f"VSX is unavailable for this result.\n\n{exc}",
            )
            return

        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self, "Open VSX", "Could not open VSX page.")
            return
        self.append_log(f"Opened AAVSO VSX page for loaded result {target_name}: {url}")

    def on_vsx_selection_changed(self) -> None:
        obj = self.current_vsx_selection()
        self.selected_target = None
        self.single_target_precheck_ready = False
        self.comparison_stars = []
        self.check_star = None
        self.series_optimized_aperture_settings = None
        self.set_selected_target_labels()
        self.clear_single_result_summary()
        self.comp_tree.clear()
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            self.loaded_lightcurve_csv = None
            self.loaded_lightcurve_target_name = "Target"
            self.loaded_lightcurve_source_label = ""
            self.current_result_origin = "none"
            self.result_status = "none"
            self.fit_status = "none"
            self.export_status = "none"
            self.current_exports.clear()
            self.lightcurve_status_label.setText(
                "Select a target or run a new single measurement."
                if obj is not None
                else "Select a target for single measurement."
            )
            self.update_overall_status()
        elif self.loaded_lightcurve_csv is not None and self.loaded_lightcurve_csv.exists():
            self.lightcurve_status_label.setText(
                f"Loaded Light Curve available: {self.loaded_lightcurve_csv}"
            )
        else:
            self.lightcurve_status_label.setText("Select a target to plot an existing Light Curve.")
        self.refresh_action_availability()
        if obj is None:
            self.vsx_preview_timer.stop()
            return
        self.vsx_preview_timer.start(150)
        self.append_log(
            "Highlighted VSX object: "
            f"{obj.name or '(unnamed)'} "
            f"type={obj.object_type or '-'} "
            f"period={obj.period or '-'} "
            f"mag={vsx_magnitude_display(obj) or '-'} "
            f"oid={obj.value_for(('OID', 'oid')) or '-'} "
            f"RA={obj.ra or '-'} Dec={obj.dec or '-'}"
        )

    def on_vsx_double_clicked(self, _item: QTreeWidgetItem, _column: int) -> None:
        """Select the double-clicked VSX row as the current target."""

        self.select_target()

    def preview_selected_vsx_in_siril(self) -> None:
        obj = self.current_vsx_selection()
        if obj is None:
            return
        self.show_annotation_objects(
            [("V", obj)],
            "VSX Preview",
            "varstar_selected_overlay.csv",
            log_success=False,
        )

    def current_vsx_selection(self) -> CatalogObject | None:
        selected = self.vsx_tree.selectedItems()
        if not selected:
            return None
        row_index = selected[0].data(0, VSX_CATALOG_INDEX_ROLE)
        if not isinstance(row_index, int) or row_index >= len(self.catalog_objects):
            return None
        if row_index < 0:
            return None
        return self.catalog_objects[row_index]

    def selected_vsx_targets_for_batch(self) -> list[CatalogObject]:
        """Return unique VSX rows selected for the required Batch component."""

        return [obj for obj in self.vsx_targets_from_items_for_batch(self.vsx_tree.selectedItems()) if not is_added_target(obj)]

    def visible_vsx_targets_for_batch(self) -> list[CatalogObject]:
        """Return unique VSX rows currently visible in the Variables table."""

        return [obj for obj in self.vsx_targets_from_items_for_batch(
            self.vsx_tree.topLevelItem(row)
            for row in range(self.vsx_tree.topLevelItemCount())
        ) if not is_added_target(obj)]

    def vsx_targets_from_items_for_batch(self, items: Iterable[QTreeWidgetItem]) -> list[CatalogObject]:
        """Return unique catalog objects represented by VSX tree items."""

        targets: list[CatalogObject] = []
        seen_indexes: set[int] = set()
        for item in items:
            row_index = item.data(0, VSX_CATALOG_INDEX_ROLE)
            if not isinstance(row_index, int) or row_index < 0:
                continue
            if row_index in seen_indexes or row_index >= len(self.catalog_objects):
                continue
            seen_indexes.add(row_index)
            targets.append(self.catalog_objects[row_index])
        return targets

    def selected_comparison_catalog_source(self) -> str | None:
        """Return the configured catalog for comp/check-star discovery."""

        selected = COMPARISON_CATALOG_SOURCE
        if selected is None:
            return None
        if selected in COMPARISON_CATALOG_QUERY_CHOICES:
            return selected
        self.append_log(
            "WARNING: Invalid COMPARISON_CATALOG_SOURCE; using auto catalog selection."
        )
        return None

    def single_target_precheck_measurement(self, target: CatalogObject) -> ApertureMeasurement | None:
        """Measure the selected target once in single mode before compstar search."""

        if self.reference_frame is None:
            return None
        try:
            target_ra, target_dec = coordinate_pair(target, "Target")
        except ValueError as exc:
            self.append_log(f"ERROR: {exc}")
            return None
        aperture_settings = resolve_aperture_settings(self.reference_frame)
        measurements = aperture_measurements_for_frame(
            self.reference_frame.path,
            self.reference_frame.index,
            [
                {
                    "object_id": target.name or "target",
                    "role": "target",
                    "ra_deg": target_ra,
                    "dec_deg": target_dec,
                    "catalog_mag": target.magnitude_float(),
                    "catalog_source": target.catalog_source or "VSX",
                    "catalog_id": target.catalog_id if is_added_target(target) else target.value_for(("OID", "oid")) or target.name,
                }
            ],
            aperture_settings.aperture_radius_px,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
        )
        return measurements[0] if measurements else None

    def single_annulus_mask_candidate_for_target(
        self,
        frame_path: Path,
        measurement: ApertureMeasurement,
        aperture_settings: ApertureSettings,
    ) -> SingleAnnulusMaskCandidate | None:
        """Find the one safely maskable bright island for a rejected target."""

        try:
            _header, data, _wcs, _saturation = read_loaded_photometry_frame(frame_path)
            cutout, local_x, local_y, _x0, _y0 = image_cutout_for_radius(
                data, measurement.x, measurement.y, aperture_settings.annulus_outer_px
            )
        except Exception as exc:
            self.append_log(f"WARNING: Could not inspect target annulus for masking: {exc}")
            return None
        ring = annulus_mask(
            cutout.shape,
            local_x,
            local_y,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
        )
        return single_annulus_mask_candidate(cutout, ring, measurement.background_median)

    def confirm_single_annulus_mask(self, candidate: SingleAnnulusMaskCandidate) -> bool:
        """Offer the only user choice needed for a safely isolated ring source."""

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("Bright Sources in Background Annulus")
        if candidate.island_count == 1:
            dialog.setText("One separate bright source lies in the background annulus.")
        else:
            dialog.setText(
                f"{candidate.island_count} separate bright sources lie in the background annulus."
            )
        dialog.setInformativeText(
            "SeePhot can automatically mask these regions. "
            "The remaining background annulus is sufficient for the measurement."
        )
        dialog.setDetailedText(
            f"Detected bright regions: {candidate.island_count}; "
            "automatically masked annulus area: "
            f"{candidate.masked_pixel_fraction:.1%}; remaining annulus: "
            f"{candidate.remaining_pixel_fraction:.1%}."
        )
        use_mask = dialog.addButton("Use Cleaned Background", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("Discard Measurement", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(use_mask)
        dialog.exec()
        return dialog.clickedButton() is use_mask

    def single_measurement_work_frame(self) -> tuple[Path, int]:
        """Return the solved work frame used by single-image photometry."""

        work_dir = self.work_sequence_directory()
        if work_dir is None:
            raise ValueError("Select a FITS input file first.")
        sequence_files = solved_sequence_fits_files(work_dir, WORK_SEQUENCE_NAME)
        if not sequence_files:
            raise ValueError(f"No plate-solved work FITS found for {WORK_SEQUENCE_NAME}")
        frame_path = sequence_files[0]
        return frame_path, sequence_index(frame_path) or 1

    def single_measurement_specs(
        self,
        image_source: str = "",
        aavso_filter: str = "",
    ) -> list[dict[str, object]]:
        """Return object specs for the current single-image target/comp/check set."""

        if self.selected_target is None:
            raise ValueError("Select a VSX target first.")
        target_ra, target_dec = coordinate_pair(
            self.selected_target.catalog_object,
            "Target",
        )
        specs: list[dict[str, object]] = [
            {
                "object_id": self.selected_target.catalog_object.name or "target",
                "role": "target",
                "ra_deg": target_ra,
                "dec_deg": target_dec,
                "catalog_mag": self.selected_target.catalog_object.magnitude_float(),
                "catalog_source": self.selected_target.catalog_object.catalog_source or "VSX",
                "catalog_id": (
                    self.selected_target.catalog_object.catalog_id
                    if is_added_target(self.selected_target.catalog_object)
                    else self.selected_target.catalog_object.value_for(("OID", "oid"))
                    or self.selected_target.catalog_object.name
                ),
                "image_source": image_source,
                "aavso_filter": aavso_filter,
            }
        ]
        return specs

    def optimize_series_ringset(
        self,
        sequence_files: list[Path],
        target_ra: float,
        target_dec: float,
        comp_rows: list[tuple[int, CatalogObject, float, float]],
        check_row: tuple[CatalogObject, float, float] | None,
        image_source: str,
        aavso_filter: str,
    ) -> ApertureSettings:
        """Select one aperture/annulus ringset for the full light-curve sequence."""

        self.series_optimized_aperture_settings = None
        if self.reference_frame is None or self.selected_target is None:
            return resolve_aperture_settings(self.reference_frame)

        default_settings = resolve_aperture_settings(self.reference_frame)
        candidates = series_ringset_candidate_settings(default_settings)
        if len(candidates) <= 1:
            self.append_log(
                "Series ringset optimization skipped: "
                "reference FWHM unavailable; using default aperture/annulus."
            )
            return default_settings

        sample_files = evenly_sample_paths(sequence_files, SERIES_RINGSET_SAMPLE_FRAMES)
        if not sample_files:
            self.append_log(
                "Series ringset optimization skipped: no solved sample frames available."
            )
            return default_settings

        target_name = self.selected_target.catalog_object.name.strip() or "target"
        target_catalog_mag = self.selected_target.catalog_object.magnitude_float()
        target_catalog_id = self.selected_target.catalog_object.value_for(("OID", "oid")) or target_name
        specs: list[dict[str, object]] = [
            {
                "object_id": target_name,
                "role": "target",
                "ra_deg": target_ra,
                "dec_deg": target_dec,
                "catalog_mag": target_catalog_mag,
                "catalog_source": "VSX",
                "catalog_id": target_catalog_id,
                "image_source": image_source,
                "aavso_filter": aavso_filter,
            }
        ]
        for comp_index, comp, comp_ra, comp_dec in comp_rows:
            spec = candidate_catalog_spec(comp)
            spec["object_id"] = comp.catalog_id.strip() or comp.name.strip() or f"comp_{comp_index:02d}"
            spec["role"] = "comparison"
            spec["ra_deg"] = comp_ra
            spec["dec_deg"] = comp_dec
            spec["image_source"] = image_source
            spec["aavso_filter"] = aavso_filter
            specs.append(spec)
        if check_row is not None:
            check_obj, check_ra, check_dec = check_row
            spec = candidate_catalog_spec(check_obj)
            spec["object_id"] = check_obj.catalog_id.strip() or check_obj.name.strip() or "check"
            spec["role"] = "check"
            spec["ra_deg"] = check_ra
            spec["dec_deg"] = check_dec
            spec["image_source"] = image_source
            spec["aavso_filter"] = aavso_filter
            specs.append(spec)

        loaded_samples: list[tuple[Path, int, fits.Header, np.ndarray, WCS, float]] = []
        for sample_path in sample_files:
            frame_index = sequence_index(sample_path)
            if frame_index is None:
                continue
            try:
                header, data, wcs, saturation_threshold = read_loaded_photometry_frame(sample_path)
            except Exception as exc:
                self.append_log(
                    f"WARNING: Series ringset sample skipped: {sample_path.name}: {exc}"
                )
                continue
            loaded_samples.append((sample_path, frame_index, header, data, wcs, saturation_threshold))

        if not loaded_samples:
            self.append_log(
                "Series ringset optimization skipped: no readable sample frames available."
            )
            return default_settings

        self.append_log(
            f"Series ringset optimization started: {SERIES_RINGSET_RULE_ID}, "
            f"{len(candidates)} ringsets, {len(loaded_samples)}/{len(sequence_files)} "
            "sample frame(s)."
        )
        results: list[SeriesRingsetOptimizationResult] = []
        for settings in candidates:
            sample_measurements: list[list[ApertureMeasurement]] = []
            for sample_path, frame_index, header, data, wcs, saturation_threshold in loaded_samples:
                sample_measurements.append(
                    aperture_measurements_from_loaded_frame(
                        sample_path,
                        frame_index,
                        header,
                        data,
                        wcs,
                        saturation_threshold,
                        specs,
                        settings.aperture_radius_px,
                        settings.annulus_inner_px,
                        settings.annulus_outer_px,
                    )
                )
            results.append(score_series_ringset_measurements(settings, sample_measurements))

        default_result = results[0]
        best_result = min(results, key=lambda item: item.score)
        if (
            best_result.calibrated_frame_count == 0
            or best_result.target_valid_rate <= 0
            or best_result.comp_valid_frame_rate <= 0
        ):
            self.append_log(
                "WARNING: Series ringset optimization found no usable ringset; "
                "using default aperture/annulus."
            )
            return default_settings

        best_note_parts = [
            item
            for item in (
                best_result.settings.note,
                f"score={best_result.score:.3f}",
                f"default_score={default_result.score:.3f}",
                f"samples={best_result.calibrated_frame_count}/{best_result.sample_count}",
            )
            if item
        ]
        best_settings = ApertureSettings(
            best_result.settings.aperture_radius_px,
            best_result.settings.annulus_inner_px,
            best_result.settings.annulus_outer_px,
            best_result.settings.mode,
            best_result.settings.fwhm_px,
            "; ".join(best_note_parts),
        )
        self.series_optimized_aperture_settings = best_settings
        self.append_log(
            "Series ringset default: "
            f"{format_series_ringset_result(default_result)}."
        )
        self.append_log(
            "Series ringset selected: "
            f"{format_series_ringset_result(best_result)}."
        )
        return best_settings

    def select_target(self) -> None:
        obj = self.current_vsx_selection()
        if obj is None:
            QMessageBox.warning(self, "Select Target", "Select a variable first.")
            return
        if is_added_target(obj) and self.photometry_mode != MODE_SINGLE_MEASUREMENT:
            QMessageBox.warning(self, "Select Target", "Added targets are for Single Measurement only.")
            return
        self.set_selected_target_object(obj)

    def add_target(self) -> None:
        """Add a named single-measurement target from one of three position sources."""

        if self.photometry_mode != MODE_SINGLE_MEASUREMENT or self.reference_frame is None:
            QMessageBox.warning(self, "Add Target", "Prepare a field in Single Measurement mode first.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Target for Single Measurement")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        layout.addLayout(form)
        source = QComboBox()
        source.addItem("SIMBAD identifier", "SIMBAD")
        source.addItem("Gaia DR3 source ID", "GAIA_DR3")
        source.addItem("Manual RA / Dec", "MANUAL")
        form.addRow("Position source:", source)
        identifier = QLineEdit()
        identifier.setPlaceholderText("Object name or identifier")
        form.addRow("Identifier:", identifier)
        name = QLineEdit()
        name.setPlaceholderText("Required for manual coordinates")
        form.addRow("Target name:", name)
        object_type = QLineEdit()
        object_type.setPlaceholderText("Optional, e.g. nova candidate")
        form.addRow("Type (optional):", object_type)
        ra = QLineEdit()
        ra.setPlaceholderText("ICRS degrees or hh:mm:ss")
        form.addRow("RA (degrees):", ra)
        dec = QLineEdit()
        dec.setPlaceholderText("ICRS degrees or ±dd:mm:ss")
        form.addRow("Dec (degrees):", dec)

        def update_inputs(_index: int | None = None) -> None:
            manual = source.currentData() == "MANUAL"
            identifier.setEnabled(not manual)
            name.setEnabled(manual)
            ra.setEnabled(manual)
            dec.setEnabled(manual)

        source.currentIndexChanged.connect(update_inputs)
        update_inputs()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Resolve and Add")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def resolve_and_add() -> None:
            try:
                selected_source = str(source.currentData())
                if selected_source == "SIMBAD":
                    obj = resolve_simbad_target(identifier.text(), object_type.text())
                elif selected_source == "GAIA_DR3":
                    obj = resolve_gaia_dr3_target(identifier.text(), object_type.text())
                else:
                    ra_deg, dec_deg = parse_added_target_coordinates(ra.text(), dec.text())
                    obj = added_target_object(
                        name.text(), "MANUAL", name.text(), ra_deg, dec_deg,
                        object_type.text(),
                    )
                settings = resolve_aperture_settings(self.reference_frame)
                margin = settings.annulus_outer_px + PHOTOMETRY_EDGE_MARGIN_EXTRA_PX
                _x, _y, problem = object_pixel_position_in_frame(obj, self.reference_frame, margin)
                if problem is not None:
                    raise ValueError(f"Target is outside the usable image area: {problem}")
            except Exception as exc:
                self.append_log(f"WARNING: Add Target failed: {exc}")
                QMessageBox.warning(dialog, "Add Target", str(exc))
                return
            if QMessageBox.question(
                dialog, "Confirm Target",
                f"{obj.name}\n{obj.catalog_source}: {obj.catalog_id}\n"
                f"ICRS RA={obj.ra}°, Dec={obj.dec}°\n\nAdd this target?",
            ) != QMessageBox.StandardButton.Yes:
                return
            for existing in self.catalog_objects:
                if (existing.catalog_source, existing.catalog_id) == (obj.catalog_source, obj.catalog_id):
                    QMessageBox.warning(dialog, "Add Target", "This target is already in Variables.")
                    return
                if catalog_object_filename_stem(existing).casefold() == catalog_object_filename_stem(obj).casefold():
                    QMessageBox.warning(dialog, "Add Target", "A target with this result filename is already in Variables.")
                    return
            self.catalog_objects.append(obj)
            self.apply_vsx_filter()
            for index in range(self.vsx_tree.topLevelItemCount()):
                item = self.vsx_tree.topLevelItem(index)
                if self.catalog_objects[item.data(0, VSX_CATALOG_INDEX_ROLE)] is obj:
                    self.vsx_tree.setCurrentItem(item)
                    self.vsx_tree.scrollToItem(item)
                    break
            self.append_log(
                f"Added single-measurement target {obj.name}: "
                f"{obj.catalog_source}:{obj.catalog_id}, RA={obj.ra}, Dec={obj.dec}."
            )
            dialog.accept()

        buttons.accepted.connect(resolve_and_add)
        dialog.exec()

    def set_selected_target_object(self, obj: CatalogObject) -> bool:
        """Set the current target from a catalog object without relying on tree selection."""

        if is_added_target(obj) and self.photometry_mode != MODE_SINGLE_MEASUREMENT:
            self.show_lightcurve_failure_dialog("Added targets are for Single Measurement only.")
            return False
        if self.reference_frame is None:
            if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
                self.show_single_measurement_warning(
                    "Run Detect Variables first.",
                    title="Select Target",
                )
            else:
                self.show_lightcurve_failure_dialog(
                    "Run Detect Variables first.",
                    title="Select Target",
                )
            return False

        self.selected_target = SelectedTarget(obj, self.reference_frame)
        self.single_target_precheck_ready = False
        self.loaded_lightcurve_csv = None
        self.loaded_lightcurve_target_name = "Target"
        self.loaded_lightcurve_source_label = ""
        self.current_result_origin = "none"
        self.comp_status = "none"
        self.result_status = "none"
        self.fit_status = "none"
        self.export_status = "none"
        self.current_exports.clear()
        self.current_extremum_fit_result = None
        self.current_extremum_fit_csv = None
        self.set_extremum_fit_text("Min/Max fit: none")
        self.clear_comparison_star_state()
        self.set_selected_target_labels(obj.name or "(unnamed)")
        self.update_selected_target_lightcurve_status()
        self.refresh_action_availability()
        self.append_log(
            "Selected target: "
            f"{obj.name or '(unnamed)'} "
            f"type={obj.object_type or '-'} "
            f"period={obj.period or '-'} "
            f"mag={vsx_magnitude_display(obj) or '-'} "
            f"RA={obj.ra or '-'} Dec={obj.dec or '-'}"
        )
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            measurement = self.single_target_precheck_measurement(obj)
            if measurement is None:
                self.lightcurve_status_label.setText(
                    "Single target precheck failed: target could not be measured."
                )
                self.append_log("ERROR: Single target precheck failed before measurement.")
                self.show_single_measurement_warning(
                    "Target could not be measured.\n\nRun Measurement remains disabled.",
                )
                return False
            error_text = (
                "n/a"
                if measurement.inst_mag_error is None
                else f"{measurement.inst_mag_error:.3f} mag"
            )
            self.append_log(
                "Single target precheck: "
                f"quality={measurement.quality_flag}, "
                f"SNR={measurement.snr:.1f}, "
                f"inst_err={error_text}, "
                f"x={measurement.x:.1f}, y={measurement.y:.1f}."
            )
            precheck_mask_candidate = None
            if (
                not measurement.valid
                and measurement.quality_flag == "ANNULUS_CONTAMINATION"
            ):
                aperture_settings = resolve_aperture_settings(self.reference_frame)
                precheck_mask_candidate = self.single_annulus_mask_candidate_for_target(
                    self.reference_frame.path,
                    measurement,
                    aperture_settings,
                )
                if precheck_mask_candidate is not None:
                    self.append_log(
                        "Single target precheck found safely maskable annulus source(s): "
                        f"islands={precheck_mask_candidate.island_count}; "
                        "measurement remains available pending user confirmation."
                    )
            if not measurement.valid and precheck_mask_candidate is None:
                self.lightcurve_status_label.setText(
                    "Single target is not measurable: "
                    f"{measurement.quality_flag} ({measurement.note})."
                )
                self.append_log(
                    "ERROR: Single target is not measurable; measurement disabled: "
                    f"{measurement.quality_flag} ({measurement.note})."
                )
                self.show_single_measurement_warning(
                    "Target is not measurable.\n\n"
                    f"{measurement.quality_flag} ({measurement.note})\n\n"
                    "Run Measurement remains disabled.",
                )
                return False
            if np.isfinite(measurement.snr) and measurement.snr < SINGLE_TARGET_LOW_SNR_WARNING:
                self.lightcurve_status_label.setText(
                    "Single target measurable but weak: "
                    f"SNR={measurement.snr:.1f}, expected error={error_text}."
                )
                self.append_log(
                    "WARNING: Single target SNR is low for a useful single measurement: "
                    f"SNR={measurement.snr:.1f}, inst_err={error_text}."
                )
            self.single_target_precheck_ready = True
            self.refresh_action_availability()
            if precheck_mask_candidate is not None:
                self.lightcurve_status_label.setText(
                    "Single target selected with maskable annulus contamination. "
                    "Run Measurement will ask before cleaning the background."
                )
            else:
                self.lightcurve_status_label.setText(
                    "Single target selected. Run Measurement will use Field Zero Point calibration."
                )
        return True

    def select_target_by_name_for_automation(self, target_name: str) -> bool:
        """Select exactly one VSX target by name for the required Automatic assistant."""

        query = target_name.strip().lower()
        if not query:
            self.append_log("ERROR: Automatic target name is empty.")
            return False
        if not self.catalog_objects:
            self.append_log("ERROR: Automatic target selection failed: VSX table is empty.")
            return False
        exact_matches = [
            obj
            for obj in self.catalog_objects
            if obj.name.strip().lower() == query
        ]
        if not exact_matches:
            self.append_log(f"ERROR: Automatic target not found: {target_name}")
            return False
        if len(exact_matches) > 1:
            self.append_log(
                f"ERROR: Automatic target name is ambiguous: {target_name} "
                f"({len(exact_matches)} exact matches)."
            )
            return False
        return self.set_selected_target_object(exact_matches[0])

    def find_comparison_stars(self) -> bool:
        self.append_log("Selecting Comp Stars and Check Star for current Light Curve.")
        self.comparison_selection_failure_reason = ""
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            self.append_log("Comp Stars are not used in Single Measurement mode; Field ZP is automatic.")
            return False
        self.set_comp_status("selecting")
        if self.selected_target is None:
            self.set_comp_status("failed")
            self.show_lightcurve_failure_dialog(
                "Select a variable first.",
                title="Comp Stars",
            )
            return False
        if self.reference_frame is None:
            self.set_comp_status("failed")
            self.show_lightcurve_failure_dialog(
                "Run Detect Variables first.",
                title="Comp Stars",
            )
            return False

        catalog_source = self.selected_comparison_catalog_source()
        catalog_label = catalog_source or COMPARISON_CATALOG_AUTO_NAME
        cache_catalog = (catalog_source or "auto_apass_dr10_ucac4").lower().replace(" ", "_")
        target_stem = catalog_object_filename_stem(self.selected_target.catalog_object)
        output_csv = (
            self.reference_frame.path.parent
            / f"{COMPARISON_CATALOG_CSV_PREFIX}_{cache_catalog}_{target_stem}.csv"
        )
        target_mag = self.selected_target.catalog_object.magnitude_float()
        objects: list[CatalogObject] | None = None
        if output_csv.exists() and output_csv.stat().st_size > 0:
            try:
                objects = read_catalog_objects(output_csv)
                if objects:
                    self.append_log(
                        f"Reusing cached {catalog_label} candidates: {output_csv.name} "
                        f"({len(objects)} rows)."
                    )
                else:
                    self.append_log(
                        f"Ignoring empty cached {catalog_label} candidates: {output_csv.name}; "
                        "querying again."
                    )
                    objects = None
            except Exception as exc:
                self.append_log(f"WARNING: Could not read cached {catalog_label} CSV: {exc}")
                output_csv.unlink(missing_ok=True)

        if objects is None:
            try:
                radius_arcmin = reference_frame_search_radius_arcmin(self.reference_frame)
                self.append_log(
                    f"Querying {catalog_label} Comp Star catalog: "
                    f"center RA={self.reference_frame.ra_deg:.6f}, "
                    f"Dec={self.reference_frame.dec_deg:.6f}, "
                    f"radius={radius_arcmin:.1f} arcmin, "
                    f"target V={target_mag if target_mag is not None else 'n/a'}, "
                    f"dV={DEFAULT_COMP_DVMAG:g}, "
                    f"eV<={DEFAULT_COMP_EMAG:g}, "
                    f"max rows={APASS_DR10_MAX_QUERY_ROWS}."
                )
                objects = query_comparison_catalog_region(
                    self.reference_frame,
                    target_mag,
                    DEFAULT_COMP_DVMAG,
                    self.append_log,
                    catalog_source,
                )
                write_catalog_objects(output_csv, objects, COMPARISON_CATALOG_COLUMNS)
            except Exception as exc:
                message = catalog_query_error_message(catalog_label, exc)
                self.set_comp_status("failed")
                self.append_log(f"ERROR: {message}")
                self.show_lightcurve_failure_dialog(
                    f"Could not load Comp Stars.\n\n{message}",
                    title="Comp Stars",
                    critical=True,
                )
                return False
            if not objects:
                self.append_log(
                    f"{catalog_label} catalog query completed successfully: 0 candidates returned."
                )
            self.append_log(
                f"{catalog_label} candidates written: {output_csv.name} ({len(objects)} rows)."
            )

        raw_count = len(objects)
        if raw_count >= APASS_DR10_MAX_QUERY_ROWS:
            self.append_log(
                f"WARNING: {catalog_label} query reached the configured row limit; "
                "candidate selection uses the closest catalog magnitudes first."
            )
        self.append_log(
            f"Selecting Comp Stars and Check Star from {raw_count} {catalog_label} candidate(s)."
        )
        try:
            series_aperture_settings = require_fixed_fwhm_aperture_settings(self.reference_frame)
        except RuntimeError as exc:
            message = str(exc)
            self.set_comp_status("failed")
            self.append_log(f"ERROR: {message}")
            self.show_lightcurve_failure_dialog(
                message,
                title="Comparison Stars",
            )
            return False
        self.append_log(
            "Series comp/check fixed ringset: "
            f"{DEFAULT_RINGSET_RULE_ID}; "
            f"aperture={series_aperture_settings.aperture_radius_px:.2f}px, "
            f"annulus={series_aperture_settings.annulus_inner_px:.2f}-"
            f"{series_aperture_settings.annulus_outer_px:.2f}px, "
            f"fwhm={series_aperture_settings.fwhm_px:.2f}px."
        )
        candidate_pool, vetting_result = filter_comparison_candidates_with_vetting(
            objects,
            self.selected_target.catalog_object,
            DEFAULT_COMP_DVMAG,
            DEFAULT_COMP_EMAG,
            COMP_SELECTION_POOL_LIMIT,
            self.reference_frame.path.parent,
            self.reference_frame,
            self.append_log,
        )
        if should_try_post_vetting_ucac4(catalog_source, objects, len(candidate_pool)):
            self.append_log(
                "APASS DR10 kept only "
                f"{len(candidate_pool)} fully vetted candidate(s), fewer than "
                f"{COMPARISON_CATALOG_FALLBACK_MIN_CANDIDATES}; "
                "trying UCAC4 via VizieR with the same selection checks."
            )
            try:
                ucac4_objects = query_ucac4_region(
                    self.reference_frame,
                    target_mag,
                    DEFAULT_COMP_DVMAG,
                    self.append_log,
                )
                ucac4_pool, ucac4_vetting_result = filter_comparison_candidates_with_vetting(
                    ucac4_objects,
                    self.selected_target.catalog_object,
                    DEFAULT_COMP_DVMAG,
                    DEFAULT_COMP_EMAG,
                    COMP_SELECTION_POOL_LIMIT,
                    self.reference_frame.path.parent,
                    self.reference_frame,
                    self.append_log,
                )
                self.append_log(
                    "Post-vetting catalog comparison: "
                    f"APASS_DR10={len(candidate_pool)}, UCAC4={len(ucac4_pool)} "
                    "usable candidate(s)."
                )
                if len(ucac4_pool) > len(candidate_pool):
                    objects = ucac4_objects
                    raw_count = len(objects)
                    candidate_pool = ucac4_pool
                    vetting_result = ucac4_vetting_result
                    try:
                        write_catalog_objects(output_csv, objects, COMPARISON_CATALOG_COLUMNS)
                    except Exception as exc:
                        self.append_log(
                            f"WARNING: Could not update UCAC4 candidate cache {output_csv.name}: {exc}"
                        )
                    self.append_log(
                        "Using UCAC4 after full vetting."
                    )
                else:
                    self.append_log("Keeping APASS DR10 after full vetting.")
            except Exception as exc:
                self.append_log(
                    "WARNING: UCAC4 post-vetting fallback failed; "
                    f"keeping the APASS DR10 result: {exc}"
                )
        if len(candidate_pool) >= 4:
            if len(candidate_pool) > DEFAULT_COMP_COUNT:
                self.comparison_stars = candidate_pool[:DEFAULT_COMP_COUNT]
                check_candidates = candidate_pool[DEFAULT_COMP_COUNT:]
            else:
                self.comparison_stars = candidate_pool[:-1]
                check_candidates = candidate_pool[-1:]
                self.append_log(
                    "Only "
                    f"{len(candidate_pool)} photometrically vetted candidates remain; "
                    f"using {len(self.comparison_stars)} Comp Star(s) and testing "
                    "the last candidate as Check Star."
                )
            check_quality = select_check_star_against_ensemble(
                self.comparison_stars,
                check_candidates,
                self.reference_frame.path.parent,
                self.reference_frame,
                self.append_log,
                vetting_result,
            )
        else:
            self.comparison_stars = candidate_pool[:DEFAULT_COMP_COUNT]
            check_quality = None
        if check_quality is not None:
            self.check_star = check_quality.candidate
            self.append_log(
                "Check Star selected by ensemble vetting: "
                f"{self.check_star.catalog_id or self.check_star.name or '(unnamed)'} "
                f"delta={check_quality.median_delta:+.3f}, "
                f"scatter={check_quality.robust_scatter:.3f}, "
                f"n={check_quality.valid_count}/{check_quality.sample_count}."
            )
        else:
            self.check_star = None
            if len(candidate_pool) >= 4:
                self.append_log(
                    "WARNING: Check Star ensemble vetting found no robust independent candidate; "
                    "continuing without Check Star."
                )
        candidate_pool_count = len(candidate_pool)
        self.populate_comp_tree(self.comparison_stars)
        self.append_log(
            f"Comp Stars loaded: {len(self.comparison_stars)} from {output_csv.name} "
            f"({raw_count} {catalog_label} rows; {candidate_pool_count} candidate(s) "
            "passed visibility and photometric sample-frame vetting; "
            f"dV={DEFAULT_COMP_DVMAG:g}, max={DEFAULT_COMP_COUNT})"
        )
        if self.check_star is not None:
            self.append_log(
                "Check Star selected: "
                f"{self.check_star.catalog_id or self.check_star.name or '(unnamed)'} "
                f"mag={self.check_star.magnitude or '-'} "
                f"e_V={self.check_star.value_for(('err_mag_v',)) or '-'} "
                f"nobs={self.check_star.value_for(('nobs_v',)) or '-'} "
                f"RA={self.check_star.ra or '-'} Dec={self.check_star.dec or '-'}"
            )
        else:
            self.append_log("WARNING: No suitable Check Star available after Comp Star selection.")
        if len(self.comparison_stars) < MIN_VALID_COMP_STARS:
            self.set_comp_status("failed")
            self.comparison_selection_failure_reason = comparison_star_selection_failure_message(
                raw_count,
                len(self.comparison_stars),
            )
            self.append_log(
                f"WARNING: {self.comparison_selection_failure_reason}"
            )
            self.update_compstars_dialog_status()
            return False
        else:
            self.set_comp_status("ready")
            self.update_compstars_dialog_status()
            return True

    def populate_comp_tree(self, objects: list[CatalogObject]) -> None:
        self.comp_tree.clear()
        for row_index, obj in enumerate(objects):
            item = QTreeWidgetItem(
                [
                    obj.catalog_id or obj.name,
                    obj.magnitude,
                    obj.value_for(("err_mag_v",)),
                    obj.value_for(("nobs_v",)),
                    obj.ra,
                    obj.dec,
                    " | ".join(f"{key}={value}" for key, value in obj.values.items()),
                ]
            )
            item.setData(0, 0x0100, row_index)
            self.comp_tree.addTopLevelItem(item)
        if self.check_star is not None:
            check = self.check_star
            item = QTreeWidgetItem(
                [
                    f"Check: {check.catalog_id or check.name}",
                    check.magnitude,
                    check.value_for(("err_mag_v",)),
                    check.value_for(("nobs_v",)),
                    check.ra,
                    check.dec,
                    " | ".join(f"{key}={value}" for key, value in check.values.items()),
                ]
            )
            item.setData(0, 0x0100, "check")
            self.comp_tree.addTopLevelItem(item)
        for column in range(self.comp_tree.columnCount()):
            self.comp_tree.resizeColumnToContents(column)
        self.update_compstars_dialog_status()

    def current_comp_selection(self) -> CatalogObject | None:
        selected = self.comp_tree.selectedItems()
        if not selected:
            return None
        row_index = selected[0].data(0, 0x0100)
        if row_index == "check":
            return self.check_star
        if not isinstance(row_index, int) or row_index >= len(self.comparison_stars):
            return None
        return self.comparison_stars[row_index]

    def on_comp_selection_changed(self) -> None:
        self.refresh_action_availability()

    def compstars_summary_text(self) -> str:
        target = (
            self.selected_target.catalog_object.name.strip()
            if self.selected_target is not None
            else ""
        ) or "none"
        check_text = "yes" if self.check_star is not None else "no"
        if self.comp_status == "ready":
            status = "selected automatically"
        elif self.comp_status == "failed":
            status = "failed"
        elif self.comp_status == "selecting":
            status = "selecting"
        else:
            status = "not selected"
        return (
            f"Target: {target}\n"
            f"Status: {status}\n"
            f"Catalog: {COMPARISON_CATALOG_AUTO_NAME if COMPARISON_CATALOG_SOURCE is None else COMPARISON_CATALOG_SOURCE}\n"
            f"Comp Stars: {len(self.comparison_stars)}\n"
            f"Check Star: {check_text}"
        )

    def update_compstars_dialog_status(self) -> None:
        if self.compstars_status_label is not None:
            self.compstars_status_label.setText(self.compstars_summary_text())
        self.refresh_action_availability()

    def show_compstars_dialog(self) -> None:
        if not self.comparison_stars and self.check_star is None:
            QMessageBox.information(
                self,
                "Comp Stars",
                "Create a Light Curve first.",
            )
            return

        if self.compstars_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("Comp Stars")
            dialog.resize(980, 560)
            layout = QVBoxLayout(dialog)

            self.compstars_status_label = QLabel()
            self.compstars_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.compstars_status_label.setWordWrap(True)
            layout.addWidget(self.compstars_status_label)

            action_row = QHBoxLayout()
            action_row.addWidget(self.show_selected_comp_button)
            action_row.addWidget(self.show_all_comp_button)
            action_row.addStretch(1)
            layout.addLayout(action_row)
            layout.addWidget(self.comp_tree, stretch=1)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.close)
            layout.addWidget(buttons)
            self.compstars_dialog = dialog

        self.update_compstars_dialog_status()
        self.compstars_dialog.show()
        self.compstars_dialog.raise_()
        self.compstars_dialog.activateWindow()

    def target_annotation_object(self) -> tuple[str, CatalogObject] | None:
        if self.selected_target is None:
            return None
        return ("V", self.selected_target.catalog_object)

    def show_selected_compstar(self) -> None:
        target = self.target_annotation_object()
        comp = self.current_comp_selection()
        if target is None:
            QMessageBox.warning(self, "Show Selected", "Select a variable first.")
            return
        if comp is None:
            QMessageBox.warning(self, "Show Selected", "Select a Comp Star first.")
            return
        self.show_annotation_objects(
            [target, ("C", comp)],
            "Show Selected",
            "compstar_selected_overlay.csv",
        )

    def show_all_compstars(self) -> None:
        target = self.target_annotation_object()
        if target is None:
            QMessageBox.warning(self, "Show All", "Select a variable first.")
            return
        objects = [target] + [
            (f"C{index}", obj)
            for index, obj in enumerate(self.comparison_stars, start=1)
        ]
        if self.check_star is not None:
            objects.append(("K", self.check_star))
        self.show_annotation_objects(objects, "Show All", "compstars_overlay.csv")

    def lightcurve_output_directory(self) -> Path | None:
        source_dir = self.current_source_directory()
        return ensure_results_directory(source_dir) if source_dir is not None else None

    def selected_target_output_stem(self) -> str | None:
        if self.selected_target is None:
            return None
        return catalog_object_filename_stem(self.selected_target.catalog_object)

    def selected_target_result_csv(self) -> Path | None:
        output_dir = self.lightcurve_output_directory()
        stem = self.selected_target_output_stem()
        if output_dir is None or stem is None:
            return None
        if self.lightcurve_results_module is None:
            self.lightcurve_results_module = load_lightcurve_results_module()
        return Path(self.lightcurve_results_module.raw_lightcurve_result_path(output_dir, stem))

    def selected_target_diagnostic_curve_csv(self) -> Path | None:
        """Return the separate non-exportable target-contamination curve path."""

        output_dir = self.lightcurve_output_directory()
        stem = self.selected_target_output_stem()
        if output_dir is None or stem is None:
            return None
        return output_dir / f"{stem}_diagnostic_curve.csv"

    def start_light_curve_for_batch(self) -> None:
        """Run one batch light-curve target through the normal GUI busy path."""

        self.run_busy_action(
            "PHOTOMETRY_RUNNING",
            "Creating Light Curve.",
            self.run_light_curve,
        )

    def start_measurement_for_batch(self, mode: str) -> None:
        """Run one Batch target with the photometry mode frozen at Batch start."""

        selected_mode = self.selected_photometry_mode()
        if selected_mode != mode:
            self.append_log(
                "WARNING: Batch photometry mode differed from the current mode selector; "
                f"using {selected_mode}."
            )
        if selected_mode == MODE_SINGLE_MEASUREMENT:
            self.run_busy_action(
                "PHOTOMETRY_RUNNING",
                "Creating Single Measurement.",
                self.run_single_measurement_for_batch,
            )
            return
        self.start_light_curve_for_batch()

    def selected_target_single_result_csv(self) -> Path | None:
        output_dir = self.lightcurve_output_directory()
        stem = self.selected_target_output_stem()
        if output_dir is None or stem is None:
            return None
        if self.lightcurve_results_module is None:
            self.lightcurve_results_module = load_lightcurve_results_module()
        return Path(self.lightcurve_results_module.single_measurement_result_path(output_dir, stem))

    def show_single_measurement_warning(
        self,
        message: str,
        *,
        title: str = "Single Measurement",
    ) -> None:
        """Record a Single failure and avoid modal dialogs during a Batch."""

        self._single_measurement_failure_message = str(message)
        if self.batch_tab is not None and getattr(self.batch_tab, "running", False):
            return
        QMessageBox.warning(self, title, message)

    def run_single_measurement_for_batch(self) -> None:
        """Run Single Measurement and translate its result to the Batch signal."""

        result_csv = self.selected_target_single_result_csv()
        before = None
        if result_csv is not None and result_csv.exists():
            stat = result_csv.stat()
            before = (stat.st_mtime_ns, stat.st_size)
        self._single_measurement_failure_message = ""
        self.run_single_measurement()
        changed_result = False
        if result_csv is not None and result_csv.exists():
            stat = result_csv.stat()
            changed_result = before != (stat.st_mtime_ns, stat.st_size)
        if result_csv is not None and (self.result_status == "created" or changed_result):
            self.automatic_lightcurve_finished.emit(True, str(result_csv))
            return
        message = (
            self._single_measurement_failure_message
            or "Single Measurement failed. See the log for details."
        )
        self.automatic_lightcurve_finished.emit(False, message)

    def selected_target_instrumental_csv(self) -> Path | None:
        output_dir = self.lightcurve_output_directory()
        stem = self.selected_target_output_stem()
        if output_dir is None or stem is None:
            return None
        return diagnostics_directory_for_results_dir(output_dir) / f"{stem}_instrumental_photometry.csv"

    def selected_target_plot_png(self) -> Path | None:
        output_dir = self.lightcurve_output_directory()
        stem = self.selected_target_output_stem()
        if output_dir is None or stem is None:
            return None
        return output_dir / f"{stem}_result_curve.png"

    def diagnostic_result_csv(self) -> Path | None:
        """Return the current result CSV used for diagnostics."""

        current = self.current_export_lightcurve()
        if current is not None:
            return current[0]
        return self.selected_target_result_csv()

    def instrumental_csv_for_result_csv(self, result_csv: Path) -> Path:
        """Return the expected instrumental CSV path for one result CSV."""

        if self.lightcurve_results_module is None:
            self.lightcurve_results_module = load_lightcurve_results_module()
        resolver = getattr(
            self.lightcurve_results_module,
            "instrumental_csv_for_result_csv",
            None,
        )
        if not callable(resolver):
            raise RuntimeError("Central result-file contract is unavailable.")
        return Path(resolver(result_csv))

    def create_binned_curve(self) -> None:
        """Create and select a separately stored scientific flux-binned curve."""

        source_csv = self.loaded_lightcurve_csv
        if source_csv is None or not source_csv.exists():
            QMessageBox.warning(self, "Create Binned Curve", "Create or load a raw Light Curve first.")
            return
        source_metadata = read_result_metadata_header(source_csv)
        if source_metadata.get("SOURCE_PROVENANCE") != "SINGLE_FRAME_LG_SERIES" or source_metadata.get("BINNING_ALLOWED") != "1":
            provenance = source_metadata.get("SOURCE_PROVENANCE", "missing").strip() or "missing"
            allowed = source_metadata.get("BINNING_ALLOWED", "missing").strip() or "missing"
            message = (
                "Binning requires a current L/G single-frame result "
                "(FILTER=L or G, NCOMBINE=1).\n\n"
                f"This result has SOURCE_PROVENANCE={provenance}, "
                f"BINNING_ALLOWED={allowed}.\n\n"
                "Stacks, already binned results, and results created before the current "
                "provenance check are rejected."
            )
            self.binning_eligibility_label.setText(
                f"Not eligible: {provenance}; BINNING_ALLOWED={allowed}."
            )
            self.append_log(
                f"Binning rejected for {source_csv}: SOURCE_PROVENANCE={provenance}, "
                f"BINNING_ALLOWED={allowed}."
            )
            QMessageBox.warning(self, "Create Binned Curve", message)
            return
        instrumental_csv = self.instrumental_csv_for_result_csv(source_csv)
        if not instrumental_csv.exists():
            QMessageBox.warning(self, "Create Binned Curve", "Instrumental photometry CSV not found.")
            return
        try:
            source_dir = self.source_directory_for_result_csv(source_csv)
            source_input_files = (
                sorted(
                    path
                    for path in source_dir.iterdir()
                    if is_fits_file(path) and not is_plate_solve_artifact(path)
                )
                if source_dir is not None and source_dir.is_dir()
                else []
            )
            expected_input_fingerprint = source_metadata.get(
                "SOURCE_INPUT_FINGERPRINT_SHA256", ""
            ).strip()
            if not expected_input_fingerprint:
                raise ValueError(
                    "Source input fingerprint is missing; run single-frame L/G photometry again."
                )
            if frame_series_provenance(source_input_files) != "SINGLE_FRAME_LG_SERIES":
                raise ValueError("Selected source directory is no longer a L/G single-frame series.")
            if input_series_fingerprint(source_input_files) != expected_input_fingerprint:
                raise ValueError(
                    "Input FITS files changed since photometry; run single-frame photometry again."
                )
            module = load_measurement_binning_module()
            with instrumental_csv.open(newline="") as handle:
                instrumental_rows = list(csv_data_dict_reader(handle))
            fingerprint = module.source_fingerprint(instrumental_rows)
            scatter_rejected_frames = module.scatter_rejected_frame_indices(source_metadata)
            frames, excluded = module.calibrated_frames_from_instrumental_rows(
                instrumental_rows,
                excluded_frame_indices=scatter_rejected_frames,
            )
            mode = str(self.binning_mode_combo.currentData())
            value = int(self.binning_value_spin.value())
            groups, skipped = module.plan_bins(frames, mode, value)
            bins = [module.combine_flux_bin(group) for group in groups]
            if not bins:
                raise ValueError("No bin with at least two eligible original-image measurements was created.")
            with source_csv.open(newline="") as handle:
                source_reader = csv_data_dict_reader(handle)
                fieldnames = list(source_reader.fieldnames or [])
            for extra in (
                "bin_input_count",
                "bin_check_input_count",
                "bin_start_jd",
                "bin_end_jd",
                "bin_exposure_seconds",
                "bin_source_frames",
            ):
                if extra not in fieldnames:
                    fieldnames.append(extra)
            if self.lightcurve_results_module is None:
                self.lightcurve_results_module = load_lightcurve_results_module()
            output_path_builder = getattr(
                self.lightcurve_results_module,
                "binned_result_path",
                None,
            )
            if not callable(output_path_builder):
                raise RuntimeError("Central result-file contract is unavailable.")
            output_csv = Path(output_path_builder(source_csv, mode, value))
            metadata = dict(source_metadata)
            for trim_key in (
                "LIGHTCURVE_TRIMMED",
                "LIGHTCURVE_ORIGINAL_OBS_COUNT",
                "LIGHTCURVE_TRIM_JD_START",
                "LIGHTCURVE_TRIM_JD_END",
            ):
                metadata.pop(trim_key, None)
            metadata.update({
                "SOURCE_PROVENANCE": "BINNED_RESULT",
                "BINNING_ALLOWED": "0",
                "BIN_SOURCE_RESULT": source_csv.name,
                "BIN_SOURCE_FINGERPRINT_SHA256": fingerprint,
                "BINNING_MODE": mode,
                "BINNING_VALUE": value,
                "BINNING_GAP_RULE": "3x median eligible-input cadence; no cross-gap bin",
                "BINNING_FLUX_RULE": module.FLUX_RULE_ID,
                "BINNING_MIN_INPUTS": module.MIN_INPUTS_PER_BIN,
                "BINNING_EXCLUDED_INPUTS": excluded,
                "BINNING_SKIPPED_REST_BINS": skipped,
                "PHOTOMETRY_SOFTWARE": SOFTWARE_NAME,
            })
            rows: list[dict[str, object]] = []
            for index, item in enumerate(bins, start=1):
                row: dict[str, object] = {name: "" for name in fieldnames}
                bin_snr = item.flux / item.flux_error
                check_snr = (
                    item.check_flux / item.check_flux_error
                    if item.check_flux is not None and item.check_flux_error is not None
                    else None
                )
                check_delta_mag = (
                    item.check_magnitude - item.check_catalog_mag
                    if item.check_magnitude is not None and item.check_catalog_mag is not None
                    else None
                )
                check_valid = item.check_magnitude is not None
                bin_valid = (
                    np.isfinite(bin_snr)
                    and bin_snr >= MIN_PHOTOMETRY_SNR
                    and item.magnitude_error <= MAX_INSTRUMENTAL_MAG_ERROR
                )
                bin_flag = "OK"
                if not bin_valid:
                    bin_flag = "LOW_SNR" if bin_snr < MIN_PHOTOMETRY_SNR else "HIGH_MAG_ERROR"
                row.update({
                    "frame_index": index,
                    "filename": ";".join(frame.filename for frame in item.frames),
                    "image_source": item.frames[0].image_source,
                    "aavso_filter": item.frames[0].aavso_filter,
                    "jd": f"{item.jd:.8f}",
                    "target_calibrated_mag": f"{item.magnitude:.6f}",
                    "target_calibrated_mag_error": f"{item.magnitude_error:.6f}",
                    "target_inst_mag": "",
                    "target_inst_mag_error": "",
                    "target_snr": f"{bin_snr:.6f}",
                    "check_object_id": item.check_object_id,
                    "check_catalog_source": item.check_catalog_source,
                    "check_catalog_id": item.check_catalog_id,
                    "check_snr": "" if check_snr is None else f"{check_snr:.6f}",
                    "check_inst_mag_error": (
                        ""
                        if item.check_magnitude_error is None
                        else f"{item.check_magnitude_error:.6f}"
                    ),
                    "check_catalog_mag": (
                        "" if item.check_catalog_mag is None else f"{item.check_catalog_mag:.6f}"
                    ),
                    "check_catalog_mag_error": (
                        ""
                        if item.check_catalog_mag_error is None
                        else f"{item.check_catalog_mag_error:.6f}"
                    ),
                    "check_catalog_nobs": item.check_catalog_nobs,
                    "check_catalog_b_minus_v": (
                        ""
                        if item.check_catalog_b_minus_v is None
                        else f"{item.check_catalog_b_minus_v:.6f}"
                    ),
                    "check_catalog_g_minus_r": (
                        ""
                        if item.check_catalog_g_minus_r is None
                        else f"{item.check_catalog_g_minus_r:.6f}"
                    ),
                    "check_calibrated_mag": (
                        "" if item.check_magnitude is None else f"{item.check_magnitude:.6f}"
                    ),
                    "check_delta_mag": (
                        "" if check_delta_mag is None else f"{check_delta_mag:.6f}"
                    ),
                    "check_quality_status": "OK" if check_valid else "INVALID",
                    "check_quality_flag": "OK" if check_valid else "INSUFFICIENT_BIN_INPUTS",
                    "valid": "1" if bin_valid else "0",
                    "quality_status": "OK" if bin_valid else "INVALID",
                    "quality_flag": bin_flag,
                    "quality_note": (
                        "flux-binned derived result"
                        if bin_valid else "flux-binned result does not meet target quality threshold"
                    ),
                    "bin_input_count": len(item.frames),
                    "bin_check_input_count": item.check_input_count,
                    "bin_start_jd": f"{item.start_jd:.8f}",
                    "bin_end_jd": f"{item.end_jd:.8f}",
                    "bin_exposure_seconds": f"{item.exposure_seconds:.6f}",
                    "bin_source_frames": ";".join(str(frame.frame_index) for frame in item.frames),
                })
                rows.append(row)
            if not any(str(row.get("valid", "")) == "1" for row in rows):
                raise ValueError("No binned point meets the existing target quality threshold.")
            metadata = augment_result_metadata_from_rows(metadata, rows)
            with output_csv.open("w", newline="") as handle:
                write_result_metadata_header(handle, metadata)
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        except Exception as exc:
            self.binning_eligibility_label.setText(f"Binning failed: {exc}")
            self.append_log(f"Binning failed: {exc}")
            QMessageBox.warning(self, "Create Binned Curve", str(exc))
            return
        self.set_current_lightcurve_from_plugin(
            output_csv,
            source_metadata.get("OBJECT_NAME", "Target") or "Target",
            plot_lightcurve=True,
        )
        self.binning_eligibility_label.setText(f"Created {len(bins)} binned points; raw curve remains unchanged.")
        self.lightcurve_status_label.setText(f"Binned curve created: {output_csv}")
        self.export_status_label.setText(f"Current export source: {output_csv}")
        self.append_log(
            f"Binned curve written: {output_csv} ({len(bins)} bins, {len(frames)} eligible inputs, "
            f"{excluded} excluded, {skipped} short rest bins skipped)."
        )

    def activate_raw_curve(self) -> None:
        """Switch an active binned result back to its unmodified source curve."""

        current = self.loaded_lightcurve_csv
        if current is None or not current.exists():
            QMessageBox.warning(self, "Use Raw Curve", "No active Light Curve is available.")
            return
        metadata = read_result_metadata_header(current)
        source_name = metadata.get("BIN_SOURCE_RESULT", "").strip()
        if metadata.get("SOURCE_PROVENANCE") != "BINNED_RESULT" or not source_name:
            QMessageBox.information(self, "Use Raw Curve", "The active curve is already the raw result.")
            return
        source_csv = current.with_name(source_name)
        if not source_csv.exists():
            QMessageBox.warning(self, "Use Raw Curve", "The raw source result CSV was not found.")
            return
        try:
            self.set_current_lightcurve_from_plugin(source_csv, "", plot_lightcurve=True)
        except Exception as exc:
            QMessageBox.warning(self, "Use Raw Curve", f"Could not activate raw curve:\n{exc}")
            return
        self.binning_eligibility_label.setText("Raw curve active; it may be binned again with another size.")
        self.append_log(f"Raw curve activated: {source_csv}")

    def source_directory_for_result_csv(self, result_csv: Path) -> Path | None:
        """Infer the original source directory from the current result location."""

        source_dir = self.current_source_directory()
        if source_dir is not None:
            return source_dir
        result_dir = result_csv.parent
        if result_dir.parent.name != RESULTS_DIRECTORY_NAME:
            return None
        source_name = result_dir.name
        if "_b4_" in source_name:
            source_name = source_name.split("_b4_", 1)[0]
        candidate = result_dir.parent.parent / source_name
        return candidate if candidate.exists() else None

    def frame_search_directories_for_result_csv(self, result_csv: Path) -> list[Path]:
        """Return likely directories containing the work-sequence FITS frames."""

        directories: list[Path] = []
        source_dir = self.source_directory_for_result_csv(result_csv)
        if source_dir is not None:
            directories.append(temp_work_directory_for(source_dir))
        work_dir = self.current_work_directory()
        if work_dir is not None:
            directories.append(work_dir)
        unique: list[Path] = []
        for directory in directories:
            if directory.exists() and directory not in unique:
                unique.append(directory)
        return unique

    def result_metadata(
        self,
        measurements: list[ApertureMeasurement],
        aperture_settings: ApertureSettings,
        metadata_fits_path: Path,
    ) -> dict[str, object]:
        """Build the general metadata header for a new result CSV."""

        if self.selected_target is None:
            raise RuntimeError("No VSX target selected for result metadata.")
        if self.reference_frame is None:
            raise RuntimeError("No solved reference frame available for result metadata.")

        optional_settings = load_optional_observer_result_settings()
        field_metadata = reference_frame_field_metadata(self.reference_frame)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                warnings.simplefilter("ignore", VerifyWarning)
                metadata_header = fits.getheader(metadata_fits_path)
        except Exception as exc:
            metadata_header = fits.Header()
            self.append_log(
                "WARNING: Could not read result instrument metadata from "
                f"{metadata_fits_path.name}: {exc}; using configured/default fallback."
            )
        instrument_metadata, instrument_sources = result_instrument_metadata_from_header(
            metadata_header,
            optional_settings["TELESCOPE"],
            RESULT_TELESCOPE_SPECS,
            optional_settings["TELESCOPE_SOURCE"],
        )
        self.append_log(
            "Result instrument metadata: "
            f"telescope={instrument_metadata['TELESCOPE']} ({instrument_sources['TELESCOPE']}), "
            f"sensor={instrument_metadata['SENSOR']} ({instrument_sources['SENSOR']}), "
            f"pixel scale={instrument_metadata['PIXEL_SCALE_ARCSEC_PX']} arcsec/px "
            f"({instrument_sources['PIXEL_SCALE_ARCSEC_PX']})."
        )
        target = self.selected_target.catalog_object
        target_exptimes = [
            item.exptime
            for item in measurements
            if item.role == "target" and np.isfinite(item.exptime)
        ]
        exposure_seconds = ""
        if target_exptimes:
            exposure_seconds = f"{float(np.median(np.array(target_exptimes, dtype=np.float64))):.6f}".rstrip("0").rstrip(".")

        photometry_method = (
            "Aperture "
            f"{aperture_settings.mode}; "
            f"radius_px={aperture_settings.aperture_radius_px:.3f}; "
            f"annulus_px={aperture_settings.annulus_inner_px:.3f}-"
            f"{aperture_settings.annulus_outer_px:.3f}"
        )
        if aperture_settings.fwhm_px is not None:
            photometry_method += f"; reference_fwhm_px={aperture_settings.fwhm_px:.3f}"
        if aperture_settings.note:
            photometry_method += f"; note={aperture_settings.note}"

        return {
            "RESULT_METADATA_VERSION": CURRENT_RESULT_METADATA_VERSION,
            **result_target_metadata(target),
            **field_metadata,
            "OBSERVER_BAV": optional_settings["OBSERVER_BAV"],
            "OBSERVER_NAME": optional_settings["OBSERVER_NAME"],
            "OBSERVER_AAVSO": optional_settings["OBSERVER_AAVSO"],
            "OBSERVER_SITE": optional_settings["OBSERVER_SITE"],
            "OBSERVER_LATITUDE": optional_settings["OBSERVER_LATITUDE"],
            "OBSERVER_LONGITUDE": optional_settings["OBSERVER_LONGITUDE"],
            **instrument_metadata,
            "PHOTOMETRY_METHOD": photometry_method,
            "RINGSET_RULE_ID": DEFAULT_RINGSET_RULE_ID,
            "RINGSET_NOTE": aperture_settings.note,
            "PHOTOMETRY_SOFTWARE": SOFTWARE_NAME,
            "LIGHTCURVE_SOFTWARE": SOFTWARE_NAME,
            "SOFTWARE": SOFTWARE_NAME,
            "TIME_SYSTEM": "JD_UTC",
            "EXPOSURE_SECONDS": exposure_seconds,
            "COMP_STARS": "; ".join(catalog_object_report_entry(obj) for obj in self.comparison_stars),
            "CHECK_STAR": (
                catalog_object_report_entry(self.check_star)
                if self.check_star is not None
                else ""
            ),
        }

    def update_selected_target_lightcurve_status(self) -> None:
        if self.selected_target is None:
            self.lightcurve_status_label.setText("No target selected.")
            return

        target_name = self.selected_target.catalog_object.name.strip() or "target"
        self.lightcurve_status_label.setText(
            f"No Light Curve generated or loaded for {target_name}. "
            "Create Light Curve will select Comp Stars and Check Star automatically."
        )

    def work_sequence_directory(self) -> Path | None:
        if self.reference_frame is not None:
            return self.reference_frame.path.parent
        return self.current_work_directory()

    def update_binning_controls(self) -> None:
        """Update the scientific-binning size field for the selected mode."""

        if self.binning_mode_combo.currentData() == "seconds":
            self.binning_value_spin.setSuffix(" s")
            self.binning_value_spin.setValue(600)
        else:
            self.binning_value_spin.setSuffix(" images")
            self.binning_value_spin.setValue(10)

    def single_field_zp_catalog_cache_path(self) -> Path | None:
        """Return the per-field auto-catalog cache file for Field-ZP single measurements."""

        if self.reference_frame is None:
            return None
        return self.reference_frame.path.parent / SINGLE_FIELD_ZP_CATALOG_CACHE_NAME

    def single_field_zp_legacy_catalog_cache_path(self) -> Path | None:
        """Return the legacy per-field APASS cache path, if a prepared field has one."""

        if self.reference_frame is None:
            return None
        return self.reference_frame.path.parent / SINGLE_FIELD_ZP_LEGACY_CATALOG_CACHE_NAME

    def cached_single_field_zp_catalog_objects(self) -> list[CatalogObject]:
        """Return cached Field-ZP catalog objects, querying once per prepared field."""

        if self.reference_frame is None:
            raise RuntimeError("Load a reference frame first.")
        cache_path = self.single_field_zp_catalog_cache_path()
        legacy_cache_path = self.single_field_zp_legacy_catalog_cache_path()
        cache_candidates = [path for path in (cache_path, legacy_cache_path) if path is not None]
        for candidate_cache_path in cache_candidates:
            if not candidate_cache_path.exists() or candidate_cache_path.stat().st_size <= 0:
                continue
            try:
                objects = read_catalog_objects(candidate_cache_path)
                if objects:
                    source_summary = catalog_source_summary(obj.catalog_source for obj in objects)
                    self.append_log(
                        f"Reusing cached Field-ZP catalog: {candidate_cache_path.name} "
                        f"({len(objects)} rows, sources={source_summary})."
                    )
                    return objects
                self.append_log(
                    f"WARNING: Field-ZP catalog cache is empty; querying again: {candidate_cache_path.name}"
                )
            except Exception as exc:
                self.append_log(
                    f"WARNING: Could not read Field-ZP catalog cache {candidate_cache_path.name}: {exc}"
                )
                try:
                    candidate_cache_path.unlink(missing_ok=True)
                except Exception:
                    pass

        radius_arcmin = reference_frame_search_radius_arcmin(self.reference_frame)
        self.append_log(
            f"Querying {COMPARISON_CATALOG_AUTO_NAME} Field-ZP catalog: "
            f"center RA={self.reference_frame.ra_deg:.6f}, "
            f"Dec={self.reference_frame.dec_deg:.6f}, "
            f"radius={radius_arcmin:.1f} arcmin, "
            f"max rows={APASS_DR10_MAX_QUERY_ROWS}."
        )
        objects = query_comparison_catalog_region(
            self.reference_frame,
            None,
            99.0,
            self.append_log,
            None,
        )
        if cache_path is not None:
            try:
                write_catalog_objects(cache_path, objects, COMPARISON_CATALOG_COLUMNS)
                source_summary = catalog_source_summary(obj.catalog_source for obj in objects)
                self.append_log(
                    f"Cached Field-ZP catalog: {cache_path.name} "
                    f"({len(objects)} rows, sources={source_summary})."
                )
            except Exception as exc:
                self.append_log(f"WARNING: Could not write Field-ZP catalog cache: {exc}")
        return objects

    def run_single_measurement(self) -> None:
        """Measure one target in the selected single FITS work frame."""

        self.set_result_display_kind(single=True)
        self.append_log("Run Single Measurement requested.")
        if self.selected_target is None:
            current_selection = self.current_vsx_selection()
            if current_selection is not None:
                if not self.set_selected_target_object(current_selection):
                    self.set_result_status("failed")
                    return

        self.loaded_lightcurve_csv = None
        self.loaded_lightcurve_target_name = "Target"
        self.loaded_lightcurve_source_label = ""
        self.current_result_origin = "none"
        self.current_exports.clear()
        self.clear_single_result_summary()
        self.set_result_status("creating")
        calibration_label = "Field Zero Point"
        self.append_log(f"Single measurement calibration: {calibration_label}.")
        if self.selected_target is None:
            self.set_result_status("failed")
            self.show_single_measurement_warning("Select a variable first.")
            return
        if self.reference_frame is None:
            self.set_result_status("failed")
            self.show_single_measurement_warning("Run Detect Variables first.")
            return

        work_dir = self.work_sequence_directory()
        output_dir = self.lightcurve_output_directory()
        stem = self.selected_target_output_stem()
        source_dir = self.current_source_directory()
        if work_dir is None or output_dir is None or stem is None:
            self.set_result_status("failed")
            self.show_single_measurement_warning("Select a FITS input file first.")
            return

        try:
            frame_path, frame_index = self.single_measurement_work_frame()
        except ValueError as exc:
            self.set_result_status("failed")
            self.append_log(f"ERROR: {exc}")
            self.show_single_measurement_warning(str(exc))
            return

        source_file = self.current_source_fits_file()
        filter_metadata = infer_image_filter_metadata(
            source_file.parent if source_file is not None else self.current_source_directory(),
            (source_file,) if source_file is not None else (frame_path,),
        )
        if filter_metadata is None:
            message = (
                "No unambiguous supported FITS FILTER/CHANMODE metadata found. "
                "Run CFA Channels / Stack first or use a FITS file with "
                "FILTER=L, FILTER=G or FILTER=V."
            )
            self.set_result_status("failed")
            self.append_log(f"ERROR: {message}")
            self.show_single_measurement_warning(
                "Unsupported FITS filter metadata.\n\nRun CFA Channels / Stack first."
            )
            return
        image_source, aavso_filter, origin_marker = filter_metadata
        self.append_log(
            f"Single photometry source/filter: image_source={image_source}, "
            f"AAVSO filter={aavso_filter}."
        )
        if origin_marker:
            self.append_log(f"FITS provenance marker: {origin_marker}")

        try:
            specs = self.single_measurement_specs(image_source, aavso_filter)
        except ValueError as exc:
            self.set_result_status("failed")
            self.append_log(f"ERROR: {exc}")
            self.show_single_measurement_warning(str(exc))
            return

        aperture_settings = resolve_aperture_settings(self.reference_frame)
        blend_assessment = assess_single_target_catalog_blend(
            self.selected_target.catalog_object,
            self.reference_frame,
            aperture_settings,
            self.append_log,
        )
        if blend_assessment.status == QUALITY_STATUS_OK:
            self.append_log(blend_assessment.message)
        else:
            self.append_log(
                f"WARNING: {blend_assessment.message} "
                "The aperture measurement will run before this quality decision is applied."
            )

        measurements = aperture_measurements_for_frame(
            frame_path,
            frame_index,
            specs,
            aperture_settings.aperture_radius_px,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
        )
        target_measurements = [item for item in measurements if item.role == "target"]
        target_measurement = target_measurements[0] if target_measurements else None
        if (
            target_measurement is not None
            and not target_measurement.valid
            and target_measurement.quality_flag == "ANNULUS_CONTAMINATION"
        ):
            mask_candidate = self.single_annulus_mask_candidate_for_target(
                frame_path,
                target_measurement,
                aperture_settings,
            )
            if mask_candidate is not None:
                self.append_log(
                    "Single target annulus mask candidate: "
                    f"islands={mask_candidate.island_count}; "
                    f"masked={mask_candidate.masked_pixel_fraction:.1%}, "
                    f"remaining={mask_candidate.remaining_pixel_fraction:.1%}."
                )
                if self.confirm_single_annulus_mask(mask_candidate):
                    measurements = aperture_measurements_for_frame(
                        frame_path,
                        frame_index,
                        specs,
                        aperture_settings.aperture_radius_px,
                        aperture_settings.annulus_inner_px,
                        aperture_settings.annulus_outer_px,
                        {target_measurement.object_id: mask_candidate.exclusion_mask},
                    )
                    target_measurement = next(
                        (item for item in measurements if item.role == "target"), None
                    )
                    self.append_log(
                        "Single target background masked after user confirmation: "
                        f"{mask_candidate.masked_pixel_count} pixel(s) excluded."
                    )
                else:
                    self.append_log("Single target annulus-mask proposal declined by user.")
        if target_measurement is None or not target_measurement.valid or target_measurement.inst_mag is None:
            message = (
                "Target measurement is not valid"
                if target_measurement is None
                else f"Target measurement is not valid: {target_measurement.quality_flag}"
            )
            self.set_result_status("failed")
            self.append_log(f"ERROR: {message}")
            self.show_single_measurement_warning("Target could not be measured.")
            return

        target_error = (
            target_measurement.inst_mag_error
            if target_measurement.inst_mag_error is not None
            else float("nan")
        )
        check_measurement = next((item for item in measurements if item.role == "check"), None)
        zero_point_scatter = 0.0
        calibrated_mag: float
        calibrated_error: float
        check_calibrated_mag: float | None = None
        check_delta_mag: float | None = None
        diagnostics_dir = diagnostics_directory_for_results_dir(output_dir)
        if self.lightcurve_results_module is None:
            self.lightcurve_results_module = load_lightcurve_results_module()
        result_csv = Path(
            self.lightcurve_results_module.single_measurement_result_path(
                output_dir,
                stem,
            )
        )
        instrumental_csv = diagnostics_dir / f"{stem}_single_instrumental_photometry.csv"
        optional_settings = load_optional_observer_result_settings()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FITSFixedWarning)
                warnings.simplefilter("ignore", VerifyWarning)
                metadata_header = fits.getheader(frame_path)
        except Exception as exc:
            metadata_header = fits.Header()
            self.append_log(
                "WARNING: Could not read result instrument metadata from "
                f"{frame_path.name}: {exc}; using configured/default fallback."
            )
        instrument_metadata, instrument_sources = result_instrument_metadata_from_header(
            metadata_header,
            optional_settings["TELESCOPE"],
            RESULT_TELESCOPE_SPECS,
            optional_settings["TELESCOPE_SOURCE"],
        )
        self.append_log(
            "Result instrument metadata: "
            f"telescope={instrument_metadata['TELESCOPE']} ({instrument_sources['TELESCOPE']}), "
            f"sensor={instrument_metadata['SENSOR']} ({instrument_sources['SENSOR']}), "
            f"pixel scale={instrument_metadata['PIXEL_SCALE_ARCSEC_PX']} arcsec/px "
            f"({instrument_sources['PIXEL_SCALE_ARCSEC_PX']})."
        )
        try:
            field_metadata = reference_frame_field_metadata(self.reference_frame)
        except Exception as exc:
            self.set_result_status("failed")
            self.append_log(f"ERROR: Reference-field metadata could not be created: {exc}")
            self.show_single_measurement_warning(
                f"Could not create reference-field metadata.\n\n{exc}"
            )
            return
        metadata = {
            "RESULT_METADATA_VERSION": CURRENT_RESULT_METADATA_VERSION,
            "SINGLE_MEASUREMENT_VERSION": 1,
            "SCRIPT_VERSION": SCRIPT_VERSION,
            "MODE": MODE_SINGLE_MEASUREMENT,
            "SINGLE_FIELD_ZP_METHOD": SINGLE_FIELD_ZP_METHOD_ID,
            **result_target_metadata(self.selected_target.catalog_object),
            **({"EXPORT_ALLOWED": 0} if is_added_target(self.selected_target.catalog_object) else {}),
            **field_metadata,
            "SOURCE_FILE": str(self.current_source_fits_file() or ""),
            "WORK_FILE": str(frame_path),
            "PHOTOMETRY_METHOD": (
                f"Aperture {aperture_settings.mode}; "
                f"radius_px={aperture_settings.aperture_radius_px:.3f}; "
                f"annulus_px={aperture_settings.annulus_inner_px:.3f}-"
                f"{aperture_settings.annulus_outer_px:.3f}"
            ),
            "PHOTOMETRY_METHOD_NOTE": aperture_settings.note,
            "SOFTWARE": SOFTWARE_NAME,
            "OBSERVER_BAV": optional_settings["OBSERVER_BAV"],
            "OBSERVER_NAME": optional_settings["OBSERVER_NAME"],
            "OBSERVER_AAVSO": optional_settings["OBSERVER_AAVSO"],
            "OBSERVER_SITE": optional_settings["OBSERVER_SITE"],
            "OBSERVER_LATITUDE": optional_settings["OBSERVER_LATITUDE"],
            "OBSERVER_LONGITUDE": optional_settings["OBSERVER_LONGITUDE"],
            **instrument_metadata,
        }
        field_zp_objects = self.cached_single_field_zp_catalog_objects()
        field_zp_catalog_sources = catalog_source_summary(
            obj.catalog_source for obj in field_zp_objects
        )
        self.append_log(
            f"Single field-ZP catalog candidates: {len(field_zp_objects)} "
            f"(sources={field_zp_catalog_sources})."
        )
        field_zp_candidates = select_single_field_zp_candidates_with_ucac4_fallback(
            field_zp_objects,
            self.reference_frame,
            aperture_settings,
            self.append_log,
        )
        if is_added_target(self.selected_target.catalog_object):
            field_zp_candidates = exclude_target_from_field_references(
                field_zp_candidates, self.selected_target.catalog_object,
                self.reference_frame, aperture_settings.annulus_outer_px,
            )
        field_zp_specs = [field_zp_catalog_spec(candidate) for candidate in field_zp_candidates]
        field_zp_measurements = aperture_measurements_for_frame(
            frame_path,
            frame_index,
            field_zp_specs,
            aperture_settings.aperture_radius_px,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
        )
        field_zp_references = single_field_zp_references_from_measurements(field_zp_measurements)
        measured_field_zp_catalog_sources = catalog_source_summary(
            reference.measurement.catalog_source for reference in field_zp_references
        )
        field_zp_references, field_zp_check_reference = reserve_single_field_zp_check_reference(
            field_zp_references
        )
        field_zp_fit = fit_single_field_zero_point(field_zp_references)
        informational_result = False
        if not field_zp_fit.success or field_zp_fit.zero_point is None:
            informational_references = single_field_zp_references_from_measurements(
                field_zp_measurements,
                SINGLE_FIELD_ZP_INFORMATIONAL_MIN_SNR,
            )
            informational_references, informational_check = reserve_single_field_zp_check_reference(
                informational_references
            )
            informational_fit = fit_single_field_zero_point(informational_references)
            if informational_fit.success and informational_fit.zero_point is not None:
                informational_result = True
                field_zp_references = informational_references
                field_zp_check_reference = informational_check
                field_zp_fit = informational_fit
                self.append_log(
                    "WARNING: Single field-ZP export threshold was not met; "
                    f"an informational fit uses references with SNR >= "
                    f"{SINGLE_FIELD_ZP_INFORMATIONAL_MIN_SNR:.0f}. "
                    "The result is diagnostic only and cannot be exported."
                )
        if field_zp_check_reference is not None:
            check_measurement = field_zp_check_reference.measurement
            self.append_log(
                "Single field-ZP check reserved outside fit: "
                f"{check_measurement.catalog_source}:{check_measurement.catalog_id or check_measurement.object_id}, "
                f"catalog V={check_measurement.catalog_mag:.3f}, "
                f"SNR={check_measurement.snr:.1f}, "
                f"e_V={format_optional_float(check_measurement.catalog_mag_error)}, "
                f"B-V={format_optional_float(check_measurement.catalog_b_minus_v)}, "
                f"g-r={format_optional_float(check_measurement.catalog_g_minus_r)}."
            )
        else:
            self.append_log(
                "WARNING: No independent Single field-ZP Check Star reserved; "
                "too few suitable references after quality filtering."
            )
        reference_quality_counts = Counter(
            reference.reject_reason or "fit_candidate"
            for reference in field_zp_references
        )
        self.append_log(f"Single field-ZP reference quality: {format_counter(reference_quality_counts)}.")
        if not field_zp_fit.success or field_zp_fit.zero_point is None:
            message = (
                "Single field-ZP measurement failed: "
                f"{field_zp_fit.status} "
                f"(used refs={field_zp_fit.used_count}/{field_zp_fit.usable_count})."
            )
            self.set_result_status("failed")
            self.append_log(f"ERROR: {message}")
            self.show_single_measurement_warning(
                "Single Measurement failed.\n\nNot enough usable reference stars."
            )
            return

        calibrated_mag = float(target_measurement.inst_mag) + float(field_zp_fit.zero_point)
        field_zp_error = (
            field_zp_fit.zero_point_error
            if field_zp_fit.zero_point_error is not None
            else 0.0
        )
        calibrated_error = float(
            np.sqrt(target_error * target_error + field_zp_error * field_zp_error)
            if np.isfinite(target_error)
            else field_zp_error
        )
        zero_point_scatter = field_zp_fit.scatter if field_zp_fit.scatter is not None else 0.0
        if (
            check_measurement is not None
            and check_measurement.valid
            and check_measurement.inst_mag is not None
        ):
            check_calibrated_mag = (
                float(check_measurement.inst_mag) + float(field_zp_fit.zero_point)
            )
            if check_measurement.catalog_mag is not None:
                check_delta_mag = (
                    check_calibrated_mag - float(check_measurement.catalog_mag)
                )
        linearity_references = [
            reference.measurement
            for reference in field_zp_references
            if reference.used_for_fit
        ]
        linearity_assessment = assess_bright_instrumental_range(
            target_measurement,
            linearity_references,
        )
        self.append_log(
            "Single bright-linearity check: "
            f"{linearity_assessment.status}/{linearity_assessment.flag}; "
            f"refs={linearity_assessment.reference_count}; "
            f"{linearity_assessment.note}."
        )
        if linearity_assessment.status == QUALITY_STATUS_INVALID:
            self.set_result_status("failed")
            self.append_log(
                "ERROR: Single Measurement rejected by bright-linearity check: "
                f"{linearity_assessment.note}."
            )
            self.show_single_measurement_warning(
                "Single Measurement rejected.\n\n"
                f"{linearity_assessment.note}"
            )
            return
        target_measurement = apply_bright_linearity_assessment(
            target_measurement,
            linearity_assessment,
        )
        target_measurement = apply_target_blend_assessment(
            target_measurement,
            blend_assessment,
        )
        informational_only = informational_result and target_measurement.valid
        if informational_only:
            target_measurement = replace(
                target_measurement,
                valid=False,
                quality_status=QUALITY_STATUS_INVALID,
                quality_flag=combine_quality_flag(
                    target_measurement.quality_flag, "INFORMATIONAL_FIELD_ZP"
                ),
                note=combine_quality_note(
                    target_measurement.note,
                    "Field-ZP references did not meet the export SNR threshold; "
                    "calibrated magnitude is informational only",
                ),
            )
        measurements = [
            target_measurement if item.role == "target" else item
            for item in measurements
        ]
        field_zp_metadata = {
            **metadata,
            **({
                "RESULT_PURPOSE": "INFORMATIONAL_SINGLE_FIELD_ZP",
                "EXPORT_ALLOWED": 0,
                "ARCHIVE_ALLOWED": 0,
                "DIAGNOSTIC_REASON": "FIELD_ZP_REFERENCE_SNR_BELOW_EXPORT_LIMIT",
                "FIELD_ZP_INFORMATIONAL_MIN_SNR": SINGLE_FIELD_ZP_INFORMATIONAL_MIN_SNR,
                "FIELD_ZP_EXPORT_MIN_SNR": SINGLE_FIELD_ZP_MIN_SNR,
            } if informational_result else {}),
            "SINGLE_TARGET_BACKGROUND_MASKED": int(target_measurement.annulus_masked),
            "SINGLE_TARGET_BACKGROUND_MASKED_PIXELS": target_measurement.annulus_masked_pixel_count,
            "SINGLE_TARGET_BACKGROUND_MASKED_FRACTION": format_csv_float(
                target_measurement.annulus_masked_pixel_fraction,
                10,
            ),
            "FIELD_ZP_STATUS": field_zp_fit.status,
            "FIELD_ZP_REFERENCE_MEASURED": field_zp_fit.measured_count,
            "FIELD_ZP_REFERENCE_USABLE": field_zp_fit.usable_count,
            "FIELD_ZP_REFERENCE_USED": field_zp_fit.used_count,
            "FIELD_ZP_REFERENCE_CLIPPED": field_zp_fit.rejected_clip_count,
            "FIELD_ZP_CATALOG_SOURCES": measured_field_zp_catalog_sources,
            "FIELD_ZP_SCATTER": format_csv_float(field_zp_fit.scatter),
            "FIELD_ZP_ERROR": format_csv_float(field_zp_fit.zero_point_error),
            "FIELD_ZP_NOTE": "single-measurement calibration method",
            "BRIGHT_LINEARITY_STATUS": linearity_assessment.status,
            "BRIGHT_LINEARITY_FLAG": linearity_assessment.flag,
            "BRIGHT_LINEARITY_NOTE": linearity_assessment.note,
            "BRIGHT_LINEARITY_REFERENCE_COUNT": linearity_assessment.reference_count,
            "BRIGHT_LINEARITY_LIMIT_MAG": format_csv_float(linearity_assessment.bright_limit_catalog_mag),
            "BRIGHT_LINEARITY_TARGET_MAG": format_csv_float(linearity_assessment.target_catalog_mag),
            "BRIGHT_LINEARITY_MARGIN_MAG": format_csv_float(linearity_assessment.margin_mag),
        }
        field_zp_references_csv = diagnostics_dir / f"{stem}_single_field_zp_references.csv"
        write_instrumental_photometry(instrumental_csv, measurements)
        write_single_field_zp_measurement_csv(
            result_csv,
            field_zp_metadata,
            target_measurement,
            check_measurement,
            field_zp_fit,
            calibrated_mag,
            calibrated_error,
            check_calibrated_mag,
            check_delta_mag,
        )
        write_single_field_zp_references_csv(
            field_zp_references_csv,
            field_zp_metadata,
            field_zp_references,
            field_zp_fit,
        )
        if informational_only:
            self.current_result_origin = "single"
            self.loaded_lightcurve_csv = result_csv
            self.loaded_lightcurve_target_name = (
                self.selected_target.catalog_object.name.strip() or target_measurement.object_id
            )
            self.loaded_lightcurve_source_label = source_dir.name if source_dir is not None else ""
            self.current_exports.clear()
            self.set_result_status("warning")
            self.set_single_result_summary(
                target_measurement,
                check_measurement,
                calibrated_mag,
                calibrated_error,
                check_calibrated_mag,
                check_delta_mag,
                zero_point_scatter,
                field_zp_fit.used_count,
                "refs",
            )
            self.lightcurve_status_label.setText(
                f"Informational Single Measurement: mag={calibrated_mag:.3f} +/- "
                f"{calibrated_error:.3f}; field refs={field_zp_fit.used_count}. "
                f"Export disabled. Output: {result_csv}"
            )
            self.export_status_label.setText(
                "Informational Single Measurement only; AAVSO, BAV and archive export disabled."
            )
            self.refresh_bav_tab_view()
            self.refresh_action_availability()
            self.append_log(
                "Informational Single Measurement written: "
                f"{result_csv.name}; mag={calibrated_mag:.4f} +/- "
                f"{calibrated_error:.4f}; export disabled."
            )
            return
        if not target_measurement.valid:
            message = (
                target_contamination_failure_message()
                if blend_assessment.status == QUALITY_STATUS_INVALID
                else "Rejected: Invalid target measurement"
            )
            self.set_result_status("failed")
            self.set_single_result_summary(
                target_measurement,
                check_measurement,
                calibrated_mag,
                calibrated_error,
                check_calibrated_mag,
                check_delta_mag,
                zero_point_scatter,
                field_zp_fit.used_count,
                "refs",
            )
            self.lightcurve_status_label.setText(
                f"{message}. Diagnostic: {result_csv}"
            )
            self.export_status_label.setText(
                "Single measurement rejected; no result available for export."
            )
            self.append_log(f"ERROR: {message}")
            self.append_log(
                "Rejected Single Measurement diagnostic written: "
                f"{result_csv.name}; quality="
                f"{measurement_quality_status(target_measurement)}/"
                f"{target_measurement.quality_flag}."
            )
            self.append_log(
                f"Single instrumental photometry written: {instrumental_csv.name}."
            )
            self.append_log(
                f"Single field-ZP references written: {field_zp_references_csv.name}."
            )
            self.show_single_measurement_warning(
                f"{message}\n\nThe displayed magnitude is diagnostic only and cannot be exported.",
                title="Single Measurement — REJECTED / INVALID",
            )
            return
        self.append_log(
            "Single field-ZP measurement written: "
            f"{result_csv.name}; mag={calibrated_mag:.4f} "
            f"+/- {calibrated_error:.4f}, "
            f"ZP={field_zp_fit.zero_point:.4f}, "
            f"scatter={field_zp_fit.scatter:.4f}, "
            f"used refs={field_zp_fit.used_count}/{field_zp_fit.usable_count}."
        )
        self.append_log(f"Single field-ZP references written: {field_zp_references_csv.name}.")

        self.current_result_origin = "single"
        self.loaded_lightcurve_csv = result_csv
        self.loaded_lightcurve_target_name = self.selected_target.catalog_object.name.strip() or target_measurement.object_id
        self.loaded_lightcurve_source_label = source_dir.name if source_dir is not None else ""
        self.current_exports.clear()
        self.set_result_status("created")
        self.refresh_bav_tab_view()
        self.refresh_action_availability()
        check_text = (
            "n/a"
            if check_delta_mag is None
            else f"{check_delta_mag:+.3f} mag"
        )
        reference_count = field_zp_fit.used_count
        self.set_single_result_summary(
            target_measurement,
            check_measurement,
            calibrated_mag,
            calibrated_error,
            check_calibrated_mag,
            check_delta_mag,
            zero_point_scatter,
            reference_count,
            "refs",
        )
        self.lightcurve_status_label.setText(
            f"Single measurement: mag={calibrated_mag:.3f} +/- {calibrated_error:.3f}, "
            f"field refs={reference_count}, "
            f"check delta={check_text}. "
            f"Output: {result_csv}"
        )
        self.export_status_label.setText(f"Single measurement output: {result_csv.name}")
        self.append_log(
            "Single measurement written: "
            f"{result_csv.name}; mag={calibrated_mag:.4f} +/- {calibrated_error:.4f}, "
            f"target SNR={target_measurement.snr:.1f}, "
            f"references={reference_count}, "
            f"ZP scatter={zero_point_scatter:.4f}, "
            f"aperture={aperture_settings.aperture_radius_px:.2f}px, "
            f"annulus={aperture_settings.annulus_inner_px:.2f}-"
            f"{aperture_settings.annulus_outer_px:.2f}px, "
            f"check delta={check_text}."
        )
        self.append_log(f"Single instrumental photometry written: {instrumental_csv.name}.")
        if check_measurement is not None:
            check_mag_text = (
                "n/a"
                if check_calibrated_mag is None
                else f"{check_calibrated_mag:.4f}"
            )
            self.append_log(
                "Single check: "
                f"{check_measurement.catalog_source}:{check_measurement.catalog_id or check_measurement.object_id}, "
                f"calibrated={check_mag_text}, "
                f"delta={check_text}."
            )

        target_status = measurement_quality_status(target_measurement)
        reference_invalid = sum(1 for reference in field_zp_references if not reference.measurement.valid)
        reference_warning = sum(
            1
            for reference in field_zp_references
            if reference.measurement.valid
            and measurement_quality_status(reference.measurement) == QUALITY_STATUS_WARNING
        )
        single_summary = (
            f"Target quality: {target_status}/{target_measurement.quality_flag}\n"
            f"Field references: used={reference_count}, invalid={reference_invalid}, "
            f"warning={reference_warning}\n"
            f"Check delta: {check_text}\n\n"
            f"{result_csv}"
        )
        self.append_log(single_summary)

    def request_contaminated_target_diagnostic_curve(self) -> bool | None:
        """Ask whether a manual rejected target should continue for diagnostics."""

        if not ENABLE_CONTAMINATED_TARGET_DIAGNOSTIC_CURVE:
            return None
        if self.batch_tab is not None and getattr(self.batch_tab, "running", False):
            return None

        reply = QMessageBox.question(
            self,
            "Diagnostic Curve",
            "The target is rejected because of modeled contamination.\n\n"
            "Continue with Comp Star selection and aperture measurements to attempt "
            "a separate diagnostic curve?\n\n"
            "The curve will be marked invalid and cannot be exported, archived, "
            "or used for Min/Max fitting.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.append_log("Diagnostic contaminated-target curve declined.")
            return False
        self.append_log(
            "Diagnostic contaminated-target curve requested; continuing with "
            "Comp Star selection and aperture measurements."
        )
        return True

    def offer_contaminated_target_diagnostic_curve(
        self,
        measured: list[ApertureMeasurement],
        assessed: list[ApertureMeasurement],
        result_metadata: dict[str, object],
        target_name: str,
        source_label: str,
        *,
        confirmed: bool = False,
    ) -> bool:
        """Offer one explicitly non-exportable curve after a manual blend rejection."""

        if not ENABLE_CONTAMINATED_TARGET_DIAGNOSTIC_CURVE:
            return False
        if self.batch_tab is not None and getattr(self.batch_tab, "running", False):
            return False

        if not confirmed:
            decision = self.request_contaminated_target_diagnostic_curve()
            if decision is not True:
                return decision is not None

        diagnostic_measurements = diagnostic_target_blend_measurements(
            measured,
            assessed,
        )
        if not any(
            item.role == "target" and item.valid
            for item in diagnostic_measurements
        ):
            self.append_log(
                "Diagnostic contaminated-target curve unavailable: "
                "no target frame was valid before the blend assessment."
            )
            return False

        diagnostic_csv = self.selected_target_diagnostic_curve_csv()
        if diagnostic_csv is None:
            self.append_log("ERROR: Diagnostic curve output path is unavailable.")
            QMessageBox.warning(
                self,
                "Diagnostic Curve",
                "Diagnostic curve output path is unavailable.",
            )
            return True

        diagnostic_metadata = {
            **result_metadata,
            "RESULT_PURPOSE": CONTAMINATED_TARGET_DIAGNOSTIC_PURPOSE,
            "EXPORT_ALLOWED": 0,
            "ARCHIVE_ALLOWED": 0,
            "EXTREMUM_FIT_ALLOWED": 0,
            "DIAGNOSTIC_REASON": "TARGET_BLEND_MODELED_CONTAMINATION",
            "DIAGNOSTIC_NOTE": (
                "Rejected target measurements retained for visual testing only; "
                "not a scientific result"
            ),
        }
        diagnostic_stats = write_calibrated_light_curve(
            diagnostic_csv,
            diagnostic_measurements,
            diagnostic_metadata,
            result_rows_valid=False,
        )
        if diagnostic_stats.rows_written == 0:
            self.append_log(
                "Diagnostic contaminated-target curve could not be created: "
                "no otherwise usable calibrated rows remained."
            )
            QMessageBox.warning(
                self,
                "Diagnostic Curve",
                "No otherwise usable calibrated target rows remained.",
            )
            return True

        self.loaded_lightcurve_csv = diagnostic_csv
        self.loaded_lightcurve_target_name = target_name
        self.loaded_lightcurve_source_label = source_label
        self.current_result_origin = "diagnostic"
        self.current_exports.clear()
        self.set_result_status("warning")
        self.export_status_label.setText(
            "Diagnostic curve: scientific export and archive are disabled."
        )
        self.tabs.setCurrentWidget(self.lightcurve_tab)
        self.plot_light_curve_csv(
            diagnostic_csv,
            diagnostic_csv.with_suffix(".png"),
            f"{target_name} — DIAGNOSTIC: TARGET CONTAMINATION — NOT FOR EXPORT",
            source_label,
            allow_extremum=False,
            include_invalid=True,
        )
        self.lightcurve_status_label.setText(
            "Diagnostic contaminated-target curve written: "
            f"{diagnostic_stats.rows_written} row(s). NOT FOR EXPORT. "
            f"Output: {diagnostic_csv}"
        )
        self.append_log(
            "Diagnostic contaminated-target curve written: "
            f"{diagnostic_csv} ({diagnostic_stats.rows_written} row(s)); "
            "all target result rows remain valid=0/INVALID and export is disabled."
        )
        return True

    def run_light_curve(self) -> None:
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            self.run_single_measurement()
            return

        self.set_result_display_kind(single=False)
        self.append_log("Create Light Curve requested.")
        self.loaded_lightcurve_csv = None
        self.loaded_lightcurve_target_name = "Target"
        self.loaded_lightcurve_source_label = ""
        self.current_result_origin = "none"
        self.current_exports.clear()
        self.set_result_status("creating")
        if self.selected_target is None:
            self.fail_lightcurve_run("Select a variable first.")
            self.show_lightcurve_failure_dialog("Select a variable first.")
            return
        if self.reference_frame is None:
            self.fail_lightcurve_run("Run Detect Variables first.")
            self.show_lightcurve_failure_dialog("Run Detect Variables first.")
            return

        try:
            target_blend_aperture_settings = require_fixed_fwhm_aperture_settings(self.reference_frame)
        except RuntimeError as exc:
            message = str(exc)
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            self.show_lightcurve_failure_dialog(message)
            return
        blend_assessment = assess_target_catalog_blend(
            self.selected_target.catalog_object,
            self.reference_frame,
            target_blend_aperture_settings,
            self.append_log,
            warning_limit_mag=SERIES_TARGET_BLEND_WARNING_LIMIT_MAG,
            invalid_limit_mag=SERIES_TARGET_BLEND_ERROR_LIMIT_MAG,
        )
        self.clear_comparison_star_state()
        diagnostic_curve_requested = False
        if blend_assessment.status == QUALITY_STATUS_OK:
            self.append_log(blend_assessment.message)
        elif blend_assessment.status == QUALITY_STATUS_INVALID:
            self.append_log(f"WARNING: {blend_assessment.message}")
            diagnostic_decision = self.request_contaminated_target_diagnostic_curve()
            if diagnostic_decision is True:
                diagnostic_curve_requested = True
            else:
                message = target_contamination_failure_message()
                self.fail_lightcurve_run(message)
                self.lightcurve_status_label.setText(message)
                self.append_log(f"ERROR: {message}")
                if diagnostic_decision is None:
                    self.show_lightcurve_failure_dialog(message)
                return
        else:
            self.append_log(
                f"WARNING: {blend_assessment.message} "
                "All aperture measurements will run before this quality decision is applied."
            )

        self.lightcurve_status_label.setText(
            "Selecting Comp Stars and Check Star automatically before creating the Light Curve."
        )
        selection_started = time.monotonic()
        if not self.find_comparison_stars():
            message = (
                self.comparison_selection_failure_reason
                or "Comparison-star selection failed. See the log for details."
            )
            self.fail_lightcurve_run(message)
            self.lightcurve_status_label.setText(
                f"Light Curve not created: {message}"
            )
            self.show_lightcurve_failure_dialog(
                f"Could not select Comp Stars.\n\n{message}",
            )
            return
        self.append_log(
            "Comp/check selection completed in "
            f"{time.monotonic() - selection_started:.1f}s."
        )

        try:
            target_ra, target_dec = coordinate_pair(
                self.selected_target.catalog_object,
                "Target",
            )
            comp_rows = [
                (
                    index,
                    comp,
                    *coordinate_pair(comp, f"Comparison star {index}"),
                )
                for index, comp in enumerate(self.comparison_stars, start=1)
            ]
            check_row = (
                (
                    self.check_star,
                    *coordinate_pair(self.check_star, "Check Star"),
                )
                if self.check_star is not None
                else None
            )
        except ValueError as exc:
            self.fail_lightcurve_run(str(exc))
            self.append_log(f"ERROR: {exc}")
            self.show_lightcurve_failure_dialog(str(exc), critical=True)
            return

        work_dir = self.work_sequence_directory()
        output_dir = self.lightcurve_output_directory()
        if work_dir is None or output_dir is None:
            self.fail_lightcurve_run("Select a FITS folder first.")
            self.show_lightcurve_failure_dialog("Select a FITS folder first.")
            return
        sequence_name = WORK_SEQUENCE_NAME
        all_sequence_files = sequence_fits_files(work_dir, sequence_name)
        if not all_sequence_files:
            message = f"No sequence FITS files found for {sequence_name}"
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            self.show_lightcurve_failure_dialog(
                "No prepared FITS files found.\n\nRun Detect Variables first.",
            )
            return
        sequence_files = solved_sequence_fits_files(work_dir, sequence_name)
        skipped_unsolved = len(all_sequence_files) - len(sequence_files)
        if not sequence_files:
            message = f"No plate-solved sequence FITS files found for {sequence_name}"
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            self.show_lightcurve_failure_dialog(
                "No solved FITS files found.\n\nRun Detect Variables again.",
            )
            return
        source_dir = self.current_source_directory()
        filter_metadata = infer_image_filter_metadata(
            source_dir,
            tuple(sequence_files[:3]),
        )
        if filter_metadata is None:
            message = (
                "No unambiguous supported FITS FILTER/CHANMODE metadata found. "
                "Run CFA Channels / Stack first or use FITS files with "
                "FILTER=L, FILTER=G or FILTER=V."
            )
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            self.show_lightcurve_failure_dialog(
                "Unsupported FITS filter metadata.\n\nRun CFA Channels / Stack first.",
            )
            return
        image_source, aavso_filter, origin_marker = filter_metadata
        # Registration and plate solving may drop NCOMBINE from their work
        # copies. Provenance must therefore be checked on the selected input
        # source files, never on the temporary sequence files.
        source_input_files = (
            sorted(
                path
                for path in source_dir.iterdir()
                if is_fits_file(path) and not is_plate_solve_artifact(path)
            )
            if source_dir is not None and source_dir.is_dir()
            else []
        )
        source_provenance = frame_series_provenance(source_input_files)
        source_input_fingerprint = input_series_fingerprint(source_input_files)
        binning_allowed = source_provenance == "SINGLE_FRAME_LG_SERIES"
        stack_image_count = (
            uniform_stack_image_count(source_input_files)
            if source_provenance == "DERIVED_FITS"
            else None
        )
        self.append_log(
            f"Photometry source/filter: image_source={image_source}, "
            f"AAVSO filter={aavso_filter}."
        )
        if origin_marker:
            self.append_log(f"FITS provenance marker: {origin_marker}")
        self.append_log(
            f"Binning source provenance: {source_provenance} "
            f"({len(source_input_files)} selected input FITS files)."
        )

        instrumental_csv = self.selected_target_instrumental_csv()
        result_csv = self.selected_target_result_csv()
        if instrumental_csv is None or result_csv is None:
            self.fail_lightcurve_run("Select a variable first.")
            self.show_lightcurve_failure_dialog("Select a variable first.")
            return
        for path in (instrumental_csv, result_csv):
            if path.exists():
                path.unlink()

        self.series_optimized_aperture_settings = None
        try:
            aperture_settings = require_fixed_fwhm_aperture_settings(self.reference_frame)
        except RuntimeError as exc:
            message = str(exc)
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            self.show_lightcurve_failure_dialog(message)
            return
        self.append_log(
            "Series ringset fixed default: "
            f"aperture={aperture_settings.aperture_radius_px:.2f}px, "
            f"annulus={aperture_settings.annulus_inner_px:.2f}-"
            f"{aperture_settings.annulus_outer_px:.2f}px, "
            f"fwhm={aperture_settings.fwhm_px:.2f}px; "
            f"{aperture_settings.note}."
        )
        self.append_log(
            f"Running aperture photometry on {len(sequence_files)} frame(s), "
            f"1 target and {len(comp_rows)} Comp Stars."
        )
        if check_row is not None:
            check_obj = check_row[0]
            self.append_log(
                f"Check Star photometry enabled: {check_obj.name or '(unnamed)'}."
            )
        else:
            self.append_log("No Check Star selected for this Light Curve.")
        if skipped_unsolved:
            self.append_log(
                f"Skipping {skipped_unsolved} unsolved frame(s) without WCS."
            )
        self.append_log(
            "Aperture settings: "
            f"{aperture_settings.mode}; "
            f"reference FWHM={aperture_settings.fwhm_px:.2f}px; "
            f"radius={aperture_settings.aperture_radius_px:.2f}px, "
            f"annulus={aperture_settings.annulus_inner_px:.2f}-"
            f"{aperture_settings.annulus_outer_px:.2f}px; "
            f"{aperture_settings.note}."
        )

        target_name = self.selected_target.catalog_object.name.strip() or "target"
        target_catalog_mag = self.selected_target.catalog_object.magnitude_float()
        target_catalog_id = self.selected_target.catalog_object.value_for(("OID", "oid")) or target_name
        measurement_specs: list[dict[str, object]] = [
            {
                "object_id": target_name,
                "role": "target",
                "ra_deg": target_ra,
                "dec_deg": target_dec,
                "catalog_mag": target_catalog_mag,
                "catalog_source": "VSX",
                "catalog_id": target_catalog_id,
                "image_source": image_source,
                "aavso_filter": aavso_filter,
            }
        ]
        for comp_index, comp, comp_ra, comp_dec in comp_rows:
            comp_name = comp.catalog_id.strip() or comp.name.strip() or f"comp_{comp_index:02d}"
            measurement_specs.append(
                {
                    "object_id": comp_name,
                    "role": "comparison",
                    "ra_deg": comp_ra,
                    "dec_deg": comp_dec,
                    "catalog_mag": comp.magnitude_float(),
                    "catalog_source": comp.catalog_source or APASS_DR10_SOURCE_NAME,
                    "catalog_id": comp.catalog_id,
                    "catalog_mag_error": comp.magnitude_error_float(),
                    "catalog_nobs": comp.nobs_v_int(),
                    "catalog_mag_b": comp.band_float("mag_b"),
                    "catalog_mag_g": comp.band_float("mag_g"),
                    "catalog_mag_r": comp.band_float("mag_r"),
                    "catalog_b_minus_v": comp.color_index_float("mag_b", "mag_v"),
                    "catalog_g_minus_r": comp.color_index_float("mag_g", "mag_r"),
                    "image_source": image_source,
                    "aavso_filter": aavso_filter,
                }
            )
        if check_row is not None:
            check_obj, check_ra, check_dec = check_row
            check_name = check_obj.catalog_id.strip() or check_obj.name.strip() or "check"
            measurement_specs.append(
                {
                    "object_id": check_name,
                    "role": "check",
                    "ra_deg": check_ra,
                    "dec_deg": check_dec,
                    "catalog_mag": check_obj.magnitude_float(),
                    "catalog_source": check_obj.catalog_source or APASS_DR10_SOURCE_NAME,
                    "catalog_id": check_obj.catalog_id,
                    "catalog_mag_error": check_obj.magnitude_error_float(),
                    "catalog_nobs": check_obj.nobs_v_int(),
                    "catalog_mag_b": check_obj.band_float("mag_b"),
                    "catalog_mag_g": check_obj.band_float("mag_g"),
                    "catalog_mag_r": check_obj.band_float("mag_r"),
                    "catalog_b_minus_v": check_obj.color_index_float("mag_b", "mag_v"),
                    "catalog_g_minus_r": check_obj.color_index_float("mag_g", "mag_r"),
                    "image_source": image_source,
                    "aavso_filter": aavso_filter,
                }
            )
        reference_frame_measurements = aperture_measurements_for_frame(
            self.reference_frame.path,
            self.reference_frame.index,
            measurement_specs,
            aperture_settings.aperture_radius_px,
            aperture_settings.annulus_inner_px,
            aperture_settings.annulus_outer_px,
        )
        reference_frame_measurements, reference_linearity = apply_frame_bright_linearity_check(
            reference_frame_measurements
        )
        if reference_linearity is not None:
            self.append_log(
                "Series reference-frame bright-linearity check: "
                f"{reference_linearity.status}/{reference_linearity.flag}; "
                f"refs={reference_linearity.reference_count}; "
                f"{reference_linearity.note}."
            )
            if reference_linearity.status == QUALITY_STATUS_INVALID:
                message = (
                    "Light Curve rejected by reference-frame bright-linearity check: "
                    f"{reference_linearity.note}."
                )
                self.fail_lightcurve_run(message)
                self.append_log(f"ERROR: {message}")
                self.show_lightcurve_failure_dialog(
                    "Light Curve rejected.\n\n"
                    f"{reference_linearity.note}",
                )
                return
        photometry_started = time.monotonic()
        measurements, frame_linearity_counts = measure_series_sequence(
            sequence_files,
            measurement_specs,
            aperture_settings,
            progress=self.append_log,
            event_pump=QApplication.processEvents,
        )
        self.append_log(
            "Frame aperture measurements completed in "
            f"{time.monotonic() - photometry_started:.1f}s."
        )
        if frame_linearity_counts:
            self.append_log(
                "Series frame bright-linearity summary: "
                f"{format_counter(frame_linearity_counts)}."
            )

        if not measurements:
            message = "No aperture measurements were produced."
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            run_log = self.write_run_log(output_dir)
            self.append_log(f"Run log written: {run_log.name}")
            self.write_run_log(output_dir)
            self.show_lightcurve_failure_dialog("No measurements were created.")
            return

        measurements, local_background_assessment = apply_series_local_background_quality(
            measurements
        )
        if local_background_assessment.baseline_ratio is None:
            self.append_log(
                "Series local-background guard unavailable: "
                f"eligible frames={local_background_assessment.eligible_count}, "
                f"need={SERIES_LOCAL_BACKGROUND_MIN_FRAMES}."
            )
        else:
            self.append_log(
                "Series local-background guard: "
                f"method={local_background_assessment.method}, "
                f"eligible={local_background_assessment.eligible_count}, "
                f"baseline ratio={local_background_assessment.baseline_ratio:.4f}, "
                f"robust sigma={local_background_assessment.robust_sigma:.4f}; "
                f"warning={local_background_assessment.warning_count}, "
                f"rejected={local_background_assessment.invalid_count}."
            )

        measurements_before_target_blend = measurements
        measurements = apply_target_blend_assessment_to_measurements(
            measurements_before_target_blend,
            blend_assessment,
        )
        if blend_assessment.status != QUALITY_STATUS_OK:
            affected_targets = sum(
                1 for item in measurements if item.role == "target"
            )
            self.append_log(
                "Target blend quality applied after aperture measurement: "
                f"{blend_assessment.status}/{blend_assessment.flag}; "
                f"target rows={affected_targets}."
            )

        measurements, temporal_spike_assessment = apply_series_temporal_spike_quality(
            measurements
        )
        self.append_log(
            "Series temporal target-spike guard: "
            f"method={temporal_spike_assessment.method}, "
            f"eligible={temporal_spike_assessment.eligible_count}, "
            f"median cadence="
            f"{format_optional_float(temporal_spike_assessment.median_cadence_seconds, 2)} s, "
            f"rejected={temporal_spike_assessment.rejected_count}."
        )

        try:
            result_metadata = self.result_metadata(
                measurements,
                aperture_settings,
                sequence_files[0],
            )
            result_metadata.update(
                {
                    "SOURCE_PROVENANCE": source_provenance,
                    "BINNING_ALLOWED": "1" if binning_allowed else "0",
                    "STACK_IMAGES_PER_RESULT": (
                        "" if stack_image_count is None else stack_image_count
                    ),
                    "SOURCE_INPUT_FINGERPRINT_SHA256": source_input_fingerprint,
                    "LOCAL_BACKGROUND_QC_METHOD": local_background_assessment.method,
                    "LOCAL_BACKGROUND_QC_ELIGIBLE": local_background_assessment.eligible_count,
                    "LOCAL_BACKGROUND_QC_WARNINGS": local_background_assessment.warning_count,
                    "LOCAL_BACKGROUND_QC_REJECTED": local_background_assessment.invalid_count,
                    "LOCAL_BACKGROUND_QC_BASELINE_RATIO": (
                        ""
                        if local_background_assessment.baseline_ratio is None
                        else f"{local_background_assessment.baseline_ratio:.8f}"
                    ),
                    "LOCAL_BACKGROUND_QC_ROBUST_SIGMA": (
                        ""
                        if local_background_assessment.robust_sigma is None
                        else f"{local_background_assessment.robust_sigma:.8f}"
                    ),
                    "TEMPORAL_SPIKE_QC_METHOD": temporal_spike_assessment.method,
                    "TEMPORAL_SPIKE_QC_ELIGIBLE": temporal_spike_assessment.eligible_count,
                    "TEMPORAL_SPIKE_QC_REJECTED": temporal_spike_assessment.rejected_count,
                    "TEMPORAL_SPIKE_QC_MEDIAN_CADENCE_SECONDS": (
                        ""
                        if temporal_spike_assessment.median_cadence_seconds is None
                        else f"{temporal_spike_assessment.median_cadence_seconds:.6f}"
                    ),
                }
            )
        except Exception as exc:
            message = f"Result metadata could not be created: {exc}"
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            self.show_lightcurve_failure_dialog(
                f"Could not create result metadata.\n\n{exc}",
                critical=True,
            )
            return

        write_instrumental_photometry(instrumental_csv, measurements, result_metadata)
        calibration_stats = write_calibrated_light_curve(
            result_csv,
            measurements,
            result_metadata,
        )
        rows_written = calibration_stats.rows_written
        valid_target = sum(1 for item in measurements if item.role == "target" and item.valid)
        valid_comp = sum(1 for item in measurements if item.role == "comparison" and item.valid)
        valid_check = sum(1 for item in measurements if item.role == "check" and item.valid)
        quality_summary = photometry_quality_summary(len(sequence_files), rows_written, measurements)
        self.append_log(
            f"Instrumental photometry written: {instrumental_csv.name} "
            f"({valid_target} valid target, {valid_comp} valid comparison, "
            f"{valid_check} valid check measurements)."
        )
        if calibration_stats.scatter_limit is not None:
            self.append_log(
                "Dynamic comparison scatter filter: "
                f"median={calibration_stats.median_scatter:.4f} mag, "
                f"robust sigma={calibration_stats.robust_scatter_sigma:.4f} mag, "
                f"limit={calibration_stats.scatter_limit:.4f} mag; "
                f"rejected {calibration_stats.rejected_high_scatter} frame(s)."
            )
        if rows_written == 0:
            compact_rejection = False
            if blend_assessment.status == QUALITY_STATUS_INVALID:
                message = target_contamination_failure_message()
                compact_rejection = True
            elif all_target_measurements_rejected_by_annulus_contamination(measurements):
                message = annulus_contamination_failure_message()
                compact_rejection = True
            else:
                message = (
                    "No calibrated Light Curve rows were written. "
                    f"Need at least {MIN_VALID_COMP_STARS} valid Comp Stars per frame "
                    "and a stable Comp Star zero point."
                )
            self.fail_lightcurve_run(message)
            self.append_log(f"ERROR: {message}")
            for line in photometry_summary_lines(
                len(sequence_files),
                rows_written,
                measurements,
                result_csv,
            ):
                self.append_log(line)
            for line in comparison_star_stability_lines(len(sequence_files), measurements):
                self.append_log(line)
            try:
                result_csv.unlink(missing_ok=True)
            except OSError as exc:
                self.append_log(
                    f"WARNING: Could not remove empty Light Curve result {result_csv}: {exc}"
                )
            else:
                self.append_log(
                    f"Empty Light Curve result removed after failed photometry: {result_csv}"
                )
            run_log = self.write_run_log(output_dir)
            self.append_log(f"Run log written: {run_log.name}")
            self.write_run_log(output_dir)
            diagnostic_offer_handled = False
            if blend_assessment.status == QUALITY_STATUS_INVALID:
                diagnostic_offer_handled = self.offer_contaminated_target_diagnostic_curve(
                    measurements_before_target_blend,
                    measurements,
                    result_metadata,
                    target_name,
                    source_dir.name if source_dir is not None else "",
                    confirmed=diagnostic_curve_requested,
                )
            if not diagnostic_offer_handled:
                self.show_lightcurve_failure_dialog(
                    (
                        message
                        if compact_rejection
                        else "No usable Light Curve points were created.\n\nSee the log for details."
                    ),
                )
            return

        self.loaded_lightcurve_csv = result_csv
        self.loaded_lightcurve_target_name = target_name
        self.loaded_lightcurve_source_label = source_dir.name if source_dir is not None else ""
        self.current_result_origin = "created"
        self.current_exports.clear()
        self.set_result_status("created")
        self.refresh_bav_tab_view()
        self.lightcurve_status_label.setText(
            f"Light Curve written: {rows_written} row(s), "
            f"target warnings={quality_summary['target_warning']}, "
            f"target errors={quality_summary['target_invalid']}, "
            f"comp ignored={quality_summary['comp_invalid']}. "
            f"Comp Stars={len(self.comparison_stars)}, "
            f"check={'yes' if self.check_star is not None else 'no'}. "
            f"Output: {result_csv}"
        )
        self.export_status_label.setText(f"Current export source: {result_csv}")
        self.automatic_lightcurve_finished.emit(True, str(result_csv))
        batch_running = bool(
            self.batch_tab is not None and getattr(self.batch_tab, "running", False)
        )
        if not batch_running:
            self.tabs.setCurrentWidget(self.lightcurve_tab)
        self.append_log(f"Calibrated Light Curve written: {result_csv} ({rows_written} row(s)).")
        for line in photometry_summary_lines(
            len(sequence_files),
            rows_written,
            measurements,
            result_csv,
        ):
            self.append_log(line)
        for line in comparison_star_stability_lines(len(sequence_files), measurements):
            self.append_log(line)
        for line in check_star_summary_lines(result_csv):
            self.append_log(line)
        run_log = self.write_run_log(output_dir)
        self.append_log(f"Run log written: {run_log.name}")
        self.write_run_log(output_dir)
        completion_text = (
            "Aperture photometry complete:\n\n"
            f"{photometry_quality_summary_text(quality_summary)}\n\n"
            f"{instrumental_csv}\n"
            f"{result_csv}"
        )
        if photometry_summary_needs_warning_dialog(quality_summary):
            self.append_log("WARNING: Light Curve completed with rejected or warning measurements.")
        self.append_log(completion_text)

    def plot_light_curve(self) -> None:
        if self.loaded_lightcurve_csv is not None:
            if not self.loaded_lightcurve_csv.exists():
                QMessageBox.warning(
                    self,
                    "Plot Light Curve",
                    "Loaded Light Curve CSV not found.",
                )
                return
            diagnostic = self.current_result_origin == "diagnostic"
            plot_target_name = self.loaded_lightcurve_target_name
            if diagnostic:
                plot_target_name += " — DIAGNOSTIC: TARGET CONTAMINATION — NOT FOR EXPORT"
            self.plot_light_curve_csv(
                self.loaded_lightcurve_csv,
                self.loaded_lightcurve_csv.with_suffix(".png"),
                plot_target_name,
                self.loaded_lightcurve_source_label,
                allow_extremum=not diagnostic,
                include_invalid=diagnostic,
            )
            return

        output_dir = self.lightcurve_output_directory()
        if output_dir is None:
            QMessageBox.warning(self, "Plot Light Curve", "Select a FITS folder first.")
            return

        result_csv = self.selected_target_result_csv()
        if result_csv is None:
            QMessageBox.warning(self, "Plot Light Curve", "Select a variable first.")
            return
        if not result_csv.exists():
            QMessageBox.warning(
                self,
                "Plot Light Curve",
                "Light Curve CSV not found.",
            )
            return
        output_png = self.selected_target_plot_png()
        target_name = (
            self.selected_target.catalog_object.name.strip()
            if self.selected_target is not None
            else "Target"
        )
        source_dir = self.current_source_directory()
        source_label = source_dir.name if source_dir is not None else ""
        self.plot_light_curve_csv(
            result_csv,
            output_png,
            target_name or "Target",
            source_label,
        )

    def plot_light_curve_from_csv_for_batch(self, result_csv: Path | str) -> None:
        """Plot one result CSV from the required Batch component without enabling fit tools."""

        busy_message = self.busy_context_change_message("load another result")
        if busy_message is not None:
            raise RuntimeError(busy_message)
        path = Path(result_csv)
        result_metadata = read_result_metadata_header(path)
        if self.lightcurve_results_module is None:
            self.lightcurve_results_module = load_lightcurve_results_module()
        parsed_filename = self.lightcurve_results_module.parse_result_filename(path)
        fallback_name = parsed_filename.target_stem if parsed_filename is not None else path.stem
        target_name = result_metadata.get("OBJECT_NAME", "").strip() or fallback_name
        target_name = target_name.replace("_", " ") or "Target"
        self.loaded_lightcurve_csv = path
        self.loaded_lightcurve_target_name = target_name
        self.set_selected_target_labels(target_name)
        self.loaded_lightcurve_source_label = path.parent.name
        self.current_result_origin = "loaded"
        self.current_exports.clear()
        self.set_result_status("loaded")
        self.export_status_label.setText(f"Current export source: {path}")
        self.plot_light_curve_csv(
            path,
            None,
            target_name,
            path.parent.name,
            allow_extremum=False,
        )
        self.refresh_plugin_tab_views()

    def describe_batch_result(self, result_csv: Path | str) -> tuple[str, str]:
        """Return the compact OK/WARN/ERROR status shown in the Batch table."""

        path = Path(result_csv)
        if not result_csv_contains_single_field_zp_rows(path):
            return "OK", "Light Curve ready"
        rows: list[dict[str, str]] = []
        with path.open(newline="") as handle:
            rows.extend(csv_data_dict_reader(handle))
        if not rows:
            return "ERROR", "Single Measurement result has no data row"
        row = rows[0]
        quality_status = csv_text(row.get("quality_status"), QUALITY_STATUS_OK).upper()
        quality_flag = csv_text(row.get("quality_flag"), "")
        magnitude = format_optional_float(
            first_result_row_float(row, "target_calibrated_mag", "calibrated_mag"),
            4,
        )
        error = format_optional_float(
            first_result_row_float(
                row,
                "target_calibrated_mag_error",
                "calibrated_mag_error",
            ),
            4,
        )
        measurement = f"mag={magnitude} +/- {error}"
        if "INFORMATIONAL_FIELD_ZP" in quality_flag.split("|"):
            return "WARN", f"informational only; {measurement}; export disabled"
        if not result_row_is_valid(row) or quality_status == QUALITY_STATUS_INVALID:
            return "ERROR", f"{quality_flag or 'INVALID'}; {measurement} (diagnostic only)"
        if quality_status == QUALITY_STATUS_WARNING:
            return "WARN", f"{quality_flag or 'quality warning'}; {measurement}"
        return "OK", measurement

    def show_result_from_csv_for_batch(self, result_csv: Path | str) -> None:
        """Show a Batch result according to its CSV content."""

        self.set_current_lightcurve_from_plugin(
            result_csv,
            "",
            plot_lightcurve=True,
            allow_extremum=True,
        )
        self.tabs.setCurrentWidget(self.lightcurve_tab)

    def open_light_curve_csv(self) -> None:
        busy_message = self.busy_context_change_message("load another result")
        if busy_message is not None:
            QMessageBox.information(self, "Operation Running", busy_message)
            return
        start_dir = self.current_results_directory() or self.current_source_directory() or Path.home()
        if self.photometry_mode == MODE_SINGLE_MEASUREMENT:
            title = "Open Single Measurement CSV"
            file_filter = (
                "Single Measurement CSV (*_single_field_zp_measurement.csv *.csv);;"
                "All CSV Files (*.csv)"
            )
        else:
            title = "Open Light Curve CSV"
            file_filter = "Light Curve CSV (*_result_curve*.csv result_curve.csv *.csv)"
        selected, _ = QFileDialog.getOpenFileName(
            self,
            title,
            str(start_dir),
            file_filter,
        )
        if not selected:
            return

        result_csv = Path(selected).expanduser()
        if not result_csv.exists():
            QMessageBox.warning(self, title, "Result CSV not found.")
            return
        try:
            self.set_current_lightcurve_from_plugin(
                result_csv,
                "",
                plot_lightcurve=True,
            )
        except Exception as exc:
            QMessageBox.warning(self, title, f"Could not load result CSV:\n{exc}")
            self.append_log(f"WARNING: Result CSV could not be loaded: {exc}")

    def open_result_browser(self) -> None:
        """Select a result CSV from a scanned results folder."""

        busy_message = self.busy_context_change_message("load another result source")
        if busy_message is not None:
            QMessageBox.information(self, "Operation Running", busy_message)
            return
        self._open_result_browser(bav_only=False)

    def open_bav_result_browser(self) -> None:
        """Select a result CSV with existing BAV exports."""

        busy_message = self.busy_context_change_message("load another result source")
        if busy_message is not None:
            QMessageBox.information(self, "Operation Running", busy_message)
            return
        self._open_result_browser(bav_only=True)

    def _open_result_browser(self, *, bav_only: bool) -> None:
        """Select a result CSV from a scanned folder, optionally requiring BAV files."""

        browser_single = (
            not bav_only and self.selected_photometry_mode() == MODE_SINGLE_MEASUREMENT
        )
        browser_result_kind = "single_measurement" if browser_single else "lightcurve"

        dialog_attribute = (
            "bav_result_browser_dialog" if bav_only else "result_browser_dialog"
        )
        existing_dialog = getattr(self, dialog_attribute)
        if existing_dialog is not None and existing_dialog.isVisible():
            existing_dialog.raise_()
            existing_dialog.activateWindow()
            return

        window_title = (
            "BAV Results"
            if bav_only
            else ("Open Single Measurement Result" if browser_single else "Open Light Curve Result")
        )
        item_label = (
            "BAV result(s)"
            if bav_only
            else ("Single Measurement result(s)" if browser_single else "Light Curve result(s)")
        )
        start_dir = self.current_results_directory() or self.current_source_directory() or Path.home()
        selected = QFileDialog.getExistingDirectory(
            self,
            "Open BAV Results Folder" if bav_only else "Open Result Folder",
            str(start_dir),
        )
        if not selected:
            return

        results_dir = Path(selected).expanduser()
        try:
            if self.lightcurve_results_module is None:
                self.lightcurve_results_module = load_lightcurve_results_module()
            discovery_name = (
                "discover_bav_lightcurve_results"
                if bav_only
                else (
                    "discover_single_measurement_results"
                    if browser_single
                    else "discover_lightcurve_results"
                )
            )
            discover_results = getattr(self.lightcurve_results_module, discovery_name)
        except Exception as exc:
            QMessageBox.warning(
                self,
                window_title,
                f"Could not scan result folder:\n{exc}",
            )
            self.append_log(f"WARNING: Result folder scan failed: {exc}")
            return

        def scan_results_folder() -> tuple[list[object], object | None]:
            detailed_discovery = getattr(
                self.lightcurve_results_module,
                "discover_lightcurve_results_with_diagnostics",
                None,
            )
            if not callable(detailed_discovery):
                return list(discover_results(results_dir)), None

            progress_dialog = QProgressDialog(
                "Scanning result folders...",
                "Cancel",
                0,
                0,
                self,
            )
            progress_dialog.setWindowTitle(window_title)
            progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
            progress_dialog.setMinimumDuration(400)
            progress_dialog.setAutoClose(False)
            progress_dialog.setAutoReset(False)
            scan_running = {"active": True}

            def show_progress_if_running() -> None:
                if scan_running["active"]:
                    progress_dialog.show()

            def update_progress(folder_count: int, result_count: int) -> None:
                progress_dialog.setLabelText(
                    f"Scanning result folders: {folder_count}\n"
                    f"Result CSVs found: {result_count}"
                )
                QApplication.processEvents()

            QTimer.singleShot(400, show_progress_if_running)
            try:
                report = detailed_discovery(
                    results_dir,
                    bav_only=bav_only,
                    result_kind=browser_result_kind,
                    include_archive=bav_only,
                    progress_callback=update_progress,
                    cancel_requested=progress_dialog.wasCanceled,
                )
            finally:
                scan_running["active"] = False
                progress_dialog.close()
                progress_dialog.deleteLater()
            return list(report.candidates), report

        def scan_report_text(report: object | None) -> str:
            if report is None:
                return ""
            details = [
                f"{getattr(report, 'folders_scanned', 0)} folder(s) scanned",
                f"{getattr(report, 'result_csv_found', 0)} result CSV(s) found",
            ]
            issue_count = len(getattr(report, "issues", ()))
            invalid_count = len(getattr(report, "invalid_results", ()))
            symlink_count = int(getattr(report, "symlink_directories_skipped", 0))
            archive_count = int(getattr(report, "archive_directories_skipped", 0))
            if bool(getattr(report, "cancelled", False)):
                details.append("CANCELLED: partial list")
            if issue_count:
                details.append(f"WARNING: {issue_count} path(s) unreadable")
            if invalid_count:
                details.append(f"WARNING: {invalid_count} invalid result(s) skipped")
            if symlink_count:
                details.append(f"{symlink_count} linked folder(s) skipped")
            if archive_count:
                details.append(f"{archive_count} archive folder(s) skipped")
            return " | ".join(details)

        def scan_report_is_incomplete(report: object | None) -> bool:
            if report is None:
                return False
            return bool(
                getattr(report, "cancelled", False)
                or getattr(report, "issues", ())
                or getattr(report, "invalid_results", ())
                or getattr(report, "symlink_directories_skipped", 0)
            )

        def log_scan_report(report: object | None) -> None:
            if report is None:
                return
            self.append_log(f"{window_title} scan: {scan_report_text(report)}")
            issues = tuple(getattr(report, "issues", ()))
            invalid_results = tuple(getattr(report, "invalid_results", ()))
            for issue in issues[:20]:
                self.append_log(
                    f"WARNING: Result scan could not read {issue.path}: {issue.message}"
                )
            if len(issues) > 20:
                self.append_log(
                    f"WARNING: {len(issues) - 20} additional result scan issue(s) omitted."
                )
            for invalid_result in invalid_results[:20]:
                self.append_log(
                    f"WARNING: Invalid result skipped: {invalid_result.path}: "
                    f"{invalid_result.message}"
                )
            if len(invalid_results) > 20:
                self.append_log(
                    f"WARNING: {len(invalid_results) - 20} additional invalid result(s) omitted."
                )

        try:
            candidates, scan_report = scan_results_folder()
        except Exception as exc:
            QMessageBox.warning(
                self,
                window_title,
                f"Could not scan result folder:\n{exc}",
            )
            self.append_log(f"WARNING: Result folder scan failed: {exc}")
            return
        log_scan_report(scan_report)

        if not candidates:
            message = (
                "No light-curve result CSVs with BAV output files found."
                if bav_only
                else (
                    "No Single Measurement result CSVs found."
                    if browser_single
                    else "No light-curve result CSVs found."
                )
            )
            report_text = scan_report_text(scan_report)
            if report_text:
                message = f"{message}\n\n{report_text}"
            message_box = QMessageBox.warning if scan_report_is_incomplete(scan_report) else QMessageBox.information
            message_box(self, window_title, message)
            self.append_log(f"{message} Folder: {results_dir}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(window_title)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.resize(1210 if browser_single else 940, 420)
        setattr(self, dialog_attribute, dialog)
        layout = QVBoxLayout(dialog)

        status_label = QLabel()
        status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        def update_browser_status(loaded_text: str = "") -> None:
            first_line = loaded_text or f"{len(candidates)} {item_label}: {results_dir}"
            report_text = scan_report_text(scan_report)
            status_label.setText(
                f"{first_line}\n{report_text}" if report_text else first_line
            )
            warning_color = THEME_COLORS["warning"] if scan_report_is_incomplete(scan_report) else ""
            status_label.setStyleSheet(f"color: {warning_color};" if warning_color else "")

        update_browser_status()

        table = QTableWidget(len(candidates), 11 if browser_single else 10)
        table.setHorizontalHeaderLabels(
            (
                [
                    "Star", "VSX Type", "Date", "Image folder", "Mag", "Error",
                    "Status", "Refs", "Check Δ", "AAVSO", "BAV",
                ]
                if browser_single
                else [
                    "Star", "VSX Type", "Date", "Image folder", "Variant", "Rows",
                    "Check", "PNG", "AAVSO", "BAV",
                ]
            )
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(False)
        column_widths = (
            (170, 90, 95, 270, 90, 70, 150, 55, 75, 65, 55)
            if browser_single else
            (170, 90, 95, 270, 95, 60, 55, 65, 55, 55)
        )
        for column_index, width in enumerate(column_widths):
            table.setColumnWidth(column_index, width)

        def candidate_values(candidate: object) -> list[str]:
            if browser_single:
                quality_status = str(getattr(candidate, "quality_status", "") or "OK").upper()
                quality_flag = str(getattr(candidate, "quality_flag", "") or "")
                status = "ERROR" if quality_status == QUALITY_STATUS_INVALID else (
                    "WARN" if quality_status == QUALITY_STATUS_WARNING else "OK"
                )
                if quality_flag:
                    status = f"{status}: {quality_flag}"
                return [
                    candidate.target_name,
                    candidate.variable_type,
                    candidate.report_date,
                    candidate.source_directory_name,
                    str(getattr(candidate, "calibrated_mag", "")),
                    str(getattr(candidate, "calibrated_mag_error", "")),
                    status,
                    str(getattr(candidate, "field_reference_used", "")),
                    str(getattr(candidate, "check_delta_mag", "")),
                    "yes" if candidate.aavso_exists else "no",
                    "yes" if candidate.bav_file_count else "no",
                ]
            return [
                candidate.target_name,
                candidate.variable_type,
                candidate.report_date,
                candidate.source_directory_name,
                str(getattr(candidate, "result_variant_label", "Raw")),
                str(candidate.row_count),
                "yes" if candidate.has_calibrated_check else "no",
                "yes" if candidate.png_exists else "no",
                "yes" if candidate.aavso_exists else "no",
                str(candidate.bav_file_count),
            ]

        numeric_columns = {4, 5, 7, 8} if browser_single else {5, 9}

        def populate_browser_table() -> None:
            sorting_enabled = table.isSortingEnabled()
            table.setSortingEnabled(False)
            table.setRowCount(len(candidates))
            for row_index, candidate in enumerate(candidates):
                for column_index, value in enumerate(candidate_values(candidate)):
                    item = ResultBrowserTableItem(
                        value, numeric=column_index in numeric_columns
                    )
                    item.setData(Qt.ItemDataRole.UserRole, row_index)
                    table.setItem(row_index, column_index, item)
            table.setSortingEnabled(sorting_enabled)

        populate_browser_table()
        table.setSortingEnabled(True)
        table.selectRow(0)
        layout.addWidget(table)

        button_layout = QHBoxLayout()
        preview_button = QPushButton("Preview")
        load_button = QPushButton("Load")
        rescan_button = QPushButton("Rescan")
        close_button = QPushButton("Close")
        button_layout.addWidget(preview_button)
        button_layout.addWidget(load_button)
        button_layout.addWidget(rescan_button)
        button_layout.addStretch(1)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        if browser_single:
            preview_button.setVisible(False)
            load_button.setText("Load")

        def selected_candidate() -> object | None:
            row = table.currentRow()
            item = table.item(row, 0) if row >= 0 else None
            candidate_index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if not isinstance(candidate_index, int) or not 0 <= candidate_index < len(candidates):
                return None
            return candidates[candidate_index]

        def preview_selected() -> None:
            candidate = selected_candidate()
            if candidate is None:
                return
            try:
                plot_data = load_light_curve_plot_data(candidate.result_csv)
                preview_figure = Figure(figsize=(9.5, 6.0), tight_layout=True)
                draw_light_curve_figure(
                    preview_figure,
                    plot_data,
                    candidate.target_name,
                    candidate.source_directory_name,
                    show_running_mean=False,
                    include_check=False,
                )
            except Exception as exc:
                QMessageBox.warning(dialog, "Preview Result", f"Could not preview result CSV:\n{exc}")
                self.append_log(f"WARNING: Result CSV preview failed: {exc}")
                return

            preview_dialog = QDialog(dialog)
            preview_dialog.setWindowTitle(f"Preview - {candidate.target_name}")
            preview_dialog.resize(1100, 720)
            preview_layout = QVBoxLayout(preview_dialog)
            preview_canvas = FigureCanvas(preview_figure)
            preview_toolbar = NavigationToolbar(preview_canvas, preview_dialog)
            preview_layout.addWidget(preview_toolbar)
            preview_layout.addWidget(preview_canvas)
            preview_close_button = QPushButton("Close")
            preview_close_button.clicked.connect(preview_dialog.accept)
            preview_layout.addWidget(preview_close_button, alignment=Qt.AlignmentFlag.AlignRight)
            preview_dialog.exec()

        def load_selected() -> None:
            candidate = selected_candidate()
            if candidate is None:
                return
            try:
                self.set_current_lightcurve_from_plugin(
                    candidate.result_csv,
                    candidate.target_name,
                    plot_lightcurve=True,
                )
            except Exception as exc:
                QMessageBox.warning(
                    dialog,
                    window_title,
                    f"Could not load result CSV:\n{exc}",
                )
                self.append_log(f"WARNING: Result CSV could not be loaded: {exc}")
                return
            source_label = "BAV result" if bav_only else "Result"
            check_text = "available" if candidate.has_calibrated_check else "not stored"
            self.append_log(
                f"{source_label} selected from folder scan: {candidate.result_csv}; "
                f"Check Star: {check_text}."
            )
            update_browser_status(
                f"Loaded: {candidate.target_name} ({candidate.report_date}) | "
                f"Check Star: {check_text} | "
                f"{len(candidates)} {item_label}: {results_dir}"
            )

        def rescan_results() -> None:
            nonlocal scan_report
            try:
                refreshed, refreshed_report = scan_results_folder()
            except Exception as exc:
                QMessageBox.warning(
                    dialog,
                    window_title,
                    f"Could not rescan result folder:\n{exc}",
                )
                self.append_log(f"WARNING: Result folder rescan failed: {exc}")
                return
            scan_report = refreshed_report
            log_scan_report(scan_report)
            candidates[:] = refreshed
            populate_browser_table()
            if candidates:
                table.selectRow(0)
            preview_button.setEnabled(bool(candidates))
            load_button.setEnabled(bool(candidates))
            update_browser_status()

        preview_button.clicked.connect(preview_selected)
        load_button.clicked.connect(load_selected)
        table.cellDoubleClicked.connect(lambda _row, _column: load_selected())
        rescan_button.clicked.connect(rescan_results)
        close_button.clicked.connect(dialog.close)
        dialog.destroyed.connect(
            lambda _object=None: setattr(self, dialog_attribute, None)
        )
        dialog.show()

    def set_current_lightcurve_from_plugin(
        self,
        result_csv: Path | str,
        target_name: str,
        *,
        plot_lightcurve: bool = False,
        allow_extremum: bool = True,
    ) -> None:
        """Load a curve or Single Measurement result according to its content."""

        busy_message = self.busy_context_change_message("load another result")
        if busy_message is not None:
            raise RuntimeError(busy_message)
        path = Path(result_csv).expanduser()
        if not path.exists():
            raise RuntimeError(f"Result CSV not found: {path}")
        name = str(target_name or "").strip()
        result_metadata = read_result_metadata_header(path)
        if self.lightcurve_results_module is None:
            self.lightcurve_results_module = load_lightcurve_results_module()
        parsed_filename = self.lightcurve_results_module.parse_result_filename(path)
        is_single_result = result_csv_contains_single_field_zp_rows(path)
        is_diagnostic_result = result_metadata_is_diagnostic(result_metadata)
        can_plot_lightcurve = (
            (
                parsed_filename is not None
                and parsed_filename.result_kind
                == self.lightcurve_results_module.RESULT_KIND_LIGHTCURVE
            )
            or is_diagnostic_result
        )
        if not is_single_result:
            if not can_plot_lightcurve:
                raise RuntimeError(
                    "CSV is neither a Light Curve nor a Single Measurement result"
                )
            load_light_curve_plot_data(
                path,
                include_invalid=is_diagnostic_result,
            )
        if not name:
            name = result_metadata.get("OBJECT_NAME", "").strip()
            if not name:
                name = (
                    parsed_filename.target_stem
                    if parsed_filename is not None
                    else path.stem.removesuffix("_diagnostic_curve")
                )
            name = name.replace("_", " ")
        self.loaded_lightcurve_csv = path
        self.loaded_lightcurve_target_name = name or "Target"
        self.set_selected_target_labels(self.loaded_lightcurve_target_name)
        self.loaded_lightcurve_source_label = path.parent.name
        self.current_result_origin = (
            "diagnostic" if is_diagnostic_result else ("single" if is_single_result else "loaded")
        )
        self.current_exports.clear()
        self.set_result_status("warning" if is_diagnostic_result else "loaded")
        self.set_result_display_kind(single=is_single_result)
        if is_single_result:
            rows: list[dict[str, str]] = []
            with path.open(newline="") as handle:
                rows.extend(csv_data_dict_reader(handle))
            if not rows:
                raise RuntimeError("Single Measurement result has no data row")
            self.set_loaded_single_result_summary(path, rows)
            if not any(result_row_is_valid(row) for row in rows):
                informational = (
                    result_metadata.get("RESULT_PURPOSE") == "INFORMATIONAL_SINGLE_FIELD_ZP"
                    and "INFORMATIONAL_FIELD_ZP" in rows[0].get("quality_flag", "").split("|")
                )
                self.set_result_status("warning" if informational else "failed")
                if informational:
                    self.lightcurve_status_label.setText(
                        f"Informational Single Measurement loaded: {path}"
                    )
                    self.export_status_label.setText(
                        "Informational Single Measurement only; export disabled."
                    )
                    self.append_log(f"Informational Single Measurement loaded: {path}")
                else:
                    self.lightcurve_status_label.setText(
                        f"Rejected Single Measurement diagnostic loaded: {path}"
                    )
                    self.export_status_label.setText(
                        "Rejected Single Measurement diagnostic; no valid row for export."
                    )
                    self.append_log(f"Rejected Single Measurement diagnostic loaded: {path}")
            else:
                quality_status = csv_text(rows[0].get("quality_status"), QUALITY_STATUS_OK).upper()
                self.set_result_status(
                    "warning" if quality_status == QUALITY_STATUS_WARNING else "loaded"
                )
                self.lightcurve_status_label.setText(f"Single Measurement loaded: {path}")
                self.export_status_label.setText(f"Current export source: {path}")
                self.append_log(f"Single Measurement CSV loaded: {path}")
            self.update_current_result_displays()
            self.clear_extremum_fit(redraw=False, reset_status=False)
            self.refresh_action_availability()
            self.refresh_plugin_tab_views()
            return
        if is_diagnostic_result:
            self.export_status_label.setText(
                "Diagnostic curve: scientific export and archive are disabled."
            )
            self.lightcurve_status_label.setText(f"Diagnostic curve loaded: {path}")
        else:
            self.export_status_label.setText(f"Current export source: {path}")
            self.lightcurve_status_label.setText(f"Light curve loaded: {path}")
        self.update_current_result_displays()
        self.append_log(f"Light Curve CSV loaded: {path}")
        self.clear_extremum_fit(redraw=False, reset_status=False)
        if plot_lightcurve and can_plot_lightcurve:
            plot_target_name = self.loaded_lightcurve_target_name
            if is_diagnostic_result:
                plot_target_name += " — DIAGNOSTIC: TARGET CONTAMINATION — NOT FOR EXPORT"
            self.plot_light_curve_csv(
                path,
                None,
                plot_target_name,
                path.parent.name,
                allow_extremum=allow_extremum and not is_diagnostic_result,
                include_invalid=is_diagnostic_result,
            )
        self.refresh_action_availability()
        self.refresh_plugin_tab_views()

    def current_export_lightcurve(self) -> tuple[Path, str] | None:
        """Return the current valid photometry result and target name for export."""

        if (
            self.current_result_origin in {"loaded", "created", "single"}
            and self.loaded_lightcurve_csv is not None
            and self.loaded_lightcurve_csv.exists()
        ):
            try:
                metadata = read_result_metadata_header(self.loaded_lightcurve_csv)
            except OSError:
                return None
            if not result_metadata_allows_export(metadata):
                return None
            return self.loaded_lightcurve_csv, self.loaded_lightcurve_target_name or "Target"
        return None

    def current_display_result(self) -> tuple[Path, str] | None:
        """Return the displayed result even when scientific export is disabled."""

        if self.loaded_lightcurve_csv is None or not self.loaded_lightcurve_csv.exists():
            return None
        return self.loaded_lightcurve_csv, self.loaded_lightcurve_target_name or "Target"

    def current_aavso_export_directory(self) -> Path | None:
        """Return the directory where the current AAVSO report is written."""

        current = self.current_export_lightcurve()
        if current is not None:
            result_csv, _target_name = current
            return result_csv.parent / "AAVSO"
        if self.loaded_lightcurve_csv is None or not self.loaded_lightcurve_csv.exists():
            return None
        return self.loaded_lightcurve_csv.parent / "AAVSO"

    def current_aavso_export_path(self, result_csv: Path) -> Path:
        """Return the AAVSO report path for one result CSV."""

        if self.lightcurve_results_module is None:
            self.lightcurve_results_module = load_lightcurve_results_module()
        return Path(self.lightcurve_results_module.aavso_report_path(result_csv))

    def open_aavso_export_folder(self) -> None:
        """Open the folder containing the current AAVSO export."""

        folder = self.current_aavso_export_directory()
        if folder is None:
            self.append_log("WARNING: No AAVSO output folder available.")
            QMessageBox.warning(
                self,
                "AAVSO Export",
                "Select or create a result CSV first.",
            )
            return
        if not folder.is_dir():
            self.append_log(f"WARNING: AAVSO output folder not found: {folder}")
            QMessageBox.warning(
                self,
                "AAVSO Export",
                "No AAVSO output folder found for the current result.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self.append_log(f"WARNING: Could not open AAVSO output folder: {folder}")
            QMessageBox.warning(
                self,
                "AAVSO Export",
                "Could not open folder.",
            )
            return
        self.append_log(f"AAVSO output folder opened: {folder}")

    def open_aavso_apps(self) -> None:
        """Open the AAVSO apps landing page."""

        folder = self.current_aavso_export_directory()
        if folder is None:
            self.append_log("WARNING: No AAVSO path available for clipboard.")
        else:
            try:
                QApplication.clipboard().setText(os.fspath(folder))
            except Exception as exc:
                self.append_log(f"WARNING: Could not copy AAVSO path to clipboard: {exc}")
            else:
                self.append_log("AAVSO path copied to clipboard.")
        if not QDesktopServices.openUrl(QUrl(AAVSO_APPS_URL)):
            self.append_log(f"WARNING: Could not open AAVSO apps URL: {AAVSO_APPS_URL}")
            QMessageBox.warning(
                self,
                "AAVSO Apps",
                "Could not open AAVSO apps.",
            )
            return
        self.append_log(f"AAVSO apps opened: {AAVSO_APPS_URL}")

    def resolved_aavso_observer_code(self) -> tuple[str, str]:
        """Return the AAVSO observer code and the source used for export."""

        observer_code = validated_aavso_observer_code(
            self.aavso_observer_code_edit.text()
        )
        if self.aavso_observer_code_edit.text() != observer_code:
            self.aavso_observer_code_edit.setText(observer_code)
        if observer_code == getattr(self, "_aavso_bav_prefill", ""):
            return observer_code, "BAV settings"
        return observer_code, "Export tab"

    def uppercase_aavso_observer_code_input(self, text: str) -> None:
        """Uppercase AAVSO observer-code text as the user enters it."""

        normalized = text.upper()
        if normalized == text:
            return
        cursor_position = self.aavso_observer_code_edit.cursorPosition()
        self.aavso_observer_code_edit.setText(normalized)
        self.aavso_observer_code_edit.setCursorPosition(cursor_position)

    def set_aavso_observer_code_from_bav(self, value: object) -> bool:
        """Refresh the BAV prefill without replacing a manual Export override."""

        try:
            observer_code = validated_aavso_observer_code(value)
        except ValueError:
            return False
        current_code = self.aavso_observer_code_edit.text().strip().upper()
        previous_prefill = getattr(self, "_aavso_bav_prefill", "")
        use_new_prefill = not current_code or current_code == previous_prefill
        self._aavso_bav_prefill = observer_code
        if use_new_prefill:
            self.aavso_observer_code_edit.setText(observer_code)
        return use_new_prefill

    def export_aavso_report(self) -> None:
        """Export the current light curve as an AAVSO Extended Format report."""

        self.set_export_process_status("creating")
        current = self.current_export_lightcurve()
        if current is None:
            self.set_export_process_status("failed")
            QMessageBox.warning(
                self,
                "AAVSO Export",
                "Create or open a result first.",
            )
            return
        try:
            observer_code, observer_code_source = self.resolved_aavso_observer_code()
        except ValueError as exc:
            self.set_export_process_status("failed")
            self.append_log(f"WARNING: AAVSO export observer code rejected: {exc}")
            QMessageBox.warning(self, "AAVSO Export", str(exc))
            return
        result_csv, target_name = current
        output_path = self.current_aavso_export_path(result_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        has_calibrated_check = False
        try:
            instrument = aavso_instrument_from_result_csv(result_csv)
            has_calibrated_check = result_csv_has_calibrated_check(result_csv)
            rows_written = write_aavso_extended_report(
                result_csv,
                output_path,
                target_name,
                observer_code,
                instrument,
            )
        except Exception as exc:
            self.set_export_process_status("failed")
            self.append_log(f"ERROR: AAVSO export failed: {exc}")
            QMessageBox.critical(self, "AAVSO Export", f"AAVSO export failed:\n{exc}")
            return
        if rows_written == 0:
            self.set_export_process_status("failed")
            self.append_log(f"WARNING: AAVSO export wrote no rows: {output_path}")
            QMessageBox.warning(
                self,
                "AAVSO Export",
                "No valid rows were exported.",
            )
            return
        check_note = ""
        if not has_calibrated_check:
            check_note = " KNAME/KMAG were written as na because no calibrated Check Star is available."
            self.append_log("WARNING: AAVSO export has no calibrated Check Star; KNAME/KMAG written as na.")
        self.export_status_label.setText(
            f"AAVSO report written: {output_path} ({rows_written} row(s)).{check_note}"
        )
        self.append_log(
            f"AAVSO report written: {output_path} ({rows_written} row(s)); "
            f"observer code from {observer_code_source}.{check_note}"
        )
        self.mark_export_status("AAVSO")
        QMessageBox.information(
            self,
            "AAVSO Export",
            "AAVSO report written.",
        )

    def plot_light_curve_csv(
        self,
        result_csv: Path,
        output_png: Path | None,
        target_name: str,
        source_label: str = "",
        *,
        allow_extremum: bool = True,
        include_invalid: bool = False,
    ) -> None:
        try:
            plot_data = load_light_curve_plot_data(
                result_csv,
                include_invalid=include_invalid,
            )
            axis, bin_status_text = draw_light_curve_figure(
                self.lightcurve_figure,
                plot_data,
                target_name,
                source_label,
                show_running_mean=self.show_running_mean_checkbox.isChecked(),
                include_check=True,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Plot Light Curve", str(exc))
            return

        self.lightcurve_jd_values = plot_data.jd_values
        self.lightcurve_mag_values = plot_data.mag_values
        self.lightcurve_mag_errors = plot_data.error_values
        self.lightcurve_selected_range = None
        self.lightcurve_fit_artists = []
        self.current_extremum_fit_result = None
        self.current_extremum_fit_csv = None
        self.set_extremum_fit_text("Min/Max fit: none")

        self.ensure_plot_dialog()
        self.lightcurve_axis = axis
        if allow_extremum:
            self.lightcurve_span_selector = SpanSelector(
                axis,
                self.on_lightcurve_span_selected,
                "horizontal",
                useblit=True,
                props={"facecolor": "#4c78a8", "alpha": 0.18},
                interactive=True,
            )
        else:
            self.lightcurve_span_selector = None
        self.lightcurve_canvas.draw()
        self.refresh_action_availability()

        if output_png is not None:
            self.lightcurve_figure.savefig(output_png, dpi=150)
            self.lightcurve_status_label.setText(
                f"Plotted {len(plot_data.jd_values)} point(s).{bin_status_text} Saved: {output_png}"
            )
            self.append_log(f"Light Curve plot written: {output_png} from {result_csv}")
        else:
            self.lightcurve_status_label.setText(
                f"Plotted {len(plot_data.jd_values)} point(s).{bin_status_text} Source: {result_csv}"
            )
            self.append_log(f"Light Curve loaded and plotted from: {result_csv}")

        output_dir = output_png.parent if output_png is not None else result_csv.parent
        run_log = self.write_run_log(output_dir)
        self.append_log(f"Run log updated: {run_log.name}")
        self.write_run_log(output_dir)

    def on_lightcurve_span_selected(self, xmin: float, xmax: float) -> None:
        if xmin == xmax:
            return
        self.lightcurve_selected_range = (min(xmin, xmax), max(xmin, xmax))
        self.refresh_action_availability()
        self.lightcurve_status_label.setText(
            f"Selected JD range: {self.lightcurve_selected_range[0]:.8f} - "
            f"{self.lightcurve_selected_range[1]:.8f}. "
            "Choose Fit Min/Max or Trim to Selection."
        )

    def trim_lightcurve_to_selection(self) -> None:
        """Permanently make the selected JD span the current result CSV."""

        current = self.current_export_lightcurve()
        if current is None:
            QMessageBox.warning(
                self,
                "Trim Light Curve",
                "Create or open a Light Curve result first.",
            )
            return
        if self.lightcurve_selected_range is None:
            QMessageBox.warning(
                self,
                "Trim Light Curve",
                "Drag across the plot to select the complete range to keep.",
            )
            return

        result_csv, target_name = current
        try:
            plan = build_lightcurve_trim_plan(
                result_csv,
                *self.lightcurve_selected_range,
            )
            export_files = local_derived_export_files(result_csv, target_name)
        except Exception as exc:
            self.append_log(f"WARNING: Light Curve trim could not be prepared: {exc}")
            QMessageBox.warning(self, "Trim Light Curve", str(exc))
            return

        removed_count = plan.original_row_count - len(plan.rows)
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("Trim Light Curve")
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setText("Make the selected range the complete light curve?")
        details = (
            f"Keep {len(plan.rows)} of {plan.original_row_count} CSV rows and remove "
            f"{removed_count}.\n\n"
            "This permanently rewrites the current result CSV and clears the current "
            "Min/Max fit. The instrumental photometry CSV is not changed."
        )
        if export_files:
            details += (
                f"\n\n{len(export_files)} existing local AAVSO/BAV export file(s) "
                "will be deleted and must be recreated."
            )
            confirmation.setDetailedText(
                "Local export files to delete:\n"
                + "\n".join(str(path) for path in export_files)
            )
        confirmation.setInformativeText(details)
        trim_button = confirmation.addButton("Trim", QMessageBox.ButtonRole.AcceptRole)
        confirmation.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        confirmation.setDefaultButton(trim_button)
        confirmation.exec()
        if confirmation.clickedButton() is not trim_button:
            self.append_log("Light Curve trim aborted by user.")
            return

        try:
            deleted_exports, failed_exports = apply_lightcurve_trim_plan(plan, export_files)
        except Exception as exc:
            self.append_log(f"ERROR: Light Curve trim failed: {type(exc).__name__}: {exc}")
            QMessageBox.critical(
                self,
                "Trim Light Curve",
                f"The Light Curve could not be trimmed.\n\n{exc}",
            )
            return

        self.clear_extremum_fit(redraw=False, reset_status=True)
        self.reset_export_status()
        export_status = f"Current export source was trimmed; recreate AAVSO/BAV exports: {result_csv}"
        if failed_exports:
            export_status += f"; {len(failed_exports)} old export file(s) could not be deleted"
        self.export_status_label.setText(export_status)
        output_png = result_csv.with_suffix(".png")
        self.plot_light_curve_csv(
            result_csv,
            output_png,
            target_name,
            self.loaded_lightcurve_source_label,
        )
        message = (
            f"Light Curve trimmed: kept {len(plan.rows)} of {plan.original_row_count} rows "
            f"in JD {plan.range_min_jd:.8f}-{plan.range_max_jd:.8f}; "
            f"deleted {len(deleted_exports)} local export file(s)."
        )
        self.lightcurve_status_label.setText(message)
        self.append_log(message)
        for path, exc in failed_exports:
            self.append_log(f"WARNING: Could not remove stale export {path}: {exc}")
        self.refresh_plugin_tab_views()
        try:
            self.write_run_log(result_csv.parent)
        except Exception as exc:
            self.append_log(f"WARNING: Run log update after Light Curve trim failed: {exc}")
        completion_message = (
            f"Light Curve trimmed.\n\nKept {len(plan.rows)} of "
            f"{plan.original_row_count} rows."
        )
        if failed_exports:
            completion_message += (
                "\n\nSome old AAVSO/BAV export files could not be deleted. "
                "Recreate them before use:\n"
                + "\n".join(str(path) for path, _ in failed_exports)
            )
            QMessageBox.warning(self, "Trim Light Curve", completion_message)
        else:
            QMessageBox.information(self, "Trim Light Curve", completion_message)

    def clear_extremum_fit(
        self,
        redraw: bool = True,
        reset_status: bool = True,
        clear_selection: bool = True,
    ) -> None:
        for artist in self.lightcurve_fit_artists:
            try:
                artist.remove()
            except ValueError:
                pass
        self.lightcurve_fit_artists = []
        self.current_extremum_fit_result = None
        self.current_extremum_fit_csv = None
        self.set_extremum_fit_text("Min/Max fit: none")
        if clear_selection:
            self.lightcurve_selected_range = None
            if self.lightcurve_span_selector is not None:
                try:
                    self.lightcurve_span_selector.clear()
                except AttributeError:
                    pass
        self.refresh_action_availability()
        if reset_status:
            self.set_fit_status("none")
        else:
            self.update_overall_status()
        if redraw:
            self.lightcurve_canvas.draw()

    def fit_selected_extremum(self) -> None:
        attempt_number = self.extremum_fit_attempt_counter + 1

        def format_quality_value(value: object) -> str:
            if isinstance(value, float):
                return f"{value:.8f}" if np.isfinite(value) else "nan"
            return str(value)

        def log_fit_quality(decision: str, reason: str, **quality: object) -> None:
            parts = [
                f"decision={decision}",
                f"reason={reason}",
                f"range={xmin:.8f}-{xmax:.8f}",
            ]
            parts.extend(f"{key}={format_quality_value(value)}" for key, value in quality.items())
            self.append_log(f"Min/Max fit #{attempt_number} quality: " + ", ".join(parts))

        def log_model_check(check: dict[str, object]) -> None:
            model = str(check.get("model", "unknown"))
            parts = [
                f"model={model}",
                f"decision={check.get('decision', '')}",
            ]
            for key, value in check.items():
                if key in {"model", "decision"}:
                    continue
                parts.append(f"{key}={format_quality_value(value)}")
            self.append_log(f"Min/Max fit #{attempt_number} model check: " + ", ".join(parts))

        def persist_fit_log() -> None:
            output_dir = self.lightcurve_output_directory()
            if output_dir is not None:
                self.write_run_log(output_dir)

        # Even an invalid new attempt must invalidate an older export result.
        self.clear_extremum_fit(
            redraw=self.lightcurve_canvas is not None, reset_status=False, clear_selection=False)
        if (
            self.lightcurve_axis is None
            or self.lightcurve_jd_values is None
            or self.lightcurve_mag_values is None
            or self.lightcurve_mag_errors is None
        ):
            self.set_fit_status("failed")
            QMessageBox.warning(self, "Fit Min/Max", "Plot a Light Curve first.")
            return
        if self.lightcurve_selected_range is None:
            self.set_fit_status("failed")
            QMessageBox.warning(
                self,
                "Fit Min/Max",
                "Select a JD range first.",
            )
            return

        self.extremum_fit_attempt_counter = attempt_number
        self.set_fit_status("fitting")
        xmin, xmax = self.lightcurve_selected_range
        selected_model_setting = str(self.extremum_model_combo.currentData())
        required_model_name = (
            None
            if selected_model_setting == EXTREMUM_MODEL_AUTOMATIC
            else selected_model_setting
        )
        model_label = next(
            (
                label
                for label, model_name in EXTREMUM_MODEL_OPTIONS
                if model_name == selected_model_setting
            ),
            selected_model_setting,
        )
        calculation = calculate_extremum_fit(
            self.lightcurve_jd_values,
            self.lightcurve_mag_values,
            self.lightcurve_mag_errors,
            xmin,
            xmax,
            required_model_name=required_model_name,
        )
        fit_mode_label = f"curve model={model_label}, exact user range"
        for model_check in calculation.model_checks:
            log_model_check(model_check)

        if not calculation.accepted:
            self.set_fit_status("failed")
            log_fit_quality("rejected", calculation.reason, **calculation.metrics)
            self.set_extremum_fit_text(
                f"Min/Max fit: rejected ({model_label})"
            )
            self.append_log(
                f"Min/Max fit #{attempt_number} rejected: {calculation.reason}, "
                f"mode={fit_mode_label}, range={xmin:.8f}-{xmax:.8f}"
            )
            persist_fit_log()
            QMessageBox.warning(self, "Fit Min/Max", calculation.message)
            return

        result = calculation.result
        if result is None or calculation.selected_x is None:
            self.set_fit_status("failed")
            QMessageBox.warning(self, "Fit Min/Max", "Fit failed.")
            return

        axis = self.lightcurve_axis
        xlim = axis.get_xlim()
        ylim = axis.get_ylim()
        self.clear_extremum_fit(redraw=False, reset_status=False, clear_selection=False)
        fit_x_plot = np.array(calculation.fit_plot_jd, dtype=np.float64)
        fit_y_plot = np.array(calculation.fit_plot_mag, dtype=np.float64)
        fit_line = axis.plot(fit_x_plot, fit_y_plot, color="#d55e00", linewidth=1.5)[0]
        marker = axis.plot(calculation.vertex_jd, calculation.vertex_mag, "s", color="#d55e00", markersize=5)[0]
        vline = axis.axvline(calculation.vertex_jd, color="#d55e00", linewidth=0.9, linestyle="--")
        self.lightcurve_fit_artists = [fit_line, marker, vline]
        axis.set_xlim(xlim)
        axis.set_ylim(ylim)
        warning_suffix = f" ({'; '.join(calculation.warnings)})" if calculation.warnings else ""
        inlier_count = int(result["point_count"])
        selected_count = int(result["selected_point_count"])
        self.current_extremum_fit_result = result
        current = self.current_export_lightcurve()
        self.current_extremum_fit_csv = current[0] if current is not None else None
        self.set_fit_status("selected")
        self.refresh_action_availability()
        log_fit_quality("accepted", "ok", **calculation.metrics)
        model_suffix = (
            f", model={calculation.model_name or result.get('fit_model', 'unknown')}"
            f", selected={model_label}"
        )
        fit_summary = (
            f"{calculation.extremum_type}: JD {calculation.vertex_jd:.8f}"
            + (f" +/- {calculation.vertex_jd_error:.8f}" if np.isfinite(calculation.vertex_jd_error) else "")
            + f", mag {calculation.vertex_mag:.4f}"
            + (f" +/- {calculation.vertex_mag_error:.4f}" if np.isfinite(calculation.vertex_mag_error) else "")
            + f", n={inlier_count}/{selected_count}, rms={float(result['rms']):.4f}"
            + model_suffix
            + warning_suffix
        )
        self.lightcurve_status_label.setText(fit_summary)
        self.set_extremum_fit_text(f"Min/Max fit: {fit_summary}")
        self.update_overall_status()
        self.append_log(
            f"Min/Max fit #{attempt_number} accepted: {calculation.extremum_type}, "
            f"JD={calculation.vertex_jd:.8f}, mag={calculation.vertex_mag:.4f}, "
            f"JD_err={calculation.vertex_jd_error:.8f}, mag_err={calculation.vertex_mag_error:.4f}, "
            f"n={inlier_count}/{selected_count}, rms={float(result['rms']):.4f}, "
            f"weighted_rms={float(result['weighted_rms']):.4f}, "
            f"model={calculation.model_name or result.get('fit_model', 'unknown')}, "
            f"mode={fit_mode_label}, "
            f"range={xmin:.8f}-{xmax:.8f}"
            + (f", warnings={'; '.join(calculation.warnings)}" if calculation.warnings else "")
        )
        persist_fit_log()
        self.lightcurve_canvas.draw()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.is_busy():
            QMessageBox.warning(
                self,
                "Close",
                f"An operation is still running:\n{self.busy_message or self.busy_state}",
            )
            event.ignore()
            return
        source_dir = self.current_source_directory()
        self.is_closing = True
        cleanup_directories = set(self.session_temp_directories)
        if source_dir is not None:
            cleanup_directories.add(temp_directory_for(source_dir))
        safe_directory = source_dir
        if safe_directory is None or not safe_directory.is_dir():
            safe_directory = next(
                (
                    tmp_dir.parent
                    for tmp_dir in cleanup_directories
                    if tmp_dir.parent.is_dir()
                ),
                None,
            )
        self.close_siril_display_context("close", safe_directory=safe_directory)
        for tmp_dir in sorted(cleanup_directories, key=lambda path: str(path)):
            try:
                remove_temp_directory_tree(tmp_dir, tmp_dir.parent)
                self.session_temp_directories.discard(tmp_dir)
                self.append_log(f"Temporary directory removed on close: {tmp_dir}")
            except Exception as exc:
                self.append_log(
                    "WARNING: Could not remove temporary directory on close "
                    f"for {tmp_dir}: {exc}"
                )
        if self.plot_dialog is not None:
            self.plot_dialog.close()
            self.plot_dialog = None
        if self.compstars_dialog is not None:
            self.compstars_dialog.close()
            self.compstars_dialog = None
        if self.cfa_stack_window is not None:
            self.cfa_stack_window.close()
            self.cfa_stack_window = None
        local_loop = self.local_event_loop
        if isinstance(local_loop, QEventLoop) and local_loop.isRunning():
            local_loop.quit()
        event.accept()

    def build_overview_html(self) -> str:
        colors = THEME_COLORS
        return f"""
        <html>
        <head>
        <style>
            body {{ color: {colors['text']}; }}
            h2 {{ color: {colors['title_text']}; margin-bottom: 4px; }}
            h3 {{ color: {colors['title_text']}; margin-top: 18px; margin-bottom: 4px; }}
            h4 {{ color: {colors['title_text']}; margin-top: 10px; margin-bottom: 4px; }}
            p {{ margin-top: 4px; margin-bottom: 7px; }}
            ul {{ margin-top: 4px; margin-bottom: 8px; }}
            li {{ margin-bottom: 3px; }}
            .tabs {{ background-color: {colors['panel']}; }}
            .tab {{
                background-color: {colors['panel']};
                border: 1px solid {colors['border']};
                padding: 7px;
            }}
            .note {{
                background-color: {colors['status']};
                border-left: 3px solid {colors['status_accent']};
                padding: 8px;
                margin-top: 7px;
                margin-bottom: 9px;
            }}
            code {{ color: {colors['title_text']}; }}
        </style>
        </head>
        <body>
            <h2>{WINDOW_TITLE}</h2>
            <h3>Purpose</h3>
            <p>SeePhot creates light curves for variable stars from prepared FITS image
            sequences. It finds targets in VSX, measures their brightness against comparison
            stars, and provides plots and reports for the results.</p>

            <h3>Workflow and navigation</h3>
            <h4>Using the interface</h4>
            <p>Work through the tabs from left to right. Within each tab, follow the controls
            from top to bottom.</p>
            <p>The lower pane shows progress, warnings, and rejection details.<br>
            <b>Reset</b> clears the session and temporary SeePhot files.<br>
            <b>Close</b> exits the window.</p>

            <h4>Workflow</h4>
            <ul>
                <li>Prepare original CFA images by extracting channels and, optionally,
                stacking frames in groups.</li>
                <li>Choose <b>Light Curve</b> or <b>Single Measurement</b> mode.</li>
                <li>Detect variable stars in the image using the AAVSO VSX catalog.</li>
                <li>Select one or more stars for photometry.</li>
                <li>Perform aperture photometry.</li>
                <li>Optionally fit a brightness maximum or minimum.</li>
                <li>Generate AAVSO report files.</li>
            </ul>

            <br>
            <table class="tabs" width="100%" cellspacing="4" cellpadding="5">
                <tr>
                    <td class="tab" align="center"><b>Prepare</b></td>
                    <td class="tab" align="center"><b>Variables</b></td>
                    <td class="tab" align="center"><b>Photometry</b></td>
                    <td class="tab" align="center"><b>Export</b></td>
                    <td class="tab" align="center"><b>Tools</b></td>
                </tr>
            </table>

            <h3>Prepare</h3>
            <ul>
                <li>Use <b>CFA Channels / Stack</b> to extract the L (luminance) or G
                (green) channel from the original Seestar CFA images.</li>
                <li>Optionally, stack the images in suitable groups. Use <b>Tools</b> &gt;
                <b>Stack Profiling</b> for a grouping recommendation.</li>
                <li>Set <b>Mode</b> to <b>Light Curve</b> or <b>Single Measurement</b>.</li>
                <li>Click <b>Browse...</b> and select the prepared FITS folder.</li>
                <li>Click <b>Run</b>.</li>
            </ul>
            <p>SeePhot registers and plate-solves the sequence, selects a reference frame,
            and queries VSX for variables in the field.</p>

            <h3>Variables</h3>
            <ul>
                <li>Filter the VSX list by name, type, or maximum magnitude if required.</li>
                <li>For variable types, use one of the two Seestar presets or enter your own
                selection.</li>
                <li>Choose a target by double-clicking its row.</li>
                <li>Alternatively, select the row and click <b>Select Target</b>.</li>
                <li>Click <b>Open VSX</b> to view the selected target's catalog page.</li>
            </ul>

            <h3>Photometry</h3>
            <p><b>Light Curve mode</b></p>
            <ul>
                <li>Select <b>Light Curve</b> in <b>Mode</b>.</li>
                <li>Click <b>Create Light Curve</b>.</li>
                <li>Comparison stars and an independent check star are selected automatically.</li>
                <li>Click <b>Show Comparison Stars</b> to inspect the selected ensemble.</li>
            </ul>
            <p><b>Single Measurement mode</b></p>
            <ul>
                <li>Select <b>Single Measurement</b> in <b>Mode</b>.</li>
                <li>Run the measurement for the selected target. A field zero point is determined
                automatically; comparison stars are not used.</li>
            </ul>
            <p>Measurements can be rejected for:</p>
            <ul>
                <li>saturation, excessive high pixels, or non-positive flux;</li>
                <li>SNR below {MIN_PHOTOMETRY_SNR:g} or magnitude error above
                {MAX_INSTRUMENTAL_MAG_ERROR:g} mag;</li>
                <li>a large centroid offset;</li>
                <li>a completely modeled Gaia neighbor blend in a single measurement with at least
                {TARGET_BLEND_ERROR_LIMIT_MAG:.2f} mag, or in a series with at least
                {SERIES_TARGET_BLEND_ERROR_LIMIT_MAG:.2f} mag, expected target impact;</li>
                <li>background contamination of a comparison or check star with at least
                {REFERENCE_ANNULUS_IMPACT_INVALID_MAG:.2f} mag estimated impact;</li>
                <li>target background/annulus contamination with at least
                {TARGET_ANNULUS_IMPACT_INVALID_MAG:.2f} mag estimated impact;</li>
                <li>a large per-series mismatch between the already measured target
                background and the simultaneous comparison-star backgrounds;</li>
                <li>inconsistent comparison-star zero points.</li>
            </ul>
            <p>Warnings remain usable and stay visible in the CSV and log. In particular,
            target background/annulus contamination is a warning from
            {TARGET_ANNULUS_IMPACT_WARNING_MAG:.2f} mag and invalid from
            {TARGET_ANNULUS_IMPACT_INVALID_MAG:.2f} mag.
            Missing or ambiguous Gaia evidence is also a warning; the aperture measurement
            still runs. In a series, a completely modeled Gaia target blend is also a
            warning from {SERIES_TARGET_BLEND_WARNING_LIMIT_MAG:.2f} mag and invalid from
            {SERIES_TARGET_BLEND_ERROR_LIMIT_MAG:.2f} mag; its flux is not corrected.</p>
            <div class="note">
                The result contains only valid calibrated target points.<br>
                Results: <code>../{RESULTS_DIRECTORY_NAME}/&lt;FITS-folder-name&gt;</code><br>
                Files: <code>&lt;target&gt;_result_curve.csv</code> and
                <code>&lt;target&gt;_result_curve.png</code><br>
                Details: <code>{DIAGNOSTICS_DIRECTORY_NAME}</code>
            </div>

            <p><b>Open and plot a result</b></p>
            <ul>
                <li>Select <b>Light Curve</b> or <b>Single Measurement</b> in <b>Mode</b>
                before opening results. The CSV picker and <b>Results Folder</b> browser
                show results for the selected mode only.</li>
                <li><b>Light Curve CSV</b> opens an existing result for the selected mode.</li>
                <li><b>Results Folder</b> opens the result browser for the selected mode.</li>
                <li><b>VSX</b> opens the current target's catalog page.</li>
                <li><b>Plot Light Curve</b> opens the graph.</li>
                <li><b>Running mean</b> adds an overlay without changing the CSV.</li>
                <li>To remove noisy data at the beginning or end, drag the complete
                range to keep and click <b>Trim to Selection</b>. This permanently
                rewrites the current result CSV.</li>
            </ul>

            <div class="note">
                Trimming clears the current Min/Max fit and deletes existing local
                AAVSO/BAV export files after confirmation. Recreate those exports from
                the trimmed result. The instrumental photometry CSV is unchanged.
            </div>

            <p><b>Fit a minimum or maximum</b></p>
            <ol>
                <li>Drag a JD range around one visible extremum.</li>
                <li>Include both flanks and at least {EXTREMUM_MINIMUM_FIT_POINTS} points.</li>
                <li>Keep the recommended robust cubic spline, or deliberately choose another curve model.</li>
                <li>Click <b>Fit Min/Max</b>.</li>
            </ol>
            <p>The robust cubic spline chooses its smoothing automatically. A manually
            selected model is never silently replaced by another one.</p>
            <p>A fit is rejected if:</p>
            <ul>
                <li>the range is too small or unbalanced;</li>
                <li>the curve is nearly linear;</li>
                <li>the extremum lies outside the selection or measured magnitude range;</li>
                <li>the selected range contains no extremum or multiple extrema;</li>
                <li>the prominence is too weak compared with the scatter.</li>
            </ul>
            <p>An accepted fit is marked in the plot and recorded in the run log.
            <b>Clear Fit</b> removes the annotation.</p>

            <h3>Export</h3>
            <p>Choose the mode and result to export. SeePhot creates the export files
            that match the selected mode and result.</p>
            <ol>
                <li>Enter your AAVSO observer code.</li>
                <li>Check the displayed telescope.</li>
                <li>Click <b>Export AAVSO Report</b>.</li>
            </ol>
            <p>The telescope name is read from the Seestar FITS header and should normally be
            correct. If necessary, correct the FITS <code>TELESCOP</code> value before creating
            the Light Curve.</p>
            <ul>
                <li>Only valid calibrated rows are exported.</li>
                <li><b>Open Folder</b> shows the report file.</li>
                <li><b>AAVSO apps</b> opens the web tools, where you can upload the report.</li>
            </ul>

        </body>
        </html>
        """

    def build_help_html(self) -> str:
        """Return concise help for the tab that is currently visible."""

        tab_index = self.tabs.currentIndex()
        tab_name = self.tabs.tabText(tab_index) if tab_index >= 0 else "SeePhot"
        help_by_tab = {
            "Prepare": (
                "<h3>Process single directory</h3>"
                "<ul><li><b>CFA Channels / Stack</b> prepares original Seestar CFA frames "
                "from one folder.</li>"
                "<li>Choose channels and stack groups; see progress in the log.</li></ul>"
                "<h3>Process multiple directories</h3>"
                "<ul><li><b>Batch Mode</b> prepares several CFA folders with shared settings.</li>"
                "<li>See the status and log for the batch.</li></ul>"
                "<h3>Detect Variables</h3>"
                "<ul><li>Choose <b>Mode</b>, then enter an input path or use <b>Browse...</b>.</li>"
                "<li>Check <b>Status</b> and click <b>Run</b> to prepare the input "
                "and query VSX.</li></ul>"
            ),
            "Variables": (
                "<h3>Filter the VSX list</h3>"
                "<ul><li><b>Name</b> finds matching stars; <b>Type</b> limits variable "
                "types. The preset menu fills the type field.</li>"
                "<li><b>Mag &le;</b> sets the maximum catalog magnitude. Press Enter "
                "in a field to update the list.</li></ul>"
                "<h3>Select a target</h3>"
                "<ul><li>Double-click a row or click <b>Select Target</b> to use that star.</li>"
                "<li><b>Open VSX</b> shows its catalog entry.</li></ul>"
            ),
            "Photometry": (
                "<h3>Single target</h3>"
                "<ul><li>Click <b>Create Light Curve</b> for the selected target.</li>"
                "<li>Use <b>Show Comparison Stars</b> to inspect the selected stars.</li></ul>"
                "<h3>Multiple targets</h3>"
                "<ul><li>Click <b>Select targets</b>, choose VSX targets, and run the batch.</li>"
                "<li>See each target's status and result CSV; <b>Show</b> opens a result.</li></ul>"
                "<h3>Result</h3>"
                "<ul><li>For a single measurement, see its status and target/check values.</li></ul>"
                "<h3>Measurement quality</h3>"
                "<ul><li>A fully modeled Gaia target-neighbor blend is invalid from "
                f"{TARGET_BLEND_ERROR_LIMIT_MAG:.2f} mag expected impact for a single "
                f"measurement, or {SERIES_TARGET_BLEND_ERROR_LIMIT_MAG:.2f} mag in a series. "
                "Missing or ambiguous Gaia evidence is a warning.</li>"
                "<li>Comparison and check-star annulus contamination is invalid from "
                f"{REFERENCE_ANNULUS_IMPACT_INVALID_MAG:.2f} mag estimated impact.</li>"
                "<li>Target annulus contamination warns from "
                f"{TARGET_ANNULUS_IMPACT_WARNING_MAG:.2f} mag and is invalid from "
                f"{TARGET_ANNULUS_IMPACT_INVALID_MAG:.2f} mag estimated impact.</li></ul>"
                "<h3>Photometric Binning</h3>"
                "<ul><li>Combine measured fluxes from eligible original images into a separate photometry result.</li>"
                "<li>Use <b>Use Raw Curve</b> to return to the source result.</li></ul>"
                "<h3>Plot / Fit</h3>"
                "<ul><li>Plot the curve and optionally show its running mean.</li>"
                "<li>Select a JD range to fit a minimum/maximum or permanently trim the CSV.</li></ul>"
                "<h3>Open</h3>"
                "<ul><li><b>Light Curve CSV</b> opens one result directly.</li>"
                "<li><b>Results Folder</b> lists the results in the selected folder. "
                "Select a result and click <b>Load</b>, or double-click its row, to open it.</li>"
                "<li><b>VSX</b> opens the current target's catalog page.</li></ul>"
            ),
            "Export": (
                "<h3>Config</h3>"
                "<ul><li>Enter your AAVSO observer code.</li>"
                "<li>Check the telescope shown for the current result.</li></ul>"
                "<h3>Export</h3>"
                "<ul><li>Click <b>Export AAVSO Report</b> for the selected or opened "
                "Light Curve result.</li>"
                "<li>Use <b>Open Folder</b> to see the report. <b>AAVSO apps</b> "
                "opens the web tools and copies the export folder path to the clipboard. "
                "Paste it into the upload file chooser, then select the report file.</li></ul>"
            ),
            "QC": (
                "Use this developer-oriented tab to run or compare focused quality checks for "
                "the current result. Read the log before saving a new reference result."
            ),
            "Archive": (
                "Choose a results folder, review its worklist and status, then archive only the "
                "intended results or images. Add or inspect comments before archiving if needed."
            ),
            "Ext_Tools": (
                "Open a FITS file to inspect the external-telescope header tools available on "
                "this tab. Any findings and errors are reported in the log."
            ),
            "Tools": (
                "<h3>Photometric linearity</h3>"
                "<ul><li>Choose a solved FITS image; adjust the BP-RP range if needed.</li>"
                "<li>See the Gaia V comparison and residual plots.</li></ul>"
                "<h3>Stack Profiling</h3>"
                "<ul><li>Choose an original FITS folder and click <b>Analyze Timing</b>.</li>"
                "<li>See frame timing, grouping options, and a recommendation when available.</li></ul>"
            ),
        }
        body = help_by_tab.get(
            tab_name,
            "Use the controls on this tab from top to bottom. Progress, warnings, and errors "
            "appear in the log below.",
        )
        tab = self.tabs.currentWidget()
        if tab is not None:
            body = tab.property("context_help_body") or body
        footer = (
            tab.property("context_help_footer") if tab is not None else None
        ) or "For the complete workflow, click <b>Overview</b>."
        colors = THEME_COLORS
        return f"""
        <html><head><style>
            body {{ color: {colors['text']}; }}
            h2 {{ color: {colors['title_text']}; margin-bottom: 8px; }}
            h3 {{ color: {colors['title_text']}; margin-top: 10px; margin-bottom: 4px; }}
            ul {{ margin-top: 4px; margin-bottom: 8px; }}
            li {{ margin-bottom: 3px; }}
            .note {{ background-color: {colors['status']}; border-left: 3px solid
                {colors['status_accent']}; padding: 9px; }}
        </style></head><body>
            <h2>{tab_name}</h2>
            <div class="note">{body}</div>
            <p>{footer}</p>
        </body></html>
        """

    def show_overview(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{APP_DISPLAY_NAME} Overview")
        dialog.resize(780, 620)

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
        tab_index = self.tabs.currentIndex()
        tab_name = self.tabs.tabText(tab_index) if tab_index >= 0 else APP_DISPLAY_NAME
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{tab_name} Help")
        dialog.resize(620, 300)

        layout = QVBoxLayout(dialog)
        text_view = QTextEdit()
        text_view.setReadOnly(True)
        text_view.setHtml(self.build_help_html())
        layout.addWidget(text_view)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()


def install_qt_exception_hook() -> None:
    """Keep uncaught Qt-slot exceptions from aborting the embedded Python process."""

    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=sys.stderr)

    sys.excepthook = handle_exception


def activate_existing_main_window(app: QApplication) -> bool:
    """Bring an already open SeePhot window to front if one exists."""

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
    """Acquire the per-user SeePhot instance lock, if available."""

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
    install_qt_exception_hook()
    app = QApplication.instance()
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
            APP_DISPLAY_NAME,
            "SeePhot is already running.",
        )
        return

    window = LightCurveWindow()
    window._single_instance_lock = single_instance_lock
    loop = QEventLoop()
    window.local_event_loop = loop
    window.destroyed.connect(single_instance_lock.unlock)
    window.destroyed.connect(loop.quit)
    window.show()
    loop.exec()


def main() -> None:
    run_app()


if __name__ == "__main__":
    main()
