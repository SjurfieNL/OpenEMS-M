"""Closed-loop Energy Scheduler voor OpenEMS-M."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional

from models import EMSContext, SchedulerState
from ems_config import (
    TARGET_SOC_FULL_TOLERANCE_PERCENT,
    TARGET_SOC_RELEASE_HYSTERESIS_PERCENT,
)


class EnergyScheduler:
    """Bepaalt, activeert en verifieert noodzakelijke state-overgangen."""

    def __init__(
        self,
        app,
        controller,
        verification_delay=8,
        retry_delay=30,
        max_verification_retries=1,
    ):
        self.app = app
        self.controller = controller
        self.verification_delay = max(1, int(verification_delay))
        self.retry_delay = max(1, int(retry_delay))
        self.max_verification_retries = max(
            0, int(max_verification_retries)
        )

        self.current_state = SchedulerState.IDLE
        self.previous_state = SchedulerState.IDLE
        self.last_reason = "Scheduler gestart"
        self.last_execution = "Nog geen actie uitgevoerd"
        self.last_transition: Optional[datetime] = None
        self.last_controller_action = "Nog geen controlleractie"
        self.last_verification = "Nog niet gecontroleerd"
        self.verification_ok: Optional[bool] = None
        self._verification_generation = 0
        self._retry_generation = 0
        self._retry_pending = False
        self._retry_state: Optional[SchedulerState] = None
        self._verification_at: Optional[datetime] = None
        self._failed_state_latch: Optional[SchedulerState] = None
        self._retry_context: Optional[EMSContext] = None
        self._telemetry_blocked = False
        self._external_handoff_pending = False
        self.locked_charge_powers = None
        self.target_charge_latches = {}
        self.battery_statuses = [
            {
                "naam": battery.get("name", f"Accu {index + 1}"),
                "status": "ONBEKEND",
                "details": "Automatische regeling heeft nog niet gecontroleerd",
            }
            for index, battery in enumerate(
                getattr(self.controller, "batteries", [])
            )
        ]

    def determine(self, context: EMSContext) -> SchedulerState:
        """Bepaalt de gewenste toestand zonder een accucommando te sturen."""
        desired_state, reason = self._desired_state(context)
        context.scheduler_state = desired_state
        context.scheduler_reason = reason
        return desired_state

    def evaluate(
        self,
        context: EMSContext,
        automatic_allowed: bool = True,
    ) -> SchedulerState:
        desired_state = self.determine(context)
        reason = context.scheduler_reason

        if desired_state == SchedulerState.CHARGING:
            context.charge_distribution = self._active_charge_powers(context)
        else:
            self.reset_charge_session()

        state_changed = desired_state != self.current_state
        charge_changed = (
            desired_state == SchedulerState.CHARGING
            and tuple(round(value) for value in context.charge_distribution)
            != tuple(
                round(value)
                for value in (self.locked_charge_powers or [])
            )
        )
        recovery_required = (
            self._telemetry_blocked or self._external_handoff_pending
        )
        runtime_monitor = getattr(
            self.app, "runtime_safety_monitor", None
        )
        if (
            desired_state == SchedulerState.CHARGING
            and runtime_monitor is not None
            and runtime_monitor.charge_recovery_active()
        ):
            self.last_execution = (
                "Wacht op begrensde herstelcontrole van individuele accu"
            )
            self.last_reason = reason
            return desired_state

        if (
            self._failed_state_latch is not None
            and desired_state == self._failed_state_latch
        ):
            self.last_execution = (
                "Geen nieuwe opdracht: verificatiefout blijft vergrendeld "
                "tot de gewenste state verandert"
            )
            self.last_reason = reason
            return desired_state

        if self._failed_state_latch is not None:
            self.app.log(
                "[Scheduler] Verificatiefout vrijgegeven: gewenste state is veranderd"
            )
            self._failed_state_latch = None

        if self._retry_pending:
            if desired_state == self._retry_state:
                self.last_execution = "Wacht op eenmalige verificatieherkansing"
                self.last_reason = reason
                return desired_state
            self.cancel_retry("gewenste state is veranderd")

        if not self.controller.enabled:
            self.last_execution = (
                "Observatiemodus: AUTO_CONTROL_ENABLED staat uit"
            )
            if state_changed or reason != self.last_reason:
                self._log_transition(desired_state, reason, executed=False)
            self.previous_state = self.current_state
            self.current_state = desired_state
            self.last_reason = reason
            return desired_state

        if not automatic_allowed:
            self.last_execution = "Automatische uitvoering gepauzeerd door testmodus"
            if state_changed or reason != self.last_reason:
                self._log_transition(desired_state, reason, executed=False)
            self.last_reason = reason
            return desired_state

        if not state_changed and not charge_changed and not recovery_required:
            self.last_execution = "Geen actie: gewenste state is al actief"
            self.last_reason = reason
            return desired_state

        previous = self.current_state
        success = self._execute_transition(desired_state, context)

        if not success:
            self.last_execution = (
                f"Overgang naar {desired_state.value} niet uitgevoerd"
            )
            self.last_controller_action = "Controlleropdracht mislukt"
            self.last_verification = "Niet uitgevoerd"
            self.verification_ok = False
            self._set_all_battery_statuses(
                "FOUT", "Automatische controlleropdracht is niet uitgevoerd"
            )
            self.app.log(
                "[Scheduler] Controller heeft de gewenste overgang niet uitgevoerd",
                level="WARNING",
            )
            return desired_state

        self.previous_state = previous
        self.current_state = desired_state
        self._telemetry_blocked = False
        self._external_handoff_pending = False
        self.last_reason = reason
        self.last_transition = context.now
        self.last_controller_action = self._controller_action_name(desired_state)
        self.last_execution = (
            "Individuele laadvermogens bijgewerkt"
            if not state_changed and charge_changed
            else (
                f"Overgang uitgevoerd: {previous.value} -> "
                f"{desired_state.value}"
            )
        )
        self.last_verification = "Controle ingepland"
        self.verification_ok = None
        self._set_all_battery_statuses(
            "CONTROLEREN", "Opdracht verstuurd; wacht op automatische controle"
        )
        self._log_transition(desired_state, reason, executed=True)
        self._schedule_verification(desired_state, context, attempt=0)
        return desired_state

    def status(self) -> dict:
        """Geeft een compact snapshot voor logging en dashboards."""
        battery_statuses = [
            dict(item) for item in self.battery_statuses
        ]
        monitor = getattr(self.app, "runtime_safety_monitor", None)
        if monitor is not None:
            for index, item in enumerate(battery_statuses):
                if monitor.is_top_charge_latched(index):
                    item["status"] = "BMS TOPSTOP"
                    item["details"] = (
                        "Nagenoeg vol; nieuwe laadpogingen geblokkeerd "
                        "tot het laadblok eindigt of SOC 3% is gedaald"
                    )
        for index, latch in list(self.target_charge_latches.items()):
            if index >= len(battery_statuses):
                continue
            battery_statuses[index]["status"] = "DOEL BEREIKT"
            battery_statuses[index]["details"] = (
                f"Doelzone bereikt bij {latch['soc']:.1f}%; "
                f"geblokkeerd tot {latch['block_end'].strftime('%H:%M')} "
                "of tot de SOC 3% daalt"
            )
        return {
            "current_state": self.current_state.value,
            "previous_state": self.previous_state.value,
            "last_transition": self.last_transition,
            "last_reason": self.last_reason,
            "last_execution": self.last_execution,
            "last_controller_action": self.last_controller_action,
            "last_verification": self.last_verification,
            "verification_ok": self.verification_ok,
            "retry_pending": self._retry_pending,
            "verification_at": (
                self._verification_at.isoformat()
                if self._verification_at is not None
                else None
            ),
            "fault_latched": self._failed_state_latch is not None,
            "telemetry_blocked": self._telemetry_blocked,
            "external_handoff_pending": self._external_handoff_pending,
            "battery_statuses": battery_statuses,
        }

    def mark_telemetry_blocked(self, freshness):
        """Maakt een sensorblokkade zichtbaar en herstelbaar."""
        self._telemetry_blocked = True
        problems = list(freshness.get("problems", []))
        per_battery = {}
        general = []
        for item in problems:
            reason = item.get("reden") or "Kritieke sensordata niet actueel"
            index = item.get("accu_index")
            if index is None:
                general.append(reason)
            else:
                per_battery.setdefault(int(index), []).append(reason)
        for index, status in enumerate(self.battery_statuses):
            reasons = per_battery.get(index, [])
            if reasons:
                status["status"] = "SENSORDATA TE OUD"
                status["details"] = " | ".join(reasons)
            else:
                status["status"] = "GEBLOKKEERD"
                status["details"] = (
                    "Automatische regeling wacht op actuele kritieke "
                    "sensordata"
                )
        message = " | ".join(
            general + [
                reason
                for reasons in per_battery.values()
                for reason in reasons
            ]
        )
        self.last_execution = (
            "Geen nieuwe opdracht: "
            + (message or "kritieke sensordata niet actueel")
        )
        self.last_verification = "Geblokkeerd door sensordata"
        self.verification_ok = None
        self._notify_status_changed()

    def telemetry_blocked(self):
        return self._telemetry_blocked

    def _execute_transition(
        self,
        state: SchedulerState,
        context: EMSContext,
    ) -> bool:
        if state == SchedulerState.CHARGING:
            powers = list(context.charge_distribution)
            success = self.controller.set_charge(
                powers,
                source="auto",
            )
            if success:
                self.locked_charge_powers = powers
            return success

        if state in {
            SchedulerState.DISCHARGING,
            SchedulerState.SELF_CONSUMPTION,
        }:
            return self.controller.set_self_consumption(source="auto")

        if state in {SchedulerState.IDLE, SchedulerState.STOPPED}:
            return self.controller.set_stop(source="auto")

        return False

    def reset_charge_session(self) -> None:
        self.locked_charge_powers = None

    def reset_fault(self) -> None:
        """Wist een foutvergrendeling bij bewust herstarten van OpenEMS."""
        self._failed_state_latch = None
        self.cancel_retry("scheduler opnieuw gestart")

    def mark_external_stop(self, reason: str) -> None:
        """Synchroniseer de scheduler nadat een andere regelaar de accu's stopt."""
        self.cancel_retry("controller is door terugleverregeling gestopt")
        self._external_handoff_pending = False
        self.previous_state = self.current_state
        self.current_state = SchedulerState.STOPPED
        self.last_reason = str(reason)
        self.last_execution = (
            "Controller extern gestopt; actuele planning wordt opnieuw uitgevoerd"
        )

    def mark_external_handoff(self, reason: str) -> None:
        """Maak een directe overgang uit export mogelijk zonder tussenstop."""
        self.cancel_retry("terugleverregeling draagt direct over")
        # Dwing evaluate() de geplande volgende state ook te sturen wanneer
        # die gelijk is aan de laatst bekende schedulerstate.
        self._external_handoff_pending = True
        self.last_reason = str(reason)
        self.last_execution = (
            "Terugleverregeling vrijgegeven; directe overgang naar actuele "
            "planning wordt uitgevoerd"
        )

    def cancel_retry(self, reason="uitvoering gepauzeerd") -> None:
        if self._retry_pending:
            self.app.log(f"[Scheduler] Herkansing geannuleerd: {reason}")
        self._retry_generation += 1
        self._verification_generation += 1
        self._retry_pending = False
        self._retry_state = None
        self._retry_context = None
        self._verification_at = None

    def _schedule_verification(
        self,
        state: SchedulerState,
        context: EMSContext,
        attempt: int,
    ) -> None:
        self._verification_generation += 1
        generation = self._verification_generation
        expected_powers = list(context.charge_distribution)
        self._retry_context = context

        verification_delay = self.verification_delay
        resolver = getattr(
            self.controller, "recommended_verification_delay", None
        )
        if callable(resolver):
            verification_delay = max(
                1, int(resolver(self.verification_delay))
            )
        self._verification_at = (
            datetime.now(context.now.tzinfo)
            + timedelta(seconds=verification_delay)
        )

        self.app.run_in(
            self._verify_callback,
            verification_delay,
            generation=generation,
            state=state.value,
            expected_powers=expected_powers,
            attempt=attempt,
        )

    def _verify_callback(self, kwargs) -> None:
        generation = int(kwargs["generation"])
        if generation != self._verification_generation:
            return
        self._verification_at = None

        wait_remaining = getattr(
            self.controller, "verification_wait_remaining", None
        )
        if callable(wait_remaining):
            remaining = float(wait_remaining(self.verification_delay))
            if remaining > 0:
                delay = max(1, int(remaining + 0.999))
                self._verification_at = (
                    datetime.now(self._retry_context.now.tzinfo)
                    + timedelta(seconds=delay)
                )
                self.app.run_in(self._verify_callback, delay, **kwargs)
                self.last_verification = (
                    "Wacht op volledige controllerreeks en verwerking door "
                    "de accu's"
                )
                self._notify_status_changed()
                return

        state = SchedulerState(kwargs["state"])
        expected_powers = kwargs.get("expected_powers", [])
        attempt = int(kwargs.get("attempt", 0))
        result = self.controller.verify_state(
            state,
            expected_powers=expected_powers,
        )

        if result.get("pending"):
            verification_delay = 2
            self.last_verification = result["summary"]
            self._verification_at = (
                datetime.now(self._retry_context.now.tzinfo)
                + timedelta(seconds=verification_delay)
            )
            self.app.run_in(
                self._verify_callback,
                verification_delay,
                **kwargs,
            )
            self._notify_status_changed()
            return

        self.verification_ok = result["ok"]
        self.last_verification = result["summary"]
        self._apply_verification_result(result)

        if result["ok"]:
            self._retry_pending = False
            self._retry_state = None
            self._retry_context = None
            self._verification_at = None
            self.app.log(
                f"[Scheduler] State bevestigd: {state.value} | "
                f"{result['summary']}"
            )
            self._notify_status_changed()
            return

        failed_state = self.current_state
        self.controller.invalidate_last_command(
            reason=f"state {state.value} niet bevestigd"
        )
        self.previous_state = failed_state
        self.current_state = SchedulerState.ERROR
        self.app.log(
            f"[Scheduler] Verificatie mislukt voor {state.value}: "
            f"{result['summary']}",
            level="WARNING",
        )

        if attempt < self.max_verification_retries:
            for item in self.battery_statuses:
                if item["status"] != "OK":
                    item["status"] = "HERSTELLEN"
                    item["details"] = (
                        "Afwijking bevestigd; eenmalige herkansing ingepland"
                    )
            self._retry_generation += 1
            retry_generation = self._retry_generation
            self._retry_pending = True
            self._retry_state = state
            retry_context = self._retry_context
            if retry_context is None:
                return
            self._retry_context = replace(
                retry_context,
                now=datetime.now(retry_context.now.tzinfo),
            )
            self._verification_at = (
                datetime.now(retry_context.now.tzinfo)
                + timedelta(seconds=self.retry_delay)
            )
            self.last_execution = (
                f"Eenmalige herkansing over {self.retry_delay} seconden"
            )
            self.app.run_in(
                self._retry_callback,
                self.retry_delay,
                generation=retry_generation,
                state=state.value,
                attempt=attempt + 1,
            )
            self._notify_status_changed()
            return

        self._failed_state_latch = state
        self.last_execution = (
            "Verificatie definitief mislukt; verdere identieke opdrachten "
            "zijn geblokkeerd"
        )
        self.app.log(
            f"[Scheduler] State {state.value} geblokkeerd na "
            f"{attempt + 1} mislukte verificaties",
            level="ERROR",
        )
        self._notify_status_changed()

    def _retry_callback(self, kwargs) -> None:
        if int(kwargs["generation"]) != self._retry_generation:
            return
        self._verification_at = None
        self._retry_pending = False
        self._retry_state = None
        context = self._retry_context
        self._retry_context = None
        if context is None or not self.controller.enabled:
            return

        context = replace(context, now=datetime.now(context.now.tzinfo))
        state = SchedulerState(kwargs["state"])
        desired_state, _reason = self._desired_state(context)
        if desired_state != state:
            self.app.log(
                f"[Scheduler] Herkansing vervallen: gewenste state is nu "
                f"{desired_state.value}"
            )
            return

        if state == SchedulerState.CHARGING:
            context.charge_distribution = self._active_charge_powers(context)
        if not self._execute_transition(state, context):
            self._failed_state_latch = state
            self.current_state = SchedulerState.ERROR
            self.last_execution = "Controllerherkansing niet uitgevoerd"
            return

        self.previous_state = SchedulerState.ERROR
        self.current_state = state
        self.last_transition = context.now
        self.last_controller_action = self._controller_action_name(state)
        self.last_execution = f"Eenmalige herkansing uitgevoerd: {state.value}"
        self.last_verification = "Controle na herkansing ingepland"
        self.verification_ok = None
        self._set_all_battery_statuses(
            "CONTROLEREN", "Herkansing uitgevoerd; wacht op controle"
        )
        self._notify_status_changed()
        self._schedule_verification(
            state,
            context,
            attempt=int(kwargs["attempt"]),
        )

    def _set_all_battery_statuses(self, status, details):
        for item in self.battery_statuses:
            item["status"] = status
            item["details"] = details

    def _notify_status_changed(self):
        publish = getattr(self.app, "_publish_dashboard", None)
        if callable(publish):
            publish()

    def _apply_verification_result(self, result):
        by_index = {
            int(item["index"]): item
            for item in result.get("batteries", [])
            if "index" in item
        }
        for index, status in enumerate(self.battery_statuses):
            item = by_index.get(index)
            if item is None:
                status["status"] = "ONBEKEND"
                status["details"] = "Geen verificatiegegevens ontvangen"
                continue
            status["status"] = "OK" if item.get("ok") else "FOUT"
            status["details"] = self._verification_details(item)

    @staticmethod
    def _verification_details(item):
        parts = []
        for key, label in (
            ("work_mode", "mode"),
            ("rs485", "RS485"),
            ("force_mode", "Force Mode"),
            ("charge_power", "laden"),
            ("discharge_power", "ontladen"),
        ):
            value = item.get(key)
            if value is not None:
                suffix = " W" if "power" in key else ""
                parts.append(f"{label}={value}{suffix}")
        return ", ".join(parts) or (
            "Automatische controllerstatus bevestigd"
            if item.get("ok")
            else "Automatische controllerstatus wijkt af"
        )

    def _desired_state(self, context: EMSContext):
        now = context.now

        active_charge_powers = self._active_charge_powers(context)
        if sum(active_charge_powers) > 0:
            return (
                SchedulerState.CHARGING,
                "Minstens één individueel laadmoment is actief",
            )

        if context.discharge_plan:
            for block in context.discharge_plan.blocks:
                if block.start <= now < block.end:
                    if context.battery.average_soc <= context.minimum_soc:
                        return (
                            SchedulerState.SELF_CONSUMPTION,
                            "Ontlaadblok actief, maar minimum-SOC is bereikt",
                        )
                    return (
                        SchedulerState.SELF_CONSUMPTION,
                        "Zelfconsumptieblok actief",
                    )

        return (
            SchedulerState.STOPPED,
            "Geen laad- of ontlaadblok actief",
        )

    def _active_charge_powers(self, context: EMSContext):
        self._refresh_target_charge_latches(context)
        if not context.battery_charge_plans:
            if context.charge_plan and (
                context.charge_plan.start
                <= context.now
                < context.charge_plan.end
            ):
                return self._filter_runtime_charge_powers(
                    self.filter_target_charge_powers(
                        list(context.charge_distribution)
                    )
                )
            return [0.0 for _ in context.battery.soc_values]

        powers = [0.0 for _ in context.battery.soc_values]
        for plan in context.battery_charge_plans:
            if (
                plan.index >= len(powers)
                or context.battery.soc_values[plan.index]
                >= min(float(context.target_soc), float(plan.target_soc))
            ):
                continue
            if self._battery_charge_opportunity_active(context, plan):
                powers[plan.index] = plan.planned_power
        return self._filter_runtime_charge_powers(
            self.filter_target_charge_powers(powers)
        )

    def _battery_charge_opportunity_active(self, context, plan):
        if any(
            segment.start <= context.now < segment.end
            for segment in plan.segments
        ):
            return True
        if plan.end is None or context.now < plan.end:
            return False
        economic_end = self._economic_charge_block_end(context)
        return bool(
            economic_end is not None
            and context.now < economic_end
        )

    def _refresh_target_charge_latches(self, context: EMSContext):
        """Houdt een volle accu rustig binnen hetzelfde economische blok."""
        now = context.now
        block_end = self._economic_charge_block_end(context)
        monitor = getattr(self.app, "runtime_safety_monitor", None)
        if monitor is not None:
            monitor.update_charge_block_end(block_end)
        for index, soc in enumerate(context.battery.soc_values):
            latch = self.target_charge_latches.get(index)
            release_soc = max(
                0.0,
                float(context.target_soc)
                - TARGET_SOC_RELEASE_HYSTERESIS_PERCENT,
            )
            if latch is not None and (
                now >= latch["block_end"]
                or float(soc) < release_soc
            ):
                self.target_charge_latches.pop(index, None)
                self.app.log(
                    f"{context.battery.names[index]}: doel-SOC-vergrendeling "
                    "vrijgegeven"
                )
                latch = None
            if latch is not None or block_end is None:
                continue
            threshold = self._target_reached_threshold(context.target_soc)
            if (
                float(soc) >= threshold
                and self._battery_has_active_charge_opportunity(context, index)
            ):
                self.target_charge_latches[index] = {
                    "soc": float(soc),
                    "target": float(context.target_soc),
                    "block_end": block_end,
                }
                self.app.log(
                    f"{context.battery.names[index]}: doelzone bereikt bij "
                    f"{float(soc):.1f}% (doel {context.target_soc:.1f}%); "
                    f"niet opnieuw laden binnen dit laadblok tot "
                    f"{block_end.strftime('%H:%M')}"
                )

    @staticmethod
    def _target_reached_threshold(target_soc):
        target = float(target_soc)
        if target >= 100.0 - 1e-6:
            return target - TARGET_SOC_FULL_TOLERANCE_PERCENT
        return target

    def _battery_has_active_charge_opportunity(self, context, index):
        if context.battery_charge_plans:
            return any(
                plan.index == index
                and self._battery_charge_opportunity_active(context, plan)
                for plan in context.battery_charge_plans
            )
        return bool(
            context.charge_plan
            and context.charge_plan.start
            <= context.now
            < context.charge_plan.end
        )

    @staticmethod
    def _economic_charge_block_end(context):
        return EnergyScheduler._economic_charge_block_end_for_plan(
            context.charge_plan, context.now
        )

    @staticmethod
    def _economic_charge_block_end_for_plan(plan, now):
        if plan is None:
            return None
        slots = sorted(
            list(plan.eligible_slots or []),
            key=lambda item: EnergyScheduler._slot_datetime(item["from"]),
        )
        containing = [
            item for item in slots
            if (
                EnergyScheduler._slot_datetime(item["from"])
                <= now
                < EnergyScheduler._slot_datetime(item["till"])
            )
        ]
        if not containing:
            return plan.end
        end = EnergyScheduler._slot_datetime(containing[0]["till"])
        for item in slots:
            if EnergyScheduler._slot_datetime(item["from"]) == end:
                end = EnergyScheduler._slot_datetime(item["till"])
        return end

    @classmethod
    def charge_plan_runtime_end(cls, plan, now):
        """Laat een onvoltooide accu doorladen tot het goedkope blok eindigt."""
        if plan is None:
            return None
        economic_end = cls._economic_charge_block_end_for_plan(plan, now)
        return economic_end or plan.end

    @staticmethod
    def _slot_datetime(value):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    def filter_target_charge_powers(self, powers):
        result = list(powers)
        for index in self.target_charge_latches:
            if index < len(result):
                result[index] = 0.0
        return result

    def planning_soc_values(self, soc_values, target_soc, now):
        """Voorkomt dat de planner dezelfde volle procent opnieuw inplant."""
        values = list(soc_values)
        threshold = self._target_reached_threshold(target_soc)
        release_soc = max(
            0.0,
            float(target_soc) - TARGET_SOC_RELEASE_HYSTERESIS_PERCENT,
        )
        # De doelzone geldt ook direct na een herstart, voordat de scheduler
        # een runtime-latch heeft kunnen aanmaken. Anders plant 99% bij een
        # doel van 100% alsnog een korte, dure top-up die vervolgens alleen
        # bij de uitvoering wordt tegengehouden.
        for index, soc in enumerate(values):
            if float(soc) >= threshold:
                values[index] = max(float(soc), float(target_soc))
        for index, latch in list(self.target_charge_latches.items()):
            soc = float(values[index]) if index < len(values) else 0.0
            if (
                now >= latch["block_end"]
                or soc < release_soc
            ):
                self.target_charge_latches.pop(index, None)
                continue
            if index < len(values):
                values[index] = max(soc, float(target_soc))
        return values

    def _filter_runtime_charge_powers(self, powers):
        monitor = getattr(self.app, "runtime_safety_monitor", None)
        if monitor is None:
            return powers
        return monitor.filter_charge_powers(powers)

    def _controller_action_name(self, state: SchedulerState) -> str:
        if state == SchedulerState.CHARGING:
            return "set_charge"
        if state in {
            SchedulerState.DISCHARGING,
            SchedulerState.SELF_CONSUMPTION,
        }:
            return "set_self_consumption"
        if state in {SchedulerState.IDLE, SchedulerState.STOPPED}:
            return "set_stop"
        return "onbekend"

    def _log_transition(self, desired_state, reason, executed):
        status = "uitgevoerd" if executed else "niet uitgevoerd"
        self.app.log(
            "[Scheduler] "
            f"{self.current_state.value} -> {desired_state.value} | "
            f"{reason} | {status}"
        )
