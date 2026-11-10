from datetime import datetime, timedelta, timezone

from self_consumption_planner import SelfConsumptionPlanner


def _prices(values):
    start = datetime(2026, 7, 18, 10, tzinfo=timezone.utc)
    return [
        {
            "from": (start + timedelta(hours=index)).isoformat(),
            "till": (start + timedelta(hours=index + 1)).isoformat(),
            "price": price,
        }
        for index, price in enumerate(values)
    ]


def _plan(values):
    return SelfConsumptionPlanner("Europe/Amsterdam").calculate(
        future_prices=_prices(values),
        average_charge_price=0.10,
        discharge_margin=0.05,
        minimum_soc=20,
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[80, 80, 80],
        capacities=[5.12, 5.12, 5.12],
        discharge_efficiencies=[0.92, 0.92, 0.92],
        minimum_block_minutes=30,
    )


def test_profitable_future_hours_still_create_self_consumption_block():
    plan = _plan([0.12, 0.20, 0.22])
    assert plan.blocks
    assert plan.blocks[0].start.hour == 13
    assert not hasattr(plan.blocks[0], "expected_home_power")


def test_visible_all_in_price_difference_is_the_only_margin():
    plan = SelfConsumptionPlanner("Europe/Amsterdam").calculate(
        future_prices=_prices([0.19, 0.20]),
        average_charge_price=0.12,
        discharge_margin=0.07,
        minimum_soc=20,
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[80, 80, 80],
        capacities=[5.12, 5.12, 5.12],
        discharge_efficiencies=[0.92, 0.92, 0.92],
        minimum_block_minutes=30,
    )

    assert plan.price_limit == 0.19
    assert plan.cost_basis_per_kwh == 0.12
    assert plan.cost_basis_source == "all-in prijs gepland laadblok"
    assert [slot["price"] for slot in plan.eligible_slots] == [0.19, 0.20]


def test_charge_window_is_never_also_a_self_consumption_window():
    slots = _prices([0.20, 0.30])
    plan = SelfConsumptionPlanner("Europe/Amsterdam").calculate(
        future_prices=slots,
        average_charge_price=0.10,
        discharge_margin=0.05,
        minimum_soc=20,
        names=["Accu 1"],
        soc_values=[80],
        capacities=[5.12],
        discharge_efficiencies=[0.92],
        minimum_block_minutes=30,
        excluded_intervals=[(
            slots[0]["from"], slots[0]["till"]
        )],
    )

    assert [slot["price"] for slot in plan.eligible_slots] == [0.30]
    assert all(
        block.start >= datetime.fromisoformat(slots[1]["from"])
        for block in plan.blocks
    )


def test_self_consumption_and_export_plans_have_separate_context_fields():
    from models import EMSContext

    fields = EMSContext.__dataclass_fields__
    assert "discharge_plan" in fields
    assert "export_plan" in fields


def test_self_consumption_uses_a_separate_charge_reference_per_day():
    planner = SelfConsumptionPlanner("Europe/Amsterdam")
    today = [
        {
            "from": "2026-07-30T18:00:00+02:00",
            "till": "2026-07-30T19:00:00+02:00",
            "price": 0.30,
        },
    ]
    tomorrow = [
        {
            "from": "2026-07-31T18:00:00+02:00",
            "till": "2026-07-31T19:00:00+02:00",
            "price": 0.27,
        },
        {
            "from": "2026-07-31T19:00:00+02:00",
            "till": "2026-07-31T20:00:00+02:00",
            "price": 0.36,
        },
    ]

    plan = planner.calculate(
        future_prices=today + tomorrow,
        average_charge_price=0.99,
        average_charge_prices_by_day={
            "2026-07-30": 0.15,
            "2026-07-31": 0.23,
        },
        discharge_margin=0.05,
        minimum_soc=20,
        names=["Accu 1"],
        soc_values=[80],
        capacities=[5.12],
        discharge_efficiencies=[0.92],
        minimum_block_minutes=30,
    )

    assert [slot["price"] for slot in plan.eligible_slots] == [0.30, 0.36]


def test_self_consumption_block_never_crosses_local_midnight():
    planner = SelfConsumptionPlanner("Europe/Amsterdam")
    slots = [
        {
            "from": "2026-07-30T23:00:00+02:00",
            "till": "2026-07-31T00:00:00+02:00",
            "price": 0.30,
        },
        {
            "from": "2026-07-31T00:00:00+02:00",
            "till": "2026-07-31T01:00:00+02:00",
            "price": 0.30,
        },
    ]

    plan = planner.calculate(
        future_prices=slots,
        average_charge_price=0.10,
        average_charge_prices_by_day={
            "2026-07-30": 0.10,
            "2026-07-31": 0.10,
        },
        discharge_margin=0.05,
        minimum_soc=20,
        names=["Accu 1"],
        soc_values=[80],
        capacities=[5.12],
        discharge_efficiencies=[0.92],
        minimum_block_minutes=30,
    )

    assert len(plan.blocks) == 2
    assert plan.blocks[0].end.date() == datetime(2026, 7, 31).date()
    assert plan.blocks[1].start.date() == datetime(2026, 7, 31).date()


def test_started_self_consumption_survives_changed_price_reference():
    planner = SelfConsumptionPlanner("Europe/Amsterdam")
    slots = [
        {
            "from": "2026-08-08T17:00:00+02:00",
            "till": "2026-08-08T18:00:00+02:00",
            "price": 0.2087,
        },
        {
            "from": "2026-08-08T18:00:00+02:00",
            "till": "2026-08-08T19:00:00+02:00",
            "price": 0.2952,
        },
    ]
    common = {
        "future_prices": slots,
        "discharge_margin": 0.058,
        "minimum_soc": 12,
        "names": ["Accu 1", "Accu 2", "Accu 3"],
        "soc_values": [99, 99, 99],
        "capacities": [5.12, 5.12, 5.12],
        "discharge_efficiencies": [0.92, 0.92, 0.92],
        "minimum_block_minutes": 30,
    }
    previous = planner.calculate(
        average_charge_price=0.1197,
        **common,
    )
    recalculated = planner.calculate(
        average_charge_price=0.1536,
        **common,
    )
    now = datetime.fromisoformat("2026-08-08T17:00:19+02:00")

    assert previous.blocks[0].start.hour == 17
    assert recalculated.blocks[0].start.hour == 18

    locked = planner.lock_started_interval(
        recalculated, previous, now
    )

    assert locked.blocks[0] is previous.blocks[0]
    assert locked.blocks[0].start.hour == 17
    assert locked.blocks[0].end == previous.blocks[0].end
    assert any(
        str(slot["from"]).startswith("2026-08-08T17:00:00")
        for slot in locked.eligible_slots
    )


def test_future_self_consumption_can_still_be_replanned():
    planner = SelfConsumptionPlanner("Europe/Amsterdam")
    previous = _plan([0.20, 0.22])
    recalculated = _plan([0.10, 0.22])
    before_start = previous.blocks[0].start - timedelta(minutes=1)

    result = planner.lock_started_interval(
        recalculated, previous, before_start
    )

    assert result is recalculated
