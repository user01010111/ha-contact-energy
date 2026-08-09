"""Tests for Contact Energy API failure and retry boundaries."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMockResponse,
)

from custom_components.contact_energy.api import (
    API_BASE_URL,
    PUBLIC_APPLICATION_API_KEY,
    CannotConnect,
    ContactEnergyApi,
    InvalidAuth,
    MalformedResponse,
    RetryableApiError,
    UnexpectedResponse,
    _parse_retry_after,
)

from .conftest import (
    TEST_ACCOUNT_ID,
    TEST_CONTRACT_ID,
    TEST_EMAIL,
    TEST_ICP,
    TEST_PASSWORD,
)

LOGIN_URL = f"{API_BASE_URL}/login/v2"
ACCOUNTS_URL = f"{API_BASE_URL}/accounts/v2"
USAGE_URL = (
    f"{API_BASE_URL}/usage/v2/{TEST_CONTRACT_ID}"
    f"?ba={TEST_ACCOUNT_ID}&interval=hourly&from=2026-08-01&to=2026-08-01"
)


def _api(hass) -> ContactEnergyApi:
    return ContactEnergyApi(
        hass,
        TEST_EMAIL,
        TEST_PASSWORD,
        TEST_ACCOUNT_ID,
        TEST_CONTRACT_ID,
    )


def _sequence(*responses: dict):
    queued = deque(responses)

    async def _next(method, url, data):
        return AiohttpClientMockResponse(method=method, url=url, **queued.popleft())

    return _next


async def test_invalid_credentials_are_preserved(hass, aioclient_mock) -> None:
    aioclient_mock.post(LOGIN_URL, status=401)

    with pytest.raises(InvalidAuth):
        await _api(hass).async_login()


async def test_timeout_is_connection_failure(hass, aioclient_mock) -> None:
    aioclient_mock.post(LOGIN_URL, exc=TimeoutError())

    with pytest.raises(CannotConnect):
        await _api(hass).async_login()


async def test_retryable_server_failure_is_distinct(hass, aioclient_mock) -> None:
    aioclient_mock.post(LOGIN_URL, status=503, headers={"Retry-After": "120"})

    with pytest.raises(RetryableApiError) as raised:
        await _api(hass).async_login()

    assert raised.value.status == 503
    assert raised.value.retry_after == 120


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
async def test_provider_safe_transient_statuses_are_retryable(
    hass, aioclient_mock, status
) -> None:
    aioclient_mock.post(LOGIN_URL, status=status)

    with pytest.raises(RetryableApiError) as raised:
        await _api(hass).async_login()

    assert raised.value.status == status


def test_retry_after_accepts_http_date_and_bounds_delay() -> None:
    now = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)

    assert (
        _parse_retry_after({"Retry-After": "Sun, 09 Aug 2026 00:02:00 GMT"}, now) == 120
    )
    assert (
        _parse_retry_after({"Retry-After": "Sun, 09 Aug 2026 03:00:00 GMT"}, now)
        == 3600
    )
    assert _parse_retry_after({"Retry-After": "not-a-date"}, now) is None


async def test_malformed_login_response(hass, aioclient_mock) -> None:
    aioclient_mock.post(LOGIN_URL, json={"unexpected": True})

    with pytest.raises(MalformedResponse):
        await _api(hass).async_login()


async def test_forbidden_response_does_not_start_reauthentication(
    hass, aioclient_mock
) -> None:
    aioclient_mock.post(LOGIN_URL, json={"token": "session-example"})
    aioclient_mock.get(ACCOUNTS_URL, status=403)

    with pytest.raises(UnexpectedResponse) as raised:
        await _api(hass).async_get_accounts()

    assert raised.value.status == 403
    assert len(aioclient_mock.mock_calls) == 2


async def test_concurrent_failed_login_wave_uses_one_request(
    hass, aioclient_mock
) -> None:
    aioclient_mock.post(LOGIN_URL, status=401)
    api = _api(hass)

    results = await asyncio.gather(
        *(api.async_login() for _ in range(3)),
        return_exceptions=True,
    )

    assert all(isinstance(result, InvalidAuth) for result in results)
    assert len(aioclient_mock.mock_calls) == 1


async def test_transport_exception_cause_does_not_retain_url(
    hass, aioclient_mock, caplog
) -> None:
    sentinel = "private-marker.example.invalid"
    aioclient_mock.post(LOGIN_URL, exc=aiohttp.InvalidURL(f"https://{sentinel}"))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CannotConnect) as raised:
        await _api(hass).async_login()

    assert raised.value.__cause__ is None
    assert sentinel not in caplog.text


async def test_expired_session_relogs_once_and_recovers(
    hass, aioclient_mock, accounts_payload
) -> None:
    aioclient_mock.post(
        LOGIN_URL,
        side_effect=_sequence(
            {"json": {"token": "session-one"}},
            {"json": {"token": "session-two"}},
        ),
    )
    aioclient_mock.get(
        ACCOUNTS_URL,
        side_effect=_sequence(
            {"status": 401},
            {"json": accounts_payload},
        ),
    )

    assert await _api(hass).async_get_accounts() == accounts_payload
    methods = [call[0].lower() for call in aioclient_mock.mock_calls]
    assert methods == ["post", "get", "post", "get"]


async def test_repeated_401_stops_after_one_relogin(hass, aioclient_mock) -> None:
    aioclient_mock.post(
        LOGIN_URL,
        side_effect=_sequence(
            {"json": {"token": "session-one"}},
            {"json": {"token": "session-two"}},
        ),
    )
    aioclient_mock.get(
        ACCOUNTS_URL,
        side_effect=_sequence({"status": 401}, {"status": 401}),
    )

    with pytest.raises(InvalidAuth):
        await _api(hass).async_get_accounts()

    assert len(aioclient_mock.mock_calls) == 4


async def test_reporting_gap_returns_empty_list(hass, aioclient_mock) -> None:
    aioclient_mock.post(LOGIN_URL, json={"token": "session-example"})
    aioclient_mock.post(USAGE_URL, status=404)

    assert await _api(hass).async_get_usage("2026-08-01") == []


async def test_logs_never_include_sensitive_values(
    hass, aioclient_mock, caplog
) -> None:
    aioclient_mock.post(LOGIN_URL, json={"token": "session-sensitive"})
    aioclient_mock.post(USAGE_URL, status=503)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RetryableApiError):
        await _api(hass).async_get_usage("2026-08-01")

    log_text = caplog.text
    for sensitive in (
        TEST_EMAIL,
        TEST_PASSWORD,
        TEST_ACCOUNT_ID,
        TEST_CONTRACT_ID,
        TEST_ICP,
        "session-sensitive",
        PUBLIC_APPLICATION_API_KEY,
        USAGE_URL,
    ):
        assert sensitive not in log_text
    assert "usage endpoint returned status 503" in log_text
