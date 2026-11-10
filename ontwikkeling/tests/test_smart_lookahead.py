from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from charge_planner import ChargePlanner

ZONE = ZoneInfo("Europe/Amsterdam")


def day_prices(day, today_low, tomorrow_flag=False):
    prices = []
    for hour in range(24):
        start = datetime(2026, 11, day, hour, tzinfo=ZONE)
        price = 0.30
        if 12 <= hour < 15:
            price = today_low
        prices.append({
            "from": start.isoformat(),
            "till": (start + timedelta(hours=1)).isoformat(),
            "price": price,
        })
    return prices


def run(smart, low_tomorrow=0.10, low_today=0.15, soc=20.0):
    planner = ChargePlanner("Europe/Amsterdam")
    prices = day_prices(1, low_today) + day_prices(2, low_tomorrow)
    home = [
        {"start": (datetime(2026, 11, 1, 0, tzinfo=ZONE) + timedelta(minutes=15 * i)).isoformat(),
         "vermogen_w": 200.0}
        for i in range(192)
    ]
    kwargs = dict(
        prices=prices, names=["A"], soc_values=[soc], capacities=[10.0],
        max_charge_values=[2500], target_soc=100.0, charge_efficiency=0.9,
        minimum_power=500, price_margin=0.01, home_forecast=home, fallback_home_power=200,
        projection_start=datetime(2026, 11, 1, 8, tzinfo=ZONE),
    )
    if smart:
        kwargs.update(
            smart_enabled=True, smart_reserve_soc=15.0, smart_buffer=0.2,
            smart_min_price_difference=0.02,
        )
    return planner, planner.calculate_per_battery_each_day(**kwargs)


def test_smart_reduces_today_when_tomorrow_cheaper():
    _, normal = run(False)
    planner, smart = run(True)
    n_energy = normal[0][1][0].battery_energy_needed
    s_energy = smart[0][1][0].battery_energy_needed
    assert s_energy < n_energy
    assert smart[0][1][0].target_soc < 100.0
    assert smart[0][1][0].target_soc >= 15.0
    assert planner.last_smart_notes
    assert "uitgesteld" in planner.last_smart_notes[0]["tekst"]


def test_smart_full_charge_when_difference_small():
    _, normal = run(False, low_tomorrow=0.14)
    planner, smart = run(True, low_tomorrow=0.14)
    assert (
        smart[0][1][0].battery_energy_needed
        == normal[0][1][0].battery_energy_needed
    )
    assert "Volledig" in planner.last_smart_notes[0]["tekst"]


def test_smart_off_changes_nothing():
    planner, _ = run(False)
    assert planner.last_smart_notes == []


class FakeApp:
    def __init__(self, states):
        self.states = states

    def get_state(self, entity, attribute=None):
        return self.states.get(entity)


def test_runtime_settings_smart_params_defaults_and_values():
    from runtime_settings import RuntimeSettings
    off = RuntimeSettings(FakeApp({}))
    assert off.smart_charge_params()["smart_enabled"] is False
    on = RuntimeSettings(FakeApp({
        "input_boolean.openems_slim_laden": "on",
        "input_number.openems_slim_laden_reserve_soc": "15",
        "input_number.openems_slim_laden_buffer": "20",
        "input_number.openems_slim_laden_min_verschil": "2",
    }))
    params = on.smart_charge_params()
    assert params["smart_enabled"] is True
    assert params["smart_reserve_soc"] == 15.0
    assert abs(params["smart_buffer"] - 0.20) < 1e-9
    assert abs(params["smart_min_price_difference"] - 0.02) < 1e-9


def test_planner_accepts_smart_params_from_settings():
    from runtime_settings import RuntimeSettings
    params = RuntimeSettings(FakeApp({
        "input_boolean.openems_slim_laden": "on",
    })).smart_charge_params()
    planner = ChargePlanner("Europe/Amsterdam")
    prices = day_prices(1, 0.15) + day_prices(2, 0.10)
    result = planner.calculate_per_battery_each_day(
        prices=prices, names=["A"], soc_values=[20.0], capacities=[10.0],
        max_charge_values=[2500], target_soc=100.0, charge_efficiency=0.9,
        minimum_power=500, price_margin=0.01, fallback_home_power=200,
        projection_start=datetime(2026, 11, 1, 8, tzinfo=ZONE), **params,
    )
    assert result and planner.last_smart_notes
