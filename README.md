# SeePhot

Private prerelease repository for SeePhot, a Siril Python application for
Seestar variable-star photometry.

> **Development status:** internal prerelease (`0.4-pre`). Interfaces, file
> formats, photometry rules, and installation steps may still change.

## Planned Prerelease Bundle

The internal prerelease contains the main application and the small set of
modules needed for the current manual workflow:

- `SeePhot_Main.py` — application entry point.
- `SeePhot_CFA.py` — optional CFA channel extraction and stacking companion.
- `sp_mod_results.py` — result discovery and metadata helpers.
- `sp_mod_bav.py` — optional BAV report support.

The following development or machine-specific modules are intentionally not
part of this prerelease:

- `sp_mod_auto.py`
- `sp_mod_batch.py`
- `sp_mod_qc.py`
- `sp_mod_linearity.py`
- `sp_mod_archive.py`

## Requirements

- Siril 1.3.0 or newer with Python scripting support.
- `sirilpy` 1.0.13 or newer.
- Python packages used by the main workflow: `PyQt6`, `astropy`, `numpy`, and
  `matplotlib`.
- Some optional operations may additionally use `scipy`, `astroquery`, or
  `reportlab`.

SeePhot is intended to run inside Siril's Python environment, not as a
standalone system-Python application.

## Installation

1. Copy all included `.py` files into the same Siril Python script directory.
2. In Siril, select that directory under the Python scripts settings.
3. Start `SeePhot_Main.py` from Siril's Scripts menu.

Only `SeePhot_Main.py` and `SeePhot_CFA.py` are executable applications.
Files beginning with `sp_mod_` are support modules and should not be started
directly.

## Repository Status

This repository is private and intended for internal prerelease testing. It is
not yet the public distribution channel for SeePhot.

The standalone CFA/stacking companion is maintained separately at:

https://github.com/Aquarius58/siril-seestar-stack

## License

SeePhot and the included support modules are licensed under
GPL-3.0-or-later. See `LICENSE` and the SPDX identifier in each source file.

`SeePhot_CFA.py` is maintained separately under the same license in its
upstream repository.
