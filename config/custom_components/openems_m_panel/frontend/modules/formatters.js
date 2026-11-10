export function formatNumber(value, decimals = 0) {
  return Number.isFinite(value)
    ? value.toLocaleString("nl-NL", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
    : "—";
}

export function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}
