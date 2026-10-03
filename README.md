# SeePhot

SeePhot is a Siril Python application for Seestar variable-star photometry,
light-curve analysis, and related CFA/stack workflows.

## Current Main Release

- SeePhot Main: `0.8.70`
- SeePhot CFA: `0.5.0`

The Main release is a manually tested snapshot. The Main and CFA applications
keep independent internal version numbers; the GitHub release version follows
SeePhot Main.

## Release Bundle

Keep all ten Python files in the same Siril script directory:

- `SeePhot_Main.py` — main photometry and analysis application.
- `SeePhot_CFA.py` — CFA channel extraction and stacking companion.
- `sp_mod_analyze.py` — standalone analysis and photometric linearity tools.
- `sp_mod_auto.py` — multi-folder CFA/stack batch workflow.
- `sp_mod_batch.py` — multi-target photometry batch workflow.
- `sp_mod_bav.py` — BAV report and export support.
- `sp_mod_binning.py` — scientific flux-based light-curve binning.
- `sp_mod_linearity.py` — linearity and dynamic-range diagnostics.
- `sp_mod_profiling.py` — stack timing analysis and recommendations.
- `sp_mod_results.py` — result discovery, metadata, and output-path helpers.


## Main Features

- Series photometry and Single Measurement workflows for Seestar FITS data.
- Variable-star discovery, comparison-star handling, and calibrated result
  generation.
- Light-curve plotting, scientific binning, and robust extremum fitting.
- Gaia-based target and annulus contamination assessment with explicit
  rejection and diagnostic outcomes.
- AAVSO and BAV export workflows, including multiple extrema and optional
  period information.
- Multi-target photometry batches and multi-folder CFA/stack batches.
- Additional Single Measurement targets from SIMBAD, Gaia DR3, or entered
  coordinates, with clearly marked informational results when needed.
- CFA channel extraction from a single frame without stacking.
- Pause-aware and dynamic stacking modes with stack profiling and planning
  recommendations.
- Photometric linearity analysis and support for Seestar S50 and S50 Pro
  metadata.

## Requirements

- Siril 1.3.0 or newer with Python scripting support.
- `sirilpy` 1.0.13 or newer.
- Python packages used by the core workflow: `PyQt6`, `astropy`, `numpy`, and
  `matplotlib`.
- Some workflows additionally use `scipy`, `astroquery`, or `reportlab`.

SeePhot is intended to run inside Siril's Python environment, not as a
standalone system-Python application.

## Installation

1. Download the source archive from the GitHub release page.
2. Copy the ten `.py` files listed under **Release Bundle** into the same Siril
   Python script directory.
3. In Siril, select that directory under the Python scripts settings.
4. Start `SeePhot_Main.py` from Siril's Scripts menu.

`SeePhot_Main.py` and `SeePhot_CFA.py` are the executable applications. Files
beginning with `sp_mod_` are support modules and should not be started
directly.

## CFA Release

The standalone public CFA/stack application is maintained separately at:

https://github.com/Aquarius58/siril-seestar-stack

The CFA file included here is the version tested as part of this Main release
snapshot and can therefore differ from a later standalone CFA release.

## Repository Status

This repository contains tagged SeePhot Main releases. Repository visibility
is managed independently on GitHub and does not change the release contents.

## License

SeePhot and the included support modules are licensed under
GPL-3.0-or-later. See `LICENSE` and the SPDX identifier in each source file.
