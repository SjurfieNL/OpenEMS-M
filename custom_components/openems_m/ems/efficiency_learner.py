# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Passief zelflerend laad- en ontlaadrendement per accu."""
from .. import ems_paths

from datetime import datetime
from pathlib import Path

from .persistence import JsonStore


class EfficiencyLearner:
    """Leert uitsluitend uit stabiele, normale gebruikssessies.

    De metingen sturen nooit rechtstreeks een accu aan. Een planner mag een
    resultaat pas gebruiken wanneer de gebruiker Zelfgeleerd kiest en de
    bijbehorende richting de status betrouwbaar heeft.
    """

    MIN_POWER_W = 200
    MIN_DURATION_S = 15 * 60
    MIN_SOC_DELTA = 3.0
    BIN_SIZE_W = 250

    def __init__(self, app, batteries, timezone, data_file=None):
        self.app = app
        self.batteries = batteries
        self.timezone = timezone
        self.data_file = data_file or (
            ems_paths.data_parent()
            / "OpenEMS-M-data"
            / "zelfgeleerd_rendement.json"
        )
        self.store = JsonStore(
            self.data_file,
            app=app,
            timezone=timezone,
            label="Zelfgeleerd rendement",
        )
        self.data = self.store.load(self._empty_data)
        self._ensure_batteries()
        self.sessions = [None for _ in batteries]
        self.previous = [None for _ in batteries]

    def sample(self, now, soc_values, capacities):
        now = self._local(now)
        for index, battery in enumerate(self.batteries):
            power = self._number(battery.get("actual_power"))
            soc = self._value(soc_values, index)
            capacity = self._value(capacities, index)
            if power is None or soc is None or not capacity or capacity <= 0:
                self._finish(index, now)
                self.previous[index] = None
                continue
            direction = self._direction(power)
            previous = self.previous[index]
            self.previous[index] = {
                "time": now, "power": float(power), "soc": float(soc),
            }
            session = self.sessions[index]
            if direction is None:
                self._finish(index, now)
                continue
            if session is None or session["direction"] != direction:
                self._finish(index, now)
                self.sessions[index] = {
                    "direction": direction,
                    "start": now,
                    "start_soc": float(soc),
                    "last_soc": float(soc),
                    "capacity": float(capacity),
                    "ac_kwh": 0.0,
                    "power_ws": 0.0,
                    "seconds": 0.0,
                }
                continue
            if previous is None:
                continue
            elapsed = max(
                0.0, min(120.0, (now - previous["time"]).total_seconds())
            )
            if elapsed <= 0:
                continue
            previous_direction = self._direction(previous["power"])
            if previous_direction != direction:
                continue
            average_power = (
                abs(float(previous["power"])) + abs(float(power))
            ) / 2
            session["ac_kwh"] += average_power / 1000 * elapsed / 3600
            session["power_ws"] += average_power * elapsed
            session["seconds"] += elapsed
            session["last_soc"] = float(soc)
            session["capacity"] = float(capacity)
        self.publish(now)

    def publish(self, now=None):
        moment = self._local(now or datetime.now(self.timezone))
        for index, battery in enumerate(self.batteries):
            learned = self.data["batteries"][index]
            charge = self._summary(learned["charge"])
            discharge = self._summary(learned["discharge"])
            self.app.set_state(
                f"sensor.openems_accu_{index + 1}_zelfgeleerd_rendement",
                state=self._state_label(charge, discharge),
                attributes={
                    "friendly_name": (
                        f"OpenEMS zelfgeleerd rendement {battery['name']}"
                    ),
                    "icon": "mdi:gauge",
                    "laden": charge,
                    "ontladen": discharge,
                    "laatst_bijgewerkt": moment.isoformat(),
                    "uitleg": (
                        "Passieve OpenEMS-meting uit SOC-verandering en "
                        "werkelijk AC-vermogen; nog niet gebruikt tenzij de "
                        "gebruiker Zelfgeleerd rendement kiest."
                    ),
                },
                replace=True,
            )

    def discharge_power_profiles(self):
        """Geef planners alleen de openbare, gevalideerde ontlaadprofielen."""
        return [
            self._summary(item["discharge"])
            for item in self.data.get("batteries", [])
        ]

    def _finish(self, index, now):
        session = self.sessions[index]
        self.sessions[index] = None
        if not session:
            return
        delta = (
            session["last_soc"] - session["start_soc"]
            if session["direction"] == "charge"
            else session["start_soc"] - session["last_soc"]
        )
        if (
            session["seconds"] < self.MIN_DURATION_S
            or delta < self.MIN_SOC_DELTA
            or session["ac_kwh"] <= 0.05
        ):
            return
        battery_kwh = session["capacity"] * delta / 100
        if session["direction"] == "charge":
            numerator = battery_kwh
            denominator = session["ac_kwh"]
        else:
            numerator = session["ac_kwh"]
            denominator = battery_kwh
        efficiency = numerator / denominator if denominator > 0 else 0.0
        if not 0.60 <= efficiency <= 1.05:
            return
        average_power = session["power_ws"] / session["seconds"]
        power_bin = int(
            round(average_power / self.BIN_SIZE_W) * self.BIN_SIZE_W
        )
        target = self.data["batteries"][index][session["direction"]]
        self._add(target["overall"], numerator, denominator, session, now)
        bucket = target["bins"].setdefault(
            str(max(self.BIN_SIZE_W, power_bin)), self._empty_aggregate()
        )
        self._add(bucket, numerator, denominator, session, now)
        self.store.save(self.data)

    @staticmethod
    def _add(target, numerator, denominator, session, now):
        target["numerator_kwh"] += numerator
        target["denominator_kwh"] += denominator
        target["seconds"] += session["seconds"]
        target["sessions"] += 1
        target["last"] = now.isoformat()

    def _summary(self, direction):
        overall = self._aggregate_view(direction["overall"])
        bins = []
        for power, values in direction["bins"].items():
            view = self._aggregate_view(values)
            view["vermogen_w"] = int(power)
            bins.append(view)
        bins.sort(key=lambda item: item["vermogen_w"])
        candidates = [
            item for item in bins
            if item["betrouwbaarheid"] in ("redelijk", "hoog")
        ]
        preferred = (
            max(candidates, key=lambda item: item["rendement_pct"])
            if candidates else None
        )
        return {
            **overall,
            "vermogensgebieden": bins,
            "voorkeursvermogen_w": (
                preferred["vermogen_w"] if preferred else None
            ),
        }

    @staticmethod
    def _aggregate_view(values):
        denominator = float(values.get("denominator_kwh", 0.0))
        numerator = float(values.get("numerator_kwh", 0.0))
        efficiency = numerator / denominator if denominator > 0 else None
        sessions = int(values.get("sessions", 0))
        energy = max(numerator, denominator)
        if sessions >= 4 and energy >= 5.0:
            confidence = "hoog"
        elif sessions >= 2 and energy >= 2.0:
            confidence = "redelijk"
        else:
            confidence = "nog lerend"
        return {
            "rendement_pct": (
                round(min(1.0, efficiency) * 100, 1)
                if efficiency is not None else None
            ),
            "betrouwbaarheid": confidence,
            "sessies": sessions,
            "energie_kwh": round(energy, 2),
            "duur_uren": round(float(values.get("seconds", 0.0)) / 3600, 1),
            "laatste_meting": values.get("last"),
        }

    @staticmethod
    def _state_label(charge, discharge):
        if (
            charge["betrouwbaarheid"] == "hoog"
            and discharge["betrouwbaarheid"] == "hoog"
        ):
            return "betrouwbaar"
        if charge["sessies"] or discharge["sessies"]:
            return "lerend"
        return "nog geen geldige sessie"

    @staticmethod
    def _direction(power):
        if power <= -EfficiencyLearner.MIN_POWER_W:
            return "charge"
        if power >= EfficiencyLearner.MIN_POWER_W:
            return "discharge"
        return None

    def _number(self, entity_id):
        if not entity_id:
            return None
        try:
            value = self.app.get_state(entity_id)
            if value in (None, "", "unknown", "unavailable"):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _value(values, index):
        try:
            return float(values[index])
        except (IndexError, TypeError, ValueError):
            return None

    def _local(self, value):
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    @staticmethod
    def _empty_aggregate():
        return {
            "numerator_kwh": 0.0,
            "denominator_kwh": 0.0,
            "seconds": 0.0,
            "sessions": 0,
            "last": None,
        }

    @classmethod
    def _empty_direction(cls):
        return {"overall": cls._empty_aggregate(), "bins": {}}

    def _ensure_batteries(self):
        """Vul opgeslagen data aan wanneer er een accu bij is gekomen."""
        if not isinstance(self.data, dict):
            self.data = self._empty_data()
        items = self.data.setdefault("batteries", [])
        while len(items) < len(self.batteries):
            items.append({
                "charge": self._empty_direction(),
                "discharge": self._empty_direction(),
            })

    def _empty_data(self):
        return {
            "version": 1,
            "batteries": [
                {
                    "charge": self._empty_direction(),
                    "discharge": self._empty_direction(),
                }
                for _ in self.batteries
            ],
        }
