const PLANNED = {
  export: {
    mode: "export",
    stage: "planned",
    className: "bar-export-plan",
    label: "Terugleveren · gepland",
  },
  charge: {
    mode: "charge",
    stage: "planned",
    className: "bar-charge",
    label: "Laden · gepland",
  },
  self: {
    mode: "self",
    stage: "planned",
    className: "bar-discharge",
    label: "Zelfconsumptie · gepland",
  },
};

const CANDIDATE = {
  export: {
    mode: "standby",
    stage: "candidate",
    candidateType: "export",
    className: "bar-export-window",
    label: "Terugleveren toegestaan",
  },
  charge: {
    mode: "standby",
    stage: "candidate",
    candidateType: "charge",
    className: "bar-charge-window",
    label: "Laden mogelijk",
  },
  self: {
    mode: "standby",
    stage: "candidate",
    candidateType: "self",
    className: "bar-discharge-window",
    label: "Zelfconsumptie mogelijk",
  },
};

const ACTUAL = {
  export: {
    mode: "export",
    stage: "actual",
    className: "bar-export-plan",
    label: "Terugleveren · uitgevoerd",
  },
  charge: {
    mode: "charge",
    stage: "actual",
    className: "bar-charge",
    label: "Laden · uitgevoerd",
  },
  self: {
    mode: "self",
    stage: "actual",
    className: "bar-discharge",
    label: "Zelfconsumptie · uitgevoerd",
  },
  standby: {
    mode: "standby",
    stage: "actual",
    className: "bar-standby",
    label: "Stand-by · gemeten",
  },
};

const STANDBY_PLANNED = {
  mode: "standby",
  stage: "planned",
  className: "bar-standby",
  label: "Stand-by",
};

const copy = (value) => ({ ...value });

function manualClassification(mode, active) {
  const base = ACTUAL[mode] || ACTUAL.standby;
  return {
    ...base,
    label: `${base.mode === "charge" ? "Laden"
      : base.mode === "self" ? "Zelfconsumptie"
        : base.mode === "export" ? "Terugleveren" : "Stand-by"} · `
      + (active ? "handmatig actief" : "handmatig uitgevoerd"),
  };
}

function planningClassification(signals) {
  // De terugleverplanner heeft grafiekprioriteit. Een werkelijk gepubliceerd
  // blok is de bron van waarheid; de frontend rekent de financiële selectie
  // niet opnieuw uit.
  if (signals.exportPlanned) return copy(PLANNED.export);
  if (signals.exportCandidate) return copy(CANDIDATE.export);
  if (signals.chargePlanned) return copy(PLANNED.charge);
  if (signals.selfPlanned) return copy(PLANNED.self);
  if (signals.chargeCandidate) return copy(CANDIDATE.charge);
  if (signals.selfCandidate) return copy(CANDIDATE.self);
  return copy(STANDBY_PLANNED);
}

/**
 * Classificeer precies één prijsinterval.
 *
 * Alle signalen worden buiten deze functie uit Home Assistant gelezen. De
 * functie kent geen sensoren, prijzen of browsercache en is daardoor volledig
 * deterministisch te testen.
 */
export function classifyPricePlanningSlot(signals) {
  const phase = signals.phase || "future";

  if (phase === "past") {
    if (signals.exportExecuted) return copy(ACTUAL.export);
    if (signals.manualMode) {
      return manualClassification(signals.manualMode, false);
    }
    if (signals.measuredMode === "charge") return copy(ACTUAL.charge);
    if (signals.measuredMode === "self") return copy(ACTUAL.self);
    if (signals.measuredMode == null) {
      if (signals.statusMode === "charge") return copy(ACTUAL.charge);
      if (signals.statusMode === "self") return copy(ACTUAL.self);
    }
    if (signals.exportCandidate) return copy(CANDIDATE.export);
    if (signals.chargeCandidate) return copy(CANDIDATE.charge);
    if (signals.selfCandidate) return copy(CANDIDATE.self);
    return copy(ACTUAL.standby);
  }

  if (phase === "current") {
    if (signals.exportExecuted) {
      return { ...ACTUAL.export, label: "Terugleveren · nu actief" };
    }
    if (signals.manualControl) {
      return manualClassification(
        signals.manualMode || signals.liveMode || "standby", true
      );
    }
    if (signals.liveMode === "charge") {
      return { ...ACTUAL.charge, label: "Laden · nu actief" };
    }
    if (signals.liveMode === "self") {
      return { ...ACTUAL.self, label: "Zelfconsumptie · nu actief" };
    }
  }

  return planningClassification(signals);
}
