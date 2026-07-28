"""Tests for the entity auto-derivation + discovery logic (discovery.py).

Runnable two ways:
    python3 tests/test_discovery.py      # plain, no deps
    pytest tests/test_discovery.py       # if pytest is available
"""
from __future__ import annotations

import sys
from pathlib import Path

# Standalone-run bootstrap (`python3 tests/test_discovery.py`); under pytest, conftest already ran.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import install_stubs  # noqa: E402

install_stubs()


class FakeState:
    def __init__(self, entity_id, state, **attrs):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attrs


from custom_components.sesharo.discovery import (  # noqa: E402
    discover_candidates,
    slugify_signal,
    suggest_mapping,
)
from custom_components.sesharo.const import (  # noqa: E402
    CONF_CUSTOM_ENTITY,
    CONF_CUSTOM_KIND,
    CONF_CUSTOM_NAME,
    CONF_CUSTOM_SIGNAL,
    CONF_CUSTOM_UNIT,
    KIND_EVENT,
    KIND_METRIC,
)


# ── slugify ───────────────────────────────────────────────────────────────
def test_slugify_basic():
    assert slugify_signal("Washer Power") == "washer_power"


def test_slugify_strips_and_collapses():
    assert slugify_signal("  Kitchen   Temp!! ") == "kitchen_temp"


def test_slugify_must_start_alnum():
    assert slugify_signal("__weird__") == "weird"
    assert slugify_signal("123abc") == "123abc"  # a leading digit is allowed by the slug rule


def test_slugify_empty_when_nothing_usable():
    assert slugify_signal("!!!") == ""
    assert slugify_signal("") == ""


def test_slugify_truncates_to_49():
    assert len(slugify_signal("a" * 80)) == 49


# ── suggest_mapping ─────────────────────────────────────────────────────────
def test_suggest_numeric_sensor_is_metric_with_unit():
    state = FakeState("sensor.washer_power", "42.5", unit_of_measurement="W",
                      friendly_name="Washer Power", device_class="power")
    m = suggest_mapping("sensor.washer_power", state)
    assert m[CONF_CUSTOM_SIGNAL] == "washer_power"
    assert m[CONF_CUSTOM_KIND] == KIND_METRIC
    assert m[CONF_CUSTOM_UNIT] == "W"
    assert m[CONF_CUSTOM_NAME] == "Washer Power"


def test_suggest_binary_sensor_is_event_no_unit():
    state = FakeState("binary_sensor.front_door", "on", device_class="door",
                      friendly_name="Front Door")
    m = suggest_mapping("binary_sensor.front_door", state)
    assert m[CONF_CUSTOM_KIND] == KIND_EVENT
    assert m[CONF_CUSTOM_UNIT] == ""
    assert m[CONF_CUSTOM_SIGNAL] == "front_door"


def test_suggest_person_is_event():
    state = FakeState("person.joe", "home", friendly_name="Joe")
    m = suggest_mapping("person.joe", state)
    assert m[CONF_CUSTOM_KIND] == KIND_EVENT


def test_suggest_non_numeric_sensor_rejected():
    # A text sensor (e.g. a weather condition string) can't be a metric → skip it.
    state = FakeState("sensor.weather", "cloudy", friendly_name="Weather")
    assert suggest_mapping("sensor.weather", state) is None


def test_suggest_unsupported_domain_rejected():
    state = FakeState("light.kitchen", "on")
    assert suggest_mapping("light.kitchen", state) is None


def test_suggest_falls_back_to_friendly_name_for_slug():
    # object_id slugifies to empty ("___") → fall back to the friendly name.
    state = FakeState("sensor.___", "5", friendly_name="Attic Temp", unit_of_measurement="°C")
    m = suggest_mapping("sensor.___", state)
    assert m[CONF_CUSTOM_SIGNAL] == "attic_temp"


# ── discover_candidates ─────────────────────────────────────────────────────
def _states():
    return [
        FakeState("sensor.washer_power", "42", unit_of_measurement="W",
                  friendly_name="Washer Power", device_class="power"),          # preset (power)
        FakeState("sensor.bedroom_temp", "21", unit_of_measurement="°C",
                  friendly_name="Bedroom Temp", device_class="temperature"),    # preset (temperature)
        FakeState("sensor.washer_cycles", "7", friendly_name="Washer Cycles"),  # numeric, no class
        FakeState("binary_sensor.mailbox", "off", friendly_name="Mailbox"),     # event, no class
        FakeState("sensor.weather", "sunny", friendly_name="Weather"),          # non-numeric → skip
        FakeState("light.kitchen", "on"),                                       # unsupported → skip
        FakeState("sensor.dead", "unavailable", friendly_name="Dead"),          # skip state
    ]


def test_discover_excludes_presets_when_enabled():
    cands = discover_candidates(_states(), set(), set(), presets_enabled=True)
    entities = {c[CONF_CUSTOM_ENTITY] for c in cands}
    # power + temperature are presets → excluded; weather/light/dead never qualify.
    assert entities == {"sensor.washer_cycles", "binary_sensor.mailbox"}


def test_discover_includes_presets_when_disabled():
    cands = discover_candidates(_states(), set(), set(), presets_enabled=False)
    entities = {c[CONF_CUSTOM_ENTITY] for c in cands}
    assert "sensor.washer_power" in entities
    assert "sensor.bedroom_temp" in entities


def test_discover_skips_already_mapped_entities():
    cands = discover_candidates(
        _states(), {"sensor.washer_cycles"}, set(), presets_enabled=True
    )
    entities = {c[CONF_CUSTOM_ENTITY] for c in cands}
    assert "sensor.washer_cycles" not in entities
    assert "binary_sensor.mailbox" in entities


def test_discover_dedups_colliding_signals():
    states = [
        FakeState("sensor.power", "1", friendly_name="A"),
        FakeState("sensor.power", "2", friendly_name="B"),  # same object_id → same base slug
    ]
    # Two distinct entities with the same slug base; also collide with an existing "power" signal.
    cands = discover_candidates(states, set(), {"power"}, presets_enabled=True)
    signals = [c[CONF_CUSTOM_SIGNAL] for c in cands]
    assert len(set(signals)) == len(signals)  # all unique
    assert "power" not in signals  # avoided the existing mapping's signal


def test_discover_recognized_class_ranked_first():
    states = [
        FakeState("sensor.zzz_no_class", "1", friendly_name="No Class"),
        FakeState("sensor.aaa_pressure", "1013", friendly_name="Pressure",
                  device_class="pressure", unit_of_measurement="hPa"),
    ]
    cands = discover_candidates(states, set(), set(), presets_enabled=True)
    assert cands[0][CONF_CUSTOM_ENTITY] == "sensor.aaa_pressure"  # has device_class → first


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
