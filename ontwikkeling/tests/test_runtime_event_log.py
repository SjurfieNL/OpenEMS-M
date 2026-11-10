from pathlib import Path
import json
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from runtime_event_log import RuntimeEventLog


class App:
    def __init__(self):
        self.states = {}
        self.logs = []

    def set_state(self, entity, state, attributes, replace):
        self.states[entity] = {
            "state": state,
            "attributes": attributes,
            "replace": replace,
        }

    def log(self, message, **kwargs):
        self.logs.append((message, kwargs))


def test_runtime_events_are_persisted_and_published_per_battery():
    app = App()
    with TemporaryDirectory() as directory:
        path = Path(directory) / "events.json"
        log = RuntimeEventLog(
            app, ZoneInfo("Europe/Amsterdam"), data_file=path
        )
        log.record(
            "warning",
            "Accu 3 wijkt af",
            "Herstelpoging gestart",
            battery_index=2,
            battery_name="Accu 3",
            details="werkelijk vermogen=470 W, verwacht circa 0 W",
            expected_state="stopped",
            attempt=1,
        )

        assert path.exists()
        sensor = app.states[RuntimeEventLog.SENSOR]
        assert sensor["attributes"]["aantal"] == 1
        assert (
            sensor["attributes"]["aantallen_per_accu"]["Accu 3"][
                "waarschuwingen"
            ]
            == 1
        )
        event = sensor["attributes"]["gebeurtenissen"][0]
        assert event["accu_nummer"] == 3
        assert event["herstelpoging"] == 1
        assert "470 W" in event["details"]


def test_runtime_event_log_survives_restart():
    app = App()
    with TemporaryDirectory() as directory:
        path = Path(directory) / "events.json"
        first = RuntimeEventLog(
            app, ZoneInfo("Europe/Amsterdam"), data_file=path
        )
        first.record(
            "recovered",
            "Accu 3 hersteld",
            "Verificatie weer in orde",
            battery_index=2,
            battery_name="Accu 3",
        )
        second = RuntimeEventLog(
            app, ZoneInfo("Europe/Amsterdam"), data_file=path
        )

        assert len(second.data["events"]) == 1
        assert second.data["events"][0]["niveau"] == "recovered"


def test_repeated_events_are_compacted_and_history_can_be_cleared():
    app = App()
    with TemporaryDirectory() as directory:
        path = Path(directory) / "events.json"
        log = RuntimeEventLog(
            app, ZoneInfo("Europe/Amsterdam"), data_file=path
        )
        for _ in range(2):
            log.record(
                "warning",
                "Accu 3 wijkt af",
                "Herstelcontrole ingepland",
                battery_index=2,
                battery_name="Accu 3",
                expected_state="self_consumption",
            )

        sensor = app.states[RuntimeEventLog.SENSOR]
        assert RuntimeEventLog.RETENTION_DAYS == 30
        assert RuntimeEventLog.MAX_EVENTS == 300
        assert sensor["attributes"]["aantal"] == 1
        assert sensor["attributes"]["aantal_meldingen"] == 2
        assert sensor["attributes"]["gebeurtenissen"][0]["aantal"] == 2
        assert (
            sensor["attributes"]["aantallen_per_accu"]["Accu 3"][
                "waarschuwingen"
            ]
            == 2
        )

        log.clear()

        assert log.data["events"] == []
        assert (
            app.states[RuntimeEventLog.SENSOR]["attributes"]["aantal"]
            == 0
        )


def test_sensor_attributes_stay_below_home_assistant_recorder_limit():
    app = App()
    with TemporaryDirectory() as directory:
        path = Path(directory) / "events.json"
        log = RuntimeEventLog(
            app, ZoneInfo("Europe/Amsterdam"), data_file=path
        )
        for index in range(100):
            log.record(
                "warning",
                f"Unieke runtime-afwijking {index}",
                "Uitgebreide samenvatting " + ("s" * 500),
                battery_index=index % 3,
                battery_name=f"Accu {(index % 3) + 1}",
                details="Technische diagnose " + ("d" * 2000),
                expected_state=f"status-{index}",
            )

        attributes = app.states[RuntimeEventLog.SENSOR]["attributes"]
        encoded = json.dumps(
            attributes,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")

        assert len(log.data["events"]) == 100
        assert len(encoded) <= RuntimeEventLog.MAX_PUBLISHED_ATTRIBUTE_BYTES
        assert attributes["aantal"] == 100
        assert attributes["historie_ingekort"] is True
        assert attributes["aantal_gepubliceerd"] < 100
        assert len(attributes["gebeurtenissen"][0]["details"]) == 1200
