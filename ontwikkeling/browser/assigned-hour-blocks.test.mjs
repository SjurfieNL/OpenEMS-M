import assert from "node:assert/strict";
import { groupAssignedHourSlots } from "../../config/custom_components/openems_m_panel/frontend/modules/assigned-hour-blocks.mjs";

const hour = 60 * 60 * 1000;
const start = new Date("2026-08-03T20:00:00+02:00");
const slot = (offset, mode, power, current = false) => ({
  start: new Date(start.getTime() + offset * hour),
  end: new Date(start.getTime() + (offset + 1) * hour),
  mode,
  power,
  current,
});

const groups = groupAssignedHourSlots([
  slot(0, "self", 900, true),
  slot(1, "self", 900),
  slot(2, "export", 3000),
  slot(3, "export", 3000),
  slot(4, "export", 2500),
]);

assert.equal(groups.length, 3);
assert.equal(groups[0].mode, "self");
assert.equal(groups[0].end.getTime(), start.getTime() + 2 * hour);
assert.equal(groups[0].current, true);
assert.equal(groups[0].slots.length, 2);
assert.equal(groups[0].slots[1].start.getTime(), start.getTime() + hour);
assert.equal(groups[1].mode, "export");
assert.equal(groups[1].power, 3000);
assert.equal(groups[1].slots.length, 2);
assert.equal(groups[2].power, 2500);

// Voorspelde uren worden nooit met het echte plan samengevoegd.
const mixed = groupAssignedHourSlots([
  slot(0, "standby", 0),
  slot(1, "standby", 0),
  { ...slot(2, "standby", 0), forecast: true },
  { ...slot(3, "charge", 0), forecast: true },
  { ...slot(4, "charge", 0), forecast: true },
]);
assert.deepEqual(
  mixed.map((group) => [group.mode, group.forecast, group.slots.length]),
  [["standby", false, 2], ["standby", true, 1], ["charge", true, 2]],
);

console.log("assigned hour block grouping passed");
