"""Bewaakt netspanning en backupmodus zonder de backupfunctie te schrijven."""

from __future__ import annotations

from datetime import datetime
import math
import time


class GridStatusMonitor:
    STATUS_SENSOR = "sensor.openems_netstatus"
    GRID_PRESENT_SENSOR = "binary_sensor.openems_net_aanwezig"
    OUTAGE_SENSOR = "binary_sensor.openems_netuitval"
    DURATION_SENSOR = "sensor.openems_netuitval_duur"

    MIN_HEALTHY_VOLTAGE = 207.0
    MAX_HEALTHY_VOLTAGE = 253.0
    OUTAGE_VOLTAGE = 50.0
    DETECTION_SECONDS = 2.0
    NORMAL_RECOVERY_SECONDS = 10.0
    OUTAGE_RECOVERY_SECONDS = 30.0

    def __init__(self, app, batteries, timezone, event_log=None, clock=None):
        self.app = app
        self.batteries = batteries
        self.timezone = timezone
        self.event_log = event_log
        self.clock = clock or time.monotonic
        self.status = "unknown"
        self._candidate = None
        self._candidate_since = None
        self._battery_outage_evidence = [None] * len(batteries)
        self._battery_evidence_candidates = [None] * len(batteries)
        self._battery_evidence_since = [None] * len(batteries)
        self._outage_started_at = None
        self._outage_started_clock = None
        self._last_duration_state = None
        self.last_snapshot = []
        self.publish()

    def tick(self, kwargs=None):
        snapshot = self._snapshot()
        self.last_snapshot = snapshot
        self._record_battery_transitions(snapshot)
        candidate = self._candidate_status(snapshot)
        now = self.clock()
        if candidate != self._candidate:
            self._candidate = candidate
            self._candidate_since = now
        required = self._required_stability(candidate)
        if (
            candidate != self.status
            and self._candidate_since is not None
            and now - self._candidate_since >= required
        ):
            self._set_status(candidate, snapshot)
        self.publish()

    def allows_charging(self):
        return self.status in {"normal", "high"}

    def allows_export(self):
        return self.status == "normal"

    def planning_paused(self):
        return self.status == "off_grid"

    def allows_controller_action(self, action, source="auto"):
        action = str(action).lower()
        if self.status == "off_grid":
            return False
        if self.status in {"low", "unknown"} and "laden" in action:
            return False
        if self.status in {"high", "low", "unknown"} and "ontladen" in action:
            return False
        return True

    def status_reason(self):
        return {
            "normal": "Netspanning binnen 207–253 V",
            "high": "Netspanning boven 253 V; terugleveren geblokkeerd",
            "low": "Netspanning onder 207 V; laden geblokkeerd",
            "off_grid": "Backup actief · Planning gepauzeerd",
            "unknown": "Netstatus kon niet betrouwbaar worden vastgesteld",
        }.get(self.status, "Netstatus onbekend")

    def _snapshot(self):
        result = []
        for index, battery in enumerate(self.batteries):
            voltage = self._number(battery.get("ac_voltage"))
            inverter = self._text(battery.get("inverter_state"))
            backup = "backup" in inverter
            voltage_outage = (
                voltage is not None and voltage <= self.OUTAGE_VOLTAGE
            )
            result.append({
                "index": index,
                "name": battery.get("name", f"Accu {index + 1}"),
                "voltage": voltage,
                "inverter_state": inverter or "onbekend",
                "backup_mode": backup,
                "voltage_outage": voltage_outage,
                "outage_evidence": voltage_outage or backup,
            })
        return result

    def _candidate_status(self, snapshot):
        quorum = max(1, math.floor(len(snapshot) / 2) + 1)
        voltage_outage = sum(item["voltage_outage"] for item in snapshot)
        backup = sum(item["backup_mode"] for item in snapshot)
        if voltage_outage >= quorum or backup >= quorum:
            return "off_grid"
        valid = [
            item["voltage"] for item in snapshot
            if item["voltage"] is not None
        ]
        if len(valid) < quorum:
            return "unknown"
        if sum(value > self.MAX_HEALTHY_VOLTAGE for value in valid) >= quorum:
            return "high"
        if sum(
            self.OUTAGE_VOLTAGE < value < self.MIN_HEALTHY_VOLTAGE
            for value in valid
        ) >= quorum:
            return "low"
        if sum(
            self.MIN_HEALTHY_VOLTAGE <= value <= self.MAX_HEALTHY_VOLTAGE
            for value in valid
        ) >= quorum and backup < quorum:
            return "normal"
        return "unknown"

    def _required_stability(self, candidate):
        if self.status == "off_grid" and candidate == "normal":
            return self.OUTAGE_RECOVERY_SECONDS
        if self.status == "off_grid":
            return float("inf")
        if candidate == "normal":
            return self.NORMAL_RECOVERY_SECONDS
        return self.DETECTION_SECONDS

    def _set_status(self, status, snapshot):
        previous = self.status
        self.status = status
        if status == "off_grid" and previous != "off_grid":
            self._outage_started_at = datetime.now(self.timezone)
            self._outage_started_clock = self.clock()
            self._last_duration_state = None
            controller = getattr(self.app, "controller", None)
            suspend = getattr(controller, "suspend_for_grid_outage", None)
            if callable(suspend):
                suspend()
            scheduler = getattr(self.app, "scheduler", None)
            if scheduler is not None:
                scheduler.mark_external_stop(
                    "Netuitval; planning gepauzeerd"
                )
            self._record_system_event(
                "warning",
                "Netuitval bevestigd",
                self._outage_summary(snapshot) + " · Planning gepauzeerd",
            )
        elif previous == "off_grid" and status == "normal":
            duration = self.duration_seconds()
            self._record_system_event(
                "recovered",
                "Net hersteld",
                f"Net 30 seconden stabiel binnen 207–253 V · "
                f"uitvalduur {self.format_duration(duration)}",
            )
            controller = getattr(self.app, "controller", None)
            resume = getattr(controller, "resume_after_grid_outage", None)
            if callable(resume):
                resume()
            self._outage_started_at = None
            self._outage_started_clock = None
            self._last_duration_state = None
        elif previous != status:
            level = "recovered" if status == "normal" else "warning"
            title = {
                "high": "Hoge netspanning",
                "low": "Lage netspanning",
                "unknown": "Netstatus onbekend",
                "normal": "Netspanning hersteld",
            }.get(status, "Netstatus gewijzigd")
            self._record_system_event(level, title, self.status_reason())
        if previous != status:
            callback = getattr(self.app, "grid_status_changed", None)
            if callable(callback):
                callback(previous, status)
            elif previous == "off_grid" and status == "normal":
                legacy_callback = getattr(
                    self.app, "grid_status_restored", None
                )
                if callable(legacy_callback):
                    legacy_callback()

    def _record_battery_transitions(self, snapshot):
        now = self.clock()
        for item in snapshot:
            index = item["index"]
            evidence = bool(item["outage_evidence"])
            if self._battery_evidence_candidates[index] != evidence:
                self._battery_evidence_candidates[index] = evidence
                self._battery_evidence_since[index] = now
            previous = self._battery_outage_evidence[index]
            if previous is None and not evidence:
                self._battery_outage_evidence[index] = False
                continue
            since = self._battery_evidence_since[index]
            if (
                previous == evidence
                or since is None
                or now - since < self.DETECTION_SECONDS
            ):
                continue
            self._battery_outage_evidence[index] = evidence
            voltage = item["voltage"]
            voltage_text = (
                f"{voltage:.1f} V" if voltage is not None else "onbekend"
            )
            if evidence:
                reasons = []
                if item["voltage_outage"]:
                    reasons.append(f"AC-spanning {voltage_text}")
                if item["backup_mode"]:
                    reasons.append("Backup Mode")
                self._record_battery_event(
                    "warning", "Netuitval gemeld",
                    item, " · ".join(reasons),
                )
            elif voltage is not None:
                self._record_battery_event(
                    "recovered", "Netsignaal hersteld",
                    item,
                    f"AC-spanning {voltage_text} · "
                    f"omvormer {item['inverter_state']}",
                )

    def _record_battery_event(self, level, title, item, summary):
        if self.event_log is None:
            return
        self.event_log.record(
            level, title, summary,
            battery_index=item["index"],
            battery_name=item["name"],
            expected_state=("off_grid" if level == "warning" else "on_grid"),
        )

    def _record_system_event(self, level, title, summary):
        if self.event_log is not None:
            self.event_log.record(level, title, summary)
        self.app.log(f"[Netstatus] {title}: {summary}")

    def _outage_summary(self, snapshot):
        voltage = [
            item["name"] for item in snapshot if item["voltage_outage"]
        ]
        backup = [item["name"] for item in snapshot if item["backup_mode"]]
        parts = []
        if voltage:
            parts.append("AC vrijwel 0 V: " + ", ".join(voltage))
        if backup:
            parts.append("Backup Mode: " + ", ".join(backup))
        return " · ".join(parts) or "Netuitval via accutelemetrie"

    def duration_seconds(self):
        if self._outage_started_clock is None:
            return 0
        return max(0, int(self.clock() - self._outage_started_clock))

    @staticmethod
    def format_duration(seconds):
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds} seconde" if seconds == 1 else f"{seconds} seconden"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minuut" if minutes == 1 else f"{minutes} minuten"
        hours, minutes = divmod(minutes, 60)
        hour_text = f"{hours} uur"
        if minutes == 0:
            return hour_text
        minute_text = f"{minutes} minuut" if minutes == 1 else f"{minutes} minuten"
        return f"{hour_text} en {minute_text}"

    def publish(self):
        duration = self.duration_seconds()
        duration_state = duration if duration < 60 else (duration // 60) * 60
        if duration_state != self._last_duration_state:
            self._last_duration_state = duration_state
            attributes = {
                "friendly_name": "OpenEMS netuitvalduur",
                "icon": "mdi:timer-alert-outline",
                "unit_of_measurement": "s",
                "device_class": "duration",
                "weergave": self.format_duration(duration),
            }
            if self._outage_started_at is not None:
                attributes["gestart"] = self._outage_started_at.isoformat()
            self.app.set_state(
                self.DURATION_SENSOR,
                state=str(duration),
                attributes=attributes,
                replace=True,
            )
        state_names = {
            "normal": "On Grid",
            "high": "Hoge netspanning",
            "low": "Lage netspanning",
            "off_grid": "Off Grid",
            "unknown": "Netstatus onbekend",
        }
        attributes = {
            "friendly_name": "OpenEMS netstatus",
            "icon": (
                "mdi:transmission-tower-off" if self.status == "off_grid"
                else "mdi:transmission-tower"
            ),
            "code": self.status,
            "reden": self.status_reason(),
            "laden_toegestaan": self.allows_charging(),
            "terugleveren_toegestaan": self.allows_export(),
            "planning_gepauzeerd": self.planning_paused(),
            "accus": [
                {
                    "naam": item["name"],
                    "ac_spanning_v": item["voltage"],
                    "omvormerstatus": item["inverter_state"],
                    "netuitval_signaal": item["outage_evidence"],
                }
                for item in self.last_snapshot
            ],
        }
        self.app.set_state(
            self.STATUS_SENSOR,
            state=state_names.get(self.status, "Netstatus onbekend"),
            attributes=attributes,
            replace=True,
        )
        binary_state = "off" if self.status == "off_grid" else (
            "unknown" if self.status == "unknown" else "on"
        )
        self.app.set_state(
            self.GRID_PRESENT_SENSOR,
            state=binary_state,
            attributes={
                "friendly_name": "OpenEMS net aanwezig",
                "device_class": "connectivity",
            },
            replace=True,
        )
        self.app.set_state(
            self.OUTAGE_SENSOR,
            state="on" if self.status == "off_grid" else "off",
            attributes={
                "friendly_name": "OpenEMS netuitval",
                "device_class": "problem",
                "duur_seconden": duration,
            },
            replace=True,
        )

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

    def _text(self, entity_id):
        if not entity_id:
            return ""
        value = self.app.get_state(entity_id)
        if value in (None, "", "unknown", "unavailable"):
            return ""
        return str(value).strip().lower()
