# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Uitvoering en gebeurtenislog van geplande netexport.

De economische keuze wordt uitsluitend door ``DischargePlanner`` gemaakt.
Deze module voert alleen een actief exportblok uit, bewaakt de harde
netgrens en stuurt met de snelle netmeting bij.
"""
from .. import ems_paths

from datetime import datetime
import time
from pathlib import Path

from .ems_config import (
    GRID_EXPORT_SAFETY_MARGIN_W,
    MAX_GRID_EXPORT_POWER_W,
)
from .persistence import JsonStore
from .models import DischargeBlock, DischargePlan


class GridExportPlanner:
    """Kleine, zuivere vermogensverdeler zonder economische logica."""

    MINIMUM_ACTIVE_POWER_W = 100.0
    EFFICIENT_POWER_TOLERANCE = 0.10

    @classmethod
    def distribute_power(cls, total_power, energy_weights, maximums):
        """Verdeel vermogen zonder een accu voor slechts enkele watts te starten."""
        limits = [max(0.0, float(value)) for value in maximums]
        result = cls._distribute_once(
            total_power, energy_weights, limits
        )
        too_small = {
            index for index, value in enumerate(result)
            if 0 < value < cls.MINIMUM_ACTIVE_POWER_W
        }
        if too_small:
            # Verwijder minieme bijdragen en verdeel het volledige verzoek
            # opnieuw over de overige accu's. Onvoldoende restcapaciteit mag
            # het totaal iets verlagen; daarvoor starten we geen extra accu.
            limits = [
                0.0 if index in too_small else value
                for index, value in enumerate(limits)
            ]
            result = cls._distribute_once(
                total_power, energy_weights, limits
            )
        return [
            0.0 if 0 < value < cls.MINIMUM_ACTIVE_POWER_W
            else round(value, 1)
            for value in result
        ]

    @classmethod
    def select_efficient_indexes(
        cls, target_power, ordered_indexes, maximums, preferred_powers
    ):
        """Kies eenmaal het kleinste aantal accu's dat efficiënt volstaat."""
        prefixes = []
        selected = []
        for raw_index in ordered_indexes:
            index = int(raw_index)
            if not 0 <= index < len(maximums):
                continue
            if float(maximums[index]) <= 0:
                continue
            selected.append(index)
            floor = sum(
                float(preferred_powers.get(item, 1250.0))
                * (1.0 - cls.EFFICIENT_POWER_TOLERANCE)
                for item in selected
            )
            ceiling = sum(
                min(
                    float(maximums[item]),
                    float(preferred_powers.get(item, 1250.0))
                    * (1.0 + cls.EFFICIENT_POWER_TOLERANCE),
                )
                for item in selected
            )
            target = float(target_power)
            if floor <= target + 0.5:
                feasible = min(ceiling, target)
                prefixes.append((target - feasible, len(selected), list(selected)))
        if prefixes:
            return min(prefixes)[2]
        return [int(ordered_indexes[0])] if ordered_indexes else []

    @classmethod
    def distribute_fixed_efficient(
        cls, requested, selected_indexes, maximums, preferred_powers,
        allow_below_band=False,
    ):
        """Verdeel over de vaste selectie binnen ±10% van elk optimum."""
        result = [0.0 for _ in maximums]
        selected = [
            int(index) for index in selected_indexes
            if 0 <= int(index) < len(maximums)
            and float(maximums[int(index)]) > 0
        ]
        if not selected:
            return result
        optimum = {
            index: min(
                float(maximums[index]),
                max(0.0, float(preferred_powers.get(index, 1250.0))),
            )
            for index in selected
        }
        lower = {
            index: value * (1.0 - cls.EFFICIENT_POWER_TOLERANCE)
            for index, value in optimum.items()
        }
        upper = {
            index: min(
                float(maximums[index]),
                value * (1.0 + cls.EFFICIENT_POWER_TOLERANCE),
            )
            for index, value in optimum.items()
        }
        target = max(0.0, float(requested))
        if target < sum(lower.values()):
            share = target / len(selected)
            for index in selected:
                result[index] = min(float(maximums[index]), share)
            return [round(value, 1) for value in result]

        assigned = min(sum(upper.values()), target)
        for index in selected:
            result[index] = optimum[index]
        difference = assigned - sum(result)
        bounds = upper if difference > 0 else lower
        remaining = abs(difference)
        open_indexes = list(selected)
        while open_indexes and remaining > 0.5:
            share = remaining / len(open_indexes)
            progressed = 0.0
            next_open = []
            for index in open_indexes:
                room = abs(bounds[index] - result[index])
                change = min(room, share)
                result[index] += change if difference > 0 else -change
                progressed += change
                if room - change > 0.5:
                    next_open.append(index)
            if progressed <= 0.5:
                break
            remaining -= progressed
            open_indexes = next_open
        return [round(value, 1) for value in result]

    @staticmethod
    def _distribute_once(total_power, energy_weights, maximums):
        count = len(maximums)
        result = [0.0] * count
        remaining = max(0.0, float(total_power))
        active = {
            index for index in range(count)
            if float(maximums[index]) > 0
            and index < len(energy_weights)
            and float(energy_weights[index]) > 0
        }
        while active and remaining > 0.5:
            weight = sum(float(energy_weights[index]) for index in active)
            before = remaining
            for index in list(active):
                share = (
                    remaining / len(active)
                    if weight <= 0
                    else remaining * float(energy_weights[index]) / weight
                )
                room = max(0.0, float(maximums[index]) - result[index])
                amount = min(room, share)
                result[index] += amount
                if room - amount <= 0.5:
                    active.remove(index)
            remaining = max(0.0, float(total_power) - sum(result))
            if abs(before - remaining) < 0.5:
                break
        return result

    @staticmethod
    def blocked(reason, count, **values):
        return {
            **values,
            "geschikt": False,
            "reden": reason,
            "gewenst_exportvermogen_w": 0.0,
            "gewenst_ontlaadvermogen_w": 0.0,
            "vermogens_per_accu_w": [0.0] * count,
        }


class GridExportManager:
    STATUS_SENSOR = "sensor.openems_grid_export_status"
    MAX_CONTROL_STEP_W = 750.0
    CONTROL_DEADBAND_W = 50.0

    def __init__(
        self,
        app,
        settings,
        controller,
        battery_manager,
        grid_calibrator,
        telemetry_guard,
        financial_tracker,
        timezone,
        data_file=None,
        safety_service=None,
    ):
        self.app = app
        self.settings = settings
        self.controller = controller
        self.battery_manager = battery_manager
        self.grid_calibrator = grid_calibrator
        self.telemetry_guard = telemetry_guard
        self.financial_tracker = financial_tracker
        self.safety_service = safety_service
        self.timezone = timezone
        self.planner = GridExportPlanner()
        self.active = False
        self._latched_block = None
        self._latched_plan = None
        self.last_decision = None
        self._last_event_key = None
        self._energy_block_key = None
        self._delivered_energy_kwh = 0.0
        self._last_energy_sample_at = None
        self._allocation_source = "Nog niet verdeeld"
        self._fixed_export_key = None
        self._fixed_export_indexes = []
        self.data_file = data_file or (
            ems_paths.data_parent()
            / "OpenEMS-M-data"
            / "grid_export_gebeurtenissen.json"
        )
        self.store = JsonStore(
            self.data_file,
            app=app,
            timezone=timezone,
            label="Grid Export-gebeurtenissen",
        )
        loaded = self.store.load(self._empty_data)
        self.data = loaded if isinstance(loaded, dict) else self._empty_data()
        self._prune_completed_blocks()

    def tick(self, kwargs=None):
        checkpoint = getattr(self.app, "runtime_checkpoint", None)
        if checkpoint is not None and checkpoint.startup_pending():
            return
        mode = self.settings.grid_export_mode()
        try:
            decision = self._decision()
        except (TypeError, ValueError) as error:
            # _decision() leest zelf al accugegevens, vóórdat de
            # telemetrie-betrouwbaarheidscheck verderop de kans krijgt dit
            # netjes af te vangen. Een tijdelijk onbeschikbare sensor (bv.
            # een accu die na een storing nog niet reageert) mag hier geen
            # onafgevangen crash van de hele tick veroorzaken.
            decision = {
                "geschikt": False,
                "reden": f"Kritieke sensordata is niet actueel: {error}",
            }
            self.last_decision = decision
            self._deactivate(safety=True)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        self.last_decision = decision
        net_status = getattr(self.app, "grid_status_monitor", None)
        if net_status is not None and not net_status.allows_export():
            decision = {
                **decision,
                "geschikt": False,
                "reden": net_status.status_reason(),
            }
            self.last_decision = decision
            self._deactivate(safety=True)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        export_allowed = getattr(
            self.settings, "grid_export_allowed", lambda: True
        )
        if not export_allowed():
            decision = {
                **decision,
                "geschikt": False,
                "reden": "Terugleveren aan het net staat uit",
            }
            self.last_decision = decision
            self._deactivate(safety=False)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        if mode != "automatic":
            self._deactivate(safety=False)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        if not self.controller.enabled:
            decision = {
                **decision,
                "geschikt": False,
                "reden": "OpenEMS-hoofdschakelaar staat niet op Auto",
            }
            self._deactivate(safety=True)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        freshness = self.telemetry_guard.check(
            include_grid=True, publish=False
        )
        if not freshness["ok"]:
            decision = {
                **decision,
                "geschikt": False,
                "reden": "Kritieke sensordata is niet actueel",
            }
            self._deactivate(safety=True)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        if not self._bms_safe():
            decision = {
                **decision,
                "geschikt": False,
                "reden": "BMS-, alarm- of foutstatus blokkeert export",
            }
            self._deactivate(safety=True)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        opposite = self._charging_batteries()
        # Een laadblok is geen exportconflict. Deze beveiliging mag uitsluitend
        # ingrijpen wanneer er nu werkelijk een exportblok uitgevoerd wordt.
        if opposite and decision.get("geschikt"):
            decision = {
                **decision,
                "geschikt": False,
                "reden": (
                    "Tegengestelde richting gedetecteerd; export geblokkeerd: "
                    + ", ".join(opposite)
                ),
            }
            self.last_decision = decision
            self.controller.set_stop(source="export")
            self.active = False
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        if not decision.get("geschikt"):
            self._deactivate(
                safety=False,
                direct_handoff=bool(
                    self.active
                    and decision.get("reden")
                    == "Geen gepland exportblok actief"
                ),
            )
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        block = self._active_block()
        self._record_delivered_energy(block)
        battery = self.battery_manager.read()
        if self._fixed_reserve_reached(
            battery, float(decision["reserve_soc"])
        ):
            decision = {
                **decision,
                "geschikt": False,
                "reden": "Ingestelde SOC-reserve is bereikt",
                "geleverd_kwh": self._delivered_energy_kwh,
            }
            self.last_decision = decision
            self._mark_block_completed(block)
            self._deactivate(safety=False)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return
        try:
            grid_power = float(
                self.grid_calibrator.corrected_grid_power()
            )
        except (TypeError, ValueError) as error:
            decision = {
                **decision,
                "geschikt": False,
                "reden": f"Snelle netmeting onbruikbaar: {error}",
            }
            self._deactivate(safety=True)
            self._publish_status(mode, decision)
            self._event(mode, decision)
            return

        # Het plan geeft totaal accuvermogen. Woningverbruik wordt niet apart
        # gecompenseerd; alleen het resterende vermogen gaat werkelijk naar
        # het net.
        current_total = (
            sum(self.controller.desired_powers)
            if self.active
            and self.controller.desired_state == "forced_discharge"
            else 0.0
        )
        target_total = max(
            0.0, float(decision["gewenst_ontlaadvermogen_w"])
        )
        requested = (
            min(self.MAX_CONTROL_STEP_W, target_total)
            if not self.active else target_total
        )
        balancer_export = getattr(
            self.settings, "load_balancer_enabled", lambda _kind: True
        )("terugleveren")
        hard_export_w = self._hard_export_limit()
        hard_limit_exceeded = (
            balancer_export and grid_power < -hard_export_w
        )
        if hard_limit_exceeded:
            requested = max(
                0.0,
                current_total
                - (-grid_power - hard_export_w)
                - GRID_EXPORT_SAFETY_MARGIN_W,
            )
        elif self.active:
            requested = min(
                current_total + self.MAX_CONTROL_STEP_W,
                max(current_total - self.MAX_CONTROL_STEP_W, requested),
            )
        powers = self._selector_powers(
            requested,
            battery,
            decision["reserve_soc"],
            target_total,
        )
        decision = {**decision, "vermogens_per_accu_w": powers}
        self.last_decision = decision
        if sum(powers) < 50:
            decision = {
                **decision,
                "geschikt": False,
                "reden": "Geen veilig ontlaadvermogen beschikbaar",
            }
            self._deactivate(safety=False)
            self._publish_status(mode, decision, grid_power)
            self._event(mode, decision, grid_power)
            return
        # self.active moet al True zijn vóórdat set_discharge() wordt
        # aangeroepen: die roept zelf al checkpoint.capture() aan, en de
        # eigenaarsbepaling daar leest deze vlag. Bij een mislukte opdracht
        # wordt de vorige waarde hersteld, zodat het gedrag bij falen gelijk
        # blijft aan voorheen.
        was_active = self.active
        self.active = True
        if self.controller.set_discharge(powers, source="export"):
            checkpoint = getattr(self.app, "runtime_checkpoint", None)
            if checkpoint is not None:
                checkpoint.capture()
        else:
            self.active = was_active
        self._publish_status(mode, decision, grid_power, sum(powers))
        self._event(mode, decision, grid_power, sum(powers))

    def controls_execution(self):
        return (
            getattr(
                self.settings, "grid_export_allowed", lambda: True
            )()
            and self.settings.grid_export_mode() == "automatic"
            and self._active_block() is not None
        )

    def stop(self, safety=True):
        self._deactivate(safety=safety)

    def finish_if_expired(self):
        """Sluit een verstreken exportblok vóór de volgende modus af."""
        was_exporting = self.active or (
            self.controller.desired_state == "forced_discharge"
        )
        if not was_exporting or self._active_block() is not None:
            return False
        self._deactivate(safety=False, direct_handoff=True)
        return True

    def _decision(self, soc_values=None):
        context = getattr(self.app, "_context", None)
        battery = self.battery_manager.read()
        count = len(battery.names)
        block = self._active_block()
        plan = (
            getattr(context, "export_plan", None)
            if context is not None else None
        ) or self._latched_plan
        if plan is None or block is None:
            return self.planner.blocked(
                "Geen gepland exportblok actief", count
            )
        minimum_soc = float(
            getattr(context, "minimum_soc", 0.0)
        )
        block_reserve = getattr(block, "reserve_soc", None)
        reserve_soc = max(
            minimum_soc,
            float(
                block_reserve
                if block_reserve is not None
                else getattr(plan, "reserve_soc", minimum_soc)
            ),
        )
        values = list(soc_values or battery.soc_values)
        usable = [
            max(0.0, float(capacity))
            * max(0.0, float(soc) - reserve_soc) / 100
            for capacity, soc in zip(battery.capacities, values)
        ]
        exportable = sum(
            value * self._discharge_efficiency(index)
            for index, value in enumerate(usable)
        )
        if exportable <= 0.001:
            return self.planner.blocked(
                "Ingestelde SOC-reserve is bereikt",
                count,
                reserve_soc=reserve_soc,
                exporteerbaar_kwh=0.0,
            )
        home_power_reader = getattr(self.settings, "home_power", None)
        home_power = (
            home_power_reader()
            if callable(home_power_reader)
            else self._number(self.settings.home_entity())
        )
        if home_power is None:
            return self.planner.blocked(
                "Actueel woningvermogen ontbreekt; export is veilig geblokkeerd",
                count,
                reserve_soc=reserve_soc,
                exporteerbaar_kwh=exportable,
            )
        inventory_cost = self.financial_tracker.average_inventory_cost()
        export_price = self.settings.grid_export_price()
        planned_cost = getattr(block, "cost_basis_per_kwh", None)
        if export_price is None or (
            planned_cost is None and inventory_cost is None
        ):
            return self.planner.blocked(
                "Exportprijs of werkelijke voorraadkostprijs ontbreekt",
                count,
                reserve_soc=reserve_soc,
            )
        efficiency_reader = getattr(
            self.settings, "export_efficiency", None
        )
        efficiency, efficiency_source = (
            efficiency_reader()
            if callable(efficiency_reader)
            else (1.0, "geen rendement")
        )
        comparison_cost = (
            float(planned_cost)
            if planned_cost is not None
            else float(inventory_cost) / max(0.60, efficiency)
        )
        difference = float(export_price) - comparison_cost
        minimum = float(
            getattr(plan, "minimum_price_difference", 0.0)
        )
        if difference + 1e-9 < minimum:
            return self.planner.blocked(
                "Actueel prijsverschil is onder het ingestelde minimum",
                count,
                reserve_soc=reserve_soc,
                exportprijs=export_price,
                voorraadkosten_per_kwh=inventory_cost,
                vergelijkingskostprijs_per_kwh=comparison_cost,
                exportrendement=efficiency,
                rendementsbron=efficiency_source,
                nettowinst_per_kwh=difference,
            )
        configured_export = min(
            self._hard_export_limit(),
            self._helper(
                "grid_export_max_power", MAX_GRID_EXPORT_POWER_W
            ),
        )
        planned_total = max(
            0.0,
            float(
                getattr(block, "planned_total_power", 0.0)
                or getattr(block, "planned_export_power", 0.0)
            ),
        )
        # Netexport maximaal 5000 W; het woningverbruik mag er bovenop,
        # want dat komt nooit op het net.
        planned_total = min(
            configured_export - GRID_EXPORT_SAFETY_MARGIN_W
            + max(0.0, float(home_power)),
            planned_total,
        )
        desired_total = min(
            sum(battery.max_discharge_values),
            planned_total,
        )
        desired_export = max(0.0, desired_total - max(0.0, float(home_power)))
        powers = self.planner.distribute_power(
            desired_total, usable, battery.max_discharge_values
        )
        return {
            "geschikt": True,
            "reden": "Gepland winstgevend exportblok actief",
            "reserve_soc": reserve_soc,
            "reserve_kwh": sum(battery.capacities) * reserve_soc / 100,
            "exporteerbaar_kwh": exportable,
            "exportprijs": export_price,
            "voorraadkosten_per_kwh": inventory_cost,
            "vergelijkingskostprijs_per_kwh": comparison_cost,
            "exportrendement": efficiency,
            "rendementsbron": efficiency_source,
            "kostprijsbron": (
                "vastgelegd bij planning"
                if planned_cost is not None else "actuele voorraadkost"
            ),
            "nettowinst_per_kwh": difference,
            "woningvermogen_w": home_power,
            "gewenst_exportvermogen_w": desired_export,
            "gewenst_ontlaadvermogen_w": desired_total,
            "vermogens_per_accu_w": powers,
            "blok_start": block.start.isoformat(),
            "blok_einde": block.end.isoformat(),
            "doel_energie_kwh": float(
                getattr(block, "expected_energy", 0.0) or 0.0
            ),
            "geleverd_kwh": self._delivered_energy_kwh,
        }

    def _active_block(self, now=None):
        context = getattr(self.app, "_context", None)
        plan = getattr(context, "export_plan", None)
        moment = now or datetime.now(self.timezone)
        if self.active and self._latched_block is not None:
            if (
                self._latched_block.start
                <= moment
                < self._latched_block.end
            ):
                return self._latched_block
            # De oorspronkelijke eindtijd blijft leidend; een herberekening
            # mag een lopend blok niet ongemerkt verlengen.
            self._latched_block = None
            self._latched_plan = None
            return None
        if plan is None:
            return None
        block = next(
            (
                block for block in plan.blocks
                if block.start <= moment < block.end
                and not self._block_completed(block)
            ),
            None,
        )
        if block is not None:
            self._latched_block = block
            self._latched_plan = plan
        return block

    def active_block(self):
        """Geeft het werkelijk uitgevoerde, vergrendelde blok terug."""
        return self._active_block() if self.active else None

    def locked_active_block(self, now=None):
        """Geeft een voor dit uur vastgezet blok ook tijdens herstel terug."""
        moment = now or datetime.now(self.timezone)
        block = self._latched_block
        if block is not None and block.start <= moment < block.end:
            return block
        return None

    def checkpoint(self):
        """Geeft uitsluitend de actieve, vastgezette exportuitvoering terug."""
        block = self._active_block()
        plan = self._latched_plan
        if not self.active or block is None or plan is None:
            return {}
        return {
            "start": block.start.isoformat(),
            "end": block.end.isoformat(),
            "average_price": float(block.average_price),
            "highest_price": float(block.highest_price),
            "planned_total_power": float(block.planned_total_power),
            "planned_export_power": float(block.planned_export_power),
            "expected_energy": float(block.expected_energy),
            "delivered_energy": float(self._delivered_energy_kwh),
            "powers_per_battery": list(map(
                float, block.powers_per_battery
            )),
            "reserve_soc": (
                float(block.reserve_soc)
                if block.reserve_soc is not None else None
            ),
            "price_limit": float(plan.price_limit),
            "available_energy": float(plan.available_energy),
            "cost_basis_per_kwh": plan.cost_basis_per_kwh,
            "block_cost_basis_per_kwh": getattr(
                block, "cost_basis_per_kwh", None
            ),
            "minimum_price_difference": float(
                plan.minimum_price_difference
            ),
            "selection_margin": float(plan.selection_margin),
        }

    def restore_checkpoint(self, data):
        """Herstelt een nog geldig exportblok; tick regelt daarna weer live."""
        if (
            not isinstance(data, dict)
            or not self.settings.grid_export_allowed()
            or self.settings.grid_export_mode() != "automatic"
            or not self.controller.enabled
        ):
            return False
        try:
            start = datetime.fromisoformat(str(data["start"]))
            end = datetime.fromisoformat(str(data["end"]))
            now = datetime.now(self.timezone)
            if start.tzinfo is None:
                start = start.replace(tzinfo=self.timezone)
            if end.tzinfo is None:
                end = end.replace(tzinfo=self.timezone)
            start = start.astimezone(self.timezone)
            end = end.astimezone(self.timezone)
            if not start <= now < end:
                return False
            block = DischargeBlock(
                start=start,
                end=end,
                average_price=float(data["average_price"]),
                highest_price=float(data["highest_price"]),
                planned_total_power=float(data["planned_total_power"]),
                planned_export_power=float(data["planned_export_power"]),
                expected_energy=float(data.get("expected_energy", 0.0)),
                powers_per_battery=list(map(
                    float, data.get("powers_per_battery", [])
                )),
                reserve_soc=(
                    float(data["reserve_soc"])
                    if data.get("reserve_soc") is not None else None
                ),
                cost_basis_per_kwh=(
                    float(data["block_cost_basis_per_kwh"])
                    if data.get("block_cost_basis_per_kwh") is not None
                    else (
                        float(data["cost_basis_per_kwh"])
                        if data.get("cost_basis_per_kwh") is not None else None
                    )
                ),
            )
            plan = DischargePlan(
                price_limit=float(data["price_limit"]),
                available_energy=float(data["available_energy"]),
                blocks=[block],
                cost_basis_per_kwh=(
                    float(data["cost_basis_per_kwh"])
                    if data.get("cost_basis_per_kwh") is not None else None
                ),
                reserve_soc=float(data.get("reserve_soc") or 0.0),
                minimum_price_difference=float(
                    data.get("minimum_price_difference", 0.0)
                ),
                selection_margin=float(
                    data.get("selection_margin", 0.0)
                ),
                planned_total_power=float(data["planned_total_power"]),
                planned_export_power=float(data["planned_export_power"]),
            )
        except (KeyError, TypeError, ValueError):
            return False
        self._latched_block = block
        self._latched_plan = plan
        self.active = True
        self._energy_block_key = self._block_key(block)
        self._delivered_energy_kwh = max(
            0.0, float(data.get("delivered_energy", 0.0))
        )
        self._last_energy_sample_at = time.monotonic()
        self.app.run_in(self.tick, 0)
        return True

    def _deactivate(self, safety, direct_handoff=False):
        self._latched_block = None
        self._latched_plan = None
        self._energy_block_key = None
        self._delivered_energy_kwh = 0.0
        self._last_energy_sample_at = None
        self._fixed_export_key = None
        self._fixed_export_indexes = []
        controller_is_exporting = (
            self.controller.desired_state == "forced_discharge"
        )
        if not self.active and not controller_is_exporting:
            return
        self.active = False
        scheduler = getattr(self.app, "scheduler", None)
        handoff = getattr(scheduler, "mark_external_handoff", None)
        can_handoff = bool(
            direct_handoff
            and not safety
            and callable(handoff)
            and hasattr(self.app, "execute_plan")
        )
        if can_handoff:
            handoff("Terugleverblok beëindigd")
        else:
            stopped = self.controller.set_stop(source="export")
            if stopped and scheduler is not None:
                scheduler.mark_external_stop("Terugleverblok beëindigd")
        if not safety and hasattr(self.app, "run_in"):
            self.app.run_in(self.app.execute_plan, 0)

    def _bms_safe(self):
        if self.safety_service is not None:
            return self.safety_service.all_safe("export")
        monitor = getattr(self.app, "runtime_safety_monitor", None)
        if monitor is None:
            return False
        return all(
            monitor.is_battery_operation_safe(
                index, "forced_discharge"
            ).safe
            for index in range(len(self.controller.batteries))
        )

    def _selector_powers(
        self, requested, battery, reserve_soc, selection_target=None
    ):
        coordinator = getattr(
            self.app, "coordinated_self_consumption", None
        )
        selector = getattr(coordinator, "battery_selector", None)
        profiles = self._learned_discharge_profiles()
        preferred = {
            index: profile.get("voorkeursvermogen_w")
            for index, profile in profiles.items()
            if profile.get("voorkeursvermogen_w") is not None
        }
        order_reader = getattr(selector, "export_order", None)
        order = (
            order_reader(battery, reserve_soc)
            if callable(order_reader) else []
        )
        if not order:
            self._allocation_source = "Verdelen over alle beschikbare accu's"
            return self.planner.distribute_power(
                requested,
                self._surplus_weights(reserve_soc, battery),
                battery.max_discharge_values,
            )
        block_key = self._block_key(self._active_block())
        if block_key != self._fixed_export_key:
            self._fixed_export_key = block_key
            self._fixed_export_indexes = self.planner.select_efficient_indexes(
                selection_target if selection_target is not None else requested,
                order,
                battery.max_discharge_values,
                preferred,
            )
        self._allocation_source = (
            "Vaste exportselectie - zelfgeleerd optimum +/-10%"
            if preferred else "Vaste exportselectie - efficient optimum +/-10%"
        )
        return self.planner.distribute_fixed_efficient(
            requested,
            self._fixed_export_indexes,
            battery.max_discharge_values,
            preferred,
            allow_below_band=(
                selection_target is not None
                and float(requested) + 0.5 < float(selection_target)
            ),
        )

    def _fixed_reserve_reached(self, battery, reserve_soc):
        return bool(self._fixed_export_indexes) and any(
            float(battery.soc_values[index]) <= float(reserve_soc) + 1e-9
            for index in self._fixed_export_indexes
        )

    def _charging_batteries(self):
        charging = []
        disabled = getattr(self.controller, "battery_disabled", None)
        for index, battery in enumerate(self.controller.batteries):
            if callable(disabled) and disabled(index):
                continue
            actual = self._number(battery.get("actual_power"))
            charge = self._number(battery.get("charge_power"))
            force = str(
                self.app.get_state(battery.get("force_mode")) or ""
            ).lower()
            if (
                (actual is not None and actual < -100.0)
                or (charge is not None and charge > 100.0)
                or force == "charge"
            ):
                charging.append(battery.get("name", f"Accu {index + 1}"))
        return charging

    def _learned_discharge_profiles(self):
        learner = getattr(self.app, "efficiency_learner", None)
        reader = getattr(learner, "discharge_power_profiles", None)
        result = {}
        if not callable(reader):
            return result
        # De aparte keuze Rendement bij terugleveren is hier leidend. Uit en
        # Handmatig houden het gevraagde aantal kWh leidend; Automatisch mag
        # de betrouwbare vermogensprofielen gebruiken om de accukeuze en het
        # werkpunt economisch te optimaliseren.
        efficiency_reader = getattr(self.settings, "export_efficiency", None)
        if not callable(efficiency_reader):
            return result
        try:
            _efficiency, source = efficiency_reader()
        except (KeyError, TypeError, ValueError):
            return result
        if source != "zelfgeleerd":
            return result
        try:
            profiles = reader()
        except (KeyError, TypeError, ValueError):
            return result
        for index, summary in enumerate(profiles):
            try:
                if isinstance(summary, dict):
                    result[index] = summary
            except (AttributeError, TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _block_key(block):
        if block is None:
            return None
        return f"{block.start.isoformat()}|{block.end.isoformat()}"

    def _record_delivered_energy(self, block):
        key = self._block_key(block)
        now = time.monotonic()
        if key is None:
            return
        if key != self._energy_block_key:
            self._energy_block_key = key
            self._delivered_energy_kwh = 0.0
            self._last_energy_sample_at = now
            return
        if self._last_energy_sample_at is None:
            self._last_energy_sample_at = now
            return
        elapsed = max(0.0, min(120.0, now - self._last_energy_sample_at))
        self._last_energy_sample_at = now
        if elapsed <= 0:
            return
        disabled = getattr(self.controller, "battery_disabled", None)
        actual_total = sum(
            max(0.0, self._number(battery.get("actual_power")) or 0.0)
            for index, battery in enumerate(self.controller.batteries)
            if not (callable(disabled) and disabled(index))
        )
        self._delivered_energy_kwh += actual_total * elapsed / 3600000.0

    def _block_completed(self, block):
        return self._block_key(block) in set(
            self.data.get("completed_blocks", [])
        )

    def _mark_block_completed(self, block):
        key = self._block_key(block)
        if key is None:
            return
        completed = self.data.setdefault("completed_blocks", [])
        if key not in completed:
            completed.append(key)
        self._prune_completed_blocks()
        self._save()

    def _prune_completed_blocks(self):
        oldest = datetime.now(self.timezone).timestamp() - 2 * 86400
        kept = []
        for key in self.data.get("completed_blocks", []):
            try:
                end = datetime.fromisoformat(str(key).split("|", 1)[1])
                if end.tzinfo is None:
                    end = end.replace(tzinfo=self.timezone)
                if end.timestamp() >= oldest:
                    kept.append(key)
            except (IndexError, TypeError, ValueError):
                continue
        self.data["completed_blocks"] = kept[-100:]

    @staticmethod
    def _surplus_weights(reserve_soc, battery):
        return [
            capacity * max(0.0, soc - reserve_soc) / 100
            for capacity, soc in zip(
                battery.capacities, battery.soc_values
            )
        ]

    def _event(
        self, mode, decision, grid_power=None, command_power=None
    ):
        key = (
            mode,
            bool(self.active),
            str(decision.get("reden")),
            str(decision.get("blok_start")),
        )
        if key == self._last_event_key:
            return
        self._last_event_key = key
        try:
            soc_per_battery = list(self.battery_manager.read().soc_values)
        except (TypeError, ValueError):
            # Deze gebeurtenis wordt ook geregistreerd wanneer juist een
            # ontbrekende accusensor de reden is dat export niet actief is;
            # dat mag het wegschrijven van de gebeurtenis zelf niet blokkeren.
            soc_per_battery = []
        event = {
            "tijd": datetime.now(self.timezone).isoformat(),
            "modus": mode,
            "actief": bool(self.active),
            "reden": decision.get("reden"),
            "exportprijs_eur_kwh": self._rounded(
                decision.get("exportprijs"), 4
            ),
            "voorraadkostprijs_eur_kwh": self._rounded(
                decision.get("voorraadkosten_per_kwh"), 4
            ),
            "prijsverschil_eur_kwh": self._rounded(
                decision.get("nettowinst_per_kwh"), 4
            ),
            "reserve_soc_pct": self._rounded(
                decision.get("reserve_soc"), 1
            ),
            "doel_export_w": self._rounded(
                decision.get("gewenst_exportvermogen_w"), 0
            ),
            "werkelijk_netvermogen_w": self._rounded(grid_power, 0),
            "opdracht_ontladen_w": self._rounded(command_power, 0),
            "soc_per_accu_pct": soc_per_battery,
        }
        events = self.data.setdefault("events", [])
        events.append(event)
        del events[:-250]
        self._save()
        if hasattr(self.app, "log"):
            self.app.log(
                "[Export] {}: {}".format(
                    "actief" if self.active else "niet actief",
                    decision.get("reden"),
                )
            )

    def _publish_status(
        self, mode, decision, grid_power=None, command_power=None
    ):
        labels = {
            "off": "Uit",
            "simulation": "Alleen simuleren",
            "automatic": "Automatisch terugleveren",
        }
        active = self.active and mode == "automatic"
        active_block = self._active_block()
        planned = active_block is not None
        dynamic_reader = getattr(
            self.settings, "dynamic_export_reserve_enabled", None
        )
        dynamic_enabled = bool(
            dynamic_reader() if callable(dynamic_reader) else False
        )
        base_plan = getattr(
            getattr(self.app, "_context", None), "export_plan", None
        )
        base_reserve = getattr(
            base_plan, "reserve_soc", decision.get("reserve_soc")
        )
        dynamic_active = (
            active
            and decision.get("reserve_soc") is not None
            and active_block is not None
            and getattr(active_block, "reserve_soc", None) is not None
            and float(decision.get("reserve_soc"))
            < float(base_reserve) - 0.1
        )
        state = (
            "terugleveren actief" if active
            else "terugleveren gepland" if planned
            else "uit" if mode == "off"
            else "wachten"
        )
        self.app.set_state(
            self.STATUS_SENSOR,
            state=state,
            attributes={
                "friendly_name": "OpenEMS terugleverstatus",
                "icon": "mdi:transmission-tower-export",
                "modus": labels.get(mode, mode),
                "reden": decision.get("reden"),
                "export_actief": active,
                "export_gepland": planned,
                "harde_netgrens_w": self._hard_export_limit(),
                "normale_regelgrens_w": (
                    self._hard_export_limit()
                    - GRID_EXPORT_SAFETY_MARGIN_W
                ),
                "exportprijs_eur_kwh": self._rounded(
                    decision.get("exportprijs"), 4
                ),
                "voorraadkostprijs_eur_kwh": self._rounded(
                    decision.get("voorraadkosten_per_kwh"), 4
                ),
                "vergelijkingskostprijs_eur_kwh": self._rounded(
                    decision.get("vergelijkingskostprijs_per_kwh"), 4
                ),
                "exportrendement_pct": self._rounded(
                    (
                        decision.get("exportrendement") * 100
                        if decision.get("exportrendement") is not None
                        else None
                    ),
                    1,
                ),
                "rendementsbron": decision.get("rendementsbron"),
                "nettowinst_eur_kwh": self._rounded(
                    decision.get("nettowinst_per_kwh"), 4
                ),
                "reserve_soc_pct": self._rounded(
                    decision.get("reserve_soc"), 1
                ),
                "dynamische_reserve_ingeschakeld": (
                    dynamic_enabled
                ),
                "dynamische_reserve_actief": dynamic_active,
                "exporteerbaar_kwh": self._rounded(
                    decision.get("exporteerbaar_kwh"), 2
                ),
                "doel_export_w": self._rounded(
                    decision.get("gewenst_exportvermogen_w"), 0
                ),
                "werkelijk_netvermogen_w": self._rounded(grid_power, 0),
                "opdracht_ontladen_w": self._rounded(command_power, 0),
                "verdelingsbron": self._allocation_source,
                "geleverd_in_blok_kwh": self._rounded(
                    self._delivered_energy_kwh, 3
                ),
                "doelenergie_blok_kwh": self._rounded(
                    decision.get("doel_energie_kwh"), 3
                ),
                "vermogens_per_accu_w": [
                    round(float(value))
                    for value in decision.get("vermogens_per_accu_w", [])
                ],
                "laatste_gebeurtenissen": self.data.get("events", [])[-20:],
            },
            replace=True,
        )

    @staticmethod
    def _empty_data():
        return {
            "version": 4,
            "events": [],
            "completed_blocks": [],
        }

    def _save(self):
        self.store.save(self.data)

    def _discharge_efficiency(self, index):
        method = getattr(self.settings, "effective_efficiencies", None)
        if callable(method):
            values = method()[1]
            if index < len(values):
                return max(0.01, float(values[index]))
        return 1.0

    def _hard_export_limit(self):
        getter = getattr(self.settings, "max_export_w", None)
        if callable(getter):
            try:
                value = float(getter())
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        return float(MAX_GRID_EXPORT_POWER_W)

    def _helper(self, name, default):
        entities = {
            "grid_export_max_power": (
                "input_number.openems_maximaal_exportvermogen"
            ),
        }
        try:
            return float(self.app.get_state(entities[name]))
        except (KeyError, TypeError, ValueError):
            return float(default)

    def _number(self, entity):
        if not entity:
            return None
        try:
            return float(self.app.get_state(entity))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _rounded(value, digits):
        try:
            return round(float(value), digits)
        except (TypeError, ValueError):
            return None
