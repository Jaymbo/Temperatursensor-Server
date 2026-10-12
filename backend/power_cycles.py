"""Zyklus-Erkennung und Oszilloskop-Overlay fuer die Strommessung.

Der Mess-ESP (Wemos D1 mini + INA226) streamt ROHPROBEN (Dauerstream @ 200 Hz).
Diese Modul leitet daraus OSZILLOSKOP-ARTIG die Messzyklen des Loggers ab:

  * Segmentierung: Ein Zyklus beginnt, wenn der Strom ueber den Trigger-Schwellwert
    steigt (Logger wacht auf) und endet, wenn der Strom fuer min_quiet_s unter
    dem Ruhe-Schwellwert bleibt (Logger schlaeft).
  * Landmarks: Phasen-Grenzen innerhalb eines Zyklus (Wake, WLAN-Connect,
    HTTP-Send) werden ueber Stromschwellen erkannt. Die Dauern von-bis zeigen,
    WELCHER TEIL WIE LANGE BRAUCHT - z. B. "WLAN-Connect dauert meist 0,8 s,
    aber 2,3 s wenn der Server beim 1. Versuch nicht antwortet".
  * Overlay: Alle Zyklen werden auf t=0 (Trigger) synchronisiert, damit sie wie
    am Oszilloskop uebereinander gelegt werden koennen.

Die Erkennung ist bewusst SCHWELLEN-BASIERT und wird an echten Messdaten
kalibriert (Schwellwerte sind Parameter mit Defaults). Wie bei battery_health
wird NICHTS persistiert - die Rohdaten bleiben unberuehrt, die Erkennung ist
jederzeit aenderbar.
"""

from __future__ import annotations

import datetime
import math
import statistics

# --- Defaults (an echten Daten kalibrieren; alle Endpoints uebernehmen sie) ---
DEFAULT_TRIGGER_MA = 5.0     # Stromanstieg = Logger wacht auf
DEFAULT_QUIET_MA = 2.0       # darunter gilt: schlaeft
DEFAULT_MIN_QUIET_S = 2.0    # so lange ruhig -> Zyklus ist vorbei
DEFAULT_MAX_CYCLE_S = 120.0  # laenger = abgeschnitten (kein Schlaf erkannt)
DEFAULT_GAP_S = 5.0          # Loch im Stream beendet den Zyklus
DEFAULT_SPIKE_END_MA = 40.0  # darunter: Wake-Spike vorbei (Messphase)

# Landmark-Schwellen: (Name, Schwelle mA, Sustain-Dauer s)
#   wake : erster Anstieg ueber trigger (kein Sustain - der Spike IST das Signal)
#   spike_end : Strom faellt unter spike_end_ma NACH dem Wake-Spike -> Messphase
#               (wird NICHT als >-Schwelle gesucht, sondern als Fall unter die
#                Schwelle; siehe _spike_end_idx)
#   wlan : nachhaltig ueber 40 mA (Radio an, Connect-Phase)
#   send : nachhaltig ueber 80 mA (TX-Burst beim HTTP-Post)
DEFAULT_LANDMARKS = (
    ("wake", DEFAULT_TRIGGER_MA, 0.0),
    ("spike_end", DEFAULT_SPIKE_END_MA, 0.0),   # Spezialfall: Fall unter Schwelle
    ("wlan", 40.0, 0.1),
    ("send", 80.0, 0.1),
)


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


def _sustained_above(
    samples: list[tuple[float, float]],
    start_idx: int,
    end_idx: int,
    threshold_ma: float,
    sustain_s: float,
) -> int | None:
    """
    Ersten Index >= start_idx, dessen Strom AB DAUER ueber threshold_ma liegt
    (fuer >= sustain_s zusammenhaengend, bei sustain_s=0 reicht ein Sample).
    @return Index oder None.
    """
    i = start_idx
    while i <= end_idx:
        if samples[i][1] > threshold_ma:
            # Ende des zusammenhaengenden Laufs ueber der Schwelle finden.
            j = i
            while j <= end_idx and samples[j][1] > threshold_ma:
                j += 1
            run_s = samples[j - 1][0] - samples[i][0]
            if sustain_s <= 0.0 or run_s >= sustain_s:
                return i
            i = j                     # Lauf zu kurz -> danach weitersuchen
        else:
            i += 1
    return None


def segment_cycles(
    samples: list[tuple[float, float]],
    trigger_ma: float = DEFAULT_TRIGGER_MA,
    quiet_ma: float = DEFAULT_QUIET_MA,
    min_quiet_s: float = DEFAULT_MIN_QUIET_S,
    max_cycle_s: float = DEFAULT_MAX_CYCLE_S,
    gap_s: float = DEFAULT_GAP_S,
) -> list[dict]:
    """
    Segmentiert den Stromverlauf in Zyklen.

    Ein Zyklus startet am ersten Sample ueber trigger_ma nach einer Ruhephase
    und endet, wenn der Strom fuer >= min_quiet_s unter quiet_ma bleibt, ein
    Datenloch > gap_s auftritt oder max_cycle_s erreicht ist.

    @return Liste von {start_idx, end_idx, truncated, gap}
    """
    cycles: list[dict] = []
    n = len(samples)
    i = 0
    while i < n:
        # Ruhephase bis zum naechsten Trigger ueberspringen.
        if samples[i][1] <= trigger_ma:
            i += 1
            continue
        start = i
        end = i
        truncated = False
        gap = False
        quiet_start = None          # Zeitpunkt, seit dem durchgehend ruhig
        j = i
        while j < n:
            t = samples[j][0]
            if t - samples[start][0] > max_cycle_s:
                truncated = True
                end = j
                break
            if j > 0 and (t - samples[j - 1][0]) > gap_s:
                # Datenloch (z. B. Sende-Pause des Mess-ESP) beendet den Zyklus.
                # end = Sample VOR dem Loch; nie unter den Trigger fallen lassen
                # (sonst end < start -> i = end + 1 bleibt beim selben Trigger
                # haengen).
                gap = True
                end = max(start, j - 1)
                break
            if samples[j][1] > quiet_ma:
                end = j
                quiet_start = None
            elif quiet_start is None:
                quiet_start = t
            if quiet_start is not None and (t - quiet_start) >= min_quiet_s:
                break               # Zyklus vorbei: Ruhe >= min_quiet_s
            j += 1
        else:
            end = j - 1
        # Ruhe am Ende abschneiden (Stream endete mitten in der Schluss-Ruhe):
        while end > start and samples[end][1] <= quiet_ma:
            end -= 1
        cycles.append({
            "start_idx": start,
            "end_idx": end,
            "truncated": truncated,
            "gap": gap,
        })
        i = end + 1
    return cycles


def _spike_end_idx(
    samples: list[tuple[float, float]],
    start_idx: int,
    end_idx: int,
    spike_end_ma: float,
) -> int | None:
    """
    Ende des Wake-Spike-Laufs: erster Index >= start_idx, dessen Strom UNTER
    spike_end_ma faellt (Spike vorbei, Messphase laeuft). @return Index oder None.
    """
    for i in range(start_idx, end_idx + 1):
        if samples[i][1] < spike_end_ma:
            return i
    return None


def extract_landmarks(
    samples: list[tuple[float, float]],
    start_idx: int,
    end_idx: int,
    landmarks: tuple = DEFAULT_LANDMARKS,
    spike_end_ma: float = DEFAULT_SPIKE_END_MA,
) -> dict:
    """
    Erkennt die Phasen-Grenzen (Landmarks) innerhalb eines Zyklus.

    @return {name: {"start_s": t, "idx": i} | None, ...} mit t relativ zum
            Zyklusstart (t=0 = Trigger).
    """
    t0 = samples[start_idx][0]
    out: dict = {}
    search_from = start_idx
    for name, threshold_ma, sustain_s in landmarks:
        idx = None
        if search_from <= end_idx:
            if name == "spike_end":
                # Spezialfall: kein >-Schwellwert, sondern der erste Fall unter
                # spike_end_ma nach dem Wake-Spike.
                idx = _spike_end_idx(samples, search_from, end_idx, spike_end_ma)
            else:
                idx = _sustained_above(samples, search_from, end_idx, threshold_ma, sustain_s)
        if idx is None:
            out[name] = None
        else:
            out[name] = {"start_s": samples[idx][0] - t0, "idx": idx}
            search_from = idx + 1  # Landmarks kommen der Reihe nach
    return out


def analyze_cycles(
    measurements: list[dict],
    trigger_ma: float = DEFAULT_TRIGGER_MA,
    quiet_ma: float = DEFAULT_QUIET_MA,
    min_quiet_s: float = DEFAULT_MIN_QUIET_S,
    max_cycle_s: float = DEFAULT_MAX_CYCLE_S,
    gap_s: float = DEFAULT_GAP_S,
    landmarks_cfg: tuple = DEFAULT_LANDMARKS,
    spike_end_ma: float = DEFAULT_SPIKE_END_MA,
) -> dict:
    """
    Vollanalyse: Segmentierung + Landmarks + Phasen-Dauern je Zyklus.

    @return {"cycles": [...], "stats": {...}} mit
      cycle = {
        "start": ISO des Triggers, "duration_s": ...,
        "peak_ma": ..., "charge_uah": ..., "truncated": ..., "gap": ...,
        "landmarks": {name: {"start_s": ...} | None},
        "phases": {name: {"start_s": ..., "end_s": ..., "duration_s": ...}},
      }
      stats = je Phase min/median/max der Dauer ueber alle Zyklen
    """
    samples = _prepare_samples(measurements)
    if not samples:
        return {"cycles": [], "stats": {}}

    t_base_iso = None
    for m in measurements:
        t_base_iso = _parse_iso(m.get("measured_at"))
        if t_base_iso is not None:
            break

    segments = segment_cycles(samples, trigger_ma, quiet_ma, min_quiet_s,
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

        lm = extract_landmarks(samples, s_idx, e_idx, landmarks_cfg,
                               spike_end_ma=spike_end_ma)
        # Phasen (alle t relativ zum Trigger t0=0):
        #   wake       : Trigger .. Ende des Wake-Spike-Laufs
        #   measure    : Spike-Ende .. WLAN-Start (Messung/NAU7802, Strom moderat)
        #   wlan       : WLAN-Start .. Send-Start (Radio an, Connect)
        #   send_tail  : Send-Start .. Zyklusende (TX-Burst + Rest)
        spike_end_lm = lm.get("spike_end")
        if spike_end_lm is not None:
            wake_end = spike_end_lm["start_s"]
        elif lm.get("wlan") is not None:
            wake_end = lm["wlan"]["start_s"]     # kein eindeutiger Spike-Ende
        else:
            wake_end = duration
        wlan_start = lm["wlan"]["start_s"] if lm.get("wlan") else wake_end
        wlan_start = max(wlan_start, wake_end)
        send_start = lm["send"]["start_s"] if lm.get("send") else duration
        send_start = max(send_start, wlan_start)
        phases = {
            "wake": {"start_s": 0.0, "end_s": wake_end,
                     "duration_s": wake_end},
            "measure": {"start_s": wake_end, "end_s": wlan_start,
                        "duration_s": wlan_start - wake_end},
            "wlan": {"start_s": wlan_start, "end_s": send_start,
                     "duration_s": send_start - wlan_start},
            "send_tail": {"start_s": send_start, "end_s": duration,
                          "duration_s": duration - send_start},
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
    for name in ("wake", "measure", "wlan", "send_tail"):
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
    Oszilloskop-Overlay: die letzten `limit` Zyklen, alle auf t=0 (Trigger)
    synchronisiert.

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
