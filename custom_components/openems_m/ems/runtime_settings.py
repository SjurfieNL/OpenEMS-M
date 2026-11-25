# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Leest door de gebruiker gekozen Home Assistant-entiteiten veilig in."""

from copy import deepcopy
from .ems_config import (
    BATTERIES,
    BATTERY_DISABLED_HELPERS,
    BATTERY_ENTITY_FIELDS,
    BATTERY_ENTITY_HELPERS,
    LOAD_BALANCER_HELPERS,
    LB_BOTH_HELPER,
    LB_CT_OFF_HELPER,
    LB_DEFAULT_MAX_EXPORT_W,
    LB_DEFAULT_MAX_IMPORT_W,
    LB_EXPORT_RANGE_W,
    LB_IMPORT_RANGE_W,
    LB_INITIALIZED_HELPER,
    LB_MAX_EXPORT_HELPER,
    LB_MAX_IMPORT_HELPER,
    LB_P1_OFF_HELPER,
    LB_PREFERRED_HELPER,
    AUTO_EXPORT_RESERVE_HELPER,
    SMART_CHARGE_BUFFER_HELPER,
    SMART_CHARGE_HELPER,
    SMART_CHARGE_MIN_DIFFERENCE_HELPER,
    SMART_CHARGE_RESERVE_HELPER,
    GRID_POWER_ENTITY_HELPER,
    GRID_SENSOR_SOURCE_HELPER,
    GRID_REFERENCE_ENTITY_HELPER,
    GRID_POWER_SIGN_HELPER,
    GRID_OFFSET_MODE_HELPER,
    GRID_MANUAL_OFFSET_HELPER,
    GRID_AUTOMATIC_OFFSET_HELPER,
    GRID_EXPORT_MODE_HELPER,
    GRID_CHARGE_ALLOWED_HELPER,
    GRID_EXPORT_ALLOWED_HELPER,
    FRANK_ALL_IN_ENTITY_HELPER,
    FRANK_MARKET_ENTITY_HELPER,
    HELPERS,
    HOME_POWER_ENTITY_HELPER,
    PRICE_SENSOR,
    CHARGE_EFFICIENCY_MODE_HELPER,
    DISCHARGE_EFFICIENCY_MODE_HELPER,
    DYNAMIC_EXPORT_RESERVE_HELPER,
    EXPORT_RESERVE_MODE_HELPER,
    EXPORT_FIXED_RESERVE_KWH_HELPER,
    EXPORT_BEFORE_CHARGE_MODE_HELPER,
    EXPORT_BEFORE_CHARGE_RESERVE_KWH_HELPER,
    EXPORT_DYNAMIC_RESERVE_KWH_HELPER,
    HOME_POWER_OFFSET_HELPER,
    EXPORT_EFFICIENCY_MODE_HELPER,
    MANUAL_CHARGE_EFFICIENCY_HELPER,
    MANUAL_DISCHARGE_EFFICIENCY_HELPER,
    MANUAL_EXPORT_EFFICIENCY_HELPER,
    SELF_CONSUMPTION_FALLBACK_HELPER,
    SELF_CONSUMPTION_MODE_HELPER,
)


class RuntimeSettings:
    """Centrale bron voor de configuratie die in HA gewijzigd kan worden."""

    def __init__(self, app):
        self.app = app

    def batteries(self):
        result = deepcopy(BATTERIES)
        for index, battery in enumerate(result):
            for field in BATTERY_ENTITY_FIELDS:
                configured = self._text(BATTERY_ENTITY_HELPERS[(index, field)])
                if configured:
                    battery[field] = configured
        return result

    def battery_disabled(self, index):
        """True als de gebruiker deze accu in de instellingen heeft uitgezet.

        Een ontbrekende of onbeschikbare helper telt als "niet uitgezet", zodat
        een HA-herstart of een nog niet geladen helper nooit een accu stopt.
        """
        try:
            helper = BATTERY_DISABLED_HELPERS[int(index)]
        except (IndexError, TypeError, ValueError):
            return False
        return self._text(helper).lower() == "on"

    def disabled_battery_indexes(self):
        return [
            index for index in range(len(BATTERIES))
            if self.battery_disabled(index)
        ]

    def load_balancer_enabled(self, kind):
        """Load balancer per onderdeel; ontbrekend of onbekend telt als AAN.

        De helper heeft de omgekeerde betekenis: AAN = uitgeschakeld.
        """
        helper = LOAD_BALANCER_HELPERS.get(kind)
        if helper is None:
            return True
        return self._text(helper).lower() != "on"

    def auto_export_reserve_enabled(self):
        return self._text(AUTO_EXPORT_RESERVE_HELPER).lower() == "on"

    def smart_charge_params(self):
        """Parameters voor slim vooruitkijken; uit als de schakelaar uit is."""
        enabled = self._text(SMART_CHARGE_HELPER).lower() == "on"
        return {
            "smart_enabled": enabled,
            "smart_reserve_soc": self._number(
                SMART_CHARGE_RESERVE_HELPER, 15.0
            ),
            "smart_buffer": self._number(SMART_CHARGE_BUFFER_HELPER, 20.0)
            / 100.0,
            "smart_min_price_difference": self._number(
                SMART_CHARGE_MIN_DIFFERENCE_HELPER, 2.0
            ) / 100.0,
        }

    def ct_grid_offset(self):
        """Offset voor de CT-spoel, ook als P1 de hoofdbron is."""
        if self._text("input_boolean.openems_netoffset_geinitialiseerd") != "on":
            return 0.0
        if self.grid_offset_mode() == "automatic":
            return self.automatic_grid_offset()
        return self.manual_grid_offset()

    # ---- grenzen en netsensoren van de load balancer -------------------
    def _limit(self, helper, default, bounds):
        if self._text(LB_INITIALIZED_HELPER) != "on":
            return float(default)
        value = self._number(helper, default=None)
        if value is None or not bounds[0] <= value <= bounds[1]:
            return float(default)
        return float(value)

    def max_import_w(self):
        """Bovengrens voor netimport (laden + woning), in W."""
        return self._limit(
            LB_MAX_IMPORT_HELPER, LB_DEFAULT_MAX_IMPORT_W, LB_IMPORT_RANGE_W
        )

    def max_export_w(self):
        """Bovengrens voor teruglevering aan het net, in W."""
        return self._limit(
            LB_MAX_EXPORT_HELPER, LB_DEFAULT_MAX_EXPORT_W, LB_EXPORT_RANGE_W
        )

    def lb_sensors(self):
        """(p1_aan, ct_aan); beide uit telt veilig als beide aan."""
        initialized = self._text(LB_INITIALIZED_HELPER) == "on"
        p1 = not (initialized and self._text(LB_P1_OFF_HELPER) == "on")
        ct = not (initialized and self._text(LB_CT_OFF_HELPER) == "on")
        if not p1 and not ct:
            return True, True
        return p1, ct

    def lb_preferred(self):
        return "CT" if self._text(LB_PREFERRED_HELPER).upper() == "CT" else "P1"

    def lb_both(self):
        return self._text(LB_BOTH_HELPER) == "on"

    def self_consumption_mode(self):
        value = self._text(SELF_CONSUMPTION_MODE_HELPER).lower()
        return "openems" if value == "openems" else "marstek"

    def fallback_enabled(self):
        return self._text(SELF_CONSUMPTION_FALLBACK_HELPER).lower() == "on"

    def grid_entity(self):
        if self.grid_sensor_source() == "p1":
            return self.grid_reference_entity()
        return self._text(GRID_POWER_ENTITY_HELPER)

    def grid_sensor_source(self):
        return (
            "p1"
            if self._text(GRID_SENSOR_SOURCE_HELPER).lower() == "p1"
            else "ct"
        )

    def grid_reference_entity(self):
        return self._text(GRID_REFERENCE_ENTITY_HELPER)

    def home_entity(self):
        return self._text(HOME_POWER_ENTITY_HELPER)

    def home_power_offset(self):
        return self._number(HOME_POWER_OFFSET_HELPER, 0.0)

    def home_power(self):
        entity = self.home_entity()
        value = self._number(entity, None) if entity else None
        if value is None:
            return None
        return value + self.home_power_offset()

    def grid_charging_allowed(self):
        return self._text(GRID_CHARGE_ALLOWED_HELPER).lower() != "off"

    def grid_export_allowed(self):
        return self._text(GRID_EXPORT_ALLOWED_HELPER).lower() != "off"

    def grid_export_mode(self):
        value = self._text(GRID_EXPORT_MODE_HELPER).lower()
        if value == "automatisch exporteren":
            return "automatic"
        if value == "uit":
            return "off"
        return "simulation"

    def dynamic_export_reserve_enabled(self):
        return (
            self.export_reserve_before_charge_mode() == "dynamic"
            or self.export_reserve_after_charge_mode() == "dynamic"
        )

    def export_reserve_before_charge_mode(self):
        value = self._text(EXPORT_BEFORE_CHARGE_MODE_HELPER).lower()
        return "dynamic" if value == "dynamisch" else "fixed"

    def export_reserve_after_charge_mode(self):
        value = self._text(EXPORT_RESERVE_MODE_HELPER).lower()
        if not value:
            enabled = (
                self._text(DYNAMIC_EXPORT_RESERVE_HELPER).lower() == "on"
            )
            return "dynamic" if enabled else "fixed"
        return "dynamic" if value == "dynamisch" else "fixed"

    def fixed_export_reserve_before_charge_kwh(self):
        return self._number(
            EXPORT_BEFORE_CHARGE_RESERVE_KWH_HELPER, 8.0
        )

    def fixed_export_reserve_after_charge_kwh(self):
        return self._number(EXPORT_FIXED_RESERVE_KWH_HELPER, 8.5)

    def dynamic_export_reserve_kwh(self):
        return self._number(EXPORT_DYNAMIC_RESERVE_KWH_HELPER, 2.0)

    def fixed_export_reserve_kwh(self):
        """Achterwaarts compatibele naam voor Reserve na laden."""
        return self.fixed_export_reserve_after_charge_kwh()

    def frank_market_entity(self):
        return self._text(FRANK_MARKET_ENTITY_HELPER)

    def grid_export_price(self):
        entity = self.frank_market_entity()
        market_price = self._number(entity, None) if entity else None
        if market_price is None:
            return None
        return market_price + self._number(
            HELPERS["grid_export_return_fee"], 0.0182
        )

    def frank_all_in_entity(self):
        return self._text(FRANK_ALL_IN_ENTITY_HELPER) or PRICE_SENSOR

    def effective_efficiencies(self):
        charge_mode = self._efficiency_mode(CHARGE_EFFICIENCY_MODE_HELPER)
        discharge_mode = self._efficiency_mode(
            DISCHARGE_EFFICIENCY_MODE_HELPER
        )
        charge = []
        discharge = []
        details = []
        for index, _battery in enumerate(BATTERIES, start=1):
            charge_value, charge_source = self._direction_efficiency(
                index, "laden", charge_mode,
                MANUAL_CHARGE_EFFICIENCY_HELPER,
            )
            discharge_value, discharge_source = self._direction_efficiency(
                index, "ontladen", discharge_mode,
                MANUAL_DISCHARGE_EFFICIENCY_HELPER,
            )
            charge.append(charge_value)
            discharge.append(discharge_value)
            details.append({
                "index": index - 1,
                "charge": charge_value,
                "discharge": discharge_value,
                "charge_source": charge_source,
                "discharge_source": discharge_source,
            })
        return charge, discharge, details

    def export_efficiency(self):
        mode = self._efficiency_mode(EXPORT_EFFICIENCY_MODE_HELPER)
        if mode == "none":
            return 1.0, "geen rendement"
        if mode == "manual":
            value = self._number(MANUAL_EXPORT_EFFICIENCY_HELPER, 100.0)
            return self._bounded_efficiency(value), "handmatig"
        learned = []
        for index in range(1, len(BATTERIES) + 1):
            charge = self._learned_efficiency(index, "laden")
            discharge = self._learned_efficiency(index, "ontladen")
            if charge is None or discharge is None:
                return 1.0, "zelfgeleerd: nog onvoldoende gegevens"
            learned.append(charge * discharge)
        return sum(learned) / len(learned), "zelfgeleerd"

    def _direction_efficiency(self, index, direction, mode, manual_helper):
        if mode == "none":
            return 1.0, "geen rendement"
        if mode == "manual":
            value = self._number(manual_helper, 100.0)
            return self._bounded_efficiency(value), "handmatig"
        learned = self._learned_efficiency(index, direction)
        if learned is None:
            return 1.0, "zelfgeleerd: nog onvoldoende gegevens"
        return learned, "zelfgeleerd"

    def _learned_efficiency(self, index, direction):
        entity_id = (
            f"sensor.openems_accu_{index}_zelfgeleerd_rendement"
        )
        state = self.app.get_state(entity_id, attribute="all")
        if not isinstance(state, dict):
            return None
        values = state.get("attributes", {}).get(direction, {})
        if values.get("betrouwbaarheid") != "hoog":
            return None
        try:
            return self._bounded_efficiency(values["rendement_pct"])
        except (KeyError, TypeError, ValueError):
            return None

    def _efficiency_mode(self, helper):
        value = self._text(helper).lower()
        if value == "handmatig rendement":
            return "manual"
        if value == "zelfgeleerd rendement":
            return "learned"
        return "none"

    @staticmethod
    def _bounded_efficiency(percent):
        value = float(percent)
        if value <= 1:
            return min(1.0, max(0.60, value))
        return min(1.0, max(0.60, value / 100))

    def grid_import_sign(self):
        value = self._text(GRID_POWER_SIGN_HELPER).lower()
        return -1.0 if "teruglevering" in value else 1.0

    def grid_offset_mode(self):
        value = self._text(GRID_OFFSET_MODE_HELPER).lower()
        return "automatic" if value == "automatisch" else "manual"

    def manual_grid_offset(self):
        return self._number(GRID_MANUAL_OFFSET_HELPER, 0.0)

    def automatic_grid_offset(self):
        return self._number(GRID_AUTOMATIC_OFFSET_HELPER, 0.0)

    def effective_grid_offset(self):
        if self.grid_sensor_source() == "p1":
            return 0.0
        if (
            self._text("input_boolean.openems_netoffset_geinitialiseerd")
            != "on"
        ):
            return 0.0
        if self.grid_offset_mode() == "automatic":
            return self.automatic_grid_offset()
        return self.manual_grid_offset()

    def validate(self):
        """Geeft alle ontbrekende of ongeldige verplichte entiteiten terug."""
        problems = []
        required = [("P1/netvermogen", self.grid_entity())]
        for index, battery in enumerate(self.batteries()):
            if self.battery_disabled(index):
                # Een bewust uitgeschakelde accu mag uit staan of offline zijn.
                continue
            for field in (
                "soc", "actual_power", "max_charge_power",
                "max_discharge_power", "charge_power", "discharge_power",
                "rs485_control", "work_mode", "force_mode",
            ):
                required.append((f"{battery['name']} {field}", battery[field]))

        for label, entity_id in required:
            if not entity_id:
                problems.append(f"{label}: niet ingesteld")
                continue
            state = self.app.get_state(entity_id)
            if state in (None, "unknown", "unavailable"):
                problems.append(f"{label}: {entity_id} is niet beschikbaar")
        return problems

    def _text(self, entity_id):
        value = self.app.get_state(entity_id)
        if value in (None, "unknown", "unavailable"):
            return ""
        return str(value).strip()

    def _number(self, entity_id, default=0.0):
        try:
            return float(self._text(entity_id))
        except (TypeError, ValueError):
            return None if default is None else float(default)
