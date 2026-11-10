from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from battery_health_manager import BatteryHealthManager
from battery_manager import BatteryManager
from ems_config import BATTERIES, BATTERY_DISABLED_HELPERS
from runtime_settings import RuntimeSettings
from telemetry_freshness import TelemetryFreshnessGuard

TZ = ZoneInfo("Europe/Amsterdam")
SRC = Path(__file__).resolve().parents[2] / (
    "addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/marstek_ems.py"
)


class App:
    def __init__(self, states=None):
        self.states = states or {}
        self.published = {}

    def get_state(self, entity, attribute=None):
        if attribute == "all":
            return None
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, *args, **kwargs):
        pass


def _all_on(app):
    for battery in BATTERIES:
        for key in (
            "soc", "capacity", "max_charge_power", "max_discharge_power"
        ):
            app.states[battery[key]] = "50" if key == "soc" else "5000"
    return app


def test_four_helpers_exist():
    assert len(BATTERY_DISABLED_HELPERS) == len(BATTERIES) == 4
    assert BATTERY_DISABLED_HELPERS[3] == (
        "input_boolean.openems_accu4_uitgeschakeld"
    )


def test_runtime_settings_disabled_flags():
    app = App()
    settings = RuntimeSettings(app)
    assert settings.disabled_battery_indexes() == []
    app.states[BATTERY_DISABLED_HELPERS[3]] = "unavailable"
    assert settings.disabled_battery_indexes() == []
    app.states[BATTERY_DISABLED_HELPERS[3]] = "on"
    app.states[BATTERY_DISABLED_HELPERS[1]] = "on"
    assert settings.disabled_battery_indexes() == [1, 3]
    app.states[BATTERY_DISABLED_HELPERS[1]] = "off"
    assert settings.disabled_battery_indexes() == [3]


def test_validate_skips_disabled_battery():
    app = App()
    settings = RuntimeSettings(app)
    before = [p for p in settings.validate() if "Accu 4" in p]
    assert before
    app.states[BATTERY_DISABLED_HELPERS[3]] = "on"
    after = settings.validate()
    assert not [p for p in after if "Accu 4" in p]
    assert [p for p in after if "Accu 1" in p]


def test_battery_manager_unchanged_without_disabled():
    app = _all_on(App())
    state = BatteryManager(app, BATTERIES).read()
    assert state.capacities == [5000.0] * 4
    assert state.average_soc == 50.0
    assert all(state.available)


def test_battery_manager_unavailable_still_raises_when_enabled():
    app = _all_on(App())
    app.states[BATTERIES[3]["soc"]] = "unavailable"
    try:
        BatteryManager(app, BATTERIES, lambda: []).read()
    except ValueError:
        return
    raise AssertionError("ValueError verwacht")


def test_battery_manager_disabled_offline_battery():
    app = _all_on(App())
    for key in ("soc", "capacity", "max_charge_power", "max_discharge_power"):
        app.states[BATTERIES[3][key]] = "unavailable"
    app.states[BATTERIES[0]["soc"]] = "30"
    state = BatteryManager(app, BATTERIES, lambda: [3]).read()
    assert state.capacities[3] == 0.0
    assert state.max_charge_values[3] == 0.0
    assert state.max_discharge_values[3] == 0.0
    assert state.available == [True, True, True, False]
    assert abs(state.average_soc - (30 + 50 + 50) / 3) < 1e-6
    assert state.capacity == 15000.0


def _health(path, disabled):
    return BatteryHealthManager(
        App(), [{"name": f"Accu {i}"} for i in range(1, 5)], TZ,
        data_file=Path(path), disabled_provider=lambda: disabled,
    )


def test_health_manager_disabled_battery():
    with TemporaryDirectory() as tmp:
        disabled = [3]
        health = _health(Path(tmp) / "h.json", disabled)
        assert not health.is_available(3)
        assert health.is_available(0)
        assert health.power_limit(3) == 0.0
        assert health.filter_indexes([0, 1, 2, 3]) == [0, 1, 2]
        assert health.filter_powers([100, 100, 100, 100]) == [
            100, 100, 100, 0.0
        ]
        health.publish()
        state, attrs = health.app.published[health.STATUS_SENSOR]
        assert "1 accu uitgeschakeld" in state
        assert attrs["accus"][3]["status"] == "disabled"
        disabled.clear()
        health.publish()
        state, attrs = health.app.published[health.STATUS_SENSOR]
        assert state == "alle accu's beschikbaar"
        assert health.is_available(3)


class TStettings:
    def grid_entity(self):
        return "sensor.grid"


def test_telemetry_skips_disabled_battery():
    app = App({"sensor.grid": "10", "sensor.s1": "50", "sensor.p1": "0"})
    batteries = [
        {"name": "Accu 1", "soc": "sensor.s1", "actual_power": "sensor.p1"},
        {"name": "Accu 2", "soc": "sensor.s2", "actual_power": "sensor.p2"},
    ]
    guard = TelemetryFreshnessGuard(
        app, TStettings(), batteries, disabled_provider=lambda: [1]
    )
    result = guard.check()
    assert result["ok"]
    assert not any("Accu 2" in item["naam"] for item in result["measurements"])


def test_marstek_ems_wiring_source():
    text = SRC.read_text(encoding="utf-8")
    for token in (
        "BATTERY_DISABLED_HELPERS",
        "disabled_provider=self.runtime_settings.disabled_battery_indexes",
        "_battery_disabled_changed",
        "_apply_battery_disable",
        "_apply_disabled_batteries_at_startup",
        "isolate_batteries",
    ):
        assert token in text, token


def test_efficiency_learner_pads_data_saved_with_three_batteries():
    import json
    from efficiency_learner import EfficiencyLearner
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "z.json"
        three = [{"name": f"A{i}", "actual_power": f"sensor.p{i}"} for i in range(3)]
        four = three + [{"name": "A3", "actual_power": "sensor.p3"}]
        old = EfficiencyLearner(App(), three, TZ, data_file=path)
        old.store.save(old.data)
        assert len(json.loads(path.read_text())["batteries"]) == 3
        app = App({f"sensor.p{i}": "0" for i in range(4)})
        learner = EfficiencyLearner(app, four, TZ, data_file=path)
        from datetime import datetime
        learner.sample(datetime.now(TZ), [50, 50, 50, 50], [5000] * 4)
        assert len(learner.data["batteries"]) == 4
        assert "sensor.openems_accu_4_zelfgeleerd_rendement" in app.published
