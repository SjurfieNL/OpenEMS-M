"""Centrale, overzichtelijke logweergave voor OpenEMS-M."""


class EMSLogger:
    SEPARATOR = "------------------------------------------------------------"

    def __init__(self, app, price_manager, price_log_hours=24):
        self.app = app
        self.price_manager = price_manager
        self.price_log_hours = price_log_hours

    def warning(self, message):
        self.app.log(message, level="WARNING")

    def log_plan(self, context, scheduler_execution=None, scheduler_status=None):
        battery = context.battery
        charge_plan = context.charge_plan
        discharge_plan = context.discharge_plan

        self._section("MARSTEK EMS STATUS")
        self.app.log(f"Tijd: {context.now.strftime('%d-%m-%Y %H:%M:%S')}")
        self.app.log("")
        self.app.log(f"Gemiddelde SOC: {battery.average_soc:.1f}%")
        for name, soc in zip(battery.names, battery.soc_values):
            self.app.log(f"SOC {name.lower()}: {soc:.1f}%")
        self.app.log("")
        self.app.log(f"Capaciteit: {battery.capacity:.2f} kWh")
        self.app.log(f"Opslag aanwezig: {battery.stored_energy:.2f} kWh")
        self.app.log(f"Doel SOC: {context.target_soc:.1f}%")
        self.app.log(f"Minimum SOC: {context.minimum_soc:.1f}%")
        self.app.log("")
        self.app.log(
            f"Nog in accu nodig: {context.battery_energy_needed:.2f} kWh"
        )
        self.app.log(
            f"Benodigde netenergie bij "
            f"{context.charge_efficiency * 100:.0f}% rendement: "
            f"{context.needed_energy:.2f} kWh"
        )
        self.app.log(f"Max laadvermogen: {battery.max_charge:.0f} W")
        self.app.log(f"Max ontlaadvermogen: {battery.max_discharge:.0f} W")

        self._section("ENERGY SCHEDULER")
        status = scheduler_status or {}
        self.app.log(f"Gewenste state: {context.scheduler_state.value}")
        self.app.log(f"Actuele state: {status.get('current_state', 'onbekend')}")
        self.app.log(f"Vorige state: {status.get('previous_state', 'onbekend')}")
        last_transition = status.get("last_transition")
        self.app.log(
            "Laatste overgang: "
            + (last_transition.strftime('%d-%m-%Y %H:%M:%S') if last_transition else "nog geen")
        )
        self.app.log(f"Reden: {context.scheduler_reason}")
        self.app.log(f"Controlleractie: {status.get('last_controller_action', 'onbekend')}")
        self.app.log(f"Verificatie: {status.get('last_verification', 'onbekend')}")
        self.app.log(f"Uitvoering: {scheduler_execution or 'Onbekend'}")

        if not charge_plan:
            self._section("MARSTEK LAADADVIES")
            self.app.log("Geen laadplan nodig of mogelijk.")
            self.app.log(
                f"Minimale laadtijd: {context.hours_needed:.2f} uur"
            )
            self._end()
            return

        self._section("MARSTEK LAADADVIES")
        self.app.log(
            f"Laadprijsmarge: {context.price_margin:.4f} EUR/kWh"
        )
        self.app.log(
            f"Goedkoopste prijs: {charge_plan.cheapest_price:.4f} EUR/kWh"
        )
        self.app.log(
            f"Toegestane prijsgrens: {charge_plan.price_limit:.4f} EUR/kWh"
        )
        self.app.log(
            f"Gemiddelde laadprijs: {charge_plan.average_price:.4f} EUR/kWh"
        )
        self.app.log(
            f"Ontlaadmarge: {context.discharge_margin:.4f} EUR/kWh"
        )
        if discharge_plan:
            self.app.log(
                f"Ontladen vanaf: {discharge_plan.price_limit:.4f} EUR/kWh"
            )
        self.app.log(
            "Laadblok actief: "
            + (
                "ja"
                if any(
                    segment.start <= context.now < segment.end
                    for plan in context.battery_charge_plans
                    for segment in plan.segments
                )
                else "nee"
            )
        )
        self.app.log("")
        self.app.log("Geselecteerd laadblok:")
        for item in charge_plan.slots:
            start = self.price_manager.local_datetime(item["from"])
            end = self.price_manager.local_datetime(item["till"])
            self.app.log(
                f"{start.strftime('%d-%m %H:%M')} - "
                f"{end.strftime('%H:%M')}  "
                f"{float(item['price']):.4f} EUR/kWh"
            )

        self._section("MARSTEK LAADPLANNER")
        self.app.log(
            f"Laadvermogen totaal: {sum(context.charge_distribution):.0f} W"
        )
        for name, power in zip(battery.names, context.charge_distribution):
            self.app.log(f"{name} laadvermogen: {power:.0f} W")
        self.app.log(f"Start: {charge_plan.start.strftime('%d-%m %H:%M')}")
        self.app.log(f"Einde: {charge_plan.end.strftime('%d-%m %H:%M')}")
        self.app.log(
            f"Minimale laadtijd bij maximaal vermogen: "
            f"{context.hours_needed:.2f} uur"
        )
        self.app.log(f"Gepland laadvenster: {charge_plan.hours:.2f} uur")
        self.app.log(
            "Volledig economisch prijsblok: "
            f"{len(getattr(charge_plan, 'eligible_slots', []))} uur"
        )
        self.app.log("")
        self.app.log("Individuele accuplanning:")
        for plan in context.battery_charge_plans:
            strategy = {
                "voorkeursbereik": "binnen efficiënt voorkeursbereik",
                "noodzakelijk_hoger": (
                    "hoger vermogen nodig binnen beschikbaar laadblok"
                ),
                "kleine_energievraag": (
                    "korte lading onder voorkeursbereik"
                ),
                "geen_lading_nodig": "geen lading nodig",
            }.get(plan.power_strategy, plan.power_strategy)
            self.app.log(
                f"{plan.name}: SOC {plan.current_soc:.1f}% -> "
                f"{plan.target_soc:.1f}% | {plan.grid_energy_needed:.3f} kWh "
                f"uit net | rendement {plan.charge_efficiency * 100:.1f}% | "
                f"verlies "
                f"{plan.grid_energy_needed - plan.battery_energy_needed:.3f} "
                f"kWh | {plan.planned_power:.0f} W | "
                f"{plan.duration_hours * 60:.0f} minuten | "
                f"{plan.average_price:.4f} EUR/kWh | {strategy} "
                f"({plan.preferred_min_power:.0f}-"
                f"{plan.preferred_max_power:.0f} W)"
            )
            for segment in plan.segments:
                self.app.log(
                    f"  {segment.start.strftime('%d-%m %H:%M')} - "
                    f"{segment.end.strftime('%H:%M')} | "
                    f"{segment.power:.0f} W | "
                    f"{segment.price:.4f} EUR/kWh"
                )

        self._section("MARSTEK ONTLAADPLANNER")
        if not discharge_plan:
            self.app.log("Geen ontlaadplan beschikbaar.")
        else:
            self.app.log(
                f"Gemiddelde laadprijs: {charge_plan.average_price:.4f} EUR/kWh"
            )
            self.app.log(
                f"Ontlaadmarge: {context.discharge_margin:.4f} EUR/kWh"
            )
            self.app.log(f"Minimum SOC: {context.minimum_soc:.1f}%")
            self.app.log(
                f"Beschikbare energie boven minimum SOC: "
                f"{discharge_plan.available_energy:.2f} kWh"
            )
            self.app.log(
                f"Ontladen vanaf: {discharge_plan.price_limit:.4f} EUR/kWh"
            )
            self.app.log(
                f"Aantal gevonden ontlaadblokken: {len(discharge_plan.blocks)}"
            )

        self._log_prices(context)
        self._log_discharge_blocks(context)
        self._log_export_plan(context)
        self._end()

    def _log_prices(self, context):
        charge_starts = {
            item["from"] for item in context.charge_plan.slots
        }
        discharge_starts = set()
        if context.discharge_plan:
            discharge_starts = {
                item["from"]
                for block in context.discharge_plan.blocks
                for item in block.slots
            }

        self._section("TOEKOMSTIGE PRIJZEN")
        self.app.log("Datum  Tijd   Prijs       Laden  Ontladen")
        for item in context.future_prices[:self.price_log_hours]:
            start = self.price_manager.local_datetime(item["from"])
            charge = "JA" if item["from"] in charge_starts else "nee"
            discharge = "JA" if item["from"] in discharge_starts else "nee"
            self.app.log(
                f"{start.strftime('%d-%m')}  {start.strftime('%H:%M')}  "
                f"{float(item['price']):.4f}      {charge:<5}  {discharge}"
            )

    def _log_discharge_blocks(self, context):
        blocks = context.discharge_plan.blocks if context.discharge_plan else []
        self._section("GEVONDEN ONTLAADBLOKKEN")
        if not blocks:
            self.app.log("Geen winstgevende ontlaadblokken gevonden.")
            return

        for number, block in enumerate(blocks, start=1):
            difference = block.average_price - context.charge_plan.average_price
            self.app.log(f"Ontlaadblok {number}")
            self.app.log(f"Start: {block.start.strftime('%d-%m %H:%M')}")
            self.app.log(f"Einde: {block.end.strftime('%d-%m %H:%M')}")
            self.app.log(f"Aantal uren: {block.hours}")
            self.app.log(
                f"Gemiddelde prijs: {block.average_price:.4f} EUR/kWh"
            )
            self.app.log(
                f"Hoogste prijs: {block.highest_price:.4f} EUR/kWh"
            )
            self.app.log(
                f"Prijsverschil t.o.v. laden: {difference:.4f} EUR/kWh"
            )
            if number < len(blocks):
                self.app.log("")

    def _log_export_plan(self, context):
        export_plan = getattr(context, "export_plan", None)
        self._section("MARSTEK EXPORTPLANNER")
        if not export_plan:
            self.app.log("Geen exportplan beschikbaar.")
            return

        self.app.log(
            f"Kostprijsbasis: {export_plan.cost_basis_per_kwh:.4f} EUR/kWh "
            f"({export_plan.cost_basis_source})"
            if export_plan.cost_basis_per_kwh is not None
            else f"Kostprijsbasis: onbekend ({export_plan.cost_basis_source})"
        )
        self.app.log(
            f"Minimaal prijsverschil: {export_plan.minimum_price_difference:.4f} EUR/kWh"
        )
        self.app.log(
            f"Selectiemarge t.o.v. dagpiek: {export_plan.selection_margin:.4f} EUR/kWh"
        )
        self.app.log(
            f"Prijslimiet (kostprijsbasis + minimaal verschil): "
            f"{export_plan.price_limit:.4f} EUR/kWh"
        )
        self.app.log(f"Reserve SOC: {export_plan.reserve_soc:.1f}%")
        self.app.log(
            f"Beschikbare energie boven reserve: "
            f"{export_plan.available_energy:.2f} kWh"
        )
        for day, peak in sorted(export_plan.daily_peak_prices.items()):
            limit = export_plan.daily_peak_limits.get(day)
            self.app.log(
                f"Dagpiek {day}: {peak:.4f} EUR/kWh"
                + (
                    f" | ondergrens venster: {limit:.4f} EUR/kWh"
                    if limit is not None else ""
                )
            )

        self.app.log("")
        self.app.log(
            f"In aanmerking komende (donkerpaarse) uren: "
            f"{len(export_plan.eligible_slots)}"
        )
        for item in export_plan.eligible_slots:
            start = self.price_manager.local_datetime(item["from"])
            self.app.log(
                f"  {start.strftime('%d-%m %H:%M')} | "
                f"{float(item['price']):.4f} EUR/kWh"
            )

        self.app.log("")
        self.app.log(
            f"Geselecteerde (lichtpaarse) terugleverblokken: "
            f"{len(export_plan.blocks)}"
        )
        for number, block in enumerate(export_plan.blocks, start=1):
            self.app.log(f"Terugleverblok {number}")
            self.app.log(f"Start: {block.start.strftime('%d-%m %H:%M')}")
            self.app.log(f"Einde: {block.end.strftime('%H:%M')}")
            self.app.log(f"Aantal uren: {block.hours:.2f}")
            self.app.log(
                f"Gemiddelde terugleverprijs: {block.average_price:.4f} EUR/kWh"
            )
            self.app.log(
                f"Hoogste terugleverprijs: {block.highest_price:.4f} EUR/kWh"
            )
            self.app.log(f"Verwachte energie: {block.expected_energy:.3f} kWh")
            self.app.log(
                f"Gepland totaalvermogen: {block.planned_total_power:.0f} W"
            )
            if block.cost_basis_per_kwh is not None:
                self.app.log(
                    f"Kostprijsbasis blok: {block.cost_basis_per_kwh:.4f} EUR/kWh"
                )
            if block.reserve_soc is not None:
                self.app.log(f"Reserve SOC blok: {block.reserve_soc:.1f}%")
            if number < len(export_plan.blocks):
                self.app.log("")

    def _section(self, title):
        self.app.log(self.SEPARATOR)
        self.app.log(title)
        self.app.log(self.SEPARATOR)

    def _end(self):
        self._section("EINDE PLANBEREKENING")
