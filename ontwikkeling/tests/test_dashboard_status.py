from datetime import datetime, timezone
from types import SimpleNamespace

from dashboard_status import DashboardStatus
from models import (
    BatteryChargePlan, BatteryChargeSegment, DischargeBlock, DischargePlan,
)


class DummyApp:
    def __init__(self):
        self.states = {}

    def set_state(self, entity, state, attributes, replace=True):
        self.states[entity] = (state, attributes)

    def get_state(self, entity):
        value = self.states.get(entity)
        return value[0] if isinstance(value, tuple) else value


def plan(start_hour, end_hour, price):
    return SimpleNamespace(
        start=datetime(2026, 7, 15, start_hour, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, end_hour, tzinfo=timezone.utc),
        average_price=price,
        effective_charge_hours=1.75,
    )


def test_active_self_consumption_start_stays_fixed_until_block_ends():
    status = DashboardStatus(DummyApp())
    day = datetime(2026, 7, 15, tzinfo=timezone.utc)
    original = SimpleNamespace(
        start=day.replace(hour=10),
        end=day.replace(hour=13),
    )
    recalculated = SimpleNamespace(
        start=day.replace(hour=11),
        end=day.replace(hour=13),
    )
    later = SimpleNamespace(
        start=day.replace(hour=14),
        end=day.replace(hour=15),
    )

    assert status._stable_discharge_start(
        original, day.replace(hour=10, minute=5)
    ) == day.replace(hour=10)
    assert status._stable_discharge_start(
        recalculated, day.replace(hour=11, minute=5)
    ) == day.replace(hour=10)
    assert status._stable_discharge_start(
        later, day.replace(hour=14, minute=5)
    ) == day.replace(hour=14)


def test_charge_card_uses_full_price_slot_end_not_early_target():
    zone = timezone.utc
    charge_plan = SimpleNamespace(
        start=datetime(2026, 7, 29, 13, tzinfo=zone),
        end=datetime(2026, 7, 29, 14, 45, tzinfo=zone),
        slots=[
            {
                "from": "2026-07-29T13:00:00+00:00",
                "till": "2026-07-29T14:00:00+00:00",
                "price": 0.12,
            },
            {
                "from": "2026-07-29T14:00:00+00:00",
                "till": "2026-07-29T15:00:00+00:00",
                "price": 0.14,
            },
        ],
    )

    assert DashboardStatus(DummyApp())._charge_display_end(
        charge_plan
    ) == datetime(2026, 7, 29, 15, tzinfo=zone)


def test_self_consumption_is_primary_status_for_every_battery():
    app = DummyApp()
    app.states.update({
        "sensor.power_1": "329",
        "sensor.power_2": "0",
        "sensor.power_3": "0",
    })
    batteries = [
        {"actual_power": f"sensor.power_{index}"}
        for index in range(1, 4)
    ]
    context = SimpleNamespace(
        scheduler_state=SimpleNamespace(value="self_consumption")
    )
    status = DashboardStatus(app)

    assert [
        status._actual_battery_mode(index, batteries, context)
        for index in range(3)
    ] == ["Zelfconsumptie", "Zelfconsumptie", "Zelfconsumptie"]


def test_active_export_shows_zero_power_battery_as_standby():
    app = DummyApp()
    app.grid_export_manager = SimpleNamespace(
        active=True,
        last_decision={"vermogens_per_accu_w": [1404, 2152, 0]},
    )
    batteries = [{"actual_power": None} for _index in range(3)]
    context = SimpleNamespace(scheduler_state=SimpleNamespace(value="idle"))
    status = DashboardStatus(app)

    assert [
        status._actual_battery_mode(index, batteries, context)
        for index in range(3)
    ] == ["Terugleveren", "Terugleveren", "Stand-by"]


def test_charge_sensor_keeps_today_and_next_charge_block():
    app = DummyApp()
    today = plan(11, 13, 0.16)
    tomorrow = SimpleNamespace(
        start=datetime(2026, 7, 16, 10, tzinfo=timezone.utc),
        end=datetime(2026, 7, 16, 12, tzinfo=timezone.utc),
        average_price=0.23,
    )
    context = SimpleNamespace(
        scheduler_reason="Geen laadblok actief",
        battery=SimpleNamespace(
            average_soc=32.3, stored_energy=4.97,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[0.0, 0.0, 0.0],
        charge_efficiency=0.92,
        charge_plan=today,
        discharge_plan=None,
        now=datetime(2026, 7, 15, 17, tzinfo=timezone.utc),
    )
    scheduler_status = {
        "current_state": "stopped",
        "last_execution": "Stop",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(
        context,
        scheduler_status,
        current_price=0.29,
        auto_control_enabled=True,
        dashboard_charge_plan=tomorrow,
    )

    state, attributes = app.states["sensor.openems_volgend_laadblok"]
    assert state.startswith("16-07")
    assert (
        app.states["sensor.openems_gepland_laadvermogen"][0] == "0"
    )
    assert attributes["blokken"] == [
        {"start": today.start.isoformat(), "einde": today.end.isoformat()},
        {
            "start": tomorrow.start.isoformat(),
            "einde": tomorrow.end.isoformat(),
        },
    ]


def test_duplicate_current_and_dashboard_plan_is_published_once():
    app = DummyApp()
    current = plan(11, 13, 0.16)
    context = SimpleNamespace(
        scheduler_reason="Laadblok actief",
        battery=SimpleNamespace(
            average_soc=40.0, stored_energy=6.0, names=["Accu 1"],
        ),
        charge_distribution=[1000.0],
        charge_efficiency=0.92,
        charge_plan=current,
        discharge_plan=None,
        now=datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
    )
    status = {
        "current_state": "charging",
        "last_execution": "Laden",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(
        context, status, 0.16, True, dashboard_charge_plan=current
    )

    blocks = app.states["sensor.openems_volgend_laadblok"][1]["blokken"]
    assert len(blocks) == 1


def test_charge_sensor_publishes_full_economic_price_window_separately():
    app = DummyApp()
    current = plan(14, 16, 0.12)
    current.slots = []
    current.eligible_slots = [
        {
            "from": datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
            "till": datetime(2026, 7, 15, 13, tzinfo=timezone.utc),
            "price": 0.13,
        },
        {
            "from": datetime(2026, 7, 15, 13, tzinfo=timezone.utc),
            "till": datetime(2026, 7, 15, 14, tzinfo=timezone.utc),
            "price": 0.12,
        },
    ]
    context = SimpleNamespace(
        scheduler_reason="Wacht op laadblok",
        battery=SimpleNamespace(
            average_soc=40.0, stored_energy=6.0, names=["Accu 1"],
        ),
        charge_distribution=[0.0],
        charge_efficiency=0.92,
        charge_plan=current,
        discharge_plan=None,
        now=datetime(2026, 7, 15, 11, tzinfo=timezone.utc),
    )
    status = {
        "current_state": "stopped",
        "last_execution": "Wachten",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(context, status, 0.16, True, current)

    attributes = app.states["sensor.openems_volgend_laadblok"][1]
    assert len(attributes["blokken"]) == 1
    assert len(attributes["prijsblokken"]) == 2


def test_price_windows_remain_visible_without_executable_plan():
    app = DummyApp()
    now = datetime(2026, 7, 15, 18, tzinfo=timezone.utc)
    charge_slot = {
        "from": datetime(2026, 7, 16, 12, tzinfo=timezone.utc),
        "till": datetime(2026, 7, 16, 13, tzinfo=timezone.utc),
        "price": 0.12,
    }
    discharge_slot = {
        "from": datetime(2026, 7, 16, 20, tzinfo=timezone.utc),
        "till": datetime(2026, 7, 16, 21, tzinfo=timezone.utc),
        "price": 0.31,
    }
    context = SimpleNamespace(
        scheduler_reason="Doel-SOC bereikt",
        battery=SimpleNamespace(
            average_soc=99.0,
            stored_energy=15.21,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[0.0, 0.0, 0.0],
        charge_efficiency=0.92,
        charge_plan=None,
        discharge_plan=None,
        now=now,
        price_margin=0.02,
        discharge_margin=0.10,
        minimum_soc=20.0,
        hours_needed=0.0,
    )
    status = {
        "current_state": "stopped",
        "last_execution": "Doel bereikt",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(
        context,
        status,
        current_price=0.20,
        auto_control_enabled=True,
        dashboard_charge_price_slots=[charge_slot],
        dashboard_discharge_price_slots=[discharge_slot],
    )

    charge_attrs = app.states["sensor.openems_volgend_laadblok"][1]
    discharge_attrs = app.states["sensor.openems_volgend_ontlaadblok"][1]
    assert charge_attrs["blokken"] == []
    assert charge_attrs["prijsblokken"] == [{
        "start": charge_slot["from"].isoformat(),
        "einde": charge_slot["till"].isoformat(),
    }]
    assert discharge_attrs["blokken"] == []
    assert discharge_attrs["prijsblokken"] == [{
        "start": discharge_slot["from"].isoformat(),
        "einde": discharge_slot["till"].isoformat(),
    }]


def test_actual_discharge_blocks_render_without_charge_plan():
    app = DummyApp()
    now = datetime(2026, 7, 15, 18, tzinfo=timezone.utc)
    start = datetime(2026, 7, 15, 20, tzinfo=timezone.utc)
    end = datetime(2026, 7, 15, 21, tzinfo=timezone.utc)
    slot = {"from": start, "till": end, "price": 0.31}
    block = DischargeBlock(
        start=start,
        end=end,
        average_price=0.31,
        highest_price=0.31,
        slots=[slot],
        expected_energy=0.4,
        energy_cost=0.08,
        avoided_cost=0.124,
        net_profit=0.044,
    )
    discharge_plan = DischargePlan(
        price_limit=0.28,
        available_energy=8.0,
        blocks=[block],
        eligible_slots=[slot],
        cost_basis_per_kwh=0.20,
        cost_basis_source="werkelijke voorraadkostprijs",
    )
    context = SimpleNamespace(
        scheduler_reason="Ontlaadblok gepland",
        battery=SimpleNamespace(
            average_soc=99.0,
            stored_energy=15.21,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[0.0, 0.0, 0.0],
        charge_efficiency=0.92,
        charge_plan=None,
        discharge_plan=discharge_plan,
        now=now,
        price_margin=0.02,
        discharge_margin=0.08,
        minimum_soc=20.0,
        hours_needed=0.0,
    )
    status = {
        "current_state": "stopped",
        "last_execution": "Wachten op ontlaadblok",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(
        context,
        status,
        current_price=0.20,
        auto_control_enabled=True,
        dashboard_charge_price_slots=[],
        dashboard_discharge_price_slots=[slot],
    )

    attrs = app.states["sensor.openems_volgend_ontlaadblok"][1]
    assert attrs["blokken"][0]["start"] == start.isoformat()
    overview = app.states["sensor.openems_planningsoverzicht"][1]
    assert overview["ontlaadplan"]["gemiddelde_laadprijs"] == 0.20
    assert (
        overview["ontlaadplan"]["kostprijsbron"]
        == "werkelijke voorraadkostprijs"
    )


def test_export_simulation_publishes_only_dark_purple_candidates():
    app = DummyApp()
    app.states["input_select.openems_grid_export_modus"] = (
        "Alleen simuleren"
    )
    now = datetime(2026, 7, 15, 18, tzinfo=timezone.utc)
    start = datetime(2026, 7, 15, 20, tzinfo=timezone.utc)
    end = datetime(2026, 7, 15, 21, tzinfo=timezone.utc)
    candidate = {"from": start, "till": end, "price": 0.40}
    block = DischargeBlock(
        start=start,
        end=end,
        average_price=0.40,
        highest_price=0.40,
        planned_export_power=3000,
    )
    export_plan = DischargePlan(
        price_limit=0.30,
        available_energy=4.0,
        blocks=[block],
        eligible_slots=[candidate],
        reserve_soc=40,
        cost_basis_per_kwh=0.25,
        minimum_price_difference=0.05,
        selection_margin=0.02,
        cost_basis_source="werkelijke voorraadkostprijs",
    )
    context = SimpleNamespace(
        scheduler_reason="Zelfconsumptie gepland",
        battery=SimpleNamespace(
            average_soc=80.0, stored_energy=12.0,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[0.0, 0.0, 0.0],
        charge_efficiency=0.92,
        charge_plan=None,
        discharge_plan=None,
        export_plan=export_plan,
        now=now,
        price_margin=0.02,
        discharge_margin=0.05,
        minimum_soc=20.0,
        hours_needed=0.0,
    )
    status = {
        "current_state": "stopped",
        "last_execution": "Wachten",
        "last_verification": "OK",
    }
    DashboardStatus(app).publish(context, status, 0.20, True)
    attrs = app.states["sensor.openems_volgend_exportblok"][1]
    assert attrs["simulatie"] is True
    assert attrs["prijsblokken"] == [{
        "start": start.isoformat(),
        "einde": end.isoformat(),
    }]
    assert attrs["blokken"] == []
    assert attrs["voorraadkostprijs_eur_kwh"] == 0.25
    assert attrs["toegestaan_vanaf_eur_kwh"] == 0.30
    assert attrs["gepland_vanaf_eur_kwh"] is None
    assert attrs["piekmarge_eur_kwh"] == 0.02
    assert attrs["kostprijsbron"] == "werkelijke voorraadkostprijs"


def test_export_off_hides_candidates_and_selected_blocks():
    app = DummyApp()
    app.states["input_select.openems_grid_export_modus"] = "Uit"
    now = datetime(2026, 7, 15, 18, tzinfo=timezone.utc)
    start = datetime(2026, 7, 15, 20, tzinfo=timezone.utc)
    end = datetime(2026, 7, 15, 21, tzinfo=timezone.utc)
    block = DischargeBlock(
        start=start, end=end, average_price=0.40, highest_price=0.40,
        planned_export_power=3000,
    )
    context = SimpleNamespace(
        scheduler_reason="Wachten",
        battery=SimpleNamespace(
            average_soc=80.0, stored_energy=12.0,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[0.0, 0.0, 0.0],
        charge_efficiency=0.92,
        charge_plan=None,
        discharge_plan=None,
        export_plan=DischargePlan(
            price_limit=0.30, available_energy=4.0, blocks=[block],
            eligible_slots=[{"from": start, "till": end, "price": 0.40}],
            reserve_soc=40,
        ),
        now=now, price_margin=0.02, discharge_margin=0.05,
        minimum_soc=20.0, hours_needed=0.0,
    )
    status = {
        "current_state": "stopped",
        "last_execution": "Wachten",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(context, status, 0.20, True)

    attrs = app.states["sensor.openems_volgend_exportblok"][1]
    assert attrs["prijsblokken"] == []
    assert attrs["blokken"] == []


def test_automatic_export_publishes_light_purple_selected_blocks():
    app = DummyApp()
    app.states["input_select.openems_grid_export_modus"] = (
        "Automatisch exporteren"
    )
    now = datetime(2026, 7, 15, 18, tzinfo=timezone.utc)
    start = datetime(2026, 7, 15, 20, tzinfo=timezone.utc)
    end = datetime(2026, 7, 15, 21, tzinfo=timezone.utc)
    block = DischargeBlock(
        start=start,
        end=end,
        average_price=0.40,
        highest_price=0.40,
        planned_export_power=3000,
    )
    context = SimpleNamespace(
        scheduler_reason="Zelfconsumptie gepland",
        battery=SimpleNamespace(
            average_soc=80.0, stored_energy=12.0,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[0.0, 0.0, 0.0],
        charge_efficiency=0.92,
        charge_plan=None,
        discharge_plan=None,
        export_plan=DischargePlan(
            price_limit=0.30,
            available_energy=4.0,
            blocks=[block],
            eligible_slots=[{
                "from": start, "till": end, "price": 0.40,
            }],
            reserve_soc=40,
            cost_basis_per_kwh=0.20,
            minimum_price_difference=0.08,
            selection_margin=0.02,
            planned_export_power=3000,
        ),
        now=now,
        price_margin=0.02,
        discharge_margin=0.05,
        minimum_soc=20.0,
        hours_needed=0.0,
    )
    status = {
        "current_state": "stopped",
        "last_execution": "Wachten",
        "last_verification": "OK",
    }
    DashboardStatus(app).publish(context, status, 0.20, True)
    attrs = app.states["sensor.openems_volgend_exportblok"][1]
    assert attrs["simulatie"] is False
    assert attrs["blokken"] == [{
        "start": start.isoformat(),
        "einde": end.isoformat(),
        "doel_export_w": 3000,
        "gemiddelde_prijs": 0.40,
        "voorraadkostprijs_eur_kwh": None,
    }]
    overview = app.states["sensor.openems_planningsoverzicht"][1]
    export = overview["terugleverplan"]
    assert export["modus"] == "Automatisch exporteren"
    assert export["voorraadkostprijs_eur_kwh"] == 0.20
    assert export["toegestaan_vanaf_eur_kwh"] == 0.30
    assert export["gepland_vanaf_eur_kwh"] is None
    assert export["piekmarge_eur_kwh"] == 0.02
    assert export["reserve_soc_pct"] == 40
    assert export["gepland_exportvermogen_w"] == 3000
    assert export["blokken"][0]["doel_export_w"] == 3000


def test_active_plan_publishes_energy_and_power_per_battery():
    app = DummyApp()
    current = plan(11, 13, 0.16)
    context = SimpleNamespace(
        scheduler_reason="Laadblok actief",
        battery=SimpleNamespace(
            average_soc=40.0,
            stored_energy=6.0,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[1000.0, 1500.0, 2000.0],
        charge_efficiency=0.92,
        charge_plan=current,
        discharge_plan=None,
        now=datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
    )
    status = {
        "current_state": "charging",
        "last_execution": "Laden",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(context, status, 0.16, True, current)

    state, attributes = app.states["sensor.openems_gepland_laadvermogen"]
    assert state == "4500"
    assert attributes["laadblok_actief"] is True
    assert attributes["geplande_energie_kwh"] == 7.25
    assert attributes["per_accu"][0] == {
        "naam": "Accu 1",
        "vermogen_w": 1000,
        "energie_kwh": 1.61,
    }


def test_planning_overview_contains_log_report_details():
    app = DummyApp()
    start = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    charge = SimpleNamespace(
        start=start,
        end=datetime(2026, 7, 15, 16, tzinfo=timezone.utc),
        average_price=0.2513,
        cheapest_price=0.2478,
        price_limit=0.2658,
        effective_charge_hours=4.0,
        hours=4.0,
        slots=[
            {
                "from": start,
                "till": datetime(
                    2026, 7, 15, 13, tzinfo=timezone.utc
                ),
                "price": 0.2550,
            },
        ],
    )
    discharge_block = SimpleNamespace(
        start=datetime(2026, 7, 15, 19, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, 21, tzinfo=timezone.utc),
        hours=2,
        average_price=0.3234,
        highest_price=0.3316,
    )
    discharge = SimpleNamespace(
        price_limit=0.3063,
        available_energy=12.29,
        blocks=[discharge_block],
    )
    context = SimpleNamespace(
        scheduler_reason="Laadblok actief",
        battery=SimpleNamespace(
            average_soc=90.0,
            stored_energy=13.82,
            names=["Accu 1", "Accu 2", "Accu 3"],
        ),
        charge_distribution=[1321.0, 653.0, 1187.0],
        charge_efficiency=0.92,
        charge_plan=charge,
        discharge_plan=discharge,
        now=datetime(2026, 7, 15, 15, tzinfo=timezone.utc),
        price_margin=0.018,
        discharge_margin=0.055,
        minimum_soc=10.0,
        hours_needed=0.22,
    )
    status = {
        "current_state": "charging",
        "last_execution": "Laden",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(context, status, 0.2543, True, charge)

    state, attributes = app.states["sensor.openems_planningsoverzicht"]
    assert state == "laden en ontladen gepland"
    assert attributes["laadplan"]["gemiddelde_prijs"] == 0.2513
    assert attributes["laadplan"]["totaal_vermogen_w"] == 3161
    assert attributes["laadplan"]["per_accu"][2]["naam"] == "Accu 3"
    assert (
        attributes["laadplan"]["per_accu"][2]["laadvermogen_w"]
        == 1187
    )
    assert attributes["laadplan"]["blokken"][0]["prijs"] == 0.255
    assert attributes["ontlaadplan"]["ontladen_vanaf"] == 0.3063
    assert attributes["ontlaadplan"]["blokken"][0]["prijsverschil"] == 0.0721


def test_planning_overview_publishes_individual_battery_timing():
    app = DummyApp()
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    segment = BatteryChargeSegment(
        start=now,
        end=datetime(2026, 7, 15, 12, 36, tzinfo=timezone.utc),
        power=500,
        price=0.10,
    )
    battery_plan = BatteryChargePlan(
        index=0,
        name="Accu 1",
        current_soc=97,
        target_soc=100,
        capacity=5.12,
        battery_energy_needed=0.154,
        grid_energy_needed=0.30,
        minimum_power=500,
        planned_power=500,
        average_price=0.10,
        segments=[segment],
    )
    charge = SimpleNamespace(
        start=segment.start,
        end=segment.end,
        average_price=0.10,
        cheapest_price=0.10,
        price_limit=0.12,
        effective_charge_hours=0.6,
        hours=1.0,
        power=500,
        slots=[],
    )
    context = SimpleNamespace(
        scheduler_reason="Individueel laadmoment actief",
        battery=SimpleNamespace(
            average_soc=97,
            stored_energy=4.97,
            names=["Accu 1"],
            soc_values=[97],
        ),
        target_soc=100,
        charge_distribution=[500],
        charge_efficiency=0.92,
        charge_plan=charge,
        battery_charge_plans=[battery_plan],
        discharge_plan=None,
        now=now,
        price_margin=0.02,
        discharge_margin=0.06,
        minimum_soc=10,
        hours_needed=0.06,
    )
    status = {
        "current_state": "charging",
        "last_execution": "Laden",
        "last_verification": "OK",
    }

    DashboardStatus(app).publish(context, status, 0.10, True, charge)

    attributes = app.states["sensor.openems_planningsoverzicht"][1]
    item = attributes["laadplan"]["per_accu"][0]
    assert item["status"] == "laden"
    assert item["minimum_vermogen_w"] == 500
    assert item["voorkeur_minimum_w"] == 1000
    assert item["voorkeur_maximum_w"] == 1500
    assert item["vermogensstrategie"] == "voorkeursbereik"
    assert item["laadvermogen_w"] == 500
    assert item["laadduur_uren"] == 0.6
    assert item["segmenten"][0]["einde"] == segment.end.isoformat()


def test_dashboard_shows_charging_until_economic_hour_ends():
    app = DummyApp()
    now = datetime(2026, 7, 15, 16, 59, tzinfo=timezone.utc)
    segment = BatteryChargeSegment(
        start=datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 15, 16, 45, tzinfo=timezone.utc),
        power=1200,
        price=0.10,
    )
    battery_plan = BatteryChargePlan(
        index=0,
        name="Accu 2",
        current_soc=80,
        target_soc=100,
        capacity=5.12,
        battery_energy_needed=1.0,
        grid_energy_needed=1.1,
        minimum_power=500,
        planned_power=1200,
        average_price=0.10,
        segments=[segment],
    )
    context = SimpleNamespace(
        now=now,
        target_soc=100,
        battery=SimpleNamespace(
            average_soc=98,
            names=["Accu 2"],
            soc_values=[98],
        ),
        battery_charge_plans=[battery_plan],
        charge_plan=SimpleNamespace(
            eligible_slots=[{
                "from": datetime(
                    2026, 7, 15, 16, 0, tzinfo=timezone.utc
                ),
                "till": datetime(
                    2026, 7, 15, 17, 0, tzinfo=timezone.utc
                ),
            }],
        ),
        charge_distribution=[1200],
    )

    dashboard = DashboardStatus(app)

    assert dashboard._active_charge_powers(context) == [1200]
    item = dashboard._battery_planning_items(
        context, [battery_plan]
    )[0]
    assert item["status"] == "laden"
    assert "Laadbuffer" in item["reden"]


def test_next_action_merges_contiguous_hourly_charge_segments():
    now = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)
    segments = [
        BatteryChargeSegment(
            start=datetime(2026, 7, 27, hour, tzinfo=timezone.utc),
            end=datetime(2026, 7, 27, hour + 1, tzinfo=timezone.utc),
            power=1200,
            price=0.10,
        )
        for hour in (14, 15, 16)
    ]
    plan = BatteryChargePlan(
        index=0,
        name="Accu 1",
        current_soc=50,
        target_soc=100,
        capacity=5.12,
        battery_energy_needed=3.0,
        grid_energy_needed=3.3,
        minimum_power=500,
        planned_power=1200,
        average_price=0.10,
        segments=segments,
    )
    context = SimpleNamespace(now=now)

    action = DashboardStatus(DummyApp())._next_battery_action(
        0, context, [plan], None
    )

    assert action == "17:00 stand-by"


def test_next_action_stands_by_between_self_consumption_and_selected_charge():
    now = datetime(2026, 7, 28, 9, 57, tzinfo=timezone.utc)
    discharge_plan = SimpleNamespace(blocks=[SimpleNamespace(
        start=datetime(2026, 7, 28, 9, tzinfo=timezone.utc),
        end=datetime(2026, 7, 28, 11, tzinfo=timezone.utc),
    )], per_battery=[SimpleNamespace(index=0, expected_power=750)])
    charge_plan = SimpleNamespace(
        index=0,
        planned_power=1200,
        segments=[SimpleNamespace(
            start=datetime(2026, 7, 28, 12, tzinfo=timezone.utc),
            end=datetime(2026, 7, 28, 13, tzinfo=timezone.utc),
            power=1200,
        )],
    )
    context = SimpleNamespace(now=now)

    action = DashboardStatus(DummyApp())._next_battery_action(
        0, context, [charge_plan], discharge_plan
    )

    assert action == "11:00 stand-by"


def test_active_export_has_priority_in_system_status():
    app = DummyApp()
    app.grid_export_manager = SimpleNamespace(
        active=True,
        last_decision={
            "gewenst_exportvermogen_w": 4147,
            "reserve_soc": 46,
        },
    )
    dashboard = DashboardStatus(app)
    context = SimpleNamespace(
        scheduler_reason="Zelfconsumptieblok actief",
        export_plan=SimpleNamespace(per_battery=[
            SimpleNamespace(predicted_end_soc=61, capacity=5.12),
            SimpleNamespace(predicted_end_soc=62, capacity=5.12),
            SimpleNamespace(predicted_end_soc=63, capacity=5.12),
        ]),
    )

    state, reason = dashboard._effective_system_status(
        context, {"current_state": "idle"}
    )

    assert state == "Terugleveren"
    assert reason == (
        "Doel 4,15 kW \u00b7 verwacht resterend 62% "
        "\u00b7 minimumreserve 46%"
    )


def test_active_self_consumption_block_has_one_system_status():
    for direction in ("charge", "discharge", "stop"):
        app = DummyApp()
        app.controller = SimpleNamespace(coordinator=SimpleNamespace(
            active=True,
            phase="controlling",
            desired_direction=direction,
        ))
        context = SimpleNamespace(
            scheduler_reason="Zelfconsumptieblok actief",
        )

        state, reason = DashboardStatus(app)._effective_system_status(
            context, {"current_state": "self_consumption"}
        )

        assert state == "self_consumption"
        assert reason == "Zelfconsumptie actief"


def test_scheduled_charging_keeps_laden_system_status():
    app = DummyApp()
    app.controller = SimpleNamespace(coordinator=SimpleNamespace(
        active=True,
        phase="controlling",
        desired_direction="charge",
    ))
    context = SimpleNamespace(
        scheduler_reason="Minstens één individueel laadmoment is actief",
    )

    state, reason = DashboardStatus(app)._effective_system_status(
        context, {"current_state": "charging"}
    )

    assert state == "charging"
    assert reason == context.scheduler_reason


def test_only_standby_hours_show_rust_system_status():
    for scheduler_state in ("idle", "stopped"):
        context = SimpleNamespace(
            scheduler_reason="Geen laad- of ontlaadblok actief",
        )

        state, reason = DashboardStatus(
            DummyApp()
        )._effective_system_status(
            context, {"current_state": scheduler_state}
        )

        assert state == "idle"
        assert reason == context.scheduler_reason


def test_next_action_shows_return_from_export_to_self_consumption():
    now = datetime(2026, 7, 27, 20, 15, tzinfo=timezone.utc)
    export_block = SimpleNamespace(
        start=datetime(2026, 7, 27, 20, tzinfo=timezone.utc),
        end=datetime(2026, 7, 27, 21, tzinfo=timezone.utc),
    )
    context = SimpleNamespace(
        now=now,
        export_plan=SimpleNamespace(blocks=[export_block]),
    )

    action = DashboardStatus(DummyApp())._next_battery_action(
        0, context, [], None
    )

    assert action == "21:00 zelfconsumptie"


def test_coordinated_verification_replaces_unknown_dashboard_status():
    class Coordinator:
        active = True
        phase = "controlling"

        @staticmethod
        def verification_result():
            return {"batteries": [{"index": 0, "ok": True}]}

    app = DummyApp()
    app.controller = SimpleNamespace(coordinator=Coordinator())
    app.batteries = []
    context = SimpleNamespace(
        now=datetime(2026, 7, 27, 20, tzinfo=timezone.utc),
        battery=SimpleNamespace(names=["Accu 1"]),
        discharge_plan=None,
        export_plan=None,
        scheduler_state=SimpleNamespace(value="self_consumption"),
    )

    statuses = DashboardStatus(app)._enriched_battery_statuses(
        context,
        [{
            "naam": "Accu 1",
            "status": "ONBEKEND",
            "details": "Nog niet gecontroleerd",
        }],
        [],
    )

    assert statuses[0]["status"] == "OK"
    assert "vloeiende vermogensregeling" in statuses[0]["details"]


def test_central_mode_regulator_replaces_unknown_native_self_consumption():
    class ModeCoordinator:
        @staticmethod
        def status():
            return {
                "mode": "self_consumption",
                "owner": "Energiescheduler",
                "powers": [],
                "last_transition": "Start in self_consumption",
            }

    class Controller:
        mode_coordinator = ModeCoordinator()
        coordinator = None

        @staticmethod
        def verify_state(mode, expected_powers=None):
            assert mode == "self_consumption"
            assert expected_powers == []
            return {
                "batteries": [{"index": 0, "ok": True}],
            }

    app = DummyApp()
    app.controller = Controller()
    app.batteries = []
    context = SimpleNamespace(
        now=datetime(2026, 7, 28, 11, tzinfo=timezone.utc),
        battery=SimpleNamespace(names=["Accu 1"]),
        discharge_plan=None,
        export_plan=None,
        scheduler_state=SimpleNamespace(value="self_consumption"),
    )
    dashboard = DashboardStatus(app)

    statuses = dashboard._enriched_battery_statuses(
        context,
        [{
            "naam": "Accu 1",
            "status": "ONBEKEND",
            "details": "Nog niet gecontroleerd",
        }],
        [],
    )

    assert statuses[0]["status"] == "OK"
    assert "Zelfconsumptie" in statuses[0]["details"]
    assert dashboard._central_last_action("Nog geen actie uitgevoerd") == (
        "Energiescheduler: Start in self_consumption"
    )
