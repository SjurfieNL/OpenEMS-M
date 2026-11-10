"""Automatische exportreserve: houd genoeg over voor het eigen verbruik."""

from datetime import timedelta


def home_energy_kwh(forecast, start, end, fallback_power_w):
    """Verwacht woningverbruik (kWh) tussen start en end uit kwartierwaarden."""
    if end <= start:
        return 0.0
    fallback = max(0.0, float(fallback_power_w or 0.0))
    quarters = max(1, int(round((end - start).total_seconds() / 900)))
    values = []
    for item in forecast or []:
        power = item.get("vermogen_w")
        values.append(fallback if power is None else max(0.0, float(power)))
    values = values[:quarters]
    values += [fallback] * (quarters - len(values))
    return sum(values) * 0.25 / 1000.0


def auto_reserve_soc(
    energy_kwh, total_capacity_kwh, minimum_soc, reserve_soc, buffer
):
    """Reserve = vaste reserve + verbruik tot het volgende laadblok + buffer."""
    if total_capacity_kwh <= 0:
        return float(minimum_soc)
    needed = float(energy_kwh) * (1.0 + max(0.0, float(buffer)))
    value = float(reserve_soc) + needed / float(total_capacity_kwh) * 100.0
    return min(100.0, max(float(minimum_soc), value))


def horizon_end(now, next_charge_start, default_hours=24):
    if next_charge_start is not None and next_charge_start > now:
        return next_charge_start
    return now + timedelta(hours=default_hours)
