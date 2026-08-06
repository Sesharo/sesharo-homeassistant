"""Tests for the opt-in Sentry wiring (sentry.py).

The safety-critical behaviour is the ``before_send`` filter: because this component runs inside
end users' Home Assistant hosts, it must drop every event that doesn't originate from
``custom_components.sesharo`` so we never capture a user's unrelated errors. Also asserts the
env-gated no-op (no DSN → Sentry never initialised).
"""

from __future__ import annotations

from custom_components.sesharo import sentry


def test_event_from_our_logger_is_ours():
    assert sentry._event_is_ours({"logger": "custom_components.sesharo.coordinator"}) is True


def test_event_from_other_logger_is_not_ours():
    assert sentry._event_is_ours({"logger": "homeassistant.core"}) is False


def test_event_with_our_stackframe_module_is_ours():
    event = {
        "exception": {
            "values": [{"stacktrace": {"frames": [{"module": "custom_components.sesharo.api"}]}}]
        }
    }
    assert sentry._event_is_ours(event) is True


def test_event_with_our_stackframe_filename_is_ours():
    event = {
        "exception": {
            "values": [
                {"stacktrace": {"frames": [{"filename": "custom_components/sesharo/panel.py"}]}}
            ]
        }
    }
    assert sentry._event_is_ours(event) is True


def test_event_from_unrelated_integration_is_not_ours():
    event = {
        "exception": {
            "values": [
                {"stacktrace": {"frames": [{"module": "homeassistant.components.hue.light"}]}}
            ]
        }
    }
    assert sentry._event_is_ours(event) is False


def test_before_send_drops_foreign_events():
    assert sentry._before_send({"logger": "homeassistant.core"}, {}) is None


def test_before_send_keeps_our_events():
    event = {"logger": "custom_components.sesharo.coordinator"}
    assert sentry._before_send(event, {}) is event


def test_init_sentry_is_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SESHARO_SENTRY_DSN", raising=False)
    # Force the "not yet initialised" state so the guard doesn't short-circuit first.
    monkeypatch.setattr(sentry, "_initialized", False)
    sentry.init_sentry()
    assert sentry._initialized is False  # never turned on without a DSN


def test_component_version_reads_manifest():
    # Reads the shipped manifest.json — should be a dotted version, not the "unknown" fallback.
    version = sentry._component_version()
    assert version != "unknown"
    assert version[0].isdigit()
