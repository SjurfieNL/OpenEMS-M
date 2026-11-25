# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Bekende v0.10.1-planner voor geplande zelfconsumptie."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import BatteryDischargePlan, DischargeBlock, DischargePlan


class SelfConsumptionPlanner:
    def __init__(self, timezone):
        self.timezone = ZoneInfo(timezone)

    def calculate(
        self,
        future_prices,
        average_charge_price,
        discharge_margin,
        minimum_soc,
        avg_soc=None,
        capacity=None,
        names=None,
        soc_values=None,
        capacities=None,
        discharge_efficiencies=None,
        resolution="Automatisch",
        minimum_block_minutes=30,
        excluded_intervals=None,
        average_charge_prices_by_day=None,
    ):
        names, soc_values, capacities = self._battery_inputs(
            names, soc_values, capacities, avg_soc, capacity
        )
        count = len(names)
        discharge_efficiencies = self._efficiencies(
            discharge_efficiencies, count, 1.0
        )
        # Zelfconsumptie vergelijkt dezelfde all-in prijzen die de gebruiker
        # in de Frank-grafiek ziet. De ingestelde marge is de enige marge.
        price_reference = max(0.0, float(average_charge_price))
        daily_references = {
            str(day): max(0.0, float(value))
            for day, value in (
                average_charge_prices_by_day or {}
            ).items()
        }
        price_limit = price_reference + float(discharge_margin)
        slots, resolution_minutes = self._resample(
            future_prices, resolution
        )
        excluded_intervals = excluded_intervals or []
        slots = [
            slot for slot in slots
            if not self._overlaps_any(slot, excluded_intervals)
        ]

        eligible_slots = [
            slot for slot in slots
            if float(slot["price"]) >= (
                self._reference_for_slot(
                    slot, daily_references, price_reference
                )
                + float(discharge_margin)
            )
        ]

        raw_blocks = self._contiguous_blocks(eligible_slots)
        minimum_hours = max(0.0, float(minimum_block_minutes) / 60)
        raw_blocks = [
            block for block in raw_blocks
            if self._block_hours(block) + 1e-9 >= minimum_hours
        ]

        available_battery = [
            cap * max(0.0, soc - float(minimum_soc)) / 100
            for soc, cap in zip(soc_values, capacities)
        ]
        usable_battery = [
            energy * efficiency
            for energy, efficiency in zip(
                available_battery, discharge_efficiencies
            )
        ]
        selected_slots = [
            slot
            for block in raw_blocks
            for slot in block
        ]
        selected_groups = self._contiguous_blocks(selected_slots)
        expected_by_battery = [0.0 for _ in usable_battery]
        blocks = []
        for slot_group in selected_groups:
            average_price = self._weighted_average(slot_group)
            blocks.append(DischargeBlock(
                start=self._local_datetime(slot_group[0]["from"]),
                end=self._local_datetime(slot_group[-1]["till"]),
                average_price=average_price,
                highest_price=max(
                    float(item["price"]) for item in slot_group
                ),
                slots=[self._public_slot(item) for item in slot_group],
                profitable=True,
            ))

        per_battery = []
        for index, name in enumerate(names):
            expected = expected_by_battery[index]
            per_battery.append(BatteryDischargePlan(
                index=index,
                name=name,
                current_soc=soc_values[index],
                minimum_soc=float(minimum_soc),
                predicted_end_soc=soc_values[index],
                capacity=capacities[index],
                available_energy=available_battery[index],
                usable_energy=usable_battery[index],
                expected_energy=expected,
                expected_power=0.0,
                duration_hours=0.0,
                discharge_efficiency=discharge_efficiencies[index],
            ))

        return DischargePlan(
            price_limit=price_limit,
            available_energy=sum(available_battery),
            blocks=blocks,
            eligible_slots=[
                self._public_slot(item) for item in eligible_slots
            ],
            per_battery=per_battery,
            resolution_minutes=resolution_minutes,
            cost_basis_per_kwh=price_reference,
            cost_basis_source="all-in prijs gepland laadblok",
        )

    @staticmethod
    def lock_started_interval(plan, previous_plan, now):
        """Behoud een reeds gestart zelfconsumptieblok tot zijn eindtijd."""
        if plan is None or previous_plan is None:
            return plan

        locked = next(
            (
                block for block in previous_plan.blocks
                if block.start <= now < block.end
            ),
            None,
        )
        if locked is None:
            return plan

        blocks = [
            block for block in plan.blocks
            if block.end <= locked.start or block.start >= locked.end
        ]
        blocks.append(locked)
        blocks.sort(key=lambda block: block.start)

        eligible = {
            (str(slot["from"]), str(slot["till"])): dict(slot)
            for slot in plan.eligible_slots
        }
        for slot in locked.slots:
            eligible[(str(slot["from"]), str(slot["till"]))] = dict(slot)

        return replace(
            plan,
            blocks=blocks,
            eligible_slots=sorted(
                eligible.values(),
                key=lambda slot: str(slot["from"]),
            ),
        )

    @staticmethod
    def _public_slot(item):
        return {
            key: value for key, value in item.items()
            if not str(key).startswith("_")
        }

    def _resample(self, prices, resolution):
        normalized = sorted(
            (dict(item) for item in prices),
            key=lambda item: self._local_datetime(item["from"]).timestamp(),
        )
        unique = {}
        for item in normalized:
            start = self._local_datetime(item["from"])
            end = self._local_datetime(item["till"])
            if end.timestamp() > start.timestamp():
                unique[(start.timestamp(), end.timestamp())] = item
        normalized = list(unique.values())
        # Gebruik de volledige bronintervallen om de bronresolutie te
        # herkennen. Het eerste uitvoerbare interval kan na een herplanning
        # immers al deels verstreken zijn (bijvoorbeeld 19:07-20:00).
        # Met min() werd zo'n restuur ten onrechte als kwartierbron gezien.
        native = max(
            (
                round(
                    (
                        self._local_datetime(item["till"]).timestamp()
                        - self._local_datetime(item["from"]).timestamp()
                    ) / 60
                )
                for item in normalized
            ),
            default=60,
        )
        text = str(resolution).lower()
        target = 15 if "15" in text else 60 if "60" in text else native
        if target == 15:
            result = []
            for item in normalized:
                start = self._local_datetime(item["from"])
                end = self._local_datetime(item["till"])
                while start.timestamp() < end.timestamp():
                    till = start + timedelta(minutes=15)
                    if till.timestamp() > end.timestamp():
                        till = end
                    result.append({
                        "from": start.isoformat(),
                        "till": till.isoformat(),
                        "price": float(item["price"]),
                    })
                    start = till
            return result, 15
        if target == 60 and native < 60:
            groups = {}
            for item in normalized:
                start = self._local_datetime(item["from"])
                key = (
                    start.replace(minute=0, second=0, microsecond=0)
                    .timestamp()
                )
                groups.setdefault(key, []).append(item)
            result = []
            for start_timestamp, items in sorted(groups.items()):
                if self._block_hours(items) <= 0:
                    continue
                start = min(
                    self._local_datetime(item["from"])
                    for item in items
                )
                end = max(
                    self._local_datetime(item["till"])
                    for item in items
                )
                result.append({
                    "from": start.isoformat(),
                    "till": end.isoformat(),
                    "price": self._weighted_average(items),
                })
            return result, 60
        return normalized, int(native)

    def _contiguous_blocks(self, slots):
        blocks = []
        current = []
        for item in slots:
            previous_end = (
                self._local_datetime(current[-1]["till"])
                if current else None
            )
            item_start = self._local_datetime(item["from"])
            if current and (
                previous_end.timestamp() != item_start.timestamp()
                or self._local_datetime(
                    current[-1]["from"]
                ).date() != item_start.date()
            ):
                blocks.append(current)
                current = []
            current.append(item)
        if current:
            blocks.append(current)
        return blocks

    def _reference_for_slot(self, slot, daily_references, fallback):
        day = str(self._local_datetime(slot["from"]).date())
        return float(daily_references.get(day, fallback))

    def _battery_inputs(
        self, names, soc_values, capacities, avg_soc, capacity
    ):
        if names is not None and soc_values is not None and capacities is not None:
            return list(names), list(map(float, soc_values)), list(map(float, capacities))
        return (
            ["Accusysteem"],
            [float(avg_soc or 0.0)],
            [float(capacity or 0.0)],
        )

    @staticmethod
    def _values(values, count, default):
        result = list(values or [])
        return [
            float(result[index]) if index < len(result) else float(default)
            for index in range(count)
        ]

    def _efficiencies(self, values, count, default):
        return [
            min(1.0, max(0.01, value))
            for value in self._values(values, count, default)
        ]

    def _block_hours(self, slots):
        return sum(
            max(0.0, (
                self._local_datetime(item["till"])
                .timestamp()
                - self._local_datetime(item["from"]).timestamp()
            ) / 3600)
            for item in slots
        )

    def _weighted_average(self, slots):
        hours = self._block_hours(slots)
        if hours <= 0:
            return 0.0
        return sum(
            float(item["price"])
            * (
                self._local_datetime(item["till"]).timestamp()
                - self._local_datetime(item["from"]).timestamp()
            ) / 3600
            for item in slots
        ) / hours

    def _overlaps_any(self, slot, intervals):
        slot_start = self._local_datetime(slot["from"])
        slot_end = self._local_datetime(slot["till"])
        return any(
            self._local_datetime(start) < slot_end
            and self._local_datetime(end) > slot_start
            for start, end in intervals
        )

    def _local_datetime(self, value):
        if isinstance(value, datetime):
            moment = value
        else:
            moment = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.timezone)
        return moment.astimezone(self.timezone)
