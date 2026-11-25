# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Algemene energievoorraad per accu voor planning en simulatie."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BatteryEnergyInventory:
    capacity_kwh: float
    energy_kwh: float
    discharge_efficiency: float

    @property
    def soc_pct(self):
        return (
            self.energy_kwh / self.capacity_kwh * 100
            if self.capacity_kwh > 0 else 0.0
        )


class EnergyInventory:
    def __init__(self, batteries):
        self.batteries = list(batteries)

    @classmethod
    def from_values(cls, capacities, energies, efficiencies):
        return cls([
            BatteryEnergyInventory(
                capacity_kwh=max(0.0, float(capacity)),
                energy_kwh=min(
                    max(0.0, float(capacity)),
                    max(0.0, float(energies[index])),
                ),
                discharge_efficiency=min(
                    1.0, max(0.01, float(efficiencies[index]))
                ),
            )
            for index, capacity in enumerate(capacities)
        ])

    @property
    def total_kwh(self):
        return sum(item.energy_kwh for item in self.batteries)

    @property
    def soc_values(self):
        return [item.soc_pct for item in self.batteries]

    @property
    def energy_values(self):
        return [item.energy_kwh for item in self.batteries]

    def withdraw_ac_energy(self, delivered_kwh, power_weights):
        total_power = sum(max(0.0, float(value)) for value in power_weights)
        draws = []
        for index, battery in enumerate(self.batteries):
            delivered = (
                max(0.0, float(delivered_kwh))
                * max(0.0, float(power_weights[index])) / total_power
                if total_power > 0 else 0.0
            )
            draw = min(
                battery.energy_kwh,
                delivered / battery.discharge_efficiency,
            )
            battery.energy_kwh -= draw
            draws.append(draw)
        return draws
