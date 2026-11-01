"""Tests for contract-scoped entity identity and privacy."""

import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contact_energy import _contract_key
from custom_components.contact_energy.const import (
    CONF_ACCOUNT_ID,
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    CONTRACT_KEY_LENGTH,
    DEFAULT_CURRENCY,
    DOMAIN,
    contract_device_name,
    contract_entry_title,
)
from custom_components.contact_energy.coordinator import ContactEnergyData
from custom_components.contact_energy.models import parse_account_snapshot
from custom_components.contact_energy.periods import PeriodSummary
from custom_components.contact_energy.sensor import (
    SENSOR_DESCRIPTIONS,
    ContactEnergySensor,
)
from custom_components.contact_energy.statistics import StatisticsResult

from .conftest import TEST_CONTRACT_ID, TEST_ICP


def test_icp_display_names_balance_identification_and_log_privacy() -> None:
    icp = "000123456789012"

    assert contract_entry_title(icp) == "Contact Energy electricity (ICP …789012)"
    assert icp not in contract_entry_title(icp)
    assert contract_device_name(icp) == f"Contact Energy electricity (ICP {icp})"


def test_contract_keys_are_unique_and_opaque(entry_data) -> None:
    first = MockConfigEntry(domain=DOMAIN, data=entry_data)
    second_data = {
        **entry_data,
        CONF_CONTRACT_ID: "contract-second",
        CONF_CONTRACT_ICP: "icp-second",
    }
    second = MockConfigEntry(domain=DOMAIN, data=second_data)

    first_key = _contract_key(first)
    second_key = _contract_key(second)

    assert first_key != second_key
    assert len(first_key) == CONTRACT_KEY_LENGTH
    assert all(
        str(entry_data[key]) not in first_key
        for key in (CONF_ACCOUNT_ID, CONF_CONTRACT_ID, CONF_CONTRACT_ICP)
    )


def test_multiple_contracts_produce_unique_entity_ids(hass, entry_data) -> None:
    first_entry = MockConfigEntry(domain=DOMAIN, data=entry_data)
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            **entry_data,
            CONF_CONTRACT_ID: "contract-second",
            CONF_CONTRACT_ICP: "icp-second",
        },
    )
    coordinator = MagicMock(hass=hass)

    first = ContactEnergySensor(
        coordinator,
        _contract_key(first_entry),
        str(first_entry.data[CONF_CONTRACT_ICP]),
        SENSOR_DESCRIPTIONS[0],
    )
    second = ContactEnergySensor(
        coordinator,
        _contract_key(second_entry),
        str(second_entry.data[CONF_CONTRACT_ICP]),
        SENSOR_DESCRIPTIONS[0],
    )

    assert first.unique_id != second.unique_id
    assert first.device_info["name"] == f"Contact Energy electricity (ICP {TEST_ICP})"
    assert second.device_info["name"] == ("Contact Energy electricity (ICP icp-second)")
    assert TEST_ICP not in first.unique_id


def test_usage_allows_downward_corrections_and_money_is_nzd(hass) -> None:
    coordinator = MagicMock(hass=hass)
    usage = ContactEnergySensor(
        coordinator, "contract-key", TEST_ICP, SENSOR_DESCRIPTIONS[0]
    )
    money = ContactEnergySensor(
        coordinator, "contract-key", TEST_ICP, SENSOR_DESCRIPTIONS[1]
    )

    assert usage.state_class == "total"
    assert money.native_unit_of_measurement == DEFAULT_CURRENCY
    assert money.state_class is None


def test_monetary_descriptions_do_not_use_a_measurement_state_class() -> None:
    monetary_descriptions = [
        description
        for description in SENSOR_DESCRIPTIONS
        if description.device_class is SensorDeviceClass.MONETARY
    ]

    assert len(monetary_descriptions) == 10
    assert all(description.state_class is None for description in monetary_descriptions)


def _sensor(hass, accounts_payload, key, statistics, fetched=None):
    data = ContactEnergyData(
        account=parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID),
        statistics=statistics,
        last_successful_usage_fetch=fetched,
    )
    coordinator = MagicMock(hass=hass, data=data, last_update_success=True)
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == key)
    return ContactEnergySensor(coordinator, "contract-key", TEST_ICP, description)


@pytest.mark.parametrize(
    ("age", "expected"),
    [(None, "no_data"), (0, "current"), (48, "current"), (49, "delayed")],
)
def test_usage_freshness_is_independent_of_fetch_success(
    hass, accounts_payload, monkeypatch, age, expected
) -> None:
    now = datetime(2026, 8, 9, 18, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    latest = now - timedelta(hours=age + 1) if age is not None else None
    statistics = StatisticsResult(0, 0, "NZD", latest_usage=latest)
    status = _sensor(hass, accounts_payload, "usage_data_status", statistics, now)
    timestamp = _sensor(hass, accounts_payload, "latest_usage", statistics, now)
    fetched = _sensor(
        hass, accounts_payload, "last_successful_usage_fetch", statistics, now
    )

    assert status.native_value == expected
    assert status.options == ["no_data", "current", "delayed"]
    assert timestamp.native_value == latest
    assert fetched.native_value == now
    assert all(
        entity.extra_state_attributes is None for entity in (status, timestamp, fetched)
    )


@pytest.mark.parametrize(
    ("metric", "value", "average", "change", "unit"),
    [
        ("energy", 93, 3, 50, "kWh"),
        ("cost", 31, 1, -50, "NZD"),
    ],
)
def test_period_sensors_show_coverage_and_normalised_comparison(
    hass, accounts_payload, metric, value, average, change, unit
) -> None:
    periods = {
        "last_month": PeriodSummary(
            date(2026, 3, 1), date(2026, 4, 1), 744, 744, 744, 93, 31, "NZD"
        ),
        "previous_month": PeriodSummary(
            date(2026, 2, 1), date(2026, 3, 1), 672, 672, 672, 56, 56, "NZD"
        ),
    }
    statistics = StatisticsResult(0, 0, "NZD", periods=periods)
    sensor = _sensor(hass, accounts_payload, f"last_month_{metric}", statistics)

    assert sensor.native_value == value
    assert sensor.native_unit_of_measurement == unit
    assert sensor.state_class is None
    assert sensor.extra_state_attributes == {
        "period_start": "2026-03-01",
        "period_end_exclusive": "2026-04-01",
        "expected_hours": 744,
        "received_hours": 744,
        "cost_hours": 744,
        "daily_average": average,
        "change_percent": change,
        "comparison_basis": "daily_average",
        "comparison_start": "2026-02-01",
        "comparison_end_exclusive": "2026-03-01",
    }
    assert TEST_ICP not in json.dumps(sensor.extra_state_attributes)


def test_incomplete_period_does_not_expose_partial_totals(hass, accounts_payload):
    periods = {
        "yesterday": PeriodSummary(
            date(2026, 8, 8), date(2026, 8, 9), 24, 23, 22, None, None, "NZD"
        ),
    }
    for metric in ("energy", "cost"):
        sensor = _sensor(
            hass,
            accounts_payload,
            f"yesterday_{metric}",
            StatisticsResult(0, 0, "NZD", periods=periods),
        )
        assert sensor.native_value is None
        assert sensor.extra_state_attributes["received_hours"] == 23
        assert sensor.extra_state_attributes["cost_hours"] == 22
        assert sensor.extra_state_attributes["daily_average"] is None
        assert sensor.extra_state_attributes["change_percent"] is None


def test_cost_comparison_requires_matching_currency(hass, accounts_payload):
    periods = {
        "yesterday": PeriodSummary(
            date(2026, 8, 8), date(2026, 8, 9), 24, 24, 24, 24, 12, "AUD"
        ),
        "previous_day": PeriodSummary(
            date(2026, 8, 7), date(2026, 8, 8), 24, 24, 24, 24, 10, "NZD"
        ),
    }
    sensor = _sensor(
        hass,
        accounts_payload,
        "yesterday_cost",
        StatisticsResult(0, 0, "NZD", periods=periods),
    )
    assert sensor.native_unit_of_measurement == "AUD"
    assert sensor.native_value == 12
    assert sensor.extra_state_attributes["change_percent"] is None


@pytest.mark.parametrize(
    "key",
    [
        "yesterday_energy",
        "yesterday_free_energy",
        "yesterday_cost",
        "last_month_energy",
        "last_month_cost",
        "last_month_free_energy",
        "last_month_paid_energy",
        "latest_day_date",
        "latest_day_energy",
        "latest_day_free_energy",
        "latest_day_paid_energy",
        "latest_day_cost",
        "month_to_date_energy",
        "month_to_date_cost",
        "rolling_7_days_energy",
        "rolling_7_days_cost",
        "rolling_30_days_energy",
        "rolling_30_days_cost",
    ],
)
def test_missing_summary_is_unknown(hass, accounts_payload, key):
    sensor = _sensor(hass, accounts_payload, key, StatisticsResult(0, 0, None))
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


@pytest.mark.parametrize(
    ("total", "free", "percentage"),
    [(24, 6, 25), (24, 0, 0), (0, 0, None), (24, None, None), (None, None, None)],
)
def test_free_sensor_reports_snapshot_and_percentage_without_double_counting(
    hass, accounts_payload, total, free, percentage
):
    period = PeriodSummary(
        date(2026, 8, 8),
        date(2026, 8, 9),
        24,
        24 if total is not None else 23,
        0,
        total,
        None,
        None,
        free_energy=free,
        free_hours=24 if free is not None else 23,
    )
    sensor = _sensor(
        hass,
        accounts_payload,
        "yesterday_free_energy",
        StatisticsResult(0, 0, "NZD", periods={"yesterday": period}),
    )
    assert sensor.native_value == free
    assert sensor.native_unit_of_measurement == "kWh"
    assert sensor.state_class is None
    assert sensor.extra_state_attributes["free_percentage"] == percentage
    assert sensor.extra_state_attributes["free_hours"] == period.free_hours
    assert "daily_average" not in sensor.extra_state_attributes


@pytest.mark.parametrize("period_key", ["latest_day", "last_month"])
@pytest.mark.parametrize(
    ("metric", "value"),
    [("energy", 24), ("free_energy", 6), ("paid_energy", 18), ("cost", 9)],
)
def test_daily_and_monthly_sensors_expose_the_same_period(
    hass, accounts_payload, period_key, metric, value
):
    start, end = date(2026, 8, 1), date(2026, 9, 1)
    period = PeriodSummary(start, end, 744, 744, 744, 24, 9, "NZD", 6, 744)
    sensor = _sensor(
        hass,
        accounts_payload,
        f"{period_key}_{metric}",
        StatisticsResult(0, 0, "NZD", periods={period_key: period}),
    )
    assert sensor.native_value == value
    assert sensor.state_class is None
    assert sensor.extra_state_attributes["period_start"] == start.isoformat()
    assert sensor.extra_state_attributes["period_end_exclusive"] == end.isoformat()
    if period_key == "latest_day" or metric in ("free_energy", "paid_energy"):
        assert sensor.extra_state_attributes["total_kwh"] == 24
        assert sensor.extra_state_attributes["free_kwh"] == 6
        assert sensor.extra_state_attributes["paid_kwh"] == 18
        assert sensor.extra_state_attributes["free_percentage"] == 25


@pytest.mark.parametrize(
    ("period_key", "previous_key"),
    [
        ("month_to_date", "previous_month_to_date"),
        ("rolling_7_days", "previous_7_days"),
        ("rolling_30_days", "previous_30_days"),
    ],
)
@pytest.mark.parametrize(("metric", "value"), [("energy", 48), ("cost", 12)])
def test_covered_comparisons_report_dates_breakdown_and_change(
    hass, accounts_payload, period_key, previous_key, metric, value
):
    current = PeriodSummary(
        date(2026, 8, 1), date(2026, 8, 3), 48, 48, 48, 48, 12, "NZD", 6, 48
    )
    previous = PeriodSummary(
        date(2026, 7, 1), date(2026, 7, 3), 48, 48, 48, 24, 6, "NZD"
    )
    sensor = _sensor(
        hass,
        accounts_payload,
        f"{period_key}_{metric}",
        StatisticsResult(
            0, 0, "NZD", periods={period_key: current, previous_key: previous}
        ),
    )
    assert sensor.native_value == value
    assert sensor.state_class is None
    assert sensor.extra_state_attributes["covered_through"] == "2026-08-02"
    assert sensor.extra_state_attributes["comparison_end_exclusive"] == "2026-07-03"
    assert sensor.extra_state_attributes["change_percent"] == 100
    assert sensor.extra_state_attributes["free_kwh"] == 6
    assert sensor.extra_state_attributes["paid_kwh"] == 42


def test_complete_day_observation_is_not_claimed_as_provider_publication(
    hass, accounts_payload
):
    period = PeriodSummary(
        date(2026, 8, 1), date(2026, 8, 2), 24, 24, 0, 24, None, None
    )
    # Day ends at 12:00 UTC; it was first observed complete 28 hours later.
    observed = datetime(2026, 8, 2, 16, tzinfo=UTC)
    sensor = _sensor(
        hass,
        accounts_payload,
        "latest_day_date",
        StatisticsResult(
            0,
            0,
            None,
            periods={"latest_day": period},
            observation_started=observed - timedelta(hours=1),
            first_complete_observed=observed,
        ),
    )
    attrs = sensor.extra_state_attributes
    assert attrs["first_observed_complete"] == observed.isoformat()
    assert attrs["age_hours_at_first_observation"] == 28
    assert attrs["period_predates_tracking"] is True
    assert "publication_time" not in attrs
