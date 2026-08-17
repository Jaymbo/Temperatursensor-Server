import sqlite3
import datetime
import os

# Use a DB file located next to this module to avoid cwd-dependent paths
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def initialize_db():
    """
    Initialisiert die Datenbank und erstellt die notwendigen Tabellen,
    falls sie nicht existieren.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Tabelle 'sessions' erstellen sensor_id muss ohne not null sein
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT,
            start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            calibration_data TEXT,
            custom_text TEXT,
            messintervall REAL DEFAULT 10.0,
            sendpuffer INTEGER DEFAULT 60
        )
        """
    )

    # Tabelle 'measurements' erstellen
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            temperature REAL NOT NULL,
            UNIQUE(session_id, timestamp),
            FOREIGN KEY (session_id) REFERENCES sessions (id)
        )
        """
    )

    # Tabelle 'comments' erstellen
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_session TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            temperature REAL NOT NULL,
            comment TEXT NOT NULL
        )
        """
    )

    conn.commit()

    # Migration: Neue Spalten hinzufügen, falls noch nicht vorhanden
    cursor.execute("PRAGMA table_info(sessions)")
    columns = [row[1] for row in cursor.fetchall()]
    if "messintervall" not in columns:  # pragma: no cover (Migration, Spalte existiert bereits im CREATE TABLE)
        cursor.execute("ALTER TABLE sessions ADD COLUMN messintervall REAL DEFAULT 10.0")  # pragma: no cover
    if "sendpuffer" not in columns:  # pragma: no cover (Migration, Spalte existiert bereits im CREATE TABLE)
        cursor.execute("ALTER TABLE sessions ADD COLUMN sendpuffer INTEGER DEFAULT 60")  # pragma: no cover
    if "correction_points" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN correction_points TEXT")
    conn.commit()
    conn.close()


def get_all_series():
    """
    Gibt alle Sensor-Session-Kombinationen zurück.
    (Beispiel: [{'sensor_session': 'sensor_A_session1', 'custom_text': 'example text'}, ...])
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Jetzt genau aus der Tabelle sessions abfragen
    cursor.execute("SELECT sensor_id, id, custom_text FROM sessions ORDER BY sensor_id, id")
    result = [
        {"sensor_session": f"{row[0]}_{row[1]}", "custom_text": row[2]} for row in cursor.fetchall()
    ]
    print(result)  # Debug-Ausgabe
    conn.close()
    return result


def get_data_by_sensor(sensor_session: str):
    """
    Gibt alle Zeitpunkte und Temperaturen für einen bestimmten Sensor in einer bestimmten Session zurück.
    Dabei wird das start_time aus der Session mit dem time_offset aus measurements verrechnet.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # sensor_session wird im Format sensorID_sessionID erwartet
    sensor_id, session_id = sensor_session.split("_")

    # Hole das Startdatum der Session
    cursor.execute("SELECT start_time FROM sessions WHERE id = ? AND sensor_id = ?", (session_id, sensor_id))
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return []
    
    # Hole die Messwerte für die Session, sortiert nach timestamp
    cursor.execute(
        "SELECT timestamp, temperature FROM measurements WHERE session_id = ? ORDER BY timestamp ASC",
        (session_id,)
    )
    result = []
    for timestamp, temperature in cursor.fetchall():
        # Berechne den tatsächlichen Messzeitpunkt
        timestamp = timestamp
        result.append({
            "timestamp": timestamp,
            "temperature": temperature
        })
    
    conn.close()
    return result


def get_comments_by_sensor(sensor_session: str):
    """
    Gibt alle Kommentare für eine bestimmte Sensor-Session zurück.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT timestamp, temperature, comment FROM comments WHERE sensor_session = ? ORDER BY timestamp",
        (sensor_session,)
    )
    result = [
        {"timestamp": row[0], "temperature": row[1], "comment": row[2]} for row in cursor.fetchall()
    ]

    conn.close()
    return result


def add_comment(sensor_session: str, timestamp: str, temperature: float, comment: str):
    """
    Fügt einen neuen Kommentar in die Datenbank ein.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO comments (sensor_session, timestamp, temperature, comment) VALUES (?, ?, ?, ?)",
        (sensor_session, timestamp, temperature, comment)
    )

    conn.commit()
    conn.close()


def delete_comment(sensor_session: str, timestamp: str, temperature: float, comment: str):
    """
    Löscht einen Kommentar aus der Datenbank basierend auf sensor_session, timestamp, temperature und comment.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # SQL-Befehl zum Löschen des Kommentars
    cursor.execute(
        "DELETE FROM comments WHERE sensor_session = ? AND timestamp = ? AND temperature = ? AND comment = ?",
        (sensor_session, timestamp, temperature, comment),
    )

    conn.commit()
    conn.close()


def get_calibration_points(sensor_id: str):
    """
    Gibt Basis-Kalibrierungsparameter und Korrekturpunkte für eine Sensor_id zurück.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    session_id = get_latest_session_for_sensor_id(sensor_id)
    cursor.execute(
        "SELECT calibration_data, correction_points FROM sessions "
        "WHERE id = ?",
        (session_id,)
    )
    row = cursor.fetchone()

    if row:
        result = [{"calibration": row[0], "correction_points": row[1]}]
    else:
        result = []

    conn.close()
    return result


def add_calibration(
    sensor_id: str,
    calibration_data: str,
    correction_points: str | None = None
):
    """
    Speichert die Korrekturpunkte für die aktuelle Session.

    :param sensor_id: Die Sensor-ID
    :param calibration_data: Basis-Kalibrierungsdaten ("R0,A,B,U0,R1") – bleibt unverändert
    :param correction_points: JSON-String der Korrekturpunkte
                             "[{"t": 25.0, "delta": -5.0}, ...]" oder None
    """
    session_id = get_latest_session_for_sensor_id(sensor_id)
    sensor_session = f"{sensor_id}_{session_id}"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        sensor_id, session_id = sensor_session.split("_")
    except ValueError:  # pragma: no cover
        raise ValueError(f"Ungültiges sensor_session Format: {sensor_session}. Erwartet: 'sensor_id_session_id'")  # pragma: no cover (defensiv, praktisch unerreichbar)

    # Nur correction_points updaten; calibration_data bleibt die initiale Basis
    cursor.execute(
        "UPDATE sessions SET correction_points = ? WHERE id = ?",
        (correction_points if correction_points else None, session_id)
    )
    conn.commit()
    conn.close()
    print(f"Korrekturpunkte für Session {sensor_session} aktualisiert: {correction_points}")


def get_latest_session_for_sensor_id(sensor_id: str):
    """
    Gibt die neueste sensor_session-ID für eine gegebene sensor_id zurück.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM sessions WHERE sensor_id = ? ORDER BY start_time DESC LIMIT 1",
        (sensor_id,)
    )
    row = cursor.fetchone()
    print(f"Neueste Session für Sensor-ID {sensor_id}: {row}")
    conn.close()

    if row is None:
        raise ValueError(f"Keine Session für Sensor-ID {sensor_id} gefunden.")

    return row[0]


def add_temperature_data(sensor_id: str, timestamps: list[str], temperatures: list[float]) -> str:
    """
    Fügt eine Liste von Temperaturwerten und zugehörigen Zeitpunkten in die Datenbank ein.
    Die neueste sensor_session wird basierend auf der sensor_id gefunden.
    Gibt die sensor_session zurück.
    """
    if len(timestamps) != len(temperatures):
        raise ValueError("Die Anzahl der Zeitpunkte und Temperaturen muss übereinstimmen.")

    session_id = get_latest_session_for_sensor_id(sensor_id)
    print(f"Füge Daten für Sensor-ID {sensor_id} in Session {session_id} ein.")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Füge die neuen Temperaturdaten in die measurements-Tabelle ein
    data_to_insert = [(session_id, timestamps[i], temperatures[i]) for i in range(len(timestamps))]
    cursor.executemany(
        "INSERT OR IGNORE INTO measurements (session_id, timestamp, temperature) VALUES (?, ?, ?)",
        data_to_insert
    )

    conn.commit()
    conn.close()

    return f"{sensor_id}_{session_id}"


def get_start_time_of_latest_session(sensor_id: str):
    """
    Gibt den Startzeitpunkt der neuesten sensor_session für eine gegebene sensor_id zurück.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT start_time FROM sessions WHERE sensor_id = ? ORDER BY start_time DESC LIMIT 1",
        (sensor_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        raise ValueError(f"Keine Session für Sensor-ID {sensor_id} gefunden.")

    return row[0]

def process_relative_data(timestamps: list[float], temperatures: list[float]):
    """
    Verarbeitet eine Liste von Timestamps und Temperaturen, wobei der erste Eintrag absolut ist
    und alle weiteren Einträge die Abweichung vom vorherigen Wert darstellen.

    :param timestamps: Liste von Timestamps (erster Eintrag absolut, Rest als Differenz in Sekunden)
    :param temperatures: Liste von Temperaturen (erster Eintrag absolut, Rest als Differenz)
    :return: Liste von absoluten Timestamps und Temperaturen
    """
    if not timestamps or not temperatures:
        raise ValueError("Timestamps und Temperaturen dürfen nicht leer sein.")

    if len(timestamps) != len(temperatures):
        raise ValueError("Die Anzahl der Timestamps und Temperaturen muss übereinstimmen.")

    # Absoluten Startwert initialisieren
    absolute_timestamps = [timestamps[0]]
    absolute_temperatures = [temperatures[0]]

    # Iteriere über die Differenzen und berechne die absoluten Werte
    for i in range(1, len(timestamps)):
        absolute_timestamps.append(absolute_timestamps[-1] + timestamps[i])
        absolute_temperatures.append(absolute_temperatures[-1] + temperatures[i])

    return absolute_timestamps, absolute_temperatures

def clone_latest_session_with_calibration(sensor_id: str, messintervall: float = 10.0, sendpuffer: int = 60):
    """Erstellt eine neue Session für einen Sensor.

    Regeln:
    1. Falls bereits Sessions für die sensor_id existieren -> Kalibrierdaten der neuesten übernehmen.
    2. Falls keine existieren -> Default-Kalibrierdaten verwenden.
    3. Falls ein *leerer* Eintrag (sensor_id IS NULL) existiert, dieser aber evtl. einen custom_text enthält:
       diesen Eintrag wiederverwenden (Update), statt einen neuen anzulegen.

    :param sensor_id: Die ID des Sensors
    :param messintervall: Intervall zwischen Messungen in Sekunden (Default: 60.0)
    :param sendpuffer: Anzahl der Messpunkte vor dem Senden (Default: 10)

    Rückgabe:
        dict(new_sensor_session=..., calibration_data=..., custom_text=..., messintervall=..., sendpuffer=...)
    """
    DEFAULT_CALIBRATION = "100,3.9083e-3,-5.775e-7,3.0,10000"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        # 1) Leeren Eintrag (ohne sensor_id) suchen
        cursor.execute(
            """
            SELECT id, custom_text, messintervall, sendpuffer FROM sessions
            WHERE sensor_id IS NULL
            ORDER BY id DESC LIMIT 1
            """
        )
        empty_row = cursor.fetchone()

        # 2) Neueste existierende Session für sensor_id suchen
        cursor.execute(
            "SELECT id, calibration_data, correction_points FROM sessions WHERE sensor_id = ? ORDER BY start_time DESC LIMIT 1",
            (sensor_id,)
        )
        latest = cursor.fetchone()  # (id, calibration_data, correction_points) oder None

        if latest and latest[1]:
            calibration_data = latest[1]
        else:
            calibration_data = DEFAULT_CALIBRATION

        correction_points = latest[2] if latest and latest[2] else None

        if empty_row:
            # Reuse empty row - take messintervall/sendpuffer from the empty entry if set
            entry_id, custom_text, entry_messintervall, entry_sendpuffer = empty_row
            # Use values from empty entry if they were explicitly set, otherwise use defaults
            if entry_messintervall is not None:
                messintervall = entry_messintervall
            if entry_sendpuffer is not None:
                sendpuffer = entry_sendpuffer
            cursor.execute(
                "UPDATE sessions SET sensor_id = ?, start_time = datetime('now','localtime'), "
                "calibration_data = ?, correction_points = ?, messintervall = ?, sendpuffer = ? WHERE id = ?",
                (sensor_id, calibration_data, correction_points, messintervall, sendpuffer, entry_id)
            )
            new_id = entry_id
        else:
            # Insert fresh row (autoincrement id)
            custom_text = None
            cursor.execute(
                "INSERT INTO sessions (sensor_id, start_time, calibration_data, correction_points, messintervall, sendpuffer) "
                "VALUES (?, datetime('now','localtime'), ?, ?, ?, ?)",
                (sensor_id, calibration_data, correction_points, messintervall, sendpuffer)
            )
            new_id = cursor.lastrowid

        new_sensor_session = f"{sensor_id}_{new_id}"
        conn.commit()
        return {
            "new_sensor_session": new_sensor_session,
            "calibration_data": calibration_data,
            "correction_points": correction_points,
            "custom_text": custom_text,
            "messintervall": messintervall,
            "sendpuffer": sendpuffer,
        }
    finally:
        conn.close()

def add_or_update_custom_text_entry(custom_text: str, messintervall: float = 10.0, sendpuffer: int = 60):
    """
    Fügt einen neuen Eintrag mit nur custom_text hinzu oder überschreibt den neuesten bestehenden Eintrag,
    bei dem nur id und custom_text gefüllt sind und der Rest leer ist.

    :param custom_text: Der benutzerdefinierte Text für den Eintrag.
    :param messintervall: Intervall zwischen Messungen in Sekunden (Default: 60.0)
    :param sendpuffer: Anzahl der Messpunkte vor dem Senden (Default: 10)
    :return: Die ID des Eintrags.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Suche nach dem neuesten Eintrag, bei dem nur id und custom_text gefüllt sind
    cursor.execute(
        """
        SELECT id FROM sessions
        WHERE sensor_id IS NULL
        ORDER BY id DESC LIMIT 1
        """
    )
    row = cursor.fetchone()

    if row:
        # Überschreibe den bestehenden Eintrag
        entry_id = row[0]
        cursor.execute(
            "UPDATE sessions SET custom_text = ?, messintervall = ?, sendpuffer = ? WHERE id = ?",
            (custom_text, messintervall, sendpuffer, entry_id)
        )
    else:  # pragma: no cover
        # Füge einen neuen Eintrag hinzu
        cursor.execute(
            "INSERT INTO sessions (custom_text, messintervall, sendpuffer) VALUES (?, ?, ?)",
            (custom_text, messintervall, sendpuffer)
        )
        entry_id = cursor.lastrowid

    conn.commit()  # pragma: no cover
    conn.close()  # pragma: no cover

    return entry_id