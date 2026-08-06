"""Tests for the HTTP client (api.py) — status→exception mapping and transport-error wrapping.

Uses pytest-homeassistant-custom-component's ``aioclient_mock`` to stand in for the Sesharo API,
so we exercise the *real* aiohttp request/response path (not a hand-rolled fake).
"""

from __future__ import annotations

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.sesharo.api import (
    SesharoApiError,
    SesharoAuthError,
    SesharoClient,
)

BASE = "https://api.example.test"
USER = "user-1"
TOKEN = "pat-abc"
PUSH_URL = f"{BASE}/users/{USER}/home-assistant"
SIGNALS_URL = f"{PUSH_URL}/signals"


def _client(hass) -> SesharoClient:
    return SesharoClient(async_get_clientsession(hass), BASE, USER, TOKEN)


async def test_push_success_returns_json(hass, aioclient_mock):
    aioclient_mock.post(PUSH_URL, json={"accepted": 3})
    result = await _client(hass).async_push({"readings": [], "events": []})
    assert result == {"accepted": 3}


async def test_push_sends_bearer_token_and_payload(hass, aioclient_mock):
    aioclient_mock.post(PUSH_URL, json={})
    await _client(hass).async_push({"readings": [{"signal": "x"}], "events": []})
    assert len(aioclient_mock.mock_calls) == 1
    _method, _url, data, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert data == {"readings": [{"signal": "x"}], "events": []}


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_auth_statuses_raise_auth_error(hass, aioclient_mock, status):
    aioclient_mock.post(PUSH_URL, status=status)
    with pytest.raises(SesharoAuthError):
        await _client(hass).async_push({"readings": [], "events": []})


@pytest.mark.parametrize("status", [400, 422, 500, 503])
async def test_other_error_statuses_raise_api_error(hass, aioclient_mock, status):
    aioclient_mock.post(PUSH_URL, status=status, text="boom")
    with pytest.raises(SesharoApiError) as exc:
        await _client(hass).async_push({"readings": [], "events": []})
    assert str(status) in str(exc.value)


async def test_transport_error_wrapped_as_api_error(hass, aioclient_mock):
    import aiohttp

    aioclient_mock.post(PUSH_URL, exc=aiohttp.ClientError("connection reset"))
    with pytest.raises(SesharoApiError):
        await _client(hass).async_push({"readings": [], "events": []})


async def test_timeout_wrapped_as_api_error(hass, aioclient_mock):
    """Regression: a request timeout raises asyncio.TimeoutError (NOT an aiohttp.ClientError).

    Before the fix, it escaped ``async_push`` as an unhandled exception in the push interval
    callback — and the coordinator, which only catches ``SesharoApiError``, never requeued the
    buffered events. It must now be wrapped like any other transport failure.
    """
    aioclient_mock.post(PUSH_URL, exc=TimeoutError())
    with pytest.raises(SesharoApiError):
        await _client(hass).async_push({"readings": [], "events": []})


async def test_validate_posts_empty_batch(hass, aioclient_mock):
    aioclient_mock.post(PUSH_URL, json={})
    await _client(hass).async_validate()
    _method, _url, data, _headers = aioclient_mock.mock_calls[0]
    assert data == {"readings": [], "events": []}


async def test_list_signals_success(hass, aioclient_mock):
    payload = {"metrics": [{"signal": "home_power"}], "events": []}
    aioclient_mock.get(SIGNALS_URL, json=payload)
    assert await _client(hass).async_list_signals() == payload


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_list_signals_auth_error(hass, aioclient_mock, status):
    aioclient_mock.get(SIGNALS_URL, status=status)
    with pytest.raises(SesharoAuthError):
        await _client(hass).async_list_signals()


async def test_list_signals_timeout_wrapped(hass, aioclient_mock):
    aioclient_mock.get(SIGNALS_URL, exc=TimeoutError())
    with pytest.raises(SesharoApiError):
        await _client(hass).async_list_signals()


def test_base_url_strips_trailing_slash():
    # No network, so a real session isn't needed — base_url only touches the stored string.
    client = SesharoClient(session=None, base_url=f"{BASE}/", user_id=USER, token=TOKEN)
    assert client.base_url == BASE
