"""Tests for setup, contract selection, and reauthentication flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType

from custom_components.contact_energy.config_flow import _user_schema
from custom_components.contact_energy.const import (
    CONF_CONTRACT_ID,
    CONF_USAGE_DAYS,
    DOMAIN,
)
from custom_components.contact_energy.models import Contract

from .conftest import (
    TEST_ACCOUNT_ID,
    TEST_CONTRACT_ID,
    TEST_EMAIL,
    TEST_ICP,
    TEST_PASSWORD,
)

USER_INPUT = {
    CONF_EMAIL: TEST_EMAIL,
    CONF_PASSWORD: TEST_PASSWORD,
    CONF_USAGE_DAYS: 10,
}


async def test_successful_flow_with_contract_selection(
    hass, recorder_dependency
) -> None:
    contracts = [
        Contract("contract-a", TEST_ACCOUNT_ID, "icp-a", "Example address A"),
        Contract("contract-b", TEST_ACCOUNT_ID, "icp-b", "Example address B"),
    ]
    with patch(
        "custom_components.contact_energy.config_flow.validate_input",
        AsyncMock(return_value=contracts),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=USER_INPUT,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "contract"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CONTRACT_ID: "contract-b"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Contact Energy electricity"
    assert result["data"][CONF_CONTRACT_ID] == "contract-b"
    assert result["data"][CONF_PASSWORD] == TEST_PASSWORD
    assert result["result"].unique_id not in {
        "contract-b",
        TEST_ACCOUNT_ID,
        "icp-b",
    }


async def test_single_contract_skips_selection(hass, recorder_dependency) -> None:
    contract = Contract(
        TEST_CONTRACT_ID,
        TEST_ACCOUNT_ID,
        TEST_ICP,
        "Example address",
    )
    with patch(
        "custom_components.contact_energy.config_flow.validate_input",
        AsyncMock(return_value=[contract]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=USER_INPUT,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONTRACT_ID] == TEST_CONTRACT_ID


async def test_existing_legacy_entry_prevents_duplicate_setup(
    hass, mock_config_entry, recorder_dependency
) -> None:
    mock_config_entry.add_to_hass(hass)
    contract = Contract(
        TEST_CONTRACT_ID,
        TEST_ACCOUNT_ID,
        TEST_ICP,
        "Example address",
    )
    with patch(
        "custom_components.contact_energy.config_flow.validate_input",
        AsyncMock(return_value=[contract]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=USER_INPUT,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_invalid_credentials_show_form_error(hass, recorder_dependency) -> None:
    from custom_components.contact_energy.api import InvalidAuth

    with patch(
        "custom_components.contact_energy.config_flow.validate_input",
        AsyncMock(side_effect=InvalidAuth),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data=USER_INPUT,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize("days", [2, 32])
def test_history_days_are_bounded(days: int) -> None:
    with pytest.raises(vol.Invalid):
        _user_schema()({**USER_INPUT, CONF_USAGE_DAYS: days})


async def test_reauthentication_updates_existing_entry(
    hass, mock_config_entry, recorder_dependency
) -> None:
    mock_config_entry.add_to_hass(hass)
    new_email = "updated@example.invalid"
    new_password = "updated-example-password"
    contract = Contract(
        TEST_CONTRACT_ID,
        TEST_ACCOUNT_ID,
        TEST_ICP,
        "Example address",
    )
    with patch(
        "custom_components.contact_energy.config_flow.validate_input",
        AsyncMock(return_value=[contract]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": mock_config_entry.entry_id,
                "unique_id": mock_config_entry.unique_id,
            },
            data=mock_config_entry.data,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        with patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(return_value=True),
        ) as reload_entry:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_EMAIL: new_email, CONF_PASSWORD: new_password},
            )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_EMAIL] == new_email
    assert mock_config_entry.data[CONF_PASSWORD] == new_password
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    reload_entry.assert_awaited_once_with(mock_config_entry.entry_id)


async def test_reauthentication_rejects_same_contract_from_different_identity(
    hass, mock_config_entry, recorder_dependency
) -> None:
    mock_config_entry.add_to_hass(hass)
    returned = Contract(
        TEST_CONTRACT_ID,
        "different-account",
        "different-icp",
        "Example address",
    )
    with patch(
        "custom_components.contact_energy.config_flow.validate_input",
        AsyncMock(return_value=[returned]),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": mock_config_entry.entry_id,
                "unique_id": mock_config_entry.unique_id,
            },
            data=mock_config_entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_EMAIL: TEST_EMAIL, CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_contract"}
