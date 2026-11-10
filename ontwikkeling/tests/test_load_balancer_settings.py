"""Instelbare grenzen en P1/CT-keuze van de load balancer."""

from datetime import datetime, timezone

from load_balancer import LoadBalancer
from runtime_settings import RuntimeSettings


class App:
    def __init__(self, states=None):
        self.states = dict(states or {})
        self.logs = []
        self.published = {}

    def get_state(self, entity, attribute=None):
        value = self.states.get(entity)
        if attribute == "all":
            if value is None:
                return None
            return {
                "state": value,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
        return value

    def log(self, message, level="INFO"):
        self.logs.append(message)

    def set_state(self, entity, state=None, attributes=None, replace=False):
        self.published[entity] = (state, attributes)


class Controller:
    desired_state = "charging"
    desired_powers = [1000, 1000]
    desired_source = "auto"
    requested_charge_total = 2000.0

    def reapply_charge(self):
        pass


def balancer(states, overrides=None):
    base = {
        "input_boolean.openems_lb_geinitialiseerd": "on",
        "input_number.openems_lb_max_import": "7950",
        "input_number.openems_lb_max_export": "5000",
        "input_boolean.openems_lb_p1_uit": "off",
        "input_boolean.openems_lb_ct_uit": "off",
        "input_select.openems_lb_voorkeur": "P1",
        "input_boolean.openems_lb_beide_tegelijk": "off",
        "sensor.p1_netto_vermogen": "1000",
        "sensor.meterkast_shellyctgrid_vermogen": "1200",
    }
    base.update(states)
    base.update(overrides or {})
    app = App(base)
    settings = RuntimeSettings(app)
    # CT-offset en teken: neutraal houden in deze tests.
    settings.grid_import_sign = lambda: 1
    settings.ct_grid_offset = lambda: 0.0
    return LoadBalancer(app, settings, Controller(), []), app


def test_limits_follow_helpers_and_fall_back_to_defaults():
    lb, app = balancer({"input_number.openems_lb_max_import": "7000",
                        "input_number.openems_lb_max_export": "4800"})
    assert lb.limit_w == 7000
    assert lb.export_limit_w == 4800
    app.states["input_number.openems_lb_max_import"] = "50"  # buiten bereik
    app.states["input_number.openems_lb_max_export"] = "unavailable"
    assert lb.limit_w == 7950
    assert lb.export_limit_w == 5000


def test_defaults_used_until_initialized():
    lb, app = balancer({"input_boolean.openems_lb_geinitialiseerd": "off",
                        "input_number.openems_lb_max_import": "1000"})
    assert lb.limit_w == 7950


def test_preferred_sensor_wins_and_other_is_fallback():
    lb, app = balancer({})
    assert lb._read_import() == (1000.0, "P1")
    app.states["sensor.p1_netto_vermogen"] = "unavailable"
    assert lb._read_import() == (1200.0, "CT")
    lb, app = balancer({"input_select.openems_lb_voorkeur": "CT"})
    assert lb._read_import() == (1200.0, "CT")
    app.states["sensor.meterkast_shellyctgrid_vermogen"] = "unknown"
    assert lb._read_import() == (1000.0, "P1")


def test_single_sensor_is_used_alone_without_fallback():
    lb, app = balancer({"input_boolean.openems_lb_ct_uit": "on"})
    assert lb._read_import() == (1000.0, "P1")
    app.states["sensor.p1_netto_vermogen"] = "unavailable"
    assert lb._read_import() is None
    lb, app = balancer({"input_boolean.openems_lb_p1_uit": "on"})
    assert lb._read_import() == (1200.0, "CT")


def test_both_off_counts_as_both_on_for_safety():
    lb, app = balancer({"input_boolean.openems_lb_p1_uit": "on",
                        "input_boolean.openems_lb_ct_uit": "on"})
    assert lb._read_import() == (1000.0, "P1")


def test_both_simultaneously_uses_strictest_value():
    lb, app = balancer({"input_boolean.openems_lb_beide_tegelijk": "on"})
    # import: hoogste meting telt
    assert lb._read_import() == (1200.0, "P1+CT")
    # export: laagste (meest negatieve) meting telt
    app.states["sensor.p1_netto_vermogen"] = "-4800"
    app.states["sensor.meterkast_shellyctgrid_vermogen"] = "-5200"
    value, source = lb._read_import()
    assert source == "P1+CT" and value == -4800.0
    assert lb._export_basis_w == -5200.0
    # valt één sensor weg, dan telt de andere
    app.states["sensor.meterkast_shellyctgrid_vermogen"] = "unavailable"
    assert lb._read_import() == (-4800.0, "P1")


def test_import_limit_setting_changes_when_charging_is_capped():
    lb, app = balancer({"input_number.openems_lb_max_import": "6000",
                        "sensor.p1_netto_vermogen": "6500"})
    lb.tick()
    assert lb.cap_w is not None and lb.mode == "begrenst"
    lb2, _ = balancer({"input_number.openems_lb_max_import": "8000",
                       "sensor.p1_netto_vermogen": "6500"})
    lb2.tick()
    assert lb2.cap_w is None


def test_status_publishes_sensor_details():
    lb, app = balancer({})
    lb.tick()
    attrs = app.published["sensor.openems_load_balancer"][1]
    assert attrs["sensor_modus"] == "voorkeur P1"
    assert attrs["limiet_w"] == 7950
    assert attrs["export_limiet_w"] == 5000
