<p align="center">
  <img src="https://raw.githubusercontent.com/Sesharo/sesharo-homeassistant/main/images/logo.png" alt="Sesharo" width="128" height="128" />
</p>

<h1 align="center">Sesharo for Home Assistant</h1>

A first-party [Home Assistant](https://www.home-assistant.io/) custom integration that pushes selected
Home Assistant data into [Sesharo](https://sesharo.com), where it becomes health-metrics and timeline
events alongside your wearables, calendar, and location data.

## What it sends

**Presets (auto-discovered by `device_class`, on by default):**

| Home Assistant | Sesharo signal | Unit |
|---|---|---|
| `temperature` sensors | `home_temperature` | °C |
| `humidity` sensors | `home_humidity` | % |
| `carbon_dioxide` sensors | `home_co2` | ppm |
| `pm25` sensors | `home_pm25` | µg/m³ |
| `power` sensors | `home_power` | W |
| `energy` sensors | `home_energy` | kWh |
| `occupancy`/`presence` binary sensors, `person.*` | `home_presence` (event) | — |
| `motion` binary sensors | `home_motion` (event) | — |
| `door` / `window` binary sensors | `home_door` / `home_window` (event) | — |

Values are converted to the canonical unit before sending (e.g. °F → °C, Wh → kWh).

**Custom mappings:** map *any* entity to a Sesharo signal of your choice (metric or event) from the
integration's Options. Custom metrics create a user-owned metric type in Sesharo automatically.

## How it works

Every *interval* (default 5 min) the integration snapshots your mapped numeric entities and posts them,
along with any discrete state changes (presence/door/motion, etc.) captured since the last push, to:

```
POST {base_url}/users/{user_id}/home-assistant
Authorization: Bearer <Personal Access Token>
{ "readings": [ … ], "events": [ … ] }
```

Ingest is **idempotent** — retries and overlaps are de-duplicated server-side, so nothing double-counts.

## The Sesharo panel

After setup, a **Sesharo** panel appears in the Home Assistant sidebar. It's the main place to manage
the integration:

- **Push health** at a glance — connection, last/next push, failure count, and a **Push now** button.
- The **mapping table** — every entity → signal mapping with its live last-sent value; add one inline
  without leaving the page.
- **Presets** — a master switch plus a per-signal toggle for each of the nine curated presets.
- **Worth tracking** — one-tap suggestions for entities no preset covers, with names and units filled
  in for you.
- When adding a mapping you can send an entity into a **brand-new signal** or one you **already track
  in Sesharo** (your phone, another integration, or manual logs) so the readings join it — units are
  matched or converted automatically.

The integration's **Configure** (Options) menu still works as a text-only fallback.

## Installation (HACS)

1. HACS → Integrations → ⋮ → **Custom repositories** → add this repo, category **Integration**.
2. Install **Sesharo**, then restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → **Sesharo**.

## Configuration

You'll need three things from the Sesharo app:

- **API base URL** — `https://api.sesharo.com` (default).
- **User ID** — your Sesharo user UUID.
- **Personal Access Token** — create one in Sesharo under **Settings → Developer**. The token is stored
  in Home Assistant's config entry and sent only to your Sesharo API base URL.

Then open the integration's **Configure** (Options) to adjust the push interval, toggle presets, and add
custom entity → signal mappings.

## Notes

- No entities are created in Home Assistant — this integration only *exports* data.
- The token grants API access to your Sesharo account; treat it like a password.
- **Logo / icon:** the logo above renders on the HACS page from `images/logo.png`. To make the Sesharo
  icon appear in Home Assistant's own *Devices & Services* list, the brand-ready assets in `brands/`
  (`icon.png` 256×256, `logo.png`) must be submitted to the [home-assistant/brands](https://github.com/home-assistant/brands)
  repo under `custom_integrations/sesharo/` — that repo is the only source HA reads integration icons from.
