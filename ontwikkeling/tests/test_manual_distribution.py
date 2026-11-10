from manual_controller import ManualController
from test_manual_controller import DummyApp, DummyController

CH = "input_number.openems_handmatig_laadvermogen_totaal"
DIS = "input_number.openems_handmatig_ontlaadvermogen_totaal"
DIST = "input_select.openems_handmatig_verdeling"
EXC = [f"input_boolean.openems_handmatig_accu{n}_uit" for n in (1, 2, 3, 4)]


class Controller(DummyController):
    def __init__(self, socs=(50, 50, 50, 50)):
        super().__init__()
        self.batteries = [
            {"name": f"Accu {n}", "soc": f"sensor.soc{n}",
             "max_charge_power": f"number.mc{n}",
             "max_discharge_power": f"number.md{n}"}
            for n in (1, 2, 3, 4)
        ]
        self.socs = socs

    def set_charge(self, powers, source):
        self.desired_powers = list(powers)
        return super().set_charge(powers, source)

    def set_discharge(self, powers, source):
        self.desired_powers = list(powers)
        return super().set_discharge(powers, source)


def make(mode="Laden", charge=4000, discharge=3000, dist="Gelijk",
         excluded=(), socs=(50, 50, 50, 50)):
    app = DummyApp()
    app.states.update({
        "input_select.openems_handmatige_modus": mode,
        CH: str(charge), DIS: str(discharge), DIST: dist,
    })
    for n in range(4):
        app.states[f"sensor.soc{n + 1}"] = str(socs[n])
        app.states[f"number.mc{n + 1}"] = "2350"
        app.states[f"number.md{n + 1}"] = "2350"
        app.states[EXC[n]] = "on" if n in excluded else "off"
    controller = Controller(socs)
    manual = ManualController(
        app=app, controller=controller,
        enabled_helper="input_boolean.openems_actief",
        mode_helper="input_select.openems_handmatige_modus",
        charge_power_helper=CH, discharge_power_helper=DIS,
        test_mode_helper="input_select.marstek_controller_test",
        battery_count=4, distribution_helper=DIST, excluded_helpers=EXC,
    )
    return app, controller, manual


def test_equal_split_over_all_four():
    _a, controller, manual = make(charge=4000)
    manual.apply_current()
    assert controller.calls[0] == ("charge", [1000.0] * 4, "manual")


def test_only_selected_batteries_take_part():
    _a, controller, manual = make(charge=4000, excluded=(1, 3))
    manual.apply_current()
    assert controller.calls[0][1] == [2000.0, 0.0, 2000.0, 0.0]


def test_cap_per_battery_is_redistributed():
    _a, controller, manual = make(charge=6000, excluded=(3,))
    manual.apply_current()
    assert controller.calls[0][1] == [2000.0, 2000.0, 2000.0, 0.0]
    _a, controller, manual = make(charge=7000, excluded=(3,))
    manual.apply_current()
    assert all(value <= 2350 for value in controller.calls[0][1])
    assert abs(sum(controller.calls[0][1]) - 7000) < 1e-6


def test_total_above_capacity_is_clipped_to_maxima():
    _a, controller, manual = make(charge=8000, excluded=(2, 3))
    manual.apply_current()
    assert controller.calls[0][1] == [2350.0, 2350.0, 0.0, 0.0]


def test_by_soc_charge_favours_lowest_soc():
    _a, controller, manual = make(
        charge=3000, dist="Naar SOC", socs=(20, 50, 80, 95)
    )
    manual.apply_current()
    powers = controller.calls[0][1]
    assert abs(sum(powers) - 3000) < 1e-6
    assert powers[0] > powers[1] > powers[2] > powers[3]


def test_by_soc_discharge_favours_highest_soc():
    _a, controller, manual = make(
        mode="Ontladen", discharge=3000, dist="Naar SOC",
        socs=(20, 50, 80, 95),
    )
    manual.apply_current()
    powers = controller.calls[0][1]
    assert controller.calls[0][0] == "discharge"
    assert abs(sum(powers) - 3000) < 1e-6
    assert powers[3] > powers[2] > powers[1] > powers[0]


def test_discharge_uses_own_value_and_range_to_8000():
    _a, controller, manual = make(mode="Ontladen", charge=500, discharge=8000)
    manual.apply_current()
    assert controller.calls[0][0] == "discharge"
    assert abs(sum(controller.calls[0][1]) - 8000) < 1e-6
    assert max(controller.calls[0][1]) <= 2350


def test_value_above_8000_is_clipped():
    _a, controller, manual = make(charge=12000)
    manual.apply_current()
    assert sum(controller.calls[0][1]) <= 8000


def test_no_battery_selected_stops_and_reports():
    app, controller, manual = make(excluded=(0, 1, 2, 3))
    manual.apply_current()
    assert controller.calls == [("stop", "manual")]
    state, _attrs = app.published["sensor.openems_handmatige_status"]
    assert state == "Geblokkeerd"


def test_disabled_battery_in_settings_is_skipped():
    app, controller, manual = make(charge=3000)

    class Settings:
        @staticmethod
        def battery_disabled(index):
            return index == 3

    app.runtime_settings = Settings()
    manual.apply_current()
    assert controller.calls[0][1] == [1000.0, 1000.0, 1000.0, 0.0]


def test_change_of_checkbox_reapplies_only_in_charge_or_discharge():
    app, _controller, manual = make(mode="Stop")
    manual._changed(EXC[0], None, "off", "on", {})
    assert app.callbacks == []
    app.states["input_select.openems_handmatige_modus"] = "Laden"
    manual._changed(EXC[0], None, "off", "on", {})
    assert len(app.callbacks) == 1


def test_legacy_mode_without_distribution_unchanged():
    from test_manual_controller import make_manual
    app = DummyApp()
    controller = DummyController()
    make_manual(app, controller).apply_current()
    assert controller.calls == [("charge", [750.0, 750.0, 750.0], "manual")]


def test_panel_and_yaml_wiring():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    panel = (root / "config/custom_components/openems_m_panel/frontend/openems-panel.js").read_text(encoding="utf-8")
    for token in ("manual-power", "manual-accu-", "manual-distribution",
                  "openems_handmatig_laadvermogen_totaal",
                  "openems_handmatig_ontlaadvermogen_totaal"):
        assert token in panel, token
    assert 'this._range("manual-discharge"' not in panel
    yaml = (root / "config/packages/openems_helper.yaml").read_text(encoding="utf-8")
    for token in ("openems_handmatig_laadvermogen_totaal:", "openems_handmatig_ontlaadvermogen_totaal:",
                  "openems_handmatig_verdeling:", "openems_handmatig_accu4_uit:"):
        assert token in yaml, token
    ems = (root / "addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/marstek_ems.py").read_text(encoding="utf-8")
    assert "distribution_helper=MANUAL_DISTRIBUTION_HELPER" in ems


class SelfController(Controller):
    def set_native_self_consumption_selection(self, indexes, source):
        self.calls.append(("self_sel", list(indexes), source))
        return True


def make_self(excluded=()):
    app, _c, _m = make("Zelfconsumptie", excluded=excluded)
    controller = SelfController()
    manual = ManualController(
        app=app, controller=controller,
        enabled_helper="input_boolean.openems_actief",
        mode_helper="input_select.openems_handmatige_modus",
        charge_power_helper=CH, discharge_power_helper=DIS,
        test_mode_helper="input_select.marstek_controller_test",
        battery_count=4, distribution_helper=DIST, excluded_helpers=EXC,
    )
    return app, controller, manual


def test_self_consumption_all_batteries_uses_normal_command():
    _a, controller, manual = make_self()
    manual.apply_current()
    assert controller.calls[0] == ("self", "manual")


def test_self_consumption_with_selection_uses_native_selection():
    _a, controller, manual = make_self(excluded=(1, 3))
    manual.apply_current()
    assert controller.calls[0] == ("self_sel", [0, 2], "manual")


def test_self_consumption_without_battery_is_blocked_and_stops():
    _a, controller, manual = make_self(excluded=(0, 1, 2, 3))
    manual.apply_current()
    assert controller.calls[0] == ("stop", "manual")
    assert not any(call[0] in ("self", "self_sel") for call in controller.calls)


def test_changing_checkbox_in_self_consumption_reapplies():
    app, _c, manual = make_self(excluded=(1,))
    before = manual._generation
    manual._changed(EXC[2], "state", "off", "on", {})
    assert manual._generation == before + 1
    manual._changed(DIST, "state", "Gelijk", "Naar SOC", {})
    assert manual._generation == before + 1  # verdeling telt niet bij zelfconsumptie


def test_full_battery_gets_nothing_and_share_goes_to_others_equal():
    _a, controller, manual = make(charge=6000, socs=(100, 50, 50, 50))
    manual.apply_current()
    assert controller.calls[0][1] == [0.0, 2000.0, 2000.0, 2000.0]


def test_full_battery_gets_nothing_by_soc_and_total_is_kept():
    # Situatie uit de praktijk: 97 / 99 / 100 / 64 %.
    _a, controller, manual = make(
        charge=6000, dist="Naar SOC", socs=(97, 99, 100, 64)
    )
    manual.apply_current()
    powers = controller.calls[0][1]
    assert powers[2] == 0.0
    assert abs(sum(powers) - 6000) < 1e-6
    assert powers[3] == 2350.0 and powers[0] == 2350.0
    assert abs(powers[1] - 1300.0) < 1e-6


def test_empty_battery_gets_nothing_when_discharging():
    _a, controller, manual = make(
        mode="Ontladen", discharge=6000, socs=(10, 50, 50, 50)
    )
    manual.apply_current()
    assert controller.calls[0][1] == [0.0, 2000.0, 2000.0, 2000.0]


def test_all_full_keeps_previous_behaviour():
    _a, controller, manual = make(charge=4000, socs=(100, 100, 100, 100))
    manual.apply_current()
    assert controller.calls[0][1] == [1000.0] * 4


def test_battery_becoming_full_triggers_redistribution_once():
    app, controller, manual = make(charge=6000, socs=(99, 50, 50, 50))
    manual.apply_current()
    assert controller.calls[0][1] == [1500.0] * 4
    app.callbacks.clear()

    # Gewone SOC-stap: geen nieuwe opdracht.
    app.states["sensor.soc2"] = "51"
    manual._soc_changed("sensor.soc2", "state", "50", "51", {})
    assert app.callbacks == []

    # Accu 1 raakt vol: herverdelen.
    app.states["sensor.soc1"] = "100"
    manual._soc_changed("sensor.soc1", "state", "99", "100", {})
    assert len(app.callbacks) == 1
    callback, _delay, kwargs = app.callbacks.pop(0)
    callback(kwargs)
    assert controller.calls[-1][1] == [0.0, 2000.0, 2000.0, 2000.0]


def test_full_battery_rejoins_only_below_hysteresis():
    app, controller, manual = make(charge=6000, socs=(100, 50, 50, 50))
    manual.apply_current()
    app.callbacks.clear()

    app.states["sensor.soc1"] = "99"
    manual._soc_changed("sensor.soc1", "state", "100", "99", {})
    assert app.callbacks == []  # nog binnen de marge: blijft 0 W

    app.states["sensor.soc1"] = "96"
    manual._soc_changed("sensor.soc1", "state", "99", "96", {})
    assert len(app.callbacks) == 1
    callback, _delay, kwargs = app.callbacks.pop(0)
    callback(kwargs)
    assert controller.calls[-1][1] == [1500.0] * 4


def test_soc_change_ignored_outside_charge_or_discharge():
    app, _controller, manual = make(charge=6000, socs=(99, 50, 50, 50))
    manual.apply_current()
    app.callbacks.clear()
    app.states["input_select.openems_handmatige_modus"] = "Stop"
    manual._soc_changed("sensor.soc1", "state", "99", "100", {})
    assert app.callbacks == []


def test_soc_reapply_waits_while_a_command_is_running():
    app, controller, manual = make(charge=6000, socs=(99, 50, 50, 50))
    manual.apply_current()
    app.callbacks.clear()
    app.states["sensor.soc1"] = "100"
    manual._soc_changed("sensor.soc1", "state", "99", "100", {})
    callback, _delay, kwargs = app.callbacks.pop(0)
    manual._busy = True
    callback(kwargs)
    assert len(app.callbacks) == 1  # opnieuw ingepland
    manual._busy = False
    callback, _delay, kwargs = app.callbacks.pop(0)
    callback(kwargs)
    assert controller.calls[-1][1] == [0.0, 2000.0, 2000.0, 2000.0]
