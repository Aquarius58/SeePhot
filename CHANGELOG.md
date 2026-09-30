# Changelog

## 0.8.60 - 2026-09-30

- Added Single Measurement targets from SIMBAD, Gaia DR3, or manually entered
  coordinates, with source provenance and safeguards for references and
  scientific exports.
- Added an informational field zero-point result when reference signal-to-noise
  is below the export threshold.
- Improved multiple-target batch selection and reset behavior.
- Enabled CFA channel extraction from a single frame without creating a stack.

## 0.8.40 - 2026-09-26

- Improved result browsing with VSX type display, numeric sorting, and clearer
  mode-specific result navigation.
- Made light-curve trimming safer and report when stale export files could not
  be removed.
- Added provenance for scatter-rejected frames and refined Gaia isolation and
  photometric analysis workflows.
- Improved CFA and BAV workflow handling.

## 0.8.20 - 2026-09-20

- Expanded the Main release bundle to ten scripts, adding analysis tools,
  automated CFA/stack processing, target batch processing, scientific
  light-curve binning, linearity diagnostics, and stack profiling.
- Added pause-aware and dynamic stacking workflows with improved CFA stack
  planning, recommendations, and logging.
- Added Seestar S50 Pro support and improved FITS metadata handling for newer
  Seestar firmware.
- Reworked result discovery, generated result variants, binning provenance,
  and Single Measurement workflows.
- Improved target and annulus contamination handling, measurement validation,
  and robust extremum fitting.
- Expanded the BAV workflow with optional periods, multiple extrema, comments,
  improved filenames, and more reliable result handling.
- Refined application state, contextual help, batch workflows, and
  user-facing error reporting.

## 0.4.5-pre - 2026-08-05

- Added post-measurement Gaia DR3 target-blend assessment for light curves
  and single measurements, with explicit warning and rejection outcomes.
- Applied target and annulus contamination decisions consistently, excluded
  invalid measurements from scientific plots, extremum fitting, and AAVSO
  export, and clearly marked rejected single measurements as diagnostic.
- Unified AAVSO observer-code handling across the Export and BAV tabs,
  including uppercase normalization, validation, and synchronized prefilling.
- Derived telescope and instrument information from the current result data
  and refreshed the read-only BAV display when the selected result changes.
- Improved BAV reports with a visible VSX object-page link and a fallback URL
  for older results that only contain the VSX object identifier.

## 0.4-pre - 2026-07-29

- Simplified the preparation workflow into clearer FITS preparation and
  variable-detection steps.
- Copied the current AAVSO or BAV output path to the clipboard when opening
  the corresponding website, and stopped creating empty BAV output folders.
- Separated extremum-support rejection from model fitting and added
  asymptotic-parabola, parabolic-spline, and smoothing-spline candidates.
- Selected extremum models by deterministic bootstrap timing accuracy while
  keeping residual information as a diagnostic.
- Improved extremum validation and user-facing rejection reasons for weak,
  edge-located, or insufficiently supported extrema.
- Improved the standard Photometry `Results Folder` browser with faster
  recursive scans, transient-error retries, progress and cancellation, and
  explicit warnings for unreadable paths.
- Prevented invalid piecewise-knot trials from emitting SciPy numerical
  differentiation warnings.

## 0.3-pre - 2026-07-27

- Created the private prerelease repository structure.
- Defined the initial minimal bundle for SeePhot `0.3-pre`.
