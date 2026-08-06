"""Config + options flows for the Sesharo integration."""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SesharoApiError, SesharoAuthError, SesharoClient
from .discovery import discover_candidates, suggest_mapping
from .const import (
    CONF_BASE_URL,
    CONF_CUSTOM,
    CONF_CUSTOM_ENTITY,
    CONF_CUSTOM_KIND,
    CONF_CUSTOM_NAME,
    CONF_CUSTOM_SIGNAL,
    CONF_CUSTOM_UNIT,
    CONF_INTERVAL,
    CONF_PRESET_DISABLED,
    CONF_PRESET_EXCLUDED,
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


# Mirrors the backend signal/category slug rule (app/schemas/home_assistant.py): a lowercase
# slug, 1–49 chars. Validated here so a bad slug is caught before it 422s on the first push.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,48}$")


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
    """A looping menu: adjust settings, add/remove any number of custom mappings, then save.

    HA can only render one form at a time, so a full mapping *table* isn't possible in a single
    screen. Instead each action returns to the menu, letting you add as many mappings as you like
    in one session; nothing persists until you pick "Save & close".
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        # Working copy accumulated across sub-steps; only written on the finish step.
        opts = config_entry.options
        self._options: dict[str, Any] = {
            CONF_INTERVAL: opts.get(CONF_INTERVAL, DEFAULT_INTERVAL),
            CONF_PRESETS_ENABLED: opts.get(CONF_PRESETS_ENABLED, True),
            # Carried through untouched — the per-preset toggles and per-entity exclusions are
            # managed by the panel, and the old menu flow must not silently drop them on save.
            CONF_PRESET_DISABLED: list(opts.get(CONF_PRESET_DISABLED, []) or []),
            CONF_PRESET_EXCLUDED: list(opts.get(CONF_PRESET_EXCLUDED, []) or []),
            CONF_CUSTOM: [dict(c) for c in opts.get(CONF_CUSTOM, [])],
        }
        # The entity picked in add_mapping step 1, carried into the pre-filled step 2.
        self._pending_entity: str | None = None

    # ── helpers ───────────────────────────────────────────────────────────
    def _add_mapping(self, entity: str, signal: str, kind: str, unit: str, name: str) -> None:
        """Replace any existing mapping for ``entity`` and append the new one."""
        custom = [c for c in self._options[CONF_CUSTOM] if c[CONF_CUSTOM_ENTITY] != entity]
        custom.append({
            CONF_CUSTOM_ENTITY: entity,
            CONF_CUSTOM_SIGNAL: signal,
            CONF_CUSTOM_KIND: kind,
            CONF_CUSTOM_UNIT: unit,
            CONF_CUSTOM_NAME: name,
        })
        self._options[CONF_CUSTOM] = custom

    def _mapped_entities(self) -> set[str]:
        return {c[CONF_CUSTOM_ENTITY] for c in self._options[CONF_CUSTOM]}

    def _mapped_signals(self) -> set[str]:
        return {c[CONF_CUSTOM_SIGNAL] for c in self._options[CONF_CUSTOM]}

    def _summary(self) -> str:
        custom = self._options.get(CONF_CUSTOM, [])
        if not custom:
            return "none"
        return "\n".join(
            f"• {c[CONF_CUSTOM_ENTITY]} → {c[CONF_CUSTOM_SIGNAL]} ({c[CONF_CUSTOM_KIND]})"
            for c in custom
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "discover", "add_mapping", "remove_mappings", "finish"],
            description_placeholders={"current": self._summary()},
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._options[CONF_INTERVAL] = max(MIN_INTERVAL, int(user_input[CONF_INTERVAL]))
            self._options[CONF_PRESETS_ENABLED] = user_input[CONF_PRESETS_ENABLED]
            return await self.async_step_init()
        schema = vol.Schema({
            vol.Required(CONF_INTERVAL, default=self._options[CONF_INTERVAL]): int,
            vol.Required(CONF_PRESETS_ENABLED, default=self._options[CONF_PRESETS_ENABLED]): bool,
        })
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_discover(self, user_input: dict[str, Any] | None = None):
        """Scan HA for trackable entities not already covered, and bulk-add the chosen ones."""
        candidates = discover_candidates(
            self.hass.states.async_all(),
            self._mapped_entities(),
            self._mapped_signals(),
            presets_enabled=self._options[CONF_PRESETS_ENABLED],
        )
        if not candidates:  # nothing to suggest — bounce back with a note
            return await self.async_step_init()
        by_entity = {c[CONF_CUSTOM_ENTITY]: c for c in candidates}
        if user_input is not None:
            for entity in user_input.get("add", []):
                c = by_entity.get(entity)
                if c is not None:
                    self._add_mapping(
                        entity, c[CONF_CUSTOM_SIGNAL], c[CONF_CUSTOM_KIND],
                        c[CONF_CUSTOM_UNIT], c[CONF_CUSTOM_NAME],
                    )
            return await self.async_step_init()
        labels = {
            c[CONF_CUSTOM_ENTITY]:
                f"{c[CONF_CUSTOM_ENTITY]} → {c[CONF_CUSTOM_SIGNAL]} ({c[CONF_CUSTOM_KIND]})"
            for c in candidates
        }
        schema = vol.Schema({vol.Required("add", default=[]): cv.multi_select(labels)})
        return self.async_show_form(step_id="discover", data_schema=schema)

    async def async_step_add_mapping(self, user_input: dict[str, Any] | None = None):
        """Step 1 of adding a mapping: pick the entity. Step 2 pre-fills from it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity = (user_input.get(CONF_CUSTOM_ENTITY) or "").strip()
            if not entity:
                errors[CONF_CUSTOM_ENTITY] = "entity_required"
            else:
                self._pending_entity = entity
                return await self.async_step_configure_mapping()
        schema = vol.Schema({vol.Required(CONF_CUSTOM_ENTITY): selector.EntitySelector()})
        return self.async_show_form(step_id="add_mapping", data_schema=schema, errors=errors)

    async def async_step_configure_mapping(self, user_input: dict[str, Any] | None = None):
        """Step 2: signal/kind/unit/name pre-filled from the picked entity; user confirms or tweaks."""
        entity = self._pending_entity
        if entity is None:  # defensive — shouldn't be reachable without step 1
            return await self.async_step_add_mapping()
        errors: dict[str, str] = {}
        if user_input is not None:
            signal = (user_input.get(CONF_CUSTOM_SIGNAL) or "").strip().lower()
            if not _SLUG_RE.match(signal):
                errors[CONF_CUSTOM_SIGNAL] = "invalid_signal"
            if not errors:
                self._add_mapping(
                    entity, signal,
                    user_input.get(CONF_CUSTOM_KIND, KIND_METRIC),
                    (user_input.get(CONF_CUSTOM_UNIT) or "").strip(),
                    (user_input.get(CONF_CUSTOM_NAME) or "").strip(),
                )
                self._pending_entity = None
                return await self.async_step_init()

        # Derive defaults from the live entity state (falls back to bare guesses if it's gone).
        state = self.hass.states.get(entity)
        suggestion = suggest_mapping(entity, state) if state is not None else None
        defaults = suggestion or {
            CONF_CUSTOM_SIGNAL: "", CONF_CUSTOM_KIND: KIND_METRIC,
            CONF_CUSTOM_UNIT: "", CONF_CUSTOM_NAME: "",
        }
        # If the user just failed validation, keep what they typed.
        if user_input is not None:
            defaults = {**defaults, **{k: v for k, v in user_input.items()}}
        schema = vol.Schema({
            vol.Required(CONF_CUSTOM_SIGNAL, default=defaults[CONF_CUSTOM_SIGNAL]): str,
            vol.Required(CONF_CUSTOM_KIND, default=defaults[CONF_CUSTOM_KIND]): selector.SelectSelector(
                selector.SelectSelectorConfig(options=[KIND_METRIC, KIND_EVENT])
            ),
            vol.Optional(CONF_CUSTOM_UNIT, default=defaults[CONF_CUSTOM_UNIT]): str,
            vol.Optional(CONF_CUSTOM_NAME, default=defaults[CONF_CUSTOM_NAME]): str,
        })
        return self.async_show_form(
            step_id="configure_mapping", data_schema=schema, errors=errors,
            description_placeholders={"entity": entity},
        )

    async def async_step_remove_mappings(self, user_input: dict[str, Any] | None = None):
        custom = self._options[CONF_CUSTOM]
        if not custom:  # nothing to remove — bounce back to the menu
            return await self.async_step_init()
        if user_input is not None:
            to_remove = set(user_input.get("remove", []))
            self._options[CONF_CUSTOM] = [c for c in custom if c[CONF_CUSTOM_ENTITY] not in to_remove]
            return await self.async_step_init()
        labels = {
            c[CONF_CUSTOM_ENTITY]: f"{c[CONF_CUSTOM_ENTITY]} → {c[CONF_CUSTOM_SIGNAL]} ({c[CONF_CUSTOM_KIND]})"
            for c in custom
        }
        schema = vol.Schema({vol.Required("remove", default=[]): cv.multi_select(labels)})
        return self.async_show_form(step_id="remove_mappings", data_schema=schema)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None):
        return self.async_create_entry(title="", data=self._options)
