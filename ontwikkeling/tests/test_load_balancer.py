from datetime import datetime, timezone

from battery_manager import BatteryManager
from ems_config import BATTERIES, LOAD_BALANCER_HELPERS
from load_balancer import LoadBalancer
from marstek_controller import MarstekController
from runtime_settings import RuntimeSettings
from test_marstek_controller import BATTERIES as ONE, DummyApp

P1 = "sensor.p1_netto_vermogen"
CT = "sensor.meterkast_shellyctgrid_vermogen"


class App(DummyApp):
    def __init__(self):
        super().__init__()
        self.updated = {}
        self.published = {}
        self.now = datetime.now(timezone.utc).isoformat()

    def get_state(self, entity, attribute=None):
        if attribute == "all":
            return {"last_updated": self.updated.get(entity, self.now)}
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)


def _four(app):
    result = []
    for n in (1, 2, 3, 4):
        battery = {
            k: v.replace("accu1", f"accu{n}") if isinstance(v, str) else v
            for k, v in ONE[0].items()
        }
        battery["name"] = f"Accu {n}"
        battery["actual_power"] = f"sensor.accu{n}_actual"
        result.append(battery)
        app.states[f"number.accu{n}_max_charge_power"] = "2350"
        app.states[f"number.accu{n}_max_discharge_power"] = "2350"
        app.states[f"switch.accu{n}_rs485_control_mode"] = "off"
        app.states[f"sensor.accu{n}_actual"] = "0"
    return result


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def setup(p1=1000, ct=None):
    app = App()
    batteries = _four(app)
    app.states[P1] = str(p1)
    if ct is not None:
        app.states[CT] = str(ct)
    settings = RuntimeSettings(app)
    controller = MarstekController(app, batteries, enabled=True)
    clock = Clock()
    balancer = LoadBalancer(app, settings, controller, batteries, clock=clock)
    controller.bind_load_balancer(balancer)
    return app, controller, balancer, clock


def charge(controller, app, total_each=2000):
    app.states.update({f"sensor.accu{n}_actual": str(-total_each) for n in (1, 2, 3, 4)})
    assert controller.set_charge([total_each] * 4, source="auto")


def test_no_cap_below_limit():
    app, controller, balancer, _ = setup(p1=3000)
    charge(controller, app, 1000)
    balancer.tick()
    assert balancer.cap_w is None
    assert balancer.mode == "bewaakt"
    assert sum(controller.desired_powers) == 4000


def test_overload_reduces_charge_by_overshoot():
    app, controller, balancer, _ = setup(p1=8000 + 1000)  # 9000 W import
    charge(controller, app, 2000)  # 8000 W laden
    balancer.tick()
    # limiet 7950: 1050 W te veel -> 8000 - 1050 = 6950
    assert abs(balancer.cap_w - 6950) < 1
    assert abs(sum(controller.desired_powers) - 6950) < 1
    assert balancer.mode == "begrenst"
    assert app.published["sensor.openems_load_balancer"][0] == "begrenst"


def test_total_never_above_8050_with_home_load_sweep():
    for home in (0, 500, 2000, 4000, 6000):
        app, controller, balancer, _ = setup(p1=home + 8000)
        charge(controller, app, 2000)
        balancer.tick()
        total = sum(controller.desired_powers)
        assert total + home <= 8050 + 1, (home, total)


def test_recovery_is_gradual_after_delay():
    app, controller, balancer, clock = setup(p1=9000)
    charge(controller, app, 2000)
    balancer.tick()
    cap = balancer.cap_w
    app.states[P1] = "5000"
    balancer.tick()  # start herstelteller
    assert balancer.cap_w == cap
    clock.t += 10
    balancer.tick()
    assert balancer.cap_w == cap  # nog te kort
    clock.t += 15
    balancer.tick()
    assert abs(balancer.cap_w - (cap + 500)) < 1


def test_recovery_between_thresholds_holds():
    app, controller, balancer, clock = setup(p1=9000)
    charge(controller, app, 2000)
    balancer.tick()
    cap = balancer.cap_w
    app.states[P1] = "7800"  # tussen 7600 en 7950
    clock.t += 100
    balancer.tick()
    assert balancer.cap_w == cap


def test_cap_cleared_when_requested_power_reached():
    app, controller, balancer, clock = setup(p1=8300)
    charge(controller, app, 2000)
    balancer.tick()
    assert balancer.cap_w is not None
    app.states[P1] = "1000"
    for _ in range(40):
        clock.t += 25
        balancer.tick()
    assert balancer.cap_w is None
    assert abs(sum(controller.desired_powers) - 8000) < 1


def test_falls_back_to_ct_when_p1_unavailable():
    app, controller, balancer, _ = setup(p1=0, ct=9000)
    app.states[P1] = "unavailable"
    charge(controller, app, 2000)
    balancer.tick()
    assert balancer.source == "CT"
    assert balancer.cap_w is not None


def test_stale_p1_uses_ct():
    app, controller, balancer, _ = setup(p1=9000, ct=2000)
    app.updated[P1] = "2000-01-01T00:00:00+00:00"
    charge(controller, app, 2000)
    balancer.tick()
    assert balancer.source == "CT"
    assert balancer.cap_w is None


def test_no_measurement_uses_emergency_limit():
    app, controller, balancer, _ = setup()
    app.states[P1] = "unavailable"
    charge(controller, app, 2000)
    balancer.tick()
    assert balancer.cap_w == 3500
    assert balancer.mode == "noodgrens"
    assert abs(sum(controller.desired_powers) - 3500) < 1


def test_checkbox_laden_off_disables_cap():
    app, controller, balancer, _ = setup(p1=9000)
    app.states[LOAD_BALANCER_HELPERS["laden"]] = "on"  # AAN = balancer uit
    charge(controller, app, 2000)
    balancer.tick()
    assert balancer.cap_w is None
    assert sum(controller.desired_powers) == 8000


def test_manual_mode_follows_own_checkbox():
    app, controller, balancer, _ = setup(p1=9000)
    controller.enabled = False
    controller.test_enabled = True
    app.states[LOAD_BALANCER_HELPERS["handmatig"]] = "on"
    app.states.update({f"sensor.accu{n}_actual": "-2000" for n in (1, 2, 3, 4)})
    controller.set_charge([2000] * 4, source="manual")
    balancer.tick()
    assert balancer.cap_w is None
    assert sum(controller.desired_powers) == 8000


def test_settings_default_all_enabled():
    settings = RuntimeSettings(App())
    for kind in ("laden", "terugleveren", "handmatig"):
        assert settings.load_balancer_enabled(kind)


def test_idle_balancer_does_nothing():
    app, controller, balancer, _ = setup(p1=9500)
    balancer.tick()
    assert balancer.cap_w is None
    assert balancer.mode == "inactief"


def test_planner_max_charge_capped_at_7850():
    app = App()
    for b in BATTERIES:
        app.states[b["soc"]] = "50"
        app.states[b["capacity"]] = "5120"
        app.states[b["max_charge_power"]] = "2350"
        app.states[b["max_discharge_power"]] = "2350"
    state = BatteryManager(app, BATTERIES).read()
    assert abs(state.max_charge - 7850) < 1e-6
    assert abs(sum(state.max_charge_values) - 7850) < 1e-6
    assert state.max_discharge == 9400


def test_planner_cap_not_applied_below_limit():
    app = App()
    for b in BATTERIES[:3]:
        app.states[b["soc"]] = "50"
        app.states[b["capacity"]] = "5120"
        app.states[b["max_charge_power"]] = "2350"
        app.states[b["max_discharge_power"]] = "2350"
    state = BatteryManager(app, BATTERIES[:3]).read()
    assert state.max_charge == 7050


def test_manual_mode_is_limited_when_checkbox_on():
    app, controller, balancer, _ = setup(p1=9000)
    controller.enabled = False
    controller.test_enabled = True
    app.states.update({f"sensor.accu{n}_actual": "-2000" for n in (1, 2, 3, 4)})
    assert controller.set_charge([2000] * 4, source="manual")
    balancer.tick()
    assert balancer.cap_w is not None
    assert sum(controller.desired_powers) < 8000


def test_wiring_and_panel_source():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    ems = (root / "addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/marstek_ems.py").read_text(encoding="utf-8")
    for token in ("LoadBalancer(", "bind_load_balancer", "self.load_balancer.tick", "_load_balancer_changed", "LOAD_BALANCER_HELPERS"):
        assert token in ems, token
    export = (root / "addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/grid_export.py").read_text(encoding="utf-8")
    assert 'load_balancer_enabled' in export and '"terugleveren"' in export
    panel = (root / "config/custom_components/openems_m_panel/frontend/openems-panel.js").read_text(encoding="utf-8")
    for kind in ("laden", "terugleveren", "handmatig"):
        assert f"lb-toggle-{kind}" in panel
        assert f"openems_loadbalancer_{kind}_uit" in panel
    yaml = (root / "config/packages/openems_helper.yaml").read_text(encoding="utf-8")
    for kind in ("laden", "terugleveren", "handmatig"):
        assert f"openems_loadbalancer_{kind}_uit:" in yaml


def discharge(controller, app, each=2000, source="manual"):
    if source == "manual":
        controller.enabled = False
        controller.test_enabled = True
    app.states.update({f"sensor.accu{n}_actual": str(each) for n in (1, 2, 3, 4)})
    assert controller.set_discharge([each] * 4, source=source)


def test_manual_discharge_export_above_5000_is_reduced():
    app, controller, balancer, _ = setup(p1=-6000)  # 6000 W export
    discharge(controller, app, 2000)  # 8000 W ontladen
    balancer.tick()
    assert abs(balancer.discharge_cap_w - 7000) < 1
    assert abs(sum(controller.desired_powers) - 7000) < 1
    assert balancer.export_mode == "begrenst"


def test_export_below_limit_is_not_capped():
    app, controller, balancer, _ = setup(p1=-3000)
    discharge(controller, app, 1500)
    balancer.tick()
    assert balancer.discharge_cap_w is None
    assert balancer.export_mode == "bewaakt"
    assert sum(controller.desired_powers) == 6000


def test_export_recovery_is_gradual():
    app, controller, balancer, clock = setup(p1=-6000)
    discharge(controller, app, 2000)
    balancer.tick()
    cap = balancer.discharge_cap_w
    app.states[P1] = "-3000"
    balancer.tick()
    clock.t += 25
    balancer.tick()
    assert abs(balancer.discharge_cap_w - (cap + 500)) < 1


def test_export_cap_cleared_when_request_reached():
    app, controller, balancer, clock = setup(p1=-5200)
    discharge(controller, app, 1500)  # 6000 W gevraagd
    balancer.tick()
    assert balancer.discharge_cap_w is not None
    app.states[P1] = "-2000"
    for _ in range(6):
        clock.t += 25
        balancer.tick()
    assert balancer.discharge_cap_w is None
    assert sum(controller.desired_powers) == 6000


def test_export_checkbox_off_disables_cap():
    app, controller, balancer, _ = setup(p1=-6000)
    app.states[LOAD_BALANCER_HELPERS["handmatig"]] = "on"
    discharge(controller, app, 2000)
    balancer.tick()
    assert balancer.discharge_cap_w is None
    assert sum(controller.desired_powers) == 8000


def test_export_source_is_left_to_its_own_limit():
    app, controller, balancer, _ = setup(p1=-6000)
    discharge(controller, app, 2000, source="export")
    balancer.tick()
    assert balancer.discharge_cap_w is None


def test_export_falls_back_to_ct_when_p1_missing():
    app, controller, balancer, _ = setup(p1=0, ct=-6000)
    app.states[P1] = "unavailable"
    discharge(controller, app, 2000)
    balancer.tick()
    assert balancer.discharge_cap_w is not None
