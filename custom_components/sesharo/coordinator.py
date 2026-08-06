"""Collects mapped Home Assistant states and pushes them to Sesharo on an interval.

Readings (numeric sensors) are snapshotted every interval; events (discrete state changes on
presence/door/motion/person entities) are buffered as they happen and flushed with the next push.
Both preset (auto-discovered by ``device_class``) and user-defined custom mappings are honoured.

The pusher also keeps a small amount of **push-health state** (last/next push, failure streak, the
last value sent per entity) which the sidebar panel reads over the WebSocket API — the integration
creates no HA entities, so a diagnostic sensor isn't an option.
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
from homeassistant.util import dt as dt_util

from .api import SesharoApiError, SesharoClient
from .const import (
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
    DEFAULT_INTERVAL,
    KIND_METRIC,
    PERSON_EVENT_CATEGORY,
    PRESET_EVENTS,
    PRESET_METRICS,
)
from .units import convert_units, fmt_value, to_canonical

_LOGGER = logging.getLogger(__name__)

_SKIP_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE, "", "None"}

# The device_class a `person.*` entity is toggled under (it has no device_class of its own).
_PRESENCE_DEVICE_CLASS = "occupancy"


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
            target = (
                self._custom_metric
                if entry.get(CONF_CUSTOM_KIND) == KIND_METRIC
                else self._custom_event
            )
            target[entry[CONF_CUSTOM_ENTITY]] = entry
        self._presets_enabled = options.get(CONF_PRESETS_ENABLED, True)
        self._preset_disabled: set[str] = set(options.get(CONF_PRESET_DISABLED, []) or [])
        # Individual preset-matched entities the user opted out of (preset stays on for the rest).
        self._preset_excluded: set[str] = set(options.get(CONF_PRESET_EXCLUDED, []) or [])
        self._interval = timedelta(seconds=int(self._options.get(CONF_INTERVAL, DEFAULT_INTERVAL)))

        # ── push-health state (read by the panel via websocket_api) ──────────
        self._last_push_at: datetime | None = None
        self._last_ok: bool | None = None  # None = never pushed yet
        self._last_error: str | None = None
        self._consecutive_failures = 0
        self._last_reading_count = 0
        self._last_event_count = 0
        # entity_id -> {"value": str, "at": iso, "signal": str}
        self._last_sent: dict[str, dict[str, Any]] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        self._unsubs.append(
            async_track_time_interval(self._hass, self._async_flush, self._interval)
        )
        self._unsubs.append(
            self._hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed)
        )

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._async_flush(final=True)

    # ── preset gating ─────────────────────────────────────────────────────
    def _preset_active(self, device_class: str | None) -> bool:
        return bool(
            self._presets_enabled
            and device_class is not None
            and device_class not in self._preset_disabled
        )

    # ── mapping resolution ────────────────────────────────────────────────
    def _metric_for(self, state: State) -> tuple[str, str | None, str | None, bool] | None:
        """Return (signal, unit, display_name, is_custom) for a numeric entity, or None."""
        custom = self._custom_metric.get(state.entity_id)
        if custom is not None:
            return (
                custom[CONF_CUSTOM_SIGNAL],
                custom.get(CONF_CUSTOM_UNIT),
                custom.get(CONF_CUSTOM_NAME),
                True,
            )
        if state.entity_id in self._preset_excluded:
            return None  # preset stays on for its class, but this entity was opted out
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
        if self._preset_active(device_class):
            preset = PRESET_METRICS.get(device_class)
            if preset is not None:
                signal, canonical_unit = preset
                return (
                    signal,
                    state.attributes.get(ATTR_UNIT_OF_MEASUREMENT, canonical_unit),
                    None,
                    False,
                )
        return None

    def _event_category_for(self, entity_id: str, state: State) -> str | None:
        custom = self._custom_event.get(entity_id)
        if custom is not None:
            return custom[CONF_CUSTOM_SIGNAL]
        if entity_id in self._preset_excluded:
            return None  # preset stays on for its class, but this entity was opted out
        if entity_id.startswith("person."):
            return PERSON_EVENT_CATEGORY if self._preset_active(_PRESENCE_DEVICE_CLASS) else None
        device_class = state.attributes.get(ATTR_DEVICE_CLASS)
        if self._preset_active(device_class):
            return PRESET_EVENTS.get(device_class)
        return None

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
        occurred_at = new_state.last_changed.isoformat()
        self._event_buffer.append(
            {
                "category": category,
                "occurred_at": occurred_at,
                "state": new_state.state,
                "entity_id": new_state.entity_id,
                "name": new_state.attributes.get(ATTR_FRIENDLY_NAME),
            }
        )
        self._last_sent[new_state.entity_id] = {
            "value": new_state.state,
            "at": occurred_at,
            "signal": category,
        }

    # ── flush ─────────────────────────────────────────────────────────────
    def _snapshot_readings(self) -> list[dict[str, Any]]:
        readings: list[dict[str, Any]] = []
        for state in self._hass.states.async_all():
            if state.state in _SKIP_STATES:
                continue
            mapping = self._metric_for(state)
            if mapping is None:
                continue
            signal, unit, display_name, is_custom = mapping
            try:
                raw = float(state.state)
            except (ValueError, TypeError):
                continue
            entity_unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            recorded_at = state.last_changed.isoformat()
            reading: dict[str, Any] = {
                "signal": signal,
                "recorded_at": recorded_at,
                "entity_id": state.entity_id,
            }
            if not is_custom:
                # Preset: convert the entity's unit → the signal's canonical unit.
                value = to_canonical(signal, raw, entity_unit)
            else:
                custom = self._custom_metric.get(state.entity_id, {})
                target_unit = custom.get(CONF_CUSTOM_TARGET_UNIT)
                if target_unit:
                    # Joining an existing signal with a fixed unit — convert into it or skip. The
                    # panel blocks saving an inconvertible mismatch, so None here should be rare.
                    converted = convert_units(raw, entity_unit, target_unit)
                    if converted is None:
                        _LOGGER.warning(
                            "Skipping %s → %s: cannot convert %s to %s",
                            state.entity_id,
                            signal,
                            entity_unit,
                            target_unit,
                        )
                        continue
                    value = converted
                    reading["unit"] = target_unit
                else:
                    # Net-new custom signal: send raw, carry unit + name so the backend creates it.
                    value = raw
                    reading["unit"] = unit
                    reading["display_name"] = display_name or state.attributes.get(
                        ATTR_FRIENDLY_NAME
                    )
            reading["value"] = value
            readings.append(reading)
            self._last_sent[state.entity_id] = {
                "value": fmt_value(value),
                "at": recorded_at,
                "signal": signal,
            }
        return readings

    async def _async_flush(
        self, now: datetime | None = None, *, final: bool = False, manual: bool = False
    ) -> None:
        readings = self._snapshot_readings()
        events = self._event_buffer
        self._event_buffer = []
        # A manual "push now" still contacts the API even with nothing to send, so the panel can
        # confirm the connection and refresh last/next push.
        if not readings and not events and not manual:
            return
        try:
            result = await self._client.async_push({"readings": readings, "events": events})
            _LOGGER.debug("Sesharo push: %s", result)
            self._last_ok = True
            self._last_error = None
            self._consecutive_failures = 0
            self._last_push_at = dt_util.utcnow()
            self._last_reading_count = len(readings)
            self._last_event_count = len(events)
        except SesharoApiError as exc:
            # Requeue events so a transient failure doesn't drop transitions; readings re-snapshot.
            self._event_buffer = events + self._event_buffer
            self._last_ok = False
            self._last_error = str(exc)
            self._consecutive_failures += 1
            self._last_push_at = dt_util.utcnow()
            if final:
                _LOGGER.warning(
                    "Final Sesharo push failed, %d event(s) dropped: %s", len(events), exc
                )
            else:
                _LOGGER.warning("Sesharo push failed, will retry next interval: %s", exc)

    # ── panel-facing API ──────────────────────────────────────────────────
    async def async_push_now(self) -> dict[str, Any]:
        """Flush immediately (contacting the API even if empty) and return fresh status."""
        await self._async_flush(manual=True)
        return self.status()

    async def async_list_signals(self) -> dict[str, Any]:
        """Proxy the panel's request to list the user's existing Sesharo signals."""
        return await self._client.async_list_signals()

    def last_sent(self) -> dict[str, dict[str, Any]]:
        """entity_id → {value, at, signal} for the last reading/event sent (drives the table)."""
        return dict(self._last_sent)

    def status(self) -> dict[str, Any]:
        last_iso = self._last_push_at.isoformat() if self._last_push_at else None
        next_iso = (self._last_push_at + self._interval).isoformat() if self._last_push_at else None
        return {
            "connected": self._last_ok,  # True / False / None(never pushed)
            "last_push": last_iso,
            "next_push": next_iso,
            "interval": int(self._interval.total_seconds()),
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
            "last_reading_count": self._last_reading_count,
            "last_event_count": self._last_event_count,
            "base_url": self._client.base_url,
            "custom_count": len(self._custom_metric) + len(self._custom_event),
            "last_sent": self.last_sent(),
        }
