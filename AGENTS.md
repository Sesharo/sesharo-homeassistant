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
| `const.py` | `DOMAIN`, config keys, the preset catalog (`PRESET_METRICS` by `device_class`, `PRESET_EVENTS`, `PERSON_EVENT_CATEGORY`), `PRESET_CATALOG` (panel row metadata: label + MDI icon per preset), per-preset opt-out key `CONF_PRESET_DISABLED`, per-**entity** preset opt-out key `CONF_PRESET_EXCLUDED` (keep a preset on, drop specific sensors), custom-mapping `CONF_CUSTOM_TARGET_UNIT`, panel constants, defaults. |
| `api.py` | `SesharoClient` — async POST via HA's shared aiohttp session; `async_validate()` sends an empty batch; `async_list_signals()` GETs `/signals` (existing metric types + event categories) for the panel picker; `base_url` property; raises `SesharoAuthError` (401/403/404) vs `SesharoApiError`. |
| `coordinator.py` | `SesharoPusher` — maps entities (preset by `device_class` + custom), snapshots readings each interval, buffers events on `EVENT_STATE_CHANGED`, flushes in one POST (final flush on unload). Honours the **per-preset opt-out** (`CONF_PRESET_DISABLED`) and the **per-entity preset opt-out** (`CONF_PRESET_EXCLUDED` — a preset-matched entity is skipped, metric or event, if listed). Tracks **push-health state** (`status()`: last/next push, failure streak, per-entity `last_sent`) read by the panel; `async_push_now()` flushes on demand (contacts the API even when empty); `async_list_signals()` proxies the picker fetch. Preset readings convert via `units.to_canonical`; a custom mapping that **joins an existing signal** carries `target_unit` and converts into it (or skips if inconvertible) so joined readings never store wrong numbers. |
| `units.py` | **Pure** unit conversion (no HA, unit-tested): `convert_units(value, from, to)` within a family (temperature/power/energy) returning `None` when inconvertible; `to_canonical(signal, …)` for presets; `fmt_value()`. |
| `discovery.py` | **Pure** entity→mapping smarts (no running HA needed, so unit-testable): `suggest_mapping()` derives a full candidate (slugified signal, metric/event kind, unit, display name) from one entity's state; `discover_candidates()` scans all states and returns the trackable ones **not** already covered by a preset or an existing custom mapping (signals de-duped, recognized-`device_class` ranked first). |
| `websocket_api.py` | The `sesharo/*` WebSocket commands that back the sidebar panel: reads `get_config` / `status` / `list_signals` / `suggestions`; admin-only writes `set_settings` / `set_presets` / `set_preset_excluded` (per-entity opt-out list) / `set_mappings` / `push_now`. Writes persist by updating the config entry's options (fires the reload listener). Registered once via `async_register(hass)`. |
| `panel.py` | Registers the `/sesharo` custom panel (`panel_custom.async_register_panel`) + serves the JS module from `www/` (`http.async_register_static_paths`) + registers the WS commands. Global/once (guarded in `hass.data`); torn down when the last entry unloads. |
| `www/sesharo-panel.js` | The panel itself — a **no-build ES module** (extends `HTMLElement`, composes HA's own `ha-card`/`ha-button`/`ha-switch`/`ha-icon`/`ha-entity-picker` + custom DOM for the signal-picker popover). Status card, mapping table w/ live last-sent, presets (master + per-preset switches, each expandable to a per-sensor include/exclude checklist), suggestions, inline add-row with the new/existing/presets signal picker (unit-match badge + inconvertible-mismatch guard), first-run empty state. HA theme tokens pierce the shadow DOM (light/dark free); Sesharo cobalt is a local brand var. `www/sesharo-mark-{light,dark}.svg` are the status/hero marks. |
| `config_flow.py` | Setup (base URL / user ID / PAT, validated) + a **menu-based Options flow** — now a **text-only fallback** to the panel (the panel is the primary surface). *Push settings* (interval/presets), ***Suggest entities to track*** (`discover` — a tick-box `cv.multi_select` of `discover_candidates()`; selected ones bulk-add with auto-derived signal/kind/unit), *Add a custom mapping* (**two steps**: pick entity via `EntitySelector`, then a form **pre-filled** from `suggest_mapping()`; slug validated against `_SLUG_RE`, replace-by-entity), *Remove custom mappings*, *Save & close*. Carries `CONF_PRESET_DISABLED` **and** `CONF_PRESET_EXCLUDED` through untouched so it never clobbers the panel's per-preset toggles or per-sensor exclusions. |
| `__init__.py` | `async_setup_entry` / `async_unload_entry`; reloads on options change; registers the panel (once) on setup and tears it down when the last entry unloads. |
| `manifest.json` / `strings.json` / `translations/en.json` / `hacs.json` | HA + HACS metadata + UI strings. `strings.json` is the source; keep `translations/en.json` an exact copy. |
| `images/logo.png` | Sesharo mark shown on the HACS page (embedded in README via a raw GitHub URL). |
| `brands/icon.png` (256×256) / `brands/logo.png` | Brand-ready assets to submit to `home-assistant/brands` → `custom_integrations/sesharo/` so the icon shows in HA's *Devices & Services* list (the only source HA reads icons from). |

## Presets → Sesharo signals

Metrics (numeric, by `device_class`): `temperature→home_temperature` (°C), `humidity→home_humidity`
(%), `carbon_dioxide→home_co2` (ppm), `pm25→home_pm25` (µg/m³), `power→home_power` (W),
`energy→home_energy` (kWh). Events: occupancy/presence + `person.*` → `home_presence`; `motion` →
`home_motion`; `door`/`window` → `home_door`/`home_window`. Custom mappings send any entity → a chosen
signal slug (metric or event); custom metrics create a user-owned type in Sesharo.

## Sidebar panel (`/sesharo`)

HA's config/options flow can only render one form at a time, so a mapping **table**, live status and
an inline add-row aren't possible there. The panel replaces that: a custom sidebar panel registered
from `panel.py`, served as a JS module out of `www/`, talking to the `sesharo/*` WebSocket commands
in `websocket_api.py` (never to `sesharo-api` directly — the PAT stays server-side).

- **No build step.** `www/sesharo-panel.js` is a plain ES module that composes HA's own registered
  elements rather than bundling Lit/TS (this is a Python HACS repo with no JS toolchain). It
  re-renders on WS-driven state changes and, throttled, on `hass` updates for live values — but
  **never mid-edit**, so the entity/signal pickers keep focus. Deviations from the design handoff:
  the signal-picker popover is custom DOM (not `ha-combo-box` with a renderer) and icons use
  `<ha-icon icon="mdi:…">` (not `@mdi/js` path imports) — both to stay build-free. Behaviour, copy
  and tokens follow the handoff.
- **WebSocket surface** (`hass.callWS({type})`): `sesharo/get_config`, `sesharo/status`,
  `sesharo/list_signals`, `sesharo/suggestions` (reads); `sesharo/set_settings`,
  `sesharo/set_presets`, `sesharo/set_mappings`, `sesharo/push_now` (admin-only writes). Writes call
  `async_update_entry(options=…)` → the update listener reloads the entry → a fresh `SesharoPusher`.
- **Existing-signal picker** needs `GET /users/{id}/home-assistant/signals` in `sesharo-api`
  (metric types + event categories with reading/entry counts, unit, sources, first/last seen). The
  panel merges both into the "Already in your Sesharo" group and shows a **unit-matches** badge when
  an existing signal's unit equals the entity's; joining a mismatched unit is converted (if the
  family is known) or blocked.
- **Per-preset toggles**: the master `presets_enabled` switch plus a per-`device_class` opt-out set
  (`CONF_PRESET_DISABLED`). A preset is active iff the master is on and its class isn't opted out.
- **Per-sensor exclusions**: each enabled preset with >1 matched sensor expands to a checklist of its
  entities (all on by default). Unchecking one adds its `entity_id` to `CONF_PRESET_EXCLUDED` (via
  `set_preset_excluded`); the pusher then skips just that entity while the preset keeps sending the
  rest. The row shows "N of M sensors" when some are excluded.

## Conventions

- Values are sent in **canonical units** — convert in `units.to_canonical()` (presets) or via the
  mapping's `target_unit` (custom joins), never rely on the backend.
- `signal`/`category` slugs must match `^[a-z0-9][a-z0-9_]{0,48}$` (the backend 422s otherwise).
- Add a preset by extending `PRESET_METRICS` / `PRESET_EVENTS` in `const.py` **and** seeding the
  matching metric type in `sesharo-api` (migration) if it's a metric.
- Keep the runtime lean — use HA's bundled aiohttp/voluptuous. The only declared
  requirement is `sentry-sdk` (error reporting, see below), which stays dormant unless
  a DSN env var is set; don't add others without a strong reason.

## Error reporting (Sentry)

`sentry.py` provides **opt-in, off-by-default** crash reporting. Because the component
runs inside end users' Home Assistant hosts, `init_sentry()` is a **no-op unless the
`SESHARO_SENTRY_DSN` environment variable is set** on the host (mirrors the env-gated
guard in `sesharo-api/app/telemetry.py`). It's called once from `async_setup_entry` via
an executor (sentry-sdk init is blocking).

Even when enabled, a `before_send` filter drops every event that does **not** originate
from `custom_components.sesharo` (checked against the logger name and stack-frame
modules), so we never capture the user's unrelated HA errors. `send_default_pii=False`;
no trace sampling. Optional `SESHARO_SENTRY_ENVIRONMENT` overrides the environment tag
(default `production`). `sentry-sdk` is pinned in `manifest.json` (`==2.63.0`, matching
the backend) so HACS installs it; with no DSN set it just sits idle. Sentry project:
`sesharo-homeassistant` (org `sesharo`).

## Tests

`tests/test_discovery.py` covers the pure `discovery.py` logic (slugify, `suggest_mapping`,
`discover_candidates` — preset exclusion, already-mapped skip, signal de-dup, ranking).
`tests/test_units.py` covers `units.py` (temperature/power/energy conversion, inconvertible→`None`,
preset `to_canonical`, `fmt_value`). HA isn't installed in this repo, so `tests/conftest.py` injects a
minimal `homeassistant.const` stub + bare `custom_components.sesharo` package objects (bypassing
`__init__`, which imports the full HA runtime). Each runs two ways: `python3 tests/test_*.py` (no
deps) or `pytest tests/`. **Off-device only** (needs an on-device / `pytest-homeassistant-custom-component`
run): the config-flow UI, the **panel** (`www/sesharo-panel.js` — WS round-trip, picker, live values),
the `websocket_api.py` command handlers, `panel_custom` registration + static serving, and `hassfest`.

## Releasing / HACS updates

HACS always **re-downloads the whole `custom_components/sesharo/`** — there's no patch/delta. The
"proper" update flow (an *Update available* card in *Settings → Updates* with a version + changelog,
not a silent branch re-pull) requires **GitHub Releases with semver tags**: with no releases HACS
tracks the default branch and every update is just a branch re-download. To ship a version:

1. Bump `manifest.json` `version` (HACS/HA show it as the installed version).
2. Add a `## vX.Y.Z` section to `CHANGELOG.md` (this is the release body / HACS changelog).
3. `make release` (from `main`, clean tree) — extracts that CHANGELOG section and runs
   `gh release create vX.Y.Z`. HACS auto-switches to release-tracking once releases exist; no
   `zip_release`/`filename` needed in `hacs.json` (source download is fine).
4. A HA **restart is still required** after update — Python code only loads on restart (HACS prompts).

`make` targets: `test` (off-device unit + discovery tests), `check` (py_compile + `node --check` +
JSON/translations sync), `release` (runs `check` + `test` first). Run `make check test` before a PR.

The panel's `module_url` is cache-busted with `?v=<manifest version>` (see `panel.py`) so the new JS
loads after an update without a manual hard refresh — **bump the version on any `www/` change**.

## Validation / status

Built 2026-07-26. Live on a real HA instance since 2026-07-27 — metrics confirmed landing in Sesharo
(temperature/humidity/CO₂/power/energy). **2026-07-27 fixes:** the options flow 500'd on open (a bare
`vol.All(list)` for the removal picker couldn't be serialized when no custom mappings existed) — fixed
and rebuilt as the menu flow above; its state machine (menu loop, add/replace/validate, remove, finish,
empty-remove bounce) is verified off-device via a stubbed-HA harness. **2026-07-27 smarter mapping:**
added `discovery.py` + the *Suggest entities* discover step and the two-step pre-filled *Add a custom
mapping* flow (16 discovery tests green off-device). Still **unverified on-device:** `hassfest`, the
options round-trip in the real UI (incl. the new discover/configure steps), `EntitySelector` rendering,
and event capture. Note HA reads the packaged copy, not `~/dev` — update via HACS (or copy files over)
+ restart to pick up changes.

**2026-08-04 panel redesign (`v0.2.0`).** Replaced the menu-only UI with a full `/sesharo` sidebar
panel (`www/sesharo-panel.js`) fed by the `sesharo/*` WebSocket commands (`websocket_api.py`),
registered from `panel.py`. Added push-health status + `push_now`, per-preset toggles
(`CONF_PRESET_DISABLED`), a custom-join `target_unit` conversion path, and `units.py` (extracted +
unit-tested). Requires the new `sesharo-api` `GET …/home-assistant/signals` endpoint (shipped
alongside) for the existing-signal picker. **28 off-device tests green** (12 units + 16 discovery),
py_compile + `node --check` clean. **Unverified on-device:** everything panel/WS/`panel_custom`/
`hassfest` (see Tests). The old menu flow stays as a text-only fallback. Update via HACS + restart.

**2026-08-05 per-sensor preset exclusions (`v0.3.0`).** Presets matched by `device_class` sent *every*
matching entity; now a user can keep a preset on and drop specific sensors. New `CONF_PRESET_EXCLUDED`
option (list of `entity_id`s) honoured by the pusher for both metric and event presets; new admin
`sesharo/set_preset_excluded` WS command; the panel's *Presets* card expands each enabled multi-sensor
preset into a per-sensor include/exclude checklist. Config-flow fallback carries the key through.
28 off-device tests still green (the logic lives in the HA-runtime coordinator, so it's covered by the
on-device caveat like `CONF_PRESET_DISABLED`). Update via HACS + restart.
