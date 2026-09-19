"""Scheduling arithmetic shared by the deployment methods.

When assets are bought, when they retire, and how their cost is spread across the
years. Nothing here knows what the assets are: the same functions size a fleet
of vehicles, a set of balloon launchpads and a population of drones. A method module
describes its own assets and comes here for the timing.

Pure arithmetic over pandas objects indexed by year. Nothing reads a file,
imports a schema, or knows which method called it -- which is why
spread_development takes two numbers rather than a Development object, leaving
each method free to keep a class of its own.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def find_program_years(
    pattern: pd.DataFrame, lead_time_years: int, development_years: int
) -> pd.RangeIndex:
    """The years a program spans, working backwards from its first deployment.

    Assets must be in service by the first year anything is deployed, so they
    were ordered lead_time_years earlier, and development had to finish before
    that first order. The index runs from the start of development through the
    last year of the pattern.

    A method with more than one lead time passes the longest: the first order is
    set by whichever item takes longest to arrive.
    """
    deploying = pattern.index[pattern.sum(axis=1) > 0]
    if deploying.empty:
        raise ValueError("the deployment pattern deploys nothing in any year")
    return pd.RangeIndex(
        int(deploying.min()) - lead_time_years - development_years,
        int(pattern.index.max()) + 1,
        name="year",
    )


def determine_units_required(demand: pd.Series, capacity: float) -> pd.Series:
    """Assets that must be in service each year to supply `demand`.

    `capacity` is what one asset supplies in a year, in whatever `demand`
    counts: missions for a vehicle, launches for a launchpad, catches for a
    drone.
    """
    # Rounded before the ceiling: 2.0000000000000004 units of demand is a float
    # artifact, and ceiling it would buy a whole spurious asset.
    return np.ceil((demand / capacity).round(9)).astype(int)


def schedule_assets(units_required: pd.Series, lifetime_years: int) -> pd.DataFrame:
    """Entries, retirements and the number in service, year by year.

    An asset entering service in year y retires at y + lifetime_years, so a
    year's retirements are the entries from lifetime_years ago. The loop carries
    a single number: how many are in service now.

    Assets never retire early and are only ever bought to cover a shortfall, so
    a fall in demand leaves the surplus in service until its life runs out.
    """
    entering: dict[int, int] = {}
    retiring: dict[int, int] = {}
    active: dict[int, int] = {}
    in_service = 0

    for year, required in units_required.items():
        retiring[year] = entering.get(year - lifetime_years, 0)
        in_service -= retiring[year]
        entering[year] = max(0, int(required) - in_service)
        in_service += entering[year]
        active[year] = in_service

    return pd.DataFrame(
        {"entering_service": entering, "retiring": retiring, "active": active}
    ).rename_axis("year")


def spread_development(years: pd.RangeIndex, nre: float, duration_years: int) -> pd.Series:
    """Non-recurring engineering spread evenly over the years before the first order.

    Development must finish before anything can be ordered, which is the first
    duration_years of `years` -- exactly where find_program_years puts it, so
    the two cannot disagree about when development happens.
    """
    spend = pd.Series(0.0, index=years)
    spend.iloc[:duration_years] = nre / duration_years
    return spend


def spread_capital(entering_service: pd.Series, cost: float, lead_time_years: int) -> pd.Series:
    """Unit cost paid evenly across the lead time, completing at delivery.

    An asset entering service in year d is paid for in even installments across
    years d - lead_time .. d - 1, so a year's capital outlay covers every unit
    due to arrive within the lead time ahead. With no lead time the whole cost
    falls in the delivery year, which is what an off-the-shelf purchase wants.

    Even spreading is a financing assumption: real procurement uses deposits and
    milestone payments. It is neutral, and it only affects the ramp-up and the
    tail -- for a population in steady replacement the annual totals are
    identical.
    """
    if lead_time_years == 0:
        return entering_service * float(cost)
    arriving_soon = sum(entering_service.shift(-k) for k in range(1, lead_time_years + 1))
    return arriving_soon.fillna(0.0) * (cost / lead_time_years)