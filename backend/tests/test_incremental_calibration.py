"""Tests für inverse_correction, merge_correction_points und inkrementelle Kalibrierung."""
import json
import db as dbmod
from calibration_strategy import (
    inverse_correction,
    merge_correction_points,
    apply_correction,
    interpolation_correction,
)


class TestInverseCorrection:
    """inverse_correction: T_korrigiert -> T_roh"""

    def test_no_points(self):
        assert inverse_correction(20.0, []) == 20.0

    def test_single_point_offset(self):
        """1 Punkt: globaler Offset +5 → T_raw = T_cor - 5"""
        points = [{"t": 25.0, "delta": 5.0}]
        assert abs(inverse_correction(30.0, points) - 25.0) < 1e-9
        assert abs(inverse_correction(20.0, points) - 15.0) < 1e-9

    def test_single_point_negative_offset(self):
        points = [{"t": 10.0, "delta": -3.0}]
        assert abs(inverse_correction(8.0, points) - 11.0) < 1e-9
        assert abs(inverse_correction(5.0, points) - 8.0) < 1e-9

    def test_two_points_on_first(self):
        """An Kalibrierpunkt t0: T_raw = t0 genau"""
        points = [{"t": 0.0, "delta": 5.0}, {"t": 10.0, "delta": 10.0}]
        assert abs(inverse_correction(5.0, points) - 0.0) < 1e-9
        assert abs(inverse_correction(20.0, points) - 10.0) < 1e-9

    def test_two_points_mid(self):
        """Mittig: Rohwert muss linear interpoliert sein"""
        points = [{"t": 0.0, "delta": 5.0}, {"t": 10.0, "delta": 15.0}]
        # Bei T_cor=10: slope=1, T_raw=(10-5+1*0)/(1+1) = 2.5
        raw = inverse_correction(10.0, points)
        assert abs(raw - 2.5) < 1e-9

    def test_below_first_point(self):
        """Extrapolation unterhalb: erster delta"""
        points = [{"t": 20.0, "delta": 5.0}, {"t": 40.0, "delta": 10.0}]
        assert abs(inverse_correction(22.0, points) - 17.0) < 1e-9  # 22 - 5 = 17

    def test_above_last_point(self):
        """Extrapolation oberhalb: letzter delta"""
        points = [{"t": 20.0, "delta": 5.0}, {"t": 40.0, "delta": 10.0}]
        assert abs(inverse_correction(55.0, points) - 45.0) < 1e-9  # 55 - 10 = 45

    def test_roundtrip(self):
        """apply → inverse → wieder Rohwert"""
        points = [{"t": 0.0, "delta": 5.0}, {"t": 10.0, "delta": 10.0}]
        for raw in [-5.0, 0.0, 5.0, 10.0, 20.0]:
            corrected = apply_correction(raw, points)
            back = inverse_correction(corrected, points)
            assert abs(back - raw) < 1e-9


class TestMergeCorrectionPoints:
    """merge_correction_points: neue + alte Punkte zusammenführen"""

    def test_empty_old(self):
        old = []
        new = [{"t": 10.0, "delta": 5.0}]
        result = merge_correction_points(old, new)
        assert len(result) == 1
        assert result[0]["t"] == 10.0

    def test_empty_new(self):
        old = [{"t": 5.0, "delta": 3.0}]
        new = []
        result = merge_correction_points(old, new)
        assert len(result) == 1
        assert result[0]["t"] == 5.0

    def test_replace_existing(self):
        """Gleicher t-Wert → neuer delta ersetzt alten"""
        old = [{"t": 10.0, "delta": 2.0}]
        new = [{"t": 10.0, "delta": 6.0}]
        result = merge_correction_points(old, new)
        assert len(result) == 1
        assert result[0]["delta"] == 6.0

    def test_replace_within_tolerance(self):
        """t-Wert innerhalb 0.01 → Ersatz"""
        old = [{"t": 10.0, "delta": 2.0}]
        new = [{"t": 10.005, "delta": 6.0}]
        result = merge_correction_points(old, new)
        assert len(result) == 1
        assert result[0]["delta"] == 6.0

    def test_add_new(self):
        """Neuer t-Wert wird hinzugefügt"""
        old = [{"t": 0.0, "delta": 5.0}]
        new = [{"t": 10.0, "delta": 10.0}]
        result = merge_correction_points(old, new)
        assert len(result) == 2

    def test_sorted(self):
        """Ergebnis ist nach t sortiert"""
        old = [{"t": 20.0, "delta": 3.0}]
        new = [{"t": 5.0, "delta": 1.0}, {"t": 15.0, "delta": 2.0}]
        result = merge_correction_points(old, new)
        ts = [p["t"] for p in result]
        assert ts == sorted(ts)


class TestIncrementalCalibrationEndpoint:
    """End-to-End: /calibrate mit bestehenden Korrekturpunkten"""

    def _ensure_session(self, sensor_id: str):
        try:
            dbmod.get_latest_session_for_sensor_id(sensor_id)
        except Exception:
            import sqlite3
            conn = sqlite3.connect(dbmod.DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO sessions (sensor_id, start_time, calibration_data) "
                "VALUES (?, datetime('now','localtime'), NULL)",
                (sensor_id,),
            )
            conn.commit()
            conn.close()

    def _store_correction(self, sensor_id: str, points: str):
        dbmod.add_calibration(sensor_id, "", points)

    def test_first_calibration_no_existing(self, client):
        """Erste Kalibrierung ohne bestehende Punkte → wie vorher"""
        sensor_id = "inc1"
        self._ensure_session(sensor_id)
        payload = {
            "sensor_session": sensor_id,
            "calibration_points": [{"measured": 10.0, "target": 15.0}],
        }
        res = client.post("/calibrate", json=payload)
        assert res.status_code == 200
        result = res.json()
        cp = result["correction_points"]
        assert len(cp) == 1
        assert abs(cp[0]["t"] - 10.0) < 0.01
        assert abs(cp[0]["delta"] - 5.0) < 0.01

    def test_second_calibration_builds_on_first(self, client):
        """
        Kalibrierung #1: Roh 10 → delta+5 (zeigt 15)
        Kalibrierung #2: User klickt bei Roh 10, sagt "soll 16 sein"
        → Rohwert bleibt 10, neues delta = 6, ersetzt alten bei t=10
        """
        sensor_id = "inc2"
        self._ensure_session(sensor_id)
        self._store_correction(sensor_id, json.dumps([{"t": 10.0, "delta": 5.0}]))

        # User klickt bei Rohwert 10, sagt "soll 16 sein"
        payload = {
            "sensor_session": sensor_id,
            "calibration_points": [{"measured": 10.0, "target": 16.0}],
        }
        res = client.post("/calibrate", json=payload)
        assert res.status_code == 200
        result = res.json()
        cp = result["correction_points"]
        assert len(cp) == 1
        assert abs(cp[0]["t"] - 10.0) < 0.01
        assert abs(cp[0]["delta"] - 6.0) < 0.01
        # Rohwert 10 → sollte bei 16 landen
        corrected = apply_correction(10.0, cp)
        assert abs(corrected - 16.0) < 0.01

    def test_calibrate_is_preview_only_no_merge(self, client):
        """
        POST /calibrate ist eine reine Vorschau: es wird NICHTS mit dem
        DB-Stand gemerged. Die gesendeten Punkte bilden die (einzige)
        Punktmenge.
        """
        sensor_id = "inc3"
        self._ensure_session(sensor_id)
        # DB enthält bereits Punkte – dürfen NICHT in die Vorschau einfließen
        self._store_correction(
            sensor_id, json.dumps([{"t": 0.0, "delta": 5.0}, {"t": 10.0, "delta": 10.0}])
        )

        payload = {
            "sensor_session": sensor_id,
            "calibration_points": [{"measured": 5.0, "target": 8.0}],
        }
        res = client.post("/calibrate", json=payload)
        assert res.status_code == 200
        result = res.json()
        cp = result["correction_points"]
        # Kein Merge: nur der gesendete Punkt, DB-Stand wird ignoriert
        assert len(cp) == 1
        assert abs(cp[0]["t"] - 5.0) < 0.01
        assert abs(cp[0]["delta"] - 3.0) < 0.01
        # Bei Roh=5 sollte korrigiert ~8 sein
        corrected = apply_correction(5.0, cp)
        assert abs(corrected - 8.0) < 0.01

    def test_replace_same_raw_point(self, client):
        """
        Erste Kalibrierung: t=10, delta=5 (zeigt 15)
        Zweite: User klickt bei Roh 10, sagt "soll 13 sein"
        → Rohwert bleibt 10, neues delta = 3, ersetzt alten bei t=10
        """
        sensor_id = "inc4"
        self._ensure_session(sensor_id)
        self._store_correction(sensor_id, json.dumps([{"t": 10.0, "delta": 5.0}]))

        payload = {
            "sensor_session": sensor_id,
            "calibration_points": [{"measured": 10.0, "target": 13.0}],
        }
        res = client.post("/calibrate", json=payload)
        assert res.status_code == 200
        result = res.json()
        cp = result["correction_points"]
        assert len(cp) == 1
        assert abs(cp[0]["t"] - 10.0) < 0.01
        assert abs(cp[0]["delta"] - 3.0) < 0.01
        assert abs(apply_correction(10.0, cp) - 13.0) < 0.01

    def test_calibrate_then_apply_replaces_not_accumulates(self, client):
        """
        Neues Verhalten:
        1. POST /calibrate ist eine reine Vorschau – schreibt NICHTS in die DB
           und mergt NICHT mit bestehenden Punkten.
        2. POST /calibration persistiert und ERSETZT die alten Punkte.
        3. _calibrated-Preview liest den persistenten (eretzten) Stand.
        """
        import sqlite3

        sensor_id = "inc5"
        self._ensure_session(sensor_id)

        # Füge variierende Messdaten ein
        conn = sqlite3.connect(dbmod.DB_PATH)
        cur = conn.cursor()
        session_id = dbmod.get_latest_session_for_sensor_id(sensor_id)
        for i, temp in enumerate([33.0, 34.0, 35.0, 36.0, 37.0]):
            cur.execute(
                "INSERT OR IGNORE INTO measurements (session_id, timestamp, temperature) VALUES (?, ?, ?)",
                (session_id, f"2024-01-01T00:0{i}:00", temp),
            )
        conn.commit()
        conn.close()
        sensor_session = f"{sensor_id}_{session_id}"

        # Vorschau #1: Roh 33 → soll 34 sein (delta +1). Kein Merge, kein DB-Write.
        res1 = client.post("/calibrate", json={
            "sensor_session": sensor_session,
            "calibration_points": [{"measured": 33.0, "target": 34.0}],
        })
        assert res1.status_code == 200
        cp1 = res1.json()["correction_points"]
        assert len(cp1) == 1
        assert abs(cp1[0]["delta"] - 1.0) < 0.01

        # Anwenden (persistiert) via POST /calibration
        client.post("/calibration", json={
            "sensor_id": sensor_id,
            "correction_points": json.dumps(cp1),
        })

        # _calibrated Preview liest den persistenten Stand (delta+1)
        res_preview = client.get(f"/series/{sensor_session}_calibrated")
        assert res_preview.status_code == 200
        preview_data = res_preview.json()["data"]
        assert len(preview_data) == 5
        temps_after_first = [p["temperature"] for p in preview_data]
        assert abs(temps_after_first[0] - 34.0) < 0.01
        assert abs(temps_after_first[1] - 35.0) < 0.01
        assert abs(temps_after_first[4] - 38.0) < 0.01
        assert len(set(temps_after_first)) > 1, f"Erste Kalibrierung mappt alles auf denselben Wert: {temps_after_first}"

        # Vorschau #2: Roh 33 → soll 29.9 sein (delta -3.1) – Ersetzen, NICHT Akkumulieren
        res2 = client.post("/calibrate", json={
            "sensor_session": sensor_session,
            "calibration_points": [{"measured": 33.0, "target": 29.9}],
        })
        assert res2.status_code == 200
        cp2 = res2.json()["correction_points"]
        assert len(cp2) == 1
        assert abs(cp2[0]["t"] - 33.0) < 0.01
        assert abs(cp2[0]["delta"] - (-3.1)) < 0.01

        # Anwenden (überschreibt die alten Punkte vollständig)
        client.post("/calibration", json={
            "sensor_id": sensor_id,
            "correction_points": json.dumps(cp2),
        })

        # _calibrated Preview muss den ERSETZTEN Stand zeigen, NICHT alles auf 29.9
        res_preview2 = client.get(f"/series/{sensor_session}_calibrated")
        assert res_preview2.status_code == 200
        preview_data2 = res_preview2.json()["data"]
        temps_after_second = [p["temperature"] for p in preview_data2]
        # Bei delta-3.1: 33→29.9, 34→30.9, 35→31.9, 36→32.9, 37→33.9
        assert abs(temps_after_second[0] - 29.9) < 0.01, f"Erster Punkt: {temps_after_second[0]}"
        assert abs(temps_after_second[1] - 30.9) < 0.01, f"Zweiter Punkt: {temps_after_second[1]}"
        assert abs(temps_after_second[4] - 33.9) < 0.01, f"Fünfter Punkt: {temps_after_second[4]}"
        assert len(set(temps_after_second)) > 1, f"Zweite Kalibrierung mappt alles auf {temps_after_second[0]}: {temps_after_second}"