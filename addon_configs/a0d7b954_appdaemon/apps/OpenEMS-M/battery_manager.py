"""Leest en berekent alle accugegevens."""

from ems_config import MAX_PLANNED_CHARGE_POWER_W
from models import BatteryState


class BatteryManager:
    def __init__(self, app, batteries, disabled_provider=None):
        self.app = app
        self.batteries = batteries
        self.disabled_provider = disabled_provider

    def disabled_indexes(self):
        """Indexen van accu's die de gebruiker heeft uitgeschakeld."""
        if not callable(self.disabled_provider):
            return set()
        try:
            return {int(index) for index in self.disabled_provider()}
        except (TypeError, ValueError):
            return set()

    def read(self) -> BatteryState:
        disabled = self.disabled_indexes()
        soc_values = []
        capacities = []
        max_charge_values = []
        max_discharge_values = []
        for index, item in enumerate(self.batteries):
            if index in disabled:
                # Een uitgeschakelde accu telt niet mee in capaciteit en
                # vermogen en mag offline zijn zonder de planning te storen.
                soc = self._optional_float(item["soc"])
                soc_values.append(0.0 if soc is None else soc)
                capacities.append(0.0)
                max_charge_values.append(0.0)
                max_discharge_values.append(0.0)
                continue
            soc_values.append(self._float_state(item["soc"]))
            capacities.append(self._float_state(item["capacity"]))
            max_charge_values.append(
                self._float_state(item["max_charge_power"])
            )
            max_discharge_values.append(
                self._float_state(item["max_discharge_power"])
            )

        total_charge = sum(max_charge_values)
        if total_charge > MAX_PLANNED_CHARGE_POWER_W:
            # De planning rekent nooit met meer totaal laadvermogen dan de
            # aansluiting toelaat; verdeel de beperking evenredig.
            factor = MAX_PLANNED_CHARGE_POWER_W / total_charge
            max_charge_values = [value * factor for value in max_charge_values]

        enabled_soc = [
            value for index, value in enumerate(soc_values)
            if index not in disabled
        ]
        capacity = sum(capacities)
        average_soc = (
            sum(enabled_soc) / len(enabled_soc) if enabled_soc else 0.0
        )

        return BatteryState(
            names=[item["name"] for item in self.batteries],
            soc_values=soc_values,
            capacities=capacities,
            capacity=capacity,
            average_soc=average_soc,
            stored_energy=capacity * average_soc / 100,
            max_charge_values=max_charge_values,
            max_discharge_values=max_discharge_values,
            max_charge=sum(max_charge_values),
            max_discharge=sum(max_discharge_values),
            available=[
                index not in disabled for index in range(len(self.batteries))
            ],
        )

    def _optional_float(self, entity_id):
        state = self.app.get_state(entity_id)
        if state in (None, "", "unknown", "unavailable"):
            return None
        try:
            return float(state)
        except (TypeError, ValueError):
            return None

    def _float_state(self, entity_id):
        state = self.app.get_state(entity_id)
        if state in (None, "", "unknown", "unavailable"):
            raise ValueError(f"Geen geldige waarde voor {entity_id}: {state}")
        return float(state)
