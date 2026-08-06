"""Test harness — works in two environments.

1. **No-dependency standalone runners** (`python3 tests/test_units.py`, `make test`): Home Assistant
   isn't installed, so ``install_stubs()`` injects the tiny slice of ``homeassistant.const`` that the
   *pure* modules (``units``/``discovery``) import. This keeps the fast, dependency-free smoke path
   that predates the full harness.

2. **Full pytest + pytest-homeassistant-custom-component** (`pytest`, CI): a real Home Assistant is
   installed, so the runtime modules (``coordinator``/``api``/``websocket_api``/``config_flow``/
   ``__init__``) can be exercised against a real ``hass``, ``MockConfigEntry`` and mocked aiohttp.
   Here ``install_stubs()`` is a **no-op** — stubbing out ``homeassistant`` would poison the real
   import that these tests depend on.

The switch is automatic: if the real ``homeassistant`` package imports, we use it; otherwise we stub.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Repo root on the path so ``import custom_components.sesharo.*`` resolves in both modes
# (``custom_components`` is an implicit namespace package — no __init__.py needed).
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _real_ha_available() -> bool:
    """True when a real Home Assistant is importable (the pytest/pHACC environment)."""
    try:
        import homeassistant.config_entries  # noqa: F401
    except ImportError:
        return False
    return True


def install_stubs() -> None:
    """Inject minimal HA stubs — ONLY when a real Home Assistant isn't installed.

    In the pHACC environment the real HA is present, so this is a no-op: replacing ``homeassistant``
    with a stub would break every runtime-module test. In the bare standalone environment we seed
    ``homeassistant.const`` plus bare ``custom_components[.sesharo]`` package objects pointing at the
    source dirs, so ``import ...discovery`` finds the module under ``__path__`` without executing the
    full-HA ``__init__``.
    """
    if _real_ha_available():
        return

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
    """A stand-in for homeassistant.core.State (used by the pure-logic standalone tests)."""

    def __init__(self, entity_id: str, state: str, **attrs):
        self.entity_id = entity_id
        self.state = state
        self.attributes = attrs


# ── pytest + pytest-homeassistant-custom-component wiring ─────────────────────
# Only when a real HA is installed. Guarded so the standalone runners (which import this module
# under a plain python3 with neither pytest nor HA) never touch these.
if _real_ha_available():
    import pytest

    # pytest-homeassistant-custom-component auto-registers via its `pytest11` entry point, so its
    # fixtures (`hass`, `aioclient_mock`, `hass_ws_client`, `enable_custom_integrations`, …) are
    # already available — no `pytest_plugins` declaration needed (which would error in a non-rootdir
    # conftest anyway).

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """pHACC refuses to load a custom integration unless this fixture is requested.

        Autouse so every test that sets up the Sesharo entry gets it without boilerplate.
        """
        yield
