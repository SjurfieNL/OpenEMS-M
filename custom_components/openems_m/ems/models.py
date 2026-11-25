# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Datamodellen voor OpenEMS-M.

Deze module bevat uitsluitend data. Geen Home Assistant-, planner- of
controllerlogica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class SchedulerState(str, Enum):
    IDLE = "idle"
    CHARGING = "charging"
    DISCHARGING = "discharging"
    SELF_CONSUMPTION = "self_consumption"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class BatteryState:
    names: list[str]
    soc_values: list[float]
    capacities: list[float]
    capacity: float
    average_soc: float
    stored_energy: float
    max_charge_values: list[float]
    max_discharge_values: list[float]
    max_charge: float
    max_discharge: float
    available: list[bool] = field(default_factory=list)


@dataclass(frozen=True)
class ChargePlan:
    start: datetime
    end: datetime
    power: float
    hours: float
    average_price: float
    cheapest_price: float
    price_limit: float
    slots: list[dict[str, Any]] = field(default_factory=list)
    eligible_slots: list[dict[str, Any]] = field(default_factory=list)
    block_is_active: bool = False
    effective_charge_hours: float = 0.0


@dataclass(frozen=True)
class BatteryChargeSegment:
    start: datetime
    end: datetime
    power: float
    price: float

    @property
    def hours(self) -> float:
        return max(
            0.0, (self.end.timestamp() - self.start.timestamp()) / 3600
        )

    @property
    def energy_kwh(self) -> float:
        return self.power / 1000 * self.hours


@dataclass(frozen=True)
class BatteryChargePlan:
    index: int
    name: str
    current_soc: float
    target_soc: float
    capacity: float
    battery_energy_needed: float
    grid_energy_needed: float
    minimum_power: float
    planned_power: float
    average_price: float
    charge_efficiency: float = 1.0
    preferred_min_power: float = 1000.0
    preferred_max_power: float = 1500.0
    power_strategy: str = "voorkeursbereik"
    segments: list[BatteryChargeSegment] = field(default_factory=list)

    @property
    def start(self) -> Optional[datetime]:
        return min((item.start for item in self.segments), default=None)

    @property
    def end(self) -> Optional[datetime]:
        return max((item.end for item in self.segments), default=None)

    @property
    def duration_hours(self) -> float:
        return sum(item.hours for item in self.segments)


@dataclass(frozen=True)
class DischargeBlock:
    start: datetime
    end: datetime
    average_price: float
    highest_price: float
    slots: list[dict[str, Any]] = field(default_factory=list)
    expected_energy: float = 0.0
    energy_cost: float = 0.0
    avoided_cost: float = 0.0
    conversion_loss: float = 0.0
    net_profit: float = 0.0
    profitable: bool = True
    planned_total_power: float = 0.0
    planned_export_power: float = 0.0
    powers_per_battery: list[float] = field(default_factory=list)
    reserve_soc: Optional[float] = None
    cost_basis_per_kwh: Optional[float] = None

    @property
    def hours(self) -> float:
        return max(
            0.0, (self.end.timestamp() - self.start.timestamp()) / 3600
        )


@dataclass(frozen=True)
class BatteryDischargePlan:
    index: int
    name: str
    current_soc: float
    minimum_soc: float
    predicted_end_soc: float
    capacity: float
    available_energy: float
    usable_energy: float
    expected_energy: float
    expected_power: float
    duration_hours: float
    discharge_efficiency: float


@dataclass(frozen=True)
class DischargePlan:
    price_limit: float
    available_energy: float
    blocks: list[DischargeBlock] = field(default_factory=list)
    eligible_slots: list[dict[str, Any]] = field(default_factory=list)
    per_battery: list[BatteryDischargePlan] = field(default_factory=list)
    resolution_minutes: int = 60
    cost_basis_per_kwh: Optional[float] = None
    cost_basis_source: str = "economische laadreferentie"
    reserve_soc: float = 0.0
    minimum_price_difference: float = 0.0
    selection_margin: float = 0.0
    daily_peak_prices: dict[str, float] = field(default_factory=dict)
    daily_peak_limits: dict[str, float] = field(default_factory=dict)
    planned_total_power: float = 0.0
    planned_export_power: float = 0.0


@dataclass
class EMSContext:
    now: datetime
    battery: BatteryState
    target_soc: float
    minimum_soc: float
    price_margin: float
    discharge_margin: float
    charge_efficiency: float
    battery_energy_needed: float
    needed_energy: float
    hours_needed: float
    future_prices: list[dict[str, Any]] = field(default_factory=list)
    charge_plan: Optional[ChargePlan] = None
    battery_charge_plans: list[BatteryChargePlan] = field(
        default_factory=list
    )
    discharge_plan: Optional[DischargePlan] = None
    export_plan: Optional[DischargePlan] = None
    charge_distribution: list[float] = field(default_factory=list)
    scheduler_state: SchedulerState = SchedulerState.IDLE
    scheduler_reason: str = "Nog niet bepaald"
