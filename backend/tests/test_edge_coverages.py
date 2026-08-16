import math
import types
import sys
import pytest


def test_voltage_to_temperature_nan_edges():
    from calibration_strategy import voltage_to_temperature

    params = {'R0': 1000.0, 'A': 3.9083e-3, 'B': -5.775e-7, 'U0': 3.3, 'R1': 1000.0}
    assert math.isnan(voltage_to_temperature(0.0, params))
    assert math.isnan(voltage_to_temperature(3.3, params))


def test_two_point_calibration_denom_zero():
    from calibration_strategy import CalibrationStrategy

    cs = CalibrationStrategy()
    # Same measured temperatures -> R1m == R2m; equal non-zero targets force denom == 0
    with pytest.raises(ValueError):
        cs.determine_calibration_parameters([(10.0, 1.0), (10.0, 1.0)])


def test_generate_corrected_preview_data_none_when_no_data(monkeypatch):
    # Inject a fake db module so the function sees no original data
    fake_db = types.SimpleNamespace(get_data_by_sensor=lambda sid: [])
    monkeypatch.setitem(sys.modules, 'db', fake_db)

    from calibration_strategy import generate_corrected_preview_data

    assert generate_corrected_preview_data('1_1', [{"t": 20.0, "delta": 0.0}]) is None


def test_generate_corrected_preview_data_all_invalid(monkeypatch):
    # Return entries that are invalid/malformed; should result in None
    data = [
        123,
        {},
        {'timestamp': 't1', 'temperature': None},
        {'timestamp': None, 'temperature': 20},
        {'timestamp': 't2', 'temperature': 'not-a-number'},
    ]
    fake_db = types.SimpleNamespace(get_data_by_sensor=lambda sid: data)
    monkeypatch.setitem(sys.modules, 'db', fake_db)

    from calibration_strategy import generate_corrected_preview_data

    assert generate_corrected_preview_data('1_1', [{"t": 20.0, "delta": 0.0}]) is None


def test_add_measurements_endpoint_value_error_no_session(client):
    # No session exists for this sensor_id -> get_start_time_of_latest_session raises -> ValueError path
    payload = {
        "sensor_id": "no_session_sensor",
        "timestamps": [0, 1, 1],
        "temperatures": [20.0, 0.1, -0.1],
    }
    resp = client.post("/measurements", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "error in main.py"
    assert "Keine Session" in data.get("message", "")


def test_add_measurements_endpoint_generic_error(client, monkeypatch):
    # Create a session for sensor S1
    import db as dbmod
    dbmod.clone_latest_session_with_calibration("S1")

    # Patch add_temperature_data to raise a generic Exception -> generic except branch
    import main
    def _raise(*args, **kwargs):
        raise Exception("boom")
    monkeypatch.setattr(main, "add_temperature_data", _raise, raising=True)

    payload = {
        "sensor_id": "S1",
        "timestamps": [0, 1, 1],
        "temperatures": [20.0, 0.1, -0.1],
    }
    resp = client.post("/measurements", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "error in main.py"
    assert data.get("message") == "Ein unerwarteter Fehler ist aufgetreten."


def test_calibrate_preview_no_data(client):
    # Create a session for sensor 11 without measurements
    import db as dbmod
    result = dbmod.clone_latest_session_with_calibration("11")
    sensor_session = result["new_sensor_session"]

    resp = client.post(
        "/calibrate",
        json={
            "sensor_session": sensor_session,
            "calibration_points": [{"measured": 20.0, "target": 20.0}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "success"
    assert data.get("is_preview") is True
    # New API returns correction_points instead of optimized_params
    assert "correction_points" in data


def test_clone_latest_session_reuses_empty_entry(temp_db_path, monkeypatch):
    # Use raw DB functions without TestClient
    import db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    dbmod.initialize_db()

    # Create empty entry with custom text
    empty_id = dbmod.add_or_update_custom_text_entry("Hello")
    # Clone for S9 -> should reuse empty row id
    out = dbmod.clone_latest_session_with_calibration("S9")
    assert out["new_sensor_session"].endswith(f"_{empty_id}")
    assert out["custom_text"] == "Hello"
