"""Coordinated account and usage updates for Contact Energy."""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

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
from .billing import (
    ElectricityBill,
    GasBill,
    latest_invoice,
    parse_electricity_bill,
    parse_gas_bill,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    CONF_CONTRACT_TYPE,
    CONF_USAGE_DAYS,
    DEFAULT_USAGE_DAYS,
    MAX_USAGE_DAYS,
    MIN_USAGE_DAYS,
    UPDATE_INTERVAL,
)
from .models import (
    CONTACT_TIME_ZONE,
    AccountSnapshot,
    MonthlyGasUsage,
    UsagePoint,
    gas_month_window,
    parse_account_snapshot,
    parse_monthly_gas_usage,
    parse_plan_name,
    parse_usage_points,
)
from .statistics import ContactEnergyStatistics, StatisticsResult

_LOGGER = logging.getLogger(__name__)
HISTORY_REQUEST_SPACING = 0.1


@dataclass(frozen=True, slots=True)
class ContactEnergyData:
    """Data shared by Contact Energy coordinator entities."""

    account: AccountSnapshot
    statistics: StatisticsResult | None
    last_successful_usage_fetch: datetime | None = None
    gas_bill: GasBill | None = None
    gas_bill_valid: bool = True
    plan_name: str | None = None
    electricity_bill: ElectricityBill | None = None
    electricity_bill_valid: bool = True
    gas_monthly: tuple[MonthlyGasUsage, ...] = ()
    gas_monthly_valid: bool = True
    gas_monthly_updated: datetime | None = None


def _bounded_usage_days(value: object) -> int:
    """Return a safe history window for new and legacy config entries."""
    if isinstance(value, bool):
        return DEFAULT_USAGE_DAYS
    try:
        days = int(value)
    except TypeError, ValueError, OverflowError:
        return DEFAULT_USAGE_DAYS
    return min(MAX_USAGE_DAYS, max(MIN_USAGE_DAYS, days))


class ContactEnergyCoordinator(DataUpdateCoordinator[ContactEnergyData]):
    """Fetch account and usage data through one client and update path."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ContactEnergyApi,
        statistics: ContactEnergyStatistics | None,
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
        self._last_successful_usage_fetch: datetime | None = None
        self._plan_name: str | None = None
        self._next_plan_check: datetime | None = None
        self._electricity_bill: ElectricityBill | None = None
        self._electricity_bill_valid = True
        self._next_electricity_bill_check: datetime | None = None
        self._optional_requests_allowed = True
        self._gas_monthly: tuple[MonthlyGasUsage, ...] = ()
        self._gas_monthly_valid = True
        self._gas_monthly_updated: datetime | None = None
        self._next_gas_monthly_check: datetime | None = None

    def enable_history_fetches(self) -> None:
        """Allow normal refreshes to import the configured history window."""
        self._history_fetches_enabled = True

    async def _async_setup(self) -> None:
        """Load persistent statistics state before the first refresh."""
        if self._statistics is not None:
            await self._statistics.async_load()

    async def _async_update_data(self) -> ContactEnergyData:
        """Keep optional plan lookup failures separate from usage and billing."""
        data = await self._async_update_primary_data()
        if (
            self._entry.data.get(CONF_CONTRACT_TYPE, 1) == 1
            and not self._history_fetches_enabled
        ):
            # Initial electricity setup restores state; the existing background
            # refresh handles optional network calls alongside usage history.
            return data
        now = dt_util.utcnow()
        if self._optional_requests_allowed and (
            self._next_plan_check is None or now >= self._next_plan_check
        ):
            self._next_plan_check = now + timedelta(days=1)
            try:
                payload = await self.api.async_get_plan_details()
                self._plan_name = parse_plan_name(
                    payload,
                    self._entry.data[CONF_ACCOUNT_ID],
                    self._entry.data[CONF_CONTRACT_ID],
                )
            except (RetryableApiError, CannotConnect, InvalidAuth) as err:
                # One endpoint failing must not starve independent snapshots.
                if isinstance(err, RetryableApiError) and err.status == 429:
                    self._optional_requests_allowed = False
                self._next_plan_check = now + max(
                    UPDATE_INTERVAL,
                    timedelta(seconds=getattr(err, "retry_after", None) or 0),
                )
                _LOGGER.debug("Optional Contact plan information is unavailable")
            except (
                MalformedResponse,
                UnexpectedResponse,
            ):
                self._plan_name = None
                _LOGGER.debug("Optional Contact plan information is unavailable")
        if (
            self._entry.data.get(CONF_CONTRACT_TYPE, 1) == 1
            and self._optional_requests_allowed
            and (
                self._next_electricity_bill_check is None
                or now >= self._next_electricity_bill_check
            )
        ):
            self._next_electricity_bill_check = now + timedelta(days=1)
            today = now.astimezone(CONTACT_TIME_ZONE).date()
            try:
                index = await self.api.async_get_bill_transactions(
                    (today - timedelta(days=120)).strftime("%Y%m%d"),
                    today.strftime("%Y%m%d"),
                )
                invoice = latest_invoice(index)
                bill = None
                if invoice is not None:
                    payload = await self.api.async_get_bill(invoice)
                    bill = parse_electricity_bill(
                        payload,
                        account_id=str(self._entry.data[CONF_ACCOUNT_ID]),
                        contract_id=str(self._entry.data[CONF_CONTRACT_ID]),
                        icp=str(self._entry.data[CONF_CONTRACT_ICP]),
                        invoice_id=invoice,
                    )
                self._electricity_bill = bill
                self._electricity_bill_valid = True
            except (RetryableApiError, CannotConnect, InvalidAuth) as err:
                self._next_electricity_bill_check = now + max(
                    UPDATE_INTERVAL,
                    timedelta(seconds=getattr(err, "retry_after", None) or 0),
                )
                _LOGGER.debug("Optional Contact electricity bill is unavailable")
            except MalformedResponse, UnexpectedResponse:
                self._electricity_bill = None
                self._electricity_bill_valid = False
                _LOGGER.debug("Optional Contact electricity bill is unavailable")
        if (
            self._entry.data.get(CONF_CONTRACT_TYPE, 1) == 2
            and self._optional_requests_allowed
            and (
                self._next_gas_monthly_check is None
                or now >= self._next_gas_monthly_check
            )
        ):
            self._next_gas_monthly_check = now + timedelta(days=1)
            start, end = gas_month_window(now.astimezone(CONTACT_TIME_ZONE).date())
            try:
                payload = await self.api.async_get_monthly_gas_usage(
                    start.isoformat(), end.isoformat()
                )
                self._gas_monthly = parse_monthly_gas_usage(payload, start, end)
                self._gas_monthly_valid = True
                self._gas_monthly_updated = now
            except (RetryableApiError, CannotConnect, InvalidAuth) as err:
                self._next_gas_monthly_check = now + max(
                    UPDATE_INTERVAL,
                    timedelta(seconds=getattr(err, "retry_after", None) or 0),
                )
                _LOGGER.debug("Optional Contact monthly gas usage is unavailable")
            except MalformedResponse, UnexpectedResponse:
                self._gas_monthly = ()
                self._gas_monthly_valid = False
                _LOGGER.debug("Optional Contact monthly gas usage is unavailable")
        return replace(
            data,
            plan_name=self._plan_name,
            electricity_bill=self._electricity_bill,
            electricity_bill_valid=self._electricity_bill_valid,
            gas_monthly=self._gas_monthly,
            gas_monthly_valid=self._gas_monthly_valid,
            gas_monthly_updated=self._gas_monthly_updated,
        )

    async def _async_update_primary_data(self) -> ContactEnergyData:
        """Fetch one account snapshot and a bounded range of usage days."""
        self._optional_requests_allowed = True
        try:
            account_payload = await self.api.async_get_accounts()
            account = parse_account_snapshot(
                account_payload,
                self._entry.data[CONF_CONTRACT_ID],
            )
            if self._entry.data.get(CONF_CONTRACT_TYPE, 1) == 2:
                today = dt_util.utcnow().astimezone(CONTACT_TIME_ZONE).date()
                transactions = await self.api.async_get_bill_transactions(
                    (today - timedelta(days=120)).strftime("%Y%m%d"),
                    today.strftime("%Y%m%d"),
                )
                try:
                    invoice = latest_invoice(transactions)
                except MalformedResponse:
                    return ContactEnergyData(
                        account=account, statistics=None, gas_bill_valid=False
                    )
                bill = None
                if invoice is not None:
                    payload = await self.api.async_get_bill(invoice)
                    try:
                        bill = parse_gas_bill(
                            payload,
                            account_id=self._entry.data[CONF_ACCOUNT_ID],
                            contract_id=self._entry.data[CONF_CONTRACT_ID],
                            icp=self._entry.data[CONF_CONTRACT_ICP],
                            invoice_id=invoice,
                        )
                    except MalformedResponse:
                        # Unsupported bills may persist until the next billing cycle.
                        return ContactEnergyData(
                            account=account, statistics=None, gas_bill_valid=False
                        )
                return ContactEnergyData(
                    account=account, statistics=None, gas_bill=bill
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

        assert self._statistics is not None
        if not self._history_fetches_enabled:
            statistics = await self._statistics.async_process([])
            return ContactEnergyData(account=account, statistics=statistics)

        today = dt_util.utcnow().astimezone(CONTACT_TIME_ZONE).date()
        usage_days = _bounded_usage_days(
            self._entry.data.get(CONF_USAGE_DAYS, DEFAULT_USAGE_DAYS)
        )
        usage_points: list[UsagePoint] = []
        consecutive_failures = 0
        all_requests_succeeded = True
        for request_index, days_ago in enumerate(range(1, usage_days + 1)):
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
                self._optional_requests_allowed = False
                all_requests_succeeded = False
                _LOGGER.debug("Stopping usage history after a connection failure")
                break
            except RetryableApiError as err:
                self._optional_requests_allowed = False
                all_requests_succeeded = False
                _LOGGER.debug("Skipping one temporarily unavailable Contact usage day")
                consecutive_failures += 1
                if err.status == 429 or consecutive_failures >= 3:
                    break
                continue
            except MalformedResponse, UnexpectedResponse:
                all_requests_succeeded = False
                _LOGGER.debug("Skipping one malformed Contact usage day")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break
                continue
            consecutive_failures = 0
            usage_points.extend(parsed_points)

        statistics = await self._statistics.async_process(usage_points)
        if all_requests_succeeded:
            self._last_successful_usage_fetch = dt_util.utcnow()
        return ContactEnergyData(
            account=account,
            statistics=statistics,
            last_successful_usage_fetch=self._last_successful_usage_fetch,
        )
