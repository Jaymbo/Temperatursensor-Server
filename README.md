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

## Lizenz

MIT License – siehe [LICENSE](LICENSE)# Test
