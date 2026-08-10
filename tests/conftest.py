"""Shared fixtures for Contact Energy tests."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contact_energy.const import (
    CONF_ACCOUNT_ID,
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    CONF_USAGE_DAYS,
    DOMAIN,
    contract_digest,
    contract_entry_title,
)

pytest_plugins = "pytest_homeassistant_custom_component"

TEST_EMAIL = "user@example.invalid"
TEST_PASSWORD = "example-password-not-real"
TEST_ACCOUNT_ID = "account-example"
TEST_CONTRACT_ID = "contract-example"
TEST_ICP = "icp-example"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in tests."""


@pytest.fixture
def entry_data() -> dict[str, Any]:
    """Return fully synthetic config entry data."""
    return {
        CONF_EMAIL: TEST_EMAIL,
        CONF_PASSWORD: TEST_PASSWORD,
        CONF_ACCOUNT_ID: TEST_ACCOUNT_ID,
        CONF_CONTRACT_ID: TEST_CONTRACT_ID,
        CONF_CONTRACT_ICP: TEST_ICP,
        CONF_USAGE_DAYS: 3,
    }


@pytest.fixture
def mock_config_entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """Return a synthetic Contact Energy config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=contract_entry_title(TEST_ICP),
        data=entry_data,
        unique_id=contract_digest(TEST_ACCOUNT_ID, TEST_CONTRACT_ID, TEST_ICP),
    )


@pytest.fixture
def recorder_dependency(hass) -> None:
    """Mark recorder loaded for config-flow tests that do not exercise it."""
    hass.config.components.add("recorder")


@pytest.fixture
def accounts_payload() -> dict[str, Any]:
    """Return a sanitized representative accounts response."""
    return {
        "accountDetail": {
            "id": TEST_ACCOUNT_ID,
            "accountBalance": {"currentBalance": "12.34"},
            "nextBill": {"amount": "45.67", "date": "20 Aug 2026"},
            "invoice": {"amountDue": "8.90", "paymentDueDate": "25 Aug 2026"},
            "contracts": [
                {
                    "id": "contract-other",
                    "contractType": 1,
                    "icp": "icp-other",
                    "premise": {"supplyAddress": {"shortForm": "Example address B"}},
                    "devices": [
                        {
                            "nextMeterReadDate": "01 Sep 2026",
                            "registers": [{"previousMeterReadingDate": "01 Aug 2026"}],
                        }
                    ],
                },
                {
                    "id": TEST_CONTRACT_ID,
                    "contractType": 1,
                    "icp": TEST_ICP,
                    "premise": {"supplyAddress": {"shortForm": "Example address A"}},
                    "devices": [
                        {
                            "nextMeterReadDate": "02 Sep 2026",
                            "registers": [{"previousMeterReadingDate": "02 Aug 2026"}],
                        }
                    ],
                },
            ],
        }
    }
