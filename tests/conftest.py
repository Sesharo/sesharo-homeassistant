"""Off-device test harness.

The Sesharo component is dependency-free and Home Assistant isn't installed in this repo's test
environment, so we inject the tiny slice of ``homeassistant.const`` that ``discovery.py`` imports.
This lets the pure mapping/discovery logic (the actual "smarts") be exercised without a running HA.
Config-flow UI rendering still needs an on-device / ``pytest-homeassistant-custom-component`` run.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def install_stubs() -> None:
    """Register minimal stub packages so the pure submodules import without a real HA install.

    Importing ``custom_components.sesharo`` normally runs its ``__init__`` (which pulls in the full
    Home Assistant runtime). We pre-seed ``custom_components`` + ``custom_components.sesharo`` as
    bare package objects pointing at the source dirs, so ``import ...discovery`` finds the module
    under ``__path__`` without executing ``__init__``. ``homeassistant.const`` is stubbed too.
    """
    if "homeassistant" not in sys.modules:
        ha = types.ModuleType("homeassistant")
        ha.const = types.ModuleType("homeassistant.const")
        ha.const.ATTR_DEVICE_CLASS = "device_class"
        ha.const.ATTR_FRIENDLY_NAME = "friendly_name"
        ha.const.ATTR_UNIT_OF_MEASUREMENT = "unit_of_measurement"
        ha.const.STATE_UNAVAILABLE = "unavailable"
        ha.const.STATE_UNKNOWN = "unknown"
        sys.modules["homeassistant"] = ha
        sys.modules["homeassistant.const"] = ha.const

    if "custom_components" not in sys.modules:
        cc = types.ModuleType("custom_components")
        cc.__path__ = [str(_ROOT / "custom_components")]
        sys.modules["custom_components"] = cc
    if "custom_components.sesharo" not in sys.modules:
        pkg = types.ModuleType("custom_components.sesharo")
        pkg.__path__ = [str(_ROOT / "custom_components" / "sesharo")]
        sys.modules["custom_components.sesharo"] = pkg


install_stubs()


class FakeState:
    """A stand-in for homeassistant.core.State."""

    def __init__(self, entity_id: str, state: str, **attrs):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attrs
