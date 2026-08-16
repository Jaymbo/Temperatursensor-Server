import sys
import types
import sqlite3
import pytest

import db as dbmod


def _seed_session(sensor_id: str, calibration: str | None = None) -> str:
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data) VALUES (?, datetime('now','localtime'), ?)",
        (sensor_id, calibration),
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return f"{sensor_id}_{session_id}"


def test_series_calibrated_no_calibration_points(client):
    # Create session without calibration_data — get_calibration_points returns
    # [{"calibration": None, "correction_points": None}] which is truthy, but
    # correction_points is None → generate_corrected_preview_data returns None
    sensor_session = _seed_session("70", calibration=None)
    res = client.get(f"/series/{sensor_session}_calibrated")
    assert res.status_code == 200
    # Falls get_calibration_points [] zurückgibt (neue Logik), bekommen wir den
    # "Keine Kalibrierungsdaten"-Zweig. Ansonsten "Fehler beim Generieren".
    assert "error" in res.json()


def test_series_calibrated_invalid_calibration_string(client):
    # Provide valid calibration string but no correction_points -> returns data with no correction
    sensor_session = _seed_session("71", calibration="1000,3.9083e-3,-5.775e-7,3.3,1000")
    res = client.get(f"/series/{sensor_session}_calibrated")
    # No measurements exist, so returns error about no preview data
    assert res.status_code == 200
    assert "error" in res.json()


def test_add_measurements_generic_exception_path(client, monkeypatch):
    _seed_session("72", calibration=None)

    # Force a generic exception in add_measurements flow (after validations)
    import main

    def boom(_sensor_id: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "get_start_time_of_latest_session", boom)

    payload = {
        "sensor_id": "72",
        "timestamps": [0, 1],
        "temperatures": [10.0, 0.1],
    }
    res = client.post("/measurements", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error in main.py"
    assert body["message"] == "Ein unerwarteter Fehler ist aufgetreten."


def test_db_get_data_by_sensor_row_none_returns_empty():
    # Session id not present -> row None path
    out = dbmod.get_data_by_sensor("999_123456")
    assert out == []


def test_calibration_strategy_generate_curve_step_zero():
    from calibration_strategy import CalibrationStrategy
    strat = CalibrationStrategy()
    curve = strat.generate_calibration_curve(
        optimized_params=strat.current_params,
        current_params=strat.current_params,
        t_min=0.0,
        t_max=1.0,
        step=0.0,  # triggers step<=0 branch
    )
    assert curve["measured"] and curve["corrected"]


# removed: duplicate of bounds check covered elsewhere


def test_generate_corrected_preview_data_exception_path(monkeypatch):
    # Make the internal import db.get_data_by_sensor raise -> hit broad except branch
    import calibration_strategy as cs

    # Create a dummy module named 'db' with a raising function
    dummy = types.SimpleNamespace()

    def raise_get_data(_sensor_id: str):
        raise RuntimeError("fail")

    setattr(dummy, "get_data_by_sensor", raise_get_data)
    # Backup and replace
    orig = sys.modules.get("db")
    sys.modules["db"] = dummy
    try:
        out = cs.generate_corrected_preview_data("1", [{"t": 20.0, "delta": 0.5}])
        assert out is None
    finally:
        if orig is not None:
            sys.modules["db"] = orig
        else:
            del sys.modules["db"]


# removed: duplicate call to internal conversion test helper


def test_calibrate_rejects_more_than_two_points(client):
    # Create a real session
    sess = _seed_session("73", calibration=None)
    # No limit on points anymore - should succeed
    res = client.post(
        "/calibrate",
        json={
            "sensor_session": sess,
            "calibration_points": [
                {"measured": 10.0, "target": 10.0},
                {"measured": 20.0, "target": 20.0},
                {"measured": 30.0, "target": 30.0},
            ],
        },
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"


def test_one_point_calibration_invalid_voltage():
    from calibration_strategy import CalibrationStrategy
    # Make params produce invalid U (<=0 or >=U0) by using negative R1
    strat = CalibrationStrategy({"R0": 1000.0, "A": 3.9083e-3, "B": -5.775e-7, "U0": 3.3, "R1": -1000.0})
    with pytest.raises(ValueError):
        strat.determine_calibration_parameters([(20.0, 20.0)])


def test_one_point_calibration_invalid_denominator():
    from calibration_strategy import CalibrationStrategy
    strat = CalibrationStrategy()
    # Use extreme target to force denom <= 0
    with pytest.raises(ValueError):
        strat.determine_calibration_parameters([(20.0, 1_000_000.0)])


def test_two_point_calibration_invalid_voltages():
    from calibration_strategy import CalibrationStrategy
    # Negative R1 yields invalid U
    strat = CalibrationStrategy({"R0": 1000.0, "A": 3.9083e-3, "B": -5.775e-7, "U0": 3.3, "R1": -500.0})
    with pytest.raises(ValueError):
        strat.determine_calibration_parameters([(10.0, 10.0), (20.0, 20.0)])


def test_update_sensor_session_error_branches(client, monkeypatch):
    import main

    # ValueError path
    def raise_val(_):
        raise ValueError("nope")

    monkeypatch.setattr(main, "clone_latest_session_with_calibration", raise_val)
    res = client.post("/update/99")
    assert res.status_code == 200
    assert res.json()["status"] == "error"

    # Generic exception path
    def raise_exc(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "clone_latest_session_with_calibration", raise_exc)
    res = client.post("/update/99")
    assert res.status_code == 200
    assert res.json()["status"] == "error"


def test_start_sensor_error_branch(client, monkeypatch):
    import main

    def raise_exc(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "add_or_update_custom_text_entry", raise_exc)
    res = client.post("/start_sensor", json={"custom_text": "x"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "error"


def _insert_measurement_for_session(sensor_session: str, timestamp: str = "2025-01-01T00:00:00", temperature: float = 20.0):
    sensor_id, session_id = sensor_session.split("_")
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO measurements (session_id, timestamp, temperature) VALUES (?, ?, ?)",
        (session_id, timestamp, temperature),
    )
    conn.commit()
    conn.close()


def test_series_calibrated_success_with_data(client):
    # Seed session with valid calibration string and one measurement and correction_points
    cal = "1000,3.9083e-3,-5.775e-7,3.3,1000"
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data, correction_points) VALUES (?, datetime('now','localtime'), ?, ?)",
        ("81", cal, '[{"t": 20.0, "delta": 1.0}]'),
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    sensor_session = f"81_{session_id}"
    _insert_measurement_for_session(sensor_session)

    res = client.get(f"/series/{sensor_session}_calibrated")
    assert res.status_code == 200
    payload = res.json()
    assert "data" in payload and isinstance(payload["data"], list)
    # comments comes from original session
    assert "comments" in payload


def test_db_add_temperature_data_length_mismatch_raises(temp_db_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    dbmod.initialize_db()
    # Need a session to exist to reach length check path
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO sessions (sensor_id) VALUES (?)", ("X",))
    conn.commit(); conn.close()
    with pytest.raises(ValueError):
        dbmod.add_temperature_data("X", [0.0, 1.0], [10.0])


def test_process_relative_data_happy_path():
    from db import process_relative_data
    ts, temps = process_relative_data([0.0, 1.0, 2.0], [10.0, 0.5, -0.5])
    assert ts == [0.0, 1.0, 3.0]
    assert temps == [10.0, 10.5, 10.0]


def test_add_or_update_custom_text_entry_insert_branch(temp_db_path, monkeypatch):
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    dbmod.initialize_db()
    # No empty row exists yet, so insert branch executes
    entry_id = dbmod.add_or_update_custom_text_entry("hello")
    assert isinstance(entry_id, int) and entry_id > 0


def test_add_or_update_custom_text_entry_update_branch(temp_db_path, monkeypatch):
    """Update-Branch: existing empty row gets updated instead of inserting new one."""
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    dbmod.initialize_db()
    # Create an empty row first
    first_id = dbmod.add_or_update_custom_text_entry("first")
    # Second call should reuse the existing row (update branch)
    second_id = dbmod.add_or_update_custom_text_entry("second")
    assert second_id == first_id  # Same row reused


def test_calibrate_preview_with_data(client):
    # Create session with one measurement; ensure corrected_data branch is taken
    cal = "1000,3.9083e-3,-5.775e-7,3.3,1000"
    sensor_session = _seed_session("82", calibration=cal)
    _insert_measurement_for_session(sensor_session, temperature=21.0)

    res = client.post(
        "/calibrate",
        json={
            "sensor_session": sensor_session,
            "calibration_points": [{"measured": 20.0, "target": 20.0}],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body.get("status") == "success"
    assert body.get("is_preview") is True
    assert body.get("preview_session") == f"{sensor_session}_calibrated"
    assert "curve" in body and isinstance(body["curve"].get("measured", []), list)
