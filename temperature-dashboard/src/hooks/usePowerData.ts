import { useCallback, useState } from "react";
import { buildApiUrl } from "../api";

/**
 * Laedt die Strom-Zyklusanalyse vom Server (/power/loggers, /power/cycles).
 *
 * Wie useBatteryData bewusst getrennt: Die Strom-Rohproben haben ein ganz
 * anderes Zeit-Raster (200 Hz Dauerstream) als die Temperatur-/Akku-Daten.
 * Die Auswertung (Zyklus-Trigger, Phasen, Overlay) macht das Backend
 * (backend/power_cycles.py) - hier kommen nur die aufbereiteten Daten an.
 */

export interface PowerLandmark {
  start_s: number;
}

export interface PowerPhase {
  start_s: number;
  end_s: number;
  duration_s: number;
}

export interface PowerCycle {
  start: string;
  duration_s: number;
  peak_ma: number;
  truncated: boolean;
  gap: boolean;
  landmarks: Record<string, PowerLandmark | null>;
  phases: Record<string, PowerPhase>;
  t_s: number[];
  ma: number[];
}

export interface PowerPhaseStat {
  n: number;
  min_s: number;
  median_s: number;
  max_s: number;
}

/** Zusammenfassung einer Session im Firmware-Versionsvergleich. */
export interface PowerSessionSummary {
  session_id: number | null;
  n_cycles: number;
  n_cycles_complete: number;
  charge_uah_total: number;
  charge_uah_median: number | null;
  charge_uah_max: number | null;
  duration_median_s: number | null;
  peak_ma_median: number | null;
}

/** Reihenfolge der Phasen fuer die Tabelle (wie im Backend definiert). */
export const POWER_PHASE_ORDER = ["pre_peak", "spike", "tail"] as const;

export const usePowerData = () => {
  const [loggers, setLoggers] = useState<string[]>([]);
  const [cycles, setCycles] = useState<PowerCycle[]>([]);
  const [stats, setStats] = useState<Record<string, PowerPhaseStat>>({});
  const [sessions, setSessions] = useState<PowerSessionSummary[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const loadLoggers = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch(buildApiUrl("/power/loggers"));
      if (!res.ok) throw new Error(`Fehler beim Laden der Logger: ${res.statusText}`);
      const data = await res.json();
      setLoggers(data.loggers || []);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Strom-Logger.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadCycles = useCallback(async (loggerId: string, overlayLimit = 20,
                                        window?: { startS: number; endS: number },
                                        maxPointsPerCycle = 500) => {
    setIsLoading(true);
    setError(null);
    try {
      // Overlay (Rohkurven je Zyklus) und Statistik (Phasen-Dauern + Sessions)
      // parallel. Die Session-Zusammenfassung kommt aus /power/sessions
      // (Firmware-Versionsvergleich: Verbrauch pro Zyklus je Session).
      // Beim Reinzoomen liefert `window` den Ausschnitt (Sekunden seit
      // Zyklusanfang) - der Server antwortet dann mit voller Aufloesung.
      const winParams = window
        ? `&window_start_s=${window.startS}&window_end_s=${window.endS}`
        : "";
      const [overlayRes, cyclesRes, sessionsRes] = await Promise.all([
        fetch(buildApiUrl(`/power/cycles/${loggerId}/overlay?overlay_limit=${overlayLimit}` +
          `&max_points_per_cycle=${maxPointsPerCycle}${winParams}`)),
        fetch(buildApiUrl(`/power/cycles/${loggerId}`)),
        fetch(buildApiUrl(`/power/sessions/${loggerId}`)),
      ]);
      if (!overlayRes.ok || !cyclesRes.ok || !sessionsRes.ok) {
        throw new Error("Fehler beim Laden der Zyklusanalyse.");
      }
      const overlay = await overlayRes.json();
      const cyclesData = await cyclesRes.json();
      const sessionsData = await sessionsRes.json();
      setCycles(overlay.cycles || []);
      setStats(cyclesData.stats || {});
      setSessions(sessionsData.sessions || []);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Zyklusanalyse.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  return { loggers, cycles, stats, sessions, isLoading, error, loadLoggers, loadCycles };
};
