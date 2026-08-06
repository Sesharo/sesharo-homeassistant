"""The Sesharo integration — pushes selected Home Assistant states into Sesharo."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SesharoClient
from .const import CONF_BASE_URL, CONF_TOKEN, CONF_USER_ID, DOMAIN
from .coordinator import SesharoPusher
from .panel import async_register_panel, async_unregister_panel
from .sentry import init_sentry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Opt-in, off by default: no-op unless SESHARO_SENTRY_DSN is set on the host.
    # Runs in an executor because sentry-sdk init imports + starts a transport.
    await hass.async_add_executor_job(init_sentry)

    client = SesharoClient(
        async_get_clientsession(hass),
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USER_ID],
        entry.data[CONF_TOKEN],
    )
    pusher = SesharoPusher(hass, client, dict(entry.options))
    await pusher.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = pusher
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))

    # Register the sidebar panel + its WebSocket commands (global, once — guarded internally).
    await async_register_panel(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Fires on every options change (reload), so it must NOT touch the panel/static-path
    # registration — those are torn down only on permanent removal (async_remove_entry).
    pusher: SesharoPusher | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if pusher is not None:
        await pusher.async_stop()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Permanent removal — drop the sidebar panel once no Sesharo entries remain."""
    remaining = [k for k in hass.data.get(DOMAIN, {}) if not k.startswith("_")]
    if not remaining:
        async_unregister_panel(hass)


async def _async_reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so changed options (interval, presets, custom mappings) take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
