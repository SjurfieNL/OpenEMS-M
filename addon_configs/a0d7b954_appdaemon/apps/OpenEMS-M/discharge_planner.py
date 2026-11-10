"""Eenvoudige, kostprijsgestuurde Export Planner."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from models import BatteryDischargePlan, DischargeBlock, DischargePlan
from ems_config import MAX_GRID_EXPORT_POWER_W


class DischargePlanner:
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
        max_discharge_values=None,
        discharge_efficiencies=None,
        resolution="Automatisch",
        minimum_block_minutes=30,
        inventory_cost_per_usable_kwh=None,
        export_efficiency=1.0,
        selection_margin=0.0,
        reserve_soc=None,
        maximum_export_power=1000.0,
        preferred_min_power=1000.0,
        preferred_max_power=1500.0,
        preferred_discharge_powers=None,
        battery_order=None,
        excluded_intervals=None,
        cost_basis_floor=None,
    ):
        """Plan de beste winstgevende terugleveruren.

        ``discharge_margin`` is het minimaal vereiste prijsverschil met de
        laadkostprijs. ``selection_margin`` is hoeveel de prijs onder de
        hoogste prijs van dezelfde kalenderdag mag liggen. Alleen direct
        aansluitende uren rond die dagpiek vormen het teruglevervenster.
        """
        names, soc_values, capacities = self._battery_inputs(
            names, soc_values, capacities, avg_soc, capacity
        )
        count = len(names)
        max_discharge_values = self._values(
            max_discharge_values, count, 2500.0
        )
        discharge_efficiencies = self._efficiencies(
            discharge_efficiencies, count, 1.0
        )
        inventory_cost = (
            max(0.0, float(inventory_cost_per_usable_kwh))
            if inventory_cost_per_usable_kwh is not None else None
        )
        export_efficiency = min(
            1.0, max(0.60, float(export_efficiency))
        )
        cost_basis = (
            inventory_cost / export_efficiency
            if inventory_cost is not None
            else float(average_charge_price)
        )
        if cost_basis_floor is not None:
            # Een dagreferentie (bijv. een laag gemeten of geplande laadprijs)
            # mag de kostprijs nooit onder wat de energie in de accu's echt
            # kostte drukken: anders lijkt verlieslatend terugleveren rendabel.
            cost_basis = max(cost_basis, float(cost_basis_floor))
        minimum_difference = max(0.0, float(discharge_margin))
        peak_margin = max(0.0, float(selection_margin))
        price_limit = cost_basis + minimum_difference
        reserve_soc = max(
            float(minimum_soc),
            float(minimum_soc if reserve_soc is None else reserve_soc),
        )
        # De waarde is het totale accuvermogen: netexport (max 5000 W) plus
        # het verwachte woningverbruik, begrensd door de accu's zelf.
        maximum_export_power = min(
            sum(max_discharge_values),
            max(0.0, float(maximum_export_power)),
        )
        preferred_min_power = max(0.0, float(preferred_min_power))
        preferred_max_power = max(
            preferred_min_power, float(preferred_max_power)
        )
        slots, resolution_minutes = self._resample(
            future_prices, resolution
        )
        excluded_intervals = excluded_intervals or []
        slots = [
            slot for slot in slots
            if not self._overlaps_any(slot, excluded_intervals)
        ]

        profitable_slots = [
            dict(slot) for slot in slots
            if float(slot["price"]) + 1e-9 >= price_limit
        ]
        eligible_slots, daily_peaks, daily_peak_limits = (
            self._daily_peak_window_slots(
                slots, profitable_slots, peak_margin
            )
        )
        qualifying = [dict(slot) for slot in eligible_slots]
        minimum_hours = max(0.0, float(minimum_block_minutes) / 60)
        available_battery = [
            cap * max(0.0, soc - reserve_soc) / 100
            for soc, cap in zip(soc_values, capacities)
        ]
        usable_battery = [
            energy * efficiency
            for energy, efficiency in zip(
                available_battery, discharge_efficiencies
            )
        ]
        total_usable = sum(usable_battery)
        total_maximum = sum(max_discharge_values)
        powers_per_battery = self._efficient_power_distribution(
            maximum_export_power,
            usable_battery,
            max_discharge_values,
            preferred_min_power,
            preferred_max_power,
            preferred_discharge_powers,
            battery_order,
        )
        planned_total_power = sum(powers_per_battery)
        power_ceiling = planned_total_power
        selected_slots = self._best_energy_slots(
            qualifying,
            total_usable,
            power_ceiling,
            minimum_hours,
        )
        selected_groups = self._contiguous_blocks(selected_slots)
        total_hours = sum(
            self._block_hours(group) for group in selected_groups
        )
        requested_energy = min(
            total_usable, planned_total_power / 1000 * total_hours
        )
        expected_by_battery = self._allocate(
            requested_energy,
            usable_battery,
            max_discharge_values,
            total_hours,
        )
        expected_energy = min(
            requested_energy, round(sum(expected_by_battery), 12)
        )
        planned_export_power = planned_total_power
        blocks = []
        for slot_group in selected_groups:
            hours = self._block_hours(slot_group)
            block_fraction = hours / total_hours if total_hours else 0.0
            block_energy = expected_energy * block_fraction
            allocations = [
                amount * block_fraction for amount in expected_by_battery
            ]
            average_price = self._weighted_average(slot_group)
            input_cost = block_energy * cost_basis
            avoided_cost = block_energy * average_price
            conversion_loss = sum(
                amount / max(0.01, discharge_efficiencies[index]) - amount
                for index, amount in enumerate(allocations)
            )
            net_profit = avoided_cost - input_cost
            blocks.append(DischargeBlock(
                start=self._local_datetime(slot_group[0]["from"]),
                end=self._local_datetime(slot_group[-1]["till"]),
                average_price=average_price,
                highest_price=max(
                    float(item["price"]) for item in slot_group
                ),
                slots=[self._public_slot(item) for item in slot_group],
                expected_energy=block_energy,
                energy_cost=input_cost,
                avoided_cost=avoided_cost,
                conversion_loss=conversion_loss,
                net_profit=net_profit,
                profitable=True,
                planned_total_power=planned_total_power,
                planned_export_power=planned_export_power,
                powers_per_battery=list(powers_per_battery),
                reserve_soc=reserve_soc,
                cost_basis_per_kwh=cost_basis,
            ))

        per_battery = []
        for index, name in enumerate(names):
            expected = expected_by_battery[index]
            battery_draw = expected / max(
                0.01, discharge_efficiencies[index]
            )
            predicted_end = max(
                float(minimum_soc),
                soc_values[index]
                - (battery_draw / capacities[index] * 100
                   if capacities[index] > 0 else 0.0),
            )
            per_battery.append(BatteryDischargePlan(
                index=index,
                name=name,
                current_soc=soc_values[index],
                minimum_soc=reserve_soc,
                predicted_end_soc=predicted_end,
                capacity=capacities[index],
                available_energy=available_battery[index],
                usable_energy=usable_battery[index],
                expected_energy=expected,
                expected_power=powers_per_battery[index],
                duration_hours=(
                    expected / (powers_per_battery[index] / 1000)
                    if powers_per_battery[index] > 0 else 0.0
                ),
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
            cost_basis_per_kwh=cost_basis,
            cost_basis_source=(
                "werkelijke laadprijs met gekozen exportrendement"
                if inventory_cost is not None
                else "economische laadreferentie"
            ),
            reserve_soc=reserve_soc,
            minimum_price_difference=minimum_difference,
            selection_margin=peak_margin,
            daily_peak_prices=daily_peaks,
            daily_peak_limits=daily_peak_limits,
            planned_total_power=planned_total_power,
            planned_export_power=planned_export_power,
        )

    def _daily_peak_window_slots(
        self, slots, profitable_slots, peak_margin
    ):
        """Kies per dag het aaneengesloten venster rond het duurste uur."""
        profitable_keys = {
            self._slot_key(item) for item in profitable_slots
        }
        grouped = {}
        for item in sorted(slots, key=self._slot_key):
            day = str(self._local_datetime(item["from"]).date())
            grouped.setdefault(day, []).append(item)
        selected = []
        daily_peaks = {}
        daily_limits = {}
        for day in sorted(grouped):
            day_slots = grouped[day]
            peak_index = max(
                range(len(day_slots)),
                key=lambda index: (
                    float(day_slots[index]["price"]),
                    -self._local_datetime(
                        day_slots[index]["from"]
                    ).timestamp(),
                ),
            )
            peak_price = float(day_slots[peak_index]["price"])
            peak_limit = peak_price - float(peak_margin)
            daily_peaks[day] = peak_price
            daily_limits[day] = peak_limit

            def allowed(index):
                item = day_slots[index]
                return (
                    self._slot_key(item) in profitable_keys
                    and float(item["price"]) + 1e-9 >= peak_limit
                )

            if not allowed(peak_index):
                continue
            left = peak_index
            while (
                left > 0
                and self._slots_touch(
                    day_slots[left - 1], day_slots[left]
                )
                and allowed(left - 1)
            ):
                left -= 1
            right = peak_index
            while (
                right + 1 < len(day_slots)
                and self._slots_touch(
                    day_slots[right], day_slots[right + 1]
                )
                and allowed(right + 1)
            ):
                right += 1
            selected.extend(
                dict(item) for item in day_slots[left:right + 1]
            )
        return selected, daily_peaks, daily_limits

    def _slots_touch(self, left, right):
        return (
            self._local_datetime(left["till"]).timestamp()
            == self._local_datetime(right["from"]).timestamp()
        )

    def with_dynamic_reserve_before_charge(
        self,
        base_plan,
        future_prices,
        now,
        next_charge_start,
        forecast,
        *,
        forecast_margin=1.15,
        dynamic_reserve_kwh=2.0,
        next_charge_reference_price=None,
        **kwargs,
    ):
        """Promoveer alleen vrije donkere prijsvakken vlak vóór laden.

        De gewone planning met vaste reserve blijft leidend. Deze tweede laag
        projecteert woningverbruik tot een betrouwbaar laadmoment en verlaagt
        de reserve alleen voor nog niet geselecteerde, economisch toegestane
        intervallen. Ontbrekende kwartierdata laat de basisplanning ongemoeid.

        ``next_charge_reference_price`` is de eerstvolgende écht geplande
        laadprijs (al gedeeld door het laadrendement door de aanroeper).
        Deze vrijgave-laag verkoopt alleen als dat voordeliger is dan die
        prijs — niet de kostprijsbasis van vandaag. Zolang die prijs niet
        bekend is (bv. omdat de prijzen van morgen nog niet gepubliceerd
        zijn en er dus nog geen laadmoment gepland kon worden) wordt hier
        helemaal niets vrijgegeven; de gewone planning met vaste reserve
        blijft dan leidend.
        """
        if next_charge_reference_price is None:
            return base_plan
        if (
            base_plan is None
            or next_charge_start is None
            or next_charge_start <= now
        ):
            return base_plan
        home_use_intervals = kwargs.get("home_use_intervals")
        points = self._forecast_points(
            forecast,
            now,
            next_charge_start,
            home_use_intervals,
        )
        if points is None:
            return base_plan
        capacities = list(map(float, kwargs.get("capacities") or []))
        soc_values = list(map(float, kwargs.get("soc_values") or []))
        if not capacities or len(capacities) != len(soc_values):
            return base_plan
        total_capacity = sum(max(0.0, value) for value in capacities)
        if total_capacity <= 0:
            return base_plan
        efficiencies = self._efficiencies(
            kwargs.get("discharge_efficiencies"),
            len(capacities),
            1.0,
        )
        average_efficiency = max(
            0.60, sum(efficiencies) / len(efficiencies)
        )
        fixed_reserve = max(
            float(kwargs.get("minimum_soc", 0.0)),
            float(kwargs.get("reserve_soc", 0.0)),
        )
        absolute_minimum = float(kwargs.get("minimum_soc", 0.0))
        selected_intervals = [
            (block.start, block.end) for block in base_plan.blocks
        ]
        candidates = [
            dict(slot) for slot in base_plan.eligible_slots
            if (
                now < self._local_datetime(slot["till"])
                <= next_charge_start
                and not self._overlaps_any(slot, selected_intervals)
            )
        ]
        if not candidates:
            return base_plan

        best_plan = None
        best_profit = 0.0
        for release_at in sorted({
            self._local_datetime(slot["from"]) for slot in candidates
        }):
            if release_at < now:
                release_at = now
            home_before = (
                self._forecast_energy(
                    points, now, release_at, home_use_intervals
                )
                * float(forecast_margin)
            )
            home_after = (
                self._forecast_energy(
                    points,
                    release_at,
                    next_charge_start,
                    home_use_intervals,
                )
                * float(forecast_margin)
            )
            # De gebruiker kiest hoeveel fysieke energie bij het begin van
            # het laadvenster moet resteren. De minimum-SOC blijft altijd de
            # harde ondergrens; verwacht woningverbruik tot dat venster komt
            # boven op die gewenste eindreserve.
            arrival_reserve_soc = max(
                absolute_minimum,
                max(0.0, float(dynamic_reserve_kwh))
                / total_capacity * 100,
            )
            required_battery_kwh = home_after / average_efficiency
            dynamic_reserve = min(
                fixed_reserve,
                max(
                    absolute_minimum,
                    arrival_reserve_soc
                    + required_battery_kwh / total_capacity * 100,
                ),
            )
            if dynamic_reserve >= fixed_reserve - 0.1:
                continue

            prior_export = sum(
                float(getattr(block, "expected_energy", 0.0))
                for block in base_plan.blocks
                if block.end <= release_at
                and block.end <= next_charge_start
            )
            projected_draw = (
                home_before + prior_export
            ) / average_efficiency
            loss_pct = projected_draw / total_capacity * 100
            projected_soc = [
                max(absolute_minimum, value - loss_pct)
                for value in soc_values
            ]
            release_prices = [
                slot for slot in candidates
                if self._local_datetime(slot["from"]) >= release_at
            ]
            if not release_prices:
                continue
            dynamic_kwargs = dict(kwargs)
            dynamic_kwargs.update({
                "soc_values": projected_soc,
                "avg_soc": sum(projected_soc) / len(projected_soc),
                "reserve_soc": dynamic_reserve,
                "excluded_intervals": selected_intervals,
                # Vrijgegeven "overtollige" energie wordt vergeleken met de
                # eerstvolgende geplande laadprijs, niet met de kostprijsbasis
                # van vandaag (die geldt voor de reeds aanwezige voorraad).
                "average_charge_price": next_charge_reference_price,
                "inventory_cost_per_usable_kwh": None,
            })
            dynamic_kwargs.pop("average_charge_prices_by_day", None)
            plan = self.calculate_with_energy_resets(
                future_prices=release_prices,
                energy_resets=[],
                **dynamic_kwargs,
            )
            profit = sum(block.net_profit for block in plan.blocks)
            if plan.blocks and profit > best_profit + 1e-9:
                best_plan = plan
                best_profit = profit

        if best_plan is None:
            return base_plan
        blocks = sorted(
            [*base_plan.blocks, *best_plan.blocks],
            key=lambda block: block.start,
        )
        return replace(
            base_plan,
            blocks=blocks,
            planned_total_power=max(
                base_plan.planned_total_power,
                best_plan.planned_total_power,
            ),
            planned_export_power=max(
                base_plan.planned_export_power,
                best_plan.planned_export_power,
            ),
        )

    def _forecast_points(self, forecast, start, end, intervals=None):
        active_intervals = self._clipped_intervals(intervals, start, end)
        if active_intervals == []:
            return []
        points = []
        for item in forecast or []:
            moment = self._local_datetime(item.get("start"))
            if not self._in_intervals(moment, active_intervals):
                continue
            if not start <= moment < end:
                continue
            power = item.get("vermogen_w")
            samples = int(item.get("samples", 0) or 0)
            if power is None or samples <= 0:
                return None
            points.append((moment, max(0.0, float(power))))
        expected = max(
            1,
            int(
                (
                    end.timestamp() - start.timestamp()
                    if active_intervals is None
                    else sum(
                        interval_end.timestamp()
                        - interval_start.timestamp()
                        for interval_start, interval_end in active_intervals
                    )
                ) / 900
            ),
        )
        return points if len(points) >= expected else None

    def calculate_with_energy_resets(
        self,
        future_prices,
        energy_resets,
        **kwargs,
    ):
        """Plan export per energieperiode tussen geplande laadmomenten.

        Een voltooide laadplanning mag alleen exporturen ná dat laadmoment
        van extra energie voorzien. Door iedere periode apart te plannen kan
        een toekomstige lading nooit een eerder exportuur financieren, terwijl
        een nieuwe lading op de volgende dag wel opnieuw energie beschikbaar
        maakt boven de ingestelde SOC-reserve.
        """
        prices = sorted(
            (dict(item) for item in future_prices),
            key=lambda item: self._local_datetime(item["from"]).timestamp(),
        )
        daily_charge_prices = {
            str(day): float(value)
            for day, value in (
                kwargs.pop("average_charge_prices_by_day", {}) or {}
            ).items()
        }
        home_forecast = list(kwargs.pop("home_forecast", []) or [])
        fallback_home_power = kwargs.pop("fallback_home_power", None)
        home_use_intervals = kwargs.pop("home_use_intervals", None)
        current_soc = list(map(float, kwargs.get("soc_values") or []))
        resets = sorted(
            (
                (
                    self._local_datetime(item["at"]),
                    list(map(float, item["soc_values"])),
                    item.get("average_charge_price"),
                    item.get("reserve_soc_after"),
                )
                for item in energy_resets or []
                if item.get("at") is not None
            ),
            key=lambda item: item[0].timestamp(),
        )
        plans = []
        period_start = None
        period_kwargs = dict(kwargs)
        for (
            reset_at, reset_soc, reset_charge_price, reset_reserve_soc
        ) in resets:
            period_prices = [
                item for item in prices
                if (
                    (period_start is None or self._local_datetime(
                        item["from"]
                    ) >= period_start)
                    and self._local_datetime(item["till"]) <= reset_at
                )
            ]
            if period_prices:
                current_soc = self._calculate_daily_periods(
                    period_prices,
                    current_soc,
                    period_kwargs,
                    plans,
                    daily_charge_prices,
                    home_forecast,
                    fallback_home_power,
                    home_use_intervals,
                )
            current_soc = list(reset_soc)
            if reset_charge_price is not None:
                # Energie na dit laadmoment hoort bij de laadprijs van dit
                # laadmoment. Een laadprijs van morgen mag nooit de
                # terugleverdrempel van vandaag bepalen.
                period_kwargs["average_charge_price"] = float(
                    reset_charge_price
                )
                period_kwargs["inventory_cost_per_usable_kwh"] = None
            if reset_reserve_soc is not None:
                # Vanaf de voltooide lading geldt Reserve na laden. Hiermee
                # kan dezelfde planning vóór en na het laadblok verschillende
                # vaste grenzen veilig toepassen.
                period_kwargs["reserve_soc"] = float(reset_reserve_soc)
            period_start = reset_at
        final_prices = [
            item for item in prices
            if (
                period_start is None
                or self._local_datetime(item["from"]) >= period_start
            )
        ]
        if final_prices:
            current_soc = self._calculate_daily_periods(
                final_prices,
                current_soc,
                period_kwargs,
                plans,
                daily_charge_prices,
                home_forecast,
                fallback_home_power,
                home_use_intervals,
            )
        if not plans:
            return self.calculate(
                future_prices=prices,
                **kwargs,
            )
        return self._merge_period_plans(plans)

    @staticmethod
    def _keep_inventory_floor(period_kwargs):
        """Bewaar de voorraadkostprijs als ondergrens voor de dagreferentie."""
        inventory = period_kwargs.get("inventory_cost_per_usable_kwh")
        if inventory is None:
            return
        efficiency = min(
            1.0, max(0.60, float(period_kwargs.get("export_efficiency", 1.0)))
        )
        period_kwargs["cost_basis_floor"] = (
            max(0.0, float(inventory)) / efficiency
        )

    def _calculate_daily_periods(
        self,
        prices,
        current_soc,
        kwargs,
        plans,
        daily_charge_prices=None,
        home_forecast=None,
        fallback_home_power=None,
        home_use_intervals=None,
    ):
        """Plan iedere kalenderdag apart en draag alleen echte energie over."""
        grouped = {}
        for item in prices:
            day = self._local_datetime(item["from"]).date()
            grouped.setdefault(day, []).append(item)
        projected_soc = list(current_soc)
        for day in sorted(grouped):
            period_kwargs = dict(kwargs)
            day_start = min(
                self._local_datetime(item["from"])
                for item in grouped[day]
            )
            day_end = max(
                self._local_datetime(item["till"])
                for item in grouped[day]
            )
            projected_soc = self._soc_after_forecast_home_use(
                projected_soc,
                day_start,
                day_end,
                home_forecast or [],
                fallback_home_power,
                home_use_intervals,
                period_kwargs.get("capacities", []),
                period_kwargs.get("discharge_efficiencies", []),
                period_kwargs.get("minimum_soc", 0.0),
            )
            daily_reference = (daily_charge_prices or {}).get(str(day))
            if daily_reference is not None:
                # Iedere kalenderdag krijgt zijn eigen laadkostreferentie.
                # Alleen de berekende SOC wordt over middernacht meegenomen.
                period_kwargs["average_charge_price"] = float(
                    daily_reference
                )
                if day == min(grouped):
                    # Alleen de energie die er nu al in zit (vandaag) kost
                    # minstens de voorraadprijs; latere dagen volgen hun
                    # eigen geplande lading.
                    self._keep_inventory_floor(period_kwargs)
                period_kwargs["inventory_cost_per_usable_kwh"] = None
            period_kwargs["soc_values"] = list(projected_soc)
            period_kwargs["avg_soc"] = (
                sum(projected_soc) / len(projected_soc)
                if projected_soc else kwargs.get("avg_soc")
            )
            plan = self.calculate(
                future_prices=grouped[day],
                **period_kwargs,
            )
            if daily_reference is not None:
                plan = replace(
                    plan,
                    cost_basis_source=(
                        "gemiddelde all-in prijs van de geplande of "
                        "uitgevoerde laaduren van deze dag"
                    ),
                )
            plans.append(plan)
            if plan.per_battery:
                projected_soc = [
                    item.predicted_end_soc for item in plan.per_battery
                ]
        return projected_soc

    def _soc_after_forecast_home_use(
        self,
        soc_values,
        start,
        end,
        forecast,
        fallback_home_power,
        home_use_intervals,
        capacities,
        discharge_efficiencies,
        minimum_soc,
    ):
        if end <= start or not soc_values:
            return list(soc_values)
        active_intervals = self._clipped_intervals(
            home_use_intervals, start, end
        )
        if active_intervals == []:
            return list(soc_values)
        hours = (
            (end.timestamp() - start.timestamp()) / 3600
            if active_intervals is None
            else sum(
                (interval_end.timestamp() - interval_start.timestamp())
                / 3600
                for interval_start, interval_end in active_intervals
            )
        )
        quarters = max(0, int(round(hours * 4)))
        values = []
        for item in forecast:
            moment = self._local_datetime(item.get("start"))
            power = item.get("vermogen_w")
            if (
                start <= moment < end
                and self._in_intervals(moment, active_intervals)
                and power is not None
            ):
                values.append(max(0.0, float(power)))
        fallback = max(0.0, float(fallback_home_power or 0.0))
        home_energy = (
            sum(values) + max(0, quarters - len(values)) * fallback
        ) * 0.25 / 1000
        total_capacity = sum(max(0.0, float(value)) for value in capacities)
        if total_capacity <= 0:
            return list(soc_values)
        efficiencies = [
            max(0.60, float(value)) for value in discharge_efficiencies
        ]
        efficiency = (
            sum(efficiencies) / len(efficiencies)
            if efficiencies else 1.0
        )
        draw_soc = home_energy / efficiency / total_capacity * 100
        return [
            max(float(minimum_soc), float(soc) - draw_soc)
            for soc in soc_values
        ]

    def _forecast_energy(self, points, start, end, intervals=None):
        active_intervals = self._clipped_intervals(intervals, start, end)
        if active_intervals == []:
            return 0.0
        return sum(
            power * 0.25 / 1000
            for moment, power in points
            if (
                start <= moment < end
                and self._in_intervals(moment, active_intervals)
            )
        )

    def _clipped_intervals(self, intervals, start, end):
        if intervals is None:
            return None
        clipped = []
        for raw_start, raw_end in intervals:
            interval_start = max(start, self._local_datetime(raw_start))
            interval_end = min(end, self._local_datetime(raw_end))
            if interval_start < interval_end:
                clipped.append((interval_start, interval_end))
        return clipped

    @staticmethod
    def _in_intervals(moment, intervals):
        return intervals is None or any(
            start <= moment < end for start, end in intervals
        )

    def _merge_period_plans(self, plans):
        """Voeg chronologisch veilige deelplannen samen tot één exportplan."""
        template = plans[0]
        count = max((len(plan.per_battery) for plan in plans), default=0)
        per_battery = []
        for index in range(count):
            items = [
                plan.per_battery[index]
                for plan in plans
                if index < len(plan.per_battery)
            ]
            first = items[0]
            last = items[-1]
            per_battery.append(BatteryDischargePlan(
                index=first.index,
                name=first.name,
                current_soc=first.current_soc,
                minimum_soc=first.minimum_soc,
                predicted_end_soc=last.predicted_end_soc,
                capacity=first.capacity,
                available_energy=sum(
                    item.available_energy for item in items
                ),
                usable_energy=sum(item.usable_energy for item in items),
                expected_energy=sum(
                    item.expected_energy for item in items
                ),
                expected_power=max(
                    (item.expected_power for item in items), default=0.0
                ),
                duration_hours=sum(
                    item.duration_hours for item in items
                ),
                discharge_efficiency=first.discharge_efficiency,
            ))
        return DischargePlan(
            price_limit=template.price_limit,
            available_energy=sum(
                plan.available_energy for plan in plans
            ),
            blocks=sorted(
                (
                    block
                    for plan in plans
                    for block in plan.blocks
                ),
                key=lambda block: block.start,
            ),
            eligible_slots=sorted(
                (
                    slot
                    for plan in plans
                    for slot in plan.eligible_slots
                ),
                key=lambda slot: self._local_datetime(slot["from"]),
            ),
            per_battery=per_battery,
            resolution_minutes=template.resolution_minutes,
            cost_basis_per_kwh=template.cost_basis_per_kwh,
            cost_basis_source=template.cost_basis_source,
            reserve_soc=template.reserve_soc,
            minimum_price_difference=template.minimum_price_difference,
            selection_margin=template.selection_margin,
            daily_peak_prices={
                day: price
                for plan in plans
                for day, price in plan.daily_peak_prices.items()
            },
            daily_peak_limits={
                day: price
                for plan in plans
                for day, price in plan.daily_peak_limits.items()
            },
            planned_total_power=max(
                (plan.planned_total_power for plan in plans), default=0.0
            ),
            planned_export_power=max(
                (plan.planned_export_power for plan in plans), default=0.0
            ),
        )

    def _best_energy_slots(
        self, slots, available_energy, power_ceiling, minimum_hours
    ):
        if not slots or available_energy <= 0 or power_ceiling <= 0:
            return []
        ranked = sorted(
            slots,
            key=lambda item: (
                -float(item["price"]),
                self._local_datetime(item["from"]).timestamp(),
            ),
        )
        selected = []
        capacity = 0.0
        required = max(0.0, float(available_energy))
        for slot in ranked:
            selected.append(slot)
            capacity += power_ceiling / 1000 * self._block_hours([slot])
            if capacity + 1e-9 >= required:
                break
        selected = sorted(selected, key=self._slot_key)
        if minimum_hours <= 0:
            return selected
        valid_keys = set()
        for group in self._contiguous_blocks(selected):
            if self._block_hours(group) + 1e-9 >= minimum_hours:
                valid_keys.update(self._slot_key(item) for item in group)
        return [
            item for item in selected if self._slot_key(item) in valid_keys
        ]

    def _power_distribution(
        self, total_power, energy, maximums, preferred_min, preferred_max
    ):
        count = len(maximums)
        result = [0.0] * count
        remaining = max(0.0, float(total_power))
        active = [
            index for index in range(count)
            if maximums[index] > 0 and energy[index] > 0.001
        ]
        if not active:
            return result
        weights = sum(energy[index] for index in active)
        for index in active:
            share = (
                remaining / len(active)
                if weights <= 0
                else remaining * energy[index] / weights
            )
            result[index] = min(
                float(maximums[index]),
                max(0.0, share),
            )
        # Gebruik het efficiënte minimum als daar genoeg totaalvermogen voor
        # is; kleine restenergie mag bewust lager uitkomen.
        if remaining >= preferred_min * len(active):
            for index in active:
                result[index] = max(
                    min(preferred_min, float(maximums[index])),
                    result[index],
                )
        result = [
            min(value, preferred_max, float(maximums[index]))
            for index, value in enumerate(result)
        ]
        scale = (
            min(1.0, remaining / sum(result))
            if sum(result) > 0 else 0.0
        )
        return [round(value * scale, 1) for value in result]

    @staticmethod
    def _efficient_power_distribution(
        requested,
        energy,
        maximums,
        preferred_min,
        preferred_max,
        preferred_powers=None,
        battery_order=None,
        tolerance=0.10,
    ):
        """Kies eenmaal genoeg accu's en blijf binnen ±10% van hun optimum."""
        count = len(maximums)
        result = [0.0] * count
        preferences = list(preferred_powers or [])
        fallback = (float(preferred_min) + float(preferred_max)) / 2
        order = [
            int(index) for index in (battery_order or range(count))
            if 0 <= int(index) < count
            and float(maximums[int(index)]) > 0
            and float(energy[int(index)]) > 0.001
        ]
        if not order:
            return result

        optimum = {}
        lower = {}
        upper = {}
        for index in order:
            raw = preferences[index] if index < len(preferences) else None
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = fallback
            value = min(float(maximums[index]), max(0.0, value))
            optimum[index] = value
            lower[index] = min(value, value * (1.0 - tolerance))
            upper[index] = min(float(maximums[index]), value * (1.0 + tolerance))

        target = max(0.0, float(requested))
        prefixes = []
        selected = []
        for index in order:
            selected.append(index)
            floor = sum(lower[item] for item in selected)
            ceiling = sum(upper[item] for item in selected)
            if floor <= target + 0.5:
                feasible = min(ceiling, target)
                prefixes.append((target - feasible, len(selected), list(selected)))
        selected = min(prefixes)[2] if prefixes else [order[0]]

        minimum_total = sum(lower[index] for index in selected)
        optimum_total = sum(optimum[index] for index in selected)
        maximum_total = sum(upper[index] for index in selected)
        assigned_total = min(maximum_total, target)
        if assigned_total < minimum_total:
            result[selected[0]] = assigned_total
            return [round(value, 1) for value in result]
        base_total = optimum_total
        for index in selected:
            result[index] = optimum[index]

        difference = assigned_total - base_total
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

    def _overlaps_any(self, slot, intervals):
        start = self._local_datetime(slot["from"])
        end = self._local_datetime(slot["till"])
        for raw_start, raw_end in intervals:
            other_start = self._local_datetime(raw_start)
            other_end = self._local_datetime(raw_end)
            if other_start < end and other_end > start:
                return True
        return False

    def _slot_key(self, item):
        return (
            self._local_datetime(item["from"]).timestamp(),
            self._local_datetime(item["till"]).timestamp(),
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
            if current and (
                self._local_datetime(current[-1]["till"]).timestamp()
                != self._local_datetime(item["from"]).timestamp()
            ):
                blocks.append(current)
                current = []
            current.append(item)
        if current:
            blocks.append(current)
        return blocks

    def _allocate(self, energy, remaining, max_powers, hours):
        allocations = [0.0] * len(remaining)
        left = energy
        active = {
            index for index, value in enumerate(remaining) if value > 0
        }
        while active and left > 1e-9:
            weight = sum(remaining[index] for index in active)
            progressed = 0.0
            for index in list(active):
                share = left * remaining[index] / weight if weight else 0.0
                cap = min(
                    remaining[index],
                    max(0.0, max_powers[index]) / 1000 * hours,
                )
                amount = min(share, cap - allocations[index])
                if amount > 0:
                    allocations[index] += amount
                    progressed += amount
                if allocations[index] >= cap - 1e-9:
                    active.discard(index)
            left -= progressed
            if progressed <= 1e-9:
                break
        return allocations

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
