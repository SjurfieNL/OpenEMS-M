"""Atomaire OpenEMS-aansturing via de actieve Marstek-coordinators.

OpenEMS 0.13 gebruikt bij voorkeur de meegeleverde ``marstek_modbus``-driver
met expliciete accunummers. De oude Energy Manager-route blijft alleen als
rollbackcompatibiliteit bestaan. OpenEMS gebruikt altijd de reeds geopende
coordinator-client en opent zelf nooit een tweede TCP-verbinding.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import logging
from pathlib import Path
import re
import time


MARSTEK_DOMAIN = "marstek_venus_energy_manager"
MARSTEK_MODBUS_DOMAIN = "marstek_modbus"
MANUAL_MODE_KEY = "manual_mode_enabled"
RS485_ENABLE = 0x55AA
BMS_DISCHARGE_CUTOFF_SOC = 15
ATOMIC_WRITE_TIMEOUT_SECONDS = 10.0
COMMUNICATION_BACKOFF_BASE_SECONDS = 15.0
COMMUNICATION_BACKOFF_MAX_SECONDS = 900.0
REGISTER_KEYS = {
    "set_discharge_power": "set_discharge_power",
    "set_charge_power": "set_charge_power",
    "force_mode": "force_mode",
    "rs485_control": "rs485_control_mode",
}

_LOGGER = logging.getLogger(__name__)


class AtomicBridgeError(RuntimeError):
    """Een atomaire accu-opdracht kon niet veilig worden uitgevoerd."""


class MarstekAtomicBridge:
    """Zoekt Marstek-coordinators en voert OpenEMS-opdrachten atomair uit."""

    def __init__(
        self, hass, sleep=None, monotonic=None,
        write_timeout=ATOMIC_WRITE_TIMEOUT_SECONDS,
    ):
        self.hass = hass
        self._sleep = sleep or asyncio.sleep
        self._monotonic = monotonic or time.monotonic
        self._write_timeout = max(0.1, float(write_timeout))
        self._communication_failures = {}
        self._communication_backoff_until = {}
        self._command_locks = {}
        self._pending_commands = None
        self._pending_request_id = None
        self._worker_task = None
        self._instrumented_clients = set()

    def targets(self):
        """Geef alle accu's in een stabiele, natuurlijke naamvolgorde."""
        modbus_data = self.hass.data.get(MARSTEK_MODBUS_DOMAIN, {})
        if modbus_data:
            targets = []
            numbers = set()
            for entry_id, coordinator in modbus_data.items():
                if coordinator is None:
                    continue
                number = self._modbus_battery_number(
                    entry_id, coordinator
                )
                try:
                    number = int(number)
                except (TypeError, ValueError):
                    raise AtomicBridgeError(
                        "Marstek Venus Modbus-entry mist het expliciete "
                        "OpenEMS-accunummer 1, 2, 3 of 4"
                    )
                if number not in (1, 2, 3, 4) or number in numbers:
                    raise AtomicBridgeError(
                        f"Ongeldig of dubbel OpenEMS-accunummer: {number}"
                    )
                numbers.add(number)
                self._instrument_modbus_client(coordinator)
                targets.append((number, entry_id, None, coordinator))
            # Accu 1 t/m 3 zijn verplicht; accu 4 is optioneel en wordt
            # meegenomen zodra de vierde Modbus-entry is gekoppeld.
            if not {1, 2, 3} <= numbers:
                missing = ", ".join(
                    str(number) for number in sorted({1, 2, 3} - numbers)
                )
                raise AtomicBridgeError(
                    "OpenEMS start pas wanneer minimaal accu 1 t/m 3 "
                    "Marstek Modbus-accu's zijn gekoppeld; "
                    f"ontbrekend: {missing or 'geen'}"
                )
            targets.sort(key=lambda item: item[0])
            return [
                (entry_id, controller, coordinator)
                for _number, entry_id, controller, coordinator in targets
            ]

        domain_data = self.hass.data.get(MARSTEK_DOMAIN, {})
        targets = []
        seen = set()
        for entry_id, entry_data in domain_data.items():
            if not isinstance(entry_data, dict):
                continue
            controller = entry_data.get("controller")
            for coordinator in entry_data.get("coordinators", []):
                identity = id(coordinator)
                if identity in seen:
                    continue
                seen.add(identity)
                self._instrument_modbus_client(coordinator)
                targets.append((entry_id, controller, coordinator))
        targets.sort(key=lambda item: self._natural_key(item[2].name))
        return targets

    def _modbus_battery_number(self, entry_id, coordinator):
        """Lees het nummer of migreer oudere entries via hun accunaam."""
        number = getattr(coordinator, "openems_battery_number", None)
        if number not in (None, ""):
            return number

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is not None:
            for values in (
                getattr(entry, "data", None),
                getattr(entry, "options", None),
            ):
                if isinstance(values, dict):
                    number = values.get("openems_battery_number")
                    if number not in (None, ""):
                        return number

        texts = [getattr(coordinator, "name", "")]
        if entry is not None:
            texts.insert(0, getattr(entry, "title", ""))
        texts.extend(self._config_entry_entity_ids(entry_id))
        for value in texts:
            match = re.search(
                r"(?:accu|battery)\s*[-_#]?\s*([123])(?!\d)",
                str(value),
                flags=re.IGNORECASE,
            )
            if match:
                number = int(match.group(1))
                coordinator.openems_battery_number = number
                return number
        return None

    def _config_entry_entity_ids(self, entry_id):
        """Lees de expliciete accu-identiteit uit HA's entiteitenregister."""
        try:
            from homeassistant.helpers import entity_registry as er

            registry = er.async_get(self.hass)
            entries = er.async_entries_for_config_entry(registry, entry_id)
        except (ImportError, AttributeError, TypeError):
            return []
        return [
            str(getattr(entity, "entity_id", ""))
            for entity in entries
            if getattr(entity, "entity_id", None)
        ]

    def _target_from_entity_id(self, entity_id, battery):
        """Koppel rechtstreeks via het reeds bekende HA-entity-id."""
        if not entity_id:
            return None
        try:
            from homeassistant.helpers import entity_registry as er

            registry = er.async_get(self.hass)
            entity = registry.async_get(str(entity_id))
        except (ImportError, AttributeError, TypeError):
            entity = None
        if entity is None:
            raise AtomicBridgeError(
                f"OpenEMS-entity {entity_id} ontbreekt in het entiteitenregister"
            )
        entry_id = getattr(entity, "config_entry_id", None)
        coordinator = self.hass.data.get(
            MARSTEK_MODBUS_DOMAIN, {}
        ).get(entry_id)
        if coordinator is None:
            raise AtomicBridgeError(
                f"OpenEMS-entity {entity_id} is niet aan een actieve "
                "Marstek Modbus-coordinator gekoppeld"
            )
        match = re.search(
            r"(?:accu|battery)\s*[-_#]?\s*([123])(?!\d)",
            str(entity_id),
            flags=re.IGNORECASE,
        )
        if match is not None and int(match.group(1)) != int(battery):
            raise AtomicBridgeError(
                f"Accu {battery} hoort niet bij OpenEMS-entity {entity_id}"
            )
        coordinator.openems_battery_number = int(battery)
        self._instrument_modbus_client(coordinator)
        return entry_id, None, coordinator

    def _instrument_modbus_client(self, coordinator):
        """Audit alle ruwe writes, ook wanneer een andere module ze uitvoert."""
        client = getattr(coordinator, "client", None)
        if client is None or id(client) in self._instrumented_clients:
            return
        original = getattr(client, "async_write_register", None)
        if not callable(original):
            return
        self._instrumented_clients.add(id(client))

        def audited_write(register, value, *args, **kwargs):
            writer = self._raw_write_caller()
            key = self._key_for_register(coordinator, register)
            old_value = (getattr(coordinator, "data", None) or {}).get(key)
            async def execute():
                try:
                    result = await original(register, value, *args, **kwargs)
                except Exception:
                    self._audit(
                        coordinator, register, key, old_value, value,
                        "ruwe_modbus_client", writer, None,
                        "raw_write_exception", writer=writer,
                    )
                    raise
                self._audit(
                    coordinator, register, key, old_value, value,
                    "ruwe_modbus_client", writer, None,
                    "raw_write_ok" if result else "raw_write_mislukt",
                    writer=writer,
                )
                return result
            return execute()

        client.async_write_register = audited_write

    @staticmethod
    def _raw_write_caller():
        for frame in inspect.stack()[2:14]:
            filename = Path(frame.filename).name
            if filename in {"modbus_client.py", "coordinator.py"}:
                continue
            if (
                filename == "marstek_atomic_bridge.py"
                and frame.function in {
                    "audited_write", "_instrument_modbus_client"
                }
            ):
                continue
            return f"{filename}:{frame.function}:{frame.lineno}"
        return "onbekende_schrijver"

    @staticmethod
    def _key_for_register(coordinator, register):
        for key in (
            "backup_function", "rs485_control", "force_mode",
            "set_charge_power", "set_discharge_power", "user_work_mode",
            "charging_cutoff_capacity", "discharging_cutoff_capacity",
            "max_charge_power", "max_discharge_power",
        ):
            try:
                if coordinator.get_register(key) == register:
                    return REGISTER_KEYS.get(key, key)
            except Exception:
                continue
        return f"register_{register}"

    async def set_power(
        self,
        battery,
        charge_power,
        discharge_power,
        *,
        source="onbekend",
        reason="",
        request_id=None,
        entity_id=None,
    ):
        """Schrijf setpoints en Force Mode onder dezelfde Marstek-lock."""
        battery = int(battery)
        charge_power = self._power(charge_power)
        discharge_power = self._power(discharge_power)
        if charge_power and discharge_power:
            raise AtomicBridgeError(
                "Laden en ontladen mogen niet tegelijk groter dan nul zijn"
            )

        target = self._target_from_entity_id(entity_id, battery)
        if target is None:
            targets = self.targets()
            if battery < 1 or battery > len(targets):
                raise AtomicBridgeError(
                    f"Accu {battery} bestaat niet; "
                    f"{len(targets)} accu's gevonden"
                )
            target = targets[battery - 1]
        entry_id, controller, coordinator = target
        if getattr(coordinator, "_is_shutting_down", False):
            raise AtomicBridgeError(f"Accu {battery} wordt afgesloten")
        if not getattr(coordinator, "is_available", True):
            raise AtomicBridgeError(f"Accu {battery} is niet bereikbaar")

        force_mode = 1 if charge_power else 2 if discharge_power else 0
        expected = {
            "force_mode": force_mode,
            "set_charge_power": charge_power,
            "set_discharge_power": discharge_power,
        }
        remaining = self._communication_backoff_remaining(coordinator)
        if remaining > 0:
            self._audit_atomic(
                coordinator,
                expected,
                source=str(source),
                reason=(
                    f"{reason}; communicatie-back-off nog {remaining:.0f} s"
                ).strip("; "),
                request_id=request_id,
                status="overgeslagen_communicatie_backoff",
                attempt=0,
                feedback=self._existing_feedback(coordinator, expected),
            )
            raise AtomicBridgeError(
                f"Accu {battery}: communicatie-back-off nog "
                f"{remaining:.0f} seconden"
            )
        lock = getattr(controller, "_control_lock", None)
        if lock is None:
            self._claim_control(entry_id, controller)
        else:
            # Alleen de eigendomsoverdracht deelt de algemene PD-lock. Iedere
            # accu heeft daarna zijn eigen coordinator-lock en TCP-verbinding,
            # zodat Accu 1/2/3 hun atomaire opdracht parallel mogen uitvoeren.
            async with lock:
                self._claim_control(entry_id, controller)
        command_lock = self._command_locks.setdefault(
            id(coordinator), asyncio.Lock()
        )
        async with command_lock:
            try:
                # Bij een time-out eenmaal opnieuw proberen; dezelfde
                # waarden opnieuw schrijven is veilig (idempotent).
                for attempt in (1, 2):
                    try:
                        result = await asyncio.wait_for(
                            self._write(
                                controller,
                                coordinator,
                                charge_power,
                                discharge_power,
                                force_mode,
                                source=str(source),
                                reason=str(reason),
                                request_id=request_id,
                            ),
                            timeout=self._write_timeout,
                        )
                        break
                    except asyncio.TimeoutError:
                        if attempt == 2:
                            raise
                        _LOGGER.warning(
                            "Accu %s: Modbus-opdracht trager dan %.0f s; "
                            "eenmaal opnieuw", battery, self._write_timeout,
                        )
            except asyncio.TimeoutError as error:
                delay = self._record_communication_failure(coordinator)
                self._audit_atomic(
                    coordinator,
                    expected,
                    source=str(source),
                    reason=(
                        f"{reason}; atomaire opdracht na "
                        f"{self._write_timeout:.1f} s afgebroken; "
                        f"back-off {delay:.0f} s"
                    ).strip("; "),
                    request_id=request_id,
                    status="afgebroken_communicatietimeout",
                    attempt=1,
                )
                raise AtomicBridgeError(
                    f"Accu {battery}: Modbus-opdracht duurde langer dan "
                    f"{self._write_timeout:.1f} seconden; "
                    f"{delay:.0f} seconden back-off"
                ) from error
            except AtomicBridgeError:
                self._record_communication_failure(coordinator)
                raise
            else:
                self._clear_communication_failure(coordinator)
                return result

    def _communication_backoff_remaining(self, coordinator):
        until = self._communication_backoff_until.get(id(coordinator), 0.0)
        return max(0.0, until - self._monotonic())

    def _record_communication_failure(self, coordinator):
        key = id(coordinator)
        failures = self._communication_failures.get(key, 0) + 1
        self._communication_failures[key] = failures
        delay = min(
            COMMUNICATION_BACKOFF_BASE_SECONDS * (2 ** (failures - 1)),
            COMMUNICATION_BACKOFF_MAX_SECONDS,
        )
        self._communication_backoff_until[key] = self._monotonic() + delay
        return delay

    def _clear_communication_failure(self, coordinator):
        key = id(coordinator)
        self._communication_failures.pop(key, None)
        self._communication_backoff_until.pop(key, None)

    async def set_powers(self, commands, request_id=None):
        """Voer één volledige OpenEMS-intentie parallel per accu uit."""
        latest = {}
        for command in commands:
            battery = int(command["battery"])
            latest[battery] = {
                "battery": battery,
                "charge_power": command.get("charge_power", 0),
                "discharge_power": command.get("discharge_power", 0),
                "source": command.get("source", "onbekend"),
                "reason": command.get("reason", ""),
                "entity_id": command.get("entity_id"),
            }
        return await asyncio.gather(*[
            self.set_power(
                command["battery"],
                command["charge_power"],
                command["discharge_power"],
                source=command["source"],
                reason=command["reason"],
                request_id=request_id,
                entity_id=command.get("entity_id"),
            )
            for command in latest.values()
        ])

    def enqueue_powers(self, commands, request_id=None):
        """Bewaar tijdens uitvoering uitsluitend de nieuwste complete batch."""
        self._pending_commands = [dict(command) for command in commands]
        self._pending_request_id = (
            str(request_id) if request_id is not None else None
        )
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = self.hass.async_create_task(
                self._run_latest_worker()
            )

    async def _run_latest_worker(self):
        while self._pending_commands is not None:
            commands = self._pending_commands
            request_id = self._pending_request_id
            self._pending_commands = None
            self._pending_request_id = None
            try:
                results = await self.set_powers(
                    commands, request_id=request_id
                )
                payload = {
                    "ok": True,
                    "request_id": request_id,
                    "results": results,
                }
            except Exception as error:
                payload = {
                    "ok": False,
                    "request_id": request_id,
                    "error": str(error),
                }
            panel_data = self.hass.data.setdefault("openems_m_panel", {})
            panel_data["last_atomic_result"] = payload
            self.hass.bus.async_fire(
                "openems_marstek_control_result",
                payload,
            )

    async def _write(
        self,
        controller,
        coordinator,
        charge_power,
        discharge_power,
        force_mode,
        source,
        reason,
        request_id,
    ):
        await self._ensure_rs485(
            coordinator, source, reason, request_id
        )

        expected = {
            "force_mode": force_mode,
            "set_charge_power": charge_power,
            "set_discharge_power": discharge_power,
        }
        existing = self._existing_feedback(coordinator, expected)
        if self._can_skip_existing(
            coordinator, discharge_power, force_mode, existing
        ):
            self._remember_controller_state(
                controller, coordinator, charge_power, discharge_power
            )
            self._audit_atomic(
                coordinator,
                expected,
                source=source,
                reason=reason,
                request_id=request_id,
                status="overgeslagen_ongewijzigd",
                attempt=0,
                feedback=existing,
            )
            return {
                "ok": True,
                "battery": coordinator.name,
                **expected,
                "battery_power": existing.get("battery_power"),
                "delivery_ok": True,
                "delivery_reason": "registers en levering al correct",
                "wake_attempted": False,
                "write_skipped": True,
            }
        last_feedback = None
        for attempt in range(1, 3):
            old_values = self._register_snapshot(coordinator, expected)
            last_feedback = await coordinator.write_power_atomic(
                discharge_power,
                charge_power,
                force_mode,
                read_back=True,
            )
            self._audit_atomic(
                coordinator,
                expected,
                source=source,
                reason=reason,
                request_id=request_id,
                status=(
                    "geschreven_bevestigd"
                    if last_feedback is not None and all(
                        int(last_feedback.get(key, -1)) == value
                        for key, value in expected.items()
                    )
                    else "geschreven_niet_bevestigd"
                ),
                attempt=attempt,
                feedback=last_feedback,
                old_values=old_values,
            )
            if last_feedback is not None and all(
                int(last_feedback.get(key, -1)) == value
                for key, value in expected.items()
            ):
                delivery = await self._check_delivery(
                    coordinator,
                    charge_power,
                    discharge_power,
                    force_mode,
                    expected,
                    last_feedback,
                    source,
                    reason,
                    request_id,
                )
                self._remember_controller_state(
                    controller, coordinator, charge_power, discharge_power
                )
                return {
                    "ok": True,
                    "battery": coordinator.name,
                    **expected,
                    "battery_power": delivery["feedback"].get(
                        "battery_power"
                    ),
                    "delivery_ok": delivery["ok"],
                    "delivery_reason": delivery["reason"],
                    "wake_attempted": delivery["wake_attempted"],
                    "write_skipped": False,
                }

        raise AtomicBridgeError(
            f"{coordinator.name} bevestigde de atomaire opdracht niet: "
            f"{last_feedback!r}"
        )

    @staticmethod
    def _existing_feedback(coordinator, expected):
        data = getattr(coordinator, "data", None) or {}
        try:
            matches = all(
                int(round(float(data.get(field)))) == value
                for field, value in expected.items()
            )
        except (TypeError, ValueError):
            return None
        if not matches:
            return None
        return {
            **expected,
            "battery_power": data.get("battery_power"),
        }

    @staticmethod
    def _can_skip_existing(
        coordinator, discharge_power, force_mode, feedback
    ):
        if feedback is None:
            return False
        if force_mode != 2 or discharge_power < 100:
            return True
        actual = feedback.get("battery_power")
        if actual is not None and abs(float(actual)) >= discharge_power * 0.10:
            return True
        data = getattr(coordinator, "data", None) or {}
        try:
            soc = float(data.get("battery_soc"))
        except (TypeError, ValueError):
            return False
        minimum_soc = float(getattr(coordinator, "min_soc", 0))
        return soc <= max(
            minimum_soc + 1, BMS_DISCHARGE_CUTOFF_SOC
        )

    async def _check_delivery(
        self,
        coordinator,
        charge_power,
        discharge_power,
        force_mode,
        expected,
        feedback,
        source,
        reason,
        request_id,
    ):
        """Bevestig ontlaadlevering zonder de opdracht tussendoor te wijzigen."""
        if force_mode != 2 or discharge_power < 100:
            return {
                "ok": True,
                "reason": "niet van toepassing",
                "wake_attempted": False,
                "feedback": feedback,
            }

        actual = feedback.get("battery_power")
        if actual is not None and abs(float(actual)) >= discharge_power * 0.10:
            return {
                "ok": True,
                "reason": "werkelijk vermogen bevestigd",
                "wake_attempted": False,
                "feedback": feedback,
            }

        data = getattr(coordinator, "data", None) or {}
        try:
            soc = float(data.get("battery_soc"))
        except (TypeError, ValueError):
            soc = None
        minimum_soc = float(getattr(coordinator, "min_soc", 0))
        cutoff = max(minimum_soc + 1, BMS_DISCHARGE_CUTOFF_SOC)
        if soc is not None and soc <= cutoff:
            return {
                "ok": True,
                "reason": "BMS-bescherming bij lage SOC",
                "wake_attempted": False,
                "feedback": feedback,
            }

        return {
            "ok": False,
            "reason": "geen werkelijk vermogen bevestigd",
            "wake_attempted": False,
            "feedback": feedback,
        }

    def _claim_control(self, entry_id, controller):
        """Pauzeer de MarsTek-PD-loop; OpenEMS wordt de enige schrijver."""
        if controller is None:
            return
        if not getattr(controller, "manual_mode_enabled", False):
            controller.manual_mode_enabled = True
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is not None and not entry.data.get(MANUAL_MODE_KEY, False):
                new_data = dict(entry.data)
                new_data[MANUAL_MODE_KEY] = True
                self.hass.config_entries.async_update_entry(
                    entry, data=new_data
                )
        controller.previous_power = 0
        controller.previous_error = 0
        controller._active_charge_batteries = []
        controller._active_discharge_batteries = []

    async def _ensure_rs485(
        self, coordinator, source, reason, request_id
    ):
        """Activeer RS485 wanneer native/handmatige modus hem heeft uitgezet."""
        data = getattr(coordinator, "data", None) or {}
        reported = data.get("rs485_control_mode")
        needs_enable = (
            getattr(coordinator, "rs485_user_disabled", False)
            or reported is not None and reported != RS485_ENABLE
        )
        if needs_enable:
            setter = getattr(coordinator, "set_rs485_user_disabled", None)
            if setter is not None:
                setter(False)
            register = coordinator.get_register("rs485_control")
            if register is None or not await coordinator.write_register(
                register, RS485_ENABLE, do_refresh=False
            ):
                raise AtomicBridgeError(
                    f"{coordinator.name}: RS485 kon niet worden geactiveerd"
                )
            self._audit(
                coordinator, register, "rs485_control_mode",
                reported, RS485_ENABLE, source,
                f"{reason}; RS485 vereist voor OpenEMS-opdracht".strip("; "),
                request_id, "geschreven_rs485_aan",
            )
            if getattr(coordinator, "data", None) is not None:
                coordinator.data["rs485_control_mode"] = RS485_ENABLE

    @staticmethod
    def _register_snapshot(coordinator, expected):
        data = getattr(coordinator, "data", None) or {}
        return {key: data.get(key) for key in expected}

    def _audit_atomic(
        self,
        coordinator,
        expected,
        *,
        source,
        reason,
        request_id,
        status,
        attempt,
        feedback=None,
        old_values=None,
    ):
        old_values = old_values or self._register_snapshot(
            coordinator, expected
        )
        feedback = feedback or {}
        for key, new_value in expected.items():
            register = coordinator.get_register(key)
            self._audit(
                coordinator,
                register,
                key,
                old_values.get(key),
                new_value,
                source,
                reason,
                request_id,
                status,
                attempt=attempt,
                read_back=feedback.get(key),
            )

    def _audit(
        self,
        coordinator,
        register,
        key,
        old_value,
        new_value,
        source,
        reason,
        request_id,
        status,
        *,
        attempt=None,
        read_back=None,
        writer="openems_m_panel.marstek_atomic_bridge",
    ):
        payload = {
            "tijd": datetime.now(timezone.utc).isoformat(),
            "batterij": str(coordinator.name),
            "register": register,
            "sleutel": key,
            "oude_waarde": old_value,
            "nieuwe_waarde": new_value,
            "read_back": read_back,
            "bron": str(source or "onbekend"),
            "reden": str(reason or "niet opgegeven"),
            "schrijver": writer,
            "status": status,
            "poging": attempt,
            "request_id": request_id,
        }
        # Alleen afwijkingen als waarschuwing; normale schrijfopdrachten als
        # info en ongewijzigde (overgeslagen) opdrachten alleen bij debug.
        _LOGGER.log(
            self._audit_log_level(status),
            "OPENEMS_MODBUS_WRITE battery=%s register=%s key=%s old=%r "
            "new=%r read_back=%r source=%s reason=%s writer=%s status=%s "
            "attempt=%s request_id=%s",
            payload["batterij"], payload["register"], payload["sleutel"],
            payload["oude_waarde"], payload["nieuwe_waarde"],
            payload["read_back"], payload["bron"], payload["reden"],
            payload["schrijver"], payload["status"], payload["poging"],
            payload["request_id"],
        )
        self.hass.bus.async_fire("openems_modbus_write_audit", payload)

    @staticmethod
    def _audit_log_level(status):
        status = str(status or "")
        if status == "overgeslagen_ongewijzigd":
            return logging.DEBUG
        if status in (
            "geschreven_bevestigd",
            "geschreven_rs485_aan",
            "raw_write_ok",
        ):
            return logging.INFO
        return logging.WARNING

    @staticmethod
    def _remember_controller_state(
        controller, coordinator, charge_power, discharge_power
    ):
        if controller is None:
            return
        controller.previous_power = (
            charge_power if charge_power else -discharge_power
        )
        if charge_power:
            controller._active_charge_batteries = [coordinator]
            controller._active_discharge_batteries = []
        elif discharge_power:
            controller._active_charge_batteries = []
            controller._active_discharge_batteries = [coordinator]
        else:
            controller._active_charge_batteries = []
            controller._active_discharge_batteries = []

    @staticmethod
    def _power(value):
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError) as error:
            raise AtomicBridgeError(f"Ongeldig vermogen: {value!r}") from error
        if number < 0 or number > 2500:
            raise AtomicBridgeError(
                f"Vermogen {number} W valt buiten 0..2500 W"
            )
        return number

    @staticmethod
    def _natural_key(value):
        return [
            int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", str(value))
        ]
