"""Registers the Sesharo sidebar panel (`/sesharo`) and serves its JS module.

A custom panel is a web-component registered via ``panel_custom.async_register_panel``, loaded as an
ES module from a static path we serve out of the integration's ``www/`` directory. Registration is
**global, not per-entry** — done once on the first entry setup and torn down when the last entry
unloads — so we guard it with a flag in ``hass.data[DOMAIN]``.
"""
from __future__ import annotations

import logging
import os

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import websocket_api
from .const import (
    DOMAIN,
    PANEL_ICON,
    PANEL_JS_FILENAME,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
    PANEL_WEBCOMPONENT,
)

_LOGGER = logging.getLogger(__name__)

# All three guards are set once and never reset on entry *unload* — options changes reload the entry
# (unload → setup) constantly, and re-registering a static path or panel raises on the duplicate.
# The panel guard is only cleared on permanent removal (async_unregister_panel, from
# async_remove_entry) so a later re-add re-registers it; the static path + WS commands are HA-lifetime
# and stay put (there's no public unregister for a static path anyway).
_STATIC_REGISTERED = "_static_registered"
_PANEL_REGISTERED = "_panel_registered"
_WS_REGISTERED = "_ws_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the JS module, register the panel + the WebSocket commands. Idempotent across reloads."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if not domain_data.get(_WS_REGISTERED):
        websocket_api.async_register(hass)
        domain_data[_WS_REGISTERED] = True

    if not domain_data.get(_STATIC_REGISTERED):
        www_dir = os.path.join(os.path.dirname(__file__), "www")
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, www_dir, cache_headers=False)]
        )
        domain_data[_STATIC_REGISTERED] = True

    if domain_data.get(_PANEL_REGISTERED):
        return

    # Cache-bust the ES module by the integration version so a HACS update actually reloads the
    # panel — browsers cache module URLs, and our static path is served without cache headers but the
    # URL is stable, so without this the old panel keeps showing until a manual hard refresh.
    integration = await async_get_integration(hass, DOMAIN)
    module_url = f"{PANEL_STATIC_URL}/{PANEL_JS_FILENAME}?v={integration.version}"

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_WEBCOMPONENT,
        module_url=module_url,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        require_admin=True,
        config={},
        embed_iframe=False,
        trust_external=False,
    )
    domain_data[_PANEL_REGISTERED] = True
    _LOGGER.debug("Registered Sesharo panel at /%s", PANEL_URL_PATH)


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the panel (WS commands + static path stay — they're harmless and hard to unwind)."""
    domain_data = hass.data.get(DOMAIN, {})
    if domain_data.get(_PANEL_REGISTERED):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        domain_data[_PANEL_REGISTERED] = False
