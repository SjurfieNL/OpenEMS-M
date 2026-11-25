"""switch-entiteiten van de Marstek-accu's (via de ingebouwde driver)."""

from .platforms import async_setup_platform_for_batteries


async def async_setup_entry(hass, entry, async_add_entities):
    await async_setup_platform_for_batteries(hass, entry, async_add_entities, "switch")
