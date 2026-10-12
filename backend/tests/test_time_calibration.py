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


# ── Fehler-/Defensivpfade (fuer 100 % Coverage) ──────────────────────────

def test_extract_sensor_id_without_underscore():
    from main import _extract_sensor_id
    assert _extract_sensor_id("plain") == "plain"


def test_apply_time_calibration_empty_data():
    from main import apply_time_calibration_to_session
    assert apply_time_calibration_to_session("x_1", []) == []


def test_apply_time_calibration_skips_non_dict(client):
    from main import apply_time_calibration_to_session
    _seed("30", "2026-01-01T00:00:00", 300, [])
    dbmod.set_time_factor("30", 2.0)
    data = [
        "kein-dict",
        {"timestamp": "2026-01-01T02:00:00", "temperature": 21.0},
        {"temperature": 5.0},   # ohne timestamp -> unveraendert
    ]
    out = apply_time_calibration_to_session("30_300", data)
    assert out[0] == "kein-dict"
    assert out[1]["timestamp"] == "2026-01-01T04:00:00"
    assert out[2] == {"temperature": 5.0}


def test_apply_time_calibration_no_start_returns_data(client):
    from main import apply_time_calibration_to_session
    # Faktor != 1, aber Session existiert nicht -> start_time None -> unveraendert
    dbmod.set_time_factor("31", 2.0)
    data = [{"timestamp": "2026-01-01T02:00:00", "temperature": 21.0}]
    assert apply_time_calibration_to_session("31_999", data) == data


def test_generate_time_calibration_preview_paths(client, monkeypatch):
    import main
    _seed("40", "2026-01-01T00:00:00", 400, [("2026-01-01T02:00:00", 21.0)])
    out = main.generate_time_calibration_preview("40_400", 2.0)
    assert out["timestamps"] == ["2026-01-01T04:00:00"]
    assert out["temperatures"] == [21.0]
    # keine Daten -> None
    assert main.generate_time_calibration_preview("41_401", 2.0) is None
    # ungueltige Punkte werden uebersprungen; nur der gueltige bleibt
    _seed("42", "2026-01-01T00:00:00", 402, [])
    monkeypatch.setattr(main, "get_data_by_sensor", lambda s: [
        "kein-dict",
        {"timestamp": None, "temperature": 20.0},
        {"timestamp": "2026-01-01T02:00:00", "temperature": None},
        {"timestamp": "2026-01-01T02:00:00", "temperature": 21.0},
    ])
    out = main.generate_time_calibration_preview("42_402", 2.0)
    assert out["timestamps"] == ["2026-01-01T04:00:00"]
    # ALLE Punkte ungueltig -> None
    monkeypatch.setattr(main, "get_data_by_sensor", lambda s: [
        "kein-dict", {"timestamp": None, "temperature": 1.0}])
    assert main.generate_time_calibration_preview("42_402", 2.0) is None


def test_compute_new_factor_invalid_iso_raises():
    import pytest
    from main import compute_new_time_factor
    with pytest.raises(ValueError):
        compute_new_time_factor(
            1.0, "kaputt", "2026-01-01T04:00:00", "2026-01-01T00:00:00")


def test_measurements_broadcast_applies_time_factor(client, monkeypatch):
    """/measurements mit gesetztem Faktor: Broadcast enthaelt korrigierte Timestamps."""
    import main
    _seed("50", "2026-01-01T00:00:00", 500, [])
    dbmod.set_time_factor("50", 2.0)
    captured = []

    async def fake_broadcast(session, payload):
        captured.append((session, payload))

    monkeypatch.setattr(main.manager, "broadcast", fake_broadcast)
    res = client.post("/measurements", json={
        "sensor_id": "50", "timestamps": [0, 3600], "temperatures": [20.0, 21.0]})
    assert res.json()["status"] == "success"
    assert captured and captured[0][1]["action"] == "new_measurements"
    ts = captured[0][1]["timestamps"]
    assert ts[0].startswith("2026-01-01T00:00:00")  # Start fix
    assert ts[1].startswith("2026-01-01T02:00:00")  # 1h * 2 = 2h


def test_calibrate_time_missing_fields(client):
    res = client.post("/calibrate_time", json={})
    assert res.json()["status"] == "error"


def test_calibrate_time_no_session(client):
    res = client.post("/calibrate_time", json={
        "sensor_session": "60_6000",
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00"})
    assert res.json()["message"] == "Keine Session/Startzeit gefunden."


def test_calibrate_time_negative_factor(client):
    # actual VOR dem Session-Start -> negativer Faktor -> abgelehnt
    sensor_session = _seed("61", "2026-01-01T00:00:00", 601, [])
    res = client.post("/calibrate_time", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2025-01-01T00:00:00"})
    assert res.json()["message"] == "Ungueltiger Zeitfaktor berechnet."


def test_calibrate_time_unexpected_error(client, monkeypatch):
    import main
    sensor_session = _seed("62", "2026-01-01T00:00:00", 602, [])

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "compute_new_time_factor", boom)
    res = client.post("/calibrate_time", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00"})
    assert res.json()["status"] == "error"
    assert "Zeit-Kalibrierungsfehler" in res.json()["message"]


def test_calibrate_time_apply_no_session(client):
    res = client.post("/calibrate_time/apply", json={
        "sensor_session": "63_6003",
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00"})
    assert res.json()["message"] == "Keine Session/Startzeit gefunden."


def test_calibrate_time_apply_negative_factor(client):
    sensor_session = _seed("64", "2026-01-01T00:00:00", 604, [])
    res = client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2025-01-01T00:00:00"})
    assert res.json()["message"] == "Ungueltiger Zeitfaktor berechnet."


def test_calibrate_time_apply_unexpected_error(client, monkeypatch):
    import main
    sensor_session = _seed("65", "2026-01-01T00:00:00", 605, [])

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "compute_new_time_factor", boom)
    res = client.post("/calibrate_time/apply", json={
        "sensor_session": sensor_session,
        "measured_timestamp": "2026-01-01T02:00:00",
        "actual_timestamp": "2026-01-01T04:00:00"})
    assert res.json()["status"] == "error"
    assert "Zeit-Kalibrierungsfehler" in res.json()["message"]


def test_main_parse_iso_defensive():
    from main import _parse_iso
    assert _parse_iso("") is None
    assert _parse_iso(None) is None
    assert _parse_iso("kaputt") is None


def test_reading_age_invalid_dates():
    from main import _reading_age_seconds
    assert _reading_age_seconds(None) is None
    assert _reading_age_seconds({"measured_at": None, "received_at": None}) is None
    # Datum vorhanden, aber unparsebar -> None
    assert _reading_age_seconds({"measured_at": None, "received_at": "kaputt"}) is None


def test_get_session_start_time_malformed_session():
    # sensor_session ohne '_' -> ValueError im split -> None
    assert dbmod.get_session_start_time("keinunderscore") is None


def test_apply_time_factor_invalid_timestamp():
    start = "2026-01-01T00:00:00"
    # Ungueltiger Timestamp -> Original zurueck (defensiv)
    assert dbmod.apply_time_factor("kaputt", start, 2.0) == "kaputt"
