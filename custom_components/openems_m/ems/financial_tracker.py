# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Persistente meting van werkelijke en geplande financiële resultaten."""
from .. import ems_paths

from datetime import datetime, timedelta
from pathlib import Path

from .persistence import JsonStore


class FinancialTracker:
    STATUS_SENSOR = "sensor.openems_financiele_resultaten"

    def __init__(self, app, settings, batteries, timezone, data_file=None):
        self.app = app
        self.settings = settings
        self.batteries = batteries
        self.timezone = timezone
        self.data_file = data_file or (
            ems_paths.data_parent()
            / "OpenEMS-M-data"
            / "financiele_resultaten.json"
        )
        self.store = JsonStore(
            self.data_file,
            app=app,
            timezone=timezone,
            label="Financiële resultaten",
        )
        self.data = self._load()

    def average_inventory_cost(self):
        usable = sum(
            max(0.0, float(item.get("usable_kwh", 0.0)))
            for item in self.data.get("inventory", [])
        )
        if usable <= 1e-9:
            return None
        cost = sum(
            max(0.0, float(item.get("cost_eur", 0.0)))
            for item in self.data.get("inventory", [])
        )
        return cost / usable

    def recent_charge_cost(self):
        """Geef alleen een werkelijk gemeten recente laadprijs terug."""
        charge = 0.0
        cost = 0.0
        days = self.data.get("days", {})
        for key in sorted(days)[-7:]:
            values = days.get(key, {})
            charge += max(
                0.0, float(values.get("laadenergie_kwh", 0.0))
            )
            cost += max(
                0.0, float(values.get("laadkosten_eur", 0.0))
            )
        return cost / charge if charge > 0.05 else None

    def today_charge_cost(self, now):
        """Werkelijk gemeten laadprijs van uitsluitend vandaag."""
        key = self._local(now).date().isoformat()
        day = self.data.get("days", {}).get(key, {})
        charge = max(0.0, float(day.get("laadenergie_kwh", 0.0)))
        cost = max(0.0, float(day.get("laadkosten_eur", 0.0)))
        return cost / charge if charge > 0.05 else None

    def sample(
        self,
        now,
        price,
        soc_values,
        capacities,
        minimum_soc,
        charge_efficiencies,
        discharge_efficiencies,
        export_price=None,
    ):
        now = self._local(now)
        powers = [
            self._number(item.get("actual_power"))
            for item in self.batteries
        ]
        home_power_reader = getattr(self.settings, "home_power", None)
        measured_home_power = (
            home_power_reader()
            if callable(home_power_reader)
            else self._number(self.settings.home_entity())
        )
        grid_entity = self.settings.grid_reference_entity()
        grid_power = self._number(grid_entity)
        fallback_home_power = max(
            0.0,
            sum(max(0.0, value) for value in powers if value is not None),
        )
        home_power = (
            measured_home_power
            if measured_home_power is not None else fallback_home_power
        )
        home_fallback = measured_home_power is None
        last = self.data.get("last_sample")
        previous_powers = self.data.get("last_powers")
        previous_home_power = self.data.get("last_home_power")
        previous_grid_power = self.data.get("last_grid_power")
        previous_home_fallback = bool(
            self.data.get("last_home_fallback", False)
        )
        self.data["last_sample"] = now.isoformat()
        if not last:
            self._seed_inventory(
                soc_values, capacities, minimum_soc,
                self._inventory_seed_price(float(price))
            )
            self._remember_measurement(
                powers, home_power, home_fallback, grid_power
            )
            self._save()
            self.publish(now)
            return
        try:
            elapsed = (now - self._local(last)).total_seconds()
        except (TypeError, ValueError):
            elapsed = 0.0
        elapsed = max(0.0, elapsed)
        measured_seconds = min(120.0, elapsed)
        if measured_seconds <= 0:
            self.publish(now)
            return
        day = self._day(now)
        if (
            any(value is None for value in powers)
            or not isinstance(previous_powers, list)
            or len(previous_powers) != len(powers)
            or any(value is None for value in previous_powers)
        ):
            day["niet_gemeten_s"] += elapsed
            day["ontbrekende_samples"] += 1
            self._remember_measurement(
                powers, home_power, home_fallback, grid_power
            )
            self._save()
            self.publish(now, warning="Accuvermogen niet volledig beschikbaar")
            return
        # Trapeziumintegratie gebruikt het gemiddelde van de vorige en
        # huidige meting. Een herstartgat langer dan twee minuten telt als
        # niet gemeten tijd en wordt niet als energie geboekt.
        previous_home = (
            float(previous_home_power)
            if previous_home_power is not None else float(home_power)
        )
        home_energy = (
            max(0.0, previous_home) + max(0.0, float(home_power))
        ) / 2 / 1000 * (measured_seconds / 3600)
        hours = measured_seconds / 3600
        grid_import = 0.0
        grid_export = 0.0
        if grid_power is not None and previous_grid_power is not None:
            grid_import = (
                max(0.0, float(previous_grid_power))
                + max(0.0, float(grid_power))
            ) / 2 / 1000 * hours
            grid_export = (
                max(0.0, -float(previous_grid_power))
                + max(0.0, -float(grid_power))
            ) / 2 / 1000 * hours
        day["gemeten_s"] += measured_seconds
        day["niet_gemeten_s"] += max(0.0, elapsed - measured_seconds)
        day["geldige_samples"] += 1
        if home_fallback or previous_home_fallback:
            day["woning_fallback_s"] += measured_seconds
        inventory = self.data.setdefault("inventory", [])
        self._ensure_inventory(len(self.batteries))

        total_charge = 0.0
        total_discharge = 0.0
        for index, (previous_power, current_power) in enumerate(
            zip(previous_powers, powers)
        ):
            charge = (
                max(0.0, -float(previous_power))
                + max(0.0, -float(current_power))
            ) / 2 / 1000 * hours
            discharge = (
                max(0.0, float(previous_power))
                + max(0.0, float(current_power))
            ) / 2 / 1000 * hours
            total_charge += charge
            total_discharge += discharge
            # De financiële voorraad bewaart de werkelijk betaalde
            # laadenergie en all-inprijs. Rendement is een afzonderlijke
            # plannerkeuze en mag deze zichtbare kostprijs niet verhogen.
            usable_added = charge
            inventory[index]["usable_kwh"] += usable_added
            inventory[index]["cost_eur"] += charge * float(price)
            available = inventory[index]["usable_kwh"]
            consumed = min(discharge, available)
            unit_cost = (
                inventory[index]["cost_eur"] / available
                if available > 1e-9 else float(price)
            )
            inventory[index]["usable_kwh"] = max(0.0, available - consumed)
            inventory[index]["cost_eur"] = max(
                0.0,
                inventory[index]["cost_eur"] - consumed * unit_cost,
            )

        self._reconcile_inventory(
            now, soc_values, capacities, minimum_soc, float(price)
        )

        battery_to_home = min(total_discharge, home_energy)
        day["laadenergie_kwh"] += total_charge
        day["ontlaadenergie_kwh"] += total_discharge
        day["woning_uit_accu_kwh"] += battery_to_home
        day["netafname_kwh"] += grid_import
        day["teruglevering_kwh"] += grid_export
        day["terugleveropbrengst_eur"] += (
            grid_export * float(export_price)
            if export_price is not None else 0.0
        )
        day["laadkosten_eur"] += total_charge * float(price)
        self._remember_measurement(
            powers, home_power, home_fallback, grid_power
        )
        self._save()
        self.publish(now)

    def publish(self, now, warning=None):
        now = self._local(now)
        today = self._day(now)
        yesterday_key = (now.date() - timedelta(days=1)).isoformat()
        yesterday = self.data.get("days", {}).get(
            yesterday_key, self._empty_period()
        )
        week = self._empty_period()
        for offset in range(7):
            key = (now.date() - timedelta(days=offset)).isoformat()
            values = self.data.get("days", {}).get(key, {})
            for field in week:
                week[field] += float(values.get(field, 0.0))
        month_key = now.strftime("%Y-%m")
        month = self._empty_period()
        for date_key, values in self.data.get("days", {}).items():
            if date_key.startswith(month_key):
                for field in month:
                    month[field] += float(values.get(field, 0.0))
        today_view = self._view(today, now.date().isoformat())
        yesterday_view = self._view(yesterday, yesterday_key)
        week_view = self._view(week, "afgelopen 7 dagen")
        month_view = self._view(month, month_key)
        inventory_kwh = sum(
            max(0.0, float(item.get("usable_kwh", 0.0)))
            for item in self.data.get("inventory", [])
        )
        inventory_cost = sum(
            max(0.0, float(item.get("cost_eur", 0.0)))
            for item in self.data.get("inventory", [])
        )
        average_inventory_cost = (
            inventory_cost / inventory_kwh
            if inventory_kwh > 1e-9 else None
        )
        self.app.set_state(
            self.STATUS_SENSOR,
            state=(
                f"{average_inventory_cost:.4f}"
                if average_inventory_cost is not None else "onbekend"
            ),
            attributes={
                "friendly_name": "OpenEMS financiële resultaten",
                "icon": "mdi:finance",
                "unit_of_measurement": "EUR/kWh",
                "vandaag": today_view,
                "gisteren": yesterday_view,
                "afgelopen_7_dagen": week_view,
                "deze_maand": month_view,
                "opgeslagen_energie": {
                    "bruikbaar_kwh": round(inventory_kwh, 3),
                    "totale_kostprijs_eur": round(inventory_cost, 3),
                    "gemiddelde_kostprijs_per_kwh": (
                        round(average_inventory_cost, 4)
                        if average_inventory_cost is not None else None
                    ),
                },
                "waarschuwing": warning,
                "kwaliteitslabel": today_view["kwaliteitslabel"],
                "meetdekking_pct": today_view["meetdekking_pct"],
                "uitleg": {
                    "gemeten": [
                        "laadenergie_kwh", "ontlaadenergie_kwh",
                        "netafname_kwh", "teruglevering_kwh",
                        "laadkosten_eur", "terugleveropbrengst_eur",
                    ],
                    "voorraad": [
                        "bruikbaar_kwh", "totale_kostprijs_eur",
                        "gemiddelde_kostprijs_per_kwh",
                    ],
                },
                "opslagbestand": str(self.data_file),
            },
            replace=True,
        )

    def _view(self, period, key):
        result = {
            field: round(float(period.get(field, default)), 4)
            for field, default in self._empty_period().items()
        }
        charge = result["laadenergie_kwh"]
        result["gemiddelde_laadprijs"] = round(
            result["laadkosten_eur"] / charge, 4
        ) if charge > 0 else None
        result["periode"] = key
        measured = result["gemeten_s"]
        missing = result["niet_gemeten_s"]
        observed = measured + missing
        coverage = measured / observed * 100 if observed > 0 else 0.0
        fallback = (
            result["woning_fallback_s"] / measured * 100
            if measured > 0 else 0.0
        )
        result["meetdekking_pct"] = round(coverage, 1)
        result["woning_fallback_pct"] = round(fallback, 1)
        if observed <= 0:
            quality = "nog geen meetperiode"
        elif coverage >= 98 and fallback <= 1:
            quality = "hoog"
        elif coverage >= 90 and fallback <= 20:
            quality = "redelijk"
        else:
            quality = "beperkt"
        result["kwaliteitslabel"] = quality
        return result

    def _seed_inventory(
        self, soc_values, capacities, minimum_soc, price
    ):
        self._ensure_inventory(len(self.batteries))
        for index in range(len(self.batteries)):
            usable = (
                float(capacities[index])
                * max(0.0, float(soc_values[index]) - float(minimum_soc))
                / 100
            )
            if self.data["inventory"][index]["usable_kwh"] <= 0:
                self.data["inventory"][index] = {
                    "usable_kwh": usable,
                    "cost_eur": usable * price,
                }

    def _reconcile_inventory(
        self, now, soc_values, capacities, minimum_soc, fallback_price
    ):
        """Corrigeert meetverlies rustig richting werkelijk bruikbare SOC."""
        last = self.data.get("last_inventory_reconcile")
        if last:
            try:
                if (now - self._local(last)).total_seconds() < 15 * 60:
                    return
            except (TypeError, ValueError):
                pass
        self.data["last_inventory_reconcile"] = now.isoformat()
        self._ensure_inventory(len(self.batteries))
        seed_price = self._inventory_seed_price(fallback_price)
        for index, item in enumerate(self.data["inventory"]):
            if index >= len(soc_values) or index >= len(capacities):
                continue
            capacity = max(0.0, float(capacities[index]))
            actual = capacity * max(
                0.0, float(soc_values[index]) - float(minimum_soc)
            ) / 100
            tracked = max(0.0, float(item.get("usable_kwh", 0.0)))
            difference = actual - tracked
            if abs(difference) <= 0.02:
                continue
            # Per kwartier maximaal vijf procent van de accucapaciteit. Zo
            # verdwijnen structurele meet-/rendementsverschillen zonder dat
            # een afgeronde SOC-sprong de kostprijs abrupt laat verspringen.
            limit = max(0.05, capacity * 0.05)
            corrected = max(
                0.0, tracked + max(-limit, min(limit, difference))
            )
            unit_cost = (
                float(item.get("cost_eur", 0.0)) / tracked
                if tracked > 1e-9 else seed_price
            )
            item["usable_kwh"] = corrected
            item["cost_eur"] = max(0.0, corrected * unit_cost)

    def _day(self, now):
        key = now.date().isoformat()
        days = self.data.setdefault("days", {})
        if key not in days:
            days[key] = self._empty_period()
        else:
            for field, value in self._empty_period().items():
                days[key].setdefault(field, value)
        return days[key]

    @staticmethod
    def _empty_period():
        return {
            "laadenergie_kwh": 0.0,
            "ontlaadenergie_kwh": 0.0,
            "woning_uit_accu_kwh": 0.0,
            "netafname_kwh": 0.0,
            "teruglevering_kwh": 0.0,
            "terugleveropbrengst_eur": 0.0,
            "laadkosten_eur": 0.0,
            "gemeten_s": 0.0,
            "niet_gemeten_s": 0.0,
            "woning_fallback_s": 0.0,
            "geldige_samples": 0.0,
            "ontbrekende_samples": 0.0,
        }

    def _load(self):
        data = self.store.load(self._empty_data)
        if int(data.get("version", 0)) < 4:
            # Oude voorraadkosten bevatten tweemaal een aangenomen
            # rendement. Start de voorraad opnieuw vanuit de actuele SOC en
            # de werkelijk gemeten gemiddelde laadprijs.
            data["inventory"] = []
            data["last_sample"] = None
        data["version"] = 5
        data.setdefault("last_inventory_reconcile", None)
        return data

    @staticmethod
    def _empty_data():
        return {
            "version": 5,
            "last_sample": None,
            "last_powers": None,
            "last_home_power": None,
            "last_grid_power": None,
            "last_home_fallback": False,
            "inventory": [],
            "last_inventory_reconcile": None,
            "days": {},
        }

    def _remember_measurement(
        self, powers, home_power, home_fallback, grid_power
    ):
        self.data["last_powers"] = (
            [float(value) for value in powers]
            if all(value is not None for value in powers) else None
        )
        self.data["last_home_power"] = (
            float(home_power) if home_power is not None else None
        )
        self.data["last_home_fallback"] = bool(home_fallback)
        self.data["last_grid_power"] = (
            float(grid_power) if grid_power is not None else None
        )

    def _save(self):
        self.store.save(self.data)

    def _ensure_inventory(self, count):
        inventory = self.data.setdefault("inventory", [])
        while len(inventory) < count:
            inventory.append({"usable_kwh": 0.0, "cost_eur": 0.0})

    def _inventory_seed_price(self, fallback):
        recent = self.recent_charge_cost()
        return recent if recent is not None else float(fallback)

    def _number(self, entity):
        if not entity:
            return None
        value = self.app.get_state(entity)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _local(self, value):
        if isinstance(value, datetime):
            moment = value
        else:
            moment = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.timezone)
        return moment.astimezone(self.timezone)
