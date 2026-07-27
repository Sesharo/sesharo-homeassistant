# sesharo-homeassistant — Agent Context

A **Home Assistant custom integration** that pushes selected HA entity states into Sesharo. It's a
client of `sesharo-api` (same as the mobile/web/plugin frontends) — it creates **no HA entities**, it
only exports data. Cross-repo context lives in `~/dev/sesharo-meta`; the full design is
`sesharo-meta/docs/home-assistant-integration.md`.

## What it does

Every push interval (default 300s) it snapshots mapped **numeric** entities and drains buffered
**discrete state changes**, then POSTs both to:

```
POST {base_url}/users/{user_id}/home-assistant
Authorization: Bearer <Personal Access Token>
{ "readings": [...], "events": [...] }
```

Ingest is idempotent server-side (see the api-contract). **Auth = a Sesharo Personal Access Token**
(no OAuth) pasted into the config flow.

## Layout (`custom_components/sesharo/`)

| File | Role |
|---|---|
| `const.py` | `DOMAIN`, config keys, the preset catalog (`PRESET_METRICS` by `device_class`, `PRESET_EVENTS`, `PERSON_EVENT_CATEGORY`), defaults. |
| `api.py` | `SesharoClient` — async POST via HA's shared aiohttp session; `async_validate()` sends an empty batch; raises `SesharoAuthError` (401/403/404) vs `SesharoApiError`. |
| `coordinator.py` | `SesharoPusher` — maps entities (preset by `device_class` + custom), snapshots readings each interval, buffers events on `EVENT_STATE_CHANGED`, flushes in one POST (final flush on unload). `_to_canonical()` converts preset units (°F→°C, Wh→kWh, kW→W). |
| `config_flow.py` | Setup (base URL / user ID / PAT, validated) + Options (interval, presets toggle, add/remove custom `entity → signal` mappings). |
| `__init__.py` | `async_setup_entry` / `async_unload_entry`; reloads on options change. |
| `manifest.json` / `strings.json` / `translations/en.json` / `hacs.json` | HA + HACS metadata + UI strings. |

## Presets → Sesharo signals

Metrics (numeric, by `device_class`): `temperature→home_temperature` (°C), `humidity→home_humidity`
(%), `carbon_dioxide→home_co2` (ppm), `pm25→home_pm25` (µg/m³), `power→home_power` (W),
`energy→home_energy` (kWh). Events: occupancy/presence + `person.*` → `home_presence`; `motion` →
`home_motion`; `door`/`window` → `home_door`/`home_window`. Custom mappings send any entity → a chosen
signal slug (metric or event); custom metrics create a user-owned type in Sesharo.

## Conventions

- Values are sent in **canonical units** — convert in `_to_canonical()`, never rely on the backend.
- `signal`/`category` slugs must match `^[a-z0-9][a-z0-9_]{0,48}$` (the backend 422s otherwise).
- Add a preset by extending `PRESET_METRICS` / `PRESET_EVENTS` in `const.py` **and** seeding the
  matching metric type in `sesharo-api` (migration) if it's a metric.
- Keep the component dependency-free (`requirements: []`) — use HA's bundled aiohttp/voluptuous.

## Validation / status

Built 2026-07-26. **Syntax + JSON validated only.** Before shipping, run on a real HA instance:
`hassfest` validation, config-flow round-trip, and confirm data lands in Sesharo. HA is not installed
in the dev sandbox, so runtime behaviour (state-change capture, options reload) is **unverified**.
