"""OpenEMS-M: energiemanagement voor Marstek Venus-accu's.

- De ingebouwde Marstek-driver draait per accu (subentry) en levert de
  accu-entiteiten en de atomaire aansturing (openems_m.set_battery_power[s]).
- De OpenEMS-instellingen zijn entiteiten van deze integratie (helpers.py).
- De OpenEMS-logica draait op de tussenlaag (ems_runtime.py). Zolang de
  hoofdschakelaar (switch.openems_actief) uit staat, kijkt OpenEMS alleen mee.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .battery_entry import BatteryEntry
from .const import DOMAIN, DRIVER_DATA, PLATFORMS, SUBENTRY_BATTERY

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .driver.coordinator import MarstekCoordinator
    from .services import async_register_services

    driver_data = hass.data.setdefault(DRIVER_DATA, {})
    batteries: dict[str, BatteryEntry] = {}
    coordinators = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_BATTERY:
            continue
        view = BatteryEntry(entry, subentry)
        coordinator = MarstekCoordinator(hass, view)
        try:
            await coordinator.async_load_registers(view.data.get("device_version"))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Registers voor %s niet geladen: %s", view.title, err)
        # Een onbereikbare accu blokkeert de andere accu's niet: zijn
        # entiteiten blijven onbeschikbaar en de driver probeert het bij elke
        # uitleesronde opnieuw (met afkoeltijd).
        if not await coordinator.async_init():
            _LOGGER.warning("%s (%s:%s) nu niet bereikbaar; driver probeert het opnieuw",
                            view.title, coordinator.host, coordinator.port)
        batteries[view.subentry_id] = view
        coordinators[view.subentry_id] = coordinator
        driver_data[view.subentry_id] = coordinator

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "config": dict(entry.data),
        "batteries": batteries,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for coordinator in coordinators.values():
        await coordinator.async_refresh()
    async_register_services(hass)
    from .ems_runtime import async_start_ems

    hass.data[DOMAIN][entry.entry_id]["ems"] = await async_start_ems(hass, entry)
    from .panel import async_register_panel

    try:
        await async_register_panel(hass, entry)
    except Exception:  # noqa: BLE001 - zonder paneel blijft OpenEMS werken
        _LOGGER.exception("OpenEMS-M-paneel kon niet worden geregistreerd")
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.info("OpenEMS-M geladen met %s accu('s)", len(batteries))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .ems_runtime import async_stop_ems
    from .panel import async_remove_panel
    from .services import async_unregister_services

    async_remove_panel(hass)
    stored_now = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    await async_stop_ems(hass, stored_now.pop("ems", None))
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    stored = hass.data.get(DOMAIN, {}).pop(entry.entry_id, {})
    driver_data = hass.data.get(DRIVER_DATA, {})
    for subentry_id in stored.get("batteries", {}):
        coordinator = driver_data.pop(subentry_id, None)
        if coordinator is not None:
            try:
                await coordinator.async_close()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Sluiten van accuverbinding mislukt: %s", err)
    async_unregister_services(hass)
    return True
