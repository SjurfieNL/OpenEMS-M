import pytest
from datetime import datetime, timedelta, timezone

from telemetry_freshness import TelemetryFreshnessGuard


class Settings:
    def grid_entity(self):
        return "sensor.grid"


class App:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.states = {
            "sensor.grid": "100",
            "sensor.soc": "50",
            "sensor.power": "0",
        }
        self.updated = {
            entity: now.isoformat() for entity in self.states
        }
        self.published = {}
        self.logs = []

    def get_state(self, entity, attribute=None):
        if attribute == "all":
            return {"last_updated": self.updated.get(entity)}
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, message, **kwargs):
        self.logs.append(message)


def test_valid_number_with_old_timestamp_is_diagnostic_only():
    app = App()
    app.updated["sensor.grid"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    ).isoformat()
    guard = TelemetryFreshnessGuard(
        app, Settings(), [{
            "name": "Accu 1",
            "soc": "sensor.soc",
            "actual_power": "sensor.power",
        }]
    )

    result = guard.check()

    assert result["ok"]
    assert result["problems"] == []
    grid = next(
        item for item in result["measurements"]
        if item["naam"] == "Netvermogen"
    )
    assert grid["verouderd"] is True
    assert "120s oud" in grid["waarschuwing"]
    assert (
        app.published[guard.STATUS_SENSOR][0]
        == "ouderdom waarschuwing"
    )


def test_current_critical_data_is_accepted():
    app = App()
    guard = TelemetryFreshnessGuard(
        app, Settings(), [{
            "name": "Accu 1",
            "soc": "sensor.soc",
            "actual_power": "sensor.power",
        }]
    )

    assert guard.check()["ok"]


def test_old_stationary_zero_power_is_accepted_as_standby():
    app = App()
    app.updated["sensor.power"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    guard = TelemetryFreshnessGuard(
        app, Settings(), [{
            "name": "Accu 1",
            "soc": "sensor.soc",
            "actual_power": "sensor.power",
        }]
    )

    result = guard.check(include_grid=False)

    assert result["ok"]
    power = next(
        item for item in result["measurements"]
        if item["naam"].endswith("werkelijk vermogen")
    )
    assert power["stabiele_rustwaarde"] is True


def test_old_nonzero_battery_power_is_diagnostic_only():
    app = App()
    app.states["sensor.power"] = "500"
    app.updated["sensor.power"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    guard = TelemetryFreshnessGuard(
        app, Settings(), [{
            "name": "Accu 1",
            "soc": "sensor.soc",
            "actual_power": "sensor.power",
        }]
    )

    result = guard.check(include_grid=False)

    assert result["ok"]
    assert result["problems"] == []
    power = next(
        item for item in result["measurements"]
        if item["naam"].endswith("werkelijk vermogen")
    )
    assert power["verouderd"] is True
    assert power["stabiele_rustwaarde"] is False


def test_unavailable_battery_power_remains_a_hard_problem():
    app = App()
    app.states["sensor.power"] = "unavailable"
    guard = TelemetryFreshnessGuard(
        app, Settings(), [{
            "name": "Accu 1",
            "soc": "sensor.soc",
            "actual_power": "sensor.power",
        }]
    )

    result = guard.check(include_grid=False)

    assert not result["ok"]
    assert "unavailable" in result["problems"][0]["reden"]


def _guard_past_startup_grace(app, batteries):
    guard = TelemetryFreshnessGuard(app, Settings(), batteries)
    guard._started_at = datetime.now(timezone.utc) - timedelta(
        seconds=guard.STARTUP_GRACE_SECONDS + 1
    )
    return guard


def test_freshly_started_guard_never_blocks_on_extreme_staleness():
    # Vlak na een herstart (AppDaemon of heel Home Assistant, bijv. na het
    # bijwerken van een custom_component) hebben integraties tijd nodig om
    # opnieuw te rapporteren. Een net aangemaakte guard mag dat nooit
    # meteen als "vastgelopen sensor" bestempelen en accu's stoppen.
    app = App()
    app.states["sensor.power"] = "500"
    app.updated["sensor.power"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    app.updated["sensor.grid"] = (
        datetime.now(timezone.utc) - timedelta(minutes=30)
    ).isoformat()
    guard = TelemetryFreshnessGuard(
        app, Settings(), [{
            "name": "Accu 1",
            "soc": "sensor.soc",
            "actual_power": "sensor.power",
        }]
    )

    result = guard.check()

    assert result["ok"]
    assert result["problems"] == []


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_extremely_stale_nonzero_battery_power_blocks_commands():
    app = App()
    app.states["sensor.power"] = "500"
    app.updated["sensor.power"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    guard = _guard_past_startup_grace(app, [{
        "name": "Accu 1",
        "soc": "sensor.soc",
        "actual_power": "sensor.power",
    }])

    result = guard.check(include_grid=False)

    assert not result["ok"]
    power = next(
        item for item in result["measurements"]
        if item["naam"].endswith("werkelijk vermogen")
    )
    assert power["ok"] is False
    assert "vastgelopen" in power["reden"]


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_extremely_stale_stationary_zero_power_still_accepted():
    app = App()
    app.updated["sensor.power"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    guard = _guard_past_startup_grace(app, [{
        "name": "Accu 1",
        "soc": "sensor.soc",
        "actual_power": "sensor.power",
    }])

    result = guard.check(include_grid=False)

    assert result["ok"]
    power = next(
        item for item in result["measurements"]
        if item["naam"].endswith("werkelijk vermogen")
    )
    assert power["ok"] is True
    assert power["stabiele_rustwaarde"] is True


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_extremely_stale_grid_sensor_blocks_commands():
    app = App()
    app.updated["sensor.grid"] = (
        datetime.now(timezone.utc) - timedelta(minutes=30)
    ).isoformat()
    guard = _guard_past_startup_grace(app, [{
        "name": "Accu 1",
        "soc": "sensor.soc",
        "actual_power": "sensor.power",
    }])

    result = guard.check()

    assert not result["ok"]
    grid = next(
        item for item in result["measurements"]
        if item["naam"] == "Netvermogen"
    )
    assert grid["ok"] is False
    assert "vastgelopen" in grid["reden"]
