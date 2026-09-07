"""Turning a temperature target into a deployment pattern.

A scenario may name the deployed masses directly, or name the temperature it wants
and leave the masses to be derived. This module does the second. A scenario
with a deployment_pattern skips it entirely.

For each year the temperature pattern gives the warming expected without SAI
and the warming wanted. The difference is the cooling the program has to
deliver, clamped at zero: a year already below its target needs no deployment.

Turning that cooling into a mass takes three numbers.

    cooling_per_forcing is the global mean cooling produced by a watt per
    square meter of forcing applied at the injection latitude. The cooling
    wanted, divided by this number, equals the forcing needed.

    The deployed material's forcing.per_Tg_per_year is the forcing produced by
    a teragram deployed each year. The forcing needed, divided by it, is a mass.

    That coefficient was measured for injection at one particular place, whose
    residence time is recorded beside it as forcing.reference_lifetime_months.
    Aerosol deployed where it survives longer does more with the same annual
    mass, so the mass is scaled by reference_lifetime_months over the residence
    time at this scenario's own altitude and latitude. Above one means more
    material is needed than in the reference case; below one, less.

Residence times come from the lifetime table, averaged across its two
seasons and interpolated between altitude rows. Only the ratio of two of them
is ever taken, so the table stays in months and nothing converts to years.

What falls out is one number: teragrams per year of the deployed material per
degree of cooling. The cooling schedule multiplied by this number givesthe mass to deploy
each year, half into the northern hemisphere and half into the southern at the
same latitude (or all of it into one column at the equator).

This module reads no files and imports nothing from load_inputs. Everything
arrives as an argument, which makes the signature of generate_deployment_pattern()
the list of everything the derivation depends on. It returns Tg/year, so
load_inputs validates and converts it exactly as it would a hand-written CSV,
and no unit conversion happens in here. determine_cooling() is public as well,
because the cost per degree-year the front ends will report has to be divided by
the same cooling schedule the deployment was derived from, not a second copy of it.

Processes this module does not represent:

    Response is linear in injection rate. Real forcing per teragram falls as
    the rate rises: more aerosol coagulates into larger particles, which
    scatter less per unit mass and fall out sooner. The model is optimistic
    here, increasingly so above roughly a degree of cooling.

    Cooling is instantaneous. A year's injection produces that year's cooling
    with no ocean lag, so a sharply rising target understates what
    the early years would really need.

    One latitude, north and south. Allocating across several latitudes would
    need goals beyond global mean temperature, which this model can't realistically simulate.

    The lifetime adjustment is a ratio of passive tracer residence times, which
    take no account of aerosol settling out. However, the same omission sits above
    and below the division sign, so one might optimistically say that the bias cancels out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# --- Reading the lifetime table ----------------------------------------------


def average_seasons(lifetimes: pd.DataFrame) -> pd.DataFrame:
    """Average two injection seasons into one annual table.
    This is done to the Toohey (2025) data as a crude stand-in for a program that deploys all year round. It is a named
    function rather than a line inside find_lifetime because it is the whole of
    the seasonal simplification: a seasonal deployment strategy would replace
    this and nothing else.
    """
    return lifetimes.groupby(level="altitude").mean()


def find_lifetime(annual: pd.DataFrame, altitude: float, latitude: int) -> float:
    """Residence time in months at one altitude and latitude.

    Interpolated between the table's altitude rows, because the difference
    between 13 and 14 km matters and the table is coarse.
    """
    # Dropping the blanks first is what makes the two errors below differ. A
    # blank means that altitude is below the tropopause at this latitude, so
    # the lowest usable row is 17 km near the equator but 13 km at 45 degrees.
    column = annual[latitude].dropna()

    if altitude < column.index.min():
        raise ValueError(
            f"{altitude:,.0f} m is below the lowest stratospheric level the lifetime "
            f"table has at {latitude} degrees ({column.index.min():,.0f} m)"
        )
    if altitude > column.index.max():
        raise ValueError(
            f"{altitude:,.0f} m is above the top of the lifetime table "
            f"({column.index.max():,.0f} m)"
        )

    # groupby returns altitudes ascending, which is what np.interp requires.
    return float(np.interp(altitude, column.index, column.values))


# --- From a temperature target to a deployed mass ----------------------------


def determine_cooling(temperature: pd.DataFrame) -> pd.Series:
    """Cooling the program must deliver each year, in degC.

    Clamped at zero rather than rejected: a year whose target sits above the
    warming expected without SAI needs no deployment, which is the ordinary way
    a program begins. load_inputs rejects a pattern where no year needs any cooling.
    """
    cooling = temperature["temperature_without_sai"] - temperature["temperature_target"]
    return cooling.clip(lower=0)


def determine_mass_per_degree(
    cooling_per_forcing: float,
    forcing_per_Tg_per_year: float,
    reference_lifetime_months: float,
    lifetime_months: float,
) -> float:
    """Tg per year of the deployed material per degC of cooling."""
    forcing = 1.0 / cooling_per_forcing            # W/m2 needed per degC
    mass = forcing / forcing_per_Tg_per_year       # Tg/year needed per degC

    # Aerosol deployed where it survives longer does more with the same annual
    # mass. Both times are tracer residence times from the same table, so the
    # units cancel and so, to a degree, does their shared neglect of settling.
    return mass * (reference_lifetime_months / lifetime_months)


def spread_across_latitudes(
    mass: pd.Series, latitude: int, latitudes: list[int]
) -> pd.DataFrame:
    """Place a yearly mass into a deployment pattern's columns, half per hemisphere.

    Columns are the injection latitudes mirrored about the equator, which gives
    the same set a hand-written pattern CSV uses.
    """
    columns = sorted(set(latitudes) | {-lat for lat in latitudes})
    pattern = pd.DataFrame(0.0, index=mass.index, columns=columns)
    pattern.index.name = "year"
    pattern.columns.name = "latitude"

    if latitude == 0:
        pattern[0] = mass          # the equator has no halves to split into
    else:
        pattern[latitude] = mass / 2.0
        pattern[-latitude] = mass / 2.0
    return pattern


def generate_deployment_pattern(
    temperature: pd.DataFrame,              # year -> temperature_without_sai, temperature_target
    altitude: float,                        # meters
    latitude: int,                          # degrees, on the lifetime table's grid
    cooling_per_forcing: dict[int, float],  # degC per W/m2, by injection latitude
    lifetimes: pd.DataFrame,                # months, (season, altitude) x latitude
    forcing_per_Tg_per_year: float,         # W/m2 per Tg/year of the deployed material
    reference_lifetime_months: float,       # the residence time that figure was calibrated at
) -> pd.DataFrame:
    """The mass to deploy each year to meet the temperature pattern's target.

    Indexed by year, one column per latitude in the symmetric grid, in Tg per
    year -- the shape of a deployment pattern CSV, so load_inputs checks and
    converts it exactly as it would a hand-written one.
    """
    cooling = determine_cooling(temperature)
    lifetime = find_lifetime(average_seasons(lifetimes), altitude, latitude)
    per_degree = determine_mass_per_degree(
        cooling_per_forcing[latitude],
        forcing_per_Tg_per_year,
        reference_lifetime_months,
        lifetime,
    )
    return spread_across_latitudes(cooling * per_degree, latitude, list(cooling_per_forcing))