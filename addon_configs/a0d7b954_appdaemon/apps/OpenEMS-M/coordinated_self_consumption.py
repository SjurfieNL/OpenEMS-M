"""Gecoördineerde zelfconsumptie: alle accu's altijd dezelfde richting."""

import time

from efficient_battery_selector import EfficientBatterySelector
from ems_config import (
    COORDINATED_EVENT_MIN_INTERVAL_SECONDS,
    COORDINATED_GRID_DEADBAND_W,
    COORDINATED_MAX_STEP_W,
    COORDINATED_MIN_POWER_W,
    MARSTEK_BATCH_CONFIRMATION_TIMEOUT_SECONDS,
    POWER_CONFLICT_THRESHOLD_W,
    RUNTIME_DELIVERY_GRACE_SECONDS,
    RUNTIME_MISMATCH_CONFIRMATIONS,
)
from power_distribution import (
    distribute_charge_power,
    distribute_discharge_power,
)


class CoordinatedSelfConsumption:
    def __init__(
        self,
        app,
        controller,
        battery_manager,
        settings,
        grid_calibrator=None,
    ):
        self.app = app
        self.controller = controller
        self.battery_manager = battery_manager
        self.settings = settings
        self.grid_calibrator = grid_calibrator
        self.battery_selector = EfficientBatterySelector(
            app,
            controller.batteries,
            controller=controller,
            battery_manager=battery_manager,
            settings=settings,
            grid_calibrator=grid_calibrator,
        )
        self.active = False
        self.fallback_latched = False
        self.phase = "inactive"
        self.target_power = 0.0
        self.desired_direction = "stop"
        self.expected_powers = [0.0 for _ in controller.batteries]
        self._generation = 0
        self._mismatch_counts = [0 for _ in controller.batteries]
        self._recoveries = [0 for _ in controller.batteries]
        self._conflict_count = 0
        self._pending_target = None
        self._recovering_until = [0.0 for _ in controller.batteries]
        self._delivery_grace_until = 0.0
        self._tick_running = False
        self._last_event_tick = 0.0
        self._pending_stop_request_id = None
        self._pending_stop_purpose = None
        self._pending_stop_generation = None

    def activate(self):
        if self.fallback_latched:
            self.app.log(
                "OpenEMS zelfconsumptie is vergrendeld na terugval; kies eerst "
                "Marstek en daarna opnieuw OpenEMS",
                level="WARNING",
            )
            return False
        problems = self.settings.validate()
        if problems:
            self.app.log("OpenEMS zelfconsumptie niet gestart | " + " | ".join(problems), level="ERROR")
            return False
        self._generation += 1
        self.active = True
        self.phase = "stopping"
        self.target_power = 0.0
        self.desired_direction = "stop"
        self.expected_powers = [0.0 for _ in self.controller.batteries]
        self._mismatch_counts = [0 for _ in self.controller.batteries]
        self._recoveries = [0 for _ in self.controller.batteries]
        self._conflict_count = 0
        self._recovering_until = [0.0 for _ in self.controller.batteries]
        if not self.controller.set_stop(
            source="coordinated", require_confirmation=True
        ):
            self.active = False
            return False
        self._await_stop_confirmation("activation")
        self.app.log("OpenEMS zelfconsumptie: veilige Stop-overgang gestart")
        self._publish("Overgang", "Veilige Stop wordt bevestigd")
        return True

    def deactivate(self, clear_latch=False):
        self._generation += 1
        self.active = False
        self.phase = "inactive"
        self._clear_pending_stop()
        self.battery_selector.deactivate()
        if clear_latch:
            self.fallback_latched = False

    def tick(self, kwargs=None):
        if not self.active or self.phase != "controlling":
            return
        pending = getattr(
            self.controller, "atomic_batch_pending", None
        )
        if callable(pending) and pending():
            return
        event_driven = bool(
            isinstance(kwargs, dict) and kwargs.get("event_driven")
        )
        now = time.monotonic()
        if event_driven and (
            now - self._last_event_tick
            < COORDINATED_EVENT_MIN_INTERVAL_SECONDS
        ):
            return
        if self._tick_running:
            return
        if event_driven:
            self._last_event_tick = now
        self._tick_running = True
        try:
            guard = getattr(self.app, "telemetry_guard", None)
            if guard is not None:
                freshness = guard.check(include_grid=True)
                guard.log_transition()
                if not freshness["ok"]:
                    self._central_failure("kritieke sensordata is verouderd")
                    return
            grid = (
                self.grid_calibrator.corrected_grid_power()
                if self.grid_calibrator is not None
                else self._number(self.settings.grid_entity())
                * self.settings.grid_import_sign()
            )
            actual = [self._number(item["actual_power"]) for item in self.controller.batteries]
            if self._has_power_conflict(actual):
                self._conflict_count += 1
                if self._conflict_count >= RUNTIME_MISMATCH_CONFIRMATIONS:
                    self._central_failure("tegelijk laden en ontladen gemeten")
                    return
            else:
                self._conflict_count = 0

            # Accuvermogen: positief ontladen, negatief laden. Het bestaande
            # accuvermogen plus de resterende netafname vormt de nieuwe setpoint.
            reconstructed_request = sum(actual) + grid
            if abs(reconstructed_request) <= COORDINATED_GRID_DEADBAND_W:
                reconstructed_request = 0.0
            requested = self._limit_step(reconstructed_request)
            self._latest_reconstructed_request = reconstructed_request
            self._apply_target(requested)
            self._verify_runtime(actual)
        except (TypeError, ValueError) as error:
            self._central_failure(f"ongeldige telemetrie: {error}")
        finally:
            self._tick_running = False

    def verification_result(self):
        if self.phase in {"stopping", "transitioning", "recovering"}:
            return {
                "ok": False,
                "pending": True,
                "summary": "Wacht op bevestiging van de MarsTek-batch",
                "failed_indexes": [],
                "batteries": [],
            }
        state = "forced_discharge" if self.desired_direction == "discharge" else (
            "charging" if self.desired_direction == "charge" else "stopped"
        )
        return self.controller.verify_state(state, self.expected_powers)

    def _finish_activation(self, kwargs):
        if not self.active or int(kwargs["generation"]) != self._generation:
            return
        pending = getattr(self.controller, "command_sequence_pending", None)
        if callable(pending) and pending():
            self.app.run_in(
                self._finish_activation, 1, generation=self._generation
            )
            return
        result = self.controller.verify_state("stopped")
        if not result["ok"]:
            self._central_failure("veilige Stop-overgang niet bevestigd")
            return
        self._complete_activation()

    def handle_atomic_result(self, data):
        """Rond een veilige Stop pas af na read-back van dezelfde batch."""
        if not isinstance(data, dict):
            return
        request_id = data.get("request_id")
        matches_stop = (
            request_id is None
            or (
                str(request_id) == self._pending_stop_request_id
                and self._pending_stop_generation == self._generation
            )
        )
        if request_id is not None and not matches_stop:
            latest = getattr(
                self.controller, "last_atomic_request_id", lambda: None
            )()
            if str(request_id) != str(latest):
                return
            if not data.get("ok") and self.active:
                self._central_failure(
                    "nieuwste MarsTek-vermogensbatch is niet bevestigd: "
                    + str(data.get("error") or "onbekende schrijffout")
                )
            return
        if request_id is None or not matches_stop:
            return
        purpose = self._pending_stop_purpose
        self._clear_pending_stop()
        ok, reason = self._confirmed_stop_result(data)
        if not ok:
            self._central_failure(
                f"MarsTek Stop-batch niet bevestigd: {reason}"
            )
            return
        self.app.log(
            "MarsTek Stop-batch bevestigd door atomaire read-back"
        )
        if purpose == "activation":
            self._complete_activation()
        elif purpose == "direction":
            self._complete_direction_transition()

    def _await_stop_confirmation(self, purpose):
        resolver = getattr(
            self.controller, "last_atomic_request_id", None
        )
        request_id = resolver() if callable(resolver) else None
        if not request_id:
            delay = self.controller.recommended_verification_delay(minimum=4)
            callback = (
                self._finish_activation
                if purpose == "activation"
                else self._finish_direction_transition
            )
            self.app.run_in(
                callback, delay, generation=self._generation
            )
            return
        self._pending_stop_request_id = str(request_id)
        self._pending_stop_purpose = purpose
        self._pending_stop_generation = self._generation
        self.app.run_in(
            self._stop_confirmation_timeout,
            MARSTEK_BATCH_CONFIRMATION_TIMEOUT_SECONDS,
            generation=self._generation,
            request_id=str(request_id),
        )

    def _stop_confirmation_timeout(self, kwargs):
        if (
            not self.active
            or int(kwargs["generation"]) != self._generation
            or str(kwargs["request_id"]) != self._pending_stop_request_id
        ):
            return
        self._clear_pending_stop()
        self._central_failure(
            "geen resultaat ontvangen voor de veilige MarsTek Stop-batch"
        )

    def _complete_activation(self):
        if not self.active or self.phase != "stopping":
            return
        self.phase = "controlling"
        self.app.log("OpenEMS zelfconsumptie actief; centrale vermogensregeling gestart")
        self._publish("Actief", "Centrale P1-regeling actief")
        self.tick()

    def _confirmed_stop_result(self, data):
        if not data.get("ok"):
            return False, str(data.get("error") or "onbekende schrijffout")
        results = data.get("results")
        if (
            not isinstance(results, list)
            or len(results) != len(self.controller.batteries)
        ):
            return False, "read-backresultaten zijn niet voor alle accu's aanwezig"
        for result in results:
            try:
                correct = (
                    result.get("ok") is True
                    and int(result.get("force_mode", -1)) == 0
                    and int(result.get("set_charge_power", -1)) == 0
                    and int(result.get("set_discharge_power", -1)) == 0
                )
            except (AttributeError, TypeError, ValueError):
                correct = False
            if not correct:
                return False, "minstens één accu bevestigde Stop/0 W niet"
        return True, "Stop/0 W bevestigd"

    def _clear_pending_stop(self):
        self._pending_stop_request_id = None
        self._pending_stop_purpose = None
        self._pending_stop_generation = None

    def _apply_target(self, requested, reconstructed_request=None):
        if reconstructed_request is None:
            reconstructed_request = getattr(
                self, "_latest_reconstructed_request", requested
            )
        battery = self.battery_manager.read()
        minimum_soc = self._helper_number("input_number.marstek_minimum_soc", 20.0)
        target_soc = self._helper_number("input_number.marstek_doel_soc", 95.0)
        if requested >= COORDINATED_MIN_POWER_W:
            selector_powers = self.battery_selector.allocate(
                requested,
                battery,
                minimum_soc,
                demand_power=(
                    requested
                    if reconstructed_request is None
                    else max(0.0, reconstructed_request)
                ),
            )
            powers = (
                selector_powers
                if selector_powers is not None
                else distribute_discharge_power(
                    requested, battery.soc_values, minimum_soc,
                    battery.max_discharge_values, COORDINATED_MIN_POWER_W,
                )
            )
            direction = "discharge" if sum(powers) > 0 else "stop"
        elif requested <= -COORDINATED_MIN_POWER_W:
            if self.battery_selector.enabled():
                self.battery_selector.allocate(0.0, battery, minimum_soc)
            powers = distribute_charge_power(
                -requested, battery.soc_values, target_soc,
                battery.max_charge_values, COORDINATED_MIN_POWER_W,
            )
            direction = "charge" if sum(powers) > 0 else "stop"
        else:
            if self.battery_selector.enabled():
                self.battery_selector.allocate(0.0, battery, minimum_soc)
            powers = [0.0 for _ in self.controller.batteries]
            direction = "stop"

        rounded = [round(value) for value in powers]
        if direction == self.desired_direction and rounded == [round(v) for v in self.expected_powers]:
            self.target_power = requested
            return
        if (
            direction in {"charge", "discharge"}
            and self.desired_direction in {"charge", "discharge"}
            and direction != self.desired_direction
        ):
            self._start_direction_transition(direction, rounded, requested)
            return
        self._send_direction(direction, rounded)
        self.desired_direction = direction
        self.expected_powers = rounded
        self.target_power = requested
        self._publish(
            "Actief",
            f"{direction} | totaal {sum(rounded):.0f} W",
        )

    def _send_direction(self, direction, powers):
        if direction != self.desired_direction:
            self._delivery_grace_until = (
                time.monotonic() + RUNTIME_DELIVERY_GRACE_SECONDS
            )
        if direction == "charge":
            self.controller.set_charge(powers, source="coordinated")
        elif direction == "discharge":
            self.controller.set_discharge(powers, source="coordinated")
        else:
            self.controller.set_stop(source="coordinated")

    def _start_direction_transition(self, direction, powers, target):
        self.phase = "transitioning"
        self._pending_target = (direction, list(powers), target)
        self.controller.set_stop(
            source="coordinated", require_confirmation=True
        )
        self._await_stop_confirmation("direction")
        self.app.log(
            f"OpenEMS zelfconsumptie: veilige richtingswissel naar {direction}"
        )
        self._publish("Overgang", f"Stop voor richtingswissel naar {direction}")

    def _finish_direction_transition(self, kwargs):
        if not self.active or int(kwargs["generation"]) != self._generation:
            return
        pending = getattr(self.controller, "command_sequence_pending", None)
        if callable(pending) and pending():
            self.app.run_in(
                self._finish_direction_transition,
                1,
                generation=self._generation,
            )
            return
        if not self.controller.verify_state("stopped")["ok"]:
            self._central_failure("Stop bij richtingswissel niet bevestigd")
            return
        self._complete_direction_transition()

    def _complete_direction_transition(self):
        if (
            not self.active
            or self.phase != "transitioning"
            or self._pending_target is None
        ):
            return
        direction, powers, target = self._pending_target
        self._pending_target = None
        self._send_direction(direction, powers)
        self.desired_direction = direction
        self.expected_powers = powers
        self.target_power = target
        self.phase = "controlling"
        self._publish("Actief", f"Richtingswissel naar {direction} voltooid")

    def _verify_runtime(self, actual):
        pending = getattr(self.controller, "command_sequence_pending", None)
        if callable(pending) and pending():
            return
        result = self.verification_result()
        for item in result["batteries"]:
            index = item["index"]
            # De vermogensverdeler heeft deze accu bewust niet geselecteerd,
            # bijvoorbeeld omdat SOC <= minimum-SOC. Stop + circa 0 W is dan
            # het gewenste gedrag en mag nooit een herstelactie veroorzaken.
            if self.expected_powers[index] < COORDINATED_MIN_POWER_W:
                self._mismatch_counts[index] = 0
                self._recoveries[index] = 0
                continue
            delivery_ready = getattr(
                self.controller, "battery_delivery_ready", None
            )
            if callable(delivery_ready) and not delivery_ready(
                index, RUNTIME_DELIVERY_GRACE_SECONDS
            ):
                self._mismatch_counts[index] = 0
                continue
            if time.monotonic() < self._recovering_until[index]:
                continue
            delivery_ok = self._delivery_ok(actual[index], index)
            if item["ok"] and delivery_ok:
                self._mismatch_counts[index] = 0
                self._recoveries[index] = 0
                continue
            self._mismatch_counts[index] += 1
            if self._mismatch_counts[index] < RUNTIME_MISMATCH_CONFIRMATIONS:
                continue
            if self._recoveries[index] >= 1:
                self.controller.stop_batteries([index], source="coordinated")
                self._central_failure(f"{item['name']} blijft afwijken na herstel")
                return
            self._recoveries[index] = 1
            self._mismatch_counts[index] = 0
            self.phase = "recovering"
            self._recovering_until[index] = (
                time.monotonic()
                + self.controller.recommended_verification_delay(minimum=4)
                + RUNTIME_DELIVERY_GRACE_SECONDS
            )
            self._restore_battery({
                "generation": self._generation,
                "index": index,
            })

    def _restore_battery(self, kwargs):
        if not self.active or int(kwargs["generation"]) != self._generation:
            return
        index = int(kwargs["index"])
        pending = getattr(
            self.controller, "command_sequence_pending", None
        )
        if callable(pending) and pending():
            self.app.run_in(
                self._restore_battery,
                1,
                generation=self._generation,
                index=index,
            )
            return
        safety = getattr(self.app, "runtime_safety_monitor", None)
        state = (
            "forced_discharge"
            if self.desired_direction == "discharge"
            else "charging"
            if self.desired_direction == "charge"
            else "stopped"
        )
        if safety is not None:
            safe, reason = safety._bms_allows(index, state)
            if not safe:
                self.controller.stop_batteries(
                    [index], source="coordinated"
                )
                self._central_failure(
                    f"{self.controller.batteries[index]['name']}: "
                    f"herstel afgebroken door {reason}"
                )
                return
        self.controller.restore_batteries(
            state,
            [index], self.expected_powers, source="coordinated",
        )
        self.app.run_in(
            self._finish_battery_recovery,
            1,
            generation=self._generation,
            index=index,
        )
        self.app.log(
            f"{self.controller.batteries[index]['name']}: dezelfde gewenste "
            "opdracht eenmaal opnieuw verstuurd",
            level="WARNING",
        )

    def _finish_battery_recovery(self, kwargs):
        if not self.active or int(kwargs["generation"]) != self._generation:
            return
        index = int(kwargs["index"])
        pending = getattr(
            self.controller, "command_sequence_pending", None
        )
        delivery_ready = getattr(
            self.controller, "battery_delivery_ready", None
        )
        ready = (
            delivery_ready(index, RUNTIME_DELIVERY_GRACE_SECONDS)
            if callable(delivery_ready) else True
        )
        if (callable(pending) and pending()) or not ready:
            self.app.run_in(
                self._finish_battery_recovery,
                1,
                generation=self._generation,
                index=index,
            )
            return
        self._recovering_until[index] = 0.0
        self._mismatch_counts[index] = 0
        self.phase = "controlling"
        self.app.log(
            f"{self.controller.batteries[index]['name']}: "
            "herstel bevestigd; centrale regeling hervat"
        )
        self._publish("Actief", "Centrale P1-regeling actief")
        self.tick()

    def _central_failure(self, reason):
        self.app.log(f"OpenEMS zelfconsumptie gestopt: {reason}", level="ERROR")
        self._publish("Fout", reason)
        fallback = self.settings.fallback_enabled()
        self.deactivate()
        self.controller.set_stop(source="coordinated")
        if fallback:
            self.fallback_latched = True
            delay = self.controller.recommended_verification_delay(minimum=4)
            self.app.run_in(self._apply_native_fallback, delay, generation=self._generation)

    def _apply_native_fallback(self, kwargs):
        if int(kwargs["generation"]) != self._generation or not self.fallback_latched:
            return
        self.controller.set_self_consumption(source="coordinated", force_native=True)
        self.app.log("Veilige terugval naar Marstek zelfconsumptie uitgevoerd; terugval is vergrendeld", level="WARNING")
        self._publish("Teruggevallen", "Marstek actief; automatische terugval vergrendeld")

    def _publish(self, state, message):
        self.app.set_state(
            "sensor.openems_zelfconsumptie_status",
            state=state,
            attributes={
                "friendly_name": "OpenEMS zelfconsumptiestatus",
                "icon": "mdi:home-battery-outline",
                "melding": message,
                "fase": self.phase,
                "richting": self.desired_direction,
                "doelvermogen_w": round(self.target_power),
                "per_accu_w": list(self.expected_powers),
                "terugval_vergrendeld": self.fallback_latched,
            },
            replace=True,
        )

    def _delivery_ok(self, actual, index):
        if time.monotonic() < self._delivery_grace_until:
            return True
        expected = self.expected_powers[index]
        if expected < COORDINATED_MIN_POWER_W:
            return abs(actual) <= POWER_CONFLICT_THRESHOLD_W
        tolerance = max(100.0, expected * 0.25)
        if self.desired_direction == "discharge":
            return actual >= -POWER_CONFLICT_THRESHOLD_W and abs(actual - expected) <= tolerance
        if self.desired_direction == "charge":
            return actual <= POWER_CONFLICT_THRESHOLD_W and abs((-actual) - expected) <= tolerance
        return True

    @staticmethod
    def _has_power_conflict(values):
        return (
            any(value > POWER_CONFLICT_THRESHOLD_W for value in values)
            and any(value < -POWER_CONFLICT_THRESHOLD_W for value in values)
        )

    def _limit_step(self, requested):
        low = self.target_power - COORDINATED_MAX_STEP_W
        high = self.target_power + COORDINATED_MAX_STEP_W
        return max(low, min(high, requested))

    def _number(self, entity_id):
        value = self.app.get_state(entity_id)
        if value in (None, "", "unknown", "unavailable"):
            raise ValueError(f"{entity_id}={value}")
        return float(value)

    def _helper_number(self, entity_id, default):
        try:
            return self._number(entity_id)
        except ValueError:
            return float(default)
