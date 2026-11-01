"""Validated data models for Contact Energy responses."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

from .api import MalformedResponse

CONTACT_TIME_ZONE = ZoneInfo("Pacific/Auckland")


@dataclass(frozen=True, slots=True)
class Contract:
    """A selectable electricity or piped-gas contract."""

    contract_id: str
    account_id: str
    icp: str
    address: str
    contract_type: int = 1


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """Account values exposed by sensor entities."""

    account_balance: float | None
    next_bill_amount: float | None
    next_bill_date: date | None
    payment_due: float | None
    payment_due_date: date | None
    previous_reading_date: date | None
    next_reading_date: date | None
    next_reading_is_estimate: bool | None = None


@dataclass(frozen=True, slots=True)
class UsagePoint:
    """A validated hourly usage point."""

    start: datetime
    energy: float
    cost: float | None
    currency: str | None
    free_energy: float | None = None


@dataclass(frozen=True, slots=True)
class MonthlyGasUsage:
    """Contact's calendar-month estimate/report, independent of invoice readings."""

    start: date
    energy: float | None


def gas_month_window(today: date) -> tuple[date, date]:
    """Return the last twelve calendar months, excluding the open month."""
    return today.replace(year=today.year - 1, day=1), today.replace(day=1)


def parse_monthly_gas_usage(
    payload: Any, start: date, end: date
) -> tuple[MonthlyGasUsage, ...]:
    """Validate dated monthly reports without inventing cost or reading quality."""
    if not isinstance(payload, list) or len(payload) > 13:
        raise MalformedResponse("Unexpected monthly gas response")
    months: dict[date, MonthlyGasUsage] = {}
    for row in payload:
        if not isinstance(row, Mapping):
            raise MalformedResponse("Unexpected monthly gas row")
        stamp = _optional_datetime(row.get("date"))
        local = stamp.astimezone(CONTACT_TIME_ZONE) if stamp is not None else None
        unit = row.get("unit")
        if (
            local is None
            or local.day != 1
            or local.hour != 0
            or any(
                type(row.get(key)) is not int
                for key in ("year", "month", "day", "hour")
            )
            or (row["year"], row["month"], row["day"], row["hour"])
            != (local.year, local.month, 1, 0)
            or row.get("timeZone") != "Pacific/Auckland"
            or not isinstance(unit, str)
            or unit.strip().casefold() != "kwh"
        ):
            raise MalformedResponse("Invalid monthly gas period")
        energy = _optional_number(row.get("value"), allow_negative=False)
        if row.get("value") is not None and energy is None:
            raise MalformedResponse("Invalid monthly gas usage")
        month = local.date()
        if month in months:
            raise MalformedResponse("Duplicate monthly gas period")
        months[month] = MonthlyGasUsage(month, energy)
    return tuple(months[month] for month in sorted(months) if start <= month < end)


def parse_plan_name(
    payload: Mapping[str, Any], account_id: str, contract_id: str
) -> str | None:
    """Select one account-owned contract label, without inferring tariff terms."""
    if not isinstance(payload, Mapping) or _identifier_value(
        payload.get("accountId")
    ) != _identifier_value(account_id):
        raise MalformedResponse("Plan account does not match selection")
    premises = payload.get("premises")
    if not isinstance(premises, list):
        raise MalformedResponse("Unexpected plan premises")
    names = []
    for premise in premises:
        if not isinstance(premise, Mapping) or not isinstance(
            premise.get("services"), list
        ):
            raise MalformedResponse("Unexpected plan services")
        for service in premise["services"]:
            if not isinstance(service, Mapping) or not isinstance(
                service.get("contract"), Mapping
            ):
                raise MalformedResponse("Unexpected plan contract")
            if _identifier_value(service["contract"].get("contractId")) != (
                _identifier_value(contract_id)
            ):
                continue
            details = service.get("planDetails")
            name = (
                details.get("externalPlanDescription")
                if isinstance(details, Mapping)
                else None
            )
            names.append(name)
    if len(names) != 1:
        return None
    name = names[0]
    if not isinstance(name, str) or not name.isprintable():
        return None
    name = name.strip()
    return name if 1 <= len(name) <= 255 else None


def parse_contracts(payload: Mapping[str, Any]) -> list[Contract]:
    """Extract supported contracts without retaining response objects."""
    detail = payload.get("accountDetail")
    if not isinstance(detail, Mapping):
        raise MalformedResponse("Accounts response is missing account details")

    account_id = _required_identifier(detail, "id")
    raw_contracts = detail.get("contracts")
    if not isinstance(raw_contracts, list):
        raise MalformedResponse("Accounts response is missing contracts")

    contracts: list[Contract] = []
    for raw_contract in raw_contracts:
        if not isinstance(raw_contract, Mapping):
            continue
        kind = raw_contract.get("contractType")
        if type(kind) is not int or kind not in (1, 2):
            continue
        try:
            contract_id = _required_identifier(raw_contract, "id")
            icp = _required_identifier(raw_contract, "icp")
        except MalformedResponse:
            continue
        contracts.append(
            Contract(
                contract_id=contract_id,
                account_id=account_id,
                icp=icp,
                address=_contract_address(raw_contract),
                contract_type=kind,
            )
        )

    if not contracts:
        raise MalformedResponse("No valid energy contracts were returned")
    return contracts


def parse_account_snapshot(
    payload: Mapping[str, Any], contract_id: str
) -> AccountSnapshot:
    """Parse account sensors and select meter dates by configured contract."""
    detail = payload.get("accountDetail")
    if not isinstance(detail, Mapping):
        raise MalformedResponse("Accounts response is missing account details")

    configured_contract_id = _identifier_value(contract_id)
    selected_contract: Mapping[str, Any] | None = None
    contracts = detail.get("contracts")
    if isinstance(contracts, list):
        for item in contracts:
            if not isinstance(item, Mapping):
                continue
            try:
                returned_contract_id = _identifier_value(item.get("id"))
            except MalformedResponse:
                continue
            if returned_contract_id == configured_contract_id:
                selected_contract = item
                break
    if selected_contract is None:
        raise MalformedResponse("Configured contract was not returned")

    account_balance = _nested_number(detail, "accountBalance", "currentBalance")
    next_bill_amount = _nested_number(detail, "nextBill", "amount")
    payment_due = _nested_number(detail, "invoice", "amountDue")
    next_bill_date = _nested_date(detail, "nextBill", "date")
    payment_due_date = _nested_date(detail, "invoice", "paymentDueDate")

    previous_reading_date: date | None = None
    next_reading_date: date | None = None
    devices = selected_contract.get("devices")
    next_reading_is_estimate = None
    if (
        isinstance(devices, list)
        and devices
        and all(isinstance(device, Mapping) for device in devices)
    ):
        # A contract-wide date must agree across all its meters/registers.
        next_dates = {_optional_date(d.get("nextMeterReadDate")) for d in devices}
        if len(next_dates) == 1:
            next_reading_date = next_dates.pop()
        estimates = [d.get("nextReadingIsEstimate") for d in devices]
        if all(type(value) is bool for value in estimates) and len(set(estimates)) == 1:
            next_reading_is_estimate = estimates[0]
        registers = [d.get("registers") for d in devices]
        if all(
            isinstance(rows, list)
            and rows
            and all(isinstance(r, Mapping) for r in rows)
            for rows in registers
        ):
            previous_dates = {
                _optional_date(r.get("previousMeterReadingDate"))
                for rows in registers
                for r in rows
            }
            if len(previous_dates) == 1:
                previous_reading_date = previous_dates.pop()

    return AccountSnapshot(
        account_balance=account_balance,
        next_bill_amount=next_bill_amount,
        next_bill_date=next_bill_date,
        payment_due=payment_due,
        payment_due_date=payment_due_date,
        previous_reading_date=previous_reading_date,
        next_reading_date=next_reading_date,
        next_reading_is_estimate=next_reading_is_estimate,
    )


def parse_usage_points(
    payload: list[dict[str, Any]],
    requested_date: date | None = None,
) -> list[UsagePoint]:
    """Return valid hourly points, tolerating only partial record corruption."""
    points: list[UsagePoint] = []
    for raw_point in payload:
        unit = raw_point.get("unit")
        if isinstance(unit, str) and unit.strip().lower() != "kwh":
            continue
        start = _optional_datetime(raw_point.get("date"))
        energy = _optional_number(raw_point.get("value"), allow_negative=False)
        if (
            start is None
            or energy is None
            or (
                requested_date
                and start.astimezone(CONTACT_TIME_ZONE).date() != requested_date
            )
        ):
            continue
        currency = _optional_currency(raw_point.get("currency"))
        points.append(
            UsagePoint(
                start=start,
                energy=energy,
                cost=(
                    _optional_number(raw_point.get("dollarValue"), allow_negative=False)
                    if currency is not None
                    else None
                ),
                currency=currency,
                free_energy=parse_free_energy(raw_point.get("unchargedValue"), energy),
            )
        )
    if payload and not points:
        raise MalformedResponse("Usage response contained no valid hourly points")
    return points


def parse_free_energy(value: Any, energy: float) -> float | None:
    """Accept an explicit uncharged subset, never infer it from off-peak pricing."""
    free = _optional_number(value, allow_negative=False)
    return free if free is not None and free <= energy else None


def _required_identifier(data: Mapping[str, Any], key: str) -> str:
    """Return a non-empty API identifier."""
    return _identifier_value(data.get(key))


def _identifier_value(value: Any) -> str:
    """Normalize one API identifier consistently across setup and updates."""
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise MalformedResponse("Accounts response contains an invalid identifier")
    normalized = str(value).strip()
    if not normalized:
        raise MalformedResponse("Accounts response contains an invalid identifier")
    return normalized


def _contract_address(contract: Mapping[str, Any]) -> str:
    """Return an address label used only during interactive contract selection."""
    premise = contract.get("premise")
    if not isinstance(premise, Mapping):
        return "Energy contract"
    supply_address = premise.get("supplyAddress")
    if not isinstance(supply_address, Mapping):
        return "Energy contract"
    address = supply_address.get("shortForm")
    if not isinstance(address, str) or not address.strip():
        return "Energy contract"
    return address.strip()


def _nested_number(data: Mapping[str, Any], parent: str, child: str) -> float | None:
    """Parse an optional finite number from a nested response object."""
    parent_value = data.get(parent)
    if not isinstance(parent_value, Mapping):
        return None
    return _optional_number(parent_value.get(child))


def _optional_number(value: Any, *, allow_negative: bool = True) -> float | None:
    """Parse an optional finite decimal without accepting booleans."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except TypeError, ValueError, OverflowError:
        return None
    if not math.isfinite(number) or (not allow_negative and number < 0):
        return None
    return number


def _nested_date(data: Mapping[str, Any], parent: str, child: str) -> date | None:
    """Parse an optional nested date."""
    parent_value = data.get(parent)
    if not isinstance(parent_value, Mapping):
        return None
    return _optional_date(parent_value.get(child))


def _optional_date(value: Any) -> date | None:
    """Parse a Contact display date or ISO date."""
    if not isinstance(value, str) or not value.strip():
        return None
    for date_format in ("%d %b %Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip(), date_format).date()
        except ValueError:
            continue
    return None


def _optional_datetime(value: Any) -> datetime | None:
    """Parse an aware top-of-hour API timestamp and normalize it to UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = dt_util.parse_datetime(value)
    if (
        parsed is None
        or parsed.tzinfo is None
        or parsed.minute
        or parsed.second
        or parsed.microsecond
    ):
        return None
    return dt_util.as_utc(parsed)


def _optional_currency(value: Any) -> str | None:
    """Return a conservative ISO-style currency code."""
    if not isinstance(value, str):
        return None
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        return None
    return currency
