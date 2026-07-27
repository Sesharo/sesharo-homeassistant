"""The Sesharo integration — pushes selected Home Assistant states into Sesharo."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SesharoClient
from .const import CONF_BASE_URL, CONF_TOKEN, CONF_USER_ID, DOMAIN
from .coordinator import SesharoPusher

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    pusher: SesharoPusher | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if pusher is not None:
        await pusher.async_stop()
    return True


async def _async_reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so changed options (interval, presets, custom mappings) take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
