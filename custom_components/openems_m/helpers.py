"""OpenEMS-instellingen als entiteiten van de integratie.

In de AppDaemon-versie stonden ~170 helpers in een YAML-package
(input_boolean, input_number, input_select, input_text, input_button). Een
integratie kan geen input_*-entiteiten maken, dus maakt OpenEMS-M ze aan als:

    input_boolean.X -> switch.X     input_number.X -> number.X
    input_select.X  -> select.X     input_text.X   -> text.X
    input_button.X  -> button.X

met dezelfde naam (X), opties en grenzen. De tussenlaag vertaalt de oude
namen, zodat de OpenEMS-code ongewijzigd blijft (zie HELPER_ENTITY_MAP).

Beginwaarde, in deze volgorde:
1. de laatst bekende waarde (na een herstart);
2. een waarde uit de wizard (prijs, grenzen, netsensoren, Automatisch);
3. de waarde van de oude input_*-helper, als die nog bestaat (migratie);
4. de standaard van het type (uit, minimum, eerste optie, leeg).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

HELPER_DOMAINS = {
    "input_boolean": "switch",
    "input_number": "number",
    "input_select": "select",
    "input_text": "text",
    "input_button": "button",
}
# Services van de oude helpers -> services van de nieuwe entiteiten.
SERVICE_MAP = {
    ("input_boolean", "turn_on"): ("switch", "turn_on"),
    ("input_boolean", "turn_off"): ("switch", "turn_off"),
    ("input_boolean", "toggle"): ("switch", "toggle"),
    ("input_number", "set_value"): ("number", "set_value"),
    ("input_select", "select_option"): ("select", "select_option"),
    ("input_text", "set_value"): ("text", "set_value"),
    ("input_button", "press"): ("button", "press"),
}


@lru_cache(maxsize=1)
def helper_definitions() -> dict:
    path = Path(__file__).with_name("helpers.json")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def helper_entity_map() -> dict[str, str]:
    """{'input_number.x': 'number.x', ...} voor alle OpenEMS-helpers."""
    mapping = {}
    for helper_type, items in helper_definitions().items():
        domain = HELPER_DOMAINS[helper_type]
        for object_id in items:
            mapping[f"{helper_type}.{object_id}"] = f"{domain}.{object_id}"
    return mapping


def wizard_initial_values(config: dict) -> dict[str, object]:
    """Beginwaarden uit de wizard, per oude helpernaam."""
    values: dict[str, object] = {}
    if "auto_control" in config:
        values["input_boolean.openems_actief"] = bool(config["auto_control"])
    max_import = config.get("max_import_w")
    margin = config.get("safety_margin_w") or 0
    if max_import:
        values["input_number.openems_lb_max_import"] = float(max_import) - float(margin)
    if config.get("max_export_w") is not None:
        values["input_number.openems_lb_max_export"] = float(config["max_export_w"])
    p1, ct = config.get("p1_entity"), config.get("ct_entity")
    preferred = config.get("preferred_sensor") or ("P1" if p1 else "CT")
    if p1 or ct:
        values["input_boolean.openems_lb_p1_uit"] = not p1
        values["input_boolean.openems_lb_ct_uit"] = not ct
        values["input_select.openems_lb_voorkeur"] = preferred
        values["input_select.openems_grid_sensor_bron"] = preferred
        values["input_text.openems_grid_vermogenssensor"] = (
            p1 if preferred == "P1" and p1 else ct or p1
        )
    if config.get("price_entity"):
        values["input_text.openems_frank_all_in_prijssensor"] = str(config["price_entity"])
    if config.get("feed_in_eur_kwh") is not None:
        values["input_number.openems_frank_terugleververgoeding"] = float(
            config["feed_in_eur_kwh"])
    known = helper_entity_map()
    return {key: value for key, value in values.items() if key in known}


def old_helper_state(hass, old_entity_id: str):
    state = hass.states.get(old_entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    return state.state


def build_helper_entities(hass, entry, platform_domain: str) -> list:
    """Maak de helper-entiteiten voor een platform (switch, number, ...)."""
    from . import helper_entities as he

    classes = {
        "switch": he.OpenEMSSwitch,
        "number": he.OpenEMSNumber,
        "select": he.OpenEMSSelect,
        "text": he.OpenEMSText,
        "button": he.OpenEMSButton,
    }
    if platform_domain not in classes:
        return []
    initial = wizard_initial_values(dict(entry.data))
    entities = []
    for helper_type, items in helper_definitions().items():
        if HELPER_DOMAINS[helper_type] != platform_domain:
            continue
        for object_id, conf in items.items():
            old_id = f"{helper_type}.{object_id}"
            entities.append(classes[platform_domain](
                entry, object_id, conf, old_id, initial.get(old_id)))
    return entities
