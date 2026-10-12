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

/** Session-Durchschnitt (Standard-Ansicht): Mittelwert +/- Standardabw. */
export interface PowerSessionAvg {
  session_id: number | null;
  n_cycles: number;
  t_s: number[];
  mean: number[];
  std: number[];
  duration_median_s: number;
  peak_ma_median: number;
  landmarks: Record<string, PowerLandmark | null>;
  phases: Record<string, PowerPhase>;
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

/** Auswaehlbare Session (Auswahl im Power-Panel). */
export interface PowerSessionOption {
  id: number;
  sensor_id: number | null;
  start_time: string;
  custom_text: string | null;
}

/** Reihenfolge der Phasen fuer die Tabelle (wie im Backend definiert). */
export const POWER_PHASE_ORDER = ["pre_peak", "spike", "tail"] as const;

export type PowerOverlayMode = "avg" | "cycles";

export const usePowerData = () => {
  const [loggers, setLoggers] = useState<string[]>([]);
  const [cycles, setCycles] = useState<PowerCycle[]>([]);
  const [avg, setAvg] = useState<PowerSessionAvg[]>([]);
  const [stats, setStats] = useState<Record<string, PowerPhaseStat>>({});
  const [sessions, setSessions] = useState<PowerSessionSummary[]>([]);
  const [sessionOptions, setSessionOptions] = useState<PowerSessionOption[]>([]);
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
                                        maxPointsPerCycle = 500,
                                        mode: PowerOverlayMode = "avg",
                                        sessionIds?: number[]) => {
    setIsLoading(true);
    setError(null);
    try {
      // Overlay (avg-Kurven oder Zyklen) und Statistik (Phasen-Dauern +
      // Sessions) parallel. Die Session-Zusammenfassung kommt aus
      // /power/sessions (Firmware-Versionsvergleich).
      // Beim Reinzoomen liefert `window` den Ausschnitt (Sekunden seit
      // Zyklusanfang) - der Server antwortet dann mit voller Aufloesung.
      const winParams = window
        ? `&window_start_s=${window.startS}&window_end_s=${window.endS}`
        : "";
      const modeParams = buildModeParams(mode, sessionIds);
      const [overlayRes, cyclesRes, sessionsRes] = await Promise.all([
        fetch(buildApiUrl(`/power/cycles/${loggerId}/overlay?overlay_limit=${overlayLimit}` +
          `&max_points_per_cycle=${maxPointsPerCycle}${winParams}${modeParams}`)),
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
      setAvg(overlay.avg || []);
      setStats(cyclesData.stats || {});
      setSessions(sessionsData.sessions || []);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Zyklusanalyse.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  /**
   * Nur das Overlay nachladen (Zoom-Nachladen in voller Aufloesung bzw.
   * Reset auf die Gesamtansicht). Bewusst OHNE /power/cycles und
   * /power/sessions: Die Statistik aendert sich beim Zoomen nicht und deren
   * Vollanalyse wuerde das Nachladen unnötig verlangsamen.
   */
  const loadOverlay = useCallback(async (loggerId: string,
                                         window?: { startS: number; endS: number },
                                         maxPointsPerCycle = 500,
                                         overlayLimit = 20,
                                         mode: PowerOverlayMode = "avg",
                                         sessionIds?: number[]) => {
    setError(null);
    try {
      const winParams = window
        ? `&window_start_s=${window.startS}&window_end_s=${window.endS}`
        : "";
      const modeParams = buildModeParams(mode, sessionIds);
      const res = await fetch(buildApiUrl(`/power/cycles/${loggerId}/overlay?overlay_limit=${overlayLimit}` +
        `&max_points_per_cycle=${maxPointsPerCycle}${winParams}${modeParams}`));
      if (!res.ok) throw new Error("Fehler beim Laden des Overlays.");
      const overlay = await res.json();
      setCycles(overlay.cycles || []);
      setAvg(overlay.avg || []);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden des Overlays.");
    }
  }, []);

  /**
   * Auswaehlbare Sessions laden (fuer die Multi-Auswahl im Power-Panel):
   * Alle Sessions mit Startzeit, neueste zuerst.
   */
  const loadSessionOptions = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(buildApiUrl("/sessions"));
      if (!res.ok) throw new Error(`Fehler beim Laden der Sessions: ${res.statusText}`);
      const data = await res.json();
      const opts: PowerSessionOption[] = (data.sessions || [])
        .filter((s: any) => s.start_time != null)
        .map((s: any) => ({
          id: Number(s.id),
          sensor_id: s.sensor_id ?? null,
          start_time: String(s.start_time),
          custom_text: s.custom_text ?? null,
        }))
        .sort((a: PowerSessionOption, b: PowerSessionOption) =>
          b.start_time.localeCompare(a.start_time));
      setSessionOptions(opts);
    } catch (err) {
      console.error(err);
      setError("Fehler beim Laden der Session-Liste.");
    }
  }, []);

  return {
    loggers, cycles, avg, stats, sessions, sessionOptions,
    isLoading, error,
    loadLoggers, loadCycles, loadOverlay, loadSessionOptions,
  };
};

/**
 * URL-Parameter fuer Overlay-Modus + Session-Filter:
 * - "avg" (Standard): alle Sessions bzw. die gewaehlten (kommasepariert)
 * - "cycles": klassisches Einzelzyklen-Overlay
 */
function buildModeParams(mode: PowerOverlayMode, sessionIds?: number[]): string {
  if (mode === "cycles") return "&mode=cycles";
  const sidParams = sessionIds && sessionIds.length > 0
    ? `&session_ids=${sessionIds.join(",")}`
    : "";
  return `&mode=avg${sidParams}`;
}
