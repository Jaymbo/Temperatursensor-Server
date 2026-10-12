import sqlite3
import datetime
import math
import os

# Use a DB file located next to this module to avoid cwd-dependent paths
DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

# SQLite-Zugriff: timeout verhindert "database is locked" bei konkurrierenden
# Schreibern (Strommesser-Firmware streamt regelmaessig /power/*-Endpoints).
# WAL-Modus wird in initialize_db() gesetzt (persistent je DB-Datei): Leser
# blockieren den Schreiber dann nicht mehr und umgekehrt.
DB_TIMEOUT_S = 30.0


def initialize_db():
    """
    Initialisiert die Datenbank und erstellt die notwendigen Tabellen,
    falls sie nicht existieren.
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    # WAL: Leser + Schreiber parallel moeglich (statt Datenbank-Lock bei jedem
    # INSERT aus den Power-Streams). Persistent je DB-Datei, daher einmalig ok.
    cursor.execute("PRAGMA journal_mode=WAL")

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

    # Tabelle 'time_calibration' (Zeit-Kalibrierung pro Sensor)
    # Speichert einen Faktor K pro sensor_id, der beim Anzeigen der Daten auf die
    # Timestamps relativ zum Session-Start angewendet wird. Die Rohdaten in
    # 'measurements' bleiben unveraendert (K kann so jederzeit rechnerisch revidiert
    # werden). Siehe db.apply_time_factor.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS time_calibration (
            sensor_id TEXT PRIMARY KEY,
            factor REAL NOT NULL DEFAULT 1.0
        )
        """
    )

    _create_battery_tables(cursor)
    _create_power_tables(cursor)

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
    if "hw_rev" not in columns:
        # Hardware-Revision der gespeicherten Kalibrierdaten. NULL = Altbestand
        # (vor Einfuehrung dieses Feldes) -> wird NICHT als Konflikt gewertet.
        # Siehe Temperatursensor/config.h (HW_REV) und Wlan.ino (Warn-Logik).
        cursor.execute("ALTER TABLE sessions ADD COLUMN hw_rev INTEGER")
    conn.commit()
    conn.close()


# =============================================================================
# Akku-Telemetrie (Schema)
# =============================================================================
#
# Warum der Akku GETRENNT vom Sensor modelliert wird, obwohl er verloetet ist:
#
#   1. Ein Wechsel ist moeglich (Ausnahme, aber sie kommt vor).
#   2. Logger 1 hatte einen Kurzschluss zwischen Lade+ und Lade- (0 Ohm gemessen,
#      nach Entfernen 16,47 kOhm). Das war ein GERAETEFEHLER, kein Akkufehler.
#      Ohne getrennte Modellierung ist dieser Fall spaeter nicht rekonstruierbar:
#      mit einem reinen sensor_id-Schluessel saehe es aus, als waere der Akku alt.
#
# Die Zuordnung erfolgt daher ueber 'battery_assignments' (Zeitintervall statt
# 1:1-Fremdschluessel). 'battery_readings' haelt jeden empfangenen Messwert
# mit BEIDEN Zeiten (measured_at = Geraetezeit, received_at = Serverzeit).


def _create_battery_tables(cursor):
    """Legt die Akku-Tabellen an (idempotent)."""
    # battery: der physische Akku (Aufkleber/ID), NICHT das Geraet.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS batteries (
            battery_id TEXT PRIMARY KEY,
            capacity_class TEXT,
            purchase_date TEXT,
            status TEXT NOT NULL DEFAULT 'in_use',
            notes TEXT
        )
        """
    )

    # battery_assignments: welcher Akku war wann in welchem Sensor.
    # 'to_time' NULL = aktuell im Einsatz. 'reason' = initial | swap | repair.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS battery_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            battery_id TEXT NOT NULL,
            sensor_id TEXT NOT NULL,
            from_time TEXT NOT NULL,
            to_time TEXT,
            reason TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_batt_assign_sensor "
        "ON battery_assignments (sensor_id, from_time)"
    )

    # battery_readings: jeder empfangene Akku-Messwert.
    #   measured_at = Geraetezeit (Session-Start + Geraete-Elapsed)
    #   received_at = Serverzeit beim Empfang
    # BEIDE Zeiten werden immer gespeichert (bekanntes Zeitdrift-Problem,
    # Faktor ~0.956; Auswertungen duerfen sich nicht auf nur eine stuetzen).
    # 'under_load' markiert Messungen waehrend einer Lastphase (fuer R = dV/dI).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS battery_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT NOT NULL,
            battery_id TEXT,
            measured_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            mv INTEGER NOT NULL,
            raw INTEGER,
            under_load INTEGER,
            seq INTEGER,
            hw_rev INTEGER,
            UNIQUE(sensor_id, measured_at)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_batt_read_sensor "
        "ON battery_readings (sensor_id, measured_at)"
    )

    # battery_sessions: aus den Messwerten ABGELEITETE Betriebsphasen.
    # Typ = measure (Messbetrieb) | charge (Ladung) | outage (Ausfall).
    # Wird von der Server-Auswertung neu erzeugt (siehe battery_health.py),
    # damit die Erkennungslogik jederzeit aenderbar ist, ohne Rohdaten zu
    # verlieren. Heisst bewusst NICHT 'sessions' (das sind Temperatursessions).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS battery_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT NOT NULL,
            battery_id TEXT,
            start_time TEXT NOT NULL,
            end_time TEXT,
            type TEXT NOT NULL,
            note TEXT
        )"""
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_batt_sess_sensor "
        "ON battery_sessions (sensor_id, start_time)"
    )


# =============================================================================
# Strommessung (Mess-ESP mit INA226, Schema)
# =============================================================================
#
# Die Strommessung laeuft auf einem SEPARATEN ESP (nicht dem Logger), damit der
# Messaufbau die Temperaturmessung nicht beeinflusst. Der Mess-ESP sendet Strom,
# nicht Temperatur. 'logger_id' benennt den gemessenen Logger.
#
# Vorzeichen: Entladen = positiv, Laden = negativ (siehe Firmware).


def _create_power_tables(cursor):
    """Legt die Tabellen der Strommessung an (idempotent)."""
    # power_measurements: Rohproben bzw. Live-Werte (1 Hz) des Mess-ESP.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS power_measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logger_id TEXT NOT NULL,
            measured_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            current_ma REAL NOT NULL,
            bus_v REAL,
            shunt_mv REAL,
            range_ma REAL,
            seq INTEGER,
            UNIQUE(logger_id, measured_at, range_ma)
        )"""
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_power_meas_logger "
        "ON power_measurements (logger_id, measured_at)"
    )

    # power_aggregates: Aggregate fuer die Live-Anzeige. Ladung wird durch
    # INTEGRATION der Rohproben berechnet (charge_uah), NICHT durch Mittelung
    # der Stroeme - der Mittelwert ueber uA-Schlaf und mA-Peak ist bedeutungslos.
    # 'gap' markiert eine Sendeluecke (Messung pausiert, WLAN an) NICHT kaschiert.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS power_aggregates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logger_id TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            samples INTEGER NOT NULL,
            current_ma_mean REAL,
            current_ma_min REAL,
            current_ma_max REAL,
            charge_uah REAL,
            gap INTEGER DEFAULT 0,
            received_at TEXT
        )"""
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_power_agg_logger "
        "ON power_aggregates (logger_id, window_start)"
    )


def _is_valid_battery_mv(mv) -> bool:
    """Plausibilitaetsgrenzen fuer Akku-Millivolt (siehe Firmware config.h)."""
    try:
        mv = int(mv)
    except (TypeError, ValueError):
        return False
    return 2500 <= mv <= 4500


def get_all_series():
    """
    Gibt alle Sensor-Session-Kombinationen zurück.
    (Beispiel: [{'sensor_session': 'sensor_A_session1', 'custom_text': 'example text'}, ...])
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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

    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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
            "SELECT id, calibration_data, correction_points, hw_rev FROM sessions WHERE sensor_id = ? ORDER BY start_time DESC LIMIT 1",
            (sensor_id,)
        )
        latest = cursor.fetchone()  # (id, calibration_data, correction_points, hw_rev) oder None

        if latest and latest[1]:
            calibration_data = latest[1]
        else:
            calibration_data = DEFAULT_CALIBRATION

        correction_points = latest[2] if latest and latest[2] else None
        # Hardware-Revision der Kalibrierdaten in die neue Session uebernehmen.
        # Wichtig: Nach einem HW_REV-Wechsel bleibt so die Warnlogik in der
        # Firmware aktiv (siehe Wlan.ino), statt die Revision zu verlieren.
        hw_rev = latest[3] if latest else None

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
                "calibration_data = ?, correction_points = ?, messintervall = ?, sendpuffer = ?, "
                "hw_rev = ? WHERE id = ?",
                (sensor_id, calibration_data, correction_points, messintervall, sendpuffer, hw_rev, entry_id)
            )
            new_id = entry_id
        else:
            # Insert fresh row (autoincrement id)
            custom_text = None
            cursor.execute(
                "INSERT INTO sessions (sensor_id, start_time, calibration_data, correction_points, messintervall, sendpuffer, hw_rev) "
                "VALUES (?, datetime('now','localtime'), ?, ?, ?, ?, ?)",
                (sensor_id, calibration_data, correction_points, messintervall, sendpuffer, hw_rev)
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

def get_time_factor(sensor_id: str) -> float:
    """
    Gibt den gespeicherten Zeit-Kalibrierungs-Faktor K fuer eine sensor_id zurueck.

    K = 1.0 bedeutet "keine Korrektur". K > 1 streckt die Zeit (Uhr lief zu langsam),
    K < 1 staucht die Zeit (Uhr lief zu schnell). Fehlt noch keine Kalibrierung,
    wird 1.0 zurueckgegeben.
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute("SELECT factor FROM time_calibration WHERE sensor_id = ?", (sensor_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return 1.0
    return float(row[0]) if row[0] is not None else 1.0


def set_time_factor(sensor_id: str, factor: float) -> None:
    """
    Speichert (oder ueberschreibt) den Zeit-Kalibrierungs-Faktor K fuer eine sensor_id.
    """
    if not sensor_id:
        raise ValueError("sensor_id darf nicht leer sein.")
    if not (isinstance(factor, (int, float)) and factor > 0.0 and math.isfinite(float(factor))):
        raise ValueError("factor muss eine positive, endliche Zahl sein.")
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO time_calibration (sensor_id, factor) VALUES (?, ?)",
        (sensor_id, float(factor)),
    )
    conn.commit()
    conn.close()
    print(f"Zeit-Kalibrierungsfaktor fuer Sensor {sensor_id} aktualisiert: {factor}")


def get_session_start_time(sensor_session: str) -> str | None:
    """
    Gibt den Startzeitpunkt (ISO-String) einer bestimmten sensor_session zurueck
    oder None, falls die Session nicht existiert.

    Erwartet sensor_session im Format 'sensor_id_session_id'.
    """
    try:
        sensor_id, session_id = sensor_session.split("_")
    except ValueError:
        return None
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT start_time FROM sessions WHERE id = ? AND sensor_id = ?",
        (session_id, sensor_id),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def apply_time_factor(timestamp: str, start_time: str | None, factor: float) -> str:
    """
    Wendet den Zeit-Kalibrierungs-Faktor K auf einen absoluten Timestamp an.

    Berechnung (fixer Startpunkt):
        korrigiert = start + (gemessen - start) * K

    Damit bleibt der Session-Start immer exakt unveraendert und nur der Abstand
    zum Start wird gestreckt (K>1) bzw. gestaucht (K<1).

    :param timestamp: ISO-String des gemessenen (gespeicherten) Zeitpunkts
    :param start_time: ISO-String des Session-Starts (Kann None sein -> kein Faktor)
    :param factor: Der Kalibrierungs-Faktor K (1.0 = keine Korrektur)
    :return: ISO-String des korrigierten Zeitpunkts (bei Fehler -> Original)
    """
    if factor == 1.0 or start_time is None or timestamp is None:
        return timestamp
    try:
        ts = datetime.datetime.fromisoformat(timestamp)
        start = datetime.datetime.fromisoformat(start_time)
    except (ValueError, TypeError):
        return timestamp
    # Nur den Abstand zum Start skalieren -> Startpunkt bleibt fix
    delta = ts - start
    corrected = start + delta * factor
    return corrected.isoformat()


def add_or_update_custom_text_entry(custom_text: str, messintervall: float = 10.0, sendpuffer: int = 60):
    """
    Fügt einen neuen Eintrag mit nur custom_text hinzu oder überschreibt den neuesten bestehenden Eintrag,
    bei dem nur id und custom_text gefüllt sind und der Rest leer ist.

    :param custom_text: Der benutzerdefinierte Text für den Eintrag.
    :param messintervall: Intervall zwischen Messungen in Sekunden (Default: 60.0)
    :param sendpuffer: Anzahl der Messpunkte vor dem Senden (Default: 10)
    :return: Die ID des Eintrags.
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
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

# =============================================================================
# Akku-Telemetrie (Zugriffsfunktionen)
# =============================================================================


def get_session_hw_rev(sensor_id: str):
    """
    Gibt die Hardware-Revision der neuesten Session eines Sensors zurueck
    oder None, wenn keine/keine bekannte Revision gespeichert ist.

    None bedeutet "Altbestand" und darf NICHT als Konflikt gewertet werden.
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT hw_rev FROM sessions WHERE sensor_id = ? "
        "ORDER BY start_time DESC LIMIT 1",
        (sensor_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def set_session_hw_rev(sensor_id: str, hw_rev: int) -> None:
    """
    Setzt die Hardware-Revision der NEUESTEN Session eines Sensors.

    Wird beim ersten /measurements-Paket mit hw_rev aufgerufen. Da die Revision
    nur beim Flashen wechselt, ist das Setzen auf die neueste Session korrekt
    (eine laufende Session hat genau eine Revision).
    """
    session_id = get_latest_session_for_sensor_id(sensor_id)
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sessions SET hw_rev = ? WHERE id = ?",
        (int(hw_rev), session_id),
    )
    conn.commit()
    conn.close()


def get_battery_assignment(sensor_id: str, at_time: str | None = None):
    """
    Gibt die zum Zeitpunkt gueltige Akku-Zuordnung zurueck:
    {"battery_id": ..., "from_time": ..., "to_time": ...} oder None.

    :param at_time: ISO-Zeit; None = aktuell (to_time IS NULL).
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    if at_time is None:
        cursor.execute(
            "SELECT battery_id, from_time, to_time FROM battery_assignments "
            "WHERE sensor_id = ? AND to_time IS NULL "
            "ORDER BY from_time DESC LIMIT 1",
            (sensor_id,),
        )
    else:
        cursor.execute(
            "SELECT battery_id, from_time, to_time FROM battery_assignments "
            "WHERE sensor_id = ? AND from_time <= ? "
            "AND (to_time IS NULL OR to_time >= ?) "
            "ORDER BY from_time DESC LIMIT 1",
            (sensor_id, at_time, at_time),
        )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {"battery_id": row[0], "from_time": row[1], "to_time": row[2]}


def add_battery_assignment(
    sensor_id: str,
    battery_id: str,
    from_time: str,
    to_time: str | None = None,
    reason: str | None = None,
):
    """
    Legt eine Akku-Zuordnung an bzw. schliesst sie (Wechsel).

    Ein Wechsel wird als Ereignis protokolliert: die offene Zuordnung bekommt
    ein to_time, die neue startet. Wird ueber POST /battery/assign ausgeloest.
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO battery_assignments (battery_id, sensor_id, from_time, to_time, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (battery_id, sensor_id, from_time, to_time, reason),
    )
    conn.commit()
    conn.close()


def close_open_battery_assignment(sensor_id: str, to_time: str) -> None:
    """Schliesst die offene Zuordnung eines Sensors (Wechsel/Reparatur)."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE battery_assignments SET to_time = ? "
        "WHERE sensor_id = ? AND to_time IS NULL",
        (to_time, sensor_id),
    )
    conn.commit()
    conn.close()


def add_battery_readings(rows: list[dict]) -> int:
    """
    Fuegt mehrere Akku-Messwerte ein (INSERT OR IGNORE -> Duplikate verworfen).

    Jede Zeile: sensor_id, battery_id, measured_at, received_at, mv,
                raw(opt), under_load(opt), seq(opt), hw_rev(opt)

    Unplausible mv werden verworfen (siehe _is_valid_battery_mv), damit der
    Flottenvergleich nicht durch ADC-Ausreisser verfaelscht wird.

    :return: Anzahl der eingefuegten Zeilen
    """
    data = []
    for r in rows:
        mv = r.get("mv")
        if not _is_valid_battery_mv(mv):
            continue
        data.append((
            r["sensor_id"], r.get("battery_id"), r["measured_at"], r["received_at"],
            int(mv), r.get("raw"), r.get("under_load"), r.get("seq"), r.get("hw_rev"),
        ))
    if not data:
        return 0
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT OR IGNORE INTO battery_readings "
        "(sensor_id, battery_id, measured_at, received_at, mv, raw, under_load, seq, hw_rev) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        data,
    )
    inserted = cursor.rowcount
    conn.commit()
    conn.close()
    return inserted


def get_battery_readings(sensor_id: str, limit: int | None = None,
                         since: str | None = None) -> list[dict]:
    """
    Gibt die Akku-Messwerte eines Sensors chronologisch zurueck.

    :param limit: maximale Anzahl (None = alle)
    :param since: nur Messwerte ab diesem ISO-Zeitpunkt
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    sql = ("SELECT measured_at, received_at, mv, raw, under_load, seq, hw_rev, battery_id "
           "FROM battery_readings WHERE sensor_id = ?")
    params: list = [sensor_id]
    if since is not None:
        sql += " AND measured_at >= ?"
        params.append(since)
    sql += " ORDER BY measured_at ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "measured_at": r[0], "received_at": r[1], "mv": r[2], "raw": r[3],
            "under_load": bool(r[4]) if r[4] is not None else None,
            "seq": r[5], "hw_rev": r[6], "battery_id": r[7],
        }
        for r in rows
    ]


def get_latest_battery_reading(sensor_id: str):
    """Letzter Akku-Messwert eines Sensors oder None."""
    readings = get_battery_readings(sensor_id)
    return readings[-1] if readings else None


def get_battery_sensor_ids() -> list[str]:
    """Alle Sensor-IDs, fuer die Akku-Messwerte vorliegen."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT sensor_id FROM battery_readings ORDER BY sensor_id")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_all_battery_readings(since: str | None = None, limit_per_sensor: int | None = None) -> list[dict]:
    """
    Alle Akku-Messwerte aller Sensoren (fuer den Flottenvergleich).

    :param limit_per_sensor: nur die letzten N Werte je Sensor
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT sensor_id FROM battery_readings")
    sensor_ids = [r[0] for r in cursor.fetchall()]
    conn.close()
    out: list[dict] = []
    for sid in sensor_ids:
        readings = get_battery_readings(sid, since=since)
        if limit_per_sensor is not None:
            readings = readings[-int(limit_per_sensor):]
        for r in readings:
            r["sensor_id"] = sid
            out.append(r)
    return out


def upsert_battery(battery_id: str, capacity_class: str | None = None,
                   purchase_date: str | None = None, status: str = "in_use",
                   notes: str | None = None) -> None:
    """Legt einen Akku an bzw. aktualisiert seine Stammdaten."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO batteries (battery_id, capacity_class, purchase_date, status, notes) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(battery_id) DO UPDATE SET "
        "capacity_class=excluded.capacity_class, purchase_date=excluded.purchase_date, "
        "status=excluded.status, notes=excluded.notes",
        (battery_id, capacity_class, purchase_date, status, notes),
    )
    conn.commit()
    conn.close()


def get_batteries() -> list[dict]:
    """Alle Akkus (Stammdaten) zurueckgeben."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT battery_id, capacity_class, purchase_date, status, notes FROM batteries "
        "ORDER BY battery_id"
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "battery_id": r[0], "capacity_class": r[1], "purchase_date": r[2],
            "status": r[3], "notes": r[4],
        }
        for r in rows
    ]


def replace_battery_sessions(sensor_id: str, sessions: list[dict]) -> None:
    """
    Ersetzt die abgeleiteten Betriebsphasen (measure/charge/outage) eines Sensors.

    Die Erkennung liegt in battery_health.py; hier wird nur gespeichert. Ein
    erneuter Lauf erzeugt die Phasen neu (Rohdaten bleiben unberuehrt).
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM battery_sessions WHERE sensor_id = ?", (sensor_id,))
    cursor.executemany(
        "INSERT INTO battery_sessions (sensor_id, battery_id, start_time, end_time, type, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (sensor_id, s.get("battery_id"), s["start_time"], s.get("end_time"),
             s["type"], s.get("note"))
            for s in sessions
        ],
    )
    conn.commit()
    conn.close()


def get_battery_sessions(sensor_id: str) -> list[dict]:
    """Abgeleitete Betriebsphasen eines Sensors (chronologisch)."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT sensor_id, battery_id, start_time, end_time, type, note "
        "FROM battery_sessions WHERE sensor_id = ? ORDER BY start_time",
        (sensor_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "sensor_id": r[0], "battery_id": r[1], "start_time": r[2],
            "end_time": r[3], "type": r[4], "note": r[5],
        }
        for r in rows
    ]


# =============================================================================
# Strommessung (Zugriffsfunktionen)
# =============================================================================


def add_power_measurements(rows: list[dict]) -> int:
    """
    Fuegt Strom-Rohproben ein (INSERT OR IGNORE -> Duplikate verworfen).

    Jede Zeile: logger_id, measured_at, received_at, current_ma,
                bus_v(opt), shunt_mv(opt), range_ma(opt), seq(opt)
    """
    if not rows:
        return 0
    data = [
        (
            r["logger_id"], r["measured_at"], r["received_at"], float(r["current_ma"]),
            r.get("bus_v"), r.get("shunt_mv"), r.get("range_ma"), r.get("seq"),
        )
        for r in rows
    ]
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT OR IGNORE INTO power_measurements "
        "(logger_id, measured_at, received_at, current_ma, bus_v, shunt_mv, range_ma, seq) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        data,
    )
    inserted = cursor.rowcount
    conn.commit()
    conn.close()
    return inserted


def add_power_aggregate(row: dict) -> None:
    """
    Speichert ein Aggregat (Live-Anzeige). charge_uah wird durch Integration
    der Rohproben berechnet (nicht durch Mittelwertbildung, siehe Schema).
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO power_aggregates "
        "(logger_id, window_start, window_end, samples, current_ma_mean, "
        "current_ma_min, current_ma_max, charge_uah, gap, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["logger_id"], row["window_start"], row["window_end"], row["samples"],
            row.get("current_ma_mean"), row.get("current_ma_min"),
            row.get("current_ma_max"), row.get("charge_uah"),
            1 if row.get("gap") else 0, row.get("received_at"),
        ),
    )
    conn.commit()
    conn.close()


def get_power_aggregates(logger_id: str, since: str | None = None) -> list[dict]:
    """Aggregate eines Loggers (chronologisch), optional ab since."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    sql = ("SELECT window_start, window_end, samples, current_ma_mean, current_ma_min, "
           "current_ma_max, charge_uah, gap FROM power_aggregates WHERE logger_id = ?")
    params: list = [logger_id]
    if since is not None:
        sql += " AND window_start >= ?"
        params.append(since)
    sql += " ORDER BY window_start ASC"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "window_start": r[0], "window_end": r[1], "samples": r[2],
            "current_ma_mean": r[3], "current_ma_min": r[4], "current_ma_max": r[5],
            "charge_uah": r[6], "gap": bool(r[7]) if r[7] is not None else False,
        }
        for r in rows
    ]


def get_power_measurements(logger_id: str, since: str | None = None,
                           limit: int | None = None) -> list[dict]:
    """Rohproben eines Loggers (chronologisch)."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    sql = ("SELECT measured_at, current_ma, bus_v, shunt_mv, range_ma, seq "
           "FROM power_measurements WHERE logger_id = ?")
    params: list = [logger_id]
    if since is not None:
        sql += " AND measured_at >= ?"
        params.append(since)
    sql += " ORDER BY measured_at ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "measured_at": r[0], "current_ma": r[1], "bus_v": r[2],
            "shunt_mv": r[3], "range_ma": r[4], "seq": r[5],
        }
        for r in rows
    ]


def get_power_logger_ids() -> list[str]:
    """Logger-IDs, fuer die Stromproben vorliegen (neueste zuerst)."""
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT logger_id, MAX(measured_at) FROM power_measurements "
        "GROUP BY logger_id ORDER BY MAX(measured_at) DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_sessions_chronological() -> list[dict]:
    """
    Alle Temperatursessions mit Startzeit (chronologisch, ohne Kalibrierdaten).

    Grundlage fuer die serverseitige Zuordnung von Stromproben zu Sessions:
    Eine Stromprobe gehoert zur Session mit der spaetesten start_time <=
    Messzeitpunkt. Bewusst OHNE sensor_id-Filter: Es wird nur EIN Logger
    gleichzeitig gemessen, und die Session startet beim Anlegen der neuen
    Firmware-Version (siehe clone_latest_session_with_calibration).

    @return [{id, sensor_id, start_time, custom_text}] (chronologisch)
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_S)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, sensor_id, start_time, custom_text FROM sessions "
        "ORDER BY start_time ASC, id ASC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "sensor_id": r[1], "start_time": r[2], "custom_text": r[3]}
        for r in rows
    ]
