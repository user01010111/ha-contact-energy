"""Config and reauthentication flows for Contact Energy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api import (
    CannotConnect,
    ContactEnergyApi,
    InvalidAuth,
    MalformedResponse,
    RetryableApiError,
    UnexpectedResponse,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    CONF_USAGE_DAYS,
    DEFAULT_USAGE_DAYS,
    DOMAIN,
    MAX_USAGE_DAYS,
    MIN_USAGE_DAYS,
    contract_digest,
)
from .models import Contract, parse_contracts


def _user_schema(suggested: Mapping[str, Any] | None = None) -> vol.Schema:
    """Return the bounded initial setup schema."""
    values = suggested or {}
    email_key = (
        vol.Required(CONF_EMAIL, default=values[CONF_EMAIL])
        if CONF_EMAIL in values
        else vol.Required(CONF_EMAIL)
    )
    return vol.Schema(
        {
            email_key: cv.string,
            vol.Required(CONF_PASSWORD): cv.string,
            vol.Optional(
                CONF_USAGE_DAYS,
                default=values.get(CONF_USAGE_DAYS, DEFAULT_USAGE_DAYS),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_USAGE_DAYS, max=MAX_USAGE_DAYS),
            ),
        }
    )


async def validate_input(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> list[Contract]:
    """Validate credentials and return selectable electricity contracts."""
    api = ContactEnergyApi(hass, data[CONF_EMAIL], data[CONF_PASSWORD])
    await api.async_login()
    return parse_contracts(await api.async_get_accounts())


class ContactEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle Contact Energy configuration and reauthentication."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize transient flow state."""
        self._entry_data: dict[str, Any] = {}
        self._contracts: list[Contract] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate credentials and collect the selected contract."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._contracts = await validate_input(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect, RetryableApiError:
                errors["base"] = "cannot_connect"
            except MalformedResponse, UnexpectedResponse:
                errors["base"] = "unknown"
            else:
                self._entry_data = dict(user_input)
                if len(self._contracts) == 1:
                    return await self._async_create_contract_entry(self._contracts[0])
                return await self.async_step_contract()

        suggested = {
            key: value
            for key, value in (user_input or {}).items()
            if key != CONF_PASSWORD
        }
        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(suggested),
            errors=errors,
        )

    async def async_step_contract(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one validated electricity contract."""
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = next(
                (
                    contract
                    for contract in self._contracts
                    if contract.contract_id == user_input[CONF_CONTRACT_ID]
                ),
                None,
            )
            if selected is not None:
                return await self._async_create_contract_entry(selected)
            errors["base"] = "invalid_contract"

        contract_labels = {
            contract.contract_id: f"Electricity contract {index}: {contract.address}"
            for index, contract in enumerate(self._contracts, start=1)
        }
        return self.async_show_form(
            step_id="contract",
            data_schema=vol.Schema(
                {vol.Required(CONF_CONTRACT_ID): vol.In(contract_labels)}
            ),
            errors=errors,
        )

    async def _async_create_contract_entry(
        self, contract: Contract
    ) -> ConfigFlowResult:
        """Create one contract-scoped config entry."""
        if any(
            entry.data.get(CONF_CONTRACT_ID) == contract.contract_id
            for entry in self._async_current_entries()
        ):
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(
            contract_digest(contract.account_id, contract.contract_id, contract.icp)
        )
        self._abort_if_unique_id_configured()
        data = {
            **self._entry_data,
            CONF_ACCOUNT_ID: contract.account_id,
            CONF_CONTRACT_ID: contract.contract_id,
            CONF_CONTRACT_ICP: contract.icp,
        }
        return self.async_create_entry(title="Contact Energy electricity", data=data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication for the existing config entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate new credentials and update the existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                contracts = await validate_input(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect, RetryableApiError:
                errors["base"] = "cannot_connect"
            except MalformedResponse, UnexpectedResponse:
                errors["base"] = "unknown"
            else:
                selected = next(
                    (
                        contract
                        for contract in contracts
                        if (
                            contract.account_id == str(entry.data[CONF_ACCOUNT_ID])
                            and contract.contract_id
                            == str(entry.data[CONF_CONTRACT_ID])
                            and contract.icp == str(entry.data[CONF_CONTRACT_ICP])
                        )
                    ),
                    None,
                )
                if selected is None:
                    errors["base"] = "invalid_contract"
                else:
                    await self.async_set_unique_id(
                        contract_digest(
                            selected.account_id,
                            selected.contract_id,
                            selected.icp,
                        )
                    )
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_EMAIL: user_input[CONF_EMAIL],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL,
                        default=(user_input or {}).get(
                            CONF_EMAIL, entry.data[CONF_EMAIL]
                        ),
                    ): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                }
            ),
            errors=errors,
        )
