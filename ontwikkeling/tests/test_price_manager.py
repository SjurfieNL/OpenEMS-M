from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from price_manager import PriceManager


class App:
    def __init__(self, prices):
        self.prices = prices
        self.logs = []

    def get_state(self, entity, attribute=None):
        return self.prices if attribute == "prices" else "0.20"

    def log(self, message, **kwargs):
        self.logs.append(message)


def item(start, hours=1, price=0.20):
    return {
        "from": start.isoformat(),
        "till": (start + timedelta(hours=hours)).isoformat(),
        "price": price,
    }


def test_duplicate_and_invalid_price_slots_are_normalized():
    start = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    first = item(start)
    app = App([first, dict(first), {"price": 0.2}])
    manager = PriceManager(
        app, "sensor.price", "prices", "Europe/Amsterdam"
    )

    result = manager.get_planning_prices()

    assert len(result) == 1
    assert any("dubbel" in message for message in app.logs)


def test_dst_price_times_keep_distinct_offsets():
    manager = PriceManager(
        App([]), "sensor.price", "prices", "Europe/Amsterdam"
    )
    summer_two = manager.local_datetime("2026-10-25T00:00:00+00:00")
    winter_two = manager.local_datetime("2026-10-25T01:00:00+00:00")

    assert summer_two.hour == winter_two.hour == 2
    assert summer_two.utcoffset() != winter_two.utcoffset()
    assert summer_two.timestamp() != winter_two.timestamp()


def test_actionable_prices_drop_history_and_trim_current_interval():
    zone = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 7, 18, 13, 20, tzinfo=zone)
    prices = [
        item(datetime(2026, 7, 18, hour, tzinfo=zone))
        for hour in (11, 12, 13, 14)
    ]
    original = [dict(value) for value in prices]
    manager = PriceManager(
        App([]), "sensor.price", "prices", "Europe/Amsterdam"
    )

    result = manager.get_actionable_prices(prices, now)

    assert len(result) == 2
    assert manager.local_datetime(result[0]["from"]) == now
    assert manager.local_datetime(result[0]["till"]).hour == 14
    assert manager.local_datetime(result[1]["from"]).hour == 14
    assert prices == original


def test_remaining_prices_keep_started_interval_until_original_end():
    zone = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 7, 19, 7, 42, tzinfo=zone)
    prices = [
        item(datetime(2026, 7, 19, hour, tzinfo=zone))
        for hour in (6, 7, 8)
    ]
    manager = PriceManager(
        App([]), "sensor.price", "prices", "Europe/Amsterdam"
    )

    result = manager.get_remaining_prices(prices, now)

    assert len(result) == 2
    assert manager.local_datetime(result[0]["from"]).hour == 7
    assert manager.local_datetime(result[0]["from"]).minute == 0
    assert manager.local_datetime(result[0]["till"]).hour == 8


def test_global_resolution_aggregates_quarters_for_all_planners():
    zone = ZoneInfo("Europe/Amsterdam")
    start = datetime(2026, 7, 19, 7, 0, tzinfo=zone)
    quarters = [
        {
            "from": (start + timedelta(minutes=15 * index)).isoformat(),
            "till": (
                start + timedelta(minutes=15 * (index + 1))
            ).isoformat(),
            "price": price,
        }
        for index, price in enumerate((0.10, 0.20, 0.30, 0.40))
    ]
    manager = PriceManager(
        App([]), "sensor.price", "prices", "Europe/Amsterdam"
    )

    hourly = manager.apply_resolution(quarters, "60 minuten")
    quarter = manager.apply_resolution(quarters, "15 minuten")

    assert len(hourly) == 1
    assert round(hourly[0]["price"], 4) == 0.25
    assert len(quarter) == 4


def test_charge_prices_are_restricted_to_dark_green_economic_slots():
    zone = ZoneInfo("Europe/Amsterdam")
    manager = PriceManager(
        App([]), "sensor.price", "prices", "Europe/Amsterdam"
    )
    blue_now = item(
        datetime(2026, 7, 18, 19, 18, tzinfo=zone),
        price=0.2646,
    )
    green_tomorrow = item(
        datetime(2026, 7, 19, 14, tzinfo=zone),
        price=0.1232,
    )
    blue_tomorrow = item(
        datetime(2026, 7, 19, 21, tzinfo=zone),
        price=0.3107,
    )

    result = manager.restrict_to_economic_slots(
        [blue_now, green_tomorrow, blue_tomorrow],
        [green_tomorrow],
    )

    assert result == [green_tomorrow]


def test_remaining_part_of_current_green_slot_stays_chargeable():
    zone = ZoneInfo("Europe/Amsterdam")
    manager = PriceManager(
        App([]), "sensor.price", "prices", "Europe/Amsterdam"
    )
    full_green = item(
        datetime(2026, 7, 18, 14, tzinfo=zone),
        price=0.1260,
    )
    remaining_green = {
        **full_green,
        "from": datetime(
            2026, 7, 18, 14, 20, tzinfo=zone
        ).isoformat(),
    }

    result = manager.restrict_to_economic_slots(
        [remaining_green],
        [full_green],
    )

    assert result == [remaining_green]
