import math
import pytest


def test_determine_params_empty_raises():
    from calibration_strategy import CalibrationStrategy

    cs = CalibrationStrategy()
    with pytest.raises(ValueError):
        cs.determine_calibration_parameters([])


def test_determine_params_too_many_points_direct_call():
    from calibration_strategy import CalibrationStrategy

    cs = CalibrationStrategy()
    with pytest.raises(ValueError):
        cs.determine_calibration_parameters([(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)])


def test_two_point_calibration_f1_not_finite(monkeypatch):
    # Force B = NaN but keep U1,U2 valid by monkeypatching temperature_to_voltage
    import calibration_strategy as cs

    params = {"R0": 1000.0, "A": 3.9083e-3, "B": float('nan'), "U0": 3.3, "R1": 1000.0}
    strat = cs.CalibrationStrategy(params)

    # Return valid voltages regardless of inputs
    monkeypatch.setattr(cs, "temperature_to_voltage", lambda t, p: 1.0 if t == 10.0 else 1.5)

    with pytest.raises(ValueError):
        strat.determine_calibration_parameters([(10.0, 10.0), (20.0, 20.0)])


def test_one_point_calibration_target_not_met(monkeypatch):
    # Make the back-conversion return a wrong temperature to trigger the exactness check
    import calibration_strategy as cs

    strat = cs.CalibrationStrategy()

    # Keep forward conversion real, but break backward validation to be off by > 1e-8
    monkeypatch.setattr(cs, "voltage_to_temperature", lambda u, p: 19.0)

    with pytest.raises(ValueError):
        strat.determine_calibration_parameters([(20.0, 20.0)])


def test_one_point_calibration_success():
    """Erfolgreicher Pfad von _calibrate_one_point (return params)."""
    from calibration_strategy import CalibrationStrategy
    strat = CalibrationStrategy()
    params = strat.determine_calibration_parameters([(20.0, 20.0)])
    assert params is not None
    assert "R0" in params


def test_two_point_calibration_success():
    """Erfolgreicher Pfad von _calibrate_two_points inkl. Validierung."""
    from calibration_strategy import CalibrationStrategy
    strat = CalibrationStrategy()
    params = strat.determine_calibration_parameters([(10.0, 10.0), (30.0, 30.0)])
    assert params is not None
    assert "R0" in params
    assert "A" in params


def test_two_point_calibration_validation_fails(monkeypatch):
    """2-Punkt: Validierung (Ziele nicht exakt getroffen) → ValueError."""
    from calibration_strategy import CalibrationStrategy
    import calibration_strategy as cs

    strat = CalibrationStrategy()
    # U1,U2 normal berechnen lassen, aber voltage_to_temperature für Validierung absichtlich falsch
    original_v2t = cs.voltage_to_temperature
    call_count = [0]

    def fake_v2t(u, p):
        call_count[0] += 1
        # Die ersten beiden Aufrufe in _calibrate_two_points sind U1,U2 Berechnung (über temperature_to_voltage, nicht v2t)
        # Der dritte Aufruf ist die Validierung → absichtlich falsches Ergebnis
        return 999.0

    monkeypatch.setattr(cs, "voltage_to_temperature", fake_v2t)
    with pytest.raises(ValueError):
        strat.determine_calibration_parameters([(10.0, 10.0), (30.0, 30.0)])


def test_test_conversion_functions():
    """test_conversion_functions() muss existieren und durchlaufen."""
    from calibration_strategy import test_conversion_functions
    test_conversion_functions()


def test_apply_correction():
    """apply_correction wendet Korrektur an und gibt T_final zurück."""
    from calibration_strategy import apply_correction
    # Globaler Offset
    assert abs(apply_correction(20.0, [{"t": 20.0, "delta": -2.0}]) - 18.0) < 1e-9
    # 0 Punkte → keine Korrektur
    assert apply_correction(20.0, []) == 20.0


def test_generate_calibration_curve_step_zero():
    """step <= 0 → wird auf 1.0 zurückgesetzt."""
    from calibration_strategy import CalibrationStrategy
    strat = CalibrationStrategy()
    curve = strat.generate_calibration_curve(
        optimized_params=strat.current_params,
        current_params=strat.current_params,
        step=0.0,
    )
    assert len(curve["measured"]) > 0
    assert len(curve["corrected"]) > 0


def test_generate_calibration_curve_isnan_branch():
    """Wenn voltage_to_temperature NaN liefert, wird der Punkt übersprungen."""
    from calibration_strategy import CalibrationStrategy
    # Params mit extremen Werten, sodass disc < 0 → NaN
    params = {"R0": 1000.0, "A": 3.9083e-3, "B": -5.775e-7, "U0": 3.3, "R1": 1000.0}
    opt = {"R0": 1000.0, "A": 3.9083e-3, "B": 1e-3, "U0": 3.3, "R1": 1000.0}
    strat = CalibrationStrategy(params)
    curve = strat.generate_calibration_curve(
        optimized_params=opt,
        current_params=params,
        t_min=0.0,
        t_max=5.0,
        step=1.0,
    )
    assert "measured" in curve
    assert "corrected" in curve


def test_voltage_to_temperature_disc_negative():
    """disc < 0 in voltage_to_temperature → NaN."""
    from calibration_strategy import voltage_to_temperature
    import math
    # disc = A² - 4*B*(1 - ratio). Mit A=0.5, B=1.0, ratio=0.833 → disc < 0
    params = {"R0": 1000.0, "A": 0.5, "B": 1.0, "U0": 3.3, "R1": 1000.0}
    result = voltage_to_temperature(1.5, params)
    assert math.isnan(result)


def test_interpolation_correction_t1_eq_t0():
    """t1 == t0 branch in interpolation_correction (within for-loop, not extrapolation)."""
    from calibration_strategy import interpolation_correction
    # t=20 must strictly be > points[0].t and < points[-1].t to enter the loop.
    # Points 1 and 2 share the same temperature, so loop finds t1 == t0.
    points = [
        {"t": 10.0, "delta": -1.0},
        {"t": 20.0, "delta": -3.0},
        {"t": 20.0, "delta": -5.0},
        {"t": 30.0, "delta": 0.0},
    ]
    result = interpolation_correction(20.0, points)
    assert abs(result - (-3.0)) < 1e-9


def test_two_point_calibration_r0_invalid(monkeypatch):
    """R0_new <= 0 oder nicht-finit → ValueError bei 2-Punkt-Kalibrierung."""
    from calibration_strategy import CalibrationStrategy
    import calibration_strategy as cs

    params = {"R0": 1000.0, "A": 3.9083e-3, "B": -5.775e-7, "U0": 3.3, "R1": 1000.0}
    strat = CalibrationStrategy(params)

    # U1 > U0 → R1m negativ → R0_new < 0
    monkeypatch.setattr(cs, "temperature_to_voltage", lambda t, p: 4.0 if t == 10.0 else 1.5)

    with pytest.raises(ValueError):
        strat.determine_calibration_parameters([(10.0, 10.0), (20.0, 20.0)])
