"""Gedeelde opzet voor de entiteitsplatforms.

Per platform: de OpenEMS-instellingen (helpers) van dit platform, en per accu
de entiteiten van de ingebouwde Marstek-driver (onder de accu-subentry).
"""

from __future__ import annotations

import importlib

from .const import DOMAIN
from .helpers import build_helper_entities

DRIVER_PLATFORMS = {"sensor", "switch", "select", "button", "number", "binary_sensor"}


async def async_setup_platform_for_batteries(hass, entry, async_add_entities, platform: str):
    helpers = build_helper_entities(hass, entry, platform)
    if helpers:
        async_add_entities(helpers)
    if platform not in DRIVER_PLATFORMS:
        return
    driver_platform = importlib.import_module(f"{__package__}.driver.{platform}")
    batteries = hass.data[DOMAIN][entry.entry_id]["batteries"]
    for view in batteries.values():
        def _add(entities, update_before_add=False, _subentry_id=view.subentry_id):
            async_add_entities(entities, update_before_add, config_subentry_id=_subentry_id)
        await driver_platform.async_setup_entry(hass, view, _add)
