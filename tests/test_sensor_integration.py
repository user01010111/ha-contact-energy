"""Exercise the sensor platform with real HA setup and synthetic provider data."""

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.const import Platform
from homeassistant.helpers import entity_registry as er

from custom_components.contact_energy import _contract_key
from custom_components.contact_energy.const import DOMAIN
from custom_components.contact_energy.models import CONTACT_TIME_ZONE
from custom_components.contact_energy.sensor import SENSOR_DESCRIPTIONS

from .conftest import TEST_ICP


async def test_new_sensors_register_reload_and_preserve_legacy_entity_id(
    hass, mock_config_entry, accounts_payload, recorder_dependency, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda *_args: None,
    )

    async def usage(requested_date):
        requested = date.fromisoformat(requested_date)
        start = datetime.combine(requested, time(), CONTACT_TIME_ZONE).astimezone(UTC)
        end = datetime.combine(
            requested + timedelta(days=1), time(), CONTACT_TIME_ZONE
        ).astimezone(UTC)
        return [
            {
                "date": (start + timedelta(hours=hour)).isoformat(),
                "value": 1.0,
                "dollarValue": 0.25,
                "currency": "NZD",
                "unit": "kWh",
            }
            for hour in range(int((end - start).total_seconds() / 3600))
        ]

    mock_config_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    legacy = registry.async_get_or_create(
        Platform.SENSOR,
        DOMAIN,
        f"{DOMAIN}_{TEST_ICP}_usage",
        config_entry=mock_config_entry,
        suggested_object_id="legacy_electricity_usage",
    )
    with (
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_accounts",
            AsyncMock(return_value=accounts_payload),
        ),
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_usage",
            AsyncMock(side_effect=usage),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        key = _contract_key(mock_config_entry)
        entity_ids = {
            description.key: registry.async_get_entity_id(
                Platform.SENSOR, DOMAIN, f"{key}_{description.key}"
            )
            for description in SENSOR_DESCRIPTIONS
        }
        assert len(entity_ids) == 15
        assert all(entity_ids.values())
        assert entity_ids["usage"] == legacy.entity_id
        assert hass.states.get(entity_ids["usage_data_status"]).state == "current"
        assert hass.states.get(entity_ids["latest_usage"]).state != "unknown"
        assert hass.states.get(entity_ids["last_successful_usage_fetch"]).state != (
            "unknown"
        )
        yesterday = hass.states.get(entity_ids["yesterday_energy"])
        cost = hass.states.get(entity_ids["yesterday_cost"])
        hours = yesterday.attributes["expected_hours"]
        assert float(yesterday.state) == hours
        assert float(cost.state) == hours * 0.25
        assert cost.attributes["unit_of_measurement"] == "NZD"
        assert "state_class" not in yesterday.attributes
        assert "state_class" not in cost.attributes
        assert hass.states.get(entity_ids["last_month_energy"]).state == "unknown"
        before = hass.states.get(legacy.entity_id).state

        coordinator = mock_config_entry.runtime_data.coordinator
        coordinator.async_set_update_error(Exception("synthetic connection failure"))
        await hass.async_block_till_done()
        assert hass.states.get(entity_ids["usage_data_status"]).state == "unavailable"
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entity_ids["usage_data_status"]).state == "current"

        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(legacy.entity_id).state == before
        assert (
            registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{key}_usage")
            == legacy.entity_id
        )
        assert hass.states.get(entity_ids["yesterday_energy"]).state == yesterday.state
        assert (
            len(er.async_entries_for_config_entry(registry, mock_config_entry.entry_id))
            == 15
        )
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
