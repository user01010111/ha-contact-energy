"""Tests for complete, calendar-aligned electricity summaries."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from custom_components.contact_energy.models import CONTACT_TIME_ZONE, UsagePoint
from custom_components.contact_energy.periods import (
    percentage_change,
    summarise_history,
    summarise_periods,
    summarise_profile,
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


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 8, 9), 24), (date(2026, 4, 5), 25), (date(2026, 9, 27), 23)],
)
def test_free_summary_requires_complete_day_including_dst(day, hours) -> None:
    today = day + timedelta(days=1)
    points = [replace(p, free_energy=0.25) for p in _hours(day, today)]
    result = summarise_periods(points, today)["yesterday"]
    assert result.free_energy == hours * 0.25
    assert result.free_hours == result.expected_hours == hours
    assert result.energy == hours
    assert summarise_periods(points[:-1], today)["yesterday"].free_energy is None


@pytest.mark.parametrize("invalid", [None, -1, 2, float("nan"), True])
def test_missing_or_invalid_free_hour_does_not_hide_total(invalid) -> None:
    today = date(2026, 8, 10)
    points = [replace(p, free_energy=0.25) for p in _hours(date(2026, 8, 9), today)]
    points[0] = replace(points[0], free_energy=invalid)
    result = summarise_periods(points, today)["yesterday"]
    assert result.free_energy is None
    assert result.free_hours == 23
    assert result.energy == 24
    assert result.cost == 6


def test_conflicting_free_duplicates_are_order_independent_and_keep_totals() -> None:
    today = date(2026, 8, 10)
    points = [replace(p, free_energy=0.25) for p in _hours(date(2026, 8, 9), today)]
    points += [replace(points[0], free_energy=0.5), points[0]]
    forward = summarise_periods(points, today)["yesterday"]
    backward = summarise_periods(list(reversed(points)), today)["yesterday"]
    assert forward == backward
    assert forward.free_energy is None
    assert forward.energy == 24
    assert forward.cost == 6


def test_zero_free_usage_is_distinct_from_missing_and_not_carried_to_next_day() -> None:
    today = date(2026, 8, 10)
    points = [replace(p, free_energy=0) for p in _hours(date(2026, 8, 9), today)]
    assert summarise_periods(points, today)["yesterday"].free_energy == 0
    next_day = summarise_periods(points, today + timedelta(days=1))
    assert next_day["yesterday"].free_energy is None
    assert next_day["previous_day"].free_energy == 0


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


def test_latest_complete_day_survives_two_day_reporting_delay() -> None:
    points = [
        replace(p, free_energy=0.25) for p in _hours(date(2026, 8, 6), date(2026, 8, 9))
    ]
    points.pop()  # The newest day is incomplete.
    result = summarise_periods(points, date(2026, 8, 10))
    latest = result["latest_day"]
    assert latest.start == date(2026, 8, 7)
    assert latest.energy == 24
    assert latest.free_energy == 6
    assert latest.paid_energy == 18
    assert latest.free_percentage == 25
    assert result["yesterday"].energy is None


@pytest.mark.parametrize("days", [7, 30])
@pytest.mark.parametrize("end", [date(2026, 4, 7), date(2026, 9, 29)])
def test_rolling_comparison_uses_complete_calendar_days_through_latest(days, end):
    start = end - timedelta(days=days * 2)
    points = _hours(start, end)
    result = summarise_periods(points, end + timedelta(days=2))
    current = result[f"rolling_{days}_days"]
    previous = result[f"previous_{days}_days"]
    boundary = end - timedelta(days=days)
    assert (current.start, current.end, current.days) == (boundary, end, days)
    assert (previous.start, previous.end, previous.days) == (start, boundary, days)
    assert current.energy == len(_hours(boundary, end))
    assert previous.energy == len(_hours(start, boundary))
    assert current.expected_hours != days * 24  # Includes a DST change.


@pytest.mark.parametrize("missing_previous", [False, True])
def test_rolling_gap_is_unknown_without_shifting_the_comparison(missing_previous):
    start, end = date(2026, 8, 1), date(2026, 8, 15)
    points = _hours(start, end)
    points.pop(0 if missing_previous else 7 * 24)
    result = summarise_periods(points, end + timedelta(days=2))
    assert result["rolling_7_days"].end == end
    assert result["previous_7_days"].end == date(2026, 8, 8)
    assert (result["previous_7_days"].energy is None) == missing_previous
    assert (result["rolling_7_days"].energy is None) != missing_previous


@pytest.mark.parametrize("year", [2024, 2026])
def test_month_to_date_uses_equal_covered_day_count_and_no_future_hours(year):
    points = _hours(date(year, 2, 1), date(year, 3, 11))
    result = summarise_periods(points, date(year, 3, 13))
    current = result["month_to_date"]
    previous = result["previous_month_to_date"]
    assert (current.start, current.end) == (date(year, 3, 1), date(year, 3, 11))
    assert (previous.start, previous.end) == (date(year, 2, 1), date(year, 2, 11))
    assert current.days == previous.days == 10
    assert current.energy == previous.energy == 240


def test_month_to_date_never_compares_more_days_than_previous_month_contains():
    points = _hours(date(2026, 2, 1), date(2026, 3, 31))
    result = summarise_periods(points, date(2026, 3, 31))
    assert result["month_to_date"].days == 30
    assert "previous_month_to_date" not in result


def test_month_to_date_waits_for_current_month_and_requires_full_coverage():
    points = _hours(date(2026, 7, 1), date(2026, 8, 1))
    result = summarise_periods(points, date(2026, 8, 3))
    assert "month_to_date" not in result
    points += _hours(date(2026, 8, 2), date(2026, 8, 4))
    result = summarise_periods(points, date(2026, 8, 5))
    assert result["month_to_date"].energy is None
    assert result["month_to_date"].received_hours == 48
    assert result["previous_month_to_date"].days == 3


def test_usage_profile_excludes_partial_days_and_bounds_samples():
    end = date(2026, 8, 30)
    points = _hours(end - timedelta(days=35), end)
    # Partial day would otherwise bias the hourly and weekday averages.
    gap_day = end - timedelta(days=2)
    points = [
        p
        for p in points
        if not (
            p.start.astimezone(CONTACT_TIME_ZONE).date() == gap_day
            and p.start.astimezone(CONTACT_TIME_ZONE).hour == 12
        )
    ]
    latest = summarise_periods(points, end)["latest_day"]
    profile = summarise_profile(points + [points[-1]], latest)
    assert profile.start == end - timedelta(days=28)
    assert profile.end == end
    assert profile.complete_days == 27
    assert profile.hourly_average == (1,) * 24
    assert profile.hourly_samples == (27,) * 24
    assert profile.weekday_average == (24,) * 7
    assert sum(profile.weekday_samples) == 27
    assert profile.weekday_samples[gap_day.weekday()] == 3


@pytest.mark.parametrize(
    ("day", "samples"), [(date(2026, 4, 5), 2), (date(2026, 9, 27), 0)]
)
def test_usage_profile_counts_repeated_and_skipped_clock_hours(day, samples):
    end = day + timedelta(days=1)
    points = _hours(day, end)
    profile = summarise_profile(points, summarise_periods(points, end)["latest_day"])
    assert profile.complete_days == 1
    assert profile.hourly_samples[2] == samples
    assert profile.hourly_average[2] == (1 if samples else None)
    assert profile.weekday_average[day.weekday()] == len(points)
    assert sum(profile.weekday_samples) == 1
    assert sum(profile.hourly_samples) == len(points)


def test_usage_profile_missing_days_are_not_zeroes_or_carried_forward():
    day = date(2026, 8, 3)
    points = _hours(day, day + timedelta(days=1), energy=0)
    latest = summarise_periods(points, day + timedelta(days=2))["latest_day"]
    profile = summarise_profile(points, latest)
    assert profile.weekday_average == (0, None, None, None, None, None, None)
    assert profile.weekday_samples == (1, 0, 0, 0, 0, 0, 0)
    assert summarise_profile(points, None) is None
    assert summarise_profile(points[:-1], latest) is None


@pytest.mark.parametrize("day", [date(2026, 4, 5), date(2026, 9, 27)])
def test_history_preserves_date_gaps_zeroes_and_dst(day):
    end = day + timedelta(days=3)
    points = [replace(p, free_energy=0) for p in _hours(day, day + timedelta(days=1))]
    points += [
        replace(p, free_energy=0)
        for p in _hours(end - timedelta(days=1), end, energy=0)
    ]
    history = summarise_history(points, day, end)
    first, missing, zero = history.daily
    assert first.start == day
    assert first.expected_hours in (23, 25)
    assert first.free_energy == 0
    assert first.paid_energy == first.energy == first.expected_hours
    assert missing.energy is missing.free_energy is missing.paid_energy is None
    assert missing.received_hours == 0
    assert zero.energy == zero.free_energy == zero.paid_energy == 0


def test_history_months_require_full_calendar_coverage_and_recompute_corrections():
    start, end = date(2026, 7, 15), date(2026, 9, 3)
    points = [replace(p, free_energy=0.25) for p in _hours(start, end)]
    history = summarise_history(points, start, end)
    assert len(history.daily) == (end - start).days
    assert len(history.monthly) == 2
    assert history.monthly[0].energy is None  # Retention cutoff inside July.
    august = history.monthly[1]
    assert august.start == date(2026, 8, 1)
    assert august.free_energy == 31 * 24 * 0.25
    august_index = len(_hours(start, date(2026, 8, 1)))
    points[august_index] = replace(points[august_index], free_energy=None)
    revised = summarise_history(points, start, end)
    assert revised.monthly[1].energy == august.energy
    assert revised.monthly[1].free_energy is None
    assert revised.monthly[1].paid_energy is None
    assert revised.daily[17].free_energy is None  # Same date, not the fetch date.


def test_latest_day_does_not_choose_older_prices_or_free_values() -> None:
    points = [
        replace(p, free_energy=0.25) for p in _hours(date(2026, 8, 7), date(2026, 8, 9))
    ]
    points[-1] = replace(points[-1], cost=None, free_energy=None)
    latest = summarise_periods(points, date(2026, 8, 10))["latest_day"]
    assert latest.start == date(2026, 8, 8)
    assert latest.energy == 24
    assert latest.cost is None
    assert latest.free_energy is None
    assert latest.paid_energy is None
    assert latest.free_percentage is None


@pytest.mark.parametrize("day", [date(2026, 4, 5), date(2026, 9, 27)])
def test_latest_complete_day_respects_dst_and_ignores_today_and_future(day) -> None:
    today = day + timedelta(days=3)
    points = _hours(day, day + timedelta(days=1)) + _hours(
        today, today + timedelta(days=2)
    )
    latest = summarise_periods(points, today)["latest_day"]
    assert latest.start == day
    assert latest.expected_hours in (23, 25)
    assert latest.energy == latest.expected_hours


def test_no_complete_observed_day_has_no_latest_day() -> None:
    points = _hours(date(2026, 8, 8), date(2026, 8, 10))[1:-1]
    assert "latest_day" not in summarise_periods(points, date(2026, 8, 11))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2024, 2, 1), date(2024, 3, 1)),
        (date(2026, 4, 1), date(2026, 5, 1)),
        (date(2026, 9, 1), date(2026, 10, 1)),
    ],
)
def test_monthly_free_paid_breakdown_requires_all_hours(start, end) -> None:
    points = [replace(p, free_energy=0.25) for p in _hours(start, end)]
    summary = summarise_periods(points, end)["last_month"]
    assert summary.energy == len(points)
    assert summary.free_energy == len(points) * 0.25
    assert summary.paid_energy == len(points) * 0.75
    assert summary.free_percentage == 25
    incomplete = summarise_periods(points[:-1], end)["last_month"]
    assert incomplete.energy is incomplete.free_energy is incomplete.paid_energy is None
    points[0] = replace(points[0], free_energy=None)
    missing_free = summarise_periods(points, end)["last_month"]
    assert missing_free.energy == len(points)
    assert missing_free.cost == len(points) * 0.25
    assert missing_free.free_energy is missing_free.paid_energy is None


def test_latest_day_and_monthly_zero_usage_have_no_percentage() -> None:
    points = [
        replace(p, free_energy=0)
        for p in _hours(date(2026, 8, 1), date(2026, 9, 1), energy=0)
    ]
    result = summarise_periods(points, date(2026, 9, 1))
    for key in ("latest_day", "last_month"):
        assert (
            result[key].energy
            == result[key].free_energy
            == result[key].paid_energy
            == 0
        )
        assert result[key].free_percentage is None


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
