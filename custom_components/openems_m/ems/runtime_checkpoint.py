# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Crashbestendig herstel van de actieve OpenEMS-uitvoering."""

from __future__ import annotations
from .. import ems_paths

from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from .models import (
    BatteryChargePlan,
    BatteryChargeSegment,
    BatteryDischargePlan,
    ChargePlan,
    DischargeBlock,
    DischargePlan,
    EMSContext,
    SchedulerState,
)
from .persistence import JsonStore


class RuntimeCheckpointManager:
    """Bewaart proces-eigenaarschap en hervat alleen een veilige opdracht."""

    STATUS_SENSOR = "sensor.openems_runtime_herstel"
    # Planning is backward-compatible als optioneel veld toegevoegd, zodat
    # ook het checkpoint van vóór deze update veilig kan worden hervat.
    VERSION = 1
    MAX_STARTUP_ATTEMPTS = 12

    def __init__(self, app, timezone, data_file=None):
        self.app = app
        self.timezone = timezone
        path = data_file or (
            ems_paths.data_parent()
            / "OpenEMS-M-data"
            / "actieve_runtime.json"
        )
        self.store = JsonStore(
            path, app=app, timezone=timezone, label="Actieve runtime"
        )
        self.saved = self.store.load(self._empty)
        self._last_write_at = self._datetime(self.saved.get("saved_at"))
        self.pending = True
        self.armed = False
        self.override = None
        self.planning_restored = False
        self._boundary_handles = []
        self.last_result = "Wacht op actuele sensordata"

    def start(self):
        self.app.run_in(self._attempt_startup_recovery, 2, attempt=0)
        self._publish("wachten", self.last_result)

    def startup_pending(self):
        return self.pending

    def controls_execution(self):
        if not self.override:
            return False
        if not self._override_valid(datetime.now(self.timezone)):
            self.clear("opgeslagen proces is verlopen")
            return False
        return self.override.get("owner") in {
            "charging", "self_consumption"
        }

    def apply_override(self):
        """Voert een hersteld laad- of zelfconsumptieproces veilig uit."""
        if not self.controls_execution():
            return False
        owner = self.override["owner"]
        if owner == "charging":
            powers = self._active_charge_powers(
                self.override, datetime.now(self.timezone)
            )
            if powers is None:
                self.clear("herstelde laadsegmenten zijn afgelopen")
                return False
            target_soc = float(self.override.get("target_soc", 100.0))
            battery = self.app.battery_manager.read()
            powers = [
                0.0 if index < len(battery.soc_values)
                and float(battery.soc_values[index]) >= target_soc - 1.0
                else power
                for index, power in enumerate(powers)
            ]
            if not self._operation_safe("charging", powers):
                self.app.controller.set_stop(source="recovery")
                self.clear("laden niet hervat door actuele veiligheidsstatus")
                return True
            try:
                success = self.app.controller.set_charge(
                    powers, source="recovery"
                )
            except (KeyError, TypeError, ValueError) as error:
                success = False
                self.app.log(
                    f"[Herstart-herstel] Laden hervatten mislukt: {error}",
                    level="ERROR",
                )
            if not success:
                self.app.controller.set_stop(source="recovery")
                self.clear("Laadopdracht kon niet veilig worden hersteld")
                return True
            self.last_result = (
                "Laden hervat: "
                + ", ".join(f"Accu {i + 1} {p:.0f} W"
                            for i, p in enumerate(powers))
            )
            self._publish("hersteld", self.last_result, owner=owner)
            return True
        if owner == "self_consumption":
            if not self._operation_safe("self_consumption"):
                self.app.controller.set_stop(source="recovery")
                self.clear(
                    "zelfconsumptie niet hervat door actuele veiligheidsstatus"
                )
                return True
            success = self.app.controller.set_self_consumption(
                source="recovery",
                force_native=bool(
                    self.app.coordinated_self_consumption.fallback_latched
                ),
            )
            if not success:
                self.app.controller.set_stop(source="recovery")
                self.clear(
                    "Zelfconsumptieopdracht kon niet veilig worden hersteld"
                )
                return True
            self.last_result = "Zelfconsumptieproces hervat"
            self._publish("hersteld", self.last_result, owner=owner)
            return True
        return False

    def handoff_to_fresh_plan(self):
        """Behoudt een herstelde opdracht tot de oorspronkelijke eindtijd."""
        if self.pending or not self.override:
            return False
        if self._override_valid(datetime.now(self.timezone)):
            return False
        self.clear("Herstelde opdracht heeft zijn oorspronkelijke eindtijd bereikt")
        self.app.log(
            "[Herstart-herstel] Oorspronkelijke eindtijd bereikt; "
            "actuele planning neemt over"
        )
        return True

    def capture(self, kwargs=None):
        """Schrijft een atomaire momentopname na planning of controlleractie."""
        if not self.armed:
            return
        now = datetime.now(self.timezone)
        if self.override and self._override_valid(now):
            snapshot = dict(self.override)
            snapshot["saved_at"] = now.isoformat()
            snapshot["controller_state"] = self.app.controller.desired_state
            snapshot["powers"] = list(map(
                float, self.app.controller.desired_powers or []
            ))
        else:
            snapshot = self._snapshot(now)
        previous_content = {
            key: value for key, value in self.saved.items()
            if key != "saved_at"
        }
        current_content = {
            key: value for key, value in snapshot.items()
            if key != "saved_at"
        }
        if (
            current_content == previous_content
            and self._last_write_at is not None
            and now - self._last_write_at < timedelta(seconds=60)
        ):
            return
        self.saved = snapshot
        self.store.save(snapshot)
        self._last_write_at = now

    def clear(self, reason):
        self._cancel_boundaries()
        self.override = None
        self.last_result = reason
        if self.armed:
            self.saved = self._empty()
            self.saved["saved_at"] = datetime.now(
                self.timezone
            ).isoformat()
            self.saved["last_result"] = reason
            self.store.save(self.saved)
            self._last_write_at = datetime.now(self.timezone)
        self._publish("niet_actief", reason)

    def _attempt_startup_recovery(self, kwargs):
        attempt = int(kwargs.get("attempt", 0))
        freshness = self.app.telemetry_guard.check(
            include_grid=self._needs_grid(self.saved),
            publish=False,
        )
        if not freshness["ok"] and attempt < self.MAX_STARTUP_ATTEMPTS:
            self.last_result = (
                "Herstel wacht op actuele sensordata "
                f"({attempt + 1}/{self.MAX_STARTUP_ATTEMPTS})"
            )
            self._publish("wachten", self.last_result)
            self.app.run_in(
                self._attempt_startup_recovery, 5, attempt=attempt + 1
            )
            return

        restored = False
        reason = "Geen actief proces om te herstellen"
        if freshness["ok"]:
            restored, reason = self._restore_saved()
        else:
            reason = "Herstel afgewezen: sensordata bleef onbetrouwbaar"
            self.app.controller.set_stop(source="recovery")

        self.pending = False
        self.armed = True
        self.last_result = reason
        self._publish("hersteld" if restored else "niet_hervat", reason)
        self.app.log(f"[Herstart-herstel] {reason}")
        # Pas na het herstel een vervolgplanning bouwen. De herstelde actieve
        # opdracht blijft daarbij tot haar oorspronkelijke eindtijd leidend.
        # Een herstelde planning kan geldig geserialiseerd zijn terwijl het
        # bijbehorende actieve proces inmiddels is verlopen. Bouw in dat
        # geval meteen een actuele planning; anders blijft de scheduler met
        # uitsluitend verlopen blokken op Stand-by staan.
        if not self.planning_restored or not restored:
            self.app.calculate_plan({"recovery_ready": True, "force": True})
        self.capture()
        if not restored:
            self.app.run_in(self.app.execute_plan, 0)

    def _restore_saved(self):
        data = self.saved if isinstance(self.saved, dict) else {}
        if int(data.get("version", 0)) != self.VERSION:
            return False, "Geen compatibel runtime-checkpoint gevonden"

        now = datetime.now(self.timezone)
        saved_at = self._datetime(data.get("saved_at"))
        if saved_at is None or now - saved_at > timedelta(hours=24):
            return False, "Runtime-checkpoint is te oud"

        self._restore_safety_latches(data)
        self.planning_restored = self._restore_planning(data)
        owner = data.get("owner")
        if not owner:
            return False, "Vorige proceseigenaar ontbreekt"
        if owner in {"idle", "stopped"}:
            self.app.controller.set_stop(source="recovery")
            self.app.run_in(self.app.execute_plan, 1)
            return True, "Vorige Stand-by/Stop veilig bevestigd"

        if owner == "manual":
            if self.app.get_state(self.app.manual_controller.enabled_helper) == "on":
                return False, "Handmatige opdracht vervallen: OpenEMS staat op Auto"
            self.app.manual_controller.apply_current({})
            return True, "Handmatige opdracht veilig opnieuw toegepast"

        if owner == "test":
            mode = self.app.get_state(self.app.controller_test.helper_entity)
            if mode in (None, "", "unknown", "unavailable", "Uit"):
                return False, "Technische testmodus is inmiddels uitgeschakeld"
            self.app.controller_test.apply_current()
            return True, f"Technische testmodus {mode} hervat"

        if str(self.app.get_state(
            self.app.manual_controller.enabled_helper
        )).lower() != "on":
            return False, "Automatisch proces niet hervat: OpenEMS staat Handmatig"

        if owner == "export":
            if self.app.grid_export_manager.restore_checkpoint(
                data.get("export", {})
            ):
                return True, "Actief terugleverproces veilig hervat"
            return False, "Terugleverproces was verlopen of niet meer toegestaan"

        if owner == "balance":
            # Oude checkpoint van de verwijderde OpenEMS-celbalancering:
            # niets hervatten, gewoon de normale planning laten sturen.
            self.app.run_in(self.app.execute_plan, 0)
            return True, "Oude balanceersessie vervallen; normale planning hervat"

        if owner in {"charging", "self_consumption"}:
            self.override = data
            if not self._override_valid(now):
                self.override = None
                return False, "Opgeslagen proces is inmiddels verlopen"
            self._schedule_override_boundaries(data, now)
            applied = self.apply_override()
            if not applied or self.override is None:
                return False, self.last_result
            return True, (
                "Actief laadproces veilig hervat"
                if owner == "charging"
                else "Actief zelfconsumptieproces veilig hervat"
            )

        return False, f"Onbekende proceseigenaar in checkpoint: {owner}"

    def _snapshot(self, now):
        owner = self._owner()
        context = getattr(self.app, "_context", None)
        controller = self.app.controller
        result = {
            "version": self.VERSION,
            "saved_at": now.isoformat(),
            "owner": owner,
            "controller_state": controller.desired_state,
            "powers": list(map(float, controller.desired_powers or [])),
            "scheduler_state": self.app.scheduler.current_state.value,
            "scheduler_fault_latched": (
                self.app.scheduler._failed_state_latch.value
                if self.app.scheduler._failed_state_latch is not None
                else None
            ),
            "self_consumption_fallback_latched": bool(
                self.app.coordinated_self_consumption.fallback_latched
            ),
        }
        if context is not None:
            result["planning"] = self._planning_snapshot(context)
        if owner == "charging" and context is not None:
            result["target_soc"] = float(context.target_soc)
            charge_segments = [
                [] for _ in self.app.controller.batteries
            ]
            for plan in context.battery_charge_plans:
                if not 0 <= int(plan.index) < len(charge_segments):
                    continue
                charge_segments[int(plan.index)] = [
                    {
                        "start": segment.start.isoformat(),
                        "end": segment.end.isoformat(),
                        "power": float(segment.power),
                    }
                    for segment in plan.segments
                    if segment.end > now
                ]
            result["charge_segments"] = charge_segments
            result["active_until"] = self._latest_segment_end(
                result["charge_segments"]
            )
        elif owner == "self_consumption":
            block = self._active_discharge_block(context, now)
            result["active_until"] = (
                block.end.isoformat() if block is not None else None
            )
            result["self_consumption_mode"] = (
                self.app.runtime_settings.self_consumption_mode()
            )
        elif owner == "export":
            result["export"] = self.app.grid_export_manager.checkpoint()
            result["active_until"] = result["export"].get("end")
        return result

    def _owner(self):
        test = self.app.get_state(self.app.controller_test.helper_entity)
        if test not in (None, "", "unknown", "unavailable", "Uit"):
            return "test"
        if str(self.app.get_state(
            self.app.manual_controller.enabled_helper
        )).lower() != "on":
            return "manual"
        if self.app.grid_export_manager.active:
            return "export"
        if self.app.coordinated_self_consumption.active:
            return "self_consumption"
        if self.app.controller.desired_state == "charging":
            return "charging"
        if self.app.controller.desired_state == "self_consumption":
            return "self_consumption"
        if self.app.controller.desired_state == "stopped":
            return "stopped"
        state = self.app.scheduler.current_state
        if state == SchedulerState.CHARGING:
            return "charging"
        if state in {
            SchedulerState.DISCHARGING, SchedulerState.SELF_CONSUMPTION
        }:
            return "self_consumption"
        return state.value

    def _override_valid(self, now):
        if not self.override:
            return False
        if (
            str(self.app.get_state(
                self.app.manual_controller.enabled_helper
            )).lower() != "on"
            or not self.app.controller.enabled
        ):
            return False
        owner = self.override.get("owner")
        if owner == "charging":
            if not self.app.runtime_settings.grid_charging_allowed():
                return False
            return self._active_charge_powers(self.override, now) is not None
        end = self._datetime(self.override.get("active_until"))
        return end is not None and now < end

    def _active_charge_powers(self, data, now):
        plans = data.get("charge_segments")
        if not isinstance(plans, list) or not plans:
            return None
        powers = []
        future = False
        for segments in plans:
            power = 0.0
            for item in segments or []:
                start = self._datetime(item.get("start"))
                end = self._datetime(item.get("end"))
                if start is None or end is None:
                    continue
                future = future or end > now
                if start <= now < end:
                    power = max(0.0, float(item.get("power", 0.0)))
                    break
            powers.append(power)
        return powers if future else None

    def _operation_safe(self, state, powers=None):
        operation = (
            "charging" if state == "charging"
            else "self_consumption"
        )
        indexes = None
        if powers is not None:
            indexes = [i for i, power in enumerate(powers) if power > 0]
        return self.app.safety_service.all_safe(operation, indexes=indexes)

    def _restore_safety_latches(self, data):
        failed = data.get("scheduler_fault_latched")
        if failed:
            try:
                self.app.scheduler._failed_state_latch = SchedulerState(failed)
            except ValueError:
                pass
        if data.get("self_consumption_fallback_latched"):
            self.app.coordinated_self_consumption.fallback_latched = True

    def _planning_snapshot(self, context):
        """Serialiseer de geldige uurplanning zonder live accutelemetrie."""
        return {
            "target_soc": float(context.target_soc),
            "minimum_soc": float(context.minimum_soc),
            "price_margin": float(context.price_margin),
            "discharge_margin": float(context.discharge_margin),
            "charge_efficiency": float(context.charge_efficiency),
            "future_prices": self._encode(context.future_prices),
            "charge_plan": self._encode_dataclass(context.charge_plan),
            "battery_charge_plans": [
                self._encode(asdict(plan))
                for plan in context.battery_charge_plans
            ],
            "discharge_plan": self._encode_dataclass(
                context.discharge_plan
            ),
            "export_plan": self._encode_dataclass(context.export_plan),
            "charge_distribution": list(map(
                float, context.charge_distribution
            )),
            "current_price": getattr(self.app, "_current_price", None),
        }

    def _restore_planning(self, data):
        """Herstel de voor de herstart vastgelegde uurblokken."""
        raw = data.get("planning")
        if not isinstance(raw, dict):
            return False
        try:
            battery = self.app.battery_manager.read()
            charge_plan = self._restore_charge_plan(raw.get("charge_plan"))
            battery_charge_plans = [
                self._restore_battery_charge_plan(item)
                for item in raw.get("battery_charge_plans", [])
            ]
            discharge_plan = self._restore_discharge_plan(
                raw.get("discharge_plan")
            )
            export_plan = self._restore_discharge_plan(
                raw.get("export_plan")
            )
            now = datetime.now(self.timezone)
            context = EMSContext(
                now=now,
                battery=battery,
                target_soc=float(raw["target_soc"]),
                minimum_soc=float(raw["minimum_soc"]),
                price_margin=float(raw["price_margin"]),
                discharge_margin=float(raw["discharge_margin"]),
                charge_efficiency=float(raw["charge_efficiency"]),
                battery_energy_needed=battery.capacity * max(
                    0.0, float(raw["target_soc"]) - battery.average_soc
                ) / 100,
                needed_energy=0.0,
                hours_needed=0.0,
                future_prices=self._decode(raw.get("future_prices", [])),
                charge_plan=charge_plan,
                battery_charge_plans=battery_charge_plans,
                discharge_plan=discharge_plan,
                export_plan=export_plan,
                charge_distribution=list(map(
                    float, raw.get("charge_distribution", [])
                )),
            )
        except (KeyError, TypeError, ValueError):
            return False
        context.needed_energy = (
            context.battery_energy_needed / context.charge_efficiency
            if context.charge_efficiency > 0
            else context.battery_energy_needed
        )
        context.hours_needed = (
            context.needed_energy / (battery.max_charge / 1000)
            if battery.max_charge > 0 else 0.0
        )
        self.app._context = context
        self.app._dashboard_charge_plan = charge_plan
        self.app._dashboard_battery_charge_plans = battery_charge_plans
        self.app._dashboard_charge_price_slots = list(
            charge_plan.eligible_slots if charge_plan else []
        )
        self.app._dashboard_discharge_price_slots = list(
            discharge_plan.eligible_slots if discharge_plan else []
        )
        self.app._dashboard_discharge_profit_blocks = list(
            discharge_plan.blocks if discharge_plan else []
        )
        current_price = raw.get("current_price")
        if current_price is not None:
            self.app._current_price = float(current_price)
        self.app.scheduler.determine(context)
        self.app._schedule_execution_boundaries(context)
        self.app._publish_dashboard()
        return True

    def _restore_charge_plan(self, raw):
        if not isinstance(raw, dict):
            return None
        return ChargePlan(**self._decode(raw))

    def _restore_battery_charge_plan(self, raw):
        values = self._decode(raw)
        values["segments"] = [
            BatteryChargeSegment(**item)
            for item in values.get("segments", [])
        ]
        return BatteryChargePlan(**values)

    def _restore_discharge_plan(self, raw):
        if not isinstance(raw, dict):
            return None
        values = self._decode(raw)
        values["blocks"] = [
            DischargeBlock(**item) for item in values.get("blocks", [])
        ]
        values["per_battery"] = [
            BatteryDischargePlan(**item)
            for item in values.get("per_battery", [])
        ]
        return DischargePlan(**values)

    def _encode_dataclass(self, value):
        return self._encode(asdict(value)) if value is not None else None

    def _encode(self, value):
        if isinstance(value, datetime):
            return {"__datetime__": value.isoformat()}
        if isinstance(value, dict):
            return {key: self._encode(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._encode(item) for item in value]
        return value

    def _decode(self, value):
        if isinstance(value, dict):
            if set(value) == {"__datetime__"}:
                return self._datetime(value["__datetime__"])
            return {key: self._decode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._decode(item) for item in value]
        return value

    def _schedule_override_boundaries(self, data, now):
        self._cancel_boundaries()
        values = {data.get("active_until")}
        for segments in data.get("charge_segments", []) or []:
            for item in segments or []:
                values.add(item.get("start"))
                values.add(item.get("end"))
        moments = sorted(
            moment for moment in (
                self._datetime(value) for value in values
            )
            if moment is not None and moment > now
        )
        for moment in moments:
            handle = self.app.run_in(
                self._override_boundary,
                max(1.0, (moment - now).total_seconds()),
            )
            if handle is not None:
                self._boundary_handles.append(handle)

    def _override_boundary(self, kwargs):
        if not self.apply_override():
            self.app.execute_plan({})

    def _cancel_boundaries(self):
        cancel = getattr(self.app, "cancel_timer", None)
        if callable(cancel):
            for handle in self._boundary_handles:
                try:
                    cancel(handle)
                except (KeyError, ValueError):
                    pass
        self._boundary_handles = []

    @staticmethod
    def _active_discharge_block(context, now):
        plan = getattr(context, "discharge_plan", None)
        if plan is None:
            return None
        return next(
            (block for block in plan.blocks if block.start <= now < block.end),
            None,
        )

    @staticmethod
    def _latest_segment_end(plans):
        values = [
            item.get("end")
            for segments in plans
            for item in (segments or [])
            if item.get("end")
        ]
        return max(values, default=None)

    def _needs_grid(self, data):
        return data.get("owner") in {"self_consumption", "export"}

    def _publish(self, state, message, **attributes):
        self.app.set_state(
            self.STATUS_SENSOR,
            state=state,
            attributes={
                "friendly_name": "OpenEMS herstart-herstel",
                "melding": message,
                "checkpoint": str(self.store.path),
                "startup_bezig": self.pending,
                "actieve_override": (
                    self.override.get("owner") if self.override else None
                ),
                **attributes,
            },
            replace=True,
        )

    def _datetime(self, value):
        if not value:
            return None
        try:
            result = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if result.tzinfo is None:
            result = result.replace(tzinfo=self.timezone)
        return result.astimezone(self.timezone)

    @classmethod
    def _empty(cls):
        return {
            "version": cls.VERSION,
            "saved_at": None,
            "owner": None,
        }
