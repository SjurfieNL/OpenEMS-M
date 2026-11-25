# OpenEMS-M

Energiemanagement voor **Marstek Venus**-thuisaccu's in Home Assistant, met een
dynamisch energiecontract. OpenEMS-M plant laden en ontladen op de goedkoopste
en duurste uren, bewaakt de grenzen van je netaansluiting (load balancer) en
stuurt de accu's rechtstreeks aan via Modbus TCP.

**Versie 1.0.0-beta.1** — eerste installeerbare versie. Gebruik met beleid en
kijk de eerste dagen mee.

## Wat je nodig hebt

- Home Assistant **2026.11** of nieuwer, met [HACS](https://hacs.xyz).
- 1 tot 4 Marstek Venus-accu's (E v1/v2, E v3, D of A) met **Modbus TCP**
  bereikbaar op je netwerk (vast IP-adres, poort 502).
- Een **prijssensor met de all-in stroomprijs** per kWh (inclusief opslag en
  energiebelasting) met de prijzen in een `prices`-attribuut in het formaat van
  de Frank Energie-integratie: een lijst van `{from, till, price}`. De
  all-in-sensor van de Frank Energie-integratie werkt direct.
  **Nord Pool / ENTSO-e** werkt in deze beta alleen via een eigen
  (template-)sensor die de prijzen in dat formaat aanbiedt; rechtstreekse
  ondersteuning volgt.
- Een **netvermogenssensor** (P1-meter en/of CT-meter), afname positief,
  teruglevering negatief. Hoe vaker hij bijwerkt, hoe sneller de load balancer
  kan ingrijpen (aanbevolen: minstens elke 10 s).

## Installatie

1. HACS → menu (⋮) → **Aangepaste repositories** → deze repository toevoegen,
   type **Integratie**.
2. Zoek **OpenEMS-M** in HACS, installeer en **herstart Home Assistant**.
3. Instellingen → Apparaten en diensten → **Integratie toevoegen** →
   **OpenEMS-M**. De wizard vraagt:
   1. **Energiecontract**: prijsbron, prijssensor (all-in) en terugleververgoeding.
   2. **Accu's**: per accu naam, IP-adres, poort, Unit-ID, model en
      accunummer (1–4, doorlopend). De wizard test de verbinding direct en toont
      SOC en firmware.
   3. **Aansluiting**: maximale import en export (W, totaal) en veiligheidsmarge.
   4. **Netsensoren**: P1 en/of CT, met voorkeur.
   5. **Start**: Automatisch aan of uit.
4. Het paneel **OpenEMS-M** verschijnt in de zijbalk.

## Gebruik

- **Hoofdschakelaar** (`switch.openems_actief`): uit = OpenEMS kijkt alleen
  mee en stuurt niets aan. Laat hem de eerste dag uit en controleer de planning
  in het paneel.
- **Accu toevoegen**: bij de integratie → **Accu toevoegen**. Daarna Home
  Assistant herstarten (je krijgt een melding).
- **Accu tijdelijk uitzetten**: in het paneel bij de instellingen
  ("Accu N actief in OpenEMS"). OpenEMS laat die accu dan volledig met rust.
- **Instellingen** staan als entiteiten onder het apparaat *OpenEMS-M*
  (`switch.`, `number.`, `select.`, `text.`, `button.`) en zijn ook in het
  paneel te wijzigen. De sensor voor het woningverbruik stel je in het paneel in.
- **Data** (geleerd rendement, verbruiksprofiel, resultaten) en het
  automatisch geschreven `openems_site.yaml` staan in `/config/openems_m/`.
  Die map blijft bij updates bewaard.

## Belangrijk

- Gebruik OpenEMS-M **niet tegelijk** met de losse integratie `marstek_modbus`
  op dezelfde accu's, en niet naast de AppDaemon-versie van OpenEMS-M. Twee
  Modbus-verbindingen of twee aansturingen naar dezelfde accu storen elkaar.
- Een accu die (tijdelijk) niet bereikbaar is, houdt de rest niet tegen; zijn
  entiteiten blijven onbeschikbaar tot hij weer antwoordt.
- OpenEMS drukt nooit zelf de reset-knop van een accu in.

Overstappen van de AppDaemon-versie: zie [MIGRATIE.md](MIGRATIE.md).

## Ontwikkeling

- `custom_components/openems_m/ems/` (planning en aansturing),
  `driver/` (Marstek Modbus-driver), `frontend/` (paneel) en `helpers.json`
  worden **gegenereerd** uit de ontwikkelmap. Niet handmatig wijzigen; na een
  wijziging opnieuw genereren vanuit de ontwikkelmap:
  ```bash
  python integratie/tools/sync_ems.py .
  ```
- `bridge/hassapi.py` is de tussenlaag die de OpenEMS-code binnen Home
  Assistant laat draaien (zelfde functies als AppDaemon; alle callbacks achter
  elkaar in één werk-thread).
- Tests:
  ```bash
  python -m pytest -q tests
  ```

## Licentie

Zie `LICENSE`. De Marstek-driver is gebaseerd op
[marstek_venus_modbus](https://github.com/ViperRNMC/marstek_venus_modbus).
