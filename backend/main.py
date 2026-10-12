from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from db import (
    get_all_series, get_data_by_sensor, get_comments_by_sensor,
    add_comment, delete_comment, get_calibration_points,
    add_calibration, add_temperature_data,
    get_start_time_of_latest_session,
    process_relative_data, clone_latest_session_with_calibration,
    initialize_db, add_or_update_custom_text_entry,
    get_time_factor, set_time_factor, get_session_start_time, apply_time_factor,
    # Akku-Telemetrie
    get_session_hw_rev, set_session_hw_rev,
    get_battery_assignment, add_battery_assignment, close_open_battery_assignment,
    add_battery_readings, get_battery_readings, get_latest_battery_reading,
    get_battery_sensor_ids,
    upsert_battery, get_batteries, replace_battery_sessions, get_battery_sessions,
    # Strommessung
    add_power_measurements, add_power_aggregate, get_power_aggregates,
    get_power_measurements, get_power_logger_ids, get_sessions_chronological,
)
import db
import power_cycles
from update import (
    pull_update, get_current_version, check_setup, check_for_update,
    get_admin_password
)
import battery_health
from typing import List, Dict
import json
import datetime
import sqlite3
import threading
import time

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


def _extract_sensor_id(sensor_session: str) -> str:
    """
    Extrahiert die sensor_id aus einer sensor_session ('sensor_id_session_id').
    Fehlt '_', wird die Session selbst als sensor_id verwendet.
    """
    if "_" in sensor_session:
        return sensor_session.split("_", 1)[0]
    return sensor_session


def apply_time_calibration_to_session(
    sensor_session: str,
    data: list,
    factor: float | None = None,
) -> list:
    """
    Wendet den (persistenten oder uebergebenen) Zeit-Kalibrierungs-Faktor K auf
    alle Timestamps einer Session an. Temperaturen bleiben unveraendert; nur die
    Timestamps verschieben sich relativ zum Session-Start (fixer Startpunkt).

    :param sensor_session: 'sensor_id_session_id'
    :param data: Liste von {timestamp, temperature}
    :param factor: Explicit Faktor; Default = persistenter Faktor des Sensors
    :return: Neue Liste mit korrigierten Timestamps (Datenobjekte unveraendert)
    """
    if not data:
        return data
    if factor is None:
        factor = get_time_factor(_extract_sensor_id(sensor_session))
    if factor == 1.0:
        return data
    start_time = get_session_start_time(sensor_session)
    if start_time is None:
        return data
    result = []
    for entry in data:
        if not isinstance(entry, dict):
            result.append(entry)
            continue
        corrected = dict(entry)
        if "timestamp" in entry:
            corrected["timestamp"] = apply_time_factor(
                entry["timestamp"], start_time, factor
            )
        result.append(corrected)
    return result


def generate_time_calibration_preview(
    sensor_session: str,
    factor: float,
) -> dict | None:
    """
    Erzeugt korrigierte Messdaten (Timestamps) fuer eine Zeit-Kalibrierungs-Preview.

    Rohdaten aus der DB werden mit dem gegebenen Faktor K relativ zum Session-Start
    verschoben. Temperaturen bleiben unveraendert.

    :return: {timestamps: [...], temperatures: [...]} oder None
    """
    original = get_data_by_sensor(sensor_session)
    if not original:
        return None
    start_time = get_session_start_time(sensor_session)
    timestamps = []
    temperatures = []
    for p in original:
        if not isinstance(p, dict):
            continue
        ts = p.get("timestamp")
        temp = p.get("temperature")
        if ts is None or temp is None:
            continue
        timestamps.append(apply_time_factor(ts, start_time, factor))
        temperatures.append(temp)
    if not timestamps:
        return None
    return {"timestamps": timestamps, "temperatures": temperatures}


def compute_new_time_factor(
    current_factor: float,
    measured_timestamp: str,
    actual_timestamp: str,
    start_time: str | None,
) -> float:
    """
    Berechnet den neuen Zeit-Kalibrierungs-Faktor K aus einem Kalibrierpunkt.

    Der Nutzer klickt auf einen *angezeigten* (bereits mit current_factor
    korrigierten) Punkt `measured` und sagt, dieser soll eigentlich `actual` sein.
    Der Startpunkt ist fix, daher:
        K_new = K_old * (actual - start) / (measured - start)

    Dies entspricht kumulativ "bisheriger_Faktor * neuer_Faktor" und vermeidet
    Rundungsakkumulation, da immer der Abstand zum fixen Start betrachtet wird.

    :param current_factor: Bisheriger Faktor K_old (1.0 falls keine Kalibrierung)
    :param measured_timestamp: ISO-String des (korrigierten) angezeigten Punkts
    :param actual_timestamp: ISO-String des vom Nutzer gewuenschten (echten) Punkts
    :param start_time: ISO-String des Session-Starts
    :return: Der neue Faktor K_new
    """
    if start_time is None:
        raise ValueError("start_time ist erforderlich, um einen Zeitfaktor zu berechnen.")
    try:
        measured = datetime.datetime.fromisoformat(measured_timestamp)
        actual = datetime.datetime.fromisoformat(actual_timestamp)
        start = datetime.datetime.fromisoformat(start_time)
    except (ValueError, TypeError) as e:
        raise ValueError("Ungueltige ISO-Timestamps.") from e

    measured_offset = (measured - start).total_seconds()
    actual_offset = (actual - start).total_seconds()
    if abs(measured_offset) < 1e-9:
        # Punkt liegt am Start -> Abstand 0, Faktor nicht bestimmbar; alten behalten
        return current_factor
    return current_factor * (actual_offset / measured_offset)


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

    # Zeit-Kalibrierung: Timestamps relativ zum Session-Start skalieren (fixer Start).
    # Temperaturen bleiben unveraendert; der Faktor ist pro sensor_id gespeichert.
    sensor_id = _extract_sensor_id(sensor_session)
    data = apply_time_calibration_to_session(sensor_session, data, get_time_factor(sensor_id))
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


def _store_battery_payload(sensor_id: str, session_start: datetime.datetime, payload: dict) -> int:
    """
    Legt die im /measurements-Paket mitgesendeten Akku-Werte ab.

    Die Akku-Listen (battery_mv/-seq/-ts/-raw) sind PARALLEL und beziehen sich
    auf die Geraete-Elapsed-Zeit (battery_ts), NICHT auf die Temperaturliste.
    Der absolute Messzeitpunkt ist Session-Start + battery_ts. Als received_at
    wird die Serverzeit gespeichert (bekanntes Zeitdrift-Problem: beide Zeiten
    werden gehalten, siehe db._create_battery_tables).

    :return: Anzahl der tatsaechlich eingefuegten (plausiblen) Werte.
    """
    mv_list = payload.get("battery_mv")
    if not mv_list:
        return 0

    seq_list = payload.get("battery_seq") or []
    ts_list = payload.get("battery_ts") or []
    raw_list = payload.get("battery_raw") or []

    assignment = get_battery_assignment(sensor_id)
    battery_id = assignment["battery_id"] if assignment else None
    hw_rev = get_session_hw_rev(sensor_id)
    received_at = datetime.datetime.now().isoformat()

    rows = []
    for i, mv in enumerate(mv_list):
        if i >= len(ts_list):
            break
        try:
            measured_at = (session_start + datetime.timedelta(
                seconds=float(ts_list[i]))).isoformat()
        except (TypeError, ValueError):
            continue
        rows.append({
            "sensor_id": sensor_id,
            "battery_id": battery_id,
            "measured_at": measured_at,
            "received_at": received_at,
            "mv": mv,
            "raw": raw_list[i] if i < len(raw_list) else None,
            "under_load": None,
            "seq": seq_list[i] if i < len(seq_list) else None,
            "hw_rev": hw_rev,
        })
    return add_battery_readings(rows)


@app.post("/measurements")
async def add_measurements_endpoint(payload: dict):
    """
    Fügt eine Liste von Temperaturwerten und Zeitpunkten in die Datenbank ein.
    Erwartet ein JSON-Objekt mit den Feldern:
    - sensor_id: str
    - timestamps: List[float] (erste Eintrag absolut in s, Rest als Differenzen in s)
    - temperatures: List[float] (erste Eintrag absolut, Rest als Differenzen)
    - hw_rev: int (optional) Hardware-Revision -> wird an der Session vermerkt
    - battery_mv / battery_seq / battery_ts / battery_raw: optionale, parallele
      Listen der Akku-Telemetrie (siehe Firmware battery.ino). battery_ts ist die
      Geraete-Elapsed-Zeit je Messwert (NICHT die der Temperaturliste!).
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

        # Hardware-Revision der laufenden Session merken (erster Wert gewinnt).
        hw_rev = payload.get("hw_rev")
        if hw_rev is not None:
            try:
                set_session_hw_rev(sensor_id, int(hw_rev))
            except Exception as e:
                print(f"hw_rev konnte nicht gespeichert werden: {e}")

        # Akku-Telemetrie ablegen (Zeit = Session-Start + Geraete-Elapsed).
        _store_battery_payload(sensor_id, start_time, payload)

        # Zeit-Kalibrierung: Timestamps relativ zum Session-Start skalieren (fixer Start).
        # Rohdaten in der DB bleiben unveraendert; nur die angezeigten Werte werden verschoben.
        factor = get_time_factor(sensor_id)
        if factor != 1.0:
            broadcast_timestamps = [
                apply_time_factor(ts, start_time.isoformat(), factor) for ts in absolute_timestamps
            ]
        else:
            broadcast_timestamps = absolute_timestamps

        # Broadcast-Nachricht senden
        await manager.broadcast(sensor_session, {
            "action": "new_measurements",
            "timestamps": broadcast_timestamps,
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
            "calibration_hw_rev": get_session_hw_rev(sensor_id),
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
        messintervall = payload.get("messintervall", 10.0)
        sendpuffer = payload.get("sendpuffer", 60)
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

        from calibration_strategy import apply_correction, interpolation_correction, generate_corrected_preview_data

        # Kalibrierungs-Preview: NUR berechnen, NICHTS in der DB speichern.
        # Die Persistenz erfolgt erst bei "Kalibrierung anwenden" via POST /calibration.
        # Die gesendeten Punkte bilden die vollständige (kumulative) Punktmenge
        # und ERSETZEN bestehende Punkte – es wird nicht gemerged/akkumuliert.
        raw_calibration_points = []
        for pt in calibration_points:
            measured = float(pt["measured"])
            target = float(pt["target"])
            raw_calibration_points.append({"t": measured, "delta": target - measured})

        correction_points = raw_calibration_points

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


@app.post("/calibrate_time")
async def calibrate_time_sensor(request: dict):
    """
    Zeit-Kalibrierungs-Preview (NICHT persistent) basierend auf einem Kalibrierpunkt.

    Erwartet:
    - sensor_session: str (z.B. "1_15")
    - measured_timestamp: str (ISO des ANGEZEIGTEN / bereits mit aktuellem Faktor
      korrigierten Punkts, den der Nutzer geklickt hat)
    - actual_timestamp: str (ISO des vom Nutzer gewuenschten ECHTEN Zeitpunkts)

    Der Startpunkt der Session ist fix; der Faktor wird aus
      K_new = K_old * (actual - start) / (measured - start)
    berechnet (kumulativ; entspricht "bisheriger * neuer Faktor").
    Die Preview wird ueber WebSocket als '_calibrated'/'_time'-Session gesendet,
    in der DB wird nichts gespeichert (erst bei /calibrate_time/apply).
    """
    try:
        sensor_session = request.get("sensor_session")
        measured_ts = request.get("measured_timestamp")
        actual_ts = request.get("actual_timestamp")

        if not sensor_session or not measured_ts or not actual_ts:
            return {
                "status": "error",
                "message": "sensor_session, measured_timestamp und actual_timestamp sind erforderlich.",
            }

        sensor_id = _extract_sensor_id(sensor_session)
        current_factor = get_time_factor(sensor_id)
        start_time = get_session_start_time(sensor_session)
        if start_time is None:
            return {"status": "error", "message": "Keine Session/Startzeit gefunden."}

        new_factor = compute_new_time_factor(
            current_factor, measured_ts, actual_ts, start_time
        )
        if new_factor <= 0:
            return {"status": "error", "message": "Ungueltiger Zeitfaktor berechnet."}

        corrected = generate_time_calibration_preview(sensor_session, new_factor)
        preview_session = f"{sensor_session}_calibrated"

        if corrected and len(corrected.get("timestamps", [])) > 0:
            await manager.broadcast(preview_session, {
                "action": "new_sensor_session",
                "sensor_session": preview_session,
                "custom_text": "⏱ Zeit-Kalibrierung-Vorschau",
            })
            await manager.broadcast(preview_session, {
                "action": "update_measurements",
                "timestamps": corrected["timestamps"],
                "temperatures": corrected["temperatures"],
                "calibration_points": 1,
                "strategy": "Zeit-Kalibrierung",
            })

        return {
            "status": "success",
            "message": f"Zeit-Kalibrierungs-Preview erstellt (Faktor K={new_factor:.6f}).",
            "current_factor": current_factor,
            "new_factor": new_factor,
            "preview_session": preview_session,
            "is_preview": True,
        }
    except Exception as e:
        print(f"Fehler bei der Zeit-Kalibrierung: {e}")
        return {"status": "error", "message": f"Zeit-Kalibrierungsfehler: {str(e)}"}


@app.post("/calibrate_time/apply")
def apply_time_calibration(request: dict):
    """
    Speichert den Zeit-Kalibrierungs-Faktor K fuer einen Sensor (persistent).

    Erwartet:
    - sensor_session: str
    - measured_timestamp: str (ISO des ANGEZEIGTEN Punkts)
    - actual_timestamp: str (ISO des gewuenschten ECHTEN Punkts)

    Berechnet K_new = K_old * (actual - start) / (measured - start) und speichert
    ihn. Rohdaten bleiben unveraendert; beim naechsten Lesezugriff wird K angewandt.
    """
    try:
        sensor_session = request.get("sensor_session")
        measured_ts = request.get("measured_timestamp")
        actual_ts = request.get("actual_timestamp")

        if not sensor_session or not measured_ts or not actual_ts:
            return {
                "status": "error",
                "message": "sensor_session, measured_timestamp und actual_timestamp sind erforderlich.",
            }

        sensor_id = _extract_sensor_id(sensor_session)
        current_factor = get_time_factor(sensor_id)
        start_time = get_session_start_time(sensor_session)
        if start_time is None:
            return {"status": "error", "message": "Keine Session/Startzeit gefunden."}

        new_factor = compute_new_time_factor(
            current_factor, measured_ts, actual_ts, start_time
        )
        if new_factor <= 0:
            return {"status": "error", "message": "Ungueltiger Zeitfaktor berechnet."}

        set_time_factor(sensor_id, new_factor)
        return {
            "status": "success",
            "message": f"Zeit-Kalibrierung gespeichert (Faktor K={new_factor:.6f}).",
            "new_factor": new_factor,
        }
    except Exception as e:
        print(f"Fehler beim Speichern der Zeit-Kalibrierung: {e}")
        return {"status": "error", "message": f"Zeit-Kalibrierungsfehler: {str(e)}"}


@app.get("/time_calibration")
def get_time_calibration(sensor_id: str):
    """Gibt den aktuell gespeicherten Zeit-Kalibrierungs-Faktor K eines Sensors zurueck."""
    return {
        "sensor_id": sensor_id,
        "factor": get_time_factor(sensor_id),
    }


@app.delete("/time_calibration")
def reset_time_calibration(sensor_id: str):
    """Setzt den Zeit-Kalibrierungs-Faktor K eines Sensors zurueck auf 1.0 (kein Faktor)."""
    set_time_factor(sensor_id, 1.0)
    return {"status": "success", "sensor_id": sensor_id, "factor": 1.0}


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


# ── Akku-Telemetrie ──────────────────────────────────────────────
#
# Aufteilung der Auswertung:
#   * /battery/summary  -> schnelle Statuszeile je Sensor (Akku-Symbol + Alter)
#   * /battery/{id}     -> Verlauf eines Sensors (fuer den Chart)
#   * /battery/fleet    -> Flottenvergleich (relative Metriken)
#   * /battery/health   -> Vollauswertung inkl. Verdachtsursache
#
# Die eigentliche Analyse steckt in battery_health.py; hier werden nur Daten
# geladen und zusammengefuehrt. Erkannte Betriebsphasen (Laden/Ausfall) werden
# bei /battery/health zusaetzlich persistiert, damit sie im UI als Marker
# dienen - die Rohdaten bleiben unberuehrt und die Erkennung aenderbar.

# Erwartetes Sendeintervall fuer die Ausfallerkennung (Firmware-Default).
_BATTERY_EXPECTED_INTERVAL_S = 10.0


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _reading_age_seconds(reading: dict | None):
    """Alter eines Messwerts in Sekunden (bevorzugt Geraetezeit, sonst Empfang)."""
    if not reading:
        return None
    ref = _parse_iso(reading.get("measured_at")) or _parse_iso(reading.get("received_at"))
    if ref is None:
        return None
    return max(0.0, (datetime.datetime.now() - ref).total_seconds())


@app.get("/battery/summary")
def battery_summary():
    """
    Statuszeile je Sensor: letzter Messwert, Stufe (5 Stufen, KEIN Prozent),
    Alter der letzten Messung und zugeordneter Akku.
    """
    summary = []
    for sensor_id in get_battery_sensor_ids():
        reading = get_latest_battery_reading(sensor_id)
        assignment = get_battery_assignment(sensor_id)
        summary.append({
            "sensor_id": sensor_id,
            "battery_id": assignment["battery_id"] if assignment else None,
            "latest": reading,
            "level": battery_health.battery_level_stage(
                reading["mv"] if reading else None,
                reading.get("under_load") if reading else None,
            ),
            "age_seconds": _reading_age_seconds(reading),
            "hw_rev": get_session_hw_rev(sensor_id),
        })
    return {"summary": summary}


@app.get("/battery/fleet")
def battery_fleet():
    """
    Flottenvergleich: relative Metriken je Sensor/Akku, Ausreisser oben.
    Nutzt bewusst RELATIVE Kennzahlen (Rate, Voll->Abschalt, Ladeverhalten),
    da ein absoluter Spannungsschwellwert "alt" nicht von "leer" unterscheidet.
    """
    per_sensor = {}
    for sensor_id in get_battery_sensor_ids():
        readings = get_battery_readings(sensor_id)
        assignment = get_battery_assignment(sensor_id)
        per_sensor[sensor_id] = battery_health.analyze_sensor(
            sensor_id,
            readings,
            expected_interval_s=_BATTERY_EXPECTED_INTERVAL_S,
            hw_rev=get_session_hw_rev(sensor_id),
            battery_id=assignment["battery_id"] if assignment else None,
        )
    return battery_health.build_fleet(per_sensor)


@app.get("/battery/health")
def battery_health_all(sensor_id: str | None = None):
    """
    Vollauswertung der Akku-Gesundheit.

    :param sensor_id: optional auf einen Sensor beschraenken
    """
    sensor_ids = [sensor_id] if sensor_id else get_battery_sensor_ids()
    result = {}
    for sid in sensor_ids:
        readings = get_battery_readings(sid)
        assignment = get_battery_assignment(sid)
        analysis = battery_health.analyze_sensor(
            sid,
            readings,
            expected_interval_s=_BATTERY_EXPECTED_INTERVAL_S,
            hw_rev=get_session_hw_rev(sid),
            battery_id=assignment["battery_id"] if assignment else None,
        )
        # Erkannte Betriebsphasen ableiten und persistieren (Rohdaten unberuehrt).
        sessions = analysis["measure_sessions"] + analysis["charge_sessions"] + analysis["outages"]
        replace_battery_sessions(sid, [
            {
                "battery_id": assignment["battery_id"] if assignment else None,
                "start_time": s["start_time"],
                "end_time": s.get("end_time"),
                "type": s["type"],
                "note": s.get("note"),
            }
            for s in sessions
        ])
        result[sid] = analysis
    return {"health": result}


@app.get("/battery/{sensor_id}")
def battery_history(sensor_id: str, limit: int | None = None, since: str | None = None):
    """Akku-Verlauf eines Sensors (chronologisch) fuer den Chart."""
    return {
        "sensor_id": sensor_id,
        "readings": get_battery_readings(sensor_id, limit=limit, since=since),
        "sessions": get_battery_sessions(sensor_id),
        "assignment": get_battery_assignment(sensor_id),
    }


@app.post("/battery")
def create_or_update_battery(payload: dict):
    """
    Legt einen Akku an bzw. aktualisiert seine Stammdaten.
    Erwartet: {battery_id, capacity_class?, purchase_date?, status?, notes?}
    """
    battery_id = payload.get("battery_id")
    if not battery_id:
        return {"status": "error", "message": "battery_id ist erforderlich."}
    upsert_battery(
        battery_id,
        capacity_class=payload.get("capacity_class"),
        purchase_date=payload.get("purchase_date"),
        status=payload.get("status", "in_use"),
        notes=payload.get("notes"),
    )
    return {"status": "success", "batteries": get_batteries()}


@app.post("/battery/assign")
def assign_battery(payload: dict):
    """
    Ordnet einen Akku einem Sensor zu bzw. protokolliert einen Wechsel.

    Erwartet: {sensor_id, battery_id, from_time?, reason?}
    Bei einem Wechsel wird die offene Zuordnung geschlossen (to_time = from_time).
    """
    sensor_id = payload.get("sensor_id")
    battery_id = payload.get("battery_id")
    if not sensor_id or not battery_id:
        return {"status": "error", "message": "sensor_id und battery_id sind erforderlich."}
    from_time = payload.get("from_time") or datetime.datetime.now().isoformat()
    reason = payload.get("reason", "initial")
    close_open_battery_assignment(sensor_id, from_time)
    add_battery_assignment(sensor_id, battery_id, from_time, reason=reason)
    return {"status": "success", "assignment": get_battery_assignment(sensor_id)}


def _power_cycle_kwargs(
    peak_trigger_ma: float | None = None,
    pre_peak_s: float | None = None,
    refractory_s: float | None = None,
    spike_end_ma: float | None = None,
    max_cycle_s: float | None = None,
    gap_s: float | None = None,
) -> dict:
    """Nur gesetzte Schwellwert-Parameter an die Analyse durchreichen."""
    names = ("peak_trigger_ma", "pre_peak_s", "refractory_s", "spike_end_ma",
             "max_cycle_s", "gap_s")
    return {n: v for n, v in zip(names, (peak_trigger_ma, pre_peak_s,
                                          refractory_s, spike_end_ma,
                                          max_cycle_s, gap_s))
            if v is not None}


# ── Strommessung (Mess-ESP mit INA226) ───────────────────────────


@app.get("/power/loggers")
def power_loggers():
    """Logger-IDs mit Stromproben (neueste Aktivitaet zuerst)."""
    return {"loggers": get_power_logger_ids()}


@app.post("/power/measurements")
def add_power_measurements_endpoint(payload: dict):
    """
    Nimmt Strom-Rohproben des Mess-ESP entgegen.

    Erwartet: {logger_id, timestamps:[...], currents_ma:[...], bus_v?, shunt_mv?,
               range_ma?, seq?}. Vorzeichen: Entladen positiv, Laden negativ.
    Der absolute Messzeitpunkt ist received_at (Serverzeit) minus Alter der Probe,
    wenn 'age_s' mitgesendet wird; sonst Serverzeit.
    """
    logger_id = payload.get("logger_id")
    currents = payload.get("currents_ma")
    if not logger_id or not currents:
        return {"status": "error", "message": "logger_id und currents_ma sind erforderlich."}

    timestamps = payload.get("timestamps")
    received_at = datetime.datetime.now()
    bus_v = payload.get("bus_v")
    shunt_mv = payload.get("shunt_mv")
    range_ma = payload.get("range_ma")
    seq_list = payload.get("seq") or []

    rows = []
    for i, current in enumerate(currents):
        if timestamps and i < len(timestamps):
            measured_at = (received_at + datetime.timedelta(
                seconds=float(timestamps[i]))).isoformat()
        else:
            measured_at = received_at.isoformat()
        rows.append({
            "logger_id": logger_id,
            "measured_at": measured_at,
            "received_at": received_at.isoformat(),
            "current_ma": current,
            "bus_v": bus_v[i] if isinstance(bus_v, list) and i < len(bus_v) else bus_v,
            "shunt_mv": shunt_mv[i] if isinstance(shunt_mv, list) and i < len(shunt_mv) else shunt_mv,
            "range_ma": range_ma,
            "seq": seq_list[i] if i < len(seq_list) else None,
        })
    inserted = add_power_measurements(rows)
    return {"status": "success", "inserted": inserted}


@app.post("/power/aggregate")
def add_power_aggregate_endpoint(payload: dict):
    """
    Nimmt ein Aggregat (Live-Anzeige) entgegen.

    Erwartet: {logger_id, window_start, window_end, samples, current_ma_mean?,
               current_ma_min?, current_ma_max?, charge_uah?, gap?}
    charge_uah wird im Mess-ESP durch INTEGRATION der Rohproben berechnet; hier
    wird es nur gespeichert (Mittelwerte ueber uA/mA waeren bedeutungslos).
    """
    logger_id = payload.get("logger_id")
    if not logger_id or not payload.get("window_start") or not payload.get("window_end"):
        return {"status": "error", "message": "logger_id, window_start und window_end sind erforderlich."}
    add_power_aggregate({
        "logger_id": logger_id,
        "window_start": payload["window_start"],
        "window_end": payload["window_end"],
        "samples": payload.get("samples", 0),
        "current_ma_mean": payload.get("current_ma_mean"),
        "current_ma_min": payload.get("current_ma_min"),
        "current_ma_max": payload.get("current_ma_max"),
        "charge_uah": payload.get("charge_uah"),
        "gap": payload.get("gap", False),
        "received_at": datetime.datetime.now().isoformat(),
    })
    return {"status": "success"}


@app.get("/power/{logger_id}")
def power_history(logger_id: str, since: str | None = None, limit: int | None = None):
    """Aggregate (Live) und Rohproben eines Loggers."""
    return {
        "logger_id": logger_id,
        "aggregates": get_power_aggregates(logger_id, since=since),
        "measurements": get_power_measurements(logger_id, since=since, limit=limit),
    }


@app.get("/power/cycles/{logger_id}")
def power_cycles_endpoint(
    logger_id: str,
    since: str | None = None,
    limit: int | None = None,
    peak_trigger_ma: float | None = None,
    pre_peak_s: float | None = None,
    refractory_s: float | None = None,
    spike_end_ma: float | None = None,
    max_cycle_s: float | None = None,
    gap_s: float | None = None,
):
    """
    Zyklusanalyse: Segmentierung + Landmarks + Phasen-Dauern je Zyklus.

    Rohdaten bleiben unberuehrt; die Erkennung ist schwellenbasiert und kann
    per Query-Parameter angepasst werden (Defaults siehe power_cycles.py).
    """
    measurements = get_power_measurements(logger_id, since=since, limit=limit)
    kwargs = _power_cycle_kwargs(peak_trigger_ma, pre_peak_s, refractory_s,
                                 spike_end_ma, max_cycle_s, gap_s)
    return power_cycles.analyze_cycles(measurements, **kwargs)


@app.get("/power/sessions/{logger_id}")
def power_sessions_endpoint(
    logger_id: str,
    since: str | None = None,
    limit: int | None = None,
    peak_trigger_ma: float | None = None,
    pre_peak_s: float | None = None,
    refractory_s: float | None = None,
    spike_end_ma: float | None = None,
    max_cycle_s: float | None = None,
    gap_s: float | None = None,
):
    """
    Firmware-Versionsvergleich: Zyklen mit Session-Zuordnung + Zusammenfassung.

    Jeder Zyklus bekommt die Temperatursession zugewiesen, die zum Zyklusstart
    aktiv war (spaeteste Session mit start_time <= Zyklusstart). Die
    Zusammenfassung zeigt pro Session den Gesamt-/Median-Verbrauch pro Zyklus
    (charge_uah) und die Median-Dauer - damit lassen sich zwei Firmware-
    Versionen direkt vergleichen (siehe power_cycles.analyze_cycles_by_session).
    """
    measurements = get_power_measurements(logger_id, since=since, limit=limit)
    sessions = db.get_sessions_chronological()
    kwargs = _power_cycle_kwargs(peak_trigger_ma, pre_peak_s, refractory_s,
                                 spike_end_ma, max_cycle_s, gap_s)
    return power_cycles.analyze_cycles_by_session(measurements, sessions, **kwargs)


@app.get("/power/cycles/{logger_id}/overlay")
def power_cycles_overlay_endpoint(
    logger_id: str,
    since: str | None = None,
    limit: int | None = None,
    overlay_limit: int = 20,
    window_start_s: float | None = None,
    window_end_s: float | None = None,
    max_points_per_cycle: int = 500,
    peak_trigger_ma: float | None = None,
    pre_peak_s: float | None = None,
    refractory_s: float | None = None,
    spike_end_ma: float | None = None,
    max_cycle_s: float | None = None,
    gap_s: float | None = None,
):
    """
    Oszilloskop-Overlay: die letzten `overlay_limit` Zyklen, alle auf t=0
    (Zyklusanfang) synchronisiert, inkl. Landmarks und Phasen.

    Performance: Rohproben werden per LTTB auf `max_points_per_cycle` Punkte
    pro Zyklus reduziert (Default 500 statt ~10.000). Beim Reinzoomen ruft
    das Frontend `window_start_s`/`window_end_s` (Sekunden seit Zyklusanfang)
    auf, um das Zeitfenster in voller Aufloesung nachzuladen.
    """
    # Wichtig: fuer die Overlay-Ansicht die VOLLSTAENDIGEN Rohdaten nehmen,
    # damit die letzten Zyklen nicht durch ein kleines Daten-Limit beschnitten
    # werden (limit ist hier bewusst nur ein Analyse-Deckel).
    kwargs = _power_cycle_kwargs(peak_trigger_ma, pre_peak_s, refractory_s,
                                 spike_end_ma, max_cycle_s, gap_s)
    analysis, samples = _get_cached_power_analysis(
        logger_id, since=since, limit=limit, kwargs=kwargs)
    return {
        "cycles": power_cycles._render_overlay_cycles(
            analysis, samples, limit=overlay_limit,
            window_start_s=window_start_s, window_end_s=window_end_s,
            max_points_per_cycle=max_points_per_cycle,
        ),
        "stats": analysis["stats"],
    }


# --- Overlay-Analyse-Cache ---------------------------------------------------
#
# Das Zoom-Nachladen soll im ms-Bereich antworten. Der teure Teil ist NICHT
# das Rendern des Fensters (Bisect + LTTB, wenige ms), sondern die Voll-
# analyse ueber ~500k Rohproben. Diese wird pro (logger, since, limit,
# Schwellwerte) gecacht; die Gueltigkeit wird ueber eine einzige guenstige
# SQL-Max-Abfrage geprueft (neueste Probe + Anzahl aendern sich bei neuen
# Daten). TTL als zweite Schranke gegen sehr haeufige inserts.
_OVERLAY_CACHE_TTL_S = 5.0
_overlay_cache: dict = {}
_overlay_cache_lock = threading.Lock()


def _power_cache_key(logger_id: str, since: str | None, limit: int | None,
                     kwargs: dict) -> tuple:
    return (logger_id, since, limit,
            tuple(sorted((k, v) for k, v in kwargs.items())))


def _power_fingerprint(logger_id: str) -> tuple:
    """Guenstige Gueltigkeits-Pruefung: neueste Probe + Zeilenanzahl."""
    conn = sqlite3.connect(db.DB_PATH, timeout=db.DB_TIMEOUT_S)
    try:
        row = conn.execute(
            "SELECT MAX(measured_at), COUNT(*) FROM power_measurements "
            "WHERE logger_id = ?", (logger_id,)
        ).fetchone()
        return (row[0], row[1])
    finally:
        conn.close()


def _get_cached_power_analysis(
    logger_id: str,
    since: str | None,
    limit: int | None,
    kwargs: dict,
) -> tuple[dict, list]:
    """
    Vollanalyse + Rohproben fuer den Overlay-Endpoint, gecacht.

    Bei Cache-Hit entfaellt sowohl das Laden der Rohproben als auch die
    Vollanalyse - das Zoom-Nachladen rendert dann nur noch das Fenster
    (ms-Bereich).
    """
    key = _power_cache_key(logger_id, since, limit, kwargs)
    now = time.monotonic()
    with _overlay_cache_lock:
        entry = _overlay_cache.get(key)
        if entry is not None and now - entry["ts"] < _OVERLAY_CACHE_TTL_S:
            cached_fp = entry["fingerprint"]
        else:
            cached_fp = None

    if cached_fp is not None:
        if _power_fingerprint(logger_id) == cached_fp:
            with _overlay_cache_lock:
                entry["ts"] = now  # TTL verlaengern
            return entry["analysis"], entry["samples"]
        # Daten haben sich geaendert -> neu berechnen.
        with _overlay_cache_lock:
            _overlay_cache.pop(key, None)

    measurements = get_power_measurements(logger_id, since=since, limit=limit)
    samples = power_cycles._prepare_samples(measurements)
    t_base_iso = None
    for m in measurements:
        t_base_iso = power_cycles._parse_iso(m.get("measured_at"))
        if t_base_iso is not None:
            break
    analysis = power_cycles.analyze_cycles_from_samples(
        samples, t_base_iso=t_base_iso.isoformat() if t_base_iso else None, **kwargs)
    with _overlay_cache_lock:
        # Cache begrenzen (verschiedene logger/Schwellwert-Kombis).
        if len(_overlay_cache) >= 8:
            _overlay_cache.clear()
        _overlay_cache[key] = {
            "ts": now,
            "fingerprint": _power_fingerprint(logger_id),
            "analysis": analysis,
            "samples": samples,
        }
    return analysis, samples
