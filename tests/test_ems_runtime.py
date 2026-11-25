"""EMS starten binnen de integratie: site-bestand, naamvertaling, echte rooktest."""

import logging
import time
from types import SimpleNamespace

import yaml

from openems_m import ems_runtime
from openems_m.helpers import SERVICE_MAP, helper_definitions, helper_entity_map
from test_bridge_hassapi import FakeHass, in_worker, wait_for


def _entry():
    def sub(sid, number, title):
        return SimpleNamespace(subentry_id=sid, subentry_type="battery", title=title,
                               data={"openems_battery_number": number, "name": title})
    return SimpleNamespace(
        entry_id="e1",
        data={"max_import_w": 8050, "max_export_w": 5000, "safety_margin_w": 100,
              "p1_entity": "sensor.p1", "ct_entity": "sensor.ct",
              "price_entity": "sensor.prijs", "auto_control": False},
        subentries={"b": sub("b", 2, "Berging Accu2"), "a": sub("a", 1, "Berging Accu1")},
    )


def test_site_config_from_wizard_and_subentries():
    site = ems_runtime.build_site_config(_entry(), "nl")
    assert site == {
        "entiteit_taal": "nl",
        "accus": [{"naam": "Berging Accu1", "prefix": "berging_accu1"},
                  {"naam": "Berging Accu2", "prefix": "berging_accu2"}],
        "net": {"max_import_w": 8050, "max_export_w": 5000, "marge_w": 100},
        "sensoren": {"p1": "sensor.p1", "ct_net": "sensor.ct"},
        "prijs": {"all_in_sensor": "sensor.prijs"},
    }
    assert ems_runtime.build_site_config(_entry(), "en-GB")["entiteit_taal"] == "en"


def test_write_site_config_detects_changes(tmp_path):
    site = ems_runtime.build_site_config(_entry(), "nl")
    assert ems_runtime.write_site_config(tmp_path, site) is True
    assert ems_runtime.write_site_config(tmp_path, site) is False
    text = (tmp_path / "openems_site.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(text) == site
    site["net"]["marge_w"] = 150
    assert ems_runtime.write_site_config(tmp_path, site) is True


# ------------------------------------------------------- naamvertaling
def _mapped_app(hass):
    from test_bridge_hassapi import App
    app = App()
    app.bind(hass, logging.getLogger("test.map"), entity_map=helper_entity_map(),
             service_map=SERVICE_MAP)
    app.start()
    assert wait_for(lambda: app.init_thread is not None)
    return app


def test_bridge_translates_old_helper_names():
    hass = FakeHass()
    app = _mapped_app(hass)
    try:
        hass.in_loop(hass.states.async_set, "switch.openems_actief", "off", {})
        hass.in_loop(hass.states.async_set, "number.openems_lb_max_import", "8010.0",
                     {"min": 1000, "max": 30000})
        assert in_worker(app, lambda: app.get_state("input_boolean.openems_actief")) == "off"
        assert in_worker(app, lambda: app.get_state(
            "input_number.openems_lb_max_import", attribute="max")) == 30000
        seen = []
        app.listen_state(lambda e, a, o, n, k: seen.append((e, o, n)),
                         "input_boolean.openems_actief")
        hass.in_loop(hass.states.async_set, "switch.openems_actief", "on", {})
        assert wait_for(lambda: seen == [("input_boolean.openems_actief", "off", "on")])

        in_worker(app, lambda: app.call_service(
            "input_number/set_value", entity_id="input_number.openems_lb_max_import",
            value=7000))
        in_worker(app, lambda: app.turn_on("input_boolean.openems_actief"))
        in_worker(app, lambda: app.call_service(
            "input_select/select_option", entity_id="input_select.openems_lb_voorkeur",
            option="CT"))
        in_worker(app, lambda: app.call_service(
            "input_text/set_value", entity_id="input_text.openems_grid_vermogenssensor",
            value="sensor.x"))
        # niet-OpenEMS-helper blijft ongewijzigd
        in_worker(app, lambda: app.call_service(
            "input_boolean/turn_on", entity_id="input_boolean.iets_van_de_gebruiker"))
        calls = [(d, s, data["entity_id"]) for d, s, data, *_ in hass.services.calls]
        assert calls == [
            ("number", "set_value", "number.openems_lb_max_import"),
            ("switch", "turn_on", "switch.openems_actief"),
            ("select", "select_option", "select.openems_lb_voorkeur"),
            ("text", "set_value", "text.openems_grid_vermogenssensor"),
            ("input_boolean", "turn_on", "input_boolean.iets_van_de_gebruiker"),
        ]
    finally:
        app.stop()
        hass.close()


# ---------------------------------------------- echte EMS, met entiteiten
class ServiceHass(FakeHass):
    """Nep-HA waarin de helper-services de entiteit-state echt bijwerken."""

    def __init__(self):
        super().__init__()
        services = self.services
        states = self.states

        async def async_call(domain, service, data, blocking=False):
            services.calls.append((domain, service, dict(data), blocking, None))
            entity = data.get("entity_id")
            if not isinstance(entity, str) or states.get(entity) is None:
                return
            attrs = dict(states.get(entity).attributes)
            if (domain, service) in (("switch", "turn_on"), ("switch", "turn_off")):
                states.async_set(entity, service.split("_")[1], attrs)
            elif (domain, service) in (("number", "set_value"), ("text", "set_value")):
                states.async_set(entity, str(data["value"]), attrs)
            elif (domain, service) == ("select", "select_option"):
                states.async_set(entity, data["option"], attrs)
        services.async_call = async_call


def _create_helper_states(hass):
    for helper_type, items in helper_definitions().items():
        for object_id, conf in items.items():
            entity = helper_entity_map()[f"{helper_type}.{object_id}"]
            domain = entity.split(".")[0]
            if domain == "switch":
                state, attrs = "off", {}
            elif domain == "number":
                state = str(float(conf.get("min", 0)))
                attrs = {"min": conf.get("min"), "max": conf.get("max"),
                         "step": conf.get("step")}
            elif domain == "select":
                state, attrs = conf["options"][0], {"options": conf["options"]}
            elif domain == "text":
                state, attrs = "", {"max": conf.get("max", 100)}
            else:
                state, attrs = "unknown", {}
            hass.in_loop(hass.states.async_set, entity, state, attrs)


def test_real_ems_runs_on_integration_entities(tmp_path):
    from openems_m import ems_paths
    from openems_m.ems.marstek_ems import MarstekEMS

    ems_paths.set_root(tmp_path)
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)
    logger = logging.getLogger("test.openems.integratie")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(Collect())
    hass = ServiceHass()
    _create_helper_states(hass)
    app = MarstekEMS()
    app.bind(hass, logger, entity_map=helper_entity_map(), service_map=SERVICE_MAP)
    try:
        app.start()
        assert wait_for(lambda: any("OpenEMS-M v" in r.getMessage() for r in records), 10)
        time.sleep(1.5)
    finally:
        app.stop()
        hass.close()
    errors = [r.getMessage() for r in records if r.levelno >= logging.ERROR]
    assert errors == []
    untranslated = [r.getMessage() for r in records
                    if "Geen vertaling voor service" in r.getMessage()]
    assert untranslated == []
    domains = {c[0] for c in hass.services.calls}
    # de EMS schrijft zijn standaardinstellingen naar de NIEUWE entiteiten
    assert not {"input_boolean", "input_number", "input_select", "input_text"} & domains
    # geen accu's: niets naar accu-entiteiten
    assert not [c for c in hass.services.calls
                if c[0] in ("number", "select", "button")
                and "accu" in str(c[2].get("entity_id", "")) and "openems" not in
                str(c[2].get("entity_id", ""))]
    # hoofdschakelaar staat (standaard) uit: alleen meekijken
    assert hass.states.get("switch.openems_actief").state == "off"
