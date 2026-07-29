# Changelog

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
