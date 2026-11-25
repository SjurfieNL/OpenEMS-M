from openems_m.validators import (
    validate_battery, validate_grid, validate_host, validate_price, validate_sensors,
)


def bat(**kw):
    base = {"host": "192.168.1.10", "port": 502, "unit_id": 1, "model": "e_v3", "number": 1}
    base.update(kw)
    return base


def test_host():
    assert validate_host("192.168.178.35") is None
    assert validate_host("accu-1.local") is None
    assert validate_host("") == "invalid_host"
    assert validate_host("192.168.1.1 ") is None
    assert validate_host("bad host!") == "invalid_host"


def test_battery_ok_and_limits():
    assert validate_battery(bat(), []) is None
    assert validate_battery(bat(port=0), []) == "invalid_port"
    assert validate_battery(bat(unit_id=248), []) == "invalid_unit_id"
    assert validate_battery(bat(model="x"), []) == "invalid_model"
    assert validate_battery(bat(number=5), []) == "invalid_battery_number"
    assert validate_battery(bat(port="abc"), []) == "invalid_number"


def test_battery_duplicates():
    first = bat()
    assert validate_battery(bat(host="192.168.1.11"), [first]) == "duplicate_battery_number"
    assert validate_battery(bat(number=2), [first]) == "duplicate_battery_host"
    assert validate_battery(bat(number=2, unit_id=2), [first]) is None
    four = [bat(number=n, host=f"10.0.0.{n}") for n in range(1, 5)]
    assert validate_battery(bat(number=4, host="10.0.0.9"), four) == "too_many_batteries"


def test_grid():
    assert validate_grid(8050, 5000, 100) is None
    assert validate_grid(0, 5000, 100) == "invalid_grid_limit"
    assert validate_grid(8050, -1, 100) == "invalid_grid_limit"
    assert validate_grid(100, 0, 100) == "margin_too_large"
    assert validate_grid("x", 1, 1) == "invalid_number"


def test_sensors_and_price():
    assert validate_sensors("sensor.p1", None, "P1") is None
    assert validate_sensors(None, "sensor.ct", "CT") is None
    assert validate_sensors(None, None, "P1") == "no_grid_sensor"
    assert validate_sensors(None, "sensor.ct", "P1") == "preferred_missing"
    assert validate_price("frank_energie", "sensor.prijs") is None
    assert validate_price("custom_sensor", "") == "price_entity_required"
