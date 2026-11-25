# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Veilige centrale aansturing voor de Marstek-accu's.

Alle Home Assistant schrijfacties naar de Marstek-systemen horen in deze
module. De planner bepaalt wat gewenst is; deze controller voert het later uit.
Automatische uitvoering staat standaard uit via ems_config.AUTO_CONTROL_ENABLED.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Sequence

from .ems_config import BMS_DISCHARGE_CUTOFF_SOC, DEVICE_RESET_ENABLED

from .mode_coordinator import OperatingModeCoordinator


class MarstekController:
    """Voert gecontroleerde schrijfopdrachten uit voor alle accu's."""

    def __init__(
        self,
        app,
        batteries,
        enabled=False,
        test_enabled=False,
        command_delay=0.25,
        atomic_control=False,
    ):
        self.app = app
        self.batteries = batteries
        self.enabled = bool(enabled)
        self.test_enabled = bool(test_enabled)
        self.command_delay = max(0.0, float(command_delay))
        self.atomic_control = bool(atomic_control)
        self._last_command = None
        self._last_duplicate_log_at = 0.0
        self._duplicate_log_interval = 60.0
        self._sequence_generation = 0
        self._sequence_active = False
        self._sequence_dispatch_complete = False
        self._sequence_source = None
        self._queued_sequence = None
        self._last_sequence_finish_delay = 0.0
        self._last_sequence_completed_at = None
        self._sequence_phases = []
        self._sequence_last_phase_by_battery = {}
        self._last_atomic_request_id = None
        self._last_atomic_result_ok = None
        self._atomic_batch_pending = False
        self._atomic_request_battery_indexes = []
        self._direction_handoff_generation = 0
        self._pending_direction_handoff = None
        self._battery_desired_since = [0.0 for _ in self.batteries]
        # Een apparaat-reset is de zwaarste automatische herstelstap. Houd de
        # poging centraal bij zodat automatische en handmatige bewaking nooit
        # dezelfde accu tegelijk of herhaaldelijk kunnen resetten.
        self.device_reset_enabled = DEVICE_RESET_ENABLED
        self._device_reset_attempted = [False for _ in self.batteries]
        self._device_reset_pending = [False for _ in self.batteries]
        self._device_reset_owner = [None for _ in self.batteries]
        self.coordinator = None
        self.load_balancer = None
        self._released_log_at = 0.0
        self._requested_charge = None
        self.requested_charge_total = 0.0
        self._requested_discharge = None
        self.requested_discharge_total = 0.0
        self.health_manager = None
        self.grid_status_monitor = None
        self.mode_coordinator = OperatingModeCoordinator(app)
        self.desired_state = None
        self.desired_powers = []
        self.desired_source = None
        self.desired_since = 0.0

    def release_control(self, source="manual") -> None:
        """Laat alle accu's los: OpenEMS stuurt of corrigeert niets meer.

        Alleen een handmatige opdracht, een bewuste test of het weer aanzetten
        van Auto neemt de besturing weer over.
        """
        self._deactivate_coordinator_for(source)
        self._cancel_sequence(clear_queue=True)
        self._requested_charge = None
        self._requested_discharge = None
        self._last_command = None
        self._remember_desired(
            "released", [0.0 for _ in self.batteries], source
        )
        self.app.log(
            "Controller: accu's losgelaten; OpenEMS stuurt niet meer aan "
            "tot een nieuwe opdracht"
        )

    def _release_blocks(self, source) -> bool:
        if self.desired_state != "released":
            return False
        if source in {"manual", "test"}:
            return False
        if self.enabled:
            return False
        now = time.monotonic()
        if now - self._released_log_at >= 300:
            self._released_log_at = now
            self.app.log(
                f"Controller: opdracht van '{source}' genegeerd; accu's zijn "
                "losgelaten (handmatige modus Stop)"
            )
        return True

    def bind_load_balancer(self, load_balancer) -> None:
        self.load_balancer = load_balancer

    def _import_limited_powers(self, powers, source="auto"):
        """Begrens het totale laadvermogen volgens de load balancer."""
        values = [max(0.0, float(value)) for value in powers]
        self.requested_charge_total = sum(values)
        balancer = self.load_balancer
        if balancer is None:
            return values
        cap = balancer.charge_cap(source)
        total = sum(values)
        if cap is None or total <= cap or total <= 0:
            return values
        factor = max(0.0, cap) / total
        return [value * factor for value in values]

    def _export_limited_powers(self, powers, source="auto"):
        """Begrens het totale ontlaadvermogen zodat de export onder de grens blijft."""
        values = [max(0.0, float(value)) for value in powers]
        self.requested_discharge_total = sum(values)
        balancer = self.load_balancer
        if balancer is None:
            return values
        cap = balancer.discharge_cap(source)
        total = sum(values)
        if cap is None or total <= cap or total <= 0:
            return values
        factor = max(0.0, cap) / total
        return [value * factor for value in values]

    def reapply_discharge(self) -> bool:
        """Voer de laatste (onbegrensde) ontlaadopdracht opnieuw uit."""
        requested = self._requested_discharge
        if requested is None or self.desired_state != "forced_discharge":
            return False
        powers, source = requested
        return self.set_discharge(powers, source=source)

    def reapply_charge(self) -> bool:
        """Voer de laatste (onbegrensde) laadopdracht opnieuw uit."""
        requested = self._requested_charge
        if requested is None or self.desired_state != "charging":
            return False
        powers, source = requested
        return self.set_charge(powers, source=source)

    def bind_coordinator(self, coordinator) -> None:
        self.coordinator = coordinator

    def bind_health_manager(self, health_manager) -> None:
        self.health_manager = health_manager

    def bind_grid_status_monitor(self, monitor) -> None:
        self.grid_status_monitor = monitor

    def suspend_for_grid_outage(self) -> None:
        """Stop alleen OpenEMS-writes; laat de interne backupregeling intact."""
        if self.coordinator is not None:
            self.coordinator.deactivate(clear_latch=True)
        self._cancel_sequence(clear_queue=True)
        self._last_command = None
        self.desired_state = "grid_outage"
        self.desired_powers = []
        self.desired_source = "grid_status"
        self.desired_since = time.monotonic()
        self.app.log(
            "Controller gepauzeerd voor netuitval; backupfunctie niet gewijzigd"
        )

    def resume_after_grid_outage(self) -> None:
        """Maak na stabiel netherstel een volledige nieuwe opdracht mogelijk."""
        self._last_command = None
        self.desired_state = None
        self.desired_powers = []
        self.desired_source = None
        self.desired_since = time.monotonic()
        self.app.log("Controller vrijgegeven na stabiel netherstel")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.app.log(
            "Marstek automatische aansturing: "
            f"{'AAN' if self.enabled else 'UIT'}"
        )
        if self.enabled and self.desired_state == "released":
            # Auto neemt de losgelaten accu's weer over met een bekende stand.
            self.desired_state = None
            self._last_command = None
            self.set_stop(source="auto")

    def set_charge(self, powers: Sequence[float], source="auto") -> bool:
        """Laadt alleen accu's met vermogen; overige accu's worden gestopt."""
        self._deactivate_coordinator_for(source)
        self._prepare_explicit_manual_control(source)
        normalized = self._normalize_powers(powers, "max_charge_power")
        normalized = self._health_filtered_powers(normalized)
        self._requested_charge = (list(normalized), source)
        normalized = self._import_limited_powers(normalized, source)
        if self._opposite_direction_batteries("charge"):
            return self._start_direction_handoff(
                "charge", normalized, source
            )
        signature = (
            "charge", source, tuple(round(value) for value in normalized)
        )
        if self._is_duplicate(signature):
            return True
        if not self._can_execute("laden", normalized, source):
            return False

        smooth_update = self._can_update_power_smoothly(
            "charging", normalized
        )
        active_indexes = [
            index for index, power in enumerate(normalized) if power > 0
        ]
        stopped_indexes = [
            index for index, power in enumerate(normalized) if power <= 0
        ]
        if smooth_update:
            commands = self._build_smooth_power_update_commands(
                "charge", normalized
            )
        else:
            commands = self._build_stop_commands(stopped_indexes)
            commands.extend(self._build_power_commands(
                "charge",
                [normalized[index] for index in active_indexes],
                active_indexes,
            ))
        self._start_sequence(commands, source=source)
        self._remember_desired("charging", normalized, source)
        self._last_command = signature
        self.app.log(
            (
                "Controller laadvermogen vloeiend aangepast: "
                if smooth_update
                else "Controller laadreeks gestart: laden | "
            )
            + ", ".join(
                f"{battery['name']} {power:.0f} W"
                for battery, power in zip(self.batteries, normalized)
            )
        )
        return True

    def set_discharge(self, powers: Sequence[float], source="auto") -> bool:
        """Zet alle accu's in ontladen met een vermogen per accu."""
        self._deactivate_coordinator_for(source)
        self._prepare_explicit_manual_control(source)
        normalized = self._normalize_powers(powers, "max_discharge_power")
        normalized = self._health_filtered_powers(normalized)
        self._requested_discharge = (list(normalized), source)
        normalized = self._export_limited_powers(normalized, source)
        if self._opposite_direction_batteries("discharge"):
            return self._start_direction_handoff(
                "discharge", normalized, source
            )
        signature = (
            "discharge", source, tuple(round(value) for value in normalized)
        )
        if self._is_duplicate(signature):
            return True
        if not self._can_execute("ontladen", normalized, source):
            return False

        smooth_update = self._can_update_power_smoothly(
            "forced_discharge", normalized
        )
        if smooth_update:
            commands = self._build_smooth_power_update_commands(
                "discharge", normalized
            )
        else:
            active_indexes = [
                index for index, power in enumerate(normalized) if power > 0
            ]
            stopped_indexes = [
                index for index, power in enumerate(normalized) if power <= 0
            ]
            commands = self._build_stop_commands(stopped_indexes)
            commands.extend(self._build_power_commands(
                "discharge",
                [normalized[index] for index in active_indexes],
                active_indexes,
            ))
        self._start_sequence(commands, source=source)
        self._remember_desired("forced_discharge", normalized, source)
        self._last_command = signature
        self.app.log(
            (
                "Controller ontlaadvermogen vloeiend aangepast: "
                if smooth_update
                else "Controller ontlaadreeks gestart: ontladen | "
            )
            + ", ".join(
                f"{battery['name']} {power:.0f} W"
                for battery, power in zip(self.batteries, normalized)
            )
        )
        return True

    def set_self_consumption(self, source="auto", force_native=False) -> bool:
        """Schakelt alle accu's naar anti-feed/self-consumption."""
        selector = (
            getattr(self.coordinator, "battery_selector", None)
            if self.coordinator is not None else None
        )
        mode = (
            self.coordinator.settings.self_consumption_mode()
            if self.coordinator is not None else "marstek"
        )
        if (
            not force_native
            and mode == "marstek"
            and selector is not None
            and selector.enabled()
            and getattr(selector, "native_active", False)
        ):
            return True
        if (
            not force_native
            and self.coordinator is not None
            and mode == "openems"
        ):
            if (
                selector is not None
                and getattr(selector, "native_active", False)
            ):
                selector.deactivate()
            if self.coordinator.active:
                return True
            return self.coordinator.activate()
        if self.coordinator is not None:
            self.coordinator.deactivate(clear_latch=not force_native)
        if (
            not force_native
            and selector is not None
            and selector.enabled()
        ):
            return selector.activate_native(source=source)
        signature = ("self_consumption", source)
        if self._is_duplicate(signature):
            return True
        if not self._can_execute("self-consumption", source=source):
            return False

        # Stop de geforceerde registers en kies daarna expliciet anti-feed.
        # Alleen bij deze overgang schrijven we User Work Mode: het uitschakelen
        # van RS485 door de firmware is hier juist het gewenste gedrag.
        self._start_sequence(
            self._build_native_control_commands(), source=source
        )
        self._remember_desired("self_consumption", [], source)
        self._last_command = signature
        self.app.log("Controllerreeks gestart: native self-consumption")
        return True

    def set_native_self_consumption_selection(
        self, active_indexes, source="selector"
    ) -> bool:
        """Zet alleen geselecteerde accu's op native anti-feed."""
        active = self._battery_indexes(active_indexes)
        if self.health_manager is not None:
            active = self.health_manager.filter_indexes(active)
        inactive = [
            index for index in range(len(self.batteries))
            if index not in active
        ]
        signature = (
            "native_self_consumption_selection",
            source,
            tuple(active),
        )
        if self._is_duplicate(signature):
            return True
        if not self._can_execute(
            "native accuselector", source=source
        ):
            return False
        commands = self._build_native_control_commands(active)
        commands.extend(
            self._build_isolated_stop_commands(inactive)
        )
        self._start_sequence(commands, source=source)
        self._remember_desired("self_consumption", [], source)
        self._last_command = signature
        self.app.log(
            "Native accuselector toegepast | actief: "
            + (
                ", ".join(self.batteries[index]["name"] for index in active)
                if active else "geen"
            )
            + " | Force Stop: "
            + (
                ", ".join(
                    self.batteries[index]["name"] for index in inactive
                )
                if inactive else "geen"
            )
        )
        return True

    def repair_native_self_consumption_selection(
        self, active_indexes, failed_indexes, source="selector"
    ) -> bool:
        """Herstel alleen accu's waarvan de native read-back echt afwijkt.

        De normale write-only-if-changed-bescherming wordt hier bewust voor
        deze bevestigde herstelactie doorbroken. Home Assistant kan na een
        service-time-out namelijk al optimistisch ``off`` tonen terwijl de
        Venus de RS485-opdracht niet heeft verwerkt.
        """
        active = set(self._battery_indexes(active_indexes))
        failed = self._battery_indexes(failed_indexes)
        if not failed:
            return True
        if not self._can_execute("native accuselector herstel", source=source):
            return False
        repair_active = [index for index in failed if index in active]
        repair_inactive = [index for index in failed if index not in active]
        commands = self._build_native_control_commands(
            repair_active, force_write=True
        )
        commands.extend(self._build_isolated_stop_commands(
            repair_inactive, force_write=True
        ))
        self._start_sequence(commands, source=source)
        self._remember_desired("self_consumption", [], source)
        self._last_command = (
            "native_self_consumption_repair",
            source,
            tuple(failed),
            time.monotonic(),
        )
        self.app.log(
            "Native accuselector gericht hersteld | "
            + ", ".join(self.batteries[index]["name"] for index in failed),
            level="WARNING",
        )
        return True

    def _start_direction_handoff(self, direction, powers, source):
        """Stop centraal voordat laden en ontladen van richting wisselen."""
        self._direction_handoff_generation += 1
        generation = self._direction_handoff_generation
        self._pending_direction_handoff = (
            str(direction), list(powers), str(source), generation
        )
        if not self._can_execute("veilige richtingswissel", source=source):
            self._pending_direction_handoff = None
            return False
        self._start_sequence(
            self._build_stop_commands(),
            source=f"{source}:richtingswissel",
        )
        self._remember_desired(
            "stopped", [0.0 for _ in self.batteries], source
        )
        self._last_command = None
        self.app.log(
            "Controller richtingswissel: eerst alle accu's bevestigd stoppen",
            level="WARNING",
        )
        self.app.run_in(
            self._resume_direction_handoff,
            1,
            generation=generation,
        )
        return True

    def _resume_direction_handoff(self, kwargs):
        pending = self._pending_direction_handoff
        if pending is None:
            return
        direction, powers, source, generation = pending
        if int(kwargs["generation"]) != generation:
            return
        if self.command_sequence_pending():
            self.app.run_in(
                self._resume_direction_handoff,
                1,
                generation=generation,
            )
            return
        opposite = self._opposite_direction_batteries(direction)
        if opposite:
            self.app.log(
                "Controller richtingswissel geblokkeerd; tegengestelde "
                "richting nog actief op " + ", ".join(opposite),
                level="ERROR",
            )
            self.app.run_in(
                self._resume_direction_handoff,
                1,
                generation=generation,
            )
            return
        self._pending_direction_handoff = None
        if direction == "charge":
            self.set_charge(powers, source=source)
        else:
            self.set_discharge(powers, source=source)

    def _opposite_direction_batteries(self, direction):
        if (
            direction == "discharge" and self.desired_state == "charging"
        ) or (
            direction == "charge"
            and self.desired_state == "forced_discharge"
        ):
            return [
                battery.get("name", f"Accu {index + 1}")
                for index, battery in enumerate(self.batteries)
            ]
        opposite = []
        for index, battery in enumerate(self.batteries):
            if self.battery_disabled(index):
                # Een uitgeschakelde accu mag zelf (bv. via de Marstek-app)
                # laden of ontladen zonder de overige accu's te blokkeren.
                continue
            actual = self._optional_state_number(battery.get("actual_power"))
            charge = self._optional_state_number(battery.get("charge_power"))
            discharge = self._optional_state_number(
                battery.get("discharge_power")
            )
            force = self._state_text(battery.get("force_mode"))
            conflict = (
                direction == "discharge"
                and (
                    (actual is not None and actual < -100)
                    or (charge is not None and charge > 100)
                    or force == "charge"
                )
            ) or (
                direction == "charge"
                and (
                    (actual is not None and actual > 100)
                    or (discharge is not None and discharge > 100)
                    or force == "discharge"
                )
            )
            if conflict:
                opposite.append(battery.get("name", f"Accu {index + 1}"))
        return opposite

    def _optional_state_number(self, entity_id):
        if not entity_id:
            return None
        value = self.app.get_state(entity_id)
        if value in (None, "", "unknown", "unavailable"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def set_stop(self, source="auto", require_confirmation=False) -> bool:
        """Stopt geforceerd laden en ontladen zonder RS485 uit te schakelen."""
        self._direction_handoff_generation += 1
        self._pending_direction_handoff = None
        self._deactivate_coordinator_for(source)
        signature = ("stop", source)
        if not require_confirmation and self._is_duplicate(signature):
            return True
        if not self._can_execute("stop", source=source):
            return False

        self._start_sequence(self._build_stop_commands(), source=source)
        self._remember_desired(
            "stopped", [0.0 for _ in self.batteries], source
        )
        self._last_command = signature
        self.app.log("Controller stopreeks gestart")
        return True


    def verify_state(
        self,
        state,
        expected_powers=None,
        battery_indexes=None,
    ) -> dict:
        """Controleert of alle accu's de gewenste schedulerstate hebben."""
        if self.command_sequence_pending():
            return {
                "ok": False,
                "pending": True,
                "summary": (
                    "Controlleropdracht wordt nog volledig uitgevoerd; "
                    "verificatie uitgesteld"
                ),
                "failed_indexes": [],
                "batteries": [],
            }
        state_value = getattr(state, "value", str(state))
        if (
            state_value in {"discharging", "self_consumption"}
            and self.coordinator is not None
            and self.coordinator.active
        ):
            return self.coordinator.verification_result()
        selector = (
            getattr(self.coordinator, "battery_selector", None)
            if self.coordinator is not None else None
        )
        if (
            state_value in {"discharging", "self_consumption"}
            and selector is not None
            and getattr(selector, "native_active", False)
        ):
            return selector.verification_result()
        expected_powers = list(expected_powers or [])
        details = []
        battery_results = []
        failed_indexes = []
        all_ok = True

        for index in self._battery_indexes(battery_indexes):
            if self.battery_disabled(index):
                # Uitgeschakelde accu: OpenEMS stuurt hem niet, dus ook
                # niet controleren of herstellen.
                continue
            battery = self.batteries[index]
            work_mode = self._state_text(battery["work_mode"])
            force_mode = self._state_text(battery["force_mode"])
            rs485 = self._state_text(battery["rs485_control"])
            charge_power = self._state_number(battery["charge_power"])
            discharge_power = self._state_number(battery["discharge_power"])
            actual_entity = battery.get("actual_power")
            actual_power = (
                self._state_number(actual_entity)
                if actual_entity else None
            )

            if state_value == "charging":
                expected = expected_powers[index] if index < len(expected_powers) else 0.0
                if expected > 0:
                    battery_ok = (
                        (self.atomic_control or work_mode == "anti_feed")
                        and rs485 == "on"
                        and force_mode == "charge"
                        and self._power_matches(charge_power, expected)
                        and discharge_power <= 100
                        and self._physical_delivery_started(
                            actual_power, expected, "charge"
                        )
                    )
                else:
                    battery_ok = (
                        rs485 == "on"
                        and force_mode in {"standby", "stop"}
                        and charge_power <= 25
                        and discharge_power <= 25
                        and (
                            actual_power is None
                            or abs(actual_power) <= 100
                        )
                    ) if self.atomic_control else (
                        work_mode == "anti_feed" and (
                            rs485 == "off" or (
                                force_mode in {"standby", "stop"}
                                and charge_power <= 25
                                and discharge_power <= 25
                                and (
                                    actual_power is None
                                    or abs(actual_power) <= 100
                                )
                            )
                        )
                    )
            elif state_value in {"discharging", "self_consumption"}:
                # Force- en vermogensregisters mogen een oude waarde tonen zodra
                # anti-feed actief is en de native Marstek-regeling de leiding heeft.
                battery_ok = work_mode == "anti_feed" and rs485 == "off"
            elif state_value == "forced_discharge":
                expected = expected_powers[index] if index < len(expected_powers) else 0.0
                if expected > 0:
                    battery_ok = (
                        (self.atomic_control or work_mode == "anti_feed")
                        and rs485 == "on"
                        and force_mode == "discharge"
                        and charge_power <= 100
                        and self._power_matches(discharge_power, expected)
                        and (
                            self._battery_at_discharge_cutoff(battery)
                            or self._physical_delivery_started(
                                actual_power, expected, "discharge"
                            )
                        )
                    )
                else:
                    battery_ok = (
                        rs485 == "on"
                        and force_mode in {"standby", "stop"}
                        and charge_power <= 25
                        and discharge_power <= 25
                        and (
                            actual_power is None
                            or abs(actual_power) <= 100
                        )
                    ) if self.atomic_control else (
                        work_mode == "anti_feed" and (
                            rs485 == "off" or (
                                force_mode in {"standby", "stop"}
                                and charge_power <= 25
                                and discharge_power <= 25
                                and (
                                    actual_power is None
                                    or abs(actual_power) <= 100
                                )
                            )
                        )
                    )
            elif state_value in {"idle", "stopped"}:
                if self.atomic_control:
                    battery_ok = (
                        rs485 == "on"
                        and force_mode in {"standby", "stop"}
                        and charge_power <= 25
                        and discharge_power <= 25
                        and (
                            actual_power is None
                            or abs(actual_power) <= 100
                        )
                    )
                else:
                    battery_ok = work_mode == "anti_feed" and (
                        rs485 == "off" or (
                            force_mode in {"standby", "stop"}
                            and charge_power <= 25
                            and discharge_power <= 25
                            and (
                                actual_power is None
                                or abs(actual_power) <= 100
                            )
                        )
                    )
            else:
                battery_ok = False

            all_ok = all_ok and battery_ok
            if not battery_ok:
                failed_indexes.append(index)
            details.append(
                f"{battery['name']}={'OK' if battery_ok else 'FOUT'} "
                f"(mode={work_mode}, rs485={rs485}, force={force_mode}, "
                f"laden={charge_power:.0f} W, ontladen={discharge_power:.0f} W"
                + (
                    f", werkelijk={actual_power:.0f} W)"
                    if actual_power is not None else ")"
                )
            )
            battery_results.append({
                "index": index,
                "name": battery["name"],
                "ok": battery_ok,
                "work_mode": work_mode,
                "rs485": rs485,
                "force_mode": force_mode,
                "charge_power": charge_power,
                "discharge_power": discharge_power,
                "actual_power": actual_power,
            })

        return {
            "ok": all_ok,
            "summary": " | ".join(details),
            "failed_indexes": failed_indexes,
            "batteries": battery_results,
        }

    def stop_batteries(
        self, battery_indexes, source="auto", safety=False
    ) -> bool:
        """Stopt uitsluitend de opgegeven accu's voor een herstelactie."""
        indexes = self._battery_indexes(battery_indexes)
        if not indexes:
            return True
        if not self._can_execute("gerichte stop", source=source):
            return False

        self._start_sequence(
            self._build_stop_commands(indexes),
            source=source,
            safety=safety,
        )
        self._last_command = None
        self.app.log(
            "Controller gerichte stopreeks gestart: "
            + ", ".join(self.batteries[index]["name"] for index in indexes)
        )
        return True

    def isolate_batteries(
        self, battery_indexes, source="auto", safety=False
    ) -> bool:
        """Isoleert accu's veilig zonder native zelfconsumptie als fallback."""
        indexes = self._battery_indexes(battery_indexes)
        if not indexes:
            return True
        if not self._can_execute("veilige isolatie", source=source):
            return False

        self._start_sequence(
            self._build_isolated_stop_commands(indexes),
            source=source,
            safety=safety,
        )
        self._last_command = None
        self.app.log(
            "Controller veilige isolatiereeks gestart: "
            + ", ".join(self.batteries[index]["name"] for index in indexes)
        )
        return True

    def restore_batteries(
        self,
        state,
        battery_indexes,
        expected_powers=None,
        source="auto",
    ) -> bool:
        """Herstelt uitsluitend afwijkende accu's naar de gewenste state."""
        state_value = getattr(state, "value", str(state))
        indexes = self._battery_indexes(battery_indexes)
        expected_powers = list(expected_powers or [])
        if not indexes:
            return True
        if not self._can_execute("gerichte herstelactie", source=source):
            return False

        if state_value == "charging":
            active_indexes = [
                index for index in indexes
                if index < len(expected_powers)
                and expected_powers[index] > 0
            ]
            stopped_indexes = [
                index for index in indexes if index not in active_indexes
            ]
            commands = self._build_stop_commands(stopped_indexes)
            commands.extend(self._build_power_commands(
                "charge",
                [expected_powers[index] for index in active_indexes],
                active_indexes,
            ))
        elif state_value == "forced_discharge":
            powers = [expected_powers[index] for index in indexes]
            commands = self._build_power_commands("discharge", powers, indexes)
        elif state_value in {"discharging", "self_consumption"}:
            if self.coordinator is not None and self.coordinator.active:
                coordinated_powers = self.coordinator.expected_powers
                direction = self.coordinator.desired_direction
                if direction in {"charge", "discharge"}:
                    powers = [coordinated_powers[index] for index in indexes]
                    commands = self._build_power_commands(
                        direction, powers, indexes
                    )
                else:
                    commands = self._build_stop_commands(indexes)
            elif (
                self.coordinator is not None
                and getattr(
                    getattr(
                        self.coordinator, "battery_selector", None
                    ),
                    "native_active",
                    False,
                )
            ):
                selected = set(
                    self.coordinator.battery_selector.active_indexes
                )
                active_indexes = [
                    index for index in indexes if index in selected
                ]
                stopped_indexes = [
                    index for index in indexes if index not in selected
                ]
                commands = self._build_native_control_commands(
                    active_indexes
                )
                commands.extend(
                    self._build_isolated_stop_commands(stopped_indexes)
                )
            else:
                commands = self._build_native_control_commands(indexes)
        elif state_value in {"idle", "stopped"}:
            commands = self._build_stop_commands(indexes)
        else:
            raise ValueError(f"Ongeldige herstelstate: {state_value}")

        self._start_sequence(commands, source=source)
        self.desired_since = time.monotonic()
        self._last_command = None
        self.app.log(
            f"Controller gerichte herstelreeks gestart: {state_value} | "
            + ", ".join(self.batteries[index]["name"] for index in indexes)
        )
        return True

    def _state_text(self, entity_id: str) -> str:
        state = self.app.get_state(entity_id)
        return "unavailable" if state is None else str(state).lower()

    def _state_number(self, entity_id: str) -> float:
        state = self.app.get_state(entity_id)
        try:
            return float(state)
        except (TypeError, ValueError):
            return float("inf")

    def enable_rs485(self, source="auto") -> bool:
        if not self._can_execute("RS485 inschakelen", source=source):
            return False
        self._cancel_sequence()
        self._enable_rs485_all()
        return True

    def request_device_reset(self, index, source="auto") -> bool:
        """Reset precies een accu, maximaal eenmaal tot bewezen herstel."""
        index = self._battery_indexes([index])[0]
        battery = self.batteries[index]
        if not self.device_reset_enabled:
            self.app.log(
                f"{battery['name']}: apparaat-reset staat uit; "
                "OpenEMS drukt nooit op een resetknop",
                level="WARNING",
            )
            return False
        entity_id = battery.get("reset_device")
        if not entity_id:
            self.app.log(
                f"{battery['name']}: apparaat-reset niet beschikbaar; "
                "resetknop is niet ingesteld",
                level="ERROR",
            )
            return False
        if self._device_reset_attempted[index]:
            self.app.log(
                f"{battery['name']}: apparaat-reset niet herhaald; de "
                "eenmalige resetpoging is al gebruikt",
                level="WARNING",
            )
            return False
        if self.command_sequence_pending():
            self.app.log(
                f"{battery['name']}: apparaat-reset wacht; een "
                "controlleropdracht is nog niet afgerond",
                level="WARNING",
            )
            return False
        if not self._can_execute("apparaat-reset", source=source):
            return False

        self._device_reset_attempted[index] = True
        self._device_reset_pending[index] = True
        self._device_reset_owner[index] = str(source)
        self.app.call_service("button/press", entity_id=entity_id)
        self.app.log(
            f"{battery['name']}: eenmalige apparaat-reset gestart via "
            f"{entity_id}",
            level="WARNING",
        )
        return True

    def device_reset_pending(self, index) -> bool:
        index = self._battery_indexes([index])[0]
        return self._device_reset_pending[index]

    def device_reset_owner(self, index):
        index = self._battery_indexes([index])[0]
        return self._device_reset_owner[index]

    def complete_device_reset(self, index, recovered=False) -> None:
        """Rond de reset af; alleen bewezen herstel geeft een nieuwe kans."""
        index = self._battery_indexes([index])[0]
        self._device_reset_pending[index] = False
        self._device_reset_owner[index] = None
        if recovered:
            self._device_reset_attempted[index] = False
            self.app.log(
                f"{self.batteries[index]['name']}: apparaat-reset heeft "
                "de besturing hersteld; resetvergrendeling vrijgegeven"
            )

    def disable_rs485(self, source="auto") -> bool:
        if not self._can_execute("RS485 uitschakelen", source=source):
            return False
        self._cancel_sequence()
        for battery in self.batteries:
            self._dispatch_command(
                "rs485_off_one",
                {"entity_id": battery["rs485_control"]},
                source=source,
            )
            self._pause()
        self._last_command = ("rs485_off",)
        self.app.log("Controller uitgevoerd: RS485 uitgeschakeld")
        return True

    def _enable_rs485_all(self) -> None:
        for battery in self.batteries:
            self._dispatch_command(
                "rs485_on_one",
                {"entity_id": battery["rs485_control"]},
                source="enable_rs485",
            )
            self._pause()

    def _start_sequence(self, commands, source="auto", safety=False) -> None:
        """Serialiseert opdrachten; alleen een veiligheidsstop mag onderbreken."""
        commands = self._without_disabled_battery_commands(commands)
        if not commands:
            return
        if self._sequence_active or self._atomic_batch_pending:
            if not safety:
                # Alleen de nieuwste volledige gewenste opdracht is relevant.
                # Zo kunnen scheduler, selector en herstelmonitor nooit elkaars
                # half uitgevoerde registerreeks ongeldig maken.
                self._queued_sequence = (commands, source)
                queued_finish = max(
                    0.0,
                    (len({int(item[0]) for item in commands}) - 1)
                    * self.command_delay,
                )
                self._last_sequence_finish_delay += (
                    self.command_delay + queued_finish
                )
                self.app.log(
                    "Controlleropdracht veilig in wachtrij gezet: "
                    f"{source} wacht op {self._sequence_source or 'lopende reeks'}"
                )
                return
            self.app.log(
                "Lopende controllerreeks onderbroken door veiligheidsisolatie",
                level="WARNING",
            )
            self._cancel_sequence(clear_queue=True)

        self._begin_sequence(commands, source)

    def _begin_sequence(self, commands, source) -> None:
        """Start een reeds toegelaten volledige commandoreeks."""
        self._release_pending_deliveries()
        self._sequence_generation += 1
        self._sequence_active = True
        self._sequence_dispatch_complete = False
        self._sequence_source = source
        self._last_atomic_request_id = None
        self._last_atomic_result_ok = None
        self._atomic_batch_pending = False
        self._atomic_request_battery_indexes = []
        generation = self._sequence_generation
        phases = {}
        for phase, action, parameters in commands:
            phases.setdefault(int(phase), []).append((action, parameters))
        self._sequence_phases = [
            phases[phase] for phase in sorted(phases)
        ]
        self._last_sequence_finish_delay = max(
            0.0, (len(self._sequence_phases) - 1) * self.command_delay
        )
        last_phase_by_battery = {}
        for phase_index, phase_commands in enumerate(self._sequence_phases):
            for _action, parameters in phase_commands:
                battery_index = self._command_battery_index(parameters)
                if battery_index is not None:
                    last_phase_by_battery[battery_index] = phase_index
        self._sequence_last_phase_by_battery = last_phase_by_battery
        for battery_index in last_phase_by_battery:
            self._battery_desired_since[battery_index] = None
        if self._sequence_phases:
            self._sequence_step({"generation": generation, "index": 0})
        else:
            self._sequence_dispatch_complete = True
            self._complete_sequence()

    def _cancel_sequence(self, clear_queue=True) -> None:
        # AppDaemon-timers hoeven niet afzonderlijk geannuleerd te worden:
        # een oudere generatie wordt bij uitvoering veilig genegeerd.
        self._release_pending_deliveries()
        self._sequence_generation += 1
        self._sequence_active = False
        self._sequence_dispatch_complete = False
        self._sequence_source = None
        if clear_queue:
            self._queued_sequence = None
        self._sequence_phases = []
        self._sequence_last_phase_by_battery = {}
        self._last_sequence_finish_delay = 0.0
        self._last_atomic_request_id = None
        self._last_atomic_result_ok = None
        self._atomic_batch_pending = False
        self._atomic_request_battery_indexes = []

    def _complete_sequence(self) -> None:
        """Start pas na volledige verzending/read-back de nieuwste wachtrijtaak."""
        if not self._sequence_dispatch_complete or self._atomic_batch_pending:
            return
        self._sequence_active = False
        self._sequence_source = None
        queued = self._queued_sequence
        self._queued_sequence = None
        if queued is not None:
            commands, source = queued
            self._begin_sequence(commands, source)
        else:
            self._last_sequence_completed_at = time.monotonic()

    def recommended_verification_delay(self, minimum=0) -> int:
        """Geeft voldoende tijd voor de volledige opdrachtreeks en telemetrie."""
        return max(
            int(math.ceil(float(minimum))),
            int(math.ceil(self._last_sequence_finish_delay + 3.0)),
        )

    def last_atomic_request_id(self):
        """Geeft het ID van de laatst aangeboden volledige MarsTek-batch."""
        return self._last_atomic_request_id

    def atomic_batch_pending(self) -> bool:
        """Meldt of de nieuwste MarsTek-batch nog geen read-back heeft."""
        return self._atomic_batch_pending

    def command_sequence_pending(self) -> bool:
        """Meldt of een seriele opdracht nog loopt of in de wachtrij staat."""
        return bool(
            self._sequence_active
            or self._atomic_batch_pending
            or self._queued_sequence is not None
        )

    def verification_wait_remaining(self, settle_seconds=20) -> float:
        """Resterende rusttijd, gerekend vanaf de laatste volledige reeks."""
        if self.command_sequence_pending():
            return max(1.0, float(settle_seconds))
        if self._last_sequence_completed_at is None:
            return 0.0
        elapsed = time.monotonic() - self._last_sequence_completed_at
        return max(0.0, float(settle_seconds) - elapsed)

    def handle_atomic_result(self, data) -> bool:
        """Verwerk uitsluitend de read-back van de nieuwste MarsTek-batch."""
        if not isinstance(data, dict):
            return False
        request_id = data.get("request_id")
        if (
            request_id is None
            or str(request_id) != self._last_atomic_request_id
        ):
            return False
        self._atomic_batch_pending = False
        self._last_atomic_result_ok = bool(data.get("ok"))
        if not self._last_atomic_result_ok:
            self._pending_direction_handoff = None
            self._cancel_sequence(clear_queue=True)
            self.app.log(
                "Atomaire accu-opdracht niet bevestigd; "
                "runtime-bewaking herstelt de afwijkende accu gericht",
                level="ERROR",
            )
            return True
        confirmed_at = time.monotonic()
        for index in self._atomic_request_battery_indexes:
            self._battery_desired_since[index] = confirmed_at
        self._complete_sequence()
        return True

    def battery_delivery_ready(self, index, grace_seconds) -> bool:
        """Geeft elke accu dezelfde rusttijd na zijn eigen laatste write."""
        index = int(index)
        if index < 0 or index >= len(self._battery_desired_since):
            return False
        sent_at = self._battery_desired_since[index]
        return (
            sent_at is not None
            and time.monotonic() - sent_at >= float(grace_seconds)
        )

    def _release_pending_deliveries(self):
        """Laat een afgebroken reeks na een nieuwe volledige grace controleren."""
        cancelled_at = time.monotonic()
        for index, sent_at in enumerate(self._battery_desired_since):
            if sent_at is None:
                self._battery_desired_since[index] = cancelled_at

    def _build_power_commands(
        self,
        direction,
        powers,
        battery_indexes=None,
    ):
        """Bouwt de Marstek-volgorde: ontladen, laden, daarna Force Mode."""
        if direction not in {"charge", "discharge"}:
            raise ValueError(f"Ongeldige vermogensrichting: {direction}")

        indexes = self._battery_indexes(battery_indexes)
        if len(powers) != len(indexes):
            raise ValueError("Aantal herstelvermogens klopt niet met de accu's")

        if self.atomic_control:
            return self._build_atomic_power_commands(
                direction, powers, indexes
            )

        commands = []
        for index, power in zip(indexes, powers):
            battery = self.batteries[index]
            commands.extend([
                (
                    0,
                    "work_mode_one",
                    {
                        "entity_id": battery["work_mode"],
                        "option": "anti_feed",
                    },
                ),
                (
                    1,
                    "rs485_on_one",
                    {"entity_id": battery["rs485_control"]},
                ),
                (
                    2,
                    "number_value_one",
                    {
                        "entity_id": battery["discharge_power"],
                        "value": round(float(power))
                        if direction == "discharge" else 0,
                    },
                ),
                (
                    3,
                    "number_value_one",
                    {
                        "entity_id": battery["charge_power"],
                        "value": round(float(power))
                        if direction == "charge" else 0,
                    },
                ),
                (
                    4,
                    "force_mode_one",
                    {
                        "entity_id": battery["force_mode"],
                        "option": direction,
                    },
                ),
            ])
        return commands

    def _can_update_power_smoothly(self, state, powers):
        """Laat de centrale regisseur beslissen over een directe wijziging."""
        return self.mode_coordinator.can_update_smoothly(
            state, powers
        )

    def _build_smooth_power_update_commands(self, direction, powers):
        """Pas alleen gewijzigde accu's aan; laat de overige doorlopen."""
        if self.atomic_control:
            return self._build_setpoint_update_commands(direction, powers)

        steady_indexes = []
        starting_indexes = []
        stopping_indexes = []
        for index, (old, new) in enumerate(zip(
            self.desired_powers, powers
        )):
            if old > 0 and new > 0:
                steady_indexes.append(index)
            elif old <= 0 < new:
                starting_indexes.append(index)
            elif old > 0 and new <= 0:
                stopping_indexes.append(index)

        commands = []
        if steady_indexes:
            commands.extend(self._build_setpoint_update_commands(
                direction,
                [powers[index] for index in steady_indexes],
                steady_indexes,
            ))
        if starting_indexes:
            commands.extend(self._build_power_commands(
                direction,
                [powers[index] for index in starting_indexes],
                starting_indexes,
            ))
        if stopping_indexes:
            commands.extend(self._build_stop_commands(stopping_indexes))
        return commands

    def _build_setpoint_update_commands(
        self, direction, powers, battery_indexes=None
    ):
        """Wijzig het vermogen vloeiend; atomair blijft de opdracht compleet."""
        indexes = self._battery_indexes(battery_indexes)
        if self.atomic_control:
            return self._build_atomic_power_commands(
                direction,
                powers,
                indexes,
            )
        entity_key = (
            "charge_power" if direction == "charge"
            else "discharge_power"
        )
        return [
            (
                0,
                "number_value_one",
                {
                    "entity_id": battery[entity_key],
                    "value": round(float(power)),
                },
            )
            for index, power in zip(indexes, powers)
            for battery in [self.batteries[index]]
        ]

    def _build_stop_commands(self, battery_indexes=None):
        indexes = self._battery_indexes(battery_indexes)
        if self.atomic_control:
            return self._build_atomic_power_commands(
                "stop",
                [0.0 for _ in indexes],
                indexes,
            )
        commands = []
        for index in indexes:
            battery = self.batteries[index]
            commands.extend([
                (
                    0,
                    "work_mode_one",
                    {
                        "entity_id": battery["work_mode"],
                        "option": "anti_feed",
                    },
                ),
                (
                    1,
                    "rs485_on_one",
                    {"entity_id": battery["rs485_control"]},
                ),
                (
                    2,
                    "number_value_one",
                    {"entity_id": battery["discharge_power"], "value": 0},
                ),
                (
                    3,
                    "number_value_one",
                    {"entity_id": battery["charge_power"], "value": 0},
                ),
                (
                    4,
                    "force_mode_one",
                    {
                        "entity_id": battery["force_mode"],
                        "option": "standby",
                    },
                ),
            ])
        return commands

    def _build_isolated_stop_commands(
        self, battery_indexes=None, force_write=False
    ):
        """Zet ook de onderliggende native terugvalmodus veilig op handmatig."""
        indexes = self._battery_indexes(battery_indexes)
        if self.atomic_control:
            commands = []
            for index in indexes:
                commands.append(
                    (
                        0,
                        "work_mode_one",
                        {
                            "entity_id": self.batteries[index]["work_mode"],
                            "option": "manual",
                            "force_write": force_write,
                        },
                    )
                )
            commands.extend(
                (
                    1,
                    "atomic_power_one",
                    {
                        "battery": index + 1,
                        "charge_power": 0,
                        "discharge_power": 0,
                        "entity_id": self.batteries[index]["force_mode"],
                    },
                )
                for index in indexes
            )
            return commands
        commands = []
        for index in indexes:
            battery = self.batteries[index]
            commands.extend([
                (
                    0,
                    "work_mode_one",
                    {
                        "entity_id": battery["work_mode"],
                        "option": "manual",
                        "force_write": force_write,
                    },
                ),
                # Work Mode wijzigen kan RS485 uitschakelen. Schakel hem
                # daarom daarna altijd opnieuw in voor de echte Force Stop.
                (
                    1,
                    "rs485_on_one",
                    {
                        "entity_id": battery["rs485_control"],
                        "force_write": force_write,
                    },
                ),
                (
                    2,
                    "number_value_one",
                    {
                        "entity_id": battery["discharge_power"], "value": 0,
                        "force_write": force_write,
                    },
                ),
                (
                    3,
                    "number_value_one",
                    {
                        "entity_id": battery["charge_power"], "value": 0,
                        "force_write": force_write,
                    },
                ),
                (
                    4,
                    "force_mode_one",
                    {
                        "entity_id": battery["force_mode"],
                        "option": "standby",
                        "force_write": force_write,
                    },
                ),
            ])
        return commands

    def _build_atomic_power_commands(self, direction, powers, indexes):
        """Bouw één complete MarsTek-opdracht per accu zonder HA-registerfasen."""
        if direction not in {"charge", "discharge", "stop"}:
            raise ValueError(f"Ongeldige atomaire richting: {direction}")
        return [
            (
                0,
                "atomic_power_one",
                {
                    "battery": index + 1,
                    "charge_power": (
                        round(float(power)) if direction == "charge" else 0
                    ),
                    "discharge_power": (
                        round(float(power))
                        if direction == "discharge" else 0
                    ),
                    # Alleen voor interne accu-index- en grace-administratie.
                    "entity_id": self.batteries[index]["force_mode"],
                },
            )
            for index, power in zip(indexes, powers)
        ]

    def _build_native_control_commands(
        self, battery_indexes=None, force_write=False
    ):
        """Bouw native stappen die hun status pas bij uitvoering bepalen."""
        commands = []
        for index in self._battery_indexes(battery_indexes):
            battery = self.batteries[index]
            # Deze stap kan achter een export- of stopreeks in de wachtrij
            # komen. Beslis daarom niet nu op basis van een status die door de
            # voorgaande reeks nog kan veranderen.
            commands.append((
                0,
                "native_control_one",
                {
                    "entity_id": battery["work_mode"],
                    "work_mode_entity": battery["work_mode"],
                    "rs485_entity": battery["rs485_control"],
                    "force_write": force_write,
                },
            ))
        return commands

    def _battery_indexes(self, battery_indexes=None):
        if battery_indexes is None:
            return list(range(len(self.batteries)))

        result = []
        for value in battery_indexes:
            index = int(value)
            if index < 0 or index >= len(self.batteries):
                raise ValueError(f"Ongeldige accu-index: {index}")
            if index not in result:
                result.append(index)
        return result

    def _sequence_step(self, kwargs) -> None:
        if int(kwargs["generation"]) != self._sequence_generation:
            return

        index = int(kwargs["index"])
        if index >= len(self._sequence_phases):
            return
        touched_batteries = set()
        atomic_batch = []
        phase_sent = False
        for action, parameters in self._sequence_phases[index]:
            if action == "atomic_power_one":
                atomic_batch.append({
                    "battery": parameters["battery"],
                    "charge_power": parameters["charge_power"],
                    "discharge_power": parameters["discharge_power"],
                    "source": self._sequence_source or "onbekend",
                    "reason": (
                        "volledige atomaire controlleropdracht "
                        f"{self._sequence_source or 'onbekend'}"
                    ),
                    "entity_id": parameters["entity_id"],
                })
            else:
                phase_sent = bool(
                    self._dispatch_command(action, parameters)
                ) or phase_sent
            battery_index = self._command_battery_index(parameters)
            if battery_index is not None:
                touched_batteries.add(battery_index)
        if atomic_batch:
            phase_sent = True
            service_data = {"commands": atomic_batch}
            if self.coordinator is not None:
                request_id = (
                    f"{int(time.time() * 1000)}-"
                    f"{self._sequence_generation}-{index}"
                )
                self._last_atomic_request_id = request_id
                self._atomic_batch_pending = True
                self._atomic_request_battery_indexes = sorted(
                    touched_batteries
                )
                service_data["request_id"] = request_id
            self.app.call_service(
                "openems_m_panel/set_battery_powers",
                **service_data,
            )
            self.app.run_in(
                self._atomic_sequence_timeout,
                5,
                generation=self._sequence_generation,
                request_id=self._last_atomic_request_id,
            )
        if not atomic_batch:
            sent_at = time.monotonic()
            for battery_index in touched_batteries:
                if (
                    self._sequence_last_phase_by_battery.get(battery_index)
                    == index
                ):
                    self._battery_desired_since[battery_index] = sent_at

        next_index = index + 1
        if next_index >= len(self._sequence_phases):
            self._sequence_dispatch_complete = True
            self._complete_sequence()
            return
        next_kwargs = {
            "generation": self._sequence_generation,
            "index": next_index,
        }
        # Een volledig ongewijzigde fase heeft de accu niets te verwerken.
        # Bewaar de ingestelde pauze uitsluitend na een echte Modbus-write.
        if self.command_delay <= 0 or not phase_sent:
            self._sequence_step(next_kwargs)
        else:
            self.app.run_in(
                self._sequence_step,
                self.command_delay,
                **next_kwargs,
            )

    def _atomic_sequence_timeout(self, kwargs) -> None:
        """Voorkomt dat ontbrekende bridge-read-back de wachtrij blokkeert."""
        if (
            int(kwargs["generation"]) != self._sequence_generation
            or str(kwargs.get("request_id")) != self._last_atomic_request_id
            or not self._atomic_batch_pending
        ):
            return
        self._atomic_batch_pending = False
        self._last_atomic_result_ok = False
        self._release_pending_deliveries()
        self.app.log(
            "Atomaire read-back niet binnen 5 seconden ontvangen; "
            "nieuwste controlleropdracht wordt veilig vrijgegeven",
            level="WARNING",
        )
        self._complete_sequence()

    def _dispatch_command(self, action, parameters, source=None) -> bool:
        source = str(source or self._sequence_source or "onbekend")
        disabled_index = self._disabled_command_battery(action, parameters)
        if disabled_index is not None:
            # Harde blokkade: een in de instellingen uitgeschakelde accu
            # krijgt nooit een schrijfopdracht van OpenEMS.
            self._log_disabled_skip(disabled_index, action)
            return False
        if action == "native_control_one":
            work_mode_entity = parameters["work_mode_entity"]
            rs485_entity = parameters["rs485_entity"]
            force_write = bool(parameters.get("force_write"))
            work_mode = self._state_text(work_mode_entity)
            rs485 = self._state_text(rs485_entity)
            if work_mode != "anti_feed":
                return self._dispatch_command(
                    "work_mode_one",
                    {
                        "entity_id": work_mode_entity,
                        "option": "anti_feed",
                        "force_write": force_write,
                    },
                    source=source,
                )
            if rs485 != "off":
                return self._dispatch_command(
                    "rs485_off_one",
                    {
                        "entity_id": rs485_entity,
                        "force_write": force_write,
                    },
                    source=source,
                )
            return False
        entity_id = parameters.get("entity_id")
        old_value = self.app.get_state(entity_id) if entity_id else None
        new_value = self._command_target_value(action, parameters)
        if entity_id:
            force_write = bool(parameters.get("force_write"))
            unchanged = (
                not force_write
                and
                new_value is not None
                and self._same_command_value(old_value, new_value)
            )
            self._audit_direct_write(
                action, parameters, old_value, new_value, source,
                (
                    "service_overgeslagen_ongewijzigd"
                    if unchanged else "service_verzonden"
                ),
            )
            if unchanged:
                return False
        if action == "rs485_on_one":
            self.app.turn_on(parameters["entity_id"])
        elif action == "rs485_off_one":
            self.app.turn_off(parameters["entity_id"])
        elif action == "work_mode_one":
            self.app.call_service(
                "select/select_option",
                entity_id=parameters["entity_id"],
                option=parameters["option"],
            )
        elif action == "force_mode_one":
            self.app.call_service(
                "select/select_option",
                entity_id=parameters["entity_id"],
                option=parameters["option"],
            )
        elif action == "number_value_one":
            self.app.call_service(
                "number/set_value",
                entity_id=parameters["entity_id"],
                value=parameters["value"],
            )
        elif action == "atomic_power_one":
            self.app.call_service(
                "openems_m_panel/set_battery_power",
                battery=parameters["battery"],
                charge_power=parameters["charge_power"],
                discharge_power=parameters["discharge_power"],
            )
        else:
            raise ValueError(f"Onbekende controllerstap: {action}")
        return True

    @staticmethod
    def _command_target_value(action, parameters):
        if action == "rs485_on_one":
            return "on"
        if action == "rs485_off_one":
            return "off"
        if action in {"work_mode_one", "force_mode_one"}:
            return parameters.get("option")
        if action == "number_value_one":
            return parameters.get("value")
        return None

    @staticmethod
    def _same_command_value(old_value, new_value):
        try:
            return abs(float(old_value) - float(new_value)) < 0.5
        except (TypeError, ValueError):
            return str(old_value).strip().lower() == str(
                new_value
            ).strip().lower()

    def _audit_direct_write(
        self, action, parameters, old_value, new_value, source, status
    ):
        entity_id = parameters.get("entity_id")
        index = self._command_battery_index(parameters)
        battery = (
            self.batteries[index]["name"]
            if index is not None else "onbekend"
        )
        field = None
        if index is not None:
            for candidate in (
                "rs485_control", "work_mode", "force_mode",
                "charge_power", "discharge_power",
            ):
                if self.batteries[index].get(candidate) == entity_id:
                    field = candidate
                    break
        registers = {
            "rs485_control": 42000,
            "force_mode": 42010,
            "charge_power": 42020,
            "discharge_power": 42021,
            "work_mode": 43000,
        }
        message = (
            "OPENEMS_MODBUS_WRITE "
            f"time={datetime.now(timezone.utc).isoformat()} "
            f"battery={battery} register={registers.get(field)} "
            f"key={field or action} old={old_value!r} new={new_value!r} "
            f"source={source} reason=controlleractie_{action} "
            "writer=appdaemon.marstek_controller "
            f"status={status} entity={entity_id}"
        )
        self.app.log(message, level="WARNING")
        fire_event = getattr(self.app, "fire_event", None)
        if callable(fire_event):
            fire_event(
                "openems_modbus_write_audit",
                tijd=datetime.now(timezone.utc).isoformat(),
                batterij=battery,
                batterij_nummer=(index + 1 if index is not None else None),
                register=registers.get(field),
                sleutel=field or action,
                oude_waarde=old_value,
                nieuwe_waarde=new_value,
                bron=source,
                reden=f"controlleractie {action}",
                schrijver="appdaemon.marstek_controller",
                status=status,
                entity_id=entity_id,
            )

    def _command_battery_index(self, parameters):
        entity_id = parameters.get("entity_id")
        if not entity_id:
            return None
        for index, battery in enumerate(self.batteries):
            if entity_id in battery.values():
                return index
        return None

    def battery_disabled(self, index) -> bool:
        """True als de gebruiker deze accu in de instellingen heeft uitgezet."""
        checker = getattr(self.health_manager, "is_disabled", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(int(index)))
        except (TypeError, ValueError):
            return False

    def _disabled_command_battery(self, action, parameters):
        """Index van de uitgeschakelde accu waar deze stap op schrijft."""
        parameters = parameters or {}
        indexes = set()
        index = self._command_battery_index(parameters)
        if index is not None:
            indexes.add(index)
        for key in ("work_mode_entity", "rs485_entity"):
            entity_id = parameters.get(key)
            if not entity_id:
                continue
            for battery_index, battery in enumerate(self.batteries):
                if entity_id in battery.values():
                    indexes.add(battery_index)
        if action == "atomic_power_one" and parameters.get("battery"):
            try:
                indexes.add(int(parameters["battery"]) - 1)
            except (TypeError, ValueError):
                pass
        for battery_index in sorted(indexes):
            if self.battery_disabled(battery_index):
                return battery_index
        return None

    def _without_disabled_battery_commands(self, commands):
        """Haalt alle stappen voor uitgeschakelde accu's uit een reeks."""
        result = []
        for command in commands:
            action = command[1] if len(command) > 1 else None
            parameters = command[2] if len(command) > 2 else {}
            disabled_index = self._disabled_command_battery(
                action, parameters
            )
            if disabled_index is not None:
                self._log_disabled_skip(disabled_index, action)
                continue
            result.append(command)
        return result

    def _log_disabled_skip(self, index, action) -> None:
        now = time.monotonic()
        last = getattr(self, "_disabled_skip_log_at", None)
        if last is None:
            last = self._disabled_skip_log_at = {}
        if now - last.get(index, 0.0) < 60.0:
            return
        last[index] = now
        self.app.log(
            f"{self.batteries[index]['name']} is uitgeschakeld in OpenEMS: "
            f"opdracht '{action}' niet verstuurd (accu blijft met rust)"
        )

    def _normalize_powers(self, powers: Sequence[float], max_key: str):
        if len(powers) != len(self.batteries):
            raise ValueError(
                "Aantal vermogens komt niet overeen met het aantal accu's"
            )

        result = []
        for battery, requested in zip(self.batteries, powers):
            maximum = self._read_positive_number(battery[max_key])
            result.append(min(max(0.0, float(requested)), maximum))
        return result

    def _read_positive_number(self, entity_id: str) -> float:
        state = self.app.get_state(entity_id)
        if state in (None, "", "unknown", "unavailable"):
            raise ValueError(f"Entiteit niet beschikbaar: {entity_id}")
        return max(0.0, float(state))

    @staticmethod
    def _power_matches(actual, expected):
        """Omnibattery-tolerantie: minimaal 100 W of 10 procent."""
        return abs(actual - expected) <= max(100.0, abs(expected) * 0.10)

    @staticmethod
    def _physical_delivery_started(actual, expected, direction):
        """Accepteer een opdracht pas zodra de accu fysiek vermogen levert."""
        if actual is None:
            # Installaties zonder werkelijke-vermogenssensor blijven bruikbaar.
            return True
        threshold = max(100.0, abs(float(expected)) * 0.10)
        if direction == "charge":
            return float(actual) <= -threshold
        if direction == "discharge":
            return float(actual) >= threshold
        raise ValueError(f"Ongeldige leveringsrichting: {direction}")

    def _battery_at_discharge_cutoff(self, battery):
        """Geldt afzonderlijk voor iedere accu met SOC van 13% of lager."""
        soc_entity = battery.get("soc")
        soc = self._state_number(soc_entity) if soc_entity else None
        return soc is not None and soc <= BMS_DISCHARGE_CUTOFF_SOC

    def _can_execute(self, action: str, powers=None, source="auto") -> bool:
        grid_guard = self.grid_status_monitor
        if grid_guard is not None and not grid_guard.allows_controller_action(
            action, source
        ):
            self.app.log(
                f"Controlleropdracht geblokkeerd door netstatus: {action} "
                f"({grid_guard.status_reason()})",
                level="WARNING",
            )
            return False
        if self._release_blocks(source):
            return False
        if source in {"auto", "export"}:
            allowed = self.enabled
            setting = "AUTO_CONTROL_ENABLED"
        elif source == "manual":
            allowed = self.test_enabled and not self.enabled
            setting = "handmatige modus"
        elif source == "coordinated":
            allowed = self.enabled or self.test_enabled
            setting = "OpenEMS zelfconsumptie"
        elif source == "selector":
            allowed = self.enabled or self.test_enabled
            setting = "efficiënte accuselector"
        elif source == "recovery":
            allowed = self.test_enabled
            setting = "veilig herstart-herstel"
        else:
            allowed = self.test_enabled and source == "test"
            setting = "TEST_CONTROL_ENABLED"
        if allowed:
            return True

        details = ""
        if powers is not None:
            details = " | " + ", ".join(f"{value:.0f} W" for value in powers)

        self.app.log(
            f"SIMULATIE controller: {action}{details} "
            f"({setting} staat uit)"
        )
        return False

    def _is_duplicate(self, signature) -> bool:
        if signature != self._last_command:
            return False
        now = time.monotonic()
        if now - self._last_duplicate_log_at >= self._duplicate_log_interval:
            self.app.log("Controller: identieke opdracht overgeslagen")
            self._last_duplicate_log_at = now
        return True

    def invalidate_last_command(self, reason="verificatie mislukt") -> None:
        """Maakt een echte herhaling mogelijk na een mislukte verificatie."""
        self._last_command = None
        self.app.log(
            f"Controller: opdrachtcache gewist ({reason}); herhaling toegestaan",
            level="WARNING",
        )

    def _deactivate_coordinator_for(self, source):
        if source != "coordinated" and self.coordinator is not None:
            self.coordinator.deactivate()

    def _remember_desired(self, state, powers, source="auto"):
        self.desired_state = state
        self.desired_powers = list(powers)
        self.desired_source = source
        self.desired_since = time.monotonic()
        self.mode_coordinator.record(source, state, powers)
        checkpoint = getattr(self.app, "runtime_checkpoint", None)
        if checkpoint is not None:
            checkpoint.capture()

    def _health_filtered_powers(self, powers):
        if self.health_manager is None:
            return list(powers)
        return self.health_manager.filter_powers(powers)

    def _prepare_explicit_manual_control(self, source):
        if source != "manual" or self.health_manager is None:
            return
        release = getattr(
            self.health_manager, "release_manual_selector_quarantines", None
        )
        if not callable(release):
            return
        released = release()
        if released:
            names = ", ".join(
                self.batteries[index].get("name", f"Accu {index + 1}")
                for index in released
            )
            self.app.log(
                "Handmatige bediening: oude softwarematige "
                f"selectoruitsluiting opgeheven voor {names}",
                level="WARNING",
            )

    def _pause(self) -> None:
        # AppDaemon service-opdrachten worden in de aangeroepen volgorde verstuurd.
        # self.sleep() is in recente AppDaemon-versies asynchroon en mag hier niet
        # zonder await worden gebruikt. De waarde blijft in config gereserveerd
        # voor een latere niet-blokkerende command queue.
        return
