"""Zyklus-Erkennung und Oszilloskop-Overlay fuer die Strommessung.

Der Mess-ESP (Wemos D1 mini + INA226) streamt ROHPROBEN (Dauerstream @ 200 Hz).
Diese Modul leitet daraus OSZILLOSKOP-ARTIG die Messzyklen des Loggers ab.

Zyklus-Definition (peak-basiert, kalibriert an echten Daten: Der gemessene
Logger zieht dauerhaft ~74 mA Grundlast - eine "Ruhe"-Erkennung unter 2 mA
kommt nie zustande und wuerde jeden Zyklus an max_cycle_s abschneiden):

  * Trigger  : ERSTES Sample, das peak_trigger_ma ueberschreitet (ein Sample
               reicht - der TX-Burst IST das Signal). Danach gilt fuer
               refractory_s KEIN neuer Trigger (Debounce, damit der Peak-Lauf
               von bis zu ~2 s nicht mehrfach zaehlt).
  * Zyklus   : startet pre_peak_s VOR dem Trigger (Vorlauf-Fenster) und endet
               pre_peak_s vor dem naechsten Trigger. Der letzte Zyklus laeuft
               bis zum Streamende (Deckel max_cycle_s, dann truncated).
  * Landmarks: trigger (der >-Moment) und spike_end (erster Fall unter
               spike_end_ma nach dem Trigger = Ende des TX-Bursts).
  * Phasen   : pre_peak (Grundlast-Vorlauf), spike (TX-Burst), tail (Rest).
  * Overlay  : Alle Zyklen werden auf t=0 (Zyklusanfang) synchronisiert, damit
               sie wie am Oszilloskop uebereinander gelegt werden koennen.

Die Erkennung ist bewusst SCHWELLEN-BASIERT und wird an echten Messdaten
kalibriert (Schwellwerte sind Parameter mit Defaults). Wie bei battery_health
wird NICHTS persistiert - die Rohdaten bleiben unberuehrt, die Erkennung ist
jederzeit aenderbar.
"""

from __future__ import annotations

import bisect
import datetime
import statistics

# --- Defaults (an echten Daten kalibriert; alle Endpoints uebernehmen sie) ---
DEFAULT_PEAK_TRIGGER_MA = 100.0  # Trigger = erste Ueberschreitung (TX-Burst)
DEFAULT_PRE_PEAK_S = 5.0         # Zyklus startet so viel VOR dem Trigger
DEFAULT_REFRACTORY_S = 10.0      # nach einem Trigger kein neuer Peak-Trigger
DEFAULT_SPIKE_END_MA = 85.0      # darunter: TX-Burst vorbei (zwischen
                                 # Grundlast ~74 mA und Peak ~135 mA)
DEFAULT_MAX_CYCLE_S = 120.0      # Deckel fuer den UNTERBROCHENEN letzten Zyklus
DEFAULT_GAP_S = 5.0              # Datenloch im Zyklusfenster -> gap-Markierung

# Overlay-Rendering: max. Punkte je Zyklus nach LTTB-Downsampling. 500 Punkte
# rendern zuegig; die volle Aufloesung liefert der Endpoint beim Reinzoomen
# (window_start_s/window_end_s) nach.
DEFAULT_MAX_POINTS_PER_CYCLE = 500


def _parse_iso(value):
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _prepare_samples(measurements: list[dict]) -> list[tuple[float, float]]:
    """
    Rohproben -> [(t_s, current_ma)] mit t_s = Sekunden seit erster Probe.

    Nicht parsebare Zeilen werden verworfen. Ein negatives current_ma (Laden)
    wird auf 0 geklemmt: Fuer die Zyklus-Phasen zaehlt nur Entladen.
    """
    base = None
    out = []
    for m in measurements:
        t = _parse_iso(m.get("measured_at"))
        ma = m.get("current_ma")
        if t is None or ma is None:
            continue
        if base is None:
            base = t
        t_s = (t - base).total_seconds()
        out.append((t_s, max(0.0, float(ma))))
    out.sort(key=lambda p: p[0])
    return out


def _find_trigger_indices(
    samples: list[tuple[float, float]],
    peak_trigger_ma: float,
    refractory_s: float,
) -> list[int]:
    """
    Indizes aller Peak-Trigger: erstes Sample ueber peak_trigger_ma, danach
    fuer refractory_s gesperrt (Debounce - der Peak-Lauf zaehlt nur einmal).
    """
    triggers: list[int] = []
    refractory_until = None
    for i, (t, ma) in enumerate(samples):
        if ma > peak_trigger_ma and (refractory_until is None or t >= refractory_until):
            triggers.append(i)
            refractory_until = t + refractory_s
    return triggers


def segment_cycles(
    samples: list[tuple[float, float]],
    peak_trigger_ma: float = DEFAULT_PEAK_TRIGGER_MA,
    pre_peak_s: float = DEFAULT_PRE_PEAK_S,
    refractory_s: float = DEFAULT_REFRACTORY_S,
    max_cycle_s: float = DEFAULT_MAX_CYCLE_S,
    gap_s: float = DEFAULT_GAP_S,
) -> list[dict]:
    """
    Segmentiert den Stromverlauf peak-basiert in Zyklen.

    Ein Zyklus startet pre_peak_s vor seinem Trigger und endet pre_peak_s vor
    dem naechsten Trigger; der letzte Zyklus laeuft bis zum Streamende (bei
    laenger als max_cycle_s: abgeschnitten, truncated=True). Ein Datenloch
    > gap_s INNERHALB des Zyklusfensters wird als gap markiert (die Ladungs-
    Integration ueberspringt solche Schritte bereits).

    @return Liste von {start_idx, end_idx, trigger_idx, truncated, gap}
    """
    triggers = _find_trigger_indices(samples, peak_trigger_ma, refractory_s)
    cycles: list[dict] = []
    for k, trig_idx in enumerate(triggers):
        t_start = samples[trig_idx][0] - pre_peak_s
        # ersten Sample im Vorlauf-Fenster suchen (nicht vor dem Streamanfang)
        start_idx = trig_idx
        while start_idx > 0 and samples[start_idx - 1][0] >= t_start:
            start_idx -= 1

        truncated = False
        if k + 1 < len(triggers):
            # Ende: letzter Sample VOR dem Start des naechsten Zyklus.
            t_next_start = samples[triggers[k + 1]][0] - pre_peak_s
            end_idx = triggers[k + 1]
            while end_idx > start_idx and samples[end_idx][0] >= t_next_start:
                end_idx -= 1
        else:
            # Letzter (ggf. unvollstaendiger) Zyklus: bis zum Streamende,
            # mit max_cycle_s als Deckel gegen "Peak hoert nie auf".
            end_idx = len(samples) - 1
            if samples[end_idx][0] - samples[start_idx][0] > max_cycle_s:
                truncated = True
                end_idx = start_idx
                while (end_idx + 1 < len(samples)
                       and samples[end_idx + 1][0] - samples[start_idx][0] <= max_cycle_s):
                    end_idx += 1

        gap = False
        for j in range(start_idx + 1, end_idx + 1):
            if samples[j][0] - samples[j - 1][0] > gap_s:
                gap = True
                break

        cycles.append({
            "start_idx": start_idx,
            "end_idx": end_idx,
            "trigger_idx": trig_idx,
            "truncated": truncated,
            "gap": gap,
        })
    return cycles


def _spike_end_idx(
    samples: list[tuple[float, float]],
    start_idx: int,
    end_idx: int,
    spike_end_ma: float,
) -> int | None:
    """
    Ende des TX-Bursts: erster Index >= start_idx, dessen Strom UNTER
    spike_end_ma faellt (Burst vorbei). @return Index oder None.
    """
    for i in range(start_idx, end_idx + 1):
        if samples[i][1] < spike_end_ma:
            return i
    return None


def extract_landmarks(
    samples: list[tuple[float, float]],
    start_idx: int,
    end_idx: int,
    trigger_idx: int,
    spike_end_ma: float = DEFAULT_SPIKE_END_MA,
) -> dict:
    """
    Landmarks eines Zyklus (t relativ zum Zyklusanfang, t0 = Trigger - pre).

    trigger  : der Peak-Trigger selbst (wird aus der Segmentierung uebernommen,
               nicht neu gesucht)
    spike_end: erster Fall unter spike_end_ma nach dem Trigger (None, wenn der
               Strom bis zum Zyklusende darueber bleibt)
    """
    t0 = samples[start_idx][0]
    out: dict = {
        "trigger": {"start_s": samples[trigger_idx][0] - t0, "idx": trigger_idx},
    }
    sp = _spike_end_idx(samples, trigger_idx, end_idx, spike_end_ma)
    if sp is None:
        out["spike_end"] = None
    else:
        out["spike_end"] = {"start_s": samples[sp][0] - t0, "idx": sp}
    return out


def analyze_cycles_from_samples(
    samples: list[tuple[float, float]],
    peak_trigger_ma: float = DEFAULT_PEAK_TRIGGER_MA,
    pre_peak_s: float = DEFAULT_PRE_PEAK_S,
    refractory_s: float = DEFAULT_REFRACTORY_S,
    spike_end_ma: float = DEFAULT_SPIKE_END_MA,
    max_cycle_s: float = DEFAULT_MAX_CYCLE_S,
    gap_s: float = DEFAULT_GAP_S,
    t_base_iso: str | None = None,
) -> dict:
    """
    Vollanalyse aus bereits aufbereiteten Samples (siehe _prepare_samples).

    Von analyze_cycles getrennt, damit der Overlay-Cache die Analyse ohne
    erneutes ISO-Parsen/Laden der Rohproben aufrufen kann.

    @return {"cycles": [...], "stats": {...}} (gleiche Struktur wie
            analyze_cycles)
    """
    if not samples:
        return {"cycles": [], "stats": {}}

    # Akzeptiert ISO-String oder datetime; normiert auf datetime.
    t_base = (t_base_iso if isinstance(t_base_iso, datetime.datetime)
              else _parse_iso(t_base_iso))

    segments = segment_cycles(samples, peak_trigger_ma, pre_peak_s, refractory_s,
                              max_cycle_s, gap_s)
    cycles: list[dict] = []
    for seg in segments:
        s_idx, e_idx = seg["start_idx"], seg["end_idx"]
        t0 = samples[s_idx][0]
        t_end = samples[e_idx][0]
        duration = t_end - t0

        peak_ma = max(samples[i][1] for i in range(s_idx, e_idx + 1))
        charge_ma_s = 0.0
        for i in range(s_idx + 1, e_idx + 1):
            dt = samples[i][0] - samples[i - 1][0]
            if dt <= 0 or dt > gap_s:
                continue
            charge_ma_s += samples[i][1] * dt
        charge_uah = charge_ma_s / 3.6

        lm = extract_landmarks(samples, s_idx, e_idx, seg["trigger_idx"],
                               spike_end_ma=spike_end_ma)
        # Phasen (alle t relativ zum Zyklusanfang t0=0):
        #   pre_peak : Zyklusanfang .. Trigger (Grundlast-Vorlauf)
        #   spike    : Trigger .. Fall unter spike_end_ma (TX-Burst)
        #   tail     : Spike-Ende .. Zyklusende
        trigger_s = lm["trigger"]["start_s"]
        spike_end_s = lm["spike_end"]["start_s"] if lm["spike_end"] else duration
        spike_end_s = max(spike_end_s, trigger_s)
        phases = {
            "pre_peak": {"start_s": 0.0, "end_s": trigger_s,
                         "duration_s": trigger_s},
            "spike": {"start_s": trigger_s, "end_s": spike_end_s,
                      "duration_s": spike_end_s - trigger_s},
            "tail": {"start_s": spike_end_s, "end_s": duration,
                     "duration_s": duration - spike_end_s},
        }

        start_iso = None
        if t_base is not None:
            start_iso = (t_base + datetime.timedelta(seconds=t0)).isoformat()

        cycles.append({
            "start": start_iso,
            "t0_s": t0,
            "duration_s": round(duration, 3),
            "peak_ma": round(peak_ma, 3),
            "charge_uah": round(charge_uah, 4),
            "truncated": seg["truncated"],
            "gap": seg["gap"],
            "landmarks": {
                name: ({"start_s": round(v["start_s"], 3)} if v else None)
                for name, v in lm.items()
            },
            "phases": {
                name: {
                    "start_s": round(p["start_s"], 3),
                    "end_s": round(p["end_s"], 3),
                    "duration_s": round(p["duration_s"], 3),
                }
                for name, p in phases.items()
            },
        })

    # Statistik je Phase: min/median/max der Dauern (nur vollstaendige Zyklen).
    stats: dict = {}
    full = [c for c in cycles if not c["truncated"]]
    for name in ("pre_peak", "spike", "tail"):
        durs = [c["phases"][name]["duration_s"] for c in full]
        if durs:
            stats[name] = {
                "n": len(durs),
                "min_s": round(min(durs), 3),
                "median_s": round(statistics.median(durs), 3),
                "max_s": round(max(durs), 3),
            }
    if full:
        stats["cycle"] = {
            "n": len(full),
            "min_s": round(min(c["duration_s"] for c in full), 3),
            "median_s": round(statistics.median([c["duration_s"] for c in full]), 3),
            "max_s": round(max(c["duration_s"] for c in full), 3),
        }
    return {"cycles": cycles, "stats": stats}


def analyze_cycles(
    measurements: list[dict],
    peak_trigger_ma: float = DEFAULT_PEAK_TRIGGER_MA,
    pre_peak_s: float = DEFAULT_PRE_PEAK_S,
    refractory_s: float = DEFAULT_REFRACTORY_S,
    spike_end_ma: float = DEFAULT_SPIKE_END_MA,
    max_cycle_s: float = DEFAULT_MAX_CYCLE_S,
    gap_s: float = DEFAULT_GAP_S,
) -> dict:
    """
    Vollanalyse: Segmentierung + Landmarks + Phasen-Dauern je Zyklus.

    @return {"cycles": [...], "stats": {...}} mit
      cycle = {
        "start": ISO des Zyklusanfangs (Trigger - pre_peak_s),
        "duration_s": ..., "peak_ma": ..., "charge_uah": ...,
        "truncated": ..., "gap": ...,
        "landmarks": {name: {"start_s": ...} | None},
        "phases": {name: {"start_s": ..., "end_s": ..., "duration_s": ...}},
      }
      stats = je Phase min/median/max der Dauer ueber alle vollstaendigen Zyklen
    """
    samples = _prepare_samples(measurements)
    t_base_iso = None
    for m in measurements:
        t_base_iso = _parse_iso(m.get("measured_at"))
        if t_base_iso is not None:
            break
    return analyze_cycles_from_samples(
        samples, peak_trigger_ma=peak_trigger_ma, pre_peak_s=pre_peak_s,
        refractory_s=refractory_s, spike_end_ma=spike_end_ma,
        max_cycle_s=max_cycle_s, gap_s=gap_s, t_base_iso=t_base_iso)


def assign_sessions_to_cycles(
    cycles: list[dict],
    sessions: list[dict],
) -> list[dict]:
    """
    Ordnet jedem Zyklus die zum Startzeitpunkt aktive Temperatursession zu.

    Regel (siehe db.get_sessions_chronological): Eine Probe/Zyklus gehoert zur
    Session mit der spaetesten start_time <= Zyklusstart. Bei `None`-Sessions
    bzw. Zyklen vor der ersten Session bleibt session_id None.

    @return neue Liste mit zusaetzlichem Feld "session_id" (int | None)
    """
    parsed = []
    for s in sessions:
        st = _parse_iso(s.get("start_time"))
        if st is not None:
            parsed.append((st, s.get("id")))

    out = []
    for c in cycles:
        session_id = None
        if parsed:
            t0 = _parse_iso(c.get("start"))
            if t0 is not None:
                # spaeteste Session, deren Start <= Zyklusstart
                best = None
                for st, sid in parsed:
                    if st <= t0:
                        best = sid
                    else:
                        break
                session_id = best
        out.append({**c, "session_id": session_id})
    return out


def summarize_by_session(cycles: list[dict]) -> list[dict]:
    """
    Fasst Zyklen pro Session zusammen (Firmware-Versionsvergleich).

    Pro Session: Anzahl Zyklen, Gesamt-/Median-Verbrauch je Zyklus und
    Median-Dauer. Nur vollstaendige Zyklen (nicht truncated) zaehlen fuer die
    Mediane; der Gesamtverbrauch inkludiert abgeschnittene Zyklen anteilig.

    @return [{session_id, n_cycles, cycles_complete, charge_uah_total,
              charge_uah_median, duration_median_s, ...}] (chronologisch)
    """
    groups: dict = {}
    for c in cycles:
        groups.setdefault(c.get("session_id"), []).append(c)

    out = []
    for session_id in sorted(groups, key=lambda s: (s is None, s)):
        cyc = groups[session_id]
        complete = [c for c in cyc if not c.get("truncated")]
        charges = [c["charge_uah"] for c in cyc if c.get("charge_uah") is not None]
        durs = [c["duration_s"] for c in complete if c.get("duration_s") is not None]
        peaks = [c["peak_ma"] for c in complete if c.get("peak_ma") is not None]
        out.append({
            "session_id": session_id,
            "n_cycles": len(cyc),
            "n_cycles_complete": len(complete),
            "charge_uah_total": round(sum(charges), 4) if charges else 0.0,
            "charge_uah_median": round(statistics.median(charges), 4) if charges else None,
            "charge_uah_max": round(max(charges), 4) if charges else None,
            "duration_median_s": round(statistics.median(durs), 3) if durs else None,
            "peak_ma_median": round(statistics.median(peaks), 3) if peaks else None,
        })
    return out


def analyze_cycles_by_session(
    measurements: list[dict],
    sessions: list[dict],
    **kwargs,
) -> dict:
    """
    Vollanalyse wie analyze_cycles, zusaetzlich mit Session-Zuordnung und
    Session-Zusammenfassung (Firmware-Versionsvergleich).

    @return analyze_cycles-Struktur + {"sessions": summarize_by_session(...)}
            (cycles enthalten je "session_id": int | None)
    """
    analysis = analyze_cycles(measurements, **kwargs)
    cycles = assign_sessions_to_cycles(analysis["cycles"], sessions)
    return {**analysis, "cycles": cycles, "sessions": summarize_by_session(cycles)}


def _lttb_select(t_s: list[float], ma: list[float], target_n: int) -> tuple[list[int], int]:
    """
    LTTB (Largest-Triangle-Three-Buckets) Auswahl von `target_n` Indizes aus
    einer Reihe (t_s, ma) - bewahrt Peaks und Form besser als gleichmaessiges
    Abtasten. Erster und letzter Punkt werden immer behalten.

    @return (ausgewaehlte Indizes, effektive Punktzahl)
    """
    n = len(t_s)
    if n <= target_n:
        return list(range(n)), n

    # Buckets ueber den Indexbereich; jeder Bucket liefert einen Punkt.
    bucket_size = (n - 2) / (target_n - 2)
    selected: list[int] = [0]
    prev_idx = 0
    for b in range(target_n - 2):
        # Bucket-Grenzen (Indizes in die Ausgangsreihe)
        a = int(1 + b * bucket_size)
        z = min(n - 1, int(1 + (b + 1) * bucket_size))
        z = max(z, a + 1)
        # Mittelungspunkt des NAECHSTEN Buckets (LTTB-Definition)
        na = int(1 + (b + 1) * bucket_size)
        nz = min(n - 1, int(1 + (b + 2) * bucket_size))
        nz = max(nz, na + 1)
        avg_t = sum(t_s[na:nz]) / (nz - na)
        avg_ma = sum(ma[na:nz]) / (nz - na)
        best_i, best_area = a, -1.0
        for i in range(a, z):
            ax, ay = t_s[prev_idx], ma[prev_idx]
            bx, by = t_s[i], ma[i]
            cx, cy = avg_t, avg_ma
            area = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))
            if area > best_area:
                best_area, best_i = area, i
        selected.append(best_i)
        prev_idx = best_i
    selected.append(n - 1)
    return selected, len(selected)


def _render_overlay_cycles(
    analysis: dict,
    samples: list[tuple[float, float]],
    limit: int = 20,
    window_start_s: float | None = None,
    window_end_s: float | None = None,
    max_points_per_cycle: int = DEFAULT_MAX_POINTS_PER_CYCLE,
) -> list[dict]:
    """
    Overlay-Zyklen aus voraufbereiteter Analyse + Rohproben rendern.

    Getrennt von build_overlay, damit der teure Teil (Vollanalyse ueber alle
    Proben) gecacht werden kann und beim Zoom-Nachladen nur der Fenster-
    Schnitt (Bisect, O(log n + k)) neu laeuft.
    """
    # Sortierte Zeitachse einmalig fuer den Bisect-Fensterschnitt.
    ts_all = [t for t, _ in samples]
    out = []
    for c in analysis["cycles"][-int(limit):]:
        # Zugehoerige Rohproben: ueber t0_s und Dauer aus dem Stream schneiden.
        t0 = c["t0_s"]
        t1 = t0 + c["duration_s"]
        # Zoom-Fenster anwenden (Achse: Sekunden seit Zyklusanfang).
        # Epsilon 1e-6: Rand-Samples trotz Fließkomma-Drift aus ISO->s
        # (datetime.total_seconds) nicht versehentlich verwerfen.
        w0 = t0 + (window_start_s if window_start_s is not None else 0.0)
        w1 = t1 + 1e-9 if window_end_s is None else t0 + max(window_end_s, 0.0) + 1e-9
        i0 = bisect.bisect_left(ts_all, w0 - 1e-6)
        i1 = bisect.bisect_right(ts_all, w1)
        # ABSOLUTE Koordinaten (Sekunden seit Zyklusanfang, NICHT auf das
        # Fenster rebasiert): Der Chart behaelt die echte Achsenposition,
        # Pan/Zoom fuehlen sich natuerlich an und der linke Rand zeigt die
        # tatsaechliche Fensterzeit statt klebender 0.
        t_s = [round(samples[i][0] - t0, 4) for i in range(i0, i1)]
        ma = [round(samples[i][1], 4) for i in range(i0, i1)]
        if max_points_per_cycle and len(t_s) > max_points_per_cycle:
            keep, _ = _lttb_select(t_s, ma, max_points_per_cycle)
            t_s = [t_s[i] for i in keep]
            ma = [ma[i] for i in keep]
        # Landmarks/Phasen bleiben (wie die Datenpunkte) in absoluten
        # fensteruebergreifenden Koordinaten.
        lm_out = c["landmarks"]
        ph_out = c["phases"]
        out.append({
            "start": c["start"],
            "duration_s": c["duration_s"],
            "peak_ma": c["peak_ma"],
            "truncated": c["truncated"],
            "gap": c["gap"],
            "landmarks": lm_out,
            "phases": ph_out,
            "t_s": t_s,
            "ma": ma,
        })
    return out


def build_overlay(
    measurements: list[dict],
    limit: int = 20,
    window_start_s: float | None = None,
    window_end_s: float | None = None,
    max_points_per_cycle: int = DEFAULT_MAX_POINTS_PER_CYCLE,
    **kwargs,
) -> dict:
    """
    Oszilloskop-Overlay: die letzten `limit` Zyklen, alle auf t=0
    (Zyklusanfang = Trigger - pre_peak_s) synchronisiert.

    Optionaler Ausschnitt: window_start_s/window_end_s (relativ zum
    Zyklusanfang) beschneiden jeden Zyklus auf dieses Fenster - Grundlage
    fuer das Zoom-Nachladen im Frontend.

    Downsampling: pro Zyklus auf max_points_per_cycle Punkte per LTTB
    (siehe _lttb_select); Landmark-/Phasengrenzen bleiben immer erhalten.

    @return {"cycles": [{start, duration_s, landmarks, t_s: [...], ma: [...]}]}
    """
    analysis = analyze_cycles(measurements, **kwargs)
    samples = _prepare_samples(measurements)
    return {
        "cycles": _render_overlay_cycles(
            analysis, samples, limit=limit,
            window_start_s=window_start_s, window_end_s=window_end_s,
            max_points_per_cycle=max_points_per_cycle,
        ),
        "stats": analysis["stats"],
    }


# --- Session-Durchschnitt (Standard-Ansicht) --------------------------------

# Raster fuer den Session-Mittelwert: Aufloesung in Sekunden (Default 20 ms).
# Kleiner = glaetter, aber mehr Punkte fuer das Frontend; 20 ms reichen, um
# TX-Burst-Form und Spike-Ende sauber abzubilden, und ergeben bei 10-s-Zyklen
# 500 Punkte je Session-Kurve.
DEFAULT_AVG_STEP_S = 0.02

# Max. Zyklen pro Session, die in den Mittelwert eingehen (aeltere fallen
# raus) - begrenzt die Rechenlast bei sehr langen Sessions.
DEFAULT_AVG_CYCLE_LIMIT = 200

# Deckel fuer die Punkte je Session-Kurve: Das Raster wird an die laengste
# Zyklusdauer angepasst (step >= max_dur / MAX_AVG_POINTS), damit lange
# Zyklen (echte Daten: 60-240 s statt 10 s) nicht 12k-Punkt-Kurven erzeugen
# (Render-Last Backend + Chart-Frontend).
MAX_AVG_POINTS = 600

# Zoom-Fenster: Das Raster darf bis auf die Sample-Aufloesung (5 ms @ 200 Hz)
# feinern - sonst bleibt ein tief gezoomter Ausschnitt bei 20 ms stehen und
# Detail (Burst-Kanten, Spitzen) fehlt. Ohne Fenster gilt der 20-ms-Deckel.
MIN_AVG_STEP_S = 0.005
# Erhoehter Punkt-Deckel im Zoom-Fenster (kleiner Zeitraum -> mehr Punkte
# pro Sekunde sind vertretbar; Chart rendert das Fenstergeschnitte gut).
MAX_AVG_POINTS_ZOOM = 2400


def render_session_average(
    analysis: dict,
    samples: list[tuple[float, float]],
    sessions: list[dict] | None = None,
    session_ids: list[int] | None = None,
    step_s: float = DEFAULT_AVG_STEP_S,
    cycle_limit: int = DEFAULT_AVG_CYCLE_LIMIT,
    window_start_s: float | None = None,
    window_end_s: float | None = None,
) -> dict:
    """
    Standard-Ansicht: pro Session Mittelwert-Kurve +/- Standardabweichung.

    Alle Zyklen einer Session (letzte `cycle_limit`) werden auf ein
    gemeinsames Raster (0 .. laengste Zyklusdauer, Abstand `step_s`)
    resampelt; je Rasterpunkt werden Mittelwert und Standardabweichung
    berechnet. Ergebnis: WENIG Punkte (500 je Session statt 500 je Zyklus
    mal 20 Zyklen) und der Streubereich zeigt Abweichler/Firmware-Differenzen
    direkt im Chart.

    @param analysis  Ergebnis von analyze_cycles (Zyklen ohne session_id)
    @param samples   Rohproben [(t_abs_s, current_ma), ...] aufsteigend
    @param sessions  Session-Liste wie db.get_sessions_chronological()
                     (None = keine Zuordnung, alle Zyklen unter session_id None)
    @param session_ids  Filter (None = alle Sessions); Reihenfolge bleibt
                        chronologisch (aeste Session zuerst)
    @return {"avg": [{session_id, t_s: [...], mean: [...], std: [...],
                      n_cycles, duration_median_s, landmarks: {...}}]}
            t_s/mean/std gleich lang; std = 0 bei n_cycles == 1.
    """
    ts_all = [t for t, _ in samples]
    # Zyklen chronologisch (aeste zuerst) - die letzten cycle_limit je Session
    # gehen in den Mittelwert. Truncated-Zyklen (Deckel) verfaelschen den
    # Mittelwert am Ende -> ausschliessen.
    cycles = [c for c in analysis["cycles"] if not c.get("truncated")]
    if sessions:
        cycles = assign_sessions_to_cycles(cycles, sessions)
    groups: dict = {}
    for c in cycles:
        groups.setdefault(c.get("session_id"), []).append(c)

    wanted = set(session_ids) if session_ids is not None else None
    # Fenstergrenzen (achsenrelativ) vorab bestimmen.
    w0 = window_start_s if window_start_s is not None else None
    w1 = window_end_s if window_end_s is not None else None
    step = max(step_s, 1e-3)

    out = []
    for sid in sorted(groups, key=lambda s: (s is None, s)):
        if wanted is not None and sid not in wanted:
            continue
        group = groups[sid][-int(cycle_limit):]
        n = len(group)
        # Gemeinsames Raster ueber die laengste Dauer im Fenster. ABSOLUTE
        # Koordinaten (kein Rebasieren auf 0): hi bleibt an der echten
        # Fensterposition, der Chart behaelt die Achsenposition beim Zoom.
        max_dur = max((c["duration_s"] for c in group), default=0.0)
        lo = w0 if w0 is not None else 0.0
        hi = min(w1, max_dur) if (w1 is not None and w1 < max_dur) else max_dur
        if hi <= lo:
            continue
        # Raster in absoluten Fenstergrenzen; die Abtastung je Zyklus mappt
        # auf t0 + t (Absolutzeit im Stream). Punkt-Deckel: bei sehr langen
        # Zyklen vergroessert sich der Schritt (kein 12k-Punkt-Chart).
        span = hi - lo
        if w0 is not None or w1 is not None:
            # Zoom-Fenster: Raster darf bis zur Sample-Aufloesung feinern
            # (MIN_AVG_STEP_S), sonst fehlen Burst-Kanten/Spitzen im tief
            # gezoomten Ausschnitt. step_s (20-ms-Default des Clients)
            # blockiert hier bewusst NICHT - der Deckel ist der groessere
            # Punkt-Budget MAX_AVG_POINTS_ZOOM ueber den kleineren
            # Fensterzeitraum.
            step = max(MIN_AVG_STEP_S, span / MAX_AVG_POINTS_ZOOM)
        else:
            step = max(step_s, span / max(MAX_AVG_POINTS, 1))
        grid_t = []
        g = lo
        while g < hi - 1e-9:
            grid_t.append(round(g, 4))
            g += step
        grid_t.append(round(hi, 4))

        # Resampling je Zyklus (Bisect-Schnitt + linear. Interpolation).
        cols: list[list[float]] = []
        for c in group:
            t0 = c["t0_s"]
            i0 = bisect.bisect_left(ts_all, t0 + lo - 1e-6)
            i1 = bisect.bisect_right(ts_all, t0 + hi + 1e-6)
            ct = [samples[i][0] - t0 for i in range(i0, i1)]
            cma = [samples[i][1] for i in range(i0, i1)]
            if len(ct) < 2:
                continue
            cols.append(_interp_grid(ct, cma, list(grid_t)))

        if not cols:
            continue
        # n_cycles = Zyklen, die wirklich im Mittelwert stecken (resampelbar).
        # Mittelwert/Varianz je Rasterpunkt in EINEM Durchlauf ueber die
        # Spalten (statt je Rasterpunkt ueber alle Zyklen zu iterieren).
        m = len(cols)
        sums = [0.0] * len(grid_t)
        for col in cols:
            for j, v in enumerate(col):
                sums[j] += v
        mean = [round(s / m, 3) for s in sums]
        if m == 1:
            std = [0.0] * len(grid_t)
        else:
            var_sums = [0.0] * len(grid_t)
            for col in cols:
                for j, v in enumerate(col):
                    d = v - sums[j] / m
                    var_sums[j] += d * d
            std = [round((vs / (m - 1)) ** 0.5, 3) for vs in var_sums]

        # Landmarks/Phasen aus dem Median-Zyklus (repraesentativ); bleiben
        # (wie die Datenpunkte) in absoluten Koordinaten - kein Rebasieren.
        durs = [c["duration_s"] for c in group]
        median_c = group[min(range(n), key=lambda i: abs(durs[i] - statistics.median(durs)))]
        lm = median_c["landmarks"]
        ph = median_c["phases"]
        out.append({
            "session_id": sid,
            "n_cycles": len(cols),
            "t_s": grid_t,
            "mean": mean,
            "std": std,
            "duration_median_s": round(statistics.median(durs), 3),
            "peak_ma_median": round(statistics.median(c["peak_ma"] for c in group), 3),
            "landmarks": lm,
            "phases": ph,
        })
    return {"avg": out}


def _interp_grid(
    t: list[float],
    ma: list[float],
    grid: list[float],
) -> list[float] | None:
    """
    Linear. Interpolation einer Kurve (t, ma) auf ein Raster. Rasterpunkte
    ausserhalb der Kurve werden auf den Randwert geklemmt (alle Zyklen sind
    auf t=0 synchronisiert, leichte Dauer-Differenzen betreffen nur das Ende).
    Der Aufrufer filtert Kurven mit weniger als 2 Punkten vorher heraus.
    """
    out = []
    for g in grid:
        if g <= t[0]:
            out.append(ma[0])
        elif g >= t[-1]:
            out.append(ma[-1])
        else:
            # bisect_right: erster Index mit t[i] > g -> t[i-1] <= g < t[i]
            i = bisect.bisect_right(t, g)
            span = t[i] - t[i - 1]
            f = (g - t[i - 1]) / span if span > 0 else 0.0
            out.append(ma[i - 1] + f * (ma[i] - ma[i - 1]))
    return out
