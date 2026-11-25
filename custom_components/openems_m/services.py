"""Services openems_m.set_battery_power(s): de atomaire accu-aansturing.

Overgenomen uit openems_m_panel; de brug werkt nu met de ingebouwde driver.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .driver.atomic_bridge import AtomicBridgeError, MarstekAtomicBridge

SERVICE_SET_BATTERY_POWER = "set_battery_power"
SERVICE_SET_BATTERY_POWERS = "set_battery_powers"
RESULT_EVENT = "openems_marstek_control_result"
POWER_COMMAND_SCHEMA = {
    vol.Required("battery"): vol.All(vol.Coerce(int), vol.Range(min=1, max=4)),
    vol.Optional("charge_power", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=2500)),
    vol.Optional("discharge_power", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=2500)),
    vol.Optional("source", default="onbekend"): vol.Coerce(str),
    vol.Optional("reason", default=""): vol.Coerce(str),
    vol.Optional("entity_id"): vol.Coerce(str),
}
SET_POWER_SCHEMA = vol.Schema(POWER_COMMAND_SCHEMA)
SET_POWERS_SCHEMA = vol.Schema({
    vol.Required("commands"): [POWER_COMMAND_SCHEMA],
    vol.Optional("request_id"): vol.Coerce(str),
})


def async_register_services(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("atomic_service_registered"):
        return
    bridge = MarstekAtomicBridge(hass)

    async def _set_battery_power(call: ServiceCall) -> None:
        try:
            result = await bridge.set_power(
                call.data["battery"],
                call.data["charge_power"],
                call.data["discharge_power"],
                source=call.data.get("source", "service"),
                reason=call.data.get("reason", "losse service-opdracht"),
                entity_id=call.data.get("entity_id"),
            )
            data["last_atomic_result"] = result
            hass.bus.async_fire(RESULT_EVENT, result)
        except AtomicBridgeError as error:
            raise HomeAssistantError(str(error)) from error

    async def _set_battery_powers(call: ServiceCall) -> None:
        bridge.enqueue_powers(call.data["commands"], request_id=call.data.get("request_id"))

    hass.services.async_register(DOMAIN, SERVICE_SET_BATTERY_POWER, _set_battery_power,
                                 schema=SET_POWER_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_BATTERY_POWERS, _set_battery_powers,
                                 schema=SET_POWERS_SCHEMA)
    data["atomic_bridge"] = bridge
    data["atomic_service_registered"] = True


def async_unregister_services(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN, {})
    if not data.get("atomic_service_registered"):
        return
    for name in (SERVICE_SET_BATTERY_POWER, SERVICE_SET_BATTERY_POWERS):
        hass.services.async_remove(DOMAIN, name)
    data.pop("atomic_bridge", None)
    data["atomic_service_registered"] = False
