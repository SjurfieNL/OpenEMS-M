"""Entiteitsklassen voor de OpenEMS-instellingen (zie helpers.py)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .helpers import old_helper_state


class _HelperBase:
    """Gemeenschappelijk: vaste entity_id, naam, icoon, apparaat."""

    _attr_has_entity_name = False
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    platform_domain = ""

    def __init__(self, entry, object_id: str, conf: dict, old_entity_id: str, wizard_value):
        self.entity_id = f"{self.platform_domain}.{object_id}"
        self._attr_unique_id = f"{entry.entry_id}_helper_{object_id}"
        self._attr_name = conf.get("name") or object_id
        self._attr_icon = conf.get("icon")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="OpenEMS-M",
            manufacturer="OpenEMS-M",
            model="Energiemanagement",
        )
        self._conf = conf
        self._old_entity_id = old_entity_id
        self._wizard_value = wizard_value

    def _start_value(self, restored):
        """Volgorde: laatst bekend, wizard, oude helper, None."""
        if restored not in (None, "unknown", "unavailable"):
            return restored
        if self._wizard_value is not None:
            return self._wizard_value
        return old_helper_state(self.hass, self._old_entity_id)


class OpenEMSSwitch(_HelperBase, SwitchEntity, RestoreEntity):
    platform_domain = "switch"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        value = self._start_value(last.state if last else None)
        if isinstance(value, bool):
            self._attr_is_on = value
        else:
            self._attr_is_on = str(value).lower() == "on"

    async def async_turn_on(self, **kwargs) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()


class OpenEMSNumber(_HelperBase, RestoreNumber):
    platform_domain = "number"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        conf = self._conf
        self._attr_native_min_value = float(conf.get("min", 0))
        self._attr_native_max_value = float(conf.get("max", 100))
        self._attr_native_step = float(conf.get("step", 1))
        self._attr_native_unit_of_measurement = conf.get("unit_of_measurement")
        self._attr_mode = (
            NumberMode.SLIDER if conf.get("mode") == "slider" else NumberMode.BOX
        )
        self._attr_native_value = None

    def _clamp(self, value) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return min(max(number, self._attr_native_min_value), self._attr_native_max_value)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        restored = None if last is None else last.native_value
        value = self._clamp(self._start_value(restored))
        self._attr_native_value = (
            value if value is not None else self._attr_native_min_value
        )

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = self._clamp(value)
        self.async_write_ha_state()


class OpenEMSSelect(_HelperBase, SelectEntity, RestoreEntity):
    platform_domain = "select"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self._attr_options = [str(o) for o in self._conf.get("options", [])]
        self._attr_current_option = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        value = self._start_value(last.state if last else None)
        if value not in self._attr_options and last is not None:
            value = self._start_value(None)
        if value not in self._attr_options:
            value = self._attr_options[0] if self._attr_options else None
        self._attr_current_option = value

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            raise ValueError(f"Ongeldige optie {option!r} voor {self.entity_id}")
        self._attr_current_option = option
        self.async_write_ha_state()


class OpenEMSText(_HelperBase, TextEntity, RestoreEntity):
    platform_domain = "text"

    def __init__(self, *args) -> None:
        super().__init__(*args)
        conf = self._conf
        self._attr_native_min = int(conf.get("min", 0))
        self._attr_native_max = min(int(conf.get("max", 100)), 255)
        if conf.get("pattern"):
            self._attr_pattern = conf["pattern"]
        self._attr_mode = TextMode.PASSWORD if conf.get("mode") == "password" else TextMode.TEXT
        self._attr_native_value = ""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        value = self._start_value(last.state if last else None)
        self._attr_native_value = "" if value is None else str(value)[: self._attr_native_max]

    async def async_set_value(self, value: str) -> None:
        self._attr_native_value = str(value)[: self._attr_native_max]
        self.async_write_ha_state()


class OpenEMSButton(_HelperBase, ButtonEntity):
    """Knop: OpenEMS reageert op de state-wijziging (tijdstip van indrukken)."""

    platform_domain = "button"

    async def async_press(self) -> None:
        return None
