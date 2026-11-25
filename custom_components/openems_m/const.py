"""Constanten voor de OpenEMS-M integratie."""

DOMAIN = "openems_m"

# Prijsbronnen
PRICE_FRANK = "frank_energie"
PRICE_NORDPOOL = "nordpool_entsoe"
PRICE_CUSTOM = "custom_sensor"
PRICE_SOURCES = [PRICE_FRANK, PRICE_NORDPOOL, PRICE_CUSTOM]

# Accumodellen (gelijk aan de marstek_modbus-registerbestanden)
MODELS = ["e_v12", "e_v3", "d", "a"]
MAX_BATTERIES = 4

# Config-sleutels
CONF_PRICE_SOURCE = "price_source"
CONF_PRICE_ENTITY = "price_entity"
CONF_SURCHARGE = "surcharge_eur_kwh"
CONF_ENERGY_TAX = "energy_tax_eur_kwh"
CONF_FEED_IN = "feed_in_eur_kwh"

CONF_BATTERIES = "batteries"
CONF_HOST = "host"
CONF_PORT = "port"
CONF_UNIT_ID = "unit_id"
CONF_MODEL = "model"
CONF_NUMBER = "number"
CONF_ADD_ANOTHER = "add_another"

CONF_PHASES = "phases"
CONF_MAX_IMPORT = "max_import_w"
CONF_MAX_EXPORT = "max_export_w"
CONF_MARGIN = "safety_margin_w"

CONF_P1_ENTITY = "p1_entity"
CONF_CT_ENTITY = "ct_entity"
CONF_PREFERRED = "preferred_sensor"

CONF_AUTO = "auto_control"

DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 1
DEFAULT_MARGIN_W = 100

# Fase 3: accu's als subentries en de ingebouwde driver
SUBENTRY_BATTERY = "battery"
DRIVER_DATA = "openems_m_driver"  # hass.data-sleutel met coordinator per accu
CONF_NAME = "name"
CONF_DEVICE_VERSION = "device_version"
CONF_BATTERY_NUMBER = "openems_battery_number"
MODEL_TO_VERSION = {"e_v12": "E v1/v2", "e_v3": "E v3", "d": "D", "a": "A"}
CONNECT_TIMEOUT_S = 8.0
PLATFORMS = ["sensor", "switch", "select", "button", "number", "binary_sensor", "text"]
