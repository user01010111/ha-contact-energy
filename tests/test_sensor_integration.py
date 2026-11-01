"""Exercise the sensor platform with real HA setup and synthetic provider data."""

import json
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder.db_schema import StateAttributes
from homeassistant.const import Platform
from homeassistant.core import Event
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.contact_energy import _contract_key
from custom_components.contact_energy.const import DOMAIN
from custom_components.contact_energy.models import CONTACT_TIME_ZONE
from custom_components.contact_energy.sensor import SENSOR_DESCRIPTIONS

from .conftest import TEST_ICP


@pytest.mark.parametrize(("existing_yesterday", "delay_days"), [(True, 0), (False, 2)])
async def test_new_sensors_register_reload_and_preserve_legacy_entity_id(
    hass,
    mock_config_entry,
    accounts_payload,
    recorder_dependency,
    monkeypatch,
    existing_yesterday,
    delay_days,
) -> None:
    monkeypatch.setattr(
        "custom_components.contact_energy.coordinator.HISTORY_REQUEST_SPACING", 0
    )
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda *_args: None,
    )

    today = dt_util.utcnow().astimezone(CONTACT_TIME_ZONE).date()

    async def usage(requested_date):
        requested = date.fromisoformat(requested_date)
        if (today - requested).days <= delay_days:
            return []
        start = datetime.combine(requested, time(), CONTACT_TIME_ZONE).astimezone(UTC)
        end = datetime.combine(
            requested + timedelta(days=1), time(), CONTACT_TIME_ZONE
        ).astimezone(UTC)
        return [
            {
                "date": (start + timedelta(hours=hour)).isoformat(),
                "value": 1.0,
                "unchargedValue": 0.25,
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
    key = _contract_key(mock_config_entry)
    yesterday_keys = ("yesterday_energy", "yesterday_free_energy", "yesterday_cost")
    if existing_yesterday:
        for sensor_key in yesterday_keys:
            registry.async_get_or_create(
                Platform.SENSOR,
                DOMAIN,
                f"{key}_{sensor_key}",
                config_entry=mock_config_entry,
            )
    with (
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_bill_transactions",
            AsyncMock(return_value={"transactions": []}),
        ),
        patch(
            "custom_components.contact_energy.ContactEnergyApi.async_get_plan_details",
            AsyncMock(return_value={}),
        ),
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
        assert len(entity_ids) == 33
        assert all(entity_ids.values())
        profile_entry = registry.async_get(entity_ids["usage_profile"])
        assert profile_entry.disabled
        registry.async_update_entity(profile_entry.entity_id, disabled_by=None)
        history_entry = registry.async_get(entity_ids["usage_history"])
        assert history_entry.disabled
        registry.async_update_entity(history_entry.entity_id, disabled_by=None)
        for sensor_key in yesterday_keys:
            entity = registry.async_get(entity_ids[sensor_key])
            assert entity.disabled == (not existing_yesterday)
            if not existing_yesterday:
                assert hass.states.get(entity.entity_id) is None
                registry.async_update_entity(entity.entity_id, disabled_by=None)
        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entity_ids["usage"] == legacy.entity_id
        status = "delayed" if delay_days else "current"
        assert hass.states.get(entity_ids["usage_data_status"]).state == status
        assert hass.states.get(entity_ids["latest_usage"]).state != "unknown"
        assert hass.states.get(entity_ids["last_successful_usage_fetch"]).state != (
            "unknown"
        )
        yesterday = hass.states.get(entity_ids["yesterday_energy"])
        cost = hass.states.get(entity_ids["yesterday_cost"])
        hours = yesterday.attributes["expected_hours"]
        free = hass.states.get(entity_ids["yesterday_free_energy"])
        if delay_days:
            assert yesterday.state == cost.state == free.state == "unknown"
            assert free.attributes["free_percentage"] is None
            assert free.attributes["free_hours"] == 0
        else:
            assert float(yesterday.state) == hours
            assert float(cost.state) == hours * 0.25
            assert float(free.state) == hours * 0.25
            assert free.attributes["free_percentage"] == 25
            assert free.attributes["free_hours"] == hours
        assert free.attributes["unit_of_measurement"] == "kWh"
        assert "state_class" not in free.attributes
        if not delay_days:
            assert cost.attributes["unit_of_measurement"] == "NZD"
        assert "state_class" not in yesterday.attributes
        assert "state_class" not in cost.attributes
        assert hass.states.get(entity_ids["last_month_energy"]).state == "unknown"
        assert hass.states.get(entity_ids["last_month_free_energy"]).state == "unknown"
        assert hass.states.get(entity_ids["last_month_paid_energy"]).state == "unknown"
        for period in ("rolling_7_days", "rolling_30_days"):
            for metric in ("energy", "cost"):
                state = hass.states.get(entity_ids[f"{period}_{metric}"])
                assert state.state == "unknown"  # Only three days were requested.
                assert "state_class" not in state.attributes
                assert (
                    state.attributes["covered_through"]
                    == (today - timedelta(days=delay_days + 1)).isoformat()
                )
        latest_date = (today - timedelta(days=delay_days + 1)).isoformat()
        assert hass.states.get(entity_ids["latest_day_date"]).state == latest_date
        profile = hass.states.get(entity_ids["usage_profile"])
        assert int(profile.state) == 3 - delay_days
        assert profile.attributes["complete_days"] == 3 - delay_days
        assert len(profile.attributes["hourly_average_kwh"]) == 24
        assert len(profile.attributes["weekday_samples"]) == 7
        assert "state_class" not in profile.attributes
        history = hass.states.get(entity_ids["usage_history"])
        assert int(history.state) == 3 - delay_days
        assert len(history.attributes["daily"]) == 93
        assert len(history.attributes["monthly"]) <= 4
        assert history.attributes["daily"][-1]["free_kwh"] == (
            None if delay_days else hours * 0.25
        )
        assert datetime.fromisoformat(
            history.attributes["daily"][-1]["start"]
        ).date() == (today - timedelta(days=1))
        recorded_attributes = json.loads(
            StateAttributes.shared_attrs_bytes_from_event(
                Event("state_changed", {"new_state": history}), None
            )
        )
        assert "daily" not in recorded_attributes
        assert "monthly" not in recorded_attributes
        assert recorded_attributes["unit_of_measurement"] == "days"
        latest = hass.states.get(entity_ids["latest_day_energy"])
        latest_free = hass.states.get(entity_ids["latest_day_free_energy"])
        latest_paid = hass.states.get(entity_ids["latest_day_paid_energy"])
        latest_hours = latest.attributes["expected_hours"]
        assert float(latest.state) == latest_hours
        assert float(latest_free.state) == latest_hours * 0.25
        assert float(latest_paid.state) == latest_hours * 0.75
        assert latest_free.attributes["free_percentage"] == 25
        assert latest_free.attributes["period_start"] == latest_date
        assert (
            float(hass.states.get(entity_ids["latest_day_cost"]).state)
            == latest_hours * 0.25
        )
        assert all(
            "state_class" not in state.attributes
            for state in (latest, latest_free, latest_paid)
        )
        before = hass.states.get(legacy.entity_id).state

        coordinator = mock_config_entry.runtime_data.coordinator
        coordinator.async_set_update_error(Exception("synthetic connection failure"))
        await hass.async_block_till_done()
        assert hass.states.get(entity_ids["usage_data_status"]).state == "unavailable"
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entity_ids["usage_data_status"]).state == status

        assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(legacy.entity_id).state == before
        assert (
            registry.async_get_entity_id(Platform.SENSOR, DOMAIN, f"{key}_usage")
            == legacy.entity_id
        )
        assert hass.states.get(entity_ids["yesterday_energy"]).state == yesterday.state
        assert hass.states.get(entity_ids["yesterday_free_energy"]).state == free.state
        assert hass.states.get(entity_ids["latest_day_date"]).state == latest_date
        assert (
            hass.states.get(entity_ids["latest_day_free_energy"]).state
            == latest_free.state
        )
        assert (
            len(er.async_entries_for_config_entry(registry, mock_config_entry.entry_id))
            == 33
        )
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
