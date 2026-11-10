"""Blijvende, compacte gebeurtenishistorie voor runtime-afwijkingen."""

from datetime import datetime, timedelta
import json
from pathlib import Path

from persistence import JsonStore


class RuntimeEventLog:
    SENSOR = "sensor.openems_runtime_gebeurtenissen"
    CLEAR_BUTTON = "input_button.openems_runtime_gebeurtenissen_wissen"
    RETENTION_DAYS = 30
    MAX_EVENTS = 300
    # Home Assistant weigert state-attributen vanaf 16.384 bytes. Houd ruimte
    # voor JSON-overhead en eventuele wijzigingen in Recorder.
    MAX_PUBLISHED_ATTRIBUTE_BYTES = 14_000
    MAX_PUBLISHED_EVENTS = 50
    DEDUPLICATION_MINUTES = 15

    def __init__(self, app, timezone, data_file=None):
        self.app = app
        self.timezone = timezone
        self.data_file = data_file or (
            Path(__file__).resolve().parent.parent
            / "OpenEMS-M-data"
            / "runtime_gebeurtenissen.json"
        )
        self.store = JsonStore(
            self.data_file,
            app=app,
            timezone=timezone,
            label="Runtime-gebeurtenissen",
        )
        loaded = self.store.load(lambda: {"events": []})
        self.data = loaded if isinstance(loaded, dict) else {"events": []}
        if not isinstance(self.data.get("events"), list):
            self.data["events"] = []
        self._prune()
        self.publish()
        listen_state = getattr(self.app, "listen_state", None)
        if callable(listen_state):
            listen_state(self._clear_requested, self.CLEAR_BUTTON)

    def record(
        self,
        level,
        title,
        summary,
        *,
        battery_index=None,
        battery_name=None,
        details=None,
        expected_state=None,
        attempt=None,
    ):
        now = datetime.now(self.timezone)
        event = {
            "id": f"{now:%Y%m%d%H%M%S%f}",
            "tijd": now.isoformat(),
            "niveau": str(level),
            "titel": str(title),
            "samenvatting": str(summary),
            "details": str(details or summary),
        }
        if battery_index is not None:
            event["accu_index"] = int(battery_index)
            event["accu_nummer"] = int(battery_index) + 1
        if battery_name:
            event["accu"] = str(battery_name)
        if expected_state:
            event["verwachte_status"] = str(expected_state)
        if attempt is not None:
            event["herstelpoging"] = int(attempt)
        duplicate = self._recent_duplicate(event, now)
        if duplicate is None:
            event["aantal"] = 1
            self.data["events"].insert(0, event)
        else:
            duplicate["eerste_tijd"] = duplicate.get(
                "eerste_tijd", duplicate.get("tijd")
            )
            duplicate["tijd"] = event["tijd"]
            duplicate["aantal"] = int(duplicate.get("aantal", 1)) + 1
            duplicate["samenvatting"] = event["samenvatting"]
            duplicate["details"] = event["details"]
            if attempt is not None:
                duplicate["herstelpoging"] = int(attempt)
            self.data["events"].remove(duplicate)
            self.data["events"].insert(0, duplicate)
            event = duplicate
        self._prune(now)
        self.store.save(self.data)
        self.publish()
        return event

    def clear(self):
        self.data = {"events": []}
        self.store.save(self.data)
        self.publish()
        self.app.log("OpenEMS-gebeurtenishistorie handmatig gewist")

    def _clear_requested(self, entity, attribute, old, new, kwargs):
        self.clear()

    def publish(self):
        events = list(self.data.get("events", []))
        counts = {}
        for event in events:
            name = event.get("accu")
            if not name:
                continue
            bucket = counts.setdefault(
                name,
                {
                    "fouten": 0,
                    "waarschuwingen": 0,
                    "hersteld": 0,
                    "informatie": 0,
                },
            )
            key = {
                "error": "fouten",
                "warning": "waarschuwingen",
                "recovered": "hersteld",
                "info": "informatie",
            }.get(event.get("niveau"), "informatie")
            bucket[key] += max(1, int(event.get("aantal", 1)))
        latest = events[0] if events else None
        attributes = {
            "friendly_name": "OpenEMS runtime-gebeurtenissen",
            "icon": "mdi:timeline-alert-outline",
            "bewaartermijn_dagen": self.RETENTION_DAYS,
            "aantal": len(events),
            "aantal_meldingen": sum(
                max(1, int(event.get("aantal", 1)))
                for event in events
            ),
            "aantallen_per_accu": counts,
            "bijgewerkt": datetime.now(self.timezone).isoformat(),
        }
        published = self._events_for_sensor(events, attributes)
        attributes.update({
            "gebeurtenissen": published,
            "aantal_gepubliceerd": len(published),
            "historie_ingekort": len(published) < len(events),
        })
        self.app.set_state(
            self.SENSOR,
            state=(latest.get("titel") if latest else "Geen gebeurtenissen"),
            attributes=attributes,
            replace=True,
        )

    def _events_for_sensor(self, events, base_attributes):
        """Publiceer de nieuwste details zonder de Recorder-grens te raken."""
        published = []
        for event in events[:self.MAX_PUBLISHED_EVENTS]:
            compact = dict(event)
            for key, limit in (
                ("titel", 160),
                ("samenvatting", 400),
                ("details", 1200),
                ("verwachte_status", 120),
            ):
                value = compact.get(key)
                if isinstance(value, str) and len(value) > limit:
                    compact[key] = value[: limit - 1] + "…"
            candidate = dict(base_attributes)
            candidate.update({
                "gebeurtenissen": published + [compact],
                "aantal_gepubliceerd": len(published) + 1,
                "historie_ingekort": True,
            })
            size = len(json.dumps(
                candidate,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8"))
            if size > self.MAX_PUBLISHED_ATTRIBUTE_BYTES:
                break
            published.append(compact)
        return published

    def _recent_duplicate(self, event, now):
        signature = (
            event.get("niveau"),
            event.get("titel"),
            event.get("accu_index"),
            event.get("verwachte_status"),
        )
        cutoff = now - timedelta(minutes=self.DEDUPLICATION_MINUTES)
        for existing in self.data.get("events", []):
            existing_signature = (
                existing.get("niveau"),
                existing.get("titel"),
                existing.get("accu_index"),
                existing.get("verwachte_status"),
            )
            if existing_signature != signature:
                continue
            try:
                timestamp = datetime.fromisoformat(
                    str(existing.get("tijd"))
                )
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=self.timezone)
            except (TypeError, ValueError):
                return None
            return existing if timestamp >= cutoff else None
        return None

    def _prune(self, now=None):
        now = now or datetime.now(self.timezone)
        cutoff = now - timedelta(days=self.RETENTION_DAYS)
        retained = []
        for event in self.data.get("events", []):
            try:
                timestamp = datetime.fromisoformat(str(event.get("tijd")))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=self.timezone)
            except (TypeError, ValueError):
                continue
            if timestamp >= cutoff:
                retained.append(event)
            if len(retained) >= self.MAX_EVENTS:
                break
        self.data["events"] = retained
