"""Veilige nulpuntkalibratie van een snelle netvermogenssensor."""

from collections import deque
from statistics import median
import math
import time
from datetime import datetime

from ems_config import (
    GRID_MANUAL_OFFSET_HELPER,
    GRID_OFFSET_MANUAL_LIMIT_W,
    GRID_OFFSET_CALIBRATE_BUTTON,
    GRID_OFFSET_CALIBRATION_TIMEOUT_S,
    GRID_OFFSET_LAST_CALIBRATION_HELPER,
    GRID_OFFSET_MODE_HELPER,
    GRID_OFFSET_MAX_ABS_W,
    GRID_OFFSET_MIN_SAMPLES,
    GRID_OFFSET_REFERENCE_LIMIT_W,
    GRID_OFFSET_STABILITY_SECONDS,
    GRID_OFFSET_STABILITY_SPAN_W,
    BATTERIES,
    HOME_OFFSET_BATTERY_IDLE_W,
    HOME_OFFSET_CALIBRATE_BUTTON,
    HOME_OFFSET_LAST_CALIBRATION_HELPER,
    HOME_OFFSET_REFERENCE_LIMIT_W,
    HOME_POWER_OFFSET_HELPER,
)


class OffsetEstimator:
    """Schat een vaste offset uit stabiele metingen rond het nulpunt."""

    def __init__(
        self,
        stability_seconds=30,
        stability_span=80,
        reference_limit=500,
        maximum_offset=300,
        minimum_samples=6,
    ):
        self.stability_seconds = float(stability_seconds)
        self.stability_span = float(stability_span)
        self.reference_limit = float(reference_limit)
        self.maximum_offset = float(maximum_offset)
        self.minimum_samples = int(minimum_samples)
        self.fast_samples = deque(maxlen=240)
        self.offset_samples = deque(maxlen=31)
        self.reliable_target = None
        self.reliable_confidence = 0
        self.reliable_spread = None

    def reset(self):
        self.fast_samples.clear()
        self.offset_samples.clear()
        self.reliable_target = None
        self.reliable_confidence = 0
        self.reliable_spread = None

    def add_fast(self, timestamp, value):
        self.fast_samples.append((float(timestamp), float(value)))
        self._trim(float(timestamp))

    def observe(self, timestamp, reference):
        timestamp = float(timestamp)
        reference = float(reference)
        self._trim(timestamp)
        samples = [
            (moment, value)
            for moment, value in self.fast_samples
            if moment >= timestamp - self.stability_seconds
        ]
        if len(samples) < 4:
            return self._result("wachten_op_meetduur")
        coverage = samples[-1][0] - samples[0][0]
        if coverage < self.stability_seconds * 0.65:
            return self._result("wachten_op_meetduur")
        values = [value for _, value in samples]
        if max(values) - min(values) > self.stability_span:
            return self._result("snelle_sensor_niet_stabiel")
        if abs(reference) > self.reference_limit:
            return self._result("referentie_te_ver_van_nulpunt")

        candidate = reference - median(values)
        if abs(candidate) > self.maximum_offset:
            return self._result("verschil_buiten_veilige_grens")
        self.offset_samples.append(candidate)
        if len(self.offset_samples) < self.minimum_samples:
            return self._result("meer_geldige_metingen_nodig")

        target = median(self.offset_samples)
        deviations = [abs(value - target) for value in self.offset_samples]
        spread = median(deviations)
        if spread > self.stability_span / 2:
            return self._result("offsetmetingen_niet_eenduidig")
        confidence = min(
            100,
            round(len(self.offset_samples) / 15 * 100),
        )
        self.reliable_target = target
        self.reliable_confidence = confidence
        self.reliable_spread = spread
        return self._result(
            "betrouwbaar",
            target=target,
        )

    def _trim(self, timestamp):
        oldest = timestamp - max(120.0, self.stability_seconds * 2)
        while self.fast_samples and self.fast_samples[0][0] < oldest:
            self.fast_samples.popleft()

    def _result(self, status, target=None):
        return {
            "status": status,
            "target": target,
            "learned_target": self.reliable_target,
            "confidence": self.reliable_confidence,
            "spread": self.reliable_spread,
            "valid_samples": len(self.offset_samples),
        }


class GridOffsetCalibrator:
    STATUS_SENSOR = "sensor.openems_netoffset_status"
    CORRECTED_SENSOR = "sensor.openems_gecorrigeerd_netvermogen"

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.estimator = OffsetEstimator(
            stability_seconds=GRID_OFFSET_STABILITY_SECONDS,
            stability_span=GRID_OFFSET_STABILITY_SPAN_W,
            reference_limit=GRID_OFFSET_REFERENCE_LIMIT_W,
            maximum_offset=GRID_OFFSET_MAX_ABS_W,
            minimum_samples=GRID_OFFSET_MIN_SAMPLES,
        )
        self.last_result = self.estimator._result("opstarten")
        self.raw_fast = None
        self.reference = None
        self.corrected = None
        self.sign_mismatch_samples = 0
        self.sign_error_latched = False
        self.calibrating = False
        self.calibration_started = None
        self.calibration_outcome = None
        self.clock = time.time

    def start(self):
        self.app.listen_state(
            self._calibrate_pressed,
            GRID_OFFSET_CALIBRATE_BUTTON,
        )
        self.app.run_every(self.tick, "now", 5)

    def corrected_grid_power(self):
        if self.sign_error_latched:
            raise ValueError(
                "tekenrichting snelle netvermogenssensor lijkt onjuist"
            )
        raw = self._read_fast()
        return raw + self.settings.effective_grid_offset()

    def tick(self, kwargs=None):
        now = time.time()
        offset = self.settings.effective_grid_offset()
        try:
            self.raw_fast = self._read_fast()
            self.corrected = self.raw_fast + offset
        except (TypeError, ValueError) as error:
            self.raw_fast = None
            self.reference = None
            self.corrected = None
            self._sensor_error(error, offset)
            self._calibration_timeout(str(error))
            return
        try:
            self.reference = self._number(
                self.settings.grid_reference_entity()
            )
            opposite = (
                abs(self.raw_fast) >= 200
                and abs(self.reference) >= 200
                and self.raw_fast * self.reference < 0
            )
            self.sign_mismatch_samples = (
                self.sign_mismatch_samples + 1 if opposite else 0
            )
            if self.sign_mismatch_samples >= 6:
                self.sign_error_latched = True
                self.last_result = self.estimator._result(
                    "tekenrichting_onjuist"
                )
                self._publish(
                    offset,
                    error=(
                        "Snelle sensor en P1 hebben langdurig een "
                        "tegengesteld teken"
                    ),
                )
                return
            if not opposite:
                self.sign_error_latched = False
            self.estimator.add_fast(now, self.raw_fast)
            self.last_result = self.estimator.observe(now, self.reference)
            if self.calibrating:
                self._calibration_step()
            offset = self.settings.effective_grid_offset()
            self.corrected = self.raw_fast + offset
            self._publish(offset)
        except (TypeError, ValueError) as error:
            self.reference = None
            self._sensor_error(error, offset)
            self._calibration_timeout(str(error))

    def _calibration_timeout(self, error=None):
        """Stop een lopende kalibratie die te lang geen resultaat geeft."""
        if (
            self.calibrating
            and self.calibration_started is not None
            and self.clock() - self.calibration_started
            > GRID_OFFSET_CALIBRATION_TIMEOUT_S
        ):
            status = self.last_result["status"]
            self.calibrating = False
            self.calibration_started = None
            self.last_result = dict(self.last_result, status="kalibratie_mislukt")
            self.calibration_outcome = "mislukt"
            self.app.log(
                "[Netoffset] Kalibratie gestopt zonder betrouwbaar resultaat "
                f"(laatste status: {status}); de bestaande offset blijft gelden",
                level="WARNING",
            )
            if error is not None:
                self._publish(
                    self.settings.effective_grid_offset(), error=error
                )

    def _sensor_error(self, error, offset):
        self.last_result = self.estimator._result(
            "sensor_niet_beschikbaar"
        )
        self._publish(offset, error=str(error))

    def _calibration_step(self):
        """Eenmalige kalibratie: bewaar de offset zodra de meting betrouwbaar is."""
        target = self.last_result["target"]
        if target is not None and abs(target) > GRID_OFFSET_MANUAL_LIMIT_W:
            self.calibrating = False
            self.calibration_started = None
            self.last_result = dict(self.last_result, status="kalibratie_mislukt")
            self.calibration_outcome = "mislukt"
            self.app.log(
                f"[Netoffset] Kalibratie afgebroken: gemeten verschil "
                f"{target:+.0f} W valt buiten ±{GRID_OFFSET_MANUAL_LIMIT_W} W; "
                "controleer de sensoren. De bestaande offset blijft gelden",
                level="WARNING",
            )
            return
        if target is not None:
            value = round(target)
            self.app.call_service(
                "input_number/set_value",
                entity_id=GRID_MANUAL_OFFSET_HELPER,
                value=value,
            )
            self.app.call_service(
                "input_select/select_option",
                entity_id=GRID_OFFSET_MODE_HELPER,
                option="Handmatig",
            )
            stamp = datetime.now(
                getattr(self.app, "timezone", None)
            ).strftime("%d-%m-%Y %H:%M")
            self.app.call_service(
                "input_text/set_value",
                entity_id=GRID_OFFSET_LAST_CALIBRATION_HELPER,
                value=f"{stamp} · {value:+d} W",
            )
            self.calibrating = False
            self.calibration_started = None
            self.last_result = dict(self.last_result, status="kalibratie_klaar")
            self.calibration_outcome = "klaar"
            self.app.log(
                f"[Netoffset] Kalibratie klaar: offset {value:+d} W vastgezet"
            )
            return
        self._calibration_timeout()

    def _calibrate_pressed(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        self.estimator.reset()
        self.sign_mismatch_samples = 0
        self.sign_error_latched = False
        self.calibrating = True
        self.calibration_started = self.clock()
        self.last_result = self.estimator._result("opnieuw_gestart")
        self.calibration_outcome = None
        self.app.log(
            "[Netoffset] Eenmalige kalibratie gestart; meet zolang de "
            "omstandigheden stabiel zijn"
        )
        self.tick()

    def _read_fast(self):
        if self.settings.grid_entity() == self.CORRECTED_SENSOR:
            raise ValueError(
                "De gecorrigeerde OpenEMS-sensor mag niet als ruwe "
                "regelsensor worden gekozen"
            )
        value = self._number(self.settings.grid_entity())
        return value * self.settings.grid_import_sign()

    def _number(self, entity_id):
        value = self.app.get_state(entity_id)
        if value in (None, "", "unknown", "unavailable"):
            raise ValueError(f"{entity_id or 'sensor'}={value}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{entity_id or 'sensor'}={value}")
        return number

    def _publish(self, offset, error=None):
        difference = (
            self.reference - self.raw_fast
            if self.reference is not None and self.raw_fast is not None
            else None
        )
        attributes = {
            "friendly_name": "OpenEMS netoffsetstatus",
            "icon": "mdi:tune-vertical",
            "modus": self.settings.grid_offset_mode(),
            "snelle_sensor": self.settings.grid_entity(),
            "referentiesensor": self.settings.grid_reference_entity(),
            "snelle_sensor_raw_w": self._rounded(self.raw_fast),
            "p1_referentie_w": self._rounded(self.reference),
            "actueel_verschil_w": self._rounded(difference),
            "handmatige_offset_w": round(
                self.settings.manual_grid_offset()
            ),
            "automatische_offset_w": round(
                self.settings.automatic_grid_offset()
            ),
            "actieve_offset_w": round(offset),
            "gecorrigeerd_netvermogen_w": self._rounded(self.corrected),
            "kalibratiestatus": self.last_result["status"],
            "kalibratie_actief": self.calibrating,
            "kalibratie_resultaat": self.calibration_outcome,
            "laatste_kalibratie": (
                self.app.get_state(GRID_OFFSET_LAST_CALIBRATION_HELPER)
                if self.app.get_state(GRID_OFFSET_LAST_CALIBRATION_HELPER)
                not in (None, "unknown", "unavailable")
                else ""
            ),
            "geldige_metingen": self.last_result["valid_samples"],
            "betrouwbaarheid_pct": self.last_result["confidence"],
            "kandidaat_offset_w": self._rounded(
                self.last_result["learned_target"]
            ),
            "huidige_kandidaat_offset_w": self._rounded(
                self.last_result["target"]
            ),
            "spreiding_w": self._rounded(self.last_result["spread"]),
            "fout": error,
        }
        state = (
            "fout" if error
            else "kalibreren" if self.calibrating
            else "automatisch" if self.settings.grid_offset_mode()
            == "automatic"
            else "handmatig"
        )
        self.app.set_state(
            self.STATUS_SENSOR,
            state=state,
            attributes=attributes,
            replace=True,
        )
        if self.corrected is not None:
            self.app.set_state(
                self.CORRECTED_SENSOR,
                state=round(self.corrected, 1),
                attributes={
                    "friendly_name": "OpenEMS gecorrigeerd netvermogen",
                    "icon": "mdi:transmission-tower",
                    "unit_of_measurement": "W",
                    "device_class": "power",
                    "state_class": "measurement",
                    "ruw_vermogen_w": self._rounded(self.raw_fast),
                    "toegepaste_offset_w": round(offset),
                },
                replace=True,
            )

    @staticmethod
    def _rounded(value):
        return round(value, 1) if value is not None else None


class HomeOffsetCalibrator:
    """Eenmalige kalibratie van de woning-CT tegen de P1.

    Zonder zonnepanelen en met alle accu's in rust is het woningverbruik
    gelijk aan het netvermogen. Het verschil is dan de vaste offset van de
    woning-CT. De uitkomst komt in de bestaande instelling "CT Verbruik
    Offset"; automatisch bijstellen gebeurt niet.
    """

    STATUS_SENSOR = "sensor.openems_home_offset_status"

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings
        self.estimator = OffsetEstimator(
            stability_seconds=GRID_OFFSET_STABILITY_SECONDS,
            stability_span=GRID_OFFSET_STABILITY_SPAN_W,
            reference_limit=HOME_OFFSET_REFERENCE_LIMIT_W,
            maximum_offset=GRID_OFFSET_MAX_ABS_W,
            minimum_samples=GRID_OFFSET_MIN_SAMPLES,
        )
        self.last_result = self.estimator._result("rust")
        self.calibrating = False
        self.calibration_started = None
        self.raw_home = None
        self.reference = None
        self.battery_power = None
        self.error = None
        self.calibration_outcome = None
        self.clock = time.time

    def start(self):
        self.app.listen_state(
            self._pressed, HOME_OFFSET_CALIBRATE_BUTTON
        )
        self.app.run_every(self.tick, "now+7", 5)
        self._publish()

    def _number(self, entity_id):
        value = self.app.get_state(entity_id)
        if value in (None, "", "unknown", "unavailable"):
            raise ValueError(f"{entity_id or 'sensor'}={value}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{entity_id or 'sensor'}={value}")
        return number

    def _pressed(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        self.estimator.reset()
        self.calibrating = True
        self.calibration_started = self.clock()
        self.error = None
        self.calibration_outcome = None
        self.last_result = self.estimator._result("opnieuw_gestart")
        self.app.log(
            "[Woningoffset] Eenmalige kalibratie gestart; accu's moeten in "
            "rust zijn en het huis rustig"
        )
        self.tick()

    def tick(self, kwargs=None):
        if not self.calibrating:
            return
        now = self.clock()
        try:
            self.raw_home = self._number(self.settings.home_entity())
            self.reference = self._number(
                self.settings.grid_reference_entity()
            )
            self.battery_power = sum(
                abs(self._number(item["actual_power"]))
                for item in BATTERIES
                if item.get("actual_power")
            )
            self.error = None
        except (TypeError, ValueError, KeyError) as error:
            self.error = str(error)
            self.last_result = self.estimator._result(
                "sensor_niet_beschikbaar"
            )
            self._timeout()
            self._publish()
            return
        if self.battery_power > HOME_OFFSET_BATTERY_IDLE_W:
            # Accu's leveren of laden: woning en net zijn dan niet gelijk.
            self.estimator.reset()
            self.last_result = self.estimator._result("accu_actief")
        else:
            self.estimator.add_fast(now, self.raw_home)
            self.last_result = self.estimator.observe(now, self.reference)
            self._step()
        self._timeout()
        self._publish()

    def _finish(self, status):
        self.calibrating = False
        self.calibration_started = None
        self.last_result = dict(self.last_result, status=status)
        self.calibration_outcome = (
            "klaar" if status == "kalibratie_klaar" else "mislukt"
        )

    def _step(self):
        target = self.last_result["target"]
        if target is None:
            return
        if abs(target) > GRID_OFFSET_MANUAL_LIMIT_W:
            self._finish("kalibratie_mislukt")
            self.app.log(
                f"[Woningoffset] Kalibratie afgebroken: gemeten verschil "
                f"{target:+.0f} W valt buiten ±{GRID_OFFSET_MANUAL_LIMIT_W} W; "
                "controleer de sensoren. De bestaande offset blijft gelden",
                level="WARNING",
            )
            return
        value = round(target)
        self.app.call_service(
            "input_number/set_value",
            entity_id=HOME_POWER_OFFSET_HELPER,
            value=value,
        )
        stamp = datetime.now(
            getattr(self.app, "timezone", None)
        ).strftime("%d-%m-%Y %H:%M")
        self.app.call_service(
            "input_text/set_value",
            entity_id=HOME_OFFSET_LAST_CALIBRATION_HELPER,
            value=f"{stamp} · {value:+d} W",
        )
        self._finish("kalibratie_klaar")
        self.app.log(
            f"[Woningoffset] Kalibratie klaar: offset {value:+d} W vastgezet"
        )

    def _timeout(self):
        if (
            self.calibrating
            and self.calibration_started is not None
            and self.clock() - self.calibration_started
            > GRID_OFFSET_CALIBRATION_TIMEOUT_S
        ):
            status = self.last_result["status"]
            self._finish("kalibratie_mislukt")
            self.app.log(
                "[Woningoffset] Kalibratie gestopt zonder betrouwbaar "
                f"resultaat (laatste status: {status}); de bestaande "
                "offset blijft gelden",
                level="WARNING",
            )

    @staticmethod
    def _rounded(value):
        return None if value is None else round(float(value), 1)

    def _publish(self):
        last = self.app.get_state(HOME_OFFSET_LAST_CALIBRATION_HELPER)
        self.app.set_state(
            self.STATUS_SENSOR,
            state="kalibreren" if self.calibrating else "rust",
            attributes={
                "friendly_name": "OpenEMS woningoffset status",
                "icon": "mdi:home-lightning-bolt-outline",
                "kalibratiestatus": self.last_result["status"],
                "kalibratie_actief": self.calibrating,
                "kalibratie_resultaat": self.calibration_outcome,
                "woning_raw_w": self._rounded(self.raw_home),
                "p1_referentie_w": self._rounded(self.reference),
                "accu_vermogen_w": self._rounded(self.battery_power),
                "huidige_kandidaat_offset_w": self._rounded(
                    self.last_result["target"]
                ),
                "geldige_metingen": self.last_result["valid_samples"],
                "actieve_offset_w": round(
                    self.settings.home_power_offset()
                ),
                "laatste_kalibratie": (
                    last if last not in (None, "unknown", "unavailable")
                    else ""
                ),
                "fout": self.error,
            },
            replace=True,
        )
