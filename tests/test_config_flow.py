"""Tests for the config + options flows (config_flow.py).

Drives the real HA flow machinery: the setup form (happy path + the two error keys) and the
options menu (settings round-trip, the two-step custom-mapping add, and removal).
"""

from __future__ import annotations

import aiohttp
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sesharo.const import (
    CONF_BASE_URL,
    CONF_CUSTOM,
    CONF_CUSTOM_ENTITY,
    CONF_CUSTOM_KIND,
    CONF_CUSTOM_SIGNAL,
    CONF_INTERVAL,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    KIND_METRIC,
)

BASE = "https://api.example.test"
USER = "user-1"
TOKEN = "pat-abc"
PUSH_URL = f"{BASE}/users/{USER}/home-assistant"

USER_INPUT = {CONF_BASE_URL: BASE, CONF_USER_ID: USER, CONF_TOKEN: TOKEN}


# ── setup flow ───────────────────────────────────────────────────────────────
async def test_user_flow_success_creates_entry(hass, aioclient_mock):
    aioclient_mock.post(PUSH_URL, json={})  # async_validate posts an empty batch
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == USER_INPUT


async def test_user_flow_invalid_auth(hass, aioclient_mock):
    aioclient_mock.post(PUSH_URL, status=401)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass, aioclient_mock):
    aioclient_mock.post(PUSH_URL, exc=aiohttp.ClientError("no route"))
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_entry_aborts(hass, aioclient_mock):
    aioclient_mock.post(PUSH_URL, json={})
    existing = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id=f"{BASE}::{USER}")
    existing.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ── options flow ─────────────────────────────────────────────────────────────
def _entry(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options=options or {})
    entry.add_to_hass(hass)
    return entry


async def _menu(hass, entry, step):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_options_settings_roundtrip_persists_interval(hass):
    entry = _entry(hass, {CONF_INTERVAL: 300})
    result = await _menu(hass, entry, "settings")
    assert result["type"] == FlowResultType.FORM
    # Submit a new interval, then bounce back to the menu and Save & close.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL: 600, "presets_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_INTERVAL] == 600


async def test_options_settings_clamps_below_minimum(hass):
    entry = _entry(hass, {CONF_INTERVAL: 300})
    result = await _menu(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL: 5, "presets_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_INTERVAL] >= 60  # MIN_INTERVAL floor


async def test_options_add_custom_mapping_two_step(hass):
    hass.states.async_set(
        "sensor.washer",
        "42",
        {"unit_of_measurement": "W", "friendly_name": "Washer", "device_class": "power"},
    )
    entry = _entry(hass)
    # Step 1: pick the entity.
    result = await _menu(hass, entry, "add_mapping")
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CUSTOM_ENTITY: "sensor.washer"}
    )
    # Step 2: form pre-filled from suggest_mapping — confirm the derived signal.
    assert result["step_id"] == "configure_mapping"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CUSTOM_SIGNAL: "washer_power",
            CONF_CUSTOM_KIND: KIND_METRIC,
            "unit": "W",
            "display_name": "Washer",
        },
    )
    # Back at the menu; Save & close.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    custom = entry.options[CONF_CUSTOM]
    assert len(custom) == 1
    assert custom[0][CONF_CUSTOM_ENTITY] == "sensor.washer"
    assert custom[0][CONF_CUSTOM_SIGNAL] == "washer_power"


async def test_options_add_mapping_rejects_bad_slug(hass):
    hass.states.async_set("sensor.washer", "42", {"unit_of_measurement": "W"})
    entry = _entry(hass)
    result = await _menu(hass, entry, "add_mapping")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CUSTOM_ENTITY: "sensor.washer"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_CUSTOM_SIGNAL: "Bad Slug!", CONF_CUSTOM_KIND: KIND_METRIC},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_CUSTOM_SIGNAL: "invalid_signal"}


async def test_options_remove_custom_mapping(hass):
    existing = {
        CONF_CUSTOM_ENTITY: "sensor.washer",
        CONF_CUSTOM_SIGNAL: "washer_power",
        CONF_CUSTOM_KIND: KIND_METRIC,
        "unit": "W",
        "display_name": "Washer",
    }
    entry = _entry(hass, {CONF_CUSTOM: [existing]})
    result = await _menu(hass, entry, "remove_mappings")
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"remove": ["sensor.washer"]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_CUSTOM] == []


async def test_options_carries_panel_managed_keys_through(hass):
    """The legacy menu flow must not clobber the panel's per-preset/per-entity opt-outs on save."""
    entry = _entry(
        hass,
        {
            "preset_disabled": ["humidity"],
            "preset_excluded": ["sensor.freezer"],
            "preset_caps": {"home_energy": 5},
        },
    )
    result = await _menu(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL: 300, "presets_enabled": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options["preset_disabled"] == ["humidity"]
    assert entry.options["preset_excluded"] == ["sensor.freezer"]
    assert entry.options["preset_caps"] == {"home_energy": 5}
