import db as dbmod


def _ensure_session(sensor_id: str):
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


def test_measurements_happy_path(client):
    sensor_id = "1"
    _ensure_session(sensor_id)

    payload = {
        "sensor_id": sensor_id,
        "timestamps": [0, 100, 100, 100],
        "temperatures": [22.5, 0.1, -0.3, 0.2],
    }
    res = client.post("/measurements", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["sensor_session"].startswith(f"{sensor_id}_")