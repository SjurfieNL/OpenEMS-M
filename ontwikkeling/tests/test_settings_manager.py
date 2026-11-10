import pytest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from settings_manager import SettingsManager
from runtime_settings import RuntimeSettings
from ems_config import BATTERIES


class DummyApp:
    def __init__(self):
        self.states = {
            "input_number.openems_laden_vanaf": "14",
            "input_number.openems_laden_tot": "18",
        }
        self.services = []
        self.published = {}

    def get_state(self, entity, attribute=None):
        return self.states.get(entity)

    def listen_state(self, *args, **kwargs):
        pass

    def run_in(self, *args, **kwargs):
        pass

    def call_service(self, service, **kwargs):
        self.services.append((service, kwargs))

    def set_state(self, entity, state, attributes, replace=True):
        self.published[entity] = (state, attributes)

    def log(self, *args, **kwargs):
        pass


class RunInRecordingApp(DummyApp):
    def __init__(self):
        super().__init__()
        self.scheduled = []

    def run_in(self, callback, delay, **kwargs):
        self.scheduled.append((callback, delay, kwargs))


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_initialize_reschedules_when_ha_helpers_not_yet_loaded_at_startup():
    app = RunInRecordingApp()
    manager = SettingsManager(app)

    manager._initialize()

    assert app.services == []
    assert len(app.scheduled) == 1
    callback, delay, kwargs = app.scheduled[0]
    assert callback == manager._initialize
    assert delay == manager.STARTUP_READINESS_RETRY_SECONDS
    assert kwargs == {"attempt": 1}


@pytest.mark.skip(reason="Verouderde test: de geteste functie bestaat niet in deze versie van OpenEMS-M")
def test_initialize_gives_up_and_proceeds_after_max_readiness_attempts():
    app = RunInRecordingApp()
    manager = SettingsManager(app)

    manager._initialize(
        {"attempt": manager.STARTUP_READINESS_MAX_ATTEMPTS}
    )

    assert app.scheduled == []
    assert any(
        kwargs.get("entity_id")
        == "input_boolean.openems_snelle_schakelaars_geinitialiseerd"
        for _service, kwargs in app.services
    )


def test_initialize_proceeds_immediately_once_ha_helpers_are_available():
    app = RunInRecordingApp()
    app.states["input_boolean.openems_instellingen_geinitialiseerd"] = "off"
    manager = SettingsManager(app)

    manager._initialize()

    assert app.scheduled == []
    assert any(
        kwargs.get("entity_id")
        == "input_boolean.openems_snelle_schakelaars_geinitialiseerd"
        for _service, kwargs in app.services
    )


def test_personal_defaults_are_saved_outside_version_and_restored():
    app = DummyApp()
    manager = SettingsManager(app)
    with TemporaryDirectory() as directory:
        manager.data_file = Path(directory) / "persoonlijke_standaard.json"
        manager._save_pressed("button", None, "oud", "nieuw", {})

        stored = json.loads(manager.data_file.read_text(encoding="utf-8"))
        assert stored["input_number.openems_laden_vanaf"] == "14"
        assert stored["input_number.openems_laden_tot"] == "18"

        manager._restore_pressed("button", None, "oud", "nieuw", {})
        assert (
            "input_number/set_value",
            {
                "entity_id": "input_number.openems_laden_vanaf",
                "value": 14.0,
            },
        ) in app.services


def test_helper_package_has_no_forced_initial_values():
    package = (
        Path(__file__).parents[2]
        / "config"
        / "packages"
        / "openems_helper.yaml"
    )
    assert "initial:" not in package.read_text(encoding="utf-8")


def test_v013_migrates_only_known_old_marstek_entity_defaults():
    app = DummyApp()
    app.states.update({
        "input_text.openems_accu1_soc": "sensor.accu1_battery_soc",
        "input_text.openems_accu1_charge_power": (
            "number.accu1_set_charge_power"
        ),
        "input_text.openems_accu1_alarm_status": (
            "sensor.accu1_alarm_status"
        ),
        "input_text.openems_accu2_soc": "sensor.eigen_accu2_soc",
    })
    manager = SettingsManager(app)

    manager._migrate_marstek_modbus_entities()

    assert (
        "input_text/set_value",
        {
            "entity_id": "input_text.openems_accu1_soc",
            "value": "sensor.berging_accu1_batterij_soc",
        },
    ) in app.services
    assert (
        "input_text/set_value",
        {
            "entity_id": "input_text.openems_accu1_charge_power",
            "value": "number.berging_accu1_forceer_laadvermogen",
        },
    ) in app.services
    assert (
        "input_text/set_value",
        {
            "entity_id": "input_text.openems_accu1_alarm_status",
            "value": "",
        },
    ) in app.services
    assert not any(
        kwargs.get("entity_id") == "input_text.openems_accu2_soc"
        for _service, kwargs in app.services
    )


def test_v013_uses_actual_dutch_marstek_modbus_entity_ids():
    for number, battery in enumerate(BATTERIES, start=1):
        prefix = f"berging_accu{number}"
        assert battery["soc"] == f"sensor.{prefix}_batterij_soc"
        assert battery["actual_power"] == f"sensor.{prefix}_ac_vermogen"
        assert battery["charge_power"] == (
            f"number.{prefix}_forceer_laadvermogen"
        )
        assert battery["discharge_power"] == (
            f"number.{prefix}_forceer_ontlaadvermogen"
        )
        assert battery["rs485_control"] == (
            f"switch.{prefix}_rs485_bedrijfsmodus"
        )
        assert battery["work_mode"] == (
            f"select.{prefix}_gebruikersmodus"
        )
        assert battery["force_mode"] == (
            f"select.{prefix}_geforceerde_modus"
        )
        assert battery["alarm_status"] == ""
        assert battery["fault_status"] == ""
        assert "backup" not in battery
        assert all("laden_tot_soc" not in value for value in battery.values())


def test_new_offset_helpers_are_initialized_without_changing_old_settings():
    app = DummyApp()
    app.states[
        "input_boolean.openems_instellingen_geinitialiseerd"
    ] = "on"
    manager = SettingsManager(app)

    manager._initialize()

    assert (
        "input_text/set_value",
        {
            "entity_id": "input_text.openems_grid_referentiesensor",
            "value": "sensor.p1_netto_vermogen",
        },
    ) in app.services
    assert (
        "input_select/select_option",
        {
            "entity_id": "input_select.openems_grid_offset_modus",
            "option": "Handmatig",
        },
    ) in app.services
    assert (
        "input_text/set_value",
        {
            "entity_id": "input_text.openems_grid_verbruiksensor",
            "value": "sensor.p1_netto_vermogen",
        },
    ) in app.services
    assert not any(
        item[1].get("entity_id") == "input_number.openems_laden_vanaf"
        for item in app.services
    )


def test_helper_package_contains_manual_and_automatic_offset_controls():
    package = (
        Path(__file__).parents[2]
        / "config"
        / "packages"
        / "openems_helper.yaml"
    ).read_text(encoding="utf-8")

    assert "openems_grid_referentiesensor:" in package
    assert "openems_grid_verbruiksensor:" in package
    assert "openems_grid_offset_modus:" in package
    assert "openems_grid_handmatige_offset:" in package
    assert "openems_grid_automatische_offset:" in package
    assert "openems_grid_offset_kalibreren:" in package


def test_helper_package_contains_economic_discharge_controls():
    package = (
        Path(__file__).parents[2]
        / "config"
        / "packages"
        / "openems_helper.yaml"
    ).read_text(encoding="utf-8")

    assert "openems_alleen_ontladen_bij_netto_winst:" not in package
    assert "openems_ontlaad_tijdresolutie:" in package
    assert "name: OpenEMS tijdresolutie" in package
    assert "- 15 minuten" in package
    assert "- 60 minuten" in package
    assert "OpenEMS tijdresolutie ontladen" not in package
    assert "openems_ontlaad_rendement_modus:" in package
    assert "openems_handmatig_ontlaadrendement:" in package
    assert "openems_slijtagekosten_per_kwh:" not in package
    assert "openems_minimale_netto_winst_per_kwh:" not in package
    assert "openems_accu1_active_balance_mode:" not in package
    assert "openems_accu2_active_balance_mode:" not in package
    assert "openems_accu3_active_balance_mode:" not in package


def test_grid_export_defaults_to_simulation_and_never_restores_automatic():
    app = DummyApp()
    manager = SettingsManager(app)
    manager._apply({
        "input_select.openems_grid_export_modus":
            "Automatisch exporteren",
    })
    assert (
        "input_select/select_option",
        {
            "entity_id": "input_select.openems_grid_export_modus",
            "option": "Alleen simuleren",
        },
    ) in app.services

    factory = manager.factory_settings
    assert (
        factory["input_select.openems_grid_export_modus"]
        == "Alleen simuleren"
    )


def test_export_planner_migration_applies_safe_limits_once():
    app = DummyApp()
    manager = SettingsManager(app)
    manager._initialize_export_planner()
    expected = {
        "input_select.openems_grid_export_modus": "Alleen simuleren",
        "input_number.marstek_minimaal_prijsverschil_ontladen": 0.05,
        "input_number.openems_minimale_exportwinst_per_kwh": 0.005,
        "input_number.openems_export_veiligheidsbuffer_soc": 40.0,
        "input_number.openems_maximaal_exportvermogen": 5000.0,
    }
    for entity, value in expected.items():
        assert any(
            call[1].get("entity_id") == entity
            and (
                call[1].get("option") == value
                or call[1].get("value") == value
            )
            for call in app.services
        )
    assert any(
        call[1].get("entity_id")
        == "input_boolean.openems_export_planner_geinitialiseerd"
        for call in app.services
    )



def test_legacy_automatic_resolution_is_migrated_to_hourly():
    app = DummyApp()
    app.states[
        "input_select.openems_ontlaad_tijdresolutie"
    ] = "Automatisch"
    manager = SettingsManager(app)

    manager._migrate_time_resolution()

    assert (
        "input_select/select_option",
        {
            "entity_id": "input_select.openems_ontlaad_tijdresolutie",
            "option": "60 minuten",
        },
    ) in app.services


def test_helper_package_contains_grid_export_controls():
    package = (
        Path(__file__).parents[2]
        / "config"
        / "packages"
        / "openems_helper.yaml"
    ).read_text(encoding="utf-8")

    assert "openems_grid_export_modus:" in package
    assert "Alleen simuleren" in package
    assert "openems_minimaal_prijsverschil_grid_export:" in package
    assert "openems_frank_terugleververgoeding:" in package
    assert "openems_minimale_exportwinst_per_kwh:" in package
    assert "openems_maximaal_exportvermogen:" in package
    assert "openems_export_veiligheidsbuffer_soc:" in package
    assert "openems_dynamische_terugleverreserve:" in package
    assert "openems_handmatige_exportprijs:" not in package
    assert "openems_frank_all_in_prijssensor:" in package
    assert "openems_frank_marktprijssensor:" in package
    assert "openems_laad_rendement_modus:" in package
    assert "openems_ontlaad_rendement_modus:" in package
    assert "openems_export_rendement_modus:" in package
    assert "openems_handmatig_export_totaalrendement:" in package
    assert "openems_round_trip_rendement_automatisch:" not in package


def test_factory_uses_separate_export_pricing_and_no_efficiency_default():
    manager = SettingsManager(DummyApp())
    factory = manager.factory_settings
    assert (
        factory["input_number.openems_minimaal_prijsverschil_grid_export"]
        == 0.05
    )
    assert (
        factory["input_number.openems_frank_terugleververgoeding"]
        == 0.0182
    )
    assert factory["input_select.openems_laad_rendement_modus"] == (
        "Geen rendement"
    )
    assert factory["input_select.openems_ontlaad_rendement_modus"] == (
        "Geen rendement"
    )
    assert factory["input_select.openems_export_rendement_modus"] == (
        "Geen rendement"
    )
    assert factory[
        "input_number.openems_handmatig_export_totaalrendement"
    ] == 85.0
    assert "input_number.openems_handmatige_exportprijs" not in factory
    assert (
        factory["input_boolean.openems_dynamische_terugleverreserve"]
        == "off"
    )
    assert factory["input_select.openems_export_reserve_modus"] == "Vast"
    assert factory["input_number.openems_export_vaste_reserve_kwh"] == 8.5
    assert factory[
        "input_select.openems_export_reserve_voor_laden_modus"
    ] == "Vast"
    assert factory[
        "input_number.openems_export_reserve_voor_laden_kwh"
    ] == 8.0
    assert factory[
        "input_number.openems_export_dynamische_reserve_kwh"
    ] == 2.0


def test_split_export_reserve_migration_preserves_existing_after_reserve():
    app = DummyApp()
    app.states["input_number.openems_export_vaste_reserve_kwh"] = "8.5"
    manager = SettingsManager(app)

    manager._initialize_split_export_reserve()

    changed = {call[1].get("entity_id") for call in app.services}
    assert "input_number.openems_export_vaste_reserve_kwh" not in changed
    assert (
        "input_number/set_value",
        {
            "entity_id": "input_number.openems_export_reserve_voor_laden_kwh",
            "value": 8.0,
        },
    ) in app.services
    assert (
        "input_number/set_value",
        {
            "entity_id": "input_number.openems_export_dynamische_reserve_kwh",
            "value": 2.0,
        },
    ) in app.services


def test_dynamic_export_reserve_runtime_setting_reads_boolean_helper():
    app = DummyApp()
    settings = RuntimeSettings(app)
    assert settings.dynamic_export_reserve_enabled() is False

    app.states[
        "input_boolean.openems_dynamische_terugleverreserve"
    ] = "on"
    assert settings.dynamic_export_reserve_enabled() is True


def test_before_after_and_dynamic_export_reserves_are_independent():
    app = DummyApp()
    app.states.update({
        "input_select.openems_export_reserve_voor_laden_modus": "Dynamisch",
        "input_select.openems_export_reserve_modus": "Vast",
        "input_number.openems_export_reserve_voor_laden_kwh": "8.0",
        "input_number.openems_export_vaste_reserve_kwh": "8.5",
        "input_number.openems_export_dynamische_reserve_kwh": "2.0",
    })
    settings = RuntimeSettings(app)

    assert settings.export_reserve_before_charge_mode() == "dynamic"
    assert settings.export_reserve_after_charge_mode() == "fixed"
    assert settings.fixed_export_reserve_before_charge_kwh() == 8.0
    assert settings.fixed_export_reserve_after_charge_kwh() == 8.5
    assert settings.dynamic_export_reserve_kwh() == 2.0


def test_manual_efficiency_modes_are_separate_per_direction():
    app = DummyApp()
    app.states.update({
        "input_select.openems_laad_rendement_modus":
            "Handmatig rendement",
        "input_select.openems_ontlaad_rendement_modus":
            "Handmatig rendement",
        "input_number.openems_handmatig_laadrendement": "92",
        "input_number.openems_handmatig_ontlaadrendement": "90",
    })
    charge, discharge, details = RuntimeSettings(
        app
    ).effective_efficiencies()

    assert round(charge[0], 4) == 0.92
    assert round(discharge[1], 4) == 0.90
    assert round(charge[2], 4) == 0.92
    assert all(item["charge_source"] == "handmatig" for item in details)
    assert all(item["discharge_source"] == "handmatig" for item in details)


def test_unreliable_self_learned_measurement_uses_no_hidden_fallback():
    app = DummyApp()
    app.states.update({
        "input_select.openems_laad_rendement_modus":
            "Zelfgeleerd rendement",
        "input_select.openems_ontlaad_rendement_modus":
            "Zelfgeleerd rendement",
        "sensor.openems_accu_1_zelfgeleerd_rendement": {
            "attributes": {
                "laden": {
                    "rendement_pct": 91,
                    "betrouwbaarheid": "nog lerend",
                },
                "ontladen": {
                    "rendement_pct": 89,
                    "betrouwbaarheid": "nog lerend",
                },
            },
        },
    })
    charge, discharge, details = RuntimeSettings(
        app
    ).effective_efficiencies()

    assert charge[0] == 1.0
    assert discharge[0] == 1.0
    assert "onvoldoende" in details[0]["charge_source"]
    assert "onvoldoende" in details[0]["discharge_source"]


def test_reliable_self_learned_efficiency_and_manual_export_are_used():
    app = DummyApp()
    app.states.update({
        "input_select.openems_laad_rendement_modus":
            "Zelfgeleerd rendement",
        "input_select.openems_ontlaad_rendement_modus":
            "Zelfgeleerd rendement",
        "input_select.openems_export_rendement_modus":
            "Handmatig rendement",
        "input_number.openems_handmatig_export_totaalrendement": "85",
    })
    for index in range(1, 4):
        app.states[
            f"sensor.openems_accu_{index}_zelfgeleerd_rendement"
        ] = {
            "attributes": {
                "laden": {
                    "rendement_pct": 93,
                    "betrouwbaarheid": "hoog",
                },
                "ontladen": {
                    "rendement_pct": 91,
                    "betrouwbaarheid": "hoog",
                },
            },
        }
    settings = RuntimeSettings(app)
    charge, discharge, details = settings.effective_efficiencies()
    export, source = settings.export_efficiency()
    # Accu 4 heeft nog geen betrouwbaar geleerd rendement: tijdelijk 100 %.
    assert charge == [0.93, 0.93, 0.93, 1.0]
    assert discharge == [0.91, 0.91, 0.91, 1.0]
    assert all(
        item["charge_source"] == "zelfgeleerd" for item in details[:3]
    )
    assert "onvoldoende" in details[3]["charge_source"]
    assert export == 0.85
    assert source == "handmatig"


def test_grid_export_uses_bare_market_price_plus_return_fee():
    app = DummyApp()
    settings = RuntimeSettings(app)
    assert settings.grid_export_price() is None

    app.states["input_text.openems_frank_marktprijssensor"] = (
        "sensor.frank_market"
    )
    app.states["sensor.frank_market"] = "0.1234"
    app.states["input_number.openems_frank_terugleververgoeding"] = "0.0182"
    assert settings.grid_export_price() == 0.1416


def test_new_quick_switches_and_ct_offsets_are_runtime_settings():
    app = DummyApp()
    app.states.update({
        "input_boolean.openems_netladen_toegestaan": "off",
        "input_boolean.openems_terugleveren_toegestaan": "on",
        "input_select.openems_grid_sensor_bron": "P1",
        "input_text.openems_grid_referentiesensor": "sensor.p1",
        "input_text.openems_woning_vermogenssensor": "sensor.home",
        "sensor.home": "420",
        "input_number.openems_ct_verbruik_offset": "-20",
        "input_number.openems_export_vaste_reserve_kwh": "3.84",
    })
    settings = RuntimeSettings(app)
    assert settings.grid_charging_allowed() is False
    assert settings.grid_export_allowed() is True
    assert settings.grid_entity() == "sensor.p1"
    assert settings.home_power() == 400
    assert settings.fixed_export_reserve_kwh() == 3.84


def test_load_balancer_limits_are_initialized_once():
    app = DummyApp()
    manager = SettingsManager(app)

    manager._initialize_load_balancer()

    values = {
        data["entity_id"]: data.get("value", data.get("option"))
        for _, data in app.services
    }
    assert values["input_number.openems_lb_max_import"] == 7950.0
    assert values["input_number.openems_lb_max_export"] == 5000.0
    assert values["input_select.openems_lb_voorkeur"] == "P1"
    assert "input_boolean.openems_lb_geinitialiseerd" in values

    app.services.clear()
    app.states["input_boolean.openems_lb_geinitialiseerd"] = "on"
    manager._initialize_load_balancer()
    assert app.services == []


def test_automatic_offset_becomes_fixed_manual_offset_once():
    app = DummyApp()
    app.states["input_select.openems_grid_offset_modus"] = "Automatisch"
    app.states["input_number.openems_grid_automatische_offset"] = "37.0"
    manager = SettingsManager(app)

    manager._initialize_offset_once()

    assert (
        "input_number/set_value",
        {"entity_id": "input_number.openems_grid_handmatige_offset",
         "value": 37.0},
    ) in app.services
    assert (
        "input_select/select_option",
        {"entity_id": "input_select.openems_grid_offset_modus",
         "option": "Handmatig"},
    ) in app.services
    app.services.clear()
    app.states["input_boolean.openems_offset_eenmalig_geinitialiseerd"] = "on"
    manager._initialize_offset_once()
    assert app.services == []


def test_price_forecast_starts_off_with_default_values_once():
    app = DummyApp()
    manager = SettingsManager(app)

    manager._initialize_price_forecast()

    values = {
        data["entity_id"]: data.get("value", None)
        for _, data in app.services
    }
    assert values["input_number.openems_prijsvoorspelling_min_verschil"] == 5.0
    assert values["input_number.openems_prijsvoorspelling_ondergrens_soc"] == 30.0
    assert values["input_number.openems_prijsvoorspelling_dagen"] == 2.0
    assert values["input_text.openems_prijsvoorspelling_sensor"] == (
        "sensor.wattwanneer_gemiddelde_deze_week"
    )
    assert ("input_boolean/turn_off", {
        "entity_id": "input_boolean.openems_prijsvoorspelling"
    }) in app.services

    app.services.clear()
    app.states["input_boolean.openems_prijsvoorspelling_geinitialiseerd"] = "on"
    manager._initialize_price_forecast()
    assert app.services == []


def test_price_forecast_margin_default_is_three_ct():
    app = DummyApp()
    SettingsManager(app)._initialize_price_forecast()
    values = {d["entity_id"]: d.get("value") for _, d in app.services}
    assert values["input_number.openems_prijsvoorspelling_extra_marge"] == 3.0
