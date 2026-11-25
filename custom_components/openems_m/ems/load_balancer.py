# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Load balancer: houdt laden onder de grens van de hoofdaansluiting.

Meet elke paar seconden de netimport (P1, met de CT-spoel als reserve) en
bepaalt een maximaal totaal laadvermogen. De controller past dat toe op iedere
laadopdracht. Zodra er weer ruimte is, wordt stapsgewijs hersteld.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .ems_config import (
    LOAD_BALANCER_CT_DEFAULT,
    LOAD_BALANCER_FALLBACK_CHARGE_W,
    LOAD_BALANCER_P1_DEFAULT,
    LOAD_BALANCER_RECOVER_AFTER_S,
    LOAD_BALANCER_RECOVER_MARGIN_W,
    LB_DEFAULT_MAX_EXPORT_W,
    LB_DEFAULT_MAX_IMPORT_W,
    LOAD_BALANCER_RECOVER_STEP_W,
    LOAD_BALANCER_STALE_AFTER_S,
    LOAD_BALANCER_STATUS_SENSOR,
    GRID_EXPORT_SAFETY_MARGIN_W,
    LOAD_BALANCER_EXPORT_RECOVER_MARGIN_W,
)
from .ems_config import GRID_POWER_ENTITY_HELPER


class LoadBalancer:
    def __init__(self, app, settings, controller, batteries, clock=None):
        self.app = app
        self.settings = settings
        self.controller = controller
        self.batteries = batteries
        self.clock = clock or time.monotonic
        self.cap_w = None
        self.discharge_cap_w = None
        self.export_w = None
        self.export_mode = "inactief"
        self._export_recover_since = None
        self.source = ""
        self.import_w = None
        self._export_basis_w = None
        self.p1_w = None
        self.ct_w = None
        self.sensor_mode = ""
        self.mode = "inactief"
        self.p1_interval_s = None
        self._p1_last_updated = None
        self._recover_since = None
        self._fallback_logged = False
        self._last_log = None

    # ---- aanroep door de controller ------------------------------------
    def charge_cap(self, source):
        """Maximaal totaal laadvermogen voor deze bron, of None."""
        kind = "handmatig" if source == "manual" else "laden"
        if not self.settings.load_balancer_enabled(kind):
            return None
        return self.cap_w

    def discharge_cap(self, source):
        """Maximaal totaal ontlaadvermogen (export bewaakt), of None."""
        if source == "export":
            return None  # het terugleverblok bewaakt zijn eigen grens
        if not self.settings.load_balancer_enabled(self._discharge_kind(source)):
            return None
        return self.discharge_cap_w

    @staticmethod
    def _discharge_kind(source):
        return "handmatig" if source == "manual" else "terugleveren"

    def _setting(self, name, default):
        getter = getattr(self.settings, name, None)
        if not callable(getter):
            return default
        try:
            return getter()
        except Exception:
            return default

    @property
    def export_limit_w(self):
        return (
            self._setting("max_export_w", LB_DEFAULT_MAX_EXPORT_W)
            - GRID_EXPORT_SAFETY_MARGIN_W
        )

    @property
    def limit_w(self):
        return self._setting("max_import_w", LB_DEFAULT_MAX_IMPORT_W)

    # ---- periodieke controle ---------------------------------------------
    def tick(self, kwargs=None):
        try:
            self._tick()
        except Exception as error:  # nooit de app laten crashen
            self.app.log(
                f"[Load balancer] Onverwachte fout: {error}", level="WARNING"
            )

    def _tick(self):
        controller = self.controller
        charging = (
            controller.desired_state == "charging"
            and sum(controller.desired_powers) > 0
        )
        reading = self._read_import()
        previous_cap = self.cap_w
        if reading is None:
            self.import_w = None
            self.source = "geen"
            if charging and self._enabled_for_current():
                self.cap_w = (
                    LOAD_BALANCER_FALLBACK_CHARGE_W
                    if previous_cap is None
                    else min(previous_cap, LOAD_BALANCER_FALLBACK_CHARGE_W)
                )
                self.mode = "noodgrens"
                if not self._fallback_logged:
                    self.app.log(
                        "[Load balancer] Geen netmeting beschikbaar; laden "
                        f"begrensd op {LOAD_BALANCER_FALLBACK_CHARGE_W} W",
                        level="WARNING",
                    )
                    self._fallback_logged = True
        else:
            self._fallback_logged = False
            self.import_w, self.source = reading
            if not charging or not self._enabled_for_current():
                if self.cap_w is not None:
                    self.cap_w = None
                self._recover_since = None
                self.mode = "inactief" if not charging else "uit"
            else:
                self._balance(previous_cap)
        if self.cap_w != previous_cap:
            self._log_change(previous_cap)
            self._reapply()
        self._export_tick(reading)
        self.publish()

    # ---- exportbewaking (ontladen) -----------------------------------------
    def _export_tick(self, reading):
        controller = self.controller
        previous = self.discharge_cap_w
        discharging = (
            controller.desired_state == "forced_discharge"
            and sum(controller.desired_powers) > 0
        )
        source = controller.desired_source
        enabled = (
            discharging
            and source != "export"
            and self.settings.load_balancer_enabled(
                self._discharge_kind(source)
            )
        )
        basis = (
            self._export_basis_w
            if self._export_basis_w is not None
            else (None if reading is None else reading[0])
        )
        if reading is None or not enabled:
            self.export_w = None if reading is None else max(0.0, -basis)
            self.discharge_cap_w = None
            self._export_recover_since = None
            self.export_mode = "inactief" if not discharging else "uit"
            if reading is None and enabled:
                self.export_mode = "geen netmeting"
        else:
            self.export_w = max(0.0, -basis)
            self._export_balance(previous)
        if self.discharge_cap_w != previous:
            self._log_export_change()
            reapply = getattr(self.controller, "reapply_discharge", None)
            if callable(reapply):
                reapply()

    def _export_balance(self, previous):
        now = self.clock()
        limit = self.export_limit_w
        actual = self._actual_discharge_w()
        if self.export_w > limit:
            target = max(0.0, actual - (self.export_w - limit))
            self.discharge_cap_w = (
                target if previous is None else min(previous, target)
            )
            self._export_recover_since = None
            self.export_mode = "begrenst"
            return
        if previous is None:
            self.export_mode = "bewaakt"
            return
        self.export_mode = "begrenst"
        if self.export_w < limit - LOAD_BALANCER_EXPORT_RECOVER_MARGIN_W:
            if self._export_recover_since is None:
                self._export_recover_since = now
            if now - self._export_recover_since >= LOAD_BALANCER_RECOVER_AFTER_S:
                self.discharge_cap_w = previous + LOAD_BALANCER_RECOVER_STEP_W
                self._export_recover_since = now
                requested = float(
                    getattr(self.controller, "requested_discharge_total", 0.0)
                )
                if self.discharge_cap_w >= requested:
                    self.discharge_cap_w = None
                    self.export_mode = "bewaakt"
        else:
            self._export_recover_since = None

    def _actual_discharge_w(self):
        total = 0.0
        known = False
        disabled = getattr(self.controller, "battery_disabled", None)
        for index, battery in enumerate(self.batteries):
            if callable(disabled) and disabled(index):
                # Uitgeschakelde accu is niet regelbaar: zijn vermogen hoort
                # bij het overige verbruik, niet bij de te begrenzen accu's.
                continue
            value = self._number(battery.get("actual_power"))
            if value is None:
                continue
            known = True
            total += max(0.0, value)  # positief = ontladen
        if not known:
            return float(sum(self.controller.desired_powers))
        return total

    def _log_export_change(self):
        if self.discharge_cap_w is None:
            self.app.log("[Load balancer] Ontladen weer onbeperkt")
        else:
            self.app.log(
                f"[Load balancer] Ontladen begrensd op "
                f"{self.discharge_cap_w:.0f} W (export "
                f"{self.export_w if self.export_w is not None else '?'} W, "
                f"grens {self.export_limit_w} W)",
                level="WARNING",
            )

    def _enabled_for_current(self):
        source = self.controller.desired_source
        kind = "handmatig" if source == "manual" else "laden"
        return self.settings.load_balancer_enabled(kind)

    def _balance(self, previous_cap):
        now = self.clock()
        actual = self._actual_charge_w()
        limit = self.limit_w
        if self.import_w > limit:
            target = max(0.0, actual + (limit - self.import_w))
            self.cap_w = (
                target if previous_cap is None else min(previous_cap, target)
            )
            self._recover_since = None
            self.mode = "begrenst"
            return
        if previous_cap is None:
            self.mode = "bewaakt"
            return
        self.mode = "begrenst"
        if self.import_w < self.limit_w - LOAD_BALANCER_RECOVER_MARGIN_W:
            if self._recover_since is None:
                self._recover_since = now
            if now - self._recover_since >= LOAD_BALANCER_RECOVER_AFTER_S:
                self.cap_w = previous_cap + LOAD_BALANCER_RECOVER_STEP_W
                self._recover_since = now
                if self.cap_w >= self._requested_total():
                    self.cap_w = None
                    self.mode = "bewaakt"
        else:
            self._recover_since = None

    def _requested_total(self):
        return float(getattr(self.controller, "requested_charge_total", 0.0))

    def _actual_charge_w(self):
        total = 0.0
        known = False
        disabled = getattr(self.controller, "battery_disabled", None)
        for index, battery in enumerate(self.batteries):
            if callable(disabled) and disabled(index):
                # Uitgeschakelde accu is niet regelbaar: zijn vermogen hoort
                # bij het overige verbruik, niet bij de te begrenzen accu's.
                continue
            value = self._number(battery.get("actual_power"))
            if value is None:
                continue
            known = True
            total += max(0.0, -value)  # negatief = laden
        if not known:
            return float(sum(self.controller.desired_powers))
        return total

    def _reapply(self):
        reapply = getattr(self.controller, "reapply_charge", None)
        if callable(reapply):
            reapply()

    def _log_change(self, previous):
        if self.cap_w is None:
            self.app.log("[Load balancer] Laden weer onbeperkt")
        else:
            self.app.log(
                f"[Load balancer] Laden begrensd op {self.cap_w:.0f} W "
                f"(netimport {self.import_w if self.import_w is not None else '?'} "
                f"W, bron {self.source})",
                level="WARNING" if previous is None else "INFO",
            )

    # ---- meting -------------------------------------------------------------
    def _read_p1(self):
        return self._fresh_number(self._p1_entity(), track=True)

    def _read_ct(self):
        value = self._fresh_number(self._ct_entity())
        if value is None:
            return None
        sign = self.settings.grid_import_sign()
        return value * sign + self.settings.ct_grid_offset()

    def _read_import(self):
        """Netmeting volgens de gekozen sensoren: (waarde, bron) of None.

        Alleen P1 of alleen CT: die ene sensor. Beide aan: de voorkeur met
        automatische terugval, of (optioneel) beide tegelijk waarbij de
        strengste meting telt: de hoogste voor import en de laagste voor
        export, zodat de eerste grens die geraakt wordt bepaalt.
        """
        p1_on, ct_on = self._setting("lb_sensors", (True, True))
        both = bool(self._setting("lb_both", False)) and p1_on and ct_on
        preferred = self._setting("lb_preferred", "P1")
        self._export_basis_w = None
        self.p1_w = None
        self.ct_w = None
        if both:
            self.p1_w = self._read_p1()
            self.ct_w = self._read_ct()
        else:
            order = ["P1", "CT"] if preferred != "CT" else ["CT", "P1"]
            for name in order:
                if name == "P1" and p1_on:
                    self.p1_w = self._read_p1()
                    value = self.p1_w
                elif name == "CT" and ct_on:
                    self.ct_w = self._read_ct()
                    value = self.ct_w
                else:
                    continue
                if value is not None:
                    break
        self.sensor_mode = (
            "beide" if both
            else "alleen P1" if not ct_on
            else "alleen CT" if not p1_on
            else f"voorkeur {preferred}"
        )
        if self.p1_w is not None and self.ct_w is not None:
            self._export_basis_w = min(self.p1_w, self.ct_w)
            return max(self.p1_w, self.ct_w), "P1+CT"
        if self.p1_w is not None:
            return self.p1_w, "P1"
        if self.ct_w is not None:
            return self.ct_w, "CT"
        return None

    def _p1_entity(self):
        return (
            self.settings.grid_reference_entity() or LOAD_BALANCER_P1_DEFAULT
        )

    def _ct_entity(self):
        return (
            self.settings._text(GRID_POWER_ENTITY_HELPER)
            or LOAD_BALANCER_CT_DEFAULT
        )

    def _fresh_number(self, entity, track=False):
        if not entity:
            return None
        state = self.app.get_state(entity)
        number = self._to_float(state)
        if number is None:
            return None
        try:
            full = self.app.get_state(entity, attribute="all")
        except TypeError:
            full = None
        stamp = None
        if isinstance(full, dict):
            stamp = full.get("last_reported") or full.get("last_updated")
        moment = self._parse(stamp)
        if moment is not None:
            age = (datetime.now(timezone.utc) - moment).total_seconds()
            if track:
                if (
                    self._p1_last_updated is not None
                    and moment != self._p1_last_updated
                ):
                    delta = (moment - self._p1_last_updated).total_seconds()
                    if 0 < delta < 120:
                        self.p1_interval_s = round(delta, 1)
                self._p1_last_updated = moment
            if age > LOAD_BALANCER_STALE_AFTER_S:
                return None
        return number

    @staticmethod
    def _parse(value):
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @staticmethod
    def _to_float(state):
        if state in (None, "", "unknown", "unavailable"):
            return None
        try:
            return float(state)
        except (TypeError, ValueError):
            return None

    def _number(self, entity):
        if not entity:
            return None
        return self._to_float(self.app.get_state(entity))

    # ---- status ----------------------------------------------------------------
    def publish(self):
        self.app.set_state(
            LOAD_BALANCER_STATUS_SENSOR,
            state=self.mode,
            attributes={
                "friendly_name": "OpenEMS load balancer",
                "icon": "mdi:scale-balance",
                "netimport_w": (
                    round(self.import_w) if self.import_w is not None else None
                ),
                "bron": self.source,
                "sensor_modus": self.sensor_mode,
                "p1_w": (
                    round(self.p1_w) if self.p1_w is not None else None
                ),
                "ct_w": round(self.ct_w) if self.ct_w is not None else None,
                "limiet_w": self.limit_w,
                "max_laadvermogen_w": (
                    round(self.cap_w) if self.cap_w is not None else None
                ),
                "p1_interval_s": self.p1_interval_s,
                "export_modus": self.export_mode,
                "export_w": (
                    round(self.export_w) if self.export_w is not None else None
                ),
                "export_limiet_w": self.export_limit_w,
                "max_ontlaadvermogen_w": (
                    round(self.discharge_cap_w)
                    if self.discharge_cap_w is not None else None
                ),
                "laden_actief": self.settings.load_balancer_enabled("laden"),
                "terugleveren_actief": self.settings.load_balancer_enabled(
                    "terugleveren"
                ),
                "handmatig_actief": self.settings.load_balancer_enabled(
                    "handmatig"
                ),
            },
            replace=True,
        )
