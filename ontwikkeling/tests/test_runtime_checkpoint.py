from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from runtime_checkpoint import RuntimeCheckpointManager
from models import DischargeBlock, DischargePlan


TZ = ZoneInfo("Europe/Amsterdam")


class DummyApp:
    def __init__(self):
        self.states = {}
        self.published = {}
        self.states["input_boolean.openems_actief"] = "on"
        self.manual_controller = SimpleNamespace(
            enabled_helper="input_boolean.openems_actief"
        )
        self.controller = SimpleNamespace(enabled=True)
        self.runtime_settings = SimpleNamespace(
            grid_charging_allowed=lambda: True
        )

    def get_state(self, entity):
        return self.states.get(entity)

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, *args, **kwargs):
        pass

    def run_in(self, callback, delay, **kwargs):
        return (callback.__name__, delay, kwargs)


def manager():
    temporary = TemporaryDirectory()
    instance = RuntimeCheckpointManager(
        DummyApp(),
        TZ,
        data_file=Path(temporary.name) / "runtime.json",
    )
    instance._test_temporary = temporary
    return instance


def test_staggered_charge_segments_resume_per_battery():
    instance = manager()
    now = datetime.now(TZ)
    data = {
        "charge_segments": [
            [{
                "start": (now - timedelta(minutes=5)).isoformat(),
                "end": (now + timedelta(minutes=55)).isoformat(),
                "power": 1100,
            }],
            [{
                "start": (now + timedelta(minutes=25)).isoformat(),
                "end": (now + timedelta(minutes=85)).isoformat(),
                "power": 1300,
            }],
            [],
        ]
    }
    assert instance._active_charge_powers(data, now) == [
        1100.0, 0.0, 0.0
    ]
    assert instance._active_charge_powers(
        data, now + timedelta(minutes=30)
    ) == [1100.0, 1300.0, 0.0]


def test_expired_charge_checkpoint_is_not_kept_active():
    instance = manager()
    now = datetime.now(TZ)
    instance.override = {
        "owner": "charging",
        "charge_segments": [[{
            "start": (now - timedelta(hours=2)).isoformat(),
            "end": (now - timedelta(hours=1)).isoformat(),
            "power": 1000,
        }]],
    }
    assert instance._override_valid(now) is False


def test_runtime_checkpoint_is_stored_outside_app_code_directory():
    source = (
        Path(__file__).parents[2]
        / "addon_configs"
        / "a0d7b954_appdaemon"
        / "apps"
        / "OpenEMS-M"
        / "runtime_checkpoint.py"
    ).read_text(encoding="utf-8")
    assert '/ "OpenEMS-M-data"' in source
    assert '/ "actieve_runtime.json"' in source


def test_execution_waits_for_recovery_before_new_scheduler_command():
    source = (
        Path(__file__).parents[2]
        / "addon_configs"
        / "a0d7b954_appdaemon"
        / "apps"
        / "OpenEMS-M"
        / "marstek_ems.py"
    ).read_text(encoding="utf-8")
    execute = source.index("def execute_plan")
    pending = source.index(
        "if self.runtime_checkpoint.startup_pending():", execute
    )
    handoff = source.index(
        "self.runtime_checkpoint.handoff_to_fresh_plan()", execute
    )
    override = source.index(
        "if self.runtime_checkpoint.apply_override():", execute
    )
    scheduler = source.index("self.scheduler.evaluate(", execute)
    freshness = source.index("freshness = self.telemetry_guard.check(", execute)
    assert execute < pending < freshness < handoff < override < scheduler


def test_fresh_plan_keeps_charge_recovery_until_original_end():
    instance = manager()
    now = datetime.now(TZ)
    instance.pending = False
    instance.armed = False
    instance.override = {
        "owner": "charging",
        "charge_segments": [[{
            "start": (now - timedelta(minutes=5)).isoformat(),
            "end": (now + timedelta(minutes=55)).isoformat(),
            "power": 1000,
        }]],
    }

    assert instance.handoff_to_fresh_plan() is False
    assert instance.override is not None


def test_active_charge_is_restored_with_original_per_battery_power():
    instance = manager()
    app = instance.app
    now = datetime.now(TZ)
    commands = []
    app.controller = SimpleNamespace(
        enabled=True,
        batteries=[{}, {}, {}],
        desired_state=None,
        desired_powers=[],
        set_charge=lambda powers, source: (
            commands.append((list(powers), source)) or True
        ),
        set_stop=lambda source: True,
    )
    app.manual_controller = SimpleNamespace(
        enabled_helper="input_boolean.openems_actief"
    )
    app.controller_test = SimpleNamespace(
        helper_entity="input_select.marstek_controller_test"
    )
    app.scheduler = SimpleNamespace(_failed_state_latch=None)
    app.coordinated_self_consumption = SimpleNamespace(
        fallback_latched=False
    )
    app.battery_manager = SimpleNamespace(
        read=lambda: SimpleNamespace(soc_values=[30, 30, 30])
    )
    app.safety_service = SimpleNamespace(
        all_safe=lambda operation, indexes=None: True
    )
    app.states.update({
        "input_boolean.openems_actief": "on",
        "input_select.marstek_controller_test": "Uit",
    })
    instance.saved = {
        "version": instance.VERSION,
        "saved_at": now.isoformat(),
        "owner": "charging",
        "target_soc": 100,
        "charge_segments": [
            [{
                "start": (now - timedelta(minutes=5)).isoformat(),
                "end": (now + timedelta(minutes=55)).isoformat(),
                "power": 1192,
            }],
            [{
                "start": (now + timedelta(minutes=25)).isoformat(),
                "end": (now + timedelta(minutes=85)).isoformat(),
                "power": 1303,
            }],
            [],
        ],
    }
    restored, reason = instance._restore_saved()
    assert restored is True
    assert "laadproces" in reason
    assert commands == [([1192.0, 0.0, 0.0], "recovery")]


def test_export_plan_roundtrip_preserves_original_hour_boundaries():
    instance = manager()
    start = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    block = DischargeBlock(
        start=start,
        end=start + timedelta(hours=1),
        average_price=0.40,
        highest_price=0.40,
        planned_total_power=1539,
        planned_export_power=1000,
        reserve_soc=50,
    )
    plan = DischargePlan(
        price_limit=0.30,
        available_energy=3.0,
        blocks=[block],
        reserve_soc=50,
    )

    restored = instance._restore_discharge_plan(
        instance._encode_dataclass(plan)
    )

    assert restored.blocks[0].start == start
    assert restored.blocks[0].end == start + timedelta(hours=1)
    assert restored.blocks[0].reserve_soc == 50
