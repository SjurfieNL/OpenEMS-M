# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Centrale regie voor controller-eigenaars, modi en vloeiende updates."""

from __future__ import annotations


class OperatingModeCoordinator:
    """Houdt één waarheid bij over eigenaar, modus en overgangstype."""

    STATUS_SENSOR = "sensor.openems_modusregisseur"
    POWER_MODES = {"charging", "forced_discharge"}

    def __init__(self, app):
        self.app = app
        self.owner = "geen"
        self.mode = None
        self.powers = []
        self.last_transition = "Nog geen controlleropdracht"
        self.smooth_update = False

    def can_update_smoothly(self, mode, powers) -> bool:
        """Vermogen mag direct wijzigen zolang de richting gelijk blijft."""
        return (
            self.mode == mode
            and mode in self.POWER_MODES
            and len(self.powers) == len(powers)
            and all(float(power) >= 0 for power in powers)
        )

    def record(self, source, mode, powers) -> None:
        previous_mode = self.mode
        previous_owner = self.owner
        owner = self._owner(source, mode)
        smooth = previous_mode == mode and mode in self.POWER_MODES
        self.owner = owner
        self.mode = mode
        self.powers = [float(power) for power in powers]
        self.smooth_update = smooth
        if smooth:
            self.last_transition = (
                f"Vloeiende vermogenswijziging binnen {mode}; "
                "geen groepsstop"
            )
        elif previous_mode is None:
            self.last_transition = f"Start in {mode}"
        else:
            self.last_transition = (
                f"{previous_mode} → {mode} | "
                f"{previous_owner} → {owner}"
            )
        self._publish()

    def status(self) -> dict:
        return {
            "owner": self.owner,
            "mode": self.mode or "onbekend",
            "powers": list(self.powers),
            "smooth_update": self.smooth_update,
            "last_transition": self.last_transition,
        }

    @staticmethod
    def _owner(source, mode):
        labels = {
            "manual": "Handmatige bediening",
            "coordinated": "OpenEMS zelfconsumptie",
            "export": "Terugleverregeling",
            "recovery": "Herstart-herstel",
            "test": "Controllertest",
        }
        if source == "auto":
            return "Energiescheduler"
        return labels.get(source, str(source or "onbekend"))

    def _publish(self):
        setter = getattr(self.app, "set_state", None)
        if not callable(setter):
            return
        setter(
            self.STATUS_SENSOR,
            state=self.mode or "onbekend",
            attributes={
                "friendly_name": "OpenEMS centrale modusregisseur",
                "icon": "mdi:state-machine",
                "eigenaar": self.owner,
                "modus": self.mode or "onbekend",
                "vermogens_per_accu_w": [
                    round(power) for power in self.powers
                ],
                "vloeiende_vermogenswijziging": self.smooth_update,
                "laatste_overgang": self.last_transition,
            },
            replace=True,
        )
