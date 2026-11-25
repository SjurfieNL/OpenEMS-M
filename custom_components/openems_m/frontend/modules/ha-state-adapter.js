export function entityObject(hass, entityId) {
  return hass?.states?.[entityId] || null;
}

export function entityState(hass, entityId, fallback = "onbekend") {
  const value = entityObject(hass, entityId)?.state;
  return (
    value == null || value === "unknown" || value === "unavailable"
      ? fallback : value
  );
}

export function numericEntityState(hass, entityId) {
  const value = Number(entityState(hass, entityId, Number.NaN));
  return Number.isFinite(value) ? value : Number.NaN;
}
