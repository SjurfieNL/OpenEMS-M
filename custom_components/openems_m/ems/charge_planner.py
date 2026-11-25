# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Economische laadplanner."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import BatteryChargePlan, BatteryChargeSegment, ChargePlan


class ChargePlanner:
    def __init__(
        self,
        timezone,
        max_preferred_hours=6,
        completion_buffer_minutes=15,
    ):
        self.timezone = ZoneInfo(timezone)
        self.max_preferred_hours = max_preferred_hours
        self.completion_buffer_hours = max(
            0.0, float(completion_buffer_minutes) / 60
        )

    def calculate(
        self,
        prices,
        needed_energy,
        max_charge,
        price_margin,
        allowed_start_hour=0,
        allowed_end_hour=24,
    ):
        if needed_energy <= 0 or max_charge <= 0 or not prices:
            return None

        prices = sorted(prices, key=lambda item: self._local_datetime(item["from"]))
        prices = [
            item for item in prices
            if self._inside_allowed_window(
                item, allowed_start_hour, allowed_end_hour
            )
        ]
        if not prices:
            return None

        cheapest_index = min(
            range(len(prices)),
            key=lambda index: float(prices[index]["price"]),
        )
        cheapest_price = float(prices[cheapest_index]["price"])
        price_limit = cheapest_price + price_margin

        selected_indices = [cheapest_index]
        left_index = cheapest_index - 1
        right_index = cheapest_index + 1

        while (
            self._slots_duration(
                [prices[index] for index in selected_indices]
            ) < self.max_preferred_hours
        ):
            left_allowed = (
                left_index >= 0
                and float(prices[left_index]["price"]) <= price_limit
                and self._contiguous(prices[left_index], prices[left_index + 1])
            )
            right_allowed = (
                right_index < len(prices)
                and float(prices[right_index]["price"]) <= price_limit
                and self._contiguous(prices[right_index - 1], prices[right_index])
            )
            if not left_allowed and not right_allowed:
                break
            if left_allowed and right_allowed:
                if float(prices[left_index]["price"]) <= float(
                    prices[right_index]["price"]
                ):
                    selected_indices.append(left_index)
                    left_index -= 1
                else:
                    selected_indices.append(right_index)
                    right_index += 1
            elif left_allowed:
                selected_indices.append(left_index)
                left_index -= 1
            else:
                selected_indices.append(right_index)
                right_index += 1

        selected_indices.sort()
        block = [prices[index] for index in selected_indices]
        base_minimum_hours = max(
            1 / 60,
            needed_energy * 1000 / max_charge,
        )
        minimum_hours = max(
            1 / 60,
            base_minimum_hours + self.completion_buffer_hours,
        )

        if self._slots_duration(block) + 1e-9 < minimum_hours:
            possible_blocks = self._possible_blocks(prices, minimum_hours)
            # Als de toegestane periode te kort is voor de volledige buffer,
            # blijft laden volgens de technisch noodzakelijke minimumduur beter
            # dan helemaal geen laadplan maken.
            if not possible_blocks and minimum_hours > base_minimum_hours:
                possible_blocks = self._possible_blocks(
                    prices, base_minimum_hours
                )
            if not possible_blocks:
                return None
            _, block = min(possible_blocks, key=lambda item: item[0])

        hours = self._slots_duration(block)
        average_price = sum(
            float(item["price"]) * self._slot_duration(item)
            for item in block
        ) / hours
        # Bereken alsof het laadblok eerder eindigt. De echte eindtijd blijft
        # behouden, zodat afronding, meetvertraging en laad-taper nabij 100%
        # niet zorgen dat de doel-SOC pas op of na de blokgrens wordt gehaald.
        effective_charge_hours = max(
            1 / 60,
            hours - self.completion_buffer_hours,
        )
        charge_power = min(
            needed_energy / effective_charge_hours * 1000,
            max_charge,
        )
        now_local = datetime.now(self.timezone)
        block_start = self._local_datetime(block[0]["from"])
        block_end = self._local_datetime(block[-1]["till"])

        return ChargePlan(
            start=block_start,
            end=block_end,
            power=charge_power,
            hours=float(hours),
            average_price=average_price,
            cheapest_price=cheapest_price,
            price_limit=price_limit,
            slots=block,
            block_is_active=block_start <= now_local < block_end,
            effective_charge_hours=effective_charge_hours,
        )

    def calculate_per_battery(
        self,
        prices,
        names,
        soc_values,
        capacities,
        max_charge_values,
        target_soc,
        charge_efficiency,
        minimum_power,
        price_margin,
        charge_efficiencies=None,
        preferred_min_power=1000,
        preferred_max_power=1500,
        allowed_start_hour=0,
        allowed_end_hour=24,
    ):
        """Plant elke accu alleen in de goedkoopste benodigde deeluren."""
        minimum_power = max(0.0, float(minimum_power))
        preferred_min_power = max(
            minimum_power, float(preferred_min_power)
        )
        preferred_max_power = max(
            preferred_min_power, float(preferred_max_power)
        )
        efficiencies = self._normalized_efficiencies(
            len(names), charge_efficiency, charge_efficiencies
        )
        battery_needs, grid_needs = self._energy_requirements(
            soc_values, capacities, target_soc, efficiencies
        )
        total_needed = sum(grid_needs)
        if total_needed <= 0:
            return None, [
                self._empty_battery_plan(
                    index, name, soc_values[index], target_soc,
                    capacities[index], minimum_power, efficiencies[index],
                    preferred_min_power, preferred_max_power,
                )
                for index, name in enumerate(names)
            ]

        required_hours = max(
            (
                energy / (max_power / 1000)
                for energy, max_power in zip(
                    grid_needs, max_charge_values
                )
                if energy > 0 and max_power > 0
            ),
            default=0.0,
        )
        if required_hours <= 0:
            return None, []
        feasible_total_power = total_needed / required_hours * 1000
        candidate = self.calculate(
            prices=prices,
            needed_energy=total_needed,
            max_charge=feasible_total_power,
            price_margin=price_margin,
            allowed_start_hour=allowed_start_hour,
            allowed_end_hour=allowed_end_hour,
        )
        if candidate is None:
            # Als de doel-SOC niet volledig binnen de resterende goedkope
            # uren past, benut dan het groene venster maximaal. Een lege
            # accu helemaal niet laden is slechter dan een gedeeltelijke
            # economische lading.
            usable_hours = self._maximum_usable_contiguous_hours(
                prices, allowed_start_hour, allowed_end_hour
            )
            partial_energy = min(
                total_needed,
                feasible_total_power / 1000 * usable_hours,
            )
            if partial_energy <= 0:
                return None, []
            candidate = self.calculate(
                prices=prices,
                needed_energy=partial_energy,
                max_charge=feasible_total_power,
                price_margin=price_margin,
                allowed_start_hour=allowed_start_hour,
                allowed_end_hour=allowed_end_hour,
            )
            if candidate is None:
                return None, []
            grid_needs = [
                min(
                    energy,
                    max(0.0, float(max_charge_values[index]))
                    / 1000
                    * candidate.effective_charge_hours,
                )
                for index, energy in enumerate(grid_needs)
            ]
            battery_needs = [
                energy * efficiencies[index]
                for index, energy in enumerate(grid_needs)
            ]

        battery_plans = []
        for index, name in enumerate(names):
            energy = grid_needs[index]
            battery_energy = battery_needs[index]
            if energy <= 0:
                battery_plans.append(self._empty_battery_plan(
                    index, name, soc_values[index], target_soc,
                    capacities[index], minimum_power, efficiencies[index],
                    preferred_min_power, preferred_max_power,
                ))
                continue
            battery_plans.append(self._battery_plan(
                index=index,
                name=name,
                current_soc=soc_values[index],
                target_soc=target_soc,
                capacity=capacities[index],
                battery_energy_needed=battery_energy,
                grid_energy_needed=energy,
                max_power=max_charge_values[index],
                minimum_power=minimum_power,
                preferred_min_power=preferred_min_power,
                preferred_max_power=preferred_max_power,
                charge_efficiency=efficiencies[index],
                slots=candidate.slots,
            ))

        aggregate = self._aggregate_plan(candidate, battery_plans)
        return aggregate, battery_plans

    def calculate_per_battery_earliest_day(self, prices, **kwargs):
        """Kiest de vroegste dag waarop de volledige lading haalbaar is."""
        forecasts = self.calculate_per_battery_each_day(prices, **kwargs)
        return forecasts[0] if forecasts else (None, [])

    def calculate_per_battery_each_day(self, prices, **kwargs):
        """Maakt per zichtbare dag een voorlopige individuele laadplanning."""
        home_forecast = list(kwargs.pop("home_forecast", []) or [])
        fallback_home_power = kwargs.pop("fallback_home_power", None)
        projected_export_blocks = list(
            kwargs.pop("projected_export_blocks", []) or []
        )
        discharge_efficiencies = list(
            kwargs.pop("discharge_efficiencies", []) or []
        )
        projection_start = kwargs.pop("projection_start", None)
        projection_minimum_soc = float(
            kwargs.pop("projection_minimum_soc", 0.0)
        )
        smart_enabled = bool(kwargs.pop("smart_enabled", False))
        smart_reserve_soc = float(kwargs.pop("smart_reserve_soc", 15.0))
        smart_buffer = float(kwargs.pop("smart_buffer", 0.20))
        smart_min_difference = float(
            kwargs.pop("smart_min_price_difference", 0.02)
        )
        forecast_prices = list(kwargs.pop("forecast_prices", []) or [])
        forecast_min_difference = float(
            kwargs.pop("forecast_min_difference", 0.05)
        )
        forecast_floor_soc = float(kwargs.pop("forecast_floor_soc", 30.0))
        forecast_days = int(kwargs.pop("forecast_days", 2))
        forecast_extra_margin = float(
            kwargs.pop("forecast_extra_margin", 0.0)
        )
        forecast_real_days = kwargs.pop("forecast_real_days", None)
        self.last_smart_notes = []
        self.forecast_postponed = False
        # True zodra de planner bewust niet (meer) laadt vandaag: een eerder
        # gestart plan mag dat besluit dan niet overrulen.
        self.charge_deferred = False
        self.forecast_day_plans = []
        grouped = {}
        for item in sorted(
            prices, key=lambda value: self._local_datetime(value["from"])
        ):
            day = self._local_datetime(item["from"]).date()
            grouped.setdefault(day, []).append(item)
        forecasts = []
        projected_soc = list(map(float, kwargs.get("soc_values") or []))
        cursor_value = projection_start or (
            min(
                prices, key=lambda item: self._local_datetime(item["from"])
            )["from"] if prices else None
        )
        if isinstance(cursor_value, datetime):
            cursor = (
                cursor_value.replace(tzinfo=self.timezone)
                if cursor_value.tzinfo is None else
                cursor_value.astimezone(self.timezone)
            )
        else:
            cursor = (
                self._local_datetime(cursor_value)
                if cursor_value is not None else None
            )
        ordered_days = sorted(grouped)
        forecast_groups = {}
        if forecast_prices and ordered_days:
            for item in forecast_prices:
                forecast_day = self._local_datetime(item["from"]).date()
                if (
                    forecast_day > ordered_days[-1]
                    and (forecast_day - ordered_days[0]).days <= forecast_days
                ):
                    forecast_groups.setdefault(forecast_day, []).append(item)
        for position, day in enumerate(ordered_days):
            day_kwargs = dict(kwargs)
            day_kwargs["soc_values"] = list(projected_soc)
            try:
                preliminary, _ = self.calculate_per_battery(
                    prices=grouped[day], **day_kwargs
                )
            except ValueError as error:
                if "maximaal laadvermogen is 0 W" in str(error):
                    raise
                continue
            day_end = max(
                self._local_datetime(item["till"])
                for item in grouped[day]
            )
            if (
                position == 0
                and preliminary is None
                and self._all_at_target(day_kwargs)
            ):
                # De accu's zitten op de doel-SOC: nu valt niets bij te laden.
                # Niet alvast bijladen voor het verbruik van later vandaag,
                # want dat zou in de planning vóór dat verbruik vallen.
                note = self._full_battery_note(
                    day, ordered_days, grouped, forecast_groups,
                    forecast_min_difference, forecast_extra_margin,
                    forecast_real_days,
                )
                self.last_smart_notes.append(
                    {"dag": str(day), "tekst": note}
                )
                self.charge_deferred = True
                if cursor is not None and cursor < day_end:
                    projected_soc = self._project_soc_after_draw(
                        projected_soc, cursor, day_end, home_forecast,
                        fallback_home_power, projected_export_blocks,
                        kwargs.get("capacities", []),
                        discharge_efficiencies, projection_minimum_soc,
                    )
                cursor = day_end
                continue
            plan_start = preliminary.start if preliminary else day_end
            if cursor is not None and cursor < plan_start:
                projected_soc = self._project_soc_after_draw(
                    projected_soc,
                    cursor,
                    plan_start,
                    home_forecast,
                    fallback_home_power,
                    projected_export_blocks,
                    kwargs.get("capacities", []),
                    discharge_efficiencies,
                    projection_minimum_soc,
                )
                day_kwargs["soc_values"] = list(projected_soc)
            if smart_enabled and preliminary is not None:
                reduced, note = self._smart_target(
                    position, ordered_days, grouped, preliminary,
                    day_kwargs, smart_reserve_soc, smart_buffer,
                    smart_min_difference, home_forecast,
                    fallback_home_power, projected_export_blocks,
                    discharge_efficiencies,
                    forecast_groups=forecast_groups,
                    forecast_min_difference=forecast_min_difference,
                    forecast_floor_soc=forecast_floor_soc,
                    forecast_extra_margin=forecast_extra_margin,
                    forecast_real_days=forecast_real_days,
                )
                if note:
                    self.last_smart_notes.append(
                        {"dag": str(day), "tekst": note}
                    )
                if reduced is not None:
                    day_kwargs["target_soc"] = reduced
            try:
                plan, battery_plans = self.calculate_per_battery(
                    prices=grouped[day], **day_kwargs
                )
            except ValueError as error:
                if "maximaal laadvermogen is 0 W" in str(error):
                    raise
                cursor = day_end
                continue
            if plan is not None:
                forecasts.append((plan, battery_plans))
                for battery_plan in battery_plans:
                    index = battery_plan.index
                    capacity = float(battery_plan.capacity)
                    if capacity <= 0 or index >= len(projected_soc):
                        continue
                    projected_soc[index] = min(
                        float(battery_plan.target_soc),
                        projected_soc[index]
                        + float(battery_plan.battery_energy_needed)
                        / capacity * 100,
                    )
                cursor = plan.end
            if cursor is not None and cursor < day_end:
                projected_soc = self._project_soc_after_draw(
                    projected_soc,
                    cursor,
                    day_end,
                    home_forecast,
                    fallback_home_power,
                    projected_export_blocks,
                    kwargs.get("capacities", []),
                    discharge_efficiencies,
                    projection_minimum_soc,
                )
            cursor = day_end
        if forecast_groups:
            try:
                self.forecast_day_plans = self._plan_forecast_days(
                    forecast_groups, kwargs, projected_soc, cursor,
                    home_forecast, fallback_home_power,
                    projected_export_blocks, discharge_efficiencies,
                    projection_minimum_soc,
                    smart_enabled, smart_reserve_soc, smart_buffer,
                    forecast_min_difference + forecast_extra_margin,
                    forecast_floor_soc,
                )
            except Exception:  # alleen weergave; nooit de planning storen
                self.forecast_day_plans = []
        return forecasts

    def _smart_target(
        self, position, ordered_days, grouped, preliminary, day_kwargs,
        reserve_soc, buffer, minimum_difference, home_forecast,
        fallback_home_power, export_blocks, discharge_efficiencies,
        forecast_groups=None, forecast_min_difference=0.05,
        forecast_floor_soc=30.0, forecast_extra_margin=0.0,
        forecast_real_days=None,
    ):
        """Slim vooruitkijken: laad niet vol als een latere dag goedkoper is.

        Geeft (verlaagd_doel_soc, melding) terug. Het doel is het verbruik tot
        het volgende, voldoende goedkopere laadblok (plus buffer) en de
        reserve-SOC; nooit meer dan het normale doel.
        """
        if position + 1 >= len(ordered_days):
            if not forecast_groups:
                return None, None
            result = self._forecast_target(
                preliminary, day_kwargs, forecast_groups, reserve_soc,
                buffer, forecast_min_difference, forecast_floor_soc,
                home_forecast, fallback_home_power, export_blocks,
                discharge_efficiencies,
                extra_margin=forecast_extra_margin,
                real_days=forecast_real_days,
            )
            if position == 0 and result[0] is not None:
                # Laden van de lopende dag is uitgesteld op de voorspelling;
                # de accu-energie is dan voor het huis bedoeld.
                self.forecast_postponed = True
                self.charge_deferred = True
            return result
        original_target = float(day_kwargs["target_soc"])
        next_day = ordered_days[position + 1]
        # Zelfde energiebehoefte als vandaag, zodat de prijzen vergelijkbaar zijn.
        next_kwargs = dict(day_kwargs)
        try:
            next_plan, _ = self.calculate_per_battery(
                prices=grouped[next_day], **next_kwargs
            )
        except ValueError:
            return None, None
        if next_plan is None:
            return None, None
        difference = (
            float(preliminary.cheapest_price) - float(next_plan.cheapest_price)
        )
        if difference + 1e-9 < minimum_difference:
            return None, (
                f"Volledig laden: het volgende laadblok is maar "
                f"{max(0.0, difference) * 100:.1f} ct goedkoper"
            )
        count = len(day_kwargs["soc_values"])
        skip = self._fleet_bridge_not_needed(
            preliminary, next_plan.start, day_kwargs, reserve_soc, buffer,
            home_forecast, fallback_home_power, export_blocks,
            discharge_efficiencies,
        )
        if skip is not None:
            if position == 0:
                self.charge_deferred = True
            return 0.0, (
                f"Niet laden vóór {next_plan.start:%d-%m %H:%M} "
                f"({difference * 100:.1f} ct goedkoper): {skip}"
            )
        after = self._project_soc_after_draw(
            [100.0] * count, preliminary.end, next_plan.start,
            home_forecast, fallback_home_power, export_blocks,
            day_kwargs.get("capacities", []), discharge_efficiencies, 0.0,
        )
        draw = max(0.0, 100.0 - float(after[0])) if after else 0.0
        required = min(
            original_target, reserve_soc + draw * (1.0 + buffer)
        )
        return required, (
            f"Laden uitgesteld: {next_plan.start:%d-%m %H:%M} is "
            f"{difference * 100:.1f} ct goedkoper; vandaag laden tot "
            f"{required:.0f}% (verbruik tot dan {draw:.0f}% + buffer)"
        )

    def _fleet_bridge_not_needed(
        self, preliminary, next_start, day_kwargs, reserve_soc, buffer,
        home_forecast, fallback_home_power, export_blocks,
        discharge_efficiencies,
    ):
        """Kijk naar ALLE accu's samen of een overbruggingslading nodig is.

        Het huis wordt uit de gezamenlijke voorraad gevoed (de selector zet de
        accu's met de meeste lading in). Een losse accu die laag staat is dus
        geen reden om vóór een goedkoper blok bij te laden zolang de totale
        energie boven de reserve het verbruik tot dat blok (plus buffer) dekt.
        Geeft een toelichting terug als laden overbodig is, anders None.
        """
        soc_values = [float(v) for v in day_kwargs.get("soc_values") or []]
        capacities = [
            max(0.0, float(v)) for v in day_kwargs.get("capacities") or []
        ]
        total_capacity = sum(capacities)
        if (
            not soc_values
            or len(capacities) != len(soc_values)
            or total_capacity <= 0
            or next_start <= preliminary.start
        ):
            return None
        fleet_soc = sum(
            soc * capacity for soc, capacity in zip(soc_values, capacities)
        ) / total_capacity
        after = self._project_soc_after_draw(
            [100.0] * len(soc_values), preliminary.start, next_start,
            home_forecast, fallback_home_power, export_blocks,
            capacities, discharge_efficiencies, 0.0,
        )
        draw = max(0.0, 100.0 - float(after[0])) if after else 0.0
        needed = float(reserve_soc) + draw * (1.0 + float(buffer))
        if fleet_soc + 1e-9 < needed:
            return None
        return (
            f"accu's samen {fleet_soc:.0f}%, verbruik tot dan "
            f"{draw:.0f}% + buffer, reserve {float(reserve_soc):.0f}%"
        )

    FULL_BATTERY_TOLERANCE_SOC = 2.0

    def _all_at_target(self, day_kwargs):
        target = float(day_kwargs["target_soc"])
        values = day_kwargs.get("soc_values") or []
        return bool(values) and all(
            float(value) >= target - self.FULL_BATTERY_TOLERANCE_SOC
            for value in values
        )

    def _full_battery_note(
        self, day, ordered_days, grouped, forecast_groups,
        minimum_difference, extra_margin, real_days,
    ):
        base = "Accu's zitten op de doel-SOC; vandaag geen laadplan"
        if len(ordered_days) != 1 or not forecast_groups:
            return base
        today_min = min(float(item["price"]) for item in grouped[day])
        best_day, best_price = None, None
        for forecast_day, items in forecast_groups.items():
            price = min(float(item["price"]) for item in items)
            if best_price is None or price < best_price:
                best_day, best_price = forecast_day, price
        if best_price is None:
            return base
        needed = float(minimum_difference)
        if real_days is not None and best_day not in real_days:
            needed += max(0.0, float(extra_margin))
        difference = today_min - best_price
        if difference + 1e-9 >= needed:
            # Goedkopere dag in zicht: de energie is voor het huis bedoeld.
            self.forecast_postponed = True
            return (
                f"{base}; {best_day:%d-%m} is naar verwachting "
                f"{difference * 100:.1f} ct goedkoper, dus geen terugleveren"
            )
        return base

    def _plan_forecast_days(
        self, forecast_groups, kwargs, projected_soc, cursor, home_forecast,
        fallback_home_power, export_blocks, discharge_efficiencies,
        projection_minimum_soc, smart_enabled, reserve_soc, buffer,
        minimum_difference, floor_soc,
    ):
        """Indicatief laadplan voor dagen zonder Frank-prijzen (alleen weergave)."""
        days = sorted(forecast_groups)
        soc = list(projected_soc)
        capacities = kwargs.get("capacities", [])
        reserve = max(float(reserve_soc), float(floor_soc))
        result = []

        def project(start, end):
            return self._project_soc_after_draw(
                soc, start, end, home_forecast, fallback_home_power,
                export_blocks, capacities, discharge_efficiencies,
                projection_minimum_soc,
            )

        for position, day in enumerate(days):
            prices = forecast_groups[day]
            day_end = max(
                self._local_datetime(item["till"]) for item in prices
            )
            day_kwargs = dict(kwargs)
            day_kwargs["soc_values"] = list(soc)
            entry = {
                "dag": str(day),
                "soc_start": round(sum(soc) / len(soc), 0) if soc else None,
                "laden": [],
                "gemiddelde_prijs": None,
                "energie_kwh": 0.0,
                "notitie": None,
            }
            try:
                preliminary, _ = self.calculate_per_battery(
                    prices=prices, **day_kwargs
                )
            except ValueError:
                preliminary = None
            if preliminary is None:
                entry["notitie"] = "Geen bijladen nodig"
                if cursor is not None and cursor < day_end:
                    soc = project(cursor, day_end)
                cursor = day_end
                result.append(entry)
                continue
            if cursor is not None and cursor < preliminary.start:
                soc = project(cursor, preliminary.start)
                day_kwargs["soc_values"] = list(soc)
            if smart_enabled:
                reduced, note = self._smart_target(
                    position, days, forecast_groups, preliminary,
                    day_kwargs, reserve, buffer, minimum_difference,
                    home_forecast, fallback_home_power, export_blocks,
                    discharge_efficiencies,
                )
                if reduced is not None:
                    day_kwargs["target_soc"] = reduced
                entry["notitie"] = note
            try:
                plan, battery_plans = self.calculate_per_battery(
                    prices=prices, **day_kwargs
                )
            except ValueError:
                plan, battery_plans = None, []
            if plan is not None:
                intervals = set()
                for battery_plan in battery_plans:
                    for segment in battery_plan.segments:
                        intervals.add((segment.start, segment.end))
                    index = battery_plan.index
                    capacity = float(battery_plan.capacity)
                    if capacity > 0 and index < len(soc):
                        soc[index] = min(
                            float(battery_plan.target_soc),
                            soc[index]
                            + float(battery_plan.battery_energy_needed)
                            / capacity * 100,
                        )
                entry["laden"] = [
                    {"van": start.isoformat(), "tot": end.isoformat()}
                    for start, end in sorted(intervals)
                ]
                entry["gemiddelde_prijs"] = round(
                    float(plan.average_price), 4
                )
                entry["energie_kwh"] = round(sum(
                    float(item.grid_energy_needed)
                    for item in battery_plans
                ), 2)
                cursor = plan.end
            if cursor is not None and cursor < day_end:
                soc = project(cursor, day_end)
            cursor = day_end
            result.append(entry)
        return result

    def _forecast_target(
        self, preliminary, day_kwargs, forecast_groups, reserve_soc, buffer,
        minimum_difference, floor_soc, home_forecast, fallback_home_power,
        export_blocks, discharge_efficiencies, extra_margin=0.0,
        real_days=None,
    ):
        """Zelfde afweging als bij een bekende dag, maar op voorspelde prijzen.

        Bekende Frank-prijzen zijn leidend; dit loopt alleen voor de dagen
        waarvoor Frank nog geen prijzen heeft. Het verschil moet groter zijn
        dan de (hogere) voorspeldrempel, en de accu houdt minstens de
        ondergrens over bij het voorspelde laadblok.
        """
        original_target = float(day_kwargs["target_soc"])
        best = None
        for forecast_day in sorted(forecast_groups):
            try:
                plan, _ = self.calculate_per_battery(
                    prices=forecast_groups[forecast_day], **dict(day_kwargs)
                )
            except ValueError:
                continue
            if plan is None:
                continue
            if best is None or (
                float(plan.cheapest_price) < float(best.cheapest_price) - 1e-9
            ):
                best = plan
        if best is None:
            return None, None
        difference = (
            float(preliminary.cheapest_price) - float(best.cheapest_price)
        )
        estimate = (
            real_days is not None and best.start.date() not in real_days
        )
        if estimate:
            minimum_difference += max(0.0, float(extra_margin))
        if difference + 1e-9 < minimum_difference:
            return None, (
                f"Volledig laden: de voorspelling is maar "
                f"{max(0.0, difference) * 100:.1f} ct goedkoper"
                + (
                    f" (schatting, vereist {minimum_difference * 100:.0f} ct)"
                    if estimate else ""
                )
            )
        count = len(day_kwargs["soc_values"])
        base = max(float(reserve_soc), float(floor_soc))
        skip = self._fleet_bridge_not_needed(
            preliminary, best.start, day_kwargs, base, buffer,
            home_forecast, fallback_home_power, export_blocks,
            discharge_efficiencies,
        )
        if skip is not None:
            return 0.0, (
                f"Niet laden vóór {best.start:%d-%m %H:%M} (voorspelling "
                f"{difference * 100:.1f} ct goedkoper): {skip}"
            )
        after = self._project_soc_after_draw(
            [100.0] * count, preliminary.end, best.start,
            home_forecast, fallback_home_power, export_blocks,
            day_kwargs.get("capacities", []), discharge_efficiencies, 0.0,
        )
        draw = max(0.0, 100.0 - float(after[0])) if after else 0.0
        required = min(original_target, base + draw * (1.0 + buffer))
        return required, (
            f"Laden uitgesteld op voorspelling: {best.start:%d-%m %H:%M} is "
            f"naar verwachting {difference * 100:.1f} ct goedkoper; "
            f"vandaag laden tot {required:.0f}% (verbruik tot dan "
            f"{draw:.0f}% + buffer, ondergrens {base:.0f}%"
            + (
                f", schatting: marge {minimum_difference * 100:.0f} ct)"
                if estimate else ")"
            )
        )

    def _project_soc_after_draw(
        self,
        soc_values,
        start,
        end,
        home_forecast,
        fallback_home_power,
        export_blocks,
        capacities,
        discharge_efficiencies,
        minimum_soc,
    ):
        if end <= start or not soc_values:
            return list(soc_values)
        hours = (end.timestamp() - start.timestamp()) / 3600
        expected_quarters = max(1, int(round(hours * 4)))
        forecast_values = []
        for item in home_forecast:
            moment = self._local_datetime(item.get("start"))
            power = item.get("vermogen_w")
            if start <= moment < end and power is not None:
                forecast_values.append(max(0.0, float(power)))
        fallback = max(0.0, float(fallback_home_power or 0.0))
        missing = max(0, expected_quarters - len(forecast_values))
        home_energy = (
            sum(forecast_values) + missing * fallback
        ) * 0.25 / 1000
        export_energy = 0.0
        for block in export_blocks:
            overlap_start = max(start, block.start)
            overlap_end = min(end, block.end)
            if overlap_end <= overlap_start or block.hours <= 0:
                continue
            fraction = (
                overlap_end.timestamp() - overlap_start.timestamp()
            ) / 3600 / block.hours
            export_energy += max(
                0.0, float(getattr(block, "expected_energy", 0.0))
            ) * min(1.0, fraction)
        efficiencies = [
            max(0.60, float(value))
            for value in discharge_efficiencies
        ]
        efficiency = (
            sum(efficiencies) / len(efficiencies)
            if efficiencies else 1.0
        )
        total_capacity = sum(max(0.0, float(value)) for value in capacities)
        if total_capacity <= 0:
            return list(soc_values)
        draw_soc = (home_energy + export_energy) / efficiency
        draw_soc = draw_soc / total_capacity * 100
        return [
            max(float(minimum_soc), float(soc) - draw_soc)
            for soc in soc_values
        ]

    def with_economic_price_window(self, plan, prices, price_margin):
        """Voegt economische uren van iedere zichtbare dag toe."""
        if plan is None:
            return None
        eligible_slots = self.economic_price_slots(prices, price_margin)
        return replace(
            plan,
            eligible_slots=eligible_slots,
        )

    def economic_price_slots(self, prices, price_margin):
        """Geeft prijsblokken terug, onafhankelijk van SOC of laadbehoefte."""
        grouped = {}
        for item in prices:
            day = self._local_datetime(item["from"]).date()
            grouped.setdefault(day, []).append(item)
        if not grouped:
            return []
        eligible_slots = []
        for day in sorted(grouped):
            day_prices = grouped[day]
            day_cheapest = min(
                float(item["price"]) for item in day_prices
            )
            day_limit = day_cheapest + float(price_margin)
            eligible_slots.extend(
                item for item in day_prices
                if float(item["price"]) <= day_limit
            )
        return eligible_slots

    def _battery_plan(
        self,
        index,
        name,
        current_soc,
        target_soc,
        capacity,
        battery_energy_needed,
        grid_energy_needed,
        max_power,
        minimum_power,
        preferred_min_power,
        preferred_max_power,
        charge_efficiency,
        slots,
    ):
        max_power = max(0.0, float(max_power))
        if max_power <= 0:
            raise ValueError(f"{name}: maximaal laadvermogen is 0 W")
        chronological_slots = sorted(
            slots,
            key=lambda item: self._local_datetime(item["from"]),
        )
        preferred_min_power = min(
            max_power,
            max(minimum_power, float(preferred_min_power)),
        )
        preferred_max_power = min(
            max_power,
            max(preferred_min_power, float(preferred_max_power)),
        )

        # Gebruik eerst extra goedkope uren om binnen het efficiënte
        # voorkeursmaximum te blijven. Alleen wanneer dat niet past, mag het
        # werkelijke maximum van de accu worden benut.
        selected, usable_hours = self._fitting_contiguous_window(
            chronological_slots, grid_energy_needed, preferred_max_power
        )
        if selected is None:
            selected, usable_hours = self._fitting_contiguous_window(
                chronological_slots, grid_energy_needed, max_power
            )
        if selected is None:
            raise ValueError(
                f"{name}: benodigde energie past niet binnen het laadvenster"
            )

        planned_power = min(
            max_power,
            max(
                min(minimum_power, max_power),
                grid_energy_needed / usable_hours * 1000,
            ),
        )
        duration_left = grid_energy_needed / (planned_power / 1000)
        segments = []
        for slot in selected:
            if duration_left <= 1e-9:
                break
            start = self._local_datetime(slot["from"])
            slot_end = self._local_datetime(slot["till"])
            available = max(
                0.0, (slot_end - start).total_seconds() / 3600
            )
            duration = min(duration_left, available)
            if duration <= 0:
                continue
            segments.append(BatteryChargeSegment(
                start=start,
                end=start + timedelta(hours=duration),
                power=planned_power,
                price=float(slot["price"]),
            ))
            duration_left -= duration
        if duration_left > 1 / 3600:
            raise ValueError(
                f"{name}: laadduur kon niet volledig worden ingepland"
            )
        weighted_price = sum(
            item.price * item.energy_kwh for item in segments
        ) / grid_energy_needed
        if planned_power > preferred_max_power + 1:
            power_strategy = "noodzakelijk_hoger"
        elif planned_power < preferred_min_power - 1:
            power_strategy = "kleine_energievraag"
        else:
            power_strategy = "voorkeursbereik"
        return BatteryChargePlan(
            index=index,
            name=name,
            current_soc=float(current_soc),
            target_soc=float(target_soc),
            capacity=float(capacity),
            battery_energy_needed=float(battery_energy_needed),
            grid_energy_needed=float(grid_energy_needed),
            minimum_power=min(minimum_power, max_power),
            planned_power=planned_power,
            average_price=weighted_price,
            charge_efficiency=charge_efficiency,
            preferred_min_power=preferred_min_power,
            preferred_max_power=preferred_max_power,
            power_strategy=power_strategy,
            segments=segments,
        )

    @staticmethod
    def _normalized_efficiencies(count, fallback, configured):
        values = list(configured or [])
        if not values:
            values = [fallback] * count
        values = [
            max(0.01, min(1.0, float(value))) for value in values
        ]
        values.extend(
            [max(0.01, min(1.0, float(fallback)))]
            * max(0, count - len(values))
        )
        return values[:count]

    @staticmethod
    def _energy_requirements(
        soc_values, capacities, target_soc, efficiencies
    ):
        battery_needs = [
            float(capacity)
            * max(0.0, float(target_soc) - float(soc)) / 100
            for soc, capacity in zip(soc_values, capacities)
        ]
        grid_needs = [
            energy / efficiencies[index]
            for index, energy in enumerate(battery_needs)
        ]
        return battery_needs, grid_needs

    def _fitting_contiguous_window(
        self, chronological_slots, grid_energy_needed, power_limit
    ):
        """Kiest het goedkoopste passende venster zonder laadgaten.

        De oude selectie sorteerde losse uren op prijs. Een resterend
        deeluur kon daardoor vóór een later volledig uur terechtkomen,
        waardoor een accu midden in één laadblok werkelijk werd gestopt.
        Eerst wordt nu de kortste passende aaneengesloten reeks gekozen;
        binnen die lengte wint de reeks met de laagste gewogen prijs.
        """
        slot_count = len(chronological_slots)
        for count in range(1, slot_count + 1):
            fitting = []
            for start_index in range(0, slot_count - count + 1):
                window = chronological_slots[
                    start_index:start_index + count
                ]
                if any(
                    not self._contiguous(left, right)
                    for left, right in zip(window, window[1:])
                ):
                    continue
                duration = self._slots_duration(window)
                usable = max(
                    1 / 60,
                    duration - self.completion_buffer_hours,
                )
                if (
                    grid_energy_needed
                    > power_limit / 1000 * usable + 1e-9
                ):
                    continue
                weighted_price = sum(
                    float(item["price"]) * self._slot_duration(item)
                    for item in window
                ) / duration
                fitting.append((
                    weighted_price,
                    self._local_datetime(window[0]["from"]),
                    window,
                    usable,
                ))
            if fitting:
                _price, _start, window, usable = min(
                    fitting, key=lambda item: (item[0], item[1])
                )
                return list(window), usable
        return None, 0.0

    def _maximum_usable_contiguous_hours(
        self, prices, allowed_start_hour, allowed_end_hour
    ):
        allowed = sorted(
            (
                item for item in prices
                if self._inside_allowed_window(
                    item, allowed_start_hour, allowed_end_hour
                )
            ),
            key=lambda item: self._local_datetime(item["from"]),
        )
        if not allowed:
            return 0.0
        blocks = []
        current = []
        for item in allowed:
            if current and not self._contiguous(current[-1], item):
                blocks.append(current)
                current = []
            current.append(item)
        if current:
            blocks.append(current)
        return max(
            (
                max(
                    0.0,
                    self._slots_duration(block)
                    - self.completion_buffer_hours,
                )
                for block in blocks
            ),
            default=0.0,
        )

    @staticmethod
    def _empty_battery_plan(
        index,
        name,
        current_soc,
        target_soc,
        capacity,
        minimum_power,
        charge_efficiency,
        preferred_min_power=1000,
        preferred_max_power=1500,
    ):
        return BatteryChargePlan(
            index=index,
            name=name,
            current_soc=float(current_soc),
            target_soc=float(target_soc),
            capacity=float(capacity),
            battery_energy_needed=0.0,
            grid_energy_needed=0.0,
            minimum_power=float(minimum_power),
            planned_power=0.0,
            average_price=0.0,
            charge_efficiency=charge_efficiency,
            preferred_min_power=float(preferred_min_power),
            preferred_max_power=float(preferred_max_power),
            power_strategy="geen_lading_nodig",
            segments=[],
        )

    def _aggregate_plan(self, candidate, battery_plans):
        segments = [
            segment
            for plan in battery_plans
            for segment in plan.segments
        ]
        if not segments:
            return None
        total_energy = sum(item.energy_kwh for item in segments)
        average_price = sum(
            item.price * item.energy_kwh for item in segments
        ) / total_energy
        boundaries = sorted({
            moment
            for item in segments
            for moment in (item.start, item.end)
        })
        peak_power = 0.0
        for start, end in zip(boundaries, boundaries[1:]):
            midpoint = start + (end - start) / 2
            peak_power = max(
                peak_power,
                sum(
                    item.power
                    for item in segments
                    if item.start <= midpoint < item.end
                ),
            )
        used_slots = []
        for slot in candidate.slots:
            slot_start = self._local_datetime(slot["from"])
            slot_end = self._local_datetime(slot["till"])
            if any(
                item.start < slot_end and item.end > slot_start
                for item in segments
            ):
                used_slots.append(slot)
        now = datetime.now(self.timezone)
        return ChargePlan(
            start=min(item.start for item in segments),
            end=max(item.end for item in segments),
            power=peak_power,
            hours=self._slots_duration(used_slots),
            average_price=average_price,
            cheapest_price=candidate.cheapest_price,
            price_limit=candidate.price_limit,
            slots=used_slots,
            eligible_slots=list(candidate.slots),
            block_is_active=any(
                item.start <= now < item.end for item in segments
            ),
            effective_charge_hours=max(
                plan.duration_hours for plan in battery_plans
            ),
        )

    def _possible_blocks(self, prices, minimum_hours):
        possible_blocks = []
        for start_index in range(len(prices)):
            candidate = []
            duration = 0.0
            for end_index in range(start_index, len(prices)):
                if candidate and not self._contiguous(
                    candidate[-1], prices[end_index]
                ):
                    break
                candidate.append(prices[end_index])
                duration += self._slot_duration(prices[end_index])
                if duration + 1e-9 < minimum_hours:
                    continue
                average_price = sum(
                    float(item["price"]) * self._slot_duration(item)
                    for item in candidate
                ) / duration
                possible_blocks.append((average_price, list(candidate)))
                break
        return possible_blocks

    def _slots_duration(self, slots):
        return sum(self._slot_duration(item) for item in slots)

    def _slot_duration(self, item):
        return max(
            0.0,
            (
                self._local_datetime(item["till"])
                - self._local_datetime(item["from"])
            ).total_seconds() / 3600,
        )

    def _local_datetime(self, value):
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).astimezone(self.timezone)

    def _inside_allowed_window(self, item, start_hour, end_hour):
        start_hour = float(start_hour)
        end_hour = float(end_hour)
        if not 0 <= start_hour < end_hour <= 24:
            return False
        slot_start = self._local_datetime(item["from"])
        slot_end = self._local_datetime(item["till"])
        start_value = slot_start.hour + slot_start.minute / 60
        end_value = slot_end.hour + slot_end.minute / 60
        if slot_end.date() != slot_start.date():
            end_value = 24.0
        return start_value >= start_hour and end_value <= end_hour

    def _contiguous(self, left, right):
        return (
            self._local_datetime(left["till"]).timestamp()
            == self._local_datetime(right["from"]).timestamp()
        )
