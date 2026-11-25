"""Installatiewizard voor OpenEMS-M (5 stappen)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry, ConfigFlow, ConfigFlowResult, ConfigSubentryFlow, SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .battery_entry import battery_subentry_data, battery_unique_id, existing_batteries
from .connection import CannotConnect, async_test_connection

from .const import (
    CONF_ADD_ANOTHER, CONF_AUTO, CONF_CT_ENTITY,
    CONF_FEED_IN, CONF_HOST, CONF_MARGIN, CONF_MAX_EXPORT, CONF_MAX_IMPORT,
    CONF_MODEL, CONF_NUMBER, CONF_P1_ENTITY, CONF_PORT,
    CONF_PREFERRED, CONF_PRICE_ENTITY, CONF_PRICE_SOURCE,
    CONF_NAME, CONF_UNIT_ID, DEFAULT_MARGIN_W, DEFAULT_PORT, DEFAULT_UNIT_ID, DOMAIN,
    MAX_BATTERIES, MODELS, PRICE_FRANK, PRICE_SOURCES, SUBENTRY_BATTERY,
)
from .validators import (
    validate_battery, validate_grid, validate_price, validate_sensors,
)

_POWER_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="power")
)


def _number(minimum, maximum, step, unit=None):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=step,
            mode=selector.NumberSelectorMode.BOX, unit_of_measurement=unit,
        )
    )


def _choice(options, key):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, translation_key=key,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _battery_schema(count: int):
    number = min(count + 1, MAX_BATTERIES)
    return vol.Schema({
        vol.Required(CONF_NAME, default=f"Accu {number}"): selector.TextSelector(),
        vol.Required(CONF_HOST): selector.TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): _number(1, 65535, 1),
        vol.Required(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): _number(0, 247, 1),
        vol.Required(CONF_MODEL, default="e_v3"): _choice(MODELS, "model"),
        vol.Required(CONF_NUMBER, default=number): _number(1, MAX_BATTERIES, 1),
    })


def _battery_from_input(user_input) -> dict:
    return {
        CONF_NAME: str(user_input.get(CONF_NAME) or "").strip(),
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_UNIT_ID: int(user_input[CONF_UNIT_ID]),
        CONF_MODEL: user_input[CONF_MODEL],
        CONF_NUMBER: int(user_input[CONF_NUMBER]),
    }


async def _check_battery(hass, battery: dict, existing: list[dict]):
    """Valideer de invoer en test de verbinding. Geeft (fout, meting)."""
    error = validate_battery(battery, existing)
    if error:
        return error, None
    if not battery[CONF_NAME]:
        return "name_required", None
    try:
        reading = await async_test_connection(
            hass, battery[CONF_HOST], battery[CONF_PORT], battery[CONF_UNIT_ID],
            battery[CONF_MODEL],
        )
    except CannotConnect:
        return "cannot_connect", None
    return None, reading


def _reading_text(battery: dict, reading: dict) -> str:
    firmware = reading.get("firmware")
    extra = f", firmware {firmware}" if firmware else ""
    return f"{battery[CONF_NAME]}: SOC {reading['soc']:g} %{extra}"


class OpenEMSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Wizard: contract -> accu's -> aansluiting -> netsensoren -> start."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._batteries: list[dict] = []
        self._last_reading = ""

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry):
        return {SUBENTRY_BATTERY: BatterySubentryFlow}

    # 1. Energiecontract ----------------------------------------------------
    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = validate_price(
                user_input.get(CONF_PRICE_SOURCE), user_input.get(CONF_PRICE_ENTITY)
            )
            if error:
                errors["base"] = error
            else:
                self._data.update(user_input)
                return await self.async_step_battery()
        schema = vol.Schema({
            vol.Required(CONF_PRICE_SOURCE, default=PRICE_FRANK):
                _choice(PRICE_SOURCES, "price_source"),
            vol.Required(CONF_PRICE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_FEED_IN, default=0.0): _number(0, 0.25, 0.0001, "EUR/kWh"),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # 2. Accu's (herhaalbaar, met verbindingstest) -----------------------
    async def async_step_battery(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            battery = _battery_from_input(user_input)
            error, reading = await _check_battery(self.hass, battery, self._batteries)
            if error:
                errors["base"] = error
            else:
                self._batteries.append(battery)
                self._last_reading = _reading_text(battery, reading)
                if user_input.get(CONF_ADD_ANOTHER) and len(self._batteries) < MAX_BATTERIES:
                    return await self.async_step_battery()
                return await self.async_step_grid()
        schema = _battery_schema(len(self._batteries)).extend({
            vol.Required(CONF_ADD_ANOTHER, default=False): selector.BooleanSelector(),
        })
        return self.async_show_form(
            step_id="battery", data_schema=schema, errors=errors,
            description_placeholders={
                "count": str(len(self._batteries)),
                "last": self._last_reading or "-",
            },
        )

    # 3. Aansluiting --------------------------------------------------------
    async def async_step_grid(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            error = validate_grid(
                user_input[CONF_MAX_IMPORT], user_input[CONF_MAX_EXPORT],
                user_input[CONF_MARGIN],
            )
            if error:
                errors["base"] = error
            else:
                self._data.update({
                    CONF_MAX_IMPORT: float(user_input[CONF_MAX_IMPORT]),
                    CONF_MAX_EXPORT: float(user_input[CONF_MAX_EXPORT]),
                    CONF_MARGIN: float(user_input[CONF_MARGIN]),
                })
                return await self.async_step_sensors()
        schema = vol.Schema({
            vol.Required(CONF_MAX_IMPORT, default=8050): _number(100, 60000, 10, "W"),
            vol.Required(CONF_MAX_EXPORT, default=5000): _number(0, 60000, 10, "W"),
            vol.Required(CONF_MARGIN, default=DEFAULT_MARGIN_W): _number(0, 5000, 10, "W"),
        })
        return self.async_show_form(step_id="grid", data_schema=schema, errors=errors)

    # 4. Netsensoren --------------------------------------------------------
    async def async_step_sensors(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            p1 = user_input.get(CONF_P1_ENTITY)
            ct = user_input.get(CONF_CT_ENTITY)
            preferred = user_input.get(CONF_PREFERRED)
            error = validate_sensors(p1, ct, preferred)
            if error:
                errors["base"] = error
            else:
                self._data.update({
                    CONF_P1_ENTITY: p1, CONF_CT_ENTITY: ct, CONF_PREFERRED: preferred,
                })
                return await self.async_step_start()
        schema = vol.Schema({
            vol.Optional(CONF_P1_ENTITY): _POWER_SENSOR,
            vol.Optional(CONF_CT_ENTITY): _POWER_SENSOR,
            vol.Required(CONF_PREFERRED, default="P1"): _choice(["P1", "CT"], "preferred"),
        })
        return self.async_show_form(step_id="sensors", data_schema=schema, errors=errors)

    # 5. Start --------------------------------------------------------------
    async def async_step_start(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_AUTO] = bool(user_input.get(CONF_AUTO, False))
            subentries = []
            for battery in self._batteries:
                data = battery_subentry_data(battery)
                subentries.append({
                    "subentry_type": SUBENTRY_BATTERY,
                    "title": data[CONF_NAME],
                    "data": data,
                    "unique_id": battery_unique_id(data),
                })
            return self.async_create_entry(
                title="OpenEMS-M", data=self._data, subentries=subentries,
            )
        schema = vol.Schema({
            vol.Required(CONF_AUTO, default=False): selector.BooleanSelector(),
        })
        return self.async_show_form(
            step_id="start", data_schema=schema,
            description_placeholders={"count": str(len(self._batteries))},
        )


class BatterySubentryFlow(ConfigSubentryFlow):
    """'Accu toevoegen' bij een bestaande OpenEMS-M installatie."""

    async def async_step_user(self, user_input=None) -> SubentryFlowResult:
        entry = self._get_entry()
        existing = existing_batteries(entry)
        errors: dict[str, str] = {}
        if user_input is not None:
            battery = _battery_from_input(user_input)
            error, reading = await _check_battery(self.hass, battery, existing)
            if error:
                errors["base"] = error
            else:
                data = battery_subentry_data(battery)
                return self.async_create_entry(
                    title=data[CONF_NAME], data=data, unique_id=battery_unique_id(data),
                )
        return self.async_show_form(
            step_id="user", data_schema=_battery_schema(len(existing)), errors=errors,
            description_placeholders={"count": str(len(existing))},
        )
