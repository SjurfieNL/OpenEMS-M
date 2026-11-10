"""Blijvend beheer van fabrieks- en persoonlijke OpenEMS-instellingen."""

import json
from pathlib import Path

from ems_config import (
    BATTERY_DISTRIBUTION_HELPER,
    BATTERIES,
    CT_GRID_SENSOR_DEFAULT,
    CT_HOME_SENSOR_DEFAULT,
    P1_SENSOR_DEFAULT,
    BATTERY_ENTITY_FIELDS,
    BATTERY_ENTITY_HELPERS,
    DEFAULTS,
    EMS_ENABLED_HELPER,
    GRID_POWER_ENTITY_HELPER,
    GRID_DISPLAY_ENTITY_HELPER,
    GRID_REFERENCE_ENTITY_HELPER,
    GRID_POWER_SIGN_HELPER,
    GRID_SENSOR_SOURCE_HELPER,
    LB_BOTH_HELPER,
    LB_CT_OFF_HELPER,
    LB_DEFAULT_MAX_EXPORT_W,
    LB_DEFAULT_MAX_IMPORT_W,
    LB_INITIALIZED_HELPER,
    LB_MAX_EXPORT_HELPER,
    LB_MAX_IMPORT_HELPER,
    LB_P1_OFF_HELPER,
    LB_PREFERRED_HELPER,
    GRID_OFFSET_ONCE_INITIALIZED_HELPER,
    PRICE_FORECAST_DAYS_HELPER,
    PRICE_FORECAST_DEFAULT_DAYS,
    PRICE_FORECAST_DEFAULT_MARGIN_CT,
    PRICE_FORECAST_MARGIN_HELPER,
    PRICE_FORECAST_DEFAULT_ENTITY,
    PRICE_FORECAST_DEFAULT_FLOOR_SOC,
    PRICE_FORECAST_DEFAULT_MIN_DIFF_CT,
    PRICE_FORECAST_ENTITY_HELPER,
    PRICE_FORECAST_FLOOR_HELPER,
    PRICE_FORECAST_HELPER,
    PRICE_FORECAST_INITIALIZED_HELPER,
    PRICE_FORECAST_MIN_DIFF_HELPER,
    GRID_OFFSET_MODE_HELPER,
    GRID_MANUAL_OFFSET_HELPER,
    GRID_AUTOMATIC_OFFSET_HELPER,
    GRID_CHARGE_ALLOWED_HELPER,
    GRID_EXPORT_ALLOWED_HELPER,
    GRID_EXPORT_INITIALIZED_HELPER,
    GRID_EXPORT_PRICING_INITIALIZED_HELPER,
    GRID_EXPORT_MODE_HELPER,
    FRANK_ALL_IN_ENTITY_HELPER,
    FRANK_MARKET_ENTITY_HELPER,
    HOME_POWER_ENTITY_HELPER,
    MANUAL_CHARGE_POWER_HELPER,
    MANUAL_DISCHARGE_POWER_HELPER,
    MANUAL_MODE_HELPER,
    LEGACY_MARKET_SENSORS,
    LEGACY_PRICE_SENSORS,
    MARKET_PRICE_SENSOR_CANDIDATES,
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
    SOLAR_POWER_ENTITY_HELPER,
)


class SettingsManager:
    SAVE_BUTTON = "input_button.openems_huidige_instellingen_opslaan"
    RESTORE_BUTTON = "input_button.openems_eigen_standaard_herstellen"
    FACTORY_BUTTON = "input_button.openems_fabrieksinstellingen_herstellen"
    INITIALIZED_HELPER = "input_boolean.openems_instellingen_geinitialiseerd"
    OFFSET_INITIALIZED_HELPER = (
        "input_boolean.openems_netoffset_geinitialiseerd"
    )
    GRID_DISPLAY_INITIALIZED_HELPER = (
        "input_boolean.openems_gridweergave_geinitialiseerd"
    )
    ECONOMY_INITIALIZED_HELPER = (
        "input_boolean.openems_economie_geinitialiseerd"
    )
    EXPORT_PLANNER_INITIALIZED_HELPER = (
        "input_boolean.openems_export_planner_geinitialiseerd"
    )
    EFFICIENCY_CHOICES_INITIALIZED_HELPER = (
        "input_boolean.openems_rendement_keuzes_geinitialiseerd"
    )
    QUICK_SWITCHES_INITIALIZED_HELPER = (
        "input_boolean.openems_snelle_schakelaars_geinitialiseerd"
    )
    SPLIT_EXPORT_RESERVE_INITIALIZED_HELPER = (
        "input_boolean.openems_gescheiden_exportreserve_geinitialiseerd"
    )
    SMART_CHARGE_INITIALIZED_HELPER = (
        "input_boolean.openems_slim_laden_geinitialiseerd"
    )
    STATUS_SENSOR = "sensor.openems_instellingenbeheer"

    def __init__(self, app):
        self.app = app
        self.data_file = (
            Path(__file__).resolve().parent.parent
            / "OpenEMS-M-data"
            / "persoonlijke_standaard.json"
        )
        self.factory_settings = self._factory_settings()

    def start(self):
        self.app.listen_state(self._save_pressed, self.SAVE_BUTTON)
        self.app.listen_state(self._restore_pressed, self.RESTORE_BUTTON)
        self.app.listen_state(self._factory_pressed, self.FACTORY_BUTTON)
        self.app.run_in(self._initialize, 2)
        self._publish(
            "gereed",
            "Instellingenbeheer is gereed",
            persoonlijke_standaard=self.data_file.exists(),
        )

    def _initialize(self, kwargs=None):
        self._initialize_frank_and_efficiency()
        self._initialize_grid_offset()
        self._initialize_grid_display()
        self._initialize_economy()
        self._migrate_time_resolution()
        self._migrate_marstek_modbus_entities()
        self._initialize_grid_export()
        self._initialize_export_planner()
        self._initialize_grid_export_pricing()
        self._initialize_efficiency_choices()
        self._initialize_quick_switches()
        self._initialize_split_export_reserve()
        self._initialize_smart_charge()
        self._initialize_load_balancer()
        self._initialize_offset_once()
        self._initialize_price_forecast()
        if self.app.get_state(self.INITIALIZED_HELPER) == "on":
            return
        grid_entity = self._state(GRID_POWER_ENTITY_HELPER)
        start_hour = self._state("input_number.openems_laden_vanaf")
        end_hour = self._state("input_number.openems_laden_tot")
        first_install = (
            not grid_entity
            and start_hour in (None, "0", "0.0")
            and end_hour in (None, "1", "1.0")
            and not self._state(BATTERY_ENTITY_HELPERS[(0, "soc")])
        )
        if first_install:
            self._apply(self.factory_settings)
            message = "Eerste installatie: fabrieksinstellingen toegepast"
        else:
            message = "Bestaande instellingen behouden"
        self.app.call_service(
            "input_boolean/turn_on",
            entity_id=self.INITIALIZED_HELPER,
        )
        self._publish("geïnitialiseerd", message)

    def _migrate_time_resolution(self):
        entity_id = "input_select.openems_ontlaad_tijdresolutie"
        if self._state(entity_id) not in ("15 minuten", "60 minuten"):
            self._set(entity_id, "60 minuten")
            self.app.log(
                "[Instellingen] Oude automatische tijdresolutie "
                "gemigreerd naar 60 minuten"
            )

    def _migrate_marstek_modbus_entities(self):
        """Vertaal uitsluitend bekende oude standaard-ID's naar v0.13."""
        old_suffixes = {
            "soc": "battery_soc",
            "capacity": "battery_total_energy",
            "actual_power": "ac_power",
            "max_charge_power": "max_charge_power",
            "max_discharge_power": "max_discharge_power",
            "charge_power": "set_charge_power",
            "discharge_power": "set_discharge_power",
            "rs485_control": "rs485_control_mode",
            "work_mode": "user_work_mode",
            "force_mode": "force_mode",
            "max_cell_voltage": "max_cell_voltage",
            "min_cell_voltage": "min_cell_voltage",
            "temperature": "internal_temperature",
            "alarm_status": "alarm_status",
            "fault_status": "fault_status",
            "cycles": "battery_cycle_count",
            "total_charging_energy": "total_charging_energy",
            "total_discharging_energy": "total_discharging_energy",
        }
        domains = {
            "max_charge_power": "number",
            "max_discharge_power": "number",
            "charge_power": "number",
            "discharge_power": "number",
            "rs485_control": "switch",
            "work_mode": "select",
            "force_mode": "select",
        }
        migrated = 0
        for index, battery in enumerate(BATTERIES):
            number = index + 1
            for field, suffix in old_suffixes.items():
                helper = BATTERY_ENTITY_HELPERS[(index, field)]
                domain = domains.get(field, "sensor")
                old_entity = f"{domain}.accu{number}_{suffix}"
                if self._state(helper) != old_entity:
                    continue
                self._set(helper, battery.get(field, ""))
                migrated += 1
        if migrated:
            self.app.log(
                "[Instellingen] "
                f"{migrated} oude Marstek-entiteitskoppelingen "
                "veilig gemigreerd naar marstek_modbus v0.13"
            )

    def _initialize_quick_switches(self):
        if self.app.get_state(
            self.QUICK_SWITCHES_INITIALIZED_HELPER
        ) == "on":
            return
        self._set(GRID_CHARGE_ALLOWED_HELPER, "on")
        self._set(GRID_EXPORT_ALLOWED_HELPER, "on")
        self._set(GRID_SENSOR_SOURCE_HELPER, "CT")
        self._set(
            EXPORT_RESERVE_MODE_HELPER,
            "Dynamisch"
            if self._state(DYNAMIC_EXPORT_RESERVE_HELPER) == "on"
            else "Vast",
        )
        self._set(HOME_POWER_OFFSET_HELPER, 0)
        self._set(self.QUICK_SWITCHES_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Nieuwe snelle schakelaars veilig toegevoegd"
        )

    def _initialize_split_export_reserve(self):
        """Voeg de voor-laadreserve toe en behoud de bestaande na-reserve."""
        if self.app.get_state(
            self.SPLIT_EXPORT_RESERVE_INITIALIZED_HELPER
        ) == "on":
            return
        self._set(EXPORT_BEFORE_CHARGE_MODE_HELPER, "Vast")
        self._set(EXPORT_BEFORE_CHARGE_RESERVE_KWH_HELPER, 8.0)
        self._set(EXPORT_DYNAMIC_RESERVE_KWH_HELPER, 2.0)
        self._set(self.SPLIT_EXPORT_RESERVE_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Reserve voor laden toegevoegd op 8,0 kWh; "
            "bestaande Reserve na laden is behouden"
        )

    def _initialize_smart_charge(self):
        """Eenmalige startwaarden voor slim laden en handmatige bediening."""
        if self.app.get_state(self.SMART_CHARGE_INITIALIZED_HELPER) == "on":
            return
        self._set("input_number.openems_slim_laden_reserve_soc", 15)
        self._set("input_number.openems_slim_laden_buffer", 20)
        self._set("input_number.openems_slim_laden_min_verschil", 2)
        self._set("input_number.openems_handmatig_laadvermogen_totaal", 500)
        self._set("input_number.openems_handmatig_ontlaadvermogen_totaal", 500)
        self._set("input_select.openems_handmatig_verdeling", "Gelijk")
        self._set(self.SMART_CHARGE_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Slim laden (reserve 15%, buffer 20%, 2 ct) "
            "en handmatige startwaarden gezet"
        )

    def _initialize_load_balancer(self):
        """Eenmalige startwaarden voor de instelbare load-balancergrenzen."""
        if self.app.get_state(LB_INITIALIZED_HELPER) == "on":
            return
        self._set(LB_MAX_IMPORT_HELPER, LB_DEFAULT_MAX_IMPORT_W)
        self._set(LB_MAX_EXPORT_HELPER, LB_DEFAULT_MAX_EXPORT_W)
        self._set(LB_P1_OFF_HELPER, "off")
        self._set(LB_CT_OFF_HELPER, "off")
        self._set(LB_PREFERRED_HELPER, "P1")
        self._set(LB_BOTH_HELPER, "off")
        self._set(LB_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Load balancer: importgrens "
            f"{LB_DEFAULT_MAX_IMPORT_W:.0f} W, exportgrens "
            f"{LB_DEFAULT_MAX_EXPORT_W:.0f} W, P1 met CT als reserve"
        )

    def _initialize_price_forecast(self):
        """Startwaarden prijsvoorspelling; staat na installatie uit."""
        if self.app.get_state(PRICE_FORECAST_INITIALIZED_HELPER) == "on":
            return
        self._set(PRICE_FORECAST_HELPER, "off")
        self._set(PRICE_FORECAST_ENTITY_HELPER, PRICE_FORECAST_DEFAULT_ENTITY)
        self._set(
            PRICE_FORECAST_MIN_DIFF_HELPER, PRICE_FORECAST_DEFAULT_MIN_DIFF_CT
        )
        self._set(
            PRICE_FORECAST_FLOOR_HELPER, PRICE_FORECAST_DEFAULT_FLOOR_SOC
        )
        self._set(PRICE_FORECAST_DAYS_HELPER, PRICE_FORECAST_DEFAULT_DAYS)
        self._set(
            PRICE_FORECAST_MARGIN_HELPER, PRICE_FORECAST_DEFAULT_MARGIN_CT
        )
        self._set(PRICE_FORECAST_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Prijsvoorspelling: uit (zet aan in het paneel), "
            "verschil 5 ct, ondergrens 30%, 2 dagen vooruit"
        )

    def _initialize_offset_once(self):
        """Offset wordt eenmalig gekalibreerd en daarna vastgehouden.

        De tot nu toe automatisch bijgestelde waarde wordt de vaste
        handmatige offset, zodat het gedrag bij de overstap gelijk blijft.
        """
        if self.app.get_state(GRID_OFFSET_ONCE_INITIALIZED_HELPER) == "on":
            return
        if self._state(GRID_OFFSET_MODE_HELPER) == "Automatisch":
            try:
                current = float(self._state(GRID_AUTOMATIC_OFFSET_HELPER))
            except (TypeError, ValueError):
                current = 0.0
            self._set(GRID_MANUAL_OFFSET_HELPER, round(current))
            self._set(GRID_OFFSET_MODE_HELPER, "Handmatig")
            self.app.log(
                f"[Instellingen] Netoffset {round(current):+d} W vastgezet "
                "als handmatige offset (eenmalige kalibratie)"
            )
        self._set(GRID_OFFSET_ONCE_INITIALIZED_HELPER, "on")

    def _initialize_grid_offset(self):
        if self.app.get_state(self.OFFSET_INITIALIZED_HELPER) == "on":
            return
        self._set(
            GRID_REFERENCE_ENTITY_HELPER,
            P1_SENSOR_DEFAULT,
        )
        self._set(GRID_OFFSET_MODE_HELPER, "Handmatig")
        self._set(GRID_MANUAL_OFFSET_HELPER, 0)
        self._set(GRID_AUTOMATIC_OFFSET_HELPER, 0)
        self._set(self.OFFSET_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Netoffset ingesteld op handmatig (0 W); "
            "kalibreer eenmalig met de knop"
        )

    def _initialize_grid_display(self):
        if self.app.get_state(
            self.GRID_DISPLAY_INITIALIZED_HELPER
        ) == "on":
            return
        self._set(
            GRID_DISPLAY_ENTITY_HELPER,
            P1_SENSOR_DEFAULT,
        )
        self._set(self.GRID_DISPLAY_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Gridweergave ingesteld op P1 Netto Vermogen"
        )

    def _initialize_economy(self):
        if self.app.get_state(self.ECONOMY_INITIALIZED_HELPER) == "on":
            return
        self._set(
            "input_number.openems_minimale_ontlaadblokduur",
            DEFAULTS["minimum_discharge_block"],
        )
        self._set(
            "input_select.openems_ontlaad_tijdresolutie", "60 minuten"
        )
        self._set(self.ECONOMY_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Economische ontlaadinstellingen toegevoegd"
        )

    def _initialize_grid_export(self):
        if self.app.get_state(GRID_EXPORT_INITIALIZED_HELPER) == "on":
            return
        self._set(GRID_EXPORT_MODE_HELPER, "Alleen simuleren")
        self._set(
            "input_number.openems_minimale_exportwinst_per_kwh",
            DEFAULTS["grid_export_selection_margin"],
        )
        self._set(
            "input_number.openems_maximaal_exportvermogen",
            DEFAULTS["grid_export_max_power"],
        )
        self._set(
            "input_number.openems_export_veiligheidsbuffer_soc",
            DEFAULTS["grid_export_buffer_soc"],
        )
        self._set(GRID_EXPORT_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Grid Export veilig op Alleen simuleren ingesteld"
        )

    def _initialize_grid_export_pricing(self):
        """Scheid de exportprijs eenmalig van de zelfconsumptiemarge."""
        if self.app.get_state(
            GRID_EXPORT_PRICING_INITIALIZED_HELPER
        ) == "on":
            return
        self._set(
            "input_number.openems_minimaal_prijsverschil_grid_export",
            DEFAULTS["grid_export_minimum_difference"],
        )
        self._set(
            "input_number.openems_frank_terugleververgoeding",
            DEFAULTS["grid_export_return_fee"],
        )
        self._set(GRID_EXPORT_PRICING_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Grid Export-prijsmodel gescheiden van "
            "zelfconsumptie"
        )

    def _initialize_export_planner(self):
        """Voer de eenmalige, veilige migratie naar de nieuwe planner uit."""
        if self.app.get_state(
            self.EXPORT_PLANNER_INITIALIZED_HELPER
        ) == "on":
            return
        self._set(GRID_EXPORT_MODE_HELPER, "Alleen simuleren")
        self._set(
            "input_number.marstek_minimaal_prijsverschil_ontladen",
            DEFAULTS["discharge_margin"],
        )
        self._set(
            "input_number.openems_minimale_exportwinst_per_kwh",
            DEFAULTS["grid_export_selection_margin"],
        )
        self._set(
            "input_number.openems_export_veiligheidsbuffer_soc",
            DEFAULTS["grid_export_buffer_soc"],
        )
        self._set(
            "input_number.openems_maximaal_exportvermogen",
            DEFAULTS["grid_export_max_power"],
        )
        self._set(self.EXPORT_PLANNER_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Nieuwe Export Planner veilig gemigreerd naar "
            "Alleen simuleren; 5000 W harde netgrens actief"
        )

    def _initialize_frank_and_efficiency(self):
        defaults = {
            FRANK_ALL_IN_ENTITY_HELPER: PRICE_SENSOR,
            FRANK_MARKET_ENTITY_HELPER: MARKET_PRICE_SENSOR_CANDIDATES[0],
        }
        for entity_id, value in defaults.items():
            if not self._state(entity_id):
                self._set(entity_id, value)
        self._migrate_frank_entities()

    def _entity_exists(self, entity_id):
        try:
            return self.app.get_state(entity_id) is not None
        except Exception:
            return False

    def _migrate_frank_entities(self):
        """Zet een oude Frank-entiteitsnaam om naar de nieuwe, als die bestaat."""
        moves = (
            (FRANK_ALL_IN_ENTITY_HELPER, LEGACY_PRICE_SENSORS,
             (PRICE_SENSOR,)),
            (FRANK_MARKET_ENTITY_HELPER, LEGACY_MARKET_SENSORS,
             MARKET_PRICE_SENSOR_CANDIDATES),
        )
        for helper, legacy, candidates in moves:
            current = self._state(helper)
            if current and current not in legacy:
                continue
            if current and self._entity_exists(current):
                # De oude entiteit bestaat nog en levert nog; niets wijzigen.
                state = self.app.get_state(current)
                if state not in ("unavailable", "unknown"):
                    continue
            target = next(
                (name for name in candidates if self._entity_exists(name)),
                None,
            )
            if target is None:
                self.app.log(
                    f"[Instellingen] Nieuwe Frank-entiteit voor {helper} "
                    "niet gevonden; oude naam blijft staan",
                    level="WARNING",
                )
                continue
            self._set(helper, target)
            manager = getattr(
                self.app,
                "price_manager"
                if helper == FRANK_ALL_IN_ENTITY_HELPER
                else "export_price_manager",
                None,
            )
            if manager is not None:
                manager.sensor = target
            self.app.log(
                f"[Instellingen] Frank-entiteit omgezet naar {target}"
            )

    def _initialize_efficiency_choices(self):
        if self.app.get_state(
            self.EFFICIENCY_CHOICES_INITIALIZED_HELPER
        ) == "on":
            return
        # Veilig en volledig transparant migreren: geen enkele planner past
        # na de upgrade stilzwijgend een rendementsaanname toe.
        self._set(CHARGE_EFFICIENCY_MODE_HELPER, "Geen rendement")
        self._set(DISCHARGE_EFFICIENCY_MODE_HELPER, "Geen rendement")
        self._set(EXPORT_EFFICIENCY_MODE_HELPER, "Geen rendement")
        self._set(
            MANUAL_CHARGE_EFFICIENCY_HELPER,
            DEFAULTS["manual_charge_efficiency"],
        )
        self._set(
            MANUAL_DISCHARGE_EFFICIENCY_HELPER,
            DEFAULTS["manual_discharge_efficiency"],
        )
        self._set(
            MANUAL_EXPORT_EFFICIENCY_HELPER,
            DEFAULTS["manual_export_efficiency"],
        )
        self._set(self.EFFICIENCY_CHOICES_INITIALIZED_HELPER, "on")
        self.app.log(
            "[Instellingen] Rendementkeuzes veilig gemigreerd naar Geen "
            "rendement; zelflerende meting blijft passief actief"
        )

    def _save_pressed(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        values = {
            entity_id: self.app.get_state(entity_id)
            for entity_id in self.factory_settings
            if entity_id not in {EMS_ENABLED_HELPER, MANUAL_MODE_HELPER}
            and self._state(entity_id) is not None
        }
        if values.get(GRID_EXPORT_MODE_HELPER) == "Automatisch exporteren":
            values[GRID_EXPORT_MODE_HELPER] = "Alleen simuleren"
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.data_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(values, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.data_file)
        self._publish(
            "opgeslagen",
            f"{len(values)} huidige instellingen zijn als persoonlijke "
            "standaard opgeslagen",
            persoonlijke_standaard=True,
        )

    def _restore_pressed(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        if not self.data_file.exists():
            self._publish(
                "niet beschikbaar",
                "Er is nog geen persoonlijke standaard opgeslagen",
                level="WARNING",
                persoonlijke_standaard=False,
            )
            return
        try:
            values = json.loads(self.data_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            self._publish(
                "fout",
                f"Persoonlijke standaard kon niet worden gelezen: {error}",
                level="ERROR",
            )
            return
        self._apply(values)
        self._publish(
            "hersteld",
            f"{len(values)} persoonlijke standaardinstellingen hersteld",
            persoonlijke_standaard=True,
        )

    def _factory_pressed(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        self._apply(self.factory_settings)
        self._publish(
            "fabrieksinstellingen",
            "OpenEMS-fabrieksinstellingen zijn hersteld",
            persoonlijke_standaard=self.data_file.exists(),
        )

    def _apply(self, values):
        for entity_id, value in values.items():
            if entity_id in self.factory_settings:
                if (
                    entity_id == GRID_EXPORT_MODE_HELPER
                    and value == "Automatisch exporteren"
                ):
                    value = "Alleen simuleren"
                if (
                    entity_id
                    == "input_select.openems_ontlaad_tijdresolutie"
                    and value not in ("15 minuten", "60 minuten")
                ):
                    value = "60 minuten"
                self._set(entity_id, value)

    def _set(self, entity_id, value):
        domain = entity_id.split(".", 1)[0]
        if domain == "input_number":
            self.app.call_service(
                "input_number/set_value",
                entity_id=entity_id,
                value=float(value),
            )
        elif domain == "input_select":
            self.app.call_service(
                "input_select/select_option",
                entity_id=entity_id,
                option=str(value),
            )
        elif domain == "input_text":
            self.app.call_service(
                "input_text/set_value",
                entity_id=entity_id,
                value=str(value),
            )
        elif domain == "input_boolean":
            service = "turn_on" if str(value).lower() == "on" else "turn_off"
            self.app.call_service(
                f"input_boolean/{service}", entity_id=entity_id
            )

    def _publish(self, state, message, level="INFO", **attributes):
        self.app.set_state(
            self.STATUS_SENSOR,
            state=state,
            attributes={
                "friendly_name": "OpenEMS instellingenbeheer",
                "icon": "mdi:content-save-cog",
                "melding": message,
                "niveau": level,
                "opslagbestand": str(self.data_file),
                **attributes,
            },
            replace=True,
        )
        self.app.log(f"[Instellingen] {message}", level=level)

    def _state(self, entity_id):
        value = self.app.get_state(entity_id)
        if value in (None, "", "unknown", "unavailable"):
            return None
        return str(value)

    @staticmethod
    def _factory_settings():
        values = {
            EMS_ENABLED_HELPER: "off",
            GRID_CHARGE_ALLOWED_HELPER: "on",
            GRID_EXPORT_ALLOWED_HELPER: "on",
            SELF_CONSUMPTION_FALLBACK_HELPER: "on",
            MANUAL_MODE_HELPER: "Stop",
            SELF_CONSUMPTION_MODE_HELPER: "Marstek",
            BATTERY_DISTRIBUTION_HELPER: "Alle accu’s verdelen",
            GRID_POWER_SIGN_HELPER: "Positief is afname",
            GRID_SENSOR_SOURCE_HELPER: "CT",
            GRID_REFERENCE_ENTITY_HELPER: P1_SENSOR_DEFAULT,
            GRID_DISPLAY_ENTITY_HELPER: P1_SENSOR_DEFAULT,
            GRID_OFFSET_MODE_HELPER: "Handmatig",
            GRID_MANUAL_OFFSET_HELPER: 0,
            GRID_AUTOMATIC_OFFSET_HELPER: 0,
            GRID_OFFSET_ONCE_INITIALIZED_HELPER: "on",
            SettingsManager.OFFSET_INITIALIZED_HELPER: "on",
            SettingsManager.GRID_DISPLAY_INITIALIZED_HELPER: "on",
            SettingsManager.ECONOMY_INITIALIZED_HELPER: "on",
            SettingsManager.EXPORT_PLANNER_INITIALIZED_HELPER: "on",
            GRID_EXPORT_PRICING_INITIALIZED_HELPER: "on",
            GRID_EXPORT_INITIALIZED_HELPER: "on",
            GRID_EXPORT_MODE_HELPER: "Alleen simuleren",
            DYNAMIC_EXPORT_RESERVE_HELPER: "off",
            EXPORT_RESERVE_MODE_HELPER: "Vast",
            EXPORT_FIXED_RESERVE_KWH_HELPER: 8.5,
            EXPORT_BEFORE_CHARGE_MODE_HELPER: "Vast",
            EXPORT_BEFORE_CHARGE_RESERVE_KWH_HELPER: 8.0,
            EXPORT_DYNAMIC_RESERVE_KWH_HELPER: 2.0,
            SettingsManager.SPLIT_EXPORT_RESERVE_INITIALIZED_HELPER: "on",
            "input_number.openems_slim_laden_reserve_soc": 15,
            "input_number.openems_slim_laden_buffer": 20,
            "input_number.openems_slim_laden_min_verschil": 2,
            SettingsManager.SMART_CHARGE_INITIALIZED_HELPER: "on",
            PRICE_FORECAST_HELPER: "off",
            PRICE_FORECAST_ENTITY_HELPER: PRICE_FORECAST_DEFAULT_ENTITY,
            PRICE_FORECAST_MIN_DIFF_HELPER: PRICE_FORECAST_DEFAULT_MIN_DIFF_CT,
            PRICE_FORECAST_FLOOR_HELPER: PRICE_FORECAST_DEFAULT_FLOOR_SOC,
            PRICE_FORECAST_DAYS_HELPER: PRICE_FORECAST_DEFAULT_DAYS,
            PRICE_FORECAST_MARGIN_HELPER: PRICE_FORECAST_DEFAULT_MARGIN_CT,
            PRICE_FORECAST_INITIALIZED_HELPER: "on",
            LB_MAX_IMPORT_HELPER: LB_DEFAULT_MAX_IMPORT_W,
            LB_MAX_EXPORT_HELPER: LB_DEFAULT_MAX_EXPORT_W,
            LB_P1_OFF_HELPER: "off",
            LB_CT_OFF_HELPER: "off",
            LB_PREFERRED_HELPER: "P1",
            LB_BOTH_HELPER: "off",
            LB_INITIALIZED_HELPER: "on",
            HOME_POWER_OFFSET_HELPER: 0,
            SettingsManager.EFFICIENCY_CHOICES_INITIALIZED_HELPER: "on",
            CHARGE_EFFICIENCY_MODE_HELPER: "Geen rendement",
            DISCHARGE_EFFICIENCY_MODE_HELPER: "Geen rendement",
            EXPORT_EFFICIENCY_MODE_HELPER: "Geen rendement",
            MANUAL_CHARGE_EFFICIENCY_HELPER: DEFAULTS[
                "manual_charge_efficiency"
            ],
            MANUAL_DISCHARGE_EFFICIENCY_HELPER: DEFAULTS[
                "manual_discharge_efficiency"
            ],
            MANUAL_EXPORT_EFFICIENCY_HELPER: DEFAULTS[
                "manual_export_efficiency"
            ],
            FRANK_ALL_IN_ENTITY_HELPER: PRICE_SENSOR,
            FRANK_MARKET_ENTITY_HELPER: MARKET_PRICE_SENSOR_CANDIDATES[0],
            "input_select.openems_ontlaad_tijdresolutie": "60 minuten",
            "input_number.openems_minimale_ontlaadblokduur": DEFAULTS[
                "minimum_discharge_block"
            ],
            "input_number.openems_minimale_exportwinst_per_kwh": DEFAULTS[
                "grid_export_selection_margin"
            ],
            "input_number.openems_minimaal_prijsverschil_grid_export": DEFAULTS[
                "grid_export_minimum_difference"
            ],
            "input_number.openems_frank_terugleververgoeding": DEFAULTS[
                "grid_export_return_fee"
            ],
            "input_number.openems_maximaal_exportvermogen": DEFAULTS[
                "grid_export_max_power"
            ],
            "input_number.openems_export_veiligheidsbuffer_soc": DEFAULTS[
                "grid_export_buffer_soc"
            ],
            MANUAL_CHARGE_POWER_HELPER: 500,
            MANUAL_DISCHARGE_POWER_HELPER: 500,
            "input_number.marstek_doel_soc": DEFAULTS["target_soc"],
            "input_number.marstek_minimum_soc": DEFAULTS["minimum_soc"],
            "input_number.marstek_prijsmarge": DEFAULTS["charge_margin"],
            "input_number.marstek_minimaal_prijsverschil_ontladen": (
                DEFAULTS["discharge_margin"]
            ),
            "input_number.openems_laden_vanaf": DEFAULTS[
                "charge_window_start"
            ],
            "input_number.openems_laden_tot": DEFAULTS["charge_window_end"],
            "input_number.openems_minimaal_laadvermogen_per_accu": DEFAULTS[
                "minimum_charge_power"
            ],
            HOME_POWER_ENTITY_HELPER: CT_HOME_SENSOR_DEFAULT,
            GRID_POWER_ENTITY_HELPER: CT_GRID_SENSOR_DEFAULT,
            SOLAR_POWER_ENTITY_HELPER: "",
        }
        for index, battery in enumerate(BATTERIES):
            for field in BATTERY_ENTITY_FIELDS:
                values[BATTERY_ENTITY_HELPERS[(index, field)]] = battery[
                    field
                ]
        return values
