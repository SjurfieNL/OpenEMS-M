import time

from marstek_controller import MarstekController
from manual_controller import ManualController
from runtime_safety_monitor import RuntimeSafetyMonitor
from test_manual_controller import DummyApp, DummyController, make_manual
import test_runtime_safety_monitor as safety


class ReleaseController(DummyController):
    """DummyController met release_control en verify op 'stopped'."""

    def __init__(self, ok=True):
        super().__init__()
        self.ok = ok
        self.released = []
        self.restored = []
        self.desired_powers = [0.0, 0.0, 0.0]

    def release_control(self, source="manual"):
        self.released.append(source)

    def restore_batteries(self, *args, **kwargs):
        self.restored.append(args)
        return True

    def verify_state(self, state, expected_powers=None, battery_indexes=None):
        return {
            "ok": self.ok, "summary": "Accu's OK" if self.ok else "Accu 2=FOUT",
            "failed_indexes": [] if self.ok else [1], "batteries": [],
        }


def stop_manual(ok=True):
    app = DummyApp()
    app.states["input_select.openems_handmatige_modus"] = "Stop"
    controller = ReleaseController(ok=ok)
    manual = make_manual(app, controller)
    return app, controller, manual


def test_stop_sends_one_stop_verifies_then_releases():
    app, controller, manual = stop_manual(ok=True)
    manual.apply_current()
    assert controller.calls == [("stop", "manual")]
    app.run_callbacks()
    assert controller.released == ["manual"]
    state, attrs = app.published["sensor.openems_handmatige_status"]
    assert state == "Stop" and attrs["verificatie"] == "LOSGELATEN"
    assert controller.calls == [("stop", "manual")]  # niets extra


def test_failed_stop_check_does_not_recover_but_releases():
    app, controller, manual = stop_manual(ok=False)
    manual.apply_current()
    app.run_callbacks()
    assert controller.restored == []
    assert controller.released == ["manual"]
    assert controller.calls == [("stop", "manual")]


def test_other_modes_do_not_release():
    app = DummyApp()
    controller = ReleaseController()
    manual = make_manual(app, controller)  # modus Laden
    manual.apply_current()
    app.run_callbacks()
    assert controller.released == []


def test_start_in_stop_releases_without_sending_commands():
    app, controller, manual = stop_manual()
    manual.start()
    assert controller.released == ["manual"]
    assert controller.calls == []


def test_start_in_other_mode_or_auto_does_not_release():
    app = DummyApp()
    controller = ReleaseController()
    make_manual(app, controller).start()
    assert controller.released == []
    app, controller, manual = stop_manual()
    app.states["input_boolean.openems_actief"] = "on"
    manual.start()
    assert controller.released == []


def real_controller(enabled=False, test_enabled=True):
    from test_marstek_controller import BATTERIES, DummyApp as CtrlApp
    app = CtrlApp()
    return app, MarstekController(
        app, BATTERIES, enabled=enabled, test_enabled=test_enabled
    )


def test_released_controller_blocks_background_sources():
    app, controller = real_controller()
    controller.release_control(source="manual")
    assert controller.desired_state == "released"
    for source in ("coordinated", "selector", "recovery", "auto", "export"):
        assert controller._can_execute("laden", [100], source) is False
    assert controller.set_charge([500], source="coordinated") is False
    assert controller.desired_state == "released"


def test_released_controller_accepts_manual_and_test():
    app, controller = real_controller()
    controller.release_control(source="manual")
    assert controller._can_execute("laden", [100], "manual") is True
    assert controller._can_execute("laden", [100], "test") is True
    assert controller.set_charge([500], source="manual") is True
    assert controller.desired_state == "charging"


def test_enabling_auto_takes_control_back():
    app, controller = real_controller()
    controller.release_control(source="manual")
    controller.set_enabled(True)
    assert controller.desired_state == "stopped"
    assert controller._can_execute("laden", [100], "auto") is True


def test_not_released_behaviour_unchanged():
    app, controller = real_controller()
    assert controller._can_execute("laden", [100], "coordinated") is True
    assert controller._can_execute("laden", [100], "auto") is False


def test_safety_monitor_ignores_released_state():
    app = safety.App()
    controller = safety.Controller(app)
    controller.desired_state = "released"
    controller.desired_powers = [0, 0, 0]
    controller.desired_source = "manual"
    controller.desired_since = time.monotonic() - 60
    app.states["sensor.power_1"] = "-3000"  # accu laadt via Marstek-app
    monitor = RuntimeSafetyMonitor(app, controller)
    for _ in range(5):
        monitor.tick()
    assert controller.restored == [] and controller.isolated == []
    assert controller.stopped == [] and app.callbacks == []
