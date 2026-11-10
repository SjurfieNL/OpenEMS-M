from types import SimpleNamespace

from ems_config import (
    FRANK_ALL_IN_ENTITY_HELPER,
    FRANK_MARKET_ENTITY_HELPER,
    LEGACY_MARKET_SENSORS,
    LEGACY_PRICE_SENSORS,
    MARKET_PRICE_SENSOR_CANDIDATES,
    PRICE_SENSOR,
)
from settings_manager import SettingsManager


class App:
    def __init__(self, states):
        self.states = dict(states)
        self.logs = []
        self.price_manager = SimpleNamespace(sensor=LEGACY_PRICE_SENSORS[0])
        self.export_price_manager = SimpleNamespace(sensor=LEGACY_MARKET_SENSORS[0])

    def get_state(self, entity, attribute=None):
        return self.states.get(entity)

    def call_service(self, service, **data):
        if service == "input_text/set_value":
            self.states[data["entity_id"]] = data["value"]

    def log(self, message, level="INFO"):
        self.logs.append((level, message))


def manager(states):
    app = App(states)
    instance = SettingsManager.__new__(SettingsManager)
    instance.app = app
    instance._set = lambda entity, value: app.states.__setitem__(entity, value)
    return instance, app


def test_legacy_all_in_name_moves_to_new_when_new_exists():
    m, app = manager({
        FRANK_ALL_IN_ENTITY_HELPER: LEGACY_PRICE_SENSORS[0],
        FRANK_MARKET_ENTITY_HELPER: LEGACY_MARKET_SENSORS[0],
        PRICE_SENSOR: "unavailable",
    })
    m._migrate_frank_entities()
    assert app.states[FRANK_ALL_IN_ENTITY_HELPER] == PRICE_SENSOR
    assert app.price_manager.sensor == PRICE_SENSOR
    # marktprijs: geen nieuwe kandidaat bestaat -> oude naam blijft staan
    assert app.states[FRANK_MARKET_ENTITY_HELPER] == LEGACY_MARKET_SENSORS[0]
    assert any(level == "WARNING" for level, _ in app.logs)


def test_market_candidate_used_when_it_exists():
    m, app = manager({
        FRANK_ALL_IN_ENTITY_HELPER: PRICE_SENSOR,
        FRANK_MARKET_ENTITY_HELPER: LEGACY_MARKET_SENSORS[0],
        MARKET_PRICE_SENSOR_CANDIDATES[0]: "0.24331",
    })
    m._migrate_frank_entities()
    assert app.states[FRANK_MARKET_ENTITY_HELPER] == MARKET_PRICE_SENSOR_CANDIDATES[0]
    assert app.export_price_manager.sensor == MARKET_PRICE_SENSOR_CANDIDATES[0]
    assert MARKET_PRICE_SENSOR_CANDIDATES[0].endswith("huidige_elektriciteit_inkoopprijs")


def test_working_legacy_entity_is_left_alone():
    m, app = manager({
        FRANK_ALL_IN_ENTITY_HELPER: LEGACY_PRICE_SENSORS[0],
        LEGACY_PRICE_SENSORS[0]: "0.30",
        PRICE_SENSOR: "0.30",
    })
    m._migrate_frank_entities()
    assert app.states[FRANK_ALL_IN_ENTITY_HELPER] == LEGACY_PRICE_SENSORS[0]


def test_custom_user_entity_is_never_overwritten():
    m, app = manager({
        FRANK_ALL_IN_ENTITY_HELPER: "sensor.mijn_eigen_prijs",
        PRICE_SENSOR: "0.30",
    })
    m._migrate_frank_entities()
    assert app.states[FRANK_ALL_IN_ENTITY_HELPER] == "sensor.mijn_eigen_prijs"


def test_new_default_is_used_in_code_and_panel():
    import pathlib
    panel = pathlib.Path(
        "config/custom_components/openems_m_panel/frontend/openems-panel.js"
    ).read_text(encoding="utf-8")
    assert PRICE_SENSOR in panel
    assert "meterkast_frank_energie_prijzen" not in panel
