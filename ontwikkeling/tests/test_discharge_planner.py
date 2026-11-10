from datetime import datetime, timedelta, timezone

from discharge_planner import DischargePlanner
from models import DischargePlan


def prices(values, minutes=60, start=None):
    start = start or datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    return [
        {
            "from": (start + timedelta(minutes=minutes * index)).isoformat(),
            "till": (
                start + timedelta(minutes=minutes * (index + 1))
            ).isoformat(),
            "price": value,
        }
        for index, value in enumerate(values)
    ]


def calculate(price_data, **overrides):
    values = {
        "future_prices": price_data,
        "average_charge_price": 0.10,
        "inventory_cost_per_usable_kwh": 0.20,
        "discharge_margin": 0.05,
        "selection_margin": 0.02,
        "minimum_soc": 20,
        "reserve_soc": 40,
        "names": ["Accu 1", "Accu 2", "Accu 3"],
        "soc_values": [90, 80, 70],
        "capacities": [5.12, 5.12, 5.12],
        "max_discharge_values": [2500, 2500, 2500],
        "discharge_efficiencies": [0.92, 0.92, 0.92],
        "minimum_block_minutes": 15,
        "maximum_export_power": 5000,
        "preferred_min_power": 1000,
        "preferred_max_power": 1500,
        "resolution": "60 minuten",
    }
    values.update(overrides)
    return DischargePlanner("Europe/Amsterdam").calculate(**values)


def test_real_inventory_cost_and_daily_peak_margin_select_export_hours():
    plan = calculate(prices([0.24, 0.25, 0.27, 0.35]))
    assert plan.price_limit == 0.25
    assert [item["price"] for item in plan.eligible_slots] == [0.35]
    assert [
        slot["price"] for block in plan.blocks for slot in block.slots
    ] == [0.35]
    assert (
        plan.cost_basis_source
        == "werkelijke laadprijs met gekozen exportrendement"
    )


def test_higher_daily_peak_margin_adds_attached_profitable_hours():
    narrow = calculate(
        prices([0.30, 0.35, 0.36, 0.34, 0.20]),
        selection_margin=0.0,
    )
    wide = calculate(
        prices([0.30, 0.35, 0.36, 0.34, 0.20]),
        selection_margin=0.03,
    )

    assert [slot["price"] for slot in narrow.eligible_slots] == [0.36]
    assert [slot["price"] for slot in wide.eligible_slots] == [
        0.35, 0.36, 0.34
    ]
    assert list(wide.daily_peak_prices.values()) == [0.36]
    assert round(list(wide.daily_peak_limits.values())[0], 4) == 0.33


def test_peak_window_stops_at_a_non_qualifying_adjacent_hour():
    plan = calculate(
        prices([0.35, 0.20, 0.36, 0.35]),
        selection_margin=0.03,
    )

    assert [slot["price"] for slot in plan.eligible_slots] == [0.36, 0.35]


def test_export_efficiency_is_only_applied_when_explicitly_supplied():
    without_loss = calculate(
        prices([0.20, 0.22]),
        inventory_cost_per_usable_kwh=0.18,
        discharge_margin=0.01,
        selection_margin=0.0,
        export_efficiency=1.0,
    )
    with_loss = calculate(
        prices([0.20, 0.22]),
        inventory_cost_per_usable_kwh=0.18,
        discharge_margin=0.01,
        selection_margin=0.0,
        export_efficiency=0.85,
    )
    assert without_loss.cost_basis_per_kwh == 0.18
    assert round(with_loss.cost_basis_per_kwh, 4) == 0.2118
    assert without_loss.eligible_slots
    assert not with_loss.eligible_slots


def test_reserve_soc_limits_exportable_energy_and_predicted_end_soc():
    plan = calculate(prices([0.40, 0.39, 0.38, 0.37]))
    assert plan.reserve_soc == 40
    assert all(item.predicted_end_soc >= 40 for item in plan.per_battery)
    expected_available = sum(
        5.12 * max(0, soc - 40) / 100 for soc in [90, 80, 70]
    )
    assert abs(plan.available_energy - expected_available) < 1e-9


def test_best_prices_are_selected_first_when_energy_is_limited():
    plan = calculate(
        prices([0.30, 0.50, 0.35]),
        soc_values=[45, 45, 45],
        capacities=[1, 1, 1],
    )
    selected = [
        slot["price"] for block in plan.blocks for slot in block.slots
    ]
    assert selected == [0.50]


def test_planned_power_prefers_1000_to_1500_w_per_active_battery():
    plan = calculate(prices([0.40, 0.39, 0.38]))
    assert plan.blocks
    powers = plan.blocks[0].powers_per_battery
    assert all(0 <= power <= 1500 for power in powers)
    assert sum(powers) >= 1000
    assert any(power >= 1000 for power in powers)


def test_grid_export_is_hard_limited_to_5000_w():
    plan = calculate(
        prices([0.60]),
        maximum_export_power=7500,
        soc_values=[100, 100, 100],
        reserve_soc=20,
    )
    assert plan.planned_export_power <= 5000
    assert plan.planned_total_power <= 5000
    assert plan.planned_total_power <= 7500


def test_charge_intervals_are_never_selected_for_export():
    slots = prices([0.50, 0.49])
    plan = calculate(
        slots,
        excluded_intervals=[(slots[0]["from"], slots[0]["till"])],
    )
    selected = [
        slot["price"] for block in plan.blocks for slot in block.slots
    ]
    assert selected == [0.49]


def test_future_charge_only_funds_export_after_charge_completion():
    slots = prices([0.70, 0.69, 0.50, 0.49])
    values = {
        "average_charge_price": 0.10,
        "inventory_cost_per_usable_kwh": 0.20,
        "discharge_margin": 0.05,
        "selection_margin": 0.0,
        "minimum_soc": 20,
        "reserve_soc": 40,
        "names": ["Accu 1", "Accu 2", "Accu 3"],
        "soc_values": [40, 40, 40],
        "capacities": [1, 1, 1],
        "max_discharge_values": [2500, 2500, 2500],
        "discharge_efficiencies": [1.0, 1.0, 1.0],
        "minimum_block_minutes": 15,
        "maximum_export_power": 5000,
        "preferred_min_power": 1000,
        "preferred_max_power": 1500,
        "resolution": "60 minuten",
    }
    plan = DischargePlanner(
        "Europe/Amsterdam"
    ).calculate_with_energy_resets(
        future_prices=slots,
        energy_resets=[{
            "at": slots[2]["from"],
            "soc_values": [100, 100, 100],
        }],
        **values,
    )

    selected = [
        slot for block in plan.blocks for slot in block.slots
    ]
    assert selected
    assert all(
        datetime.fromisoformat(slot["from"])
        >= datetime.fromisoformat(slots[2]["from"])
        for slot in selected
    )


def test_each_completed_daily_charge_creates_a_new_export_energy_period():
    slots = prices([0.20, 0.50, 0.30, 0.20, 0.60, 0.30])
    values = {
        "average_charge_price": 0.10,
        "inventory_cost_per_usable_kwh": 0.20,
        "discharge_margin": 0.05,
        "selection_margin": 0.0,
        "minimum_soc": 20,
        "reserve_soc": 40,
        "names": ["Accu 1"],
        "soc_values": [40],
        "capacities": [1],
        "max_discharge_values": [1500],
        "discharge_efficiencies": [1.0],
        "minimum_block_minutes": 15,
        "maximum_export_power": 1500,
        "preferred_min_power": 1000,
        "preferred_max_power": 1500,
        "resolution": "60 minuten",
    }
    plan = DischargePlanner(
        "Europe/Amsterdam"
    ).calculate_with_energy_resets(
        future_prices=slots,
        energy_resets=[
            {"at": slots[1]["from"], "soc_values": [100]},
            {"at": slots[4]["from"], "soc_values": [100]},
        ],
        **values,
    )

    selected_prices = [
        slot["price"]
        for block in plan.blocks
        for slot in block.slots
    ]
    assert selected_prices == [0.50, 0.60]


def test_tomorrow_charge_price_cannot_change_tonight_export_threshold():
    tonight = prices(
        [0.30],
        start=datetime(2026, 7, 30, 19, tzinfo=timezone.utc),
    )
    tomorrow = prices(
        [0.40],
        start=datetime(2026, 7, 31, 19, tzinfo=timezone.utc),
    )
    plan = DischargePlanner(
        "Europe/Amsterdam"
    ).calculate_with_energy_resets(
        future_prices=tonight + tomorrow,
        energy_resets=[
            {
                "at": tonight[0]["from"],
                "soc_values": [100],
                "average_charge_price": 0.15,
            },
            {
                "at": tomorrow[0]["from"],
                "soc_values": [100],
                "average_charge_price": 0.23,
            },
        ],
        average_charge_price=0.99,
        inventory_cost_per_usable_kwh=None,
        discharge_margin=0.10,
        selection_margin=0.0,
        minimum_soc=20,
        reserve_soc=40,
        names=["Accu 1"],
        soc_values=[40],
        capacities=[5.12],
        max_discharge_values=[1500],
        discharge_efficiencies=[1.0],
        minimum_block_minutes=15,
        maximum_export_power=1500,
        preferred_min_power=1000,
        preferred_max_power=1500,
        resolution="60 minuten",
    )

    selected = [
        slot
        for block in plan.blocks
        for slot in block.slots
    ]
    assert [slot["price"] for slot in selected] == [0.30, 0.40]


def test_tomorrows_higher_price_cannot_displace_todays_export():
    today = prices(
        [0.30],
        start=datetime(2026, 7, 27, 18, tzinfo=timezone.utc),
    )
    tomorrow = prices(
        [0.60],
        start=datetime(2026, 7, 28, 18, tzinfo=timezone.utc),
    )
    plan = DischargePlanner(
        "Europe/Amsterdam"
    ).calculate_with_energy_resets(
        future_prices=today + tomorrow,
        energy_resets=[],
        average_charge_price=0.10,
        inventory_cost_per_usable_kwh=0.20,
        discharge_margin=0.05,
        selection_margin=0.0,
        minimum_soc=20,
        reserve_soc=40,
        names=["Accu 1"],
        soc_values=[100],
        capacities=[1],
        max_discharge_values=[1000],
        discharge_efficiencies=[1.0],
        minimum_block_minutes=15,
        maximum_export_power=1000,
        preferred_min_power=500,
        preferred_max_power=1000,
        resolution="60 minuten",
    )

    selected = [
        slot for block in plan.blocks for slot in block.slots
    ]
    assert [slot["price"] for slot in selected] == [0.30]
    assert datetime.fromisoformat(selected[0]["from"]).date() == (
        datetime.fromisoformat(today[0]["from"]).date()
    )


def test_export_uses_a_separate_charge_reference_per_calendar_day():
    today = prices(
        [0.30],
        start=datetime(2026, 7, 30, 18, tzinfo=timezone.utc),
    )
    tomorrow = prices(
        [0.27, 0.36],
        start=datetime(2026, 7, 31, 18, tzinfo=timezone.utc),
    )
    plan = DischargePlanner(
        "Europe/Amsterdam"
    ).calculate_with_energy_resets(
        future_prices=today + tomorrow,
        energy_resets=[],
        average_charge_price=0.99,
        average_charge_prices_by_day={
            "2026-07-30": 0.15,
            "2026-07-31": 0.23,
        },
        inventory_cost_per_usable_kwh=0.01,
        discharge_margin=0.05,
        selection_margin=0.0,
        minimum_soc=20,
        reserve_soc=40,
        names=["Accu 1"],
        soc_values=[100],
        capacities=[5.12],
        max_discharge_values=[1000],
        discharge_efficiencies=[1.0],
        minimum_block_minutes=15,
        maximum_export_power=1000,
        preferred_min_power=500,
        preferred_max_power=1000,
        resolution="60 minuten",
    )

    assert [slot["price"] for slot in plan.eligible_slots] == [0.30, 0.36]
    assert "laaduren van deze dag" in plan.cost_basis_source


def test_forecast_home_use_does_not_spread_export_over_the_hour():
    slots = prices(
        [0.40],
        start=datetime(2026, 7, 31, 18, tzinfo=timezone.utc),
    )
    common = dict(
        future_prices=slots,
        energy_resets=[],
        average_charge_price=0.10,
        inventory_cost_per_usable_kwh=None,
        discharge_margin=0.05,
        selection_margin=0.0,
        minimum_soc=20,
        reserve_soc=40,
        names=["Accu 1"],
        soc_values=[100],
        capacities=[5.12],
        max_discharge_values=[2500],
        discharge_efficiencies=[1.0],
        minimum_block_minutes=15,
        maximum_export_power=2500,
        preferred_min_power=500,
        preferred_max_power=2500,
        resolution="60 minuten",
    )
    planner = DischargePlanner("Europe/Amsterdam")
    without_home = planner.calculate_with_energy_resets(**common)
    with_home = planner.calculate_with_energy_resets(
        **common,
        fallback_home_power=1000,
    )

    assert with_home.available_energy < without_home.available_energy
    assert with_home.planned_total_power == without_home.planned_total_power
    assert with_home.blocks[0].expected_energy == without_home.blocks[0].expected_energy


def test_idle_forecast_does_not_reduce_export_energy():
    start = datetime(2026, 8, 10, 6, tzinfo=timezone.utc)
    slots = prices([0.40, 0.39], start=start)
    common = dict(
        future_prices=slots,
        energy_resets=[],
        average_charge_price=0.10,
        inventory_cost_per_usable_kwh=None,
        discharge_margin=0.05,
        selection_margin=0.02,
        minimum_soc=20,
        reserve_soc=40,
        names=["Accu 1"],
        soc_values=[100],
        capacities=[5.12],
        max_discharge_values=[2500],
        discharge_efficiencies=[1.0],
        minimum_block_minutes=15,
        maximum_export_power=2500,
        preferred_min_power=500,
        preferred_max_power=2500,
        resolution="60 minuten",
        home_forecast=[
            {
                "start": (start + timedelta(minutes=15 * index)).isoformat(),
                "vermogen_w": 1000,
                "samples": 4,
            }
            for index in range(8)
        ],
    )
    planner = DischargePlanner("Europe/Amsterdam")
    all_home_use = planner.calculate_with_energy_resets(
        **common,
        home_use_intervals=[(slots[0]["from"], slots[1]["till"])],
    )
    first_hour_only = planner.calculate_with_energy_resets(
        **common,
        home_use_intervals=[(slots[0]["from"], slots[0]["till"])],
    )

    assert first_hour_only.available_energy > all_home_use.available_energy
    assert (
        first_hour_only.available_energy - all_home_use.available_energy
        == 1.0
    )


def test_dynamic_reserve_ignores_forecast_outside_home_use_intervals():
    planner = DischargePlanner("Europe/Amsterdam")
    now = datetime(2026, 8, 10, 5, tzinfo=timezone.utc)
    points = [
        (now + timedelta(minutes=15 * index), 1000.0)
        for index in range(8)
    ]
    self_consumption = [(now, now + timedelta(hours=1))]

    assert planner._forecast_energy(
        points,
        now,
        now + timedelta(hours=2),
        self_consumption,
    ) == 1.0
    assert len(planner._forecast_points(
        [
            {
                "start": moment.isoformat(),
                "vermogen_w": power,
                "samples": 4,
            }
            for moment, power in points[:4]
        ],
        now,
        now + timedelta(hours=2),
        self_consumption,
    )) == 4


def test_same_target_charge_resets_export_energy_on_each_day():
    today = prices(
        [0.30],
        start=datetime(2026, 7, 27, 18, tzinfo=timezone.utc),
    )
    tomorrow = prices(
        [0.60],
        start=datetime(2026, 7, 28, 18, tzinfo=timezone.utc),
    )
    plan = DischargePlanner(
        "Europe/Amsterdam"
    ).calculate_with_energy_resets(
        future_prices=today + tomorrow,
        energy_resets=[
            {"at": today[0]["from"], "soc_values": [100]},
            {"at": tomorrow[0]["from"], "soc_values": [100]},
        ],
        average_charge_price=0.10,
        inventory_cost_per_usable_kwh=0.20,
        discharge_margin=0.05,
        selection_margin=0.0,
        minimum_soc=20,
        reserve_soc=40,
        names=["Accu 1"],
        soc_values=[40],
        capacities=[1],
        max_discharge_values=[1000],
        discharge_efficiencies=[1.0],
        minimum_block_minutes=15,
        maximum_export_power=1000,
        preferred_min_power=500,
        preferred_max_power=1000,
        resolution="60 minuten",
    )

    selected_prices = [
        slot["price"]
        for block in plan.blocks
        for slot in block.slots
    ]
    assert selected_prices == [0.30, 0.60]


def test_quarter_hour_resolution_and_minimum_block_are_respected():
    plan = calculate(
        prices([0.50, 0.49, 0.20], minutes=15),
        resolution="15 minuten",
        minimum_block_minutes=30,
    )
    assert plan.resolution_minutes == 15
    assert len(plan.blocks) == 1
    assert len(plan.blocks[0].slots) == 2


def test_dynamic_reserve_only_promotes_dark_slot_before_next_charge():
    planner = DischargePlanner("Europe/Amsterdam")
    now = datetime(2026, 7, 28, 4, tzinfo=timezone.utc)
    slots = prices([0.40, 0.20], start=now)
    base_plan = DischargePlan(
        price_limit=0.25,
        available_energy=0,
        blocks=[],
        eligible_slots=[slots[0]],
        reserve_soc=46,
    )
    forecast = [
        {
            "start": (now + timedelta(minutes=15 * index)).isoformat(),
            "vermogen_w": 100,
            "samples": 4,
        }
        for index in range(8)
    ]

    result = planner.with_dynamic_reserve_before_charge(
        base_plan,
        slots,
        now,
        now + timedelta(hours=2),
        forecast,
        next_charge_reference_price=0.10,
        average_charge_price=0.10,
        inventory_cost_per_usable_kwh=0.20,
        discharge_margin=0.05,
        selection_margin=0.0,
        minimum_soc=20,
        reserve_soc=46,
        dynamic_reserve_kwh=2.0,
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[46, 46, 46],
        capacities=[5.12, 5.12, 5.12],
        max_discharge_values=[2500, 2500, 2500],
        discharge_efficiencies=[0.92, 0.92, 0.92],
        minimum_block_minutes=15,
        maximum_export_power=5000,
        preferred_min_power=1000,
        preferred_max_power=1500,
        resolution="60 minuten",
        excluded_intervals=[],
    )

    assert result.blocks
    assert result.blocks[0].slots == [slots[0]]
    assert 20 < result.blocks[0].reserve_soc < 46
    # De vrijgegeven kWh wordt vergeleken met de eerstvolgende geplande
    # laadprijs, niet met de kostprijsbasis van vandaag.
    assert result.blocks[0].cost_basis_per_kwh == 0.10


def test_dynamic_reserve_releases_nothing_without_a_known_next_charge_price():
    """27-08-2026: zolang er geen eerstvolgende geplande laadprijs bekend
    is (bv. omdat morgen se prijzen nog niet gepubliceerd zijn), mag deze
    laag helemaal niets vrijgeven — de vaste reserve blijft dan leidend."""
    planner = DischargePlanner("Europe/Amsterdam")
    now = datetime(2026, 7, 28, 4, tzinfo=timezone.utc)
    slots = prices([0.40, 0.20], start=now)
    base_plan = DischargePlan(
        price_limit=0.25,
        available_energy=0,
        blocks=[],
        eligible_slots=[slots[0]],
        reserve_soc=46,
    )
    forecast = [
        {
            "start": (now + timedelta(minutes=15 * index)).isoformat(),
            "vermogen_w": 100,
            "samples": 4,
        }
        for index in range(8)
    ]

    result = planner.with_dynamic_reserve_before_charge(
        base_plan,
        slots,
        now,
        now + timedelta(hours=2),
        forecast,
        # next_charge_reference_price bewust niet meegegeven (None).
        discharge_margin=0.05,
        selection_margin=0.0,
        minimum_soc=20,
        reserve_soc=46,
        dynamic_reserve_kwh=2.0,
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[46, 46, 46],
        capacities=[5.12, 5.12, 5.12],
        max_discharge_values=[2500, 2500, 2500],
        discharge_efficiencies=[0.92, 0.92, 0.92],
        minimum_block_minutes=15,
        maximum_export_power=5000,
        preferred_min_power=1000,
        preferred_max_power=1500,
        resolution="60 minuten",
        excluded_intervals=[],
    )

    assert result is base_plan
    assert result.blocks == []


def test_dynamic_reserve_keeps_fixed_plan_when_forecast_is_incomplete():
    planner = DischargePlanner("Europe/Amsterdam")
    now = datetime(2026, 7, 28, 4, tzinfo=timezone.utc)
    slots = prices([0.40], start=now)
    base_plan = DischargePlan(
        price_limit=0.25,
        available_energy=0,
        blocks=[],
        eligible_slots=slots,
        reserve_soc=46,
    )

    result = planner.with_dynamic_reserve_before_charge(
        base_plan,
        slots,
        now,
        now + timedelta(hours=2),
        [{
            "start": now.isoformat(),
            "vermogen_w": 100,
            "samples": 4,
        }],
        next_charge_reference_price=0.10,
        minimum_soc=20,
        reserve_soc=46,
        names=["Accu 1"],
        soc_values=[46],
        capacities=[5.12],
    )

    assert result is base_plan
    assert result.blocks == []


def test_dynamic_reserve_never_promotes_noneligible_price_slot():
    planner = DischargePlanner("Europe/Amsterdam")
    now = datetime(2026, 7, 28, 4, tzinfo=timezone.utc)
    slots = prices([0.40], start=now)
    base_plan = DischargePlan(
        price_limit=0.25,
        available_energy=0,
        blocks=[],
        eligible_slots=[],
        reserve_soc=46,
    )

    result = planner.with_dynamic_reserve_before_charge(
        base_plan,
        slots,
        now,
        now + timedelta(hours=1),
        [],
        next_charge_reference_price=0.10,
        minimum_soc=20,
        reserve_soc=46,
        names=["Accu 1"],
        soc_values=[46],
        capacities=[5.12],
    )

    assert result is base_plan
    assert result.blocks == []


def test_winter_time_repeated_hour_keeps_two_real_hours():
    start = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
    plan = calculate(prices([0.40, 0.41], start=start))
    assert len(plan.blocks) == 1
    assert plan.blocks[0].hours == 2.0


def _floor_plan(daily_reference):
    start = datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
    evening = prices([0.30, 0.3185, 0.31, 0.30], start=start)
    return DischargePlanner("Europe/Amsterdam").calculate_with_energy_resets(
        future_prices=evening,
        energy_resets=[],
        average_charge_price=0.31,
        average_charge_prices_by_day={"2026-11-01": daily_reference},
        inventory_cost_per_usable_kwh=0.3113,
        export_efficiency=0.90,
        discharge_margin=0.0507,
        selection_margin=0.02,
        minimum_soc=12,
        reserve_soc=12,
        names=["Accu 1"],
        soc_values=[100],
        capacities=[5.12],
        max_discharge_values=[1000],
        discharge_efficiencies=[1.0],
        minimum_block_minutes=15,
        maximum_export_power=1000,
        preferred_min_power=500,
        preferred_max_power=1000,
        resolution="60 minuten",
    )


def test_low_daily_reference_cannot_push_cost_basis_below_inventory_cost():
    # Gemeld geval: dagreferentie 0,2516 < voorraadprijs 0,3113 -> paars.
    plan = _floor_plan(0.2516)
    assert plan.cost_basis_per_kwh >= 0.3113 / 0.90 - 1e-9
    assert plan.eligible_slots == []


def test_higher_daily_reference_still_wins_over_inventory_floor():
    plan = _floor_plan(0.4054)
    assert abs(plan.cost_basis_per_kwh - 0.4054) < 1e-9
