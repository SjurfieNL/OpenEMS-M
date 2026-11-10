"""Publiceert compacte Home Assistant-sensoren voor het tablet-dashboard."""

import math
from datetime import datetime, timedelta



class DashboardStatus:
    def __init__(self, app):
        self.app = app
        self._active_discharge_start = None
        self._active_discharge_end = None

    def publish(
        self,
        context,
        scheduler_status,
        current_price,
        auto_control_enabled,
        dashboard_charge_plan=None,
        dashboard_battery_charge_plans=None,
        dashboard_charge_price_slots=None,
        dashboard_discharge_price_slots=None,
        dashboard_discharge_profit_blocks=None,
    ):
        self.publish_main_switch(auto_control_enabled)
        battery_charge_plans = list(
            getattr(context, "battery_charge_plans", [])
        )
        visible_battery_charge_plans = (
            battery_charge_plans
            if context.charge_plan
            and context.charge_plan.end > context.now
            else list(dashboard_battery_charge_plans or [])
        )
        battery_statuses = self._enriched_battery_statuses(
            context,
            scheduler_status.get("battery_statuses", []),
            visible_battery_charge_plans,
        )
        system_state, system_reason = self._effective_system_status(
            context, scheduler_status
        )
        last_action = self._central_last_action(
            scheduler_status["last_execution"]
        )
        self._sensor(
            "sensor.openems_status",
            system_state,
            icon="mdi:home-battery",
            friendly_name="OpenEMS status",
            reden=system_reason,
            laatste_actie=last_action,
            actie=last_action,
            verificatie=scheduler_status["last_verification"],
            verificatie_op=scheduler_status.get("verification_at"),
            accu_statussen=battery_statuses,
            automatische_aansturing=auto_control_enabled,
        )
        self._sensor(
            "sensor.openems_gemiddelde_soc",
            round(context.battery.average_soc, 1),
            icon="mdi:battery-high",
            friendly_name="Gemiddelde SOC",
            unit_of_measurement="%",
        )
        self._sensor(
            "sensor.openems_opgeslagen_energie",
            round(context.battery.stored_energy, 2),
            icon="mdi:battery-charging",
            friendly_name="Opgeslagen energie",
            unit_of_measurement="kWh",
        )
        self._sensor(
            "sensor.openems_huidige_prijs",
            round(float(current_price), 4),
            icon="mdi:currency-eur",
            friendly_name="Huidige stroomprijs",
            unit_of_measurement="EUR/kWh",
        )
        active_powers = self._active_charge_powers(context)
        active_charge_plan = (
            context.charge_plan if sum(active_powers) > 0 else None
        )
        planning_per_battery = []
        if active_charge_plan:
            for index, name in enumerate(context.battery.names):
                power = active_powers[index]
                plan = next(
                    (
                        item for item in battery_charge_plans
                        if item.index == index
                    ),
                    None,
                )
                planning_per_battery.append({
                    "naam": name,
                    "vermogen_w": round(power),
                    "energie_kwh": round(
                        plan.battery_energy_needed
                        if plan else (
                            power / 1000
                            * active_charge_plan.effective_charge_hours
                            * context.charge_efficiency
                        ),
                        2,
                    ),
                })

        self._sensor(
            "sensor.openems_gepland_laadvermogen",
            round(sum(active_powers)),
            icon="mdi:lightning-bolt",
            friendly_name="Gepland laadvermogen",
            unit_of_measurement="W",
            laadblok_actief=active_charge_plan is not None,
            effectieve_laaduur_uren=(
                round(active_charge_plan.effective_charge_hours, 2)
                if active_charge_plan else 0.0
            ),
            geplande_energie_kwh=round(sum(
                item["energie_kwh"] for item in planning_per_battery
            ), 2),
            per_accu=planning_per_battery,
        )

        charge_plan = dashboard_charge_plan
        charge_display_end = self._charge_display_end(charge_plan)
        # De sensorstate blijft het eerstvolgende blok tonen, maar de grafiek
        # heeft ook het (eventueel al afgelopen) plan van vandaag nodig. Zonder
        # dit tweede interval verdwijnt het groene laadblok zodra het volgende
        # laadblok naar morgen doorschuift.
        charge_intervals = []
        seen_charge_intervals = set()
        plan_groups = (
            battery_charge_plans,
            list(dashboard_battery_charge_plans or []),
        )
        for plans in plan_groups:
            for battery_plan in plans:
                for segment in battery_plan.segments:
                    interval = (segment.start, segment.end)
                    if interval in seen_charge_intervals:
                        continue
                    seen_charge_intervals.add(interval)
                    charge_intervals.append(interval)
        if not charge_intervals:
            for candidate in (context.charge_plan, dashboard_charge_plan):
                if candidate is None:
                    continue
                interval = (candidate.start, candidate.end)
                if interval in seen_charge_intervals:
                    continue
                seen_charge_intervals.add(interval)
                charge_intervals.append(interval)
        eligible_intervals = []
        seen_eligible_intervals = set()
        price_slot_groups = []
        if dashboard_charge_price_slots is not None:
            price_slot_groups.append(list(dashboard_charge_price_slots))
        else:
            for candidate in (context.charge_plan, dashboard_charge_plan):
                if candidate is None:
                    continue
                price_slot_groups.append(
                    candidate.eligible_slots
                    if getattr(candidate, "eligible_slots", None)
                    else getattr(candidate, "slots", [])
                )
        for slots in price_slot_groups:
            for slot in slots:
                interval = (
                    self._iso(slot.get("from")),
                    self._iso(slot.get("till")),
                )
                if None in interval or interval in seen_eligible_intervals:
                    continue
                seen_eligible_intervals.add(interval)
                eligible_intervals.append(interval)
        self._sensor(
            "sensor.openems_volgend_laadblok",
            (
                f"{charge_plan.start.strftime('%d-%m')} van "
                f"{charge_plan.start.strftime('%H:%M')} t/m "
                f"{charge_display_end.strftime('%H:%M')}"
                if charge_plan else "geen"
            ),
            icon="mdi:battery-clock",
            friendly_name="Volgend laadblok",
            start=charge_plan.start.isoformat() if charge_plan else "geen",
            einde=(
                charge_display_end.isoformat() if charge_plan else "geen"
            ),
            gemiddelde_prijs=(
                round(charge_plan.average_price, 4) if charge_plan else None
            ),
            blokken=[
                {
                    "start": start.isoformat(),
                    "einde": end.isoformat(),
                }
                for start, end in sorted(charge_intervals)
            ],
            prijsblokken=[
                {"start": start, "einde": end}
                for start, end in sorted(eligible_intervals)
            ],
            overzichtsblokken=self._charge_overview_blocks(
                plan_groups, charge_plan, charge_display_end
            ),
        )

        discharge_blocks = (
            context.discharge_plan.blocks
            if context.discharge_plan else []
        )
        discharge_price_intervals = []
        discharge_slots = (
            list(dashboard_discharge_price_slots)
            if dashboard_discharge_price_slots is not None
            else (
                getattr(context.discharge_plan, "eligible_slots", [])
                if context.discharge_plan else []
            )
        )
        for slot in discharge_slots:
            start = self._iso(slot.get("from"))
            end = self._iso(slot.get("till"))
            if start is not None and end is not None:
                discharge_price_intervals.append({
                    "start": start,
                    "einde": end,
                })
        next_discharge = next(
            (block for block in discharge_blocks if block.end > context.now),
            None,
        )
        discharge_display_start = self._stable_discharge_start(
            next_discharge, context.now
        )
        self._sensor(
            "sensor.openems_volgend_ontlaadblok",
            (
                discharge_display_start.strftime("%d-%m %H:%M")
                if next_discharge else "geen"
            ),
            icon="mdi:battery-arrow-down",
            friendly_name="Volgend ontlaadblok",
            start=(
                discharge_display_start.isoformat()
                if next_discharge else "geen"
            ),
            einde=(
                next_discharge.end.isoformat()
                if next_discharge else "geen"
            ),
            gemiddelde_prijs=(
                round(next_discharge.average_price, 4)
                if next_discharge else None
            ),
            blokken=[
                {
                    "start": block.start.isoformat(),
                    "einde": block.end.isoformat(),
                    "verwachte_energie_kwh": round(
                        float(getattr(block, "expected_energy", 0.0)), 3
                    ),
                    "netto_resultaat_eur": round(
                        float(getattr(block, "net_profit", 0.0)), 3
                    ),
                    "gemiddelde_prijs": round(
                        float(block.average_price), 4
                    ),
                }
                for block in discharge_blocks
            ],
            prijsblokken=discharge_price_intervals,
            winstblokken=[
                {
                    "start": block.start.isoformat(),
                    "einde": block.end.isoformat(),
                }
                for block in list(
                    dashboard_discharge_profit_blocks or []
                )
            ],
        )
        export_plan = getattr(context, "export_plan", None)
        export_blocks = list(export_plan.blocks) if export_plan else []
        export_manager = getattr(
            self.app, "grid_export_manager", None
        )
        active_export_block = (
            export_manager.active_block()
            if export_manager is not None
            and bool(getattr(export_manager, "active", False))
            and callable(getattr(export_manager, "active_block", None))
            else None
        )
        if (
            active_export_block is not None
            and not any(
                block.start == active_export_block.start
                and block.end == active_export_block.end
                for block in export_blocks
            )
        ):
            export_blocks.append(active_export_block)
        export_blocks.sort(key=lambda block: block.start)
        next_export = next(
            (block for block in export_blocks if block.end > context.now),
            None,
        )
        export_mode = getattr(
            self.app, "get_state", lambda _entity: None
        )("input_select.openems_grid_export_modus")
        simulation_mode = export_mode == "Alleen simuleren"
        export_enabled = export_mode in (
            "Alleen simuleren", "Automatisch exporteren"
        )
        export_cost_basis = getattr(
            next_export, "cost_basis_per_kwh", None
        )
        if export_cost_basis is None and export_plan is not None:
            export_cost_basis = getattr(
                export_plan, "cost_basis_per_kwh", None
            )
        export_minimum_difference = (
            getattr(export_plan, "minimum_price_difference", None)
            if export_plan else None
        )
        export_selection_margin = (
            getattr(export_plan, "selection_margin", None)
            if export_plan else None
        )
        export_price_limit = (
            getattr(export_plan, "price_limit", None)
            if export_plan else None
        )
        self._sensor(
            "sensor.openems_volgend_exportblok",
            (
                next_export.start.strftime("%d-%m %H:%M")
                if next_export else "geen"
            ),
            icon="mdi:transmission-tower-export",
            friendly_name="Volgend terugleverblok",
            start=(
                next_export.start.isoformat()
                if next_export else "geen"
            ),
            einde=(
                next_export.end.isoformat()
                if next_export else "geen"
            ),
            gemiddelde_prijs=(
                round(float(next_export.average_price), 4)
                if next_export else None
            ),
            voorraadkostprijs_eur_kwh=(
                round(float(export_cost_basis), 4)
                if export_cost_basis is not None else None
            ),
            minimaal_prijsverschil_eur_kwh=(
                round(float(export_minimum_difference), 4)
                if export_minimum_difference is not None else None
            ),
            extra_selectiemarge_eur_kwh=(
                round(float(export_selection_margin), 4)
                if export_selection_margin is not None else None
            ),
            piekmarge_eur_kwh=(
                round(float(export_selection_margin), 4)
                if export_selection_margin is not None else None
            ),
            toegestaan_vanaf_eur_kwh=(
                round(float(export_price_limit), 4)
                if export_price_limit is not None else None
            ),
            gepland_vanaf_eur_kwh=None,
            dagpiekprijzen=dict(
                getattr(export_plan, "daily_peak_prices", {})
                if export_plan else {}
            ),
            dagpiekgrenzen=dict(
                getattr(export_plan, "daily_peak_limits", {})
                if export_plan else {}
            ),
            kostprijsbron=(
                getattr(export_plan, "cost_basis_source", None)
                if export_plan else None
            ),
            prijsblokken=[
                {
                    "start": self._iso(slot.get("from")),
                    "einde": self._iso(slot.get("till")),
                }
                for slot in (
                    getattr(export_plan, "eligible_slots", [])
                    if export_plan and export_enabled else []
                )
                if self._iso(slot.get("from")) is not None
                and self._iso(slot.get("till")) is not None
            ],
            blokken=[
                {
                    "start": block.start.isoformat(),
                    "einde": block.end.isoformat(),
                    "doel_export_w": round(float(
                        getattr(block, "planned_export_power", 0.0)
                    )),
                    "gemiddelde_prijs": round(
                        float(block.average_price), 4
                    ),
                    "voorraadkostprijs_eur_kwh": (
                        round(float(block.cost_basis_per_kwh), 4)
                        if block.cost_basis_per_kwh is not None else None
                    ),
                }
                for block in export_blocks
                if export_enabled and not simulation_mode
            ],
            simulatie=simulation_mode,
        )
        self._publish_planning_overview(
            context,
            context.charge_plan or dashboard_charge_plan,
            discharge_blocks,
            visible_battery_charge_plans,
        )

    def _charge_display_end(self, plan):
        """Toon de echte prijsblokgrens, niet het 15-minuten-laadmikpunt."""
        if plan is None:
            return None
        slot_ends = []
        for slot in getattr(plan, "slots", []):
            value = slot.get("till")
            try:
                parsed = (
                    value if isinstance(value, datetime)
                    else datetime.fromisoformat(
                        str(value).replace("Z", "+00:00")
                    )
                )
            except (TypeError, ValueError):
                continue
            slot_ends.append(parsed.astimezone(plan.end.tzinfo))
        slot_ends = [
            value for value in slot_ends
            if value is not None and value >= plan.end
        ]
        return max(slot_ends, default=plan.end)

    def _charge_overview_blocks(
        self, plan_groups, primary_plan, primary_display_end
    ):
        segments = []
        seen = set()
        for plans in plan_groups:
            for battery_plan in plans:
                for segment in battery_plan.segments:
                    key = (
                        segment.start, segment.end, float(segment.price)
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    segments.append(segment)
        segments.sort(key=lambda item: (item.start, item.end))
        groups = []
        for segment in segments:
            if not groups or segment.start > groups[-1]["end"]:
                groups.append({
                    "start": segment.start,
                    "end": segment.end,
                    "segments": [segment],
                })
            else:
                groups[-1]["end"] = max(
                    groups[-1]["end"], segment.end
                )
                groups[-1]["segments"].append(segment)
        result = []
        for group in groups:
            duration_price = [
                (
                    max(0.0, (
                        segment.end - segment.start
                    ).total_seconds()),
                    float(segment.price),
                )
                for segment in group["segments"]
            ]
            total_seconds = sum(item[0] for item in duration_price)
            average = (
                sum(seconds * price for seconds, price in duration_price)
                / total_seconds
                if total_seconds > 0 else None
            )
            end = group["end"]
            if (
                primary_plan is not None
                and group["start"] == primary_plan.start
            ):
                end = max(end, primary_display_end)
                average = float(primary_plan.average_price)
            result.append({
                "start": group["start"].isoformat(),
                "einde": end.isoformat(),
                "gemiddelde_prijs": (
                    round(average, 4) if average is not None else None
                ),
            })
        if primary_plan is not None and not any(
            item["start"] == primary_plan.start.isoformat()
            for item in result
        ):
            result.append({
                "start": primary_plan.start.isoformat(),
                "einde": primary_display_end.isoformat(),
                "gemiddelde_prijs": round(
                    float(primary_plan.average_price), 4
                ),
            })
        return sorted(result, key=lambda item: item["start"])

    def _stable_discharge_start(self, block, now):
        if block is None:
            self._active_discharge_start = None
            self._active_discharge_end = None
            return None
        if not block.start <= now < block.end:
            self._active_discharge_start = None
            self._active_discharge_end = None
            return block.start
        same_active_window = (
            self._active_discharge_start is not None
            and self._active_discharge_end is not None
            and now < self._active_discharge_end
            and block.start < self._active_discharge_end
        )
        if not same_active_window:
            self._active_discharge_start = block.start
        self._active_discharge_end = block.end
        return self._active_discharge_start

    def _enriched_battery_statuses(
        self, context, statuses, battery_charge_plans
    ):
        result = [dict(item) for item in statuses]
        while len(result) < len(context.battery.names):
            index = len(result)
            result.append({
                "naam": context.battery.names[index],
                "status": "ONBEKEND",
                "details": "Nog geen technische controle",
            })
        batteries = list(getattr(self.app, "batteries", []))
        discharge = getattr(context, "discharge_plan", None)
        coordinator = getattr(
            getattr(self.app, "controller", None), "coordinator", None
        )
        coordinated = {}
        if (
            coordinator is not None
            and bool(getattr(coordinator, "active", False))
            and getattr(coordinator, "phase", None) == "controlling"
        ):
            try:
                coordinated = {
                    int(value["index"]): value
                    for value in coordinator.verification_result().get(
                        "batteries", []
                    )
                }
            except (TypeError, ValueError):
                coordinated = {}
        central = self._central_battery_verification(context)
        for index, item in enumerate(result):
            coordinated_verification = coordinated.get(index)
            verification = coordinated_verification or central.get(index)
            if item.get("status") == "ONBEKEND" and verification is not None:
                item["status"] = (
                    "OK" if verification.get("ok") else "CONTROLEREN"
                )
                if coordinated_verification is not None:
                    item["details"] = (
                        "Actieve vloeiende vermogensregeling bevestigd"
                        if verification.get("ok")
                        else "Vermogensregeling wordt technisch gecontroleerd"
                    )
                else:
                    item["details"] = (
                        verification.get("details")
                        or "Centrale modus technisch bevestigd"
                        if verification.get("ok")
                        else verification.get("details")
                        or "Centrale modus wordt technisch gecontroleerd"
                    )
            actual_power = (
                self._state_number(batteries[index].get("actual_power"))
                if index < len(batteries)
                else None
            )
            item["werkelijk_vermogen_w"] = (
                round(actual_power) if actual_power is not None else None
            )
            item["huidige_status"] = self._actual_battery_mode(
                index, batteries, context
            )
            item["volgende_actie"] = self._next_battery_action(
                index, context, battery_charge_plans, discharge
            )
        return result

    def _central_battery_verification(self, context):
        """Gebruik de controller als technische waarheid voor de overzichtskaart."""
        controller = getattr(self.app, "controller", None)
        verifier = getattr(controller, "verify_state", None)
        if not callable(verifier):
            return {}
        coordinator = getattr(controller, "mode_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        central = status_getter() if callable(status_getter) else {}
        mode = central.get("mode")
        powers = central.get("powers", [])
        if mode in {None, "", "onbekend"}:
            scheduler_state = getattr(context, "scheduler_state", None)
            mode = getattr(scheduler_state, "value", scheduler_state)
        if mode in {None, "", "onbekend"}:
            return {}
        try:
            result = verifier(mode, expected_powers=powers)
        except (TypeError, ValueError):
            return {}
        label = {
            "charging": "Laden",
            "forced_discharge": "Terugleveren",
            "self_consumption": "Zelfconsumptie",
            "discharging": "Zelfconsumptie",
            "idle": "Stand-by",
            "stopped": "Stand-by",
        }.get(str(mode), str(mode))
        return {
            int(item["index"]): {
                **item,
                "details": (
                    f"Centrale modusregisseur bevestigt {label}"
                    if item.get("ok")
                    else f"{label} wijkt technisch af; controle loopt"
                ),
            }
            for item in result.get("batteries", [])
            if "index" in item
        }

    def _central_last_action(self, scheduler_action):
        controller = getattr(self.app, "controller", None)
        coordinator = getattr(controller, "mode_coordinator", None)
        status_getter = getattr(coordinator, "status", None)
        central = status_getter() if callable(status_getter) else {}
        transition = central.get("last_transition")
        mode = central.get("mode")
        if transition and mode not in {None, "", "onbekend"}:
            owner = central.get("owner", "Centrale modusregisseur")
            return f"{owner}: {transition}"
        return scheduler_action

    def _effective_system_status(self, context, scheduler_status):
        export = getattr(self.app, "grid_export_manager", None)
        if export is not None and bool(getattr(export, "active", False)):
            decision = getattr(export, "last_decision", None) or {}
            target = decision.get("gewenst_exportvermogen_w")
            reserve = decision.get("reserve_soc")
            expected = self._expected_export_end_soc(context)
            parts = []
            if target is not None:
                parts.append(
                    f"Doel {self._nl_number(float(target) / 1000, 2)} kW"
                )
            if expected is not None:
                parts.append(
                    "verwacht resterend "
                    f"{self._nl_number(expected, 0)}%"
                )
            if reserve is not None:
                parts.append(
                    "minimumreserve "
                    f"{self._nl_number(float(reserve), 0)}%"
                )
            return (
                "Terugleveren",
                " \u00b7 ".join(parts) if parts else "Terugleveren actief",
            )
        scheduler_state = scheduler_status["current_state"]
        if scheduler_state == "charging":
            return scheduler_state, context.scheduler_reason
        coordinator = getattr(
            getattr(self.app, "controller", None), "coordinator", None
        )
        if (
            coordinator is not None
            and bool(getattr(coordinator, "active", False))
            and getattr(coordinator, "phase", None) == "controlling"
        ):
            # Laden, ontladen en de nulregeling zijn binnen dit blok slechts
            # technische richtingen van dezelfde gebruikersmodus.
            return "self_consumption", "Zelfconsumptie actief"
        if scheduler_state in {"self_consumption", "discharging"}:
            return "self_consumption", context.scheduler_reason
        if scheduler_state in {"idle", "stopped"}:
            return "idle", context.scheduler_reason
        return (
            scheduler_state,
            context.scheduler_reason,
        )

    @staticmethod
    def _expected_export_end_soc(context):
        plan = getattr(context, "export_plan", None)
        items = list(getattr(plan, "per_battery", []) if plan else [])
        weighted = [
            (
                float(getattr(item, "predicted_end_soc")),
                float(getattr(item, "capacity")),
            )
            for item in items
            if getattr(item, "predicted_end_soc", None) is not None
            and getattr(item, "capacity", None) is not None
            and float(getattr(item, "capacity")) > 0
        ]
        capacity = sum(value[1] for value in weighted)
        if capacity <= 0:
            return None
        return sum(soc * size for soc, size in weighted) / capacity

    @staticmethod
    def _nl_number(value, decimals):
        return f"{float(value):.{int(decimals)}f}".replace(".", ",")

    def _actual_battery_mode(self, index, batteries, context):
        if index >= len(batteries):
            return "Onbekend"
        export = getattr(self.app, "grid_export_manager", None)
        if export is not None and bool(getattr(export, "active", False)):
            decision = getattr(export, "last_decision", None) or {}
            expected = list(decision.get("vermogens_per_accu_w") or [])
            if index < len(expected):
                return (
                    "Terugleveren"
                    if float(expected[index]) > 25.0 else "Stand-by"
                )
            # Alleen voor oude statusdata zonder verdeling blijft de globale
            # exportmodus de beste beschikbare terugmelding.
            return "Terugleveren"
        scheduler_state = getattr(context, "scheduler_state", None)
        scheduler_value = getattr(scheduler_state, "value", scheduler_state)
        if scheduler_value == "self_consumption":
            return "Zelfconsumptie"
        battery = batteries[index]
        power = self._state_number(battery.get("actual_power"))
        if power is not None:
            if power < -25:
                return "Laden"
            if power > 25:
                return "Ontladen"
        rs485 = str(
            self.app.get_state(battery.get("rs485_control")) or ""
        ).lower()
        work = str(
            self.app.get_state(battery.get("work_mode")) or ""
        ).lower()
        force = str(
            self.app.get_state(battery.get("force_mode")) or ""
        ).lower()
        if rs485 == "off" and work in {"anti_feed", "anti-feed"}:
            return "Zelfconsumptie"
        if rs485 == "on" and force == "stop":
            return "Stand-by"
        return "Stand-by"

    def _next_battery_action(
        self, index, context, battery_charge_plans, discharge_plan
    ):
        candidates = []
        for plan in battery_charge_plans:
            if int(getattr(plan, "index", -1)) != index:
                continue
            segments = sorted(
                (
                    segment
                    for segment in getattr(plan, "segments", [])
                    if segment.end > context.now
                ),
                key=lambda segment: segment.start,
            )
            active_index = next(
                (
                    position
                    for position, segment in enumerate(segments)
                    if segment.start <= context.now < segment.end
                ),
                None,
            )
            if active_index is not None:
                # De planner bewaart ieder prijsuur als een apart segment.
                # Aaneengesloten segmenten zijn echter één laadperiode; toon
                # daarom pas na het laatste aansluitende uur "stand-by".
                continuous_end = segments[active_index].end
                for segment in segments[active_index + 1:]:
                    if segment.start > continuous_end:
                        break
                    continuous_end = max(continuous_end, segment.end)
                candidates.append((continuous_end, "stand-by", 0.0))
            elif segments:
                segment = segments[0]
                candidates.append((
                    segment.start,
                    "laden",
                    float(getattr(segment, "power", 0.0)
                          or getattr(plan, "planned_power", 0.0)),
                ))
        if discharge_plan is not None:
            per_battery = next(
                (
                    item for item in getattr(
                        discharge_plan, "per_battery", []
                    )
                    if int(getattr(item, "index", -1)) == index
                ),
                None,
            )
            power = float(
                getattr(per_battery, "expected_power", 0.0)
                if per_battery is not None else 0.0
            )
            for block in getattr(discharge_plan, "blocks", []):
                if block.end <= context.now:
                    continue
                if block.start <= context.now:
                    candidates.append((
                        block.end, "stand-by", 0.0
                    ))
                else:
                    candidates.append((
                        block.start,
                        "ontladen",
                        power,
                    ))
        export_plan = getattr(context, "export_plan", None)
        for block in (
            getattr(export_plan, "blocks", []) if export_plan else []
        ):
            if block.end <= context.now:
                continue
            if block.start <= context.now:
                candidates.append((
                    block.end, "zelfconsumptie", 0.0
                ))
            else:
                candidates.append((
                    block.start, "terugleveren", 0.0
                ))
        if not candidates:
            return "Geen volgende actie"
        moment, action, power = min(candidates, key=lambda item: item[0])
        label = f"{moment.strftime('%H:%M')} {action}"
        if power > 0:
            label += f" · {power:,.0f} W".replace(",", ".")
        return label

    def _state_number(self, entity):
        if not entity:
            return None
        try:
            return float(self.app.get_state(entity))
        except (TypeError, ValueError):
            return None

    def _publish_planning_overview(
        self,
        context,
        charge_plan,
        discharge_blocks,
        battery_charge_plans,
    ):
        active_powers = self._active_charge_powers(context)
        per_battery = self._battery_planning_items(
            context, battery_charge_plans
        )
        charge_slots = []
        for slot in getattr(charge_plan, "slots", []) if charge_plan else []:
            charge_slots.append({
                "start": self._iso(slot.get("from")),
                "einde": self._iso(slot.get("till")),
                "prijs": round(float(slot.get("price", 0.0)), 4),
            })

        average_charge_price = (
            float(charge_plan.average_price) if charge_plan else None
        )
        discharge_plan = context.discharge_plan
        discharge_cost_basis = (
            float(discharge_plan.cost_basis_per_kwh)
            if discharge_plan is not None
            and getattr(discharge_plan, "cost_basis_per_kwh", None) is not None
            else average_charge_price
        )
        discharge_items = []
        for number, block in enumerate(discharge_blocks, start=1):
            average_price = float(block.average_price)
            discharge_items.append({
                "nummer": number,
                "start": self._iso(block.start),
                "einde": self._iso(block.end),
                "uren": round(float(block.hours), 2),
                "gemiddelde_prijs": round(average_price, 4),
                "hoogste_prijs": round(float(block.highest_price), 4),
                "prijsverschil": (
                    round(average_price - discharge_cost_basis, 4)
                    if discharge_cost_basis is not None else None
                ),
                "verwachte_energie_kwh": round(
                    float(getattr(block, "expected_energy", 0.0)), 3
                ),
                "energiekosten_eur": round(
                    float(getattr(block, "energy_cost", 0.0)), 3
                ),
                "vermeden_inkoop_eur": round(
                    float(getattr(block, "avoided_cost", 0.0)), 3
                ),
                "omzetverlies_kwh": round(
                    float(getattr(block, "conversion_loss", 0.0)), 3
                ),
                "netto_resultaat_eur": round(
                    float(getattr(block, "net_profit", 0.0)), 3
                ),
            })

        export_plan = getattr(context, "export_plan", None)
        export_mode = getattr(
            self.app, "get_state", lambda _entity: None
        )("input_select.openems_grid_export_modus")
        export_items = []
        for number, block in enumerate(
            getattr(export_plan, "blocks", []) if export_plan else [],
            start=1,
        ):
            export_items.append({
                "nummer": number,
                "start": self._iso(block.start),
                "einde": self._iso(block.end),
                "uren": round(float(block.hours), 2),
                "gemiddelde_prijs": round(
                    float(block.average_price), 4
                ),
                "hoogste_prijs": round(float(block.highest_price), 4),
                "verwachte_export_kwh": round(
                    float(getattr(block, "expected_energy", 0.0)), 3
                ),
                "doel_export_w": round(
                    float(getattr(block, "planned_export_power", 0.0))
                ),
                "netto_resultaat_eur": round(
                    float(getattr(block, "net_profit", 0.0)), 3
                ),
            })

        planned_labels = []
        if charge_plan:
            planned_labels.append("laden")
        if discharge_items:
            planned_labels.append("ontladen")
        if export_items:
            planned_labels.append("terugleveren")
        state = (
            " en ".join(planned_labels) + " gepland"
            if planned_labels else "geen planning"
        )

        self._sensor(
            "sensor.openems_planningsoverzicht",
            state,
            icon="mdi:clipboard-text-clock-outline",
            friendly_name="OpenEMS planningsoverzicht",
            bijgewerkt=self._iso(context.now),
            laadplan={
                "beschikbaar": charge_plan is not None,
                "start": self._iso(charge_plan.start) if charge_plan else None,
                "einde": self._iso(charge_plan.end) if charge_plan else None,
                "gemiddelde_prijs": (
                    round(average_charge_price, 4)
                    if average_charge_price is not None else None
                ),
                "goedkoopste_prijs": (
                    round(float(charge_plan.cheapest_price), 4)
                    if charge_plan
                    and getattr(charge_plan, "cheapest_price", None) is not None
                    else None
                ),
                "prijsgrens": (
                    round(float(charge_plan.price_limit), 4)
                    if charge_plan
                    and getattr(charge_plan, "price_limit", None) is not None
                    else None
                ),
                "laadprijsmarge": round(
                    float(getattr(context, "price_margin", 0.0)), 4
                ),
                "minimale_laadtijd_uren": round(
                    float(getattr(context, "hours_needed", 0.0)), 2
                ),
                "gepland_venster_uren": (
                    round(float(getattr(charge_plan, "hours", 0.0)), 2)
                    if charge_plan else 0.0
                ),
                "totaal_vermogen_w": (
                    round(
                        float(
                            getattr(
                                charge_plan,
                                "power",
                                sum(getattr(context, "charge_distribution", [])),
                            )
                        )
                    )
                    if charge_plan else 0
                ),
                "actief_vermogen_w": round(sum(active_powers)),
                "minimum_vermogen_per_accu_w": round(
                    min(
                        (
                            plan.minimum_power
                            for plan in battery_charge_plans
                            if plan.grid_energy_needed > 0
                        ),
                        default=0.0,
                    )
                ),
                "voorkeur_minimum_per_accu_w": round(
                    min(
                        (
                            plan.preferred_min_power
                            for plan in battery_charge_plans
                            if plan.grid_energy_needed > 0
                        ),
                        default=0.0,
                    )
                ),
                "voorkeur_maximum_per_accu_w": round(
                    max(
                        (
                            plan.preferred_max_power
                            for plan in battery_charge_plans
                            if plan.grid_energy_needed > 0
                        ),
                        default=0.0,
                    )
                ),
                "per_accu": per_battery,
                "blokken": charge_slots,
            },
            ontlaadplan={
                "beschikbaar": discharge_plan is not None,
                "gemiddelde_laadprijs": (
                    round(discharge_cost_basis, 4)
                    if discharge_cost_basis is not None else None
                ),
                "kostprijsbron": (
                    getattr(
                        discharge_plan,
                        "cost_basis_source",
                        "economische laadreferentie",
                    )
                    if discharge_plan else None
                ),
                "ontlaadmarge": round(
                    float(getattr(context, "discharge_margin", 0.0)), 4
                ),
                "minimum_soc": round(
                    float(getattr(context, "minimum_soc", 0.0)), 1
                ),
                "beschikbare_energie_kwh": (
                    round(float(discharge_plan.available_energy), 2)
                    if discharge_plan else 0.0
                ),
                "ontladen_vanaf": (
                    round(float(discharge_plan.price_limit), 4)
                    if discharge_plan else None
                ),
                "tijdresolutie_minuten": (
                    int(getattr(discharge_plan, "resolution_minutes", 60))
                    if discharge_plan else None
                ),
                "minimaal_prijsverschil_per_kwh": (
                    round(float(getattr(
                        discharge_plan,
                        "minimum_price_difference",
                        0.0,
                    )), 4)
                    if discharge_plan else 0.0
                ),
                "extra_selectiemarge_per_kwh": (
                    round(float(getattr(
                        discharge_plan, "selection_margin", 0.0
                    )), 4)
                    if discharge_plan else 0.0
                ),
                "reserve_soc_pct": (
                    round(float(getattr(
                        discharge_plan, "reserve_soc", 0.0
                    )), 1)
                    if discharge_plan else 0.0
                ),
                "per_accu": [
                    {
                        "index": item.index,
                        "naam": item.name,
                        "huidige_soc": round(item.current_soc, 1),
                        "verwachte_eind_soc": round(
                            item.predicted_end_soc, 1
                        ),
                        "beschikbaar_kwh": round(
                            item.available_energy, 3
                        ),
                        "bruikbaar_kwh": round(item.usable_energy, 3),
                        "verwacht_ontladen_kwh": round(
                            item.expected_energy, 3
                        ),
                        "verwacht_vermogen_w": round(
                            item.expected_power
                        ),
                        "verwachte_duur_uren": round(
                            item.duration_hours, 2
                        ),
                        "ontlaadrendement_pct": round(
                            item.discharge_efficiency * 100, 1
                        ),
                    }
                    for item in (
                        getattr(discharge_plan, "per_battery", [])
                        if discharge_plan else []
                    )
                ],
                "blokken": discharge_items,
            },
            terugleverplan={
                "beschikbaar": export_plan is not None,
                "modus": export_mode or "Uit",
                "voorraadkostprijs_eur_kwh": (
                    round(float(export_plan.cost_basis_per_kwh), 4)
                    if export_plan
                    and export_plan.cost_basis_per_kwh is not None
                    else None
                ),
                "kostprijsbron": (
                    getattr(export_plan, "cost_basis_source", None)
                    if export_plan else None
                ),
                "minimaal_prijsverschil_eur_kwh": (
                    round(float(
                        export_plan.minimum_price_difference
                    ), 4)
                    if export_plan else 0.0
                ),
                "extra_selectiemarge_eur_kwh": (
                    round(float(export_plan.selection_margin), 4)
                    if export_plan else 0.0
                ),
                "piekmarge_eur_kwh": (
                    round(float(export_plan.selection_margin), 4)
                    if export_plan else 0.0
                ),
                "toegestaan_vanaf_eur_kwh": (
                    round(float(export_plan.price_limit), 4)
                    if export_plan else None
                ),
                "gepland_vanaf_eur_kwh": None,
                "dagpiekprijzen": (
                    dict(export_plan.daily_peak_prices)
                    if export_plan else {}
                ),
                "dagpiekgrenzen": (
                    dict(export_plan.daily_peak_limits)
                    if export_plan else {}
                ),
                "reserve_soc_pct": (
                    round(float(export_plan.reserve_soc), 1)
                    if export_plan else 0.0
                ),
                "beschikbare_energie_kwh": (
                    round(float(export_plan.available_energy), 2)
                    if export_plan else 0.0
                ),
                "gepland_exportvermogen_w": (
                    round(float(export_plan.planned_export_power))
                    if export_plan else 0
                ),
                "blokken": export_items,
            },
        )

    def _active_charge_powers(self, context):
        soc_values = list(getattr(
            context.battery,
            "soc_values",
            [context.battery.average_soc for _ in context.battery.names],
        ))
        powers = [0.0 for _ in soc_values]
        battery_charge_plans = list(
            getattr(context, "battery_charge_plans", [])
        )
        for plan in battery_charge_plans:
            if (
                plan.index >= len(powers)
                or soc_values[plan.index]
                >= context.target_soc
            ):
                continue
            if self._battery_charge_active(context, plan):
                powers[plan.index] = plan.planned_power
        if not battery_charge_plans and context.charge_plan and (
            context.charge_plan.start <= context.now < context.charge_plan.end
        ):
            powers = list(context.charge_distribution)
        scheduler = getattr(self.app, "scheduler", None)
        if scheduler is not None:
            powers = scheduler.filter_target_charge_powers(powers)
        monitor = getattr(self.app, "runtime_safety_monitor", None)
        return (
            monitor.filter_charge_powers(powers)
            if monitor is not None else powers
        )

    def _battery_charge_active(self, context, plan):
        if any(
            segment.start <= context.now < segment.end
            for segment in plan.segments
        ):
            return True
        if plan.end is None or context.now < plan.end:
            return False
        charge_plan = getattr(context, "charge_plan", None)
        slots = list(
            getattr(charge_plan, "eligible_slots", [])
            or getattr(charge_plan, "slots", [])
        )
        for slot in slots:
            start = self._datetime(slot.get("from"))
            end = self._datetime(slot.get("till"))
            if (
                start is not None
                and end is not None
                and start <= context.now < end
                and plan.end <= context.now
            ):
                return True
        return False

    @staticmethod
    def _datetime(value):
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None

    def _battery_planning_items(self, context, battery_charge_plans):
        if not battery_charge_plans:
            return [
                {
                    "index": index,
                    "naam": name,
                    "status": "wachtend",
                    "reden": "Gezamenlijke laadplanning",
                    "huidige_soc": round(
                        float(getattr(
                            context.battery, "average_soc", 0.0
                        )),
                        1,
                    ),
                    "doel_soc": round(
                        float(getattr(context, "target_soc", 0.0)), 1
                    ),
                    "benodigd_in_accu_kwh": 0.0,
                    "benodigd_uit_net_kwh": 0.0,
                    "minimum_vermogen_w": 0,
                    "voorkeur_minimum_w": 0,
                    "voorkeur_maximum_w": 0,
                    "vermogensstrategie": "geen_planning",
                    "laadvermogen_w": round(
                        float(context.charge_distribution[index])
                        if index < len(context.charge_distribution)
                        else 0.0
                    ),
                    "laadduur_uren": 0.0,
                    "gemiddelde_prijs": None,
                    "segmenten": [],
                }
                for index, name in enumerate(context.battery.names)
            ]
        result = []
        for plan in battery_charge_plans:
            soc = (
                context.battery.soc_values[plan.index]
                if plan.index < len(context.battery.soc_values)
                else plan.current_soc
            )
            active = next(
                (
                    segment for segment in plan.segments
                    if segment.start <= context.now < segment.end
                ),
                None,
            )
            future = [
                segment for segment in plan.segments
                if segment.start > context.now
            ]
            if soc >= context.target_soc:
                status = "voltooid"
                reason = "Doel-SOC bereikt"
            elif active or self._battery_charge_active(context, plan):
                status = "laden"
                reason = (
                    "Laadbuffer is actief; doel-SOC wordt gecontroleerd"
                    if active is None
                    else "Goedkoop individueel laadmoment is actief"
                )
            elif future:
                status = "wachtend"
                reason = "Wacht op het eerstvolgende goedkope laadmoment"
            elif plan.grid_energy_needed <= 0:
                status = "voltooid"
                reason = "Geen aanvullende energie nodig"
            else:
                status = "afgelopen"
                reason = "Geplande laadmomenten zijn verstreken"
            result.append({
                "index": plan.index,
                "naam": plan.name,
                "status": status,
                "reden": reason,
                "huidige_soc": round(float(soc), 1),
                "doel_soc": round(float(plan.target_soc), 1),
                "capaciteit_kwh": round(float(plan.capacity), 2),
                "benodigd_in_accu_kwh": round(
                    float(plan.battery_energy_needed), 3
                ),
                "benodigd_uit_net_kwh": round(
                    float(plan.grid_energy_needed), 3
                ),
                "laadrendement_pct": round(
                    float(plan.charge_efficiency) * 100, 1
                ),
                "berekend_laadverlies_kwh": round(
                    float(
                        plan.grid_energy_needed
                        - plan.battery_energy_needed
                    ),
                    3,
                ),
                "minimum_vermogen_w": round(float(plan.minimum_power)),
                "voorkeur_minimum_w": round(
                    float(plan.preferred_min_power)
                ),
                "voorkeur_maximum_w": round(
                    float(plan.preferred_max_power)
                ),
                "vermogensstrategie": plan.power_strategy,
                "laadvermogen_w": round(float(plan.planned_power)),
                "laadduur_uren": round(float(plan.duration_hours), 3),
                "start": self._iso(plan.start),
                "einde": self._iso(plan.end),
                "gemiddelde_prijs": (
                    round(float(plan.average_price), 4)
                    if plan.grid_energy_needed > 0 else None
                ),
                "segmenten": [
                    {
                        "start": self._iso(segment.start),
                        "einde": self._iso(segment.end),
                        "vermogen_w": round(float(segment.power)),
                        "prijs": round(float(segment.price), 4),
                        "energie_kwh": round(float(segment.energy_kwh), 3),
                    }
                    for segment in plan.segments
                ],
            })
        return result

    @staticmethod
    def _iso(value):
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def _sensor(self, entity_id, state, **attributes):
        # Home Assistant bewaart sensorstates als tekst. Een numerieke 0 kan
        # door AppDaemon als een ontbrekende (falsy) state worden behandeld.
        # Bij een nog niet bestaande virtuele sensor resulteert dat in HTTP
        # 400, omdat dan alleen attributen naar HA worden gestuurd.
        if state is None or (
            isinstance(state, float) and not math.isfinite(state)
        ):
            state = "unknown"
        else:
            state = str(state)
        self.app.set_state(
            entity_id,
            state=state,
            attributes=attributes,
            replace=True,
        )

    def publish_main_switch(self, enabled):
        self._sensor(
            "sensor.openems_hoofdschakelaar",
            "AUTO" if enabled else "HANDMATIG",
            icon="mdi:power-plug" if enabled else "mdi:power-plug-off",
            friendly_name="OpenEMS bedieningsmodus",
        )
