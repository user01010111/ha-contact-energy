"""Synthetic gas bills exercise fail-closed identity and accounting checks."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.contact_energy.api import MalformedResponse
from custom_components.contact_energy.billing import latest_invoice, parse_gas_bill
from custom_components.contact_energy.const import (
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    CONF_CONTRACT_TYPE,
    DOMAIN,
)
from custom_components.contact_energy.models import parse_contracts


@pytest.fixture
def gas_bill_payload():
    """Invented values with the verified current-bill field structure."""
    return {
        "AccountNumber": "account-example",
        "InvoiceNumber": "invoice-example",
        "isCurrentBill": True,
        "TotalCurrentCharges": "46.00",
        "CurrentChargesGST": "6.00",
        "TechnicalDetails": [{"Version": "1.0"}],
        "Installations": [
            {
                "Contract": "gas-example",
                "ICP": "gas-icp-example",
                "Product": "Natural Gas",
                "Installation": "installation-example",
                "Premise": "premise-example",
                "ValidFrom": "2026-02-01T00:00:00.000+00:00",
                "ValidTo": "2026-02-28T00:00:00.000+00:00",
                "Days": "28",
            }
        ],
        "Registers": [
            {
                "Installation": "installation-example",
                "ICP": "gas-icp-example",
                "Product": "Natural Gas",
                "BillUnitOfMeasure": "kWh",
                "EnergyUnits": "100",
                "CurrentMeterReadType": "01",
                "Device": "device-example",
                "Register": "register-example",
            }
        ],
        "ClassificationDetails": [
            {
                "Contract": "gas-example",
                "Premise": "premise-example",
                "Classification": "002",
                "AmountExclGST": "40.00",
                "GSTAmount": "6.00",
                "AmountInclGST": "46.00",
            }
        ],
        "VariableCharges": [{"Contract": "gas-example", "AmountExclGST": "20.00"}],
        "FixedCharges": [
            {"Contract": "gas-example", "AmountExclGST": "18.00"},
            {"Contract": "gas-example", "AmountExclGST": "0.50"},
            {"Contract": "gas-example", "AmountExclGST": "1.50"},
        ],
    }


def parse(payload):
    return parse_gas_bill(
        payload,
        account_id="account-example",
        contract_id="gas-example",
        icp="gas-icp-example",
        invoice_id="invoice-example",
    )


def test_current_bill(gas_bill_payload):
    bill = parse(gas_bill_payload)
    assert (bill.energy, bill.cost, bill.cost_excl_gst, bill.gst) == (100, 46, 40, 6)
    assert bill.days == 28
    assert bill.start.isoformat() == "2026-02-01"
    assert bill.end.isoformat() == "2026-02-28"
    assert bill.reading_quality == "actual"
    assert bill.invoice_charges_incl_gst == 46
    assert bill.invoice_share_percent == 100
    assert bill.fixed_cost_excl_gst == bill.variable_cost_excl_gst == 20
    assert "example" not in repr(bill)


@pytest.mark.parametrize(
    "code,expected", [("03", "estimated"), ("02", "unknown"), (None, "unknown")]
)
def test_reading_quality(gas_bill_payload, code, expected):
    gas_bill_payload["Registers"][0]["CurrentMeterReadType"] = code
    assert parse(gas_bill_payload).reading_quality == expected


@pytest.mark.parametrize(
    "field,value",
    [("AccountNumber", "other"), ("InvoiceNumber", "other"), ("isCurrentBill", False)],
)
def test_wrong_bill_rejected(gas_bill_payload, field, value):
    gas_bill_payload[field] = value
    with pytest.raises(MalformedResponse, match="reconciled"):
        parse(gas_bill_payload)


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("TechnicalDetails", "Version", "2.0"),
        ("Installations", "Contract", "other"),
        ("Installations", "ICP", "other"),
        ("Installations", "Product", "Electricity"),
        ("Installations", "Days", "29"),
        ("Installations", "ValidTo", "2026-01-31"),
        ("Registers", "BillUnitOfMeasure", "m3"),
        ("Registers", "EnergyUnits", "NaN"),
        ("Registers", "EnergyUnits", "1e9999"),
        ("Registers", "EnergyUnits", "-1"),
        ("Registers", "EnergyUnits", True),
        ("Registers", "Product", "Electricity"),
        ("Registers", "Device", None),
        ("ClassificationDetails", "Classification", "008"),
        ("ClassificationDetails", "Premise", "other"),
        ("ClassificationDetails", "GSTAmount", "7"),
        ("FixedCharges", "AmountExclGST", "19"),
        ("VariableCharges", "AmountExclGST", None),
    ],
)
def test_malformed_bill_rejected(gas_bill_payload, section, field, value):
    gas_bill_payload[section][0][field] = value
    with pytest.raises(MalformedResponse):
        parse(gas_bill_payload)


@pytest.mark.parametrize(
    "section", ["Registers", "Installations", "ClassificationDetails"]
)
def test_duplicates_rejected(gas_bill_payload, section):
    gas_bill_payload[section].append(deepcopy(gas_bill_payload[section][0]))
    with pytest.raises(MalformedResponse):
        parse(gas_bill_payload)


def test_other_contract_charges_excluded(gas_bill_payload):
    gas_bill_payload["FixedCharges"].append(
        {"Contract": "other", "AmountExclGST": "900"}
    )
    assert parse(gas_bill_payload).cost == 46


def combined_invoice(payload):
    """A second service on the same invoice, not a matching consumption period."""
    payload["ClassificationDetails"].append(
        {
            "Contract": "other-service",
            "Premise": "other-premise",
            "Classification": "001",
            "AmountExclGST": "60.00",
            "GSTAmount": "9.00",
            "AmountInclGST": "69.00",
        }
    )
    payload.update(TotalCurrentCharges="115.00", CurrentChargesGST="15.00")
    return payload


def test_invoice_share_excludes_balances_and_payments(gas_bill_payload):
    payload = combined_invoice(gas_bill_payload)
    payload.update(CurrentAmount="999.00", TotalAmount="1234.00", InvoiceGST="150.00")
    bill = parse(payload)
    assert bill.invoice_charges_incl_gst == 115
    assert bill.invoice_share_percent == 40
    assert bill.cost == 46


@pytest.mark.parametrize(
    "field,value",
    [
        ("TotalCurrentCharges", None),
        ("TotalCurrentCharges", "0"),
        ("TotalCurrentCharges", "-1"),
        ("TotalCurrentCharges", "114.99"),
        ("TotalCurrentCharges", "NaN"),
        ("TotalCurrentCharges", "1e9999"),
        ("TotalCurrentCharges", True),
        ("CurrentChargesGST", "14.99"),
        ("CurrentChargesGST", "-1"),
        ("CurrentChargesGST", None),
    ],
)
def test_unreconciled_total_only_disables_share(gas_bill_payload, field, value):
    payload = combined_invoice(gas_bill_payload)
    payload[field] = value
    bill = parse(payload)
    assert bill.cost == 46
    assert bill.invoice_share_percent is None
    assert bill.invoice_charges_incl_gst is None


@pytest.mark.parametrize("invalid", ["duplicate", "credit", "tax", "identity"])
def test_unsupported_other_service_disables_share(gas_bill_payload, invalid):
    payload = combined_invoice(gas_bill_payload)
    other = payload["ClassificationDetails"][1]
    if invalid == "duplicate":
        payload["ClassificationDetails"].append(deepcopy(other))
        payload.update(TotalCurrentCharges="184", CurrentChargesGST="24")
    elif invalid == "credit":
        other.update(AmountExclGST="-20", GSTAmount="-3", AmountInclGST="-23")
        payload.update(TotalCurrentCharges="23", CurrentChargesGST="3")
    elif invalid == "tax":
        other["AmountExclGST"] = "59"
    elif invalid == "identity":
        other["Contract"] = None
    bill = parse(payload)
    assert bill.cost == 46
    assert bill.invoice_share_percent is None


def test_other_service_line_details_do_not_define_invoice_share(gas_bill_payload):
    payload = combined_invoice(gas_bill_payload)
    payload["VariableCharges"].append(
        {"Contract": "other-service", "AmountExclGST": None}
    )
    assert parse(payload).invoice_share_percent == 40
    # Adjustments within a positive service total are already in the denominator.
    payload["VariableCharges"][-1]["AmountExclGST"] = "-1"
    assert parse(payload).invoice_share_percent == 40


def test_invoice_order_and_payment_filtering():
    payload = {
        "transactions": [
            {"date": "2026-01-01", "invoiceId": "older"},
            {"date": "2026-03-01", "description": "Payment"},
            {"date": "2026-02-01", "invoiceId": "newer"},
        ]
    }
    assert latest_invoice(payload) == "newer"
    payload["transactions"].append({"date": "2026-02-01", "invoiceId": "ambiguous"})
    with pytest.raises(MalformedResponse):
        latest_invoice(payload)
    assert latest_invoice({"transactions": []}) is None


def test_gas_contract_is_selectable(accounts_payload):
    accounts_payload["accountDetail"]["contracts"][0]["contractType"] = 2
    accounts_payload["accountDetail"]["contracts"][0].pop("premise")
    contracts = parse_contracts(accounts_payload)
    assert [c.contract_type for c in contracts] == [2, 1]
    assert contracts[0].address == "Energy contract"


async def test_gas_setup_refresh_reload_and_wrong_bill(
    hass, mock_config_entry, accounts_payload, gas_bill_payload, recorder_dependency
):
    from homeassistant.const import Platform
    from homeassistant.helpers import entity_registry as er

    from custom_components.contact_energy import _contract_key

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_CONTRACT_TYPE: 2,
            CONF_CONTRACT_ID: "gas-example",
            CONF_CONTRACT_ICP: "gas-icp-example",
        },
    )
    contract = accounts_payload["accountDetail"]["contracts"][1]
    contract.update(id="gas-example", icp="gas-icp-example", contractType=2)
    contract["devices"][0]["nextReadingIsEstimate"] = True
    transactions = {
        "transactions": [{"date": "2026-03-01", "invoiceId": "invoice-example"}]
    }
    with (
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_accounts",
            AsyncMock(return_value=accounts_payload),
        ),
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_bill_transactions",
            AsyncMock(return_value=transactions),
        ) as index,
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_bill",
            AsyncMock(return_value=gas_bill_payload),
        ) as api_bill,
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_usage",
            AsyncMock(),
        ) as usage,
        patch("custom_components.contact_energy.ContactEnergyStatistics") as statistics,
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        registry = er.async_get(hass)
        key = _contract_key(mock_config_entry)
        entities = {
            name: registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{key}_{name}")
            for name in (
                "billed_gas_usage",
                "billed_gas_cost",
                "gas_reading_quality",
                "previous_reading_date",
                "next_reading_date",
                "gas_invoice_share",
            )
        }
        assert (
            len(er.async_entries_for_config_entry(registry, mock_config_entry.entry_id))
            == 6
        )
        assert hass.states.get(entities["billed_gas_usage"]).state == "100.0"
        cost = hass.states.get(entities["billed_gas_cost"])
        assert cost.state == "46.0"
        assert cost.attributes["includes_gst"] is True
        assert cost.attributes["unit_of_measurement"] == "NZD"
        assert "state_class" not in cost.attributes
        assert "invoice" not in repr(cost.attributes).lower()
        assert hass.states.get(entities["gas_reading_quality"]).state == "actual"
        assert hass.states.get(entities["previous_reading_date"]).state == "2026-08-02"
        next_read = hass.states.get(entities["next_reading_date"])
        assert next_read.state == "2026-09-02"
        assert next_read.attributes["next_reading_is_estimate"] is True
        share = hass.states.get(entities["gas_invoice_share"])
        assert share.state == "100.0"
        assert share.attributes["unit_of_measurement"] == "%"
        assert share.attributes["invoice_charges_incl_gst"] == 46
        assert "state_class" not in share.attributes
        assert index.await_count == api_bill.await_count == 1
        usage.assert_not_awaited()
        statistics.assert_not_called()
        coordinator = mock_config_entry.runtime_data.coordinator
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entities["billed_gas_usage"]).state == "100.0"
        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(entities["billed_gas_cost"]).state == "46.0"
        gas_bill_payload["TotalCurrentCharges"] = "0"
        await mock_config_entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entities["gas_invoice_share"]).state == "unknown"
        assert hass.states.get(entities["billed_gas_cost"]).state == "46.0"
        gas_bill_payload["TotalCurrentCharges"] = "46"
        gas_bill_payload["InvoiceNumber"] = "wrong-invoice"
        await mock_config_entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entities["billed_gas_cost"]).state == "unavailable"
        assert hass.states.get(entities["next_reading_date"]).state == "2026-09-02"
        assert hass.states.get(entities["gas_invoice_share"]).state == "unavailable"
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_gas_config_flow(hass, recorder_dependency):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.contact_energy.models import Contract

    from .test_config_flow import USER_INPUT

    with patch(
        "custom_components.contact_energy.config_flow.validate_input",
        AsyncMock(
            return_value=[
                Contract(
                    "gas-example",
                    "account-example",
                    "gas-icp-example",
                    "Example address",
                    2,
                ),
            ]
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONTRACT_TYPE] == 2
    assert "gas" in result["title"]


@pytest.mark.parametrize(
    "invalid", ["not_current", "wrong_invoice", "credit", "ambiguous"]
)
async def test_unsupported_gas_bill_loads_without_setup_retry(
    hass,
    mock_config_entry,
    accounts_payload,
    gas_bill_payload,
    recorder_dependency,
    invalid,
):
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.helpers import entity_registry as er

    from custom_components.contact_energy.const import UPDATE_INTERVAL

    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_CONTRACT_TYPE: 2,
            CONF_CONTRACT_ID: "gas-example",
            CONF_CONTRACT_ICP: "gas-icp-example",
        },
    )
    accounts_payload["accountDetail"]["contracts"][1].update(
        id="gas-example", icp="gas-icp-example", contractType=2
    )
    valid = deepcopy(gas_bill_payload)
    transactions = {
        "transactions": [{"date": "2026-03-01", "invoiceId": "invoice-example"}]
    }
    if invalid == "not_current":
        gas_bill_payload["isCurrentBill"] = False
    elif invalid == "wrong_invoice":
        gas_bill_payload["InvoiceNumber"] = "other"
    elif invalid == "credit":
        gas_bill_payload["FixedCharges"][0]["AmountExclGST"] = "-1"
    else:
        transactions["transactions"].append(
            {"date": "2026-03-01", "invoiceId": "other"}
        )
    api = AsyncMock()
    api.async_get_accounts.return_value = accounts_payload
    api.async_get_bill_transactions.return_value = transactions
    api.async_get_bill.return_value = gas_bill_payload
    with patch(
        "custom_components.contact_energy.ContactEnergyApi", return_value=api
    ) as client:
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert mock_config_entry.state is ConfigEntryState.LOADED
        entities = er.async_entries_for_config_entry(
            er.async_get(hass), mock_config_entry.entry_id
        )
        assert len(entities) == 6
        assert all(
            hass.states.get(e.entity_id).state
            == (
                "2026-08-02"
                if e.unique_id.endswith("_previous_reading_date")
                else "2026-09-02"
                if e.unique_id.endswith("_next_reading_date")
                else "unavailable"
            )
            for e in entities
        )
        assert (
            mock_config_entry.runtime_data.coordinator.update_interval
            == UPDATE_INTERVAL
        )
        assert client.call_count == 1
        api.async_get_accounts.assert_awaited_once()
        api.async_get_bill_transactions.assert_awaited_once()
        assert api.async_get_bill.await_count == (0 if invalid == "ambiguous" else 1)
        api.async_get_usage.assert_not_awaited()
        api.async_get_bill.return_value = valid
        api.async_get_bill_transactions.return_value = {
            "transactions": transactions["transactions"][:1]
        }
        await mock_config_entry.runtime_data.coordinator.async_refresh()
        await hass.async_block_till_done()
        assert all(
            hass.states.get(e.entity_id).state != "unavailable" for e in entities
        )
        assert client.call_count == 1
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
