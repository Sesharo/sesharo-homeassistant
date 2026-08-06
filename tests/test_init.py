"""Tests for the integration lifecycle (__init__.py + panel.py registration).

Sets the entry up against a real ``hass`` (with the real frontend/panel_custom/http deps) and
asserts: the pusher lands in ``hass.data``, the sidebar panel registers once, an options change
reloads into a fresh pusher, unload drops the pusher but keeps the panel, and permanent removal
finally tears the panel down.
"""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sesharo.const import (
    CONF_BASE_URL,
    CONF_INTERVAL,
    CONF_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)
from custom_components.sesharo.coordinator import SesharoPusher
from custom_components.sesharo.panel import _PANEL_REGISTERED

USER_INPUT = {
    CONF_BASE_URL: "https://api.example.test",
    CONF_USER_ID: "user-1",
    CONF_TOKEN: "pat-abc",
}


async def _setup(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_pusher_and_registers_panel(hass):
    entry = await _setup(hass)
    try:
        pusher = hass.data[DOMAIN][entry.entry_id]
        assert isinstance(pusher, SesharoPusher)
        assert hass.data[DOMAIN].get(_PANEL_REGISTERED) is True
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_unload_removes_pusher_but_keeps_panel(hass):
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    # Pusher gone; panel stays (unload is also the reload path — tearing it down would flicker it).
    assert entry.entry_id not in hass.data[DOMAIN]
    assert hass.data[DOMAIN].get(_PANEL_REGISTERED) is True


async def test_options_update_reloads_into_fresh_pusher(hass):
    entry = await _setup(hass, {CONF_INTERVAL: 300})
    try:
        first = hass.data[DOMAIN][entry.entry_id]
        hass.config_entries.async_update_entry(entry, options={CONF_INTERVAL: 120})
        await hass.async_block_till_done()
        second = hass.data[DOMAIN][entry.entry_id]
        assert second is not first  # reloaded
        assert second.status()["interval"] == 120
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_remove_entry_unregisters_panel_when_last(hass):
    entry = await _setup(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN].get(_PANEL_REGISTERED) is False
