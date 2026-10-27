"""Complete electricity periods calculated from retained hourly observations."""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from .models import CONTACT_TIME_ZONE, UsagePoint


@dataclass(frozen=True, slots=True)
class PeriodSummary:
    """A calendar period, with an exclusive end and explicit hourly coverage."""

    start: date
    end: date
    expected_hours: int
    received_hours: int
    cost_hours: int
    energy: float | None
    cost: float | None
    currency: str | None

    @property
    def days(self) -> int:
        """Return calendar days rather than elapsed 24-hour intervals."""
        return (self.end - self.start).days

    @property
    def energy_per_day(self) -> float | None:
        """Return comparable daily consumption only for a complete period."""
        return self.energy / self.days if self.energy is not None else None

    @property
    def cost_per_day(self) -> float | None:
        """Return comparable daily cost only for a complete priced period."""
        return self.cost / self.days if self.cost is not None else None


def summarise_periods(
    points: list[UsagePoint], today: date
) -> dict[str, PeriodSummary]:
    """Summarise fixed completed calendar periods without filling missing hours."""
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    previous_month = (last_month - timedelta(days=1)).replace(day=1)
    periods = {
        "yesterday": (today - timedelta(days=1), today),
        "previous_day": (today - timedelta(days=2), today - timedelta(days=1)),
        "last_month": (last_month, this_month),
        "previous_month": (previous_month, last_month),
    }
    selected: dict[datetime, UsagePoint] = {}
    conflicts: set[datetime] = set()
    for point in points:
        start = point.start
        if (
            start.tzinfo is None
            or start.utcoffset() is None
            or start.minute
            or start.second
            or start.microsecond
        ):
            continue
        start = start.astimezone(UTC)
        if start.minute or start.second or start.microsecond:
            continue
        if not _valid_number(point.energy):
            conflicts.add(start)
            continue
        currency = point.currency
        valid_currency = (
            isinstance(currency, str)
            and len(currency) == 3
            and currency.isascii()
            and currency.isalpha()
            and currency.isupper()
        )
        cost = point.cost if valid_currency and _valid_number(point.cost) else None
        normalized = UsagePoint(
            start, point.energy, cost, currency if cost is not None else None
        )
        if start in selected and selected[start] != normalized:
            conflicts.add(start)
        else:
            selected[start] = normalized
    for start in conflicts:
        selected.pop(start, None)

    result: dict[str, PeriodSummary] = {}
    for key, (start_date, end_date) in periods.items():
        start = datetime.combine(start_date, time(), CONTACT_TIME_ZONE).astimezone(UTC)
        end = datetime.combine(end_date, time(), CONTACT_TIME_ZONE).astimezone(UTC)
        expected_hours = int((end - start).total_seconds() // 3600)
        rows = [selected[hour] for hour in sorted(selected) if start <= hour < end]
        priced = [point for point in rows if point.cost is not None]
        currencies = {point.currency for point in priced}
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        complete = len(rows) == expected_hours
        energy = _finite_sum([point.energy for point in rows]) if complete else None
        result[key] = PeriodSummary(
            start=start_date,
            end=end_date,
            expected_hours=expected_hours,
            received_hours=len(rows),
            cost_hours=len(priced),
            energy=energy,
            cost=(
                _finite_sum([point.cost for point in priced if point.cost is not None])
                if energy is not None
                and len(priced) == expected_hours
                and currency is not None
                else None
            ),
            currency=currency,
        )
    return result


def percentage_change(current: float | None, previous: float | None) -> float | None:
    """Return percentage change, or unknown for missing data or a zero baseline."""
    if not _valid_number(current) or not _valid_number(previous) or previous == 0:
        return None
    result = (current / previous - 1) * 100
    return result if math.isfinite(result) else None


def _valid_number(value: float | None) -> bool:
    """Accept only finite, nonnegative numeric observations."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError, TypeError:
        return False


def _finite_sum(values: list[float]) -> float | None:
    """Avoid exposing infinity if individually finite readings overflow in total."""
    try:
        result = math.fsum(values)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None
