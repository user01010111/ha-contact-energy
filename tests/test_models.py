"""Tests for response validation and selected-contract parsing."""

from copy import deepcopy
from datetime import date, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contact_energy.api import MalformedResponse
from custom_components.contact_energy.models import (
    parse_account_snapshot,
    parse_contracts,
    parse_usage_points,
)
from custom_components.contact_energy.statistics import ContactEnergyStatistics

from .conftest import TEST_CONTRACT_ID, TEST_ICP
from .test_statistics import FakeStore


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


@pytest.mark.parametrize("value", ["20260902", "2026-09-02", "02 Sep 2026"])
def test_meter_date_formats_and_estimate_flag(accounts_payload, value):
    device = accounts_payload["accountDetail"]["contracts"][1]["devices"][0]
    device["nextMeterReadDate"] = value
    device["nextReadingIsEstimate"] = False
    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)
    assert snapshot.next_reading_date == date(2026, 9, 2)
    assert snapshot.next_reading_is_estimate is False


@pytest.mark.parametrize("value", [None, "true", 1, {}, []])
def test_estimate_flag_requires_boolean(accounts_payload, value):
    accounts_payload["accountDetail"]["contracts"][1]["devices"][0][
        "nextReadingIsEstimate"
    ] = value
    assert (
        parse_account_snapshot(
            accounts_payload, TEST_CONTRACT_ID
        ).next_reading_is_estimate
        is None
    )


@pytest.mark.parametrize(
    "invalid", ["different", "missing", "malformed", "no_registers"]
)
def test_multiple_meters_do_not_choose_arbitrary_dates(accounts_payload, invalid):
    devices = accounts_payload["accountDetail"]["contracts"][1]["devices"]
    devices[0]["nextReadingIsEstimate"] = True
    devices.append(deepcopy(devices[0]))
    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)
    assert snapshot.previous_reading_date == date(2026, 8, 2)
    assert snapshot.next_reading_date == date(2026, 9, 2)
    value = "03 Sep 2026" if invalid == "different" else None
    devices[1]["nextMeterReadDate"] = value
    devices[1]["nextReadingIsEstimate"] = False
    devices[1]["registers"][0]["previousMeterReadingDate"] = value
    if invalid == "malformed":
        devices[1] = None
    elif invalid == "no_registers":
        devices[1]["registers"] = []
    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)
    assert snapshot.previous_reading_date is None
    assert snapshot.next_reading_date is None
    assert snapshot.next_reading_is_estimate is None


def test_conflicting_register_dates_are_unknown(accounts_payload):
    registers = accounts_payload["accountDetail"]["contracts"][1]["devices"][0][
        "registers"
    ]
    registers.append({"previousMeterReadingDate": "01 Aug 2026"})
    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)
    assert snapshot.previous_reading_date is None
    assert snapshot.next_reading_date == date(2026, 9, 2)


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


async def test_negative_usage_cost_preserves_energy_and_previous_cost(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda *_args: None,
    )
    stamp = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    statistics = ContactEnergyStatistics(
        hass, "entry", "contract", TEST_ICP, FakeStore()
    )
    original = {
        "date": (stamp - timedelta(hours=2)).isoformat(),
        "value": 1,
        "dollarValue": 0.25,
        "currency": "NZD",
    }
    await statistics.async_process(parse_usage_points([original]))

    corrected = {**original, "value": 1.5, "dollarValue": -0.1}
    new = {
        **original,
        "date": (stamp - timedelta(hours=1)).isoformat(),
        "value": 2,
        "dollarValue": -0.2,
    }
    points = parse_usage_points([corrected, new])
    result = await statistics.async_process(points)

    assert [point.cost for point in points] == [None, None]
    assert result.energy == 3.5
    assert result.cost == 0.25


def test_negative_account_balance_remains_valid(accounts_payload) -> None:
    accounts_payload["accountDetail"]["accountBalance"]["currentBalance"] = -12.34

    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)

    assert snapshot.account_balance == -12.34


def test_unrepresentable_optional_numbers_are_missing(accounts_payload) -> None:
    accounts_payload["accountDetail"]["accountBalance"]["currentBalance"] = 10**400
    points = parse_usage_points(
        [
            {
                "date": "2026-08-01T01:00:00+12:00",
                "value": 1,
                "dollarValue": 10**400,
                "currency": "NZD",
            },
            {"date": "2026-08-01T02:00:00+12:00", "value": 10**400},
        ]
    )
    snapshot = parse_account_snapshot(accounts_payload, TEST_CONTRACT_ID)

    assert snapshot.account_balance is None
    assert len(points) == 1
    assert points[0].energy == 1
    assert points[0].cost is None
