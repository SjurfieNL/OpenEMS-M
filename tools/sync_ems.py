"""Genereer custom_components/openems_m/ems/ uit de AppDaemon-code.

De AppDaemon-versie blijft tot de overstap de enige bron. Dit script kopieert
die code en past alleen aan wat nodig is om binnen de integratie te draaien:

1. `import appdaemon.plugins.hass.hassapi as hass`
       -> `from ..bridge import hassapi as hass`
2. imports van OpenEMS-modules (`from load_balancer import X`)
       -> relatieve imports (`from .load_balancer import X`)
3. `Path(__file__).resolve().parent.parent` (datamap)
       -> `ems_paths.data_parent()`  (buiten custom_components)

(Service-aanroepen naar `openems_m_panel/...` blijven staan; de tussenlaag
stuurt ze door naar `openems_m/...`.)

Daarnaast genereert het de Marstek-driver (`openems_m/driver/`) uit
`config/custom_components/marstek_modbus` en de atomaire brug uit
`openems_m_panel/marstek_atomic_bridge.py`, plus kopieen van de driver- en
brugtests die naar de gegenereerde code wijzen (`tests/generated/`).

Verder blijft elke regel gelijk. Gebruik (vanuit de werkmap):
    python integratie/tools/sync_ems.py .
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

HEADER = (
    "# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig\n"
    "# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.\n"
)
HASSAPI_OLD = "import appdaemon.plugins.hass.hassapi as hass"
HASSAPI_NEW = "from ..bridge import hassapi as hass"
PATH_OLD = "Path(__file__).resolve().parent.parent"
PATH_NEW = "ems_paths.data_parent()"
PATHS_IMPORT = "from .. import ems_paths\n"


def convert(source: str, modules: set[str]) -> str:
    text = source.replace(HASSAPI_OLD, HASSAPI_NEW)
    names = "|".join(sorted(map(re.escape, modules), key=len, reverse=True))
    text = re.sub(rf"(?m)^(\s*)from ({names}) import ", r"\1from .\2 import ", text)
    text = re.sub(rf"(?m)^(\s*)import ({names})\s*$", r"\1from . import \2", text)
    if PATH_OLD in text:
        text = text.replace(PATH_OLD, PATH_NEW)
        text = _add_import(text, PATHS_IMPORT)
    return HEADER + text


def _add_import(text: str, line: str) -> str:
    lines = text.splitlines(keepends=True)
    index = 0
    # na docstring en `from __future__`
    if lines and lines[0].lstrip().startswith(('"""', "'''")):
        quote = lines[0].lstrip()[:3]
        if lines[0].count(quote) >= 2 and len(lines[0].strip()) > 3:
            index = 1
        else:
            for i in range(1, len(lines)):
                if quote in lines[i]:
                    index = i + 1
                    break
    for i in range(index, len(lines)):
        if lines[i].startswith("from __future__"):
            index = i + 1
    lines.insert(index, line)
    return "".join(lines)


def main(src_dir: str, dest_dir: str) -> list[str]:
    src = Path(src_dir)
    dest = Path(dest_dir)
    modules = {p.stem for p in src.glob("*.py")}
    if "marstek_ems" not in modules:
        raise SystemExit(f"Geen OpenEMS-M AppDaemon-code gevonden in {src}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "__init__.py").write_text(
        HEADER + '"""OpenEMS-logica (gegenereerd uit de AppDaemon-code)."""\n',
        encoding="utf-8",
    )
    written = []
    for path in sorted(src.glob("*.py")):
        out = convert(path.read_text(encoding="utf-8"), modules)
        (dest / path.name).write_text(out, encoding="utf-8")
        written.append(path.name)
    return written


# ------------------------------------------------------------------ driver
DRIVER_FILES = ["coordinator.py", "const.py", "sensor.py", "number.py", "select.py",
                "switch.py", "button.py", "binary_sensor.py"]
DRIVER_DOMAIN_OLD = 'DOMAIN = "marstek_modbus"'
DRIVER_DOMAIN_NEW = 'DOMAIN = "openems_m_driver"'
SUPER_INIT = re.compile(r"(?m)^(?P<ind>[ \t]+)super\(\)\.__init__\((?:.|\n)*?\n(?P=ind)\)\n")
CONFIG_ENTRY_FIX = (
    "{ind}# OpenEMS-M: DataUpdateCoordinator zet config_entry op de hoofd-entry;\n"
    "{ind}# zet hem terug op de accu-weergave zodat unieke ids per accu blijven.\n"
    "{ind}self.config_entry = entry\n"
)


def convert_driver(name: str, source: str) -> str:
    text = source
    if name == "const.py":
        if DRIVER_DOMAIN_OLD not in text:
            raise SystemExit("const.py: DOMAIN-regel niet gevonden")
        text = text.replace(DRIVER_DOMAIN_OLD, DRIVER_DOMAIN_NEW)
    if name == "coordinator.py":
        match = SUPER_INIT.search(text)
        if match is None:
            raise SystemExit("coordinator.py: super().__init__ niet gevonden")
        fix = CONFIG_ENTRY_FIX.format(ind=match.group("ind"))
        text = text[: match.end()] + fix + text[match.end():]
    return HEADER + text


BRIDGE_REPLACEMENTS = [
    ('MARSTEK_MODBUS_DOMAIN = "marstek_modbus"', 'MARSTEK_MODBUS_DOMAIN = "openems_m_driver"'),
    ('entry_id = getattr(entity, "config_entry_id", None)',
     'entry_id = getattr(entity, "config_subentry_id", None) or getattr(\n'
     '            entity, "config_entry_id", None\n        )'),
    ('self.hass.data.setdefault("openems_m_panel", {})', 'self.hass.data.setdefault("openems_m", {})'),
]


def convert_bridge(source: str) -> str:
    text = source
    for old, new in BRIDGE_REPLACEMENTS:
        if text.count(old) != 1:
            raise SystemExit(f"brug: verwacht precies 1x {old!r}, gevonden {text.count(old)}")
        text = text.replace(old, new)
    return HEADER + text


def sync_driver(werkmap: Path, dest: Path) -> list[str]:
    src = werkmap / "config" / "custom_components" / "marstek_modbus"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    (dest / "__init__.py").write_text(
        HEADER + '"""Marstek Venus Modbus-driver (gegenereerd uit marstek_modbus)."""\n',
        encoding="utf-8")
    written = []
    for name in DRIVER_FILES:
        (dest / name).write_text(convert_driver(name, (src / name).read_text(encoding="utf-8")),
                                 encoding="utf-8")
        written.append(name)
    (dest / "helpers").mkdir()
    for path in sorted((src / "helpers").glob("*.py")):
        (dest / "helpers" / path.name).write_text(
            HEADER + path.read_text(encoding="utf-8"), encoding="utf-8")
        written.append("helpers/" + path.name)
    if not (dest / "helpers" / "__init__.py").exists():
        (dest / "helpers" / "__init__.py").write_text(HEADER, encoding="utf-8")
    shutil.copytree(src / "registers", dest / "registers")
    written.append("registers/")
    license_src = src / "LICENSE"
    if not license_src.exists():
        license_src = Path(__file__).with_name("driver_LICENSE")
    shutil.copyfile(license_src, dest / "LICENSE")
    written.append("LICENSE")
    bridge = werkmap / "config" / "custom_components" / "openems_m_panel" / "marstek_atomic_bridge.py"
    (dest / "atomic_bridge.py").write_text(convert_bridge(bridge.read_text(encoding="utf-8")),
                                           encoding="utf-8")
    written.append("atomic_bridge.py")
    return written


PARITY_TESTS = {
    "test_marstek_modbus_driver.py": [('/ "marstek_modbus"', '/ "openems_m" / "driver"')],
    "test_marstek_coordinator_reconnect.py": [('/ "marstek_modbus"', '/ "openems_m" / "driver"')],
    "test_marstek_offline_startup.py": [
        ("def test_setup_raises_not_ready_when_battery_unreachable",
         "import pytest  # noqa: E402\n\n\n@pytest.mark.skip(reason=\"OpenEMS-M zet accu's per subentry op; "
         "een offline accu blokkeert de rest niet (zie test_driver_setup.py)\")\n"
         "def test_setup_raises_not_ready_when_battery_unreachable"),
    ],
    "test_marstek_atomic_bridge.py": [],
}


def sync_parity_tests(werkmap: Path, dest: Path) -> list[str]:
    """Kopieer driver- en brugtests en laat ze naar de gegenereerde code wijzen."""
    src = werkmap / "ontwikkeling" / "tests"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    written = []
    for name, replacements in PARITY_TESTS.items():
        path = src / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        text = text.replace('/ "config"\n    / "custom_components"', '/ "custom_components"')
        text = text.replace('/ "config"\n        / "custom_components"', '/ "custom_components"')
        for old, new in replacements:
            text = text.replace(old, new)
        if name == "test_marstek_atomic_bridge.py":
            text = re.sub(
                r'BRIDGE_PATH = \((?:.|\n)*?\n\)',
                'BRIDGE_PATH = (\n    Path(__file__).resolve().parents[2]\n'
                '    / "custom_components" / "openems_m" / "driver" / "atomic_bridge.py"\n)',
                text, count=1)
            text = text.replace('"marstek_modbus"', '"openems_m_driver"')
        (dest / name).write_text(
            HEADER.replace("de AppDaemon-code", "de werkmap-tests") + text, encoding="utf-8")
        written.append(name)
    return written


# ----------------------------------------------------------------- helpers
HELPER_TYPES = ("input_boolean", "input_number", "input_select", "input_text", "input_button")
# Helpers die OpenEMS gebruikt maar die niet in openems_helper.yaml staan
# (bij de oorspronkelijke installatie elders gedefinieerd). Zonder deze zou een
# nieuwe installatie ze missen. Grenzen gelijk aan de oorspronkelijke helpers.
EXTRA_HELPERS = {
    "input_number": {
        "marstek_doel_soc": {"name": "Marstek doel-SOC", "min": 50, "max": 100,
                             "step": 1, "unit_of_measurement": "%",
                             "icon": "mdi:battery-arrow-up"},
        "marstek_minimum_soc": {"name": "Marstek minimum-SOC", "min": 5, "max": 50,
                                "step": 1, "unit_of_measurement": "%",
                                "icon": "mdi:battery-arrow-down"},
        "marstek_prijsmarge": {"name": "Marstek prijsmarge laden", "min": 0, "max": 0.05,
                               "step": 0.001, "unit_of_measurement": "€/kWh",
                               "icon": "mdi:cash-plus"},
        "marstek_minimaal_prijsverschil_ontladen": {
            "name": "Marstek minimaal prijsverschil ontladen", "min": 0, "max": 0.5,
            "step": 0.001, "unit_of_measurement": "€/kWh", "icon": "mdi:cash-minus"},
    },
}


def sync_helpers(werkmap: Path, dest: Path) -> int:
    """Zet config/packages/openems_helper.yaml om naar helpers.json.

    De integratie maakt daaruit zelf de entiteiten aan (switch/number/select/
    text/button), zodat er geen YAML-package meer nodig is.
    """
    import json

    import yaml

    data = yaml.safe_load((werkmap / "config" / "packages" / "openems_helper.yaml")
                          .read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    count = 0
    for helper_type in HELPER_TYPES:
        items = data.get(helper_type) or {}
        out[helper_type] = {}
        for object_id, conf in items.items():
            out[helper_type][str(object_id)] = dict(conf or {})
            count += 1
        for object_id, conf in EXTRA_HELPERS.get(helper_type, {}).items():
            if object_id not in out[helper_type]:
                out[helper_type][object_id] = dict(conf)
                count += 1
    unknown = set(data) - set(HELPER_TYPES)
    if unknown:
        raise SystemExit(f"openems_helper.yaml: onbekende helpertypes {sorted(unknown)}")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return count


# ------------------------------------------------------------------- paneel
JS_HELPER_NAME = re.compile(
    r"\binput_(boolean|number|select|text|button)\.((?:openems|marstek)_)")
JS_DOMAINS = {"boolean": "switch", "number": "number", "select": "select",
              "text": "text", "button": "button"}
JS_SHIM = """
// OpenEMS-M (integratie): de instellingen heten switch./number./select./text./
// button. in plaats van input_*. Service-aanroepen naar de oude helperdomeinen
// gaan naar de nieuwe. Gegenereerd door tools/sync_ems.py.
const __OPENEMS_DOMAIN_MAP = {
  input_boolean: "switch", input_number: "number", input_select: "select",
  input_text: "text", input_button: "button",
};
function __openemsCallService(hass, domain, service, data, ...rest) {
  const mapped = __OPENEMS_DOMAIN_MAP[domain];
  const entity = data && typeof data.entity_id === "string" ? data.entity_id : "";
  if (mapped && !entity.startsWith(`${domain}.`)) domain = mapped;
  return hass.callService(domain, service, data, ...rest);
}
"""
STATIC_OLD = "openems_m_panel_static"
STATIC_NEW = "openems_m_static"


def convert_panel_js(text: str) -> str:
    text = JS_HELPER_NAME.sub(lambda m: f"{JS_DOMAINS[m.group(1)]}.{m.group(2)}", text)
    text = text.replace("this._hass.callService(", "__openemsCallService(this._hass, ")
    text = text.replace(STATIC_OLD, STATIC_NEW)
    lines = text.splitlines(keepends=True)
    last_import = 0
    in_import = False
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("import "):
            in_import = not stripped.rstrip().endswith(";")
            last_import = index + 1
        elif in_import:
            last_import = index + 1
            if stripped.rstrip().endswith(";"):
                in_import = False
        elif stripped and not stripped.startswith("//") and last_import:
            break
    lines.insert(last_import, JS_SHIM)
    return "// GEGENEREERD door tools/sync_ems.py uit openems_m_panel. Niet handmatig wijzigen.\n" + "".join(lines)


def sync_panel(werkmap: Path, dest: Path) -> list[str]:
    src = werkmap / "config" / "custom_components" / "openems_m_panel"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src / "frontend", dest)
    js = dest / "openems-panel.js"
    js.write_text(convert_panel_js(js.read_text(encoding="utf-8")), encoding="utf-8")
    brand_dest = dest.parent / "brand"
    if brand_dest.exists():
        shutil.rmtree(brand_dest)
    shutil.copytree(src / "brand", brand_dest)
    return sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    werkmap = Path(sys.argv[1]).resolve()
    root = Path(__file__).resolve().parents[1]
    ems = main(str(werkmap / "addon_configs" / "a0d7b954_appdaemon" / "apps" / "OpenEMS-M"),
               str(root / "custom_components" / "openems_m" / "ems"))
    driver = sync_driver(werkmap, root / "custom_components" / "openems_m" / "driver")
    tests = sync_parity_tests(werkmap, root / "tests" / "generated")
    helpers = sync_helpers(werkmap, root / "custom_components" / "openems_m" / "helpers.json")
    panel = sync_panel(werkmap, root / "custom_components" / "openems_m" / "frontend")
    print(f"{len(ems)} EMS-modules, {len(driver)} driveronderdelen, {len(tests)} tests, "
          f"{helpers} helpers, {len(panel)} paneelbestanden gegenereerd")
