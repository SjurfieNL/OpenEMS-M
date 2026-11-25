"""Het paneel in de integratie: gegenereerde JS en registratie."""

import asyncio
import json
import pathlib
import re
import shutil
import subprocess
import sys
import types
from types import SimpleNamespace

import pytest

from openems_m import panel
from openems_m.helpers import helper_entity_map

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "openems_m"
JS = ROOT / "frontend" / "openems-panel.js"


def test_generated_js_has_no_old_helper_names_left():
    text = JS.read_text(encoding="utf-8")
    body = text.split("function __openemsCallService", 1)[1]
    assert not re.search(r"input_(boolean|number|select|text|button)\.", body)
    assert "openems_m_panel_static" not in text
    assert "this._hass.callService(" not in text
    assert text.count("__openemsCallService(this._hass, ") >= 10


def test_every_literal_helper_in_js_exists_as_entity():
    text = JS.read_text(encoding="utf-8")
    known = set(helper_entity_map().values())
    found = set(re.findall(r"\b((?:switch|number|select|text|button)\.(?:openems|marstek)_[a-z0-9_]+)\b"
                           r"(?![$`{])", text))
    missing = {e for e in found if e not in known and not e.endswith("_")}
    # De controller-testmodus bestaat bewust niet in een nieuwe installatie.
    assert missing <= {"select.marstek_controller_test"}, missing


@pytest.mark.skipif(shutil.which("node") is None, reason="node niet aanwezig")
def test_generated_js_is_valid_and_shim_maps_domains():
    assert subprocess.run(["node", "--check", str(JS)], capture_output=True).returncode == 0
    shim = JS.read_text(encoding="utf-8")
    start = shim.index("const __OPENEMS_DOMAIN_MAP")
    end = shim.index("\n}\n", shim.index("function __openemsCallService")) + 3
    script = shim[start:end] + """
const calls = [];
const hass = {callService: (d, s, data) => calls.push([d, s, data.entity_id])};
__openemsCallService(hass, "input_boolean", "turn_on", {entity_id: "switch.openems_actief"});
__openemsCallService(hass, "input_text", "set_value", {entity_id: "text.openems_x", value: "a"});
__openemsCallService(hass, "input_boolean", "turn_on", {entity_id: "input_boolean.van_gebruiker"});
__openemsCallService(hass, "button", "press", {entity_id: "button.accu_reset"});
console.log(JSON.stringify(calls));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        ["switch", "turn_on", "switch.openems_actief"],
        ["text", "set_value", "text.openems_x"],
        ["input_boolean", "turn_on", "input_boolean.van_gebruiker"],
        ["button", "press", "button.accu_reset"],
    ]


def test_frontend_files_and_brand_present():
    for name in ("openems-panel.js", "modules/formatters.js", "assets/openems-logo.png"):
        assert (ROOT / "frontend" / name).is_file(), name
    assert (ROOT / "brand" / "icon.png").is_file()
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert {"http", "frontend", "panel_custom"} <= set(manifest["dependencies"])


def test_panel_config_uses_wizard_grid_sensor():
    assert panel.panel_config({"p1_entity": "sensor.p1", "ct_entity": "sensor.ct",
                               "preferred_sensor": "CT"})["grid_power_entity"] == "sensor.ct"
    assert panel.panel_config({"p1_entity": "sensor.p1",
                               "preferred_sensor": "P1"})["grid_power_entity"] == "sensor.p1"
    assert panel.panel_config({})["grid_power_entity"] == ""


def test_register_and_remove_panel(monkeypatch):
    calls = []
    frontend = types.ModuleType("homeassistant.components.frontend")
    frontend.async_remove_panel = lambda hass, path, warn_if_unknown=True: calls.append(("remove", path))
    panel_custom = types.ModuleType("homeassistant.components.panel_custom")

    async def register(hass, **kwargs):
        calls.append(("register", kwargs["frontend_url_path"], kwargs["module_url"].split("?")[0],
                      kwargs["config"]["grid_power_entity"]))
    panel_custom.async_register_panel = register
    http = types.ModuleType("homeassistant.components.http")
    http.StaticPathConfig = lambda url, path, cache_headers=True: (url, path)
    components = sys.modules["homeassistant.components"]
    monkeypatch.setitem(sys.modules, "homeassistant.components.frontend", frontend)
    monkeypatch.setitem(sys.modules, "homeassistant.components.panel_custom", panel_custom)
    monkeypatch.setitem(sys.modules, "homeassistant.components.http", http)
    monkeypatch.setattr(components, "frontend", frontend, raising=False)
    monkeypatch.setattr(components, "panel_custom", panel_custom, raising=False)

    class Http:
        async def async_register_static_paths(self, configs):
            calls.append(("static", configs[0][0]))

    class Hass:
        data = {}
        http = Http()

        async def async_add_executor_job(self, func, *args):
            return func(*args)
    hass = Hass()
    entry = SimpleNamespace(data={"p1_entity": "sensor.p1", "preferred_sensor": "P1"})
    asyncio.run(panel.async_register_panel(hass, entry))
    asyncio.run(panel.async_register_panel(hass, entry))   # tweede keer: geen dubbel pad
    panel.async_remove_panel(hass)
    assert calls.count(("static", "/openems_m_static")) == 1
    assert ("register", "openems-m", "/openems_m_static/openems-panel.js", "sensor.p1") in calls
    assert calls[-1] == ("remove", "openems-m")
