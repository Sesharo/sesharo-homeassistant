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
| `config_flow.py` | Setup (base URL / user ID / PAT, validated) + a **menu-based Options flow** (`async_show_menu`): *Push settings* (interval/presets), *Add a custom mapping* (loops back to the menu, so any number of mappings can be added in one session; entity picked via `EntitySelector`, slug validated against `_SLUG_RE`, replace-by-entity), *Remove custom mappings* (`cv.multi_select`), *Save & close* (only `async_step_finish` persists — the working copy `self._options` accumulates across sub-steps). |
| `__init__.py` | `async_setup_entry` / `async_unload_entry`; reloads on options change. |
| `manifest.json` / `strings.json` / `translations/en.json` / `hacs.json` | HA + HACS metadata + UI strings. `strings.json` is the source; keep `translations/en.json` an exact copy. |
| `images/logo.png` | Sesharo mark shown on the HACS page (embedded in README via a raw GitHub URL). |
| `brands/icon.png` (256×256) / `brands/logo.png` | Brand-ready assets to submit to `home-assistant/brands` → `custom_integrations/sesharo/` so the icon shows in HA's *Devices & Services* list (the only source HA reads icons from). |

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

Built 2026-07-26. Live on a real HA instance since 2026-07-27 — metrics confirmed landing in Sesharo
(temperature/humidity/CO₂/power/energy). **2026-07-27 fixes:** the options flow 500'd on open (a bare
`vol.All(list)` for the removal picker couldn't be serialized when no custom mappings existed) — fixed
and rebuilt as the menu flow above; its state machine (menu loop, add/replace/validate, remove, finish,
empty-remove bounce) is verified off-device via a stubbed-HA harness. Still **unverified on-device:**
`hassfest`, the options round-trip in the real UI, `EntitySelector` rendering, and event capture. Note
HA reads the packaged copy, not `~/dev` — update via HACS (or copy files over) + restart to pick up changes.
