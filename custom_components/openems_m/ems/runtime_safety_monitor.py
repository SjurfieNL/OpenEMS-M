# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Continue, begrensde bewaking van actieve Force Mode-opdrachten."""

import time
from datetime import datetime

from .ems_config import (
    POWER_CONFLICT_THRESHOLD_W,
    RUNTIME_DELIVERY_GRACE_SECONDS,
    RUNTIME_MISMATCH_CONFIRMATIONS,
    TOP_CHARGE_TAPER_SOC_PERCENT,
)


class RuntimeSafetyMonitor:
    MAX_RECOVERY_ATTEMPTS = 1
    DEVICE_RESET_WAIT_SECONDS = 15
    DEVICE_RESET_POLL_SECONDS = 5
    DEVICE_RESET_MAX_WAIT_SECONDS = 90

    def __init__(
        self, app, controller, safety_service=None, event_log=None,
        health_manager=None,
    ):
        self.app = app
        self.controller = controller
        self.safety_service = safety_service
        self.event_log = event_log
        self.health_manager = health_manager
        count = len(controller.batteries)
        self.counts = [0] * count
        self.recoveries = [0] * count
        self.latched = [False] * count
        self.limited = {}
        self.recovering = [False] * count
        self.top_charge_latches = [None] * count
        self.current_charge_block_end = None
        self._desired_signature = None
        self._generations = [0] * count
        self._native_generation = 0
        self.native_conflicts = 0
        self.native_recovery = 0
        self.native_healthy = 0

    def tick(self, kwargs=None):
        coordinator = self.controller.coordinator
        if coordinator is not None and coordinator.active:
            return
        pending = getattr(self.controller, "command_sequence_pending", None)
        if callable(pending) and pending():
            # Een tussenstand tijdens een lopende registerreeks is geen fout.
            return
        wait_remaining = getattr(
            self.controller, "verification_wait_remaining", None
        )
        if (
            callable(wait_remaining)
            and wait_remaining(RUNTIME_DELIVERY_GRACE_SECONDS) > 0
        ):
            return
        state = self.controller.desired_state
        signature = (
            state,
            tuple(round(value) for value in self.controller.desired_powers),
            getattr(self.controller, "desired_source", None),
        )
        if signature != self._desired_signature:
            self._desired_signature = signature
            self._reset()
        if state == "self_consumption":
            self._monitor_native()
            return
        if state not in {"charging", "forced_discharge", "stopped"}:
            self._reset()
            return
        delivery_ready = getattr(
            self.controller, "battery_delivery_ready", None
        )
        if (
            not callable(delivery_ready)
            and time.monotonic() - self.controller.desired_since
            < RUNTIME_DELIVERY_GRACE_SECONDS
        ):
            return

        result = self.controller.verify_state(
            state, self.controller.desired_powers
        )
        export_total_ok = (
            state == "forced_discharge"
            and getattr(self.controller, "desired_source", None) == "export"
            and self._export_total_delivery_ok()
        )
        for item in result["batteries"]:
            index = item["index"]
            availability = getattr(
                self.health_manager, "is_available", None
            )
            if callable(availability) and not availability(index):
                # De centrale accugezondheid bezit deze rustperiode. Nieuwe
                # vermogensverdelingen mogen geen tweede herstelcyclus voor
                # dezelfde reeds geïsoleerde accu starten.
                self.counts[index] = 0
                self.recovering[index] = False
                self.latched[index] = True
                continue
            reset_pending = getattr(
                self.controller, "device_reset_pending", None
            )
            if callable(reset_pending) and reset_pending(index):
                # De resetroute bezit deze accu totdat zij zelf de terugkeer,
                # herhaalopdracht en eindverificatie heeft afgehandeld.
                continue
            if callable(delivery_ready) and not delivery_ready(
                index, RUNTIME_DELIVERY_GRACE_SECONDS
            ):
                continue
            if self.latched[index] or self.recovering[index]:
                continue
            battery_state = self._expected_battery_state(index)
            safe, reason = self._bms_allows(index, battery_state)
            delivery_ok = (
                export_total_ok
                or self._export_individual_delivery_ok(index)
                if battery_state == "forced_discharge"
                and getattr(
                    self.controller, "desired_source", None
                ) == "export"
                else self._delivery_ok(index, battery_state)
            )
            if item["ok"] and delivery_ok and safe:
                if self.recoveries[index] > 0:
                    self._event(
                        "recovered",
                        f"{item['name']} hersteld",
                        "Besturing en werkelijk vermogen zijn weer correct",
                        index=index,
                        details=(
                            f"Verwachte status {battery_state}; verificatie "
                            "is opnieuw volledig in orde."
                        ),
                        expected_state=battery_state,
                        attempt=self.recoveries[index],
                    )
                self.counts[index] = 0
                self.recoveries[index] = 0
                continue
            # Tegengesteld werkelijk vermogen is direct gevaarlijk: een accu
            # die ontlaadt terwijl laden is opgedragen (of andersom) wordt
            # zonder de normale bevestigingswachttijd geïsoleerd. Deze regel
            # geldt identiek en onafhankelijk voor iedere aangesloten accu.
            opposite_direction = self._opposite_direction(
                index, battery_state
            )
            if opposite_direction:
                self.counts[index] = RUNTIME_MISMATCH_CONFIRMATIONS
            else:
                self.counts[index] += 1
            if self.counts[index] < RUNTIME_MISMATCH_CONFIRMATIONS:
                continue
            if not safe:
                self.controller.isolate_batteries(
                    [index], source=self._source(), safety=True
                )
                self.app.log(
                    f"{item['name']}: veilig gestopt door BMS/SOC-signaal "
                    f"({reason})",
                    level="INFO",
                )
                self._event(
                    "info",
                    f"{item['name']} veilig gestopt",
                    "BMS- of SOC-beveiliging heeft ingegrepen",
                    index=index,
                    details=reason,
                    expected_state=battery_state,
                )
                self.counts[index] = 0
                self.recoveries[index] = 1
                self.latched[index] = True
                continue
            if opposite_direction:
                self.controller.isolate_batteries(
                    [index], source=self._source(), safety=True
                )
                self.app.log(
                    f"{item['name']}: direct veilig gestopt wegens vermogen "
                    "in de tegengestelde richting",
                    level="ERROR",
                )
                self._event(
                    "error",
                    f"{item['name']} veilig gestopt",
                    "Werkelijk vermogen liep in de tegengestelde richting",
                    index=index,
                    details=self._mismatch_details(
                        item, index, battery_state
                    ),
                    expected_state=battery_state,
                )
                self.counts[index] = 0
                self.latched[index] = True
                continue
            if self.recoveries[index] >= self.MAX_RECOVERY_ATTEMPTS:
                if self._start_device_reset(index, battery_state, item):
                    continue
                self.controller.isolate_batteries(
                    [index], source=self._source(), safety=True
                )
                self.app.log(
                    f"{item['name']}: veilig geïsoleerd; status bleef "
                    "afwijken en de eenmalige apparaat-reset was niet "
                    "beschikbaar of was al gebruikt",
                    level="ERROR",
                )
                self._event(
                    "error",
                    f"{item['name']} veilig geïsoleerd",
                    "Afwijking bleef bestaan na begrensd herstel",
                    index=index,
                    details=self._mismatch_details(
                        item, index, battery_state
                    ),
                    expected_state=battery_state,
                    attempt=self.MAX_RECOVERY_ATTEMPTS,
                )
                self.counts[index] = 0
                self.latched[index] = True
                if self.health_manager is not None:
                    self.health_manager.quarantine(
                        index,
                        self._mismatch_details(
                            item, index, battery_state
                        ),
                        attempts=self.MAX_RECOVERY_ATTEMPTS,
                    )
                continue
            self.recoveries[index] += 1
            attempt = self.recoveries[index]
            self.counts[index] = 0
            self.recovering[index] = True
            self._generations[index] += 1
            delay = 0
            self.app.run_in(
                self._restore,
                delay,
                generation=self._generations[index],
                index=index,
                state=battery_state,
                attempt=attempt,
            )
            details = self._mismatch_details(
                item, index, battery_state
            )
            self.app.log(
                f"{item['name']}: besturingsafwijking bevestigd "
                f"({details}); dezelfde gewenste opdracht opnieuw verstuurd "
                f"({attempt}/{self.MAX_RECOVERY_ATTEMPTS})",
                level="WARNING",
            )
            self._event(
                "warning",
                f"{item['name']} wijkt af",
                f"Herstelpoging {attempt}/{self.MAX_RECOVERY_ATTEMPTS} gestart",
                index=index,
                details=details,
                expected_state=battery_state,
                attempt=attempt,
            )

    def _start_device_reset(self, index, state, item):
        requester = getattr(self.controller, "request_device_reset", None)
        if not callable(requester) or not requester(
            index, source=self._source()
        ):
            return False
        self.recovering[index] = True
        self.counts[index] = 0
        self._generations[index] += 1
        generation = self._generations[index]
        details = self._mismatch_details(item, index, state)
        self.app.log(
            f"{item['name']}: gewone herstelopdracht hielp niet; "
            f"eenmalige apparaat-reset uitgevoerd en wacht "
            f"{self.DEVICE_RESET_WAIT_SECONDS} seconden op terugkeer",
            level="WARNING",
        )
        self._event(
            "warning",
            f"{item['name']} wordt eenmaal gereset",
            "Gerichte apparaat-reset na mislukte herhaalopdracht",
            index=index,
            details=details,
            expected_state=state,
            attempt=1,
        )
        self.app.run_in(
            self._resume_after_device_reset,
            self.DEVICE_RESET_WAIT_SECONDS,
            generation=generation,
            index=index,
            state=state,
            waited=self.DEVICE_RESET_WAIT_SECONDS,
        )
        return True

    def _resume_after_device_reset(self, kwargs):
        index = int(kwargs["index"])
        if int(kwargs["generation"]) != self._generations[index]:
            return
        state = str(kwargs["state"])
        if state != self._expected_battery_state(index):
            self._cancel_device_reset(index)
            return

        guard = getattr(self.app, "telemetry_guard", None)
        if guard is not None:
            freshness = guard.check(include_grid=False)
            unavailable = any(
                item.get("accu_index") == index
                for item in freshness["problems"]
            )
            if unavailable:
                waited = int(kwargs.get("waited", 0))
                if waited < self.DEVICE_RESET_MAX_WAIT_SECONDS:
                    self.app.run_in(
                        self._resume_after_device_reset,
                        self.DEVICE_RESET_POLL_SECONDS,
                        **dict(
                            kwargs,
                            waited=waited + self.DEVICE_RESET_POLL_SECONDS,
                        ),
                    )
                    return
                self._fail_device_reset(
                    index, state,
                    "geen verse accutelemetrie binnen 90 seconden",
                )
                return

        safe, reason = self._bms_allows(index, state)
        if not safe:
            self._fail_device_reset(
                index, state, f"BMS/SOC verhindert herstel: {reason}"
            )
            return
        powers = list(self.controller.desired_powers)
        if not self.controller.restore_batteries(
            state, [index], powers, source=self._source()
        ):
            self._fail_device_reset(
                index, state, "opdracht na reset werd geweigerd"
            )
            return

        delay_resolver = getattr(
            self.controller, "recommended_verification_delay", None
        )
        delay = (
            delay_resolver(RUNTIME_DELIVERY_GRACE_SECONDS)
            if callable(delay_resolver)
            else RUNTIME_DELIVERY_GRACE_SECONDS
        )
        self.app.run_in(
            self._verify_device_reset,
            max(1, int(delay)),
            generation=kwargs["generation"],
            index=index,
            state=state,
        )

    def _verify_device_reset(self, kwargs):
        index = int(kwargs["index"])
        if int(kwargs["generation"]) != self._generations[index]:
            return
        state = str(kwargs["state"])
        if state != self._expected_battery_state(index):
            self._cancel_device_reset(index)
            return
        result = self.controller.verify_state(
            state,
            self.controller.desired_powers,
            battery_indexes=[index],
        )
        item = next(
            (
                value for value in result.get("batteries", [])
                if int(value.get("index", -1)) == index
            ),
            None,
        )
        safe, reason = self._bms_allows(index, state)
        if (
            item is not None
            and item.get("ok")
            and self._delivery_ok(index, state)
            and safe
        ):
            self.controller.complete_device_reset(index, recovered=True)
            self.recovering[index] = False
            self.recoveries[index] = 0
            self.counts[index] = 0
            self.app.log(
                f"{item['name']}: besturing hersteld na eenmalige "
                "apparaat-reset"
            )
            self._event(
                "recovered",
                f"{item['name']} hersteld na reset",
                "Apparaat-reset en eenmalige herhaalopdracht zijn bevestigd",
                index=index,
                expected_state=state,
                attempt=1,
            )
            return
        details = (
            self._mismatch_details(item, index, state)
            if item is not None else
            str(reason or "geen geldige verificatie ontvangen")
        )
        self._fail_device_reset(index, state, details)

    def _cancel_device_reset(self, index):
        completer = getattr(self.controller, "complete_device_reset", None)
        if callable(completer):
            completer(index, recovered=False)
        self.recovering[index] = False

    def _fail_device_reset(self, index, state, details):
        self._cancel_device_reset(index)
        self.controller.isolate_batteries(
            [index], source=self._source(), safety=True
        )
        self.latched[index] = True
        name = self.controller.batteries[index]["name"]
        self.app.log(
            f"{name}: veilig geïsoleerd; eenmalige apparaat-reset heeft "
            f"de afwijking niet opgelost ({details})",
            level="ERROR",
        )
        self._event(
            "error",
            f"{name} na reset veilig geïsoleerd",
            "Eenmalige apparaat-reset heeft het probleem niet opgelost",
            index=index,
            details=details,
            expected_state=state,
            attempt=1,
        )
        if self.health_manager is not None:
            self.health_manager.quarantine(index, details, attempts=1)

    def _restore(self, kwargs):
        index = int(kwargs["index"])
        if int(kwargs["generation"]) != self._generations[index]:
            return
        state = str(kwargs["state"])
        if state != self._expected_battery_state(index):
            # Controleer dit ook in de callback zelf. Een nieuwe hoofdopdracht
            # kan namelijk vlak voor de volgende vijfseconden-tick binnenkomen.
            # Een herstelactie uit de vorige toestand mag dan nooit meer een
            # laad/ontlaadopdracht naar deze accu sturen.
            self._generations[index] += 1
            self.recovering[index] = False
            self.app.log(
                f"{self.controller.batteries[index]['name']}: verouderde "
                f"herstelopdracht {state} genegeerd; actuele opdracht is "
                f"{self._expected_battery_state(index)}",
                level="INFO",
            )
            return
        self.recovering[index] = False
        guard = getattr(self.app, "telemetry_guard", None)
        if guard is not None:
            freshness = guard.check(include_grid=False)
            if any(
                item.get("accu_index") == index
                for item in freshness["problems"]
            ):
                self.controller.isolate_batteries(
                    [index], source=self._source(), safety=True
                )
                self.app.log(
                    f"{self.controller.batteries[index]['name']}: herstel "
                    "afgebroken omdat kritieke sensordata niet actueel is",
                    level="ERROR",
                )
                self._event(
                    "error",
                    f"{self.controller.batteries[index]['name']} geïsoleerd",
                    "Herstel afgebroken door verouderde sensordata",
                    index=index,
                    details=(
                        "Kritieke accugegevens waren niet actueel; de accu "
                        "is uit veiligheid niet opnieuw gestart."
                    ),
                    expected_state=kwargs["state"],
                    attempt=kwargs.get("attempt"),
                )
                self.latched[index] = True
                return
        safe, reason = self._bms_allows(index, state)
        if not safe:
            self.controller.isolate_batteries(
                [index], source=self._source(), safety=True
            )
            self.latched[index] = True
            self.app.log(
                f"{self.controller.batteries[index]['name']}: herstel "
                f"afgebroken en veilig geïsoleerd ({reason})",
                level="INFO",
            )
            self._event(
                "info",
                f"{self.controller.batteries[index]['name']} veilig gestopt",
                "Herstel niet toegestaan door BMS/SOC",
                index=index,
                details=reason,
                expected_state=state,
                attempt=kwargs.get("attempt"),
            )
            return
        recovery_powers = list(self.controller.desired_powers)
        self.controller.restore_batteries(
            state, [index], recovery_powers, source=self._source()
        )
        # Behoud exact het gevraagde vermogen. De controller slaat onderdelen
        # die al correct staan over.
        self._desired_signature = (
            self.controller.desired_state,
            tuple(
                round(value)
                for value in self.controller.desired_powers
            ),
            getattr(self.controller, "desired_source", None),
        )

    def _monitor_native(self):
        values = []
        disabled = getattr(self.controller, "battery_disabled", None)
        for index, battery in enumerate(self.controller.batteries):
            if callable(disabled) and disabled(index):
                # Uitgeschakelde accu: eigen laden/ontladen is geen conflict.
                continue
            actual = self._number(battery.get("actual_power"))
            if actual is None:
                return
            values.append(actual)
        conflict = (
            any(value > POWER_CONFLICT_THRESHOLD_W for value in values)
            and any(value < -POWER_CONFLICT_THRESHOLD_W for value in values)
        )
        if not conflict:
            self.native_conflicts = 0
            self.native_healthy += 1
            if self.native_healthy >= 6:
                self.native_recovery = 0
            return
        self.native_healthy = 0
        self.native_conflicts += 1
        if self.native_conflicts < RUNTIME_MISMATCH_CONFIRMATIONS:
            return
        self.native_conflicts = 0
        source = self._source()
        if self.native_recovery >= 1:
            self.controller.set_stop(source=source)
            self.app.log(
                "Marstek zelfconsumptie definitief gestopt: opnieuw tegelijk "
                "laden en ontladen gemeten",
                level="ERROR",
            )
            self._event(
                "error",
                "Zelfconsumptie veilig gestopt",
                "Opnieuw tegelijk laden en ontladen gemeten",
                details=(
                    "De Marstek-regeling bleef na herstel tegengestelde "
                    "vermogensrichtingen gebruiken."
                ),
                expected_state="self_consumption",
            )
            return
        self.native_recovery = 1
        self._native_generation += 1
        self.controller.set_stop(source=source)
        self.app.run_in(
            self._restore_native,
            self.controller.recommended_verification_delay(minimum=4),
            generation=self._native_generation,
            source=source,
        )
        self.app.log(
            "Marstek zelfconsumptie: vermogensconflict bevestigd; één veilige "
            "Stop/herstelpoging gestart",
            level="WARNING",
        )
        self._event(
            "warning",
            "Zelfconsumptie vermogensconflict",
            "Gelijktijdig laden en ontladen bevestigd",
            details=(
                "OpenEMS heeft één veilige Stop/herstelpoging gestart."
            ),
            expected_state="self_consumption",
            attempt=1,
        )

    def _restore_native(self, kwargs):
        if int(kwargs["generation"]) != self._native_generation:
            return
        coordinator = self.controller.coordinator
        force_native = bool(
            coordinator is not None
            and coordinator.settings.self_consumption_mode() == "openems"
        )
        self.controller.invalidate_last_command("native conflict herstellen")
        self.controller.set_self_consumption(
            source=kwargs["source"], force_native=force_native
        )

    def _delivery_ok(self, index, state):
        if state == "stopped":
            expected = 0.0
        elif index < len(self.controller.desired_powers):
            expected = self.controller.desired_powers[index]
        else:
            expected = 0.0
        entity = self.controller.batteries[index].get("actual_power")
        if not entity:
            return True
        guard = getattr(self.app, "telemetry_guard", None)
        if guard is not None and guard.is_stale(entity):
            # Een oude maar geldige Marstek-vermogenswaarde is geen hard
            # leveringsbewijs. RS485, Force Mode, BMS en ingestelde registers
            # blijven in verify_state wel volledig gecontroleerd.
            return True
        actual = self._number(entity)
        if actual is None:
            return False
        if state == "stopped":
            return abs(actual) <= POWER_CONFLICT_THRESHOLD_W
        if state == "charging":
            soc = self._number(
                self.controller.batteries[index].get("soc")
            )
            if (
                soc is not None
                and soc >= TOP_CHARGE_TAPER_SOC_PERCENT
                and actual <= 0.0
            ):
                # Nabij vol mag het BMS de laadstroom zelfstandig afbouwen
                # of stoppen. Positief werkelijk vermogen (ontladen) blijft
                # bewust via de bestaande foutcontrole lopen.
                return True
        tolerance = max(100.0, expected * 0.25)
        measured = actual if state == "forced_discharge" else -actual
        if self._is_limited(index, state, expected, measured, tolerance):
            return True
        self._clear_limited(index)
        return measured >= -POWER_CONFLICT_THRESHOLD_W and abs(measured - expected) <= tolerance

    LIMITED_MINIMUM_POWER_W = 200.0

    def _is_limited(self, index, state, expected, measured, tolerance):
        """Een accu die in de goede richting minder levert is begrensd.

        Dat is geen storing (bv. eigen max-laadvermogen of BMS-afbouw): de
        accu blijft meedoen. Te veel vermogen of de verkeerde richting blijft
        wel een afwijking.
        """
        if state not in {"charging", "forced_discharge"} or expected <= 0:
            return False
        floor = max(self.LIMITED_MINIMUM_POWER_W, expected * 0.10)
        if not (floor <= measured < expected - tolerance):
            return False
        previous = self.limited.get(index)
        self.limited[index] = round(measured)
        if previous is None:
            name = self.controller.batteries[index]["name"]
            self.app.log(
                f"{name}: begrensd op circa {measured:.0f} W "
                f"(gevraagd {expected:.0f} W); doet gewoon mee",
                level="INFO",
            )
        self._publish_limited()
        return True

    def _clear_limited(self, index):
        if self.limited.pop(index, None) is not None:
            self._publish_limited()

    def _publish_limited(self):
        try:
            self.app.set_state(
                "sensor.openems_accu_begrenzing",
                # Als tekst: AppDaemon laat een state 0 weg en HA weigert
                # dan de hele update met een 400 Bad Request.
                state=str(len(self.limited)),
                attributes={
                    "friendly_name": "OpenEMS begrensde accu's",
                    "accus": {
                        self.controller.batteries[i]["name"]: watt
                        for i, watt in self.limited.items()
                    },
                },
            )
        except Exception:  # status is nooit kritiek voor de besturing
            pass

    def _export_total_delivery_ok(self):
        """Beoordeel export eerst op het gezamenlijke accuresultaat."""
        expected_total = 0.0
        measured_total = 0.0
        selected = 0
        guard = getattr(self.app, "telemetry_guard", None)
        for index, expected in enumerate(self.controller.desired_powers):
            expected = max(0.0, float(expected))
            if expected <= 0 or index >= len(self.controller.batteries):
                continue
            entity = self.controller.batteries[index].get("actual_power")
            if not entity or (
                guard is not None and guard.is_stale(entity)
            ):
                return False
            actual = self._number(entity)
            if actual is None or actual < -POWER_CONFLICT_THRESHOLD_W:
                return False
            expected_total += expected
            measured_total += max(0.0, actual)
            selected += 1
        if selected == 0 or expected_total <= 0:
            return False
        tolerance = max(250.0, expected_total * 0.15)
        return abs(measured_total - expected_total) <= tolerance

    def _export_individual_delivery_ok(self, index):
        """Accepteer een werkelijk werkende exportaccu zonder exactheidsdwang."""
        if index >= len(self.controller.desired_powers):
            return False
        expected = max(0.0, float(self.controller.desired_powers[index]))
        if expected <= 0:
            return self._delivery_ok(index, "forced_discharge")
        entity = self.controller.batteries[index].get("actual_power")
        if not entity:
            return True
        guard = getattr(self.app, "telemetry_guard", None)
        if guard is not None and guard.is_stale(entity):
            return True
        actual = self._number(entity)
        if actual is None or actual < -POWER_CONFLICT_THRESHOLD_W:
            return False
        # De netregelaar vangt normale verschillen tussen omvormers op. Pas
        # wanneer een geselecteerde accu minder dan de helft van haar opdracht
        # werkelijk levert, is een individuele herstelcontrole gerechtvaardigd.
        minimum_delivery = max(100.0, expected * 0.50)
        return actual >= minimum_delivery

    def _opposite_direction(self, index, state):
        """Herken een verse meting in de tegengestelde vermogensrichting."""
        if state not in {"charging", "forced_discharge"}:
            return False
        if (
            index >= len(self.controller.desired_powers)
            or self.controller.desired_powers[index] <= 0
        ):
            return False
        entity = self.controller.batteries[index].get("actual_power")
        if not entity:
            return False
        guard = getattr(self.app, "telemetry_guard", None)
        if guard is not None and guard.is_stale(entity):
            return False
        actual = self._number(entity)
        if actual is None:
            return False
        if state == "charging":
            return actual > POWER_CONFLICT_THRESHOLD_W
        return actual < -POWER_CONFLICT_THRESHOLD_W

    def _battery_state(self, state, index):
        if (
            state == "charging"
            and (
                index >= len(self.controller.desired_powers)
                or self.controller.desired_powers[index] <= 0
            )
        ):
            return "stopped"
        return state

    def _expected_battery_state(self, index):
        return self._battery_state(self.controller.desired_state, index)

    def _mismatch_details(self, item, index, state):
        """Geeft per register en vermogensmeting aan wat er afwijkt."""
        reasons = []
        expected = (
            self.controller.desired_powers[index]
            if index < len(self.controller.desired_powers)
            else 0.0
        )
        rs485 = item.get("rs485")
        force_mode = item.get("force_mode")
        charge_power = item.get("charge_power")
        discharge_power = item.get("discharge_power")

        if state == "charging":
            if rs485 is not None and rs485 != "on":
                reasons.append(f"RS485={rs485}, verwacht on")
            if force_mode is not None and force_mode != "charge":
                reasons.append(f"Force Mode={force_mode}, verwacht charge")
            if (
                charge_power is not None
                and abs(charge_power - expected)
                > max(100.0, abs(expected) * 0.10)
            ):
                reasons.append(
                    f"ingesteld laadvermogen={charge_power:.0f} W, "
                    f"verwacht {expected:.0f} W"
                )
            if discharge_power is not None and discharge_power > 100:
                reasons.append(
                    f"ingesteld ontlaadvermogen={discharge_power:.0f} W, "
                    "verwacht maximaal 100 W"
                )
        elif state == "forced_discharge":
            if rs485 is not None and rs485 != "on":
                reasons.append(f"RS485={rs485}, verwacht on")
            if force_mode is not None and force_mode != "discharge":
                reasons.append(
                    f"Force Mode={force_mode}, verwacht discharge"
                )
            if charge_power is not None and charge_power > 100:
                reasons.append(
                    f"ingesteld laadvermogen={charge_power:.0f} W, "
                    "verwacht maximaal 100 W"
                )
            if (
                discharge_power is not None
                and abs(discharge_power - expected)
                > max(100.0, abs(expected) * 0.10)
            ):
                reasons.append(
                    f"ingesteld ontlaadvermogen={discharge_power:.0f} W, "
                    f"verwacht {expected:.0f} W"
                )
        elif state == "stopped":
            if rs485 == "on" and force_mode != "stop":
                reasons.append(
                    f"RS485={rs485} en Force Mode={force_mode}, "
                    "verwacht Force Mode stop"
                )
            if charge_power is not None and charge_power > 25:
                reasons.append(
                    f"ingesteld laadvermogen={charge_power:.0f} W, "
                    "verwacht maximaal 25 W"
                )
            if discharge_power is not None and discharge_power > 25:
                reasons.append(
                    f"ingesteld ontlaadvermogen={discharge_power:.0f} W, "
                    "verwacht maximaal 25 W"
                )

        entity = self.controller.batteries[index].get("actual_power")
        if entity:
            actual = self._number(entity)
            if actual is None:
                reasons.append(f"werkelijk vermogen {entity}=onbeschikbaar")
            elif state == "stopped":
                if abs(actual) > POWER_CONFLICT_THRESHOLD_W:
                    reasons.append(
                        f"werkelijk vermogen={actual:.0f} W, verwacht circa 0 W"
                    )
            else:
                measured = actual if state == "forced_discharge" else -actual
                if abs(measured) < 0.5:
                    measured = 0.0
                tolerance = max(100.0, expected * 0.25)
                if (
                    measured < -POWER_CONFLICT_THRESHOLD_W
                    or abs(measured - expected) > tolerance
                ):
                    direction = (
                        "ontlaadvermogen"
                        if state == "forced_discharge"
                        else "laadvermogen"
                    )
                    reasons.append(
                        f"werkelijk {direction}={measured:.0f} W, "
                        f"verwacht {expected:.0f} W "
                        f"(tolerantie ±{tolerance:.0f} W)"
                    )

        if not reasons and not item.get("ok", False):
            reasons.append("controllerstatus wijkt af")
        return "; ".join(reasons) or "onbekende afwijking"

    def _bms_allows(self, index, state):
        assessment = self.is_battery_operation_safe(index, state)
        return assessment.safe, assessment.reason

    def is_battery_operation_safe(self, index, desired_state):
        """Publieke compatibiliteitsinterface voor operationele veiligheid."""
        if self.safety_service is not None:
            return self.safety_service.is_battery_operation_safe(
                index, desired_state
            )
        # Alleen voor geïsoleerde oude tests zonder centrale SafetyService.
        from .safety_service import BatterySafetyService
        return BatterySafetyService(
            self.app, self.controller.batteries
        ).is_battery_operation_safe(index, desired_state)

    def filter_charge_powers(self, powers):
        """Voorkomt herstarten van een geaccepteerde BMS-topstop."""
        result = list(powers)
        for index in range(min(len(result), len(self.top_charge_latches))):
            if self.is_top_charge_latched(index):
                result[index] = 0.0
            elif (
                self.recoveries[index] > 0
                and index < len(self.controller.desired_powers)
            ):
                result[index] = min(
                    float(result[index]),
                    max(0.0, float(self.controller.desired_powers[index])),
                )
        return result

    def charge_recovery_active(self):
        return any(self.recovering)

    def is_top_charge_latched(self, index):
        latch = self.top_charge_latches[index]
        if latch is None:
            return False
        block_end = latch.get("block_end")
        if block_end is not None:
            now = datetime.now(block_end.tzinfo)
            if now >= block_end:
                self.top_charge_latches[index] = None
                self.latched[index] = False
                self.recoveries[index] = 0
                self.app.log(
                    f"{self.controller.batteries[index]['name']}: "
                    "BMS-topstop vrijgegeven aan het einde van het "
                    "economische laadblok"
                )
                return False
        soc = self._number(self.controller.batteries[index].get("soc"))
        if soc is not None and soc <= float(latch["soc"]) - 3.0:
            self.top_charge_latches[index] = None
            self.latched[index] = False
            self.recoveries[index] = 0
            self.app.log(
                f"{self.controller.batteries[index]['name']}: "
                "top-of-charge-vergrendeling vrijgegeven; SOC is 3% gedaald"
            )
            return False
        return True

    def update_charge_block_end(self, block_end):
        self.current_charge_block_end = block_end

    def _source(self):
        return "auto" if self.controller.enabled else "manual"

    def _event(
        self,
        level,
        title,
        summary,
        *,
        index=None,
        details=None,
        expected_state=None,
        attempt=None,
    ):
        if self.event_log is None:
            return
        battery_name = None
        if index is not None and index < len(self.controller.batteries):
            battery_name = self.controller.batteries[index].get(
                "name", f"Accu {index + 1}"
            )
        self.event_log.record(
            level,
            title,
            summary,
            battery_index=index,
            battery_name=battery_name,
            details=details,
            expected_state=expected_state,
            attempt=attempt,
        )

    def _number(self, entity):
        if not entity:
            return None
        value = self.app.get_state(entity)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _reset(self):
        # Een nieuwe gewenste toestand moet alle nog ingeplande herstelacties
        # van de vorige toestand ongeldig maken. Anders kan bijvoorbeeld een
        # vertraagde laad-herstelactie alsnog worden uitgevoerd nadat de
        # hoofdregeling inmiddels naar ontladen is overgeschakeld.
        self._generations = [
            generation + 1 for generation in self._generations
        ]
        self._native_generation += 1
        pending = getattr(self.controller, "device_reset_pending", None)
        completer = getattr(self.controller, "complete_device_reset", None)
        if callable(pending) and callable(completer):
            for index in range(len(self.controller.batteries)):
                if pending(index):
                    # De fysieke reset kan al uitgevoerd zijn, maar een nieuwe
                    # hoofdopdracht maakt het oude herstel ongeldig. Geef geen
                    # tweede resetkans voordat herstel echt bewezen is.
                    completer(index, recovered=False)
        self.counts = [0] * len(self.controller.batteries)
        self.recoveries = [0] * len(self.controller.batteries)
        self.latched = [False] * len(self.controller.batteries)
        self.recovering = [False] * len(self.controller.batteries)
