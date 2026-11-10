# Integration domain name
DOMAIN = "marstek_modbus"

# Manufacturer and model information for the Marstek Venus battery
MANUFACTURER = "Marstek"
MODEL = "Venus"

# Default network configuration for Modbus connection
DEFAULT_PORT = 502
DEFAULT_MESSAGE_WAIT_MS = 80  # Default wait time for Modbus messages in milliseconds
DEFAULT_UNIT_ID = 1  # Default Modbus Unit ID (unit ID)

# General scan intervals (in seconds)
DEFAULT_SCAN_INTERVALS = {
    "high": 10,        # fast-changing sensors and former medium-priority sensors
    "low": 60,         # slower-changing sensors and former very_low-priority sensors
    "very_low": 3600,  # near-static diagnostic sensors (e.g. device_ip, gateway_ip);
                       # some register definitions still use this older tier name,
                       # which is otherwise unmapped and causes the coordinator to
                       # log "has no scan_interval defined, skipping this poll" and
                       # skip the sensor on every single update cycle.
}

# Supported device versions
SUPPORTED_VERSIONS = [
    "E v1/v2", 
    "E v3",
    "D",
    "A"]

# Note: register loading logic (get_registers) was moved to
# `coordinator.py` to keep `const.py` focused on constants only.