const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..", "..");
const frontend = path.join(
  root, "config", "custom_components", "openems_m_panel", "frontend"
);
const server = http.createServer((request, response) => {
  const requestPath = new URL(request.url, "http://127.0.0.1").pathname;
  if (
    requestPath === "/openems-panel.js"
    || requestPath.startsWith("/modules/")
  ) {
    const relative = requestPath === "/openems-panel.js"
      ? "openems-panel.js" : requestPath.slice(1);
    response.writeHead(200, { "Content-Type": "text/javascript" });
    response.end(fs.readFileSync(path.join(frontend, relative)));
    return;
  }
  response.writeHead(200, { "Content-Type": "text/html" });
  response.end(`<!doctype html><meta name="viewport" content="width=device-width">
    <script type="module" src="/openems-panel.js"></script>`);
});

let browser;
(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({
    viewport: { width: 412, height: 915 },
  });
  await page.goto(`http://127.0.0.1:${port}`);
  await page.waitForFunction(() => customElements.get("openems-m-panel"));
  const result = await page.evaluate(async () => {
    const start = new Date();
    start.setMinutes(Math.floor(start.getMinutes() / 15) * 15, 0, 0);
    start.setTime(start.getTime() - 24 * 3600000);
    const prices = Array.from({ length: 192 }, (_, index) => {
      const from = new Date(start.getTime() + index * 15 * 60000).toISOString();
      const wave = Math.sin(index / 15) * 0.035;
      return { from, price: 0.24 + wave };
    });
    const market = prices.map((item) => ({ ...item, price: item.price - 0.12 }));
    const marketPlus = prices.map((item) => ({ ...item, price: item.price - 0.045 }));
    const entity = (state, attributes = {}) => ({
      state: String(state), attributes, last_changed: new Date().toISOString(),
      last_updated: new Date().toISOString(),
    });
    const states = {
      "input_boolean.openems_actief": entity("on"),
      "input_select.openems_accuverdeling": entity("Alle accu’s verdelen"),
      "sensor.meterkast_frank_energie_prijzen_current_electricity_price_all_in": entity(prices[96].price, { prices }),
      "sensor.meterkast_frank_energie_prijzen_current_electricity_market_price": entity(market[96].price, { prices: market }),
      "sensor.meterkast_frank_energie_prijzen_current_electricity_price": entity(marketPlus[96].price, { prices: marketPlus }),
      "input_select.openems_ontlaad_tijdresolutie": entity("15 minuten"),
      "sensor.berging_accu1_ac_vermogen": entity(363, { unit_of_measurement: "W" }),
      "sensor.berging_accu2_ac_vermogen": entity(0, { unit_of_measurement: "W" }),
      "sensor.berging_accu3_ac_vermogen": entity(0, { unit_of_measurement: "W" }),
      "sensor.openems_volgend_laadblok": entity("theoretisch actief", {
        start: prices[96].from,
        einde: new Date(new Date(prices[96].from).getTime() + 15 * 60000).toISOString(),
        blokken: [{
          start: prices[96].from,
          einde: new Date(new Date(prices[96].from).getTime() + 15 * 60000).toISOString(),
        }],
      }),
      "input_select.openems_grid_export_modus": entity("Alleen simuleren"),
      "sensor.openems_volgend_exportblok": entity("simulatie", {
        simulatie: true,
        prijsblokken: [
          {
            start: prices[100].from,
            einde: new Date(new Date(prices[100].from).getTime() + 15 * 60000).toISOString(),
          },
          {
            start: prices[101].from,
            einde: new Date(new Date(prices[101].from).getTime() + 15 * 60000).toISOString(),
          },
        ],
        blokken: [],
      }),
    };
    [1, 2, 3].forEach((battery) => {
      states[`sensor.berging_accu${battery}_batterij_soc`] = entity(
        battery === 1 ? 30 : 62, { unit_of_measurement: "%" }
      );
      states[`sensor.berging_accu${battery}_batterij_totale_energie`] = entity(
        5.12, { unit_of_measurement: "kWh" }
      );
      states[`sensor.berging_accu${battery}_volledige_cycli_bms`] = entity(18 + battery);
      states[`sensor.berging_accu${battery}_interne_temperatuur`] = entity(
        34 + battery, { unit_of_measurement: "°C" }
      );
      states[`sensor.berging_accu${battery}_omvormer_status`] = entity(
        battery === 1 ? "discharging" : "standby"
      );
      states[`sensor.berging_accu${battery}_batterijspanning`] = entity(
        52.1, { unit_of_measurement: "V" }
      );
      Array.from({ length: 16 }, (_, index) => {
        const voltage = 3.221 + battery * 0.001
          + (index === 3 ? -0.011 : index === 11 ? 0.008 : index * 0.0003);
        states[`sensor.berging_accu${battery}_batterij_pack_1_cel_${index + 1}_spanning`] = entity(
          voltage.toFixed(3), { unit_of_measurement: "V" }
        );
      });
    });
    Object.entries(states).forEach(([entityId, value]) => {
      value.entity_id = entityId;
    });
    const panel = document.createElement("openems-m-panel");
    const serviceCalls = [];
    const hass = {
      states,
      callWS: async () => [],
      callService: async (domain, service, data) => {
        serviceCalls.push({ domain, service, data });
      },
      connection: { subscribeEvents: async () => () => undefined },
      config: { unit_system: {} },
      locale: { language: "nl" },
    };
    panel.hass = hass;
    document.body.append(panel);
    panel._showTab("graphs");
    const quarter = {
      marketSegments: panel.shadowRoot.querySelectorAll("#market-chart .market-segment").length,
      marketPlusSegments: panel.shadowRoot.querySelectorAll("#market-chart .marketplus-segment").length,
      allInSegments: panel.shadowRoot.querySelectorAll("#market-chart .allin-segment").length,
      resolution: panel.shadowRoot.querySelector("#market-chart")?.textContent.includes("Resolutie 15 min"),
      currentPlanned: [
        "bar-charge-window", "bar-discharge-window",
        "bar-charge", "bar-discharge",
      ].some((name) => panel.shadowRoot.querySelector(
        "#price-chart .current-price-bar"
      )?.classList.contains(name)),
      exportAllowed: panel.shadowRoot.querySelectorAll(
        "#price-chart .bar-export-window"
      ).length,
      exportPlanned: panel.shadowRoot.querySelectorAll(
        "#price-chart .bar-export-plan"
      ).length,
    };
    states["input_select.openems_grid_export_modus"].state = "Automatisch exporteren";
    // Het gepubliceerde blok is de bron van waarheid. Een verouderd
    // simulatie-attribuut mag de grafiek niet donkerpaars maken.
    states["sensor.openems_volgend_exportblok"].attributes.blokken = [{
      start: prices[101].from,
      einde: new Date(new Date(prices[101].from).getTime() + 15 * 60000).toISOString(),
      doel_export_w: 1200,
    }];
    panel._renderPriceChart();
    const automaticExportPlanned = panel.shadowRoot.querySelectorAll(
      "#price-chart .bar-export-plan"
    ).length;
    const automaticExportAllowed = panel.shadowRoot.querySelectorAll(
      "#price-chart .bar-export-window"
    ).length;
    states["input_select.openems_ontlaad_tijdresolutie"].state = "60 minuten";
    panel._renderPriceChart();
    const manualHour = new Date();
    manualHour.setMinutes(0, 0, 0);
    manualHour.setHours(manualHour.getHours() - 1);
    const historyItem = (entityId, state, minute) => ({
      entity_id: entityId,
      state,
      last_changed: new Date(
        manualHour.getTime() + minute * 60000
      ).toISOString(),
    });
    panel._statusHistory = {
      "sensor.openems_hoofdschakelaar": [
        historyItem(
          "sensor.openems_hoofdschakelaar", "HANDMATIG", 0
        ),
      ],
      "sensor.openems_handmatige_status": [
        historyItem("sensor.openems_handmatige_status", "Stop", 0),
        historyItem("sensor.openems_handmatige_status", "Ontladen", 10),
      ],
    };
    const dominantManualMode = panel._dominantManualPriceMode(
      manualHour, 60 * 60000
    );
    const manualColours = {
      stop: panel._manualPriceMode("Stop"),
      self: panel._manualPriceMode("Zelfconsumptie"),
      charge: panel._manualPriceMode("Laden"),
      discharge: panel._manualPriceMode("Ontladen"),
    };
    const graphOrder = [...panel.shadowRoot.querySelectorAll(".graph-grid > .card")]
      .map((card) => card.querySelector(".chart")?.id);
    panel._showTab("batteries");
    const cellMonitor = {
      monitors: panel.shadowRoot.querySelectorAll(".cell-monitor").length,
      columns: panel.shadowRoot.querySelectorAll(".cell-column").length,
      live: panel.shadowRoot.querySelector("#b1-cells-status")?.textContent,
      delta: panel.shadowRoot.querySelector("#b1-cells-delta")?.textContent,
      minimums: panel.shadowRoot.querySelectorAll(".cell-column.minimum").length,
      maximums: panel.shadowRoot.querySelectorAll(".cell-column.maximum").length,
    };
    panel._showTab("settings");
    const settingsSections = [
      ...panel.shadowRoot.querySelectorAll("details.settings-section"),
    ];
    panel.shadowRoot.querySelector('[data-auto-mode="off"]').click();
    panel.hass = hass;
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const manualPending = panel.shadowRoot.querySelector(
      '[data-auto-mode="off"]'
    ).classList.contains("active");
    const distributionSelect = panel.shadowRoot.querySelector(
      "#battery-distribution-select"
    );
    distributionSelect.value = "Efficiënte accuselector (aanbevolen)";
    distributionSelect.dispatchEvent(new Event("change", { bubbles: true }));
    panel.hass = hass;
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const distributionPending = distributionSelect.value;
    return {
      tabs: panel.shadowRoot.querySelectorAll(".tab").length,
      overview: Boolean(panel.shadowRoot.querySelector(".overview-page")),
      mobileRule: panel.shadowRoot.textContent.includes("Bediening tonen"),
      priceTicks: panel.shadowRoot.querySelectorAll("#price-chart .price-grid text").length,
      quarter,
      automaticExportPlanned,
      automaticExportAllowed,
      dominantManualMode,
      manualColours,
      hourlySegments: panel.shadowRoot.querySelectorAll("#market-chart .market-segment").length,
      hourlyResolution: panel.shadowRoot.querySelector("#market-chart")?.textContent.includes("Resolutie 60 min"),
      graphOrder,
      cellMonitor,
      settingsSections: settingsSections.length,
      openSettingsSections: settingsSections.filter((item) => item.open).length,
      quickVisible: Boolean(panel.shadowRoot.querySelector(
        ".settings-grid > .quick-settings-section"
      )),
      manualPending,
      distributionPending,
      serviceCalls,
    };
  });
  if (
    result.tabs !== 6 || !result.overview || !result.mobileRule
    || result.priceTicks < 3 || result.quarter.marketSegments < 96
    || result.quarter.marketPlusSegments !== result.quarter.marketSegments
    || result.quarter.allInSegments !== result.quarter.marketSegments
    || !result.quarter.resolution
    || result.quarter.exportAllowed < 1 || result.quarter.exportPlanned !== 0
    || result.automaticExportPlanned !== 1
    || result.automaticExportAllowed !== 1
    || result.dominantManualMode !== "self"
    || JSON.stringify(result.manualColours) !== JSON.stringify({
      stop: "standby", self: "self", charge: "charge", discharge: "self",
    })
    || result.hourlySegments < 24 || result.hourlySegments > 48
    || !result.hourlyResolution
    || result.graphOrder.join(",") !== [
      "price-chart", "power-chart-today", "power-chart-yesterday",
      "power-chart-week", "soc-chart-today", "soc-chart-yesterday",
      "market-chart",
    ].join(",")
    || result.cellMonitor.monitors !== 3
    || result.cellMonitor.columns !== 48
    || result.cellMonitor.live !== "16/16 live"
    || !result.cellMonitor.delta?.endsWith("mV")
    || result.cellMonitor.minimums !== 3
    || result.cellMonitor.maximums !== 3
    || result.settingsSections !== 7 || result.openSettingsSections !== 0
    || !result.quickVisible || !result.manualPending
    || result.distributionPending !== "Efficiënte accuselector (aanbevolen)"
    || !result.serviceCalls.some((call) => (
      call.domain === "input_boolean" && call.service === "turn_off"
      && call.data.entity_id === "input_boolean.openems_actief"
    ))
    || !result.serviceCalls.some((call) => (
      call.domain === "input_select" && call.service === "select_option"
      && call.data.entity_id === "input_select.openems_accuverdeling"
    ))
  ) {
    throw new Error(`Dashboard DOM-smoke faalde: ${JSON.stringify(result)}`);
  }
  await browser.close();
  server.close();
  console.log("1 browser/DOM test passed");
})().catch((error) => {
  if (browser) browser.close();
  server.close();
  console.error(error);
  process.exitCode = 1;
});
