"""Optional plan information is scoped, bounded and never a tariff assumption."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contact_energy.api import (
    API_BASE_URL,
    CannotConnect,
    InvalidAuth,
    MalformedResponse,
    RetryableApiError,
    UnexpectedResponse,
)
from custom_components.contact_energy.coordinator import ContactEnergyCoordinator
from custom_components.contact_energy.models import parse_plan_name
from custom_components.contact_energy.sensor import (
    GAS_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    ContactEnergySensor,
)

from .conftest import TEST_ACCOUNT_ID, TEST_CONTRACT_ID, TEST_ICP
from .test_api import LOGIN_URL, _api
from .test_coordinator import FakeStatistics


@pytest.fixture
def plan_payload():
    return {
        "accountId": TEST_ACCOUNT_ID,
        "premises": [
            {
                "services": [
                    {
                        "contract": {"contractId": "other-contract"},
                        "planDetails": {"externalPlanDescription": "Other plan"},
                    },
                    {
                        "contract": {"contractId": TEST_CONTRACT_ID},
                        "planDetails": {"externalPlanDescription": "Example plan"},
                    },
                ]
            }
        ],
    }


def test_plan_selection_and_duplicate_contracts(plan_payload):
    assert parse_plan_name(plan_payload, TEST_ACCOUNT_ID, TEST_CONTRACT_ID) == (
        "Example plan"
    )
    services = plan_payload["premises"][0]["services"]
    services.append(deepcopy(services[-1]))
    assert parse_plan_name(plan_payload, TEST_ACCOUNT_ID, TEST_CONTRACT_ID) is None
    services.clear()
    assert parse_plan_name(plan_payload, TEST_ACCOUNT_ID, TEST_CONTRACT_ID) is None


@pytest.mark.parametrize("name", [None, True, {}, "", "  ", "x" * 256, "a\nb"])
def test_invalid_plan_labels_stay_unknown(plan_payload, name):
    plan_payload["premises"][0]["services"][1]["planDetails"][
        "externalPlanDescription"
    ] = name
    assert parse_plan_name(plan_payload, TEST_ACCOUNT_ID, TEST_CONTRACT_ID) is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"accountId": "wrong-account", "premises": []},
        {"accountId": TEST_ACCOUNT_ID, "premises": None},
        {"accountId": TEST_ACCOUNT_ID, "premises": [None]},
        {"accountId": TEST_ACCOUNT_ID, "premises": [{"services": [None]}]},
    ],
)
def test_malformed_plan_ownership_and_structure_rejected(payload):
    with pytest.raises(MalformedResponse):
        parse_plan_name(payload, TEST_ACCOUNT_ID, TEST_CONTRACT_ID)


async def test_plan_endpoint_uses_existing_session(hass, aioclient_mock, plan_payload):
    aioclient_mock.post(LOGIN_URL, json={"token": "session-example"})
    aioclient_mock.get(
        f"{API_BASE_URL}/usage/v2/plan-details?ba={TEST_ACCOUNT_ID}", json=plan_payload
    )
    api = _api(hass)
    assert await api.async_get_plan_details() == plan_payload
    assert await api.async_get_plan_details() == plan_payload
    assert aioclient_mock.call_count == 3  # One login, two reads.


@pytest.mark.parametrize("payload", [[], None, "not-a-mapping"])
async def test_plan_endpoint_rejects_non_mapping(hass, aioclient_mock, payload):
    aioclient_mock.post(LOGIN_URL, json={"token": "session-example"})
    aioclient_mock.get(
        f"{API_BASE_URL}/usage/v2/plan-details?ba={TEST_ACCOUNT_ID}", json=payload
    )
    with pytest.raises(MalformedResponse):
        await _api(hass).async_get_plan_details()


@pytest.mark.parametrize(
    "error",
    [
        InvalidAuth(),
        CannotConnect(),
        MalformedResponse(),
        RetryableApiError(429, 60),
        RetryableApiError(429, 12 * 3600),
        UnexpectedResponse(403),
    ],
)
async def test_optional_plan_failure_does_not_disable_primary_data_or_loop(
    hass, mock_config_entry, accounts_payload, monkeypatch, plan_payload, error
):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.return_value = []
    api.async_get_plan_details.side_effect = [plan_payload, error, plan_payload]
    coordinator = ContactEnergyCoordinator(
        hass, mock_config_entry, api, FakeStatistics()
    )
    coordinator.enable_history_fetches()
    assert (await coordinator._async_update_data()).plan_name == "Example plan"
    now += timedelta(hours=8)
    assert (await coordinator._async_update_data()).plan_name == "Example plan"
    assert api.async_get_plan_details.await_count == 1
    now += timedelta(hours=16)
    data = await coordinator._async_update_data()
    transient = isinstance(error, (InvalidAuth, CannotConnect, RetryableApiError))
    assert data.plan_name == ("Example plan" if transient else None)
    assert data.account.account_balance == 12.34
    assert data.statistics.energy == 0
    await coordinator._async_update_data()
    assert api.async_get_plan_details.await_count == 2
    now += timedelta(hours=8)
    await coordinator._async_update_data()
    retry_due = transient and (getattr(error, "retry_after", None) or 0) <= 8 * 3600
    assert api.async_get_plan_details.await_count == (3 if retry_due else 2)
    now += timedelta(hours=16)
    data = await coordinator._async_update_data()
    assert data.plan_name == "Example plan"
    coordinator.async_set_updated_data(data)
    for descriptions in (SENSOR_DESCRIPTIONS, GAS_SENSOR_DESCRIPTIONS):
        description = next(d for d in descriptions if d.key == "plan_name")
        sensor = ContactEnergySensor(coordinator, "opaque-key", TEST_ICP, description)
        assert sensor.native_value == "Example plan"
        assert sensor.extra_state_attributes is None


@pytest.mark.parametrize("contract_type", [1, 2])
@pytest.mark.parametrize(
    "error",
    [
        CannotConnect(),
        InvalidAuth(),
        RetryableApiError(503, None),
        RetryableApiError(429, None),
    ],
)
async def test_repeated_plan_failure_does_not_starve_other_optional_lookups(
    hass, mock_config_entry, accounts_payload, monkeypatch, contract_type, error
):
    now = datetime(2026, 10, 1, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, "contract_type": contract_type},
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.return_value = []
    api.async_get_plan_details.side_effect = error
    api.async_get_bill_transactions.return_value = {"transactions": []}
    api.async_get_monthly_gas_usage.return_value = []
    coordinator = ContactEnergyCoordinator(
        hass, mock_config_entry, api, FakeStatistics() if contract_type == 1 else None
    )
    coordinator.enable_history_fetches()
    for hours in (0, 8, 16):
        now += timedelta(hours=hours)
        await coordinator._async_update_data()
    assert api.async_get_plan_details.await_count == 3
    rate_limited = isinstance(error, RetryableApiError) and error.status == 429
    optional = (
        api.async_get_bill_transactions
        if contract_type == 1
        else api.async_get_monthly_gas_usage
    )
    assert optional.await_count == (0 if rate_limited else 2)
