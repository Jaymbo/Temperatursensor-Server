"""Tests fuer die Strom-Zyklen-Endpunkte in main.py (/power/cycles/...)."""

import datetime

import db as dbmod


BASE = datetime.datetime(2026, 10, 12, 12, 0, 0)

# Gesamtdauer eines synthetischen Profils (Schlaf+Wake+Mess+WLAN+Send+Schlaf)
PROFILE_DURATION_S = 2.0 + 0.1 + 1.0 + 0.8 + 0.5 + 3.0


def _synthetic_profile(wlan_duration_s: float = 0.8, offset_s: float = 0.0) -> list[tuple[float, float]]:
    """
    Ein Zyklus: 2 s Ruhe (0.1 mA), Wake-Spike (150 mA, 0.1 s), Messphase
    (10 mA, 1 s), WLAN (60 mA, wlan_duration_s), Send (120 mA, 0.5 s),
    Ruhe (3 s). Sample-Raster 5 ms (wie Firmware).
    """
    pts = []
    t = 0.0
    dt = 0.005

    def phase(duration, ma):
        nonlocal t
        for _ in range(int(duration / dt)):
            pts.append((round(t + offset_s, 3), ma))
            t += dt

    phase(2.0, 0.1)                       # Schlaf
    phase(0.1, 150.0)                     # Wake-Spike
    phase(1.0, 10.0)                      # Messphase
    phase(wlan_duration_s, 60.0)          # WLAN-Connect
    phase(0.5, 120.0)                     # HTTP-Send
    phase(3.0, 0.1)                       # Schlaf danach
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
    assert cycle["peak_ma"] == 150.0
    assert cycle["truncated"] is False

    # Landmarks sind relativ zum Zyklusstart (wake = Trigger = 0.0)
    lm = cycle["landmarks"]
    assert lm["wake"]["start_s"] == 0.0
    assert 0.05 <= lm["spike_end"]["start_s"] <= 0.15
    assert lm["wlan"]["start_s"] == 1.1
    assert lm["send"]["start_s"] == 1.9

    # stats: Median der WLAN-Phase ~ 0.8 s (nur ein Zyklus)
    assert data["stats"]["wlan"]["median_s"] == 0.8


def test_power_cycles_threshold_override(client):
    """trigger_ma sehr hoch -> Zyklus wird nicht mehr erkannt."""
    _post_samples(client, "2", _synthetic_profile())
    data = client.get(
        "/power/cycles/2", params={"trigger_ma": 1000.0}
    ).json()
    assert data["cycles"] == []


# ── GET /power/cycles/{logger_id}/overlay ────────────────────────────────

def test_power_cycles_overlay(client):
    pts = _profile_chain(1) + _synthetic_profile(
        wlan_duration_s=2.5, offset_s=PROFILE_DURATION_S
    )
    _post_samples(client, "3", pts)
    data = client.get("/power/cycles/3/overlay").json()
    # Beide Zyklen synchronisiert auf t=0 (Trigger)
    assert len(data["cycles"]) == 2
    first = data["cycles"][0]
    assert first["t_s"][0] == 0.0
    assert first["ma"][0] == 150.0
    assert first["landmarks"]["wake"]["start_s"] == 0.0
    # Start-Timestamps unterscheiden sich (zweiter Zyklus spaeter)
    assert data["cycles"][1]["start"] != first["start"]
    # Laengere WLAN-Phase im zweiten Zyklus sichtbar
    assert data["cycles"][1]["phases"]["wlan"]["duration_s"] == 2.5


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
