"""Tests fuer die Zeit-Kalibrierung (Faktor pro sensor_id, fixer Startpunkt).

Kernformeln (siehe backend/main.py, backend/db.py):
- Anwendung: korrigiert = start + (gemessen - start) * K   (fixer Start)
- Neuer Faktor aus einem Punkt:
    K_new = K_old * (actual - start) / (measured - start)
  wobei `measured` der bereits mit K_old korrigierte, angezeigte Punkt ist.
  -> kumulativ entspricht dies "bisheriger_Faktor * neuer_Faktor".

Hinweis: Alle DB-bereichten Tests nehmen das `client`-Fixture aus conftest.py,
damit DB_PATH auf eine isolierte Temp-DB gepatcht ist (keine Kollision mit der
echten DB / dem laufenden Server).
"""
import sqlite3

import db as dbmod


# ── Helpers (nutzen dbmod.DB_PATH, d.h. die gepatchte Temp-DB) ────────────

def _insert_session(sensor_id: str, start_time_iso: str, id_: int) -> None:
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO sessions (id, sensor_id, start_time, calibration_data) "
        "VALUES (?, ?, ?, ?)",
        (id_, sensor_id, start_time_iso, "100,3.9083e-3,-5.775e-7,3.3,10000"),
    )
    conn.commit()
    conn.close()


def _add_measurements(id_: int, points: list[tuple[str, float]]) -> None:
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR IGNORE INTO measurements (session_id, timestamp, temperature) VALUES (?, ?, ?)",
        [(id_, t, temp) for t, temp in points],
    )
    conn.commit()
    conn.close()


def _seed(sensor_id: str, start_iso: str, session_id: int,
          points: list[tuple[str, float]]) -> str:
    _insert_session(sensor_id, start_iso, session_id)
    _add_measurements(session_id, points)
    return f"{sensor_id}_{session_id}"


# ── db.apply_time_factor (pure) ───────────────────────────────────────────

def test_apply_time_factor_stretches_from_fixed_start():
    start = "2026-01-01T00:00:00"
    # 2 Stunden nach Start, K=2 -> 4 Stunden nach Start (Start bleibt fix).
    assert dbmod.apply_time_factor("2026-01-01T02:00:00", start, 2.0) == "2026-01-01T04:00:00"


def test_apply_time_factor_keeps_start_fixed():
    start = "2026-01-01T00:00:00"
    assert dbmod.apply_time_factor(start, start, 3.0) == start


def test_apply_time_factor_no_change_when_k_is_one():
    start = "2026-01-01T00:00:00"
    assert dbmod.apply_time_factor("2026-01-01T02:00:00", start, 1.0) == "2026-01-01T02:00:00"


def test_apply_time_factor_compression():
    start = "2026-01-01T00:00:00"
    # K=0.5: 2h werden zu 1h.
    assert dbmod.apply_time_factor("2026-01-01T02:00:00", start, 0.5) == "2026-01-01T01:00:00"


def test_apply_time_factor_handles_none_start():
    # Kein Start -> Original unveraendert (defensiv).
    assert dbmod.apply_time_factor("2026-01-01T02:00:00", None, 2.0) == "2026-01-01T02:00:00"


# ── db.get/set_time_factor ───────────────────────────────────────────────

def test_time_factor_default_is_one(client):
    assert dbmod.get_time_factor("999") == 1.0


def test_set_and_get_time_factor(client):
    dbmod.set_time_factor("42", 1.5)
    assert dbmod.get_time_factor("42") == 1.5


def test_set_time_factor_rejects_invalid(client):
    import pytest

    with pytest.raises(ValueError):
        dbmod.set_time_factor("x", 0.0)
    with pytest.raises(ValueError):
        dbmod.set_time_factor("x", -1.0)
    with pytest.raises(ValueError):
        dbmod.set_time_factor("", 1.0)


# ── main.compute_new_time_factor (pure) ──────────────────────────────────

def test_compute_new_factor_first_calibration():
    from main import compute_new_time_factor

    # K_old=1.0. Gemessen (angezeigt) 2h nach Start, soll 4h -> K_new=2.0.
    k = compute_new_time_factor(
        1.0,
        "2026-01-01T02:00:00",  # measured (angezeigt)
        "2026-01-01T04:00:00",  # actual (gewuenscht)
        "2026-01-01T00:00:00",  # start
    )
    assert abs(k - 2.0) < 1e-9


def test_compute_new_factor_cumulative_matches_multiplication():
    from main import compute_new_time_factor

    start = "2026-01-01T00:00:00"
    # Erster Kalibrierpunkt: angezeigter Punkt 2h -> soll 3h.
    k1 = compute_new_time_factor(1.0, "2026-01-01T02:00:00", "2026-01-01T03:00:00", start)
    assert abs(k1 - 1.5) < 1e-9
    # Zweiter Kalibrierpunkt (K_old=1.5): angezeigter Punkt 2h -> soll 3h.
    k2 = compute_new_time_factor(k1, "2026-01-01T02:00:00", "2026-01-01T03:00:00", start)
    assert abs(k2 - 2.25) < 1e-9  # 1.5 * (3h)/(2h)
    assert abs(k2 - k1 * 1.5) < 1e-9


def test_compute_new_factor_at_start_keeps_old():
    from main import compute_new_time_factor

    start = "2026-01-01T00:00:00"
    # Punkt liegt exakt am Start -> Abstand 0, Faktor bleibt unveraendert.
    k = compute_new_time_factor(1.7, start, "2026-01-01T05:00:00", start)
    assert abs(k - 1.7) < 1e-9


def test_compute_new_factor_requires_start():
    import pytest
    from main import compute_new_time_factor

    with pytest.raises(ValueError):
        compute_new_time_factor(1.0, "2026-01-01T02:00:00", "2026-01-01T03:00:00", None)


# ── main.apply_time_calibration_to_session (Read-Pfad) ───────────────────

def test_apply_time_calibration_to_session_uses_stored_factor(client):
    from main import apply_time_calibration_to_session

    _seed("7", "2026-01-01T00:00:00", 100,
          [("2026-01-01T00:00:00", 20.0), ("2026-01-01T02:00:00", 21.0)])
    dbmod.set_time_factor("7", 2.0)
    data = [
        {"timestamp": "2026-01-01T00:00:00", "temperature": 20.0},  # Start -> fix
        {"timestamp": "2026-01-01T02:00:00", "temperature": 21.0},  # 2h -> 4h
    ]
    out = apply_time_calibration_to_session("7_100", data)
    assert out[0]["timestamp"] == "2026-01-01T00:00:00"
    assert out[1]["timestamp"] == "2026-01-01T04:00:00"
    assert out[0]["temperature"] == 20.0  # Temperaturen unveraendert
    assert out[1]["temperature"] == 21.0


def test_apply_time_calibration_to_session_noop_when_factor_one(client):
    from main import apply_time_calibration_to_session

    _seed("8", "2026-01-01T00:00:00", 101, [("2026-01-01T02:00:00", 21.0)])
    data = [{"timestamp": "2026-01-01T02:00:00", "temperature": 21.0}]
    out = apply_time_calibration_to_session("8_101", data)
    assert out[0]["timestamp"] == "2026-01-01T02:00:00"


# ── Endpoints ────────────────────────────────────────────────────────────

def test_calibrate_time_apply_stores_factor(client):
    sensor_session = _seed("1", "2026-01-01T00:00:00", 200,
                           [("2026-01-01T00:00:00", 20.0), ("2026-01-01T02:00:00", 21.0)])
    res = client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00",
    })
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert abs(res.json()["new_factor"] - 2.0) < 1e-9
    assert abs(dbmod.get_time_factor("1") - 2.0) < 1e-9


def test_calibrate_time_apply_invalid_missing(client):
    sensor_session = _seed("2", "2026-01-01T00:00:00", 201, [])
    res = client.post("/calibrate_time/apply", json={"sensor_session": sensor_session})
    assert res.status_code == 200
    assert res.json()["status"] == "error"


def test_time_calibration_get_and_reset(client):
    sensor_session = _seed("3", "2026-01-01T00:00:00", 202, [])
    client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00",
    })
    g = client.get("/time_calibration", params={"sensor_id": "3"})
    assert g.status_code == 200
    assert abs(g.json()["factor"] - 2.0) < 1e-9

    d = client.delete("/time_calibration", params={"sensor_id": "3"})
    assert d.status_code == 200
    assert d.json()["factor"] == 1.0
    assert dbmod.get_time_factor("3") == 1.0


def test_calibrate_time_preview_returns_factor(client):
    sensor_session = _seed("4", "2026-01-01T00:00:00", 203,
                           [("2026-01-01T00:00:00", 20.0), ("2026-01-01T02:00:00", 21.0)])
    res = client.post("/calibrate_time", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert abs(body["new_factor"] - 2.0) < 1e-9
    assert body["is_preview"] is True
    # Preview ist NICHT persistent
    assert dbmod.get_time_factor("4") == 1.0


def test_series_read_path_applies_time_factor(client):
    sensor_session = _seed("5", "2026-01-01T00:00:00", 204,
                           [("2026-01-01T00:00:00", 20.0), ("2026-01-01T02:00:00", 21.0)])
    client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00",
    })
    res = client.get(f"/series/{sensor_session}")
    assert res.status_code == 200
    ts = [d["timestamp"] for d in res.json()["data"]]
    assert ts[0] == "2026-01-01T00:00:00"  # Start fix
    assert ts[1] == "2026-01-01T04:00:00"  # 2h -> 4h


def test_raw_measurements_unchanged_after_calibration(client):
    """Rohdaten in 'measurements' duerfen NICHT aendern (reversible Korrektur)."""
    sensor_session = _seed("6", "2026-01-01T00:00:00", 205,
                           [("2026-01-01T00:00:00", 20.0), ("2026-01-01T02:00:00", 21.0)])
    client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00",
    })
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT timestamp FROM measurements WHERE session_id = 205 ORDER BY timestamp")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    assert "2026-01-01T02:00:00" in rows
    assert "2026-01-01T04:00:00" not in rows


def test_cumulative_calibration_via_endpoints(client):
    """Zweimal kalibrieren ueber den Endpoint -> Faktor kompositioniert (1.5*1.5)."""
    sensor_session = _seed("10", "2026-01-01T00:00:00", 210,
                           [("2026-01-01T00:00:00", 20.0), ("2026-01-01T02:00:00", 21.0)])
    client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T03:00:00",
    })
    assert abs(dbmod.get_time_factor("10") - 1.5) < 1e-9
    client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T03:00:00",
    })
    assert abs(dbmod.get_time_factor("10") - 2.25) < 1e-9


def test_multiple_sessions_each_use_own_start(client):
    """'Zukunfige UND vergangene' Sessions: Jede nutzt ihren eigenen Start."""
    _insert_session("20", "2026-01-01T00:00:00", 300)
    _insert_session("20", "2026-03-01T00:00:00", 301)
    _add_measurements(300, [("2026-01-01T02:00:00", 20.0)])
    _add_measurements(301, [("2026-03-01T02:00:00", 20.0)])
    dbmod.set_time_factor("20", 2.0)

    from main import apply_time_calibration_to_session
    a = apply_time_calibration_to_session("20_300", [{"timestamp": "2026-01-01T02:00:00", "temperature": 20.0}])
    b = apply_time_calibration_to_session("20_301", [{"timestamp": "2026-03-01T02:00:00", "temperature": 20.0}])
    assert a[0]["timestamp"] == "2026-01-01T04:00:00"  # 2h*2 relativ zu Jan-Start
    assert b[0]["timestamp"] == "2026-03-01T04:00:00"  # 2h*2 relativ zu Mar-Start
