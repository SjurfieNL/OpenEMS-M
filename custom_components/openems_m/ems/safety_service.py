# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Publieke, gedeelde veiligheidscontrole voor accu-operaties."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyAssessment:
    safe: bool
    reason: str = ""


class BatterySafetyService:
    """Eén publieke veiligheidsinterface voor alle planners/controllers."""

    SAFE_STATUS_VALUES = {
        "", "none", "normal", "ok", "off", "0",
        "no alarm", "no alarms", "no fault", "no faults",
        "geen alarm", "geen fout",
    }

    def __init__(self, app, batteries):
        self.app = app
        self.batteries = batteries
        self.health_manager = None

    def bind_health_manager(self, health_manager):
        self.health_manager = health_manager

    def is_battery_operation_safe(self, index, desired_state):
        if (
            self.health_manager is not None
            and not self.health_manager.is_available(index)
        ):
            status = self.health_manager.status(index)
            return SafetyAssessment(
                False,
                "tijdelijk buiten gebruik: "
                + str(status.get("reason") or "herstelpauze"),
            )
        return self.hardware_assessment(index, desired_state)

    def hardware_assessment(self, index, desired_state):
        if index < 0 or index >= len(self.batteries):
            return SafetyAssessment(False, "Onbekende accu")
        battery = self.batteries[index]
        for field in ("fault_status", "alarm_status"):
            entity = battery.get(field)
            if not entity:
                continue
            value = self.app.get_state(entity)
            if str(value).strip().lower() not in self.SAFE_STATUS_VALUES:
                return SafetyAssessment(False, f"{field}={value}")

        soc = self._number(battery.get("soc"))
        if soc is None:
            return SafetyAssessment(True)
        minimum = self._number("input_number.marstek_minimum_soc")
        target = self._number("input_number.marstek_doel_soc")
        if (
            desired_state in {"forced_discharge", "discharge", "export"}
            and minimum is not None and soc <= minimum
        ):
            return SafetyAssessment(
                False, f"SOC {soc:.1f}% <= minimum {minimum:.1f}%"
            )
        if (
            desired_state in {"charging", "charge"}
            and target is not None and soc >= target
        ):
            return SafetyAssessment(
                False, f"SOC {soc:.1f}% >= doel {target:.1f}%"
            )
        return SafetyAssessment(True)

    def all_safe(self, desired_state, indexes=None):
        if indexes is None:
            selected = [
                index for index in range(len(self.batteries))
                if (
                    self.health_manager is None
                    or self.health_manager.is_available(index)
                )
            ]
        else:
            selected = list(indexes)
        # Een tijdelijke uitsluiting laat de overige accu's veilig doorwerken.
        # Zonder beschikbare accu is een gezamenlijke operatie niet mogelijk.
        if not selected:
            return False
        return all(
            self.is_battery_operation_safe(index, desired_state).safe
            for index in selected
        )

    def _number(self, entity):
        if not entity:
            return None
        try:
            return float(self.app.get_state(entity))
        except (TypeError, ValueError):
            return None
