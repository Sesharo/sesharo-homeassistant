"""WebSocket commands that back the Sesharo sidebar panel.

HA can only render one options form at a time, so the mapping *table*, live status and inline
add-row of the panel can't come from the config/options flow. Instead the panel (a JS module served
from ``www/``) talks to these commands over ``hass.callWS({type: "sesharo/…"})``.

All commands operate on the integration's single (or a named) config entry:
  * reads   — ``get_config``, ``status``, ``list_signals``, ``suggestions``
  * writes  — ``set_settings``, ``set_presets``, ``set_mappings``, ``push_now`` (admin only)

Writes persist by updating the config entry's options, which fires the update listener in
``__init__`` → the entry reloads → a fresh ``SesharoPusher`` picks up the change.
"""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .api import SesharoApiError
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
    CONF_PRESETS_ENABLED,
    DEFAULT_INTERVAL,
    DOMAIN,
    KIND_EVENT,
    KIND_METRIC,
    MIN_INTERVAL,
    PRESET_CATALOG,
)
from .coordinator import SesharoPusher
from .discovery import discover_candidates

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,48}$")

_MAPPING_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CUSTOM_ENTITY): str,
        vol.Required(CONF_CUSTOM_SIGNAL): str,
        vol.Required(CONF_CUSTOM_KIND): vol.In([KIND_METRIC, KIND_EVENT]),
        vol.Optional(CONF_CUSTOM_UNIT, default=""): str,
        vol.Optional(CONF_CUSTOM_NAME, default=""): str,
        vol.Optional(CONF_CUSTOM_TARGET_UNIT): vol.Any(str, None),
    },
    extra=vol.REMOVE_EXTRA,
)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register every Sesharo panel command (idempotent — guarded in __init__)."""
    for handler in (
        ws_get_config,
        ws_status,
        ws_list_signals,
        ws_suggestions,
        ws_set_settings,
        ws_set_presets,
        ws_set_mappings,
        ws_push_now,
    ):
        websocket_api.async_register_command(hass, handler)


# ── entry/pusher resolution ──────────────────────────────────────────────────
def _resolve(
    hass: HomeAssistant, msg: dict[str, Any]
) -> tuple[ConfigEntry | None, SesharoPusher | None]:
    entries = hass.config_entries.async_entries(DOMAIN)
    entry_id = msg.get("entry_id")
    entry = (
        hass.config_entries.async_get_entry(entry_id)
        if entry_id
        else (entries[0] if entries else None)
    )
    pusher = hass.data.get(DOMAIN, {}).get(entry.entry_id) if entry else None
    return entry, pusher


def _presets_payload() -> list[dict[str, Any]]:
    return [
        {"device_class": dc, "kind": kind, "signal": signal, "label": label, "icon": icon}
        for dc, kind, signal, label, icon in PRESET_CATALOG
    ]


# ── reads ────────────────────────────────────────────────────────────────────
@websocket_api.websocket_command(
    {vol.Required("type"): "sesharo/get_config", vol.Optional("entry_id"): str}
)
@callback
def ws_get_config(hass, connection, msg) -> None:
    entry, _ = _resolve(hass, msg)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "No Sesharo integration configured")
        return
    opts = entry.options
    connection.send_result(
        msg["id"],
        {
            "entry_id": entry.entry_id,
            "interval": opts.get(CONF_INTERVAL, DEFAULT_INTERVAL),
            "presets_enabled": opts.get(CONF_PRESETS_ENABLED, True),
            "preset_disabled": list(opts.get(CONF_PRESET_DISABLED, []) or []),
            "mappings": [dict(c) for c in opts.get(CONF_CUSTOM, []) or []],
            "presets": _presets_payload(),
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "sesharo/status", vol.Optional("entry_id"): str}
)
@callback
def ws_status(hass, connection, msg) -> None:
    _, pusher = _resolve(hass, msg)
    if pusher is None:
        connection.send_error(msg["id"], "not_found", "Sesharo integration not loaded")
        return
    connection.send_result(msg["id"], pusher.status())


@websocket_api.websocket_command(
    {vol.Required("type"): "sesharo/list_signals", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_list_signals(hass, connection, msg) -> None:
    _, pusher = _resolve(hass, msg)
    if pusher is None:
        connection.send_error(msg["id"], "not_found", "Sesharo integration not loaded")
        return
    try:
        connection.send_result(msg["id"], await pusher.async_list_signals())
    except SesharoApiError as exc:
        connection.send_error(msg["id"], "api_error", str(exc))


@websocket_api.websocket_command(
    {vol.Required("type"): "sesharo/suggestions", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_suggestions(hass, connection, msg) -> None:
    entry, _ = _resolve(hass, msg)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "No Sesharo integration configured")
        return
    opts = entry.options
    mapped_entities = {c[CONF_CUSTOM_ENTITY] for c in opts.get(CONF_CUSTOM, []) or []}
    mapped_signals = {c[CONF_CUSTOM_SIGNAL] for c in opts.get(CONF_CUSTOM, []) or []}
    # Snapshot the states on the event loop, then run the (potentially large) scan in an executor so
    # a big entity registry can never block the loop — defence-in-depth beside the termination fix in
    # discover_candidates. State objects are immutable snapshots, so they're safe to read off-thread.
    states = hass.states.async_all()
    presets_enabled = opts.get(CONF_PRESETS_ENABLED, True)
    candidates = await hass.async_add_executor_job(
        lambda: discover_candidates(
            states, mapped_entities, mapped_signals, presets_enabled=presets_enabled
        )
    )
    connection.send_result(msg["id"], {"candidates": candidates})


# ── writes (admin only) ──────────────────────────────────────────────────────
async def _update_options(hass: HomeAssistant, entry: ConfigEntry, changes: dict[str, Any]) -> None:
    """Merge ``changes`` into the entry options and persist (fires the reload listener)."""
    new_options = {**dict(entry.options), **changes}
    hass.config_entries.async_update_entry(entry, options=new_options)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "sesharo/set_settings",
        vol.Optional("entry_id"): str,
        vol.Required("interval"): vol.Coerce(int),
    }
)
@websocket_api.async_response
async def ws_set_settings(hass, connection, msg) -> None:
    entry, _ = _resolve(hass, msg)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "No Sesharo integration configured")
        return
    await _update_options(hass, entry, {CONF_INTERVAL: max(MIN_INTERVAL, int(msg["interval"]))})
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "sesharo/set_presets",
        vol.Optional("entry_id"): str,
        vol.Required("presets_enabled"): bool,
        vol.Optional("preset_disabled", default=list): [str],
    }
)
@websocket_api.async_response
async def ws_set_presets(hass, connection, msg) -> None:
    entry, _ = _resolve(hass, msg)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "No Sesharo integration configured")
        return
    await _update_options(
        hass,
        entry,
        {
            CONF_PRESETS_ENABLED: bool(msg["presets_enabled"]),
            CONF_PRESET_DISABLED: list(dict.fromkeys(msg.get("preset_disabled", []))),
        },
    )
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "sesharo/set_mappings",
        vol.Optional("entry_id"): str,
        vol.Required("mappings"): [_MAPPING_SCHEMA],
    }
)
@websocket_api.async_response
async def ws_set_mappings(hass, connection, msg) -> None:
    entry, _ = _resolve(hass, msg)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "No Sesharo integration configured")
        return
    # Validate slugs + de-dup by entity (last wins) before persisting.
    by_entity: dict[str, dict[str, Any]] = {}
    for m in msg["mappings"]:
        signal = (m[CONF_CUSTOM_SIGNAL] or "").strip().lower()
        if not _SLUG_RE.match(signal):
            connection.send_error(
                msg["id"], "invalid_signal",
                f"Signal '{signal}' must be lowercase letters, numbers and underscores (max 49).",
            )
            return
        entry_map = {
            CONF_CUSTOM_ENTITY: m[CONF_CUSTOM_ENTITY],
            CONF_CUSTOM_SIGNAL: signal,
            CONF_CUSTOM_KIND: m[CONF_CUSTOM_KIND],
            CONF_CUSTOM_UNIT: (m.get(CONF_CUSTOM_UNIT) or "").strip(),
            CONF_CUSTOM_NAME: (m.get(CONF_CUSTOM_NAME) or "").strip(),
        }
        target_unit = m.get(CONF_CUSTOM_TARGET_UNIT)
        if target_unit:
            entry_map[CONF_CUSTOM_TARGET_UNIT] = target_unit.strip()
        by_entity[m[CONF_CUSTOM_ENTITY]] = entry_map
    await _update_options(hass, entry, {CONF_CUSTOM: list(by_entity.values())})
    connection.send_result(msg["id"], {"ok": True, "count": len(by_entity)})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "sesharo/push_now", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_push_now(hass, connection, msg) -> None:
    _, pusher = _resolve(hass, msg)
    if pusher is None:
        connection.send_error(msg["id"], "not_found", "Sesharo integration not loaded")
        return
    connection.send_result(msg["id"], await pusher.async_push_now())
