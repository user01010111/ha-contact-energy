"""Validate current bills without retaining invoice or customer identifiers."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .api import MalformedResponse


@dataclass(frozen=True, slots=True)
class GasBill:
    """One contract's billed gas, not a cumulative meter or usage statistic."""

    start: date
    end: date
    days: int
    energy: float
    cost: float
    cost_excl_gst: float
    gst: float
    variable_cost_excl_gst: float
    fixed_cost_excl_gst: float
    reading_quality: str
    invoice_charges_incl_gst: float | None = None
    invoice_share_percent: float | None = None


@dataclass(frozen=True, slots=True)
class ElectricityBill:
    """Reconciled contract charges, independent of hourly usage estimates."""

    start: date
    end: date
    days: int
    cost: float
    cost_excl_gst: float
    gst: float


def _invalid() -> MalformedResponse:
    return MalformedResponse("Energy bill could not be reconciled")


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise _invalid()
    try:
        result = Decimal(str(value))
    except InvalidOperation, ValueError:
        raise _invalid() from None
    if not result.is_finite() or result < 0 or result > Decimal("1e12"):
        raise _invalid()
    return result


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise _invalid()
    try:
        # Bill dates are calendar labels, not hourly timestamps to convert.
        return datetime.fromisoformat(value).date()
    except ValueError:
        for fmt in ("%d %b %Y", "%Y%m%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
    raise _invalid()


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    if value.isascii() and value.isdecimal():
        return value.lstrip("0") or "0"
    return value


def _rows(payload: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise _invalid()
    result = payload.get(key)
    if not isinstance(result, list) or not all(isinstance(v, dict) for v in result):
        raise _invalid()
    return result


def latest_invoice(payload: Mapping[str, Any]) -> str | None:
    """Select a dated invoice, never a payment or an ambiguously ordered bill."""
    invoices = [
        (_date(row.get("date")), _identifier(row["invoiceId"]), row["invoiceId"])
        for row in _rows(payload, "transactions")
        if row.get("invoiceId")
    ]
    if not invoices:
        return None
    newest = max(item[0] for item in invoices)
    candidates = {key: raw for day, key, raw in invoices if day == newest}
    if len(candidates) != 1:
        raise _invalid()
    return next(iter(candidates.values()))


def _invoice_charges(payload: Mapping[str, Any]) -> Decimal | None:
    """Use reconciled current charges, never an amount due or account balance."""
    try:
        details = _rows(payload, "ClassificationDetails")
        identities = [
            tuple(
                _identifier(row.get(key))
                for key in ("Contract", "Premise", "Classification")
            )
            for row in details
        ]
        if not identities or len(set(identities)) != len(identities):
            return None
        amounts = [
            tuple(
                _number(row.get(key))
                for key in ("AmountExclGST", "GSTAmount", "AmountInclGST")
            )
            for row in details
        ]
        if any(excl + gst != incl for excl, gst, incl in amounts):
            return None
        total = _number(payload.get("TotalCurrentCharges"))
        gst_total = _number(payload.get("CurrentChargesGST"))
        if (
            total == 0
            or sum((row[2] for row in amounts), Decimal(0)) != total
            or sum((row[1] for row in amounts), Decimal(0)) != gst_total
        ):
            return None
        # Other services may omit individual line amounts; their reconciled
        # service totals, not tariff-line details, define the invoice share.
        return total
    except MalformedResponse:
        return None


def _current_installation(
    payload: Mapping[str, Any],
    *,
    account_id: str,
    contract_id: str,
    icp: str,
    invoice_id: str,
    product: str,
) -> tuple[dict[str, Any], date, date, int]:
    """Share current-bill ownership and billing-period validation across fuels."""
    technical = _rows(payload, "TechnicalDetails")
    if (
        len(technical) != 1
        or not str(technical[0].get("Version", "")).startswith("1.")
        or _identifier(payload.get("AccountNumber")) != _identifier(account_id)
        or _identifier(payload.get("InvoiceNumber")) != _identifier(invoice_id)
        or payload.get("isCurrentBill") is not True
    ):
        raise _invalid()
    installations = [
        v for v in _rows(payload, "Installations") if v.get("Contract") == contract_id
    ]
    if len(installations) != 1:
        raise _invalid()
    installation = installations[0]
    if installation.get("ICP") != icp or installation.get("Product") != product:
        raise _invalid()
    start, end = (
        _date(installation.get("ValidFrom")),
        _date(installation.get("ValidTo")),
    )
    days = (end - start).days + 1
    if not 1 <= days <= 366 or _number(installation.get("Days")) != days:
        raise _invalid()
    return installation, start, end, days


def _contract_charges(
    payload: Mapping[str, Any], contract_id: str, premise: Any, classification: str
) -> tuple[Decimal, Decimal, Decimal]:
    """Read the selected service's explicit pre-tax, tax and post-tax totals."""
    details = [
        v
        for v in _rows(payload, "ClassificationDetails")
        if v.get("Contract") == contract_id
    ]
    if (
        len(details) != 1
        or details[0].get("Classification") != classification
        or _identifier(details[0].get("Premise")) != _identifier(premise)
    ):
        raise _invalid()
    excl, gst, incl = (
        _number(details[0].get(key))
        for key in ("AmountExclGST", "GSTAmount", "AmountInclGST")
    )
    if excl + gst != incl:
        raise _invalid()
    return excl, gst, incl


def parse_electricity_bill(
    payload: Mapping[str, Any],
    *,
    account_id: str,
    contract_id: str,
    icp: str,
    invoice_id: str,
) -> ElectricityBill:
    """Expose actual electricity charges without guessing missing tariff lines."""
    installation, start, end, days = _current_installation(
        payload,
        account_id=account_id,
        contract_id=contract_id,
        icp=icp,
        invoice_id=invoice_id,
        product="Electricity",
    )
    excl, gst, incl = _contract_charges(
        payload, contract_id, installation.get("Premise"), "001"
    )
    return ElectricityBill(start, end, days, float(incl), float(excl), float(gst))


def parse_gas_bill(
    payload: Mapping[str, Any],
    *,
    account_id: str,
    contract_id: str,
    icp: str,
    invoice_id: str,
) -> GasBill:
    """Reconcile identity, coverage, units and tax before exposing a snapshot."""
    installation, start, end, days = _current_installation(
        payload,
        account_id=account_id,
        contract_id=contract_id,
        icp=icp,
        invoice_id=invoice_id,
        product="Natural Gas",
    )
    registers = [
        v
        for v in _rows(payload, "Registers")
        if v.get("Installation") == installation.get("Installation")
        and v.get("ICP") == icp
    ]
    if not registers or any(
        v.get("BillUnitOfMeasure") != "kWh" or v.get("Product") != "Natural Gas"
        for v in registers
    ):
        raise _invalid()
    identities = [(v.get("Device"), v.get("Register")) for v in registers]
    if any(
        not all(isinstance(part, str) and part for part in key) for key in identities
    ) or len(set(identities)) != len(identities):
        raise _invalid()
    energy = sum((_number(v.get("EnergyUnits")) for v in registers), Decimal(0))
    qualities = {
        v.get("CurrentMeterReadType")
        if isinstance(v.get("CurrentMeterReadType"), str)
        else None
        for v in registers
    }
    quality = (
        "actual"
        if qualities == {"01"}
        else "estimated"
        if qualities == {"03"}
        else "unknown"
    )
    excl, gst, incl = _contract_charges(
        payload, contract_id, installation.get("Premise"), "002"
    )
    totals = []
    for key in ("VariableCharges", "FixedCharges"):
        rows = [v for v in _rows(payload, key) if v.get("Contract") == contract_id]
        totals.append(sum((_number(v.get("AmountExclGST")) for v in rows), Decimal(0)))
    if sum(totals) != excl:
        raise _invalid()
    invoice_charges = _invoice_charges(payload)
    return GasBill(
        start=start,
        end=end,
        days=days,
        energy=float(energy),
        cost=float(incl),
        cost_excl_gst=float(excl),
        gst=float(gst),
        variable_cost_excl_gst=float(totals[0]),
        fixed_cost_excl_gst=float(totals[1]),
        reading_quality=quality,
        invoice_charges_incl_gst=(
            float(invoice_charges) if invoice_charges is not None else None
        ),
        invoice_share_percent=(
            float(incl / invoice_charges * 100) if invoice_charges is not None else None
        ),
    )
