"""Tests for contract-scoped stable cumulative statistics."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.contact_energy.models import UsagePoint
from custom_components.contact_energy.statistics import (
    ContactEnergyStatistics,
    StatisticsStateError,
)


class FakeStore:
    """In-memory Home Assistant Store substitute."""

    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        """Load a copy of persisted state."""
        return deepcopy(self.data)

    async def async_save(self, data):
        """Persist a copy of accumulator state."""
        self.data = deepcopy(data)


def _point(start, energy, cost=0.0) -> UsagePoint:
    return UsagePoint(start=start, energy=energy, cost=cost, currency="NZD")


async def test_sums_stay_stable_across_overlap_gap_fill_and_restart(
    hass, monkeypatch
) -> None:
    imported = []

    def capture(_hass, metadata, rows):
        imported.append((metadata, list(rows)))

    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        capture,
    )
    store = FakeStore()
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    first = _point(now - timedelta(hours=4), 1.0, 0.2)
    gap = _point(now - timedelta(hours=3), 0.5, 0.1)
    second = _point(now - timedelta(hours=2), 2.0, 0.4)
    third = _point(now - timedelta(hours=1), 3.0, 0.6)

    statistics = ContactEnergyStatistics(
        hass,
        "entry-example",
        "contract-key-a",
        store,
    )
    result = await statistics.async_process([first, second])
    assert result.energy == 3.0
    energy_rows = next(
        rows
        for metadata, rows in imported
        if metadata["statistic_id"] == statistics.energy_statistic_id
    )
    assert [row["sum"] for row in energy_rows] == [1.0, 3.0]

    imported.clear()
    result = await statistics.async_process([first, second, third])
    assert result.energy == 6.0
    energy_rows = next(
        rows
        for metadata, rows in imported
        if metadata["statistic_id"] == statistics.energy_statistic_id
    )
    assert [row["sum"] for row in energy_rows] == [1.0, 3.0, 6.0]

    imported.clear()
    restarted = ContactEnergyStatistics(
        hass,
        "entry-example",
        "contract-key-a",
        store,
    )
    result = await restarted.async_process([first, second, third])
    assert result.energy == 6.0
    assert len(imported) == 2

    imported.clear()
    result = await restarted.async_process([first, gap, second, third])
    assert result.energy == 6.5
    energy_rows = next(
        rows
        for metadata, rows in imported
        if metadata["statistic_id"] == restarted.energy_statistic_id
    )
    assert [row["sum"] for row in energy_rows] == [1.0, 1.5, 3.5, 6.5]


async def test_inconsistent_currency_does_not_erase_stored_cost(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda *_args: None,
    )
    store = FakeStore()
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    statistics = ContactEnergyStatistics(
        hass,
        "entry-example",
        "contract-key-a",
        store,
    )
    original = _point(now - timedelta(hours=1), 1.0, 0.25)
    await statistics.async_process([original])

    correction = UsagePoint(
        start=original.start,
        energy=1.2,
        cost=99.0,
        currency="AUD",
    )
    result = await statistics.async_process([correction])

    assert result.energy == 1.2
    assert result.cost == 0.25
    assert result.currency == "NZD"


async def test_oldest_refetched_date_is_not_archived_or_recounted(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda *_args: None,
    )
    now = datetime(2026, 8, 9, tzinfo=UTC)
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.dt_util.utcnow",
        lambda: now,
    )
    store = FakeStore()
    point = _point(datetime(2026, 7, 9, tzinfo=UTC), 1.0, 0.25)
    statistics = ContactEnergyStatistics(hass, "entry", "contract", store)

    assert (await statistics.async_process([point])).energy == 1.0
    assert (await statistics.async_process([point])).energy == 1.0
    restarted = ContactEnergyStatistics(hass, "entry", "contract", store)
    assert (await restarted.async_process([point])).energy == 1.0
    assert len(store.data["points"]) == 1


async def test_point_gets_one_final_replay_when_it_crosses_archive_cutoff(
    hass, monkeypatch
) -> None:
    imported = []
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda _hass, metadata, rows: imported.append((metadata, list(rows))),
    )
    current = datetime(2026, 8, 8, tzinfo=UTC)
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.dt_util.utcnow",
        lambda: current,
    )
    store = FakeStore()
    point = _point(datetime(2026, 7, 8, tzinfo=UTC), 1.0, 0.25)
    statistics = ContactEnergyStatistics(hass, "entry", "contract", store)
    await statistics.async_process([point])

    imported.clear()
    current = datetime(2026, 8, 9, tzinfo=UTC)
    result = await statistics.async_process([])

    energy_rows = next(
        rows
        for metadata, rows in imported
        if metadata["statistic_id"] == statistics.energy_statistic_id
    )
    assert [row["sum"] for row in energy_rows] == [1.0]
    assert result.energy == 1.0
    assert store.data["archive_energy"] == 1.0
    assert store.data["points"] == {}


async def test_partial_cost_preserves_known_value_and_does_not_lock_currency(
    hass, monkeypatch
) -> None:
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda *_args: None,
    )
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    store = FakeStore()
    statistics = ContactEnergyStatistics(hass, "entry", "contract", store)
    original = _point(now - timedelta(hours=2), 1.0, 0.25)
    await statistics.async_process([original])

    partial = UsagePoint(original.start, 1.2, None, None)
    result = await statistics.async_process([partial])
    assert result.cost == 0.25

    fresh_store = FakeStore()
    fresh = ContactEnergyStatistics(hass, "entry-2", "contract", fresh_store)
    no_cost = UsagePoint(now - timedelta(hours=1), 1.0, None, "AUD")
    await fresh.async_process([no_cost])
    valid = _point(no_cost.start, 1.0, 0.4)
    result = await fresh.async_process([valid])
    assert result.currency == "NZD"
    assert result.cost == 0.4


async def test_malformed_persistent_state_blocks_unsafe_totals(hass) -> None:
    store = FakeStore()
    store.data = {
        "archive_energy": -1,
        "archive_cost": 0,
        "currency": "123",
        "points": {},
    }
    statistics = ContactEnergyStatistics(hass, "entry", "contract", store)

    with pytest.raises(StatisticsStateError):
        await statistics.async_load()


async def test_conflicting_duplicate_timestamp_is_ignored(hass, monkeypatch) -> None:
    imported = []
    monkeypatch.setattr(
        "custom_components.contact_energy.statistics.async_add_external_statistics",
        lambda *args: imported.append(args),
    )
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    statistics = ContactEnergyStatistics(hass, "entry", "contract", FakeStore())

    result = await statistics.async_process(
        [_point(now, 1.0, 0.2), _point(now, 2.0, 0.4)]
    )

    assert result.energy == 0
    assert imported == []


def test_multiple_contracts_have_unique_statistic_ids(hass) -> None:
    first = ContactEnergyStatistics(hass, "entry-a", "contract-a", FakeStore())
    second = ContactEnergyStatistics(hass, "entry-b", "contract-b", FakeStore())

    assert first.energy_statistic_id != second.energy_statistic_id
    assert first.cost_statistic_id != second.cost_statistic_id
