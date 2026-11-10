from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from charge_planner import ChargePlanner
from price_forecast import ForecastError, PriceForecast, parse_forecast
from price_manager import PriceManager

ZONE = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=ZONE)
ENTITY = "sensor.wattwanneer_gemiddelde_deze_week"


def items(start, count, price=0.20, real_until=0, step_h=1):
    result = []
    for i in range(count):
        moment = start + timedelta(hours=i * step_h)
        result.append({
            "start": moment.astimezone(timezone.utc).isoformat(),
            "price": price,
            "source": "entsoe_day_ahead" if i < real_until else "model",
        })
    return result


class App:
    def __init__(self, forecast=None, enabled="on"):
        self.states = {
            "input_boolean.openems_prijsvoorspelling": enabled,
        }
        self.forecast = forecast
        self.logs = []
        self.published = {}

    def get_state(self, entity, attribute=None):
        if entity == ENTITY and attribute == "forecast":
            if isinstance(self.forecast, Exception):
                raise self.forecast
            return self.forecast
        return self.states.get(entity)

    def log(self, message, level="INFO"):
        self.logs.append((level, message))

    def set_state(self, entity, state=None, attributes=None, **kwargs):
        self.published[entity] = (state, attributes)

    def run_every(self, *args, **kwargs):
        pass


def make(forecast, enabled="on", **kwargs):
    app = App(forecast, enabled)
    clock = lambda: NOW.astimezone(timezone.utc)
    return PriceForecast(app, "Europe/Amsterdam", clock=clock, **kwargs), app


def test_parse_accepts_real_sensor_format_and_flags_real_hours():
    start = datetime(2026, 10, 31, 23, tzinfo=timezone.utc)
    hours = parse_forecast(items(start, 6, real_until=3))
    assert len(hours) == 6
    assert [h["real"] for h in hours] == [True] * 3 + [False] * 3
    assert hours[0]["start"] == start.timestamp()


def test_parse_averages_quarter_hours():
    start = NOW.replace(minute=0)
    data = []
    for i, price in enumerate([0.10, 0.20, 0.30, 0.40] * 2):
        data.append({
            "start": (start + timedelta(minutes=15 * i)).astimezone(
                timezone.utc).isoformat(),
            "price": price, "source": "model",
        })
    hours = parse_forecast(data)
    assert len(hours) == 2 and hours[0]["market"] == pytest.approx(0.25)


@pytest.mark.parametrize("bad", [
    None, "unavailable", [], [{"x": 1}] * 5,
    [{"start": "2026-11-01T00:00:00+00:00", "price": "abc"}] * 5,
])
def test_parse_rejects_bad_attribute(bad):
    with pytest.raises(ForecastError):
        parse_forecast(bad)


def test_parse_rejects_prices_in_wrong_unit():
    with pytest.raises(ForecastError):
        parse_forecast(items(NOW, 6, price=24.0))  # ct i.p.v. EUR


def test_refresh_converts_to_all_in_like_frank():
    fc, app = make(
        items(NOW.replace(minute=0), 30),
        current_provider=lambda: (0.42341, 0.24331),
    )
    fc.refresh()
    assert fc.usable()
    assert fc.fixed == pytest.approx(0.129, abs=1e-4)
    first = fc.prices()[0]
    assert first["price"] == pytest.approx(0.20 * 1.21 + 0.129, abs=1e-4)
    state, attrs = app.published["sensor.openems_prijsvoorspelling"]
    assert state == "actief" and attrs["uren"] and attrs["bron"] == ENTITY


def test_disabled_does_not_read_or_supply_prices():
    fc, app = make(items(NOW, 30), enabled="off")
    fc.refresh()
    assert fc.prices() == [] and fc.planner_kwargs([], None, "60") == {}
    assert app.published["sensor.openems_prijsvoorspelling"][0] == "uit"


def test_sensor_failure_keeps_last_forecast_for_24_hours():
    fc, app = make(items(NOW.replace(minute=0), 30))
    fc.refresh()
    app.forecast = "unavailable"
    fc.refresh()
    assert fc.usable()  # laatste goede data blijft
    assert fc.error and any(level == "WARNING" for level, _ in app.logs)
    later = NOW + timedelta(hours=25)
    fc.clock = lambda: later.astimezone(timezone.utc)
    assert not fc.usable() and fc.prices() == []


def test_sensor_not_available_at_start_gives_error_state():
    fc, app = make(None)
    fc.refresh()
    assert app.published["sensor.openems_prijsvoorspelling"][0] == "fout"


def test_real_hours_that_differ_from_frank_are_rejected():
    start = NOW.replace(minute=0)
    frank = [
        {"from": (start + timedelta(hours=i)).isoformat(),
         "till": (start + timedelta(hours=i + 1)).isoformat(), "price": 0.05}
        for i in range(6)
    ]
    fc, _ = make(items(start, 30, price=0.30, real_until=6),
                 market_provider=lambda: frank)
    fc.refresh()
    assert not fc.usable() and "wijken" in (fc.error or "")


def test_real_hours_matching_frank_are_accepted():
    start = NOW.replace(minute=0)
    frank = [
        {"from": (start + timedelta(hours=i)).isoformat(),
         "till": (start + timedelta(hours=i + 1)).isoformat(), "price": 0.2002}
        for i in range(6)
    ]
    fc, _ = make(items(start, 30, price=0.20, real_until=6),
                 market_provider=lambda: frank)
    fc.refresh()
    assert fc.usable() and fc.mean_error == pytest.approx(0.0002)


def test_only_hours_after_frank_end_are_used_and_params_default():
    midnight = NOW.replace(hour=0, minute=0)
    fc, _ = make(items(midnight, 72))
    manager = PriceManager(App(), "sensor.x", "prices", "Europe/Amsterdam")
    known = [{
        "from": midnight.isoformat(),
        "till": (midnight + timedelta(hours=24)).isoformat(),
        "price": 0.3,
    }]
    kwargs = fc.planner_kwargs(known, manager, "60 minuten")
    first = datetime.fromisoformat(kwargs["forecast_prices"][0]["from"])
    assert first >= midnight + timedelta(hours=24)
    assert kwargs["forecast_min_difference"] == pytest.approx(0.05)
    assert kwargs["forecast_floor_soc"] == 30.0 and kwargs["forecast_days"] == 2


# ---- planner ---------------------------------------------------------------
def hour_prices(day, low, hour_low=(12, 15), base=0.30):
    out = []
    for hour in range(24):
        start = datetime(2026, 11, day, hour, tzinfo=ZONE)
        out.append({
            "from": start.isoformat(),
            "till": (start + timedelta(hours=1)).isoformat(),
            "price": low if hour_low[0] <= hour < hour_low[1] else base,
        })
    return out


def planner_run(forecast_low, floor=30.0, min_diff=0.05, days=2, soc=20.0,
                with_forecast=True):
    planner = ChargePlanner("Europe/Amsterdam")
    home = [
        {"start": (datetime(2026, 11, 1, 0, tzinfo=ZONE)
                   + timedelta(minutes=15 * i)).isoformat(),
         "vermogen_w": 200.0}
        for i in range(96 * 4)
    ]
    kwargs = dict(
        prices=hour_prices(1, 0.15), names=["A"], soc_values=[soc],
        capacities=[10.0], max_charge_values=[2500], target_soc=100.0,
        charge_efficiency=0.9, minimum_power=500, price_margin=0.01,
        home_forecast=home, fallback_home_power=200,
        projection_start=datetime(2026, 11, 1, 8, tzinfo=ZONE),
        smart_enabled=True, smart_reserve_soc=15.0, smart_buffer=0.2,
        smart_min_price_difference=0.02,
    )
    if with_forecast:
        kwargs.update(
            forecast_prices=hour_prices(2, forecast_low),
            forecast_min_difference=min_diff, forecast_floor_soc=floor,
            forecast_days=days,
        )
    return planner, planner.calculate_per_battery_each_day(**kwargs)


def test_clearly_cheaper_forecast_reduces_todays_target():
    _, normal = planner_run(0.05, with_forecast=False)
    planner, smart = planner_run(0.05)
    assert (
        smart[0][1][0].battery_energy_needed
        < normal[0][1][0].battery_energy_needed
    )
    assert "voorspelling" in planner.last_smart_notes[0]["tekst"]
    assert smart[0][1][0].target_soc >= 30.0


def test_small_forecast_difference_changes_nothing():
    _, normal = planner_run(0.12, with_forecast=False)
    planner, smart = planner_run(0.12)  # 3 ct goedkoper < 5 ct drempel
    assert (
        smart[0][1][0].battery_energy_needed
        == normal[0][1][0].battery_energy_needed
    )
    assert "Volledig" in planner.last_smart_notes[0]["tekst"]


def test_forecast_beyond_allowed_days_is_ignored():
    planner, _ = planner_run(0.05, days=0)
    assert planner.last_smart_notes == []


def test_floor_soc_limits_how_low_the_target_goes():
    _, low = planner_run(0.05, floor=15.0)
    _, high = planner_run(0.05, floor=60.0)
    assert high[0][1][0].target_soc >= low[0][1][0].target_soc
    assert high[0][1][0].target_soc >= 60.0


def test_no_forecast_arguments_keeps_old_behaviour():
    planner, result = planner_run(0.05, with_forecast=False)
    assert planner.last_smart_notes == [] and result


# ---- koppeling met de app (appdaemon is hier niet geïnstalleerd) -----------
def _load_app_class():
    import sys
    import types

    if "marstek_ems" in sys.modules:
        return sys.modules["marstek_ems"].MarstekEMS
    hassapi = types.ModuleType("appdaemon.plugins.hass.hassapi")
    hassapi.Hass = type("Hass", (), {})
    for name in ("appdaemon", "appdaemon.plugins", "appdaemon.plugins.hass"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["appdaemon.plugins.hass.hassapi"] = hassapi
    import marstek_ems
    return marstek_ems.MarstekEMS


def test_app_helpers_return_values_and_never_raise():
    cls = _load_app_class()
    logs = []
    app = SimpleNamespace(
        runtime_settings=SimpleNamespace(
            frank_all_in_entity=lambda: "sensor.allin",
            frank_market_entity=lambda: "sensor.markt",
        ),
        get_state=lambda entity, **kw: {
            "sensor.allin": "0.42341", "sensor.markt": "unavailable",
        }.get(entity),
        log=lambda message, level="INFO": logs.append(message),
        price_forecast=SimpleNamespace(
            planner_kwargs=lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        ),
        price_manager=None,
    )
    assert cls._forecast_current_prices(app) == (0.42341, None)
    assert cls._forecast_planner_kwargs(app, [], "60 minuten") == {}
    assert logs and "Niet gebruikt" in logs[0]


# ---- extra marge bij schattingen ------------------------------------------
def planner_run_margin(low, margin, real_days, min_diff=0.05):
    planner, _ = planner_run(low)  # basis voor helpers
    planner = ChargePlanner("Europe/Amsterdam")
    home = [
        {"start": (datetime(2026, 11, 1, 0, tzinfo=ZONE)
                   + timedelta(minutes=15 * i)).isoformat(),
         "vermogen_w": 200.0}
        for i in range(96 * 4)
    ]
    result = planner.calculate_per_battery_each_day(
        prices=hour_prices(1, 0.15), names=["A"], soc_values=[20.0],
        capacities=[10.0], max_charge_values=[2500], target_soc=100.0,
        charge_efficiency=0.9, minimum_power=500, price_margin=0.01,
        home_forecast=home, fallback_home_power=200,
        projection_start=datetime(2026, 11, 1, 8, tzinfo=ZONE),
        smart_enabled=True, smart_reserve_soc=15.0, smart_buffer=0.2,
        smart_min_price_difference=0.02,
        forecast_prices=hour_prices(2, low),
        forecast_min_difference=min_diff, forecast_floor_soc=30.0,
        forecast_days=2, forecast_extra_margin=margin,
        forecast_real_days=real_days,
    )
    return planner, result


def test_estimate_day_needs_extra_margin():
    # 7 ct goedkoper: genoeg voor 5 ct, te weinig voor 5+3 ct bij een schatting.
    planner, _ = planner_run_margin(0.08, 0.03, set())
    assert "Volledig" in planner.last_smart_notes[0]["tekst"]
    assert "schatting" in planner.last_smart_notes[0]["tekst"]


def test_fixed_day_ignores_extra_margin():
    from datetime import date
    planner, _ = planner_run_margin(0.08, 0.03, {date(2026, 11, 2)})
    assert "uitgesteld" in planner.last_smart_notes[0]["tekst"]


def test_estimate_day_clearly_cheaper_still_postpones_and_mentions_margin():
    planner, _ = planner_run_margin(0.05, 0.03, set())
    text = planner.last_smart_notes[0]["tekst"]
    assert "uitgesteld" in text and "marge 8 ct" in text


def test_planner_kwargs_marks_real_and_estimated_days():
    from datetime import date
    midnight = NOW.replace(hour=0, minute=0)
    data = items(midnight, 72, real_until=48)  # dag 1 en 2 vast, dag 3 model
    fc, _ = make(data)
    fc.refresh()
    manager = PriceManager(App(), "sensor.x", "prices", "Europe/Amsterdam")
    known = [{
        "from": midnight.isoformat(),
        "till": (midnight + timedelta(hours=24)).isoformat(),
        "price": 0.3,
    }]
    kwargs = fc.planner_kwargs(known, manager, "60 minuten")
    assert kwargs["forecast_extra_margin"] == pytest.approx(0.03)
    assert kwargs["forecast_real_days"] == {date(2026, 11, 2)}


# ---- terugleveren blijft uit zolang laden is uitgesteld -------------------
def test_postponed_charge_sets_flag_only_when_target_reduced():
    planner, _ = planner_run(0.05)
    assert planner.forecast_postponed is True
    planner, _ = planner_run(0.12)  # te klein verschil: volledig laden
    assert planner.forecast_postponed is False
    planner, _ = planner_run(0.05, with_forecast=False)
    assert planner.forecast_postponed is False


def test_export_plan_is_empty_with_reserve_100():
    from discharge_planner import DischargePlanner

    start = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    future = [
        {
            "from": (start + timedelta(hours=i)).isoformat(),
            "till": (start + timedelta(hours=i + 1)).isoformat(),
            "price": price,
        }
        for i, price in enumerate([0.24, 0.25, 0.27, 0.45])
    ]
    plan = DischargePlanner("Europe/Amsterdam").calculate(
        future_prices=future, average_charge_price=0.10,
        inventory_cost_per_usable_kwh=0.20, discharge_margin=0.05,
        selection_margin=0.02, minimum_soc=20, reserve_soc=100.0,
        names=["A", "B"], soc_values=[90, 80], capacities=[5.12, 5.12],
        max_discharge_values=[2500, 2500],
        discharge_efficiencies=[0.92, 0.92], minimum_block_minutes=15,
        maximum_export_power=5000, preferred_min_power=1000,
        preferred_max_power=1500, resolution="60 minuten",
    )
    assert plan.blocks == []


# ---- volle accu + indicatief plan voor voorspelde dagen -------------------
def _today_prices():
    real = [0.415, 0.414, 0.397, 0.407, 0.44, 0.474, 0.49, 0.49, 0.488,
            0.473, 0.445, 0.432, 0.412]
    out = []
    for i, price in enumerate(real):
        start = datetime(2026, 11, 1, 11 + i, tzinfo=ZONE)
        out.append({
            "from": start.isoformat(),
            "till": (start + timedelta(hours=1)).isoformat(),
            "price": price,
        })
    return out


def _forecast_days():
    out = []
    for day, base in ((2, 0.33), (3, 0.34)):
        for hour in range(24):
            start = datetime(2026, 11, day, hour, tzinfo=ZONE)
            price = base + (0.1 if 16 <= hour <= 20 else 0.0)
            out.append({
                "from": start.isoformat(),
                "till": (start + timedelta(hours=1)).isoformat(),
                "price": price,
            })
    return out


def _full_run(soc, with_forecast=True, margin=0.03, real=True):
    planner = ChargePlanner("Europe/Amsterdam")
    home = [
        {"start": (datetime(2026, 11, 1, 0, tzinfo=ZONE)
                   + timedelta(minutes=15 * i)).isoformat(),
         "vermogen_w": 300.0}
        for i in range(96 * 4)
    ]
    kwargs = dict(
        prices=_today_prices(), names=list("ABCD"),
        soc_values=[float(soc)] * 4, capacities=[5.12] * 4,
        max_charge_values=[2500] * 4, target_soc=100.0,
        charge_efficiency=0.9, minimum_power=500, price_margin=0.01,
        home_forecast=home, fallback_home_power=300,
        projection_start=datetime(2026, 11, 1, 11, tzinfo=ZONE),
        smart_enabled=True, smart_reserve_soc=15.0, smart_buffer=0.2,
        smart_min_price_difference=0.01,
    )
    if with_forecast:
        kwargs.update(
            forecast_prices=_forecast_days(), forecast_min_difference=0.01,
            forecast_floor_soc=30.0, forecast_days=2,
            forecast_extra_margin=margin,
            forecast_real_days=(
                {datetime(2026, 11, 2).date()} if real else set()
            ),
        )
    return planner, planner.calculate_per_battery_each_day(**kwargs)


def test_full_battery_gets_no_charge_plan_for_later_consumption():
    # Voorheen: de planner trok het verbruik van de rest van de dag af en
    # plande daarvoor alvast een laadblok, ook bij volle accu's.
    planner, result = _full_run(100, with_forecast=False)
    assert result == []
    assert "doel-SOC" in planner.last_smart_notes[0]["tekst"]
    assert planner.forecast_postponed is False


def test_full_battery_with_cheaper_forecast_day_blocks_export():
    planner, result = _full_run(100)
    assert result == []
    text = planner.last_smart_notes[0]["tekst"]
    assert "geen terugleveren" in text
    assert planner.forecast_postponed is True


def test_full_battery_without_cheaper_day_keeps_export_allowed():
    planner, _ = _full_run(100, margin=0.30, real=False)  # schatting: marge telt
    assert planner.forecast_postponed is False


def test_battery_that_really_needs_energy_still_gets_a_plan():
    planner, result = _full_run(50, with_forecast=False)
    assert result and result[0][1][0].battery_energy_needed > 0


def test_indicative_plan_covers_forecast_days_only():
    planner, result = _full_run(100)
    days = [entry["dag"] for entry in planner.forecast_day_plans]
    assert days == ["2026-11-02", "2026-11-03"]
    assert result == []  # het echte plan (en dus de besturing) blijft leeg
    third = planner.forecast_day_plans[1]
    assert third["laden"] and third["gemiddelde_prijs"] is not None
    assert all(item["van"] < item["tot"] for item in third["laden"])


def test_indicative_plan_failure_never_breaks_planning(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kapot")

    monkeypatch.setattr(ChargePlanner, "_plan_forecast_days", boom)
    planner, result = _full_run(50)
    assert planner.forecast_day_plans == [] and result is not None


def test_no_forecast_arguments_means_no_indicative_plan():
    planner, _ = _full_run(50, with_forecast=False)
    assert planner.forecast_day_plans == []


def _publish_with(day_plans, forecast_prices, state_log):
    from self_consumption_planner import SelfConsumptionPlanner

    cls = _load_app_class()
    manager = PriceManager(App(), "sensor.x", "prices", "Europe/Amsterdam")
    app = SimpleNamespace(
        _forecast_kwargs={"forecast_prices": forecast_prices},
        charge_planner=SimpleNamespace(forecast_day_plans=day_plans),
        price_manager=manager,
        self_consumption_planner=SelfConsumptionPlanner("Europe/Amsterdam"),
        set_state=lambda entity, state=None, attributes=None, **kw:
            state_log.append((entity, state, attributes)),
        log=lambda *a, **k: None,
    )
    shared = {
        "average_charge_price": 0.33, "discharge_margin": 0.02,
        "minimum_soc": 20, "avg_soc": 80, "capacity": 20.0,
        "names": list("ABCD"), "soc_values": [80.0] * 4,
        "capacities": [5.12] * 4, "discharge_efficiencies": [0.92] * 4,
        "resolution": "60 minuten", "minimum_block_minutes": 30,
    }
    cls._publish_forecast_plan(app, shared, 0.33)


def test_forecast_plan_sensor_marks_charge_self_and_standby_hours():
    planner, _ = _full_run(100)
    log = []
    _publish_with(
        planner.forecast_day_plans, _forecast_days(), log
    )
    entity, state, attributes = log[-1]
    assert entity == "sensor.openems_voorspeld_plan" and state == "aan"
    modes = {item["van"][:13]: item["modus"] for item in attributes["uren"]}
    assert set(modes.values()) <= {"laden", "zelf", "standby"}
    assert "laden" in modes.values() and "standby" in modes.values()
    # De duurste avonduren zijn zelfconsumptie, de nacht is stand-by/laden.
    assert modes["2026-11-02T18"] == "zelf"
    assert modes["2026-11-02T03"] in ("standby", "laden")


def test_forecast_plan_sensor_is_off_without_forecast():
    log = []
    _publish_with([], [], log)
    assert log[-1][1] == "uit" and log[-1][2]["uren"] == []


# ---- een lopend, eerder plan mag het uitstel niet overrulen --------------
def test_charge_deferred_flag_for_full_battery_and_postponement():
    planner, _ = _full_run(100)
    assert planner.charge_deferred is True       # volle accu
    planner, _ = _full_run(60)
    assert planner.charge_deferred is True       # uitgesteld op voorspelling
    planner, _ = _full_run(60, with_forecast=False)
    assert planner.charge_deferred is False      # gewoon plan blijft gelden


def test_kept_previous_plan_is_not_used_when_charging_is_deferred():
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[2]
        / "addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/marstek_ems.py"
    ).read_text(encoding="utf-8")
    block = text.split("previous_charge_plan.start <= now", 1)[1][:200]
    assert 'getattr(self.charge_planner, "charge_deferred", False)' in block


def test_kept_previous_plan_is_dropped_when_all_batteries_at_target():
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[2]
        / "addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/marstek_ems.py"
    ).read_text(encoding="utf-8")
    block = text.split("previous_charge_plan.start <= now", 1)[1][:600]
    assert "float(target_soc) - 2.0" in block
    assert "for value in planning_soc_values" in block


def test_forecast_status_hours_contain_bare_market_price():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    py = (root / "addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/price_forecast.py").read_text(encoding="utf-8")
    js = (root / "config/custom_components/openems_m_panel/frontend/openems-panel.js").read_text(encoding="utf-8")
    # Kale marktprijs (excl. btw); geschatte uren inclusief de geleerde correctie.
    assert '"markt": round(self.market(item), 5)' in py
    assert "hour.markt" in js and "market + (Number.isFinite(returnFee)" in js
    assert "Terugleverprijs${point.forecast" in js


def test_panel_drops_charge_candidate_when_forecast_is_clearly_cheaper():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "config/custom_components/openems_m_panel/frontend/openems-panel.js"
    ).read_text(encoding="utf-8")
    assert "_forecastBeatsCharge(time, stepMs) {" in js
    assert "&& !this._forecastBeatsCharge(time, stepMs)," in js
    assert "this._lastFrankEndMs = lastFrank;" in js
    assert "this._frankPriceByTime = new Map(" in js


# ---- zelflerende correctie op de modelschatting ---------------------------
class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment.astimezone(timezone.utc)


def frank_rows(start, count, price):
    return [
        {
            "from": (start + timedelta(hours=i)).isoformat(),
            "till": (start + timedelta(hours=i + 1)).isoformat(),
            "price": price,
        }
        for i in range(count)
    ]


def learn_day(model_price, real_price, data_file=None):
    """Model schat dag+1 op model_price; daarna blijkt real_price echt."""
    day = NOW.replace(hour=0) + timedelta(days=1)
    clock = Clock(NOW)
    frank = []
    app = App(items(day, 30, price=model_price))
    forecast = PriceForecast(
        app, "Europe/Amsterdam", clock=clock,
        market_provider=lambda: frank, data_file=data_file,
    )
    forecast.refresh()
    # De volgende dag publiceert Frank de echte prijzen voor die 24 uur.
    clock.moment = day + timedelta(hours=-10)
    frank.extend(frank_rows(day, 24, real_price))
    app.forecast = items(day, 30, price=real_price, real_until=24)
    forecast.refresh()
    return forecast, app


def test_bias_learns_model_too_low_and_corrects_only_estimated_hours():
    forecast, app = learn_day(model_price=0.18, real_price=0.21)
    assert forecast.bias_samples() == 24
    assert forecast.bias() == pytest.approx(0.03)
    real = [h for h in forecast.hours if h["real"]][0]
    model = [h for h in forecast.hours if not h["real"]][0]
    assert forecast.market(real) == pytest.approx(real["market"])
    assert forecast.market(model) == pytest.approx(model["market"] + 0.03)
    attrs = app.published["sensor.openems_prijsvoorspelling"][1]
    assert attrs["correctie_ct"] == pytest.approx(3.0)
    assert attrs["correctie_actief"] is True
    estimated = [u for u in attrs["uren"] if not u["echt"]]
    assert estimated and estimated[0]["markt"] == pytest.approx(0.24)


def test_bias_inactive_below_minimum_samples():
    day = NOW.replace(hour=0) + timedelta(days=1)
    clock = Clock(NOW)
    frank = []
    app = App(items(day, 30, price=0.18))
    forecast = PriceForecast(app, "Europe/Amsterdam", clock=clock,
                             market_provider=lambda: frank)
    forecast.refresh()
    clock.moment = day + timedelta(hours=-10)
    frank.extend(frank_rows(day, 10, 0.21))
    forecast.refresh()
    assert forecast.bias_samples() == 10
    assert forecast.bias() == 0.0


def test_bias_is_clamped_to_five_cent():
    forecast, _app = learn_day(model_price=0.10, real_price=0.40)
    assert forecast.bias() == pytest.approx(0.05)
    forecast, _app = learn_day(model_price=0.40, real_price=0.10)
    assert forecast.bias() == pytest.approx(-0.05)


def test_bias_ignores_estimates_made_less_than_12_hours_ahead():
    day = NOW.replace(hour=0) + timedelta(days=1)
    clock = Clock(day + timedelta(hours=-6))  # te kort vooruit
    frank = []
    app = App(items(day, 30, price=0.18))
    forecast = PriceForecast(app, "Europe/Amsterdam", clock=clock,
                             market_provider=lambda: frank)
    forecast.refresh()
    assert not any(
        float(k) < (day + timedelta(hours=6)).timestamp()
        for k in forecast.bias_data["schattingen"]
    )


def test_bias_forgets_errors_older_than_seven_days():
    forecast, _app = learn_day(model_price=0.18, real_price=0.21)
    forecast.clock = Clock(NOW + timedelta(days=10))
    forecast._learn_bias([], {})
    assert forecast.bias_samples() == 0
    assert forecast.bias() == 0.0


def test_bias_survives_restart_via_data_file(tmp_path):
    path = tmp_path / "prijsvoorspelling_correctie.json"
    forecast, _app = learn_day(0.18, 0.21, data_file=path)
    assert path.exists()
    again = PriceForecast(App(None), "Europe/Amsterdam",
                          clock=Clock(NOW), data_file=path)
    assert again.bias_samples() == 24
    assert again.bias() == pytest.approx(0.03)


def test_corrupt_bias_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "prijsvoorspelling_correctie.json"
    path.write_text("{kapot", encoding="utf-8")
    forecast = PriceForecast(App(None), "Europe/Amsterdam",
                             clock=Clock(NOW), data_file=path)
    assert forecast.bias() == 0.0
