from datetime import timezone

from grid_status_monitor import GridStatusMonitor


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Events:
    def __init__(self):
        self.items = []

    def record(self, *args, **kwargs):
        self.items.append((args, kwargs))


class Controller:
    def __init__(self):
        self.suspended = 0
        self.resumed = 0

    def suspend_for_grid_outage(self):
        self.suspended += 1

    def resume_after_grid_outage(self):
        self.resumed += 1


class Scheduler:
    def __init__(self):
        self.stops = []

    def mark_external_stop(self, reason):
        self.stops.append(reason)


class App:
    def __init__(self, batteries):
        self.states = {}
        for battery in batteries:
            self.states[battery["ac_voltage"]] = "230"
            self.states[battery["inverter_state"]] = "Bypass"
        self.published = {}
        self.logs = []
        self.controller = Controller()
        self.scheduler = Scheduler()
        self.restored = 0

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, **kwargs):
        self.published[entity] = kwargs

    def log(self, *args, **kwargs):
        self.logs.append((args, kwargs))

    def grid_status_restored(self):
        self.restored += 1


def batteries():
    return [
        {
            "name": f"Accu {index}",
            "ac_voltage": f"sensor.accu{index}_ac_voltage",
            "inverter_state": f"sensor.accu{index}_inverter",
        }
        for index in (1, 2, 3)
    ]


def monitor():
    items = batteries()
    app = App(items)
    clock = Clock()
    events = Events()
    guard = GridStatusMonitor(
        app, items, timezone.utc, event_log=events, clock=clock
    )
    return guard, app, clock, events


def settle(guard, clock, seconds):
    guard.tick()
    clock.advance(seconds)
    guard.tick()


def test_normal_grid_requires_stable_207_to_253_volts():
    guard, app, clock, _events = monitor()

    settle(guard, clock, 10)

    assert guard.status == "normal"
    assert guard.allows_charging() is True
    assert guard.allows_export() is True
    assert app.published[guard.GRID_PRESENT_SENSOR]["state"] == "on"


def test_high_voltage_blocks_export_but_keeps_charging_available():
    guard, app, clock, _events = monitor()
    settle(guard, clock, 10)
    for battery in guard.batteries:
        app.states[battery["ac_voltage"]] = "254"

    settle(guard, clock, 2)

    assert guard.status == "high"
    assert guard.allows_charging() is True
    assert guard.allows_export() is False
    assert guard.allows_controller_action("laden") is True
    assert guard.allows_controller_action("ontladen") is False


def test_low_voltage_blocks_charging_and_planned_export():
    guard, app, clock, _events = monitor()
    settle(guard, clock, 10)
    for battery in guard.batteries:
        app.states[battery["ac_voltage"]] = "206"

    settle(guard, clock, 2)

    assert guard.status == "low"
    assert guard.allows_charging() is False
    assert guard.allows_export() is False


def test_two_zero_voltage_readings_confirm_outage_and_log_each_battery():
    guard, app, clock, events = monitor()
    settle(guard, clock, 10)
    for battery in guard.batteries[:2]:
        app.states[battery["ac_voltage"]] = "0"

    settle(guard, clock, 2)

    assert guard.status == "off_grid"
    assert app.controller.suspended == 1
    assert app.published[guard.OUTAGE_SENSOR]["state"] == "on"
    battery_events = [
        item for item in events.items
        if item[0][1] == "Netuitval gemeld"
    ]
    assert len(battery_events) == 2
    assert {item[1]["battery_name"] for item in battery_events} == {
        "Accu 1", "Accu 2"
    }
    assert any(item[0][1] == "Netuitval bevestigd" for item in events.items)


def test_backup_mode_is_sufficient_without_zero_voltage():
    guard, app, clock, _events = monitor()
    settle(guard, clock, 10)
    for battery in guard.batteries[:2]:
        app.states[battery["inverter_state"]] = "Backup Mode"

    settle(guard, clock, 2)

    assert guard.status == "off_grid"


def test_outage_only_recovers_after_thirty_seconds_healthy_grid():
    guard, app, clock, events = monitor()
    settle(guard, clock, 10)
    for battery in guard.batteries:
        app.states[battery["ac_voltage"]] = "0"
    settle(guard, clock, 2)
    for battery in guard.batteries:
        app.states[battery["ac_voltage"]] = "230"

    settle(guard, clock, 29)
    assert guard.status == "off_grid"
    clock.advance(1)
    guard.tick()

    assert guard.status == "normal"
    assert app.controller.resumed == 1
    assert app.restored == 1
    assert any(item[0][1] == "Net hersteld" for item in events.items)


def test_outage_does_not_release_into_abnormal_voltage():
    guard, app, clock, _events = monitor()
    settle(guard, clock, 10)
    for battery in guard.batteries:
        app.states[battery["ac_voltage"]] = "0"
    settle(guard, clock, 2)
    for battery in guard.batteries:
        app.states[battery["ac_voltage"]] = "254"

    settle(guard, clock, 120)

    assert guard.status == "off_grid"
    assert app.controller.resumed == 0


def test_duration_format_switches_from_seconds_to_minutes_and_hours():
    assert GridStatusMonitor.format_duration(42) == "42 seconden"
    assert GridStatusMonitor.format_duration(60) == "1 minuut"
    assert GridStatusMonitor.format_duration(17 * 60) == "17 minuten"
    assert GridStatusMonitor.format_duration(64 * 60) == "1 uur en 4 minuten"
