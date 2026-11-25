import asyncio
import json
import pathlib
import re
from types import SimpleNamespace

import pytest

from openems_m import config_flow as cf
from openems_m.config_flow import BatterySubentryFlow, OpenEMSConfigFlow
from openems_m.connection import CannotConnect

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "openems_m"


def run(coro):
    return asyncio.run(coro)


PRICE = {"price_source": "frank_energie", "price_entity": "sensor.prijs",
         "feed_in_eur_kwh": 0.0}
GRID = {"max_import_w": 8050, "max_export_w": 5000, "safety_margin_w": 100}


def bat(host, number, more=False, name=None):
    return {"name": name or f"Accu {number}", "host": host, "port": 502.0, "unit_id": 1.0,
            "model": "e_v3", "number": float(number), "add_another": more}


@pytest.fixture
def online(monkeypatch):
    """Elke accu antwoordt, behalve IP's in `offline`."""
    offline = set()
    tested = []

    async def fake_test(hass, host, port, unit_id, model):
        tested.append((host, port, unit_id, model))
        if host in offline:
            raise CannotConnect("geen verbinding")
        return {"soc": 64.0, "firmware": "149"}
    monkeypatch.setattr(cf, "async_test_connection", fake_test)
    return SimpleNamespace(offline=offline, tested=tested)


def new_flow():
    flow = OpenEMSConfigFlow()
    flow.hass = object()
    return flow


def test_full_flow_creates_battery_subentries(online):
    flow = new_flow()
    assert run(flow.async_step_user())["step_id"] == "user"
    assert run(flow.async_step_user(PRICE))["step_id"] == "battery"
    res = run(flow.async_step_battery(bat("10.0.0.1", 1, True, "Berging Accu1")))
    assert res["step_id"] == "battery"
    assert run(flow.async_step_battery(bat("10.0.0.2", 2)))["step_id"] == "grid"
    assert run(flow.async_step_grid(GRID))["step_id"] == "sensors"
    assert run(flow.async_step_sensors({"p1_entity": "sensor.p1", "ct_entity": "sensor.ct",
                                        "preferred_sensor": "CT"}))["step_id"] == "start"
    res = run(flow.async_step_start({"auto_control": False}))
    assert res["type"] == "create_entry"
    assert "batteries" not in res["data"]
    assert res["data"]["auto_control"] is False and res["data"]["max_import_w"] == 8050.0
    subs = res["subentries"]
    assert [s["title"] for s in subs] == ["Berging Accu1", "Accu 2"]
    first = subs[0]
    assert first["subentry_type"] == "battery"
    assert first["unique_id"] == "10.0.0.1:502:1"
    assert first["data"] == {"host": "10.0.0.1", "port": 502, "unit_id": 1,
                             "device_version": "E v3", "model": "e_v3",
                             "openems_battery_number": 1, "name": "Berging Accu1"}
    assert online.tested == [("10.0.0.1", 502, 1, "e_v3"), ("10.0.0.2", 502, 1, "e_v3")]


def test_unreachable_battery_stays_on_step(online):
    online.offline.add("10.0.0.9")
    flow = new_flow()
    run(flow.async_step_user(PRICE))
    res = run(flow.async_step_battery(bat("10.0.0.9", 1)))
    assert res["step_id"] == "battery" and res["errors"] == {"base": "cannot_connect"}
    assert flow._batteries == []


def test_invalid_input_is_not_connection_tested(online):
    flow = new_flow()
    run(flow.async_step_user(PRICE))
    res = run(flow.async_step_battery(bat("bad host!", 1)))
    assert res["errors"] == {"base": "invalid_host"}
    res = run(flow.async_step_battery({**bat("10.0.0.1", 1), "name": "  "}))
    assert res["errors"] == {"base": "name_required"}
    assert online.tested == []


def test_last_reading_shown_in_next_battery_form(online):
    flow = new_flow()
    run(flow.async_step_user(PRICE))
    run(flow.async_step_battery(bat("10.0.0.1", 1, True)))
    assert flow._last_reading == "Accu 1: SOC 64 %, firmware 149"


def test_other_steps_still_validate(online):
    flow = new_flow()
    run(flow.async_step_user(PRICE))
    run(flow.async_step_battery(bat("10.0.0.1", 1)))
    res = run(flow.async_step_grid({**GRID, "safety_margin_w": 9000}))
    assert res["errors"] == {"base": "margin_too_large"}
    res = run(flow.async_step_sensors({"preferred_sensor": "P1"}))
    assert res["errors"] == {"base": "no_grid_sensor"}


def test_max_four_batteries_then_grid(online):
    flow = new_flow()
    run(flow.async_step_user(PRICE))
    for n in range(1, 4):
        assert run(flow.async_step_battery(bat(f"10.0.0.{n}", n, True)))["step_id"] == "battery"
    assert run(flow.async_step_battery(bat("10.0.0.4", 4, True)))["step_id"] == "grid"


def _parent_with(*batteries):
    subs = {}
    for i, data in enumerate(batteries):
        subs[f"s{i}"] = SimpleNamespace(subentry_type="battery", data=data,
                                        subentry_id=f"s{i}", title=data["name"])
    return SimpleNamespace(subentries=subs)


def test_subentry_flow_adds_battery(online):
    flow = BatterySubentryFlow()
    flow.hass = object()
    flow.parent_entry = _parent_with({"host": "10.0.0.1", "port": 502, "unit_id": 1,
                                      "openems_battery_number": 1, "name": "Accu 1"})
    form = run(flow.async_step_user())
    assert form["step_id"] == "user" and form["placeholders"] == {"count": "1"}
    res = run(flow.async_step_user(bat("10.0.0.2", 2)))
    assert res["type"] == "create_entry" and res["title"] == "Accu 2"
    assert res["unique_id"] == "10.0.0.2:502:1"
    assert res["data"]["openems_battery_number"] == 2


def test_subentry_flow_rejects_duplicates(online):
    flow = BatterySubentryFlow()
    flow.hass = object()
    flow.parent_entry = _parent_with({"host": "10.0.0.1", "port": 502, "unit_id": 1,
                                      "openems_battery_number": 1, "name": "Accu 1"})
    res = run(flow.async_step_user(bat("10.0.0.5", 1)))
    assert res["errors"] == {"base": "duplicate_battery_number"}
    res = run(flow.async_step_user(bat("10.0.0.1", 2)))
    assert res["errors"] == {"base": "duplicate_battery_host"}
    assert online.tested == []


def test_supported_subentry_types():
    assert OpenEMSConfigFlow.async_get_supported_subentry_types(None) == {
        "battery": BatterySubentryFlow}


def test_translations_cover_all_keys():
    flow_src = (ROOT / "config_flow.py").read_text()
    val_src = (ROOT / "validators.py").read_text()
    errors = set(re.findall(r'return "([a-z_]+)"', val_src))
    errors |= set(re.findall(r'return "([a-z_]+)", None', flow_src))
    steps = set(re.findall(r'step_id="([a-z_]+)"', flow_src)) - {"user"} | {"user"}
    for name in ("strings.json", "translations/en.json", "translations/nl.json"):
        t = json.loads((ROOT / name).read_text(encoding="utf-8"))
        assert errors <= set(t["config"]["error"]), (name, errors - set(t["config"]["error"]))
        assert {"user", "battery", "grid", "sensors", "start"} <= set(t["config"]["step"])
        sub = t["config_subentries"]["battery"]
        assert errors <= set(sub["error"]) and "user" in sub["step"]
        assert sub["initiate_flow"]["user"]
        assert "sensor" in t["entity"] and "number" in t["entity"]


def test_schema_keys_have_labels(online):
    t = json.loads((ROOT / "translations/nl.json").read_text(encoding="utf-8"))
    steps = t["config"]["step"]
    flow = new_flow()
    forms = {"user": run(flow.async_step_user())}
    run(flow.async_step_user(PRICE))
    forms["battery"] = run(flow.async_step_battery())
    forms["grid"] = run(flow.async_step_grid())
    forms["sensors"] = run(flow.async_step_sensors())
    forms["start"] = run(flow.async_step_start())
    for step, form in forms.items():
        keys = {k.key for k in form["schema"]}
        assert keys <= set(steps[step]["data"]), (step, keys - set(steps[step]["data"]))
    sub = BatterySubentryFlow()
    sub.hass = object()
    sub.parent_entry = _parent_with()
    keys = {k.key for k in run(sub.async_step_user())["schema"]}
    assert keys <= set(t["config_subentries"]["battery"]["step"]["user"]["data"])


def test_manifest_and_hacs():
    m = json.loads((ROOT / "manifest.json").read_text())
    assert m["domain"] == "openems_m" and m["config_flow"] is True and m["version"]
    assert any(r.startswith("pymodbus") for r in m["requirements"])
    h = json.loads((ROOT.parents[1] / "hacs.json").read_text())
    assert h["name"] == "OpenEMS-M"
