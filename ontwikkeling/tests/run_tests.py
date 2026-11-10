"""Minimale runner voor de assert-tests, zonder externe pytest-afhankelijkheid."""

import importlib.util
import pathlib
import sys


PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_ROOT = (
    PACKAGE_ROOT
    / "addon_configs"
    / "a0d7b954_appdaemon"
    / "apps"
    / "OpenEMS-M"
)
sys.path.insert(0, str(APP_ROOT))
total = 0

for path in sorted(pathlib.Path(__file__).parent.glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, function in vars(module).items():
        if name.startswith("test_") and callable(function):
            function()
            total += 1

print(f"{total} tests passed")
