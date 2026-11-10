from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from efficient_battery_selector import EfficientBatterySelector
from ems_config import BATTERY_DISTRIBUTION_HELPER


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _Events:
    def __init__(self):
        self.items = []

    def record(self, *args, **kwargs):
        self.items.append((args, kwargs))


class _App:
    def __init__(self, batteries, enabled=True):
        self.states = {
            BATTERY_DISTRIBUTION_HELPER: (
                EfficientBatterySelector.EFFICIENT_OPTION
                if enabled else "Alle accu’s verdelen"
            )
        }
        for index, battery in enumerate(batteries):
            self.states[battery["cycles"]] = str(index + 1)
            self.states[battery["total_charging_energy"]] = str(100 + index)
            self.states[battery["total_discharging_energy"]] = str(80 + index)
            self.states[battery["temperature"]] = str(30 + index)
        self.published = {}
        self.logs = []
        self.runtime_event_log = _Events()

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, **kwargs):
        self.published[entity] = kwargs

    def log(self, *args, **kwargs):
        self.logs.append((args, kwargs))


class _NativeController:
    def __init__(self):
        self.selections = []
        self.invalidations = []
        self.repairs = []

    def invalidate_last_command(self, reason):
        self.invalidations.append(reason)

    def set_native_self_consumption_selection(
        self, indexes, source="selector"
    ):
        self.selections.append((tuple(indexes), source))
        return True

    def recommended_verification_delay(self, minimum=0):
        return minimum

    def atomic_batch_pending(self):
        return False

    def repair_native_self_consumption_selection(
        self, active_indexes, failed_indexes, source="selector"
    ):
        self.repairs.append((
            tuple(active_indexes), tuple(failed_indexes), source
        ))
        return True


class _NativeSettings:
    @staticmethod
    def grid_entity():
        return "sensor.grid"

    @staticmethod
    def grid_import_sign():
        return 1.0


class _NativeGrid:
    def __init__(self, power):
        self.power = power

    def corrected_grid_power(self):
        return self.power


class _NativeBatteryManager:
    def __init__(self, state):
        self.state = state

    def read(self):
        return self.state


class _Health:
    def __init__(self, unavailable=()):
        self.unavailable = set(unavailable)

    def is_available(self, index):
        return index not in self.unavailable


class _QuarantineHealth(_Health):
    def __init__(self):
        super().__init__()
        self.quarantines = []

    def quarantine(self, index, reason, attempts=0):
        self.unavailable.add(index)
        self.quarantines.append((index, reason, attempts))


def _batteries(count=3):
    return [
        {
            "name": f"Accu {index + 1}",
            "cycles": f"sensor.accu{index + 1}_cycles",
            "total_charging_energy": (
                f"sensor.accu{index + 1}_total_charging_energy"
            ),
            "total_discharging_energy": (
                f"sensor.accu{index + 1}_total_discharging_energy"
            ),
            "temperature": f"sensor.accu{index + 1}_temperature",
        }
        for index in range(count)
    ]


def _battery_state(socs=None, maximums=None, count=3):
    return SimpleNamespace(
        soc_values=list(socs or [100] * count),
        max_discharge_values=list(maximums or [2500] * count),
    )


def _selector(enabled=True, count=3):
    temporary = TemporaryDirectory()
    batteries = _batteries(count)
    app = _App(batteries, enabled=enabled)
    clock = _Clock()
    selector = EfficientBatterySelector(
        app,
        batteries,
        data_file=Path(temporary.name) / "selector.json",
        clock=clock,
    )
    selector._test_temporary = temporary
    return selector, app, clock


def _native_selector(grid_power=400):
    temporary = TemporaryDirectory()
    batteries = _batteries()
    for index, battery in enumerate(batteries):
        battery.update({
            "actual_power": f"sensor.accu{index + 1}_actual",
            "work_mode": f"select.accu{index + 1}_work",
            "rs485_control": f"switch.accu{index + 1}_rs485",
            "force_mode": f"select.accu{index + 1}_force",
            "charge_power": f"number.accu{index + 1}_charge",
            "discharge_power": f"number.accu{index + 1}_discharge",
        })
    app = _App(batteries)
    app.states["input_number.marstek_minimum_soc"] = "12"
    for battery in batteries:
        app.states[battery["actual_power"]] = "0"
        app.states[battery["work_mode"]] = "manual"
        app.states[battery["rs485_control"]] = "on"
        app.states[battery["force_mode"]] = "stop"
        app.states[battery["charge_power"]] = "0"
        app.states[battery["discharge_power"]] = "0"
    state = _battery_state()
    controller = _NativeController()
    clock = _Clock()
    selector = EfficientBatterySelector(
        app,
        batteries,
        data_file=Path(temporary.name) / "selector.json",
        clock=clock,
        controller=controller,
        battery_manager=_NativeBatteryManager(state),
        settings=_NativeSettings(),
        grid_calibrator=_NativeGrid(grid_power),
    )
    selector._test_temporary = temporary
    return selector, app, controller, clock, state


def test_legacy_mode_keeps_existing_distribution():
    selector, _app, _clock = _selector(enabled=False)
    assert selector.allocate(500, _battery_state(), 12) is None


def test_selection_uses_soc_round_then_cycles_soc_and_throughput():
    selector, app, _clock = _selector()
    batteries = selector.batteries
    app.states[batteries[0]["cycles"]] = "5"
    app.states[batteries[1]["cycles"]] = "3"
    app.states[batteries[2]["cycles"]] = "3"
    app.states[batteries[1]["total_charging_energy"]] = "120"
    app.states[batteries[2]["total_charging_energy"]] = "90"
    state = _battery_state([90, 80, 90])

    powers = selector.allocate(500, state, 12)

    assert powers == [0.0, 0.0, 500.0]
    assert selector.active_indexes == [2]


def test_restart_selection_finishes_highest_soc_round_first():
    selector, app, _clock = _selector()
    batteries = selector.batteries
    app.states[batteries[0]["cycles"]] = "5"
    app.states[batteries[1]["cycles"]] = "3"
    app.states[batteries[2]["cycles"]] = "1"

    selector.allocate(500, _battery_state([65, 16, 13]), 12)

    assert selector.active_indexes == [0]
    assert selector.targets[0] == 50
    assert "hoogste SOC-ronde" in selector.last_reason


def test_each_soc_round_uses_lowest_cycles_within_that_round():
    selector, app, _clock = _selector()
    batteries = selector.batteries
    app.states[batteries[0]["cycles"]] = "5"
    app.states[batteries[1]["cycles"]] = "3"
    app.states[batteries[2]["cycles"]] = "1"
    state = _battery_state([78, 58, 46])

    selector.allocate(500, state, 12)
    assert selector.active_indexes == [1]
    assert selector.targets[1] == 50

    state.soc_values[1] = 50
    selector.allocate(500, state, 12)
    assert selector.active_indexes == [0]
    assert selector.targets[0] == 50

    state.soc_values[0] = 50
    selector.allocate(500, state, 12)
    assert selector.active_indexes == [2]
    assert selector.targets[2] == 25


def test_deactivate_and_reactivate_reconstructs_soc_round_from_live_soc():
    selector, app, _clock = _selector()
    batteries = selector.batteries
    app.states[batteries[0]["cycles"]] = "5"
    app.states[batteries[1]["cycles"]] = "3"
    app.states[batteries[2]["cycles"]] = "1"
    state = _battery_state([65, 16, 13])

    selector.allocate(500, state, 12)
    selector.deactivate()
    selector.allocate(500, state, 12)

    assert selector.active_indexes == [0]
    assert selector.targets[0] == 50


def test_soc_targets_rotate_50_then_reselect_for_25_round():
    selector, _app, _clock = _selector()
    state = _battery_state()
    selector.allocate(500, state, 12)
    assert selector.active_indexes == [0]
    assert selector.targets[0] == 50

    state.soc_values[0] = 50
    selector.allocate(500, state, 12)
    assert selector.active_indexes == [1]

    state.soc_values[1] = 50
    selector.allocate(500, state, 12)
    assert selector.active_indexes == [2]

    state.soc_values[2] = 50
    selector.allocate(500, state, 12)
    assert selector.active_indexes == [0]
    assert selector.targets[0] == 25


def test_second_battery_only_after_two_minutes_above_threshold():
    selector, _app, clock = _selector()
    state = _battery_state()
    selector.allocate(1700, state, 12)
    clock.advance(119)
    selector.allocate(1700, state, 12)
    assert len(selector.active_indexes) == 1

    clock.advance(1)
    powers = selector.allocate(1700, state, 12)
    assert len(selector.active_indexes) == 2
    assert sorted(value for value in powers if value) == [850.0, 850.0]


def test_short_import_peak_does_not_add_a_battery():
    selector, _app, clock = _selector()
    state = _battery_state()
    selector.allocate(1700, state, 12)
    clock.advance(5)
    selector.allocate(400, state, 12)
    clock.advance(200)
    selector.allocate(400, state, 12)
    assert len(selector.active_indexes) == 1


def test_unchanged_selection_is_not_logged_every_control_cycle():
    selector, app, clock = _selector()
    state = _battery_state()

    selector.allocate(900, state, 12)
    first_count = sum(
        1 for args, _kwargs in app.runtime_event_log.items
        if len(args) > 1 and args[1] == "Accu geselecteerd"
    )
    clock.advance(5)
    selector.allocate(900, state, 12)
    second_count = sum(
        1 for args, _kwargs in app.runtime_event_log.items
        if len(args) > 1 and args[1] == "Accu geselecteerd"
    )

    assert first_count == 1
    assert second_count == first_count


def test_downscale_preserves_original_primary():
    selector, _app, clock = _selector()
    state = _battery_state()
    selector.allocate(1700, state, 12)
    primary = selector.active_indexes[0]
    clock.advance(120)
    selector.allocate(1700, state, 12)
    assert len(selector.active_indexes) == 2

    selector.allocate(1200, state, 12)
    clock.advance(599)
    selector.allocate(1200, state, 12)
    assert len(selector.active_indexes) == 2
    clock.advance(1)
    selector.allocate(1200, state, 12)
    assert selector.active_indexes == [primary]


def test_fourth_battery_only_after_two_minutes_above_fourth_threshold():
    selector, _app, clock = _selector(count=4)
    state = _battery_state(count=4)
    selector.allocate(4600, state, 12)
    # Het vermogen vraagt direct minstens twee accu's; de vierde pas na 120 s.
    assert len(selector.active_indexes) == 2
    clock.advance(119)
    selector.allocate(4600, state, 12)
    assert len(selector.active_indexes) < 4

    clock.advance(1)
    powers = selector.allocate(4600, state, 12)
    assert len(selector.active_indexes) == 4
    assert selector.selected_count == 4
    assert abs(sum(powers) - 4600) < 1
    assert max(powers) <= 2500


def test_fourth_battery_not_added_between_third_and_fourth_threshold():
    selector, _app, clock = _selector(count=4)
    state = _battery_state(count=4)
    selector.allocate(4400, state, 12)
    clock.advance(120)
    selector.allocate(4400, state, 12)
    clock.advance(600)
    selector.allocate(4400, state, 12)
    assert len(selector.active_indexes) == 3


def test_four_batteries_downscale_one_step_at_a_time():
    selector, _app, clock = _selector(count=4)
    state = _battery_state(count=4)
    selector.allocate(4600, state, 12)
    clock.advance(120)
    selector.allocate(4600, state, 12)
    assert len(selector.active_indexes) == 4

    # Boven de uitschakeldrempel (3900 W) blijft alles actief.
    selector.allocate(4000, state, 12)
    clock.advance(700)
    selector.allocate(4000, state, 12)
    assert len(selector.active_indexes) == 4

    # Eronder pas na 600 s, en eerst terug naar drie accu's.
    selector.allocate(3500, state, 12)
    clock.advance(599)
    selector.allocate(3500, state, 12)
    assert len(selector.active_indexes) == 4
    clock.advance(1)
    selector.allocate(3500, state, 12)
    assert len(selector.active_indexes) == 3


def test_four_batteries_low_demand_still_uses_one_battery():
    selector, _app, _clock = _selector(count=4)
    powers = selector.allocate(900, _battery_state(count=4), 12)
    assert len(selector.active_indexes) == 1
    assert selector.selected_count == 1
    assert sum(powers) == 900
    assert len(powers) == 4


def test_idle_returns_to_one_selected_battery():
    selector, _app, clock = _selector()
    state = _battery_state()
    selector.allocate(1700, state, 12)
    clock.advance(120)
    selector.allocate(1700, state, 12)
    assert len(selector.active_indexes) == 2

    selector.allocate(0, state, 12)
    assert len(selector.active_indexes) == 1
    assert selector.selected_count == 1


def test_insufficient_active_capacity_scales_up_immediately():
    selector, _app, _clock = _selector()
    powers = selector.allocate(
        1400,
        _battery_state(maximums=[500, 1000, 2500]),
        12,
    )
    assert len(selector.active_indexes) == 2
    assert sum(powers) == 1400


def test_raw_demand_controls_count_but_ramped_power_is_sent():
    selector, _app, _clock = _selector()
    powers = selector.allocate(
        400,
        _battery_state(),
        12,
        demand_power=1700,
    )
    assert sum(powers) == 400


def test_missing_metadata_falls_back_and_recovers_once():
    selector, app, _clock = _selector()
    cycles = selector.batteries[1]["cycles"]
    app.states[cycles] = "unavailable"
    assert selector.allocate(500, _battery_state(), 12) is None
    warning_count = len(app.runtime_event_log.items)
    assert warning_count == 1

    assert selector.allocate(500, _battery_state(), 12) is None
    assert len(app.runtime_event_log.items) == warning_count

    app.states[cycles] = "2"
    assert selector.allocate(500, _battery_state(), 12) is not None
    assert len(app.runtime_event_log.items) > warning_count


def test_native_selector_keeps_selected_battery_on_marstek_anti_feed():
    selector, app, controller, _clock, _state = _native_selector()

    assert selector.activate_native() is True

    assert selector.active_indexes == [0]
    assert controller.selections[-1] == ((0,), "selector")
    published = app.published[selector.STATUS_SENSOR]
    assert published["attributes"]["vermogensregeling"] == (
        "Marstek anti-feed"
    )


def test_export_selector_skips_a_temporarily_unavailable_battery():
    selector, app, _clock = _selector()
    app.battery_health_manager = _Health(unavailable=(0,))

    powers = selector.allocate_export(
        1500, _battery_state(), 12, desired_count=1
    )

    assert powers[0] == 0
    assert selector.active_indexes == [1]


def test_export_selector_uses_least_cycles_not_highest_soc():
    selector, app, _clock = _selector()
    batteries = selector.batteries
    app.states[batteries[0]["cycles"]] = "8"
    app.states[batteries[1]["cycles"]] = "2"
    app.states[batteries[2]["cycles"]] = "4"

    powers = selector.allocate_export(
        900, _battery_state([95, 35, 80]), 12, desired_count=1
    )

    assert powers == [0.0, 900.0, 0.0]
    assert selector.active_indexes == [1]


def test_native_selector_uses_battery_power_plus_grid_residual():
    selector, app, controller, clock, _state = _native_selector()
    selector.activate_native()
    app.states[selector.batteries[0]["actual_power"]] = "1200"
    selector.grid_calibrator.power = 500

    selector.tick_native()
    clock.advance(120)
    selector.tick_native()

    assert len(selector.active_indexes) == 2
    assert len(controller.selections[-1][0]) == 2
    assert selector.last_demand == 1700


def test_native_handoff_freezes_selection_while_sequence_is_pending():
    selector, app, controller, clock, _state = _native_selector()
    controller.pending = True
    controller.command_sequence_pending = lambda: controller.pending
    assert selector.activate_native() is True
    original = tuple(selector.active_indexes)
    app.states[selector.batteries[original[0]]["actual_power"]] = "1200"
    selector.grid_calibrator.power = 700

    clock.advance(20)
    selector.tick_native()

    assert tuple(selector.active_indexes) == original
    assert controller.selections == [(original, "selector")]


def test_native_handoff_waits_for_ct_delivery_without_repair_write():
    selector, app, controller, clock, _state = _native_selector(
        grid_power=650
    )
    controller.command_sequence_pending = lambda: False
    assert selector.activate_native() is True
    selected = selector.batteries[selector.active_indexes[0]]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "0"

    clock.advance(10)
    selector.tick_native()
    clock.advance(10)
    selector.tick_native()

    assert controller.repairs == []
    assert selector._native_transition_active is True

    app.states[selected["actual_power"]] = "600"
    clock.advance(2)
    selector.tick_native()

    assert selector._native_transition_active is False


def test_native_selector_falls_back_to_all_native_on_missing_metadata():
    selector, app, controller, _clock, _state = _native_selector()
    app.states[selector.batteries[1]["cycles"]] = "unavailable"

    assert selector.activate_native() is True

    assert controller.selections[-1][0] == (0, 1, 2)
    assert app.published[selector.STATUS_SENSOR]["state"] == (
        "Tijdelijk teruggevallen"
    )

    app.states[selector.batteries[1]["cycles"]] = "2"
    selector.tick_native()
    assert len(controller.selections[-1][0]) == 1


def test_native_verification_accepts_stale_mode_for_confirmed_force_stop():
    selector, app, _controller, _clock, _state = _native_selector()
    selector.native_active = True
    selector.active_indexes = [0]
    selected = selector.batteries[0]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"

    result = selector.verification_result()
    assert result["ok"] is True

    inactive = selector.batteries[1]
    app.states[inactive["rs485_control"]] = "off"
    app.states[inactive["work_mode"]] = "anti_feed"
    assert selector.verification_result()["ok"] is True


def test_native_verification_rejects_real_power_on_inactive_battery():
    selector, app, _controller, _clock, _state = _native_selector()
    selector.native_active = True
    selector.active_indexes = [0]
    selected = selector.batteries[0]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    inactive = selector.batteries[1]
    app.states[inactive["actual_power"]] = "175"

    result = selector.verification_result()

    assert result["ok"] is False
    assert "werkelijk=175 W" in result["summary"]


def test_native_verification_requires_physical_delivery_when_house_demands_power():
    selector, app, _controller, clock, _state = _native_selector(
        grid_power=650
    )
    assert selector.activate_native() is True
    selected = selector.batteries[selector.active_indexes[0]]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "0"

    result = selector.verification_result()

    assert result["ok"] is False
    assert result["pending"] is True
    assert "wacht op actuele vermogensmeting" in result["summary"]
    assert "fysieke levering" in result["summary"]

    clock.advance(75)
    result = selector.verification_result()
    assert result["pending"] is True

    clock.advance(2)
    result = selector.verification_result()

    assert result["ok"] is False
    assert result.get("pending") is not True


def test_native_verification_accepts_delivery_arriving_during_meter_grace():
    selector, app, _controller, clock, _state = _native_selector(
        grid_power=650
    )
    assert selector.activate_native() is True
    selected = selector.batteries[selector.active_indexes[0]]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "0"

    clock.advance(55)
    waiting = selector.verification_result()
    assert waiting["pending"] is True

    app.states[selected["actual_power"]] = "392"
    confirmed = selector.verification_result()

    assert confirmed["ok"] is True
    assert confirmed.get("pending") is not True


def test_native_verification_accepts_bms_stop_at_thirteen_percent():
    selector, app, _controller, _clock, state = _native_selector(
        grid_power=650
    )
    assert selector.activate_native() is True
    selected_index = selector.active_indexes[0]
    state.soc_values[selected_index] = 13
    selected = selector.batteries[selected_index]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "0"

    result = selector.verification_result()

    assert result["ok"] is True
    assert "BMS begrensd" in result["summary"]


def test_native_verification_accepts_shared_delivery_from_multiple_batteries():
    selector, app, _controller, _clock, _state = _native_selector(
        grid_power=900
    )
    selector.native_active = True
    selector.active_indexes = [0, 1]
    selector.last_demand = 900
    for index in selector.active_indexes:
        battery = selector.batteries[index]
        app.states[battery["work_mode"]] = "anti_feed"
        app.states[battery["rs485_control"]] = "off"
    app.states[selector.batteries[0]["actual_power"]] = "850"
    app.states[selector.batteries[1]["actual_power"]] = "0"

    result = selector.verification_result()

    assert result["ok"] is True


def test_native_delivery_overrules_known_stale_user_work_mode_readback():
    selector, app, _controller, _clock, _state = _native_selector(
        grid_power=650
    )
    assert selector.activate_native() is True
    selected = selector.batteries[selector.active_indexes[0]]
    app.states[selected["work_mode"]] = "manual"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "600"

    result = selector.verification_result()

    assert result["ok"] is True


def test_native_recovery_targets_only_the_confirmed_failed_battery():
    selector, app, controller, clock, _state = _native_selector(
        grid_power=650
    )
    assert selector.activate_native() is True
    selected_index = selector.active_indexes[0]
    selected = selector.batteries[selected_index]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "0"

    clock.advance(55)
    _confirm_native_mismatch(selector, clock)
    clock.advance(18)
    _confirm_native_mismatch(selector, clock)

    assert controller.repairs == [
        ((selected_index,), (selected_index,), "selector")
    ]


def test_native_verification_rereads_before_sending_recovery():
    selector, app, controller, clock, _state = _native_selector()
    assert selector.activate_native() is True

    clock.advance(10)
    first = selector.verification_result()
    assert first["ok"] is False
    assert first["pending"] is True

    selector._verify_native_if_due()
    assert len(controller.selections) == 1
    assert selector._native_retries == 0

    selected = selector.batteries[selector.active_indexes[0]]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "400"
    clock.advance(2)
    assert selector.verification_result()["ok"] is True


def test_native_verification_confirms_persistent_mismatch_on_second_read():
    selector, _app, _controller, clock, _state = _native_selector()
    assert selector.activate_native() is True

    clock.advance(10)
    assert selector.verification_result()["pending"] is True
    clock.advance(2)
    confirmed = selector.verification_result()
    assert confirmed["ok"] is False
    assert confirmed.get("pending") is not True


def _confirm_native_mismatch(selector, clock):
    clock.advance(10)
    selector._verify_native_if_due()
    clock.advance(2)
    selector._verify_native_if_due()


def test_alternating_force_stop_lag_never_counts_as_one_global_failure():
    selector, app, controller, clock, _state = _native_selector()
    assert selector.activate_native() is True
    selected = selector.batteries[selector.active_indexes[0]]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "400"
    inactive = [
        battery for battery in selector.batteries
        if battery is not selected
    ]

    for attempt in range(4):
        failing = inactive[attempt % 2]
        recovered = inactive[(attempt + 1) % 2]
        app.states[failing["force_mode"]] = "charge"
        app.states[recovered["force_mode"]] = "stop"
        _confirm_native_mismatch(selector, clock)

        assert selector._native_latched is False
        assert selector._native_retries == 1
        assert selector.active_indexes == [0]

    assert controller.selections[-1][0] == (0,)


def test_same_battery_needs_four_confirmed_recoveries_before_fallback():
    selector, app, controller, clock, _state = _native_selector()
    assert selector.activate_native() is True
    selected = selector.batteries[selector.active_indexes[0]]
    app.states[selected["work_mode"]] = "anti_feed"
    app.states[selected["rs485_control"]] = "off"
    app.states[selected["actual_power"]] = "400"
    failing = selector.batteries[1]
    app.states[failing["force_mode"]] = "charge"

    for _attempt in range(3):
        _confirm_native_mismatch(selector, clock)
        assert selector._native_latched is False

    _confirm_native_mismatch(selector, clock)

    assert selector._native_latched is True
    assert controller.selections[-1][0] == (0, 1, 2)


def test_quarantined_native_battery_is_immediately_replaced():
    selector, app, controller, clock, _state = _native_selector()
    health = _QuarantineHealth()
    app.battery_health_manager = health
    assert selector.activate_native() is True
    failed_index = selector.active_indexes[0]

    for _attempt in range(4):
        _confirm_native_mismatch(selector, clock)

    assert failed_index in health.unavailable
    assert selector._native_transition_active is True
    assert selector.active_indexes
    assert failed_index not in selector.active_indexes
    assert controller.selections[-1][0] == tuple(selector.active_indexes)


def test_single_battery_high_demand_does_not_repeat_scale_up_decisions():
    selector, app, clock = _selector(count=1)
    state = _battery_state(count=1)
    for _ in range(10):
        selector.allocate(3000, state, 12)
        clock.advance(121)
    assert selector.selected_count == 1
    assert selector.up_level is None
    logs = [str(entry) for entry in getattr(app, "logs", [])]
    assert not any("accu’s actief na" in entry for entry in logs)
