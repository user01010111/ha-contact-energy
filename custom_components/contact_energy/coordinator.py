"""Coordinated account and usage updates for Contact Energy."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    CannotConnect,
    ContactEnergyApi,
    InvalidAuth,
    MalformedResponse,
    RetryableApiError,
    UnexpectedResponse,
)
from .const import (
    CONF_CONTRACT_ID,
    CONF_USAGE_DAYS,
    DEFAULT_USAGE_DAYS,
    MAX_USAGE_DAYS,
    MIN_USAGE_DAYS,
    UPDATE_INTERVAL,
)
from .models import (
    AccountSnapshot,
    UsagePoint,
    parse_account_snapshot,
    parse_usage_points,
)
from .statistics import ContactEnergyStatistics, StatisticsResult

_LOGGER = logging.getLogger(__name__)
HISTORY_REQUEST_SPACING = 0.1


@dataclass(frozen=True, slots=True)
class ContactEnergyData:
    """Data shared by Contact Energy coordinator entities."""

    account: AccountSnapshot
    statistics: StatisticsResult


def _bounded_usage_days(value: object) -> int:
    """Return a safe history window for new and legacy config entries."""
    if isinstance(value, bool):
        return DEFAULT_USAGE_DAYS
    try:
        days = int(value)
    except TypeError, ValueError:
        return DEFAULT_USAGE_DAYS
    return min(MAX_USAGE_DAYS, max(MIN_USAGE_DAYS, days))


class ContactEnergyCoordinator(DataUpdateCoordinator[ContactEnergyData]):
    """Fetch account and usage data through one client and update path."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ContactEnergyApi,
        statistics: ContactEnergyStatistics,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Contact Energy",
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self._entry = entry
        self._statistics = statistics
        self._history_fetches_enabled = False

    def enable_history_fetches(self) -> None:
        """Allow normal refreshes to import the configured history window."""
        self._history_fetches_enabled = True

    async def _async_setup(self) -> None:
        """Load persistent statistics state before the first refresh."""
        await self._statistics.async_load()

    async def _async_update_data(self) -> ContactEnergyData:
        """Fetch one account snapshot and a bounded range of usage days."""
        try:
            account_payload = await self.api.async_get_accounts()
            account = parse_account_snapshot(
                account_payload,
                self._entry.data[CONF_CONTRACT_ID],
            )
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(
                "Contact Energy credentials were rejected"
            ) from err
        except RetryableApiError as err:
            raise UpdateFailed(
                "Contact Energy is temporarily unavailable",
                retry_after=err.retry_after,
            ) from err
        except (CannotConnect, MalformedResponse, UnexpectedResponse) as err:
            raise UpdateFailed("Unable to update Contact Energy account data") from err

        if not self._history_fetches_enabled:
            statistics = await self._statistics.async_process([])
            return ContactEnergyData(account=account, statistics=statistics)

        today = dt_util.now().date()
        usage_days = _bounded_usage_days(
            self._entry.data.get(CONF_USAGE_DAYS, DEFAULT_USAGE_DAYS)
        )
        usage_points: list[UsagePoint] = []
        consecutive_failures = 0
        for request_index, days_ago in enumerate(range(usage_days, 0, -1)):
            if request_index:
                await asyncio.sleep(HISTORY_REQUEST_SPACING)
            requested_date = today - timedelta(days=days_ago)
            try:
                payload = await self.api.async_get_usage(requested_date.isoformat())
                parsed_points = parse_usage_points(payload, requested_date)
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed(
                    "Contact Energy credentials were rejected"
                ) from err
            except CannotConnect:
                _LOGGER.debug("Stopping usage history after a connection failure")
                break
            except RetryableApiError as err:
                _LOGGER.debug("Skipping one temporarily unavailable Contact usage day")
                consecutive_failures += 1
                if err.status == 429 or consecutive_failures >= 3:
                    break
                continue
            except MalformedResponse, UnexpectedResponse:
                _LOGGER.debug("Skipping one malformed Contact usage day")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break
                continue
            consecutive_failures = 0
            usage_points.extend(parsed_points)

        statistics = await self._statistics.async_process(usage_points)
        return ContactEnergyData(account=account, statistics=statistics)
