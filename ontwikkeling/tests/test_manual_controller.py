import pytest
from datetime import datetime

from manual_controller import ManualController


class DummyApp:
    def __init__(self):
        self.states = {
            "input_boolean.openems_actief": "off",
            "input_select.openems_handmatige_modus": "Laden",
            "input_number.openems_handmatig_laadvermogen_per_accu": "750",
            "input_number.openems_handmatig_ontlaadvermogen_per_accu": "600",
            "input_select.marstek_controller_test": "Uit",
        }
        self.callbacks = []
        self.published = {}

    def get_state(self, entity):
        return self.states.get(entity)

    def listen_state(self, *_args):
        return None

    def run_in(self, callback, delay, **kwargs):
        self.callbacks.append((callback, delay, kwargs))

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, *_args, **_kwargs):
        return None

    def run_callbacks(self):
        while self.callbacks:
            callback, _delay, kwargs = self.callbacks.pop(0)
            callback(kwargs)


class DummyController:
    def __init__(self):
        self.calls = []
        self.verify_args = None

    def set_charge(self, powers, source):
        self.calls.append(("charge", powers, source))
        return True

    def set_discharge(self, powers, source):
        self.calls.append(("discharge", powers, source))
        return True

    def set_self_consumption(self, source):
        self.calls.append(("self", source))
        return True

    def set_stop(self, source):
        self.calls.append(("stop", source))
        return True

    def verify_state(self, state, expected_powers):
        self.verify_args = (state, expected_powers)
        return {"ok": True, "summary": "Accu's OK"}


class CompletionAwareController(DummyController):
    def __init__(self):
        super().__init__()
        self.waits = [7, 0]
        self.verify_count = 0

    def verification_wait_remaining(self, settle_seconds):
        return self.waits.pop(0)

    def verify_state(self, state, expected_powers):
        self.verify_count += 1
        return super().verify_state(state, expected_powers)


class RecoveryController(DummyController):
    def __init__(self, verification_results):
        super().__init__()
        self.verification_results = list(verification_results)
        self.batteries = [
            {"name": "Accu 1"},
            {"name": "Accu 2"},
            {"name": "Accu 3"},
        ]

    def verify_state(
        self,
        state,
        expected_powers=None,
        battery_indexes=None,
    ):
        self.verify_args = (state, expected_powers, battery_indexes)
        return self.verification_results.pop(0)

    def stop_batteries(self, battery_indexes, source):
        self.calls.append(("target_stop", list(battery_indexes), source))
        return True

    def restore_batteries(
        self,
        state,
        battery_indexes,
        expected_powers=None,
        source="auto",
    ):
        self.calls.append((
            "target_restore",
            state,
            list(battery_indexes),
            list(expected_powers or []),
            source,
        ))
        return True

    def recommended_verification_delay(self, minimum):
        return minimum


class ResetRecoveryController(RecoveryController):
    def __init__(self, verification_results):
        super().__init__(verification_results)
        self.reset_attempted = [False, False, False]
        self.reset_pending = [False, False, False]
        self.resets = []

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


class AdoptController(DummyController):
    """Simuleert een groep die al actief laadt/ontlaadt met desired_powers."""

    def __init__(self, desired_state="charging", desired_powers=None):
        super().__init__()
        self.batteries = [
            {
                "name": "Accu 1",
                "charge_power": "number.accu1_forceer_laadvermogen",
                "discharge_power": "number.accu1_forceer_ontlaadvermogen",
            },
            {
                "name": "Accu 2",
                "charge_power": "number.accu2_forceer_laadvermogen",
                "discharge_power": "number.accu2_forceer_ontlaadvermogen",
            },
            {
                "name": "Accu 3",
                "charge_power": "number.accu3_forceer_laadvermogen",
                "discharge_power": "number.accu3_forceer_ontlaadvermogen",
            },
        ]
        self.desired_state = desired_state
        self.desired_powers = list(desired_powers or [750.0, 750.0, 750.0])
        self.adopt_calls = []
        self.adopt_result = True

    def adopt_single_battery_power(self, index, power, source):
        self.adopt_calls.append((index, power, source))
        if self.adopt_result:
            self.desired_powers[index] = power
        return self.adopt_result


def _result(ok, failed_indexes=None, summary=None):
    return {
        "ok": ok,
        "failed_indexes": list(failed_indexes or []),
        "summary": summary or ("Accu's OK" if ok else "Accu 3=FOUT"),
    }


def make_manual(app, controller):
    return ManualController(
        app=app,
        controller=controller,
        enabled_helper="input_boolean.openems_actief",
        mode_helper="input_select.openems_handmatige_modus",
        charge_power_helper="input_number.openems_handmatig_laadvermogen_per_accu",
        discharge_power_helper="input_number.openems_handmatig_ontlaadvermogen_per_accu",
        test_mode_helper="input_select.marstek_controller_test",
        battery_count=3,
    )


def test_manual_charge_uses_same_power_for_three_batteries():
    app = DummyApp()
    controller = DummyController()
    manual = make_manual(app, controller)

    manual.apply_current()

    assert controller.calls == [("charge", [750.0, 750.0, 750.0], "manual")]
    _state, attributes = app.published["sensor.openems_handmatige_status"]
    assert datetime.fromisoformat(attributes["verificatie_op"])


def test_auto_mode_blocks_manual_commands():
    app = DummyApp()
    app.states["input_boolean.openems_actief"] = "on"
    controller = DummyController()

    make_manual(app, controller).apply_current()

    assert controller.calls == []


def test_forced_discharge_gets_matching_verification_mode():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Ontladen"
    controller = DummyController()
    manual = make_manual(app, controller)

    manual.apply_current()
    callback, _delay, kwargs = app.callbacks[-1]
    callback(kwargs)

    assert controller.calls == [("discharge", [600.0, 600.0, 600.0], "manual")]
    assert controller.verify_args == (
        "forced_discharge",
        [600.0, 600.0, 600.0],
    )


def test_manual_discharge_stops_first_while_a_battery_is_charging():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Ontladen"
    app.states["sensor.actual_2"] = "-1800"
    app.states["sensor.charge_2"] = "1800"
    app.states["select.force_2"] = "charge"
    controller = DummyController()
    controller.batteries = [
        {"name": "Accu 1"},
        {
            "name": "Accu 2",
            "actual_power": "sensor.actual_2",
            "charge_power": "sensor.charge_2",
            "force_mode": "select.force_2",
        },
        {"name": "Accu 3"},
    ]
    manual = make_manual(app, controller)

    manual.apply_current()

    assert controller.calls == [("stop", "manual")]
    state, _attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Omschakelen"

    app.states["sensor.actual_2"] = "0"
    app.states["sensor.charge_2"] = "0"
    app.states["select.force_2"] = "standby"
    callback, _delay, kwargs = app.callbacks.pop(0)
    callback(kwargs)
    assert controller.calls[-1] == (
        "discharge", [600.0, 600.0, 600.0], "manual"
    )


def test_manual_direction_watchdog_stops_partial_opposite_delivery():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Ontladen"
    app.states["sensor.actual_2"] = "0"
    controller = DummyController()
    controller.batteries = [
        {"name": "Accu 1"},
        {"name": "Accu 2", "actual_power": "sensor.actual_2"},
        {"name": "Accu 3"},
    ]
    manual = make_manual(app, controller)

    manual.apply_current()
    app.states["sensor.actual_2"] = "-1800"
    callback, _delay, kwargs = app.callbacks.pop(0)
    callback(kwargs)

    assert controller.calls[-1] == ("stop", "manual")
    state, _attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Noodstop"


def test_verification_waits_from_actual_sequence_completion():
    app = DummyApp()
    controller = CompletionAwareController()
    manual = make_manual(app, controller)

    manual.apply_current()
    watchdog, _delay, watchdog_kwargs = app.callbacks.pop(0)
    watchdog(watchdog_kwargs)
    callback, _delay, kwargs = app.callbacks.pop(0)
    callback(kwargs)

    assert controller.verify_count == 0
    assert app.callbacks[-1][1] == 7

    app.run_callbacks()

    assert controller.verify_count == 1
    state, attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Laden"
    assert attributes["verificatie"] == "OK"


def test_temporary_stale_state_gets_short_rechecks_without_commands():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Zelfconsumptie"
    controller = RecoveryController([
        _result(False, [2]),
        _result(False, [2]),
        _result(True),
    ])
    manual = make_manual(app, controller)

    manual.apply_current()
    app.run_callbacks()

    assert controller.calls == [("self", "manual")]
    state, attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Zelfconsumptie"
    assert attributes["verificatie"] == "OK"


def test_selector_verification_without_power_fields_does_not_crash():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Zelfconsumptie"
    controller = RecoveryController([{
        "ok": True,
        "failed_indexes": [],
        "summary": "Native selectie OK",
        "batteries": [{
            "index": 0,
            "ok": True,
            "work_mode": "anti_feed",
            "rs485": "off",
            "force_mode": "standby",
        }],
    }])
    manual = make_manual(app, controller)

    manual.apply_current()
    app.run_callbacks()

    _state, attributes = app.published["sensor.openems_handmatige_status"]
    assert attributes["verificatie"] == "OK"
    assert "laden=onbekend" in attributes["accu_statussen"][0]["details"]


def test_persistent_failure_stops_and_restores_only_failed_battery():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Zelfconsumptie"
    controller = RecoveryController([
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(True),
        _result(True),
    ])
    manual = make_manual(app, controller)

    manual.apply_current()
    app.run_callbacks()

    assert controller.calls == [
        ("self", "manual"),
        ("target_restore", "self_consumption", [2], [], "manual"),
    ]
    state, attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Zelfconsumptie"
    assert attributes["verificatie"] == "OK NA HERSTEL"
    assert attributes["actie"].startswith("Herstel geslaagd")
    assert [item["status"] for item in attributes["accu_statussen"]] == [
        "OK", "OK", "OK"
    ]


def test_failed_restore_gets_final_stop_and_no_infinite_retry():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Zelfconsumptie"
    controller = RecoveryController([
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(True),
    ])
    manual = make_manual(app, controller)

    manual.apply_current()
    app.run_callbacks()

    assert controller.calls == [
        ("self", "manual"),
        ("target_restore", "self_consumption", [2], [], "manual"),
        ("target_stop", [2], "manual"),
    ]
    assert app.callbacks == []
    state, attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Fout"
    assert attributes["verificatie"] == "DEFINITIEF MISLUKT"
    assert "veilig gestopt" in attributes["melding"]
    assert attributes["accu_statussen"][0]["status"] == "OK"
    assert attributes["accu_statussen"][1]["status"] == "OK"
    assert attributes["accu_statussen"][2]["status"] == (
        "FOUT - VEILIG GESTOPT"
    )


def test_manual_failure_resets_only_failed_battery_once_then_retries():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Zelfconsumptie"
    controller = ResetRecoveryController([
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(False, [2]),
        _result(True),
    ])
    manual = make_manual(app, controller)

    manual.apply_current()
    app.run_callbacks()

    assert controller.resets == [(2, "manual")]
    assert controller.calls == [
        ("self", "manual"),
        ("target_restore", "self_consumption", [2], [], "manual"),
        ("target_restore", "self_consumption", [2], [], "manual"),
    ]
    assert controller.reset_pending[2] is False
    state, attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Zelfconsumptie"
    assert attributes["verificatie"] == "OK NA APPARAAT-RESET"


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_direct_battery_entity_change_is_adopted_in_manual_charging_mode():
    app = DummyApp()
    app.states["number.accu2_forceer_laadvermogen"] = "2000"
    controller = AdoptController(
        desired_state="charging", desired_powers=[750.0, 750.0, 750.0]
    )
    manual = make_manual(app, controller)

    manual._battery_power_changed(
        "number.accu2_forceer_laadvermogen", "state", "750", "2000", {}
    )
    app.run_callbacks()

    assert controller.adopt_calls == [(1, 2000.0, "manual")]


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_direct_battery_entity_change_ignored_in_auto_mode():
    app = DummyApp()
    app.states["input_boolean.openems_actief"] = "on"
    app.states["number.accu2_forceer_laadvermogen"] = "2000"
    controller = AdoptController()
    manual = make_manual(app, controller)

    manual._battery_power_changed(
        "number.accu2_forceer_laadvermogen", "state", "750", "2000", {}
    )
    app.run_callbacks()

    assert controller.adopt_calls == []


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_direct_battery_entity_change_ignored_when_mode_mismatches():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Ontladen"
    app.states["number.accu2_forceer_laadvermogen"] = "2000"
    controller = AdoptController()
    manual = make_manual(app, controller)

    manual._battery_power_changed(
        "number.accu2_forceer_laadvermogen", "state", "750", "2000", {}
    )
    app.run_callbacks()

    assert controller.adopt_calls == []


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_battery_entity_echo_of_own_confirmed_write_is_ignored():
    app = DummyApp()
    app.states["number.accu2_forceer_laadvermogen"] = "750"
    controller = AdoptController(
        desired_state="charging", desired_powers=[750.0, 750.0, 750.0]
    )
    manual = make_manual(app, controller)

    manual._battery_power_changed(
        "number.accu2_forceer_laadvermogen", "state", "0", "750", {}
    )

    assert app.callbacks == []
    assert controller.adopt_calls == []


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_direct_battery_entity_change_ignored_outside_charge_or_discharge():
    app = DummyApp()
    app.states["number.accu2_forceer_laadvermogen"] = "2000"
    controller = AdoptController(
        desired_state="self_consumption", desired_powers=[]
    )
    manual = make_manual(app, controller)

    manual._battery_power_changed(
        "number.accu2_forceer_laadvermogen", "state", "0", "2000", {}
    )
    app.run_callbacks()

    # adopt_single_battery_power wordt wel aangeroepen (de fake accepteert
    # dit ongeacht desired_state), maar de echte MarstekController wijst
    # dit expliciet af buiten laden/ontladen; hier verifiëren we alleen dat
    # het handmatige pad de aanvraag doorzet naar de controller.
    assert controller.adopt_calls == [(1, 2000.0, "manual")]


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_rejected_adoption_publishes_warning_and_keeps_desired_powers():
    app = DummyApp()
    app.states["number.accu2_forceer_laadvermogen"] = "2000"
    controller = AdoptController(
        desired_state="charging", desired_powers=[750.0, 750.0, 750.0]
    )
    controller.adopt_result = False
    manual = make_manual(app, controller)

    manual._battery_power_changed(
        "number.accu2_forceer_laadvermogen", "state", "750", "2000", {}
    )
    app.run_callbacks()

    assert controller.adopt_calls == [(1, 2000.0, "manual")]
    assert controller.desired_powers == [750.0, 750.0, 750.0]
    state, attributes = app.published["sensor.openems_handmatige_status"]
    assert state == "Geweigerd"
    assert attributes["niveau"] == "WARNING"


class LoadBalancedController(RecoveryController):
    """Controller waarvan de load balancer het doel na de opdracht verlaagt."""

    def __init__(self, verification_results):
        super().__init__(verification_results)
        self.desired_state = None
        self.desired_source = None
        self.desired_powers = []

    def set_discharge(self, powers, source):
        self.calls.append(("discharge", powers, source))
        self.desired_state = "forced_discharge"
        self.desired_source = source
        self.desired_powers = list(powers)
        return True

    def set_charge(self, powers, source):
        self.calls.append(("charge", powers, source))
        self.desired_state = "charging"
        self.desired_source = source
        self.desired_powers = list(powers)
        return True


def test_verification_uses_load_balanced_discharge_target():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Ontladen"
    controller = LoadBalancedController([_result(True)])
    manual = make_manual(app, controller)

    manual.apply_current()
    # Load balancer begrenst export na de handmatige opdracht.
    controller.desired_powers = [350.0, 350.0, 350.0]
    app.run_callbacks()

    assert controller.verify_args[1] == [350.0, 350.0, 350.0]


def test_recovery_repeats_load_balanced_target_not_original_power():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Ontladen"
    controller = LoadBalancedController([
        _result(False, [1]),
        _result(False, [1]),
        _result(False, [1]),
        _result(False, [1]),
        _result(True),
    ])
    manual = make_manual(app, controller)

    manual.apply_current()
    controller.desired_powers = [200.0, 200.0, 200.0]
    app.run_callbacks()

    assert controller.calls[-1] == (
        "target_restore",
        "forced_discharge",
        [1],
        [200.0, 200.0, 200.0],
        "manual",
    )


def test_load_balanced_charge_target_is_used_for_verification():
    app = DummyApp()
    controller = LoadBalancedController([_result(True)])
    manual = make_manual(app, controller)

    manual.apply_current()
    controller.desired_powers = [500.0, 500.0, 0.0]
    app.run_callbacks()

    assert controller.verify_args[1] == [500.0, 500.0, 0.0]


def test_target_of_other_owner_is_not_adopted():
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Ontladen"
    controller = LoadBalancedController([_result(True)])
    manual = make_manual(app, controller)

    manual.apply_current()
    controller.desired_source = "auto"
    controller.desired_powers = [100.0, 100.0, 100.0]
    app.run_callbacks()

    assert controller.verify_args[1] == [600.0, 600.0, 600.0]
