"""Hoofdapp voor OpenEMS-M met gescheiden runtime-taken."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import appdaemon.plugins.hass.hassapi as hass

from battery_manager import BatteryManager
from battery_health_manager import BatteryHealthManager
from charge_planner import ChargePlanner
from coordinated_self_consumption import CoordinatedSelfConsumption
from controller_test import ControllerTestMode
from dashboard_status import DashboardStatus
from load_balancer import LoadBalancer
from discharge_planner import DischargePlanner
from self_consumption_planner import SelfConsumptionPlanner
from ems_config import (
    BATTERIES, BATTERY_ENTITY_LANGUAGE, MAX_GRID_EXPORT_POWER_W,
    MAX_GRID_IMPORT_POWER_W, SITE_CONFIG_PATH,
    ATOMIC_MARSTEK_CONTROL_ENABLED, AUTO_CONTROL_ENABLED,
    BATTERY_DISABLED_HELPERS, BATTERY_DISTRIBUTION_HELPER,
    SMART_CHARGE_STATUS_SENSOR,
    LOAD_BALANCER_HELPERS, LOAD_BALANCER_INTERVAL_SECONDS,
    CHARGE_COMPLETION_BUFFER_MINUTES,
    CHARGE_EFFICIENCY,
    CONTROLLER_COMMAND_DELAY_SECONDS, DEFAULTS, EMS_ENABLED_HELPER,
    EMS_NAME, EMS_SPRINT, EMS_VERSION, EXECUTION_INTERVAL_SECONDS, HELPERS,
    LOG_INTERVAL_SECONDS, MANUAL_CHARGE_POWER_HELPER,
    MANUAL_DISTRIBUTION_HELPER, MANUAL_EXCLUDED_HELPERS,
    MANUAL_DISCHARGE_POWER_HELPER, MANUAL_MODE_HELPER,
    MANUAL_STATUS_RECHECK_ATTEMPTS, MANUAL_STATUS_RECHECK_DELAY_SECONDS,
    MAX_PREFERRED_CHARGE_HOURS,
    MAX_VERIFICATION_RETRIES, MONITOR_INTERVAL_SECONDS,
    PLAN_INTERVAL_SECONDS, PRICE_ATTRIBUTE, PRICE_LOG_HOURS, PRICE_SENSOR,
    TEST_CHARGE_POWER_PER_BATTERY, TEST_CONTROL_ENABLED,
    TEST_DISCHARGE_POWER_PER_BATTERY, TEST_MODE_HELPER, TIMEZONE,
    VERIFICATION_DELAY_SECONDS, VERIFICATION_RETRY_DELAY_SECONDS,
    COORDINATED_CONTROL_INTERVAL_SECONDS, SELF_CONSUMPTION_MODE_HELPER,
    EFFICIENT_MINIMUM_CHARGE_POWER_W,
    EFFICIENT_PREFERRED_MIN_CHARGE_POWER_W,
    EFFICIENT_PREFERRED_MAX_CHARGE_POWER_W,
    FRANK_ALL_IN_ENTITY_HELPER,
    CHARGE_EFFICIENCY_MODE_HELPER,
    DISCHARGE_EFFICIENCY_MODE_HELPER,
    DYNAMIC_EXPORT_RESERVE_HELPER,
    EXPORT_RESERVE_MODE_HELPER,
    EXPORT_FIXED_RESERVE_KWH_HELPER,
    EXPORT_EFFICIENCY_MODE_HELPER,
    GRID_CHARGE_ALLOWED_HELPER,
    GRID_EXPORT_ALLOWED_HELPER,
    GRID_SENSOR_SOURCE_HELPER,
    GRID_POWER_ENTITY_HELPER,
    GRID_REFERENCE_ENTITY_HELPER,
    HOME_POWER_OFFSET_HELPER,
)
from ems_context import EMSContextBuilder
from ems_logger import EMSLogger
from energy_scheduler import EnergyScheduler
from efficiency_learner import EfficiencyLearner
from financial_tracker import FinancialTracker
from grid_export import GridExportManager
from grid_offset_calibrator import GridOffsetCalibrator, HomeOffsetCalibrator
from grid_status_monitor import GridStatusMonitor
from home_load_forecaster import HomeLoadForecaster
from marstek_controller import MarstekController
from manual_controller import ManualController
from models import SchedulerState
from price_manager import PriceManager
from price_forecast import PriceForecast
from export_reserve import auto_reserve_soc, home_energy_kwh, horizon_end
from runtime_settings import RuntimeSettings
from runtime_event_log import RuntimeEventLog
from runtime_checkpoint import RuntimeCheckpointManager
from runtime_safety_monitor import RuntimeSafetyMonitor
from safety_service import BatterySafetyService
from settings_manager import SettingsManager
from telemetry_freshness import TelemetryFreshnessGuard


class MarstekEMS(hass.Hass):
    def _publish_site_config(self):
        """Maak de installatie-instellingen zichtbaar voor het paneel."""
        self.set_state(
            "sensor.openems_configuratie",
            state=str(len(BATTERIES)),
            attributes={
                "friendly_name": "OpenEMS configuratie",
                "icon": "mdi:cog-outline",
                "accus": [
                    {
                        "nummer": index,
                        "naam": battery["name"],
                        "prefix": battery.get("prefix", ""),
                    }
                    for index, battery in enumerate(BATTERIES, start=1)
                ],
                "entiteit_taal": BATTERY_ENTITY_LANGUAGE,
                "max_import_w": MAX_GRID_IMPORT_POWER_W,
                "max_export_w": MAX_GRID_EXPORT_POWER_W,
                "eigen_instellingen": SITE_CONFIG_PATH.is_file(),
            },
        )

    def initialize(self):
        self.timezone = ZoneInfo(TIMEZONE)
        self.runtime_settings = RuntimeSettings(self)
        self.batteries = self.runtime_settings.batteries()
        self.battery_manager = BatteryManager(
            self, self.batteries,
            disabled_provider=self.runtime_settings.disabled_battery_indexes,
        )
        self.grid_offset_calibrator = GridOffsetCalibrator(
            self, self.runtime_settings
        )
        self.home_offset_calibrator = HomeOffsetCalibrator(
            self, self.runtime_settings
        )
        self.telemetry_guard = TelemetryFreshnessGuard(
            self, self.runtime_settings, self.batteries,
            disabled_provider=self.runtime_settings.disabled_battery_indexes,
        )
        self.price_manager = PriceManager(
            self, self.runtime_settings.frank_all_in_entity(),
            PRICE_ATTRIBUTE, TIMEZONE
        )
        self.export_price_manager = PriceManager(
            self, self.runtime_settings.frank_market_entity(),
            PRICE_ATTRIBUTE, TIMEZONE
        )
        self.price_forecast = PriceForecast(
            self, TIMEZONE,
            market_provider=self._forecast_market_prices,
            current_provider=self._forecast_current_prices,
            data_file=(
                Path(__file__).resolve().parent.parent
                / "OpenEMS-M-data"
                / "prijsvoorspelling_correctie.json"
            ),
        )
        self.charge_planner = ChargePlanner(
            TIMEZONE,
            MAX_PREFERRED_CHARGE_HOURS,
            CHARGE_COMPLETION_BUFFER_MINUTES,
        )
        self.discharge_planner = DischargePlanner(TIMEZONE)
        self.self_consumption_planner = SelfConsumptionPlanner(TIMEZONE)
        self.ems_logger = EMSLogger(self, self.price_manager, PRICE_LOG_HOURS)
        self.dashboard_status = DashboardStatus(self)
        self.controller = MarstekController(
            app=self,
            batteries=self.batteries,
            enabled=False,
            test_enabled=TEST_CONTROL_ENABLED,
            command_delay=CONTROLLER_COMMAND_DELAY_SECONDS,
            atomic_control=ATOMIC_MARSTEK_CONTROL_ENABLED,
        )
        self.coordinated_self_consumption = CoordinatedSelfConsumption(
            app=self,
            controller=self.controller,
            battery_manager=self.battery_manager,
            settings=self.runtime_settings,
            grid_calibrator=self.grid_offset_calibrator,
        )
        self.controller.bind_coordinator(self.coordinated_self_consumption)
        self.load_balancer = LoadBalancer(
            self, self.runtime_settings, self.controller, self.batteries
        )
        self.controller.bind_load_balancer(self.load_balancer)
        self.safety_service = BatterySafetyService(self, self.batteries)
        self.battery_health_manager = BatteryHealthManager(
            self,
            self.batteries,
            self.timezone,
            safety_service=self.safety_service,
            disabled_provider=self.runtime_settings.disabled_battery_indexes,
        )
        self.safety_service.bind_health_manager(
            self.battery_health_manager
        )
        self.controller.bind_health_manager(
            self.battery_health_manager
        )
        self.runtime_event_log = RuntimeEventLog(self, self.timezone)
        self.grid_status_monitor = GridStatusMonitor(
            self,
            self.batteries,
            self.timezone,
            event_log=self.runtime_event_log,
        )
        self.controller.bind_grid_status_monitor(self.grid_status_monitor)
        self.runtime_safety_monitor = RuntimeSafetyMonitor(
            self,
            self.controller,
            self.safety_service,
            event_log=self.runtime_event_log,
            health_manager=self.battery_health_manager,
        )
        self.scheduler = EnergyScheduler(
            self,
            self.controller,
            verification_delay=VERIFICATION_DELAY_SECONDS,
            retry_delay=VERIFICATION_RETRY_DELAY_SECONDS,
            max_verification_retries=MAX_VERIFICATION_RETRIES,
        )
        self.manual_controller = ManualController(
            app=self,
            controller=self.controller,
            enabled_helper=EMS_ENABLED_HELPER,
            mode_helper=MANUAL_MODE_HELPER,
            charge_power_helper=MANUAL_CHARGE_POWER_HELPER,
            discharge_power_helper=MANUAL_DISCHARGE_POWER_HELPER,
            test_mode_helper=TEST_MODE_HELPER,
            battery_count=len(self.batteries),
            distribution_helper=MANUAL_DISTRIBUTION_HELPER,
            excluded_helpers=MANUAL_EXCLUDED_HELPERS,
            verification_delay=VERIFICATION_DELAY_SECONDS,
            status_recheck_delay=MANUAL_STATUS_RECHECK_DELAY_SECONDS,
            status_rechecks=MANUAL_STATUS_RECHECK_ATTEMPTS,
        )
        self.settings_manager = SettingsManager(self)
        self.financial_tracker = FinancialTracker(
            self, self.runtime_settings, self.batteries,
            self.timezone,
        )
        self.efficiency_learner = EfficiencyLearner(
            self, self.batteries, self.timezone
        )
        self.grid_export_manager = GridExportManager(
            app=self,
            settings=self.runtime_settings,
            controller=self.controller,
            battery_manager=self.battery_manager,
            grid_calibrator=self.grid_offset_calibrator,
            telemetry_guard=self.telemetry_guard,
            financial_tracker=self.financial_tracker,
            safety_service=self.safety_service,
            timezone=self.timezone,
        )
        self.home_load_forecaster = HomeLoadForecaster(
            self, self.runtime_settings, self.timezone
        )

        self._context = None
        self._dashboard_charge_plan = None
        self._dashboard_battery_charge_plans = []
        self._dashboard_charge_price_slots = []
        self._dashboard_discharge_price_slots = []
        self._dashboard_discharge_profit_blocks = []
        self._current_price = None
        self._boundary_handles = []
        self._plan_generation = 0
        self._plan_calculation_active = False
        self._last_plan_calculation_at = None
        self._deferred_plan_generation = 0
        self._input_change_generation = 0
        self._telemetry_retry_generation = 0
        self._self_consumption_restart_generation = 0
        self._telemetry_retry_pending = False
        self._fast_grid_listener_handles = []

        self._battery_disable_generation = [
            0 for _ in BATTERY_DISABLED_HELPERS
        ]
        for helper in BATTERY_DISABLED_HELPERS:
            self.listen_state(self._battery_disabled_changed, helper)
        self.listen_state(self._openems_enabled_changed, EMS_ENABLED_HELPER)
        self.listen_state(self._test_mode_changed, TEST_MODE_HELPER)
        self.listen_state(
            self._self_consumption_mode_changed,
            SELF_CONSUMPTION_MODE_HELPER,
        )
        self.listen_state(
            self._self_consumption_mode_changed,
            BATTERY_DISTRIBUTION_HELPER,
        )
        self.listen_event(
            self._marstek_control_result,
            "openems_marstek_control_result",
        )
        self.listen_state(
            self._planning_input_changed,
            self.runtime_settings.frank_all_in_entity(),
        )
        self.listen_state(
            self._planning_input_changed, FRANK_ALL_IN_ENTITY_HELPER
        )
        for helper in (
            CHARGE_EFFICIENCY_MODE_HELPER,
            DISCHARGE_EFFICIENCY_MODE_HELPER,
            EXPORT_EFFICIENCY_MODE_HELPER,
        ):
            self.listen_state(self._planning_input_changed, helper)
        self.listen_state(
            self._planning_input_changed,
            DYNAMIC_EXPORT_RESERVE_HELPER,
        )
        for helper in (
            GRID_EXPORT_ALLOWED_HELPER,
            GRID_SENSOR_SOURCE_HELPER,
            EXPORT_RESERVE_MODE_HELPER,
            EXPORT_FIXED_RESERVE_KWH_HELPER,
            HOME_POWER_OFFSET_HELPER,
        ):
            self.listen_state(self._planning_input_changed, helper)
        self.listen_state(
            self._grid_charge_allowed_changed,
            GRID_CHARGE_ALLOWED_HELPER,
        )
        for entity_id in HELPERS.values():
            self.listen_state(self._planning_input_changed, entity_id)
        for helper in (
            GRID_POWER_ENTITY_HELPER,
            GRID_REFERENCE_ENTITY_HELPER,
        ):
            self.listen_state(self._grid_sensor_entity_changed, helper)
        self._bind_fast_grid_listeners()

        self._apply_openems_enabled(self.get_state(EMS_ENABLED_HELPER))
        self.controller_test = ControllerTestMode(
            app=self,
            controller=self.controller,
            helper_entity=TEST_MODE_HELPER,
            battery_count=len(self.batteries),
            charge_power=TEST_CHARGE_POWER_PER_BATTERY,
            discharge_power=TEST_DISCHARGE_POWER_PER_BATTERY,
        )
        self.runtime_checkpoint = RuntimeCheckpointManager(
            self, self.timezone
        )
        self.controller_test.start()
        self.manual_controller.start()
        self.settings_manager.start()
        self.price_forecast.start()
        self.grid_offset_calibrator.start()
        self.home_offset_calibrator.start()
        self.runtime_checkpoint.start()
        self._publish_site_config()

        self.log("============================================================")
        self.log(f"{EMS_NAME} v{EMS_VERSION}")
        self.log(f"Sprint: {EMS_SPRINT}")
        self.log(
            "Runtime: planning {}s | uitvoering {}s | monitoring {}s | "
            "logging {}s".format(
                PLAN_INTERVAL_SECONDS,
                EXECUTION_INTERVAL_SECONDS,
                MONITOR_INTERVAL_SECONDS,
                LOG_INTERVAL_SECONDS,
            )
        )
        self.log("============================================================")

        # Het herstart-herstel bepaalt eerst of de bestaande uitvoering nog
        # geldig is. Pas daarna mag een verse planning worden berekend.
        now = datetime.now(self.timezone)
        self.run_every(
            self.calculate_plan,
            now + timedelta(seconds=PLAN_INTERVAL_SECONDS),
            PLAN_INTERVAL_SECONDS,
        )
        self.run_every(self.execute_plan, "now", EXECUTION_INTERVAL_SECONDS)
        self.run_every(
            self.load_balancer.tick, "now+5", LOAD_BALANCER_INTERVAL_SECONDS
        )
        for helper in LOAD_BALANCER_HELPERS.values():
            self.listen_state(self._load_balancer_changed, helper)
        self.run_every(
            self.grid_export_manager.tick,
            now + timedelta(seconds=3),
            5,
        )
        self.run_every(
            self.grid_status_monitor.tick,
            "now",
            1,
        )
        self.run_every(
            self._coordinated_self_consumption_tick,
            now + timedelta(seconds=COORDINATED_CONTROL_INTERVAL_SECONDS),
            COORDINATED_CONTROL_INTERVAL_SECONDS,
        )
        self.run_every(
            self.runtime_safety_monitor.tick,
            now + timedelta(seconds=5),
            5,
        )
        self.run_every(
            self.battery_health_manager.tick,
            now + timedelta(seconds=7),
            30,
        )
        self.run_every(
            self.runtime_checkpoint.capture,
            now + timedelta(seconds=5),
            5,
        )
        self.run_every(
            self._sample_financial_results,
            now + timedelta(seconds=60),
            60,
        )
        self.run_every(
            self.home_load_forecaster.sample,
            now + timedelta(seconds=20),
            900,
        )
        self.run_every(
            self.monitor_system,
            now + timedelta(seconds=5),
            MONITOR_INTERVAL_SECONDS,
        )
        self.run_every(
            self.log_system,
            now + timedelta(seconds=10),
            LOG_INTERVAL_SECONDS,
        )
        # Een bij een herstart al uitgeschakelde accu opnieuw veilig stoppen.
        self.run_in(self._apply_disabled_batteries_at_startup, 20)

    # ------------------------------------------------------------------
    # Accu's per stuk in de instellingen aan- of uitzetten
    # ------------------------------------------------------------------
    BATTERY_DISABLE_RETRIES = 3
    BATTERY_DISABLE_RETRY_SECONDS = 10

    def _load_balancer_changed(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        self.log(f"[Load balancer] Instelling gewijzigd: {entity} = {new}")
        self.load_balancer.tick()

    def _battery_disabled_changed(self, entity, attribute, old, new, kwargs):
        if old == new or entity not in BATTERY_DISABLED_HELPERS:
            return
        index = BATTERY_DISABLED_HELPERS.index(entity)
        name = self.batteries[index].get("name", f"Accu {index + 1}")
        disabled = str(new).lower() == "on"
        was_disabled = str(old).lower() == "on"
        if disabled == was_disabled:
            return
        self._battery_disable_generation[index] += 1
        generation = self._battery_disable_generation[index]
        event_log = getattr(self, "runtime_event_log", None)
        if disabled:
            self.log(
                f"{name} uitgeschakeld in de instellingen: gebruikersmodus "
                "wordt Handmatig en de accu telt niet meer mee"
            )
            if event_log is not None:
                event_log.record(
                    "warning", f"{name} uitgeschakeld",
                    "Handmatig uitgeschakeld in de OpenEMS-instellingen",
                    battery_index=index, battery_name=name,
                )
            self._apply_battery_disable(
                {"index": index, "generation": generation, "attempt": 1}
            )
        else:
            self.log(f"{name} weer ingeschakeld in de instellingen")
            if event_log is not None:
                event_log.record(
                    "info", f"{name} weer ingeschakeld",
                    "OpenEMS gebruikt deze accu weer",
                    battery_index=index, battery_name=name,
                )
        self.battery_health_manager.publish()
        self._planning_input_changed(entity, None, "x", "y", {})
        if self._context is not None:
            self.run_in(self.execute_plan, 1)

    def _apply_battery_disable(self, kwargs):
        """Zet een uitgeschakelde accu veilig op Handmatig met 0 W.

        De controller zet daarvoor de gebruikersmodus op Handmatig en geeft
        een Force Stop. Lukt dat niet direct (bijvoorbeeld door een andere
        lopende opdracht), dan volgen enkele herhalingen.
        """
        index = int(kwargs["index"])
        if int(kwargs["generation"]) != self._battery_disable_generation[index]:
            return
        if not self.runtime_settings.battery_disabled(index):
            return
        accepted = self.controller.isolate_batteries(
            [index], source="selector"
        )
        attempt = int(kwargs.get("attempt", 1))
        if accepted or attempt >= self.BATTERY_DISABLE_RETRIES:
            return
        self.run_in(
            self._apply_battery_disable,
            self.BATTERY_DISABLE_RETRY_SECONDS,
            index=index,
            generation=kwargs["generation"],
            attempt=attempt + 1,
        )

    def _apply_disabled_batteries_at_startup(self, kwargs):
        for index in self.runtime_settings.disabled_battery_indexes():
            self._battery_disable_generation[index] += 1
            self._apply_battery_disable({
                "index": index,
                "generation": self._battery_disable_generation[index],
                "attempt": 1,
            })
        self.battery_health_manager.publish()

    def calculate_plan(self, kwargs):
        """Berekent alleen plannen; stuurt geen controllercommando's."""
        kwargs = kwargs or {}
        checkpoint = getattr(self, "runtime_checkpoint", None)
        if (
            checkpoint is not None
            and checkpoint.startup_pending()
            and not kwargs.get("recovery_ready")
        ):
            return
        moment = time.monotonic()
        if self._plan_calculation_active:
            self.log(
                "[Planning] Dubbele gelijktijdige plantrigger samengevoegd",
                level="DEBUG",
            )
            return
        if (
            not kwargs.get("force")
            and self._last_plan_calculation_at is not None
            and moment - self._last_plan_calculation_at < 3.0
        ):
            if kwargs.get("input_change"):
                self._deferred_plan_generation += 1
                self.run_in(
                    self._deferred_input_plan,
                    max(
                        0.1,
                        3.0 - (moment - self._last_plan_calculation_at),
                    ),
                    generation=self._deferred_plan_generation,
                )
            self.log(
                "[Planning] Plantrigger rond dezelfde uurgrens "
                "samengevoegd",
                level="DEBUG",
            )
            return
        self._plan_calculation_active = True
        self._last_plan_calculation_at = moment
        try:
            self._calculate_plan(kwargs)
        finally:
            self._plan_calculation_active = False

    def _forecast_market_prices(self):
        """Bekende Frank-marktprijzen, om de voorspelling mee te toetsen."""
        self.export_price_manager.sensor = (
            self.runtime_settings.frank_market_entity()
        )
        return self.export_price_manager.get_planning_prices()

    def _forecast_current_prices(self):
        """(all-in, markt) van dit moment, om de vaste opslag af te leiden."""
        values = []
        for entity in (
            self.runtime_settings.frank_all_in_entity(),
            self.runtime_settings.frank_market_entity(),
        ):
            try:
                values.append(float(self.get_state(entity)))
            except (TypeError, ValueError):
                values.append(None)
        return tuple(values)

    def _forecast_planner_kwargs(self, planning_prices, resolution):
        """Voorspelde prijzen voor Slim laden; bij elke fout: niets extra's."""
        try:
            kwargs = self.price_forecast.planner_kwargs(
                planning_prices, self.price_manager, resolution
            )
            self._forecast_kwargs = kwargs
            return kwargs
        except Exception as error:
            self._forecast_kwargs = {}
            self.log(
                f"[Prijsvoorspelling] Niet gebruikt in de planning: {error}",
                level="WARNING",
            )
            return {}

    def _publish_forecast_plan(self, shared_kwargs, reference_price):
        """Indicatief laden/zelfconsumptie voor dagen zonder Frank-prijzen.

        Alleen weergave in de grafiek; er wordt niets mee aangestuurd.
        """
        sensor = "sensor.openems_voorspeld_plan"
        attributes = {
            "friendly_name": "OpenEMS voorspeld plan",
            "uren": [],
            "dagen": [],
        }
        try:
            forecast = getattr(self, "_forecast_kwargs", None) or {}
            prices = list(forecast.get("forecast_prices") or [])
            day_plans = list(
                getattr(self.charge_planner, "forecast_day_plans", []) or []
            )
            if not prices or not day_plans:
                self.set_state(sensor, state="uit", attributes=attributes)
                return
            charge_intervals = []
            references = {}
            last_reference = float(reference_price)
            for entry in day_plans:
                if entry.get("gemiddelde_prijs") is not None:
                    last_reference = float(entry["gemiddelde_prijs"])
                references[entry["dag"]] = last_reference
                for item in entry.get("laden", []):
                    charge_intervals.append((
                        self.price_manager.local_datetime(item["van"]),
                        self.price_manager.local_datetime(item["tot"]),
                    ))
            plan_days = set(references)
            indicative_planner = self.self_consumption_planner
            self_plan = indicative_planner.calculate(
                future_prices=prices,
                excluded_intervals=charge_intervals,
                **{
                    **shared_kwargs,
                    "average_charge_prices_by_day": references,
                },
            )
            blocks = [(b.start, b.end) for b in self_plan.blocks]
            hours = []
            for item in prices:
                start = self.price_manager.local_datetime(item["from"])
                end = self.price_manager.local_datetime(item["till"])
                if str(start.date()) not in plan_days:
                    continue
                if any(s < end and start < e for s, e in charge_intervals):
                    mode = "laden"
                elif any(s < end and start < e for s, e in blocks):
                    mode = "zelf"
                else:
                    mode = "standby"
                hours.append({"van": start.isoformat(), "modus": mode})
            attributes["uren"] = hours
            attributes["dagen"] = day_plans
            self.set_state(sensor, state="aan", attributes=attributes)
        except Exception as error:
            self.log(
                f"[Prijsvoorspelling] Voorspeld plan niet gemaakt: {error}",
                level="WARNING",
            )
            try:
                self.set_state(sensor, state="uit", attributes=attributes)
            except Exception:
                pass

    def _publish_smart_charge(self):
        params = self.runtime_settings.smart_charge_params()
        notes = list(getattr(self.charge_planner, "last_smart_notes", []))
        if not params["smart_enabled"]:
            state, text = "uit", "Slim vooruitkijken staat uit"
        elif notes:
            state, text = "aan", notes[0]["tekst"]
        else:
            state, text = "aan", "Geen volgende dag om mee te vergelijken"
        self.set_state(
            SMART_CHARGE_STATUS_SENSOR,
            state=state,
            attributes={
                "friendly_name": "OpenEMS slim laden",
                "uitleg": text,
                "dagen": notes,
            },
        )

    def _calculate_plan(self, kwargs):
        """Interne planberekening achter de trigger- en opstartbewaking."""
        try:
            now = datetime.now(self.timezone)
            self.price_manager.sensor = (
                self.runtime_settings.frank_all_in_entity()
            )
            battery = self.battery_manager.read()
            target_soc = self._helper("target_soc")
            minimum_soc = self._helper("minimum_soc")
            price_margin = self._helper("charge_margin")
            discharge_margin = self._helper("discharge_margin")
            charge_window_start = self._helper("charge_window_start")
            charge_window_end = self._helper("charge_window_end")
            minimum_charge_power = max(
                EFFICIENT_MINIMUM_CHARGE_POWER_W,
                self._helper("minimum_charge_power"),
            )
            (
                charge_efficiencies,
                discharge_efficiencies,
                _efficiency_details,
            ) = self.runtime_settings.effective_efficiencies()
            discharge_resolution = (
                self.get_state(HELPERS["discharge_resolution"])
                or "Automatisch"
            )

            battery_energy_needed = battery.capacity * max(
                0.0, target_soc - battery.average_soc
            ) / 100
            needed_energy = self._net_energy(battery_energy_needed)
            raw_planning_prices = self.price_manager.get_planning_prices()
            if not raw_planning_prices:
                self.ems_logger.warning(
                    "Geen Frank-prijzen voor de planningsperiode beschikbaar; "
                    "vorige geldige planning blijft actief"
                )
                return
            planning_prices = self.price_manager.apply_resolution(
                raw_planning_prices, discharge_resolution
            )
            self.export_price_manager.sensor = (
                self.runtime_settings.frank_market_entity()
            )
            raw_export_prices = (
                self.export_price_manager.get_planning_prices()
            )
            export_prices = self.export_price_manager.apply_resolution(
                raw_export_prices, discharge_resolution
            )
            export_prices = self.export_price_manager.add_price_adjustment(
                export_prices,
                self._helper("grid_export_return_fee"),
            )

            actionable_prices = self.price_manager.get_actionable_prices(
                planning_prices, now
            )
            remaining_prices = self.price_manager.get_remaining_prices(
                planning_prices, now
            )
            dashboard_charge_price_slots = (
                self.charge_planner.economic_price_slots(
                    planning_prices, price_margin
                )
            )
            actionable_charge_prices = (
                self.price_manager.restrict_to_economic_slots(
                    actionable_prices,
                    dashboard_charge_price_slots,
                )
            )
            planning_soc_values = self.scheduler.planning_soc_values(
                battery.soc_values, target_soc, now
            )
            planning_end = max(
                (
                    self.price_manager.local_datetime(item["till"])
                    for item in planning_prices
                ),
                default=now + timedelta(hours=24),
            )
            planning_horizon_hours = max(
                24,
                int((planning_end - now).total_seconds() / 3600) + 1,
            )
            planning_home_forecast = self.home_load_forecaster.forecast(
                now, hours=planning_horizon_hours
            )
            previous_export_blocks = list(
                getattr(
                    getattr(self._context, "export_plan", None),
                    "blocks",
                    [],
                )
                if self._context is not None else []
            )
            daily_charge_forecasts = (
                self.charge_planner.calculate_per_battery_each_day(
                prices=actionable_charge_prices,
                names=battery.names,
                soc_values=planning_soc_values,
                capacities=battery.capacities,
                max_charge_values=battery.max_charge_values,
                target_soc=target_soc,
                charge_efficiency=(
                    sum(charge_efficiencies) / len(charge_efficiencies)
                    if charge_efficiencies else CHARGE_EFFICIENCY
                ),
                charge_efficiencies=charge_efficiencies,
                minimum_power=minimum_charge_power,
                price_margin=price_margin,
                preferred_min_power=EFFICIENT_PREFERRED_MIN_CHARGE_POWER_W,
                preferred_max_power=EFFICIENT_PREFERRED_MAX_CHARGE_POWER_W,
                allowed_start_hour=charge_window_start,
                allowed_end_hour=charge_window_end,
                home_forecast=planning_home_forecast,
                fallback_home_power=(
                    self.home_load_forecaster.baseline_power()
                ),
                projected_export_blocks=previous_export_blocks,
                discharge_efficiencies=discharge_efficiencies,
                projection_start=now,
                projection_minimum_soc=minimum_soc,
                **self.runtime_settings.smart_charge_params(),
                **self._forecast_planner_kwargs(
                    planning_prices, discharge_resolution
                ),
                )
            )
            self._publish_smart_charge()
            charge_plan, battery_charge_plans = (
                daily_charge_forecasts[0]
                if daily_charge_forecasts else (None, [])
            )
            battery_energy_needed = sum(
                plan.battery_energy_needed for plan in battery_charge_plans
            )
            needed_energy = sum(
                plan.grid_energy_needed for plan in battery_charge_plans
            )
            previous_charge_plan = (
                self._context.charge_plan if self._context else None
            )
            previous_runtime_end = (
                self.scheduler.charge_plan_runtime_end(
                    previous_charge_plan, now
                )
                if previous_charge_plan else None
            )
            if previous_charge_plan and (
                previous_charge_plan.start <= now < previous_runtime_end
            ) and not getattr(self.charge_planner, "charge_deferred", False) and not (
                planning_soc_values and all(
                    float(value) >= float(target_soc) - 2.0
                    for value in planning_soc_values
                )
            ):
                # De planning mikt bewust op een kwartier voor het einde.
                # Bewaar haar tijdens die uitloop, zodat de scheduler blijft
                # laden wanneer de actuele doel-SOC nog niet is bereikt.
                charge_plan = previous_charge_plan
                battery_charge_plans = list(
                    self._context.battery_charge_plans
                )
            dashboard_charge_plan = charge_plan
            dashboard_battery_charge_plans = list(battery_charge_plans)
            active_plan_day = (
                charge_plan.start.date() if charge_plan else None
            )
            export_charge_forecasts = (
                [(charge_plan, list(battery_charge_plans))]
                if charge_plan is not None else []
            )
            for forecast_plan, forecast_battery_plans in (
                daily_charge_forecasts
            ):
                if forecast_plan.start.date() == active_plan_day:
                    continue
                dashboard_battery_charge_plans.extend(
                    forecast_battery_plans
                )
                export_charge_forecasts.append((
                    forecast_plan, list(forecast_battery_plans)
                ))
            charge_plan = self.charge_planner.with_economic_price_window(
                charge_plan, planning_prices, price_margin
            )
            dashboard_charge_plan = (
                self.charge_planner.with_economic_price_window(
                    dashboard_charge_plan, planning_prices, price_margin
                )
            )

            hours_needed = (
                max(
                    (
                        plan.grid_energy_needed
                        / (battery.max_charge_values[plan.index] / 1000)
                        for plan in battery_charge_plans
                        if battery.max_charge_values[plan.index] > 0
                    ),
                    default=0.0,
                )
            )
            charge_distribution = [
                plan.planned_power for plan in battery_charge_plans
            ]
            inventory_cost = (
                self.financial_tracker.average_inventory_cost()
            )
            recent_charge_cost = (
                self.financial_tracker.recent_charge_cost()
            )
            current_planning_price = (
                self.price_manager.get_current_price(planning_prices)
            )
            reference_charge_price = (
                float(recent_charge_cost)
                if recent_charge_cost is not None
                else float(
                    current_planning_price
                    if current_planning_price is not None
                    else planning_prices[0]["price"]
                )
            )
            daily_charge_references = {}
            today_key = str(now.date())
            today_charge_cost = (
                self.financial_tracker.today_charge_cost(now)
            )
            if today_charge_cost is not None:
                daily_charge_references[today_key] = float(
                    today_charge_cost
                )
            elif recent_charge_cost is not None:
                daily_charge_references[today_key] = float(
                    recent_charge_cost
                )
            for forecast, _forecast_battery_plans in (
                export_charge_forecasts
            ):
                daily_charge_references[str(forecast.start.date())] = (
                    float(forecast.average_price)
                )
            self_consumption_reference_price = (
                daily_charge_references.get(
                    today_key, reference_charge_price
                )
            )
            export_efficiency, _export_efficiency_source = (
                self.runtime_settings.export_efficiency()
            )
            learned_export_powers = []
            if _export_efficiency_source == "zelfgeleerd":
                learned_export_powers = [
                    profile.get("voorkeursvermogen_w")
                    if isinstance(profile, dict) else None
                    for profile in (
                        self.efficiency_learner.discharge_power_profiles()
                    )
                ]
            planned_charge_intervals = [
                (segment.start, segment.end)
                for _forecast, forecast_battery_plans
                in export_charge_forecasts
                for forecast_battery_plan in forecast_battery_plans
                for segment in forecast_battery_plan.segments
            ]
            economic_charge_intervals = [
                (
                    self.price_manager.local_datetime(item["from"]),
                    self.price_manager.local_datetime(item["till"]),
                )
                for item in dashboard_charge_price_slots
            ]
            future_planned_charge_starts = sorted(
                start for start, _end in planned_charge_intervals
                if start > now
            )
            next_planned_charge_start = (
                future_planned_charge_starts[0]
                if future_planned_charge_starts else None
            )
            before_same_day_charge = bool(
                next_planned_charge_start is not None
                and next_planned_charge_start.date() == now.date()
            )
            possible_charge_starts = sorted(
                start for start, _end in economic_charge_intervals
                if start > now
            )
            next_possible_charge_start = (
                possible_charge_starts[0]
                if possible_charge_starts
                else next_planned_charge_start
            )
            total_capacity = max(0.0, float(battery.capacity))

            def reserve_soc(kwh):
                return max(
                    minimum_soc,
                    (
                        max(0.0, float(kwh)) / total_capacity * 100
                        if total_capacity > 0 else minimum_soc
                    ),
                )

            before_mode_reader = getattr(
                self.runtime_settings,
                "export_reserve_before_charge_mode",
                lambda: "fixed",
            )
            after_mode_reader = getattr(
                self.runtime_settings,
                "export_reserve_after_charge_mode",
                lambda: "fixed",
            )
            before_kwh_reader = getattr(
                self.runtime_settings,
                "fixed_export_reserve_before_charge_kwh",
                self.runtime_settings.fixed_export_reserve_kwh,
            )
            after_kwh_reader = getattr(
                self.runtime_settings,
                "fixed_export_reserve_after_charge_kwh",
                self.runtime_settings.fixed_export_reserve_kwh,
            )
            before_reserve_soc = reserve_soc(before_kwh_reader())
            after_reserve_soc = reserve_soc(after_kwh_reader())
            active_reserve_mode = (
                before_mode_reader()
                if before_same_day_charge else after_mode_reader()
            )
            active_reserve_soc = (
                before_reserve_soc
                if before_same_day_charge else after_reserve_soc
            )
            self._auto_export_reserve_note = None
            self._forecast_export_block = bool(
                getattr(self.charge_planner, "forecast_postponed", False)
            )
            if self.runtime_settings.auto_export_reserve_enabled():
                smart = self.runtime_settings.smart_charge_params()
                reserve_end = horizon_end(now, next_possible_charge_start)
                horizon = max(
                    1,
                    int((reserve_end - now).total_seconds() / 3600) + 1,
                )
                usage_kwh = home_energy_kwh(
                    self.home_load_forecaster.forecast(now, hours=horizon),
                    now,
                    reserve_end,
                    self.home_load_forecaster.baseline_power(),
                )
                active_reserve_mode = "fixed"
                active_reserve_soc = auto_reserve_soc(
                    usage_kwh,
                    total_capacity,
                    minimum_soc,
                    smart["smart_reserve_soc"],
                    smart["smart_buffer"],
                )
                self._auto_export_reserve_note = (
                    f"Automatische reserve {active_reserve_soc:.0f}% "
                    f"(verbruik tot {reserve_end:%d-%m %H:%M}: "
                    f"{usage_kwh:.1f} kWh)"
                )
            if self._forecast_export_block:
                # Laden is uitgesteld op de prijsvoorspelling: de energie in
                # de accu is bedoeld voor het huis tot het goedkope blok.
                # Teruglevering wordt daarom niet gepland.
                active_reserve_mode = "fixed"
                active_reserve_soc = 100.0
                self._auto_export_reserve_note = (
                    "Terugleveren uit: laden is uitgesteld op de "
                    "prijsvoorspelling"
                )
            self.set_state(
                "sensor.openems_export_reserve_auto",
                state="aan" if self._auto_export_reserve_note else "uit",
                attributes={
                    "friendly_name": "OpenEMS automatische exportreserve",
                    "uitleg": (
                        self._auto_export_reserve_note
                        or "Vaste exportreserve in gebruik"
                    ),
                },
            )
            excluded_charge_intervals = [
                *economic_charge_intervals,
                *planned_charge_intervals,
            ]
            projected_soc_values = list(map(float, battery.soc_values))
            export_energy_resets = []
            for forecast, forecast_battery_plans in sorted(
                export_charge_forecasts,
                key=lambda item: item[0].end,
            ):
                soc_after_charge = list(projected_soc_values)
                charge_completed = False
                for forecast_battery_plan in forecast_battery_plans:
                    if not forecast_battery_plan.segments:
                        continue
                    charge_completed = True
                    index = forecast_battery_plan.index
                    capacity = float(forecast_battery_plan.capacity)
                    added_soc = (
                        float(
                            forecast_battery_plan.battery_energy_needed
                        )
                        / capacity * 100
                        if capacity > 0 else 0.0
                    )
                    soc_after_charge[index] = min(
                        float(forecast_battery_plan.target_soc),
                        float(forecast_battery_plan.current_soc) + added_soc,
                    )
                if charge_completed:
                    export_energy_resets.append({
                        "at": forecast.end,
                        "soc_values": soc_after_charge,
                        "average_charge_price": float(
                            forecast.average_price
                        ),
                        "reserve_soc_after": after_reserve_soc,
                    })
                    projected_soc_values = soc_after_charge
            shared_discharge_kwargs = {
                "average_charge_price": (
                    self_consumption_reference_price
                ),
                "discharge_margin": discharge_margin,
                "minimum_soc": minimum_soc,
                "avg_soc": battery.average_soc,
                "capacity": battery.capacity,
                "names": battery.names,
                "soc_values": battery.soc_values,
                "capacities": battery.capacities,
                "discharge_efficiencies": discharge_efficiencies,
                "resolution": discharge_resolution,
                "minimum_block_minutes": self._helper(
                    "minimum_discharge_block"
                ),
                "average_charge_prices_by_day": (
                    daily_charge_references
                ),
            }
            self_consumption_plan = self.self_consumption_planner.calculate(
                future_prices=remaining_prices,
                excluded_intervals=excluded_charge_intervals,
                **shared_discharge_kwargs,
            )
            previous_self_consumption_plan = (
                self._context.discharge_plan if self._context else None
            )
            self_consumption_plan = (
                self.self_consumption_planner.lock_started_interval(
                    self_consumption_plan,
                    previous_self_consumption_plan,
                    now,
                )
            )
            planned_home_use_intervals = [
                (block.start, block.end)
                for block in self_consumption_plan.blocks
            ]
            self._publish_forecast_plan(
                shared_discharge_kwargs, self_consumption_reference_price
            )
            export_kwargs = {
                **shared_discharge_kwargs,
                "average_charge_price": reference_charge_price,
                "inventory_cost_per_usable_kwh": inventory_cost,
                "export_efficiency": export_efficiency,
                "discharge_margin": self._helper(
                    "grid_export_minimum_difference"
                ),
                "max_discharge_values": battery.max_discharge_values,
                "selection_margin": self._helper(
                    "grid_export_selection_margin"
                ),
                "reserve_soc": active_reserve_soc,
                "maximum_export_power": (
                    self._helper("grid_export_max_power")
                    + max(
                        0.0,
                        float(
                            self.home_load_forecaster.baseline_power()
                            or 0.0
                        ),
                    )
                ),
                "preferred_min_power": (
                    EFFICIENT_PREFERRED_MIN_CHARGE_POWER_W
                ),
                "preferred_max_power": (
                    EFFICIENT_PREFERRED_MAX_CHARGE_POWER_W
                ),
                "preferred_discharge_powers": learned_export_powers,
                "excluded_intervals": excluded_charge_intervals,
                "home_use_intervals": planned_home_use_intervals,
            }
            remaining_export_prices = (
                self.export_price_manager.get_remaining_prices(
                    export_prices, now
                )
            )
            export_plan = self.discharge_planner.calculate_with_energy_resets(
                future_prices=remaining_export_prices,
                energy_resets=export_energy_resets,
                home_forecast=planning_home_forecast,
                fallback_home_power=(
                    self.home_load_forecaster.baseline_power()
                ),
                **export_kwargs,
            )
            if (
                active_reserve_mode == "dynamic"
                and next_possible_charge_start is not None
            ):
                horizon_hours = max(
                    1,
                    int(
                        (
                            next_possible_charge_start.timestamp()
                            - now.timestamp()
                        ) / 3600
                    ) + 1,
                )
                # Alleen de écht geplande laadprijs telt als referentie voor
                # het vrijgeven van "overtollige" energie. Is er (nog) geen
                # gepland laadmoment — bv. omdat morgen se prijzen nog niet
                # bekend zijn — dan is deze prijs None en geeft de planner
                # bewust niets vrij (zie with_dynamic_reserve_before_charge).
                next_planned_charge_price = (
                    daily_charge_references.get(
                        str(next_planned_charge_start.date())
                    )
                    if next_planned_charge_start is not None else None
                )
                average_charge_efficiency = (
                    sum(charge_efficiencies) / len(charge_efficiencies)
                    if charge_efficiencies else CHARGE_EFFICIENCY
                )
                next_charge_reference_price = (
                    next_planned_charge_price
                    / max(0.01, average_charge_efficiency)
                    if next_planned_charge_price is not None else None
                )
                export_plan = (
                    self.discharge_planner
                    .with_dynamic_reserve_before_charge(
                        export_plan,
                        remaining_export_prices,
                        now,
                        next_possible_charge_start,
                        self.home_load_forecaster.forecast(
                            now, hours=horizon_hours
                        ),
                        dynamic_reserve_kwh=(
                            self.runtime_settings
                            .dynamic_export_reserve_kwh()
                        ),
                        next_charge_reference_price=(
                            next_charge_reference_price
                        ),
                        **export_kwargs,
                    )
                )
            export_plan = self._enforce_export_reserve_floor(
                export_plan, minimum_soc
            )
            export_plan = self._lock_started_export_interval(
                export_plan, now
            )
            dashboard_discharge_price_slots = list(
                self_consumption_plan.eligible_slots
            )

            context = EMSContextBuilder.build(
                now=now,
                battery=battery,
                target_soc=target_soc,
                minimum_soc=minimum_soc,
                price_margin=price_margin,
                discharge_margin=discharge_margin,
                charge_efficiency=(
                    sum(charge_efficiencies) / len(charge_efficiencies)
                    if charge_efficiencies else CHARGE_EFFICIENCY
                ),
                battery_energy_needed=battery_energy_needed,
                needed_energy=needed_energy,
                hours_needed=hours_needed,
                future_prices=planning_prices,
                charge_plan=charge_plan,
                battery_charge_plans=battery_charge_plans,
                discharge_plan=self_consumption_plan,
                export_plan=export_plan,
                charge_distribution=charge_distribution,
            )
            self.scheduler.determine(context)
            self._context = context
            self._dashboard_charge_plan = dashboard_charge_plan
            self._dashboard_battery_charge_plans = (
                dashboard_battery_charge_plans
            )
            self._dashboard_charge_price_slots = (
                dashboard_charge_price_slots
            )
            self._dashboard_discharge_price_slots = (
                dashboard_discharge_price_slots
            )
            self._dashboard_discharge_profit_blocks = list(
                self_consumption_plan.blocks
            )
            self._current_price = self.price_manager.get_current_price(
                planning_prices
            )
            if self._current_price is None:
                self._current_price = planning_prices[0]["price"]
            self._schedule_execution_boundaries(context)
            self._publish_dashboard()
            self.log(
                "[Planning] Nieuwe planning berekend; exacte start- en "
                "stoptijden ingepland"
            )
        except (TypeError, ValueError, KeyError) as error:
            self.log(f"Planberekening mislukt: {error}", level="ERROR")

    def execute_plan(self, kwargs):
        """Voert alleen de actuele gewenste state uit."""
        if self.runtime_checkpoint.startup_pending():
            return
        if self._context is None:
            return
        if self.grid_status_monitor.planning_paused():
            self.scheduler.cancel_retry("netuitval; planning gepauzeerd")
            return
        if self.grid_export_manager.controls_execution():
            return
        freshness = self.telemetry_guard.check(
            include_grid=(
                self.runtime_settings.self_consumption_mode() == "openems"
            )
        )
        self.telemetry_guard.log_transition()
        if not freshness["ok"]:
            already_blocked = self.scheduler.telemetry_blocked()
            self.scheduler.mark_telemetry_blocked(freshness)
            indexes = sorted({
                item["accu_index"] for item in freshness["problems"]
                if item["accu_index"] is not None
            })
            has_grid_problem = any(
                item["accu_index"] is None
                for item in freshness["problems"]
            )
            if not already_blocked:
                if has_grid_problem:
                    self.controller.set_stop(source="auto")
                elif indexes:
                    self.controller.stop_batteries(
                        indexes, source="auto", safety=True
                    )
            self._schedule_telemetry_recovery()
            return
        self._telemetry_retry_pending = False
        self.runtime_checkpoint.handoff_to_fresh_plan()
        if self.runtime_checkpoint.apply_override():
            return
        context = replace(self._context, now=datetime.now(self.timezone))
        if not self.runtime_settings.grid_charging_allowed():
            context = replace(
                context,
                charge_plan=None,
                battery_charge_plans=[],
            )
        if not self.grid_status_monitor.allows_charging():
            context = replace(
                context,
                charge_plan=None,
                battery_charge_plans=[],
            )
        test_state = self.get_state(TEST_MODE_HELPER)
        automatic_allowed = test_state in (
            None, "", "unknown", "unavailable", "Uit"
        )
        self.scheduler.evaluate(
            context,
            automatic_allowed=automatic_allowed,
        )
        # ``context`` kan hierboven tijdelijk zijn begrensd door de actuele
        # netstatus. Bewaar die uitvoeringskopie niet als de definitieve
        # planning: anders wist een korte status ``unknown`` het laadplan en
        # kan OpenEMS na netherstel niets meer hervatten.

    def _schedule_telemetry_recovery(self):
        if self._telemetry_retry_pending:
            return
        self._telemetry_retry_pending = True
        self._telemetry_retry_generation += 1
        self.run_in(
            self._retry_after_telemetry,
            10,
            generation=self._telemetry_retry_generation,
        )

    def _retry_after_telemetry(self, kwargs):
        if int(kwargs["generation"]) != self._telemetry_retry_generation:
            return
        self._telemetry_retry_pending = False
        if str(self.get_state(EMS_ENABLED_HELPER)).lower() != "on":
            return
        self.execute_plan({})

    def _coordinated_self_consumption_tick(self, kwargs):
        """Laat geen zelfconsumptieregelaar een exportblok overschrijven."""
        if self.grid_status_monitor.planning_paused():
            return
        if self.grid_export_manager.controls_execution():
            return
        self.coordinated_self_consumption.tick(kwargs)
        self.coordinated_self_consumption.battery_selector.tick_native()

    def grid_status_restored(self):
        """Bouw na betrouwbaar netherstel altijd een verse planning."""
        if str(self.get_state(EMS_ENABLED_HELPER)).lower() == "on":
            self.calculate_plan({"force": True})
            self.run_in(self.execute_plan, 0)
        else:
            self.run_in(self.manual_controller.apply_current, 0)

    def grid_status_changed(self, previous, current):
        """Beoordeel iedere bevestigde netstatusovergang opnieuw."""
        if previous == current:
            return
        if current == "normal":
            self.grid_status_restored()
            return
        if str(self.get_state(EMS_ENABLED_HELPER)).lower() == "on":
            self.run_in(self.execute_plan, 0)

    def _marstek_control_result(self, event_name, data, kwargs):
        """Geef de atomaire MarsTek read-back aan de wachtende overgang."""
        handled = self.controller.handle_atomic_result(data)
        self.coordinated_self_consumption.handle_atomic_result(data)
        if not handled or not isinstance(data, dict):
            return
        request_id = data.get("request_id") or "zonder request-id"
        if not data.get("ok"):
            self.log(
                "[Modbus-bridge] Atomaire accu-opdracht mislukt | "
                f"request={request_id} | "
                f"fout={data.get('error') or 'onbekende bridgefout'}",
                level="ERROR",
            )
            return
        results = data.get("results") or []
        batteries = ", ".join(
            str(item.get("battery") or f"Accu {index + 1}")
            for index, item in enumerate(results)
            if isinstance(item, dict)
        )
        self.log(
            "[Modbus-bridge] Atomaire accu-opdracht bevestigd"
            f" | request={request_id}"
            f" | accu's={batteries or len(results)}"
        )

    def _coordinated_grid_changed(
        self, entity, attribute, old, new, kwargs
    ):
        """Regel direct op een verse snelle netmeting; 5 s blijft watchdog."""
        if old == new or new in (None, "", "unknown", "unavailable"):
            return
        if self.grid_export_manager.controls_execution():
            return
        self.coordinated_self_consumption.tick({"event_driven": True})

    def _grid_sensor_entity_changed(
        self, entity, attribute, old, new, kwargs
    ):
        """Verplaats de snelle listener direct naar een gewijzigde CT-sensor."""
        if old == new:
            return
        self._bind_fast_grid_listeners()
        self._planning_input_changed(entity, attribute, old, new, kwargs)

    def _bind_fast_grid_listeners(self):
        cancel = getattr(self, "cancel_listen_state", None)
        if callable(cancel):
            for handle in self._fast_grid_listener_handles:
                try:
                    cancel(handle)
                except (TypeError, ValueError):
                    pass
        self._fast_grid_listener_handles = []
        entities = {
            self.get_state(GRID_POWER_ENTITY_HELPER),
            self.get_state(GRID_REFERENCE_ENTITY_HELPER),
        }
        for entity in entities:
            if not entity or "." not in str(entity):
                continue
            handle = self.listen_state(
                self._coordinated_grid_changed, str(entity)
            )
            if handle is not None:
                self._fast_grid_listener_handles.append(handle)

    def monitor_system(self, kwargs):
        """Ververst SOC en dashboard zonder de planning te wijzigen."""
        if self._context is None:
            return
        try:
            battery = self.battery_manager.read()
            battery_energy_needed = battery.capacity * max(
                0.0, self._context.target_soc - battery.average_soc
            ) / 100
            needed_energy = self._net_energy(battery_energy_needed)
            hours_needed = (
                needed_energy / (battery.max_charge / 1000)
                if battery.max_charge > 0 else 0.0
            )
            context = replace(
                self._context,
                now=datetime.now(self.timezone),
                battery=battery,
                battery_energy_needed=battery_energy_needed,
                needed_energy=needed_energy,
                hours_needed=hours_needed,
            )
            self.scheduler.determine(context)
            self._context = context
            current_price = self.price_manager.get_current_price(
                context.future_prices
            )
            if current_price is not None:
                self._current_price = current_price
            self._publish_dashboard()
        except (TypeError, ValueError, KeyError) as error:
            self.log(f"Monitoring mislukt: {error}", level="WARNING")

    def log_system(self, kwargs):
        """Schrijft het uitgebreide rapport op een rustig interval."""
        if self._context is None:
            return
        context = replace(self._context, now=datetime.now(self.timezone))
        self.scheduler.determine(context)
        self.ems_logger.log_plan(
            context,
            scheduler_execution=self.scheduler.last_execution,
            scheduler_status=self.scheduler.status(),
        )

    def _schedule_execution_boundaries(self, context):
        self._cancel_boundary_timers()
        self._plan_generation += 1
        generation = self._plan_generation
        now = datetime.now(self.timezone)
        boundaries = {}
        for plan in context.battery_charge_plans:
            for segment in plan.segments:
                boundaries.setdefault(segment.start, False)
                boundaries[segment.end] = True
        if context.discharge_plan:
            for block in context.discharge_plan.blocks:
                boundaries.setdefault(block.start, False)
                boundaries[block.end] = True
        if context.export_plan:
            for block in context.export_plan.blocks:
                boundaries.setdefault(block.start, False)
                boundaries[block.end] = True

        for moment in sorted(boundary for boundary in boundaries if boundary > now):
            handle = self.run_at(
                self._exact_execution,
                moment,
                generation=generation,
                refresh_plan=boundaries[moment],
            )
            self._boundary_handles.append(handle)

    def _cancel_boundary_timers(self):
        timer_running = getattr(self, "timer_running", None)
        for handle in self._boundary_handles:
            if callable(timer_running):
                try:
                    if not timer_running(handle):
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
            try:
                self.cancel_timer(handle)
            except (ValueError, TypeError) as error:
                self.log(
                    f"[Debug] Oude planningstimer kon niet worden "
                    f"geannuleerd: {error}",
                    level="DEBUG",
                )
        self._boundary_handles = []

    def _exact_execution(self, kwargs):
        if int(kwargs["generation"]) != self._plan_generation:
            return
        self.grid_export_manager.finish_if_expired()
        self.execute_plan({})
        if kwargs.get("refresh_plan"):
            # Na een blokeinde ook het informatieve volgende blok verversen.
            self.run_in(self.calculate_plan, 2)

    def _publish_dashboard(self):
        if self._context is None or self._current_price is None:
            return
        self.dashboard_status.publish(
            context=self._context,
            scheduler_status=self.scheduler.status(),
            current_price=self._current_price,
            auto_control_enabled=self.controller.enabled,
            dashboard_charge_plan=self._dashboard_charge_plan,
            dashboard_battery_charge_plans=(
                self._dashboard_battery_charge_plans
            ),
            dashboard_charge_price_slots=(
                self._dashboard_charge_price_slots
            ),
            dashboard_discharge_price_slots=(
                self._dashboard_discharge_price_slots
            ),
            dashboard_discharge_profit_blocks=(
                self._dashboard_discharge_profit_blocks
            ),
        )
        checkpoint = getattr(self, "runtime_checkpoint", None)
        if checkpoint is not None:
            checkpoint.capture()

    def _planning_input_changed(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        self._input_change_generation += 1
        self.run_in(
            self._debounced_plan,
            2,
            generation=self._input_change_generation,
        )

    def _grid_charge_allowed_changed(
        self, entity, attribute, old, new, kwargs
    ):
        self._planning_input_changed(entity, attribute, old, new, kwargs)
        if old != new and str(new).lower() == "off":
            self.run_in(self.execute_plan, 0)

    def _debounced_plan(self, kwargs):
        if int(kwargs["generation"]) == self._input_change_generation:
            self.calculate_plan({"input_change": True})

    def _deferred_input_plan(self, kwargs):
        if int(kwargs["generation"]) != self._deferred_plan_generation:
            return
        self.calculate_plan({"force": True, "input_change": True})

    def _lock_started_export_interval(self, plan, now):
        """Voorkom dat een herplanning een reeds begonnen exportuur toevoegt."""
        if plan is None:
            return None
        locked = None
        previous = getattr(self, "_context", None)
        previous_plan = getattr(previous, "export_plan", None)
        if previous_plan is not None:
            locked = next(
                (
                    block for block in previous_plan.blocks
                    if block.start <= now < block.end
                ),
                None,
            )
        if locked is None:
            locked_reader = getattr(
                self.grid_export_manager, "locked_active_block", None
            )
            if callable(locked_reader):
                locked = locked_reader(now)

        # Zonder eerder vastgelegd blok zijn alleen nog niet begonnen blokken
        # geldig. Een vastgelegd blok behoudt exact zijn originele eindtijd.
        blocks = [block for block in plan.blocks if block.start >= now]
        if locked is not None:
            blocks = [
                block for block in blocks
                if block.end <= locked.start or block.start >= locked.end
            ]
            blocks.append(locked)
        blocks.sort(key=lambda block: block.start)
        return replace(plan, blocks=blocks)

    @staticmethod
    def _enforce_export_reserve_floor(plan, reserve_soc):
        """Laat ook een dynamische planning nooit onder de instelling komen."""
        if plan is None:
            return None
        floor = max(0.0, float(reserve_soc))
        blocks = [
            replace(
                block,
                reserve_soc=max(
                    floor,
                    float(
                        block.reserve_soc
                        if block.reserve_soc is not None
                        else plan.reserve_soc
                    ),
                ),
            )
            for block in plan.blocks
            if (
                block.reserve_soc is None
                or float(block.reserve_soc) + 1e-9 >= floor
            )
        ]
        return replace(
            plan,
            blocks=blocks,
            reserve_soc=max(floor, float(plan.reserve_soc)),
        )

    def _net_energy(self, battery_energy):
        charge_efficiencies = (
            self.runtime_settings.effective_efficiencies()[0]
        )
        efficiency = (
            sum(charge_efficiencies) / len(charge_efficiencies)
            if charge_efficiencies else CHARGE_EFFICIENCY
        )
        return (
            battery_energy / efficiency
            if efficiency > 0 else battery_energy
        )

    def _helper(self, name):
        entity_id = HELPERS[name]
        default = DEFAULTS[name]
        state = self.get_state(entity_id)
        if state in (None, "", "unknown", "unavailable"):
            self.log(
                f"{entity_id} niet beschikbaar; standaardwaarde "
                f"{default} wordt gebruikt.",
                level="WARNING",
            )
            return float(default)
        try:
            return float(state)
        except (TypeError, ValueError):
            self.log(
                f"Ongeldige waarde voor {entity_id}: {state}; "
                f"standaardwaarde {default} wordt gebruikt.",
                level="WARNING",
            )
            return float(default)

    def _openems_enabled_changed(self, entity, attribute, old, new, kwargs):
        self._apply_openems_enabled(new)

    def _apply_openems_enabled(self, state):
        requested = str(state).lower() == "on"
        enabled = AUTO_CONTROL_ENABLED and requested
        if enabled:
            self.scheduler.reset_charge_session()
            self.scheduler.reset_fault()
            self.controller.set_enabled(True)
            self.dashboard_status.publish_main_switch(True)
            self.scheduler.current_state = SchedulerState.IDLE
            self.manual_controller.publish(
                "Auto", "Automatische planning bestuurt de accu's"
            )
            self.log("OpenEMS bedieningsmodus: AUTO")
            if self._context is not None:
                self.run_in(self.execute_plan, 1)
            return

        self.scheduler.cancel_retry("OpenEMS hoofdschakelaar uit")
        # Stop ook alle nog geplande native-selectorverificaties wanneer de
        # gebruiker expliciet de centrale handmatige bediening overneemt.
        self.coordinated_self_consumption.deactivate(clear_latch=True)
        self.grid_export_manager.stop(safety=True)
        if self.controller.enabled:
            self.controller.set_stop(source="auto")
        self.scheduler.reset_charge_session()
        self.controller.set_enabled(False)
        self.dashboard_status.publish_main_switch(False)
        self.log("OpenEMS bedieningsmodus: HANDMATIG")
        self.run_in(self.manual_controller.apply_current, 1)

    def _test_mode_changed(self, entity, attribute, old, new, kwargs):
        if new != "Uit":
            self.scheduler.cancel_retry("controller-testmodus actief")
            return
        if str(self.get_state(EMS_ENABLED_HELPER)).lower() != "on":
            self.run_in(self.manual_controller.apply_current, 1)
            return
        self.scheduler.current_state = SchedulerState.IDLE
        self.scheduler.reset_fault()
        self.run_in(self.execute_plan, 1)

    def _self_consumption_mode_changed(
        self, entity, attribute, old, new, kwargs
    ):
        if old == new:
            return
        self._self_consumption_restart_generation += 1
        # De overgang verloopt altijd via Stop. Daarna wordt alleen opnieuw
        # zelfconsumptie gestart als dat op dit moment werkelijk gewenst is.
        self.coordinated_self_consumption.deactivate(clear_latch=True)
        self.controller.invalidate_last_command("zelfconsumptieregeling gewijzigd")
        current_manual = self.get_state(MANUAL_MODE_HELPER)
        automatic_self_consumption = self.scheduler.current_state in {
            SchedulerState.DISCHARGING,
            SchedulerState.SELF_CONSUMPTION,
        }
        if current_manual == "Zelfconsumptie" or automatic_self_consumption:
            source = "auto" if self.controller.enabled else "manual"
            self.controller.set_stop(source=source)
            self.run_in(
                self._restart_self_consumption,
                5,
                source=source,
                generation=self._self_consumption_restart_generation,
            )

    def _restart_self_consumption(self, kwargs):
        if (
            int(kwargs["generation"])
            != self._self_consumption_restart_generation
        ):
            return
        current_manual = self.get_state(MANUAL_MODE_HELPER)
        automatic_self_consumption = self.scheduler.current_state in {
            SchedulerState.DISCHARGING,
            SchedulerState.SELF_CONSUMPTION,
        }
        if current_manual != "Zelfconsumptie" and not automatic_self_consumption:
            self.log(
                "Verouderde herstart naar zelfconsumptie genegeerd; "
                "de actuele planning heeft inmiddels een andere opdracht"
            )
            return
        self.controller.invalidate_last_command("veilige modusovergang")
        self.controller.set_self_consumption(source=kwargs["source"])

    def _sample_financial_results(self, kwargs=None):
        if self._context is None or self._current_price is None:
            return
        try:
            (
                charge_efficiencies,
                discharge_efficiencies,
                _efficiency_details,
            ) = self.runtime_settings.effective_efficiencies()
            self.financial_tracker.sample(
                now=datetime.now(self.timezone),
                price=float(self._current_price),
                soc_values=self._context.battery.soc_values,
                capacities=self._context.battery.capacities,
                minimum_soc=self._context.minimum_soc,
                charge_efficiencies=charge_efficiencies,
                discharge_efficiencies=discharge_efficiencies,
                export_price=self.runtime_settings.grid_export_price(),
            )
            self.efficiency_learner.sample(
                now=datetime.now(self.timezone),
                soc_values=self._context.battery.soc_values,
                capacities=self._context.battery.capacities,
            )
        except (OSError, TypeError, ValueError, KeyError) as error:
            self.log(
                f"Financiële resultaatmeting mislukt: {error}",
                level="WARNING",
            )
