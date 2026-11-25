# GEGENEREERD door tools/sync_ems.py uit de werkmap-tests. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Een onbereikbare accu mag HA-opstart en uitleesrondes niet ophouden."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import test_marstek_modbus_driver as driver_tests

DRIVER_ROOT = driver_tests.DRIVER_ROOT
module = driver_tests.module
MarstekModbusClient = driver_tests.MarstekModbusClient


class _DeadWire:
    connected = False

    async def read_holding_registers(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("mag niet lezen zonder verbinding")

    async def write_register(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("mag niet schrijven zonder verbinding")


def _offline_client():
    client = MarstekModbusClient.__new__(MarstekModbusClient)
    client.client = _DeadWire()
    client.host = "192.168.178.36"
    client.port = 502
    client.unit_id = 1
    client.message_wait_sec = 0
    client._request_lock = asyncio.Lock()
    client._last_connect_failure = None
    client.connect_calls = 0

    async def _fake_connect():
        client.connect_calls += 1
        client._last_connect_failure = module.monotonic()
        return False

    client.async_connect = _fake_connect
    return client


def test_offline_read_cycle_tries_connect_only_once_within_cooldown():
    client = _offline_client()

    async def _cycle():
        results = []
        for register in (41200, 42000, 42010, 42011, 42020, 42021, 43000):
            results.append(
                await client.async_read_holding_registers(register, 1)
            )
        return results

    results = asyncio.run(_cycle())
    assert results == [None] * 7
    # Vroeger: een verbindingspoging (~3 s) per register.
    assert client.connect_calls == 1


def test_offline_write_fails_fast_during_cooldown():
    client = _offline_client()
    client._last_connect_failure = module.monotonic()

    ok = asyncio.run(client.async_write_register(42020, 500))
    assert ok is False
    assert client.connect_calls == 0

    atomic = asyncio.run(
        client.async_write_read_atomic([(42020, 500)], [], settle_delay=0)
    )
    assert atomic is None
    assert client.connect_calls == 0


def test_connect_is_retried_after_cooldown():
    client = _offline_client()
    client._last_connect_failure = (
        module.monotonic() - module.CONNECT_RETRY_COOLDOWN_S - 1
    )

    assert asyncio.run(client.async_read_holding_registers(42000, 1)) is None
    assert client.connect_calls == 1


def test_explicit_reconnect_ignores_cooldown():
    client = _offline_client()
    client._last_connect_failure = module.monotonic()

    async def _close():
        return None

    client.async_close = _close
    assert asyncio.run(client.async_reconnect()) is False
    assert client.connect_calls == 1


import pytest  # noqa: E402


@pytest.mark.skip(reason="OpenEMS-M zet accu's per subentry op; een offline accu blokkeert de rest niet (zie test_driver_setup.py)")
def test_setup_raises_not_ready_when_battery_unreachable():
    source = (DRIVER_ROOT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    setup = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "async_setup_entry"
    )
    body = ast.unparse(setup)
    assert "connected = await coordinator.async_init()" in body
    assert "raise ConfigEntryNotReady" in body
    # NotReady moet door de brede except heen naar HA.
    assert "except ConfigEntryNotReady:\n        raise" in body
    # Platforms pas na een geslaagde verbinding.
    assert body.index("raise ConfigEntryNotReady") < body.index(
        "async_forward_entry_setups"
    )
    assert "from homeassistant.exceptions import ConfigEntryNotReady" in source
