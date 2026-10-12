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
    data = client.get("/power/cycles/3/overlay", params={"mode": "cycles"}).json()
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
        "/power/cycles/4/overlay", params={"overlay_limit": 2, "mode": "cycles"}
    ).json()
    assert len(data["cycles"]) == 2


def test_power_cycles_overlay_empty(client):
    data = client.get("/power/cycles/unbekannt/overlay", params={"mode": "cycles"}).json()
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

    Der Cache haelt nur die ANALYSE (RAM-Design), die Rohproben werden je
    Request frisch geladen. Geprueft ueber einen Counter-Mock: Kein zweiter
    Vollanalyse-Lauf, aber Rohproben-Abfrage bleibt (bewusst) drin.
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

    first = client.get("/power/cycles/7/overlay", params={"mode": "cycles"}).json()
    assert calls["n"] == 1
    second = client.get("/power/cycles/7/overlay", params={"mode": "cycles"}).json()
    assert calls["n"] == 2  # Rohproben werden je Request frisch geladen
    assert second["cycles"] == first["cycles"]

    # Zoom-Fenster auf Cache-Hit rendern (nur Fensterschnitt + LTTB)
    win = client.get("/power/cycles/7/overlay",
                     params={"mode": "cycles", "window_start_s": 4.0, "window_end_s": 6.0}).json()
    assert win["cycles"][0]["t_s"][0] == 0.0
    assert win["cycles"][0]["landmarks"]["trigger"]["start_s"] == pytest.approx(1.0, abs=0.01)


def test_overlay_cache_invalidated_on_new_data(client):
    """Neue Proben innerhalb der TTL -> Fingerprint aendert sich -> Neuberechnung."""
    import main as mainmod
    mainmod._overlay_cache.clear()
    _post_samples(client, "8", _synthetic_profile())
    first = client.get("/power/cycles/8/overlay", params={"mode": "cycles"}).json()
    assert len(first["cycles"]) == 1

    # Neue Proben anhaengen -> zweiter Zyklus -> Cache muss aktualisieren
    pts = _synthetic_profile()
    offset = 10.0
    _post_samples(client, "8", [(t + offset, ma) for t, ma in pts])
    second = client.get("/power/cycles/8/overlay", params={"mode": "cycles"}).json()
    assert len(second["cycles"]) == 2


def test_overlay_cache_ttl_expiry(client, monkeypatch):
    """TTL abgelaufen -> Fingerprint wird geprueft, bei Gleichheit weiterverwendet."""
    import main as mainmod
    mainmod._overlay_cache.clear()
    _post_samples(client, "9", _synthetic_profile())
    client.get("/power/cycles/9/overlay", params={"mode": "cycles"}).json()

    # TTL kuenstlich ablaufen lassen: ts zuruecksetzen
    for entry in mainmod._overlay_cache.values():
        entry["ts"] -= mainmod._OVERLAY_CACHE_TTL_S + 1.0

    # Fingerprint gleich -> Cache wird trotzdem weiterverwendet (Abfrage ok)
    data = client.get("/power/cycles/9/overlay", params={"mode": "cycles"}).json()
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
        client.get(f"/power/cycles/e{i}/overlay", params={"mode": "cycles"}).json()
    assert len(mainmod._overlay_cache) <= 8


def test_invalidate_power_caches_on_new_samples(client):
    """Insert neuer Rohproben leert beide Power-Caches aktiv (RAM-Freigabe)."""
    import main as mainmod
    mainmod._overlay_cache.clear()
    mainmod._session_avg_cache.clear()
    _post_samples(client, "inv1", _profile_chain(1))
    client.get("/power/cycles/inv1/overlay", params={"mode": "cycles"}).json()
    client.get("/power/cycles/inv1/overlay").json()  # avg-Modus
    assert len(mainmod._overlay_cache) == 1
    assert len(mainmod._session_avg_cache) == 1

    # Neuer Insert -> Caches muessen geleert sein (nicht nur ungueltig).
    _post_samples(client, "inv1", _synthetic_profile(offset_s=PROFILE_DURATION_S))
    assert mainmod._overlay_cache == {}
    assert mainmod._session_avg_cache == {}


def test_overlay_cache_fingerprint_mismatch_recomputes(client):
    """Fingerprint-Abweichung ohne Insert-Invalidierung -> Pop + Neuberechnung.

    Der Insert-Endpoint leert den Cache aktiv; dieser defensive Pfad (Daten
    haben sich auf anderem Weg geaendert) wird direkt ueber einen manipulierten
    Fingerprint geprueft.
    """
    import main as mainmod
    mainmod._overlay_cache.clear()
    _post_samples(client, "fm1", _profile_chain(1))
    client.get("/power/cycles/fm1/overlay", params={"mode": "cycles"}).json()
    assert len(mainmod._overlay_cache) == 1
    for entry in mainmod._overlay_cache.values():
        entry["fingerprint"] = ("x", 0)  # abweichend zum echten Fingerprint

    data = client.get("/power/cycles/fm1/overlay", params={"mode": "cycles"}).json()
    # Pop-Pfad lief: Eintrag neu berechnet (wieder valide, Zyklus erkannt).
    assert len(data["cycles"]) == 1
    assert len(mainmod._overlay_cache) == 1
    for entry in mainmod._overlay_cache.values():
        assert entry["fingerprint"] != ("x", 0)


def test_session_avg_cache_fingerprint_mismatch_recomputes(client):
    """Wie test_overlay_cache_fingerprint_mismatch_recomputes, avg-Cache."""
    import main as mainmod
    mainmod._session_avg_cache.clear()
    _post_samples(client, "fm2", _profile_chain(1))
    client.get("/power/cycles/fm2/overlay").json()
    assert len(mainmod._session_avg_cache) == 1
    for entry in mainmod._session_avg_cache.values():
        entry["fingerprint"] = ("x", 0)

    data = client.get("/power/cycles/fm2/overlay").json()
    assert len(data["avg"]) == 1
    for entry in mainmod._session_avg_cache.values():
        assert entry["fingerprint"] != ("x", 0)


def test_overlay_cache_eviction_on_third_key(client):
    """Dritter Cache-Key bei Limit 2 -> while-Loop leert den Cache."""
    import main as mainmod
    mainmod._overlay_cache.clear()
    _post_samples(client, "ev1", _synthetic_profile())
    client.get("/power/cycles/ev1/overlay", params={"mode": "cycles"}).json()
    # Zwei Keys OHNE Insert dazwischen (Insert wuerde aktiv leeren):
    client.get("/power/cycles/ev1/overlay",
               params={"mode": "cycles", "peak_trigger_ma": 200.0}).json()
    assert len(mainmod._overlay_cache) == 2
    # Dritter Key -> Eviction (while len >= 2: clear).
    client.get("/power/cycles/ev1/overlay",
               params={"mode": "cycles", "peak_trigger_ma": 300.0}).json()
    assert len(mainmod._overlay_cache) == 1


def test_session_avg_cache_eviction_on_third_key(client):
    """Dritter avg-Cache-Key bei Limit 2 -> Eviction-Clear."""
    import main as mainmod
    mainmod._session_avg_cache.clear()
    _post_samples(client, "ev3", _profile_chain(1))
    client.get("/power/cycles/ev3/overlay").json()
    client.get("/power/cycles/ev3/overlay",
               params={"window_start_s": 0.0, "window_end_s": 5.0}).json()
    assert len(mainmod._session_avg_cache) == 2
    client.get("/power/cycles/ev3/overlay",
               params={"window_start_s": 0.0, "window_end_s": 4.0}).json()
    assert len(mainmod._session_avg_cache) == 1


# ── Overlay mode="avg" (Session-Durchschnitt, Standard-Ansicht) ───────────

def _create_sessions(client, specs: list[tuple[str, str | None]]):
    """Sessions ueber /update/{sensor_id} anlegen (wie Firmware-Release)."""
    out = []
    for sensor_id, custom_text in specs:
        if custom_text is not None:
            client.post("/custom-text", json={"custom_text": custom_text})
        res = client.post(f"/update/{sensor_id}").json()
        session_id = int(res["new_sensor_session"].split("_", 1)[1])
        out.append({"id": session_id, "sensor_id": sensor_id})
    return out


def test_overlay_avg_mode_default(client):
    """Default-Modus 'avg': Mittelwert + std je Session statt Einzelzyklen."""
    pts = _profile_chain(3)
    _post_samples(client, "a1", pts)
    data = client.get("/power/cycles/a1/overlay").json()
    assert "avg" in data and "cycles" not in data
    assert len(data["avg"]) == 1
    s = data["avg"][0]
    assert s["session_id"] is None  # keine Sessions angelegt -> alle None
    assert s["n_cycles"] == 3
    assert len(s["t_s"]) == len(s["mean"]) == len(s["std"])
    # Grundlast-Mittelwert ~74.5, Spike-Mittelwert ~135: Peak bleibt sichtbar.
    assert max(s["mean"]) == pytest.approx(135.0, abs=2.0)
    assert s["std"][0] == 0.0  # identische Zyklen -> keine Streuung am Anfang
    # Landmarks aus dem Median-Zyklus mitgeliefert
    assert s["landmarks"]["trigger"]["start_s"] == pytest.approx(5.0, abs=0.05)


def test_overlay_avg_mode_std_band_with_outlier(client):
    """Abweichender Zyklus zeigt sich als std > 0 im Band."""
    pts = _profile_chain(2)
    # Dritter Zyklus mit laengerem Burst -> Streuung in der Spike-Phase
    pts += _synthetic_profile(wlan_duration_s=2.5, offset_s=2 * PROFILE_DURATION_S)
    _post_samples(client, "a2", pts)
    data = client.get("/power/cycles/a2/overlay").json()
    s = data["avg"][0]
    assert s["n_cycles"] == 3
    # In der Spike-Phase (t ~5..7 s) Streuung, am Start (Grundlast) keine.
    idx_spike = min(range(len(s["t_s"])), key=lambda i: abs(s["t_s"][i] - 6.0))
    assert s["std"][idx_spike] > 0.5
    assert s["std"][0] == 0.0


def test_overlay_avg_mode_session_filter(client):
    """session_ids-Filter: nur die gewaehlten Sessions erscheinen."""
    created = _create_sessions(client, [("1", None), (None, None)])
    _post_samples(client, "a3", _profile_chain(2))
    data = client.get(
        "/power/cycles/a3/overlay",
        params={"session_ids": str(created[0]["id"])},
    ).json()
    # Beide Zyklen liegen nach beiden Session-Starts -> sonst gefiltert.
    assert data["avg"] == []


def test_overlay_avg_mode_session_selected(client):
    """Session-Filter greift: gewaehlte Session bleibt, andere faellt raus."""
    created = _create_sessions(client, [("1", None), (None, None)])
    _post_samples(client, "a4", _profile_chain(2))
    data = client.get("/power/cycles/a4/overlay").json()
    assert len(data["avg"]) == 1
    assert data["avg"][0]["session_id"] == created[-1]["id"]
    data2 = client.get(
        "/power/cycles/a4/overlay",
        params={"session_ids": f"{created[0]['id']},{created[1]['id']}"},
    ).json()
    assert len(data2["avg"]) == 1  # nur eine Session hat Zyklen


def test_overlay_avg_mode_invalid_session_ids(client):
    """Kaputte session_ids -> 422."""
    _post_samples(client, "a5", _profile_chain(1))
    res = client.get(
        "/power/cycles/a5/overlay", params={"session_ids": "x,y"})
    assert res.status_code == 422


def test_overlay_avg_mode_window(client):
    """Zoom-Fenster im avg-Modus: Raster nur ueber das Fenster."""
    pts = _profile_chain(3)
    _post_samples(client, "a6", pts)
    data = client.get(
        "/power/cycles/a6/overlay",
        params={"window_start_s": 4.0, "window_end_s": 6.0},
    ).json()
    s = data["avg"][0]
    assert s["t_s"][0] == pytest.approx(0.0, abs=0.01)
    assert s["t_s"][-1] == pytest.approx(2.0, abs=0.05)
    assert len(s["t_s"]) < 150  # 2 s / 20 ms ~ 100 Punkte
    # Trigger fensterrelativ bei 5.0 - 4.0 = 1.0 s
    assert s["landmarks"]["trigger"]["start_s"] == pytest.approx(1.0, abs=0.05)


def test_render_session_average_unit_empty(client):
    """Keine Zyklen -> leeres avg."""
    out = power_cycles.render_session_average(
        {"cycles": []}, [], sessions=[])
    assert out == {"avg": []}


def test_render_session_average_unit_truncated_skipped(client):
    """Truncated-Zyklen gehen nicht in den Mittelwert."""
    import datetime as dt
    base = dt.datetime(2026, 10, 12, 12, 0, 0)
    pts = _profile_chain(2)
    m = [{"measured_at": (base + dt.timedelta(seconds=t)).isoformat(),
          "current_ma": ma} for t, ma in pts]
    analysis = power_cycles.analyze_cycles(m)
    assert all(not c["truncated"] for c in analysis["cycles"])
    samples = power_cycles._prepare_samples(m)
    out = power_cycles.render_session_average(analysis, samples)
    assert out["avg"][0]["n_cycles"] == 2


def test_render_session_average_unit_edge_cases(client):
    """Randfaelle: leeres Fenster, zu kurze Zyklen, Interpolation."""
    import datetime as dt
    base = dt.datetime(2026, 10, 12, 12, 0, 0)
    pts = _profile_chain(2)
    m = [{"measured_at": (base + dt.timedelta(seconds=t)).isoformat(),
          "current_ma": ma} for t, ma in pts]
    analysis = power_cycles.analyze_cycles(m)
    samples = power_cycles._prepare_samples(m)

    # 1. Fenster ausserhalb aller Zyklen -> hi <= lo -> leeres Ergebnis.
    out = power_cycles.render_session_average(
        analysis, samples, window_start_s=50.0, window_end_s=60.0)
    assert out == {"avg": []}

    # 2. Zyklus zu kurz (< 2 Samples im Schnitt) -> kein Absturz, leer.
    short = power_cycles.analyze_cycles([{"measured_at": m[0]["measured_at"],
                                          "current_ma": 74.5}])
    out2 = power_cycles.render_session_average(short, samples[:1])
    assert out2 == {"avg": []}

    # 2b. Konstruierte Analyse mit 2 Zyklen: resampeln scheitert (ct < 2
    #     Samples) bzw. nur ein Zyklus resampelbar -> std-Zweig.
    fake_cycle = {
        "start": m[0]["measured_at"], "t0_s": 0.0, "duration_s": 1.0,
        "peak_ma": 135.0, "truncated": False, "gap": False,
        "landmarks": {"trigger": {"start_s": 0.5}, "spike_end": None},
        "phases": {"pre_peak": {"start_s": 0.0, "end_s": 0.5, "duration_s": 0.5},
                   "spike": {"start_s": 0.5, "end_s": 1.0, "duration_s": 0.5},
                   "tail": {"start_s": 1.0, "end_s": 1.0, "duration_s": 0.0}},
        "charge_uah": 0.2,
    }
    # samples decken nur den ersten Zyklus ab -> zweiter Zyklus: ct < 2.
    analysis2 = {"cycles": [fake_cycle, {**fake_cycle, "t0_s": 1000.0}]}
    out2b = power_cycles.render_session_average(analysis2, samples[:300])
    assert out2b["avg"][0]["n_cycles"] == 1
    assert all(v == 0.0 for v in out2b["avg"][0]["std"])  # Single-Col-Zweig

    # 2c. Alle Zyklen resampeln nicht -> komplett leer.
    analysis3 = {"cycles": [{**fake_cycle, "t0_s": 1000.0}]}
    out2c = power_cycles.render_session_average(analysis3, samples[:300])
    assert out2c == {"avg": []}

    # 2d. Beide Zyklen resampelbar -> std > 0 (Zweizweig) moeglich: zwei
    #     identische Zyklen -> std = 0, aber Mehr-Zyklen-Pfad geprueft.
    analysis4 = {"cycles": [fake_cycle, {**fake_cycle}]}
    out2d = power_cycles.render_session_average(analysis4, samples[:300])
    assert out2d["avg"][0]["n_cycles"] == 2
    assert all(v == 0.0 for v in out2d["avg"][0]["std"])

    # 3. _interp_grid: Rand-Klemmung + lineare Interpolation.
    col = power_cycles._interp_grid(
        [0.0, 1.0], [10.0, 20.0], [-1.0, 0.5, 2.0])
    assert col == [10.0, 15.0, 20.0]
    # Eine Kurve mit < 2 Punkten ist vom Aufrufer ausgeschlossen; der
    # Index-Zugriff waere hier ungueltig - deshalb nur 2+-Punkt-Kurven.

    # 4. Mehrere Sessions -> Gruppen getrennt, Reihenfolge chronologisch.
    sessions = [
        {"id": 5, "start_time": (base - dt.timedelta(seconds=100)).isoformat()},
        {"id": 7, "start_time": (base + dt.timedelta(seconds=100)).isoformat()},
    ]
    out3 = power_cycles.render_session_average(analysis, samples, sessions=sessions)
    # Beide Zyklen (t=0..20) liegen nach Session 5, vor Session 7 -> nur 5.
    assert [s["session_id"] for s in out3["avg"]] == [5]
    # Filter auf Session 7 -> leer.
    out4 = power_cycles.render_session_average(
        analysis, samples, sessions=sessions, session_ids=[7])
    assert out4 == {"avg": []}


def test_sessions_list_endpoint(client):
    """GET /sessions: Auswahl-Listing, neueste Session zuerst."""
    created = _create_sessions(client, [("1", None), (None, None)])
    data = client.get("/sessions").json()
    ids = [s["id"] for s in data["sessions"]]
    assert created[-1]["id"] in ids and created[0]["id"] in ids
    # Neueste zuerst (absteigende IDs reichen als chronologischer Proxy).
    assert ids[0] == max(ids)
    assert all("start_time" in s and "custom_text" in s for s in data["sessions"])


# ── Session-Average-Render-Cache ─────────────────────────────────────────

def test_session_avg_cache_hit_and_fresh(client, monkeypatch):
    """Zweiter avg-Request innerhalb der TTL: Cache-Hit, kein Neurendern."""
    import main as mainmod
    mainmod._session_avg_cache.clear()
    _post_samples(client, "c1", _profile_chain(2))

    calls = {"n": 0}
    orig = mainmod.power_cycles.render_session_average

    def counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(mainmod.power_cycles, "render_session_average", counting)
    first = client.get("/power/cycles/c1/overlay").json()
    assert calls["n"] == 1
    second = client.get("/power/cycles/c1/overlay").json()
    assert calls["n"] == 1  # Cache-Hit
    assert second["avg"] == first["avg"]


def test_session_avg_cache_invalidated_on_new_data(client):
    """Neue Proben -> Fingerprint aendert sich -> avg wird neu berechnet."""
    import main as mainmod
    mainmod._session_avg_cache.clear()
    _post_samples(client, "c2", _profile_chain(1))
    first = client.get("/power/cycles/c2/overlay").json()
    assert len(first["avg"]) == 1
    _post_samples(client, "c2", _synthetic_profile(offset_s=PROFILE_DURATION_S))
    second = client.get("/power/cycles/c2/overlay").json()
    assert second["avg"][0]["n_cycles"] == 2


def test_session_avg_cache_ttl_expiry(client):
    """Abgelaufene TTL: Fingerprint-Pruefung, bei Gleichheit weiterverwendet."""
    import main as mainmod
    mainmod._session_avg_cache.clear()
    _post_samples(client, "c3", _profile_chain(1))
    client.get("/power/cycles/c3/overlay").json()
    for entry in mainmod._session_avg_cache.values():
        entry["ts"] -= mainmod._SESSION_AVG_CACHE_TTL_S + 1.0
    data = client.get("/power/cycles/c3/overlay").json()
    assert len(data["avg"]) == 1


def test_session_avg_cache_eviction(client):
    """Mehr als _OVERLAY_CACHE_MAX_ENTRIES Eintraege -> Cache geleert."""
    import main as mainmod
    mainmod._session_avg_cache.clear()
    for i in range(mainmod._OVERLAY_CACHE_MAX_ENTRIES + 2):
        _post_samples(client, f"f{i}", _profile_chain(1))
        client.get(f"/power/cycles/f{i}/overlay").json()
    assert len(mainmod._session_avg_cache) <= mainmod._OVERLAY_CACHE_MAX_ENTRIES


def test_session_avg_cache_different_keys(client):
    """Verschiedene Session-Filter/Fenster fuehren zu separaten Eintraegen.

    Bewusst nur 2 Schluessel: _OVERLAY_CACHE_MAX_ENTRIES = 2, ein dritter
    wuerde den Cache leeren (Eviction, siehe test_session_avg_cache_eviction).
    """
    import main as mainmod
    mainmod._session_avg_cache.clear()
    created = _create_sessions(client, [("1", None), (None, None)])
    _post_samples(client, "c4", _profile_chain(1))
    client.get("/power/cycles/c4/overlay").json()
    client.get("/power/cycles/c4/overlay",
               params={"session_ids": str(created[0]["id"])}).json()
    assert len(mainmod._session_avg_cache) == 2
