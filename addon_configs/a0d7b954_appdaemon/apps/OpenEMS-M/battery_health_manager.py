"""Centrale tijdelijke uitsluiting per accu, zonder proefvermogen."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from persistence import JsonStore


class BatteryHealthManager:
    STATUS_SENSOR = "sensor.openems_accugezondheid"
    COOLDOWN_SECONDS = 15 * 60

    def __init__(
        self, app, batteries, timezone, safety_service=None, data_file=None,
        disabled_provider=None,
    ):
        self.app = app
        self.batteries = batteries
        self.timezone = timezone
        self.safety_service = safety_service
        self.disabled_provider = disabled_provider
        self.data_file = data_file or (
            Path(__file__).resolve().parent.parent
            / "OpenEMS-M-data"
            / "accugezondheid.json"
        )
        self.store = JsonStore(
            self.data_file,
            app=app,
            timezone=timezone,
            label="Accugezondheid",
        )
        loaded = self.store.load(self._empty_data)
        self.data = loaded if isinstance(loaded, dict) else self._empty_data()
        self._ensure_batteries()
        migrated = self._migrate_legacy_export_quarantine()
        if self._migrate_long_quarantine():
            migrated = True
        if self._migrate_probation_status():
            migrated = True
        if self._reset_startup_quarantines():
            migrated = True
        if migrated:
            self.store.save(self.data)
        self.publish()

    def quarantine(self, index, reason, attempts=3):
        if not 0 <= int(index) < len(self.batteries):
            return False
        now = datetime.now(self.timezone)
        item = self._item(index)
        item.update({
            "status": "quarantined",
            "since": now.isoformat(),
            "until": (now + timedelta(
                seconds=self.COOLDOWN_SECONDS
            )).isoformat(),
            "reason": str(reason or "Herstelpogingen mislukt"),
            "attempts": int(attempts),
        })
        self._save_publish()
        return True

    def tick(self, kwargs=None):
        changed = False
        now = datetime.now(self.timezone)
        for index in range(len(self.batteries)):
            item = self._item(index)
            if item.get("status") != "quarantined":
                continue
            until = self._datetime(item.get("until"))
            if until is None or now < until:
                continue
            safe, reason = self._hardware_safe(index)
            if not safe:
                if item.get("status_detail") != reason:
                    item["status_detail"] = reason
                    changed = True
                continue
            item.update({
                "status": "healthy",
                "since": None,
                "reason": "",
                "attempts": 0,
                "status_detail": (
                    "Wachttijd verstreken; volledig beschikbaar voor de "
                    "volgende echte opdracht"
                ),
                "until": None,
            })
            changed = True
        if changed:
            self.store.save(self.data)
        self.publish(now)

    def disabled_indexes(self):
        """Accu's die de gebruiker bewust heeft uitgeschakeld."""
        if not callable(self.disabled_provider):
            return set()
        try:
            return {int(index) for index in self.disabled_provider()}
        except (TypeError, ValueError):
            return set()

    def is_disabled(self, index):
        return int(index) in self.disabled_indexes()

    def is_available(self, index):
        if self.is_disabled(index):
            return False
        item = self._item(index)
        return item.get("status") != "quarantined"

    def power_limit(self, index):
        if not self.is_available(index):
            return 0.0
        return None

    def filter_powers(self, powers):
        result = []
        for index, power in enumerate(powers):
            limit = self.power_limit(index)
            value = max(0.0, float(power))
            result.append(value if limit is None else min(value, limit))
        return result

    def release_manual_selector_quarantines(self):
        """Geef veilige, uitsluitend door de selector geblokkeerde accu's vrij.

        Een expliciete handmatige opdracht mag niet door een oude softwarematige
        selectieverificatie tot 0 W worden teruggebracht. Echte hardware- of
        BMS-onveiligheid blijft altijd geblokkeerd.
        """
        released = []
        for index in range(len(self.batteries)):
            item = self._item(index)
            reason = str(item.get("reason") or "")
            if (
                item.get("status") != "quarantined"
                or "native anti-feedselectie" not in reason.lower()
            ):
                continue
            safe, safe_reason = self._hardware_safe(index)
            if not safe:
                item["status_detail"] = (
                    "Handmatige vrijgave geweigerd: "
                    f"{safe_reason or 'hardwarebeveiliging actief'}"
                )
                continue
            item.update({
                "status": "healthy",
                "since": None,
                "until": None,
                "reason": "",
                "status_detail": (
                    "Vrijgegeven voor expliciete handmatige bediening"
                ),
                "attempts": 0,
            })
            released.append(index)
        if released:
            self._save_publish()
        return released

    def filter_indexes(self, indexes):
        return [
            int(index) for index in indexes
            if self.is_available(int(index))
        ]

    def status(self, index):
        return dict(self._item(index))

    def publish(self, now=None):
        moment = now or datetime.now(self.timezone)
        batteries = []
        disabled = self.disabled_indexes()
        for index, battery in enumerate(self.batteries):
            item = self._item(index)
            until = self._datetime(item.get("until"))
            remaining = max(
                0, int((until - moment).total_seconds())
            ) if until is not None else 0
            if index in disabled:
                batteries.append({
                    "index": index,
                    "naam": battery.get("name", f"Accu {index + 1}"),
                    "status": "disabled",
                    "reden": "Handmatig uitgeschakeld in de instellingen",
                    "status_detail": (
                        "OpenEMS bestuurt deze accu niet; gebruikersmodus "
                        "staat op Handmatig"
                    ),
                    "opnieuw_proberen_op": None,
                    "resterend_seconden": 0,
                })
                continue
            batteries.append({
                "index": index,
                "naam": battery.get("name", f"Accu {index + 1}"),
                "status": item.get("status", "healthy"),
                "reden": item.get("reason", ""),
                "status_detail": item.get("status_detail", ""),
                "opnieuw_proberen_op": item.get("until"),
                "resterend_seconden": remaining,
            })
        unavailable = [
            item for item in batteries
            if item["status"] not in ("healthy", "disabled")
        ]
        switched_off = [
            item for item in batteries if item["status"] == "disabled"
        ]
        parts = []
        if switched_off:
            parts.append(f"{len(switched_off)} accu uitgeschakeld")
        if unavailable:
            parts.append(f"{len(unavailable)} accu tijdelijk beperkt")
        self.app.set_state(
            self.STATUS_SENSOR,
            state=(
                "alle accu's beschikbaar" if not parts else " · ".join(parts)
            ),
            attributes={
                "friendly_name": "OpenEMS accugezondheid",
                "icon": "mdi:battery-heart-variant",
                "accus": batteries,
                "cooldown_uren": self.COOLDOWN_SECONDS / 3600,
                "laatst_bijgewerkt": moment.isoformat(),
            },
            replace=True,
        )

    def _hardware_safe(self, index):
        checker = getattr(
            self.safety_service, "hardware_assessment", None
        )
        if not callable(checker):
            return True, ""
        assessment = checker(index, "stopped")
        return bool(assessment.safe), str(assessment.reason or "")

    def _item(self, index):
        self._ensure_batteries()
        return self.data["batteries"][int(index)]

    def _ensure_batteries(self):
        items = self.data.setdefault("batteries", [])
        while len(items) < len(self.batteries):
            items.append(self._healthy_item())
        del items[len(self.batteries):]

    def _migrate_legacy_export_quarantine(self):
        """Wis éénmalig alleen de oude, te strenge exportvermogensfout."""
        try:
            version = int(self.data.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        if version >= 2:
            return False
        for index in range(len(self.batteries)):
            item = self._item(index)
            reason = str(item.get("reason") or "")
            if (
                item.get("status") == "quarantined"
                and self._legacy_export_delivery_only(reason)
            ):
                safe, _safe_reason = self._hardware_safe(index)
                if safe:
                    item.update({
                        "status": "healthy",
                        "since": None,
                        "until": None,
                        "reason": "",
                        "status_detail": (
                            "Eenmalig vrijgegeven na verbeterde "
                            "exportvermogenscontrole"
                        ),
                        "attempts": 0,
                    })
        self.data["version"] = 2
        return True

    def _migrate_long_quarantine(self):
        """Kort bestaande drie-uursblokkades eenmalig in tot vijftien minuten."""
        try:
            version = int(self.data.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        if version >= 3:
            return False
        now = datetime.now(self.timezone)
        latest = now + timedelta(seconds=self.COOLDOWN_SECONDS)
        for index in range(len(self.batteries)):
            item = self._item(index)
            if item.get("status") != "quarantined":
                continue
            until = self._datetime(item.get("until"))
            if until is None or until > latest:
                item["until"] = latest.isoformat()
                item["status_detail"] = (
                    "Oude drie-uursblokkade ingekort tot maximaal 15 minuten"
                )
        self.data["version"] = 3
        return True

    def _migrate_probation_status(self):
        """Verwijder oude beperkte herteststatussen eenmalig."""
        try:
            version = int(self.data.get("version", 1))
        except (TypeError, ValueError):
            version = 1
        if version >= 4:
            return False
        for item in self.data.get("batteries", []):
            if item.get("status") != "probation":
                continue
            item.update({
                "status": "healthy",
                "since": None,
                "until": None,
                "reason": "",
                "status_detail": (
                    "Oude beperkte hertest verwijderd; volledig beschikbaar"
                ),
                "attempts": 0,
            })
        self.data["version"] = 4
        return True

    def _reset_startup_quarantines(self):
        """Begin iedere runtime zonder opgeslagen softwarematige wachttimers."""
        changed = False
        for item in self.data.get("batteries", []):
            if item.get("status") != "quarantined":
                continue
            item.update({
                "status": "healthy",
                "since": None,
                "until": None,
                "reason": "",
                "status_detail": (
                    "Opstart: opgeslagen softwarematige wachttijd gewist; "
                    "actuele BMS/SOC-controle blijft actief"
                ),
                "attempts": 0,
            })
            changed = True
        return changed

    @staticmethod
    def _legacy_export_delivery_only(reason):
        value = str(reason or "").strip().lower()
        if not (
            "werkelijk ontlaadvermogen=" in value
            and "verwacht" in value
        ):
            return False
        hard_failures = (
            "tegengesteld", "rs485=", "force mode=", "ingesteld ",
            "alarm", "fault", "onbeschikbaar",
        )
        if any(token in value for token in hard_failures):
            return False
        match = re.search(
            r"werkelijk ontlaadvermogen=([0-9]+(?:[.,][0-9]+)?)\s*w"
            r".*?verwacht\s+([0-9]+(?:[.,][0-9]+)?)\s*w",
            value,
        )
        if match is None:
            return False
        actual = float(match.group(1).replace(",", "."))
        expected = float(match.group(2).replace(",", "."))
        return expected > 0 and actual >= max(100.0, expected * 0.50)

    def _save_publish(self):
        self.store.save(self.data)
        self.publish()

    def _datetime(self, value):
        if not value:
            return None
        try:
            result = datetime.fromisoformat(str(value))
            if result.tzinfo is None:
                result = result.replace(tzinfo=self.timezone)
            return result.astimezone(self.timezone)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _healthy_item():
        return {
            "status": "healthy",
            "since": None,
            "until": None,
            "reason": "",
            "status_detail": "",
            "attempts": 0,
        }

    @classmethod
    def _empty_data(cls):
        return {"version": 4, "batteries": []}
