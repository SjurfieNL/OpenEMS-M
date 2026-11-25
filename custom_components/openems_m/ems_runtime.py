"""Starten en stoppen van de OpenEMS-logica binnen de integratie.

1. Datamap /config/openems_m/ (buiten custom_components, overleeft updates).
2. openems_site.yaml daarin schrijven uit de wizard en de accu-subentries.
   De OpenEMS-code leest dat bestand bij het laden (zoals in AppDaemon).
3. De app (MarstekEMS) starten op de tussenlaag, met de naamvertaling van de
   oude helpers naar de entiteiten van deze integratie.
"""

from __future__ import annotations

import importlib
import logging
import re
import sys
from pathlib import Path

from .const import CONF_BATTERY_NUMBER, CONF_NAME, SUBENTRY_BATTERY

_LOGGER = logging.getLogger(__name__)
EMS_LOGGER_NAME = f"{__package__}.ems"
SITE_FILE = "openems_site.yaml"


def slugify(text: str) -> str:
    """Zelfde vorm als een HA-entiteitsnaam ('Berging Accu1' -> berging_accu1)."""
    try:
        from homeassistant.util import slugify as ha_slugify
        return ha_slugify(str(text))
    except Exception:  # noqa: BLE001 - buiten HA (tests)
        value = re.sub(r"[^a-z0-9]+", "_", str(text).lower())
        return value.strip("_")


def build_site_config(entry, language: str) -> dict:
    data = dict(entry.data)
    batteries = []
    for sub in entry.subentries.values():
        if sub.subentry_type != SUBENTRY_BATTERY:
            continue
        number = int(sub.data.get(CONF_BATTERY_NUMBER) or 0)
        name = str(sub.data.get(CONF_NAME) or sub.title)
        batteries.append((number, {"naam": name, "prefix": slugify(sub.title or name)}))
    batteries.sort(key=lambda item: item[0])
    site: dict = {
        "entiteit_taal": "nl" if str(language or "nl").lower().startswith("nl") else "en",
        "accus": [item[1] for item in batteries],
        "net": {
            "max_import_w": int(float(data.get("max_import_w", 8050))),
            "max_export_w": int(float(data.get("max_export_w", 5000))),
            "marge_w": int(float(data.get("safety_margin_w", 100))),
        },
        "sensoren": {},
    }
    if data.get("p1_entity"):
        site["sensoren"]["p1"] = data["p1_entity"]
    if data.get("ct_entity"):
        site["sensoren"]["ct_net"] = data["ct_entity"]
    if data.get("price_entity"):
        site["prijs"] = {"all_in_sensor": data["price_entity"]}
    if not site["accus"]:
        site.pop("accus")
    return site


def write_site_config(root: Path, site: dict) -> bool:
    """Schrijf openems_site.yaml; geeft True als de inhoud veranderde."""
    import yaml

    root.mkdir(parents=True, exist_ok=True)
    path = root / SITE_FILE
    text = (
        "# Automatisch geschreven door OpenEMS-M uit de installatiewizard.\n"
        "# Wijzig de instellingen via de integratie, niet in dit bestand.\n"
        + yaml.safe_dump(site, allow_unicode=True, sort_keys=False)
    )
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def ems_loaded() -> bool:
    return f"{__package__}.ems.ems_config" in sys.modules


def prepare_and_import(root: Path, site: dict):
    """In de executor: datamap + site-bestand, daarna de EMS-code laden."""
    from . import ems_paths

    ems_paths.set_root(root)
    changed = write_site_config(root, site)
    already = ems_loaded()
    module = importlib.import_module(f"{__package__}.ems.marstek_ems")
    return module, changed and already


async def async_start_ems(hass, entry):
    """Start OpenEMS; geeft de app terug (of None bij een fout)."""
    from .helpers import SERVICE_MAP, helper_entity_map

    root = Path(hass.config.path("openems_m"))
    site = build_site_config(entry, getattr(hass.config, "language", "nl"))
    try:
        module, needs_restart = await hass.async_add_executor_job(
            prepare_and_import, root, site)
    except Exception:  # noqa: BLE001 - de integratie blijft bruikbaar
        _LOGGER.exception("OpenEMS-logica kon niet worden geladen")
        return None
    if needs_restart:
        _LOGGER.warning(
            "Accu's of wizard-instellingen zijn gewijzigd; herstart Home Assistant "
            "zodat OpenEMS de nieuwe instellingen gebruikt")
        _notify(hass, "OpenEMS-M: herstart nodig",
                "De accu's of installatie-instellingen zijn gewijzigd. Herstart Home "
                "Assistant zodat OpenEMS de nieuwe instellingen gebruikt.")
    app = module.MarstekEMS()
    app.bind(hass, logging.getLogger(EMS_LOGGER_NAME), entity_map=helper_entity_map(),
             service_map=SERVICE_MAP)
    app.start()
    _LOGGER.info("OpenEMS-logica gestart (%s accu('s))", len(site.get("accus", [])))
    return app


async def async_stop_ems(hass, app) -> None:
    if app is None:
        return
    await hass.async_add_executor_job(app.stop)


def _notify(hass, title: str, message: str) -> None:
    try:
        from homeassistant.components import persistent_notification
        persistent_notification.async_create(
            hass, message, title=title, notification_id="openems_m_restart")
    except Exception:  # noqa: BLE001
        pass
