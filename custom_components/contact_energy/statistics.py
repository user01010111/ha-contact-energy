"""Stable external-statistics accumulation for Contact Energy usage."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MAX_USAGE_DAYS, STORAGE_KEY_PREFIX, STORAGE_VERSION
from .models import CONTACT_TIME_ZONE, UsagePoint

_LOGGER = logging.getLogger(__name__)


class StatisticsStore(Protocol):
    """Storage protocol used by the statistics accumulator."""

    async def async_load(self) -> dict[str, Any] | None:
        """Load stored statistics state."""

    async def async_save(self, data: dict[str, Any]) -> None:
        """Save statistics state."""


@dataclass(frozen=True, slots=True)
class StatisticsResult:
    """Current lifetime totals exposed to entities."""

    energy: float
    cost: float
    currency: str | None


class ContactEnergyStatistics:
    """Persist a bounded correction window and stable lifetime totals."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        contract_key: str,
        store: StatisticsStore | None = None,
    ) -> None:
        """Initialize the accumulator."""
        self._hass = hass
        self._contract_key = contract_key
        self._store = store or Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry_id}",
        )
        self._loaded = False
        self._archive_energy = 0.0
        self._archive_cost = 0.0
        self._currency: str | None = None
        self._points: dict[str, dict[str, float | None]] = {}

    @property
    def energy_statistic_id(self) -> str:
        """Return the contract-scoped energy statistic ID."""
        return f"{DOMAIN}:energy_consumption_{self._contract_key}"

    @property
    def cost_statistic_id(self) -> str:
        """Return the contract-scoped cost statistic ID."""
        return f"{DOMAIN}:energy_cost_{self._contract_key}"

    async def async_load(self) -> None:
        """Load strictly validated persisted accumulator state once."""
        if self._loaded:
            return
        stored = await self._store.async_load()
        if stored is None:
            self._loaded = True
            return
        if not isinstance(stored, dict):
            raise StatisticsStateError("Contact Energy statistics storage is invalid")

        self._archive_energy = _stored_nonnegative_number(
            stored.get("archive_energy"), "archive energy"
        )
        self._archive_cost = _stored_nonnegative_number(
            stored.get("archive_cost"), "archive cost"
        )
        currency = stored.get("currency")
        if currency is not None and not _valid_currency(currency):
            raise StatisticsStateError("Contact Energy statistics currency is invalid")
        self._currency = currency
        if self._archive_cost and self._currency is None:
            raise StatisticsStateError("Contact Energy statistics currency is missing")

        raw_points = stored.get("points")
        if not isinstance(raw_points, dict):
            raise StatisticsStateError("Contact Energy statistics points are invalid")
        normalized_timestamps: set[str] = set()
        for timestamp, values in raw_points.items():
            if not isinstance(timestamp, str) or not isinstance(values, dict):
                raise StatisticsStateError("Contact Energy statistics point is invalid")
            parsed = dt_util.parse_datetime(timestamp)
            if not _valid_hour_start(parsed):
                raise StatisticsStateError(
                    "Contact Energy statistics timestamp is invalid"
                )
            normalized_timestamp = dt_util.as_utc(parsed).isoformat()
            if normalized_timestamp in normalized_timestamps:
                raise StatisticsStateError(
                    "Contact Energy statistics contain a duplicate timestamp"
                )
            normalized_timestamps.add(normalized_timestamp)
            energy = _stored_nonnegative_number(values.get("energy"), "energy")
            cost = _stored_optional_nonnegative_number(values.get("cost"), "cost")
            if cost is not None and self._currency is None:
                raise StatisticsStateError(
                    "Contact Energy statistics currency is missing"
                )
            self._points[normalized_timestamp] = {
                "energy": energy,
                "cost": cost,
            }
        self._loaded = True

    async def async_process(self, usage_points: list[UsagePoint]) -> StatisticsResult:
        """Merge corrections, replay retained sums, and persist lifetime state."""
        await self.async_load()
        state_changed = False

        points = _deduplicated_points(usage_points)
        batch_currencies = {
            point.currency
            for point in points
            if point.cost is not None and point.currency is not None
        }
        if self._currency is None and len(batch_currencies) == 1:
            self._currency = batch_currencies.pop()
            state_changed = True
        elif self._currency is None and len(batch_currencies) > 1:
            _LOGGER.debug("Ignoring usage costs from an inconsistent currency batch")

        for point in points:
            _validate_usage_point(point)
            timestamp = dt_util.as_utc(point.start).isoformat()
            old = self._points.get(timestamp)
            old_cost = old.get("cost") if old is not None else None
            cost = old_cost
            if point.cost is not None and point.currency == self._currency:
                cost = point.cost
            elif point.cost is not None:
                _LOGGER.debug("Ignoring a usage cost with an inconsistent currency")

            new = {"energy": point.energy, "cost": cost}
            if old == new:
                continue
            self._points[timestamp] = new
            state_changed = True

        energy_total = self._archive_energy
        cost_total = self._archive_cost
        energy_rows: list[StatisticData] = []
        cost_rows: list[StatisticData] = []
        for timestamp, values in sorted(self._points.items()):
            start = dt_util.parse_datetime(timestamp)
            if start is None:
                continue
            start = dt_util.as_utc(start)
            energy_total += float(values["energy"] or 0.0)
            if values["cost"] is not None:
                cost_total += float(values["cost"])
            energy_rows.append(StatisticData(start=start, sum=energy_total))
            if self._currency:
                cost_rows.append(StatisticData(start=start, sum=cost_total))

        contact_today = dt_util.utcnow().astimezone(CONTACT_TIME_ZONE).date()
        oldest_refetched_date = contact_today - timedelta(days=MAX_USAGE_DAYS)
        for timestamp in sorted(self._points):
            parsed = dt_util.parse_datetime(timestamp)
            if (
                parsed is None
                or parsed.astimezone(CONTACT_TIME_ZONE).date() >= oldest_refetched_date
            ):
                continue
            archived = self._points.pop(timestamp)
            self._archive_energy += float(archived["energy"] or 0.0)
            self._archive_cost += float(archived["cost"] or 0.0)
            state_changed = True

        if energy_rows:
            async_add_external_statistics(
                self._hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=(
                        "Contact Energy electricity consumption "
                        f"({self._contract_key[:8]})"
                    ),
                    source=DOMAIN,
                    statistic_id=self.energy_statistic_id,
                    unit_class="energy",
                    unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                ),
                energy_rows,
            )
        if cost_rows and self._currency:
            async_add_external_statistics(
                self._hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=f"Contact Energy electricity cost ({self._contract_key[:8]})",
                    source=DOMAIN,
                    statistic_id=self.cost_statistic_id,
                    unit_class=None,
                    unit_of_measurement=self._currency,
                ),
                cost_rows,
            )

        if state_changed:
            await self._store.async_save(
                {
                    "archive_energy": self._archive_energy,
                    "archive_cost": self._archive_cost,
                    "currency": self._currency,
                    "points": self._points,
                }
            )

        return StatisticsResult(
            energy=energy_total,
            cost=cost_total,
            currency=self._currency,
        )


class StatisticsStateError(HomeAssistantError):
    """Persisted statistics state is unsafe to use."""


class StatisticsDataError(HomeAssistantError):
    """A usage point violates the internal statistics contract."""


def _stored_nonnegative_number(value: Any, label: str) -> float:
    """Return a finite nonnegative stored number or raise."""
    parsed = _stored_optional_nonnegative_number(value, label)
    if parsed is None:
        raise StatisticsStateError(f"Contact Energy statistics {label} is missing")
    return parsed


def _stored_optional_nonnegative_number(value: Any, label: str) -> float | None:
    """Return an optional finite nonnegative stored number or raise."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StatisticsStateError(f"Contact Energy statistics {label} is invalid")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise StatisticsStateError(f"Contact Energy statistics {label} is invalid")
    return parsed


def _valid_currency(value: Any) -> bool:
    """Return whether a persisted value is an uppercase ISO-style currency."""
    return (
        isinstance(value, str)
        and len(value) == 3
        and value.isalpha()
        and value.isupper()
    )


def _valid_hour_start(value: Any) -> bool:
    """Return whether a value is an aware top-of-hour datetime."""
    return bool(
        value is not None
        and value.tzinfo is not None
        and not value.minute
        and not value.second
        and not value.microsecond
    )


def _validate_usage_point(point: UsagePoint) -> None:
    """Enforce the validated model contract before mutating persistent state."""
    if (
        not _valid_hour_start(point.start)
        or not math.isfinite(point.energy)
        or point.energy < 0
        or (
            point.cost is not None and (not math.isfinite(point.cost) or point.cost < 0)
        )
        or (point.currency is not None and not _valid_currency(point.currency))
        or (point.cost is not None and point.currency is None)
    ):
        raise StatisticsDataError("Contact Energy usage point is invalid")


def _deduplicated_points(points: list[UsagePoint]) -> list[UsagePoint]:
    """Return deterministic points while rejecting conflicting duplicates."""
    selected: dict[str, UsagePoint] = {}
    conflicts: set[str] = set()
    for point in points:
        _validate_usage_point(point)
        timestamp = dt_util.as_utc(point.start).isoformat()
        previous = selected.get(timestamp)
        if previous is not None and previous != point:
            conflicts.add(timestamp)
            continue
        selected[timestamp] = point
    if conflicts:
        _LOGGER.debug("Ignoring conflicting duplicate Contact usage points")
    return [
        selected[timestamp]
        for timestamp in sorted(selected)
        if timestamp not in conflicts
    ]
