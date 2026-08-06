"""Optional Sentry error reporting for the Sesharo HA integration.

This component runs inside *other people's* Home Assistant installs, so telemetry
is **off by default and opt-in**: nothing is sent unless the `SESHARO_SENTRY_DSN`
environment variable is set on the HA host. Even then, a `before_send` filter drops
any event that does not originate from `custom_components.sesharo`, so we never
capture unrelated errors from the user's HA or other integrations.

Mirrors the env-gated guard in `sesharo-api` (`app/telemetry.py`): no DSN → no-op.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Only report events whose stack / logger lives under our package.
_OUR_PACKAGE = "custom_components.sesharo"

# Env vars (read from the HA host environment, never from config entries — this is
# a maintainer/self-host debugging aid, not a user-facing feature).
_DSN_ENV = "SESHARO_SENTRY_DSN"
_ENVIRONMENT_ENV = "SESHARO_SENTRY_ENVIRONMENT"

# Init is process-global; guard against re-running it per config entry.
_initialized = False


def _component_version() -> str:
    """Best-effort read of the version from manifest.json for the release tag."""
    try:
        manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
        return str(manifest.get("version", "unknown"))
    except (OSError, ValueError):
        return "unknown"


def _event_is_ours(event: dict[str, Any]) -> bool:
    """True if the event originates from this component (stack frame or logger)."""
    if str(event.get("logger", "")).startswith(_OUR_PACKAGE):
        return True
    for exc in event.get("exception", {}).get("values", []):
        for frame in exc.get("stacktrace", {}).get("frames", []):
            module = frame.get("module") or ""
            filename = frame.get("filename") or ""
            if module.startswith(_OUR_PACKAGE) or _OUR_PACKAGE.replace(".", "/") in filename:
                return True
    return False


def _before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    return event if _event_is_ours(event) else None


def init_sentry() -> None:
    """Initialise Sentry if `SESHARO_SENTRY_DSN` is set. Safe to call repeatedly.

    Blocking (imports sentry-sdk, starts the transport) — call from an executor
    thread, not the HA event loop.
    """
    global _initialized
    if _initialized:
        return

    dsn = os.environ.get(_DSN_ENV)
    if not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:
        _LOGGER.warning(
            "%s is set but sentry-sdk is not installed; skipping error reporting", _DSN_ENV
        )
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get(_ENVIRONMENT_ENV, "production"),
        release=f"sesharo-homeassistant@{_component_version()}",
        # Scope every event to our component; drop everyone else's errors.
        before_send=_before_send,
        # This is someone else's HA — never attach their IP / user data.
        send_default_pii=False,
        # Error monitoring only; no transaction sampling by default.
        traces_sample_rate=0.0,
    )
    _initialized = True
    _LOGGER.info("Sesharo Sentry error reporting enabled")
