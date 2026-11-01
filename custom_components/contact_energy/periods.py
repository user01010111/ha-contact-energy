"""Complete electricity periods calculated from retained hourly observations."""

import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta

from .models import CONTACT_TIME_ZONE, UsagePoint, parse_free_energy


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
    free_energy: float | None = None
    free_hours: int = 0

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

    @property
    def paid_energy(self) -> float | None:
        """Return consumption outside the explicitly uncharged portion."""
        if self.energy is None or self.free_energy is None:
            return None
        return self.energy - self.free_energy

    @property
    def free_percentage(self) -> float | None:
        """Return the uncharged share, undefined for zero total consumption."""
        if self.free_energy is None or not self.energy:
            return None
        return self.free_energy / self.energy * 100


@dataclass(frozen=True, slots=True)
class UsageProfile:
    """Observed averages from complete days, not a forecast or tariff schedule."""

    start: date
    end: date
    complete_days: int
    hourly_average: tuple[float | None, ...]
    hourly_samples: tuple[int, ...]
    weekday_average: tuple[float | None, ...]
    weekday_samples: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class UsageHistory:
    """Dated complete-period snapshots, retaining unknown periods as gaps."""

    daily: tuple[PeriodSummary, ...]
    monthly: tuple[PeriodSummary, ...]


def _normalise_points(points: list[UsagePoint]) -> list[UsagePoint]:
    """Share validation and duplicate handling across summaries and profiles."""
    selected: dict[datetime, UsagePoint] = {}
    conflicts: set[datetime] = set()
    free_conflicts: set[datetime] = set()
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
            start,
            point.energy,
            cost,
            currency if cost is not None else None,
            parse_free_energy(point.free_energy, point.energy),
        )
        previous = selected.get(start)
        if previous is not None:
            if replace(previous, free_energy=None) != replace(
                normalized, free_energy=None
            ):
                conflicts.add(start)
            if previous.free_energy != normalized.free_energy:
                free_conflicts.add(start)
        selected[start] = normalized
    for start in conflicts:
        selected.pop(start, None)
    for start in free_conflicts - conflicts:
        selected[start] = replace(selected[start], free_energy=None)

    return [selected[hour] for hour in sorted(selected)]


def summarise_periods(
    points: list[UsagePoint], today: date
) -> dict[str, PeriodSummary]:
    """Summarise calendar periods and the latest complete observed day."""
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    previous_month = (last_month - timedelta(days=1)).replace(day=1)
    periods = {
        "yesterday": (today - timedelta(days=1), today),
        "previous_day": (today - timedelta(days=2), today - timedelta(days=1)),
        "last_month": (last_month, this_month),
        "previous_month": (previous_month, last_month),
    }
    ordered = _normalise_points(points)
    result: dict[str, PeriodSummary] = {}
    for key, (start_date, end_date) in periods.items():
        result[key] = _summarise_period(ordered, start_date, end_date)

    days: dict[date, list[UsagePoint]] = {}
    for point in ordered:
        day = point.start.astimezone(CONTACT_TIME_ZONE).date()
        if day < today:
            days.setdefault(day, []).append(point)
    for day in sorted(days, reverse=True):
        summary = _summarise_period(days[day], day, day + timedelta(days=1))
        if summary.energy is not None:
            result["latest_day"] = summary
            break
    if latest := result.get("latest_day"):
        for length in (7, 30):
            boundary = latest.end - timedelta(days=length)
            result[f"rolling_{length}_days"] = _summarise_period(
                ordered, boundary, latest.end
            )
            result[f"previous_{length}_days"] = _summarise_period(
                ordered, boundary - timedelta(days=length), boundary
            )
        if latest.start >= this_month:
            result["month_to_date"] = _summarise_period(ordered, this_month, latest.end)
            previous_end = last_month + (latest.end - this_month)
            # Never compare 30 March days against 28 February days.
            if previous_end <= this_month:
                result["previous_month_to_date"] = _summarise_period(
                    ordered, last_month, previous_end
                )
    return result


def summarise_profile(
    points: list[UsagePoint], latest: PeriodSummary | None
) -> UsageProfile | None:
    """Average complete days in the 28-day window ending at the latest day."""
    if latest is None:
        return None
    start = latest.end - timedelta(days=28)
    days: dict[date, list[UsagePoint]] = {}
    for point in _normalise_points(points):
        day = point.start.astimezone(CONTACT_TIME_ZONE).date()
        if start <= day < latest.end:
            days.setdefault(day, []).append(point)
    hours: list[list[float]] = [[] for _ in range(24)]
    weekdays: list[list[float]] = [[] for _ in range(7)]
    for day, rows in days.items():
        summary = _summarise_period(rows, day, day + timedelta(days=1))
        if summary.energy is None:
            continue
        weekdays[day.weekday()].append(summary.energy)
        for point in rows:
            hours[point.start.astimezone(CONTACT_TIME_ZONE).hour].append(point.energy)
    complete_days = sum(len(values) for values in weekdays)
    if not complete_days:
        return None

    def averages(groups: list[list[float]]) -> tuple[float | None, ...]:
        # Divide first so finite averages do not overflow while summing samples.
        return tuple(
            _finite_sum([value / len(values) for value in values]) if values else None
            for values in groups
        )

    return UsageProfile(
        start=start,
        end=latest.end,
        complete_days=complete_days,
        hourly_average=averages(hours),
        hourly_samples=tuple(len(values) for values in hours),
        weekday_average=averages(weekdays),
        weekday_samples=tuple(len(values) for values in weekdays),
    )


def summarise_history(
    points: list[UsagePoint], start: date, today: date
) -> UsageHistory:
    """Summarise the retained window without assigning late data to fetch dates."""
    ordered = _normalise_points(points)
    by_day: dict[date, list[UsagePoint]] = {}
    for point in ordered:
        day = point.start.astimezone(CONTACT_TIME_ZONE).date()
        if start <= day < today:
            by_day.setdefault(day, []).append(point)
    daily = []
    day = start
    while day < today:
        end = day + timedelta(days=1)
        daily.append(_summarise_period(by_day.get(day, []), day, end))
        day = end
    monthly = []
    month = start.replace(day=1)
    while month < today.replace(day=1):
        end = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        monthly.append(_summarise_period(ordered, month, end))
        month = end
    return UsageHistory(tuple(daily), tuple(monthly))


def _summarise_period(
    points: list[UsagePoint], start_date: date, end_date: date
) -> PeriodSummary:
    """Apply the same coverage checks to a fixed period or candidate latest day."""
    start = datetime.combine(start_date, time(), CONTACT_TIME_ZONE).astimezone(UTC)
    end = datetime.combine(end_date, time(), CONTACT_TIME_ZONE).astimezone(UTC)
    expected_hours = int((end - start).total_seconds() // 3600)
    rows = [point for point in points if start <= point.start < end]
    priced = [point for point in rows if point.cost is not None]
    free_rows = [point for point in rows if point.free_energy is not None]
    currencies = {point.currency for point in priced}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    energy = (
        _finite_sum([point.energy for point in rows])
        if len(rows) == expected_hours
        else None
    )
    return PeriodSummary(
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
        free_energy=(
            _finite_sum([point.free_energy for point in free_rows])
            if energy is not None and len(free_rows) == expected_hours
            else None
        ),
        free_hours=len(free_rows),
    )


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
