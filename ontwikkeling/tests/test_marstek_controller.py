import pytest
import marstek_controller
from marstek_controller import MarstekController
from types import SimpleNamespace


BATTERIES = [
    {
        "name": "Accu 1",
        "max_charge_power": "number.accu1_max_charge_power",
        "max_discharge_power": "number.accu1_max_discharge_power",
        "charge_power": "number.accu1_set_charge_power",
        "discharge_power": "number.accu1_set_discharge_power",
        "rs485_control": "switch.accu1_rs485_control_mode",
        "work_mode": "select.accu1_user_work_mode",
        "force_mode": "select.accu1_force_mode",
        "reset_device": "button.accu1_reset_device",
    }
]


class DummyApp:
    def __init__(self):
        self.states = {
            "number.accu1_max_charge_power": "2500",
            "number.accu1_max_discharge_power": "2500",
            "switch.accu1_rs485_control_mode": "off",
        }
        self.calls = []
        self.callbacks = []
        self.logs = []

    def get_state(self, entity):
        return self.states.get(entity)

    def call_service(self, service, **kwargs):
        self.calls.append((service, kwargs))
        if (
            service == "select/select_option"
            and kwargs.get("entity_id") == "select.accu1_user_work_mode"
        ):
            self.states["select.accu1_user_work_mode"] = kwargs.get("option")
            self.states["switch.accu1_rs485_control_mode"] = "off"

    def turn_on(self, entity):
        self.calls.append(("turn_on", {"entity_id": entity}))
        self.states[entity] = "on"

    def turn_off(self, entity):
        self.calls.append(("turn_off", {"entity_id": entity}))
        self.states[entity] = "off"

    def run_in(self, callback, delay, **kwargs):
        self.callbacks.append((callback, delay, kwargs))

    def log(self, *args, **kwargs):
        self.logs.append((args, kwargs))

    def run_callbacks(self):
        while self.callbacks:
            self.callbacks.sort(key=lambda item: item[1])
            callback, _delay, kwargs = self.callbacks.pop(0)
            callback(kwargs)


def test_duplicate_command_message_is_rate_limited():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)
    signature = ("discharge", "export", (1000,))
    controller._last_command = signature
    moments = iter((100.0, 105.0, 161.0))
    original = marstek_controller.time.monotonic
    marstek_controller.time.monotonic = lambda: next(moments)
    try:
        assert controller._is_duplicate(signature) is True
        assert controller._is_duplicate(signature) is True
        assert controller._is_duplicate(signature) is True
    finally:
        marstek_controller.time.monotonic = original

    duplicate_logs = [
        args for args, _kwargs in app.logs
        if args and args[0] == "Controller: identieke opdracht overgeslagen"
    ]
    assert len(duplicate_logs) == 2


def _three_batteries(app):
    batteries = []
    for number in (1, 2, 3):
        battery = {
            key: value.replace("accu1", f"accu{number}")
            if isinstance(value, str) else value
            for key, value in BATTERIES[0].items()
        }
        battery["name"] = f"Accu {number}"
        batteries.append(battery)
        app.states[f"number.accu{number}_max_charge_power"] = "2500"
        app.states[f"number.accu{number}_max_discharge_power"] = "2500"
        app.states[f"switch.accu{number}_rs485_control_mode"] = "off"
    return batteries


def test_controller_filters_quarantined_battery_from_export_command():
    class Health:
        @staticmethod
        def filter_powers(powers):
            return [powers[0], 0.0, powers[2]]

    app = DummyApp()
    controller = MarstekController(
        app, _three_batteries(app), enabled=True, command_delay=0.25
    )
    controller.bind_health_manager(Health())

    assert controller.set_discharge(
        [1000, 1000, 1000], source="export"
    ) is True
    assert controller.desired_powers == [1000, 0.0, 1000]
    assert controller.desired_source == "export"


def test_central_controller_stops_before_switching_charge_to_discharge():
    app = DummyApp()
    battery = dict(BATTERIES[0])
    battery["actual_power"] = "sensor.accu1_actual_power"
    app.states["sensor.accu1_actual_power"] = "-1800"
    app.states["number.accu1_set_charge_power"] = "1800"
    app.states["number.accu1_set_discharge_power"] = "0"
    app.states["select.accu1_force_mode"] = "charge"
    controller = MarstekController(
        app,
        [battery],
        enabled=False,
        test_enabled=True,
        command_delay=0.25,
    )

    assert controller.set_discharge([1200], source="manual") is True
    assert controller.desired_state == "stopped"
    assert not any(
        call[0] == "select/select_option"
        and call[1].get("entity_id") == "select.accu1_force_mode"
        and call[1].get("option") == "discharge"
        for call in app.calls
    )

    app.states["sensor.accu1_actual_power"] = "0"
    app.states["number.accu1_set_charge_power"] = "0"
    app.states["select.accu1_force_mode"] = "standby"
    app.run_callbacks()

    assert controller.desired_state == "forced_discharge"
    assert controller.desired_powers == [1200]


def test_manual_charge_releases_selector_quarantine_before_filtering():
    class Health:
        released = False

        def release_manual_selector_quarantines(self):
            self.released = True
            return [0, 1]

        def filter_powers(self, powers):
            return list(powers) if self.released else [0.0, 0.0, powers[2]]

    app = DummyApp()
    controller = MarstekController(
        app,
        _three_batteries(app),
        enabled=False,
        test_enabled=True,
        command_delay=0.25,
    )
    health = Health()
    controller.bind_health_manager(health)

    assert controller.set_charge([175, 175, 175], source="manual") is True
    assert controller.desired_powers == [175, 175, 175]


def test_device_reset_is_off_by_default_and_never_presses_button():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)
    assert controller.device_reset_enabled is False
    assert controller.request_device_reset(0, source="auto") is False
    assert not any(call[0] == "button/press" for call in app.calls)


def test_device_reset_is_sent_only_once_until_verified_recovery():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)
    controller.device_reset_enabled = True

    assert controller.request_device_reset(0, source="auto") is True
    assert controller.request_device_reset(0, source="auto") is False
    assert app.calls.count((
        "button/press", {"entity_id": "button.accu1_reset_device"}
    )) == 1
    assert controller.device_reset_pending(0) is True

    controller.complete_device_reset(0, recovered=False)
    assert controller.device_reset_pending(0) is False
    assert controller.request_device_reset(0, source="auto") is False

    controller.complete_device_reset(0, recovered=True)
    assert controller.request_device_reset(0, source="auto") is True


def test_charge_normalizes_mode_before_rs485_power_and_force_mode():
    app = DummyApp()
    controller = MarstekController(
        app, BATTERIES, enabled=True, command_delay=0.25
    )

    assert controller.set_charge([1200]) is True
    assert app.calls == [
        (
            "select/select_option",
            {
                "entity_id": "select.accu1_user_work_mode",
                    "option": "anti_feed",
            },
        )
    ]
    assert len(app.callbacks) == 1
    assert app.callbacks[0][1] == 0.25

    app.run_callbacks()
    assert app.calls == [
        (
            "select/select_option",
            {
                "entity_id": "select.accu1_user_work_mode",
                    "option": "anti_feed",
            },
        ),
        ("turn_on", {"entity_id": "switch.accu1_rs485_control_mode"}),
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_discharge_power", "value": 0},
        ),
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_charge_power", "value": 1200},
        ),
        (
            "select/select_option",
            {"entity_id": "select.accu1_force_mode", "option": "charge"},
        ),
    ]


def test_charge_reenables_rs485_after_mode_normalization():
    app = DummyApp()
    app.states["switch.accu1_rs485_control_mode"] = "on"
    controller = MarstekController(app, BATTERIES, enabled=True)

    controller.set_charge([1000])
    app.run_callbacks()

    assert any(service == "turn_on" for service, _kwargs in app.calls)
    assert len(app.calls) == 5


def test_discharge_writes_discharge_then_charge_zero_then_force_mode():
    app = DummyApp()
    app.states["switch.accu1_rs485_control_mode"] = "on"
    controller = MarstekController(app, BATTERIES, enabled=True)

    controller.set_discharge([900])
    app.run_callbacks()

    assert app.calls == [
        (
            "select/select_option",
            {
                "entity_id": "select.accu1_user_work_mode",
                    "option": "anti_feed",
            },
        ),
        ("turn_on", {"entity_id": "switch.accu1_rs485_control_mode"}),
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_discharge_power", "value": 900},
        ),
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_charge_power", "value": 0},
        ),
        (
            "select/select_option",
            {"entity_id": "select.accu1_force_mode", "option": "discharge"},
        ),
    ]


def test_discharge_command_is_not_changed_at_bms_cutoff():
    app = DummyApp()
    batteries = _three_batteries(app)
    for number, soc in ((1, 40), (2, 13), (3, 35)):
        batteries[number - 1]["soc"] = f"sensor.accu{number}_soc"
        app.states[f"sensor.accu{number}_soc"] = str(soc)
    controller = MarstekController(
        app, batteries, enabled=True, command_delay=0
    )

    assert controller.set_discharge([500, 500, 500]) is True
    app.run_callbacks()

    assert controller.desired_powers == [500, 500, 500]
    force_calls = {
        data["entity_id"]: data["option"]
        for service, data in app.calls
        if service == "select/select_option"
        and data.get("entity_id", "").endswith("_force_mode")
    }
    assert force_calls == {
        "select.accu1_force_mode": "discharge",
        "select.accu2_force_mode": "discharge",
        "select.accu3_force_mode": "discharge",
    }


def test_manual_charge_power_changes_without_zero_or_force_rewrite():
    app = DummyApp()
    controller = MarstekController(
        app, BATTERIES, enabled=False, test_enabled=True
    )
    controller.set_charge([1200], source="manual")
    app.run_callbacks()
    app.calls.clear()

    assert controller.set_charge([1800], source="manual") is True
    app.run_callbacks()

    assert app.calls == [(
        "number/set_value",
        {"entity_id": "number.accu1_set_charge_power", "value": 1800},
    )]
    assert controller.desired_state == "charging"
    assert controller.desired_powers == [1800.0]


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_adopt_single_battery_power_changes_only_that_battery():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, enabled=False, test_enabled=True
    )
    controller.set_charge([750, 750, 750], source="manual")
    app.run_callbacks()
    app.calls.clear()

    assert controller.adopt_single_battery_power(1, 2000, source="manual") is True
    app.run_callbacks()

    assert controller.desired_powers == [750.0, 2000.0, 750.0]
    assert (
        "number/set_value",
        {"entity_id": "number.accu2_set_charge_power", "value": 2000},
    ) in app.calls


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_adopt_single_battery_power_uses_current_discharge_direction():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, enabled=False, test_enabled=True
    )
    controller.set_discharge([600, 600, 600], source="manual")
    app.run_callbacks()
    app.calls.clear()

    assert controller.adopt_single_battery_power(0, 1500, source="manual") is True
    app.run_callbacks()

    assert controller.desired_powers == [1500.0, 600.0, 600.0]
    assert controller.desired_state == "forced_discharge"


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_adopt_single_battery_power_rejected_outside_charge_or_discharge():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, enabled=False, test_enabled=True
    )

    assert controller.adopt_single_battery_power(0, 1500, source="manual") is False
    assert controller.desired_powers == []


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_adopt_single_battery_power_rejects_out_of_range_index():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, enabled=False, test_enabled=True
    )
    controller.set_charge([750, 750, 750], source="manual")
    app.run_callbacks()

    assert controller.adopt_single_battery_power(5, 2000, source="manual") is False


def test_smooth_charge_change_never_stops_unchanged_battery_group():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(app, batteries, enabled=True)
    controller.set_charge([1000, 1100, 0])
    app.run_callbacks()
    app.calls.clear()

    assert controller.set_charge([1050, 0, 900]) is True
    app.run_callbacks()

    accu1_calls = [
        call for call in app.calls
        if "accu1" in str(call)
    ]
    assert accu1_calls == [(
        "number/set_value",
        {"entity_id": "number.accu1_set_charge_power", "value": 1050},
    )]
    assert any("accu2_force_mode" in str(call) for call in app.calls)
    assert any("accu3_force_mode" in str(call) for call in app.calls)
    assert controller.mode_coordinator.status()["smooth_update"] is True


def test_manual_discharge_power_changes_without_zero_or_force_rewrite():
    app = DummyApp()
    controller = MarstekController(
        app, BATTERIES, enabled=False, test_enabled=True
    )
    controller.set_discharge([900], source="manual")
    app.run_callbacks()
    app.calls.clear()

    assert controller.set_discharge([1400], source="manual") is True
    app.run_callbacks()

    assert app.calls == [(
        "number/set_value",
        {
            "entity_id": "number.accu1_set_discharge_power",
            "value": 1400,
        },
    )]
    assert controller.desired_state == "forced_discharge"
    assert controller.desired_powers == [1400.0]


def test_coordinated_discharge_changes_only_setpoints():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(app, batteries, enabled=True)

    controller.set_discharge(
        [950, 980, 1040], source="coordinated"
    )
    app.run_callbacks()
    app.calls.clear()

    assert controller.set_discharge(
        [970, 1000, 1060], source="coordinated"
    ) is True
    app.run_callbacks()

    assert app.calls == [
        (
            "number/set_value",
            {
                "entity_id": f"number.accu{number}_set_discharge_power",
                "value": value,
            },
        )
        for number, value in zip((1, 2, 3), (970, 1000, 1060))
    ]


def test_automatic_charge_changes_only_setpoints():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(app, batteries, enabled=True)

    controller.set_charge([1000, 1100, 1200])
    app.run_callbacks()
    app.calls.clear()

    assert controller.set_charge([1050, 1150, 1250]) is True
    app.run_callbacks()

    assert app.calls == [
        (
            "number/set_value",
            {
                "entity_id": f"number.accu{number}_set_charge_power",
                "value": value,
            },
        )
        for number, value in zip((1, 2, 3), (1050, 1150, 1250))
    ]


def test_atomic_charge_uses_one_parallel_batch_service_call():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app,
        batteries,
        enabled=True,
        command_delay=1.0,
        atomic_control=True,
    )

    assert controller.set_charge([1000, 1100, 1200]) is True

    assert app.calls == [
        (
            "openems_m_panel/set_battery_powers",
            {
                "commands": [
                        {
                            "battery": number,
                            "charge_power": power,
                            "discharge_power": 0,
                            "source": "auto",
                        "reason": (
                                "volledige atomaire controlleropdracht auto"
                            ),
                            "entity_id": f"select.accu{number}_force_mode",
                        }
                    for number, power in zip(
                        (1, 2, 3), (1000, 1100, 1200)
                    )
                ],
            },
        )
    ]
    assert len(app.callbacks) == 1
    assert app.callbacks[0][0].__name__ == "_atomic_sequence_timeout"
    assert controller.recommended_verification_delay(0) == 3


def test_confirmed_atomic_stop_is_never_skipped_as_duplicate():
    app = DummyApp()
    controller = MarstekController(
        app, BATTERIES, enabled=True, atomic_control=True
    )
    controller.bind_coordinator(SimpleNamespace(deactivate=lambda: None))

    assert controller.set_stop(
        source="coordinated", require_confirmation=True
    ) is True
    first_request = controller.last_atomic_request_id()
    assert controller.set_stop(
        source="coordinated", require_confirmation=True
    ) is True
    assert len(app.calls) == 1
    assert controller.handle_atomic_result({
        "request_id": first_request, "ok": True
    }) is True
    second_request = controller.last_atomic_request_id()

    assert len(app.calls) == 2
    assert first_request
    assert second_request
    assert first_request != second_request


def test_atomic_delivery_grace_starts_after_matching_readback():
    app = DummyApp()
    controller = MarstekController(
        app, BATTERIES, enabled=True, atomic_control=True
    )
    controller.bind_coordinator(SimpleNamespace(deactivate=lambda: None))

    assert controller.set_stop(
        source="coordinated", require_confirmation=True
    ) is True
    request_id = controller.last_atomic_request_id()

    assert controller.atomic_batch_pending() is True
    assert controller.battery_delivery_ready(0, 0) is False
    assert controller.handle_atomic_result({
        "ok": True,
        "request_id": request_id,
        "results": [],
    }) is True
    assert controller.atomic_batch_pending() is False
    assert controller.battery_delivery_ready(0, 0) is True


def test_failed_atomic_batch_no_longer_blocks_the_healthy_batteries():
    """Storing 27-08-2026: een onbevestigde batch voor Accu 3 hield via een
    globale vergrendeling ook Accu 1 en Accu 2 uren van het laadblok af,
    terwijl runtime_safety_monitor.py de afwijkende accu al gericht (reset +
    herhaalopdracht + eventueel quarantaine) herstelt. Die globale
    vergrendeling is verwijderd: een niet-bevestigde batch wordt gelogd en
    laat de volgende opdracht (voor alle accu's) gewoon opnieuw toe, zodat
    alleen het per-accu-hersteltraject nog bepaalt of een accu meedoet."""
    app = DummyApp()
    controller = MarstekController(
        app, BATTERIES, enabled=True, atomic_control=True
    )
    controller.bind_coordinator(SimpleNamespace(deactivate=lambda: None))

    assert controller.set_charge([1200], source="auto") is True
    failed_request = controller.last_atomic_request_id()
    assert controller.handle_atomic_result({
        "request_id": failed_request, "ok": False
    }) is True

    assert controller.set_discharge([900], source="auto") is True
    assert controller.set_charge([1200], source="auto") is True


def test_atomic_direction_change_never_publishes_partial_register_states():
    app = DummyApp()
    controller = MarstekController(
        app, BATTERIES, enabled=True, atomic_control=True
    )
    controller.bind_coordinator(SimpleNamespace(deactivate=lambda: None))

    assert controller.set_charge([1200]) is True
    charge_request = controller.last_atomic_request_id()
    assert controller.set_discharge([900]) is True
    assert len(app.calls) == 1

    assert controller.handle_atomic_result({
        "request_id": charge_request, "ok": True
    }) is True
    stop_request = controller.last_atomic_request_id()
    assert app.calls[-1][1]["commands"][0]["charge_power"] == 0
    assert app.calls[-1][1]["commands"][0]["discharge_power"] == 0

    assert controller.handle_atomic_result({
        "request_id": stop_request, "ok": True
    }) is True
    app.run_callbacks()

    assert app.calls[-1][1]["commands"][0]["charge_power"] == 0
    assert app.calls[-1][1]["commands"][0]["discharge_power"] == 900


def test_atomic_mixed_charge_stops_inactive_batteries_in_same_phase():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, enabled=True, atomic_control=True
    )

    assert controller.set_charge([0, 700, 0]) is True

    assert app.calls == [
        (
            "openems_m_panel/set_battery_powers",
            {
                "commands": [
                        {
                            "battery": 1,
                            "charge_power": 0,
                            "discharge_power": 0,
                            "source": "auto",
                        "reason": (
                                "volledige atomaire controlleropdracht auto"
                            ),
                            "entity_id": "select.accu1_force_mode",
                        },
                        {
                            "battery": 3,
                            "charge_power": 0,
                            "discharge_power": 0,
                            "source": "auto",
                        "reason": (
                                "volledige atomaire controlleropdracht auto"
                            ),
                            "entity_id": "select.accu3_force_mode",
                        },
                        {
                            "battery": 2,
                            "charge_power": 700,
                            "discharge_power": 0,
                            "source": "auto",
                        "reason": (
                                "volledige atomaire controlleropdracht auto"
                            ),
                            "entity_id": "select.accu2_force_mode",
                        },
                ],
            },
        ),
    ]


def test_atomic_verification_does_not_require_anti_feed_shadow_mode():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "manual",
        "switch.accu1_rs485_control_mode": "on",
        "select.accu1_force_mode": "charge",
        "number.accu1_set_charge_power": "1200",
        "number.accu1_set_discharge_power": "0",
    })
    controller = MarstekController(
        app, BATTERIES, enabled=True, atomic_control=True
    )

    assert controller.verify_state("charging", [1200])["ok"] is True


def test_atomic_stop_verification_requires_rs485_force_stop_and_zeroes():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "manual",
        "switch.accu1_rs485_control_mode": "on",
        "select.accu1_force_mode": "stop",
        "number.accu1_set_charge_power": "0",
        "number.accu1_set_discharge_power": "0",
    })
    controller = MarstekController(
        app, BATTERIES, enabled=True, atomic_control=True
    )

    assert controller.verify_state("stopped")["ok"] is True
    app.states["switch.accu1_rs485_control_mode"] = "off"
    assert controller.verify_state("stopped")["ok"] is False


def test_atomic_forced_discharge_accepts_intentionally_stopped_zero_battery():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "switch.accu1_rs485_control_mode": "on",
        "select.accu1_force_mode": "stop",
        "number.accu1_set_charge_power": "0",
        "number.accu1_set_discharge_power": "0",
    })
    controller = MarstekController(
        app, BATTERIES, enabled=True, atomic_control=True
    )

    assert controller.verify_state("forced_discharge", [0])["ok"] is True


def test_active_battery_change_skips_equal_rs485_but_sets_charge_mode():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(app, batteries, enabled=True)

    controller.set_charge([1000, 1100, 0])
    app.run_callbacks()
    app.calls.clear()

    assert controller.set_charge([1000, 1100, 1200]) is True
    app.run_callbacks()

    assert not any(
        service == "turn_on"
        and kwargs["entity_id"] == "switch.accu3_rs485_control_mode"
        for service, kwargs in app.calls
    )
    assert any(
        service == "select/select_option"
        and kwargs.get("option") == "charge"
        for service, kwargs in app.calls
    )


def test_new_sequence_waits_until_pending_old_sequence_is_complete():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)

    controller.set_charge([1200])
    controller.set_discharge([900])
    app.run_callbacks()

    force_modes = [
        kwargs.get("option")
        for service, kwargs in app.calls
        if service == "select/select_option"
        and kwargs.get("entity_id") == "select.accu1_force_mode"
    ]
    assert force_modes == ["charge", "standby", "discharge"]


def test_unchanged_phases_do_not_consume_modbus_processing_delay():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "manual",
        "switch.accu1_rs485_control_mode": "on",
        "number.accu1_set_discharge_power": "0",
        "number.accu1_set_charge_power": "0",
    })
    controller = MarstekController(app, BATTERIES, enabled=True)

    controller._start_sequence([
        (
            0,
            "number_value_one",
            {
                "entity_id": "number.accu1_set_discharge_power",
                "value": 0,
            },
        ),
        (
            1,
            "force_mode_one",
            {
                "entity_id": "select.accu1_force_mode",
                "option": "standby",
            },
        ),
    ], source="test")

    assert app.callbacks == []
    assert any(
        service == "select/select_option"
        and kwargs.get("option") == "standby"
        for service, kwargs in app.calls
    )


def test_grid_guard_blocks_charge_before_any_modbus_write():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)
    guard = type("Guard", (), {
        "allows_controller_action": staticmethod(
            lambda action, source: action != "laden"
        ),
        "status_reason": staticmethod(
            lambda: "Netspanning onder 207 V; laden geblokkeerd"
        ),
    })()
    controller.bind_grid_status_monitor(guard)

    assert controller.set_charge([1200], source="auto") is False
    assert app.calls == []


def test_grid_outage_suspends_queue_without_touching_backup_switch():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)
    controller.set_charge([1200], source="auto")

    controller.suspend_for_grid_outage()
    app.run_callbacks()

    assert controller.desired_state == "grid_outage"
    assert controller.command_sequence_pending() is False
    assert not any(
        "backup" in str(kwargs.get("entity_id", "")).lower()
        for _service, kwargs in app.calls
    )


def test_normal_stop_waits_for_pending_charge_and_then_stops():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)

    controller.set_charge([1200])
    controller.set_stop()
    app.run_callbacks()

    force_modes = [
        kwargs.get("option")
        for service, kwargs in app.calls
        if service == "select/select_option"
        and kwargs.get("entity_id") == "select.accu1_force_mode"
    ]
    assert force_modes == ["charge", "standby"]
    assert app.calls[-3:] == [
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_discharge_power", "value": 0},
        ),
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_charge_power", "value": 0},
        ),
            (
                "select/select_option",
                {
                    "entity_id": "select.accu1_force_mode",
                    "option": "standby",
                },
            ),
    ]


def test_safety_isolation_preempts_pending_normal_sequence():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)

    controller.set_charge([1200])
    controller.isolate_batteries([0], source="auto", safety=True)
    app.run_callbacks()

    assert not any(
        kwargs.get("option") == "charge"
        for service, kwargs in app.calls
        if service == "select/select_option"
    )
    assert any(
        kwargs.get("option") == "standby"
        for service, kwargs in app.calls
        if service == "select/select_option"
    )


def test_stop_enables_rs485_when_called_directly_from_native_mode():
    app = DummyApp()
    app.states["select.accu1_user_work_mode"] = "ups"
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.set_stop() is True
    app.run_callbacks()

    assert app.calls == [
        (
            "select/select_option",
            {
                "entity_id": "select.accu1_user_work_mode",
                "option": "anti_feed",
            },
        ),
        ("turn_on", {"entity_id": "switch.accu1_rs485_control_mode"}),
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_discharge_power", "value": 0},
        ),
        (
            "number/set_value",
            {"entity_id": "number.accu1_set_charge_power", "value": 0},
        ),
        (
            "select/select_option",
            {"entity_id": "select.accu1_force_mode", "option": "standby"},
        ),
    ]


def test_stop_reenables_rs485_after_mode_normalization():
    app = DummyApp()
    app.states["switch.accu1_rs485_control_mode"] = "on"
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.set_stop() is True
    app.run_callbacks()

    assert any(service == "turn_on" for service, _kwargs in app.calls)
    assert len(app.calls) == 5


def test_three_batteries_are_dispatched_together_per_safe_phase():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, enabled=True, command_delay=1.0
    )

    controller.set_charge([1000, 1100, 1200])
    assert len(app.calls) == 3
    assert app.calls == [
        (
            "select/select_option",
                {
                    "entity_id": f"select.accu{number}_user_work_mode",
                        "option": "anti_feed",
                },
        )
        for number in (1, 2, 3)
    ]
    assert len(app.callbacks) == 1
    assert controller.recommended_verification_delay(8) == 8
    assert not controller.battery_delivery_ready(0, 10)
    assert not controller.battery_delivery_ready(1, 10)
    assert not controller.battery_delivery_ready(2, 10)

    app.run_callbacks()
    assert len(app.calls) == 15
    assert [
        kwargs["entity_id"]
        for service, kwargs in app.calls[3:6]
        if service == "turn_on"
    ] == [
        f"switch.accu{number}_rs485_control_mode"
        for number in (1, 2, 3)
    ]
    assert [
        kwargs["entity_id"]
        for service, kwargs in app.calls[-3:]
        if service == "select/select_option"
    ] == [
        f"select.accu{number}_force_mode"
        for number in (1, 2, 3)
    ]
    assert all(
        controller.battery_delivery_ready(index, 0)
        for index in range(3)
    )


def test_mixed_charge_plan_stops_inactive_batteries():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, enabled=True, command_delay=0
    )

    assert controller.set_charge([0, 700, 0]) is True
    app.run_callbacks()

    force_calls = [
        kwargs
        for service, kwargs in app.calls
        if service == "select/select_option"
        and kwargs["entity_id"].endswith("_force_mode")
    ]
    assert force_calls == [
        {
            "entity_id": "select.accu1_force_mode",
            "option": "standby",
        },
        {
            "entity_id": "select.accu3_force_mode",
            "option": "standby",
        },
        {
            "entity_id": "select.accu2_force_mode",
            "option": "charge",
        },
    ]
    assert controller.desired_powers == [0.0, 700.0, 0.0]


def test_mixed_charge_verification_accepts_stopped_inactive_battery():
    app = DummyApp()
    batteries = _three_batteries(app)
    for number in (1, 2, 3):
        app.states.update({
            f"select.accu{number}_user_work_mode": "anti_feed",
            f"switch.accu{number}_rs485_control_mode": "on",
            f"select.accu{number}_force_mode": (
                "charge" if number == 2 else "stop"
            ),
            f"number.accu{number}_set_charge_power": (
                "700" if number == 2 else "0"
            ),
            f"number.accu{number}_set_discharge_power": "0",
        })
    controller = MarstekController(app, batteries, enabled=True)

    result = controller.verify_state("charging", [0, 700, 0])

    assert result["ok"] is True


def test_self_consumption_uses_only_native_mode_transition():
    app = DummyApp()
    app.states["switch.accu1_rs485_control_mode"] = "on"
    app.states["select.accu1_user_work_mode"] = "manual"
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.set_self_consumption() is True
    app.run_callbacks()

    assert app.calls == [
        (
            "select/select_option",
            {
                "entity_id": "select.accu1_user_work_mode",
                "option": "anti_feed",
            },
        ),
    ]
    assert app.states["switch.accu1_rs485_control_mode"] == "off"


def test_self_consumption_skips_anti_feed_and_rs485_when_already_equal():
    app = DummyApp()
    app.states["select.accu1_user_work_mode"] = "anti_feed"
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.set_self_consumption() is True
    app.run_callbacks()
    assert app.calls == []


def test_self_consumption_selects_anti_feed_when_rs485_is_already_off():
    app = DummyApp()
    app.states["select.accu1_user_work_mode"] = "manual"
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.set_self_consumption() is True
    app.run_callbacks()
    assert app.calls == [
        (
            "select/select_option",
            {
                "entity_id": "select.accu1_user_work_mode",
                "option": "anti_feed",
            },
        ),
    ]


def test_self_consumption_only_disables_rs485_when_mode_is_already_native():
    app = DummyApp()
    app.states["switch.accu1_rs485_control_mode"] = "on"
    app.states["select.accu1_user_work_mode"] = "anti_feed"
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.set_self_consumption() is True
    app.run_callbacks()

    assert app.calls == [
        ("turn_off", {"entity_id": "switch.accu1_rs485_control_mode"}),
    ]


def test_queued_native_handoff_uses_status_after_preceding_sequence():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "switch.accu1_rs485_control_mode": "on",
        "select.accu1_force_mode": "discharge",
        "number.accu1_set_charge_power": "0",
        "number.accu1_set_discharge_power": "1600",
    })
    controller = MarstekController(
        app, BATTERIES, enabled=True, command_delay=0.25
    )

    assert controller.set_stop(source="export") is True
    assert controller.set_native_self_consumption_selection([0]) is True
    # Bootst de uiteindelijke Venus-status na de voorgaande reeks na. De
    # selectoropdracht was met de oudere anti_feed-status in de rij gezet.
    app.states["select.accu1_user_work_mode"] = "manual"
    app.run_callbacks()

    assert app.states["select.accu1_user_work_mode"] == "anti_feed"
    assert app.states["switch.accu1_rs485_control_mode"] == "off"
    assert app.calls[-1] == (
        "select/select_option",
        {
            "entity_id": "select.accu1_user_work_mode",
            "option": "anti_feed",
        },
    )


def test_verification_does_not_depend_on_user_work_mode():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "switch.accu1_rs485_control_mode": "on",
        "select.accu1_force_mode": "charge",
        "number.accu1_set_charge_power": "1200",
        "number.accu1_set_discharge_power": "0",
    })
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.verify_state("charging", [1200])["ok"] is True


def test_forced_discharge_requires_physical_delivery_when_sensor_exists():
    app = DummyApp()
    battery = dict(BATTERIES[0], actual_power="sensor.accu1_actual_power")
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "switch.accu1_rs485_control_mode": "on",
        "select.accu1_force_mode": "discharge",
        "number.accu1_set_charge_power": "0",
        "number.accu1_set_discharge_power": "500",
        "sensor.accu1_actual_power": "0",
    })
    controller = MarstekController(app, [battery], enabled=True)

    result = controller.verify_state("forced_discharge", [500])
    assert result["ok"] is False
    assert result["failed_indexes"] == [0]

    app.states["sensor.accu1_actual_power"] = "180"
    assert controller.verify_state("forced_discharge", [500])["ok"] is True


def test_each_battery_at_13_percent_is_exempt_from_delivery_recovery():
    app = DummyApp()
    batteries = _three_batteries(app)
    for number, soc in ((1, 13), (2, 13), (3, 13.1)):
        battery = batteries[number - 1]
        battery["soc"] = f"sensor.accu{number}_soc"
        battery["actual_power"] = f"sensor.accu{number}_actual_power"
        app.states.update({
            battery["work_mode"]: "anti_feed",
            battery["rs485_control"]: "on",
            battery["force_mode"]: "discharge",
            battery["charge_power"]: "0",
            battery["discharge_power"]: "500",
            battery["soc"]: str(soc),
            battery["actual_power"]: "0",
        })
    controller = MarstekController(app, batteries, enabled=True)

    result = controller.verify_state("forced_discharge", [500, 500, 500])

    assert result["ok"] is False
    assert result["failed_indexes"] == [2]


def test_charging_requires_physical_delivery_when_sensor_exists():
    app = DummyApp()
    battery = dict(BATTERIES[0], actual_power="sensor.accu1_actual_power")
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "switch.accu1_rs485_control_mode": "on",
        "select.accu1_force_mode": "charge",
        "number.accu1_set_charge_power": "500",
        "number.accu1_set_discharge_power": "0",
        "sensor.accu1_actual_power": "0",
    })
    controller = MarstekController(app, [battery], enabled=True)

    assert controller.verify_state("charging", [500])["ok"] is False
    app.states["sensor.accu1_actual_power"] = "-180"
    assert controller.verify_state("charging", [500])["ok"] is True


def test_self_consumption_verification_requires_anti_feed_and_rs485_off():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "switch.accu1_rs485_control_mode": "off",
        "select.accu1_force_mode": "discharge",
        "number.accu1_set_charge_power": "500",
        "number.accu1_set_discharge_power": "900",
    })
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.verify_state("self_consumption")["ok"] is True
    app.states["select.accu1_user_work_mode"] = "manual"
    assert controller.verify_state("self_consumption")["ok"] is False


def test_verification_reports_only_the_failed_battery_indexes():
    app = DummyApp()
    batteries = _three_batteries(app)
    for number in (1, 2, 3):
        app.states[f"select.accu{number}_user_work_mode"] = "anti_feed"
        app.states[f"switch.accu{number}_rs485_control_mode"] = (
            "off" if number == 1 else "on"
        )
        app.states[f"select.accu{number}_force_mode"] = "stop"
        app.states[f"number.accu{number}_set_charge_power"] = "0"
        app.states[f"number.accu{number}_set_discharge_power"] = "0"
    controller = MarstekController(app, batteries, enabled=True)

    result = controller.verify_state("self_consumption")

    assert result["ok"] is False
    assert result["failed_indexes"] == [1, 2]


def test_targeted_self_consumption_recovery_leaves_other_batteries_alone():
    app = DummyApp()
    batteries = _three_batteries(app)
    app.states["switch.accu2_rs485_control_mode"] = "on"
    app.states["select.accu2_user_work_mode"] = "manual"
    controller = MarstekController(
        app, batteries, test_enabled=True, command_delay=0.25
    )

    assert controller.restore_batteries(
        "self_consumption", [1], source="manual"
    ) is True
    app.run_callbacks()

    touched_entities = [
        kwargs.get("entity_id") for _service, kwargs in app.calls
    ]
    assert touched_entities
    assert all("accu2" in entity for entity in touched_entities)
    assert touched_entities == ["select.accu2_user_work_mode"]


def test_charge_recovery_only_resends_the_settings_that_still_differ():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "switch.accu1_rs485_control_mode": "on",
        "number.accu1_set_discharge_power": "0",
        "number.accu1_set_charge_power": "0",
        "select.accu1_force_mode": "standby",
    })
    controller = MarstekController(
        app, BATTERIES, enabled=True, command_delay=0.25
    )

    assert controller.restore_batteries(
        "charging", [0], [1200], source="auto"
    ) is True
    app.run_callbacks()

    assert app.calls == [
        (
            "number/set_value",
            {
                "entity_id": "number.accu1_set_charge_power",
                "value": 1200,
            },
        ),
        (
            "select/select_option",
            {
                "entity_id": "select.accu1_force_mode",
                "option": "charge",
            },
        ),
    ]


def test_targeted_stop_from_native_mode_only_touches_failed_battery():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(
        app, batteries, test_enabled=True, command_delay=0.25
    )

    assert controller.stop_batteries([2], source="manual") is True
    app.run_callbacks()

    touched_entities = [
        kwargs.get("entity_id") for _service, kwargs in app.calls
    ]
    assert len(touched_entities) == 5
    assert all("accu3" in entity for entity in touched_entities)


def test_safe_isolation_prevents_fallback_to_self_consumption():
    app = DummyApp()
    batteries = _three_batteries(app)
    app.states["select.accu3_user_work_mode"] = "anti_feed"
    controller = MarstekController(
        app, batteries, test_enabled=True, command_delay=0
    )

    assert controller.isolate_batteries([2], source="manual") is True
    app.run_callbacks()

    assert app.calls[-5:] == [
        (
            "select/select_option",
            {
                "entity_id": "select.accu3_user_work_mode",
                "option": "manual",
            },
        ),
        (
            "turn_on",
            {"entity_id": "switch.accu3_rs485_control_mode"},
        ),
        (
            "number/set_value",
            {
                "entity_id": "number.accu3_set_discharge_power",
                "value": 0,
            },
        ),
        (
            "number/set_value",
            {
                "entity_id": "number.accu3_set_charge_power",
                "value": 0,
            },
        ),
        (
            "select/select_option",
            {
                "entity_id": "select.accu3_force_mode",
                    "option": "standby",
            },
        ),
    ]


def test_idle_verification_accepts_native_control():
    app = DummyApp()
    app.states.update({
        "select.accu1_user_work_mode": "anti_feed",
        "select.accu1_force_mode": "discharge",
        "number.accu1_set_charge_power": "500",
        "number.accu1_set_discharge_power": "900",
    })
    controller = MarstekController(app, BATTERIES, enabled=True)

    assert controller.verify_state("idle")["ok"] is True



class _SelectorForController:
    def __init__(self, enabled=True):
        self._enabled = enabled
        self.native_active = False
        self.native_calls = []

    def enabled(self):
        return self._enabled

    def activate_native(self, source="auto"):
        self.native_active = True
        self.native_calls.append(source)
        return True

    def deactivate(self):
        self.native_active = False


class _CoordinatorForSelector:
    def __init__(self, mode):
        self.mode = mode
        self.active = False
        self.activate_calls = 0
        self.battery_selector = _SelectorForController()
        self.settings = self

    def self_consumption_mode(self):
        return self.mode

    def activate(self):
        self.active = True
        self.activate_calls += 1
        return True

    def deactivate(self, clear_latch=False):
        self.active = False
        self.battery_selector.deactivate()


def test_native_selector_only_chooses_batteries_and_keeps_antifeed():
    app = DummyApp()
    batteries = _three_batteries(app)
    controller = MarstekController(app, batteries, enabled=True)

    assert controller.set_native_self_consumption_selection([0]) is True
    app.run_callbacks()

    calls_by_entity = {
        kwargs.get("entity_id"): (service, kwargs)
        for service, kwargs in app.calls
        if kwargs.get("entity_id")
    }
    assert calls_by_entity[
        "select.accu1_user_work_mode"
    ][1]["option"] == "anti_feed"
    assert "switch.accu1_rs485_control_mode" not in calls_by_entity
    for number in (2, 3):
        assert calls_by_entity[
            f"select.accu{number}_user_work_mode"
        ][1]["option"] == "manual"
        assert calls_by_entity[
            f"switch.accu{number}_rs485_control_mode"
        ][0] == "turn_on"
        assert calls_by_entity[
            f"select.accu{number}_force_mode"
            ][1]["option"] == "standby"


def test_marstek_mode_uses_native_selector_without_changing_mode():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)
    coordinator = _CoordinatorForSelector("marstek")
    controller.bind_coordinator(coordinator)

    assert controller.set_self_consumption(source="auto") is True

    assert coordinator.activate_calls == 0
    assert coordinator.mode == "marstek"
    assert coordinator.battery_selector.native_calls == ["auto"]


def test_openems_mode_keeps_existing_coordinated_self_consumption():
    app = DummyApp()
    controller = MarstekController(app, BATTERIES, enabled=True)
    coordinator = _CoordinatorForSelector("openems")
    controller.bind_coordinator(coordinator)

    assert controller.set_self_consumption(source="auto") is True

    assert coordinator.activate_calls == 1
    assert coordinator.mode == "openems"
    assert coordinator.battery_selector.native_calls == []
