from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from efficiency_learner import EfficiencyLearner


class App:
    def __init__(self):
        self.states = {"sensor.power": "0"}
        self.published = {}

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = {
            "state": state,
            "attributes": attributes,
        }

    def log(self, *args, **kwargs):
        pass


def _run_session(learner, app, start, direction, start_soc, end_soc):
    samples = 90
    power = -1000 if direction == "charge" else 1000
    for minute in range(samples + 1):
        fraction = minute / samples
        soc = start_soc + (end_soc - start_soc) * fraction
        app.states["sensor.power"] = str(power)
        learner.sample(
            start + timedelta(minutes=minute),
            [soc],
            [5.12],
        )
    app.states["sensor.power"] = "0"
    learner.sample(
        start + timedelta(minutes=samples + 1),
        [end_soc],
        [5.12],
    )


def test_passive_learning_keeps_charge_and_discharge_separate():
    zone = ZoneInfo("Europe/Amsterdam")
    app = App()
    with TemporaryDirectory() as directory:
        learner = EfficiencyLearner(
            app,
            [{"name": "Accu 1", "actual_power": "sensor.power"}],
            zone,
            data_file=Path(directory) / "efficiency.json",
        )
        moment = datetime(2026, 7, 27, 0, 0, tzinfo=zone)
        for session in range(4):
            _run_session(
                learner, app,
                moment + timedelta(hours=session * 4),
                "charge", 20, 47,
            )
        for session in range(4):
            _run_session(
                learner, app,
                moment + timedelta(days=1, hours=session * 4),
                "discharge", 80, 48,
            )

        attributes = app.published[
            "sensor.openems_accu_1_zelfgeleerd_rendement"
        ]["attributes"]
        charge = attributes["laden"]
        discharge = attributes["ontladen"]
        assert charge["betrouwbaarheid"] == "hoog"
        assert discharge["betrouwbaarheid"] == "hoog"
        assert 90 <= charge["rendement_pct"] <= 100
        assert 90 <= discharge["rendement_pct"] <= 100
        assert charge["voorkeursvermogen_w"] == 1000
        assert discharge["voorkeursvermogen_w"] == 1000
        assert charge["sessies"] == 4
        assert discharge["sessies"] == 4
