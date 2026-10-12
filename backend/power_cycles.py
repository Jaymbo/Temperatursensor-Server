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
    if not samples:
        return {"cycles": [], "stats": {}}

    t_base_iso = None
    for m in measurements:
        t_base_iso = _parse_iso(m.get("measured_at"))
        if t_base_iso is not None:
            break

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
        if t_base_iso is not None:
            start_iso = (t_base_iso + datetime.timedelta(seconds=t0)).isoformat()

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


def build_overlay(
    measurements: list[dict],
    limit: int = 20,
    **kwargs,
) -> dict:
    """
    Oszilloskop-Overlay: die letzten `limit` Zyklen, alle auf t=0
    (Zyklusanfang = Trigger - pre_peak_s) synchronisiert.

    @return {"cycles": [{start, duration_s, landmarks, t_s: [...], ma: [...]}]}
    """
    analysis = analyze_cycles(measurements, **kwargs)
    samples = _prepare_samples(measurements)
    out = []
    for c in analysis["cycles"][-int(limit):]:
        # Zugehoerige Rohproben: ueber t0_s und Dauer aus dem Stream schneiden.
        t0 = c["t0_s"]
        t1 = t0 + c["duration_s"]
        t_s = []
        ma = []
        for t, cur in samples:
            if t < t0 - 1e-9:
                continue
            if t > t1 + 1e-9:
                break
            t_s.append(round(t - t0, 4))
            ma.append(round(cur, 4))
        out.append({
            "start": c["start"],
            "duration_s": c["duration_s"],
            "peak_ma": c["peak_ma"],
            "truncated": c["truncated"],
            "gap": c["gap"],
            "landmarks": c["landmarks"],
            "phases": c["phases"],
            "t_s": t_s,
            "ma": ma,
        })
    return {"cycles": out, "stats": analysis["stats"]}
