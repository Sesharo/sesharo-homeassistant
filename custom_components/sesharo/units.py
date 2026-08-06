"""Pure unit-conversion helpers (no running HA needed, so unit-testable like ``discovery.py``).

Values are sent to Sesharo in canonical units — the pusher converts, never the backend. This module
holds the conversion tables and the two entry points the coordinator uses:

- ``convert_units`` — general from→to conversion within a known family (temperature / power /
  energy). Returns ``None`` when the pair isn't convertible so the caller can *skip* rather than
  record a wrong number.
- ``to_canonical`` — convert a *preset* reading to that signal's canonical unit.
"""
from __future__ import annotations

from .const import PRESET_METRICS

# Each family maps a recognised unit alias to a factor/formula that takes the value **to** the
# family's base unit. Anything not listed is "unknown" and conversion is refused.
_TEMP_TO_C = {  # base: celsius
    "°c": lambda v: v, "c": lambda v: v, "celsius": lambda v: v,
    "°f": lambda v: (v - 32.0) * 5.0 / 9.0, "f": lambda v: (v - 32.0) * 5.0 / 9.0,
    "fahrenheit": lambda v: (v - 32.0) * 5.0 / 9.0,
    "k": lambda v: v - 273.15, "kelvin": lambda v: v - 273.15,
}
_C_TO = {  # celsius → canonical target
    "celsius": lambda v: v,
    "°f": lambda v: v * 9.0 / 5.0 + 32.0,
    "kelvin": lambda v: v + 273.15,
}
_TEMP_ALIAS = {  # normalise a target-unit alias to a _C_TO key
    "°c": "celsius", "c": "celsius", "celsius": "celsius",
    "°f": "°f", "f": "°f", "fahrenheit": "°f", "k": "kelvin", "kelvin": "kelvin",
}
_POWER_TO_W = {"w": 1.0, "watt": 1.0, "watts": 1.0, "kw": 1000.0, "kilowatt": 1000.0, "mw": 0.001}
_ENERGY_TO_KWH = {
    "kwh": 1.0, "wh": 0.001, "watt-hour": 0.001, "watt hour": 0.001, "mwh": 1000.0,
}


def _norm(unit: str | None) -> str:
    return (unit or "").strip().lower()


def convert_units(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert ``value`` between two units in the same family. Same/empty target → identity.

    Returns ``None`` if the pair isn't convertible (different families or an unrecognised unit)."""
    f, t = _norm(from_unit), _norm(to_unit)
    if f == t or not t:
        return value
    if f in _TEMP_TO_C and _TEMP_ALIAS.get(t) in _C_TO:
        return _C_TO[_TEMP_ALIAS[t]](_TEMP_TO_C[f](value))
    if f in _POWER_TO_W and t in _POWER_TO_W:
        return value * _POWER_TO_W[f] / _POWER_TO_W[t]
    if f in _ENERGY_TO_KWH and t in _ENERGY_TO_KWH:
        return value * _ENERGY_TO_KWH[f] / _ENERGY_TO_KWH[t]
    return None


def to_canonical(signal: str, value: float, unit: str | None) -> float:
    """Convert a *preset* reading to Sesharo's canonical unit. Unknown units pass through
    (assumed already canonical, matching prior behaviour)."""
    preset_unit = dict(PRESET_METRICS.values()).get(signal)
    if preset_unit is None:
        return value
    converted = convert_units(value, unit, preset_unit)
    return converted if converted is not None else value


def fmt_value(value: float) -> str:
    """Compact display string for a numeric value (drops a trailing .0)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")
