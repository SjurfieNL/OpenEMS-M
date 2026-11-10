import time
from datetime import datetime, timedelta, timezone

from runtime_safety_monitor import RuntimeSafetyMonitor


def battery(number):
    return {
        "name": f"Accu {number}",
        "actual_power": f"sensor.power_{number}",
        "soc": f"sensor.soc_{number}",
        "alarm_status": f"sensor.alarm_{number}",
        "fault_status": f"sensor.fault_{number}",
        "reset_device": f"button.reset_{number}",
    }


class App:
    def __init__(self):
        self.states = {
            "input_number.marstek_minimum_soc": "20",
            "input_number.marstek_doel_soc": "95",
        }
        for index in range(1, 4):
            self.states[f"sensor.power_{index}"] = "-500"
            self.states[f"sensor.soc_{index}"] = "50"
            self.states[f"sensor.alarm_{index}"] = "Normal"
            self.states[f"sensor.fault_{index}"] = "Normal"
        self.logs = []
        self.callbacks = []

    def get_state(self, entity):
        return self.states.get(entity)

    def log(self, message, **kwargs):
        self.logs.append(message)

    def run_in(self, callback, delay, **kwargs):
        self.callbacks.append((callback, kwargs))


class Controller:
    def __init__(self, app):
        self.app = app
        self.batteries = [battery(1), battery(2), battery(3)]
        self.coordinator = None
        self.enabled = True
        self.desired_state = "charging"
        self.desired_powers = [500, 500, 500]
        self.desired_source = "auto"
        self.desired_since = time.monotonic() - 60
        self.delivery_ready = [True, True, True]
        self.stopped = []
        self.isolated = []
        self.isolated_safety = []
        self.restored = []
        self.restore_powers = []
        self.reset_attempted = [False, False, False]
        self.reset_pending = [False, False, False]
        self.resets = []

    def verify_state(self, state, powers, battery_indexes=None):
        indexes = (
            list(battery_indexes)
            if battery_indexes is not None else range(len(self.batteries))
        )
        return {
            "batteries": [
                {
                    "index": index,
                    "name": item["name"],
                    "ok": True,
                }
                for index, item in enumerate(self.batteries)
                if index in indexes
            ]
        }

    def stop_batteries(self, indexes, source, safety=False):
        self.stopped.append(list(indexes))

    def isolate_batteries(self, indexes, source, safety=False):
        self.isolated.append(list(indexes))
        self.isolated_safety.append(bool(safety))

    def restore_batteries(self, state, indexes, powers, source):
        self.restored.append((state, list(indexes)))
        self.restore_powers.append(list(powers))
        self.desired_since = time.monotonic() - 60
        return True

    def request_device_reset(self, index, source):
        if self.reset_attempted[index]:
            return False
        self.reset_attempted[index] = True
        self.reset_pending[index] = True
        self.resets.append((index, source))
        return True

    def device_reset_pending(self, index):
        return self.reset_pending[index]

    def complete_device_reset(self, index, recovered=False):
        self.reset_pending[index] = False
        if recovered:
            self.reset_attempted[index] = False

    def recommended_verification_delay(self, minimum=4):
        return minimum

    def battery_delivery_ready(self, index, grace_seconds):
        return self.delivery_ready[index]


def test_one_unavailable_battery_gets_same_command_without_pre_stop():
    app = App()
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.power_2"] = "unavailable"

    for _ in range(3):
        monitor.tick()

    assert controller.isolated == []
    assert monitor.recoveries[1] == 1
    assert len(app.callbacks) == 1


def test_each_battery_gets_its_own_delivery_grace():
    app = App()
    controller = Controller(app)
    controller.delivery_ready[2] = False
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.power_1"] = "0"
    app.states["sensor.power_3"] = "0"

    for _ in range(3):
        monitor.tick()

    assert controller.isolated == []
    assert monitor.recoveries[0] == 1
    assert monitor.counts[2] == 0


def test_opposite_charge_direction_isolated_immediately_for_every_battery():
    for index in range(3):
        app = App()
        controller = Controller(app)
        monitor = RuntimeSafetyMonitor(app, controller)
        app.states[f"sensor.power_{index + 1}"] = "700"

        monitor.tick()

        assert controller.isolated == [[index]]
        assert controller.isolated_safety == [True]
        assert monitor.recoveries[index] == 0
        assert len(app.callbacks) == 0


def test_lower_charge_power_is_accepted_per_battery_from_96_percent():
    app = App()
    app.states["input_number.marstek_doel_soc"] = "100"
    controller = Controller(app)
    controller.desired_powers = [2300, 500, 500]
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.soc_1"] = "96"
    app.states["sensor.power_1"] = "-250"

    for _ in range(6):
        monitor.tick()

    assert monitor.counts[0] == 0
    assert monitor.recoveries[0] == 0
    assert controller.isolated == []
    assert app.callbacks == []


def test_lower_charge_power_below_96_percent_still_triggers_recovery():
    app = App()
    app.states["input_number.marstek_doel_soc"] = "100"
    controller = Controller(app)
    controller.desired_powers = [2300, 500, 500]
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.soc_1"] = "95.9"
    # Vrijwel geen vermogen (onder de begrensd-drempel) blijft een afwijking.
    app.states["sensor.power_1"] = "-100"

    for _ in range(3):
        monitor.tick()

    assert controller.isolated == []
    assert monitor.recoveries[0] == 1
    assert len(app.callbacks) == 1


def test_opposite_charge_direction_remains_error_at_99_percent():
    app = App()
    app.states["input_number.marstek_doel_soc"] = "100"
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.soc_1"] = "99"
    app.states["sensor.power_1"] = "700"

    monitor.tick()

    assert controller.isolated == [[0]]
    assert monitor.recoveries[0] == 0
    assert len(app.callbacks) == 0



def test_bms_alarm_aborts_scheduled_recovery():
    app = App()
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    monitor._generations[0] = 4
    app.states["sensor.alarm_1"] = "Over voltage"

    monitor._restore({
        "index": 0,
        "generation": 4,
        "state": "charging",
    })

    assert controller.restored == []
    assert controller.isolated == [[0]]
    assert any("herstel afgebroken" in item for item in app.logs)


def test_old_recovery_callback_is_ignored():
    app = App()
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    monitor._generations[0] = 5

    monitor._restore({
        "index": 0,
        "generation": 4,
        "state": "charging",
    })

    assert controller.restored == []


def test_state_change_cancels_pending_recovery_from_previous_command():
    app = App()
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.power_1"] = "0"

    for _ in range(3):
        monitor.tick()

    assert len(app.callbacks) == 1
    callback, old_charge_recovery = app.callbacks.pop(0)

    controller.desired_state = "forced_discharge"
    controller.desired_powers = [1400, 2100, 2400]
    controller.desired_source = "export"
    app.states["sensor.power_1"] = "1400"
    app.states["sensor.power_2"] = "2100"
    app.states["sensor.power_3"] = "2400"
    monitor.tick()

    callback(old_charge_recovery)

    assert controller.restored == []


def test_callback_itself_rejects_old_charge_before_next_monitor_tick():
    app = App()
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.power_1"] = "0"

    for _ in range(3):
        monitor.tick()

    callback, old_charge_recovery = app.callbacks.pop(0)
    controller.desired_state = "forced_discharge"
    controller.desired_powers = [1400, 2100, 2400]
    controller.desired_source = "export"

    callback(old_charge_recovery)

    assert controller.restored == []
    assert any(
        "verouderde herstelopdracht charging genegeerd" in item
        for item in app.logs
    )


def test_active_charge_gets_one_retry_then_one_device_reset():
    class Health:
        def __init__(self):
            self.calls = []

        def quarantine(self, index, reason, attempts):
            self.calls.append((index, reason, attempts))

        def mark_verified(self, index, details):
            pass

    app = App()
    controller = Controller(app)
    health = Health()
    monitor = RuntimeSafetyMonitor(
        app, controller, health_manager=health
    )
    app.states["sensor.power_1"] = "0"

    for _ in range(3):
        monitor.tick()
    assert monitor.recoveries[0] == 1
    callback, kwargs = app.callbacks.pop(0)
    callback(kwargs)
    assert controller.restored[-1] == ("charging", [0])

    for _ in range(3):
        monitor.tick()
    assert controller.resets == [(0, "auto")]
    reset_callback, reset_kwargs = app.callbacks.pop(0)
    assert reset_callback == monitor._resume_after_device_reset
    reset_callback(reset_kwargs)
    assert controller.restored[-1] == ("charging", [0])

    verify_callback, verify_kwargs = app.callbacks.pop(0)
    verify_callback(verify_kwargs)

    assert controller.isolated.count([0]) == 1
    assert monitor.latched[0] is True
    assert health.calls[0][0] == 0
    assert health.calls[0][2] == 1
    assert controller.resets == [(0, "auto")]
    assert any("apparaat-reset heeft" in item for item in app.logs)


def test_successful_device_reset_retries_once_and_releases_lock():
    app = App()
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    app.states["sensor.power_1"] = "0"

    for _ in range(3):
        monitor.tick()
    callback, kwargs = app.callbacks.pop(0)
    callback(kwargs)

    for _ in range(3):
        monitor.tick()
    callback, kwargs = app.callbacks.pop(0)
    callback(kwargs)

    app.states["sensor.power_1"] = "-500"
    callback, kwargs = app.callbacks.pop(0)
    callback(kwargs)

    assert controller.resets == [(0, "auto")]
    assert controller.reset_pending[0] is False
    assert controller.reset_attempted[0] is False
    assert monitor.latched[0] is False


def test_confirmed_mismatch_and_recovery_are_sent_to_event_log():
    class EventLog:
        def __init__(self):
            self.events = []

        def record(self, *args, **kwargs):
            self.events.append((args, kwargs))

    app = App()
    controller = Controller(app)
    events = EventLog()
    monitor = RuntimeSafetyMonitor(
        app, controller, event_log=events
    )
    app.states["sensor.power_1"] = "0"

    for _ in range(3):
        monitor.tick()
    assert events.events[-1][0][0] == "warning"
    assert events.events[-1][1]["battery_name"] == "Accu 1"

    callback, kwargs = app.callbacks.pop(0)
    callback(kwargs)
    app.states["sensor.power_1"] = "-500"
    monitor.tick()

    assert events.events[-1][0][0] == "recovered"
    assert "hersteld" in events.events[-1][0][1]


def test_bounded_recovery_near_target_soc_keeps_requested_power():
    app = App()
    app.states["input_number.marstek_doel_soc"] = "100"
    app.states["sensor.soc_1"] = "94"
    app.states["sensor.power_1"] = "0"
    controller = Controller(app)
    controller.desired_powers = [710, 500, 500]
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(3):
        monitor.tick()
    callback, kwargs = app.callbacks.pop(0)
    callback(kwargs)

    assert controller.restore_powers[-1][0] == 710
    assert controller.desired_powers[0] == 710
    assert not any("verlaagd naar 500 W" in item for item in app.logs)


def test_tapered_charge_at_99_percent_needs_no_recovery_or_topstop():
    app = App()
    app.states["input_number.marstek_doel_soc"] = "100"
    app.states["sensor.soc_1"] = "99"
    app.states["sensor.power_1"] = "0"
    controller = Controller(app)
    controller.desired_powers = [710, 500, 500]
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(6):
        monitor.tick()

    assert not monitor.is_top_charge_latched(0)
    assert monitor.recoveries[0] == 0
    assert controller.isolated == []
    assert app.callbacks == []
    assert monitor.filter_charge_powers([710, 500, 500])[0] == 710


def test_bms_topstop_is_released_after_economic_charge_block():
    app = App()
    controller = Controller(app)
    monitor = RuntimeSafetyMonitor(app, controller)
    monitor.top_charge_latches[0] = {
        "soc": 99.0,
        "reason": "test",
        "block_end": datetime.now(timezone.utc) - timedelta(seconds=1),
    }
    monitor.latched[0] = True

    assert monitor.is_top_charge_latched(0) is False
    assert monitor.latched[0] is False


def test_export_accepts_individual_shortfall_when_total_delivery_is_good():
    app = App()
    controller = Controller(app)
    controller.desired_state = "forced_discharge"
    controller.desired_source = "export"
    controller.desired_powers = [868, 1618, 1118]
    app.states["sensor.power_1"] = "1000"
    app.states["sensor.power_2"] = "1800"
    app.states["sensor.power_3"] = "797"
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(6):
        monitor.tick()

    assert controller.isolated == []
    assert monitor.recoveries == [0, 0, 0]


def test_export_accepts_useful_individual_delivery_when_total_is_still_low():
    app = App()
    controller = Controller(app)
    controller.desired_state = "forced_discharge"
    controller.desired_source = "export"
    controller.desired_powers = [868, 1618, 1118]
    app.states["sensor.power_1"] = "868"
    app.states["sensor.power_2"] = "1200"
    app.states["sensor.power_3"] = "797"
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(6):
        monitor.tick()

    assert controller.isolated == []
    assert monitor.recoveries == [0, 0, 0]


def test_export_still_recovers_when_total_delivery_is_far_too_low():
    app = App()
    controller = Controller(app)
    controller.desired_state = "forced_discharge"
    controller.desired_source = "export"
    controller.desired_powers = [1000, 1000, 1000]
    app.states["sensor.power_1"] = "1000"
    app.states["sensor.power_2"] = "1000"
    app.states["sensor.power_3"] = "0"
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(3):
        monitor.tick()

    assert controller.isolated == []
    assert monitor.recoveries[2] == 1
    assert len(app.callbacks) == 1


def test_export_opposite_direction_remains_immediate_error():
    app = App()
    controller = Controller(app)
    controller.desired_state = "forced_discharge"
    controller.desired_source = "export"
    controller.desired_powers = [1000, 1000, 1000]
    app.states["sensor.power_1"] = "1000"
    app.states["sensor.power_2"] = "1000"
    app.states["sensor.power_3"] = "-300"
    monitor = RuntimeSafetyMonitor(app, controller)

    monitor.tick()

    assert controller.isolated == [[2]]
    assert controller.isolated_safety == [True]


def test_quarantined_battery_is_not_reverified_after_redistribution():
    class Health:
        @staticmethod
        def is_available(index):
            return index != 2

        @staticmethod
        def mark_verified(index, details):
            return None

    app = App()
    controller = Controller(app)
    controller.desired_state = "forced_discharge"
    controller.desired_source = "export"
    controller.desired_powers = [1500, 1500, 0]
    app.states["sensor.power_1"] = "1500"
    app.states["sensor.power_2"] = "1500"
    app.states["sensor.power_3"] = "0"
    monitor = RuntimeSafetyMonitor(
        app, controller, health_manager=Health()
    )

    for _ in range(9):
        monitor.tick()

    assert controller.isolated == []
    assert monitor.recoveries == [0, 0, 0]
    assert monitor.latched[2] is True


def make_limited_monitor(measured, expected):
    app = App()
    controller = Controller(app)
    controller.desired_powers = [expected, expected, expected]
    app.states["sensor.power_1"] = str(measured)
    app.set_state = lambda *args, **kwargs: None
    return RuntimeSafetyMonitor(app, controller), app, controller


def test_limited_battery_keeps_participating_and_is_not_isolated():
    monitor, app, controller = make_limited_monitor(measured=-1070, expected=2000)
    assert monitor._delivery_ok(0, "charging") is True
    assert monitor.limited == {0: 1070}
    assert not controller.isolated


def test_overdelivery_and_wrong_direction_are_still_faults():
    monitor, _app, _c = make_limited_monitor(measured=-3500, expected=2000)
    assert monitor._delivery_ok(0, "charging") is False
    monitor, _app, _c = make_limited_monitor(measured=300, expected=2000)
    assert monitor._delivery_ok(0, "charging") is False
    monitor, _app, _c = make_limited_monitor(measured=-50, expected=2000)
    assert monitor._delivery_ok(0, "charging") is False


def test_limited_sensor_state_is_never_falsy_zero():
    """AppDaemon laat een state van 0 weg; HA weigert dat met een 400-fout."""
    monitor, app, _controller = make_limited_monitor(measured=-1070, expected=2000)
    calls = []
    app.set_state = lambda entity, **kwargs: calls.append((entity, kwargs))
    assert monitor._delivery_ok(0, "charging") is True
    monitor._clear_limited(0)
    states = [kwargs["state"] for entity, kwargs in calls
              if entity == "sensor.openems_accu_begrenzing"]
    assert states == ["1", "0"]
    assert all(isinstance(state, str) and state for state in states)
