import db as dbmod
import json as jsonmod


def _ensure_session_with_calibration(sensor_id: str, calibration: str | None = None):
    """Make sure the DB has a latest session for given sensor, with optional calibration string."""
    try:
        dbmod.get_latest_session_for_sensor_id(sensor_id)
        return
    except Exception:
        import sqlite3

        conn = sqlite3.connect(dbmod.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions (sensor_id, start_time, calibration_data) VALUES (?, datetime('now','localtime'), ?)",
            (sensor_id, calibration),
        )
        conn.commit()
        conn.close()


def test_single_point_offset(client):
    """1 Kalibrierpunkt → globaler Offset (delta = target - measured)."""
    sensor_id = "1"
    _ensure_session_with_calibration(sensor_id, calibration="1000,3.9083e-3,-5.775e-7,3.32,981.7")
    payload = {
        "sensor_session": sensor_id,
        "calibration_points": [{"measured": 19.8, "target": 20.0}],
    }

    res = client.post("/calibrate", json=payload)
    assert res is not None and res.status_code == 200
    result = res.json()

    pc = result.get("point_checks", [])
    assert isinstance(pc, list) and len(pc) == 1
    err = pc[0].get("error")
    # Bei einem Punkt ist die Korrektur exakt der delta → Fehler ≈ 0
    assert err is not None and abs(err) < 1e-9

    # correction_points im Ergebnis vorhanden
    cp = result.get("correction_points")
    assert isinstance(cp, list) and len(cp) == 1
    assert cp[0]["t"] == 19.8
    assert abs(cp[0]["delta"] - 0.2) < 1e-9


def test_two_points_interpolation(client):
    """2 Kalibrierpunkte → lineare Interpolation zwischen den Punkten."""
    sensor_id = "2"
    _ensure_session_with_calibration(sensor_id, calibration="1000,3.9083e-3,-5.775e-7,3.32,981.7")
    payload = {
        "sensor_session": sensor_id,
        "calibration_points": [
            {"measured": 19.832, "target": 20.0},
            {"measured": 49.523, "target": 49.0},
        ],
    }

    res = client.post("/calibrate", json=payload)
    assert res is not None and res.status_code == 200
    result = res.json()

    pc = result.get("point_checks", [])
    assert isinstance(pc, list) and len(pc) == 2
    # Bei exakten Kalibrierpunkten ist der Fehler ≈ 0
    errs = [p.get("error") for p in pc]
    assert all(e is not None and abs(e) < 1e-9 for e in errs)

    cp = result.get("correction_points")
    assert len(cp) == 2


def test_many_points_no_limit(client):
    """Mehr als 2 Punkte sind erlaubt (kein Limit mehr)."""
    sensor_id = "3"
    _ensure_session_with_calibration(sensor_id)
    payload = {
        "sensor_session": sensor_id,
        "calibration_points": [
            {"measured": 0.0, "target": 0.0},
            {"measured": 25.0, "target": 24.0},
            {"measured": 50.0, "target": 51.0},
        ],
    }

    res = client.post("/calibrate", json=payload)
    assert res.status_code == 200
    result = res.json()
    assert result["status"] == "success"
    assert len(result["correction_points"]) == 3


def test_interpolation_function():
    """Teste interpolation_correction direkt."""
    from calibration_strategy import interpolation_correction, apply_correction

    # 0 Punkte → Korrektur = 0
    assert interpolation_correction(20.0, []) == 0.0

    # 1 Punkt → globaler Offset
    points = [{"t": 25.0, "delta": -3.0}]
    assert interpolation_correction(0.0, points) == -3.0
    assert interpolation_correction(100.0, points) == -3.0

    # 2 Punkte → lineare Interpolation
    points = [
        {"t": 0.0, "delta": 0.0},
        {"t": 10.0, "delta": -5.0},
    ]
    assert abs(interpolation_correction(0.0, points) - 0.0) < 1e-9
    assert abs(interpolation_correction(10.0, points) - (-5.0)) < 1e-9
    assert abs(interpolation_correction(5.0, points) - (-2.5)) < 1e-9

    # Extrapolation
    assert abs(interpolation_correction(-5.0, points) - 0.0) < 1e-9
    assert abs(interpolation_correction(20.0, points) - (-5.0)) < 1e-9

    # apply_correction = T + delta
    assert abs(apply_correction(20.0, [{"t": 20.0, "delta": -2.0}]) - 18.0) < 1e-9


def test_calibrate_curve_and_point_checks(client):
    """Kurven- und Point-Check-Daten im Ergebnis."""
    sensor_id = "4"
    _ensure_session_with_calibration(sensor_id, calibration="1000,3.9083e-3,-5.775e-7,3.32,981.7")
    payload = {
        "sensor_session": sensor_id,
        "calibration_points": [
            {"measured": 10.0, "target": 10.5},
            {"measured": 30.0, "target": 29.0},
        ],
    }

    res = client.post("/calibrate", json=payload)
    result = res.json()
    curve = result.get("curve")
    assert curve is not None
    assert len(curve["measured"]) > 0
    assert len(curve["corrected"]) > 0
    # Kurve deckt den Bereich der Kalibrierpunkte ab
    assert curve["measured"][0] <= 5.0
    assert curve["measured"][-1] >= 35.0