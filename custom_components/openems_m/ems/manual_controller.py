# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Veilige handmatige bediening wanneer OpenEMS niet in Auto staat."""

from datetime import datetime, timedelta, timezone

from .ems_config import (
    BMS_DISCHARGE_CUTOFF_SOC,
    TARGET_SOC_RELEASE_HYSTERESIS_PERCENT,
)
from .models import SchedulerState

# Een volle accu kan niets opnemen; zijn aandeel gaat naar de andere accu's.
# Pas onder deze grens doet hij weer mee, zodat hij niet blijft wisselen.
FULL_SOC_PERCENT = 100.0
FULL_RELEASE_SOC_PERCENT = FULL_SOC_PERCENT - TARGET_SOC_RELEASE_HYSTERESIS_PERCENT
SOC_REAPPLY_RETRY_SECONDS = 5


class ManualController:
    DEVICE_RESET_WAIT_SECONDS = 15
    DEVICE_RESET_POLL_SECONDS = 5
    DEVICE_RESET_MAX_WAIT_SECONDS = 90
    DIRECTION_RECHECK_SECONDS = 1

    ACTIONS = {
        "Stop": SchedulerState.STOPPED,
        "Laden": SchedulerState.CHARGING,
        "Ontladen": SchedulerState.DISCHARGING,
        "Zelfconsumptie": SchedulerState.SELF_CONSUMPTION,
    }

    def __init__(
        self,
        app,
        controller,
        enabled_helper,
        mode_helper,
        charge_power_helper,
        discharge_power_helper,
        test_mode_helper,
        battery_count,
        distribution_helper=None,
        excluded_helpers=None,
        verification_delay=8,
        status_recheck_delay=1,
        status_rechecks=3,
    ):
        self.app = app
        self.controller = controller
        self.enabled_helper = enabled_helper
        self.mode_helper = mode_helper
        self.charge_power_helper = charge_power_helper
        self.discharge_power_helper = discharge_power_helper
        self.test_mode_helper = test_mode_helper
        self.battery_count = battery_count
        # Met een verdelingshelper zijn de vermogens TOTAAL-waarden voor de
        # aangevinkte accu's; zonder blijft het oude vermogen per accu gelden.
        self.distribution_helper = distribution_helper
        self.excluded_helpers = list(excluded_helpers or [])
        self.max_power = 8000.0 if distribution_helper else 2500.0
        self.verification_delay = max(1, int(verification_delay))
        self.status_recheck_delay = max(1, int(status_recheck_delay))
        self.status_rechecks = max(0, int(status_rechecks))
        self._generation = 0
        self._busy = False
        # Accu's die bij de laatste verdeling wegens SOC 0 W kregen.
        self._soc_blocked = set()
        self._soc_blocked_direction = None
        batteries = getattr(self.controller, "batteries", [])
        self._battery_statuses = [
            {
                "naam": (
                    batteries[index].get("name", f"Accu {index + 1}")
                    if index < len(batteries)
                    else f"Accu {index + 1}"
                ),
                "status": "ONBEKEND",
                "details": "Nog niet gecontroleerd",
            }
            for index in range(self.battery_count)
        ]

    def start(self):
        self.app.listen_state(self._changed, self.mode_helper)
        self.app.listen_state(self._changed, self.charge_power_helper)
        self.app.listen_state(self._changed, self.discharge_power_helper)
        for helper in [self.distribution_helper, *self.excluded_helpers]:
            if helper:
                self.app.listen_state(self._changed, helper)
        if self.distribution_helper:
            for battery in getattr(self.controller, "batteries", []):
                soc_entity = battery.get("soc")
                if soc_entity:
                    self.app.listen_state(self._soc_changed, soc_entity)
        if (
            not self._auto_enabled()
            and (self.app.get_state(self.mode_helper) or "Stop") == "Stop"
        ):
            # Na een herstart in Stop blijft OpenEMS van de accu's af.
            release = getattr(self.controller, "release_control", None)
            if callable(release):
                release(source="manual")
        if self._auto_enabled():
            self.publish("Auto", "Automatische planning bestuurt de accu's")
        else:
            self.publish("Gereed", "Nog geen handmatige opdracht uitgevoerd")

    def apply_current(self, kwargs=None):
        if self._busy or self._auto_enabled():
            return
        if self.app.get_state(self.test_mode_helper) not in (
            None, "", "unknown", "unavailable", "Uit"
        ):
            self.publish(
                "Geblokkeerd",
                "De technische controller-testmodus is nog actief",
                level="WARNING",
            )
            return

        mode = self.app.get_state(self.mode_helper) or "Stop"
        desired_state = self.ACTIONS.get(mode)
        if desired_state is None:
            self.publish("Fout", f"Onbekende handmatige modus: {mode}", "ERROR")
            return

        charge_power = self._power(self.charge_power_helper)
        discharge_power = self._power(self.discharge_power_helper)
        if desired_state in {
            SchedulerState.CHARGING, SchedulerState.DISCHARGING
        }:
            opposite = self._opposite_direction_batteries(desired_state)
            if opposite:
                self._start_direction_handoff(mode, opposite)
                return
        expected_powers = []
        selection = None
        self._busy = True
        try:
            if desired_state in {
                SchedulerState.CHARGING, SchedulerState.DISCHARGING
            } and self.distribution_helper:
                direction = (
                    "charge"
                    if desired_state == SchedulerState.CHARGING
                    else "discharge"
                )
                total = (
                    charge_power
                    if desired_state == SchedulerState.CHARGING
                    else discharge_power
                )
                distributed = self.distribute(direction, total)
                if distributed is None:
                    self.controller.set_stop(source="manual")
                    self.publish(
                        "Geblokkeerd",
                        "Geen accu geselecteerd voor handmatig "
                        f"{'laden' if direction == 'charge' else 'ontladen'}",
                        level="WARNING",
                    )
                    return
                expected_powers = distributed
                if direction == "charge":
                    success = self.controller.set_charge(
                        expected_powers, source="manual"
                    )
                else:
                    success = self.controller.set_discharge(
                        expected_powers, source="manual"
                    )
                if success:
                    expected_powers = list(getattr(
                        self.controller, "desired_powers", expected_powers
                    ))
            elif desired_state == SchedulerState.CHARGING:
                expected_powers = [charge_power] * self.battery_count
                success = self.controller.set_charge(
                    expected_powers, source="manual"
                )
                if success:
                    expected_powers = list(getattr(
                        self.controller, "desired_powers", expected_powers
                    ))
            elif desired_state == SchedulerState.DISCHARGING:
                expected_powers = [discharge_power] * self.battery_count
                success = self.controller.set_discharge(
                    expected_powers, source="manual"
                )
                if success:
                    expected_powers = list(getattr(
                        self.controller, "desired_powers", expected_powers
                    ))
            elif desired_state == SchedulerState.SELF_CONSUMPTION:
                chosen = (
                    self.participants() if self.excluded_helpers
                    else list(range(self.battery_count))
                )
                if not chosen:
                    self.controller.set_stop(source="manual")
                    self.publish(
                        "Geblokkeerd",
                        "Geen accu geselecteerd voor handmatige "
                        "zelfconsumptie",
                        level="WARNING",
                    )
                    return
                if len(chosen) < self.battery_count:
                    selection = chosen
                    success = (
                        self.controller
                        .set_native_self_consumption_selection(
                            chosen, source="manual"
                        )
                    )
                else:
                    success = self.controller.set_self_consumption(
                        source="manual"
                    )
            else:
                success = self.controller.set_stop(source="manual")

            if not success:
                self.publish("Fout", f"Handmatige opdracht {mode} geweigerd", "ERROR")
                return

            self._set_battery_statuses(
                range(self.battery_count),
                "CONTROLEREN",
                "Opdracht verstuurd; wacht op status",
            )
            verification_delay = self._verification_delay()
            verification_at = (
                datetime.now(timezone.utc)
                + timedelta(seconds=verification_delay)
            ).isoformat()
            self.publish(
                mode,
                f"Opdracht uitgevoerd; verificatie over {verification_delay} seconden",
                charge_power=charge_power,
                discharge_power=discharge_power,
                verificatie_op=verification_at,
            )
            self._generation += 1
            if desired_state in {
                SchedulerState.CHARGING, SchedulerState.DISCHARGING
            }:
                self.app.run_in(
                    self._direction_watchdog,
                    self.DIRECTION_RECHECK_SECONDS,
                    generation=self._generation,
                    state=desired_state.value,
                    mode=mode,
                )
            self.app.run_in(
                self._verify,
                verification_delay,
                generation=self._generation,
                state=(
                    "forced_discharge"
                    if desired_state == SchedulerState.DISCHARGING
                    else desired_state.value
                ),
                mode=mode,
                expected_powers=expected_powers,
                battery_indexes=selection,
            )
        except (TypeError, ValueError, KeyError) as error:
            self.publish("Fout", f"Handmatige bediening mislukt: {error}", "ERROR")
        finally:
            self._busy = False

    def _start_direction_handoff(self, mode, opposite):
        self._generation += 1
        generation = self._generation
        self.controller.set_stop(source="manual")
        self.publish(
            "Omschakelen",
            "Eerst alle accu's stoppen; tegengestelde richting actief op "
            + ", ".join(opposite),
            level="WARNING",
        )
        self.app.run_in(
            self._retry_direction_handoff,
            self.DIRECTION_RECHECK_SECONDS,
            generation=generation,
            mode=mode,
        )

    def _retry_direction_handoff(self, kwargs):
        if int(kwargs["generation"]) != self._generation or self._auto_enabled():
            return
        pending = getattr(self.controller, "command_sequence_pending", None)
        if callable(pending) and pending():
            self.app.run_in(
                self._retry_direction_handoff,
                self.DIRECTION_RECHECK_SECONDS,
                **kwargs,
            )
            return
        mode = self.app.get_state(self.mode_helper) or "Stop"
        desired_state = self.ACTIONS.get(mode)
        if desired_state not in {
            SchedulerState.CHARGING, SchedulerState.DISCHARGING
        }:
            self.apply_current()
            return
        opposite = self._opposite_direction_batteries(desired_state)
        if opposite:
            self.publish(
                "Geblokkeerd",
                "Tegengestelde richting is nog niet gestopt op "
                + ", ".join(opposite),
                level="ERROR",
            )
            self.app.run_in(
                self._retry_direction_handoff,
                self.DIRECTION_RECHECK_SECONDS,
                **kwargs,
            )
            return
        self.apply_current()

    def _direction_watchdog(self, kwargs):
        if int(kwargs["generation"]) != self._generation or self._auto_enabled():
            return
        desired_state = self.ACTIONS.get(kwargs.get("mode"))
        opposite = self._opposite_direction_batteries(desired_state)
        if not opposite:
            return
        self._generation += 1
        self.controller.set_stop(source="manual")
        self.publish(
            "Noodstop",
            "Gelijktijdig laden en ontladen voorkomen; gestopt vanwege "
            + ", ".join(opposite),
            level="ERROR",
        )

    def _opposite_direction_batteries(self, desired_state):
        opposite = []
        disabled = getattr(self.controller, "battery_disabled", None)
        for index, battery in enumerate(
            getattr(self.controller, "batteries", [])
        ):
            if callable(disabled) and disabled(index):
                continue
            actual = self._state_number(battery.get("actual_power"))
            charge = self._state_number(battery.get("charge_power"))
            discharge = self._state_number(battery.get("discharge_power"))
            force = str(
                self.app.get_state(battery.get("force_mode")) or ""
            ).lower()
            conflict = (
                desired_state == SchedulerState.DISCHARGING
                and (
                    (actual is not None and actual < -100)
                    or (charge is not None and charge > 100)
                    or force == "charge"
                )
            ) or (
                desired_state == SchedulerState.CHARGING
                and (
                    (actual is not None and actual > 100)
                    or (discharge is not None and discharge > 100)
                    or force == "discharge"
                )
            )
            if conflict:
                opposite.append(
                    battery.get("name", f"Accu {index + 1}")
                )
        return opposite

    def _state_number(self, entity):
        if not entity:
            return None
        value = self.app.get_state(entity)
        if value in (None, "", "unknown", "unavailable"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def publish(self, state, message, level="INFO", **attributes):
        self.app.set_state(
            "sensor.openems_handmatige_status",
            state=state,
            attributes={
                "friendly_name": "OpenEMS handmatige status",
                "icon": "mdi:hand-back-right",
                "melding": message,
                "actie": message,
                "niveau": level,
                "accu_statussen": [
                    dict(status) for status in self._battery_statuses
                ],
                **attributes,
            },
            replace=True,
        )
        self.app.log(f"[Handmatig] {message}", level=level)

    def _changed(self, entity, attribute, old, new, kwargs):
        if old == new or self._auto_enabled():
            return
        mode = self.app.get_state(self.mode_helper)
        if entity == self.charge_power_helper and mode != "Laden":
            return
        if entity == self.discharge_power_helper and mode != "Ontladen":
            return
        if entity == self.distribution_helper and mode not in (
            "Laden", "Ontladen"
        ):
            return
        if entity in self.excluded_helpers and mode not in (
            "Laden", "Ontladen", "Zelfconsumptie"
        ):
            return
        self._generation += 1
        self.app.run_in(
            self._debounced_apply,
            1,
            generation=self._generation,
        )

    def _release_after_stop(self, kwargs):
        """Laat de accu's los zodra de stopopdracht is gecontroleerd."""
        if kwargs.get("state") != "stopped" or kwargs.get("mode") != "Stop":
            return
        release = getattr(self.controller, "release_control", None)
        if callable(release):
            release(source="manual")
            self.publish(
                "Stop",
                "Accu's gestopt en losgelaten; OpenEMS stuurt niet meer aan "
                "tot je de modus wijzigt",
                verificatie="LOSGELATEN",
            )

    def _debounced_apply(self, kwargs):
        if int(kwargs["generation"]) == self._generation:
            self.apply_current()

    def _verify(self, kwargs):
        if int(kwargs["generation"]) != self._generation or self._auto_enabled():
            return
        if self._wait_for_completed_command(self._verify, kwargs):
            return
        extra = {}
        if kwargs.get("battery_indexes") is not None:
            extra["battery_indexes"] = kwargs["battery_indexes"]
        result = self.controller.verify_state(
            kwargs["state"],
            expected_powers=self._current_expected(kwargs),
            **extra,
        )
        if result.get("pending"):
            self.publish(
                "Controleren",
                "Opdracht wordt nog uitgevoerd; verificatie wacht zonder "
                "herstelpoging",
                verificatie="WACHT OP CONTROLLER",
            )
            self.app.run_in(
                self._verify,
                self.status_recheck_delay,
                **kwargs,
            )
            return
        self._record_verification(result, "WACHT OP STATUS")
        if result["ok"]:
            self.publish(
                kwargs["mode"],
                result["summary"],
                verificatie="OK",
            )
            self._release_after_stop(kwargs)
            return

        recheck = int(kwargs.get("recheck", 0))
        if recheck < self.status_rechecks:
            next_recheck = recheck + 1
            self.publish(
                "Controleren",
                "Status nog niet bevestigd; hercontrole "
                f"{next_recheck}/{self.status_rechecks} over "
                f"{self.status_recheck_delay} seconde",
                level="WARNING",
                verificatie="WACHT OP STATUS",
            )
            self.app.run_in(
                self._verify,
                self.status_recheck_delay,
                **dict(kwargs, recheck=next_recheck),
            )
            return

        if kwargs.get("state") == "stopped":
            # Bij Stop alleen controleren; geen herstelpogingen of resets.
            self.publish(
                kwargs["mode"],
                "Stop verstuurd, maar niet alle accu's meldden stand-by: "
                f"{result['summary']}. OpenEMS stuurt niet verder aan.",
                level="WARNING",
                verificatie="NIET BEVESTIGD",
            )
            self._release_after_stop(kwargs)
            return

        failed_indexes = self._failed_indexes(result)
        self._set_battery_statuses(
            failed_indexes,
            "HERSTELLEN",
            "Dezelfde gewenste opdracht wordt opnieuw gestuurd",
        )
        if not self.controller.restore_batteries(
            kwargs["state"],
            failed_indexes,
            expected_powers=self._current_expected(kwargs),
            source="manual",
        ):
            self._set_battery_statuses(
                failed_indexes,
                "FOUT",
                "Herhaalopdracht geweigerd",
            )
            self._publish_final_error(
                "Herstel niet gestart: herhaalopdracht geweigerd",
                result["summary"],
            )
            return

        names = self._battery_names(failed_indexes)
        self.publish(
            "Herstellen",
            f"{names}: status blijft afwijken; dezelfde opdracht opnieuw "
            "verstuurd",
            level="WARNING",
            verificatie="HERHAALOPDRACHT",
        )
        self.app.run_in(
            self._verify_recovery_target,
            self._recovery_delay(),
            generation=kwargs["generation"],
            state=kwargs["state"],
            mode=kwargs["mode"],
            expected_powers=self._current_expected(kwargs),
            failed_indexes=failed_indexes,
        )

    def _verify_recovery_target(self, kwargs):
        if not self._callback_is_current(kwargs):
            return
        if self._wait_for_completed_command(
            self._verify_recovery_target, kwargs
        ):
            return
        indexes = list(kwargs["failed_indexes"])
        result = self.controller.verify_state(
            kwargs["state"],
            expected_powers=self._current_expected(kwargs),
            battery_indexes=indexes,
        )
        if result.get("pending"):
            self.app.run_in(
                self._verify_recovery_target,
                self.status_recheck_delay,
                **kwargs,
            )
            return
        self._record_verification(result, "HERSTELLEN")
        if result["ok"]:
            self.publish(
                kwargs["mode"],
                f"Herstel geslaagd | {result['summary']}",
                verificatie="OK NA HERSTEL",
            )
            return

        still_failed = self._failed_indexes(result, fallback=indexes)
        if self._start_device_reset_recovery(kwargs, still_failed):
            return
        self._start_final_stop(kwargs, still_failed, result)

    def _start_device_reset_recovery(self, kwargs, indexes):
        requester = getattr(self.controller, "request_device_reset", None)
        pending = getattr(self.controller, "device_reset_pending", None)
        if not callable(requester):
            return False
        reset_indexes = []
        externally_owned = []
        for index in indexes:
            if callable(pending) and pending(index):
                externally_owned.append(index)
            elif requester(index, source="manual"):
                reset_indexes.append(index)
        if not reset_indexes and not externally_owned:
            return False

        if reset_indexes:
            self._set_battery_statuses(
                reset_indexes,
                "RESETTEN",
                "Eenmalige apparaat-reset uitgevoerd; wacht op terugkeer",
            )
            self.publish(
                "Resetten",
                f"{self._battery_names(reset_indexes)}: eenmalige "
                f"apparaat-reset; wacht "
                f"{self.DEVICE_RESET_WAIT_SECONDS} seconden",
                level="WARNING",
                verificatie="APPARAAT-RESET",
            )
            self.app.run_in(
                self._retry_after_device_reset,
                self.DEVICE_RESET_WAIT_SECONDS,
                generation=kwargs["generation"],
                state=kwargs["state"],
                mode=kwargs["mode"],
                expected_powers=self._current_expected(kwargs),
                reset_indexes=reset_indexes,
                waited=self.DEVICE_RESET_WAIT_SECONDS,
            )
        if externally_owned:
            # De runtimebewaking heeft de reset al gestart. Observeer alleen;
            # stuur geen tweede reset of herhaalopdracht vanuit de handmatige
            # route.
            self._set_battery_statuses(
                externally_owned,
                "WACHT OP RESET",
                "Centrale herstelroute behandelt deze accu",
            )
            self.app.run_in(
                self._wait_for_external_device_reset,
                self.DEVICE_RESET_POLL_SECONDS,
                generation=kwargs["generation"],
                state=kwargs["state"],
                mode=kwargs["mode"],
                expected_powers=self._current_expected(kwargs),
                reset_indexes=externally_owned,
                waited=self.DEVICE_RESET_POLL_SECONDS,
            )
        return True

    def _wait_for_external_device_reset(self, kwargs):
        if not self._callback_is_current(kwargs):
            return
        pending = getattr(self.controller, "device_reset_pending", None)
        indexes = list(kwargs["reset_indexes"])
        still_pending = (
            [index for index in indexes if pending(index)]
            if callable(pending) else []
        )
        waited = int(kwargs.get("waited", 0))
        if still_pending and waited < self.DEVICE_RESET_MAX_WAIT_SECONDS:
            self.app.run_in(
                self._wait_for_external_device_reset,
                self.DEVICE_RESET_POLL_SECONDS,
                **dict(
                    kwargs,
                    reset_indexes=still_pending,
                    waited=waited + self.DEVICE_RESET_POLL_SECONDS,
                ),
            )
            return
        result = self.controller.verify_state(
            kwargs["state"],
            expected_powers=self._current_expected(kwargs),
            battery_indexes=indexes,
        )
        if result.get("ok"):
            self.publish(
                kwargs["mode"],
                f"Centraal resetherstel geslaagd | {result['summary']}",
                verificatie="OK NA APPARAAT-RESET",
            )
            return
        self._start_final_stop(
            kwargs,
            self._failed_indexes(result, fallback=indexes),
            result,
        )

    def _retry_after_device_reset(self, kwargs):
        if not self._callback_is_current(kwargs):
            self._complete_device_resets(
                kwargs["reset_indexes"], recovered=False
            )
            return
        guard = getattr(self.app, "telemetry_guard", None)
        if guard is not None:
            freshness = guard.check(include_grid=False)
            reset_set = set(int(value) for value in kwargs["reset_indexes"])
            unavailable = any(
                item.get("accu_index") in reset_set
                for item in freshness["problems"]
            )
            if unavailable:
                waited = int(kwargs.get("waited", 0))
                if waited < self.DEVICE_RESET_MAX_WAIT_SECONDS:
                    self.app.run_in(
                        self._retry_after_device_reset,
                        self.DEVICE_RESET_POLL_SECONDS,
                        **dict(
                            kwargs,
                            waited=waited + self.DEVICE_RESET_POLL_SECONDS,
                        ),
                    )
                    return
                self._complete_device_resets(
                    kwargs["reset_indexes"], recovered=False
                )
                result = {
                    "summary": "Geen verse accutelemetrie binnen 90 seconden"
                }
                self._start_final_stop(
                    kwargs, list(kwargs["reset_indexes"]), result
                )
                return

        indexes = list(kwargs["reset_indexes"])
        if not self.controller.restore_batteries(
            kwargs["state"],
            indexes,
            expected_powers=self._current_expected(kwargs),
            source="manual",
        ):
            self._complete_device_resets(indexes, recovered=False)
            self._start_final_stop(
                kwargs, indexes,
                {"summary": "Opdracht na apparaat-reset werd geweigerd"},
            )
            return
        self._set_battery_statuses(
            indexes,
            "CONTROLEREN NA RESET",
            "Gewenste opdracht eenmaal opnieuw verstuurd",
        )
        self.app.run_in(
            self._verify_device_reset_target,
            self._recovery_delay(),
            **kwargs,
        )

    def _verify_device_reset_target(self, kwargs):
        if not self._callback_is_current(kwargs):
            self._complete_device_resets(
                kwargs["reset_indexes"], recovered=False
            )
            return
        if self._wait_for_completed_command(
            self._verify_device_reset_target, kwargs
        ):
            return
        indexes = list(kwargs["reset_indexes"])
        result = self.controller.verify_state(
            kwargs["state"],
            expected_powers=self._current_expected(kwargs),
            battery_indexes=indexes,
        )
        if result.get("pending"):
            self.app.run_in(
                self._verify_device_reset_target,
                self.status_recheck_delay,
                **kwargs,
            )
            return
        self._record_verification(result, "FOUT NA RESET")
        if result["ok"]:
            self._complete_device_resets(indexes, recovered=True)
            self.publish(
                kwargs["mode"],
                f"Herstel na apparaat-reset geslaagd | {result['summary']}",
                verificatie="OK NA APPARAAT-RESET",
            )
            return

        still_failed = self._failed_indexes(result, fallback=indexes)
        failed = set(still_failed)
        for index in indexes:
            self._complete_device_resets(
                [index], recovered=index not in failed
            )
        self._start_final_stop(kwargs, still_failed, result)

    def _complete_device_resets(self, indexes, recovered):
        completer = getattr(self.controller, "complete_device_reset", None)
        if callable(completer):
            for index in indexes:
                completer(index, recovered=recovered)

    def _start_final_stop(self, kwargs, still_failed, result):
        self._set_battery_statuses(
            still_failed,
            "VEILIG STOPPEN",
            "Herstel mislukt; definitieve Stop wordt uitgevoerd",
        )
        if not self.controller.stop_batteries(still_failed, source="manual"):
            self._set_battery_statuses(
                still_failed,
                "FOUT",
                "Definitieve Stop-opdracht geweigerd",
            )
            self._publish_final_error(
                "Herstel mislukt en definitieve Stop werd geweigerd",
                result["summary"],
            )
            return

        self.publish(
            "Veilig stoppen",
            f"{self._battery_names(still_failed)}: herstel mislukt; "
            "definitieve Stop gestart",
            level="WARNING",
            verificatie="DEFINITIEVE STOP",
        )
        self.app.run_in(
            self._verify_final_stop,
            self._recovery_delay(),
            generation=kwargs["generation"],
            failed_indexes=still_failed,
        )

    def _verify_final_stop(self, kwargs):
        if not self._callback_is_current(kwargs):
            return
        if self._wait_for_completed_command(
            self._verify_final_stop, kwargs
        ):
            return
        indexes = list(kwargs["failed_indexes"])
        result = self.controller.verify_state(
            "stopped",
            battery_indexes=indexes,
        )
        if result.get("pending"):
            self.app.run_in(
                self._verify_final_stop,
                self.status_recheck_delay,
                **kwargs,
            )
            return
        if result["ok"]:
            self._set_battery_statuses(
                indexes,
                "FOUT - VEILIG GESTOPT",
                "Herstel beëindigd; accu blijft gestopt",
            )
            message = (
                f"{self._battery_names(indexes)} veilig gestopt en wordt "
                "niet verder automatisch hersteld"
            )
        else:
            self._record_verification(result, "FOUT - STOP NIET BEVESTIGD")
            message = "Definitieve Stop kon niet worden bevestigd"
        self._publish_final_error(message, result["summary"])

    def _current_expected(self, kwargs):
        """Actueel doel van de controller, inclusief load-balancerbegrenzing.

        De load balancer past het vermogen na de handmatige opdracht vloeiend
        aan. Controle en herhaalopdracht moeten dat actuele doel gebruiken en
        niet het vermogen van het moment waarop de opdracht werd gegeven.
        """
        stored = list(kwargs.get("expected_powers") or [])
        state = kwargs.get("state")
        if state not in ("charging", "forced_discharge"):
            return stored
        desired_state = getattr(self.controller, "desired_state", None)
        desired_state = getattr(desired_state, "value", desired_state)
        if (
            desired_state != state
            or getattr(self.controller, "desired_source", None) != "manual"
        ):
            return stored
        current = getattr(self.controller, "desired_powers", None)
        if not isinstance(current, (list, tuple)) or not current:
            return stored
        return [float(power) for power in current]

    def _callback_is_current(self, kwargs):
        return (
            int(kwargs["generation"]) == self._generation
            and not self._auto_enabled()
        )

    def _wait_for_completed_command(self, callback, kwargs):
        """Wacht twintig seconden vanaf de werkelijk voltooide reeks."""
        resolver = getattr(
            self.controller, "verification_wait_remaining", None
        )
        if not callable(resolver):
            return False
        remaining = float(resolver(self.verification_delay))
        if remaining <= 0:
            return False
        if not kwargs.get("completion_wait_reported"):
            self.publish(
                "Controleren",
                "Wacht op volledige uitvoering en verwerking door alle accu's",
                verificatie="WACHT OP ACCU'S",
            )
        self.app.run_in(
            callback,
            max(1, int(remaining + 0.999)),
            **dict(kwargs, completion_wait_reported=True),
        )
        return True

    def _failed_indexes(self, result, fallback=None):
        indexes = result.get("failed_indexes")
        if indexes is None:
            indexes = fallback if fallback is not None else range(self.battery_count)
        return [int(index) for index in indexes]

    def _battery_names(self, indexes):
        names = []
        batteries = getattr(self.controller, "batteries", [])
        for index in indexes:
            if index < len(batteries):
                names.append(batteries[index].get("name", f"Accu {index + 1}"))
            else:
                names.append(f"Accu {index + 1}")
        return ", ".join(names)

    def _record_verification(self, result, failed_status):
        batteries = result.get("batteries") or []
        if not batteries:
            failed = set(self._failed_indexes(result))
            for index in range(self.battery_count):
                self._set_battery_statuses(
                    [index],
                    failed_status if index in failed else "OK",
                )
            return

        for battery in batteries:
            index = int(battery["index"])
            status = "OK" if battery.get("ok") else failed_status
            charge_power = self._verification_power(
                battery.get("charge_power")
            )
            discharge_power = self._verification_power(
                battery.get("discharge_power")
            )
            details = (
                f"mode={battery.get('work_mode', 'onbekend')}, "
                f"rs485={battery.get('rs485', 'onbekend')}, "
                f"force={battery.get('force_mode', 'onbekend')}, "
                f"laden={charge_power}, ontladen={discharge_power}"
            )
            self._set_battery_statuses([index], status, details)

    @staticmethod
    def _verification_power(value):
        try:
            return f"{float(value):.0f} W"
        except (TypeError, ValueError):
            return "onbekend"

    def _set_battery_statuses(self, indexes, status, details=None):
        for index in indexes:
            index = int(index)
            if index < 0 or index >= len(self._battery_statuses):
                continue
            self._battery_statuses[index]["status"] = status
            if details is not None:
                self._battery_statuses[index]["details"] = details

    def _publish_final_error(self, message, summary):
        self.publish(
            "Fout",
            f"{message} | {summary}",
            level="ERROR",
            verificatie="DEFINITIEF MISLUKT",
        )

    def _recovery_delay(self):
        resolver = getattr(
            self.controller, "recommended_verification_delay", None
        )
        if callable(resolver):
            return max(1, int(resolver(3)))
        return 3

    def _auto_enabled(self):
        return str(self.app.get_state(self.enabled_helper)).lower() == "on"

    # ---- verdeling over de gekozen accu's --------------------------------
    def participants(self):
        """Indexen van accu's die in de handmatige modus meedoen."""
        settings = getattr(self.app, "runtime_settings", None)
        result = []
        for index in range(self.battery_count):
            helper = (
                self.excluded_helpers[index]
                if index < len(self.excluded_helpers) else None
            )
            if helper and str(self.app.get_state(helper)).lower() == "on":
                continue
            disabled = getattr(settings, "battery_disabled", None)
            if callable(disabled) and disabled(index):
                continue
            result.append(index)
        return result

    def distribute(self, direction, total):
        """Verdeel het totaalvermogen; None als geen accu is gekozen."""
        indexes = self.participants()
        if not indexes:
            return None
        batteries = getattr(self.controller, "batteries", [])
        key = "max_charge_power" if direction == "charge" else "max_discharge_power"
        mode = str(self.app.get_state(self.distribution_helper) or "Gelijk")
        by_soc = mode.strip().lower() == "naar soc"
        caps, weights, blocked = {}, {}, set()
        for index in indexes:
            battery = batteries[index] if index < len(batteries) else {}
            cap = self._number(battery.get(key))
            caps[index] = 2500.0 if cap is None else max(0.0, cap)
            raw_soc = self._number(battery.get("soc"))
            if self._soc_blocks(index, direction, raw_soc):
                blocked.add(index)
            weight = 1.0
            if by_soc:
                soc = 50.0 if raw_soc is None else min(100.0, max(0.0, raw_soc))
                weight = (100.0 - soc) if direction == "charge" else soc
                weight = max(weight, 1.0)
            weights[index] = weight
        if blocked and len(blocked) < len(indexes):
            # Volle (of lege) accu's krijgen 0 W; de waterfill geeft hun
            # aandeel direct door aan de accu's die nog kunnen.
            for index in blocked:
                caps[index] = 0.0
        # Kunnen alle accu's niets meer, dan blijft de oude verdeling staan.
        self._soc_blocked = blocked
        self._soc_blocked_direction = direction
        shares = self._waterfill(float(total), caps, weights)
        return [shares.get(index, 0.0) for index in range(self.battery_count)]

    def _soc_blocks(self, index, direction, soc, previous=None):
        """True als deze accu wegens zijn SOC niet kan laden/ontladen."""
        if soc is None:
            return False
        if direction == "charge":
            if previous is None:
                previous = (
                    self._soc_blocked_direction == "charge"
                    and index in self._soc_blocked
                )
            if previous:
                return soc > FULL_RELEASE_SOC_PERCENT
            return soc >= FULL_SOC_PERCENT
        return soc <= BMS_DISCHARGE_CUTOFF_SOC

    def _soc_changed(self, entity, attribute, old, new, kwargs):
        """Herverdeel alleen als een accu vol/leeg raakt of weer meedoet."""
        if old == new or self._auto_enabled():
            return
        mode = self.app.get_state(self.mode_helper)
        if mode not in ("Laden", "Ontladen"):
            return
        direction = "charge" if mode == "Laden" else "discharge"
        if self._soc_blocked_direction != direction:
            return
        batteries = getattr(self.controller, "batteries", [])
        index = next(
            (
                i for i, battery in enumerate(batteries)
                if battery.get("soc") == entity
            ),
            None,
        )
        if index is None or index not in self.participants():
            return
        was_blocked = index in self._soc_blocked
        try:
            soc = float(new)
        except (TypeError, ValueError):
            return
        if self._soc_blocks(index, direction, soc, previous=was_blocked) == was_blocked:
            return
        self._generation += 1
        self.app.run_in(
            self._soc_reapply,
            1,
            generation=self._generation,
        )

    def _soc_reapply(self, kwargs):
        if int(kwargs["generation"]) != self._generation:
            return
        if self._busy:
            # Een lopende opdracht wordt eerst afgerond; daarna herverdelen.
            self.app.run_in(
                self._soc_reapply,
                SOC_REAPPLY_RETRY_SECONDS,
                generation=self._generation,
            )
            return
        self.apply_current()

    @staticmethod
    def _waterfill(total, caps, weights):
        """Verdeel naar gewicht; accu's op hun maximum geven de rest door."""
        result = {index: 0.0 for index in caps}
        open_indexes = set(caps)
        remaining = max(0.0, total)
        while open_indexes and remaining > 1e-9:
            weight_sum = sum(weights[i] for i in open_indexes)
            capped = False
            for index in sorted(open_indexes):
                share = remaining * weights[index] / weight_sum
                room = caps[index] - result[index]
                if share >= room - 1e-9:
                    result[index] += room
                    open_indexes.discard(index)
                    capped = True
                    remaining -= room
                    break
            if not capped:
                for index in open_indexes:
                    result[index] += remaining * weights[index] / weight_sum
                remaining = 0.0
        return result

    def _number(self, entity_id):
        if not entity_id:
            return None
        state = self.app.get_state(entity_id)
        try:
            return float(state)
        except (TypeError, ValueError):
            return None

    def _power(self, entity_id):
        state = self.app.get_state(entity_id)
        try:
            return min(self.max_power, max(0.0, float(state)))
        except (TypeError, ValueError):
            return 0.0

    def _verification_delay(self):
        resolver = getattr(
            self.controller, "recommended_verification_delay", None
        )
        if callable(resolver):
            return max(1, int(resolver(self.verification_delay)))
        return self.verification_delay
