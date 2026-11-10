"""Gerichte runner voor de fysieke accuaansturing en zelfconsumptie."""

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

FILES = (
    "test_coordinated_self_consumption.py",
    "test_marstek_atomic_bridge.py",
    "test_marstek_controller.py",
)
total = 0

for filename in FILES:
    path = pathlib.Path(__file__).parent / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, function in vars(module).items():
        if name.startswith("test_") and callable(function):
            function()
            total += 1

print(f"{total} gerichte controllertests geslaagd")
