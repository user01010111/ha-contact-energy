"""Tests for response validation and selected-contract parsing."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.contact_energy.api import MalformedResponse
from custom_components.contact_energy.models import (
    parse_account_snapshot,
    parse_contracts,
    parse_usage_points,
)

from .conftest import TEST_CONTRACT_ID


def test_contracts_and_selected_meter_dates(accounts_payload) -> None:
    contracts = parse_contracts(accounts_payload)
    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)

    assert len(contracts) == 2
    assert snapshot.previous_reading_date == date(2026, 8, 2)
    assert snapshot.next_reading_date == date(2026, 9, 2)
    assert snapshot.account_balance == 12.34


def test_malformed_optional_fields_do_not_raise(accounts_payload) -> None:
    detail = accounts_payload["accountDetail"]
    detail["accountBalance"]["currentBalance"] = "not-a-number"
    detail["nextBill"]["date"] = None
    selected = detail["contracts"][1]
    selected["devices"][0]["registers"] = []

    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)

    assert snapshot.account_balance is None
    assert snapshot.next_bill_date is None
    assert snapshot.previous_reading_date is None


def test_usage_parser_validates_fields_and_ignores_offpeak_semantics() -> None:
    points = parse_usage_points(
        [
            {
                "date": "2026-08-01T01:00:00+12:00",
                "value": "1.25",
                "dollarValue": "0.40",
                "offpeakValue": "99.99",
                "currency": "nzd",
                "unit": "kWh",
            },
            {"date": "not-a-date", "value": "2.0"},
            {"date": "2026-08-01T02:00:00+12:00", "value": "nan"},
            {"date": "2026-08-01T03:00:00+12:00", "value": "-1"},
            {
                "date": "2026-08-01T04:00:00+12:00",
                "value": "1.0",
                "dollarValue": "0.2",
                "unit": "m3",
            },
        ]
    )

    assert len(points) == 1
    assert points[0].energy == 1.25
    assert points[0].cost == 0.4
    assert points[0].currency == "NZD"


def test_usage_cost_requires_a_valid_currency() -> None:
    points = parse_usage_points(
        [
            {
                "date": "2026-08-01T01:00:00+12:00",
                "value": "1.0",
                "dollarValue": "0.2",
                "currency": "not-a-currency",
                "unit": "kWh",
            }
        ]
    )

    assert points[0].energy == 1.0
    assert points[0].cost is None


def test_integer_contract_identifier_round_trips(accounts_payload) -> None:
    contract = accounts_payload["accountDetail"]["contracts"][1]
    contract["id"] = 42

    parsed = parse_contracts(accounts_payload)
    snapshot = parse_account_snapshot(accounts_payload, parsed[1].contract_id)

    assert parsed[1].contract_id == "42"
    assert snapshot.next_reading_date == date(2026, 9, 2)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-01T01:30:00+12:00",
        "2026-08-01T01:00:01+12:00",
        "2026-08-01T01:00:00.100000+12:00",
        "2026-08-01T01:00:00",
    ],
)
def test_usage_parser_rejects_non_hourly_timestamps(timestamp) -> None:
    with pytest.raises(MalformedResponse):
        parse_usage_points([{"date": timestamp, "value": "1.0"}])


def test_usage_parser_rejects_points_outside_requested_date() -> None:
    with pytest.raises(MalformedResponse):
        parse_usage_points(
            [{"date": "2026-08-02T01:00:00+12:00", "value": "1.0"}],
            date(2026, 8, 1),
        )


def test_usage_parser_tolerates_one_bad_record_when_valid_data_remains() -> None:
    points = parse_usage_points(
        [
            {"date": "invalid", "value": "1.0"},
            {"date": "2026-08-01T01:00:00+12:00", "value": "2.0"},
        ]
    )

    assert [point.energy for point in points] == [2.0]
