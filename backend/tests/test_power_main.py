"""Tests fuer die Strom-Zyklen-Endpunkte in main.py (/power/cycles/...)."""

import datetime

import pytest

import power_cycles


BASE = datetime.datetime(2026, 10, 12, 12, 0, 0)

# Gesamtdauer eines synthetischen Profils (5 s Grundlast + Burst + Rest)
PROFILE_DURATION_S = 10.0


def _synthetic_profile(wlan_duration_s: float = 0.8, offset_s: float = 0.0) -> list[tuple[float, float]]:
    """
    Ein Zyklus (echtes Profil nachgebaut): 5 s Grundlast (~74 mA), TX-Burst
    (~135 mA, 0.2 + wlan_duration_s), Grundlast bis Zyklusende = 10 s.
    Sample-Raster 5 ms (wie Firmware).
    """
    pts = []
    t = 0.0
    dt = 0.005

    def phase(duration, ma):
        nonlocal t
        for _ in range(int(duration / dt)):
            pts.append((round(t + offset_s, 3), ma))
            t += dt

    phase(5.0, 74.5)                       # Grundlast-Vorlauf
    phase(0.2 + wlan_duration_s, 135.0)    # TX-Burst (Trigger + Rest)
    phase(10.0 - 5.0 - 0.2 - wlan_duration_s, 74.5)  # Grundlast nach Burst
    return pts


def _profile_chain(count: int, wlan_duration_s: float = 0.8) -> list[tuple[float, float]]:
    """`count` Zyklen hintereinander, ohne ueberlappende Zeitstempel."""
    pts = []
    offset = 0.0
    for _ in range(count):
        pts += _synthetic_profile(wlan_duration_s, offset_s=offset)
        offset += PROFILE_DURATION_S
    return pts


def _post_samples(client, logger_id: str, pts: list[tuple[float, float]]):
    res = client.post("/power/measurements", json={
        "logger_id": logger_id,
        "currents_ma": [ma for _, ma in pts],
        "timestamps": [t for t, _ in pts],
        "range_ma": 100.0,
    })
    assert res.json()["status"] == "success"


# ── GET /power/cycles/{logger_id} ────────────────────────────────────────

def test_power_cycles_empty(client):
    res = client.get("/power/cycles/unbekannt")
    assert res.json() == {"cycles": [], "stats": {}}


def test_power_cycles_detects_phases(client):
    _post_samples(client, "1", _synthetic_profile())
    data = client.get("/power/cycles/1").json()
    assert len(data["cycles"]) == 1

    cycle = data["cycles"][0]
    assert cycle["peak_ma"] == 135.0
    assert cycle["truncated"] is False

    # Landmarks sind relativ zum Zyklusanfang (Trigger bei pre_peak_s = 5.0)
    lm = cycle["landmarks"]
    assert lm["trigger"]["start_s"] == 5.0
    # spike_end = Burst-Ende (Trigger + 1.0 s Burst) = 6.0 s nach Zyklusanfang
    assert 5.9 <= lm["spike_end"]["start_s"] <= 6.1

    # stats: Median der Spike-Phase ~ 1.0 s (nur ein Zyklus)
    assert data["stats"]["spike"]["median_s"] == pytest.approx(1.0, abs=0.05)


def test_power_cycles_threshold_override(client):
    """peak_trigger_ma ueber Peak -> Zyklus wird nicht mehr erkannt."""
    _post_samples(client, "2", _synthetic_profile())
    data = client.get(
        "/power/cycles/2", params={"peak_trigger_ma": 1000.0}
    ).json()
    assert data["cycles"] == []


# ── GET /power/cycles/{logger_id}/overlay ────────────────────────────────

def test_power_cycles_overlay(client):
    pts = _profile_chain(1) + _synthetic_profile(
        wlan_duration_s=2.5, offset_s=PROFILE_DURATION_S
    )
    _post_samples(client, "3", pts)
    data = client.get("/power/cycles/3/overlay").json()
    # Beide Zyklen synchronisiert auf t=0 (Zyklusanfang)
    assert len(data["cycles"]) == 2
    first = data["cycles"][0]
    assert first["t_s"][0] == 0.0
    assert first["ma"][0] == 74.5
    assert first["landmarks"]["trigger"]["start_s"] == 5.0
    # Start-Timestamps unterscheiden sich (zweiter Zyklus spaeter)
    assert data["cycles"][1]["start"] != first["start"]
    # Laengere Burst-Phase im zweiten Zyklus sichtbar
    assert data["cycles"][1]["phases"]["spike"]["duration_s"] == pytest.approx(2.7, abs=0.05)


def test_power_cycles_overlay_limit(client):
    pts = _profile_chain(5)
    _post_samples(client, "4", pts)
    data = client.get(
        "/power/cycles/4/overlay", params={"overlay_limit": 2}
    ).json()
    assert len(data["cycles"]) == 2


def test_power_cycles_overlay_empty(client):
    data = client.get("/power/cycles/unbekannt/overlay").json()
    assert data["cycles"] == []


# ── GET /power/loggers ───────────────────────────────────────────────────

def test_power_loggers_sorted_by_latest(client):
    _post_samples(client, "2", _synthetic_profile(offset_s=100.0))
    _post_samples(client, "1", _synthetic_profile())
    res = client.get("/power/loggers")
    # Neueste Aktivitaet zuerst
    assert res.json()["loggers"] == ["2", "1"]


def test_power_loggers_empty(client):
    assert client.get("/power/loggers").json() == {"loggers": []}


# ── GET /power/sessions/{logger_id} (Firmware-Versionsvergleich) ─────────

def _create_sessions(client, specs: list[tuple[str, str | None]]):
    """
    Legt Sessions an (ueber den existierenden Endpoint /update/{sensor_id} -
    genau der Weg, wie auch die Firmware eine neue Version startet) und gibt
    die neu erstellten Session-IDs zurueck.

    specs: [(sensor_id, custom_text-oder-None), ...] in Reihenfolge.
    """
    out = []
    for sensor_id, custom_text in specs:
        if custom_text is not None:
            client.post("/custom-text", json={"custom_text": custom_text})
        res = client.post(f"/update/{sensor_id}").json()
        session_id = int(res["new_sensor_session"].split("_", 1)[1])
        out.append({"id": session_id, "sensor_id": sensor_id})
    return out


def test_power_sessions_assignment_and_summary(client):
    # Zwei Sessions: Version A, dann Version B (neuere Firmware).
    created = _create_sessions(client, [("1", None), (None, None)])
    assert len(created) == 2
    # Beide Zyklen liegen NACH beiden Session-Starts (Server setzt
    # measured_at = Empfangszeit + Alter) -> alle gehoeren zur neuesten.
    _post_samples(client, "1", _profile_chain(2))

    data = client.get("/power/sessions/1").json()
    assert len(data["cycles"]) == 2
    assert all("session_id" in c for c in data["cycles"])
    assert all(c["session_id"] == created[-1]["id"] for c in data["cycles"])

    # Zusammenfassung: eine Gruppe (neueste Session), Gesamtverbrauch =
    # Summe der Zyklus-Verbraeuche, Mediane plausibel
    assert len(data["sessions"]) == 1
    s = data["sessions"][0]
    assert s["session_id"] == created[-1]["id"]
    assert s["n_cycles"] == 2
    assert s["n_cycles_complete"] == 2
    total = round(sum(c["charge_uah"] for c in data["cycles"]), 4)
    assert s["charge_uah_total"] == total
    assert s["charge_uah_median"] is not None and s["charge_uah_median"] > 0
    assert s["duration_median_s"] is not None and s["duration_median_s"] > 0


def test_power_sessions_unit_assignment(client):
    """Unit: Zuordnungsregel 'spaeteste start_time <= Zyklusstart'."""
    sessions = [
        {"id": 10, "start_time": "2026-10-12T12:00:00"},
        {"id": 11, "start_time": "2026-10-12T13:00:00"},
    ]
    cycles = [
        {"start": "2026-10-12T11:00:00"},   # vor aller ersten -> None
        {"start": "2026-10-12T12:30:00"},   # in Session 10
        {"start": "2026-10-12T13:30:00"},   # in Session 11
        {"start": "2026-10-12T12:59:59"},   # knapp vor Session 11 -> 10
    ]
    out = power_cycles.assign_sessions_to_cycles(cycles, sessions)
    assert [c["session_id"] for c in out] == [None, 10, 11, 10]


def test_power_sessions_empty(client):
    data = client.get("/power/sessions/unbekannt").json()
    assert data["cycles"] == []
    assert data["sessions"] == []
    assert data["stats"] == {}


def test_power_sessions_threshold_override(client):
    """Alle Schwellwert-Parameter werden an die Analyse durchgereicht."""
    _post_samples(client, "8", _profile_chain(1))
    # peak_trigger_ma ueber Peak -> kein Zyklus mehr erkannt
    data = client.get("/power/sessions/8", params={
        "peak_trigger_ma": 1000.0, "pre_peak_s": 1.0, "refractory_s": 0.5,
        "spike_end_ma": 85.0, "max_cycle_s": 30.0, "gap_s": 2.0,
    }).json()
    assert data["cycles"] == []
    assert data["sessions"] == []


def test_power_sessions_cycle_has_session_id(client):
    """Auch ohne Sessions in der DB: Feld session_id muss existieren (None)."""
    _post_samples(client, "9", _profile_chain(1))
    data = client.get("/power/sessions/9").json()
    assert len(data["cycles"]) == 1
    assert data["cycles"][0]["session_id"] is None
    assert data["sessions"][0]["session_id"] is None
    assert data["sessions"][0]["n_cycles"] == 1


# ── Overlay-Analyse-Cache (Zoom-Nachladen im ms-Bereich) ─────────────────

def test_overlay_cache_hit_is_fast_and_fresh(client, monkeypatch):
    """Zweiter Overlay-Request innerhalb der TTL: Cache-Hit, identisches Ergebnis.

    Der Cache-Hit-Pfad enthaelt kein get_power_measurements und keine
    Vollanalyse - geprueft ueber einen Counter-Mock.
    """
    import main as mainmod
    pts = _profile_chain(3)
    _post_samples(client, "7", pts)

    calls = {"n": 0}
    orig = mainmod.get_power_measurements

    def counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(mainmod, "get_power_measurements", counting)
    # Cache leeren (globales Dict), damit der Test deterministisch ist
    mainmod._overlay_cache.clear()

    first = client.get("/power/cycles/7/overlay").json()
    assert calls["n"] == 1
    second = client.get("/power/cycles/7/overlay").json()
    assert calls["n"] == 1  # Cache-Hit: keine erneute Messwert-Abfrage
    assert second["cycles"] == first["cycles"]

    # Zoom-Fenster auf Cache-Hit rendern (nur Fensterschnitt + LTTB)
    win = client.get("/power/cycles/7/overlay",
                     params={"window_start_s": 4.0, "window_end_s": 6.0}).json()
    assert win["cycles"][0]["t_s"][0] == 0.0
    assert win["cycles"][0]["landmarks"]["trigger"]["start_s"] == pytest.approx(1.0, abs=0.01)


def test_overlay_cache_invalidated_on_new_data(client):
    """Neue Proben innerhalb der TTL -> Fingerprint aendert sich -> Neuberechnung."""
    import main as mainmod
    mainmod._overlay_cache.clear()
    _post_samples(client, "8", _synthetic_profile())
    first = client.get("/power/cycles/8/overlay").json()
    assert len(first["cycles"]) == 1

    # Neue Proben anhaengen -> zweiter Zyklus -> Cache muss aktualisieren
    pts = _synthetic_profile()
    offset = 10.0
    _post_samples(client, "8", [(t + offset, ma) for t, ma in pts])
    second = client.get("/power/cycles/8/overlay").json()
    assert len(second["cycles"]) == 2


def test_overlay_cache_ttl_expiry(client, monkeypatch):
    """TTL abgelaufen -> Fingerprint wird geprueft, bei Gleichheit weiterverwendet."""
    import main as mainmod
    mainmod._overlay_cache.clear()
    _post_samples(client, "9", _synthetic_profile())
    client.get("/power/cycles/9/overlay").json()

    # TTL kuenstlich ablaufen lassen: ts zuruecksetzen
    for entry in mainmod._overlay_cache.values():
        entry["ts"] -= mainmod._OVERLAY_CACHE_TTL_S + 1.0

    # Fingerprint gleich -> Cache wird trotzdem weiterverwendet (Abfrage ok)
    data = client.get("/power/cycles/9/overlay").json()
    assert len(data["cycles"]) == 1


def test_power_fingerprint_none_logger(client):
    """Fingerprint fuer unbekannten Logger -> (None, 0)."""
    import main as mainmod
    fp = mainmod._power_fingerprint("gibtsnicht")
    assert fp == (None, 0)


def test_overlay_cache_eviction(client):
    """Mehr als 8 Eintraege -> Cache wird geleert (Groessenbegrenzung)."""
    import main as mainmod
    mainmod._overlay_cache.clear()
    for i in range(9):
        _post_samples(client, f"e{i}", _synthetic_profile())
        client.get(f"/power/cycles/e{i}/overlay").json()
    assert len(mainmod._overlay_cache) <= 8
