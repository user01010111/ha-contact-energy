"""The Contact Energy integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import ContactEnergyApi
from .const import (
    CONF_ACCOUNT_ID,
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_ID,
    CONTRACT_KEY_LENGTH,
    DOMAIN,
    SENSOR_KEYS,
    contract_digest,
)
from .coordinator import ContactEnergyCoordinator
from .statistics import ContactEnergyStatistics

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass(slots=True)
class ContactEnergyRuntimeData:
    """Runtime objects shared by this config entry."""

    client: ContactEnergyApi
    coordinator: ContactEnergyCoordinator
    contract_key: str


type ContactEnergyConfigEntry = ConfigEntry[ContactEnergyRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: ContactEnergyConfigEntry
) -> bool:
    """Set up Contact Energy from a config entry."""
    contract_key = _contract_key(entry)
    _migrate_legacy_registry(hass, entry, contract_key)
    client = ContactEnergyApi(
        hass,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_ACCOUNT_ID],
        entry.data[CONF_CONTRACT_ID],
    )
    statistics = ContactEnergyStatistics(
        hass,
        entry.entry_id,
        contract_key,
    )
    coordinator = ContactEnergyCoordinator(hass, entry, client, statistics)
    entry.runtime_data = ContactEnergyRuntimeData(
        client=client,
        coordinator=coordinator,
        contract_key=contract_key,
    )

    await coordinator.async_config_entry_first_refresh()
    coordinator.enable_history_fetches()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        "Contact Energy history backfill",
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ContactEnergyConfigEntry
) -> bool:
    """Unload a Contact Energy config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _contract_key(entry: ConfigEntry) -> str:
    """Return a stable opaque key unique to the selected contract and ICP."""
    return contract_digest(
        entry.data[CONF_ACCOUNT_ID],
        entry.data[CONF_CONTRACT_ID],
        entry.data[CONF_CONTRACT_ICP],
    )[:CONTRACT_KEY_LENGTH]


def _migrate_legacy_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    contract_key: str,
) -> None:
    """Replace legacy private registry identifiers after collision preflight."""
    digest = contract_digest(
        entry.data[CONF_ACCOUNT_ID],
        entry.data[CONF_CONTRACT_ID],
        entry.data[CONF_CONTRACT_ICP],
    )
    entity_registry = er.async_get(hass)
    icp = str(entry.data[CONF_CONTRACT_ICP])
    entity_updates: list[tuple[str, str]] = []
    for sensor_key in SENSOR_KEYS:
        legacy_unique_id = f"{DOMAIN}_{icp}_{sensor_key}"
        legacy_entity_id = entity_registry.async_get_entity_id(
            Platform.SENSOR,
            DOMAIN,
            legacy_unique_id,
        )
        target_unique_id = f"{contract_key}_{sensor_key}"
        target_entity_id = entity_registry.async_get_entity_id(
            Platform.SENSOR,
            DOMAIN,
            target_unique_id,
        )
        if (
            legacy_entity_id is not None
            and target_entity_id is not None
            and legacy_entity_id != target_entity_id
        ):
            raise ConfigEntryError(
                "Contact Energy registry migration found a duplicate entity"
            )
        if legacy_entity_id is not None:
            entity_updates.append((legacy_entity_id, target_unique_id))

    device_registry = dr.async_get(hass)
    legacy_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, icp),
        entry.entry_id,
    )
    target_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, contract_key),
        entry.entry_id,
    )
    if (
        legacy_device is not None
        and target_device is not None
        and legacy_device.id != target_device.id
    ):
        raise ConfigEntryError(
            "Contact Energy registry migration found a duplicate device"
        )
    if any(
        other.entry_id != entry.entry_id and other.unique_id == digest
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        raise ConfigEntryError(
            "Contact Energy registry migration found a duplicate config entry"
        )

    hass.config_entries.async_update_entry(
        entry,
        unique_id=digest,
        title="Contact Energy electricity",
    )
    for entity_id, target_unique_id in entity_updates:
        entity_registry.async_update_entity(
            entity_id,
            new_unique_id=target_unique_id,
        )
    if legacy_device is not None:
        device_registry.async_update_device(
            legacy_device.id,
            new_identifiers={(DOMAIN, contract_key)},
            name="Contact Energy electricity account",
        )
