"""Tests for the push engine (coordinator.py) — the heart of the integration.

The ``SesharoClient`` is the external I/O boundary, so it's replaced with a recording fake; the
*coordinator logic* (mapping resolution, unit conversion, preset gating, per-entity exclusion, event
buffering, requeue-on-failure, push-health state) runs for real against a real ``hass`` with real
``State`` objects set via ``hass.states.async_set``.
"""

from __future__ import annotations

import pytest

from custom_components.sesharo.api import SesharoApiError
from custom_components.sesharo.const import (
    CONF_CUSTOM,
    CONF_CUSTOM_ENTITY,
    CONF_CUSTOM_KIND,
    CONF_CUSTOM_NAME,
    CONF_CUSTOM_SIGNAL,
    CONF_CUSTOM_TARGET_UNIT,
    CONF_CUSTOM_UNIT,
    CONF_INTERVAL,
    CONF_PRESET_DISABLED,
    CONF_PRESET_EXCLUDED,
    CONF_PRESETS_ENABLED,
    KIND_METRIC,
)
from custom_components.sesharo.coordinator import SesharoPusher

BASE = "https://api.example.test"


class FakeClient:
    """Records pushes; can be toggled to fail like a real transport error would."""

    def __init__(self) -> None:
        self.base_url = BASE
        self.pushes: list[dict] = []
        self.fail = False
        self.signals = {"metrics": [], "events": []}

    async def async_push(self, payload: dict) -> dict:
        if self.fail:
            raise SesharoApiError("simulated push failure")
        self.pushes.append(payload)
        return {"ok": True}

    async def async_list_signals(self) -> dict:
        return self.signals


def _pusher(hass, client: FakeClient, **options) -> SesharoPusher:
    options.setdefault(CONF_INTERVAL, 300)
    return SesharoPusher(hass, client, options)


def _last_readings(client: FakeClient) -> list[dict]:
    assert client.pushes, "expected at least one push"
    return client.pushes[-1]["readings"]


def _by_signal(readings: list[dict]) -> dict[str, dict]:
    return {r["signal"]: r for r in readings}


# ── preset metrics + unit conversion ─────────────────────────────────────────
async def test_preset_metric_converts_to_canonical_unit(hass):
    client = FakeClient()
    pusher = _pusher(hass, client)
    # A temperature preset reading in Fahrenheit must be sent in canonical celsius.
    hass.states.async_set(
        "sensor.attic", "212", {"device_class": "temperature", "unit_of_measurement": "°F"}
    )
    await pusher.async_push_now()
    reading = _by_signal(_last_readings(client))["home_temperature"]
    assert reading["value"] == pytest.approx(100.0)
    assert reading["entity_id"] == "sensor.attic"


async def test_master_switch_off_sends_no_preset_readings(hass):
    client = FakeClient()
    pusher = _pusher(hass, client, **{CONF_PRESETS_ENABLED: False})
    hass.states.async_set("sensor.attic", "21", {"device_class": "temperature"})
    await pusher.async_push_now()
    assert _last_readings(client) == []


async def test_per_class_opt_out_drops_that_class_only(hass):
    client = FakeClient()
    pusher = _pusher(hass, client, **{CONF_PRESET_DISABLED: ["temperature"]})
    hass.states.async_set("sensor.attic", "21", {"device_class": "temperature"})
    hass.states.async_set("sensor.meter", "500", {"device_class": "power"})
    await pusher.async_push_now()
    signals = _by_signal(_last_readings(client))
    assert "home_temperature" not in signals
    assert "home_power" in signals


async def test_per_entity_exclusion_keeps_siblings(hass):
    client = FakeClient()
    pusher = _pusher(hass, client, **{CONF_PRESET_EXCLUDED: ["sensor.freezer"]})
    hass.states.async_set("sensor.freezer", "-18", {"device_class": "temperature"})
    hass.states.async_set("sensor.living", "21", {"device_class": "temperature"})
    await pusher.async_push_now()
    entities = {r["entity_id"] for r in _last_readings(client)}
    assert entities == {"sensor.living"}


async def test_non_numeric_and_unavailable_states_skipped(hass):
    client = FakeClient()
    pusher = _pusher(hass, client)
    hass.states.async_set("sensor.text", "cloudy", {"device_class": "temperature"})
    hass.states.async_set("sensor.dead", "unavailable", {"device_class": "power"})
    await pusher.async_push_now()
    assert _last_readings(client) == []


# ── custom mappings ──────────────────────────────────────────────────────────
def _custom(entity, signal, kind=KIND_METRIC, unit="", name="", target_unit=None):
    entry = {
        CONF_CUSTOM_ENTITY: entity,
        CONF_CUSTOM_SIGNAL: signal,
        CONF_CUSTOM_KIND: kind,
        CONF_CUSTOM_UNIT: unit,
        CONF_CUSTOM_NAME: name,
    }
    if target_unit is not None:
        entry[CONF_CUSTOM_TARGET_UNIT] = target_unit
    return entry


async def test_custom_net_new_metric_sends_unit_and_display_name(hass):
    client = FakeClient()
    pusher = _pusher(
        hass,
        client,
        **{CONF_CUSTOM: [_custom("sensor.washer", "washer_power", unit="W", name="Washer")]},
    )
    hass.states.async_set("sensor.washer", "42", {"unit_of_measurement": "W"})
    await pusher.async_push_now()
    reading = _by_signal(_last_readings(client))["washer_power"]
    assert reading["value"] == pytest.approx(42.0)
    assert reading["unit"] == "W"
    assert reading["display_name"] == "Washer"


async def test_custom_join_converts_into_target_unit(hass):
    client = FakeClient()
    pusher = _pusher(
        hass,
        client,
        **{CONF_CUSTOM: [_custom("sensor.meter", "home_power", unit="kW", target_unit="watts")]},
    )
    hass.states.async_set("sensor.meter", "2", {"unit_of_measurement": "kW"})
    await pusher.async_push_now()
    reading = _by_signal(_last_readings(client))["home_power"]
    assert reading["value"] == pytest.approx(2000.0)  # 2 kW → 2000 W
    assert reading["unit"] == "watts"


async def test_custom_join_inconvertible_is_skipped(hass):
    client = FakeClient()
    pusher = _pusher(
        hass,
        client,
        # Temperature entity joined to a watts signal — no conversion path → must be skipped,
        # never sent with a wrong number.
        **{CONF_CUSTOM: [_custom("sensor.temp", "home_power", unit="°C", target_unit="watts")]},
    )
    hass.states.async_set("sensor.temp", "20", {"unit_of_measurement": "°C"})
    await pusher.async_push_now()
    assert _last_readings(client) == []


# ── event capture + buffering ────────────────────────────────────────────────
async def test_state_transition_is_buffered_and_flushed(hass):
    client = FakeClient()
    pusher = _pusher(hass, client)
    await pusher.async_start()
    try:
        hass.states.async_set("binary_sensor.door", "off", {"device_class": "door"})
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.door", "on", {"device_class": "door"})
        await hass.async_block_till_done()
        await pusher.async_push_now()
    finally:
        pusher._unsubs and [u() for u in pusher._unsubs]
        pusher._unsubs.clear()
    events = client.pushes[-1]["events"]
    door_events = [e for e in events if e["entity_id"] == "binary_sensor.door"]
    assert door_events, "door transition should have been captured"
    assert door_events[-1]["category"] == "home_door"
    assert door_events[-1]["state"] == "on"


async def test_attribute_only_change_is_not_an_event(hass):
    client = FakeClient()
    pusher = _pusher(hass, client)
    await pusher.async_start()
    try:
        hass.states.async_set("binary_sensor.door", "on", {"device_class": "door"})
        await hass.async_block_till_done()
        # Same state value, different attribute → not a transition, must not buffer.
        hass.states.async_set("binary_sensor.door", "on", {"device_class": "door", "extra": "x"})
        await hass.async_block_till_done()
        await pusher.async_push_now()
    finally:
        [u() for u in pusher._unsubs]
        pusher._unsubs.clear()
    events = client.pushes[-1]["events"]
    # Exactly one transition (off→on happens implicitly on first set from unknown), the
    # attribute-only change adds nothing.
    door = [e for e in events if e["entity_id"] == "binary_sensor.door"]
    assert len(door) <= 1


# ── failure handling / push-health ───────────────────────────────────────────
async def test_events_requeue_on_failure_then_send_on_recovery(hass):
    client = FakeClient()
    pusher = _pusher(hass, client)
    await pusher.async_start()
    try:
        hass.states.async_set("binary_sensor.door", "off", {"device_class": "door"})
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.door", "on", {"device_class": "door"})
        await hass.async_block_till_done()

        client.fail = True
        await pusher._async_flush()  # interval-style flush; fails
        status = pusher.status()
        assert status["connected"] is False
        assert status["consecutive_failures"] == 1

        client.fail = False
        await pusher._async_flush()  # recovers, requeued events go out
    finally:
        [u() for u in pusher._unsubs]
        pusher._unsubs.clear()
    sent_events = [e for p in client.pushes for e in p["events"]]
    assert any(e["entity_id"] == "binary_sensor.door" for e in sent_events)
    assert pusher.status()["connected"] is True
    assert pusher.status()["consecutive_failures"] == 0


async def test_interval_flush_with_nothing_to_send_is_skipped(hass):
    client = FakeClient()
    pusher = _pusher(hass, client)
    await pusher._async_flush()  # no data, not manual → no API contact
    assert client.pushes == []


async def test_manual_push_contacts_api_even_when_empty(hass):
    client = FakeClient()
    pusher = _pusher(hass, client)
    await pusher.async_push_now()  # manual → contacts API to confirm connectivity
    assert len(client.pushes) == 1
    assert client.pushes[0] == {"readings": [], "events": []}


async def test_status_shape_after_push(hass):
    client = FakeClient()
    pusher = _pusher(hass, client, **{CONF_INTERVAL: 120})
    hass.states.async_set("sensor.meter", "500", {"device_class": "power"})
    await pusher.async_push_now()
    status = pusher.status()
    assert status["connected"] is True
    assert status["interval"] == 120
    assert status["base_url"] == BASE
    assert status["last_push"] is not None
    assert status["next_push"] is not None
    assert status["last_reading_count"] == 1
    # last_sent drives the panel's live table
    assert "sensor.meter" in status["last_sent"]


async def test_async_list_signals_proxies_client(hass):
    client = FakeClient()
    client.signals = {"metrics": [{"signal": "home_power"}], "events": []}
    pusher = _pusher(hass, client)
    assert await pusher.async_list_signals() == client.signals
