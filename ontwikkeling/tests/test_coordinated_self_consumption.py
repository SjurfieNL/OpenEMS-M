from coordinated_self_consumption import CoordinatedSelfConsumption
from power_distribution import distribute_discharge_power
from runtime_safety_monitor import RuntimeSafetyMonitor


class _App:
    def __init__(self):
        self.callbacks = []
        self.logs = []
        self.states = {
            "input_number.marstek_minimum_soc": "20",
            "input_number.marstek_doel_soc": "95",
        }

    def get_state(self, entity):
        return self.states.get(entity)

    def run_in(self, callback, delay, **kwargs):
        self.callbacks.append((callback, delay, kwargs))

    def log(self, *args, **kwargs):
        self.logs.append((args, kwargs))

    def set_state(self, *args, **kwargs):
        pass


class _Controller:
    def __init__(self):
        self.batteries = [{"name": f"Accu {n}"} for n in (1, 2, 3)]
        self.enabled = False
        self.coordinator = None
        self.desired_state = "charging"
        self.desired_powers = [500, 500, 500]
        self.desired_since = 0
        self.calls = []
        self.atomic_pending = False

    def set_stop(self, source="auto", require_confirmation=False):
        self.calls.append(("stop", source))
        return True

    def set_charge(self, powers, source="auto"):
        self.calls.append(("charge", powers, source))
        return True

    def set_discharge(self, powers, source="auto"):
        self.calls.append(("discharge", powers, source))
        return True

    def stop_batteries(self, indexes, source="auto"):
        self.calls.append(("stop_batteries", indexes, source))
        return True

    def isolate_batteries(self, indexes, source="auto", safety=False):
        self.calls.append(("isolate_batteries", indexes, source))
        return True

    def restore_batteries(self, state, indexes, powers, source="auto"):
        self.calls.append(("restore", state, indexes, source))
        return True

    def recommended_verification_delay(self, minimum=0):
        return minimum

    def atomic_batch_pending(self):
        return self.atomic_pending

    def battery_delivery_ready(self, index, grace_seconds):
        return True

    def verify_state(self, state, powers=None):
        return {
            "ok": False,
            "batteries": [
                {"index": n, "name": f"Accu {n + 1}", "ok": False}
                for n in range(3)
            ],
        }


class _BatteryManager:
    def read(self):
        class Battery:
            soc_values = [50, 50, 50]
            max_charge_values = [2500, 2500, 2500]
            max_discharge_values = [2500, 2500, 2500]
        return Battery()


class _Settings:
    pass


class _GridCalibrator:
    def __init__(self, corrected):
        self.corrected = corrected

    def corrected_grid_power(self):
        return self.corrected


def test_discharge_distribution_uses_only_soc_above_minimum():
    result = distribute_discharge_power(
        1200, [20, 40, 60], 20, [2500, 2500, 2500], 100
    )
    assert result == [0.0, 400.0, 800.0]


def test_discharge_distribution_never_exceeds_maximum():
    result = distribute_discharge_power(
        3000, [80, 80, 80], 20, [500, 1000, 2500], 100
    )
    assert result == [500, 1000, 1500]


def test_power_conflict_detects_simultaneous_charge_and_discharge():
    assert CoordinatedSelfConsumption._has_power_conflict([2500, -2300, 0])
    assert not CoordinatedSelfConsumption._has_power_conflict([2500, 400, 0])
    assert not CoordinatedSelfConsumption._has_power_conflict([-500, -1200, 0])


def test_direction_change_goes_through_stop_before_discharge():
    app = _App()
    controller = _Controller()
    coordinator = CoordinatedSelfConsumption(
        app, controller, _BatteryManager(), _Settings()
    )
    coordinator.active = True
    coordinator.phase = "controlling"
    coordinator.desired_direction = "charge"
    coordinator.expected_powers = [500, 500, 500]

    coordinator._apply_target(900)

    assert controller.calls == [("stop", "coordinated")]
    assert coordinator.phase == "transitioning"
    assert len(app.callbacks) == 1


def test_self_consumption_uses_corrected_fast_grid_power():
    app = _App()
    controller = _Controller()
    for index, battery in enumerate(controller.batteries):
        entity = f"sensor.accu{index + 1}_power"
        battery["actual_power"] = entity
        app.states[entity] = "0"
    coordinator = CoordinatedSelfConsumption(
        app,
        controller,
        _BatteryManager(),
        _Settings(),
        grid_calibrator=_GridCalibrator(150),
    )
    coordinator.active = True
    coordinator.phase = "controlling"
    requested = []
    coordinator._apply_target = requested.append
    coordinator._verify_runtime = lambda actual: None

    coordinator.tick()

    assert requested == [150]


def test_event_driven_control_is_rate_limited_but_watchdog_still_runs():
    app = _App()
    controller = _Controller()
    for index, battery in enumerate(controller.batteries):
        entity = f"sensor.accu{index + 1}_power"
        battery["actual_power"] = entity
        app.states[entity] = "0"
    coordinator = CoordinatedSelfConsumption(
        app,
        controller,
        _BatteryManager(),
        _Settings(),
        grid_calibrator=_GridCalibrator(150),
    )
    coordinator.active = True
    coordinator.phase = "controlling"
    requested = []
    coordinator._apply_target = requested.append
    coordinator._verify_runtime = lambda actual: None

    coordinator.tick({"event_driven": True})
    coordinator.tick({"event_driven": True})
    coordinator.tick({})

    assert requested == [150, 150]


def test_overlapping_control_tick_is_skipped():
    app = _App()
    controller = _Controller()
    coordinator = CoordinatedSelfConsumption(
        app,
        controller,
        _BatteryManager(),
        _Settings(),
        grid_calibrator=_GridCalibrator(150),
    )
    coordinator.active = True
    coordinator.phase = "controlling"
    coordinator._tick_running = True
    requested = []
    coordinator._apply_target = requested.append

    coordinator.tick({"event_driven": True})

    assert requested == []


def test_control_tick_waits_while_latest_marstek_batch_is_pending():
    app = _App()
    controller = _Controller()
    controller.atomic_pending = True
    coordinator = CoordinatedSelfConsumption(
        app,
        controller,
        _BatteryManager(),
        _Settings(),
        grid_calibrator=_GridCalibrator(150),
    )
    coordinator.active = True
    coordinator.phase = "controlling"
    requested = []
    coordinator._apply_target = requested.append

    coordinator.tick({"event_driven": True})

    assert requested == []


def test_targeted_recovery_pauses_normal_setpoint_updates():
    app = _App()
    controller = _Controller()
    controller.verify_state = lambda state, powers=None: {
        "ok": False,
        "batteries": [
            {"index": 0, "name": "Accu 1", "ok": True},
            {"index": 1, "name": "Accu 2", "ok": True},
            {"index": 2, "name": "Accu 3", "ok": False},
        ],
    }
    coordinator = CoordinatedSelfConsumption(
        app, controller, _BatteryManager(), _Settings()
    )
    coordinator.active = True
    coordinator.phase = "controlling"
    coordinator.desired_direction = "discharge"
    coordinator.expected_powers = [0, 0, 750]

    for _ in range(3):
        coordinator._verify_runtime([0, 0, 0])

    assert coordinator.phase == "recovering"
    assert controller.calls == [
        ("restore", "forced_discharge", [2], "coordinated")
    ]
    coordinator.tick({"event_driven": True})
    assert controller.calls == [
        ("restore", "forced_discharge", [2], "coordinated")
    ]


def test_stop_transition_waits_for_matching_atomic_readback():
    app = _App()
    controller = _Controller()
    coordinator = CoordinatedSelfConsumption(
        app, controller, _BatteryManager(), _Settings()
    )
    coordinator.active = True
    coordinator.phase = "stopping"
    coordinator._generation = 4
    coordinator._pending_stop_request_id = "batch-4"
    coordinator._pending_stop_purpose = "activation"
    coordinator._pending_stop_generation = 4
    coordinator.tick = lambda kwargs=None: None
    stop_results = [
        {
            "ok": True,
            "force_mode": 0,
            "set_charge_power": 0,
            "set_discharge_power": 0,
        }
        for _ in range(3)
    ]

    coordinator.handle_atomic_result({
        "ok": True,
        "request_id": "oude-batch",
        "results": stop_results,
    })
    assert coordinator.phase == "stopping"

    coordinator.handle_atomic_result({
        "ok": True,
        "request_id": "batch-4",
        "results": stop_results,
    })

    assert coordinator.phase == "controlling"
    assert coordinator.active is True
    assert coordinator._pending_stop_request_id is None


def test_stop_transition_rejects_matching_batch_without_stop_readback():
    app = _App()
    controller = _Controller()
    coordinator = CoordinatedSelfConsumption(
        app, controller, _BatteryManager(), _Settings()
    )
    coordinator.active = True
    coordinator.phase = "stopping"
    coordinator._generation = 2
    coordinator._pending_stop_request_id = "batch-2"
    coordinator._pending_stop_purpose = "activation"
    coordinator._pending_stop_generation = 2
    coordinator._central_failure = lambda reason: setattr(
        coordinator, "failure_reason", reason
    )

    coordinator.handle_atomic_result({
        "ok": True,
        "request_id": "batch-2",
        "results": [{
            "ok": True,
            "force_mode": 2,
            "set_charge_power": 0,
            "set_discharge_power": 750,
        }],
    })

    assert "niet bevestigd" in coordinator.failure_reason
    assert coordinator.phase == "stopping"


def test_runtime_verification_ignores_intentionally_inactive_empty_batteries():
    app = _App()
    controller = _Controller()
    controller.verify_state = lambda state, powers=None: {
        "ok": False,
        "batteries": [
            {"index": 0, "name": "Accu 1", "ok": False},
            {"index": 1, "name": "Accu 2", "ok": False},
            {"index": 2, "name": "Accu 3", "ok": True},
        ],
    }
    coordinator = CoordinatedSelfConsumption(
        app, controller, _BatteryManager(), _Settings()
    )
    coordinator.active = True
    coordinator.phase = "controlling"
    coordinator.desired_direction = "discharge"
    coordinator.expected_powers = [0, 0, 750]

    for _ in range(5):
        coordinator._verify_runtime([0, 0, 700])

    assert controller.calls == []
    assert coordinator.active is True
    assert coordinator._mismatch_counts == [0, 0, 0]


def test_runtime_monitor_without_reset_support_uses_one_bounded_retry():
    app = _App()
    controller = _Controller()
    monitor = RuntimeSafetyMonitor(app, controller)

    monitor.tick()
    monitor.tick()
    assert app.callbacks == []
    monitor.tick()
    assert len(app.callbacks) == 3
    pending = list(app.callbacks)
    app.callbacks.clear()
    for callback, _delay, kwargs in pending:
        callback(kwargs)

    for _ in range(3):
        monitor.tick()

    isolations = [
        call for call in controller.calls
        if call[0] == "isolate_batteries"
    ]
    restores = [
        call for call in controller.calls if call[0] == "restore"
    ]
    # Eén identieke herhaalopdracht per accu, daarna veilige eindisolatie als
    # de controller geen gerichte apparaat-reset ondersteunt.
    assert len(isolations) == 3
    assert len(restores) == 3


def test_runtime_monitor_logs_expected_bms_stop_as_info():
    app = _App()
    app.states["sensor.accu1_soc"] = "95"
    controller = _Controller()
    controller.batteries[0]["soc"] = "sensor.accu1_soc"
    controller.verify_state = lambda state, powers=None: {
        "ok": True,
        "batteries": [
            {"index": n, "name": f"Accu {n + 1}", "ok": True}
            for n in range(3)
        ],
    }
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(3):
        monitor.tick()

    bms_logs = [
        (args[0], kwargs.get("level"))
        for args, kwargs in app.logs
        if "veilig gestopt door BMS/SOC-signaal" in args[0]
    ]
    assert bms_logs == [
        (
            "Accu 1: veilig gestopt door BMS/SOC-signaal "
            "(SOC 95.0% >= doel 95.0%)",
            "INFO",
        )
    ]


def test_runtime_monitor_logs_exact_power_mismatch():
    app = _App()
    app.states["sensor.accu3_ac_power"] = "0"
    controller = _Controller()
    controller.batteries[2]["actual_power"] = "sensor.accu3_ac_power"
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(3):
        monitor.tick()

    accu3_logs = [
        args[0] for args, _kwargs in app.logs
        if args[0].startswith("Accu 3: besturingsafwijking bevestigd")
    ]
    assert len(accu3_logs) == 1
    assert "werkelijk laadvermogen=0 W" in accu3_logs[0]
    assert "verwacht 500 W (tolerantie ±125 W)" in accu3_logs[0]


def test_runtime_monitor_never_restarts_intentionally_inactive_battery():
    app = _App()
    controller = _Controller()
    controller.desired_powers = [500, 0, 500]
    monitor = RuntimeSafetyMonitor(app, controller)

    for _ in range(3):
        monitor.tick()

    callback, kwargs = next(
        (callback, kwargs)
        for callback, _delay, kwargs in app.callbacks
        if kwargs["index"] == 1
    )
    callback(kwargs)

    assert ("restore", "stopped", [1], "manual") in controller.calls
    assert ("restore", "charging", [1], "manual") not in controller.calls
