# GEGENEREERD door tools/sync_ems.py uit de AppDaemon-code. Niet handmatig
# wijzigen: pas de AppDaemon-bron aan en draai het script opnieuw.
"""Verdeelt totaal laadvermogen over de accu's."""


def distribute_charge_power(
    total_power,
    soc_values,
    target_soc,
    max_charge_values,
    minimum_power=0.0,
):
    available_room = [
        max(0.0, target_soc - soc)
        for soc in soc_values
    ]
    total_room = sum(available_room)

    if total_power <= 0 or total_room <= 0:
        return [0.0 for _ in soc_values]

    distribution = []
    for index, room in enumerate(available_room):
        requested = total_power * room / total_room
        if room > 0 and requested > 0:
            requested = max(requested, float(minimum_power))
        distribution.append(
            min(requested, max_charge_values[index])
        )

    remaining_power = total_power - sum(distribution)

    while remaining_power > 1:
        changed = False
        for index in range(len(distribution)):
            spare_power = max_charge_values[index] - distribution[index]
            if spare_power <= 0:
                continue
            extra_power = min(spare_power, remaining_power)
            distribution[index] += extra_power
            remaining_power -= extra_power
            changed = True
            if remaining_power <= 1:
                break
        if not changed:
            break

    return distribution


def distribute_discharge_power(
    total_power,
    soc_values,
    minimum_soc,
    max_discharge_values,
    minimum_power=0.0,
):
    """Verdeelt ontladen naar rato van energie boven de minimum-SOC."""
    available_energy = [max(0.0, soc - minimum_soc) for soc in soc_values]
    total_available = sum(available_energy)
    if total_power <= 0 or total_available <= 0:
        return [0.0 for _ in soc_values]

    distribution = []
    for index, available in enumerate(available_energy):
        requested = total_power * available / total_available
        if available > 0 and requested > 0:
            requested = max(requested, float(minimum_power))
        distribution.append(min(requested, max_discharge_values[index]))

    remaining = total_power - sum(distribution)
    while remaining > 1:
        changed = False
        for index in range(len(distribution)):
            spare = max_discharge_values[index] - distribution[index]
            if spare <= 0 or available_energy[index] <= 0:
                continue
            extra = min(spare, remaining)
            distribution[index] += extra
            remaining -= extra
            changed = True
            if remaining <= 1:
                break
        if not changed:
            break
    return distribution
