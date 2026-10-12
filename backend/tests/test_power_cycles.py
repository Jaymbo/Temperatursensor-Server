"""Tests fuer die Zyklus-Erkennung / das Oszilloskop-Overlay (power_cycles).

Peak-basierte Zyklus-Definition (siehe power_cycles-Moduldoc): Trigger =
erstes Sample ueber peak_trigger_ma (Default 100 mA), Zyklus = Trigger - 5 s
bis naechster Trigger - 5 s, Refraktaerzeit 10 s nach dem Trigger.

Synthetische Profile: Der echte Logger zieht DAUERHAFT ~74 mA Grundlast
(keine Ruhe < 2 mA!), nutzbare Energie steckt im TX-Burst (Peak ~135 mA,
~1,6 s). Die Profile ahmen das nach.
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


def _synthetic_profile(wlan_duration_s: float = 0.8, base_ma: float = 74.5,
                       peak_ma: float = 135.0) -> list[tuple[float, float]]:
    """
    Ein Zyklus (echtes Profil nachgebaut): 5 s Grundlast (~74 mA), dann
    TX-Burst (~135 mA, 0.2 + wlan_duration_s), dann Grundlast bis
    Zyklusende = 10 s. Sample-Raster 5 ms (wie Firmware).
    """
    pts = []
    t = 0.0
    dt = 0.005

    def phase(duration, ma):
        nonlocal t
        for _ in range(int(duration / dt)):
            pts.append((round(t, 3), ma))
            t += dt

    phase(5.0, base_ma)                  # Grundlast-Vorlauf
    phase(0.2 + wlan_duration_s, peak_ma)  # TX-Burst (Trigger + Rest)
    phase(10.0 - 5.0 - 0.2 - wlan_duration_s, base_ma)  # Grundlast nach Burst
    return pts


def _profile_chain(count: int, wlan_duration_s: float = 0.8) -> list[tuple[float, float]]:
    """`count` Zyklen hintereinander, ohne ueberlappende Zeitstempel."""
    pts = []
    offset = 0.0
    for _ in range(count):
        pts += [(t + offset, ma) for t, ma in _synthetic_profile(wlan_duration_s)]
        offset += 10.0
    return pts


class TestTriggerDetection:
    def test_trigger_is_first_sample_above_threshold(self):
        samples = power_cycles._prepare_samples(
            _measurements(_synthetic_profile()))
        triggers = power_cycles._find_trigger_indices(samples, 100.0, 10.0)
        assert len(triggers) == 1
        # Trigger = erstes Sample des TX-Bursts (Grundlast endet bei t=5.0).
        assert samples[triggers[0]][0] == pytest.approx(5.0, abs=0.01)

    def test_refractory_debounces_long_burst(self):
        # Burst von 2 s (400 Samples ueber der Schwelle) -> nur EIN Trigger.
        dt = 0.005
        pts = [((i * dt), 80.0) for i in range(int(5.0 / dt))]
        pts += [((5.0 + i * dt), 150.0) for i in range(int(2.0 / dt))]
        samples = power_cycles._prepare_samples(_measurements(pts))
        triggers = power_cycles._find_trigger_indices(samples, 100.0, 10.0)
        assert len(triggers) == 1
        assert samples[triggers[0]][0] == pytest.approx(5.0, abs=0.01)

    def test_refractory_ends_then_new_trigger(self):
        # Zwei Buerste im Abstand 12 s (Refraktaer 10 s) -> zwei Trigger.
        pts = []
        for start in (0.0, 12.0):
            pts += [((start + i * 0.005), 150.0) for i in range(40)]
        samples = power_cycles._prepare_samples(_measurements(pts))
        triggers = power_cycles._find_trigger_indices(samples, 100.0, 10.0)
        assert len(triggers) == 2

    def test_short_burst_below_trigger_no_cycle(self):
        # Nur Grundlast -> kein Trigger.
        pts = [((i * 0.005), 74.5) for i in range(int(20.0 / 0.005))]
        samples = power_cycles._prepare_samples(_measurements(pts))
        assert power_cycles._find_trigger_indices(samples, 100.0, 10.0) == []


class TestSegmentation:
    def test_single_cycle_with_pre_window(self):
        samples = power_cycles._prepare_samples(_measurements(_synthetic_profile()))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 1
        seg = cycles[0]
        assert not seg["truncated"]
        assert not seg["gap"]
        # Zyklus = Trigger (5.0) - 5 s Vorlauf bis Streamende (Grundlast bis 10).
        assert samples[seg["start_idx"]][0] == pytest.approx(0.0, abs=0.01)
        assert samples[seg["end_idx"]][0] == pytest.approx(10.0, abs=0.01)
        assert samples[seg["trigger_idx"]][0] == pytest.approx(5.0, abs=0.01)

    def test_two_cycles_sliced_before_next_trigger(self):
        samples = power_cycles._prepare_samples(_measurements(_profile_chain(2)))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 2
        for seg in cycles:
            assert not seg["truncated"]
            assert not seg["gap"]
        # Erster Zyklus endet 5 s vor dem zweiten Trigger (t=15) -> t=10.0.
        assert samples[cycles[0]["end_idx"]][0] == pytest.approx(10.0, abs=0.01)
        assert samples[cycles[1]["start_idx"]][0] == pytest.approx(10.0, abs=0.01)
        assert samples[cycles[1]["trigger_idx"]][0] == pytest.approx(15.0, abs=0.01)

    def test_data_gap_marked_within_cycle(self):
        # Loch MITTEN im Zyklus (6 s) -> gap-Markierung (der Zyklus selbst
        # bleibt im neuen Modell bestehen).
        pts = []
        for t, ma in _synthetic_profile():
            if t < 6.0:
                pts.append((t, ma))
            else:
                pts.append((t + 6.0, ma))
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 1
        assert cycles[0]["gap"]

    def test_last_cycle_truncated_at_max_cycle_s(self):
        # Dauer-Peak > max_cycle_s mit langer Refraktaerzeit -> EIN Zyklus,
        # der am Deckel abgeschnitten wird.
        pts = [((i * 0.005), 150.0) for i in range(int(300 / 0.005))]
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples, max_cycle_s=120.0,
                                             refractory_s=400.0)
        assert len(cycles) == 1
        assert cycles[0]["truncated"]
        assert samples[cycles[0]["end_idx"]][0] == pytest.approx(120.005, abs=0.01)

    def test_continuous_peak_yields_cycle_per_refractory_period(self):
        # Dauer-Peak mit Default-Refraktaerzeit: alle refractory_s ein Trigger,
        # jeder Zyklus ~10 s lang, keiner truncated.
        pts = [((i * 0.005), 150.0) for i in range(int(300 / 0.005))]
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 30
        assert all(not c["truncated"] for c in cycles)
        assert samples[cycles[1]["trigger_idx"]][0] == pytest.approx(10.0, abs=0.01)


class TestLandmarksAndPhases:
    def test_phases_of_standard_profile(self):
        analysis = power_cycles.analyze_cycles(_measurements(_synthetic_profile(0.8)))
        assert len(analysis["cycles"]) == 1
        c = analysis["cycles"][0]
        assert c["peak_ma"] == pytest.approx(135.0, abs=0.1)
        # Phasen: pre_peak = Vorlauf (5 s), spike = Burst (1.0 s), tail = Rest.
        assert c["phases"]["pre_peak"]["duration_s"] == pytest.approx(5.0, abs=0.05)
        assert c["phases"]["spike"]["duration_s"] == pytest.approx(1.0, abs=0.1)
        assert c["phases"]["tail"]["duration_s"] == pytest.approx(4.0, abs=0.1)
        assert c["landmarks"]["trigger"]["start_s"] == pytest.approx(5.0, abs=0.01)
        # spike_end = erster Fall unter 85 mA nach dem Trigger.
        assert c["landmarks"]["spike_end"]["start_s"] == pytest.approx(6.0, abs=0.1)

    def test_longer_burst_visible_in_duration(self):
        short = power_cycles.analyze_cycles(_measurements(_synthetic_profile(0.8)))
        long_ = power_cycles.analyze_cycles(_measurements(_synthetic_profile(2.5)))
        d_short = short["cycles"][0]["phases"]["spike"]["duration_s"]
        d_long = long_["cycles"][0]["phases"]["spike"]["duration_s"]
        assert d_long > d_short + 1.0

    def test_stats_median_and_spread(self):
        pts = []
        for wlan_s in (0.8, 0.8, 2.5):
            p = _synthetic_profile(wlan_s)
            offset = (pts[-1][0] + 5.0) if pts else 0.0
            pts += [(t + offset, ma) for t, ma in p]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        stats = analysis["stats"]["spike"]
        assert stats["n"] == 3
        assert stats["median_s"] == pytest.approx(1.0, abs=0.1)
        assert stats["max_s"] > stats["median_s"] + 1.0

    def test_missing_spike_end_falls_back(self):
        # Strom bleibt die ganze Zeit ueber spike_end_ma -> kein spike_end-
        # Landmark; die spike-Phase laeuft bis zum Zyklusende.
        pts = [((i * 0.005), 150.0) for i in range(int(2.0 / 0.005))]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        c = analysis["cycles"][0]
        assert c["landmarks"]["spike_end"] is None
        assert c["phases"]["spike"]["end_s"] == pytest.approx(c["duration_s"], abs=0.01)
        assert c["phases"]["tail"]["duration_s"] == pytest.approx(0.0, abs=0.01)

    def test_empty_input(self):
        analysis = power_cycles.analyze_cycles([])
        assert analysis == {"cycles": [], "stats": {}}


class TestOverlay:
    def test_overlay_synchronizes_on_cycle_start(self):
        pts = []
        for wlan_s in (0.8, 2.5):
            p = _synthetic_profile(wlan_s)
            offset = (pts[-1][0] + 5.0) if pts else 0.0
            pts += [(t + offset, ma) for t, ma in p]
        overlay = power_cycles.build_overlay(_measurements(pts), limit=10)
        assert len(overlay["cycles"]) == 2
        for c in overlay["cycles"]:
            # t=0 ist immer der Zyklusanfang (Grundlast-Vorlauf).
            assert c["t_s"][0] == pytest.approx(0.0, abs=0.01)
            assert c["ma"][0] == pytest.approx(74.5, abs=1.0)
            # Trigger liegt bei pre_peak_s = 5 s.
            assert c["landmarks"]["trigger"]["start_s"] == pytest.approx(5.0, abs=0.01)

    def test_overlay_limit(self):
        overlay = power_cycles.build_overlay(
            _measurements(_profile_chain(5)), limit=3)
        assert len(overlay["cycles"]) == 3


class TestOverlayDownsampling:
    """LTTB-Downsampling + Zoom-Fenster im Overlay (Performance-Fix)."""

    def test_lttb_keeps_first_last_and_reduces(self):
        # 1000 Punkte -> 100 ausgewaehlt; erster/letzter bleiben immer.
        t = [i * 0.01 for i in range(1000)]
        ma = [100.0 + (50.0 if i % 7 == 0 else 0.0) for i in range(1000)]
        keep, n = power_cycles._lttb_select(t, ma, 100)
        assert n == 100
        assert keep[0] == 0 and keep[-1] == 999
        assert len(set(keep)) == 100  # monoton & eindeutig

    def test_lttb_no_downsampling_if_below_target(self):
        t = [0.0, 1.0, 2.0]
        keep, n = power_cycles._lttb_select(t, [1, 2, 3], 100)
        assert keep == [0, 1, 2] and n == 3

    def test_overlay_default_downsamples(self):
        # Synthetischer Zyklus mit vielen Samples (> 500) -> Overlay klein.
        pts = _synthetic_profile(0.8)
        pts = [(t, ma) for t, ma in pts] + [
            (pts[-1][0] + (i + 1) * 0.005, 74.5) for i in range(2000)]
        overlay = power_cycles.build_overlay(_measurements(pts))
        c = overlay["cycles"][0]
        assert len(c["t_s"]) <= power_cycles.DEFAULT_MAX_POINTS_PER_CYCLE
        # Peaks im Downsample nicht weggebuegelt (LTTB bewahrt Extrema besser).
        assert max(c["ma"]) >= 130.0

    def test_overlay_window_zooms_into_detail(self):
        pts = _synthetic_profile(0.8)
        pts += [(pts[-1][0] + (i + 1) * 0.005, 74.5) for i in range(2000)]
        m = _measurements(pts)
        full = power_cycles.build_overlay(m)
        # Fenster um den Trigger (t=5 s): 4.0 .. 6.5 s
        win = power_cycles.build_overlay(m, window_start_s=4.0, window_end_s=6.0)
        fc, wc = full["cycles"][0], win["cycles"][0]
        # Fenster (4..6 s = 400 Punkte < Ziel-500) behaelt die VOLLE
        # Aufloesung (gleichmaessiges 5-ms-Raster), waehrend die Gesamtansicht
        # per LTTB ungleichmaessig verdichtet ist.
        assert len(wc["t_s"]) <= 500
        gaps = [b - a for a, b in zip(wc["t_s"], wc["t_s"][1:])]
        assert max(gaps) == pytest.approx(0.005, abs=1e-6)  # 200 Hz erhalten
        full_gaps = [b - a for a, b in zip(fc["t_s"], fc["t_s"][1:])]
        assert max(full_gaps) > 0.01  # verdichtet
        # Fenster relativ zum Fensteranfang (4.0 s): erste Koordinate ~0.
        assert wc["t_s"][0] == pytest.approx(0.0, abs=0.01)
        # Trigger liegt fensterrelativ bei 5.0 - 4.0 = 1.0 s (wie die
        # Datenpunkte, die ebenfalls auf 4.0 s verschoben werden).
        assert wc["landmarks"]["trigger"]["start_s"] == pytest.approx(1.0, abs=0.01)

    def test_overlay_window_without_downsampling(self):
        pts = _synthetic_profile(0.8)
        pts += [(pts[-1][0] + (i + 1) * 0.005, 74.5) for i in range(2000)]
        m = _measurements(pts)
        win = power_cycles.build_overlay(m, window_start_s=4.0, window_end_s=6.5,
                                         max_points_per_cycle=0)
        # 0 = kein Downsampling; Fenster 2.5 s @ 200 Hz = ~500 Punkte
        assert len(win["cycles"][0]["t_s"]) > 100


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

    def test_negative_current_clamped_to_zero(self):
        samples = power_cycles._prepare_samples(
            [{"measured_at": _iso(0.0), "current_ma": -12.0}])
        assert samples == [(0.0, 0.0)]

    def test_trigger_before_pre_window_clamps_to_stream_start(self):
        # Trigger frueher als pre_peak_s nach Streambeginn -> Zyklus startet
        # trotzdem am Streamanfang (kein negativer Index).
        pts = [((i * 0.005), 150.0) for i in range(40)]
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert len(cycles) == 1
        assert cycles[0]["start_idx"] == 0

    def test_last_cycle_not_truncated_when_short(self):
        # Letzter Zyklus kuerzer als max_cycle_s -> nicht abgeschnitten.
        pts = _synthetic_profile()
        samples = power_cycles._prepare_samples(_measurements(pts))
        cycles = power_cycles.segment_cycles(samples)
        assert not cycles[0]["truncated"]

    def test_gap_threshold_override(self):
        # gap_s=0.001 -> jedes 5-ms-Raster gilt als Loch.
        samples = power_cycles._prepare_samples(_measurements(_synthetic_profile()))
        cycles = power_cycles.segment_cycles(samples, gap_s=0.001)
        assert cycles[0]["gap"]

    def test_charge_ignores_degenerate_timesteps(self):
        # Doppelte Zeitstempel (dt <= 0, z. B. nach Uhr-Korrektur) tragen
        # NICHT zur Ladung bei: nur ein 5-ms-Intervall zaehlt.
        pts = [(0.0, 120.0), (0.005, 120.0), (0.005, 120.0)]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        c = analysis["cycles"][0]
        # 120 mA * 0.005 s = 0.6 mAs -> /3.6 = 0.1667 uAh (nicht das Doppelte)
        assert c["charge_uah"] == pytest.approx(0.1667, abs=0.001)

    def test_charge_skips_data_gap(self):
        # 6-s-Loch mitten im Zyklus -> der integrates Schritt wird uebersprungen.
        pts = [(t, ma) for t, ma in _synthetic_profile() if t < 6.0]
        pts += [(t + 6.0, ma) for t, ma in _synthetic_profile() if t >= 6.0]
        analysis = power_cycles.analyze_cycles(_measurements(pts))
        assert analysis["cycles"][0]["gap"]
        # Grundlast ohne den 6-s-Sprung waere ~74.5 mA * 10 s / 3.6 = 207 uAh;
        # das Loch darf nichts dazuzzaehlen.
        assert analysis["cycles"][0]["charge_uah"] < 300.0

    def test_stats_skips_truncated_cycles(self):
        # Nur ein truncated-Zyklus -> keine Phasen-Statistik.
        pts = [((i * 0.005), 150.0) for i in range(int(300 / 0.005))]
        analysis = power_cycles.analyze_cycles(
            _measurements(pts), refractory_s=400.0)
        assert analysis["stats"] == {}

    def test_analyze_cycles_kwargs_forwarded(self):
        # Alle Parameter muessen von analyze_cycles angenommen werden
        # (Durchreichen aus main.py).
        samples = _measurements(_synthetic_profile())
        a = power_cycles.analyze_cycles(
            samples, peak_trigger_ma=100.0, pre_peak_s=1.0, refractory_s=2.0,
            spike_end_ma=85.0, max_cycle_s=60.0, gap_s=5.0)
        assert len(a["cycles"]) == 1
