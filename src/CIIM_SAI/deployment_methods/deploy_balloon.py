"""Cost and schedule model for balloon-borne deployment.

Spherical latex balloons are filled with a lift gas and with the deployed material as a
payload gas, released from launch facilities, and burst at the injection
altitude, releasing both. Balloons are disposable, so one is consumed per launch; the capital in
this method is launch facilities, launchpads, and the drones that collect the
debris.

A key future capability to be implemented is non-elastic plastic balloons. These
would have non-spherical geometries, such as cylinders or tetroons. Since this
module assumes spherical balloons, it will need to be modified to cover those cases.

Defines the shape of inputs/deployment_methods/balloon.yaml. Reads no files:
everything arrives via Inputs.
"""

from __future__ import annotations

import math

from CIIM_SAI.climatology import (
    GAS_CONSTANT,
    MOLAR_MASS_AIR,
    find_atmospheric_temperature_and_pressure,
)


def find_gas_fill(
    balloon_mass: float,          # kg, the empty envelope
    burst_diameter: float,        # m, the diameter the latex bursts at
    altitude: float,              # m, where the balloon is to reach that diameter
    buoyancy_margin: float,       # net lift as a fraction of displaced air mass; proportion between 0 and 1
    molar_mass_lift: float,       # kg/mol
    molar_mass_payload: float,    # kg/mol
) -> tuple[float, float]:
    """Kilograms of lift gas and of payload gas in one balloon.

    The fill is set by the altitude at which the balloon is meant to burst. A
    balloon bursts when the latex reaches its burst diameter, so a larger fill
    reaches that diameter lower down: the target altitude fixes the volume's
    pressure and temperature, and so fixes how many moles of gas go in. Aiming
    higher means less gas, and therefore less lift and less payload.

    Two facts make the solve a pair of linear equations rather than an ascent
    simulation.

        Free lift does not change with altitude. These balloons are not pressurized, so the
        gas inside sits at ambient pressure and temperature, and the ratio of
        gas density to air density stays at the ratio of their molar masses all
        the way up. The mass of gas is fixed, so the displaced air mass --
        density of air times volume -- is fixed too. This is why the balance 
        can be written at burst altitude, where volume is known because it is,
        by definition, the maximum volume of the balloon.

        Displaced air mass is just total moles times the molar mass of air.
        Equal volumes at equal pressure and temperature hold equal moles, so
        the air pushed aside has the same mole count as the gas inside, and
        there is no need to compute a density at all.

    Writing N for total moles at burst, the two equations are

        n_lift + n_payload = N = P * V / (R * T)
        balloon_mass + n_lift * M_lift + n_payload * M_payload
            = (1 - buoyancy_margin) * N * M_air

    the second being Archimedes' principle with the margin held back as net lift.
    Substituting the first into the second and solving for n_lift gives the
    expression below.

    Raises ValueError if an input is out of range, or if the solve does not land
    on a physical fill. Both gas counts must come out non-negative, and the two
    ways of failing have different causes:

        moles_payload < 0 -- the envelope is too heavy for the air it displaces.
        At high enough altitude even an all-lift-gas balloon cannot carry the
        envelope while holding back the margin.

        moles_lift < 0 -- the fill needs no lift gas, because the payload gas is
        light enough on its own. This model assumes a payload gas denser than
        air, so this points at molar_mass_payload rather than at altitude.

    Both messages are phrased for molar_mass_lift < molar_mass_payload. The
    algebra is symmetric in the two gases and needs only that their molar masses
    differ, so swapping the arguments still returns a correct fill with the two
    values swapped -- but a failure will then be reported under the other
    message. The underlying condition either way is that
    (1 - buoyancy_margin) * M_air - balloon_mass / N, the mean molar mass the
    fill must hit, falls between the two molar masses.
    """
    if not 0.0 < buoyancy_margin < 1.0:
        raise ValueError(
            f"buoyancy_margin is net lift as a fraction of displaced air mass and "
            f"must lie strictly between 0 and 1; got {buoyancy_margin!r}. At 0 the "
            f"balloon is neutrally buoyant and never ascends; at 1 or above it "
            f"would have to weigh nothing at all. A margin passed as a percentage "
            f"rather than a fraction lands here."
        )
    if molar_mass_lift == molar_mass_payload: # IDK if this error check is really that important, but it doesn't hurt
        raise ValueError(
            "molar_mass_lift and molar_mass_payload are equal, so the fill is "
            "singular: every split of the moles gives the same total mass"
        )

    temperature, pressure = find_atmospheric_temperature_and_pressure(altitude)

    volume = 4.0 / 3.0 * math.pi * (burst_diameter / 2.0) ** 3
    moles_total = pressure * volume / (GAS_CONSTANT * temperature)
    displaced_air_mass = moles_total * MOLAR_MASS_AIR

    moles_lift = (
        moles_total * ((1.0 - buoyancy_margin) * MOLAR_MASS_AIR - molar_mass_payload)
        - balloon_mass
    ) / (molar_mass_lift - molar_mass_payload)
    moles_payload = moles_total - moles_lift

    if moles_payload < 0.0:
        raise ValueError(
            f"a {balloon_mass:,.1f} kg balloon bursting at {altitude:,.0f} m displaces "
            f"{displaced_air_mass:,.2f} kg of air, which cannot lift the envelope and a "
            f"{buoyancy_margin:.0%} margin even filled entirely with lift gas"
        )
    if moles_lift < 0.0:
        raise ValueError(
            f"the payload gas is buoyant enough at {altitude:,.0f} m to need no lift gas; "
            f"this model assumes a payload gas denser than air"
        )

    return moles_lift * molar_mass_lift, moles_payload * molar_mass_payload