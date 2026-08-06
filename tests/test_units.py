"""Tests for the pure unit-conversion logic (units.py).

Runnable two ways:
    python3 tests/test_units.py      # plain, no deps
    pytest tests/test_units.py       # if pytest is available
"""

from __future__ import annotations

import sys
from pathlib import Path

# Standalone-run bootstrap; under pytest, conftest already ran.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import install_stubs

install_stubs()

from custom_components.sesharo.units import (
    convert_units,
    fmt_value,
    to_canonical,
)


def _close(a, b, eps=1e-6):
    return abs(a - b) < eps


# ── convert_units ────────────────────────────────────────────────────────────
def test_identity_same_unit():
    assert convert_units(21.0, "°C", "celsius") == 21.0  # normalised, same family base
    assert convert_units(50.0, "dBA", "dBA") == 50.0  # unknown unit, but equal → identity


def test_empty_target_is_identity():
    assert convert_units(7.5, "dBA", "") == 7.5
    assert convert_units(7.5, "dBA", None) == 7.5


def test_temperature_conversions():
    assert _close(convert_units(32.0, "°F", "celsius"), 0.0)
    assert _close(convert_units(212.0, "F", "celsius"), 100.0)
    assert _close(convert_units(0.0, "celsius", "°F"), 32.0)
    assert _close(convert_units(273.15, "K", "celsius"), 0.0)
    assert _close(convert_units(0.0, "celsius", "kelvin"), 273.15)


def test_power_conversions():
    assert _close(convert_units(1.5, "kW", "watts"), 1500.0)
    assert _close(convert_units(2000.0, "W", "kw"), 2.0)


def test_energy_conversions():
    assert _close(convert_units(500.0, "Wh", "kwh"), 0.5)
    assert _close(convert_units(2.0, "kWh", "wh"), 2000.0)


def test_incompatible_pairs_return_none():
    assert convert_units(10.0, "dBA", "celsius") is None  # unknown from-unit
    assert convert_units(10.0, "celsius", "watts") is None  # different families
    assert convert_units(10.0, "ppm", "µg/m³") is None  # both unknown, differ


# ── to_canonical (preset signals) ────────────────────────────────────────────
def test_to_canonical_temperature_f_to_c():
    assert _close(to_canonical("home_temperature", 68.0, "°F"), 20.0)


def test_to_canonical_power_kw_to_w():
    assert _close(to_canonical("home_power", 1.0, "kW"), 1000.0)


def test_to_canonical_energy_wh_to_kwh():
    assert _close(to_canonical("home_energy", 1000.0, "Wh"), 1.0)


def test_to_canonical_unknown_signal_passthrough():
    # Not a preset → value passes through untouched.
    assert to_canonical("bedroom_noise", 42.0, "dBA") == 42.0


def test_to_canonical_unknown_unit_passthrough():
    # Preset signal but an unrecognised unit → assume already canonical (prior behaviour).
    assert to_canonical("home_temperature", 21.0, "weird") == 21.0


# ── fmt_value ─────────────────────────────────────────────────────────────────
def test_fmt_value_drops_trailing_zero():
    assert fmt_value(21.0) == "21"
    assert fmt_value(21.50) == "21.5"
    assert fmt_value(21.25) == "21.25"
    assert fmt_value(0.0) == "0"


# ── standalone runner ───────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
