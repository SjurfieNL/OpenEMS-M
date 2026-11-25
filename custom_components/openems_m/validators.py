"""Pure controles voor de wizard (zonder Home Assistant, dus los te testen).

Elke functie geeft een foutcode terug (str) of None als de invoer klopt.
De foutcodes staan als vertaling in strings.json onder config.error.
"""

from __future__ import annotations

import ipaddress

from .const import MAX_BATTERIES, MODELS


def validate_host(host) -> str | None:
    text = str(host or "").strip()
    if not text:
        return "invalid_host"
    try:
        ipaddress.ip_address(text)
        return None
    except ValueError:
        pass
    # Hostnaam toestaan (letters, cijfers, punt, streepje)
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789.-")
    if len(text) <= 253 and set(text.lower()) <= allowed and not text.startswith("-"):
        return None
    return "invalid_host"


def validate_battery(battery: dict, existing: list[dict]) -> str | None:
    """Controleer één accu tegen de al ingevoerde accu's."""
    error = validate_host(battery.get("host"))
    if error:
        return error
    try:
        port = int(battery.get("port"))
        unit = int(battery.get("unit_id"))
        number = int(battery.get("number"))
    except (TypeError, ValueError):
        return "invalid_number"
    if not 1 <= port <= 65535:
        return "invalid_port"
    if not 0 <= unit <= 247:
        return "invalid_unit_id"
    if battery.get("model") not in MODELS:
        return "invalid_model"
    if not 1 <= number <= MAX_BATTERIES:
        return "invalid_battery_number"
    if len(existing) >= MAX_BATTERIES:
        return "too_many_batteries"
    for other in existing:
        if int(other.get("number")) == number:
            return "duplicate_battery_number"
        if (str(other.get("host")).strip().lower() == str(battery.get("host")).strip().lower()
                and int(other.get("port")) == port and int(other.get("unit_id")) == unit):
            return "duplicate_battery_host"
    return None


def validate_grid(max_import, max_export, margin) -> str | None:
    try:
        imp, exp, mar = float(max_import), float(max_export), float(margin)
    except (TypeError, ValueError):
        return "invalid_number"
    if imp <= 0 or exp < 0 or mar < 0:
        return "invalid_grid_limit"
    if mar >= imp:
        return "margin_too_large"
    return None


def validate_sensors(p1, ct, preferred) -> str | None:
    if not p1 and not ct:
        return "no_grid_sensor"
    if preferred == "P1" and not p1:
        return "preferred_missing"
    if preferred == "CT" and not ct:
        return "preferred_missing"
    return None


def validate_price(source, entity) -> str | None:
    if not entity:
        return "price_entity_required"
    return None
