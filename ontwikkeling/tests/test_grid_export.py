from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from grid_export import GridExportManager, GridExportPlanner
from models import DischargeBlock, DischargePlan
from safety_service import BatterySafetyService


TZ = ZoneInfo("Europe/Amsterdam")


def test_tiny_export_share_is_removed_and_redistributed():
    powers = GridExportPlanner.distribute_power(
        4036, [26.1, 15.1, 0.1], [2500, 2500, 2500]
    )

    assert powers == [2500.0, 1536.0, 0.0]


def test_tiny_export_share_stays_off_when_other_batteries_are_full():
    powers = GridExportPlanner.distribute_power(
        5050, [10, 10, 0.1], [2500, 2500, 2500]
    )

    assert powers == [2500.0, 2500.0, 0.0]


def test_export_efficiency_mode_controls_use_of_power_profiles():
    instance, app, settings, _controller = manager()
    app.efficiency_learner = SimpleNamespace(
        discharge_power_profiles=lambda: [{
            "rendement_pct": 95,
            "betrouwbaarheid": "hoog",
            "voorkeursvermogen_w": 1250,
            "vermogensgebieden": [],
        }] * 3
    )

    settings.export_efficiency = lambda: (1.0, "geen rendement")
    assert instance._learned_discharge_profiles() == {}

    settings.export_efficiency = lambda: (0.9, "handmatig")
    assert instance._learned_discharge_profiles() == {}

    settings.export_efficiency = lambda: (0.92, "zelfgeleerd")
    assert set(instance._learned_discharge_profiles()) == {0, 1, 2}


class DummyApp:
    def __init__(self):
        self.states = {
            "sensor.home": "1000",
            "sensor.grid": "0",
            "input_number.openems_maximaal_exportvermogen": "7500",
        }
        self.published = {}
        self._context = None
        self.callbacks = []

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, *args, **kwargs):
        pass

    def run_in(self, callback, delay, **kwargs):
        self.callbacks.append((callback, delay, kwargs))

    def execute_plan(self, kwargs):
        pass


class DummySettings:
    def __init__(self, mode="automatic"):
        self.mode = mode
        self.export_allowed = True

    def grid_export_mode(self):
        return self.mode

    def grid_export_allowed(self):
        return self.export_allowed

    def grid_display_entity(self):
        return "sensor.grid"

    def grid_export_price(self):
        return 0.40

    def home_entity(self):
        return "sensor.home"

    def effective_efficiencies(self):
        return [0.92] * 3, [0.92] * 3, []


class DummyController:
    def __init__(self):
        self.enabled = True
        self.desired_powers = [0.0, 0.0, 0.0]
        self.desired_state = "stopped"
        self.commands = []
        self.batteries = [{}, {}, {}]

    def set_discharge(self, powers, source):
        self.commands.append(("discharge", list(powers), source))
        self.desired_powers = list(powers)
        self.desired_state = "forced_discharge"
        return True

    def set_stop(self, source):
        self.commands.append(("stop", source))
        self.desired_state = "stopped"
        self.desired_powers = [0.0, 0.0, 0.0]
        return True


def manager(mode="automatic"):
    temporary = TemporaryDirectory()
    app = DummyApp()
    settings = DummySettings(mode)
    controller = DummyController()
    battery = SimpleNamespace(
        names=["Accu 1", "Accu 2", "Accu 3"],
        soc_values=[90.0, 80.0, 70.0],
        capacities=[5.12, 5.12, 5.12],
        max_discharge_values=[2500.0, 2500.0, 2500.0],
    )
    now = datetime.now(TZ)
    block = DischargeBlock(
        start=now - timedelta(minutes=5),
        end=now + timedelta(minutes=55),
        average_price=0.40,
        highest_price=0.40,
        planned_total_power=6000,
        planned_export_power=5000,
        powers_per_battery=[2000, 2000, 2000],
    )
    plan = DischargePlan(
        price_limit=0.25,
        available_energy=6,
        blocks=[block],
        reserve_soc=40,
        minimum_price_difference=0.05,
        planned_total_power=6000,
        planned_export_power=5000,
    )
    app._context = SimpleNamespace(
        minimum_soc=20,
        discharge_plan=None,
        export_plan=plan,
    )
    instance = GridExportManager(
        app=app,
        settings=settings,
        controller=controller,
        battery_manager=SimpleNamespace(read=lambda: battery),
        grid_calibrator=SimpleNamespace(
            corrected_grid_power=lambda: 100.0
        ),
        telemetry_guard=SimpleNamespace(
            check=lambda **kwargs: {"ok": True, "problems": []}
        ),
        financial_tracker=SimpleNamespace(
            average_inventory_cost=lambda: 0.20
        ),
        safety_service=SimpleNamespace(all_safe=lambda operation: True),
        timezone=TZ,
        data_file=Path(temporary.name) / "events.json",
    )
    instance._test_temporary = temporary
    return instance, app, settings, controller


def test_automatic_export_starts_with_bounded_step():
    instance, _app, _settings, controller = manager()
    instance.tick()
    assert controller.commands[0][0] == "discharge"
    assert sum(controller.commands[0][1]) <= 750.1


def test_abnormal_grid_voltage_blocks_export_before_discharge_command():
    instance, app, _settings, controller = manager()
    app.grid_status_monitor = SimpleNamespace(
        allows_export=lambda: False,
        status_reason=lambda: (
            "Netspanning boven 253 V; terugleveren geblokkeerd"
        ),
    )

    instance.tick()

    assert not any(command[0] == "discharge" for command in controller.commands)
    state, attributes = app.published[instance.STATUS_SENSOR]
    assert "boven 253 V" in attributes["reden"]


def test_missing_battery_sensor_data_is_handled_without_crashing():
    instance, app, _settings, controller = manager()

    def raise_missing_soc():
        raise ValueError(
            "Geen geldige waarde voor "
            "sensor.berging_accu2_batterij_soc: unavailable"
        )

    instance.battery_manager = SimpleNamespace(read=raise_missing_soc)

    instance.tick()

    assert not any(
        command[0] == "discharge" for command in controller.commands
    )
    state, attributes = app.published[instance.STATUS_SENSOR]
    assert "niet actueel" in attributes["reden"]


def test_export_continues_when_one_battery_is_temporarily_quarantined():
    class Health:
        @staticmethod
        def is_available(index):
            return index != 2

        @staticmethod
        def status(index):
            return {"reason": "drie herstelpogingen mislukt"}

    instance, app, _settings, controller = manager()
    safety = BatterySafetyService(app, controller.batteries)
    safety.bind_health_manager(Health())
    instance.safety_service = safety

    instance.tick()

    assert controller.commands
    assert controller.commands[0][0] == "discharge"
    assert not any(command[0] == "stop" for command in controller.commands)


def test_runtime_uses_cost_basis_stored_on_planned_block():
    instance, app, _settings, _controller = manager()
    block = app._context.export_plan.blocks[0]
    object.__setattr__(block, "cost_basis_per_kwh", 0.37)

    decision = instance._decision()

    assert decision["geschikt"] is False
    assert decision["vergelijkingskostprijs_per_kwh"] == 0.37


def test_grid_export_permission_blocks_real_export():
    instance, _app, settings, controller = manager()
    settings.export_allowed = False
    instance.tick()
    assert controller.commands == []
    assert instance.last_decision["geschikt"] is False


def test_running_export_block_survives_plan_recalculation():
    instance, app, _settings, controller = manager()
    original_block = app._context.export_plan.blocks[0]

    instance.tick()
    assert instance.active is True
    app._context.export_plan.blocks.clear()
    instance.tick()

    assert instance.active is True
    assert instance.active_block() is original_block
    assert not any(command[0] == "stop" for command in controller.commands)


def test_hard_grid_limit_survives_invalid_7500_w_helper_value():
    instance, _app, _settings, _controller = manager()
    decision = instance._decision()
    assert decision["gewenst_exportvermogen_w"] == 5000
    assert decision["gewenst_ontlaadvermogen_w"] <= 7500
    # Netexport max 5000 W; het woningverbruik mag daar bovenop (marge 0 W).


def test_planned_power_is_total_battery_power_not_net_export_plus_home():
    instance, app, _settings, _controller = manager()
    app.states["sensor.home"] = "400"
    block = app._context.export_plan.blocks[0]
    object.__setattr__(block, "planned_total_power", 1500)
    object.__setattr__(block, "planned_export_power", 1500)

    decision = instance._decision()

    assert decision["gewenst_ontlaadvermogen_w"] == 1500
    assert decision["gewenst_exportvermogen_w"] == 1100


def test_home_load_is_floor_when_it_exceeds_planned_total_power():
    instance, app, _settings, _controller = manager()
    app.states["sensor.home"] = "1800"
    block = app._context.export_plan.blocks[0]
    object.__setattr__(block, "planned_total_power", 1500)
    object.__setattr__(block, "planned_export_power", 1500)

    decision = instance._decision()

    assert decision["gewenst_ontlaadvermogen_w"] == 1500
    assert decision["gewenst_exportvermogen_w"] == 0


def test_export_discharge_does_not_separately_compensate_home_consumption():
    instance, app, _settings, _controller = manager()
    app.states["sensor.home"] = "539"
    block = app._context.export_plan.blocks[0]
    object.__setattr__(block, "planned_export_power", 25)
    object.__setattr__(block, "planned_total_power", 25)

    decision = instance._decision()

    assert decision["gewenst_ontlaadvermogen_w"] == 25


def test_export_uses_fixed_cycle_order_without_stateful_soc_rounds():
    instance, app, _settings, controller = manager()

    class Selector:
        def __init__(self):
            self.calls = []

        def export_order(self, battery, reserve_soc):
            self.calls.append(reserve_soc)
            return [2, 0, 1]

    selector = Selector()
    app.coordinated_self_consumption = SimpleNamespace(
        battery_selector=selector
    )
    instance.tick()

    assert selector.calls
    assert controller.commands[0][1] == [250.0, 250.0, 250.0]
    assert instance._fixed_export_indexes == [2, 0, 1]


def test_export_does_not_stop_on_forecast_energy_before_reserve():
    instance, app, _settings, controller = manager()
    scheduler = SimpleNamespace(reasons=[])
    scheduler.mark_external_stop = scheduler.reasons.append
    app.scheduler = scheduler
    block = app._context.export_plan.blocks[0]
    object.__setattr__(block, "expected_energy", 0.001)
    controller.batteries[0]["actual_power"] = "sensor.actual_1"
    app.states["sensor.actual_1"] = "1000"

    instance.tick()
    instance._last_energy_sample_at -= 4.0
    instance.tick()

    assert instance.active is True
    assert controller.commands[-1][0] == "discharge"
    assert instance._delivered_energy_kwh >= 0.001


def test_fixed_export_selection_stays_equal_when_soc_order_changes():
    instance, app, _settings, _controller = manager()
    instance.settings.export_efficiency = lambda: (0.92, "zelfgeleerd")
    app.efficiency_learner = SimpleNamespace(
        discharge_power_profiles=lambda: [
            {
                "betrouwbaarheid": "hoog",
                "voorkeursvermogen_w": 1100,
            }
        ] * 3
    )

    class Selector:
        def __init__(self):
            self.order = [0, 2, 1]

        def export_order(self, battery, reserve_soc):
            return list(self.order)

    selector = Selector()
    app.coordinated_self_consumption = SimpleNamespace(
        battery_selector=selector
    )
    battery = instance.battery_manager.read()
    first = instance._selector_powers(2197, battery, 40, 2197)
    selector.order = [2, 0, 1]
    second = instance._selector_powers(2197, battery, 40, 2197)

    assert instance._fixed_export_indexes == [0, 2]
    assert first == second == [1098.5, 0.0, 1098.5]


def test_detected_charging_blocks_export_and_sends_stop():
    instance, app, _settings, controller = manager()
    controller.batteries[1] = {
        "name": "Accu 2",
        "actual_power": "sensor.actual_2",
    }
    app.states["sensor.actual_2"] = "-1800"

    instance.tick()

    assert controller.commands == [("stop", "export")]
    assert "Tegengestelde richting" in instance.last_decision["reden"]


def test_charging_without_active_export_never_stops_the_charge_block():
    instance, app, _settings, controller = manager()
    object.__setattr__(app._context.export_plan, "blocks", [])
    controller.batteries[1] = {
        "name": "Accu 2",
        "actual_power": "sensor.actual_2",
    }
    app.states["sensor.actual_2"] = "-1800"

    instance.tick()

    assert controller.commands == []
    assert instance.last_decision["reden"] == "Geen gepland exportblok actief"


def test_planned_export_reserve_is_authoritative_at_runtime():
    instance, app, settings, _controller = manager()
    settings.fixed_export_reserve_kwh = lambda: 7.68
    block = app._context.export_plan.blocks[0]
    object.__setattr__(block, "reserve_soc", 25)

    decision = instance._decision()

    assert decision["reserve_soc"] == 25


def test_minimum_soc_remains_an_absolute_export_floor():
    instance, app, _settings, _controller = manager()
    block = app._context.export_plan.blocks[0]
    object.__setattr__(block, "reserve_soc", 10)

    decision = instance._decision()

    assert decision["reserve_soc"] == 20


def test_measured_export_above_5000_w_bypasses_normal_downward_step():
    instance, _app, _settings, controller = manager()
    instance.active = True
    controller.desired_state = "forced_discharge"
    controller.desired_powers = [2000, 2000, 2000]
    instance.grid_calibrator.corrected_grid_power = lambda: -5500
    instance.tick()
    command = controller.commands[-1]
    if command[0] == "discharge":
        assert sum(command[1]) <= 5500
    else:
        assert command == ("stop", "export")


def test_simulation_never_sends_controller_command():
    instance, _app, _settings, controller = manager("simulation")
    instance.tick()
    assert controller.commands == []


def test_leaving_automatic_mode_stops_active_export():
    instance, _app, settings, controller = manager()
    instance.tick()
    settings.mode = "off"
    instance.tick()
    assert controller.commands[-1] == ("stop", "export")


def test_ending_export_marks_scheduler_stopped_before_plan_resumes():
    instance, app, settings, controller = manager()
    scheduler = SimpleNamespace(reasons=[])
    scheduler.mark_external_stop = scheduler.reasons.append
    app.scheduler = scheduler
    instance.tick()

    settings.mode = "off"
    instance.tick()

    assert controller.commands[-1] == ("stop", "export")
    assert scheduler.reasons == ["Terugleverblok beëindigd"]
    assert app.callbacks[-1][0] == app.execute_plan
    assert app.callbacks[-1][1] == 0


def test_stale_forced_discharge_is_stopped_even_when_active_flag_is_false():
    instance, app, settings, controller = manager()
    scheduler = SimpleNamespace(reasons=[])
    scheduler.mark_external_stop = scheduler.reasons.append
    app.scheduler = scheduler
    controller.desired_state = "forced_discharge"
    instance.active = False

    instance._deactivate(safety=False)

    assert controller.commands[-1] == ("stop", "export")
    assert app.callbacks[-1][0] == app.execute_plan


def test_expired_export_is_closed_before_next_scheduler_mode():
    instance, app, _settings, controller = manager()
    scheduler = SimpleNamespace(stops=[], handoffs=[])
    scheduler.mark_external_stop = scheduler.stops.append
    scheduler.mark_external_handoff = scheduler.handoffs.append
    app.scheduler = scheduler
    instance.active = True
    controller.desired_state = "forced_discharge"
    old = instance._active_block()
    instance._latched_block = DischargeBlock(
        start=old.start - timedelta(hours=2),
        end=old.start - timedelta(hours=1),
        average_price=old.average_price,
        highest_price=old.highest_price,
    )

    assert instance.finish_if_expired() is True
    assert instance.active is False
    assert not any(command[0] == "stop" for command in controller.commands)
    assert scheduler.stops == []
    assert scheduler.handoffs == ["Terugleverblok beëindigd"]
    assert app.callbacks[-1][0] == app.execute_plan


def test_reserve_reached_blocks_export():
    instance, _app, _settings, controller = manager()
    instance.battery_manager.read().soc_values[:] = [40, 40, 40]
    instance.tick()
    assert controller.commands == []
    assert "reserve" in instance.last_decision["reden"].lower()


def test_dynamic_block_reserve_overrides_fixed_plan_reserve():
    instance, app, _settings, _controller = manager()
    block = app._context.export_plan.blocks[0]
    app._context.export_plan.blocks[0] = DischargeBlock(
        start=block.start,
        end=block.end,
        average_price=block.average_price,
        highest_price=block.highest_price,
        planned_total_power=block.planned_total_power,
        planned_export_power=block.planned_export_power,
        powers_per_battery=block.powers_per_battery,
        reserve_soc=25,
    )

    decision = instance._decision()

    assert decision["reserve_soc"] == 25


def test_missing_live_home_power_blocks_export_without_fixed_guess():
    instance, app, _settings, controller = manager()
    app.states.pop("sensor.home")
    instance.tick()
    assert controller.commands == []
    assert "woningvermogen ontbreekt" in instance.last_decision["reden"]


def test_event_log_only_adds_on_status_change():
    instance, _app, _settings, _controller = manager("simulation")
    instance.tick()
    instance.tick()
    assert len(instance.data["events"]) == 1


def test_corrupt_event_file_is_preserved():
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "events.json"
        path.write_text("{broken", encoding="utf-8")
        instance, app, settings, controller = manager("off")
        GridExportManager(
            app=app,
            settings=settings,
            controller=controller,
            battery_manager=instance.battery_manager,
            grid_calibrator=instance.grid_calibrator,
            telemetry_guard=instance.telemetry_guard,
            financial_tracker=instance.financial_tracker,
            safety_service=instance.safety_service,
            timezone=TZ,
            data_file=path,
        )
        assert list(Path(temporary).glob("events.corrupt-*.json"))
