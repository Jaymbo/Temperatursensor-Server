"""
Kalibrierungslogik und Hilfsfunktionen für PT1000-Sensoren.

- Ein-Punkt-Kalibrierung: passt nur R1 an
- Zwei-Punkt-Kalibrierung: passt U0 und R1 an

Enthält Vorwärts-/Rückrechnungsfunktionen und einen Helfer
zum Erzeugen korrigierter Vorschau-Daten.
"""
from __future__ import annotations

from typing import List, Tuple, Dict
import numpy as np
import math


class CalibrationStrategy:
    """Intelligente Kalibrierungsstrategie für PT1000-Temperatursensoren."""

    def __init__(self, current_params: Dict[str, float] | None = None):
        self.default_params: Dict[str, float] = {
            'R0': 1000.0,
            'A': 3.9083e-3,
            'B': -5.775e-7,
            'U0': 3.3,
            'R1': 1000.0,
        }
        self.current_params: Dict[str, float] = (
            current_params.copy() if current_params else self.default_params.copy()
        )

    def determine_calibration_parameters(
        self, calibration_points: List[Tuple[float, float]]
    ) -> Dict[str, float]:
        if not calibration_points:
            raise ValueError("Keine Kalibrierpunkte angegeben")
        n = len(calibration_points)
        measured = np.array([p[0] for p in calibration_points], dtype=float)
        target = np.array([p[1] for p in calibration_points], dtype=float)
        if n == 1:
            return self._calibrate_one_point(measured, target)
        if n == 2:
            return self._calibrate_two_points(measured, target)
        raise ValueError("Es sind maximal zwei Kalibrierpunkte erlaubt.")

    def _calibrate_one_point(self, measured: np.ndarray, target: np.ndarray) -> Dict[str, float]:
        """
        Exakte 1-Punkt-Kalibrierung: R0 wird angepasst, A,B,U0,R1 bleiben unverändert.

        Gegeben ist die gemessene Temperatur t_meas, daraus wird mit aktuellen Parametern
        die Spannung U berechnet. Aus U folgt der gemessene Widerstand R_meas.
        Für das Ziel t_target gilt: R_meas = R0_new * (1 + A*t_target + B*t_target^2).
        Daraus: R0_new = R_meas / (1 + A*t_target + B*t_target^2).
        """
        params = self.current_params.copy()
        t_meas = float(measured[0])
        t_target = float(target[0])
        A, B, U0, R1 = (params[k] for k in ('A', 'B', 'U0', 'R1'))
        U = temperature_to_voltage(t_meas, params)
        if not (isinstance(U, float) and 0.0 < U < U0):
            raise ValueError("Ein-Punkt-Kalibrierung: Ungültige Spannung.")
        # Widerstand aus Spannung (unter Beibehaltung von U0,R1)
        R_meas = U * R1 / (U0 - U)
        denom = (1.0 + A * t_target + B * (t_target ** 2))
        if denom <= 0 or not math.isfinite(denom):
            raise ValueError("Ein-Punkt-Kalibrierung: Ungültiger Nenner für R0.")
        R0_new = R_meas / denom
        if R0_new <= 0 or not math.isfinite(R0_new):  # pragma: no cover
            raise ValueError("Ein-Punkt-Kalibrierung: R0 ungültig.")  # pragma: no cover
        params['R0'] = float(R0_new)
        # Validierung: mit optimierten Parametern muss U -> t_target liefern
        t_back = voltage_to_temperature(U, params)
        if not (isinstance(t_back, float) and abs(t_back - t_target) <= 1e-8):
            raise ValueError("Ein-Punkt-Kalibrierung: Ziel wird nicht exakt getroffen.")
        return params

    def _calibrate_two_points(self, measured: np.ndarray, target: np.ndarray) -> Dict[str, float]:
        """
        Exakte 2-Punkt-Kalibrierung: R0 und A werden angepasst, B,U0,R1 bleiben unverändert.

        Gegeben U1,U2 aus t_meas1, t_meas2 und aktuellen Parametern.
        Daraus R1m = R(U1), R2m = R(U2). Für Ziele t1,t2 gilt:
          R1m = R0 * (1 + A*t1 + B*t1^2)
          R2m = R0 * (1 + A*t2 + B*t2^2)
        => Auflösen liefert eine exakte Formel für A, danach R0.
        """
        cur = self.current_params
        A_cur = float(cur['A'])
        B = float(cur['B'])
        U0 = float(cur['U0'])
        R1 = float(cur['R1'])

        t1, t2 = float(target[0]), float(target[1])
        # Spannungen aus gemessenen Temperaturen mit aktuellen Parametern
        U1 = float(temperature_to_voltage(float(measured[0]), cur))
        U2 = float(temperature_to_voltage(float(measured[1]), cur))
        if not (0.0 < U1 < U0 and 0.0 < U2 < U0):
            raise ValueError("Zwei-Punkt-Kalibrierung: Ungültige Spannungen.")
        # Widerstände aus Spannungen
        R1m = U1 * R1 / (U0 - U1)
        R2m = U2 * R1 / (U0 - U2)

        denom = (R1m * t2 - R2m * t1)
        if abs(denom) < 1e-12:
            raise ValueError("Zwei-Punkt-Kalibrierung: Nenner≈0, unlösbar.")
        num = (R2m - R1m) + B * (R2m * (t1 ** 2) - R1m * (t2 ** 2))
        A_new = num / denom
        # R0 aus erster Gleichung
        f1 = 1.0 + A_new * t1 + B * (t1 ** 2)
        if f1 <= 0 or not math.isfinite(f1):
            raise ValueError("Zwei-Punkt-Kalibrierung: Ungültiger Faktor f1.")
        R0_new = R1m / f1
        if R0_new <= 0 or not math.isfinite(R0_new):  # pragma: no cover
                    raise ValueError("Zwei-Punkt-Kalibrierung: R0 ungültig.")  # pragma: no cover

        out = {**cur, 'R0': float(R0_new), 'A': float(A_new)}
        # Validierung: U1,U2 müssen exakt t1,t2 liefern
        v1, v2 = voltage_to_temperature(U1, out), voltage_to_temperature(U2, out)
        if not (isinstance(v1, float) and isinstance(v2, float) and abs(v1 - t1) <= 1e-8 and abs(v2 - t2) <= 1e-8):
            raise ValueError("Zwei-Punkt-Kalibrierung: Ziele nicht exakt getroffen.")
        return out

    def generate_calibration_curve(
        self,
        optimized_params: Dict[str, float],
        current_params: Dict[str, float] | None = None,
        t_min: float = -10.0,
        t_max: float = 120.0,
        step: float = 1.0,
    ) -> Dict[str, List[float]]:
        from math import isnan
        cur = current_params if current_params is not None else self.current_params
        measured_vals: List[float] = []
        corrected_vals: List[float] = []
        if step <= 0:
            step = 1.0
        T = t_min
        while T <= t_max + 1e-9:
            U = temperature_to_voltage(T, cur)
            corrected_T = voltage_to_temperature(U, optimized_params)
            if isinstance(corrected_T, float) and not isnan(corrected_T):
                measured_vals.append(T)
                corrected_vals.append(corrected_T)
            T += step
        return {"measured": measured_vals, "corrected": corrected_vals}


def temperature_to_voltage(temp: float, params: Dict[str, float]) -> float:
    R0, A, B, U0, R1 = params['R0'], params['A'], params['B'], params['U0'], params['R1']
    R = R0 * (1 + A * temp + B * temp ** 2)
    return U0 * R / (R + R1)


def voltage_to_temperature(voltage: float, params: Dict[str, float]) -> float:
    R0, A, B, U0, R1 = params['R0'], params['A'], params['B'], params['U0'], params['R1']
    if voltage >= U0 or voltage <= 0:
        return float('nan')
    R = voltage * R1 / (U0 - voltage)
    ratio = R / R0
    disc = A * A - 4 * B * (1 - ratio)
    if disc < 0:
        return float('nan')
    return (-A + math.sqrt(disc)) / (2 * B)


def test_conversion_functions() -> None:
    params = {'R0': 1000.0, 'A': 3.9083e-3, 'B': -5.775e-7, 'U0': 3.3, 'R1': 1000.0}
    for t in [0, 10, 20, 30, 40, 50]:
        u = temperature_to_voltage(t, params)
        t_back = voltage_to_temperature(u, params)
        assert abs(t_back - t) < 1e-3


def interpolation_correction(
    temperature: float,
    correction_points: List[Dict[str, float]]
) -> float:
    """Berechnet den Korrekturwert fuer eine gegebene Temperatur.

    Korrekturen werden als punktuelle Abweichung auf die Basis-Temperatur gelegt:
      T_final = T_basis + Korrektur(T_basis)

    - 0 Punkte: Korrektur = 0
    - 1 Punkt: globaler Offset (delta des einen Punktes)
    - 2+ Punkte: lineare Interpolation zwischen benachbarten Kalibrierpunkten,
      Extrapolation (letzte/erste delta) fuer Temperaturen außerhalb des Bereichs.

    :param temperature: Die Basis-Temperatur (berechnet aus CV-D-Parametern)
    :param correction_points: Liste von {"t": gemessene_T, "delta": (soll - ist)},
                              sortiert nach t (wird intern sortiert)
    :return: Korrekturwert (Delta)
    """
    if not correction_points:
        return 0.0

    # Interne Sortierung nach Temperatur
    points = sorted(correction_points, key=lambda p: p["t"])

    if len(points) == 1:
        return float(points[0]["delta"])

    t = float(temperature)

    # Unterhalb des ersten Punktes: Extrapolation (erster delta)
    if t <= points[0]["t"]:
        return float(points[0]["delta"])

    # Ueber dem letzten Punkt: Extrapolation (letzter delta)
    if t >= points[-1]["t"]:
        return float(points[-1]["delta"])

    # Lineare Interpolation zwischen benachbarten Punkten
    for i in range(len(points) - 1):
        t0, d0 = float(points[i]["t"]), float(points[i]["delta"])
        t1, d1 = float(points[i + 1]["t"]), float(points[i + 1]["delta"])
        if t0 <= t <= t1:
            if t1 == t0:  # pragma: no cover
                return d0  # pragma: no cover
            return d0 + (d1 - d0) * (t - t0) / (t1 - t0)

    # Sollte nicht erreichbar sein, aber zur Sicherheit
    return float(points[-1]["delta"])  # pragma: no cover


def inverse_correction(
    corrected_temperature: float,
    correction_points: List[Dict[str, float]]
) -> float:
    """Berechnet den Rohwert aus einer korrigierten Temperatur.

    Lost T_raw sodass: T_raw + Korrektur(T_raw) = corrected_temperature.

    - 0 Punkte: Korrektur = 0, also T_raw = corrected_temperature
    - 1 Punkt: globaler Offset, also T_raw = corrected - delta
    - 2+ Punkte: lineare Suche im richtigen Intervall
      Innerhalb eines Intervalls [t0,t1] mit deldel d0,d1 gilt:
        T_cor = T * (1 + slope) + d0 - slope * t0
        wobei slope = (d1 - d0) / (t1 - t0)
        Also: T_raw = (T_cor - d0 + slope * t0) / (1 + slope)

    :param corrected_temperature: Die korrigierte Temperatur (das was der User sieht)
    :param correction_points: Korrekturpunkte indexiert nach Roh-Temperatur
    :return: Roh-Temperatur
    """
    if not correction_points:
        return float(corrected_temperature)

    points = sorted(correction_points, key=lambda p: p["t"])
    t_cor = float(corrected_temperature)

    if len(points) == 1:
        return t_cor - float(points[0]["delta"])

    # Unterhalb des ersten Punktes: Extrapolation (d0)
    t0, d0 = float(points[0]["t"]), float(points[0]["delta"])
    if t_cor <= t0 + d0:
        return t_cor - d0

    # Oberhalb des letzten Punktes: Extrapolation (d_last)
    t_last, d_last = float(points[-1]["t"]), float(points[-1]["delta"])
    if t_cor >= t_last + d_last:
        return t_cor - d_last

    # In jedem Intervall die inverse Gleichung loesen
    for i in range(len(points) - 1):
        t0, d0 = float(points[i]["t"]), float(points[i]["delta"])
        t1, d1 = float(points[i + 1]["t"]), float(points[i + 1]["delta"])
        slope = (d1 - d0) / (t1 - t0)
        # Korrekturbedingtes Intervall der korrigierten Temperaturen
        cor_low = t0 + d0
        cor_high = t1 + d1
        # Sicherstellen dass cor_low <= cor_high (swap wenn umgekehrt)
        if cor_low > cor_high:
            cor_low, cor_high = cor_high, cor_low  # pragma: no cover (defensiv, numerisch kaum erreichbar)
        if cor_low <= t_cor <= cor_high:
            factor = 1.0 + slope
            if abs(factor) < 1e-15:
                return t0  # pragma: no cover (defensiv, degenerierter Fall kaum erreichbar)
            raw_t = (t_cor - d0 + slope * t0) / factor
            return raw_t

    # Fallback: sollte nicht erreicht werden
    return t_cor - float(points[-1]["delta"])  # pragma: no cover


def merge_correction_points(
    old_points: List[Dict[str, float]],
    new_points: List[Dict[str, float]],
) -> List[Dict[str, float]]:
    """Merge neue Korrekturpunkte in bestehende.

    Neue Punkte werden auf Rohbasis erwartet (bereits umgerechnet).
    Bei gleichem t-Wert (innerhalb Toleranz) wird der neue verwendet.
    Sonst werden neue Punkte hinzugefügt. Ergebnis wird nach t sortiert.

    :param old_points: Bereits existierende Korrekturpunkte
    :param new_points: Neue Korrekturpunkte (Roh-basiert)
    :return: Gemergte und sortierte Liste
    """
    merged: List[Dict[str, float]] = [dict(p) for p in old_points]
    tolerance = 0.01  # Gleicher t-Wert wenn Differenz < 0.01°C

    for new_pt in new_points:
        found = False
        for i, old_pt in enumerate(merged):
            if abs(old_pt["t"] - new_pt["t"]) < tolerance:
                merged[i] = {"t": new_pt["t"], "delta": new_pt["delta"]}
                found = True
                break
        if not found:
            merged.append({"t": float(new_pt["t"]), "delta": float(new_pt["delta"])})

    return sorted(merged, key=lambda p: p["t"])


def apply_correction(
    temperature: float,
    correction_points: List[Dict[str, float]]
) -> float:
    """Anwendet die Korrektur auf eine Temperatur und gibt T_final zurueck."""
    return temperature + interpolation_correction(temperature, correction_points)


def generate_corrected_preview_data(
    sensor_id: str,
    correction_points: List[Dict[str, float]] | None = None,
) -> Dict[str, List[float]] | None:
    """Generiert korrigierte Messdaten fuer eine Preview-Session.

    Wendet apply_correction auf alle gespeicherten Messwerte der Session an.

    :param sensor_id: Die Session-ID (z.B. "1_15")
    :param correction_points: [{"t": gemessene_T, "delta": (soll - ist)}, ...]
    :return: {"timestamps": [...], "temperatures": [...]} oder None
    """
    try:
        from db import get_data_by_sensor
        original = get_data_by_sensor(sensor_id)
        if not original:
            return None
        correction_points = correction_points or []
        ts: List[str] = []
        temps: List[float] = []
        ok = 0
        for p in original:
            if not isinstance(p, dict):
                continue
            timestamp = p.get('timestamp')
            temp = p.get('temperature')
            if timestamp is None or temp is None:
                continue
            try:
                t2 = apply_correction(float(temp), correction_points)
                if not math.isnan(t2):
                    ts.append(timestamp)
                    temps.append(t2)
                    ok += 1
            except (ValueError, TypeError):
                continue
        if ok == 0:
            return None
        return {"timestamps": ts, "temperatures": temps}
    except Exception:
        return None

