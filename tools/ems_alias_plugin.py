"""pytest-plugin: draai de bestaande OpenEMS-tests tegen de gegenereerde ems/-code.

De tests importeren `load_balancer`, `marstek_ems` enz. als losse modules.
Deze plugin laat die namen verwijzen naar `openems_m.ems.<module>`, zodat
precies de code getest wordt die in de integratie zit.
Gebruik (vanuit de map met de originele tests):
    python -m pytest -p ems_alias_plugin ontwikkeling/tests
met deze map en integratie/tests op PYTHONPATH.
"""

import importlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "custom_components"))

import conftest  # noqa: E402,F401  - installeert nep-homeassistant

for path in sorted((ROOT / "custom_components" / "openems_m" / "ems").glob("*.py")):
    if path.stem == "__init__":
        continue
    module = importlib.import_module(f"openems_m.ems.{path.stem}")
    sys.modules[path.stem] = module
