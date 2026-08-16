import sqlite3
import db as dbmod


def test_db_schema_initialized(client):
    # client fixture initializes a fresh temp DB and app
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(sessions);")
    sessions_cols = [c[1] for c in cur.fetchall()]
    assert {"id", "sensor_id", "start_time", "calibration_data", "custom_text"}.issubset(set(sessions_cols))

    cur.execute("PRAGMA table_info(measurements);")
    meas_cols = [c[1] for c in cur.fetchall()]
    assert {"id", "session_id", "timestamp", "temperature"}.issubset(set(meas_cols))

    cur.execute("PRAGMA table_info(comments);")
    comm_cols = [c[1] for c in cur.fetchall()]
    assert {"id", "sensor_session", "timestamp", "temperature", "comment"}.issubset(set(comm_cols))

    conn.close()