# Temperature Monitoring Server

Produktionsserver für das Temperatur-Monitoring-System.

## Architektur

```
┌─────────────┐      HTTP POST /measurements      ┌──────────────────┐
│ ESP8266     │ ────────────────────────────────► │  FastAPI backend │
│ sensor node │      POST /update/{sensor_id}     │  SQLite database   │
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

## Lizenz

MIT License – siehe [LICENSE](LICENSE)# Test
