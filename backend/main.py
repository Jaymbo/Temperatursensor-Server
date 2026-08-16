from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from db import (
    get_all_series, get_data_by_sensor, get_comments_by_sensor,
    add_comment, delete_comment, get_calibration_points,
    add_calibration, add_temperature_data,
    get_start_time_of_latest_session,
    process_relative_data, clone_latest_session_with_calibration,
    initialize_db, add_or_update_custom_text_entry
)
from update import (
    pull_update, get_current_version, check_setup, check_for_update,
    get_admin_password
)
from typing import List, Dict
import json
import datetime

app = FastAPI()
# uvicorn main:app --reload --host 0.0.0.0 --port 8000

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Datenbank initialisieren
initialize_db()


# WebSocket-Verbindungsmanager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    # Implementiere die Logik, um die Nachricht an alle aktiven Verbindungen
    # zu senden
    async def broadcast(self, sensor_session: str, payload: dict):
        message = json.dumps({
            "sensor_session": sensor_session,
            "data": payload,
            "message": "Neue Daten verfügbar"
        })
        print("Broadcasting message:", message)  # Debugging-Log

        # Sende die Nachricht an alle aktiven WebSocket-Verbindungen
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                print(f"Fehler beim Senden an eine Verbindung: {e}")


manager = ConnectionManager()


def get_strategy_description(num_points: int) -> str:
    """Gibt eine Beschreibung der Kalibrierungsstrategie zurück"""
    if num_points == 0:
        return "Keine Korrektur"
    if num_points == 1:
        return "Globaler Offset"
    return f"Lineare Interpolation ({num_points} Punkte)"


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    print("WebSocket-Verbindung wird aufgebaut...")  # Debugging-Log
    await manager.connect(websocket)
    print("WebSocket-Verbindung erfolgreich aufgebaut.")  # Debugging-Log
    try:
        while True:
            # Falls Clients Nachrichten senden, können diese hier empfangen
            # werden
            message = await websocket.receive_text()
            print("Nachricht vom Client empfangen:", message)  # Debugging-Log
    except WebSocketDisconnect:
        print("WebSocket-Verbindung wurde getrennt.")  # Debugging-Log
        manager.disconnect(websocket)


@app.get("/series")
def list_series():
    # Gibt alle Sensor-Session-Kombinationen zurück
    return get_all_series()


@app.get("/series/{sensor_session}")
def get_series(sensor_session: str):
    # Prüfe ob es eine Preview-Session ist
    if sensor_session.endswith("_calibrated"):
        # Extrahiere die ursprüngliche sensor_session
        original_session = sensor_session.replace("_calibrated", "")
        
        print(f"Preview-Session angefordert: {sensor_session}")
        print(f"Ursprüngliche Session: {original_session}")
        
        # Hole die originalen Daten
        original_data = get_data_by_sensor(original_session)
        original_comments = get_comments_by_sensor(original_session)
        # Hole die Kalibrierungsparameter (nur sensor_id verwenden, nicht die komplette sensor_session)
        sensor_db_id = original_session.split('_')[0] if '_' in original_session else original_session
        calibration_data = get_calibration_points(sensor_db_id)

        if not calibration_data:
            return {"error": "Keine Kalibrierungsdaten für Preview verfügbar"}

        # Korrekturpunkte laden
        latest = calibration_data[-1]
        correction_points_str = latest.get("correction_points")
        correction_points = []
        if correction_points_str:
            import json as jsonmod
            try:
                correction_points = jsonmod.loads(correction_points_str)
            except Exception:
                pass

        from calibration_strategy import generate_corrected_preview_data
        corrected_data = generate_corrected_preview_data(original_session, correction_points)

        if corrected_data:
            # Konvertiere zu dem Format, das das Frontend erwartet
            preview_data = []
            for i, (timestamp, temp) in enumerate(zip(corrected_data["timestamps"], corrected_data["temperatures"])):
                preview_data.append({
                    "timestamp": timestamp,
                    "temperature": temp
                })

            print(f"Preview-Daten generiert: {len(preview_data)} Datenpunkte")
            return {"data": preview_data, "comments": original_comments}
        else:
            return {"error": "Fehler beim Generieren der Preview-Daten"}
    
    # Normale Session-Behandlung
    data = get_data_by_sensor(sensor_session)
    comments = get_comments_by_sensor(sensor_session)
    print("comments:", comments)  # Debugging-Log
    return {"data": data, "comments": comments}


@app.post("/notify/{sensor_session}")
async def notify(sensor_session: str, payload: dict):
    print("Notify-Route aufgerufen")
    print("Sensor-Session:", sensor_session)  # Debugging-Log
    # Die empfangenen Daten direkt an die Broadcast-Funktion weitergeben
    await manager.broadcast(sensor_session, payload)
    return {"status": "ok"}


@app.post("/comments")
async def add_comment_endpoint(comment: dict):
    """
    Fügt einen neuen Kommentar hinzu.
    Erwartet ein JSON-Objekt mit den Feldern:
    - sensor_session: str
    - timestamp: str
    - temperature: float
    - comment: str
    """
    # Kommentar hinzufügen
    add_comment(
        comment["sensor_session"],
        comment["timestamp"],
        comment["temperature"],
        comment["comment"]
    )
    new_comment = {
        "action": "add_comment",
        "timestamp": comment["timestamp"],
        "temperature": comment["temperature"],
        "comment": comment["comment"]
    }

    # Broadcast-Nachricht senden
    await manager.broadcast(comment["sensor_session"], new_comment)

    return {"status": "success"}


@app.delete("/comments")
async def delete_comment_endpoint(comment: dict):
    """
    Löscht einen Kommentar aus der Datenbank.
    Erwartet ein JSON-Objekt mit den Feldern:
    - sensor_session: str
    - timestamp: str
    """
    sensor_session = comment["sensor_session"]
    timestamp = comment["timestamp"]
    temperature = comment["temperature"]
    comment_text = comment["comment"]

    # Kommentar löschen
    delete_comment(
        sensor_session,
        timestamp,
        temperature,
        comment_text
    )

    # Broadcast-Nachricht senden
    await manager.broadcast(sensor_session, {
        "action": "delete_comment",
        "timestamp": timestamp,
        "temperature": temperature,
        "comment": comment_text
    })

    return {"status": "success"}


@app.get("/calibration")
def fetch_calibration_points(sensor_id: str):
    """
    Gibt alle Kalibrierungspunkte für eine bestimmte Sensor-Session zurück.
    """
    return get_calibration_points(sensor_id)


@app.post("/calibration")
def add_calibration_point(calibration_point: dict):
    """
    Speichert die Korrekturpunkte für einen Sensor.

    Erwartet ein JSON-Objekt mit den Feldern:
    - sensor_id: str
    - correction_points: str (JSON-String der Korrekturpunkte)
      Format: "[{"t": 25.0, "delta": -5.0}, ...]"
    """
    correction_points = calibration_point.get("correction_points", None)
    sensor_id = calibration_point["sensor_id"]
    # calibration_data bleibt unverändert; wir übergeben den bestehenden Wert nicht
    add_calibration(sensor_id, "", correction_points)
    return {"status": "success"}


@app.post("/measurements")
async def add_measurements_endpoint(payload: dict):
    """
    Fügt eine Liste von Temperaturwerten und Zeitpunkten in die Datenbank ein.
    Erwartet ein JSON-Objekt mit den Feldern:
    - sensor_id: str
    - timestamps: List[float] (erste Eintrag absolut in s, Rest als Differenzen in s)
    - temperatures: List[float] (erste Eintrag absolut, Rest als Differenzen)
    """
    sensor_id = payload.get("sensor_id")
    timestamps = payload.get("timestamps")
    temperatures = payload.get("temperatures")

    # Validierung der Eingabedaten
    if not sensor_id or not timestamps or not temperatures:
        return {"status": "error", "message": "sensor_id, timestamps und temperatures sind erforderlich."}

    if len(timestamps) != len(temperatures):
        return {"status": "error", "message": "Die Anzahl der Zeitpunkte und Temperaturen muss übereinstimmen."}
    absolute_temperatures = []
    absolute_timestamps = []
    try:
        # Hole den Startzeitpunkt der neuesten Session
        start_time = get_start_time_of_latest_session(sensor_id)

        # Konvertiere den Startzeitpunkt von einem String in ein datetime-Objekt
        start_time = datetime.datetime.fromisoformat(start_time)

        # Konvertiere relative Daten in absolute Werte
        absolute_timestamps, absolute_temperatures = process_relative_data(timestamps, temperatures)
        absolute_timestamps = [start_time + datetime.timedelta(seconds=ts) for ts in absolute_timestamps]

        # Konvertiere die absoluten Timestamps zurück in Strings für die Datenbank
        absolute_timestamps = [ts.isoformat() for ts in absolute_timestamps]

        # Daten in die Datenbank einfügen
        sensor_session = add_temperature_data(sensor_id, absolute_timestamps, absolute_temperatures)

        # Broadcast-Nachricht senden
        await manager.broadcast(sensor_session, {
            "action": "new_measurements",
            "timestamps": absolute_timestamps,
            "temperatures": absolute_temperatures
        })

        return {"status": "success", "sensor_session": sensor_session}
    except ValueError as e:
        return {"status": "error in main.py", "message": str(e), "absolute_timestamps": absolute_timestamps, "absolute_temperatures": absolute_temperatures}
    except Exception as e:
        print(f"Fehler beim Hinzufügen der Messdaten: {e}")
        return {"status": "error in main.py", "message": "Ein unerwarteter Fehler ist aufgetreten.", "absolute_timestamps": absolute_timestamps, "absolute_temperatures": absolute_temperatures}


@app.post("/update/pull")
def update_pull(request: dict):
    """
    Zieht die neueste Version vom Repository.
    Erwartet: {"password": str}
    Uvicorn --reload startet automatisch neu, wenn Dateien sich ändern.
    """
    password = request.get("password", "")
    result = pull_update(password)
    return result


@app.post("/update/{sensor_id}")
async def update_sensor_session(sensor_id: str):
    """
    Nutzt clone_latest_session_with_calibration, um die neueste Sensor-Session zu klonen
    und die Kalibrierdaten sowie die neue Sensor-Session zurückzugeben.

    :param sensor_id: Die ID des Sensors
    :return: Ein JSON-Objekt mit den Kalibrierdaten und der neuen Sensor-Session
    """
    try:
        result = clone_latest_session_with_calibration(sensor_id)

        # Broadcast the new sensor session
        await manager.broadcast(result["new_sensor_session"], {
            "action": "new_sensor_session",
            "sensor_session": result["new_sensor_session"],
            "custom_text": result.get("custom_text", "")  # Optional, falls custom_text verwendet wird
        })
        print(f"Neue Sensor-Session erstellt: {result['new_sensor_session']}")  # Debugging-Log

        return {
            "status": "success",
            "new_sensor_session": result["new_sensor_session"],
            "calibration_data": result["calibration_data"],
            "correction_points": result.get("correction_points"),
            "messintervall": result.get("messintervall", 60.0),
            "sendpuffer": result.get("sendpuffer", 10)
        }
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        print(f"Fehler beim Aktualisieren der Sensor-Session: {e}")
        return {"status": "error", "message": "Ein unerwarteter Fehler ist aufgetreten."}


@app.post("/start_sensor")
async def start_sensor(payload: dict):
    """
    Ruft die Funktion add_or_update_custom_text_entry auf, um einen neuen Eintrag mit custom_text zu erstellen
    oder einen bestehenden leeren Eintrag zu aktualisieren.

    :param payload: JSON-Objekt mit "custom_text" (str), "messintervall" (float, Default: 60.0), "sendpuffer" (int, Default: 10)
    :return: Ein JSON-Objekt mit der ID des Eintrags
    """
    try:
        custom_text = payload.get("custom_text", "")
        messintervall = payload.get("messintervall", 60.0)
        sendpuffer = payload.get("sendpuffer", 10)
        entry_id = f"None_{add_or_update_custom_text_entry(custom_text, messintervall, sendpuffer)}"
        print(f"Custom text: {custom_text}")  # Debugging-Log
        print(f"Neuer Eintrag erstellt oder aktualisiert: {entry_id}")  # Debugging-Log

        # Broadcast the new sensor session
        await manager.broadcast(entry_id, {
            "action": "new_sensor_session",
            "sensor_session": entry_id,
            "custom_text": custom_text
        })
        print(f"Broadcast-Nachricht gesendet: none_{entry_id}")  # Debugging-Log

        return {
            "status": "success",
            "entry_id": entry_id
        }
    except Exception as e:
        print(f"Fehler beim Starten des Sensors: {e}")
        return {
            "status": "error",
            "message": "Ein unerwarteter Fehler ist aufgetreten."
        }


@app.post("/calibrate")
async def calibrate_sensor(request: dict):
    """
    Kalibrierungs-Preview basierend auf interpolierten Korrekturpunkten.

    Erwartet:
    - sensor_session: str (z.B. "1_15")
    - calibration_points: [{"measured": T_m, "target": T_s}, ...]

    Die Korrektur wird als delta = target - measured berechnet und dann
    per linearer Interpolation auf die gemessenen Temperaturen gelegt.
    """
    try:
        sensor_session = request.get("sensor_session")
        calibration_points = request.get("calibration_points", [])

        if not sensor_session:
            return {"status": "error", "message": "sensor_session ist erforderlich"}

        if len(calibration_points) == 0:
            return {"status": "error", "message": "Mindestens ein Kalibrierpunkt erforderlich"}

        from calibration_strategy import apply_correction, interpolation_correction, generate_corrected_preview_data, merge_correction_points

        # Lade existierende Korrekturpunkte aus der DB
        sensor_db_id = sensor_session.split('_')[0] if '_' in sensor_session else sensor_session
        old_correction_points: List[Dict[str, float]] = []
        existing_cal = get_calibration_points(sensor_db_id)
        if existing_cal:
            latest = existing_cal[-1]
            cp_str = latest.get("correction_points")
            if cp_str:
                try:
                    old_correction_points = json.loads(cp_str)
                except Exception:
                    pass

        # Neue Kalibrierpunkte als Rohwert interpretieren (Frontend sendet Rohwert)
        raw_calibration_points = []
        for pt in calibration_points:
            measured = float(pt["measured"])
            target = float(pt["target"])
            raw_calibration_points.append({"t": measured, "delta": target - measured})

        # Merge mit bestehenden Punkten
        correction_points = merge_correction_points(old_correction_points, raw_calibration_points)

        # Gemergte Korrekturpunkte persistent speichern, damit /series/{session}_calibrated
        # die vollständige kumulative Korrektur nachlesen kann
        add_calibration(sensor_db_id, "", json.dumps(correction_points))

        # Preview-Session erstellen und Daten senden
        preview_session_id = f"{sensor_session}_calibrated"

        # Generiere korrigierte Daten mit interpolation_correction
        corrected_data = generate_corrected_preview_data(sensor_session, correction_points)

        # Diagnose: Kalibrier-Kurve (gemessen -> korrigiert)
        curve = {"measured": [], "corrected": []}
        if correction_points:
            measured_vals = [pt["t"] for pt in correction_points]
            t_min = min(measured_vals) - 5
            t_max = max(measured_vals) + 5
            step = 0.5
            T = t_min
            while T <= t_max + 1e-9:
                curve["measured"].append(T)
                curve["corrected"].append(apply_correction(T, correction_points))
                T += step

        # Diagnose: Korrekturfehler direkt an den Kalibrierpunkten
        point_checks = []
        for pt in correction_points:
            t, delta = pt["t"], pt["delta"]
            corrected_t = apply_correction(t, correction_points)
            target = t + delta
            point_checks.append({
                "measured": t,
                "target": target,
                "corrected": corrected_t,
                "error": corrected_t - target,
            })

        if corrected_data and len(corrected_data.get("temperatures", [])) > 0:
            await manager.broadcast(preview_session_id, {
                "action": "new_sensor_session",
                "sensor_session": preview_session_id,
                "custom_text": "🔧 Kalibrierungs-Vorschau",
            })

            await manager.broadcast(preview_session_id, {
                "action": "update_measurements",
                "timestamps": corrected_data["timestamps"],
                "temperatures": corrected_data["temperatures"],
                "calibration_points": len(correction_points),
                "strategy": get_strategy_description(len(correction_points)),
            })

            print(f"  ✅ Preview-Session {preview_session_id} aktualisiert mit {len(corrected_data['temperatures'])} Datenpunkten")
            print(f"  📊 Strategie: {get_strategy_description(len(correction_points))}")
        else:
            print("  ⚠️  Keine Preview-Daten generiert")

        print(f"Kalibrierungs-Preview für {sensor_session} erstellt:")
        print(f"  Korrekturpunkte: {correction_points}")
        print(f"  Preview-Session-ID: {preview_session_id}")

        return {
            "status": "success",
            "message": f"Kalibrierungs-Preview mit {len(correction_points)} Punkten erstellt",
            "correction_points": correction_points,
            "preview_session": preview_session_id,
            "is_preview": True,
            "curve": curve,
            "point_checks": point_checks,
        }
    except HTTPException:  # pragma: no cover
        raise  # pragma: no cover
    except Exception as e:
        print(f"Fehler bei der Kalibrierung: {e}")
        return {"status": "error", "message": f"Kalibrierungsfehler: {str(e)}"}


@app.post("/verify_password")
def verify_password(request: dict):
    """
    Verifiziert das Admin-Passwort.
    Erwartet: {"password": str}
    Gibt {"valid": bool} zurück.
    """
    password = request.get("password", "")
    admin_pw = get_admin_password()
    return {"valid": password == admin_pw}


# ── Update Endpoints ─────────────────────────────────────────────

@app.get("/version")
def version_info():
    """Gibt die aktuelle Versionsnummer des Backend-Containers zurück."""
    return {"version": get_current_version()}


@app.get("/setup")
def setup_check():
    """
    Prüft, ob das System korrekt konfiguriert ist.
    Gibt is_configured, missing, version zurück.
    """
    setup = check_setup()
    setup["version"] = get_current_version()
    return setup


@app.get("/update/check")
def update_check():
    """Prüft, ob es neue Commits auf origin/main gibt (öffentlich)."""
    result = check_for_update()
    return result
