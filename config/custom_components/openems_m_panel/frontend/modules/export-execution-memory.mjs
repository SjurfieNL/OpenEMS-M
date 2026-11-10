const RETENTION_MS = 3 * 24 * 60 * 60 * 1000;

const validInterval = (value) => {
  if (!Array.isArray(value) || value.length < 2) return null;
  const start = new Date(value[0]);
  const end = new Date(value[1]);
  return !Number.isNaN(start.getTime())
    && !Number.isNaN(end.getTime())
    && end > start ? [start, end] : null;
};

export function rememberExecutedExportIntervals(
  existing = [], active = [], now = new Date()
) {
  const cutoff = now.getTime() - RETENTION_MS;
  const intervals = [...existing, ...active]
    .map(validInterval)
    .filter((item) => item && item[1].getTime() >= cutoff)
    .sort((left, right) => left[0] - right[0]);
  const merged = [];
  for (const interval of intervals) {
    const previous = merged.at(-1);
    if (previous && interval[0] <= previous[1]) {
      if (interval[1] > previous[1]) previous[1] = interval[1];
    } else {
      merged.push([...interval]);
    }
  }
  return merged;
}

export function executedExportOverlaps(intervals, start, durationMs) {
  const end = new Date(start.getTime() + durationMs);
  return intervals.some((value) => {
    const interval = validInterval(value);
    return interval && interval[0] < end && interval[1] > start;
  });
}

export function statusWasActiveDuring(
  transitions, activeStates, start, durationMs, now = new Date()
) {
  const end = start.getTime() + durationMs;
  const ordered = (transitions || [])
    .map((item) => ({
      state: String(item?.state || "").trim().toLowerCase(),
      time: Date.parse(item?.last_changed || item?.last_updated || 0),
    }))
    .filter((item) => Number.isFinite(item.time))
    .sort((left, right) => left.time - right.time);
  return ordered.some((item, index) => (
    activeStates.includes(item.state)
    && item.time < end
    && (ordered[index + 1]?.time ?? now.getTime()) > start.getTime()
  ));
}
