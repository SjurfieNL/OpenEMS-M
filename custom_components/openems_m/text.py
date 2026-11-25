"""text-entiteiten: de tekst-instellingen van OpenEMS (voorheen input_text)."""

from .platforms import async_setup_platform_for_batteries


async def async_setup_entry(hass, entry, async_add_entities):
    await async_setup_platform_for_batteries(hass, entry, async_add_entities, "text")
