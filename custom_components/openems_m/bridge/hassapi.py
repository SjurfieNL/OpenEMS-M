"""AppDaemon-compatibele tussenlaag op Home Assistant.

De OpenEMS-logica is geschreven voor AppDaemon (`hass.Hass`). Deze module levert
dezelfde 13 functies die OpenEMS gebruikt, met dezelfde parameters en
callback-vorm, maar draait binnen Home Assistant:

    log, get_state, set_state, call_service, turn_on, turn_off,
    run_in, run_every, run_at, cancel_timer, listen_state, listen_event

Gedrag dat bewust gelijk is aan AppDaemon:
- Alle callbacks (timers, state- en event-listeners, initialize) lopen achter
  elkaar in EEN werk-thread. Er draait nooit iets tegelijk.
- Een fout in een callback wordt gelogd en stopt de rest niet.
- listen_state zonder attribute vuurt alleen als de state-waarde verandert.
- get_state(..., attribute="all") geeft state, attributes, last_changed,
  last_updated en last_reported (ISO-tekst).
- set_state voegt attributen samen met de bestaande, tenzij replace=True.
- call_service wacht tot Home Assistant de service heeft uitgevoerd.

Deze module importeert niets uit Home Assistant: hij krijgt het `hass`-object
mee. Daardoor is hij volledig te testen met een nep-hass.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import queue
import threading
import time as _time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

SERVICE_TIMEOUT_S = 30.0
STATE_WRITE_TIMEOUT_S = 10.0
QUEUE_WARN_SIZE = 50
# Services die in de AppDaemon-versie bij een andere integratie hoorden.
SERVICE_DOMAIN_ALIASES = {"openems_m_panel": "openems_m"}

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class _Worker:
    """EEN thread die callbacks uit een wachtrij op volgorde uitvoert."""

    def __init__(self, name: str, logger: logging.Logger) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._logger = logger
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.ident: int | None = None

    def start(self) -> None:
        self._thread.start()

    def submit(self, func: Callable, *args) -> None:
        if self._stopped.is_set():
            return
        self._queue.put((func, args))
        size = self._queue.qsize()
        if size == QUEUE_WARN_SIZE:
            self._logger.warning(
                "OpenEMS-wachtrij loopt achter (%s callbacks wachtend)", size
            )

    def stop(self, timeout: float = 5.0) -> None:
        self._stopped.set()
        self._queue.put(None)
        if self._thread.is_alive() and threading.get_ident() != self.ident:
            self._thread.join(timeout)

    def idle(self) -> bool:
        return self._queue.unfinished_tasks == 0

    def _run(self) -> None:
        self.ident = threading.get_ident()
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                func, args = item
                if self._stopped.is_set():
                    continue
                try:
                    func(*args)
                except Exception:  # noqa: BLE001 - net als AppDaemon: loggen, doorgaan
                    self._logger.error(
                        "Fout in OpenEMS-callback %s:\n%s",
                        getattr(func, "__qualname__", func),
                        traceback.format_exc(),
                    )
            finally:
                self._queue.task_done()


class Hass:
    """Basisklasse met de AppDaemon-functies die OpenEMS gebruikt.

    Gebruik:
        app = MarstekEMS()
        app.bind(hass, logger)
        app.start()        # roept initialize() aan in de werk-thread
        ...
        app.stop()
    """

    # ------------------------------------------------------------ levenscyclus
    def bind(self, hass, logger: logging.Logger | None = None, name: str = "openems_m",
             entity_map: dict[str, str] | None = None,
             service_map: dict[tuple[str, str], tuple[str, str]] | None = None) -> None:
        """Koppel aan Home Assistant.

        entity_map: oude naam -> nieuwe naam (bijv. input_number.x -> number.x).
        service_map: (domein, service) van de oude naam -> nieuwe (domein, service).
        """
        self._ha = hass
        self._entity_map = dict(entity_map or {})
        self._service_map = dict(service_map or {})
        self._loop: asyncio.AbstractEventLoop = hass.loop
        self._logger = logger or logging.getLogger(f"custom_components.{name}.ems")
        self._worker = _Worker(f"{name}-ems", self._logger)
        self._handles = itertools.count(1)
        self._timers: dict[int, dict] = {}
        self._timers_lock = threading.Lock()
        self._state_listeners: dict[str, list[tuple[int, Callable, dict]]] = {}
        self._event_listeners: dict[int, Callable] = {}
        self._listeners_lock = threading.Lock()
        self._unsub_state = None
        self._running = False

    def start(self) -> None:
        """Start de werk-thread en voer initialize() daarin uit."""
        self._running = True
        self._worker.start()
        self._call_in_loop(self._async_subscribe_states)
        init = getattr(self, "initialize", None)
        if init is not None:
            self._worker.submit(init)

    def stop(self) -> None:
        """Stop alle timers, listeners en de werk-thread."""
        self._running = False
        with self._timers_lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            self._cancel_loop_handle(timer)
        with self._listeners_lock:
            self._state_listeners.clear()
            event_unsubs = list(self._event_listeners.values())
            self._event_listeners.clear()
        for unsub in event_unsubs:
            self._call_in_loop(unsub)
        if self._unsub_state is not None:
            self._call_in_loop(self._unsub_state)
            self._unsub_state = None
        terminate = getattr(self, "terminate", None)
        if terminate is not None:
            try:
                terminate()
            except Exception:  # noqa: BLE001
                self._logger.error("Fout in terminate():\n%s", traceback.format_exc())
        self._worker.stop()

    # ------------------------------------------------------------------- log
    def log(self, msg, *args, level: str = "INFO", **kwargs) -> None:
        text = str(msg) % args if args else str(msg)
        self._logger.log(_LEVELS.get(str(level).upper(), logging.INFO), text)

    def error(self, msg, *args, level: str = "ERROR", **kwargs) -> None:
        self.log(msg, *args, level=level)

    # ----------------------------------------------------------------- state
    def get_state(self, entity_id: str | None = None, attribute: str | None = None,
                  default: Any = None, **kwargs) -> Any:
        if entity_id is None:
            return {s.entity_id: self._state_dict(s) for s in self._ha.states.async_all()}
        state = self._ha.states.get(self._real_id(entity_id))
        if state is None:
            return default
        if attribute is None:
            return state.state
        if attribute == "all":
            return self._state_dict(state)
        attrs = state.attributes or {}
        if attribute in attrs:
            return attrs[attribute]
        full = self._state_dict(state)
        if attribute in full:
            return full[attribute]
        return default

    @staticmethod
    def _state_dict(state) -> dict:
        return {
            "entity_id": state.entity_id,
            "state": state.state,
            "attributes": dict(state.attributes or {}),
            "last_changed": _iso(getattr(state, "last_changed", None)),
            "last_updated": _iso(getattr(state, "last_updated", None)),
            "last_reported": _iso(
                getattr(state, "last_reported", None)
                or getattr(state, "last_updated", None)
            ),
        }

    def set_state(self, entity_id: str, state: Any = None, attributes: dict | None = None,
                  replace: bool = False, **kwargs) -> dict | None:
        entity_id = self._real_id(entity_id)
        current = self._ha.states.get(entity_id)
        new_state = state
        if new_state is None:
            new_state = current.state if current is not None else "unknown"
        merged: dict = {}
        if not replace and current is not None:
            merged.update(current.attributes or {})
        merged.update(attributes or {})

        def _write():
            self._ha.states.async_set(entity_id, str(new_state), merged)

        self._run_in_loop_wait(_write, STATE_WRITE_TIMEOUT_S, f"set_state {entity_id}")
        return {"entity_id": entity_id, "state": str(new_state), "attributes": merged}

    # --------------------------------------------------------------- services
    def call_service(self, service: str, **data) -> Any:
        domain, _, name = str(service).replace(".", "/", 1).partition("/")
        if not domain or not name:
            self.log(f"Ongeldige service: {service}", level="WARNING")
            return None
        data.pop("namespace", None)
        domain = SERVICE_DOMAIN_ALIASES.get(domain, domain)
        domain, name, data = self._map_service(domain, name, data)
        future = asyncio.run_coroutine_threadsafe(
            self._ha.services.async_call(domain, name, data, blocking=True),
            self._loop,
        )
        try:
            return future.result(SERVICE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - AppDaemon logt en gaat door
            future.cancel()
            self.log(f"Service {domain}/{name} mislukt: {exc!r}", level="WARNING")
            return None

    def turn_on(self, entity_id: str, **data) -> Any:
        domain = entity_id.split(".", 1)[0]
        return self.call_service(f"{domain}/turn_on", entity_id=entity_id, **data)

    def turn_off(self, entity_id: str, **data) -> Any:
        domain = entity_id.split(".", 1)[0]
        return self.call_service(f"{domain}/turn_off", entity_id=entity_id, **data)

    # ----------------------------------------------------------------- timers
    def run_in(self, callback: Callable, delay: float, **kwargs) -> int:
        return self._schedule(callback, max(0.0, float(delay)), None, kwargs)

    def run_at(self, callback: Callable, start, **kwargs) -> int:
        return self._schedule(callback, self._delay_until(start), None, kwargs)

    def run_every(self, callback: Callable, start, interval: float, **kwargs) -> int:
        interval = float(interval)
        if interval <= 0:
            raise ValueError("run_every: interval moet groter dan 0 zijn")
        return self._schedule(callback, self._delay_until(start), interval, kwargs)

    def cancel_timer(self, handle) -> bool:
        with self._timers_lock:
            timer = self._timers.pop(handle, None)
        if timer is None:
            return False
        timer["cancelled"] = True
        self._cancel_loop_handle(timer)
        return True

    def timer_running(self, handle) -> bool:
        with self._timers_lock:
            return handle in self._timers

    def _delay_until(self, start) -> float:
        if start is None or start == "now":
            return 0.0
        if isinstance(start, str):
            text = start.strip().replace(" ", "")
            if text.startswith("now+"):
                return max(0.0, float(text[4:]))
            raise ValueError(f"Onbekende starttijd: {start!r}")
        if isinstance(start, datetime):
            now = datetime.now(start.tzinfo or timezone.utc) if start.tzinfo else datetime.now()
            return max(0.0, (start - now).total_seconds())
        return max(0.0, float(start))

    def _schedule(self, callback, delay, interval, kwargs) -> int:
        handle = next(self._handles)
        timer = {
            "handle": handle,
            "callback": callback,
            "interval": interval,
            "kwargs": dict(kwargs),
            "due": _time.monotonic() + delay,
            "loop_handle": None,
            "pending": False,
            "cancelled": False,
        }
        with self._timers_lock:
            self._timers[handle] = timer
        self._call_in_loop(lambda: self._arm(timer))
        return handle

    def _arm(self, timer) -> None:
        """Draait in de eventloop: zet de volgende uitvoering klaar."""
        if timer["cancelled"] or not self._running:
            return
        delay = max(0.0, timer["due"] - _time.monotonic())
        timer["loop_handle"] = self._loop.call_later(delay, self._fire, timer)

    def _fire(self, timer) -> None:
        """Draait in de eventloop: zet de callback in de werk-thread."""
        if timer["cancelled"] or not self._running:
            return
        if timer["interval"] is None:
            with self._timers_lock:
                self._timers.pop(timer["handle"], None)
            self._worker.submit(self._run_timer, timer)
            return
        # run_every: vast ritme; een nog wachtende uitvoering wordt niet verdubbeld
        if not timer["pending"]:
            timer["pending"] = True
            self._worker.submit(self._run_timer, timer)
        now = _time.monotonic()
        timer["due"] += timer["interval"]
        if timer["due"] < now:
            missed = int((now - timer["due"]) // timer["interval"]) + 1
            timer["due"] += missed * timer["interval"]
        self._arm(timer)

    def _run_timer(self, timer) -> None:
        timer["pending"] = False
        if timer["cancelled"] or not self._running:
            return
        kwargs = dict(timer["kwargs"])
        kwargs["__handle"] = timer["handle"]
        timer["callback"](kwargs)

    def _cancel_loop_handle(self, timer) -> None:
        timer["cancelled"] = True
        loop_handle = timer.get("loop_handle")
        if loop_handle is not None:
            self._call_in_loop(loop_handle.cancel)

    # -------------------------------------------------------------- listeners
    def listen_state(self, callback: Callable, entity_id: str, attribute: str | None = None,
                     new: Any = None, old: Any = None, **kwargs) -> int:
        handle = next(self._handles)
        options = {"attribute": attribute, "new": new, "old": old, "kwargs": dict(kwargs),
                   "name": entity_id}
        with self._listeners_lock:
            self._state_listeners.setdefault(self._real_id(entity_id), []).append(
                (handle, callback, options))
        return handle

    def cancel_listen_state(self, handle) -> bool:
        with self._listeners_lock:
            for entity, items in self._state_listeners.items():
                kept = [item for item in items if item[0] != handle]
                if len(kept) != len(items):
                    self._state_listeners[entity] = kept
                    return True
        return False

    def listen_event(self, callback: Callable, event: str, **kwargs) -> int:
        handle = next(self._handles)
        extra = dict(kwargs)

        def _handler(ha_event):
            data = dict(getattr(ha_event, "data", {}) or {})
            self._worker.submit(callback, ha_event.event_type, data, dict(extra))

        def _subscribe():
            unsub = self._ha.bus.async_listen(event, _handler)
            with self._listeners_lock:
                self._event_listeners[handle] = unsub

        self._call_in_loop(_subscribe)
        return handle

    def _async_subscribe_states(self) -> None:
        """Draait in de eventloop: EEN listener op state_changed voor alles."""
        self._unsub_state = self._ha.bus.async_listen("state_changed", self._on_state_changed)

    def _on_state_changed(self, ha_event) -> None:
        data = ha_event.data
        entity_id = data.get("entity_id")
        with self._listeners_lock:
            listeners = list(self._state_listeners.get(entity_id, ()))
        if not listeners:
            return
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        for _handle, callback, options in listeners:
            attribute = options["attribute"]
            if attribute in (None, "state"):
                old_value = old_state.state if old_state is not None else None
                new_value = new_state.state if new_state is not None else None
                name = "state"
            elif attribute == "all":
                old_value = self._state_dict(old_state) if old_state is not None else None
                new_value = self._state_dict(new_state) if new_state is not None else None
                name = "all"
            else:
                old_value = (old_state.attributes or {}).get(attribute) if old_state is not None else None
                new_value = (new_state.attributes or {}).get(attribute) if new_state is not None else None
                name = attribute
            if attribute != "all" and old_value == new_value:
                continue
            if options["new"] is not None and new_value != options["new"]:
                continue
            if options["old"] is not None and old_value != options["old"]:
                continue
            self._worker.submit(
                callback, options.get("name", entity_id), name, old_value, new_value,
                dict(options["kwargs"])
            )

    # ------------------------------------------------------- naamvertaling
    def _real_id(self, entity_id):
        if not isinstance(entity_id, str):
            return entity_id
        return self._entity_map.get(entity_id, entity_id)

    def _map_service(self, domain: str, name: str, data: dict):
        """Oude helper-service -> service van de nieuwe entiteit."""
        entity = data.get("entity_id")
        if entity is None:
            return domain, name, data
        ids = entity if isinstance(entity, (list, tuple)) else [entity]
        mapped = [self._real_id(e) for e in ids]
        if mapped == list(ids):
            return domain, name, data
        target = self._service_map.get((domain, name))
        if target is None:
            self.log(f"Geen vertaling voor service {domain}/{name}", level="WARNING")
            return domain, name, data
        data = dict(data)
        data["entity_id"] = mapped if isinstance(entity, (list, tuple)) else mapped[0]
        return target[0], target[1], data

    # ------------------------------------------------------------- hulpjes
    def _call_in_loop(self, func: Callable) -> None:
        if self._in_loop_thread():
            func()
        else:
            self._loop.call_soon_threadsafe(func)

    def _in_loop_thread(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def _run_in_loop_wait(self, func: Callable, timeout: float, what: str) -> None:
        if self._in_loop_thread():
            func()
            return
        done = threading.Event()
        error: list[BaseException] = []

        def _wrapped():
            try:
                func()
            except BaseException as exc:  # noqa: BLE001
                error.append(exc)
            finally:
                done.set()

        self._loop.call_soon_threadsafe(_wrapped)
        if not done.wait(timeout):
            self.log(f"{what}: geen antwoord van Home Assistant binnen {timeout:.0f} s",
                     level="WARNING")
            return
        if error:
            self.log(f"{what} mislukt: {error[0]!r}", level="WARNING")
