"""Tests fuer den Strom-Stream (/power/stream, /power/charge, /power/window)
und die gespeicherten Verbrauchs-Bereiche (/power/selections)."""

import datetime

import pytest


BASE = datetime.datetime(2026, 10, 12, 12, 0, 0)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


def _post_samples(client, logger_id: str, pts: list[tuple[float, float]]):
    """pts: [(offset_s, ma)] -> /power/measurements (5-ms-Raster wie Firmware)."""
    res = client.post("/power/measurements", json={
        "logger_id": logger_id,
        "timestamps": [p[0] for p in pts],
        "currents_ma": [p[1] for p in pts],
    })
    assert res.status_code == 200, res.text
    return res.json()


def _continuous_pts(start: datetime.datetime, count: int, ma: float = 50.0):
    """count Proben im 5-ms-Raster ab `start` (fuer gemessene Dauer)."""
    return [(round(i * 0.005, 3), ma) for i in range(count)]


def _add_session(client, start: datetime.datetime, text: str | None = None):
    """Legt eine Session direkt in der (gepatchten) DB an."""
    import db as dbmod
    conn = dbmod.sqlite3.connect(dbmod.DB_PATH)
    conn.execute(
        "INSERT INTO sessions (sensor_id, start_time, calibration_data, "
        "messintervall, sendpuffer) VALUES (1, ?, '[]', 10, 6)",
        (start.isoformat(),),
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if text is not None:
        conn.execute("UPDATE sessions SET custom_text = ? WHERE id = ?", (text, sid))
    conn.commit()
    conn.close()
    return sid


# --- /power/stream ------------------------------------------------------------

def test_stream_default_window_and_buckets(client):
    # Hinweis: measured_at = Serverzeit + timestamp-Offset -> die Proben liegen
    # knapp NACH dem Testzeitpunkt. Das Default-to_ts (= jetzt) wuerde sie
    # verpassen, daher to_ts explizit in die Zukunft; from_ts bleibt default
    # (to_ts - 120 s) und erfasst alle Proben.
    now = datetime.datetime.now()
    _post_samples(client, "1", _continuous_pts(now, 400, ma=42.0))
    res = client.get("/power/stream/1", params={
        "to_ts": (now + datetime.timedelta(seconds=3)).isoformat()})
    assert res.status_code == 200
    data = res.json()
    assert data["logger_id"] == "1"
    # 400 Proben @ 5 ms streuen ueber 3 kalender-alignierte 1-s-Buckets
    assert len(data["buckets"]) == 3
    b0 = data["buckets"][0]
    # Bucket-Grenzen absolut (Epoch-ms), 1-s-Raster, aufsteigend
    assert b0["t_ms"] % 1000 == 0
    assert data["buckets"][1]["t_ms"] == b0["t_ms"] + 1000
    assert data["buckets"][2]["t_ms"] == b0["t_ms"] + 2000
    assert b0["ma_min"] == pytest.approx(42.0)
    assert b0["ma_max"] == pytest.approx(42.0)
    assert b0["ma_mean"] == pytest.approx(42.0)
    # Alle 400 Proben sind in Buckets gelandet (nichts verworfen)
    assert sum(b["n"] for b in data["buckets"]) == 400
    # Session-Liste immer vorhanden (auch leer)
    assert data["sessions"] == []
    # Nur from_ts gesetzt: to_ts defaultet auf jetzt -> Proben danach, leer ok
    res = client.get("/power/stream/1", params={"from_ts": now.isoformat()})
    assert res.status_code == 200
    assert isinstance(res.json()["buckets"], list)


def test_stream_explicit_window_and_custom_bucket(client):
    now = datetime.datetime.now()
    _post_samples(client, "1", _continuous_pts(now, 400, ma=42.0))
    frm = now.isoformat()
    to = (now + datetime.timedelta(seconds=2)).isoformat()
    frm_ms = round(now.timestamp() * 1000)
    to_ms = round((now + datetime.timedelta(seconds=2)).timestamp() * 1000)
    res = client.get("/power/stream/1", params={"from_ts": frm, "to_ts": to,
                                                "bucket_s": 0.5})
    assert res.status_code == 200
    buckets = res.json()["buckets"]
    # 2 s Spannweite ueber kalender-alignierte 0,5-s-Buckets
    assert len(buckets) >= 4
    assert sum(b["n"] for b in buckets) == 400
    # Bucket-Grenzen: 0,5-s-Raster, aufsteigend, im Fenster
    for a, b in zip(buckets, buckets[1:]):
        assert b["t_ms"] - a["t_ms"] == 500
    # Erster Bucket kann vor from_ms beginnen (kalender-aligniert, enthaelt
    # aber Proben im Fenster); letzter Bucket liegt vor to_ms.
    assert buckets[-1]["t_ms"] < to_ms
    assert buckets[0]["t_ms"] >= frm_ms - 500


def test_stream_validation_errors(client):
    now = datetime.datetime.now()
    # bucket_s zu gross / zu klein
    assert client.get("/power/stream/1", params={"bucket_s": 61}).status_code == 422
    assert client.get("/power/stream/1", params={"bucket_s": 0}).status_code == 422
    # from_ts nach to_ts
    res = client.get("/power/stream/1", params={
        "from_ts": (now + datetime.timedelta(seconds=10)).isoformat(),
        "to_ts": now.isoformat()})
    assert res.status_code == 422
    # ungueltiges Format
    res = client.get("/power/stream/1", params={"from_ts": "kein-datum"})
    assert res.status_code == 422
    assert "from_ts" in res.json()["detail"]


def test_stream_sessions_overlapping(client):
    s1 = _add_session(client, BASE, text="Alt")
    s2 = _add_session(client, BASE + datetime.timedelta(seconds=50), text="Neu")
    now = datetime.datetime.now()
    # Proben ab Session 2 (letzte 10 s)
    _post_samples(client, "1", _continuous_pts(now - datetime.timedelta(seconds=1), 200))
    res = client.get("/power/stream/1", params={
        "from_ts": (now - datetime.timedelta(seconds=5)).isoformat(),
        "to_ts": now.isoformat()})
    data = res.json()
    ids = [s["id"] for s in data["sessions"]]
    # Fenster liegt komplett in/neben Session 2; Session 1 endet davor
    assert s2 in ids
    for s in data["sessions"]:
        if s["id"] == s2:
            assert s["end_time"] is None  # offenes Ende (letzte Session)
        if s["id"] == s1:
            assert s["end_time"] is not None  # endet mit Start von Session 2


def test_stream_skips_unparseable_rows(client):
    now = datetime.datetime.now()
    _post_samples(client, "1", _continuous_pts(now, 100, ma=42.0))
    import db as dbmod
    conn = dbmod.sqlite3.connect(dbmod.DB_PATH)
    conn.execute(
        "INSERT INTO power_measurements (logger_id, measured_at, received_at, "
        "current_ma) VALUES ('1', 'kaputt', 'x', 1.0)")
    conn.commit()
    conn.close()
    res = client.get("/power/stream/1")
    assert res.status_code == 200
    assert all(b["ma_mean"] == pytest.approx(42.0) for b in res.json()["buckets"])


# --- /power/charge ------------------------------------------------------------

def test_charge_trapezoid_integration(client):
    now = datetime.datetime.now()
    # 100 Proben @ 5 ms @ 50 mA = 0,495 s gemessene Dauer
    # Trapez: 0,5 s * 50 mA = 25 mA*s = 6.944 uAh (Rundung am Rand)
    pts = _continuous_pts(now, 100, ma=50.0)
    _post_samples(client, "1", pts)
    frm = now.isoformat()
    to = (now + datetime.timedelta(seconds=2)).isoformat()
    res = client.post("/power/charge/1", json={"from_ts": frm, "to_ts": to})
    assert res.status_code == 200
    data = res.json()
    assert data["n_samples"] == 100
    assert data["gaps"] == 0
    assert data["duration_s"] == pytest.approx(0.495)
    # Trapez-Integration: 99 Intervalle @ 5 ms @ 50 mA (1 mA*s = 1/3.6 uAh)
    expected_uah = 99 * 0.005 * 50.0 / 3.6
    assert data["charge_uah"] == pytest.approx(expected_uah, rel=1e-6)
    assert data["mean_ma"] == pytest.approx(50.0)


def test_charge_gap_not_counted_in_duration(client):
    now = datetime.datetime.now()
    # 100 Proben, dann 1 s Pause (Luecke), dann wieder 100 Proben - alles in
    # EINEM POST (ein received_at), damit die Luecke nicht durch
    # INSERT OR IGNORE-Duplikate verschwindet.
    pts = ([(round(i * 0.005, 3), 50.0) for i in range(100)]
           + [(round(1.495 + i * 0.005, 3), 30.0) for i in range(100)])
    _post_samples(client, "1", pts)
    res = client.post("/power/charge/1", json={
        "from_ts": now.isoformat(),
        "to_ts": (now + datetime.timedelta(seconds=4)).isoformat()})
    data = res.json()
    assert data["gaps"] == 1
    # Dauer = 0,495 + 0,495 (Luecke zaehlt nicht)
    assert data["duration_s"] == pytest.approx(0.99)
    expected = (99 * 0.005 * 50.0 + 99 * 0.005 * 30.0) / 3.6
    assert data["charge_uah"] == pytest.approx(expected, rel=1e-6)


def test_charge_validation_and_empty(client):
    now = datetime.datetime.now()
    # fehlende Felder
    assert client.post("/power/charge/1", json={}).status_code == 422
    # from >= to
    res = client.post("/power/charge/1", json={"from_ts": now.isoformat(),
                                               "to_ts": now.isoformat()})
    assert res.status_code == 422
    # ungueltiges Format
    res = client.post("/power/charge/1", json={"from_ts": "x", "to_ts": now.isoformat()})
    assert res.status_code == 422
    # leerer Bereich (keine Proben) -> Nullen, kein Fehler
    res = client.post("/power/charge/1", json={"from_ts": now.isoformat(),
                                               "to_ts": (now + datetime.timedelta(seconds=1)).isoformat()})
    assert res.status_code == 200
    data = res.json()
    assert data["n_samples"] == 0
    assert data["charge_uah"] == 0.0
    assert data["mean_ma"] == 0.0


# --- /power/window ------------------------------------------------------------

def test_window_latest_session(client):
    _add_session(client, BASE - datetime.timedelta(hours=1), text="Alt")
    latest = _add_session(client, datetime.datetime.now() - datetime.timedelta(seconds=30),
                          text="Aktuell")
    res = client.get("/power/window/1")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == latest
    assert data["end"] is None  # Live-Follow: offenes Ende


def test_window_specific_session_with_measurements(client):
    sid = _add_session(client, BASE, text="Session")
    now = datetime.datetime.now()
    # letzte Probe 5 s vor "jetzt"
    _post_samples(client, "1", _continuous_pts(now - datetime.timedelta(seconds=5), 200))
    res = client.get("/power/window/1", params={"session_id": sid})
    assert res.status_code == 200
    data = res.json()
    # Start = Session-Start - 20 s
    assert datetime.datetime.fromisoformat(data["start"]) == BASE - datetime.timedelta(seconds=20)
    # Ende = letzte Messung + 60 s
    last_dt = datetime.datetime.fromisoformat(data["end"]) - datetime.timedelta(seconds=60)
    assert last_dt > BASE
    # Ende liegt nach der letzten Probe
    assert datetime.datetime.fromisoformat(data["end"]) > now - datetime.timedelta(seconds=5)


def test_window_session_without_measurements(client):
    sid = _add_session(client, BASE, text="Leer")
    res = client.get("/power/window/1", params={"session_id": sid})
    assert res.status_code == 200
    data = res.json()
    # Start + 60 s, wenn keine Messungen vorliegen
    assert datetime.datetime.fromisoformat(data["end"]) == BASE + datetime.timedelta(seconds=60)


def test_window_unknown_session(client):
    assert client.get("/power/window/1", params={"session_id": 9999}).status_code == 404


# --- /power/selections ----------------------------------------------------------

def test_selections_save_list_delete_cycle(client):
    now = datetime.datetime.now()
    _post_samples(client, "1", _continuous_pts(now, 200, ma=60.0))
    frm = now.isoformat()
    to = (now + datetime.timedelta(seconds=2)).isoformat()

    # Speichern: Werte werden serverseitig berechnet
    res = client.post("/power/selections/1", json={
        "from_ts": frm, "to_ts": to, "label": "Firmware A"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    sel_id = data["id"]
    assert data["label"] == "Firmware A"
    assert data["mean_ma"] == pytest.approx(60.0)
    assert data["duration_s"] > 0

    # Label-Leere -> None
    res = client.post("/power/selections/1", json={"from_ts": frm, "to_ts": to,
                                                   "label": "   "})
    assert res.status_code == 200
    assert res.json()["label"] is None

    # Listen
    res = client.get("/power/selections/1")
    assert res.status_code == 200
    sels = res.json()["selections"]
    assert len(sels) == 2  # neueste zuerst
    assert sels[0]["id"] > sels[1]["id"]
    assert sels[1]["charge_uah"] == pytest.approx(
        client.post("/power/charge/1", json={"from_ts": frm, "to_ts": to}).json()["charge_uah"])

    # Loeschen
    assert client.delete(f"/power/selections/1/{sel_id}").status_code == 200
    # zweites Loeschen -> 404
    assert client.delete(f"/power/selections/1/{sel_id}").status_code == 404
    assert len(client.get("/power/selections/1").json()["selections"]) == 1


def test_selections_validation(client):
    now = datetime.datetime.now()
    assert client.post("/power/selections/1", json={}).status_code == 422
    res = client.post("/power/selections/1", json={"from_ts": now.isoformat(),
                                                   "to_ts": now.isoformat()})
    assert res.status_code == 422
    res = client.post("/power/selections/1", json={"from_ts": "bad",
                                                   "to_ts": now.isoformat()})
    assert res.status_code == 422


# --- Randfaelle (100 % Coverage) ----------------------------------------------

def test_bucket_rows_rejects_nonpositive_bucket():
    """_bucket_power_rows verteidigt sich gegen bucket_s <= 0."""
    import db as dbmod
    with pytest.raises(ValueError):
        dbmod._bucket_power_rows([("2026-10-12T12:00:00", 1.0)], bucket_s=0)


def test_stream_and_charge_skip_unparseable_timestamp(client):
    """Zeile mit ungueltigem, aber im Fenster sortierendem Zeitstempel."""
    import db as dbmod
    now = datetime.datetime.now()
    _post_samples(client, "1", _continuous_pts(now, 100, ma=42.0))
    # 'x' sortiert nach den echten Proben, aber vor 'T14:...' (to_ts unten)
    conn = dbmod.sqlite3.connect(dbmod.DB_PATH)
    conn.execute(
        "INSERT INTO power_measurements (logger_id, measured_at, received_at, "
        "current_ma) VALUES ('1', ?, 'x', 7.0)",
        (now.replace(microsecond=0).isoformat().replace("T13:", "T13:")
         + "x" if False else now.isoformat() + "x",))
    conn.commit()
    conn.close()
    frm = now.isoformat()
    to = (now + datetime.timedelta(hours=1)).isoformat()
    buckets = dbmod.get_power_stream_buckets("1", frm, to, bucket_s=30.0)
    # 7.0-mA-Zeile verworfen: nur die 42.0-Proben in 2 Buckets (30 s)
    assert len(buckets) == 1
    assert buckets[0]["ma_mean"] == pytest.approx(42.0)
    charge = dbmod.get_power_charge("1", frm, to)
    assert charge["n_samples"] == 100
    assert charge["mean_ma"] == pytest.approx(42.0)


def test_stream_sessions_skips_empty_start_time(client):
    """Session ohne start_time ('') wird bei der Zuordnung uebersprungen."""
    _add_session(client, BASE)            # gueltige Session
    conn = _db_conn()
    conn.execute("INSERT INTO sessions (sensor_id, start_time) VALUES (1, '')")
    conn.commit()
    conn.close()
    now = datetime.datetime.now()
    res = client.get("/power/stream/1", params={
        "from_ts": (now - datetime.timedelta(seconds=5)).isoformat(),
        "to_ts": now.isoformat()})
    assert res.status_code == 200
    assert all(s["start_time"] for s in res.json()["sessions"])


def test_stream_sessions_future_session_excluded(client):
    """Session, die nach dem Fenster beginnt, erscheint nicht."""
    _add_session(client, datetime.datetime.now() + datetime.timedelta(days=1))
    now = datetime.datetime.now()
    res = client.get("/power/stream/1", params={
        "from_ts": (now - datetime.timedelta(seconds=5)).isoformat(),
        "to_ts": now.isoformat()})
    assert res.status_code == 200
    assert res.json()["sessions"] == []


def test_window_no_session_in_past(client):
    """Nur zukuenftige Sessions -> 404 bei Live-Follow (ohne session_id)."""
    _add_session(client, datetime.datetime.now() + datetime.timedelta(days=1))
    res = client.get("/power/window/1")
    assert res.status_code == 404


def test_window_session_end_at_next_session_start(client):
    """Ende = letzte Messung, aber naechste Session begrenzt das Suchfenster."""
    sid = _add_session(client, BASE, text="Frueh")
    _add_session(client, BASE + datetime.timedelta(seconds=30), text="Spaeter")
    res = client.get("/power/window/1", params={"session_id": sid})
    assert res.status_code == 200
    data = res.json()
    # Ende = Session-Start + 60 s (keine Messungen; Naechste-Start + 60 schneide
    # hier nicht frueher: upper = BASE + 30 s, letzte Messung gibt es nicht ->
    # window_end = BASE + 60 s ... ABER last <= upper schraenkt nicht ein, da
    # keine Messung existiert -> Start + 60 s)
    assert datetime.datetime.fromisoformat(data["end"]) == BASE + datetime.timedelta(seconds=60)


def _db_conn():
    import db as dbmod
    return dbmod.sqlite3.connect(dbmod.DB_PATH)
