"""Validated data models for Contact Energy responses."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

from .api import MalformedResponse

CONTACT_TIME_ZONE = ZoneInfo("Pacific/Auckland")


@dataclass(frozen=True, slots=True)
class Contract:
    """A selectable electricity contract."""

    contract_id: str
    account_id: str
    icp: str
    address: str


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


@dataclass(frozen=True, slots=True)
class UsagePoint:
    """A validated hourly usage point."""

    start: datetime
    energy: float
    cost: float | None
    currency: str | None


def parse_contracts(payload: Mapping[str, Any]) -> list[Contract]:
    """Extract valid electricity contracts without retaining response objects."""
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
        if raw_contract.get("contractType") != 1:
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
            )
        )

    if not contracts:
        raise MalformedResponse("No valid electricity contracts were returned")
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
    if isinstance(devices, list) and devices and isinstance(devices[0], Mapping):
        device = devices[0]
        next_reading_date = _optional_date(device.get("nextMeterReadDate"))
        registers = device.get("registers")
        if (
            isinstance(registers, list)
            and registers
            and isinstance(registers[0], Mapping)
        ):
            previous_reading_date = _optional_date(
                registers[0].get("previousMeterReadingDate")
            )

    return AccountSnapshot(
        account_balance=account_balance,
        next_bill_amount=next_bill_amount,
        next_bill_date=next_bill_date,
        payment_due=payment_due,
        payment_due_date=payment_due_date,
        previous_reading_date=previous_reading_date,
        next_reading_date=next_reading_date,
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
                    _optional_number(raw_point.get("dollarValue"))
                    if currency is not None
                    else None
                ),
                currency=currency,
            )
        )
    if payload and not points:
        raise MalformedResponse("Usage response contained no valid hourly points")
    return points


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
        return "Electricity contract"
    supply_address = premise.get("supplyAddress")
    if not isinstance(supply_address, Mapping):
        return "Electricity contract"
    address = supply_address.get("shortForm")
    if not isinstance(address, str) or not address.strip():
        return "Electricity contract"
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
        number = float(Decimal(str(value)))
    except InvalidOperation, TypeError, ValueError:
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
    for date_format in ("%d %b %Y", "%Y-%m-%d"):
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
