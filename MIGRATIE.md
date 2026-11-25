# Overstappen van de AppDaemon-versie naar de integratie

Deze handleiding is voor een bestaande installatie met OpenEMS-M in AppDaemon,
de losse integraties `marstek_modbus` en `openems_m_panel`, en het
helper-package `openems_helper.yaml`. Reken op ongeveer een half uur. Doe het
op een rustig moment, niet tijdens een gepland laad- of ontlaadblok.

Tijdens de overstap stuurt OpenEMS de accu's een paar minuten niet aan.

## Voorbereiding

1. **Back-up**: Instellingen → Systeem → Back-ups → **Back-up maken**.
2. **Noteer per accu** de naam van het apparaat (bijv. *Berging Accu1*),
   het IP-adres, de Unit-ID, het model en het OpenEMS-accunummer.
   Gebruik straks **exact dezelfde namen**: dan houden alle accu-entiteiten
   dezelfde naam (`berging_accu1_...`) en blijven je dashboards en
   automatiseringen werken.

## Overstappen

3. **AppDaemon OpenEMS-M stoppen**: zet in `apps.yaml` de app OpenEMS-M uit
   (of stop de AppDaemon-add-on als die verder niets doet).
4. **Data kopiëren**: kopieer de map `OpenEMS-M-data` uit
   `addon_configs/a0d7b954_appdaemon/apps/` naar `/config/openems_m/`
   (dus `/config/openems_m/OpenEMS-M-data/`). Zo behoud je geleerd rendement,
   verbruiksprofiel en financiële resultaten.
5. **Accu's loskoppelen**: Instellingen → Apparaten en diensten →
   **Marstek Venus Modbus** → bij elke accu **Verwijderen**.
   Verwijder daarna ook de integratie **OpenEMS-M Paneel**.
   *Laat `openems_helper.yaml` nog staan*: OpenEMS-M neemt bij de eerste start
   je huidige instellingen over uit die helpers.
6. **OpenEMS-M installeren** via HACS (zie README) en **Home Assistant
   herstarten**.
7. **Wizard doorlopen** met dezelfde namen, IP-adressen en accunummers als in
   stap 2. Kies bij Start: Automatisch **uit**.
8. **Controleren**:
   - de accu-entiteiten heten weer `berging_accu1_...` enz.;
   - het paneel OpenEMS-M toont de accu's, de planning en je instellingen;
   - in het logboek (Instellingen → Systeem → Logboek) staan geen fouten van
     `openems_m`.

## Afronden (na een dag meekijken)

9. Zet de **OpenEMS hoofdschakelaar** (`switch.openems_actief`) aan.
10. **Opruimen**, als alles goed werkt:
    - verwijder `custom_components/marstek_modbus` en
      `custom_components/openems_m_panel`;
    - verwijder `packages/openems_helper.yaml` en herstart. Je instellingen
      staan nu in de entiteiten van OpenEMS-M;
    - de app OpenEMS-M in AppDaemon mag weg.

Let op: in eigen dashboards of automatiseringen die OpenEMS-instellingen
gebruiken, verandert `input_boolean.openems_...` in `switch.openems_...`,
`input_number.` in `number.`, `input_select.` in `select.`, `input_text.` in
`text.` en `input_button.` in `button.` (zelfde naam erachter).

## Terug naar de oude situatie

Zet de back-up van stap 1 terug (Instellingen → Systeem → Back-ups). Daarmee
komen AppDaemon OpenEMS-M, `marstek_modbus`, het paneel en de helpers precies
terug zoals ze waren.
