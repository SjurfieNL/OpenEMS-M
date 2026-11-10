"""Contracttests voor de OpenEMS-uitbreiding van marstek_modbus."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types


DRIVER_ROOT = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "custom_components"
    / "marstek_modbus"
)


class _UnusedAsyncModbusTcpClient:
    pass


if "pymodbus" not in sys.modules:
    pymodbus = types.ModuleType("pymodbus")
    pymodbus_client = types.ModuleType("pymodbus.client")
    pymodbus_client.__path__ = []
    pymodbus_client_tcp = types.ModuleType("pymodbus.client.tcp")
    pymodbus_exceptions = types.ModuleType("pymodbus.exceptions")
    pymodbus_client.AsyncModbusTcpClient = _UnusedAsyncModbusTcpClient
    pymodbus_client_tcp.AsyncModbusTcpClient = _UnusedAsyncModbusTcpClient
    pymodbus_exceptions.ModbusIOException = OSError
    sys.modules["pymodbus"] = pymodbus
    sys.modules["pymodbus.client"] = pymodbus_client
    sys.modules["pymodbus.client.tcp"] = pymodbus_client_tcp
    sys.modules["pymodbus.exceptions"] = pymodbus_exceptions

package = types.ModuleType("marstek_modbus")
package.__path__ = [str(DRIVER_ROOT)]
helpers = types.ModuleType("marstek_modbus.helpers")
helpers.__path__ = [str(DRIVER_ROOT / "helpers")]
sys.modules.setdefault("marstek_modbus", package)
sys.modules.setdefault("marstek_modbus.helpers", helpers)

spec = importlib.util.spec_from_file_location(
    "marstek_modbus.helpers.modbus_client",
    DRIVER_ROOT / "helpers" / "modbus_client.py",
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
MarstekModbusClient = module.MarstekModbusClient


class _Response:
    def __init__(self, registers=None, error=False):
        self.registers = registers
        self._error = error

    def isError(self):
        return self._error


class _WireClient:
    def __init__(self, *, fail_write=None):
        self.connected = True
        self.calls = []
        self.fail_write = fail_write
        self.values = {
            42021: 900,
            42020: 0,
            42010: 2,
            30001: 0xFC7C,  # -900 as signed int16
        }

    async def write_register(self, address, value, **kwargs):
        self.calls.append(("write", address, value))
        if address == self.fail_write:
            return _Response(error=True)
        self.values[address] = value
        return _Response()

    async def read_holding_registers(self, address, count, **kwargs):
        self.calls.append(("read", address, count))
        return _Response([self.values[address]])


def _client(wire):
    client = MarstekModbusClient.__new__(MarstekModbusClient)
    client.client = wire
    client.unit_id = 1
    client.message_wait_sec = 0
    client._request_lock = asyncio.Lock()
    return client


def test_atomic_driver_writes_complete_intent_before_any_readback():
    wire = _WireClient()
    client = _client(wire)

    result = asyncio.run(
        client.async_write_read_atomic(
            [(42021, 900), (42020, 0), (42010, 2)],
            [
                ("set_discharge_power", 42021, "uint16"),
                ("set_charge_power", 42020, "uint16"),
                ("force_mode", 42010, "uint16"),
                ("battery_power", 30001, "int16"),
            ],
            settle_delay=0,
        )
    )

    assert wire.calls == [
        ("write", 42021, 900),
        ("write", 42020, 0),
        ("write", 42010, 2),
        ("read", 42021, 1),
        ("read", 42020, 1),
        ("read", 42010, 1),
        ("read", 30001, 1),
    ]
    assert result == {
        "set_discharge_power": 900,
        "set_charge_power": 0,
        "force_mode": 2,
        "battery_power": -900,
    }


def test_atomic_driver_stops_immediately_after_failed_write():
    wire = _WireClient(fail_write=42020)
    client = _client(wire)

    result = asyncio.run(
        client.async_write_read_atomic(
            [(42021, 900), (42020, 0), (42010, 2)],
            [("force_mode", 42010, "uint16")],
            settle_delay=0,
        )
    )

    assert result is None
    assert wire.calls == [
        ("write", 42021, 900),
        ("write", 42020, 0),
    ]


def test_entity_writes_rollback_and_schedule_real_readback():
    switch = (DRIVER_ROOT / "switch.py").read_text(encoding="utf-8")
    select = (DRIVER_ROOT / "select.py").read_text(encoding="utf-8")
    number = (DRIVER_ROOT / "number.py").read_text(encoding="utf-8")
    coordinator = (
        DRIVER_ROOT / "coordinator.py"
    ).read_text(encoding="utf-8")

    for source in (switch, select, number):
        assert "old_value" in source
        assert "if not success:" in source
        assert "async_confirm_control_value" in source
    assert "set_rs485_user_disabled(rs485_disabled)" in switch
    assert "timeout=8.0" in coordinator
    assert "Control read-back mismatch" in coordinator


def test_ipv4_registers_decode_to_dotted_address():
    client = _client(_WireClient())

    # 0xC0A8 0xB223 -> 192.168.178.35
    assert client._decode_registers(30400, [0xC0A8, 0xB223], "ipv4") == (
        "192.168.178.35"
    )
    assert client._default_count_for_data_type("ipv4") == 2
    # Unset address or a short response gives no value instead of an error.
    assert client._decode_registers(30400, [0, 0], "ipv4") is None
    assert client._decode_registers(30400, [0xC0A8], "ipv4") is None


def test_register_maps_only_use_supported_data_types():
    client = _client(_WireClient())
    data_types = set()
    for path in (DRIVER_ROOT / "registers").glob("*.yaml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("data_type:"):
                data_types.add(stripped.split(":", 1)[1].strip().strip("'\""))

    for data_type in sorted(data_types):
        regs = [0x0101] * 5
        try:
            client._decode_registers(30000, regs, data_type, bit_index=0)
        except ValueError as exc:
            raise AssertionError(f"Unsupported data_type in register map: {data_type}") from exc
