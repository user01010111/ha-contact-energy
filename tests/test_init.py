"""Tests for config-entry setup and unload lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.contact_energy import (
    _contract_key,
    _migrate_legacy_registry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.contact_energy.const import (
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    DOMAIN,
    SENSOR_USAGE,
    contract_device_name,
    contract_entry_title,
)


@pytest.mark.parametrize(
    "failure",
    [
        ConfigEntryAuthFailed("credentials rejected"),
        ConfigEntryNotReady("service unavailable"),
    ],
)
async def test_setup_preserves_home_assistant_setup_failures(
    hass, mock_config_entry, failure
) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.contact_energy.ContactEnergyCoordinator."
            "async_config_entry_first_refresh",
            AsyncMock(side_effect=failure),
        ),
        pytest.raises(type(failure)),
    ):
        await async_setup_entry(hass, mock_config_entry)


async def test_one_client_runtime_and_clean_unload(hass, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.contact_energy.ContactEnergyCoordinator."
            "async_config_entry_first_refresh",
            AsyncMock(),
        ),
        patch(
            "custom_components.contact_energy.ContactEnergyCoordinator.async_refresh",
            AsyncMock(),
        ) as refresh,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(),
        ) as forward,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ) as unload,
    ):
        assert await async_setup_entry(hass, mock_config_entry)
        runtime = mock_config_entry.runtime_data
        assert runtime.coordinator.api is runtime.client
        assert await async_unload_entry(hass, mock_config_entry)

    forward.assert_awaited_once()
    refresh.assert_awaited_once()
    unload.assert_awaited_once()


async def test_entry_reload_does_not_wait_for_history_backfill(
    hass,
    mock_config_entry,
    accounts_payload,
    recorder_dependency,
    monkeypatch,
) -> None:
    history_started = asyncio.Event()
    first_history_cancelled = asyncio.Event()
    replacement_history_started = asyncio.Event()
    replacement_history_finished = asyncio.Event()
    allow_history_to_finish = asyncio.Event()
    history_attempts = 0

    async def slow_usage(_requested_date: str) -> list[dict[str, object]]:
        nonlocal history_attempts
        history_attempts += 1
        current_attempt = history_attempts
        if current_attempt == 1:
            history_started.set()
        else:
            replacement_history_started.set()
        try:
            await allow_history_to_finish.wait()
        except asyncio.CancelledError:
            if current_attempt == 1:
                first_history_cancelled.set()
            raise
        if current_attempt == 4:
            replacement_history_finished.set()
        return []

    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING",
        0,
    )
    mock_config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_accounts",
            AsyncMock(return_value=accounts_payload),
        ),
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_usage",
            AsyncMock(side_effect=slow_usage),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert mock_config_entry.state is ConfigEntryState.LOADED
        async with asyncio.timeout(1):
            await history_started.wait()

        async with asyncio.timeout(1):
            assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert first_history_cancelled.is_set()
        async with asyncio.timeout(1):
            await replacement_history_started.wait()

        allow_history_to_finish.set()
        async with asyncio.timeout(1):
            await replacement_history_finished.wait()
        await asyncio.sleep(0)


async def test_setup_migrates_legacy_registry_identifiers(
    hass, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    icp = mock_config_entry.data[CONF_CONTRACT_ICP]
    hass.config_entries.async_update_entry(
        mock_config_entry,
        unique_id=mock_config_entry.data["contract_id"],
        title="Private legacy title",
    )
    legacy_unique_id = f"{DOMAIN}_{icp}_{SENSOR_USAGE}"
    entity_registry = er.async_get(hass)
    legacy_entity = entity_registry.async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        legacy_unique_id,
        config_entry=mock_config_entry,
    )
    device_registry = dr.async_get(hass)
    legacy_device = device_registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, icp)},
        name="Legacy Contact Energy device",
    )

    with (
        patch(
            "custom_components.contact_energy.ContactEnergyCoordinator."
            "async_config_entry_first_refresh",
            AsyncMock(),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(),
        ),
    ):
        await async_setup_entry(hass, mock_config_entry)

    contract_key = _contract_key(mock_config_entry)
    assert entity_registry.async_get(legacy_entity.entity_id).unique_id == (
        f"{contract_key}_{SENSOR_USAGE}"
    )
    assert device_registry.async_get(legacy_device.id).identifiers == {
        (DOMAIN, contract_key)
    }
    assert device_registry.async_get(legacy_device.id).name == contract_device_name(icp)
    assert mock_config_entry.title == contract_entry_title(icp)
    assert mock_config_entry.unique_id != mock_config_entry.data[CONF_CONTRACT_ICP]
    assert mock_config_entry.unique_id != mock_config_entry.data["contract_id"]


def test_registry_migration_preflights_entity_collision(
    hass, mock_config_entry
) -> None:
    mock_config_entry.add_to_hass(hass)
    icp = mock_config_entry.data[CONF_CONTRACT_ICP]
    contract_key = _contract_key(mock_config_entry)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        unique_id=mock_config_entry.data[CONF_CONTRACT_ID],
        title="Legacy title",
    )
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{DOMAIN}_{icp}_{SENSOR_USAGE}",
        config_entry=mock_config_entry,
    )
    registry.async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{contract_key}_{SENSOR_USAGE}",
        config_entry=mock_config_entry,
    )

    with pytest.raises(ConfigEntryError):
        _migrate_legacy_registry(hass, mock_config_entry, contract_key)

    assert registry.async_get(legacy.entity_id).unique_id == (
        f"{DOMAIN}_{icp}_{SENSOR_USAGE}"
    )
    assert mock_config_entry.unique_id == mock_config_entry.data[CONF_CONTRACT_ID]
    assert mock_config_entry.title == "Legacy title"


def test_device_migration_is_scoped_to_config_entry(hass, entry_data) -> None:
    first = MockConfigEntry(domain=DOMAIN, data=entry_data, unique_id="first")
    second_data = {**entry_data, CONF_CONTRACT_ID: "contract-second"}
    second = MockConfigEntry(domain=DOMAIN, data=second_data, unique_id="second")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    icp = entry_data[CONF_CONTRACT_ICP]
    registry = dr.async_get(hass)
    first_device = registry.async_get_or_create(
        config_entry_id=first.entry_id,
        identifiers={(DOMAIN, icp)},
        name="First legacy device",
    )
    second_device = registry.async_get_or_create(
        config_entry_id=second.entry_id,
        identifiers={(DOMAIN, icp)},
        name="Second legacy device",
    )

    second_key = _contract_key(second)
    _migrate_legacy_registry(hass, second, second_key)

    assert registry.async_get(first_device.id).identifiers == {(DOMAIN, icp)}
    assert registry.async_get(second_device.id).identifiers == {(DOMAIN, second_key)}


def test_existing_opaque_device_gets_icp_display_name(hass, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    contract_key = _contract_key(mock_config_entry)
    icp = mock_config_entry.data[CONF_CONTRACT_ICP]
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, contract_key)},
        name="Contact Energy electricity account",
    )

    _migrate_legacy_registry(hass, mock_config_entry, contract_key)

    updated = registry.async_get(device.id)
    assert updated is not None
    assert updated.identifiers == {(DOMAIN, contract_key)}
    assert updated.name == contract_device_name(icp)
