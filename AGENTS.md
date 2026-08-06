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
| `const.py` | `DOMAIN`, config keys, the preset catalog (`PRESET_METRICS` by `device_class`, `PRESET_EVENTS`, `PERSON_EVENT_CATEGORY`), `PRESET_CATALOG` (panel row metadata: label + MDI icon per preset), per-preset opt-out key `CONF_PRESET_DISABLED`, per-**entity** preset opt-out key `CONF_PRESET_EXCLUDED` (keep a preset on, drop specific sensors), per-**signal** entity-cap key `CONF_PRESET_CAPS` + `DEFAULT_PRESET_ENTITY_CAP` (bound how many entities feed one slug), custom-mapping `CONF_CUSTOM_TARGET_UNIT`, panel constants, defaults. |
| `api.py` | `SesharoClient` — async POST via HA's shared aiohttp session; `async_validate()` sends an empty batch; `async_list_signals()` GETs `/signals` (existing metric types + event categories) for the panel picker; `base_url` property; raises `SesharoAuthError` (401/403/404) vs `SesharoApiError`. |
| `coordinator.py` | `SesharoPusher` — maps entities (preset by `device_class` + custom), snapshots readings each interval, buffers events on `EVENT_STATE_CHANGED`, flushes in one POST (final flush on unload). Honours the **per-preset opt-out** (`CONF_PRESET_DISABLED`), the **per-entity preset opt-out** (`CONF_PRESET_EXCLUDED` — a preset-matched entity is skipped, metric or event, if listed), and the **per-signal entity cap** (`CONF_PRESET_CAPS` — after exclusions, keep only the lowest-`entity_id` N feeding each signal; `_cap_for`/`_cap_allow_set`). Tracks **push-health state** (`status()`: last/next push, failure streak, per-entity `last_sent`) read by the panel; `async_push_now()` flushes on demand (contacts the API even when empty); `async_list_signals()` proxies the picker fetch. Preset readings convert via `units.to_canonical`; a custom mapping that **joins an existing signal** carries `target_unit` and converts into it (or skips if inconvertible) so joined readings never store wrong numbers. |
| `units.py` | **Pure** unit conversion (no HA, unit-tested): `convert_units(value, from, to)` within a family (temperature/power/energy) returning `None` when inconvertible; `to_canonical(signal, …)` for presets; `fmt_value()`. |
| `discovery.py` | **Pure** entity→mapping smarts (no running HA needed, so unit-testable): `suggest_mapping()` derives a full candidate (slugified signal, metric/event kind, unit, display name) from one entity's state; `discover_candidates()` scans all states and returns the trackable ones **not** already covered by a preset or an existing custom mapping (signals de-duped, recognized-`device_class` ranked first). |
| `websocket_api.py` | The `sesharo/*` WebSocket commands that back the sidebar panel: reads `get_config` / `status` / `list_signals` / `suggestions`; admin-only writes `set_settings` / `set_presets` / `set_preset_excluded` (per-entity opt-out list) / `set_preset_cap` (per-signal entity cap; `get_config` also echoes `preset_caps` + `default_cap`) / `set_mappings` / `push_now`. Writes persist by updating the config entry's options (fires the reload listener). Registered once via `async_register(hass)`. |
| `panel.py` | Registers the `/sesharo` custom panel (`panel_custom.async_register_panel`) + serves the JS module from `www/` (`http.async_register_static_paths`) + registers the WS commands. Global/once (guarded in `hass.data`); torn down when the last entry unloads. |
| `www/sesharo-panel.js` | The panel itself — a **no-build ES module** (extends `HTMLElement`, composes HA's own `ha-card`/`ha-button`/`ha-switch`/`ha-icon`/`ha-entity-picker` + custom DOM for the signal-picker popover). Status card, mapping table w/ live last-sent, presets (master + per-preset switches, each expandable to a per-sensor include/exclude checklist with a "Send up to N" cap input, Select all/none, and a name/entity_id filter), suggestions, inline add-row with the new/existing/presets signal picker (unit-match badge + inconvertible-mismatch guard), first-run empty state. HA theme tokens pierce the shadow DOM (light/dark free); Sesharo cobalt is a local brand var. `www/sesharo-mark-{light,dark}.svg` are the status/hero marks. |
| `config_flow.py` | Setup (base URL / user ID / PAT, validated) + a **menu-based Options flow** — now a **text-only fallback** to the panel (the panel is the primary surface). *Push settings* (interval/presets), ***Suggest entities to track*** (`discover` — a tick-box `cv.multi_select` of `discover_candidates()`; selected ones bulk-add with auto-derived signal/kind/unit), *Add a custom mapping* (**two steps**: pick entity via `EntitySelector`, then a form **pre-filled** from `suggest_mapping()`; slug validated against `_SLUG_RE`, replace-by-entity), *Remove custom mappings*, *Save & close*. Carries `CONF_PRESET_DISABLED`, `CONF_PRESET_EXCLUDED` **and** `CONF_PRESET_CAPS` through untouched so it never clobbers the panel's per-preset toggles, per-sensor exclusions, or per-signal caps. |
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
  rest. The expanded list also has **Select all / Select none** and a **name/entity_id filter** so
  trimming a large class isn't box-by-box. The row shows "N of M sensors" when some are dropped.
- **Per-signal entity caps**: a curated preset can match dozens of sensors that all funnel into one
  slug (40 `power` sensors → `home_power`). `CONF_PRESET_CAPS` (`{signal -> max}`, via
  `set_preset_cap`) bounds how many actually push; absent → `DEFAULT_PRESET_ENTITY_CAP` (10), `<=0` →
  no limit. The pusher applies it **after** exclusions (opt-outs never consume a slot) and keeps the
  **lowest `entity_id`s** so the panel (which mirrors that ordering) and the pusher send the same
  ones; over-cap rows show "over limit". Applies to metric *and* event presets. Custom mappings are
  never capped. **Behaviour note:** the default cap means a fresh install stops at 10 sensors/signal
  by default — raise the per-signal cap in the panel to send more.

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

## Tests, linting, CI

**Two test tiers, one harness (`tests/conftest.py` auto-detects which):**

1. **Dependency-free smoke** (`make smoke` / `python3 tests/test_units.py` / `…test_discovery.py`):
   the pure modules `units.py` + `discovery.py`. When a real HA *isn't* importable, `conftest`
   injects a minimal `homeassistant.const` stub + bare `custom_components.sesharo` package objects
   (bypassing `__init__`). No install needed — fast gate, also what `make release` runs.
2. **Full pytest suite** (`make test`, needs `pip install -r requirements_test.txt`): runs against a
   **real** Home Assistant via **`pytest-homeassistant-custom-component`** (pHACC). When real HA is
   present, `conftest`'s stub injection is a no-op. Covers the previously-untested runtime modules:
   - `test_api.py` — status→exception mapping (401/403/404→auth, others→api) + **transport/timeout
     wrapping** (regression for the `asyncio.TimeoutError` escape).
   - `test_coordinator.py` — preset conversion/gating, per-class + per-entity opt-out, custom
     net-new vs `target_unit` join (incl. inconvertible-skip), event buffering, **requeue-on-failure**,
     push-health `status()`, manual-push-when-empty.
   - `test_websocket_api.py` — every `sesharo/*` command via a real WS client: reads, admin-gated
     writes, slug validation, mapping de-dup, `push_now`, and the **require_admin rejection**.
   - `test_config_flow.py` — setup happy path + `invalid_auth`/`cannot_connect`/duplicate-abort;
     options menu (settings round-trip, two-step add-mapping, bad-slug, remove, panel-key carry).
   - `test_init.py` — setup→pusher in `hass.data`, options-change reload, unload, panel teardown on
     permanent removal.
   - `test_sentry.py` — the `_before_send`/`_event_is_ours` filter + env-gated no-op.

   ~98 tests, ~92% line coverage (`make coverage`). CI enforces `--cov-fail-under=85`.

   > **pHACC needs the frontend wheel.** The integration hard-depends on `frontend`/`panel_custom`,
   > so setting an entry up in tests requires `home-assistant-frontend` (provides `hass_frontend`),
   > which pHACC omits. It's pinned in `requirements_test.txt` to the version HA 2026.2.x declares —
   > bump it in lockstep when you bump the `pytest-homeassistant-custom-component` pin.

**Lint/format:** `ruff` (config in `pyproject.toml`). `make lint` (check) / `make format` (apply).
Line width is owned by `ruff format` (E501 disabled in the linter, matching HA core). `.pre-commit-config.yaml`
wires ruff + the translations-sync guard — `pre-commit install` once.

**CI** (`.github/workflows/ci.yml`, on push/PR): ruff lint + format-check, pytest+coverage, HA
**`hassfest`** (manifest/deps/translations), and **HACS validation**. Sesharo commits straight to
`main`, so CI runs on `main` too — a green pipeline gates a manual `make release`.

**Still on-device only** (pHACC can't cover): the **panel** `www/sesharo-panel.js` browser behaviour
(WS round-trip in a real browser, picker popover, live `hass` values, theme) — `node --check` is the
only automated guard (`make check`).

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

`make` targets: `test` (full pytest suite), `smoke` (dependency-free units+discovery), `lint`/`format`
(ruff), `coverage`, `check` (py_compile + `node --check` + JSON/translations sync), `install-dev`
(venv + dev deps), `release` (runs `check` + `smoke` first — the no-dep gates; the full suite + lint
run in CI). Run `make lint test check` before merging.

The panel's `module_url` is cache-busted with `?v=<manifest version>` (see `panel.py`) so the new JS
loads after an update without a manual hard refresh — **bump the version on any `www/` change**.

## HACS default-store submission

The repo is being prepped for inclusion in the **HACS default store** (so users can install it by
name instead of adding a custom repository). Current state against the
[HACS inclusion requirements](https://hacs.xyz/docs/publish/include):

- ✅ **Public repo** with a **description** and **topics** (`home-assistant`, `hacs`, … — set on the
  GitHub repo, not in-tree) and a **homepage** (`https://sesharo.com`).
- ✅ **`LICENSE`** (MIT) in the repo root.
- ✅ **Releases** exist with semver tags (`make release`); HACS tracks releases.
- ✅ **`hacs.json`** valid (`render_readme: true` → the README is the HACS description; no `info.md`
  needed) and **`hassfest`** + **HACS validation** pass in CI (`.github/workflows/ci.yml`).
- ⏳ **Brands** — the one remaining step. The domain must exist in
  [`home-assistant/brands`](https://github.com/home-assistant/brands) under
  `custom_integrations/sesharo/`. Assets are staged in `brands/` (`icon.png` is 256×256; `logo.png`
  is identical, so the brands PR should submit **only `icon.png`** — brands treats a same-as-icon
  logo as unnecessary). Open a PR adding `custom_integrations/sesharo/icon.png` (+ optional
  `icon@2x.png` 512×512). Until it merges, the HACS CI job keeps `ignore: brands`; **drop that once
  the brands PR merges**, then the actual submission is opening an issue on `hacs/default`.

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

**2026-08-05 stability hardening (`v0.3.1`).** Closed the "unverified on-device" gap for the *logic*
(the browser panel is still on-device-only). Adopted **`pytest-homeassistant-custom-component`** and
wrote real tests for every previously-untested runtime module — coordinator, api, websocket_api,
config_flow, `__init__` lifecycle, sentry filter — against a real `hass`/`MockConfigEntry`/mocked
aiohttp. **~98 tests, ~92% coverage.** Added **ruff** (lint+format), a **GitHub Actions CI** (ruff +
pytest/coverage + `hassfest` + HACS validation), and **pre-commit**. **Runtime fix:** wrapped
`asyncio.TimeoutError` in `api.py` (a push timeout used to escape unhandled and skip the event
requeue). The dependency-free `units`/`discovery` smoke runners still work (`make smoke`). See the
*Tests, linting, CI* section. Everything runs green off-device in CI; the panel's in-browser behaviour
remains the one on-device caveat. Update via HACS + restart.

**2026-08-05 per-signal entity caps + bulk trimming (`v0.4.0`).** A curated preset can match dozens
of sensors that all funnel into one Sesharo slug (40 `power` → `home_power`), which floods a signal.
New `CONF_PRESET_CAPS` (`{signal -> max}`, admin WS `sesharo/set_preset_cap`; `get_config` echoes
`preset_caps` + `default_cap`) bounds how many entities feed each slug — absent → the new
`DEFAULT_PRESET_ENTITY_CAP` (**10**), `<=0` → no limit. The pusher applies it after exclusions (opt-
outs never consume a slot) and keeps the **lowest `entity_id`s** so the panel and pusher agree on
which send; covers metric *and* event presets, customs never capped (`_cap_for` / `_cap_allow_set` /
`_preset_metric_allow` / `_preset_event_ids`). The panel's expanded preset gained a "Send up to N"
cap input, **Select all / Select none**, a **name/entity_id filter** (for hand-picking at scale), and
"over limit" badges on capped-out rows. Config-flow fallback carries the key through. **Behaviour
change:** existing installs that sent every matching sensor now stop at 10 per signal until the cap is
raised. **107 tests green** (added coordinator cap cases — metric/event/default/unlimited/exclude-
first/per-signal — plus WS `set_preset_cap` + config-flow carry), ruff clean, `node --check` clean.
On-device panel behaviour is still the standing caveat. Update via HACS + restart.
