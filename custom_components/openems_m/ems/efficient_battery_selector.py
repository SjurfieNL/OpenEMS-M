# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Efficiënte keuze van één, twee of drie onafhankelijke Marstek-accu's."""

from __future__ import annotations
from .. import ems_paths

import time
from pathlib import Path

from .ems_config import (
    BMS_DISCHARGE_CUTOFF_SOC,
    BATTERY_SELECTOR_DELIVERY_CONFIRMATION_SECONDS,
    BATTERY_DISTRIBUTION_HELPER,
    BATTERY_SELECTOR_DOWN_SECONDS,
    BATTERY_SELECTOR_FOUR_OFF_W,
    BATTERY_SELECTOR_FOUR_ON_W,
    BATTERY_SELECTOR_MIN_POWER_W,
    BATTERY_SELECTOR_ONE_OFF_W,
    BATTERY_SELECTOR_ONE_ON_W,
    BATTERY_SELECTOR_THREE_OFF_W,
    BATTERY_SELECTOR_THREE_ON_W,
    BATTERY_SELECTOR_UP_SECONDS,
)
from .persistence import JsonStore


class EfficientBatterySelector:
    STATUS_SENSOR = "sensor.openems_efficiente_accuselector"
    EFFICIENT_OPTION = "Efficiënte accuselector (aanbevolen)"

    def __init__(
        self,
        app,
        batteries,
        data_file=None,
        clock=None,
        controller=None,
        battery_manager=None,
        settings=None,
        grid_calibrator=None,
    ):
        self.app = app
        self.batteries = batteries
        self.clock = clock or time.monotonic
        self.controller = controller
        self.battery_manager = battery_manager
        self.settings = settings
        self.grid_calibrator = grid_calibrator
        self.data_file = data_file or (
            ems_paths.data_parent()
            / "OpenEMS-M-data"
            / "accuselector.json"
        )
        self.store = JsonStore(
            self.data_file,
            app=app,
            label="Efficiënte accuselector",
        )
        loaded = self.store.load(
            lambda: {"last_used": {}, "last_primary": None}
        )
        self.last_used = dict(loaded.get("last_used") or {})
        self.last_primary = self._optional_int(loaded.get("last_primary"))
        self.active_indexes = []
        self.targets = {}
        self.selected_count = 1
        self.up_since = None
        self.up_level = None
        self.down_since = None
        self.down_level = None
        self.fallback_reason = None
        self.last_reason = "Nog geen accu geselecteerd"
        self.last_demand = 0.0
        self.native_active = False
        self.native_source = "auto"
        self._native_commanded = None
        self._native_verify_after = None
        self._native_mismatch_signature = None
        self._native_mismatch_since = None
        self._native_retries = 0
        self._native_retry_counts = {}
        self._native_latched = False
        self._native_transition_active = False
        self._native_transition_started_at = None
        self._last_announced_selection = ()
        self._pending_selection_events = []

    def enabled(self):
        return (
            str(self.app.get_state(BATTERY_DISTRIBUTION_HELPER)).strip()
            == self.EFFICIENT_OPTION
        )

    def deactivate(self):
        self.native_active = False
        self._native_commanded = None
        self._native_verify_after = None
        self._native_mismatch_signature = None
        self._native_mismatch_since = None
        self._native_retries = 0
        self._native_retry_counts = {}
        self._native_latched = False
        self._native_transition_active = False
        self._native_transition_started_at = None
        self.active_indexes = []
        self.targets = {}
        self.selected_count = 1
        self._reset_timers()
        self._publish("Uitgeschakeld", "Zelfconsumptie is niet actief")

    def activate_native(self, source="auto"):
        """Start selectie terwijl Marstek zelf het anti-feedvermogen regelt."""
        if not self.enabled():
            return False
        if (
            self.controller is None
            or self.battery_manager is None
            or self.settings is None
        ):
            self._enter_fallback(
                "native selector is niet volledig gekoppeld"
            )
            return False
        self.native_active = True
        self.native_source = source
        self._native_commanded = None
        self._native_verify_after = None
        self._native_mismatch_signature = None
        self._native_mismatch_since = None
        self._native_retries = 0
        self._native_retry_counts = {}
        self._native_latched = False
        self._native_transition_active = False
        self._native_transition_started_at = None
        return self.tick_native(force=True)

    def tick_native(self, force=False):
        """Kiest actieve anti-feedaccu's; Marstek bepaalt hun vermogen."""
        if not self.native_active:
            return False
        if not self.enabled():
            self.deactivate()
            return False
        if self._native_latched:
            return self._command_native_selection(
                tuple(range(len(self.batteries)))
            )
        # Houd de eerste keuze vast totdat de overdracht werkelijk bevestigd
        # is. Overgangstelemetrie uit export/stand-by mag geen extra accu laten
        # bijschakelen terwijl de Modbus-reeks nog wordt verwerkt.
        transition_guard = getattr(
            self.controller, "command_sequence_pending", None
        )
        if (
            self._native_transition_active
            and self._native_commanded is not None
            and callable(transition_guard)
        ):
            self._verify_native_if_due()
            if self._native_transition_active:
                return True
        try:
            battery_state = self.battery_manager.read()
            minimum_soc = self._minimum_soc()
            # Een uitgeschakelde accu telt als 0 W: OpenEMS stuurt hem niet en
            # zijn eigen vermogen zit al in de netmeting.
            health = getattr(self.app, "battery_health_manager", None)
            disabled = getattr(health, "is_disabled", None)
            actual = [
                0.0
                if callable(disabled) and disabled(index)
                else self._number(battery.get("actual_power"))
                for index, battery in enumerate(self.batteries)
            ]
            grid = self._native_grid_power()
        except (AttributeError, TypeError, ValueError) as error:
            return self._native_fallback(
                f"meetgegevens konden niet worden gelezen: {error}",
                force=force,
            )
        if grid is None or any(value is None for value in actual):
            return self._native_fallback(
                "actueel accuvermogen of Grid CT ontbreekt",
                force=force,
            )

        self.last_demand = max(0.0, sum(actual) + grid)
        snapshots, problem = self._snapshots(
            battery_state, minimum_soc
        )
        if problem:
            return self._native_fallback(problem, force=force)
        self._leave_fallback()

        eligible = [item for item in snapshots if item["eligible"]]
        if not eligible:
            self.active_indexes = []
            self.targets = {}
            self.selected_count = 1
            self._reset_timers()
        else:
            self._synchronise_active(
                eligible,
                min(max(1, self.selected_count), len(eligible)),
                minimum_soc,
            )
            if self.last_demand < BATTERY_SELECTOR_MIN_POWER_W:
                self.selected_count = 1
                self._reset_timers()
            else:
                self._update_selected_count(eligible)
            desired_count = min(self.selected_count, len(eligible))
            self._synchronise_active(
                eligible, desired_count, minimum_soc
            )
            self._advance_soc_targets(eligible, minimum_soc)
            self._synchronise_active(
                eligible, desired_count, minimum_soc
            )

        selected = tuple(self.active_indexes)
        success = self._command_native_selection(
            selected, force=force
        )
        names = [
            self.batteries[index].get("name", f"Accu {index + 1}")
            for index in selected
        ]
        message = (
            f"{', '.join(names)} op Marstek anti-feed · "
            f"woningvraag {self.last_demand:.0f} W"
            if names else
            "Alle accu’s gestopt: geen veilige accu boven minimum-SOC"
        )
        self._publish(
            "Actief" if names else "Wachten",
            message,
            snapshots=snapshots,
        )
        self._verify_native_if_due()
        return success

    def verification_result(self):
        """Controleert de native selectie: actief anti-feed, rest Force Stop."""
        selected = set(self.active_indexes)
        try:
            battery_state = self.battery_manager.read()
            soc_values = list(battery_state.soc_values)
        except (AttributeError, TypeError, ValueError):
            soc_values = []
        delivery_required = self.last_demand >= BATTERY_SELECTOR_MIN_POWER_W
        selected_delivery = sum(
            max(0.0, self._number(self.batteries[index].get("actual_power")) or 0.0)
            for index in selected
        )
        delivery_missing = (
            delivery_required
            and selected_delivery < BATTERY_SELECTOR_MIN_POWER_W
        )
        details = []
        batteries = []
        failed = []
        for index, battery in enumerate(self.batteries):
            work_mode = str(
                self.app.get_state(battery["work_mode"]) or ""
            ).lower()
            rs485 = str(
                self.app.get_state(battery["rs485_control"]) or ""
            ).lower()
            force_mode = str(
                self.app.get_state(battery["force_mode"]) or ""
            ).lower()
            charge = self._number(battery.get("charge_power"))
            discharge = self._number(battery.get("discharge_power"))
            actual = self._number(battery.get("actual_power"))
            if index in selected:
                soc = self._value(soc_values, index)
                bms_cutoff = (
                    soc is not None
                    and soc <= BMS_DISCHARGE_CUTOFF_SOC
                )
                physical_delivery = (
                    not delivery_missing
                    or bms_cutoff
                )
                # Register 43000 kan bij Venus E3 de vorige user mode blijven
                # terugmelden. Echte ontlading met RS485 uit is dan sterker
                # bewijs voor actieve native anti-feed dan die stale modusread.
                # Dat bewijs moet van DEZE accu komen: levert een andere
                # geselecteerde accu genoeg, dan mag een accu die na RS485-uit
                # naar 'manual' is teruggevallen niet als OK tellen.
                own_delivery = (
                    actual is not None
                    and actual >= BATTERY_SELECTOR_MIN_POWER_W
                )
                mode_confirmed = (
                    work_mode == "anti_feed"
                    or (delivery_required and own_delivery)
                )
                ok = (
                    mode_confirmed
                    and rs485 == "off"
                    and (actual is None or actual >= -100)
                    and physical_delivery
                )
                expected = (
                    "Marstek anti-feed; ontladen door BMS begrensd"
                    if bms_cutoff and delivery_required
                    else "Marstek anti-feed met fysieke levering"
                    if delivery_required
                    else "Marstek anti-feed"
                )
            else:
                # Force Stop zelf en het werkelijke rustvermogen zijn hier
                # leidend. Work mode en RS485 lopen in Home Assistant soms
                # achter terwijl de accu de stopopdracht al heeft uitgevoerd.
                ok = (
                    force_mode in {"standby", "stop"}
                    and charge is not None and charge <= 25
                    and discharge is not None and discharge <= 25
                    and (actual is None or abs(actual) <= 100)
                )
                expected = "Force Stop"
            name = battery.get("name", f"Accu {index + 1}")
            actual_text = (
                f"{actual:.0f} W" if actual is not None else "onbekend"
            )
            details.append(
                f"{name}={'OK' if ok else 'FOUT'} ({expected}; "
                f"mode={work_mode or 'onbekend'}, "
                f"rs485={rs485 or 'onbekend'}, "
                f"force={force_mode or 'onbekend'}, "
                f"laden={charge if charge is not None else 'onbekend'} W, "
                f"ontladen={discharge if discharge is not None else 'onbekend'} W, "
                f"werkelijk={actual_text})"
            )
            batteries.append({
                "index": index,
                "name": name,
                "ok": ok,
                "expected": expected,
                "work_mode": work_mode,
                "rs485": rs485,
                "force_mode": force_mode,
                "actual_power": actual,
            })
            if not ok:
                failed.append(index)
        result = {
            "ok": not failed,
            "summary": " | ".join(details),
            "failed_indexes": failed,
            "batteries": batteries,
        }
        result["delivery_pending"] = bool(
            failed
            and delivery_missing
            and all(
                item["index"] in selected
                and item["work_mode"] == "anti_feed"
                and item["rs485"] == "off"
                and (
                    item["actual_power"] is None
                    or item["actual_power"] >= -100
                )
                for item in batteries if not item["ok"]
            )
        )
        now = self.clock()
        transition_age = (
            now - self._native_transition_started_at
            if self._native_transition_started_at is not None else None
        )
        if (
            result["delivery_pending"]
            and self._native_transition_active
            and transition_age is not None
            and transition_age
            < BATTERY_SELECTOR_DELIVERY_CONFIRMATION_SECONDS
        ):
            result["pending"] = True
            result["summary"] = (
                "Besturing bevestigd; wacht op actuele vermogensmeting "
                f"({transition_age:.0f}/"
                f"{BATTERY_SELECTOR_DELIVERY_CONFIRMATION_SECONDS} s) | "
                f"{result['summary']}"
            )
            return result
        if result["ok"]:
            self._native_mismatch_signature = None
            self._native_mismatch_since = None
            return result
        signature = tuple(
            (
                item["index"], item["work_mode"], item["rs485"],
                item["force_mode"], item["actual_power"],
            )
            for item in batteries if not item["ok"]
        )
        if self._native_commanded is not None and (
            signature != self._native_mismatch_signature
            or self._native_mismatch_since is None
            or now - self._native_mismatch_since < 2.0
        ):
            if signature != self._native_mismatch_signature:
                self._native_mismatch_signature = signature
                self._native_mismatch_since = now
            result["pending"] = True
            result["summary"] = (
                "Afwijking wordt eerst opnieuw uitgelezen | "
                f"{result['summary']}"
            )
            return result
        self._native_mismatch_signature = None
        self._native_mismatch_since = None
        return result

    def _native_fallback(self, reason, force=False):
        self._enter_fallback(reason)
        selected = tuple(range(len(self.batteries)))
        self.active_indexes = list(selected)
        self.selected_count = 1
        success = self._command_native_selection(
            selected, force=force
        )
        self._publish(
            "Tijdelijk teruggevallen",
            f"Alle accu’s gebruiken Marstek anti-feed: {reason}",
        )
        return success

    def _command_native_selection(self, selected, force=False):
        if not force and selected == self._native_commanded:
            return True
        changed = selected != self._native_commanded
        if force:
            self.controller.invalidate_last_command(
                "native accuselector opnieuw toepassen"
            )
        success = self.controller.set_native_self_consumption_selection(
            selected,
            source="selector",
        )
        if success:
            self._native_commanded = selected
            verification_delay = (
                self.controller.recommended_verification_delay(minimum=10)
            )
            self._native_verify_after = self.clock() + verification_delay
            self._native_mismatch_signature = None
            self._native_mismatch_since = None
            self._native_transition_active = True
            if changed or self._native_transition_started_at is None:
                self._native_transition_started_at = self.clock()
            if changed:
                self._native_retries = 0
                self._native_retry_counts = {}
        return success

    def _verify_native_if_due(self):
        if (
            self._native_verify_after is None
            or self.clock() < self._native_verify_after
        ):
            return
        pending = getattr(self.controller, "command_sequence_pending", None)
        if not callable(pending):
            pending = getattr(self.controller, "atomic_batch_pending", None)
        if callable(pending) and pending():
            self._native_verify_after = self.clock() + 2.0
            # De CT-wachttijd begint pas nadat de laatste queued Modbus-reeks
            # werkelijk vrij is; wachtrijtijd is geen verwerkingstijd voor de
            # geselecteerde accu.
            self._native_transition_started_at = self.clock()
            return
        result = self.verification_result()
        if result.get("pending"):
            self._native_verify_after = self.clock() + 2.0
            return
        if result["ok"]:
            if self._native_retries:
                self._decision(
                    "Accuselector hersteld",
                    "Native anti-feedselectie is opnieuw volledig bevestigd",
                    level="recovered",
                )
            self._native_retries = 0
            self._native_retry_counts = {}
            self._native_verify_after = None
            self._native_transition_active = False
            self._native_transition_started_at = None
            return
        # Zijn anti-feed en RS485 al goed, dan kan uitsluitend de interne
        # CT-regeling nog aan het opbouwen zijn. Geef Marstek daarvoor maximaal
        # 30 seconden vanaf de selectie zonder een herstelwrite te sturen.
        transition_age = (
            self.clock() - self._native_transition_started_at
            if self._native_transition_started_at is not None else 999.0
        )
        if result.get("delivery_pending") and transition_age < 30.0:
            self._native_verify_after = self.clock() + 2.0
            self._native_mismatch_signature = None
            self._native_mismatch_since = None
            return
        failed = set(map(int, result.get("failed_indexes", [])))
        # Alleen opeenvolgende bevestigde afwijkingen van dezelfde accu tellen
        # samen. Een herstelde accu wist zijn eigen historie, zodat vertraagde
        # HA-statussen van verschillende accu's nooit één globale foutreeks
        # vormen.
        self._native_retry_counts = {
            index: count
            for index, count in self._native_retry_counts.items()
            if index in failed
        }
        for index in failed:
            self._native_retry_counts[index] = (
                self._native_retry_counts.get(index, 0) + 1
            )
        self._native_retries = max(
            self._native_retry_counts.values(), default=0
        )
        exhausted = [
            index for index, count in self._native_retry_counts.items()
            if count > 3
        ]
        if not exhausted:
            attempts = ", ".join(
                f"{self.batteries[index].get('name', f'Accu {index + 1}')} "
                f"{self._native_retry_counts[index]}/3"
                for index in sorted(failed)
            )
            self._decision(
                "Accuselector herstelt selectie",
                f"Verificatie mislukt; herstelpoging per accu: "
                f"{attempts} | {result['summary']}",
                level="warning",
            )
            repair = getattr(
                self.controller,
                "repair_native_self_consumption_selection",
                None,
            )
            if callable(repair):
                success = repair(
                    tuple(self.active_indexes),
                    sorted(failed),
                    source="selector",
                )
                if success:
                    self._native_commanded = tuple(self.active_indexes)
                    self._native_verify_after = (
                        self.clock()
                        + self.controller.recommended_verification_delay(
                            minimum=10
                        )
                    )
                    self._native_mismatch_signature = None
                    self._native_mismatch_since = None
            else:
                self._command_native_selection(
                    tuple(self.active_indexes), force=True
                )
            return
        health = getattr(self.app, "battery_health_manager", None)
        if health is not None:
            for index in exhausted:
                health.quarantine(
                    index,
                    "Native anti-feedselectie bleef na drie "
                    "herstelpogingen afwijken",
                    attempts=3,
                )
            self._native_retry_counts = {}
            self._native_retries = 0
            self._native_verify_after = None
            # De mislukte selectie stond nog als lopende overdracht vast.
            # Wis die overgang volledig, zodat tick_native de uitgesloten accu
            # werkelijk verwijdert en meteen een volgende kandidaat kiest.
            self._native_transition_active = False
            self._native_transition_started_at = None
            self._native_commanded = None
            self._decision(
                "Accu tijdelijk buiten gebruik",
                "Afwijkende accu is vijftien minuten uitgesloten; de selector "
                "schakelt over naar de volgende geschikte accu",
                level="error",
            )
            return self.tick_native(force=True)
        self._native_latched = True
        self.active_indexes = list(range(len(self.batteries)))
        self.selected_count = len(self.active_indexes)
        self._decision(
            "Accuselector tijdelijk teruggevallen",
            "Selectie kon niet betrouwbaar worden bevestigd; alle accu’s "
            "zijn teruggezet op Marstek anti-feed",
            level="error",
        )
        self._command_native_selection(
            tuple(self.active_indexes), force=True
        )
        self._publish(
            "Tijdelijk teruggevallen",
            "Alle accu’s gebruiken Marstek anti-feed na mislukte verificatie",
        )

    def _native_grid_power(self):
        if self.grid_calibrator is not None:
            return float(self.grid_calibrator.corrected_grid_power())
        entity = self.settings.grid_entity()
        value = self._number(entity)
        if value is None:
            return None
        sign = getattr(self.settings, "grid_import_sign", lambda: 1.0)()
        return value * float(sign)

    def _minimum_soc(self):
        value = self._number("input_number.marstek_minimum_soc")
        return 20.0 if value is None else value

    def allocate(
        self, requested, battery_state, minimum_soc, demand_power=None
    ):
        """Geeft efficiënte ontlaadvermogens of None voor de oude verdeler."""
        control_power = max(0.0, float(requested))
        self.last_demand = max(
            0.0,
            float(
                control_power if demand_power is None else demand_power
            ),
        )
        if not self.enabled():
            self._reset_timers()
            self._publish(
                "Uitgeschakeld",
                "Alle accu’s verdelen is geselecteerd",
            )
            return None

        snapshots, problem = self._snapshots(battery_state, minimum_soc)
        if problem:
            self._enter_fallback(problem)
            return None
        self._leave_fallback()

        eligible = [item for item in snapshots if item["eligible"]]
        if not eligible or self.last_demand < BATTERY_SELECTOR_MIN_POWER_W:
            self._reset_timers()
            self.selected_count = 1
            if eligible and self.active_indexes:
                self._synchronise_active(
                    eligible, 1, minimum_soc
                )
            else:
                self.active_indexes = []
                self.targets = {}
            self._publish(
                "Wachten",
                "Geen ontlaadvermogen nodig"
                if eligible else "Geen veilige accu boven minimum-SOC",
                snapshots=snapshots,
            )
            return [0.0 for _ in self.batteries]

        self._synchronise_active(
            eligible,
            min(self.selected_count, len(eligible)),
            minimum_soc,
        )
        self._update_selected_count(eligible)
        desired_count = min(self.selected_count, len(eligible))
        self._synchronise_active(eligible, desired_count, minimum_soc)
        self._advance_soc_targets(eligible, minimum_soc)
        self._synchronise_active(eligible, desired_count, minimum_soc)

        powers = self._distribute(
            control_power,
            self.active_indexes,
            battery_state.max_discharge_values,
        )
        active_names = [
            self.batteries[index].get("name", f"Accu {index + 1}")
            for index in self.active_indexes
        ]
        message = (
            f"{', '.join(active_names)} actief · "
            f"vraag {self.last_demand:.0f} W"
        )
        self._publish("Actief", message, snapshots=snapshots, powers=powers)
        return powers

    def allocate_export(
        self, requested, battery_state, reserve_soc, desired_count=None
    ):
        """Compatibele exportverdeling zonder SOC-rondes of SOC-wissels.

        De GridExportManager houdt tijdens normale uitvoering zelf de vaste
        blokselectie bij. Deze compatibiliteitsroute gebruikt dezelfde
        deterministische basisvolgorde: minste cycli, daarna accunummer.
        """
        requested = max(0.0, float(requested or 0.0))
        order = self.export_order(battery_state, reserve_soc)
        powers = [0.0 for _ in self.batteries]
        if requested <= 0.0 or not order:
            self.active_indexes = []
            return powers
        count = max(1, int(desired_count or 1))
        self.active_indexes = list(order[:min(count, len(order))])
        return self._distribute(
            requested,
            self.active_indexes,
            battery_state.max_discharge_values,
        )

    def export_order(self, battery_state, reserve_soc):
        """Geef voor export een vaste rangorde: minste cycli, daarna index."""
        if not self.enabled():
            return []
        snapshots, problem = self._snapshots(battery_state, reserve_soc)
        if problem:
            return []
        candidates = [item for item in snapshots if item["eligible"]]
        return [
            item["index"] for item in sorted(
                candidates,
                key=lambda item: (float(item["cycles"]), int(item["index"])),
            )
        ]

    def _snapshots(self, battery_state, minimum_soc):
        result = []
        problems = []
        for index, battery in enumerate(self.batteries):
            soc = self._value(battery_state.soc_values, index)
            cycles = self._number(battery.get("cycles"))
            charged = self._number(battery.get("total_charging_energy"))
            discharged = self._number(
                battery.get("total_discharging_energy"), required=False
            )
            temperature = self._number(battery.get("temperature"))
            required = {
                "SOC": soc,
                "cycli": cycles,
                "totaal geladen kWh": charged,
                "temperatuur": temperature,
            }
            for label, value in required.items():
                if value is None:
                    problems.append(
                        f"{battery.get('name', f'Accu {index + 1}')} "
                        f"{label} ontbreekt"
                    )
            safe, safety_reason = self._safe(index)
            throughput = (
                charged + discharged
                if charged is not None and discharged is not None
                else charged
            )
            result.append({
                "index": index,
                "name": battery.get("name", f"Accu {index + 1}"),
                "soc": soc,
                "cycles": cycles,
                "throughput": throughput,
                "temperature": temperature,
                "max_power": self._value(
                    battery_state.max_discharge_values, index
                ) or 0.0,
                "eligible": bool(
                    soc is not None
                    and soc > float(minimum_soc)
                    and safe
                ),
                "safety_reason": safety_reason,
            })
        return result, "; ".join(problems)

    def _safe(self, index):
        health = getattr(self.app, "battery_health_manager", None)
        if health is not None:
            if not health.is_available(index):
                return False, "tijdelijk buiten gebruik"
        service = getattr(self.app, "safety_service", None)
        checker = getattr(service, "is_battery_operation_safe", None)
        if not callable(checker):
            return True, ""
        assessment = checker(index, "forced_discharge")
        return bool(assessment.safe), str(assessment.reason or "")

    def _update_selected_count(self, eligible):
        now = self.clock()
        by_index = {item["index"]: item for item in eligible}
        active_capacity = sum(
            float(by_index[index]["max_power"])
            for index in self.active_indexes
            if index in by_index
        )
        capacity_count = max(1, len(self.active_indexes))
        remaining_maximums = sorted(
            (
                float(item["max_power"])
                for item in eligible
                if item["index"] not in self.active_indexes
            ),
            reverse=True,
        )
        for value in remaining_maximums:
            if active_capacity + 1 >= self.last_demand:
                break
            active_capacity += value
            capacity_count += 1
        if capacity_count > self.selected_count:
            self.selected_count = capacity_count
            self._reset_timers()
            self._decision(
                "Accu bijgeschakeld",
                f"Direct opgeschaald naar {capacity_count} accu’s omdat "
                "de actieve vermogenscapaciteit anders onvoldoende is",
            )

        up_target = (
            4 if self.last_demand > BATTERY_SELECTOR_FOUR_ON_W
            else 3 if self.last_demand > BATTERY_SELECTOR_THREE_ON_W
            else 2 if self.last_demand > BATTERY_SELECTOR_ONE_ON_W
            else None
        )
        if up_target is not None:
            # Nooit meer accu's vragen dan er beschikbaar zijn (bijv. 1 accu).
            up_target = min(up_target, len(eligible))
        if up_target is not None and up_target > self.selected_count:
            if self.up_level != up_target:
                self.up_level = up_target
                self.up_since = now
            elif now - self.up_since >= BATTERY_SELECTOR_UP_SECONDS:
                self.selected_count = min(up_target, len(eligible))
                self.up_level = None
                self.up_since = None
                self.down_level = None
                self.down_since = None
                self._decision(
                    "Accu bijgeschakeld",
                    f"{self.selected_count} accu’s actief na "
                    f"{BATTERY_SELECTOR_UP_SECONDS:.0f} seconden boven "
                    f"{ {2: BATTERY_SELECTOR_ONE_ON_W, 3: BATTERY_SELECTOR_THREE_ON_W, 4: BATTERY_SELECTOR_FOUR_ON_W}[up_target]:.0f} W",
                )
        else:
            self.up_level = None
            self.up_since = None

        down_target = (
            3 if self.selected_count >= 4
            and self.last_demand < BATTERY_SELECTOR_FOUR_OFF_W
            else 2 if self.selected_count == 3
            and self.last_demand < BATTERY_SELECTOR_THREE_OFF_W
            else 1 if self.selected_count == 2
            and self.last_demand < BATTERY_SELECTOR_ONE_OFF_W
            else None
        )
        if down_target is not None:
            if self.down_level != down_target:
                self.down_level = down_target
                self.down_since = now
            elif now - self.down_since >= BATTERY_SELECTOR_DOWN_SECONDS:
                self.selected_count = down_target
                self.down_level = None
                self.down_since = None
                self.up_level = None
                self.up_since = None
                self._decision(
                    "Accu uitgeschakeld",
                    f"Teruggeschaald naar {down_target} accu"
                    f"{'’s' if down_target != 1 else ''} na "
                    f"{BATTERY_SELECTOR_DOWN_SECONDS:.0f} seconden laag "
                    "vermogen",
                )
        else:
            self.down_level = None
            self.down_since = None

        self.selected_count = max(
            1, min(self.selected_count, len(eligible))
        )

    def _synchronise_active(self, eligible, desired_count, minimum_soc):
        eligible_indexes = {item["index"] for item in eligible}
        removed = [
            index for index in self.active_indexes
            if index not in eligible_indexes
        ]
        self.active_indexes = [
            index for index in self.active_indexes
            if index in eligible_indexes
        ]
        for index in removed:
            self.targets.pop(index, None)
        while len(self.active_indexes) > desired_count:
            removed_index = self.active_indexes.pop()
            self.targets.pop(removed_index, None)
        while len(self.active_indexes) < desired_count:
            candidates = [
                item for item in eligible
                if item["index"] not in self.active_indexes
            ]
            if not candidates:
                break
            previous = (
                self.active_indexes[0]
                if self.active_indexes else self.last_primary
            )
            chosen = self._choose(candidates, previous)
            self.active_indexes.append(chosen["index"])
            self.targets[chosen["index"]] = self._initial_target(
                chosen["soc"], minimum_soc
            )
            self._mark_used(chosen["index"])
            self.last_reason = self._selection_reason(chosen, candidates)
            self._pending_selection_events.append((
                chosen, self.last_reason
            ))
        if self.active_indexes:
            primary = self.active_indexes[0]
            if self.last_primary != primary:
                self.last_primary = primary
                self._save_state()
        self._announce_active_selection()

    def _advance_soc_targets(self, eligible, minimum_soc):
        by_index = {item["index"]: item for item in eligible}
        for position, index in list(enumerate(self.active_indexes)):
            current = by_index.get(index)
            if current is None:
                continue
            target = float(
                self.targets.get(
                    index,
                    self._initial_target(current["soc"], minimum_soc),
                )
            )
            if current["soc"] > target + 0.1:
                continue
            replacements = [
                item for item in eligible
                if item["index"] not in self.active_indexes
                and item["soc"] > target + 0.1
            ]
            if replacements:
                chosen = self._choose(replacements, index)
                self.active_indexes[position] = chosen["index"]
                self.targets.pop(index, None)
                self.targets[chosen["index"]] = target
                self._mark_used(chosen["index"])
                self.last_reason = self._selection_reason(
                    chosen, replacements
                )
                self._decision(
                    "Accu gewisseld",
                    f"{current['name']} bereikte {target:.0f}%; "
                    f"{chosen['name']} neemt over",
                    chosen,
                )
                self._last_announced_selection = tuple(
                    self.active_indexes
                )
                self._pending_selection_events = []
                continue
            lower = self._lower_target(target, minimum_soc)
            if lower < target - 0.1:
                next_round = [
                    item for item in eligible
                    if (
                        item["index"] == index
                        or item["index"] not in self.active_indexes
                    )
                    and item["soc"] > lower + 0.1
                ]
                chosen = self._choose(next_round, index)
                if chosen["index"] != index:
                    self.active_indexes[position] = chosen["index"]
                    self.targets.pop(index, None)
                    self.targets[chosen["index"]] = lower
                    self._mark_used(chosen["index"])
                    self.last_reason = self._selection_reason(
                        chosen, next_round
                    )
                    self._decision(
                        "Accu gewisseld",
                        f"Alle accu’s bereikten {target:.0f}%; "
                        f"{chosen['name']} start de ronde tot {lower:.0f}%",
                        chosen,
                    )
                    self._last_announced_selection = tuple(
                        self.active_indexes
                    )
                    self._pending_selection_events = []
                else:
                    self.targets[index] = lower
                    self.last_reason = (
                        f"Alle accu’s bereikten {target:.0f}%; "
                        f"{current['name']} blijft actief tot {lower:.0f}%"
                    )

    def _announce_active_selection(self):
        """Log alleen een echte wijziging van de logische accukeuze."""
        selected = tuple(self.active_indexes)
        if selected == self._last_announced_selection:
            self._pending_selection_events = []
            return
        previously_selected = set(self._last_announced_selection)
        pending = {
            int(item["index"]): (item, reason)
            for item, reason in self._pending_selection_events
        }
        for index in selected:
            if index in previously_selected:
                continue
            battery, reason = pending.get(index, (
                {
                    "index": index,
                    "name": self.batteries[index].get(
                        "name", f"Accu {index + 1}"
                    ),
                },
                self.last_reason,
            ))
            self._decision(
                "Accu geselecteerd",
                f"{battery['name']} geselecteerd: {reason}",
                battery,
            )
        self._last_announced_selection = selected
        self._pending_selection_events = []

    def _choose(self, candidates, previous):
        count = len(self.batteries)

        def logical_distance(index):
            if previous is None:
                return index
            distance = (index - int(previous)) % count
            return distance if distance > 0 else count

        return min(
            candidates,
            key=lambda item: (
                self._soc_round_rank(item["soc"]),
                float(item["cycles"]),
                -float(item["soc"]),
                float(item["throughput"]),
                float(item["temperature"]),
                float(self.last_used.get(str(item["index"]), 0.0)),
                logical_distance(item["index"]),
            ),
        )

    @staticmethod
    def _selection_reason(chosen, candidates):
        if len(candidates) == 1:
            return "enige beschikbare kandidaat"
        selected_round = EfficientBatterySelector._soc_round_rank(
            chosen["soc"]
        )
        round_candidates = [
            item for item in candidates
            if EfficientBatterySelector._soc_round_rank(item["soc"])
            == selected_round
        ]
        round_prefix = (
            "hoogste SOC-ronde, "
            if len(round_candidates) < len(candidates) else ""
        )
        if len(round_candidates) == 1:
            return f"{round_prefix}enige kandidaat in deze ronde"
        minimum_cycles = min(
            item["cycles"] for item in round_candidates
        )
        if chosen["cycles"] == minimum_cycles and sum(
            item["cycles"] == minimum_cycles for item in round_candidates
        ) == 1:
            return f"{round_prefix}minste cycli"
        highest_soc = max(
            item["soc"] for item in candidates
            if (
                EfficientBatterySelector._soc_round_rank(item["soc"])
                == selected_round
                and item["cycles"] == minimum_cycles
            )
        )
        if chosen["soc"] == highest_soc:
            return f"{round_prefix}gelijke cycli, hoogste SOC"
        return f"{round_prefix}gelijke waarden, logische vervolgkeuze"

    @staticmethod
    def _soc_round_rank(soc):
        """Werk eerst 50%, daarna 25%, daarna minimum-SOC af."""
        value = float(soc)
        if value > 50.0:
            return 0
        if value > 25.0:
            return 1
        return 2

    def _mark_used(self, index):
        stamp = time.time()
        self.last_used[str(index)] = stamp
        self._save_state()

    def _save_state(self):
        self.store.save({
            "last_used": self.last_used,
            "last_primary": self.last_primary,
        })

    def _enter_fallback(self, reason):
        if self.fallback_reason != reason:
            self.fallback_reason = reason
            self._decision(
                "Accuselector tijdelijk teruggevallen",
                reason,
                level="warning",
            )
        self._reset_timers()
        self._publish(
            "Tijdelijk teruggevallen",
            f"Alle accu’s verdelen: {reason}",
        )

    def _leave_fallback(self):
        if self.fallback_reason is None:
            return
        previous = self.fallback_reason
        self.fallback_reason = None
        self._decision(
            "Accuselector hersteld",
            f"Alle selectiegegevens zijn weer beschikbaar; eerdere reden: "
            f"{previous}",
            level="recovered",
        )

    def _decision(self, title, summary, battery=None, level="info"):
        self.app.log(f"[Accuselector] {summary}")
        event_log = getattr(self.app, "runtime_event_log", None)
        recorder = getattr(event_log, "record", None)
        if callable(recorder):
            recorder(
                level,
                title,
                summary,
                battery_index=(
                    battery.get("index") if battery is not None else None
                ),
                battery_name=(
                    battery.get("name") if battery is not None else None
                ),
                expected_state="self_consumption",
            )

    def _publish(
        self, state, message, snapshots=None, powers=None
    ):
        now = self.clock()
        timer = None
        if self.up_since is not None:
            timer = {
                "richting": "opschalen",
                "doel_aantal": self.up_level,
                "verstreken_s": round(now - self.up_since),
                "vereist_s": BATTERY_SELECTOR_UP_SECONDS,
            }
        elif self.down_since is not None:
            timer = {
                "richting": "afschalen",
                "doel_aantal": self.down_level,
                "verstreken_s": round(now - self.down_since),
                "vereist_s": BATTERY_SELECTOR_DOWN_SECONDS,
            }
        snapshot_map = {
            item["index"]: item for item in (snapshots or [])
        }
        active = []
        for index in self.active_indexes if snapshots is not None else []:
            item = snapshot_map.get(index, {})
            active.append({
                "index": index,
                "naam": self.batteries[index].get(
                    "name", f"Accu {index + 1}"
                ),
                "soc": item.get("soc"),
                "doel_soc": self.targets.get(index),
                "vermogen_w": (
                    powers[index]
                    if powers is not None and index < len(powers)
                    else None
                ),
            })
        self.app.set_state(
            self.STATUS_SENSOR,
            state=state,
            attributes={
                "friendly_name": "OpenEMS efficiënte accuselector",
                "icon": "mdi:battery-sync-outline",
                "melding": message,
                "modus": (
                    "Efficiënte accuselector"
                    if self.enabled() else "Alle accu’s verdelen"
                ),
                "vermogensregeling": (
                    "Marstek anti-feed"
                    if self.native_active else "OpenEMS"
                ),
                "berekende_vraag_w": round(self.last_demand),
                "aantal_actief": len(active),
                "actieve_accus": active,
                "selectiereden": self.last_reason,
                "timer": timer,
                "terugvalreden": self.fallback_reason,
                "drempels_w": {
                    "tweede_aan": BATTERY_SELECTOR_ONE_ON_W,
                    "tweede_uit": BATTERY_SELECTOR_ONE_OFF_W,
                    "derde_aan": BATTERY_SELECTOR_THREE_ON_W,
                    "derde_uit": BATTERY_SELECTOR_THREE_OFF_W,
                    "vierde_aan": BATTERY_SELECTOR_FOUR_ON_W,
                    "vierde_uit": BATTERY_SELECTOR_FOUR_OFF_W,
                },
            },
            replace=True,
        )

    def _reset_timers(self):
        self.up_since = None
        self.up_level = None
        self.down_since = None
        self.down_level = None

    @staticmethod
    def _distribute(total, indexes, maximums):
        result = [0.0 for _ in maximums]
        remaining = max(0.0, float(total))
        open_indexes = list(indexes)
        while open_indexes and remaining > 0.5:
            share = remaining / len(open_indexes)
            next_open = []
            delivered = 0.0
            for index in open_indexes:
                spare = max(0.0, float(maximums[index]) - result[index])
                addition = min(share, spare)
                result[index] += addition
                delivered += addition
                if spare - addition > 0.5:
                    next_open.append(index)
            if delivered <= 0.5:
                break
            remaining -= delivered
            open_indexes = next_open
        return result

    @staticmethod
    def _initial_target(soc, minimum_soc):
        if float(soc) > 50.0:
            return 50.0
        if float(soc) > 25.0 and float(minimum_soc) < 25.0:
            return 25.0
        return float(minimum_soc)

    @staticmethod
    def _lower_target(target, minimum_soc):
        minimum = float(minimum_soc)
        if target > 25.0 and minimum < 25.0:
            return 25.0
        return minimum

    def _number(self, entity_id, required=True):
        if not entity_id:
            return None
        value = self.app.get_state(entity_id)
        if value in (None, "", "unknown", "unavailable"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _value(values, index):
        try:
            return float(values[index])
        except (IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
