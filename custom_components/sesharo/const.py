"""Constants + the preset signal catalog for the Sesharo Home Assistant integration."""
from __future__ import annotations

DOMAIN = "sesharo"

# Config-entry / options keys
CONF_BASE_URL = "base_url"
CONF_USER_ID = "user_id"
CONF_TOKEN = "token"
CONF_INTERVAL = "interval"
CONF_PRESETS_ENABLED = "presets_enabled"
CONF_CUSTOM = "custom"  # list of custom entity mappings (see below)

# A custom mapping entry (in options[CONF_CUSTOM]):
#   {"entity_id": "sensor.washer_power", "signal": "washer_power",
#    "kind": "metric" | "event", "unit": "watts", "display_name": "Washer Power"}
CONF_CUSTOM_ENTITY = "entity_id"
CONF_CUSTOM_SIGNAL = "signal"
CONF_CUSTOM_KIND = "kind"
CONF_CUSTOM_UNIT = "unit"
CONF_CUSTOM_NAME = "display_name"

KIND_METRIC = "metric"
KIND_EVENT = "event"

DEFAULT_BASE_URL = "https://api.sesharo.com"
DEFAULT_INTERVAL = 300  # seconds between pushes
MIN_INTERVAL = 60

# Curated presets — auto-discovered by the sensor's device_class. Each maps to a seeded Sesharo
# metric type (numeric) sending values in the canonical unit shown (the pusher converts).
#   device_class -> (sesharo signal slug, canonical unit)
PRESET_METRICS: dict[str, tuple[str, str]] = {
    "temperature": ("home_temperature", "celsius"),
    "humidity": ("home_humidity", "percent"),
    "carbon_dioxide": ("home_co2", "ppm"),
    "pm25": ("home_pm25", "ugm3"),
    "power": ("home_power", "watts"),
    "energy": ("home_energy", "kwh"),
}

# Curated event presets — binary_sensor/person state changes become Sesharo timeline events.
#   device_class -> sesharo event category slug
PRESET_EVENTS: dict[str, str] = {
    "occupancy": "home_presence",
    "presence": "home_presence",
    "motion": "home_motion",
    "door": "home_door",
    "window": "home_window",
}
# `person.*` entities are treated as presence regardless of device_class.
PERSON_EVENT_CATEGORY = "home_presence"
