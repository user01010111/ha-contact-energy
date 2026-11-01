"""Synthetic current electricity invoices, including omitted tariff prices."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contact_energy import _contract_key
from custom_components.contact_energy.api import (
    CannotConnect,
    InvalidAuth,
    MalformedResponse,
    RetryableApiError,
)
from custom_components.contact_energy.billing import parse_electricity_bill
from custom_components.contact_energy.const import DOMAIN
from custom_components.contact_energy.coordinator import ContactEnergyCoordinator
from custom_components.contact_energy.sensor import (
    SENSOR_DESCRIPTIONS,
    ContactEnergySensor,
)

from .conftest import TEST_ACCOUNT_ID, TEST_CONTRACT_ID, TEST_ICP
from .test_coordinator import FakeStatistics


@pytest.fixture
def electricity_bill_payload():
    return {
        "AccountNumber": TEST_ACCOUNT_ID,
        "InvoiceNumber": "invoice-example",
        "isCurrentBill": True,
        "TechnicalDetails": [{"Version": "1.0"}],
        "Installations": [
            {
                "Contract": TEST_CONTRACT_ID,
                "ICP": TEST_ICP,
                "Product": "Electricity",
                "Premise": "premise-example",
                "ValidFrom": "2026-08-01",
                "ValidTo": "2026-08-31",
                "Days": "31",
            }
        ],
        "ClassificationDetails": [
            {
                "Contract": TEST_CONTRACT_ID,
                "Premise": "premise-example",
                "Classification": "001",
                "AmountExclGST": "80",
                "GSTAmount": "12",
                "AmountInclGST": "92",
            }
        ],
        "Registers": [],
        "VariableCharges": [{"AmountExclGST": None, "Price": None}],
        "Payments": [{"amount": "900"}],
        "OpeningBalance": "1000",
    }


def parse(payload):
    return parse_electricity_bill(
        payload,
        account_id=TEST_ACCOUNT_ID,
        contract_id=TEST_CONTRACT_ID,
        icp=TEST_ICP,
        invoice_id="invoice-example",
    )


def test_electricity_charges_use_service_totals_not_balance_or_null_lines(
    electricity_bill_payload,
):
    bill = parse(electricity_bill_payload)
    assert (bill.cost, bill.cost_excl_gst, bill.gst) == (92, 80, 12)
    assert bill.days == 31
    assert bill.start.isoformat() == "2026-08-01"
    assert bill.end.isoformat() == "2026-08-31"
    assert "example" not in repr(bill)
    assert not hasattr(bill, "energy")
    assert not hasattr(bill, "free_energy")


@pytest.mark.parametrize(
    "key,value",
    [("AccountNumber", "wrong"), ("InvoiceNumber", "wrong"), ("isCurrentBill", False)],
)
def test_current_electricity_bill_identity_is_mandatory(
    electricity_bill_payload, key, value
):
    electricity_bill_payload[key] = value
    with pytest.raises(MalformedResponse):
        parse(electricity_bill_payload)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("TechnicalDetails", "Version", "2.0"),
        ("Installations", "Contract", "other-contract"),
        ("Installations", "ICP", "other-icp"),
        ("Installations", "Product", "Natural Gas"),
        ("Installations", "Days", "30"),
        ("Installations", "ValidTo", "invalid"),
        ("ClassificationDetails", "Premise", "wrong-premise"),
        ("ClassificationDetails", "Premise", None),
        ("ClassificationDetails", "Contract", "wrong-contract"),
        ("ClassificationDetails", "Classification", "002"),
        ("ClassificationDetails", "GSTAmount", "13"),
        ("ClassificationDetails", "AmountExclGST", "-1"),
        ("ClassificationDetails", "AmountInclGST", "NaN"),
    ],
)
def test_inconsistent_electricity_charges_rejected(
    electricity_bill_payload, section, key, value
):
    electricity_bill_payload[section][0][key] = value
    with pytest.raises(MalformedResponse):
        parse(electricity_bill_payload)


@pytest.mark.parametrize("section", ["Installations", "ClassificationDetails"])
def test_duplicate_bill_selections_rejected(electricity_bill_payload, section):
    electricity_bill_payload[section].append(
        deepcopy(electricity_bill_payload[section][0])
    )
    with pytest.raises(MalformedResponse):
        parse(electricity_bill_payload)


def test_zero_electricity_charges_are_valid(electricity_bill_payload):
    for key in ("AmountExclGST", "AmountInclGST", "GSTAmount"):
        electricity_bill_payload["ClassificationDetails"][0][key] = "0"
    assert parse(electricity_bill_payload).cost == 0


def test_missing_premises_do_not_establish_bill_ownership(electricity_bill_payload):
    for section in ("Installations", "ClassificationDetails"):
        electricity_bill_payload[section][0].pop("Premise")
    with pytest.raises(MalformedResponse):
        parse(electricity_bill_payload)


async def test_electricity_bill_state_survives_entry_reload(
    hass,
    mock_config_entry,
    accounts_payload,
    electricity_bill_payload,
    recorder_dependency,
    monkeypatch,
):
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    mock_config_entry.add_to_hass(hass)
    prefix = "custom_components.contact_energy.ContactEnergyApi."
    with (
        patch(prefix + "async_get_accounts", AsyncMock(return_value=accounts_payload)),
        patch(prefix + "async_get_usage", AsyncMock(return_value=[])),
        patch(prefix + "async_get_plan_details", AsyncMock(return_value={})),
        patch(
            prefix + "async_get_bill_transactions",
            AsyncMock(
                return_value={
                    "transactions": [
                        {"date": "2026-09-01", "invoiceId": "invoice-example"}
                    ]
                }
            ),
        ),
        patch(
            prefix + "async_get_bill", AsyncMock(return_value=electricity_bill_payload)
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        entity_id = er.async_get(hass).async_get_entity_id(
            Platform.SENSOR,
            DOMAIN,
            f"{_contract_key(mock_config_entry)}_billed_electricity_cost",
        )
        state = hass.states.get(entity_id)
        assert float(state.state) == 92
        assert state.attributes["unit_of_measurement"] == "NZD"
        assert state.attributes["includes_gst"] is True
        assert state.attributes["gst"] == 12
        assert "state_class" not in state.attributes
        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(entity_id).state == state.state
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_optional_electricity_bill_cadence_failure_and_recovery(
    hass, mock_config_entry, accounts_payload, electricity_bill_payload, monkeypatch
):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.return_value = []
    api.async_get_plan_details.return_value = {}
    api.async_get_bill_transactions.return_value = {
        "transactions": [{"date": "2026-09-01", "invoiceId": "invoice-example"}]
    }
    api.async_get_bill.return_value = electricity_bill_payload
    coordinator = ContactEnergyCoordinator(
        hass, mock_config_entry, api, FakeStatistics()
    )
    coordinator.enable_history_fetches()
    data = await coordinator._async_update_data()
    assert data.electricity_bill.cost == 92
    coordinator.async_set_updated_data(data)
    sensor = ContactEnergySensor(
        coordinator,
        "opaque-key",
        TEST_ICP,
        next(d for d in SENSOR_DESCRIPTIONS if d.key == "billed_electricity_cost"),
    )
    assert sensor.native_value == 92
    assert sensor.native_unit_of_measurement == "NZD"
    assert sensor.state_class is None
    assert sensor.extra_state_attributes == {
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "days": 31,
        "includes_gst": True,
        "charges_excl_gst": 80,
        "gst": 12,
    }
    now += timedelta(hours=8)
    await coordinator._async_update_data()
    assert api.async_get_bill.await_count == 1
    electricity_bill_payload["InvoiceNumber"] = "wrong-invoice"
    now += timedelta(hours=16)
    data = await coordinator._async_update_data()
    assert data.statistics.energy == 0 and data.account.account_balance == 12.34
    assert data.electricity_bill is None and not data.electricity_bill_valid
    coordinator.async_set_updated_data(data)
    assert not sensor.available and sensor.native_value is None
    api.async_get_bill_transactions.return_value = {"transactions": []}
    now += timedelta(days=1)
    data = await coordinator._async_update_data()
    assert data.electricity_bill is None and data.electricity_bill_valid
    coordinator.async_set_updated_data(data)
    assert sensor.available and sensor.native_value is None


async def test_usage_rate_limit_does_not_trigger_optional_requests(
    hass, mock_config_entry, accounts_payload
):
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.side_effect = RetryableApiError(429, 60)
    coordinator = ContactEnergyCoordinator(
        hass, mock_config_entry, api, FakeStatistics()
    )
    coordinator.enable_history_fetches()
    await coordinator._async_update_data()
    api.async_get_plan_details.assert_not_awaited()
    api.async_get_bill_transactions.assert_not_awaited()


@pytest.mark.parametrize("endpoint", ["async_get_bill_transactions", "async_get_bill"])
@pytest.mark.parametrize(
    "error,delay",
    [
        (CannotConnect(), 8),
        (InvalidAuth(), 8),
        (RetryableApiError(503, 60), 8),
        (RetryableApiError(429, 12 * 3600), 12),
    ],
)
async def test_transient_bill_failure_preserves_snapshot_and_retries_bounded(
    hass,
    mock_config_entry,
    accounts_payload,
    electricity_bill_payload,
    monkeypatch,
    endpoint,
    error,
    delay,
):
    now = datetime(2026, 9, 2, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.return_value = []
    api.async_get_plan_details.return_value = {}
    api.async_get_bill_transactions.return_value = {
        "transactions": [{"date": "2026-09-01", "invoiceId": "invoice-example"}]
    }
    api.async_get_bill.return_value = electricity_bill_payload
    coordinator = ContactEnergyCoordinator(
        hass, mock_config_entry, api, FakeStatistics()
    )
    coordinator.enable_history_fetches()
    original = await coordinator._async_update_data()
    now += timedelta(days=1)
    request = getattr(api, endpoint)
    request.side_effect = error
    data = await coordinator._async_update_data()
    assert data.electricity_bill == original.electricity_bill
    assert data.electricity_bill_valid
    assert data.statistics.energy == 0
    assert request.await_count == 2
    request.side_effect = None
    now += timedelta(hours=delay) - timedelta(seconds=1)
    await coordinator._async_update_data()
    assert request.await_count == 2
    now += timedelta(seconds=1)
    data = await coordinator._async_update_data()
    assert request.await_count == 3
    assert data.electricity_bill == original.electricity_bill
    assert data.electricity_bill_valid


async def test_legacy_integer_entry_identifiers_match_string_bill_identifiers(
    hass, entry_data, accounts_payload, electricity_bill_payload, monkeypatch
):
    entry_data.update(account_id=123, contract_id=456, contract_icp=789)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data)
    for contract in accounts_payload["accountDetail"]["contracts"]:
        if contract["id"] == TEST_CONTRACT_ID:
            contract["id"] = 456
    electricity_bill_payload["AccountNumber"] = "123"
    electricity_bill_payload["Installations"][0].update(Contract="456", ICP="789")
    electricity_bill_payload["ClassificationDetails"][0]["Contract"] = "456"
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_usage.return_value = []
    api.async_get_plan_details.return_value = {}
    api.async_get_bill_transactions.return_value = {
        "transactions": [{"date": "2026-09-01", "invoiceId": "invoice-example"}]
    }
    api.async_get_bill.return_value = electricity_bill_payload
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    coordinator = ContactEnergyCoordinator(hass, entry, api, FakeStatistics())
    coordinator.enable_history_fetches()
    data = await coordinator._async_update_data()
    assert data.electricity_bill_valid
    assert data.electricity_bill.cost == 92
