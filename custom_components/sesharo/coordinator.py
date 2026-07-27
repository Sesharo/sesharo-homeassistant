"""Collects mapped Home Assistant states and pushes them to Sesharo on an interval.

Readings (numeric sensors) are snapshotted every interval; events (discrete state changes on
presence/door/motion/person entities) are buffered as they happen and flushed with the next push.
Both preset (auto-discovered by ``device_class``) and user-defined custom mappings are honoured.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_time_interval

from .api import SesharoApiError, SesharoClient
from .const import (
    CONF_CUSTOM,
    CONF_CUSTOM_ENTITY,
    CONF_CUSTOM_KIND,
    CONF_CUSTOM_NAME,
    CONF_CUSTOM_SIGNAL,
    CONF_CUSTOM_UNIT,
    CONF_INTERVAL,
    CONF_PRESETS_ENABLED,
    DEFAULT_INTERVAL,
    KIND_METRIC,
    PERSON_EVENT_CATEGORY,
    PRESET_EVENTS,
    PRESET_METRICS,
)

_LOGGER = logging.getLogger(__name__)

_SKIP_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE, "", "None"}


def _to_canonical(signal: str, value: float, unit: str | None) -> float:
    """Convert a preset reading to Sesharo's canonical unit. Custom signals pass through."""
    u = (unit or "").strip().lower()
    if signal == "home_temperature":
        if u in ("°f", "f", "fahrenheit"):
            return (value - 32.0) * 5.0 / 9.0
        if u in ("k", "kelvin"):
            return value - 273.15
        return value  # assume °C
    if signal == "home_power":
        if u in ("kw", "kilowatt"):
            return value * 1000.0
        if u in ("mw",):  # milliwatt
            return value / 1000.0
        return value  # W
    if signal == "home_energy":
        if u in ("wh", "watt-hour", "watt hour"):
            return value / 1000.0
        if u in ("mwh",):
            return value * 1000.0
        return value  # kWh
    return value


class SesharoPusher:
    def __init__(self, hass: HomeAssistant, client: SesharoClient, options: dict[str, Any]) -> None:
        self._hass = hass
        self._client = client
        self._options = options
        self._event_buffer: list[dict[str, Any]] = []
        self._unsubs: list = []
        # entity_id -> custom mapping entry
        self._custom_metric: dict[str, dict] = {}
        self._custom_event: dict[str, dict] = {}
        for entry in options.get(CONF_CUSTOM, []) or []:
            target = self._custom_metric if entry.get(CONF_CUSTOM_KIND) == KIND_METRIC else self._custom_event
            target[entry[CONF_CUSTOM_ENTITY]] = entry
        self._presets_enabled = options.get(CONF_PRESETS_ENABLED, True)

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        interval = timedelta(seconds=int(self._options.get(CONF_INTERVAL, DEFAULT_INTERVAL)))
        self._unsubs.append(async_track_time_interval(self._hass, self._async_flush, interval))
        self._unsubs.append(self._hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._async_flush(final=True)

    # ── mapping resolution ────────────────────────────────────────────────
    def _metric_for(self, state: State) -> tuple[str, str | None, str | None] | None:
        """Return (signal, unit, display_name) for a numeric entity, or None."""
        custom = self._custom_metric.get(state.entity_id)
        if custom is not None:
            return custom[CONF_CUSTOM_SIGNAL], custom.get(CONF_CUSTOM_UNIT), custom.get(CONF_CUSTOM_NAME)
        if not self._presets_enabled:
            return None
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
        preset = PRESET_METRICS.get(device_class) if device_class else None
        if preset is not None:
            signal, canonical_unit = preset
            return signal, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, canonical_unit), None
        return None

    def _event_category_for(self, entity_id: str, state: State) -> str | None:
        custom = self._custom_event.get(entity_id)
        if custom is not None:
            return custom[CONF_CUSTOM_SIGNAL]
        if not self._presets_enabled:
            return None
        if entity_id.startswith("person."):
            return PERSON_EVENT_CATEGORY
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
        return PRESET_EVENTS.get(device_class) if device_class else None

    # ── event capture ─────────────────────────────────────────────────────
    @callback
    def _on_state_changed(self, event: Event) -> None:
        new_state: State | None = event.data.get("new_state")
        old_state: State | None = event.data.get("old_state")
        if new_state is None or new_state.state in _SKIP_STATES:
            return
        if old_state is not None and old_state.state == new_state.state:
            return  # attribute-only change, not a transition
        category = self._event_category_for(new_state.entity_id, new_state)
        if category is None:
            return
        self._event_buffer.append({
            "category": category,
            "occurred_at": new_state.last_changed.isoformat(),
            "state": new_state.state,
            "entity_id": new_state.entity_id,
            "name": new_state.attributes.get(ATTR_FRIENDLY_NAME),
        })

    # ── flush ─────────────────────────────────────────────────────────────
    def _snapshot_readings(self) -> list[dict[str, Any]]:
        readings: list[dict[str, Any]] = []
        for state in self._hass.states.async_all():
            if state.state in _SKIP_STATES:
                continue
            mapping = self._metric_for(state)
            if mapping is None:
                continue
            signal, unit, display_name = mapping
            try:
                raw = float(state.state)
            except (ValueError, TypeError):
                continue
            is_preset = state.entity_id not in self._custom_metric
            value = _to_canonical(signal, raw, unit) if is_preset else raw
            reading: dict[str, Any] = {
                "signal": signal,
                "value": value,
                "recorded_at": state.last_changed.isoformat(),
                "entity_id": state.entity_id,
            }
            if not is_preset:  # custom types carry the user's unit + name so the backend can create them
                reading["unit"] = unit
                reading["display_name"] = display_name or state.attributes.get(ATTR_FRIENDLY_NAME)
            readings.append(reading)
        return readings

    async def _async_flush(self, now: datetime | None = None, *, final: bool = False) -> None:
        readings = self._snapshot_readings()
        events = self._event_buffer
        self._event_buffer = []
        if not readings and not events:
            return
        try:
            result = await self._client.async_push({"readings": readings, "events": events})
            _LOGGER.debug("Sesharo push: %s", result)
        except SesharoApiError as exc:
            # Requeue events so a transient failure doesn't drop transitions; readings re-snapshot.
            self._event_buffer = events + self._event_buffer
            if final:
                _LOGGER.warning("Final Sesharo push failed, %d event(s) dropped: %s", len(events), exc)
            else:
                _LOGGER.warning("Sesharo push failed, will retry next interval: %s", exc)
