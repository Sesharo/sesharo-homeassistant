# Changelog

All notable changes to the Sesharo Home Assistant integration. Versions match `manifest.json`; the
section for the current version is what `make release` publishes to GitHub Releases (and HACS renders
as the update changelog).

## v0.3.1 — Reliability: survive API timeouts, plus a real test + CI suite

A maintenance release focused on stability. No user-facing feature changes.

### Fixed

- **Push timeouts no longer crash the push cycle.** A request that exceeded the 30s timeout raised
  `asyncio.TimeoutError`, which isn't an `aiohttp.ClientError` — so it escaped the client as an
  unhandled exception in the interval callback, and buffered events were **not** requeued for retry.
  Timeouts are now wrapped like any other transport failure, so a slow/unreachable Sesharo API just
  fails that push and retries next interval (events preserved).

### Developer / project health (no runtime impact)

- **Real off-device test suite.** Added `pytest` + `pytest-homeassistant-custom-component` tests for
  the previously-untested runtime modules — the push coordinator (unit conversion, preset gating,
  per-entity exclusion, event buffering, requeue-on-failure), the API client (status→error mapping,
  timeout wrapping), the WebSocket command surface (admin gating, slug validation, de-dup), the
  config/options flows, the setup/unload/panel lifecycle, and the Sentry filter. ~98 tests, ~92%
  coverage. The dependency-free `units`/`discovery` runners still work as a fast smoke path.
- **Linting + formatting** via `ruff` (`make lint` / `make format`).
- **CI** (`.github/workflows/ci.yml`): ruff + pytest/coverage + Home Assistant `hassfest` + HACS
  validation on every push/PR.
- **pre-commit** hooks (ruff + translations-sync).

## v0.3.0 — Choose which sensors a preset sends

Presets are matched by device class, so turning one on used to send **every** matching entity — all
of your temperature sensors, including the noisy freezer probe you didn't want. Now you can keep a
preset on and just drop the sensors you don't care about.

### Added

- **Per-sensor preset opt-out.** In the panel's *Presets* card, expand any enabled preset with more
  than one sensor to see the individual entities it's sending. Every sensor is on by default; uncheck
  one to stop sending just that entity while the preset stays on for the rest. The row count shows
  "N of M sensors" when you've excluded some.
- Persisted as a new `preset_excluded` option (a list of `entity_id`s), honoured by the pusher for
  both metric and event presets and carried through the fallback options menu untouched. Backed by a
  new admin-only `sesharo/set_preset_excluded` WebSocket command.

## v0.2.1 — Fix a panel hang that froze Home Assistant

A **critical fix** for anyone on v0.2.0. Opening the panel's *add a mapping* entity suggestions could
**freeze all of Home Assistant** — the UI would drop to "loading data / failed to connect" until the
watchdog restarted core.

### Fixed

- **Infinite loop in entity discovery.** When a suggested signal slug was already ~49 characters and
  collided with an existing one, the de-duplication suffix (`_2`, `_3`, …) was truncated straight back
  to the original by the 49-char slug cap, so the loop never terminated. The disambiguating suffix now
  always fits within the cap, guaranteeing termination. Added a timeout-guarded regression test.

### Hardened

- **Suggestions scan moved off the event loop.** The `suggestions` WebSocket command now snapshots
  states on the loop and runs the scan in an executor, so a large entity registry can never block
  Home Assistant — defence-in-depth beside the termination fix.

## v0.2.0 — The Sesharo panel

This release adds a proper **Sesharo panel** to the Home Assistant sidebar. Everything you could do
from the integration's *Configure* menu now has a real UI — plus live push status, a mapping table,
and a much better way to add mappings.

### New — a sidebar panel

Open **Sesharo** in the sidebar after setup. It shows:

- **Push health at a glance** — whether data is reaching Sesharo, when the last push happened and
  when the next one is due, how many signals are mapped, and a **Push now** button to send
  immediately.
- **A mapping table** — every entity → Sesharo signal, each with its live last-sent value. Remove a
  mapping, or add one **inline** without leaving the page.
- **Presets** — the nine curated presets (temperature, humidity, CO₂, fine particles, power, energy,
  presence, motion, doors) with a master switch **and a per-signal toggle** for each, so you can turn
  off just the ones you don't want.
- **Worth tracking** — one-tap suggestions for entities no preset covers, with the signal name, kind
  and unit filled in for you.

### New — send into a signal you already track

When you add a mapping you can now route an entity into a **signal that already exists in your
Sesharo account** (fed by your phone, another integration, or logged by hand) so the readings join
it — not just create a brand-new signal. The picker shows what you already track with reading counts
and units, flags when an entity's unit **matches** the existing signal, and **converts** the reading
into the signal's unit when they differ (or stops you if there's no safe conversion) so joined data
is never stored with the wrong numbers.

### Also in this release

- Per-preset on/off is remembered independently of the master switch.
- Readings that join an existing signal are unit-converted before sending.
- The old *Configure* menu is still there as a text-only fallback.

### Notes

- The integration still creates **no entities** in Home Assistant — it only exports data to Sesharo.
- After updating, **restart Home Assistant** to load the new version (HACS will prompt you).
- The "signal you already track" picker needs the matching Sesharo API update, which is already live
  on `api.sesharo.com`.

## v0.1.0 — Initial release

- Push selected Home Assistant entity states into Sesharo via a Personal Access Token.
- Curated presets auto-discovered by `device_class`, plus user-defined custom mappings.
- Idempotent bulk ingest; values sent in canonical units.
- Entity discovery + pre-filled custom-mapping flow in the options menu.
- Opt-in, off-by-default Sentry error reporting scoped to the component.
