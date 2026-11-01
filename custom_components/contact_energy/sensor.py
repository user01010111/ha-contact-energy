"""Sensor platform for Contact Energy."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import ContactEnergyConfigEntry
from .const import (
    CONF_CONTRACT_ICP,
    CONF_CONTRACT_TYPE,
    DEFAULT_CURRENCY,
    DOMAIN,
    DOMAIN_NAME,
    contract_device_name,
)
from .coordinator import ContactEnergyCoordinator, ContactEnergyData
from .models import CONTACT_TIME_ZONE, gas_month_window
from .periods import PeriodSummary, percentage_change


@dataclass(frozen=True, kw_only=True)
class ContactEnergySensorDescription(SensorEntityDescription):
    """Describe a Contact Energy sensor."""

    value_fn: Callable[[ContactEnergyData], Any] | None = None
    period_key: str | None = None
    period_field: str | None = None
    comparison_key: str | None = None
    bill_field: str | None = None


def _usage_status(data: ContactEnergyData) -> str:
    """Describe source freshness independently of successful HTTP requests."""
    latest = data.statistics.latest_usage
    if latest is None:
        return "no_data"
    if dt_util.utcnow() - (latest + timedelta(hours=1)) > timedelta(hours=48):
        return "delayed"
    return "current"


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
    ContactEnergySensorDescription(
        key="latest_usage",
        name="Latest electricity usage",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.statistics.latest_usage,
    ),
    ContactEnergySensorDescription(
        key="last_successful_usage_fetch",
        name="Last successful usage fetch",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_successful_usage_fetch,
    ),
    ContactEnergySensorDescription(
        key="usage_data_status",
        name="Electricity data status",
        device_class=SensorDeviceClass.ENUM,
        options=["no_data", "current", "delayed"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_usage_status,
    ),
    ContactEnergySensorDescription(
        key="latest_day_date",
        name="Latest complete electricity day",
        device_class=SensorDeviceClass.DATE,
        period_key="latest_day",
        period_field="start",
    ),
    ContactEnergySensorDescription(
        key="latest_day_energy",
        name="Latest complete day electricity usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="latest_day",
    ),
    ContactEnergySensorDescription(
        key="latest_day_free_energy",
        name="Latest complete day free electricity",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="latest_day",
        period_field="free_energy",
    ),
    ContactEnergySensorDescription(
        key="latest_day_paid_energy",
        name="Latest complete day paid electricity",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="latest_day",
        period_field="paid_energy",
    ),
    ContactEnergySensorDescription(
        key="latest_day_cost",
        name="Latest complete day electricity cost",
        device_class=SensorDeviceClass.MONETARY,
        period_key="latest_day",
    ),
    ContactEnergySensorDescription(
        key="yesterday_energy",
        name="Yesterday electricity usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="yesterday",
        comparison_key="previous_day",
        entity_registry_enabled_default=False,
    ),
    ContactEnergySensorDescription(
        key="yesterday_free_energy",
        name="Yesterday free electricity",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="yesterday",
        period_field="free_energy",
        icon="mdi:lightning-bolt-outline",
        entity_registry_enabled_default=False,
    ),
    ContactEnergySensorDescription(
        key="yesterday_cost",
        name="Yesterday electricity cost",
        device_class=SensorDeviceClass.MONETARY,
        period_key="yesterday",
        comparison_key="previous_day",
        entity_registry_enabled_default=False,
    ),
    ContactEnergySensorDescription(
        key="last_month_energy",
        name="Last month electricity usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="last_month",
        comparison_key="previous_month",
    ),
    ContactEnergySensorDescription(
        key="last_month_free_energy",
        name="Last month free electricity",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="last_month",
        period_field="free_energy",
    ),
    ContactEnergySensorDescription(
        key="last_month_paid_energy",
        name="Last month paid electricity",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        period_key="last_month",
        period_field="paid_energy",
    ),
    ContactEnergySensorDescription(
        key="last_month_cost",
        name="Last month electricity cost",
        device_class=SensorDeviceClass.MONETARY,
        period_key="last_month",
        comparison_key="previous_month",
    ),
    ContactEnergySensorDescription(
        key="plan_name",
        name="Plan",
        icon="mdi:text-box-outline",
        value_fn=lambda data: data.plan_name,
    ),
    ContactEnergySensorDescription(
        key="billed_electricity_cost",
        name="Last billed electricity charges",
        device_class=SensorDeviceClass.MONETARY,
        icon="mdi:cash-check",
        value_fn=lambda data: (
            data.electricity_bill.cost if data.electricity_bill else None
        ),
    ),
    ContactEnergySensorDescription(
        key="usage_profile",
        name="Electricity usage profile",
        icon="mdi:chart-bar",
        native_unit_of_measurement="days",
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            data.statistics.profile.complete_days if data.statistics.profile else None
        ),
    ),
    ContactEnergySensorDescription(
        key="usage_history",
        name="Electricity usage history",
        icon="mdi:chart-timeline-variant",
        native_unit_of_measurement="days",
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            sum(day.energy is not None for day in data.statistics.history.daily)
            if data.statistics.history
            else None
        ),
    ),
    *(
        ContactEnergySensorDescription(
            key=f"{period}_{metric}",
            name=f"{name} electricity {label}",
            device_class=device_class,
            native_unit_of_measurement=unit,
            period_key=period,
            comparison_key=previous,
        )
        for period, previous, name in (
            ("month_to_date", "previous_month_to_date", "Month to date"),
            ("rolling_7_days", "previous_7_days", "Latest 7 complete days"),
            ("rolling_30_days", "previous_30_days", "Latest 30 complete days"),
        )
        for metric, label, device_class, unit in (
            ("energy", "usage", SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
            ("cost", "cost", SensorDeviceClass.MONETARY, None),
        )
    ),
)


GAS_SENSOR_DESCRIPTIONS = (
    ContactEnergySensorDescription(
        key="monthly_gas_usage",
        name="Last month reported gas usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:fire",
    ),
    *(
        description
        for description in SENSOR_DESCRIPTIONS
        if description.key
        in {"previous_reading_date", "next_reading_date", "plan_name"}
    ),
    ContactEnergySensorDescription(
        key="billed_gas_usage",
        name="Last billed gas usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        bill_field="energy",
        icon="mdi:fire",
    ),
    ContactEnergySensorDescription(
        key="billed_gas_cost",
        name="Last billed gas charges",
        device_class=SensorDeviceClass.MONETARY,
        bill_field="cost",
        icon="mdi:cash",
    ),
    ContactEnergySensorDescription(
        key="gas_reading_quality",
        name="Gas reading quality",
        device_class=SensorDeviceClass.ENUM,
        options=["actual", "estimated", "unknown"],
        bill_field="reading_quality",
    ),
    ContactEnergySensorDescription(
        key="gas_invoice_share",
        name="Gas share of current invoice charges",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        bill_field="invoice_share_percent",
        icon="mdi:percent",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ContactEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Contact Energy coordinator entities."""
    runtime = entry.runtime_data
    contract_type = entry.data.get(CONF_CONTRACT_TYPE, 1)
    async_add_entities(
        ContactEnergySensor(
            runtime.coordinator,
            runtime.contract_key,
            str(entry.data[CONF_CONTRACT_ICP]),
            description,
            contract_type,
        )
        for description in (
            GAS_SENSOR_DESCRIPTIONS if contract_type == 2 else SENSOR_DESCRIPTIONS
        )
    )


class ContactEnergySensor(CoordinatorEntity[ContactEnergyCoordinator], SensorEntity):
    """A Contact Energy sensor backed by the shared coordinator."""

    entity_description: ContactEnergySensorDescription
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"daily", "monthly"})

    def __init__(
        self,
        coordinator: ContactEnergyCoordinator,
        contract_key: str,
        icp: str,
        description: ContactEnergySensorDescription,
        contract_type: int = 1,
    ) -> None:
        """Initialize a coordinator sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{contract_key}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_key)},
            name=contract_device_name(icp, contract_type),
            manufacturer=DOMAIN_NAME,
            model="Cloud account",
            configuration_url="https://auth.contact.co.nz/",
        )
        if (
            description.device_class == SensorDeviceClass.MONETARY
            and description.period_key is None
        ):
            self._attr_native_unit_of_measurement = DEFAULT_CURRENCY

    @property
    def available(self) -> bool:
        """Keep unsupported bills unavailable without repeatedly retrying setup."""
        if self.entity_description.key == "billed_electricity_cost":
            return super().available and self.coordinator.data.electricity_bill_valid
        if self.entity_description.key == "monthly_gas_usage":
            return super().available and self.coordinator.data.gas_monthly_valid
        return super().available and (
            not self.entity_description.bill_field
            or self.coordinator.data.gas_bill_valid
        )

    @property
    def native_value(self) -> float | date | datetime | str | None:
        """Return the validated coordinator value."""
        description = self.entity_description
        if description.key == "monthly_gas_usage":
            today = dt_util.utcnow().astimezone(CONTACT_TIME_ZONE).date()
            month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            return next(
                (
                    p.energy
                    for p in self.coordinator.data.gas_monthly
                    if p.start == month
                ),
                None,
            )
        if description.bill_field:
            bill = self.coordinator.data.gas_bill
            return getattr(bill, description.bill_field) if bill is not None else None
        if description.period_key:
            period = self.coordinator.data.statistics.periods.get(
                description.period_key
            )
            if period is None:
                return None
            if description.period_field:
                return getattr(period, description.period_field)
            if description.device_class == SensorDeviceClass.MONETARY:
                return period.cost
            return period.energy
        if description.value_fn is not None:
            return description.value_fn(self.coordinator.data)
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Use the reported period currency rather than an account default."""
        description = self.entity_description
        if (
            description.period_key
            and description.device_class == SensorDeviceClass.MONETARY
        ):
            period = self.coordinator.data.statistics.periods.get(
                description.period_key
            )
            return period.currency if period is not None else None
        return super().native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose period coverage and comparable daily averages, not partial totals."""
        description = self.entity_description
        if description.key == "monthly_gas_usage":
            data = self.coordinator.data
            start, end = gas_month_window(
                dt_util.utcnow().astimezone(CONTACT_TIME_ZONE).date()
            )
            return {
                "period_start": (end - timedelta(days=1)).replace(day=1).isoformat(),
                "period_end_exclusive": end.isoformat(),
                "last_successful_fetch": data.gas_monthly_updated.isoformat()
                if data.gas_monthly_updated
                else None,
                "monthly": [
                    {"start": p.start.isoformat(), "energy_kwh": p.energy}
                    for p in data.gas_monthly
                    if start <= p.start < end
                ],
            }
        if description.key == "billed_electricity_cost":
            bill = self.coordinator.data.electricity_bill
            if bill is None:
                return None
            return {
                "period_start": bill.start.isoformat(),
                "period_end": bill.end.isoformat(),
                "days": bill.days,
                "includes_gst": True,
                "charges_excl_gst": bill.cost_excl_gst,
                "gst": bill.gst,
            }
        if description.key == "usage_history":
            history = self.coordinator.data.statistics.history
            if history is None:
                return None
            return {
                "daily": [_history_row(period) for period in history.daily],
                "monthly": [_history_row(period) for period in history.monthly],
            }
        if description.key == "usage_profile":
            profile = self.coordinator.data.statistics.profile
            if profile is None:
                return None
            return {
                "period_start": profile.start.isoformat(),
                "period_end_exclusive": profile.end.isoformat(),
                "complete_days": profile.complete_days,
                "hourly_average_kwh": dict(enumerate(profile.hourly_average)),
                "hourly_samples": dict(enumerate(profile.hourly_samples)),
                "weekday_average_kwh": dict(enumerate(profile.weekday_average)),
                "weekday_samples": dict(enumerate(profile.weekday_samples)),
            }
        if description.key == "next_reading_date":
            estimate = self.coordinator.data.account.next_reading_is_estimate
            return (
                {"next_reading_is_estimate": estimate} if estimate is not None else None
            )
        if description.bill_field:
            bill = self.coordinator.data.gas_bill
            if bill is None:
                return None
            attributes = {
                "period_start": bill.start.isoformat(),
                "period_end": bill.end.isoformat(),
                "days": bill.days,
                "reading_quality": bill.reading_quality,
            }
            if description.bill_field == "cost":
                attributes.update(
                    {
                        "includes_gst": True,
                        "charges_excl_gst": bill.cost_excl_gst,
                        "gst": bill.gst,
                        "usage_charges_excl_gst": bill.variable_cost_excl_gst,
                        "fixed_charges_excl_gst": bill.fixed_cost_excl_gst,
                    }
                )
            if description.bill_field == "invoice_share_percent":
                attributes.update(
                    {
                        "includes_gst": True,
                        "invoice_charges_incl_gst": bill.invoice_charges_incl_gst,
                        "gas_charges_incl_gst": bill.cost,
                    }
                )
            return attributes
        if not description.period_key:
            return None
        periods = self.coordinator.data.statistics.periods
        period = periods.get(description.period_key)
        if period is None:
            return None
        if description.period_key == "latest_day" or description.period_field in {
            "free_energy",
            "paid_energy",
        }:
            attributes = {
                "period_start": period.start.isoformat(),
                "period_end_exclusive": period.end.isoformat(),
                "expected_hours": period.expected_hours,
                "received_hours": period.received_hours,
                "cost_hours": period.cost_hours,
                "free_hours": period.free_hours,
                "total_kwh": period.energy,
                "free_kwh": period.free_energy,
                "paid_kwh": period.paid_energy,
                "free_percentage": period.free_percentage,
            }
            if description.key == "latest_day_date":
                statistics = self.coordinator.data.statistics
                observed = statistics.first_complete_observed
                started = statistics.observation_started
                end = datetime.combine(period.end, time(), CONTACT_TIME_ZONE)
                attributes.update(
                    {
                        "first_observed_complete": observed.isoformat()
                        if observed
                        else None,
                        "age_hours_at_first_observation": (
                            (observed - end).total_seconds() / 3600
                            if observed
                            else None
                        ),
                        "period_predates_tracking": end <= started if started else None,
                    }
                )
            return attributes
        previous = periods.get(description.comparison_key)
        monetary = description.device_class == SensorDeviceClass.MONETARY
        average = period.cost_per_day if monetary else period.energy_per_day
        previous_average = None
        if previous is not None:
            if monetary and period.currency == previous.currency:
                previous_average = previous.cost_per_day
            elif not monetary:
                previous_average = previous.energy_per_day
        attributes = {
            "period_start": period.start.isoformat(),
            "period_end_exclusive": period.end.isoformat(),
            "expected_hours": period.expected_hours,
            "received_hours": period.received_hours,
            "cost_hours": period.cost_hours,
            "daily_average": average,
            "change_percent": percentage_change(average, previous_average),
            "comparison_basis": "daily_average",
            "comparison_start": previous.start.isoformat() if previous else None,
            "comparison_end_exclusive": previous.end.isoformat() if previous else None,
        }
        if description.period_key in {
            "month_to_date",
            "rolling_7_days",
            "rolling_30_days",
        }:
            attributes.update(
                {
                    "covered_through": (period.end - timedelta(days=1)).isoformat(),
                    "free_hours": period.free_hours,
                    "total_kwh": period.energy,
                    "free_kwh": period.free_energy,
                    "paid_kwh": period.paid_energy,
                    "free_percentage": period.free_percentage,
                }
            )
        return attributes


def _history_row(period: PeriodSummary) -> dict[str, Any]:
    """Expose only period totals/coverage and an unambiguous consumption date."""
    return {
        "start": datetime.combine(period.start, time(), CONTACT_TIME_ZONE).isoformat(),
        "end_exclusive": datetime.combine(
            period.end, time(), CONTACT_TIME_ZONE
        ).isoformat(),
        "total_kwh": period.energy,
        "free_kwh": period.free_energy,
        "paid_kwh": period.paid_energy,
        "expected_hours": period.expected_hours,
        "received_hours": period.received_hours,
        "free_hours": period.free_hours,
    }
