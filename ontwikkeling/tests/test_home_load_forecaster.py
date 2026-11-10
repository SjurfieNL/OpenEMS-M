from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from home_load_forecaster import HomeLoadForecaster


class App:
    def __init__(self):
        self.states = {"sensor.home": 400}
        self.published = {}

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, *args, **kwargs):
        pass


def test_profile_publishes_smoothed_automatic_estimate():
    with TemporaryDirectory() as temporary:
        app = App()
        manager = HomeLoadForecaster(
            app,
            type("Settings", (), {"home_entity": lambda self: "sensor.home"})(),
            ZoneInfo("Europe/Amsterdam"),
            Path(temporary) / "profile.json",
        )
        manager.sample()
        _state, attributes = app.published[manager.STATUS_SENSOR]
        assert "Automatische rustige" in attributes["modus"]
        assert attributes["bron"] == "kwartierprofiel en recent gemiddelde"
        assert app.published[manager.STATUS_SENSOR][0] == 400
        assert len(attributes["profiel_per_kwartier"]) == 96
