from pathlib import Path


APP_ROOT = (
    Path(__file__).parents[2]
    / "addon_configs"
    / "a0d7b954_appdaemon"
    / "apps"
    / "OpenEMS-M"
)


def test_first_plan_waits_until_runtime_recovery_is_ready():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    initialize = source.split("def initialize(self):", 1)[1].split(
        "\n    def ", 1
    )[0]
    assert "self.calculate_plan({})" not in initialize
    checkpoint = (APP_ROOT / "runtime_checkpoint.py").read_text(
        encoding="utf-8"
    )
    assert 'self.app.calculate_plan({"recovery_ready": True, "force": True})' in checkpoint


def test_started_export_hour_is_locked_against_late_replanning():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    assert "def _lock_started_export_interval" in source
    assert "block.start >= now" in source
    assert "locked_active_block" in source


def test_started_self_consumption_block_is_locked_during_replanning():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    assert "lock_started_interval(" in source
    assert "previous_self_consumption_plan" in source


def test_stale_telemetry_rechecks_every_ten_seconds_until_recovered():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    assert "self._schedule_telemetry_recovery()" in source
    assert "def _retry_after_telemetry" in source
    assert "self.execute_plan({})" in source


def test_battery_power_age_matches_slow_sensor_reporting_interval():
    source = (APP_ROOT / "ems_config.py").read_text(encoding="utf-8")
    assert "BATTERY_POWER_DATA_MAX_AGE_SECONDS = 120" in source


def test_delayed_self_consumption_restart_is_generation_guarded():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    section = source.split(
        "def _self_consumption_mode_changed", 1
    )[1].split("def _sample_financial_results", 1)[0]
    assert "self._self_consumption_restart_generation += 1" in section
    assert "generation=self._self_consumption_restart_generation" in section
    assert 'int(kwargs["generation"])' in section
    assert "automatic_self_consumption" in section
    assert "Verouderde herstart naar zelfconsumptie genegeerd" in section


def test_discharge_plan_is_not_disabled_when_charge_plan_is_absent():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    assert (
        "self_consumption_plan = "
        "self.self_consumption_planner.calculate(" in source
    )


def test_export_fallback_never_uses_the_next_future_charge_plan_price():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    section = source.split("reference_charge_price = (", 1)[1].split(
        "export_efficiency", 1
    )[0]
    assert "recent_charge_cost" in section
    assert "current_planning_price" in section
    assert '"average_charge_price": float(' in source
    export = source.split("export_kwargs = {", 1)[1].split(
        "remaining_export_prices", 1
    )[0]
    assert '"average_charge_price": reference_charge_price' in export
    assert "self_consumption_reference_price" not in export


def test_charge_and_self_consumption_windows_are_hard_exclusive():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    assert "self_consumption_reference_price" in source
    assert '"average_charge_price": reference_charge_price' in source
    call = source.split(
        "self.self_consumption_planner.calculate(", 1
    )[1].split(")", 1)[0]
    assert "excluded_intervals=excluded_charge_intervals" in call
    assert "economic_charge_intervals" in source
    assert (
        "export_plan = "
        "self.discharge_planner.calculate_with_energy_resets(" in source
    )
    assert (
        "visual_discharge_plan if charge_plan is not None else None"
        not in source
    )
    assert "average_inventory_cost()" in source
    assert "future_prices=remaining_prices" in source
    assert "remaining_export_prices = (" in source
    assert "future_prices=remaining_export_prices" in source
    assert "get_remaining_prices(" in source


def test_export_planner_uses_future_charge_energy_resets():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    assert "calculate_with_energy_resets(" in source
    assert "export_energy_resets" in source
    assert "forecast_battery_plan.battery_energy_needed" in source
    assert "forecast_battery_plan.capacity" in source
    assert "for segment in forecast_battery_plan.segments" in source
    assert "if charge_completed:" in source
    assert "if soc_after_charge != projected_soc_values:" not in source


def test_charge_planner_only_receives_dark_green_economic_prices():
    source = (APP_ROOT / "marstek_ems.py").read_text(encoding="utf-8")
    assert "restrict_to_economic_slots(" in source
    assert "prices=actionable_charge_prices" in source
