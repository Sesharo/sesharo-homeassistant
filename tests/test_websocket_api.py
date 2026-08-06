"""Tests for the panel's WebSocket command surface (websocket_api.py).

Uses pHACC's ``hass_ws_client`` to issue real ``sesharo/*`` commands against a set-up entry:
reads (get_config/status/list_signals/suggestions), admin-gated writes (set_settings/set_presets/
set_preset_excluded/set_mappings, incl. slug validation + de-dup), push_now, and the require_admin
rejection for a read-only user.
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sesharo.const import (
    CONF_BASE_URL,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

BASE = "https://api.example.test"
USER = "user-1"
TOKEN = "pat-abc"
PUSH_URL = f"{BASE}/users/{USER}/home-assistant"
SIGNALS_URL = f"{PUSH_URL}/signals"
USER_INPUT = {CONF_BASE_URL: BASE, CONF_USER_ID: USER, CONF_TOKEN: TOKEN}


@pytest.fixture
async def entry(hass):
    e = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    yield e
    await hass.config_entries.async_unload(e.entry_id)
    await hass.async_block_till_done()


# ── reads ────────────────────────────────────────────────────────────────────
async def test_get_config_returns_shape(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/get_config"})
    msg = await client.receive_json()
    assert msg["success"] is True
    result = msg["result"]
    assert result["entry_id"] == entry.entry_id
    assert result["presets_enabled"] is True
    assert result["mappings"] == []
    # The preset catalog is echoed for the panel's preset rows.
    assert any(p["signal"] == "home_temperature" for p in result["presets"])
    # Cap config is echoed so the panel knows the default + any per-signal overrides.
    assert result["preset_caps"] == {}
    assert result["default_cap"] == 10


async def test_status_returns_health(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/status"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["base_url"] == BASE
    assert msg["result"]["connected"] is None  # never pushed yet


async def test_list_signals_proxies_api(hass, hass_ws_client, aioclient_mock, entry):
    aioclient_mock.get(SIGNALS_URL, json={"metrics": [{"signal": "home_power"}], "events": []})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/list_signals"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["metrics"][0]["signal"] == "home_power"


async def test_suggestions_lists_uncovered_entities(hass, hass_ws_client, entry):
    # A plain numeric sensor with no preset device_class → should be suggested.
    hass.states.async_set("sensor.washer_cycles", "7", {"friendly_name": "Washer Cycles"})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/suggestions"})
    msg = await client.receive_json()
    assert msg["success"] is True
    entities = {c["entity_id"] for c in msg["result"]["candidates"]}
    assert "sensor.washer_cycles" in entities


# ── admin writes ─────────────────────────────────────────────────────────────
async def test_set_settings_updates_interval(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/set_settings", "interval": 600})
    msg = await client.receive_json()
    assert msg["success"] is True
    await hass.async_block_till_done()
    assert entry.options["interval"] == 600


async def test_set_settings_clamps_to_minimum(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/set_settings", "interval": 5})
    await client.receive_json()
    await hass.async_block_till_done()
    assert entry.options["interval"] >= 60


async def test_set_presets_persists(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "sesharo/set_presets",
            "presets_enabled": True,
            "preset_disabled": ["humidity", "humidity"],
        }
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    await hass.async_block_till_done()
    assert entry.options["preset_disabled"] == ["humidity"]  # de-duped


async def test_set_preset_excluded_persists(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "sesharo/set_preset_excluded",
            "preset_excluded": ["sensor.freezer", "sensor.freezer"],
        }
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    await hass.async_block_till_done()
    assert entry.options["preset_excluded"] == ["sensor.freezer"]


async def test_set_preset_cap_persists_override(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "sesharo/set_preset_cap", "signal": "home_energy", "cap": 5}
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    await hass.async_block_till_done()
    assert entry.options["preset_caps"] == {"home_energy": 5}


async def test_set_preset_cap_default_value_is_not_pinned(hass, hass_ws_client, entry):
    # Setting a signal back to the default cap should drop it from the dict (keeps options clean).
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "sesharo/set_preset_cap", "signal": "home_energy", "cap": 3}
    )
    assert (await client.receive_json())["success"] is True
    await client.send_json(
        {"id": 2, "type": "sesharo/set_preset_cap", "signal": "home_energy", "cap": 10}
    )
    assert (await client.receive_json())["success"] is True
    await hass.async_block_till_done()
    assert entry.options["preset_caps"] == {}


async def test_set_preset_cap_clamps_negative_to_zero(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "sesharo/set_preset_cap", "signal": "home_power", "cap": -4}
    )
    assert (await client.receive_json())["success"] is True
    await hass.async_block_till_done()
    assert entry.options["preset_caps"] == {"home_power": 0}  # 0 = no limit


async def test_set_mappings_validates_slug(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "sesharo/set_mappings",
            "mappings": [{"entity_id": "sensor.x", "signal": "Bad Slug!", "kind": "metric"}],
        }
    )
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "invalid_signal"


async def test_set_mappings_dedups_by_entity(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "sesharo/set_mappings",
            "mappings": [
                {"entity_id": "sensor.x", "signal": "first", "kind": "metric"},
                {
                    "entity_id": "sensor.x",
                    "signal": "second",
                    "kind": "metric",
                },  # same entity, wins
            ],
        }
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["count"] == 1
    await hass.async_block_till_done()
    assert entry.options["custom"][0]["signal"] == "second"


async def test_push_now_contacts_api(hass, hass_ws_client, aioclient_mock, entry):
    aioclient_mock.post(PUSH_URL, json={"accepted": 0})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/push_now"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["connected"] is True  # a successful manual push flips this on
    assert len(aioclient_mock.mock_calls) == 1


# ── admin gate ───────────────────────────────────────────────────────────────
async def test_non_admin_cannot_write(hass, hass_ws_client, hass_read_only_access_token, entry):
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json({"id": 1, "type": "sesharo/set_settings", "interval": 600})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "unauthorized"


async def test_unknown_entry_id_errors(hass, hass_ws_client, entry):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sesharo/status", "entry_id": "does-not-exist"})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "not_found"
