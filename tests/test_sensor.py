"""Tests for contract-scoped entity identity and privacy."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass
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
from custom_components.contact_energy.sensor import (
    SENSOR_DESCRIPTIONS,
    ContactEnergySensor,
)

from .conftest import TEST_ICP


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

    assert len(monetary_descriptions) == 3
    assert all(description.state_class is None for description in monetary_descriptions)
