from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from discharge_planner import DischargePlanner
from energy_scheduler import EnergyScheduler
from financial_tracker import FinancialTracker
from models import BatteryState, ChargePlan, EMSContext, SchedulerState


class DayApp:
    def __init__(self):
        self.states = {
            "sensor.battery": "0",
            "sensor.home": "500",
            "sensor.grid": "500",
        }
        self.published = {}

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, message, **kwargs):
        pass


class DaySettings:
    def home_entity(self):
        return "sensor.home"

    def grid_reference_entity(self):
        return "sensor.grid"


def test_complete_simulated_day_connects_plan_execution_and_finances():
    zone = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026, 7, 18, 0, tzinfo=zone)
    price_values = [
        0.18, 0.15, 0.09, 0.08, 0.10, 0.14,
        0.18, 0.20, 0.21, 0.19, 0.17, 0.16,
        0.15, 0.16, 0.18, 0.22, 0.27, 0.34,
        0.42, 0.39, 0.31, 0.25, 0.21, 0.19,
    ]
    price_data = [
        {
            "from": (start + timedelta(hours=index)).isoformat(),
            "till": (start + timedelta(hours=index + 1)).isoformat(),
            "price": price,
        }
        for index, price in enumerate(price_values)
    ]
    discharge_plan = DischargePlanner("Europe/Amsterdam").calculate(
        future_prices=price_data,
        average_charge_price=0.09,
        discharge_margin=0.08,
        minimum_soc=20,
        names=["Accu 1"],
        soc_values=[70],
        capacities=[5.0],
        max_discharge_values=[2500],
        discharge_efficiencies=[0.92],
        minimum_block_minutes=30,
    )
    charge_plan = ChargePlan(
        start=start + timedelta(hours=2),
        end=start + timedelta(hours=4),
        power=1000,
        hours=2,
        average_price=0.085,
        cheapest_price=0.08,
        price_limit=0.10,
    )
    battery = BatteryState(
        names=["Accu 1"],
        soc_values=[70],
        capacities=[5.0],
        capacity=5.0,
        average_soc=70,
        stored_energy=3.5,
        max_charge_values=[2500],
        max_discharge_values=[2500],
        max_charge=2500,
        max_discharge=2500,
    )
    app = DayApp()
    scheduler = EnergyScheduler(
        app, SimpleNamespace(batteries=[{"name": "Accu 1"}])
    )
    with TemporaryDirectory() as directory:
        tracker = FinancialTracker(
            app,
            DaySettings(),
            [{"actual_power": "sensor.battery"}],
            zone,
            data_file=Path(directory) / "results.json",
        )
        seen = set()
        for minute in range(24 * 60 + 1):
            now = start + timedelta(minutes=minute)
            context = EMSContext(
                now=now,
                battery=battery,
                target_soc=90,
                minimum_soc=20,
                price_margin=0.02,
                discharge_margin=0.08,
                charge_efficiency=0.92,
                battery_energy_needed=1.8,
                needed_energy=2.0,
                hours_needed=2.0,
                charge_plan=charge_plan,
                discharge_plan=discharge_plan,
                charge_distribution=[1000],
            )
            state = scheduler.determine(context)
            seen.add(state)
            app.states["sensor.battery"] = {
                SchedulerState.CHARGING: "-1000",
                SchedulerState.SELF_CONSUMPTION: "500",
            }.get(state, "0")
            price = price_values[min(23, minute // 60)]
            tracker.sample(
                now,
                price,
                [70],
                [5.0],
                20,
                [0.92],
                [0.92],
            )

        result = tracker.data["days"]["2026-07-18"]
        assert SchedulerState.CHARGING in seen
        assert SchedulerState.SELF_CONSUMPTION in seen
        assert SchedulerState.STOPPED in seen
        assert result["laadenergie_kwh"] > 1.9
        assert result["ontlaadenergie_kwh"] > 0
        assert result["laadkosten_eur"] > 0
        assert app.published[tracker.STATUS_SENSOR][1]["vandaag"][
            "meetdekking_pct"
        ] == 100.0
