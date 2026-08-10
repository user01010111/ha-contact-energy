"""Sensor platform for Contact Energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ContactEnergyConfigEntry
from .const import (
    CONF_CONTRACT_ICP,
    DEFAULT_CURRENCY,
    DOMAIN,
    DOMAIN_NAME,
    contract_device_name,
)
from .coordinator import ContactEnergyCoordinator, ContactEnergyData


@dataclass(frozen=True, kw_only=True)
class ContactEnergySensorDescription(SensorEntityDescription):
    """Describe a Contact Energy sensor."""

    value_fn: Callable[[ContactEnergyData], Any]


SENSOR_DESCRIPTIONS: tuple[ContactEnergySensorDescription, ...] = (
    ContactEnergySensorDescription(
        key="usage",
        name="Electricity usage",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:meter-electric",
        value_fn=lambda data: data.statistics.energy,
    ),
    ContactEnergySensorDescription(
        key="account_balance",
        name="Account balance",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:cash",
        value_fn=lambda data: data.account.account_balance,
    ),
    ContactEnergySensorDescription(
        key="next_bill_amount",
        name="Next bill amount",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:cash-clock",
        value_fn=lambda data: data.account.next_bill_amount,
    ),
    ContactEnergySensorDescription(
        key="next_bill_date",
        name="Next bill date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar",
        value_fn=lambda data: data.account.next_bill_date,
    ),
    ContactEnergySensorDescription(
        key="payment_due",
        name="Payment due",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:cash-marker",
        value_fn=lambda data: data.account.payment_due,
    ),
    ContactEnergySensorDescription(
        key="payment_due_date",
        name="Payment due date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar-clock",
        value_fn=lambda data: data.account.payment_due_date,
    ),
    ContactEnergySensorDescription(
        key="previous_reading_date",
        name="Previous meter reading date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar",
        value_fn=lambda data: data.account.previous_reading_date,
    ),
    ContactEnergySensorDescription(
        key="next_reading_date",
        name="Next meter reading date",
        device_class=SensorDeviceClass.DATE,
        icon="mdi:calendar",
        value_fn=lambda data: data.account.next_reading_date,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ContactEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Contact Energy coordinator entities."""
    runtime = entry.runtime_data
    async_add_entities(
        ContactEnergySensor(
            runtime.coordinator,
            runtime.contract_key,
            str(entry.data[CONF_CONTRACT_ICP]),
            description,
        )
        for description in SENSOR_DESCRIPTIONS
    )


class ContactEnergySensor(CoordinatorEntity[ContactEnergyCoordinator], SensorEntity):
    """A Contact Energy sensor backed by the shared coordinator."""

    entity_description: ContactEnergySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ContactEnergyCoordinator,
        contract_key: str,
        icp: str,
        description: ContactEnergySensorDescription,
    ) -> None:
        """Initialize a coordinator sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{contract_key}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_key)},
            name=contract_device_name(icp),
            manufacturer=DOMAIN_NAME,
            model="Cloud account",
            configuration_url="https://auth.contact.co.nz/",
        )
        if description.device_class == SensorDeviceClass.MONETARY:
            self._attr_native_unit_of_measurement = DEFAULT_CURRENCY

    @property
    def native_value(self) -> float | date | None:
        """Return the validated coordinator value."""
        return self.entity_description.value_fn(self.coordinator.data)
