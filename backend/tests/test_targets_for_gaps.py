import sqlite3

import db as dbmod
from calibration_strategy import temperature_to_voltage, voltage_to_temperature, generate_corrected_preview_data, apply_correction, interpolation_correction
from main import get_strategy_description


def _seed_session_with_measurements(sensor_id: str, points: list[tuple[str, float]]):
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    # create session
    cur.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data) VALUES (?, datetime('now','localtime'), NULL)",
        (sensor_id,),
    )
    session_id = cur.lastrowid
    # add some points
    for ts, temp in points:
        cur.execute(
            "INSERT INTO measurements (session_id, timestamp, temperature) VALUES (?, ?, ?)",
            (session_id, ts, temp),
        )
    conn.commit()
    conn.close()
    return f"{sensor_id}_{session_id}"


def test_get_all_series_and_list_series(client):
    # Seed two sessions: one real, one placeholder None_
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (sensor_id, custom_text) VALUES (NULL, 'placeholder')")
    cur.execute("INSERT INTO sessions (sensor_id, start_time) VALUES ('10', datetime('now','localtime'))")
    conn.commit()
    conn.close()

    # Direct DB function
    series = dbmod.get_all_series()
    assert any(s["sensor_session"].startswith("None_") for s in series)
    assert any(s["sensor_session"].startswith("10_") for s in series)

    # API list
    res = client.get("/series")
    assert res.status_code == 200
    payload = res.json()
    assert isinstance(payload, list) and len(payload) >= 2


def test_strategy_description_map():
    assert get_strategy_description(0) == "Keine Korrektur"
    assert get_strategy_description(1) == "Globaler Offset"
    assert "Lineare Interpolation" in get_strategy_description(3)
    assert "3 Punkte" in get_strategy_description(3)


def test_get_series_calibrated_error_when_no_data(client):
    # Create sensor without measurements and without calibration
    s = _seed_session_with_measurements("11", [])
    # Ask for calibrated preview
    res = client.get(f"/series/{s}_calibrated")
    assert res.status_code == 200
    body = res.json()
    assert "error" in body


def test_conversion_roundtrip_and_preview_generator(client):
    # Roundtrip on typical params
    params = {'R0': 1000.0, 'A': 3.9083e-3, 'B': -5.775e-7, 'U0': 3.3, 'R1': 1000.0}
    for t in [0.0, 25.0, 50.0]:
        u = temperature_to_voltage(t, params)
        t2 = voltage_to_temperature(u, params)
        assert abs(t - t2) < 1e-3

    # Seed some original data and call preview with correction_points
    sensor_session = _seed_session_with_measurements("12", [
        ("2025-01-01T00:00:00", 20.0),
        ("2025-01-01T00:00:10", 20.5),
    ])
    # apply_correction uses correction_points (delta-based)
    correction = [{"t": 20.0, "delta": 0.0}]
    out = generate_corrected_preview_data(sensor_session, correction)
    assert out is not None
    assert len(out["timestamps"]) == 2 and len(out["temperatures"]) == 2


def test_get_series_calibrated_success(client):
    # Seed a session with calibration and measurements and correction_points
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data, correction_points) VALUES ('13', datetime('now','localtime'), ?, ?)",
        ("1000,3.9083e-3,-5.775e-7,3.3,1000", '[{"t": 19.0, "delta": 0.5}]'),
    )
    session_id = cur.lastrowid
    cur.executemany(
        "INSERT INTO measurements (session_id, timestamp, temperature) VALUES (?, ?, ?)",
        [
            (session_id, "2025-01-01T00:00:00", 19.0),
            (session_id, "2025-01-01T00:00:05", 19.2),
        ],
    )
    conn.commit()
    conn.close()

    sensor_session = f"13_{session_id}"
    res = client.get(f"/series/{sensor_session}_calibrated")
    assert res.status_code == 200
    body = res.json()
    assert "data" in body and isinstance(body["data"], list) and len(body["data"]) == 2


def test_get_series_calibrated_parse_error(client):
    # Malformed calibration string should return error
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data) VALUES ('14', datetime('now','localtime'), 'broken')"
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    res = client.get(f"/series/14_{session_id}_calibrated")
    assert res.status_code == 200
    assert "error" in res.json()


def test_update_sensor_session_error_paths(client, monkeypatch):
    import main

    # ValueError path
    monkeypatch.setattr(main, "clone_latest_session_with_calibration", lambda sensor_id: (_ for _ in ()).throw(ValueError("no session")))
    res = client.post("/update/99")
    assert res.status_code == 200
    assert res.json()["status"] == "error"

    # Generic Exception path
    monkeypatch.setattr(main, "clone_latest_session_with_calibration", lambda sensor_id: (_ for _ in ()).throw(Exception("boom")))
    res = client.post("/update/99")
    assert res.status_code == 200
    assert res.json()["status"] == "error"


def test_add_measurements_endpoint_validation(client):
    # Missing fields
    res = client.post("/measurements", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"

    # Length mismatch
    # Need an existing session to get start_time
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (sensor_id, start_time) VALUES ('20', datetime('now','localtime'))")
    conn.commit()
    conn.close()
    res = client.post(
        "/measurements",
        json={"sensor_id": "20", "timestamps": [0, 1], "temperatures": [22.5]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"].startswith("error")


def test_broadcast_catches_exceptions(client):
    import main

    captured = []

    class Good:
        async def send_text(self, msg: str):
            captured.append(msg)

    class Bad:
        async def send_text(self, msg: str):
            raise RuntimeError("nope")

    main.manager.active_connections = [Good(), Bad()]
    # Ensure there is some session id to notify on
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (sensor_id, start_time) VALUES ('21', datetime('now','localtime'))")
    sid = cur.lastrowid
    conn.commit(); conn.close()

    res = client.post(f"/notify/21_{sid}", json={"action": "x"})
    assert res.status_code == 200
    assert captured and "21_" in captured[0]


def test_calibrate_no_preview_data_but_success(client):
    # Sensor with no measurements → corrected_data is None, but endpoint still returns success
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (sensor_id, start_time) VALUES ('30', datetime('now','localtime'))")
    conn.commit(); conn.close()

    res = client.post(
        "/calibrate",
        json={"sensor_session": "30", "calibration_points": [{"measured": 20.0, "target": 20.0}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body.get("is_preview") is True


def test_calibrate_missing_sensor_session(client):
    """Keine sensor_session → Fehler."""
    res = client.post("/calibrate", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"


def test_calibrate_empty_calibration_points(client):
    """Keine calibration_points → Fehler."""
    res = client.post("/calibrate", json={"sensor_session": "30_1", "calibration_points": []})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"


def test_calibrate_generic_exception(client, monkeypatch):
    """Genereller Exception-Pfad in /calibrate."""
    import calibration_strategy as cs
    monkeypatch.setattr(cs, "generate_corrected_preview_data", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    res = client.post(
        "/calibrate",
        json={"sensor_session": "30_1", "calibration_points": [{"measured": 20.0, "target": 20.0}]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"


def test_series_calibrated_json_parse_error(client):
    """Invalid JSON in correction_points → except Exception: pass branch."""
    # Verwende gültigen sensor_id Format (keine Unterstriche im sensor_id)
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data, correction_points) VALUES (?, datetime('now','localtime'), ?, ?)",
        ("JERR", "1000,3.9083e-3,-5.775e-7,3.3,1000", "NOT_VALID_JSON{{}"),
    )
    session_id = cur.lastrowid
    conn.commit(); conn.close()

    res = client.get(f"/series/JERR_{session_id}_calibrated")
    assert res.status_code == 200


# Duplicate of edge coverage test for internal conversion helper removed to avoid redundancy
