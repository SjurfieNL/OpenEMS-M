"""Een accu (subentry) zo aanbieden dat de Marstek-driver hem als entry ziet.

De driver verwacht per accu een eigen ConfigEntry met .data, .options,
.entry_id en .title. In OpenEMS-M is een accu een subentry onder de
hoofd-entry. Deze weergave geeft de accu-gegevens; al het andere (state,
async_on_unload, ...) komt van de echte hoofd-entry.
"""

from __future__ import annotations

from .const import (
    CONF_BATTERY_NUMBER, CONF_DEVICE_VERSION, CONF_HOST, CONF_MODEL, CONF_NAME,
    CONF_NUMBER, CONF_PORT, CONF_UNIT_ID, DOMAIN, MODEL_TO_VERSION,
)


class BatteryEntry:
    """Weergave van een accu-subentry als driver-entry."""

    def __init__(self, entry, subentry) -> None:
        self._entry = entry
        self.subentry_id = subentry.subentry_id
        self.entry_id = subentry.subentry_id
        self.title = subentry.title
        self.data = dict(subentry.data)
        self.options = {}
        self.domain = DOMAIN

    def __getattr__(self, name):
        # Alleen aangeroepen voor attributen die hierboven niet bestaan.
        return getattr(self._entry, name)


def battery_subentry_data(battery: dict) -> dict:
    """Zet wizard-invoer om naar de sleutels die de driver gebruikt."""
    return {
        CONF_HOST: str(battery[CONF_HOST]).strip(),
        CONF_PORT: int(battery[CONF_PORT]),
        CONF_UNIT_ID: int(battery[CONF_UNIT_ID]),
        CONF_DEVICE_VERSION: MODEL_TO_VERSION[battery[CONF_MODEL]],
        CONF_MODEL: battery[CONF_MODEL],
        CONF_BATTERY_NUMBER: int(battery[CONF_NUMBER]),
        CONF_NAME: str(battery.get(CONF_NAME) or f"Accu {int(battery[CONF_NUMBER])}").strip(),
    }


def battery_unique_id(data: dict) -> str:
    return f"{str(data[CONF_HOST]).strip().lower()}:{int(data[CONF_PORT])}:{int(data[CONF_UNIT_ID])}"


def existing_batteries(entry) -> list[dict]:
    """Accu's die al onder de hoofd-entry hangen, in wizard-vorm."""
    result = []
    for sub in getattr(entry, "subentries", {}).values():
        if sub.subentry_type != "battery":
            continue
        result.append({
            CONF_HOST: sub.data.get(CONF_HOST),
            CONF_PORT: sub.data.get(CONF_PORT),
            CONF_UNIT_ID: sub.data.get(CONF_UNIT_ID),
            CONF_NUMBER: sub.data.get(CONF_BATTERY_NUMBER),
        })
    return result
