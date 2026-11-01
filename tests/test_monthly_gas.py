"""Calendar gas reports are dated snapshots, not billed or cumulative usage."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder.db_schema import StateAttributes
from homeassistant.const import Platform
from homeassistant.core import Event
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.contact_energy import _contract_key
from custom_components.contact_energy.api import (
    API_BASE_URL,
    CannotConnect,
    MalformedResponse,
    RetryableApiError,
)
from custom_components.contact_energy.const import DOMAIN
from custom_components.contact_energy.coordinator import ContactEnergyCoordinator
from custom_components.contact_energy.models import (
    CONTACT_TIME_ZONE,
    parse_monthly_gas_usage,
)
from custom_components.contact_energy.sensor import (
    GAS_SENSOR_DESCRIPTIONS,
    ContactEnergySensor,
)

from .conftest import TEST_ACCOUNT_ID, TEST_CONTRACT_ID, TEST_ICP
from .test_api import LOGIN_URL, _api


def row(month=9, value="100", year=2026):
    return {
        "year": year,
        "month": month,
        "day": 1,
        "hour": 0,
        "date": datetime(year, month, 1, tzinfo=CONTACT_TIME_ZONE).isoformat(),
        "value": value,
        "unit": "kWh",
        "timeZone": "Pacific/Auckland",
        "dollarValue": None,
    }


def parse(payload):
    return parse_monthly_gas_usage(payload, date(2025, 10, 1), date(2026, 10, 1))


def test_monthly_gas_sorts_clips_and_preserves_unknown_and_zero():
    result = parse([row(9, "0"), row(8, None), row(10, "999")])
    assert [(p.start, p.energy) for p in result] == [
        (date(2026, 8, 1), None),
        (date(2026, 9, 1), 0),
    ]
    assert parse([]) == ()
    assert parse([row(9, "12.5")])[0].energy == 12.5


@pytest.mark.parametrize(
    "key,value",
    [
        ("value", "NaN"),
        ("value", "inf"),
        ("value", "wrong"),
        ("value", -1),
        ("value", True),
        ("unit", "m3"),
        ("unit", None),
        ("date", "2026-09-02"),
        ("date", "2026-09-01T13:00:00+12:00"),
        ("date", "2026-09-01T00:00:00Z"),
        ("date", "2026-08-01T00:00:00+12:00"),
        ("month", True),
        ("year", "2026"),
        ("day", 2),
        ("hour", 1),
        ("timeZone", "UTC"),
    ],
)
def test_malformed_monthly_gas_rejected(key, value):
    point = row()
    point[key] = value
    with pytest.raises(MalformedResponse):
        parse([point])


@pytest.mark.parametrize("payload", [None, {}, [None], [row(), row()], [row()] * 14])
def test_monthly_gas_shape_and_duplicates_rejected(payload):
    with pytest.raises(MalformedResponse):
        parse(payload)


async def test_monthly_gas_endpoint_and_empty_response(hass, aioclient_mock):
    aioclient_mock.post(LOGIN_URL, json={"token": "session-example"})
    url = (
        f"{API_BASE_URL}/usage/v2/{TEST_CONTRACT_ID}?ba={TEST_ACCOUNT_ID}"
        "&interval=monthly&from=2025-10-01&to=2026-10-01"
    )
    aioclient_mock.post(url, json=[row()])
    assert await _api(hass).async_get_monthly_gas_usage("2025-10-01", "2026-10-01") == [
        row()
    ]


@pytest.mark.parametrize("status", [204, 404])
async def test_monthly_gas_empty_status_is_unknown_not_zero(
    hass, aioclient_mock, status
):
    aioclient_mock.post(LOGIN_URL, json={"token": "session-example"})
    url = (
        f"{API_BASE_URL}/usage/v2/{TEST_CONTRACT_ID}?ba={TEST_ACCOUNT_ID}"
        "&interval=monthly&from=2025-10-01&to=2026-10-01"
    )
    aioclient_mock.post(url, status=status)
    assert (
        await _api(hass).async_get_monthly_gas_usage("2025-10-01", "2026-10-01") == []
    )


@pytest.mark.parametrize("payload", [{}, "wrong", [None]])
async def test_monthly_gas_endpoint_rejects_malformed_body(
    hass, aioclient_mock, payload
):
    aioclient_mock.post(LOGIN_URL, json={"token": "session-example"})
    url = (
        f"{API_BASE_URL}/usage/v2/{TEST_CONTRACT_ID}?ba={TEST_ACCOUNT_ID}"
        "&interval=monthly&from=2025-10-01&to=2026-10-01"
    )
    aioclient_mock.post(url, json=payload)
    with pytest.raises(MalformedResponse):
        await _api(hass).async_get_monthly_gas_usage("2025-10-01", "2026-10-01")


@pytest.mark.parametrize(
    "start,end",
    [
        ("2024-10-01", "2026-10-01"),
        ("2026-10-02", "2026-11-01"),
        ("2026-11-01", "2026-10-01"),
        ("bad", "2026-10-01"),
    ],
)
async def test_monthly_request_bound_is_enforced(hass, start, end):
    with pytest.raises(MalformedResponse):
        await _api(hass).async_get_monthly_gas_usage(start, end)


@pytest.mark.parametrize("error", [CannotConnect(), RetryableApiError(429, 12 * 3600)])
async def test_optional_gas_history_cadence_failure_correction_and_rollover(
    hass, mock_config_entry, accounts_payload, monkeypatch, error
):
    now = datetime(2026, 10, 1, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, "contract_type": 2}
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_bill_transactions.return_value = {"transactions": []}
    api.async_get_plan_details.return_value = {}
    api.async_get_monthly_gas_usage.return_value = [row()]
    coordinator = ContactEnergyCoordinator(hass, mock_config_entry, api, None)
    data = await coordinator._async_update_data()
    coordinator.async_set_updated_data(data)
    sensor = ContactEnergySensor(
        coordinator,
        "opaque-example",
        TEST_ICP,
        next(d for d in GAS_SENSOR_DESCRIPTIONS if d.key == "monthly_gas_usage"),
        2,
    )
    assert sensor.native_value == 100
    assert sensor.state_class is None
    assert sensor.extra_state_attributes["period_start"] == "2026-09-01"
    assert sensor.extra_state_attributes["period_end_exclusive"] == "2026-10-01"
    assert "monthly" in sensor._unrecorded_attributes
    assert api.async_get_monthly_gas_usage.call_args.args == (
        "2025-10-01",
        "2026-10-01",
    )
    now += timedelta(hours=8)
    await coordinator._async_update_data()
    assert api.async_get_monthly_gas_usage.await_count == 1
    now += timedelta(hours=16)
    api.async_get_monthly_gas_usage.side_effect = error
    failed = await coordinator._async_update_data()
    assert failed.gas_monthly == data.gas_monthly
    assert failed.gas_monthly_updated == data.gas_monthly_updated
    assert failed.gas_bill_valid and failed.gas_monthly_valid
    api.async_get_monthly_gas_usage.side_effect = None
    api.async_get_monthly_gas_usage.return_value = [row(value="90")]
    retry = max(8 * 3600, getattr(error, "retry_after", None) or 0)
    now += timedelta(seconds=retry - 1)
    await coordinator._async_update_data()
    assert api.async_get_monthly_gas_usage.await_count == 2
    now += timedelta(seconds=1)
    data = await coordinator._async_update_data()
    coordinator.async_set_updated_data(data)
    assert sensor.native_value == 90
    assert api.async_get_monthly_gas_usage.await_count == 3
    now = datetime(2026, 11, 1, tzinfo=UTC)
    assert sensor.native_value is None  # Never relabel September as October.
    data = await coordinator._async_update_data()
    coordinator.async_set_updated_data(data)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["period_start"] == "2026-10-01"
    api.async_get_monthly_gas_usage.return_value = [row(value="bad")]
    now += timedelta(days=1)
    data = await coordinator._async_update_data()
    coordinator.async_set_updated_data(data)
    assert not sensor.available and data.gas_bill_valid
    assert data.gas_monthly == ()


def test_monthly_gas_does_not_accept_bill_quality_or_cost_as_month_data():
    point = deepcopy(row())
    point.update(dollarValue="45", readingType="Actual")
    result = parse([point])[0]
    assert not hasattr(result, "cost") and not hasattr(result, "reading_quality")


async def test_monthly_gas_real_entity_reload_and_recorder_exclusion(
    hass, mock_config_entry, accounts_payload, recorder_dependency, monkeypatch
):
    # Contact has entered October while UTC remains in September.
    now = datetime(2026, 9, 30, 12, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, "contract_type": 2}
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_bill_transactions.return_value = {"transactions": []}
    api.async_get_plan_details.return_value = {}
    api.async_get_monthly_gas_usage.return_value = [row()]
    with patch("custom_components.contact_energy.ContactEnergyApi", return_value=api):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        entity_id = er.async_get(hass).async_get_entity_id(
            Platform.SENSOR,
            DOMAIN,
            f"{_contract_key(mock_config_entry)}_monthly_gas_usage",
        )
        state = hass.states.get(entity_id)
        assert state.state == "100.0"
        assert state.attributes["period_start"] == "2026-09-01"
        assert state.attributes["monthly"] == [
            {"start": "2026-09-01", "energy_kwh": 100}
        ]
        assert "state_class" not in state.attributes
        encoded = json.loads(
            StateAttributes.shared_attrs_bytes_from_event(
                Event("state_changed", {"new_state": state}), None
            )
        )
        assert "monthly" not in encoded
        assert "contract_id" not in state.attributes and "icp" not in state.attributes
        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(entity_id).state == state.state
        assert api.async_get_monthly_gas_usage.await_count == 2
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
