export function groupAssignedHourSlots(slots = []) {
  const groups = [];
  for (const raw of slots) {
    if (!raw?.start || !raw?.end || !raw?.mode) continue;
    const slot = {
      ...raw,
      start: new Date(raw.start),
      end: new Date(raw.end),
      power: Number(raw.power) || 0,
      forecast: Boolean(raw.forecast),
    };
    if (
      Number.isNaN(slot.start.getTime())
      || Number.isNaN(slot.end.getTime())
      || slot.end <= slot.start
    ) continue;
    const previous = groups.at(-1);
    const contiguous = previous
      && previous.end.getTime() === slot.start.getTime();
    const samePlan = previous
      && previous.mode === slot.mode
      && previous.forecast === slot.forecast
      && Math.abs(previous.power - slot.power) < 0.5;
    if (contiguous && samePlan) {
      previous.end = slot.end;
      previous.current = previous.current || Boolean(slot.current);
      previous.slots.push(slot);
      continue;
    }
    groups.push({
      ...slot,
      current: Boolean(slot.current),
      slots: [slot],
    });
  }
  return groups;
}
