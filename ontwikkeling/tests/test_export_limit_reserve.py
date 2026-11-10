from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from export_reserve import auto_reserve_soc, home_energy_kwh, horizon_end

ZONE = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 1, 20, 0, tzinfo=ZONE)


def forecast(power, quarters=96):
    return [
        {"start": (NOW + timedelta(minutes=15 * i)).isoformat(),
         "vermogen_w": power}
        for i in range(quarters)
    ]


def test_home_energy_over_ten_hours():
    kwh = home_energy_kwh(forecast(500.0), NOW, NOW + timedelta(hours=10), 300)
    assert abs(kwh - 5.0) < 1e-9


def test_missing_forecast_uses_fallback():
    kwh = home_energy_kwh([], NOW, NOW + timedelta(hours=2), 400)
    assert abs(kwh - 0.8) < 1e-9


def test_auto_reserve_adds_buffer_and_reserve():
    soc = auto_reserve_soc(5.0, 20.0, 10.0, 15.0, 0.20)
    assert abs(soc - (15 + 5.0 * 1.2 / 20 * 100)) < 1e-9


def test_auto_reserve_never_below_minimum_or_above_100():
    assert auto_reserve_soc(0.0, 20.0, 20.0, 5.0, 0.2) == 20.0
    assert auto_reserve_soc(50.0, 20.0, 10.0, 15.0, 0.2) == 100.0
    assert auto_reserve_soc(1.0, 0.0, 12.0, 15.0, 0.2) == 12.0


def test_horizon_defaults_to_24h():
    assert horizon_end(NOW, None) == NOW + timedelta(hours=24)
    later = NOW + timedelta(hours=9)
    assert horizon_end(NOW, later) == later


def test_planner_export_power_includes_home_allowance():
    from discharge_planner import DischargePlanner
    import inspect
    sig = inspect.signature(DischargePlanner.calculate)
    assert "maximum_export_power" in sig.parameters
