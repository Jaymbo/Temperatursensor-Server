"""Tests fuer die Auswertungslogik in battery_health.py.

Die Metriken sind RELATIV (Flottenvergleich), da ein absoluter Schwellwert
"alt" nicht von "leer" unterscheiden kann. Bei zu wenig Daten muss "unbekannt"
zurueckkommen, nicht geraten werden.
"""
import datetime

import battery_health as bh


def _pt(iso, mv, under_load=None):
    return {"measured_at": iso, "mv": mv, "under_load": under_load}


# ── parse_time ───────────────────────────────────────────────────────────

def test_parse_time():
    assert bh.parse_time("2026-01-01T00:00:00") == datetime.datetime(2026, 1, 1)
    assert bh.parse_time("") is None
    assert bh.parse_time(None) is None
    assert bh.parse_time("kaputt") is None


# ── battery_level_stage (5 Stufen, kein Prozent) ─────────────────────────

def test_battery_level_stage_thresholds(monkeypatch):
    # Stufen aus den Defaults voll=4200/leer=3300 (Quartile dazwischen).
    # Bewusst UNABHAENGIG von backend/battery_thresholds.json getestet:
    # die Stufenlogik selbst, nicht die konkrete Konfiguration.
    monkeypatch.setattr(bh, "load_thresholds", lambda: {"full_mv": 4200, "empty_mv": 3300})
    assert bh.battery_level_stage(4200)["stage"] == 5
    assert bh.battery_level_stage(4100)["stage"] == 4
    assert bh.battery_level_stage(3975)["stage"] == 4
    assert bh.battery_level_stage(3850)["stage"] == 3
    assert bh.battery_level_stage(3750)["stage"] == 3
    assert bh.battery_level_stage(3625)["stage"] == 2
    assert bh.battery_level_stage(3400)["stage"] == 1
    assert bh.battery_level_stage(3000)["stage"] == 0
    assert bh.battery_level_stage(3000)["label"] == "leer"


def test_battery_level_stage_uses_config_file():
    # Die produktive Konfiguration (full=4100, empty=3500): Skala endet bei
    # 4,1 V (schonende Vollladung), "kritisch" ab 3,5 V.
    assert bh.load_thresholds() == {"full_mv": 4100, "empty_mv": 3500}
    assert bh.battery_level_stage(4100)["stage"] == 5
    assert bh.battery_level_stage(3900)["stage"] == 3
    assert bh.battery_level_stage(3500)["stage"] == 1
    assert bh.battery_level_stage(3400)["stage"] == 0


def test_battery_level_stage_none_and_under_load():
    result = bh.battery_level_stage(None)
    assert result["stage"] is None
    assert result["label"] == "unbekannt"
    assert result["under_load"] is False

    loaded = bh.battery_level_stage(3800, under_load=True)
    assert loaded["under_load"] is True
    assert loaded["stage"] == 3


# ── load_thresholds (einstellbare Schwellwerte voll/leer) ────────────────

def test_load_thresholds_defaults_without_file(tmp_path):
    assert bh.load_thresholds(str(tmp_path / "fehlt.json")) == {
        "full_mv": 4200, "empty_mv": 3300,
    }


def test_load_thresholds_reads_config_file(tmp_path):
    cfg = tmp_path / "battery_thresholds.json"
    cfg.write_text('{"full_mv": 4150, "empty_mv": 3400}', encoding="utf-8")
    assert bh.load_thresholds(str(cfg)) == {"full_mv": 4150, "empty_mv": 3400}


def test_load_thresholds_invalid_values_fall_back_to_defaults(tmp_path):
    cfg = tmp_path / "battery_thresholds.json"
    # ungueltiges JSON
    cfg.write_text('{"full_mv": }', encoding="utf-8")
    assert bh.load_thresholds(str(cfg)) == {"full_mv": 4200, "empty_mv": 3300}
    # leer >= voll -> ungueltig
    cfg.write_text('{"full_mv": 3400, "empty_mv": 3400}', encoding="utf-8")
    assert bh.load_thresholds(str(cfg)) == {"full_mv": 4200, "empty_mv": 3300}
    # falscher Typ / ausserhalb des Plausibilitaetsbereichs
    cfg.write_text('{"full_mv": "4200", "empty_mv": 99999}', encoding="utf-8")
    assert bh.load_thresholds(str(cfg)) == {"full_mv": 4200, "empty_mv": 3300}


# ── _sorted_readings ────────────────────────────────────────────────────

def test_sorted_readings_filters_invalid():
    readings = [
        _pt("2026-01-01T00:02:00", 3900),
        _pt("kaputt", 3800),
        _pt("2026-01-01T00:01:00", 4000),
        {"measured_at": "2026-01-01T00:03:00", "mv": None},
    ]
    out = bh._sorted_readings(readings)
    assert [r["mv"] for r in out] == [4000, 3900]


def test_sorted_readings_custom_time_key():
    readings = [
        {"received_at": "2026-01-01T00:02:00", "mv": 3900},
        {"received_at": "2026-01-01T00:01:00", "mv": 4000},
    ]
    out = bh._sorted_readings(readings, time_key="received_at")
    assert [r["mv"] for r in out] == [4000, 3900]


# ── detect_outages ──────────────────────────────────────────────────────

def test_detect_outages_gap():
    readings = [
        _pt("2026-01-01T00:00:00", 4000),
        _pt("2026-01-01T00:00:10", 3990),
        _pt("2026-01-01T01:00:00", 3900),  # grosse Luecke
    ]
    outages = bh.detect_outages(readings, expected_interval_s=10.0)
    assert len(outages) == 1
    assert outages[0]["type"] == "outage"
    assert outages[0]["gap_s"] == 3600 - 10


def test_detect_outages_invalid_interval():
    readings = [_pt("2026-01-01T00:00:00", 4000), _pt("2026-01-01T01:00:00", 3900)]
    assert bh.detect_outages(readings, expected_interval_s=None) == []
    assert bh.detect_outages(readings, expected_interval_s=0) == []


# ── detect_charge_sessions ──────────────────────────────────────────────

def test_detect_charge_sessions():
    readings = [
        _pt("2026-01-01T00:00:00", 3700),
        _pt("2026-01-01T00:10:00", 3750),
        _pt("2026-01-01T00:20:00", 3800),
        _pt("2026-01-01T00:30:00", 3900),
    ]
    charges = bh.detect_charge_sessions(readings)
    assert len(charges) == 1
    assert charges[0]["start_mv"] == 3700
    assert charges[0]["end_mv"] == 3900
    assert charges[0]["points"] == 4


def test_detect_charge_sessions_too_short_or_flat():
    # Nur 2 Punkte -> unter min_points
    short = [_pt("2026-01-01T00:00:00", 3700), _pt("2026-01-01T00:10:00", 3750)]
    assert bh.detect_charge_sessions(short) == []
    # Steigt nicht -> keine Ladung
    flat = [
        _pt("2026-01-01T00:00:00", 3700),
        _pt("2026-01-01T00:10:00", 3690),
        _pt("2026-01-01T00:20:00", 3680),
    ]
    assert bh.detect_charge_sessions(flat) == []
    # Genug Punkte, aber zu kleine Gesamtzunahme
    tiny = [
        _pt("2026-01-01T00:00:00", 3700),
        _pt("2026-01-01T00:10:00", 3705),
        _pt("2026-01-01T00:20:00", 3710),
    ]
    assert bh.detect_charge_sessions(tiny) == []


# ── detect_measure_sessions ─────────────────────────────────────────────

def test_detect_measure_sessions_empty():
    assert bh.detect_measure_sessions([], [], []) == []


def test_detect_measure_sessions_excludes_charge_and_outage():
    readings = [
        _pt("2026-01-01T00:00:00", 4000),
        _pt("2026-01-01T00:00:10", 3990),
        _pt("2026-01-01T00:10:00", 3980),
        _pt("2026-01-01T00:20:00", 3970),
    ]
    charges = [{"start_time": "2026-01-01T00:10:00", "end_time": "2026-01-01T00:20:00"}]
    outages = []
    measures = bh.detect_measure_sessions(readings, charges, outages)
    assert len(measures) == 1
    assert measures[0]["type"] == "measure"
    assert measures[0]["points"] == 2


# ── discharge_rate_mv_per_day ───────────────────────────────────────────

def test_discharge_rate_linear():
    # 100 mV Abfall in 1 Tag -> -100 mV/Tag
    readings = [
        _pt("2026-01-01T00:00:00", 4000),
        _pt("2026-01-01T12:00:00", 3950),
        _pt("2026-01-02T00:00:00", 3900),
    ]
    rate = bh.discharge_rate_mv_per_day(readings, [])
    assert abs(rate["mv_per_day"] + 100.0) < 1e-6
    assert rate["n"] == 3


def test_discharge_rate_too_few_points():
    rate = bh.discharge_rate_mv_per_day([_pt("2026-01-01T00:00:00", 4000)], [])
    assert rate["mv_per_day"] is None
    assert rate["n"] == 1


def test_discharge_rate_zero_denominator():
    readings = [_pt("2026-01-01T00:00:00", 4000), _pt("2026-01-01T00:00:00", 3900)]
    rate = bh.discharge_rate_mv_per_day(readings, [])
    assert rate["mv_per_day"] is None


def test_discharge_rate_excludes_charge_interval():
    readings = [
        _pt("2026-01-01T00:00:00", 4000),
        _pt("2026-01-01T00:10:00", 3900),
        _pt("2026-01-01T00:20:00", 3950),  # im Ladeintervall -> ignoriert
        _pt("2026-01-01T00:30:00", 3890),
    ]
    charges = [{"start_time": "2026-01-01T00:15:00", "end_time": "2026-01-01T00:25:00"}]
    rate = bh.discharge_rate_mv_per_day(readings, charges)
    assert rate["n"] == 3  # der Lade-Punkt (00:20) fliegt raus


# ── full_to_cutoff_days ────────────────────────────────────────────────

def test_full_to_cutoff_days():
    readings = [
        _pt("2026-01-01T00:00:00", 4100),
        _pt("2026-01-02T00:00:00", 3600),
        _pt("2026-01-03T00:00:00", 3150),
    ]
    result = bh.full_to_cutoff_days(readings)
    assert result["days"] == 2.0
    assert result["to"] == "2026-01-03T00:00:00"


def test_full_to_cutoff_no_full_point():
    readings = [_pt("2026-01-01T00:00:00", 4000)]
    result = bh.full_to_cutoff_days(readings)
    assert result["days"] is None
    assert result["from"] is None


def test_full_to_cutoff_never_reaches_cutoff():
    readings = [_pt("2026-01-01T00:00:00", 4100), _pt("2026-01-02T00:00:00", 3700)]
    result = bh.full_to_cutoff_days(readings)
    assert result["days"] is None
    assert result["from"] == "2026-01-01T00:00:00"
    assert result["to"] is None


# ── charge_behavior ────────────────────────────────────────────────────

def test_charge_behavior_six_hour_stop():
    readings = [
        _pt("2026-01-01T00:00:00", 3700),
        _pt("2026-01-01T03:00:00", 3850),
        _pt("2026-01-01T06:00:00", 3900),
        _pt("2026-01-01T06:20:00", 3902),
        _pt("2026-01-01T06:30:00", 3898),
    ]
    charges = [{"start_time": "2026-01-01T00:00:00", "end_time": "2026-01-01T06:30:00"}]
    result = bh.charge_behavior(charges, readings)
    assert result["six_hour_stops"] == 1
    session = result["sessions"][0]
    assert session["six_hour_stop"] is True
    assert session["plateau_at_end"] is True
    assert session["start_mv"] == 3700
    assert session["end_mv"] == 3898


def test_charge_behavior_no_plateau_and_invalid_times():
    readings = [
        _pt("2026-01-01T00:00:00", 3700),
        _pt("2026-01-01T06:00:00", 3900),
        _pt("2026-01-01T06:30:00", 3950),
    ]
    charges = [
        {"start_time": "2026-01-01T00:00:00", "end_time": "2026-01-01T06:30:00"},
        {"start_time": "kaputt", "end_time": "2026-01-01T06:30:00"},
    ]
    result = bh.charge_behavior(charges, readings)
    # zweite Session hat ungueltige Zeit -> uebersprungen
    assert len(result["sessions"]) == 1
    assert result["sessions"][0]["plateau_at_end"] is False
    assert result["six_hour_stops"] == 0


# ── internal_resistance_ohm ────────────────────────────────────────────

def test_internal_resistance_pairs():
    readings = [
        _pt("2026-01-01T00:00:00", 3900, under_load=True),
        _pt("2026-01-01T00:00:30", 3800, under_load=False),
    ]
    result = bh.internal_resistance_ohm(readings)
    # Ohne bekannten Laststrom bleibt der Ohm-Wert unbekannt (nicht raten).
    assert result["r_ohm"] is None
    assert result["n_pairs"] == 1


def test_internal_resistance_no_pairs():
    readings = [_pt("2026-01-01T00:00:00", 3900, under_load=True)]
    assert bh.internal_resistance_ohm(readings)["n_pairs"] == 0


# ── estimate_remaining_days ───────────────────────────────────────────

def test_estimate_remaining_days():
    assert bh.estimate_remaining_days({"mv_per_day": -100.0}, 3500) == 3.5
    assert bh.estimate_remaining_days({"mv_per_day": -100.0}, 3000) == 0.0
    assert bh.estimate_remaining_days({"mv_per_day": None}, 3500) is None
    assert bh.estimate_remaining_days(None, 3500) is None
    assert bh.estimate_remaining_days({"mv_per_day": 10.0}, 3500) is None
    assert bh.estimate_remaining_days({"mv_per_day": -100.0}, None) is None


# ── analyze_sensor ────────────────────────────────────────────────────

def test_analyze_sensor_empty():
    result = bh.analyze_sensor("1", [], hw_rev=2, battery_id="B1")
    assert result["sensor_id"] == "1"
    assert result["hw_rev"] == 2
    assert result["battery_id"] == "B1"
    assert result["n_readings"] == 0
    assert result["latest_mv"] is None
    assert result["latest_at"] is None
    assert result["level"]["label"] == "unbekannt"
    assert result["cycles"] == 0


def test_analyze_sensor_with_data():
    readings = [
        _pt("2026-01-01T00:00:00", 4100),
        _pt("2026-01-01T12:00:00", 3950),
        _pt("2026-01-02T00:00:00", 3800, under_load=True),
    ]
    result = bh.analyze_sensor("1", readings, expected_interval_s=10.0)
    assert result["n_readings"] == 3
    assert result["latest_mv"] == 3800
    assert result["latest_at"] == "2026-01-02T00:00:00"
    assert result["level"]["under_load"] is True
    assert result["discharge_rate"]["mv_per_day"] is not None
    # grosse Luecken zwischen den Punkten -> Ausfaelle erkannt
    assert len(result["outages"]) >= 1


# ── build_fleet + classify_suspicion ──────────────────────────────────

def test_build_fleet_sorting_and_median():
    per_sensor = {
        "1": {"battery_id": "B1", "hw_rev": 2, "discharge_rate": {"mv_per_day": -100.0},
              "remaining_days": 5.0, "cycles": 2, "latest_mv": 3800,
              "latest_at": "t", "level": {"stage": 3}},
        "2": {"battery_id": "B2", "hw_rev": 2, "discharge_rate": {"mv_per_day": -10.0},
              "remaining_days": 50.0, "cycles": 1, "latest_mv": 4000,
              "latest_at": "t", "level": {"stage": 4}},
        "3": {"battery_id": None, "hw_rev": None, "discharge_rate": {"mv_per_day": None},
              "remaining_days": None, "cycles": 0, "latest_mv": None,
              "latest_at": None, "level": {"stage": None}},
    }
    fleet = bh.build_fleet(per_sensor)
    # Ausreisser (hohe Rate) oben, None ans Ende
    assert fleet["fleet"][0]["sensor_id"] == "1"
    assert fleet["fleet"][-1]["sensor_id"] == "3"
    assert fleet["median_rate_mv_per_day"] == -55.0


def test_build_fleet_empty():
    fleet = bh.build_fleet({})
    assert fleet["fleet"] == []
    assert fleet["median_rate_mv_per_day"] is None


def test_classify_suspicion_branches():
    assert bh.classify_suspicion(None, -100.0, 2)["color"] == "unbekannt"
    assert bh.classify_suspicion(-100.0, None, 2)["color"] == "unbekannt"
    assert bh.classify_suspicion(-100.0, 0.0, 2)["color"] == "unbekannt"
    # Rate ~ Median -> gruen
    assert bh.classify_suspicion(-100.0, -100.0, 2)["color"] == "gruen"
    # 1.5x Median -> gelb
    assert bh.classify_suspicion(-150.0, -100.0, 2)["color"] == "gelb"
    # 3x Median -> rot
    assert bh.classify_suspicion(-300.0, -100.0, 2)["color"] == "rot"


# ── _median ──────────────────────────────────────────────────────────

def test_median():
    assert bh._median([]) == 0.0
    assert bh._median([5.0]) == 5.0
    assert bh._median([1.0, 3.0]) == 2.0
    assert bh._median([1.0, 2.0, 3.0]) == 2.0
