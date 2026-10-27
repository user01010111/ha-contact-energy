"""Tests for complete, calendar-aligned electricity summaries."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from custom_components.contact_energy.models import CONTACT_TIME_ZONE, UsagePoint
from custom_components.contact_energy.periods import (
    percentage_change,
    summarise_periods,
)


def _hours(start: date, end: date, *, energy: float = 1.0) -> list[UsagePoint]:
    """Build synthetic UTC observations between Contact calendar boundaries."""
    instant = datetime.combine(start, time(), CONTACT_TIME_ZONE).astimezone(UTC)
    stop = datetime.combine(end, time(), CONTACT_TIME_ZONE).astimezone(UTC)
    points = []
    while instant < stop:
        points.append(UsagePoint(instant, energy, 0.25, "NZD"))
        instant += timedelta(hours=1)
    return points


@pytest.mark.parametrize(
    ("day", "expected"),
    [(date(2026, 8, 9), 24), (date(2026, 4, 5), 25), (date(2026, 9, 27), 23)],
)
def test_complete_days_include_both_dst_transitions(day, expected) -> None:
    today = day + timedelta(days=1)
    points = _hours(day, today)

    result = summarise_periods(points, today)["yesterday"]

    assert result.start == day
    assert result.end == today
    assert result.days == 1
    assert (
        result.expected_hours == result.received_hours == result.cost_hours == expected
    )
    assert result.energy == result.energy_per_day == expected
    assert result.cost == result.cost_per_day == expected * 0.25
    assert result.currency == "NZD"


def test_dst_fold_local_timestamps_remain_distinct() -> None:
    points = _hours(date(2026, 4, 5), date(2026, 4, 6))
    local_points = [
        replace(point, start=point.start.astimezone(CONTACT_TIME_ZONE))
        for point in points
    ]

    result = summarise_periods(local_points, date(2026, 4, 6))["yesterday"]

    assert result.received_hours == 25
    assert result.energy == 25


def test_gap_is_not_a_zero_and_cannot_be_filled_by_extra_outside_point() -> None:
    points = _hours(date(2026, 8, 8), date(2026, 8, 10))
    points.pop()

    result = summarise_periods(points, date(2026, 8, 10))

    assert result["yesterday"].received_hours == 23
    assert result["yesterday"].energy is None
    assert result["yesterday"].cost is None
    assert result["yesterday"].energy_per_day is None
    assert result["previous_day"].energy == 24


def test_missing_cost_preserves_energy_without_zero_filling() -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    points[0] = replace(points[0], cost=None)

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.energy == 24
    assert result.cost is None
    assert result.cost_per_day is None
    assert result.cost_hours == 23


@pytest.mark.parametrize("currency", ["USD", None, "nzd", "NŻD", ""])
def test_mixed_or_invalid_currency_never_produces_a_money_total(currency) -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    points[0] = replace(points[0], currency=currency)

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.energy == 24
    assert result.cost is None
    if currency == "USD":
        assert result.currency is None
    else:
        assert result.cost_hours == 23


def test_identical_duplicates_count_once_regardless_of_order_or_timezone() -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    duplicates = [
        replace(point, start=point.start.astimezone(CONTACT_TIME_ZONE))
        for point in points
    ]

    result = summarise_periods(list(reversed(points + duplicates)), date(2026, 8, 10))[
        "yesterday"
    ]

    assert result.received_hours == 24
    assert result.energy == 24
    assert result.cost == 6


def test_conflicting_duplicate_is_not_silently_chosen() -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    points.extend([replace(points[0], energy=2), points[0]])

    forward = summarise_periods(points, date(2026, 8, 10))["yesterday"]
    reverse = summarise_periods(list(reversed(points)), date(2026, 8, 10))["yesterday"]

    assert forward == reverse
    assert forward.received_hours == 23
    assert forward.energy is None
    assert forward.cost is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True, 10**400])
def test_invalid_energy_cannot_complete_a_period(value) -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    points[0] = replace(points[0], energy=value)

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.energy is None
    assert result.received_hours == 23


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True])
def test_invalid_cost_preserves_valid_energy(value) -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    points[0] = replace(points[0], cost=value)

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.energy == 24
    assert result.cost is None
    assert result.cost_hours == 23


def test_non_hour_and_naive_timestamps_do_not_fill_missing_hours() -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    points[0] = replace(points[0], start=points[0].start.replace(tzinfo=None))
    points[1] = replace(points[1], start=points[1].start + timedelta(minutes=30))

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.received_hours == 22
    assert result.energy is None


def test_non_hour_utc_offsets_cannot_fill_expected_utc_hour_slots() -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10))
    points[0] = replace(
        points[0],
        start=points[0].start.replace(tzinfo=timezone(timedelta(minutes=30))),
    )

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.received_hours == 23
    assert result.energy is None


@pytest.mark.parametrize(
    (
        "today",
        "previous_start",
        "last_start",
        "this_start",
        "last_days",
        "previous_days",
    ),
    [
        (
            date(2026, 1, 31),
            date(2025, 11, 1),
            date(2025, 12, 1),
            date(2026, 1, 1),
            31,
            30,
        ),
        (
            date(2024, 4, 1),
            date(2024, 2, 1),
            date(2024, 3, 1),
            date(2024, 4, 1),
            31,
            29,
        ),
        (
            date(2026, 5, 1),
            date(2026, 3, 1),
            date(2026, 4, 1),
            date(2026, 5, 1),
            30,
            31,
        ),
    ],
)
def test_calendar_months_use_real_lengths_and_exclude_current_month(
    today, previous_start, last_start, this_start, last_days, previous_days
) -> None:
    points = _hours(previous_start, today + timedelta(days=1))

    result = summarise_periods(points, today)

    last = result["last_month"]
    previous = result["previous_month"]
    assert (last.start, last.end, last.days) == (last_start, this_start, last_days)
    assert (previous.start, previous.end, previous.days) == (
        previous_start,
        last_start,
        previous_days,
    )
    assert last.energy == len(_hours(last_start, this_start))
    assert previous.energy == len(_hours(previous_start, last_start))
    assert last.energy_per_day == last.energy / last_days
    assert last.cost_per_day == last.cost / last_days


def test_month_comparison_normalises_different_calendar_lengths() -> None:
    points = _hours(date(2026, 6, 1), date(2026, 8, 1))
    periods = summarise_periods(points, date(2026, 8, 20))
    current = periods["last_month"]
    previous = periods["previous_month"]

    assert current.energy != previous.energy
    assert percentage_change(current.energy_per_day, previous.energy_per_day) == 0
    assert percentage_change(current.cost_per_day, previous.cost_per_day) == 0


def test_empty_and_incomplete_months_remain_unknown() -> None:
    periods = summarise_periods([], date(2026, 8, 10))

    assert set(periods) == {"yesterday", "previous_day", "last_month", "previous_month"}
    assert all(
        period.energy is None and period.cost is None for period in periods.values()
    )
    assert all(
        period.received_hours == period.cost_hours == 0 for period in periods.values()
    )
    partial = summarise_periods(
        _hours(date(2026, 7, 2), date(2026, 8, 1)), date(2026, 8, 10)
    )["last_month"]
    assert partial.energy is None
    assert partial.received_hours == 30 * 24
    assert partial.expected_hours == 31 * 24


def test_zero_usage_and_cost_are_valid_complete_totals() -> None:
    points = [
        replace(point, cost=0.0)
        for point in _hours(date(2026, 8, 9), date(2026, 8, 10), energy=0)
    ]

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.energy == result.cost == 0
    assert result.cost_hours == 24


def test_overflowing_totals_are_unknown_not_infinite() -> None:
    points = [
        replace(point, cost=1e308)
        for point in _hours(date(2026, 8, 9), date(2026, 8, 10), energy=1e308)
    ]

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.received_hours == 24
    assert result.energy is None
    assert result.cost is None


def test_overflowing_energy_also_withholds_cost() -> None:
    points = _hours(date(2026, 8, 9), date(2026, 8, 10), energy=1e308)

    result = summarise_periods(points, date(2026, 8, 10))["yesterday"]

    assert result.energy is None
    assert result.cost is None


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [(12, 10, 20), (8, 10, -20), (0, 10, -100), (10, 10, 0)],
)
def test_percentage_change(current, previous, expected) -> None:
    assert percentage_change(current, previous) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("current", "previous"),
    [
        (None, 1),
        (1, None),
        (1, 0),
        (0, 0),
        (float("inf"), 1),
        (1, float("nan")),
        (1e308, 1e-308),
    ],
)
def test_undefined_percentage_changes_are_unknown(current, previous) -> None:
    assert percentage_change(current, previous) is None
