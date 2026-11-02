"""Shared geometric airmass, independent of Config, Qt, Siril and file writers.

Inputs are WGS84 latitude/longitude, ICRS degrees and the already resolved UTC
measurement JD. Broadcasting follows NumPy (use stars[:, None] for a grid).
No exposure midpoint, stack weighting or extinction correction is performed
here. Astropy manages IERS downloads and its cache using its configuration. Missing numeric output is NaN; writers must serialize it as
null/empty together with its status, never as an invented number.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import warnings

import erfa
import numpy as np
from astropy import units as u
from astropy.time import Time
from astropy.utils import iers

MODEL_ID = 'GEOMETRIC_SECZ_V1'
MIN_ALTITUDE_DEG = 10.0
# Only accommodate floating-point roundoff at a mathematically exact boundary.
ANGLE_TOLERANCE_DEG = 1e-10


@dataclass(frozen=True)
class AirmassResult:
    airmass: np.ndarray
    altitude_deg: np.ndarray
    status: np.ndarray
    earth_orientation_status: np.ndarray
    earth_orientation: dict
    warnings: tuple[str, ...]
    elevation_m: float
    elevation_source: str
    model_id: str = MODEL_ID


def airmass_from_altitude(altitude_deg):
    """Return (airmass, status) for geometric altitude; 10 degrees is included."""
    altitude = np.asarray(altitude_deg, dtype=float)
    values = np.full(altitude.shape, np.nan)
    status = np.full(altitude.shape, 'INVALID_ALTITUDE', dtype='U48')
    finite = np.isfinite(altitude)
    below = finite & (altitude < MIN_ALTITUDE_DEG - ANGLE_TOLERANCE_DEG)
    status[below] = 'BELOW_AIRMASS_MODEL_ALTITUDE_LIMIT'
    valid = finite & ~below & (altitude <= 90 + ANGLE_TOLERANCE_DEG)
    values[valid] = 1 / np.sin(np.deg2rad(np.clip(altitude[valid], MIN_ALTITUDE_DEG, 90)))
    status[valid] = 'OK'
    return values, status


def _numbers(raw):
    """Keep missing distinct from malformed values, including booleans."""
    source = np.asarray(raw, dtype=object)
    values = np.full(source.shape, np.nan)
    missing = np.zeros(source.shape, dtype=bool)
    for index in np.ndindex(source.shape):
        value = source[index]
        if value is None:
            missing[index] = True
        elif isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_)):
            values[index] = float(value)
    return values, missing


def calculate_airmass(*, latitude_deg, longitude_deg, ra_deg, dec_deg, jd_utc,
                      elevation_m=None) -> AirmassResult:
    """Compute paired/broadcast star-time samples with one explicit site.

    Optional elevation is WGS84 ellipsoidal metres. Without it, 0 m is a
    documented calculation assumption, not a measured site property. Earth
    orientation predictions are marked. Outside the table, nearest available
    corrections are used; without a table, UT1-UTC and polar motion are zero.
    Both approximations are explicit in the Earth-orientation status.
    """
    inputs = [_numbers(value) for value in (ra_deg, dec_deg, jd_utc)]
    ra, dec, jd, missing_ra, missing_dec, missing_jd = np.broadcast_arrays(
        *(item[0] for item in inputs), *(item[1] for item in inputs))
    shape = ra.shape
    altitude = np.full(shape, np.nan)
    values = np.full(shape, np.nan)
    status = np.full(shape, 'OK', dtype='U48')
    eop_status = np.full(shape, 'NOT_CHECKED', dtype='U24')
    metadata = {'auto_download': bool(iers.conf.auto_download),
                'auto_max_age_days': iers.conf.auto_max_age}
    messages = []
    height = 0.0 if elevation_m is None else elevation_m
    height_source = 'assumed_zero_for_geometry' if elevation_m is None else 'provided_wgs84_ellipsoidal'

    def result():
        return AirmassResult(values, altitude, status, eop_status, metadata,
                             tuple(dict.fromkeys(messages)), float(height) if site_valid else math.nan,
                             height_source)

    site_values = (latitude_deg, longitude_deg, height)
    site_valid = all(isinstance(value, (int, float, np.integer, np.floating))
                     and not isinstance(value, (bool, np.bool_)) and math.isfinite(value)
                     for value in site_values)
    site_valid = site_valid and -90 <= latitude_deg <= 90 and -180 <= longitude_deg <= 180
    if not site_valid:
        status[...] = 'MISSING_SITE' if latitude_deg is None or longitude_deg is None else 'INVALID_SITE'
        return result()
    position_valid = np.isfinite(ra) & np.isfinite(dec) & (ra >= 0) & (ra < 360) & (np.abs(dec) <= 90)
    time_valid = np.isfinite(jd) & (jd > 0)
    status[~time_valid] = 'INVALID_TIME'
    status[missing_jd] = 'MISSING_TIME'
    status[~position_valid] = 'INVALID_POSITION'
    status[missing_ra | missing_dec] = 'MISSING_POSITION'
    eligible = np.flatnonzero((position_valid & time_valid).ravel())
    if not eligible.size:
        return result()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        times = Time(jd.ravel()[eligible], format='jd', scale='utc')
        dut1 = np.zeros(eligible.size)
        xp = np.zeros(eligible.size)
        yp = np.zeros(eligible.size)
        eop_status.ravel()[eligible] = 'DEFAULT_ZERO'
        metadata['fallback_policy'] = 'nearest_available_else_zero'
        try:
            table = iers.earth_orientation_table.get()
            try:
                # Request the actual measurement time first so IERS_Auto can
                # refresh a stale cached table using Astropy's normal policy.
                ut_value, ut_status = table.ut1_utc(times, return_status=True)
                pm_x, pm_y, pm_status = table.pm_xy(times, return_status=True)
            except (ValueError, OSError, RuntimeError) as exc:
                messages.append(f'Earth-orientation update/interpolation failed: {exc}; '
                                'using available table data without another download.')
                metadata['refresh_failed'] = True
                # Only the recovery attempt accepts stale data and suppresses
                # another download. The caller's configuration is restored.
                with iers.conf.set_temp('auto_download', False), iers.conf.set_temp('auto_max_age', None):
                    nearest = Time(np.clip(times.mjd, float(np.min(table['MJD'].value)),
                                           float(np.max(table['MJD'].value))), format='mjd', scale='utc')
                    ut_value, ut_status = table.ut1_utc(nearest, return_status=True)
                    pm_x, pm_y, pm_status = table.pm_xy(nearest, return_status=True)
            # IERS_Auto may have refreshed this table in place during the call.
            minimum = float(np.min(table['MJD'].value))
            maximum = float(np.max(table['MJD'].value))
            dut1 = np.asarray(ut_value.to_value(u.s))
            xp = np.asarray(pm_x.to_value(u.rad))
            yp = np.asarray(pm_y.to_value(u.rad))
            usable = np.isfinite(dut1) & np.isfinite(xp) & np.isfinite(yp)
            covered = (times.mjd >= minimum) & (times.mjd <= maximum)
            predicted = (ut_status == iers.FROM_IERS_A_PREDICTION) | (pm_status == iers.FROM_IERS_A_PREDICTION)
            eop_status.ravel()[eligible] = np.where(usable,
                np.where(covered, np.where(predicted, 'PREDICTED', 'OBSERVED'),
                         'NEAREST_AVAILABLE'), 'DEFAULT_ZERO')
            dut1 = np.where(usable, dut1, 0.)
            xp = np.where(usable, xp, 0.)
            yp = np.where(usable, yp, 0.)
            metadata.update(table=type(table).__name__, minimum_mjd=minimum,
                            maximum_mjd=maximum, data_path=table.meta.get('data_path', ''))
        except (ValueError, OSError, RuntimeError, KeyError) as exc:
            dut1 = np.zeros(eligible.size)
            xp = np.zeros(eligible.size)
            yp = np.zeros(eligible.size)
            eop_status.ravel()[eligible] = 'DEFAULT_ZERO'
            messages.append(f'Earth orientation: {exc}; using UT1-UTC=0 s and polar motion=0.')
        if np.any(eop_status.ravel()[eligible] == 'NEAREST_AVAILABLE'):
            messages.append('Earth orientation outside table coverage: nearest available corrections used.')
        if np.any(eop_status.ravel()[eligible] == 'DEFAULT_ZERO'):
            messages.append('Earth orientation unavailable: using UT1-UTC=0 s and polar motion=0.')
        try:
            # ERFA is Astropy's astrometry backend. Supplying EOP explicitly
            # keeps this transformation usable even when no IERS table can load.
            _, zenith, _, _, _, _ = erfa.atco13(
                np.deg2rad(ra.ravel()[eligible]), np.deg2rad(dec.ravel()[eligible]),
                0., 0., 0., 0., times.jd1, times.jd2, dut1,
                math.radians(longitude_deg), math.radians(latitude_deg), height,
                xp, yp, 0., 0., 0., 0.55)
            altitude.ravel()[eligible] = 90. - np.rad2deg(zenith)
            sample_values, sample_status = airmass_from_altitude(altitude.ravel()[eligible])
            values.ravel()[eligible] = sample_values
            status.ravel()[eligible] = sample_status
        except (ValueError, OSError, RuntimeError, erfa.ErfaError) as exc:
            status.ravel()[eligible] = 'COMPUTATION_ERROR'
            messages.append(f'Coordinate transformation: {exc}')
        messages.extend(str(item.message) for item in caught)
    return result()


def validate_export_metadata(metadata, *, source, export_name):
    """Validate saved geometry provenance; return whether airmass is required."""
    version = str(metadata.get('RESULT_METADATA_VERSION', '1'))
    if version not in {'1', '2'}:
        raise ValueError(f"{source}: unsupported result metadata version {version!r}")
    if version == '1':
        return False
    if metadata.get('AIRMASS_MODEL') != MODEL_ID:
        raise ValueError(f"{source}: missing or unsupported airmass model; use a new measurement result.")
    for key, lower, upper in (
        ('OBSERVER_LATITUDE', -90, 90), ('OBSERVER_LONGITUDE', -180, 180),
        ('AIRMASS_TARGET_RA_DEG', 0, 360), ('AIRMASS_TARGET_DEC_DEG', -90, 90),
    ):
        try:
            value = float(metadata.get(key, ''))
        except (TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value) or not lower <= value <= upper or (key == 'AIRMASS_TARGET_RA_DEG' and value == 360):
            raise ValueError(f"{source}: {export_name} requires valid saved {key}; check site/target coordinates and use a new measurement result.")
    return True


def saved_export_airmass(row, *, required):
    """Return a saved model-valid value rounded for export, or na for old Results."""
    if not required:
        return 'na'
    try:
        value = float(row.get('airmass', ''))
    except (TypeError, ValueError):
        value = math.nan
    maximum = 1 / math.sin(math.radians(MIN_ALTITUDE_DEG))
    if row.get('airmass_status', '').strip() != 'OK' or not math.isfinite(value) or not 1 <= value <= maximum + 1e-8:
        raise ValueError(
            f"requires valid saved airmass (status {row.get('airmass_status') or 'missing'}, "
            f"airmass {row.get('airmass', '')!r}). Check the measurement site, target coordinates "
            "and times; remeasure missing geometry. Below 10 degrees, use observations within the model range."
        )
    return f'{value:.3f}'
