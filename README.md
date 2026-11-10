# OpenEMS-M

Energiemanagement voor **Marstek Venus**-thuisaccu's in Home Assistant, op basis van
**dynamische energieprijzen**. OpenEMS-M plant laden, ontladen en terugleveren per
uur, bewaakt de netaansluiting (load balancer) en stuurt de accu's rechtstreeks
via Modbus TCP aan.

- 1 tot 4 Marstek Venus-accu's
- Planning op dynamische prijzen (getest met Frank Energie), met prijsvoorspelling
- Load balancer op de P1-meter (en optioneel CT-klemmen)
- Eigen zijbalkpaneel in Home Assistant: status, planning, grafieken, resultaten en instellingen
- Handmatige modus (laden/ontladen, gelijk of naar SOC verdeeld)

> **Let op:** OpenEMS-M stuurt je accu's echt aan. Gebruik op eigen risico,
> controleer de instellingen van je netaansluiting en houd de eerste dagen het
> log in de gaten. Er wordt geen enkele garantie gegeven (zie LICENSE).

---

## Wat zit er in deze repository

| Map | Wat | Waar in Home Assistant |
|---|---|---|
| `config/custom_components/marstek_modbus/` | Marstek Modbus-driver (aangepaste versie van *marstek_venus_modbus*) | `/config/custom_components/marstek_modbus/` |
| `config/custom_components/openems_m_panel/` | Zijbalkpaneel + Modbus-brug | `/config/custom_components/openems_m_panel/` |
| `config/packages/openems_helper.yaml` | Alle OpenEMS-helpers | `/config/packages/openems_helper.yaml` |
| `addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M/` | Het EMS zelf (AppDaemon-app) | AppDaemon-map `apps/OpenEMS-M/` |
| `addon_configs/a0d7b954_appdaemon/apps/openems_site.example.yaml` | Voorbeeld van jouw installatie-instellingen | `apps/openems_site.yaml` |
| `ontwikkeling/` | Tests (niet nodig in Home Assistant) | – |

De mappen `config` en `addon_configs` komen overeen met de gelijknamige
Samba-shares van Home Assistant OS.

---

## Vereisten

- **Home Assistant OS** 2026.11 of nieuwer
- Add-on **AppDaemon** (Instellingen → Add-ons → Add-on store → AppDaemon)
- Marstek Venus E (v1/v2 of v3), D of A met **Modbus TCP** op een **vast IP-adres**
  (Venus E v3 via de LAN-poort; oudere modellen via een RS485-naar-LAN-adapter)
- Een **P1-netvermogenssensor** in watt (positief = afname van het net)
- Een **dynamische all-in-prijssensor** met een `prices`-attribuut
  (bijv. de Frank Energie-integratie)

> **Belangrijk:** gebruik **niet** de `marstek_modbus`-integratie uit de
> Marketplace/HACS. OpenEMS-M heeft de meegeleverde versie nodig; de officiële
> versie mist de functies waarmee OpenEMS de accu's veilig aanstuurt. Staat die
> al geïnstalleerd, verwijder hem dan eerst (en accepteer daarna geen update
> van "Marstek Venus Modbus" uit de Marketplace).

---

## Installatie

1. **Back-up maken** in Home Assistant (Instellingen → Systeem → Back-ups).
2. **Bestanden kopiëren** (bijv. via de Samba-add-on):
   - `config/custom_components/marstek_modbus` → `/config/custom_components/`
   - `config/custom_components/openems_m_panel` → `/config/custom_components/`
   - `config/packages/openems_helper.yaml` → `/config/packages/`
   - `addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M` → AppDaemon `apps/`
3. **configuration.yaml** aanvullen (als dit er nog niet staat):

   ```yaml
   homeassistant:
     packages: !include_dir_named packages

   openems_m_panel:
   ```

4. **AppDaemon-app registreren**: zet in AppDaemon `apps/apps.yaml`:

   ```yaml
   marstek_ems:
     module: marstek_ems
     class: MarstekEMS
   ```

5. **Home Assistant herstarten.**
6. **Accu('s) toevoegen**: Instellingen → Apparaten en diensten → Integratie
   toevoegen → *Marstek Venus Modbus*. Vul in: IP-adres, poort `502`,
   Unit-ID `1`, het juiste apparaatmodel en het **OpenEMS-accunummer**
   (1 voor de eerste accu, 2 voor de tweede, …).
7. **Installatie-instellingen**: kopieer `openems_site.example.yaml` naar
   `apps/openems_site.yaml` en vul in:
   - `entiteit_taal`: `nl` of `en` (de taal van je Marstek-entiteiten)
   - `accus`: per accu het **prefix** van de entity_id's
     (heet je SOC-sensor `sensor.marstek_venus_battery_soc`, dan is het prefix `marstek_venus`)
   - `net`: maximale import en export van je aansluiting
   - `sensoren`: je P1-sensor (en eventuele CT-klemmen)
   - `prijs`: je all-in-prijssensor
8. **AppDaemon-add-on herstarten.** In het AppDaemon-log verschijnt
   `OpenEMS-M v…`, en in de zijbalk staat het paneel **OpenEMS-M**.
9. Controleer in het paneel onder **Instellingen** de entiteiten per accu en
   de netsensoren. Zet daarna **Automatisch** aan.

### Eerste dagen

- Kijk in het AppDaemon-log en in het paneel (Status) of de accu's de
  opdrachten bevestigen.
- OpenEMS drukt **nooit** zelf de resetknop van een accu in.
- Firmware-updates van Marstek kunnen de Modbus-verbinding tijdelijk
  verbreken; de driver verbindt daarna vanzelf opnieuw.

---

## Bijwerken

1. Back-up maken.
2. De vier mappen/bestanden uit stap 2 opnieuw kopiëren (je eigen
   `openems_site.yaml` en de map `OpenEMS-M-data` blijven staan).
3. Home Assistant herstarten en daarna de AppDaemon-add-on.
4. Paneel verversen met Ctrl+F5.

---

## Testen (ontwikkelaars)

Vanuit de hoofdmap van de repository:

```bash
PYTHONPATH=addon_configs/a0d7b954_appdaemon/apps/OpenEMS-M python -m pytest -q ontwikkeling/tests
node ontwikkeling/browser/assigned-hour-blocks.test.mjs
```

---

## Licentie en herkomst

- OpenEMS-M en het paneel: **GNU GPL v3** (zie `LICENSE`).
  Het visuele ontwerp van het paneel is afgeleid van
  [Marstek Venus Energy Manager](https://github.com/ffunes/Marstek-Venus-Energy-Manager) (GPL v3).
- De Modbus-driver is een aangepaste versie van
  [marstek_venus_modbus](https://github.com/ViperRNMC/marstek_venus_modbus) van ViperRNMC,
  **MIT-licentie** (zie `config/custom_components/marstek_modbus/LICENSE`).
