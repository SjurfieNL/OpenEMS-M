from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from financial_tracker import FinancialTracker


class Settings:
    def home_entity(self):
        return "sensor.home"

    def grid_reference_entity(self):
        return "sensor.grid"


class App:
    def __init__(self):
        self.states = {
            "sensor.home": "1000",
            "sensor.battery": "0",
            "sensor.grid": "0",
        }
        self.published = {}
        self.logs = []

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, message, **kwargs):
        self.logs.append((message, kwargs))


def test_financial_results_survive_restart_and_use_inventory_cost():
    zone = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026, 7, 18, 10, tzinfo=zone)
    with TemporaryDirectory() as directory:
        path = Path(directory) / "results.json"
        app = App()
        tracker = FinancialTracker(
            app, Settings(),
            [{"actual_power": "sensor.battery"}],
            zone, data_file=path,
        )
        tracker.sample(
            start, 0.10, [50], [5], 20, [0.9], [0.9]
        )
        app.states["sensor.battery"] = "-1000"
        tracker.sample(
            start + timedelta(minutes=1), 0.10,
            [50], [5], 20, [0.9], [0.9]
        )

        restarted = FinancialTracker(
            app, Settings(),
            [{"actual_power": "sensor.battery"}],
            zone, data_file=path,
        )
        app.states["sensor.battery"] = "1000"
        restarted.sample(
            start + timedelta(minutes=2), 0.30,
            [50], [5], 20, [0.9], [0.9]
        )
        restarted.sample(
            start + timedelta(minutes=3), 0.30,
            [50], [5], 20, [0.9], [0.9]
        )

        today = restarted.data["days"]["2026-07-18"]
        assert today["laadenergie_kwh"] > 0
        assert today["ontlaadenergie_kwh"] > 0
        assert today["gemeten_s"] > 0
        assert path.exists()


def test_power_uses_trapezoid_and_quality_is_published():
    zone = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026, 7, 18, 10, tzinfo=zone)
    with TemporaryDirectory() as directory:
        app = App()
        tracker = FinancialTracker(
            app, Settings(), [{"actual_power": "sensor.battery"}],
            zone, data_file=Path(directory) / "results.json",
        )
        app.states["sensor.battery"] = "0"
        tracker.sample(
            start, 0.10, [50], [5], 20, [0.9], [0.9]
        )
        app.states["sensor.battery"] = "-1000"
        tracker.sample(
            start + timedelta(minutes=1), 0.10,
            [50], [5], 20, [0.9], [0.9]
        )

        today = tracker.data["days"]["2026-07-18"]
        assert abs(today["laadenergie_kwh"] - (0.5 / 60)) < 1e-9
        published = app.published[tracker.STATUS_SENSOR][1]["vandaag"]
        assert published["meetdekking_pct"] == 100.0
        assert published["kwaliteitslabel"] == "hoog"


def test_grid_energy_and_return_revenue_are_measured_and_published():
    zone = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026, 7, 18, 10, tzinfo=zone)
    with TemporaryDirectory() as directory:
        app = App()
        tracker = FinancialTracker(
            app, Settings(), [{"actual_power": "sensor.battery"}],
            zone, data_file=Path(directory) / "results.json",
        )
        app.states["sensor.grid"] = "-1000"
        tracker.sample(
            start, 0.10, [50], [5], 20, [0.9], [0.9],
            export_price=0.20,
        )
        tracker.sample(
            start + timedelta(hours=1), 0.10,
            [50], [5], 20, [0.9], [0.9],
            export_price=0.20,
        )

        attrs = app.published[tracker.STATUS_SENSOR][1]
        today = attrs["vandaag"]
        assert today["teruglevering_kwh"] == round(1 / 30, 4)
        assert today["terugleveropbrengst_eur"] == round(0.20 / 30, 4)
        assert "gisteren" in attrs
        assert "afgelopen_7_dagen" in attrs
        assert "opgeslagen_energie" in attrs


def test_corrupt_financial_file_is_backed_up_and_reported():
    zone = ZoneInfo("Europe/Amsterdam")
    with TemporaryDirectory() as directory:
        path = Path(directory) / "results.json"
        path.write_text("{geen geldige json", encoding="utf-8")
        app = App()

        tracker = FinancialTracker(
            app, Settings(), [{"actual_power": "sensor.battery"}],
            zone, data_file=path,
        )

        assert tracker.data["version"] == 5
        assert not path.exists()
        assert list(Path(directory).glob("results.corrupt-*.json"))
        assert any("onleesbaar" in message for message, _ in app.logs)


def test_recent_charge_cost_uses_only_measured_past_charge_energy():
    zone = ZoneInfo("Europe/Amsterdam")
    with TemporaryDirectory() as directory:
        tracker = FinancialTracker(
            App(), Settings(), [{"actual_power": "sensor.battery"}],
            zone, data_file=Path(directory) / "results.json",
        )
        tracker.data["days"] = {
            "2026-07-29": {
                "laadenergie_kwh": 1.0,
                "laadkosten_eur": 0.15,
            },
            "2026-07-30": {
                "laadenergie_kwh": 2.0,
                "laadkosten_eur": 0.36,
            },
        }

        assert abs(tracker.recent_charge_cost() - 0.17) < 1e-9


def test_inventory_reconciles_gradually_to_actual_usable_soc():
    zone = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 7, 18, 10, tzinfo=zone)
    with TemporaryDirectory() as directory:
        tracker = FinancialTracker(
            App(), Settings(), [{"actual_power": "sensor.battery"}],
            zone, data_file=Path(directory) / "results.json",
        )
        tracker.data["inventory"] = [{"usable_kwh": 1.0, "cost_eur": 0.20}]
        tracker.data["last_inventory_reconcile"] = None

        tracker._reconcile_inventory(now, [80], [5], 20, 0.30)

        assert tracker.data["inventory"][0]["usable_kwh"] == 1.25
        assert tracker.data["inventory"][0]["cost_eur"] == 0.25
        tracker._reconcile_inventory(
            now + timedelta(minutes=5), [80], [5], 20, 0.30
        )
        assert tracker.data["inventory"][0]["usable_kwh"] == 1.25
