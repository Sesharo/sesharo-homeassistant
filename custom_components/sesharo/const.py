"""Constants + the preset signal catalog for the Sesharo Home Assistant integration."""

from __future__ import annotations

DOMAIN = "sesharo"

# Config-entry / options keys
CONF_BASE_URL = "base_url"
CONF_USER_ID = "user_id"
CONF_TOKEN = "token"
CONF_INTERVAL = "interval"
CONF_PRESETS_ENABLED = "presets_enabled"  # master switch for all presets
# Per-preset opt-out: a list of `device_class` keys the user turned off individually while the
# master switch is on (finer-grained than the single boolean, driven by the panel's per-row
# switches). A preset is active iff the master switch is on AND its device_class is not listed here.
CONF_PRESET_DISABLED = "preset_disabled"
# Per-entity preset opt-out: a list of individual `entity_id`s the user excluded from an *otherwise
# enabled* preset (e.g. keep the temperature preset on but stop sending a noisy freezer probe). Finer
# still than CONF_PRESET_DISABLED — a preset-matched entity is sent iff its class isn't disabled AND
# the entity isn't in this list. Custom mappings are unaffected (they're explicit intent).
CONF_PRESET_EXCLUDED = "preset_excluded"
# Per-preset entity cap: a dict of {sesharo signal slug -> max number of entities to send}. A curated
# preset (e.g. temperature) can legitimately match dozens of sensors that all funnel into one slug
# (home_temperature); the cap bounds how many of them actually push, so a big install doesn't flood a
# single signal by default. Absent slug -> DEFAULT_PRESET_ENTITY_CAP; a value <= 0 means "no limit".
# Applied *after* CONF_PRESET_EXCLUDED (user opt-outs never consume a cap slot), keeping the
# lowest-`entity_id` N sensors so the panel and pusher pick the same ones. Custom mappings are never
# capped — they're explicit intent.
CONF_PRESET_CAPS = "preset_caps"
CONF_CUSTOM = "custom"  # list of custom entity mappings (see below)

# A custom mapping entry (in options[CONF_CUSTOM]):
#   {"entity_id": "sensor.washer_power", "signal": "washer_power",
#    "kind": "metric" | "event", "unit": "watts", "display_name": "Washer Power",
#    "target_unit": "watts"}
# `target_unit` is set only when the mapping joins an *existing* Sesharo signal whose unit is fixed:
# the pusher converts the entity's reading into it before sending (see coordinator `_to_canonical`),
# so joining `sensor.bedroom_noise` (dBA) into a signal stored in dBA never records wrong numbers.
CONF_CUSTOM_ENTITY = "entity_id"
CONF_CUSTOM_SIGNAL = "signal"
CONF_CUSTOM_KIND = "kind"
CONF_CUSTOM_UNIT = "unit"
CONF_CUSTOM_NAME = "display_name"
CONF_CUSTOM_TARGET_UNIT = "target_unit"

KIND_METRIC = "metric"
KIND_EVENT = "event"

DEFAULT_BASE_URL = "https://api.sesharo.com"
DEFAULT_INTERVAL = 300  # seconds between pushes
MIN_INTERVAL = 60
# Default cap on how many preset-matched entities feed a single Sesharo signal (see CONF_PRESET_CAPS).
# A sane guardrail so, e.g., 40 power sensors don't all push into home_power out of the box — the user
# can raise it (or set 0 for no limit) per signal from the panel.
DEFAULT_PRESET_ENTITY_CAP = 10

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

# ── Sidebar panel ────────────────────────────────────────────────────────────
# A custom panel (`/sesharo`) served as a JS module from the integration's `www/` dir. Registered
# once (see __init__.py) and fed by the WebSocket commands in `websocket_api.py`.
PANEL_URL_PATH = "sesharo"  # http://<ha>/sesharo
PANEL_TITLE = "Sesharo"
PANEL_ICON = "mdi:pulse"
PANEL_WEBCOMPONENT = "sesharo-panel"
PANEL_JS_FILENAME = "sesharo-panel.js"
PANEL_STATIC_URL = "/sesharo_panel"  # served from custom_components/sesharo/www

# Preset presentation for the panel — one row per curated signal. The panel renders the label + MDI
# icon; the coordinator matches by `device_class`. Person presence has no device_class (matched by
# the `person.` domain), so it carries `device_class=None` and is toggled by its signal slug.
#   (device_class, kind, signal, label, mdi icon name)
PRESET_CATALOG: list[tuple[str | None, str, str, str, str]] = [
    ("temperature", KIND_METRIC, "home_temperature", "Temperature", "mdiThermometer"),
    ("humidity", KIND_METRIC, "home_humidity", "Humidity", "mdiWaterPercent"),
    ("carbon_dioxide", KIND_METRIC, "home_co2", "Carbon dioxide", "mdiMoleculeCo2"),
    ("pm25", KIND_METRIC, "home_pm25", "Fine particles", "mdiAirFilter"),
    ("power", KIND_METRIC, "home_power", "Power", "mdiFlash"),
    ("energy", KIND_METRIC, "home_energy", "Energy", "mdiLightningBolt"),
    ("occupancy", KIND_EVENT, "home_presence", "Presence", "mdiAccount"),
    ("motion", KIND_EVENT, "home_motion", "Motion", "mdiMotionSensor"),
    ("door", KIND_EVENT, "home_door", "Doors", "mdiDoorOpen"),
]
