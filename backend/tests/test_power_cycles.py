"""Tests fuer die Zyklus-Erkennung / das Oszilloskop-Overlay (power_cycles).

Synthetische Stromprofile: Ruhe (schlafender Logger) -> Wake-Spike -> Messphase ->
WLAN-Connect -> HTTP-Transmit -> Ruhe. Ein Profil-Variant prueft den Fall
"Server erst spaeter erreicht" (laengere WLAN-Phase).
"""

import importlib.util
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "power_cycles", os.path.join(_BACKEND_DIR, "power_cycles.py")
)
power_cycles = importlib.util.module_from_spec(_spec)
sys.modules["power_cycles"] = power_cycles
_spec.loader.exec_module(power_cycles)


BASE = __import__("datetime").datetime(2026, 10, 12, 12, 0, 0)


def _iso(t_s: float) -> str:
    return (BASE + __import__("datetime").timedelta(seconds=t_s)).isoformat()


def _measurements(points: list[tuple[float, float]]) -> list[dict]:
    """[(t_s, current_ma)] -> Rohproben-Liste wie aus der DB."""
    return [
        {"measured_at": _iso(t), "current_ma": ma, "bus_v": 3.3, "shunt_mv": ma * 0.1}
        for t, ma in points
    ]


def _synthetic_profile(wlan_duration_s: float = 0.8) -> list[tuple[float, float]]:
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
            pts.append((round(t, 3), ma))
            t += dt

    phase(2.0, 0.1)                       # Schlaf
    phase(0.1, 150.0)                     # Wake-Spike
    phase(1.0, 10.0)                      # Messphase
    phase(wlan_duration_s, 60.0)          # WLAN-Connect
    phase(0.5, 120.0)                     # HTTP-Send
    phase(3.0, 0.1)                       # Schlaf danach
    return pts


class TestSegmentation:
    def test_single_cycle_detected(self):
        samples = power_cycles._prepare_samples(_measurements(_synthetic_profile()))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 1
        seg = cycles[0]
        assert not seg["truncated"]
        assert not seg["gap"]
        # Zyklus startet beim Wake-Spike (t=2.0 s) und endet vor dem Schlaf.
        assert samples[seg["start_idx"]][0] == pytest.approx(2.0, abs=0.01)
        end_t = samples[seg["end_idx"]][0]
        assert 3.5 <= end_t <= 5.0          # Send endet ~3.6 s, Schlaf danach

    def test_two_cycles_with_gap_between(self):
        pts = _synthetic_profile() + _synthetic_profile(wlan_duration_s=1.5)
        # Zweiten Zyklus zeitversetzt einfuegen (nach 8 s Ruhe).
        offset = pts[-1][0] + 2.0
        pts = [(t, ma) for t, ma in pts] + [(t + offset, ma) for t, ma in pts]
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 2
        assert not cycles[0]["truncated"]
        assert not cycles[1]["truncated"]

    def test_data_gap_ends_cycle(self):
        # Loch MITTEN im Zyklus (6 s, z. B. Sende-Pause des Mess-ESP, die in die
        # WLAN-Phase faellt) beendet den Zyklus -> Luecke wird markiert.
        pts = []
        for t, ma in _synthetic_profile():
            if t < 3.2:                     # bis Anfang WLAN-Phase
                pts.append((t, ma))
            elif t >= 3.2:                  # Rest 6 s versetzt ANHANGEN
                pts.append((t + 6.0, ma))
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert cycles[0]["gap"]


class TestLandmarks:
    def test_phases_of_short_connect(self):
        analysis = power_cycles.analyze_cycles(_measurements(_synthetic_profile(0.8)))
        assert len(analysis["cycles"]) == 1
        c = analysis["cycles"][0]
        assert c["phases"]["wake"]["duration_s"] == pytest.approx(0.1, abs=0.05)
        # WLAN-Phase ~0.8 s (60 mA >= 40 mA), dann Send (120 mA).
        assert c["phases"]["wlan"]["duration_s"] == pytest.approx(0.8, abs=0.1)
        assert c["phases"]["send_tail"]["duration_s"] >= 0.3

    def test_longer_connect_visible_in_duration(self):
        short = power_cycles.analyze_cycles(_measurements(_synthetic_profile(0.8)))
        long_ = power_cycles.analyze_cycles(_measurements(_synthetic_profile(2.5)))
        d_short = short["cycles"][0]["phases"]["wlan"]["duration_s"]
        d_long = long_["cycles"][0]["phases"]["wlan"]["duration_s"]
        # "Manchmal braucht der Teil so lange, manchmal aber auch laenger."
        assert d_long > d_short + 1.0

    def test_stats_median_and_spread(self):
        pts = []
        for wlan_s in (0.8, 0.8, 2.5):
            p = _synthetic_profile(wlan_s)
            offset = (pts[-1][0] + 2.0) if pts else 0.0
            pts += [(t + offset, ma) for t, ma in p]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        stats = analysis["stats"]["wlan"]
        assert stats["n"] == 3
        assert stats["median_s"] == pytest.approx(0.8, abs=0.1)
        assert stats["max_s"] > stats["median_s"] + 1.0

    def test_missing_landmark_falls_back(self):
        # Zyklus OHNE TX-Burst ueber 80 mA (Server nie erreicht) -> send-Landmark
        # fehlt. Semantik: Die WLAN-Phase laeuft dann bis zum ZYKLENDE (der ESP
        # wiederholt Connect/Send), statt sie sofort zu beenden.
        pts = [p for p in _synthetic_profile(0.8) if p[1] < 80.0]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        c = analysis["cycles"][0]
        assert c["landmarks"]["send"] is None
        assert c["phases"]["wlan"]["end_s"] == pytest.approx(
            c["duration_s"], abs=0.01
        )
        assert c["phases"]["send_tail"]["duration_s"] == pytest.approx(0.0, abs=0.01)

    def test_empty_input(self):
        analysis = power_cycles.analyze_cycles([])
        assert analysis == {"cycles": [], "stats": {}}


class TestOverlay:
    def test_overlay_synchronizes_on_trigger(self):
        pts = []
        for i, wlan_s in enumerate((0.8, 2.5)):
            p = _synthetic_profile(wlan_s)
            offset = (pts[-1][0] + 2.0) if pts else 0.0
            pts += [(t + offset, ma) for t, ma in p]
        overlay = power_cycles.build_overlay(_measurements(pts), limit=10)
        assert len(overlay["cycles"]) == 2
        for c in overlay["cycles"]:
            # t=0 ist immer der Trigger: erster Sample ist Wake-Spike.
            assert c["t_s"][0] == pytest.approx(0.0, abs=0.01)
            assert c["ma"][0] == pytest.approx(150.0, abs=1.0)
            # Phasen relativ zum Trigger.
            assert c["phases"]["wake"]["start_s"] == pytest.approx(0.0, abs=0.01)

    def test_overlay_limit(self):
        pts = []
        for i in range(5):
            p = _synthetic_profile(0.8)
            offset = (pts[-1][0] + 2.0) if pts else 0.0
            pts += [(t + offset, ma) for t, ma in p]
        overlay = power_cycles.build_overlay(_measurements(pts), limit=3)
        assert len(overlay["cycles"]) == 3


class TestEdgeCases:
    """Randfaelle fuer 100% Coverage der Analyse-Logik."""

    def test_invalid_measurements_skipped(self):
        # Ungueltige Zeilen (kaputtes ISO, fehlender Strom) werden verworfen.
        rows = [
            {"measured_at": "kaputt", "current_ma": 5.0},
            {"measured_at": _iso(0.0), "current_ma": None},
            {"measured_at": _iso(0.005), "current_ma": 150.0},
        ]
        samples = power_cycles._prepare_samples(rows)
        assert samples == [(0.0, 150.0)]

    def test_sustained_run_too_short_continues_search(self):
        # Kurzlauf ueber trigger (0.05 s < sustain 0.1 s), dann spaeter ein
        # ausreichend langer Lauf -> Landmark muss beim zweiten liegen.
        dt = 0.005
        pts = [(i * dt, 10.0) for i in range(400)]          # 2 s Ruhe
        pts += [((2.0 + i * dt), 150.0) for i in range(10)]  # 0.05 s kurz
        pts += [((2.05 + i * dt), 10.0) for i in range(100)]  # 0.5 s Ruhe
        pts += [((2.55 + i * dt), 150.0) for i in range(40)]  # 0.2 s lang
        landmarks_cfg = (("x", 100.0, 0.1),)
        lm = power_cycles.extract_landmarks(
            power_cycles._prepare_samples(_measurements(pts)), 0, len(pts) - 1,
            landmarks_cfg,
        )
        assert lm["x"]["start_s"] == pytest.approx(2.55, abs=0.01)

    def test_truncated_cycle_at_max_duration(self):
        # Daueraktivitaet > max_cycle_s -> wird in max_cycle_s-Bloecke geschnitten.
        dt = 0.005
        pts = [((i * dt), 60.0) for i in range(int(300 / dt))]  # 300 s aktiv
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples, max_cycle_s=120.0)
        assert len(cycles) >= 2
        assert cycles[0]["truncated"]
        assert samples[cycles[0]["end_idx"]][0] == pytest.approx(120.005, abs=0.01)

    def test_stream_ends_after_trigger_without_quiet(self):
        # Stream endet direkt nach dem Trigger (keine Ruhe mehr) -> Zyklus
        # bis zum letzten Sample, Ruhe-Abschneiden greift nicht.
        pts = [(0.0, 0.1), (0.005, 150.0), (0.01, 150.0)]
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 1
        assert cycles[0]["end_idx"] == 2

    def test_no_spike_end_within_cycle(self):
        # Aktiver Zyklus, der NIE unter spike_end_ma faellt -> kein spike_end-
        # Landmark; wake_end faellt auf den naechsten Landmark (wlan/send).
        dt = 0.005
        pts = [((i * dt), 120.0) for i in range(int(2.0 / dt))]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        c = analysis["cycles"][0]
        assert c["landmarks"]["spike_end"] is None
        assert c["phases"]["wake"]["end_s"] == pytest.approx(
            c["phases"]["wlan"]["start_s"], abs=0.01
        )
        assert c["phases"]["measure"]["duration_s"] == pytest.approx(0.0, abs=0.01)

    def test_charge_ignores_degenerate_timesteps(self):
        # Doppelte Zeitstempel (dt <= 0, z. B. nach Uhr-Korrektur) tragen
        # NICHT zur Ladung bei: nur ein 5-ms-Intervall zaehlt.
        pts = [(0.0, 100.0), (0.005, 100.0), (0.005, 100.0)]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        c = analysis["cycles"][0]
        # 100 mA * 0.005 s = 0.5 mAs -> /3.6 = 0.1389 uAh (nicht das Doppelte)
        assert c["charge_uah"] == pytest.approx(0.1389, abs=0.001)

    def test_stream_ends_mid_quiet_trims_tail(self):
        # Stream endet nach dem Trigger MIT kurzer Schluss-Ruhe (< min_quiet_s,
        # kein Datenloch) -> angehaengte Ruhe-Samples werden abgeschnitten.
        dt = 0.005
        pts = [((i * dt), 150.0) for i in range(20)]          # 0.1 s Spike
        pts += [((0.1 + i * dt), 0.1) for i in range(200)]    # 1 s Ruhe (< 2 s)
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 1
        end_t = samples[cycles[0]["end_idx"]][0]
        assert end_t == pytest.approx(0.095, abs=0.01)  # Ruhe entfernt
