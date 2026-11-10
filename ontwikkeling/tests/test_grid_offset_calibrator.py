from math import isclose
import time

from grid_offset_calibrator import GridOffsetCalibrator, OffsetEstimator


def stable_estimate(reference, fast):
    estimator = OffsetEstimator(
        stability_seconds=30,
        stability_span=80,
        reference_limit=500,
        maximum_offset=300,
        minimum_samples=6,
    )
    result = None
    for timestamp in range(0, 56, 5):
        estimator.add_fast(timestamp, fast)
        result = estimator.observe(timestamp, reference)
    return result


def test_stable_zero_measurements_find_plus_fifty_watt_offset():
    result = stable_estimate(reference=0, fast=-50)

    assert result["status"] == "betrouwbaar"
    assert isclose(result["target"], 50)
    assert result["valid_samples"] >= 6


def test_same_additive_offset_applies_to_import_and_export():
    importing = stable_estimate(reference=100, fast=50)
    exporting = stable_estimate(reference=-100, fast=-150)

    assert importing["target"] == 50
    assert exporting["target"] == 50


def test_changing_fast_sensor_is_rejected():
    estimator = OffsetEstimator(
        stability_seconds=30,
        stability_span=80,
        minimum_samples=2,
    )
    result = None
    for timestamp in range(0, 36, 5):
        estimator.add_fast(
            timestamp,
            -50 if timestamp % 10 == 0 else 100,
        )
        result = estimator.observe(timestamp, 0)

    assert result["status"] == "snelle_sensor_niet_stabiel"
    assert result["target"] is None


def test_measurement_far_from_zero_is_not_used_for_calibration():
    estimator = OffsetEstimator(
        stability_seconds=30,
        reference_limit=500,
        minimum_samples=2,
    )
    result = None
    for timestamp in range(0, 36, 5):
        estimator.add_fast(timestamp, 550)
        result = estimator.observe(timestamp, 600)

    assert result["status"] == "referentie_te_ver_van_nulpunt"
    assert result["valid_samples"] == 0


def test_unsafe_offset_is_rejected_instead_of_clamped():
    result = stable_estimate(reference=0, fast=-400)

    assert result["status"] == "verschil_buiten_veilige_grens"
    assert result["target"] is None


def test_rejected_current_period_keeps_reliable_calibration_confidence():
    estimator = OffsetEstimator(
        stability_seconds=30,
        stability_span=80,
        minimum_samples=6,
    )
    reliable = None
    for timestamp in range(0, 56, 5):
        estimator.add_fast(timestamp, -50)
        reliable = estimator.observe(timestamp, 0)
    assert reliable["status"] == "betrouwbaar"
    previous_confidence = reliable["confidence"]
    previous_target = reliable["learned_target"]

    rejected = None
    for timestamp in range(60, 101, 5):
        estimator.add_fast(
            timestamp,
            -50 if timestamp % 10 == 0 else 100,
        )
        rejected = estimator.observe(timestamp, 0)

    assert rejected["status"] == "snelle_sensor_niet_stabiel"
    assert rejected["confidence"] >= previous_confidence
    assert rejected["confidence"] > 0
    assert rejected["learned_target"] == previous_target
    assert rejected["valid_samples"] >= 6


class DummyApp:
    def __init__(self):
        self.states = {
            "sensor.fast": "-50",
            "sensor.reference": "0",
        }
        self.services = []
        self.published = {}

    def get_state(self, entity):
        return self.states.get(entity)

    def call_service(self, service, **kwargs):
        self.services.append((service, kwargs))

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def listen_state(self, *args, **kwargs):
        pass

    def run_every(self, *args, **kwargs):
        pass

    def log(self, *args, **kwargs):
        pass


class DummySettings:
    def __init__(self, mode="automatic", manual=0, automatic=0):
        self.mode = mode
        self.manual = manual
        self.automatic = automatic

    def grid_entity(self):
        return "sensor.fast"

    def grid_reference_entity(self):
        return "sensor.reference"

    def grid_import_sign(self):
        return 1

    def grid_offset_mode(self):
        return self.mode

    def manual_grid_offset(self):
        return self.manual

    def automatic_grid_offset(self):
        return self.automatic

    def effective_grid_offset(self):
        return self.automatic if self.mode == "automatic" else self.manual


def test_manual_plus_fifty_offset_corrects_minus_fifty_to_zero():
    calibrator = GridOffsetCalibrator(
        DummyApp(),
        DummySettings(mode="manual", manual=50),
    )

    assert calibrator.corrected_grid_power() == 0


def _primed_calibrator(mode="manual"):
    app = DummyApp()
    settings = DummySettings(mode=mode, manual=0, automatic=0)
    calibrator = GridOffsetCalibrator(app, settings)
    now = time.time()
    for seconds in range(30, -1, -5):
        calibrator.estimator.add_fast(now - seconds, -50)
    for _ in range(6):
        calibrator.estimator.offset_samples.append(50)
    return app, calibrator


def test_offset_does_not_change_without_calibration_request():
    app, calibrator = _primed_calibrator(mode="manual")

    calibrator.tick()

    assert not any(
        entity.get("entity_id", "").endswith("offset")
        or "offset" in entity.get("entity_id", "")
        for _, entity in app.services
    )
    assert calibrator.calibrating is False


def test_one_time_calibration_stores_fixed_offset_and_stops():
    app, calibrator = _primed_calibrator(mode="automatic")
    calibrator.calibrating = True
    calibrator.calibration_started = calibrator.clock()

    calibrator.tick()

    assert (
        "input_number/set_value",
        {"entity_id": "input_number.openems_grid_handmatige_offset", "value": 50},
    ) in app.services
    assert (
        "input_select/select_option",
        {
            "entity_id": "input_select.openems_grid_offset_modus",
            "option": "Handmatig",
        },
    ) in app.services
    assert any(
        service == "input_text/set_value"
        and data["entity_id"] == "input_text.openems_offset_laatste_kalibratie"
        and data["value"].endswith("+50 W")
        for service, data in app.services
    )
    assert calibrator.calibrating is False

    app.services.clear()
    calibrator.tick()
    assert not any(
        service == "input_number/set_value" for service, _ in app.services
    )


def test_calibration_times_out_and_keeps_existing_offset():
    app = DummyApp()
    calibrator = GridOffsetCalibrator(app, DummySettings(mode="manual"))
    calibrator.calibrating = True
    calibrator.calibration_started = calibrator.clock() - 10_000

    calibrator.tick()

    assert calibrator.calibrating is False
    assert not any(
        service in ("input_number/set_value", "input_select/select_option")
        for service, _ in app.services
    )
    assert (
        app.published["sensor.openems_netoffset_status"][1]["kalibratiestatus"]
        == "kalibratie_mislukt"
    )


def test_persistent_opposite_grid_sign_is_blocked():
    app = DummyApp()
    app.states["sensor.fast"] = "-500"
    app.states["sensor.reference"] = "500"
    calibrator = GridOffsetCalibrator(app, DummySettings())

    for _ in range(6):
        calibrator.tick()

    assert calibrator.sign_error_latched
    assert (
        app.published["sensor.openems_netoffset_status"][1][
            "kalibratiestatus"
        ]
        == "tekenrichting_onjuist"
    )
    try:
        calibrator.corrected_grid_power()
        assert False, "Verkeerde tekenrichting had geblokkeerd moeten worden"
    except ValueError:
        pass


def test_calibration_refuses_offset_beyond_helper_range():
    app, calibrator = _primed_calibrator(mode="manual")
    calibrator.estimator.offset_samples.clear()
    for _ in range(6):
        calibrator.estimator.offset_samples.append(200)
    calibrator.calibrating = True
    calibrator.calibration_started = calibrator.clock()

    calibrator.tick()

    assert calibrator.calibrating is False
    assert not any(
        service == "input_number/set_value" for service, _ in app.services
    )


def test_calibration_times_out_even_when_sensor_stays_unavailable():
    app = DummyApp()
    app.states["sensor.fast"] = "unavailable"
    calibrator = GridOffsetCalibrator(app, DummySettings(mode="manual"))
    calibrator.calibrating = True
    calibrator.calibration_started = calibrator.clock() - 10_000

    calibrator.tick()

    assert calibrator.calibrating is False
    attrs = app.published["sensor.openems_netoffset_status"][1]
    assert attrs["kalibratie_resultaat"] == "mislukt"
    assert attrs["kalibratie_actief"] is False


def test_grid_calibration_outcome_is_klaar_after_success_and_reset_on_press():
    app, calibrator = _primed_calibrator(mode="automatic")
    calibrator.calibrating = True
    calibrator.calibration_started = calibrator.clock()
    calibrator.tick()
    assert (
        app.published["sensor.openems_netoffset_status"][1][
            "kalibratie_resultaat"
        ] == "klaar"
    )
    calibrator._calibrate_pressed("x", None, "1", "2", {})
    assert calibrator.calibration_outcome is None


# ---- woning-CT ------------------------------------------------------------
from grid_offset_calibrator import HomeOffsetCalibrator  # noqa: E402
from ems_config import BATTERIES  # noqa: E402


class HomeSettings:
    def __init__(self, offset=0):
        self.offset = offset

    def home_entity(self):
        return "sensor.home"

    def grid_reference_entity(self):
        return "sensor.reference"

    def home_power_offset(self):
        return self.offset


def _home(raw="400", p1="450", battery="0"):
    app = DummyApp()
    app.states["sensor.home"] = raw
    app.states["sensor.reference"] = p1
    for item in BATTERIES:
        app.states[item["actual_power"]] = battery
    calibrator = HomeOffsetCalibrator(app, HomeSettings())
    clock = {"t": 1000.0}
    calibrator.clock = lambda: clock["t"]
    return app, calibrator, clock


def _run(calibrator, clock, seconds=70):
    for _ in range(seconds // 5):
        clock["t"] += 5
        calibrator.tick()


def test_home_calibration_finds_offset_when_batteries_idle():
    app, calibrator, clock = _home(raw="400", p1="450")
    calibrator._pressed("x", None, "1", "2", {})
    _run(calibrator, clock)

    assert (
        "input_number/set_value",
        {"entity_id": "input_number.openems_ct_verbruik_offset", "value": 50},
    ) in app.services
    assert any(
        service == "input_text/set_value"
        and data["entity_id"]
        == "input_text.openems_home_offset_laatste_kalibratie"
        and data["value"].endswith("+50 W")
        for service, data in app.services
    )
    assert calibrator.calibrating is False
    attrs = app.published["sensor.openems_home_offset_status"][1]
    assert attrs["kalibratie_resultaat"] == "klaar"


def test_home_calibration_waits_while_a_battery_is_active():
    app, calibrator, clock = _home(battery="800")
    calibrator._pressed("x", None, "1", "2", {})
    _run(calibrator, clock, seconds=60)

    assert calibrator.calibrating is True
    assert not any(
        service == "input_number/set_value" for service, _ in app.services
    )
    assert (
        app.published["sensor.openems_home_offset_status"][1][
            "kalibratiestatus"
        ] == "accu_actief"
    )


def test_home_calibration_refuses_offset_beyond_150_w():
    app, calibrator, clock = _home(raw="200", p1="450")  # +250 W
    calibrator._pressed("x", None, "1", "2", {})
    _run(calibrator, clock)

    assert calibrator.calibrating is False
    assert not any(
        service == "input_number/set_value" for service, _ in app.services
    )
    assert (
        app.published["sensor.openems_home_offset_status"][1][
            "kalibratie_resultaat"
        ] == "mislukt"
    )


def test_home_calibration_times_out_without_changing_offset():
    app, calibrator, clock = _home(battery="800")
    calibrator._pressed("x", None, "1", "2", {})
    clock["t"] += 10_000
    calibrator.tick()

    assert calibrator.calibrating is False
    assert not any(
        service == "input_number/set_value" for service, _ in app.services
    )


def test_home_calibration_times_out_when_sensor_unavailable():
    app, calibrator, clock = _home()
    app.states["sensor.home"] = "unavailable"
    calibrator._pressed("x", None, "1", "2", {})
    clock["t"] += 10_000
    calibrator.tick()

    assert calibrator.calibrating is False
    attrs = app.published["sensor.openems_home_offset_status"][1]
    assert attrs["kalibratie_resultaat"] == "mislukt"


def test_home_calibrator_does_nothing_until_button_is_pressed():
    app, calibrator, clock = _home()
    _run(calibrator, clock)
    assert app.services == []
