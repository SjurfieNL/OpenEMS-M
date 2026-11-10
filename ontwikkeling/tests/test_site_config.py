"""Installatie-instellingen (openems_site.yaml) voor andere installaties."""

import importlib
import sys

import pytest

import ems_config


def test_without_site_file_the_original_four_batteries_stay():
    batteries = ems_config.build_batteries({})
    assert [b["name"] for b in batteries] == ["Accu 1", "Accu 2", "Accu 3", "Accu 4"]
    assert batteries[0]["soc"] == "sensor.berging_accu1_batterij_soc"
    assert batteries[3]["force_mode"] == "select.berging_accu4_geforceerde_modus"


def test_single_english_battery_from_site_config():
    site = {
        "entiteit_taal": "en",
        "accus": [{"naam": "Thuisaccu", "prefix": "marstek_venus"}],
    }
    batteries = ems_config.build_batteries(site)
    assert len(batteries) == 1
    battery = batteries[0]
    assert battery["name"] == "Thuisaccu"
    assert battery["soc"] == "sensor.marstek_venus_battery_soc"
    assert battery["charge_power"] == "number.marstek_venus_set_charge_power"
    assert battery["rs485_control"] == "switch.marstek_venus_rs485_control_mode"
    assert battery["cycles"] == "sensor.marstek_venus_cycle_count"
    # Elk veld dat de rest van OpenEMS gebruikt is aanwezig.
    for field in ems_config.BATTERY_ENTITY_FIELDS:
        assert field in battery


def test_short_list_notation_and_validation():
    assert ems_config.build_batteries({"accus": ["accu_a", "accu_b"]})[1][
        "soc"
    ] == "sensor.accu_b_batterij_soc"
    with pytest.raises(ValueError):
        ems_config.build_batteries({"accus": [{"naam": "zonder prefix"}]})
    with pytest.raises(ValueError):
        ems_config.build_batteries({"accus": ["a", "b", "c", "d", "e"]})
    with pytest.raises(ValueError):
        ems_config.build_batteries({"entiteit_taal": "de", "accus": ["a"]})


def test_site_file_sets_grid_limits_and_sensors(tmp_path, monkeypatch):
    site = tmp_path / "openems_site.yaml"
    site.write_text(
        "entiteit_taal: en\n"
        "accus:\n"
        "  - naam: Accu\n"
        "    prefix: marstek_venus\n"
        "net:\n"
        "  max_import_w: 5750\n"
        "  max_export_w: 3680\n"
        "sensoren:\n"
        "  p1: sensor.p1_meter_power\n"
        "  ct_net: ''\n"
        "prijs:\n"
        "  all_in_sensor: sensor.my_all_in_price\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENEMS_SITE_CONFIG", str(site))
    original = sys.modules["ems_config"]
    try:
        module = importlib.reload(original)
        assert len(module.BATTERIES) == 1
        assert module.MAX_GRID_IMPORT_POWER_W == 5750
        assert module.MAX_GRID_EXPORT_POWER_W == 3680
        assert module.MAX_PLANNED_CHARGE_POWER_W == 5550
        assert module.LB_DEFAULT_MAX_IMPORT_W == 5650
        assert module.DEFAULTS["grid_export_max_power"] == 3680.0
        assert module.LOAD_BALANCER_P1_DEFAULT == "sensor.p1_meter_power"
        # Leeg = standaardwaarde houden.
        assert module.LOAD_BALANCER_CT_DEFAULT == (
            "sensor.meterkast_shellyctgrid_vermogen"
        )
        assert module.PRICE_SENSOR == "sensor.my_all_in_price"
        assert len(module.BATTERY_DISABLED_HELPERS) == 1
    finally:
        monkeypatch.delenv("OPENEMS_SITE_CONFIG")
        importlib.reload(original)
    assert len(sys.modules["ems_config"].BATTERIES) == 4
