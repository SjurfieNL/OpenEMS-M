from datetime import datetime, timedelta, timezone
from math import isclose
from zoneinfo import ZoneInfo

from charge_planner import ChargePlanner


def make_prices(count, start=None, price=0.10):
    start = start or datetime(2026, 7, 14, 10, tzinfo=timezone.utc)
    return [
        {
            "from": (start + timedelta(hours=index)).isoformat(),
            "till": (start + timedelta(hours=index + 1)).isoformat(),
            "price": price,
        }
        for index in range(count)
    ]


def test_power_targets_completion_fifteen_minutes_before_block_end():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=1,
        completion_buffer_minutes=15,
    )

    plan = planner.calculate(
        prices=make_prices(1),
        needed_energy=1.0,
        max_charge=2500,
        price_margin=0.01,
    )

    assert plan.hours == 1.0
    assert isclose(plan.power, 1000 / 0.75)


def test_buffer_reserves_extra_hour_when_required_by_max_power():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=1,
        completion_buffer_minutes=15,
    )

    plan = planner.calculate(
        prices=make_prices(3),
        needed_energy=4.5,
        max_charge=2500,
        price_margin=0.01,
    )

    assert plan.hours == 3.0
    assert isclose(plan.power, 4500 / 2.75)


def test_power_never_exceeds_maximum_with_buffer():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=1,
        completion_buffer_minutes=15,
    )

    plan = planner.calculate(
        prices=make_prices(1),
        needed_energy=2.5,
        max_charge=2500,
        price_margin=0.01,
    )

    assert plan.power == 2500


def test_individual_plans_use_cheapest_time_and_at_least_500_watts():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    prices = make_prices(6, price=0.20)
    prices[3]["price"] = 0.10
    prices[2]["price"] = 0.11

    aggregate, plans = planner.calculate_per_battery(
        prices=prices,
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[99.0, 90.0, 98.0],
        capacities=[5.12, 5.12, 5.12],
        max_charge_values=[2500, 2500, 2500],
        target_soc=100.0,
        charge_efficiency=1.0,
        minimum_power=500,
        price_margin=0.11,
    )

    assert aggregate is not None
    assert len(plans) == 3
    assert all(plan.planned_power >= 500 for plan in plans)
    assert all(plan.segments[0].price == 0.10 for plan in plans)
    assert isclose(
        sum(segment.energy_kwh for segment in plans[0].segments),
        plans[0].grid_energy_needed,
    )
    assert plans[0].duration_hours < plans[1].duration_hours


def test_empty_batteries_partially_charge_in_short_green_window():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    zone = ZoneInfo("Europe/Amsterdam")
    prices = [{
        "from": datetime(
            2026, 7, 30, 12, 9, tzinfo=zone
        ).isoformat(),
        "till": datetime(
            2026, 7, 30, 13, 0, tzinfo=zone
        ).isoformat(),
        "price": 0.1293,
    }]

    aggregate, plans = planner.calculate_per_battery(
        prices=prices,
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[11.0, 31.0, 38.0],
        capacities=[5.12, 5.12, 5.12],
        max_charge_values=[2500, 2500, 2500],
        target_soc=100.0,
        charge_efficiency=0.89,
        charge_efficiencies=[0.89, 0.89, 0.89],
        minimum_power=500,
        price_margin=0.015,
        allowed_start_hour=12,
        allowed_end_hour=17,
    )

    assert aggregate is not None
    assert len(plans) == 3
    assert all(plan.segments for plan in plans)
    assert all(isclose(plan.planned_power, 2500) for plan in plans)
    assert all(isclose(plan.grid_energy_needed, 1.5) for plan in plans)
    assert all(
        plan.battery_energy_needed
        < plan.capacity * (100.0 - plan.current_soc) / 100
        for plan in plans
    )


def test_daily_forecast_contains_actual_slots_for_both_visible_days():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    start = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    prices = make_prices(8, start=start, price=0.15)
    prices += make_prices(
        8, start=start + timedelta(days=1), price=0.14
    )

    forecasts = planner.calculate_per_battery_each_day(
        prices=prices,
        names=["Accu 1"],
        soc_values=[60.0],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=90.0,
        charge_efficiency=0.9,
        minimum_power=500,
        price_margin=0.05,
        fallback_home_power=100,
        projection_start=start,
        projection_minimum_soc=20,
    )

    assert len(forecasts) == 2
    assert all(plan is not None for plan, _battery_plans in forecasts)
    assert all(
        battery_plans[0].segments
        for _plan, battery_plans in forecasts
    )
    assert forecasts[0][0].start.date() != forecasts[1][0].start.date()
    assert 20 < forecasts[1][1][0].current_soc < 90


def test_future_day_does_not_recharge_again_without_projected_consumption():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    start = datetime(2026, 7, 18, 8, tzinfo=timezone.utc)
    prices = make_prices(8, start=start, price=0.15)
    prices += make_prices(8, start=start + timedelta(days=1), price=0.14)

    forecasts = planner.calculate_per_battery_each_day(
        prices=prices,
        names=["Accu 1"],
        soc_values=[60.0],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=90.0,
        charge_efficiency=0.9,
        minimum_power=500,
        price_margin=0.05,
        projection_start=start,
    )

    assert len(forecasts) == 1


def test_individual_battery_uses_contiguous_hours_below_1500_watts():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    prices = make_prices(6, price=0.20)
    prices[4]["price"] = 0.10
    prices[1]["price"] = 0.11
    prices[3]["price"] = 0.12

    _aggregate, plans = planner.calculate_per_battery(
        prices=prices,
        names=["Accu 1"],
        soc_values=[20.0],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=1.0,
        minimum_power=500,
        price_margin=0.11,
    )

    plan = plans[0]
    assert len(plan.segments) == 3
    assert [segment.price for segment in plan.segments] == [0.20, 0.12, 0.10]
    assert all(
        left.end == right.start
        for left, right in zip(plan.segments, plan.segments[1:])
    )
    assert plan.planned_power <= 1500
    assert plan.power_strategy == "voorkeursbereik"
    assert isclose(
        sum(segment.energy_kwh for segment in plan.segments),
        plan.grid_energy_needed,
    )


def test_partial_current_hour_continues_without_stop_until_buffer():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    start = datetime(2026, 7, 27, 15, 36, tzinfo=timezone.utc)
    prices = [
        {
            "from": start.isoformat(),
            "till": datetime(
                2026, 7, 27, 16, 0, tzinfo=timezone.utc
            ).isoformat(),
            "price": 0.13,
        },
        {
            "from": datetime(
                2026, 7, 27, 16, 0, tzinfo=timezone.utc
            ).isoformat(),
            "till": datetime(
                2026, 7, 27, 17, 0, tzinfo=timezone.utc
            ).isoformat(),
            "price": 0.10,
        },
        {
            "from": datetime(
                2026, 7, 27, 17, 0, tzinfo=timezone.utc
            ).isoformat(),
            "till": datetime(
                2026, 7, 27, 18, 0, tzinfo=timezone.utc
            ).isoformat(),
            "price": 0.40,
        },
    ]

    aggregate, plans = planner.calculate_per_battery(
        prices=prices,
        names=["Accu 2"],
        soc_values=[68.75],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=1.0,
        minimum_power=500,
        preferred_min_power=1000,
        preferred_max_power=1500,
        price_margin=0.05,
    )

    plan = plans[0]
    assert aggregate is not None
    assert len(plan.segments) == 2
    assert plan.segments[0].start == start
    assert plan.segments[0].end == plan.segments[1].start
    assert plan.end == datetime(
        2026, 7, 27, 16, 45, tzinfo=timezone.utc
    )
    assert aggregate.eligible_slots[-1]["till"] == prices[1]["till"]


def test_today_example_only_spreads_battery_one_over_three_hours():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    old_powers = [1590, 1304, 1187]
    grid_needs = [power * 1.75 / 1000 for power in old_powers]
    capacities = [5.12, 5.12, 5.12]
    soc_values = [
        100 - energy / capacity * 100
        for energy, capacity in zip(grid_needs, capacities)
    ]

    _aggregate, plans = planner.calculate_per_battery(
        prices=make_prices(6),
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=soc_values,
        capacities=capacities,
        max_charge_values=[2500, 2500, 2500],
        target_soc=100.0,
        charge_efficiency=1.0,
        minimum_power=500,
        preferred_min_power=1000,
        preferred_max_power=1500,
        price_margin=0.01,
    )

    assert [len(plan.segments) for plan in plans] == [3, 2, 2]
    assert [round(plan.planned_power) for plan in plans] == [
        1012, 1304, 1187,
    ]
    assert all(
        plan.power_strategy == "voorkeursbereik" for plan in plans
    )


def test_power_above_preferred_maximum_is_only_used_when_window_is_too_short():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )

    _aggregate, plans = planner.calculate_per_battery(
        prices=make_prices(2),
        names=["Accu 1"],
        soc_values=[0.0],
        capacities=[3.5],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=1.0,
        minimum_power=500,
        preferred_min_power=1000,
        preferred_max_power=1500,
        price_margin=0.01,
    )

    assert plans[0].planned_power == 2000
    assert plans[0].power_strategy == "noodzakelijk_hoger"
    assert len(plans[0].segments) == 2


def test_small_energy_need_gets_exact_partial_slot_at_500_watts():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )

    _aggregate, plans = planner.calculate_per_battery(
        prices=make_prices(3),
        names=["Accu 1"],
        soc_values=[99.0],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=1.0,
        minimum_power=500,
        price_margin=0.01,
    )

    plan = plans[0]
    assert plan.planned_power == 500
    assert isclose(plan.duration_hours, 0.1024)
    assert isclose(plan.segments[0].energy_kwh, 0.0512)


def test_each_battery_uses_its_configured_charge_efficiency():
    planner = ChargePlanner("Europe/Amsterdam")

    aggregate, plans = planner.calculate_per_battery(
        prices=make_prices(3),
        names=["Accu 1", "Accu 2"],
        soc_values=[90.0, 90.0],
        capacities=[5.12, 5.12],
        max_charge_values=[2500, 2500],
        target_soc=100.0,
        charge_efficiency=0.92,
        charge_efficiencies=[0.80, 0.95],
        minimum_power=500,
        price_margin=0.01,
    )

    assert aggregate is not None
    assert plans[0].charge_efficiency == 0.80
    assert plans[1].charge_efficiency == 0.95
    assert plans[0].grid_energy_needed > plans[1].grid_energy_needed
    assert aggregate.eligible_slots


def test_graph_price_window_ignores_allowed_hours_and_six_hour_limit():
    planner = ChargePlanner(
        "Europe/Amsterdam",
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    )
    prices = make_prices(8, price=0.10)

    raw, _plans = planner.calculate_per_battery(
        prices=prices,
        names=["Accu 1"],
        soc_values=[95.0],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=0.92,
        minimum_power=500,
        price_margin=0.01,
        allowed_start_hour=14,
        allowed_end_hour=16,
    )
    decorated = planner.with_economic_price_window(
        raw, prices, price_margin=0.01
    )

    assert len(raw.eligible_slots) == 2
    assert len(decorated.eligible_slots) == 8
    assert decorated.slots == raw.slots


def test_economic_price_slots_do_not_require_battery_energy():
    planner = ChargePlanner("Europe/Amsterdam")
    prices = make_prices(5)
    for item, price in zip(prices, [0.30, 0.20, 0.10, 0.11, 0.14]):
        item["price"] = price

    slots = planner.economic_price_slots(prices, 0.02)

    assert [round(float(item["price"]), 2) for item in slots] == [0.10, 0.11]


def daily_prices(day, start_hour, end_hour, price):
    zone = ZoneInfo("Europe/Amsterdam")
    start = datetime(day.year, day.month, day.day, start_hour, tzinfo=zone)
    return [
        {
            "from": (start + timedelta(hours=index)).isoformat(),
            "till": (start + timedelta(hours=index + 1)).isoformat(),
            "price": price + index * 0.0001,
        }
        for index in range(end_hour - start_hour)
    ]


def test_earliest_feasible_day_wins_even_when_tomorrow_is_cheaper():
    planner = ChargePlanner("Europe/Amsterdam")
    today = datetime(2026, 7, 18).date()
    tomorrow = today + timedelta(days=1)
    prices = (
        daily_prices(today, 13, 17, 0.126)
        + daily_prices(tomorrow, 11, 17, 0.123)
    )

    plan, _battery_plans = planner.calculate_per_battery_earliest_day(
        prices=prices,
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[49.0, 59.0, 84.0],
        capacities=[5.12, 5.12, 5.12],
        max_charge_values=[2500, 2500, 2500],
        target_soc=100.0,
        charge_efficiency=0.875,
        charge_efficiencies=[0.875, 0.875, 0.875],
        minimum_power=500,
        price_margin=0.013,
        allowed_start_hour=11,
        allowed_end_hour=17,
    )

    assert plan is not None
    assert plan.start.date() == today


def test_short_green_window_today_is_used_before_full_plan_tomorrow():
    planner = ChargePlanner("Europe/Amsterdam")
    zone = ZoneInfo("Europe/Amsterdam")
    today = datetime(2026, 7, 18).date()
    tomorrow = today + timedelta(days=1)
    prices = [
        {
            "from": datetime(2026, 7, 18, 16, 30, tzinfo=zone).isoformat(),
            "till": datetime(2026, 7, 18, 17, 0, tzinfo=zone).isoformat(),
            "price": 0.126,
        },
        *daily_prices(tomorrow, 11, 17, 0.123),
    ]

    forecasts = planner.calculate_per_battery_each_day(
        prices=prices,
        names=["Accu 1"],
        soc_values=[20.0],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=0.92,
        minimum_power=500,
        price_margin=0.013,
        allowed_start_hour=11,
        allowed_end_hour=17,
    )

    assert len(forecasts) == 2
    today_plan, today_battery_plans = forecasts[0]
    tomorrow_plan, tomorrow_battery_plans = forecasts[1]
    assert today_plan.start.date() == today
    assert tomorrow_plan.start.date() == tomorrow
    assert (
        today_battery_plans[0].battery_energy_needed
        < tomorrow_battery_plans[0].battery_energy_needed
    )


def test_restart_inside_current_hour_uses_only_remaining_minutes():
    planner = ChargePlanner("Europe/Amsterdam")
    zone = ZoneInfo("Europe/Amsterdam")
    restart = datetime(2026, 7, 18, 13, 20, tzinfo=zone)
    prices = [
        {
            "from": restart.isoformat(),
            "till": datetime(2026, 7, 18, 14, 0, tzinfo=zone).isoformat(),
            "price": 0.10,
        },
        {
            "from": datetime(2026, 7, 18, 14, 0, tzinfo=zone).isoformat(),
            "till": datetime(2026, 7, 18, 15, 0, tzinfo=zone).isoformat(),
            "price": 0.20,
        },
    ]

    plan, battery_plans = planner.calculate_per_battery_earliest_day(
        prices=prices,
        names=["Accu 1"],
        soc_values=[90.0],
        capacities=[5.0],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=1.0,
        minimum_power=500,
        price_margin=0.20,
        allowed_start_hour=11,
        allowed_end_hour=17,
    )

    assert plan.start == restart
    assert all(
        segment.start >= restart
        for segment in battery_plans[0].segments
    )
    assert plan.hours < 1.0


def test_graph_price_window_contains_economic_hours_for_both_days():
    planner = ChargePlanner("Europe/Amsterdam")
    today = datetime(2026, 7, 18).date()
    tomorrow = today + timedelta(days=1)
    prices = (
        daily_prices(today, 11, 17, 0.126)
        + daily_prices(tomorrow, 11, 17, 0.123)
    )
    plan, _battery_plans = planner.calculate_per_battery_earliest_day(
        prices=prices,
        names=["Accu 1"],
        soc_values=[90.0],
        capacities=[5.12],
        max_charge_values=[2500],
        target_soc=100.0,
        charge_efficiency=0.92,
        minimum_power=500,
        price_margin=0.001,
        allowed_start_hour=11,
        allowed_end_hour=17,
    )

    decorated = planner.with_economic_price_window(
        plan, prices, price_margin=0.001
    )
    visible_days = {
        planner._local_datetime(item["from"]).date()
        for item in decorated.eligible_slots
    }

    assert plan.start.date() == today
    assert visible_days == {today, tomorrow}
