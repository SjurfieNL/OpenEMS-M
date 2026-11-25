# GEGENEREERD door tools/sync_ems.py uit de werkmap-tests. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Herverbinden van marstek_modbus na opeenvolgende uitleestimeouts."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types


DRIVER_ROOT = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "openems_m" / "driver"
)


def _stub(name, **attrs):
    module = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _DataUpdateCoordinator:
    def __init__(self, *args, **kwargs):
        pass

    def __class_getitem__(cls, item):
        return cls


def _load_coordinator():
    _stub("homeassistant")
    _stub("homeassistant.config_entries", ConfigEntry=object)
    _stub("homeassistant.core", HomeAssistant=object)
    _stub("homeassistant.helpers")
    _stub("homeassistant.helpers.entity", Entity=object)
    _stub(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_DataUpdateCoordinator,
    )
    _stub("homeassistant.util")
    _stub("homeassistant.util.dt", utcnow=lambda: None)
    if "pymodbus" not in sys.modules:
        class _Unused:
            pass
        _stub("pymodbus")
        client = _stub("pymodbus.client", AsyncModbusTcpClient=_Unused)
        client.__path__ = []
        _stub("pymodbus.client.tcp", AsyncModbusTcpClient=_Unused)
        _stub("pymodbus.exceptions", ModbusIOException=OSError)
    package = types.ModuleType("marstek_reconnect_pkg")
    package.__path__ = [str(DRIVER_ROOT)]
    sys.modules["marstek_reconnect_pkg"] = package
    helpers = types.ModuleType("marstek_reconnect_pkg.helpers")
    helpers.__path__ = [str(DRIVER_ROOT / "helpers")]
    sys.modules["marstek_reconnect_pkg.helpers"] = helpers
    spec = importlib.util.spec_from_file_location(
        "marstek_reconnect_pkg.coordinator", DRIVER_ROOT / "coordinator.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


coordinator_module = _load_coordinator()
MarstekCoordinator = coordinator_module.MarstekCoordinator


class _HangingClient:
    host = "192.168.178.39"
    port = 502

    def __init__(self):
        self.reconnects = 0
        self.hang = True

    async def async_read_register(self, **kwargs):
        if self.hang:
            await asyncio.sleep(3600)
        return 42

    async def async_reconnect(self):
        self.reconnects += 1
        self.hang = False
        return True


def _coordinator(client):
    coordinator = MarstekCoordinator.__new__(MarstekCoordinator)
    coordinator.client = client
    coordinator._entity_types = {}
    coordinator._scales = {}
    coordinator._timeouts_in_cycle = 0
    coordinator._consecutive_read_timeouts = 0
    coordinator._last_timeout_reconnect = None
    return coordinator


def _run_reads(coordinator, count, monkeypatch):
    real_wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        # 10 s uitleestimeout in de test terugbrengen tot 10 ms.
        return await real_wait_for(awaitable, min(timeout, 0.01))

    monkeypatch.setattr(coordinator_module.asyncio, "wait_for", fast_wait_for)
    sensor = {"key": "battery_soc", "register": 34002, "data_type": "uint16"}

    async def run():
        return [
            await coordinator.async_read_value(sensor, "battery_soc")
            for _ in range(count)
        ]

    return asyncio.run(run())


def test_reconnects_after_three_consecutive_read_timeouts(monkeypatch):
    client = _HangingClient()
    coordinator = _coordinator(client)

    results = _run_reads(coordinator, 4, monkeypatch)

    # Na drie timeouts één keer opnieuw verbonden; de vierde lezing slaagt.
    assert client.reconnects == 1
    assert results == [None, None, None, 42]
    assert coordinator._consecutive_read_timeouts == 0


def test_two_timeouts_do_not_reconnect_and_success_resets(monkeypatch):
    client = _HangingClient()
    coordinator = _coordinator(client)

    _run_reads(coordinator, 2, monkeypatch)
    assert client.reconnects == 0
    assert coordinator._consecutive_read_timeouts == 2

    client.hang = False
    _run_reads(coordinator, 1, monkeypatch)
    assert coordinator._consecutive_read_timeouts == 0


def test_reconnect_has_cooldown(monkeypatch):
    client = _HangingClient()
    coordinator = _coordinator(client)
    coordinator._last_timeout_reconnect = coordinator_module.monotonic()

    _run_reads(coordinator, 3, monkeypatch)

    # Binnen de afkoeltijd geen tweede reconnect.
    assert client.reconnects == 0
    assert coordinator_module.READ_TIMEOUT_RECONNECT_AFTER == 3
    assert coordinator_module.READ_TIMEOUT_RECONNECT_COOLDOWN_S >= 30
