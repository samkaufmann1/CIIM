"""Review draft: annual aircraft scheduling, not yet wired into the model.

Intended home: deploy_aircraft.py. Inputs are validated upstream, as elsewhere
in CIIM. Annual flights are whole flights; cycles per aircraft are cohort
averages and can therefore be fractional.
"""

from dataclasses import dataclass

import pandas as pd


@dataclass
class Cohort:
    delivered: int             # calendar year entering service
    count: int
    cycles: float = 0.0        # accumulated cycles per aircraft


def schedule_aircraft(
    flights: pd.Series,
    aircraft_required: pd.Series,
    lifetime_years: int,
    lifetime_cycles: int,
) -> pd.DataFrame:
    """Retire, deliver, and fly aircraft in annual steps.

    flights and aircraft_required share a consecutive integer year index.
    The caller sizes aircraft_required using availability and excess capacity.

    At the start of each year, retire cohorts whose calendar life has elapsed
    or whose accumulated cycles have reached the limit. Buy any fleet shortfall,
    then share this year's flights equally across all aircraft in service.
    Availability rotates across the fleet; spare capacity reduces average wear.
    Surplus aircraft remain in service and incur full annual maintenance.

    Cycle retirement uses the previous year's closing cycle count. A cohort
    reaching its limit during a year serves that entire year and retires at the
    next year's start. This annual approximation can overshoot the cycle limit
    by less than one year's flying; no within-year replacements are modeled.

    The caller shifts deliveries backward by the lead time to schedule orders
    and capital spending, using the same annual convention as other methods.
    """
    cohorts: list[Cohort] = []
    rows = []

    for year, annual_flights in flights.items():
        required = int(aircraft_required.loc[year])
        retiring_calendar = retiring_cycles = 0
        survivors = []
        for cohort in cohorts:
            if year - cohort.delivered >= lifetime_years:
                # Count simultaneous calendar and cycle retirement only once.
                retiring_calendar += cohort.count
            elif cohort.cycles >= lifetime_cycles - 1e-8:
                # Tolerance avoids delaying retirement for floating-point noise.
                retiring_cycles += cohort.count
            else:
                survivors.append(cohort)
        cohorts = survivors

        active = sum(cohort.count for cohort in cohorts)
        entering = max(0, required - active)
        if entering:
            cohorts.append(Cohort(int(year), entering))
            active += entering

        cycles_per_aircraft = float(annual_flights) / active if active else 0.0
        for cohort in cohorts:
            cohort.cycles += cycles_per_aircraft

        rows.append({
            "year": year,
            "aircraft_required": required,
            "aircraft_entering_service": entering,
            "aircraft_retiring_calendar": retiring_calendar,
            "aircraft_retiring_cycles": retiring_cycles,
            "aircraft_active": active,
            "cycles_per_aircraft": cycles_per_aircraft,
        })

    return pd.DataFrame(rows).set_index("year")
