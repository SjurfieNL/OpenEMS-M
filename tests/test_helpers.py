"""OpenEMS-instellingen als entiteiten (helpers.py / helper_entities.py)."""

import asyncio
import json
import pathlib
from types import SimpleNamespace

from openems_m import helper_entities as he
from openems_m.helpers import (
    HELPER_DOMAINS, SERVICE_MAP, build_helper_entities, helper_definitions,
    helper_entity_map, wizard_initial_values,
)

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "openems_m"


def run(coro):
    return asyncio.run(coro)


class States:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, entity_id):
        if entity_id not in self.values:
            return None
        return SimpleNamespace(state=self.values[entity_id])


def entry(**data):
    return SimpleNamespace(entry_id="e1", data=data)


def added(entity, hass_states=None, last_state=None, last_number=None):
    entity.hass = SimpleNamespace(states=States(hass_states))
    entity.last_state = None if last_state is None else SimpleNamespace(state=last_state)
    entity.last_number = None if last_number is None else SimpleNamespace(
        native_value=last_number)
    run(entity.async_added_to_hass())
    return entity


def test_all_172_helpers_are_defined_and_mapped():
    defs = helper_definitions()
    assert {k: len(v) for k, v in defs.items()} == {
        "input_boolean": 36, "input_button": 6, "input_number": 33,
        "input_select": 15, "input_text": 82}
    mapping = helper_entity_map()
    assert len(mapping) == 172
    for extra in ("marstek_doel_soc", "marstek_minimum_soc", "marstek_prijsmarge",
                  "marstek_minimaal_prijsverschil_ontladen"):
        assert mapping[f"input_number.{extra}"] == f"number.{extra}"
    assert defs["input_number"]["marstek_prijsmarge"]["max"] == 0.05
    assert mapping["input_boolean.openems_actief"] == "switch.openems_actief"
    assert mapping["input_number.openems_lb_max_import"] == "number.openems_lb_max_import"
    assert all(v.split(".")[0] in HELPER_DOMAINS.values() for v in mapping.values())


def test_helpers_json_matches_generator_source():
    src = ROOT.parents[2] / "config" / "packages" / "openems_helper.yaml"  # werkmap
    if not src.exists():
        import pytest
        pytest.skip("werkmap-bron niet aanwezig")
    import yaml
    yaml_data = yaml.safe_load(src.read_text(encoding="utf-8"))
    on_disk = json.loads((ROOT / "helpers.json").read_text(encoding="utf-8"))
    for helper_type, items in yaml_data.items():
        assert set(items) <= set(on_disk[helper_type])


def test_wizard_values_map_to_helpers():
    values = wizard_initial_values({
        "auto_control": False, "max_import_w": 8050, "safety_margin_w": 100,
        "max_export_w": 5000, "p1_entity": "sensor.p1", "ct_entity": None,
        "preferred_sensor": "P1", "price_entity": "sensor.prijs", "feed_in_eur_kwh": 0.09,
    })
    assert values == {
        "input_boolean.openems_actief": False,
        "input_number.openems_lb_max_import": 7950.0,
        "input_number.openems_lb_max_export": 5000.0,
        "input_boolean.openems_lb_p1_uit": False,
        "input_boolean.openems_lb_ct_uit": True,
        "input_select.openems_lb_voorkeur": "P1",
        "input_select.openems_grid_sensor_bron": "P1",
        "input_text.openems_grid_vermogenssensor": "sensor.p1",
        "input_text.openems_frank_all_in_prijssensor": "sensor.prijs",
        "input_number.openems_frank_terugleververgoeding": 0.09,
    }


def test_build_entities_per_platform_with_fixed_entity_ids():
    counts = {p: len(build_helper_entities(None, entry(), p))
              for p in ("switch", "number", "select", "text", "button", "sensor")}
    assert counts == {"switch": 36, "number": 33, "select": 15, "text": 82,
                      "button": 6, "sensor": 0}
    switch = build_helper_entities(None, entry(), "switch")[0]
    assert switch.entity_id.startswith("switch.openems_")
    assert switch._attr_unique_id.startswith("e1_helper_")


def _one(platform, object_id, **data):
    for item in build_helper_entities(None, entry(**data), platform):
        if item.entity_id == f"{platform}.{object_id}":
            return item
    raise AssertionError(object_id)


def test_start_value_order_restored_wizard_old_default():
    # 1. laatst bekend wint van alles
    sw = added(_one("switch", "openems_actief", auto_control=True),
               {"input_boolean.openems_actief": "off"}, last_state="off")
    assert sw._attr_is_on is False
    # 2. wizard wint van oude helper
    sw = added(_one("switch", "openems_actief", auto_control=True),
               {"input_boolean.openems_actief": "off"})
    assert sw._attr_is_on is True
    # 3. oude helper (migratie)
    sw = added(_one("switch", "openems_lb_beide_tegelijk"),
               {"input_boolean.openems_lb_beide_tegelijk": "on"})
    assert sw._attr_is_on is True
    # 4. standaard
    assert added(_one("switch", "openems_lb_beide_tegelijk"))._attr_is_on is False


def test_number_clamps_and_restores():
    num = added(_one("number", "openems_lb_max_import"), last_number=99999)
    assert num._attr_native_value == num._attr_native_max_value
    num = added(_one("number", "openems_lb_max_import"),
                {"input_number.openems_lb_max_import": "8010.0"})
    assert num._attr_native_value == 8010.0
    num = added(_one("number", "openems_lb_max_import"))
    assert num._attr_native_value == num._attr_native_min_value
    run(num.async_set_native_value(5000))
    assert num._attr_native_value == 5000 and num.written == 1


def test_select_falls_back_on_invalid_values():
    sel = added(_one("select", "openems_lb_voorkeur"), last_state="Onzin")
    assert sel._attr_current_option == sel._attr_options[0]
    sel = added(_one("select", "openems_lb_voorkeur", preferred_sensor="CT",
                     ct_entity="sensor.ct"))
    assert sel._attr_current_option == "CT"
    try:
        run(sel.async_select_option("X"))
    except ValueError:
        pass
    else:
        raise AssertionError("ongeldige optie geaccepteerd")


def test_text_keeps_old_value_and_limits_length():
    txt = added(_one("text", "openems_grid_vermogenssensor"),
                {"input_text.openems_grid_vermogenssensor": "sensor.net_vermogen_snel"})
    assert txt._attr_native_value == "sensor.net_vermogen_snel"
    run(txt.async_set_value("x" * 400))
    assert len(txt._attr_native_value) == txt._attr_native_max <= 255


def test_service_map_covers_all_services_used_by_ems():
    ems = (ROOT / "ems")
    import re
    used = set()
    for path in ems.glob("*.py"):
        used |= set(re.findall(r'"(input_[a-z]+)/([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert used <= set(SERVICE_MAP), used - set(SERVICE_MAP)
