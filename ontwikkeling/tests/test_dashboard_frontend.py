from pathlib import Path


FRONTEND = (
    Path(__file__).parents[2]
    / "config"
    / "custom_components"
    / "openems_m_panel"
    / "frontend"
    / "openems-panel.js"
)
PLANNING_CLASSIFIER = FRONTEND.parent / "modules" / "price-chart-planning.mjs"


def source():
    return FRONTEND.read_text(encoding="utf-8")


def classifier_source():
    return PLANNING_CLASSIFIER.read_text(encoding="utf-8")


def test_openems_logo_is_used_in_dashboard_header():
    text = source()
    logo = (
        FRONTEND.parent
        / "assets"
        / "openems-logo.png"
    )
    assert logo.is_file()
    assert 'src="/openems_m_panel_static/assets/openems-logo.png?v=' in text
    assert '<div class="logo">O</div>' not in text


def test_obsolete_external_round_trip_efficiency_is_not_rendered():
    text = source()
    assert 'row("Totaal rendement"' not in text
    assert "efficiency: this._displayState(b.efficiency)" not in text


def test_battery_controls_are_collapsed_under_settings():
    text = source()
    assert '<details class="battery-settings">' in text
    assert "Instellingen</summary><div class=\"battery-controls\">" in text


def test_all_backup_switches_remain_manual_dashboard_controls():
    text = source()
    assert 'this._wireSwitch(`b${b.n}-backup`, b.backup);' in text
    assert '${toggle("backup", "Back-upfunctie")}' in text
    assert 'backup-protected' not in text


def test_battery_diagnostics_show_modbus_versions_ac_and_safe_reset():
    text = source()

    for fragment in (
        "modbus_verbinding",
        "modbus_adres",
        "firmwareversie",
        "bms_versie",
        "ems_versie",
        "vms_versie",
        "communicatiemodule_firmware",
        "ac_spanning",
        "ac_stroom",
        "ac_vermogen",
        "ac_frequentie",
        "ac_off_grid_spanning",
        "ac_off_grid_stroom",
        "ac_off_grid_vermogen",
    ):
        assert fragment in text
    assert 'button.${p}_${batterySuffix("apparaat_resetten", lang)}' in text
    assert '"apparaat_resetten": "reset_device"' in text
    assert 'callService("button", "press"' in text
    assert "Accu ${b.n} opnieuw opstarten?" in text
    assert "button.berging_accu${n}_fabrieksinstellingen_herstellen" not in text
    assert "wifi_signaalsterkte" not in text


def test_overview_shows_grid_status_and_live_outage_duration():
    text = source()

    assert 'gridStatus: "sensor.openems_netstatus"' in text
    assert 'gridOutageDuration: "sensor.openems_netuitval_duur"' in text
    assert 'id="outage-duration"' in text
    assert 'netCode === "off_grid"' in text
    assert '"Backup actief"' in text
    assert '"Planning gepauzeerd"' in text


def test_total_soc_series_is_not_rendered():
    text = source()
    assert "SOC per accu · gisteren" in text
    assert "SOC per accu · vandaag" in text
    assert "soc-chart-yesterday" in text
    assert "soc-chart-today" in text


def test_empty_time_uses_smoothed_home_forecast_in_all_operating_modes():
    text = source()
    method = text.split("  _estimatedEmpty() {", 1)[1].split(
        "  _powerEntities()", 1
    )[0]
    assert "E.homeForecast" in method
    assert "actualPower" not in method
    assert "E.minimumSoc" in method
    assert "usableEnergy" in method
    assert "this._estimatedEmpty()" in text
    assert '"Rustig verbruik", "estimated-home-power"' in text


def test_price_chart_distinguishes_price_window_from_actual_plan():
    text = source()
    assert "bar-charge-window" in text
    assert '"prijsblokken"' in text
    assert "Laden mogelijk" in text
    assert "Laden gepland / gemeten" in text


def test_price_chart_has_discharge_window_and_now_marker():
    text = source()
    assert "bar-discharge-window" in text
    assert "Zelfconsumptie mogelijk" in text
    assert "Zelfconsumptie gepland / gemeten" in text
    assert "now-marker" in text
    assert ">NU</text>" in text


def test_price_chart_distinguishes_export_permission_from_export_plan():
    text = source()
    assert 'nextExport: "sensor.openems_volgend_exportblok"' in text
    assert "bar-export-window" in text
    assert "bar-export-plan" in text
    assert "Terugleveren toegestaan" in text
    assert "Terugleveren gepland / gemeten" in text
    assert ".bar-export-window{fill:#6540a4" in text
    assert ".bar-export-plan{fill:#c6a7ff" in text


def test_price_chart_refreshes_on_the_actual_export_helpers():
    text = source()
    fingerprint = text.split("  _priceChartFingerprint() {", 1)[1].split(
        "  _priceData()", 1
    )[0]
    assert "E.gridExportAllowed" in fingerprint
    assert "E.exportMode" in fingerprint
    assert "openems_grid_export_allowed" not in fingerprint
    assert "openems_grid_export_mode" not in fingerprint


def test_status_page_contains_return_delivery_advice():
    text = source()
    assert "attrs.terugleverplan" in text
    assert "Terugleveradvies" in text
    assert "Voorspelde terugleverblokken" in text
    assert "Reserve eigen verbruik" in text
    assert ".export-advice-block{border-left:3px solid #c6a7ff}" in text


def test_current_price_interval_uses_live_power_with_candidate_fallback():
    text = source()
    mode = text.split(
        "  _priceModeAt(time, stepMs = 3600000) {", 1
    )[1].split("  _priceCandidateAt", 1)[0]
    assert "time <= now && now < intervalEnd" in mode
    assert "this._liveBatteryMode()" in mode
    assert "_liveBatteryMode()" in mode
    candidate = text.split(
        "  _priceCandidateAt(time, stepMs = 3600000) {", 1
    )[1].split("  _dischargeCandidateAt", 1)[0]
    assert "if (intervalEnd <= now) return false" not in candidate
    assert "time <= now && now < intervalEnd" not in candidate


def test_price_charts_have_shared_euro_axis_and_adaptive_resolution():
    text = source()
    assert "_priceAxis(values, height, top, bottom)" in text
    assert 'class="price-grid"' in text
    assert "€/kWh" in text
    assert "_selectedPriceStepMs(rows)" in text
    assert 'selected.startsWith("15")' in text
    assert 'selected.startsWith("60")' in text
    assert 'this._state(E.dischargeResolution, "60 minuten")' in text
    assert "Resolutie 15 min" in text
    assert "Resolutie 60 min" in text


def test_market_chart_uses_return_price_and_all_in_layers():
    text = source()
    assert 'frankMarket: "sensor.frank_energie_stroomprijzen_huidige_elektriciteit_inkoopprijs"' in text
    assert "openems_frank_terugleververgoeding" in text
    assert "Terugleverprijs" in text
    assert 'segment(0, p.market, "market-segment"' in text
    assert 'segment(p.market, middle, "marketplus-segment"' in text
    assert 'segment(middle, p.value, "allin-segment"' in text
    assert ".market-segment{fill:#55d996}" in text
    assert ".marketplus-segment{fill:#8aa8ff}" in text
    assert ".allin-segment{fill:#ff9f43}" in text


def test_settings_page_has_persistent_default_actions():
    text = source()
    assert 'id="save-defaults"' in text
    assert 'id="restore-defaults"' in text
    assert 'id="restore-factory"' in text


def test_auto_overview_uses_scheduler_status_instead_of_manual_status():
    text = source()
    assert "if (auto) manual = this._obj(E.status);" in text
    assert '? "Auto"' in text
    assert '[[E.soc,"#ffffff","Totaal"]' not in text


def test_overview_system_state_prefers_real_battery_power_direction():
    text = source()
    patch = text.split("  _patch() {", 1)[1].split(
        "  _patchManualStatus", 1
    )[0]
    assert "actualPower < -25" in patch
    assert '? "Laden"' in patch
    assert "actualPower > 25" in patch
    assert '? \"Terugleveren\" : \"Zelfconsumptie\"' in patch


def test_overview_battery_summaries_update_without_visiting_battery_tab():
    text = source()
    patch = text.split("  _patch() {", 1)[1].split(
        "  _patchManualStatus", 1
    )[0]
    assert "E.batteries.forEach((b) => this._patchBatterySummary(b));" in patch
    assert "_patchBatterySummary(b)" in text


def test_overview_shows_battery_quarantine_and_live_retry_countdown():
    text = source()
    assert 'batteryHealth: "sensor.openems_accugezondheid"' in text
    assert "Tijdelijk buiten gebruik" in text
    assert "Veilige hertest" not in text
    assert "proefvermogen_w" not in text
    assert "opnieuw_proberen_op" in text
    assert "this._compactDuration(remaining)" in text
    assert "E.batteries.forEach((b) => this._patchBatterySummary(b));" in text


def test_dashboard_keeps_history_cache_and_recovers_after_reconnect():
    text = source()
    assert "DASHBOARD_SESSION_CACHE" in text
    assert "connectedCallback()" in text
    assert 'document.addEventListener("visibilitychange"' in text
    assert "this._renderCachedHistory();" in text
    assert "this._historyAttemptAt < 10000" in text


def test_inverter_status_and_power_have_separate_centered_elements():
    text = source()
    assert 'class="battery-live"' in text
    assert "-inverter-summary`" in text
    assert 'discharge: "Ontladen"' in text


def test_cell_charts_center_each_pack_on_one_shared_deviation_scale():
    text = source()
    method = text.split("  _sharedCellDeviationScale() {", 1)[1].split(
        "  _patchBatterySummary", 1
    )[0]
    assert "E.batteries" in method
    assert ".flatMap" in method
    assert "Math.abs(value - average) * 1000" in method
    assert "Math.max(6, Math.ceil(observed * 1.25))" in method
    assert "50 +" in text
    assert "sharedScale.maximumMv" in text
    assert "midden = gemiddelde" in text


def test_history_reader_supports_home_assistant_list_responses():
    text = source()
    assert "_historyItems(response, entityId)" in text
    assert "Array.isArray(response[0])" in text


def test_rs485_toggle_does_not_write_user_work_mode():
    text = source()
    assert "_wireRs485(b)" in text
    rs485_method = text.split("  _wireRs485(b) {", 1)[1].split(
        "  _savePowerEntity", 1
    )[0]
    assert '"switch"' in rs485_method
    assert '_setBooleanEntity("switch", b.rs485, turningOn)' in rs485_method
    assert "select_option" not in rs485_method
    assert "b.work" not in rs485_method


def test_manual_error_is_split_per_battery_and_wraps_inside_card():
    text = source()
    assert '.split(" | ")' in text
    assert '.join("\\n")' in text
    assert "white-space:pre-wrap" in text
    assert "overflow-wrap:anywhere" in text
    assert "overflow-y:auto" in text


def test_manual_card_keeps_battery_statuses_separate_from_current_action():
    text = source()
    assert 'id="manual-battery-statuses"' in text
    assert "manual?.attributes?.accu_statussen" in text
    assert 'class="manual-action"' in text
    assert "manual?.attributes?.actie" in text
    assert ".manual-battery-row.ok span" in text
    assert ".manual-battery-row.error span" in text


def test_system_card_uses_concise_manual_status_copy():
    text = source()
    assert 'this.$("system-reason").hidden = !auto' in text
    assert '? (status?.attributes?.reden || "Geen reden beschikbaar") : ""' in text


def test_manual_verification_has_live_countdown():
    text = source()
    assert "_manualCountdownTimer" in text
    assert "Opdracht wordt gecontroleerd over ${remaining}" in text
    assert "Verificatie wordt uitgevoerd…" in text


def test_active_charge_plan_renders_total_and_each_battery():
    text = source()
    assert 'class="charge-planning"' in text
    assert '"Totaal", totalEnergy, totalPower, true' in text
    assert "planned.attributes?.per_accu" in text


def test_rs485_off_has_no_automatic_retry_loop():
    text = source()
    rs485_method = text.split("  _wireRs485(b) {", 1)[1].split(
        "  _savePowerEntity", 1
    )[0]
    assert "attempt < 3" not in rs485_method
    assert "verificationAt" not in rs485_method


def test_self_consumption_choice_and_disabled_fallback_are_visible():
    text = source()
    assert 'data-selfcons="Marstek"' in text
    assert 'data-selfcons="OpenEMS"' in text
    assert 'id="fallback-setting"' in text
    assert 'fallbackCard.classList.toggle("frozen", !openems)' in text


def test_battery_entity_configuration_uses_shared_helpers():
    text = source()
    assert "openems_accu${battery.n}_${field}" in text
    assert 'data-entity-helper="${battery.helpers[field]}"' in text


def test_status_tab_renders_full_energy_planning_overview():
    text = source()
    assert 'planning: "sensor.openems_planningsoverzicht"' in text
    assert 'id="planning-status-content"' in text
    assert "_renderPlanningStatus()" in text
    assert "Gemiddelde prijs gepland laadblok" in text
    assert "Piek-laadvermogen" in text
    assert "Laadvermogen per accu" in text
    assert "Geselecteerde laadblokken" in text
    assert "Zelfconsumptieadvies" in text
    assert "Geen winstgevende ontlaadblokken gevonden" in text
    assert 'class="battery-plan-grid"' in text
    assert "Benodigd uit net" in text
    assert "Minimumvermogen" in text


def test_status_advice_details_are_not_duplicated_below_battery_planning():
    text = source()
    planning_method = text.split("  _renderPlanningStatus() {", 1)[1].split(
        "  _verificationHtml", 1
    )[0]
    assert "planning-detail-grid" not in planning_method
    assert "battery-plan-grid" in planning_method
    advice_method = text.split("  _renderAdviceSummaries() {", 1)[1].split(
        "  _renderPlanningStatus", 1
    )[0]
    assert "Geselecteerde laadblokken" in advice_method
    assert "Geselecteerde ontlaadblokken" in advice_method
    assert "Voorspelde terugleverblokken" in advice_method


def test_week_history_retries_and_keeps_previous_valid_graph():
    text = source()
    assert "for (let attempt = 0; attempt < 3; attempt++)" in text
    assert "this._weekHistoryAvailable = true" in text
    assert "&& !this._weekHistoryAvailable" in text


def test_future_export_colour_trusts_published_planner_intervals():
    text = source()
    candidate = text.split(
        "  _exportCandidateAt(time, stepMs = 3600000) {", 1
    )[1].split("  _exportPlannedAt", 1)[0]
    planned = text.split(
        "  _exportPlannedAt(time, stepMs = 3600000) {", 1
    )[1].split("  _historicalExportIntervals", 1)[0]
    assert 'this._intervals(E.nextExport, "prijsblokken")' in candidate
    assert "_exportEconomicAt" not in text
    assert 'mode === "Automatisch exporteren"' in planned
    assert 'this._intervals(E.nextExport, "blokken")' in planned
    assert "simulatie === false" not in planned


def test_finished_price_hours_use_actual_export_and_measurements_before_fallback():
    text = source()
    method = text.split(
        "  _priceClassificationAt(time, stepMs = 3600000) {", 1
    )[1].split(
        "  _plannedPriceModeAt", 1
    )[0]
    assert 'phase: isPast ? "past"' in method
    assert "this._historicalExportWasActive(time, stepMs)" in method
    assert "this._dominantManualPriceMode(time, stepMs)" in method
    assert "this._historicalBatteryMode(time, stepMs)" in method
    assert "classifyPricePlanningSlot({" in method
    assert "batteryPowerEntities = E.batteries.map((b) => b.actual)" in text
    assert "[home, grid, ...batteryPowerEntities]" in text
    fallback = text.split(
        "  _historicalBatteryMode(time, stepMs = 3600000) {", 1
    )[1].split("  _modeLabel", 1)[0]
    assert "chargingBatteries >= 1" in fallback
    assert "dischargingBatteries >= 1" in fallback


def test_current_price_hour_uses_manual_majority_or_auto_plan():
    text = source()
    method = text.split(
        "  _priceClassificationAt(time, stepMs = 3600000) {", 1
    )[1].split("  _plannedPriceModeAt", 1)[0]
    assert 'this._state(E.auto) !== "on"' in method
    assert "_dominantManualPriceMode(time, stepMs)" in method
    assert "this._manualPriceMode(" in method
    assert "this._state(E.manualStatus)" in method
    assert "classifyPricePlanningSlot({" in method
    assert "exportPlanned:" in method
    assert "exportCandidate:" in method


def test_finished_economic_charge_candidates_remain_dark_green():
    text = source()
    method = text.split(
        "  _priceCandidateAt(time, stepMs = 3600000) {", 1
    )[1].split("  _dischargeCandidateAt", 1)[0]
    assert 'this._intervals(E.nextCharge, "prijsblokken")' in method
    assert "intervalEnd <= now" not in method


def test_manual_hour_colours_follow_selected_manual_action():
    text = source()
    mapping = text.split("  _manualPriceMode(state) {", 1)[1].split(
        "  _dominantManualPriceMode", 1
    )[0]
    assert 'value === "laden"' in mapping
    assert 'return "charge"' in mapping
    assert 'value === "zelfconsumptie"' in mapping
    assert 'return "self"' in mapping
    assert 'value === "ontladen"' in mapping
    assert 'return "export"' not in mapping
    assert 'value === "stop"' in mapping
    assert 'return "standby"' in mapping
    majority = text.split(
        "  _dominantManualPriceMode(time, stepMs = 3600000) {", 1
    )[1].split("  _liveBatteryMode()", 1)[0]
    assert "dominant[1] > (end - start) / 2" in majority
    classifier = classifier_source()
    assert "manualClassification(signals.manualMode, false)" in classifier
    assert "manualMode || signals.liveMode" in classifier


def test_export_colours_are_retained_in_price_history():
    text = source()
    assert "this._exportPlanHistory, E.nextExport" in text
    assert '_historicalExportIntervals("prijsblokken")' in text
    assert '_historicalExportIntervals("blokken")' in text
    assert "this._historicalExportWasActive(time, stepMs)" in text
    assert "Terugleveren · uitgevoerd" in classifier_source()
    history = text.split("  async _loadHistory(force) {", 1)[1].split(
        "  _renderPowerHistory", 1
    )[0]
    assert "E.exportStatus" in history
    assert "entity_ids: [E.nextExport]" in history
    assert "no_attributes: false" in history


def test_measured_grid_export_only_confirms_a_real_planner_block():
    text = source()
    method = text.split(
        "  _historicalExportWasActive(time, stepMs = 3600000) {", 1
    )[1].split(
        "  _historicalBatteryMode(time, stepMs = 3600000) {", 1
    )[0]
    assert "if (statusWasActive) return true" in method
    assert 'this._historicalExportIntervals("blokken")' in method
    assert 'this._historicalExportIntervals("prijsblokken")' not in method
    assert "this._historicalGridExportWasActive(time, stepMs)" in method
    assert "this._powerHistory.grid" in method
    assert "watts < -50" in method
    assert "exportedKwh >= 0.02" in method


def test_today_graphs_are_before_yesterday_for_desktop_and_mobile():
    text = source()
    assert text.index('id="power-chart-today"') < text.index(
        'id="power-chart-yesterday"'
    )
    assert text.index('id="soc-chart-today"') < text.index(
        'id="soc-chart-yesterday"'
    )
    assert ".graph-grid{grid-template-columns:repeat(2" in text
    assert "@media(max-width:850px){.graph-grid" in text


def test_history_loads_recent_graphs_before_week_and_reuses_inflight_request():
    text = source()
    method = text.split("  async _loadHistory(force) {", 1)[1].split(
        "  _renderPowerHistory(", 1
    )[0]
    assert "if (this._historyLoading) return this._historyLoading" in method
    assert "yesterdayStart.toISOString()" in method
    assert "this._renderPowerHistory(false)" in method
    assert method.index("this._renderPowerHistory(false)") < method.index(
        "weekStart.toISOString()"
    )
    assert "this._renderPowerHistory(true)" in method


def test_history_only_refreshes_on_visible_history_tabs_without_overlap():
    text = source()
    assert "this._historyPrefetchTimer = setTimeout" not in text
    assert 'this._tab === "graphs" || this._tab === "status"' in text
    assert 'document.visibilityState !== "hidden"' in text
    assert "if (this._historyLoading) return this._historyLoading" in text


def test_price_chart_does_not_recalculate_export_finances():
    text = source()
    assert "_priceDataForRender" not in text
    assert "matchingBlock?.voorraadkostprijs_eur_kwh" not in text
    assert "blockCost + minimumDifference" not in text


def test_failed_setting_change_shows_visible_home_assistant_notification():
    text = source()
    assert 'new CustomEvent("hass-notification"' in text


def test_frank_status_history_renders_without_waiting_for_power_and_soc():
    text = source()
    method = text.split("  async _loadHistory(force) {", 1)[1].split(
        "  _renderPowerHistory(", 1
    )[0]
    assert "const statusTask" in method
    assert "const recentPowerTask" in method
    assert "const socTask" in method
    assert method.index("this._renderPriceChart()") < method.index(
        "await Promise.allSettled"
    )
    assert "Historie wordt bijgewerkt" in method
    assert "Historie bijgewerkt" in method


def test_price_hours_have_small_gaps_and_subtle_rounded_corners():
    text = source()
    chart = text.split("  _renderPriceChart() {", 1)[1].split(
        "  _priceData()", 1
    )[0]
    assert "const barGap = Math.min(2.2" in chart
    assert "const visualBarWidth" in chart
    assert 'rx="${cornerRadius}"' in chart
    assert 'width="${visualBarWidth}"' in chart
    assert "profitable-discharge" not in chart
    assert "profitableDischarge" not in chart


def test_runtime_events_have_filters_counts_and_clickable_details():
    text = source()
    assert 'runtimeEvents: "sensor.openems_runtime_gebeurtenissen"' in text
    assert 'data-event-filter="error"' in text
    assert 'data-event-filter="warning"' in text
    assert 'data-event-filter="recovered"' in text
    assert 'id="event-counts"' in text
    assert "_showRuntimeEvent(event)" in text
    assert 'id="event-modal"' in text


def test_energy_flow_chart_uses_clear_source_and_destination_colours():
    text = source()
    assert '"Energiestromen' in text
    assert "Woning uit net" in text
    assert "Woning uit accu" in text
    assert "Accu laden" in text
    assert "Terugleveren" in text
    assert ".flow-grid-home-bar{fill:#ff9f43}" in text
    assert ".flow-battery-home-bar{fill:#46aaf2}" in text
    assert ".flow-battery-charge-bar{fill:#55d996}" in text
    assert ".flow-export-bar{fill:#b58aff}" in text


def test_energy_flow_chart_uses_fifteen_minute_stacked_buckets():
    text = source()
    method = text.split("  _renderPowerHistory(includeWeek = true) {", 1)[1].split(
        "  _flowChartScale(", 1
    )[0]
    assert "15 * 60" in method
    assert "60 * 60" in method
    assert "item.gridToHome + item.batteryToHome + item.batteryCharge" in method
    assert '["flow-grid-home-bar", item.gridToHome]' in method
    assert '["flow-battery-home-bar", item.batteryToHome]' in method
    assert '["flow-battery-charge-bar", item.batteryCharge]' in method
    assert 'class="flow-export-bar"' in method


def test_energy_flow_allocation_matches_the_selected_display_logic():
    text = source()
    method = text.split("  _energyFlow(homePower, gridPower, batteryPower) {", 1)[1].split(
        "  _averagePower", 1
    )[0]
    assert "const batteryToHome = Math.min(home, discharge)" in method
    assert "const gridToHome = Math.max(0, home - batteryToHome)" in method
    assert "const batteryCharge = Math.max(0, -" in method
    assert "const exportPower = Math.max(0, -" in method


def test_status_explains_preferred_charge_power_range_and_fallback():
    text = source()
    assert "Voorkeursbereik" in text
    assert "Vermogenskeuze" in text
    assert "Efficiënt verdeeld" in text
    assert "Hoger vermogen noodzakelijk" in text
    assert "voorkeur_minimum_per_accu_w" in text
    assert "voorkeur_maximum_per_accu_w" in text


def test_settings_monitor_fast_grid_sensor_and_p1_offset_together():
    text = source()
    assert 'id="grid-reference-input"' in text
    assert 'id="grid-offset-mode"' not in text
    assert 'id="grid-offset-manual-input"' in text
    assert 'id="grid-offset-calibrate"' in text
    assert 'id="grid-offset-calibrate-settings"' in text
    assert 'id="grid-offset-last"' in text
    assert "<b>Meting en offsets</b>" in text
    assert 'id="grid-offset-fast"' in text
    assert 'id="grid-offset-reference"' in text
    assert 'id="grid-offset-difference"' in text
    assert 'id="grid-offset-automatic"' in text
    assert 'id="grid-offset-active"' in text
    assert 'id="grid-offset-corrected"' in text
    assert "Automatische offset is betrouwbaar berekend" in text
    assert "Gecorrigeerd netvermogen" in text


def test_display_grid_sensor_is_separate_from_fast_control_sensor():
    text = source()
    assert 'gridDisplaySensorHelper: "input_text.openems_grid_verbruiksensor"' in text
    assert 'id="grid-display-sensor-input"' in text
    assert "Sensor gridverbruik" in text
    assert '"sensor.p1_netto_vermogen"' in text
    assert "entities.fastGrid" in text
    overview = text.split(
        'this.$("grid-power").textContent', 1
    )[1].split("this._patchPowerSensorSettings", 1)[0]
    assert "powerEntities.grid" in overview
    assert "correctedGridPower" not in overview


def test_flow_chart_uses_separate_positive_and_export_scales():
    text = source()
    method = text.split("  _renderPowerHistory(includeWeek = true) {", 1)[1].split(
        "  _energyFlow", 1
    )[0]
    scale = text.split("  _flowChartScale(", 1)[1].split(
        "  _energyFlow", 1
    )[0]
    assert "const positiveMax" in method
    assert "const exportMax" in method
    assert "scale.exportMax" in method
    assert "positiveTicks" in method
    assert "negativeTicks" in method
    assert "flow-axis-label" in method
    assert "flow-zero" in method
    assert "exportMax: 0" in scale
    assert "zero: height - padding" in scale
    assert "negativeShare" in scale
def test_mobile_overview_is_vertical_scrollable_and_ordered():
    text = source()

    assert "@media(max-width:600px)" in text
    assert "flex-direction:column;height:100%;overflow-x:hidden;overflow-y:auto" in text
    assert ".overview-page .hero{order:1" in text
    assert ".overview-page .battery-strip{order:2" in text
    assert ".overview-page .charge-plan{order:3" in text
    assert ".overview-page .discharge-plan{order:4" in text
    assert ".overview-page .control-card{order:5" in text
    assert "mobile-control-toggle" in text


def test_financial_results_and_discharge_controls_are_visible():
    text = source()

    assert 'data-page="results"' in text
    assert "sensor.openems_financiele_resultaten" in text
    assert "Marge onder hoogste dagprijs" in text
    assert "Reserve voor laden" in text
    assert "Reserve na laden" in text
    assert "Dynamische reserve" in text
    assert "Geschatte slijtagekosten" not in text


def test_results_show_clickable_efficiency_power_profiles():
    text = source()

    assert "Rendement per vermogensgebied" in text
    assert 'data-efficiency-direction="${item}"' in text
    assert 'data-efficiency-power="${power}"' in text
    assert "vermogensgebieden" in text
    assert "Betrouwbaarheid" in text
    assert "Gemeten energie" in text
    assert "Laatste meting" in text
    assert "Tijdresolutie OpenEMS" in text


def test_grid_export_mode_status_is_visible_without_legacy_results():
    text = source()

    assert 'exportMode: "input_select.openems_grid_export_modus"' in text
    assert 'id="export-mode-select"' in text
    assert "Alleen simuleren" in text
    assert "Automatisch exporteren" in text
    assert 'id="export-overview"' not in text
    assert 'id="export-results"' not in text
    assert "Geschatte extra winst door Grid Export" not in text
    assert "Netto energiekosten na verrekening" not in text


def test_dynamic_export_reserve_control_is_visible_and_wired():
    text = source()

    assert 'beforeChargeReserveMode: "input_select.openems_export_reserve_voor_laden_modus"' in text
    assert 'beforeChargeReserve: "input_number.openems_export_reserve_voor_laden_kwh"' in text
    assert 'dynamicReserve: "input_number.openems_export_dynamische_reserve_kwh"' in text
    assert 'id="before-charge-reserve-mode"' in text
    assert 'id="export-reserve-mode"' in text


def test_light_export_colour_requires_actionable_automatic_plan():
    text = source()
    planned = text.split(
        "  _exportPlannedAt(time, stepMs = 3600000) {", 1
    )[1].split("  _historicalExportIntervals", 1)[0]
    assert 'mode === "Automatisch exporteren"' in planned
    assert "simulatie" not in planned
    assert 'this._intervals(E.nextExport, "blokken")' in planned
    assert 'id="dynamic-export-reserve-toggle"' not in text
    assert "Werking dynamische reserve" in text
    assert "donkergroene mogelijke laadblok" in text
    assert "this._patchDynamicExportReserve(attrs);" in text
    assert 'E.beforeChargeReserveMode' in text
    assert 'E.exportReserveMode' in text


def test_export_uses_bare_market_price_plus_configured_frank_fee():
    text = source()

    assert 'frankMarket: "sensor.frank_energie_stroomprijzen_huidige_elektriciteit_inkoopprijs"' in text
    assert "input_number.openems_frank_terugleververgoeding" in text
    assert "Frank terugleververgoeding" in text
    assert "exportPriceHelper" not in text
    assert "export-price-sensor-input" not in text


def test_stale_automatic_battery_status_is_shown_as_error():
    text = source()
    assert 'normalized.includes("te oud")' in text
    assert 'normalized.includes("geblokkeerd")' in text


def test_battery_badge_uses_measured_power_instead_of_configured_mode():
    text = source()

    assert "const actual = this._obj(b.actual)" in text
    assert 'if (power < -25) return "Laden"' in text
    assert 'if (power > 25) return "Ontladen"' in text
    assert 'return "Stand-by"' in text


def test_frontend_uses_separate_state_and_formatter_modules():
    text = source()
    frontend = FRONTEND.parent
    assert './modules/formatters.js' in text
    assert './modules/ha-state-adapter.js' in text
    assert (frontend / "modules" / "formatters.js").exists()
    assert (frontend / "modules" / "ha-state-adapter.js").exists()


def test_balance_manager_selector_is_not_in_quick_settings():
    text = source()
    assert 'id="balance-mode-select"' not in text
    assert 'id="balance-setting-status"' not in text
    # De Marstek-BMS-balancering blijft per accu zichtbaar.
    assert "Balanceermodus" in text
    assert "activeBalance" not in text
    assert "active_balance_mode" not in text


def test_openems_has_no_cell_balancing_of_its_own():
    # Celbalancering is de taak van de BMS in de accu's zelf.
    text = source()
    assert 'id="balance-status-content"' not in text
    assert "Celbalancering" not in text
    assert "_renderBalanceStatus" not in text
    assert "openems_cell_balance" not in text
    apps = Path(__file__).resolve().parents[2] / "addon_configs" / "a0d7b954_appdaemon" / "apps" / "OpenEMS-M"
    assert not (apps / "balance_manager.py").exists()
    for path in apps.glob("*.py"):
        code = path.read_text(encoding="utf-8")
        assert "balance_manager" not in code, path.name
        assert "openems_cell_balance" not in code, path.name


def test_global_resolution_is_in_energy_supplier_settings():
    text = source()
    quick = text.index("Snelle schakelfuncties")
    resolution = text.index("Tijdresolutie OpenEMS")
    supplier = text.index("Energieleverancier")
    assert quick < supplier < resolution
    assert "Tijdresolutie ontlaadplanner" not in text
    assert "<option>Automatisch</option>" not in text[
        resolution:resolution + 250
    ]


def test_overview_battery_rows_show_compact_actual_state():
    text = source()
    assert "battery?.huidige_status" in text
    assert "selected ? \"Zelfconsumptie\" : \"Rust\"" in text
    assert "Math.abs(actualPower) >= 1" in text
    assert "battery?.volgende_actie" not in text
    assert "selectorNoChange" in text


def test_settings_are_grouped_by_domain():
    text = source()
    for title in (
        "Snelle schakelfuncties", "<b>Laden</b>", "<b>Ontladen</b>",
        "Zelfconsumptie", "Terugleveren", "Energieleverancier",
        "Geavanceerd menu",
    ):
        assert title in text
    assert '_settingsCards("laden")' in text
    assert '_settingsCards("ontladen")' in text
    assert '_settingsCards("leverancier")' in text
    assert 'class="advanced-only"' in text


def test_settings_sections_are_collapsible_again():
    text = source()
    assert text.count('<details class="settings-section') >= 7
    assert text.count('summary class="settings-section-title"') >= 7
    assert ".settings-section[open]>.settings-section-title:after" in text
    assert ".settings-section-content{display:grid" in text


def test_export_financial_sliders_are_under_energy_supplier():
    text = source()
    settings = text.split("settings: [", 1)[1].split(
        "batteryEntityFields:", 1
    )[0]
    assert (
        '"input_number.openems_minimaal_prijsverschil_grid_export", '
        '"Terugleveren minimaal laadprijsverschil", "EUR/kWh", '
        '"leverancier"'
    ) in settings
    assert (
        '"input_number.openems_minimale_exportwinst_per_kwh", '
        '"Marge onder hoogste dagprijs", "EUR/kWh", "leverancier"'
    ) in settings
    assert '"number-entry"' not in settings
    assert '"Minimale winstmarge", "EUR/kWh", "export"' not in settings


def test_dark_purple_export_candidate_overrides_future_blue_plan():
    classifier = classifier_source()
    assert classifier.index("signals.exportCandidate") < classifier.index(
        "signals.chargePlanned"
    )
    assert classifier.index("signals.exportCandidate") < classifier.index(
        "signals.selfPlanned"
    )


def test_settings_start_with_large_automatic_manual_switch():
    text = source()
    assert 'data-auto-mode="on">Automatisch' in text
    assert 'data-auto-mode="off">Handmatig' in text
    assert 'data-settings-view="standard"' not in text
    assert 'data-settings-view="advanced"' not in text
    assert 'id="settings-auto-toggle"' not in text
    assert 'id="advanced-settings-toggle"' in text
    assert "openems-settings-advanced" in text
    assert ".advanced-only[hidden]{display:none!important}" in text
    assert '<details class="settings-section quick-settings-section">' in text


def test_new_standard_switches_and_conditional_fields_are_wired():
    text = source()
    for control in (
        'id="settings-mode-switch"', 'id="grid-charge-toggle"',
        'id="grid-export-toggle"', 'id="grid-source-select"',
        'id="export-reserve-mode"',
    ):
        assert control in text
    assert 'id="grid-offset-status-card" hidden' in text
    assert "openems_frank_terugleververgoeding" not in text[
        text.index("settings: ["):text.index("batteryEntityFields:")
    ]


def test_status_page_has_requested_order_and_three_column_top_row():
    text = source()
    status = text.split('<div class="status-layout">', 1)[1].split(
        "</div>\n            <div id=\"event-modal\"", 1
    )[0]
    ordered = [
        "Actuele controle", "Ingedeelde urenblokken",
        "Gebeurtenissen · 30 dagen", "Efficiënte accuselector",
        "Zelfgeleerd rendement per accu", "Laadadvies",
        "Zelfconsumptieadvies", "Terugleveradvies",
        "Actuele energieplanning",
    ]
    positions = [status.index(label) for label in ordered]
    assert positions == sorted(positions)
    assert "grid-template-columns:repeat(3,minmax(0,1fr))" in text
    assert 'id="assigned-hour-blocks"' in text
    assert "_renderAssignedHourBlocks()" in text
    assert "_renderAdviceSummaries()" in text
    assert "groupAssignedHourSlots(slots)" in text
    assert "Voorspelling per accu" not in text


def test_light_purple_export_plan_is_the_primary_planner_truth():
    text = source()
    method = text.split(
        "  _plannedPriceModeAt(time, stepMs = 3600000) {", 1
    )[1].split("  _candidatePriceModeAt", 1)[0]
    assert "this._exportPlannedAt(time, stepMs)" in method
    assert "this._exportCandidateAt(time, stepMs)" not in method
    classifier = classifier_source()
    assert classifier.index("signals.exportPlanned") < classifier.index(
        "signals.exportCandidate"
    )


def test_transparent_efficiency_modes_are_available_on_all_planners():
    text = source()
    assert 'chargeEfficiencyMode: "input_select.openems_laad_rendement_modus"' in text
    assert 'dischargeEfficiencyMode: "input_select.openems_ontlaad_rendement_modus"' in text
    assert 'exportEfficiencyMode: "input_select.openems_export_rendement_modus"' in text
    assert 'id="charge-efficiency-mode"' in text
    assert 'id="discharge-efficiency-mode"' in text
    assert 'id="export-efficiency-mode"' in text
    assert "Zelfgeleerd rendement" in text
    assert "Zelfgeleerd rendement per accu" in text
    assert "_renderEfficiencyStatus()" in text
    assert "round_trip_efficiency_total" not in text


def test_requested_graph_order_and_scales_are_present():
    text = source()
    graph = text.split('<div class="graph-grid">', 1)[1].split(
        "</div>\n          </section>", 1
    )[0]
    assert graph.index('id="price-chart"') < graph.index('id="power-chart-today"')
    assert graph.index('id="power-chart-today"') < graph.index('id="power-chart-yesterday"')
    assert graph.index('id="power-chart-yesterday"') < graph.index('id="power-chart-week"')
    assert graph.index('id="power-chart-week"') < graph.index('id="soc-chart-today"')
    assert graph.index('id="soc-chart-today"') < graph.index('id="soc-chart-yesterday"')
    assert graph.index('id="soc-chart-yesterday"') < graph.index('id="market-chart"')
    assert "const ticks = [0, 25, 50, 75, 100]" in text
    assert "soc-axis-label" in text


def test_decision_explanation_graph_is_removed():
    text = source()

    assert 'id="decision-chart"' not in text
    assert 'id="decision-detail"' not in text
    assert "Beslissingsuitleg · 48 uur" not in text
    assert "_renderDecisionChart(data)" not in text
    assert "_decisionForPoint(point)" not in text


def test_price_graph_uses_hover_and_tap_tooltip_for_selected_hour():
    text = source()

    assert 'id="price-tooltip"' in text
    assert 'bar.addEventListener("pointerenter"' in text
    assert 'bar.addEventListener("click"' in text
    assert "_showPriceTooltip(point, bar" in text
    assert "All-inprijs" in text
    assert "Terugleverprijs" in text
    assert 'id="price-detail" hidden' in text


def test_selector_status_is_compact_and_uses_soc_instead_of_power_as_soc():
    text = source()

    assert 'class="selector-summary"' in text
    assert 'class="selector-meta"' in text
    assert "item.soc == null" in text
    assert "Woningvraag" in text
    assert "Actieve accu’s" not in text


def test_active_selector_overrides_idle_system_heading():
    text = source()

    assert "const selectorActive = auto" in text
    assert 'selectorActive\n      ? "Zelfconsumptie"' in text


def test_price_graph_is_limited_to_today_and_tomorrow_from_midnight():
    text = source()
    method = text.split("  _priceData() {", 1)[1].split(
        "  _priceRows(", 1
    )[0]
    assert "todayStart.setHours(0, 0, 0, 0)" in method
    assert "dayAfterTomorrow.setDate(dayAfterTomorrow.getDate() + 2)" in method
    assert "row.time >= todayStart && row.time < dayAfterTomorrow" in method
    assert ".slice(0, limit)" in method
    assert ".slice(-limit)" not in method


def test_price_graph_uses_one_central_classification_for_colour_and_label():
    text = source()
    chart = text.split("  _renderPriceChart() {", 1)[1].split(
        "  _priceData()", 1
    )[0]
    assert "const classification = this._priceClassificationAt(" in chart
    assert 'class="${classification.className}' in chart
    assert "${classification.label}" in chart
    assert "Frank Energie · vandaag en morgen" in text
    assert "classifyPricePlanningSlot" in text


def test_actual_export_has_priority_and_historical_status_is_only_a_fallback():
    classifier = classifier_source()
    past = classifier.split('if (phase === "past")', 1)[1].split(
        'if (phase === "current")', 1
    )[0]
    assert past.index("signals.exportExecuted") < past.index(
        "signals.manualMode"
    )
    assert past.index("signals.manualMode") < past.index(
        'signals.measuredMode === "charge"'
    )
    assert "signals.measuredMode == null" in past


def test_missing_tomorrow_prices_are_explained_and_legends_are_truthful():
    text = source()
    assert "Morgenprijzen nog niet gepubliceerd" in text
    assert "Nog niet gepubliceerd" in text
    assert "Laden gepland / gemeten" in text
    assert "Zelfconsumptie gepland / gemeten" in text
    assert "Terugleveren gepland / gemeten" in text
    assert "Werkelijk laadplan" not in text


def test_overview_cards_dynamically_show_current_and_next_block_type():
    text = source()

    assert 'id="current-block-title"' in text
    assert 'id="next-block-title"' in text
    assert "_patchOverviewBlocks()" in text
    assert 'type: "laadblok"' in text
    assert 'type: "terugleverblok"' in text
    assert 'type: "zelfconsumptieblok"' in text
    assert 'block.gemiddelde_prijs ?? attrs.gemiddelde_prijs' in text
    assert '`${current ? "Huidig" : "Volgend"} ${block.type}`' in text


def test_all_selects_and_number_controls_use_pending_value_protection():
    text = source()
    assert "_wireControlStability()" in text
    assert "_openemsPendingValue" in text
    assert "_openemsPendingUntil" in text
    assert "_controlMaySync(element, authoritativeValue)" in text
    assert "_syncControlValue(input, this._state(entity, obj.state))" in text
    assert (
        '_setSelect(id, value) { this._syncControlValue(this.$(id), value); }'
        in text
    )


def test_entity_commands_wait_for_home_assistant_confirmation():
    text = source()
    assert "this._pendingEntityStates = new Map()" in text
    assert "_callEntityService(domain, service, entity" in text
    assert "until: Date.now() + 20000" in text
    assert "if (Date.now() < pending.until) return pending.value" in text
    assert '_toggleEntity("input_boolean", E.auto)' in text


def test_hidden_dashboard_pages_are_not_rerendered_on_every_update():
    text = source()
    assert 'if (this._tab === "overview")' in text
    assert 'else if (this._tab === "graphs")' in text
    assert 'else if (this._tab === "settings")' in text
    assert 'else if (this._tab === "status")' in text
    assert "this._schedulePatch();" in text


def test_price_history_colours_are_cached_during_background_refresh():
    text = source()
    assert "openems-price-classifications-v1" in text
    assert "_cachedPriceClassification(time, stepMs)" in text
    assert "_rememberPriceClassification(time, stepMs, classification)" in text
    assert "Bestaande inkleuring blijft behouden" in text


def test_native_select_options_have_explicit_dark_and_light_colours():
    text = source()
    assert "select option{background:#1d2327;color:#f4f7f6}" in text
    assert (
        ':host([data-theme="light"]) select option'
        "{background:#fff;color:#17201d}"
        in text
    )


def test_selector_explains_that_existing_control_mode_is_preserved():
    text = source()
    assert 'id="battery-distribution-status"' in text
    assert (
        "Marstek regelt het vermogen; OpenEMS kiest alleen de actieve accu’s."
        in text
    )
    assert (
        "OpenEMS regelt het vermogen; de selector kiest de actieve accu’s."
        in text
    )


def test_price_forecast_settings_and_chart_are_wired():
    text = source()
    assert 'priceForecast: "input_boolean.openems_prijsvoorspelling"' in text
    assert 'priceForecastStatus: "sensor.openems_prijsvoorspelling"' in text
    assert 'id="price-forecast-toggle"' in text
    assert 'id="price-forecast-status"' in text
    assert 'id="price-forecast-entity-input"' in text
    # De schakelaar staat in Instellingen → Laden, vóór de laadinstellingen.
    laden = text.index("<b>Laden</b><small>Doel en rendementsberekening")
    assert laden < text.index('id="price-forecast-toggle"') < text.index(
        "Rendement bij laden<select"
    )
    for helper in (
        "openems_prijsvoorspelling_min_verschil",
        "openems_prijsvoorspelling_ondergrens_soc",
        "openems_prijsvoorspelling_dagen",
    ):
        assert f"input_number.{helper}" in text
    # Frank blijft leidend en de samenvatting gebruikt alleen echte prijzen.
    assert "_forecastRows(stepMs, lastFrank, horizon)" in text
    assert "data.filter((p) => !p.forecast)" in text
    assert "forecast-bar" in text and "(voorspeld" in text


def test_price_forecast_margin_setting_is_in_panel():
    assert "input_number.openems_prijsvoorspelling_extra_marge" in source()


def test_calibration_buttons_give_immediate_feedback_and_home_ct_calibration():
    text = source()
    assert 'homeOffsetCalibrate: "input_button.openems_home_offset_kalibreren"' in text
    assert 'id="home-offset-calibrate"' in text and 'id="home-offset-last"' in text
    assert "_pressCalibration(" in text and "button.disabled = running" in text
    assert "Kalibratie gestart… dit duurt ongeveer 1 minuut" in text
    assert "accu_actief" in text and "Kalibratie mislukt" in text


def test_forecast_bars_use_indicative_plan_colours():
    text = source()
    assert 'priceForecastPlan: "sensor.openems_voorspeld_plan"' in text
    assert '" forecast-charge"' in text and '" forecast-self"' in text
    assert ".forecast-bar.forecast-charge{fill:var(--accent)" in text
    assert "mode," in text and "forecast: true, mode, stepMs" in text
    assert "verwacht" in text and "indicatief plan" in text


def test_assigned_hour_blocks_include_marked_forecast_hours():
    text = source()
    renderer = text.split("_renderAssignedHourBlocks() {", 1)[1].split(
        "\n  _forecastPlanModes() {", 1
    )[0]
    # Voorspelde uren alleen ná de laatste Frank-prijs en nooit over een echt plan.
    assert "this._forecastPlanModes()" in renderer
    assert "planned || start.getTime() < lastFrankEnd" in renderer
    assert "const power = forecast ? 0 : plannedPower(mode, start, end);" in renderer
    assert "Math.min(\n      72," in renderer
    assert '${forecast ? " forecast" : ""}' in renderer
    assert 'class="assigned-hour-forecast">voorspeld</i>' in renderer
    helper = text.split("_forecastPlanModes() {", 1)[1].split("\n  }\n", 1)[0]
    assert 'status?.state !== "actief"' in helper
    assert 'plan?.state !== "aan"' in helper
    assert 'laden: "charge", zelf: "self", standby: "standby"' in helper
    assert ".assigned-hour.forecast{" in text
    assert "assigned-hour-blocks.mjs?v=20261108a" in text


def test_panel_batteries_follow_openems_site_config():
    text = source()
    # Standaard de oorspronkelijke vier accu's, anders uit sensor.openems_configuratie.
    assert "const DEFAULT_BATTERY_SITE = [1, 2, 3, 4]" in text
    assert "berging_accu${n}" in text
    assert '"sensor.openems_configuratie"' in text
    assert "_applySiteConfig()" in text
    assert "E.batteries.splice(0, E.batteries.length" in text
    assert '${E.batteries.map((b) => `<span class="soc-${b.n}">${b.name}</span>`).join("")}' in text
    assert 'id="accu-active-toggle-${b.n}"' in text
    assert "Accu 4 actief in OpenEMS" not in text
