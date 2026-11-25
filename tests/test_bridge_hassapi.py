"""Tests voor de tussenlaag (bridge/hassapi.py) met een echte asyncio-eventloop."""

import ast
import asyncio
import logging
import pathlib
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from openems_m.bridge.hassapi import Hass

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "openems_m"


# ----------------------------------------------------------------- nep-hass
class FakeState(SimpleNamespace):
    pass


class FakeBus:
    def __init__(self):
        self.listeners = {}

    def async_listen(self, event, handler):
        self.listeners.setdefault(event, []).append(handler)

        def unsub():
            self.listeners[event].remove(handler)
        return unsub

    def async_fire(self, event, data):
        for handler in list(self.listeners.get(event, [])):
            handler(SimpleNamespace(event_type=event, data=data))


class FakeStates:
    def __init__(self, bus):
        self._states = {}
        self._bus = bus
        self.write_threads = []

    def get(self, entity_id):
        return self._states.get(entity_id)

    def async_all(self):
        return list(self._states.values())

    def async_set(self, entity_id, state, attributes=None):
        self.write_threads.append(threading.get_ident())
        old = self._states.get(entity_id)
        now = datetime.now(timezone.utc)
        changed = old is None or old.state != state
        new = FakeState(
            entity_id=entity_id, state=state, attributes=dict(attributes or {}),
            last_changed=now if changed else old.last_changed,
            last_updated=now, last_reported=now,
        )
        self._states[entity_id] = new
        self._bus.async_fire("state_changed",
                             {"entity_id": entity_id, "old_state": old, "new_state": new})


class FakeServices:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.delay = 0.0

    async def async_call(self, domain, service, data, blocking=False):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("service kapot")
        self.calls.append((domain, service, dict(data), blocking, threading.get_ident()))


class FakeHass:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.bus = FakeBus()
        self.states = FakeStates(self.bus)
        self.services = FakeServices()

    def in_loop(self, func, *args):
        done = threading.Event()
        result = []

        def run():
            result.append(func(*args))
            done.set()
        self.loop.call_soon_threadsafe(run)
        assert done.wait(2)
        return result[0]

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(2)


class App(Hass):
    def __init__(self):
        self.events = []
        self.init_thread = None

    def initialize(self):
        self.init_thread = threading.get_ident()


def wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def env():
    hass = FakeHass()
    app = App()
    app.bind(hass, logging.getLogger("test.openems"))
    app.start()
    assert wait_for(lambda: app.init_thread is not None)
    yield hass, app
    app.stop()
    hass.close()


def in_worker(app, func):
    """Voer func uit in de werk-thread en wacht op het resultaat."""
    result, done = [], threading.Event()

    def run():
        try:
            result.append(func())
        finally:
            done.set()
    app._worker.submit(run)
    assert done.wait(5)
    return result[0] if result else None


# ------------------------------------------------------------------- tests
def test_initialize_runs_in_worker_thread(env):
    hass, app = env
    assert app.init_thread != threading.get_ident()
    assert app.init_thread != hass.loop_thread.ident


def test_get_state_variants(env):
    hass, app = env
    hass.in_loop(hass.states.async_set, "sensor.p1", "1200", {"unit": "W"})
    assert in_worker(app, lambda: app.get_state("sensor.p1")) == "1200"
    assert in_worker(app, lambda: app.get_state("sensor.bestaat_niet")) is None
    assert in_worker(app, lambda: app.get_state("sensor.bestaat_niet", default="x")) == "x"
    full = in_worker(app, lambda: app.get_state("sensor.p1", attribute="all"))
    assert full["state"] == "1200" and full["attributes"] == {"unit": "W"}
    assert full["last_reported"] and full["last_updated"] and full["last_changed"]
    assert in_worker(app, lambda: app.get_state("sensor.p1", attribute="unit")) == "W"
    assert in_worker(app, lambda: app.get_state("sensor.p1", attribute="last_updated"))
    assert in_worker(app, lambda: app.get_state("sensor.p1", attribute="nope")) is None


def test_set_state_merges_unless_replace_and_writes_in_loop(env):
    hass, app = env
    in_worker(app, lambda: app.set_state("sensor.oe", state="a", attributes={"x": 1, "y": 2}))
    in_worker(app, lambda: app.set_state("sensor.oe", state="b", attributes={"y": 3}))
    s = hass.states.get("sensor.oe")
    assert s.state == "b" and s.attributes == {"x": 1, "y": 3}
    in_worker(app, lambda: app.set_state("sensor.oe", state="c", attributes={"z": 9},
                                         replace=True))
    assert hass.states.get("sensor.oe").attributes == {"z": 9}
    assert set(hass.states.write_threads) == {hass.loop_thread.ident}


def test_call_service_blocking_and_failure(env):
    hass, app = env
    in_worker(app, lambda: app.call_service("number/set_value",
                                            entity_id="number.a", value=500))
    in_worker(app, lambda: app.call_service("select.select_option",
                                            entity_id="select.m", option="Stop"))
    in_worker(app, lambda: app.call_service("openems_m_panel/set_battery_powers",
                                            commands=[]))
    in_worker(app, lambda: app.turn_on("switch.x"))
    in_worker(app, lambda: app.turn_off("input_boolean.y"))
    calls = [(d, s, data, b) for d, s, data, b, _t in hass.services.calls]
    assert calls == [
        ("number", "set_value", {"entity_id": "number.a", "value": 500}, True),
        ("select", "select_option", {"entity_id": "select.m", "option": "Stop"}, True),
        ("openems_m", "set_battery_powers", {"commands": []}, True),
        ("switch", "turn_on", {"entity_id": "switch.x"}, True),
        ("input_boolean", "turn_off", {"entity_id": "input_boolean.y"}, True),
    ]
    assert {t for *_r, t in hass.services.calls} == {hass.loop_thread.ident}
    hass.services.fail = True
    assert in_worker(app, lambda: app.call_service("number/set_value", value=1)) is None


def test_call_service_waits_until_done(env):
    hass, app = env
    hass.services.delay = 0.2
    start = time.monotonic()
    in_worker(app, lambda: app.call_service("number/set_value", value=1))
    assert time.monotonic() - start >= 0.18
    assert len(hass.services.calls) == 1


def test_run_in_passes_kwargs_and_keeps_order(env):
    hass, app = env
    seen = []
    app.run_in(lambda kw: seen.append(("b", kw["generation"])), 0.10, generation=2)
    app.run_in(lambda kw: seen.append(("a", kw["generation"])), 0.05, generation=1)
    app.run_in(lambda kw: seen.append(("c", kw.get("generation"))), 0.15)
    assert wait_for(lambda: len(seen) == 3)
    assert seen == [("a", 1), ("b", 2), ("c", None)]


def test_run_at_and_run_every_forms(env):
    hass, app = env
    seen = []
    app.run_at(lambda kw: seen.append("at"),
               datetime.now(timezone.utc) + timedelta(seconds=0.1))
    ticks = []
    handle = app.run_every(lambda kw: ticks.append(time.monotonic()), "now", 0.1)
    app.run_every(lambda kw: seen.append("plus"), "now+0.05", 10)
    app.run_every(lambda kw: seen.append("dt"),
                  datetime.now(timezone.utc) + timedelta(seconds=0.05), 10)
    assert wait_for(lambda: "at" in seen and len(ticks) >= 4)
    assert "plus" in seen and "dt" in seen
    assert app.cancel_timer(handle) is True
    count = len(ticks)
    time.sleep(0.3)
    assert len(ticks) <= count + 1
    assert app.cancel_timer(handle) is False


def test_run_every_does_not_pile_up_when_callback_is_slow(env):
    hass, app = env
    runs = []

    def slow(kw):
        runs.append(time.monotonic())
        time.sleep(0.25)
    handle = app.run_every(slow, "now", 0.05)
    time.sleep(0.8)
    app.cancel_timer(handle)
    # zonder bescherming zouden dit er ~16 zijn; nu maximaal ~1 per 0,25 s
    assert 2 <= len(runs) <= 5


def test_callbacks_never_run_concurrently(env):
    hass, app = env
    active, peak, threads = [0], [0], set()
    lock = threading.Lock()

    def work(*_args):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            threads.add(threading.get_ident())
        time.sleep(0.02)
        with lock:
            active[0] -= 1
    for _ in range(10):
        app.run_in(work, 0.01)
    app.listen_state(work, "sensor.x")
    for value in range(5):
        hass.in_loop(hass.states.async_set, "sensor.x", str(value), {})
    assert wait_for(lambda: app._worker.idle(), 5)
    time.sleep(0.1)
    assert peak[0] == 1
    assert threads == {app._worker.ident}


def test_error_in_callback_does_not_stop_others(env, caplog):
    hass, app = env
    seen = []

    def boom(kw):
        raise ValueError("kapot")
    app.run_in(boom, 0.01)
    app.run_in(lambda kw: seen.append("na fout"), 0.05)
    with caplog.at_level(logging.ERROR):
        assert wait_for(lambda: seen == ["na fout"])
    assert any("kapot" in r.getMessage() for r in caplog.records)


def test_listen_state_semantics(env):
    hass, app = env
    calls = []

    def cb(entity, attribute, old, new, kwargs):
        calls.append((entity, attribute, old, new))
    hass.in_loop(hass.states.async_set, "input_select.mode", "Stop", {"opt": 1})
    app.listen_state(cb, "input_select.mode")
    hass.in_loop(hass.states.async_set, "input_select.mode", "Laden", {"opt": 1})
    hass.in_loop(hass.states.async_set, "input_select.mode", "Laden", {"opt": 2})  # alleen attribuut
    hass.in_loop(hass.states.async_set, "sensor.ander", "1", {})
    assert wait_for(lambda: app._worker.idle())
    time.sleep(0.05)
    assert calls == [("input_select.mode", "state", "Stop", "Laden")]

    filtered, attr = [], []
    app.listen_state(lambda e, a, o, n, k: filtered.append(n), "input_select.mode", new="Stop")
    app.listen_state(lambda e, a, o, n, k: attr.append((o, n)), "input_select.mode",
                     attribute="opt")
    hass.in_loop(hass.states.async_set, "input_select.mode", "Ontladen", {"opt": 3})
    hass.in_loop(hass.states.async_set, "input_select.mode", "Stop", {"opt": 3})
    assert wait_for(lambda: filtered == ["Stop"] and attr == [(2, 3)])


def test_listen_event_signature(env):
    hass, app = env
    got = []
    app.listen_event(lambda name, data, kwargs: got.append((name, data, kwargs)),
                     "openems_marstek_control_result")
    time.sleep(0.05)
    hass.in_loop(hass.bus.async_fire, "openems_marstek_control_result", {"ok": True})
    assert wait_for(lambda: got == [("openems_marstek_control_result", {"ok": True}, {})])


def test_stop_cancels_everything():
    hass = FakeHass()
    app = App()
    app.bind(hass)
    app.start()
    ticks, states = [], []
    app.run_every(lambda kw: ticks.append(1), "now", 0.05)
    app.listen_state(lambda *a: states.append(a), "sensor.x")
    assert wait_for(lambda: len(ticks) >= 2)
    app.stop()
    count = len(ticks)
    hass.in_loop(hass.states.async_set, "sensor.x", "5", {})
    time.sleep(0.3)
    assert len(ticks) == count and states == []
    assert hass.bus.listeners.get("state_changed") == []
    hass.close()


def test_log_levels(env, caplog):
    _hass, app = env
    with caplog.at_level(logging.DEBUG, logger="test.openems"):
        app.log("info-bericht")
        app.log("waarschuwing", level="WARNING")
        app.log("fout", level="ERROR")
    levels = {r.getMessage(): r.levelno for r in caplog.records}
    assert levels == {"info-bericht": logging.INFO, "waarschuwing": logging.WARNING,
                      "fout": logging.ERROR}


# ------------------------------------------------- koppeling met de EMS-code
def test_generated_ems_uses_bridge_without_name_clashes():
    from openems_m.ems import marstek_ems

    assert issubclass(marstek_ems.MarstekEMS, Hass)
    bridge_src = (ROOT / "bridge" / "hassapi.py").read_text(encoding="utf-8")
    tree = ast.parse(bridge_src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Hass")
    bridge_names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    bridge_names |= set(re.findall(r"self\.(_[a-z_]+)\s*[:=]", bridge_src))
    api = {"log", "error", "get_state", "set_state", "call_service", "turn_on", "turn_off",
           "run_in", "run_at", "run_every", "cancel_timer", "listen_state", "listen_event",
           "cancel_listen_state", "timer_running"}
    internal = bridge_names - api
    for path in (ROOT / "ems").glob("*.py"):
        src = path.read_text(encoding="utf-8")
        used = set(re.findall(r"self\.([A-Za-z_]\w*)\s*=", src))
        used |= {n.name for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.FunctionDef)}
        if path.name == "marstek_ems.py":
            assert not (internal & used), (path.name, internal & used)


def test_generated_ems_has_only_intended_changes():
    ems = ROOT / "ems"
    for path in ems.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert src.startswith("# GEGENEREERD"), path.name
        assert "appdaemon" not in src.split("\n", 3)[-1] or path.name == "__init__.py" \
            or "import appdaemon" not in src, path.name
        assert "Path(__file__).resolve().parent.parent" not in src, path.name
        assert not re.search(r"(?m)^\s*from (load_balancer|models|ems_config|persistence) import",
                             src), path.name


def test_real_marstek_ems_starts_on_bridge(tmp_path):
    """Rooktest: de echte OpenEMS-app start op de tussenlaag zonder fouten."""
    from openems_m import ems_paths
    from openems_m.ems.marstek_ems import MarstekEMS

    ems_paths.set_root(tmp_path)
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)
    logger = logging.getLogger("test.openems.smoke")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(Collect())
    hass = FakeHass()
    app = MarstekEMS()
    app.bind(hass, logger)
    try:
        app.start()
        assert wait_for(lambda: any("OpenEMS-M v" in r.getMessage() for r in records), 10)
        time.sleep(1.0)
    finally:
        app.stop()
        hass.close()
    errors = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
    assert errors == []
    # zonder accu's mag er niets naar accu-entiteiten gestuurd worden
    assert not [c for c in hass.services.calls if c[0] in ("number", "select", "button")]
