from datetime import datetime, timedelta, timezone

from energy_scheduler import EnergyScheduler
from models import (
    BatteryChargePlan, BatteryChargeSegment, BatteryState, ChargePlan,
    DischargeBlock, DischargePlan, EMSContext, SchedulerState,
)


class DummyApp:
    def __init__(self):
        self.callbacks = []
        self.logs = []

    def log(self, message, **kwargs):
        self.logs.append((message, kwargs))

    def run_in(self, callback, delay, **kwargs):
        self.callbacks.append((callback, delay, kwargs))
        return len(self.callbacks)

    def run_callback(self, name):
        for index, (callback, _delay, kwargs) in enumerate(self.callbacks):
            if callback.__name__ == name:
                self.callbacks.pop(index)
                callback(kwargs)
                return
        raise AssertionError(f"Callback niet gevonden: {name}")

    def get_state(self, entity):
        return None


class DummyController:
    def __init__(self, verification_results=None):
        self.enabled = True
        self.verification_results = list(verification_results or [True])
        self.charge_calls = 0
        self.charge_powers = []
        self.stop_calls = 0
        self.self_consumption_calls = 0
        self.invalidations = 0

    def set_charge(self, powers, source="auto"):
        self.charge_calls += 1
        self.charge_powers.append(list(powers))
        return True

    def set_stop(self, source="auto"):
        self.stop_calls += 1
        return True

    def set_self_consumption(self, source="auto"):
        self.self_consumption_calls += 1
        return True

    def verify_state(self, state, expected_powers=None):
        ok = self.verification_results.pop(0)
        return {"ok": ok, "summary": "OK" if ok else "FOUT"}

    def invalidate_last_command(self, reason=""):
        self.invalidations += 1


def make_context(now, charging=True):
    battery = BatteryState(
        names=["Accu 1"], soc_values=[50.0], capacities=[5.12],
        capacity=5.12, average_soc=50.0, stored_energy=2.56,
        max_charge_values=[2500.0], max_discharge_values=[2500.0],
        max_charge=2500.0, max_discharge=2500.0,
    )
    context = EMSContext(
        now=now, battery=battery, target_soc=90.0, minimum_soc=20.0,
        price_margin=0.01, discharge_margin=0.06,
        charge_efficiency=0.92, battery_energy_needed=2.0,
        needed_energy=2.17, hours_needed=0.87,
        charge_distribution=[1000.0],
    )
    if charging:
        context.charge_plan = ChargePlan(
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=5),
            power=1000, hours=1, average_price=0.10,
            cheapest_price=0.09, price_limit=0.11,
        )
    return context


def test_determine_sends_no_controller_command():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc))

    assert scheduler.determine(context) == SchedulerState.CHARGING
    assert controller.charge_calls == 0


def test_same_state_does_not_repeat_command():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc))

    scheduler.evaluate(context)
    scheduler.evaluate(context)
    assert controller.charge_calls == 1


def test_external_export_stop_forces_self_consumption_to_resume():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now, charging=False)
    context.discharge_plan = DischargePlan(
        price_limit=0.25,
        available_energy=2.0,
        blocks=[DischargeBlock(
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=5),
            average_price=0.30,
            highest_price=0.32,
        )],
    )

    scheduler.current_state = SchedulerState.SELF_CONSUMPTION
    scheduler.mark_external_stop("Terugleverblok beëindigd")
    state = scheduler.evaluate(context)

    assert state == SchedulerState.SELF_CONSUMPTION
    assert controller.self_consumption_calls == 1


def test_external_export_handoff_goes_directly_to_self_consumption():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now, charging=False)
    context.discharge_plan = DischargePlan(
        price_limit=0.25,
        available_energy=2.0,
        blocks=[DischargeBlock(
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=5),
            average_price=0.30,
            highest_price=0.32,
        )],
    )

    scheduler.current_state = SchedulerState.SELF_CONSUMPTION
    scheduler.mark_external_handoff("Terugleverblok beëindigd")
    state = scheduler.evaluate(context)

    assert state == SchedulerState.SELF_CONSUMPTION
    assert controller.stop_calls == 0
    assert controller.self_consumption_calls == 1


def test_external_export_handoff_goes_directly_to_charging():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc), charging=True)

    scheduler.current_state = SchedulerState.SELF_CONSUMPTION
    scheduler.mark_external_handoff("Terugleverblok beëindigd")
    state = scheduler.evaluate(context)

    assert state == SchedulerState.CHARGING
    assert controller.stop_calls == 0
    assert controller.charge_calls == 1


def test_external_export_handoff_stops_only_when_next_state_is_idle():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc), charging=False)

    scheduler.current_state = SchedulerState.SELF_CONSUMPTION
    scheduler.mark_external_handoff("Terugleverblok beëindigd")
    state = scheduler.evaluate(context)

    assert state == SchedulerState.STOPPED
    assert controller.stop_calls == 1
    assert controller.charge_calls == 0
    assert controller.self_consumption_calls == 0


def test_external_export_stop_remains_stopped_without_active_block():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc), charging=False)

    scheduler.current_state = SchedulerState.SELF_CONSUMPTION
    scheduler.mark_external_stop("Terugleverblok beëindigd")
    state = scheduler.evaluate(context)

    assert state == SchedulerState.STOPPED
    assert controller.self_consumption_calls == 0
    assert controller.stop_calls == 0


def test_fresh_scheduler_resumes_active_charge_block_after_restart():
    app = DummyApp()
    controller = DummyController()
    restarted_scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc))

    state = restarted_scheduler.evaluate(context)

    assert state == SchedulerState.CHARGING
    assert controller.charge_calls == 1
    assert controller.charge_powers == [[1000.0]]


def test_automatic_verification_publishes_per_battery_ok_status():
    app = DummyApp()
    controller = DummyController()
    controller.batteries = [{"name": "Accu 1"}]
    controller.verify_state = lambda state, expected_powers=None: {
        "ok": True,
        "summary": "Accu 1=OK",
        "batteries": [
            {
                "index": 0,
                "name": "Accu 1",
                "ok": True,
                "work_mode": "anti_feed",
                "rs485": "off",
            }
        ],
    }
    scheduler = EnergyScheduler(app, controller)

    scheduler.evaluate(make_context(datetime.now(timezone.utc)))
    assert scheduler.status()["battery_statuses"][0]["status"] == "CONTROLEREN"
    app.run_callback("_verify_callback")

    item = scheduler.status()["battery_statuses"][0]
    assert item["status"] == "OK"
    assert "RS485=off" in item["details"]


def test_one_retry_then_latch_until_state_changes():
    app = DummyApp()
    controller = DummyController([False, False, True])
    scheduler = EnergyScheduler(
        app, controller, verification_delay=8,
        retry_delay=30, max_verification_retries=1,
    )
    now = datetime.now(timezone.utc)
    context = make_context(now)

    scheduler.evaluate(context)
    assert scheduler.status()["verification_at"] is not None
    app.run_callback("_verify_callback")
    assert scheduler.status()["retry_pending"] is True
    assert scheduler.status()["verification_at"] is not None

    app.run_callback("_retry_callback")
    assert controller.charge_calls == 2
    app.run_callback("_verify_callback")
    assert scheduler.status()["fault_latched"] is True

    scheduler.evaluate(context)
    assert controller.charge_calls == 2

    stopped_context = make_context(now, charging=False)
    scheduler.evaluate(stopped_context)
    assert controller.stop_calls == 1


def test_state_change_cancels_pending_retry_and_stops_immediately():
    app = DummyApp()
    controller = DummyController([False, True])
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)

    scheduler.evaluate(make_context(now))
    app.run_callback("_verify_callback")
    scheduler.evaluate(make_context(now, charging=False))

    assert scheduler.status()["retry_pending"] is False
    assert controller.stop_calls == 1


def test_active_discharge_block_uses_clear_self_consumption_reason():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now, charging=False)
    context.discharge_plan = DischargePlan(
        price_limit=0.25,
        available_energy=2.0,
        blocks=[DischargeBlock(
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=5),
            average_price=0.30,
            highest_price=0.32,
        )],
    )

    assert scheduler.determine(context) == SchedulerState.SELF_CONSUMPTION
    assert context.scheduler_reason == "Zelfconsumptieblok actief"


def test_scheduler_sends_zero_for_battery_outside_its_charge_segment():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now)
    context.battery = BatteryState(
        names=["Accu 1", "Accu 2"],
        soc_values=[50.0, 60.0],
        capacities=[5.12, 5.12],
        capacity=10.24,
        average_soc=55.0,
        stored_energy=5.632,
        max_charge_values=[2500.0, 2500.0],
        max_discharge_values=[2500.0, 2500.0],
        max_charge=5000.0,
        max_discharge=5000.0,
    )
    context.charge_distribution = [800.0, 900.0]
    context.battery_charge_plans = [
        BatteryChargePlan(
            index=0, name="Accu 1", current_soc=50, target_soc=90,
            capacity=5.12, battery_energy_needed=2.0,
            grid_energy_needed=2.2, minimum_power=500,
            planned_power=800, average_price=0.1,
            segments=[BatteryChargeSegment(
                start=now - timedelta(minutes=5),
                end=now + timedelta(minutes=5),
                power=800, price=0.1,
            )],
        ),
        BatteryChargePlan(
            index=1, name="Accu 2", current_soc=60, target_soc=90,
            capacity=5.12, battery_energy_needed=1.5,
            grid_energy_needed=1.7, minimum_power=500,
            planned_power=900, average_price=0.09,
            segments=[BatteryChargeSegment(
                start=now + timedelta(hours=1),
                end=now + timedelta(hours=2),
                power=900, price=0.09,
            )],
        ),
    ]

    scheduler.evaluate(context)

    assert controller.charge_powers == [[800, 0.0]]


def test_scheduler_uses_remaining_economic_hour_when_target_is_not_reached():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now)
    context.charge_plan = ChargePlan(
        start=now - timedelta(hours=1),
        end=now + timedelta(minutes=10),
        power=900,
        hours=1,
        average_price=0.10,
        cheapest_price=0.09,
        price_limit=0.11,
        slots=[{
            "from": (now - timedelta(hours=1)).isoformat(),
            "till": (now + timedelta(minutes=10)).isoformat(),
            "price": 0.10,
        }],
        eligible_slots=[{
            "from": (now - timedelta(hours=1)).isoformat(),
            "till": (now + timedelta(minutes=10)).isoformat(),
            "price": 0.10,
        }],
    )
    context.battery_charge_plans = [BatteryChargePlan(
        index=0,
        name="Accu 1",
        current_soc=50,
        target_soc=90,
        capacity=5.12,
        battery_energy_needed=2.0,
        grid_energy_needed=2.2,
        minimum_power=500,
        planned_power=900,
        average_price=0.10,
        segments=[BatteryChargeSegment(
            start=now - timedelta(hours=1),
            end=now - timedelta(minutes=5),
            power=900,
            price=0.10,
        )],
    )]

    scheduler.evaluate(context)

    assert controller.charge_powers == [[900]]


def test_planned_end_is_not_a_hard_stop_during_economic_hour():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    context = make_context(now)
    planned_end = now
    economic_end = now + timedelta(minutes=33)
    context.charge_plan = ChargePlan(
        start=now - timedelta(hours=1),
        end=planned_end,
        power=900,
        hours=1,
        average_price=0.10,
        cheapest_price=0.09,
        price_limit=0.11,
        eligible_slots=[{
            "from": now - timedelta(hours=1),
            "till": economic_end,
            "price": 0.10,
        }],
    )
    context.battery_charge_plans = [BatteryChargePlan(
        index=0,
        name="Accu 1",
        current_soc=50,
        target_soc=90,
        capacity=5.12,
        battery_energy_needed=2.0,
        grid_energy_needed=2.2,
        minimum_power=500,
        planned_power=900,
        average_price=0.10,
        segments=[BatteryChargeSegment(
            start=now - timedelta(hours=1),
            end=planned_end,
            power=900,
            price=0.10,
        )],
    )]

    assert scheduler.charge_plan_runtime_end(
        context.charge_plan, now
    ) == economic_end
    assert scheduler.evaluate(context) == SchedulerState.CHARGING
    assert controller.charge_powers == [[900]]


def test_scheduler_updates_powers_without_restarting_global_charge_state():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now)
    scheduler.evaluate(context)
    assert controller.charge_powers == [[1000.0]]

    context.charge_distribution = [1500.0]
    scheduler.locked_charge_powers = [1000.0]
    scheduler.evaluate(context)

    assert controller.charge_powers == [[1000.0], [1500.0]]
    assert scheduler.current_state == SchedulerState.CHARGING


def test_stale_telemetry_is_visible_per_battery():
    app = DummyApp()
    controller = DummyController()
    controller.batteries = [
        {"name": "Accu 1"},
        {"name": "Accu 2"},
    ]
    scheduler = EnergyScheduler(app, controller)

    scheduler.mark_telemetry_blocked({
        "ok": False,
        "problems": [{
            "accu_index": 0,
            "reden": "Accu 1 werkelijk vermogen: 130s oud (max 120s)",
        }],
    })

    statuses = scheduler.status()["battery_statuses"]
    assert statuses[0]["status"] == "SENSORDATA TE OUD"
    assert "130s oud" in statuses[0]["details"]
    assert statuses[1]["status"] == "GEBLOKKEERD"
    assert "Geen nieuwe opdracht" in scheduler.last_execution


def test_scheduler_reapplies_same_state_after_telemetry_recovers():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc))
    scheduler.evaluate(context)
    assert controller.charge_calls == 1

    scheduler.mark_telemetry_blocked({
        "ok": False,
        "problems": [{
            "accu_index": 0,
            "reden": "Accuvermogen te oud",
        }],
    })
    scheduler.evaluate(context)

    assert controller.charge_calls == 2
    assert scheduler.telemetry_blocked() is False


def test_target_100_treats_99_percent_as_reached_for_rest_of_block():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now)
    context.target_soc = 100.0
    context.battery = BatteryState(
        names=["Accu 1"], soc_values=[99.0], capacities=[5.12],
        capacity=5.12, average_soc=99.0, stored_energy=5.0688,
        max_charge_values=[2500.0], max_discharge_values=[2500.0],
        max_charge=2500.0, max_discharge=2500.0,
    )
    context.charge_plan = ChargePlan(
        start=now - timedelta(minutes=5),
        end=now + timedelta(minutes=8),
        power=500, hours=1, average_price=0.10,
        cheapest_price=0.09, price_limit=0.11,
        eligible_slots=[{
            "from": now - timedelta(minutes=19),
            "till": now + timedelta(minutes=41),
            "price": 0.10,
        }],
    )
    context.charge_distribution = [500.0]

    state = scheduler.evaluate(context)

    assert state == SchedulerState.STOPPED
    assert controller.charge_calls == 0
    assert controller.stop_calls == 1
    assert scheduler.target_charge_latches[0]["soc"] == 99.0


def test_target_below_100_requires_the_actual_target():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    context = make_context(datetime.now(timezone.utc))
    context.target_soc = 95.0
    context.battery = BatteryState(
        names=["Accu 1"], soc_values=[94.0], capacities=[5.12],
        capacity=5.12, average_soc=94.0, stored_energy=4.8128,
        max_charge_values=[2500.0], max_discharge_values=[2500.0],
        max_charge=2500.0, max_discharge=2500.0,
    )

    assert scheduler.evaluate(context) == SchedulerState.CHARGING
    assert controller.charge_powers == [[1000.0]]
    assert scheduler.target_charge_latches == {}


def test_target_latch_releases_after_three_percent_soc_drop():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    context = make_context(now)
    context.target_soc = 100.0
    context.battery = BatteryState(
        names=["Accu 1"], soc_values=[99.0], capacities=[5.12],
        capacity=5.12, average_soc=99.0, stored_energy=5.0688,
        max_charge_values=[2500.0], max_discharge_values=[2500.0],
        max_charge=2500.0, max_discharge=2500.0,
    )
    scheduler.determine(context)
    assert 0 in scheduler.target_charge_latches

    context.battery = BatteryState(
        names=["Accu 1"], soc_values=[96.9], capacities=[5.12],
        capacity=5.12, average_soc=96.9, stored_energy=4.961,
        max_charge_values=[2500.0], max_discharge_values=[2500.0],
        max_charge=2500.0, max_discharge=2500.0,
    )

    assert scheduler.determine(context) == SchedulerState.CHARGING
    assert scheduler.target_charge_latches == {}


def test_latched_soc_is_reported_to_planner_as_target_until_block_ends():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)
    scheduler.target_charge_latches[0] = {
        "soc": 99.0,
        "target": 100.0,
        "block_end": now + timedelta(minutes=30),
    }

    assert scheduler.planning_soc_values([99.0], 100.0, now) == [100.0]


def test_full_target_zone_is_applied_to_planner_before_runtime_latch_exists():
    app = DummyApp()
    controller = DummyController()
    scheduler = EnergyScheduler(app, controller)
    now = datetime.now(timezone.utc)

    assert scheduler.target_charge_latches == {}
    assert scheduler.planning_soc_values([99.0, 98.9], 100.0, now) == [
        100.0,
        98.9,
    ]
