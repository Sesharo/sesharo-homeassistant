# Sesharo for Home Assistant

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
