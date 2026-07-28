"""Turn Home Assistant entities into suggested Sesharo mappings.

Two jobs, both pure (no running HA needed — they take plain ``State``-like objects), so they can be
unit-tested with lightweight stubs:

- ``suggest_mapping`` — given one entity's state, derive a full candidate mapping (signal slug, kind,
  unit, display name) so the *Add a custom mapping* flow can pre-fill sensible defaults instead of
  making the user hand-type a slug.
- ``discover_candidates`` — scan every entity and return the ones worth tracking that aren't already
  covered by a preset or an existing custom mapping, so the *Suggest entities* flow can offer a
  tick-box list.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)

from .const import (
    CONF_CUSTOM_ENTITY,
    CONF_CUSTOM_KIND,
    CONF_CUSTOM_NAME,
    CONF_CUSTOM_SIGNAL,
    CONF_CUSTOM_UNIT,
    KIND_EVENT,
    KIND_METRIC,
    PERSON_EVENT_CATEGORY,
    PRESET_EVENTS,
    PRESET_METRICS,
)

_SKIP_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE, "", "None"}

# Domains we know how to export. sensor → usually a metric; binary_sensor/person → events.
_METRIC_DOMAINS = {"sensor"}
_EVENT_DOMAINS = {"binary_sensor", "person"}


def slugify_signal(text: str) -> str:
    """Coerce a name/entity id into a valid Sesharo slug (``^[a-z0-9][a-z0-9_]{0,48}$``).

    Returns ``""`` if nothing usable survives, so callers can fall back.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    slug = re.sub(r"^[^a-z0-9]+", "", slug)  # must start with a letter/number
    return slug[:49]


def _is_numeric(state: Any) -> bool:
    try:
        float(state.state)
        return True
    except (ValueError, TypeError):
        return False


def derive_kind(entity_id: str, state: Any) -> str:
    """Classify an entity as a metric (numeric reading) or event (discrete state change)."""
    domain = entity_id.split(".", 1)[0]
    if domain in _EVENT_DOMAINS:
        return KIND_EVENT
    device_class = state.attributes.get(ATTR_DEVICE_CLASS)
    if device_class in PRESET_EVENTS:
        return KIND_EVENT
    return KIND_METRIC


def suggest_mapping(entity_id: str, state: Any) -> dict[str, Any] | None:
    """Derive a candidate mapping for one entity, or ``None`` if it isn't worth exporting.

    Signal slug defaults to the entity's object id (``sensor.washer_power`` → ``washer_power``),
    falling back to a slugified friendly name. Metrics carry the entity's unit; the friendly name
    becomes the display name.
    """
    domain, _, object_id = entity_id.partition(".")
    if domain not in _METRIC_DOMAINS and domain not in _EVENT_DOMAINS:
        return None

    kind = derive_kind(entity_id, state)
    # A metric is only useful if it actually reads a number.
    if kind == KIND_METRIC and not _is_numeric(state):
        return None

    friendly = state.attributes.get(ATTR_FRIENDLY_NAME) or object_id
    signal = slugify_signal(object_id) or slugify_signal(friendly)
    if not signal:
        return None

    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) or "" if kind == KIND_METRIC else ""
    return {
        CONF_CUSTOM_ENTITY: entity_id,
        CONF_CUSTOM_SIGNAL: signal,
        CONF_CUSTOM_KIND: kind,
        CONF_CUSTOM_UNIT: unit or "",
        CONF_CUSTOM_NAME: friendly,
    }


def _covered_by_preset(entity_id: str, state: Any) -> bool:
    """True if presets already export this entity (so discovery shouldn't re-suggest it)."""
    if entity_id.startswith("person."):
        return True
    device_class = state.attributes.get(ATTR_DEVICE_CLASS)
    if not device_class:
        return False
    return device_class in PRESET_METRICS or device_class in PRESET_EVENTS


def discover_candidates(
    states: Iterable[Any],
    mapped_entities: set[str],
    mapped_signals: set[str],
    *,
    presets_enabled: bool,
) -> list[dict[str, Any]]:
    """Suggest entities to add, skipping anything already covered.

    Excludes entities that already have a custom mapping and — when presets are on — entities a
    preset already exports. Signals are de-duplicated (a numeric suffix is appended on collision)
    so two suggestions never fold into the same Sesharo metric type. Results are ordered
    recognized-device-class first, then by entity id, for a stable, sensible list.
    """
    used_signals = set(mapped_signals)
    ranked: list[tuple[tuple[int, str], dict[str, Any]]] = []
    for state in states:
        entity_id = state.entity_id
        if entity_id in mapped_entities:
            continue
        if state.state in _SKIP_STATES:
            continue
        if presets_enabled and _covered_by_preset(entity_id, state):
            continue
        candidate = suggest_mapping(entity_id, state)
        if candidate is None:
            continue
        # Recognized device_class sorts first; entity id breaks ties for determinism.
        has_class = bool(state.attributes.get(ATTR_DEVICE_CLASS))
        ranked.append(((0 if has_class else 1, entity_id), candidate))

    ranked.sort(key=lambda pair: pair[0])
    candidates = [candidate for _, candidate in ranked]

    # De-dup signals across the batch + against existing mappings.
    for candidate in candidates:
        base = candidate[CONF_CUSTOM_SIGNAL]
        signal = base
        n = 2
        while signal in used_signals:
            signal = slugify_signal(f"{base}_{n}")
            n += 1
        candidate[CONF_CUSTOM_SIGNAL] = signal
        used_signals.add(signal)

    return candidates
