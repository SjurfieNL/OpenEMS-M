# Wijzigingen

## 1.0.0-beta.1

Eerste installeerbare versie van OpenEMS-M als Home Assistant-integratie.

- Installatie via HACS met een wizard: energiecontract, accu's (met
  verbindingstest), aansluiting, netsensoren en start.
- Geen AppDaemon meer nodig: de OpenEMS-logica draait binnen Home Assistant op
  een tussenlaag met hetzelfde gedrag (alle callbacks achter elkaar).
- Marstek Venus Modbus-driver ingebouwd; elke accu is een onderdeel met
  "Accu toevoegen". Een onbereikbare accu blokkeert de andere niet.
- 1 tot 4 accu's (doorlopend genummerd vanaf 1).
- Alle OpenEMS-instellingen (172) zijn entiteiten van de integratie; geen
  YAML-package meer nodig. Bij een overstap worden bestaande waarden van de
  oude helpers automatisch overgenomen.
- OpenEMS-M-paneel in de zijbalk, onderdeel van de integratie.
- Data en `openems_site.yaml` in `/config/openems_m/`.
