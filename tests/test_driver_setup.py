"""Opstart van de ingebouwde driver: accu's als subentries, offline accu, platforms."""

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from openems_m import connection
from openems_m.battery_entry import BatteryEntry, battery_subentry_data


def run(coro):
    return asyncio.run(coro)


def sub(sid, number, host, name=None):
    data = battery_subentry_data({"name": name or f"Accu {number}", "host": host, "port": 502,
                                  "unit_id": 1, "model": "e_v3", "number": number})
    return SimpleNamespace(subentry_id=sid, subentry_type="battery", title=data["name"],
                           data=data)


class FakeEntry:
    def __init__(self, subentries):
        self.entry_id = "entry1"
        self.data = {"max_import_w": 8050}
        self.subentries = {s.subentry_id: s for s in subentries}
        self.state = "setup_in_progress"
        self.unloads = []

    def async_on_unload(self, func):
        self.unloads.append(func)

    def add_update_listener(self, func):
        return lambda: None


# ------------------------------------------------------------- weergave
def test_battery_entry_view_exposes_battery_and_forwards_rest():
    entry = FakeEntry([sub("abc", 2, "10.0.0.2", "Berging Accu2")])
    view = BatteryEntry(entry, entry.subentries["abc"])
    assert view.entry_id == "abc" and view.subentry_id == "abc"
    assert view.title == "Berging Accu2" and view.domain == "openems_m"
    assert view.data["host"] == "10.0.0.2" and view.data["device_version"] == "E v3"
    assert view.data["openems_battery_number"] == 2 and view.options == {}
    assert view.state == "setup_in_progress"          # komt van de hoofd-entry
    view.async_on_unload("x")
    assert entry.unloads == ["x"]


def test_generated_coordinator_keeps_battery_view_as_config_entry():
    src = (pytest.importorskip("pathlib").Path(__file__).resolve().parents[1]
           / "custom_components/openems_m/driver/coordinator.py").read_text(encoding="utf-8")
    init_end = src.index("super().__init__(")
    assert "self.config_entry = entry" in src[init_end:init_end + 600]


# ------------------------------------------------------- verbindingstest
class FakeClient:
    behaviour = {}

    def __init__(self, host, port, timeout=3, unit_id=1, **kw):
        self.host, self.port, self.unit_id = host, port, unit_id
        self.closed = False
        FakeClient.last = self

    async def async_connect(self):
        return self.behaviour.get("connect", True)

    async def async_read_register(self, register, data_type="uint16", count=None, **kw):
        if self.behaviour.get("hang"):
            await asyncio.sleep(30)
        return self.behaviour.get(register)

    async def async_close(self):
        self.closed = True


REGISTERS = {"SENSOR_DEFINITIONS": [
    {"key": "battery_soc", "register": 34002, "scale": 0.1, "data_type": "uint16"},
    {"key": "ems_version", "register": 30200, "scale": 1, "data_type": "uint16"},
]}


@pytest.fixture
def fake_driver(monkeypatch):
    coord = types.ModuleType("openems_m.driver.coordinator")
    coord.get_registers = lambda version: REGISTERS
    client_mod = types.ModuleType("openems_m.driver.helpers.modbus_client")
    client_mod.MarstekModbusClient = FakeClient
    monkeypatch.setitem(sys.modules, "openems_m.driver.coordinator", coord)
    monkeypatch.setitem(sys.modules, "openems_m.driver.helpers.modbus_client", client_mod)
    FakeClient.behaviour = {}
    return FakeClient


class LoopHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def test_connection_reads_soc_and_firmware(fake_driver):
    fake_driver.behaviour = {34002: 642, 30200: 149}
    result = run(connection.async_test_connection(LoopHass(), "10.0.0.1", 502, 1, "e_v3"))
    assert result == {"soc": 64.2, "firmware": "149"}
    assert fake_driver.last.closed


@pytest.mark.parametrize("behaviour", [
    {"connect": False},
    {34002: None},
    {34002: 5000},
])
def test_connection_failures_raise_cannot_connect(fake_driver, behaviour):
    fake_driver.behaviour = behaviour
    with pytest.raises(connection.CannotConnect):
        run(connection.async_test_connection(LoopHass(), "10.0.0.1", 502, 1, "e_v3"))
    assert fake_driver.last.closed


def test_connection_times_out(fake_driver, monkeypatch):
    monkeypatch.setattr(connection, "CONNECT_TIMEOUT_S", 0.2)
    fake_driver.behaviour = {"hang": True}
    with pytest.raises(connection.CannotConnect):
        run(connection.async_test_connection(LoopHass(), "10.0.0.1", 502, 1, "e_v3"))
    assert fake_driver.last.closed


# ------------------------------------------------------- opstart per accu
class FakeCoordinator:
    instances = []
    offline_hosts = set()

    def __init__(self, hass, entry):
        self.config_entry = entry
        self.host = entry.data["host"]
        self.port = entry.data["port"]
        self.loaded = None
        self.refreshed = False
        self.closed = False
        FakeCoordinator.instances.append(self)

    async def async_load_registers(self, version):
        self.loaded = version

    async def async_init(self):
        return self.host not in self.offline_hosts

    async def async_refresh(self):
        self.refreshed = True

    async def async_close(self):
        self.closed = True


class SetupHass:
    def __init__(self):
        self.data = {}
        self.forwarded = []
        self.unloaded = []
        self.services = SimpleNamespace(registered=[], removed=[])
        self.services.async_register = lambda d, n, f, schema=None: self.services.registered.append((d, n))
        self.services.async_remove = lambda d, n: self.services.removed.append((d, n))
        self.bus = SimpleNamespace(async_fire=lambda *a: None)
        hass = self

        class _CE:
            async def async_forward_entry_setups(self, entry, platforms):
                hass.forwarded.append(list(platforms))

            async def async_unload_platforms(self, entry, platforms):
                hass.unloaded.append(list(platforms))
                return True
        self.config_entries = _CE()


@pytest.fixture
def fake_setup(monkeypatch):
    coord = types.ModuleType("openems_m.driver.coordinator")
    coord.MarstekCoordinator = FakeCoordinator
    bridge = types.ModuleType("openems_m.driver.atomic_bridge")

    class AtomicBridgeError(Exception):
        pass

    class MarstekAtomicBridge:
        def __init__(self, hass):
            self.hass = hass
    bridge.AtomicBridgeError = AtomicBridgeError
    bridge.MarstekAtomicBridge = MarstekAtomicBridge
    monkeypatch.setitem(sys.modules, "openems_m.driver.coordinator", coord)
    monkeypatch.setitem(sys.modules, "openems_m.driver.atomic_bridge", bridge)
    monkeypatch.delitem(sys.modules, "openems_m.services", raising=False)
    FakeCoordinator.instances = []
    FakeCoordinator.offline_hosts = set()
    return FakeCoordinator


def test_setup_creates_coordinator_per_battery_and_survives_offline(fake_setup, monkeypatch):
    import openems_m
    from openems_m import ems_runtime

    started, stopped = [], []

    async def fake_start(hass, entry):
        started.append(entry.entry_id)
        return "ems-app"

    async def fake_stop(hass, app):
        stopped.append(app)
    monkeypatch.setattr(ems_runtime, "async_start_ems", fake_start)
    monkeypatch.setattr(ems_runtime, "async_stop_ems", fake_stop)
    from openems_m import panel
    panel_calls = []

    async def fake_register(hass, entry):
        panel_calls.append("register")
    monkeypatch.setattr(panel, "async_register_panel", fake_register)
    monkeypatch.setattr(panel, "async_remove_panel", lambda hass: panel_calls.append("remove"))
    fake_setup.offline_hosts = {"10.0.0.2"}
    hass = SetupHass()
    entry = FakeEntry([sub("a", 1, "10.0.0.1"), sub("b", 2, "10.0.0.2")])
    assert run(openems_m.async_setup_entry(hass, entry)) is True
    assert [c.host for c in fake_setup.instances] == ["10.0.0.1", "10.0.0.2"]
    assert all(c.loaded == "E v3" and c.refreshed for c in fake_setup.instances)
    assert set(hass.data["openems_m_driver"]) == {"a", "b"}
    assert set(hass.data["openems_m"]["entry1"]["batteries"]) == {"a", "b"}
    assert hass.forwarded == [["sensor", "switch", "select", "button", "number",
                               "binary_sensor", "text"]]
    assert started == ["entry1"] and hass.data["openems_m"]["entry1"]["ems"] == "ems-app"
    assert ("openems_m", "set_battery_power") in hass.services.registered
    assert ("openems_m", "set_battery_powers") in hass.services.registered

    assert run(openems_m.async_unload_entry(hass, entry)) is True
    assert stopped == ["ems-app"]
    assert panel_calls == ["register", "remove"]
    assert all(c.closed for c in fake_setup.instances)
    assert hass.data["openems_m_driver"] == {}
    assert ("openems_m", "set_battery_power") in hass.services.removed


def test_platform_wrapper_passes_subentry_id(fake_setup, monkeypatch):
    from openems_m import platforms

    calls = []
    fake_platform = types.ModuleType("openems_m.driver.sensor")

    async def driver_setup(hass, view, add):
        add([f"entity-{view.subentry_id}"])
    fake_platform.async_setup_entry = driver_setup
    monkeypatch.setitem(sys.modules, "openems_m.driver.sensor", fake_platform)
    entry = FakeEntry([sub("a", 1, "10.0.0.1"), sub("b", 2, "10.0.0.2")])
    hass = SimpleNamespace(data={"openems_m": {"entry1": {"batteries": {
        sid: BatteryEntry(entry, s) for sid, s in entry.subentries.items()}}}},
        states=SimpleNamespace(get=lambda e: None))

    def add(entities, update_before_add=False, *, config_subentry_id=None):
        calls.append((entities, config_subentry_id))
    run(platforms.async_setup_platform_for_batteries(hass, entry, add, "sensor"))
    assert calls == [(["entity-a"], "a"), (["entity-b"], "b")]
