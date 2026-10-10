# Temperature Monitoring Server

Produktionsserver für das Temperatur-Monitoring-System.

## Architektur

```
┌─────────────┐      HTTP POST /measurements      ┌──────────────────┐
│ ESP8266     │ ────────────────────────────────► │  FastAPI backend │
│ sensor node │      POST /update/{sensor_id}     │  SQLite database │
└─────────────┘                                   └────────┬─────────┘
                                                           │
                                      WebSocket /ws, REST  │
                                      ┌────────────────────┘
                                      ▼
                           ┌────────────────────┐
                           │ React/Vite dashboard
                           │ (nginx, static files)
                           └────────────────────┘
```

## Schnellstart

```bash
# 1. Voraussetzungen installieren (git, docker, docker compose)
# 2. Setup-Skript ausführen
bash setup-server.sh

# Frontend:  http://<ip>:5173
# Backend:   http://<ip>:8000
```

## Konfiguration

Kopiere `.env.example` nach `.env` und passe an:

```env
ADMIN_PASSWORD=dein-sicheres-passwort
GITHUB_TOKEN=    # Optional, nur für private Repos
```

## Self-Update

Der Server aktualisiert sich selbst via `POST /update/pull`. Nach erfolgreichem
Update wird der Container automatisch neu gestartet (`SET_AUTO_RESTART=true`).

## Zeit-Kalibrierung (ESP8266-Uhr-Abweichung)

Die ESP8266-Uhr kann über Tage driften (z. B. 50 min/Tag). Über den
Kalibrierungsmodus (Button oben rechts → Passwort → **Zeit**) kann man das
korrigieren, ohne die Rohdaten zu verändern:

- **Bedienung:** Kalibriermodus → *Zeit* → Punkt auf der Kurve anklicken →
  korrekte Uhrzeit eingeben (z. B. `08:35` statt angezeigtem `07:40`) →
  *Vorschau* → *Anwenden*.
- **Wie es funktioniert:** Der Startzeitpunkt bleibt **fix**. Für jeden Punkt
  wird nur der Abstand zum Start mit einem Faktor `K` skaliert:
  `korrigiert = start + (gemessen − start) · K`.
- **Kumulative Mehrfach-Kalibrierung:** Ein neuer Punkt rechnet
  `K_neu = K_old · (wirklich − start) / (angezeigt − start)`, also
  „bisheriger Faktor × neuer Faktor", ohne Rundungsakkumulation.
- **Kein ESP-Change:** Der Faktor wird pro Sensor gespeichert
  (`time_calibration`-Tabelle) und nur beim **Anzeigen** (Read-Pfad +
  Live-Broadcast) angewandt. Die gespeicherten Roh-Timestamps bleiben
  unverändert, daher ist die Korrektur jederzeit revidierbar.
- **Vergangene & zukünftige Sessions:** Der Faktor gilt pro Sensor und wird
  für alle Sessions angewandt (jede Session relativ zu ihrem eigenen Start).

## Akku-Telemetrie & Akku-Gesundheit

Die Sensoren melden neben der Temperatur auch die Akkuspannung. Das Backend
speichert sie getrennt von den Temperaturdaten und stellt eine Auswertung +
Dashboard-Ansicht bereit.

### Datenmodell (`backend/db.py`)

| Tabelle | Zweck |
|---|---|
| `batteries` | Akku-Stammdaten (`battery_id`, Kapazitätsklasse, Kaufdatum, Status) |
| `battery_assignments` | Zuordnung Akku↔Sensor mit `from_time`/`to_time` (Wechsel = protokolliertes Ereignis) |
| `battery_readings` | Roh-Messwerte (`measured_at`, `mv`, `raw`, `under_load`, `seq`, `hw_rev`); `UNIQUE(sensor_id, measured_at)` |
| `battery_sessions` | erkannte Betriebsphasen (`measure`/`charge`/`outage`) |
| `power_measurements` | Strom-Rohproben des Mess-ESP (INA226) |
| `power_aggregates` | Live-Aggregate (inkl. integrierter Ladung `charge_uah`) |

**Wichtig:** `battery_id` ist bewusst **vom Sensor getrennt** modelliert. Logger 1
hatte einen Gerätefehler (Kurzschluss), keinen Akkufehler – der Akku kann an
einen anderen Sensor wandern. Akkus sind verlötet; ein Wechsel ist ein
eigenständiges, protokolliertes Ereignis.

### Auswertung (`backend/battery_health.py`)

Ein **absoluter Spannungsschwellwert** kann „alt/defekt“ nicht von „gerade leer“
unterscheiden (Ladezustand, Temperatur, Last). Deshalb werden **relative
Kennzahlen im Flottenvergleich** berechnet:

1. Entladerate (mV/Tag) – findet Alterung
2. Zeit Voll → Abschaltgrenze (`CUTOFF_MV=3150`) – direkter Kapazitätsersatz
3. Ladeverhalten (Endspannung, 6-h-Stopp) – findet den Ladecontroller-Bug
4. Zyklenzahl aus erkannten Lade-/Entladesessions
5. optional Innenwiderstand `R=dV/dI` (braucht Last-/Ruhepaare)

Bei zu wenig Daten wird bewusst `unbekannt` ausgegeben statt zu raten.

**Akku-Anzeige:** LiPo ist zwischen ca. 3,7–3,9 V sehr flach – eine lineare
Prozentanzeige aus der Spannung existiert nicht. Daher bewusst **5 Stufen** statt
Prozent (`battery_level_stage()` in `battery_health.py`): 5 „voll“, 4 „gut“,
3 „mittel“, 2 „schwach“, 1 „kritisch“, 0 „leer“. Liegt eine Messung **unter Last**
vor (`under_load`), wird die steilere Spannung genutzt und der Wert entsprechend
**gekennzeichnet** (nicht umskaliert).

**Einstellbare Schwellwerte:** Die Grenzen „voll“ und „leer“ stehen in
`backend/battery_thresholds.json` (Format `{"full_mv": 4200, "empty_mv": 3300}`).
Fehlt die Datei oder ist sie ungültig, gelten die Defaults (voll = 4200 mV,
leer = 3300 mV – bei 3,7 V Nennspannung ist noch viel Kapazität übrig). Die
5 Stufen werden gleichmäßig zwischen voll und leer verteilt; Änderungen wirken
ohne Neustart beim nächsten Aufruf.

### REST-Endpunkte

| Methode | Pfad | Beschreibung |
|---|---|---|
| GET | `/battery/summary` | Statuszeile je Sensor: letzter Wert, Stufe, Alter, Akku-ID, `hw_rev` |
| GET | `/battery/fleet` | Flottenvergleich (relative Kennzahlen, Ausreißer oben) |
| GET | `/battery/health` | Vollauswertung; optional `?sensor_id=…`; persistiert erkannte Phasen |
| GET | `/battery/{sensor_id}` | Verlauf: `readings`, `sessions`, `assignment` (für den Chart) |
| POST | `/battery` | Akku anlegen/aktualisieren (Stammdaten) |
| POST | `/battery/assign` | Akku einem Sensor zuordnen / Wechsel protokollieren |
| POST | `/power/measurements` | Strom-Rohproben des Mess-ESP (Entladen positiv, Laden negativ) |
| POST | `/power/aggregate` | Live-Aggregat (Integration erfolgt im Mess-ESP) |
| GET | `/power/{logger_id}` | Aggregate + Rohproben eines Loggers |

Die Akkuspannung wird vom Sensor als Teil des `/measurements`-Payloads
gesendet und über `_store_battery_payload()` gespeichert.

### Dashboard-Ansicht

Der Button **„Akku“** in der Toolbar öffnet ein Panel (`aria-label="Akku-Uebersicht"`) mit:

- **Badges** je Sensor (Ampelpunkt, Spannung, Stufe, Alter)
- **Status-Tabelle** (Entladerate, Resttage, Zyklen, Verdacht inkl. Ursache)
- **Flottenvergleich** (normierte Balken, Median = 100 %)
- **Akku-Chart** (Spannung über Zeit, gestrichelte `CUTOFF_V=3.15`-Referenz,
  Marker für Laden/Ausfall/Akkuwechsel, `under_load`-Punkte hervorgehoben)

Klick auf Badge, Tabellenzeile oder Balken lädt den Verlauf des jeweiligen Sensors.
Zusätzlich zeigt die **Session-Liste** links unter der Session-Auswahl je Sensor
(kompaktes Badge: Ampelpunkt + Spannung) den Akku-Status der **neuesten Session**;
ein Klick öffnet das Akku-Panel mit dem Verlauf.
Details siehe `temperature-dashboard/README.md`.

## Lizenz

MIT License – siehe [LICENSE](LICENSE)
