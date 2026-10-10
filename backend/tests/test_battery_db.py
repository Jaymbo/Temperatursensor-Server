"""Tests fuer die Akku-/Strom-Zugriffsfunktionen in db.py.

Nutzt das `client`-Fixture (conftest.py), damit DB_PATH auf eine isolierte
Temp-DB gepatcht und das Schema initialisiert ist.
"""
import sqlite3

import db as dbmod


def _insert_session(sensor_id: str, start_time_iso: str, id_: int, hw_rev=None) -> None:
    conn = sqlite3.connect(dbmod.DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO sessions (id, sensor_id, start_time, calibration_data, hw_rev) "
        "VALUES (?, ?, ?, ?, ?)",
        (id_, sensor_id, start_time_iso, "100,3.9083e-3,-5.775e-7,3.3,10000", hw_rev),
    )
    conn.commit()
    conn.close()


def _reading(sensor_id, measured_at, mv, **kw):
    row = {
        "sensor_id": sensor_id,
        "battery_id": kw.get("battery_id"),
        "measured_at": measured_at,
        "received_at": kw.get("received_at", measured_at),
        "mv": mv,
        "raw": kw.get("raw"),
        "under_load": kw.get("under_load"),
        "seq": kw.get("seq"),
        "hw_rev": kw.get("hw_rev"),
    }
    return row


# ── Plausibilitaetsgrenzen ────────────────────────────────────────────────

def test_is_valid_battery_mv():
    assert dbmod._is_valid_battery_mv(3150) is True
    assert dbmod._is_valid_battery_mv(2500) is True
    assert dbmod._is_valid_battery_mv(4500) is True
    assert dbmod._is_valid_battery_mv(2499) is False
    assert dbmod._is_valid_battery_mv(4501) is False
    assert dbmod._is_valid_battery_mv(None) is False
    assert dbmod._is_valid_battery_mv("abc") is False


# ── hw_rev an der Session ────────────────────────────────────────────────

def test_session_hw_rev_none_and_set(client):
    _insert_session("1", "2026-01-01T00:00:00", 1)
    # Ohne hw_rev -> None (Altbestand, darf nicht als Konflikt gelten)
    assert dbmod.get_session_hw_rev("1") is None
    dbmod.set_session_hw_rev("1", 2)
    assert dbmod.get_session_hw_rev("1") == 2


def test_session_hw_rev_missing_sensor(client):
    assert dbmod.get_session_hw_rev("gibtsnicht") is None


# ── battery_assignments ───────────────────────────────────────────────────

def test_battery_assignment_lifecycle(client):
    assert dbmod.get_battery_assignment("1") is None
    dbmod.add_battery_assignment("1", "B1", "2026-01-01T00:00:00", reason="initial")
    current = dbmod.get_battery_assignment("1")
    assert current["battery_id"] == "B1"
    assert current["to_time"] is None

    # Zeitpunktbasierte Abfrage (innerhalb des offenen Intervalls)
    at = dbmod.get_battery_assignment("1", at_time="2026-01-02T00:00:00")
    assert at["battery_id"] == "B1"

    # Wechsel: offene Zuordnung schliessen, neue anlegen
    dbmod.close_open_battery_assignment("1", "2026-02-01T00:00:00")
    dbmod.add_battery_assignment("1", "B2", "2026-02-01T00:00:00", reason="swap")
    assert dbmod.get_battery_assignment("1")["battery_id"] == "B2"

    # Abfrage in der Vergangenheit trifft noch B1
    past = dbmod.get_battery_assignment("1", at_time="2026-01-15T00:00:00")
    assert past["battery_id"] == "B1"


def test_battery_assignment_unknown_time(client):
    dbmod.add_battery_assignment("9", "B1", "2026-01-01T00:00:00")
    assert dbmod.get_battery_assignment("9", at_time="2025-01-01T00:00:00") is None


# ── battery_readings ──────────────────────────────────────────────────────

def test_add_and_get_battery_readings(client):
    rows = [
        _reading("1", "2026-01-01T00:00:00", 4100, raw=100, seq=1, hw_rev=2),
        _reading("1", "2026-01-01T00:01:00", 4050, raw=99, seq=2, hw_rev=2),
        _reading("1", "2026-01-01T00:02:00", 9999),  # unplausibel -> verworfen
    ]
    inserted = dbmod.add_battery_readings(rows)
    assert inserted == 2

    readings = dbmod.get_battery_readings("1")
    assert [r["mv"] for r in readings] == [4100, 4050]
    assert readings[0]["raw"] == 100
    assert readings[0]["under_load"] is None
    assert dbmod.get_latest_battery_reading("1")["mv"] == 4050
    assert dbmod.get_battery_sensor_ids() == ["1"]


def test_add_battery_readings_empty_and_all_invalid(client):
    assert dbmod.add_battery_readings([]) == 0
    assert dbmod.add_battery_readings([_reading("1", "2026-01-01T00:00:00", 100)]) == 0


def test_battery_readings_since_and_limit(client):
    dbmod.add_battery_readings([
        _reading("1", "2026-01-01T00:00:00", 4000, under_load=True),
        _reading("1", "2026-01-02T00:00:00", 3900),
        _reading("1", "2026-01-03T00:00:00", 3800),
    ])
    since = dbmod.get_battery_readings("1", since="2026-01-02T00:00:00")
    assert [r["mv"] for r in since] == [3900, 3800]
    limited = dbmod.get_battery_readings("1", limit=1)
    assert [r["mv"] for r in limited] == [4000]
    # under_load wird als bool zurueckgegeben
    all_readings = dbmod.get_battery_readings("1")
    assert all_readings[0]["under_load"] is True


def test_latest_battery_reading_none(client):
    assert dbmod.get_latest_battery_reading("gibtsnicht") is None


def test_get_all_battery_readings(client):
    dbmod.add_battery_readings([
        _reading("1", "2026-01-01T00:00:00", 4000),
        _reading("1", "2026-01-02T00:00:00", 3900),
        _reading("2", "2026-01-01T00:00:00", 4100),
    ])
    allr = dbmod.get_all_battery_readings()
    assert len(allr) == 3
    assert all(r["sensor_id"] for r in allr)

    limited = dbmod.get_all_battery_readings(limit_per_sensor=1)
    assert len(limited) == 2  # je Sensor nur der letzte Wert

    since = dbmod.get_all_battery_readings(since="2026-01-02T00:00:00")
    assert len(since) == 1


# ── batteries (Stammdaten) ────────────────────────────────────────────────

def test_upsert_and_get_batteries(client):
    assert dbmod.get_batteries() == []
    dbmod.upsert_battery("B1", capacity_class="2000mAh",
                          purchase_date="2025-01-01", notes="neu")
    dbmod.upsert_battery("B1", capacity_class="1800mAh", status="retired",
                          purchase_date="2025-01-01", notes="alt")
    batteries = dbmod.get_batteries()
    assert len(batteries) == 1
    assert batteries[0]["capacity_class"] == "1800mAh"
    assert batteries[0]["status"] == "retired"
    assert batteries[0]["notes"] == "alt"


# ── battery_sessions ──────────────────────────────────────────────────────

def test_replace_and_get_battery_sessions(client):
    dbmod.replace_battery_sessions("1", [
        {"battery_id": "B1", "start_time": "2026-01-01T00:00:00",
         "end_time": "2026-01-01T06:00:00", "type": "measure", "note": None},
        {"battery_id": "B1", "start_time": "2026-01-01T06:00:00",
         "end_time": "2026-01-02T00:00:00", "type": "charge", "note": "+100 mV"},
    ])
    sessions = dbmod.get_battery_sessions("1")
    assert len(sessions) == 2
    assert sessions[0]["type"] == "measure"
    assert sessions[1]["note"] == "+100 mV"

    # Ersetzen loescht die alten Phasen
    dbmod.replace_battery_sessions("1", [
        {"battery_id": None, "start_time": "2026-02-01T00:00:00",
         "end_time": None, "type": "outage", "note": None},
    ])
    assert len(dbmod.get_battery_sessions("1")) == 1


# ── Strommessung ─────────────────────────────────────────────────────────

def test_add_power_measurements(client):
    assert dbmod.add_power_measurements([]) == 0
    rows = [
        {"logger_id": "1", "measured_at": "2026-01-01T00:00:00",
         "received_at": "2026-01-01T00:00:01", "current_ma": 12.5,
         "bus_v": 3.9, "shunt_mv": 1.2, "range_ma": 100.0, "seq": 1},
        {"logger_id": "1", "measured_at": "2026-01-01T00:00:01",
         "received_at": "2026-01-01T00:00:02", "current_ma": 0.05},
    ]
    assert dbmod.add_power_measurements(rows) == 2
    measurements = dbmod.get_power_measurements("1")
    assert len(measurements) == 2
    assert measurements[0]["current_ma"] == 12.5
    assert measurements[1]["bus_v"] is None

    limited = dbmod.get_power_measurements("1", limit=1)
    assert len(limited) == 1
    since = dbmod.get_power_measurements("1", since="2026-01-01T00:00:01")
    assert len(since) == 1


def test_add_and_get_power_aggregates(client):
    dbmod.add_power_aggregate({
        "logger_id": "1",
        "window_start": "2026-01-01T00:00:00",
        "window_end": "2026-01-01T00:01:00",
        "samples": 60,
        "current_ma_mean": 5.0,
        "current_ma_min": 0.1,
        "current_ma_max": 30.0,
        "charge_uah": 1.5,
        "gap": True,
        "received_at": "2026-01-01T00:01:01",
    })
    aggregates = dbmod.get_power_aggregates("1")
    assert len(aggregates) == 1
    assert aggregates[0]["gap"] is True
    assert aggregates[0]["charge_uah"] == 1.5

    since = dbmod.get_power_aggregates("1", since="2026-01-02T00:00:00")
    assert since == []


def test_power_measurements_empty_for_unknown_logger(client):
    assert dbmod.get_power_measurements("unbekannt") == []
    assert dbmod.get_power_aggregates("unbekannt") == []
