"""Bewaking van geldige maar verouderde kritieke sensordata."""

from datetime import datetime, timezone

from ems_config import (
    BATTERY_POWER_DATA_MAX_AGE_SECONDS,
    GRID_DATA_MAX_AGE_SECONDS,
    SOC_DATA_MAX_AGE_SECONDS,
)


class TelemetryFreshnessGuard:
    STATUS_SENSOR = "sensor.openems_sensordata_status"
    STANDBY_POWER_TOLERANCE_W = 25.0

    def __init__(self, app, settings, batteries, disabled_provider=None):
        self.app = app
        self.settings = settings
        self.batteries = batteries
        self.disabled_provider = disabled_provider
        self.last_result = {
            "ok": True, "status": "actueel",
            "problems": [], "warnings": [], "measurements": []
        }
        self._latched_message = None

    def check(self, include_grid=True, publish=True):
        checks = []
        if include_grid:
            checks.append((
                "Netvermogen",
                self.settings.grid_entity(),
                GRID_DATA_MAX_AGE_SECONDS,
                None,
            ))
        disabled = self._disabled_indexes()
        for index, battery in enumerate(self.batteries):
            if index in disabled:
                # Een bewust uitgeschakelde accu mag offline of stil zijn
                # zonder de regeling van de andere accu's te blokkeren.
                continue
            checks.extend([
                (
                    f"{battery['name']} SOC",
                    battery.get("soc"),
                    SOC_DATA_MAX_AGE_SECONDS,
                    index,
                    False,
                ),
                (
                    f"{battery['name']} werkelijk vermogen",
                    battery.get("actual_power"),
                    BATTERY_POWER_DATA_MAX_AGE_SECONDS,
                    index,
                    True,
                ),
            ])

        measurements = []
        problems = []
        for check in checks:
            label, entity, maximum_age, battery_index = check[:4]
            accept_stationary_standby = (
                bool(check[4]) if len(check) > 4 else False
            )
            item = self._measurement(
                label,
                entity,
                maximum_age,
                battery_index,
                accept_stationary_standby=accept_stationary_standby,
            )
            measurements.append(item)
            if not item["ok"]:
                problems.append(item)
        self.last_result = {
            "ok": not problems,
            "status": (
                "ongeldig/geblokkeerd" if problems
                else "ouderdom waarschuwing"
                if any(item.get("verouderd") for item in measurements)
                else "actueel"
            ),
            "problems": problems,
            "warnings": [
                item for item in measurements if item.get("verouderd")
            ],
            "measurements": measurements,
        }
        if publish:
            self._publish()
        return self.last_result

    def _disabled_indexes(self):
        if not callable(self.disabled_provider):
            return set()
        try:
            return {int(index) for index in self.disabled_provider()}
        except (TypeError, ValueError):
            return set()

    def log_transition(self):
        problems = self.last_result["problems"]
        message = " | ".join(item["reden"] for item in problems)
        if not message:
            if self._latched_message:
                self.app.log(
                    "[Veiligheid] Kritieke sensordata is weer actueel"
                )
            self._latched_message = None
            return
        if message != self._latched_message:
            self.app.log(
                "[Veiligheid] Geen nieuwe vermogensopdracht: " + message,
                level="WARNING",
            )
            self._latched_message = message

    def is_stale(self, entity):
        """Geeft aan of een geldige meting alleen een ouderdomswaarschuwing heeft."""
        return any(
            item.get("entity") == entity and item.get("verouderd")
            for item in self.last_result.get("measurements", [])
        )

    def _measurement(
        self,
        label,
        entity,
        maximum_age,
        battery_index,
        accept_stationary_standby=False,
    ):
        if not entity:
            return {
                "naam": label,
                "entity": "",
                "ouderdom_s": None,
                "maximaal_s": maximum_age,
                "ok": False,
                "accu_index": battery_index,
                "reden": f"{label}: sensor niet ingesteld",
            }
        state = self.app.get_state(entity)
        if state in (None, "", "unknown", "unavailable"):
            return {
                "naam": label,
                "entity": entity,
                "ouderdom_s": None,
                "maximaal_s": maximum_age,
                "ok": False,
                "accu_index": battery_index,
                "reden": f"{label}: {state or 'geen waarde'}",
            }
        try:
            all_state = self.app.get_state(entity, attribute="all")
        except TypeError:
            all_state = None
        timestamp = None
        if isinstance(all_state, dict):
            timestamp = (
                all_state.get("last_reported")
                or all_state.get("last_updated")
                or all_state.get("last_changed")
            )
        age = self._age(timestamp)
        # Oudere AppDaemon/HA-combinaties leveren geen timestamp. Een geldige
        # waarde blijft dan bruikbaar en wordt als 'ouderdom onbekend' getoond.
        stale = age is not None and age > maximum_age
        # Home Assistant verandert last_updated niet altijd wanneer een
        # integratie exact dezelfde geldige waarde opnieuw meldt. Ouderdom is
        # daarom diagnostische informatie en op zichzelf geen veilige grond
        # om een laad- of ontlaadopdracht te onderbreken.
        ok = True
        stationary_standby = False
        if stale and accept_stationary_standby:
            try:
                stationary_standby = (
                    abs(float(state)) <= self.STANDBY_POWER_TOLERANCE_W
                )
            except (TypeError, ValueError):
                stationary_standby = False
        return {
            "naam": label,
            "entity": entity,
            "ouderdom_s": round(age, 1) if age is not None else None,
            "maximaal_s": maximum_age,
            "ok": ok,
            "verouderd": stale,
            "stabiele_rustwaarde": stationary_standby,
            "accu_index": battery_index,
            "waarschuwing": (
                f"{label}: {age:.0f}s oud (max {maximum_age}s)"
                if stale else ""
            ),
            "reden": "",
        }

    @staticmethod
    def _age(value):
        if not value:
            return None
        try:
            moment = (
                value if isinstance(value, datetime)
                else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            )
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (datetime.now(timezone.utc) - moment.astimezone(timezone.utc))
                .total_seconds(),
            )
        except (TypeError, ValueError):
            return None

    def _publish(self):
        problems = self.last_result["problems"]
        warnings = [
            item["waarschuwing"]
            for item in self.last_result["measurements"]
            if item.get("waarschuwing")
        ]
        self.app.set_state(
            self.STATUS_SENSOR,
            state=self.last_result["status"],
            attributes={
                "friendly_name": "OpenEMS kritieke sensordata",
                "icon": (
                    "mdi:database-alert" if problems
                    else "mdi:database-clock"
                    if warnings else "mdi:database-check"
                ),
                "veilig": not problems,
                "problemen": [
                    item["reden"] for item in problems
                ],
                "waarschuwingen": warnings,
                "metingen": self.last_result["measurements"],
                "actie": (
                    "Normale aansturing; ouderdom alleen is diagnostisch"
                    if warnings and not problems
                    else "Normale aansturing" if not problems
                    else "Geen nieuwe vermogensopdracht"
                ),
            },
            replace=True,
        )
