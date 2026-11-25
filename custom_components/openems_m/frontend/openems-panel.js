// GEGENEREERD door tools/sync_ems.py uit openems_m_panel. Niet handmatig wijzigen.
/*
 * OpenEMS-M Panel
 * Tablet-first Home Assistant sidebar panel.
 * Visual design derived from Marstek Venus Energy Manager (GPL-3.0).
 */

import { escapeHtml, formatNumber } from "./modules/formatters.js?v=20260801b";
import {
  entityObject, entityState, numericEntityState,
} from "./modules/ha-state-adapter.js?v=20260801b";
import {
  classifyPricePlanningSlot,
} from "./modules/price-chart-planning.mjs?v=20260802a";
import {
  groupAssignedHourSlots,
} from "./modules/assigned-hour-blocks.mjs?v=20261108a";
import * as exportExecutionMemory from "./modules/export-execution-memory.mjs?v=20260803b";

// OpenEMS-M (integratie): de instellingen heten switch./number./select./text./
// button. in plaats van input_*. Service-aanroepen naar de oude helperdomeinen
// gaan naar de nieuwe. Gegenereerd door tools/sync_ems.py.
const __OPENEMS_DOMAIN_MAP = {
  input_boolean: "switch", input_number: "number", input_select: "select",
  input_text: "text", input_button: "button",
};
function __openemsCallService(hass, domain, service, data, ...rest) {
  const mapped = __OPENEMS_DOMAIN_MAP[domain];
  const entity = data && typeof data.entity_id === "string" ? data.entity_id : "";
  if (mapped && !entity.startsWith(`${domain}.`)) domain = mapped;
  return hass.callService(domain, service, data, ...rest);
}

const {
  executedExportOverlaps,
  rememberExecutedExportIntervals,
} = exportExecutionMemory;
const statusWasActiveDuring = exportExecutionMemory.statusWasActiveDuring
  || ((transitions, activeStates, start, durationMs, now = new Date()) => {
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
  });

const DASHBOARD_SESSION_CACHE = {
  historyAt: 0,
  recentPowerHistory: null,
  powerHistory: null,
  statusHistory: null,
  socHistory: null,
  exportPlanHistory: null,
  weekHistoryAvailable: false,
  historyRevision: 0,
};

// Entiteitsnamen van de marstek_modbus-integratie: Nederlands is de basis,
// voor installaties met Engelse entiteiten wordt het achtervoegsel vertaald.
const BATTERY_SUFFIX_EN = {
  "ac_frequentie": "ac_frequency",
  "ac_off_grid_spanning": "ac_offgrid_voltage",
  "ac_off_grid_stroom": "ac_offgrid_current",
  "ac_off_grid_vermogen": "ac_offgrid_power",
  "ac_spanning": "ac_voltage",
  "ac_stroom": "ac_current",
  "ac_vermogen": "ac_power",
  "apparaat_ip_adres": "device_ip_address",
  "apparaat_resetten": "reset_device",
  "back_up_functie": "backup_function",
  "batterij_soc": "battery_soc",
  "batterij_totale_energie": "battery_total_energy",
  "batterijspanning": "battery_voltage",
  "batterijstroom": "battery_current",
  "batterijvermogen": "battery_power",
  "bms_versie": "bms_version",
  "communicatiemodule_firmware": "communication_module_firmware",
  "ems_versie": "ems_version",
  "firmwareversie": "firmware_version",
  "forceer_laadvermogen": "set_charge_power",
  "forceer_ontlaadvermogen": "set_discharge_power",
  "gebruikersmodus": "user_work_mode",
  "geforceerde_modus": "force_mode",
  "interne_temperatuur": "internal_temperature",
  "max_celspanning": "max_cell_voltage",
  "max_laadvermogen": "max_charge_power",
  "max_ontlaadvermogen": "max_discharge_power",
  "min_celspanning": "min_cell_voltage",
  "modbus_adres": "modbus_address",
  "modbus_verbinding": "modbus_connection",
  "omvormer_status": "inverter_state",
  "rs485_bedrijfsmodus": "rs485_control_mode",
  "vms_versie": "vms_version",
  "volledige_cycli_bms": "cycle_count",
  "volledige_cycli_calc": "cycle_count_calc"
};
const batterySuffix = (nlSuffix, lang) => (
  lang === "en" ? (BATTERY_SUFFIX_EN[nlSuffix] || nlSuffix) : nlSuffix
);
const cellSuffix = (cell, lang) => (
  lang === "en" ? `battery_pack_1_cell_${cell}_voltage` : `batterij_pack_1_cel_${cell}_spanning`
);
// Standaard: de oorspronkelijke installatie met vier accu's (berging_accu1-4).
const DEFAULT_BATTERY_SITE = [1, 2, 3, 4].map((n) => ({ n, name: `Accu ${n}`, prefix: `berging_accu${n}` }));
const batteryEntities = ({ n, name, prefix: p }, lang = "nl") => ({
    name,
    n,
    disabled: `switch.openems_accu${n}_uitgeschakeld`,
    soc: `sensor.${p}_${batterySuffix("batterij_soc", lang)}`,
    capacity: `sensor.${p}_${batterySuffix("batterij_totale_energie", lang)}`,
    actual: `sensor.${p}_${batterySuffix("ac_vermogen", lang)}`,
    cycles: `sensor.${p}_${batterySuffix("volledige_cycli_bms", lang)}`,
    cyclesCalc: `sensor.${p}_${batterySuffix("volledige_cycli_calc", lang)}`,
    temperature: `sensor.${p}_${batterySuffix("interne_temperatuur", lang)}`,
    inverter: `sensor.${p}_${batterySuffix("omvormer_status", lang)}`,
    voltage: `sensor.${p}_${batterySuffix("batterijspanning", lang)}`,
    cellMax: `sensor.${p}_${batterySuffix("max_celspanning", lang)}`,
    cellMin: `sensor.${p}_${batterySuffix("min_celspanning", lang)}`,
    cells: Array.from(
      { length: 16 },
      (_, index) => `sensor.${p}_${cellSuffix(index + 1, lang)}`,
    ),
    cellDelta: `sensor.accu${n}_cell_delta`,
    deltaAvg: `sensor.accu${n}_delta_avg_4w`,
    balanceStatus: `sensor.accu${n}_balance_status`,
    balanceLast: `sensor.accu${n}_last_balance_read`,
    learnedEfficiency: `sensor.openems_accu_${n}_zelfgeleerd_rendement`,
    balancing: `binary_sensor.accu${n}_balancing_mode`,
    allowCharge: `switch.accu${n}_battery_allow_charge`,
    allowDischarge: `switch.accu${n}_battery_allow_discharge`,
    chargeCutoff: `number.accu${n}_charging_cutoff_capacity`,
    dischargeCutoff: `number.accu${n}_discharging_cutoff_capacity`,
    backup: `switch.${p}_${batterySuffix("back_up_functie", lang)}`,
    ip: `sensor.${p}_${batterySuffix("apparaat_ip_adres", lang)}`,
    modbusConnected: `binary_sensor.${p}_${batterySuffix("modbus_verbinding", lang)}`,
    modbusAddress: `sensor.${p}_${batterySuffix("modbus_adres", lang)}`,
    firmware: `sensor.${p}_${batterySuffix("firmwareversie", lang)}`,
    bmsVersion: `sensor.${p}_${batterySuffix("bms_versie", lang)}`,
    emsVersion: `sensor.${p}_${batterySuffix("ems_versie", lang)}`,
    vmsVersion: `sensor.${p}_${batterySuffix("vms_versie", lang)}`,
    communicationFirmware: `sensor.${p}_${batterySuffix("communicatiemodule_firmware", lang)}`,
    acVoltage: `sensor.${p}_${batterySuffix("ac_spanning", lang)}`,
    acCurrent: `sensor.${p}_${batterySuffix("ac_stroom", lang)}`,
    acPower: `sensor.${p}_${batterySuffix("ac_vermogen", lang)}`,
    acFrequency: `sensor.${p}_${batterySuffix("ac_frequentie", lang)}`,
    offGridVoltage: `sensor.${p}_${batterySuffix("ac_off_grid_spanning", lang)}`,
    offGridCurrent: `sensor.${p}_${batterySuffix("ac_off_grid_stroom", lang)}`,
    offGridPower: `sensor.${p}_${batterySuffix("ac_off_grid_vermogen", lang)}`,
    batteryCurrent: `sensor.${p}_${batterySuffix("batterijstroom", lang)}`,
    batteryPower: `sensor.${p}_${batterySuffix("batterijvermogen", lang)}`,
    deviceReset: `button.${p}_${batterySuffix("apparaat_resetten", lang)}`,
    charge: `number.${p}_${batterySuffix("forceer_laadvermogen", lang)}`,
    discharge: `number.${p}_${batterySuffix("forceer_ontlaadvermogen", lang)}`,
    maxCharge: `number.${p}_${batterySuffix("max_laadvermogen", lang)}`,
    maxDischarge: `number.${p}_${batterySuffix("max_ontlaadvermogen", lang)}`,
    rs485: `switch.${p}_${batterySuffix("rs485_bedrijfsmodus", lang)}`,
    work: `select.${p}_${batterySuffix("gebruikersmodus", lang)}`,
    force: `select.${p}_${batterySuffix("geforceerde_modus", lang)}`,
    helpers: {},
  });
const buildBatteries = (site = DEFAULT_BATTERY_SITE, lang = "nl") => site.map((item) => batteryEntities(item, lang));

const E = {
  auto: "switch.openems_actief",
  gridChargeAllowed: "switch.openems_netladen_toegestaan",
  gridExportAllowed: "switch.openems_terugleveren_toegestaan",
  manualMode: "select.openems_handmatige_modus",
  manualCharge: "number.openems_handmatig_laadvermogen_totaal",
  manualDischarge: "number.openems_handmatig_ontlaadvermogen_totaal",
  manualDistribution: "select.openems_handmatig_verdeling",
  manualExcluded: [1, 2, 3, 4].map((n) => `switch.openems_handmatig_accu${n}_uit`),
  manualStatus: "sensor.openems_handmatige_status",
  status: "sensor.openems_status",
  gridStatus: "sensor.openems_netstatus",
  gridOutageDuration: "sensor.openems_netuitval_duur",
  switchStatus: "sensor.openems_hoofdschakelaar",
  soc: "sensor.openems_gemiddelde_soc",
  stored: "sensor.openems_opgeslagen_energie",
  minimumSoc: "number.marstek_minimum_soc",
  price: "sensor.openems_huidige_prijs",
  plannedPower: "sensor.openems_gepland_laadvermogen",
  nextCharge: "sensor.openems_volgend_laadblok",
  nextDischarge: "sensor.openems_volgend_ontlaadblok",
  nextExport: "sensor.openems_volgend_exportblok",
  planning: "sensor.openems_planningsoverzicht",
  results: "sensor.openems_financiele_resultaten",
  exportStatus: "sensor.openems_grid_export_status",
  runtimeEvents: "sensor.openems_runtime_gebeurtenissen",
  runtimeRecovery: "sensor.openems_runtime_herstel",
  runtimeEventsClear: "button.openems_runtime_gebeurtenissen_wissen",
  exportMode: "select.openems_grid_export_modus",
  exportReserveMode: "select.openems_export_reserve_modus",
  fixedExportReserve: "number.openems_export_vaste_reserve_kwh",
  beforeChargeReserveMode: "select.openems_export_reserve_voor_laden_modus",
  beforeChargeReserve: "number.openems_export_reserve_voor_laden_kwh",
  dynamicReserve: "number.openems_export_dynamische_reserve_kwh",
  freshness: "sensor.openems_sensordata_status",
  chargeEfficiencyMode: "select.openems_laad_rendement_modus",
  dischargeEfficiencyMode: "select.openems_ontlaad_rendement_modus",
  exportEfficiencyMode: "select.openems_export_rendement_modus",
  homeForecast: "sensor.openems_woningverbruik_voorspelling",
  frank: "sensor.frank_energie_stroomprijzen_huidige_elektriciteitsprijs_all_in",
  frankMarket: "sensor.frank_energie_stroomprijzen_huidige_elektriciteit_inkoopprijs",
  frankAllInHelper: "text.openems_frank_all_in_prijssensor",
  frankMarketHelper: "text.openems_frank_marktprijssensor",
  homeSensorHelper: "text.openems_woning_vermogenssensor",
  gridSensorHelper: "text.openems_grid_vermogenssensor",
  gridSensorSource: "select.openems_grid_sensor_bron",
  gridDisplaySensorHelper: "text.openems_grid_verbruiksensor",
  gridReferenceHelper: "text.openems_grid_referentiesensor",
  solarSensorHelper: "text.openems_zon_vermogenssensor",
  selfConsumptionMode: "select.openems_zelfconsumptieregeling",
  selfConsumptionFallback: "switch.openems_automatisch_terugvallen_marstek",
  batteryDistribution: "select.openems_accuverdeling",
  batterySelectorStatus: "sensor.openems_efficiente_accuselector",
  batteryHealth: "sensor.openems_accugezondheid",
  loadBalancerStatus: "sensor.openems_load_balancer",
  autoExportReserve: "switch.openems_export_reserve_auto",
  autoExportReserveStatus: "sensor.openems_export_reserve_auto",
  smartCharge: "switch.openems_slim_laden",
  smartChargeStatus: "sensor.openems_slim_laden",
  priceForecast: "switch.openems_prijsvoorspelling",
  priceForecastEntityHelper: "text.openems_prijsvoorspelling_sensor",
  priceForecastStatus: "sensor.openems_prijsvoorspelling",
  priceForecastPlan: "sensor.openems_voorspeld_plan",
  loadBalancerOff: {
    laden: "switch.openems_loadbalancer_laden_uit",
    terugleveren: "switch.openems_loadbalancer_terugleveren_uit",
    handmatig: "switch.openems_loadbalancer_handmatig_uit",
  },
  lbMaxImport: "number.openems_lb_max_import",
  lbMaxExport: "number.openems_lb_max_export",
  lbP1Off: "switch.openems_lb_p1_uit",
  lbCtOff: "switch.openems_lb_ct_uit",
  lbPreferred: "select.openems_lb_voorkeur",
  lbBoth: "switch.openems_lb_beide_tegelijk",
  gridPowerSign: "select.openems_grid_vermogen_teken",
  gridOffsetMode: "select.openems_grid_offset_modus",
  gridManualOffset: "number.openems_grid_handmatige_offset",
  homePowerOffset: "number.openems_ct_verbruik_offset",
  gridAutomaticOffset: "number.openems_grid_automatische_offset",
  gridOffsetCalibrate: "button.openems_grid_offset_kalibreren",
  homeOffsetCalibrate: "button.openems_home_offset_kalibreren",
  homeOffsetStatus: "sensor.openems_home_offset_status",
  gridOffsetStatus: "sensor.openems_netoffset_status",
  correctedGridPower: "sensor.openems_gecorrigeerd_netvermogen",
  selfConsumptionStatus: "sensor.openems_zelfconsumptie_status",
  settingsManager: "sensor.openems_instellingenbeheer",
  saveDefaults: "button.openems_huidige_instellingen_opslaan",
  restoreDefaults: "button.openems_eigen_standaard_herstellen",
  restoreFactory: "button.openems_fabrieksinstellingen_herstellen",
  dischargeResolution: "select.openems_ontlaad_tijdresolutie",
  settings: [
    ["number.marstek_doel_soc", "Doel-SOC", "%", "laden"],
    ["number.openems_slim_laden_reserve_soc", "Slim laden: reserve", "%", "laden"],
    ["number.openems_slim_laden_buffer", "Slim laden: buffer op verbruik", "%", "laden"],
    ["number.openems_slim_laden_min_verschil", "Slim laden: minimaal goedkoper", "ct", "laden"],
    ["number.openems_prijsvoorspelling_min_verschil", "Voorspelling: minimaal goedkoper", "ct", "voorspelling"],
    ["number.openems_prijsvoorspelling_extra_marge", "Voorspelling: extra marge bij schatting", "ct", "voorspelling"],
    ["number.openems_prijsvoorspelling_ondergrens_soc", "Voorspelling: ondergrens accu", "%", "voorspelling"],
    ["number.openems_prijsvoorspelling_dagen", "Voorspelling: dagen vooruit meetellen", "dagen", "voorspelling"],
    ["number.openems_handmatig_laadrendement", "Laadrendement", "%", "laden", "manual-charge-efficiency"],
    ["number.marstek_minimum_soc", "Minimale SOC", "%", "ontladen"],
    ["number.openems_handmatig_ontlaadrendement", "Ontlaadrendement", "%", "ontladen", "manual-discharge-efficiency"],
    ["number.openems_minimaal_prijsverschil_grid_export", "Terugleveren minimaal laadprijsverschil", "EUR/kWh", "leverancier"],
    ["number.openems_export_reserve_voor_laden_kwh", "Reserve voor laden", "kWh", "export", "before-charge-reserve"],
    ["number.openems_export_vaste_reserve_kwh", "Reserve na laden", "kWh", "export", "after-charge-reserve"],
    ["number.openems_export_dynamische_reserve_kwh", "Dynamische reserve", "kWh", "export", "dynamic-export-reserve"],
    ["number.marstek_prijsmarge", "Laadprijsmarge", "EUR/kWh", "leverancier"],
    ["number.marstek_minimaal_prijsverschil_ontladen", "Zelfconsumptiemarge", "EUR/kWh", "leverancier"],
    ["number.openems_minimale_exportwinst_per_kwh", "Marge onder hoogste dagprijs", "EUR/kWh", "leverancier"],
    ["number.openems_laden_vanaf", "Laden toegestaan vanaf", "uur", "advanced"],
    ["number.openems_laden_tot", "Laden toegestaan tot", "uur", "advanced"],
    ["number.openems_minimaal_laadvermogen_per_accu", "Minimaal laadvermogen per accu", "W", "advanced"],
    ["number.openems_minimale_ontlaadblokduur", "Minimale ontlaadblokduur", "min", "advanced"],
    ["number.openems_maximaal_exportvermogen", "Maximaal terugleververmogen", "W", "advanced"],
    ["number.openems_handmatig_export_totaalrendement", "Handmatig totaalrendement export", "%", "advanced", "manual-export-efficiency"],
  ],
  batteryEntityFields: [
    ["soc", "SOC"], ["capacity", "Capaciteit"],
    ["actual_power", "Werkelijk AC-vermogen"],
    ["max_charge_power", "Maximaal laadvermogen"],
    ["max_discharge_power", "Maximaal ontlaadvermogen"],
    ["charge_power", "Ingesteld laadvermogen"],
    ["discharge_power", "Ingesteld ontlaadvermogen"],
    ["rs485_control", "RS485-besturing"], ["work_mode", "Werkmodus"],
    ["force_mode", "Force Mode"], ["max_cell_voltage", "Hoogste celspanning"],
    ["min_cell_voltage", "Laagste celspanning"], ["temperature", "Temperatuur"],
    ["cycles", "Batterijcycli"],
    ["total_charging_energy", "Totaal geladen energie"],
    ["total_discharging_energy", "Totaal ontladen energie"],
    ["alarm_status", "Alarmstatus"], ["fault_status", "Foutstatus"],
  ],
  batteries: buildBatteries(),
};
E.batteries.forEach((battery) => E.batteryEntityFields.forEach(([field]) => {
  battery.helpers[field] = `text.openems_accu${battery.n}_${field}`;
}));

class OpenEmsMPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._panel = null;
    this._built = false;
    this._tab = "overview";
    this._historyAt = DASHBOARD_SESSION_CACHE.historyAt;
    this._historyAttemptAt = 0;
    this._recentPowerHistory = DASHBOARD_SESSION_CACHE.recentPowerHistory;
    this._powerHistory = DASHBOARD_SESSION_CACHE.powerHistory;
    this._statusHistory = DASHBOARD_SESSION_CACHE.statusHistory;
    this._socHistory = DASHBOARD_SESSION_CACHE.socHistory;
    this._exportPlanHistory = DASHBOARD_SESSION_CACHE.exportPlanHistory;
    this._weekHistoryAvailable = DASHBOARD_SESSION_CACHE.weekHistoryAvailable;
    this._priceClassificationCache = this._loadPriceClassificationCache();
    this._executedExportIntervals = this._loadExecutedExportIntervals();
    this._selectedPrice = null;
    this._batteryIps = {};
    this._pendingEntityStates = new Map();
    this._patchFrame = null;
    this._graphRenderFingerprint = null;
    this._statusRenderFingerprint = null;
    this._settingsRenderFingerprint = null;
    this._expandedAssignedHourBlocks = new Set();
    this._efficiencyResultDirection = "ontladen";
    this._efficiencyResultSelection = null;
    this._historyRevision = DASHBOARD_SESSION_CACHE.historyRevision;
    this._advancedSettings = localStorage.getItem("openems-settings-advanced") === "true";
  }

  set hass(value) {
    this._hass = value;
    const siteChanged = this._applySiteConfig();
    if (!this._built) {
      this._build();
      this._patch();
      return;
    }
    if (siteChanged) {
      // Aantal accu's of entiteiten gewijzigd: paneel opnieuw opbouwen.
      this._build();
      this._patch();
      return;
    }
    this._schedulePatch();
  }

  _applySiteConfig() {
    // Installatie-instellingen uit OpenEMS (openems_site.yaml via AppDaemon).
    const config = this._hass?.states?.["sensor.openems_configuratie"];
    const accus = Array.isArray(config?.attributes?.accus) ? config.attributes.accus : [];
    const lang = String(config?.attributes?.entiteit_taal || "nl").toLowerCase() === "en" ? "en" : "nl";
    const site = accus
      .slice(0, 4)
      .map((item, index) => ({
        n: index + 1,
        name: String(item?.naam || `Accu ${index + 1}`),
        prefix: String(item?.prefix || "").trim(),
      }))
      .filter((item) => item.prefix);
    const chosen = site.length ? site : DEFAULT_BATTERY_SITE;
    const chosenLang = site.length ? lang : "nl";
    const signature = JSON.stringify([chosen, chosenLang]);
    if (signature === this._siteSignature) return false;
    const first = this._siteSignature === undefined;
    this._siteSignature = signature;
    E.batteries.splice(0, E.batteries.length, ...buildBatteries(chosen, chosenLang));
    return !first || site.length > 0;
  }

  set panel(value) {
    this._panel = value;
    if (this._built) this._patch();
  }

  set narrow(_value) {}
  set route(_value) {}

  connectedCallback() {
    if (!this._visibilityHandler) {
      this._visibilityHandler = () => {
        if (document.visibilityState === "hidden" || !this.isConnected) return;
        this._patch();
        this._renderCachedHistory();
        if (this._tab === "graphs" || this._tab === "status") {
          this._loadHistory(false);
        }
      };
    }
    document.addEventListener("visibilitychange", this._visibilityHandler);
    if (this._releaseControlPointer) {
      window.addEventListener("pointerup", this._releaseControlPointer);
      window.addEventListener("pointercancel", this._releaseControlPointer);
    }
    this._startTimers();
    if (this._built && this._hass) {
      this._patch();
      this._renderCachedHistory();
    }
  }

  disconnectedCallback() {
    if (this._patchFrame !== null) {
      this._patchFrame = null;
    }
    this._pendingEntityStates.forEach((pending) => clearTimeout(pending.timer));
    this._pendingEntityStates.clear();
    if (this._historyTimer) clearInterval(this._historyTimer);
    if (this._manualCountdownTimer) clearInterval(this._manualCountdownTimer);
    this._historyTimer = null;
    this._manualCountdownTimer = null;
    if (this._historyPrefetchTimer) clearTimeout(this._historyPrefetchTimer);
    if (this._classificationCacheTimer) clearTimeout(this._classificationCacheTimer);
    if (this._releaseControlPointer) {
      window.removeEventListener("pointerup", this._releaseControlPointer);
      window.removeEventListener("pointercancel", this._releaseControlPointer);
    }
    if (this._visibilityHandler) {
      document.removeEventListener(
        "visibilitychange", this._visibilityHandler
      );
    }
  }

  _build() {
    this._built = true;
    this.shadowRoot.innerHTML = `${this._styles()}
      <div class="app">
        <header class="appbar">
          <div class="brand"><div class="logo"><img src="/openems_m_static/assets/openems-logo.png?v=20260801b" alt="OpenEMS-M"></div><div><b>OpenEMS-M</b><small>Energiebeheer</small></div></div>
          <nav class="tabs">
            ${this._tabButton("overview", "mdi:view-dashboard-outline", "Overzicht")}
            ${this._tabButton("batteries", "mdi:battery-high", "Accu's")}
            ${this._tabButton("graphs", "mdi:chart-line", "Grafieken")}
            ${this._tabButton("results", "mdi:finance", "Resultaten")}
            ${this._tabButton("settings", "mdi:tune-variant", "Instellingen")}
            ${this._tabButton("status", "mdi:shield-check-outline", "Status")}
            <a class="tab home-link" href="/dashboard-walltab1/home" title="Terug naar Wallpanel"><ha-icon icon="mdi:tablet-dashboard"></ha-icon><span>Wallpanel</span></a>
          </nav>
          <div class="live"><i></i><span>Actueel</span></div>
        </header>
        <main>
          <section class="page overview-page" data-page="overview">
            <article class="card hero">
              ${this._heading("mdi:battery-charging-high", "Systeemstatus", "sys-chip")}
              <div class="hero-body">
                <div class="clickable-ring" data-more="${E.soc}" title="Open SOC-grafiek">${this._ring("system", 190)}</div>
                <div class="hero-info">
                  <div class="big-state" id="system-state">—</div>
                  <div class="muted" id="system-reason">Status wordt geladen</div>
                  <div class="outage-duration" id="outage-duration" hidden><ha-icon icon="mdi:timer-alert-outline"></ha-icon><div><span>Stroomuitval</span><b id="outage-duration-value">0 seconden</b></div></div>
                  <div class="metric-grid">
                    ${this._metric("Actuele prijs", "price-now", "€/kWh")}
                    ${this._metric("Actueel accuvermogen", "actual-power", "")}
                    ${this._metric("Opgeslagen", "stored-energy", "kWh")}
                    ${this._metric("Geschat leeg", "empty-time", "")}
                    ${this._metric("Rustig verbruik", "estimated-home-power", "W")}
                    ${this._metric("Woningverbruik", "home-power", "W")}
                    ${this._metric("Van het net", "grid-power", "W")}
                  </div>
                </div>
              </div>
            </article>
            <article class="card control-card">
              ${this._heading("mdi:toggle-switch-outline", "Bediening", "mode-chip")}
              <button id="mobile-control-toggle" class="mobile-control-toggle"><ha-icon icon="mdi:tune-variant"></ha-icon><span>Bediening tonen</span></button>
              <div class="auto-row">
                <div><b>Bedieningsmodus</b><small>Uit = Handmatig · Aan = Auto</small></div>
                <button id="auto-toggle" class="mode-toggle" aria-label="Auto of handmatig"><span>Handmatig</span><i></i><span>Auto</span></button>
              </div>
              <div id="manual-controls" class="manual-controls">
                <label>Handmatige opdracht
                  <select id="manual-mode"><option>Stop</option><option>Laden</option><option>Ontladen</option><option>Zelfconsumptie</option></select>
                </label>
                <div id="manual-power-block">
                  ${this._range("manual-power", "Vermogen totaal", 0, 8000, 50, "W")}
                </div>
                <div class="manual-accus" id="manual-accus">
                  ${E.batteries.map((b) => `<label class="manual-accu"><input type="checkbox" id="manual-accu-${b.n}"><span>Accu ${b.n}</span></label>`).join("")}
                </div>
                <div id="manual-distribution-block">
                  <label class="manual-distribution">Verdeling
                    <select id="manual-distribution"><option>Gelijk</option><option>Naar SOC</option></select>
                  </label>
                </div>
                <div class="manual-result">
                  <span id="manual-state">—</span>
                  <div id="manual-battery-statuses" class="manual-battery-statuses"></div>
                  <small class="manual-action"><b>Actie</b><span id="manual-message">Nog geen status</span></small>
                </div>
              </div>
              <div id="charge-planning" class="charge-planning" hidden>
                <div class="planning-head"><ha-icon icon="mdi:battery-clock"></ha-icon><b>Laadplanning</b></div>
                <div id="charge-planning-rows" class="planning-rows"></div>
              </div>
            </article>
            <article class="card plan-card charge-plan">
              <div class="card-head"><ha-icon id="current-block-icon" icon="mdi:clock-outline"></ha-icon><h2 id="current-block-title">Huidig blok</h2></div>
              <strong id="current-block">—</strong><small id="current-block-price">Planning wordt geladen</small>
            </article>
            <article class="card plan-card discharge-plan">
              <div class="card-head"><ha-icon id="next-block-icon" icon="mdi:clock-fast"></ha-icon><h2 id="next-block-title">Volgend blok</h2></div>
              <strong id="next-block">—</strong><small id="next-block-price">Planning wordt geladen</small>
            </article>
            <article class="card battery-strip">
              ${E.batteries.map((b) => `<div class="mini-battery" data-more="${b.soc}">${this._ring(`mini-${b.n}`, 92)}<div><b>Accu ${b.n}</b><small id="mini-mode-${b.n}">—</small></div></div>`).join("")}
            </article>
          </section>

          <section class="page" data-page="batteries" hidden>
            <div class="page-title"><div><h1>Marstek Venus E 3.0</h1><p>Individuele status van de drie accu's</p></div></div>
            <div class="battery-grid">
              ${E.batteries.map((b) => this._batteryCard(b)).join("")}
            </div>
          </section>

          <section class="page" data-page="graphs" hidden>
            <div class="page-title"><div><h1>Energie & prijzen</h1><p>Frank Energie vooruitblik en werkelijk vermogen</p></div><button class="refresh" id="refresh-history"><ha-icon icon="mdi:refresh"></ha-icon>Vernieuwen</button></div>
            <div class="graph-grid">
              <article class="card graph-wide">${this._heading("mdi:currency-eur", "Frank Energie · vandaag en morgen")}<div id="price-summary" class="price-summary"></div><div id="price-chart" class="chart"><div class="empty">Prijsgegevens worden geladen</div></div><div id="price-detail" hidden aria-live="polite"></div></article>
              <article class="card graph-half">${this._heading("mdi:home-lightning-bolt-outline", "Energiestromen · vandaag")}<div id="flow-summary-today" class="flow-summary"></div><div class="legend flow-legend"><span class="flow-grid-home">Uit net</span><span class="flow-battery-home">Uit accu</span><span class="flow-battery-charge">Accu laden</span><span class="flow-export">Terugleveren</span></div><div id="power-chart-today" class="chart"><div class="empty">Historie wordt geladen</div></div></article>
              <article class="card graph-half">${this._heading("mdi:home-lightning-bolt-outline", "Energiestromen · gisteren")}<div id="flow-summary-yesterday" class="flow-summary"></div><div class="legend flow-legend"><span class="flow-grid-home">Uit net</span><span class="flow-battery-home">Uit accu</span><span class="flow-battery-charge">Accu laden</span><span class="flow-export">Terugleveren</span></div><div id="power-chart-yesterday" class="chart"><div class="empty">Historie wordt geladen</div></div></article>
              <article class="card graph-wide">${this._heading("mdi:calendar-week", "Energiestromen · afgelopen 7 dagen")}<div id="flow-summary-week" class="flow-summary"></div><div class="legend flow-legend"><span class="flow-grid-home">Uit net</span><span class="flow-battery-home">Uit accu</span><span class="flow-battery-charge">Accu laden</span><span class="flow-export">Terugleveren</span></div><div id="power-chart-week" class="chart"><div class="empty">Historie wordt geladen</div></div></article>
              <article class="card graph-half">${this._heading("mdi:battery-heart-variant", "SOC per accu · vandaag")}<div class="soc-legend">${E.batteries.map((b) => `<span class="soc-${b.n}">${b.name}</span>`).join("")}</div><div id="soc-chart-today" class="chart"><div class="empty">Historie wordt geladen</div></div></article>
              <article class="card graph-half">${this._heading("mdi:battery-heart-variant", "SOC per accu · gisteren")}<div class="soc-legend">${E.batteries.map((b) => `<span class="soc-${b.n}">${b.name}</span>`).join("")}</div><div id="soc-chart-yesterday" class="chart"><div class="empty">Historie wordt geladen</div></div></article>
              <article class="card graph-wide">${this._heading("mdi:chart-bar-stacked", "Marktprijs, terugleverprijs en all-inprijs · 48 uur")}<div id="market-chart" class="chart"><div class="empty">Prijsgegevens worden geladen</div></div></article>
            </div>
          </section>

          <section class="page" data-page="results" hidden>
            <div class="page-title"><div><h1>Resultaten</h1><p>Werkelijk gemeten energie, kosten en opbrengst</p></div></div>
            <div id="financial-results" class="results-grid"><div class="empty">Resultaten worden verzameld</div></div>
          </section>

          <section class="page" data-page="settings" hidden>
            <div class="page-title"><div><h1>Instellingen</h1><p>Duidelijke basisbediening, met technische instellingen apart</p></div></div>
            <div class="settings-grid">
              <div class="settings-mode-switch" id="settings-mode-switch">
                <button data-auto-mode="on">Automatisch</button>
                <button data-auto-mode="off">Handmatig</button>
              </div>
              <details class="settings-section quick-settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:toggle-switch-outline"></ha-icon><div><b>Snelle schakelfuncties</b><small>De belangrijkste aan/uit- en keuzefuncties bij elkaar.</small></div></summary>
                <div class="settings-section-content">
                  <article class="card quick-settings">
                    <div class="quick-row"><div><b>Opladen uit het net</b><small>Uit blokkeert alleen de daadwerkelijke netlading.</small></div><button id="grid-charge-toggle" class="small-toggle"><i></i></button></div>
                    <div class="quick-row"><div><b>Terugleveren aan het net</b><small>Uit staat alleen zelfconsumptie toe.</small></div><button id="grid-export-toggle" class="small-toggle"><i></i></button></div>
                    ${E.batteries.map((b) => `<div class="quick-row"><div><b>${b.name} actief in OpenEMS</b><small>Uitvinken: OpenEMS laat de accu volledig met rust (geen RS485, modus of vermogen).</small></div><button id="accu-active-toggle-${b.n}" class="small-toggle"><i></i></button></div>`).join("\n                    ")}
                    <div class="quick-row"><div><b>Geavanceerde instellingen</b><small>Toon technische planners en alle entiteiten.</small></div><button id="advanced-settings-toggle" class="small-toggle"><i></i></button></div>
                  </article>
                </div>
              </details>
              <details class="settings-section quick-settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:scale-balance"></ha-icon><div><b>Load Balancer</b><small>Houdt laden en woningverbruik onder je ingestelde import- en exportgrens.</small></div></summary>
                <div class="settings-section-content">
                  <article class="card quick-settings">
                    <div class="quick-row"><div><b>Actief bij laden</b><small>Verlaagt het laden zodra het huis te veel stroom nodig heeft.</small></div><button id="lb-toggle-laden" class="small-toggle"><i></i></button></div>
                    <div class="quick-row"><div><b>Actief bij terugleveren</b><small>Bewaakt de ingestelde exportgrens.</small></div><button id="lb-toggle-terugleveren" class="small-toggle"><i></i></button></div>
                    <div class="quick-row"><div><b>Actief in handmatige modus</b><small>Uit = handmatig laden zonder bescherming van de hoofdzekering.</small></div><button id="lb-toggle-handmatig" class="small-toggle"><i></i></button></div>
                    <div class="quick-row"><label>Maximale import (W)<input id="lb-max-import" type="number" min="1000" max="12000" step="50" inputmode="numeric"></label></div>
                    <div class="quick-row"><label>Maximale export (W)<input id="lb-max-export" type="number" min="500" max="8000" step="50" inputmode="numeric"></label></div>
                    <div class="quick-row"><div><b>Netsensoren</b><small>Vink aan welke sensoren de load balancer gebruikt. Minstens één blijft aan.</small></div><div class="lb-checks"><label><input id="lb-p1-check" type="checkbox"> P1</label><label><input id="lb-ct-check" type="checkbox"> CT</label></div></div>
                    <div class="quick-row" id="lb-pref-row"><label>Voorkeurssensor<select id="lb-pref-select"><option>P1</option><option>CT</option></select></label></div>
                    <div class="quick-row" id="lb-both-row"><div><b>Beide tegelijk bewaken</b><small>Reageert op de sensor die het eerst zijn grens raakt. Uit = voorkeur, met automatische terugval op de andere.</small></div><button id="lb-both-toggle" class="small-toggle"><i></i></button></div>
                    <div id="lb-status" class="strategy-status">Status wordt geladen</div>
                  </article>
                </div>
              </details>

              <details class="settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:tune-vertical"></ha-icon><div><b>Meting en offsets</b><small>Netsensoren en correctie van de CT-spoel (geldt voor heel OpenEMS).</small></div></summary>
                <div class="settings-section-content">
                  <article class="card sensor-setting"><label class="range-label-ui"><span>CT Grid Offset<b><em id="grid-offset-manual-value">—</em> W</b></span><input id="grid-offset-manual-input" type="range" min="-150" max="150" step="2"></label><small>Vaste correctie van de CT-spoel ten opzichte van P1. Eén keer goed instellen is genoeg.</small><div class="grid-offset-controls"><button id="grid-offset-calibrate-settings"><ha-icon icon="mdi:target"></ha-icon><span class="cal-label" data-idle="Eenmalig kalibreren">Eenmalig kalibreren</span></button></div><div id="grid-offset-last" class="strategy-status">Nog geen kalibratie bekend</div><small>Kalibreer bij voorkeur als er niet geladen of ontladen wordt en het huis rustig is. De offset wordt daarna vastgezet.</small></article>
                  <article class="card sensor-setting">${this._range("home-power-offset", "CT Verbruik Offset", -150, 150, 2, "W")}<small>Vaste correctie van de woning-CT. Zonder zonnepanelen en met alle accu's in rust is het woningverbruik gelijk aan het net (P1); de kalibratie meet het verschil.</small><div class="grid-offset-controls"><button id="home-offset-calibrate"><ha-icon icon="mdi:target"></ha-icon><span class="cal-label" data-idle="Woning-CT eenmalig kalibreren">Woning-CT eenmalig kalibreren</span></button></div><div id="home-offset-last" class="strategy-status">Nog geen kalibratie bekend</div><small>Zet eerst alle accu's in rust (stand-by) en gebruik geen grote verbruikers. Duurt ongeveer 1 minuut.</small></article>
                  <div class="advanced-only">
                    <article class="card sensor-setting"><label>Entiteit P1<input id="grid-reference-input" list="power-sensors" placeholder="sensor.p1_netto_vermogen"></label></article>
                    <article class="card sensor-setting"><label>Entiteit CT Grid<input id="grid-sensor-input" list="power-sensors" placeholder="sensor..."></label></article>
                    <article class="card sensor-setting"><label>Entiteit CT Verbruik<input id="home-sensor-input" list="power-sensors" placeholder="sensor..."></label></article>
                  </div>
                </div>
              </details>

              <details class="settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:battery-arrow-up"></ha-icon><div><b>Laden</b><small>Doel en rendementsberekening voor laden.</small></div></summary>
                <div class="settings-section-content">
                  <article class="card sensor-setting"><div class="quick-row"><div><b>Slim vooruitkijken</b><small>Laad minder vol als het volgende laadblok duidelijk goedkoper is.</small></div><button id="smart-charge-toggle" class="small-toggle"><i></i></button></div><div id="smart-charge-status" class="strategy-status">Status wordt geladen</div></article>
                  <article class="card sensor-setting"><div class="quick-row"><div><b>Prijsvoorspelling gebruiken</b><small>WattWanneer-voorspelling vult alleen uren aan waarvoor Frank nog geen prijs heeft. Frank-prijzen zijn altijd leidend. Slim laden kan hierdoor uitstellen bij een duidelijk goedkopere dag.</small></div><button id="price-forecast-toggle" class="small-toggle"><i></i></button></div><div id="price-forecast-status" class="strategy-status">Status wordt geladen</div></article>
                  ${this._settingsCards("voorspelling")}
                  <div class="advanced-only"><article class="card sensor-setting"><label>Entiteit prijsvoorspelling (WattWanneer)<input id="price-forecast-entity-input" list="all-entities" placeholder="sensor.wattwanneer_gemiddelde_deze_week"></label></article></div>
                  ${this._settingsCards("laden")}
                  <article class="card sensor-setting"><label>Rendement bij laden<select id="charge-efficiency-mode"><option value="Zelfgeleerd rendement">Automatisch</option><option value="Handmatig rendement">Handmatig</option><option value="Geen rendement">Uit</option></select></label></article>
                  <div id="automatic-efficiency-status" class="strategy-status">Rendementsstatus wordt geladen</div>
                </div>
              </details>

              <details class="settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:battery-arrow-down"></ha-icon><div><b>Ontladen</b><small>Geldig voor zelfconsumptie én terugleveren.</small></div></summary>
                <div class="settings-section-content">
                  ${this._settingsCards("ontladen")}
                  <article class="card sensor-setting"><label>Rendement bij ontladen<select id="discharge-efficiency-mode"><option value="Zelfgeleerd rendement">Automatisch</option><option value="Handmatig rendement">Handmatig</option><option value="Geen rendement">Uit</option></select></label></article>
                </div>
              </details>

              <details class="settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:home-battery"></ha-icon><div><b>Zelfconsumptie</b><small>Meetbron, regelmethode en offsets.</small></div></summary>
                <div class="settings-section-content">
                  <article class="card strategy-setting"><b>Aansturing</b><div class="choice-switch" id="selfcons-choice"><button data-selfcons="Marstek">Marstek</button><button data-selfcons="OpenEMS">OpenEMS</button></div><div id="selfcons-status" class="strategy-status">Status wordt geladen</div></article>
                  <article class="card strategy-setting" id="fallback-setting"><div class="switch-line"><div><b>Automatisch terugvallen op Marstek</b></div><button id="fallback-toggle" class="small-toggle"><i></i></button></div></article>
                  <article class="card sensor-setting"><label>Grid Sensor<select id="grid-source-select"><option>P1</option><option>CT</option></select></label></article>
                  <div class="advanced-only">
                    <article class="card sensor-setting"><label>Verdeling bij zelfconsumptie<select id="battery-distribution-select"><option>Alle accu’s verdelen</option><option>Efficiënte accuselector (aanbevolen)</option></select></label><small>De efficiënte selector gebruikt normaal één accu en schakelt alleen extra accu’s bij als het gevraagde vermogen dat nodig maakt.</small><div id="battery-distribution-status" class="strategy-status">Selectorstatus wordt geladen</div></article>
                    <span hidden>Sensor gridverbruik</span><input id="grid-display-sensor-input" type="hidden"><input id="solar-sensor-input" type="hidden"><select id="grid-sign-select" hidden><option>Positief is afname</option><option>Positief is teruglevering</option></select>
                  </div>
                </div>
              </details>

              <details class="settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:transmission-tower-export"></ha-icon><div><b>Terugleveren</b><small>Simulatie en energiereserve.</small></div></summary>
                <div class="settings-section-content">
                  <article class="card export-mode-setting"><label>Terugleveren<select id="export-mode-select"><option value="Automatisch exporteren">Automatisch</option><option value="Alleen simuleren">Simuleren</option></select></label><div id="export-setting-status" class="strategy-status">Terugleverstatus wordt geladen</div></article>
                  <article class="card sensor-setting"><label>Reserve voor laden<select id="before-charge-reserve-mode"><option>Vast</option><option>Dynamisch</option></select></label><small>Alleen wanneer later dezelfde dag een bevestigd laadblok aankomt.</small></article>
                  <article class="card sensor-setting"><label>Reserve na laden<select id="export-reserve-mode"><option>Vast</option><option>Dynamisch</option></select></label><small>Na het laadblok en wanneer die dag geen toekomstig laadblok meer volgt.</small></article>
                  <article class="card sensor-setting"><div class="quick-row"><div><b>Automatische exportreserve</b><small>Reserve = verbruik tot het volgende laadblok + buffer + reserve (zelfde schuiven als Slim laden). De vaste reserves worden dan genegeerd.</small></div><button id="auto-reserve-toggle" class="small-toggle"><i></i></button></div><div id="auto-reserve-status" class="strategy-status">Status wordt geladen</div></article>
                  ${this._settingsCards("export")}
                  <article class="card strategy-setting"><b>Werking dynamische reserve</b><small>De planner bewaart het voorspelde woningverbruik tot het eerstvolgende donkergroene mogelijke laadblok plus de ingestelde Dynamische reserve.</small><div id="dynamic-export-reserve-status" class="strategy-status">Reservestatus wordt geladen</div></article>
                </div>
              </details>

              <details class="settings-section">
                <summary class="settings-section-title"><ha-icon icon="mdi:currency-eur"></ha-icon><div><b>Energieleverancier</b><small>Resolutie en prijsgrenzen voor alle planners.</small></div></summary>
                <div class="settings-section-content">
                  <article class="card sensor-setting"><label>Resolutie (Tijdresolutie OpenEMS)<select id="discharge-resolution-select"><option value="15 minuten">Kwartier</option><option value="60 minuten">Uur</option></select></label></article>
                  ${this._settingsCards("leverancier")}
                  <div class="advanced-only">
                    <article class="card sensor-setting"><label>Entiteit Marktprijs<input id="frank-market-sensor-input" list="all-entities"></label></article>
                    <article class="card sensor-setting"><b>Marktprijs+</b><small>Wordt automatisch berekend uit Marktprijs plus de vaste Frank terugleververgoeding; dit bedrag is niet handmatig instelbaar.</small></article>
                    <article class="card sensor-setting"><label>Entiteit All-in prijs<input id="frank-allin-sensor-input" list="all-entities"></label></article>
                  </div>
                </div>
              </details>

              <div class="advanced-only">
                <details class="settings-section">
                  <summary class="settings-section-title"><ha-icon icon="mdi:cog-outline"></ha-icon><div><b>Geavanceerd menu</b><small>Technische begrenzingen en accu-entiteiten.</small></div></summary>
                  <div class="settings-section-content">
                    ${this._settingsCards("advanced")}
                    <article class="card sensor-setting"><label>Rendement voor terugleverplanning<select id="export-efficiency-mode"><option>Geen rendement</option><option>Zelfgeleerd rendement</option><option>Handmatig rendement</option></select></label><div id="export-efficiency-status" class="strategy-status">Rendementsstatus wordt geladen</div></article>
                    <article class="card entity-settings">
                      <details><summary>Geavanceerd: entiteiten per accu</summary>
                        <p>Wijzig alleen wanneer jouw Omnibattery-entiteiten anders heten. Herstart AppDaemon na wijzigingen.</p>
                        ${E.batteries.map((battery) => `<fieldset><legend>Accu ${battery.n}</legend>${E.batteryEntityFields.map(([field, label]) => `<label>${label}<input id="entity-b${battery.n}-${field}" data-entity-helper="${battery.helpers[field]}" list="all-entities" placeholder="entity_id"></label>`).join("")}</fieldset>`).join("")}
                      </details>
                    </article>
                  </div>
                </details>
              </div>
              <article class="card settings-actions">
                <b>Instellingen bewaren en herstellen</b>
                <p>Persoonlijke standaarden blijven buiten de versiemap bewaard en gaan niet verloren bij een update.</p>
                <div>
                  <button id="save-defaults"><ha-icon icon="mdi:content-save-cog"></ha-icon>Huidige instellingen als mijn standaard</button>
                  <button id="restore-defaults"><ha-icon icon="mdi:backup-restore"></ha-icon>Mijn standaard herstellen</button>
                  <button id="restore-factory" class="danger"><ha-icon icon="mdi:factory"></ha-icon>Fabrieksinstellingen herstellen</button>
                </div>
                <small id="settings-manager-status">Instellingenbeheer wordt geladen</small>
              </article>
              <datalist id="power-sensors"></datalist>
              <datalist id="all-entities"></datalist>
              <article class="card warning-card"><ha-icon icon="mdi:information-outline"></ha-icon><div><b>Wijzigingen worden automatisch verwerkt</b><p>De planner rekent twee seconden na een wijziging opnieuw. Een reeds gestart laadblok en het vastgezette vermogen blijven behouden.</p></div></article>
            </div>
          </section>

          <section class="page" data-page="status" hidden>
            <div class="page-title"><div><h1>Status & historie</h1><p>Overgangen, verificaties en fouten van de afgelopen zeven dagen</p></div><button class="refresh" id="refresh-status"><ha-icon icon="mdi:refresh"></ha-icon>Vernieuwen</button></div>
            <div class="status-layout">
              <article class="card current-status">${this._heading("mdi:shield-check-outline", "Actuele controle")}<div id="status-details"></div></article>
              <article class="card status-hours">${this._heading("mdi:calendar-clock", "Ingedeelde urenblokken")}<div id="assigned-hour-blocks"><div class="empty">Urenplanning wordt geladen</div></div></article>
              <article class="card timeline-card">${this._heading("mdi:timeline-clock-outline", "Gebeurtenissen · 30 dagen")}<div id="event-counts" class="event-counts"></div><div class="event-filters"><button class="active" data-event-filter="all">Alles</button><button data-event-filter="error">Fouten</button><button data-event-filter="warning">Waarschuwingen</button><button data-event-filter="recovered">Hersteld</button><button data-event-filter="info">Informatie</button><button id="clear-runtime-events" class="event-clear"><ha-icon icon="mdi:delete-sweep-outline"></ha-icon>Alles wissen</button></div><div id="status-timeline" class="timeline"><div class="empty">Gebeurtenissen worden geladen</div></div></article>
              <article class="card grid-offset-card" id="grid-offset-status-card" hidden>
                <div class="grid-offset-head"><div><b>Grid CT-netvermogen kalibratie</b><small>P1 is de referentie; CT blijft de snelle regelsensor.</small></div><span id="grid-offset-chip" class="chip">—</span></div>
                <div class="grid-offset-controls"><button id="grid-offset-calibrate"><ha-icon icon="mdi:target"></ha-icon><span class="cal-label" data-idle="Nieuwe kalibratie starten">Nieuwe kalibratie starten</span></button></div>
                <div class="grid-offset-live">
                  <div><span>CT Grid</span><b id="grid-offset-fast">— W</b></div><div><span>P1-referentie</span><b id="grid-offset-reference">— W</b></div>
                  <div><span>Actueel verschil</span><b id="grid-offset-difference">— W</b></div><div><span>Automatisch berekend</span><b id="grid-offset-automatic">— W</b></div>
                  <div><span>Actieve offset</span><b id="grid-offset-active">— W</b></div><div><span>OpenEMS gebruikt</span><b id="grid-offset-corrected">— W</b></div>
                </div>
                <div id="grid-offset-message" class="strategy-status">Kalibratiegegevens worden geladen</div>
              </article>
              <article class="card status-selector">${this._heading("mdi:battery-sync-outline", "Efficiënte accuselector")}<div id="battery-selector-status-content"><div class="empty">Selectorstatus wordt geladen</div></div></article>
              <article class="card status-efficiency">${this._heading("mdi:gauge", "Zelfgeleerd rendement per accu")}<div id="efficiency-status-content"></div></article>
              <div class="status-advice-grid">
                <article class="card status-advice charge-advice">${this._heading("mdi:battery-arrow-up", "Laadadvies")}<div id="charge-advice-summary"><div class="empty">Laadadvies wordt geladen</div></div></article>
                <article class="card status-advice self-advice">${this._heading("mdi:home-battery", "Zelfconsumptieadvies")}<div id="self-advice-summary"><div class="empty">Zelfconsumptieadvies wordt geladen</div></div></article>
                <article class="card status-advice export-advice">${this._heading("mdi:transmission-tower-export", "Terugleveradvies")}<div id="export-advice-summary"><div class="empty">Terugleveradvies wordt geladen</div></div></article>
              </div>
              <article class="card status-planning">${this._heading("mdi:clipboard-text-clock-outline", "Actuele energieplanning")}<div id="planning-status-content"><div class="empty">Planning wordt geladen</div></div></article>
            </div>
            <div id="event-modal" class="event-modal" hidden><div class="event-modal-card"><button id="event-modal-close" class="event-modal-close" aria-label="Sluiten">×</button><div id="event-modal-content"></div></div></div>
          </section>
        </main>
      </div>`;

    this.shadowRoot.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => this._showTab(button.dataset.tab)));
    this.shadowRoot.querySelectorAll("[data-more]").forEach((el) => el.addEventListener("click", () => this._moreInfo(el.dataset.more)));
    this._eventFilter = "all";
    this._wireControls();
    this._loadDeviceInfo();
    this._startTimers();
  }

  _startTimers() {
    if (!this._historyTimer) this._historyTimer = setInterval(() => {
      if (
        (this._tab === "graphs" || this._tab === "status")
        && document.visibilityState !== "hidden"
        && this.isConnected
      ) this._loadHistory(false);
    }, 5 * 60 * 1000);
    if (!this._manualCountdownTimer) this._manualCountdownTimer = setInterval(
      () => {
        this._patchManualStatus(this._obj(E.manualStatus));
        E.batteries.forEach((b) => this._patchBatterySummary(b));
      },
      1000,
    );
  }

  _wireControls() {
    this._wireControlStability();
    this.$("auto-toggle").addEventListener("click", () => this._toggleEntity("input_boolean", E.auto));
    this.shadowRoot.querySelectorAll("[data-auto-mode]").forEach((button) =>
      button.addEventListener("click", () => this._setBooleanEntity(
        "input_boolean", E.auto, button.dataset.autoMode === "on"
      ))
    );
    this.$("grid-charge-toggle").addEventListener("click", () => this._toggleEntity("input_boolean", E.gridChargeAllowed));
    this.$("auto-reserve-toggle").addEventListener("click", () => this._toggleEntity("input_boolean", E.autoExportReserve));
    this.$("smart-charge-toggle").addEventListener("click", () => this._toggleEntity("input_boolean", E.smartCharge));
    this.$("price-forecast-toggle").addEventListener("click", () => this._toggleEntity("input_boolean", E.priceForecast));
    this.$("price-forecast-entity-input").addEventListener("change", (ev) => this._saveText(E.priceForecastEntityHelper, ev.target.value.trim()));
    this.$("grid-export-toggle").addEventListener("click", () => this._toggleEntity("input_boolean", E.gridExportAllowed));
    Object.entries(E.loadBalancerOff).forEach(([kind, entity]) => this.$(`lb-toggle-${kind}`).addEventListener("click", () => this._toggleEntity("input_boolean", entity)));
    this._wireLoadBalancerSettings();
    E.batteries.forEach((b) => this.$(`accu-active-toggle-${b.n}`).addEventListener("click", () => this._toggleEntity("input_boolean", b.disabled)));
    this.$("assigned-hour-blocks").addEventListener("click", (event) => {
      const block = event.target.closest("[data-assigned-block]");
      if (!block || block.dataset.expandable !== "true") return;
      const key = block.dataset.assignedBlock;
      if (this._expandedAssignedHourBlocks.has(key)) {
        this._expandedAssignedHourBlocks.delete(key);
      } else {
        this._expandedAssignedHourBlocks.add(key);
      }
      this._renderAssignedHourBlocks();
    });
    this.$("advanced-settings-toggle").addEventListener("click", () => this._setAdvancedSettings(!this._advancedSettings));
    this.$("manual-mode").addEventListener("change", (ev) => {
      this._selectEntity("input_select", E.manualMode, ev.target.value);
      this._patchManualPower(ev.target.value);
    });
    const manualPower = this.$("manual-power-input");
    manualPower.addEventListener("input", () => { this.$("manual-power-value").textContent = manualPower.value; });
    manualPower.addEventListener("change", () => this._callEntityService(
      "input_number", "set_value", this._manualPowerEntity(), manualPower.value,
      { value: Number(manualPower.value) }
    ));
    E.batteries.forEach((b, i) => this.$(`manual-accu-${b.n}`).addEventListener("change", (ev) => {
      this._setBooleanEntity("input_boolean", E.manualExcluded[i], !ev.target.checked);
    }));
    this.$("manual-distribution").addEventListener("change", (ev) => this._selectEntity("input_select", E.manualDistribution, ev.target.value));
    E.settings.forEach(([id], i) => this._wireNumber(`setting-${i}`, id));
    E.batteries.forEach((b) => {
      [["maxcharge", b.maxCharge], ["maxdischarge", b.maxDischarge], ["charge", b.charge], ["discharge", b.discharge]].forEach(([key, entity]) => this._wireNumber(`b${b.n}-${key}`, entity));
      this._wireRs485(b);
      // De EMS regelt deze functie nooit automatisch. Deze bediening is
      // uitsluitend gekoppeld aan een bewuste gebruikersactie op de accupagina.
      this._wireSwitch(`b${b.n}-backup`, b.backup);
      this._wireSelect(`b${b.n}-work`, b.work);
      this._wireSelect(`b${b.n}-force`, b.force);
      this.$(`b${b.n}-device-reset`).addEventListener("click", () => {
        if (!this._obj(b.deviceReset)) {
          this._notifyUser(`De resetknop van Accu ${b.n} is niet beschikbaar.`);
          return;
        }
        if (!confirm(
          `Accu ${b.n} opnieuw opstarten? De accu is tijdelijk niet `
          + "beschikbaar en OpenEMS kan de regeling kort pauzeren."
        )) return;
        Promise.resolve(__openemsCallService(this._hass, "button", "press", {
          entity_id: b.deviceReset,
        })).then(() => this._notifyUser(
          `Opnieuw opstarten van Accu ${b.n} is aangevraagd.`
        )).catch((error) => {
          console.error(`[OpenEMS-M] ${b.deviceReset} kon niet worden bediend`, error);
          this._notifyUser(`Accu ${b.n} kon niet opnieuw worden opgestart.`);
        });
      });
    });
    this.$("home-sensor-input").addEventListener("change", (ev) => this._savePowerEntity("home", ev.target.value.trim()));
    this.$("grid-sensor-input").addEventListener("change", (ev) => this._savePowerEntity("grid", ev.target.value.trim()));
    this.$("grid-display-sensor-input").addEventListener("change", (ev) => this._savePowerEntity("display-grid", ev.target.value.trim()));
    this.$("grid-reference-input").addEventListener("change", (ev) => this._saveText(E.gridReferenceHelper, ev.target.value.trim()));
    this.$("solar-sensor-input").addEventListener("change", (ev) => this._saveText(E.solarSensorHelper, ev.target.value.trim()));
    this.shadowRoot.querySelectorAll("[data-selfcons]").forEach((button) => button.addEventListener("click", () => this._selectEntity("input_select", E.selfConsumptionMode, button.dataset.selfcons)));
    this.$("battery-distribution-select").addEventListener("change", (ev) => this._selectEntity("input_select", E.batteryDistribution, ev.target.value));
    this.$("fallback-toggle").addEventListener("click", () => {
      if (this.$("fallback-toggle").disabled) return;
      this._toggleEntity("input_boolean", E.selfConsumptionFallback);
    });
    [["charge-efficiency-mode", E.chargeEfficiencyMode], ["discharge-efficiency-mode", E.dischargeEfficiencyMode], ["export-efficiency-mode", E.exportEfficiencyMode]].forEach(([id, entity]) => {
      this.$(id).addEventListener("change", (ev) => this._selectEntity("input_select", entity, ev.target.value));
    });
    this.$("discharge-resolution-select").addEventListener("change", (ev) => this._selectEntity("input_select", E.dischargeResolution, ev.target.value));
    this.$("export-mode-select").addEventListener("change", (ev) => this._selectEntity("input_select", E.exportMode, ev.target.value));
    this.$("before-charge-reserve-mode").addEventListener("change", (ev) => this._selectEntity("input_select", E.beforeChargeReserveMode, ev.target.value));
    this.$("export-reserve-mode").addEventListener("change", (ev) => this._selectEntity("input_select", E.exportReserveMode, ev.target.value));
    this.$("frank-allin-sensor-input").addEventListener("change", (ev) => this._saveText(E.frankAllInHelper, ev.target.value.trim()));
    this.$("frank-market-sensor-input").addEventListener("change", (ev) => this._saveText(E.frankMarketHelper, ev.target.value.trim()));
    this.$("mobile-control-toggle").addEventListener("click", () => {
      const card = this.$("mobile-control-toggle").closest(".control-card");
      card.classList.toggle("mobile-open");
      this.$("mobile-control-toggle").querySelector("span").textContent = card.classList.contains("mobile-open") ? "Bediening verbergen" : "Bediening tonen";
    });
    this.$("grid-sign-select").addEventListener("change", (ev) => this._selectEntity("input_select", E.gridPowerSign, ev.target.value));
    this.$("grid-source-select").addEventListener("change", (ev) => this._selectEntity("input_select", E.gridSensorSource, ev.target.value));
    this._wireNumber("grid-offset-manual", E.gridManualOffset);
    this._wireNumber("home-power-offset", E.homePowerOffset);
    this.$("grid-offset-calibrate-settings").addEventListener("click", () => this._pressCalibration("grid", E.gridOffsetCalibrate));
    this.$("home-offset-calibrate").addEventListener("click", () => this._pressCalibration("home", E.homeOffsetCalibrate));
    this.$("grid-offset-calibrate").addEventListener("click", () => this._pressCalibration("grid", E.gridOffsetCalibrate));
    this.$("save-defaults").addEventListener("click", () => {
      if (confirm("Huidige OpenEMS-instellingen als jouw persoonlijke standaard opslaan?")) {
        __openemsCallService(this._hass, "input_button", "press", { entity_id: E.saveDefaults });
      }
    });
    this.$("restore-defaults").addEventListener("click", () => {
      if (confirm("Jouw opgeslagen persoonlijke standaardinstellingen herstellen?")) {
        __openemsCallService(this._hass, "input_button", "press", { entity_id: E.restoreDefaults });
      }
    });
    this.$("restore-factory").addEventListener("click", () => {
      if (confirm("Alle OpenEMS-instellingen terugzetten naar de fabriekswaarden?")) {
        __openemsCallService(this._hass, "input_button", "press", { entity_id: E.restoreFactory });
      }
    });
    this.shadowRoot.querySelectorAll("[data-entity-helper]").forEach((input) => input.addEventListener("change", () => this._saveText(input.dataset.entityHelper, input.value.trim())));
    this.$("refresh-history").addEventListener("click", () => this._loadHistory(true));
    this.$("refresh-status").addEventListener("click", () => {
      this._renderStatusHistory();
      this._loadHistory(true);
    });
    this.$("clear-runtime-events").addEventListener("click", () => {
      if (confirm("Alle OpenEMS-gebeurtenissen definitief wissen?")) {
        __openemsCallService(this._hass, "input_button", "press", {
          entity_id: E.runtimeEventsClear,
        });
      }
    });
    this.shadowRoot.querySelectorAll("[data-event-filter]").forEach((button) =>
      button.addEventListener("click", () => {
        this._eventFilter = button.dataset.eventFilter;
        this._renderStatusHistory();
      })
    );
    this.$("event-modal-close").addEventListener("click", () => {
      this.$("event-modal").hidden = true;
    });
    this.$("event-modal").addEventListener("click", (event) => {
      if (event.target === this.$("event-modal")) {
        this.$("event-modal").hidden = true;
      }
    });
    this._applySettingsView();
  }

  _setAdvancedSettings(enabled) {
    this._advancedSettings = Boolean(enabled);
    localStorage.setItem("openems-settings-advanced", String(this._advancedSettings));
    this._applySettingsView();
  }

  _applySettingsView() {
    this.shadowRoot.querySelectorAll(".advanced-only").forEach((element) => {
      element.hidden = !this._advancedSettings;
    });
    this.$("advanced-settings-toggle")?.classList.toggle("on", this._advancedSettings);
  }

  _schedulePatch() {
    if (this._patchFrame !== null) return;
    this._patchFrame = true;
    queueMicrotask(() => {
      this._patchFrame = null;
      if (this.isConnected) this._patch();
    });
  }

  _callEntityService(domain, service, entity, expectedState, data = {}) {
    const previous = this._pendingEntityStates.get(entity);
    if (previous) clearTimeout(previous.timer);
    const pending = {
      value: String(expectedState),
      until: Date.now() + 20000,
      timer: null,
    };
    pending.timer = setTimeout(() => {
      if (this._pendingEntityStates.get(entity) !== pending) return;
      this._pendingEntityStates.delete(entity);
      this._schedulePatch();
    }, 20100);
    this._pendingEntityStates.set(entity, pending);
    this._patch();
    let request;
    try {
      request = __openemsCallService(this._hass, domain, service, {
        entity_id: entity,
        ...data,
      });
    } catch (error) {
      request = Promise.reject(error);
    }
    return Promise.resolve(request).catch((error) => {
      if (this._pendingEntityStates.get(entity) === pending) {
        clearTimeout(pending.timer);
        this._pendingEntityStates.delete(entity);
        this._patch();
      }
      console.error(`[OpenEMS-M] ${entity} kon niet worden gewijzigd`, error);
      this._notifyUser(
        `Instelling kon niet worden opgeslagen. Controleer de verbinding en probeer opnieuw.`
      );
    });
  }

  _notifyUser(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", {
      bubbles: true,
      composed: true,
      detail: { message },
    }));
  }

  _setBooleanEntity(domain, entity, enabled) {
    return this._callEntityService(
      domain, enabled ? "turn_on" : "turn_off", entity,
      enabled ? "on" : "off"
    );
  }

  _wireLoadBalancerSettings() {
    const limit = (id, entity, min, max) => this.$(id).addEventListener("change", (ev) => {
      const value = Math.round(Number(ev.target.value));
      if (!Number.isFinite(value) || value < min || value > max) {
        ev.target.value = this._state(entity, "");
        return;
      }
      this._callEntityService("input_number", "set_value", entity, value, { value });
    });
    limit("lb-max-import", E.lbMaxImport, 1000, 12000);
    limit("lb-max-export", E.lbMaxExport, 500, 8000);
    const check = (id, otherId, entity) => this.$(id).addEventListener("change", (ev) => {
      if (!ev.target.checked && !this.$(otherId).checked) {
        ev.target.checked = true;
        return;
      }
      this._setBooleanEntity("input_boolean", entity, !ev.target.checked);
    });
    check("lb-p1-check", "lb-ct-check", E.lbP1Off);
    check("lb-ct-check", "lb-p1-check", E.lbCtOff);
    this.$("lb-pref-select").addEventListener("change", (ev) => this._selectEntity("input_select", E.lbPreferred, ev.target.value));
    this.$("lb-both-toggle").addEventListener("click", () => this._toggleEntity("input_boolean", E.lbBoth));
  }

  _patchLoadBalancerSettings() {
    this._syncControlValue(this.$("lb-max-import"), Math.round(this._num(E.lbMaxImport) || 7950));
    this._syncControlValue(this.$("lb-max-export"), Math.round(this._num(E.lbMaxExport) || 5000));
    const p1 = this.$("lb-p1-check");
    const ct = this.$("lb-ct-check");
    p1.checked = this._state(E.lbP1Off) !== "on";
    ct.checked = this._state(E.lbCtOff) !== "on";
    if (!p1.checked && !ct.checked) { p1.checked = true; ct.checked = true; }
    const both = p1.checked && ct.checked;
    this._setSelect("lb-pref-select", this._state(E.lbPreferred, "P1"));
    this.$("lb-pref-row").hidden = !both || this._state(E.lbBoth) === "on";
    this.$("lb-both-row").hidden = !both;
    this.$("lb-both-toggle").classList.toggle("on", this._state(E.lbBoth) === "on");
  }

  _priceForecastText() {
    const item = this._obj(E.priceForecastStatus);
    const a = item?.attributes || {};
    const state = item?.state;
    if (state === "uit" || this._state(E.priceForecast) !== "on") return "Voorspelling staat uit";
    const updated = a.bijgewerkt
      ? new Date(a.bijgewerkt).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit" })
      : null;
    if (state === "actief") {
      const diff = a.gemiddeld_verschil_ct != null ? ` · echte uren wijken gemiddeld ${a.gemiddeld_verschil_ct} ct af van Frank` : "";
      return `Actief · bijgewerkt ${updated}${diff}`;
    }
    if (state === "verouderd") return `Verouderd (${a.leeftijd_uur} uur oud) en dus niet gebruikt${a.fout ? ` · ${a.fout}` : ""}`;
    if (state === "fout") return `Sensor niet bruikbaar: ${a.fout || "onbekende fout"}`;
    return "Wachten op de eerste voorspelling";
  }

  _forecastRows(stepMs, afterMs, untilMs) {
    const item = this._obj(E.priceForecastStatus);
    if (item?.state !== "actief" || !Array.isArray(item.attributes?.uren)) return [];
    const rows = [];
    const plan = this._obj(E.priceForecastPlan);
    const modes = new Map(
      plan?.state === "aan" && Array.isArray(plan.attributes?.uren)
        ? plan.attributes.uren.map((hour) => [new Date(hour.van).getTime(), hour.modus])
        : []
    );
    item.attributes.uren.forEach((hour) => {
      const start = new Date(hour.van).getTime();
      const value = Number(hour.prijs);
      if (!Number.isFinite(start) || !Number.isFinite(value)) return;
      const mode = modes.get(start) || null;
      const bare = Number(hour.markt);
      const returnFee = this._num("number.openems_frank_terugleververgoeding");
      const market = hour.markt !== undefined && hour.markt !== null && Number.isFinite(bare) ? bare : undefined;
      const marketPlus = Number.isFinite(market)
        ? market + (Number.isFinite(returnFee) ? returnFee : 0.0182) : undefined;
      for (let offset = 0; offset < 3600000; offset += stepMs) {
        const time = start + offset;
        if (time >= afterMs && time < untilMs) rows.push({ time: new Date(time), value, forecast: true, mode, stepMs, market, marketPlus, real: Boolean(hour.echt) });
      }
    });
    return rows.sort((a, b) => a.time - b.time);
  }

  _toggleEntity(domain, entity) {
    return this._setBooleanEntity(domain, entity, this._state(entity) !== "on");
  }

  _selectEntity(domain, entity, option) {
    return this._callEntityService(
      domain, "select_option", entity, option, { option }
    );
  }

  _wireNumber(prefix, entity) {
    const input = this.$(`${prefix}-input`);
    input.addEventListener("input", () => { this.$(`${prefix}-value`).textContent = input.value; });
    input.addEventListener("change", () => this._callEntityService(
      entity.split("." )[0], "set_value", entity, input.value,
      { value: Number(input.value) }
    ));
  }

  _wireControlStability() {
    const supported = (target) => target?.matches?.(
      "select,input[type='range'],input[type='number']"
    );
    const markEditing = (target) => {
      if (supported(target)) target._openemsEditingUntil = Date.now() + 1500;
    };
    this.shadowRoot.addEventListener(
      "pointerdown", (event) => {
        markEditing(event.target);
        if (!supported(event.target)) return;
        if (this._activePointerControl) {
          this._activePointerControl._openemsPointerActive = false;
        }
        this._activePointerControl = event.target;
        event.target._openemsPointerActive = true;
      }, true
    );
    this.shadowRoot.addEventListener(
      "keydown", (event) => markEditing(event.target), true
    );
    this.shadowRoot.addEventListener(
      "input", (event) => markEditing(event.target), true
    );
    this.shadowRoot.addEventListener("change", (event) => {
      if (!supported(event.target)) return;
      event.target._openemsPendingValue = String(event.target.value);
      event.target._openemsPendingUntil = Date.now() + 20000;
      event.target._openemsEditingUntil = 0;
    }, true);
    this._releaseControlPointer = () => {
      if (!this._activePointerControl) return;
      this._activePointerControl._openemsPointerActive = false;
      this._activePointerControl = null;
    };
    window.addEventListener("pointerup", this._releaseControlPointer);
    window.addEventListener("pointercancel", this._releaseControlPointer);
  }

  _controlValuesEqual(element, left, right) {
    if (element?.tagName === "SELECT") return String(left) === String(right);
    const leftNumber = Number(left);
    const rightNumber = Number(right);
    if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
      return Math.abs(leftNumber - rightNumber) < 0.000001;
    }
    return String(left) === String(right);
  }

  _controlMaySync(element, authoritativeValue) {
    if (!element) return false;
    if (element._openemsPendingValue !== undefined) {
      if (this._controlValuesEqual(
        element, element._openemsPendingValue, authoritativeValue
      )) {
        delete element._openemsPendingValue;
        element._openemsPendingUntil = 0;
      } else if (Date.now() < Number(element._openemsPendingUntil || 0)) {
        return false;
      } else {
        delete element._openemsPendingValue;
        element._openemsPendingUntil = 0;
        element._openemsEditingUntil = 0;
        return true;
      }
    }
    if (element._openemsPointerActive) return false;
    if (Date.now() < Number(element._openemsEditingUntil || 0)) return false;
    return this.shadowRoot.activeElement !== element;
  }

  _syncControlValue(element, authoritativeValue) {
    if (
      element
      && this._controlMaySync(element, authoritativeValue)
      && String(element.value) !== String(authoritativeValue)
    ) {
      element.value = authoritativeValue;
    }
  }

  _wireSelect(id, entity) {
    this.$(id).addEventListener("change", (ev) => this._selectEntity("select", entity, ev.target.value));
  }

  _wireSwitch(prefix, entity) {
    this.$(`${prefix}-toggle`).addEventListener(
      "click", () => this._toggleEntity("switch", entity)
    );
  }

  _wireRs485(b) {
    const button = this.$(`b${b.n}-rs485-toggle`);
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      const turningOn = this._state(b.rs485) !== "on";
      button.disabled = true;
      try {
        await this._setBooleanEntity("switch", b.rs485, turningOn);
      } catch (error) {
        console.error(`[OpenEMS-M] RS485 Accu ${b.n} kon niet worden gewijzigd`, error);
      } finally {
        button.disabled = false;
      }
    });
  }

  _savePowerEntity(kind, value) {
    if (!value || !this._obj(value)) return;
    const helper = {
      home: E.homeSensorHelper,
      grid: E.gridSensorHelper,
      "display-grid": E.gridDisplaySensorHelper,
    }[kind];
    if (!helper) return;
    localStorage.setItem(`openems-${kind}-sensor`, value);
    if (this._obj(helper)) __openemsCallService(this._hass, "input_text", "set_value", { entity_id: helper, value });
    this._historyAt = 0;
    if (this._tab === "graphs" || this._tab === "status") {
      this._loadHistory(true);
    }
  }

  _saveText(helper, value) {
    if (!helper || !value || !this._obj(value) || !this._obj(helper)) return;
    __openemsCallService(this._hass, "input_text", "set_value", { entity_id: helper, value });
  }

  _saveOptionalText(helper, value) {
    if (!helper || !this._obj(helper)) return;
    if (value && !this._obj(value)) return;
    __openemsCallService(this._hass, "input_text", "set_value", {
      entity_id: helper,
      value,
    });
  }

  async _loadDeviceInfo() {
    if (!this._hass?.callWS) return;
    try {
      const [entities, devices] = await Promise.all([
        this._hass.callWS({ type: "config/entity_registry/list" }),
        this._hass.callWS({ type: "config/device_registry/list" }),
      ]);
      E.batteries.forEach((b) => {
        const registryEntity = entities.find((item) => item.entity_id === b.soc);
        const device = devices.find((item) => item.id === registryEntity?.device_id);
        const url = device?.configuration_url || "";
        try { this._batteryIps[b.n] = new URL(url).hostname; } catch (_error) { this._batteryIps[b.n] = url; }
      });
      this._patch();
    } catch (error) {
      console.debug("[OpenEMS-M] Apparaat-IP niet beschikbaar", error);
    }
  }

  _showTab(tab) {
    this._tab = tab;
    this.shadowRoot.querySelectorAll("[data-page]").forEach((page) => { page.hidden = page.dataset.page !== tab; });
    this.shadowRoot.querySelectorAll("[data-tab]").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
    this._patch();
    if (tab === "graphs" || tab === "status") this._loadHistory(false);
  }

  _patch() {
    if (!this._hass || !this._built) return;
    this._rememberLiveExportExecution();
    const dark = this._hass.themes && this._hass.themes.darkMode;
    this.setAttribute("data-theme", dark === false ? "light" : "dark");
    const auto = this._state(E.auto) === "on";
    this.$("auto-toggle").classList.toggle("on", auto);
    this.shadowRoot.querySelectorAll("[data-auto-mode]").forEach((button) => {
      button.classList.toggle(
        "active", (button.dataset.autoMode === "on") === auto
      );
    });
    this.$("grid-charge-toggle").classList.toggle("on", this._state(E.gridChargeAllowed) === "on");
    this.$("grid-export-toggle").classList.toggle("on", this._state(E.gridExportAllowed) === "on");
    E.batteries.forEach((b) => this.$(`accu-active-toggle-${b.n}`)?.classList.toggle("on", this._state(b.disabled) !== "on"));
    Object.entries(E.loadBalancerOff).forEach(([kind, entity]) => this.$(`lb-toggle-${kind}`)?.classList.toggle("on", this._state(entity) !== "on"));
    this.$("smart-charge-toggle")?.classList.toggle("on", this._state(E.smartCharge) === "on");
    this.$("price-forecast-toggle")?.classList.toggle("on", this._state(E.priceForecast) === "on");
    const forecastText = this.$("price-forecast-status");
    if (forecastText) forecastText.textContent = this._priceForecastText();
    this._syncControlValue(this.$("price-forecast-entity-input"), this._state(E.priceForecastEntityHelper, "sensor.wattwanneer_gemiddelde_deze_week"));
    this.$("auto-reserve-toggle")?.classList.toggle("on", this._state(E.autoExportReserve) === "on");
    const autoRes = this._obj(E.autoExportReserveStatus);
    const autoResText = this.$("auto-reserve-status");
    if (autoResText) autoResText.textContent = autoRes?.attributes?.uitleg || "Geen status";
    const smart = this._obj(E.smartChargeStatus);
    const smartText = this.$("smart-charge-status");
    if (smartText) smartText.textContent = smart?.attributes?.uitleg || "Geen status";
    this._patchLoadBalancerSettings();
    const lb = this._obj(E.loadBalancerStatus);
    const lbText = this.$("lb-status");
    if (lbText) {
      const a = lb?.attributes || {};
      const labels = { bewaakt: "Bewaakt", begrenst: "Begrenst het laden", noodgrens: "Noodgrens (geen netmeting)", inactief: "Inactief (niet aan het laden)", uit: "Uitgeschakeld voor deze modus" };
      const parts = [labels[lb?.state] || "Geen status"];
      if (a.netimport_w != null) parts.push(`netimport ${Math.round(a.netimport_w)} W (${a.bron})`);
      if (a.p1_w != null && a.ct_w != null) parts.push(`P1 ${a.p1_w} W · CT ${a.ct_w} W`);
      if (a.limiet_w != null) parts.push(`grens ${Math.round(a.limiet_w)} W import / ${Math.round(a.export_limiet_w)} W export`);
      if (a.max_laadvermogen_w != null) parts.push(`max laden ${Math.round(a.max_laadvermogen_w)} W`);
      if (a.export_modus && a.export_modus !== "inactief") {
        parts.push(`ontladen: ${a.export_modus}${a.export_w != null ? `, export ${a.export_w} W` : ""}${a.max_ontlaadvermogen_w != null ? `, max ontladen ${a.max_ontlaadvermogen_w} W` : ""}`);
      }
      lbText.textContent = parts.join(" · ");
    }
    this.$("mode-chip").textContent = auto ? "AUTO" : "HANDMATIG";
    this.$("mode-chip").className = `chip ${auto ? "good" : "warn"}`;
    this.$("manual-controls").classList.toggle("disabled", auto);
    this.$("manual-controls").hidden = auto;

    const soc = this._num(E.soc);
    this._patchRing("system", soc, `${this._fmt(this._num(E.stored), 2)} / ${this._fmt(E.batteries.reduce((sum, b) => sum + (this._num(b.capacity) || 0), 0), 2)} kWh`);
    const netStatus = this._obj(E.gridStatus);
    const netCode = String(netStatus?.attributes?.code || "unknown");
    this.$("sys-chip").textContent = String(
      netStatus?.state || "Netstatus onbekend"
    ).toUpperCase();
    this.$("sys-chip").className = `chip ${
      netCode === "normal" ? "good" : netCode === "off_grid" ? "bad" : "warn"
    }`;
    const status = this._obj(E.status);
    const manual = this._obj(E.manualStatus);
    const selectorStatus = this._obj(E.batterySelectorStatus);
    const selectorActive = auto
      && String(selectorStatus?.state || "").toLowerCase() === "actief"
      && Array.isArray(selectorStatus?.attributes?.actieve_accus)
      && selectorStatus.attributes.actieve_accus.length > 0;
    const actualPower = E.batteries.reduce(
      (sum, battery) => sum + this._actualPower(battery), 0
    );
    const reportedState = selectorActive
      ? "Zelfconsumptie"
      : auto
        ? this._translateState(status?.state)
        : this._translateState(
          manual?.state || this._state(E.manualMode, "Stop")
        );
    const statusKey = String(status?.state || "").toLowerCase();
    this.$("system-state").textContent = netCode === "off_grid"
      ? "Backup actief"
      : actualPower < -25
      ? "Laden"
      : actualPower > 25
        ? (
          statusKey === "exporting" || statusKey === "terugleveren"
            ? "Terugleveren" : "Zelfconsumptie"
        )
        : reportedState;
    this.$("system-reason").hidden = !auto && netCode !== "off_grid";
    this.$("system-reason").textContent = netCode === "off_grid"
      ? "Planning gepauzeerd"
      : auto ? (status?.attributes?.reden || "Geen reden beschikbaar") : "";
    const outageDuration = this._obj(E.gridOutageDuration);
    this.$("outage-duration").hidden = netCode !== "off_grid";
    this.$("outage-duration-value").textContent =
      outageDuration?.attributes?.weergave || "0 seconden";
    this.$("price-now").textContent = this._fmt(this._num(E.price), 4);
    const actual = this.$("actual-power");
    actual.textContent = `${this._fmt(Math.abs(actualPower), 0)} W`;
    actual.className = actualPower < -5 ? "power-charge" : actualPower > 5 ? "power-discharge" : "";
    this.$("stored-energy").textContent = this._fmt(this._num(E.stored), 2);
    this.$("empty-time").textContent = this._estimatedEmpty();
    const expectedHomePower = Number(this._obj(E.homeForecast)?.state);
    this.$("estimated-home-power").textContent = Number.isFinite(
      expectedHomePower
    ) ? this._fmt(expectedHomePower, 0) : "wordt berekend";
    const powerEntities = this._powerEntities();
    const rawHomePower = this._num(powerEntities.home);
    const configuredHomeOffset = this._num(E.homePowerOffset);
    this.$("home-power").textContent = this._fmt(
      rawHomePower + (Number.isFinite(configuredHomeOffset) ? configuredHomeOffset : 0), 0
    );
    this.$("grid-power").textContent = this._fmt(
      this._num(powerEntities.grid), 0
    );
    E.batteries.forEach((b) => this._patchBatterySummary(b));
    if (this._tab === "overview") {
      this._setSelect("manual-mode", this._state(E.manualMode, "Stop"));
      this._patchManualPower();
      this._patchManualStatus(auto ? status : manual);
      this._renderChargePlanning(auto);
      this._patchOverviewBlocks();
    } else if (this._tab === "batteries") {
      E.batteries.forEach((b) => this._patchBattery(b));
    } else if (this._tab === "graphs") {
      const fingerprint = this._priceChartFingerprint();
      if (fingerprint !== this._graphRenderFingerprint) {
        this._graphRenderFingerprint = fingerprint;
        this._renderPriceChart();
      }
    } else if (this._tab === "results") {
      this._renderResults();
    } else if (this._tab === "settings") {
      const fingerprint = this._settingsFingerprint();
      if (fingerprint !== this._settingsRenderFingerprint) {
        this._settingsRenderFingerprint = fingerprint;
        this._patchEntitySettings();
        this._patchGridExport();
        this._patchEfficiencySettings();
        this._patchFrankSettings();
        this._patchPowerSensorSettings(powerEntities);
        const settingsManager = this._obj(E.settingsManager);
        const settingsStatus = this.$("settings-manager-status");
        if (settingsStatus) {
          settingsStatus.textContent = settingsManager?.attributes?.melding
            || "Instellingenbeheer is nog niet beschikbaar";
        }
        E.settings.forEach(([id], i) => this._patchRange(`setting-${i}`, id, true));
        [["setting-0", 12, 100, 1], ["setting-2", 12, 100, 1]].forEach(
          ([prefix, min, max, step]) => {
            const input = this.$(`${prefix}-input`);
            if (input) Object.assign(input, { min, max, step });
          },
        );
        this._patchRange("grid-offset-manual", E.gridManualOffset, true);
        this._patchRange("home-power-offset", E.homePowerOffset, true);
        this._applySettingsView();
      }
    } else if (this._tab === "status") {
      const fingerprint = this._statusFingerprint();
      if (fingerprint !== this._statusRenderFingerprint) {
        this._statusRenderFingerprint = fingerprint;
        this._renderStatusDetails();
        this._renderAssignedHourBlocks();
        this._renderBatterySelectorStatus();
        this._renderStatusHistory();
        this._renderEfficiencyStatus();
        this._renderAdviceSummaries();
        this._renderPlanningStatus();
      }
    }
  }

  _configuredEntityIds() {
    if (this._configuredEntityIdCache) return this._configuredEntityIdCache;
    const result = new Set();
    const visit = (value) => {
      if (typeof value === "string" && value.includes(".")) result.add(value);
      else if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === "object") Object.values(value).forEach(visit);
    };
    visit(E);
    this._configuredEntityIdCache = [...result];
    return this._configuredEntityIdCache;
  }

  _entityFingerprint(entities, suffix = "") {
    return `${entities.map((entity) => {
      const item = this._obj(entity);
      return `${entity}:${item?.state ?? ""}:${item?.last_updated ?? item?.last_changed ?? ""}`;
    }).join("|")}|${suffix}`;
  }

  _settingsFingerprint() {
    const entities = this._configuredEntityIds().filter((entity) => (
      entity.startsWith("input_") || entity === E.settingsManager
    ));
    const pending = [...this._pendingEntityStates.entries()]
      .map(([entity, item]) => `${entity}:${item.value}`)
      .sort()
      .join("|");
    return this._entityFingerprint(
      entities, `${this._advancedSettings}|${pending}`
    );
  }

  _statusFingerprint() {
    return this._entityFingerprint(
      this._configuredEntityIds(), String(this._historyRevision)
    );
  }

  _patchManualStatus(manual) {
    const stateElement = this.$("manual-state");
    const messageElement = this.$("manual-message");
    const batteriesElement = this.$("manual-battery-statuses");
    if (!stateElement || !messageElement || !batteriesElement) return;

    const auto = this._state(E.auto) === "on";
    if (auto) manual = this._obj(E.status);
    stateElement.textContent = auto
      ? "Auto"
      : (manual?.state || "—");
    const batteryStatuses = Array.isArray(manual?.attributes?.accu_statussen)
      ? manual.attributes.accu_statussen : [];
    const selector = this._obj(E.batterySelectorStatus);
    const selectorActive = auto
      && String(selector?.state || "").toLowerCase() === "actief";
    const selectedNames = new Set(
      (Array.isArray(selector?.attributes?.actieve_accus)
        ? selector.attributes.actieve_accus : [])
        .map((item) => String(
          item?.naam || `Accu ${(Number(item?.index) || 0) + 1}`
        ).toLowerCase())
    );
    batteriesElement.innerHTML = batteryStatuses.map((battery) => {
      const status = String(battery?.status || "ONBEKEND");
      const normalized = status.toLowerCase();
      const tone = (
        normalized === "ok" || normalized.includes("topstop")
        || normalized.includes("balanceren")
      ) ? "ok"
        : (
          normalized.includes("fout")
          || normalized.includes("te oud")
          || normalized.includes("geblokkeerd")
      ) ? "error" : "busy";
      const actualPower = Number(battery?.werkelijk_vermogen_w);
      const batteryName = String(battery?.naam || "Accu");
      const selected = !selectorActive
        || selectedNames.has(batteryName.toLowerCase());
      const mode = selectorActive
        ? (selected ? "Zelfconsumptie" : "Rust")
        : this._translateState(battery?.huidige_status || "Onbekend");
      const powerLabel = selected
        && Number.isFinite(actualPower)
        && Math.abs(actualPower) >= 1
        ? `<strong>${this._fmt(Math.abs(actualPower), 0)} W</strong>` : "";
      return `<div class="manual-battery-row ${tone}" title="${this._escape(battery?.details || "")}">
        <b>${this._escape(batteryName)}</b>
        <div>${powerLabel}<em>${this._escape(mode)}</em><span>${this._escape(status)}</span></div>
      </div>`;
    }).join("");
    const verificationAt = Date.parse(
      manual?.attributes?.verificatie_op || ""
    );
    if (Number.isFinite(verificationAt)) {
      messageElement.closest(".manual-action").hidden = false;
      const remaining = Math.max(
        0, Math.ceil((verificationAt - Date.now()) / 1000)
      );
      messageElement.textContent = remaining > 0
        ? `Opdracht wordt gecontroleerd over ${remaining} ${remaining === 1 ? "seconde" : "seconden"}`
        : "Verificatie wordt uitgevoerd…";
      return;
    }

    const message = manual?.attributes?.actie
      || manual?.attributes?.laatste_actie
      || manual?.attributes?.melding
      || manual?.attributes?.reden
      || (auto ? "Nog geen automatische actie" : "Geen handmatige melding");
    const messageText = String(message);
    const selectorNoChange = auto
      && messageText.includes("self_consumption → self_consumption")
      && messageText.includes("selector → selector");
    messageElement.closest(".manual-action").hidden = selectorNoChange;
    messageElement.textContent = messageText.split(" | ").join("\n");
  }

  _renderChargePlanning(auto) {
    const panel = this.$("charge-planning");
    const rowsElement = this.$("charge-planning-rows");
    const planned = this._obj(E.plannedPower);
    const active = planned?.attributes?.laadblok_actief === true;
    panel.hidden = !(auto && active);
    if (panel.hidden) return;

    const perBattery = Array.isArray(planned.attributes?.per_accu)
      ? planned.attributes.per_accu : [];
    const totalEnergy = Number(planned.attributes?.geplande_energie_kwh);
    const totalPower = Number(planned.state) / 1000;
    const row = (label, energy, power, total = false) => `
      <div class="planning-row ${total ? "total" : ""}">
        <b>${this._escape(label)}</b>
        <span>+${this._fmt(Number(energy), 2)} kWh</span>
        <span>${this._fmt(Number(power), 2)} kW</span>
      </div>`;
    rowsElement.innerHTML = `
      <div class="planning-labels">
        <span></span><span>Energie erbij</span><span>Laadvermogen</span>
      </div>` + row(
      "Totaal", totalEnergy, totalPower, true
    ) + perBattery.map((item) => row(
      item.naam,
      item.energie_kwh,
      Number(item.vermogen_w) / 1000,
    )).join("");
  }

  _wait(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  _patchPlan(entity, valueId, detailId) {
    const obj = this._obj(entity);
    this.$(valueId).textContent = this._formatBlock(obj);
    const price = obj?.attributes?.gemiddelde_prijs;
    this.$(detailId).textContent = price == null ? "Geen gemiddelde prijs" : `Gemiddelde prijs ${this._fmt(Number(price), 4)} €/kWh`;
  }

  _patchOverviewBlocks() {
    const definitions = [
      {
        entity: E.nextCharge, type: "laadblok",
        icon: "mdi:battery-clock", priority: 2,
      },
      {
        entity: E.nextExport, type: "terugleverblok",
        icon: "mdi:transmission-tower-export", priority: 3,
      },
      {
        entity: E.nextDischarge, type: "zelfconsumptieblok",
        icon: "mdi:battery-arrow-down", priority: 1,
      },
    ];
    const candidates = [];
    definitions.forEach((definition) => {
      const obj = this._obj(definition.entity);
      const attrs = obj?.attributes || {};
      let blocks = Array.isArray(attrs.overzichtsblokken)
        ? attrs.overzichtsblokken
        : Array.isArray(attrs.blokken) ? attrs.blokken : [];
      if (!blocks.length && attrs.start && attrs.einde) {
        blocks = [{
          start: attrs.start, einde: attrs.einde,
          gemiddelde_prijs: attrs.gemiddelde_prijs,
        }];
      }
      blocks.forEach((block) => {
        const start = new Date(block.start);
        const end = new Date(block.einde);
        if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return;
        const price = Number(
          block.gemiddelde_prijs ?? attrs.gemiddelde_prijs
        );
        candidates.push({
          ...definition, start, end,
          price: Number.isFinite(price) ? price : null,
        });
      });
    });
    const now = Date.now();
    const current = candidates
      .filter((block) =>
        block.start.getTime() <= now && now < block.end.getTime()
      )
      .sort((left, right) =>
        right.priority - left.priority
        || left.start - right.start
      )[0];
    const nextFrom = current ? current.end.getTime() : now;
    const next = candidates
      .filter((block) => block.start.getTime() >= nextFrom)
      .sort((left, right) =>
        left.start - right.start
        || right.priority - left.priority
      )[0];
    this._patchOverviewBlock("current", current, true);
    this._patchOverviewBlock("next", next, false);
  }

  _patchOverviewBlock(prefix, block, current) {
    const title = this.$(`${prefix}-block-title`);
    const icon = this.$(`${prefix}-block-icon`);
    const value = this.$(`${prefix}-block`);
    const price = this.$(`${prefix}-block-price`);
    if (!title || !icon || !value || !price) return;
    if (!block) {
      title.textContent = current ? "Geen huidig blok" : "Geen volgend blok";
      icon.setAttribute("icon", current ? "mdi:clock-outline" : "mdi:clock-fast");
      value.textContent = current ? "Geen actief blok" : "Geen gepland blok";
      price.textContent = "Geen gemiddelde prijs";
      return;
    }
    title.textContent = `${current ? "Huidig" : "Volgend"} ${block.type}`;
    icon.setAttribute("icon", block.icon);
    value.textContent = this._formatBlock({ attributes: {
      start: block.start.toISOString(), einde: block.end.toISOString(),
    } });
    price.textContent = block.price == null
      ? "Geen gemiddelde prijs"
      : `Gemiddelde prijs ${this._fmt(block.price, 4)} €/kWh`;
  }

  _formatBlock(obj) {
    const startValue = obj?.attributes?.start;
    const endValue = obj?.attributes?.einde;
    if (!startValue || !endValue || startValue === "geen" || endValue === "geen") {
      return obj?.state || "geen";
    }
    const start = new Date(startValue);
    const end = new Date(endValue);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
      return obj?.state || "geen";
    }
    const month = start.toLocaleDateString("nl-NL", { month: "short" }).replace(".", "");
    const time = (date) => date.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
    return `${start.getDate()} ${month} ${time(start)} - ${time(end)} uur`;
  }

  _patchBattery(b) {
    this._patchBatterySummary(b);
    const soc = this._num(b.soc);
    const capacity = this._num(b.capacity);
    this._patchRing(`battery-${b.n}`, soc, `${this._fmt(capacity * soc / 100, 2)} / ${this._fmt(capacity, 2)} kWh`);
    this.$(`b${b.n}-mode`).textContent = this._batteryMode(b);
    const power = this._actualPower(b);
    const powerEl = this.$(`b${b.n}-power`);
    powerEl.textContent = `${this._fmt(Math.abs(power), 0)} W`;
    powerEl.className = `actual-power ${power < -5 ? "power-charge" : power > 5 ? "power-discharge" : ""}`;
    this.$(`b${b.n}-inverter-summary`).textContent = this._translateInverterState(
      this._state(b.inverter)
    );
    [["maxcharge", b.maxCharge], ["maxdischarge", b.maxDischarge], ["charge", b.charge], ["discharge", b.discharge]].forEach(([key, entity]) => this._patchRange(`b${b.n}-${key}`, entity, true));
    this._patchSwitch(`b${b.n}-rs485`, b.rs485);
    this._patchSwitch(`b${b.n}-backup`, b.backup);
    this._patchEntitySelect(`b${b.n}-work`, b.work);
    this._patchEntitySelect(`b${b.n}-force`, b.force);
    const values = {
      cycles: this._state(b.cycles), temperature: this._displayState(b.temperature), inverter: this._translateInverterState(this._state(b.inverter)), voltage: this._displayState(b.voltage),
      cellmax: this._displayState(b.cellMax), cellmin: this._displayState(b.cellMin), celldelta: this._cellDelta(b), deltaavg: this._displayState(b.deltaAvg),
      balancing: this._state(b.balancing) === "on" ? "Actief" : "Inactief", balancestatus: this._state(b.balanceStatus), balancelast: this._state(b.balanceLast),
      cyclescalc: this._state(b.cyclesCalc), ip: this._batteryIp(b),
      modbus: this._state(b.modbusConnected) === "on" ? "Verbonden" : (
        this._state(b.modbusConnected) === "off" ? "Niet verbonden" : "—"
      ),
      modbusaddress: this._displayState(b.modbusAddress),
      firmware: this._displayState(b.firmware),
      bmsversion: this._displayState(b.bmsVersion),
      emsversion: this._displayState(b.emsVersion),
      vmsversion: this._displayState(b.vmsVersion),
      communicationfirmware: this._displayState(b.communicationFirmware),
      acvoltage: this._displayState(b.acVoltage),
      accurrent: this._displayState(b.acCurrent),
      acpower: this._displayState(b.acPower),
      acfrequency: this._displayState(b.acFrequency),
      offgridvoltage: this._displayState(b.offGridVoltage),
      offgridcurrent: this._displayState(b.offGridCurrent),
      offgridpower: this._displayState(b.offGridPower),
      batterycurrent: this._displayState(b.batteryCurrent),
      batterypower: this._displayState(b.batteryPower),
    };
    Object.entries(values).forEach(([key, value]) => { const el = this.$(`b${b.n}-${key}`); if (el) el.textContent = value || "—"; });
    const resetButton = this.$(`b${b.n}-device-reset`);
    if (resetButton) {
      resetButton.disabled = !this._obj(b.deviceReset);
      resetButton.title = resetButton.disabled
        ? "Reset-entiteit is niet beschikbaar"
        : `Accu ${b.n} veilig opnieuw opstarten`;
    }
    this._patchBatteryCells(b);
  }

  _patchBatteryCells(b) {
    const sharedScale = this._sharedCellDeviationScale();
    const values = b.cells.map((entity) => this._num(entity));
    const available = values
      .map((value, index) => ({ value, index }))
      .filter((item) => Number.isFinite(item.value));
    const status = this.$(`b${b.n}-cells-status`);
    const averageEl = this.$(`b${b.n}-cells-average`);
    const deltaEl = this.$(`b${b.n}-cells-delta`);
    const minMaxEl = this.$(`b${b.n}-cells-minmax`);
    const scaleEl = this.$(`b${b.n}-cells-scale`);
    if (scaleEl) {
      scaleEl.textContent = Number.isFinite(sharedScale.maximumMv)
        ? `Gedeelde schaal \u00b1${this._fmt(sharedScale.maximumMv, 0)} mV \u00b7 midden = gemiddelde`
        : "Gedeelde afwijkingsschaal voor alle accuâ€™s";
    }
    if (!available.length) {
      status.textContent = "Geen celdata";
      status.className = "cell-status unavailable";
      averageEl.textContent = "—";
      deltaEl.textContent = "—";
      minMaxEl.textContent = "—";
    } else {
      const voltages = available.map((item) => item.value);
      const minimum = Math.min(...voltages);
      const maximum = Math.max(...voltages);
      const average = voltages.reduce((sum, value) => sum + value, 0) / voltages.length;
      const deltaMv = Math.max(0, (maximum - minimum) * 1000);
      const level = deltaMv <= 15 ? "good" : deltaMv <= 30 ? "warn" : "attention";
      status.textContent = `${available.length}/16 live`;
      status.className = `cell-status ${level}`;
      averageEl.textContent = `${this._fmt(average, 3)} V`;
      deltaEl.textContent = `${this._fmt(deltaMv, 0)} mV`;
      minMaxEl.textContent = `${this._fmt(minimum, 3)} / ${this._fmt(maximum, 3)} V`;
    }

    const finiteValues = available.map((item) => item.value);
    const minimum = finiteValues.length ? Math.min(...finiteValues) : NaN;
    const maximum = finiteValues.length ? Math.max(...finiteValues) : NaN;
    const average = finiteValues.length
      ? finiteValues.reduce((sum, value) => sum + value, 0) / finiteValues.length
      : NaN;
    values.forEach((value, index) => {
      const column = this.$(`b${b.n}-cell-${index + 1}`);
      const fill = this.$(`b${b.n}-cell-${index + 1}-fill`);
      const label = this.$(`b${b.n}-cell-${index + 1}-value`);
      const availableValue = Number.isFinite(value);
      const height = availableValue
        ? Math.max(4, Math.min(100, 50 + (
          (value - average) * 1000 / sharedScale.maximumMv
        ) * 50))
        : 0;
      fill.style.height = `${height}%`;
      label.textContent = availableValue ? this._fmt(value, 3) : "—";
      column.className = "cell-column";
      if (!availableValue) column.classList.add("unavailable");
      if (availableValue && value === minimum) column.classList.add("minimum");
      if (availableValue && value === maximum) column.classList.add("maximum");
      column.title = availableValue
        ? `Cel ${index + 1}: ${this._fmt(value, 3)} V (${this._fmt((value - average) * 1000, 1)} mV t.o.v. gemiddelde)`
        : `Cel ${index + 1}: niet beschikbaar`;
    });
  }

  _sharedCellDeviationScale() {
    const deviationsMv = E.batteries.flatMap((battery) => {
      const values = battery.cells
        .map((entity) => this._num(entity))
        .filter((value) => Number.isFinite(value));
      if (!values.length) return [];
      const average = values.reduce((sum, value) => sum + value, 0) / values.length;
      return values.map((value) => Math.abs(value - average) * 1000);
    });
    if (!deviationsMv.length) return { maximumMv: 10 };
    // Eén relatieve schaal voor alle accu's. Minstens ±6 mV voorkomt nerveus
    // zoomen; 25% marge houdt de hoogste en laagste cel uit de grafiekrand.
    const observed = Math.max(...deviationsMv);
    return { maximumMv: Math.max(6, Math.ceil(observed * 1.25)) };
  }

  _patchBatterySummary(b) {
    this._patchRing(`mini-${b.n}`, this._num(b.soc), "");
    const mode = this.$(`mini-mode-${b.n}`);
    if (!mode) return;
    const health = this._batteryHealth(b);
    mode.textContent = health?.label || this._batteryMode(b);
    mode.classList.toggle("battery-health-warning", Boolean(health));
    mode.title = health?.title || "";
  }

  _batteryHealth(b) {
    const items = this._obj(E.batteryHealth)?.attributes?.accus;
    if (!Array.isArray(items)) return null;
    const item = items.find((entry) => Number(entry?.index) === b.n - 1);
    const status = String(item?.status || "healthy").toLowerCase();
    if (status === "disabled") {
      return {
        label: "Uitgeschakeld · Handmatig",
        title: item?.reden || "Handmatig uitgeschakeld in de instellingen",
      };
    }
    if (status === "quarantined") {
      const retryAt = new Date(item?.opnieuw_proberen_op || "");
      const remaining = Number.isNaN(retryAt.getTime())
        ? Number(item?.resterend_seconden || 0)
        : Math.max(0, Math.ceil((retryAt.getTime() - Date.now()) / 1000));
      return {
        label: `Tijdelijk buiten gebruik · ${this._compactDuration(remaining)}`,
        title: item?.reden || "Deze accu krijgt tijdelijk rust na mislukte herstelpogingen",
      };
    }
    return null;
  }

  _compactDuration(seconds) {
    const totalMinutes = Math.max(0, Math.ceil(Number(seconds || 0) / 60));
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    if (hours > 0) return `${hours} u ${String(minutes).padStart(2, "0")} min`;
    return `${minutes} min`;
  }

  _patchSwitch(prefix, entity) {
    const on = this._state(entity) === "on";
    this.$(`${prefix}-toggle`).classList.toggle("on", on);
    this.$(`${prefix}-label`).textContent = on ? "Aan" : "Uit";
  }

  _patchEntitySelect(id, entity) {
    const el = this.$(id);
    const obj = this._obj(entity);
    if (!el || !obj) return;
    const options = obj.attributes?.options || [];
    if (el.dataset.options !== options.join("|")) {
      el.innerHTML = options.map((option) => `<option value="${this._escape(option)}">${this._escape(this._optionLabel(option))}</option>`).join("");
      el.dataset.options = options.join("|");
    }
    this._syncControlValue(el, obj.state);
  }

  _optionLabel(value) {
    return ({ manual: "Handmatig", handmatig: "Handmatig", anti_feed: "Eigen verbruik", "anti feed": "Eigen verbruik", trade: "AI-optimalisatie", ai_optimization: "AI-optimalisatie", ai_optimisation: "AI-optimalisatie", stop: "Stop", none: "Stop", charge: "Laden", charging: "Laden", discharge: "Ontladen", discharging: "Ontladen" })[String(value).toLowerCase()] || value;
  }

  _translateInverterState(value) {
    const key = String(value || "").trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
    const exact = ({
      charge: "Laden", charging: "Laden", grid_charge: "Laden vanaf het net",
      discharge: "Ontladen", discharging: "Ontladen",
      standby: "Stand-by", idle: "Rust", stop: "Gestopt", stopped: "Gestopt",
      sleep: "Slaapstand", sleeping: "Slaapstand", off: "Uit", on: "Aan",
      self_consumption: "Zelfconsumptie", anti_feed: "Eigen verbruik",
      backup: "Back-up", updating: "Bijwerken", bypass: "Doorvoer",
      grid_tied: "Netgekoppeld", normal: "Normaal", offline: "Niet verbonden",
      fault: "Storing", error: "Fout", warning: "Waarschuwing",
      unknown: "Onbekend", unavailable: "Niet beschikbaar",
    })[key];
    if (exact) return exact;
    if (key.includes("backup")) return "Back-up";
    if (key.includes("ota") || key.includes("upgrade") || key.includes("update")) return "Bijwerken";
    if (key.includes("discharge")) return "Ontladen";
    if (key.includes("charge")) return "Laden";
    if (key.includes("standby")) return "Stand-by";
    if (key.includes("sleep")) return "Slaapstand";
    if (key.includes("bypass")) return "Doorvoer";
    if (key.includes("fault")) return "Storing";
    if (key.includes("offline")) return "Niet verbonden";
    return this._translateState(value);
  }

  _cellDelta(b) {
    const direct = this._num(b.cellDelta);
    if (Number.isFinite(direct)) return `${this._fmt(direct, 0)} mV`;
    const max = this._num(b.cellMax), min = this._num(b.cellMin);
    return Number.isFinite(max) && Number.isFinite(min) ? `${this._fmt((max - min) * 1000, 0)} mV` : "—";
  }

  _batteryIp(b) {
    if (this._batteryIps[b.n]) return this._batteryIps[b.n];
    const direct = this._state(b.ip, "");
    if (direct) return direct;
    for (const id of [b.soc, b.actual, b.inverter]) {
      const attrs = this._obj(id)?.attributes || {};
      const ip = attrs.ip_address || attrs.ip || attrs.host;
      if (ip) return ip;
    }
    return "—";
  }

  _displayState(entity) {
    const obj = this._obj(entity);
    if (!obj || ["unknown", "unavailable"].includes(obj.state)) return "—";
    return `${obj.state}${obj.attributes?.unit_of_measurement ? ` ${obj.attributes.unit_of_measurement}` : ""}`;
  }

  _actualPower(b) {
    const measured = this._num(b.actual);
    if (Number.isFinite(measured)) return measured;
    const charge = this._num(b.charge);
    const discharge = this._num(b.discharge);
    return (Number.isFinite(discharge) ? discharge : 0) - (Number.isFinite(charge) ? charge : 0);
  }

  _estimatedEmpty() {
    const expectedPower = Number(this._obj(E.homeForecast)?.state);
    if (!(expectedPower > 5)) return "wordt berekend";
    const minimumSoc = Math.max(0, this._num(E.minimumSoc));
    const usableEnergy = E.batteries.reduce((sum, battery) => {
      const capacity = this._num(battery.capacity);
      const soc = this._num(battery.soc);
      if (!Number.isFinite(capacity) || !Number.isFinite(soc)) return sum;
      return sum + capacity * Math.max(0, soc - minimumSoc) / 100
        * this._directionEfficiency("ontladen", battery);
    }, 0);
    const minutes = Math.round(
      usableEnergy / (expectedPower / 1000) * 60
    );
    if (!Number.isFinite(minutes) || minutes < 0) {
      return "wordt berekend";
    }
    if (minutes === 0) return "0 min";
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return hours ? `${hours} u ${String(rest).padStart(2, "0")} min` : `${rest} min`;
  }

  _powerEntities() {
    const cfg = this._panel?.config || {};
    const helperHome = this._state(E.homeSensorHelper, "");
    const helperFastGrid = this._state(E.gridSensorHelper, "");
    const helperDisplayGrid = this._state(E.gridDisplaySensorHelper, "");
    return {
      home: (helperHome.startsWith("sensor.") && helperHome) || localStorage.getItem("openems-home-sensor") || cfg.home_power_entity || "sensor.meterkast_shellycthome_vermogen",
      grid: (helperDisplayGrid.startsWith("sensor.") && helperDisplayGrid) || localStorage.getItem("openems-display-grid-sensor") || "sensor.p1_netto_vermogen",
      fastGrid: (helperFastGrid.startsWith("sensor.") && helperFastGrid) || localStorage.getItem("openems-grid-sensor") || cfg.grid_power_entity || "sensor.meterkast_shellyctgrid_vermogen",
    };
  }

  _patchPowerSensorSettings(entities) {
    const options = Object.values(this._hass.states).filter((obj) => obj.entity_id.startsWith("sensor.") && (obj.attributes?.device_class === "power" || obj.attributes?.unit_of_measurement === "W")).sort((a, b) => (a.attributes?.friendly_name || a.entity_id).localeCompare(b.attributes?.friendly_name || b.entity_id, "nl"));
    const list = this.$("power-sensors");
    if (list && list.dataset.count !== String(options.length)) {
      list.innerHTML = options.map((obj) => `<option value="${this._escape(obj.entity_id)}">${this._escape(obj.attributes?.friendly_name || obj.entity_id)}</option>`).join("");
      list.dataset.count = String(options.length);
    }
    this._syncControlValue(this.$("home-sensor-input"), entities.home);
    this._syncControlValue(this.$("grid-sensor-input"), entities.fastGrid);
    this._syncControlValue(this.$("grid-display-sensor-input"), entities.grid);
  }

  _patchEntitySettings() {
    const propertyMap = {
      soc: "soc", capacity: "capacity", actual_power: "actual",
      max_charge_power: "maxCharge", max_discharge_power: "maxDischarge",
      charge_power: "charge", discharge_power: "discharge",
      rs485_control: "rs485", work_mode: "work", force_mode: "force",
      max_cell_voltage: "cellMax", min_cell_voltage: "cellMin",
      temperature: "temperature", cycles: "cycles",
    };
    E.batteries.forEach((battery) => E.batteryEntityFields.forEach(([field]) => {
      const helper = battery.helpers[field];
      const configured = this._state(helper, "");
      const input = this.$(`entity-b${battery.n}-${field}`);
      this._syncControlValue(input, configured);
      if (configured && propertyMap[field]) battery[propertyMap[field]] = configured;
    }));

    const allEntities = this.$("all-entities");
    const entityIds = Object.keys(this._hass.states).sort();
    if (allEntities && allEntities.dataset.count !== String(entityIds.length)) {
      allEntities.innerHTML = entityIds.map((id) => `<option value="${this._escape(id)}"></option>`).join("");
      allEntities.dataset.count = String(entityIds.length);
    }
    const solar = this._state(E.solarSensorHelper, "");
    this._syncControlValue(this.$("solar-sensor-input"), solar);
    this._setSelect("grid-sign-select", this._state(E.gridPowerSign, "Positief is afname"));
    this._setSelect("grid-source-select", this._state(E.gridSensorSource, "CT"));
    this._patchGridOffsetSettings();

    const mode = this._state(E.selfConsumptionMode, "Marstek");
    this.shadowRoot.querySelectorAll("[data-selfcons]").forEach((button) => button.classList.toggle("active", button.dataset.selfcons === mode));
    const openems = mode === "OpenEMS";
    const fallbackCard = this.$("fallback-setting");
    const fallbackButton = this.$("fallback-toggle");
    fallbackCard.classList.toggle("frozen", !openems);
    fallbackButton.disabled = !openems;
    fallbackButton.classList.toggle("on", this._state(E.selfConsumptionFallback) === "on");
    this._setSelect("discharge-resolution-select", this._state(E.dischargeResolution, "60 minuten"));
    const distribution = this._state(
      E.batteryDistribution, "Alle accu’s verdelen"
    );
    this._setSelect("battery-distribution-select", distribution);
    const distributionStatus = this.$("battery-distribution-status");
    if (distributionStatus) {
      distributionStatus.textContent = (
        distribution === "Efficiënte accuselector (aanbevolen)"
      )
        ? openems
          ? "OpenEMS regelt het vermogen; de selector kiest de actieve accu’s."
          : "Marstek regelt het vermogen; OpenEMS kiest alleen de actieve accu’s."
        : "Bestaande verdeling over alle beschikbare accu’s.";
    }
    const status = this._obj(E.selfConsumptionStatus);
    this.$("selfcons-status").textContent = status
      ? `${status.state}: ${status.attributes?.melding || ""}`
      : `${mode}-regeling geselecteerd`;
  }

  _calibrationLabel(status) {
    const labels = {
      opstarten: "gestart",
      opnieuw_gestart: "gestart, meten duurt ongeveer 1 minuut",
      rust: "wachten",
      wachten_op_meetduur: "meten (stabiele periode nodig)",
      snelle_sensor_niet_stabiel: "wacht tot de meting rustig is",
      referentie_te_ver_van_nulpunt: "wacht tot het netvermogen dicht bij 0 W is",
      verschil_buiten_veilige_grens: "verschil te groot; controleer de sensoren",
      meer_geldige_metingen_nodig: "meer metingen verzamelen",
      offsetmetingen_niet_eenduidig: "metingen zijn nog niet eenduidig",
      accu_actief: "wacht tot alle accu's in rust zijn",
      sensor_niet_beschikbaar: "een sensor is niet beschikbaar",
      tekenrichting_onjuist: "tekenrichting onjuist; controleer de tekeninstelling",
      betrouwbaar: "bijna klaar",
      kalibratie_klaar: "klaar",
      kalibratie_mislukt: "mislukt",
    };
    return labels[status] || status || "bezig";
  }

  _pressCalibration(key, buttonEntity) {
    this._calibrationPressedAt = this._calibrationPressedAt || {};
    this._calibrationPressedAt[key] = Date.now();
    __openemsCallService(this._hass, "input_button", "press", { entity_id: buttonEntity });
    this._patchCalibrationUi();
    if (!this._calibrationTimer) {
      this._calibrationTimer = setTimeout(() => {
        this._calibrationTimer = null;
        this._patchCalibrationUi();
      }, 9000);
    }
  }

  _patchCalibrationUi() {
    const pressedAt = this._calibrationPressedAt || {};
    const apply = (key, statusEntity, buttonIds, textId) => {
      const attrs = this._obj(statusEntity)?.attributes || {};
      const pending = Date.now() - (pressedAt[key] || 0) < 8000;
      const running = attrs.kalibratie_actief === true || pending;
      buttonIds.forEach((id) => {
        const button = this.$(id);
        if (!button) return;
        button.disabled = running;
        const label = button.querySelector(".cal-label");
        if (label) label.textContent = running ? "Bezig met kalibreren…" : label.dataset.idle;
      });
      const text = this.$(textId);
      if (!text) return;
      if (running) {
        text.textContent = attrs.kalibratie_actief === true
          ? `Kalibreren… ${this._calibrationLabel(attrs.kalibratiestatus)}`
          : "Kalibratie gestart… dit duurt ongeveer 1 minuut";
      } else if (attrs.kalibratie_resultaat === "mislukt") {
        text.textContent = "Kalibratie mislukt; de vorige offset blijft gelden"
          + (attrs.laatste_kalibratie ? ` · laatste gelukte: ${attrs.laatste_kalibratie}` : "");
      } else if (attrs.laatste_kalibratie) {
        text.textContent = `${attrs.kalibratie_resultaat === "klaar" ? "Kalibratie klaar" : "Laatste kalibratie"}: ${attrs.laatste_kalibratie}`;
      } else {
        text.textContent = "Nog geen kalibratie bekend";
      }
    };
    apply("grid", E.gridOffsetStatus, ["grid-offset-calibrate-settings", "grid-offset-calibrate"], "grid-offset-last");
    apply("home", E.homeOffsetStatus, ["home-offset-calibrate"], "home-offset-last");
  }

  _patchGridOffsetSettings() {
    const referenceInput = this.$("grid-reference-input");
    this._syncControlValue(
      referenceInput,
      this._state(E.gridReferenceHelper, "sensor.p1_netto_vermogen")
    );
    const manualInput = this.$("grid-offset-manual-input");
    this._syncControlValue(
      manualInput, this._state(E.gridManualOffset, "0")
    );
    const status = this._obj(E.gridOffsetStatus);
    const attrs = status?.attributes || {};
    this._patchCalibrationUi();
    const statusCard = this.$("grid-offset-status-card");
    if (statusCard) {
      statusCard.hidden = !(
        this._state(E.selfConsumptionMode, "Marstek") === "OpenEMS"
        && this._state(E.gridSensorSource, "CT") === "CT"
      );
    }
    const attrNumber = (name) => (
      attrs[name] == null ? Number.NaN : Number(attrs[name])
    );
    this.$("grid-offset-fast").textContent =
      `${this._fmt(attrNumber("snelle_sensor_raw_w"), 0)} W`;
    this.$("grid-offset-reference").textContent =
      `${this._fmt(attrNumber("p1_referentie_w"), 0)} W`;
    this.$("grid-offset-difference").textContent =
      `${this._fmt(attrNumber("actueel_verschil_w"), 0)} W`;
    this.$("grid-offset-automatic").textContent =
      `${this._fmt(attrNumber("automatische_offset_w"), 0)} W`;
    this.$("grid-offset-active").textContent =
      `${this._fmt(attrNumber("actieve_offset_w"), 0)} W`;
    this.$("grid-offset-corrected").textContent =
      `${this._fmt(attrNumber("gecorrigeerd_netvermogen_w"), 0)} W`;
    const chip = this.$("grid-offset-chip");
    chip.textContent = this._state(E.gridOffsetMode, "Automatisch");
    chip.className = `chip ${status?.state === "fout" ? "warn" : "good"}`;
    const labels = {
      opstarten: "Kalibratie wordt gestart",
      opnieuw_gestart: "Nieuwe kalibratie gestart",
      wachten_op_meetduur: "Wachten op een stabiele meetperiode",
      snelle_sensor_niet_stabiel: "Snelle sensor verandert te sterk; deze periode telt niet mee",
      referentie_te_ver_van_nulpunt: "P1 is te ver van 0 W; wachten op een geschikter moment",
      verschil_buiten_veilige_grens: "Verschil is groter dan de veilige grens van 300 W",
      meer_geldige_metingen_nodig: "Geldige stabiele metingen worden verzameld",
      offsetmetingen_niet_eenduidig: "Metingen zijn nog niet eenduidig",
      betrouwbaar: "Automatische offset is betrouwbaar berekend",
      sensor_niet_beschikbaar: "Een van de vermogenssensoren is niet beschikbaar",
      tekenrichting_onjuist: "Snelle sensor en P1 hebben een tegengesteld teken; controleer de tekeninstelling",
    };
    const calibration = labels[attrs.kalibratiestatus]
      || attrs.kalibratiestatus || "Kalibratiegegevens worden geladen";
    const confidence = Number(attrs.betrouwbaarheid_pct);
    const samples = Number(attrs.geldige_metingen);
    this.$("grid-offset-message").textContent = `${calibration} · ${
      Number.isFinite(samples) ? samples : 0
    } geldige metingen · betrouwbaarheid ${
      Number.isFinite(confidence) ? confidence : 0
    }%`;
  }

  _batteryMode(b) {
    const actual = this._obj(b.actual);
    if (!actual || ["unknown", "unavailable", ""].includes(String(actual.state).toLowerCase())) {
      return "Onbekend";
    }
    const power = Number(actual.state);
    if (!Number.isFinite(power)) return "Onbekend";
    if (power < -25) return "Laden";
    if (power > 25) return "Ontladen";
    return "Stand-by";
  }

  _patchRing(key, value, sub) {
    const v = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
    const color = v < 20 ? "#fb6b6b" : v < 50 ? "#f6c85f" : "#55d996";
    const fg = this.$(`ring-${key}-fg`);
    fg.style.strokeDashoffset = String(100 - v);
    fg.style.stroke = color;
    fg.style.filter = `drop-shadow(0 0 7px ${color})`;
    this.$(`ring-${key}-value`).textContent = Number.isFinite(value) ? `${Math.round(value)}%` : "—%";
    const subEl = this.$(`ring-${key}-sub`);
    if (subEl) subEl.textContent = sub;
  }

  _manualPowerEntity(mode) {
    return (mode || this._state(E.manualMode, "Stop")) === "Ontladen" ? E.manualDischarge : E.manualCharge;
  }

  _patchManualPower(modeOverride) {
    const mode = modeOverride || this._state(E.manualMode, "Stop");
    const active = mode === "Laden" || mode === "Ontladen";
    const choosing = active || mode === "Zelfconsumptie";
    this.$("manual-power-block").hidden = !active;
    this.$("manual-distribution-block").hidden = !active;
    this.$("manual-accus").hidden = !choosing;
    if (!choosing) return;
    if (active) {
    const label = this.$("manual-power-input").closest("label").firstElementChild.firstChild;
    if (label) label.textContent = mode === "Ontladen" ? "Ontlaadvermogen totaal" : "Laadvermogen totaal";
    this._patchRange("manual-power", this._manualPowerEntity(mode), true);
    }
    E.batteries.forEach((b, i) => {
      const box = this.$(`manual-accu-${b.n}`);
      const off = this._state(b.disabled) === "on";
      box.disabled = off;
      box.checked = !off && this._state(E.manualExcluded[i]) !== "on";
      box.closest("label").classList.toggle("off", off);
    });
    if (active) this._setSelect("manual-distribution", this._state(E.manualDistribution, "Gelijk"));
  }

  _patchRange(prefix, entity, entityLimits = false) {
    const obj = this._obj(entity);
    if (!obj) return;
    const input = this.$(`${prefix}-input`);
    if (entityLimits) {
      input.min = obj.attributes.min ?? input.min;
      input.max = obj.attributes.max ?? input.max;
      input.step = obj.attributes.step ?? input.step;
    }
    this._syncControlValue(input, this._state(entity, obj.state));
    this.$(`${prefix}-value`).textContent = this._fmt(Number(input.value), Number(obj.attributes.step) < 0.1 ? 4 : Number(obj.attributes.step) < 1 ? 2 : 0);
  }

  _renderPriceChart() {
    const data = this._priceData();
    if (!data.length) { this.$("price-chart").innerHTML = '<div class="empty">Geen Frank-prijzen beschikbaar</div>'; return; }
    const w = 1000, h = 270, left = 62, right = 12, topPad = 28, bottom = 40;
    const axis = this._priceAxis(data.map((p) => p.value), h, topPad, bottom);
    const bw = (w - left - right) / data.length;
    const zeroY = axis.y(0);
    const now = new Date();
    const stepMs = data[0].stepMs || 3600000;
    const barGap = Math.min(2.2, Math.max(0.8, bw * 0.08));
    const visualBarWidth = Math.max(2, bw - barGap);
    const cornerRadius = Math.min(2, visualBarWidth / 3);
    const bars = data.map((p, i) => {
      const y = axis.y(p.value);
      const top = Math.min(y, zeroY), height = Math.max(2, Math.abs(zeroY - y));
      const classification = this._priceClassificationAt(p.time, stepMs);
      const active = now >= p.time
        && now < new Date(p.time.getTime() + stepMs);
      return `<rect data-price-index="${i}" class="${classification.className}${active ? " current-price-bar" : ""}${p.forecast ? ` forecast-bar${{ laden: " forecast-charge", zelf: " forecast-self", standby: " forecast-standby" }[p.mode] || ""}` : ""}" x="${left + i * bw + barGap / 2}" y="${top}" width="${visualBarWidth}" height="${height}" rx="${cornerRadius}" ry="${cornerRadius}" role="button" aria-label="${this._escape(`${p.time.toLocaleString("nl-NL")} · All-inprijs ${p.value.toFixed(4)} €/kWh · ${classification.label}`)}"></rect>`;
    }).join("");
    const labels = this._priceTimeLabels(data, left, bw, h - 12);
    const rangeStart = data[0].time.getTime();
    const rangeEnd = data[data.length - 1].time.getTime() + stepMs;
    const nowX = left + (
      (now.getTime() - rangeStart) / (rangeEnd - rangeStart)
    ) * (w - left - right);
    const nowMarker = nowX >= left && nowX <= w - right
      ? `<g class="now-marker"><line x1="${nowX}" x2="${nowX}" y1="16" y2="${h - bottom}"/><rect x="${Math.max(left, Math.min(w - right - 48, nowX - 24))}" y="1" width="48" height="20" rx="7"/><text x="${Math.max(left + 24, Math.min(w - right - 24, nowX))}" y="15">NU</text></g>`
      : "";
    const tomorrow = new Date(now);
    tomorrow.setDate(now.getDate() + 1);
    const hasTomorrow = data.some((point) =>
      point.time.getFullYear() === tomorrow.getFullYear()
      && point.time.getMonth() === tomorrow.getMonth()
      && point.time.getDate() === tomorrow.getDate()
    );
    this.$("price-chart").innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${axis.svg(left, w - right)}<line class="zero" x1="${left}" x2="${w-right}" y1="${zeroY}" y2="${zeroY}"/>${bars}${nowMarker}<g class="axis-labels">${labels}</g></svg><div id="price-tooltip" class="price-tooltip" hidden></div><div class="chart-note">${data.some((p) => p.forecast) ? '<span class="forecast-dot">Voorspeld (gestippeld; kleur = indicatief plan)</span>' : ""}<span class="charge-window-dot">Laden mogelijk</span><span class="charge-dot">Laden gepland / gemeten</span><span class="discharge-window-dot">Zelfconsumptie mogelijk</span><span class="discharge-dot">Zelfconsumptie gepland / gemeten</span><span class="export-window-dot">Terugleveren toegestaan</span><span class="export-plan-dot">Terugleveren gepland / gemeten</span><span class="standby-dot">Stand-by</span><span class="resolution-dot">${this._priceResolutionLabel(stepMs)}</span>${hasTomorrow ? "" : '<span class="source-warning">Morgenprijzen nog niet gepubliceerd</span>'}</div>`;
    this.shadowRoot.querySelectorAll("[data-price-index]").forEach((bar) => {
      const point = data[Number(bar.dataset.priceIndex)];
      bar.addEventListener("pointerenter", () => this._showPriceTooltip(point, bar));
      bar.addEventListener("focus", () => this._showPriceTooltip(point, bar));
      bar.addEventListener("click", () => this._showPriceTooltip(point, bar, true));
      bar.addEventListener("pointerleave", (event) => {
        if (event.pointerType !== "touch") this._hidePriceTooltip();
      });
      bar.setAttribute("tabindex", "0");
    });
    const actualData = data.filter((p) => !p.forecast);
    this._renderPriceSummary(actualData);
    this._renderMarketChart(actualData);
  }

  _priceChartFingerprint() {
    const frank = this._frankEntities();
    const entities = [
      ...Object.values(frank), E.price, E.nextCharge, E.nextDischarge,
      E.nextExport, E.status, E.manualStatus, E.switchStatus, E.exportStatus,
      E.priceForecastStatus, E.priceForecastPlan,
      "number.openems_frank_terugleververgoeding",
      E.gridExportAllowed,
      E.exportMode,
    ];
    const states = [...new Set(entities.filter(Boolean))].map((entity) => {
      const item = this._obj(entity);
      return `${entity}:${item?.state ?? ""}:${item?.last_updated ?? item?.last_changed ?? ""}`;
    });
    return `${states.join("|")}|${this._historyRevision}|${Boolean(this._priceHistoryLoading)}`;
  }

  _priceData() {
    const frankEntities = this._frankEntities();
    const allIn = this._priceRows(frankEntities.allIn, ["all_in_price", "allin", "price"]);
    if (!allIn.length) return [];
    const market = this._priceRows(frankEntities.market, ["market_price", "marketPrice", "energy_price", "spot_price", "price", "value"]);
    const embeddedMarket = this._priceRows(frankEntities.allIn, ["market_price", "marketPrice", "energy_price", "spot_price"]);
    const stepMs = this._selectedPriceStepMs(allIn);
    const rows = this._aggregatePriceRows(allIn, stepMs);
    const maps = [market, embeddedMarket].map((items) =>
      new Map(this._aggregatePriceRows(items, stepMs).map((item) => [item.time.getTime(), item.value]))
    );
    const returnFee = this._num("number.openems_frank_terugleververgoeding");
    const limit = Math.max(1, Math.round(48 * 3600000 / stepMs));
    const todayStart = new Date();
    todayStart.setHours(0, 0, 0, 0);
    const dayAfterTomorrow = new Date(todayStart);
    dayAfterTomorrow.setDate(dayAfterTomorrow.getDate() + 2);
    const frankRows = rows.filter((row) =>
      row.time >= todayStart && row.time < dayAfterTomorrow
    ).slice(0, limit).map((row) => ({
      time: row.time,
      value: row.value,
      market: maps[0].get(row.time.getTime()) ?? maps[1].get(row.time.getTime()),
      marketPlus: (() => {
        const bare = maps[0].get(row.time.getTime()) ?? maps[1].get(row.time.getTime());
        return Number.isFinite(bare)
          ? bare + (Number.isFinite(returnFee) ? returnFee : 0.0182)
          : undefined;
      })(),
      stepMs,
    }));
    // Frank is leidend: voorspelde uren alleen ná de laatste bekende Frank-prijs.
    const lastFrank = rows.length ? rows[rows.length - 1].time.getTime() + stepMs : 0;
    this._lastFrankEndMs = lastFrank;
    this._frankPriceByTime = new Map(frankRows.map((row) => [row.time.getTime(), row.value]));
    const horizon = todayStart.getTime() + 3 * 86400000;
    return frankRows.concat(this._forecastRows(stepMs, lastFrank, horizon));
  }

  _priceRows(entity, aliases) {
    const prices = this._obj(entity)?.attributes?.prices;
    if (!Array.isArray(prices)) return [];
    return prices.map((item) => {
      const time = new Date(item.from || item.start || item.datetime);
      let value;
      for (const key of aliases) {
        const candidate = Number(item[key]);
        if (Number.isFinite(candidate)) { value = candidate; break; }
      }
      return { time, value };
    }).filter((item) => !Number.isNaN(item.time.getTime()) && Number.isFinite(item.value))
      .sort((a, b) => a.time - b.time);
  }

  _selectedPriceStepMs(rows) {
    const differences = rows.slice(1).map((item, index) =>
      item.time.getTime() - rows[index].time.getTime()
    ).filter((difference) => difference >= 5 * 60000 && difference <= 2 * 3600000);
    const sourceStep = differences.length ? Math.min(...differences) : 3600000;
    const sourceSupportsQuarter = sourceStep <= 30 * 60000;
    const selected = String(this._state(E.dischargeResolution, "60 minuten")).toLowerCase();
    if (selected.startsWith("60")) return 3600000;
    if (selected.startsWith("15")) return sourceSupportsQuarter ? 15 * 60000 : 3600000;
    return 3600000;
  }

  _aggregatePriceRows(rows, stepMs) {
    const groups = new Map();
    rows.forEach((row) => {
      const bucket = Math.floor(row.time.getTime() / stepMs) * stepMs;
      const group = groups.get(bucket) || [];
      group.push(row.value);
      groups.set(bucket, group);
    });
    return [...groups.entries()].sort(([a], [b]) => a - b).map(([time, values]) => ({
      time: new Date(time),
      value: values.reduce((sum, value) => sum + value, 0) / values.length,
    }));
  }

  _priceModeAt(time, stepMs = 3600000) {
    return this._priceClassificationAt(time, stepMs).mode;
  }

  _loadPriceClassificationCache() {
    try {
      const stored = JSON.parse(
        localStorage.getItem("openems-price-classifications-v1") || "{}"
      );
      return stored && typeof stored.items === "object"
        ? stored.items : {};
    } catch (_error) {
      return {};
    }
  }

  _loadExecutedExportIntervals() {
    try {
      const stored = JSON.parse(
        localStorage.getItem("openems-executed-export-intervals-v1") || "[]"
      );
      return rememberExecutedExportIntervals(
        Array.isArray(stored) ? stored : [], [], new Date()
      );
    } catch (_error) {
      return [];
    }
  }

  _rememberLiveExportExecution() {
    const exportState = String(
      this._state(E.exportStatus, "")
    ).trim().toLowerCase();
    const systemState = String(
      this._state(E.status, "")
    ).trim().toLowerCase();
    const active = exportState === "terugleveren actief"
      || systemState === "exporting"
      || systemState === "terugleveren";
    if (!active || this._state(E.exportMode) !== "Automatisch exporteren") {
      return;
    }
    const now = new Date();
    const activeIntervals = this._intervals(E.nextExport, "blokken")
      .filter(([start, end]) => start <= now && now < end);
    if (!activeIntervals.length) return;
    const before = JSON.stringify(this._executedExportIntervals);
    this._executedExportIntervals = rememberExecutedExportIntervals(
      this._executedExportIntervals, activeIntervals, now
    );
    const serialized = JSON.stringify(
      this._executedExportIntervals.map(([start, end]) => [
        start.toISOString(), end.toISOString(),
      ])
    );
    if (serialized === before) return;
    try {
      localStorage.setItem(
        "openems-executed-export-intervals-v1", serialized
      );
    } catch (_error) {
      // De actuele status blijft bruikbaar zonder browseropslag.
    }
  }

  _priceClassificationCacheKey(time, stepMs) {
    return `${time.getTime()}:${stepMs}`;
  }

  _cachedPriceClassification(time, stepMs) {
    return this._priceClassificationCache[
      this._priceClassificationCacheKey(time, stepMs)
    ] || null;
  }

  _rememberPriceClassification(time, stepMs, classification) {
    const key = this._priceClassificationCacheKey(time, stepMs);
    const previous = this._priceClassificationCache[key];
    if (
      previous
      && previous.mode === classification.mode
      && previous.stage === classification.stage
      && previous.className === classification.className
      && previous.label === classification.label
      && previous.candidateType === classification.candidateType
    ) {
      return classification;
    }
    this._priceClassificationCache[key] = classification;
    if (this._classificationCacheTimer) {
      clearTimeout(this._classificationCacheTimer);
    }
    this._classificationCacheTimer = setTimeout(
      () => this._flushPriceClassificationCache(), 250
    );
    return classification;
  }

  _flushPriceClassificationCache() {
    this._classificationCacheTimer = null;
    const oldest = Date.now() - 3 * 24 * 60 * 60 * 1000;
    const items = Object.fromEntries(
      Object.entries(this._priceClassificationCache)
        .filter(([key]) => Number(key.split(":")[0]) >= oldest)
        .slice(-500)
    );
    this._priceClassificationCache = items;
    try {
      localStorage.setItem(
        "openems-price-classifications-v1",
        JSON.stringify({ savedAt: Date.now(), items })
      );
    } catch (_error) {
      // De grafiek blijft werken wanneer browseropslag niet beschikbaar is.
    }
  }

  // Een "mogelijk laden"-uur vervalt als de voorspelling in de komende 24 uur
  // duidelijk goedkoper wordt (zelfde drempels als de planner).
  _forecastBeatsCharge(time, stepMs) {
    const price = this._frankPriceByTime?.get(time.getTime());
    if (!Number.isFinite(price) || this._state(E.priceForecast) !== "on") return false;
    const end = time.getTime() + stepMs;
    if (end <= Date.now()) return false;
    const after = Math.max(end, Number(this._lastFrankEndMs) || 0);
    const rows = this._forecastRows(stepMs, after, end + 24 * 3600000);
    if (!rows.length) return false;
    const minDiffCt = this._num("number.openems_prijsvoorspelling_min_verschil");
    const marginCt = this._num("number.openems_prijsvoorspelling_extra_marge");
    const minDiff = (Number.isFinite(minDiffCt) ? minDiffCt : 2) / 100;
    const margin = (Number.isFinite(marginCt) ? marginCt : 3) / 100;
    return rows.some((row) => price - row.value >= minDiff + (row.real ? 0 : margin) - 1e-9);
  }

  _priceClassificationAt(time, stepMs = 3600000) {
    const mid = new Date(time.getTime() + stepMs / 2);
    const intervalEnd = new Date(time.getTime() + stepMs);
    const now = new Date();
    const isPast = intervalEnd <= now;
    const historyIncomplete = (
      this._priceHistoryLoading
      || !this._statusHistory
      || !this._powerHistory
    );
    if (isPast && historyIncomplete) {
      const cached = this._cachedPriceClassification(time, stepMs);
      if (cached) return cached;
    }
    const current = time <= now && now < intervalEnd;
    const measuredMode = isPast
      ? this._historicalBatteryMode(time, stepMs) : null;
    const status = String(
      isPast && measuredMode == null
        ? this._historyStateAt(E.status, mid) || "" : ""
    ).toLowerCase();
    const statusMode = status === "charging" || status === "laden"
      ? "charge"
      : [
        "self_consumption", "discharging", "ontladen",
        "zelfconsumptie",
      ].includes(status) ? "self" : null;
    const manualMode = isPast
      ? this._dominantManualPriceMode(time, stepMs)
      : current && this._state(E.auto) !== "on"
        ? this._manualPriceMode(this._state(E.manualStatus)) : null;
    const liveMode = current ? this._liveBatteryMode() : null;
    const classification = classifyPricePlanningSlot({
      phase: isPast ? "past" : current ? "current" : "future",
      exportExecuted: this._historicalExportWasActive(time, stepMs),
      manualControl: current && this._state(E.auto) !== "on",
      manualMode,
      measuredMode,
      statusMode,
      liveMode,
      exportPlanned: this._exportPlannedAt(time, stepMs),
      chargePlanned: this._overlapsInterval(
        time, stepMs, this._intervals(E.nextCharge)
      ),
      selfPlanned: this._overlapsInterval(
        time, stepMs, this._intervals(E.nextDischarge)
      ),
      exportCandidate: this._exportCandidateAt(time, stepMs),
      chargeCandidate: this._priceCandidateAt(time, stepMs)
        && !this._forecastBeatsCharge(time, stepMs),
      selfCandidate: this._dischargeCandidateAt(time, stepMs),
    });
    return isPast && !historyIncomplete
      ? this._rememberPriceClassification(time, stepMs, classification)
      : classification;
  }

  _plannedPriceModeAt(time, stepMs = 3600000) {
    if (this._exportPlannedAt(time, stepMs)) return "export";
    if (this._overlapsInterval(
      time, stepMs, this._intervals(E.nextCharge)
    )) return "charge";
    if (this._overlapsInterval(
      time, stepMs, this._intervals(E.nextDischarge)
    )) return "self";
    return null;
  }

  _candidatePriceModeAt(time, stepMs = 3600000) {
    if (this._exportCandidateAt(time, stepMs)) return "export";
    if (this._priceCandidateAt(time, stepMs)) return "charge";
    if (this._dischargeCandidateAt(time, stepMs)) return "self";
    return null;
  }

  _historyStateAt(entity, time) {
    const timestamp = time.getTime();
    return this._statusTransitions(entity)
      .map((item) => ({
        state: item.state,
        time: Date.parse(item.last_changed || item.last_updated || 0),
      }))
      .filter((item) => item.time <= timestamp)
      .sort((a, b) => b.time - a.time)[0]?.state;
  }

  _statusTransitions(entity) {
    const items = [...this._historyItems(this._statusHistory, entity)];
    const live = this._obj(entity);
    if (live?.last_changed || live?.last_updated) items.push(live);
    return items;
  }

  _manualPriceMode(state) {
    const value = String(state || "").toLowerCase();
    if (value === "laden" || value === "charging") return "charge";
    if (
      value === "ontladen" || value === "discharging"
      || value === "zelfconsumptie" || value === "self_consumption"
    ) return "self";
    if (
      value === "stop" || value === "stopped"
      || value === "idle" || value === "rust"
    ) return "standby";
    return null;
  }

  _dominantManualPriceMode(time, stepMs = 3600000) {
    if (!this._statusHistory) return null;
    const start = time.getTime();
    const end = Math.min(start + stepMs, Date.now());
    if (end <= start) return null;
    const changes = [start, end];
    for (const entity of [E.switchStatus, E.manualStatus]) {
      for (const item of this._statusTransitions(entity)) {
        const changed = Date.parse(
          item.last_changed || item.last_updated || 0
        );
        if (changed > start && changed < end) changes.push(changed);
      }
    }
    const boundaries = [...new Set(changes)].sort((a, b) => a - b);
    const durations = new Map();
    for (let index = 0; index < boundaries.length - 1; index++) {
      const segmentStart = boundaries[index];
      const segmentEnd = boundaries[index + 1];
      const mid = new Date((segmentStart + segmentEnd) / 2);
      const switchState = String(
        this._historyStateAt(E.switchStatus, mid) || "AUTO"
      ).toLowerCase();
      const manual = switchState.includes("handmatig")
        || switchState === "off";
      if (!manual) continue;
      const mode = this._manualPriceMode(
        this._historyStateAt(E.manualStatus, mid)
      );
      if (!mode) continue;
      durations.set(
        mode, (durations.get(mode) || 0) + segmentEnd - segmentStart
      );
    }
    const dominant = [...durations.entries()]
      .sort((left, right) => right[1] - left[1])[0];
    return dominant && dominant[1] > (end - start) / 2
      ? dominant[0] : null;
  }

  _liveBatteryMode() {
    const powers = E.batteries.map((battery) => {
      const measured = this._num(battery.actual);
      if (Number.isFinite(measured)) return measured;
      const charge = this._num(battery.charge);
      const discharge = this._num(battery.discharge);
      if (!Number.isFinite(charge) && !Number.isFinite(discharge)) return NaN;
      return (Number.isFinite(discharge) ? discharge : 0)
        - (Number.isFinite(charge) ? charge : 0);
    }).filter(Number.isFinite);
    if (!powers.length) return "standby";
    const charging = powers.reduce((sum, power) => sum + Math.max(0, -power), 0);
    const discharging = powers.reduce((sum, power) => sum + Math.max(0, power), 0);
    if (charging > 25 && charging >= discharging) return "charge";
    if (discharging > 25) return "self";
    return "standby";
  }

  _priceCandidateAt(time, stepMs = 3600000) {
    return this._overlapsInterval(
      time, stepMs, this._intervals(E.nextCharge, "prijsblokken")
    );
  }

  _dischargeCandidateAt(time, stepMs = 3600000) {
    const now = new Date();
    const intervalEnd = new Date(time.getTime() + stepMs);
    if (intervalEnd <= now) {
      return false;
    }
    return this._overlapsInterval(
      time, stepMs, this._intervals(E.nextDischarge, "prijsblokken")
    );
  }

  _dischargeProfitAt(time, stepMs = 3600000) {
    return this._overlapsInterval(
      time, stepMs, this._intervals(E.nextDischarge, "winstblokken")
    );
  }

  _exportCandidateAt(time, stepMs = 3600000) {
    const intervalEnd = new Date(time.getTime() + stepMs);
    if (intervalEnd <= new Date()) {
      return this._overlapsInterval(
        time, stepMs, this._historicalExportIntervals("prijsblokken")
      );
    }
    return this._overlapsInterval(
      time, stepMs, this._intervals(E.nextExport, "prijsblokken")
    );
  }

  _exportPlannedAt(time, stepMs = 3600000) {
    const intervalEnd = new Date(time.getTime() + stepMs);
    if (intervalEnd <= new Date()) {
      return this._historicalExportWasActive(time, stepMs)
        || this._overlapsInterval(
          time, stepMs, this._historicalExportIntervals("blokken")
        );
    }
    const mode = this._state(E.exportMode, "Alleen simuleren");
    return mode === "Automatisch exporteren" && this._overlapsInterval(
      time, stepMs, this._intervals(E.nextExport, "blokken")
    );
  }

  _historicalExportIntervals(attribute) {
    const intervals = [];
    const items = [
      ...this._historyItems(this._exportPlanHistory, E.nextExport)
    ];
    const live = this._obj(E.nextExport);
    if (live) items.push(live);
    for (const item of items) {
      const blocks = item?.attributes?.[attribute];
      if (!Array.isArray(blocks)) continue;
      for (const block of blocks) {
        const start = new Date(block.start);
        const end = new Date(block.einde);
        if (!Number.isNaN(start.getTime()) && !Number.isNaN(end.getTime())) {
          intervals.push([start, end]);
        }
      }
    }
    return intervals;
  }

  _historicalExportWasActive(time, stepMs = 3600000) {
    if (executedExportOverlaps(
      this._executedExportIntervals, time, stepMs
    )) {
      return true;
    }
    const start = time.getTime();
    const intervalStart = new Date(start);
    const statusWasActive = statusWasActiveDuring(
      this._statusTransitions(E.exportStatus),
      ["terugleveren actief"], intervalStart, stepMs
    ) || statusWasActiveDuring(
      this._statusTransitions(E.status),
      ["terugleveren", "exporting"], intervalStart, stepMs
    );
    if (statusWasActive) return true;

    // De HA-statushistorie kan een korte actief-overgang missen. Gebruik
    // daarom binnen een teruglevervenster ook de echte netmeting als bewijs.
    const plannedIntervals = this._historicalExportIntervals("blokken");
    return this._overlapsInterval(time, stepMs, plannedIntervals)
      && this._historicalGridExportWasActive(time, stepMs);
  }

  _historicalGridExportWasActive(time, stepMs = 3600000) {
    if (!this._powerHistory?.grid) return false;
    const start = time.getTime() / 1000;
    const end = Math.min(start + stepMs / 1000, Date.now() / 1000);
    const points = this._historyPoints(this._historyItems(
      this._powerHistory.response, this._powerHistory.grid
    ));
    let exportedKwh = 0;
    for (let index = 0; index < points.length; index++) {
      const segmentStart = Math.max(start, points[index][0]);
      const segmentEnd = Math.min(
        end, points[index + 1]?.[0] ?? Date.now() / 1000
      );
      if (segmentEnd <= segmentStart) continue;
      const watts = points[index][1];
      if (watts < -50) {
        exportedKwh += -watts * (segmentEnd - segmentStart) / 3600000;
      }
    }
    return exportedKwh >= 0.02;
  }

  _historicalBatteryMode(time, stepMs = 3600000) {
    if (!this._powerHistory) return null;
    const entities = this._powerHistory.batteryPowerEntities || [];
    const start = time.getTime() / 1000;
    const end = start + stepMs / 1000;
    const now = Date.now() / 1000;
    let observedBatteries = 0;
    let chargingBatteries = 0;
    let dischargingBatteries = 0;

    for (const entity of entities) {
      let chargedKwh = 0;
      let dischargedKwh = 0;
      let entityObservedSeconds = 0;
      const points = this._historyPoints(
        this._historyItems(this._powerHistory.response, entity)
      );
      for (let index = 0; index < points.length; index++) {
        const segmentStart = Math.max(start, points[index][0]);
        const segmentEnd = Math.min(
          end, points[index + 1]?.[0] ?? now, now
        );
        if (segmentEnd <= segmentStart) continue;
        const watts = points[index][1];
        const seconds = segmentEnd - segmentStart;
        entityObservedSeconds += seconds;
        if (watts < -50) {
          chargedKwh += -watts * seconds / 3600000;
        } else if (watts > 50) {
          dischargedKwh += watts * seconds / 3600000;
        }
      }
      if (entityObservedSeconds > 0) observedBatteries += 1;
      if (chargedKwh >= 0.02 && chargedKwh >= dischargedKwh) {
        chargingBatteries += 1;
      } else if (dischargedKwh >= 0.02) {
        dischargingBatteries += 1;
      }
    }
    if (observedBatteries === 0) return null;
    if (chargingBatteries >= 1) return "charge";
    if (dischargingBatteries >= 1) return "self";
    return "standby";
  }

  _manualOverviewMessage(manual) {
    const state = String(
      manual?.state || this._state(E.manualMode, "Stop")
    ).toLowerCase();
    const verification = String(
      manual?.attributes?.verificatie || ""
    ).toUpperCase();
    const message = String(manual?.attributes?.melding || "");

    if (state === "fout" || verification === "MISLUKT") {
      return "Handmatige opdracht mislukt · bekijk de melding bij Bediening";
    }
    if (message.startsWith("Opdracht uitgevoerd")) {
      return "Handmatige opdracht wordt uitgevoerd en gecontroleerd";
    }
    if (verification === "OK") {
      return ({
        stop: "Alle accu's zijn handmatig gestopt",
        laden: "Alle accu's laden volgens de handmatige opdracht",
        ontladen: "Alle accu's ontladen volgens de handmatige opdracht",
        zelfconsumptie: "Alle accu's staan in zelfconsumptie",
      })[state] || "Handmatige opdracht is correct uitgevoerd";
    }
    return "Handmatige bediening actief";
  }

  _inIntervals(time, intervals) { return intervals.some(([start, end]) => time >= start && time < end); }
  _overlapsInterval(time, stepMs, intervals) {
    const intervalEnd = new Date(time.getTime() + stepMs);
    return intervals.some(([start, end]) => start < intervalEnd && end > time);
  }
  _overlapsHour(time, intervals) {
    return this._overlapsInterval(time, 60 * 60 * 1000, intervals);
  }
  _intervals(entity, attribute = "blokken") {
    const obj = this._obj(entity), blocks = obj?.attributes?.[attribute];
    if (Array.isArray(blocks)) return blocks.map((b) => [new Date(b.start), new Date(b.einde)]).filter(([s, e]) => !Number.isNaN(s.getTime()) && !Number.isNaN(e.getTime()));
    const interval = this._interval(entity);
    return interval ? [interval] : [];
  }

  _renderPriceSummary(data) {
    const today = new Date(), tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
    const sameDay = (date, day) => date.getFullYear() === day.getFullYear() && date.getMonth() === day.getMonth() && date.getDate() === day.getDate();
    const stats = (day) => { const values = data.filter((p) => sameDay(p.time, day)).map((p) => p.value); return values.length ? [Math.min(...values), Math.max(...values)] : null; };
    const current = data.find((p) => new Date() >= p.time && new Date() < new Date(p.time.getTime() + (p.stepMs || 3600000)));
    const t = stats(today), m = stats(tomorrow);
    const metric = (label, value, missing = "—") =>
      `<div><span>${label}</span><b>${Number.isFinite(value) ? this._fmt(value, 4) : missing}</b><small>${Number.isFinite(value) ? "€/kWh" : ""}</small></div>`;
    this.$("price-summary").innerHTML = `${metric("Nu", current?.value)}${metric("Vandaag laag", t?.[0])}${metric("Vandaag hoog", t?.[1])}${metric("Morgen laag", m?.[0], "Nog niet gepubliceerd")}${metric("Morgen hoog", m?.[1], "Nog niet gepubliceerd")}`;
  }

  _showPriceTooltip(point, bar) {
    if (!point) return;
    const tooltip = this.$("price-tooltip");
    const chart = this.$("price-chart");
    if (!tooltip || !chart || !bar) return;
    const returnPrice = Number.isFinite(point.marketPlus)
      ? `${this._fmt(point.marketPlus, 4)} €/kWh` : "niet beschikbaar";
    const forecastMode = { laden: "laden", zelf: "zelfconsumptie", standby: "stand-by" }[point.mode];
    const forecastNote = point.forecast ? ` (voorspeld${forecastMode ? ` · ${forecastMode} verwacht` : ""})` : "";
    tooltip.innerHTML = `<b>${point.time.toLocaleDateString("nl-NL", {
      weekday: "short", day: "2-digit", month: "2-digit",
    })} · ${point.time.toLocaleTimeString("nl-NL", {
      hour: "2-digit", minute: "2-digit",
    })}</b><span>All-inprijs${forecastNote} <strong>${this._fmt(point.value, 4)} €/kWh</strong></span><span>Terugleverprijs${point.forecast ? " (voorspeld)" : ""} <strong>${returnPrice}</strong></span>`;
    tooltip.hidden = false;
    const chartRect = chart.getBoundingClientRect();
    const barRect = bar.getBoundingClientRect();
    const width = tooltip.offsetWidth;
    const height = tooltip.offsetHeight;
    const center = barRect.left - chartRect.left + barRect.width / 2;
    const left = Math.max(6, Math.min(chartRect.width - width - 6, center - width / 2));
    const above = barRect.top - chartRect.top - height - 8;
    const top = above >= 4
      ? above : Math.min(210 - height, barRect.bottom - chartRect.top + 8);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(4, top)}px`;
    const detail = this.$("price-detail");
    if (detail) detail.textContent = `${point.time.toISOString()} | ${point.value} | ${returnPrice}`;
  }

  _hidePriceTooltip() {
    const tooltip = this.$("price-tooltip");
    if (tooltip) tooltip.hidden = true;
  }

  _renderMarketChart(data) {
    const usable = data.filter((p) => Number.isFinite(p.market));
    if (!usable.length) { this.$("market-chart").innerHTML = '<div class="empty">Marktprijs ontbreekt in de Frank-prijsentiteit</div>'; return; }
    const w = 1000, h = 270, left = 62, right = 12, top = 18, bottom = 40;
    const allValues = usable.flatMap((p) => [0, p.market, Number.isFinite(p.marketPlus) ? p.marketPlus : p.market, p.value]);
    const axis = this._priceAxis(allValues, h, top, bottom);
    const bw = (w - left - right) / usable.length;
    const segment = (from, to, cls, x, width, point, label) => {
      const yFrom = axis.y(from), yTo = axis.y(to);
      return `<rect class="${cls}" x="${x}" y="${Math.min(yFrom, yTo)}" width="${width}" height="${Math.max(1, Math.abs(yFrom - yTo))}"><title>${point.time.toLocaleString("nl-NL")} · ${label} ${this._fmt(to, 4)} €/kWh</title></rect>`;
    };
    const bars = usable.map((p, i) => {
      const middle = Number.isFinite(p.marketPlus) ? p.marketPlus : p.market;
      const x = left + i * bw + 1, width = Math.max(2, bw - 2);
      return [
        segment(0, p.market, "market-segment", x, width, p, "Markt"),
        segment(p.market, middle, "marketplus-segment", x, width, p, "Terugleverprijs"),
        segment(middle, p.value, "allin-segment", x, width, p, "All-in"),
      ].join("");
    }).join("");
    const labels = this._priceTimeLabels(usable, left, bw, h - 12);
    const missingMarketPlus = usable.some((p) => !Number.isFinite(p.marketPlus));
    this.$("market-chart").innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${axis.svg(left, w - right)}${bars}<g class="axis-labels">${labels}</g></svg><div class="chart-note"><span class="market-dot">Marktprijs</span><span class="marketplus-dot">Terugleverprijs</span><span class="allin-dot">All-inprijs</span><span class="resolution-dot">${this._priceResolutionLabel(usable[0].stepMs)}</span>${missingMarketPlus ? '<span class="source-warning">Marktprijs ontbreekt deels</span>' : ""}</div>`;
  }

  _priceAxis(values, height, top, bottom) {
    const finite = values.filter(Number.isFinite);
    let min = Math.min(0, ...finite), max = Math.max(0, ...finite);
    if (max - min < 0.01) max = min + 0.01;
    const rawStep = (max - min) / 5;
    const magnitude = 10 ** Math.floor(Math.log10(rawStep || 0.01));
    const normalized = rawStep / magnitude;
    const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    const step = nice * magnitude;
    min = Math.floor(min / step) * step;
    max = Math.ceil(max / step) * step;
    if (max === min) max = min + step;
    const y = (value) => height - bottom - ((value - min) / (max - min)) * (height - top - bottom);
    const ticks = [];
    for (let value = min, guard = 0; value <= max + step / 2 && guard < 20; value += step, guard++) ticks.push(Number(value.toFixed(8)));
    const svg = (left, right) => `<g class="price-grid"><text class="price-axis-unit" x="2" y="11">€/kWh</text>${ticks.map((value) => `<line class="${Math.abs(value) < 1e-9 ? "price-zero-line" : ""}" x1="${left}" x2="${right}" y1="${y(value)}" y2="${y(value)}"/><text x="${left - 7}" y="${y(value) + 4}">${this._fmt(value, value !== 0 && Math.abs(value) < 0.1 ? 3 : 2)}</text>`).join("")}</g>`;
    return { min, max, y, svg };
  }

  _priceTimeLabels(data, left, barWidth, y) {
    if (!data.length) return "";
    const stepMs = data[0].stepMs || 3600000;
    const every = Math.max(1, Math.round(6 * 3600000 / stepMs));
    return data.map((point, index) => index % every === 0
      ? `<text x="${left + index * barWidth}" y="${y}">${point.time.toLocaleDateString("nl-NL", { weekday: "short" })} ${String(point.time.getHours()).padStart(2, "0")}:${String(point.time.getMinutes()).padStart(2, "0")}</text>`
      : "").join("");
  }

  _priceResolutionLabel(stepMs) {
    return stepMs <= 15 * 60000 ? "Resolutie 15 min" : "Resolutie 60 min";
  }

  _interval(entity) {
    const obj = this._obj(entity);
    const start = obj?.attributes?.start;
    const end = obj?.attributes?.einde;
    if (!start || !end || start === "geen" || end === "geen") return null;
    return [new Date(start), new Date(end)];
  }

  async _loadHistory(force) {
    if (!this._hass?.callWS) return;
    if (this._historyLoading) return this._historyLoading;
    this._renderCachedHistory();
    const currentTime = Date.now();
    if (!force && currentTime - this._historyAt < 4 * 60 * 1000) return;
    if (!force && currentTime - this._historyAttemptAt < 10000) return;
    this._historyAttemptAt = currentTime;
    const now = new Date();
    const todayStart = new Date(
      now.getFullYear(), now.getMonth(), now.getDate()
    );
    const yesterdayStart = new Date(todayStart);
    yesterdayStart.setDate(yesterdayStart.getDate() - 1);
    const weekStart = new Date(todayStart);
    weekStart.setDate(weekStart.getDate() - 6);
    const { home, grid } = this._powerEntities();
    const socEntities = E.batteries.map((b) => b.soc);
    const batteryPowerEntities = E.batteries.map((b) => b.actual);
    const generation = (this._historyGeneration || 0) + 1;
    this._historyGeneration = generation;
    this._priceHistoryLoading = true;
    if (this._tab === "graphs") this._renderPriceChart();
    this.$("price-detail").innerHTML = "<b>Historie wordt bijgewerkt</b><span>Toekomstplanning is al beschikbaar; verstreken uren worden nu ingekleurd.</span>";
    let statusLoaded = false;
    let powerLoaded = false;
    const request = (async () => {
      const statusTask = this._hass.callWS({
        type: "history/history_during_period",
        start_time: yesterdayStart.toISOString(),
        end_time: now.toISOString(),
        entity_ids: [
          E.status, E.manualStatus, E.switchStatus, E.exportStatus
        ],
        minimal_response: false,
        no_attributes: true,
      }).then((status) => {
        if (generation !== this._historyGeneration) return;
        this._statusHistory = status;
        DASHBOARD_SESSION_CACHE.statusHistory = status;
        statusLoaded = true;
      }).catch((error) => {
        console.warn("[OpenEMS-M] Frank-statushistorie ophalen mislukt", error);
      });
      const exportPlanTask = this._hass.callWS({
        type: "history/history_during_period",
        start_time: yesterdayStart.toISOString(),
        end_time: now.toISOString(),
        entity_ids: [E.nextExport],
        minimal_response: false,
        no_attributes: false,
      }).then((history) => {
        if (generation !== this._historyGeneration) return;
        this._exportPlanHistory = history;
        DASHBOARD_SESSION_CACHE.exportPlanHistory = history;
        this._historyRevision += 1;
        if (this._tab === "graphs") this._renderPriceChart();
      }).catch((error) => {
        console.warn("[OpenEMS-M] Terugleverplanhistorie ophalen mislukt", error);
      });
      const recentPowerTask = this._hass.callWS({
        type: "history/history_during_period",
        start_time: yesterdayStart.toISOString(),
        end_time: now.toISOString(),
        entity_ids: [home, grid, ...batteryPowerEntities],
        minimal_response: true,
        no_attributes: true,
      }).then((recentPower) => {
        if (generation !== this._historyGeneration) return;
        this._recentPowerHistory = {
          response: recentPower, home, grid, batteryPowerEntities
        };
        DASHBOARD_SESSION_CACHE.recentPowerHistory = this._recentPowerHistory;
        if (!this._powerHistory) this._powerHistory = this._recentPowerHistory;
        powerLoaded = true;
        this._renderPowerHistory(false);
      }).catch((error) => {
        console.warn("[OpenEMS-M] Recente vermogenshistorie mislukt", error);
        if (!this._powerHistory) {
          ["power-chart-yesterday", "power-chart-today"].forEach((id) => {
            this.$(id).innerHTML = '<div class="empty error">Historie ophalen mislukt</div>';
          });
        }
      });
      const socTask = this._hass.callWS({
        type: "history/history_during_period",
        start_time: yesterdayStart.toISOString(),
        end_time: now.toISOString(),
        entity_ids: socEntities,
        minimal_response: true,
        no_attributes: true,
      }).then((soc) => {
        if (generation !== this._historyGeneration) return;
        this._socHistory = soc;
        DASHBOARD_SESSION_CACHE.socHistory = soc;
        this._renderSocHistory();
      }).catch((error) => {
        console.warn("[OpenEMS-M] SOC-historie ophalen mislukt", error);
        if (!this._socHistory) {
          ["soc-chart-yesterday", "soc-chart-today"].forEach((id) => {
            this.$(id).innerHTML = '<div class="empty error">SOC-historie ophalen mislukt</div>';
          });
        }
      });
      await Promise.allSettled([
        statusTask, exportPlanTask, recentPowerTask, socTask
      ]);
      if (generation !== this._historyGeneration) return;
      this._priceHistoryLoading = false;
      this._historyRevision += 1;
      if (statusLoaded || powerLoaded) {
        this._historyAt = Date.now();
        DASHBOARD_SESSION_CACHE.historyAt = this._historyAt;
      }
      DASHBOARD_SESSION_CACHE.historyRevision = this._historyRevision;
      if (this._tab === "graphs") this._renderPriceChart();
      if (this._tab === "status") this._schedulePatch();
      this.$("price-detail").innerHTML = statusLoaded && powerLoaded
        ? "<b>Historie bijgewerkt</b><span>Tik op een uur voor prijzen, accukostprijs en uitleg over terugleveren.</span>"
        : '<b>Historie gedeeltelijk beschikbaar</b><span>Bestaande inkleuring blijft behouden; toekomstplanning blijft zichtbaar.</span>';
      try {
        const weeklyRequest = {
          type: "history/history_during_period",
          start_time: weekStart.toISOString(),
          end_time: now.toISOString(),
          entity_ids: [home, grid, ...batteryPowerEntities],
          minimal_response: true,
          no_attributes: true,
        };
        let weeklyPower;
        let weeklyError;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            weeklyPower = await this._hass.callWS(weeklyRequest);
            weeklyError = null;
            break;
          } catch (error) {
            weeklyError = error;
            if (attempt < 2) {
              await new Promise((resolve) => setTimeout(
                resolve, 300 * (attempt + 1)
              ));
            }
          }
        }
        if (weeklyError) throw weeklyError;
        if (generation !== this._historyGeneration) return;
        this._powerHistory = {
          response: weeklyPower, home, grid, batteryPowerEntities
        };
        this._weekHistoryAvailable = true;
        DASHBOARD_SESSION_CACHE.powerHistory = this._powerHistory;
        DASHBOARD_SESSION_CACHE.weekHistoryAvailable = true;
        this._renderPowerHistory(true);
      } catch (error) {
        console.warn("[OpenEMS-M] Weekhistorie ophalen mislukt", error);
        if (
          generation === this._historyGeneration
          && !this._weekHistoryAvailable
        ) {
          this.$("power-chart-week").innerHTML = '<div class="empty error">Weekhistorie ophalen mislukt</div>';
        }
      }
    })();
    this._historyLoading = request;
    try {
      await request;
    } finally {
      if (this._historyLoading === request) this._historyLoading = null;
    }
  }

  _renderPowerHistory(includeWeek = true) {
    const recentHistory = this._recentPowerHistory || this._powerHistory;
    if (!recentHistory) return;
    const now = new Date();
    const todayStart = new Date(
      now.getFullYear(), now.getMonth(), now.getDate()
    );
    const yesterdayStart = new Date(todayStart);
    yesterdayStart.setDate(yesterdayStart.getDate() - 1);
    const weekStart = new Date(todayStart);
    weekStart.setDate(weekStart.getDate() - 6);
    this._renderPowerRange(
      "power-chart-yesterday", "flow-summary-yesterday",
      yesterdayStart.getTime() / 1000, todayStart.getTime() / 1000,
      15 * 60, 16, false, recentHistory
    );
    this._renderPowerRange(
      "power-chart-today", "flow-summary-today",
      todayStart.getTime() / 1000, now.getTime() / 1000,
      15 * 60, 16, false, recentHistory
    );
    if (includeWeek) {
      this._renderPowerRange(
        "power-chart-week", "flow-summary-week",
        weekStart.getTime() / 1000, now.getTime() / 1000,
        60 * 60, 24, true, this._powerHistory || recentHistory
      );
    }
  }

  _renderCachedHistory() {
    if (!this._built) return;
    if (this._recentPowerHistory || this._powerHistory) {
      this._renderPowerHistory(this._weekHistoryAvailable);
    }
    if (this._socHistory) this._renderSocHistory();
    if (
      this._tab === "graphs"
      && (this._statusHistory || this._exportPlanHistory)
    ) this._renderPriceChart();
  }

  _renderPowerRange(
    chartId, summaryId, start, end, secondsPerBin, labelEvery, showDate,
    history = this._powerHistory
  ) {
    const {
      response, home, grid, batteryPowerEntities = [],
    } = history;
    const homePoints = this._historyPoints(
      this._historyItems(response, home)
    );
    const gridPoints = this._historyPoints(
      this._historyItems(response, grid)
    );
    const batteryPoints = batteryPowerEntities.map((entity) =>
      this._historyPoints(this._historyItems(response, entity))
    );
    if (!homePoints.length || !gridPoints.length) {
      this.$(chartId).innerHTML = '<div class="empty">Geen recorderhistorie voor woning- en netvermogen</div>';
      return;
    }

    const data = [];
    for (let binStart = start; binStart < end; binStart += secondsPerBin) {
      const binEnd = Math.min(end, binStart + secondsPerBin);
      const homePower = this._averagePower(
        homePoints, binStart, binEnd
      );
      const rawGridPower = this._averagePower(
        gridPoints, binStart, binEnd
      );
      if (!Number.isFinite(homePower) || !Number.isFinite(rawGridPower)) {
        continue;
      }
      const batteryPower = batteryPoints.reduce((sum, points) => {
        const value = this._averagePower(points, binStart, binEnd);
        return sum + (Number.isFinite(value) ? value : 0);
      }, 0);
      data.push({
        time: binStart,
        hours: (binEnd - binStart) / 3600,
        ...this._energyFlow(
          homePower, rawGridPower, batteryPower
        ),
      });
    }
    if (!data.length) {
      this.$(chartId).innerHTML = '<div class="empty">Onvoldoende overlappende vermogenshistorie</div>';
      return;
    }

    const totals = data.reduce((result, item) => {
      for (const key of [
        "gridToHome", "batteryToHome", "batteryCharge", "exportPower",
      ]) {
        result[key] += item[key] * item.hours / 1000;
      }
      return result;
    }, {
      gridToHome: 0, batteryToHome: 0,
      batteryCharge: 0, exportPower: 0,
    });
    this.$(summaryId).innerHTML = [
      ["Woning uit net", totals.gridToHome, "grid-home"],
      ["Woning uit accu", totals.batteryToHome, "battery-home"],
      ["Accu geladen", totals.batteryCharge, "battery-charge"],
      ["Teruggeleverd", totals.exportPower, "export"],
    ].map(([label, value, tone]) => `
      <div class="${tone}"><span>${label}</span>
      <b>${this._fmt(value, 2)} kWh</b></div>`
    ).join("");

    const w = 1000, h = 270, px = 42, py = 24;
    const positiveMax = Math.max(
      100,
      ...data.map((item) =>
        item.gridToHome + item.batteryToHome + item.batteryCharge
      ),
    );
    const exportMax = Math.max(
      0, ...data.map((item) => item.exportPower)
    );
    const scale = this._flowChartScale(
      positiveMax, exportMax, h, py
    );
    const zero = scale.zero;
    const positiveHeight = (value) =>
      value / scale.positiveMax * scale.positiveHeight;
    const exportHeightFor = (value) =>
      scale.exportMax > 0
        ? value / scale.exportMax * scale.negativeHeight
        : 0;
    const barWidth = (w - 2 * px) / data.length;
    const bars = data.map((item, index) => {
      const x = px + index * barWidth + 0.5;
      let top = zero;
      const positive = [
        ["flow-grid-home-bar", item.gridToHome],
        ["flow-battery-home-bar", item.batteryToHome],
        ["flow-battery-charge-bar", item.batteryCharge],
      ].map(([className, value]) => {
        const barHeight = positiveHeight(value);
        top -= barHeight;
        return `<rect class="${className}" x="${x}" y="${top}" width="${Math.max(1.5, barWidth - 1)}" height="${Math.max(0, barHeight)}"/>`;
      }).join("");
      const exportHeight = exportHeightFor(item.exportPower);
      const title = new Date(item.time * 1000).toLocaleTimeString(
        "nl-NL", { hour: "2-digit", minute: "2-digit" }
      );
      return `<g><title>${title} · uit net ${this._fmt(item.gridToHome, 0)} W · uit accu ${this._fmt(item.batteryToHome, 0)} W · laden ${this._fmt(item.batteryCharge, 0)} W · terugleveren ${this._fmt(item.exportPower, 0)} W</title>${positive}<rect class="flow-export-bar" x="${x}" y="${zero}" width="${Math.max(1.5, barWidth - 1)}" height="${Math.max(0, exportHeight)}"/></g>`;
    }).join("");
    const labels = data.map((item, index) => (
      index % labelEvery === 0
        ? `<text x="${px + index * barWidth}" y="${h - 4}">${showDate
          ? new Date(item.time * 1000).toLocaleDateString("nl-NL", { weekday: "short", day: "2-digit" })
          : new Date(item.time * 1000).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" })}</text>`
        : ""
    )).join("");
    const positiveTicks = [0, 0.25, 0.5, 0.75, 1].map((share) => {
      const value = scale.positiveMax * share;
      const y = zero - scale.positiveHeight * share;
      return `<line class="${share === 0 ? "flow-zero" : "flow-grid-line"}" x1="${px}" x2="${w-px}" y1="${y}" y2="${y}"/><text class="flow-axis-label" x="2" y="${y + 4}">${this._fmt(value, 0)} W</text>`;
    }).join("");
    const negativeTicks = scale.exportMax > 0
      ? [0.5, 1].map((share) => {
        const value = scale.exportMax * share;
        const y = zero + scale.negativeHeight * share;
        return `<line class="flow-grid-line" x1="${px}" x2="${w-px}" y1="${y}" y2="${y}"/><text class="flow-axis-label" x="2" y="${y + 4}">−${this._fmt(value, 0)} W</text>`;
      }).join("") : "";
    this.$(chartId).innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><g>${positiveTicks}${negativeTicks}</g>${bars}<g class="axis-labels">${labels}</g></svg>`;
  }

  _flowChartScale(positiveMaximum, exportMaximum, height = 270, padding = 24) {
    const positiveMax = Math.max(100, Number(positiveMaximum) || 0);
    const exportMax = Math.max(0, Number(exportMaximum) || 0);
    const plotHeight = Math.max(1, height - 2 * padding);
    if (exportMax < 1) {
      return {
        positiveMax,
        exportMax: 0,
        positiveHeight: plotHeight,
        negativeHeight: 0,
        zero: height - padding,
      };
    }
    const exportShare = exportMax / (positiveMax + exportMax);
    const negativeShare = Math.max(0.18, Math.min(0.42, exportShare));
    const negativeHeight = plotHeight * negativeShare;
    const positiveHeightAvailable = plotHeight - negativeHeight;
    return {
      positiveMax,
      exportMax,
      positiveHeight: positiveHeightAvailable,
      negativeHeight,
      zero: padding + positiveHeightAvailable,
    };
  }

  _energyFlow(homePower, gridPower, batteryPower) {
    const home = Math.max(0, Number(homePower) || 0);
    const discharge = Math.max(0, Number(batteryPower) || 0);
    const batteryCharge = Math.max(0, -(Number(batteryPower) || 0));
    const batteryToHome = Math.min(home, discharge);
    const gridToHome = Math.max(0, home - batteryToHome);
    const exportPower = Math.max(0, -(Number(gridPower) || 0));
    return {
      gridToHome, batteryToHome, batteryCharge, exportPower,
    };
  }

  _averagePower(points, start, end) {
    if (!Array.isArray(points) || !points.length || end <= start) return NaN;
    let value = null;
    let cursor = start;
    let weighted = 0;
    let observed = 0;
    for (const [time, power] of points) {
      if (time <= start) {
        value = power;
        continue;
      }
      if (time >= end) break;
      if (value != null && time > cursor) {
        weighted += value * (time - cursor);
        observed += time - cursor;
      }
      value = power;
      cursor = Math.max(start, time);
    }
    if (value != null && end > cursor) {
      weighted += value * (end - cursor);
      observed += end - cursor;
    }
    return observed > 0 ? weighted / observed : NaN;
  }

  _renderSocHistory() {
    if (!this._socHistory) return;
    const now = new Date();
    const todayStart = new Date(
      now.getFullYear(), now.getMonth(), now.getDate()
    );
    const yesterdayStart = new Date(todayStart);
    yesterdayStart.setDate(yesterdayStart.getDate() - 1);
    this._renderSocRange(
      "soc-chart-yesterday",
      yesterdayStart.getTime() / 1000,
      todayStart.getTime() / 1000,
      "00:00", "24:00", false
    );
    this._renderSocRange(
      "soc-chart-today",
      todayStart.getTime() / 1000,
      now.getTime() / 1000,
      "00:00", "nu", true
    );
  }

  _renderSocRange(chartId, minT, maxT, leftLabel, rightLabel, live) {
    const defs = E.batteries.map((b,i) => [b.soc,["#55d996","#8aa8ff","#b58aff","#ffb454"][i],`Accu ${i+1}`]);
    const series = defs.map(([id,color,name]) => {
      const allPoints = this._historyPoints(
        this._historyItems(this._socHistory, id)
      );
      const before = allPoints.filter(([time]) => time <= minT).at(-1);
      const points = allPoints.filter(
        ([time]) => time >= minT && time <= maxT
      );
      if (before && (!points.length || points[0][0] > minT)) {
        points.unshift([minT, before[1]]);
      }
      if (live) {
        const current = this._num(id);
        if (Number.isFinite(current)) {
          if (!points.length || points[points.length - 1][0] < maxT) {
            points.push([maxT, current]);
          }
        }
      } else if (points.length && points[points.length - 1][0] < maxT) {
        points.push([maxT, points[points.length - 1][1]]);
      }
      return { id, color, name, points };
    }).filter((s) => s.points.length);
    if (!series.length) { this.$(chartId).innerHTML = '<div class="empty">Geen SOC-historie beschikbaar</div>'; return; }
    const w=1000,h=260,px=38,py=20;
    const x=(t)=>px+((t-minT)/(maxT-minT))*(w-2*px), y=(v)=>h-py-(Math.max(0,Math.min(100,v))/100)*(h-2*py);
    const paths=series.map((s)=>`<path d="${s.points.map((p,i)=>`${i?"L":"M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ")}" fill="none" stroke="${s.color}" stroke-width="2.5" vector-effect="non-scaling-stroke"><title>${s.name}</title></path>`).join("");
    const ticks = [0, 25, 50, 75, 100].map((value) => `<line class="soc-grid${value === 0 ? " soc-zero" : ""}" x1="${px}" x2="${w-px}" y1="${y(value)}" y2="${y(value)}"/><text class="soc-axis-label" x="3" y="${y(value) + 4}">${value}%</text>`).join("");
    this.$(chartId).innerHTML=`<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${ticks}${paths}<g class="axis-labels"><text x="${px}" y="255">${leftLabel}</text><text x="930" y="255">${rightLabel}</text></g></svg>`;
  }

  _historyPoints(items) {
    if (!Array.isArray(items)) return [];
    return items.map((it) => {
      const t = it.lu ?? (it.last_updated ? Date.parse(it.last_updated) / 1000 : null);
      const v = Number(it.s ?? it.state);
      return [t, v];
    }).filter(([t, v]) => Number.isFinite(t) && Number.isFinite(v)).sort((a, b) => a[0] - b[0]);
  }

  _historyItems(response, entityId) {
    if (!response) return [];
    if (Array.isArray(response)) {
      if (!response.length) return [];
      if (Array.isArray(response[0])) {
        return response.find((items) => items?.[0]?.entity_id === entityId) || [];
      }
      return response.filter((item) => item?.entity_id === entityId);
    }
    return response[entityId] || [];
  }

  _renderStatusDetails() {
    const status = this._obj(E.status);
    const manual = this._obj(E.manualStatus);
    const offset = this._obj(E.gridOffsetStatus);
    const offsetAttrs = offset?.attributes || {};
    const freshness = this._obj(E.freshness);
    const financial = this._obj(E.results);
    const todayResult = financial?.attributes?.vandaag || {};
    const exportStatus = this._obj(E.exportStatus);
    const exportAttrs = exportStatus?.attributes || {};
    const homeForecast = this._obj(E.homeForecast);
    const recovery = this._obj(E.runtimeRecovery);
    const rows = [
      ["Bedrijfsstatus", this._translateState(status?.state)],
      ["Reden", status?.attributes?.reden],
      ["Laatste actie", status?.attributes?.laatste_actie],
      ["Bedieningsmodus", this._state(E.switchStatus)],
      ["Handmatige status", manual?.attributes?.melding || manual?.state],
      ["Herstart-herstel", recovery?.attributes?.melding || recovery?.state],
      ["Netoffsetmodus", this._state(E.gridOffsetMode, "—")],
      ["Actieve netoffset", offsetAttrs.actieve_offset_w == null
        ? "—" : `${this._fmt(Number(offsetAttrs.actieve_offset_w), 0)} W`],
      ["Gecorrigeerd netvermogen",
        offsetAttrs.gecorrigeerd_netvermogen_w == null
          ? "—"
          : `${this._fmt(Number(offsetAttrs.gecorrigeerd_netvermogen_w), 0)} W`],
      ["Offsetkalibratie", offsetAttrs.kalibratiestatus],
      ["Kritieke sensordata", freshness?.state || "—"],
      ["Veiligheidsactie", freshness?.attributes?.actie || "—"],
      ["Verbruiksprofiel", homeForecast?.state == null
        ? "—"
        : `${homeForecast.state} W · betrouwbaarheid ${this._fmt(Number(homeForecast.attributes?.betrouwbaarheid_pct), 1)}%`],
      ["Teruglevermodus", exportAttrs.modus || "—"],
      ["Terugleverstatus", exportStatus?.state || "—"],
      ["Terugleverbeoordeling", exportAttrs.reden || "—"],
      ["Nettowinst terugleveren", exportAttrs.nettowinst_eur_kwh == null
        ? "—"
        : `${this._fmt(Number(exportAttrs.nettowinst_eur_kwh), 4)} €/kWh`],
      ["Zelfconsumptiereserve", exportAttrs.reserve_soc_pct == null
        ? "—"
        : `${this._fmt(Number(exportAttrs.reserve_soc_pct), 1)}%`],
      ["Laadkosten vandaag", todayResult.laadkosten_eur == null
        ? "—" : `${this._fmt(Number(todayResult.laadkosten_eur), 2)} €`],
      ["Terugleveropbrengst vandaag",
        todayResult.terugleveropbrengst_eur == null
          ? "—"
          : `${this._fmt(Number(todayResult.terugleveropbrengst_eur), 2)} €`],
    ];
    const verification = status?.attributes?.verificatie || manual?.attributes?.verificatie;
    this.$("status-details").innerHTML = `${rows.map(([k, v]) => `<div class="status-row"><span>${k}</span><b>${this._escape(v || "—")}</b></div>`).join("")}
      <div class="verification-section"><span>Verificatie</span>${this._verificationHtml(verification)}</div>`;
  }

  _renderBatterySelectorStatus() {
    const root = this.$("battery-selector-status-content");
    if (!root) return;
    const sensor = this._obj(E.batterySelectorStatus);
    if (!sensor) {
      root.innerHTML = '<div class="empty">De eerste selectorstatus wordt nog verzameld</div>';
      return;
    }
    const attrs = sensor.attributes || {};
    const batteries = Array.isArray(attrs.actieve_accus) ? attrs.actieve_accus : [];
    const batteryCards = batteries.length
      ? batteries.map((item) => {
        const name = item.naam || `Accu ${(Number(item.index) || 0) + 1}`;
        const soc = item.soc == null ? "—" : `${this._fmt(Number(item.soc), 0)}%`;
        const target = item.doel_soc == null ? "—" : `${this._fmt(Number(item.doel_soc), 0)}%`;
        return `<div class="selector-battery"><b>${this._escape(name)}</b><span>${this._escape(soc)} → ${this._escape(target)}</span></div>`;
      }).join("")
      : '<div class="selector-battery"><b>Geen accu geselecteerd</b></div>';
    const timer = attrs.timer;
    const timerText = timer
      ? `${timer.richting || "wachten"} naar ${timer.doel_aantal || "—"} accu’s · ${timer.verstreken_s || 0}/${timer.vereist_s || "—"} s`
      : "Geen omschakeltimer actief";
    const demand = attrs.berekende_vraag_w == null
      ? "—" : `${this._fmt(Number(attrs.berekende_vraag_w), 0)} W`;
    root.innerHTML = `
      <div class="selector-summary">
        <span class="selector-state">${this._escape(sensor.state || "—")}</span>
        <div class="selector-batteries">${batteryCards}</div>
        <div class="selector-demand"><span>Woningvraag</span><b>${this._escape(demand)}</b></div>
      </div>
      <div class="selector-meta">
        <div><span>Vermogensregeling</span><b>${this._escape(attrs.vermogensregeling || "—")}</b></div>
        <div><span>Geselecteerd op</span><b>${this._escape(attrs.selectiereden || "—")}</b></div>
        <div><span>Omschakeling</span><b>${this._escape(timerText)}</b></div>
      </div>
      ${attrs.melding ? `<p class="selector-message">${this._escape(attrs.melding)}</p>` : ""}
      ${attrs.terugvalreden ? `<p class="selector-fallback"><b>Terugval:</b> ${this._escape(attrs.terugvalreden)}</p>` : ""}`;
  }

  _renderEfficiencyStatus() {
    const root = this.$("efficiency-status-content");
    if (!root) return;
    const chargeMode = this._state(
      E.chargeEfficiencyMode, "Geen rendement",
    );
    const dischargeMode = this._state(
      E.dischargeEfficiencyMode, "Geen rendement",
    );
    root.innerHTML = `<div class="efficiency-status-head"><span>Gebruik door planners</span><b>Laden: ${this._escape(chargeMode)} · Ontladen: ${this._escape(dischargeMode)}</b></div>
      <div class="efficiency-status-grid">${E.batteries.map((battery) => {
        const sensor = this._obj(battery.learnedEfficiency);
        const charge = sensor?.attributes?.laden || {};
        const discharge = sensor?.attributes?.ontladen || {};
        return `<article><b>Accu ${battery.n}</b>
          <div><span>Laden</span><strong>${charge.rendement_pct == null ? "nog lerend" : `${this._fmt(Number(charge.rendement_pct), 1)}%`}</strong></div>
          <div><span>Betrouwbaarheid laden</span><strong>${this._escape(charge.betrouwbaarheid || "nog lerend")}</strong></div>
          <div><span>Voorkeursvermogen laden</span><strong>${charge.voorkeursvermogen_w == null ? "—" : `${this._fmt(Number(charge.voorkeursvermogen_w), 0)} W`}</strong></div>
          <div><span>Ontladen</span><strong>${discharge.rendement_pct == null ? "nog lerend" : `${this._fmt(Number(discharge.rendement_pct), 1)}%`}</strong></div>
          <div><span>Betrouwbaarheid ontladen</span><strong>${this._escape(discharge.betrouwbaarheid || "nog lerend")}</strong></div>
          <div><span>Voorkeursvermogen ontladen</span><strong>${discharge.voorkeursvermogen_w == null ? "—" : `${this._fmt(Number(discharge.voorkeursvermogen_w), 0)} W`}</strong></div>
          <small>${this._escape(sensor?.attributes?.uitleg || "OpenEMS verzamelt passief geldige gebruikssessies.")}</small>
        </article>`;
      }).join("")}</div>`;
  }

  _renderResults() {
    const root = this.$("financial-results");
    if (!root) return;
    const sensor = this._obj(E.results);
    if (!sensor) {
      root.innerHTML = '<div class="empty">De eerste meetperiode wordt nog verzameld</div>';
      return;
    }
    const inventory = sensor.attributes?.opgeslagen_energie || {};
    const periods = [
      ["Vandaag", sensor.attributes?.vandaag || {}],
      ["Gisteren", sensor.attributes?.gisteren || {}],
      ["Afgelopen 7 dagen", sensor.attributes?.afgelopen_7_dagen || {}],
    ];
    const rows = [
      ["Net afgenomen", "netafname_kwh", "kWh"],
      ["Teruggeleverd", "teruglevering_kwh", "kWh"],
      ["Accu geladen", "laadenergie_kwh", "kWh"],
      ["Accu ontladen", "ontlaadenergie_kwh", "kWh"],
      ["Laadkosten", "laadkosten_eur", "€"],
      ["Terugleveropbrengst", "terugleveropbrengst_eur", "€"],
      ["Gemiddelde laadprijs", "gemiddelde_laadprijs", "€/kWh"],
    ];
    const average = inventory.gemiddelde_kostprijs_per_kwh;
    root.innerHTML = `
      <article class="card inventory-result">
        ${this._heading("mdi:battery-charging-medium", "Energie die nu in de accu's zit")}
        <div class="inventory-kpis">
          <div><span>Gemiddelde betaalde laadprijs</span><b>${average == null ? "—" : `${this._fmt(Number(average), 4)} €/kWh`}</b></div>
          <div><span>Bruikbare energie</span><b>${inventory.bruikbaar_kwh == null ? "—" : `${this._fmt(Number(inventory.bruikbaar_kwh), 2)} kWh`}</b></div>
          <div><span>Totale kostprijs</span><b>${inventory.totale_kostprijs_eur == null ? "—" : `${this._fmt(Number(inventory.totale_kostprijs_eur), 2)} €`}</b></div>
        </div>
      </article>
      ${this._learnedResultsHtml()}` + periods.map(([label, values]) => `
      <article class="card result-period">
        ${this._heading("mdi:cash-multiple", label)}
        <div class="result-quality"><span>Datakwaliteit</span><b>${this._escape(values.kwaliteitslabel || "nog geen meetperiode")}</b></div>
        <div class="result-rows">${rows.map(([name, field, unit]) => `
          <div><span>${name}</span><b>${values[field] == null ? "—" : `${this._fmt(Number(values[field]), field.includes("prijs") ? 4 : 2)} ${unit}`}</b></div>`).join("")}
        </div>
      </article>`).join("");
    this._wireEfficiencyResults();
  }

  _learnedResultsHtml() {
    const direction = this._efficiencyResultDirection === "laden"
      ? "laden" : "ontladen";
    return `<article class="card inventory-result efficiency-result-card">
      <div class="efficiency-result-heading">
        ${this._heading("mdi:chart-bar", "Rendement per vermogensgebied")}
        <div class="efficiency-direction-toggle" role="group" aria-label="Rendementsrichting">
          ${["laden", "ontladen"].map((item) => `<button type="button"
            data-efficiency-direction="${item}" aria-pressed="${direction === item}"
            class="${direction === item ? "active" : ""}">${item === "laden" ? "Laden" : "Ontladen"}</button>`).join("")}
        </div>
      </div>
      <p class="efficiency-result-intro">Betrouwbare metingen per 250 W. Klik op een vermogensgebied voor de onderliggende meetgegevens.</p>
      <div class="efficiency-profile-grid">${E.batteries.map(
        (battery) => this._efficiencyProfileCardHtml(battery, direction)
      ).join("")}</div>
    </article>`;
  }

  _efficiencyProfileCardHtml(battery, direction) {
    const learned = this._obj(battery.learnedEfficiency);
    const profile = learned?.attributes?.[direction] || {};
    const bins = Array.isArray(profile.vermogensgebieden)
      ? profile.vermogensgebieden.filter((item) => (
          Number.isFinite(Number(item?.vermogen_w))
          && Number.isFinite(Number(item?.rendement_pct))
        )).sort((a, b) => Number(a.vermogen_w) - Number(b.vermogen_w))
      : [];
    const preferred = Number(profile.voorkeursvermogen_w);
    const selected = this._efficiencyResultSelection;
    const selectedPoint = selected?.direction === direction
      && selected?.battery === battery.n
      ? bins.find((item) => Number(item.vermogen_w) === selected.power)
      : null;
    const bars = bins.length ? bins.map((item) => {
      const power = Number(item.vermogen_w);
      const efficiency = Number(item.rendement_pct);
      const level = Math.max(5, Math.min(100, (efficiency - 60) / 40 * 100));
      const confidence = String(item.betrouwbaarheid || "nog lerend");
      const confidenceClass = confidence === "hoog" ? "high"
        : confidence === "redelijk" ? "fair" : "learning";
      const isSelected = selectedPoint && Number(selectedPoint.vermogen_w) === power;
      const isPreferred = Number.isFinite(preferred) && preferred === power;
      return `<button type="button" class="efficiency-bin ${confidenceClass}${isSelected ? " selected" : ""}${isPreferred ? " preferred" : ""}"
        data-efficiency-battery="${battery.n}" data-efficiency-direction="${direction}"
        data-efficiency-power="${power}" aria-pressed="${isSelected ? "true" : "false"}"
        title="${this._fmt(power, 0)} W · ${this._fmt(efficiency, 1)}% · ${this._escape(confidence)}">
        <span class="efficiency-bin-value">${this._fmt(efficiency, 1)}%</span>
        <span class="efficiency-bar-track"><i style="height:${level.toFixed(1)}%"></i></span>
        <span class="efficiency-bin-power">${this._fmt(power, 0)} W</span>
        ${isPreferred ? '<span class="efficiency-preferred" aria-label="Voorkeursvermogen">★</span>' : ""}
      </button>`;
    }).join("") : '<div class="efficiency-profile-empty">Nog geen geldige vermogensgebieden</div>';
    const overall = profile.rendement_pct == null
      ? "nog lerend" : `${this._fmt(Number(profile.rendement_pct), 1)}%`;
    const preferredLabel = Number.isFinite(preferred)
      ? `${this._fmt(preferred, 0)} W` : "nog niet betrouwbaar";
    let details = "";
    if (selectedPoint) {
      const last = selectedPoint.laatste_meting
        ? new Date(selectedPoint.laatste_meting) : null;
      const lastLabel = last && !Number.isNaN(last.getTime())
        ? last.toLocaleString("nl-NL", {
            day: "2-digit", month: "2-digit", year: "numeric",
            hour: "2-digit", minute: "2-digit",
          })
        : "—";
      details = `<div class="efficiency-point-detail">
        <div><span>Vermogen</span><b>${this._fmt(Number(selectedPoint.vermogen_w), 0)} W</b></div>
        <div><span>Rendement</span><b>${this._fmt(Number(selectedPoint.rendement_pct), 1)}%</b></div>
        <div><span>Betrouwbaarheid</span><b>${this._escape(selectedPoint.betrouwbaarheid || "nog lerend")}</b></div>
        <div><span>Sessies</span><b>${Number(selectedPoint.sessies || 0)}</b></div>
        <div><span>Gemeten energie</span><b>${this._fmt(Number(selectedPoint.energie_kwh || 0), 2)} kWh</b></div>
        <div><span>Meetduur</span><b>${this._fmt(Number(selectedPoint.duur_uren || 0), 1)} uur</b></div>
        <div class="efficiency-detail-wide"><span>Laatste meting</span><b>${this._escape(lastLabel)}</b></div>
      </div>`;
    }
    return `<article class="efficiency-profile-card">
      <header><div><b>Accu ${battery.n}</b><small>${direction === "laden" ? "Laden" : "Ontladen"}</small></div>
        <div><span>Gemiddeld</span><strong>${overall}</strong></div></header>
      <div class="efficiency-profile-meta"><span>Betrouwbaarheid <b>${this._escape(profile.betrouwbaarheid || "nog lerend")}</b></span><span>Voorkeur <b>${preferredLabel}</b></span></div>
      <div class="efficiency-chart-scale"><span>100%</span><span>80%</span><span>60%</span></div>
      <div class="efficiency-bin-chart">${bars}</div>
      ${details}
    </article>`;
  }

  _wireEfficiencyResults() {
    this.shadowRoot.querySelectorAll("[data-efficiency-direction]").forEach((button) => {
      if (button.hasAttribute("data-efficiency-battery")) return;
      button.addEventListener("click", () => {
        this._efficiencyResultDirection = button.dataset.efficiencyDirection;
        this._efficiencyResultSelection = null;
        this._renderResults();
      });
    });
    this.shadowRoot.querySelectorAll("[data-efficiency-power]").forEach((button) => {
      button.addEventListener("click", () => {
        const next = {
          direction: button.dataset.efficiencyDirection,
          battery: Number(button.dataset.efficiencyBattery),
          power: Number(button.dataset.efficiencyPower),
        };
        const current = this._efficiencyResultSelection;
        this._efficiencyResultSelection = current
          && current.direction === next.direction
          && current.battery === next.battery
          && current.power === next.power ? null : next;
        this._renderResults();
      });
    });
  }

  _patchGridExport() {
    const sensor = this._obj(E.exportStatus);
    const attrs = sensor?.attributes || {};
    const mode = this._state(E.exportMode, "Alleen simuleren");
    this._setSelect("export-mode-select", mode);
    this._setSelect(
      "export-reserve-mode",
      this._state(E.exportReserveMode, "Vast"),
    );
    this._setSelect(
      "before-charge-reserve-mode",
      this._state(E.beforeChargeReserveMode, "Vast"),
    );
    this._patchDynamicExportReserve(attrs);
    const status = this.$("export-setting-status");
    if (status) {
      status.textContent = `${attrs.modus || mode} · ${attrs.reden || "Nog geen beoordeling"}`;
    }
  }

  _patchDynamicExportReserve(attrs) {
    const beforeMode = this._state(E.beforeChargeReserveMode, "Vast");
    const afterMode = this._state(E.exportReserveMode, "Vast");
    const status = this.$("dynamic-export-reserve-status");
    if (!status) return;
    const activeReserve = Number(attrs.reserve_soc_pct);
    const current = Number.isFinite(activeReserve)
      ? ` / huidige plannerreserve ${this._fmt(activeReserve, 0)}%`
      : "";
    status.textContent = `Voor laden: ${beforeMode} / Na laden: ${afterMode}${current}`;
  }

  _patchEfficiencySettings() {
    const chargeMode = this._state(
      E.chargeEfficiencyMode, "Geen rendement",
    );
    const dischargeMode = this._state(
      E.dischargeEfficiencyMode, "Geen rendement",
    );
    const exportMode = this._state(
      E.exportEfficiencyMode, "Geen rendement",
    );
    this._setSelect("charge-efficiency-mode", chargeMode);
    this._setSelect("discharge-efficiency-mode", dischargeMode);
    this._setSelect("export-efficiency-mode", exportMode);
    E.settings.forEach(([, , , , kind], index) => {
      const expectedMode = kind === "manual-charge-efficiency"
        ? chargeMode : kind === "manual-discharge-efficiency"
          ? dischargeMode : kind === "manual-export-efficiency"
            ? exportMode : null;
      if (expectedMode == null) return;
      const input = this.$(`setting-${index}-input`);
      const card = input?.closest(".setting");
      const enabled = expectedMode === "Handmatig rendement";
      if (input) input.disabled = !enabled;
      if (card) card.hidden = !enabled;
    });
    E.settings.forEach(([, , , , kind], index) => {
      const card = this.$(`setting-${index}-input`)?.closest(".setting");
      if (!card) return;
      if (kind === "before-charge-reserve") {
        card.hidden = this._state(E.beforeChargeReserveMode, "Vast") !== "Vast";
      } else if (kind === "after-charge-reserve") {
        card.hidden = this._state(E.exportReserveMode, "Vast") !== "Vast";
      } else if (kind === "dynamic-export-reserve") {
        card.hidden = this._state(E.beforeChargeReserveMode, "Vast") !== "Dynamisch"
          && this._state(E.exportReserveMode, "Vast") !== "Dynamisch";
      }
    });
    const status = this.$("automatic-efficiency-status");
    if (status) {
      status.textContent = `Laden: ${chargeMode} · Ontladen: ${dischargeMode}`;
    }
    const exportStatus = this.$("export-efficiency-status");
    if (exportStatus) {
      exportStatus.textContent = `Terugleverplanning: ${exportMode}`;
    }
  }

  _directionEfficiency(direction, battery) {
    const charging = direction === "laden";
    const mode = this._state(
      charging ? E.chargeEfficiencyMode : E.dischargeEfficiencyMode,
      "Geen rendement",
    );
    if (mode === "Handmatig rendement") {
      const value = this._num(charging
        ? "number.openems_handmatig_laadrendement"
        : "number.openems_handmatig_ontlaadrendement");
      return Number.isFinite(value)
        ? Math.min(1, Math.max(0.6, value / 100)) : 1;
    }
    if (mode === "Zelfgeleerd rendement") {
      const values = this._obj(
        battery.learnedEfficiency,
      )?.attributes?.[direction] || {};
      const value = Number(values.rendement_pct);
      if (values.betrouwbaarheid === "hoog" && Number.isFinite(value)) {
        return Math.min(1, Math.max(0.6, value / 100));
      }
    }
    return 1;
  }

  _patchFrankSettings() {
    const entities = this._frankEntities();
    const pairs = [
      ["frank-allin-sensor-input", entities.allIn],
      ["frank-market-sensor-input", entities.market],
    ];
    pairs.forEach(([id, value]) => {
      const input = this.$(id);
      this._syncControlValue(input, value || "");
    });
  }

  _frankEntities() {
    return {
      allIn: this._state(E.frankAllInHelper, E.frank),
      market: this._state(E.frankMarketHelper, E.frankMarket),
    };
  }

  _renderAssignedHourBlocks() {
    const root = this.$("assigned-hour-blocks");
    if (!root) return;
    const planning = this._obj(E.planning);
    if (!planning) {
      root.innerHTML = '<div class="empty">Nog geen urenplanning beschikbaar</div>';
      return;
    }
    const attrs = planning.attributes || {};
    const charge = attrs.laadplan || {};
    const discharge = attrs.ontlaadplan || {};
    const exportPlan = attrs.terugleverplan || {};
    const homeForecastSensor = this._obj(E.homeForecast);
    const homeForecast = Array.isArray(
      homeForecastSensor?.attributes?.profiel_per_kwartier
    ) ? homeForecastSensor.attributes.profiel_per_kwartier : [];
    const quietHomePower = Number(homeForecastSensor?.state);
    const hourMs = 60 * 60 * 1000;
    const quarterMs = 15 * 60 * 1000;
    const firstHour = new Date();
    firstHour.setMinutes(0, 0, 0);
    const overlaps = (block, start, end) => {
      const blockStart = new Date(block?.start);
      const blockEnd = new Date(block?.einde);
      return !Number.isNaN(blockStart.getTime())
        && !Number.isNaN(blockEnd.getTime())
        && blockStart < end && blockEnd > start;
    };
    const summedBatteryPower = (items, field, start, end) =>
      (Array.isArray(items) ? items : []).reduce((sum, battery) => {
        const segments = Array.isArray(battery.segmenten)
          ? battery.segmenten.filter((segment) =>
            overlaps(segment, start, end)
          ) : [];
        if (segments.length) {
          return sum + Math.max(
            ...segments.map((segment) => Number(segment.vermogen_w) || 0)
          );
        }
        return sum + (Number(battery[field]) || 0);
      }, 0);
    const plannedPower = (mode, start, end) => {
      if (mode === "charge") {
        return summedBatteryPower(
          charge.per_accu, "laadvermogen_w", start, end
        );
      }
      if (mode === "self") {
        return summedBatteryPower(
          discharge.per_accu, "verwacht_vermogen_w", start, end
        );
      }
      if (mode === "export") {
        const values = (Array.isArray(exportPlan.blokken)
          ? exportPlan.blokken : [])
          .filter((block) => overlaps(block, start, end))
          .map((block) => Number(block.doel_export_w) || 0);
        return values.length ? Math.max(...values) : Number(
          exportPlan.gepland_exportvermogen_w
        ) || 0;
      }
      return 0;
    };
    const forecastFor = (start, end) => {
      const durationMs = end.getTime() - start.getTime();
      let coveredMs = 0;
      let energyWh = 0;
      let fallbackUsed = false;
      for (const item of homeForecast) {
        const itemStart = new Date(item?.start);
        if (Number.isNaN(itemStart.getTime())) continue;
        const itemEnd = new Date(itemStart.getTime() + quarterMs);
        const overlapMs = Math.max(0, Math.min(
          end.getTime(), itemEnd.getTime()
        ) - Math.max(start.getTime(), itemStart.getTime()));
        if (!overlapMs) continue;
        const profilePower = Number(item.vermogen_w);
        const power = Number.isFinite(profilePower)
          ? profilePower : quietHomePower;
        if (!Number.isFinite(power)) continue;
        coveredMs += overlapMs;
        energyWh += power * overlapMs / hourMs;
        fallbackUsed ||= !Number.isFinite(profilePower);
      }
      const missingMs = Math.max(0, durationMs - coveredMs);
      if (missingMs > 1) {
        if (!Number.isFinite(quietHomePower)) {
          return { power: null, energyKwh: null, fallbackUsed: true };
        }
        energyWh += quietHomePower * missingMs / hourMs;
        fallbackUsed = true;
      }
      return {
        power: durationMs > 0 ? energyWh / durationMs * hourMs : null,
        energyKwh: energyWh / 1000,
        fallbackUsed,
      };
    };
    const labels = {
      charge: "Laden",
      self: "Zelfconsumptie",
      export: "Terugleveren",
      standby: "Rust",
    };
    const icons = {
      charge: "mdi:battery-arrow-up",
      self: "mdi:home-battery",
      export: "mdi:transmission-tower-export",
      standby: "mdi:power-sleep",
    };
    // Na de laatste bekende Frank-prijs vult het voorspelde plan de uren
    // aan. Dat plan is alleen indicatief: er wordt niets mee aangestuurd.
    const forecastModes = this._forecastPlanModes();
    if (forecastModes.size && !this._lastFrankEndMs) {
      // Bepaal de laatste Frank-prijs ook als de prijsgrafiek nog niet is getekend.
      try { this._priceData(); } catch (_error) { /* zonder grens: alleen voorspelde dagen */ }
    }
    const lastFrankEnd = Number(this._lastFrankEndMs) || 0;
    const lastForecastStart = Math.max(0, ...forecastModes.keys());
    const slotCount = Math.min(
      72,
      Math.max(
        24,
        Math.ceil((lastForecastStart + hourMs - firstHour.getTime()) / hourMs),
      ),
    );
    const slots = Array.from({ length: slotCount }, (_, index) => {
      const start = new Date(firstHour.getTime() + index * hourMs);
      const end = new Date(start.getTime() + hourMs);
      const planned = this._plannedPriceModeAt(start, hourMs);
      const forecastMode = planned || start.getTime() < lastFrankEnd
        ? null : forecastModes.get(start.getTime());
      const forecast = Boolean(forecastMode);
      const mode = planned || forecastMode || "standby";
      const power = forecast ? 0 : plannedPower(mode, start, end);
      const usage = forecastFor(start, end);
      return {
        start, end, mode, power, forecast, current: index === 0,
        homePower: usage.power,
        homeEnergyKwh: usage.energyKwh,
        homeFallback: usage.fallbackUsed,
      };
    });
    const rows = groupAssignedHourSlots(slots).map((block) => {
      const { start, end, mode, power, current, forecast } = block;
      const key = start.toISOString();
      const expandable = block.slots.length > 1;
      const expanded = expandable
        && this._expandedAssignedHourBlocks.has(key);
      const startDay = start.toLocaleDateString("nl-NL", {
        weekday: "short", day: "2-digit", month: "2-digit",
      });
      const endDay = new Date(end.getTime() - 1).toLocaleDateString("nl-NL", {
        weekday: "short", day: "2-digit", month: "2-digit",
      });
      const groupedDay = startDay === endDay
        ? startDay : `${startDay} - ${endDay}`;
      const day = groupedDay;
      const selfEnergy = block.slots.reduce((sum, slot) => (
        sum + (Number(slot.homeEnergyKwh) || 0)
      ), 0);
      const value = mode === "self"
        ? (selfEnergy > 0 ? `${this._fmt(selfEnergy, 2)} kWh` : "")
        : (power >= 1 ? `${this._fmt(power, 0)} W` : "");
      const details = expanded ? `<div class="assigned-hour-details">${block.slots.map((slot) => {
        const detailPower = slot.mode === "self"
          ? Number(slot.homePower) : Number(slot.power);
        return `<div class="assigned-hour-detail">
          <span>${slot.start.toLocaleTimeString("nl-NL", {
            hour: "2-digit", minute: "2-digit",
          })}–${slot.end.toLocaleTimeString("nl-NL", {
            hour: "2-digit", minute: "2-digit",
          })}</span>
          <b>${labels[slot.mode]}</b>
          <em>${Number.isFinite(detailPower) && detailPower >= 1
            ? `${this._fmt(detailPower, 0)} W` : ""}</em>
        </div>`;
      }).join("")}</div>` : "";
      return `<div class="assigned-hour-group${expanded ? " expanded" : ""}"
        data-assigned-block="${key}" data-expandable="${expandable}">
        <div class="assigned-hour ${mode}${current ? " current" : ""}${forecast ? " forecast" : ""}">
        <div><span>${day}</span><b>${start.toLocaleTimeString("nl-NL", {
          hour: "2-digit", minute: "2-digit",
        })}–${end.toLocaleTimeString("nl-NL", {
          hour: "2-digit", minute: "2-digit",
        })}</b></div>
        <ha-icon icon="${icons[mode]}"></ha-icon>
        <strong>${labels[mode]}${forecast ? ' <i class="assigned-hour-forecast">voorspeld</i>' : ""}</strong>
        <em>${value}</em>
        ${current ? '<small>Huidig uur</small>' : ""}
        ${expandable ? `<ha-icon class="assigned-hour-chevron" icon="mdi:chevron-${expanded ? "up" : "down"}"></ha-icon>` : ""}
        </div>${details}
      </div>`;
    });
    root.innerHTML = `<div class="assigned-hours-list">${rows.join("")}</div>`;
  }

  _forecastPlanModes() {
    // Uren van het voorspelde plan (sensor.openems_voorspeld_plan) per starttijd.
    const status = this._obj(E.priceForecastStatus);
    const plan = this._obj(E.priceForecastPlan);
    const result = new Map();
    if (
      status?.state !== "actief"
      || plan?.state !== "aan"
      || !Array.isArray(plan.attributes?.uren)
    ) return result;
    const modes = { laden: "charge", zelf: "self", standby: "standby" };
    for (const hour of plan.attributes.uren) {
      const time = new Date(hour?.van).getTime();
      const mode = modes[String(hour?.modus || "").toLowerCase()];
      if (Number.isFinite(time) && mode) result.set(time, mode);
    }
    return result;
  }

  _renderAdviceSummaries() {
    const planning = this._obj(E.planning);
    const roots = {
      charge: this.$("charge-advice-summary"),
      self: this.$("self-advice-summary"),
      export: this.$("export-advice-summary"),
    };
    if (!Object.values(roots).every(Boolean)) return;
    if (!planning) {
      Object.values(roots).forEach((root) => {
        root.innerHTML = '<div class="empty">Nog geen advies beschikbaar</div>';
      });
      return;
    }
    const attrs = planning.attributes || {};
    const charge = attrs.laadplan || {};
    const discharge = attrs.ontlaadplan || {};
    const exportPlan = attrs.terugleverplan || {};
    const now = new Date();
    const blocks = (plan) => Array.isArray(plan?.blokken) ? plan.blokken : [];
    const activeBlock = (plan) => blocks(plan).find((block) => {
      const start = new Date(block.start);
      const end = new Date(block.einde);
      return !Number.isNaN(start.getTime()) && start <= now && now < end;
    });
    const nextBlock = (plan) => blocks(plan)
      .filter((block) => new Date(block.start) > now)
      .sort((left, right) => new Date(left.start) - new Date(right.start))[0];
    const period = (block) => {
      if (!block) return "Geen blok gepland";
      const start = new Date(block.start);
      const end = new Date(block.einde);
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
        return "Geen geldig tijdvak";
      }
      return `${start.toLocaleDateString("nl-NL", {
        weekday: "short", day: "2-digit", month: "2-digit",
      })} · ${start.toLocaleTimeString("nl-NL", {
        hour: "2-digit", minute: "2-digit",
      })}–${end.toLocaleTimeString("nl-NL", {
        hour: "2-digit", minute: "2-digit",
      })}`;
    };
    const metric = (label, value, unit = "") =>
      `<div><span>${label}</span><b>${value}${unit ? ` ${unit}` : ""}</b></div>`;
    const number = (value, decimals = 0) => value == null
      ? "—" : this._fmt(Number(value), decimals);
    const summary = (plan, metrics, activeLabel, nextLabel, details = "") => {
      const active = activeBlock(plan);
      const next = nextBlock(plan);
      return `<div class="advice-state ${active ? "active" : ""}">
          <span>${active ? activeLabel : next ? nextLabel : "Geen blok gepland"}</span>
          <b>${period(active || next)}</b>
        </div>
        <div class="advice-kpis">${metrics.join("")}</div>
        ${details}`;
    };
    const blockRows = (plan, className, emptyText, detail) => {
      const items = blocks(plan);
      if (!items.length) {
        return `<div class="planning-none">${emptyText}</div>`;
      }
      return items.map((block, index) => `
        <div class="planning-block ${className}">
          <div><span>Blok ${index + 1}</span><small>${period(block)}</small></div>
          <div>${detail(block)}</div>
        </div>`).join("");
    };
    const chargePowers = (Array.isArray(charge.per_accu)
      ? charge.per_accu : []).map((battery) => `
        <div class="planning-power-row">
          <span>${this._escape(battery.naam)}</span>
          <b>${number(battery.laadvermogen_w)} W</b>
        </div>`).join("")
      || '<div class="planning-none">Geen laadvermogen berekend</div>';
    const chargeDetails = `
      <div class="advice-details-title"><b>Laadvermogen per accu</b><span>${number(charge.gepland_venster_uren, 2)} uur</span></div>
      <div class="advice-details">${chargePowers}</div>
      <div class="advice-details-title"><b>Geselecteerde laadblokken</b></div>
      <div class="advice-details">${blockRows(
        charge, "charge-block", "Geen laadblokken geselecteerd",
        (block) => `<b>${number(block.prijs, 4)} €/kWh</b>`
      )}</div>`;
    roots.charge.innerHTML = summary(charge, [
      metric("Goedkoopste prijs", number(charge.goedkoopste_prijs, 4), "€/kWh"),
      metric("Toegestane grens", number(charge.prijsgrens, 4), "€/kWh"),
      metric("Laadprijsmarge", number(charge.laadprijsmarge, 4), "€/kWh"),
      metric("Minimale laadtijd", number(charge.minimale_laadtijd_uren, 2), "uur"),
      metric("Nu gepland", number(charge.actief_vermogen_w), "W"),
      metric("Piekvermogen", number(charge.totaal_vermogen_w), "W"),
      metric("Gemiddelde prijs", number(charge.gemiddelde_prijs, 4), "€/kWh"),
    ], "Laden is nu gepland", "Volgend laadblok", chargeDetails);
    const selfPower = (Array.isArray(discharge.per_accu)
      ? discharge.per_accu : []).reduce(
      (sum, battery) => sum + (Number(battery.verwacht_vermogen_w) || 0), 0
    );
    const selfDetails = `
      <div class="advice-details-title"><b>Geselecteerde ontlaadblokken</b></div>
      <div class="advice-details">${blockRows(
        discharge, "discharge-block",
        "Geen winstgevende ontlaadblokken gevonden",
        (block) => `<b>${number(block.gemiddelde_prijs, 4)} €/kWh</b>
          <small>hoogste ${number(block.hoogste_prijs, 4)} · verschil +${number(block.prijsverschil, 4)}</small>`
      )}</div>`;
    roots.self.innerHTML = summary(discharge, [
      metric("All-in laadreferentie", number(discharge.gemiddelde_laadprijs, 4), "€/kWh"),
      metric("Ontlaadmarge", number(discharge.ontlaadmarge, 4), "€/kWh"),
      metric("Gepland vermogen", number(selfPower), "W"),
      metric("Ontladen vanaf", number(discharge.ontladen_vanaf, 4), "€/kWh"),
      metric("Beschikbaar", number(discharge.beschikbare_energie_kwh, 2), "kWh"),
      metric("Minimum-SOC", number(discharge.minimum_soc, 1), "%"),
      metric("Tijdresolutie", number(discharge.tijdresolutie_minuten), "min"),
    ], "Zelfconsumptie is nu gepland", "Volgend zelfconsumptieblok", selfDetails);
    const exportDetails = `
      <div class="advice-details-title"><b>Voorspelde terugleverblokken</b></div>
      <div class="advice-details">${blockRows(
        exportPlan, "export-advice-block",
        "Geen terugleverblokken geselecteerd",
        (block) => `<b>${number(block.gemiddelde_prijs, 4)} €/kWh</b>
          <small>${number(block.doel_export_w)} W · ${number(block.verwachte_export_kwh, 3)} kWh</small>`
      )}</div>`;
    roots.export.innerHTML = summary(exportPlan, [
      metric("Modus", this._escape(exportPlan.modus || "Uit")),
      metric("Voorraadkostprijs", number(exportPlan.voorraadkostprijs_eur_kwh, 4), "€/kWh"),
      metric("Gepland vermogen", number(exportPlan.gepland_exportvermogen_w), "W"),
      metric("Toegestaan vanaf", number(exportPlan.toegestaan_vanaf_eur_kwh, 4), "€/kWh"),
      metric("Beschikbaar", number(exportPlan.beschikbare_energie_kwh, 2), "kWh"),
      metric("Marge onder dagpiek", number(exportPlan.piekmarge_eur_kwh, 4), "€/kWh"),
      metric("Reserve eigen verbruik", number(exportPlan.reserve_soc_pct, 1), "%"),
    ], "Terugleveren is nu gepland", "Volgend terugleverblok", exportDetails);
  }

  _renderPlanningStatus() {
    const root = this.$("planning-status-content");
    if (!root) return;
    const planning = this._obj(E.planning);
    if (!planning) {
      root.innerHTML = '<div class="empty planning-empty">Nog geen planningsgegevens beschikbaar</div>';
      return;
    }
    const charge = planning.attributes?.laadplan || {};
    const discharge = planning.attributes?.ontlaadplan || {};
    const powers = Array.isArray(charge.per_accu) ? charge.per_accu : [];
    const metric = (label, value, unit = "") => `
      <div class="planning-metric"><span>${label}</span><b>${value}</b><small>${unit}</small></div>`;
    const value = (raw, decimals) => this._fmt(
      raw == null ? Number.NaN : Number(raw), decimals
    );
    const period = (startValue, endValue) => {
      const start = new Date(startValue);
      const end = new Date(endValue);
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "—";
      const date = start.toLocaleDateString("nl-NL", { day: "2-digit", month: "2-digit" });
      const time = (value) => value.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
      return `${date} · ${time(start)}–${time(end)}`;
    };
    const batteryCards = powers.length ? powers.map((battery) => {
      const segments = Array.isArray(battery.segmenten)
        ? battery.segmenten : [];
      const tone = battery.status === "laden" ? "active"
        : battery.status === "voltooid" ? "done"
          : battery.status === "afgelopen" ? "late" : "waiting";
      const powerStrategy = ({
        voorkeursbereik: "Efficiënt verdeeld",
        noodzakelijk_hoger: "Hoger vermogen noodzakelijk",
        kleine_energievraag: "Korte kleine lading",
        geen_lading_nodig: "Geen lading nodig",
        geen_planning: "Geen planning",
      })[battery.vermogensstrategie] || battery.vermogensstrategie || "—";
      const segmentHtml = segments.length ? segments.map((segment) => `
        <div class="battery-plan-segment">
          <span>${period(segment.start, segment.einde)}</span>
          <b>${value(segment.vermogen_w, 0)} W</b>
          <small>${value(segment.prijs, 4)} €/kWh</small>
        </div>`).join("") : '<div class="planning-none">Geen laadmoment nodig</div>';
      return `<section class="battery-plan-card ${tone}">
        <header><div><b>${this._escape(battery.naam)}</b><small>${this._escape(battery.reden)}</small></div><span>${this._escape(battery.status)}</span></header>
        <div class="battery-plan-soc"><b>${value(battery.huidige_soc, 1)}%</b><i></i><b>${value(battery.doel_soc, 1)}%</b></div>
        <div class="battery-plan-values">
          <div><span>Benodigd in accu</span><b>${value(battery.benodigd_in_accu_kwh, 3)} kWh</b></div>
          <div><span>Benodigd uit net</span><b>${value(battery.benodigd_uit_net_kwh, 3)} kWh</b></div>
          <div><span>Laadrendement</span><b>${value(battery.laadrendement_pct, 1)}%</b></div>
          <div><span>Berekend laadverlies</span><b>${value(battery.berekend_laadverlies_kwh, 3)} kWh</b></div>
          <div><span>Laadvermogen</span><b>${value(battery.laadvermogen_w, 0)} W</b></div>
          <div><span>Laadduur</span><b>${value(Number(battery.laadduur_uren) * 60, 0)} min</b></div>
          <div><span>Gemiddelde prijs</span><b>${value(battery.gemiddelde_prijs, 4)} €/kWh</b></div>
          <div><span>Minimumvermogen</span><b>${value(battery.minimum_vermogen_w, 0)} W</b></div>
          <div><span>Voorkeursbereik</span><b>${value(battery.voorkeur_minimum_w, 0)}–${value(battery.voorkeur_maximum_w, 0)} W</b></div>
          <div><span>Vermogenskeuze</span><b>${this._escape(powerStrategy)}</b></div>
        </div>
        <div class="battery-plan-timeline">${segmentHtml}</div>
      </section>`;
    }).join("") : '<div class="planning-none">Geen individuele accuplanning beschikbaar</div>';

    root.innerHTML = `
      <div class="planning-metrics">
        ${metric("Gemiddelde prijs gepland laadblok", value(charge.gemiddelde_prijs, 4), "€/kWh")}
        ${metric("Piek-laadvermogen", value(charge.totaal_vermogen_w, 0), "W")}
        ${metric("Nu actief", value(charge.actief_vermogen_w, 0), "W")}
        ${metric("Minimum per accu", value(charge.minimum_vermogen_per_accu_w, 0), "W")}
        ${metric("Voorkeur per accu", `${value(charge.voorkeur_minimum_per_accu_w, 0)}–${value(charge.voorkeur_maximum_per_accu_w, 0)}`, "W")}
        ${metric("Gepland venster", value(charge.gepland_venster_uren, 2), "uur")}
        ${metric("Ontladen vanaf", value(discharge.ontladen_vanaf, 4), "€/kWh")}
        ${metric("Beschikbare energie", value(discharge.beschikbare_energie_kwh, 2), "kWh")}
      </div>
      <div class="battery-plan-grid">${batteryCards}</div>`;
  }

  _verificationHtml(value) {
    if (!value) return '<div class="verification-line"><i></i><b>Geen verificatiegegevens</b></div>';
    const lines = String(value).replace(/[|;]/g, "\n").split(/\n|,(?=\s*Accu\s*\d+)|(?=Accu\s*\d+\s*[=:])/i).map((line) => line.trim().replace(/^,\s*/, "")).filter(Boolean);
    return lines.map((line) => {
      const bad = /fout|mislukt|niet ok|offline/i.test(line);
      return `<div class="verification-line ${bad ? "bad" : ""}"><i></i><b>${this._escape(line)}</b></div>`;
    }).join("");
  }

  _renderStatusHistory() {
    const obj = this._obj(E.runtimeEvents);
    const events = Array.isArray(obj?.attributes?.gebeurtenissen)
      ? obj.attributes.gebeurtenissen : [];
    const counts = obj?.attributes?.aantallen_per_accu || {};
    this.$("event-counts").innerHTML = Object.entries(counts).map(
      ([name, values]) => `<div><b>${this._escape(name)}</b><span>${Number(values.waarschuwingen || 0)} waarschuwingen · ${Number(values.fouten || 0)} fouten · ${Number(values.hersteld || 0)} hersteld</span></div>`
    ).join("") || "<small>Nog geen accu-afwijkingen geregistreerd</small>";
    this.shadowRoot.querySelectorAll("[data-event-filter]").forEach(
      (button) => button.classList.toggle(
        "active", button.dataset.eventFilter === this._eventFilter
      )
    );
    const visible = events.filter((event) =>
      this._eventFilter === "all"
      || String(event.niveau) === this._eventFilter
    );
    this._visibleRuntimeEvents = visible;
    if (!visible.length) {
      this.$("status-timeline").innerHTML = events.length
        ? '<div class="empty">Geen gebeurtenissen binnen dit filter</div>'
        : '<div class="empty">Nog geen runtime-afwijkingen geregistreerd</div>';
      return;
    }
    this.$("status-timeline").innerHTML = visible.map((event, index) => {
      const time = new Date(event.tijd);
      const level = ["error", "warning", "recovered", "info"].includes(
        String(event.niveau)
      ) ? String(event.niveau) : "info";
      const battery = event.accu ? `${this._escape(event.accu)} · ` : "";
      const repetitions = Math.max(1, Number(event.aantal || 1));
      const repetitionLabel = repetitions > 1
        ? ` · ${repetitions}×` : "";
      return `<button class="event ${level}" data-event-index="${index}"><i></i><div><div class="event-top"><b>${battery}${this._escape(event.titel)}${repetitionLabel}</b><time>${Number.isNaN(time.getTime()) ? "—" : time.toLocaleString("nl-NL", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</time></div><p>${this._escape(event.samenvatting || event.details || "")}</p></div></button>`;
    }).join("");
    this.shadowRoot.querySelectorAll("[data-event-index]").forEach(
      (button) => button.addEventListener("click", () =>
        this._showRuntimeEvent(
          visible[Number(button.dataset.eventIndex)]
        )
      )
    );
  }

  _showRuntimeEvent(event) {
    if (!event) return;
    const time = new Date(event.tijd);
    const row = (label, value) => value == null || value === ""
      ? "" : `<div><span>${label}</span><b>${this._escape(value)}</b></div>`;
    this.$("event-modal-content").innerHTML = `
      <small>${Number.isNaN(time.getTime()) ? "Onbekend tijdstip" : time.toLocaleString("nl-NL", { dateStyle: "full", timeStyle: "medium" })}</small>
      <h3>${this._escape(event.titel)}</h3>
      <p>${this._escape(event.samenvatting || "")}</p>
      <div class="event-detail-grid">
        ${row("Accu", event.accu)}
        ${row("Niveau", ({ error: "Fout", warning: "Waarschuwing", recovered: "Hersteld", info: "Informatie" })[event.niveau] || event.niveau)}
        ${row("Verwachte status", event.verwachte_status)}
        ${row("Herstelpoging", event.herstelpoging)}
        ${row("Aantal meldingen", Math.max(1, Number(event.aantal || 1)))}
        ${row("Eerste melding", event.eerste_tijd ? new Date(event.eerste_tijd).toLocaleString("nl-NL") : null)}
      </div>
      <h4>Technische details</h4>
      <pre>${this._escape(event.details || "Geen extra details")}</pre>`;
    this.$("event-modal").hidden = false;
  }

  _batteryCard(b) {
    const row = (label, id) => `<div class="data-row"><span>${label}</span><b id="${id}">—</b></div>`;
    const toggle = (key, label) => `<div class="switch-row"><span><b>${label}</b><small id="b${b.n}-${key}-label">—</small></span><button id="b${b.n}-${key}-toggle" class="simple-toggle" aria-label="${label}"><i></i></button></div>`;
    return `<article class="card battery-card">
      <div class="battery-title"><span><ha-icon icon="mdi:battery"></ha-icon> Accu ${b.n}</span><span class="chip" id="b${b.n}-mode">—</span></div>
      <div class="battery-top"><div data-more="${b.soc}" class="clickable-ring">${this._ring(`battery-${b.n}`, 142)}</div><div class="battery-live"><div id="b${b.n}-power" class="actual-power">0 W</div><small id="b${b.n}-inverter-summary">—</small></div></div>
      <div class="battery-quick">${row("Batterijcycli", `b${b.n}-cycles`)}${row("Interne temperatuur", `b${b.n}-temperature`)}</div>
      <details class="battery-settings"><summary><ha-icon icon="mdi:tune-variant"></ha-icon> Instellingen</summary><div class="battery-controls">
        <label class="select-control">Werkmodus<select id="b${b.n}-work"></select></label>
        <label class="select-control">Geforceerde modus<select id="b${b.n}-force"></select></label>
        ${this._range(`b${b.n}-charge`, "Laadvermogen", 0, 2500, 25, "W")}
        ${this._range(`b${b.n}-discharge`, "Ontlaadvermogen", 0, 2500, 25, "W")}
        ${this._range(`b${b.n}-maxcharge`, "Maximaal laadvermogen", 0, 2500, 25, "W")}
        ${this._range(`b${b.n}-maxdischarge`, "Maximaal ontlaadvermogen", 0, 2500, 25, "W")}
        ${toggle("rs485", "RS485-bediening")}${toggle("backup", "Back-upfunctie")}
      </div></details>
      <details class="diagnostics"><summary><ha-icon icon="mdi:information-outline"></ha-icon> Diagnose-informatie</summary><div class="diagnostic-content">
        <h4>Verbinding en status</h4><div class="data-grid">
          ${row("Modbus", `b${b.n}-modbus`)}${row("Modbus-adres", `b${b.n}-modbusaddress`)}${row("IP-adres", `b${b.n}-ip`)}${row("Omvormerstatus", `b${b.n}-inverter`)}
        </div>
        <h4>Versies</h4><div class="data-grid">
          ${row("Firmware", `b${b.n}-firmware`)}${row("BMS-versie", `b${b.n}-bmsversion`)}${row("EMS-versie", `b${b.n}-emsversion`)}${row("VMS-versie", `b${b.n}-vmsversion`)}${row("Communicatiemodule", `b${b.n}-communicationfirmware`)}
        </div>
        <h4>Net en back-upuitgang</h4><div class="data-grid">
          ${row("AC-spanning", `b${b.n}-acvoltage`)}${row("AC-stroom", `b${b.n}-accurrent`)}${row("AC-vermogen", `b${b.n}-acpower`)}${row("AC-frequentie", `b${b.n}-acfrequency`)}
          ${row("Off-grid spanning", `b${b.n}-offgridvoltage`)}${row("Off-grid stroom", `b${b.n}-offgridcurrent`)}${row("Off-grid vermogen", `b${b.n}-offgridpower`)}
        </div>
        <h4>Batterij en cellen</h4><div class="data-grid">
          ${row("Batterijspanning", `b${b.n}-voltage`)}${row("Batterijstroom", `b${b.n}-batterycurrent`)}${row("Batterijvermogen", `b${b.n}-batterypower`)}
          ${row("Maximale celspanning", `b${b.n}-cellmax`)}${row("Minimale celspanning", `b${b.n}-cellmin`)}${row("Celverschil", `b${b.n}-celldelta`)}
          ${row("Balanceermodus", `b${b.n}-balancing`)}${row("Delta gemiddeld", `b${b.n}-deltaavg`)}${row("Balansstatus", `b${b.n}-balancestatus`)}
          ${row("Laatste balansmeting", `b${b.n}-balancelast`)}${row("Cycli berekend", `b${b.n}-cyclescalc`)}
        </div>
        <div class="diagnostic-action"><span><b>Apparaat opnieuw opstarten</b><small>Herstart alleen deze accu; instellingen blijven behouden.</small></span><button id="b${b.n}-device-reset" class="device-reset"><ha-icon icon="mdi:restart"></ha-icon>Opnieuw opstarten</button></div>
      </div></details>
      <section class="cell-monitor">
        <header><div><ha-icon icon="mdi:battery-heart-variant"></ha-icon><span><b>Batterijcellen</b><small>Pack 1 · live via Modbus</small></span></div><span id="b${b.n}-cells-status" class="cell-status unavailable">Wachten</span></header>
        <div class="cell-kpis"><div><span>Gemiddeld</span><b id="b${b.n}-cells-average">—</b></div><div><span>Celverschil</span><b id="b${b.n}-cells-delta">—</b></div><div><span>Min / max</span><b id="b${b.n}-cells-minmax">—</b></div></div>
        <div class="cell-chart" aria-label="Celspanningen Accu ${b.n}">
          ${b.cells.map((entity, index) => `<button id="b${b.n}-cell-${index + 1}" class="cell-column unavailable" data-more="${entity}" aria-label="Cel ${index + 1}"><span class="cell-rail"><i id="b${b.n}-cell-${index + 1}-fill"></i></span><b id="b${b.n}-cell-${index + 1}-value">—</b><small>${index + 1}</small></button>`).join("")}
        </div>
        <div class="cell-legend"><span class="minimum">Laagste cel</span><span class="maximum">Hoogste cel</span><small id="b${b.n}-cells-scale">Gedeelde schaal voor alle accuâ€™s</small></div>
      </section>
    </article>`;
  }

  _ring(key, size) {
    return `<div class="soc-ring" style="--ring-size:${size}px"><svg viewBox="0 0 120 120"><circle class="ring-track" cx="60" cy="60" r="54"/><circle class="ring-highlight" cx="60" cy="60" r="54"/><circle id="ring-${key}-fg" class="ring-fg" cx="60" cy="60" r="54" pathLength="100"/></svg><div class="ring-center"><strong id="ring-${key}-value">—%</strong><small id="ring-${key}-sub"></small></div></div>`;
  }

  _heading(icon, title, chipId) {
    return `<div class="card-head"><ha-icon icon="${icon}"></ha-icon><h2>${title}</h2>${chipId ? `<span id="${chipId}" class="chip">—</span>` : ""}</div>`;
  }

  _metric(label, id, unit) { return `<div class="metric"><span>${label}</span><b><em id="${id}">—</em> ${unit}</b></div>`; }
  _settingsCards(category) {
    return E.settings.map(([id, label, unit, group, kind], index) => (
      group === category
        ? `<article class="card setting">${kind === "number-entry"
          ? `<label class="range-label-ui"><span>${label}<b>${unit}</b></span><input id="setting-${index}-input" type="number" min="0" max="0.5" step="0.0001"><em id="setting-${index}-value" hidden>—</em></label>`
          : this._range(`setting-${index}`, label, 0, 100, 1, unit)}<small class="entity-id">${id}</small></article>`
        : ""
    )).join("");
  }

  _range(prefix, label, min, max, step, unit) { return `<label class="range-label-ui"><span>${label}<b><em id="${prefix}-value">—</em> ${unit}</b></span><input id="${prefix}-input" type="range" min="${min}" max="${max}" step="${step}"></label>`; }
  _tabButton(id, icon, label) { return `<button class="tab ${id === "overview" ? "active" : ""}" data-tab="${id}"><ha-icon icon="${icon}"></ha-icon><span>${label}</span></button>`; }
  _setSelect(id, value) { this._syncControlValue(this.$(id), value); }
  _obj(id) { return entityObject(this._hass, id); }
  _state(id, fallback = "onbekend") {
    const authoritative = entityState(this._hass, id, fallback);
    const pending = this._pendingEntityStates.get(id);
    if (!pending) return authoritative;
    const authoritativeNumber = Number(authoritative);
    const pendingNumber = Number(pending.value);
    const confirmed = String(authoritative) === pending.value || (
      Number.isFinite(authoritativeNumber)
      && Number.isFinite(pendingNumber)
      && Math.abs(authoritativeNumber - pendingNumber) < 0.000001
    );
    if (confirmed) {
      clearTimeout(pending.timer);
      this._pendingEntityStates.delete(id);
      return authoritative;
    }
    if (Date.now() < pending.until) return pending.value;
    clearTimeout(pending.timer);
    this._pendingEntityStates.delete(id);
    return authoritative;
  }
  _num(id) { return numericEntityState(this._hass, id); }
  _fmt(value, decimals = 0) { return formatNumber(value, decimals); }
  _translateState(value) { return ({ charging: "Laden", discharging: "Ontladen", exporting: "Terugleveren", self_consumption: "Zelfconsumptie", stopped: "Stop", idle: "Rust", error: "Fout", on: "Auto", off: "Handmatig" })[String(value).toLowerCase()] || value || "—"; }
  _escape(value) { return escapeHtml(value); }
  $(id) { return this.shadowRoot.getElementById(id); }
  _moreInfo(entityId) { this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true })); }

  _styles() {
    return `<style>
      .page-title{display:flex;align-items:center;justify-content:space-between;margin:2px 0 16px}.page-title h1{font-size:24px;margin:0}.page-title p{color:var(--muted);margin:4px 0 0}.refresh{min-height:44px;padding:0 14px;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--ink);display:flex;align-items:center;gap:7px;cursor:pointer}.battery-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.battery-title{display:flex;align-items:center;justify-content:space-between;font-size:16px;font-weight:700}.battery-title>span:first-child{display:flex;align-items:center;gap:7px}.battery-top{display:flex;align-items:center;gap:18px;margin:16px 0}.power-pair{flex:1;display:grid;gap:13px}.power-pair div{padding:10px;border-radius:10px;background:var(--panel2)}.power-pair span{display:block;color:var(--muted);font-size:11px}.power-pair b{display:block;font-size:20px;margin-top:3px}.data-grid{border-top:1px solid var(--line)}.data-row{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--line);font-size:12px}.data-row span{color:var(--muted)}.data-row b{text-align:right}.graph-grid{display:grid;gap:16px}.graph-wide{min-height:330px}.chart{height:250px;position:relative}.chart svg{width:100%;height:100%;overflow:visible}.zero{stroke:var(--line);stroke-width:1}.bar-price{fill:#87949a}.bar-charge{fill:var(--accent);filter:drop-shadow(0 0 4px #55d99688)}.bar-discharge{fill:var(--blue);filter:drop-shadow(0 0 4px #8aa8ff88)}.axis-labels{fill:var(--muted);font-size:10px}.chart-note,.legend{display:flex;gap:16px;color:var(--muted);font-size:11px}.chart-note span:before,.legend span:before{content:"";display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:5px}.price-dot:before{background:#87949a}.charge-dot:before,.home-line:before{background:var(--accent)}.discharge-dot:before,.grid-line:before{background:var(--blue)}.legend{justify-content:flex-end;margin-top:-31px;position:relative;z-index:2}.range-label{position:absolute;top:3px;left:0;color:var(--muted);font-size:10px;height:220px;display:flex;flex-direction:column;justify-content:space-between}.empty{height:100%;display:grid;place-items:center;color:var(--muted)}.empty.error{color:var(--red)}.settings-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.settings-mode-switch{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:5px;border-radius:13px;background:var(--panel);border:1px solid var(--line)}.settings-mode-switch button{min-height:52px;border:0;border-radius:10px;background:transparent;color:var(--muted);font-size:14px;font-weight:700;cursor:pointer}.settings-mode-switch button.active{background:var(--accent);color:#10251c;box-shadow:0 5px 18px #55d9962a}.settings-section-title{grid-column:1/-1;display:flex;align-items:center;gap:11px;margin-top:10px;padding:12px 4px 2px;border-top:1px solid var(--line)}.settings-section-title:first-child{margin-top:0;border-top:0}.settings-section-title ha-icon{color:var(--accent)}.settings-section-title b,.settings-section-title small{display:block}.settings-section-title small{color:var(--muted);margin-top:3px}.setting{min-height:105px}.entity-id{display:none}.strategy-setting small,.sensor-setting small{display:block;color:var(--muted);margin-top:9px}.strategy-status{margin-top:10px;padding:8px;border-radius:8px;background:var(--panel2);font-size:11px;color:var(--muted)}.choice-switch{display:grid;grid-template-columns:1fr 1fr;background:var(--panel2);border-radius:10px;padding:3px;margin-top:12px}.choice-switch button{border:0;background:transparent;color:var(--muted);padding:10px;border-radius:8px;cursor:pointer}.choice-switch button.active{background:var(--accent);color:#10251c;font-weight:700}.switch-line{display:flex;justify-content:space-between;align-items:center;gap:16px}.small-toggle{min-width:48px;height:27px;border:0;border-radius:20px;background:#596269;padding:3px;cursor:pointer;color:var(--ink)}.small-toggle i{display:block;width:21px;height:21px;border-radius:50%;background:white;transition:.2s}.small-toggle.on{background:var(--accent);color:#10251c}.small-toggle.on i{transform:translateX(21px)}.quick-settings{grid-column:1/-1}.quick-row{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:12px 0;border-bottom:1px solid var(--line)}.quick-row:last-of-type{border-bottom:0}.quick-row small{display:block;color:var(--muted);margin-top:4px}.quick-row label{display:grid;grid-template-columns:1fr minmax(180px,260px);align-items:center;width:100%;gap:16px}.quick-row select{min-height:42px;border:1px solid var(--line);border-radius:9px;padding:0 10px;background:var(--panel2);color:var(--ink)}.quick-row input[type=number]{min-height:42px;box-sizing:border-box;border:1px solid var(--line);border-radius:9px;padding:0 10px;background:var(--panel2);color:var(--ink)}.lb-checks{display:flex;gap:18px}.lb-checks label{display:flex;grid-template-columns:none;align-items:center;gap:7px;width:auto}.quick-row[hidden]{display:none!important}.forecast-bar{opacity:.5;stroke:var(--ink);stroke-width:1;stroke-dasharray:3 3;fill-opacity:.35}.forecast-bar.forecast-charge{fill:var(--accent);filter:none}.forecast-bar.forecast-self{fill:var(--blue);filter:none}.forecast-bar.forecast-standby{fill:#3c4549;filter:none}.forecast-dot:before{background:transparent;border:1px dashed var(--ink)}.advanced-only{display:contents}.advanced-only[hidden]{display:none!important}.frozen{opacity:.42;filter:grayscale(.7)}.entity-settings{grid-column:1/-1}.entity-settings summary{cursor:pointer;font-weight:700}.entity-settings p{color:var(--muted);font-size:12px}.entity-settings fieldset{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:12px}.entity-settings label{font-size:11px;color:var(--muted)}.entity-settings input,.sensor-setting input,.sensor-setting select{display:block;width:100%;box-sizing:border-box;margin-top:5px;padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--panel2);color:var(--ink)}.warning-card{grid-column:1/-1;display:flex;gap:14px;align-items:flex-start}.warning-card ha-icon{color:var(--accent)}.warning-card p{color:var(--muted);margin:5px 0 0}.status-layout{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.status-selector,.status-efficiency,.status-advice-grid,.status-planning,.status-balance,.grid-offset-card{grid-column:1/-1}.balance-status-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0}.balance-status-summary div,.balance-kpis div{background:var(--panel2);border-radius:9px;padding:9px}.balance-status-summary span,.balance-kpis span{display:block;color:var(--muted);font-size:10px}.balance-status-summary b,.balance-kpis b{display:block;margin-top:3px}.balance-status-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.balance-status-card{padding:12px;border:1px solid var(--line);border-radius:12px;background:var(--panel2)}.balance-status-card.good{border-color:#55d99666}.balance-status-card.warn{border-color:#f3c75c66}.balance-status-card.attention,.balance-status-card.bad{border-color:#fb6b6b88}.balance-card-head{display:flex;justify-content:space-between;gap:8px}.balance-card-head span{color:var(--muted);font-size:11px}.balance-kpis{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:10px 0}.balance-status-card>small{display:block;color:var(--muted);line-height:1.4}.status-row{display:grid;grid-template-columns:140px 1fr;gap:12px;padding:11px 0;border-bottom:1px solid var(--line);font-size:12px}.status-row span{color:var(--muted)}.status-row b{font-weight:600;word-break:break-word}.current-status,.status-hours,.timeline-card{min-height:1210px}.timeline{max-height:1134px;overflow-y:auto;padding-right:5px}.assigned-hours-list{display:grid;gap:5px}.assigned-hour{position:relative;display:grid;grid-template-columns:minmax(82px,1fr) 18px minmax(90px,1fr) auto;align-items:center;gap:7px;min-height:45px;padding:7px 8px;border-left:3px solid #596269;border-radius:8px;background:var(--panel2);font-size:10px}.assigned-hour>div span,.assigned-hour>div b{display:block}.assigned-hour>div span{color:var(--muted);font-size:8px;text-transform:capitalize}.assigned-hour ha-icon{--mdc-icon-size:16px;color:var(--muted)}.assigned-hour strong{font-size:10px}.assigned-hour em{color:var(--ink);font-style:normal;font-weight:750;white-space:nowrap}.assigned-hour small{position:absolute;top:3px;right:6px;color:var(--accent);font-size:7px;text-transform:uppercase}.assigned-hour.current{background:var(--accent-soft)}.assigned-hour.charge{border-left-color:var(--accent)}.assigned-hour.self{border-left-color:var(--blue)}.assigned-hour.export{border-left-color:#b58aff}
      .assigned-hour.forecast{opacity:.7;border-left-style:dashed}
      .assigned-hour-forecast{margin-left:4px;color:var(--muted);font-size:8px;font-style:normal;font-weight:650;text-transform:uppercase}.status-advice-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.status-advice{min-width:0}.advice-state{padding:9px 10px;margin-bottom:8px;border-radius:10px;background:var(--panel2)}.advice-state.active{background:var(--accent-soft)}.advice-state span,.advice-state b{display:block}.advice-state span{color:var(--muted);font-size:9px}.advice-state b{margin-top:4px;font-size:11px}.advice-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}.advice-kpis>div{min-width:0;padding:8px;border-radius:8px;background:var(--panel2)}.advice-kpis span,.advice-kpis b{display:block}.advice-kpis span{color:var(--muted);font-size:8px}.advice-kpis b{margin-top:4px;font-size:11px;overflow-wrap:anywhere}.advice-details{display:grid;gap:6px;margin-top:10px}.advice-details-title{display:flex;justify-content:space-between;gap:8px;margin-top:5px;color:var(--muted);font-size:9px}.event{display:grid;grid-template-columns:14px 1fr;gap:10px;padding:9px 0}.event>i{width:9px;height:9px;border-radius:50%;background:var(--accent);box-shadow:0 0 8px #55d99677;margin-top:4px}.event.bad>i{background:var(--red);box-shadow:0 0 8px #fb6b6b77}.event-top{display:flex;justify-content:space-between;gap:12px}.event time,.event p{color:var(--muted);font-size:11px}.event p{margin:3px 0 0}
            .price-summary{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:-2px 0 12px}.price-summary>div{padding:9px 10px;border-radius:10px;background:var(--panel2)}.price-summary span,.price-summary small{display:block;color:var(--muted);font-size:9px}.price-summary b{display:block;margin:3px 0;font-size:15px}.price-tooltip{position:absolute;z-index:6;width:max-content;max-width:min(260px,calc(100% - 12px));padding:9px 11px;border:1px solid #ffffff24;border-radius:9px;background:#22272b;color:var(--ink);box-shadow:0 8px 24px #000a;pointer-events:none;font-size:10px;line-height:1.4}.price-tooltip[hidden]{display:none}.price-tooltip b,.price-tooltip span{display:block}.price-tooltip b{margin-bottom:5px}.price-tooltip span{display:flex;justify-content:space-between;gap:12px;color:var(--muted)}.price-tooltip strong{color:var(--ink);font-variant-numeric:tabular-nums}.bar-standby{fill:#3c4549}.bar-charge,.bar-discharge,.bar-standby{cursor:pointer;transition:opacity .15s}.bar-charge:hover,.bar-discharge:hover,.bar-standby:hover{opacity:.72}.standby-dot:before{background:#3c4549!important}.market-bar{fill:#8aa8ff}.allin-bar{fill:#ff9f43}.market-dot:before{background:#8aa8ff!important}.allin-dot:before{background:#ff9f43!important}.energy-import{fill:#55d996}.energy-export{fill:#b58aff}.price-line{fill:none;stroke:#ff9f43;stroke-width:3}.import-dot:before{background:#55d996!important}.export-dot:before{background:#b58aff!important}.allin-line:before{background:#ff9f43!important}.soc-legend{display:flex;justify-content:flex-end;gap:14px;margin-top:-31px;position:relative;z-index:2;color:var(--muted);font-size:10px}.soc-legend span:before{content:"";display:inline-block;width:9px;height:3px;margin-right:5px;vertical-align:middle}.soc-total:before{background:#fff}.soc-1:before{background:#55d996}.soc-2:before{background:#8aa8ff}.soc-3:before{background:#b58aff}.soc-4:before{background:#ffb454}.soc-grid{stroke:var(--line);stroke-dasharray:5 5}.sensor-setting label{display:grid;gap:8px;font-size:12px;color:var(--muted)}.sensor-setting input,.select-control select{width:100%;min-height:44px;border:1px solid var(--line);border-radius:10px;padding:0 11px;background:var(--panel2);color:var(--ink)}.sensor-setting small{display:block;margin-top:8px;color:var(--muted);font-size:10px}.clickable-ring{cursor:pointer;border-radius:50%;transition:transform .18s}.clickable-ring:hover{transform:scale(1.025)}.power-charge{color:var(--accent)!important}.power-discharge{color:#a58aff!important}.actual-power{flex:1;padding:14px;border-radius:12px;background:var(--panel2);font-size:20px;font-weight:750;text-align:center;white-space:nowrap}.battery-quick{display:grid;grid-template-columns:1fr 1fr;gap:0 14px;border-top:1px solid var(--line)}.battery-controls{display:grid;gap:12px;padding:14px 0;border-top:1px solid var(--line)}.select-control{display:grid;gap:5px;color:var(--muted);font-size:11px}.switch-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.switch-row span b,.switch-row span small{display:block}.switch-row span b{font-size:12px}.switch-row span small{font-size:10px;color:var(--muted);margin-top:3px}.simple-toggle{width:52px;height:30px;padding:3px;border:1px solid var(--line);border-radius:999px;background:var(--panel2);cursor:pointer;flex:0 0 auto}.simple-toggle i{display:block;width:22px;height:22px;border-radius:50%;background:#788184;transition:.2s}.simple-toggle.on i{transform:translateX(20px);background:var(--accent);box-shadow:0 0 10px #55d99688}.diagnostics{margin-top:4px;border-top:1px solid var(--line)}.diagnostics summary{min-height:46px;display:flex;align-items:center;gap:7px;cursor:pointer;color:var(--muted);font-size:12px;list-style:none}.diagnostics summary::-webkit-details-marker{display:none}.diagnostics summary:after{content:"â–¾";margin-left:auto;transition:.2s}.diagnostics[open] summary:after{transform:rotate(180deg)}.diagnostics ha-icon{--mdc-icon-size:17px}.verification-section{padding-top:14px}.verification-section>span{display:block;color:var(--muted);font-size:12px;margin-bottom:8px}.verification-line{display:grid;grid-template-columns:10px 1fr;align-items:start;gap:8px;padding:8px 10px;margin:6px 0;border-radius:9px;background:var(--panel2);font-size:11px}.verification-line i{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 7px #55d99677;margin-top:3px}.verification-line.bad i{background:var(--red);box-shadow:0 0 7px #fb6b6b77}.verification-line b{font-weight:550;line-height:1.4;word-break:break-word}
      :host{--accent:#55d996;--accent-soft:#55d99620;--blue:#8aa8ff;--orange:#ff9f43;--red:#fb6b6b;--bg:#101416;--panel:#171c1f;--panel2:#1d2327;--line:#ffffff12;--ink:#f4f7f6;--muted:#9aa5a8;display:block;height:100%;font-family:Inter,system-ui,sans-serif;color:var(--ink);color-scheme:dark}
      :host([data-theme="light"]){--bg:#eef2f1;--panel:#fff;--panel2:#f4f7f6;--line:#10201818;--ink:#17201d;--muted:#66716d;color-scheme:light}
      select{color-scheme:dark}select option{background:#1d2327;color:#f4f7f6}:host([data-theme="light"]) select{color-scheme:light}:host([data-theme="light"]) select option{background:#fff;color:#17201d}
      *{box-sizing:border-box}button,select,input{font:inherit}.app{height:100vh;display:flex;flex-direction:column;background:radial-gradient(circle at 75% -20%,#55d99612,transparent 38%),var(--bg);overflow:hidden}.appbar{height:64px;flex:0 0 64px;display:flex;align-items:center;gap:24px;padding:0 22px;border-bottom:1px solid var(--line);background:#151a1dcc}.brand{display:flex;align-items:center;gap:11px;min-width:175px}.brand .logo{width:40px;height:40px;display:grid;place-items:center;filter:drop-shadow(0 0 7px #55d99666)}.brand .logo img{width:100%;height:100%;object-fit:contain;display:block}.brand b{display:block}.brand small{display:block;color:var(--muted);font-size:11px}.tabs{height:100%;display:flex;overflow-x:auto;scrollbar-width:none}.tab{min-height:44px;padding:0 15px;border:0;border-bottom:3px solid transparent;background:none;color:var(--muted);display:flex;align-items:center;gap:8px;cursor:pointer;white-space:nowrap}.tab.active{color:var(--accent);border-bottom-color:var(--accent)}.home-link{text-decoration:none}.home-link:hover{color:var(--accent)}.live{margin-left:auto;display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}.live i{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 10px var(--accent)}main{flex:1;min-height:0;overflow:hidden}.page{height:100%;overflow-y:auto;padding:18px 22px 24px}.page[hidden]{display:none!important}.card{background:linear-gradient(145deg,var(--panel),color-mix(in srgb,var(--panel),#000 4%));border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:0 12px 30px #00000012}.card-head{height:26px;display:flex;align-items:center;gap:9px;margin-bottom:14px}.card-head ha-icon{color:var(--muted);--mdc-icon-size:18px}.card-head h2{margin:0;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}.card-head .chip{margin-left:auto}.chip{display:inline-flex;padding:5px 10px;border-radius:999px;border:1px solid var(--line);background:var(--panel2);font-size:11px;color:var(--muted)}.chip.good{color:var(--accent);border-color:#55d99655;background:var(--accent-soft)}.chip.warn{color:#f6c85f;border-color:#f6c85f55;background:#f6c85f12}.muted{color:var(--muted)}
      .overview-page{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(360px,.9fr);grid-template-rows:minmax(360px,1fr) 176px;gap:16px;overflow:hidden}.hero{min-height:0}.hero-body{height:calc(100% - 40px);display:flex;align-items:center;justify-content:center;gap:34px}.soc-ring{width:var(--ring-size);height:var(--ring-size);position:relative;flex:0 0 auto}.soc-ring svg{width:100%;height:100%;overflow:visible;transform:rotate(-90deg)}.soc-ring circle{fill:none;stroke-width:10}.ring-track{stroke:var(--panel2)}.ring-highlight{stroke:#ffffff0a;stroke-width:14!important;filter:blur(2px)}.ring-fg{stroke:var(--accent);stroke-linecap:round;stroke-dasharray:100;stroke-dashoffset:100;transition:stroke-dashoffset .9s ease,stroke .5s}.ring-center{position:absolute;inset:0;display:grid;place-content:center;text-align:center}.ring-center strong{font-size:clamp(27px,3.3vw,48px);line-height:1}.ring-center small{margin-top:6px;color:var(--muted);font-size:11px}.hero-info{max-width:440px;flex:1}.big-state{font-size:clamp(28px,3vw,46px);font-weight:750;margin-bottom:4px}.metric-grid{display:grid;grid-template-columns:1fr 1fr;margin-top:22px;border-top:1px solid var(--line)}.metric{padding:12px 8px 8px 0}.metric span{display:block;color:var(--muted);font-size:11px}.metric b{display:block;margin-top:5px;font-size:17px}.metric em,.range-label-ui em{font-style:normal}.control-card{min-height:0;overflow-y:auto}.auto-row{display:flex;align-items:center;justify-content:space-between;gap:15px;padding-bottom:14px;border-bottom:1px solid var(--line)}.auto-row small{display:block;color:var(--muted);margin-top:4px}.mode-toggle{min-height:48px;border:1px solid var(--line);border-radius:999px;background:var(--panel2);color:var(--muted);display:flex;align-items:center;gap:9px;padding:5px 8px 5px 12px;cursor:pointer}.mode-toggle i{width:28px;height:28px;border-radius:50%;background:#788184;transition:.25s}.mode-toggle.on i{background:var(--accent);box-shadow:0 0 12px #55d99688}.mode-toggle.on span:last-child{color:var(--accent)}.manual-controls{display:grid;gap:13px;padding-top:14px;transition:.2s}.manual-controls.disabled{opacity:.42;pointer-events:none}.manual-controls label{display:grid;gap:6px;color:var(--muted);font-size:12px}.manual-controls select{min-height:44px;border:1px solid var(--line);border-radius:10px;padding:0 12px;background:var(--panel2);color:var(--ink)}.range-label-ui>span{display:flex;justify-content:space-between;align-items:center}.range-label-ui b{color:var(--ink)}input[type="range"]{width:100%;height:28px;accent-color:var(--accent);cursor:pointer}.manual-result{padding:10px 12px;border-radius:10px;background:var(--panel2);border:1px solid var(--line);min-width:0}.manual-result span{display:block;font-weight:700}.manual-result small{display:block;color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.35;max-height:7em;overflow-x:hidden;overflow-y:auto;margin-top:3px;padding-right:3px}.plan-card{display:flex;flex-direction:column;justify-content:center}.plan-card .card-head{margin-bottom:8px}.plan-card strong{font-size:17px}.plan-card small{margin-top:6px;color:var(--muted)}.battery-strip{grid-column:2;grid-row:2;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));padding:10px 8px;gap:4px}.mini-battery{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;text-align:center;cursor:pointer;min-width:0}.manual-accus{display:flex;gap:6px;margin:6px 0 4px}.manual-controls .manual-accu{flex:1;display:flex;flex-direction:row;align-items:center;justify-content:center;gap:6px;padding:5px 4px;border:1px solid var(--line);border-radius:10px;background:var(--panel2);color:var(--ink);font-size:12px;cursor:pointer}.manual-accu.off{opacity:.45;cursor:not-allowed}.manual-accu span{white-space:nowrap}.manual-accus[hidden],#manual-distribution-block[hidden]{display:none}.manual-accu input{accent-color:var(--accent)}.manual-distribution{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:8px;color:var(--muted);font-size:11px}.manual-distribution select{min-height:38px}.mini-battery .soc-ring{transform:scale(.82)}.mini-battery b,.mini-battery small{display:block;white-space:nowrap}.mini-battery small{font-size:10px;color:var(--muted);margin-top:4px}.mini-battery small.battery-health-warning{color:#ffc85c;white-space:normal;line-height:1.25;max-width:112px}.charge-plan{grid-column:1;grid-row:2;width:calc(50% - 8px)}.discharge-plan{grid-column:1;grid-row:2;width:calc(50% - 8px);justify-self:end}
      .manual-result>span{display:block;font-weight:700}.manual-battery-statuses{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 16px;margin:8px 0;padding:6px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}@media(max-width:560px){.manual-battery-statuses{grid-template-columns:1fr}}.manual-battery-row{display:grid;grid-template-columns:minmax(54px,1fr) auto;align-items:center;gap:10px;padding:5px 0;font-size:11px}.manual-battery-row>b{font-weight:600}.manual-battery-row>div{display:flex;align-items:center;justify-content:flex-end;gap:10px;min-width:0;white-space:nowrap}.manual-battery-row strong{color:var(--ink);font-variant-numeric:tabular-nums}.manual-battery-row em{color:var(--muted);font-style:normal;font-weight:650}.manual-battery-row span{min-width:28px;font-weight:750;text-align:right}.manual-battery-row.ok span{color:var(--accent)}.manual-battery-row.busy span{color:#f6c85f}.manual-battery-row.error span{color:var(--red)}.manual-action{display:grid!important;grid-template-columns:38px 1fr;gap:7px;color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.35;max-height:5em;overflow-x:hidden;overflow-y:auto;margin-top:3px;padding-right:3px}.manual-action[hidden]{display:none!important}.manual-action>b{color:var(--ink);font-weight:600}.manual-action>span{font-weight:400}
      .charge-planning[hidden]{display:none}.charge-planning{margin-top:14px;padding:14px;border:1px solid #55d99633;border-radius:14px;background:linear-gradient(145deg,#55d99612,var(--panel2))}.planning-head{display:flex;align-items:center;gap:8px;margin-bottom:10px;color:var(--accent);font-size:13px;text-transform:uppercase;letter-spacing:.06em}.planning-head ha-icon{--mdc-icon-size:18px}.planning-rows{display:grid}.planning-labels,.planning-row{display:grid;grid-template-columns:1fr 92px 82px;align-items:center;gap:8px}.planning-labels{padding-bottom:5px;color:var(--muted);font-size:9px}.planning-labels span{text-align:right}.planning-row{padding:7px 0;border-top:1px solid var(--line);font-size:12px}.planning-row span{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}.planning-row.total{padding-bottom:9px}.planning-row.total b,.planning-row.total span{color:var(--ink);font-weight:750}
      .price-summary{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:-2px 0 12px}.price-summary>div{padding:9px 10px;border-radius:10px;background:var(--panel2)}.price-summary span,.price-summary small{display:block;color:var(--muted);font-size:9px}.price-summary b{display:block;margin:3px 0;font-size:15px}.price-tooltip{position:absolute;z-index:6;width:max-content;max-width:min(260px,calc(100% - 12px));padding:9px 11px;border:1px solid #ffffff24;border-radius:9px;background:#22272b;color:var(--ink);box-shadow:0 8px 24px #000a;pointer-events:none;font-size:10px;line-height:1.4}.price-tooltip[hidden]{display:none}.price-tooltip b,.price-tooltip span{display:block}.price-tooltip b{margin-bottom:5px}.price-tooltip span{display:flex;justify-content:space-between;gap:12px;color:var(--muted)}.price-tooltip strong{color:var(--ink);font-variant-numeric:tabular-nums}.bar-standby{fill:#3c4549}.bar-charge,.bar-discharge,.bar-standby{cursor:pointer;transition:opacity .15s}.bar-charge:hover,.bar-discharge:hover,.bar-standby:hover{opacity:.72}.standby-dot:before{background:#3c4549!important}.market-bar{fill:#8aa8ff}.allin-bar{fill:#ff9f43}.market-dot:before{background:#8aa8ff!important}.allin-dot:before{background:#ff9f43!important}.energy-import{fill:#55d996}.energy-export{fill:#b58aff}.price-line{fill:none;stroke:#ff9f43;stroke-width:3}.import-dot:before{background:#55d996!important}.export-dot:before{background:#b58aff!important}.allin-line:before{background:#ff9f43!important}.soc-legend{display:flex;justify-content:flex-end;gap:14px;margin-top:-31px;position:relative;z-index:2;color:var(--muted);font-size:10px}.soc-legend span:before{content:"";display:inline-block;width:9px;height:3px;margin-right:5px;vertical-align:middle}.soc-total:before{background:#fff}.soc-1:before{background:#55d996}.soc-2:before{background:#8aa8ff}.soc-3:before{background:#b58aff}.soc-4:before{background:#ffb454}.soc-grid{stroke:var(--line);stroke-dasharray:5 5}.sensor-setting label{display:grid;gap:8px;font-size:12px;color:var(--muted)}.sensor-setting input,.select-control select{width:100%;min-height:44px;border:1px solid var(--line);border-radius:10px;padding:0 11px;background:var(--panel2);color:var(--ink)}.sensor-setting small{display:block;margin-top:8px;color:var(--muted);font-size:10px}.clickable-ring{cursor:pointer;border-radius:50%;transition:transform .18s}.clickable-ring:hover{transform:scale(1.025)}.power-charge{color:var(--accent)!important}.power-discharge{color:#a58aff!important}.actual-power{flex:1;padding:14px;border-radius:12px;background:var(--panel2);font-size:20px;font-weight:750;text-align:center;white-space:nowrap}.battery-quick{display:grid;grid-template-columns:1fr 1fr;gap:0 14px;border-top:1px solid var(--line)}.battery-controls{display:grid;gap:12px;padding:14px 0;border-top:1px solid var(--line)}.select-control{display:grid;gap:5px;color:var(--muted);font-size:11px}.switch-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.switch-row span b,.switch-row span small{display:block}.switch-row span b{font-size:12px}.switch-row span small{font-size:10px;color:var(--muted);margin-top:3px}.simple-toggle{width:52px;height:30px;padding:3px;border:1px solid var(--line);border-radius:999px;background:var(--panel2);cursor:pointer;flex:0 0 auto}.simple-toggle i{display:block;width:22px;height:22px;border-radius:50%;background:#788184;transition:.2s}.simple-toggle.on i{transform:translateX(20px);background:var(--accent);box-shadow:0 0 10px #55d99688}.diagnostics{margin-top:4px;border-top:1px solid var(--line)}.diagnostics summary{min-height:46px;display:flex;align-items:center;gap:7px;cursor:pointer;color:var(--muted);font-size:12px;list-style:none}.diagnostics summary::-webkit-details-marker{display:none}.diagnostics summary:after{content:"▾";margin-left:auto;transition:.2s}.diagnostics[open] summary:after{transform:rotate(180deg)}.diagnostics ha-icon{--mdc-icon-size:17px}.verification-section{padding-top:14px}.verification-section>span{display:block;color:var(--muted);font-size:12px;margin-bottom:8px}.verification-line{display:grid;grid-template-columns:10px 1fr;align-items:start;gap:8px;padding:8px 10px;margin:6px 0;border-radius:9px;background:var(--panel2);font-size:11px}.verification-line i{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 7px #55d99677;margin-top:3px}.verification-line.bad i{background:var(--red);box-shadow:0 0 7px #fb6b6b77}.verification-line b{font-weight:550;line-height:1.4;word-break:break-word}
      @media(max-width:1100px){.appbar{padding:0 14px;gap:12px}.brand{min-width:auto}.brand>div:last-child{display:none}.tab{padding:0 11px}.overview-page{grid-template-columns:1.25fr .9fr;grid-template-rows:minmax(320px,1fr) 170px;padding:12px;gap:12px}.card{padding:14px}.hero-body{gap:20px}.battery-grid{gap:12px}.battery-top{gap:8px}.battery-card .soc-ring{--ring-size:112px!important}.battery-card{padding:12px}.battery-controls{gap:8px;padding:10px 0}.status-layout{grid-template-columns:repeat(3,minmax(0,1fr))}}
      @media(max-height:760px) and (min-width:850px){.appbar{height:56px;flex-basis:56px}.page{padding-top:10px}.overview-page{grid-template-rows:minmax(290px,1fr) 150px}.soc-ring[style*="190"]{--ring-size:154px!important}.hero-info .metric-grid{margin-top:12px}.metric{padding-top:8px}.control-card{font-size:12px}.manual-controls{gap:8px;padding-top:9px}.manual-controls select{min-height:38px}.range-label-ui input{height:21px}.auto-row{padding-bottom:8px}.mode-toggle{min-height:40px}.plan-card .card-head{margin-bottom:2px}.battery-strip{padding:4px 10px}.mini-battery .soc-ring{transform:scale(.68)}}
      @media(max-width:850px){.live{display:none}.home-link span{display:none}.tab span{display:none}.tab{padding:0 14px}.overview-page{display:flex;overflow-y:auto}.overview-page>*{flex:0 0 auto}.hero-body{flex-direction:column}.hero{min-height:510px}.battery-strip{order:5}.charge-plan,.discharge-plan,.battery-strip{width:auto}.battery-grid,.settings-grid,.status-layout,.status-advice-grid,.entity-settings fieldset,.balance-status-grid,.balance-status-summary{grid-template-columns:1fr}.battery-grid{padding-bottom:20px}.warning-card{grid-column:auto}.current-status,.status-hours,.timeline-card{min-height:0}.timeline{max-height:420px}}
      @media(max-width:560px){.brand .logo{width:36px;height:36px}.appbar{padding:0 8px}.tab{padding:0 9px}.page{padding:12px}.metric-grid{grid-template-columns:1fr}.battery-top{flex-direction:column}.battery-strip{grid-template-columns:1fr}.mini-battery{justify-content:flex-start}.page-title h1{font-size:20px}.refresh{font-size:0}.refresh ha-icon{margin:0}}
      @media(max-width:1100px){.price-summary{grid-template-columns:repeat(3,1fr)}.price-detail{flex-direction:column}.battery-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
      @media(max-width:760px){.price-summary{grid-template-columns:repeat(2,1fr)}.battery-grid{grid-template-columns:1fr}.soc-legend{justify-content:flex-start;margin:0 0 8px;flex-wrap:wrap}}
      .battery-live{flex:1;display:grid;justify-items:center;text-align:center}.battery-live small{color:var(--muted);font-size:12px;margin-top:7px}.battery-settings{margin-top:4px;border-top:1px solid var(--line)}.battery-settings summary{min-height:46px;display:flex;align-items:center;gap:7px;cursor:pointer;color:var(--muted);font-size:12px;list-style:none}.battery-settings summary::-webkit-details-marker{display:none}.battery-settings summary:after{content:"▾";margin-left:auto;transition:.2s}.battery-settings[open] summary:after{transform:rotate(180deg)}.battery-settings ha-icon{--mdc-icon-size:17px}.battery-settings .battery-controls{border-top:0;padding-top:8px}.diagnostic-content{padding:2px 0 8px}.diagnostic-content h4{margin:14px 0 4px;color:var(--accent);font-size:10px;font-weight:750;letter-spacing:.08em;text-transform:uppercase}.diagnostic-content h4:first-child{margin-top:4px}.diagnostic-action{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--panel2)}.diagnostic-action span b,.diagnostic-action span small{display:block}.diagnostic-action span b{font-size:12px}.diagnostic-action span small{margin-top:3px;color:var(--muted);font-size:10px;line-height:1.35}.device-reset{display:flex;align-items:center;gap:6px;min-height:38px;padding:0 11px;border:1px solid #ffb35c66;border-radius:9px;background:#ff9f4312;color:#ffc178;cursor:pointer;white-space:nowrap}.device-reset:hover{border-color:#ffb35c;background:#ff9f4320}.device-reset:disabled{opacity:.4;cursor:not-allowed}.device-reset ha-icon{--mdc-icon-size:17px}.price-range-label{position:absolute;top:3px;right:0;color:var(--orange);font-size:10px;height:103px;display:flex;flex-direction:column;justify-content:space-between;text-align:right}
      .cell-monitor{margin:16px -4px -4px;padding:14px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,#8aa8ff0b,var(--panel2))}.cell-monitor>header{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}.cell-monitor>header>div{display:flex;align-items:center;gap:8px}.cell-monitor>header ha-icon{color:var(--accent);--mdc-icon-size:19px}.cell-monitor>header b,.cell-monitor>header small{display:block}.cell-monitor>header b{font-size:12px}.cell-monitor>header small{margin-top:2px;color:var(--muted);font-size:8px}.cell-status{padding:4px 7px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:8px;white-space:nowrap}.cell-status.good{color:var(--accent);border-color:#55d99655;background:#55d9960d}.cell-status.warn{color:#f6c85f;border-color:#f6c85f55;background:#f6c85f0d}.cell-status.attention{color:var(--red);border-color:#fb6b6b66;background:#fb6b6b0d}.cell-kpis{display:grid;grid-template-columns:.8fr .8fr 1.4fr;gap:6px;margin-bottom:10px}.cell-kpis>div{min-width:0;padding:7px 8px;border-radius:8px;background:var(--panel)}.cell-kpis span,.cell-kpis b{display:block}.cell-kpis span{color:var(--muted);font-size:7px;text-transform:uppercase;letter-spacing:.04em}.cell-kpis b{margin-top:3px;font-size:10px;font-variant-numeric:tabular-nums;white-space:nowrap}.cell-chart{display:grid;grid-template-columns:repeat(16,minmax(0,1fr));gap:3px;height:154px;padding:7px 5px 5px;border-radius:10px;background:linear-gradient(180deg,#fb6b6b0b 0 12%,#8aa8ff09 12% 58%,#55d9960b 58% 100%)}.cell-column{min-width:0;padding:0;border:0;background:transparent;color:var(--ink);display:grid;grid-template-rows:1fr auto auto;justify-items:center;gap:3px;cursor:pointer}.cell-rail{position:relative;width:100%;max-width:18px;height:108px;align-self:end;border:1px solid var(--line);border-radius:5px 5px 3px 3px;background:#0b0e10aa;overflow:hidden}.cell-rail i{position:absolute;inset:auto 0 0;height:0;background:linear-gradient(180deg,#8aa8ff,#55d996);box-shadow:0 0 8px #55d99644;transition:height .5s ease}.cell-column.minimum .cell-rail{border-color:#f6c85f88}.cell-column.minimum .cell-rail i{background:linear-gradient(180deg,#f6c85f,#b38b31);box-shadow:0 0 8px #f6c85f55}.cell-column.maximum .cell-rail{border-color:#b58aff99}.cell-column.maximum .cell-rail i{background:linear-gradient(180deg,#c6a7ff,#7d58cc);box-shadow:0 0 8px #b58aff66}.cell-column.unavailable{opacity:.35}.cell-column b{font-size:6px;font-variant-numeric:tabular-nums;transform:rotate(-55deg);transform-origin:center;white-space:nowrap;margin:4px 0}.cell-column small{color:var(--muted);font-size:7px}.cell-legend{display:flex;align-items:center;gap:12px;margin-top:8px;color:var(--muted);font-size:7px}.cell-legend span:before{content:"";display:inline-block;width:7px;height:7px;margin-right:4px;border-radius:2px}.cell-legend .minimum:before{background:#f6c85f}.cell-legend .maximum:before{background:#b58aff}.cell-legend small{margin-left:auto;color:var(--muted);font-size:7px;text-align:right}
      @media(max-width:560px){.battery-top{flex-direction:row}.cell-kpis{grid-template-columns:1fr 1fr}.cell-kpis>div:last-child{grid-column:1/-1}.cell-chart{gap:2px}.cell-column b{font-size:5px}}
      .status-planning{grid-column:1/-1}.planning-empty{min-height:120px}.planning-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:16px}.planning-metric{min-width:0;padding:11px 12px;border:1px solid var(--line);border-radius:11px;background:var(--panel2)}.planning-metric span,.planning-metric small{display:block;color:var(--muted);font-size:10px}.planning-metric b{display:block;margin:4px 0 2px;font-size:17px;font-variant-numeric:tabular-nums}.planning-detail-grid{display:grid;grid-template-columns:minmax(280px,.85fr) minmax(340px,1.15fr);gap:12px}.planning-section{min-width:0;padding:14px;border:1px solid var(--line);border-radius:14px;background:color-mix(in srgb,var(--panel2),transparent 18%)}.planning-section h3{display:flex;align-items:center;gap:8px;margin:0 0 12px;font-size:13px}.planning-section h3 ha-icon{--mdc-icon-size:18px;color:var(--accent)}.planning-advice{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.planning-advice .planning-metric{padding:8px 9px}.planning-advice .planning-metric b{font-size:14px}.planning-subhead{display:flex;justify-content:space-between;gap:12px;margin:14px 0 6px;font-size:11px}.planning-subhead small{color:var(--muted)}.planning-power,.planning-blocks{display:grid;gap:6px}.planning-power-row,.planning-block{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:9px 10px;border-radius:9px;background:var(--panel);font-size:11px}.planning-power-row span,.planning-block span,.planning-block small{color:var(--muted)}.planning-block b,.planning-power-row b{font-variant-numeric:tabular-nums}.charge-block{border-left:3px solid var(--accent)}.discharge-block{border-left:3px solid var(--blue)}.export-advice-block{border-left:3px solid #c6a7ff}.discharge-block>div:last-child,.export-advice-block>div:last-child{text-align:right}.discharge-block span,.discharge-block small,.export-advice-block span,.export-advice-block small{display:block}.planning-none{padding:18px 10px;text-align:center;color:var(--muted);font-size:11px}.discharge-section,.export-advice-section{grid-column:1/-1}.discharge-section .planning-advice,.export-advice-section .planning-advice{grid-template-columns:repeat(2,minmax(0,180px));margin-bottom:10px}
      .battery-plan-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:16px}.battery-plan-card{min-width:0;padding:14px;border:1px solid var(--line);border-radius:14px;background:var(--panel2);border-top:3px solid var(--muted)}.battery-plan-card.active{border-top-color:var(--accent);box-shadow:0 0 18px #55d99612}.battery-plan-card.done{border-top-color:var(--blue)}.battery-plan-card.late{border-top-color:var(--orange)}.battery-plan-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.battery-plan-card header b,.battery-plan-card header small{display:block}.battery-plan-card header small{margin-top:3px;color:var(--muted);font-size:9px;line-height:1.35}.battery-plan-card header>span{padding:4px 7px;border-radius:999px;background:var(--panel);color:var(--muted);font-size:9px;text-transform:uppercase}.battery-plan-card.active header>span{color:var(--accent)}.battery-plan-card.done header>span{color:var(--blue)}.battery-plan-soc{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:8px;margin:13px 0}.battery-plan-soc i{height:4px;border-radius:3px;background:linear-gradient(90deg,var(--accent),var(--blue))}.battery-plan-values{display:grid;grid-template-columns:1fr 1fr;gap:7px}.battery-plan-values>div{padding:7px 8px;border-radius:8px;background:var(--panel)}.battery-plan-values span,.battery-plan-values b{display:block}.battery-plan-values span{color:var(--muted);font-size:8px}.battery-plan-values b{margin-top:3px;font-size:11px}.battery-plan-timeline{display:grid;gap:5px;margin-top:10px}.battery-plan-segment{display:grid;grid-template-columns:1fr auto;gap:2px 8px;padding:8px;border-left:3px solid var(--accent);border-radius:8px;background:var(--panel);font-size:9px}.battery-plan-segment span{color:var(--muted)}.battery-plan-segment small{grid-column:1/-1;color:var(--muted)}
      @media(max-width:850px){.status-planning{grid-column:auto}.planning-metrics,.planning-detail-grid{grid-template-columns:1fr 1fr}.planning-section,.discharge-section,.export-advice-section{grid-column:1/-1}.battery-plan-grid{grid-template-columns:1fr}}
      .bar-charge-window{fill:#55d99666;cursor:pointer;transition:opacity .15s}.bar-charge-window:hover{opacity:.72}.charge-window-dot:before{background:#55d99666!important}.settings-actions{grid-column:1/-1}.settings-actions p,.settings-actions small{display:block;color:var(--muted);font-size:11px}.settings-actions>div{display:flex;flex-wrap:wrap;gap:9px;margin:12px 0}.settings-actions button{display:flex;align-items:center;gap:7px;min-height:42px;padding:0 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel2);color:var(--ink);cursor:pointer}.settings-actions button:hover{border-color:var(--accent)}.settings-actions button.danger:hover{border-color:var(--red);color:var(--red)}.settings-actions ha-icon{--mdc-icon-size:18px}.flow-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 8px}.flow-summary>div{padding:8px 10px;border-radius:9px;background:var(--panel2);border-left:3px solid var(--muted)}.flow-summary span,.flow-summary b{display:block}.flow-summary span{color:var(--muted);font-size:9px}.flow-summary b{margin-top:3px;font-size:14px}.flow-summary .grid-home{border-color:#ff9f43}.flow-summary .battery-home{border-color:#46aaf2}.flow-summary .battery-charge{border-color:#55d996}.flow-summary .export{border-color:#b58aff}.flow-legend{justify-content:flex-end;margin:0 0 4px}.flow-grid-home:before{background:#ff9f43!important}.flow-battery-home:before{background:#46aaf2!important}.flow-battery-charge:before{background:#55d996!important}.flow-export:before{background:#b58aff!important}.flow-grid-home-bar{fill:#ff9f43}.flow-battery-home-bar{fill:#46aaf2}.flow-battery-charge-bar{fill:#55d996}.flow-export-bar{fill:#b58aff}.flow-zero{stroke:#87949a;stroke-width:1.5}
      .grid-offset-card{grid-column:1/-1}.grid-offset-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.grid-offset-head small{display:block;color:var(--muted);margin-top:5px}.grid-offset-controls{display:grid;grid-template-columns:2fr 1fr 1fr auto;align-items:end;gap:10px;margin-top:14px}.grid-offset-controls label{display:grid;gap:6px;color:var(--muted);font-size:11px}.grid-offset-controls input,.grid-offset-controls select{width:100%;min-height:42px;padding:0 10px;border:1px solid var(--line);border-radius:9px;background:var(--panel2);color:var(--ink)}.grid-offset-controls input:disabled{opacity:.45}.grid-offset-controls button{min-height:42px;padding:0 12px;border:1px solid var(--line);border-radius:9px;background:var(--panel2);color:var(--ink);display:flex;align-items:center;justify-content:center;gap:7px;cursor:pointer}.grid-offset-controls button:hover{border-color:var(--accent)}.grid-offset-controls ha-icon{--mdc-icon-size:18px}.grid-offset-live{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:12px}.grid-offset-live>div{padding:9px 10px;border-radius:9px;background:var(--panel2)}.grid-offset-live span,.grid-offset-live b{display:block}.grid-offset-live span{color:var(--muted);font-size:9px}.grid-offset-live b{margin-top:3px;font-size:15px}.flow-range-label{position:absolute;top:3px;bottom:18px;left:0;color:var(--muted);font-size:10px;display:flex;flex-direction:column;justify-content:space-between}
      .bar-discharge-window{fill:#8aa8ff66;cursor:pointer;transition:opacity .15s}.bar-discharge-window:hover{opacity:.72}.discharge-window-dot:before{background:#8aa8ff66!important}.profitable-discharge{stroke:#8aa8ff;stroke-width:2;paint-order:stroke;filter:drop-shadow(0 0 4px #8aa8ff66)}.current-price-bar{stroke:#f4f7f6;stroke-width:2;filter:drop-shadow(0 0 6px #ffffff88)}.now-marker line{stroke:#f4f7f6;stroke-width:2;stroke-dasharray:6 6;filter:drop-shadow(0 0 4px #ffffff99)}.now-marker rect{fill:#f4f7f6}.now-marker text{fill:#101416;font-size:11px;font-weight:800;text-anchor:middle}
      .bar-export-window{fill:#6540a4;cursor:pointer;transition:opacity .15s}.bar-export-plan{fill:#c6a7ff;cursor:pointer;transition:opacity .15s}.bar-export-window:hover,.bar-export-plan:hover{opacity:.72}.export-window-dot:before{background:#6540a4!important}.export-plan-dot:before{background:#c6a7ff!important}
      .settings-section{grid-column:1/-1;border:0}.settings-section>.settings-section-title{display:flex;align-items:center;gap:11px;margin-top:10px;padding:12px 4px;border-top:1px solid var(--line);cursor:pointer;list-style:none}.settings-section>.settings-section-title::-webkit-details-marker{display:none}.settings-section>.settings-section-title:after{content:"▾";margin-left:auto;color:var(--muted);transition:transform .2s}.settings-section[open]>.settings-section-title:after{transform:rotate(180deg)}.settings-section-content{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding-top:8px}
      @media(max-width:850px){.settings-section-content{grid-template-columns:1fr}}
      .market-segment{fill:#55d996}.marketplus-segment{fill:#8aa8ff}.allin-segment{fill:#ff9f43}.market-dot:before{background:#55d996!important}.marketplus-dot:before{background:#8aa8ff!important}.allin-dot:before{background:#ff9f43!important}.resolution-dot:before{background:transparent!important;border:1px solid var(--muted);border-radius:50%!important}.source-warning{color:#f6c85f}.source-warning:before{background:#f6c85f!important}.price-grid line{stroke:var(--line);stroke-width:1}.price-grid text{fill:var(--muted);font-size:10px;text-anchor:end}.price-grid .price-axis-unit{text-anchor:start;font-size:9px}.chart-note{flex-wrap:wrap;row-gap:7px}
      @media(max-width:850px){.grid-offset-controls{grid-template-columns:1fr 1fr}.grid-offset-live{grid-template-columns:1fr 1fr}}
      @media(max-width:560px){.grid-offset-controls,.grid-offset-live{grid-template-columns:1fr}}
      @media(max-width:560px){.planning-metrics,.planning-detail-grid,.planning-advice,.flow-summary{grid-template-columns:1fr}.discharge-section .planning-advice,.export-advice-section .planning-advice{grid-template-columns:1fr}.planning-block{align-items:flex-start;flex-direction:column}.discharge-block>div:last-child,.export-advice-block>div:last-child{text-align:left}.flow-legend{justify-content:flex-start;flex-wrap:wrap}}
      .mobile-control-toggle{display:none}.results-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.result-period{min-width:0}.result-net{display:flex;align-items:end;justify-content:space-between;gap:12px;padding:14px;margin-bottom:8px;border-radius:12px;background:var(--accent-soft)}.result-net span{color:var(--muted);font-size:11px}.result-net b{font-size:24px;color:var(--accent)}.result-rows>div{display:flex;justify-content:space-between;gap:12px;padding:9px 2px;border-bottom:1px solid var(--line);font-size:11px}.result-rows span{color:var(--muted)}.result-rows b{text-align:right}.result-explanation{grid-column:1/-1}.result-explanation p{color:var(--muted);font-size:11px}.result-explanation span{color:var(--ink);font-weight:700}
      .result-quality{display:flex;justify-content:space-between;gap:12px;padding:8px 2px;color:var(--muted);font-size:10px}.result-quality b{color:var(--ink);text-transform:capitalize}
      .efficiency-result-card{min-width:0}.efficiency-result-heading{display:flex;align-items:center;justify-content:space-between;gap:14px}.efficiency-result-heading .card-head{margin:0}.efficiency-result-intro{margin:10px 0 14px;color:var(--muted);font-size:11px}.efficiency-direction-toggle{display:flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:999px;background:var(--panel2)}.efficiency-direction-toggle button{min-height:32px;padding:0 13px;border:0;border-radius:999px;background:transparent;color:var(--muted);font:inherit;font-size:11px;cursor:pointer}.efficiency-direction-toggle button.active{background:var(--accent-soft);color:var(--accent);font-weight:750}.efficiency-profile-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.efficiency-profile-card{position:relative;min-width:0;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--panel2)}.efficiency-profile-card>header{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.efficiency-profile-card>header b,.efficiency-profile-card>header small,.efficiency-profile-card>header span,.efficiency-profile-card>header strong{display:block}.efficiency-profile-card>header b{color:var(--accent)}.efficiency-profile-card>header small,.efficiency-profile-card>header span{margin-top:3px;color:var(--muted);font-size:9px}.efficiency-profile-card>header>div:last-child{text-align:right}.efficiency-profile-card>header strong{margin-top:3px;font-size:19px}.efficiency-profile-meta{display:flex;justify-content:space-between;gap:10px;margin-top:11px;padding-top:9px;border-top:1px solid var(--line);font-size:9px;color:var(--muted)}.efficiency-profile-meta b{color:var(--ink)}.efficiency-chart-scale{height:105px;position:absolute;left:8px;top:116px;display:flex;flex-direction:column;justify-content:space-between;color:var(--muted);font-size:8px;pointer-events:none}.efficiency-bin-chart{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(48px,1fr);align-items:end;gap:5px;min-height:144px;margin:10px 0 0 24px;padding:6px 2px 0;overflow-x:auto;border-bottom:1px solid var(--line);background:linear-gradient(to bottom,transparent 0,transparent calc(50% - .5px),var(--line) 50%,transparent calc(50% + .5px),transparent 100%)}.efficiency-bin{position:relative;display:grid;grid-template-rows:17px 94px 18px;align-items:end;min-width:44px;padding:0 2px;border:0;background:transparent;color:var(--ink);cursor:pointer}.efficiency-bin-value,.efficiency-bin-power{font-size:8px;text-align:center;white-space:nowrap}.efficiency-bin-value{align-self:start;color:var(--muted)}.efficiency-bin-power{align-self:center}.efficiency-bar-track{height:94px;display:flex;align-items:flex-end;justify-content:center}.efficiency-bar-track i{display:block;width:min(24px,72%);min-height:4px;border-radius:5px 5px 2px 2px;background:#7b878c;transition:height .2s,filter .2s}.efficiency-bin.fair .efficiency-bar-track i{background:#f6c85f}.efficiency-bin.high .efficiency-bar-track i{background:var(--accent)}.efficiency-bin:hover .efficiency-bar-track i,.efficiency-bin.selected .efficiency-bar-track i{filter:brightness(1.2) drop-shadow(0 0 5px #55d99666)}.efficiency-bin.selected{border-radius:8px;background:#ffffff08}.efficiency-bin.preferred .efficiency-bin-power{color:var(--accent);font-weight:750}.efficiency-preferred{position:absolute;right:0;top:13px;color:#f6c85f;font-size:11px}.efficiency-profile-empty{align-self:center;min-width:220px;padding:42px 10px;color:var(--muted);font-size:10px;text-align:center}.efficiency-point-detail{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-top:11px;padding-top:11px;border-top:1px solid var(--line)}.efficiency-point-detail>div{padding:7px 8px;border-radius:8px;background:var(--panel)}.efficiency-point-detail span,.efficiency-point-detail b{display:block}.efficiency-point-detail span{color:var(--muted);font-size:8px}.efficiency-point-detail b{margin-top:3px;font-size:10px}.efficiency-point-detail .efficiency-detail-wide{grid-column:1/-1}.efficiency-profile-card:has(.efficiency-point-detail){border-color:#55d99655}
      .export-mode-setting label{display:grid;gap:8px;color:var(--muted);font-size:12px}.export-mode-setting select{width:100%;min-height:44px;padding:0 11px;border:1px solid var(--line);border-radius:10px;background:var(--panel2);color:var(--ink)}.export-mode-setting small{display:block;margin-top:8px;color:var(--muted);font-size:10px}
      .setting input[type="number"]{width:100%;min-height:44px;margin-top:12px;padding:0 11px;border:1px solid var(--line);border-radius:10px;background:var(--panel2);color:var(--ink)}
      .settings-section{grid-column:1/-1;min-width:0}.settings-section>summary{cursor:pointer;list-style:none;min-height:54px}.settings-section>summary::-webkit-details-marker{display:none}.settings-section>summary .section-chevron{margin-left:auto;color:var(--muted);transition:transform .2s}.settings-section[open]>summary .section-chevron{transform:rotate(180deg);color:var(--accent)}.settings-section-content{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding-top:14px}.settings-section:not([open])>summary{padding-bottom:10px}.setting.efficiency-locked{opacity:.48}.setting.efficiency-locked input{pointer-events:none}.flow-grid-line{stroke:var(--line);stroke-width:1;stroke-dasharray:3 4}.flow-zero{stroke:#f4f7f6;stroke-width:2.4;filter:drop-shadow(0 0 3px #ffffff55)}.flow-axis-label,.soc-axis-label{fill:var(--muted);font-size:9px}.soc-zero{stroke:#f4f7f6;stroke-width:1.8;stroke-dasharray:none}
      .status-selector,.status-efficiency{grid-column:1/-1}.selector-summary{display:flex;align-items:center;gap:12px;padding:2px 0 12px}.selector-state{padding:6px 10px;border:1px solid #55d99655;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:11px;font-weight:750}.selector-batteries{display:flex;flex-wrap:wrap;gap:8px;flex:1}.selector-battery{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:10px;background:var(--panel2);font-size:11px}.selector-battery span{color:var(--muted);font-variant-numeric:tabular-nums}.selector-demand{min-width:100px;text-align:right}.selector-demand span,.selector-meta span{display:block;color:var(--muted);font-size:9px}.selector-demand b{display:block;margin-top:3px;font-size:18px}.selector-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding-top:10px;border-top:1px solid var(--line)}.selector-meta>div{min-width:0;padding:8px 10px;border-radius:9px;background:var(--panel2)}.selector-meta b{display:block;margin-top:4px;font-size:11px;overflow-wrap:anywhere}.selector-message,.selector-fallback{margin:10px 0 0;color:var(--muted);font-size:11px}.selector-fallback{color:#f6c85f}.efficiency-status-head{display:flex;justify-content:space-between;gap:12px;padding:10px 0;color:var(--muted);font-size:11px}.efficiency-status-head b{color:var(--ink)}.efficiency-status-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.efficiency-status-grid>article{padding:13px;border:1px solid var(--line);border-radius:12px;background:var(--panel2)}.efficiency-status-grid>article>b{color:var(--accent)}.efficiency-status-grid article>div{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:11px}.efficiency-status-grid span,.efficiency-status-grid small{color:var(--muted)}.efficiency-status-grid small{display:block;margin-top:9px;line-height:1.4}
      @media(max-width:850px){.settings-section-content,.efficiency-status-grid{grid-template-columns:1fr}}
      @media(max-width:600px){.overview-page{display:flex;flex-direction:column;height:100%;overflow-x:hidden;overflow-y:auto;gap:10px;padding:10px}.overview-page>*{width:100%;flex:0 0 auto}.overview-page .hero{order:1;min-height:0}.overview-page .battery-strip{order:2}.overview-page .charge-plan{order:3}.overview-page .discharge-plan{order:4}.overview-page .control-card{order:5}.hero-body{height:auto;min-height:0;flex-direction:row;align-items:center;gap:12px}.hero-body>.clickable-ring .soc-ring{--ring-size:108px!important}.hero-info{min-width:0}.big-state{font-size:22px}.hero-info .metric-grid{grid-template-columns:1fr 1fr;margin-top:10px}.metric{padding:7px 4px 5px 0}.metric span{font-size:9px}.metric b{font-size:14px}.export-overview small{display:none}.battery-strip{grid-template-columns:repeat(4,minmax(0,1fr));padding:8px 6px}.mini-battery{min-width:0;flex-direction:column;text-align:center;justify-content:center}.mini-battery .soc-ring{--ring-size:68px!important;transform:none}.mini-battery b{font-size:11px}.plan-card{min-height:88px}.mobile-control-toggle{display:flex;width:100%;min-height:42px;align-items:center;justify-content:center;gap:7px;border:1px solid var(--line);border-radius:10px;background:var(--panel2);color:var(--ink)}.mobile-control-toggle ha-icon{--mdc-icon-size:18px}.control-card:not(.mobile-open)>.auto-row,.control-card:not(.mobile-open)>.manual-controls,.control-card:not(.mobile-open)>.charge-planning{display:none!important}.results-grid{grid-template-columns:1fr}.result-explanation{grid-column:auto}.page[data-page="results"]{padding:12px}.appbar{gap:4px}.tabs{flex:1;justify-content:space-around}.tab{padding:0 8px}}
      .discharge-battery-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.discharge-battery-card{padding:10px;border-radius:10px;background:var(--panel)}.discharge-battery-card b,.discharge-battery-card span,.discharge-battery-card small{display:block}.discharge-battery-card span{margin:5px 0;color:var(--accent);font-size:15px}.discharge-battery-card small{color:var(--muted);font-size:9px}
      @media(max-width:600px){.discharge-battery-grid{grid-template-columns:1fr}}
      .graph-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
      .graph-wide{grid-column:1/-1}.graph-half{min-width:0;min-height:330px}
      .results-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
      .inventory-result{grid-column:1/-1}
      .inventory-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
      .inventory-kpis>div{display:flex;flex-direction:column;gap:5px;padding:14px;border-radius:12px;background:var(--panel2);border:1px solid var(--line)}
      .inventory-kpis span{color:var(--muted);font-size:11px}.inventory-kpis b{font-size:19px;color:var(--accent)}
      .bar-charge,.bar-discharge{filter:none}.current-price-bar{stroke:none;filter:none}
      .price-grid line{stroke:#aab4b833;stroke-width:1.5}.price-grid .price-zero-line{stroke:#f4f7f699;stroke-width:2.2}
      .price-detail span{display:grid;gap:4px;text-align:right}.price-detail small{display:block;color:var(--muted);font-size:10px}
      .event-counts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-bottom:10px}.event-counts>div{padding:8px 9px;border-radius:9px;background:var(--panel2)}.event-counts b,.event-counts span{display:block}.event-counts span,.event-counts>small{color:var(--muted);font-size:9px;margin-top:3px}.event-filters{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}.event-filters button{padding:6px 9px;border:1px solid var(--line);border-radius:999px;background:var(--panel2);color:var(--muted);font-size:10px;cursor:pointer}.event-filters button.active{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}.event{width:100%;border:0;border-bottom:1px solid var(--line);background:transparent;color:var(--ink);text-align:left;cursor:pointer}.event:hover{background:var(--panel2)}.event.error>i{background:var(--red);box-shadow:0 0 8px #fb6b6b77}.event.warning>i{background:#f6c85f;box-shadow:0 0 8px #f6c85f77}.event.recovered>i{background:var(--accent);box-shadow:0 0 8px #55d99677}.event.info>i{background:var(--blue);box-shadow:0 0 8px #8aa8ff77}.event-modal{position:fixed;inset:0;z-index:20;display:grid;place-items:center;padding:18px;background:#000a}.event-modal[hidden]{display:none}.event-modal-card{position:relative;width:min(620px,100%);max-height:80vh;overflow:auto;padding:22px;border:1px solid var(--line);border-radius:16px;background:var(--panel);box-shadow:0 25px 70px #0008}.event-modal-close{position:absolute;top:10px;right:12px;width:36px;height:36px;border:0;border-radius:50%;background:var(--panel2);color:var(--ink);font-size:24px;cursor:pointer}.event-modal-card>div>small,.event-modal-card p{color:var(--muted)}.event-modal-card h3{margin:7px 42px 8px 0}.event-modal-card h4{margin:18px 0 7px}.event-detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}.event-detail-grid>div{padding:9px;border-radius:9px;background:var(--panel2)}.event-detail-grid span,.event-detail-grid b{display:block}.event-detail-grid span{color:var(--muted);font-size:9px}.event-detail-grid b{margin-top:3px;font-size:11px}.event-modal pre{margin:0;padding:12px;border-radius:10px;background:var(--panel2);color:var(--ink);white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.5 ui-monospace,monospace}
      .event-filters .event-clear{margin-left:auto;border-color:#fb6b6b66;color:var(--red);display:flex;align-items:center;gap:4px}.event-filters .event-clear:hover{background:#fb6b6b18}
      @media(max-width:850px){.graph-grid,.results-grid{grid-template-columns:1fr}.graph-wide,.inventory-result{grid-column:auto}.inventory-kpis{grid-template-columns:1fr}}
      @media(max-width:850px){.efficiency-profile-grid{grid-template-columns:1fr}.efficiency-result-heading{align-items:flex-start;flex-direction:column}.efficiency-bin-chart{grid-auto-columns:minmax(52px,1fr)}}
      @media(max-width:600px){.event-counts,.event-detail-grid{grid-template-columns:1fr}.selector-summary{align-items:flex-start;flex-wrap:wrap}.selector-batteries{order:3;flex-basis:100%}.selector-demand{margin-left:auto}.selector-meta{grid-template-columns:1fr}.chart-note{display:grid;grid-template-columns:1fr 1fr;gap:7px 12px;position:static;height:auto;margin-top:7px}.chart-note .source-warning{grid-column:1/-1}.graph-wide{min-height:0}#price-chart{height:auto;min-height:250px;padding-bottom:0}#price-chart svg{height:250px}.manual-battery-row>div{gap:7px;font-size:10px}}
      .assigned-hour-group{border-radius:8px;overflow:hidden}.assigned-hour-group[data-expandable="true"]{cursor:pointer}.assigned-hour-group.expanded{background:var(--panel2)}.assigned-hour-chevron{position:absolute;right:5px;bottom:2px;--mdc-icon-size:13px!important}.assigned-hour-details{display:grid;padding:2px 8px 6px 11px;border-left:3px solid var(--line);background:var(--panel2)}.assigned-hour-detail{display:grid;grid-template-columns:minmax(82px,1fr) minmax(90px,1fr) auto;gap:7px;padding:6px 4px;border-top:1px solid var(--line);font-size:9px}.assigned-hour-detail span{color:var(--muted)}.assigned-hour-detail em{font-style:normal;font-weight:700;white-space:nowrap}
      .cell-chart{background:linear-gradient(180deg,#8aa8ff08 0 49.5%,#ffffff24 49.5% 50.5%,#55d99608 50.5% 100%)}
      .chip.bad{color:var(--red);border-color:#fb6b6b66;background:#fb6b6b12}.outage-duration{display:flex;align-items:center;gap:9px;width:max-content;margin-top:14px;padding:9px 12px;border:1px solid #fb6b6b55;border-radius:11px;background:#fb6b6b0d}.outage-duration[hidden]{display:none}.outage-duration ha-icon{color:var(--red);--mdc-icon-size:20px}.outage-duration span,.outage-duration b{display:block}.outage-duration span{color:var(--muted);font-size:9px}.outage-duration b{margin-top:2px;font-size:14px;font-variant-numeric:tabular-nums}
    </style>`;
  }
}

if (!customElements.get("openems-m-panel")) customElements.define("openems-m-panel", OpenEmsMPanel);
