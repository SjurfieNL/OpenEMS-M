"""Observerend kwartierprofiel voor verwacht woningverbruik."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from persistence import JsonStore


class HomeLoadForecaster:
    STATUS_SENSOR = "sensor.openems_woningverbruik_voorspelling"
    AIRCO_POWER = "sensor.woonkamer_tcl_airco_vermogen"
    AIRCO_CLIMATE = "climate.airco_climate"
    OUTDOOR_TEMPERATURE = "sensor.balkon_motion_sensor_temperatuur"
    WEATHER_FORECAST = "sensor.thuis_weersverwachting"
    MAX_RECENT_SAMPLES = 96
    MAX_SAMPLES_PER_BUCKET = 8 * 7  # circa acht weken bij één sample/kwartier

    def __init__(self, app, settings, timezone, data_file=None):
        self.app = app
        self.settings = settings
        self.timezone = timezone
        path = data_file or (
            Path(__file__).resolve().parent.parent
            / "OpenEMS-M-data"
            / "woningverbruik_profiel.json"
        )
        self.store = JsonStore(
            path, app=app, timezone=timezone,
            label="Woningverbruiksprofiel",
        )
        self.data = self.store.load(lambda: {
            "version": 2,
            "buckets": {},
            "samples": 0,
            "recent_values": [],
            "smoothed_power_w": None,
        })

    def sample(self, kwargs=None):
        now = datetime.now(self.timezone)
        home_power_reader = getattr(self.settings, "home_power", None)
        power = (
            home_power_reader()
            if callable(home_power_reader)
            else self._number(self.settings.home_entity())
        )
        if power is None:
            self._publish(now, "Woningvermogenssensor niet beschikbaar")
            return
        key = self._bucket_key(now)
        bucket = self.data.setdefault("buckets", {}).setdefault(
            key, {"values": [], "hot_values": [], "airco_values": []}
        )
        self._append(bucket["values"], max(0.0, power))
        recent = self.data.setdefault("recent_values", [])
        recent.append(round(max(0.0, power), 1))
        del recent[:-self.MAX_RECENT_SAMPLES]
        outside = self._number(self.OUTDOOR_TEMPERATURE)
        airco = self._number(self.AIRCO_POWER)
        if outside is not None and outside >= 24:
            self._append(bucket["hot_values"], max(0.0, power))
        if airco is not None and airco > 25:
            self._append(bucket["airco_values"], max(0.0, power))
        self.data["samples"] = int(self.data.get("samples", 0)) + 1
        self._publish(now, "")

    def forecast(self, start, hours=24):
        start = self._local(start)
        hot = (self._number(self.OUTDOOR_TEMPERATURE) or 0) >= 24
        result = []
        for step in range(max(1, int(hours * 4))):
            moment = start + timedelta(minutes=15 * step)
            bucket = self.data.get("buckets", {}).get(
                self._bucket_key(moment), {}
            )
            values = (
                bucket.get("hot_values", [])
                if hot and len(bucket.get("hot_values", [])) >= 3
                else bucket.get("values", [])
            )
            result.append({
                "start": moment.isoformat(),
                "vermogen_w": round(self._average(values), 0)
                if values else None,
                "samples": len(values),
                "warmtedcorrectie": bool(
                    hot and values is bucket.get("hot_values")
                ),
            })
        return result

    def baseline_power(self):
        """Stabiele waarde die op Overzicht als Rustig verbruik staat."""
        return self._optional_number(self.data.get("smoothed_power_w"))

    def _publish(self, now, warning):
        forecast = self.forecast(now)
        valid = [item for item in forecast if item["vermogen_w"] is not None]
        confidence = min(
            100.0,
            sum(min(8, item["samples"]) for item in forecast)
            / max(1, len(forecast) * 8) * 100,
        )
        profile_power = self._average([
            item["vermogen_w"] for item in valid
        ])
        recent_power = self._average(
            self.data.get("recent_values", [])
        )
        raw_expected = (
            0.8 * profile_power + 0.2 * recent_power
            if profile_power is not None and recent_power is not None
            else profile_power if profile_power is not None
            else recent_power
        )
        previous = self._optional_number(
            self.data.get("smoothed_power_w")
        )
        expected = (
            raw_expected
            if previous is None or raw_expected is None
            else previous + 0.2 * (raw_expected - previous)
        )
        self.data["smoothed_power_w"] = (
            round(expected, 1) if expected is not None else None
        )
        self.store.save(self.data)
        self.app.set_state(
            self.STATUS_SENSOR,
            state=(
                round(expected) if expected is not None else "onvoldoende data"
            ),
            attributes={
                "friendly_name": "OpenEMS verwacht woningverbruik",
                "icon": "mdi:home-clock",
                "unit_of_measurement": "W",
                "modus": "Automatische rustige verbruiksschatting",
                "bron": (
                    "kwartierprofiel en recent gemiddelde"
                    if profile_power is not None
                    else "recent gemiddelde"
                ),
                "betrouwbaarheid_pct": round(confidence, 1),
                "profiel_per_kwartier": forecast,
                "airco_sensor": self.AIRCO_POWER,
                "buitentemperatuur_sensor": self.OUTDOOR_TEMPERATURE,
                "weersensor": self.WEATHER_FORECAST,
                "waarschuwing": warning,
            },
            replace=True,
        )

    def _append(self, values, value):
        values.append(round(float(value), 1))
        del values[:-self.MAX_SAMPLES_PER_BUCKET]

    @staticmethod
    def _average(values):
        numbers = sorted(float(value) for value in values)
        if not numbers:
            return None
        trim = int(len(numbers) * 0.1) if len(numbers) >= 10 else 0
        selected = numbers[trim:len(numbers) - trim] if trim else numbers
        return sum(selected) / len(selected)

    @staticmethod
    def _optional_number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bucket_key(moment):
        quarter = moment.hour * 4 + moment.minute // 15
        return f"{moment.weekday()}:{quarter:02d}"

    def _number(self, entity):
        if not entity:
            return None
        try:
            return float(self.app.get_state(entity))
        except (TypeError, ValueError):
            return None

    def _local(self, value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)
