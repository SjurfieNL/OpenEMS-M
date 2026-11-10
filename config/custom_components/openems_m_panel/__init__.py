"""Registreert het zelfstandige OpenEMS-M-zijbalkpaneel."""

import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .marstek_atomic_bridge import AtomicBridgeError, MarstekAtomicBridge

DOMAIN = "openems_m_panel"
PANEL_PATH = "openems-m"
STATIC_PATH = "/openems_m_panel_static"
PANEL_TITLE = "OpenEMS-M"
PANEL_ICON = "mdi:home-battery"
SERVICE_SET_BATTERY_POWER = "set_battery_power"
SERVICE_SET_BATTERY_POWERS = "set_battery_powers"
POWER_COMMAND_SCHEMA = {
    vol.Required("battery"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    vol.Optional("charge_power", default=0): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=2500)
    ),
    vol.Optional("discharge_power", default=0): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=2500)
    ),
    vol.Optional("source", default="onbekend"): vol.Coerce(str),
    vol.Optional("reason", default=""): vol.Coerce(str),
    vol.Optional("entity_id"): vol.Coerce(str),
}
SET_POWER_SCHEMA = vol.Schema(POWER_COMMAND_SCHEMA)
SET_POWERS_SCHEMA = vol.Schema(
    {
        vol.Required("commands"): [POWER_COMMAND_SCHEMA],
        vol.Optional("request_id"): vol.Coerce(str),
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Laad het paneel vanuit configuration.yaml."""
    domain_config = config.get(DOMAIN, {}) or {}
    frontend_dir = Path(__file__).parent / "frontend"
    data = hass.data.setdefault(DOMAIN, {})

    if not data.get("atomic_service_registered"):
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
                hass.bus.async_fire(
                    "openems_marstek_control_result",
                    result,
                )
            except AtomicBridgeError as error:
                raise HomeAssistantError(str(error)) from error

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_BATTERY_POWER,
            _set_battery_power,
            schema=SET_POWER_SCHEMA,
        )

        async def _set_battery_powers(call: ServiceCall) -> None:
            # De AppDaemon-callback wacht niet op Modbus. Tijdens uitvoering
            # vervangt een nieuw verzoek de nog niet gestarte tussenbatch.
            bridge.enqueue_powers(
                call.data["commands"],
                request_id=call.data.get("request_id"),
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_BATTERY_POWERS,
            _set_battery_powers,
            schema=SET_POWERS_SCHEMA,
        )
        data["atomic_bridge"] = bridge
        data["atomic_service_registered"] = True

    if not data.get("static_registered"):
        try:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(STATIC_PATH, str(frontend_dir), cache_headers=True)]
            )
        except Exception:
            _LOGGER.error(
                "OpenEMS-M-paneel: registratie van het statische pad "
                "(%s) is mislukt; het dashboard blijft daardoor "
                "onbereikbaar. Vermoedelijk is de Home Assistant-API "
                "hiervoor gewijzigd.",
                STATIC_PATH,
            )
            raise
        data["static_registered"] = True

    js_file = frontend_dir / "openems-panel.js"
    cache_bust = str(int(js_file.stat().st_mtime))
    try:
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
    except Exception:  # Paneel bestaat bij de eerste start nog niet.
        pass

    panel_config = {
        "title": PANEL_TITLE,
        "home_power_entity": domain_config.get(
            "home_power_entity",
            "sensor.meterkast_shellycthome_vermogen",
        ),
        "grid_power_entity": domain_config.get(
            "grid_power_entity",
            "sensor.meterkast_shellyctgrid_vermogen",
        ),
    }
    try:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name="openems-m-panel",
            module_url=f"{STATIC_PATH}/openems-panel.js?v={cache_bust}",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            require_admin=False,
            config=panel_config,
        )
    except Exception:
        _LOGGER.error(
            "OpenEMS-M-paneel: registratie van het zijbalkpaneel "
            "('%s') is mislukt; het paneel verdwijnt hierdoor stilletjes "
            "uit de zijbalk. Vermoedelijk is de Home Assistant-API "
            "hiervoor gewijzigd.",
            PANEL_TITLE,
        )
        raise
    data["panel_registered"] = True
    return True
