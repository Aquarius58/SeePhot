"""Scientific flux binning for SeePhot light-curve measurements.

This module deliberately has no Qt or Siril dependency so that the numerical
contract can be tested from small CSV fixtures.  It accepts only the current
instrumental CSV schema written by SeePhot.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Iterable


MIN_INPUTS_PER_BIN = 2
GAP_FACTOR = 3.0
FLUX_RULE_ID = "inverse-variance-calibrated-relative-flux-v1"


@dataclass(frozen=True)
class CalibratedFrame:
    frame_index: int
    filename: str
    jd: float
    exptime: float
    flux: float
    flux_error: float
    image_source: str
    aavso_filter: str
    comparison_ids: tuple[str, ...] = ()
    check_object_id: str = ""
    check_catalog_source: str = ""
    check_catalog_id: str = ""
    check_flux: float | None = None
    check_flux_error: float | None = None
    check_catalog_mag: float | None = None
    check_catalog_mag_error: float | None = None
    check_catalog_nobs: str = ""
    check_catalog_b_minus_v: float | None = None
    check_catalog_g_minus_r: float | None = None


@dataclass(frozen=True)
class BinnedFrame:
    frames: tuple[CalibratedFrame, ...]
    jd: float
    flux: float
    flux_error: float
    magnitude: float
    magnitude_error: float
    start_jd: float
    end_jd: float
    exposure_seconds: float
    check_input_count: int = 0
    check_object_id: str = ""
    check_catalog_source: str = ""
    check_catalog_id: str = ""
    check_flux: float | None = None
    check_flux_error: float | None = None
    check_magnitude: float | None = None
    check_magnitude_error: float | None = None
    check_catalog_mag: float | None = None
    check_catalog_mag_error: float | None = None
    check_catalog_nobs: str = ""
    check_catalog_b_minus_v: float | None = None
    check_catalog_g_minus_r: float | None = None


def _finite_positive(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _finite(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def source_fingerprint(rows: Iterable[dict[str, str]]) -> str:
    """Fingerprint all instrumental rows, in their recorded frame order."""

    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: (item.get("frame_index", ""), item.get("role", ""))):
        digest.update("\x1f".join(str(row.get(key, "")) for key in (
            "frame_index", "filename", "role", "object_id", "jd", "exptime",
            "net_flux", "flux_error", "catalog_mag", "inst_mag", "valid", "quality_status",
        )).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def calibrated_frames_from_instrumental_rows(
    rows: Iterable[dict[str, str]], *, min_valid_comps: int = 3,
) -> tuple[list[CalibratedFrame], int]:
    """Recalibrate eligible target fluxes from the complete instrumental CSV.

    A target's low S/N or high magnitude-error flag does not exclude it here;
    saturation, non-positive/unknown flux and all other invalid target flags
    do.  Comparison stars always need to be explicitly valid.  This preserves
    the existing hard QC rules while applying the target precision decision to
    the finished bin, where it belongs.
    """

    by_frame: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_frame.setdefault(str(row.get("frame_index", "")), []).append(row)
    output: list[CalibratedFrame] = []
    excluded = 0
    for frame_rows in by_frame.values():
        targets = [row for row in frame_rows if row.get("role", "").strip() == "target"]
        comps = [row for row in frame_rows if row.get("role", "").strip() == "comparison"]
        checks = [row for row in frame_rows if row.get("role", "").strip() == "check"]
        if len(targets) != 1:
            excluded += 1
            continue
        target = targets[0]
        target_flag = target.get("quality_flag", "").strip().upper()
        target_flux = _finite_positive(target.get("net_flux"))
        target_error = _finite_positive(target.get("flux_error"))
        # LOW_SNR and HIGH_MAG_ERROR are the two precision decisions deferred
        # to the final bin.  All other INVALID states remain excluded.
        target_status = target.get("quality_status", "").strip().upper()
        deferred_only = target_flag in {"LOW_SNR", "HIGH_MAG_ERROR"}
        if target_status == "INVALID" and not deferred_only:
            excluded += 1
            continue
        if target_flux is None or target_error is None:
            excluded += 1
            continue
        zero_points: list[float] = []
        comparison_ids: list[str] = []
        for comp in comps:
            if comp.get("valid", "").strip() not in {"1", "true", "True"}:
                continue
            catalog_mag = _finite(comp.get("catalog_mag"))
            inst_mag = _finite(comp.get("inst_mag"))
            if catalog_mag is not None and inst_mag is not None:
                zero_points.append(catalog_mag - inst_mag)
                comparison_ids.append(
                    comp.get("catalog_id", "").strip() or comp.get("object_id", "").strip()
                )
        if len(zero_points) < min_valid_comps:
            excluded += 1
            continue
        jd = _finite(target.get("jd"))
        exptime = _finite_positive(target.get("exptime"))
        if jd is None or exptime is None:
            excluded += 1
            continue
        zero_point = sorted(zero_points)[len(zero_points) // 2]
        scale = 10.0 ** (-0.4 * zero_point)
        mean_zero_point = sum(zero_points) / len(zero_points)
        zp_variance = sum((value - mean_zero_point) ** 2 for value in zero_points) / (len(zero_points) - 1)
        zp_error = math.sqrt(zp_variance) / math.sqrt(len(zero_points))
        # Instrumental magnitudes are defined from flux per second.  The
        # binned relative flux must use that same scale, otherwise different
        # exposure times would shift the derived magnitude.
        target_rate = target_flux / exptime
        target_rate_error = target_error / exptime
        scaled_flux = target_rate * scale
        scaled_error = math.sqrt(
            (target_rate_error * scale) ** 2
            + (scaled_flux * 0.4 * math.log(10.0) * zp_error) ** 2
        )
        check = checks[0] if len(checks) == 1 else None
        check_flux = None
        check_flux_error = None
        if check is not None and check.get("valid", "").strip() in {"1", "true", "True"}:
            raw_check_flux = _finite_positive(check.get("net_flux"))
            raw_check_error = _finite_positive(check.get("flux_error"))
            if raw_check_flux is not None and raw_check_error is not None:
                check_rate = raw_check_flux / exptime
                check_rate_error = raw_check_error / exptime
                check_flux = check_rate * scale
                check_flux_error = math.sqrt(
                    (check_rate_error * scale) ** 2
                    + (check_flux * 0.4 * math.log(10.0) * zp_error) ** 2
                )
        output.append(CalibratedFrame(
            frame_index=int(float(target.get("frame_index", "0") or 0)),
            filename=target.get("filename", ""), jd=jd, exptime=exptime,
            flux=scaled_flux, flux_error=scaled_error,
            image_source=target.get("image_source", "").strip(),
            aavso_filter=target.get("aavso_filter", "").strip(),
            comparison_ids=tuple(sorted(comparison_ids)),
            check_object_id="" if check is None else check.get("object_id", "").strip(),
            check_catalog_source="" if check is None else check.get("catalog_source", "").strip(),
            check_catalog_id="" if check is None else check.get("catalog_id", "").strip(),
            check_flux=check_flux,
            check_flux_error=check_flux_error,
            check_catalog_mag=None if check is None else _finite(check.get("catalog_mag")),
            check_catalog_mag_error=(
                None if check is None else _finite(check.get("catalog_mag_error"))
            ),
            check_catalog_nobs="" if check is None else check.get("catalog_nobs", "").strip(),
            check_catalog_b_minus_v=(
                None if check is None else _finite(check.get("catalog_b_minus_v"))
            ),
            check_catalog_g_minus_r=(
                None if check is None else _finite(check.get("catalog_g_minus_r"))
            ),
        ))
    # A bin must be tied to one reproducible comparison ensemble. Retain the
    # most frequent complete valid ensemble and exclude frames with another
    # subset rather than silently mixing their zero points.
    ensemble_counts: dict[tuple[str, ...], int] = {}
    for frame in output:
        ensemble_counts[frame.comparison_ids] = ensemble_counts.get(frame.comparison_ids, 0) + 1
    if ensemble_counts:
        canonical_ensemble = min(
            ensemble_counts,
            key=lambda ids: (-ensemble_counts[ids], ids),
        )
        consistent_output = [frame for frame in output if frame.comparison_ids == canonical_ensemble]
        excluded += len(output) - len(consistent_output)
        output = consistent_output
    output.sort(key=lambda item: item.jd)
    return output, excluded


def median_cadence_days(frames: list[CalibratedFrame]) -> float | None:
    deltas = [b.jd - a.jd for a, b in zip(frames, frames[1:]) if b.jd > a.jd]
    if not deltas:
        return None
    deltas.sort()
    return deltas[len(deltas) // 2]


def plan_bins(frames: list[CalibratedFrame], mode: str, value: int) -> tuple[list[list[CalibratedFrame]], int]:
    """Plan count or anchored-time bins, never crossing a 3x cadence gap.

    Time bins are anchored at each continuous segment's first midpoint.  A
    trailing bin with fewer than two images is deliberately omitted and
    reported as one skipped bin.
    """

    if mode not in {"count", "seconds"}:
        raise ValueError("Binning mode must be 'count' or 'seconds'.")
    if value < MIN_INPUTS_PER_BIN:
        raise ValueError("A bin needs at least two input images.")
    if not frames:
        return [], 0
    cadence = median_cadence_days(frames)
    gap_limit = None if cadence is None else GAP_FACTOR * cadence
    segments: list[list[CalibratedFrame]] = [[]]
    for frame in frames:
        if segments[-1] and gap_limit is not None and frame.jd - segments[-1][-1].jd > gap_limit:
            segments.append([])
        segments[-1].append(frame)
    groups: list[list[CalibratedFrame]] = []
    skipped = 0
    for segment in segments:
        if mode == "count":
            candidates = [segment[index:index + value] for index in range(0, len(segment), value)]
        else:
            width = value / 86400.0
            start = segment[0].jd
            buckets: dict[int, list[CalibratedFrame]] = {}
            for frame in segment:
                buckets.setdefault(int(math.floor((frame.jd - start) / width)), []).append(frame)
            candidates = [buckets[key] for key in sorted(buckets)]
        for candidate in candidates:
            if len(candidate) >= MIN_INPUTS_PER_BIN:
                groups.append(candidate)
            elif candidate:
                skipped += 1
    return groups, skipped


def combine_flux_bin(frames: list[CalibratedFrame]) -> BinnedFrame:
    """Inverse-variance combine a planned bin in calibrated linear flux."""

    if len(frames) < MIN_INPUTS_PER_BIN:
        raise ValueError("A bin needs at least two input images.")
    weights = [1.0 / (frame.flux_error * frame.flux_error) for frame in frames]
    total_weight = sum(weights)
    flux = sum(weight * frame.flux for weight, frame in zip(weights, frames)) / total_weight
    flux_error = math.sqrt(1.0 / total_weight)
    jd = sum(weight * frame.jd for weight, frame in zip(weights, frames)) / total_weight
    magnitude = -2.5 * math.log10(flux)
    magnitude_error = 2.5 / math.log(10.0) * flux_error / flux
    half_exposures = [frame.exptime / 2.0 / 86400.0 for frame in frames]
    check_candidates = [
        frame
        for frame in frames
        if frame.check_flux is not None
        and frame.check_flux_error is not None
        and frame.check_flux > 0
        and frame.check_flux_error > 0
    ]
    check_identity_counts: dict[tuple[str, str], int] = {}
    for frame in check_candidates:
        identity = (
            frame.check_catalog_source,
            frame.check_catalog_id or frame.check_object_id,
        )
        check_identity_counts[identity] = check_identity_counts.get(identity, 0) + 1
    canonical_check_identity = (
        min(check_identity_counts, key=lambda key: (-check_identity_counts[key], key))
        if check_identity_counts
        else None
    )
    check_frames = [
        frame
        for frame in check_candidates
        if (frame.check_catalog_source, frame.check_catalog_id or frame.check_object_id)
        == canonical_check_identity
    ]
    check_input_count = len(check_frames)
    check_flux = None
    check_flux_error = None
    check_magnitude = None
    check_magnitude_error = None
    check_reference = check_frames[0] if check_frames else None
    if check_input_count >= MIN_INPUTS_PER_BIN:
        check_weights = [
            1.0 / (frame.check_flux_error * frame.check_flux_error)
            for frame in check_frames
        ]
        check_total_weight = sum(check_weights)
        check_flux = sum(
            weight * frame.check_flux
            for weight, frame in zip(check_weights, check_frames)
        ) / check_total_weight
        check_flux_error = math.sqrt(1.0 / check_total_weight)
        check_magnitude = -2.5 * math.log10(check_flux)
        check_magnitude_error = (
            2.5 / math.log(10.0) * check_flux_error / check_flux
        )
    return BinnedFrame(tuple(frames), jd, flux, flux_error, magnitude, magnitude_error,
                       min(frame.jd - half for frame, half in zip(frames, half_exposures)),
                       max(frame.jd + half for frame, half in zip(frames, half_exposures)),
                       sum(frame.exptime for frame in frames),
                       check_input_count=check_input_count,
                       check_object_id="" if check_reference is None else check_reference.check_object_id,
                       check_catalog_source=(
                           "" if check_reference is None else check_reference.check_catalog_source
                       ),
                       check_catalog_id="" if check_reference is None else check_reference.check_catalog_id,
                       check_flux=check_flux,
                       check_flux_error=check_flux_error,
                       check_magnitude=check_magnitude,
                       check_magnitude_error=check_magnitude_error,
                       check_catalog_mag=(
                           None if check_reference is None else check_reference.check_catalog_mag
                       ),
                       check_catalog_mag_error=(
                           None if check_reference is None else check_reference.check_catalog_mag_error
                       ),
                       check_catalog_nobs=(
                           "" if check_reference is None else check_reference.check_catalog_nobs
                       ),
                       check_catalog_b_minus_v=(
                           None if check_reference is None else check_reference.check_catalog_b_minus_v
                       ),
                       check_catalog_g_minus_r=(
                           None if check_reference is None else check_reference.check_catalog_g_minus_r
                       ))
