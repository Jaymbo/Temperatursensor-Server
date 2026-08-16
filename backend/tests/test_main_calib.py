from typing import Dict

import db as dbmod


def _ensure_session(sensor_id: str):
    """Create a session for the given sensor if none exists."""
    try:
        dbmod.get_latest_session_for_sensor_id(sensor_id)
    except Exception:
        import sqlite3

        conn = sqlite3.connect(dbmod.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions (sensor_id, start_time, calibration_data) VALUES (?, datetime('now','localtime'), NULL)",
            (sensor_id,),
        )
        conn.commit()
        conn.close()


def test_calibration_update(client):
    """Testet das Speichern von Korrekturpunkten über die /calibration Route."""
    sensor_id = "1"
    _ensure_session(sensor_id)

    # Korrekturpunkte als JSON-String
    correction_points = '[{"t": 25.0, "delta": -2.0}, {"t": 50.0, "delta": 1.5}]'
    payload: Dict[str, str] = {"sensor_id": sensor_id, "correction_points": correction_points}

    res = client.post("/calibration", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"