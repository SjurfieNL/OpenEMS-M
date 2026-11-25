"""Het OpenEMS-M-zijbalkpaneel (voorheen de losse integratie openems_m_panel)."""

from __future__ import annotations

import logging
from pathlib import Path

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PANEL_PATH = "openems-m"
STATIC_PATH = "/openems_m_static"
PANEL_TITLE = "OpenEMS-M"
PANEL_ICON = "mdi:home-battery"
DEFAULT_HOME_POWER_ENTITY = "sensor.meterkast_shellycthome_vermogen"


def panel_config(entry_data: dict) -> dict:
    grid = (entry_data.get("ct_entity") if entry_data.get("preferred_sensor") == "CT"
            else entry_data.get("p1_entity")) or entry_data.get("ct_entity") \
        or entry_data.get("p1_entity") or ""
    return {
        "title": PANEL_TITLE,
        "home_power_entity": DEFAULT_HOME_POWER_ENTITY,
        "grid_power_entity": grid,
    }


async def async_register_panel(hass, entry) -> None:
    from homeassistant.components import frontend, panel_custom
    from homeassistant.components.http import StaticPathConfig

    data = hass.data.setdefault(DOMAIN, {})
    frontend_dir = Path(__file__).parent / "frontend"
    if not data.get("static_registered"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_PATH, str(frontend_dir), cache_headers=True)])
        data["static_registered"] = True
    js_file = frontend_dir / "openems-panel.js"
    cache_bust = str(int((await hass.async_add_executor_job(js_file.stat)).st_mtime))
    try:
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
    except Exception:  # noqa: BLE001 - bestaat bij de eerste start nog niet
        pass
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_PATH,
        webcomponent_name="openems-m-panel",
        module_url=f"{STATIC_PATH}/openems-panel.js?v={cache_bust}",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=False,
        config=panel_config(dict(entry.data)),
    )
    data["panel_registered"] = True


def async_remove_panel(hass) -> None:
    data = hass.data.get(DOMAIN, {})
    if not data.get("panel_registered"):
        return
    try:
        from homeassistant.components import frontend
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Paneel verwijderen mislukt", exc_info=True)
    data["panel_registered"] = False
