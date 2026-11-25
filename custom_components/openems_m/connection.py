"""Verbindingstest voor de wizard: lees SOC en firmware uit de accu."""

from __future__ import annotations

import asyncio
import logging

from .const import CONNECT_TIMEOUT_S, MODEL_TO_VERSION

_LOGGER = logging.getLogger(__name__)


class CannotConnect(Exception):
    """De accu reageert niet of geeft geen geldige waarden."""


def _definition(registers: dict, key: str) -> dict | None:
    for item in registers.get("SENSOR_DEFINITIONS", []):
        if item.get("key") == key:
            return item
    return None


async def _read(client, definition):
    if definition is None:
        return None
    value = await client.async_read_register(
        definition["register"],
        definition.get("data_type", "uint16"),
        definition.get("count"),
        sensor_key=definition.get("key"),
        max_retries=2,
    )
    if value is None:
        return None
    scale = definition.get("scale")
    if scale not in (None, 1) and isinstance(value, (int, float)):
        value = round(value * scale, 1)
    return value


async def async_test_connection(hass, host: str, port: int, unit_id: int, model: str) -> dict:
    """Maak verbinding, lees SOC en firmware, en sluit weer af.

    Geeft {"soc": float, "firmware": str|None}; gooit CannotConnect bij een fout.
    """
    from .driver.coordinator import get_registers
    from .driver.helpers.modbus_client import MarstekModbusClient

    version = MODEL_TO_VERSION[model]
    registers = await hass.async_add_executor_job(get_registers, version)
    client = MarstekModbusClient(host, int(port), timeout=3, unit_id=int(unit_id))

    async def _run():
        if not await client.async_connect():
            raise CannotConnect("geen verbinding")
        soc = await _read(client, _definition(registers, "battery_soc"))
        firmware = await _read(client, _definition(registers, "ems_version"))
        if soc is None or not isinstance(soc, (int, float)) or not 0 <= soc <= 100:
            raise CannotConnect(f"ongeldige SOC: {soc!r}")
        return {"soc": soc, "firmware": None if firmware is None else str(firmware)}

    try:
        return await asyncio.wait_for(_run(), CONNECT_TIMEOUT_S)
    except CannotConnect:
        raise
    except Exception as err:  # noqa: BLE001 - elke fout = geen bruikbare verbinding
        _LOGGER.debug("Verbindingstest %s:%s mislukt: %r", host, port, err)
        raise CannotConnect(str(err)) from err
    finally:
        try:
            await client.async_close()
        except Exception:  # noqa: BLE001
            pass
