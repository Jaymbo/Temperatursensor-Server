"""Tests fuer die Akku-/Strom-Endpunkte in main.py."""
import sqlite3

import db as dbmod


def _ensure_session(sensor_id: str, hw_rev=None):
    import datetime
    try:
        dbmod.get_latest_session_for_sensor_id(sensor_id)
    except Exception:
        conn = sqlite3.connect(dbmod.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions (sensor_id, start_time, calibration_data, hw_rev) "
            "VALUES (?, ?, NULL, ?)",
            (sensor_id, datetime.datetime(2026, 1, 1).isoformat(), hw_rev),
        )
        conn.commit()
        conn.close()


def _seed_reading(sensor_id, measured_at, mv, received_at=None, under_load=None):
    dbmod.add_battery_readings([{
        "sensor_id": sensor_id,
        "battery_id": None,
        "measured_at": measured_at,
        "received_at": received_at or measured_at,
        "mv": mv,
        "raw": None,
        "under_load": under_load,
        "seq": None,
        "hw_rev": None,
    }])


# ── /measurements mit Akku-Telemetrie ────────────────────────────────────

def test_measurements_with_battery_telemetry(client):
    _ensure_session("1")
    dbmod.add_battery_assignment("1", "B1", "2025-01-01T00:00:00", reason="initial")

    payload = {
        "sensor_id": "1",
        "timestamps": [0, 100],
        "temperatures": [22.5, 0.1],
        "hw_rev": 2,
        "battery_mv": [4100, 4050],
        "battery_seq": [1, 2],
        "battery_ts": [0, 100],
        "battery_raw": [100, 99],
    }
    res = client.post("/measurements", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    readings = dbmod.get_battery_readings("1")
    assert [r["mv"] for r in readings] == [4100, 4050]
    assert readings[0]["battery_id"] == "B1"
    assert readings[0]["raw"] == 100
    assert readings[0]["seq"] == 1
    assert readings[0]["hw_rev"] == 2
    assert dbmod.get_session_hw_rev("1") == 2


def test_measurements_battery_no_timestamps(client):
    """Ohne battery_ts werden keine Akku-Werte gespeichert (kein Zeitbezug)."""
    _ensure_session("2")
    payload = {
        "sensor_id": "2",
        "timestamps": [0],
        "temperatures": [20.0],
        "battery_mv": [4000],
    }
    res = client.post("/measurements", json=payload)
    assert res.json()["status"] == "success"
    assert dbmod.get_battery_readings("2") == []


def test_measurements_battery_invalid_ts_skipped(client):
    _ensure_session("3")
    payload = {
        "sensor_id": "3",
        "timestamps": [0],
        "temperatures": [20.0],
        "battery_mv": [4000],
        "battery_ts": ["kaputt"],
    }
    res = client.post("/measurements", json=payload)
    assert res.json()["status"] == "success"
    assert dbmod.get_battery_readings("3") == []


def test_measurements_invalid_hw_rev_ignored(client):
    _ensure_session("4")
    payload = {
        "sensor_id": "4",
        "timestamps": [0],
        "temperatures": [20.0],
        "hw_rev": "abc",
    }
    res = client.post("/measurements", json=payload)
    assert res.json()["status"] == "success"


# ── /update/{id} calibration_hw_rev ──────────────────────────────────────

def test_update_response_includes_calibration_hw_rev(client):
    _ensure_session("5", hw_rev=3)
    res = client.post("/update/5")
    assert res.status_code == 200
    assert res.json()["calibration_hw_rev"] == 3


# ── /battery/summary ────────────────────────────────────────────────────

def test_battery_summary_empty(client):
    res = client.get("/battery/summary")
    assert res.json() == {"summary": []}


def test_battery_summary_with_readings(client):
    _seed_reading("1", "2026-01-01T00:00:00", 4000)
    res = client.get("/battery/summary")
    data = res.json()["summary"]
    assert len(data) == 1
    assert data[0]["sensor_id"] == "1"
    assert data[0]["level"]["stage"] == 4
    assert data[0]["age_seconds"] is not None


def test_battery_summary_age_none_for_invalid_times(client):
    # Beide Zeiten ungueltig -> Alter unbekannt (None), kein Absturz.
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO battery_readings (sensor_id, measured_at, received_at, mv) "
        "VALUES (?, ?, ?, ?)",
        ("7", "kaputt", "kaputt", 3900),
    )
    conn.commit()
    conn.close()
    res = client.get("/battery/summary")
    data = res.json()["summary"]
    assert data[0]["age_seconds"] is None


def test_battery_summary_age_uses_received_at_fallback(client):
    # measured_at ungueltig -> Fallback auf received_at
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO battery_readings (sensor_id, measured_at, received_at, mv) "
        "VALUES (?, ?, ?, ?)",
        ("8", "kaputt", "2026-01-01T00:00:00", 3900),
    )
    conn.commit()
    conn.close()
    res = client.get("/battery/summary")
    assert res.json()["summary"][0]["age_seconds"] is not None


# ── /battery/fleet ──────────────────────────────────────────────────────

def test_battery_fleet(client):
    _seed_reading("1", "2026-01-01T00:00:00", 4100)
    _seed_reading("1", "2026-01-02T00:00:00", 3900)
    _seed_reading("2", "2026-01-01T00:00:00", 4000)
    _seed_reading("2", "2026-01-03T00:00:00", 3800)
    res = client.get("/battery/fleet")
    data = res.json()
    assert len(data["fleet"]) == 2
    assert data["median_rate_mv_per_day"] is not None


def test_battery_fleet_empty(client):
    res = client.get("/battery/fleet")
    assert res.json()["fleet"] == []
    assert res.json()["median_rate_mv_per_day"] is None


# ── /battery/health ─────────────────────────────────────────────────────

def test_battery_health_all_and_single(client):
    _seed_reading("1", "2026-01-01T00:00:00", 4100)
    _seed_reading("1", "2026-01-02T00:00:00", 3800)
    _seed_reading("2", "2026-01-01T00:00:00", 4000)

    res_all = client.get("/battery/health")
    assert set(res_all.json()["health"].keys()) == {"1", "2"}

    res_one = client.get("/battery/health", params={"sensor_id": "1"})
    assert list(res_one.json()["health"].keys()) == ["1"]

    # Betriebsphasen wurden persistiert
    assert len(dbmod.get_battery_sessions("1")) >= 1


def test_battery_health_empty(client):
    res = client.get("/battery/health")
    assert res.json() == {"health": {}}


# ── /battery/{sensor_id} ────────────────────────────────────────────────

def test_battery_history(client):
    _seed_reading("1", "2026-01-01T00:00:00", 4000)
    _seed_reading("1", "2026-01-02T00:00:00", 3900)
    dbmod.add_battery_assignment("1", "B1", "2025-01-01T00:00:00")
    res = client.get("/battery/1")
    data = res.json()
    assert data["sensor_id"] == "1"
    assert len(data["readings"]) == 2
    assert data["assignment"]["battery_id"] == "B1"


def test_battery_history_unknown(client):
    res = client.get("/battery/unbekannt")
    data = res.json()
    assert data["readings"] == []
    assert data["assignment"] is None


# ── POST /battery ───────────────────────────────────────────────────────

def test_create_battery(client):
    res = client.post("/battery", json={
        "battery_id": "B1", "capacity_class": "2000mAh",
        "purchase_date": "2025-01-01", "notes": "test",
    })
    assert res.json()["status"] == "success"
    assert res.json()["batteries"][0]["battery_id"] == "B1"


def test_create_battery_missing_id(client):
    res = client.post("/battery", json={"capacity_class": "2000mAh"})
    assert res.json()["status"] == "error"


# ── POST /battery/assign ─────────────────────────────────────────────────

def test_assign_battery_initial_and_swap(client):
    res = client.post("/battery/assign", json={
        "sensor_id": "1", "battery_id": "B1", "from_time": "2026-01-01T00:00:00",
    })
    assert res.json()["assignment"]["battery_id"] == "B1"

    # Wechsel: alte Zuordnung wird geschlossen, neue angelegt
    res2 = client.post("/battery/assign", json={
        "sensor_id": "1", "battery_id": "B2",
        "from_time": "2026-02-01T00:00:00", "reason": "swap",
    })
    assert res2.json()["assignment"]["battery_id"] == "B2"
    past = dbmod.get_battery_assignment("1", at_time="2026-01-15T00:00:00")
    assert past["battery_id"] == "B1"


def test_assign_battery_defaults(client):
    # ohne from_time/reason -> Defaults (Serverzeit / initial)
    res = client.post("/battery/assign", json={"sensor_id": "2", "battery_id": "B1"})
    assert res.json()["assignment"]["battery_id"] == "B1"


def test_assign_battery_missing_fields(client):
    res = client.post("/battery/assign", json={"sensor_id": "1"})
    assert res.json()["status"] == "error"


# ── POST /power/measurements ─────────────────────────────────────────────

def test_power_measurements(client):
    res = client.post("/power/measurements", json={
        "logger_id": "1",
        "currents_ma": [12.5, 0.05],
        "timestamps": [0, 1],
        "bus_v": [3.9, 3.9],
        "shunt_mv": [1.2, 0.0],
        "range_ma": 100.0,
        "seq": [1, 2],
    })
    assert res.json() == {"status": "success", "inserted": 2}
    measurements = dbmod.get_power_measurements("1")
    assert measurements[0]["bus_v"] == 3.9
    assert measurements[1]["range_ma"] == 100.0
    assert measurements[0]["seq"] == 1


def test_power_measurements_scalar_bus_v(client):
    res = client.post("/power/measurements", json={
        "logger_id": "2", "currents_ma": [5.0], "bus_v": 3.8, "range_ma": 50.0,
    })
    assert res.json()["inserted"] == 1
    measurements = dbmod.get_power_measurements("2")
    assert measurements[0]["bus_v"] == 3.8
    assert measurements[0]["shunt_mv"] is None


def test_power_measurements_missing_fields(client):
    res = client.post("/power/measurements", json={"logger_id": "1"})
    assert res.json()["status"] == "error"


# ── POST /power/aggregate ────────────────────────────────────────────────

def test_power_aggregate(client):
    res = client.post("/power/aggregate", json={
        "logger_id": "1",
        "window_start": "2026-01-01T00:00:00",
        "window_end": "2026-01-01T00:01:00",
        "samples": 60,
        "current_ma_mean": 5.0,
        "current_ma_min": 0.1,
        "current_ma_max": 30.0,
        "charge_uah": 1.5,
        "gap": False,
    })
    assert res.json()["status"] == "success"
    aggregates = dbmod.get_power_aggregates("1")
    assert aggregates[0]["samples"] == 60
    assert aggregates[0]["gap"] is False


def test_power_aggregate_missing_fields(client):
    res = client.post("/power/aggregate", json={"logger_id": "1"})
    assert res.json()["status"] == "error"


# ── GET /power/{logger_id} ──────────────────────────────────────────────

def test_power_history(client):
    client.post("/power/measurements", json={
        "logger_id": "1", "currents_ma": [5.0],
    })
    res = client.get("/power/1")
    data = res.json()
    assert data["logger_id"] == "1"
    assert len(data["measurements"]) == 1


def test_power_history_unknown(client):
    res = client.get("/power/unbekannt")
    assert res.json()["measurements"] == []
    assert res.json()["aggregates"] == []
