# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Centrale configuratie voor OpenEMS-M."""
from .. import ems_paths

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Installatie-instellingen (openems_site.yaml)
#
# Alles wat per installatie verschilt (aantal accu's, hun entiteiten,
# netaansluiting en netsensoren) staat in een eigen bestand naast de map
# OpenEMS-M: apps/openems_site.yaml. Zonder dat bestand gelden de standaard-
# waarden hieronder (de oorspronkelijke installatie met vier accu's).
# Zie openems_site.example.yaml voor alle opties.
# ---------------------------------------------------------------------------
SITE_CONFIG_PATH = Path(
    os.environ.get(
        "OPENEMS_SITE_CONFIG",
        ems_paths.data_parent() / "openems_site.yaml",
    )
)


def _load_site_config(path=None):
    """Lees openems_site.yaml; ontbreekt of is hij ongeldig, dan {}."""
    path = Path(path or SITE_CONFIG_PATH)
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - alleen bij kapotte YAML
        raise ValueError(f"openems_site.yaml is ongeldig: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("openems_site.yaml moet een sleutel/waarde-lijst zijn")
    return data


SITE_CONFIG = _load_site_config()


def _site(section, key, default):
    value = (SITE_CONFIG.get(section) or {}).get(key)
    return default if value in (None, "") else value

EMS_NAME = "OpenEMS-M"
EMS_VERSION = "0.13.1"
EMS_SPRINT = "Eigen Marstek Modbus-driver"

TIMEZONE = "Europe/Amsterdam"
# De planner rekent alleen prijzen en laadblokken uit. Uitvoering en bewaking
# hebben hieronder bewust hun eigen, kortere interval.
PLAN_INTERVAL_SECONDS = 900
EXECUTION_INTERVAL_SECONDS = 60
MONITOR_INTERVAL_SECONDS = 60
CONTROL_MONITOR_INTERVAL_SECONDS = 5
LOG_INTERVAL_SECONDS = 900
# De vijf Modbusfasen duren in de praktijk langer dan alleen hun ingestelde
# tussenpauzes. Controleer pas wanneer de Venus ook werkelijk vermogen heeft
# kunnen opbouwen of afbouwen.
VERIFICATION_DELAY_SECONDS = 20
# Alleen na een eerste afwijkende handmatige meting: drie snelle reads,
# zonder nieuwe RS485-opdrachten te sturen.
MANUAL_STATUS_RECHECK_DELAY_SECONDS = 1
MANUAL_STATUS_RECHECK_ATTEMPTS = 3
VERIFICATION_RETRY_DELAY_SECONDS = 30
MAX_VERIFICATION_RETRIES = 1
CHARGE_EFFICIENCY = 1.0
EFFICIENT_MINIMUM_CHARGE_POWER_W = 500
EFFICIENT_PREFERRED_MIN_CHARGE_POWER_W = 1000
EFFICIENT_PREFERRED_MAX_CHARGE_POWER_W = 1500
MAX_GRID_EXPORT_POWER_W = int(_site("net", "max_export_w", 5000))
# Maximaal netvermogen van de aansluiting: laden + woningverbruik blijft hieronder.
MAX_GRID_IMPORT_POWER_W = int(_site("net", "max_import_w", 8050))
GRID_IMPORT_SAFETY_MARGIN_W = int(_site("net", "marge_w", 100))
# Planning: alle accu's samen laden nooit met meer dan dit vermogen.
MAX_PLANNED_CHARGE_POWER_W = MAX_GRID_IMPORT_POWER_W - 2 * GRID_IMPORT_SAFETY_MARGIN_W
# Load balancer: standaard netsensoren (P1 eerst, CT-spoel als reserve).
P1_SENSOR_DEFAULT = _site("sensoren", "p1", "sensor.p1_netto_vermogen")
CT_GRID_SENSOR_DEFAULT = _site(
    "sensoren", "ct_net", "sensor.meterkast_shellyctgrid_vermogen"
)
CT_HOME_SENSOR_DEFAULT = _site(
    "sensoren", "ct_woning", "sensor.meterkast_shellycthome_vermogen"
)
LOAD_BALANCER_P1_DEFAULT = P1_SENSOR_DEFAULT
LOAD_BALANCER_CT_DEFAULT = CT_GRID_SENSOR_DEFAULT
LOAD_BALANCER_INTERVAL_SECONDS = 5
LOAD_BALANCER_RECOVER_BELOW_W = MAX_GRID_IMPORT_POWER_W - 450
LOAD_BALANCER_RECOVER_AFTER_S = 20
LOAD_BALANCER_RECOVER_STEP_W = 500
LOAD_BALANCER_STALE_AFTER_S = 30
LOAD_BALANCER_FALLBACK_CHARGE_W = 3500
LOAD_BALANCER_HELPERS = {
    # Helper AAN = load balancer UIT voor dit onderdeel (standaard dus actief).
    "laden": "input_boolean.openems_loadbalancer_laden_uit",
    "terugleveren": "input_boolean.openems_loadbalancer_terugleveren_uit",
    "handmatig": "input_boolean.openems_loadbalancer_handmatig_uit",
}
LOAD_BALANCER_STATUS_SENSOR = "sensor.openems_load_balancer"
# Instelbare grenzen en netsensoren van de load balancer.
LB_MAX_IMPORT_HELPER = "input_number.openems_lb_max_import"
LB_MAX_EXPORT_HELPER = "input_number.openems_lb_max_export"
LB_P1_OFF_HELPER = "input_boolean.openems_lb_p1_uit"
LB_CT_OFF_HELPER = "input_boolean.openems_lb_ct_uit"
LB_PREFERRED_HELPER = "input_select.openems_lb_voorkeur"
LB_BOTH_HELPER = "input_boolean.openems_lb_beide_tegelijk"
LB_INITIALIZED_HELPER = "input_boolean.openems_lb_geinitialiseerd"
LB_DEFAULT_MAX_IMPORT_W = MAX_GRID_IMPORT_POWER_W - GRID_IMPORT_SAFETY_MARGIN_W
LB_DEFAULT_MAX_EXPORT_W = MAX_GRID_EXPORT_POWER_W
LB_IMPORT_RANGE_W = (1000.0, 12000.0)
LB_EXPORT_RANGE_W = (500.0, 8000.0)
# Herstel van het laden begint zo ver onder de ingestelde importgrens.
LOAD_BALANCER_RECOVER_MARGIN_W = 350
GRID_EXPORT_SAFETY_MARGIN_W = 0
MAX_PREFERRED_CHARGE_HOURS = 6
CHARGE_COMPLETION_BUFFER_MINUTES = 15
TARGET_SOC_FULL_TOLERANCE_PERCENT = 1.0
TARGET_SOC_RELEASE_HYSTERESIS_PERCENT = 3.0
TOP_CHARGE_TAPER_SOC_PERCENT = 96.0
PRICE_LOG_HOURS = 48

# Hoofdtoestemming; de Home Assistant-helper bepaalt de actuele aan/uit-status.
AUTO_CONTROL_ENABLED = True
EMS_ENABLED_HELPER = "input_boolean.openems_actief"
GRID_CHARGE_ALLOWED_HELPER = "input_boolean.openems_netladen_toegestaan"
GRID_EXPORT_ALLOWED_HELPER = (
    "input_boolean.openems_terugleveren_toegestaan"
)
MANUAL_MODE_HELPER = "input_select.openems_handmatige_modus"
# Handmatig vermogen is een TOTAAL voor de gekozen accu's (0-8000 W).
MANUAL_CHARGE_POWER_HELPER = (
    "input_number.openems_handmatig_laadvermogen_totaal"
)
MANUAL_DISCHARGE_POWER_HELPER = (
    "input_number.openems_handmatig_ontlaadvermogen_totaal"
)
MANUAL_DISTRIBUTION_HELPER = "input_select.openems_handmatig_verdeling"
# Helper AAN = accu doet NIET mee (standaard doen alle accu's mee).
MANUAL_EXCLUDED_HELPERS = [
    f"input_boolean.openems_handmatig_accu{number}_uit"
    for number in range(1, 5)
]

# Alleen tijdelijke handmatige controller-tests via de helper hieronder.
TEST_CONTROL_ENABLED = True
TEST_MODE_HELPER = "input_select.marstek_controller_test"
TEST_CHARGE_POWER_PER_BATTERY = 300
TEST_DISCHARGE_POWER_PER_BATTERY = 300
# Alleen de voorbereidende niet-atomaire opdrachten gebruiken deze wachttijd.
CONTROLLER_COMMAND_DELAY_SECONDS = 1.0
# Schrijf ontlaadvermogen, laadvermogen en Force Mode als één complete
# Modbus-intentie. Dit voorkomt dat polling of de tragere v3-firmware een
# gedeeltelijke opdracht verwerkt tussen de afzonderlijke registers.
ATOMIC_MARSTEK_CONTROL_ENABLED = True
# OpenEMS wacht op de echte read-back van dezelfde MarsTek-batch. Deze timeout
# is uitsluitend de noodrem wanneer Home Assistant geen resultaat-event geeft.
MARSTEK_BATCH_CONFIRMATION_TIMEOUT_SECONDS = 15

SELF_CONSUMPTION_MODE_HELPER = (
    "input_select.openems_zelfconsumptieregeling"
)
SELF_CONSUMPTION_FALLBACK_HELPER = (
    "input_boolean.openems_automatisch_terugvallen_marstek"
)
BATTERY_DISTRIBUTION_HELPER = "input_select.openems_accuverdeling"
GRID_POWER_ENTITY_HELPER = "input_text.openems_grid_vermogenssensor"
GRID_SENSOR_SOURCE_HELPER = "input_select.openems_grid_sensor_bron"
GRID_DISPLAY_ENTITY_HELPER = "input_text.openems_grid_verbruiksensor"
GRID_REFERENCE_ENTITY_HELPER = "input_text.openems_grid_referentiesensor"
HOME_POWER_ENTITY_HELPER = "input_text.openems_woning_vermogenssensor"
SOLAR_POWER_ENTITY_HELPER = "input_text.openems_zon_vermogenssensor"
GRID_EXPORT_MODE_HELPER = "input_select.openems_grid_export_modus"
DYNAMIC_EXPORT_RESERVE_HELPER = (
    "input_boolean.openems_dynamische_terugleverreserve"
)
# De bestaande modus en kWh-helper blijven bewust de instellingen "na laden",
# zodat een upgrade de huidige gebruikerswaarde niet verplaatst of reset.
EXPORT_RESERVE_MODE_HELPER = "input_select.openems_export_reserve_modus"
EXPORT_FIXED_RESERVE_KWH_HELPER = (
    "input_number.openems_export_vaste_reserve_kwh"
)
EXPORT_BEFORE_CHARGE_MODE_HELPER = (
    "input_select.openems_export_reserve_voor_laden_modus"
)
EXPORT_BEFORE_CHARGE_RESERVE_KWH_HELPER = (
    "input_number.openems_export_reserve_voor_laden_kwh"
)
EXPORT_DYNAMIC_RESERVE_KWH_HELPER = (
    "input_number.openems_export_dynamische_reserve_kwh"
)
HOME_POWER_OFFSET_HELPER = "input_number.openems_ct_verbruik_offset"
CHARGE_EFFICIENCY_MODE_HELPER = (
    "input_select.openems_laad_rendement_modus"
)
DISCHARGE_EFFICIENCY_MODE_HELPER = (
    "input_select.openems_ontlaad_rendement_modus"
)
EXPORT_EFFICIENCY_MODE_HELPER = (
    "input_select.openems_export_rendement_modus"
)
MANUAL_CHARGE_EFFICIENCY_HELPER = (
    "input_number.openems_handmatig_laadrendement"
)
MANUAL_DISCHARGE_EFFICIENCY_HELPER = (
    "input_number.openems_handmatig_ontlaadrendement"
)
MANUAL_EXPORT_EFFICIENCY_HELPER = (
    "input_number.openems_handmatig_export_totaalrendement"
)
FRANK_ALL_IN_ENTITY_HELPER = (
    "input_text.openems_frank_all_in_prijssensor"
)
FRANK_MARKET_ENTITY_HELPER = (
    "input_text.openems_frank_marktprijssensor"
)
GRID_EXPORT_INITIALIZED_HELPER = (
    "input_boolean.openems_grid_export_geinitialiseerd"
)
GRID_EXPORT_PRICING_INITIALIZED_HELPER = (
    "input_boolean.openems_grid_export_prijsmodel_geinitialiseerd"
)
GRID_POWER_SIGN_HELPER = "input_select.openems_grid_vermogen_teken"
GRID_OFFSET_MODE_HELPER = "input_select.openems_grid_offset_modus"
GRID_MANUAL_OFFSET_HELPER = "input_number.openems_grid_handmatige_offset"
GRID_AUTOMATIC_OFFSET_HELPER = (
    "input_number.openems_grid_automatische_offset"
)
GRID_OFFSET_CALIBRATE_BUTTON = "input_button.openems_grid_offset_kalibreren"
GRID_OFFSET_MAX_ABS_W = 300
GRID_OFFSET_REFERENCE_LIMIT_W = 500
GRID_OFFSET_STABILITY_SECONDS = 30
GRID_OFFSET_STABILITY_SPAN_W = 80
GRID_OFFSET_MIN_SAMPLES = 6
GRID_OFFSET_MAX_ADJUST_STEP_W = 10
# Eenmalige kalibratie: na zoveel seconden zonder betrouwbaar resultaat stoppen.
GRID_OFFSET_CALIBRATION_TIMEOUT_S = 600
# Bereik van de vaste (handmatige) offsethelper; daarbuiten niet opslaan.
GRID_OFFSET_MANUAL_LIMIT_W = 150
GRID_OFFSET_LAST_CALIBRATION_HELPER = (
    "input_text.openems_offset_laatste_kalibratie"
)
# Eenmalige kalibratie van de woning-CT tegen de P1 (accu's in rust).
HOME_OFFSET_CALIBRATE_BUTTON = "input_button.openems_home_offset_kalibreren"
HOME_OFFSET_LAST_CALIBRATION_HELPER = (
    "input_text.openems_home_offset_laatste_kalibratie"
)
HOME_OFFSET_REFERENCE_LIMIT_W = 3000
HOME_OFFSET_BATTERY_IDLE_W = 30
GRID_OFFSET_ONCE_INITIALIZED_HELPER = (
    "input_boolean.openems_offset_eenmalig_geinitialiseerd"
)

# Centrale regeling via HA-entiteiten. De 5 s-cyclus blijft ruim boven de
# bewezen seriële commandoreeks van circa 2 s voor drie accu's.
COORDINATED_CONTROL_INTERVAL_SECONDS = 5
# Snelle netevents mogen eerder regelen dan de watchdog, maar niet sneller
# dan de MarsTek-telemetrie en de fysieke accu kunnen volgen.
COORDINATED_EVENT_MIN_INTERVAL_SECONDS = 1.5
COORDINATED_GRID_DEADBAND_W = 75
COORDINATED_MIN_POWER_W = 100
COORDINATED_MAX_STEP_W = 750
BATTERY_SELECTOR_MIN_POWER_W = 100
# De fysieke vermogensentiteit kan na een correcte native anti-feedovergang
# nog een volledige HA/Modbus-meetcyclus de oude 0 W tonen. Behandel dat in
# deze periode als wachtende telemetrie en niet als een besturingsfout.
BATTERY_SELECTOR_DELIVERY_CONFIRMATION_SECONDS = 75
# De Venus E 3.0 kan rond zijn interne ontlaadgrens stoppen zonder dat dit
# een besturingsfout is. Gebruik deze grens zowel bij geforceerd ontladen als
# bij de fysieke controle van native anti-feed.
BMS_DISCHARGE_CUTOFF_SOC = 13.0
BATTERY_SELECTOR_ONE_ON_W = 1500
BATTERY_SELECTOR_ONE_OFF_W = 1300
BATTERY_SELECTOR_THREE_ON_W = 3000
BATTERY_SELECTOR_THREE_OFF_W = 2600
# Vierde accu: zelfde verhouding (~1500 W per accu, ~13% hysterese)
BATTERY_SELECTOR_FOUR_ON_W = 4500
BATTERY_SELECTOR_FOUR_OFF_W = 3900
BATTERY_SELECTOR_UP_SECONDS = 120
BATTERY_SELECTOR_DOWN_SECONDS = 600
POWER_CONFLICT_THRESHOLD_W = 200
RUNTIME_MISMATCH_CONFIRMATIONS = 3
RUNTIME_DELIVERY_GRACE_SECONDS = 10
GRID_DATA_MAX_AGE_SECONDS = 20
BATTERY_POWER_DATA_MAX_AGE_SECONDS = 120
SOC_DATA_MAX_AGE_SECONDS = 900
FINANCIAL_SAMPLE_INTERVAL_SECONDS = 60

PRICE_SENSOR = _site(
    "prijs",
    "all_in_sensor",
    "sensor.frank_energie_stroomprijzen_huidige_elektriciteitsprijs_all_in",
)
# Nieuwe naam van de marktprijs: eerste kandidaat die bestaat wordt gebruikt.
MARKET_PRICE_SENSOR_CANDIDATES = (
    "sensor.frank_energie_stroomprijzen_huidige_elektriciteit_inkoopprijs",
    "sensor.frank_energie_stroomprijzen_huidige_elektriciteitsprijs_markt",
    "sensor.frank_energie_stroomprijzen_huidige_elektriciteitsprijs_marktprijs",
    "sensor.frank_energie_stroomprijzen_huidige_marktprijs",
)
# Oude namen van voor de naamswijziging van de Frank Energie-integratie.
LEGACY_PRICE_SENSORS = (
    "sensor.meterkast_frank_energie_prijzen_current_electricity_price_all_in",
)
LEGACY_MARKET_SENSORS = (
    "sensor.meterkast_frank_energie_prijzen_current_electricity_market_price",
)
PRICE_ATTRIBUTE = "prices"

HELPERS = {
    "target_soc": "input_number.marstek_doel_soc",
    "minimum_soc": "input_number.marstek_minimum_soc",
    "charge_margin": "input_number.marstek_prijsmarge",
    "discharge_margin": (
        "input_number.marstek_minimaal_prijsverschil_ontladen"
    ),
    "charge_window_start": "input_number.openems_laden_vanaf",
    "charge_window_end": "input_number.openems_laden_tot",
    "minimum_charge_power": (
        "input_number.openems_minimaal_laadvermogen_per_accu"
    ),
    "manual_charge_efficiency": MANUAL_CHARGE_EFFICIENCY_HELPER,
    "manual_discharge_efficiency": MANUAL_DISCHARGE_EFFICIENCY_HELPER,
    "manual_export_efficiency": MANUAL_EXPORT_EFFICIENCY_HELPER,
    "discharge_resolution": "input_select.openems_ontlaad_tijdresolutie",
    "minimum_discharge_block": (
        "input_number.openems_minimale_ontlaadblokduur"
    ),
    "grid_export_minimum_difference": (
        "input_number.openems_minimaal_prijsverschil_grid_export"
    ),
    "grid_export_selection_margin": (
        "input_number.openems_minimale_exportwinst_per_kwh"
    ),
    "grid_export_return_fee": (
        "input_number.openems_frank_terugleververgoeding"
    ),
    "grid_export_max_power": (
        "input_number.openems_maximaal_exportvermogen"
    ),
    "grid_export_buffer_soc": (
        "input_number.openems_export_veiligheidsbuffer_soc"
    ),
}

DEFAULTS = {
    "target_soc": 95.0,
    "minimum_soc": 20.0,
    "charge_margin": 0.015,
    "discharge_margin": 0.050,
    "charge_window_start": 12.0,
    "charge_window_end": 17.0,
    "minimum_charge_power": 500.0,
    "manual_charge_efficiency": 92.0,
    "manual_discharge_efficiency": 92.0,
    "manual_export_efficiency": 85.0,
    "discharge_resolution": 0.0,
    "minimum_discharge_block": 30.0,
    "grid_export_minimum_difference": 0.05,
    "grid_export_selection_margin": 0.005,
    "grid_export_return_fee": 0.0182,
    "grid_export_max_power": float(MAX_GRID_EXPORT_POWER_W),
    "grid_export_buffer_soc": 40.0,
}

# Entiteit-achtervoegsels van de marstek_modbus-integratie per taal van HA
# (de taal waarin de entiteiten zijn aangemaakt bepaalt de entity_id's).
BATTERY_ENTITY_SUFFIXES = {
    "nl": {
        "soc": "sensor.{p}_batterij_soc",
        "capacity": "sensor.{p}_batterij_totale_energie",
        "actual_power": "sensor.{p}_ac_vermogen",
        "ac_voltage": "sensor.{p}_ac_spanning",
        "inverter_state": "sensor.{p}_omvormer_status",
        "max_cell_voltage": "sensor.{p}_max_celspanning",
        "min_cell_voltage": "sensor.{p}_min_celspanning",
        "temperature": "sensor.{p}_interne_temperatuur",
        "cycles": "sensor.{p}_volledige_cycli_bms",
        "total_charging_energy": "sensor.{p}_totale_oplaadenergie",
        "total_discharging_energy": "sensor.{p}_totale_ontlaadenergie",
        "max_charge_power": "number.{p}_max_laadvermogen",
        "max_discharge_power": "number.{p}_max_ontlaadvermogen",
        "charge_power": "number.{p}_forceer_laadvermogen",
        "discharge_power": "number.{p}_forceer_ontlaadvermogen",
        "rs485_control": "switch.{p}_rs485_bedrijfsmodus",
        "work_mode": "select.{p}_gebruikersmodus",
        "force_mode": "select.{p}_geforceerde_modus",
        "reset_device": "button.{p}_apparaat_resetten",
    },
    "en": {
        "soc": "sensor.{p}_battery_soc",
        "capacity": "sensor.{p}_battery_total_energy",
        "actual_power": "sensor.{p}_ac_power",
        "ac_voltage": "sensor.{p}_ac_voltage",
        "inverter_state": "sensor.{p}_inverter_state",
        "max_cell_voltage": "sensor.{p}_max_cell_voltage",
        "min_cell_voltage": "sensor.{p}_min_cell_voltage",
        "temperature": "sensor.{p}_internal_temperature",
        "cycles": "sensor.{p}_cycle_count",
        "total_charging_energy": "sensor.{p}_total_charging_energy",
        "total_discharging_energy": "sensor.{p}_total_discharging_energy",
        "max_charge_power": "number.{p}_max_charge_power",
        "max_discharge_power": "number.{p}_max_discharge_power",
        "charge_power": "number.{p}_set_charge_power",
        "discharge_power": "number.{p}_set_discharge_power",
        "rs485_control": "switch.{p}_rs485_control_mode",
        "work_mode": "select.{p}_user_work_mode",
        "force_mode": "select.{p}_force_mode",
        "reset_device": "button.{p}_reset_device",
    },
}

MAX_BATTERIES = 4
DEFAULT_BATTERY_SITE = [
    {"naam": f"Accu {index}", "prefix": f"berging_accu{index}"}
    for index in range(1, MAX_BATTERIES + 1)
]


def build_battery(name, prefix, language="nl"):
    """Eén accu-definitie uit de marstek_modbus-apparaatnaam (prefix)."""
    suffixes = BATTERY_ENTITY_SUFFIXES.get(str(language).lower())
    if suffixes is None:
        raise ValueError(f"Onbekende entiteitstaal: {language} (nl of en)")
    battery = {"name": str(name)}
    for field, pattern in suffixes.items():
        battery[field] = pattern.format(p=prefix)
    battery["alarm_status"] = ""
    battery["fault_status"] = ""
    battery["prefix"] = str(prefix)
    return battery


def build_batteries(site=None):
    site = SITE_CONFIG if site is None else site
    language = str(site.get("entiteit_taal") or "nl").lower()
    entries = site.get("accus") or DEFAULT_BATTERY_SITE
    if not isinstance(entries, list) or not entries:
        raise ValueError("openems_site.yaml: 'accus' moet een lijst zijn")
    if len(entries) > MAX_BATTERIES:
        raise ValueError(
            f"openems_site.yaml: maximaal {MAX_BATTERIES} accu's"
        )
    batteries = []
    for index, entry in enumerate(entries, start=1):
        if isinstance(entry, str):
            entry = {"prefix": entry}
        prefix = str(entry.get("prefix") or "").strip()
        if not prefix:
            raise ValueError(f"openems_site.yaml: accu {index} mist 'prefix'")
        batteries.append(
            build_battery(entry.get("naam") or f"Accu {index}", prefix, language)
        )
    return batteries


BATTERIES = build_batteries()
BATTERY_ENTITY_LANGUAGE = str(SITE_CONFIG.get("entiteit_taal") or "nl").lower()

# Per accu een HA-schakelaar "uitgeschakeld" (uit = accu doet gewoon mee).
# De standaardstand is bewust "uit", zodat een nieuwe of herstelde helper
# nooit per ongeluk een accu uitschakelt.
BATTERY_DISABLED_HELPERS = [
    f"input_boolean.openems_accu{index + 1}_uitgeschakeld"
    for index in range(len(BATTERIES))
]

BATTERY_ENTITY_FIELDS = (
    "soc", "capacity", "actual_power", "max_charge_power",
    "max_discharge_power", "charge_power", "discharge_power",
    "rs485_control", "work_mode", "force_mode", "max_cell_voltage",
    "min_cell_voltage", "temperature", "alarm_status", "fault_status",
    "cycles", "total_charging_energy", "total_discharging_energy",
)

BATTERY_ENTITY_HELPERS = {
    (index, field): f"input_text.openems_accu{index + 1}_{field}"
    for index in range(len(BATTERIES))
    for field in BATTERY_ENTITY_FIELDS
}

SMART_CHARGE_HELPER = "input_boolean.openems_slim_laden"
SMART_CHARGE_RESERVE_HELPER = "input_number.openems_slim_laden_reserve_soc"
SMART_CHARGE_BUFFER_HELPER = "input_number.openems_slim_laden_buffer"
SMART_CHARGE_MIN_DIFFERENCE_HELPER = (
    "input_number.openems_slim_laden_min_verschil"
)
SMART_CHARGE_STATUS_SENSOR = "sensor.openems_slim_laden"
# OpenEMS drukt nooit zelf op de apparaat-resetknop van een accu.
DEVICE_RESET_ENABLED = False
# Exportbewaking: herstel pas als de export ruim onder de grens zit.
LOAD_BALANCER_EXPORT_RECOVER_MARGIN_W = 400
AUTO_EXPORT_RESERVE_HELPER = "input_boolean.openems_export_reserve_auto"

# Prijsvoorspelling (WattWanneer): alleen een aanvulling op de Frank-prijzen.
PRICE_FORECAST_HELPER = "input_boolean.openems_prijsvoorspelling"
PRICE_FORECAST_ENTITY_HELPER = "input_text.openems_prijsvoorspelling_sensor"
PRICE_FORECAST_MIN_DIFF_HELPER = (
    "input_number.openems_prijsvoorspelling_min_verschil"
)
PRICE_FORECAST_FLOOR_HELPER = (
    "input_number.openems_prijsvoorspelling_ondergrens_soc"
)
PRICE_FORECAST_DAYS_HELPER = "input_number.openems_prijsvoorspelling_dagen"
PRICE_FORECAST_MARGIN_HELPER = (
    "input_number.openems_prijsvoorspelling_extra_marge"
)
PRICE_FORECAST_INITIALIZED_HELPER = (
    "input_boolean.openems_prijsvoorspelling_geinitialiseerd"
)
PRICE_FORECAST_STATUS_SENSOR = "sensor.openems_prijsvoorspelling"
PRICE_FORECAST_PLAN_SENSOR = "sensor.openems_voorspeld_plan"
PRICE_FORECAST_DEFAULT_ENTITY = "sensor.wattwanneer_gemiddelde_deze_week"
PRICE_FORECAST_ATTRIBUTE = "forecast"
PRICE_FORECAST_REFRESH_S = 600
PRICE_FORECAST_MAX_AGE_H = 24
PRICE_FORECAST_VAT = 0.21
PRICE_FORECAST_DEFAULT_FIXED_PER_KWH = 0.129
PRICE_FORECAST_DEFAULT_MIN_DIFF_CT = 5.0
PRICE_FORECAST_DEFAULT_FLOOR_SOC = 30.0
PRICE_FORECAST_DEFAULT_DAYS = 2
PRICE_FORECAST_DEFAULT_MARGIN_CT = 3.0
# Echte day-ahead uren uit de sensor horen vrijwel exact met de bekende
# Frank-marktprijzen overeen te komen (EUR/kWh); anders wordt de sensor
# als onbetrouwbaar genegeerd (verkeerde eenheid of tijdzone).
PRICE_FORECAST_MAX_MEAN_ERROR = 0.03

