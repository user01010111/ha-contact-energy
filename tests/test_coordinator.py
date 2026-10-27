"""Tests for coordinator error mapping and reporting gaps."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.util import dt as dt_util

from custom_components.contact_energy.api import (
    CannotConnect,
    InvalidAuth,
    MalformedResponse,
    RetryableApiError,
)
from custom_components.contact_energy.coordinator import (
    ContactEnergyCoordinator,
    _bounded_usage_days,
)
from custom_components.contact_energy.models import CONTACT_TIME_ZONE
from custom_components.contact_energy.statistics import StatisticsResult


class FakeStatistics:
    """Capture parsed usage points without touching recorder storage."""

    def __init__(self) -> None:
        self.points = []

    async def async_load(self) -> None:
        """Pretend persistent state loaded."""

    async def async_process(self, points):
        """Capture points and return a current total."""
        self.points = points
        return StatisticsResult(
            energy=sum(point.energy for point in points),
            cost=sum(point.cost or 0 for point in points),
            currency="NZD",
        )


@pytest.mark.parametrize("zone", ["Pacific/Auckland", "UTC", "America/Los_Angeles"])
async def test_history_dates_use_contact_calendar(
    hass, mock_config_entry, accounts_payload, monkeypatch, zone
) -> None:
    instant = datetime(2026, 8, 9, 18, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: instant)
    monkeypatch.setattr(dt_util, "now", lambda: instant.astimezone(ZoneInfo(zone)))
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.return_value = []
    coordinator = ContactEnergyCoordinator(
        hass, mock_config_entry, api, FakeStatistics()
    )
    coordinator.enable_history_fetches()

    await coordinator._async_update_data()

    assert [call.args[0] for call in api.async_get_usage.await_args_list] == [
        "2026-08-07",
        "2026-08-08",
        "2026-08-09",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, 10),
        (True, 10),
        ("invalid", 10),
        (float("inf"), 10),
        (1, 3),
        ("12", 12),
        (999, 31),
    ],
)
def test_usage_history_window_is_bounded(value, expected) -> None:
    assert _bounded_usage_days(value) == expected


async def test_initial_refresh_restores_totals_without_fetching_history(
    hass, mock_config_entry, accounts_payload
) -> None:
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    statistics = FakeStatistics()
    persisted = StatisticsResult(energy=42.5, cost=11.25, currency="NZD")
    statistics.async_process = AsyncMock(return_value=persisted)
    coordinator = ContactEnergyCoordinator(
        hass,
        mock_config_entry,
        api,
        statistics,
    )

    data = await coordinator._async_update_data()

    api.async_get_usage.assert_not_awaited()
    statistics.async_process.assert_awaited_once_with([])
    assert data.statistics is persisted
    assert data.last_successful_usage_fetch is None


async def test_usage_gap_does_not_stop_later_days(
    hass, mock_config_entry, accounts_payload, monkeypatch
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.asyncio.sleep", sleep
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    newest_requested_date = dt_util.utcnow().astimezone(
        CONTACT_TIME_ZONE
    ).date() - timedelta(days=1)
    api.async_get_usage.side_effect = [
        RetryableApiError(503, None),
        [],
        [
            {
                "date": f"{newest_requested_date.isoformat()}T01:00:00+12:00",
                "value": "1.5",
                "dollarValue": "0.45",
                "currency": "NZD",
            }
        ],
    ]
    statistics = FakeStatistics()
    coordinator = ContactEnergyCoordinator(
        hass,
        mock_config_entry,
        api,
        statistics,
    )
    coordinator.enable_history_fetches()

    data = await coordinator._async_update_data()

    assert api.async_get_usage.await_count == 3
    assert sleep.await_count == 2
    assert len(statistics.points) == 1
    assert data.statistics.energy == 1.5
    assert data.last_successful_usage_fetch is None


async def test_repeated_auth_failure_requests_reauthentication(
    hass, mock_config_entry
) -> None:
    api = AsyncMock()
    api.async_get_accounts.side_effect = InvalidAuth
    coordinator = ContactEnergyCoordinator(
        hass,
        mock_config_entry,
        api,
        FakeStatistics(),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (InvalidAuth(), ConfigEntryAuthFailed),
        (CannotConnect("offline"), ConfigEntryNotReady),
        (RetryableApiError(503, None), ConfigEntryNotReady),
    ],
)
async def test_first_refresh_maps_setup_failures(
    hass, mock_config_entry, failure, expected
) -> None:
    mock_config_entry.add_to_hass(hass)
    api = AsyncMock()
    api.async_get_accounts.side_effect = failure
    coordinator = ContactEnergyCoordinator(
        hass,
        mock_config_entry,
        api,
        FakeStatistics(),
    )
    mock_config_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

    try:
        with pytest.raises(expected):
            await coordinator.async_config_entry_first_refresh()
    finally:
        mock_config_entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def test_rate_limit_stops_remaining_history_requests(
    hass, mock_config_entry, accounts_payload
) -> None:
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.side_effect = RetryableApiError(429, 300)
    coordinator = ContactEnergyCoordinator(
        hass,
        mock_config_entry,
        api,
        FakeStatistics(),
    )
    coordinator.enable_history_fetches()

    await coordinator._async_update_data()

    api.async_get_usage.assert_awaited_once()


@pytest.mark.parametrize(
    "failure",
    [CannotConnect("offline"), RetryableApiError(429, 300), MalformedResponse("bad")],
)
async def test_last_successful_fetch_requires_entire_window_and_survives_failure(
    hass, mock_config_entry, accounts_payload, monkeypatch, failure
) -> None:
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    current = datetime(2026, 8, 9, 18, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: current)
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.return_value = []
    coordinator = ContactEnergyCoordinator(
        hass, mock_config_entry, api, FakeStatistics()
    )
    coordinator.enable_history_fetches()

    first = await coordinator._async_update_data()
    assert first.last_successful_usage_fetch == current
    assert first.statistics.energy == 0
    assert api.async_get_usage.await_count == 3

    current += timedelta(hours=8)
    api.async_get_usage.side_effect = [[], failure, []]
    failed = await coordinator._async_update_data()
    assert failed.last_successful_usage_fetch == first.last_successful_usage_fetch

    api.async_get_usage.side_effect = None
    recovered = await coordinator._async_update_data()
    assert recovered.last_successful_usage_fetch == current

    restarted = ContactEnergyCoordinator(hass, mock_config_entry, api, FakeStatistics())
    initial = await restarted._async_update_data()
    assert initial.last_successful_usage_fetch is None
