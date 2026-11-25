"""Waar OpenEMS zijn data en installatie-instellingen bewaart.

In AppDaemon was dat de map naast de app (apps/OpenEMS-M-data). In de
integratie mag dat NIET in custom_components staan, want een update via HACS
vervangt die map. Daarom: /config/openems_m/ (instelbaar).
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path("/config/openems_m")


def set_root(path) -> None:
    global _ROOT
    _ROOT = Path(path)


def data_parent() -> Path:
    """Vervangt `Path(__file__).resolve().parent.parent` uit de AppDaemon-code."""
    return _ROOT
