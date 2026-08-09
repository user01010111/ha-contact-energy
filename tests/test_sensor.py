"""Tests for contract-scoped entity identity and privacy."""

from __future__ import annotations

from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contact_energy import _contract_key
from custom_components.contact_energy.const import (
    CONF_ACCOUNT_ID,
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    CONTRACT_KEY_LENGTH,
    DEFAULT_CURRENCY,
    DOMAIN,
)
from custom_components.contact_energy.sensor import (
    SENSOR_DESCRIPTIONS,
    ContactEnergySensor,
)


def test_contract_keys_are_unique_and_opaque(entry_data) -> None:
    first = MockConfigEntry(domain=DOMAIN, data=entry_data)
    second_data = {**entry_data, CONF_CONTRACT_ID: "contract-second"}
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
        data={**entry_data, CONF_CONTRACT_ID: "contract-second"},
    )
    coordinator = MagicMock(hass=hass)

    first = ContactEnergySensor(
        coordinator,
        _contract_key(first_entry),
        SENSOR_DESCRIPTIONS[0],
    )
    second = ContactEnergySensor(
        coordinator,
        _contract_key(second_entry),
        SENSOR_DESCRIPTIONS[0],
    )

    assert first.unique_id != second.unique_id


def test_usage_allows_downward_corrections_and_money_is_nzd(hass) -> None:
    coordinator = MagicMock(hass=hass)
    usage = ContactEnergySensor(coordinator, "contract-key", SENSOR_DESCRIPTIONS[0])
    money = ContactEnergySensor(coordinator, "contract-key", SENSOR_DESCRIPTIONS[1])

    assert usage.state_class == "total"
    assert money.native_unit_of_measurement == DEFAULT_CURRENCY
