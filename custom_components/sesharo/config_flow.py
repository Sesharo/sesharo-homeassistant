"""Config + options flows for the Sesharo integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SesharoApiError, SesharoAuthError, SesharoClient
from .const import (
    CONF_BASE_URL,
    CONF_CUSTOM,
    CONF_CUSTOM_ENTITY,
    CONF_CUSTOM_KIND,
    CONF_CUSTOM_NAME,
    CONF_CUSTOM_SIGNAL,
    CONF_CUSTOM_UNIT,
    CONF_INTERVAL,
    CONF_PRESETS_ENABLED,
    CONF_TOKEN,
    CONF_USER_ID,
    DEFAULT_BASE_URL,
    DEFAULT_INTERVAL,
    DOMAIN,
    KIND_EVENT,
    KIND_METRIC,
    MIN_INTERVAL,
)


async def _validate(hass, base_url: str, user_id: str, token: str) -> str | None:
    """Return an error key, or None if the connection works."""
    client = SesharoClient(async_get_clientsession(hass), base_url, user_id, token)
    try:
        await client.async_validate()
    except SesharoAuthError:
        return "invalid_auth"
    except SesharoApiError:
        return "cannot_connect"
    return None


class SesharoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_BASE_URL]}::{user_input[CONF_USER_ID]}")
            self._abort_if_unique_id_configured()
            error = await _validate(
                self.hass, user_input[CONF_BASE_URL], user_input[CONF_USER_ID], user_input[CONF_TOKEN]
            )
            if error is None:
                return self.async_create_entry(title="Sesharo", data=user_input)
            errors["base"] = error

        schema = vol.Schema({
            vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
            vol.Required(CONF_USER_ID): str,
            vol.Required(CONF_TOKEN): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "SesharoOptionsFlow":
        return SesharoOptionsFlow(config_entry)


class SesharoOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        options = dict(self._entry.options)
        custom: list[dict] = list(options.get(CONF_CUSTOM, []))

        if user_input is not None:
            new_options: dict[str, Any] = {
                CONF_INTERVAL: max(MIN_INTERVAL, int(user_input[CONF_INTERVAL])),
                CONF_PRESETS_ENABLED: user_input[CONF_PRESETS_ENABLED],
            }
            # Remove any selected existing mappings.
            to_remove = set(user_input.get("remove", []))
            custom = [c for c in custom if c[CONF_CUSTOM_ENTITY] not in to_remove]
            # Add a new mapping if an entity was supplied.
            new_entity = (user_input.get(CONF_CUSTOM_ENTITY) or "").strip()
            new_signal = (user_input.get(CONF_CUSTOM_SIGNAL) or "").strip()
            if new_entity and new_signal:
                custom = [c for c in custom if c[CONF_CUSTOM_ENTITY] != new_entity]
                custom.append({
                    CONF_CUSTOM_ENTITY: new_entity,
                    CONF_CUSTOM_SIGNAL: new_signal,
                    CONF_CUSTOM_KIND: user_input.get(CONF_CUSTOM_KIND, KIND_METRIC),
                    CONF_CUSTOM_UNIT: (user_input.get(CONF_CUSTOM_UNIT) or "").strip(),
                    CONF_CUSTOM_NAME: (user_input.get(CONF_CUSTOM_NAME) or "").strip(),
                })
            new_options[CONF_CUSTOM] = custom
            return self.async_create_entry(title="", data=new_options)

        existing_labels = {
            c[CONF_CUSTOM_ENTITY]: f"{c[CONF_CUSTOM_ENTITY]} → {c[CONF_CUSTOM_SIGNAL]} ({c[CONF_CUSTOM_KIND]})"
            for c in custom
        }
        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_INTERVAL, default=options.get(CONF_INTERVAL, DEFAULT_INTERVAL)): int,
            vol.Required(CONF_PRESETS_ENABLED, default=options.get(CONF_PRESETS_ENABLED, True)): bool,
        }
        # Only offer the removal picker when there is something to remove — a bare/empty
        # validator here can't be serialized to the frontend and 500s the options flow.
        if existing_labels:
            schema_dict[vol.Optional("remove", default=[])] = cv.multi_select(existing_labels)
        schema_dict.update({
            vol.Optional(CONF_CUSTOM_ENTITY, default=""): str,
            vol.Optional(CONF_CUSTOM_SIGNAL, default=""): str,
            vol.Optional(CONF_CUSTOM_KIND, default=KIND_METRIC): vol.In([KIND_METRIC, KIND_EVENT]),
            vol.Optional(CONF_CUSTOM_UNIT, default=""): str,
            vol.Optional(CONF_CUSTOM_NAME, default=""): str,
        })
        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"current": ", ".join(existing_labels.values()) or "none"},
        )
